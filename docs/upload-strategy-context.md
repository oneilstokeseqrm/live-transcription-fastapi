# Upload Strategy Investigation Report

**Purpose:** Determine how large ingestion payloads should flow from the Vercel-hosted frontend to this backend reliably.

**Date:** 2025-01-28

---

## Recommended Approach

### Recommendation: Option B — Direct Upload to Object Storage (S3/R2) with Presigned URLs

**Why this is the right choice:**

1. **Vercel's 4.5MB body limit is a hard blocker** for Option C (proxy through Vercel). A 45-minute audio file is 45-450MB depending on format—10-100x over the limit.

2. **Railway's 5-minute request timeout** creates risk for Option A (direct to backend) when processing large files synchronously. A 60-minute WAV file (600MB) takes significant time to upload + transcribe + clean.

3. **Deepgram natively supports URL-based transcription**, making Option B both cleaner and potentially faster (Deepgram fetches directly from S3, no intermediate hop through our backend).

4. **Keeps the gateway pattern intact** for all other EQ operations—only large file uploads are special-cased.

### Flow Summary

```
┌─────────────┐    1. Request presigned URL     ┌──────────────────┐
│   Browser   │ ─────────────────────────────▶  │  Next.js Gateway │
│             │                                  │  (mints JWT)     │
└─────────────┘                                  └────────┬─────────┘
       │                                                  │
       │ 2. Presigned URL returned                        │ POST /upload/init
       │◀─────────────────────────────────────────────────│
       │                                                  ▼
       │                                         ┌──────────────────┐
       │ 3. PUT file directly to S3              │  live-trans-     │
       │ ─────────────────────────────────────▶  │  fastapi backend │
       │                                         │  (returns URL)   │
       │                                         └──────────────────┘
       │                                                  │
       │ 4. Notify upload complete                        │
       │ ─────────────────────────────────────────────────│
       │    POST /upload/complete {file_key}              │
       │                                                  ▼
       │                                         ┌──────────────────┐
       │ 5. Processing result (async or polled)  │  Backend fetches │
       │◀─────────────────────────────────────── │  from S3 URL,    │
       │                                         │  sends to DG     │
       │                                         └──────────────────┘
```

---

## Known Limits

### Platform Limits (External Constraints)

| Platform | Limit | Impact | Source |
|----------|-------|--------|--------|
| **Vercel Functions** | 4.5MB request body | Cannot proxy audio files through Next.js | [Vercel Docs](https://vercel.com/docs/functions/limitations#request-body-size) |
| **Vercel Functions** | 300s max duration (Pro: 800s with Fluid Compute) | Even if body fit, long processing would timeout | [Vercel Docs](https://vercel.com/docs/functions/limitations#max-duration) |
| **Railway Reverse Proxy** | 5-minute (300s) request timeout | Large file uploads + sync processing may 502 | [Railway Help](https://station.railway.com/questions/any-workarounds-for-the-5-min-request-ti-b055adde) |
| **Deepgram** | 2GB max file size | Not a concern for typical calls | [Deepgram Docs](https://developers.deepgram.com/docs/getting-started-with-pre-recorded-audio) |
| **Deepgram** | 100 concurrent requests/project (15 on paid, 5 on PAYG) | May need queueing at scale | Deepgram Docs |

### This Repo's Limits

| Limit | Location | Value | Notes |
|-------|----------|-------|-------|
| `MAX_FILE_SIZE` | `routers/batch.py:36` | **100MB** | Enforced after full file read into memory |
| Allowed formats | `routers/batch.py:35` | `wav, mp3, flac, m4a, webm, mp4` | Hardcoded set |
| Uvicorn workers | `railway.json:7` | 2 workers | Could bottleneck concurrent uploads |
| No explicit timeout | `railway.json` | Uses uvicorn defaults | Uvicorn default is no timeout; Railway proxy enforces 5min |
| Text chunking | `utils/text_utils.py:4` | 500 words/chunk | For OpenAI cleaning calls |

### Typical Payload Sizes (45-60 Minute Interaction)

| Format | Size per Minute | 45-min File | 60-min File |
|--------|-----------------|-------------|-------------|
| WAV (16-bit, 44.1kHz, stereo) | ~10MB | ~450MB | ~600MB |
| WAV (16-bit, 16kHz, mono) | ~1.9MB | ~85MB | ~115MB |
| MP3 (128kbps) | ~1MB | ~45MB | ~60MB |
| MP3 (320kbps) | ~2.4MB | ~108MB | ~144MB |
| WebM (Opus) | ~0.1MB | ~4.5MB | ~6MB |
| FLAC | ~5MB | ~225MB | ~300MB |

**Transcript sizes:**
- Raw transcript: ~150 words/minute × 60 min = ~9,000 words ≈ **50-60KB**
- Cleaned transcript: Similar size (formatting changes, not compression)
- JSON envelope with transcript: ~60-80KB

**Conclusion:** Audio files are the bottleneck, not transcripts. Text ingestion (`/text/clean`) can safely go through the gateway; audio cannot.

---

## How `/batch/process` Handles Files Today

### Upload Handling (`routers/batch.py:86-107`)

```python
# Line 86-88: Full file read into memory
audio_bytes = await file.read()

# Line 96-107: Size validation AFTER reading
file_size = len(audio_bytes)
if file_size > MAX_FILE_SIZE:
    raise HTTPException(...)
```

**Finding:** The entire file is loaded into memory before validation. No streaming. For a 100MB file, this consumes 100MB+ RAM per concurrent request.

### Deepgram Call (`services/batch_service.py:36-52`)

```python
# Line 39-43: Bytes passed directly to SDK
source = {
    'buffer': audio_bytes,
    'mimetype': mimetype
}

# Line 51-52: Sync call (awaited)
response = await self.client.transcription.prerecorded(source, options)
```

**Finding:** Audio is sent as raw bytes to Deepgram. The SDK v2.12.0 being used supports this pattern. Deepgram **also supports URL-based ingestion** via `transcribe_url()` method, but this repo doesn't use it.

### Slowest Steps (Timeout Risk)

| Step | Typical Duration | Scales With |
|------|------------------|-------------|
| 1. File upload to backend | 10-60s | File size, network speed |
| 2. Deepgram transcription | 30-120s | Audio duration (roughly 1:1 ratio for Nova-2) |
| 3. OpenAI cleaning | 10-60s | Transcript length (chunked to 500 words) |
| 4. Lane 1+2 (EventBridge + Intelligence) | 5-30s | Transcript complexity |

**Total for 60-min file:** 2-5 minutes — dangerously close to Railway's 5-minute timeout.

### Response Timing

```python
# routers/batch.py:219-224
results = await asyncio.gather(
    _lane1_publish(),
    _lane2_intelligence(),
    return_exceptions=True  # Non-blocking
)
```

**Finding:** Response is returned **only after** transcription + cleaning complete. Lanes 1 & 2 run concurrently but must complete before response. This is synchronous from the client's perspective.

---

## How `/text/clean` Handles Large Transcripts

### Request Parsing

- Uses Pydantic `TextCleanRequest` model (`models/text_request.py`)
- No explicit size limit on `text` field
- FastAPI/Starlette default body limit: **1MB** (configurable)

### Processing Flow (`routers/text.py:67-86`)

```python
# Chunking happens inside BatchCleanerService
cleaner_service = BatchCleanerService()
cleaned_text = await cleaner_service.clean_transcript(body.text)
```

### Chunking Implementation (`services/batch_cleaner_service.py:41-54`)

```python
# Line 42-45: Split by lines, then chunk
lines = raw_transcript.strip().split('\n')
chunked_lines = split_long_lines(lines, max_words=500)

# Line 50-54: Sequential OpenAI calls per chunk
for i, chunk in enumerate(chunked_lines):
    cleaned_chunk = await self._clean_chunk(chunk)
    cleaned_chunks.append(cleaned_chunk)
```

**Finding:** Transcripts are chunked to 500 words before OpenAI calls. However, chunks are processed **sequentially**, not in parallel.

### What Fails First for Very Large Transcripts

| Failure Point | Threshold | Symptom |
|---------------|-----------|---------|
| FastAPI body parse | >1MB JSON | 413 Request Entity Too Large |
| OpenAI token limit | ~128K tokens (GPT-4o) | Per-chunk, unlikely to hit |
| Railway timeout | 5 minutes total | 502 Bad Gateway |
| Memory exhaustion | Depends on instance | Worker crash |

**Realistic limit:** A 60-minute transcript (~60KB) is well within safe bounds. The `/text/clean` endpoint can handle normal transcripts without issue.

---

## WebSocket `/listen` — Frontend Intent

### Current Implementation (`main.py:123-275`)

- Uses `MOCK_TENANT_ID` environment variable for tenant context
- Hardcodes `user_id="websocket_user"`
- No authentication mechanism

### Intended Use

Based on code analysis:
- **Primary purpose:** Demo/testing UI served at `/`
- **Not production-ready:** No auth, hardcoded user, env-var tenant
- **Real-time streaming:** Works for live recording from browser microphone

### Recommendation for Frontend Integration

If WebSocket needs to support the new frontend:

1. **Auth Strategy:** Accept JWT as query parameter on connection (`/listen?token=<jwt>`)
2. **Parse JWT on connection** in `websocket_endpoint()` before `accept()`
3. **Reject invalid tokens** with WebSocket close code 4001
4. **Extract tenant_id/user_id** from verified JWT claims

This is lower priority than batch ingestion. WebSocket can remain demo-only initially.

---

## Option Analysis

### Option A — Browser Uploads Directly to live-transcription-fastapi

**How it would work:**
- Next.js gateway mints internal JWT
- Browser sends `POST /batch/process` with `Authorization: Bearer <jwt>` directly to Railway backend
- Backend verifies JWT, processes file

**CORS Considerations:**
- Backend would need CORS headers for frontend domain
- Add `CORSMiddleware` to FastAPI app
- Credentials mode for Authorization header

**Backend Changes Required:**
1. Add JWT verification middleware (per integration plan)
2. Add CORS middleware
3. Consider increasing `MAX_FILE_SIZE` beyond 100MB
4. Potentially add chunked upload support

**Pros:**
- Simpler architecture (no S3)
- Direct control over processing

**Cons:**
- Railway 5-min timeout risk for large files
- Full file in memory per request
- Browser upload progress harder to track

**Verdict:** Viable for files <50MB and <3 minute processing. Risky for 60-minute calls.

### Option B — Browser Uploads Directly to S3/R2 with Presigned URLs

**How it would work:**

1. **Frontend calls `POST /api/gateway/upload/init`** (Next.js route)
   - Gateway mints JWT, forwards to backend
   - Backend generates presigned S3 PUT URL (5-minute expiry)
   - Returns: `{ upload_url, file_key, expires_at }`

2. **Browser uploads directly to S3**
   - `PUT <presigned_url>` with file body
   - No auth header needed (presigned)
   - Progress events available

3. **Frontend calls `POST /api/gateway/upload/complete`**
   - Body: `{ file_key, filename, mime_type }`
   - Backend fetches from S3 URL, sends to Deepgram via `transcribe_url()`
   - Option A: Synchronous response (wait for processing)
   - Option B: Return `interaction_id`, client polls for status

**Deepgram URL Ingestion (services/batch_service.py change):**

```python
# Current: buffer-based
source = {'buffer': audio_bytes, 'mimetype': mimetype}

# New: URL-based
source = {'url': s3_presigned_read_url}
response = await self.client.transcription.prerecorded(source, options)
```

Deepgram supports this natively — they fetch the file directly from S3.

**Minimal Orchestration Required:**

| Endpoint | Location | Purpose |
|----------|----------|---------|
| `POST /upload/init` | New in backend | Generate presigned PUT URL |
| `POST /upload/complete` | New in backend | Trigger processing from S3 key |
| `GET /upload/status/:id` | Optional | Poll for async processing status |

**Frontend Changes:**
- New gateway routes: `/api/gateway/upload/init`, `/api/gateway/upload/complete`
- Direct S3 upload from browser (XHR/fetch with progress)
- Handle presigned URL flow

**Backend Changes:**
1. Add boto3 S3 client (already in requirements.txt)
2. Add `POST /upload/init` endpoint
3. Add `POST /upload/complete` endpoint
4. Modify `BatchService.transcribe_audio()` to accept URL source
5. Add S3 bucket config env vars

**Pros:**
- No Vercel body limit issue
- No Railway timeout issue (S3 upload is separate from processing)
- Upload progress in browser
- Deepgram fetches directly (efficient)
- Files available for replay/audit

**Cons:**
- More moving parts (S3 bucket, IAM, presigned URLs)
- Two-phase upload flow in frontend

**Verdict:** Best option for production audio ingestion.

### Option C — Proxy Upload Through Vercel

**Vercel Limits (Confirmed):**
- **Request body: 4.5MB** — Hard limit, not configurable
- **Duration: 300s** (Hobby) / **800s** (Pro with Fluid Compute)

**45-60 Minute Audio File:**
- MP3 at 128kbps: 45-60MB
- WAV: 450-600MB

**Verdict:** **Non-viable.** A 45MB file is 10x over the body limit. Vercel is designed for JSON APIs, not file uploads.

---

## Keeping EQ Gateway Pattern for Everything Else

The presigned URL pattern **only applies to large file uploads**. All other operations continue through the standard gateway:

| Operation | Flow | Why |
|-----------|------|-----|
| Chat messages | UI → Gateway → Backend | Small JSON payloads |
| CRUD operations | UI → Gateway → Backend | Small JSON payloads |
| Text ingestion (`/text/clean`) | UI → Gateway → Backend | Transcripts are <100KB |
| Account/tenant management | UI → Gateway → Backend | Small JSON payloads |
| **Audio file upload** | UI → S3 (direct) → Backend | Files are 10-600MB |

The gateway still handles:
- Auth0 session validation
- Tenant resolution
- Internal JWT minting
- Forwarding the `/upload/init` and `/upload/complete` requests

Only the raw file bytes bypass Vercel.

---

## Minimal Lift Implementation Outline

### Frontend Changes (Next.js)

1. **New gateway routes:**
   - `app/api/gateway/upload/init/route.ts` — Forward to backend, return presigned URL
   - `app/api/gateway/upload/complete/route.ts` — Forward to backend, return processing result

2. **Upload component:**
   - Call `/api/gateway/upload/init` to get presigned URL
   - `fetch(presignedUrl, { method: 'PUT', body: file })` for direct S3 upload
   - Track upload progress via XHR `progress` event
   - Call `/api/gateway/upload/complete` when done

### Backend Changes (live-transcription-fastapi)

1. **New endpoints:**
   - `POST /upload/init` — Generate presigned PUT URL, return file_key
   - `POST /upload/complete` — Accept file_key, process from S3

2. **Modify BatchService:**
   ```python
   # NOTE: Actual signature now returns TranscriptionResult (dataclass with
   # transcript, duration_seconds, channels, words) — see batch_service.py
   async def transcribe_from_url(self, audio_url: str, mimetype: str = "audio/wav") -> TranscriptionResult:
       source = {'url': audio_url}
       response = await self.client.transcription.prerecorded(source, options)
       meta = self._log_deepgram_metadata(response, source_label="url")
       formatted = self._format_deepgram_response(response)
       return TranscriptionResult(transcript=formatted, **meta)
   ```

3. **S3 client utility:**
   ```python
   # services/s3_service.py
   def generate_presigned_upload_url(file_key: str, content_type: str) -> str:
       return s3_client.generate_presigned_url(
           'put_object',
           Params={'Bucket': BUCKET, 'Key': file_key, 'ContentType': content_type},
           ExpiresIn=300
       )
   ```

4. **Environment variables:**
   - `S3_BUCKET_NAME`
   - `S3_REGION`
   - `AWS_ACCESS_KEY_ID` (already exists)
   - `AWS_SECRET_ACCESS_KEY` (already exists)

### Files to Create/Modify

| File | Change |
|------|--------|
| `routers/upload.py` | **NEW** — `/upload/init`, `/upload/complete` endpoints |
| `services/s3_service.py` | **NEW** — Presigned URL generation |
| `services/batch_service.py` | Add `transcribe_from_url()` method |
| `main.py` | Include upload router |
| `.env.example` | Add S3 bucket config |

---

## Open Questions / Risks

| Question | Risk Level | Notes |
|----------|------------|-------|
| Which S3 bucket/region? | Low | Use same AWS account as EventBridge; create dedicated bucket |
| File retention policy? | Low | Delete after processing? Keep for audit? Configurable TTL? |
| Async vs sync processing? | Medium | Sync is simpler but risks timeout; async requires polling/webhooks |
| Max concurrent uploads per tenant? | Medium | May need rate limiting to prevent abuse |
| Error handling for S3 upload failures | Medium | Frontend needs retry logic; backend needs cleanup |
| CORS on S3 bucket | Low | Must allow PUT from frontend domain |

### Risks

| Risk | Mitigation |
|------|------------|
| S3 presigned URL leaked | Short expiry (5 min), one-time use key pattern |
| Processing timeout for very long files | Consider async pattern with webhook callback |
| Cost of S3 storage | Set lifecycle policy to delete after 24h |
| Deepgram URL fetch fails | Ensure presigned read URL has sufficient expiry |

---

## File References

| Finding | File | Line(s) |
|---------|------|---------|
| 100MB file limit | `routers/batch.py` | 36 |
| Full file read to memory | `routers/batch.py` | 86-88 |
| Deepgram buffer upload | `services/batch_service.py` | 39-43, 51-52 |
| Text chunking (500 words) | `utils/text_utils.py` | 4 |
| Sequential chunk processing | `services/batch_cleaner_service.py` | 50-54 |
| Railway config (2 workers) | `railway.json` | 7 |
| Deepgram SDK version | `requirements.txt` | 2 (v2.12.0) |
| boto3 already installed | `requirements.txt` | 11 |

---

## Sources

- [Vercel Functions Limitations](https://vercel.com/docs/functions/limitations)
- [Vercel Body Size Limit Workaround](https://vercel.com/kb/guide/how-to-bypass-vercel-body-size-limit-serverless-functions)
- [Railway 5-minute Timeout Discussion](https://station.railway.com/questions/any-workarounds-for-the-5-min-request-ti-b055adde)
- [Deepgram Pre-recorded Audio Docs](https://developers.deepgram.com/docs/getting-started-with-pre-recorded-audio)
- [Deepgram Python SDK](https://github.com/deepgram/deepgram-python-sdk)
- [Audio File Size Calculator](https://www.colincrawley.com/audio-file-size-calculator/)
