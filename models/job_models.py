"""Job model for async upload processing.

This module defines the SQLModel table for tracking async upload jobs.
Jobs are persisted to Postgres for durability across service restarts.

Job Lifecycle:
    queued -> processing -> succeeded | failed

Design Decision: Postgres over Redis
    - Jobs must survive service restarts (durability)
    - Tenant isolation is enforced at the database level
    - Job history can be queried for debugging and auditing
    - Redis remains for transient session data (WebSocket transcripts)
"""
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, Text, Index, DateTime
from typing import Optional
from datetime import datetime, timezone
from uuid import UUID, uuid4
import enum


class JobStatus(str, enum.Enum):
    """Job processing status.

    Lifecycle: queued -> processing -> succeeded | failed
    """
    queued = "queued"
    processing = "processing"
    succeeded = "succeeded"
    failed = "failed"


class JobType(str, enum.Enum):
    """Type of async job.

    Currently supports audio transcription from S3 uploads.
    Extensible for future job types.
    """
    audio_transcription = "audio_transcription"
    text_processing = "text_processing"


class UploadJob(SQLModel, table=True):
    """Async upload job tracking.

    Stores job state for presigned upload -> processing workflow.
    All queries MUST be scoped by tenant_id for multi-tenant isolation.

    Table: upload_jobs (must be created via migration)

    Indexes:
        - Primary: id
        - Composite: (tenant_id, status) for queue polling
        - Unique: (tenant_id, file_key) prevents duplicate processing
    """
    __tablename__ = "upload_jobs"

    # Primary key
    id: UUID = Field(default_factory=uuid4, primary_key=True)

    # Tenant isolation (required for all queries)
    tenant_id: UUID = Field(index=True, sa_column_kwargs={"name": "tenant_id"})
    user_id: str = Field(sa_column_kwargs={"name": "user_id"})
    pg_user_id: Optional[str] = Field(default=None, sa_column=Column(Text, name="pg_user_id"))

    # Job type and status
    job_type: JobType = Field(default=JobType.audio_transcription, sa_column_kwargs={"name": "job_type"})
    status: JobStatus = Field(default=JobStatus.queued, index=True)

    # File reference (S3 key)
    file_key: str = Field(sa_column=Column(Text, name="file_key"))
    file_name: Optional[str] = Field(default=None, sa_column=Column(Text, name="file_name"))
    mime_type: Optional[str] = Field(default=None, sa_column=Column(Text, name="mime_type"))
    file_size: Optional[int] = Field(default=None, sa_column_kwargs={"name": "file_size"})

    # Processing correlation
    interaction_id: UUID = Field(sa_column_kwargs={"name": "interaction_id"})
    trace_id: Optional[str] = Field(default=None, sa_column=Column(Text, name="trace_id"))
    account_id: Optional[str] = Field(default=None, sa_column=Column(Text, name="account_id"))

    # Result/error storage
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text, name="error_message"))
    error_code: Optional[str] = Field(default=None, sa_column=Column(Text, name="error_code"))

    # Result summary (not full transcript - that goes to events/intelligence)
    result_summary: Optional[str] = Field(default=None, sa_column=Column(Text, name="result_summary"))

    # Metadata for extensibility
    metadata_json: Optional[str] = Field(default=None, sa_column=Column(Text, name="metadata_json"))

    # Timestamps (explicit timezone-aware columns for asyncpg compatibility)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), name="created_at", nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), name="updated_at", nullable=False)
    )
    started_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), name="started_at", nullable=True)
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), name="completed_at", nullable=True)
    )

    # Composite indexes defined via __table_args__
    __table_args__ = (
        Index("ix_upload_jobs_tenant_status", "tenant_id", "status"),
        Index("ix_upload_jobs_tenant_file_key", "tenant_id", "file_key", unique=True),
    )


# --- Pydantic models for API responses ---

from pydantic import BaseModel


class JobStatusResponse(BaseModel):
    """Response model for job status polling.

    Returned by GET /upload/status/{job_id}
    """
    job_id: str
    status: JobStatus
    interaction_id: str

    # Progress info
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Result (only when succeeded)
    result_summary: Optional[str] = None

    # Error (only when failed)
    error_message: Optional[str] = None
    error_code: Optional[str] = None


class UploadInitResponse(BaseModel):
    """Response model for POST /upload/init

    Returns presigned URL for direct S3 upload.
    The signed_content_type MUST be used as the Content-Type header
    when PUTting to the presigned URL (it is embedded in the signature).
    """
    upload_url: str
    file_key: str
    job_id: str
    expires_at: datetime
    signed_content_type: str


class UploadCompleteRequest(BaseModel):
    """Request model for POST /upload/complete

    Sent after successful S3 upload to trigger processing.
    """
    file_key: str
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    metadata: Optional[dict] = None


class UploadCompleteResponse(BaseModel):
    """Response model for POST /upload/complete

    Returns job info for status polling.
    """
    job_id: str
    interaction_id: str
    status: JobStatus
