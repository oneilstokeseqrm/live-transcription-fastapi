"""Lane 1 publish + Lane 2 dispatch service shared by ``/text/clean`` and Granola.

Extracted from ``routers/text.py`` in PR-X1 of the Granola integration (phase
2d prep). The endpoint handler in ``routers/text.py`` continues to own HTTP
validation, auth context, calendar-matching enrichment, LLM text cleaning,
and envelope construction — the pieces that are HTTP-shaped and specific to
the /text/clean form-submission path. Once the envelope is built, the
handler delegates to :func:`process` here, which owns the **shared**
infrastructure:

* the in-flight Lane 2 counter + backpressure cap (formerly
  ``routers.text._INFLIGHT_LANE2`` + ``TEXT_CLEAN_MAX_BG_TASKS``),
* the registry of Lane 2 background tasks for graceful shutdown
  (formerly ``routers.text._BACKGROUND_TASKS``, drained by
  ``main._drain_text_clean_background_tasks``),
* the synchronous Lane 1 publish via :class:`AWSEventPublisher.publish_envelope`,
* the fire-and-forget Lane 2 dispatch of
  :meth:`IntelligenceService.process_transcript` with the same
  exception-routing + ``_on_done`` safety net the route handler used to inline.

The Granola ingestion adapter (Phase 2d, PR-X2) calls :func:`process` directly
from Python (NOT over HTTP per **LOCKED-41** — Railway's ~5-minute edge proxy
timeout makes intra-service HTTP fragile, and the Granola adapter builds its
envelope outside the /text/clean form-submission path so the HTTP route would
be a poor fit anyway). The Granola adapter's call site:

* skips :class:`TranscriptEnrichmentService` (Granola's API gives us richer
  attendee data than calendar-matching would),
* skips :class:`BatchCleanerService` (Granola transcripts are already
  LLM-formatted; running another cleaner pass would waste tokens and may
  degrade content), and
* constructs the envelope per **LOCKED-35/36** before calling :func:`process`.

Backpressure flow (preserved from the pre-extraction code):

1. Caller calls :func:`try_reserve_lane2_slot` BEFORE any side-effecting work
   (the /text/clean handler calls this BEFORE enrichment because enrichment
   writes ``pending_account_mapping_signals`` rows; we do not want those
   rows on a 503 retry). If it returns ``False``, the caller rejects (HTTP
   503 with ``Retry-After`` for /text/clean; transient retry for Granola).
2. Caller does its pre-process work (enrichment, cleaning, envelope build).
3. Caller calls :func:`process`. On success the slot is consumed by the
   Lane 2 ``asyncio.Task`` and released by :func:`_on_done` when the task
   completes. On :class:`Lane1PublishError` the slot is released internally
   by :func:`process` before raising — the caller does NOT call
   :func:`release_lane2_slot` in that path. On any other exception raised
   from inside :func:`process` BEFORE Lane 2 was dispatched, the same
   internal ``slot_handed_off`` flag releases the slot via the ``finally``.
4. If the caller raises BEFORE getting to :func:`process` (e.g. cleaning
   crashed), they MUST call :func:`release_lane2_slot` to roll back.

This mirrors the pre-extraction ``slot_handed_off`` pattern verbatim — the
only structural change is that the slot bookkeeping is now reachable from
two callers (the route handler and the Granola adapter) instead of being
private to one route.

Module-level state — ``_INFLIGHT_LANE2`` (single-element list) and
``_BACKGROUND_TASKS`` (set) — uses the same shape as the pre-extraction
code so the regression tests' direct manipulation pattern keeps working
(``test_text_clean_response_decoupling.py`` reaches in to set / clear /
add tasks for its assertions).
"""

from __future__ import annotations

import asyncio
import logging
import os as _os
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from models.envelope import EnvelopeV1
from services.aws_event_publisher import AWSEventPublisher
from services.intelligence_service import IntelligenceService

logger = logging.getLogger(__name__)


# Strong references to in-flight Lane 2 background tasks. The event loop
# only holds weak references to tasks created via asyncio.create_task, so
# a task can be garbage-collected mid-execution if no other reference
# exists. Holding the task here (with discard-on-done) keeps it alive
# until completion. Drained on lifespan shutdown — see
# main._drain_text_clean_background_tasks.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


# Atomic in-flight counter for Lane 2 backpressure. Pre-extraction this
# lived in routers.text; the regression tests in
# tests/integration/test_text_clean_response_decoupling.py poke at it
# directly to assert pre-Lane-1 backpressure behavior. Single-element
# list keeps the state mutable from inner closures without needing
# ``global``.
_INFLIGHT_LANE2: list[int] = [0]


def _max_background_tasks() -> int:
    """Read the Lane 2 backpressure cap at call time (NOT import time).

    Read at request time because main.py imports this module before
    calling load_dotenv() — capturing the env var at import would freeze
    the default and ignore .env overrides (Codex round-6 P2 on PR #23).
    """
    return int(_os.environ.get("TEXT_CLEAN_MAX_BG_TASKS", "50"))


def get_lane2_in_flight() -> int:
    """Read the current Lane 2 in-flight count (for logging / observability)."""
    return _INFLIGHT_LANE2[0]


def get_lane2_cap() -> int:
    """Read the configured Lane 2 cap (for logging / observability)."""
    return _max_background_tasks()


def try_reserve_lane2_slot() -> bool:
    """Atomically reserve a Lane 2 slot. Returns True on success, False if at cap.

    Pair with :func:`process` (which consumes the reservation) or
    :func:`release_lane2_slot` (to roll back without dispatching).

    The atomic increment is safe under cooperative-scheduling concurrency
    because Python's single-threaded event loop guarantees this read +
    increment is atomic w.r.t. other coroutines (no ``await`` between
    them).
    """
    cap = _max_background_tasks()
    if _INFLIGHT_LANE2[0] >= cap:
        return False
    _INFLIGHT_LANE2[0] += 1
    return True


def release_lane2_slot() -> None:
    """Roll back a slot reserved by :func:`try_reserve_lane2_slot` without dispatch.

    Call this when the caller's pre-process work raises after a successful
    reservation but BEFORE :func:`process` is called. (The /text/clean
    handler uses an outer ``try/finally`` to release the slot if cleaning
    or enrichment crashes mid-flight; Granola similarly releases if
    envelope construction raises.)

    Decrement is unconditional — caller is responsible for matching one
    reserve with one release/consume.
    """
    _INFLIGHT_LANE2[0] -= 1


@dataclass(frozen=True)
class Lane2Extras:
    """Optional Lane 2 inputs that don't live in :class:`EnvelopeV1` itself.

    These are the IntelligenceService kwargs that the /text/clean path
    sources from :class:`TranscriptEnrichmentService`. The Granola adapter
    doesn't run through the same enrichment pipeline (Granola gives us
    direct attendee data; we skip the calendar-matching path), so most
    of these will be ``None`` for the Granola call site. Set whichever
    fields apply.

    ``cleaned_transcript`` is special — when ``None``, :func:`process`
    falls back to ``envelope.content.text``. /text/clean overrides this
    with the output of :class:`BatchCleanerService` (the LLM-cleaned form
    isn't what we'd want to publish on the envelope, which carries the
    pre-cleaned form per the existing /text/clean contract). Granola
    leaves this as ``None`` so Lane 2 sees the same transcript text the
    downstream consumers saw.
    """

    cleaned_transcript: Optional[str] = None
    contact_ids: Optional[list[str]] = None
    calendar_event_id: Optional[str] = None
    enrichment_confidence: Optional[str] = None
    enrichment_match_method: Optional[str] = None


@dataclass(frozen=True)
class ProcessResult:
    """Return value from :func:`process`.

    ``interaction_id`` mirrors ``envelope.interaction_id`` as a string for
    caller convenience. ``lane1_published`` is ``True`` whenever
    :meth:`AWSEventPublisher.publish_envelope` returned without raising
    — note that the inner ``{kinesis_sequence, eventbridge_id}`` dict may
    still be ``{None, None}`` for the supported "publishing disabled /
    no AWS credentials" configuration (test_text_clean_response_decoupling
    pins this as ``test_text_clean_allows_null_publish_when_aws_disabled``).
    ``lane2_dispatched`` is ``True`` whenever :func:`process` reached
    ``asyncio.create_task``; the Task may still fail asynchronously
    (handled by :func:`_on_done`).
    """

    interaction_id: str
    lane1_published: bool
    lane2_dispatched: bool


class Lane1PublishError(Exception):
    """Raised when :meth:`AWSEventPublisher.publish_envelope` itself raises.

    The /text/clean handler translates this to HTTP 502 with detail
    "Could not publish interaction envelope downstream" (matches the
    pre-extraction contract pinned by
    ``test_text_clean_lane1_failure_produces_5xx``). The Granola adapter
    catches it and treats as a transient retry — the Kinesis sequence /
    EventBridge dedup makes retries idempotent.

    ``__cause__`` preserves the underlying boto / network exception via
    ``raise ... from e`` semantics.
    """


class TenantIsolationError(Exception):
    """Raised when caller-supplied identity kwargs disagree with the envelope.

    LOCKED-41 requires ``tenant_id`` to flow as an explicit function argument
    sourced from the caller's entity context (the credential row in vault for
    the Granola adapter; the JWT-validated context for /text/clean).
    :func:`process` cross-checks ``tenant_id`` / ``user_id`` / ``account_id``
    against the corresponding envelope fields and fails loud on mismatch —
    a caller bug that builds an envelope under the wrong tenant would
    otherwise publish + persist with no observable signal.

    This is defense-in-depth above the caller's own identity sourcing; both
    /text/clean and the Granola adapter construct the envelope from the same
    identity values they pass here, so the cross-check should always pass
    under correct caller behavior. Failures indicate a coding bug worth
    raising loudly.
    """


def _check_envelope_identity(
    *,
    tenant_id: UUID,
    user_id: str,
    account_id: str,
    envelope: EnvelopeV1,
) -> None:
    """Raise :class:`TenantIsolationError` if caller kwargs don't match envelope.

    Comparison is normalized to string form so a UUID kwarg vs a UUID-typed
    envelope field compare equal regardless of how the caller spelled them.
    """
    env_tenant = str(envelope.tenant_id)
    if env_tenant != str(tenant_id):
        raise TenantIsolationError(
            f"tenant_id mismatch: caller={tenant_id!s} envelope={env_tenant} "
            "— refusing to publish under the wrong tenant"
        )
    if envelope.user_id != user_id:
        raise TenantIsolationError(
            f"user_id mismatch: caller={user_id!r} envelope={envelope.user_id!r}"
        )
    if envelope.account_id != account_id:
        raise TenantIsolationError(
            f"account_id mismatch: caller={account_id!r} envelope={envelope.account_id!r}"
        )


async def process(
    *,
    tenant_id: UUID,
    user_id: str,
    account_id: str,
    envelope: EnvelopeV1,
    lane2_extras: Optional[Lane2Extras] = None,
) -> ProcessResult:
    """Publish (Lane 1, sync) + dispatch intelligence extraction (Lane 2, fire-and-forget).

    Preconditions:
      - Caller has reserved a Lane 2 slot via :func:`try_reserve_lane2_slot`.
        :func:`process` does NOT check the backpressure cap; it consumes
        the pre-reserved slot. Calling without a reservation will over-
        commit the cap. (The split is intentional: the /text/clean
        handler reserves BEFORE running enrichment so the 503 reject path
        produces zero pending_account_mapping_signal rows. Moving the
        reserve into :func:`process` would invert that ordering.)
      - ``envelope`` is fully constructed per the LOCKED envelope shape
        (LOCKED-35/36 for Granola; pre-existing /text/clean shape for
        the /text/clean handler).
      - ``tenant_id`` / ``user_id`` / ``account_id`` MUST match the
        corresponding fields on ``envelope`` per LOCKED-41 — these are
        explicit kwargs sourced from the caller's entity context (vault
        credential for Granola; JWT context for /text/clean), and
        :func:`_check_envelope_identity` raises
        :class:`TenantIsolationError` on mismatch. The cross-check
        happens BEFORE any side-effecting work, so a caller bug surfaces
        BEFORE we've published, persisted, or consumed the Lane 2 slot
        (slot is released via the same internal finally as other failures).

    Behavior:
      - Identity cross-check (above) — raises before any side effects.
      - Lane 1: ``AWSEventPublisher().publish_envelope(envelope)`` is awaited
        synchronously. On exception, :class:`Lane1PublishError` is raised
        AFTER the slot has been internally released — the caller does
        NOT need to release on this path.
      - Lane 2: ``IntelligenceService.process_transcript(...)`` is wrapped
        in an :func:`asyncio.create_task` and the task is added to
        ``_BACKGROUND_TASKS`` with a ``done_callback`` (``_on_done``)
        that decrements the in-flight counter and logs unhandled
        exceptions. The slot is consumed by this dispatch.

    Returns: :class:`ProcessResult`. Currently both flags are always
    ``True`` on the success path (Lane 1 returned a dict, Lane 2 task
    was scheduled); the fields are kept for forward-compat with future
    soft-failure modes (e.g. a configuration flag to skip Lane 2 for
    Granola while keeping Lane 1).

    Raises:
      :class:`TenantIsolationError` — when caller kwargs don't match envelope.
      :class:`Lane1PublishError` — when :meth:`publish_envelope` raised.
      Any other exception — preserved as-is; the slot is released via the
      internal ``finally``.
    """
    interaction_id_str = str(envelope.interaction_id) if envelope.interaction_id else ""
    slot_handed_off = False
    try:
        # LOCKED-41 cross-tenant defense-in-depth: caller identity kwargs
        # MUST match envelope. Runs inside the try/finally so a mismatch
        # also releases the slot via the same path as other failures.
        _check_envelope_identity(
            tenant_id=tenant_id,
            user_id=user_id,
            account_id=account_id,
            envelope=envelope,
        )
        # ------------------------------------------------------------------
        # Lane 1 — synchronous publish (Kinesis + EventBridge)
        # ------------------------------------------------------------------
        #
        # ``publish_envelope`` does NOT raise on the normal per-destination
        # AWS failure path — internally it catches + returns
        # ``{kinesis_sequence: None, eventbridge_id: None}``. The pre-PR
        # rule (test_text_clean_lane1_failure_produces_5xx) is that an
        # unexpected raise here surfaces as Lane1PublishError -> HTTP 502
        # for HTTP callers; Granola treats it as transient retry. The
        # supported "publishing disabled" config (both ENABLE_*_PUBLISHING
        # = false) still returns null/null and is NOT treated as failure
        # — pinned by test_text_clean_allows_null_publish_when_aws_disabled.
        try:
            publisher = AWSEventPublisher()
            lane1_result: Optional[dict[str, Any]] = await publisher.publish_envelope(envelope)
        except Exception as exc:
            logger.error(
                f"Lane 1 (publishing) raised: interaction_id={interaction_id_str}, "
                f"error={type(exc).__name__}: {exc}",
                exc_info=True,
            )
            raise Lane1PublishError(
                f"AWSEventPublisher.publish_envelope raised: {type(exc).__name__}"
            ) from exc

        # Match pre-PR observability shape — per-destination success/failure
        # log without 502'ing on null. Pre-existing observability gap
        # (Codex rounds 3/5/6/7 on PR #23 discuss this in depth): we can't
        # cleanly distinguish "null because outage" from "null because
        # disabled / no creds" with the current dict shape, so the pre-PR
        # code didn't try and neither do we.
        kinesis_status = (
            "success" if (lane1_result or {}).get("kinesis_sequence") else "failed-or-disabled"
        )
        eventbridge_status = (
            "success" if (lane1_result or {}).get("eventbridge_id") else "failed-or-disabled"
        )
        logger.info(
            f"Envelope publish complete: interaction_id={interaction_id_str}, "
            f"kinesis={kinesis_status}, eventbridge={eventbridge_status}"
        )

        # ------------------------------------------------------------------
        # Lane 2 — fire-and-forget intelligence extraction
        # ------------------------------------------------------------------
        #
        # The text Lane 2 analyzes defaults to ``envelope.content.text``
        # (Granola's path — what downstream consumers receive). The
        # /text/clean caller overrides with the LLM-cleaned form produced
        # by ``BatchCleanerService`` — which is intentionally NOT what
        # ends up on the envelope (the envelope carries the raw input)
        # so the override is necessary to preserve the pre-extraction
        # contract.
        #
        # ``is None`` check instead of ``or``: an empty-string override is
        # a valid explicit caller intent ("I cleaned the text down to
        # nothing — analyze the empty form"), not a missing value. The
        # pre-extraction route handler passed whatever ``BatchCleanerService``
        # returned (including ``""``) directly to Lane 2; preserving that
        # semantics here (Codex PR-X1 R3 P3 finding).
        extras_cleaned: Optional[str] = (
            lane2_extras.cleaned_transcript if lane2_extras else None
        )
        cleaned_transcript: str = (
            extras_cleaned if extras_cleaned is not None else envelope.content.text
        )
        contact_ids: Optional[list[str]] = lane2_extras.contact_ids if lane2_extras else None
        calendar_event_id: Optional[str] = (
            lane2_extras.calendar_event_id if lane2_extras else None
        )
        enrichment_confidence: Optional[str] = (
            lane2_extras.enrichment_confidence if lane2_extras else None
        )
        enrichment_match_method: Optional[str] = (
            lane2_extras.enrichment_match_method if lane2_extras else None
        )

        # Closures capture interaction_id_str + envelope fields by reference;
        # Python's lexical scoping makes this safe even across the gap
        # between create_task and the eventual coroutine execution.
        async def _lane2_intelligence() -> Optional[object]:
            """Run IntelligenceService.process_transcript inside the background task.

            Internal exceptions are logged and re-raised so the ``_on_done``
            callback can surface them at ERROR level. Pre-extraction this
            log was in routers/text.py; pinned by
            ``test_text_clean_lane2_exception_is_logged_not_silenced``.
            """
            try:
                intelligence_service = IntelligenceService()
                return await intelligence_service.process_transcript(
                    cleaned_transcript=cleaned_transcript,
                    interaction_id=interaction_id_str,
                    tenant_id=str(envelope.tenant_id),
                    trace_id=envelope.trace_id or "",
                    interaction_type=envelope.interaction_type,
                    account_id=envelope.account_id,
                    # Single source of event-time: thread the envelope's
                    # timestamp into Lane 2 so raw_interactions /
                    # interaction_summary_entries.interaction_timestamp matches
                    # the envelope (now() for real-time, occurred_at for a
                    # backdated /text/clean, detail.created_at for Granola)
                    # instead of IntelligenceService defaulting to a fresh
                    # utcnow(). Centralizing here fixes every caller — including
                    # the latent Granola drift — in one place (EQ-231 / A2).
                    interaction_timestamp=envelope.timestamp,
                    contact_ids=contact_ids,
                    calendar_event_id=calendar_event_id,
                    enrichment_confidence=enrichment_confidence,
                    enrichment_match_method=enrichment_match_method,
                )
            except Exception as exc:
                logger.error(
                    f"Lane 2 (intelligence) failed (non-fatal, background): "
                    f"interaction_id={interaction_id_str}, "
                    f"error={type(exc).__name__}: {exc}",
                    exc_info=True,
                )
                raise

        def _on_done(task: asyncio.Task) -> None:
            """Wrapper-level safety net for the Lane 2 background task.

            Releases the Lane 2 backpressure slot AND surfaces wrapper-level
            crashes (anything raised by Python machinery around the
            coroutine that ``_lane2_intelligence`` didn't itself catch).
            Under the pre-fire-and-forget synchronous-await model, such
            failures became HTTP 5xx and were observable; after moving to
            background, they MUST surface as a logger.error here or Python
            only emits a "Task exception was never retrieved" GC warning
            (invisible in production observability).
            """
            _BACKGROUND_TASKS.discard(task)
            _INFLIGHT_LANE2[0] -= 1
            if task.cancelled():
                logger.warning(
                    f"Lane 2 background task cancelled: "
                    f"interaction_id={interaction_id_str}"
                )
                return
            exc = task.exception()
            if exc is not None:
                logger.error(
                    f"Lane 2 background task crashed (unhandled): "
                    f"interaction_id={interaction_id_str}, "
                    f"error={type(exc).__name__}: {exc}",
                    exc_info=exc,
                )
            else:
                logger.info(
                    f"Lane 2 (intelligence) completed: "
                    f"interaction_id={interaction_id_str}"
                )

        task = asyncio.create_task(_lane2_intelligence())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_on_done)
        # Slot is now consumed by the background task; ``_on_done`` owns
        # the decrement when the task completes. The outer ``finally``
        # leaves the counter alone (slot_handed_off=True).
        slot_handed_off = True

        return ProcessResult(
            interaction_id=interaction_id_str,
            lane1_published=True,
            lane2_dispatched=True,
        )
    finally:
        if not slot_handed_off:
            _INFLIGHT_LANE2[0] -= 1
