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
from utils.context_utils import get_validated_context

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
        HTTPException: 400 for validation errors (missing headers, empty text)
        
    Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
    """
    # Validate headers and extract context (raises HTTPException 400 on failure)
    context = get_validated_context(request)
    
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
    
    # Clean text using BatchCleanerService
    try:
        cleaner_service = BatchCleanerService()
        logger.info(
            f"Starting text cleaning: interaction_id={context.interaction_id}"
        )
        cleaned_text = await cleaner_service.clean_transcript(body.text)
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
    
    # Build EnvelopeV1 with interaction_type="note" (Requirement 3.5)
    envelope = EnvelopeV1(
        tenant_id=UUID(context.tenant_id),
        user_id=context.user_id,
        interaction_type="note",
        content=ContentModel(text=cleaned_text, format="plain"),
        timestamp=datetime.now(timezone.utc),
        source=body.source,
        extras=body.metadata or {},
        interaction_id=UUID(context.interaction_id),
        trace_id=context.trace_id
    )
    
    # Async Fork - Execute Lane 1 (publishing) and Lane 2 (intelligence) concurrently
    async def _lane1_publish() -> Optional[dict]:
        """Lane 1: Publish envelope to Kinesis/EventBridge."""
        try:
            publisher = AWSEventPublisher()
            return await publisher.publish_envelope(envelope)
        except Exception as e:
            logger.error(
                f"Lane 1 (publishing) error: interaction_id={context.interaction_id}, error={e}"
            )
            raise
    
    async def _lane2_intelligence() -> Optional[object]:
        """Lane 2: Extract and persist intelligence."""
        try:
            intelligence_service = IntelligenceService()
            return await intelligence_service.process_transcript(
                cleaned_transcript=cleaned_text,
                interaction_id=context.interaction_id,
                tenant_id=context.tenant_id,
                trace_id=context.trace_id,
                interaction_type="note"
            )
        except Exception as e:
            logger.error(
                f"Lane 2 (intelligence) error: interaction_id={context.interaction_id}, error={e}"
            )
            raise
    
    # Execute both lanes concurrently with error isolation
    results = await asyncio.gather(
        _lane1_publish(),
        _lane2_intelligence(),
        return_exceptions=True
    )
    
    # Log results without failing the request
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            lane_name = "Lane 1 (publishing)" if i == 0 else "Lane 2 (intelligence)"
            logger.error(
                f"{lane_name} failed (non-critical): interaction_id={context.interaction_id}, "
                f"error={type(result).__name__}: {str(result)}",
                exc_info=result
            )
        else:
            lane_name = "Lane 1 (publishing)" if i == 0 else "Lane 2 (intelligence)"
            if i == 0 and result:
                logger.info(
                    f"Envelope published: interaction_id={context.interaction_id}, "
                    f"kinesis={'success' if result.get('kinesis_sequence') else 'failed'}, "
                    f"eventbridge={'success' if result.get('eventbridge_id') else 'failed'}"
                )
            else:
                logger.info(f"{lane_name} completed: interaction_id={context.interaction_id}")
    
    logger.info(
        f"Text cleaning request complete: interaction_id={context.interaction_id}"
    )
    
    return TextCleanResponse(
        raw_text=body.text,
        cleaned_text=cleaned_text,
        interaction_id=context.interaction_id
    )
