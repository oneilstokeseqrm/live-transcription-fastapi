"""
Text cleaning router for raw text ingestion and cleaning.

This router provides the POST /text/clean endpoint for processing raw text
(notes, legacy documents, etc.) without audio processing.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request

from models.text_request import TextCleanRequest, TextCleanResponse
from models.envelope import EnvelopeV1, ContentModel
from services.batch_cleaner_service import BatchCleanerService
from services.aws_event_publisher import AWSEventPublisher
from services.intelligence_service import IntelligenceService
from services.transcript_enrichment import TranscriptEnrichmentService
from services.internal_domains import get_tenant_internal_domains
from utils.context_utils import get_auth_context_ingestion

logger = logging.getLogger(__name__)

router = APIRouter()


# Strong references to in-flight Lane 2 background tasks. The event loop
# only holds weak references to tasks created via asyncio.create_task, so a
# task can be garbage-collected mid-execution if no other reference exists.
# Holding the task here (with discard-on-done) keeps it alive until
# completion. See https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
# Drained on lifespan shutdown — see main.py lifespan.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


# Backpressure cap on in-flight Lane 2 background tasks. Without a cap,
# bursty /text/clean traffic can accumulate unbounded concurrent OpenAI
# calls + Postgres sessions because the response no longer naturally
# throttles request concurrency (Codex /codex review P1 #2 on PR #23,
# 2026-05-20). At 50, the bound holds Lane 2 work to roughly worker memory
# + OpenAI rate-limit headroom while letting normal synthetic-injection
# cadence (1 request per 30-60s, Lane 2 takes 100-160s → 2-5 in-flight)
# pass through untouched. Tune via env var if production observability
# shows steady-state >25 in-flight tasks.
#
# Read at request time (not module import) because main.py imports this
# module before calling load_dotenv() — capturing the env var at import
# would freeze the default and ignore .env overrides (Codex round-6 P2).
import os as _os


def _max_background_tasks() -> int:
    return int(_os.environ.get("TEXT_CLEAN_MAX_BG_TASKS", "50"))


# Atomic in-flight counter for Lane 2 backpressure. Incremented BEFORE any
# ``await`` in the handler so a burst of concurrent requests can't all
# observe the same stale ``len(_BACKGROUND_TASKS)`` and overshoot the cap
# (Codex /codex review round-4 P1 on PR #23). Decremented in ``_on_done``
# (success path) and in the handler's except path (Lane 1 failure /
# unexpected error before ``create_task``). Single-element list keeps the
# state mutable from inner closures without needing ``global``.
_INFLIGHT_LANE2: list[int] = [0]


# Known trade-off (Codex /codex review P1 #1 on PR #23, 2026-05-20):
# Moving Lane 2 to fire-and-forget WIDENS an existing race between
# ``TranscriptEnrichmentService.enrich`` creating
# pending_account_mapping_signals and Lane 2's intelligence_service
# writing raw_interactions. ``services/account_provisioning/materialization.py:80-83``
# already documents this race as "narrow and self-resolving" (DBOS retries
# the workflow path; /map returns 503 → client retries). With Lane 2
# running 100-160s in the background, the race window widens beyond
# DBOS's default retry budget (~65s, 3 attempts), so interactive admin
# approvals of unknown-domain signals during the Lane 2 window may
# permanently fail until the operator retries manually.
#
# We considered an eager raw_interactions INSERT here to close the race,
# but it tripped a deeper pre-existing schema gap: ``interaction_type='note'``
# (the /text/clean default) is NOT a valid foreign-key target in the
# ``interaction_types`` lookup table on the production eq-dev branch.
# Lane 2's existing ``_persist_contact_links`` INSERT has been silently
# failing the FK and rolling back for all note + known-contact calls —
# logged as "Contact link persistence failed (non-fatal)" and unobserved
# because raw_interactions is internal. The materialization comment's
# "Lane 2 writes raw_interactions" claim is wrong for /text/clean → note.
#
# Properly closing this race requires: (a) seeding ``interaction_types``
# with 'note', (b) reviewing why the rollback was tolerated for so long,
# (c) a coordinated fix across eq-frontend Prisma schema +
# live-transcription-fastapi. Tracked as a Phase 2 follow-up; not
# blocking the immediate synthetic-injection unblock (synthetic flows
# don't approve queue signals, so the race doesn't apply).


@router.post("/clean", response_model=TextCleanResponse)
async def clean_text(body: TextCleanRequest, request: Request):
    """
    Clean raw text and publish to ecosystem.
    
    This endpoint accepts raw text, cleans it using the BatchCleanerService,
    and publishes an EnvelopeV1 event with interaction_type="note".
    
    Args:
        body: TextCleanRequest with text, optional metadata, and source
        request: FastAPI Request object for header validation
        
    Returns:
        TextCleanResponse with raw_text, cleaned_text, and interaction_id
        
    Raises:
        HTTPException: 400 for validation errors (missing headers, empty text)
        
    Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
    """
    # Validate and extract context (raises HTTPException 401/400 on failure)
    # Supports JWT from gateway (preferred) or legacy headers (when ALLOW_LEGACY_HEADER_AUTH=true)
    context = get_auth_context_ingestion(request)
    
    logger.info(
        f"Text cleaning started: interaction_id={context.interaction_id}, "
        f"tenant_id={context.tenant_id}, user_id={context.user_id}, "
        f"text_length={len(body.text)}"
    )
    
    # Additional whitespace validation (Pydantic validator handles this,
    # but we add explicit check for clearer error message)
    if not body.text.strip():
        logger.warning(
            f"Empty text rejected: interaction_id={context.interaction_id}"
        )
        raise HTTPException(
            status_code=400,
            detail="text field cannot contain only whitespace"
        )

    # Reject body/header account_id mismatch. The auth-context account_id
    # (X-Account-ID header) is the source of truth; a mismatch indicates
    # inconsistent client behavior or a tampering attempt — 400 loudly rather
    # than silently picking one source. (Phase 1 / T1.26.2)
    if body.account_id != context.account_id:
        logger.warning(
            f"account_id mismatch: interaction_id={context.interaction_id}, "
            f"body.account_id={body.account_id}, context.account_id={context.account_id}"
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "account_id mismatch: body.account_id and X-Account-ID header must agree. "
                "The authenticated account_id is the source of truth."
            ),
        )

    # Backpressure: cap concurrent Lane 2 background tasks so bursty traffic
    # can't spawn unbounded OpenAI calls + DB sessions. Atomic check +
    # reserve happens BEFORE any side-effecting awaits (enrichment writes
    # pending_account_mapping_signals; cleaner spends LLM tokens) so a
    # 503 rejection produces zero side effects (Codex /codex review
    # round-5 P1 #1). Single-threaded event loop guarantees this read +
    # increment is atomic with respect to other coroutines.
    _cap = _max_background_tasks()
    if _INFLIGHT_LANE2[0] >= _cap:
        logger.warning(
            f"Lane 2 backpressure: {_INFLIGHT_LANE2[0]} tasks in flight "
            f"(cap {_cap}); rejecting "
            f"interaction_id={context.interaction_id} with 503"
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Server intelligence-extraction queue is full; retry after "
                "Retry-After seconds."
            ),
            headers={"Retry-After": "60"},
        )
    # Reserve atomically before any await. ``slot_handed_off`` is a
    # 1-element list (mutable across the try/finally below) — flipped to
    # True only after ``asyncio.create_task`` has spawned the Lane 2 task
    # whose _on_done decrements. If any failure between here and there
    # raises (enrichment write, cleaning LLM call, Lane 1 publish), the
    # finally releases the slot.
    _INFLIGHT_LANE2[0] += 1
    slot_handed_off = [False]
    try:
        return await _process_after_slot_reserved(
            body, context, slot_handed_off
        )
    finally:
        if not slot_handed_off[0]:
            _INFLIGHT_LANE2[0] -= 1


async def _process_after_slot_reserved(
    body: TextCleanRequest,
    context,
    slot_handed_off: list,
) -> TextCleanResponse:
    """Post-backpressure body of /text/clean. Extracted so the
    backpressure slot can be released via a try/finally in ``clean_text``
    if anything in here raises before ``create_task`` hands the slot to
    a Lane 2 Task. Flip ``slot_handed_off[0]`` to True the moment the
    Task is created — the outer try/finally then correctly leaves the
    slot alone (decrement is owned by ``_on_done``).
    """
    # Pass body.participants so manual-notes flows (no calendar event in the
    # time window) still resolve contacts and queue unknown-domain signals.
    # See TranscriptEnrichmentService.enrich() docstring for caller-wins
    # semantics when both a calendar match and body.participants are present.
    # (Task 1.26.6)
    enrichment_service = TranscriptEnrichmentService()
    transcript_ts = datetime.now(timezone.utc)
    enrichment = await enrichment_service.enrich(
        tenant_id=context.tenant_id,
        transcript_timestamp=transcript_ts,
        raw_transcript=body.text,
        user_name=context.user_name,
        account_id=context.account_id,
        recording_user_id=context.pg_user_id or context.user_id,
        tenant_internal_domains=await get_tenant_internal_domains(context.tenant_id),
        participants=body.participants,
        # Codex Round 4 P2: thread the request's interaction_id into enrich()
        # so queue-signal rows have a non-NULL anchor when there's no calendar
        # match. Without this, retries can't dedupe under pending_signal_dedup.
        interaction_id=context.interaction_id,
    )

    # Prepend front-matter to text before cleaning (LLM sees attendee context)
    text_for_cleaning = body.text
    if enrichment.front_matter:
        text_for_cleaning = enrichment.front_matter + "\n\n" + body.text

    # Clean text using BatchCleanerService
    try:
        cleaner_service = BatchCleanerService()
        logger.info(
            f"Starting text cleaning: interaction_id={context.interaction_id}"
        )
        cleaned_text = await cleaner_service.clean_transcript(text_for_cleaning)
        logger.info(
            f"Text cleaning complete: interaction_id={context.interaction_id}, "
            f"cleaned_length={len(cleaned_text)}"
        )
    except Exception as e:
        # Requirement 3.6: Return original text on cleaning failure
        logger.error(
            f"Text cleaning failed, returning original: "
            f"interaction_id={context.interaction_id}, "
            f"error={type(e).__name__}: {str(e)}",
            exc_info=True
        )
        cleaned_text = body.text

    # Build extras dict: merge request metadata with optional user_name
    extras = dict(body.metadata) if body.metadata else {}
    if context.user_name:
        extras["user_name"] = context.user_name

    # Add enrichment metadata to extras
    extras.update(enrichment.to_extras_dict())

    # Include front-matter in content.text for downstream LLMs
    content_text = cleaned_text
    if enrichment.front_matter:
        content_text = enrichment.front_matter + "\n\n" + cleaned_text

    # Build EnvelopeV1 with interaction_type="note" (Requirement 3.5)
    envelope = EnvelopeV1(
        tenant_id=UUID(context.tenant_id),
        user_id=context.user_id,
        interaction_type=body.interaction_type,
        content=ContentModel(text=content_text, format="plain"),
        timestamp=transcript_ts,
        source=body.source,
        extras=extras,
        interaction_id=UUID(context.interaction_id),
        trace_id=context.trace_id,
        account_id=context.account_id,
        pg_user_id=context.pg_user_id,
    )
    
    interaction_id_str = context.interaction_id

    # Lane 1 — Kinesis/EventBridge publish runs SYNCHRONOUSLY before the
    # response. It's fast (~50-200ms), it's the durable side-effect that
    # downstream consumers (envelope subscribers) rely on, and pre-PR it
    # ran on the response path anyway. Moving it to the background would
    # introduce a new loss window on every /text/clean call (worker
    # crash/OOM/restart between response-200 and publish-complete loses
    # the only Kinesis/EventBridge emit with no retry path). Codex /codex
    # review P2 on PR #23 — accepted; keeping Lane 1 awaited.
    #
    # ``AWSEventPublisher.publish_envelope`` does NOT raise on normal AWS
    # failure paths — it logs internally and returns a dict whose values
    # may both be None (Codex /codex review round-3 P1 #1). To make the
    # "Lane 1 must succeed" contract real, check the returned dict and
    # raise 502 when neither destination accepted the envelope.
    #
    # But: if BOTH ENABLE_KINESIS_PUBLISHING and ENABLE_EVENTBRIDGE_PUBLISHING
    # are explicitly disabled (dev/local mode), the null result is a
    # supported configuration and must NOT 502 — Codex round-4 P2. We
    # discriminate by reading the same env vars publish_envelope uses
    # internally (services/aws_event_publisher.py:318-319).
    #
    # A Lane 1 failure produces HTTP 502 — client retries with the same
    # interaction_id (Kinesis sequence + EventBridge dedup idempotent).
    try:
        publisher = AWSEventPublisher()
        lane1_result = await publisher.publish_envelope(envelope)
    except Exception as e:
        # Slot release is handled by the outer try/finally in clean_text
        # (slot_handed_off is still False at this point).
        logger.error(
            f"Lane 1 (publishing) raised: interaction_id={interaction_id_str}, "
            f"error={type(e).__name__}: {str(e)}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail="Could not publish interaction envelope downstream",
        )

    # Match pre-PR behavior for the returned-dict shape: log per-destination
    # status, do NOT 502 on null. ``publish_envelope`` already returns
    # ``{None, None}`` legitimately for several supported configurations:
    # AWS credentials absent (main.validate_aws_credentials lets the app
    # continue), feature flags explicitly disabled (dev/local mode), or
    # transient boto3 client init failure. Discriminating "null because
    # outage" from these supported nulls requires a richer signal than the
    # current dict carries — the pre-PR code didn't try, and going beyond
    # that here would 502 on every credentialless dev request. Pre-existing
    # observability gap; deferred. The Codex rounds 3/5/6/7 discussion is
    # captured in PR #23 review history.
    logger.info(
        f"Envelope publish complete: interaction_id={interaction_id_str}, "
        f"kinesis={'success' if (lane1_result or {}).get('kinesis_sequence') else 'failed-or-disabled'}, "
        f"eventbridge={'success' if (lane1_result or {}).get('eventbridge_id') else 'failed-or-disabled'}"
    )

    # Lane 2 — intelligence extraction runs as fire-and-forget background work.
    # The HTTP response returns as soon as cleaned_text + Lane 1 are done;
    # Lane 2 lands in Postgres without blocking the response.
    #
    # Why: Lane 2 (GPT-4o intelligence extraction) takes 100-160s in production.
    # Awaiting it on the response path caused Railway's edge proxy (~300s hard
    # cap) to drop the client TCP under sustained load — server-side writes
    # succeeded but the client saw RemoteProtocolError, producing split-brain
    # state (see tests/integration/test_text_clean_response_decoupling.py for
    # the contract this preserves). The /upload/complete endpoint uses the
    # same asyncio.create_task pattern (routers/upload.py:331).
    #
    # Durability trade-off (Codex /codex review P1 #2 on PR #23): worker
    # crash/restart during the Lane 2 window drops the intelligence
    # extraction silently — the client already got HTTP 200. The lifespan
    # shutdown drain in main.py mitigates graceful SIGTERM (Railway's normal
    # restart path); abrupt SIGKILL/OOM still loses work. Phase 2 path:
    # move Lane 2 to DBOS (already the substrate for account_provisioning
    # workflows) for cross-restart durability.

    async def _lane2_intelligence() -> Optional[object]:
        """Lane 2: Extract and persist intelligence."""
        try:
            intelligence_service = IntelligenceService()
            return await intelligence_service.process_transcript(
                cleaned_transcript=cleaned_text,
                interaction_id=interaction_id_str,
                tenant_id=context.tenant_id,
                trace_id=context.trace_id,
                interaction_type=body.interaction_type,
                account_id=context.account_id,
                contact_ids=enrichment.contact_ids or None,
                calendar_event_id=enrichment.calendar_event_id,
                enrichment_confidence=enrichment.match_confidence,
                enrichment_match_method=enrichment.match_method,
            )
        except Exception as e:
            logger.error(
                f"Lane 2 (intelligence) failed (non-fatal, background): "
                f"interaction_id={interaction_id_str}, "
                f"error={type(e).__name__}: {str(e)}",
                exc_info=True,
            )
            raise

    def _on_done(task: asyncio.Task) -> None:
        """Wrapper-level safety net for the Lane 2 background task itself.

        ``_lane2_intelligence`` catches per-call exceptions inside its body
        and logs them. This callback handles the orthogonal case: an
        exception raised by Python machinery around the coroutine (a bug in
        a future refactor, GeneratorExit during shutdown, anything else
        that would otherwise die silently as Python's "Task exception was
        never retrieved" GC warning). Under the old synchronous-await
        model, such failures became HTTP 5xx and were observable; after
        moving to fire-and-forget, they MUST surface as a logger.error here.

        Also releases the backpressure slot reserved at handler entry.
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
                f"error={type(exc).__name__}: {str(exc)}",
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
    # Slot ownership has passed to the Task; _on_done owns the decrement.
    # The outer clean_text try/finally checks this flag and leaves the
    # counter alone if True.
    slot_handed_off[0] = True

    logger.info(
        f"Text cleaning response dispatched (Lane 2 running in background): "
        f"interaction_id={interaction_id_str}"
    )

    return TextCleanResponse(
        raw_text=body.text,
        cleaned_text=cleaned_text,
        interaction_id=interaction_id_str,
    )
