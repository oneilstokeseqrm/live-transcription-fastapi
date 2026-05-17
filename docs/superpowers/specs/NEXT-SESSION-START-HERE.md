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

16 locked decisions (grew from 14 in PR #17). New entries:

15. **SQLAlchemy 2.0.49 bindparam truncation**: `text("WHERE id = :name::uuid")` parses bindname as `name_minus_one_char`. Use `CAST(:name AS uuid)` form everywhere. Confirmed in PR #17.
16. **Materialization REQUIRES Lane 2 to have written real raw_interactions** before materializing. Placeholder pattern REMOVED. If absent → raise → DBOS retry OR /map returns 503.

Full list 1-14 unchanged from prior session (DBOS substrate, single replica, Path A EventBridge, workflow_id formula, /approve reserves synchronously, drop outbox post-M3, account_domains anchor, extras.contacts, closed interaction_type lookup, opt-in DB tests, DBOS v2.x sync launch/async events-in-steps, websockets 14.2 + compat shim, DBOS_SYSTEM_DATABASE_URL required, Codex review BEFORE merging is the gate).

**Soft cap on Codex rounds:** 4 rounds before surfacing to user. PR #17 hit 6 because we kept finding real bugs each round; the diminishing-returns inflection was around round 5 → user said "stop, ship." Mirror this judgment in M5.

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

## Verified-contracts discipline (re-probe before writing SQL)

Plan §3 contracts were probed 2026-05-15 and re-probed 2026-05-15 PM. PR #17's changes didn't alter the schema; the M2 unique index + the unchanged outbox table are still the dominant state. Before writing M5 SQL: re-probe via `mcp__neon__run_sql` against project `super-glitter-11265514`.

For M5 specifically:
- Re-fetch `https://eq-agent-action-core-production.up.railway.app/openapi.json` for verify_consumer_contracts.py's validation rules
- Re-list EventBridge rules via `mcp__aws-api__call_aws` for the same purpose
- Read the latest action-item-graph + eq-structured-graph-core Pydantic models for downstream validation

---

## Stop conditions

Stop and surface to the user if:

- `/context-restore` returns NO_CHECKPOINTS or the wrong checkpoint.
- MEMORY.md status string isn't `PHASE_1.5_M3_M4_SHIPPED_M5_NEXT`.
- PR #17 has unmerged conflicts or new Codex findings.
- Production deploy from PR #17 merge FAILED or shows errors in logs.
- Canary fails or shows unexpected behavior.
- The empty-content.text fix decision needs your input on approach (a/b/c).
- More than 4 Codex rounds during M5 without a clean pass.
- You discover NEW evidence that any of the 16 LOCKED decisions needs reconsideration.

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
