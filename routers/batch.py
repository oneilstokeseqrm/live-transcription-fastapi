"""
Batch processing router for audio file transcription and cleaning.

This router provides the POST /batch/process endpoint for processing audio
files through transcription and cleaning, publishing EnvelopeV1 events.
"""
import logging
import uuid
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, UploadFile, HTTPException, Request
from pydantic import BaseModel, Field

from models.envelope import EnvelopeV1, ContentModel
from services.batch_service import BatchService
from services.batch_cleaner_service import BatchCleanerService
from services.aws_event_publisher import AWSEventPublisher
from utils.context_utils import get_validated_context

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
    # Extract and validate request context (raises HTTPException 400 on failure)
    # Requirements: 1.1, 1.2
    context = get_validated_context(request)
    
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
    
    # Step 3: Build and publish EnvelopeV1 event (resilient - don't fail request if this fails)
    # Requirements: 2.4 - interaction_type="transcript"
    try:
        envelope = EnvelopeV1(
            tenant_id=UUID(context.tenant_id),
            user_id=context.user_id,
            interaction_type="transcript",
            content=ContentModel(text=cleaned_transcript, format="diarized"),
            timestamp=datetime.now(timezone.utc),
            source="upload",
            extras={},
            interaction_id=UUID(context.interaction_id),
            trace_id=context.trace_id
        )
        
        event_publisher = AWSEventPublisher()
        logger.info(
            f"Publishing envelope: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}"
        )
        publish_results = await event_publisher.publish_envelope(envelope)
        logger.info(
            f"Envelope published: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}, "
            f"kinesis={'success' if publish_results['kinesis_sequence'] else 'failed'}, "
            f"eventbridge={'success' if publish_results['eventbridge_id'] else 'failed'}"
        )
    except Exception as e:
        # Log error but don't fail the request - event publishing is non-critical
        logger.error(
            f"Envelope publishing failed (non-critical): processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}, error={type(e).__name__}: {str(e)}",
            exc_info=True
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
