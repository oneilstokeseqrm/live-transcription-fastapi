# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative
**Last session:** 2026-05-14 (Phase 1.5 main-scope FOUNDATION — worker code shipped in PR #12)
**Status:** PR #12 open. Worker code, agent client, materialization, advisory lock, entrypoint all landed across 19 commits. Cross-repo eq-frontend PR #366 applied the Phase 1.5 schema migration to Neon eq-dev. Codex review 6 rounds, GATE: PASS. Production E2E **13/13 PASS** against the live endpoint (no regression).
**This session's job:** Land PR #12 to main, then ship the **outbox publisher (Task 1.5.9) + queue actions (Tasks 1.5.10-1.5.11)** and deploy the Railway worker service per `tasks/downstream/railway-phase-1-5-worker.md`. Optional stretch: extend production E2E with worker case once deployment lands.

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

### Workstream A — Land PR #12 to main

PR #12 contains 19 commits of worker code. The branch is `feat/contact-quality-phase-1-5`.

Steps:
1. Verify PR #12 CI is green (or skipping — this repo doesn't have CI for Python tests; the verification is `pytest` locally + production E2E).
2. Run production E2E one more time against the live endpoint to confirm no regression. The artifact is `/tmp/e2e_phase_1_production.py` (may not survive across sessions — recreate from PR #11 / earlier session artifacts if needed). Expected: 13/13 PASS.
3. Merge with `gh pr merge --squash --delete-branch`.
4. Verify Railway auto-deploys the FastAPI service (existing service updates on every main commit). Note: the WORKER service does not yet exist — that's part of Workstream B.

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

## Suggested first actions

1. Run `/context-restore`. Expect a checkpoint titled **"phase-1.5-worker-foundation-shipped-publisher-pending"** dated 2026-05-14. Load it. If `NO_CHECKPOINTS`, STOP and investigate.

2. Read this file + `feedback_destructive_ops_blast_radius.md` + `project_contact_quality_initiative.md`'s `## Phase 1.5 main-scope foundation SHIPPED` section.

3. Check PR #12 status. Either merge it (after confirming the user wants to ship) or address any review feedback first.

4. After PR #12 merges: implement the outbox publisher (Workstream B) via `superpowers:subagent-driven-development`. Spec the implementer with the plan's lines 3448-3640. Use TDD with mock-driven tests matching the Phase 1.5 P2 + main-scope patterns (no real-DB fixtures yet).

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
