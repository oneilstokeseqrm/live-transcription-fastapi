# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-18 (M5.4 fix coded + Codex-reviewed + tested locally; **5 commits on branch `phase-1/m5.4-neo4j-merge-key-alignment` in eq-email-pipeline, UNPUSHED.** Branch contains the Cypher MERGE-key alignment + 11 tests (7 unit + 4 integration) all passing + plan §17 addendum. Path-1 checkpoint per user — code and review done; deploy + production E2E + Phase 1 sign-off arc deferred to a fresh session.)
**Status:** ⚠️ **PHASE_1_EMAIL_PIPELINE_M5.4_CODED_REVIEWED_READY_TO_PUSH** — M5.4 work is committed locally to the eq-email-pipeline branch. Next session: push, open PR, **ASK user for merge auth** (M5.4 auth does NOT extend from prior milestones), deploy verify, run plan §10.3 Steps 1-12 on a fresh UUID, walk §11 22-item invariants, sign off Phase 1.

---

## SESSION SCOPE FOR THE NEXT SESSION

**This session is the M5.4 deploy + verify arc + Phase 1 sign-off.** All design and code work is done. The next session executes the ship-and-verify pipeline.

| Item | Scope | Description |
|---|---|---|
| 1 | **PUSH** | Push branch `phase-1/m5.4-neo4j-merge-key-alignment` (5 commits, ~1450 insertions / 100 deletions). ASK user before push. |
| 2 | **PR** | Open PR with title `M5.4: align Neo4j Interaction MERGE key to system-wide convention (Phase 1 sign-off blocker)`. PR body includes Codex review trajectory (R1 P2 → R2 2×P2 → R3 CLEAN → challenge folded → R4 P1 → R5 CLEAN), test plan, deploy plan. |
| 3 | **MERGE AUTH** | **ASK USER** for merge authorization (LOCKED-10 + per-initiative auth not extending). Merge after green. |
| 4 | **DEPLOY VERIFY** | Wait for Railway deploy of eq-email-pipeline. Verify deployment Status=SUCCESS + `/api/health` 200 with postgres+neo4j+eventbridge all "ok". |
| 5 | **DLQ DRAIN** | **ASK USER** to drain the existing M5.3 DLQ message (interaction_id `c1a7a5ac-...`, points to already-deleted data; safe to drain). Per LOCKED-11. |
| 6 | **§10.3 E2E** | Re-run plan §10.3 Steps 1-12 on a FRESH UUID (NOT `b4c1f843baf7` from M5.3). Step 8 (`emails.local_enrichment_completed_at IS NOT NULL` within ~3-5 min) is the M5.4-specific verification. Steps 9-12 enrichment + idempotency + downstream + teardown. |
| 7 | **§11 WALK** | All 22 acceptance invariants. Schema (8) via pg_indexes + Neo4j SHOW CONSTRAINTS. Code (15) via grep. Contracts (3) via verify_*.py. Behavior/E2E (8) covered by §10.3 walk. |
| 8 | **SIGN-OFF** | Phase-1-email-pipeline INITIATIVE COMPLETE. Phase 2 PLANNING unblocked. |
| 9 | **HANDOFF** | /context-save, rewrite NEXT-SESSION-START-HERE.md for Phase 2 brainstorming, update MEMORY.md status, commit + push handoff docs. |

Estimated work: **~2-3 hours of focused work** (smaller than the M5.4 design+code session because the heavy lifting is done; this is deploy + verify).

---

## What's done (5 commits on branch, ALL TESTED)

| SHA | Commit | What it changed |
|---|---|---|
| `6a61254` | M5.4: align Neo4j Interaction MERGE key | The main fix — replaced MERGE-on-IMID + CREATE-fallback with single MERGE on `(tenant_id, interaction_id)` + ON MATCH SET COALESCE pattern. Plus `interaction_unique` constraint added to `ensure_constraints`. Plus 9 new tests. Plus plan §17 addendum. |
| `77129af` | Codex R1 P2 fold | Test isolation: replaced tenant-wide count with `_count_interactions_by_id` for the second-writer integration test. |
| `76cf2f2` | Codex R2 2×P2 fold | `source` + `content_text` switched from COALESCE to unconditional overwrite on match (eq-email-pipeline is authoritative for emails). Split unit test into overwrite + COALESCE classes; integration test now verifies overwrite empirically. |
| `78e3ced` | Codex challenge finding #1 fold | Discovered 2 more Interaction writers (`eq-interaction-threads`, `thematic-lm`) that create defensive stubs without `interaction_type` or `timestamp`. Added COALESCE-backfill for those fields on match + `timestamp` on ON CREATE SET. New stub-first integration test verifies the backfill empirically. |
| `dbd73c4` | Codex R4 P1 fold | conftest.py read `neo4j_container.username/password` instead of hardcoding `("neo4j", "password")` — pre-existing fixture would break in environments with `NEO4J_PASSWORD` env var set. |

**Test status:** 11/11 M5.4 tests pass (7 Cypher-shape AsyncMock unit tests + 4 multi-writer integration tests against Testcontainers Neo4j). 479 other tests pass (3 pre-existing failures in `test_pipeline_integration.py` are FK-violation environmental issues unrelated to M5.4, verified by re-running on bare main).

**Codex review status:** R5 CLEAN. 5 rounds of /codex review + 1 adversarial /codex challenge. All P0/P1/P2 findings folded or explicitly deferred to Phase 2 with rationale.

---

## Pre-flight for the deploy session (run BEFORE pushing)

1. **All-services health** (sanity check — same as M5.4 design-session pre-flight):
   ```bash
   curl -sS -o /dev/null -w "live-fastapi: %{http_code}\n" https://live-transcription-fastapi-production.up.railway.app/health
   curl -sS https://email-pipeline-production.up.railway.app/api/health
   curl -sS -o /dev/null -w "eq-agent-action-core: %{http_code}\n" https://eq-agent-action-core-production.up.railway.app/openapi.json
   ```
   Expected: all 200; eq-email-pipeline checks all "ok".

2. **Branch state still as expected:**
   ```bash
   git -C /Users/peteroneil/eq-email-pipeline log --oneline main..phase-1/m5.4-neo4j-merge-key-alignment
   ```
   Expected: 5 commits exactly: `dbd73c4 → 78e3ced → 76cf2f2 → 77129af → 6a61254`.

3. **LOCKED-17 collision check:**
   ```bash
   ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
   ```
   Pause if any other agent active in eq-email-pipeline or live-transcription-fastapi in last 60 min.

4. **DBOS drain:**
   ```sql
   SELECT * FROM dbos.workflow_status
   WHERE status IN ('PENDING','ENQUEUED','RUNNING')
     AND created_at > (EXTRACT(EPOCH FROM NOW())*1000 - 3600000);
   ```
   Expected: 0 rows (in Neon project `super-glitter-11265514`).

5. **Test tenant baseline:**
   ```sql
   SELECT COUNT(*) FROM pending_interactions
   WHERE tenant_id = '11111111-1111-4111-8111-111111111111' AND archived_at IS NULL;
   ```
   Expected: 0.

6. **NULLS NOT DISTINCT still in place:**
   ```sql
   SELECT indexdef FROM pg_indexes WHERE indexname = 'pending_signal_dedup';
   ```
   Expected: contains "NULLS NOT DISTINCT".

7. **SQS state:**
   ```bash
   aws sqs get-queue-attributes --queue-url https://sqs.us-east-1.amazonaws.com/211125681610/eq-email-promoted-queue \
     --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible --region us-east-1
   aws sqs get-queue-attributes --queue-url https://sqs.us-east-1.amazonaws.com/211125681610/eq-email-promoted-dlq \
     --attribute-names ApproximateNumberOfMessages --region us-east-1
   ```
   Expected: main queue 0 / 0; DLQ 1 (the M5.3 leftover — will be drained as part of session item 5).

---

## Critical context the next session needs

### The M5.4 fix details (cliff notes)

**Production Neo4j has 2 UNIQUENESS constraints on `:Interaction`:**
- `interaction_unique` on `(tenant_id, interaction_id)` ← the canonical key, what eq-structured-graph-core / action-item-graph / now eq-email-pipeline all MERGE on
- `interaction_email_dedup` on `(tenant_id, internet_message_id)` ← preserved as belt-and-suspenders; not the MERGE key

**Pre-M5.4 bug:** eq-email-pipeline MERGEd on `(tenant_id, internet_message_id)`. When eq-structured-graph-core (or action-item-graph, or stub writers eq-interaction-threads / thematic-lm) created the node first by `(tenant_id, interaction_id)` without IMID, the MERGE didn't reconcile → CREATE-fallback → `interaction_unique` violation → handler raises → SQS DLQ.

**M5.4 fix:** single MERGE on `(tenant_id, interaction_id)`. ON MATCH SET has three classes:
- **Authoritative-overwrite** (`source`, `content_text`): eq-email-pipeline is canonical for email interactions; overwrite generic placeholders from other writers
- **Stub-writer-backfill** (`interaction_type`, `timestamp`): defensive stubs leave these NULL; COALESCE backfills so downstream recency reads work
- **Email-only COALESCE** (subject, from_email, direction, thread_key, has_attachments, provider_message_id, internet_message_id, connected_user_id, sent_at): preserve earlier eq-email-pipeline run's values across retries

**Files touched (eq-email-pipeline ONLY — no other repos):**
- `src/pipeline/skeleton.py` — the fix (lines ~100-200)
- `src/persistence/neo4j.py` — added `interaction_unique` constraint to `ensure_constraints` (idempotent)
- `src/pipeline/email_promoted_subscriber.py` — refreshed obsolete comment
- `tests/conftest.py` — fixed Neo4jContainer auth (pre-existing fixture rot, side benefit)
- `tests/test_skeleton_multi_writer.py` — NEW 11 tests
- `docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` — §17 addendum

**Plan §17 in eq-email-pipeline is the load-bearing design doc** for M5.4. Read it first.

### What's NOT touched (preserve these — verified)

- `eq-structured-graph-core` (worker last deployed 2026-03-20; transcript pipeline unaffected)
- `action-item-graph` (reference pattern; not touched)
- `live-transcription-fastapi` (workflow emit shape, M5.3 parser, materialization, all preserved)
- `eq-frontend` (Prisma schema, M5.2 fix all preserved)
- `eq-interaction-threads`, `thematic-lm`, `opportunity-forecasting` (all preserved)

### LOCKED decisions (21 → potentially 22)

If §11 invariants verify cleanly, add LOCKED-22 per plan §17.10: *"Neo4j Interaction MERGE-key convention: (tenant_id, interaction_id) with ON MATCH SET ... = COALESCE(prop, $val) defensive pattern."*

### V1 limitations (post-M5.4 fix)

- V1 #2 (Thread.message_count + Chunk CREATE replay corruption) — STILL OPEN. Phase 2 work.
- V1 #5 (build_skeleton CREATE-fallback for missing IMID) — **CLOSED** at the Neo4j layer by M5.4 (NULL IMID now handled cleanly via MERGE-on-iid).
- V1 #1 (Personal/internal anchor log+drop) — unchanged.
- V1 #3 (legacy per-signal loop cosmetic duplicate) — unchanged.

### Codex challenge findings #2 + #3 (deferred to Phase 2)

- **#2** `ensure_constraints` swallows DDL errors silently — pre-existing pattern; Phase 2 hardening.
- **#3** Thread.message_count + Chunk CREATE replay — same as V1 #2; already documented; Phase 2 MERGE-everywhere refactor.

---

## §10.3 production E2E walk (the verification arc)

Per plan §10.3, Steps 1-12 on a fresh UUID against the test tenant `11111111-1111-4111-8111-111111111111`:

1. **Synthesize cold-inbound email** — fresh UUID `m5.4-e2e-<run_id>`; `from=test-prospect-<run_id>@cold-prospect-<run_id>.com`; processing_tier=full.
2. **POST /api/emails/ingest** → HTTP 200, status='pending_account_approval'.
3a-f. **Pending state assertions** — pending_interactions, pending_account_mappings, pending_account_mapping_signals all populated; raw_interactions / emails / interaction_summaries all empty.
4. **Duplicate webhook** → skipped_duplicate.
5. **POST /api/queue/<id>/approve** → HTTP 202 with workflow_id.
6. **Poll DBOS workflow status** → 'success' within ~90s.
7. **Verify promote** — accounts, raw_interactions, emails, interaction_summaries, interaction_contact_links, email_threads, message_count all correct.
8. **🆕 M5.4-specific:** `emails.local_enrichment_completed_at IS NOT NULL` within ~3-5 min of /approve. (This is the bug we just fixed.)
9. **Verify enrichment** — Neo4j Interaction.headline + summary populated; Pinecone vector exists; metadata.account_id correct.
10. **Idempotency** — re-emit EmailPromoted via boto3 → no-op, no new Neo4j nodes.
11. **Downstream** — action-item-graph + eq-structured-graph-core consumers received the EnvelopeV1.email and processed normally.
12. **Teardown** — atomic per LOCKED-11 (ASK user first).

If Step 8 STALLS after deploy → 6th bug; STOP and surface.

---

## §11 22-item invariants checklist

Same as M5.3 session pattern. Run after §10.3 passes. Schema (8) + Code (15) + Contracts (3) + Behavior/E2E (8) = walk each one and check it.

Plan §17.8 has the M5.4-specific additions to the existing §11 invariants.

---

## Phase 1 sign-off criteria

If ALL hold, sign off Phase-1-email-pipeline as INITIATIVE COMPLETE:
- ✅ M1, M2, M3, M4, M5.1, M5.2, M5.3, M5.4 all deployed + verified
- ✅ §10.3 Steps 1-12 all PASS
- ✅ §11 22 invariants all verified
- ✅ No new P0/P1 bugs surfaced
- ✅ V1 limitations: 3 open (#1, #2/#3, #4) + 1 closed (#5)
- ✅ Phase 2 PLANNING unblocked (separate session)

Surface to user with the sign-off summary. Then end-of-session handoff.

---

## STOP conditions (hard — surface to user)

- /context-restore returns NO_CHECKPOINTS or wrong title.
- MEMORY.md status isn't `PHASE_1_EMAIL_PIPELINE_M5.4_CODED_REVIEWED_READY_TO_PUSH`.
- Production /api/health returns non-200 (any service).
- Branch `phase-1/m5.4-neo4j-merge-key-alignment` doesn't exist or has unexpected commits.
- Pre-flight DBOS drain returns >0 active workflows.
- LOCKED-17 collision check shows concurrent agent on shared infra.
- Step 8 of §10.3 STILL stalls after M5.4 fix deploys → 6th bug; STOP.
- §11 invariants fail unexpectedly.
- Codex review re-run post-merge surfaces a P0/P1 finding (run a sanity round if anything seems off after deploy).

---

## Tools available

**Railway MCP** with saved project + service + env IDs at `memory/reference_railway_project_ids.md`. Use `mcp__railway__deployment_logs` for runtime log diagnostics.
**Neon MCP** (project `super-glitter-11265514`).
**Neo4j MCP** (shared cluster — auto-classifier blocks destructive unscoped queries correctly).
**AWS API MCP** (SQS + EventBridge).

---

## Reference artifacts

- **The plan (load-bearing):** `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` — §17 is the M5.4 design addendum.
- **M5.4 branch:** `phase-1/m5.4-neo4j-merge-key-alignment` in `/Users/peteroneil/eq-email-pipeline`. 5 commits, all green.
- **M5.4 bug evidence:** `/Users/peteroneil/EQ-CORE/live-transcription-fastapi/tasks/m5.4-bug-evidence.md` (root-cause forensics from the M5.3 session).
- **Railway project IDs:** `~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/reference_railway_project_ids.md`.
- **Lessons:** `/Users/peteroneil/EQ-CORE/live-transcription-fastapi/tasks/lessons.md` (2 M5.3 lessons + 7 prior load-bearing M5.x lessons).
- **New feedback memory from M5.4 design session:** `feedback_test_pattern_no_docker.md` — AsyncMock unit tests are the default; Docker/Testcontainers is exception, not norm.
- **Plus the new feedback memory from M5.3 session:** `feedback_complete_all_handoff_reads_before_action.md` — read EVERY mandatory artifact before pre-flight.
