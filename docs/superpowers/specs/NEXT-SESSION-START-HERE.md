# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — a multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-17 (M3 workflow + M4 /approve cutover shipped together as PR #17).
**Status:** ✅ **PHASE_1.5_M3_M4_SHIPPED_M5_NEXT** — DBOS workflow + /approve route wired + 75+ tests + 6 Codex rounds folded. PR #17 open (or merged, depending on session timing). This session verifies deploy → runs production canary → ships M5 (tooling + content fix) → optionally M3.5 (drop outbox).

---

## CRITICAL — this is a multi-session, multi-repo, long-arc project

The Contact Quality Initiative is foundational hardening of the contact + account entity layer that the entire AI-native customer intelligence platform stands on. Phase 1 SHIPPED 2026-05-14. Phase 1.5 milestones M0/M1/M2/M1-hotfix SHIPPED 2026-05-15. M3 (workflow) + M4 (/approve cutover) shipped together 2026-05-17 in PR #17 (M4 brought forward to resolve a Codex pre-merge P1).

**Remaining for Phase 1.5:** M3.5 (drop account_provisioning_outbox) + M5 (verified-contract tooling + empty-content.text backfill fix).

After Phase 1.5: explicit stopping point for comprehensive re-planning before Phase 2 (identity state machine + progressive enrichment) or Phase 3 (advanced conflict resolution).

---

## Production credentials + IDs (load-bearing reference)

Locked across the initiative. Re-stated here so the prompt is self-contained:

- **Neon Postgres (eq-dev):** project `super-glitter-11265514`, branch `production`, database `neondb`. Direct connection (no `-pooler`) for `DBOS_SYSTEM_DATABASE_URL`.
- **Test tenant:** `11111111-1111-4111-8111-111111111111` (column is `tenants.id`). All data under this tenant is test data.
- **Test user (FK target for `pending_account_mappings.owner_user_id`):** `b0000000-0000-4000-8000-000000000002`.
- **Railway FastAPI service:** project `inspiring-upliftment` (`847cfa5a-b77c-4fb0-95e4-b20e8773c23e`), service `59a69f3d-9a24-4041-942a-891c4a81c5fb`, env `e4c5ec15-1931-4632-9e58-92d9c6be4261`, URL `https://live-transcription-fastapi-production.up.railway.app`.
- **Railway eq-agent-action-core:** URL `https://eq-agent-action-core-production.up.railway.app`, service `3036ea0f-afc9-4bc4-889d-c98617d81e96`.
- **Internal JWT:** HS256, `INTERNAL_JWT_SECRET`, `iss=eq-frontend`, `aud=eq-backend`, claims: `tenant_id`, `user_id`, optional `pg_user_id`.
- **AWS:** EventBridge bus `default` (configurable via `EVENTBRIDGE_BUS_NAME`); `AWS_REGION=us-east-1`; access keys in Railway env.
- **Neo4j:** Aura instance `c6171c63`, URI `neo4j+s://c6171c63.databases.neo4j.io`. Shared across graph services.

## ⚠️ Test tenant state at session end

The shared test tenant `11111111-1111-4111-8111-111111111111` is **EMPTY** at the end of the PR #17 session. The session ran multiple destructive ops:
- A one-shot 8-DELETE FK-chain cleanup (mid-session, after test data accumulated)
- Multiple `RUN_DESTRUCTIVE_TESTS=1` pytest runs (each fires conftest teardown after every DB-touching test → deletes accounts → opportunities CASCADE-delete)

User flagged this at session end as "cleaned my account and opportunities again." Acknowledged. The lesson: the `requires_db_write` marker prevents accidental writes by *other* agents but doesn't prevent *me* from wiping the user's own seed data when I knowingly opt in. **Going forward (LOCKED 2026-05-17):** ask the user PER ACTION before destructive ops on the shared test tenant, including before each batch of `RUN_DESTRUCTIVE_TESTS=1` runs. Env-var gating is necessary but not sufficient.

If the user has re-seeded data between sessions, treat it as live — do not run destructive cleanup or DB-write tests without explicit confirmation.

---

## Pre-flight (one-time, before any work)

1. **`/context-restore`** — should load the checkpoint
   `<timestamp>-phase-1.5-m3-m4-shipped-as-pr-17.md`. This is the
   load-bearing handoff — it captures every decision, the 6 Codex
   rounds, the shared-tenant-collision incident, and the production
   state at session end.

2. **Confirm MEMORY.md status reads `PHASE_1.5_M3_M4_SHIPPED_M5_NEXT`.**
   If anything else, STOP and surface.

3. **Verify PR #17 + repo state:**
   ```bash
   gh pr view 17 --json state,mergedAt,reviews,statusCheckRollup
   git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi status
   git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi log --oneline -5
   git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi stash list
   curl -sS -o /dev/null -w "%{http_code}\n" https://live-transcription-fastapi-production.up.railway.app/health
   ```

4. **If PR #17 is NOT merged yet:** review it. Address any post-session
   feedback. Then merge. **If it IS merged:** verify production deploy
   succeeded (Railway dashboard, or `mcp__railway__deployment_status`)
   AND that DBOS launch banner appears in logs.

5. **SHARED-TENANT-COLLISION CHECK (LOCKED 2026-05-16):**
   Before ANY destructive SQL or test that triggers conftest teardown:
   ```bash
   ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
   ```
   Files modified in last hour = hazard. The
   `eq-synthetic-date-generation` agent had an active inject during
   the 2026-05-16 session that was wiped by tenant-scoped DELETEs.
   Pause + ask user if any concurrent activity is detected.

---

## Mandatory read order at session start

Approximate total reading time: 25 minutes. Tight but every doc is load-bearing for a different reason.

1. **The checkpoint** (loaded via /context-restore) — full record of M3+M4 decisions + 6 Codex rounds + production state + the shared-tenant-collision incident.

2. **THIS document** (~3 min) — wayfinding + M5 scope.

3. **`docs/superpowers/specs/2026-05-15-initiative-context-snapshot.md`** (~8 min) — standalone entry point for the WHOLE initiative.

4. **`docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md`** (~10 min) — THE LOAD-BEARING IMPLEMENTATION PLAN. For M5: **§11 M5 + §13 M5 acceptance + §10.5 tooling list**. For the empty-content.text fix: **§3.4 + §6.6**. §20 v5+v6 documents the M3+M4 session's drifts and Codex fold-ins.

5. **`tasks/lessons.md`** (~5 min) — bottom entries. **CRITICAL: the shared-infrastructure collision lesson (2026-05-16)** + the 3 lessons added at start of M3 (Codex pre-merge gate, kwarg removals in transitive deps, multi-repo schema migration sequencing).

6. **PR #17 description** — comprehensive narrative of what shipped, the 6 Codex rounds + their fold-ins, the known limitations deferred to M5.

**On-demand reference (read when work requires it):**

- `docs/superpowers/specs/2026-05-15-dbos-scaling-decisions.md` — locked single-replica V1. Do NOT revisit `--workers 1`.
- `docs/superpowers/specs/2026-05-17-next-session-prompt.md` — the paste-ready opening prompt for this session, mirrors the 2026-05-15 PM prompt's structure.
- `tasks/downstream/test-discipline-gaps-2026-05-15.md` — five expectations. M5 implements **Items 4 + 5**.
- `tasks/downstream/action-item-graph.md` + `eq-structured-graph-core.md` — consumer change briefs.

---

## This session's work — M5 (and optionally M3.5)

### STEP 1 — Verify PR #17 production deploy

- Railway deployed the merge commit.
- DBOS launched with `executor_id` matching `RAILWAY_REPLICA_ID`.
- `/health` returns 200.
- If deploy failed: investigate before proceeding.

### STEP 2 — Production canary (deferred from M3+M4)

Plan §11 M4 + §12 lists this. Canary discipline:

1. **Announce the Neon writes to the user FIRST** + verify no concurrent agents in other repos (the shared-tenant protocol).
2. Seed a synthetic queue entry via Neon MCP under test tenant `11111111-1111-4111-8111-111111111111`.
3. Mint an internal JWT for the test tenant.
4. `POST /queue/{id}/approve` with the JWT.
5. Verify 202 response + `workflow_id`.
6. Poll Neon's `dbos.workflow_status` for the workflow_id until terminal state (success/error). Expected runtime: 30-90s.
7. Verify: `accounts` row + `account_domains` row + `contacts` rows + `interaction_contact_links` rows all exist for the workflow.
8. Verify EventBridge emission (CloudTrail OR synthetic SQS consumer).
9. **Teardown the test rows.** Mandatory.

### STEP 3 — Ship M5

Three sub-deliverables in one PR:

**(i) `scripts/verify_schema.py`** (test-discipline-gaps Item 4)
- Runs EXPLAIN against the live Neon project for an arbitrary SQL text
- Reports missing-column/missing-table errors
- Catches the class of bug that produced the 2026-05-15 silent regression at design time
- Plan §10.5

**(ii) `scripts/verify_consumer_contracts.py`** (Item 5)
- Validates a proposed envelope (source + detail-type + extras shape) against live EventBridge rules + downstream Pydantic models
- Catches the class of bug that produced the action-item-graph SourceType drift incident
- Plan §10.5

**(iii) Empty-content.text fix for backfill emission**
PR #17 documented this as a deferred limitation. Three approaches (surface to user before picking):
- (a) Pull content from `interaction_summaries.summary_content` (Lane 2's post-processing output)
- (b) Add an extras flag `is_backfill=true` + coordinate with downstream consumers (action-item-graph, eq-structured-graph-core) to detect + skip content-dependent processing
- (c) Some other approach surfaced by re-reading the consumer change briefs

### STEP 4 — `/review` skill checklist update

Add "Live schema probe" + "Cross-service contracts" sections to the project's review checklist. ~half-session of documentation work.

### STEP 5 (optional) — M3.5: drop `account_provisioning_outbox`

Coordinated Prisma migration in eq-frontend. The outbox is dead code (no writers in M3); the drop is safe. Acceptance: `grep -rn account_provisioning_outbox` returns zero hits across all repos.

---

## Decisions that are LOCKED — do NOT re-litigate

17 locked decisions (grew from 14 in PR #17 + one more post-session).

Full list:

1. **Substrate is DBOS.** Locked at D7 of the 2026-05-15 rethink.
2. **Single Railway replica V1 + `executor_id=RAILWAY_REPLICA_ID`.** Multi-replica-ready by config; orphan-detector deferred to Phase 2.
3. **EventBridge Path A** (`EnvelopeV1.*` events with `Source=com.yourapp.transcription`).
4. **Workflow ID** = `f"queue-{queue_id}:approval-{approval_attempt_id}"`.
5. **`/approve` reserves the row synchronously then enqueues the workflow** via `SetWorkflowID + APPROVAL_QUEUE.enqueue_async`.
6. **`account_provisioning_outbox` is dropped** post-M3 (M3.5).
7. **Account creation idempotency anchor** is `account_domains.(tenant_id, domain)`. NOT `accounts.name` (no unique index there).
8. **Emit `extras.contacts` metadata** per downstream change briefs.
9. **Closed `INTERACTION_TYPE_TO_DETAIL_TYPE` lookup** (5 entries: transcript / meeting / note / email / batch_upload). Unknown types FAIL LOUD.
10. **Test infrastructure:** Option B (test-tenant scoping in production Neon) + `@pytest.mark.requires_db_write` opt-in marker + mandatory teardown per test.
11. **DBOS v2.x API:** sync `DBOS.launch()`/`DBOS.destroy()` at FastAPI lifespan; **async** `get_event_async`/`set_event_async` INSIDE `@DBOS.step` functions (Codex P0 round-4 fix).
12. **websockets pin 14.2** + deepgram compat shim in `services/deepgram_websockets_compat.py`.
13. **`DBOS_SYSTEM_DATABASE_URL` is REQUIRED.** `build_dbos_config()` raises if unset.
14. **Codex review BEFORE merging** is the gate. Soft cap: **4 rounds** before surfacing diminishing-returns trade-off to user. PR #17 hit 6 (user explicitly stopped at 6).
15. **SQLAlchemy 2.0.49 bindparam truncation** — `text("WHERE id = :name::uuid")` parses bindname as `name_minus_one_char`. Use `CAST(:name AS uuid)` everywhere. (NEW in PR #17.)
16. **Materialization REQUIRES real `raw_interactions` row** before materializing. Placeholder pattern REMOVED. If absent → raise ValueError → DBOS retry OR `/map` 503. (NEW in PR #17.)
17. **Per-action confirmation for destructive ops on shared test tenant.** Env-var gating (`RUN_DESTRUCTIVE_TESTS=1`) is necessary but not sufficient — the marker prevents accidental writes by other agents but does NOT prevent ME from wiping the user's own seed data. Ask the user PER BATCH of destructive runs. (NEW 2026-05-17 post-PR-#17.)

**Codex 4-round soft cap reasoning:** rounds 1-3 typically catch the real architectural P1s. Rounds 4+ find increasingly narrow edges (P2/P3). PR #17 took 6 rounds because we kept finding genuine bugs (placeholder pattern, batch_upload mapping, async event APIs) but the user surfaced "context is heavy" at round 6. Default to surfacing the trade-off after round 4 — let the user decide whether to keep iterating.

---

## What this session does NOT do

- **No Phase 2 design.** Sketched in plan §9; don't expand.
- **No re-evaluating `--workers 1`** unless Phase-2 trigger fires.
- **No touching `action-item-graph` or `eq-structured-graph-core`** directly. M5 ships tooling that VALIDATES against those repos' contracts; coordination on their content changes goes through their own agents.
- **No fixing the 50 pre-existing test failures.** Tracked separately.
- **No `feat/deal-health-v8-chrome` cleanup** in eq-frontend.

---

## User posture (load-bearing)

The user is a non-developer founder.

- **Make confident technical decisions.** Surface only product or strategic decisions.
- **Work without stopping for clarifying questions** unless a stop condition fires.
- **Strict OSS only** (no SSPL, no BSL).
- **Architectural correctness over short-term shortcuts.**
- **Context economy matters.** Surfaced in the M3+M4 session: 6 Codex rounds were felt as heavy. Honor the 4-round soft cap.
- **No production users** (all data is test data) — short-term limitations are acceptable that would block a production ship. PR #17 deferred the empty-content.text limitation on this basis.

---

## Verified-contracts discipline (re-probe before writing SQL or ANY emission code)

Plan §3 contracts were probed 2026-05-15 + re-probed 2026-05-15 PM + verified-unchanged 2026-05-17 (PR #17). For M5 you re-probe AGAIN before writing — discipline is non-substitutable per `tasks/lessons.md` "Cross-service contract verification at design time."

**Tables to re-probe via `mcp__neon__run_sql` (project `super-glitter-11265514`, branch `production`, database `neondb`):**

| Table | Why M5 cares |
|---|---|
| `pending_account_mappings` | verify_schema.py target; queue lifecycle columns |
| `pending_account_mapping_signals` | M5 emit-fix may pull source_type for downstream type mapping |
| `accounts` | verify_schema.py probes this; FK target for many things |
| `account_domains` | UNIQUE INDEX `(tenant_id, domain)` is the canonical idempotency anchor (LOCKED decision 7) |
| `contacts` | verify_schema.py target |
| `raw_interactions` | M5 P1 fix candidate: confirm whether `raw_text` column exists and how it's populated (currently NULL per intelligence_service.py) |
| `interaction_summaries` | UNIQUE INDEX `interaction_summaries_interaction_id_key`; potential source of `content.text` for M5 backfill fix |
| `interaction_contact_links` | M2 UNIQUE INDEX `(interaction_id, contact_id)` MUST still be live |
| `interaction_account_links` | tenant-less link table; teardown chain dependency |
| `account_provisioning_outbox` | M3.5 drop target; confirm still present |

**External contracts to re-probe:**

- `https://eq-agent-action-core-production.up.railway.app/openapi.json` — for verify_consumer_contracts.py validation rules + `AccountProfile` schema (still missing per plan §10.1; coordinate or treat the M3 contract-pinning test as the load-bearing definition)
- EventBridge rules via `mcp__aws-api__call_aws "aws events list-rules --event-bus-name default"`. The two live rules to verify unchanged are `action-item-graph-rule` and `eq-structured-graph-rule`. BOTH filter on `source: ["com.yourapp.transcription", "com.eq.email-pipeline"]` + `detail-type: ["EnvelopeV1.transcript", "EnvelopeV1.note", "EnvelopeV1.meeting", "EnvelopeV1.email"]`. Plan §3.3 documented; PR #17's INTERACTION_TYPE_TO_DETAIL_TYPE depends on this.
- Consumer Pydantic models:
  - `action-item-graph/src/action_item_graph/models/envelope.py:34-43` (SourceType enum — `zoom`+`generic` were missing 2026-05-15; in-flight fix by that repo's agent — independent of M5)
  - `eq-structured-graph-core/app/models/envelope.py:23-42` (EnvelopeV1; loose `source: str`, no enum constraint)

If ANY of these contracts has drifted from what PR #17's code assumes, that's a P0 finding for M5 — fix before merging.

---

## Stop conditions — STOP and surface to user if ANY fire

**Hard stops (do NOT proceed without user OK):**

- `/context-restore` returns NO_CHECKPOINTS or wrong checkpoint
- MEMORY.md status string isn't `PHASE_1.5_M3_M4_SHIPPED_M5_NEXT`
- **Codex review on the M5 PR substantively disagrees with the plan in a way the plan didn't anticipate.** This is how M1-hotfix's 2 P1s were caught + M3+M4's 6-round cycle surfaced real bugs. Treat new contract-drift findings as P0 even if Codex marks them lower.
- Production deploy from PR #17 merge FAILED or DBOS launch banner missing from logs
- Canary fails or shows unexpected behavior (likely indicates design gap, not bug — surface)
- More than **4 Codex rounds** during M5 without P1-clean — surface the diminishing-returns trade-off
- You discover NEW evidence that any of the **16 LOCKED decisions** needs reconsideration
- **Empty-content.text fix decision** (the round-6 P1 deferred from PR #17): three approaches in scope (pull from interaction_summaries / add `is_backfill` flag + downstream coord / accept limitation). Surface options before picking — strategic decision.
- **ANY destructive op on the shared test tenant.** Not just "check the ls"; surface to the user *per action* if you suspect the user has seed data in the tenant. The PR #17 session ran multiple destructive cleanups + tests that wiped test-tenant accounts/contacts/opportunities. User flagged this 2026-05-17. New rule: per-action confirmation, not just env-var gating.

**Soft signals (consider surfacing):**

- PR #17 has unmerged conflicts (likely benign — rebase + resolve)
- Codex round 1-3 with progressive narrowing (normal; keep going)
- Plan §3 contract probe shows minor drift (note in plan §20; continue)

---

## Per-milestone deliverables

When M5 finishes:
1. PR opened (with codex review BEFORE requesting merge).
2. Acceptance criteria from plan §13 M5 checked off.
3. Test suite passing (delta tracked vs main).
4. Production redeploy verified (M5 is tooling + a content fix; the tooling itself is dev-time only).
5. MEMORY.md status updated.
6. /context-save checkpoint at end of session.
7. New paste-ready prompt for the next session.

---

## Open coordination items (parallel to M5, non-blocking)

- **Agent team** — coordinate with `eq-agent-action-core` team to publish `AccountProfile` schema in their OpenAPI. The contract-pinning test from M3 is the load-bearing backup but the architectural correct answer is for them to declare the contract. Open an issue or small PR in their repo. Non-blocking.
- **eq-frontend `Live DB Tests` CI** — broken on every PR since 2026-05-11. Worth flagging to whoever owns eq-frontend CI. Not blocking M5.

---

## Final note

The plan is the load-bearing artifact. PR #17 is the narrative of what shipped. The shared-infrastructure-collision lesson is load-bearing for any write to production Neon's test tenant. The 4-round Codex soft cap is load-bearing for context economy.

**When in doubt, read the plan. When the plan is silent, surface to user.** The user pays for thinking + correct execution + careful coordination, not typing.
