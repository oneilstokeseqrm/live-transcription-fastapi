# Next-session opening prompt — Granola Phase 3, EQ-92 (B3 background import)

**Written:** 2026-06-05, at the end of the BUILD session that shipped EQ-91 (B1+B2). Paste the block
below as the opening message of the next (B3) session. EQ-91 is DONE, merged, deployed, `/health` 200.

---

```
You're continuing the multi-session Granola.ai → EQ meeting-ingestion BUILD. EQ-91 (the multi-folder
backend) is SHIPPED + MERGED + DEPLOYED + /health 200. This session builds EQ-92 (B3 = the background
history-import) — the biggest, highest-stakes phase, which starts with an eq-frontend Prisma migration
(a DIFFERENT repo). I'm a non-developer founder — plain English, make confident technical calls, surface
product/strategic decisions + the honest tradeoff. Each phase = its own PR + Codex pre-merge gate; I
authorize each merge + each Railway/Vercel/AWS/secret change.

CONTINUITY IS CRITICAL. Read everything before you touch anything. Trust but verify each artifact against
the repo + the DB; if anything is missing or doesn't match, STOP and tell me.

STEP 1 — Run /context-restore FIRST. It must load the checkpoint titled
"granola-eq91-shipped-b3-next" (file prefix 20260605-102325). If it loads anything else, STOP.

STEP 2 — Read these IN FULL before acting:
  • THE PLAN (source of truth): docs/superpowers/plans/2026-06-04-granola-phase-3-fe-be.md — read
    §Phase B3 fully + its §1a corrections C1/C2/C3/C4/C7/C8/C9/C13/C17 (folded into the B3 steps).
  • The BUILD-session kickoff: docs/superpowers/specs/2026-06-05-granola-phase-3-BUILD-session-kickoff.md
  • memory/MEMORY.md + memory/project_granola_integration.md (the 2026-06-05 EQ-91-shipped section).
  • tasks/granola-existing-system-map.md + the parallel-intake doc (only relevant at B4).
  • The backend files B3 touches: routers/granola.py (the /connect save-and-test path — _save_and_test_
    locked, _run_save_and_test, the active-row reconfigure I added in B2), services/granola_ingestion/
    scheduler.py (DBOS workflow + GRANOLA_POLL_QUEUE + _advisory_lock_key) + adapter.py run_one_cycle.
  • feedback memories: branch_safety, tenant_isolation, codex_pre_merge_gate, shared_infrastructure_
    collision, verify_existing_behavior_before_scoping, envelope_contract_immutable, test_pattern_no_docker;
    reference: prisma_schema_ownership, railway_project_ids, granola_api_shape, railway_proxy_timeout,
    eventbridge_scheduler_no_http.

STEP 3 — Verify state:
  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi && git log --oneline -3   # 922660b present
  curl -s https://live-transcription-fastapi-production.up.railway.app/health        # {"status":"ok"}
  cd /Users/peteroneil/EQ-CORE/eq-frontend && git branch --show-current && git status --short
If main lacks 922660b or /health is non-200, STOP.

STEP 4 — BUILD B3 (two repos, in this order; each = 1 PR + Codex gate; I authorize each merge):
  (1) eq-frontend `granola_import_runs` Prisma migration FIRST (deploys BEFORE the backend B3 PR; plan
      §B3 Step 1; partial-unique UNIQUE(credential_id) WHERE state IN ('queued','running') via RAW SQL;
      progress counts DERIVED not stored). eq-frontend is a SHARED branch-hopping checkout — git branch
      --show-current before EVERY commit; trust Neon (not a branch-drifted schema.prisma) for schema truth.
  (2) EQ-92 backend B3: import_runs.py (DERIVED progress reader) + new GRANOLA_IMPORT_QUEUE (concurrency
      ~2, NOT the poll queue — C3) + granola_import_one_credential DBOS workflow + /connect async
      restructure branching on import_scope (history → background import; forward → anchor last_polled_at
      = NOW() captured at ROUTE ENTRY, C4, no backfill) + lock-busy (C2) + enqueue atomicity (C8) +
      disconnect-during-import → cancelled (C9) + /status import block (C18). B3 LIFTS the B1/B2
      mode="all" /connect guard now that /connect is async.

DISCIPLINE: feature branch + PR + /codex review (4-round soft cap) before each merge; /codex consult
before novel design; verify_consumer_contracts.py 0-drift on every backend PR; founder authorizes merges
+ infra/secret changes. eq-frontend = SHARED checkout — git branch --show-current before EVERY commit.
Update Linear (EQ-92) + audit the handoff docs (repo + Linear + memory) at the end of the session.

Don't improvise — if anything's missing or doesn't match, STOP and tell me.
```

---

## State at handoff (2026-06-05)
- **EQ-91 DONE:** B1 PR #37 `de3b1f3` + B2 PR #38 `922660b`, both merged + Railway-deployed, `/health` 200.
- **Carried-forward DEFERRED (B3 must honor):** per-folder watermarks (B2 holds the shared watermark on
  any folder skip); live-API multi-folder probes (multi-folder doc §6 — defer to FE E2E / B3); newly-
  added-folder backfill on reconfigure → B3 Step 5b (C17).
- **Prod test credential** KEPT connected, LEGACY single-folder; 5-min trigger LIVE; LOCKED-11 cleanup
  deferred. main @ `922660b`.
