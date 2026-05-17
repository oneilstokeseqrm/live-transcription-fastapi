# Next Session Opening Prompt (2026-05-17 evening, post-M1+M2-DEPLOYED)

Paste the block below as the opening message of the next Claude session. Written 2026-05-17 evening after M1 (eq-frontend PR #392 → `de586bbc`) and M2 (live-transcription-fastapi PR #19 → `756575d7`) of the Phase-1-email-pipeline cold-inbound fix were merged AND deployed. 10 Codex review rounds total across both. 14 substantive findings resolved. Production state verified: Neon has new schema; Railway has new code; `/health` 200.

**Important:** the next session is **EXECUTION** of M3 only — implementing the eq-email-pipeline `EmailPromoted` subscriber. NOT plan revision. The plan is locked. Read it, implement M3, surface to user when done.

---

```
You're working in /Users/peteroneil/eq-email-pipeline.

This is a continuation session for the Contact Quality and Account-
Anchoring Initiative — a multi-phase data-quality project on an
AI-native customer intelligence platform.

Phase 1 (account-anchor contract end-to-end) shipped 2026-05-14.
Phase 1.5 (async account-provisioning workflow on DBOS substrate +
verified-contract tooling) shipped across 2026-05-15 and 2026-05-17.

Phase-1-email-pipeline M1 (eq-frontend Prisma migration) and M2
(live-transcription-fastapi workflow promote-step + EmailPromoted emit)
were shipped, merged, AND deployed to production on 2026-05-17 evening:
- M1 (eq-frontend PR #392): merged as `de586bbce1fa3d49b4ca4455c618ea095260534f`
  at 2026-05-17T22:28:49Z. Vercel `prisma migrate deploy` applied the
  migration. Neon production schema verified: pending_interactions table
  exists, emails has 3 new columns (account_provisioning_queue_id,
  local_enrichment_started_at, local_enrichment_completed_at),
  interaction_summaries has the new composite UNIQUE (tenant_id,
  interaction_id, summary_type) AND composite FK to raw_interactions,
  old single-column UNIQUE + FK are gone, raw_interactions has new
  composite UNIQUE (tenant_id, interaction_id) as FK target.
  3 Codex rounds, CLEAN at R3, 3 findings resolved (cross-tenant FK,
  index leading columns, opportunity.listByInteraction findFirst→findMany).
- M2 (live-transcription-fastapi PR #19): merged as
  `756575d7e3d6fb99949980a5ffb3968a4f6c7e9c` at 2026-05-17T22:29:38Z
  (49 seconds after M1 merge — closing the M1↔M2 deploy window).
  Railway deployment 809679fc-057f-4580-984a-093d01552bb0 Status=SUCCESS.
  /health returns 200 with {"status":"ok"}.
  7 Codex rounds, CLEAN at R7, 11 substantive findings resolved.

CRITICAL M1↔M2 deploy coordination (already executed):
The plan §7 incorrectly claimed M1 was independently deployable. M1 drops
the single-column UNIQUE on interaction_summaries.interaction_id; M2's
UPSERT_PLACEHOLDER_SUMMARY_SQL fix switches the dependent ON CONFLICT to
the new composite. Between M1 deploy and M2 deploy, every meeting approval
would fail at runtime. Actual window: M1 merged 22:28:49, M2 merged
22:29:38, Railway deploy completed shortly after. Test-data only; no
real-user impact. Window now CLOSED.

Your job THIS session is to EXECUTE M3 — the eq-email-pipeline
EmailPromoted subscriber. Implementation, NOT plan revision.

CRITICAL: this session is EXECUTION. The plan is locked at
eq-email-pipeline:033626a. If you find issues with the plan during
implementation, surface them via AskUserQuestion rather than silently
revising the plan doc. Compressing plan revision + implementation into
the same session re-introduces the trap the plan-writing session was
structured to avoid.

Before doing anything else, follow these steps IN ORDER:

1. Run `/context-restore`. You should find a checkpoint titled
   "phase-1-email-pipeline-m1-m2-shipped-m3-next" dated 2026-05-17
   evening (the 16:42 save). Note: title says "shipped" because it was
   saved pre-merge; the merge happened ~10 min after the checkpoint.
   Load it. If /context-restore returns NO_CHECKPOINTS, STOP and
   surface — that's a sync gap.

2. Read MEMORY.md (auto-loads). Confirm project status reads
   PHASE_1_EMAIL_PIPELINE_M1_M2_DEPLOYED_M3_NEXT. If anything else
   (e.g., still says "PLAN_LOCKED" or "SHIPPED_NOT_DEPLOYED"), STOP
   and surface — production state may be inconsistent with memory.

3. READ THE WAYFINDING DOC:
   /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/
     superpowers/specs/NEXT-SESSION-START-HERE.md

   Sections "SESSION SCOPE FOR THE NEXT SESSION", "CRITICAL — what
   already shipped this prior session" (with merge SHAs), "Production
   state verified end-of-prior-session", "Execution sequence — M3"
   (with concrete helper function signatures + handler skeleton),
   "LOCKED decisions" (18 total), "Acknowledged V1 limitations",
   "Pre-existing CI gotcha to surface" (live-db DIRECT_DATABASE_URL),
   and "Stop conditions" are load-bearing.

4. READ THE PLAN DOC (mandatory, ~20-25 min focused on M3 sections):
   /Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/
     2026-05-17-pending-interactions-cold-inbound-fix.md

   Especially:
   - §0 Revision history (v1 → v4 across 4 plan-writing Codex rounds)
   - §1.2 Recharacterization (this is finishing Phase 1, not new scope)
   - §6 EmailPromoted handler — PRIMARY M3 REFERENCE
   - §6.1 Where the handler lives (subscription pattern open question)
   - §6.2 Step-by-step handler logic (this is your implementation guide)
   - §6.3 Idempotency contract — honest accounting (the two-layer guard
     + partial-retry corruption acknowledgment)
   - §6.4 Failure modes table
   - §10.1 Unit tests relevant to M3 (try_claim, mark_completed, etc.)
   - §10.2 Integration tests (idempotency, light vs full tier)
   - §11 Acceptance invariants — the ship-when-true checklist
   - §14 #2 Open question: subscription pattern (resolve in M3)
   - §14 #4 Open question: light-tier handler behavior
   - §16 What this plan does NOT do

5. READ THE M2 EMIT CONTRACT (the upstream you're subscribing to):
   /Users/peteroneil/EQ-CORE/live-transcription-fastapi/services/
     account_provisioning/eventbridge_emit.py
   function emit_email_promoted_for_materialization.

   This is the WIRE CONTRACT M3's subscriber consumes:
   - Source: "com.yourapp.transcription"
   - DetailType: "EmailPromoted"
   - Detail JSON: {tenant_id, interaction_id, account_id, queue_id,
                   promoted_at}

   Also read services/account_provisioning/steps.py for the
   emit_email_promoted_events @DBOS.step that wraps it.

6. READ M1 + M2 MERGED PR DESCRIPTIONS for the comprehensive narrative:
   - https://github.com/oneilstokeseqrm/eq-frontend/pull/392
   - https://github.com/oneilstokeseqrm/live-transcription-fastapi/pull/19

   Both include detailed Codex review trajectory, design decisions,
   and acceptance invariants verified.

7. Verify pre-flight state:
   - Production /health:
     curl -sS -o /dev/null -w "%{http_code}\n"
       https://live-transcription-fastapi-production.up.railway.app/health
     Expected: 200.
   - Production schema (use Neon MCP tool mcp__neon__run_sql against
     project super-glitter-11265514, database neondb):
     a) SELECT 1 FROM information_schema.tables WHERE table_name=
        'pending_interactions'; → 1 row
     b) SELECT COUNT(*) FROM information_schema.columns WHERE
        table_schema='public' AND table_name='emails' AND column_name
        LIKE 'local_enrichment%'; → 2
     c) SELECT 1 FROM pg_indexes WHERE indexname=
        'interaction_summaries_tenant_id_interaction_id_summary_type_key';
        → 1 row
     d) SELECT 1 FROM pg_indexes WHERE indexname=
        'interaction_summaries_interaction_id_key';
        → 0 rows (must be gone)
   - eq-email-pipeline git state:
     git -C /Users/peteroneil/eq-email-pipeline status
     Expected: clean on main, untracked uv.lock only.
     git -C /Users/peteroneil/eq-email-pipeline log --oneline -3
     Expected top: 033626a docs(plan): pending_interactions design...
   - eq-frontend git state:
     git -C /Users/peteroneil/eq-frontend log --oneline -3
     Expected top: de586bbc Merge pull request #392 ...
       (the M1 merge commit, OR squash commit with same SHA).
   - live-transcription-fastapi git state:
     git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi
       log --oneline -3
     Expected top: 756575d M2: promote pending_interactions ... (#19),
       then c24c78f docs(handoff): ..., then 7ebdee5 docs(handoff)...
   - SHARED-TENANT-COLLISION CHECK (LOCKED-17):
     ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
     Any file modified in last hour = pause + ask user. M3 is
     non-destructive scope so this is informational only; would matter
     for the M5 production canary.

8. After reading, briefly confirm in one paragraph: where the prior
   session left off + your M3 implementation approach for this session
   + that you understand this is EXECUTION (not plan revision).

9. EXECUTE — M3 (eq-email-pipeline EmailPromoted subscriber).

   FIRST sub-decision: resolve the subscription-pattern open question
   (plan §14 #2). Inspect eq-email-pipeline:
   - Look for existing SQS subscriber patterns in src/.
   - Check src/providers/ and src/api/ for inbound event handling.
   - Determine: SQS-from-EventBridge vs direct EventBridge subscription.
   - Document the choice in M3 PR description.

   THEN implement per plan §6.2:

   New helpers in src/persistence/postgres.py:
   - try_claim_local_enrichment(email_id) -> bool
     Atomic CAS:
       UPDATE emails
       SET local_enrichment_started_at = NOW()
       WHERE id = $email_id
         AND local_enrichment_completed_at IS NULL
         AND (local_enrichment_started_at IS NULL
              OR local_enrichment_started_at < NOW() - INTERVAL '5 minutes')
       RETURNING id;
     Returns True if claimed.
   - mark_local_enrichment_completed(email_id) -> None
   - fetch_email_by_interaction_id(tenant_id, interaction_id) -> EmailRow
   - fetch_raw_interaction(tenant_id, interaction_id) -> RawInteractionRow
   - fetch_contacts_for_interaction(tenant_id, interaction_id)
     -> dict[email, contact_id]
     JOIN interaction_summaries (summary_type='email') →
       interaction_contact_links → contacts.
     IMPORTANT: filter by summary_type='email' (NOT 'meeting').
     The link table column literally named "interaction_id" actually
     holds summary_id (Prisma naming-drift documented in
     tasks/lessons.md).

   Handler (location TBD per subscription pattern):
   handle_email_promoted(event: EmailPromotedEvent) -> None
   - Step 0: two-layer idempotency guard.
     Layer 1 (hard): if local_enrichment_completed_at IS NOT NULL → skip.
     Layer 2 (soft TTL): atomic CAS via try_claim_local_enrichment.
     If claim lost → skip.
   - Step 1-2: read raw_interactions + emails (thread_id already set
     by M2's Step 4c, deployed).
   - Step 3: fetch_contacts_for_interaction (subset — cross-queue
     participants may not have contacts yet, that's OK).
   - Step 4: branch on processing_tier:
     * light → mark_local_enrichment_completed; return.
     * full → continue.
   - Step 5: Neo4j build_skeleton + write_flesh + LLM extract.
   - Step 6: Headline + summary on Neo4j Interaction node ONLY.
     Do NOT add headline/summary columns to emails (plan §3.5).
   - Step 7: Pinecone embedding.
   - Step 8: Thread summary update (existing pattern; idempotent).
   - Step 9: mark_local_enrichment_completed LAST (after all writes
     succeed).

   Tests:
   - Unit: each new persistence helper. Especially the try_claim CAS
     edge cases (TTL boundary, completed_at preempts claim, concurrent
     claims).
   - Integration: synthetic EmailPromoted emission with re-delivery;
     verify Neo4j has exactly one Interaction node + Pinecone has one
     vector + message_count unchanged on second delivery.

   Codex review BEFORE merge (LOCKED-10). Use:
     codex review --base main -c 'model_reasoning_effort="medium"'
       --enable web_search_cached
   4-round soft cap. Extend if real P1s keep surfacing.

   Open M3 PR; surface to user for approval before merge.

   STOP after M3. RECOMMENDATION: M4 + M5 are separate sessions per
   plan §12. M4 flips the switch on cold-inbound capture and warrants
   its own pre-merge ritual + production canary.

10. PRE-MERGE RITUAL (per LOCKED-10 + LOCKED-18):
    - `codex review --base main -c 'model_reasoning_effort="medium"'
      --enable web_search_cached` for cumulative HEAD-vs-main review.
    - 4-round soft cap. Extend when real P1s keep surfacing (M2 went
      7 rounds with real findings through R6, R7 CLEAN; M1 went 3
      rounds, R3 CLEAN). Recognize round-N convergence pattern:
      severity decreasing + non-redundant findings → continue.

11. SHARED-INFRASTRUCTURE-COLLISION PROTOCOL (LOCKED-17):
    - LAYER 1: ls ~/.claude/projects/-Users-peteroneil-*/*.jsonl.
      Any file modified in last hour = pause + ask. M3 is
      non-destructive so this is informational only.
    - LAYER 2: per-action confirmation before ANY destructive op on
      the shared test tenant. Applies for M5 canary (separate session).

12. End-of-session: /context-save with title indicating M3 status
    (e.g., "phase-1-email-pipeline-m3-shipped-m4-next" or
    "phase-1-email-pipeline-m3-in-progress" if not complete).
    Rewrite NEXT-SESSION-START-HERE.md for M4 scope. Write a new
    dated next-session-prompt.md following the depth + structure of
    THIS prompt. Update MEMORY.md status string.

ANTI-ANCHORING — 18 LOCKED decisions exist; full list in
NEXT-SESSION-START-HERE.md §"LOCKED decisions". Most load-bearing
for M3:

(7) Two hard rules — no contact without account anchor; no interaction
    without account anchor. Both invariants already enforced by M1+M2's
    deployed code; M3 must not weaken them.
(9) Materialization REQUIRES Lane 2 raw_interactions. M3's
    fetch_raw_interaction will find these rows because M2 already
    inserted them (Step 4a) before emitting EmailPromoted.
(10) Codex review BEFORE merging (4-round soft cap; round-N convergence
     pattern: severity-decrease + non-redundant findings → continue).
(14) Pending-interactions pattern (Approach C). DO NOT revisit.
(15) Lean payload + typed columns. M2's lean EmailPromoted detail
     payload reflects this — handler reads emails+raw_interactions by
     interaction_id rather than carrying full payload.
(16) Path B (full reprocess on promote) via EventBridge EmailPromoted.
     M3 IS the consumer side of this.
(17) Shared-tenant collision pre-flight.
(18) Codex multi-round defaults: --commit HEAD past ~1500 lines;
     model_reasoning_effort=medium default.

ACKNOWLEDGED V1 LIMITATIONS (documented in plan; NOT regressions):

1. Personal/internal anchor cold-inbound → log+drop. Reason: queue is
   for unknown businesses to become accounts; personal/internal don't
   fit. V2 roadmap: audit log table.
2. Neo4j build_skeleton + write_flesh partial-retry corruption.
   Mitigation: M3 implements the 2-layer guard (atomic CAS + 5-min
   soft TTL); rare scenarios (handler hangs >5min then retries).
   V2 roadmap: MERGE patterns + edge-count thread counters.
3. upsert_thread pre-existing race — FIXED in M2 for workflow promote
   path (atomic INSERT...ON CONFLICT DO UPDATE inlined in
   UPSERT_EMAIL_THREAD_SQL). M4 will fix it for eq-email-pipeline
   orchestrator known-account path.
4. Legacy per-signal loop hardcodes summary_type='meeting'. For
   re-pointed email signals (M2 4-pre-1) it creates a duplicate
   'meeting' summary alongside the existing 'email' summary. Cosmetic
   data inconsistency, NOT functionally broken — downstream filters
   by summary_type='email' get the correct link from M2's Step 5
   batch. Future cleanup: type-aware legacy loop.

VERIFIED CROSS-REPO STATE (2026-05-17 evening, end of prior session)

- M1 merged + deployed: eq-frontend de586bbc on origin/main; Vercel
  prisma migrate deploy succeeded; Neon production schema reflects all
  M1 changes (verified by direct query).
- M2 merged + deployed: live-transcription-fastapi 756575d7 on
  origin/main; Railway deployment 809679fc Status=SUCCESS; /health 200.
- eq-email-pipeline UNCHANGED (no work this prior session beyond plan
  commit 033626a). M3 is the first eq-email-pipeline work for the
  Phase-1-email-pipeline initiative.
- eq-frontend live-db CI workflow MISSING DIRECT_DATABASE_URL env var.
  PR #392 failed this check (Vercel preview deploy passed; main isn't
  protected so merge succeeded). Fix in a small follow-up PR before
  the next Prisma migration. NOT M3 scope.

UNFINISHED PHASE 1 + 1.5 WORK — FULL LIST

PRIMARY (this session's execution scope):
1. M3 — eq-email-pipeline EmailPromoted subscriber (2-3 days, medium
   risk).

NEXT (separate session):
2. M4 — eq-email-pipeline orchestrator branch + atomic upsert_thread
   (4-5 days, medium-high risk; FLIPS THE SWITCH on cold-inbound
   capture).
3. M5 — production E2E + rollback drill (1-2 days; LOCKED-17 Layer-1
   check first).

SECONDARY (other unfinished items; NOT this session):
4. Test-discipline-gaps Item 1 — audit + de-mock integration tests
   that mock lookup_account_by_domain at import level.
5. Test-discipline-gaps Item 2 — complete per-attendee branching happy
   paths in production E2E suite.
6. Test-discipline-gaps Item 3 — narrow outer except Exception: blocks
   in services/transcript_enrichment.py:399-405 and similar.
7. Production canary (deferred to M5).
8. M3.5 outbox drop — optional Prisma migration in eq-frontend.
9. eq-frontend live-db CI workflow DIRECT_DATABASE_URL fix.
10. M2-deferred: verify_consumer_contracts.py extension to recognize
    EmailPromoted detail-type + probe EventBridge rule (plan §5.5
    says M5 scope).
11. M2-deferred: legacy per-signal loop type-awareness (drops cosmetic
    duplicate 'meeting' summary).
12. M2-deferred: comprehensive unit + integration tests for the new
    Step 4 promote + Step 5 batch + emit_email_promoted_events
    (some can land alongside M3).

PHASE 2 (post-Phase-1-email-pipeline-completion; explicit stopping
point per 2026-05-15 plan):
- Neo4j build_skeleton + write_flesh MERGE-based idempotency (replaces
  V1's 2-layer guard mitigation; eliminates bounded corruption window).
- Personal/internal anchor audit log table (currently log+drop).
- Identity state machine for contacts (shell / emerging / partial /
  resolved / verified). The pending_interactions pattern is the
  symmetric construct for interactions; design Phase 2 with awareness.

PHASE 3 (post-Phase-2):
- Conflict resolution + multi-account history + fuzzy matching.

OPEN QUESTIONS DEFERRED TO EXECUTION (from plan §14)

1. eq-email-pipeline EventBridge subscription pattern — confirm during
   M3 implementation. Inspect existing inbound webhook handlers;
   document the choice in M3 PR.
2. Light tier handler behavior — confirm during M3 whether light-tier
   emails write any summaries today. If no, handler is a complete
   no-op for light tier.
3. EmailPromoted DLQ + observability — operations setup, separate from
   plan.
4. Backfill of historical dropped emails — confirm in M5 that no
   backfill needed (test data only).
5. Queue UI integration — `app/(workspace)/agent-queue` may want to
   surface a count of pending_interactions per queue entry. Defer UI
   work to a separate eq-frontend session.

PRODUCTION CREDENTIALS + IDS (self-contained reference)

- Neon Postgres (eq-dev): project super-glitter-11265514, branch
  production, database neondb. Direct connection (no -pooler) for
  DBOS_SYSTEM_DATABASE_URL.
- Test tenant: 11111111-1111-4111-8111-111111111111. All test data.
- Test user (FK target): b0000000-0000-4000-8000-000000000002.
- Railway live-transcription-fastapi: project
  847cfa5a-b77c-4fb0-95e4-b20e8773c23e, service
  59a69f3d-9a24-4041-942a-891c4a81c5fb, env
  e4c5ec15-1931-4632-9e58-92d9c6be4261, URL
  https://live-transcription-fastapi-production.up.railway.app.
  M2 deployed as 809679fc-057f-4580-984a-093d01552bb0 (SUCCESS).
- Railway eq-agent-action-core: URL
  https://eq-agent-action-core-production.up.railway.app, service
  3036ea0f-afc9-4bc4-889d-c98617d81e96.
- eq-email-pipeline: /Users/peteroneil/eq-email-pipeline (NOT under
  EQ-CORE/). Main HEAD 033626a as of M2 ship.
- eq-frontend: /Users/peteroneil/eq-frontend. M1 merged at de586bbc.
- Internal JWT: HS256, INTERNAL_JWT_SECRET, iss=eq-frontend,
  aud=eq-backend.
- AWS: EventBridge bus 'default' (configurable via
  EVENTBRIDGE_BUS_NAME); AWS_REGION=us-east-1. EmailPromoted rule
  MUST be configured (Terraform / AWS console) before M3's subscriber
  receives events. Pattern: Source=com.yourapp.transcription +
  DetailType=EmailPromoted, target = eq-email-pipeline's subscriber
  (SQS-from-EventBridge OR direct, TBD in M3).
- Neo4j: Aura instance c6171c63, URI
  neo4j+s://c6171c63.databases.neo4j.io.

USER POSTURE (load-bearing)

Non-developer founder. Make confident technical decisions; surface
only product/strategic decisions. Strict OSS only (no SSPL, no BSL).
Architectural correctness over short-term shortcuts. Cutting-edge
2026 AI-native patterns. NO sunk-cost preservation.

Context economy matters. M2 took 7 Codex rounds (heavy context);
M3 should be more contained (single-repo, smaller scope). Plan for
~4-5 Codex rounds maximum on M3 unless real P1s keep surfacing.

User explicitly REJECTED recipient-as-anchor for emails.
User explicitly LOCKED IN lean+typed schema (not JSONB).
User explicitly LOCKED IN Path B via EventBridge (not new sync API).
User explicitly LOCKED IN cross-queue link-fill-in via M2 Step 5
OR clause.
User explicitly LOCKED IN personal/internal anchor log+drop as V1
limitation.
User explicitly chose to coordinate M1+M2 merge (not revert M1) when
the deploy coupling was discovered — same posture applies to any new
coupling discovered during M3.

All data is test data; no production users yet. Architectural choices
CAN accept short-term limitations that would block a production-traffic
ship, but the user is reluctant to ship hacks.

User explicitly approves git push when asked; do NOT push without
asking first.

SCOPE OF THIS SESSION — EXPLICIT HARD CONSTRAINT

In scope:
- M3 (eq-email-pipeline EmailPromoted subscriber).

OUT of scope for this session:
- M4 (orchestrator branch + atomic upsert_thread rewrite) — separate
  session per plan; M4 flips the switch on cold-inbound capture and
  warrants its own pre-merge ritual + production canary.
- M5 (production E2E + rollback drill) — separate session.
- M1 / M2 fixes (they're CLEAN at session end; if downstream review
  surfaces new findings, surface to user — don't silently fix).
- eq-frontend live-db CI fix (DIRECT_DATABASE_URL) — small follow-up
  PR; surface to user as a pre-existing gotcha.
- Test-discipline-gaps items, M3.5 outbox drop, queue UI work, all
  Phase 2/3 work.

If you finish M3 fast with substantial context remaining, you CAN
proceed to M4 in the same session — but treat it as a deliberate
decision, NOT an automatic progression. M4 is the highest-risk
milestone in the sequence.

STOP CONDITIONS (hard — surface to user)

- /context-restore returns NO_CHECKPOINTS.
- MEMORY.md status isn't PHASE_1_EMAIL_PIPELINE_M1_M2_DEPLOYED_M3_NEXT.
- Production state has rolled back (M1 migration reverted, M2 code
  reverted) — verify Neon + /health at session start. If reverted,
  STOP and surface.
- The plan claims something about existing eq-email-pipeline code that
  doesn't match what M3 actually finds. STOP, surface, revise plan
  ONLY after user explicit approval.
- M3's Codex pre-merge review surfaces a P1 you can't resolve in one
  revision round (after the round-4 false-positive recognition
  heuristic).
- LOCKED-17 collision check shows a concurrent agent in another repo
  within the last hour AND the work you're about to do is destructive
  (M3 alone is non-destructive; would matter for M5 canary).
- You're tempted to revise the plan doc instead of surfacing a plan
  issue — STOP, surface the issue.

KEY REFERENCE PATHS (all on origin/main as of 2026-05-17 evening)

- THE PLAN (load-bearing): /Users/peteroneil/eq-email-pipeline/docs/
  superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md
  (eq-email-pipeline:033626a)
- Wayfinding doc: /Users/peteroneil/EQ-CORE/live-transcription-fastapi/
  docs/superpowers/specs/NEXT-SESSION-START-HERE.md
- M1 merged PR: https://github.com/oneilstokeseqrm/eq-frontend/pull/392
  (merged as de586bbc)
- M2 merged PR: https://github.com/oneilstokeseqrm/
  live-transcription-fastapi/pull/19 (merged as 756575d7)
- M2 emit contract: /Users/peteroneil/EQ-CORE/live-transcription-fastapi/
  services/account_provisioning/eventbridge_emit.py
  (function emit_email_promoted_for_materialization)
- M2 step wrapper: /Users/peteroneil/EQ-CORE/live-transcription-fastapi/
  services/account_provisioning/steps.py
  (function emit_email_promoted_events)
- Design doc: docs/superpowers/specs/
  2026-05-12-contact-quality-initiative-design.md
- DBOS plan: docs/superpowers/plans/
  2026-05-15-async-orchestration-dbos.md (§3.4, §6, §6.6, §6.8)
- Phase 1 plan: docs/superpowers/plans/
  2026-05-13-contact-quality-phase-1-and-1.5.md (Task 1.24 at line 2276)
- M5 verification tooling: scripts/verify_schema.py +
  scripts/verify_consumer_contracts.py (already merged; usable from M3
  for cross-repo verification)
- Comprehensive checkpoint: ~/.gstack/projects/
  oneilstokeseqrm-live-transcription-fastapi/checkpoints/
  20260517-164251-phase-1-email-pipeline-m1-m2-shipped-m3-next.md
  (saved pre-merge — merge happened ~10 min later; checkpoint
  reflects ready-to-merge state, this prompt reflects post-deploy
  state)
- Auto-memory: ~/.claude/projects/
  -Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/
- MEMORY.md: same dir, MEMORY.md

eq-email-pipeline source paths M3 touches (best guesses; confirm):
- src/persistence/postgres.py (new helpers — primary M3 file)
- src/providers/ OR src/api/ (subscriber location TBD per pattern)
- src/pipeline/skeleton.py (Neo4j skeleton build; §6.2 Step 5)
- src/pipeline/extract.py (LLM extraction; §6.2 Step 5)
- src/pipeline/flesh.py (Neo4j flesh write; §6.2 Step 5)
- tests/ (new unit + integration tests for the handler)
- docs/architecture.md (update if new helpers warrant it)

The plan is the load-bearing artifact. M2's emit step is the upstream
wire contract. Start with /context-restore. Read the plan §6 next.
Then execute M3.
```

---

## Notes for the user pasting this

This prompt is the comprehensive M3 handoff. Key differences vs the earlier 2026-05-17 evening prompt (which framed work as "open and unmerged"):

- **Status string is `PHASE_1_EMAIL_PIPELINE_M1_M2_DEPLOYED_M3_NEXT`.** Both PRs merged + deployed; production verified.
- **Concrete merge SHAs** for M1 (`de586bbc`) and M2 (`756575d7`) included throughout.
- **Production state verified inline** — Neon schema queries, /health, Railway deployment ID with SUCCESS status.
- **M1↔M2 deploy window** documented as CLOSED (49 seconds between merges, Railway deploy completed shortly after).
- **Scope is M3 only**, single-repo (eq-email-pipeline). Pre-flight verifies M1+M2 didn't roll back.
- **18 LOCKED decisions** + acknowledged V1 limitations + stop conditions all carried forward unchanged.
- **Pre-existing CI gotcha** (eq-frontend live-db DIRECT_DATABASE_URL) surfaced as a follow-up — Vercel deploy IS the meaningful validation, but should fix before next migration.
- **Session lessons codified** for the M3 agent: cross-repo deploy coordination rule + Codex round-N convergence pattern + live-db env var fix.
- **Concrete helper function signatures** + handler skeleton + test scope in the wayfinding doc.
- **Self-contained reference** — no external lookups needed to bootstrap.

Paste the prompt block at the start of the next session. The first action will be `/context-restore`, which loads the comprehensive checkpoint. The rest follows from there.
