# Next Session Opening Prompt (2026-05-17 evening, post-plan-lock)

Paste the block below as the opening message of the next Claude session. Written 2026-05-17 evening after Plan v4 was locked for the cold-inbound unknown-sender fix (4 Codex rounds; 11 substantive findings resolved; round-4 false positives recognized; 4-round soft cap honored).

**Important:** the next session is **EXECUTION** — implementing M1-M5. NOT plan revision. The plan is locked. Read it, implement M1, iterate to M5 (over one or two sessions per the recommendation below).

---

```
You're working in /Users/peteroneil/EQ-CORE/live-transcription-fastapi.

This is a continuation session for the Contact Quality and Account-
Anchoring Initiative — a multi-phase data-quality project on an
AI-native customer intelligence platform.

Phase 1 (account-anchor contract end-to-end) shipped 2026-05-14.
Phase 1.5 (async account-provisioning workflow on DBOS substrate +
verified-contract tooling) shipped across 2026-05-15 and 2026-05-17:
M0+M1+M2 (Railway prep + DBOS install + Prisma UNIQUE INDEX), M1
hotfix, M3+M4 (workflow + /approve cutover, PR #17 at ae45737), and
M5 (verified-contract tooling, PR #18 at 95f9084).

The Phase-1-email-pipeline plan was LOCKED 2026-05-17 evening
(this session's predecessor). Plan v4 at 1207 lines lives at
/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/
2026-05-17-pending-interactions-cold-inbound-fix.md (UNCOMMITTED).
4 Codex review rounds resolved 11 substantive findings. 18 LOCKED
decisions total. 2 new LOCKED decisions added (15: lean+typed
columns; 16: Path B via EventBridge EmailPromoted event).

Your job THIS session is to EXECUTE the cold-inbound unknown-sender
fix per the locked plan — finishing committed Phase 1 work (Task
1.24 acceptance criteria). Implementation, NOT plan revision.

CRITICAL: this session is EXECUTION. The plan is locked. If you
find issues with the plan during implementation, surface them via
AskUserQuestion rather than silently revising the plan doc.
Compressing plan revision + implementation into the same session
re-introduces the trap the plan-writing session was structured to
avoid.

Before doing anything else, follow these steps IN ORDER:

1. Run `/context-restore`. You should find a checkpoint titled
   "phase-1-email-pipeline-plan-v4-locked-comprehensive-handoff"
   dated 2026-05-17 (the 14:45 save). Load it. There may also be
   an earlier checkpoint from the same session at 14:35 —
   the 14:45 one is the comprehensive handoff. If /context-restore
   returns NO_CHECKPOINTS, STOP and surface — that's a sync gap.

2. Read MEMORY.md (auto-loads). Confirm project status reads
   PHASE_1_EMAIL_PIPELINE_PLAN_LOCKED_EXECUTION_NEXT. If anything
   else, STOP and surface.

3. READ THE WAYFINDING DOC:
   /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/
     superpowers/specs/NEXT-SESSION-START-HERE.md

   Sections "SESSION SCOPE FOR THE NEXT SESSION", "Execution
   sequence — M1 → M5", "LOCKED decisions" (18 total),
   "Acknowledged V1 limitations", "Stop conditions", and "Open
   questions deferred to execution" are load-bearing.

4. READ THE PLAN DOC (mandatory, ~30 min):
   /Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/
     2026-05-17-pending-interactions-cold-inbound-fix.md

   Especially:
   - §0 Revision history (v1 → v2 → v3 → v4 changes)
   - §1.2 Recharacterization (this is finishing Phase 1, not new
     scope; original Phase 1 PR #6 in eq-email-pipeline shipped
     logic + a test for partial-unknown case but not all-unknown)
   - §2 Design at a glance
   - §3.1-3.5 Schema design (pending_interactions table; emails
     new columns; interaction_summaries multi-variant UNIQUE)
   - §4.1-4.6 Orchestrator changes (decision branch; pending path;
     interaction_id pre-allocation; thread handling deferred to
     promote; light vs full tier preserved; signal flush hoist)
   - §5.1-5.5 Workflow promote-step (the 7-step linking sequence;
     Step 4 4a-4e; Step 5 cross-queue OR clause; new
     emit_email_promoted_events at workflow END)
   - §6.1-6.4 EmailPromoted handler (two-layer idempotency guard;
     atomic CAS; 5-min soft TTL; partial-retry corruption
     documented as bounded V1 limitation)
   - §7 Cross-repo migration ordering (M1 → M2 → M3 → M4 → M5)
   - §8 Edge cases (10 documented; especially §8.6 cross-queue
     cold-inbound + §8.9 duplicate webhook + §8.10 personal anchor)
   - §10 Test plan (unit, integration, E2E, rollback drill)
   - §11 Acceptance invariants (the ship-when-true checklist)
   - §12 Implementation milestones (the M1-M5 task breakdown)
   - §13 18 LOCKED decisions (do NOT re-litigate)
   - §14 Open questions deferred to execution
   - §16 What this plan does NOT do

5. Verify pre-flight state:
   - `gh pr view 18 --json state,mergedAt` → should be MERGED
     (PR #18 was the M5 ship)
   - `git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi
     status` → should show TWO uncommitted handoff artifacts:
     (a) docs/superpowers/specs/NEXT-SESSION-START-HERE.md (M)
     (b) docs/superpowers/specs/2026-05-17-evening-execution-
         next-session-prompt.md (??)
     Decide whether to commit these at session start or leave them
     for review.
   - `git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi
     log --oneline -5` → top should be `001ed89 docs(handoff):
     strengthen next-session handoff per user feedback`, then
     `5cdc838`, `fd38880`, `95f9084 feat(phase-1.5): M5 verified-
     contract tooling (#18)`, `ae45737 feat(phase-1.5): M3
     workflow + M4 /approve cutover (#17)`.
   - `git -C /Users/peteroneil/eq-email-pipeline status` → should
     show TWO uncommitted items: (a) `docs/superpowers/` directory
     (the new plan doc — recommend committing as part of M1 OR as
     a standalone "design plan" commit at session start), (b)
     `uv.lock` (pre-existing, not session-related).
   - `git -C /Users/peteroneil/eq-email-pipeline log --oneline -3`
     → top should be `084567a fix(persistence): include account_id
     in raw_interactions INSERT (Bug #5) (#8)`.
   - `curl -sS -o /dev/null -w "%{http_code}\n"
     https://live-transcription-fastapi-production.up.railway.app/
     health` → should return 200.
   - SHARED-TENANT-COLLISION CHECK (LOCKED-17):
     `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl |
     head -10`. Files modified in last hour = concurrent agent
     hazard. Pause + ask user.

6. After reading, briefly confirm in one paragraph: where the prior
   session left off + your M1 implementation approach for this
   session + that you understand this is EXECUTION (not plan
   revision).

7. EXECUTE — start with M1. Recommended overall sequence for this
   session: M1 + M2 + M3 (all independently safe deploys). Split
   M4 + M5 into a separate session because M4 flips the switch and
   warrants its own pre-merge ritual + production canary.

   First sub-decision: commit the plan doc in eq-email-pipeline?
   - Recommended: YES, at session start, as a standalone "design
     plan" commit, so M1's PR can reference it.
   - Get explicit user approval before pushing (the user prefers
     explicit commit/push permission).
   - Suggested message: `docs(plan): pending_interactions design
     for cold-inbound unknown-sender fix (v4, 4 Codex rounds)`

   M1 (eq-frontend Prisma migration; ~1-2 days, low risk):
   - cd /Users/peteroneil/eq-frontend
   - Open a fresh feature branch
   - Add `pending_interactions` Prisma model per plan §3.1 (~25
     columns; UNIQUE partial on (tenant_id, internet_message_id);
     index on (tenant_id, queue_id, archived_at); index on
     (tenant_id, expires_at) partial)
   - Add 3 new columns on Email model: account_provisioning_queue_id
     (UUID, nullable), local_enrichment_started_at (TIMESTAMPTZ,
     nullable), local_enrichment_completed_at (TIMESTAMPTZ,
     nullable). Add partial index on account_provisioning_queue_id.
   - Add UNIQUE constraint on
     interaction_summaries.(tenant_id, interaction_id, summary_type).
     VERIFY pre-flight whether already present (plan §14 open
     question #3).
   - CONFIRM `email_threads.(tenant_id, thread_key)` UNIQUE
     ALREADY EXISTS — per architecture.md:854; do NOT add it
     (Codex round-3 P3 caught the wrong claim in v3).
   - Generate Prisma migration. Review SQL carefully.
   - Run live-transcription-fastapi/scripts/verify_schema.py
     against the new columns (cross-repo verification — verifies
     the live Neon database accepts the new column references).
   - Codex review BEFORE merge per LOCKED-10. Use
     `model_reasoning_effort=medium` per LOCKED-18.
   - Open PR; surface to user for approval before merging.

   M2 (live-transcription-fastapi workflow promote-step + emit;
   ~3-4 days, medium-high risk):
   - cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi
   - Open a feature branch
   - Per plan §5.1-5.4: extend materialize_account_approval()
     with Step 4 (4-pre archives duplicates already in emails;
     4a inserts raw_interactions; 4b inserts emails; 4c upserts
     thread ONCE PER PENDING ROW; 4d inserts interaction_summaries
     with summary_type='email'; 4e archives pending).
   - Revise Step 5 link phase per plan §5.2: filter
     interaction_summaries by summary_type='email'; add OR clause
     for signals on OTHER queues that reference just-promoted
     interaction_ids (handles cross-queue cold-inbound, plan §8.6).
   - Add new emit_email_promoted_events step at END of
     account_provisioning_workflow per plan §5.4. Safe per DBOS
     plan §6.8 (appending steps at end).
   - Add MaterializationResult.promoted_interaction_ids field.
   - Update scripts/verify_consumer_contracts.py to recognize
     EmailPromoted detail-type.
   - Configure EventBridge rule for Source="com.yourapp.transcription"
     + DetailType="EmailPromoted" routing to eq-email-pipeline.
     Document the rule's ARN in M2's PR for M3 to reference.
   - Unit tests per plan §10.1.
   - Codex review BEFORE merge. Open PR.

   M3 (eq-email-pipeline EmailPromoted subscriber; ~2-3 days,
   medium risk):
   - cd /Users/peteroneil/eq-email-pipeline
   - Open a feature branch
   - First: confirm subscription pattern (open question #2 in
     plan §14). Inspect existing inbound webhook handlers; check
     for an existing SQS subscriber pattern. If SQS-from-
     EventBridge: extend the existing subscriber to handle the
     new EmailPromoted detail-type. If direct EventBridge: set
     up a new subscriber. Document the choice in M3's PR.
   - Implement handler per plan §6.2:
     * Step 0 (two-layer guard): hard guard on
       local_enrichment_completed_at; atomic CAS via
       try_claim_local_enrichment (UPDATE...WHERE...RETURNING).
     * Step 1-2: read raw_interaction + emails (thread_id
       already set by M2's Step 4c).
     * Step 3: fetch contacts (subset of participants — some
       contacts may not exist if their queue hasn't been
       approved; that's OK).
     * Step 4: branch on processing_tier. Light → skip
       LLM/Neo4j/Pinecone, just mark completed. Full → run
       full pipeline.
     * Step 5-8: Neo4j build_skeleton + write_flesh; LLM
       summary; Pinecone embed; thread summary update.
     * Step 9: mark_local_enrichment_completed LAST.
   - Add helpers in src/persistence/postgres.py:
     try_claim_local_enrichment, mark_local_enrichment_completed,
     fetch_email_by_interaction_id, fetch_raw_interaction,
     fetch_contacts_for_interaction.
   - Unit tests + integration test for idempotency (synthetic
     EmailPromoted emission with re-delivery).
   - Codex review BEFORE merge. Open PR.

   STOP after M3 OR continue to M4 in this session — your call
   based on context budget. Recommendation: STOP after M3; start
   M4 in a fresh session. M4 flips the switch.

   M4 (eq-email-pipeline orchestrator branch + atomic
   upsert_thread; ~4-5 days, medium-high risk — FLIPS THE SWITCH):
   - Extend email_exists() to UNION emails + pending_interactions
     on (tenant_id, internet_message_id).
   - Add persist_pending_interaction() helper.
   - REWRITE upsert_thread() to atomic INSERT...ON CONFLICT
     (tenant_id, thread_key) DO UPDATE — closes the pre-existing
     SELECT-then-UPSERT race. Verify all existing callers behave
     correctly after the rewrite (the known-account path uses
     upsert_thread today; semantics must be preserved).
   - Add §4.1 decision branch in orchestrator.py:174-196:
     * If account_id resolved → continue known path (no change).
     * Elif target_domain is PERSONAL → log dropped_personal_anchor + return.
     * Elif target_domain is INTERNAL → log dropped_internal_misconfigured + return.
     * Else (target_domain is unknown BUSINESS) → pending path
       per plan §4.2 (queue entry + signals + pending_interactions
       INSERT, all in one transaction).
   - Pre-allocate interaction_id at top of process_email (plan
     §4.3). Pass it as an argument to both insert_email (known
     path) and persist_pending_interaction (pending path).
   - Update tests/test_orchestrator_three_state.py with the §10.2
     cases (cold-inbound from unknown sender pending; multiple
     unknown participants; personal anchor dropped; internal
     anchor misconfigured; duplicate webhook before approval;
     handler idempotency; light tier; cross-queue link fill).
   - Codex review BEFORE merge. Open PR.

   M5 (production E2E + rollback drill; ~1-2 days):
   - LOCKED-17 Layer-1 collision check FIRST.
   - Run plan §10.3 E2E under test tenant
     11111111-1111-4111-8111-111111111111.
   - Verify all §11 acceptance invariants.
   - Optionally exercise §10.4 rollback drill (Phase 4 only).

8. PRE-MERGE RITUAL (per LOCKED-10 + LOCKED-18):
   - `codex review --commit HEAD -c model_reasoning_effort=medium`
     for multi-commit PRs once cumulative diff > ~1500 lines.
   - 4-round soft cap; extend when real P1s keep surfacing.
   - **Recognize round-4 false positives on plan-related changes**:
     Codex's grounding instinct "check the code" can flag
     planned-for-execution changes as findings. This session's R4
     found 0 real P1s but Codex flagged "v4 fixes not in repo"
     which is literally what M1-M4 deliver. If round 4 looks like
     this, plan converged; lock in.

9. SHARED-INFRASTRUCTURE-COLLISION PROTOCOL (LOCKED-17):
   - LAYER 1: ls ~/.claude/projects/-Users-peteroneil-*/*.jsonl.
     Any file modified in last hour = pause + ask.
   - LAYER 2: per-action confirmation before ANY destructive op
     on the shared test tenant. Applies when:
     * Running RUN_DESTRUCTIVE_TESTS=1 pytest
     * Running the M5 production canary
     * Any TRUNCATE / DROP / DELETE / CASCADE statement against
       test data

10. End-of-session: /context-save with a clear title indicating
    which milestones landed (e.g., "phase-1-email-pipeline-
    m1-m3-shipped-m4-next"). Rewrite NEXT-SESSION-START-HERE.md
    for whatever's next. Write a new dated next-session-prompt.md.
    Update MEMORY.md status string.

ANTI-ANCHORING — 18 LOCKED decisions exist; full list in
NEXT-SESSION-START-HERE.md §"LOCKED decisions". Most load-bearing
for this session:

(7) Two hard rules — no contact without account anchor; no
    interaction without account anchor. Approach C respects
    BOTH (pending_interactions is a separate table; raw_interactions
    still has NOT NULL account_id).
(10) Codex review BEFORE merging (4-round soft cap; round-4 false
     positives on plan-vs-code reviews are expected — recognize them).
(14) Pending-interactions pattern (Approach C). DO NOT revisit.
(15) Lean payload + typed columns. NOT JSONB. NOT full mirror.
(16) Path B (full reprocess on promote) via EventBridge EmailPromoted
     event. NOT a new sync HTTP API.
(17) Shared-tenant collision pre-flight before destructive ops.
(18) Codex multi-round: --commit HEAD past ~1500 lines;
     model_reasoning_effort=medium default.

ACKNOWLEDGED V1 LIMITATIONS (documented in plan; NOT regressions):

1. Personal/internal anchor cold-inbound → log+drop. Reason: queue
   is for unknown businesses to become accounts; personal/internal
   don't fit that model. V2 roadmap: audit log table.
2. Neo4j build_skeleton + write_flesh partial-retry corruption.
   Mitigation: 2-layer guard (atomic CAS + 5-minute soft TTL); rare
   scenarios. V2 roadmap: convert to MERGE patterns + edge-count
   thread counters.
3. upsert_thread pre-existing race FIXED in M4 (atomic INSERT...ON
   CONFLICT DO UPDATE). This is NOT a limitation — it's a fix.

VERIFIED CROSS-REPO STATE (2026-05-17 evening, end of plan-writing
session):

- eq-email-pipeline re-open trigger: ✅ DELIVERED (line 342)
- eq-frontend /dashboard/organization/email-pipeline admin route:
  ✅ EXISTS
- eq-frontend app/(workspace)/agent-queue user-facing UI: ✅ EXISTS
  (verify mid-execution if relevant; queue UI integration is out
  of plan scope per design doc §9)
- eq-agent-action-core worker_attempt_id: ❌ NOT in production
  OpenAPI BUT N/A in DBOS world (DBOS step caching + agent's
  run_id provide idempotency)
- eq-structured-graph-core consumer behavior: ⚠️ Envelope contract
  verified via M5 verify_consumer_contracts.py. Runtime MERGE
  behavior is a production-canary question (deferred to M5).
- interaction_summaries multi-variant model: ✅ DOCUMENTED at
  architecture.md:789 — link tables use summary_id (NOT
  interaction_id, despite the column NAME); the M1 UNIQUE composite
  preserves this.
- email_threads.(tenant_id, thread_key) UNIQUE: ✅ ALREADY EXISTS
  at architecture.md:854 (NO M1 migration needed for this).

UNFINISHED PHASE 1 + 1.5 WORK — FULL LIST

PRIMARY (this session's execution scope):

1. M1 — eq-frontend Prisma migration (1-2 days)
2. M2 — live-transcription-fastapi workflow + EmailPromoted emit
   (3-4 days)
3. M3 — eq-email-pipeline EmailPromoted subscriber (2-3 days)
4. M4 — eq-email-pipeline orchestrator branch + atomic upsert_thread
   (4-5 days; FLIPS THE SWITCH)
5. M5 — production E2E + rollback drill (1-2 days)

Recommendation: compress M1+M2+M3 into this session (~6-9 days
cumulative; all independently safe deploys); split M4+M5 into a
separate session.

SECONDARY (other unfinished items; NOT this session unless context
allows after M3):

6. Test-discipline-gaps Item 1 — audit + de-mock integration tests
   that mock lookup_account_by_domain at import level
7. Test-discipline-gaps Item 2 — complete per-attendee branching
   happy paths in production E2E suite (all four ingestion paths
   × known/unknown/personal/internal matrix)
8. Test-discipline-gaps Item 3 — narrow outer except Exception:
   blocks in services/transcript_enrichment.py:399-405 and similar
9. Production canary (deferred from M3+M4 + M5) — verify
   eq-structured-graph-core MERGE runtime behavior
10. M3.5 outbox drop — optional Prisma migration in eq-frontend

PHASE 2 (post-Phase-1-email-pipeline-completion; explicit stopping
point per 2026-05-15 plan):

- Neo4j build_skeleton + write_flesh MERGE-based idempotency
  (replaces V1's 2-layer guard mitigation; eliminates the bounded
  corruption window)
- Personal/internal anchor audit log table (currently log+drop)
- Identity state machine for contacts (shell / emerging / partial
  / resolved / verified). The pending_interactions pattern is the
  symmetric construct for interactions; design Phase 2 with awareness.

PHASE 3 (post-Phase-2):

- Conflict resolution + multi-account history + fuzzy matching

OPEN QUESTIONS DEFERRED TO EXECUTION (from plan §14)

1. email_exists extension exact SQL — verify column types/collations
   match between emails and pending_interactions during M4.
2. eq-email-pipeline EventBridge subscription pattern — confirm
   during M3 whether the repo uses SQS-from-EventBridge or direct
   EventBridge subscription.
3. interaction_summaries.(tenant_id, interaction_id, summary_type)
   UNIQUE — verify in M1 pre-flight whether already present.
4. Light tier handler behavior — confirm whether light-tier emails
   write any summaries today. If no, handler is a complete no-op
   for light tier.
5. EmailPromoted DLQ / observability — operations setup, separate
   from plan.
6. Backfill of historical dropped emails — confirm in M5 that no
   backfill needed (test data only).
7. Queue UI integration — `app/(workspace)/agent-queue` may want
   to surface a count of pending_interactions per queue entry.
   Defer UI to a separate eq-frontend session.

PRODUCTION CREDENTIALS + IDS (self-contained reference)

- Neon Postgres (eq-dev): project super-glitter-11265514, branch
  production, database neondb. Direct connection (no -pooler) for
  DBOS_SYSTEM_DATABASE_URL.
- Test tenant: 11111111-1111-4111-8111-111111111111. All test data.
  Per LOCKED-17, ask user per-batch for destructive ops.
- Test user (FK target): b0000000-0000-4000-8000-000000000002.
- Railway FastAPI: project 847cfa5a-b77c-4fb0-95e4-b20e8773c23e,
  service 59a69f3d-9a24-4041-942a-891c4a81c5fb, env
  e4c5ec15-1931-4632-9e58-92d9c6be4261, URL
  https://live-transcription-fastapi-production.up.railway.app.
- Railway eq-agent-action-core: URL
  https://eq-agent-action-core-production.up.railway.app, service
  3036ea0f-afc9-4bc4-889d-c98617d81e96.
- eq-email-pipeline: /Users/peteroneil/eq-email-pipeline (NOT
  under EQ-CORE/). Main HEAD 084567a as of 2026-05-17.
- eq-frontend: /Users/peteroneil/eq-frontend.
- Internal JWT: HS256, INTERNAL_JWT_SECRET, iss=eq-frontend,
  aud=eq-backend.
- AWS: EventBridge bus 'default' (configurable via
  EVENTBRIDGE_BUS_NAME); AWS_REGION=us-east-1.
- Neo4j: Aura instance c6171c63, URI
  neo4j+s://c6171c63.databases.neo4j.io.

USER POSTURE (load-bearing)

Non-developer founder. Make confident technical decisions; surface
only product/strategic decisions. Strict OSS only (no SSPL, no
BSL). Architectural correctness over short-term shortcuts.
Cutting-edge 2026 AI-native patterns. NO sunk-cost preservation.

Context economy matters. The user has surfaced context concerns
multiple times. Apply LOCKED-10 (4-round Codex soft cap) +
LOCKED-18 (--commit HEAD past ~1500 lines; medium reasoning
default) disciplines.

User explicitly REJECTED recipient-as-anchor for emails.
User explicitly LOCKED IN lean+typed schema (not JSONB).
User explicitly LOCKED IN Path B via EventBridge (not new sync API).
User explicitly LOCKED IN cross-queue link-fill-in via Step 5
OR clause.
User explicitly LOCKED IN personal/internal anchor log+drop as
V1 limitation.

All data is test data; no production users yet. Architectural
choices CAN accept short-term limitations that would block a
production-traffic ship, but the user is reluctant to ship hacks.

SCOPE OF THIS SESSION — EXPLICIT

In scope:
- M1 (eq-frontend Prisma migration) — first deploy
- M2 (live-transcription-fastapi workflow promote-step + emit) —
  safe to deploy after M1 (no-op until M4 ships)
- M3 (eq-email-pipeline EmailPromoted subscriber) — safe to deploy
  after M2 (no events emitting until M4 ships)
- The plan doc commit (eq-email-pipeline; recommended at session
  start as a standalone "design plan" commit)
- The handoff artifacts commit (live-transcription-fastapi;
  NEXT-SESSION-START-HERE.md + paste-prompt; recommended at session
  start as a standalone "docs(handoff): post-plan-lock handoff"
  commit on main)

OUT of scope for this session (deferred unless context allows):
- M4 (orchestrator branch + atomic upsert_thread) — recommend a
  SEPARATE SESSION; M4 flips the switch and warrants its own
  pre-merge ritual + production canary
- M5 (production E2E + rollback drill) — happens after M4
- Test-discipline-gaps items
- M3.5 outbox drop (optional)
- Phase 2 work

If you finish M1-M3 fast with substantial context remaining, you
CAN proceed to M4 in the same session — but treat it as a deliberate
decision, NOT an automatic progression. M4 is the highest-risk
milestone in the sequence.

STOP CONDITIONS (hard — surface to user)

- /context-restore returns NO_CHECKPOINTS
- MEMORY.md status isn't PHASE_1_EMAIL_PIPELINE_PLAN_LOCKED_EXECUTION_NEXT
- The plan claims something about existing code that doesn't match
  what M1-M4 actually find. STOP, surface, revise plan ONLY after
  user explicit approval.
- M1's Prisma migration is rejected by Codex with real P0/P1
  findings.
- Any milestone's Codex pre-merge review surfaces a P1 you can't
  resolve in one revision round (after the round-4 false-positive
  recognition heuristic).
- LOCKED-17 collision check shows a concurrent agent in another
  repo within the last hour AND the work you're about to do is
  destructive.
- You're tempted to revise the plan doc instead of surfacing a plan
  issue — STOP, surface the issue.
- The eq-frontend Prisma migration requires coordination you don't
  have agreement on (e.g., schema changes the eq-frontend agent
  hasn't seen).

The plan is the load-bearing artifact. The user is paying for
correct execution + careful coordination, not typing. A well-
executed M1 that takes longer is more valuable than three rushed
milestones that need to be re-worked.

KEY REFERENCE PATHS (for quick lookup during the session)

- THE PLAN: /Users/peteroneil/eq-email-pipeline/docs/superpowers/
  plans/2026-05-17-pending-interactions-cold-inbound-fix.md
- Wayfinding: docs/superpowers/specs/NEXT-SESSION-START-HERE.md
- Design doc: docs/superpowers/specs/2026-05-12-contact-quality-
  initiative-design.md
- DBOS plan: docs/superpowers/plans/2026-05-15-async-orchestration-
  dbos.md (§3.4, §6, §6.6, §6.8)
- Phase 1 plan: docs/superpowers/plans/2026-05-13-contact-quality-
  phase-1-and-1.5.md (Task 1.24 at line 2276)
- M5 verification tooling: scripts/verify_schema.py +
  scripts/verify_consumer_contracts.py
- Initiative snapshot: docs/superpowers/specs/2026-05-15-initiative-
  context-snapshot.md
- Lessons: tasks/lessons.md (bottom entries: shared-infra collision,
  Codex pre-merge gate, Review gates lesson — add the
  interaction_summaries Prisma naming-drift lesson when committing
  M1 or after)
- Comprehensive checkpoint: ~/.gstack/projects/oneilstokeseqrm-
  live-transcription-fastapi/checkpoints/20260517-144500-phase-1-
  email-pipeline-plan-v4-locked-comprehensive-handoff.md
- Initial save (less comprehensive): same dir, 20260517-143526-*
- Auto-memory: ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-
  transcription-fastapi/memory/
- MEMORY.md: same dir, MEMORY.md

eq-email-pipeline source paths the plan touches:
- src/pipeline/orchestrator.py (the §4.1 decision branch in M4)
- src/persistence/postgres.py (insert_email, upsert_thread,
  email_exists, link table inserts, NEW helpers for M3+M4)
- src/persistence/pending_account_mappings.py (existing reopen +
  upsert + signal helpers; referenced by §4.2 pending path)
- src/pipeline/skeleton.py (Neo4j skeleton build; referenced by
  EmailPromoted handler §6.2 Step 5)
- src/pipeline/extract.py (LLM extraction; §6.2 Step 5)
- src/pipeline/flesh.py (Neo4j flesh write; §6.2 Step 5)
- tests/test_orchestrator_three_state.py (M4 test updates)
- docs/architecture.md (schema reference; especially lines 789
  for interaction_summaries multi-variant model + 803-854 for
  emails + email_threads)

live-transcription-fastapi source paths the plan touches:
- services/account_provisioning/materialization.py (M2 Step 4 +
  Step 5)
- services/account_provisioning/workflow.py (M2 new
  emit_email_promoted_events step at END)
- services/account_provisioning/eventbridge_emit.py (existing §6.6
  emit; unchanged but reused for the new promoted interactions)
- scripts/verify_schema.py (M5 schema gate; reuse in M1 + M2)
- scripts/verify_consumer_contracts.py (M5 envelope gate; M2
  updates for EmailPromoted)

Start with /context-restore. The plan is the load-bearing artifact.
Read it before code.
```

---

## Notes for the user pasting this

Key changes vs the 2026-05-17 PM prompt (which framed the work as plan-writing):

- **Status string is `PHASE_1_EMAIL_PIPELINE_PLAN_LOCKED_EXECUTION_NEXT`.**
- **Scope is EXECUTION**, not plan-writing. The plan is locked at v4.
- LOCKED decisions are now 18 (added 15: lean+typed columns; 16: Path B EventBridge).
- 11 substantive Codex findings resolved across 4 rounds; round-4 false positives recognized — the 4-round soft cap was honored.
- M1-M5 sequence documented; M1+M2+M3 are recommended as the first execution session's scope, M4+M5 as a separate session.
- Acknowledged V1 limitations explicitly documented (personal/internal anchor log+drop; Neo4j partial-retry corruption mitigated by 2-layer guard).
- Cross-repo: M1 in eq-frontend, M2 in live-transcription-fastapi, M3+M4 in eq-email-pipeline.
- Plan doc lives in eq-email-pipeline and is UNCOMMITTED — recommend committing at session start.
- Concrete code paths listed for both repos.
- Round-4 false-positive recognition heuristic codified — saves the next agent from chasing phantom findings.
