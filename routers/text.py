"""
Text cleaning router for raw text ingestion and cleaning.

This router provides the POST /text/clean endpoint for processing raw text
(notes, legacy documents, etc.) without audio processing.

Lane 1 (publish) + Lane 2 (intelligence) dispatch + backpressure live in
:mod:`services.text_clean_service` since PR-X1 of the Granola integration
(phase 2d prep). The Granola ingestion adapter calls the same module
directly per LOCKED-41. The route handler in this file continues to own
HTTP-shaped concerns: auth context, request validation, calendar-matching
enrichment, LLM text cleaning, and envelope construction.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request

from models.text_request import TextCleanRequest, TextCleanResponse
from models.envelope import EnvelopeV1, ContentModel
from services.batch_cleaner_service import BatchCleanerService
from services.transcript_enrichment import TranscriptEnrichmentService
from services.internal_domains import get_tenant_internal_domains
from services import text_clean_service
from utils.context_utils import get_auth_context_ingestion

logger = logging.getLogger(__name__)

router = APIRouter()


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
        HTTPException: 400 for validation errors (missing headers, empty text),
                       502 on downstream publish failure,
                       503 when Lane 2 backpressure cap is reached.

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
    # round-5 P1 #1 on PR #23). Single-threaded event loop guarantees this
    # read + increment is atomic w.r.t. other coroutines (no ``await``
    # between them).
    #
    # The reservation lives in services.text_clean_service; the Granola
    # adapter (PR-X2) reserves from the same shared cap so neither caller
    # can exhaust the cap independently of the other.
    if not text_clean_service.try_reserve_lane2_slot():
        in_flight = text_clean_service.get_lane2_in_flight()
        cap = text_clean_service.get_lane2_cap()
        logger.warning(
            f"Lane 2 backpressure: {in_flight} tasks in flight "
            f"(cap {cap}); rejecting "
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

    # ``slot_held`` tracks whether the reserved Lane 2 slot still belongs
    # to this request. The slot is consumed (handed off to a background
    # task) when ``text_clean_service.process`` succeeds; the outer
    # ``finally`` then leaves it alone. The slot is released here when
    # the caller raises BEFORE ``process()`` is called (enrichment crash,
    # cleaning crash, etc.) OR when ``process()`` raises something other
    # than ``Lane1PublishError`` (which releases the slot internally).
    slot_held = True
    try:
        # Pass body.participants so manual-notes flows (no calendar event
        # in the time window) still resolve contacts and queue unknown-
        # domain signals. See TranscriptEnrichmentService.enrich() docstring
        # for caller-wins semantics when both a calendar match and
        # body.participants are present. (Task 1.26.6)
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
            # Codex Round 4 P2: thread the request's interaction_id into
            # enrich() so queue-signal rows have a non-NULL anchor when
            # there's no calendar match. Without this, retries can't dedupe
            # under pending_signal_dedup.
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

        # Build EnvelopeV1 with interaction_type from request body
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

        # Lane 1 + Lane 2 delegate to services.text_clean_service. Lane 1
        # is awaited synchronously inside ``process()``; Lane 2 dispatches
        # a background task that consumes the reserved Lane 2 slot. A Lane 1
        # raise surfaces as ``Lane1PublishError`` (HTTP 502); the helper
        # releases the slot internally before raising on that path.
        #
        # Pre-extraction this was an inline ``AWSEventPublisher().publish_envelope()``
        # await followed by ``asyncio.create_task(_lane2_intelligence())``.
        # The Granola ingestion adapter (PR-X2) calls this same helper to
        # avoid duplicating the publish + dispatch + safety-net logic.
        #
        # ``process()`` owns the slot lifecycle from the moment we call it:
        # on success it consumes the slot via Lane 2 dispatch (released by
        # ``_on_done`` when the background task completes); on Lane1PublishError
        # OR any other exception (e.g. ``asyncio.create_task`` failing during
        # shutdown), ``process()``'s own ``finally`` releases the slot. The
        # router MUST mark the slot as no-longer-held in BOTH paths, otherwise
        # the outer ``finally`` double-decrements the counter and breaks
        # backpressure for subsequent requests (Codex PR-X1 R1 P2 finding).
        try:
            try:
                result = await text_clean_service.process(
                    envelope=envelope,
                    lane2_extras=text_clean_service.Lane2Extras(
                        cleaned_transcript=cleaned_text,
                        contact_ids=enrichment.contact_ids or None,
                        calendar_event_id=enrichment.calendar_event_id,
                        enrichment_confidence=enrichment.match_confidence,
                        enrichment_match_method=enrichment.match_method,
                    ),
                )
            finally:
                # Transfer slot ownership unconditionally: process() now owns
                # it on every exit path (return, Lane1PublishError, or any
                # other raise). See block comment above for the double-
                # decrement bug this prevents.
                slot_held = False
        except text_clean_service.Lane1PublishError:
            raise HTTPException(
                status_code=502,
                detail="Could not publish interaction envelope downstream",
            )

        logger.info(
            f"Text cleaning response dispatched (Lane 2 running in background): "
            f"interaction_id={result.interaction_id}"
        )

        return TextCleanResponse(
            raw_text=body.text,
            cleaned_text=cleaned_text,
            interaction_id=result.interaction_id,
        )
    finally:
        if slot_held:
            text_clean_service.release_lane2_slot()
