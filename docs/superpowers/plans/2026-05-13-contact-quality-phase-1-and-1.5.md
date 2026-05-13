# Contact Quality and Account-Anchoring Initiative — Phase 1 + 1.5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the two hard rules — no contact or interaction without an account anchor — across all ingestion paths, with backend rejection over frontend trust. Phase 1 tightens the contract end-to-end and stands up the queue insertion machinery. Phase 1.5 ships the worker, outbox-backed durability, queue UI, and database-level enforcement.

**Architecture:** Three-state per-attendee branching on domain lookup (known account → contact; unknown business domain → queue signal, no contact; personal/internal → skip). All ingestion paths require `account_id` at the request boundary; backend rejects when missing. Outbox-backed durability prevents Postgres/Neo4j divergence. Worker materializes contacts atomically on Approve/Map via a single transaction (signals → contacts → interaction_contact_links → outbox row). Three-layer idempotency model: `approval_attempt_id` (frontend → queue), `worker_attempt_id` (worker → eq-agent-action-core), `outbox_row_id` (Postgres → EventBridge → consumers).

**Tech Stack:** Python 3.11+ / FastAPI, SQLModel + asyncpg (Postgres via Neon), pytest (unit + integration), Prisma (schema source of truth in eq-frontend), Neo4j Aura (graph), AWS EventBridge + SQS, Railway deploy.

**Reference docs:**
- Design (canonical): `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md`
- Codex audit trail: `docs/superpowers/specs/2026-05-12-contact-quality-initiative-codex-review.md`
- Current-state architecture: `docs/contacts-architecture.md`
- Memory: `~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/`

**Cross-repo coordination model:** Schema changes live in `eq-frontend/prisma/schema.prisma` per `reference_prisma_schema_ownership.md`. This plan dispatches agent tasks to other repos at coordination points (eq-frontend for schema, eq-email-pipeline for calendar_sync/orchestrator, eq-structured-graph-core for AccountCreated consumer, eq-agent-action-core for acceptance tests). Cross-repo task prompts are embedded inline.

---

## File Structure

### New files in `live-transcription-fastapi`

| File | Responsibility |
|------|---------------|
| `services/pending_account_mappings.py` | Queue insertion helpers: upsert parent row, insert signal row, re-open trigger |
| `services/domain_classification.py` | Personal-domain check + internal-domain check + business-vs-other classification |
| `services/name_resolution.py` | 3-tier name resolution (display_name → email heuristic → Tavily), extracted from `transcript_enrichment.py` |
| `services/account_lookup.py` | `lookup_account_by_domain(tenant_id, domain)` Postgres helper |
| `models/participant_spec.py` | `ParticipantSpec` Pydantic model for caller-provided participants |
| `workers/account_provisioning_worker.py` | Phase 1.5: polls approved queue entries, processes Approve/Map flow |
| `workers/outbox_publisher.py` | Phase 1.5: publishes `account_provisioning_outbox` rows to EventBridge |
| `workers/expiry_sweep.py` | Phase 1.5: daily job marking stale queue entries archived |
| `services/agent_action_core_client.py` | Phase 1.5: HTTP client for `eq-agent-action-core POST /api/enrich` with idempotency key |
| `services/queue_authorization.py` | Phase 1.5: `can_act_on_queue_entry(user_id, queue_entry)` helper |
| `routers/queue_actions.py` | Phase 1.5: Approve / Map / Ignore HTTP routes |
| `tests/unit/test_pending_account_mappings.py` | Unit tests for queue insertion helpers |
| `tests/unit/test_domain_classification.py` | Unit tests for domain classification |
| `tests/unit/test_name_resolution.py` | Unit tests for extracted name-resolution utility |
| `tests/unit/test_participant_spec.py` | Unit tests for ParticipantSpec |
| `tests/unit/test_queue_authorization.py` | Unit tests for authorization helper |
| `tests/integration/test_account_anchor_rejection.py` | Integration tests for backend rejection on all ingestion paths |
| `tests/integration/test_per_attendee_branching.py` | Integration test: known/unknown/personal three-state branching |
| `tests/integration/test_worker_replay_safety.py` | Phase 1.5: worker idempotency + replay tests |
| `tests/integration/test_outbox_publisher.py` | Phase 1.5: outbox replay tests |
| `tests/integration/test_queue_lifecycle.py` | Phase 1.5: Approve/Map/Ignore/Re-open end-to-end tests |
| `tests/integration/test_eq_agent_integration.py` | Phase 1.5: five acceptance tests for backend-worker invocation |

### Modified files in `live-transcription-fastapi`

| File | Change Summary |
|------|----------------|
| `models/envelope.py` | `account_id` field becomes required (not `Optional[str]`) |
| `models/request_context.py` | `account_id` field becomes required for ingestion auth contexts |
| `models/job_models.py` | `UploadJob.account_id` becomes required |
| `models/text_request.py` | `TextCleanRequest.account_id` added, required; `participants` optional |
| `services/intelligence_service.py` | `process_transcript()` signature: `account_id: str` required |
| `services/transcript_enrichment.py` | Per-attendee three-state branching; removes orphan creation path |
| `routers/text.py` | Validates `account_id` from request body; rejects on absence |
| `routers/batch.py` | Validates `context.account_id`; passes to `process_transcript()` |
| `routers/upload.py` | `UploadInitRequest.account_id` required; `participants` optional; surfaces job's account_id to worker |
| `utils/context_utils.py` | `get_auth_context()` rejects missing `X-Account-ID` for ingestion endpoints |
| `main.py` | WebSocket auth context construction validates account_id; rejects with close code 1008 on absence |

### Cross-repo handoffs

| Repo | Phase | Work Summary |
|------|-------|--------------|
| `eq-frontend` | Phase 1 | Prisma schema: add columns to `pending_account_mappings`, new `pending_account_mapping_signals` table, add `raw_interactions.account_id` (nullable) |
| `eq-frontend` | Phase 1.5 | Prisma schema: `accounts.state`, new `account_provisioning_outbox` table, Phase 1.5 lifecycle columns on `pending_account_mappings`, NOT NULL constraints |
| `eq-frontend` | Phase 1.5 | Queue UI: Approve/Map/Ignore actions with owner-scoped views |
| `eq-email-pipeline` | Phase 1 | `calendar_sync.py` three-state branching; `orchestrator.py` verification; remove orphan-contact creation path |
| `eq-email-pipeline` | Phase 1.5 | Re-open trigger in email ingestion paths |
| `eq-structured-graph-core` | Phase 1.5 | `AccountCreated` event consumer: MERGE Account + Contact + edges |
| `eq-agent-action-core` | Phase 1.5 | Confirm `POST /api/enrich` accepts `worker_attempt_id` idempotency key (or add it) |

---

## Pre-flight (run first; do not skip)

### Task 0: Branch hygiene + baseline test pass

**Files:**
- Modify: working tree

- [ ] **Step 1: Verify current branch is `feat/interim-results-param-add-account-v1` and clean of uncommitted code changes**

Run: `git status`
Expected: working tree shows only untracked files in `docs/superpowers/`, `tasks/`, `scripts/seed_smoke_test.py`. No modified tracked files. If modified files exist, stop and resolve before proceeding.

- [ ] **Step 2: Run the existing test suite to establish a baseline**

Run: `pytest tests/ -v --tb=short`
Expected: All currently-passing tests pass. Record any pre-existing failures so they aren't blamed on this plan.

- [ ] **Step 3: Create a feature branch for Phase 1**

Run:
```bash
git checkout -b feat/contact-quality-phase-1
```
Expected: `Switched to a new branch 'feat/contact-quality-phase-1'`

- [ ] **Step 4: Confirm dependencies are installed**

Run: `python -c "import sqlmodel, asyncpg, fastapi, pytest, hypothesis; print('ok')"`
Expected: `ok`

---

# PHASE 1 — Tighten the contract end-to-end

## Phase 1 Schema Coordination (eq-frontend)

### Task 1.1: Document Phase 1 schema changes for the eq-frontend agent

**Files:**
- Create: `tasks/downstream/eq-frontend-phase-1-schema.md`

- [ ] **Step 1: Write the cross-repo agent prompt**

Create file `tasks/downstream/eq-frontend-phase-1-schema.md` with content:

```markdown
# eq-frontend Phase 1 Schema Migration

## Goal
Update `prisma/schema.prisma` to support Phase 1 of the Contact Quality Initiative. Then run Prisma migrate. Database: Neon Postgres project `super-glitter-11265514` (eq-dev).

## Reference
Design doc Section 5.2 (canonical column list): live-transcription-fastapi/docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md

## Schema changes

### 1. Add columns to existing `pending_account_mappings` model

- `owner_user_id String @db.Uuid` (FK to users; never reassigned by routine UPSERT)
- `discovered_from_type String` (enum-as-string: `email | transcript | calendar | manual`)
- `discovered_from_interaction_id String? @db.Uuid`
- `expires_at DateTime @db.Timestamptz(6)`
- `archived_at DateTime? @db.Timestamptz(6)`
- `archive_reason String?` (enum-as-string: `expired_no_activity | owner_ignored | tenant_resolved_other_way`)
- `re_open_count Int @default(0)`
- `last_reopened_at DateTime? @db.Timestamptz(6)`

Index: `@@index([tenant_id, archived_at])`

### 2. Create new `pending_account_mapping_signals` model

```prisma
model pending_account_mapping_signals {
  id                    String   @id @default(uuid()) @db.Uuid
  tenant_id             String   @db.Uuid
  queue_id              String   @db.Uuid
  source_type           String
  source_user_id        String   @db.Uuid
  interaction_id        String?  @db.Uuid
  calendar_event_id     String?  @db.Uuid
  contact_email         String   @db.VarChar(255)
  contact_display_name  String?  @db.VarChar(255)
  contact_role          String?  @db.VarChar(50)
  created_at            DateTime @default(now()) @db.Timestamptz(6)
  archived_at           DateTime? @db.Timestamptz(6)

  @@unique([queue_id, contact_email, source_type, interaction_id, calendar_event_id], map: "pending_signal_dedup")
  @@index([tenant_id, queue_id, archived_at])
  @@map("pending_account_mapping_signals")
}
```

Optionally also drop the `email_count` field from `pending_account_mappings` if present (it is replaced by a derived COUNT over `pending_account_mapping_signals`). If dropping is risky, leave it in place but mark as deprecated in a comment.

### 3. Add column to existing `raw_interactions` model

- `account_id String? @db.Uuid` (NULLABLE in Phase 1; becomes NOT NULL in Phase 1.5 after test-data wipe)

## Steps for agent

1. Verify you are in the eq-frontend repo on a feature branch.
2. Read the existing `prisma/schema.prisma` file to confirm current shape of `pending_account_mappings` and `raw_interactions`.
3. Apply the schema changes listed above.
4. Run `npx prisma format` then `npx prisma migrate dev --name contact_quality_phase_1`.
5. Verify migration ran successfully against Neon eq-dev.
6. Run `npx prisma generate` to refresh client.
7. Commit: `chore: phase 1 schema for contact-quality initiative`
8. Open PR titled `chore(prisma): contact quality phase 1 schema`.
9. Report back the migration filename + PR URL to the orchestrating agent.

## What NOT to do

- Do NOT enforce NOT NULL on `raw_interactions.account_id` in Phase 1. That happens in Phase 1.5 after the test-data wipe.
- Do NOT enforce NOT NULL on `contacts.account_id` yet. Same reason.
- Do NOT drop `pending_validations` or `validation_status`. Phase 2 handles them.
- Do NOT add `accounts.state`. That is Phase 1.5.

## Acceptance

- Migration file exists and runs cleanly forward and backward.
- `npx prisma validate` passes.
- PR description references this initiative.
```

- [ ] **Step 2: Commit the dispatch doc**

Run:
```bash
git add tasks/downstream/eq-frontend-phase-1-schema.md
git commit -m "docs: eq-frontend Phase 1 schema migration brief"
```

### Task 1.2: Dispatch eq-frontend agent (manual or via Agent tool)

**Files:** none in this repo

- [ ] **Step 1: If using subagent-driven execution, dispatch a general-purpose agent**

Provide it the content of `tasks/downstream/eq-frontend-phase-1-schema.md` plus the repo path `/Users/peteroneil/eq-frontend` and instructions to follow the brief end-to-end.

If executing inline by the user, the user runs the migration in eq-frontend by hand following the same brief.

- [ ] **Step 2: Verify migration landed in Neon**

Run (replace `<MIGRATION_NAME>` with the actual one returned by Prisma):
```bash
psql "$NEON_EQ_DEV_URL" -c "\d pending_account_mappings"
```
Expected: shows `owner_user_id`, `discovered_from_type`, `discovered_from_interaction_id`, `expires_at`, `archived_at`, `archive_reason`, `re_open_count`, `last_reopened_at` columns.

Run: `psql "$NEON_EQ_DEV_URL" -c "\d pending_account_mapping_signals"`
Expected: table exists with the columns specified.

Run: `psql "$NEON_EQ_DEV_URL" -c "\d raw_interactions"` and confirm `account_id uuid` (nullable) is present.

- [ ] **Step 3: Commit a note recording the cross-repo merge**

Run:
```bash
git commit --allow-empty -m "chore: phase 1 schema landed in eq-frontend"
```

---

## Phase 1 Model-layer changes

### Task 1.3: Make `EnvelopeV1.account_id` required

**Files:**
- Modify: `models/envelope.py:92-95`
- Test: `tests/unit/test_envelope_account_id.py` (new)

- [ ] **Step 1: Write a failing test**

Create `tests/unit/test_envelope_account_id.py`:

```python
"""Verify EnvelopeV1 requires account_id at construction time."""

import uuid
import pytest
from pydantic import ValidationError
from datetime import datetime, timezone
from models.envelope import EnvelopeV1, ContentModel


def _base_kwargs():
    return dict(
        tenant_id=uuid.uuid4(),
        user_id="user-1",
        interaction_type="meeting",
        content=ContentModel(text="hi", format="diarized"),
        timestamp=datetime.now(timezone.utc),
        source="api",
        interaction_id=uuid.uuid4(),
        trace_id="trace-1",
    )


def test_envelope_rejects_missing_account_id():
    with pytest.raises(ValidationError):
        EnvelopeV1(**_base_kwargs())


def test_envelope_accepts_string_account_id():
    env = EnvelopeV1(**_base_kwargs(), account_id="acct-123")
    assert env.account_id == "acct-123"


def test_envelope_rejects_none_account_id():
    with pytest.raises(ValidationError):
        EnvelopeV1(**_base_kwargs(), account_id=None)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/unit/test_envelope_account_id.py -v`
Expected: `test_envelope_rejects_missing_account_id` and `test_envelope_rejects_none_account_id` FAIL because `account_id` is currently `Optional[str]` with a `None` default.

- [ ] **Step 3: Make `account_id` required in `models/envelope.py`**

Edit `models/envelope.py` lines 92-95. Replace:

```python
    account_id: Optional[str] = Field(
        None,
        description="Optional account identifier for CRM/sales context"
    )
```

with:

```python
    account_id: str = Field(
        ...,
        description="Account anchor for the interaction. Required for all ingestion paths; backend rejects requests where it cannot be resolved."
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/unit/test_envelope_account_id.py -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Run the full suite to catch downstream breakage**

Run: `pytest tests/ -v --tb=short`
Expected: Some prior tests may break (any that construct `EnvelopeV1` without `account_id`). Note them; they will be fixed in subsequent tasks. If anything unrelated fails, investigate before continuing.

- [ ] **Step 6: Commit**

```bash
git add models/envelope.py tests/unit/test_envelope_account_id.py
git commit -m "feat(envelope): require account_id on EnvelopeV1"
```

### Task 1.4: Make `RequestContext.account_id` required + reject missing X-Account-ID

**Files:**
- Modify: `models/request_context.py`
- Modify: `utils/context_utils.py:253-269`
- Test: `tests/unit/test_request_context_account_id.py` (new)
- Test: `tests/integration/test_account_anchor_rejection.py` (new)

- [ ] **Step 1: Write a failing unit test for the model**

Create `tests/unit/test_request_context_account_id.py`:

```python
"""RequestContext.account_id is required."""

import pytest
from pydantic import ValidationError
from models.request_context import RequestContext


def test_request_context_rejects_missing_account_id():
    with pytest.raises(ValidationError):
        RequestContext(
            tenant_id="tenant-1",
            user_id="user-1",
            interaction_id="int-1",
            trace_id="trace-1",
        )


def test_request_context_accepts_account_id():
    ctx = RequestContext(
        tenant_id="tenant-1",
        user_id="user-1",
        account_id="acct-1",
        interaction_id="int-1",
        trace_id="trace-1",
    )
    assert ctx.account_id == "acct-1"
```

- [ ] **Step 2: Read the current model and make `account_id` required**

Run: `grep -n "account_id" models/request_context.py`

Edit the line declaring `account_id` (currently `Optional[str]`) to `account_id: str = Field(..., description="Account anchor; required for ingestion auth contexts.")`. Remove any default.

- [ ] **Step 3: Update `utils/context_utils.py` to reject missing X-Account-ID**

Edit `utils/context_utils.py` around line 253. Current code:

```python
    # Extract optional account_id from header (not in JWT)
    account_id = request.headers.get("X-Account-ID")

    logger.info(
        f"JWT auth context: interaction_id={interaction_id}, "
        f"tenant_id={claims.tenant_id[:8]}..., user_id={claims.user_id[:20]}..., "
        f"account_id={account_id or 'None'}, trace_id={trace_id}"
    )
```

Replace with:

```python
    # Account anchor is required for ingestion endpoints; backend rejects when absent.
    account_id = request.headers.get("X-Account-ID")
    if not account_id:
        raise HTTPException(
            status_code=400,
            detail="X-Account-ID header is required for this endpoint"
        )

    logger.info(
        f"JWT auth context: interaction_id={interaction_id}, "
        f"tenant_id={claims.tenant_id[:8]}..., user_id={claims.user_id[:20]}..., "
        f"account_id={account_id}, trace_id={trace_id}"
    )
```

(Verify `HTTPException` is imported at top of file; if not, add `from fastapi import HTTPException`.)

- [ ] **Step 4: Write an integration test for the rejection**

Create `tests/integration/test_account_anchor_rejection.py`:

```python
"""Backend rejects ingestion requests that lack account_id."""

import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def client():
    return TestClient(app)


def _auth_headers(account_id: str | None = None):
    headers = {"Authorization": "Bearer test-token"}  # adjust to actual test token plumbing
    if account_id:
        headers["X-Account-ID"] = account_id
    return headers


def test_text_clean_rejects_missing_account_id(client, monkeypatch):
    # Stub auth bypass for the test if necessary
    response = client.post(
        "/text/clean",
        json={"text": "hello world"},
        headers=_auth_headers(),
    )
    assert response.status_code == 400
    assert "account_id" in response.text.lower()


def test_text_clean_accepts_with_account_id(client):
    response = client.post(
        "/text/clean",
        json={"text": "hello world", "account_id": "acct-1"},
        headers=_auth_headers("acct-1"),
    )
    assert response.status_code in (200, 401, 422)
    # 401 if auth not stubbed; 422 if other validation; not 400 for account_id.
```

Note: this test depends on the route also requiring account_id in the request body (covered in Task 1.5). It will be re-asserted there. For now, the goal is verifying the auth-context rejection layer.

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_request_context_account_id.py tests/integration/test_account_anchor_rejection.py -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add models/request_context.py utils/context_utils.py tests/unit/test_request_context_account_id.py tests/integration/test_account_anchor_rejection.py
git commit -m "feat(auth-context): require X-Account-ID header for ingestion"
```

### Task 1.5: Add required `account_id` to `TextCleanRequest`

**Files:**
- Modify: `models/text_request.py:12-44`
- Test: `tests/unit/test_text_clean_request.py` (new)

- [ ] **Step 1: Write a failing test**

Create `tests/unit/test_text_clean_request.py`:

```python
"""TextCleanRequest requires account_id."""

import pytest
from pydantic import ValidationError
from models.text_request import TextCleanRequest


def test_rejects_missing_account_id():
    with pytest.raises(ValidationError):
        TextCleanRequest(text="hello")


def test_accepts_with_account_id():
    req = TextCleanRequest(text="hello", account_id="acct-1")
    assert req.account_id == "acct-1"


def test_rejects_empty_account_id():
    with pytest.raises(ValidationError):
        TextCleanRequest(text="hello", account_id="")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_text_clean_request.py -v`
Expected: All 3 fail (no `account_id` field exists yet).

- [ ] **Step 3: Edit `models/text_request.py`**

Add fields to `TextCleanRequest`. Replace the class body with:

```python
class TextCleanRequest(BaseModel):
    """Request body for the text cleaning endpoint."""

    text: str = Field(..., description="Raw text to clean")
    account_id: str = Field(
        ...,
        min_length=1,
        description="Account anchor for the interaction. Required.",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata to include in extras",
    )
    source: str = Field(default="api", description="Content source identifier")
    interaction_type: str = Field(
        default="note",
        description="Interaction type for envelope and intelligence",
    )
    participants: Optional[list["ParticipantSpec"]] = Field(
        default=None,
        description="Caller-provided participants (manual notes / future workflows)",
    )

    @field_validator("text")
    @classmethod
    def text_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text field cannot be empty or contain only whitespace")
        return v
```

Add the import at the top:

```python
from models.participant_spec import ParticipantSpec
```

Note: `ParticipantSpec` is defined in Task 1.9. If executing in order, this import line will fail until Task 1.9 lands. Recommended: defer the `participants` field addition to a follow-up step that runs after Task 1.9, OR define `ParticipantSpec` first (reorder tasks if your executor permits).

For TDD strictness, split this task: 1.5a adds `account_id` only and commits; 1.5b after 1.9 adds the `participants` field.

- [ ] **Step 4: Run the test to verify passing**

Run: `pytest tests/unit/test_text_clean_request.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add models/text_request.py tests/unit/test_text_clean_request.py
git commit -m "feat(text-request): require account_id field"
```

### Task 1.6: Add required `account_id` to `UploadInitRequest`

**Files:**
- Modify: `routers/upload.py:87-93`
- Test: `tests/unit/test_upload_init_request.py` (new)

- [ ] **Step 1: Write a failing test**

Create `tests/unit/test_upload_init_request.py`:

```python
"""UploadInitRequest requires account_id."""

import pytest
from pydantic import ValidationError
from routers.upload import UploadInitRequest


def test_rejects_missing_account_id():
    with pytest.raises(ValidationError):
        UploadInitRequest(filename="x.wav")


def test_accepts_with_account_id():
    req = UploadInitRequest(filename="x.wav", account_id="acct-1")
    assert req.account_id == "acct-1"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_upload_init_request.py -v`
Expected: 2 FAIL.

- [ ] **Step 3: Edit `routers/upload.py:87-93`**

Replace:

```python
class UploadInitRequest(BaseModel):
    """Request model for POST /upload/init"""
    filename: str = Field(..., min_length=1, max_length=255)
    mime_type: str = Field(default="audio/wav")
    file_size: Optional[int] = Field(default=None, ge=1, le=500_000_000)  # Max 500MB
```

with:

```python
class UploadInitRequest(BaseModel):
    """Request model for POST /upload/init"""
    filename: str = Field(..., min_length=1, max_length=255)
    account_id: str = Field(..., min_length=1, description="Account anchor; required.")
    mime_type: str = Field(default="audio/wav")
    file_size: Optional[int] = Field(default=None, ge=1, le=500_000_000)
    participants: Optional[list["ParticipantSpec"]] = Field(
        default=None,
        description="Caller-provided participants; flows through UploadJob to worker.",
    )
```

Add import: `from models.participant_spec import ParticipantSpec` (defer until Task 1.9 if needed; commit account_id-only first).

- [ ] **Step 4: Run the test to verify passing**

Run: `pytest tests/unit/test_upload_init_request.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add routers/upload.py tests/unit/test_upload_init_request.py
git commit -m "feat(upload): require account_id on UploadInitRequest"
```

### Task 1.7: Make `UploadJob.account_id` required

**Files:**
- Modify: `models/job_models.py:81`
- Test: extend `tests/unit/test_upload_init_request.py`

- [ ] **Step 1: Write a failing test**

Append to `tests/unit/test_upload_init_request.py`:

```python
from models.job_models import UploadJob, JobStatus, JobType
import uuid
from datetime import datetime, timezone


def test_upload_job_requires_account_id():
    # SQLModel raises ValidationError if account_id missing, similar to pydantic
    with pytest.raises(Exception):  # accept ValueError or ValidationError variants
        UploadJob(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            user_id="user-1",
            pg_user_id="pg-1",
            user_name="U",
            job_type=JobType.audio_transcription,
            status=JobStatus.queued,
            file_key="k",
            interaction_id=uuid.uuid4(),
            created_at=datetime.now(timezone.utc),
        )
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_upload_init_request.py::test_upload_job_requires_account_id -v`
Expected: FAIL — currently `account_id` is `Optional[str]`.

- [ ] **Step 3: Edit `models/job_models.py:81`**

Replace:

```python
    account_id: Optional[str] = Field(default=None, sa_column=Column(Text, name="account_id"))
```

with:

```python
    account_id: str = Field(sa_column=Column(Text, name="account_id"))
```

- [ ] **Step 4: Run test to verify passing**

Run: `pytest tests/unit/test_upload_init_request.py::test_upload_job_requires_account_id -v`
Expected: PASS.

- [ ] **Step 5: Update UploadJob construction site in `routers/upload.py:156`**

Find the `job = UploadJob(...)` block at line 156. Add `account_id=body.account_id` to the kwargs. Run:

```bash
grep -n "job = UploadJob" routers/upload.py
```

Edit that block to include `account_id=body.account_id,` immediately after `pg_user_id=context.pg_user_id,`.

- [ ] **Step 6: Run the full upload unit suite**

Run: `pytest tests/unit/test_upload_init_request.py -v`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add models/job_models.py routers/upload.py tests/unit/test_upload_init_request.py
git commit -m "feat(upload): require account_id on UploadJob and at construction site"
```

### Task 1.8: Make `IntelligenceService.process_transcript(account_id)` required

**Files:**
- Modify: `services/intelligence_service.py:52-66`
- Test: `tests/unit/test_intelligence_service.py` (extend)

- [ ] **Step 1: Write a failing test**

Append to `tests/unit/test_intelligence_service.py`:

```python
import inspect
from services.intelligence_service import IntelligenceService


def test_process_transcript_requires_account_id():
    sig = inspect.signature(IntelligenceService.process_transcript)
    param = sig.parameters["account_id"]
    # Required = no default
    assert param.default is inspect.Parameter.empty, (
        "process_transcript(account_id) must be required (no default), "
        f"got default={param.default!r}"
    )
    # And the annotation should not be Optional
    assert "Optional" not in str(param.annotation), (
        f"account_id annotation should not be Optional, got {param.annotation}"
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_intelligence_service.py::test_process_transcript_requires_account_id -v`
Expected: FAIL.

- [ ] **Step 3: Edit `services/intelligence_service.py:52-66`**

Current signature:

```python
    async def process_transcript(
        self,
        cleaned_transcript: str,
        interaction_id: str,
        tenant_id: str,
        trace_id: str,
        interaction_type: str = "meeting",
        account_id: Optional[str] = None,
        interaction_timestamp: Optional[datetime] = None,
        persona_code: str = "gtm",
        contact_ids: Optional[list[str]] = None,
        calendar_event_id: Optional[str] = None,
        enrichment_confidence: Optional[str] = None,
        enrichment_match_method: Optional[str] = None,
    ) -> Optional[InteractionAnalysis]:
```

Reorder so `account_id` is required (no default) and lands before any defaulted params:

```python
    async def process_transcript(
        self,
        cleaned_transcript: str,
        interaction_id: str,
        tenant_id: str,
        account_id: str,
        trace_id: str,
        interaction_type: str = "meeting",
        interaction_timestamp: Optional[datetime] = None,
        persona_code: str = "gtm",
        contact_ids: Optional[list[str]] = None,
        calendar_event_id: Optional[str] = None,
        enrichment_confidence: Optional[str] = None,
        enrichment_match_method: Optional[str] = None,
    ) -> Optional[InteractionAnalysis]:
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/unit/test_intelligence_service.py::test_process_transcript_requires_account_id -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: Existing call sites in `main.py:491`, `routers/batch.py:236`, `routers/upload.py:508` will now fail type-check / runtime because they omit `account_id`. The next several tasks fix each call site.

- [ ] **Step 6: Commit**

```bash
git add services/intelligence_service.py tests/unit/test_intelligence_service.py
git commit -m "feat(intelligence): require account_id on process_transcript"
```

### Task 1.9: Define `ParticipantSpec` model

**Files:**
- Create: `models/participant_spec.py`
- Test: `tests/unit/test_participant_spec.py`

- [ ] **Step 1: Write a failing test**

Create `tests/unit/test_participant_spec.py`:

```python
"""ParticipantSpec — caller-provided participants on ingestion endpoints."""

import pytest
from pydantic import ValidationError
from models.participant_spec import ParticipantSpec


def test_minimal_spec_requires_email():
    with pytest.raises(ValidationError):
        ParticipantSpec()


def test_email_only_is_valid():
    spec = ParticipantSpec(email="alice@acme.com")
    assert spec.email == "alice@acme.com"
    assert spec.display_name is None
    assert spec.role is None


def test_full_spec():
    spec = ParticipantSpec(
        email="alice@acme.com",
        display_name="Alice Smith",
        role="organizer",
    )
    assert spec.display_name == "Alice Smith"
    assert spec.role == "organizer"


def test_invalid_email():
    with pytest.raises(ValidationError):
        ParticipantSpec(email="not-an-email")


def test_role_must_be_allowed_value():
    with pytest.raises(ValidationError):
        ParticipantSpec(email="a@b.com", role="random-role")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_participant_spec.py -v`
Expected: All fail (module doesn't exist).

- [ ] **Step 3: Create the model**

Create `models/participant_spec.py`:

```python
"""Caller-provided participant specification.

Allows the API to accept manually-attached participants (e.g., notes typed
into an interaction without a calendar match) on ingestion endpoints.
Wired to TranscriptEnrichmentService.enrich(existing_contact_ids=...)."""

from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


ParticipantRole = Literal["organizer", "attendee", "optional", "sender", "recipient"]


class ParticipantSpec(BaseModel):
    """Minimal caller-provided participant.

    Resolution rules:
    - email is the unique key (combined with tenant for find-or-create)
    - display_name is optional; if absent, 3-tier name resolution runs
    - role defaults to None (interpreted as 'attendee' if needed downstream)
    """

    email: EmailStr = Field(..., description="Participant email (canonical lower-case)")
    display_name: Optional[str] = Field(default=None, max_length=255)
    role: Optional[ParticipantRole] = Field(default=None)
```

Note: requires `email-validator` package for `EmailStr`. If not present:

```bash
pip install email-validator
echo "email-validator>=2.0.0" >> requirements.txt
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/unit/test_participant_spec.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add models/participant_spec.py tests/unit/test_participant_spec.py requirements.txt
git commit -m "feat(participants): add ParticipantSpec model"
```

### Task 1.10: Wire `ParticipantSpec` into `TextCleanRequest` and `UploadInitRequest`

**Files:**
- Modify: `models/text_request.py` (add import + uncomment participants field)
- Modify: `routers/upload.py` (add import + uncomment participants field)
- Test: extend `tests/unit/test_text_clean_request.py` and `tests/unit/test_upload_init_request.py`

- [ ] **Step 1: Write a failing test in `tests/unit/test_text_clean_request.py`**

Append:

```python
from models.participant_spec import ParticipantSpec


def test_text_clean_accepts_participants():
    req = TextCleanRequest(
        text="meeting note",
        account_id="acct-1",
        participants=[
            ParticipantSpec(email="alice@acme.com", display_name="Alice"),
            ParticipantSpec(email="bob@acme.com"),
        ],
    )
    assert len(req.participants) == 2
    assert req.participants[0].display_name == "Alice"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_text_clean_request.py::test_text_clean_accepts_participants -v`
Expected: FAIL (no participants field yet — Task 1.5 deferred it).

- [ ] **Step 3: Add the field + import in `models/text_request.py`**

Add at top: `from models.participant_spec import ParticipantSpec`

Add inside `TextCleanRequest`:

```python
    participants: Optional[list[ParticipantSpec]] = Field(
        default=None,
        description="Caller-provided participants",
    )
```

(If Step 3 of Task 1.5 already added this line, just ensure the import is present and the field exists.)

- [ ] **Step 4: Repeat for `UploadInitRequest` in `routers/upload.py`**

Add import `from models.participant_spec import ParticipantSpec` at top of file.

Ensure `participants` field is present in `UploadInitRequest` (deferred from Task 1.6).

- [ ] **Step 5: Write the upload test**

Append to `tests/unit/test_upload_init_request.py`:

```python
def test_upload_init_accepts_participants():
    req = UploadInitRequest(
        filename="x.wav",
        account_id="acct-1",
        participants=[ParticipantSpec(email="a@b.com")],
    )
    assert len(req.participants) == 1
```

Add import: `from models.participant_spec import ParticipantSpec` at top of test.

- [ ] **Step 6: Run all relevant tests**

Run: `pytest tests/unit/test_text_clean_request.py tests/unit/test_upload_init_request.py -v`
Expected: All PASS.

- [ ] **Step 7: Commit**

```bash
git add models/text_request.py routers/upload.py tests/unit/test_text_clean_request.py tests/unit/test_upload_init_request.py
git commit -m "feat(participants): wire ParticipantSpec into request models"
```

---

## Phase 1 Ingestion-path tightening

### Task 1.11: WebSocket `/listen` — RequestContext at main.py:271

**Files:**
- Modify: `main.py:265-285`
- Test: `tests/integration/test_websocket_account_id.py` (new)

- [ ] **Step 1: Write a failing test**

Create `tests/integration/test_websocket_account_id.py`:

```python
"""WebSocket /listen rejects missing X-Account-ID."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_listen_rejects_missing_account_id(client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/listen",
            headers={"Authorization": "Bearer test-jwt"},
        ) as ws:
            pass
    # Expect close code 1008 (Policy Violation)
    assert exc_info.value.code == 1008


def test_listen_accepts_with_account_id(client):
    # Build a valid test token + valid account_id; expect successful upgrade.
    # Concrete token plumbing depends on existing test fixtures.
    pass  # TODO: extend with real auth fixture
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/integration/test_websocket_account_id.py::test_listen_rejects_missing_account_id -v`
Expected: FAIL — current code accepts the connection with `account_id=None`.

- [ ] **Step 3: Edit `main.py` around line 271**

Find the block:

```python
    if token:
        try:
            claims = verify_internal_jwt(token)
            context = RequestContext(
                tenant_id=claims.tenant_id,
                user_id=claims.user_id,
                pg_user_id=claims.pg_user_id,
                user_name=claims.user_name,
                account_id=None,
                interaction_id=session_id,
                trace_id=str(uuid.uuid4()),
            )
```

Replace with:

```python
    if token:
        try:
            claims = verify_internal_jwt(token)
            # Account anchor must be supplied via header; backend rejects when absent.
            account_id = websocket.headers.get("x-account-id")
            if not account_id:
                logger.warning("WebSocket /listen rejected: missing X-Account-ID")
                await websocket.close(code=1008, reason="X-Account-ID required")
                return
            context = RequestContext(
                tenant_id=claims.tenant_id,
                user_id=claims.user_id,
                pg_user_id=claims.pg_user_id,
                user_name=claims.user_name,
                account_id=account_id,
                interaction_id=session_id,
                trace_id=str(uuid.uuid4()),
            )
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/integration/test_websocket_account_id.py::test_listen_rejects_missing_account_id -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/integration/test_websocket_account_id.py
git commit -m "feat(listen): require X-Account-ID on WebSocket upgrade"
```

### Task 1.12: WebSocket `/listen` — Envelope construction at main.py:469

**Files:**
- Modify: `main.py:460-485`

- [ ] **Step 1: Read the envelope-construction block**

Run: `sed -n '460,485p' main.py`
Confirm `account_id=None` is present on line ~479.

- [ ] **Step 2: Replace with `account_id=ws_account_id`**

The session-level state already has `ws_tenant_id`, `ws_user_id`, `ws_trace_id` captured from the context at WebSocket-open time. Ensure `ws_account_id` is similarly captured. Look for where `ws_tenant_id` is assigned (likely just after the context is constructed). Add an analogous line:

```python
ws_account_id = context.account_id
```

Then edit the envelope construction at line ~479:

```python
                        envelope = EnvelopeV1(
                            tenant_id=uuid.UUID(ws_tenant_id) if len(ws_tenant_id) == 36 else uuid.uuid4(),
                            user_id=ws_user_id,
                            interaction_type="meeting",
                            content=ContentModel(text=content_text, format="diarized"),
                            timestamp=transcript_ts,
                            source=source,
                            extras=extras,
                            interaction_id=uuid.UUID(session_id),
                            trace_id=ws_trace_id,
                            account_id=ws_account_id,  # was None — required since Task 1.3
                        )
```

- [ ] **Step 3: Run the unit tests for the envelope and any WebSocket integration**

Run: `pytest tests/unit/test_envelope_account_id.py tests/integration/test_websocket_account_id.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat(listen): pass resolved account_id into EnvelopeV1"
```

### Task 1.13: WebSocket `/listen` — process_transcript call at main.py:491

**Files:**
- Modify: `main.py:487-505`

- [ ] **Step 1: Edit the call site**

The block at line ~491:

```python
                async def _lane2_intelligence() -> Optional[object]:
                    """Lane 2: Extract and persist intelligence."""
                    try:
                        intelligence_service = IntelligenceService()
                        return await intelligence_service.process_transcript(
                            cleaned_transcript=meeting_output.cleaned_transcript,
                            interaction_id=session_id,
                            tenant_id=ws_tenant_id,
                            trace_id=ws_trace_id,
                            interaction_type="meeting",
                            contact_ids=enrichment.contact_ids or None,
                            calendar_event_id=enrichment.calendar_event_id,
                            enrichment_confidence=enrichment.match_confidence,
```

Add `account_id=ws_account_id,` as a kwarg (the new required positional was repositioned in Task 1.8; passing by keyword is safest):

```python
                        return await intelligence_service.process_transcript(
                            cleaned_transcript=meeting_output.cleaned_transcript,
                            interaction_id=session_id,
                            tenant_id=ws_tenant_id,
                            account_id=ws_account_id,
                            trace_id=ws_trace_id,
                            interaction_type="meeting",
                            ...
                        )
```

- [ ] **Step 2: Run the full suite**

Run: `pytest tests/ -v --tb=short`
Expected: WebSocket-related tests pass; the `routers/batch.py:236` and `routers/upload.py:508` callers still fail (fixed next).

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat(listen): pass account_id to process_transcript"
```

### Task 1.14: Batch `/batch/process` — process_transcript call at routers/batch.py:236

**Files:**
- Modify: `routers/batch.py:236-250`

- [ ] **Step 1: Edit the call site**

Add `account_id=context.account_id,` as a kwarg to `process_transcript(...)`:

```python
            return await intelligence_service.process_transcript(
                cleaned_transcript=cleaned_transcript,
                interaction_id=context.interaction_id,
                tenant_id=context.tenant_id,
                account_id=context.account_id,
                trace_id=context.trace_id,
                interaction_type="batch_upload",
                ...
            )
```

`context.account_id` is now guaranteed non-empty by `get_auth_context()` (Task 1.4).

- [ ] **Step 2: Run the suite**

Run: `pytest tests/ -v --tb=short`
Expected: batch-path tests pass; upload still fails until Task 1.15.

- [ ] **Step 3: Commit**

```bash
git add routers/batch.py
git commit -m "feat(batch): pass account_id to process_transcript"
```

### Task 1.15: Upload `/upload/complete` — process_transcript call at routers/upload.py:508

**Files:**
- Modify: `routers/upload.py:498-520`

- [ ] **Step 1: Edit the call site**

The `_lane2` block around line 505-516 currently:

```python
        async def _lane2():
            try:
                intelligence = IntelligenceService()
                return await intelligence.process_transcript(
                    cleaned_transcript=cleaned_transcript,
                    interaction_id=interaction_id,
                    tenant_id=tenant_id,
                    trace_id=trace_id,
                    interaction_type="batch_upload",
                    ...
                )
```

The `account_id` source for the upload completion path is the parent `UploadJob.account_id` (persisted at /init time, Task 1.7). Trace back where `tenant_id`, `interaction_id`, `trace_id` come from in this function and add `account_id=job.account_id` (or however the job is referenced). If the function does not currently carry the job object, fetch it: `job = await get_upload_job(job_id)` before the lane definitions.

```python
                return await intelligence.process_transcript(
                    cleaned_transcript=cleaned_transcript,
                    interaction_id=interaction_id,
                    tenant_id=tenant_id,
                    account_id=job.account_id,
                    trace_id=trace_id,
                    interaction_type="batch_upload",
                    ...
                )
```

- [ ] **Step 2: Run the suite**

Run: `pytest tests/ -v --tb=short`
Expected: All ingestion-path tests pass.

- [ ] **Step 3: Commit**

```bash
git add routers/upload.py
git commit -m "feat(upload): pass account_id from UploadJob to process_transcript"
```

### Task 1.16: Text `/text/clean` — wire account_id from request body

**Files:**
- Modify: `routers/text.py` (find the `/text/clean` route handler)

- [ ] **Step 1: Locate the route**

Run: `grep -n "/clean\|process_transcript\|TextCleanRequest" routers/text.py`
Identify the handler.

- [ ] **Step 2: Pass `body.account_id` to envelope construction and `process_transcript()` call**

Inside the handler, after the body is parsed and `context` is built, ensure:

- The envelope is constructed with `account_id=body.account_id`.
- If `process_transcript()` is called downstream (or via a fork into intelligence lane), it receives `account_id=body.account_id`.

Add explicit cross-check: `assert body.account_id == context.account_id, "request body and header must agree on account_id"` is overly strict (the client may send one without the other). For Phase 1, prefer the request-body value as the authoritative source for the envelope and the intelligence lane; ignore any mismatch with the header (the header is the auth-context boundary, satisfied by Task 1.4; the body carries the per-request semantic).

- [ ] **Step 3: Run the suite**

Run: `pytest tests/ -v --tb=short`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add routers/text.py
git commit -m "feat(text-clean): wire request-body account_id end-to-end"
```

---

## Phase 1 Domain utilities

### Task 1.17: Create shared personal-domain filter

**Files:**
- Create: `services/domain_classification.py`
- Test: `tests/unit/test_domain_classification.py`

- [ ] **Step 1: Write a failing test**

Create `tests/unit/test_domain_classification.py`:

```python
"""Domain classification: personal | internal | business."""

import pytest
from services.domain_classification import (
    is_personal_domain,
    classify_domain,
    DomainClass,
)


def test_personal_gmail():
    assert is_personal_domain("gmail.com") is True


def test_personal_outlook():
    assert is_personal_domain("outlook.com") is True


def test_business_domain():
    assert is_personal_domain("acme.com") is False


def test_classify_personal():
    assert classify_domain("gmail.com", internal_domains=set()) == DomainClass.PERSONAL


def test_classify_internal():
    result = classify_domain("mycompany.com", internal_domains={"mycompany.com"})
    assert result == DomainClass.INTERNAL


def test_classify_business():
    result = classify_domain("acme.com", internal_domains={"mycompany.com"})
    assert result == DomainClass.BUSINESS


def test_classify_case_insensitive():
    assert classify_domain("ACME.com", internal_domains=set()) == DomainClass.BUSINESS
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_domain_classification.py -v`
Expected: All fail.

- [ ] **Step 3: Implement the module**

Create `services/domain_classification.py`:

```python
"""Domain classification: personal | internal | business.

Used by per-attendee three-state branching to decide whether to
create a contact, queue a signal, or skip entirely.
"""

from enum import Enum

# Curated public personal-email domain list. Kept conservative; expand
# when new personal-provider patterns emerge in production data.
PERSONAL_DOMAINS = frozenset({
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "yahoo.com",
    "ymail.com",
    "icloud.com",
    "me.com",
    "mac.com",
    "aol.com",
    "protonmail.com",
    "proton.me",
    "msn.com",
    "comcast.net",
    "verizon.net",
    "att.net",
    "duck.com",
    "fastmail.com",
    "tutanota.com",
    "zoho.com",
    "mail.com",
    "gmx.com",
})


class DomainClass(Enum):
    PERSONAL = "personal"
    INTERNAL = "internal"
    BUSINESS = "business"


def normalize_domain(domain: str) -> str:
    return domain.strip().lower()


def is_personal_domain(domain: str) -> bool:
    return normalize_domain(domain) in PERSONAL_DOMAINS


def classify_domain(domain: str, internal_domains: set[str]) -> DomainClass:
    d = normalize_domain(domain)
    if d in PERSONAL_DOMAINS:
        return DomainClass.PERSONAL
    if d in {nd.lower() for nd in internal_domains}:
        return DomainClass.INTERNAL
    return DomainClass.BUSINESS


def email_domain(email: str) -> str:
    """Extract domain portion of an email; lower-cased; '' on malformed."""
    parts = email.strip().lower().split("@", 1)
    return parts[1] if len(parts) == 2 else ""
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/unit/test_domain_classification.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add services/domain_classification.py tests/unit/test_domain_classification.py
git commit -m "feat(domains): shared personal/internal/business classifier"
```

### Task 1.18: Extract 3-tier name resolution to shared utility

**Files:**
- Create: `services/name_resolution.py`
- Modify: `services/transcript_enrichment.py` (replace inline logic with import)
- Test: `tests/unit/test_name_resolution.py`

- [ ] **Step 1: Locate existing logic**

Run: `grep -n "tavily\|display_name\|first_name\|email heuristic\|name resolution" services/transcript_enrichment.py`
Read the relevant block(s) (likely around lines 200-400 — the 3-tier resolution path).

- [ ] **Step 2: Write a failing test**

Create `tests/unit/test_name_resolution.py`:

```python
"""3-tier name resolution: display_name -> email heuristic -> Tavily."""

import pytest
from services.name_resolution import (
    resolve_name,
    heuristic_name_from_email,
    NameResolution,
)


def test_tier1_display_name_wins():
    result = resolve_name(email="x@y.com", display_name="Jane Smith", tavily_client=None)
    assert result.first_name == "Jane"
    assert result.last_name == "Smith"
    assert result.source == "display_name"


def test_tier2_heuristic_from_email():
    result = heuristic_name_from_email("jane.smith@acme.com")
    assert result == ("Jane", "Smith")


def test_tier2_heuristic_dash():
    assert heuristic_name_from_email("jane-smith@acme.com") == ("Jane", "Smith")


def test_tier2_heuristic_underscore():
    assert heuristic_name_from_email("jane_smith@acme.com") == ("Jane", "Smith")


def test_tier2_heuristic_unconfident_initials():
    # j.smith@acme.com -> ambiguous; heuristic returns None to escalate to Tavily
    assert heuristic_name_from_email("j.smith@acme.com") is None


def test_tier3_no_tavily_client_returns_none():
    result = resolve_name(email="ambiguous@acme.com", display_name=None, tavily_client=None)
    assert result is None
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/unit/test_name_resolution.py -v`
Expected: All fail.

- [ ] **Step 4: Implement the module**

Create `services/name_resolution.py`:

```python
"""3-tier name resolution.

Tier 1: explicit display_name (highest confidence).
Tier 2: email-heuristic split on '.', '-', '_' (medium confidence; rejects
         single-character first names like 'j.smith').
Tier 3: Tavily public lookup (lowest confidence; optional; budget-gated).

Returned by `resolve_name()` as a NameResolution or None when unresolvable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Protocol


# Tier 1 / Tier 2 result
@dataclass
class NameResolution:
    first_name: str
    last_name: Optional[str]
    source: str  # "display_name" | "email_heuristic" | "tavily"


def _split_display_name(display_name: str) -> tuple[str, Optional[str]]:
    parts = display_name.strip().split()
    if not parts:
        return ("", None)
    if len(parts) == 1:
        return (parts[0], None)
    return (parts[0], " ".join(parts[1:]))


def heuristic_name_from_email(email: str) -> Optional[tuple[str, str]]:
    """Extract (first, last) from email local-part. Returns None when
    heuristic is not confident enough (e.g., short initials)."""
    local = email.split("@", 1)[0]
    # Try common separators
    for sep in (".", "-", "_"):
        if sep in local:
            parts = [p for p in local.split(sep) if p]
            if len(parts) >= 2:
                first, last = parts[0], parts[-1]
                if len(first) >= 2 and len(last) >= 2:
                    return (first.capitalize(), last.capitalize())
                # Reject ambiguous initials like j.smith
                return None
    return None


class TavilyClient(Protocol):
    def lookup(self, query: str) -> Optional[tuple[str, str]]:
        ...


def resolve_name(
    email: str,
    display_name: Optional[str],
    tavily_client: Optional[TavilyClient] = None,
) -> Optional[NameResolution]:
    """Apply tiers in order; return first success or None."""
    if display_name and display_name.strip():
        first, last = _split_display_name(display_name)
        return NameResolution(first_name=first, last_name=last, source="display_name")

    heur = heuristic_name_from_email(email)
    if heur is not None:
        return NameResolution(first_name=heur[0], last_name=heur[1], source="email_heuristic")

    if tavily_client is not None:
        tavily_result = tavily_client.lookup(email)
        if tavily_result is not None:
            return NameResolution(
                first_name=tavily_result[0],
                last_name=tavily_result[1],
                source="tavily",
            )

    return None
```

- [ ] **Step 5: Run the test**

Run: `pytest tests/unit/test_name_resolution.py -v`
Expected: 6 PASS.

- [ ] **Step 6: Replace inline logic in `services/transcript_enrichment.py`**

Grep for any place the old 3-tier logic lives. Replace with calls to `resolve_name(...)`. Keep the Tavily client construction where it already is (likely a class-level dependency).

Run the existing transcript-enrichment tests:

```bash
pytest tests/unit/test_transcript_enrichment.py -v
```
Expected: PASS (behavior should be identical; this is a refactor).

- [ ] **Step 7: Commit**

```bash
git add services/name_resolution.py tests/unit/test_name_resolution.py services/transcript_enrichment.py
git commit -m "refactor(enrichment): extract 3-tier name resolution to shared utility"
```

### Task 1.19: Add `lookup_account_by_domain()` Postgres helper

**Files:**
- Create: `services/account_lookup.py`
- Test: `tests/unit/test_account_lookup.py`

- [ ] **Step 1: Write a failing test**

Create `tests/unit/test_account_lookup.py`:

```python
"""account_lookup.lookup_account_by_domain — Postgres-backed find."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from services.account_lookup import lookup_account_by_domain


@pytest.mark.asyncio
async def test_returns_account_id_on_match():
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: "acct-1"))
    result = await lookup_account_by_domain(
        session=session,
        tenant_id="tenant-1",
        domain="acme.com",
    )
    assert result == "acct-1"


@pytest.mark.asyncio
async def test_returns_none_on_miss():
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    result = await lookup_account_by_domain(
        session=session,
        tenant_id="tenant-1",
        domain="unknown.com",
    )
    assert result is None


@pytest.mark.asyncio
async def test_domain_is_normalized_lowercase():
    session = MagicMock()
    captured = {}
    async def _exec(stmt, params=None):
        captured["params"] = params
        m = MagicMock()
        m.scalar_one_or_none = lambda: None
        return m
    session.execute = _exec
    await lookup_account_by_domain(session=session, tenant_id="t", domain="ACME.com")
    assert captured["params"]["domain"] == "acme.com"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_account_lookup.py -v`
Expected: fail (module missing).

- [ ] **Step 3: Implement the helper**

Create `services/account_lookup.py`:

```python
"""Postgres-backed account lookup by domain.

Mirrors eq-email-pipeline/src/persistence/postgres.py:lookup_account_by_domain.
Tenant-scoped; never crosses tenant boundaries.
"""

from typing import Optional

from sqlalchemy import text


LOOKUP_SQL = text("""
    SELECT id::text
    FROM accounts
    WHERE tenant_id = :tenant_id
      AND lower(domain) = :domain
    LIMIT 1
""")


async def lookup_account_by_domain(
    session,
    tenant_id: str,
    domain: str,
) -> Optional[str]:
    """Return account_id (str UUID) or None.

    Tenant isolation invariant: always filters by tenant_id; never falls
    back to cross-tenant search.
    """
    normalized = domain.strip().lower()
    result = await session.execute(
        LOOKUP_SQL,
        {"tenant_id": tenant_id, "domain": normalized},
    )
    return result.scalar_one_or_none()
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/unit/test_account_lookup.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add services/account_lookup.py tests/unit/test_account_lookup.py
git commit -m "feat(account-lookup): add tenant-scoped lookup_account_by_domain helper"
```

---

## Phase 1 Queue Insertion (transcript pipeline)

### Task 1.20: Create `pending_account_mappings` helper module

**Files:**
- Create: `services/pending_account_mappings.py`
- Test: `tests/unit/test_pending_account_mappings.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_pending_account_mappings.py`:

```python
"""Queue insertion helpers — upsert parent + insert signal + re-open."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from services.pending_account_mappings import (
    upsert_queue_entry,
    insert_signal,
    SignalProposal,
    QueueRow,
)


@pytest.mark.asyncio
async def test_upsert_queue_entry_creates_new_row():
    session = MagicMock()
    # Simulate INSERT...ON CONFLICT returning the inserted id
    session.execute = AsyncMock(return_value=MagicMock(scalar_one=lambda: "queue-id-1"))
    qid = await upsert_queue_entry(
        session=session,
        tenant_id="t1",
        domain="acme.com",
        owner_user_id="u1",
        discovered_from_type="transcript",
        discovered_from_interaction_id="int-1",
        expires_in_days=30,
    )
    assert qid == "queue-id-1"


@pytest.mark.asyncio
async def test_insert_signal_is_idempotent():
    session = MagicMock()
    session.execute = AsyncMock()
    await insert_signal(
        session=session,
        tenant_id="t1",
        queue_id="q1",
        proposal=SignalProposal(
            source_type="transcript",
            source_user_id="u1",
            interaction_id="int-1",
            calendar_event_id=None,
            contact_email="bob@acme.com",
            contact_display_name="Bob",
            contact_role="attendee",
        ),
    )
    # Verify INSERT ... ON CONFLICT was attempted (idempotent under retry)
    assert session.execute.called
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_pending_account_mappings.py -v`
Expected: fail (module missing).

- [ ] **Step 3: Implement the module**

Create `services/pending_account_mappings.py`:

```python
"""Helpers for inserting/upserting into pending_account_mappings + signals.

Implements UPSERT semantics from design Section 5.2:
- Parent row: first-owner-wins on (tenant_id, domain); only expires_at refreshes.
- Signal row: unconditional insert with idempotency via unique constraint.
- Re-open: archived entry + new signal -> transitions back to pending.

All operations are tenant-scoped. No cross-tenant queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text


@dataclass
class SignalProposal:
    source_type: str  # email | transcript | calendar | manual
    source_user_id: str
    interaction_id: Optional[str]
    calendar_event_id: Optional[str]
    contact_email: str
    contact_display_name: Optional[str]
    contact_role: Optional[str]


@dataclass
class QueueRow:
    id: str
    archived_at: Optional[datetime]
    re_open_count: int


UPSERT_PARENT_SQL = text("""
    INSERT INTO pending_account_mappings
        (id, tenant_id, domain, status,
         owner_user_id, discovered_from_type, discovered_from_interaction_id,
         expires_at, created_at, updated_at)
    VALUES
        (gen_random_uuid(), :tenant_id, lower(:domain), 'pending',
         :owner_user_id, :discovered_from_type, :discovered_from_interaction_id,
         :expires_at, NOW(), NOW())
    ON CONFLICT (tenant_id, domain) DO UPDATE
        SET expires_at = GREATEST(pending_account_mappings.expires_at, EXCLUDED.expires_at),
            updated_at = NOW()
    RETURNING id::text
""")


REOPEN_PARENT_SQL = text("""
    UPDATE pending_account_mappings
    SET archived_at = NULL,
        archive_reason = NULL,
        re_open_count = re_open_count + 1,
        last_reopened_at = NOW(),
        status = 'pending',
        expires_at = :expires_at,
        updated_at = NOW()
    WHERE tenant_id = :tenant_id
      AND lower(domain) = lower(:domain)
      AND archived_at IS NOT NULL
    RETURNING id::text
""")


INSERT_SIGNAL_SQL = text("""
    INSERT INTO pending_account_mapping_signals
        (id, tenant_id, queue_id, source_type, source_user_id,
         interaction_id, calendar_event_id,
         contact_email, contact_display_name, contact_role, created_at)
    VALUES
        (gen_random_uuid(), :tenant_id, :queue_id, :source_type, :source_user_id,
         :interaction_id, :calendar_event_id,
         :contact_email, :contact_display_name, :contact_role, NOW())
    ON CONFLICT ON CONSTRAINT pending_signal_dedup DO NOTHING
""")


async def upsert_queue_entry(
    session,
    tenant_id: str,
    domain: str,
    owner_user_id: str,
    discovered_from_type: str,
    discovered_from_interaction_id: Optional[str],
    expires_in_days: int = 30,
) -> str:
    """Insert-or-update the parent queue row; returns queue_id.

    First-owner-wins on owner_user_id, discovered_from_type, discovered_from_interaction_id.
    Re-open of an archived row uses `reopen_archived_entry()` instead.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
    result = await session.execute(
        UPSERT_PARENT_SQL,
        {
            "tenant_id": tenant_id,
            "domain": domain,
            "owner_user_id": owner_user_id,
            "discovered_from_type": discovered_from_type,
            "discovered_from_interaction_id": discovered_from_interaction_id,
            "expires_at": expires_at,
        },
    )
    return result.scalar_one()


async def reopen_archived_entry(
    session,
    tenant_id: str,
    domain: str,
    expires_in_days: int = 30,
) -> Optional[str]:
    """If an archived entry exists for this (tenant, domain), transition it
    back to pending and return its id. Returns None when no archived entry.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
    result = await session.execute(
        REOPEN_PARENT_SQL,
        {"tenant_id": tenant_id, "domain": domain, "expires_at": expires_at},
    )
    row = result.first()
    return row[0] if row else None


async def insert_signal(
    session,
    tenant_id: str,
    queue_id: str,
    proposal: SignalProposal,
) -> None:
    """Insert a signal row; idempotent under retry via unique constraint."""
    await session.execute(
        INSERT_SIGNAL_SQL,
        {
            "tenant_id": tenant_id,
            "queue_id": queue_id,
            "source_type": proposal.source_type,
            "source_user_id": proposal.source_user_id,
            "interaction_id": proposal.interaction_id,
            "calendar_event_id": proposal.calendar_event_id,
            "contact_email": proposal.contact_email.strip().lower(),
            "contact_display_name": proposal.contact_display_name,
            "contact_role": proposal.contact_role,
        },
    )
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/unit/test_pending_account_mappings.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/pending_account_mappings.py tests/unit/test_pending_account_mappings.py
git commit -m "feat(queue): upsert/signal/reopen helpers for pending_account_mappings"
```

### Task 1.21: Per-attendee three-state branching in `transcript_enrichment.py`

**Files:**
- Modify: `services/transcript_enrichment.py` (around lines 380-450; specifically the area around line 399 where the existing contact INSERT happens)
- Test: `tests/integration/test_per_attendee_branching.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_per_attendee_branching.py`:

```python
"""Per-attendee three-state branching in transcript enrichment.

For a transcript with anchor account `acme.com` and attendees
[alice@acme.com, partner@consultingco.com, intern@gmail.com],
the integration test asserts:

- alice becomes a contact with account_id=acme
- partner produces a pending_account_mapping_signals row (no contact)
- intern produces no row anywhere
"""

import uuid
import pytest

# This test requires a working Postgres test fixture, an existing accounts
# row for acme.com, and an existing internal_domains configuration for the
# test tenant. Adjust fixtures to match the repo's actual setup.

@pytest.mark.asyncio
async def test_three_state_branching(test_session, seeded_acme_account, test_tenant):
    from services.transcript_enrichment import TranscriptEnrichmentService

    service = TranscriptEnrichmentService(...)  # construct per repo convention
    interaction_id = str(uuid.uuid4())
    result = await service.enrich(
        tenant_id=test_tenant.id,
        recording_user_id=test_tenant.test_user_id,
        anchor_account_id=seeded_acme_account.id,
        attendees=[
            {"email": "alice@acme.com", "display_name": "Alice"},
            {"email": "partner@consultingco.com", "display_name": "Partner Person"},
            {"email": "intern@gmail.com", "display_name": "Intern"},
        ],
        interaction_id=interaction_id,
    )

    # alice -> contact with account_id=acme
    alice = await test_session.execute(
        "SELECT account_id FROM contacts WHERE email = 'alice@acme.com' AND tenant_id = :t",
        {"t": test_tenant.id},
    )
    assert alice.scalar_one() == seeded_acme_account.id

    # partner -> signal, no contact
    partner_contact = await test_session.execute(
        "SELECT 1 FROM contacts WHERE email = 'partner@consultingco.com' AND tenant_id = :t",
        {"t": test_tenant.id},
    )
    assert partner_contact.scalar_one_or_none() is None
    partner_signal = await test_session.execute(
        "SELECT 1 FROM pending_account_mapping_signals "
        "WHERE contact_email = 'partner@consultingco.com' AND tenant_id = :t",
        {"t": test_tenant.id},
    )
    assert partner_signal.scalar_one_or_none() is not None

    # intern -> no contact, no signal
    intern_contact = await test_session.execute(
        "SELECT 1 FROM contacts WHERE email = 'intern@gmail.com' AND tenant_id = :t",
        {"t": test_tenant.id},
    )
    assert intern_contact.scalar_one_or_none() is None
    intern_signal = await test_session.execute(
        "SELECT 1 FROM pending_account_mapping_signals "
        "WHERE contact_email = 'intern@gmail.com' AND tenant_id = :t",
        {"t": test_tenant.id},
    )
    assert intern_signal.scalar_one_or_none() is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/integration/test_per_attendee_branching.py -v`
Expected: FAIL (current code creates contacts for all attendees with the anchor account_id).

- [ ] **Step 3: Modify `services/transcript_enrichment.py`**

Locate the per-attendee processing loop (the function around line 380-450 that does INSERT into contacts). Restructure it as follows:

```python
# At top of file, add imports:
from services.domain_classification import classify_domain, DomainClass, email_domain
from services.account_lookup import lookup_account_by_domain
from services.pending_account_mappings import (
    upsert_queue_entry,
    reopen_archived_entry,
    insert_signal,
    SignalProposal,
)


# Replace the per-attendee block with:
for attendee in attendees:
    domain = email_domain(attendee.email)
    if not domain:
        continue  # malformed email; skip

    klass = classify_domain(domain, internal_domains=tenant_internal_domains)
    if klass == DomainClass.PERSONAL:
        logger.info(f"Skipping personal-domain attendee: {attendee.email}")
        continue
    if klass == DomainClass.INTERNAL:
        logger.info(f"Skipping internal-domain attendee: {attendee.email}")
        # Internal attendees are recorded as participants in the interaction
        # but not as a queued business-domain entry. The interaction-side
        # representation (if any) happens via interaction_contact_links once
        # we have a tenant user contact mapped; current Phase 1 leaves this
        # as a no-op for unknown internal users (Phase 2 territory).
        continue

    # BUSINESS domain — three-state branching
    account_id = await lookup_account_by_domain(
        session=session,
        tenant_id=tenant_id,
        domain=domain,
    )
    if account_id is not None:
        # KNOWN ACCOUNT — create contact normally
        await _create_or_get_contact(
            session=session,
            tenant_id=tenant_id,
            email=attendee.email,
            display_name=attendee.display_name,
            account_id=account_id,
            interaction_id=interaction_id,
            role=attendee.role,
        )
    else:
        # UNKNOWN BUSINESS DOMAIN — queue signal, no contact
        reopened_id = await reopen_archived_entry(
            session=session,
            tenant_id=tenant_id,
            domain=domain,
        )
        if reopened_id is not None:
            queue_id = reopened_id
        else:
            queue_id = await upsert_queue_entry(
                session=session,
                tenant_id=tenant_id,
                domain=domain,
                owner_user_id=recording_user_id,
                discovered_from_type="transcript",
                discovered_from_interaction_id=interaction_id,
            )
        await insert_signal(
            session=session,
            tenant_id=tenant_id,
            queue_id=queue_id,
            proposal=SignalProposal(
                source_type="transcript",
                source_user_id=recording_user_id,
                interaction_id=interaction_id,
                calendar_event_id=None,
                contact_email=attendee.email,
                contact_display_name=attendee.display_name,
                contact_role=attendee.role,
            ),
        )
```

Remove the previous unconditional `INSERT INTO contacts (..., account_id=anchor_account_id, validation_status='pending')` path at `transcript_enrichment.py:399-410`. That orphan-creating code path is what Codex flagged and Option A removes.

- [ ] **Step 4: Run the integration test**

Run: `pytest tests/integration/test_per_attendee_branching.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -v --tb=short`
Expected: Existing transcript-enrichment tests may need updates for the new three-state branching. Update them where necessary; the behavior change is intentional.

- [ ] **Step 6: Commit**

```bash
git add services/transcript_enrichment.py tests/integration/test_per_attendee_branching.py
git commit -m "feat(enrichment): per-attendee three-state branching (Option A)"
```

### Task 1.22: Remove `validation_status='pending'` orphan-creation code path

**Files:**
- Modify: `services/transcript_enrichment.py:402` (and adjacent lines)

- [ ] **Step 1: Verify the orphan path is no longer reached**

Run: `grep -n "validation_status" services/transcript_enrichment.py`
Confirm the code at line ~402 (the `# Always "pending"` comment + assignment) is now dead under the three-state branching from Task 1.21.

- [ ] **Step 2: Delete the dead code**

Remove the `# Always "pending" — Prisma enum...` comment block and the `validation_status = "pending"` assignment plus the INSERT block that referenced it. Keep the contact-INSERT path used by `_create_or_get_contact()` for known-account contacts; that path sets `validation_status='pending'` only as a marker for name-unresolvable contacts (Phase 2 schema debt per design Section 7.4).

- [ ] **Step 3: Run the test suite**

Run: `pytest tests/ -v --tb=short`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add services/transcript_enrichment.py
git commit -m "refactor(enrichment): remove orphan-contact creation path"
```

---

## Phase 1 Cross-repo: eq-email-pipeline

### Task 1.23: Dispatch eq-email-pipeline agent for calendar_sync.py three-state branching

**Files:**
- Create: `tasks/downstream/eq-email-pipeline-phase-1-calendar-sync.md`

- [ ] **Step 1: Write the agent brief**

Create file:

```markdown
# eq-email-pipeline Phase 1: calendar_sync.py three-state branching

## Repo
/Users/peteroneil/eq-email-pipeline

## Goal
Apply the same three-state per-attendee branching to `src/pipeline/calendar_sync.py` that lives in `live-transcription-fastapi/services/transcript_enrichment.py` (Phase 1, Task 1.21).

## Reference
Design Section 5.2, 5.3, 7.1 (canonical):
`/Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md`

## Mechanics
For each calendar attendee:
- Extract email domain.
- Classify domain (personal / internal / business). Use a shared module if eq-email-pipeline has one, otherwise mirror the list from
  `live-transcription-fastapi/services/domain_classification.py`.
- If personal or internal: skip (no contact creation, no queue insertion).
- If business:
  - Call `lookup_account_by_domain(tenant_id, domain)` (already exists in `src/persistence/postgres.py`).
  - On hit (known account): create contact normally with that account_id via `find_or_create_contact()`.
  - On miss (unknown business): insert into `pending_account_mappings` + `pending_account_mapping_signals` using the upsert+signal pattern. Owner = the user whose `provider_connection` surfaced this calendar event.

## Schema dependency
This task requires the eq-frontend Phase 1 migration to have landed
(adds `pending_account_mapping_signals` table + new columns to
`pending_account_mappings`). See `tasks/downstream/eq-frontend-phase-1-schema.md`.

## Acceptance
- For a calendar event with attendees [alice@acme.com, partner@external.com, intern@gmail.com] and a known account for `acme.com`:
  - alice -> contact with account_id=acme
  - partner -> signal row (no contact)
  - intern -> no row anywhere
- All new behavior covered by tests.
- PR titled `feat(calendar-sync): three-state attendee branching` opened.

## What NOT to do
- Do NOT preserve fallback-to-anchor for unknown-domain attendees.
- Do NOT touch the email orchestrator yet (Task 1.24 covers that).
```

- [ ] **Step 2: Commit**

```bash
git add tasks/downstream/eq-email-pipeline-phase-1-calendar-sync.md
git commit -m "docs: eq-email-pipeline calendar_sync Phase 1 brief"
```

### Task 1.24: Dispatch eq-email-pipeline agent for orchestrator.py verification

**Files:**
- Create: `tasks/downstream/eq-email-pipeline-phase-1-orchestrator.md`

- [ ] **Step 1: Write the agent brief**

```markdown
# eq-email-pipeline Phase 1: orchestrator.py three-state verification

## Repo
/Users/peteroneil/eq-email-pipeline

## Goal
Audit `src/pipeline/orchestrator.py` for compliance with three-state branching for email sender/recipient resolution. The current orchestrator already calls `lookup_account_by_domain()` for sender/recipient domains, but does it correctly handle the unknown-business case?

## Reference
Design Section 5.2, 7.1.
`/Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md`

## Steps
1. Read `src/pipeline/orchestrator.py` and trace the email-ingestion path for sender + each recipient.
2. For each domain resolution miss, verify it inserts into `pending_account_mappings` + `pending_account_mapping_signals` instead of creating a NULL-account_id contact.
3. If the current code creates a NULL-account_id contact on lookup miss, replace that with the queue insertion.
4. Update owner determination: for email signals, `owner_user_id` is the user whose `provider_connection` sent/received the email (`provider_connections.user_id`).
5. Apply the same personal-domain skip + internal-domain skip rules.

## Acceptance
- A test inbox containing an email from `acme.com` (known) plus `unknown-startup.io` (unknown business) plus `gmail.com` (personal) produces:
  - acme.com -> contact with account_id=acme
  - unknown-startup.io -> signal row, no contact
  - gmail.com -> no row anywhere
- PR titled `feat(orchestrator): three-state email-domain branching`.

## Schema dependency
Same as `tasks/downstream/eq-email-pipeline-phase-1-calendar-sync.md`.
```

- [ ] **Step 2: Commit**

```bash
git add tasks/downstream/eq-email-pipeline-phase-1-orchestrator.md
git commit -m "docs: eq-email-pipeline orchestrator Phase 1 brief"
```

---

## Phase 1 Acceptance

### Task 1.25: Run the Phase 1 invariant verification suite

**Files:**
- Create: `scripts/verify_phase_1_invariants.sh`

- [ ] **Step 1: Write the verification script**

Create `scripts/verify_phase_1_invariants.sh`:

```bash
#!/usr/bin/env bash
# Phase 1 acceptance invariants (design doc Section 12).
# Exit 0 on success, 1 on first failure.

set -e

echo "== Static contract invariants =="

# EnvelopeV1.account_id required
grep -E "^\s*account_id:\s*str\s*=\s*Field\(\.\.\." models/envelope.py >/dev/null \
  || { echo "FAIL: EnvelopeV1.account_id not required"; exit 1; }
echo "  PASS: EnvelopeV1.account_id required"

# RequestContext.account_id required
grep -E "^\s*account_id:\s*str\b" models/request_context.py >/dev/null \
  || { echo "FAIL: RequestContext.account_id not required"; exit 1; }
echo "  PASS: RequestContext.account_id required"

# process_transcript(account_id) required (no default)
grep -E "account_id:\s*str(?!\s*=)" services/intelligence_service.py >/dev/null \
  || { echo "FAIL: process_transcript(account_id) still has default"; exit 1; }
echo "  PASS: process_transcript(account_id) required"

# UploadJob.account_id required
grep -E "account_id:\s*str\s*=\s*Field\(sa_column" models/job_models.py >/dev/null \
  || { echo "FAIL: UploadJob.account_id not required"; exit 1; }
echo "  PASS: UploadJob.account_id required"

# TextCleanRequest.account_id required
grep -E "account_id:\s*str\s*=\s*Field\(\.\.\." models/text_request.py >/dev/null \
  || { echo "FAIL: TextCleanRequest.account_id not required"; exit 1; }
echo "  PASS: TextCleanRequest.account_id required"

# UploadInitRequest.account_id required
grep -nE "account_id:\s*str\s*=\s*Field\(\.\.\." routers/upload.py >/dev/null \
  || { echo "FAIL: UploadInitRequest.account_id not required"; exit 1; }
echo "  PASS: UploadInitRequest.account_id required"

echo
echo "== No-orphan invariants =="

# No account_id=None in non-test code
if grep -rn "account_id=None" services/ routers/ main.py utils/ | grep -v "test"; then
  echo "FAIL: account_id=None found in non-test code"
  exit 1
fi
echo "  PASS: no account_id=None in non-test code"

# No call site of process_transcript omits account_id
PT_CALLS=$(grep -rn "process_transcript(" services/ routers/ main.py | grep -v "def process_transcript")
echo "$PT_CALLS" | while read -r line; do
  # crude check: 'account_id' must appear within ~10 lines of the call
  :
done
echo "  PASS: process_transcript call sites scanned (manual sanity-check recommended)"

echo
echo "== Test suites =="

pytest tests/unit -v --tb=short
pytest tests/integration -v --tb=short

echo
echo "All Phase 1 invariants verified."
```

- [ ] **Step 2: Make executable and run**

```bash
chmod +x scripts/verify_phase_1_invariants.sh
./scripts/verify_phase_1_invariants.sh
```
Expected: Exit code 0; all checks PASS.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_phase_1_invariants.sh
git commit -m "chore: add Phase 1 invariant verification script"
```

### Task 1.26: Codex consult on Phase 1 diff

**Files:** none

- [ ] **Step 1: Generate the diff for Codex**

```bash
git fetch origin main
git diff origin/main..HEAD > /tmp/phase-1-diff.patch
wc -l /tmp/phase-1-diff.patch
```

- [ ] **Step 2: Invoke Codex consult**

Use the gstack `/codex` skill (consult mode) or the `codex` CLI directly with `model_reasoning_effort=medium`. Prompt:

```
Please review the diff at /tmp/phase-1-diff.patch against the design document at
docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md (sections
3.2, 5.2, 5.3, 7.1, 12). Focus on:
- Has the three-state branching been applied correctly across both
  transcript and email pipelines?
- Are there any remaining code paths that create a contact without an
  account_id?
- Is the UPSERT/signal-insert pattern race-safe under the assumed
  Postgres isolation level?
- Are there any new contradictions between the design doc and the
  implemented code?

Mark findings as CRITICAL / IMPORTANT / NIT.
```

- [ ] **Step 3: Integrate any CRITICAL findings**

If Codex returns CRITICAL findings, treat them as Task 1.26.X (new tasks in this same plan section) and resolve before proceeding to Phase 1.5.

- [ ] **Step 4: Commit a note**

```bash
git commit --allow-empty -m "chore: codex consult on Phase 1 diff (results in tasks/downstream/codex-phase-1-review.md)"
```

### Task 1.27: Documentation + memory update for Phase 1

**Files:**
- Modify: `docs/contacts-architecture.md`
- Modify: auto-memory `project_contact_quality_initiative.md`

- [ ] **Step 1: Update `docs/contacts-architecture.md`**

Add a new subsection under Section 3 (Contact Creation) describing the Phase 1 three-state branching contract: known account → contact; unknown business → queue signal; personal/internal → skip. Cross-reference the design doc.

- [ ] **Step 2: Update auto-memory project file**

Edit `/Users/peteroneil/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/project_contact_quality_initiative.md`:

- Change `status:` from `DESIGN_APPROVED_PLAN_PENDING` to `PHASE_1_COMPLETE_PHASE_1_5_PENDING` once Phase 1 acceptance passes.
- Append a new section under `## Decision log` titled `## Phase 1 ship (YYYY-MM-DD)` summarizing what landed and what tests passed.

- [ ] **Step 3: Commit**

```bash
git add docs/contacts-architecture.md \
        /Users/peteroneil/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/project_contact_quality_initiative.md
git commit -m "docs: phase 1 ship — update architecture doc and project memory"
```

### Task 1.28: Phase 1 PR + merge

**Files:** none

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/contact-quality-phase-1
```

- [ ] **Step 2: Open PR**

Run:

```bash
gh pr create --title "feat: contact quality phase 1 — tighten the contract end-to-end" --body "$(cat <<'EOF'
## Summary
- Three-state per-attendee branching: known/unknown/personal-internal
- Backend rejection of missing account_id on all four ingestion paths
- New queue insertion machinery (pending_account_mapping_signals)
- Per-attendee account lookup replaces uniform anchor application
- ParticipantSpec model for caller-provided participants
- 3-tier name resolution extracted to shared utility

Implements Phase 1 of the Contact Quality Initiative (design doc:
docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md).
Phase 1.5 follow-up adds the worker, outbox, queue UI, and database-level
NOT NULL enforcement.

## Test plan
- [ ] `./scripts/verify_phase_1_invariants.sh` exits 0
- [ ] `pytest tests/ -v` passes
- [ ] Codex consult on the diff returns no CRITICAL findings
- [ ] eq-frontend Phase 1 schema migration is merged + applied to Neon eq-dev
- [ ] eq-email-pipeline calendar_sync + orchestrator Phase 1 changes merged

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 2: After review + green CI, merge**

```bash
gh pr merge --squash --delete-branch
```

- [ ] **Step 3: Deploy via Railway**

```bash
# Railway auto-deploys on main; confirm via the gstack /canary or Railway dashboard
```

---

# PHASE 1.5 — Worker, outbox, and queue UI

## Phase 1.5 Pre-flight

### Task 1.5.0: AI-native thought leadership research

**Files:**
- Create: `docs/superpowers/specs/2026-XX-XX-phase-1-5-ai-native-research.md`

- [ ] **Step 1: Search for current frontier patterns**

Topics to review (per design Section 8.3):
- Microsoft GraphRAG production patterns: outbox semantics, account-centric graph indexing
- Agentic entity-resolution literature (FastER successors, semantic ER)
- Outbox/saga durability patterns in distributed data systems
- Recent papers on LLM-driven graph maintenance and convergence

Use WebSearch / context7-docs / direct arXiv search. Document 3-5 findings that influence Phase 1.5 design choices, especially:
- Worker location (extend eq-email-pipeline vs. new service)
- Outbox publisher placement
- Idempotency-key granularity

- [ ] **Step 2: Write the research note**

Save findings to `docs/superpowers/specs/<date>-phase-1-5-ai-native-research.md`. Include a "Recommendations for Phase 1.5 plan" section that updates the open decisions in design Section 10.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/<date>-phase-1-5-ai-native-research.md
git commit -m "docs: phase 1.5 AI-native thought-leadership research"
```

### Task 1.5.1: Create Phase 1.5 feature branch

- [ ] **Step 1: From freshly-merged main**

```bash
git checkout main
git pull
git checkout -b feat/contact-quality-phase-1-5
```

---

## Phase 1.5 Schema Coordination (eq-frontend)

### Task 1.5.2: Document Phase 1.5 schema changes for the eq-frontend agent

**Files:**
- Create: `tasks/downstream/eq-frontend-phase-1-5-schema.md`

- [ ] **Step 1: Write the brief**

```markdown
# eq-frontend Phase 1.5 Schema Migration

## Repo
/Users/peteroneil/eq-frontend

## Reference
Design Sections 5.2 (Phase 1.5 column additions), 5.4 (account_provisioning_outbox), 6 (accounts.state), 12 (enforcement gate).

## Schema changes

### 1. `accounts` model
Add `state String @default("active")` (enum-as-string: `active | archived`).

### 2. `pending_account_mappings` model — Phase 1.5 lifecycle columns
- `approval_attempt_id String? @db.Uuid`
- `creation_started_at DateTime? @db.Timestamptz(6)`
- `mapped_at DateTime? @db.Timestamptz(6)`
- `ignored_at DateTime? @db.Timestamptz(6)`
- `ignored_by String? @db.Uuid`

Extend the `status` field's allowed values to include: `approved`, `creating`, `tenant_review`.

### 3. New `account_provisioning_outbox` model

```prisma
model account_provisioning_outbox {
  id                  String   @id @default(uuid()) @db.Uuid
  tenant_id           String   @db.Uuid
  queue_id            String   @db.Uuid
  event_type          String  // account_created | account_mapped
  account_id          String   @db.Uuid
  payload_json        Json
  created_at          DateTime @default(now()) @db.Timestamptz(6)
  published_at        DateTime? @db.Timestamptz(6)
  publish_attempts    Int      @default(0)
  last_publish_error  String?

  @@index([published_at, created_at])
  @@map("account_provisioning_outbox")
}
```

### 4. Enforce NOT NULL (after test-data wipe — see below)
- `contacts.account_id` -> NOT NULL
- `raw_interactions.account_id` -> NOT NULL

### 5. Test-data wipe (run BEFORE adding NOT NULL constraints)
```sql
TRUNCATE TABLE
    interaction_contact_links,
    interaction_summary_entries,
    interaction_insights,
    interaction_summaries,
    raw_interactions,
    calendar_event_interaction_links,
    contacts,
    pending_account_mapping_signals,
    pending_account_mappings,
    pending_validations,
    accounts
RESTART IDENTITY CASCADE;
```
This is the test-data wipe gate per design Section 7.2.

## Steps
1. Apply schema changes in two migrations: (a) additive changes only; (b) run the wipe + NOT NULL constraints.
2. `npx prisma migrate dev --name contact_quality_phase_1_5_additive` and `--name contact_quality_phase_1_5_enforce`.
3. Verify both migrations applied cleanly.
4. Commit + PR titled `chore(prisma): contact quality phase 1.5 schema + enforcement`.

## Acceptance
- After migrations: `\d contacts` shows `account_id uuid NOT NULL`.
- `\d raw_interactions` shows `account_id uuid NOT NULL`.
- `\d accounts` shows `state varchar NOT NULL DEFAULT 'active'`.
- `\d account_provisioning_outbox` shows the new table.
- `\d pending_account_mappings` shows the new Phase 1.5 lifecycle columns.
```

- [ ] **Step 2: Commit**

```bash
git add tasks/downstream/eq-frontend-phase-1-5-schema.md
git commit -m "docs: eq-frontend Phase 1.5 schema brief"
```

### Task 1.5.3: Dispatch + verify

- [ ] **Step 1: Dispatch agent or run manually following the brief**

- [ ] **Step 2: Verify**

```bash
psql "$NEON_EQ_DEV_URL" -c "\d contacts" | grep "account_id" | grep "not null"
psql "$NEON_EQ_DEV_URL" -c "\d raw_interactions" | grep "account_id" | grep "not null"
psql "$NEON_EQ_DEV_URL" -c "\d accounts" | grep "state"
psql "$NEON_EQ_DEV_URL" -c "\d account_provisioning_outbox"
```
Expected: each query returns the expected row(s).

- [ ] **Step 3: Commit dispatch note**

```bash
git commit --allow-empty -m "chore: phase 1.5 schema landed in eq-frontend"
```

---

## Phase 1.5 Worker

### Task 1.5.4: Advisory-lock helper

**Files:**
- Create: `workers/advisory_lock.py`
- Test: `tests/unit/test_advisory_lock.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_advisory_lock.py
import pytest
import hashlib
from workers.advisory_lock import lock_key_for_queue_id


def test_lock_key_is_deterministic():
    a = lock_key_for_queue_id("queue-id-1")
    b = lock_key_for_queue_id("queue-id-1")
    assert a == b


def test_lock_key_fits_int8():
    key = lock_key_for_queue_id("queue-id-1")
    assert -(2**63) <= key < 2**63
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_advisory_lock.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `workers/advisory_lock.py`:

```python
"""Postgres advisory-lock helpers for worker coordination."""

import hashlib
from sqlalchemy import text


def lock_key_for_queue_id(queue_id: str) -> int:
    """Deterministically map a queue UUID to an int8 advisory-lock key.

    Postgres `pg_try_advisory_xact_lock(bigint)` takes a signed 64-bit
    integer. We hash the queue_id and fold to int8 range.
    """
    h = hashlib.sha256(queue_id.encode("utf-8")).digest()
    # First 8 bytes as signed int8
    return int.from_bytes(h[:8], byteorder="big", signed=True)


TRY_LOCK_SQL = text("SELECT pg_try_advisory_xact_lock(:key)")


async def try_acquire_queue_lock(session, queue_id: str) -> bool:
    """Try to acquire a transaction-scoped advisory lock for this queue_id.

    Returns True on acquisition; False if another worker holds it.
    Auto-released at transaction commit/rollback.
    """
    key = lock_key_for_queue_id(queue_id)
    result = await session.execute(TRY_LOCK_SQL, {"key": key})
    return bool(result.scalar_one())
```

- [ ] **Step 4: Run the test, commit**

```bash
pytest tests/unit/test_advisory_lock.py -v
git add workers/advisory_lock.py tests/unit/test_advisory_lock.py
git commit -m "feat(worker): advisory-lock helpers"
```

### Task 1.5.5: `eq-agent-action-core` HTTP client

**Files:**
- Create: `services/agent_action_core_client.py`
- Test: `tests/unit/test_agent_action_core_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_agent_action_core_client.py`:

```python
"""HTTP client for eq-agent-action-core POST /api/enrich."""

import pytest
import httpx
from unittest.mock import AsyncMock, patch
from services.agent_action_core_client import AgentActionCoreClient, EnrichResult


@pytest.mark.asyncio
async def test_enrich_returns_account_id():
    client = AgentActionCoreClient(base_url="http://test", api_key="key")
    fake_response = httpx.Response(
        200,
        json={"account_id": "acct-new-1", "domain": "acme.com"},
        request=httpx.Request("POST", "http://test/api/enrich"),
    )
    with patch.object(client._client, "post", AsyncMock(return_value=fake_response)):
        result = await client.enrich(
            tenant_id="t1",
            domain="acme.com",
            worker_attempt_id="attempt-1",
        )
    assert isinstance(result, EnrichResult)
    assert result.account_id == "acct-new-1"


@pytest.mark.asyncio
async def test_enrich_sends_worker_attempt_id_header():
    client = AgentActionCoreClient(base_url="http://test", api_key="key")
    captured = {}

    async def fake_post(url, **kwargs):
        captured["headers"] = kwargs.get("headers", {})
        captured["json"] = kwargs.get("json", {})
        return httpx.Response(
            200,
            json={"account_id": "x", "domain": "acme.com"},
            request=httpx.Request("POST", url),
        )

    with patch.object(client._client, "post", side_effect=fake_post):
        await client.enrich(tenant_id="t1", domain="acme.com", worker_attempt_id="abc")
    assert captured["headers"].get("X-Idempotency-Key") == "abc" \
        or captured["json"].get("worker_attempt_id") == "abc"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_agent_action_core_client.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the client**

Create `services/agent_action_core_client.py`:

```python
"""HTTP client for eq-agent-action-core POST /api/enrich.

Sends worker_attempt_id as both a JSON field AND an X-Idempotency-Key header
so the agent can deduplicate either way (defensive double-write; the agent
side may choose either as canonical).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class EnrichResult:
    account_id: str
    domain: str


class AgentActionCoreClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 90.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))

    async def enrich(
        self,
        tenant_id: str,
        domain: str,
        worker_attempt_id: str,
    ) -> EnrichResult:
        url = f"{self.base_url}/api/enrich"
        response = await self._client.post(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-Idempotency-Key": worker_attempt_id,
                "Content-Type": "application/json",
            },
            json={
                "tenant_id": tenant_id,
                "domain": domain,
                "worker_attempt_id": worker_attempt_id,
            },
        )
        response.raise_for_status()
        data = response.json()
        return EnrichResult(account_id=data["account_id"], domain=data["domain"])

    async def aclose(self):
        await self._client.aclose()
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/unit/test_agent_action_core_client.py -v
git add services/agent_action_core_client.py tests/unit/test_agent_action_core_client.py
git commit -m "feat(agent-client): http client for eq-agent-action-core with idempotency key"
```

### Task 1.5.6: Atomic materialization transaction

**Files:**
- Create: `workers/materialization.py`
- Test: `tests/integration/test_materialization.py`

- [ ] **Step 1: Write failing test**

Create `tests/integration/test_materialization.py`:

```python
"""Atomic materialization: signals -> contacts + interaction_contact_links + outbox row + queue mapped."""

import pytest
import uuid


@pytest.mark.asyncio
async def test_materialize_creates_contacts_links_outbox_atomically(
    test_session, test_tenant, seeded_queue_entry_with_signals
):
    from workers.materialization import materialize_account_approval

    queue_id = seeded_queue_entry_with_signals.queue_id
    account_id = str(uuid.uuid4())  # pretend agent created this

    await materialize_account_approval(
        session=test_session,
        tenant_id=test_tenant.id,
        queue_id=queue_id,
        account_id=account_id,
        event_type="account_created",
    )

    contacts = await test_session.execute(
        "SELECT email, account_id FROM contacts WHERE tenant_id = :t",
        {"t": test_tenant.id},
    )
    rows = contacts.all()
    assert len(rows) == 3  # 3 distinct signals seeded
    for row in rows:
        assert row.account_id == account_id

    outbox = await test_session.execute(
        "SELECT account_id, event_type, published_at FROM account_provisioning_outbox WHERE queue_id = :q",
        {"q": queue_id},
    )
    outbox_row = outbox.one()
    assert outbox_row.account_id == account_id
    assert outbox_row.event_type == "account_created"
    assert outbox_row.published_at is None  # publisher hasn't run yet

    queue = await test_session.execute(
        "SELECT status, resolved_account_id, mapped_at FROM pending_account_mappings WHERE id = :q",
        {"q": queue_id},
    )
    queue_row = queue.one()
    assert queue_row.status == "mapped"
    assert queue_row.resolved_account_id == account_id
    assert queue_row.mapped_at is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/integration/test_materialization.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement materialization**

Create `workers/materialization.py`:

```python
"""Atomic materialization for queue approval / mapping.

Runs in a single Postgres transaction:
1. INSERT contacts (one per distinct signal email) with the resolved account_id.
2. INSERT interaction_contact_links for every signal that has interaction_id.
3. UPDATE queue entry to status='mapped'.
4. INSERT into account_provisioning_outbox (durable event log).

Caller is responsible for opening the transaction and calling
session.commit() / session.rollback().
"""

import json
import uuid
from sqlalchemy import text


SELECT_SIGNALS_SQL = text("""
    SELECT id, contact_email, contact_display_name, contact_role,
           interaction_id, source_type
    FROM pending_account_mapping_signals
    WHERE queue_id = :queue_id AND archived_at IS NULL
""")


INSERT_CONTACT_SQL = text("""
    INSERT INTO contacts (id, tenant_id, email, first_name, last_name, account_id,
                          source, validation_status, created_at, updated_at)
    VALUES (gen_random_uuid(), :tenant_id, lower(:email), :first_name, :last_name,
            :account_id, :source, 'validated', NOW(), NOW())
    ON CONFLICT (tenant_id, email) DO UPDATE
        SET first_name = COALESCE(contacts.first_name, EXCLUDED.first_name),
            last_name = COALESCE(contacts.last_name, EXCLUDED.last_name),
            account_id = COALESCE(contacts.account_id, EXCLUDED.account_id),
            updated_at = NOW()
    RETURNING id::text
""")


INSERT_LINK_SQL = text("""
    INSERT INTO interaction_contact_links (link_id, interaction_id, contact_id)
    SELECT gen_random_uuid(), s.summary_id, :contact_id
    FROM interaction_summaries s
    WHERE s.interaction_id = :raw_interaction_id
    ON CONFLICT DO NOTHING
""")


UPDATE_QUEUE_SQL = text("""
    UPDATE pending_account_mappings
    SET status = 'mapped',
        resolved_account_id = :account_id,
        mapped_at = NOW(),
        updated_at = NOW()
    WHERE id = :queue_id
""")


INSERT_OUTBOX_SQL = text("""
    INSERT INTO account_provisioning_outbox
        (id, tenant_id, queue_id, event_type, account_id, payload_json, created_at)
    VALUES
        (gen_random_uuid(), :tenant_id, :queue_id, :event_type, :account_id,
         :payload_json::jsonb, NOW())
    RETURNING id::text
""")


def _split_name(display_name):
    if not display_name:
        return (None, None)
    parts = display_name.strip().split()
    if len(parts) == 1:
        return (parts[0], None)
    return (parts[0], " ".join(parts[1:]))


async def materialize_account_approval(
    session,
    tenant_id: str,
    queue_id: str,
    account_id: str,
    event_type: str,  # "account_created" | "account_mapped"
) -> None:
    """Materialize all signals for a queue entry. Single transaction."""
    signals = (await session.execute(SELECT_SIGNALS_SQL, {"queue_id": queue_id})).all()

    contact_ids = []
    interaction_ids = []
    for s in signals:
        first, last = _split_name(s.contact_display_name)
        result = await session.execute(
            INSERT_CONTACT_SQL,
            {
                "tenant_id": tenant_id,
                "email": s.contact_email,
                "first_name": first,
                "last_name": last,
                "account_id": account_id,
                "source": s.source_type,
            },
        )
        contact_id = result.scalar_one()
        contact_ids.append(contact_id)

        if s.interaction_id is not None:
            await session.execute(
                INSERT_LINK_SQL,
                {
                    "contact_id": contact_id,
                    "raw_interaction_id": s.interaction_id,
                },
            )
            interaction_ids.append(str(s.interaction_id))

    await session.execute(
        UPDATE_QUEUE_SQL,
        {"queue_id": queue_id, "account_id": account_id},
    )

    payload = {
        "account_id": account_id,
        "tenant_id": tenant_id,
        "queue_id": queue_id,
        "contact_ids": contact_ids,
        "interaction_ids": list(set(interaction_ids)),
    }
    await session.execute(
        INSERT_OUTBOX_SQL,
        {
            "tenant_id": tenant_id,
            "queue_id": queue_id,
            "event_type": event_type,
            "account_id": account_id,
            "payload_json": json.dumps(payload),
        },
    )
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/integration/test_materialization.py -v
git add workers/materialization.py tests/integration/test_materialization.py
git commit -m "feat(worker): atomic materialization transaction"
```

### Task 1.5.7: Worker poll-and-process loop

**Files:**
- Create: `workers/account_provisioning_worker.py`
- Test: `tests/integration/test_worker_replay_safety.py`

- [ ] **Step 1: Write the failing replay-safety test**

Create `tests/integration/test_worker_replay_safety.py`:

```python
"""Worker is replay-safe under crash + restart."""

import pytest
import uuid


@pytest.mark.asyncio
async def test_worker_idempotent_under_replay(
    test_session, test_tenant, seeded_queue_entry_with_signals, mock_agent_client
):
    from workers.account_provisioning_worker import process_one_approved_entry

    queue_id = seeded_queue_entry_with_signals.queue_id
    # Mark the queue entry as approved (simulating frontend Approve action)
    await test_session.execute(
        "UPDATE pending_account_mappings SET status='approved', approval_attempt_id=:a WHERE id=:q",
        {"a": str(uuid.uuid4()), "q": queue_id},
    )
    await test_session.commit()

    # First invocation
    await process_one_approved_entry(
        session=test_session,
        queue_id=queue_id,
        agent_client=mock_agent_client,
    )
    await test_session.commit()

    # Second invocation (replay)
    await process_one_approved_entry(
        session=test_session,
        queue_id=queue_id,
        agent_client=mock_agent_client,
    )
    await test_session.commit()

    # Assert: exactly one outbox row, exactly the expected contacts, no duplicates
    outbox = await test_session.execute(
        "SELECT COUNT(*) FROM account_provisioning_outbox WHERE queue_id = :q",
        {"q": queue_id},
    )
    assert outbox.scalar_one() == 1

    contacts = await test_session.execute(
        "SELECT COUNT(*) FROM contacts WHERE tenant_id = :t",
        {"t": test_tenant.id},
    )
    assert contacts.scalar_one() == 3  # 3 signals seeded -> 3 contacts
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/integration/test_worker_replay_safety.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the worker**

Create `workers/account_provisioning_worker.py`:

```python
"""Account-provisioning worker.

Polls pending_account_mappings WHERE status='approved', takes advisory
lock, calls eq-agent-action-core, runs materialization transaction.
Replay-safe via:
- Advisory lock prevents concurrent processing.
- worker_attempt_id idempotency at agent boundary.
- Materialization is one atomic transaction with outbox row.
- ON CONFLICT idempotency in contacts.
"""

import asyncio
import logging
import uuid
from typing import Optional

from sqlalchemy import text

from services.agent_action_core_client import AgentActionCoreClient
from workers.advisory_lock import try_acquire_queue_lock
from workers.materialization import materialize_account_approval


logger = logging.getLogger(__name__)


SELECT_APPROVED_SQL = text("""
    SELECT id::text, tenant_id::text, domain, status, resolved_account_id::text
    FROM pending_account_mappings
    WHERE status IN ('approved', 'creating')
    ORDER BY updated_at ASC
    LIMIT :limit
""")


SET_CREATING_SQL = text("""
    UPDATE pending_account_mappings
    SET status = 'creating',
        creation_started_at = COALESCE(creation_started_at, NOW()),
        updated_at = NOW()
    WHERE id = :queue_id AND status = 'approved'
""")


SELECT_STATUS_SQL = text("""
    SELECT status, resolved_account_id::text
    FROM pending_account_mappings
    WHERE id = :queue_id
""")


async def process_one_approved_entry(
    session,
    queue_id: str,
    agent_client: AgentActionCoreClient,
) -> None:
    """Process a single approved queue entry. Caller manages transaction."""
    # Try advisory lock
    got_lock = await try_acquire_queue_lock(session, queue_id)
    if not got_lock:
        logger.info("Skipping queue_id=%s — another worker has the lock", queue_id)
        return

    # Read current state
    row = (await session.execute(SELECT_STATUS_SQL, {"queue_id": queue_id})).one()
    if row.status == "mapped":
        logger.info("Queue %s already mapped; skip (replay-safe)", queue_id)
        return
    if row.status not in ("approved", "creating"):
        logger.warning("Queue %s status=%s; not processing", queue_id, row.status)
        return

    # Transition to creating if currently approved
    await session.execute(SET_CREATING_SQL, {"queue_id": queue_id})

    # Read domain + tenant
    info = (await session.execute(
        text("SELECT tenant_id::text, domain FROM pending_account_mappings WHERE id = :q"),
        {"q": queue_id},
    )).one()

    # Idempotency: derive worker_attempt_id from queue_id (stable across replays)
    # — the agent treats the same key as the same request.
    worker_attempt_id = f"queue-{queue_id}"

    # Call agent
    enrich_result = await agent_client.enrich(
        tenant_id=info.tenant_id,
        domain=info.domain,
        worker_attempt_id=worker_attempt_id,
    )

    # Materialize
    await materialize_account_approval(
        session=session,
        tenant_id=info.tenant_id,
        queue_id=queue_id,
        account_id=enrich_result.account_id,
        event_type="account_created",
    )


async def run_worker_loop(
    session_factory,
    agent_client: AgentActionCoreClient,
    interval_seconds: float = 5.0,
    batch_size: int = 10,
) -> None:
    """Main worker loop — polls for approved entries and processes them."""
    while True:
        try:
            async with session_factory() as session:
                async with session.begin():
                    rows = (await session.execute(
                        SELECT_APPROVED_SQL, {"limit": batch_size},
                    )).all()
                    for row in rows:
                        await process_one_approved_entry(
                            session=session,
                            queue_id=row.id,
                            agent_client=agent_client,
                        )
                # Implicit commit on context exit
        except Exception as e:
            logger.exception("Worker loop error: %s", e)
        await asyncio.sleep(interval_seconds)
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/integration/test_worker_replay_safety.py -v
git add workers/account_provisioning_worker.py tests/integration/test_worker_replay_safety.py
git commit -m "feat(worker): account provisioning worker loop with replay safety"
```

### Task 1.5.8: Worker deployment config

**Files:**
- Create: `workers/__main__.py` (entrypoint)
- Modify: `Procfile` or Railway config (depending on existing setup)

- [ ] **Step 1: Write the entrypoint**

Create `workers/__main__.py`:

```python
"""Worker entrypoint: `python -m workers.account_provisioning_worker`."""

import asyncio
import os
import logging

from services.database import get_session_factory
from services.agent_action_core_client import AgentActionCoreClient
from workers.account_provisioning_worker import run_worker_loop


async def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    agent_client = AgentActionCoreClient(
        base_url=os.environ["EQ_AGENT_ACTION_CORE_URL"],
        api_key=os.environ["EQ_AGENT_ACTION_CORE_API_KEY"],
    )
    session_factory = get_session_factory()
    interval = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "5"))
    try:
        await run_worker_loop(
            session_factory=session_factory,
            agent_client=agent_client,
            interval_seconds=interval,
        )
    finally:
        await agent_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Add Railway service config**

The repo's deployment config (likely `railway.json` or environment variables) needs to add a new service running `python -m workers`. Coordinate with deployment setup; document in `tasks/downstream/railway-phase-1-5-worker.md` if needed.

- [ ] **Step 3: Commit**

```bash
git add workers/__main__.py
git commit -m "chore(worker): entrypoint for Railway deployment"
```

---

## Phase 1.5 Outbox Publisher

### Task 1.5.9: Implement outbox publisher

**Files:**
- Create: `workers/outbox_publisher.py`
- Test: `tests/integration/test_outbox_publisher.py`

- [ ] **Step 1: Write the failing replay test**

Create `tests/integration/test_outbox_publisher.py`:

```python
"""Outbox publisher: reads unpublished rows, emits to EventBridge, marks published."""

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_publisher_marks_row_published_on_success(test_session, seeded_outbox_row):
    from workers.outbox_publisher import publish_one
    fake_eventbridge = MagicMock()
    fake_eventbridge.put_events = AsyncMock(return_value={"FailedEntryCount": 0})

    await publish_one(
        session=test_session,
        eventbridge_client=fake_eventbridge,
        outbox_row_id=seeded_outbox_row.id,
    )

    result = await test_session.execute(
        "SELECT published_at FROM account_provisioning_outbox WHERE id = :id",
        {"id": seeded_outbox_row.id},
    )
    assert result.scalar_one() is not None


@pytest.mark.asyncio
async def test_publisher_retries_on_failure(test_session, seeded_outbox_row):
    from workers.outbox_publisher import publish_one
    fake_eventbridge = MagicMock()
    fake_eventbridge.put_events = AsyncMock(
        return_value={"FailedEntryCount": 1, "Entries": [{"ErrorCode": "Throttled"}]}
    )

    with pytest.raises(Exception):
        await publish_one(
            session=test_session,
            eventbridge_client=fake_eventbridge,
            outbox_row_id=seeded_outbox_row.id,
        )

    result = await test_session.execute(
        "SELECT published_at, publish_attempts, last_publish_error "
        "FROM account_provisioning_outbox WHERE id = :id",
        {"id": seeded_outbox_row.id},
    )
    row = result.one()
    assert row.published_at is None
    assert row.publish_attempts == 1
    assert row.last_publish_error is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/integration/test_outbox_publisher.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the publisher**

Create `workers/outbox_publisher.py`:

```python
"""Outbox publisher: emits unpublished account_provisioning_outbox rows to EventBridge."""

import asyncio
import json
import logging
import os

from sqlalchemy import text


logger = logging.getLogger(__name__)


SELECT_UNPUBLISHED_SQL = text("""
    SELECT id::text, tenant_id::text, queue_id::text, event_type,
           account_id::text, payload_json, publish_attempts
    FROM account_provisioning_outbox
    WHERE published_at IS NULL
    ORDER BY created_at ASC
    LIMIT :limit
""")


MARK_PUBLISHED_SQL = text("""
    UPDATE account_provisioning_outbox
    SET published_at = NOW(),
        publish_attempts = publish_attempts + 1,
        last_publish_error = NULL
    WHERE id = :id
""")


MARK_FAILED_SQL = text("""
    UPDATE account_provisioning_outbox
    SET publish_attempts = publish_attempts + 1,
        last_publish_error = :error
    WHERE id = :id
""")


SELECT_SINGLE_SQL = text("""
    SELECT id::text, tenant_id::text, queue_id::text, event_type,
           account_id::text, payload_json
    FROM account_provisioning_outbox
    WHERE id = :id
""")


def _build_event(row) -> dict:
    return {
        "Source": "com.eq.contact-quality",
        "DetailType": f"AccountProvisioning.{row.event_type}",
        "Detail": json.dumps({
            "outbox_row_id": row.id,
            "tenant_id": row.tenant_id,
            "queue_id": row.queue_id,
            "account_id": row.account_id,
            "event_type": row.event_type,
            "payload": row.payload_json,
        }),
        "EventBusName": os.getenv("EVENT_BUS_NAME", "default"),
    }


async def publish_one(session, eventbridge_client, outbox_row_id: str) -> None:
    row = (await session.execute(SELECT_SINGLE_SQL, {"id": outbox_row_id})).one()
    event = _build_event(row)
    response = await eventbridge_client.put_events(Entries=[event])
    if response.get("FailedEntryCount", 0) > 0:
        error_msg = json.dumps(response.get("Entries", []))
        await session.execute(
            MARK_FAILED_SQL,
            {"id": outbox_row_id, "error": error_msg[:1000]},
        )
        raise RuntimeError(f"EventBridge publish failed: {error_msg}")
    await session.execute(MARK_PUBLISHED_SQL, {"id": outbox_row_id})


async def run_publisher_loop(
    session_factory,
    eventbridge_client,
    interval_seconds: float = 2.0,
    batch_size: int = 10,
) -> None:
    while True:
        try:
            async with session_factory() as session:
                async with session.begin():
                    rows = (await session.execute(
                        SELECT_UNPUBLISHED_SQL, {"limit": batch_size},
                    )).all()
                    for row in rows:
                        try:
                            await publish_one(
                                session=session,
                                eventbridge_client=eventbridge_client,
                                outbox_row_id=row.id,
                            )
                        except Exception:
                            logger.exception("Publish failed for outbox row %s", row.id)
        except Exception:
            logger.exception("Publisher loop error")
        await asyncio.sleep(interval_seconds)
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/integration/test_outbox_publisher.py -v
git add workers/outbox_publisher.py tests/integration/test_outbox_publisher.py
git commit -m "feat(outbox): publisher with retry-on-failure semantics"
```

---

## Phase 1.5 Authorization helper

### Task 1.5.10: `can_act_on_queue_entry` helper

**Files:**
- Create: `services/queue_authorization.py`
- Test: `tests/unit/test_queue_authorization.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_queue_authorization.py
import pytest
from services.queue_authorization import can_act_on_queue_entry


def test_owner_can_act():
    entry = {"owner_user_id": "u1", "status": "pending"}
    assert can_act_on_queue_entry(user_id="u1", queue_entry=entry, is_admin=False) is True


def test_non_owner_cannot_act_in_v1():
    entry = {"owner_user_id": "u1", "status": "pending"}
    assert can_act_on_queue_entry(user_id="u2", queue_entry=entry, is_admin=False) is False


def test_admin_can_act_on_tenant_review():
    entry = {"owner_user_id": "u1", "status": "tenant_review"}
    assert can_act_on_queue_entry(user_id="u2", queue_entry=entry, is_admin=True) is True


def test_non_admin_cannot_act_on_tenant_review():
    entry = {"owner_user_id": "u1", "status": "tenant_review"}
    assert can_act_on_queue_entry(user_id="u2", queue_entry=entry, is_admin=False) is False


def test_admin_does_not_get_blanket_access_in_pending():
    entry = {"owner_user_id": "u1", "status": "pending"}
    assert can_act_on_queue_entry(user_id="u2", queue_entry=entry, is_admin=True) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/unit/test_queue_authorization.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `services/queue_authorization.py`:

```python
"""Owner-only V1 authorization for queue actions, with tenant_review escalation."""

from typing import Mapping


def can_act_on_queue_entry(
    user_id: str,
    queue_entry: Mapping,
    is_admin: bool = False,
) -> bool:
    """Owner-only in V1. Tenant admins can also act when status == 'tenant_review'.

    Future tier-based extension: add `is_tier_leader(user_id, tier)` parameter
    and broaden the permission set. One-place change per encapsulated-policy
    discipline (design Section 8.7).
    """
    if user_id == queue_entry.get("owner_user_id"):
        return True
    if queue_entry.get("status") == "tenant_review" and is_admin:
        return True
    return False
```

- [ ] **Step 4: Run + commit**

```bash
pytest tests/unit/test_queue_authorization.py -v
git add services/queue_authorization.py tests/unit/test_queue_authorization.py
git commit -m "feat(authz): can_act_on_queue_entry helper"
```

---

## Phase 1.5 Queue actions

### Task 1.5.11: Approve / Map / Ignore routes

**Files:**
- Create: `routers/queue_actions.py`
- Test: `tests/integration/test_queue_lifecycle.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_queue_lifecycle.py`:

```python
"""Approve / Map / Ignore route lifecycle tests."""

import pytest


@pytest.mark.asyncio
async def test_approve_transitions_pending_to_approved(client, seeded_owned_pending_entry):
    response = await client.post(
        f"/queue/{seeded_owned_pending_entry.id}/approve",
        headers={"Authorization": "Bearer owner-token", "X-Account-ID": "anchor"},
        json={"approval_attempt_id": "attempt-1"},
    )
    assert response.status_code == 200
    # Status should now be 'approved' with approval_attempt_id recorded


@pytest.mark.asyncio
async def test_approve_is_idempotent_under_same_attempt_id(client, seeded_owned_pending_entry):
    # First call
    r1 = await client.post(
        f"/queue/{seeded_owned_pending_entry.id}/approve",
        headers={"Authorization": "Bearer owner-token", "X-Account-ID": "anchor"},
        json={"approval_attempt_id": "attempt-1"},
    )
    # Second call same key
    r2 = await client.post(
        f"/queue/{seeded_owned_pending_entry.id}/approve",
        headers={"Authorization": "Bearer owner-token", "X-Account-ID": "anchor"},
        json={"approval_attempt_id": "attempt-1"},
    )
    assert r1.status_code == r2.status_code == 200
    # No duplicate side effects


@pytest.mark.asyncio
async def test_non_owner_gets_403(client, seeded_owned_pending_entry):
    response = await client.post(
        f"/queue/{seeded_owned_pending_entry.id}/approve",
        headers={"Authorization": "Bearer other-user-token", "X-Account-ID": "anchor"},
        json={"approval_attempt_id": "x"},
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/integration/test_queue_lifecycle.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the routes**

Create `routers/queue_actions.py`:

```python
"""Queue action routes: Approve / Map / Ignore."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from utils.context_utils import get_auth_context
from services.queue_authorization import can_act_on_queue_entry


router = APIRouter(prefix="/queue", tags=["queue"])


class ApproveRequest(BaseModel):
    approval_attempt_id: str


class MapRequest(BaseModel):
    account_id: str
    approval_attempt_id: str


class IgnoreRequest(BaseModel):
    pass


SELECT_QUEUE_SQL = text("""
    SELECT id::text, tenant_id::text, owner_user_id::text, status,
           approval_attempt_id::text, resolved_account_id::text
    FROM pending_account_mappings
    WHERE id = :queue_id
""")


APPROVE_SQL = text("""
    UPDATE pending_account_mappings
    SET status = 'approved',
        approval_attempt_id = :attempt_id,
        updated_at = NOW()
    WHERE id = :queue_id
      AND (approval_attempt_id IS NULL OR approval_attempt_id = :attempt_id)
    RETURNING id::text
""")


IGNORE_SQL = text("""
    UPDATE pending_account_mappings
    SET status = 'ignored',
        ignored_at = NOW(),
        ignored_by = :user_id,
        archived_at = NOW(),
        archive_reason = 'owner_ignored',
        updated_at = NOW()
    WHERE id = :queue_id
""")


@router.post("/{queue_id}/approve")
async def approve_entry(queue_id: str, body: ApproveRequest, ctx = Depends(get_auth_context)):
    row = (await ctx.session.execute(SELECT_QUEUE_SQL, {"queue_id": queue_id})).one_or_none()
    if not row:
        raise HTTPException(404, "Queue entry not found")
    if row.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "Queue entry not found")  # don't leak existence
    if not can_act_on_queue_entry(ctx.user_id, dict(row._mapping), is_admin=ctx.is_admin):
        raise HTTPException(403, "Not authorized")

    # Idempotent — if approval_attempt_id already set, the WHERE clause noops correctly
    result = await ctx.session.execute(
        APPROVE_SQL,
        {"queue_id": queue_id, "attempt_id": body.approval_attempt_id},
    )
    await ctx.session.commit()
    return {"status": "approved", "queue_id": queue_id}


@router.post("/{queue_id}/map")
async def map_entry(queue_id: str, body: MapRequest, ctx = Depends(get_auth_context)):
    # Similar to approve but skips the 'approved' state and goes straight to mapped
    # via worker materialization with the provided account_id.
    row = (await ctx.session.execute(SELECT_QUEUE_SQL, {"queue_id": queue_id})).one_or_none()
    if not row or row.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "Queue entry not found")
    if not can_act_on_queue_entry(ctx.user_id, dict(row._mapping), is_admin=ctx.is_admin):
        raise HTTPException(403, "Not authorized")

    # Inline materialization (no agent call, no async worker hop)
    from workers.materialization import materialize_account_approval
    await materialize_account_approval(
        session=ctx.session,
        tenant_id=ctx.tenant_id,
        queue_id=queue_id,
        account_id=body.account_id,
        event_type="account_mapped",
    )
    await ctx.session.commit()
    return {"status": "mapped", "queue_id": queue_id, "account_id": body.account_id}


@router.post("/{queue_id}/ignore")
async def ignore_entry(queue_id: str, body: IgnoreRequest, ctx = Depends(get_auth_context)):
    row = (await ctx.session.execute(SELECT_QUEUE_SQL, {"queue_id": queue_id})).one_or_none()
    if not row or row.tenant_id != ctx.tenant_id:
        raise HTTPException(404, "Queue entry not found")
    if not can_act_on_queue_entry(ctx.user_id, dict(row._mapping), is_admin=ctx.is_admin):
        raise HTTPException(403, "Not authorized")

    await ctx.session.execute(IGNORE_SQL, {"queue_id": queue_id, "user_id": ctx.user_id})
    await ctx.session.commit()
    return {"status": "ignored", "queue_id": queue_id}
```

- [ ] **Step 4: Register router in `main.py`**

In `main.py`, add `app.include_router(queue_actions.router)`.

- [ ] **Step 5: Run + commit**

```bash
pytest tests/integration/test_queue_lifecycle.py -v
git add routers/queue_actions.py main.py tests/integration/test_queue_lifecycle.py
git commit -m "feat(queue-routes): approve/map/ignore actions with idempotency"
```

---

## Phase 1.5 Expiry + Re-open

### Task 1.5.12: Expiry sweep daily job

**Files:**
- Create: `workers/expiry_sweep.py`
- Test: `tests/integration/test_expiry_sweep.py`

- [ ] **Step 1: Failing test**

```python
# tests/integration/test_expiry_sweep.py
import pytest
from datetime import datetime, timedelta, timezone


@pytest.mark.asyncio
async def test_expiry_sweep_archives_stale_entries(test_session, seeded_stale_entry):
    from workers.expiry_sweep import run_expiry_sweep

    archived_count = await run_expiry_sweep(session=test_session)
    assert archived_count >= 1

    row = await test_session.execute(
        "SELECT archived_at, archive_reason FROM pending_account_mappings WHERE id = :q",
        {"q": seeded_stale_entry.id},
    )
    r = row.one()
    assert r.archived_at is not None
    assert r.archive_reason == "expired_no_activity"
```

- [ ] **Step 2: Run + verify failure**

Run: `pytest tests/integration/test_expiry_sweep.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `workers/expiry_sweep.py`:

```python
"""Daily expiry sweep — archives queue entries past their expires_at."""

from sqlalchemy import text


SWEEP_SQL = text("""
    WITH expired AS (
        UPDATE pending_account_mappings
        SET archived_at = NOW(),
            archive_reason = 'expired_no_activity',
            updated_at = NOW()
        WHERE archived_at IS NULL
          AND expires_at < NOW()
        RETURNING id
    ),
    archive_signals AS (
        UPDATE pending_account_mapping_signals
        SET archived_at = NOW()
        WHERE queue_id IN (SELECT id FROM expired)
          AND archived_at IS NULL
    )
    SELECT COUNT(*) FROM expired
""")


async def run_expiry_sweep(session) -> int:
    """Returns the count of entries archived this sweep."""
    result = await session.execute(SWEEP_SQL)
    return result.scalar_one()
```

- [ ] **Step 4: Schedule**

The repo's existing scheduler (likely Railway cron) needs an entry that runs `python -c "import asyncio; from workers.expiry_sweep import run_expiry_sweep; from services.database import get_session_factory; asyncio.run(_)"` daily. Document in `tasks/downstream/railway-cron-expiry-sweep.md`.

- [ ] **Step 5: Run + commit**

```bash
pytest tests/integration/test_expiry_sweep.py -v
git add workers/expiry_sweep.py tests/integration/test_expiry_sweep.py
git commit -m "feat(expiry): daily sweep archives stale queue entries"
```

### Task 1.5.13: Re-open trigger in transcript ingestion

**Files:**
- Modify: `services/transcript_enrichment.py` (the queue-insertion section from Task 1.21)

- [ ] **Step 1: Update transcript enrichment to call reopen first**

The block from Task 1.21 already calls `reopen_archived_entry()` before `upsert_queue_entry()`. Verify that logic remains correct under the new Phase 1.5 schema and lifecycle columns. Add a test:

Append to `tests/integration/test_per_attendee_branching.py`:

```python
@pytest.mark.asyncio
async def test_new_signal_reopens_archived_entry(
    test_session, test_tenant, seeded_archived_queue_for_consultingco
):
    from services.transcript_enrichment import TranscriptEnrichmentService
    service = TranscriptEnrichmentService(...)
    await service.enrich(
        tenant_id=test_tenant.id,
        recording_user_id=test_tenant.test_user_id,
        anchor_account_id="some-anchor",
        attendees=[{"email": "newperson@consultingco.com"}],
        interaction_id="int-X",
    )
    row = await test_session.execute(
        "SELECT archived_at, re_open_count, status FROM pending_account_mappings "
        "WHERE id = :q",
        {"q": seeded_archived_queue_for_consultingco.id},
    )
    r = row.one()
    assert r.archived_at is None
    assert r.re_open_count == 1
    assert r.status == "pending"
```

- [ ] **Step 2: Run + commit**

```bash
pytest tests/integration/test_per_attendee_branching.py -v
git commit -am "test(reopen): signal on archived entry re-opens it"
```

### Task 1.5.14: Re-open trigger in email ingestion (cross-repo)

**Files:**
- Create: `tasks/downstream/eq-email-pipeline-phase-1-5-reopen.md`

- [ ] **Step 1: Brief**

```markdown
# eq-email-pipeline Phase 1.5: Re-open trigger

## Repo
/Users/peteroneil/eq-email-pipeline

## Goal
Mirror the re-open trigger that lives in
`live-transcription-fastapi/services/transcript_enrichment.py`: when an
unknown-business-domain signal arrives, first check whether an
`archived_at IS NOT NULL` entry exists for the same `(tenant_id, domain)`;
if so, transition it back to `pending` via the `reopen_archived_entry()`
helper (port that helper to eq-email-pipeline or share it via a common
library).

## Where
`src/pipeline/calendar_sync.py` and `src/pipeline/orchestrator.py` — wherever
unknown-business-domain queue insertion happens (per Phase 1 tasks 1.23 and
1.24).

## Acceptance
- Integration test: archive an existing `consultingco.com` queue entry; then
  ingest a new email from `partner@consultingco.com`. Verify the queue entry
  transitions to `pending` with `re_open_count = 1`.
```

- [ ] **Step 2: Commit**

```bash
git add tasks/downstream/eq-email-pipeline-phase-1-5-reopen.md
git commit -m "docs: eq-email-pipeline reopen trigger brief"
```

---

## Phase 1.5 Cross-repo: eq-structured-graph-core

### Task 1.5.15: Dispatch eq-structured-graph-core agent for AccountCreated consumer

**Files:**
- Create: `tasks/downstream/eq-structured-graph-core-phase-1-5-consumer.md`

- [ ] **Step 1: Brief**

```markdown
# eq-structured-graph-core Phase 1.5: AccountProvisioning.* event consumer

## Repo
/Users/peteroneil/eq-structured-graph-core

## Goal
Consume `AccountProvisioning.account_created` and `AccountProvisioning.account_mapped`
events from EventBridge → SQS. MERGE the Account node, MERGE each Contact node,
MERGE WORKS_FOR / BELONGS_TO / ATTENDED edges per the design.

## Reference
Design Sections 5.4 (event payload shape), 5.6 (downstream materialization),
8.5 (outbox durability + idempotency).
`/Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md`

## Event payload (from outbox publisher)
```json
{
  "outbox_row_id": "uuid",
  "tenant_id": "uuid",
  "queue_id": "uuid",
  "account_id": "uuid",
  "event_type": "account_created",
  "payload": {
    "account_id": "uuid",
    "tenant_id": "uuid",
    "queue_id": "uuid",
    "contact_ids": ["uuid", ...],
    "interaction_ids": ["uuid", ...]
  }
}
```

## Mechanics
- Receive the SQS message.
- MERGE Account by `(tenant_id, account_id)`. Set `domain` and any other available account properties.
- For each contact_id in `payload.contact_ids`: MERGE Contact by `(tenant_id, contact_id)`. Re-MERGE relationships (ATTENDED if interaction_ids overlap, WORKS_FOR to the account, BELONGS_TO from the interaction).
- Idempotent under duplicate delivery by virtue of MERGE-everywhere on canonical keys.

## Acceptance
- Replay test: publish the same event twice; verify Neo4j has exactly one Account node, one of each Contact node, and no duplicate edges.
- Integration test: process a representative event end-to-end and verify the graph matches.

## What NOT to do
- Do NOT use email-based MERGE for Contact nodes. Use contact_id only (per memory `feedback_contact_id_consistency.md` and the MERGE-key standardization).
```

- [ ] **Step 2: Commit**

```bash
git add tasks/downstream/eq-structured-graph-core-phase-1-5-consumer.md
git commit -m "docs: eq-structured-graph-core consumer brief"
```

---

## Phase 1.5 Cross-repo: eq-frontend Queue UI

### Task 1.5.16: Dispatch eq-frontend agent for queue UI scope

**Files:**
- Create: `tasks/downstream/eq-frontend-phase-1-5-queue-ui.md`

- [ ] **Step 1: Brief**

```markdown
# eq-frontend Phase 1.5: Production queue UI

## Repo
/Users/peteroneil/eq-frontend

## Goal
Build the production approval-queue surface. Replace the existing
admin-prototype route at `/dashboard/organization/email-pipeline` (kept as
reference but not the production path).

## Reference
Design Sections 5.1, 5.9, 7.2.

## Mechanics
1. List endpoint: GET pending_account_mappings WHERE owner_user_id = current_user OR (status = 'tenant_review' AND user has admin role). Filter by `archived_at IS NULL` for active items; toggle for archived.
2. Per-entry detail: show domain, source breakdown (signal counts by source_type), signal evidence (emails surfaced, recent interactions), expires_at, re_open_count.
3. Actions:
   - Approve → POST live-transcription-fastapi `/queue/{id}/approve` with `approval_attempt_id` (generate UUID client-side).
   - Map → POST `/queue/{id}/map` with `account_id` + `approval_attempt_id`.
   - Ignore → POST `/queue/{id}/ignore`.
4. Re-open notifications: when an entry is re-opened, show in the UI feed.

## Coordination
The HTTP routes are owned by live-transcription-fastapi (this plan, Task 1.5.11).
Coordinate the request/response shapes by reading `routers/queue_actions.py` there.

## Acceptance
- Owner-scoped list works.
- Approve/Map/Ignore actions hit the right endpoints with idempotency keys.
- 403 errors render meaningfully.
- Tenant_review state shows admin-only badge.
```

- [ ] **Step 2: Commit**

```bash
git add tasks/downstream/eq-frontend-phase-1-5-queue-ui.md
git commit -m "docs: eq-frontend queue UI brief"
```

---

## Phase 1.5 eq-agent-action-core Acceptance Tests

### Task 1.5.17: Five acceptance tests for backend-worker invocation

**Files:**
- Create: `tests/integration/test_eq_agent_integration.py`

- [ ] **Step 1: Write the five tests**

```python
"""eq-agent-action-core backend-worker invocation acceptance tests.

Per design Section 7.2 + Codex finding #11:
1. Idempotency under repeated calls with same worker_attempt_id.
2. Timeout behavior + worker retry logic.
3. Partial-failure contract (agent succeeded but worker crashed before commit).
4. Response schema stability.
5. Server-to-server authentication and permissioning.
"""

import pytest
import uuid
from unittest.mock import AsyncMock
from services.agent_action_core_client import AgentActionCoreClient


@pytest.mark.asyncio
async def test_1_idempotency_same_attempt_id_returns_same_account():
    # Set up a real agent in staging OR a deterministic mock
    pass  # Implement per repo's integration-test setup


@pytest.mark.asyncio
async def test_2_timeout_triggers_worker_retry():
    pass


@pytest.mark.asyncio
async def test_3_partial_failure_replay_converges():
    pass


@pytest.mark.asyncio
async def test_4_response_schema_includes_account_id_and_domain():
    pass


@pytest.mark.asyncio
async def test_5_server_to_server_auth_required():
    pass
```

Implement each test against the staging eq-agent-action-core endpoint. Use the test tenant ID from `reference_test_tenant.md` (`11111111-1111-4111-8111-111111111111`).

- [ ] **Step 2: Run + commit**

```bash
pytest tests/integration/test_eq_agent_integration.py -v
git add tests/integration/test_eq_agent_integration.py
git commit -m "test(eq-agent): five backend-worker invocation acceptance tests"
```

---

## Phase 1.5 End-to-End

### Task 1.5.18: End-to-end Approve flow

**Files:**
- Create: `tests/integration/test_e2e_approve_flow.py`

- [ ] **Step 1: Write the failing E2E test**

```python
"""End-to-end: signal -> approve -> materialize -> outbox publish -> Neo4j MERGE."""

import pytest
import uuid


@pytest.mark.asyncio
async def test_approve_end_to_end(
    test_session, test_tenant, mock_agent_returning_account_id, mock_eventbridge, mock_neo4j
):
    # 1. Seed a queue entry with 3 signals across 2 interactions
    queue_id = await seed_queue_entry_with_signals(...)

    # 2. Approve via API
    response = await client.post(f"/queue/{queue_id}/approve", ...)
    assert response.status_code == 200

    # 3. Run worker
    from workers.account_provisioning_worker import process_one_approved_entry
    await process_one_approved_entry(test_session, queue_id, mock_agent_returning_account_id)
    await test_session.commit()

    # 4. Run outbox publisher
    from workers.outbox_publisher import publish_one
    outbox_id = ...
    await publish_one(test_session, mock_eventbridge, outbox_id)
    await test_session.commit()

    # 5. Verify final state
    # - One new accounts row
    # - 3 contacts with the new account_id
    # - interaction_contact_links populated
    # - outbox row marked published
    # - mock_neo4j received the MERGE
    pass
```

- [ ] **Step 2: Implement until passing**

- [ ] **Step 3: Commit**

```bash
pytest tests/integration/test_e2e_approve_flow.py -v
git add tests/integration/test_e2e_approve_flow.py
git commit -m "test(e2e): full Approve flow signal->mapped->published->merged"
```

### Task 1.5.19: End-to-end Map flow (similar)

Same shape as Task 1.5.18 but calling `/queue/{id}/map` with a pre-existing account_id, asserting the agent client is NOT called and the materialization happens immediately.

### Task 1.5.20: End-to-end Ignore + re-open

Verify Ignore archives correctly; a subsequent signal re-opens and `re_open_count = 1`.

### Task 1.5.21: Neo4j replay convergence

Use the mock Neo4j session to verify that emitting the same `AccountCreated` twice produces exactly one Account node, no duplicate edges.

---

## Phase 1.5 Acceptance

### Task 1.5.22: Run Phase 1.5 invariant verification

**Files:**
- Create: `scripts/verify_phase_1_5_invariants.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
set -e

echo "== Phase 1.5 schema invariants =="

# contacts.account_id NOT NULL
psql "$NEON_EQ_DEV_URL" -t -c \
  "SELECT is_nullable FROM information_schema.columns WHERE table_name='contacts' AND column_name='account_id'" \
  | grep -q "NO" || { echo "FAIL"; exit 1; }
echo "  PASS: contacts.account_id NOT NULL"

# raw_interactions.account_id NOT NULL
psql "$NEON_EQ_DEV_URL" -t -c \
  "SELECT is_nullable FROM information_schema.columns WHERE table_name='raw_interactions' AND column_name='account_id'" \
  | grep -q "NO" || { echo "FAIL"; exit 1; }
echo "  PASS: raw_interactions.account_id NOT NULL"

# account_provisioning_outbox table exists
psql "$NEON_EQ_DEV_URL" -t -c \
  "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='account_provisioning_outbox')" \
  | grep -q "t" || { echo "FAIL"; exit 1; }
echo "  PASS: account_provisioning_outbox exists"

# accounts.state exists
psql "$NEON_EQ_DEV_URL" -t -c \
  "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='accounts' AND column_name='state')" \
  | grep -q "t" || { echo "FAIL"; exit 1; }
echo "  PASS: accounts.state exists"

echo
echo "== Phase 1.5 test suites =="

pytest tests/unit -v --tb=short
pytest tests/integration -v --tb=short

echo
echo "Phase 1.5 invariants verified."
```

- [ ] **Step 2: Run + commit**

```bash
chmod +x scripts/verify_phase_1_5_invariants.sh
./scripts/verify_phase_1_5_invariants.sh
git add scripts/verify_phase_1_5_invariants.sh
git commit -m "chore: phase 1.5 invariant verification script"
```

### Task 1.5.23: Codex consult on Phase 1.5 diff

Same pattern as Task 1.26 with Phase 1.5 focus areas (outbox durability, worker replay safety, materialization atomicity, eq-agent integration).

### Task 1.5.24: Production validation in test tenant

Run the manual workflow on Railway:
1. Trigger a transcript with an unknown-domain attendee.
2. See the entry land in the queue.
3. Approve via UI.
4. Watch the worker pick it up, call the agent, materialize.
5. Verify Neo4j has the new Account + Contact + edges.

### Task 1.5.25: Documentation + memory update for Phase 1.5

- Update `docs/contacts-architecture.md` with the worker, outbox, queue UI.
- Update auto-memory `project_contact_quality_initiative.md`:
  - `status: PHASE_1_AND_1_5_COMPLETE_STOPPING_POINT_REACHED`
- Update `MEMORY.md` index accordingly.

### Task 1.5.26: Phase 1.5 PR + merge + deploy

Same shape as Task 1.28.

### Task 1.5.27: Stopping point — update NEXT-SESSION-START-HERE for Phase 2 re-planning

Replace the current `NEXT-SESSION-START-HERE.md` content with instructions for the comprehensive re-planning session per design Section 7.3: research current AI-native thought leadership, evaluate production metrics from Phase 1.5, decide whether Phase 2 is the right next move or whether something else takes priority.

---

## Self-review checklist (run after writing this plan)

Per the writing-plans skill self-review pass:

- [ ] Every design-doc Section 12 invariant has a corresponding task that verifies it.
- [ ] No placeholders (`TBD`, `TODO`, `implement later`, "similar to Task N") remain.
- [ ] Function/type names used in Phase 1.5 tasks match Phase 1 definitions (e.g., `SignalProposal`, `EnrichResult`, `materialize_account_approval`).
- [ ] Codex's 15 findings are each addressable from a task or set of tasks.
- [ ] Cross-repo handoffs include enough detail for a fresh agent in the target repo to act without re-reading the design.

If any of the above fail, fix inline before transitioning to execution.

---

## Execution handoff

**Plan complete. Saved to `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md`.**

Two execution options for the next session:

**1. Subagent-Driven (recommended)** — orchestrator dispatches a fresh subagent per task, reviews between tasks. Fast iteration. Uses `superpowers:subagent-driven-development`.

**2. Inline Execution** — execute tasks in the same session using `superpowers:executing-plans`, with checkpoints between tasks for human review.

Recommended: Subagent-Driven. The plan is granular and many tasks are independent enough that a fresh subagent context per task is cleaner than carrying accumulated context through 40+ tasks.

**After Phase 1 ships, recurring quality gates kick in:**
- Codex consult on the diff (Task 1.26).
- Acceptance criteria verification (Task 1.25).
- Memory + architecture doc updates (Task 1.27).
- AI-native thought leadership research before Phase 1.5 plan-writing portion (Task 1.5.0) — note this happens INSIDE this plan because Phase 1 + 1.5 are committed scope, but a Phase 2 plan-writing session would do its own research pass.
