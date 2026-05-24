# Next Session Opening Prompt (Phase 2d — Granola adapter + Path 2 logic)

**Written:** 2026-05-24 end-of-session, after Phase 2c (Granola HTTP API client) was built, hardened across 6 Codex pre-merge rounds, merged, and deployed to production as inert code.

**Paste the block below as the opening message of the next Claude session.**

This is the canonical paste-ready handoff. It mirrors the structure of the Phase 2c session opener — the new agent reads everything mandatory, writes a 2-paragraph confirmation, then builds Phase 2d (the adapter that composes vault + API client + text_clean_service into the per-credential ingestion cycle).

---

```
You're picking up the Granola.ai transcript ingestion adapter — the prior
session shipped Phase 2c (the HTTP API client) end-to-end. Module is live
in production as inert code. PR #25 merged `030523c`; Railway deployment
`9e393c5a` SUCCESS; /health 200; 6 Codex pre-merge rounds (R6 CLEAN); 42
new unit tests; 245 total passing; 0 regressions.

THIS SESSION'S JOB: build Phase 2d — the Granola adapter. Three new files
(services/granola_ingestion/{adapter,path2,outcomes}.py) that compose the
vault module (Phase 2b) + the API client (Phase 2c, JUST SHIPPED) +
text_clean_service (LOCKED-41 direct Python call, NOT HTTP) +
existing account_lookup / domain_classification / pending_account_mappings
infrastructure into the per-credential ingestion cycle. Path 2 attendee
classification + Scenario A/B/C/D branching. Envelope construction per
LOCKED-35/36. ~1.5 days estimated.

CONTINUITY IS CRITICAL. The prior session's handoff artifacts are
comprehensive and load-bearing. Read everything. Trust but verify each
artifact loads as expected. If anything is missing or unexpected, STOP and
surface to the user — do not improvise.

═══════════════════════════════════════════════════════════════════════
STEP 1 — RUN /context-restore FIRST
═══════════════════════════════════════════════════════════════════════

Before anything else, run /context-restore. It will load the most recent
checkpoint:

  ~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/
  <timestamp>-phase-2c-shipped-phase-2d-next.md

If /context-restore returns NO_CHECKPOINTS or a different title, STOP and
surface to the user immediately — the handoff is broken.

═══════════════════════════════════════════════════════════════════════
STEP 2 — VERIFY CURRENT GIT + PRODUCTION STATE
═══════════════════════════════════════════════════════════════════════

Verify both checkouts:

  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi
  git branch --show-current     # expect: main
  git log --oneline -5          # expect tip: 030523c feat(granola): Phase 2c
                                # — Granola HTTP API client (#25)
  git status --short            # expect: clean

  git -C /Users/peteroneil/eq-frontend log origin/main --oneline -3
                                # expect tip: 7905222 feat(prisma): Phase 2a
                                # — Granola vault schema (#418)
                                # — DO NOT switch the main checkout's
                                # branch (another agent may be active)

Verify production health (stop conditions):

  curl -s https://live-transcription-fastapi-production.up.railway.app/health
  # expect: {"status":"ok"}

Verify the Phase 2c module loaded inertly in production by importing it
from a local Python REPL or via:

  .venv/bin/python -c "from services.granola_ingestion import GranolaAPIClient, GranolaError, GranolaErrorCode; print('OK')"
  # expect: OK

Verify production Neon (via Neon MCP, project super-glitter-11265514,
branch br-holy-block-ads5069w, database neondb):
  - schema 'vault' exists
  - 3 tables present:
    * vault.user_credentials (15 cols)
    * vault.credential_access_log (11 cols)
    * public.external_integration_runs (17 cols, INCLUDING the granola_note_snapshot JSONB column per LOCKED-44)

If any of those is wrong, STOP and surface.

═══════════════════════════════════════════════════════════════════════
STEP 3 — READ THE COMPREHENSIVE HANDOFF DOCUMENT
═══════════════════════════════════════════════════════════════════════

The PRIMARY HANDOFF DOCUMENT is this file itself:

  docs/superpowers/specs/2026-05-24-phase-2d-session-prompt.md

It contains the full sequence below. After STEP 3, proceed to STEP 4 (the
mandatory reads).

═══════════════════════════════════════════════════════════════════════
STEP 4 — MANDATORY READS (in this order; do NOT skip)
═══════════════════════════════════════════════════════════════════════

Per the memory entry feedback_complete_all_handoff_reads_before_action.md:
complete EVERY read BEFORE starting Phase 2d implementation. Don't write
code mid-read. Use parallel tool calls to read fast.

1. WAYFINDING:
   docs/superpowers/specs/NEXT-SESSION-START-HERE.md
   — High-level status dashboard: PHASE_2C_SHIPPED state, Phase 2d scope
     summary, full lookup table of what's live in production.

2. THE EXECUTABLE PLAN (load-bearing; ~1080 lines):
   tasks/granola-integration-plan.md
   — Phase 2d is §Phase 2d. LOCKED-23 through LOCKED-44.
   — Phase 2a (§Phase 2a), Phase 2b (§Phase 2b), and Phase 2c (§Phase 2c)
     are SHIPPED — don't re-execute them. Skim only to refresh context.
   — Pay extra attention to LOCKED-35 (envelope shape: source="generic",
     interaction_type="meeting", content.format="plain"), LOCKED-36 (six
     granola_* keys in extras), LOCKED-38 (NEVER modify downstream
     Pydantic envelope contracts), LOCKED-41 (text_clean_service direct
     Python call, NOT HTTP, with tenant_id as explicit argument), and
     LOCKED-44 (granola_note_snapshot JSONB captures defer-time state).

3. PHASE 2c CODE (read THE WHOLE THING — Phase 2d will compose it):
   services/granola_ingestion/api_client.py — public methods: list_folders(),
     list_notes(folder_id, created_after, limit=100), get_note_detail(note_id).
     Note the 8 error codes in errors.py — adapter must catch and classify
     them (especially GRANOLA_NOTE_NOT_FOUND as per-note skip vs
     GRANOLA_FOLDER_NOT_FOUND as credential-level breakage).
   services/granola_ingestion/errors.py — full error catalog
   services/granola_ingestion/models.py — Pydantic shapes
   services/granola_ingestion/__init__.py — public exports

4. PHASE 2b CODE (Phase 2d's vault integration point):
   services/vault/__init__.py — public exports
   services/vault/user_credentials.py — accessor signatures + ALLOWLIST
     (services.granola_ingestion.adapter is already in it)
   services/vault/README.md — invariants + audit log

5. EXISTING INFRASTRUCTURE PHASE 2d COMPOSES (skim each):
   - services/text_clean_service.py — LOCKED-41 direct-call target. Read
     the .process() signature carefully — tenant_id is an explicit kwarg.
   - services/account_lookup.py — lookup_account_by_domain
   - services/domain_classification.py — classify_domain + business/personal/internal
   - services/internal_domains.py — get_tenant_internal_domains
   - services/pending_account_mappings.py — upsert_queue_entry + insert_signal
   - services/transcript_enrichment.py — existing transcript-source signal
     queueing for unknown secondary attendees (Phase 2d follows this pattern)

6. THE BRAINSTORM (background only; don't re-litigate):
   docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md
   — Especially the Path 2 architecture section + "The raw_interactions Trap"
     (LOCKED-25 mitigation: interaction_type="meeting", NOT "transcript")

7. THE CONSUMER-CONTRACT VERIFICATION SCRIPT (load-bearing for pre-merge):
   scripts/verify_consumer_contracts.py — Phase 2d's envelope MUST PASS
   this check BEFORE merge per LOCKED-37. Read the script to understand
   what shape it asserts.

8. PHASE 2c PR (merged commit 030523c):
   gh pr view 25
   — Read the description for the final-state Phase 2c API surface and
     the 6-round Codex hardening summary.

9. AUTO-MEMORY (auto-loads, but verify):
   ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/
   memory/MEMORY.md
   — Active Work entry should read PHASE_2C_SHIPPED + PHASE_2D_NEXT 2026-05-24.

10. PROJECT MEMORY SNAPSHOT:
    ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/
    memory/project_granola_integration.md
    — Final-state Phase 2c design + 6-round Codex trajectory + Phase 2d scope.

11. LOAD-BEARING FEEDBACK MEMORIES (read all):
    - feedback_complete_all_handoff_reads_before_action.md
    - feedback_envelope_contract_immutable.md (LOCKED-38)
    - feedback_codex_pre_merge_gate.md (Phase 2c ran 6 rounds; Phase 2d's
      envelope construction may run similar)
    - feedback_shared_infrastructure_collision.md
    - feedback_tenant_isolation.md
    - feedback_branch_safety.md
    - feedback_test_pattern_no_docker.md

12. NEW LESSONS FROM PRIOR SESSION (at bottom of tasks/lessons.md):
    - "Codex round-N convergence: scope-creep follow-ons" (NEW this session)
    — Plus all earlier lessons (carry forward).

13. AFTER ALL READS, write a 2-paragraph confirmation:
    - Para 1: What this session executes — Phase 2d
      (services/granola_ingestion/{adapter,path2,outcomes}.py composing
      vault + GranolaAPIClient + text_clean_service into a per-credential
      ingestion cycle with Path 2 attendee classification + Scenario A/B/C/D
      branching + envelope construction per LOCKED-35/36 + Scenario C
      defer-and-recover with granola_note_snapshot per LOCKED-44).
      Mention you'll use AsyncMock unit tests (per
      feedback_test_pattern_no_docker), NOT Testcontainers.
    - Para 2: Critical disciplines: (a) work on feature branch
      phase-2d/granola-adapter off live-transcription-fastapi main;
      (b) verify branch via `git branch --show-current` IMMEDIATELY
      before any commit; (c) Codex pre-merge review is MANDATORY (4-round
      soft cap; extendable per the round-N scope-creep follow-on lesson);
      (d) per-action user authorization for push to main / merge /
      destructive ops; (e) NEVER modify downstream Pydantic envelope
      contracts (LOCKED-38) — this is THE load-bearing constraint of
      Phase 2d; (f) text_clean_service is a direct Python call with
      tenant_id as explicit kwarg, NOT an HTTP call (LOCKED-41);
      (g) Phase 2c's GRANOLA_NOTE_NOT_FOUND must be treated as per-note
      skip (failed), NOT credential-level breakage (status='error');
      (h) Phase 2d's envelope MUST pass scripts/verify_consumer_contracts.py
      BEFORE merge (LOCKED-37).

═══════════════════════════════════════════════════════════════════════
STEP 5 — EXECUTE PHASE 2d
═══════════════════════════════════════════════════════════════════════

Per tasks/granola-integration-plan.md §Phase 2d:

**Branch:** create feature branch `phase-2d/granola-adapter` off main

**New files:**
- services/granola_ingestion/outcomes.py — IngestionOutcome enum (5 values)
- services/granola_ingestion/path2.py — Path 2 attendee classification +
  Scenario A/B/C/D branching helpers
- services/granola_ingestion/adapter.py — the per-credential cycle
- tests/unit/granola_ingestion/test_adapter.py — AsyncMock unit tests
- tests/unit/granola_ingestion/test_path2.py — Path 2 unit tests

**IngestionOutcome enum (Q7 tri-state, per plan):**
  - SUCCESS
  - DEFERRED_PENDING_ACCOUNT (Scenario C: no known business accounts)
  - SKIPPED_NO_BUSINESS_ATTENDEES (Scenario D)
  - FAILED (transient — Phase 2e scheduler will retry)
  - FAILED_PERMANENT (5+ retries exhausted)

**Per-credential cycle pseudocode (high-level):**
  async def run_one_cycle(credential_id: UUID) -> CycleResult:
      credential = await vault.get_granola_credential_for_user(
          tenant_id=...,
          user_id=...,
          caller_module="services.granola_ingestion.adapter",
          pool=pool,
      )
      if credential.status != "active":
          return CycleResult(skipped=True, reason="credential_not_active")

      async with GranolaAPIClient(api_key=credential.api_key) as client:
          # Path 2 fetch
          try:
              notes = await client.list_notes(
                  folder_id=credential.config["folder_id"],
                  created_after=credential.last_polled_at,
              )
          except GranolaError as e:
              if e.code is GranolaErrorCode.GRANOLA_AUTH_FAILED:
                  await mark_credential_revoked(credential_id)
                  return CycleResult(error="auth_failed")
              elif e.code is GranolaErrorCode.GRANOLA_FOLDER_NOT_FOUND:
                  await mark_credential_error(credential_id, e.code.value)
                  return CycleResult(error="folder_not_found")
              # other GranolaError codes -> transient retry path
              await increment_consecutive_failures(credential_id, e.code.value)
              return CycleResult(error=e.code.value)

          # Per-note processing
          for note_summary in notes:
              await process_note(credential, note_summary, client)

          # Re-poll deferred-pending-account rows from prior cycles
          await reprocess_deferred_notes(credential, client)

          await mark_credential_success(credential_id)

  async def process_note(credential, note_summary, client) -> IngestionOutcome:
      # Idempotency check
      existing = await get_integration_run(...)
      if existing and existing.status == "success":
          return IngestionOutcome.SUCCESS

      try:
          detail = await client.get_note_detail(note_summary.id)
      except GranolaError as e:
          if e.code is GranolaErrorCode.GRANOLA_NOTE_NOT_FOUND:
              # PER-NOTE SKIP — NOT credential breakage!
              return await record_skipped_note(credential, note_summary,
                                                "note_deleted_before_detail_fetch")
          elif e.code is GranolaErrorCode.GRANOLA_PARSE_ERROR:
              return await record_failed_note(credential, note_summary,
                                              e.code.value, str(e))
          # other transient: increment retry_count, will retry next cycle
          ...

      # Path 2 classification
      business_domains = classify_attendees(detail.attendees, credential.tenant_id)
      if not business_domains:
          return await record_skipped_note(credential, note_summary,
                                            "no_business_attendees")

      known_accounts = await lookup_known_accounts(
          credential.tenant_id, business_domains
      )
      if known_accounts:
          # Scenario A/B
          anchor_account_id = pick_anchor(known_accounts)
          envelope = build_envelope(credential, detail, anchor_account_id)
          # LOCKED-41: direct Python call, NOT HTTP. tenant_id explicit.
          await text_clean_service.process(
              tenant_id=credential.tenant_id,
              user_id=credential.user_id,
              account_id=anchor_account_id,
              envelope=envelope,
          )
          return await record_success(credential, detail, anchor_account_id)
      else:
          # Scenario C: no known accounts. Defer + capture snapshot.
          return await defer_pending_account(credential, detail, business_domains)

**Envelope build per LOCKED-35/36:**
  envelope = EnvelopeV1(
      tenant_id=credential.tenant_id,
      user_id=str(credential.user_id),
      interaction_type="meeting",         # LOCKED-25 (FK landmine mitigation)
      source="generic",                   # LOCKED-35
      account_id=str(anchor_account_id),
      timestamp=detail.created_at,
      content=ContentModel(
          text=build_transcript_with_frontmatter(detail),
          format="plain",                 # LOCKED-35
      ),
      extras={                            # LOCKED-36 six keys
          "granola_note_id": detail.id,
          "granola_web_url": detail.web_url,
          "granola_folder_name": credential.config.get("folder_name"),
          "granola_summary_text": detail.summary_text,
          "granola_calendar_event_id": detail.calendar_event.id if detail.calendar_event else None,
          "granola_attendees_raw": [a.model_dump() for a in detail.attendees],
      },
  )

**Unit tests (AsyncMock-based, per feedback_test_pattern_no_docker):**
  - Scenario A: 1 known account → text_clean_service.process called with
    tenant_id=credential.tenant_id (assert tenant isolation flow)
  - Scenario B: mix of known + unknown → ingest with known anchor
  - Scenario C: no known accounts → deferred row with granola_note_snapshot,
    queue entry created
  - Scenario D: no business attendees → skipped
  - Scenario C → A re-poll: complete queue approval → next cycle picks up
  - Scenario C recoverability: deferred note deleted on Granola side
    (get_note_detail raises GRANOLA_NOTE_NOT_FOUND); re-poll succeeds
    from snapshot (LOCKED-44)
  - Auth failure: credential.status → 'revoked'
  - Folder not found: credential.status → 'error'
  - Note not found mid-cycle: per-note skip, credential stays 'active'
  - Cross-tenant guard: envelope from tenant_A → text_clean called with
    tenant_id=tenant_A

**Pre-merge verification:**
  python scripts/verify_consumer_contracts.py \
    --source generic \
    --interaction-type meeting \
    --extras-keys "granola_note_id,granola_web_url,granola_folder_name,granola_summary_text,granola_calendar_event_id,granola_attendees_raw"
  # MUST return 0 drift. If drift on source="generic", fall back to
  # source="api" per the plan's Phase 0 documented fallback, update
  # LOCKED-35 in this plan, and document the choice in the PR.

**Codex pre-merge review:**
  1. Open PR to live-transcription-fastapi main.
  2. Run `/codex review` (4-round soft cap; extend if real bugs surface
     each round per Phase 2b/2c precedents).
  3. Fold all P1 findings; P2 judgment call.
  4. Surface to user for merge authorization after gate passes (0 P1).

**Merge + deploy:**
  1. User authorizes merge.
  2. Squash-merge to main.
  3. Railway deploys.
  4. /health 200 verified.
  5. Module loads as still-inert code (Phase 2e's scheduler is the first
     thing that will actually invoke the adapter on a schedule).

═══════════════════════════════════════════════════════════════════════
STEP 6 — PHASE 2e (NOT IN THIS SESSION)
═══════════════════════════════════════════════════════════════════════

Phase 2e (~0.5 day) wires the adapter to a 5-min cadence:
- services/granola_ingestion/scheduler.py — DBOS workflow + steps
- routers/granola_cron.py — Railway-cron HTTP endpoint

DO NOT START PHASE 2e THIS SESSION. Phase 2d ships + reviewed + merged,
then Phase 2e is its own session.

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

2. **Codex pre-merge gate is MANDATORY.** 4-round soft cap. Phase 2b
   ran 7 rounds; Phase 2c ran 6 rounds. Both had real findings every
   round. Extend if R4+ surfaces more real bugs (the scope-creep
   follow-on pattern). Switch to `--base <prior-commit>` if cumulative
   diff exceeds ~1500 lines and the wrapper times out.

3. **NEVER modify downstream Pydantic envelope contracts** (LOCKED-38).
   Phase 2d IS the envelope-construction work. This is THE constraint.
   If verify_consumer_contracts.py fails, fall back to source="api" (the
   plan's documented fallback) — do NOT modify the downstream Pydantic
   model.

4. **Tenant isolation is non-negotiable.** Every DB query MUST include
   tenant_id. text_clean_service.process() MUST be called with
   tenant_id=credential.tenant_id as an explicit kwarg (LOCKED-41).
   The cross-tenant-guard unit test is load-bearing.

5. **No Docker in tests by default.** AsyncMock-style unit tests; the
   real-Neon + real-Granola E2E happens in Phase 4 with Peter as
   design partner #0.

6. **`GRANOLA_NOTE_NOT_FOUND` must be a per-note skip, NOT credential
   breakage.** Phase 2c added this code specifically so a deleted-note
   race doesn't take the whole credential offline. The adapter's
   exception classifier is load-bearing — get it right.

7. **The vault module's ALLOWLIST already includes
   `services.granola_ingestion.adapter`.** No vault changes needed.
   Phase 2d's adapter is the first code to import services.vault for
   real (Phase 2b shipped vault as inert; Phase 2c is pure HTTP and
   doesn't touch vault).

8. **LOCKED-44 granola_note_snapshot is REQUIRED for Scenario C.** The
   snapshot must be populated at defer time. Without it, a Granola
   note deleted before approval becomes unrecoverable. Unit test must
   prove the snapshot survives a get_note_detail-404 re-poll.

═══════════════════════════════════════════════════════════════════════
STOP CONDITIONS (HARD — SURFACE TO USER IMMEDIATELY)
═══════════════════════════════════════════════════════════════════════

  - /context-restore returns NO_CHECKPOINTS or wrong checkpoint title
  - MEMORY.md Active Work doesn't read "PHASE_2C_SHIPPED" / "PHASE_2D_NEXT"
  - live-transcription-fastapi main is NOT at 030523c (or descendant)
  - eq-frontend main is NOT at 7905222 (or descendant)
  - Production /health returns non-200
  - Vault schema or 3 tables MISSING from production Neon
  - AWS infrastructure missing
  - Another agent actively working in live-transcription-fastapi within
    the last hour
  - User asks you to deviate from a LOCKED decision (LOCKED-23..44)
  - Phase 2d starts modifying any downstream Pydantic envelope contract
    → STOP (LOCKED-38)
  - Phase 2d starts calling text_clean over HTTP instead of via direct
    Python import → STOP (LOCKED-41)
  - verify_consumer_contracts.py returns drift on the locked envelope
    shape AND you can't fall back cleanly to source="api" → SURFACE

═══════════════════════════════════════════════════════════════════════
KEY STATE (verified 2026-05-24 end-of-session)
═══════════════════════════════════════════════════════════════════════

live-transcription-fastapi main:
  030523c feat(granola): Phase 2c — Granola HTTP API client (#25)
  5859b9b docs(handoff): Phase 2 foundation verified end-to-end, Phase 2c next
  ebb0c84 docs(handoff): forensic bug-evidence file
  801f358 docs(handoff): Phase 2b SHIPPED + eq-frontend BLOCKED
  7f98920 feat(vault): Phase 2b — KMS-backed credential vault Python
          module (services/vault/) (#24)

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
  Latest deploy 9e393c5a-1fcf-4b10-9d11-e62236f53b87 SUCCESS (Phase 2c merge)
  /health 200 at https://live-transcription-fastapi-production.up.railway.app/health
  4 EQ_VAULT_* env vars set + working

Vercel (eq-frontend production):
  Project    prj_0wDppCftk1VrSAsYswI5pnNRHdN8
  Team       team_Hnnnu6r1trggeAXYWHXpKfMt
  Latest production deploy 2he8eDSfSLdapZ1eRXa6mSpjJkdq READY
  Canonical URL eq-frontend-two.vercel.app

Neon (production Postgres):
  Project    super-glitter-11265514 (eq-dev)
  Branch     br-holy-block-ads5069w (production)
  Database   neondb
  Vault schema + 3 tables LIVE

Linear:
  EQ-11 — schema drift family; unchanged this session.

═══════════════════════════════════════════════════════════════════════

Start with /context-restore. Then verify git states + production health
(Step 2). Then read the comprehensive handoff document at
docs/superpowers/specs/2026-05-24-phase-2d-session-prompt.md (Step 3).
Then the mandatory reads (Step 4). Then write the 2-paragraph
confirmation. Then begin Phase 2d implementation (Step 5).

This session is fundamentally a composition task: bring vault (Phase 2b) +
GranolaAPIClient (Phase 2c, just shipped) + text_clean_service together
into the per-credential ingestion cycle with Path 2 branching. Envelope
construction per LOCKED-35/36. The 8 Phase 2c error codes drive the
adapter's exception classifier. Phase 2e (scheduler) is the NEXT session.

No deploys without per-action user authorization. PR creation is fine;
git push to feature branches is fine; git push to main, force-push, and
gh pr merge require user confirmation each time.

Always verify the current branch via `git branch --show-current`
IMMEDIATELY before any git commit in shared checkout directories.
```
