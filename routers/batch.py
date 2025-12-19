"""
Batch processing router for audio file transcription and cleaning.
"""
import logging
import uuid
from fastapi import APIRouter, UploadFile, HTTPException
from services.batch_service import BatchService
from services.batch_cleaner_service import BatchCleanerService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/batch", tags=["batch"])

# File validation constants
ALLOWED_EXTENSIONS = {"wav", "mp3", "flac", "m4a"}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB in bytes


@router.post("/process")
async def process_batch_audio(file: UploadFile):
    """
    Process an uploaded audio file through transcription and cleaning pipeline.
    
    Args:
        file: Audio file upload (WAV, MP3, FLAC, or M4A format, max 100MB)
        
    Returns:
        JSON response with raw_transcript and cleaned_transcript fields
        
    Raises:
        HTTPException: 400 for validation errors, 500 for processing errors
    """
    processing_id = str(uuid.uuid4())
    logger.info(f"Batch processing started: processing_id={processing_id}, filename={file.filename}")
    
    # Validate file extension
    if not file.filename:
        logger.warning(f"No filename provided: processing_id={processing_id}")
        raise HTTPException(status_code=400, detail="No filename provided")
    
    file_extension = file.filename.split(".")[-1].lower()
    if file_extension not in ALLOWED_EXTENSIONS:
        logger.warning(
            f"Invalid file format: processing_id={processing_id}, "
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
        logger.error(f"Failed to read file: processing_id={processing_id}, error={e}")
        raise HTTPException(status_code=400, detail="Failed to read uploaded file")
    
    # Validate file size
    file_size = len(audio_bytes)
    if file_size > MAX_FILE_SIZE:
        logger.warning(
            f"File too large: processing_id={processing_id}, "
            f"size={file_size}, max={MAX_FILE_SIZE}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE / (1024 * 1024):.0f}MB"
        )
    
    logger.info(
        f"File validated: processing_id={processing_id}, "
        f"size={file_size} bytes, extension={file_extension}"
    )
    
    # Determine MIME type
    mime_type_map = {
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "flac": "audio/flac",
        "m4a": "audio/mp4"
    }
    mime_type = mime_type_map.get(file_extension, "audio/wav")
    
    # Step 1: Transcribe audio
    try:
        batch_service = BatchService()
        logger.info(f"Starting transcription: processing_id={processing_id}")
        raw_transcript = await batch_service.transcribe_audio(audio_bytes, mime_type)
        logger.info(
            f"Transcription complete: processing_id={processing_id}, "
            f"length={len(raw_transcript)} chars"
        )
    except Exception as e:
        logger.error(
            f"Transcription failed: processing_id={processing_id}, error={e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail="Transcription service failed. Please try again."
        )
    
    # Step 2: Clean transcript
    try:
        cleaner_service = BatchCleanerService()
        logger.info(f"Starting cleaning: processing_id={processing_id}")
        cleaned_transcript = await cleaner_service.clean_transcript(raw_transcript)
        logger.info(
            f"Cleaning complete: processing_id={processing_id}, "
            f"length={len(cleaned_transcript)} chars"
        )
    except Exception as e:
        logger.error(
            f"Cleaning failed: processing_id={processing_id}, error={e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail="Transcript cleaning service failed. Please try again."
        )
    
    logger.info(f"Batch processing complete: processing_id={processing_id}")
    
    return {
        "raw_transcript": raw_transcript,
        "cleaned_transcript": cleaned_transcript
    }
