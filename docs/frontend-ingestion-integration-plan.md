# Frontend Ingestion Integration Plan

**Document Purpose:** Investigation and requirements for accepting ingested documents from the new Next.js frontend gateway.

**Status:** Investigation Complete | Implementation Pending

**Date:** 2025-01-28

---

## 1. Executive Summary

### What's Required

1. **JWT Verification Middleware** — Add FastAPI dependency to verify internal JWTs from the frontend gateway
2. **Environment Configuration** — Add three new environment variables for JWT validation
3. **Header Compatibility** — Maintain backward compatibility with current `X-Tenant-ID`/`X-User-ID` headers while supporting JWT-based identity
4. **Test Harness Update** — Extend test UI to support JWT-authenticated requests

### What's Optional

1. **WebSocket JWT Support** — Can remain environment-variable based for now (primarily internal/dev use)
2. **Account Ownership Validation** — Not currently enforced; can be added later when account storage exists
3. **Database Migration** — No schema changes required; tenant_id already stored correctly

### Key Compatibility Finding

The backend **already supports** `tenant_id` (UUID v4) and `user_id` (string) through header extraction. The JWT middleware can populate the same `RequestContext` structure, making the integration minimally invasive.

---

## 2. Endpoint Inventory Table

| Route | Method | Purpose | Auth Today | Auth Needed (Gateway) |
|-------|--------|---------|------------|----------------------|
| `/batch/process` | POST | Upload audio, transcribe, clean, publish | `X-Tenant-ID` + `X-User-ID` headers (strict) | Internal JWT |
| `/text/clean` | POST | Clean raw text, publish | `X-Tenant-ID` + `X-User-ID` headers (strict) | Internal JWT |
| `/listen` | WebSocket | Real-time audio streaming | Environment fallback (`MOCK_TENANT_ID`) | Optional: Internal JWT via query param or initial message |
| `/` | GET | Serve test UI (HTML) | None | None (public) |

### Endpoint Details

#### POST /batch/process

- **Location:** `routers/batch.py:39`
- **Request Format:** `multipart/form-data` with file upload
- **Current Headers Required:**
  - `X-Tenant-ID` (UUID v4) — **required**
  - `X-User-ID` (string) — **required**
  - `X-Trace-Id` (UUID) — optional, auto-generated
  - `X-Account-ID` (string) — optional
- **Processing Flow:** Deepgram transcription → OpenAI cleaning → Async fork (EventBridge/Kinesis + Postgres)
- **Response:** `{ raw_transcript, cleaned_transcript, interaction_id }`

#### POST /text/clean

- **Location:** `routers/text.py:27`
- **Request Format:** `application/json`
- **Body Schema:**
  ```json
  {
    "text": "string (required, non-empty)",
    "metadata": {},
    "source": "api"
  }
  ```
- **Current Headers Required:** Same as `/batch/process`
- **Processing Flow:** OpenAI cleaning → Async fork
- **Response:** `{ raw_text, cleaned_text, interaction_id }`

#### WebSocket /listen

- **Location:** `main.py:123`
- **Protocol:** WebSocket with binary (audio) and JSON (control) messages
- **Current Identity:** Falls back to `MOCK_TENANT_ID` env var, hardcoded `websocket_user`
- **Note:** WebSocket is primarily for internal/demo use; JWT support is optional

---

## 3. Current-State Findings

### 3.1 Tenant/User Model Assumptions

**Tenant Isolation:** ✅ Already implemented

The service correctly handles multi-tenancy:
- `tenant_id` is extracted from `X-Tenant-ID` header
- Stored in `RequestContext` dataclass (`models/request_context.py:12`)
- Propagated to `EnvelopeV1` events (published to EventBridge/Kinesis)
- Persisted to Postgres via `IntelligenceService`
- UUID v4 format validated by `_validate_tenant_id()` in `utils/context_utils.py:138`

**User Identity:** ✅ Already implemented

- `user_id` extracted from `X-User-ID` header
- Supports Auth0 format (`auth0|xxx`) and any non-empty string
- Included in all published events

**Account Isolation:** ⚠️ Partial

- `account_id` is extracted from `X-Account-ID` header (optional)
- Passed through to event `extras` field
- **No ownership validation** — backend doesn't verify account belongs to tenant
- This is acceptable for now; validation requires account storage that may not exist

### 3.2 Context Extraction Implementation

**Location:** `utils/context_utils.py`

Two extraction modes exist:

| Function | Use Case | Behavior |
|----------|----------|----------|
| `get_validated_context()` | Production endpoints (`/batch/process`, `/text/clean`) | Strict validation, raises HTTP 400 on failure |
| `get_request_context()` | WebSocket, backward compatibility | Lenient fallbacks to env vars |

The `get_validated_context()` function is the right integration point for JWT-based identity.

### 3.3 Existing Ingestion Flow

```
                                    ┌─────────────────────────────────────┐
                                    │         Frontend Gateway            │
                                    │   (validates Auth0, mints JWT)      │
                                    └───────────────┬─────────────────────┘
                                                    │
                                                    ▼
                                    ┌─────────────────────────────────────┐
                                    │         Backend Endpoint            │
              TODAY: Headers ──────▶│   get_validated_context()           │
              FUTURE: JWT   ──────▶│   → RequestContext(tenant_id,       │
                                    │      user_id, account_id, ...)      │
                                    └───────────────┬─────────────────────┘
                                                    │
                        ┌───────────────────────────┴───────────────────────────┐
                        ▼                                                       ▼
          ┌─────────────────────────┐                         ┌─────────────────────────┐
          │     Lane 1: Publish     │                         │   Lane 2: Intelligence  │
          │  EventBridge + Kinesis  │                         │   LLM Extract + Persist │
          └─────────────────────────┘                         └─────────────────────────┘
```

---

## 4. Proposed JWT Middleware Plan

### 4.1 Module Placement

Create new file: `middleware/jwt_auth.py`

This module will contain:
- `verify_internal_jwt()` — Core JWT verification function
- `get_jwt_context()` — FastAPI dependency that extracts and validates JWT
- `InternalJWTClaims` — Pydantic model for JWT claims

### 4.2 Dependency Design

```python
# middleware/jwt_auth.py (conceptual structure)

from fastapi import Request, HTTPException, Depends
from pydantic import BaseModel
import jwt  # PyJWT library

class InternalJWTClaims(BaseModel):
    tenant_id: str  # UUID v4
    user_id: str    # Auth0 sub
    iss: str        # Issuer
    aud: str        # Audience
    iat: int        # Issued at
    exp: int        # Expiration

def get_jwt_context(request: Request) -> RequestContext:
    """
    FastAPI dependency that:
    1. Extracts Authorization: Bearer <token> header
    2. Verifies JWT signature using INTERNAL_JWT_SECRET
    3. Validates iss, aud, exp claims
    4. Returns RequestContext with tenant_id and user_id

    Raises HTTPException 401 on validation failure.
    """
    # Implementation here
```

### 4.3 Integration Strategy: Dual-Mode Support

To support both header-based (legacy/testing) and JWT-based (gateway) authentication:

**Option A: Header Priority (Recommended)**
- If `Authorization: Bearer` header present → use JWT
- Else if `X-Tenant-ID` + `X-User-ID` headers present → use headers
- Else → 401 Unauthorized

This allows:
- Frontend gateway to use JWT
- Test scripts/curl to use headers
- Gradual migration without breaking changes

**Implementation in context_utils.py:**

```python
def get_auth_context(request: Request) -> RequestContext:
    """
    Unified authentication context extraction.

    Priority:
    1. JWT from Authorization header (new frontend gateway)
    2. X-Tenant-ID + X-User-ID headers (legacy/testing)

    Raises HTTPException 401 if neither is valid.
    """
    # Check for JWT first
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return _extract_from_jwt(auth_header[7:], request)

    # Fall back to header-based auth
    return get_validated_context(request)
```

### 4.4 Environment Variables Required

| Variable | Description | Example |
|----------|-------------|---------|
| `INTERNAL_JWT_SECRET` | HMAC-SHA256 signing secret (min 32 chars) | `K7gN...` (base64) |
| `INTERNAL_JWT_ISSUER` | Expected issuer claim | `eq-frontend` |
| `INTERNAL_JWT_AUDIENCE` | Expected audience claim | `eq-backend` |

### 4.5 Endpoints to Protect

| Endpoint | Protection |
|----------|------------|
| `POST /batch/process` | **Required** — Add JWT support via `get_auth_context()` |
| `POST /text/clean` | **Required** — Add JWT support via `get_auth_context()` |
| `WebSocket /listen` | **Optional** — Can remain environment-variable based |
| `GET /` | **None** — Static HTML, no auth needed |

---

## 5. Proposed Ingestion Request Contracts

### 5.1 Text Ingestion (POST /text/clean)

**Headers:**
```
Authorization: Bearer <internal_jwt>
Content-Type: application/json
```

**Body:**
```json
{
  "text": "Raw text content to clean...",
  "metadata": {
    "source_app": "eq-dashboard",
    "custom_field": "any value"
  },
  "source": "api"
}
```

**Required Fields:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `text` | string | Yes | Non-empty, non-whitespace text |
| `metadata` | object | No | Custom key-value pairs (passed to event `extras`) |
| `source` | string | No | Content source identifier (default: `"api"`) |

**Response:**
```json
{
  "raw_text": "Original text...",
  "cleaned_text": "Cleaned text...",
  "interaction_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### 5.2 Batch Call Recording Ingestion (POST /batch/process)

**Headers:**
```
Authorization: Bearer <internal_jwt>
Content-Type: multipart/form-data
```

**Body:**
- Form field `file`: Audio file (WAV, MP3, FLAC, M4A, WebM, MP4)
- Max size: 100MB

**Response:**
```json
{
  "raw_transcript": "Raw Deepgram output...",
  "cleaned_transcript": "Cleaned, diarized transcript...",
  "interaction_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### 5.3 Live Streaming Transcript Ingestion (WebSocket /listen)

**Connection:**
```
wss://backend.example.com/listen?token=<internal_jwt>
```
(Or initial handshake message with JWT)

**Messages (Client → Server):**
- Binary: Raw audio bytes (webm format)
- JSON: `{"type": "stop_recording"}`

**Messages (Server → Client):**
- Text: Interim transcript fragments
- JSON: Final session output
  ```json
  {
    "type": "session_complete",
    "summary": "Meeting summary...",
    "action_items": ["Action 1", "Action 2"],
    "cleaned_transcript": "Full diarized transcript...",
    "raw_transcript": "Raw transcript..."
  }
  ```

**Note:** WebSocket JWT support is optional for initial integration. Current implementation uses environment variable fallbacks.

### 5.4 Resource Ownership Validation

When request body includes `account_id` (or similar resource identifiers):

**Current Behavior:** Account ID is passed through without validation.

**Recommended Future Behavior:**
```
1. Extract account_id from request body
2. Query: SELECT 1 FROM accounts WHERE id = $account_id AND tenant_id = $jwt_tenant_id
3. If no match: Return 403 Forbidden
4. If match: Proceed with operation
```

This requires an accounts table or API that doesn't currently exist. For initial integration, pass-through is acceptable.

---

## 6. Refactor Plan

### 6.1 Auth / Middleware

| Task | Files | Description |
|------|-------|-------------|
| Create JWT verification module | `middleware/jwt_auth.py` (new) | PyJWT-based verification with claims extraction |
| Create unified auth dependency | `utils/context_utils.py` | Add `get_auth_context()` that supports both JWT and headers |
| Add PyJWT dependency | `requirements.txt` | Add `PyJWT>=2.8.0` |

**Minimal Safe Steps:**
1. Create `middleware/jwt_auth.py` with `verify_internal_jwt()` function
2. Add `get_auth_context()` to `context_utils.py` that tries JWT first, then headers
3. Unit test JWT verification in isolation
4. Integration test with mock JWT

### 6.2 Request Model Changes

| Task | Files | Description |
|------|-------|-------------|
| No changes needed | — | Existing models (`TextCleanRequest`, `BatchProcessResponse`) are compatible |

The current request/response models already support the gateway pattern:
- Identity comes from JWT (not body)
- Business payload stays in body
- `interaction_id` returned for tracking

### 6.3 Tenant Scoping in Persistence Layer

| Task | Files | Description |
|------|-------|-------------|
| Verify tenant scoping | `services/intelligence_service.py` | Confirm all DB writes include tenant_id |
| Verify event scoping | `services/aws_event_publisher.py` | Confirm EnvelopeV1 includes tenant_id |

**Finding:** Already implemented correctly. All database writes and event publishes include `tenant_id` from `RequestContext`.

### 6.4 Routing / Endpoint Changes

| Task | Files | Description |
|------|-------|-------------|
| Update batch endpoint | `routers/batch.py:58` | Replace `get_validated_context()` with `get_auth_context()` |
| Update text endpoint | `routers/text.py:48` | Replace `get_validated_context()` with `get_auth_context()` |

**Change Required:**
```python
# Before
context = get_validated_context(request)

# After
context = get_auth_context(request)
```

This is a single-line change per endpoint.

### 6.5 Test Harness Updates

| Task | Files | Description |
|------|-------|-------------|
| Add JWT generation helper | `tests/conftest.py` | Fixture to generate test JWTs |
| Update integration tests | `tests/test_integration_endpoints.py` | Add JWT-based test cases alongside header-based |
| Update test UI (optional) | `templates/index.html` | Add field for test JWT token |

**Test Strategy:**
- Keep existing header-based tests (backward compatibility)
- Add parallel JWT-based tests
- Add mixed-mode tests (JWT takes precedence over headers)

### 6.6 Logging/Observability Changes

| Task | Files | Description |
|------|-------|-------------|
| Log auth method used | `middleware/jwt_auth.py` | Log whether JWT or headers were used |
| Mask JWT in logs | `middleware/jwt_auth.py` | Never log full JWT, only first 8 chars |

---

## 7. Testing Plan

### 7.1 Using Existing Test UI

The test UI at `/` (`templates/index.html`) can be extended for gateway testing:

**Current Capabilities:**
- Live transcription mode (WebSocket)
- Batch recording mode (file upload to `/batch/process`)

**Adaptation for Gateway Testing:**

1. **Add JWT Input Field:**
   - Add text input for pasting test JWT
   - Inject into `Authorization` header on fetch

2. **Test Script for JWT Generation:**
   Create `scripts/generate_test_jwt.py`:
   ```python
   import jwt
   import os
   from datetime import datetime, timezone, timedelta

   def generate_test_jwt(tenant_id: str, user_id: str) -> str:
       return jwt.encode(
           {
               "tenant_id": tenant_id,
               "user_id": user_id,
               "iss": os.getenv("INTERNAL_JWT_ISSUER", "eq-frontend"),
               "aud": os.getenv("INTERNAL_JWT_AUDIENCE", "eq-backend"),
               "iat": datetime.now(timezone.utc),
               "exp": datetime.now(timezone.utc) + timedelta(minutes=5)
           },
           os.getenv("INTERNAL_JWT_SECRET"),
           algorithm="HS256"
       )
   ```

3. **Modified Batch Recording Flow (index.html):**
   ```javascript
   // In stopBatchRecording() function
   const jwtToken = document.getElementById('jwt-token')?.value;
   const headers = {};
   if (jwtToken) {
       headers['Authorization'] = `Bearer ${jwtToken}`;
   } else {
       // Fallback to header-based for local testing
       headers['X-Tenant-ID'] = 'test-tenant-uuid';
       headers['X-User-ID'] = 'test-user';
   }

   const response = await fetch('/batch/process', {
       method: 'POST',
       headers: headers,
       body: formData
   });
   ```

### 7.2 Automated Test Cases

**Unit Tests (`tests/unit/test_jwt_auth.py`):**
- Valid JWT with all claims → Returns RequestContext
- Missing `Authorization` header → Falls back to header auth
- Invalid JWT signature → 401 Unauthorized
- Expired JWT → 401 Unauthorized
- Wrong issuer → 401 Unauthorized
- Wrong audience → 401 Unauthorized
- Missing `tenant_id` claim → 401 Unauthorized
- Missing `user_id` claim → 401 Unauthorized

**Integration Tests (`tests/test_integration_endpoints.py`):**
- `test_batch_process_with_valid_jwt()` — Full flow with JWT
- `test_text_clean_with_valid_jwt()` — Full flow with JWT
- `test_jwt_takes_precedence_over_headers()` — JWT used when both present
- `test_fallback_to_headers_when_no_jwt()` — Header auth still works

### 7.3 E2E Verification Script

Create `scripts/verify_gateway_integration.py`:

```python
"""
End-to-end verification of frontend gateway integration.

Usage:
    python scripts/verify_gateway_integration.py --backend-url https://backend.example.com
"""

# 1. Generate test JWT locally (using shared secret)
# 2. POST to /text/clean with JWT
# 3. Verify response contains interaction_id
# 4. POST to /batch/process with JWT (using test audio file)
# 5. Verify response contains cleaned_transcript
# 6. (Optional) Poll downstream SQS/database to verify tenant_id propagation
```

---

## 8. Open Questions / Risks

### 8.1 Open Questions

| # | Question | Impact | Suggested Resolution |
|---|----------|--------|----------------------|
| 1 | Should WebSocket support JWT authentication? | Low — WebSocket is primarily for demo/internal use | Defer; keep environment variable fallback |
| 2 | How should `account_id` ownership be validated? | Medium — Currently no validation | Defer until account storage exists; document as TODO |
| 3 | Should JWT secret be rotated? If so, what's the process? | Low — Not immediate concern | Document rotation procedure; consider dual-secret support later |
| 4 | What happens if JWT expires mid-request (long audio processing)? | Low — 5-min TTL is sufficient for most requests | Accept risk; 100MB audio processes in <2 min |

### 8.2 Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| JWT secret mismatch between frontend/backend | Medium | High (all requests fail) | Document env var setup; add startup validation log |
| Clock skew causes JWT expiration failures | Low | Medium | Allow 30-second leeway in `exp` validation |
| Backward compatibility break for existing clients | Low | High | Dual-mode auth (JWT + headers) ensures compatibility |
| Performance impact of JWT verification | Very Low | Low | PyJWT HMAC verification is <1ms |

### 8.3 Dependencies on Frontend

- Frontend must mint JWTs with correct claims (`tenant_id` as UUID v4, `user_id` as string)
- Frontend must use consistent `iss` and `aud` values matching backend config
- Frontend must handle 401 responses (token refresh, re-auth)

### 8.4 Not In Scope (For This Integration)

- Auth0 direct integration (handled by frontend)
- User role/permission enforcement (identity only)
- Rate limiting by tenant
- Audit logging of all requests

---

## Appendix A: File Reference

| File | Current Purpose | Changes Needed |
|------|-----------------|----------------|
| `main.py` | App entry, WebSocket | Minimal (import auth module) |
| `routers/batch.py` | Batch processing endpoint | Single-line change to auth call |
| `routers/text.py` | Text cleaning endpoint | Single-line change to auth call |
| `utils/context_utils.py` | Header extraction | Add `get_auth_context()` |
| `middleware/jwt_auth.py` | **NEW** | Create JWT verification |
| `models/request_context.py` | RequestContext dataclass | No changes |
| `templates/index.html` | Test UI | Optional: add JWT input |
| `requirements.txt` | Dependencies | Add `PyJWT>=2.8.0` |
| `.env.example` | Env var template | Add JWT env vars |

---

## Appendix B: Frontend JWT Contract Reference

Source: `/Users/peteroneil/eq-frontend/docs/backend-internal-jwt-contract.md`

**JWT Structure:**
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

**Validation Requirements:**
1. Verify signature using `INTERNAL_JWT_SECRET` (HMAC-SHA256)
2. Verify `iss` matches `INTERNAL_JWT_ISSUER`
3. Verify `aud` matches `INTERNAL_JWT_AUDIENCE`
4. Verify `exp` is in the future
5. Extract `tenant_id` (UUID v4) and `user_id` (string)

**Error Responses:**
- 401 Unauthorized: Invalid/expired/missing token
- 403 Forbidden: Valid token but resource doesn't belong to tenant

---

## Appendix C: Implementation Checklist

Before closing this integration:

- [ ] Create `middleware/jwt_auth.py` with verification logic
- [ ] Add `get_auth_context()` to `utils/context_utils.py`
- [ ] Update `routers/batch.py` to use `get_auth_context()`
- [ ] Update `routers/text.py` to use `get_auth_context()`
- [ ] Add `PyJWT>=2.8.0` to `requirements.txt`
- [ ] Add JWT env vars to `.env.example`
- [ ] Write unit tests for JWT verification
- [ ] Write integration tests for JWT-authenticated endpoints
- [ ] Update test UI to support JWT input (optional)
- [ ] Create E2E verification script
- [ ] Document deployment requirements (env vars)
- [ ] Test with actual frontend gateway in staging

---

*Document generated by Claude Code investigation task*
