# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-18 (M4 of the Phase-1-email-pipeline cold-inbound fix shipped, merged, AND deployed to production; orchestrator branches cold-inbound from unknown business → `pending_interactions`; atomic `upsert_thread` rewrite closes the SELECT-then-UPSERT race; 2 Codex review rounds with R2 CLEAN).
**Status:** ✅ **PHASE_1_EMAIL_PIPELINE_M1_M2_M3_M4_DEPLOYED_M5_NEXT** — the end-to-end cold-inbound pipeline is **BUILT, DEPLOYED, AND LIVE.** The very next cold-inbound from an unknown business sender to a connected mailbox WILL trigger: `orchestrator §4.2 → pending_interactions row + queue entry + signals → admin /approve → workflow promotes → EmailPromoted fires → M3 subscriber runs Neo4j + Pinecone + summary enrichment.` M5 (production E2E + rollback drill) is the verification milestone that signs off the whole Phase-1-email-pipeline initiative.

---

## SESSION SCOPE FOR THE NEXT SESSION

**This session is M5 — production E2E + rollback drill per plan §10.3 + §10.4 + §11 acceptance verification.** The work is verification + lock-in, not new code (unless an E2E reveals a bug, in which case scope expands to that fix).

Recommended scope: **M5 alone.** No new feature work beyond the verification milestone. If E2E surfaces a real bug, scope expands to fix + re-verify; if E2E surfaces a known limitation (5 acknowledged V1 limitations), document and accept.

---

## CRITICAL — what already shipped + verified deployed

| Milestone | Shipped | PR | Merge SHA | Deploy verification |
|---|---|---|---|---|
| Phase 1 — account-anchor contract end-to-end | ✅ 2026-05-14 | PR #10/#11 | (legacy) | (legacy) |
| M0-M2 (DBOS + Prisma) — Phase 1.5 | ✅ 2026-05-15 | PR #14/#15 + eq-frontend PR #373 | (legacy) | (legacy) |
| M3 + M4 — workflow + /approve cutover (DBOS) | ✅ 2026-05-17 AM | PR #17 | `ae45737` | (legacy) |
| M5 — verified-contract tooling | ✅ 2026-05-17 PM | PR #18 | `95f9084` | (legacy) |
| Phase-1-email-pipeline M1 | ✅ 2026-05-17 evening | eq-frontend PR #392 | **`de586bbc`** | Vercel: Prisma migrate deploy applied; Neon schema verified |
| Phase-1-email-pipeline M2 | ✅ 2026-05-17 evening | live-transcription-fastapi PR #19 | **`756575d7`** | Railway deployment `809679fc` SUCCESS; /health 200 |
| Phase-1-email-pipeline M3 | ✅ 2026-05-18 morning | eq-email-pipeline PR #9 | **`85c0295`** | Railway deployment `5c013fd3` SUCCESS; /api/health 200; subscriber long-polling SQS |
| **Phase-1-email-pipeline M4** | **✅ 2026-05-18 evening** | eq-email-pipeline PR #10 | **`6fa181a`** | **Railway deployment `756b96e4` SUCCESS; /api/health 200 with all 3 checks ok; switch FLIPPED on cold-inbound capture** |
| **Phase-1-email-pipeline M5** | ⏳ **NEXT (this session)** | — | — | — |

### Production state verified end-of-prior-session (2026-05-18)

- **Neon Postgres (eq-dev, super-glitter-11265514)**: M1 schema applied. `pending_interactions` table exists. `emails` has `account_provisioning_queue_id`, `local_enrichment_started_at`, `local_enrichment_completed_at`. `interaction_summaries_tenant_id_interaction_id_summary_type_key` UNIQUE exists; old single-column index GONE. Composite FK `interaction_summaries_tenant_id_interaction_id_fkey` exists. `raw_interactions_tenant_id_interaction_id_key` UNIQUE exists. `email_threads_tenant_id_thread_key_key` UNIQUE exists (required for M4 atomic upsert_thread ON CONFLICT inference).
- **Railway live-transcription-fastapi**: M2 code at `756575d7`; `/health` 200.
- **Railway eq-email-pipeline**: M4 code at `6fa181a`; deployment `756b96e4` SUCCESS; `/api/ping` 200; `/api/health` 200 with postgres + neo4j + eventbridge all ok.
- **EMAIL_PROMOTED_QUEUE_URL** set on Railway eq-email-pipeline production env → subscriber's `run_polling()` is active.
- **AWS resources (account 211125681610, region us-east-1)**: SQS `eq-email-promoted-queue` (300s VT, 14d retention, redrive to DLQ after 5); SQS DLQ; queue policy allowing `events.amazonaws.com SendMessage` from rule; EventBridge rule `route-email-promoted-to-sqs` (Source `com.yourapp.transcription`, DetailType `EmailPromoted`) → SQS target; IAM inline policy `SQSEmailPromotedReader` on `eq-bff-kinesis-writer`.
- **End-to-end wire test PASSED** during M3 setup; **end-to-end behavior** awaits the first real cold-inbound (which M5 synthesizes).

### What M5 verifies

M5 is the empirical verification that the whole Phase-1-email-pipeline initiative works end-to-end on real data. The 12-step E2E (plan §10.3) walks through:

1. Synthesize cold-inbound email from `test-prospect-{uuid}@cold-prospect-{uuid}.com` to test user. Non-trivial body. processing_tier=full.
2. POST to eq-email-pipeline synthetic-injection endpoint.
3. Verify pending state (3a-3f):
   - `pending_interactions` row exists for the from_email.
   - `pending_account_mappings` row exists, status='pending'.
   - `pending_account_mapping_signals` rows exist, interaction_id matches.
   - NO `raw_interactions`, NO `emails`, NO `interaction_summaries` for the interaction_id.
4. Test duplicate webhook before approval: re-POST. Verify 1 pending row.
5. POST `/approve` with queue_id + approval_attempt_id.
6. Poll `dbos.workflow_status` until status='success'.
7. Verify promote (7a-7g):
   - `accounts` row exists, AI-researched.
   - `pending_interactions` archived with `archive_reason='promoted'`.
   - `raw_interactions` row exists, account_id=resolved.
   - `emails` row exists, account_id=resolved, account_provisioning_queue_id=queue_id, thread_id not null.
   - `interaction_summaries` row exists.
   - `interaction_contact_links` rows exist.
   - `email_threads.message_count` = 1 (incremented exactly once).
8. Wait for EmailPromoted handler to complete (poll on `emails.local_enrichment_completed_at IS NOT NULL`).
9. Verify enrichment (9a-9d):
   - Neo4j `Interaction-[:BELONGS_TO]->Account` edge exists.
   - Neo4j `Interaction.headline` and `Interaction.summary` non-null.
   - Pinecone fetch by id=preserved interaction_id returns a vector.
   - `emails.local_enrichment_completed_at` NOT NULL.
10. Test handler idempotency: re-emit EmailPromoted via boto3. Verify nothing changes.
11. Verify downstream consumers (11a-11b):
    - action-item-graph: `action_items WHERE source_interaction_id=preserved_id`.
    - eq-structured-graph-core: Neo4j MERGE confirmed.
12. Teardown per LOCKED-11.

Plus plan §10.4 rollback drill (optional but recommended), and plan §11 acceptance invariants checklist (all 22 invariants must hold).

---

## Mandatory read order for the next session (~20 min)

1. **This file.**
2. **The checkpoint** loaded via `/context-restore` (the 2026-05-18 save titled `phase-1-email-pipeline-m4-shipped-m5-next`).
3. **THE PLAN — §10 + §11**: `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` (eq-email-pipeline:`033626a`). Focus on:
   - §10.3 (production E2E — 12 numbered steps; PRIMARY M5 REFERENCE).
   - §10.4 (rollback drill).
   - §11 (acceptance invariants — the ship-when-true checklist).
   - §8 (edge cases) — re-skim before E2E so you recognize variants if they show up.
4. **The M4 PR** (https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/10) — the comprehensive narrative for what M4 shipped, including the Codex R1 fixes (direction guard, NULL-participants COALESCE, anchor TZ comment).
5. **M5's verified-contract scripts** at `/Users/peteroneil/EQ-CORE/live-transcription-fastapi/scripts/`:
   - `verify_schema.py` — run against M4's new SQL constants if any new ones materialize during E2E.
   - `verify_consumer_contracts.py` — sanity-check the EmailPromoted envelope shape against downstream consumers (action-item-graph + eq-structured-graph-core).
6. **The full M4 source code** (don't re-read, just know where to look):
   - `src/persistence/postgres.py:288-381` — atomic upsert_thread (verify it behaves correctly when participant_emails is NULL).
   - `src/persistence/postgres.py:362-413` — extended email_exists.
   - `src/persistence/postgres.py:1530+` — module-level persist_pending_interaction.
   - `src/pipeline/orchestrator.py:200` — interaction_id pre-allocation.
   - `src/pipeline/orchestrator.py:306-490` — §4.1 + §4.2 block.

---

## Execution sequence — M5

Per plan §10.3 + §10.4 + §11.

### Pre-flight (run BEFORE any M5 work)

1. **Production state stable**:
   ```bash
   curl -sS -o /dev/null -w "live-fastapi: %{http_code}\n" https://live-transcription-fastapi-production.up.railway.app/health
   curl -sS -o /dev/null -w "eq-email-pipeline: %{http_code}\n" https://email-pipeline-production.up.railway.app/api/ping
   curl -sS https://email-pipeline-production.up.railway.app/api/health
   # Expected all 200; eq-email-pipeline checks all "ok".
   ```

2. **M4 code is live**:
   ```bash
   git -C /Users/peteroneil/eq-email-pipeline log --oneline -3
   # Expected top: 6fa181a M4: orchestrator pending_interactions branch...
   ```

3. **LOCKED-17 SHARED-TENANT-COLLISION CHECK** (more important for M5 than M4 — M5's E2E writes destructive data):
   ```bash
   ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
   ```
   Any file modified in the last hour = pause + confirm with user. M5 writes to the shared test tenant `11111111-1111-4111-8111-111111111111`; collision with another agent's seed/teardown could be destructive.

4. **DBOS workflow drain check** (per plan §9 deploy discipline):
   ```sql
   SELECT * FROM dbos.workflow_status
   WHERE status IN ('pending', 'running')
     AND created_at > NOW() - INTERVAL '1 hour';
   ```
   Expected: 0 rows. If non-zero, wait for drain or surface to user.

5. **Pre-existing pending rows in test tenant** (so you know what the baseline is):
   ```sql
   SELECT COUNT(*) FROM pending_interactions
   WHERE tenant_id = '11111111-1111-4111-8111-111111111111'
     AND archived_at IS NULL;
   ```

### Step 0 — Choose synthetic email parameters

Generate a fresh UUID-based prospect identity to avoid clashes:

```python
import uuid
suffix = uuid.uuid4().hex[:12]
from_email = f"test-prospect-{suffix}@cold-prospect-{suffix}.com"
to_email = "stokeseqrm@gmail.com"  # or the test user's connected mailbox
internet_message_id = f"<m5-e2e-{suffix}@cold-prospect-{suffix}.com>"
```

### Step 1-12 — Run plan §10.3 E2E

Walk through the 12 steps in plan §10.3 in order. At each step, capture verification SQL/Neo4j/Pinecone results inline so you have an audit trail if anything breaks.

Use:
- `mcp__neon__run_sql` for Postgres verification.
- `mcp__neo4j_structured__read_neo4j_cypher` for Neo4j verification.
- `mcp__pinecone-custom__describe-index-stats` + `search-records` for Pinecone verification.
- `mcp__railway__deployment_logs` for live log streaming during the EmailPromoted handler step.

### Step 13 — Plan §11 acceptance invariants checklist

Walk every checkbox in plan §11 (the ship-when-true checklist). Pull live values for each `Schema` invariant via Neon MCP. Spot-check `Code` invariants via `grep` (they should all be true post-merge but verify). Walk through `Behavior (E2E)` invariants from the §10.3 results.

### Step 14 — (Optional) Plan §10.4 rollback drill

Per plan §10.4. Synthetic cold-inbound → store in pending_interactions → revert M4 (`git revert 6fa181a` + push + Railway redeploys old code) → subsequent cold-inbounds drop silently → re-deploy M4 → confirm recovery.

Recommended only if production has zero real-user traffic (currently true per LOCKED-11 + user's "no production users yet" framing). If user prefers to skip, document as "not exercised; recovery path documented in plan §10.4."

### Step 15 — Document M5 results

Update `tasks/lessons.md` with any new lessons surfaced by the E2E. Update `MEMORY.md` to reflect M5 status. Surface any V1 limitations that empirically manifested.

### Step 16 — Initiative sign-off

If all §11 invariants hold AND no new P0/P1 bugs, the Phase-1-email-pipeline initiative is COMPLETE. Surface to user with:

- Phase-1-email-pipeline shipped end-to-end (M1 + M2 + M3 + M4 + M5).
- 21+ LOCKED decisions list.
- 5 acknowledged V1 limitations + their V2 roadmap items.
- Phase 2 work as the natural next initiative.

---

## LOCKED decisions (21 total; do NOT re-litigate)

1. DBOS substrate.
2. Single Railway replica + `executor_id=RAILWAY_REPLICA_ID`.
3. EventBridge Path A with `source="com.yourapp.transcription"` and closed `INTERACTION_TYPE_TO_DETAIL_TYPE` lookup.
4. Workflow ID = `f"queue-{queue_id}:approval-{approval_attempt_id}"`.
5. `/approve` reserves synchronously then enqueues.
6. Option B test infrastructure (test-tenant scoping in prod Neon) + `@pytest.mark.requires_db_write` + `RUN_DESTRUCTIVE_TESTS=1`.
7. **Two hard rules** — no contact / no interaction without account anchor.
8. SQLAlchemy 2.0.49 `CAST(:name AS uuid)` form.
9. Materialization REQUIRES Lane 2 raw_interactions before materializing.
10. Codex review BEFORE merging (4-round soft cap; extendable when real P1s keep surfacing — M2 ran 7, M3 ran 6, **M4 ran 2 (R2 CLEAN)** demonstrating the round-N convergence heuristic).
11. Per-batch user confirmation for destructive ops on shared test tenant.
12. Transcripts: frontend forces anchor; emails: backend handles via pending state.
13. Recipient-as-anchor REJECTED for emails.
14. Pending-interactions pattern (Approach C).
15. Lean payload + typed columns for pending_interactions schema.
16. Path B full reprocess on promote via EventBridge `EmailPromoted` event.
17. Shared-tenant collision protocol: pre-flight `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl`.
18. Codex multi-round: `--commit HEAD` past ~1500 lines; `model_reasoning_effort=medium` default.
19. SQS-from-EventBridge for the consumer subscription pattern (M3).
20. DB CAS TTL strictly > SQS VisibilityTimeout (10 min vs 5 min; M3).
21. `HandlerOutcome` tri-state enum {COMPLETE, PERMANENT_SKIP, TRANSIENT_SKIP} (M3).

### M4-locked behaviors (descriptive, not new decisions)

- **§4.1 cold-inbound branch scope:** `direction in ("inbound", "internal")` only. Outbound-to-unknown preserves pre-M4 silent-drop fallthrough; outbound capture is a Phase 2 enhancement.
- **Atomic `upsert_thread`:** single `INSERT ... ON CONFLICT (tenant_id, thread_key) DO UPDATE` statement. Closes the SELECT-then-UPSERT race. Relies on the production UNIQUE index.
- **`persist_pending_interaction`:** module-level free function taking an asyncpg connection (NOT pool), so it participates in the caller's transaction alongside queue + signal helpers.
- **`email_exists` UNION:** emails OR active (`archived_at IS NULL`) pending_interactions. Archived (promoted/expired) pending rows do NOT block retries.

---

## Acknowledged V1 limitations (NOT regressions; documented + bounded)

1. **Personal/internal anchor cold-inbound → log+drop.** V2 roadmap: audit log table.
2. **Neo4j build_skeleton + write_flesh partial-retry corruption.** Bounded by M3 two-layer guard (atomic CAS + 10-min soft TTL > 5-min SQS VT). V2: MERGE patterns + edge-count thread counters.
3. **`upsert_thread` race** — FIXED in M2 for workflow promote path AND in M4 for orchestrator known-account path via atomic INSERT...ON CONFLICT DO UPDATE.
4. **Legacy per-signal loop hardcodes `summary_type='meeting'`** for re-pointed email signals (M2 4-pre-1). Cosmetic duplicate; downstream filters by summary_type='email' get the correct link via M2 Step 5 batch.
5. **`build_skeleton` `CREATE` fallback for missing `internet_message_id`.** Extends V1 limitation #2; same 2-layer guard bound; same V2 roadmap.

### M4-introduced (none)

M4 added zero new V1 limitations. The §4.1 outbound-not-covered scope is a deliberate Phase 2 boundary, NOT a regression — pre-M4 behavior preserved for outbound.

---

## Production credentials + IDs (load-bearing reference)

- **Neon Postgres (eq-dev):** project `super-glitter-11265514`, branch `production`, database `neondb`. Direct connection (no `-pooler`) for `DBOS_SYSTEM_DATABASE_URL`.
- **Test tenant:** `11111111-1111-4111-8111-111111111111`. All test data. Per LOCKED-11.
- **Test user (FK target):** `b0000000-0000-4000-8000-000000000002`.
- **Real stokeseqrm user:** `061ae392-47d5-4f04-9ea8-afa241f23555`.
- **Railway live-transcription-fastapi:** project `847cfa5a-b77c-4fb0-95e4-b20e8773c23e`, service `59a69f3d-9a24-4041-942a-891c4a81c5fb`, env `e4c5ec15-1931-4632-9e58-92d9c6be4261`. M2 SHA `756575d7` is deployment `809679fc` (SUCCESS).
- **Railway eq-email-pipeline:** project `f7d26745-7722-4946-aa3f-9dfc3664426f`, service `92d55588-e548-4188-a179-1d3fa9ea38d2`, env `845e3772-e146-439f-b5f5-cbdfcab6087c`, URL `https://email-pipeline-production.up.railway.app`. **M4 SHA `6fa181a` is deployment `756b96e4` (SUCCESS).** EMAIL_PROMOTED_QUEUE_URL set on this service.
- **Railway eq-agent-action-core:** URL `https://eq-agent-action-core-production.up.railway.app`, service `3036ea0f-afc9-4bc4-889d-c98617d81e96`.
- **eq-email-pipeline:** `/Users/peteroneil/eq-email-pipeline`. Main HEAD `6fa181a` (post-M4 merge).
- **eq-frontend:** `/Users/peteroneil/eq-frontend`. M1 merged at `de586bbc`.
- **AWS** (account `211125681610`, region `us-east-1`): same inventory as M3/M4 sessions — SQS main + DLQ, queue policy, EventBridge rule, IAM principal `eq-bff-kinesis-writer` with `SQSEmailPromotedReader` inline policy.
- **Neo4j:** Aura `c6171c63`, URI `neo4j+s://c6171c63.databases.neo4j.io`.
- **Pinecone:** index per env var `PINECONE_INDEX_NAME` (check Railway service variables).

---

## Stop conditions (hard — surface to user)

- `/context-restore` returns NO_CHECKPOINTS or the wrong checkpoint title.
- MEMORY.md status isn't `PHASE_1_EMAIL_PIPELINE_M1_M2_M3_M4_DEPLOYED_M5_NEXT`.
- Production /api/health returns non-200 OR any of postgres/neo4j/eventbridge is not "ok".
- M4 code is not at `6fa181a` on origin/main (M4 may have been reverted).
- LOCKED-17 collision check shows a concurrent agent in another repo within the last hour AND you're about to run destructive E2E SQL.
- E2E step fails AT A STEP THAT WORKED IN M2/M3/M4 INTEGRATION TESTS — that's a regression, surface immediately.
- E2E surfaces a new V1 limitation NOT in the documented list — surface to user before continuing.
- You're tempted to "fix" a §4.1 outbound corner case during M5 — STOP, that's out of M5 scope (and out of M4 scope per the Phase 2 boundary). Document and continue.

---

## Open questions deferred to M5

1. **Backfill of historical dropped emails** (plan §14 #6) — confirm during M5 that no backfill is needed (test data only; no real users yet per user posture).
2. **EmailPromoted DLQ + observability** (plan §14 #5) — operations setup, separate from M5 code scope. M5 should verify the DLQ catches malformed messages but the alerting wiring is post-initiative work.
3. **Queue UI integration** (plan §14 #7) — `app/(workspace)/agent-queue` may want to surface pending_interactions count. Defer to a separate eq-frontend session.

---

## Handoff artifacts from the prior session (2026-05-18 evening)

- **M4 merged**: https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/10 → `6fa181a` (2 Codex rounds; R2 CLEAN; 1 commit of fixes for R1's 3 findings).
- **M4 deployed**: Railway `756b96e4` SUCCESS; /api/health 200 with all 3 checks ok.
- **Comprehensive checkpoint**: `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/<timestamp>-phase-1-email-pipeline-m4-shipped-m5-next.md`.
- **The plan (unchanged)**: `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` (eq-email-pipeline:`033626a`).
- **Next-session prompt** (paste-ready): `docs/superpowers/specs/2026-05-19-m5-next-session-prompt.md`.
- **New lessons codified** in `tasks/lessons.md`: "Postgres array concatenation is NULL-poisoned" + "Scope to plan-explicit framing when Codex flags scope expansion" (both 2026-05-18).

---

## Phase 2 preview (what comes after the initiative ships)

These are NOT M5 scope but are the natural follow-ons that the Phase 1 + Phase-1-email-pipeline foundation enables:

1. **Neo4j MERGE-everywhere refactor** — replaces V1 limitations #2 + #5. `build_skeleton` becomes truly idempotent via MERGE on `(tenant_id, interaction_id)` fallback; `write_flesh` uses MERGE for Chunk nodes keyed on `(tenant_id, interaction_id, chunk_index)`.
2. **Personal/internal anchor audit log table** — replaces V1 limitation #1.
3. **Contacts identity state machine** — shell/emerging/partial/resolved/verified. Symmetric construct to pending_interactions for emails.
4. **Outbound cold-outreach capture** — extend M4's §4.1 BUSINESS branch to direction=outbound. Tracks the customer's first emails to new prospects in the pending queue for retroactive linking.
5. **eq-email-pipeline EmailPromoted DLQ wiring + observability** — alerting on stuck messages.
6. **Queue UI integration** — surface pending_interactions count per queue entry in `app/(workspace)/agent-queue`.

The Phase 2 design doc lives at `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md`; Phase 2 + Phase 3 sections describe the full roadmap.
