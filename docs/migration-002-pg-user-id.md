# Migration 002: `pg_user_id` Identity Bridge Column

## Status: APPLIED

- **Applied to:** Neon Postgres project `super-glitter-11265514` (eq-dev)
- **Date applied:** 2026-02-16
- **Commit:** `a54aa38` on `main`
- **Deployed to:** Railway (`live-transcription-fastapi-production`)
- **Smoke tested:** Yes (with and without `pg_user_id` in JWT)

---

## Why This Migration Exists

The `eq-frontend` application implemented an **Auth0-to-Postgres Identity Bridge**. Previously, users were identified solely by their Auth0 `user_id` (e.g., `auth0|abc123`). The bridge creates a corresponding Postgres UUID (`pg_user_id`) for each user, enabling downstream services to reference users by a stable database primary key rather than an opaque Auth0 subject string.

Internal JWTs minted by the `eq-frontend` gateway now carry an **optional** `pg_user_id` claim alongside the existing `user_id`. This service (`live-transcription-fastapi`) needed to accept, propagate, and persist that field.

## What Changed

### Database (Migration 002)

```sql
ALTER TABLE upload_jobs ADD COLUMN IF NOT EXISTS pg_user_id TEXT;
```

- **Table:** `upload_jobs` (owned by this service, NOT in eq-frontend's Prisma schema)
- **Column:** `pg_user_id TEXT` — nullable, no index
- **Why nullable:** Backward compatibility. Existing rows and JWTs without the claim remain valid.
- **Why no index:** Queries filter by `tenant_id + status`, never by `user_id` or `pg_user_id`.

### Application Code (commit `a54aa38`)

| Layer | File | Change |
|-------|------|--------|
| JWT middleware | `middleware/jwt_auth.py` | Extract optional `pg_user_id` from JWT payload into `JWTClaims` |
| Auth context | `models/request_context.py` | Added `pg_user_id: Optional[str]` to `RequestContext` |
| Context utils | `utils/context_utils.py` | Propagate `pg_user_id` from JWT claims → RequestContext |
| Envelope model | `models/envelope.py` | Added `pg_user_id` to `EnvelopeV1` (Kinesis/EventBridge events) |
| Batch event model | `models/batch_event.py` | Added `pg_user_id` to `BatchProcessingCompletedEvent` |
| Job model | `models/job_models.py` | Added `pg_user_id` column mapping to `UploadJob` SQLModel |
| Upload router | `routers/upload.py` | Dual-write `pg_user_id` on job creation; include in envelope |
| Batch router | `routers/batch.py` | Pass `pg_user_id` into `EnvelopeV1` |
| Text router | `routers/text.py` | Pass `pg_user_id` into `EnvelopeV1` |
| Event publisher | `services/aws_event_publisher.py` | Accept and include `pg_user_id` in batch completed events |
| Tests | `tests/test_jwt_auth.py` | 2 new tests: claim present → extracted; claim absent → None |

### What Did NOT Change

- **WebSocket endpoint** — no JWT auth, hardcoded user
- **Intelligence service tables** — don't store `user_id` at all
- **JWT signing/verification config** — same secret, algorithm, issuer, audience
- **`user_id` handling** — stays as primary identifier everywhere (no removal)
- **All existing tests** — backward compatible, 161 tests pass

## Schema Ownership Note

The `upload_jobs` table is **owned by this service**, not by `eq-frontend`. It is not present in `eq-frontend/schema.prisma`. The `eq-frontend` identity bridge migration (`add-pg-user-id-to-tables`) only covers its own tables:

- `chat_threads`
- `chat_jobs`
- `agent_jobs`
- `agent_artifacts`
- `agent_refinement_threads`

Therefore, this migration correctly lives in `live-transcription-fastapi/migrations/`.

## Data Flow

```
eq-frontend gateway
    │
    ├── Mints internal JWT with:
    │     tenant_id, user_id, pg_user_id (optional), interaction_id
    │
    ▼
live-transcription-fastapi
    │
    ├── JWT middleware extracts pg_user_id (None if absent)
    ├── RequestContext carries pg_user_id through request lifecycle
    │
    ├── Synchronous paths (POST /batch/process, POST /text/clean):
    │     └── pg_user_id included directly in EnvelopeV1 → Kinesis/EventBridge
    │
    └── Async upload path (POST /upload/init → background processing):
          ├── pg_user_id written to upload_jobs row (Migration 002 column)
          └── Background task reads job → pg_user_id included in EnvelopeV1
```

## Verification Performed

1. All 161 tests pass (including 2 new JWT claim tests)
2. Migration applied to Neon Postgres via MCP (`mcp__neon__run_sql`)
3. Auto-deployed to Railway on push to `main`
4. Smoke test: JWT **with** `pg_user_id` → 200 OK, field propagated
5. Smoke test: JWT **without** `pg_user_id` → 200 OK, backward compatible
