# Next Session Opening Prompt (wire the EventBridge Scheduler trigger + first real /connect E2E)

**Written:** 2026-05-25, after edge #12 (adapter `archived_at`-awareness) merged as PR #30 `06415fa`, deployed (Railway `c252ddda` SUCCESS), and was prod-verified. The user chose to pause at that milestone; this session wires the trigger + runs the first real /connect E2E.

**Paste the block below as the opening message of the next Claude session.**

---

```
You're picking up a multi-session project: the Granola.ai → EQ transcript
ingestion integration, in live-transcription-fastapi (/Users/peteroneil/EQ-CORE/
live-transcription-fastapi). The previous session shipped edge #12 (the adapter
archived_at-awareness fix) end to end — merged PR #30 as 06415fa, deployed,
prod-verified. THIS session WIRES THE 5-MINUTE TRIGGER via AWS EventBridge
Scheduler (the real "flip the switch" moment, decided last session) + runs the
first real /connect end-to-end with the founder's own Granola account.

CONTINUITY IS CRITICAL. Read everything before you touch anything. Trust but
verify each artifact; if anything is missing or doesn't match, STOP and tell me.

═══════════════════════════════════════════════════════════════════════
STEP 1 — RUN /context-restore FIRST
═══════════════════════════════════════════════════════════════════════
Run /context-restore. It must load this checkpoint:
  ~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/
  20260525-092119-edge-12-shipped-deployed-eventbridge-trigger-next.md
If it loads NO_CHECKPOINTS or a checkpoint whose title is NOT
"edge-12-shipped-deployed-eventbridge-trigger-next", STOP and tell me.

═══════════════════════════════════════════════════════════════════════
STEP 2 — READ THESE (top to bottom; complete ALL before acting)
═══════════════════════════════════════════════════════════════════════
1. docs/superpowers/specs/NEXT-SESSION-START-HERE.md — status dashboard.
2. tasks/granola-integration-plan.md — §Phase 2e (the scheduler the trigger
   wakes), §Phase 2g (next phase), §Phase 2.1 follow-ups #12 (SHIPPED — edge
   #12), #13 (bad-folder, this session if time), #14 (defer-path atomicity,
   deferred), #15 (generation token, deferred). LOCKED-23..44.
3. routers/granola_cron.py — the endpoint the trigger POSTs:
   POST /internal/granola/cron-tick, X-Internal-Cron-Secret header.
   (503 if secret unset, 401 if wrong/missing, 202 + {"enqueued":N,...} on ok.)
4. services/granola_ingestion/scheduler.py — list_active_credentials →
   per-credential GRANOLA_POLL_QUEUE workflow (the dormant dispatch path).
5. services/granola_ingestion/adapter.py — run_one_cycle + _credential_is_active
   (edge #12 liveness gates) + the /connect → run_one_cycle "save & test" path.
6. routers/granola.py — the Phase 2f admin endpoints (/validate, /connect, etc.)
   the E2E exercises.
7. tasks/lessons.md (bottom) — the Codex-gate lessons (FREEZE / SPLIT /
   gate-before-every-mutation floor) + the ALLOW_LEGACY_HEADER_AUTH=true finding.
8. MEMORY.md + project_granola_integration.md — Active Work = EDGE_#12_MERGED_DEPLOYED.
9. feedback memories: feedback_shared_infrastructure_collision (LOAD-BEARING
   before the E2E), feedback_branch_safety, feedback_tenant_isolation,
   feedback_test_pattern_no_docker, feedback_codex_pre_merge_gate.
10. reference_railway_proxy_timeout + reference_railway_project_ids.

═══════════════════════════════════════════════════════════════════════
STEP 3 — VERIFY STATE
═══════════════════════════════════════════════════════════════════════
  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi
  git branch --show-current        # expect: main
  git log --oneline -3             # tip includes 06415fa (edge #12 #30)
  git status --short               # ignore tasks/llm-modernization-investigation.md
  curl -s https://live-transcription-fastapi-production.up.railway.app/health
  # expect {"status":"ok"}
If main lacks 06415fa or /health is non-200, STOP.

═══════════════════════════════════════════════════════════════════════
STEP 4 — EXECUTE (decide ORDER with the user)
═══════════════════════════════════════════════════════════════════════
1. WIRE THE AWS EVENTBRIDGE SCHEDULER TRIGGER. Provision via the aws-api MCP
   (user authorizes each AWS change; my CLI principal is peter-admin-cli/admin):
   - EventBridge Connection (auth type API_KEY; header X-Internal-Cron-Secret =
     INTERNAL_CRON_SECRET's value — operator supplies it; it's only in Railway
     today, so copy it or rotate + set both Railway env + the Connection).
   - API destination → POST the PUBLIC cron-tick URL
     (https://live-transcription-fastapi-production.up.railway.app/internal/granola/cron-tick).
   - IAM role Scheduler assumes to invoke the API destination.
   - Schedule: rate(5 minutes), flexible-time-window OFF, target = the API
     destination, with a DLQ (SQS) for failed invocations.
   - Verify: one real tick → 202, {"enqueued":0,...} (0 active credentials yet).
   - Commit docs/infrastructure/granola-eventbridge-scheduler.md (live ARNs +
     exact create/teardown/modify aws commands — the bridge to future IaC; repo
     has NO IaC today). This commit goes on a branch + PR (or main w/ user auth).
2. FIRST REAL /connect E2E with Peter's Granola key + a real test folder.
   Pre-flight the shared-infra collision check (feedback_shared_infrastructure_collision):
   ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head
   Then /validate → /connect → wait one 5-min tick → verify in Neon:
   vault.user_credentials row, external_integration_runs status='success',
   raw_interactions interaction_type='meeting' row, no DLQ, downstream consumed.
   LOCKED-11 atomic cleanup of test artifacts afterward.
3. EDGE #13 — /connect bad-folder recovery (plan §2.1 #13). Lower priority.
4. THEN Phase 2g — transactional email on credential breakage (LOCKED-32).

═══════════════════════════════════════════════════════════════════════
NON-NEGOTIABLE DISCIPLINES
═══════════════════════════════════════════════════════════════════════
- git branch --show-current immediately before every commit (shared checkout).
- Per-action user authorization for: push-to-main, merge, Railway changes, AWS
  changes (incl. the EventBridge provisioning), GitHub-secret changes. Feature
  branch + PR + branch push are fine without asking.
- Codex pre-merge gate mandatory before any merge (4-round soft cap; the floor
  is the real stop signal — see lessons.md "gate-before-every-mutation" lesson).
- Tenant isolation: every query carries tenant_id. NEVER modify downstream
  envelope contracts (LOCKED-38).
- No Docker in tests; AsyncMock + the _FakeConn/_liveness_gate patterns in
  tests/unit/granola_ingestion/test_adapter.py. Run tests with
  DBOS_SYSTEM_DATABASE_URL set; use .venv/bin/python.
- ⚠️ ALLOW_LEGACY_HEADER_AUTH=true IN PROD: get_auth_context_* does NOT enforce
  JWT in prod. Any new JWT-only endpoint needs a bearer-token gate or pg_user_id.

USER POSTURE: Non-developer founder. Plain-English always. Make confident
technical calls; surface product/strategic decisions, scope deviations, and
risky/destructive ops. The user is careful + likes to understand WHY — when you
recommend something, give the honest tradeoff, not the convenient answer.

═══════════════════════════════════════════════════════════════════════
KEY STATE (verified 2026-05-25 end-of-edge-#12)
═══════════════════════════════════════════════════════════════════════
live-transcription-fastapi main: 06415fa (edge #12 #30). eq-frontend main: 7905222.
Railway prod: project 847cfa5a-b77c-4fb0-95e4-b20e8773c23e, env
  e4c5ec15-1931-4632-9e58-92d9c6be4261, service 59a69f3d-9a24-4041-942a-891c4a81c5fb.
  Deploy c252ddda SUCCESS. /health 200. INTERNAL_CRON_SECRET set (value only in
  Railway). ALLOW_LEGACY_HEADER_AUTH=true.
AWS (us-east-1, acct 211125681610; aws-api MCP principal = peter-admin-cli):
  KMS CMK 59a0e2bc-...; IAM user eq-vault-service. 16 EventBridge rules, ZERO
  scheduler schedules (Scheduler is net-new). ~36 SQS queues.
Neon prod: project super-glitter-11265514, branch br-holy-block-ads5069w, db neondb.

═══════════════════════════════════════════════════════════════════════
KNOWN ISSUES (carried forward)
═══════════════════════════════════════════════════════════════════════
- Pre-existing test failures UNRELATED to Granola (do NOT fix here): 1 unit
  (test_upsert_summary_uses_unique_interaction_id_index), 16 integration
  (test_queue_lifecycle).
- verify_consumer_contracts.py exits 1 on a pre-existing stale-rule-registry
  WARNING (2 EventBridge rules not in its CONSUMERS list) — NOT envelope drift;
  the 3 consumers + the generic/meeting envelope validate ✓. Proven via stash test.
- tasks/llm-modernization-investigation.md is another session's UNTRACKED WIP — leave it.
- Deferred edge-#12 hardening (ticketed, NOT this session): §2.1 #14 defer-path
  write atomicity (Codex R8 residual, benign sub-ms window); §2.1 #15
  credential-generation token (Codex R6#2 defense-in-depth, lock-prevented today).
```

---

## Why this session paused before the trigger

Edge #12 (the adapter hardening that makes a disconnect abort an in-flight sync cleanly) was a large, concurrency-rich unit: 10 Codex rounds, merged + deployed + prod-verified. The trigger + the first real /connect E2E were always designed to go together as the deliberate "flip the switch" moment, and the E2E is interactive (needs the founder's real Granola key + watching a meeting land), so the user chose to do them fresh rather than rush them at the tail of a big session.
