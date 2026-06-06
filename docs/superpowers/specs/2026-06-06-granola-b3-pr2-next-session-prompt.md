# Next-session opening prompt — Granola EQ-92 / B3, PR 2 (backend) continuation

**Written:** 2026-06-06, end of the session that shipped the `granola_import_runs` migration (PR 1) and
built the first backend component. **PR 1 is DONE + deployed + verified in prod. PR 2 (the backend
B3) is IN PROGRESS** on branch `phase-3/granola-be-b3` — component 1 of 6 (`import_runs.py`) is committed
and green; 5 components + the Codex review gate remain. Paste the block below as the next session's opening
message.

---

```
You're continuing the multi-session Granola.ai → EQ meeting-ingestion BUILD: EQ-92 / Phase 3 B3
(background history-import), PR 2 (the backend). PR 1 (the eq-frontend granola_import_runs migration) is
SHIPPED + MERGED + DEPLOYED + VERIFIED LIVE in the prod DB. The backend design has been Codex-consult-vetted
(corrections A1-A7). One backend component (import_runs.py) is built, 11 unit tests green, committed. I'm a
non-developer founder — plain English, make confident technical calls, surface product/strategic decisions +
the honest tradeoff. PR 2 = its own PR + Codex pre-merge gate; I authorize the merge + each Railway/secret
change.

CONTINUITY IS CRITICAL. Read everything before you touch anything. Trust but verify each artifact against the
repo + the DB; if anything is missing or doesn't match, STOP and tell me.

STEP 1 — Run /context-restore FIRST. It must load the checkpoint titled "granola-b3-pr2-import-runs-done"
(file prefix 20260606). If it loads anything else, STOP (fallback: this doc).

STEP 2 — Read these IN FULL before acting:
  • THE CORRECTED DESIGN = your build spec (read it FIRST, it's binding):
    tasks/b3-implementation-design.md — esp. §POST-CONSULT DECISIONS A1-A7 (they SUPERSEDE the original
    §1-7 where noted; they fold in the Codex consult that caught real flaws in the first design).
  • tasks/todo.md — the PR-2 checklist (import_runs.py is [x] done; the remaining 5 components are listed).
  • THE PLAN (source of truth): docs/superpowers/plans/2026-06-04-granola-phase-3-fe-be.md — §Phase B3 +
    §1a corrections C1-C18 + §2 /status contract + §5 deploy order. (BUILD STATUS banner reflects PR1 shipped.)
  • memory/MEMORY.md + memory/project_granola_integration.md (the 2026-06-06 entry).
  • tasks/granola-existing-system-map.md (what exists today, now incl. granola_import_runs + import_runs.py).
  • The backend files PR 2 still touches: services/granola_ingestion/scheduler.py (poll workflow +
    GRANOLA_POLL_QUEUE + _advisory_lock_key + list_active_credentials), services/granola_ingestion/adapter.py
    (run_one_cycle + CycleResult + _credential_is_active + _CredentialDeactivated), routers/granola.py
    (connect_granola + /status), services/vault/user_credentials.py (update_credential_config = the template),
    services/asyncpg_pool.py (sizing invariant). The committed import_runs.py is the dependency the rest uses.
  • feedback memories: branch_safety, codex_pre_merge_gate, tenant_isolation, shared_infrastructure_collision,
    verify_existing_behavior_before_scoping, envelope_contract_immutable, test_pattern_no_docker; reference:
    railway_project_ids, granola_api_shape, railway_proxy_timeout, prisma_schema_ownership.

STEP 3 — Verify state:
  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi
  git checkout phase-3/granola-be-b3 && git log --oneline -2   # tip 4e4346d (import_runs); main is bfaf24a
  .venv/bin/python -m pytest tests/unit/granola_ingestion/test_import_runs.py -q   # 11 passed
  # granola_import_runs is LIVE in prod (Neon super-glitter / eq-dev production branch) — verify if needed:
  #   SELECT to_regclass('public.granola_import_runs');  -> not null
  curl -s https://live-transcription-fastapi-production.up.railway.app/health   # {"status":"ok"} (see note)
NOTE: /health intermittently 502s (~15% — a benign single-worker cold-start artifact, root-caused 2026-06-06,
ticketed EQ-105). NOT a B3 blocker; retry a few times. If main lacks bfaf24a or the branch/table are missing,
STOP.

STEP 4 — BUILD the remaining 5 PR-2 components per the A1-A7 design (TDD, AsyncMock, no Docker), then the
gates. Suggested order (import_runs.py already done):
  1. services/asyncpg_pool.py — bump _DEFAULT_MAX_SIZE 10->20, env-overridable GRANOLA_DB_POOL_MAX_SIZE,
     re-derive the invariant >= 2*(poll+import concurrency)=14, update the docstring (A7). [low-risk, start here]
  2. services/vault/user_credentials.py — NEW anchor_credential_watermark (writes last_polled_at; mirrors
     update_credential_config: advisory-lock-gated, 3-field WHERE, status='active' guard, same-txn audit) (A6).
  3. services/granola_ingestion/adapter.py — surface cycle_aborted on CycleResult; set it on every
     deactivation path; thread optional import_run_id into run_one_cycle + set_import_total after first listing
     (A3, A5). TOUCHES Codex-hardened edge-#12 code — careful + re-run the full adapter test suite.
  4. services/granola_ingestion/scheduler.py — GRANOLA_IMPORT_QUEUE = Queue("granola-import", concurrency=2)
     + granola_import_one_credential workflow + run_import_step (try-lock A2; on busy leave queued + record
     lock_busy; cancel/fail/complete via cycle_aborted+credential_error_code A3); + the POLL-DEFERS guard in
     run_cycle_step: skip an active credential that is uninitialized (import_scope='history' AND
     last_polled_at IS NULL, or forward+NULL) so the IMPORT owns the first backfill, NOT the poll (A1).
  5. routers/granola.py — /connect async restructure: capture forward_anchor_at at ROUTE ENTRY (C4); LIFT the
     mode="all" 400 guard; branch on import_scope (history -> get_or_create_active_import_run + dispatch on
     GRANOLA_IMPORT_QUEUE w/ SetWorkflowID(f"granola_import_{credential_id}_{import_run_id}") + return import
     ACK; forward -> anchor_credential_watermark + import:null); DELETE the synchronous _save_and_test call;
     enqueue-atomicity recovery in /connect-retry + /status (C8); + the /status import block (C18, via
     import_runs.latest_import_run + read_import_progress; omit when no import_run).
  DO NOT build (split-out fast-follows per the consult — backlog #21): (a) newly-added-folder reconfigure
  backfill (#21a) — active-row reconfigure keeps B2 behavior; (b) exact re-import progress items-table (#21b).
  THEN: scripts/verify_consumer_contracts.py (0 drift, envelope UNCHANGED) + full unit suite + /codex review
  (4-round cap) -> fold P0/P1 -> I authorize merge -> Railway deploy -> /health 200 -> prod E2E -> then EQ-94 (FE).

DISCIPLINE: feature branch (phase-3/granola-be-b3, already created) + git branch --show-current before EVERY
commit; /codex review pre-merge gate; verify_consumer_contracts.py 0-drift; NEVER modify downstream envelope
(LOCKED-38); tenant isolation everywhere; I authorize merge + Railway/secret changes. At session end, run the
full handoff audit (repo docs + memory + Linear mutual consistency) before declaring ready.

Don't improvise — if anything is missing or doesn't match, STOP and tell me.
```

---

## State at handoff (2026-06-06)
- **PR 1 DONE:** eq-frontend #454, squash `54b9dbc8`, merged + Vercel-deployed; `public.granola_import_runs`
  live in Neon `super-glitter-11265514` (`eq-dev`) `production` branch (table + partial-unique + CHECK + 3 FKs
  verified). Codex gate PASS (1 P2 folded). Worktree removed.
- **PR 2 IN PROGRESS:** branch `phase-3/granola-be-b3` @ `4e4346d`. Component 1/6 done: `services/
  granola_ingestion/import_runs.py` + `tests/unit/granola_ingestion/test_import_runs.py` (11 green).
- **Design vetted:** Codex consult (high) caught 3 real design flaws + 1 architecture concern → corrections
  A1-A7 folded into `tasks/b3-implementation-design.md`. Most load-bearing: A1 poll-defers-to-uninitialized
  guard (the backfill race), A3 surface `cycle_aborted` (cancel vs complete), A2 try-lock + lock_busy recovery.
- **Tickets:** EQ-92 (B3, in progress); EQ-105 (sync boto3/KMS off the loop — found in the /health 502
  investigation, fast-follow); EQ-109 (per-loop asyncpg pool ownership — found in the B3 consult, fast-follow);
  backlog #21a (reconfigure backfill) + #21b (exact-progress items table) split out of B3 per the consult.
- **Carried-forward DEFERRED (still honored):** per-folder watermarks; live-API multi-folder probes; the
  reconfigure backfill (now formally A4-split → #21a). The prod test credential is KEPT connected (LEGACY
  single-folder config, no import_scope; its /status shows no import block); 5-min trigger LIVE; LOCKED-11
  cleanup deferred.
- **/health:** intermittently 502 (~15%), benign cold-start single-worker artifact (EQ-105); deployed code correct.
