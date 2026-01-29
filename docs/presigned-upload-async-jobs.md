# Presigned Upload & Async Jobs

This document describes the large file upload workflow and async job processing architecture for the live transcription service.

## Overview

The service supports two ingestion paths:

| Path | Use Case | Flow |
|------|----------|------|
| **Lane A** (small payloads) | Text transcripts, JSON | UI → Vercel Gateway → Backend (sync) |
| **Lane B** (large payloads) | Audio files (10-600MB) | UI → S3 (direct) → Backend (async) |

Lane A uses the standard gateway pattern with internal JWT authentication.
Lane B uses presigned S3 URLs to bypass Vercel's 4.5MB body limit, then triggers async processing.

## Architecture

```
Browser                  Vercel Gateway         Backend              S3
──────                  ──────────────         ───────              ──
│                            │                    │                  │
│  POST /init ───────────────────────────────────▶│                  │
│◀───── {upload_url, job_id} ────────────────────│                  │
│                            │                    │                  │
│  PUT (presigned) ──────────────────────────────────────────────────▶│
│◀────────────────────────────────────────────────────────── 200 OK ─│
│                            │                    │                  │
│  POST /complete ───────────────────────────────▶│                  │
│◀───── {job_id, status:queued} ─────────────────│                  │
│                            │                    │ async processing │
│  GET /status ──────────────────────────────────▶│                  │
│◀───── {status:succeeded} ──────────────────────│                  │
```

## Authentication

All endpoints require authentication via internal JWT from the frontend gateway.

### JWT Contract

The gateway mints short-lived JWTs with these claims:

```json
{
  "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
  "user_id": "auth0|507f1f77bcf86cd799439011",
  "iss": "eq-frontend",
  "aud": "eq-backend",
  "iat": 1706380800,
  "exp": 1706381100
}
```

### Backend Validation

The backend validates:
1. Signature using `INTERNAL_JWT_SECRET` (HMAC-SHA256)
2. Issuer matches `INTERNAL_JWT_ISSUER`
3. Audience matches `INTERNAL_JWT_AUDIENCE`
4. Token not expired (30s clock skew allowed)
5. Required claims present (`tenant_id`, `user_id`)

### Legacy Header Auth (Dev Only)

For local development and testing, set `ALLOW_LEGACY_HEADER_AUTH=true` to enable header-based auth:

```bash
curl -X POST http://localhost:8000/text/clean \
  -H "X-Tenant-ID: 550e8400-e29b-41d4-a716-446655440000" \
  -H "X-User-ID: auth0|test-user" \
  -H "Content-Type: application/json" \
  -d '{"text": "Test content"}'
```

**WARNING**: Disable this in production by setting `ALLOW_LEGACY_HEADER_AUTH=false`.

## Endpoints

### POST /upload/init

Initialize a presigned upload.

**Request:**
```json
{
  "filename": "recording.wav",
  "mime_type": "audio/wav",
  "file_size": 45000000
}
```

**Response:**
```json
{
  "upload_url": "https://s3.amazonaws.com/bucket/tenant/xxx/uploads/yyy/file?signature=...",
  "file_key": "tenant/550e8400.../uploads/abc123/recording.wav",
  "job_id": "abc123-def456-...",
  "expires_at": "2024-01-27T12:05:00Z"
}
```

### PUT to upload_url (S3 Direct)

Upload the file directly to S3 using the presigned URL:

```javascript
await fetch(uploadUrl, {
  method: 'PUT',
  body: file,
  headers: { 'Content-Type': file.type }
})
```

### POST /upload/complete

Trigger processing after successful upload.

**Request:**
```json
{
  "file_key": "tenant/550e8400.../uploads/abc123/recording.wav",
  "file_name": "recording.wav",
  "mime_type": "audio/wav",
  "file_size": 45000000
}
```

**Response:**
```json
{
  "job_id": "abc123-def456-...",
  "interaction_id": "xyz789-...",
  "status": "queued"
}
```

### GET /upload/status/{job_id}

Poll job status until complete.

**Response:**
```json
{
  "job_id": "abc123-def456-...",
  "status": "succeeded",
  "interaction_id": "xyz789-...",
  "created_at": "2024-01-27T12:00:00Z",
  "started_at": "2024-01-27T12:00:05Z",
  "completed_at": "2024-01-27T12:02:30Z",
  "result_summary": "Transcribed 15000 chars, cleaned to 12000 chars"
}
```

## Job States

| Status | Description |
|--------|-------------|
| `queued` | Job created, waiting to start |
| `processing` | Transcription and cleaning in progress |
| `succeeded` | Complete, results available |
| `failed` | Error occurred, see error_message |

## Processing Pipeline

When a job runs, it executes the same pipeline as synchronous endpoints:

1. **Transcribe** - Deepgram transcribes from S3 URL
2. **Clean** - OpenAI formats and diarizes transcript
3. **Lane 1** - Publish EnvelopeV1 to EventBridge + Kinesis
4. **Lane 2** - Extract intelligence and persist to Postgres

Lanes 1 and 2 run concurrently. Failures in either lane don't fail the job.

## S3 Configuration

The upload bucket is configured with:
- **Public access blocked** - All access via presigned URLs
- **SSE-S3 encryption** - Data encrypted at rest
- **CORS** - Allows PUT from frontend domains
- **Lifecycle policy** - Objects deleted after 1 day

**Bucket:** `eq-live-transcription-uploads-dev`
**Region:** `us-east-1`

## Environment Variables

### Backend

```bash
# JWT Authentication
INTERNAL_JWT_SECRET=<min 32 characters>
INTERNAL_JWT_ISSUER=eq-frontend
INTERNAL_JWT_AUDIENCE=eq-backend
ALLOW_LEGACY_HEADER_AUTH=false  # true for dev only

# S3 Uploads
UPLOAD_BUCKET_NAME=eq-live-transcription-uploads-dev
UPLOAD_REGION=us-east-1

# AWS Credentials (shared with EventBridge/Kinesis)
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

### Frontend

```bash
INTERNAL_JWT_SECRET=<same as backend>
INTERNAL_JWT_ISSUER=eq-frontend
INTERNAL_JWT_AUDIENCE=eq-backend
BACKEND_SERVICE_TRANSCRIPTION_URL=https://your-backend.railway.app
```

## Database Migration

Run this migration to create the jobs table:

```bash
psql $DATABASE_URL -f migrations/001_create_upload_jobs.sql
```

## Testing

### Run Tests

```bash
# All tests
ALLOW_LEGACY_HEADER_AUTH=true pytest

# JWT auth tests only
pytest tests/test_jwt_auth.py -v
```

### Frontend Test Page

Access `/dev/upload-test` in the frontend to test the full upload flow:

1. Select an audio file
2. Click "Start Upload"
3. Watch the logs as it:
   - Gets presigned URL
   - Uploads to S3
   - Triggers processing
   - Polls for completion

### Local Testing with curl

```bash
# Generate a test JWT
python -c "
import jwt, time, os
os.environ['INTERNAL_JWT_SECRET'] = 'your-32-char-secret-here'
token = jwt.encode({
    'tenant_id': '550e8400-e29b-41d4-a716-446655440000',
    'user_id': 'auth0|test',
    'iss': 'eq-frontend',
    'aud': 'eq-backend',
    'iat': int(time.time()),
    'exp': int(time.time()) + 300
}, os.environ['INTERNAL_JWT_SECRET'], algorithm='HS256')
print(token)
"

# Use the token
curl -X POST http://localhost:8000/upload/init \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"filename": "test.wav", "mime_type": "audio/wav"}'
```

## Security Considerations

1. **Presigned URLs are short-lived** (5 minutes for PUT)
2. **File keys are tenant-scoped** - Cross-tenant access rejected
3. **JWT never logged** - Only first 8 chars logged for debugging
4. **S3 objects auto-deleted** - 1-day lifecycle policy
5. **No public bucket access** - All access via presigned URLs

## Troubleshooting

### "Authorization required" (401)

- Check JWT secret matches between frontend and backend
- Check JWT hasn't expired (5 min TTL)
- Check issuer/audience match configuration

### "File not found in storage" (404)

- Verify S3 upload completed successfully
- Check file_key matches exactly
- Presigned URL may have expired (5 min)

### Job stuck in "processing"

- Check backend logs for errors
- Verify Deepgram API key is valid
- Check OpenAI API key and quota

### CORS errors on S3 upload

- Verify S3 bucket CORS allows the frontend origin
- Check Content-Type header is set correctly
