# Session: Phase 2f follow-ups — edge #12 → wire trigger → first /connect E2E → edge #13

Start state: `main` @ `1831235` (Phase 2f #29 `260b863` shipped + prod-verified). /health 200.
Plan spec: `tasks/granola-integration-plan.md` §2.1 #12 (line 955) + #13 (line 956); §Phase 4c E2E (836-877).
Locked decisions this session (with user, 2026-05-25):
- Execution order: **#12 first** (it makes the disconnect-during-sync race live once the trigger runs), then trigger, then E2E, then #13.
- Trigger platform: **AWS EventBridge Scheduler** (free at our volume — 8.7k/mo vs 14M free tier; I build it all via AWS MCP w/ user authorization). Co-exists with existing EventBridge event fabric (16 rules) but is a net-new *scheduler* (0 schedules today).
- Infra documentation: commit `docs/infrastructure/granola-eventbridge-scheduler.md` (live ARNs + exact create/teardown/modify commands) — the bridge to future IaC. No IaC framework adopted now (repo has none).

---

## 1. EDGE #12 — adapter `archived_at`-awareness  [IN PROGRESS]
Branch: `phase-2.1/granola-adapter-archived-at-guard` off `main`.
Root fix (plan §2.1 #12): a credential archived/disconnected mid-cycle must abort the in-flight cycle cleanly instead of ingesting a few more notes.
- [ ] Guard the 3 credential-state UPDATE SQLs on `AND archived_at IS NULL`:
      `_UPDATE_CREDENTIAL_POLL_SUCCESS_SQL` (adapter.py:1286), `_UPDATE_CREDENTIAL_STATUS_SQL` (:1298),
      `_INCREMENT_CREDENTIAL_FAILURES_SQL` (:1308). (Tenant+user already in WHERE.)
- [ ] Re-check the credential is still active before each publish in `process_note` / `_ingest_scenario_a`
      (re-read status/archived_at just before `text_clean_service.process`; abort the note if archived mid-cycle).
- [ ] Decide + handle the "UPDATE matched 0 rows because archived" signal (e.g. `_record_credential_transient_failure`
      already returns threshold when row gone — extend the success/status helpers similarly; don't crash the cycle).
- [ ] TDD: AsyncMock tests (mirror tests/unit/granola_ingestion/test_adapter.py `_FakeConn`/`_FakePool`):
      archived-mid-cycle → no publish + clean abort; 3 UPDATE SQLs contain `archived_at IS NULL`; active path unchanged.
- [ ] Run suite with `DBOS_SYSTEM_DATABASE_URL=x pytest` — 0 new regressions (baseline: 1 unit + 16 integration pre-existing).
- [ ] `verify_consumer_contracts.py --source generic --interaction-type meeting` → 0 drift (envelope untouched; sanity).
- [ ] Codex pre-merge gate (4-round soft cap; SPLIT/FREEZE per the two oscillation lessons).
- [ ] Open PR. DO NOT merge / push-to-main without per-action user auth.
NOTE: closing #12 means the per-endpoint `_credential_poll_lock` gates become belt-and-suspenders (can stay; removing is optional, out of scope this PR unless trivial).

## 2. WIRE THE 5-MIN TRIGGER — EventBridge Scheduler  [after #12 merges]
- [ ] Provision via AWS MCP (user authorizes each AWS change): Connection (secret header) + API destination
      (POST public /internal/granola/cron-tick) + IAM invoke role + `rate(5 minutes)` schedule + DLQ.
- [ ] Put INTERNAL_CRON_SECRET value into the EventBridge Connection (it must match Railway's env value).
- [ ] Verify: one real tick → cron-tick returns 202; with 0 credentials enqueued=0; logs show the dispatch.
- [ ] Commit `docs/infrastructure/granola-eventbridge-scheduler.md` (ARNs + create/teardown/modify commands).

## 3. FIRST REAL /connect E2E  [after trigger live]
- [ ] Pre-flight collision check (feedback_shared_infrastructure_collision): `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head`.
- [ ] User provides real Granola API key + test folder. /validate → /connect → wait one tick → verify ingest:
      vault.user_credentials row, external_integration_runs status='success', raw_interactions meeting row, no DLQ.
- [ ] LOCKED-11 atomic cleanup of test artifacts afterward.

## 4. EDGE #13 — /connect bad-folder recovery  [lower priority]
- [ ] Plan §2.1 #13 options: validate folder_id in /connect before store; OR let /connect re-configure a broken
      non-archived row instead of 409; OR PATCH /folder. Decide approach, implement smallest, Codex gate.

## Out of scope this session
- Phase 2g (transactional email on credential breakage) — its own session.
- Removing the per-endpoint advisory-lock gates (optional cleanup; leave unless trivial).

## Disciplines (non-negotiable)
- `git branch --show-current` immediately before every commit (shared checkout).
- Per-action user auth for push-to-main / merge / Railway / AWS / GitHub-secret changes.
- Tenant isolation: every query carries tenant_id. NEVER modify downstream envelope contracts (LOCKED-38).
- Tests: AsyncMock, no Docker. Run with DBOS_SYSTEM_DATABASE_URL set.
- ALLOW_LEGACY_HEADER_AUTH=true in prod (load-bearing context for any auth reasoning).

## Progress
- [x] §1 EDGE #12 coded + TDD (RED→GREEN): 3 SQL guards (`archived_at IS NULL`) + `_credential_is_active`
      recheck in run_one_cycle main loop + reprocess pass. 5 new tests. Full unit suite 496 pass /
      1 pre-existing failure / 0 regressions. 0 envelope contract drift (script exit-1 is pre-existing
      stale-rule-registry noise, proven via stash test — unrelated to this change).
- [x] §1 Codex pre-merge gate — 8 rounds. R1-R7 folded (each a real narrowing bug; gate passed/no-P1 from R2 on);
      R6#2 declined (reconnect-generation race is lock-prevented — both run_one_cycle callers hold the advisory
      lock, reactivate is gated on it); R8 residual (sub-ms window inside _defer_pending_account) ship+ticketed by
      user. 11 new tests; 504 unit pass / 1 pre-existing failure / 0 regressions; 0 envelope contract drift.
      Final gate state: PASS. Deferred items ticketed as plan §2.1 #14 (defer-path atomicity) + #15 (generation token).
- [x] §1 PR #30 opened, user-authorized merge → squash-merged `06415fa`, Railway `c252ddda` SUCCESS, prod-verified
      (/health 200, /validate 401, /status 400, cron-tick 401, /text/clean 422 — no regression). EDGE #12 DONE.
- [ ] §2 wire EventBridge Scheduler trigger + infra manifest (docs/infrastructure/granola-eventbridge-scheduler.md) — NEXT
- [ ] §3 first real /connect E2E (interactive — needs Peter's Granola key)
- [ ] §4 edge #13
