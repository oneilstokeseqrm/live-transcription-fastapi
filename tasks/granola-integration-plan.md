# Granola Integration — Implementation Plan

**Date:** 2026-05-22
**Status:** APPROVED — Ready for build
**Estimated effort:** ~6-7 days of focused engineering (~4-5 backend, ~2 frontend)
**Phase:** Phase 2 (Granola initiative)

---

## How to use this doc

This plan is **executable**. A future build session should:

1. Read [the brainstorm handoff doc](../docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md) for background (why these decisions; what was rejected).
2. Read this plan top-to-bottom for the build order, phase dependencies, and pre-merge gates.
3. Follow the phases sequentially; each phase has explicit exit criteria.

The plan **does not restate** the brainstorm doc — it references it where load-bearing.

---

## Scope

### In scope
- Backend Granola ingestion adapter in `live-transcription-fastapi`
- Per-user credential storage with AWS KMS envelope encryption
- DBOS-scheduled 5-min polling for each connected user
- Path 2 account resolution (defer + re-poll for unknown business attendees)
- New tables: `vault.user_credentials` + `public.external_integration_runs`
- AWS infrastructure: 1 KMS CMK + 1 IAM user with least-privilege policy
- Frontend Granola Connect settings page in `eq-frontend` (`/dashboard/settings/integrations/granola`)
- EQ-native Pending Approvals component (serves all sources; Granola is new emitter)
- Transactional email on credential breakage (Resend or equivalent)
- Production E2E verification against test tenant with Peter as design partner #0

### Out of scope (Phase 2.x follow-ups, ticketed but not built here)
- Real-time webhook ingestion (polling sufficient at this scale)
- Reverse-sync on Granola edits/deletes/folder-moves (LOCKED-27 snapshot-on-ingest)
- Alerting wire-up (structured logging in DB only for MVP; Phase 2.1 wires Slack/Resend)
- Org-admin bulk-onboarding tooling (Phase 3 when >10 users)
- Modifying downstream Pydantic envelope contracts (LOCKED-38 prohibition)

### Out of scope (other Phase 2 candidates, unchanged from prior backlog)
The 11-candidate Phase 2 backlog (Neo4j MERGE-everywhere refactor, contact identity state machine, outbound pending path, queue UI evolution, audit log table, Outlook NULL-IMID dedup, ensure_constraints hardening, shared MERGE-key contract doc, cross-queue link fill-in, re-open lifecycle, orphan node hygiene) remains parked.

---

## LOCKED Decisions (building on Phase 1's 22)

| # | Decision | Source |
|---|---|---|
| LOCKED-23 | Granola adapter lives in `services/granola_ingestion/` inside live-transcription-fastapi | Q1 |
| LOCKED-24 | Credentials use AWS KMS envelope encryption + `vault.user_credentials` Postgres table with per-tenant `EncryptionContext` | Q2 |
| LOCKED-25 | Granola transcripts ingest with `interaction_type="meeting"` (FK landmine mitigation per `routers/text.py:80-95`) | Q8 / brainstorm |
| LOCKED-26 | Path 2 architecture for account resolution: known account → ingest with anchor; no known accounts → defer + re-poll. **Shipped PR-X2 `607121d` 2026-05-24** (3 substantive Codex rounds; R3 CLEAN delta after R1+R2 folds). Implementation in `services/granola_ingestion/path2.py` + `services/granola_ingestion/adapter.py`. Path 2 covers Scenario A (≥1 known anchor) / Scenario C (defer + capture LOCKED-44 snapshot) / Scenario D (no business attendees → skip). 84 unit tests pin the branching | Q1 |
| LOCKED-27 | Snapshot-on-ingest semantics for Granola lifecycle (no reverse-sync) | Q5 |
| LOCKED-28 | Polling cadence = 5 minutes; scheduling via **external Railway cron + DBOS queue with explicit SetWorkflowID** (NOT @DBOS.scheduled decorator — deprecated per `docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md:768`). **Shipped Phase 2e PR #28 `4e81bb6` 2026-05-24** (11 Codex rounds; R1-R5 real bugs folded, R8-R11 oscillation frozen + stopped). `routers/granola_cron.py` POST `/internal/granola/cron-tick` (X-Internal-Cron-Secret auth) → `scheduler.list_active_credentials()` → per-credential `GRANOLA_POLL_QUEUE.enqueue_async(granola_poll_one_credential, ...)` with `SetWorkflowID(f"granola_poll_{credential_id}_{cycle_window}")`. The actual 5-min cron PINGER deferred to Phase 2f (dormant until /connect). | Q3 + Codex consult |
| LOCKED-29 | External integration dedup via `public.external_integration_runs`; UNIQUE on `(tenant_id, user_id, provider, external_id)` | Q4 |
| LOCKED-30 | Connect Granola page at `/dashboard/settings/integrations/granola`; per-user, two-step wizard | Q6 |
| LOCKED-31 | Save & test runs synchronous one-shot poll (~2s end-to-end confirmation) | Q6 |
| LOCKED-32 | Fail-fast posture — 15-min silent retry on credential errors, then banner + transactional email | Q7 |
| LOCKED-33 | Error model — structured `error_code` enum + `error_detail` JSONB on `external_integration_runs` from day 1 | Q7 |
| LOCKED-34 | Disconnect via soft-delete (`archived_at`); preserves audit trail | Q6 |
| LOCKED-35 | `source="generic"`, `interaction_type="meeting"`, `content.format="plain"` | Q8 |
| LOCKED-36 | `extras` carries six `granola_*` keys (note_id, web_url, folder_name, summary_text, calendar_event_id, attendees_raw) | Q8 |
| LOCKED-37 | Pre-merge mandatory gate — `scripts/verify_consumer_contracts.py` against proposed envelope; drift blocks ship | Q8 + lessons.md |
| LOCKED-38 | NEVER modify downstream Pydantic envelope contracts; fit new sources into existing accepted enum values | Q8 / user-stated constraint |
| LOCKED-39 | DBOS scheduler primitive = external Railway cron + DBOS queue with explicit `SetWorkflowID` derived from `(credential_id, cycle_window_minute)`. Workflows are pure orchestration; all I/O lives in DBOS steps per the repo's existing DBOS discipline. **Shipped Phase 2e `4e81bb6`.** Two hardening additions surfaced in Codex review: (a) `run_cycle_step` holds a per-credential Postgres advisory lock (`pg_try_advisory_lock`) to serialize overlapping cycles — the `workflow_id` dedup only covers WITHIN a 5-min window, not across windows when a cycle overruns; (b) the asyncpg pool MUST use a DIRECT (non-pooler) Neon connection (`services/asyncpg_pool.py` derives it from DATABASE_URL by stripping `-pooler`) because the advisory lock is session-scoped and PgBouncer transaction pooling silently defeats it. | Plan review A1 + Codex consult |
| LOCKED-40 | KMS EncryptionContext = `{tenant_id, user_id, provider, credential_id}` — per-row cryptographic binding, not just tenant-level partitioning. KMS refuses Decrypt if any of the four fields don't match what was bound at Encrypt time | Plan review + Codex consult |
| LOCKED-41 | Granola adapter calls **`services/text_clean_service.py`** (extracted from `routers/text.py` core) — NOT HTTP. `tenant_id` flows as explicit function argument; tenant isolation preserved by entity-sourced identity pattern. **Extraction shipped PR-X1 `fa97477` 2026-05-24** (5 Codex rounds, R5 CLEAN cumulative). Public surface: `process(*, tenant_id: UUID, user_id: str, account_id: str, envelope: EnvelopeV1, lane2_extras: Optional[Lane2Extras] = None)` raises `TenantIsolationError` on identity-mismatch + `Lane1PublishError` on publish failure; companion helpers `try_reserve_lane2_slot()` / `release_lane2_slot()` for backpressure. Phase 2d adapter calls `process()` directly | Plan review A2 |
| LOCKED-42 | Postgres role split is **simplified for MVP**: schema separation (`vault` schema) with audited accessor module + allowlist; single Postgres role/engine. Second-engine + `eq_vault_service` role moves to Phase 2.1 hardening | Codex consult #3 |
| LOCKED-43 | AES-GCM rotate path mints a **fresh DEK + fresh nonce on every write** (insert AND update-in-place). Nonce reuse is the classic AES-GCM footgun and is structurally prevented | Codex consult #6 |
| LOCKED-44 | `external_integration_runs.granola_note_snapshot` (JSONB) captures minimal note state at defer time, so Scenario C remains recoverable even if the Granola note is moved/deleted before approval | Codex consult #8 |

**Total LOCKED decisions across the initiative: 44** (Phase 1 closed 22; this adds 22).

---

## Build phases

### Phase 0 — Pre-flight verification (~0.5 day)

**Goal:** Confirm the chosen envelope shape (`source="generic"`, all six extras) is accepted by downstream BEFORE any new code is written.

**Steps:**
1. Run `scripts/verify_consumer_contracts.py` with proposed envelope shape:
   ```
   python scripts/verify_consumer_contracts.py \
     --source generic \
     --interaction-type meeting \
     --extras-keys "granola_note_id,granola_web_url,granola_folder_name,granola_summary_text,granola_calendar_event_id,granola_attendees_raw"
   ```
2. Confirm both `eq-structured-graph-core` AND `action-item-graph` consumers accept the envelope.
3. **If drift detected** on `source="generic"`:
   - Fall back to `source="api"` (battle-tested in production)
   - Re-run verify_consumer_contracts.py
   - Update LOCKED-35 in this plan with the final value
4. **If drift detected on extras:**
   - Reduce to minimal three (note_id, web_url, summary_text)
   - Document the drift inline in the code for future reconciliation
5. Probe Neon production schema via Neon MCP to confirm no `vault` schema exists yet and the new tables can be safely added:
   ```sql
   SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'vault';
   SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'
     AND table_name IN ('external_integration_runs', 'user_credentials');
   ```
6. **Verify Granola `created_after` filter support** (per Codex consult — empirical only confirmed `folder_id` filter):
   ```bash
   # Test with the design partner's real Granola key
   curl -H "Authorization: Bearer $GRANOLA_KEY" \
     "https://public-api.granola.ai/v1/notes?folder_id=fol_xxx&created_after=2026-05-01T00:00:00Z&limit=5"
   ```
   - Expected: filtered to notes created after the timestamp
   - If `created_after` param is ignored or returns 400: fall back to client-side filter against `created_at`; document the degradation (every poll = full folder scan). Update Phase 2c API client accordingly.
   - **EMPIRICAL OUTCOME 2026-05-22 (Phase 0 verification):** ✅ confirmed against `public-api.granola.ai/v1`. Far-future `created_after` returns 0 notes; far-past returns baseline. Server-side filter works. Bogus `folder_id` returns `VALIDATION_ERROR: Invalid folder ID format`. `/folders` returns `{folders, hasMore, cursor}`. Note IDs: `not_` prefix; folder IDs: `fol_` prefix. Note: brainstorm doc used `api.granola.ai` + `since` — both factually wrong; corrected throughout this plan.
7. **Read repo's DBOS architecture doc** before designing Phase 2e scheduler: `docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md` §768 (deprecation of `@DBOS.scheduled`) + §504 (`SetWorkflowID` dedup pattern).
8. Confirm no other agents are active in the test tenant per shared-infrastructure-collision protocol:
   ```bash
   ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
   ```
9. Create feature branch `phase-2/granola-integration` in `live-transcription-fastapi`.

**Exit criteria:**
- verify_consumer_contracts.py returns 0 drift on locked envelope shape
- No other agents active in test tenant
- Feature branch created and pushed
- LOCKED-35 finalized (either `generic` or `api`)

---

### Phase 1 — AWS infrastructure (~0.5 day)

**Goal:** KMS CMK + IAM user provisioned and Railway env vars set BEFORE backend code references them.

**Steps:**

1. **Create KMS CMK** via aws CLI or AWS MCP:
   ```
   aws kms create-key \
     --description "EQ user-secrets encryption key (vault.user_credentials DEK wrapping)" \
     --policy file://kms-key-policy.json \
     --tags TagKey=Project,TagValue=eq-vault TagKey=Environment,TagValue=production
   aws kms create-alias --alias-name alias/eq-user-secrets --target-key-id <new-key-id>
   ```
2. **Create IAM user** `eq-vault-service` with least-privilege policy (Encrypt, Decrypt, GenerateDataKey on the new CMK only; EncryptionContext required for Decrypt). Per **LOCKED-40**, EncryptionContext = `{tenant_id, user_id, provider, credential_id}` — per-row cryptographic binding:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": ["kms:Encrypt", "kms:GenerateDataKey"],
         "Resource": "arn:aws:kms:us-east-1:211125681610:key/<key-id>"
       },
       {
         "Effect": "Allow",
         "Action": ["kms:Decrypt"],
         "Resource": "arn:aws:kms:us-east-1:211125681610:key/<key-id>",
         "Condition": {
           "StringEquals": {
             "kms:EncryptionContextKeys": ["tenant_id", "user_id", "provider", "credential_id"]
           }
         }
       }
     ]
   }
   ```
3. **Generate access keys** for `eq-vault-service` and set in Railway env vars:
   - `EQ_VAULT_AWS_ACCESS_KEY_ID`
   - `EQ_VAULT_AWS_SECRET_ACCESS_KEY`
   - `EQ_VAULT_KMS_KEY_ALIAS=alias/eq-user-secrets`
   - `EQ_VAULT_AWS_REGION=us-east-1`
4. **Document IAM policy JSON** inline in `services/vault/README.md` (for future audit + key rotation).
5. **Verify KMS access** from Railway by running a one-shot smoke test:
   ```python
   import boto3
   kms = boto3.client('kms', region_name='us-east-1')
   resp = kms.generate_data_key(
     KeyId='alias/eq-user-secrets',
     KeySpec='AES_256',
     EncryptionContext={'tenant_id': 'smoke-test', 'provider': 'granola'},
   )
   assert 'Plaintext' in resp
   assert 'CiphertextBlob' in resp
   ```

**Exit criteria:**
- KMS CMK `alias/eq-user-secrets` exists and is reachable from Railway runtime
- IAM user `eq-vault-service` exists with policy attached
- Railway env vars set; smoke test passes from Railway shell
- IAM policy JSON committed to `services/vault/README.md`

---

### Phase 2 — Backend (~4-5 days)

#### Phase 2a — Database migrations in eq-frontend (~0.5 day)

**Repo:** `eq-frontend` (Prisma schema is single source of truth per [reference_prisma_schema_ownership](../../../.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/reference_prisma_schema_ownership.md))

**Steps:**

1. **Add `vault` schema declaration** to `prisma/schema.prisma`:
   ```prisma
   generator client {
     provider = "prisma-client-js"
     previewFeatures = ["multiSchema"]
   }

   datasource db {
     provider = "postgresql"
     url      = env("DATABASE_URL")
     directUrl = env("DIRECT_DATABASE_URL")
     schemas  = ["public", "vault"]
   }
   ```

2. **Add `vault.user_credentials` model** with explicit FK relations (Prisma can't infer FKs from bare scalars):
   ```prisma
   model user_credentials {
     id                uuid     @id @default(uuid())
     tenant_id         uuid
     user_id           uuid
     provider          String   // "granola"
     encrypted_api_key Bytes
     encrypted_dek     Bytes
     nonce             Bytes    // for AES-GCM; ROTATED on every encrypted_api_key write per LOCKED-43
     config            Json     // {"folder_id": "fol_...", "folder_name": "EQ"}
     status            String   // "active" | "revoked" | "error" | "archived"
     last_polled_at    DateTime?
     last_error        Json?    // {"error_code": "...", "error_detail": {...}, "occurred_at": "..."}
     consecutive_failures Int @default(0)
     created_at        DateTime @default(now())
     updated_at        DateTime @updatedAt
     archived_at       DateTime?

     tenant tenants @relation(fields: [tenant_id], references: [id])
     user   users   @relation(fields: [user_id], references: [id])

     @@unique([tenant_id, user_id, provider])
     @@index([status, last_polled_at])
     @@schema("vault")
   }
   ```

3. **Add `public.external_integration_runs` model** with explicit FK relations and the `granola_note_snapshot` JSONB (per LOCKED-44 — so Scenario C remains recoverable if the Granola note disappears before approval):
   ```prisma
   model external_integration_runs {
     id                     uuid     @id @default(uuid())
     tenant_id              uuid
     user_id                uuid
     account_id             uuid?
     provider               String   // "granola"
     external_id            String   // "not_..."
     eq_interaction_id      uuid?
     granola_updated_at     DateTime?
     ingested_at            DateTime?
     status                 String   // "success" | "deferred_pending_account" | "skipped_no_business_attendees" | "failed" | "failed_permanent"
     error_code             String?
     error_detail           Json?
     retry_count            Int      @default(0)
     granola_note_snapshot  Json?    // captured at defer time per LOCKED-44; {title, summary_text, attendees, web_url, captured_at}
     queue_id               uuid?    // FK to pending_account_mappings, set when status='deferred_pending_account'
     created_at             DateTime @default(now())
     updated_at             DateTime @updatedAt

     tenant   tenants  @relation(fields: [tenant_id], references: [id])
     user     users    @relation(fields: [user_id], references: [id])
     account  accounts? @relation(fields: [account_id], references: [id])

     @@unique([tenant_id, user_id, provider, external_id])
     @@index([provider, status, created_at])
     @@index([account_id])
     @@index([queue_id])
     @@schema("public")
   }
   ```

4. **Postgres role grant strategy (LOCKED-42 — simplified for MVP):** Use schema separation + audited accessor module (`services/vault/user_credentials.py` with allowlist), single Postgres role/engine. **Do NOT** add a `CREATE ROLE eq_vault_service` step to this Prisma migration — that's psql-only syntax incompatible with Prisma. Phase 2.1 hardening will introduce the second role + engine if security audit demands.

5. **Run `prisma migrate create`** + commit migration file.

6. **Pre-deploy verification:**
   - Vercel preview build runs `prisma migrate deploy` against preview DB → confirms migration validity
   - Live-db CI workflow (must include `DIRECT_DATABASE_URL` env var; see lessons.md "eq-frontend live-db CI workflow")

7. **Deploy to production:** Merge eq-frontend PR; Vercel deploys; Neon production schema updated.

8. **Post-deploy verification:**
   ```sql
   -- Via Neon MCP, verify schema landed:
   SELECT column_name, data_type, is_nullable
   FROM information_schema.columns
   WHERE table_schema = 'vault' AND table_name = 'user_credentials';

   SELECT conname, pg_get_constraintdef(oid)
   FROM pg_constraint
   WHERE conrelid = 'vault.user_credentials'::regclass;

   SELECT indexname, indexdef FROM pg_indexes
   WHERE schemaname = 'public' AND tablename = 'external_integration_runs';
   ```
   Expected: composite UNIQUE on both tables, FK on tenant_id/user_id/account_id, indexes present.

9. **Run `scripts/verify_schema.py`** against the new tables' SQL to confirm PREPARE succeeds.

**Exit criteria:**
- `vault` schema + `vault.user_credentials` live in production Neon with explicit FK relations
- `public.external_integration_runs` live in production Neon with `granola_note_snapshot` JSONB column and `queue_id` FK
- Schema verification (information_schema + verify_schema.py) PASS
- Backend connects via existing `DATABASE_URL` (no second engine for MVP per LOCKED-42)

---

#### Phase 2b — Vault module (~0.5 day)

**Repo:** `live-transcription-fastapi`

**New files:**
- `services/vault/__init__.py`
- `services/vault/encryption.py` — KMS GenerateDataKey + AES-256-GCM encrypt/decrypt
- `services/vault/user_credentials.py` — typed accessor module
- `services/vault/errors.py` — structured error code enum

**Structured error codes (LOCKED-33; all module-prefixed for consistency):**
```python
class VaultErrorCode(str, Enum):
    VAULT_KMS_ENCRYPT_FAILED = "vault_kms_encrypt_failed"
    VAULT_KMS_DECRYPT_FAILED = "vault_kms_decrypt_failed"
    VAULT_KMS_CONTEXT_MISMATCH = "vault_kms_context_mismatch"
    VAULT_AES_GCM_TAG_MISMATCH = "vault_aes_gcm_tag_mismatch"
    VAULT_DB_INSERT_FAILED = "vault_db_insert_failed"
    VAULT_DB_NOT_FOUND = "vault_db_not_found"
    VAULT_CALLER_NOT_ALLOWED = "vault_caller_not_allowed"
```

**Accessor signature (per LOCKED-40 — EncryptionContext binds tenant_id + user_id + provider + credential_id):**
```python
async def get_granola_credential_for_user(
    *, tenant_id: UUID, user_id: UUID, caller_module: str
) -> Optional[GranolaCredential]:
    """
    Decrypt and return a user's Granola credential.

    Raises VaultPermissionError if caller_module not in ALLOWLIST.
    Raises VaultError on KMS/DB failures (with structured error_code).
    Returns None if no active credential exists.

    KMS EncryptionContext = {
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "provider": "granola",
        "credential_id": str(credential.id),  # bound at Encrypt time; row-level isolation
    }
    KMS refuses Decrypt if ANY of the four fields don't match what was bound at Encrypt time.
    """

ALLOWLIST = {
    "services.granola_ingestion.adapter",
    "services.granola_ingestion.scheduler",
    "routers.granola",  # for /validate, /connect, /rotate, /status
}


async def store_credential(
    *, tenant_id: UUID, user_id: UUID, provider: str, api_key: str, config: dict
) -> UUID:
    """
    Encrypt and persist a credential. Per LOCKED-43, mints a FRESH DEK + FRESH nonce
    on every call (insert AND update-in-place rotate). Nonce reuse on the same DEK
    breaks AES-GCM authentication — structurally prevented by always generating new
    DEK via KMS GenerateDataKey + new 96-bit random nonce.

    Returns the credential UUID. EncryptionContext bound at GenerateDataKey time
    includes the credential UUID, so KMS Decrypt later requires the same UUID.
    """


async def rotate_credential_key(
    *, credential_id: UUID, new_api_key: str
) -> None:
    """
    Rotate the encrypted API key for an existing credential row.
    Mints FRESH DEK + FRESH nonce (NEVER reuses old DEK or old nonce).
    EncryptionContext at rotate keeps the same credential_id, so existing
    KMS Decrypt callers still work — the binding identity is the row's UUID,
    not the key material.
    """
```

**Unit tests:** AsyncMock-based (no Docker per [test-pattern-no-docker-default](../../../.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/feedback_test_pattern_no_docker.md))
- Round-trip encrypt → decrypt with mocked boto3 KMS client (full 4-field EncryptionContext)
- EncryptionContext mismatch fails closed (tenant_id, user_id, provider, credential_id — verify each individually)
- Caller-not-in-allowlist raises VaultPermissionError
- AES-GCM tag mismatch surfaces as structured error
- **Rotate path mints fresh DEK + fresh nonce on each call** — assert nonce differs from prior write; assert encrypted_dek differs from prior write (LOCKED-43)
- **Cross-user decrypt rejection**: encrypt with `(tenant_A, user_A, ...)`, try Decrypt with `(tenant_A, user_B, ...)` — KMS rejects (verifies per-row binding from LOCKED-40)
- DB connection uses standard `DATABASE_URL` (single engine per LOCKED-42)

**Exit criteria:** All vault unit tests pass; module is callable from a Python REPL with KMS access.

---

#### Phase 2c — Granola API client (~0.5 day)

**Repo:** `live-transcription-fastapi`

**New file:** `services/granola_ingestion/api_client.py`

**Methods:**
```python
class GranolaAPIClient:
    async def list_folders(self) -> list[GranolaFolder]: ...
    async def list_notes(self, folder_id: str, created_after: datetime | None) -> list[GranolaNoteSummary]: ...
    async def get_note_detail(self, note_id: str) -> GranolaNoteDetail: ...
```

**Configuration:**
- Base URL: `https://public-api.granola.ai/v1` (empirically verified 2026-05-22; brainstorm doc had `api.granola.ai` which is wrong)
- Timeout: 30s per request
- Retry strategy on 5xx/network errors: exponential backoff with jitter
  - 1s → 2s → 4s → 8s (max 4 retries per request)
- 429 handling: honor `Retry-After` header; sleep that long; resume
- All errors mapped to structured codes:
  - `auth_failed` (401)
  - `folder_not_found` (404 on folder lookup)
  - `granola_5xx` (502/503/504)
  - `granola_429` (rate limit)
  - `granola_timeout` (httpx.TimeoutException)
  - `granola_parse_error` (Pydantic validation fails on response)

**Pydantic models** matching empirically-verified Granola response shapes (per brainstorm doc §"Empirical Granola API findings"):
- `GranolaFolder`: `id`, `name`, `parent_folder_id`
- `GranolaNoteSummary`: `id`, `title`, `created_at`, `updated_at`, `folder_membership`
- `GranolaNoteDetail`: full payload incl. `attendees`, `calendar_event`, `transcript`, `summary_markdown`, `summary_text`, `web_url`

**Unit tests:** httpx mock transport
- Happy path for each method
- 5xx with retry → eventual success
- 5xx exhausted → `granola_5xx`
- 401 → `auth_failed` (no retry)
- 429 with Retry-After → honored
- Malformed response → `granola_parse_error`

**Exit criteria:** API client unit tests pass; client can call Peter's real Granola account from a Python REPL.

---

#### Phase 2d — Granola adapter (Path 2 logic) (~1.5 days)

**Repo:** `live-transcription-fastapi`

**New files:**
- `services/granola_ingestion/__init__.py`
- `services/granola_ingestion/adapter.py` — the core per-credential cycle
- `services/granola_ingestion/path2.py` — attendee classification + Scenario A/B/C/D branching
- `services/granola_ingestion/outcomes.py` — `IngestionOutcome` enum

**Outcome enum (Q7 tri-state):**
```python
class IngestionOutcome(str, Enum):
    SUCCESS = "success"
    DEFERRED_PENDING_ACCOUNT = "deferred_pending_account"
    SKIPPED_NO_BUSINESS_ATTENDEES = "skipped_no_business_attendees"
    FAILED = "failed"  # transient, will retry
    FAILED_PERMANENT = "failed_permanent"  # 5+ retries exhausted
```

**Per-credential cycle pseudocode:**
```python
async def run_one_cycle(credential_id: UUID) -> CycleResult:
    async with dbos_workflow_lock(f"granola_cycle_{credential_id}"):
        credential = await vault.get_granola_credential(credential_id)
        if credential.status != "active":
            return CycleResult(skipped=True, reason="credential_not_active")

        client = GranolaAPIClient(api_key=credential.api_key)

        # 1. Fetch new notes created after last_polled_at (Granola's filter param is `created_after`)
        try:
            notes = await client.list_notes(
                folder_id=credential.config["folder_id"],
                created_after=credential.last_polled_at,
            )
        except AuthFailedError:
            await mark_credential_revoked(credential_id)
            return CycleResult(error="auth_failed")
        except FolderNotFoundError:
            await mark_credential_error(credential_id, "folder_not_found")
            return CycleResult(error="folder_not_found")
        except (Granola5xxError, GranolaTimeoutError) as e:
            await increment_consecutive_failures(credential_id, e.error_code)
            if credential.consecutive_failures >= 3:  # 15-min boundary
                await mark_credential_error(credential_id, e.error_code)
                await send_credential_breakage_email(credential)
            return CycleResult(error=e.error_code)

        # 2. Process each note via Path 2
        for note in notes:
            await process_note(credential, note, client)

        # 3. Re-poll deferred-pending-account rows
        await reprocess_deferred_notes(credential, client)

        # 4. Update credential
        await mark_credential_success(credential_id)
        return CycleResult(notes_processed=len(notes))


async def process_note(credential, note_summary, client) -> IngestionOutcome:
    # Check if already ingested
    existing = await get_integration_run(
        tenant_id=credential.tenant_id,
        user_id=credential.user_id,
        provider="granola",
        external_id=note_summary.id,
    )
    if existing and existing.status == "success":
        return IngestionOutcome.SUCCESS  # already ingested

    try:
        detail = await client.get_note_detail(note_summary.id)
    except GranolaParseError as e:
        return await record_failed_note(credential, note_summary, "granola_parse_error", str(e))

    # Path 2 attendee classification
    business_domains = classify_attendees(detail.attendees, credential.tenant_id)

    if not business_domains:
        return await record_skipped_note(credential, note_summary, "no_business_attendees")

    known_accounts = await lookup_known_accounts(credential.tenant_id, business_domains)

    if known_accounts:
        # Scenario A or B: at least one known anchor
        anchor_account_id = pick_anchor(known_accounts)
        envelope = build_envelope(credential, detail, anchor_account_id)
        # LOCKED-41: direct call to extracted text_clean service, NOT HTTP.
        # tenant_id flows as explicit argument — tenant isolation preserved by
        # entity-sourced identity pattern (credential.tenant_id was JWT-validated
        # at /connect time and is row-immutable per UNIQUE constraint).
        await text_clean_service.process(
            tenant_id=credential.tenant_id,
            user_id=credential.user_id,
            account_id=anchor_account_id,
            envelope=envelope,
        )
        return await record_success(credential, detail, anchor_account_id)
    else:
        # Scenario C: no known accounts; defer + queue signals.
        # LOCKED-44: capture granola_note_snapshot at defer time so the meeting
        # remains recoverable even if Granola removes the note before approval.
        return await defer_pending_account(credential, detail, business_domains)
```

**Envelope build (per LOCKED-35/36):**
```python
envelope = EnvelopeV1(
    tenant_id=credential.tenant_id,
    user_id=str(credential.user_id),
    interaction_type="meeting",         # LOCKED-25
    source="generic",                   # LOCKED-35 (or "api" if Phase 0 verification surfaced drift)
    account_id=str(anchor_account_id),
    timestamp=detail.created_at,
    content=ContentModel(
        text=build_transcript_with_frontmatter(detail),
        format="plain",                 # LOCKED-35
    ),
    extras={
        "granola_note_id": detail.id,
        "granola_web_url": detail.web_url,
        "granola_folder_name": credential.config.get("folder_name"),
        "granola_summary_text": detail.summary_text,
        "granola_calendar_event_id": detail.calendar_event.id if detail.calendar_event else None,
        "granola_attendees_raw": [a.dict() for a in detail.attendees],
    },
)
```

**Retry budget (Q7):**
- Per-note `failed` retries: 5 attempts. After 5 → `failed_permanent`.
- Per-credential consecutive failure threshold: 3 cycles (= 15 min @ 5-min cadence) → `credential.status="error"` + transactional email.

**Unit tests** (AsyncMock-based):
- Scenario A: 1 known account in attendees → `text_clean_service.process` called with `tenant_id=credential.tenant_id` (assert tenant isolation flow)
- Scenario B: mix of known + unknown → ingest with known anchor; existing signal-queue logic handles unknowns
- Scenario C: no known accounts → deferred row with `granola_note_snapshot` populated, no envelope emitted, queue entry created with `queue_id` linked back to `external_integration_runs` row
- Scenario D: no business attendees → skipped
- Deferred re-poll using snapshot: simulate Granola returning 404 for the deferred note; re-poll still succeeds using `granola_note_snapshot` (LOCKED-44 recoverability)
- Deferred re-poll happy path: after account approval, next cycle picks it up
- Auth failure → credential.status="revoked"
- 5xx with consecutive_failures < 3 → silent retry; consecutive_failures = 3 → status="error" + email
- Note parse error → external_integration_runs row with `status="failed"`, `error_code="granola_parse_error"`
- Cross-tenant guard: build envelope with credential from tenant_A; assert `text_clean_service.process` is called with `tenant_id=tenant_A` and no path can substitute a different tenant_id

**Exit criteria:**
- All adapter unit tests pass
- Scenario C → Scenario A re-poll loop verified end-to-end against mock data
- Scenario C recoverability verified: deferred note's Granola counterpart deleted, re-poll still succeeds using snapshot

---

#### Phase 2e — Scheduler: Railway cron + DBOS queue with SetWorkflowID (~0.5 day)

**Repo:** `live-transcription-fastapi`

**New files:**
- `services/granola_ingestion/scheduler.py` — DBOS workflow + queue primitives (pure orchestration; I/O lives in DBOS steps)
- `routers/granola_cron.py` — HTTP endpoint Railway cron calls every 5 min

**Pattern (per LOCKED-39, aligned with repo's DBOS architecture in `docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md`):**

```python
# routers/granola_cron.py — Railway cron POSTs here every 5 min
@router.post("/internal/granola/cron-tick")
async def granola_cron_tick(_: InternalCronAuth = Depends(verify_internal_cron_secret)):
    """
    Triggered by Railway cron every 5 min.
    Reads active credentials in a DBOS step, then enqueues per-credential workflows
    with explicit SetWorkflowID derived from (credential_id, cycle_window_minute).
    DBOS guarantees at-most-once per workflow_id.
    """
    credentials = await list_active_credentials_step()  # DBOS step — I/O lives here
    cycle_minute = int(datetime.utcnow().timestamp() // 60)
    for credential in credentials:
        workflow_id = f"granola_poll_{credential.id}_{cycle_minute // 5}"  # 5-min cycle window
        await DBOS.start_workflow(
            granola_poll_one_credential,
            credential.id,
            workflow_id=workflow_id,  # explicit SetWorkflowID — dedup if cycle overlaps
        )
    return {"enqueued": len(credentials), "cycle_window": cycle_minute // 5}


@DBOS.workflow()
async def granola_poll_one_credential(credential_id: UUID):
    """Pure orchestration. All I/O delegates to @DBOS.step functions."""
    credential = await load_credential_step(credential_id)
    if credential.status != "active":
        return PollResult(skipped=True, reason="credential_not_active")

    api_key = await decrypt_credential_step(credential_id)  # vault module via step
    notes = await fetch_granola_notes_step(api_key, credential.config["folder_id"], credential.last_polled_at)

    for note in notes:
        await process_note_step(credential_id, note)

    await reprocess_deferred_notes_step(credential_id, api_key)
    await mark_credential_polled_step(credential_id)
    return PollResult(notes_processed=len(notes))


@DBOS.step()
async def list_active_credentials_step() -> list[CredentialMetadata]: ...

@DBOS.step()
async def load_credential_step(credential_id: UUID) -> CredentialMetadata: ...

@DBOS.step()
async def decrypt_credential_step(credential_id: UUID) -> str: ...

# ... etc; each I/O operation is its own @DBOS.step
```

**Railway cron configuration** (in Railway dashboard):
- Schedule: `*/5 * * * *`
- Command: `curl -X POST -H "X-Internal-Cron-Secret: $INTERNAL_CRON_SECRET" http://localhost:8080/internal/granola/cron-tick`
- Or Railway's native cron service if available

**Internal cron auth:** new env var `INTERNAL_CRON_SECRET` (random 32-byte hex). FastAPI dependency `verify_internal_cron_secret` rejects requests without the header.

**Wired into existing DBOS lifespan** at `main.py:114` (`dbos_lifespan(app)`).

**Health endpoint addition:** `/api/health` includes `"granola_adapter": {"last_cycle_success_at": "...", "active_credentials": N, "scheduling": "railway-cron"}`.

**Unit tests:**
- workflow_id dedup: two `start_workflow` calls in the same cycle_window with same credential_id → only one runs to completion (the second is a DBOS no-op)
- Cron auth: missing/wrong `X-Internal-Cron-Secret` → 401
- Workflow durability: simulate restart; in-flight workflow resumes correctly via DBOS step replay

**Exit criteria:**
- Railway cron job registered and pointing at `/internal/granola/cron-tick`
- workflow_id dedup verified empirically (two manual triggers within same window → one execution)
- /api/health reports adapter status
- One synthetic credential triggers a poll cycle within 5 min of insertion

---

#### Phase 2f — Admin/health endpoints (~0.5 day)

**Repo:** `live-transcription-fastapi`

**New file:** `routers/granola.py`

**Endpoints (all JWT-authed; tenant + user_id resolved from JWT claims):**

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/integrations/granola/validate` | `{api_key}` | `{ok: true, folders: [...]}` or `{ok: false, reason: "auth_failed" \| "rate_limited" \| "outage"}` |
| POST | `/integrations/granola/connect` | `{api_key, folder_id}` | `{ok: true, status: "connected", first_poll: {ingested: N, errors: 0}}` |
| POST | `/integrations/granola/rotate` | `{new_api_key}` | `{ok: true}` |
| PATCH | `/integrations/granola/folder` | `{folder_id}` | `{ok: true}` |
| DELETE | `/integrations/granola` | — | `{ok: true, status: "disconnected"}` |
| GET | `/integrations/granola/status` | — | Status panel data: `{connected: bool, last_polled_at, activity: {ingested_7d, deferred_7d, errors_7d}, status: "active" \| "revoked" \| "error", folder: {id, name}}` |

**Validation flow (`/validate`):**
- POST `{api_key}` → backend calls `GET /v1/notes?page_size=1` against Granola
- On 200: also call `GET /v1/folders` and return the folder list
- On 401: return `{ok: false, reason: "auth_failed"}`
- On 429/5xx: return `{ok: false, reason: "rate_limited" | "outage"}`
- **Does NOT store the key** — purely a validation call

**Connect flow (`/connect`):**
- Encrypt `api_key` via vault
- INSERT row in `vault.user_credentials` with status="active"
- Run one synchronous test poll via `run_one_cycle(credential_id)`
- Return first_poll result

**Disconnect flow (DELETE):**
- Soft-delete: `UPDATE vault.user_credentials SET archived_at=NOW(), status='archived'`
- DBOS scheduler skips archived rows

**Unit tests:**
- Validate with invalid key → 401-equivalent response
- Connect happy path → credential row + external_integration_runs row created
- Reconnect after disconnect → UPDATE existing row (not INSERT new — UNIQUE constraint)

**Exit criteria:**
- All endpoints respond with documented shapes
- Reconnect-after-disconnect path works end-to-end

---

#### Phase 2g — Transactional email on credential breakage (~0.5 day)

**Repo:** `live-transcription-fastapi`

**Goal:** Send one email when `credential.status` transitions to `revoked` or `error`.

**Implementation:**
- Use Resend SDK (or whatever email provider is wired for eq-frontend transactional emails — verify with user before committing)
- New module: `services/granola_ingestion/notifications.py`
- Idempotency key: `credential_id + status + transition_timestamp`; insert into `vault.user_credentials.last_error.email_sent_at` to dedup
- Email template:
  - Subject: "Your Granola connection needs attention"
  - Body: explains the error_code in plain English, deep-links to `/dashboard/settings/integrations/granola`

**Unit tests:**
- Transition from active → revoked sends one email
- Subsequent revoked → revoked transitions don't re-send
- Recovery (revoked → active via reconnect) resets the dedup key for next breakage

**Exit criteria:** Single email sent per credential breakage; no duplicates on repeated polls.

**Note:** If Resend isn't already wired in this repo, this phase can ship without it and surface as Phase 2.1 follow-up. The credential status transition + structured logging still works; just no email.

---

### Phase 3 — Frontend (~2 days)

**Repo:** `eq-frontend`

#### Phase 3a — Granola Connect settings page (~1 day)

**Route:** `/dashboard/settings/integrations/granola`

**Components:**
- `GranolaConnectPage` — top-level; toggles between empty-state wizard and connected status panel based on `GET /integrations/granola/status`
- `GranolaConnectWizard` — two-step (paste key → pick folder) with inline transitions, no page reloads
- `GranolaStatusPanel` — connected state with key last-4, folder, 7-day activity, rotate/change/disconnect affordances
- `GranolaPendingApprovalsBadge` — small count badge linking to EQ-native Pending Approvals page

**Backend API client:**
- `POST /integrations/granola/validate` (synchronous; ~2s)
- `POST /integrations/granola/connect` (synchronous; ~2s)
- `POST /integrations/granola/rotate`
- `PATCH /integrations/granola/folder`
- `DELETE /integrations/granola` (with confirmation modal)
- `GET /integrations/granola/status` (polled on page load + after mutations)

**UX details (LOCKED-30/31/34):**
- Two-step wizard: validate key → pick folder
- Save & test runs synchronous one-shot poll
- Activity timeframe: last 7 days
- Disconnect: soft-delete (preserves audit)
- Key display: last 4 only after onboarding
- Banner on credential status="revoked" with "Reconnect" CTA
- Banner on credential status="error" with reason + appropriate CTA

**Tests:** React Testing Library for component behavior; Playwright/MSW for E2E auth + API flows.

---

#### Phase 3b — Pending Approvals component (EQ-native) (~1 day)

**Repo:** `eq-frontend`

**Route:** `/dashboard/pending-approvals` (or wherever EQ-native makes sense — TBD with user)

**Goal:** EQ-native Pending Approvals UI that serves ALL sources (email pipeline, Granola, future Fireflies/Otter/etc).

**Component design:**
- List of pending queue entries from `pending_account_mappings`
- Each entry shows: domain, source(s), signal count, age
- Approve button → calls existing `/queue/{id}/approve` endpoint
- Ignore button → calls existing ignore endpoint
- Source-agnostic: a single entry can have signals from multiple sources (email + Granola), shown as multi-source attribution

**Decoupling from Granola:**
- This component is NOT in `/dashboard/settings/integrations/granola`
- This component does NOT mention Granola in its core; it's a general inbox
- Granola adapter just emits signals into the same queue email pipeline already uses

**Tests:** RTL component tests; happy path approve/ignore.

---

### Phase 4 — Testing (~1 day)

#### Phase 4a — Unit tests (per-module, AsyncMock)

Already enumerated per phase above. Combined target: ~80 unit tests.

#### Phase 4b — Integration tests (Neon test tenant; per [test-pattern-no-docker-default](../../../.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/feedback_test_pattern_no_docker.md))

**Setup:** Each integration test uses `tenant_id = 11111111-1111-4111-8111-111111111111` (test tenant). Conftest fixtures clean up via `@pytest.mark.requires_db_write` per the [shared-infrastructure-collision protocol](../../../.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/feedback_shared_infrastructure_collision.md).

**Scenarios:**
1. Vault round-trip: insert credential → decrypt → verify plaintext match
2. End-to-end Scenario A: pre-seed known account → insert credential → trigger poll cycle → verify `external_integration_runs.status="success"` + `raw_interactions` row exists
3. End-to-end Scenario C: insert credential with attendees from unknown business domain → trigger poll → verify `external_integration_runs.status="deferred_pending_account"` + queue entry exists in `pending_account_mappings`
4. Path 2 re-poll: complete the queue approval → trigger next poll cycle → verify status updates to "success" + interaction created
5. Reconnect-after-disconnect: soft-delete credential → reconnect with new key → verify UPDATE not INSERT
6. UNIQUE constraint absorbs deferred-pending-account race: insert 2 deferred runs for same domain → verify only one queue entry

#### Phase 4c — Production E2E

**Setup:** Peter as design partner #0; real Granola API key; real test folder; real production Railway + Neon.

**Pre-flight checks** (per [shared-infrastructure-collision](../../../.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/feedback_shared_infrastructure_collision.md)):
```bash
# 1. Confirm no other agents active in test tenant in last hour
ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10

# 2. Confirm production health
curl https://live-transcription-fastapi.up.railway.app/api/health
```

**Steps:**
1. Insert credential via UI: paste real key, validate, pick "EQ" folder, save
2. Wait for one poll cycle to complete (max 5 min)
3. Verify in Neon:
   ```sql
   SELECT * FROM vault.user_credentials WHERE provider='granola' AND archived_at IS NULL;
   SELECT * FROM external_integration_runs WHERE provider='granola' ORDER BY created_at DESC LIMIT 5;
   SELECT * FROM raw_interactions WHERE interaction_type='meeting' ORDER BY created_at DESC LIMIT 5;
   ```
4. Place a test transcript in the Granola "EQ" folder with at least one business attendee from a known account
5. Wait for next poll cycle → verify ingestion + interaction created + envelope emitted
6. Verify downstream consumption (eq-structured-graph-core + action-item-graph) — confirm no DLQ messages for the new envelope
7. **Scenario C trigger:** Place a transcript with attendees from unknown business domain → verify deferred row + pending queue entry → approve via UI → verify next poll completes the ingestion

**Test cleanup** (LOCKED-11):
```sql
-- Atomic cleanup of test artifacts
BEGIN;
DELETE FROM external_integration_runs WHERE tenant_id = :test_tenant_id AND provider = 'granola';
UPDATE vault.user_credentials SET archived_at = NOW() WHERE tenant_id = :test_tenant_id AND provider = 'granola';
-- ... full cleanup chain
COMMIT;
```

**Exit criteria:**
- All scenarios PASS end-to-end
- No DLQ messages from new Granola envelopes
- /api/health 200 throughout
- Test tenant atomically cleaned

---

### Phase 5 — Pre-merge Codex review gate

Per [codex-pre-merge-gate](../../../.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/feedback_codex_pre_merge_gate.md):

1. Open PR(s) to `main` in each repo (eq-frontend, live-transcription-fastapi).
2. Run `/codex review` on each PR diff:
   - 4-round soft cap; extend if real P1s keep surfacing per the round-N convergence pattern in lessons.md
   - All P1 findings folded into the same PR before merge
3. **Run mandatory verification scripts:**
   ```
   python scripts/verify_consumer_contracts.py \
     --source generic \
     --interaction-type meeting \
     --extras-keys "granola_note_id,granola_web_url,granola_folder_name,granola_summary_text,granola_calendar_event_id,granola_attendees_raw"

   python scripts/verify_schema.py --sql-text "$(cat services/granola_ingestion/sql.py)"
   ```
4. Document the verification results in each PR description.

**No merge without:** 0 P1 findings + 0 drift in verify_consumer_contracts.py + 0 unresolved schema errors in verify_schema.py.

---

### Phase 6 — Deploy + verify

1. **Merge order:**
   - eq-frontend Prisma migration PR first
   - Wait for Vercel deploy + Neon schema confirmed
   - live-transcription-fastapi PR second
   - Wait for Railway deploy + /api/health 200
   - eq-frontend application PR (UI) last (depends on backend endpoints)
2. **Post-deploy:**
   - /api/health 200 across all services
   - One full E2E (Peter as design partner #0) confirms end-to-end
3. **Onboard design partner #1** via the UI (no engineer involvement).
4. **Update MEMORY.md** with new project status.

---

## Test scenarios mapped to Q7 failure modes

| # | Failure mode | Test |
|---|---|---|
| 1 | Granola API key revoked | Mock 401 from list_notes → credential.status="revoked", transactional email sent |
| 2 | Folder deleted on Granola | Mock 404 from list_notes → credential.status="error", `error_code="folder_not_found"`, email sent |
| 3 | KMS decrypt failure | Mock boto3 KMS decrypt to raise → external_integration_runs row with `error_code="kms_decrypt_failed"`, no user-facing banner (admin alert) |
| 4 | Granola 5xx outage | Mock 503 from list_notes for 3 consecutive cycles → consecutive_failures=3 → credential.status="error", email |
| 5 | Rate limit (429) | Mock 429 with Retry-After=60 → sleep, retry succeeds |
| 6 | Network timeout | Mock httpx.TimeoutException → consecutive_failures++; same as #4 trajectory |
| 7 | Malformed Granola note JSON | Mock note detail with missing transcript array → row with `error_code="granola_parse_error"`, `status="failed"`, retry_count++ |
| 8 | text_clean_service raises | Mock service to raise → row with `error_code="downstream_text_clean_error"`, retry next cycle (LOCKED-41: no HTTP call) |
| 9 | Race: 2 deferred notes from same domain in one cycle | Insert 2 notes with same unknown domain → verify UNIQUE constraint on pending_account_mappings absorbs the race; only one queue entry |
| 10 | Poll cycle > 5 min | Simulate slow cycle; verify next cycle's lock acquisition returns "skipped" |
| 11 | App restart mid-poll | Kill DBOS process during cycle; verify resume on restart |
| 12 | Multiple credentials in same tenant | Two users in same tenant connect Granola → both poll independently, no cross-tenant leakage |
| 13 | Reconnect after disconnect | Soft-delete credential → reconnect with new key → verify UPDATE not INSERT, UNIQUE constraint holds |

---

## Post-implementation follow-ups (Phase 2.1+)

Captured here so they don't get lost:

1. **Alerting wire-up** (LOCKED-32 deferred): Wire Slack webhook OR Resend for the critical alert categories (KMS failure, adapter not running > 30 min, credential breakage). Estimated 0.5 day.
2. **Second Postgres role + engine for vault** (LOCKED-42 deferred): Add `eq_vault_service` role with grants restricted to `vault` schema only; route vault module through a second SQLAlchemy engine with role-restricted DATABASE_URL. Provides defense-in-depth above the audited accessor module's app-layer guard. Estimated 0.5 day.
3. **Cross-user queue visibility for Pending Approvals**: Current `pending_account_mappings` queue is first-owner-wins (`pending_account_mappings.py:101`); the Granola adapter sets owner_id = credential.user_id, so each Granola user approves their own deferred signals. Phase 2.1 could relax this to tenant-admin-visible-and-approvable for true source-agnostic Pending Approvals UX. Estimated 0.5-1 day.
4. **Re-open lifecycle hardening**: Current `pending_account_mappings.py:54` reopen helper is incomplete and can strand rows in a state that 409s on approve/map. Codex flagged this as a sequencing concern — adding more unknown-domain notes to the queue stresses the reopen path. Estimated 1 day (independent of Granola but accelerated relevance).
5. **Org-admin bulk-onboarding** (Phase 3 trigger: > 10 users): Bulk-import + per-user invitation flow. Estimated 1-2 days.
6. **Reverse-sync** (only if user feedback demands it): Granola edits/deletes/folder-moves propagate back to EQ. Significant work; LOCKED-27 says no for MVP.
7. **Webhooks instead of polling** (when Granola releases webhooks): Lower latency; eliminates poll-overlap concerns. ~1 day.
8. **Extend verify_consumer_contracts.py** to assert extras shape constraints (currently verifies source + interaction_type only).
9. **Event-driven deferred-note re-process**: when user approves a queued domain, trigger immediate re-process of any deferred Granola notes for that domain (instead of waiting for next 5-min cron tick). ~0.5 day.
10. **AES-GCM nonce reuse detection** (Phase 2.1 hardening): Add monitoring/test that detects accidental nonce reuse across rotates. Probability of collision is negligible at our scale (random 96-bit nonce), but explicit detection costs nothing once the rotate path is stable.
11. **Phase 2 backlog items** (Neo4j MERGE-everywhere refactor, contact identity state machine, etc.): Independent initiatives, prioritization TBD after Granola ships.
12. **Adapter archived_at-awareness (Phase 2f Codex R7/R8/R9 follow-up):** the Phase 2d adapter's credential-state UPDATEs (`_mark_credential_polled_success`, `_set_credential_status`, `_record_credential_transient_failure`) and `process_note` do NOT re-check `archived_at`/`status` mid-cycle. So a credential lifecycle change (reconnect/rotate/disconnect) that lands WHILE a cycle is in flight races it. Phase 2f mitigated reconnect + rotate at the endpoint layer via a shared per-credential advisory lock (`routers/granola._credential_poll_lock`), but `/disconnect` during an in-flight cycle still lets that cycle finish ingesting a few notes before stopping (no FUTURE cycles run — `list_active_credentials` filters archived; no corruption). Root fix: guard the 3 credential-state UPDATEs on `archived_at IS NULL` AND have `run_one_cycle`/`process_note` re-check the credential is still active before each publish, so an archived-mid-cycle credential aborts. Closes R7/R8/R9 at the source and would let the per-endpoint locks be removed. Estimated 0.5 day. **✅ SHIPPED 2026-05-25 (edge #12 PR, branch `phase-2.1/granola-adapter-archived-at-guard`)** — root fix landed: 3 credential-state UPDATEs guarded on `archived_at IS NULL` + a `_credential_is_active` liveness gate at every async window (before list_notes, per-note loop-top, post-fetch covering the error path, post-classify, pre-publish in `_ingest_scenario_a`, and before the end-of-cycle success UPDATE), plus skip-success-bookkeeping on abort. 8 Codex rounds (R1-R7 folded; R6#2 declined as lock-prevented; R8 residual + R6#2 split to #14/#15 below). The per-endpoint locks were KEPT (belt-and-suspenders), not removed.
13. **/connect bad-folder recovery (Phase 2f Codex R8 P2 follow-up):** `/connect` stores the credential BEFORE the first poll proves `folder_id` is valid. A deleted/typo'd folder leaves a stuck non-archived row (the test poll errors with `granola_folder_not_found`), and since there's no PATCH /folder endpoint, the user must `/disconnect` before retrying the wizard. Fix options: validate `folder_id` against Granola in `/connect` before storing; OR let `/connect` re-configure a broken (revoked/error) non-archived row instead of 409; OR add the PATCH /folder endpoint (plan §Phase 2f table had it; deferred from the shipped 5). Estimated 0.5 day. **Ticketed by user decision 2026-05-25.**
14. **Defer-path write atomicity (edge #12 Codex R8 follow-up):** edge #12 (shipped 2026-05-25) gates every *meaningful* disconnect-during-sync window, but a sub-millisecond window remains INSIDE `_defer_pending_account` — between the post-classify liveness gate and that function's own awaited writes (`_queue_unknown_domain_signals` → `pending_account_mappings`, then `_record_deferred` → `external_integration_runs`), a `/disconnect` could land and the (benign) deferred row + tenant-level pending-approval signals still get written. Closing it fully needs the gate + writes to be ATOMIC (one transaction conditioned on `archived_at`), awkward because `pending_account_mappings` is shared with the email pipeline and keyed on `(tenant, domain)`, not the credential. Benign + self-healing (the deferred row is re-processed on reconnect; the domain signal is harmless), so deferred. Estimated 0.5 day. **Ticketed by user decision 2026-05-25 (ship edge #12 now, fast-follow this).**
15. **Credential-generation token for liveness (edge #12 Codex R6 #2 defense-in-depth):** `_credential_is_active` checks current active-state, not the credential *generation* the cycle started with. A disconnect→quick-reconnect reactivating the SAME credential id mid-cycle would let a stale cycle (old key/folder) pass the check. Currently PREVENTED structurally: both `run_one_cycle` callers hold the per-credential advisory lock for the whole cycle, and `reactivate_credential` is gated on that same lock (409s during an in-flight cycle). A generation token (capture `updated_at` at cycle start, re-verify in `_credential_is_active`) would be defense-in-depth for a hypothetical future lock-free caller. Not reachable today. Estimated 0.25 day. **Ticketed by user decision 2026-05-25.**

---

## Cross-cutting constraints (cite + apply, don't restate)

These are codified in `tasks/lessons.md` and project memory. They apply to every PR in this initiative:

- **Codex pre-merge gate** ([feedback_codex_pre_merge_gate](../../../.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/feedback_codex_pre_merge_gate.md)): mandatory before merge; 4-round soft cap
- **Probe live schema at design time** (lessons.md "Four systemic quality gaps"): Neon MCP queries before any new SQL
- **Cross-service contract verification** (lessons.md "Cross-service contract verification at design time"): verify_consumer_contracts.py mandatory pre-merge
- **Tenant isolation** ([feedback_tenant_isolation](../../../.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/feedback_tenant_isolation.md)): every query MUST include tenant_id in WHERE; never query across tenants
- **Contact ID consistency** ([feedback_contact_id_consistency](../../../.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/feedback_contact_id_consistency.md)): every contact carries UUIDv4 contact_id; resolution happens ONCE upstream
- **Shared infrastructure collision protocol** ([feedback_shared_infrastructure_collision](../../../.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/feedback_shared_infrastructure_collision.md)): check for active agents before destructive SQL on test tenant
- **Branch safety** ([feedback_branch_safety](../../../.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/feedback_branch_safety.md)): all work on `phase-2/granola-integration` branch
- **No Docker in tests by default** ([feedback_test_pattern_no_docker](../../../.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/feedback_test_pattern_no_docker.md)): AsyncMock unit tests + production E2E on test tenant

---

## Pre-merge checklist

For each PR in this initiative:

- [ ] `scripts/verify_consumer_contracts.py` returns 0 drift on the proposed envelope
- [ ] `scripts/verify_schema.py` returns 0 errors on all new SQL constants
- [ ] `/codex review` returns 0 P1 findings (or documented exceptions per round-N pattern)
- [ ] All unit tests passing locally
- [ ] All integration tests passing against test tenant
- [ ] Production E2E §Phase 4c steps 1-7 PASS on test tenant
- [ ] No active agents in other projects in the last hour (test-tenant collision check)
- [ ] PR description cites verification results explicitly
- [ ] No modifications to downstream Pydantic envelope contracts (LOCKED-38)
- [ ] **DBOS scheduling uses external Railway cron + explicit SetWorkflowID** (NOT `@DBOS.scheduled`) per LOCKED-39
- [ ] **KMS EncryptionContext binds all four fields** `{tenant_id, user_id, provider, credential_id}` per LOCKED-40
- [ ] **`text_clean_service` is called directly (NOT HTTP)** per LOCKED-41 with explicit `tenant_id` argument
- [ ] **AES-GCM rotate path mints fresh DEK + fresh nonce on every write** per LOCKED-43 (verified by unit test asserting nonce differs from prior write)
- [ ] **`external_integration_runs.granola_note_snapshot` populated at defer time** per LOCKED-44 (verified by Scenario C unit test)
- [ ] **Prisma migration generates explicit FK relations** (not bare scalar tenant_id/user_id/account_id)
- [ ] **No `CREATE ROLE ... PASSWORD :'...'` inside Prisma migrations** (LOCKED-42 — single role/engine for MVP)
- [ ] **Phase 0 Granola `since` filter verification documented in PR** (either confirmed working or fallback path active)

---

## References

- **Background brainstorm** (load-bearing): `docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md`
- **Verified contract scripts:** `scripts/verify_consumer_contracts.py` + `scripts/verify_schema.py`
- **Phase 1 architectural precedents:** `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md`
- **Contacts architecture (cross-service):** `docs/contacts-architecture.md`
- **Cross-cutting lessons:** `tasks/lessons.md` (1820 lines as of 2026-05-22)
- **Phase 1 closed plan (architectural precedent):** `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md`
- **AWS account state (verified 2026-05-22):** Account ID `211125681610`, `peter-admin-cli` IAM user, KMS region us-east-1

---

## Build session entry prompt (paste this into the next session)

```
You're starting the Granola integration build. The brainstorm is closed; the plan is locked
(plan + outside-voice Codex review completed 2026-05-22).

START BY READING:
1. tasks/granola-integration-plan.md (this file) — top-to-bottom
2. docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md (background only, don't re-litigate)
3. docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md — MANDATORY for Phase 2e scheduler design (the plan's LOCKED-39 references this doc explicitly)

22 NEW LOCKED decisions (LOCKED-23 through LOCKED-44). Critical ones to internalize:
- LOCKED-39: NOT @DBOS.scheduled — use external Railway cron + DBOS queue with explicit SetWorkflowID
- LOCKED-40: KMS EncryptionContext = {tenant_id, user_id, provider, credential_id} — per-row binding
- LOCKED-41: text_clean_service.process() direct call, NOT /text/clean HTTP — tenant_id flows as argument
- LOCKED-42: Single Postgres engine for MVP; second role + engine = Phase 2.1
- LOCKED-43: Fresh DEK + fresh nonce on every encrypted_api_key write (insert AND rotate)
- LOCKED-44: external_integration_runs.granola_note_snapshot captures defer-time state

Then execute Phase 0 (pre-flight verification):
- Confirm source="generic" works downstream via scripts/verify_consumer_contracts.py
- Verify Granola `since` filter actually works (Phase 0 step 6)
- Read DBOS architecture doc and confirm Phase 2e scheduler design

Then execute Phase 1 (AWS) → Phase 2 (backend) → Phase 3 (frontend) → Phase 4 (testing) sequentially.

Codex pre-merge gate is mandatory; 4-round soft cap.

Coordinate with user before:
- Running any destructive SQL on the test tenant
- Merging the eq-frontend Prisma migration PR (cross-repo coordination)
- Any decision that deviates from the locked plan

User posture: non-developer founder; plain-English explanations; investigate thoroughly; no shortcuts.
```

---

**End of plan. Ready for build.**

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 14 issues found across Architecture / Code Quality / Test / Performance; 3 user-decided via AskUserQuestion (DBOS dedup, text_clean extraction, scope hold), rest folded into plan |
| Codex Review | `/codex consult` | Independent 2nd opinion | 1 | INTEGRATED | 11 findings: 3 user-decided (scheduler primitive, KMS binding strength, strategic scope), 8 must-fix folded into plan (Prisma syntax, AES-GCM rotate, deferred snapshot, Postgres role simplification, queue ownership note, `since` filter verification, etc.) |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | Skipped — scope already locked through brainstorm Q1-Q8 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | Skipped — frontend scope is small (one settings page + one general inbox component); will run /plan-design-review if/when Phase 3 needs visual locking |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | N/A — internal infrastructure, not a developer-facing product |

**CODEX:** 11 substantive findings. 3 surfaced to user as cross-model tension; all chose recommended options. 8 folded into plan as "must-fix" architectural corrections. Most impactful: caught that `@DBOS.scheduled` is deprecated per this repo's own DBOS architecture doc — a miss the initial review made by not reading the repo's prior plans.

**CROSS-MODEL:** Strong convergence. Both reviewers independently identified Postgres role split as architecturally underspecified (eng review C2, codex finding #3). Both identified workflow_id dedup gap (eng review A1, codex finding #2 with concrete reference to existing `routers/queue_actions.py:547` pattern). Codex went deeper on KMS binding tightness (finding #5) — caught a real tenant-internal security gap the eng review missed.

**UNRESOLVED:** 0. All findings are either user-decided or folded into the plan.

**VERDICT:** ENG CLEARED — ready to implement. 22 new LOCKED decisions captured (LOCKED-23 through LOCKED-44). Plan annotates 11 Phase 2.1 follow-ups. Build session can start immediately.
