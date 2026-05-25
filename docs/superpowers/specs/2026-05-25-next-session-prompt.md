# Next Session Opening Prompt (Phase 2f-followups + wire the cron pinger + first real /connect E2E)

**Written:** 2026-05-25, after Phase 2f (the Granola admin endpoints) merged as PR #29 `260b863`, deployed (Railway `eb2d4c81` SUCCESS), and was prod-verified. The cron pinger was deferred by user choice to this session.

**Paste the block below as the opening message of the next Claude session.**

---

```
You're picking up a multi-session project: the Granola.ai → EQ transcript
ingestion integration, in live-transcription-fastapi. The previous session
shipped Phase 2f (the admin endpoints that connect a Granola account) end to
end. This session WIRES THE 5-MINUTE TRIGGER (the real "flip the switch"
moment) + runs the first real /connect end-to-end with the founder's own
Granola account + closes two ticketed edge cases.

CONTINUITY IS CRITICAL. Read everything before you touch code. Trust but
verify each artifact loads as expected; if anything is missing or doesn't
match, STOP and surface to the user — do not improvise.

═══════════════════════════════════════════════════════════════════════
STEP 1 — RUN /context-restore FIRST
═══════════════════════════════════════════════════════════════════════
Run /context-restore. It must load this checkpoint:
  ~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/
  20260525-053223-phase-2f-shipped-cron-pinger-deferred.md
If /context-restore returns NO_CHECKPOINTS or a checkpoint whose title is
NOT "phase-2f-shipped-cron-pinger-deferred" (e.g. a Phase 2e title), STOP and
tell the user — the handoff is broken.

═══════════════════════════════════════════════════════════════════════
STEP 2 — READ THESE (top to bottom; complete ALL before coding)
═══════════════════════════════════════════════════════════════════════
Per feedback_complete_all_handoff_reads_before_action.md. Pre-flight each
path with ls/Read before declaring it read.

1. docs/superpowers/specs/NEXT-SESSION-START-HERE.md — status dashboard +
   this session's scope + the ALLOW_LEGACY_HEADER_AUTH=true prod note.
2. tasks/granola-integration-plan.md — §Phase 2f (shipped) + §Phase 2g
   (transactional email, next after this) + §Phase 2.1 follow-ups #12 + #13
   (the two ticketed edges this session closes). LOCKED-23..44.
3. routers/granola.py — the Phase 2f admin endpoints you just shipped.
   STUDY _credential_poll_lock + _run_save_and_test (the advisory-lock
   serialization against the scheduler) + the /connect store-or-reactivate
   flow.
4. routers/granola_cron.py — the cron endpoint the pinger will POST
   (POST /internal/granola/cron-tick, X-Internal-Cron-Secret header).
5. services/granola_ingestion/scheduler.py — the dormant scheduler the
   pinger wakes (list_active_credentials → per-credential workflow).
6. services/granola_ingestion/adapter.py — run_one_cycle + the credential-
   state UPDATE SQLs (_mark_credential_polled_success, _set_credential_status,
   _record_credential_transient_failure) + process_note. EDGE #12's root fix
   lives HERE (guard these on archived_at IS NULL + re-check before publish).
7. services/vault/user_credentials.py — get_credential_status +
   archive_credential (Phase 2f accessors) + the existing reactivate/rotate.
8. tasks/lessons.md (bottom) — the two Codex-oscillation lessons (FREEZE for
   unresolvable ambiguity; SPLIT for conflated concerns) + the
   ALLOW_LEGACY_HEADER_AUTH=true-in-prod finding.
9. MEMORY.md + project_granola_integration.md — Active Work =
   PHASE_2F_SHIPPED + CRON_PINGER_DEFERRED.
10. feedback memories: feedback_codex_pre_merge_gate, feedback_branch_safety,
    feedback_tenant_isolation, feedback_test_pattern_no_docker,
    feedback_shared_infrastructure_collision (load-bearing before the E2E).

═══════════════════════════════════════════════════════════════════════
STEP 3 — VERIFY STATE (after the reads)
═══════════════════════════════════════════════════════════════════════
  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi
  git branch --show-current        # expect: main
  git log --oneline -3             # tip includes 260b863 (Phase 2f #29)
                                   # + e86c09d docs(handoff) if it was pushed
  git status --short               # clean (ignore tasks/llm-modernization-
                                   #   investigation.md — another session's WIP)

  curl -s https://live-transcription-fastapi-production.up.railway.app/health
  # expect {"status":"ok"}

  # Phase 2f endpoints are live + auth-gated (prod has legacy auth ON, so
  # unauthenticated mutations 400 via the legacy path, not 401):
  curl -s -o /dev/null -w "%{http_code}\n" -X POST \
    https://.../integrations/granola/validate \
    -H 'Content-Type: application/json' --data '{"api_key":"x"}'
  # expect 401 (the bearer-token gate)

If main lacks 260b863, or /health is non-200, or /validate (no bearer) is
not 401, STOP and surface to the user.

═══════════════════════════════════════════════════════════════════════
STEP 4 — EXECUTE (decide ORDER with the user; #1 should precede/accompany #2)
═══════════════════════════════════════════════════════════════════════
1. EDGE #12 — adapter archived_at-awareness (plan §2.1 #12). RECOMMENDED
   BEFORE wiring the pinger, because the pinger makes the race live. Root
   fix: add `AND archived_at IS NULL` to the 3 credential-state UPDATE SQLs
   in adapter.py AND have run_one_cycle/process_note re-check the credential
   is still active before each publish, so a credential archived mid-cycle
   aborts cleanly. This closes the disconnect-during-sync ingestion gap (and
   would let the per-endpoint _credential_poll_lock gates be removed — but
   removing them is optional; leaving them is harmless belt-and-suspenders).
   Add AsyncMock tests. Codex pre-merge gate.

2. WIRE THE 5-MIN CRON TRIGGER — the "flip the switch" step. The user's
   original pick was a Railway cron service, but it's fiddly (curlimages/curl
   entrypoint fights a shell start command; the secret needs shell env-
   expansion; cronSchedule is dashboard-only and the Railway MCP can't set
   it; creating it via MCP without a schedule risks a restart loop).
   RECOMMENDED alternative (offer it): a GitHub Actions scheduled workflow
   (.github/workflows/granola-cron.yml, cron: '*/5 * * * *') that curls
   POST /internal/granola/cron-tick with INTERNAL_CRON_SECRET from a GitHub
   Actions repo secret. I can fully author + commit that; the user just pastes
   the secret into the repo's Actions secrets. DECIDE THE APPROACH WITH THE
   USER. INTERNAL_CRON_SECRET is already set in Railway; for GH Actions, its
   value must also be added to GitHub Actions secrets (the user holds the
   value — it's only in Railway; have them copy it, or rotate + set both).

3. FIRST REAL /connect E2E — with Peter's (founder, design partner #0) real
   Granola API key + a real test folder. Connect via the endpoint → wait one
   cron tick → verify a real meeting ingests (raw_interactions row +
   external_integration_runs status='success' + envelope downstream).
   Pre-flight the shared-infrastructure-collision check (other agents?) and
   do LOCKED-11 atomic cleanup of test artifacts afterward. This is where
   the whole chain (connect → poll → ingest) proves out for real.

4. EDGE #13 — /connect bad-folder recovery (plan §2.1 #13). Lower priority;
   can ship after the E2E. Options in the plan.

5. THEN Phase 2g — transactional email on credential breakage (LOCKED-32),
   its own session.

═══════════════════════════════════════════════════════════════════════
NON-NEGOTIABLE DISCIPLINES
═══════════════════════════════════════════════════════════════════════
- git branch --show-current IMMEDIATELY before every commit (shared checkout).
- Codex pre-merge gate MANDATORY before any merge (4-round soft cap; per the
  two oscillation lessons: FREEZE on unresolvable ambiguity, SPLIT on
  conflated concerns, STOP rather than chase).
- Per-action user authorization for push-to-main / merge / Railway changes /
  GitHub-secret changes (feature branch + PR + branch push are fine).
- Tenant isolation: every query carries tenant_id; identity from JWT claims.
- NEVER modify downstream Pydantic envelope contracts (LOCKED-38). Re-run
  scripts/verify_consumer_contracts.py if you touch envelope-adjacent code
  (the adapter edit #12 does NOT touch envelopes — should stay 0 drift).
- No Docker in tests; AsyncMock + the _FakeConn/_FakePool patterns in
  tests/unit/test_granola_admin.py + tests/unit/granola_ingestion/.
- Run tests with DBOS_SYSTEM_DATABASE_URL set (any value) so main.py imports.
- ⚠️ ALLOW_LEGACY_HEADER_AUTH=true IN PROD: get_auth_context_* does NOT
  enforce JWT in prod. Any new JWT-only endpoint needs a bearer-token gate
  (stateless) or pg_user_id requirement (writes user_id).

USER POSTURE (load-bearing): Non-developer founder. Plain-English always.
Make confident technical decisions; surface only product/strategic decisions,
scope deviations, or destructive ops. Do NOT push to main, merge PRs, or
change Railway/GitHub config without per-action user authorization.

═══════════════════════════════════════════════════════════════════════
KEY STATE (verified 2026-05-25 end-of-Phase-2f)
═══════════════════════════════════════════════════════════════════════
live-transcription-fastapi main: 260b863 (Phase 2f #29) [+ e86c09d docs if pushed]
eq-frontend main (untouched): 7905222
AWS (us-east-1, acct 211125681610): KMS CMK 59a0e2bc-c636-45e8-bccf-427ad2426ad8
  (alias eq-user-secrets); IAM user eq-vault-service.
Railway (live-transcription-fastapi prod): project 847cfa5a-b77c-4fb0-95e4-b20e8773c23e,
  env e4c5ec15-1931-4632-9e58-92d9c6be4261, service 59a69f3d-9a24-4041-942a-891c4a81c5fb.
  Latest deploy eb2d4c81 SUCCESS. /health 200. INTERNAL_CRON_SECRET set.
  ALLOW_LEGACY_HEADER_AUTH=true.
Neon (prod): project super-glitter-11265514 (eq-dev), branch br-holy-block-ads5069w,
  db neondb. Vault schema + 3 tables live (vault.user_credentials,
  vault.credential_access_log, public.external_integration_runs).
Vercel (eq-frontend): project prj_0wDppCftk1VrSAsYswI5pnNRHdN8, team
  team_Hnnnu6r1trggeAXYWHXpKfMt; canonical eq-frontend-two.vercel.app.

═══════════════════════════════════════════════════════════════════════
PHASE 2f CODEX TRAJECTORY (9 rounds; PR #29) — for context
═══════════════════════════════════════════════════════════════════════
 R1  2P2+1P3  connect insert-disambiguation; post-store load wrap; status map
 R2  3P2      activity rollup updated_at not created_at; _activity 503; disconnect wrap
 R3  2P1+2P2  require pg_user_id (no Auth0 fallback; enforces JWT); transient→connected; reconnect-race 409
 R4  2P2      /validate JWT gate; readback-None graceful + advisory lock around the test poll
 R5  1P1+1P2  lock-setup graceful; /validate auth FLIP (oscillation begins)
 R6  2P2      audit→503; /validate oscillation RESOLVED via bearer-token gate (JWT, not pg_user_id)
 R7  1P1      reconnect-during-in-flight-cycle stale write-back → gate reactivate on advisory lock
 R8  1P1+1P2  /rotate same race (shared _credential_poll_lock helper); bad-folder (P2) → ticketed
 R9  1P1      /disconnect lets in-flight cycle keep ingesting → ticketed as edge #12 (adapter fix)
 Lessons: SPLIT conflated-concern oscillation (don't freeze); ALLOW_LEGACY_HEADER_AUTH=true in prod.

═══════════════════════════════════════════════════════════════════════
ENV / KNOWN ISSUES (carried forward)
═══════════════════════════════════════════════════════════════════════
- Run tests with DBOS_SYSTEM_DATABASE_URL set (any value).
- Pre-existing test failures UNRELATED to Granola (do NOT fix here):
  * 1 unit: tests/unit/account_provisioning/test_materialization.py::
    TestSqlTextSanity::test_upsert_summary_uses_unique_interaction_id_index
    (old single-col ON CONFLICT migrated to composite; verified failing on
    main without Granola code).
  * 16 integration: tests/integration/test_queue_lifecycle.py (_SessionStub; predates).
- Local .venv needs cryptography>=44.0.0 (pinned in requirements.txt; Railway
  has it) for vault/scheduler imports.
- tasks/llm-modernization-investigation.md is an UNTRACKED file from another
  session — NOT part of this work; leave it alone.
- The 2 ticketed edges (plan §2.1 #12/#13) are the disconnect-during-sync
  ingestion gap + the bad-folder stuck row — bounded, documented, fast-follow.
```

---

## Why the cron pinger was deferred (context for the next author)

Phase 2f shipped + merged + deployed + prod-verified the admin endpoints. The user chose to HOLD the 5-min cron trigger because: (1) the Railway curl-cron is fiddly (image entrypoint + shell env-expansion + dashboard-only cronSchedule; can't be created safely via the Railway MCP); (2) flipping the switch deserves to be done deliberately alongside the first real /connect E2E. The endpoint (`/internal/granola/cron-tick`) and `INTERNAL_CRON_SECRET` are verified ready; this session only needs to add the recurring trigger (GitHub Actions scheduled workflow recommended over the Railway curl-cron) — and should do edge #12 (adapter archived_at-awareness) before/with it, since the pinger makes that race live.
