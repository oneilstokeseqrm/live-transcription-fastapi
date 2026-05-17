# Next Session Opening Prompt (2026-05-17 evening, post-M1+M2-shipped)

Paste the block below as the opening message of the next Claude session. Written 2026-05-17 evening after M1 (eq-frontend PR #392) and M2 (live-transcription-fastapi PR #19) of the Phase-1-email-pipeline cold-inbound fix were shipped as PRs. 10 Codex review rounds total. 14 substantive findings resolved.

**Important:** the next session is **EXECUTION** — implementing M3 (eq-email-pipeline EmailPromoted subscriber). NOT plan revision. The plan is locked. Read it, implement M3, surface to user when done.

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
were shipped 2026-05-17 evening as PRs:
- M1: https://github.com/oneilstokeseqrm/eq-frontend/pull/392
  (3 Codex rounds, CLEAN at R3)
- M2: https://github.com/oneilstokeseqrm/live-transcription-fastapi/pull/19
  (7 Codex rounds, CLEAN at R7; 11 substantive findings resolved)

CRITICAL M1→M2 deploy coordination: plan §7 incorrectly claimed M1 was
independently deployable. M1 drops the single-column UNIQUE on
interaction_summaries.interaction_id; M2 fixes the dependent
UPSERT_PLACEHOLDER_SUMMARY_SQL to use the new composite ON CONFLICT.
Once M1 deploys, meeting approvals break at runtime until M2 deploys.
Coordinate: merge M1 → wait for Vercel → merge M2 → wait for Railway.
~5min window acceptable on test data.

Your job THIS session is to EXECUTE M3 — the eq-email-pipeline
EmailPromoted subscriber. Implementation, NOT plan revision.

CRITICAL: this session is EXECUTION. If you find issues with the plan
during implementation, surface them via AskUserQuestion rather than
silently revising the plan doc. Compressing plan revision + implementation
into the same session re-introduces the trap the plan-writing session
was structured to avoid.

Before doing anything else, follow these steps IN ORDER:

1. Run `/context-restore`. You should find a checkpoint titled
   "phase-1-email-pipeline-m1-m2-shipped-m3-next" dated 2026-05-17
   evening (the 16:42 save). Load it. If /context-restore returns
   NO_CHECKPOINTS, STOP and surface — that's a sync gap.

2. Read MEMORY.md (auto-loads). Confirm project status reads
   PHASE_1_EMAIL_PIPELINE_M1_M2_SHIPPED_M3_NEXT. If anything else,
   STOP and surface.

3. READ THE WAYFINDING DOC:
   /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/
     superpowers/specs/NEXT-SESSION-START-HERE.md

   Sections "SESSION SCOPE FOR THE NEXT SESSION", "CRITICAL — M1↔M2
   deploy coordination", "Execution sequence — M3", "LOCKED decisions"
   (18 total), "Acknowledged V1 limitations", and "Stop conditions"
   are load-bearing.

4. READ THE PLAN DOC (mandatory, ~20 min for §6 + relevant sections):
   /Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/
     2026-05-17-pending-interactions-cold-inbound-fix.md

   Especially:
   - §6.1-6.4 EmailPromoted handler design (primary M3 reference)
   - §10.1-10.2 unit + integration test plans relevant to M3
   - §11 acceptance invariants (the ship-when-true checklist)
   - §14 #2 open question: subscription pattern (resolve in M3)
   - §14 #4 open question: light-tier handler behavior

5. READ M2 PR #19 description for the upstream contract — what
   EmailPromoted events look like (Source="com.yourapp.transcription",
   DetailType="EmailPromoted", payload of {tenant_id, interaction_id,
   account_id, queue_id, promoted_at}).

6. Verify pre-flight state:
   - Confirm M1 + M2 PRs are MERGED or surface to user for merge
     coordination. M3 should not deploy until both upstream migrations
     are live.
   - `gh pr view 392 -R oneilstokeseqrm/eq-frontend --json state,mergedAt`
   - `gh pr view 19 -R oneilstokeseqrm/live-transcription-fastapi --json state,mergedAt`
   - `git -C /Users/peteroneil/eq-email-pipeline status` → should be clean on main.
   - `git -C /Users/peteroneil/eq-email-pipeline log --oneline -3` → top
     should be `033626a docs(plan): pending_interactions design for
     cold-inbound unknown-sender fix`.
   - Production health: `curl -sS -o /dev/null -w "%{http_code}\n"
     https://live-transcription-fastapi-production.up.railway.app/health`
     should return 200.
   - SHARED-TENANT-COLLISION CHECK (LOCKED-17): `ls -lt
     ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10`. Any
     file modified in last hour = pause + ask user. M3 is non-destructive
     so informational only.

7. After reading, briefly confirm in one paragraph: where the prior
   session left off + your M3 implementation approach for this session
   + that you understand this is EXECUTION (not plan revision).

8. EXECUTE M3 — eq-email-pipeline EmailPromoted subscriber.

   Open question to resolve first (plan §14 #2): inspect existing
   inbound webhook handlers in eq-email-pipeline. Determine the
   subscription pattern (SQS-from-EventBridge vs direct EventBridge).
   Document the choice in the M3 PR description.

   Implementation per plan §6.2:
   - cd /Users/peteroneil/eq-email-pipeline
   - Open a feature branch (suggested: phase-1-email-pipeline/m3-
     email-promoted-subscriber)
   - New helpers in src/persistence/postgres.py:
     * try_claim_local_enrichment(email_id) -> bool
       Atomic CAS: UPDATE emails SET local_enrichment_started_at = NOW()
       WHERE id = $id AND local_enrichment_completed_at IS NULL
         AND (local_enrichment_started_at IS NULL
              OR local_enrichment_started_at < NOW() - INTERVAL '5 minutes')
       RETURNING id;
       Returns True if claimed (RETURNING row), False otherwise.
     * mark_local_enrichment_completed(email_id) -> None
     * fetch_email_by_interaction_id(interaction_id) -> EmailRow
     * fetch_raw_interaction(interaction_id) -> RawInteractionRow
     * fetch_contacts_for_interaction(interaction_id) -> dict[email, contact_id]
   - New handler (location TBD per subscription pattern):
     handle_email_promoted(event: EmailPromotedEvent) -> None
     * Step 0 — two-layer idempotency guard:
       - Layer 1 (hard): early-return if local_enrichment_completed_at IS NOT NULL.
       - Layer 2 (soft TTL): atomic CAS via try_claim_local_enrichment;
         skip if claim lost.
     * Step 1-2: read raw_interactions + emails (thread_id already set by M2).
     * Step 3: fetch_contacts_for_interaction (subset; some participants
       may not have contacts if their queue isn't approved yet).
     * Step 4: branch on emails.processing_tier:
       - light → mark_local_enrichment_completed; return.
       - full → continue.
     * Step 5: Neo4j build_skeleton + write_flesh + LLM extract.
     * Step 6: Headline + summary on Neo4j Interaction ONLY (no Postgres
       column per plan §3.5).
     * Step 7: Pinecone embedding.
     * Step 8: Thread summary update (existing pattern; idempotent).
     * Step 9: mark_local_enrichment_completed LAST.
   - Unit tests for each new persistence helper.
   - Integration test for idempotency: synthetic EmailPromoted event
     with re-delivery; verify Neo4j has exactly one Interaction node,
     Pinecone has one vector, message_count unchanged.
   - Codex review BEFORE merge. Use `--base main` (M3 should be < 1500
     lines cumulative); `model_reasoning_effort=medium` per LOCKED-18.
   - Open M3 PR; surface to user for approval before merging.

   STOP after M3. Recommendation per plan: M4 + M5 are separate sessions
   because M4 flips the switch on cold-inbound capture and warrants its
   own pre-merge ritual + production canary.

9. PRE-MERGE RITUAL (per LOCKED-10 + LOCKED-18):
   - `codex review --base main -c 'model_reasoning_effort="medium"'
     --enable web_search_cached` for cumulative HEAD-vs-main review.
   - 4-round soft cap. Extend when real P1s keep surfacing (M2 went 7
     rounds with real findings through R6).
   - Recognize round-4 false-positive pattern on plan-related changes.

10. SHARED-INFRASTRUCTURE-COLLISION PROTOCOL (LOCKED-17):
    - Pre-flight ls of ~/.claude/projects/-Users-peteroneil-*/*.jsonl
      before any destructive op. M3 is non-destructive so this is
      informational only. Would matter if you reach M5 canary.

11. End-of-session: /context-save with title indicating M3 status.
    Rewrite NEXT-SESSION-START-HERE.md for M4 scope. Write a new dated
    next-session-prompt.md. Update MEMORY.md status string.

ANTI-ANCHORING — 18 LOCKED decisions exist; full list in
NEXT-SESSION-START-HERE.md. Most load-bearing for M3:

(7) Two hard rules — no contact without account anchor; no interaction
    without account anchor.
(10) Codex review BEFORE merging.
(14) Pending-interactions pattern.
(15) Lean+typed payload for pending_interactions (already implemented).
(16) Path B via EventBridge EmailPromoted (M3 is the subscriber side).
(17) Shared-tenant collision pre-flight.
(18) Codex multi-round defaults.

ACKNOWLEDGED V1 LIMITATIONS (documented in plan):

1. Personal/internal anchor cold-inbound → log+drop (V2: audit log).
2. Neo4j build_skeleton + write_flesh partial-retry corruption →
   mitigated by 2-layer guard in M3 handler (5-min soft TTL + hard
   completed marker). V2: MERGE patterns + edge-count thread counters.
3. NEW (M2 R6 deferred): legacy per-signal loop creates duplicate
   'meeting' summary for re-pointed email signals. Cosmetic only.

USER POSTURE (load-bearing)

Non-developer founder. Make confident technical decisions; surface only
product/strategic decisions. Strict OSS only. Cutting-edge 2026 AI-native
patterns. NO sunk-cost preservation.

Context economy matters. M2 took 7 Codex rounds (heavy context); M3
should be more contained (single-repo, smaller scope).

User explicitly approves git push when asked; do NOT push without
asking first.

SCOPE OF THIS SESSION — EXPLICIT HARD CONSTRAINT

In scope:
- M3 (eq-email-pipeline EmailPromoted subscriber)

OUT of scope:
- M4 (orchestrator branch + atomic upsert_thread rewrite) — separate session.
- M5 (production E2E + rollback drill) — separate session.
- M1 / M2 fixes (they're CLEAN at session end; if downstream review surfaces
  new findings, surface to user).

If you finish M3 fast with substantial context remaining, you CAN proceed
to M4 in the same session — but treat it as a deliberate decision, NOT an
automatic progression. M4 is the highest-risk milestone in the sequence.

STOP CONDITIONS (hard — surface to user)

- /context-restore returns NO_CHECKPOINTS.
- MEMORY.md status isn't PHASE_1_EMAIL_PIPELINE_M1_M2_SHIPPED_M3_NEXT.
- M1 or M2 PR has NOT merged + deployed before M3 deploys.
- The plan claims something about existing eq-email-pipeline code that
  doesn't match what M3 actually finds. STOP, surface, revise plan ONLY
  after user explicit approval.
- M3's Codex pre-merge review surfaces a P1 you can't resolve in one
  revision round (after recognizing round-4 false positives).
- You're tempted to revise the plan doc instead of surfacing a plan
  issue — STOP, surface the issue.

KEY REFERENCE PATHS

- THE PLAN: /Users/peteroneil/eq-email-pipeline/docs/superpowers/
  plans/2026-05-17-pending-interactions-cold-inbound-fix.md (033626a)
- Wayfinding: /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/
  superpowers/specs/NEXT-SESSION-START-HERE.md
- M1 PR: https://github.com/oneilstokeseqrm/eq-frontend/pull/392
- M2 PR: https://github.com/oneilstokeseqrm/live-transcription-fastapi/pull/19
- M2's emit step: live-transcription-fastapi/services/account_provisioning/
  eventbridge_emit.py:emit_email_promoted_for_materialization (contract for M3)
- Checkpoint: ~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/
  checkpoints/20260517-164251-phase-1-email-pipeline-m1-m2-shipped-m3-next.md
- Auto-memory: ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-
  transcription-fastapi/memory/
- MEMORY.md: same dir, MEMORY.md

eq-email-pipeline source paths M3 touches:
- src/persistence/postgres.py (new helpers)
- TBD per subscription pattern (subscriber handler location)
- tests/ (unit + integration)
- docs/architecture.md (if updated for new helpers)

PRODUCTION CREDENTIALS + IDS (self-contained reference)

- Neon Postgres (eq-dev): project super-glitter-11265514, branch
  production, database neondb.
- Test tenant: 11111111-1111-4111-8111-111111111111.
- Railway eq-email-pipeline: TBD service ID (check eq-email-pipeline's
  Railway dashboard).
- AWS: EventBridge bus 'default', AWS_REGION=us-east-1. EmailPromoted
  rule MUST be configured before M2 prod deploy (operator task).
- Neo4j: Aura c6171c63, URI neo4j+s://c6171c63.databases.neo4j.io.

The plan is the load-bearing artifact. M2's emit step is the upstream
contract. Start with /context-restore. Read the plan §6 next. Then
execute M3.
```

---

## Notes for the user pasting this

Key changes vs the 2026-05-17 PM prompt:

- **Status string is `PHASE_1_EMAIL_PIPELINE_M1_M2_SHIPPED_M3_NEXT`.**
- **Scope is M3 only**, single-repo (eq-email-pipeline).
- M1+M2 PRs are open at session end — confirm merge coordination BEFORE M3 deploys.
- M1↔M2 deploy ordering is critical: M1 then M2 with brief window of risk.
- Session lessons codified: cross-repo deploy coordination is non-optional for constraint-relaxation changes; Codex round-N convergence pattern (severity-decrease + non-redundant findings → extend past 4-round soft cap).
- Concrete source paths in eq-email-pipeline for M3 file targets.
