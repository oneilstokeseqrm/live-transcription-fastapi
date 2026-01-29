# Database Changes: Upload Jobs Table

## Overview

This document describes the database changes introduced for the Lane B presigned upload workflow in `live-transcription-fastapi`.

## Target Database

| Property | Value |
|----------|-------|
| **Neon Project ID** | `super-glitter-11265514` |
| **Neon Project Name** | `eq-dev` |
| **Database** | `neondb` |
| **Host** | `ep-silent-waterfall-adtinpn1-pooler.c-2.us-east-1.aws.neon.tech` |
| **Railway Service** | `live-transcription-fastapi` |

## Objects Created

### 1. Extension: pgcrypto

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

Required for `gen_random_uuid()` function used as the default for `upload_jobs.id`.

### 2. Enum Types

**Note:** SQLAlchemy generates enum type names from Python class names (lowercased, no underscores).

```sql
-- Status: queued -> processing -> succeeded | failed
CREATE TYPE jobstatus AS ENUM ('queued', 'processing', 'succeeded', 'failed');

-- Type: Extensible for future job types
CREATE TYPE jobtype AS ENUM ('audio_transcription', 'text_processing');
```

### 3. Table: upload_jobs

```sql
CREATE TABLE upload_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Tenant isolation
    tenant_id UUID NOT NULL,
    user_id TEXT NOT NULL,

    -- Job type and status
    job_type jobtype NOT NULL DEFAULT 'audio_transcription',
    status jobstatus NOT NULL DEFAULT 'queued',

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

    -- Timestamps (all TIMESTAMPTZ for timezone-aware storage)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);
```

### 4. Indexes

```sql
CREATE INDEX ix_upload_jobs_tenant_id ON upload_jobs(tenant_id);
CREATE INDEX ix_upload_jobs_status ON upload_jobs(status);
CREATE INDEX ix_upload_jobs_tenant_status ON upload_jobs(tenant_id, status);
CREATE UNIQUE INDEX ix_upload_jobs_tenant_file_key ON upload_jobs(tenant_id, file_key);
```

## Migration File

Location: `/migrations/001_create_upload_jobs.sql`

## Cleanup: Mistaken Migration to Wrong Database

### Background

During initial development, the migration was accidentally applied to the wrong Neon project:

| Property | Wrong DB | Correct DB |
|----------|----------|------------|
| **Project ID** | `misty-grass-26480209` | `super-glitter-11265514` |
| **Project Name** | `eq-dev-thematic` | `eq-dev` |
| **Host** | `ep-shy-truth-adk9d3h7-pooler...` | `ep-silent-waterfall-adtinpn1-pooler...` |

### Cleanup Performed

On `misty-grass-26480209` (eq-dev-thematic):

```sql
DROP TABLE IF EXISTS upload_jobs CASCADE;
DROP TYPE IF EXISTS job_status;
DROP TYPE IF EXISTS job_type;
DROP TYPE IF EXISTS jobstatus;
DROP TYPE IF EXISTS jobtype;
```

**pgcrypto was NOT removed** because it was already in use by existing tables in that database:
- `codebooks.id`
- `theme_runs.theme_run_id`
- `themes.id`
- `analysis_interactions.id`

### Proof: Before Cleanup (misty-grass-26480209)

```
Query: SELECT tablename FROM pg_tables WHERE tablename='upload_jobs'
Result: [{"tablename": "upload_jobs"}]  -- EXISTS (wrong!)

Query: SELECT typname FROM pg_type WHERE typname IN ('job_status','job_type')
Result: [{"typname": "job_status"}, {"typname": "job_type"}]  -- EXISTS (wrong!)

Query: SELECT table_name, column_name FROM information_schema.columns
       WHERE column_default ILIKE '%gen_random_uuid%'
Result: codebooks.id, theme_runs.theme_run_id, themes.id,
        analysis_interactions.id, upload_jobs.id
```

### Proof: After Cleanup (misty-grass-26480209)

```
Query: SELECT tablename FROM pg_tables WHERE tablename='upload_jobs'
Result: []  -- GONE ✓

Query: SELECT typname FROM pg_type WHERE typname IN ('job_status','job_type','jobstatus','jobtype')
Result: []  -- GONE ✓

Query: SELECT table_name FROM information_schema.columns
       WHERE column_default ILIKE '%gen_random_uuid%'
Result: codebooks, theme_runs, themes, analysis_interactions
        -- Only legitimate tables remain ✓
```

### Proof: Correct Database State (super-glitter-11265514)

```
Query: SELECT tablename FROM pg_tables WHERE tablename='upload_jobs'
Result: [{"tablename": "upload_jobs"}]  -- EXISTS ✓

Query: SELECT typname FROM pg_type WHERE typname IN ('jobstatus','jobtype')
Result: [{"typname": "jobstatus"}, {"typname": "jobtype"}]  -- EXISTS ✓

Query: SELECT column_name, udt_name FROM information_schema.columns
       WHERE table_name='upload_jobs' AND column_name LIKE '%_at'
Result: created_at=timestamptz, updated_at=timestamptz,
        started_at=timestamptz, completed_at=timestamptz  -- All timezone-aware ✓
```

## Runtime Notes

- **ORM**: SQLModel (Python), NOT Prisma
- **Enum naming**: SQLAlchemy uses lowercase class names (`JobStatus` → `jobstatus`)
- **Timestamps**: All `TIMESTAMPTZ` for proper timezone handling with asyncpg
- **schema.prisma**: Updated as documentation snapshot only (not for migrations)

## Related Files

| File | Purpose |
|------|---------|
| `/migrations/001_create_upload_jobs.sql` | SQL migration |
| `/models/job_models.py` | SQLModel definitions |
| `/routers/upload.py` | API endpoints |
| `/services/s3_service.py` | S3 presigned URL generation |
| `/schema.prisma` | Documentation snapshot |
