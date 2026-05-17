# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-17 evening (plan v4 locked for cold-inbound unknown-sender fix; 4 Codex rounds; 11 findings resolved).
**Status:** ✅ **PHASE_1_EMAIL_PIPELINE_PLAN_LOCKED_EXECUTION_NEXT** — Plan ready for M1-M5 execution.

---

## SESSION SCOPE FOR THE NEXT SESSION

**This session is EXECUTION.** Plan-writing is complete. The implementation plan at `eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` (1207 lines, v4, LOCKED after 4 Codex rounds) is the load-bearing artifact. Read it before any code.

Execute M1-M5 in order. Each milestone is independently deployable and (where applicable) reversible. Plan to compress M1+M2+M3 into one execution session (they're independently safe deploys); split M4+M5 into a second execution session (M4 flips the switch and warrants its own pre-merge ritual + production canary).

---

## CRITICAL — multi-session, multi-repo, long-arc project

Phase 1 + Phase 1.5 main code are SHIPPED. The email-pipeline gap is **finishing committed Phase 1 work** (Task 1.24 acceptance criteria), NOT new scope.

| Milestone | Status | PR |
|---|---|---|
| Phase 1 — account-anchor contract end-to-end | ✅ Shipped 2026-05-14 | PR #10/#11 |
| M0 — Railway operational prep | ✅ Shipped 2026-05-15 | (Railway-side) |
| M1 — DBOS install | ✅ Shipped 2026-05-15 | PR #14 |
| M1 hotfix — Codex P1s | ✅ Shipped 2026-05-15 | PR #15 |
| M2 — Prisma UNIQUE INDEX | ✅ Shipped 2026-05-15 | eq-frontend PR #373 |
| M3 + M4 — workflow + /approve cutover | ✅ Shipped 2026-05-17 AM | PR #17 |
| M5 — verified-contract tooling | ✅ Shipped 2026-05-17 PM | PR #18 |
| **Phase-1-email-pipeline plan** | ✅ **Locked 2026-05-17 evening** | (no PR yet — design doc only) |
| **Phase-1-email-pipeline M1** | ⏳ Next: eq-frontend Prisma migration | TBD |
| **Phase-1-email-pipeline M2** | ⏳ Next: workflow promote-step + emit | TBD |
| **Phase-1-email-pipeline M3** | ⏳ Next: EmailPromoted subscriber | TBD |
| **Phase-1-email-pipeline M4** | ⏳ Next: orchestrator branch + atomic upsert_thread (FLIPS THE SWITCH) | TBD |
| **Phase-1-email-pipeline M5** | ⏳ Next: production E2E + rollback drill | TBD |
| M3.5 — outbox table drop | ⏸ Optional, deferred | TBD |

---

## Mandatory read order for the next session (~30 min)

1. **This file.**
2. **`eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md`** — THE plan (1207 lines). Sections 1-12 cover the full design; §13 lists LOCKED decisions; §14 lists deferred-to-execution open questions.
3. **The checkpoint** loaded via `/context-restore` (full record of the plan-writing session + 4 Codex rounds + decision history).
4. **`live-transcription-fastapi/docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md`** — §3 hard rules, §5.4 queue lifecycle, §6 account state, §9 phased trajectory.
5. **`live-transcription-fastapi/docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md`** — §6 the workflow, §6.6 emit step (the existing pattern M2 extends), §6.8 step-order discipline.
6. Quick code scan: `eq-email-pipeline/src/persistence/postgres.py:180-450` (insert_email, upsert_thread, email_exists, link table inserts) — verify the plan's claims about current code shape before changing.

---

## Execution sequence — M1 → M5

Per the plan §12. Each milestone has its own §10.1 unit tests + §10.2 integration tests.

### M1 — eq-frontend Prisma migration (1-2 days, low risk)
- Add `pending_interactions` model (plan §3.1: lean payload + typed columns + UNIQUE on `(tenant_id, internet_message_id)`).
- Add `account_provisioning_queue_id` + `local_enrichment_started_at` + `local_enrichment_completed_at` columns on `Email` model.
- Add UNIQUE constraint on `interaction_summaries.(tenant_id, interaction_id, summary_type)` (verify whether already present).
- Confirm `email_threads.(tenant_id, thread_key)` UNIQUE already exists (it does — `architecture.md:854`).
- Codex review BEFORE merge (LOCKED-10). Run `verify_schema.py` against new columns.
- Single PR in eq-frontend. Safe to deploy independently (table empty).

### M2 — live-transcription-fastapi workflow + EmailPromoted emit (3-4 days, medium-high risk)
- Extend `materialize_account_approval` with Step 4 (4a-4e per plan §5.2): pre-filter duplicates already in emails; insert raw_interactions + emails + interaction_summaries + thread upsert per pending row + archive pending.
- Revise Step 5 link phase to filter by `summary_type='email'` and handle cross-queue cold-inbound (OR clause on just-promoted interaction_ids).
- Add `emit_email_promoted_events` step at END of `account_provisioning_workflow` (safe per §6.8).
- Add `MaterializationResult.promoted_interaction_ids` field.
- Update `verify_consumer_contracts.py` to recognize `EmailPromoted` detail-type.
- Configure EventBridge rule for `Source="com.yourapp.transcription" + DetailType="EmailPromoted"`.
- Unit tests per plan §10.1. Codex review BEFORE merge.
- Single PR. Safe to deploy (no pending rows exist yet; new code is a no-op until M4 ships).

### M3 — eq-email-pipeline EmailPromoted subscriber (2-3 days, medium risk)
- Confirm during M3 whether eq-email-pipeline uses SQS-from-EventBridge or direct EventBridge subscription. Pattern dictates implementation.
- Implement handler per plan §6.2: two-layer idempotency guard (`try_claim_local_enrichment` atomic CAS at start + `mark_local_enrichment_completed` at end); branch on `processing_tier`; full pipeline runs only for `full` tier.
- Unit tests per plan §10.1. Codex review BEFORE merge.
- Single PR. Safe (no `EmailPromoted` events emitting until M4 ships).

### M4 — eq-email-pipeline orchestrator branch + atomic upsert_thread (4-5 days, medium-high risk — FLIPS THE SWITCH)
- Extend `email_exists` to UNION emails + pending_interactions.
- Add `persist_pending_interaction` + helper functions (`fetch_email_by_interaction_id`, `fetch_raw_interaction`, `fetch_contacts_for_interaction`) to `src/persistence/postgres.py`.
- **Rewrite `upsert_thread`** to atomic `INSERT...ON CONFLICT (tenant_id, thread_key) DO UPDATE` — closes the pre-existing SELECT-then-UPSERT race. Verify all existing callers still behave correctly after the rewrite.
- Add §4.1 decision branch in `orchestrator.py:174-196` (BUSINESS → pending; PERSONAL/INTERNAL → log+drop).
- Pre-allocate `interaction_id` at top of `process_email` (plan §4.3).
- Update `tests/test_orchestrator_three_state.py` with plan §10.2 cases.
- Codex review BEFORE merge. Single PR. **Flips the switch on cold-inbound capture.**

### M5 — Production E2E + rollback drill (1-2 days)
- Run plan §10.3 E2E under test tenant `11111111-1111-4111-8111-111111111111`. **LOCKED-17 Layer-1 check first** (`ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10`; any file modified in last hour = pause + ask).
- Verify all §11 acceptance invariants.
- Optionally exercise §10.4 rollback drill (Phase 4 only).

---

## LOCKED decisions (18 total; do NOT re-litigate)

1. DBOS substrate.
2. Single Railway replica + `executor_id=RAILWAY_REPLICA_ID`.
3. EventBridge Path A with `source="com.yourapp.transcription"` and closed `INTERACTION_TYPE_TO_DETAIL_TYPE` lookup.
4. Workflow ID = `f"queue-{queue_id}:approval-{approval_attempt_id}"`.
5. `/approve` reserves synchronously then enqueues.
6. Option B test infrastructure (test-tenant scoping in prod Neon) + `@pytest.mark.requires_db_write` + `RUN_DESTRUCTIVE_TESTS=1`.
7. **Two hard rules** — no contact / no interaction without account anchor. Approach C respects both.
8. SQLAlchemy 2.0.49 `CAST(:name AS uuid)` form.
9. Materialization REQUIRES Lane 2 raw_interactions before materializing. No placeholders.
10. Codex review BEFORE merging (4-round soft cap; extendable when real P1s keep surfacing).
11. PER-BATCH user confirmation for destructive ops on shared test tenant.
12. Transcripts: frontend forces anchor selection; 400 reject. Emails: backend handles no-anchor via pending state.
13. Recipient-as-anchor REJECTED for emails — misattribution.
14. Pending-interactions pattern (Approach C) — this plan.
15. **NEW — Lean payload + typed columns for pending_interactions schema** (NOT JSONB blob, NOT full mirror).
16. **NEW — Path B full reprocess on promote via EventBridge `EmailPromoted` event** (NOT new sync HTTP API).
17. Shared-tenant collision protocol: pre-flight `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl` before destructive ops.
18. Codex multi-round reviews: `--commit HEAD` past ~1500 lines; `model_reasoning_effort=medium` default.

---

## Acknowledged V1 limitations (NOT regressions; documented + bounded)

1. **Personal/internal anchor cold-inbound → log+drop.** Reason: queue is for unknown businesses to become accounts; personal/internal don't fit that model. V2 roadmap: audit log table.
2. **Neo4j build_skeleton + write_flesh partial-retry corruption.** Mitigation: 2-layer guard (atomic CAS + 5-minute soft TTL); rare scenarios (handler hangs > 5 min then retries). V2 roadmap: convert to MERGE patterns + edge-count thread counters.
3. **`upsert_thread` known race** (pre-existing) is fixed in M4 (atomic INSERT...ON CONFLICT DO UPDATE).

---

## Production credentials + IDs (load-bearing reference)

- **Neon Postgres (eq-dev):** project `super-glitter-11265514`, branch `production`, database `neondb`. Direct connection (no `-pooler`) for `DBOS_SYSTEM_DATABASE_URL`.
- **Test tenant:** `11111111-1111-4111-8111-111111111111`. All test data. Per LOCKED-11.
- **Test user:** `b0000000-0000-4000-8000-000000000002`.
- **Railway FastAPI:** project `847cfa5a-b77c-4fb0-95e4-b20e8773c23e`, service `59a69f3d-9a24-4041-942a-891c4a81c5fb`, env `e4c5ec15-1931-4632-9e58-92d9c6be4261`, URL `https://live-transcription-fastapi-production.up.railway.app`.
- **Railway eq-agent-action-core:** URL `https://eq-agent-action-core-production.up.railway.app`, service `3036ea0f-afc9-4bc4-889d-c98617d81e96`.
- **eq-email-pipeline:** `/Users/peteroneil/eq-email-pipeline` (NOT under EQ-CORE/). Main HEAD `084567a` as of 2026-05-17.
- **eq-frontend:** `/Users/peteroneil/eq-frontend`. Owns the Prisma schema for M1.
- **Internal JWT:** HS256, `INTERNAL_JWT_SECRET`, `iss=eq-frontend`, `aud=eq-backend`.
- **AWS:** EventBridge bus `default` (configurable via `EVENTBRIDGE_BUS_NAME`); `AWS_REGION=us-east-1`.
- **Neo4j:** Aura `c6171c63`, URI `neo4j+s://c6171c63.databases.neo4j.io`.

---

## Open questions deferred to execution (from plan §14)

1. `email_exists` extension exact SQL — verify column types/collations match between emails and pending_interactions.
2. eq-email-pipeline EventBridge subscription pattern — confirm during M3.
3. `interaction_summaries.(tenant_id, interaction_id, summary_type)` UNIQUE — verify in M1 pre-flight whether already present.
4. Light tier handler behavior — confirm whether light-tier writes any summaries today.
5. EmailPromoted DLQ + observability — operations setup, separate from plan.
6. Backfill of historical dropped emails — confirm in M5 that no backfill needed (test data only).
7. Queue UI integration in `app/(workspace)/agent-queue` — defer to separate eq-frontend session.

---

## Stop conditions (hard — surface to user)

- The plan claims something about existing code that doesn't match what M1-M4 actually find. Stop and re-validate before committing.
- M1's Prisma migration fails review (Codex round 1 finds real P0/P1).
- M2's workflow change breaks an existing test in `tests/services/account_provisioning/`.
- M3 confirms eq-email-pipeline uses a subscription pattern that the plan didn't anticipate (e.g., not SQS-from-EventBridge).
- M4's `upsert_thread` rewrite breaks the orchestrator's known-account path.
- M5 E2E surfaces a finding the plan didn't predict.
- LOCKED-17 collision check shows a concurrent agent in another repo within the last hour and the work is destructive.

---

## Handoff artifacts from this session

- `eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` — THE plan (UNCOMMITTED; recommend committing as part of M1 or as a standalone "design plan" commit).
- `live-transcription-fastapi/docs/superpowers/specs/NEXT-SESSION-START-HERE.md` — this file.
- `live-transcription-fastapi/docs/superpowers/specs/2026-05-17-evening-execution-next-session-prompt.md` — paste-ready opening prompt for the execution session.
- Checkpoint: `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/20260517-143526-phase-1-email-pipeline-plan-locked-v4-execution-next.md`.

---

## The plan-writing session in 3 bullets

- 4 Codex rounds (R1=5P0+4P1+5P2+1P3, R2=1P0+3P1+1P2, R3=0P0+2P1+1P2+2P3, R4=0P0+0-real-P1 false positives). 11 substantive findings resolved across rounds 1-3.
- 2 new LOCKED decisions added: (15) lean+typed columns; (16) Path B EventBridge EmailPromoted.
- Code paid for itself: M5's `verify_schema.py` + `verify_consumer_contracts.py` caught the `interaction_summaries` Prisma naming-drift landmine (link tables use `summary_id`, NOT `interaction_id`, despite the column NAME being `interaction_id`) at design time — exactly the bug class M5 was built to prevent.

The plan is the load-bearing artifact. Read it before code.
