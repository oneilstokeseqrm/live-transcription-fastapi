"""Per-credential Granola ingestion adapter.

The integration point of Phase 2d. Composes:

* :func:`services.vault.get_granola_credential_for_user` — decrypts the
  per-user Granola API key (LOCKED-40 four-field EncryptionContext).
* :class:`services.granola_ingestion.GranolaAPIClient` — HTTP primitives
  (Phase 2c).
* :mod:`services.granola_ingestion.path2` — attendee classification +
  Scenario A/C/D branching.
* :func:`services.text_clean_service.process` — LOCKED-41 direct Python
  call into the Lane 1 publish + Lane 2 dispatch pipeline. Identity
  kwargs sourced from ``credential.tenant_id`` / ``credential.user_id``
  / anchor account.
* :mod:`services.account_lookup` / :mod:`services.domain_classification` /
  :mod:`services.internal_domains` / :mod:`services.pending_account_mappings`
  — pre-existing infrastructure shared with /text/clean's enrichment path.

This module **does NOT run on a schedule yet** — Phase 2e wires the
Railway-cron → DBOS-queue → ``run_one_cycle`` invocation. Phase 2d ships
inert code so Phase 2e can build on a stable per-credential API
without conflating scheduling with adapter logic.

Per LOCKED-25 the Granola adapter ingests with ``interaction_type="meeting"``
(NOT "transcript" — see ``docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md``
§"The raw_interactions Trap"). Per LOCKED-35 the envelope uses
``source="generic"`` and ``content.format="plain"``. Per LOCKED-36 the
envelope's ``extras`` carries six ``granola_*`` keys. Per LOCKED-44 the
Scenario C deferred path captures a ``granola_note_snapshot`` JSONB so a
meeting can still be recovered if Granola removes the note before the
user approves the unknown domain.

The adapter explicitly DOES NOT go through
:class:`services.transcript_enrichment.TranscriptEnrichmentService` — that
service does calendar-matching to discover attendees, which Granola
already provides directly with richer data (names + emails per attendee).
It also DOES NOT run :class:`services.batch_cleaner_service.BatchCleanerService`
— Granola produces already-cleaned LLM output; running our cleaner on
top would waste tokens and risk degrading content quality. The envelope's
``content.text`` is built locally as a YAML front-matter block + the
speaker-tagged transcript turns Granola returns.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID, uuid4

import asyncpg

from models.envelope import ContentModel, EnvelopeV1
from services import text_clean_service
from services.account_lookup import lookup_account_by_domain
from services.database import get_async_session
from services.granola_ingestion.api_client import GranolaAPIClient
from services.granola_ingestion.errors import GranolaError, GranolaErrorCode
from services.granola_ingestion.models import (
    Attendee,
    GranolaNoteDetail,
    GranolaNoteSummary,
    TranscriptTurn,
)
from services.granola_ingestion.outcomes import IngestionOutcome
from services.granola_ingestion.path2 import (
    PathTwoDecision,
    Scenario,
    classify_attendees,
    decide_scenario,
    unique_unknown_business_domains,
)
from services.internal_domains import get_tenant_internal_domains
from services.pending_account_mappings import (
    SignalProposal,
    insert_signal,
    reopen_archived_entry,
    upsert_queue_entry,
)

# Defer the vault import so this module is loadable in environments where
# the cryptography wheel isn't installed (some local dev .venvs). The
# adapter only uses ``GranolaCredential`` for type hints; runtime callers
# (Phase 2e scheduler, tests) pass already-decrypted credentials they
# constructed via vault directly. Production Railway has cryptography
# installed via requirements.txt.
if TYPE_CHECKING:
    from services.vault import GranolaCredential

logger = logging.getLogger(__name__)


_PROVIDER = "granola"

# Per the plan: per-credential consecutive-failure budget. After 3 cycles
# of transient failures (= 15 min @ 5-min cadence), the credential moves
# to status='error' and a transactional email is sent (Phase 2g) so the
# user can investigate. The boundary is at >= 3 AFTER the increment, so
# the third failure flips the status.
_CONSECUTIVE_FAILURE_THRESHOLD = 3

# Per-note transient-retry budget. After 5 attempts of a transient
# failure on the same note, the row moves to status='failed_permanent'
# and Phase 2e skips it on subsequent cycles. Operators can manually
# re-queue via Phase 2f admin endpoints.
_PER_NOTE_RETRY_LIMIT = 5

# Intermediate status used to record the "publish dispatched, success
# not yet recorded" state — written BEFORE :func:`text_clean_service.process`
# so a crash between Lane 1 publish and the success UPSERT can't lose
# our idempotency anchor (Codex PR-X2 R1 P2 finding). NOT exposed in
# :class:`IngestionOutcome` because it's never a terminal outcome of
# :func:`process_note`; the row transitions to 'success' or 'failed' on
# the same call. Persisted as a string in
# ``external_integration_runs.status`` (no DB-level CHECK constraint
# restricts the column to the IngestionOutcome enum).
_STATUS_IN_PROGRESS = "in_progress"


class _CredentialDeactivated(Exception):
    """Raised mid-cycle when the credential is found archived/deactivated.

    Edge #12 (plan §2.1 #12 / Phase 2f Codex R9 + this PR's R1): the final
    liveness gate in :func:`_ingest_scenario_a` raises this immediately before
    the downstream publish if the credential was disconnected since the cycle
    began. :func:`run_one_cycle` and :func:`reprocess_pending_notes` catch it
    to abort the cycle cleanly — no Lane 1/Lane 2 event is emitted for a
    credential the user just disconnected. It carries the credential id for
    logging only; it never escapes the adapter.
    """


@dataclass(frozen=True)
class CycleResult:
    """Summary of one ``run_one_cycle`` invocation. Surfaced to the scheduler.

    ``notes_processed`` counts notes that reached
    :func:`process_note` (regardless of outcome — success, defer, skip,
    fail all count). ``credential_skipped`` is ``True`` when the cycle
    short-circuited because the credential was not in ``status='active'``
    (e.g., already revoked). ``credential_error_code`` is set when the
    cycle ended in a credential-level error (auth failure, folder
    deleted, sustained 5xx), so the caller can choose to alert /
    notify-user. ``deferred_reprocessed`` counts deferred rows from
    prior cycles that were re-checked this cycle (whether they
    completed, stayed deferred, or hit a per-note skip).
    """

    notes_processed: int = 0
    credential_skipped: bool = False
    credential_error_code: Optional[str] = None
    deferred_reprocessed: int = 0
    outcomes: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Frozen dataclass workaround for mutable default.
        if self.outcomes is None:
            object.__setattr__(self, "outcomes", {})


# ---------------------------------------------------------------------------
# Entry point: per-credential cycle
# ---------------------------------------------------------------------------


async def run_one_cycle(
    *,
    credential: GranolaCredential,
    pool: asyncpg.Pool,
    api_client: Optional[GranolaAPIClient] = None,
) -> CycleResult:
    """Drive one polling cycle for a decrypted credential.

    ``api_client``, when provided, is reused (caller owns its lifecycle).
    When ``None``, the cycle constructs its own ``GranolaAPIClient`` and
    closes it on exit. Tests inject a mock client this way.

    Branch summary (full detail in :func:`process_note`):
      - Credential not active → skip the whole cycle.
      - ``list_notes`` returns AUTH_FAILED → credential.status = 'revoked'.
      - ``list_notes`` returns FOLDER_NOT_FOUND → credential.status = 'error'.
      - ``list_notes`` returns other transient errors → increment
        consecutive_failures; if it reaches the threshold, credential.status
        = 'error' (and Phase 2g would email the user).
      - Per-note processing handles its own outcomes; the cycle never
        marks the credential 'error' for a single note's failure.
      - After all notes process, re-poll any deferred-pending-account rows
        from prior cycles in case their unknown domains were approved.
      - On clean exit, update ``last_polled_at`` and reset
        ``consecutive_failures`` to 0.
    """
    if credential.status != "active":
        logger.info(
            f"granola_adapter: credential {credential.id} status={credential.status!r}; skipping cycle"
        )
        return CycleResult(credential_skipped=True)

    outcomes: dict[str, int] = {}
    notes_processed = 0
    deferred_reprocessed = 0

    # Capture cycle-start timestamp BEFORE list_notes (Codex PR-X2 R1 P1
    # finding). Using CURRENT_TIMESTAMP at cycle-end would create a gap:
    # a note created between list_notes() and the end-of-cycle UPDATE
    # would not be in the T0 result set but next cycle's
    # ``created_after=cycle_end_timestamp`` would skip it forever.
    # Snapshotting at start means next cycle's filter is
    # ``created_after=this_cycle_start``, so notes created during the
    # cycle window are picked up on the next poll.
    cycle_start_at = datetime.now(timezone.utc)

    owned_client = api_client is None
    client = api_client or GranolaAPIClient(api_key=credential.api_key)
    try:
        try:
            internal_domains = await get_tenant_internal_domains(str(credential.tenant_id))
        except Exception as exc:
            # internal_domains failures are non-fatal per the helper's
            # contract (returns set()); but if a future refactor lets one
            # leak, surface as a transient credential failure rather than
            # crashing the cycle.
            logger.warning(
                f"granola_adapter: internal_domains lookup failed for tenant "
                f"{credential.tenant_id}; using empty set. error={exc!r}"
            )
            internal_domains = set()

        # Edge #12 (Codex R2 [P2]): gate the FIRST Granola API call too. The
        # credential snapshot was decrypted at cycle start; a /disconnect
        # landing before list_notes should abort before we use the
        # now-disconnected API key to page Granola at all — not just before the
        # downstream publish. For a large folder this avoids a full pagination
        # of upstream reads after the user disconnected.
        if not await _credential_is_active(
            pool=pool,
            credential_id=credential.id,
            tenant_id=credential.tenant_id,
            user_id=credential.user_id,
        ):
            logger.info(
                "granola_adapter: credential %s deactivated before cycle's first "
                "Granola call; skipping (no API request)",
                credential.id,
            )
            return CycleResult(credential_skipped=True)

        try:
            note_summaries = await client.list_notes(
                folder_id=credential.config.get("folder_id", ""),
                created_after=credential.last_polled_at,
            )
        except GranolaError as exc:
            outcome_code = await _handle_credential_level_granola_error(
                exc=exc, credential=credential, pool=pool
            )
            return CycleResult(credential_error_code=outcome_code, outcomes={})

        # Edge #12 (Codex R6 [P2] #1): track mid-cycle deactivation so the
        # end-of-cycle reprocess + success bookkeeping is SKIPPED on abort. The
        # success UPDATE only guards ``archived_at IS NULL``, so for a
        # revoked/error row (archived_at still NULL) it would otherwise clear
        # last_error / reset consecutive_failures / advance last_polled_at on a
        # credential we just detected as deactivated.
        cycle_aborted = False

        for note_summary in note_summaries:
            # Edge #12: re-check the credential is still active before each
            # note. A /disconnect (or a revoke/error transition) that lands
            # mid-cycle must STOP further ingestion immediately rather than
            # keep publishing the remaining notes downstream (the Phase 2f
            # Codex R9 disconnect-during-sync gap).
            if not await _credential_is_active(
                pool=pool,
                credential_id=credential.id,
                tenant_id=credential.tenant_id,
                user_id=credential.user_id,
            ):
                logger.info(
                    "granola_adapter: credential %s deactivated mid-cycle; "
                    "aborting remaining notes (%d processed this cycle)",
                    credential.id, notes_processed,
                )
                cycle_aborted = True
                break
            try:
                outcome = await process_note(
                    credential=credential,
                    note_summary=note_summary,
                    client=client,
                    pool=pool,
                    internal_domains=internal_domains,
                )
            except _CredentialDeactivated:
                # /disconnect (or revoke/error) landed during this note's
                # fetch/classify; a gate aborted before any state mutation.
                # Stop the cycle AND skip the end-of-cycle success bookkeeping
                # (Codex R6 #1) so we don't clear a revoked/error row's
                # last_error or advance its last_polled_at.
                logger.info(
                    "granola_adapter: credential %s deactivated mid-note; "
                    "aborting cycle (%d processed this cycle)",
                    credential.id, notes_processed,
                )
                cycle_aborted = True
                break
            outcomes[outcome.value] = outcomes.get(outcome.value, 0) + 1
            notes_processed += 1

        # Skip the end-of-cycle reprocess + success bookkeeping when the cycle
        # aborted on a mid-cycle deactivation (Codex R6 [P2] #1): a deactivated
        # credential must not have its last_error cleared or last_polled_at
        # advanced, and we must not re-publish prior deferred/failed rows for
        # it. (An archived row would no-op the success UPDATE via the
        # archived_at guard anyway; a revoked/error row would NOT — hence this
        # explicit skip.)
        if not cycle_aborted:
            # Reprocess BOTH deferred and failed rows from prior cycles
            # (Codex PR-X2 R1 P1 finding). A note-level transient failure
            # (5xx on detail fetch, lane2_backpressure, etc) used to be
            # stranded forever once last_polled_at advanced past it; this
            # cycle-end pass gives those rows a retry against the SAME
            # external_id without needing them to reappear in list_notes.
            deferred_reprocessed = await reprocess_pending_notes(
                credential=credential,
                client=client,
                pool=pool,
                internal_domains=internal_domains,
            )

            # Edge #12 (Codex R7 [P2]): the reprocess pass can itself abort on a
            # mid-pass deactivation (it breaks internally + returns a count, not
            # an abort signal). Re-check liveness immediately before the success
            # UPDATE — the cycle's last mutation — so a credential deactivated
            # DURING reprocess (or between the note loop and here) does not get
            # last_error cleared / consecutive_failures reset / last_polled_at
            # advanced.
            if await _credential_is_active(
                pool=pool,
                credential_id=credential.id,
                tenant_id=credential.tenant_id,
                user_id=credential.user_id,
            ):
                await _mark_credential_polled_success(
                    credential=credential, pool=pool, last_polled_at=cycle_start_at
                )
            else:
                logger.info(
                    "granola_adapter: credential %s deactivated by end of cycle; "
                    "skipping success bookkeeping",
                    credential.id,
                )
    finally:
        if owned_client:
            await client.aclose()

    return CycleResult(
        notes_processed=notes_processed,
        deferred_reprocessed=deferred_reprocessed,
        outcomes=outcomes,
    )


# ---------------------------------------------------------------------------
# Per-note processing
# ---------------------------------------------------------------------------


async def process_note(
    *,
    credential: GranolaCredential,
    note_summary: GranolaNoteSummary,
    client: GranolaAPIClient,
    pool: asyncpg.Pool,
    internal_domains: set[str],
) -> IngestionOutcome:
    """Process one note: fetch detail, classify, branch by scenario.

    Idempotency: callers (Phase 2e scheduler) re-invoke this on cycle
    overlap or after restarts. The ``external_integration_runs`` UNIQUE
    on ``(tenant_id, user_id, provider, external_id)`` lets us detect
    prior outcomes — if an earlier cycle already recorded ``success``,
    we short-circuit without re-fetching detail (saves a Granola call
    and avoids double-publishing the envelope, which downstream consumers
    handle but the Lane 2 OpenAI cost is real).
    """
    existing = await _get_integration_run(
        pool=pool,
        tenant_id=credential.tenant_id,
        user_id=credential.user_id,
        external_id=note_summary.id,
    )
    if existing is not None and existing.get("status") == IngestionOutcome.SUCCESS.value:
        # Already ingested by a prior cycle (e.g. Phase 2e DBOS retry).
        return IngestionOutcome.SUCCESS
    if existing is not None and existing.get("status") == IngestionOutcome.FAILED_PERMANENT.value:
        # Permanently failed; Phase 2e scheduler shouldn't have queued us
        # but a manual re-trigger could land here. Don't re-attempt.
        return IngestionOutcome.FAILED_PERMANENT

    # Preserve the eq_interaction_id from any prior 'in_progress'
    # or 'failed' row so a retry re-publishes under the SAME interaction
    # id (downstream consumers dedup on it). Codex PR-X2 R1 P2 fix.
    existing_interaction_id: Optional[UUID] = None
    if existing is not None:
        raw = existing.get("eq_interaction_id")
        if isinstance(raw, UUID):
            existing_interaction_id = raw
        elif isinstance(raw, str) and raw:
            try:
                existing_interaction_id = UUID(raw)
            except ValueError:
                existing_interaction_id = None

    # Edge #12 (Codex R9 [P2]): gate BEFORE the per-note Granola API call. The
    # cycle's loop-top check ran before this function, but `_get_integration_run`
    # above is an awaited DB lookup — a /disconnect landing in that window would
    # otherwise let `get_note_detail` fire a Granola request with the
    # just-disconnected key. Re-check here so a disconnected credential makes no
    # per-note API call either (mirrors the pre-`list_notes` gate in run_one_cycle).
    if not await _credential_is_active(
        pool=pool,
        credential_id=credential.id,
        tenant_id=credential.tenant_id,
        user_id=credential.user_id,
    ):
        logger.info(
            "granola_adapter: credential %s deactivated before note %s detail "
            "fetch; aborting cycle (no Granola API call)",
            credential.id, note_summary.id,
        )
        raise _CredentialDeactivated(credential.id)

    # Fetch the note detail (the one slow external call in the per-note path),
    # capturing any GranolaError instead of returning immediately — so the
    # SINGLE Edge #12 liveness gate below covers BOTH the success and error
    # paths.
    fetch_error: Optional[GranolaError] = None
    detail: Optional[GranolaNoteDetail] = None
    try:
        detail = await client.get_note_detail(note_summary.id)
    except GranolaError as exc:
        fetch_error = exc

    # Edge #12 (Codex R1/R3/R4): ONE liveness gate right after the note's only
    # slow external call. get_note_detail is where a /disconnect realistically
    # lands mid-note; EVERYTHING below it mutates state — the error handler
    # (skipped/failed rows), the Scenario C deferred row + pending-approval
    # signals, the Scenario D skip row, and the Scenario A publish. A single
    # check here gates them all, on BOTH the success and error paths, so an
    # archived credential records nothing (and the Scenario A in_progress
    # anchor is never written on abort). The tighter pre-publish gate inside
    # _ingest_scenario_a remains as the final guard immediately before the
    # Lane 1/Lane 2 emit; the residual classify window is local, sub-ms work.
    if not await _credential_is_active(
        pool=pool,
        credential_id=credential.id,
        tenant_id=credential.tenant_id,
        user_id=credential.user_id,
    ):
        logger.info(
            "granola_adapter: credential %s deactivated during note %s fetch; "
            "aborting cycle (no state mutation)",
            credential.id, note_summary.id,
        )
        raise _CredentialDeactivated(credential.id)

    if fetch_error is not None:
        return await _handle_note_level_granola_error(
            exc=fetch_error,
            credential=credential,
            note_summary=note_summary,
            pool=pool,
            existing=existing,
        )

    assert detail is not None  # set on the success path (no fetch_error)

    decision = await _classify_and_resolve(
        attendees=detail.attendees,
        tenant_id=credential.tenant_id,
        internal_domains=internal_domains,
    )

    # Edge #12 (Codex R5 [P2]): SECOND gate after classification. The post-fetch
    # gate above covers get_note_detail, but _classify_and_resolve itself awaits
    # per-domain account lookups — a real DB window. A /disconnect landing there
    # must still abort before the outcome writes (Scenario C → _defer_pending_account
    # writes a deferred row + pending-approval signals; Scenario D → a skip row).
    # The two gates cover the two distinct async awaits in this path; the
    # reprocess path keeps the same post-classify gate.
    if not await _credential_is_active(
        pool=pool,
        credential_id=credential.id,
        tenant_id=credential.tenant_id,
        user_id=credential.user_id,
    ):
        logger.info(
            "granola_adapter: credential %s deactivated during classification of "
            "note %s; aborting cycle (no state mutation)",
            credential.id, note_summary.id,
        )
        raise _CredentialDeactivated(credential.id)

    if decision.scenario is Scenario.D_NO_BUSINESS:
        await _record_skipped(
            pool=pool,
            credential=credential,
            note_id=note_summary.id,
            reason="no_business_attendees",
            granola_updated_at=note_summary.updated_at,
        )
        return IngestionOutcome.SKIPPED_NO_BUSINESS_ATTENDEES

    if decision.scenario is Scenario.A_KNOWN_ANCHOR:
        # Pre-existing retry_count flows so the budget converges to
        # failed_permanent under sustained outages (Codex PR-X2 R2 P2
        # fix). New row → 0.
        existing_retry_count: int = (existing or {}).get("retry_count", 0) or 0
        return await _ingest_scenario_a(
            credential=credential,
            note_summary=note_summary,
            detail=detail,
            decision=decision,
            pool=pool,
            existing_interaction_id=existing_interaction_id,
            existing_retry_count=existing_retry_count,
        )

    # Scenario C: 0 known accounts, >= 1 unknown business attendee.
    return await _defer_pending_account(
        credential=credential,
        note_summary=note_summary,
        detail=detail,
        decision=decision,
        pool=pool,
    )


async def _classify_and_resolve(
    *,
    attendees: list[Attendee],
    tenant_id: UUID,
    internal_domains: set[str],
) -> PathTwoDecision:
    """Wrap :func:`classify_attendees` + :func:`decide_scenario` with the
    BUSINESS-domain → account_id lookup that Path 2 needs.

    Implementation note: we look up each unique BUSINESS domain once via
    :func:`lookup_account_by_domain` (no batch API exists at present, so
    we issue one query per domain). For typical meetings (<= 5
    business-domain attendees) this is fine; if production data shows
    larger fan-out we can add a batch lookup later.
    """
    # First pass: extract unique domains where the attendee is BUSINESS.
    # We pre-classify with empty account map; the second pass resolves
    # only the BUSINESS domains.
    pre = classify_attendees(
        attendees, internal_domains=internal_domains, domain_to_account_id={}
    )
    business_domains: list[str] = []
    seen: set[str] = set()
    for att in pre:
        if att.domain in seen:
            continue
        from services.domain_classification import DomainClass

        if att.klass is not DomainClass.BUSINESS:
            continue
        seen.add(att.domain)
        business_domains.append(att.domain)

    # Second pass: resolve domain → account_id for each business domain.
    domain_to_account: dict[str, str] = {}
    if business_domains:
        async with get_async_session() as session:
            for domain in business_domains:
                account_id = await lookup_account_by_domain(
                    session=session, tenant_id=str(tenant_id), domain=domain
                )
                if account_id is not None:
                    domain_to_account[domain] = account_id

    full = classify_attendees(
        attendees,
        internal_domains=internal_domains,
        domain_to_account_id=domain_to_account,
    )
    return decide_scenario(full)


# ---------------------------------------------------------------------------
# Scenario A: known account, ingest
# ---------------------------------------------------------------------------


async def _ingest_scenario_a(
    *,
    credential: GranolaCredential,
    note_summary: GranolaNoteSummary,
    detail: GranolaNoteDetail,
    decision: PathTwoDecision,
    pool: asyncpg.Pool,
    existing_interaction_id: Optional[UUID] = None,
    existing_retry_count: int = 0,
) -> IngestionOutcome:
    """Build envelope per LOCKED-35/36 and dispatch via text_clean_service.

    Backpressure is reserved BEFORE the publish so a 503-equivalent
    rejection from text_clean_service (raise: BackpressureError isn't
    raised today because text_clean_service.process expects a
    pre-reserved slot — the adapter reserves via try_reserve_lane2_slot
    here). If the reserve fails, the note is recorded as transient
    failure (retry next cycle).

    ``existing_interaction_id`` (when not None) is reused as
    ``envelope.interaction_id`` so a retry of a publish-then-DB-fail
    race republishes under the SAME interaction id (downstream consumers
    dedup on it). Codex PR-X2 R1 P2 mitigation.

    ``existing_retry_count`` carries the prior row's retry_count so
    repeated Scenario A failures converge to FAILED_PERMANENT under
    sustained backpressure / Lane1-publish outages (Codex PR-X2 R2 P2
    fix). New rows start at 0; on each failure path the count is
    incremented and checked against _PER_NOTE_RETRY_LIMIT.
    """
    if not text_clean_service.try_reserve_lane2_slot():
        in_flight = text_clean_service.get_lane2_in_flight()
        cap = text_clean_service.get_lane2_cap()
        logger.warning(
            f"granola_adapter: Lane 2 backpressure ({in_flight}/{cap}); "
            f"deferring note {note_summary.id} to next cycle"
        )
        return await _record_scenario_a_failure(
            pool=pool,
            credential=credential,
            note_summary=note_summary,
            error_code="lane2_backpressure",
            error_detail={"in_flight": in_flight, "cap": cap},
            existing_retry_count=existing_retry_count,
        )

    slot_held = True
    anchor_account_id = decision.anchor_account_id
    assert anchor_account_id is not None, "Scenario A requires anchor_account_id"

    try:
        interaction_id = existing_interaction_id or uuid4()
        envelope = _build_envelope(
            credential=credential,
            detail=detail,
            anchor_account_id=anchor_account_id,
            decision=decision,
            interaction_id=interaction_id,
        )

        # Pre-write 'in_progress' BEFORE publish so a crash between Lane 1
        # success and the success UPSERT can't lose our idempotency
        # anchor (Codex PR-X2 R1 P2 fix). On next retry, the same
        # eq_interaction_id is recovered and re-published — downstream
        # consumers dedup on interaction_id, so duplicate Lane 1 events
        # don't create duplicate entities downstream (small Lane 2 cost
        # in the crash window is acknowledged).
        await _record_in_progress(
            pool=pool,
            credential=credential,
            note_id=note_summary.id,
            account_id=anchor_account_id,
            eq_interaction_id=interaction_id,
            granola_updated_at=note_summary.updated_at,
        )

        # Edge #12 (Codex R1 [P1]): FINAL liveness gate, immediately before the
        # downstream publish. The cycle's per-note recheck runs before
        # ``process_note`` fetches the note detail + classifies it; a
        # /disconnect (the DELETE endpoint takes no advisory lock) landing
        # during that fetch/classify window would pass the earlier check yet
        # still reach here. Re-checking right before ``text_clean_service.process``
        # closes that window so we never emit a Lane 1/Lane 2 event for a
        # credential the user just disconnected. The ``in_progress`` row written
        # above is a benign, self-healing idempotency anchor: a later reconnect
        # resets ``last_polled_at`` → re-scans → re-publishes under the SAME
        # ``eq_interaction_id`` (downstream dedups), so no duplicate entity.
        if not await _credential_is_active(
            pool=pool,
            credential_id=credential.id,
            tenant_id=credential.tenant_id,
            user_id=credential.user_id,
        ):
            logger.info(
                "granola_adapter: credential %s deactivated before publishing "
                "note %s; aborting cycle (no downstream emit)",
                credential.id, note_summary.id,
            )
            raise _CredentialDeactivated(credential.id)

        try:
            try:
                await text_clean_service.process(
                    tenant_id=credential.tenant_id,
                    user_id=str(credential.user_id),
                    account_id=anchor_account_id,
                    envelope=envelope,
                    # Granola transcripts come pre-clean; do NOT override
                    # cleaned_transcript so Lane 2 analyzes envelope.content.text
                    # (the front-matter + speaker-tagged turns). Skipping
                    # the override is critical per the Phase 2d design.
                    lane2_extras=None,
                )
            finally:
                # text_clean_service.process() owns slot lifecycle on every
                # exit path. Mark not-held so the outer finally is a no-op
                # (preserves the same pattern as routers/text.py post-PR-X1).
                slot_held = False
        except text_clean_service.Lane1PublishError:
            return await _record_scenario_a_failure(
                pool=pool,
                credential=credential,
                note_summary=note_summary,
                error_code="text_clean_lane1_publish_failed",
                error_detail={"note_id": note_summary.id},
                existing_retry_count=existing_retry_count,
            )

        await _record_success(
            pool=pool,
            credential=credential,
            note_id=note_summary.id,
            account_id=anchor_account_id,
            eq_interaction_id=interaction_id,
            granola_updated_at=note_summary.updated_at,
        )

        # Per the brainstorm Scenario A: queue signals for OTHER attendees
        # whose unknown business domains co-occurred in this meeting.
        # Mirrors the pre-existing behavior in TranscriptEnrichmentService.
        await _queue_unknown_domain_signals(
            pool=pool,
            credential=credential,
            decision=decision,
            interaction_id=interaction_id,
        )

        return IngestionOutcome.SUCCESS
    finally:
        if slot_held:
            text_clean_service.release_lane2_slot()


# ---------------------------------------------------------------------------
# Scenario C: defer + capture snapshot
# ---------------------------------------------------------------------------


async def _defer_pending_account(
    *,
    credential: GranolaCredential,
    note_summary: GranolaNoteSummary,
    detail: GranolaNoteDetail,
    decision: PathTwoDecision,
    pool: asyncpg.Pool,
) -> IngestionOutcome:
    """LOCKED-44: capture note snapshot; queue pending-domain signals.

    The snapshot lets the next poll cycle re-process this note even if
    Granola removes/edits the source note before the user approves the
    queued domain. Without it, a deleted-note race would leave a
    deferred row unresolvable.

    Snapshot shape (minimal — enough for the next cycle's Scenario A
    re-run): title, summary_text, attendees (raw dump), web_url,
    captured_at.
    """
    snapshot: dict[str, Any] = {
        "title": detail.title,
        "summary_text": detail.summary_text,
        "web_url": detail.web_url,
        "attendees": [att.model_dump() for att in detail.attendees],
        "transcript_turns": [turn.model_dump() for turn in detail.transcript],
        "calendar_event_id": detail.calendar_event.calendar_event_id if detail.calendar_event else None,
        "created_at": detail.created_at.isoformat(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }

    queue_ids = await _queue_unknown_domain_signals(
        pool=pool,
        credential=credential,
        decision=decision,
        interaction_id=None,  # Scenario C: no interaction yet
    )

    # Link external_integration_runs to the first queue_id (most-common
    # case: 1 unknown domain → 1 queue entry). Multi-domain meetings get
    # the first; admin UI can surface the others via the signal rows.
    queue_id = queue_ids[0] if queue_ids else None

    await _record_deferred(
        pool=pool,
        credential=credential,
        note_id=note_summary.id,
        snapshot=snapshot,
        queue_id=queue_id,
        granola_updated_at=note_summary.updated_at,
    )

    return IngestionOutcome.DEFERRED_PENDING_ACCOUNT


# ---------------------------------------------------------------------------
# Re-poll deferred notes from prior cycles
# ---------------------------------------------------------------------------


async def reprocess_pending_notes(
    *,
    credential: GranolaCredential,
    client: GranolaAPIClient,
    pool: asyncpg.Pool,
    internal_domains: set[str],
) -> int:
    """For each deferred + failed row, attempt re-processing.

    Codex PR-X2 R1 P1 fix — handles BOTH 'deferred_pending_account' and
    'failed' statuses (previously only deferred). A failed row that
    drops out of next cycle's ``list_notes(created_after=...)`` window
    would otherwise be stranded forever.

    Per-row branching:
      * 'deferred_pending_account' with snapshot → try fresh fetch; on
        ``NOTE_NOT_FOUND`` fall back to the snapshot (LOCKED-44).
        Re-classify; promote to Scenario A if the domain is now known.
      * 'failed' → re-fetch detail (no snapshot for these rows since
        defer wasn't entered). On ``NOTE_NOT_FOUND`` mark as
        skipped (note was deleted between attempts). On other errors,
        the existing per-note retry budget applies via
        :func:`_handle_note_level_granola_error`.

    Returns the count of rows that were re-processed (observability;
    not load-bearing).
    """
    rows = await _get_pending_runs(
        pool=pool,
        tenant_id=credential.tenant_id,
        user_id=credential.user_id,
    )

    reprocessed = 0
    for row in rows:
        # Edge #12: the reprocess pass also publishes (Scenario A re-promotion),
        # so it must honor a mid-cycle deactivation too — stop re-publishing if
        # the credential was archived/revoked since the cycle began.
        if not await _credential_is_active(
            pool=pool,
            credential_id=credential.id,
            tenant_id=credential.tenant_id,
            user_id=credential.user_id,
        ):
            logger.info(
                "granola_adapter: credential %s deactivated mid-cycle; "
                "aborting reprocess (%d re-processed this cycle)",
                credential.id, reprocessed,
            )
            break
        reprocessed += 1
        external_id: str = row["external_id"]
        status: str = row.get("status", "")
        snapshot: dict[str, Any] = _coerce_jsonb_dict(row.get("granola_note_snapshot"))

        # Synthetic note_summary to feed process_note's idempotency check
        # + outcome recording. Built once per row; details come from
        # either snapshot or fresh fetch below.
        if status == IngestionOutcome.DEFERRED_PENDING_ACCOUNT.value:
            if not snapshot:
                # Older deferred rows without a snapshot can't be safely
                # re-processed (LOCKED-44 prerequisite). Skip; Phase 2.1
                # may add a backfill job for these.
                logger.info(
                    f"granola_adapter: deferred row external_id={external_id} has no "
                    f"snapshot; skipping re-process"
                )
                continue

            try:
                detail = await client.get_note_detail(external_id)
            except GranolaError as exc:
                if exc.code is GranolaErrorCode.GRANOLA_NOTE_NOT_FOUND:
                    detail = _rebuild_detail_from_snapshot(external_id, snapshot)
                else:
                    logger.info(
                        f"granola_adapter: deferred re-poll {external_id} hit "
                        f"{exc.code.value}; leaving deferred"
                    )
                    continue
        else:
            # status == 'failed' — no snapshot, must re-fetch live. If
            # the note is gone, mark as skipped (we can't recover the
            # content; user can see the skip record in the admin panel).
            try:
                detail = await client.get_note_detail(external_id)
            except GranolaError as exc:
                # Edge #12 (Codex R4 [P2]): gate the reprocess error path too —
                # a /disconnect during this re-fetch must not record skipped/
                # failed retry state for an archived credential.
                if not await _credential_is_active(
                    pool=pool,
                    credential_id=credential.id,
                    tenant_id=credential.tenant_id,
                    user_id=credential.user_id,
                ):
                    logger.info(
                        "granola_adapter: credential %s deactivated during "
                        "reprocess re-fetch; aborting (%d re-processed this cycle)",
                        credential.id, reprocessed,
                    )
                    break
                if exc.code is GranolaErrorCode.GRANOLA_NOTE_NOT_FOUND:
                    await _record_skipped(
                        pool=pool,
                        credential=credential,
                        note_id=external_id,
                        reason="note_deleted_before_retry_succeeded",
                        granola_updated_at=None,
                    )
                    continue
                # Other transient codes: let _handle_note_level_granola_error
                # increment retry_count + possibly transition to permanent.
                synth_summary = GranolaNoteSummary(
                    id=external_id,
                    title=None,
                    created_at=datetime.now(timezone.utc),
                    updated_at=None,
                    folder_membership=[],
                )
                await _handle_note_level_granola_error(
                    exc=exc,
                    credential=credential,
                    note_summary=synth_summary,
                    pool=pool,
                    existing=row,
                )
                continue

        synth_summary = GranolaNoteSummary(
            id=external_id,
            title=detail.title,
            created_at=detail.created_at,
            updated_at=detail.updated_at,
            folder_membership=detail.folder_membership,
        )

        decision = await _classify_and_resolve(
            attendees=detail.attendees,
            tenant_id=credential.tenant_id,
            internal_domains=internal_domains,
        )

        # Edge #12 (Codex R3 [P2]): gate all reprocess outcome branches too
        # (Scenario C re-defer + signals, Scenario D skip, Scenario A
        # re-promotion) — a /disconnect during this row's re-fetch/classify
        # must not mutate state.
        if not await _credential_is_active(
            pool=pool,
            credential_id=credential.id,
            tenant_id=credential.tenant_id,
            user_id=credential.user_id,
        ):
            logger.info(
                "granola_adapter: credential %s deactivated during reprocess "
                "classify; aborting (%d re-processed this cycle)",
                credential.id, reprocessed,
            )
            break

        if decision.scenario is Scenario.A_KNOWN_ANCHOR:
            # Reuse the prior interaction_id (if present) so a retry of
            # a publish-then-DB-fail race re-uses the SAME id —
            # downstream dedup catches duplicate envelopes. Codex
            # PR-X2 R1 P2 fix.
            prior_iid = _coerce_uuid(row.get("eq_interaction_id"))
            existing_retry_count = int(row.get("retry_count") or 0)
            try:
                await _ingest_scenario_a(
                    credential=credential,
                    note_summary=synth_summary,
                    detail=detail,
                    decision=decision,
                    pool=pool,
                    existing_interaction_id=prior_iid,
                    existing_retry_count=existing_retry_count,
                )
            except _CredentialDeactivated:
                # Edge #12: /disconnect landed during this reprocess
                # promotion's publish; abort the reprocess pass (the
                # promotion path is the only reprocess branch that emits
                # downstream).
                logger.info(
                    "granola_adapter: credential %s deactivated during reprocess "
                    "publish; aborting reprocess (%d re-processed this cycle)",
                    credential.id, reprocessed,
                )
                break
        elif decision.scenario is Scenario.D_NO_BUSINESS:
            await _record_skipped(
                pool=pool,
                credential=credential,
                note_id=external_id,
                reason="no_business_attendees",
                granola_updated_at=detail.updated_at,
            )
        else:
            # Scenario C — the row's attendees include unknown business
            # domains and no known anchor. If this is a 'failed' row
            # being replayed (Codex PR-X2 R2 P2 fix), we MUST defer so
            # the granola_note_snapshot is captured + pending-domain
            # signals are queued (LOCKED-44 recoverability). A 'deferred'
            # row being replayed has a snapshot already; calling defer
            # again is a no-op-ish UPSERT that refreshes the captured_at
            # timestamp + ensures signal rows for any newly-discovered
            # attendees in the recovered detail. Either way the row
            # ends up in the approval flow.
            await _defer_pending_account(
                credential=credential,
                note_summary=synth_summary,
                detail=detail,
                decision=decision,
                pool=pool,
            )

    return reprocessed


# Back-compat alias for callers that still import the old name. The
# Phase 2e scheduler design references reprocess_deferred_notes; this
# alias lets that land before the scheduler PR re-points it.
reprocess_deferred_notes = reprocess_pending_notes


def _coerce_uuid(value: Any) -> Optional[UUID]:
    """Normalize asyncpg's UUID return (either UUID or str) to Optional[UUID]."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value:
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


def _rebuild_detail_from_snapshot(
    external_id: str, snapshot: dict[str, Any]
) -> GranolaNoteDetail:
    """Reconstruct a :class:`GranolaNoteDetail` from a defer-time snapshot.

    Used when the live note has been removed (LOCKED-44 recoverability).
    The snapshot was captured by :func:`_defer_pending_account` with the
    minimal fields needed for downstream consumption: attendees,
    transcript, calendar_event_id, title, summary_text, web_url,
    created_at. Reconstructs a Pydantic model from the captured JSON.
    """
    from services.granola_ingestion.models import CalendarEvent

    attendees = [Attendee.model_validate(a) for a in snapshot.get("attendees", [])]
    transcript = [
        TranscriptTurn.model_validate(t) for t in snapshot.get("transcript_turns", [])
    ]
    calendar_event = None
    cal_id = snapshot.get("calendar_event_id")
    if cal_id:
        calendar_event = CalendarEvent(calendar_event_id=cal_id)

    created_at_str = snapshot.get("created_at")
    if isinstance(created_at_str, str):
        created_at = datetime.fromisoformat(created_at_str)
    else:
        created_at = datetime.now(timezone.utc)

    return GranolaNoteDetail(
        id=external_id,
        title=snapshot.get("title"),
        created_at=created_at,
        attendees=attendees,
        calendar_event=calendar_event,
        transcript=transcript,
        summary_text=snapshot.get("summary_text"),
        web_url=snapshot.get("web_url"),
    )


# ---------------------------------------------------------------------------
# Envelope construction (LOCKED-25/35/36)
# ---------------------------------------------------------------------------


def _build_envelope(
    *,
    credential: GranolaCredential,
    detail: GranolaNoteDetail,
    anchor_account_id: str,
    decision: PathTwoDecision,
    interaction_id: Optional[UUID] = None,
) -> EnvelopeV1:
    """Construct the EnvelopeV1 per LOCKED-35/36.

    * ``source="generic"`` — LOCKED-35; verified pre-merge against
      downstream consumers via ``scripts/verify_consumer_contracts.py``.
    * ``interaction_type="meeting"`` — LOCKED-25 (mitigation for the
      raw_interactions FK landmine; ``"meeting"`` is the only
      ``interaction_type`` value verified in the production
      ``interaction_types`` lookup table on the transcript path).
    * ``content.format="plain"`` — LOCKED-35.
    * ``content.text`` — locally-rendered front-matter + speaker-tagged
      transcript turns (NOT cleaned via BatchCleanerService; Granola is
      pre-clean).
    * ``extras`` — exactly the six LOCKED-36 keys; verify_consumer_contracts.py
      checks these.

    The envelope's ``tenant_id`` / ``user_id`` / ``account_id`` are sourced
    from the credential entity + the Path 2 anchor; the
    :func:`text_clean_service.process` call site passes these as explicit
    kwargs too so the LOCKED-41 cross-check passes.
    """
    content_text = _render_content_text(
        detail=detail, decision=decision, credential=credential
    )

    extras: dict[str, Any] = {
        "granola_note_id": detail.id,
        "granola_web_url": detail.web_url,
        "granola_folder_name": credential.config.get("folder_name"),
        "granola_summary_text": detail.summary_text,
        "granola_calendar_event_id": (
            detail.calendar_event.calendar_event_id if detail.calendar_event else None
        ),
        "granola_attendees_raw": [att.model_dump() for att in detail.attendees],
    }

    return EnvelopeV1(
        tenant_id=credential.tenant_id,
        user_id=str(credential.user_id),
        interaction_type="meeting",  # LOCKED-25
        content=ContentModel(text=content_text, format="plain"),  # LOCKED-35
        timestamp=detail.created_at,
        source="generic",  # LOCKED-35
        extras=extras,  # LOCKED-36
        interaction_id=interaction_id or uuid4(),
        # Mint a trace_id: Granola has no request-scoped trace context, but the
        # shared Lane 2 path (text_clean_service → intelligence_service) does
        # ``UUID(envelope.trace_id or "")`` to persist enrichment, so a
        # None/empty trace_id crashes Lane 2 persistence (first real Granola
        # ingest, 2026-05-26 E2E). A per-interaction uuid4 gives the interaction
        # a real trace id without touching the shared service (/text/clean
        # passes its own context.trace_id, unaffected). Idempotent: trace_id is
        # not a dedup key (interaction_id is), so a fresh value on retry is fine.
        trace_id=str(uuid4()),
        account_id=anchor_account_id,
        pg_user_id=str(credential.user_id),
    )


def _render_content_text(
    *,
    detail: GranolaNoteDetail,
    decision: PathTwoDecision,
    credential: GranolaCredential,
) -> str:
    """Compose YAML front-matter + speaker-tagged transcript turns.

    Front-matter shape mirrors :meth:`TranscriptEnrichmentService._compose_front_matter`
    (the existing /text/clean enrichment path) so downstream LLMs see the
    same structured context they're already prompted for. Fields:
      - type: meeting
      - title: from detail.title
      - date: from detail.created_at (UTC ISO 8601)
      - attendees: from Path 2 classifications (known accounts + unknown
        business; we surface email + name + organizer hint when
        available). Personal/internal attendees are omitted per the
        existing pattern — they're not actionable signals.

    Transcript turns are joined with ``[microphone]`` / ``[speaker]``
    labels (Granola's audio-source diarization). Empty transcript still
    produces a valid envelope (front-matter alone) — zero-audio captures
    are legitimate per the Phase 2c spec.
    """
    front_matter = _render_front_matter(
        detail=detail, decision=decision, credential=credential
    )
    turns_text = _render_transcript_turns(detail.transcript)
    if turns_text:
        return f"{front_matter}\n\n{turns_text}"
    return front_matter


def _render_front_matter(
    *,
    detail: GranolaNoteDetail,
    decision: PathTwoDecision,
    credential: GranolaCredential,
) -> str:
    """YAML front-matter block. Matches TranscriptEnrichmentService format."""
    lines = ["---", "type: meeting"]
    if detail.title:
        escaped = detail.title.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'title: "{escaped}"')
    lines.append(f"date: {detail.created_at.strftime('%Y-%m-%dT%H:%M:%SZ')}")

    # Surface business-domain attendees (known + unknown) so downstream
    # LLMs see "who was in the room"; skip personal/internal per the
    # /text/clean pattern.
    rendered: list[str] = []
    for att in decision.known_account_attendees + decision.unknown_business_attendees:
        line = f"  - {att.email}"
        if att.name:
            line += f" ({att.name})"
        rendered.append(line)
    if rendered:
        lines.append("attendees:")
        lines.extend(rendered)

    lines.append("---")
    return "\n".join(lines)


def _render_transcript_turns(turns: list[TranscriptTurn]) -> str:
    """Speaker-source-labeled transcript text.

    Granola's diarization is by audio source only: ``microphone`` is the
    API key holder, ``speaker`` is everything else (single label for
    all non-microphone audio — no name-level resolution). We render
    each turn as ``[microphone] <text>`` or ``[speaker] <text>``, joined
    by newlines. Turns with no speaker dict default to ``[speaker]``.
    """
    out_lines: list[str] = []
    for turn in turns:
        label = "speaker"
        if isinstance(turn.speaker, dict):
            src = turn.speaker.get("source")
            if isinstance(src, str):
                label = src
        out_lines.append(f"[{label}] {turn.text}")
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Credential-level error handling
# ---------------------------------------------------------------------------


async def _handle_credential_level_granola_error(
    *,
    exc: GranolaError,
    credential: GranolaCredential,
    pool: asyncpg.Pool,
) -> str:
    """Classify a :class:`GranolaError` raised during ``list_notes``.

    Branch table (per Phase 2c error catalog + Phase 2d plan):

    * AUTH_FAILED — credential.status = 'revoked' (no retry; auth is
      not transient; Phase 2g would email the user).
    * FOLDER_NOT_FOUND — credential.status = 'error' (folder was
      deleted on Granola side; user must reconfigure via Phase 2f).
    * 5XX / TIMEOUT / RATE_LIMITED — transient: increment
      consecutive_failures; flip to 'error' if at threshold (Phase 2g
      email).
    * NOTE_NOT_FOUND should NEVER reach here (it's a per-note error
      from get_note_detail, not list_notes); if it does, treat as
      transient.
    * PARSE_ERROR — shape changed upstream; treat as transient since
      retries don't help but bounded retries give us time to react
      before the user-visible status flips.
    * HTTP_ERROR — caller-side bug; same transient handling as 5xx.
    """
    code = exc.code
    if code is GranolaErrorCode.GRANOLA_AUTH_FAILED:
        await _mark_credential_revoked(
            credential=credential, pool=pool, error_code=code.value, message=exc.message
        )
        return code.value
    if code is GranolaErrorCode.GRANOLA_FOLDER_NOT_FOUND:
        await _mark_credential_error(
            credential=credential, pool=pool, error_code=code.value, message=exc.message
        )
        return code.value
    # Transient classes.
    new_consecutive = await _record_credential_transient_failure(
        credential=credential, pool=pool, error_code=code.value, message=exc.message
    )
    if new_consecutive >= _CONSECUTIVE_FAILURE_THRESHOLD:
        await _mark_credential_error(
            credential=credential, pool=pool, error_code=code.value, message=exc.message
        )
    return code.value


async def _handle_note_level_granola_error(
    *,
    exc: GranolaError,
    credential: GranolaCredential,
    note_summary: GranolaNoteSummary,
    pool: asyncpg.Pool,
    existing: Optional[dict[str, Any]],
) -> IngestionOutcome:
    """Classify a :class:`GranolaError` raised during ``get_note_detail``.

    The load-bearing branch: ``GRANOLA_NOTE_NOT_FOUND`` is a PER-NOTE
    SKIP, not a credential-level breakage. Phase 2c added this code
    specifically so a deleted-note race (note listed, then deleted
    before detail fetch) doesn't take the whole credential offline.

    * NOTE_NOT_FOUND → record skipped (note_deleted_before_detail_fetch);
      credential stays 'active'.
    * PARSE_ERROR → record failed with retry; eventually FAILED_PERMANENT
      after _PER_NOTE_RETRY_LIMIT attempts.
    * Other transient (5XX/TIMEOUT/RATE_LIMITED) → record failed; retry.
    * AUTH_FAILED / FOLDER_NOT_FOUND on a per-note call would be unusual
      (folder/auth errors typically come from list_notes); if they do
      occur, fall through to credential-level handling.
    """
    code = exc.code
    if code is GranolaErrorCode.GRANOLA_NOTE_NOT_FOUND:
        await _record_skipped(
            pool=pool,
            credential=credential,
            note_id=note_summary.id,
            reason="note_deleted_before_detail_fetch",
            granola_updated_at=note_summary.updated_at,
        )
        # Returning SKIPPED_NO_BUSINESS_ATTENDEES is semantically wrong
        # for this case — we never got to classify. Phase 2.1 may add a
        # SKIPPED_NOTE_DELETED outcome; for MVP we collapse into the
        # existing skip bucket since both end at "no row to ingest".
        return IngestionOutcome.SKIPPED_NO_BUSINESS_ATTENDEES

    if code is GranolaErrorCode.GRANOLA_AUTH_FAILED:
        await _mark_credential_revoked(
            credential=credential, pool=pool, error_code=code.value, message=exc.message
        )
        return IngestionOutcome.FAILED
    if code is GranolaErrorCode.GRANOLA_FOLDER_NOT_FOUND:
        await _mark_credential_error(
            credential=credential, pool=pool, error_code=code.value, message=exc.message
        )
        return IngestionOutcome.FAILED

    # Transient per-note: record failed + increment retry_count.
    retry_count = (existing or {}).get("retry_count", 0) + 1
    if retry_count > _PER_NOTE_RETRY_LIMIT:
        await _record_failed_permanent(
            pool=pool,
            credential=credential,
            note_id=note_summary.id,
            error_code=code.value,
            error_detail={"message": exc.message, "retry_count": retry_count},
            granola_updated_at=note_summary.updated_at,
        )
        return IngestionOutcome.FAILED_PERMANENT

    await _record_failed(
        pool=pool,
        credential=credential,
        note_id=note_summary.id,
        error_code=code.value,
        error_detail={"message": exc.message, "retry_count": retry_count},
        granola_updated_at=note_summary.updated_at,
    )
    return IngestionOutcome.FAILED


# ---------------------------------------------------------------------------
# Pending-domain signal queueing (mirrors transcript_enrichment pattern)
# ---------------------------------------------------------------------------


async def _queue_unknown_domain_signals(
    *,
    pool: asyncpg.Pool,
    credential: GranolaCredential,
    decision: PathTwoDecision,
    interaction_id: Optional[UUID],
) -> list[str]:
    """Queue a pending parent entry per unique domain + one signal per attendee.

    Mirrors :meth:`TranscriptEnrichmentService.enrich`'s per-attendee
    branch (services/transcript_enrichment.py:296-356). The parent
    ``pending_account_mappings`` row is deduped by domain via UPSERT; the
    signal rows record one entry PER ATTENDEE so an admin reviewing the
    queue sees every named contact from that meeting (multiple attendees
    from the same unknown company → multiple signal rows linked to one
    queue parent). Source_type is ``"transcript"`` to match the existing
    taxonomy — Pending Approvals UI surfaces transcript-source signals
    identically regardless of which transcript path emitted them.

    Returns unique-domain queue_ids in domain-first-occurrence order;
    caller may link the FIRST to ``external_integration_runs.queue_id``
    (Scenario C uses this; Scenario A doesn't link since the interaction
    WAS ingested).
    """
    domains = unique_unknown_business_domains(decision)
    if not domains:
        return []

    recording_user_id = str(credential.user_id)
    queue_ids_by_domain: dict[str, str] = {}

    async with get_async_session() as session:
        # First pass: upsert/reopen the parent queue row per UNIQUE domain.
        # ``pending_account_mappings`` has UNIQUE(tenant_id, domain) so the
        # second upsert per same domain is a no-op refresh; the
        # reopen-first pattern matches transcript_enrichment's behavior
        # when the entry was previously archived.
        for domain in domains:
            reopened_id = await reopen_archived_entry(
                session=session, tenant_id=str(credential.tenant_id), domain=domain
            )
            if reopened_id is not None:
                queue_id = reopened_id
            else:
                queue_id = await upsert_queue_entry(
                    session=session,
                    tenant_id=str(credential.tenant_id),
                    domain=domain,
                    owner_user_id=recording_user_id,
                    discovered_from_type="transcript",
                    discovered_from_interaction_id=(
                        str(interaction_id) if interaction_id else None
                    ),
                )
            queue_ids_by_domain[domain] = queue_id

        # Second pass: ONE SIGNAL PER ATTENDEE (multiple attendees from
        # the same unknown domain produce N rows, all linked to the same
        # parent queue_id). Dedup is at the SQL UNIQUE level
        # (queue_id, contact_email, source_type, interaction_id,
        # calendar_event_id).
        for att in decision.unknown_business_attendees:
            queue_id = queue_ids_by_domain[att.domain]
            await insert_signal(
                session=session,
                tenant_id=str(credential.tenant_id),
                queue_id=queue_id,
                proposal=SignalProposal(
                    source_type="transcript",
                    source_user_id=recording_user_id,
                    interaction_id=str(interaction_id) if interaction_id else None,
                    calendar_event_id=None,
                    contact_email=att.email,
                    contact_display_name=att.name,
                    contact_role=None,
                ),
            )
        await session.commit()

    # Return queue_ids in domain-first-occurrence order (matches
    # ``domains`` ordering from unique_unknown_business_domains).
    return [queue_ids_by_domain[d] for d in domains]


# ---------------------------------------------------------------------------
# SQL helpers (vault.user_credentials + public.external_integration_runs)
# ---------------------------------------------------------------------------
#
# These use the shared asyncpg pool directly rather than wrapping in vault
# accessors. Rationale: the encrypted material (api_key, encrypted_dek,
# nonce) is read-only from this module's perspective; only the
# non-encrypted state columns mutate here. Phase 2.1 hardening will add
# a second Postgres role + audit-trail; for MVP the schema separation
# (vault schema) + the adapter's ALLOWLIST entry in vault.user_credentials
# is the boundary.
#
# All SQL is parameterized; tenant_id is always in the WHERE clause; no
# cross-tenant reads. Composite UNIQUE on external_integration_runs
# (tenant_id, user_id, provider, external_id) gives us idempotent upserts
# via ON CONFLICT.


_SELECT_INTEGRATION_RUN_SQL = """
SELECT id, account_id, status, retry_count, granola_note_snapshot, eq_interaction_id
FROM public.external_integration_runs
WHERE tenant_id = $1
  AND user_id = $2
  AND provider = $3
  AND external_id = $4
"""

_SELECT_DEFERRED_INTEGRATION_RUNS_SQL = """
SELECT id, external_id, granola_note_snapshot, retry_count
FROM public.external_integration_runs
WHERE tenant_id = $1
  AND user_id = $2
  AND provider = $3
  AND status = 'deferred_pending_account'
ORDER BY created_at ASC
"""

# Retryable rows = deferred (waiting for unknown-domain approval) +
# failed (transient note-level failure, still within retry budget).
# ``failed_permanent`` rows are excluded so retry-exhausted notes don't
# keep getting reprocessed forever. Codex PR-X2 R1 P1 fix — previously
# only 'deferred_pending_account' rows were reprocessed, so a 'failed'
# note that dropped out of the list_notes ``created_after`` window was
# permanently stranded.
_SELECT_PENDING_INTEGRATION_RUNS_SQL = """
SELECT id, external_id, status, granola_note_snapshot, retry_count, eq_interaction_id
FROM public.external_integration_runs
WHERE tenant_id = $1
  AND user_id = $2
  AND provider = $3
  AND status IN ('deferred_pending_account', 'failed')
ORDER BY created_at ASC
"""

_UPSERT_INTEGRATION_RUN_SQL = """
INSERT INTO public.external_integration_runs (
    id, tenant_id, user_id, account_id, provider, external_id,
    eq_interaction_id, granola_updated_at, ingested_at, status,
    error_code, error_detail, retry_count, granola_note_snapshot,
    queue_id, created_at, updated_at
) VALUES (
    $1, $2, $3, $4, $5, $6,
    $7, $8, $9, $10,
    $11, $12::jsonb, $13, $14::jsonb,
    $15, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
)
ON CONFLICT (tenant_id, user_id, provider, external_id) DO UPDATE
SET account_id = EXCLUDED.account_id,
    -- Preserve the dedupe key when a retry path doesn't supply one.
    -- Without COALESCE, a backpressure/Lane1-failure UPSERT would clobber
    -- the eq_interaction_id from the prior 'in_progress' row, and the
    -- next successful retry would mint a NEW id → downstream dedup
    -- breaks (Codex PR-X2 R2 P2 fix).
    eq_interaction_id = COALESCE(EXCLUDED.eq_interaction_id,
                                  public.external_integration_runs.eq_interaction_id),
    granola_updated_at = EXCLUDED.granola_updated_at,
    ingested_at = EXCLUDED.ingested_at,
    status = EXCLUDED.status,
    error_code = EXCLUDED.error_code,
    error_detail = EXCLUDED.error_detail,
    retry_count = EXCLUDED.retry_count,
    granola_note_snapshot = COALESCE(EXCLUDED.granola_note_snapshot,
                                     public.external_integration_runs.granola_note_snapshot),
    queue_id = EXCLUDED.queue_id,
    updated_at = CURRENT_TIMESTAMP
"""

_UPDATE_CREDENTIAL_POLL_SUCCESS_SQL = """
UPDATE vault.user_credentials
SET last_polled_at = $4,
    consecutive_failures = 0,
    last_error = NULL,
    status = CASE WHEN status = 'active' THEN 'active' ELSE status END,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $1
  AND tenant_id = $2
  AND user_id = $3
  AND archived_at IS NULL
"""

_UPDATE_CREDENTIAL_STATUS_SQL = """
UPDATE vault.user_credentials
SET status = $4,
    last_error = $5::jsonb,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $1
  AND tenant_id = $2
  AND user_id = $3
  AND archived_at IS NULL
"""

_INCREMENT_CREDENTIAL_FAILURES_SQL = """
UPDATE vault.user_credentials
SET consecutive_failures = consecutive_failures + 1,
    last_error = $4::jsonb,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $1
  AND tenant_id = $2
  AND user_id = $3
  AND archived_at IS NULL
RETURNING consecutive_failures
"""

# Edge #12 (plan §2.1 #12): a lightweight, non-decrypting liveness probe for
# the credential row, used to re-check active-state mid-cycle. The three
# credential-state UPDATEs above now also guard on ``archived_at IS NULL`` so a
# stale cycle's terminal write-back is a no-op once the row is archived; this
# SELECT is the complementary pre-publish guard (see ``_credential_is_active``).
_CREDENTIAL_IS_ACTIVE_SQL = """
SELECT EXISTS(
    SELECT 1
    FROM vault.user_credentials
    WHERE id = $1
      AND tenant_id = $2
      AND user_id = $3
      AND status = 'active'
      AND archived_at IS NULL
)
"""


async def _credential_is_active(
    *,
    pool: asyncpg.Pool,
    credential_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
) -> bool:
    """Return True iff the credential row is still active + not archived.

    The ``credential`` snapshot a cycle starts with is read once and goes
    stale: a ``/disconnect`` (soft-delete → ``archived_at`` set) or a
    revoke/error transition that lands MID-cycle is invisible to the
    in-memory object. Re-reading the live lifecycle state before each
    publish lets a credential archived mid-cycle abort the cycle cleanly
    instead of ingesting the remaining notes (plan §2.1 #12 — the
    disconnect-during-sync gap from Phase 2f Codex R9). Tenant + user
    scoped (LOCKED tenant isolation); never decrypts the key.

    **Why a current-state check (not a credential-generation token) is
    sufficient (Codex R6 [P2] #2).** A disconnect→quick-reconnect could in
    principle reactivate the SAME credential id (``reactivate_credential``
    preserves the row id, sets ``status='active'``/``archived_at=NULL``),
    which would make this predicate return True for a stale in-flight cycle
    still holding the old key/folder. That race is structurally prevented:
    BOTH callers of :func:`run_one_cycle` hold the per-credential
    ``pg_try_advisory_lock`` for the whole cycle
    (:func:`services.granola_ingestion.scheduler.run_cycle_step` and
    ``routers.granola._save_and_test_locked``), and
    ``reactivate_credential`` is itself gated on that same lock
    (``routers.granola._credential_poll_lock``) — so a reconnect cannot
    interleave with a running cycle (it 409s until the cycle releases the
    lock). A generation token would only matter for a hypothetical future
    lock-free caller; that's tracked as a defense-in-depth follow-up, not a
    reachable bug today.
    """
    async with pool.acquire() as conn:
        return bool(
            await conn.fetchval(
                _CREDENTIAL_IS_ACTIVE_SQL, credential_id, tenant_id, user_id
            )
        )


async def _get_integration_run(
    *,
    pool: asyncpg.Pool,
    tenant_id: UUID,
    user_id: UUID,
    external_id: str,
) -> Optional[dict[str, Any]]:
    """Read existing integration run row for idempotency check.

    Returns ``None`` if no row exists yet. Returns a dict mapping
    column names to values otherwise (asyncpg Record objects coerced to
    dict for easier downstream branching).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _SELECT_INTEGRATION_RUN_SQL,
            tenant_id,
            user_id,
            _PROVIDER,
            external_id,
        )
        return dict(row) if row else None


async def _get_deferred_runs(
    *,
    pool: asyncpg.Pool,
    tenant_id: UUID,
    user_id: UUID,
) -> list[dict[str, Any]]:
    """List deferred-pending-account rows for re-poll consideration."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _SELECT_DEFERRED_INTEGRATION_RUNS_SQL,
            tenant_id,
            user_id,
            _PROVIDER,
        )
        return [dict(r) for r in rows]


async def _get_pending_runs(
    *,
    pool: asyncpg.Pool,
    tenant_id: UUID,
    user_id: UUID,
) -> list[dict[str, Any]]:
    """List deferred + failed rows for re-poll consideration.

    Codex PR-X2 R1 P1 fix: a 'failed' note dropping out of next cycle's
    list_notes window (because last_polled_at advanced past its
    created_at) would be stranded forever. This query pulls both
    statuses so the cycle-end retry pass picks both up.
    ``failed_permanent`` rows are excluded so retry-exhausted notes
    don't loop.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            _SELECT_PENDING_INTEGRATION_RUNS_SQL,
            tenant_id,
            user_id,
            _PROVIDER,
        )
        return [dict(r) for r in rows]


async def _record_scenario_a_failure(
    *,
    pool: asyncpg.Pool,
    credential: GranolaCredential,
    note_summary: GranolaNoteSummary,
    error_code: str,
    error_detail: dict[str, Any],
    existing_retry_count: int,
) -> IngestionOutcome:
    """Record a Scenario A failure with retry-budget convergence.

    Codex PR-X2 R2 P2 fix: previously each failure recorded
    retry_count=1 regardless of history, so a 'failed' row replayed
    through reprocess_pending_notes would never converge to
    failed_permanent under sustained outages. This helper increments
    the existing count and routes to _record_failed_permanent at the
    threshold.

    Returns :attr:`IngestionOutcome.FAILED` for transient retries or
    :attr:`IngestionOutcome.FAILED_PERMANENT` when the budget is
    exhausted.
    """
    new_retry_count = existing_retry_count + 1
    detail_with_count = {**error_detail, "retry_count": new_retry_count}

    if new_retry_count > _PER_NOTE_RETRY_LIMIT:
        await _record_failed_permanent(
            pool=pool,
            credential=credential,
            note_id=note_summary.id,
            error_code=error_code,
            error_detail=detail_with_count,
            granola_updated_at=note_summary.updated_at,
        )
        return IngestionOutcome.FAILED_PERMANENT

    await _record_failed(
        pool=pool,
        credential=credential,
        note_id=note_summary.id,
        error_code=error_code,
        error_detail=detail_with_count,
        granola_updated_at=note_summary.updated_at,
    )
    return IngestionOutcome.FAILED


async def _record_in_progress(
    *,
    pool: asyncpg.Pool,
    credential: GranolaCredential,
    note_id: str,
    account_id: str,
    eq_interaction_id: UUID,
    granola_updated_at: Optional[datetime],
) -> None:
    """UPSERT external_integration_runs with status='in_progress'.

    Codex PR-X2 R1 P2 fix: written BEFORE Lane 1 publish so a crash
    between publish and the success UPSERT can't strand the
    eq_interaction_id. On the next cycle, ``process_note``'s idempotency
    check sees the in_progress row, retrieves the eq_interaction_id, and
    re-uses it on the retry — downstream consumers dedup on interaction
    id, so duplicate Lane 1 events do not produce duplicate entities.

    The 'in_progress' string is NOT in :class:`IngestionOutcome` since
    it's never a terminal outcome of :func:`process_note`; the next
    UPSERT (success / failed) overwrites it.
    """
    await _upsert_integration_run(
        pool=pool,
        credential=credential,
        note_id=note_id,
        account_id=account_id,
        eq_interaction_id=eq_interaction_id,
        granola_updated_at=granola_updated_at,
        ingested_at=None,
        status=_STATUS_IN_PROGRESS,
    )


async def _record_success(
    *,
    pool: asyncpg.Pool,
    credential: GranolaCredential,
    note_id: str,
    account_id: str,
    eq_interaction_id: UUID,
    granola_updated_at: Optional[datetime],
) -> None:
    """UPSERT external_integration_runs with status='success'."""
    await _upsert_integration_run(
        pool=pool,
        credential=credential,
        note_id=note_id,
        account_id=account_id,
        eq_interaction_id=eq_interaction_id,
        granola_updated_at=granola_updated_at,
        ingested_at=datetime.now(timezone.utc),
        status=IngestionOutcome.SUCCESS.value,
    )


async def _record_deferred(
    *,
    pool: asyncpg.Pool,
    credential: GranolaCredential,
    note_id: str,
    snapshot: dict[str, Any],
    queue_id: Optional[str],
    granola_updated_at: Optional[datetime],
) -> None:
    """UPSERT external_integration_runs with status='deferred_pending_account'.

    LOCKED-44: the snapshot lets a deleted-note re-poll still complete.
    The snapshot column uses COALESCE on conflict so re-upserts (e.g.
    a second cycle re-defers the same note before its domain is
    approved) preserve the original captured snapshot rather than
    overwriting with a possibly-now-stale fetch.
    """
    queue_uuid: Optional[UUID] = UUID(queue_id) if queue_id else None
    await _upsert_integration_run(
        pool=pool,
        credential=credential,
        note_id=note_id,
        account_id=None,
        eq_interaction_id=None,
        granola_updated_at=granola_updated_at,
        ingested_at=None,
        status=IngestionOutcome.DEFERRED_PENDING_ACCOUNT.value,
        granola_note_snapshot=snapshot,
        queue_id=queue_uuid,
    )


async def _record_skipped(
    *,
    pool: asyncpg.Pool,
    credential: GranolaCredential,
    note_id: str,
    reason: str,
    granola_updated_at: Optional[datetime],
) -> None:
    """UPSERT external_integration_runs with status='skipped_no_business_attendees'.

    ``reason`` distinguishes Scenario D ("no_business_attendees") from
    the per-note-deletion path ("note_deleted_before_detail_fetch"); both
    collapse into the same ``status`` for MVP since the operational
    response is identical (no ingestion). The ``error_code`` column
    captures the reason so admin/forensic queries can disambiguate.
    """
    await _upsert_integration_run(
        pool=pool,
        credential=credential,
        note_id=note_id,
        account_id=None,
        eq_interaction_id=None,
        granola_updated_at=granola_updated_at,
        ingested_at=None,
        status=IngestionOutcome.SKIPPED_NO_BUSINESS_ATTENDEES.value,
        error_code=reason,
    )


async def _record_failed(
    *,
    pool: asyncpg.Pool,
    credential: GranolaCredential,
    note_id: str,
    error_code: str,
    error_detail: dict[str, Any],
    granola_updated_at: Optional[datetime],
) -> None:
    """UPSERT external_integration_runs with status='failed'.

    ``retry_count`` is incremented separately by
    :func:`_handle_note_level_granola_error` which passes the new count
    via ``error_detail["retry_count"]``. The UPSERT records this value
    in the dedicated column too.
    """
    retry_count = error_detail.get("retry_count", 1)
    await _upsert_integration_run(
        pool=pool,
        credential=credential,
        note_id=note_id,
        account_id=None,
        eq_interaction_id=None,
        granola_updated_at=granola_updated_at,
        ingested_at=None,
        status=IngestionOutcome.FAILED.value,
        error_code=error_code,
        error_detail=error_detail,
        retry_count=retry_count,
    )


async def _record_failed_permanent(
    *,
    pool: asyncpg.Pool,
    credential: GranolaCredential,
    note_id: str,
    error_code: str,
    error_detail: dict[str, Any],
    granola_updated_at: Optional[datetime],
) -> None:
    """Mark a note's row as permanently failed (retry budget exhausted)."""
    retry_count = error_detail.get("retry_count", _PER_NOTE_RETRY_LIMIT + 1)
    await _upsert_integration_run(
        pool=pool,
        credential=credential,
        note_id=note_id,
        account_id=None,
        eq_interaction_id=None,
        granola_updated_at=granola_updated_at,
        ingested_at=None,
        status=IngestionOutcome.FAILED_PERMANENT.value,
        error_code=error_code,
        error_detail=error_detail,
        retry_count=retry_count,
    )


async def _upsert_integration_run(
    *,
    pool: asyncpg.Pool,
    credential: GranolaCredential,
    note_id: str,
    account_id: Optional[str],
    eq_interaction_id: Optional[UUID],
    granola_updated_at: Optional[datetime],
    ingested_at: Optional[datetime],
    status: str,
    error_code: Optional[str] = None,
    error_detail: Optional[dict[str, Any]] = None,
    retry_count: int = 0,
    granola_note_snapshot: Optional[dict[str, Any]] = None,
    queue_id: Optional[UUID] = None,
) -> None:
    """Single UPSERT path for all external_integration_runs writes.

    Centralizes the parameter ordering + JSONB serialization so callers
    can't drift on column shape. ``id`` is generated per call; the
    composite UNIQUE handles dedup (the inserted id is discarded by the
    ON CONFLICT update path).
    """
    account_uuid = UUID(account_id) if account_id else None
    err_detail_json = (
        json.dumps(error_detail, sort_keys=True) if error_detail is not None else None
    )
    snapshot_json = (
        json.dumps(granola_note_snapshot, sort_keys=True, default=_json_default)
        if granola_note_snapshot is not None
        else None
    )
    async with pool.acquire() as conn:
        await conn.execute(
            _UPSERT_INTEGRATION_RUN_SQL,
            uuid4(),  # id (ignored on conflict)
            credential.tenant_id,
            credential.user_id,
            account_uuid,
            _PROVIDER,
            note_id,
            eq_interaction_id,
            granola_updated_at,
            ingested_at,
            status,
            error_code,
            err_detail_json,
            retry_count,
            snapshot_json,
            queue_id,
        )


async def _mark_credential_polled_success(
    *, credential: GranolaCredential, pool: asyncpg.Pool, last_polled_at: datetime
) -> None:
    """End-of-cycle: reset consecutive_failures, stamp last_polled_at.

    ``last_polled_at`` is supplied by the caller as the CYCLE-START
    timestamp (not cycle-end / CURRENT_TIMESTAMP). Using cycle-start
    prevents the "note created during cycle window" race that Codex
    PR-X2 R1 P1 flagged: a note created between ``list_notes`` and the
    end-of-cycle UPDATE would not be in the result set, and next
    cycle's ``created_after=cycle_end`` filter would skip it forever.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            _UPDATE_CREDENTIAL_POLL_SUCCESS_SQL,
            credential.id,
            credential.tenant_id,
            credential.user_id,
            last_polled_at,
        )


async def _mark_credential_revoked(
    *,
    credential: GranolaCredential,
    pool: asyncpg.Pool,
    error_code: str,
    message: str,
) -> None:
    """Auth-failed: status='revoked'. No retry (auth doesn't improve with time)."""
    await _set_credential_status(
        credential=credential,
        pool=pool,
        status="revoked",
        error_code=error_code,
        message=message,
    )


async def _mark_credential_error(
    *,
    credential: GranolaCredential,
    pool: asyncpg.Pool,
    error_code: str,
    message: str,
) -> None:
    """Credential-level error (folder deleted, sustained 5xx, etc.).

    Status='error'; Phase 2g would send a transactional email. The
    credential remains in the DB; user can rotate via Phase 2f to
    re-activate.
    """
    await _set_credential_status(
        credential=credential,
        pool=pool,
        status="error",
        error_code=error_code,
        message=message,
    )


async def _set_credential_status(
    *,
    credential: GranolaCredential,
    pool: asyncpg.Pool,
    status: str,
    error_code: str,
    message: str,
) -> None:
    """Update credential.status + last_error JSONB. Tenant-scoped."""
    last_error = {
        "error_code": error_code,
        "message": message,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    async with pool.acquire() as conn:
        await conn.execute(
            _UPDATE_CREDENTIAL_STATUS_SQL,
            credential.id,
            credential.tenant_id,
            credential.user_id,
            status,
            json.dumps(last_error, sort_keys=True),
        )


async def _record_credential_transient_failure(
    *,
    credential: GranolaCredential,
    pool: asyncpg.Pool,
    error_code: str,
    message: str,
) -> int:
    """Increment consecutive_failures; return NEW count."""
    last_error = {
        "error_code": error_code,
        "message": message,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _INCREMENT_CREDENTIAL_FAILURES_SQL,
            credential.id,
            credential.tenant_id,
            credential.user_id,
            json.dumps(last_error, sort_keys=True),
        )
        if row is None:
            # Credential disappeared between cycle start and now (archived
            # mid-cycle). Treat as terminal failure.
            return _CONSECUTIVE_FAILURE_THRESHOLD
        return int(row["consecutive_failures"])


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def _coerce_jsonb_dict(value: Any) -> dict[str, Any]:
    """Normalize asyncpg's JSONB return to a dict (or empty dict on absence)."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _json_default(obj: Any) -> Any:
    """JSON serializer for snapshot dicts (datetime → ISO 8601)."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
