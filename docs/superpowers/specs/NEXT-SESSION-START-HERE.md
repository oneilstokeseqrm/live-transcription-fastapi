# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-18 (M5.3 shipped — agent v2-envelope parser fix. Workflow now reaches `SUCCESS` in 87s (vs M5.2's 8.8min error). Plan §10.3 Steps 1-7 PASS empirically on fresh UUID `b4c1f843baf7`. **Step 8 revealed M5.4 — cross-service Neo4j writer collision in eq-email-pipeline's `build_skeleton`.** Bug investigated, root-caused, evidence preserved, fix deferred to a fresh session for proper design discipline.)
**Status:** ⚠️ **PHASE_1_EMAIL_PIPELINE_M5.3_SHIPPED_M5.4_BLOCKER_FOUND_M5.4_NEXT** — Workflow itself is verified end-to-end. M5.4 is the FINAL Phase-1-email-pipeline blocker. Phase-1 sign-off is GATED on M5.4.

---

## SESSION SCOPE FOR THE NEXT SESSION

**This session is M5.4 — ship the cross-service Neo4j MERGE-key coordination fix, then complete §10.3 + §11 verification.**

The strategic decision (made during M5.3 with full investigation, evidence preserved):
- **The bug is at the eq-email-pipeline / eq-structured-graph-core boundary.** Two services write the same Neo4j `Interaction` node with mismatched MERGE keys.
- **The fix MUST respect the user's design intent: multi-writer safety.** ("we were trying to build so that we wouldn't run into any other issues if one of the other repos wrote first.")
- **The fix MUST NOT break other services or pipelines** — especially the transcript pipeline that also writes Interaction nodes via eq-structured-graph-core.
- **Use proper review discipline:** /codex consult on architecture choice, /plan-eng-review BEFORE coding, /codex review during PR, /codex challenge for adversarial pass on cross-service contract.
- **Scope: finish Phase 1, not Phase 2.** The minimum-blast-radius fix; defer global Neo4j MERGE-everywhere refactor to Phase 2.

| Item | M5.4 scope | Description |
|---|---|---|
| 1 | **INVESTIGATION** | Verify hypothesis by reading eq-structured-graph-core's EnvelopeV1.email handler. Confirm what fields are in workflow's emit payload. Identify ALL Neo4j writers for Interaction nodes. |
| 2 | **DESIGN** | /codex consult on 3 fix options (A: consumer-side, B: producer-side, C: shared contract). /plan-eng-review the chosen approach. See `tasks/m5.4-bug-evidence.md` for blast-radius analysis. |
| 3 | **CODE** | Minimum-blast-radius fix in chosen repo(s). Branch off main. Unit + integration tests. |
| 4 | **REVIEW** | /codex review (multi-round if needed). /codex challenge in adversarial mode — explicitly find ways the cross-service fix could break transcripts. |
| 5 | **DEPLOY + VERIFY** | PR + user merge auth + Railway deploy + /health 200 + re-run §10.3 Steps 1-12 on fresh UUID. |
| 6 | **WALK §11** | All 22 invariants verified. Phase-1-email-pipeline INITIATIVE COMPLETE sign-off. |

Estimated work: **~4-5 hours of focused work**, larger than M5.3 because of cross-service surface area and review discipline.

---

## CRITICAL — what's verified empirically end-to-end (production)

| Plan §10.3 step | Behavior | M5 (pre-M5.2) | M5.2 (Bug #4) | M5.3 (this session) | Status |
|---|---|---|---|---|---|
| Step 1 | Synthesize cold-inbound email | ✓ | ✓ | ✓ | PASS |
| Step 2 | Orchestrator §4.2 BUSINESS path fires | ✓ (HTTP 500 wrapper) | ✓ HTTP 200 | ✓ HTTP 200 | PASS |
| Step 3a-f | Pending state assertions (6 checks) | ✓ | ✓ | ✓ | PASS |
| Step 4 | Duplicate webhook → `skipped_duplicate` | ✓ | ✓ | ✓ | PASS |
| Step 5 | POST `/approve` returns 202 + correct workflow_id | ✓ | ✓ | ✓ | PASS |
| Step 6 | DBOS workflow reaches `status='success'` | ❌ httpx timeout | ❌ Bug #4 shape | **✓ 87 seconds, no errors** | **PASS** |
| Step 7 | Verify promote (account/raw/emails/summaries/links/message_count) | ⏳ NOT REACHED | ⏳ NOT REACHED | ✓ all 7 sub-assertions | PASS |
| Step 8 | EmailPromoted handler `local_enrichment_completed_at` set | ⏳ NOT REACHED | ⏳ NOT REACHED | ❌ M5.4 cross-service collision | **BLOCKED by M5.4** |
| Steps 9-12 | Enrichment + idempotency + downstream + teardown | ⏳ NOT REACHED | ⏳ NOT REACHED | ⏳ NOT REACHED | Pending M5.4 |

**M5.3 specifically verified:**
- Agent v2-envelope parser unwraps `result` correctly; `AccountProfile` validates with field aliases.
- `run_id` preserved across envelope unwrap (R1 fold of Codex P2 finding) — crash-recovery replay path still works.
- Workflow reaches SUCCESS in 87s — proves Bug #4 is closed.
- 17 unit tests pass; 198 unit+contract suite pass; only 1 pre-existing M1 schema-drift failure unchanged.

### Production state at session close (2026-05-18 end-of-day)

- **eq-frontend main HEAD**: `c3bc162` (M5.2 Fix #3 PR #398).
- **live-transcription-fastapi main HEAD**: `aa0fd23` (M5.3 PR #21 merged + Railway deployed; per-phase timeouts + v2 envelope adapter live). Plus this session's handoff commits on top.
- **eq-email-pipeline main HEAD**: `8b2c67a` (M5.2 follow-up).
- **Test tenant `11111111-...` baseline RESTORED**: 0 active pending_interactions, 0 active queue rows, 0 active signals, 0 orphan account, 0 leftover emails/raw_interactions/email_threads. M5.3 E2E artifacts cleaned per LOCKED-11 atomic transaction (10 Postgres deletes + Neo4j DETACH DELETE Interaction + DBOS workflow_status → CANCELLED).
- **Neo4j orphan nodes (Entity/Topic/Chunk/ConversationThread from M5.3's run)** intentionally NOT swept (safety classifier correctly blocked unscoped global cleanup; these MERGE-friendly orphans don't interfere with M5.4 re-run).
- **AWS infrastructure**: 6/6 SQS+EventBridge resources live (unchanged).
- **DBOS workflow `queue-68afd17e-...:approval-c7fcea01-...`**: status='CANCELLED'.

---

## M5.4 BUG — diagnostic summary (full evidence in `tasks/m5.4-bug-evidence.md`)

### Plain English summary

The eq-email-pipeline `EmailPromoted` handler tries to create + enrich a Neo4j `Interaction` node for a promoted cold-inbound email. But another service — **eq-structured-graph-core** — has already created an Interaction node for the same email (via the workflow's earlier Step 5 `EnvelopeV1.email` emit). The two services use **different MERGE keys**:

- eq-structured-graph-core writes by `(tenant_id, interaction_id)`, without setting `internet_message_id`.
- eq-email-pipeline's `build_skeleton` MERGEs by `(tenant_id, internet_message_id)`.

When eq-email-pipeline's MERGE can't find the existing node (because it has no `internet_message_id`), it falls through to a CREATE branch → hits the `(tenant_id, interaction_id)` UNIQUE constraint → handler raises → SQS retries → same wall every time → 5 attempts → DLQ.

### Why latent until now (2+ months)

Every prior M5.x attempt errored at **Step 6** (DBOS workflow itself) before reaching Step 5+6 emit phase. M5.3 was the FIRST production run that reached workflow SUCCESS → first time two writers raced for the same Interaction node → bug exposed.

### Why this is NOT a documented V1 limitation

- V1 #2 ("Neo4j partial-retry corruption — bounded by 2-layer guard") covers **single-writer** partial commits being retried.
- V1 #5 ("build_skeleton CREATE-fallback for missing internet_message_id") covers emails without RFC 5322 Message-ID headers.
- M5.4 is a **multi-writer coordination problem** — different bug class. The 2-layer guard prevents data corruption (no duplicate writes succeed) but completion is impossible without aligning the MERGE keys.

### The 3 fix options (decide in M5.4 session via /codex consult + /plan-eng-review)

See `tasks/m5.4-bug-evidence.md` for full blast-radius analysis.

- **Option A: Fix consumer (eq-email-pipeline)** — change build_skeleton MERGE to use `(tenant_id, interaction_id)`. Low risk; one repo. Loses cross-mailbox dedup affordance.
- **Option B: Fix producer (eq-structured-graph-core)** — include `internet_message_id` in its Interaction writer. Medium risk; touches transcript-handling service.
- **Option C: Shared MERGE-key contract** — define canonical Neo4j Interaction MERGE pattern; both repos adopt. Cleanest architecture; medium-high risk; sets up Phase 2 cleanly.

### Open design question for M5.4

eq-structured-graph-core already writes a rich graph (MENTIONS/Entity, DISCUSSED/Topic, SENT/Contact, RECEIVED/Contact, HAPPENED_IN/CalendarWeek, PART_OF/Chunk, GROUPS/ConversationThread). That overlaps with eq-email-pipeline's `write_flesh`. Does eq-email-pipeline's handler need to add anything beyond what eq-structured-graph-core already wrote? Or should its scope shrink to just headline + summary + Pinecone vector + thread summary + mark complete?

This is the most important design question for M5.4 — surface via /codex consult.

---

## Mandatory read order for the next session (~20-25 min)

1. **This file.**
2. **The checkpoint** loaded via `/context-restore` (the 2026-05-18 save titled `phase-1-email-pipeline-m5.3-shipped-m5.4-blocker-found-m5.4-next`).
3. **THE M5.4 EVIDENCE FILE — load-bearing forensics:** `/Users/peteroneil/EQ-CORE/live-transcription-fastapi/tasks/m5.4-bug-evidence.md`.
4. **THE PLAN — §10.3 + §11**: `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md`. Same load-bearing artifact as M5/M5.2/M5.3.
5. **The 2 new lessons** at the bottom of `tasks/lessons.md`:
   - "Multi-writer Neo4j MERGE-key coordination is its own bug class"
   - "Railway MCP deployment_logs returns runtime logs despite description"
6. **eq-email-pipeline build_skeleton code** (the consumer-side symptom): `/Users/peteroneil/eq-email-pipeline/src/pipeline/skeleton.py` lines 100-180.
7. **eq-structured-graph-core EnvelopeV1.email handler** (the producer hypothesis — verify in M5.4): `/Users/peteroneil/EQ-CORE/eq-structured-graph-core/` — grep for "MERGE (i:Interaction" or similar.
8. **The workflow's emit code** (what's in EnvelopeV1.email): `/Users/peteroneil/EQ-CORE/live-transcription-fastapi/services/account_provisioning/` — find the per-interaction EnvelopeV1 emit.

---

## Execution sequence — M5.4

### Pre-flight (run BEFORE any M5.4 work)

1. **Production health (all 3 services):**
   ```bash
   curl -sS -o /dev/null -w "live-fastapi: %{http_code}\n" \
     https://live-transcription-fastapi-production.up.railway.app/health
   curl -sS -o /dev/null -w "eq-email-pipeline: %{http_code}\n" \
     https://email-pipeline-production.up.railway.app/api/ping
   curl -sS https://email-pipeline-production.up.railway.app/api/health
   curl -sS -o /dev/null -w "eq-agent-action-core: %{http_code}\n" \
     https://eq-agent-action-core-production.up.railway.app/openapi.json
   ```

2. **M5.3 code is live:**
   ```bash
   git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi log --oneline -5
   # Expected: aa0fd23 M5.3 PR #21 in last 5 commits
   ```

3. **LOCKED-17 collision check:** `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10`. Pause if any unexpected agent active.

4. **DBOS drain check** (mcp__neon__run_sql, project `super-glitter-11265514`):
   ```sql
   SELECT * FROM dbos.workflow_status
   WHERE status IN ('PENDING', 'ENQUEUED', 'RUNNING')
     AND created_at > (EXTRACT(EPOCH FROM NOW()) * 1000 - 3600000);
   ```
   Expected: 0 rows.

5. **Test tenant baseline:**
   ```sql
   SELECT COUNT(*) FROM pending_interactions
   WHERE tenant_id = '11111111-1111-4111-8111-111111111111'
     AND archived_at IS NULL;
   ```
   Expected: 0.

6. **NULLS NOT DISTINCT still in place:**
   ```sql
   SELECT indexdef FROM pg_indexes WHERE indexname = 'pending_signal_dedup';
   ```
   Expected: contains "NULLS NOT DISTINCT".

7. **SQS DLQ empty:**
   ```bash
   aws sqs get-queue-attributes \
     --queue-url https://sqs.us-east-1.amazonaws.com/211125681610/eq-email-promoted-dlq \
     --attribute-names ApproximateNumberOfMessages \
     --region us-east-1
   ```
   Expected: 0.

If any fail, STOP and surface.

### Step 1 — Investigation (verify the hypothesis end-to-end)

Read the 3 candidate writer codebases. Confirm:
- eq-structured-graph-core IS the first writer (matches the property signature `trace_id`, `processed_at`, `source='api'`, YAML-front-matter `content_text`).
- The workflow's emit payload shape (does EnvelopeV1.email include `internet_message_id`?).
- No OTHER Neo4j writers create Interaction nodes for emails.

Use Railway MCP runtime logs (`mcp__railway__deployment_logs` on eq-structured-graph-core's latest deployment) to confirm writer activity if helpful.

### Step 2 — Design via /codex consult + /plan-eng-review

- `/codex consult` on the 3 fix options. Frame: "M5.4 is cross-service Neo4j MERGE-key collision. Which option (A/B/C from tasks/m5.4-bug-evidence.md) is the minimum-blast-radius fix? Critical: must NOT regress transcripts."
- If `/codex consult` suggests a fix not in the 3 documented options, evaluate carefully — but stay scoped to Phase 1 (no global refactors).
- `/plan-eng-review` the chosen approach. Write a short plan if the scope warrants it.

### Step 3 — Code the minimum-blast-radius fix

Branch off main in the chosen repo. Unit tests for new behavior. Integration tests if practical.

### Step 4 — Codex review (multi-round + adversarial)

- `/codex review` the diff. Fold P0/P1; consider P2/P3 per LOCKED-10 trajectory.
- `/codex challenge` in adversarial mode — explicitly ask Codex to find ways the cross-service fix could break the transcript pipeline.

### Step 5 — PR + user merge auth + deploy verify

ASK USER for merge auth (M5.3's auth does not extend to M5.4). Verify Railway deploy + /health 200 post-merge.

### Step 6 — Re-run plan §10.3 Steps 1-12 on a fresh UUID

Use a NEW UUID. Walk Steps 1-12 sequentially. Step 8 should now PASS (the M5.4-specific verification).

### Step 7 — Walk §11 22-item invariants checklist

Schema (8) via pg_indexes + information_schema. Code (15) via grep across the 4 repos. Contracts (3) via verify_*.py. Behavior/E2E (8) covered by §10.3 walk.

### Step 8 — (OPTIONAL) Rollback drill per §10.4

Ask user first per LOCKED-11. Out of scope for default sign-off.

### Step 9 — Phase-1-email-pipeline INITIATIVE COMPLETE sign-off

Surface to user with:
- All milestones M1+M2+M3+M4+M5.1+M5.2+M5.3+M5.4 deployed + verified.
- §10.3 Steps 1-12 all PASS.
- §11 22 invariants verified.
- 21 LOCKED decisions list (may grow to 22 if Option C ships).
- 4 V1 limitations (may shrink to 3 if M5.4 closes one, or grow if a new bounded limitation surfaces).
- Phase 2 PLANNING unblocked.

STOP after sign-off. Phase 2 PLANNING is a separate session.

### Step 10 — End-of-session handoff

Same pattern as today's handoff: cleanup test tenant if needed → /context-save → rewrite NEXT-SESSION-START-HERE.md for Phase 2 brainstorming → write a dated next-session-prompt.md → update MEMORY.md status → commit + push handoff docs.

---

## LOCKED decisions (21 total; do NOT re-litigate)

Same 21 as M5/M5.2/M5.3 sessions. M5.4 may add LOCKED-22 (shared MERGE-key contract) if Option C ships; or stay at 21 if Option A/B's fix doesn't warrant a new decision.

---

## Acknowledged V1 limitations (post-M5.3 state)

4 remaining. M5.4 may modify this list depending on the chosen fix:

1. **Personal/internal anchor cold-inbound log+drop** — V2 roadmap: audit log table.
2. **Neo4j build_skeleton/write_flesh partial-retry corruption** — single-writer, bounded by 2-layer guard. M5.4's multi-writer collision is a DIFFERENT class (not this limitation).
3. **Legacy per-signal loop cosmetic duplicate** — re-pointed email signals create cosmetic duplicate 'meeting' summary.
4. **build_skeleton CREATE-fallback for missing internet_message_id** — extends limitation #2 to the missing-header case.

M5.4 itself adds no new V1 limitations; it CLOSES the multi-writer gap.

---

## Stop conditions (hard — surface to user)

- `/context-restore` returns NO_CHECKPOINTS or the wrong checkpoint title.
- MEMORY.md status isn't `PHASE_1_EMAIL_PIPELINE_M5.3_SHIPPED_M5.4_BLOCKER_FOUND_M5.4_NEXT`.
- Production /api/health or /health returns non-200.
- M5.3 commit `aa0fd23` is not in live-transcription-fastapi origin/main.
- LOCKED-17 collision check shows another agent recently active in the test tenant, shared Neo4j cluster, or in eq-structured-graph-core.
- §10.3 Step 8 STILL stalls after M5.4 fix deploys → 6th bug; STOP.
- E2E surfaces a NEW V1 limitation NOT in the documented 4.
- You're tempted to refactor beyond M5.4's minimum-blast-radius fix — STOP, defer to Phase 2.
- /codex review surfaces P0 findings that fundamentally challenge the chosen fix — redo design via /codex consult.
- The fix needs to touch transcript ingestion code in eq-structured-graph-core — STOP, re-scope.

---

## Tools you have — USE THEM

**Railway MCP** is available. The key discovery from M5.3 session: `mcp__railway__deployment_logs` returns RUNTIME logs despite its description saying "build only." Use it for any Railway service diagnostics. Railway project + service IDs are saved in:
`~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/reference_railway_project_ids.md`.

**Neon MCP** for Postgres state + transactions (project `super-glitter-11265514`).

**Neo4j MCP** for graph forensics. Shared cluster — auto-classifier blocks unscoped destructive queries (correctly).

**AWS API MCP** for SQS queue metrics + DLQ inspection.

**gstack skills** — use them liberally for M5.4's design phase:
- `/codex consult` — challenge the architecture choice (CRITICAL for cross-service work)
- `/plan-eng-review` — design review BEFORE coding
- `/codex review` — code review during PR
- `/codex challenge` — adversarial pass on cross-service contract

---

## Handoff artifacts from THIS session (2026-05-18 end-of-day)

- **M5.3 PR merged + deployed:**
  - live-transcription-fastapi #21: `aa0fd23` (agent v2-envelope adapter; Codex R2 CLEAN)
- **Comprehensive checkpoint**: `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/<timestamp>-phase-1-email-pipeline-m5.3-shipped-m5.4-blocker-found-m5.4-next.md`.
- **The plan (unchanged)**: `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md`.
- **M5.4 evidence file (NEW)**: `tasks/m5.4-bug-evidence.md`.
- **Paste-ready M5.4 next-session prompt**: `docs/superpowers/specs/2026-05-19-m5.4-next-session-prompt.md`.
- **2 new lessons**: `tasks/lessons.md` (bottom).
- **Railway project IDs reference**: `memory/reference_railway_project_ids.md`.

---

## Phase 2 preview (still not Phase 2 scope; gated on Phase 1 sign-off)

Same as M5.3 preview, plus M5.4 cleanup items:
- Neo4j MERGE-everywhere global refactor (closes limitation #2 + #4; supersedes M5.4's local fix if Option C wasn't already chosen)
- Contact identity state machine
- Outbound cold-outreach capture
- EmailPromoted DLQ + observability
- Queue UI integration
- 2 deferred items from M5.3 (perf optimization using agent's account_id; CI-runnable contract test)
- (If M5.4 chose Option A or B and not Option C) define a shared MERGE-key contract document.

Phase 2 PLANNING does not start until M5.4 ships AND §10.3 Steps 1-12 + §11 invariants all PASS.
