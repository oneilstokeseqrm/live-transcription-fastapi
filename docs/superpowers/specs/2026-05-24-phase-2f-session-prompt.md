# Next Session Opening Prompt (Phase 2f — Granola admin endpoints + wire the cron pinger)

**Written:** 2026-05-24, after Phase 2e (the scheduler) merged as PR #28 `4e81bb6`, deployed, and was verified dormant end-to-end in production.

**Paste the block below as the opening message of the next Claude session.**

---

```
You're picking up the Granola.ai integration at Phase 2f — the admin
endpoints that let a user actually CONNECT a Granola account, plus
wiring the 5-minute cron PINGER that the Phase 2e scheduler is waiting
for. Phase 2e shipped the scheduler dormant; Phase 2f flips the switch.

THIS SESSION'S JOB:
1. Build routers/granola.py — the JWT-authed admin endpoints:
   /validate, /connect, /rotate, /status, /disconnect (per LOCKED-30/31/34).
2. Wire the recurring 5-min trigger that POSTs /internal/granola/cron-tick
   (a Railway cron service OR an external cron — decide with the user;
   the endpoint + INTERNAL_CRON_SECRET are already live + verified).
~0.5 day estimated. After this, connect → poll → ingest works end-to-end.

═══════════════════════════════════════════════════════════════════════
STEP 1 — /context-restore, then verify state
═══════════════════════════════════════════════════════════════════════

Run /context-restore (loads the latest checkpoint). Then verify:

  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi
  git branch --show-current        # expect: main
  git log --oneline -3             # tip: 4e81bb6 feat(granola): Phase 2e
                                   #      — scheduler (...) (#28)
  git status --short               # clean

  curl -s https://live-transcription-fastapi-production.up.railway.app/health
  # expect {"status":"ok"}

  # Phase 2e endpoint is live + auth-enforced (secret is set):
  curl -s -o /dev/null -w "%{http_code}\n" -X POST \
    https://live-transcription-fastapi-production.up.railway.app/internal/granola/cron-tick
  # expect 401 (missing secret). With the correct X-Internal-Cron-Secret
  # header it returns 202 {"enqueued":0} until this session adds /connect.

  .venv/bin/python -c "from services.granola_ingestion.scheduler import \
    granola_poll_one_credential, run_cycle_step, list_active_credentials; \
    from services.asyncpg_pool import get_asyncpg_pool; \
    from services.vault import store_credential, get_granola_credential_for_user; print('OK')"

If main is NOT at 4e81bb6 (or a descendant), or /health is non-200, STOP
and surface to the user.

═══════════════════════════════════════════════════════════════════════
STEP 2 — MANDATORY READS (complete ALL before writing code)
═══════════════════════════════════════════════════════════════════════

Per feedback_complete_all_handoff_reads_before_action.md. Pre-flight each
path with ls/Read before declaring it read (verify_mandatory_read_files_exist
lesson).

1. tasks/granola-integration-plan.md — §Phase 2f is the spec. LOCKED-23..44.
   Pay attention to LOCKED-30 (Connect page route — Phase 3a frontend),
   LOCKED-31 (Save & test = synchronous one-shot poll), LOCKED-34
   (disconnect = soft-delete via archived_at), LOCKED-32 (fail-fast posture).
2. services/vault/__init__.py + services/vault/user_credentials.py — the
   accessors Phase 2f calls: store_credential, get_granola_credential_for_user,
   rotate_credential_key, reactivate_credential. ALL take pool: asyncpg.Pool
   (use services.asyncpg_pool.get_asyncpg_pool()). ALLOWLIST already includes
   "routers.granola". reactivate_credential is the reconnect-after-disconnect
   path (UNIQUE(tenant_id,user_id,provider) covers archived rows).
3. services/granola_ingestion/adapter.py — run_one_cycle(*, credential, pool,
   api_client=None). /connect calls this once synchronously for the "save &
   test" first poll (LOCKED-31).
4. services/granola_ingestion/scheduler.py — the Phase 2e scheduler /connect
   feeds (it'll auto-pick up the new credential on the next cron tick).
5. services/granola_ingestion/api_client.py — GranolaAPIClient.list_folders /
   list_notes for /validate (validate calls list_notes(page_size=1) +
   list_folders; does NOT store the key).
6. services/asyncpg_pool.py — get_asyncpg_pool() is how routers get the pool.
7. routers/text.py + routers/queue_actions.py — JWT auth pattern
   (get_auth_context_polling / verify_internal_jwt; tenant_id + pg_user_id
   from claims).
8. routers/granola_cron.py — the Phase 2e endpoint (for the cron-pinger wiring).
9. scripts/verify_consumer_contracts.py — re-run pre-merge (Phase 2f doesn't
   touch envelopes; must stay 0 drift).
10. tasks/lessons.md (bottom) — the Codex-oscillation lesson + the prior
    Phase 2d lessons. AND feedback_codex_pre_merge_gate / feedback_branch_safety
    / feedback_tenant_isolation / feedback_test_pattern_no_docker.
11. MEMORY.md + project_granola_integration.md — Active Work =
    PHASE_2E_SHIPPED + PHASE_2F_NEXT.

═══════════════════════════════════════════════════════════════════════
STEP 3 — EXECUTE PHASE 2f
═══════════════════════════════════════════════════════════════════════

Branch: phase-2f/granola-admin off main.

New file routers/granola.py — endpoints (all JWT-authed; tenant_id + user_id
from JWT claims; per plan §Phase 2f):
  POST /integrations/granola/validate  {api_key} → {ok, folders:[...]} | {ok:false, reason}
  POST /integrations/granola/connect   {api_key, folder_id} → encrypt+store via
       vault.store_credential (or reactivate_credential if an archived row
       exists), then run_one_cycle once (LOCKED-31 "save & test") → first_poll result
  POST /integrations/granola/rotate    {new_api_key} → vault.rotate_credential_key
  GET  /integrations/granola/status    → {connected, last_polled_at, activity{7d},
       status, folder} (read non-encrypted columns; do NOT decrypt the key)
  DELETE /integrations/granola         → soft-delete (archived_at=NOW, status='archived')

Modified: main.py — register routers/granola.py.

Then wire the 5-min cron pinger (decide approach WITH the user):
  - Option A: Railway cron service (curlimages/curl image, schedule */5 * * * *,
    start cmd curls the internal endpoint with X-Internal-Cron-Secret via
    Railway private networking). NOTE: Railway MCP can't set cronSchedule —
    needs the Railway dashboard.
  - Option B: external cron (cron-job.org / GitHub Actions scheduled workflow)
    POSTing the public URL with the secret header.
  INTERNAL_CRON_SECRET is ALREADY set in Railway (Phase 2e). The endpoint is
  verified working.

Tests: AsyncMock unit tests (no Docker) — validate happy/401, connect
happy-path (credential row + first poll), reconnect-after-disconnect uses
reactivate (UPDATE not INSERT), status shape, disconnect soft-deletes.

Pre-merge: verify_consumer_contracts.py 0 drift; Codex review (4-round soft
cap — but per the Codex-oscillation lesson, distinguish new real bugs from
the reviewer reversing itself, and STOP if it oscillates).

═══════════════════════════════════════════════════════════════════════
CRITICAL DISCIPLINES (carried forward)
═══════════════════════════════════════════════════════════════════════
- git branch --show-current IMMEDIATELY before every commit (shared checkout).
- Codex pre-merge gate MANDATORY; fold real P1s; STOP if it oscillates on a
  hypothetical (see the Codex-oscillation lesson — this session ran 11 rounds).
- Per-action user authorization for push-to-main / merge / Railway changes.
- Tenant isolation: every query carries tenant_id; /connect sources tenant_id
  + user_id from JWT claims (LOCKED-41 pattern).
- NEVER modify downstream Pydantic envelope contracts (LOCKED-38).
- No Docker in tests; AsyncMock + the _FakeConn/_FakePool/_FakeCredential
  patterns from tests/unit/granola_ingestion/test_adapter.py + test_scheduler.py.
- DBOS API in this repo: DBOS.launch()/destroy() are SYNC; Queue.enqueue_async
  + SetWorkflowID for dispatch (services/account_provisioning/workflow.py +
  services/granola_ingestion/scheduler.py precedents).

USER POSTURE: Non-developer founder. Plain-English always. Confident technical
decisions; surface only product/strategic decisions, scope deviations, or
destructive ops. No push/merge/Railway-changes without per-action auth.
```

---

## Why the cron pinger was deferred to Phase 2f (context for the next author)

Phase 2e built + merged + deployed the scheduler and verified it end-to-end:
an authenticated `POST /internal/granola/cron-tick` returns `202 {"enqueued":0}`
because there are no connected credentials yet. The recurring 5-min trigger was
deferred because:

1. The scheduler is **dormant** until Phase 2f's `/connect` lands a credential —
   a cron firing now does nothing useful.
2. Railway's native cron runs a container's start command on a schedule (a
   one-shot job), not an HTTP pinger — so it needs a dedicated cron service or
   an external cron, which is real infra better added when it's load-bearing.
3. The Railway MCP `service_update` has no `cronSchedule` field, so the cron
   can't be fully automated from here regardless.

Wiring the pinger in Phase 2f means connect → poll → ingest comes alive
together the day Phase 2f ships. `INTERNAL_CRON_SECRET` is already set in
Railway and the endpoint is verified, so Phase 2f only adds the trigger.
