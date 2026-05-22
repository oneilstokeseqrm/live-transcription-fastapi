# Next Session Opening Prompt (2026-05-23, Granola integration build)

Paste the block below as the opening message of the next Claude session.

Written 2026-05-22 end-of-session after Granola integration brainstorm + plan-eng-review + codex consult all completed. Plan locked with 22 new LOCKED decisions (LOCKED-23 through LOCKED-44). Build session executes Phase 0 → Phase 6 sequentially.

---

```
You're picking up the Granola.ai transcript ingestion adapter — Phase 2 of a
multi-phase data-quality initiative on an AI-native customer intelligence
platform. Three design partners + Peter (design partner #0) on Granola Business
plan. Each user generates a personal API key + creates an "EQ" folder in
Granola; EQ polls every 5 min and ingests new transcripts. ~6-7 days of focused
engineering work, likely 3-5 build sessions.

═══════════════════════════════════════════════════════════════════════
WHERE WE ARE
═══════════════════════════════════════════════════════════════════════

Phase 1-email-pipeline: ✅ INITIATIVE COMPLETE (2026-05-18).

Granola integration brainstorm + plan: ✅ COMPLETE (2026-05-22).
  - Brainstorm closed Q1-Q8 with 16 locked decisions
  - Plan written and folded through /plan-eng-review (interactive)
  - Outside-voice /codex consult surfaced 11 findings; 3 user-decided,
    8 folded into plan as must-fix architectural corrections
  - 22 new LOCKED decisions total (LOCKED-23 through LOCKED-44)
  - 11 Phase 2.1 follow-ups captured
  - Plan is at tasks/granola-integration-plan.md (~900 lines)

Granola integration BUILD: 🟢 READY TO EXECUTE.
  This session opens Phase 0 (pre-flight verification). NOT a re-brainstorm.
  NOT a plan revision. Execute the locked plan; if something looks wrong,
  surface to user — don't silently fix.

═══════════════════════════════════════════════════════════════════════
SESSION SCOPE — BUILD PHASE 0 (and beyond)
═══════════════════════════════════════════════════════════════════════

IN scope for this session:
  1. Read all mandatory reads (this is non-negotiable; see below)
  2. Execute Phase 0 (pre-flight verification, ~0.5 day)
     - scripts/verify_consumer_contracts.py against proposed envelope
     - Verify Granola `since` filter actually works empirically
     - Probe Neon schema state
     - Confirm no other agents in test tenant
     - Create feature branch `phase-2/granola-integration`
  3. If Phase 0 PASSES cleanly: proceed to Phase 1 (AWS infra) and
     possibly Phase 2a (Prisma migration in eq-frontend)
  4. If Phase 0 surfaces drift on envelope or Granola API: STOP and
     surface to user; do NOT silently fall back without confirming.

OUT of scope:
  - Re-litigating any LOCKED decision (23-44)
  - Writing new features beyond the plan
  - Deploying without user authorization
  - Modifying downstream Pydantic envelope contracts (LOCKED-38)

═══════════════════════════════════════════════════════════════════════
MANDATORY READS (do these in order; do NOT skip)
═══════════════════════════════════════════════════════════════════════

Per the memory entry `feedback_complete_all_handoff_reads_before_action.md`:
COMPLETE EVERY READ BEFORE PRE-FLIGHT OR CODE WORK. Don't start verifying
while still reading. Use parallel tool calls to read fast.

1. THE PLAN (load-bearing executable doc):
   /Users/peteroneil/EQ-CORE/live-transcription-fastapi/tasks/granola-integration-plan.md
   - Read top-to-bottom (~900 lines)
   - Internalize the LOCKED Decisions table (22 new locks; especially
     LOCKED-39 through LOCKED-44 which are post-review architectural fixes)
   - Note the pre-merge checklist; it gates ship

2. BRAINSTORM BACKGROUND (don't re-litigate; reference only):
   /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md
   - ~600 lines covering Q1-Q5 + Q6/Q7/Q8 decisions
   - Empirical Granola API findings (verified 2026-05-21)
   - Explicit "What NOT to do" and "Rejected alternatives" sections

3. REPO'S DBOS ARCHITECTURE DOC (MANDATORY for Phase 2e scheduler):
   /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md
   - §768: deprecation of @DBOS.scheduled (the WHY behind LOCKED-39)
   - §504: SetWorkflowID dedup pattern (the HOW for Phase 2e)
   - §512: workflows are pure orchestration; I/O in steps (discipline)

4. AUTO-MEMORY INDEX (auto-loads, but verify):
   ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/MEMORY.md
   - Confirm Active Work entry reads "BRAINSTORM + REVIEW COMPLETE"
   - If anything else, STOP and surface — memory state may have drifted

5. PROJECT MEMORY SNAPSHOT:
   ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/project_granola_integration.md
   - Architecture summary + critical landmines
   - 22 LOCKED decisions list

6. LOAD-BEARING FEEDBACK MEMORIES (read in this order):
   - feedback_envelope_contract_immutable.md (LOCKED-38 enforcement)
   - feedback_codex_pre_merge_gate.md (mandatory for every PR)
   - feedback_shared_infrastructure_collision.md (before any test-tenant write)
   - feedback_tenant_isolation.md (every query has tenant_id in WHERE)
   - feedback_contact_id_consistency.md (every contact carries UUIDv4)
   - feedback_branch_safety.md (feature branch protocol)
   - feedback_test_pattern_no_docker.md (AsyncMock unit tests + prod E2E)
   - feedback_destructive_ops_blast_radius.md (before any DELETE/TRUNCATE)
   - feedback_complete_phase_before_next.md (no parking-lot deferrals)

7. CROSS-CUTTING LESSONS (1820 lines — skim for relevant sections):
   /Users/peteroneil/EQ-CORE/live-transcription-fastapi/tasks/lessons.md
   - "Source field validation" — downstream SourceType enum (LOCKED-35 driver)
   - "FK chain for interaction_contact_links" — 3-level FK gotcha
   - "Multiple ingestion paths drop account_id" — every path must carry tenant + account
   - "Cross-service contract verification at design time" — verify_consumer_contracts.py discipline
   - "Codex review is a merge gate, not a follow-up" — Phase 5 gate
   - "Coordinated multi-repo schema migrations need explicit code-lifecycle sequencing" — Phase 2a M1↔M2 pattern
   - "Tenant-scoped DELETE is NOT session-scoped on shared test infrastructure" — Phase 4c hygiene
   - "Prisma @@unique materializes as INDEX, not CONSTRAINT" — Phase 2a ON CONFLICT pattern

8. SECONDARY REFERENCE MEMORIES (skim only):
   - reference_railway_project_ids.md (project + service + env IDs)
   - reference_railway_proxy_timeout.md (5-min HTTP cap — LOCKED-31 relevance)
   - reference_prisma_schema_ownership.md (eq-frontend owns schema)
   - reference_test_tenant.md (11111111-1111-4111-8111-111111111111)
   - reference_contacts_architecture.md (cross-service contacts doc)
   - reference_neo4j_shared_instance.md (downstream graph services)

9. AFTER ALL READS: write a 2-paragraph confirmation:
   - Para 1: What the build executes (Phase 0 + immediate next steps)
   - Para 2: Critical LOCKED decisions for Phase 0/1/2 specifically
   Then proceed with Phase 0 verification.

═══════════════════════════════════════════════════════════════════════
TOP 6 LOCKED DECISIONS TO INTERNALIZE (most likely to bite the build)
═══════════════════════════════════════════════════════════════════════

These came from the outside-voice Codex review and would cause real bugs if
the build session skipped re-reading the plan:

LOCKED-39: NOT @DBOS.scheduled — that decorator is DEPRECATED per this repo's
own DBOS plan. Use external Railway cron + DBOS queue with explicit
SetWorkflowID derived from (credential_id, cycle_window_minute). Workflows
are pure orchestration; all I/O lives in @DBOS.step functions. Read the
2026-05-15-async-orchestration-dbos.md plan §768 + §504 + §512 BEFORE
writing Phase 2e.

LOCKED-40: KMS EncryptionContext binds all FOUR fields: {tenant_id, user_id,
provider, credential_id}. Not 2 fields. Per-row binding closes a
tenant-internal row-swap security gap. IAM policy condition includes all 4
keys in kms:EncryptionContextKeys.

LOCKED-41: The Granola adapter calls services/text_clean_service.py
(extracted from routers/text.py core) DIRECTLY — NOT HTTP. tenant_id flows
as explicit function argument. Tenant isolation preserved by entity-sourced
identity pattern.

LOCKED-42: Postgres role split SIMPLIFIED for MVP. Single Postgres engine,
single role, schema separation + audited accessor for vault. Do NOT add
CREATE ROLE inside the Prisma migration (psql syntax incompatible). Second
role + engine moves to Phase 2.1 hardening.

LOCKED-43: AES-GCM rotate path mints FRESH DEK + FRESH NONCE on every write
(insert AND update-in-place). Nonce reuse silently breaks AES-GCM auth.
Vault module's rotate function must call KMS GenerateDataKey + new
os.urandom(12) every time.

LOCKED-44: external_integration_runs.granola_note_snapshot (JSONB) captures
defer-time state {title, summary_text, attendees, web_url, captured_at} so
Scenario C remains recoverable even if Granola removes the note before
approval. The defer path is NOT just a "we'll re-fetch later" assumption —
we snapshot at defer time.

═══════════════════════════════════════════════════════════════════════
USER POSTURE (load-bearing — do NOT violate)
═══════════════════════════════════════════════════════════════════════

Non-developer founder. Make confident technical decisions; surface only
product / strategic decisions or scope deviations. Strict OSS only.

User's rules:
  1. Complete Phase N before Phase N+1 planning.
     ✅ Phase 1 closed → Phase 2 brainstorm + plan locked → build now allowed
  2. Cutting-edge-startup approach. No shortcuts unless the shortcut IS the
     correct architecture (verified by investigation).
  3. AI agent doesn't push or merge without per-action authorization. PRs
     can be created locally but `git push` and `gh pr merge` require user
     confirmation each time.
  4. Plain-English explanations when user asks "why" / "what happened" —
     user is non-developer; technical accuracy paired with clear framing.
  5. Investigate thoroughly; use the right gstack skills: /investigate,
     /codex consult, /codex review, /codex challenge, /review (before
     merge), /ship (after PR approval).
  6. Don't go beyond locked scope. For build: don't add features the plan
     didn't enumerate. If you find a real issue mid-build, surface to user
     and treat as plan amendment, not silent expansion.
  7. Tenant isolation is non-negotiable — every query MUST include tenant_id
     in WHERE. The Granola adapter follows the "entity-sourced identity"
     pattern (tenant_id from credential row), which is the same model used
     by existing background workers.

═══════════════════════════════════════════════════════════════════════
STOP CONDITIONS (hard — surface to user immediately)
═══════════════════════════════════════════════════════════════════════

  - MEMORY.md Active Work doesn't read "BRAINSTORM + REVIEW COMPLETE"
  - tasks/granola-integration-plan.md doesn't exist or is materially
    different from the locked version
  - Phase 0 verify_consumer_contracts.py surfaces drift on source="generic"
    AND falling back to source="api" also drifts — both options blocked
  - Phase 0 Granola `since` filter doesn't work AND client-side filter
    isn't acceptable to user
  - DBOS architecture doc reveals a third deprecation we didn't catch
  - Production /api/health returns non-200 on any service (Phase 1
    regression)
  - Test tenant has leftover artifacts from a prior session that haven't
    been cleaned (see shared-infrastructure-collision protocol)
  - User explicitly asks you to deviate from a LOCKED decision — confirm
    in writing before proceeding

═══════════════════════════════════════════════════════════════════════
PHASE-BY-PHASE EXECUTION ORDER (from the locked plan)
═══════════════════════════════════════════════════════════════════════

Phase 0 (~0.5 day):  Pre-flight verification — envelope + Granola API + schema probe + branch
Phase 1 (~0.5 day):  AWS infrastructure — KMS CMK + IAM user + Railway env vars
Phase 2a (~0.5 day): Prisma migration in eq-frontend (cross-repo coordination!)
Phase 2b (~0.5 day): Vault module (KMS envelope encryption + audited accessor)
Phase 2c (~0.5 day): Granola API client (httpx + structured errors + Pydantic models)
Phase 2d (~1.5 days): Granola adapter + Path 2 scenario logic
Phase 2e (~0.5 day): Scheduler — Railway cron + DBOS queue + SetWorkflowID
Phase 2f (~0.5 day): Admin/health endpoints (/validate, /connect, /rotate, /folder, /status, /disconnect)
Phase 2g (~0.5 day): Transactional email on credential breakage (Resend)
Phase 3 (~2 days):  Frontend — Granola Connect page + EQ-native Pending Approvals
Phase 4 (~1 day):   Testing — unit + integration + production E2E
Phase 5 (varies):   Pre-merge Codex review gate (4-round soft cap)
Phase 6 (~0.5 day): Deploy + verify

Each phase has explicit exit criteria. Don't move to N+1 until N exits clean.

═══════════════════════════════════════════════════════════════════════
COORDINATION PROTOCOL (cross-repo, cross-agent)
═══════════════════════════════════════════════════════════════════════

Cross-repo deploys (Phase 2a Prisma migration coordination):
  - eq-frontend Prisma PR merges FIRST → Vercel deploys → Neon schema updated
  - THEN live-transcription-fastapi PR merges → Railway deploys
  - Per "Coordinated multi-repo schema migrations need explicit code-lifecycle
    sequencing" lesson — additions are safe, removals need ordering

Other agents in test tenant:
  - Before any test-tenant DB write, run:
    ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
  - Files modified in the last hour = hazard signal
  - Coordinate with user before proceeding (see feedback_shared_infrastructure_collision.md)

Codex pre-merge gate:
  - Mandatory for every PR per feedback_codex_pre_merge_gate.md
  - 4-round soft cap (extend if real P1s keep surfacing per round-N pattern)
  - Run scripts/verify_consumer_contracts.py + scripts/verify_schema.py
    as part of the gate

═══════════════════════════════════════════════════════════════════════
KEY REFERENCE PATHS
═══════════════════════════════════════════════════════════════════════

PLAN (executable):
  /Users/peteroneil/EQ-CORE/live-transcription-fastapi/tasks/granola-integration-plan.md

BRAINSTORM BACKGROUND (don't re-litigate):
  /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md

WAYFINDING (this initiative's NEXT-SESSION-START-HERE):
  /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/specs/NEXT-SESSION-START-HERE.md

DBOS ARCHITECTURE (mandatory for Phase 2e):
  /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md

PHASE 1 PRECEDENT (architectural reference):
  /Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md

LESSONS (cross-cutting):
  /Users/peteroneil/EQ-CORE/live-transcription-fastapi/tasks/lessons.md

AUTO-MEMORY:
  ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/

═══════════════════════════════════════════════════════════════════════

Start with the mandatory reads. After all reads, write the 2-paragraph
confirmation. Then begin Phase 0 verification.

The session is multi-hour focused work; expect to take 1-2 build sessions
to ship Phase 0 + Phase 1 + Phase 2a-2c. Phase 2d (adapter) + Phase 2e
(scheduler) are the heart of the work and may need their own session.
Phase 3 (frontend) ships in a follow-on session. Phase 4-6 (testing +
deploy) close the initiative.

No deploys without explicit user confirmation per action. PR creation is
fine; merging and pushing require authorization.
```

---

## Notes for the user pasting this

This prompt is the Granola build kickoff. Key differences vs prior session prompts:

- **Status is `BRAINSTORM + REVIEW COMPLETE`.** Brainstorm phase is closed; the plan has been through both /plan-eng-review and /codex consult. All architectural decisions are locked.
- **22 LOCKED decisions** to internalize (LOCKED-23 through LOCKED-44). The top 6 are highlighted because they came from the Codex review and would bite the build if the next agent skips reading the plan.
- **Explicit DBOS architecture doc requirement.** Phase 2e scheduler MUST read `docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md` — `@DBOS.scheduled` is deprecated per the repo's own plan.
- **Cross-repo coordination protocol** included for the eq-frontend Prisma migration.
- **Expected runtime: 6-7 days across 3-5 build sessions.** Phase 0 + Phase 1 + Phase 2a-2c fits one focused session.
