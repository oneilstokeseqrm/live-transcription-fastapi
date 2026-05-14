# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative
**Last session:** 2026-05-14 (Phase 1.5 main-scope continuation — outbox publisher + queue actions MERGED via PR #13)
**Status:** PR #13 squash-merged to `main` at commit `ad7c710`. Railway auto-deploy `96ecc1b3-e2d9-45fd-bcae-529315cd3312` SUCCESS. Production E2E re-run post-merge: **20/20 PASS** (13 prior + 7 new queue route smoke tests). 6 Codex review rounds applied during PR development (14 P1s + 8 P2s closed with permanent regression tests; 2 Round 7 findings deferred-by-design with inline TODOs).
**This session's job:** Deploy the Railway worker service per `tasks/downstream/railway-phase-1-5-worker.md` (Workstream D in the prior session) AND extend `/tmp/e2e_phase_1_production.py` with a worker materialization end-to-end case (Workstream E).

---

## Critical context (READ FIRST)

1. **All code is in `main`.** Worker code (PR #12 at `11b3b30`) + publisher + queue actions (PR #13 at `ad7c710`) all live. The FastAPI Railway service redeployed automatically and serves all the new routes correctly. But the **worker process itself is not running in production** — the existing Railway service runs `uvicorn main:app`, not `python -m workers`. The new Railway service to run the worker process is THIS session's primary task.

2. **Deployment brief is canonical:** `tasks/downstream/railway-phase-1-5-worker.md` has the 6-step recipe. Read it first.

3. **The user is a non-developer founder.** Make confident technical decisions on deployment configuration and coordination; surface only product/strategic decisions. Work without stopping for clarifying questions.

4. **Cross-service dependency:** the worker needs `EQ_AGENT_ACTION_CORE_URL` + `EQ_AGENT_ACTION_CORE_API_KEY` env vars. The agent is already production-deployed for onboarding. Use `mcp__railway__service_list` to find its URL, and `mcp__railway__list_service_variables` to find an existing server-to-server API key pattern (or coordinate generating a new key on the agent side).

5. **Production E2E ALREADY extended with queue route smoke tests** during PR #13. The file at `/tmp/e2e_phase_1_production.py` now has 20 cases. Workstream E adds one more case for worker materialization end-to-end (seed an `approved` queue entry, wait for worker poll, assert materialized account + outbox row + published_at).

6. **Two forward-looking Codex findings deferred to follow-up PRs (TODOs in code):**
   - `services/pending_account_mappings.py` REOPEN_PARENT_SQL — needs to clear `approval_attempt_id, creation_started_at, mapped_at, resolved_account_id, ignored_at, ignored_by` when reopening. Belongs in Task 1.5.12 (expiry sweep + reopen lifecycle PR).
   - `workers/outbox_publisher.py` `_build_event` — EventBridge 256KB Detail cap not enforced. Phase 2 hardening (typical entries are ~1KB; the cap requires ~250 signals/entry).

---

## What this session does

### Workstream D — Railway worker service deployment (primary)

Per `tasks/downstream/railway-phase-1-5-worker.md`:

1. **Create service** via `mcp__railway__service_create_from_repo`:
   - project_id: `847cfa5a-b77c-4fb0-95e4-b20e8773c23e`
   - repo: `oneilstokeseqrm/live-transcription-fastapi`
   - branch: `main`
   - service_name: `live-transcription-fastapi-worker`

2. **Override start command** via `mcp__railway__service_update`:
   - start_command: `python -m workers`

3. **Copy env vars** from FastAPI service (`59a69f3d-9a24-4041-942a-891c4a81c5fb`):
   - `DATABASE_URL` (same Neon eq-dev DB)
   - `INTERNAL_JWT_SECRET` (for any future shared use)
   - `AWS_REGION`, `EVENTBRIDGE_BUS_NAME` (publisher emits via boto3 to EventBridge)

4. **Add new env vars**:
   - `EQ_AGENT_ACTION_CORE_URL` (look up via `mcp__railway__service_list`)
   - `EQ_AGENT_ACTION_CORE_API_KEY` (existing pattern or coordinate)
   - `WORKER_POLL_INTERVAL_SECONDS` (optional, default 5)
   - `PUBLISHER_POLL_INTERVAL_SECONDS` (optional, default 2)

5. **Trigger deploy** + verify via `mcp__railway__deployment_status`. Worker should log:
   - `Worker starting: agent_url=... poll_interval=5.0s`
   - `Publisher starting: interval=2.0s region=... bus=...`

6. **Smoke-test materialization** per the recipe in `railway-phase-1-5-worker.md`:
   - Seed a `pending_account_mapping` row with status='approved' via Neon MCP
   - Seed a `pending_account_mapping_signal` row referencing it
   - Wait ~10s for the worker poll
   - Verify status='mapped', resolved_account_id NOT NULL, mapped_at NOT NULL
   - Verify a row in `account_provisioning_outbox` with that queue_id
   - Verify the outbox row's `published_at IS NOT NULL` (publisher poll already fired)

### Workstream E (stretch) — Extend production E2E with worker case

Once the worker is live, add a case to `/tmp/e2e_phase_1_production.py`:

```python
def worker_materializes_approved_queue_entry():
    """Seed an approved queue entry via Neon MCP, wait for the worker poll
    + publisher poll, assert the full materialization + outbox propagation."""
    # 1. INSERT pending_account_mappings (status='approved', owner_user_id=test, etc.)
    # 2. INSERT pending_account_mapping_signals referencing it
    # 3. Wait 15s (worker poll interval=5s, publisher poll=2s, agent call ~5-10s)
    # 4. SELECT status, mapped_at FROM pending_account_mappings WHERE id=...
    #    Assert status='mapped', mapped_at IS NOT NULL.
    # 5. SELECT published_at FROM account_provisioning_outbox WHERE queue_id=...
    #    Assert exactly one row, published_at IS NOT NULL.
    # 6. Cleanup: archive the test queue entry so subsequent runs don't see it.
```

Re-run the full suite; expect 21/21 PASS.

---

## What's NOT in scope this session

- **Queue UI in eq-frontend** — separate cross-repo work (Task 1.5.16). Frontend team coordinates.
- **Task 1.5.12 expiry sweep + reopen lifecycle** — separate PR (see Round 7 deferral TODO in `services/pending_account_mappings.py`).
- **Phase 2 hardening** (DLQ, batch EventBridge, retention, 256KB enforcement) — separate planning session post-Phase-1.5 stopping point.

---

## Repository state (as of 2026-05-14 end-of-session)

- **Main HEAD:** `ad7c710 feat(phase-1.5): outbox publisher + queue actions (Tasks 1.5.9-1.5.11) (#13)`
- **All Phase 1.5 main scope code lives in main:**
  - `workers/account_provisioning_worker.py` (worker poll loop with advisory lock + agent client + materialization)
  - `workers/outbox_publisher.py` (publisher with FOR UPDATE SKIP LOCKED per-row + EventBridge async wrapper)
  - `workers/__main__.py` (entrypoint launching worker + publisher concurrently via asyncio.gather)
  - `routers/queue_actions.py` (POST /queue/{id}/approve|map|ignore, registered in main.py)
  - `services/queue_authorization.py` (can_act_on_queue_entry helper)

- **Production state:**
  - FastAPI Railway service `59a69f3d-9a24-4041-942a-891c4a81c5fb` running `uvicorn main:app` — auto-redeployed at `96ecc1b3` post-merge, serves queue routes
  - Worker Railway service: **does not exist yet** (next-session task)
  - Production endpoint `https://live-transcription-fastapi-production.up.railway.app` health: ✅

- **Neon eq-dev (`super-glitter-11265514`)** schema all Phase 1.5 columns present. Test tenant `11111111-1111-4111-8111-111111111111` present.

- **Production E2E artifact** at `/tmp/e2e_phase_1_production.py` — 20/20 PASS against production. Includes:
  - 9 Phase 1 regression cases
  - 4 Phase 1.5 P2 cases
  - 7 Phase 1.5 main scope queue route smoke cases (NEW this session)
  - Uses pg_user_id-bearing JWT (matches production identity pattern)

---

## Carry-forward invariants (now load-bearing in main, NEW)

All from PR #12 and prior, plus 22 new bugs fixed in PR #13:

- **Per-row FOR UPDATE SKIP LOCKED** in publisher — multi-process safe
- **MARK_FAILED in fresh session AFTER lock_session releases** — no self-deadlock
- **`AND published_at IS NULL`** on MARK_FAILED_SQL — no contradictory post-publish failure stamps
- **`ORDER BY publish_attempts ASC, created_at ASC`** — failed-row rotation prevents starvation
- **Pydantic validators return canonical UUID strings** — replay detection survives uppercase/braced inputs
- **`_effective_user_id(ctx) = pg_user_id or user_id`** — matches the insert pattern from ingestion routes (`routers/text.py:101`, `routers/batch.py:164`); without this, queue actions 403 their own owner
- **/ignore requires UUID-shaped effective user_id** (400 guard before SQL) — Auth0-only JWTs cannot ignore
- **/approve and /map status filters** — `archived_at IS NULL AND status IN/NOT IN (...)` — replays don't mutate mapped/creating/ignored rows
- **Tenant-scoped account lookup before /map materializes** — prevents cross-tenant attachment
- **/ignore cascades to child signals** (ARCHIVE_SIGNALS_SQL) — Codex Round 6 P1, prevents re-consumption on reopen
- **/map and /approve attempt_id strict match on replay-success** — Codex Round 5 P2, prevents different-attempt-id false success

---

## Reading order at session start

1. **Auto-loaded:** `MEMORY.md` — expect `PHASE_1.5_PUBLISHER_AND_QUEUE_ACTIONS_MERGED_WORKER_DEPLOY_PENDING`
2. **This file** — handoff
3. **Project memory:** `~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/project_contact_quality_initiative.md` — read the `## Phase 1.5 publisher + queue actions MERGED (2026-05-14)` section (will be appended this session)
4. **Deployment brief:** `tasks/downstream/railway-phase-1-5-worker.md` — the 6-step recipe
5. **Lessons:** `tasks/lessons.md` — read the `Codex spiral discipline (2026-05-14)` entry being added this session about when to defer-by-design vs keep fixing

---

## Suggested first actions

1. Run `/context-restore`. Expect a checkpoint titled **"phase-1.5-publisher-queue-actions-merged-worker-deploy-pending"** dated 2026-05-14.
2. Run the pre-flight checks (test tenant + Phase 1.5 schema + `/tmp/e2e_phase_1_production.py` + production endpoint reachable). The e2e file is in `/tmp/` so survives between Bash sessions but not reboots.
3. Pull eq-agent-action-core's production URL + decide on API key strategy.
4. Execute the 6 steps in `tasks/downstream/railway-phase-1-5-worker.md`.
5. Run the worker smoke test (Neon-seeded approved entry → expect mapped + outbox published within ~15s).
6. Extend `/tmp/e2e_phase_1_production.py` with the worker case (Workstream E).
7. End with `/context-save` — mandatory load-bearing invariant.

---

## Final note for the next agent

Phase 1.5 main scope is structurally complete. Everything that needed code lives in `main`. What remains is the operational step: stand up the worker container, wire its env vars, run a smoke test that proves the end-to-end materialization pipeline (queue entry → worker → agent call → materialization → outbox → EventBridge).

The architecture is rigorously codex-reviewed (6 rounds, 22 real bugs caught, 2 forward-looking deferrals documented). The TDD regression test density is high. Hold the bar on the smoke test — actually seed a queue entry and watch it propagate; don't trust logs alone.

After this session, Phase 1.5 main scope is done. The explicit STOPPING POINT per design Section 7.3 kicks in. Re-plan Phase 2 comprehensively before any further commitment.
