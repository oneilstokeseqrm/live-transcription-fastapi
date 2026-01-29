"""
Batch processing router for audio file transcription and cleaning.

This router provides the POST /batch/process endpoint for processing audio
files through transcription and cleaning, publishing EnvelopeV1 events.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, UploadFile, HTTPException, Request
from pydantic import BaseModel, Field

from models.envelope import EnvelopeV1, ContentModel
from services.batch_service import BatchService
from services.batch_cleaner_service import BatchCleanerService
from services.aws_event_publisher import AWSEventPublisher
from services.intelligence_service import IntelligenceService
from utils.context_utils import get_auth_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/batch", tags=["batch"])


class BatchProcessResponse(BaseModel):
    """Response from the batch processing endpoint."""
    raw_transcript: str = Field(..., description="Original transcript from Deepgram")
    cleaned_transcript: str = Field(..., description="Cleaned transcript")
    interaction_id: str = Field(..., description="Unique identifier for this interaction")

# File validation constants
ALLOWED_EXTENSIONS = {"wav", "mp3", "flac", "m4a", "webm", "mp4"}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB in bytes


@router.post("/process", response_model=BatchProcessResponse)
async def process_batch_audio(file: UploadFile, request: Request):
    """
    Process an uploaded audio file through transcription and cleaning pipeline.
    
    Args:
        file: Audio file upload (WAV, MP3, FLAC, or M4A format, max 100MB)
        request: FastAPI Request object for context extraction
        
    Returns:
        BatchProcessResponse with raw_transcript, cleaned_transcript, and interaction_id
        
    Raises:
        HTTPException: 400 for validation errors, 500 for processing errors
        
    Requirements: 1.1, 1.2, 2.1, 2.2, 2.3, 2.4, 2.5
    """
    # Extract and validate request context (raises HTTPException 401/400 on failure)
    # Supports JWT from gateway (preferred) or legacy headers (when ALLOW_LEGACY_HEADER_AUTH=true)
    # Requirements: 1.1, 1.2
    context = get_auth_context(request)
    
    processing_id = str(uuid.uuid4())
    logger.info(
        f"Batch processing started: processing_id={processing_id}, "
        f"interaction_id={context.interaction_id}, filename={file.filename}"
    )
    
    # Validate file extension
    if not file.filename:
        logger.warning(
            f"No filename provided: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}"
        )
        raise HTTPException(status_code=400, detail="No filename provided")
    
    file_extension = file.filename.split(".")[-1].lower()
    if file_extension not in ALLOWED_EXTENSIONS:
        logger.warning(
            f"Invalid file format: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}, "
            f"extension={file_extension}, allowed={ALLOWED_EXTENSIONS}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file format. Allowed formats: {', '.join(ALLOWED_EXTENSIONS)}"
        )
    
    # Read file bytes
    try:
        audio_bytes = await file.read()
    except Exception as e:
        logger.error(
            f"Failed to read file: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}, error={e}"
        )
        raise HTTPException(status_code=400, detail="Failed to read uploaded file")
    
    # Validate file size
    file_size = len(audio_bytes)
    if file_size > MAX_FILE_SIZE:
        logger.warning(
            f"File too large: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}, "
            f"size={file_size}, max={MAX_FILE_SIZE}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE / (1024 * 1024):.0f}MB"
        )
    
    logger.info(
        f"File validated: processing_id={processing_id}, "
        f"interaction_id={context.interaction_id}, "
        f"size={file_size} bytes, extension={file_extension}"
    )
    
    # Determine MIME type
    mime_type_map = {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "flac": "audio/flac",
        "m4a": "audio/mp4",
        "webm": "audio/webm",
        "mp4": "audio/mp4"
    }
    mime_type = mime_type_map.get(file_extension, "audio/wav")
    
    # Step 1: Transcribe audio
    try:
        batch_service = BatchService()
        logger.info(
            f"Starting transcription: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}"
        )
        raw_transcript = await batch_service.transcribe_audio(audio_bytes, mime_type)
        logger.info(
            f"Transcription complete: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}, "
            f"length={len(raw_transcript)} chars"
        )
    except Exception as e:
        logger.error(
            f"Transcription failed: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}, error={e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail="Transcription service failed. Please try again."
        )
    
    # Step 2: Clean transcript
    try:
        cleaner_service = BatchCleanerService()
        logger.info(
            f"Starting cleaning: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}"
        )
        cleaned_transcript = await cleaner_service.clean_transcript(raw_transcript)
        logger.info(
            f"Cleaning complete: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}, "
            f"length={len(cleaned_transcript)} chars"
        )
    except Exception as e:
        logger.error(
            f"Cleaning failed: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}, error={e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail="Transcript cleaning service failed. Please try again."
        )
    
    # Step 3: Async Fork - Execute Lane 1 (publishing) and Lane 2 (intelligence) concurrently
    # Requirements: 2.4 - interaction_type="transcript" for envelope, "batch_upload" for intelligence
    envelope = EnvelopeV1(
        tenant_id=UUID(context.tenant_id),
        user_id=context.user_id,
        interaction_type="transcript",
        content=ContentModel(text=cleaned_transcript, format="diarized"),
        timestamp=datetime.now(timezone.utc),
        source="upload",
        extras={},
        interaction_id=UUID(context.interaction_id),
        trace_id=context.trace_id,
        account_id=context.account_id
    )
    
    async def _lane1_publish() -> Optional[dict]:
        """Lane 1: Publish envelope to Kinesis/EventBridge."""
        try:
            event_publisher = AWSEventPublisher()
            return await event_publisher.publish_envelope(envelope)
        except Exception as e:
            logger.error(
                f"Lane 1 (publishing) error: processing_id={processing_id}, "
                f"interaction_id={context.interaction_id}, error={e}"
            )
            raise
    
    async def _lane2_intelligence() -> Optional[object]:
        """Lane 2: Extract and persist intelligence."""
        try:
            intelligence_service = IntelligenceService()
            return await intelligence_service.process_transcript(
                cleaned_transcript=cleaned_transcript,
                interaction_id=context.interaction_id,
                tenant_id=context.tenant_id,
                trace_id=context.trace_id,
                interaction_type="batch_upload"
            )
        except Exception as e:
            logger.error(
                f"Lane 2 (intelligence) error: processing_id={processing_id}, "
                f"interaction_id={context.interaction_id}, error={e}"
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
                f"{lane_name} failed (non-critical): processing_id={processing_id}, "
                f"interaction_id={context.interaction_id}, "
                f"error={type(result).__name__}: {str(result)}",
                exc_info=result
            )
        else:
            lane_name = "Lane 1 (publishing)" if i == 0 else "Lane 2 (intelligence)"
            if i == 0 and result:
                logger.info(
                    f"Envelope published: processing_id={processing_id}, "
                    f"interaction_id={context.interaction_id}, "
                    f"kinesis={'success' if result.get('kinesis_sequence') else 'failed'}, "
                    f"eventbridge={'success' if result.get('eventbridge_id') else 'failed'}"
                )
            else:
                logger.info(
                    f"{lane_name} completed: processing_id={processing_id}, "
                    f"interaction_id={context.interaction_id}"
                )
    
    logger.info(
        f"Batch processing complete: processing_id={processing_id}, "
        f"interaction_id={context.interaction_id}"
    )
    
    # Requirement 2.3 - return response with interaction_id
    return BatchProcessResponse(
        raw_transcript=raw_transcript,
        cleaned_transcript=cleaned_transcript,
        interaction_id=context.interaction_id
    )
