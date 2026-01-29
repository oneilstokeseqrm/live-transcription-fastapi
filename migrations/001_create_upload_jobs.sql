-- Migration: Create upload_jobs table for async upload processing
-- Run this migration against your Neon Postgres database before using the upload endpoints

-- Enable pgcrypto for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Create enum types if they don't exist
DO $$ BEGIN
    CREATE TYPE job_status AS ENUM ('queued', 'processing', 'succeeded', 'failed');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE job_type AS ENUM ('audio_transcription', 'text_processing');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Create upload_jobs table
CREATE TABLE IF NOT EXISTS upload_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Tenant isolation
    tenant_id UUID NOT NULL,
    user_id TEXT NOT NULL,

    -- Job type and status
    job_type job_type NOT NULL DEFAULT 'audio_transcription',
    status job_status NOT NULL DEFAULT 'queued',

    -- File reference (S3 key)
    file_key TEXT NOT NULL,
    file_name TEXT,
    mime_type TEXT,
    file_size BIGINT,

    -- Processing correlation
    interaction_id UUID NOT NULL,
    trace_id TEXT,
    account_id TEXT,

    -- Result/error storage
    error_message TEXT,
    error_code TEXT,
    result_summary TEXT,

    -- Metadata for extensibility
    metadata_json TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

-- Create indexes for efficient queries
CREATE INDEX IF NOT EXISTS ix_upload_jobs_tenant_id ON upload_jobs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_upload_jobs_status ON upload_jobs(status);
CREATE INDEX IF NOT EXISTS ix_upload_jobs_tenant_status ON upload_jobs(tenant_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS ix_upload_jobs_tenant_file_key ON upload_jobs(tenant_id, file_key);

-- Add comment for documentation
COMMENT ON TABLE upload_jobs IS 'Tracks async upload jobs for presigned S3 upload workflow';
COMMENT ON COLUMN upload_jobs.file_key IS 'S3 object key, tenant-scoped format: tenant/{tenant_id}/uploads/{job_id}/{filename}';
COMMENT ON COLUMN upload_jobs.interaction_id IS 'Correlation ID linking to EnvelopeV1 events and intelligence records';
