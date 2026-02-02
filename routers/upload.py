"""Upload router for presigned S3 upload workflow.

This router implements the Lane B (large payload) ingestion path:
1. POST /upload/init - Get presigned URL for browser upload
2. POST /upload/complete - Trigger async processing after upload
3. GET /upload/status/{job_id} - Poll job status

Flow:
    Browser                  Gateway              Backend                 S3
    ──────                  ───────              ───────                 ──
    │ POST /init ─────────────────────────────────▶│                      │
    │◀───── {upload_url, job_id} ─────────────────│                      │
    │                                              │                      │
    │ PUT (presigned URL) ──────────────────────────────────────────────▶│
    │◀───────────────────────────────────────────────────────── 200 OK ──│
    │                                              │                      │
    │ POST /complete ─────────────────────────────▶│                      │
    │◀───── {job_id, status: queued} ─────────────│                      │
    │                                              │ async processing     │
    │ GET /status/{job_id} ───────────────────────▶│                      │
    │◀───── {status: processing/succeeded} ────────│                      │

Security:
- All endpoints require internal JWT authentication
- File keys are tenant-scoped (tenant/{tenant_id}/uploads/...)
- Cross-tenant access is rejected with 403
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from models.job_models import (
    JobStatus,
    JobType,
    UploadJob,
    JobStatusResponse,
    UploadInitResponse,
    UploadCompleteRequest,
    UploadCompleteResponse,
)
from services.s3_service import S3Service, S3ServiceError
from services.database import get_async_session
from utils.context_utils import get_auth_context

from sqlalchemy import select

logger = logging.getLogger(__name__)


# --- MIME Type Normalization ---

_MIME_ALIASES: dict[str, str] = {
    "audio/x-m4a": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/x-mpeg": "audio/mpeg",
    "video/webm": "audio/webm",
}


def _normalize_audio_mime_type(mime_type: str) -> str:
    """Normalize browser-reported MIME types to standard IANA types.

    Browsers (especially on macOS) report non-standard MIME types for
    some audio formats.  For example, .m4a files get ``audio/x-m4a``
    instead of the standard ``audio/mp4``.  This matters because:

    1. S3 stores the Content-Type from the presigned PUT
    2. When Deepgram fetches via presigned GET, it uses Content-Type
       for format detection
    3. Non-standard types can cause Deepgram to return empty transcripts
    """
    normalized = _MIME_ALIASES.get(mime_type.lower().strip(), mime_type)
    if normalized != mime_type:
        logger.info(f"Normalized MIME type: {mime_type} → {normalized}")
    return normalized

router = APIRouter(prefix="/upload", tags=["upload"])


# --- Request/Response Models ---

class UploadInitRequest(BaseModel):
    """Request model for POST /upload/init"""
    filename: str = Field(..., min_length=1, max_length=255)
    mime_type: str = Field(default="audio/wav")
    file_size: Optional[int] = Field(default=None, ge=1, le=500_000_000)  # Max 500MB


# --- Endpoints ---

@router.post("/init", response_model=UploadInitResponse)
async def upload_init(body: UploadInitRequest, request: Request):
    """Initialize an upload and get a presigned URL.

    This endpoint:
    1. Validates JWT and extracts tenant/user
    2. Creates a job record in 'queued' status
    3. Generates a presigned PUT URL for S3
    4. Returns the URL and job_id for tracking

    The browser should then:
    1. PUT the file directly to the presigned URL
    2. Call POST /upload/complete with the file_key

    Args:
        body: UploadInitRequest with filename, mime_type, file_size
        request: FastAPI Request for auth context

    Returns:
        UploadInitResponse with upload_url, file_key, job_id, expires_at

    Raises:
        HTTPException 401: Invalid/missing JWT
        HTTPException 500: S3 or database error
    """
    # Authenticate and get tenant context
    context = get_auth_context(request)

    job_id = str(uuid.uuid4())
    interaction_id = context.interaction_id

    # Normalize MIME type (browsers report non-standard types like audio/x-m4a)
    normalized_mime = _normalize_audio_mime_type(body.mime_type)

    logger.info(
        f"Upload init: job_id={job_id}, tenant_id={context.tenant_id[:8]}..., "
        f"filename={body.filename}, mime_type={normalized_mime}"
    )

    # Generate S3 key
    s3_service = S3Service()
    file_key = s3_service.generate_file_key(
        tenant_id=context.tenant_id,
        job_id=job_id,
        filename=body.filename
    )

    # Generate presigned PUT URL
    try:
        upload_url, expires_at = s3_service.generate_presigned_put_url(
            file_key=file_key,
            content_type=normalized_mime
        )
    except S3ServiceError as e:
        logger.error(f"S3 error generating presigned URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate upload URL")

    # Create job record
    job = UploadJob(
        id=uuid.UUID(job_id),
        tenant_id=uuid.UUID(context.tenant_id),
        user_id=context.user_id,
        job_type=JobType.audio_transcription,
        status=JobStatus.queued,
        file_key=file_key,
        file_name=body.filename,
        mime_type=normalized_mime,
        file_size=body.file_size,
        interaction_id=uuid.UUID(interaction_id),
        trace_id=context.trace_id,
        account_id=context.account_id,
    )

    try:
        async with get_async_session() as session:
            session.add(job)
            await session.commit()
            logger.info(f"Job record created: job_id={job_id}")
    except Exception as e:
        logger.error(f"Database error creating job: {e}")
        raise HTTPException(status_code=500, detail="Failed to create job record")

    return UploadInitResponse(
        upload_url=upload_url,
        file_key=file_key,
        job_id=job_id,
        expires_at=expires_at,
        signed_content_type=normalized_mime,
    )


@router.post("/complete", response_model=UploadCompleteResponse)
async def upload_complete(body: UploadCompleteRequest, request: Request):
    """Trigger processing after successful S3 upload.

    This endpoint:
    1. Validates JWT and extracts tenant/user
    2. Verifies file_key belongs to tenant (security check)
    3. Verifies object exists in S3
    4. Updates job status and triggers async processing
    5. Returns immediately with job_id for polling

    Args:
        body: UploadCompleteRequest with file_key
        request: FastAPI Request for auth context

    Returns:
        UploadCompleteResponse with job_id, interaction_id, status

    Raises:
        HTTPException 401: Invalid/missing JWT
        HTTPException 403: File key doesn't belong to tenant
        HTTPException 404: Job not found or file not in S3
    """
    # Authenticate and get tenant context
    context = get_auth_context(request)

    logger.info(
        f"Upload complete: file_key={body.file_key[:50]}..., "
        f"tenant_id={context.tenant_id[:8]}..."
    )

    # Security: Verify file_key belongs to this tenant
    s3_service = S3Service()
    if not s3_service.validate_key_belongs_to_tenant(body.file_key, context.tenant_id):
        logger.warning(
            f"Cross-tenant access attempt: file_key={body.file_key[:50]}..., "
            f"tenant_id={context.tenant_id[:8]}..."
        )
        raise HTTPException(status_code=403, detail="File does not belong to this tenant")

    # Verify object exists in S3
    if not s3_service.verify_object_exists(body.file_key):
        logger.warning(f"File not found in S3: {body.file_key[:50]}...")
        raise HTTPException(status_code=404, detail="File not found in storage")

    # Find the job by file_key and tenant_id
    async with get_async_session() as session:
        stmt = select(UploadJob).where(
            UploadJob.file_key == body.file_key,
            UploadJob.tenant_id == uuid.UUID(context.tenant_id)
        )
        result = await session.execute(stmt)
        job = result.scalar_one_or_none()

        if not job:
            logger.warning(f"Job not found for file_key: {body.file_key[:50]}...")
            raise HTTPException(status_code=404, detail="Job not found")

        # Idempotency check: if job is already processing or succeeded, return immediately
        if job.status in (JobStatus.processing, JobStatus.succeeded):
            logger.info(f"Idempotent return: job_id={str(job.id)}, status={job.status}")
            return UploadCompleteResponse(
                job_id=str(job.id),
                interaction_id=str(job.interaction_id),
                status=job.status
            )

        # Allow retry of failed jobs - reset to queued
        if job.status == JobStatus.failed:
            logger.info(f"Retrying failed job: job_id={str(job.id)}")
            job.status = JobStatus.queued
            job.error_message = None
            job.error_code = None

        # Update job with any additional metadata
        if body.file_name:
            job.file_name = body.file_name
        if body.mime_type:
            job.mime_type = _normalize_audio_mime_type(body.mime_type)
        if body.file_size:
            job.file_size = body.file_size
        job.updated_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(job)

        job_id = str(job.id)
        interaction_id = str(job.interaction_id)

    # Trigger async processing (fire-and-forget within same process)
    # In production, this could be a separate worker process or queue
    asyncio.create_task(_process_upload_job(job_id, context.tenant_id))

    logger.info(f"Processing triggered: job_id={job_id}")

    return UploadCompleteResponse(
        job_id=job_id,
        interaction_id=interaction_id,
        status=JobStatus.queued
    )


@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def upload_status(job_id: str, request: Request):
    """Get status of an upload job.

    This endpoint:
    1. Validates JWT and extracts tenant/user
    2. Retrieves job record
    3. Verifies job belongs to tenant (security check)
    4. Returns current status and result (if completed)

    Args:
        job_id: UUID string of the job
        request: FastAPI Request for auth context

    Returns:
        JobStatusResponse with status, timestamps, and result/error

    Raises:
        HTTPException 401: Invalid/missing JWT
        HTTPException 403: Job doesn't belong to tenant
        HTTPException 404: Job not found
    """
    # Authenticate and get tenant context
    context = get_auth_context(request)

    # Validate job_id format
    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    async with get_async_session() as session:
        stmt = select(UploadJob).where(UploadJob.id == job_uuid)
        result = await session.execute(stmt)
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Security: Verify job belongs to tenant
        if str(job.tenant_id) != context.tenant_id:
            logger.warning(
                f"Cross-tenant job access attempt: job_id={job_id}, "
                f"job_tenant={job.tenant_id}, request_tenant={context.tenant_id}"
            )
            raise HTTPException(status_code=403, detail="Job not found")

        return JobStatusResponse(
            job_id=str(job.id),
            status=job.status,
            interaction_id=str(job.interaction_id),
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            result_summary=job.result_summary,
            error_message=job.error_message,
            error_code=job.error_code,
        )


# --- Background Processing ---

async def _process_upload_job(job_id: str, tenant_id: str):
    """Process an upload job asynchronously.

    This function:
    1. Updates job to 'processing' status
    2. Generates presigned GET URL for the file
    3. Transcribes via Deepgram (URL-based)
    4. Cleans transcript
    5. Publishes envelope and persists intelligence
    6. Updates job to 'succeeded' or 'failed'

    This is fire-and-forget from the request handler's perspective.
    Errors are captured in the job record, not propagated.

    Args:
        job_id: UUID string of the job
        tenant_id: UUID string of the tenant (for logging)
    """
    from services.batch_service import BatchService
    from services.batch_cleaner_service import BatchCleanerService
    from services.aws_event_publisher import AWSEventPublisher
    from services.intelligence_service import IntelligenceService
    from models.envelope import EnvelopeV1, ContentModel

    logger.info(f"Starting async processing: job_id={job_id}")

    try:
        job_uuid = uuid.UUID(job_id)

        # Update job to processing
        async with get_async_session() as session:
            stmt = select(UploadJob).where(UploadJob.id == job_uuid)
            result = await session.execute(stmt)
            job = result.scalar_one_or_none()

            if not job:
                logger.error(f"Job not found for processing: {job_id}")
                return

            job.status = JobStatus.processing
            job.started_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            await session.commit()

            # Capture job data for processing
            file_key = job.file_key
            mime_type = _normalize_audio_mime_type(job.mime_type or "audio/wav")
            interaction_id = str(job.interaction_id)
            user_id = job.user_id
            trace_id = job.trace_id
            account_id = job.account_id

        # Generate presigned GET URL for Deepgram
        s3_service = S3Service()
        audio_url = s3_service.generate_presigned_get_url(file_key)

        # Transcribe from URL
        batch_service = BatchService()
        logger.info(f"Transcribing from URL: job_id={job_id}, mime_type={mime_type}")
        tx_result = await batch_service.transcribe_from_url(audio_url, mime_type)
        raw_transcript = tx_result.transcript

        # Fail explicitly if Deepgram returned nothing
        if not raw_transcript.strip():
            logger.warning(
                f"Empty transcript from Deepgram: job_id={job_id}, "
                f"mime_type={mime_type}, file_key={file_key[-60:]}, "
                f"duration={tx_result.duration_seconds}s, "
                f"channels={tx_result.channels}, words={tx_result.words}"
            )
            async with get_async_session() as session:
                stmt = select(UploadJob).where(UploadJob.id == job_uuid)
                result = await session.execute(stmt)
                job = result.scalar_one_or_none()
                if job:
                    job.status = JobStatus.failed
                    job.completed_at = datetime.now(timezone.utc)
                    job.updated_at = datetime.now(timezone.utc)
                    job.error_code = "EMPTY_TRANSCRIPT"
                    job.error_message = (
                        f"Audio decoded successfully (duration={tx_result.duration_seconds}s, "
                        f"channels={tx_result.channels}) but Deepgram detected 0 words. "
                        f"The file may contain silence, music, or unintelligible audio. "
                        f"mime_type={mime_type}, file_size={job.file_size or 'unknown'}, "
                        f"file_name={job.file_name or 'unknown'}"
                    )
                    await session.commit()
            return

        # Clean transcript
        cleaner_service = BatchCleanerService()
        logger.info(f"Cleaning transcript: job_id={job_id}")
        cleaned_transcript = await cleaner_service.clean_transcript(raw_transcript)

        # Build envelope
        envelope = EnvelopeV1(
            tenant_id=uuid.UUID(tenant_id),
            user_id=user_id,
            interaction_type="transcript",
            content=ContentModel(text=cleaned_transcript, format="diarized"),
            timestamp=datetime.now(timezone.utc),
            source="upload",
            extras={},
            interaction_id=uuid.UUID(interaction_id),
            trace_id=trace_id,
            account_id=account_id
        )

        # Execute Lane 1 (publish) and Lane 2 (intelligence) concurrently
        async def _lane1():
            try:
                publisher = AWSEventPublisher()
                return await publisher.publish_envelope(envelope)
            except Exception as e:
                logger.error(f"Lane 1 error: job_id={job_id}, error={e}")
                return None

        async def _lane2():
            try:
                intelligence = IntelligenceService()
                return await intelligence.process_transcript(
                    cleaned_transcript=cleaned_transcript,
                    interaction_id=interaction_id,
                    tenant_id=tenant_id,
                    trace_id=trace_id,
                    interaction_type="batch_upload"
                )
            except Exception as e:
                logger.error(f"Lane 2 error: job_id={job_id}, error={e}")
                return None

        await asyncio.gather(_lane1(), _lane2(), return_exceptions=True)

        # Update job to succeeded
        async with get_async_session() as session:
            stmt = select(UploadJob).where(UploadJob.id == job_uuid)
            result = await session.execute(stmt)
            job = result.scalar_one_or_none()

            if job:
                job.status = JobStatus.succeeded
                job.completed_at = datetime.now(timezone.utc)
                job.updated_at = datetime.now(timezone.utc)
                job.result_summary = f"Transcribed {len(raw_transcript)} chars, cleaned to {len(cleaned_transcript)} chars"
                await session.commit()

        logger.info(f"Job completed successfully: job_id={job_id}")

    except Exception as e:
        logger.error(f"Job processing failed: job_id={job_id}, error={e}", exc_info=True)

        # Update job to failed
        try:
            async with get_async_session() as session:
                stmt = select(UploadJob).where(UploadJob.id == uuid.UUID(job_id))
                result = await session.execute(stmt)
                job = result.scalar_one_or_none()

                if job:
                    job.status = JobStatus.failed
                    job.completed_at = datetime.now(timezone.utc)
                    job.updated_at = datetime.now(timezone.utc)
                    job.error_message = str(e)[:500]  # Truncate long errors
                    job.error_code = type(e).__name__
                    await session.commit()
        except Exception as update_error:
            logger.error(f"Failed to update job status: {update_error}")


# --- Startup Tasks ---

async def reap_stuck_jobs(max_age_minutes: int = 30):
    """Reset jobs stuck in 'processing' status beyond max_age.

    Called on app startup to recover from crashes or unexpected restarts.
    Jobs that have been processing for longer than max_age_minutes are
    marked as failed with a timeout error.

    Args:
        max_age_minutes: Maximum time a job can be in 'processing' status
                         before being considered stuck (default: 30 minutes)
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)

    try:
        async with get_async_session() as session:
            stmt = select(UploadJob).where(
                UploadJob.status == JobStatus.processing,
                UploadJob.started_at < cutoff
            )
            result = await session.execute(stmt)
            stuck_jobs = result.scalars().all()

            for job in stuck_jobs:
                logger.warning(f"Reaping stuck job: job_id={job.id}, started_at={job.started_at}")
                job.status = JobStatus.failed
                job.error_message = "Job timed out (server restart or crash)"
                job.error_code = "PROCESSING_TIMEOUT"
                job.completed_at = datetime.now(timezone.utc)
                job.updated_at = datetime.now(timezone.utc)

            await session.commit()

            if stuck_jobs:
                logger.info(f"Reaped {len(stuck_jobs)} stuck jobs")
            else:
                logger.info("No stuck jobs found during startup reaper run")

    except Exception as e:
        logger.error(f"Failed to reap stuck jobs: {e}", exc_info=True)
