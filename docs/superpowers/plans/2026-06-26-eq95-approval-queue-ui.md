# EQ-95 Approvals-Queue Dropdown — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a per-user "approvals queue" dropdown in the eq-frontend top nav (next to Canvas) that lists a user's pending unknown-account items and drives Approve/Ignore through the existing backend workflow, with a live dev e2e smoke proving create+enrich works end to end.

**Architecture:** Three slices across two repos. Backend (`live-transcription-fastapi`) adds three owner-scoped READ endpoints to the existing `/queue` router. Frontend (`eq-frontend`) adds a tRPC router (cloned from `granola.ts`) + a Popover button/panel inserted into the five top bars. Read and write both go through the backend (never Prisma). An index migration lands in eq-frontend (the schema-owner repo). Then a dev-only e2e smoke.

**Tech Stack:** FastAPI + SQLAlchemy `text()` raw SQL + asyncpg (backend); Next.js App Router + tRPC v11 (superjson) + React Query + shadcn/Radix Popover + Tailwind (frontend); pytest (mock-driven) backend tests; Neon Postgres.

**Spec:** `docs/superpowers/specs/2026-06-26-eq95-approval-queue-ui-design.md` (read it first — this plan implements it).

## Global Constraints

- **Dev only.** Never address anything in the Railway `eq-prod` project. The smoke runs against dev (`live-transcription-fastapi` in project `inspiring-upliftment`; dev `eq-agent-action-core`; dev Neon).
- **No RLS on `pending_account_mappings`.** App-level `WHERE tenant_id=… AND owner_user_id=…` is the entire security boundary — it must be in the SQL of every read query, never deferred to Python.
- **JWT-only reads.** Require a UUID-shaped `pg_user_id` (no fallback to `user_id`). Legacy header auth never sets `pg_user_id`, so requiring it rejects spoofable `X-Tenant-ID`/`X-User-ID` headers.
- **SQL bind style:** always `CAST(:name AS uuid)`, never `:name::uuid` (SQLAlchemy 2.0.49 bindname truncation — `queue_actions.py:195-198`).
- **Completion signal:** terminal success = `status='mapped' AND resolved_account_id IS NOT NULL`. Never treat a non-null `resolved_account_id` alone as done (reopened rows carry stale values — `pending_account_mappings.py:54-74` TODO).
- **Pending-only:** list + count filter `status='pending' AND archived_at IS NULL`.
- **Feature flag:** the whole UI gates on `NEXT_PUBLIC_APPROVALS_QUEUE_ENABLED === 'true'` (client) + a server FORBIDDEN guard.
- **V1 context = calendar branch only.** Render `meetingTitle/occurredAt/attendeeCount` only when a `calendar_events` row resolves; otherwise fall back to domain + contacts (`contextSource='none'`). Email + `interaction_summary` branches are deferred (V1.1).
- **Map action is NOT built** (no committed plan). Approve + Ignore only.

---

## File Structure

**Backend — `live-transcription-fastapi` (modify one file + one test file):**
- Modify: `routers/queue_actions.py` — add `_require_owner_identity()` helper, three SQL constants (`COUNT_QUEUE_SQL`, `LIST_QUEUE_SQL`, `STATUS_QUEUE_SQL`), and three handlers (`GET /queue/count`, `GET /queue`, `GET /queue/{id}`). Register `/queue/count` before `/queue/{id}`.
- Test: `tests/integration/test_queue_read.py` (new) — mock-driven handler tests + SQL-text contract tests, mirroring `tests/integration/test_queue_lifecycle.py`.
- No `main.py` change (routes attach to the already-mounted `queue_actions.router`).

**Index — `eq-frontend` (schema-owner):**
- Create: `migrations/2026-06-26-eq95-pending-account-mappings-owner-index.sql` (or the repo's coordinated-DDL location — confirm in Task I1).

**Frontend — `eq-frontend` (worktree):**
- Create: `lib/trpc/routers/approvals-queue.ts` (clone `granola.ts`).
- Modify: `lib/trpc/routers/_app.ts` (register `approvalsQueue`).
- Create: `components/eq/approvals/approvals-queue-button.tsx` (Popover trigger + badge).
- Create: `components/eq/approvals/approvals-queue-panel.tsx` (list + actions + provisioning poll).
- Modify: `components/eq/layouts/home-top-bar.tsx`, `account-top-bar.tsx`, `pipeline-top-bar.tsx`, `trends-top-bar.tsx`, `app/(workspace)/intelligence/layout.tsx` (insert the button).

**E2E — `live-transcription-fastapi`:**
- Create: `scripts/eq95_smoke.py` (dev-only script-first smoke).

---

## PHASE A — Backend read API (`live-transcription-fastapi`, this repo)

> Mock-driven tests, mirroring `tests/integration/test_queue_lifecycle.py`. Run: `python -m pytest tests/integration/test_queue_read.py -q` (no DB needed). Branch first: `git checkout -b eq-95/approval-queue-ui`.

### Task A1: Shared read-auth helper + `GET /queue/count`

**Files:**
- Modify: `routers/queue_actions.py` (add helper + SQL + handler near the other handlers)
- Test: `tests/integration/test_queue_read.py` (new)

**Interfaces:**
- Produces: `_require_owner_identity(ctx) -> str` (UUID owner id or raises 403); `COUNT_QUEUE_SQL`; `GET /queue/count` → `{"count": int}`.

- [ ] **Step 1: Write the failing tests** — create `tests/integration/test_queue_read.py`. Copy the harness scaffolding verbatim from `tests/integration/test_queue_lifecycle.py` lines 37–196 (the `INTERNAL_JWT_*` env setup BEFORE importing main, `_make_jwt`, `_fake_queue_row`, `_execute_result`, `_SessionStub`, `_SessionCM`, `_patch_session`, `client` fixture). Then add:

```python
import uuid
from unittest.mock import MagicMock

PG_USER_UUID = "33333333-3333-4333-8333-333333333333"


def test_count_rejects_missing_jwt(client):
    """No Authorization header → 401."""
    r = client.get("/queue/count")
    assert r.status_code == 401, r.text


def test_count_requires_pg_user_id(client):
    """JWT without pg_user_id → 403 (JWT-only / unambiguous owner)."""
    token = _make_jwt(pg_user_id=None)  # mints tenant_id + user_id only
    r = client.get("/queue/count", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403, r.text


def test_count_happy_path(client):
    session = _SessionStub(execute_results=[_execute_result(scalar_one=7)])
    with _patch_session(session):
        r = client.get(
            "/queue/count",
            headers={"Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}"},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"count": 7}
    # owner + tenant + pending filter must be bound
    params = session.calls[0][1]
    assert params["owner_user_id"] == PG_USER_UUID


def test_count_sql_is_owner_scoped_and_pending():
    """Contract: the count SQL must filter owner, tenant, archived, pending."""
    from routers.queue_actions import COUNT_QUEUE_SQL
    sql = str(COUNT_QUEUE_SQL)
    assert "owner_user_id = CAST(:owner_user_id AS uuid)" in sql
    assert "tenant_id = CAST(:tenant_id AS uuid)" in sql
    assert "archived_at IS NULL" in sql
    assert "status = 'pending'" in sql


def test_count_rejects_legacy_header_auth(client, monkeypatch):
    """Even with legacy header auth ENABLED, spoofed X-* headers (no JWT) are denied.
    Legacy auth never populates pg_user_id, so the owner-identity guard rejects it —
    this pins the no-RLS read surface to JWT-only."""
    monkeypatch.setenv("ALLOW_LEGACY_HEADER_AUTH", "true")
    r = client.get("/queue/count", headers={
        "X-Tenant-ID": "11111111-1111-4111-8111-111111111111",
        "X-User-ID": "b0000000-0000-4000-8000-000000000002",
    })
    # 403 is the owner-guard path (legacy ctx has no pg_user_id). If the legacy
    # header set is incomplete it may 400/401 — either way, spoofed headers grant
    # NO access. The security property under test is "not 200".
    assert r.status_code in (400, 401, 403), r.text
```

Note: confirm `_make_jwt`'s signature supports `pg_user_id=None`; in `test_queue_lifecycle.py` it is `_make_jwt(user_id=..., tenant_id=..., pg_user_id=...)`. If `_execute_result` lacks a `scalar_one` kwarg, the count handler should instead read `.one().count`; adjust the stub/handler to agree (use `.scalar_one()` in the handler and pass `scalar_one=7`).

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/integration/test_queue_read.py -q`
Expected: FAIL (`COUNT_QUEUE_SQL` / route not defined → ImportError or 404).

- [ ] **Step 3: Implement the helper + SQL + handler** in `routers/queue_actions.py`. Add near the other module-level SQL constants:

```python
COUNT_QUEUE_SQL = text("""
    SELECT count(*) AS count
    FROM pending_account_mappings
    WHERE tenant_id = CAST(:tenant_id AS uuid)
      AND owner_user_id = CAST(:owner_user_id AS uuid)
      AND archived_at IS NULL
      AND status = 'pending'
""")
```

Add the helper (near `_effective_user_id`):

```python
def _require_owner_identity(ctx) -> str:
    """Owner identity for READ endpoints: a UUID-shaped pg_user_id.

    JWT-only enforcement without an auth-method flag: legacy header auth never
    populates pg_user_id (utils/context_utils.py), so requiring it here rejects
    spoofable X-Tenant-ID/X-User-ID headers on this non-RLS table. No fallback
    to user_id (the Auth0 sub) — owner_user_id is a pg UUID.
    """
    owner = getattr(ctx, "pg_user_id", None)
    if not owner:
        raise HTTPException(status_code=403, detail="pg_user_id claim required")
    try:
        uuid.UUID(owner)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=403, detail="pg_user_id must be UUID-shaped")
    return owner
```

Add the handler (place this BEFORE any `GET /{queue_id}` route so `/count` is matched as a static path):

```python
@router.get("/count")
async def queue_count(request: Request):
    ctx = get_auth_context_polling(request)
    owner_id = _require_owner_identity(ctx)
    async with get_async_session() as session:
        result = await session.execute(
            COUNT_QUEUE_SQL,
            {"tenant_id": ctx.tenant_id, "owner_user_id": owner_id},
        )
        count = result.scalar_one()
    return {"count": int(count)}
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `python -m pytest tests/integration/test_queue_read.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add routers/queue_actions.py tests/integration/test_queue_read.py
git commit -m "feat(EQ-95): owner-scoped GET /queue/count + JWT-only read guard"
```

---

### Task A2: `GET /queue/{id}` (single-row status read)

**Files:**
- Modify: `routers/queue_actions.py`
- Test: `tests/integration/test_queue_read.py`

**Interfaces:**
- Produces: `STATUS_QUEUE_SQL`; `GET /queue/{queue_id}` → `{queueId, domain, status, resolvedAccountId}`. Completion = `status=='mapped' and resolvedAccountId is not None`.

- [ ] **Step 1: Write the failing tests** (append):

```python
def test_status_rejects_bad_uuid(client):
    token = _make_jwt(pg_user_id=PG_USER_UUID)
    r = client.get("/queue/not-a-uuid", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400, r.text


def test_status_not_found_returns_404(client):
    session = _SessionStub(execute_results=[_execute_result(one_or_none=None)])
    qid = str(uuid.uuid4())
    with _patch_session(session):
        r = client.get(
            f"/queue/{qid}",
            headers={"Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}"},
        )
    assert r.status_code == 404, r.text


def test_status_happy_path_pending(client):
    qid = str(uuid.uuid4())
    row = MagicMock(queue_id=qid, domain="acme.com", status="pending",
                    resolved_account_id=None)
    session = _SessionStub(execute_results=[_execute_result(one_or_none=row)])
    with _patch_session(session):
        r = client.get(
            f"/queue/{qid}",
            headers={"Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"queueId": qid, "domain": "acme.com",
                    "status": "pending", "resolvedAccountId": None}


def test_status_sql_is_owner_and_tenant_scoped():
    from routers.queue_actions import STATUS_QUEUE_SQL
    sql = str(STATUS_QUEUE_SQL)
    assert "id = CAST(:queue_id AS uuid)" in sql
    assert "tenant_id = CAST(:tenant_id AS uuid)" in sql
    assert "owner_user_id = CAST(:owner_user_id AS uuid)" in sql
```

- [ ] **Step 2: Run tests, verify they fail.** `python -m pytest tests/integration/test_queue_read.py -q` → FAIL.

- [ ] **Step 3: Implement.** Add SQL + handler (handler registered AFTER `/count`):

```python
STATUS_QUEUE_SQL = text("""
    SELECT id::text AS queue_id,
           domain,
           status,
           resolved_account_id::text AS resolved_account_id
    FROM pending_account_mappings
    WHERE id = CAST(:queue_id AS uuid)
      AND tenant_id = CAST(:tenant_id AS uuid)
      AND owner_user_id = CAST(:owner_user_id AS uuid)
""")


@router.get("/{queue_id}")
async def queue_status(queue_id: str, request: Request):
    ctx = get_auth_context_polling(request)
    _validate_uuid_path_param(queue_id, "queue_id")
    owner_id = _require_owner_identity(ctx)
    async with get_async_session() as session:
        row = (await session.execute(
            STATUS_QUEUE_SQL,
            {"queue_id": queue_id, "tenant_id": ctx.tenant_id, "owner_user_id": owner_id},
        )).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Queue entry not found")
    return {
        "queueId": row.queue_id,
        "domain": row.domain,
        "status": row.status,
        "resolvedAccountId": row.resolved_account_id,
    }
```

- [ ] **Step 4: Run tests, verify they pass.** Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add routers/queue_actions.py tests/integration/test_queue_read.py
git commit -m "feat(EQ-95): owner-scoped GET /queue/{id} status read"
```

---

### Task A3: `GET /queue` (batched list + calendar context)

**Files:**
- Modify: `routers/queue_actions.py`
- Test: `tests/integration/test_queue_read.py`

**Interfaces:**
- Produces: `LIST_QUEUE_SQL`; `GET /queue` → `{"entries": [Entry]}` where `Entry = {queueId, domain, status, sourceType, createdAt, expiresAt, reOpenCount, contactCount, contacts:[{email,displayName,role}], contextSource, meetingTitle, occurredAt, attendeeCount}`.

- [ ] **Step 1: Write the failing tests** (append). Mock the list row shape the handler reads:

```python
def test_list_rejects_missing_jwt(client):
    assert client.get("/queue").status_code == 401


def test_list_requires_pg_user_id(client):
    token = _make_jwt(pg_user_id=None)
    assert client.get("/queue", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_list_happy_path(client):
    qid = str(uuid.uuid4())
    row = MagicMock(
        queue_id=qid, domain="acme.com", status="pending", source_type="transcript",
        created_at="2026-06-26T00:00:00+00:00", expires_at="2026-07-26T00:00:00+00:00",
        re_open_count=0, contact_count=2,
        contacts=[{"email": "jane@acme.com", "displayName": "Jane", "role": "VP"}],
        context_source="calendar", meeting_title="Q3 Sync",
        occurred_at="2026-06-20T10:00:00+00:00", attendee_count=3,
    )
    session = _SessionStub(execute_results=[_execute_result(all_rows=[row])])
    with _patch_session(session):
        r = client.get("/queue", headers={"Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}"})
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    assert len(entries) == 1
    e = entries[0]
    assert e["queueId"] == qid and e["domain"] == "acme.com"
    assert e["contactCount"] == 2 and e["contextSource"] == "calendar"
    assert e["meetingTitle"] == "Q3 Sync"


def test_list_sql_is_owner_scoped_pending_and_batched():
    from routers.queue_actions import LIST_QUEUE_SQL
    sql = str(LIST_QUEUE_SQL)
    assert "owner_user_id = CAST(:owner_user_id AS uuid)" in sql
    assert "tenant_id = CAST(:tenant_id AS uuid)" in sql
    assert "archived_at IS NULL" in sql
    assert "status = 'pending'" in sql
    assert "LIMIT" in sql
    # batched: contacts aggregated, not per-row fetched
    assert "jsonb_agg" in sql or "json_agg" in sql
    # NO email/interaction_summary branch in V1 — calendar only
    assert "pending_interactions" not in sql
```

- [ ] **Step 2: Run tests, verify they fail.**

- [ ] **Step 3: Implement.** Add the batched SQL (one statement; CTE + aggregate; calendar branch only for V1):

```python
LIST_QUEUE_SQL = text("""
    WITH q AS (
        SELECT id, domain, status, discovered_from_type,
               created_at, expires_at, re_open_count
        FROM pending_account_mappings
        WHERE tenant_id = CAST(:tenant_id AS uuid)
          AND owner_user_id = CAST(:owner_user_id AS uuid)
          AND archived_at IS NULL
          AND status = 'pending'
        ORDER BY created_at DESC
        LIMIT :limit
    ),
    sig AS (  -- representative source + calendar anchor per queue (deterministic tie-break on s.id)
        SELECT s.queue_id,
               (array_agg(s.source_type ORDER BY s.created_at, s.id))[1] AS source_type,
               (array_agg(s.calendar_event_id ORDER BY s.created_at, s.id)
                  FILTER (WHERE s.calendar_event_id IS NOT NULL))[1] AS calendar_event_id
        FROM pending_account_mapping_signals s
        JOIN q ON q.id = s.queue_id
        WHERE s.archived_at IS NULL
        GROUP BY s.queue_id
    ),
    contacts AS (  -- dedup by email FIRST, so contact_count and contacts agree
        SELECT queue_id,
               count(*) AS contact_count,
               jsonb_agg(jsonb_build_object(
                   'email', contact_email,
                   'displayName', contact_display_name,
                   'role', contact_role) ORDER BY contact_email) AS contacts
        FROM (
            SELECT DISTINCT ON (s.queue_id, s.contact_email)
                   s.queue_id, s.contact_email, s.contact_display_name, s.contact_role
            FROM pending_account_mapping_signals s
            JOIN q ON q.id = s.queue_id
            WHERE s.archived_at IS NULL
            ORDER BY s.queue_id, s.contact_email, s.created_at, s.id
        ) d
        GROUP BY queue_id
    )
    SELECT q.id::text AS queue_id,
           q.domain,
           q.status,
           COALESCE(sig.source_type, q.discovered_from_type) AS source_type,
           q.created_at,
           q.expires_at,
           q.re_open_count,
           COALESCE(contacts.contact_count, 0) AS contact_count,
           COALESCE(contacts.contacts, '[]'::jsonb) AS contacts,
           CASE WHEN ce.id IS NOT NULL THEN 'calendar' ELSE 'none' END AS context_source,
           ce.title AS meeting_title,
           ce.start_time AS occurred_at,
           att.attendee_count
    FROM q
    LEFT JOIN sig ON sig.queue_id = q.id
    LEFT JOIN contacts ON contacts.queue_id = q.id
    LEFT JOIN calendar_events ce
           ON ce.id = sig.calendar_event_id
          AND ce.tenant_id = CAST(:tenant_id AS uuid)   -- tenant-scope: no-RLS surface
    LEFT JOIN LATERAL (
        SELECT count(*) AS attendee_count
        FROM calendar_event_attendees a
        WHERE a.calendar_event_id = sig.calendar_event_id
          AND a.is_resource = false
    ) att ON ce.id IS NOT NULL   -- depend on the RESOLVED calendar row, not just the id
    ORDER BY q.created_at DESC
""")
```

Add the handler (after `/count`, before `/{queue_id}` is fine since `/queue` (empty path) and `/queue/{id}` don't collide; but keep `/count` first):

```python
QUEUE_LIST_LIMIT = 50


@router.get("")
async def queue_list(request: Request):
    ctx = get_auth_context_polling(request)
    owner_id = _require_owner_identity(ctx)
    async with get_async_session() as session:
        rows = (await session.execute(
            LIST_QUEUE_SQL,
            {"tenant_id": ctx.tenant_id, "owner_user_id": owner_id, "limit": QUEUE_LIST_LIMIT},
        )).all()
    return {"entries": [
        {
            "queueId": r.queue_id,
            "domain": r.domain,
            "status": r.status,
            "sourceType": r.source_type,
            "createdAt": r.created_at,
            "expiresAt": r.expires_at,
            "reOpenCount": r.re_open_count,
            "contactCount": int(r.contact_count),
            "contacts": r.contacts,
            "contextSource": r.context_source,
            "meetingTitle": r.meeting_title,
            "occurredAt": r.occurred_at,
            "attendeeCount": r.attendee_count,
        }
        for r in rows
    ]}
```

> Note: `@router.get("")` on a prefixed router serves `GET /queue`. If the test client needs a trailing slash, also confirm FastAPI's `redirect_slashes` default; the tests above call `/queue` (no slash) which matches `@router.get("")`.

- [ ] **Step 4: Validate the SQL against live (dev) schema** before trusting it — this is the riskiest SQL in the plan:

Run: `python scripts/verify_schema.py --sql-text "$(python -c "from routers.queue_actions import LIST_QUEUE_SQL; print(str(LIST_QUEUE_SQL))")"`
(Point `DATABASE_URL` at a Neon **dev/test branch**, not prod.) Expected: PREPARE/EXPLAIN succeeds (no missing column/table). Fix any column-name drift before continuing.

- [ ] **Step 5: Run tests, verify they pass.** `python -m pytest tests/integration/test_queue_read.py -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add routers/queue_actions.py tests/integration/test_queue_read.py
git commit -m "feat(EQ-95): owner-scoped GET /queue list with batched calendar context"
```

---

## PHASE I — Index migration (`eq-frontend`, schema-owner repo)

### Task I1: Add the owner-scoped index

**Files:**
- Create: `eq-frontend/migrations/2026-06-26-eq95-pending-account-mappings-owner-index.sql` (confirm the repo's coordinated-DDL convention first — it may be a `prisma/migrations/` SQL or a raw `migrations/` file).

- [ ] **Step 1: Confirm the DDL location.** In `eq-frontend`, check how prior `pending_account_mappings` columns were added (grep migrations + `prisma/migrations/` for `pending_account_mappings`). Match that convention.

- [ ] **Step 2: Validate the index SQL against dev** from `live-transcription-fastapi`:

Run: `python scripts/verify_schema.py --sql-text "CREATE INDEX IF NOT EXISTS idx_pending_account_mappings_owner_pending ON pending_account_mappings (tenant_id, owner_user_id, created_at DESC) WHERE archived_at IS NULL AND status = 'pending'"`
Expected: valid (table/columns exist).

- [ ] **Step 3: Write the migration file:**

```sql
-- EQ-95: owner-scoped reads for the approvals-queue dropdown.
-- Partial + created_at DESC matches the hot query exactly:
-- GET /queue + /queue/count filter (tenant_id, owner_user_id) WHERE archived_at IS NULL
-- AND status='pending', ORDER BY created_at DESC.
CREATE INDEX IF NOT EXISTS idx_pending_account_mappings_owner_pending
    ON pending_account_mappings (tenant_id, owner_user_id, created_at DESC)
    WHERE archived_at IS NULL AND status = 'pending';
```

- [ ] **Step 4: Apply to dev** (per the repo's DDL apply convention; do NOT touch prod). Verify with `\d pending_account_mappings` that the index exists.

- [ ] **Step 5: Commit** (in eq-frontend, on its own branch):

```bash
git add migrations/2026-06-26-eq95-pending-account-mappings-owner-index.sql
git commit -m "feat(EQ-95): index pending_account_mappings (tenant_id, owner_user_id, archived_at)"
```

---

## PHASE C — Frontend (`eq-frontend` worktree)

> Create the worktree via superpowers:using-git-worktrees (the checkout is shared and busy). First read the repo's component-test setup (`package.json` test script + an existing `*.test.tsx`) and mirror it for the component tests below. Clone sources: `lib/trpc/routers/granola.ts`, `components/eq/layouts/sidebar.tsx` (`AgentQueueBadge`), `app/(workspace)/agent-queue/page.tsx`.

### Task C1: `approvals-queue` tRPC router + registration + feature gate

**Files:**
- Create: `lib/trpc/routers/approvals-queue.ts`
- Modify: `lib/trpc/routers/_app.ts`

**Interfaces:**
- Produces: `approvalsQueueRouter` with `list`, `count`, `status(input:{queueId})`, `approve(input:{queueId, approvalAttemptId})`, `ignore(input:{queueId})`. All `protectedProcedure`, all `callBackend` proxies to `gatewayConfig.transcriptionServiceUrl` at the default audience.

- [ ] **Step 1: Write the router** `lib/trpc/routers/approvals-queue.ts` (clone the `granola.ts` shape verbatim — imports, `baseUrl()`, `unwrap`, `backendErrorCode`, `buildAuthContext`):

```ts
import { z } from 'zod'
import { TRPCError } from '@trpc/server'

import { router, protectedProcedure } from '../init'
import { callBackend, type BackendResponse } from '@/lib/gateway-forward'
import { gatewayConfig } from '@/lib/gateway-config'
import { buildAuthContext } from '@/lib/gateway-auth-context'

const APPROVALS_ENABLED = process.env.NEXT_PUBLIC_APPROVALS_QUEUE_ENABLED === 'true'

function assertApprovalsEnabled(): void {
  if (!APPROVALS_ENABLED) {
    throw new TRPCError({ code: 'FORBIDDEN', message: 'Approvals queue is not enabled.' })
  }
}

function baseUrl(): string {
  return gatewayConfig.transcriptionServiceUrl
}

function backendErrorCode(status: number): TRPCError['code'] {
  switch (status) {
    case 400: return 'BAD_REQUEST'
    case 401: return 'UNAUTHORIZED'
    case 403: return 'FORBIDDEN'
    case 404: return 'NOT_FOUND'
    case 409: return 'CONFLICT'
    case 429: return 'TOO_MANY_REQUESTS'
    default: return 'INTERNAL_SERVER_ERROR'
  }
}

function unwrap<T>(resp: BackendResponse<T>): T {
  if (!resp.ok) {
    throw new TRPCError({ code: backendErrorCode(resp.status), message: `Approvals backend error (HTTP ${resp.status}).` })
  }
  return resp.data
}

export interface ApprovalContact { email: string; displayName: string | null; role: string | null }
export interface ApprovalEntry {
  queueId: string; domain: string; status: string; sourceType: string
  createdAt: string; expiresAt: string; reOpenCount: number
  contactCount: number; contacts: ApprovalContact[]
  contextSource: 'calendar' | 'none'
  meetingTitle: string | null; occurredAt: string | null; attendeeCount: number | null
}
export interface ApprovalListResponse { entries: ApprovalEntry[] }
export interface ApprovalCountResponse { count: number }
export interface ApprovalStatusResponse { queueId: string; domain: string; status: string; resolvedAccountId: string | null }

export const approvalsQueueRouter = router({
  count: protectedProcedure.query(async ({ ctx }): Promise<ApprovalCountResponse> => {
    assertApprovalsEnabled()
    return unwrap(await callBackend<ApprovalCountResponse>({
      method: 'GET', url: `${baseUrl()}/queue/count`, authContext: buildAuthContext(ctx),
    }))
  }),

  list: protectedProcedure.query(async ({ ctx }): Promise<ApprovalListResponse> => {
    assertApprovalsEnabled()
    return unwrap(await callBackend<ApprovalListResponse>({
      method: 'GET', url: `${baseUrl()}/queue`, authContext: buildAuthContext(ctx),
    }))
  }),

  status: protectedProcedure
    .input(z.object({ queueId: z.string().uuid() }))
    .query(async ({ ctx, input }): Promise<ApprovalStatusResponse> => {
      assertApprovalsEnabled()
      return unwrap(await callBackend<ApprovalStatusResponse>({
        method: 'GET', url: `${baseUrl()}/queue/${input.queueId}`, authContext: buildAuthContext(ctx),
      }))
    }),

  approve: protectedProcedure
    .input(z.object({ queueId: z.string().uuid(), approvalAttemptId: z.string().uuid() }))
    .mutation(async ({ ctx, input }): Promise<{ status: string }> => {
      assertApprovalsEnabled()
      return unwrap(await callBackend<{ status: string }>({
        method: 'POST', url: `${baseUrl()}/queue/${input.queueId}/approve`,
        body: { approval_attempt_id: input.approvalAttemptId },
        authContext: buildAuthContext(ctx),
      }))
    }),

  ignore: protectedProcedure
    .input(z.object({ queueId: z.string().uuid() }))
    .mutation(async ({ ctx, input }): Promise<{ status: string }> => {
      assertApprovalsEnabled()
      return unwrap(await callBackend<{ status: string }>({
        method: 'POST', url: `${baseUrl()}/queue/${input.queueId}/ignore`, body: {},
        authContext: buildAuthContext(ctx),
      }))
    }),
})
```

- [ ] **Step 2: Register** in `lib/trpc/routers/_app.ts` — add `import { approvalsQueueRouter } from './approvals-queue'` near the other imports and `approvalsQueue: approvalsQueueRouter,` inside the `router({ ... })` block.

- [ ] **Step 3: Typecheck.** Run: `pnpm tsc --noEmit` (or the repo's typecheck script). Expected: no errors; `AppRouter` now exposes `approvalsQueue`.

- [ ] **Step 4: Commit**

```bash
git add lib/trpc/routers/approvals-queue.ts lib/trpc/routers/_app.ts
git commit -m "feat(EQ-95): approvalsQueue tRPC router (backend proxy)"
```

---

### Task C2: `ApprovalsQueueButton` (Popover trigger + count badge)

**Files:**
- Create: `components/eq/approvals/approvals-queue-button.tsx`

**Interfaces:**
- Produces: `<ApprovalsQueueButton />` — a self-contained Popover whose trigger matches the Canvas button style and shows a pending-count badge; renders `<ApprovalsQueuePanel/>` (Task C3) in `PopoverContent`. Returns `null` when the feature flag is off.

- [ ] **Step 1: Implement** (badge mirrors `AgentQueueBadge`; flag-gated):

```tsx
'use client'

import { Inbox } from 'lucide-react'
import { cn } from '@/lib/utils'
import { trpc } from '@/lib/trpc/client'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { ApprovalsQueuePanel } from './approvals-queue-panel'

const APPROVALS_ENABLED = process.env.NEXT_PUBLIC_APPROVALS_QUEUE_ENABLED === 'true'

export function ApprovalsQueueButton() {
  if (!APPROVALS_ENABLED) return null
  return <ApprovalsQueueButtonInner />
}

function ApprovalsQueueButtonInner() {
  const { data } = trpc.approvalsQueue.count.useQuery(undefined, { refetchInterval: 30_000 })
  const count = data?.count ?? 0
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            'relative flex items-center gap-1.5 px-3 py-1 text-xs font-medium',
            'bg-ui-glass-surface-elevated border border-ui-glass-border rounded-md',
            'hover:bg-ui-hover-light transition-all duration-200',
            'text-ui-glass-text-secondary hover:text-ui-glass-text-primary',
          )}
          aria-label="Open approvals queue"
        >
          <Inbox className="w-3.5 h-3.5" />
          Approvals
          {count > 0 && (
            <span className="px-1.5 py-0.5 text-[10px] font-medium bg-ui-blue-500 text-white rounded-full leading-none">
              {count > 99 ? '99+' : count}
            </span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-96 p-0 max-h-[28rem] overflow-hidden">
        <ApprovalsQueuePanel />
      </PopoverContent>
    </Popover>
  )
}
```

- [ ] **Step 2: Typecheck.** `pnpm tsc --noEmit` → no errors (panel import resolves once C3 lands; do C3 first or stub it).

- [ ] **Step 3: Commit**

```bash
git add components/eq/approvals/approvals-queue-button.tsx
git commit -m "feat(EQ-95): approvals-queue nav button with count badge"
```

---

### Task C3: `ApprovalsQueuePanel` (list + Approve/Ignore + in-place provisioning)

**Files:**
- Create: `components/eq/approvals/approvals-queue-panel.tsx`

**Interfaces:**
- Consumes: `trpc.approvalsQueue.{list,status,approve,ignore}`. Renders pending rows; Approve → optimistic "Creating account…" tracked in local state, polls `status` until `status==='mapped' && resolvedAccountId` → "✓ added" → drop + invalidate count; Ignore → drop + invalidate count.

- [ ] **Step 1: Implement** (list/empty/loading mirror the agent-queue page; provisioning poll uses an enabled `status` query per tracked id):

```tsx
'use client'

import { useState, useEffect } from 'react'
import { Inbox, Loader2, CheckCircle2, X, Calendar } from 'lucide-react'
import { toast } from 'sonner'
import { trpc } from '@/lib/trpc/client'
import { Skeleton } from '@/components/eq/ui/skeleton'
import Button from '@/components/eq/ui/button'
import type { ApprovalEntry } from '@/lib/trpc/routers/approvals-queue'

// crypto.randomUUID is available in the browser; used for the idempotency key.
function newAttemptId(): string {
  return crypto.randomUUID()
}

export function ApprovalsQueuePanel() {
  const utils = trpc.useUtils()
  const listQuery = trpc.approvalsQueue.list.useQuery(undefined, { refetchInterval: 15_000 })
  // Snapshot of rows being provisioned. The list is pending-only, so once Approve
  // flips a row off 'pending' the 15s refetch drops it from entries — the snapshot
  // keeps the row rendered (and its status poll alive) until it reaches 'mapped'.
  const [provisioning, setProvisioning] = useState<Record<string, ApprovalEntry>>({})

  const ignore = trpc.approvalsQueue.ignore.useMutation({
    onSuccess: () => { listQuery.refetch(); utils.approvalsQueue.count.invalidate() },
    onError: (e) => toast.error(e.message),
  })

  const approve = trpc.approvalsQueue.approve.useMutation({
    onError: (e, vars) => {
      setProvisioning((p) => { const n = { ...p }; delete n[vars.queueId]; return n })
      toast.error(e.message)
    },
  })

  function startApprove(entry: ApprovalEntry) {
    setProvisioning((p) => ({ ...p, [entry.queueId]: entry }))   // snapshot FIRST
    approve.mutate({ queueId: entry.queueId, approvalAttemptId: newAttemptId() })
  }
  function finish(queueId: string) {                              // poll saw status === 'mapped'
    setProvisioning((p) => { const n = { ...p }; delete n[queueId]; return n })
    listQuery.refetch(); utils.approvalsQueue.count.invalidate()
  }

  // Render the union: provisioning snapshots first, then pending rows not being provisioned.
  const listEntries = (listQuery.data?.entries ?? []).filter((e) => !provisioning[e.queueId])
  const rows = [...Object.values(provisioning), ...listEntries]
  const isLoading = listQuery.isLoading && rows.length === 0

  return (
    <div className="flex flex-col max-h-[28rem]">
      <div className="px-4 py-3 border-b border-ui-glass-border text-sm font-medium">
        Account approvals
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-2">
        {isLoading && (<><Skeleton className="h-16 w-full rounded-ui-card" /><Skeleton className="h-16 w-full rounded-ui-card" /></>)}

        {!isLoading && rows.length === 0 && (
          <div className="flex flex-col items-center justify-center py-10 text-center">
            <Inbox className="h-8 w-8 text-ui-glass-text-secondary opacity-40 mb-2" />
            <p className="text-sm text-ui-glass-text-secondary">No pending approvals</p>
          </div>
        )}

        {rows.map((e) => (
          <ApprovalRow
            key={e.queueId}
            entry={e}
            provisioning={Boolean(provisioning[e.queueId])}
            onApprove={() => startApprove(e)}
            onIgnore={() => ignore.mutate({ queueId: e.queueId })}
            onResolved={() => finish(e.queueId)}
          />
        ))}
      </div>
    </div>
  )
}

function ApprovalRow({
  entry, provisioning, onApprove, onIgnore, onResolved,
}: {
  entry: ApprovalEntry
  provisioning: boolean
  onApprove: () => void
  onIgnore: () => void
  onResolved: () => void
}) {
  const [stalled, setStalled] = useState(false)

  // Stop the spinner after ~2 min (the deferred orphan-approval edge — non-blocking;
  // the admin page is the backstop). Reset when provisioning toggles off.
  useEffect(() => {
    if (!provisioning) { setStalled(false); return }
    const t = setTimeout(() => setStalled(true), 120_000)
    return () => clearTimeout(t)
  }, [provisioning])

  // Poll this row's status only while provisioning (and not yet stalled).
  // React Query v5: if per-query onSuccess is unavailable, move this check into a
  // useEffect keyed on the status query's `data`.
  trpc.approvalsQueue.status.useQuery(
    { queueId: entry.queueId },
    {
      enabled: provisioning && !stalled,
      refetchInterval: 4_000,
      onSuccess: (s) => {
        if (s.status === 'mapped' && s.resolvedAccountId) {
          toast.success(`${entry.domain} added`)
          onResolved()
        }
      },
    },
  )

  return (
    <div className="rounded-ui-card border border-ui-glass-border bg-ui-glass-surface-elevated/30 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-sm font-medium truncate">
            <Calendar className="h-3.5 w-3.5 opacity-60 shrink-0" />
            {entry.domain}
          </div>
          <div className="text-xs text-ui-glass-text-secondary truncate mt-0.5">
            {entry.contextSource === 'calendar' && entry.meetingTitle
              ? `${entry.meetingTitle} · ${entry.attendeeCount ?? 0} attendees`
              : `${entry.contactCount} contact${entry.contactCount === 1 ? '' : 's'}`}
          </div>
          {entry.contacts[0] && (
            <div className="text-[11px] text-ui-glass-text-secondary opacity-70 truncate">
              {entry.contacts[0].email}{entry.contactCount > 1 ? ` +${entry.contactCount - 1}` : ''}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {provisioning ? (
            stalled ? (
              <span className="text-xs text-ui-glass-text-secondary">Still working — check back</span>
            ) : (
              <span className="flex items-center gap-1 text-xs text-ui-glass-text-secondary">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Creating…
              </span>
            )
          ) : (
            <>
              <Button variant="secondary" size="small" leftIcon={<X className="h-3 w-3" />} onClick={onIgnore}>Ignore</Button>
              <Button variant="primary" size="small" leftIcon={<CheckCircle2 className="h-3 w-3" />} onClick={onApprove}>Approve</Button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
```

> Verify-as-you-go: confirm `Button`'s import path/props (`@/components/eq/ui/button`, `variant`/`size`/`leftIcon`) and `Skeleton` match the agent-queue page's usage. **React Query version:** if the repo is on v5 (check `package.json`), per-query `onSuccess` is removed — move the completion check (`status === 'mapped' && resolvedAccountId → onResolved()`) into a `useEffect` keyed on the status query's `data` instead of the `onSuccess` in `ApprovalRow`. **Do not** simplify the panel back to mapping `listQuery` entries directly — the snapshot-union is what keeps a row visible through provisioning (the list is pending-only, so an approved row leaves it); collapsing it reintroduces the dropped-row bug.

- [ ] **Step 2: Typecheck + lint.** `pnpm tsc --noEmit && pnpm lint` → clean.

- [ ] **Step 3: Commit**

```bash
git add components/eq/approvals/approvals-queue-panel.tsx
git commit -m "feat(EQ-95): approvals-queue panel with in-place provisioning poll"
```

---

### Task C4: Insert the button into the five top bars

**Files:**
- Modify: `components/eq/layouts/account-top-bar.tsx`, `pipeline-top-bar.tsx` (via `rightActions` callers OR sibling of `{rightActions}`)
- Modify: `components/eq/layouts/home-top-bar.tsx`, `trends-top-bar.tsx`, `app/(workspace)/intelligence/layout.tsx` (wrap Canvas + new button in a `flex items-center gap-2` div)

- [ ] **Step 1: home-top-bar.tsx** — wrap the Canvas `<button>` (lines 63–83) and the new button in a flex div. Replace the direct child:

```tsx
        <div className="flex items-center gap-2">
          <ApprovalsQueueButton />
          <button type="button" onClick={togglePanel} /* …existing Canvas button unchanged… */>
            {/* …existing Canvas content… */}
          </button>
        </div>
```
Add `import { ApprovalsQueueButton } from '@/components/eq/approvals/approvals-queue-button'` at the top.

- [ ] **Step 2: trends-top-bar.tsx** and **intelligence/layout.tsx** — same wrap pattern (Canvas is a direct child of `justify-between`; wrap it + `<ApprovalsQueueButton />` in `flex items-center gap-2`). Add the import to each.

- [ ] **Step 3: account-top-bar.tsx + pipeline-top-bar.tsx** — these already render `<div className="flex items-center gap-2">{rightActions}<button>Canvas…</button></div>`. Insert `<ApprovalsQueueButton />` as the first child of that div (before `{rightActions}`), and add the import. (Putting it in the component, not via `rightActions`, guarantees it shows on every route that uses these bars regardless of caller.)

- [ ] **Step 4: Verify in the running app.** Start the dev server; confirm the Approvals button renders next to Canvas on `/home`, `/accounts`, `/pipeline`, `/trends`, `/intelligence`, that the badge appears only when count>0, and the panel opens. (Requires `NEXT_PUBLIC_APPROVALS_QUEUE_ENABLED=true` in the dev env.)

- [ ] **Step 5: Commit**

```bash
git add components/eq/layouts/home-top-bar.tsx components/eq/layouts/trends-top-bar.tsx \
        components/eq/layouts/account-top-bar.tsx components/eq/layouts/pipeline-top-bar.tsx \
        app/\(workspace\)/intelligence/layout.tsx
git commit -m "feat(EQ-95): mount approvals button in all five top bars"
```

---

## PHASE D — Dev e2e smoke

### Task D1: Pre-smoke config gate

- [ ] **Step 1:** Confirm the dev `live-transcription-fastapi` (project `inspiring-upliftment`) `AGENT_ACTION_CORE_BASE_URL` and DB URL point at **dev** resources, not `eq-prod`. Use the non-secret-leaking link method (`memory/reference_railway_secret_injection.md`): link a scratch dir, `railway run env | grep -E 'AGENT_ACTION_CORE_BASE_URL|DATABASE_URL'` — confirm hostnames are dev, NOT the `eq-prod` Postgres/agent. STOP if anything points at prod.

### Task D2: Script-first e2e (the workflow proof)

**Files:**
- Create: `scripts/eq95_smoke.py`

- [ ] **Step 1:** Write a dev-only script that, under a disposable test tenant: (a) ingests a transcript **with a matched calendar event** for an unknown business domain (drives the proven calendar branch) via the real dev ingest path; (b) mints a dev JWT (carrying `tenant_id` + `pg_user_id` = the recording user) and calls `GET /queue` — assert the row appears with `domain`, `contextSource='calendar'`, `meetingTitle`; (c) `GET /queue/count` == expected; (d) `POST /queue/{id}/approve` with a fresh `approval_attempt_id`; (e) poll `GET /queue/{id}` until `status='mapped' AND resolvedAccountId` (timeout ~120s); (f) assert the `accounts` + `account_domains` rows + contact link exist; (g) **atomic cleanup** (LOCKED-11 teardown — delete the test tenant's pending/account/contact rows + cancel any DBOS workflow).
- [ ] **Step 2:** Run it against dev. Expected: all asserts pass; cleanup leaves no residue.
- [ ] **Step 3: Commit**

```bash
git add scripts/eq95_smoke.py
git commit -m "test(EQ-95): dev-only script-first e2e smoke for the approvals workflow"
```

### Task D3: UI click-through

- [ ] **Step 1:** With the dev frontend (flag on) logged in as the test user, ingest one more unknown-domain transcript, open the Approvals dropdown, confirm the row renders, click **Approve**, watch "Creating… → ✓ added", and confirm the badge decrements. Clean up the test data.

---

## Self-Review (run after writing — completed)

- **Spec coverage:** §5 (3 endpoints) → A1–A3; auth hardening (JWT-only, pg_user_id, owner-in-SQL) → A1 helper + every SQL; batched query → A3; completion predicate → A2 + C3; index R4 → I1; frontend §6 → C1–C4; feature flag → C1/C2; data flow §7 → C3; e2e §9 → D1–D3. Deferred (Map, email/interaction_summary branches, pagination) intentionally excluded per spec.
- **Type consistency:** `ApprovalEntry`/`ApprovalContact` shapes in C1 match the backend `GET /queue` JSON keys in A3 (camelCase: `queueId`, `contactCount`, `contextSource`, `meetingTitle`). Completion predicate identical in A2/C3 (`status==='mapped' && resolvedAccountId`).
- **Open verify-first (carry into execution):** email-branch `interaction_id` (deferred, not in V1 SQL); dev↔prod schema parity (structure stated identical); the React Query major version (affects C3's `onSuccess`).

---

## Execution Notes
- **Backend (Phase A) executes in this repo/session.** Frontend (Phase C) executes in an `eq-frontend` worktree. Index (Phase I) is an eq-frontend DDL change. E2E (Phase D) spans both, dev only.
- **`/codex consult` on this plan: DONE 2026-06-26** (resumed session). 2 P1s + 6 P2s folded in: C3 snapshot-union (provisioning rows survive the pending-only list refetch); `LIST_QUEUE_SQL` `att ON ce.id IS NOT NULL` (no stray attendee_count when context='none'); tenant-scoped `calendar_events` join; email-deduped contacts (`DISTINCT ON`); deterministic tie-break (`created_at, id`); legacy-auth rejection test; partial index; C3 stall timeout. Codex confirmed (no change): route shape (`@router.get("")` serves `/queue`; static `/count` before dynamic `/{id}`), test-harness reuse (`_make_jwt(pg_user_id=None)`, `_execute_result(scalar_one=/all_rows=)` all exist), the completion predicate, and the index-in-eq-frontend call.
