# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-17 evening (M1 + M2 of the Phase-1-email-pipeline cold-inbound fix shipped as PRs; 10 Codex review rounds total across both; 14 substantive findings resolved).
**Status:** ✅ **PHASE_1_EMAIL_PIPELINE_M1_M2_SHIPPED_M3_NEXT** — Both PRs open and unmerged. M3 (eq-email-pipeline EmailPromoted subscriber) is the next milestone, in a DIFFERENT repo with no overlap with this session's edited files.

---

## SESSION SCOPE FOR THE NEXT SESSION

**This session is EXECUTION of M3.** Implementation only, NOT plan revision. The plan at `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` (v4, committed as eq-email-pipeline:`033626a`) is the load-bearing artifact. Read §6 (EmailPromoted handler design) before any code.

Recommended scope: **M3 alone**. M4 (orchestrator branch + atomic `upsert_thread` rewrite) FLIPS THE SWITCH on cold-inbound capture and warrants its own session per the plan. The user previously chose to stop at M3 to preserve context budget.

**Before any M3 code work**: confirm M1 + M2 PRs are merged (or coordinate the merge — see "Pre-flight" below). M3's handler reads schema (`local_enrichment_*_at` columns) that only exists post-M1 deploy.

---

## CRITICAL — multi-session, multi-repo, long-arc project

| Milestone | Status | PR |
|---|---|---|
| Phase 1 — account-anchor contract end-to-end | ✅ Shipped 2026-05-14 | PR #10/#11 |
| M0-M2 (DBOS + Prisma) — Phase 1.5 | ✅ Shipped 2026-05-15 | PR #14/#15 + eq-frontend PR #373 |
| M3 + M4 — workflow + /approve cutover (DBOS) | ✅ Shipped 2026-05-17 AM | PR #17 |
| M5 — verified-contract tooling | ✅ Shipped 2026-05-17 PM | PR #18 |
| **Phase-1-email-pipeline M1** | ✅ Open as eq-frontend PR #392 (3 Codex rounds, CLEAN) | https://github.com/oneilstokeseqrm/eq-frontend/pull/392 |
| **Phase-1-email-pipeline M2** | ✅ Open as PR #19 (7 Codex rounds, CLEAN) | https://github.com/oneilstokeseqrm/live-transcription-fastapi/pull/19 |
| **Phase-1-email-pipeline M3** | ⏳ Next: EmailPromoted subscriber in eq-email-pipeline | TBD |
| Phase-1-email-pipeline M4 | ⏸ Future session: orchestrator branch + atomic upsert_thread (FLIPS THE SWITCH) | TBD |
| Phase-1-email-pipeline M5 | ⏸ Future session: production E2E + rollback drill | TBD |

---

## CRITICAL — M1↔M2 deploy coordination

The plan §7 claimed M1 was independently deployable. **That's false.** M1 drops the single-column UNIQUE on `interaction_summaries.interaction_id`; M2's `UPSERT_PLACEHOLDER_SUMMARY_SQL` switches the existing `ON CONFLICT (interaction_id)` to the new composite `(tenant_id, interaction_id, summary_type)`. Once M1 deploys (Vercel runs `prisma migrate deploy`), **every meeting approval breaks at runtime** until M2 deploys.

**Deploy sequence the user must execute:**
1. Merge M1 (eq-frontend PR #392).
2. Wait for Vercel `prisma migrate deploy` to complete (~2-3 min).
3. Merge M2 (live-transcription-fastapi PR #19).
4. Wait for Railway deploy to complete (~2-3 min).

Window of risk between steps 2 and 3 (~3-5 min total): any meeting approval would fail. Acceptable because **all data is test data, no production users**.

**M3 must wait until BOTH deploys complete.** M3's handler reads `emails.local_enrichment_started_at` and `emails.local_enrichment_completed_at` — those columns only exist post-M1 deploy. M3 also gets EmailPromoted events emitted from M2's workflow — those events only fire post-M2 deploy (and only after M4 starts populating pending_interactions). So:

- Pre-M1+M2 deploys: M3 work CAN proceed locally (code + tests), but cannot deploy or live-test.
- Post-M1+M2 deploys: M3 can deploy safely (no `EmailPromoted` events being emitted until M4 ships, so handler is dormant).

---

## Mandatory read order for the next session (~20 min)

1. **This file.**
2. **The checkpoint** loaded via `/context-restore` (the comprehensive 2026-05-17 evening save titled `phase-1-email-pipeline-m1-m2-shipped-m3-next`).
3. **`/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md`** — THE plan. Especially:
   - §6 (handler design) — primary M3 reference.
   - §10 (test plan) — relevant cases.
   - §11 (acceptance invariants) — the ship-when-true checklist.
   - §14 #2 (open question: subscription pattern).
4. **PR #19** in live-transcription-fastapi for the upstream contract — what `EmailPromoted` events look like (Source, DetailType, payload).
5. Quick code scan: `eq-email-pipeline/src/persistence/postgres.py` (existing helper conventions), `eq-email-pipeline/src/pipeline/orchestrator.py` (existing async patterns), `eq-email-pipeline/src/pipeline/skeleton.py:186` + `flesh.py:173` (the non-idempotent Neo4j writes the two-layer guard bounds).

---

## Execution sequence — M3

Per plan §6 + §12.

### Pre-flight

1. Confirm M1 + M2 PRs merged + deployed (or coordinate merge per the section above).
2. Verify production health: `curl -sS -o /dev/null -w "%{http_code}\n" https://live-transcription-fastapi-production.up.railway.app/health` returns 200.
3. SHARED-TENANT-COLLISION CHECK (LOCKED-17): `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10`. Any file modified in last hour = concurrent agent hazard. Non-destructive M3 scope tolerates this (informational), but flag if you see concurrent eq-email-pipeline work.
4. Confirm production Neon has new pending_interactions table + emails columns: `SELECT column_name FROM information_schema.columns WHERE table_name='emails' AND column_name LIKE 'local_enrichment%';` should return 2 rows.

### M3 — EmailPromoted subscriber (2-3 days, medium risk)

**Open question to resolve first** (plan §14 #2): does eq-email-pipeline use SQS-from-EventBridge or direct EventBridge subscription? Inspect existing inbound webhook handlers; check for any existing SQS subscriber. Document the choice in the M3 PR.

**Handler implementation per plan §6.2**:
- Step 0 — two-layer idempotency guard:
  - Layer 1 (hard): early-return if `emails.local_enrichment_completed_at IS NOT NULL`.
  - Layer 2 (soft TTL): atomic CAS via `try_claim_local_enrichment` (`UPDATE emails SET local_enrichment_started_at = NOW() WHERE id = $id AND completed_at IS NULL AND (started_at IS NULL OR started_at < NOW() - INTERVAL '5 minutes') RETURNING id`). If RETURNING is empty, another instance is processing → skip.
- Step 1-2: read `raw_interactions.raw_text` + `emails` row (thread_id already set by M2's Step 4c).
- Step 3: `fetch_contacts_for_interaction` — resolve subset of known participants from `interaction_contact_links` JOIN `contacts`. Subset because participants on still-pending queues won't have contacts yet.
- Step 4: branch on `emails.processing_tier`:
  - `light` → skip LLM/Neo4j/Pinecone, just `mark_local_enrichment_completed`. Return.
  - `full` → run full pipeline.
- Step 5: Neo4j `build_skeleton` + `write_flesh` (plus LLM extraction).
- Step 6: Headline + summary on Neo4j `Interaction` node ONLY (no Postgres column; plan §3.5).
- Step 7: Pinecone embedding.
- Step 8: Thread summary update (existing pattern; UPDATE is idempotent).
- Step 9: `mark_local_enrichment_completed` LAST (after all writes succeed).

**New helpers in `src/persistence/postgres.py`**:
- `try_claim_local_enrichment(email_id) -> bool` — atomic CAS, returns True if claimed.
- `mark_local_enrichment_completed(email_id) -> None`.
- `fetch_email_by_interaction_id(interaction_id) -> EmailRow`.
- `fetch_raw_interaction(interaction_id) -> RawInteractionRow`.
- `fetch_contacts_for_interaction(interaction_id) -> dict[email, contact_id]`.

**Tests**:
- Unit: each new helper.
- Integration: synthetic EmailPromoted emission with re-delivery (idempotency); light tier no-op; full tier all writes.

**Codex review BEFORE merge** per LOCKED-10. `model_reasoning_effort=medium` per LOCKED-18. Use `--base main` for cumulative diff review (M3 should be < 1500 lines).

**Open M3 PR**; surface to user for approval before merge.

### M4 + M5 — DEFERRED to separate session

Per plan §12: M4 flips the switch on cold-inbound capture; M5 verifies end-to-end. Both warrant their own pre-merge ritual + production canary. Do NOT continue past M3 in this session unless context budget is genuinely generous + user explicitly approves.

---

## LOCKED decisions (18 total; do NOT re-litigate)

1. DBOS substrate.
2. Single Railway replica + `executor_id=RAILWAY_REPLICA_ID`.
3. EventBridge Path A with `source="com.yourapp.transcription"` and closed `INTERACTION_TYPE_TO_DETAIL_TYPE` lookup.
4. Workflow ID = `f"queue-{queue_id}:approval-{approval_attempt_id}"`.
5. `/approve` reserves synchronously then enqueues.
6. Option B test infrastructure (test-tenant scoping in prod Neon).
7. **Two hard rules** — no contact / no interaction without account anchor.
8. SQLAlchemy 2.0.49 `CAST(:name AS uuid)` form.
9. Materialization REQUIRES Lane 2 raw_interactions before materializing.
10. Codex review BEFORE merging (4-round soft cap; extendable when real P1s keep surfacing — M2 ran 7 rounds with real findings through R6).
11. Per-batch user confirmation for destructive ops on shared test tenant.
12. Transcripts: frontend forces anchor; emails: backend handles via pending state.
13. Recipient-as-anchor REJECTED for emails.
14. Pending-interactions pattern (Approach C).
15. Lean payload + typed columns for pending_interactions schema.
16. Path B full reprocess on promote via EventBridge `EmailPromoted` event.
17. Shared-tenant collision protocol: pre-flight `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl`.
18. Codex multi-round: `--commit HEAD` past ~1500 lines; `model_reasoning_effort=medium` default.

---

## Acknowledged V1 limitations (NOT regressions; documented + bounded)

1. **Personal/internal anchor cold-inbound → log+drop.** V2: audit log table.
2. **Neo4j build_skeleton + write_flesh partial-retry corruption.** Mitigation: 2-layer guard (atomic CAS + 5-min soft TTL). V2: MERGE patterns + edge-count thread counters.
3. **`upsert_thread` known race** (pre-existing) FIXED in M2 for the workflow promote path; M4 will fix it in eq-email-pipeline's orchestrator known-account path too.
4. **NEW (M2 R6 deferred)**: legacy per-signal loop hardcodes `summary_type='meeting'`. For re-pointed email signals (round-4 fix) it creates a duplicate 'meeting' summary alongside the existing 'email' summary. Cosmetic data inconsistency, NOT functionally broken. Future cleanup: type-aware legacy loop.

---

## Production credentials + IDs (load-bearing reference)

- **Neon Postgres (eq-dev):** project `super-glitter-11265514`, branch `production`, database `neondb`.
- **Test tenant:** `11111111-1111-4111-8111-111111111111`. All test data.
- **Test user:** `b0000000-0000-4000-8000-000000000002`.
- **Railway FastAPI:** project `847cfa5a-b77c-4fb0-95e4-b20e8773c23e`, service `59a69f3d-9a24-4041-942a-891c4a81c5fb`, env `e4c5ec15-1931-4632-9e58-92d9c6be4261`, URL `https://live-transcription-fastapi-production.up.railway.app`.
- **eq-email-pipeline:** `/Users/peteroneil/eq-email-pipeline` (NOT under EQ-CORE/). Main HEAD `033626a` as of 2026-05-17.
- **eq-frontend:** `/Users/peteroneil/eq-frontend`. M1 branch `phase-1-email-pipeline/m1-pending-interactions` pushed; PR #392.
- **AWS:** EventBridge bus `default` (configurable via `EVENTBRIDGE_BUS_NAME`); `AWS_REGION=us-east-1`. EmailPromoted rule must be configured before M2 deploys.
- **Neo4j:** Aura `c6171c63`, URI `neo4j+s://c6171c63.databases.neo4j.io`.

---

## Open questions deferred to execution

1. **eq-email-pipeline EventBridge subscription pattern** (plan §14 #2) — confirm during M3 implementation whether the repo uses SQS-from-EventBridge or direct EventBridge.
2. **Light tier handler behavior** (plan §14 #4) — confirm whether light-tier emails write any summaries today. If no, handler is a complete no-op for light tier.
3. **EmailPromoted DLQ + observability** (plan §14 #5) — operations setup, separate from plan.
4. **Backfill of historical dropped emails** (plan §14 #6) — confirm in M5 that no backfill needed.
5. **Queue UI integration** (plan §14 #7) — defer to eq-frontend session.

---

## Stop conditions (hard — surface to user)

- M1 or M2 PR has NOT merged + deployed when M3 starts implementing the schema-dependent helpers.
- The plan claims something about existing eq-email-pipeline code that doesn't match what M3 actually finds. STOP, surface, revise plan ONLY after user explicit approval.
- M3's Codex pre-merge review surfaces a P1 you can't resolve in one revision round (after the round-4 false-positive recognition heuristic).
- LOCKED-17 collision check shows a concurrent agent in another repo within the last hour AND the work is destructive (M3 alone is non-destructive; would matter for M5 canary).
- You're tempted to revise the plan doc instead of surfacing a plan issue — STOP, surface the issue.

---

## Handoff artifacts from this session

- `eq-frontend` M1 PR #392: https://github.com/oneilstokeseqrm/eq-frontend/pull/392 (3 Codex rounds, CLEAN at R3).
- `live-transcription-fastapi` M2 PR #19: https://github.com/oneilstokeseqrm/live-transcription-fastapi/pull/19 (7 Codex rounds, CLEAN at R7).
- **Comprehensive checkpoint**: `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/20260517-164251-phase-1-email-pipeline-m1-m2-shipped-m3-next.md`.
- **The plan (unchanged)**: `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` (eq-email-pipeline:033626a).
- **Next-session prompt** (paste-ready): `docs/superpowers/specs/2026-05-17-evening-m3-next-session-prompt.md`.

---

## Session lessons (codify into tasks/lessons.md as part of M3 PR or later)

1. **Cross-repo deploy coordination** is non-optional when a schema migration relaxes a constraint that downstream `ON CONFLICT` clauses reference. The plan-writing session can miss this because it focuses on the new schema, not on existing SQL depending on the old schema. **Lesson**: before locking a plan that drops or relaxes a UNIQUE constraint, GREP all repos for `ON CONFLICT (<constraint cols>)` SQL referencing it.

2. **Codex round-N convergence pattern**: when findings remain non-redundant + decrease in severity across rounds (P1→P2→0), the design is converging and rounds 5-7 are still valuable. The 4-round soft cap is a default; extending is justified when severity is decreasing AND each round adds NEW unique findings (M2 hit this pattern — 11 substantive findings, 0 redundant, R7 CLEAN).
