# Next Session Opening Prompt (Phase 2e — Granola scheduler: Railway cron + DBOS workflow)

**Written:** 2026-05-24 end-of-session, after Phase 2d (the Granola adapter + Path 2 logic) was built across two PRs, hardened across 3 + 5 Codex rounds, merged, and deployed to production as inert code.

**Paste the block below as the opening message of the next Claude session.**

This is the canonical paste-ready handoff. Mirrors the structure of the Phase 2c and Phase 2d session openers — the new agent reads everything mandatory, writes a 2-paragraph confirmation, then builds Phase 2e (the scheduler that wires `run_one_cycle` to a 5-minute cadence via Railway cron + DBOS workflow).

---

```
You're picking up the Granola.ai transcript ingestion scheduler — the prior
session shipped Phase 2d (the adapter + Path 2 logic) across two PRs
end-to-end. Module is live in production as inert code. PR #26 merged
`fa97477` (PR-X1: text_clean_service extraction); PR #27 merged `607121d`
(PR-X2: Granola adapter + Path 2). Railway deployment `edbcf4ef` SUCCESS;
/health 200; /text/clean smoke probe HTTP 200 post-deploy verified the
extracted text_clean_service did not regress production; 5 Codex
pre-merge rounds on PR-X1 (R5 CLEAN cumulative) + 3 substantive Codex
rounds on PR-X2 (R3 CLEAN delta after R1+R2 folds); 84 granola unit
tests; 327 total passing; 0 regressions.

THIS SESSION'S JOB: build Phase 2e — the scheduler. Two new files
(services/granola_ingestion/scheduler.py + routers/granola_cron.py)
that wire the inert adapter to a 5-minute Railway-cron + DBOS-workflow
cadence per LOCKED-28 + LOCKED-39. Each cron tick loads active
credentials, dispatches per-credential workflows with explicit
SetWorkflowID (workflow_id = f"granola_poll_{credential_id}_{cycle_window_minute//5}")
so DBOS dedups overlapping cycles. ~0.5 day estimated.

CONTINUITY IS CRITICAL. The prior session shipped TWO PRs (PR-X1 +
PR-X2) and the trajectory across both informs Phase 2e. Read everything.
Trust but verify each artifact loads as expected. If anything is missing
or unexpected, STOP and surface to the user — do not improvise.

═══════════════════════════════════════════════════════════════════════
STEP 1 — RUN /context-restore FIRST
═══════════════════════════════════════════════════════════════════════

Before anything else, run /context-restore. It will load the most recent
checkpoint:

  ~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/
  <timestamp>-phase-2d-shipped-phase-2e-next.md

If /context-restore returns NO_CHECKPOINTS or a different title, STOP and
surface to the user immediately — the handoff is broken.

═══════════════════════════════════════════════════════════════════════
STEP 2 — VERIFY CURRENT GIT + PRODUCTION STATE
═══════════════════════════════════════════════════════════════════════

Verify both checkouts:

  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi
  git branch --show-current     # expect: main
  git log --oneline -6          # expect tip is a docs(handoff) commit
                                # above 607121d (PR-X2 merge); current
                                # tip on main is the handoff commit →
                                # 607121d feat(granola): Phase 2d — Granola
                                #         adapter + Path 2 logic (PR-X2) (#27)
                                # → 906c6e0 docs(plan): record PR-X1 SHA fa97477
                                #         at LOCKED-41
                                # → fa97477 refactor(text-clean): extract
                                #         services/text_clean_service.py
                                #         from routers/text.py (PR-X1) (#26)
                                # → 4d7f415 docs(handoff): make Phase 2d stop
                                #         conditions match parity of Phase 2c
  git status --short            # expect: clean

  git -C /Users/peteroneil/eq-frontend log origin/main --oneline -3
                                # expect tip: 7905222 feat(prisma): Phase 2a
                                # — Granola vault schema (#418)
                                # — DO NOT switch the main checkout's
                                # branch (another agent may be active)

Verify production health (stop conditions):

  curl -s https://live-transcription-fastapi-production.up.railway.app/health
  # expect: {"status":"ok"}

Verify the Phase 2d adapter loaded inertly in production:

  .venv/bin/python -c "from services.granola_ingestion import run_one_cycle, process_note, CycleResult, IngestionOutcome, Scenario; print('OK')"
  # expect: OK

  .venv/bin/python -c "from services.text_clean_service import process, TenantIsolationError, Lane2Extras, try_reserve_lane2_slot, release_lane2_slot, Lane1PublishError; print('OK')"
  # expect: OK

Verify production Neon (via Neon MCP, project super-glitter-11265514,
branch br-holy-block-ads5069w, database neondb):
  - schema 'vault' exists
  - 3 tables present:
    * vault.user_credentials (15 cols)
    * vault.credential_access_log (11 cols)
    * public.external_integration_runs (17 cols, INCLUDING granola_note_snapshot JSONB per LOCKED-44)

If any of those is wrong, STOP and surface.

═══════════════════════════════════════════════════════════════════════
STEP 3 — READ THE COMPREHENSIVE HANDOFF DOCUMENT
═══════════════════════════════════════════════════════════════════════

The PRIMARY HANDOFF DOCUMENT is this file itself:

  docs/superpowers/specs/2026-05-24-phase-2e-session-prompt.md

It contains the full sequence below. After STEP 3, proceed to STEP 4 (the
mandatory reads).

═══════════════════════════════════════════════════════════════════════
STEP 4 — MANDATORY READS (in this order; do NOT skip)
═══════════════════════════════════════════════════════════════════════

Per the memory entry feedback_complete_all_handoff_reads_before_action.md:
complete EVERY read BEFORE starting Phase 2e implementation. Don't write
code mid-read. Use parallel tool calls to read fast.

Per the NEW lesson codified this session (verify_mandatory_read_files_exist):
before claiming a file as a mandatory read, ls/Read it first to confirm
existence — the Phase 2c→2d handoff listed services/text_clean_service.py
as a mandatory read but the file didn't exist (LOCKED-41 had locked the
decision without scheduling the extraction work; PR-X1 added it). This
session's prompt has been pre-flighted: every path below has been
verified to exist on main at commit 607121d.

1. WAYFINDING:
   docs/superpowers/specs/NEXT-SESSION-START-HERE.md
   — High-level status dashboard: PHASE_2D_SHIPPED state, Phase 2e scope
     summary, full lookup table of what's live in production.

2. THE EXECUTABLE PLAN (load-bearing; ~1080 lines):
   tasks/granola-integration-plan.md
   — Phase 2e is §Phase 2e. LOCKED-23 through LOCKED-44 are locked.
   — Phase 2a, 2b, 2c, 2d all SHIPPED (see §LOCKED-41 for PR-X1 SHA
     fa97477; PR-X2 SHA 607121d in the trajectory section).
   — Pay extra attention to LOCKED-28 (5-min polling cadence), LOCKED-39
     (Railway cron + DBOS queue with explicit SetWorkflowID — NOT
     @DBOS.scheduled which is deprecated per
     docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md:768).

3. THE DBOS ARCHITECTURE DOC (load-bearing for scheduler design):
   docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md
   — §768 deprecation of @DBOS.scheduled
   — §504 SetWorkflowID dedup pattern
   — Reference for how the eq-email-pipeline DBOS workflows are
     structured (the closest precedent).

4. PHASE 2d ADAPTER CODE (read THE WHOLE THING — Phase 2e dispatches it):
   services/granola_ingestion/adapter.py — run_one_cycle, process_note,
     CycleResult. Public API the scheduler will invoke.
   services/granola_ingestion/path2.py — referenced by adapter; skim
     for context.
   services/granola_ingestion/outcomes.py — IngestionOutcome enum.
   services/granola_ingestion/__init__.py — public exports.

5. PHASE 2d ADAPTER TESTS (study the mock patterns Phase 2e tests reuse):
   tests/unit/granola_ingestion/test_adapter.py — _FakeConn, _FakePool,
     _FakeCredential patterns. Phase 2e tests will mock DBOS dispatch +
     reuse the existing _FakeConn for state queries.

6. PHASE 2c API CLIENT (skim — Phase 2e doesn't touch it directly):
   services/granola_ingestion/api_client.py — public methods reference.
   services/granola_ingestion/errors.py — 8 error codes.

7. PHASE 2b VAULT (skim — Phase 2e will list active credentials):
   services/vault/__init__.py — exports
   services/vault/user_credentials.py — accessor signatures. Phase 2e's
     scheduler needs a NEW vault accessor (or a direct SELECT) to LIST
     active credentials: `SELECT id, tenant_id, user_id FROM
     vault.user_credentials WHERE status='active' AND archived_at IS
     NULL`. Decide where this lives — pure SELECT (no decryption) can be
     a plain SQL helper or a new vault accessor. The plan §Phase 2e
     pseudocode uses `list_active_credentials_step()` as a DBOS step.

8. PR-X1 EXTRACTION (PR #26, merged commit fa97477):
   services/text_clean_service.py — public API: process(), Lane2Extras,
     try_reserve_lane2_slot, release_lane2_slot, Lane1PublishError,
     TenantIsolationError. Phase 2e doesn't call this directly; the
     adapter does. Skim for the slot lifecycle so you understand what
     run_one_cycle does internally.

9. PRE-EXISTING DBOS RUNTIME:
   services/dbos_runtime.py — DBOS lifespan + initialization. Phase 2e
     uses the same DBOS instance; cron-handler calls into DBOS.start_workflow.

10. EXISTING ROUTER PATTERNS (for routers/granola_cron.py shape):
    routers/queue_actions.py — example of a router that uses DBOS workflows
    routers/text.py — example of internal endpoint patterns

11. THE BRAINSTORM (background only; don't re-litigate):
    docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md
    — Especially Q3 (polling cadence = 5 min) + the existing infrastructure
      section.

12. THE CONSUMER-CONTRACT VERIFICATION SCRIPT (load-bearing for pre-merge):
    scripts/verify_consumer_contracts.py — Phase 2e doesn't change the
    envelope, so this should remain clean. But re-run pre-merge as a
    discipline check.

13. PR #26 + PR #27 (merged PRs from this session):
    gh pr view 26  # PR-X1 description + 5 Codex rounds summary
    gh pr view 27  # PR-X2 description + 3 substantive Codex rounds summary

14. AUTO-MEMORY (auto-loads, but verify):
    ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/
    memory/MEMORY.md
    — Active Work entry should read PHASE_2D_SHIPPED + PHASE_2E_NEXT 2026-05-24.

15. PROJECT MEMORY SNAPSHOT:
    ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/
    memory/project_granola_integration.md
    — Final state PR-X1 + PR-X2 + Phase 2e scope.

16. LOAD-BEARING FEEDBACK MEMORIES (read all):
    - feedback_complete_all_handoff_reads_before_action.md
    - feedback_envelope_contract_immutable.md (LOCKED-38; Phase 2e doesn't
      touch envelopes but the discipline carries forward)
    - feedback_codex_pre_merge_gate.md
    - feedback_shared_infrastructure_collision.md
    - feedback_tenant_isolation.md
    - feedback_branch_safety.md
    - feedback_test_pattern_no_docker.md

17. NEW LESSONS FROM THIS SESSION (at bottom of tasks/lessons.md):
    - "Verify mandatory-read files exist before declaring them in
      handoffs" (NEW; the Phase 2c→2d gap that PR-X1 closed)
    - "Adapter-pattern PRs converge in 2-3 Codex rounds with scope-creep
      follow-ons" (NEW; refines [[feedback-codex-pre-merge-gate]] with
      the PR-X1 + PR-X2 trajectory data)
    - "Pre-write idempotency anchor BEFORE downstream publish" (NEW; the
      'in_progress' status pattern from PR-X2 R1 P2 fix)
    — Plus all earlier lessons (carry forward).

18. AFTER ALL READS, write a 2-paragraph confirmation:
    - Para 1: What this session executes — Phase 2e
      (services/granola_ingestion/scheduler.py + routers/granola_cron.py
      wire the inert adapter to a 5-min Railway-cron + DBOS-workflow
      cadence per LOCKED-28 + LOCKED-39). Each cron tick lists active
      credentials, dispatches per-credential workflows with
      SetWorkflowID derived from (credential_id, cycle_window_minute),
      DBOS dedups overlapping cycles. Pure orchestration in workflows;
      all I/O lives in @DBOS.step. New env var INTERNAL_CRON_SECRET for
      cron auth. Mention AsyncMock unit tests for workflow_id dedup +
      cron auth + lifecycle.
    - Para 2: Critical disciplines: (a) work on feature branch
      phase-2e/granola-scheduler off live-transcription-fastapi main;
      (b) verify branch via `git branch --show-current` IMMEDIATELY
      before any commit; (c) Codex pre-merge review is MANDATORY
      (4-round soft cap; extendable per the round-N scope-creep follow-on
      lesson — both PR-X1 and PR-X2 needed 2-3 substantive rounds);
      (d) per-action user authorization for push to main / merge /
      destructive ops; (e) DO NOT use @DBOS.scheduled (deprecated per
      LOCKED-39 / DBOS architecture doc §768); (f) workflow_id MUST be
      f"granola_poll_{credential_id}_{cycle_window_minute//5}" so DBOS
      idempotency catches overlapping cycles; (g) cron auth via
      X-Internal-Cron-Secret header — random 32-byte hex secret in
      Railway env var; (h) Phase 2e ships scheduler but does NOT
      activate ingestion in production because no Phase 2f means no
      connected credentials — the scheduler will find 0 active rows
      and exit cleanly each cron tick until Phase 2f lands.

═══════════════════════════════════════════════════════════════════════
STEP 5 — EXECUTE PHASE 2e
═══════════════════════════════════════════════════════════════════════

Per tasks/granola-integration-plan.md §Phase 2e:

**Branch:** create feature branch `phase-2e/granola-scheduler` off main

**New files:**
- services/granola_ingestion/scheduler.py — DBOS workflow + steps
- routers/granola_cron.py — HTTP endpoint Railway cron POSTs every 5 min
- tests/unit/granola_ingestion/test_scheduler.py — AsyncMock unit tests
- tests/unit/test_granola_cron.py — cron endpoint auth + dispatch tests

**Modified files:**
- main.py — register routers/granola_cron.router on the FastAPI app

**Pseudocode (per LOCKED-39):**
```python
# routers/granola_cron.py
@router.post("/internal/granola/cron-tick")
async def granola_cron_tick(
    _: InternalCronAuth = Depends(verify_internal_cron_secret),
):
    """Railway cron POSTs here every 5 min."""
    credentials = await list_active_credentials_step()  # DBOS step
    cycle_minute = int(datetime.utcnow().timestamp() // 60)
    cycle_window = cycle_minute // 5
    for credential in credentials:
        workflow_id = f"granola_poll_{credential.id}_{cycle_window}"
        await DBOS.start_workflow(
            granola_poll_one_credential,
            credential.id, credential.tenant_id, credential.user_id,
            workflow_id=workflow_id,  # SetWorkflowID dedup
        )
    return {"enqueued": len(credentials), "cycle_window": cycle_window}


# services/granola_ingestion/scheduler.py
@DBOS.workflow()
async def granola_poll_one_credential(
    credential_id: UUID, tenant_id: UUID, user_id: UUID
):
    """Pure orchestration. All I/O lives in @DBOS.step functions."""
    credential = await load_credential_step(
        tenant_id=tenant_id, user_id=user_id
    )
    if credential is None or credential.status != "active":
        return PollResult(skipped=True, reason="credential_inactive")
    pool = get_async_pool()  # the existing asyncpg pool
    result = await run_one_cycle(credential=credential, pool=pool)
    return PollResult.from_cycle_result(result)


@DBOS.step()
async def list_active_credentials_step() -> list[CredentialMetadata]:
    """SELECT id, tenant_id, user_id FROM vault.user_credentials
    WHERE status='active' AND archived_at IS NULL"""
    ...


@DBOS.step()
async def load_credential_step(
    *, tenant_id: UUID, user_id: UUID
) -> Optional[GranolaCredential]:
    """Delegates to vault.get_granola_credential_for_user with
    caller_module='services.granola_ingestion.scheduler'."""
    ...
```

**Railway cron setup (do AFTER PR merges; user authorization required):**
- Schedule: `*/5 * * * *`
- Command: ``curl -X POST -H "X-Internal-Cron-Secret: $INTERNAL_CRON_SECRET" http://localhost:8080/internal/granola/cron-tick``
- Or Railway's native cron service if available
- New env var INTERNAL_CRON_SECRET (random 32-byte hex)

**Health endpoint addition:** `/health` already returns `{"status":"ok"}`;
consider adding `/health/granola` returning
`{"last_cycle_success_at": "...", "active_credentials": N,
"scheduling": "railway-cron"}` so operators have observability.

**Unit tests (AsyncMock-based, no Docker):**
- workflow_id dedup: two start_workflow calls in same cycle_window with same
  credential_id → DBOS returns a no-op for the second (mock DBOS behavior)
- Cron auth: missing / wrong X-Internal-Cron-Secret → 401
- Cron tick with N credentials → DBOS.start_workflow called N times with
  distinct workflow_ids
- Cron tick with 0 active credentials → returns {"enqueued": 0}; no
  DBOS calls
- Workflow with credential.status='revoked' → PollResult(skipped=True)
- Workflow happy path: list_active → load → run_one_cycle invoked with
  the right credential

**Pre-merge verification:**
- python scripts/verify_consumer_contracts.py --source generic
  --interaction-type meeting   # MUST stay 0 drift (Phase 2e doesn't
  touch envelopes)
- All Phase 2d tests still pass (no regressions in adapter behavior)

**Codex pre-merge review:**
1. Open PR to live-transcription-fastapi main.
2. Run `/codex review` (4-round soft cap; extend if real bugs surface
   each round per Phase 2c/2d precedents — both took 2-3 substantive
   rounds).
3. Fold all P1 findings; P2 judgment call.
4. Surface to user for merge authorization after gate passes (0 P1).

**Merge + deploy:**
1. User authorizes merge.
2. Squash-merge to main.
3. Railway deploys.
4. /health 200 verified.
5. After merge: configure Railway cron + set INTERNAL_CRON_SECRET env
   var (user-authorized per-action). The scheduler will start running
   every 5 min but find 0 active credentials until Phase 2f adds
   /connect.

═══════════════════════════════════════════════════════════════════════
STEP 6 — PHASE 2f IS NOT THIS SESSION
═══════════════════════════════════════════════════════════════════════

Phase 2f (~0.5 day) is the admin endpoints (/validate, /connect, /rotate,
/status, /disconnect). This is what users hit to actually connect a
Granola account. After Phase 2f the scheduler from Phase 2e starts
finding active credentials and polling Granola for real.

DO NOT START PHASE 2f THIS SESSION. Phase 2e ships scheduler + merged,
then Phase 2f is its own session.

═══════════════════════════════════════════════════════════════════════
USER POSTURE (LOAD-BEARING — DO NOT VIOLATE)
═══════════════════════════════════════════════════════════════════════

Non-developer founder. Plain-English explanations always. Confident
technical decisions; surface only product / strategic decisions, scope
deviations, or destructive ops. AI agent doesn't push or merge without
per-action authorization (PR creation is fine; push to feature branches
is fine; push to main + merge + force-push require explicit auth each
time).

═══════════════════════════════════════════════════════════════════════
CRITICAL DISCIPLINES
═══════════════════════════════════════════════════════════════════════

1. **Branch verification before commits.** `git branch --show-current`
   IMMEDIATELY before any commit. Prior sessions experienced silent
   branch switches in shared checkouts.

2. **Codex pre-merge gate is MANDATORY.** 4-round soft cap. PR-X1 ran
   5 rounds (R5 CLEAN cumulative); PR-X2 ran 3 substantive rounds (R3
   CLEAN delta after R1+R2 folds). Both had real findings every round
   until convergence — the round-N scope-creep follow-on pattern is
   normal. Extend if R4+ surfaces more real bugs. Switch to
   `--base <prior-commit>` if cumulative diff exceeds ~1500 lines
   (PR-X1 R4+, PR-X2 R2+ both used this — wrapper times out otherwise).

3. **DO NOT use @DBOS.scheduled decorator** (deprecated per
   LOCKED-39 and docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md
   §768). The correct pattern is external Railway cron + explicit
   SetWorkflowID — `f"granola_poll_{credential_id}_{cycle_window//5}"`.

4. **workflow_id is the DBOS idempotency key.** Same credential_id +
   same cycle_window must produce the same workflow_id so DBOS dedups
   overlapping cycles (a 6-min cycle on a 5-min schedule would otherwise
   start a second instance while the first is running).

5. **Pure orchestration in workflows; all I/O in @DBOS.step.** Mirrors
   the repo's existing DBOS discipline. Each I/O operation (DB read,
   Granola API call, KMS decrypt) is its own @DBOS.step with explicit
   `retries_allowed=False` (or `True` with bounded attempts) per the
   DBOS arch doc.

6. **The scheduler is the WAKE-UP. It does NOT flip the switch.** Until
   Phase 2f adds /connect, no credentials exist; the scheduler runs and
   exits cleanly each tick. This is desirable — we ship the scheduler
   first so Phase 2f's /connect → run_one_cycle path works end-to-end
   the day Phase 2f deploys.

7. **Cron auth via X-Internal-Cron-Secret header.** Random 32-byte hex
   in env var INTERNAL_CRON_SECRET. FastAPI dependency rejects requests
   without the header. Do NOT use Railway's "private network" assumption
   alone — defense-in-depth.

8. **NEVER modify downstream Pydantic envelope contracts** (LOCKED-38).
   Phase 2e doesn't touch envelopes, but the discipline carries forward
   for future phases.

9. **Tenant isolation is non-negotiable.** Every DB query includes
   tenant_id. The scheduler's `list_active_credentials_step` is the
   exception (it lists ACROSS tenants to find work), but every
   subsequent call (load_credential_step, run_one_cycle, adapter
   internals) carries tenant_id explicitly.

10. **No Docker in tests by default.** AsyncMock-style unit tests; the
    real-Neon + real-Granola E2E happens in Phase 4 with Peter as
    design partner #0.

═══════════════════════════════════════════════════════════════════════
STOP CONDITIONS (HARD — SURFACE TO USER IMMEDIATELY)
═══════════════════════════════════════════════════════════════════════

  - /context-restore returns NO_CHECKPOINTS or wrong checkpoint title
  - MEMORY.md Active Work doesn't read "PHASE_2D_SHIPPED" / "PHASE_2E_NEXT"
  - live-transcription-fastapi main is NOT at 607121d (or descendant)
  - eq-frontend main is NOT at 7905222 (or descendant)
  - Production /health returns non-200
  - Vault schema or 3 tables MISSING from production Neon
  - AWS infrastructure missing (verify):
    * aws kms describe-key --key-id 59a0e2bc-c636-45e8-bccf-427ad2426ad8
      → expect Enabled=true, KeyState=Enabled, KeyManager=CUSTOMER
    * aws iam get-user --user-name eq-vault-service
      → expect Arn=arn:aws:iam::211125681610:user/eq-vault-service
  - Another agent actively working in live-transcription-fastapi within
    the last hour (run: ls -lt ~/.claude/projects/-Users-peteroneil-EQ-
    CORE-live-transcription-fastapi/*.jsonl | head -3 — files modified
    in last hour = hazard signal; if so, switch to a Conductor worktree
    or coordinate with the other agent before any commit on shared
    branches)
  - User asks you to deviate from a LOCKED decision (LOCKED-23..44)
  - Phase 2e starts using @DBOS.scheduled instead of external cron +
    SetWorkflowID → STOP (LOCKED-39 violation)
  - Phase 2e starts modifying downstream Pydantic envelope contracts
    → STOP (LOCKED-38)
  - Phase 2e tries to manipulate Granola API directly from the
    cron-handler instead of dispatching to a DBOS workflow → STOP
    (durability + retry budget come from DBOS; running I/O directly in
    the cron handler loses both)

═══════════════════════════════════════════════════════════════════════
KEY STATE (verified 2026-05-24 end-of-session)
═══════════════════════════════════════════════════════════════════════

live-transcription-fastapi main:
  <handoff-commit> docs(handoff): Phase 2d shipped end-to-end, Phase 2e next
  607121d feat(granola): Phase 2d — Granola adapter + Path 2 logic (PR-X2) (#27)
  906c6e0 docs(plan): record PR-X1 SHA fa97477 at LOCKED-41
  fa97477 refactor(text-clean): extract services/text_clean_service.py from routers/text.py (PR-X1) (#26)
  4d7f415 docs(handoff): make Phase 2d stop conditions match parity of Phase 2c opener
  bb2c1af docs(handoff): Phase 2c shipped end-to-end, Phase 2d next
  030523c feat(granola): Phase 2c — Granola HTTP API client (#25)

eq-frontend main (unchanged):
  7905222 feat(prisma): Phase 2a — Granola vault schema (#418)

AWS (us-east-1, account 211125681610):
  KMS CMK    59a0e2bc-c636-45e8-bccf-427ad2426ad8 (alias eq-user-secrets)
  IAM user   eq-vault-service
  Auto-rotation enabled; next 2027-05-23

Railway (live-transcription-fastapi production):
  Project    847cfa5a-b77c-4fb0-95e4-b20e8773c23e
  Env        e4c5ec15-1931-4632-9e58-92d9c6be4261
  Service    59a69f3d-9a24-4041-942a-891c4a81c5fb
  Latest deploy edbcf4ef-8bf3-4be2-80da-b35c98cc267f SUCCESS (PR-X2 merge)
  /health 200 at https://live-transcription-fastapi-production.up.railway.app/health
  4 EQ_VAULT_* env vars set + working
  Phase 2d adapter loaded inertly; /text/clean post-PR-X1 smoke probe HTTP 200

Vercel (eq-frontend production):
  Project    prj_0wDppCftk1VrSAsYswI5pnNRHdN8
  Team       team_Hnnnu6r1trggeAXYWHXpKfMt
  Latest production deploy 2he8eDSfSLdapZ1eRXa6mSpjJkdq READY
  Canonical URL eq-frontend-two.vercel.app

Neon (production Postgres):
  Project    super-glitter-11265514 (eq-dev)
  Branch     br-holy-block-ads5069w (production)
  Database   neondb
  Vault schema + 3 tables LIVE (vault.user_credentials, vault.credential_access_log,
  public.external_integration_runs with granola_note_snapshot JSONB)

Linear:
  EQ-11 — schema drift family; unchanged this session.

═══════════════════════════════════════════════════════════════════════
SESSION RECAP — what shipped this session (2026-05-24)
═══════════════════════════════════════════════════════════════════════

This was a 2-PR session that built Phase 2d. The original plan was 1 PR;
mid-session investigation revealed that LOCKED-41 referenced a file
(services/text_clean_service.py) that didn't exist — the prior Phase 2b
and 2c sessions had shipped their pieces without doing the extraction
LOCKED-41 required. PR-X1 closed that gap before PR-X2 built on it.

**PR #26 (PR-X1) — services/text_clean_service.py extraction**
- Merged as commit fa97477 on 2026-05-24
- Pure refactor: lifted Lane 1 publish + Lane 2 dispatch + backpressure
  out of routers/text.py's private _process_after_slot_reserved into
  services/text_clean_service.py. routers/text.py now delegates.
- Public API: process(*, tenant_id, user_id, account_id, envelope,
  lane2_extras=None), try_reserve_lane2_slot(), release_lane2_slot(),
  Lane2Extras, ProcessResult, Lane1PublishError, TenantIsolationError.
- LOCKED-41 cross-tenant guard: tenant_id/user_id/account_id are
  explicit kwargs; process() raises TenantIsolationError on mismatch
  with envelope.
- 5 Codex rounds (R5 CLEAN cumulative):
  - R1: 1 P2 (slot double-release on non-Lane1 raises from process())
  - R2: CLEAN (delta on R1 fix)
  - R3: 1 P2 + 1 P3 (LOCKED-41 explicit kwargs missing; empty-string
    cleaned_transcript override treated as missing)
  - R4: CLEAN (delta on R3 fix)
  - R5: CLEAN (cumulative against main)
- 23 new unit tests in tests/unit/test_text_clean_service.py
- 9 existing integration tests in test_text_clean_response_decoupling.py
  updated to patch services.text_clean_service.* (the names moved); all
  still pass
- 268 unit tests passing post-merge

**PR #27 (PR-X2) — Phase 2d Granola adapter + Path 2 logic**
- Merged as commit 607121d on 2026-05-24
- New files: services/granola_ingestion/{outcomes,path2,adapter}.py +
  tests/unit/granola_ingestion/{test_path2,test_adapter}.py
- IngestionOutcome enum (5 values per Q7); Path 2 attendee classification
  + Scenario A/C/D branching; per-credential cycle composing vault +
  GranolaAPIClient + text_clean_service.
- Envelope per LOCKED-35/36: source="generic", interaction_type="meeting",
  content.format="plain", 6 granola_* extras keys.
- LOCKED-25 mitigation: interaction_type="meeting" (NOT "transcript" —
  the raw_interactions FK landmine).
- LOCKED-44: Scenario C captures granola_note_snapshot JSONB at defer
  time so the meeting remains recoverable if Granola removes the note.
- LOCKED-41: text_clean_service.process called with credential.tenant_id /
  credential.user_id / anchor_account_id as explicit kwargs.
- 3 substantive Codex rounds (R3 CLEAN delta after folds):
  - R1: 2 P1 + 1 P2 (stranded failed rows / cycle-end watermark race /
    publish→DB-fail duplicate publish)
  - R2: 1 P1 + 3 P2 (SELECT missing eq_interaction_id / UPSERT clobbers
    it on retry / retry budget not enforced on replay / Scenario C
    from failed-row replay never defers)
  - R3: CLEAN delta after R2 folds
  - R4: TIMEOUT (3814-line cumulative; codified workaround = delta
    scoping past 1500 lines)
- 38 new unit tests (17 path2 + 21 adapter, then +6 more during R1+R2
  folds = 27 adapter total = 84 granola tests total)
- 327 unit + integration tests pass overall post-merge
- production /text/clean smoke probe HTTP 200 post-PR-X1 deploy proved
  no regression
- production /health 200 + module imports cleanly post-PR-X2 deploy
  proved the inert adapter loads (this was the first PR to import
  services.vault → which means the cryptography wheel needs to be in
  the runtime environment; production Railway has it; local .venv
  parity is via TYPE_CHECKING guard so tests don't require cryptography)

**Lessons codified this session (in tasks/lessons.md):**

1. **Verify mandatory-read files exist before declaring them in
   handoffs.** The Phase 2c→2d handoff listed
   services/text_clean_service.py as a mandatory read but the file
   didn't exist; LOCKED-41 had locked the decision without scheduling
   the extraction work. Pre-flight every mandatory-read path with
   `ls` / `Read` before declaring it readable; when a LOCKED decision
   references a file, treat its creation as an explicit prerequisite
   milestone — not implicitly bundled into the first downstream phase.

2. **Adapter-pattern PRs converge in 2-3 Codex rounds with scope-creep
   follow-ons.** PR-X1 (extraction) and PR-X2 (adapter composition)
   both had 2-3 substantive Codex rounds before R3/R5 CLEAN. The
   pattern from each: R1 surfaces real bugs at the largest blast-radius
   surface (slot lifecycle, stranded retries); fix introduces a
   narrower bug at a smaller surface (SELECT projection missing the
   added column; UPSERT COALESCE missing the new column); R2 catches
   that. R3 typically CLEAN. The round-N convergence pattern from
   Phase 2c (PR #25, 6 rounds) holds; smaller PRs converge in fewer
   rounds. Switch to `codex review --base <prior-commit>` once
   cumulative diff exceeds ~1500 lines.

3. **Pre-write idempotency anchor BEFORE the downstream publish call.**
   The adapter's external_integration_runs row pre-writes
   status='in_progress' + the envelope's interaction_id BEFORE
   text_clean_service.process() is called. If the publish succeeds but
   the success UPSERT fails, the next cycle's idempotency check
   recovers the prior interaction_id and re-publishes under the same
   id — downstream consumers dedup. Without the pre-write, the retry
   would mint a new interaction_id and downstream would see two
   interactions for the same Granola note. The 'in_progress' status
   is not in IngestionOutcome (never a terminal outcome of
   process_note); it's an intermediate state managed by the SQL
   helper.

**Codex full trajectory cross-PR (this session):**

| PR | Round | Base | P1 | P2 | P3 | Real findings |
|---|---|---|---|---|---|---|
| X1 | R1 | main | 0 | 1 | 0 | Slot double-release on non-Lane1 raise |
| X1 | R2 | R1 | 0 | 0 | 0 | CLEAN (delta) |
| X1 | R3 | main | 0 | 1 | 1 | LOCKED-41 kwargs; empty-string override |
| X1 | R4 | R3 | 0 | 0 | 0 | CLEAN (delta) |
| X1 | R5 | main | 0 | 0 | 0 | CLEAN cumulative — convergence |
| X2 | R1 | main | 2 | 1 | 0 | Stranded failed; watermark race; pub→DB dup |
| X2 | R2 | R1 | 1 | 3 | 0 | SELECT/UPSERT eq_iid; retry budget; C defer |
| X2 | R3 | R2 | 0 | 0 | 0 | CLEAN (delta) — convergence |
| X2 | R4 | main | — | — | — | wrapper timeout @ 3814 lines (use delta past 1500) |

═══════════════════════════════════════════════════════════════════════
ENV / KNOWN ISSUES (carried forward)
═══════════════════════════════════════════════════════════════════════

**Pre-existing in-repo env gap (NOT introduced this session):**
- Local `.venv` is missing `cryptography>=44.0.0` (pinned in
  requirements.txt). Vault tests (tests/unit/vault/) skip-fail in local
  pytest until `pip install -r requirements.txt` runs. Production
  Railway has the dep. Phase 2d's adapter.py uses TYPE_CHECKING guard
  on the vault import so adapter tests (which mock vault credentials)
  don't require cryptography either.

**Pre-existing failing tests (UNRELATED to this session):**
- 16 tests in tests/integration/test_queue_lifecycle.py fail on main
  (verified by stashing PR-X1 + running the same test on tip of main).
  These are _SessionStub-related and predate this session.
- 1 deselected pre-existing test
  (test_upsert_summary_uses_unique_interaction_id_index) — old
  single-column ON CONFLICT migrated to composite during M2/M5.2.

**Orphan stash (preserved on stack from prior session):**
- `stash@{0}: WIP on railway-deployment ...` — 7 lines across .env.example
  and requirements.txt. Recoverable via `git stash apply 0` or
  cleanable via `git stash drop 0`.

═══════════════════════════════════════════════════════════════════════

Start with /context-restore. Then verify git states + production health
(Step 2). Then read the comprehensive handoff document at
docs/superpowers/specs/2026-05-24-phase-2e-session-prompt.md (Step 3).
Then the mandatory reads (Step 4). Then write the 2-paragraph
confirmation. Then begin Phase 2e implementation (Step 5).

This session ships the scheduler (Railway cron + DBOS workflow with
explicit SetWorkflowID). It is THE WAKE-UP: until Phase 2f adds /connect,
no credentials exist; the scheduler runs every 5 min and exits cleanly.
Phase 2f (admin endpoints) is the NEXT session after this.

No deploys without per-action user authorization. PR creation is fine;
git push to feature branches is fine; git push to main, force-push, and
gh pr merge require user confirmation each time.

Always verify the current branch via `git branch --show-current`
IMMEDIATELY before any git commit in shared checkout directories.
```
