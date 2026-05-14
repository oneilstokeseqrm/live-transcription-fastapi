# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative
**Last session:** 2026-05-14 (Phase 1.5 main-scope FOUNDATION — worker code MERGED via PR #12 + post-merge E2E verified)
**Status:** PR #12 MERGED to main at commit `11b3b30` via squash. Railway auto-deploy `c13b0847-bdc0-4a06-afd0-3c4e4c31804d` reached SUCCESS. Production E2E re-run post-merge: **13/13 PASS** against the live endpoint. Worker code (advisory lock, agent HTTP client, atomic materialization, worker poll loop, entrypoint) lives in main but the existing FastAPI Railway service doesn't run `python -m workers` — a new Railway service for that is below in Workstream D.
**This session's job:** Ship the **outbox publisher (Task 1.5.9) + queue actions (Tasks 1.5.10-1.5.11)** and deploy the Railway worker service per `tasks/downstream/railway-phase-1-5-worker.md`. Optional stretch: extend production E2E with worker case once deployment lands.
**Also available:** A dispatch-pattern research note may be at `docs/superpowers/specs/2026-05-14-dispatch-patterns-research.md` (background-dispatched at end of previous session to validate polling-vs-event-driven architecture before Phase 2). If present, read it BEFORE starting the publisher build — it may recommend an alternative dispatch pattern worth considering. If absent, the research subagent didn't complete; the polling architecture in PR #12 is the default.

---

## Critical context (READ FIRST)

1. **PR #12 ships worker code but NOT deployment.** The worker code lands; the Railway service that runs `python -m workers` is created in this session per the brief at `tasks/downstream/railway-phase-1-5-worker.md`. This means until the Railway service exists + the publisher (Task 1.5.9) is running, queue entries that get approved will accumulate `account_provisioning_outbox` rows that nothing reads. No data loss; just no downstream propagation to Neo4j/eq-structured-graph-core.

2. **Outbox publisher is the architecturally most-important piece this session.** Without it, the worker's materialization commits Postgres rows but EventBridge consumers never receive `AccountCreated`/`AccountMapped` events. Ship the publisher BEFORE the Railway worker service goes live (the publisher can run as a second asyncio task in the same `workers/__main__.py` entrypoint per the AI-native research recommendation in commit `bbfe757`).

3. **Codex review during PR #12 surfaced 2 repeated findings worth knowing about:**
   - **Repeated FALSE POSITIVE**: `ON CONFLICT (tenant_id, email)` on `contacts`. Codex thinks no unique constraint exists. The actual schema HAS a UNIQUE INDEX `contacts_tenant_id_email_key` (verified via Neon MCP in Round 2 + Round 6). PostgreSQL's ON CONFLICT inference works with unique indexes per the docs. Codex looks at this repo's `migrations/` only and misses the Prisma-managed unique declaration. **Do not "fix" this if Codex flags it again.**
   - **Repeated PHASING DECISION**: outbox publisher absent. This is Task 1.5.9, deliberately deferred to this session. The plan explicitly multi-session-phases the work.

4. **The user is a non-developer founder.** Make confident technical decisions on dispatch, fix shape, and review judgment. Surface only product/strategic decisions. Work without stopping for clarifying questions; make the reasonable call and continue.

5. **CRITICAL LESSON from this session about destructive operations:** The cross-repo schema agent ran `TRUNCATE ... RESTART IDENTITY CASCADE` against 11 tables to enable the NOT NULL flip. The cascade silently wiped ~6 additional tables (`opportunities`, `opportunity_pipeline`, `pipeline_forecast`, `forecast_snapshots`, `deal_events`, `emails`, several `opportunity_*` analytic tables) because they FK into `accounts`. The user's pipeline-page demo briefly appeared broken. See the new feedback memory at `~/.claude/projects/.../memory/feedback_destructive_ops_blast_radius.md` — **always verify FK cascade chain before any TRUNCATE/CASCADE operation, even on test data.**

---

## What this session does

### Workstream A — ✅ DONE (in prior session)

PR #12 merged to main at commit `11b3b30` via squash at end of prior session. Railway auto-deployed the FastAPI service (`c13b0847-bdc0-4a06-afd0-3c4e4c31804d` SUCCESS). Production E2E re-run post-merge: 13/13 PASS. Worker code is in main but the worker process itself is not yet running — that's Workstream D.

### Workstream B — Outbox publisher (Task 1.5.9)

Plan: `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` lines 3448-3640. The plan's spec is implementable as-is.

**Key design choice (AI-native research finding, validated):** Run the outbox publisher as a **separate asyncio task in the same OS process as the worker**. The threshold for splitting into a separate service is >5 replicas (we're at 1). Update `workers/__main__.py` to launch BOTH `run_worker_loop` and `run_publisher_loop` as concurrent asyncio tasks via `asyncio.gather`.

Acceptance criteria:
- `workers/outbox_publisher.py` exists with `publish_one` + `run_publisher_loop`
- Tests cover: success path (marks `published_at`), failure path (increments `publish_attempts`, sets `last_publish_error`, leaves `published_at NULL`)
- `workers/__main__.py` runs both worker + publisher concurrently
- EventBridge envelope structure follows the existing `services/aws_event_publisher.py` pattern in this repo (Source, DetailType, Detail JSON, EventBusName)

### Workstream C — Queue actions (Tasks 1.5.10-1.5.11)

Plan: `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` lines 3644-3927.

Two pieces:
- **`services/queue_authorization.py`** — `can_act_on_queue_entry(user_id, queue_entry, is_admin)` helper. Tests in `tests/unit/test_queue_authorization.py`. The helper is small (5-10 lines) but architecturally central.
- **`routers/queue_actions.py`** — three HTTP routes (`POST /queue/{id}/approve`, `/map`, `/ignore`). Tests in `tests/integration/test_queue_lifecycle.py`. Idempotency via `approval_attempt_id`. Authorization via the helper.

Pre-register in `main.py` (`app.include_router(queue_actions.router)`).

The Map route inline-materializes via `materialize_account_approval(event_type="account_mapped")`. The Approve route transitions to `status='approved'` and lets the worker pick it up.

### Workstream D — Railway worker service deployment

Per `tasks/downstream/railway-phase-1-5-worker.md` — apply the 6-step deployment process via Railway MCP. Coordinate with `eq-agent-action-core`'s production URL + API key (look up via `mcp__railway__service_list`; if no service-to-service auth is set up between worker and agent, document the gap and ship without the worker actually running).

After deployment: smoke-test the materialization path by seeding an `approved` queue entry via Neon MCP and watching for the materialized account + outbox row + published_at within ~30 seconds (worker poll + publisher poll).

### Workstream E (stretch) — Production E2E extension

Once the worker is running, extend `/tmp/e2e_phase_1_production.py` with a worker case per the plan's "Phase 1.5 Production E2E Discipline" section: seed approved queue entry, wait for materialization, assert account + outbox row + published_at.

---

## Read these in order before doing any work

1. **Auto-loaded:** `MEMORY.md` — expect `PHASE_1.5_WORKER_FOUNDATION_SHIPPED_PUBLISHER_PENDING`.
2. **Project memory:** `~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/project_contact_quality_initiative.md` — the `## Phase 1.5 main-scope foundation SHIPPED (2026-05-14)` section near the bottom has the full 19-commit list + Codex round history.
3. **The new feedback memory:** `~/.claude/projects/.../memory/feedback_destructive_ops_blast_radius.md` — TRUNCATE/CASCADE discipline.
4. **The plan, Task 1.5.9 forward:** `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` lines 3448-3640 (publisher) + 3644-3927 (queue actions).
5. **The design doc, Section 5.4 + 8.5:** for the outbox publisher architecture.
6. **The deployment brief:** `tasks/downstream/railway-phase-1-5-worker.md`.

---

## Carry-forward invariants (now load-bearing in PR #12)

All from Phase 1 + Phase 1.5 P2 cleanup, plus:

- **Three-layer idempotency:** `worker_attempt_id = f"{tenant_id}:queue-{queue_id}"` (AI-native research recommendation; validated). `outbox_row_id`. `approval_attempt_id`.
- **Per-entry transaction isolation:** each queue entry runs in its OWN session.begin() block. The poll itself runs in a separate session that closes before per-entry sessions start. Avoids SQLAlchemy 2.0 autobegin pitfall.
- **Race-safe placeholder summary via UPSERT:** `ON CONFLICT (interaction_id) DO UPDATE SET updated_at = updated_at RETURNING summary_id`. The no-op UPDATE makes RETURNING fire in both branches.
- **Cross-account contact reassignment is Phase 3 scope.** Materialization fails loud when a contact's existing `account_id` differs from the materialization's input.
- **Archive race check.** Worker re-reads `archived_at` along with `status`; skips when not null (race: archived after poll).
- **Failed-row updated_at bump.** When per-entry txn fails, a separate session bumps `updated_at` so the row rotates to the back of the queue (prevents starvation).
- **TRUNCATE/CASCADE has hidden blast radius.** Always check FK topology via `pg_constraint` / `pg_foreign_keys` before any destructive op. See `feedback_destructive_ops_blast_radius.md`.

---

## Repository state (as of 2026-05-14 end-of-session)

- **Open PR:** PR #12 on `feat/contact-quality-phase-1-5` (this repo) — 19 commits, awaiting review/merge.
- **Cross-repo open PRs:**
  - eq-frontend PR #366 — Phase 1.5 schema migration (layered on top of #349 which is still open).
  - eq-frontend PR #349 — Phase 1 schema (still open; CI failing; schema is LIVE on Neon).
- **Branch:** `feat/contact-quality-phase-1-5` (DO NOT delete until PR #12 merges).
- **Main HEAD at branch creation:** `aa62928`.

- **Neon eq-dev schema (project `super-glitter-11265514`):** All Phase 1 + Phase 1.5 P2 + Phase 1.5 main-scope schema is LIVE. Key Phase 1.5 additions: `accounts.state`, `pending_account_mappings` 5 lifecycle columns, `account_provisioning_outbox` table, `contacts.account_id` NOT NULL, `raw_interactions.account_id` NOT NULL.

- **Test data state:** TRUNCATE wiped 11 explicit tables + ~6 cascade tables. The pipeline page demo, opportunity views, etc. will appear empty until reseeded. **This is the user-impacting fallout from this session; reseeding may be needed.**

- **Production credentials:** Same as prior sessions. Railway service `59a69f3d-9a24-4041-942a-891c4a81c5fb` (the FastAPI service); production URL `https://live-transcription-fastapi-production.up.railway.app`. INTERNAL_JWT_SECRET pullable via Railway MCP.

- **eq-agent-action-core endpoint:** Look up via `mcp__railway__service_list` — the agent is production-deployed for onboarding; reuse its existing auth pattern.

---

## Pre-flight checks (run BEFORE any code work)

These are non-negotiable verifications, because the user is running a database cleanup with a different agent between sessions.

### Check 1: Test tenant still exists in Neon eq-dev

```python
# Via mcp__neon__run_sql
SELECT id, name FROM tenants WHERE id = '11111111-1111-4111-8111-111111111111';
```

Expected: one row. If empty, the cleanup agent removed the test tenant — STOP and seed it before any other database work:

```sql
INSERT INTO tenants (id, name, created_at, updated_at)
VALUES ('11111111-1111-4111-8111-111111111111', 'EQ Test Tenant', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
```

### Check 2: Phase 1.5 schema still intact

```python
# Verify the key new columns + table exist
SELECT column_name FROM information_schema.columns
WHERE table_name = 'contacts' AND column_name = 'account_id' AND is_nullable = 'NO';

SELECT to_regclass('public.account_provisioning_outbox') IS NOT NULL AS exists;
```

Both must return positive. If either fails, the cleanup agent damaged the schema and you must investigate before proceeding (likely Prisma migration drift from eq-frontend).

### Check 3: Production E2E artifact still on disk

```bash
test -f /tmp/e2e_phase_1_production.py && echo "PRESENT" || echo "MISSING"
```

If MISSING (the file lives in `/tmp` which doesn't survive system reboots), you need to recreate it before you can run post-deploy verification. The recreation pattern: a Python script using `httpx` + `pyjwt` that pulls `INTERNAL_JWT_SECRET` via Railway MCP from service `59a69f3d-9a24-4041-942a-891c4a81c5fb`, issues a 5-min JWT with `{tenant_id: test_tenant, user_id: auth0|test-user-001, iss: eq-frontend, aud: eq-backend}` signed with HS256, and exercises 13 cases against `https://live-transcription-fastapi-production.up.railway.app`. Reference: prior session's checkpoint at `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/20260514-070009-phase-1-shipped-handoff-for-phase-1.5.md` describes the 9 original cases; current expanded version has 13. If recreate: copy the structure from `tasks/downstream/codex-phase-1-findings.md` Test plan section.

### Check 4: Production endpoint reachable

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://live-transcription-fastapi-production.up.railway.app/healthz
```

Expected: `200` or `404` (both indicate the service is up; `/healthz` route may not exist but the server responds).

If all four checks pass, proceed. If any fail, STOP and surface to user.

---

## Suggested first actions

1. Run `/context-restore`. Expect a checkpoint titled **"phase-1.5-worker-foundation-merged-publisher-pending"** dated 2026-05-14. Load it. If `NO_CHECKPOINTS`, STOP and investigate.

2. Read this file + `feedback_destructive_ops_blast_radius.md` + `project_contact_quality_initiative.md`'s `## Phase 1.5 main-scope foundation MERGED` section.

3. **IF the dispatch-pattern research note exists** at `docs/superpowers/specs/2026-05-14-dispatch-patterns-research.md`: read it carefully. It may recommend a different dispatch architecture (e.g., Postgres logical replication via EventBridge Pipes, hybrid LISTEN/NOTIFY + polling). If it recommends a course change, surface that as a strategic decision before building the publisher. If it confirms polling is right, proceed with confidence.

4. Implement the outbox publisher (Workstream B) via `superpowers:subagent-driven-development`. Spec the implementer with the plan's lines 3448-3640. Use TDD with mock-driven tests matching the Phase 1.5 P2 + main-scope patterns (no real-DB fixtures yet).

5. Then queue actions (Workstream C). Then Railway worker deployment (Workstream D). Then optionally the E2E extension (Workstream E).

6. At each phase boundary: real `/codex review --base main` — non-substitutable. Apply the lessons learned from this session: cross-check schema P1s against Neon MCP before accepting; stop the spiral when remaining findings are repeats / phasing / verified false-positives.

7. End with `/context-save` (mandatory load-bearing invariant).

---

## Stopping point reminder

After Phase 1.5 main scope ships (publisher + queue actions + UI + deployment all live and validated), there's an explicit STOPPING POINT per the design doc Section 7.3. Re-plan Phase 2 comprehensively before any further commitment. Do not proactively scope Phase 2.

---

## Final note for the next agent

The bones of Phase 1.5 main scope are now in main (after PR #12 merges). The worker code, materialization transaction, advisory lock, agent client, schema — all of it is sound and Codex-verified across 6 rounds. What's missing is the LAST MILE: the publisher that closes the loop on durability, the routes that let users approve/map/ignore, and the Railway service that runs the worker. Each is bounded, testable, well-specified.

The architectural standard remains high. Hold the bar. Run real Codex at every phase boundary. Stop the spiral when remaining findings are repeats. Ship clean.
