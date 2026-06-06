> ## ⛔ SUPERSEDED (2026-06-06) — this prompt's job (the prod import E2E) is DONE.
> The prod import E2E (history + forward) **PASSED** and **EQ-92 is Done** in Linear. The remaining Phase-3 work
> is the frontend (EQ-94). **Use the next prompt instead:**
> `docs/superpowers/specs/2026-06-06-granola-eq94-frontend-next-session-prompt.md` (resume checkpoint
> `granola-e2e-passed-eq94-next`). This file is kept as the historical E2E runbook only.

# Next-session opening prompt — Granola EQ-92/B3: backend SHIPPED → prod E2E → EQ-94 (frontend)

**Written:** 2026-06-06, end of the session that SHIPPED + DEPLOYED the B3 background-import backend (PR 2).
The B3 backend is COMPLETE, MERGED (`061ef37`), DEPLOYED (Railway `9cda4b1e` SUCCESS, `/health` 200), and
Codex-gate-clean (4 rounds). The build session got heavy, so the founder + I deliberately deferred the one
delicate remaining step — the **prod import E2E** — to a fresh session. Paste the block below to start it.

---

```
You're continuing the multi-session Granola.ai → EQ meeting-ingestion BUILD: EQ-92 / Phase 3. The BACKEND is
DONE. B1+B2 (EQ-91) and B3 (EQ-92, the background history-import) are ALL SHIPPED + DEPLOYED to prod:
main @ 061ef37, B3 = PR #39 (squash 061ef37), Railway 9cda4b1e SUCCESS, /health 200, routes live + auth-gated.
The B3 Codex pre-merge gate ran 4 ROUNDS → CLEAN (7 P1s folded) + a pre-Codex multi-agent review; 603 unit
tests / 0 new failures; 0 Pyright errors; 0 envelope drift. I'm a non-developer founder — plain English,
confident technical calls, surface product/strategic decisions + the honest tradeoff. I authorize every
merge + each Railway/AWS/secret change.

CONTINUITY IS CRITICAL. Read everything before you touch anything. Trust but verify each artifact against the
repo + the DB; if anything is missing or doesn't match, STOP and tell me. The test tenant is SHARED — run the
shared-infra-collision check before ANY write to it.

STEP 1 — Run /context-restore FIRST. It must load the checkpoint titled "granola-b3-pr2-shipped-e2e-next"
(file prefix 20260606). If it loads anything else, STOP (fallback: this doc).

STEP 2 — Read these IN FULL before acting:
  • memory/MEMORY.md + memory/project_granola_integration.md (the 2026-06-06 (LATER) "PR2 SHIPPED" entry — the
    authoritative state + the full A1-A7 / Codex-4-round record).
  • docs/superpowers/plans/2026-06-04-granola-phase-3-fe-be.md — SOURCE OF TRUTH. The BUILD STATUS banner
    (B1+B2+B3 shipped), §3 Testing strategy (the prod E2E walk), §F1-F4 (the frontend, EQ-94), §2 contract.
  • tasks/granola-existing-system-map.md (what's live today — now includes the full B3 backend).
  • feedback memories: shared_infrastructure_collision (LOAD-BEARING for the E2E), branch_safety,
    tenant_isolation, envelope_contract_immutable, test_pattern_no_docker, verify_existing_behavior_before_scoping;
    reference: railway_project_ids, granola_api_shape, railway_proxy_timeout, prisma_schema_ownership, test_tenant.

STEP 3 — Verify state:
  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi
  git checkout main && git pull --ff-only && git log --oneline -1   # 061ef37 ... B3 PR #39
  curl -s https://live-transcription-fastapi-production.up.railway.app/health   # {"status":"ok"} (retry past the benign ~15% cold-start 502, EQ-105)
  # granola_import_runs LIVE in Neon super-glitter (eq-dev production). The prod test cred 6a727bae-... is a
  # LEGACY single-folder connection (no import_scope) — its /status correctly shows NO import block.

STEP 4 — THE PROD IMPORT E2E (the deferred step). This touches the SHARED test tenant + the founder's test
credential, so:
  (a) FIRST run the shared-infra-collision check (scan ~/.claude/projects/-Users-peteroneil-*/*.jsonl for
      other active agents in the last hour; prefer a Neon branch if write-heavy). Per
      [[shared-infrastructure-collision]].
  (b) Mint an internal HS256 JWT: read INTERNAL_JWT_SECRET + INTERNAL_CRON_SECRET from the Railway env via the
      Railway MCP (service 59a69f3d-..., env e4c5ec15-..., project 847cfa5a-...) into shell vars — NEVER print
      them. iss=eq-frontend, aud=eq-backend, tenant=11111111-1111-4111-8111-111111111111,
      pg_user_id=061ae392-47d5-4f04-9ea8-afa241f23555 (stokeseqrm@gmail.com). (Prior E2E used a stdlib mint in
      /tmp/granola_e2e.py, not committed.)
  (c) HISTORY E2E: disconnect (DELETE) then /connect the test cred with
      {api_key, folders:[{id:"fol_sBJi17PeBXpHN7",name:"Test EQ"}], import_scope:"history"}. Assert /connect
      returns FAST with import:{import_run_id, state:"queued", total:null, done:0} (NOT a synchronous first
      poll). Then poll /status: import block goes indeterminate→"N of M"→complete; the granola_import_runs row
      advances state queued→running→complete (verify in Neon). "Test EQ" has ~1 note (the Second Rodeo note,
      Scenario-D skip), so the import completes in seconds with done≈0/skipped≈1 (no real ingest, no DLQ).
      Confirm the import ran on GRANOLA_IMPORT_QUEUE (not the poll queue) + the advisory-lock handoff (the 5-min
      poll defers via A1 while last_polled_at is NULL, then resumes once the import sets/leaves it).
  (d) FORWARD E2E: disconnect then /connect with import_scope:"forward". Assert import:null, /status omits the
      import block, last_polled_at ≈ the connect time (no backfill), and the next poll picks up only NEW notes.
  (e) Reconnecting flips the cred legacy→B3-history (it IS a test cred). LOCKED-11 cleanup stays deferred until
      the founder says. If you want the cred back to legacy afterward, reconnect without import_scope.
  THEN: update memory + the plan banner + Linear (EQ-92 → Done) for the E2E result; then start EQ-94.

STEP 5 — EQ-94 (THE FRONTEND, greenfield) — separate, AFTER the E2E confirms the backend. Per plan §F1-F4 in
eq-frontend (SHARED branch-hopping checkout — git branch --show-current before EVERY commit): F1 tRPC
procedures over the gateway-JWT rail (callBackend + mintInternalJwt pg_user_id → BACKEND_SERVICE_TRANSCRIPTION_URL;
C12 enforce the design-partner gate in EVERY procedure); F2 gated card (onboarding meeting-connect + settings
connections); F3 key-paste→multi-folder picker→import-scope wizard; F4 status panel (folders + import progress
polling /status, stop on terminal state per C18/D2). Each F-phase = its own PR + Codex pre-merge gate.

DISCIPLINE: feature branches + git branch --show-current before every commit; /codex review pre-merge gate;
verify_consumer_contracts.py 0-drift on any backend change; NEVER modify the downstream envelope (LOCKED-38);
tenant isolation everywhere; I authorize merges + Railway/secret changes. Honor the open tickets (do NOT
re-implement): EQ-105 (sync boto3/KMS off the loop), EQ-109 (per-loop asyncpg pool), the NEW residual-P2
per-activation import-lifecycle scoping, backlog #21a/#21b. At session end, run the full handoff audit (repo
docs + memory + Linear mutual consistency) + a stale-signature grep.

Don't improvise — if anything is missing or doesn't match, STOP and tell me.
```

---

## State at handoff (2026-06-06, end of the B3 build session)
- **B3 backend SHIPPED + DEPLOYED:** PR #39, squash `061ef37` → main; Railway `9cda4b1e` SUCCESS; `/health` 200;
  routes live + auth-gated. All A1-A7 + C4/C8/C18. Codex gate 4 rounds → clean (7 P1s folded) + pre-Codex
  multi-agent review. 603 unit tests / 0 new failures (1 pre-existing `account_provisioning` failure, identical
  on main); 0 Pyright errors on changed source; `verify_consumer_contracts.py` 0 drift.
- **Build commits (now on main via the #39 squash):** `0410a53` A7 pool · `d4c1615` A6 vault anchor · `7afb0b0`
  A3/A5 adapter · `5866173` A1/A2 scheduler · `1ce1da1` A2 cron · `4b1e4e7` C4/C8/C18 router · `e7fddd2`
  pre-Codex fold · `cea5578` Codex r1 fold · `7665c4d` Codex r2 fold · `0056024` Codex r3 fold · `57c5edd` docs.
- **DEFERRED to next session (deliberate, heavy-context call):** the prod import E2E (history + forward) — the
  one delicate prod + shared-tenant operation; safest on a fresh, focused context.
- **NEXT after the E2E:** EQ-94 (the frontend, F1-F4).
- **Tickets:** EQ-92 (B3 — code shipped, move to Done after the prod E2E passes); EQ-105 (sync boto3/KMS off the
  loop); EQ-109 (per-loop asyncpg pool); **NEW residual-P2** per-activation import-lifecycle scoping (a crashed
  reconnect leaving an old `complete` run → poll re-lists dedup-safe without a fresh progress row — data correct,
  progress UI missing; needs a per-activation marker); backlog #21a (reconfigure backfill) + #21b (exact-progress
  items table).
- **Carried-forward DEFERRED:** per-folder watermarks; live-API multi-folder probes (do in the prod E2E); the
  prod test credential is KEPT connected (legacy single-folder, no import_scope); 5-min trigger LIVE; LOCKED-11
  cleanup deferred until the founder says.
- **/health:** intermittently 502s (~15%, benign single-worker cold-start; EQ-105) — retry a few times.
