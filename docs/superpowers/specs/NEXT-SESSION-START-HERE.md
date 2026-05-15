# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — a multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-15 PM (M0 + M1 + M2 + M1-hotfix shipped to production, all empirically verified including a live `/listen` smoke test).
**Status:** ✅ **PHASE_1.5_M0_M1_M2_SHIPPED_M3_NEXT** — DBOS substrate is installed and running in production. Two database changes are live in production Neon. Live transcription path verified post-hotfix. The runway for M3 is clean. **This session executes M3 (the biggest remaining milestone). Code execution IS in scope.**

---

## CRITICAL — this is a multi-session, multi-repo, long-arc project

The Contact Quality Initiative is foundational hardening of the contact + account entity layer that the entire AI-native customer intelligence platform stands on. Phase 1 SHIPPED 2026-05-14 (silent regression fixed 2026-05-15 at `31f513f`). Phase 1.5 is what this session implements. Phase 2 + Phase 3 are documented for architectural coherence and explicitly out of scope.

Decisions made for Phase 1.5 compound across Phase 2 + Phase 3. The DBOS substrate was chosen specifically because it carries all three phases on the same primitives. M1 + M2 + M1-hotfix are now SHIPPED and stable in production — M3 is the workflow code that uses the substrate.

---

## Pre-flight (one-time, before any work)

1. **`/context-restore`** — should load the checkpoint `20260515-132118-phase-1.5-m0-m1-m2-shipped-plus-hotfix-m3-next.md`. **This is the load-bearing handoff** — it captures every decision, drift, lesson, and production state at session end.

2. **Confirm MEMORY.md status reads `PHASE_1.5_M0_M1_M2_SHIPPED_M3_NEXT`.** If anything else, STOP and surface.

3. **Verify repo state:**
   ```bash
   git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi status
   # Should be: on `main` at `e334638`, working tree clean, 0/0 with origin
   git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi stash list
   # Should show 2 entries; stash@{0} is "M2 split correction for plan §20 v4"
   ```

4. **Verify production is healthy:**
   ```bash
   curl -sS -o /dev/null -w "%{http_code}\n" https://live-transcription-fastapi-production.up.railway.app/health
   # Should be 200
   ```

5. **Pop the stash IMMEDIATELY** — it's the plan v4 §20 update documenting M2's split and other M1 drifts:
   ```bash
   git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi stash pop stash@{0}
   ```
   This puts the plan-doc update into your working tree. Commit it as part of M3's first commit.

---

## Mandatory read order at session start

Approximate total reading time: 30 minutes. Tight but every doc is load-bearing for a different reason.

1. **The checkpoint** (already loaded via /context-restore) — full record of M0/M1/M2/hotfix decisions + production state + 3 new lessons captured.

2. **`docs/superpowers/specs/2026-05-15-initiative-context-snapshot.md`** (~8 min) — standalone entry point for the WHOLE initiative. Section 5 reflects the locked DBOS decision. Section 6 has 30 numbered hard invariants any implementation must preserve.

3. **THIS document** (~3 min) — wayfinding + M3 scope.

4. **`docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md`** (~15 min, ~1200 lines after v4 amendments) — **THE LOAD-BEARING IMPLEMENTATION PLAN.** Read in full. For M3 specifically: **§5 (file-by-file plan), §6 (workflow detail), §7 (test-discipline expectations per component), §13 M3 (acceptance criteria), §14 (component × test-discipline matrix).** §20 (revision history; v4 documents M1's API drift + M2's split + M1-hotfix Codex P1s) records every correction so you don't re-derive.

5. **`docs/superpowers/specs/2026-05-15-dbos-scaling-decisions.md`** (~5 min) — locked single-replica V1 + multi-replica-ready posture. **Do NOT revisit `--workers 1` decision** unless a Phase-2 trigger fires.

6. **`tasks/downstream/test-discipline-gaps-2026-05-15.md`** (~3 min) — all five expectations the plan addresses. M3 implements Items 1, 2, 3 in the workflow's test coverage.

**On-demand reference (read when work requires it):**
- `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` — design doc; Sections 1, 7.2, 8.5 reflect the DBOS architecture
- `tasks/downstream/action-item-graph.md` + `tasks/downstream/eq-structured-graph-core.md` — consumer change briefs (relevant for M3 emit-step coding; the `extras.contacts` requirement is locked there)
- The previous session's checkpoint at `20260515-155911-phase-1.5-m0-m1-m2-shipped.md` (mid-session save; superseded by the latest one but has fuller M0/M1/M2 narrative if needed)

---

## This session's work — execute M3 (and ideally M3.5)

### M3 — Workflow definition + tests (1 PR in live-transcription-fastapi)

**Plan §11 explicitly calls M3 "probably a session by itself."** Pace accordingly.

**Scope:**

1. **Create `services/account_provisioning/` package** with these new files:
   - `__init__.py`
   - `workflow.py` — the `@DBOS.workflow()` function with 7 steps per plan §4.1
   - `steps.py` — the 6 `@DBOS.step()` functions per plan §6.1 (revalidate, transition, agent_enrich, resolve_or_create_account, materialize_signals, emit_eventbridge_events)
   - `types.py` — Pydantic models including `AccountProfile`, `AccountProvisioningResult`, `MaterializationResult`, `EmissionRecord`
   - `eventbridge_emit.py` — the emit step's per-interaction logic with `INTERACTION_TYPE_TO_DETAIL_TYPE` closed lookup (plan §3.3, §6.6)
   - `materialization.py` — **moved** from `workers/materialization.py`. SQL stays the same EXCEPT:
     - Remove `INSERT_OUTBOX_SQL` write (lines 230-251 of the current `workers/materialization.py`)
     - Remove the in-memory `linked_pairs` dedup at line 161
     - Change link INSERT to `ON CONFLICT (interaction_id, contact_id) DO NOTHING` (the M2 unique index is live)

2. **Update `routers/queue_actions.py`** — change the import path from `workers.materialization` to `services.account_provisioning.materialization`. `/map`'s inline materialization call uses the moved function. (Do NOT wire `/approve` to the workflow yet — that's M4.)

3. **Rewrite `services/agent_action_core_client.py`** per plan §5.3 + §3.2. New contract:
   ```python
   class AgentActionCoreClient:
       async def enrich(self, *, url: str, effort: Literal["low","medium","high"]="medium", jwt: str) -> AccountProfile
       async def get_run(self, *, run_id: str, jwt: str) -> AccountProfile
   ```
   Call body: `{url, effort}` (per agent's `/openapi.json`). Auth: Bearer JWT. Stream=false for blocking JSON response.

4. **Write all tests** per plan §7 + §13 M3:
   - `tests/unit/account_provisioning/test_workflow.py` — workflow-level unit tests
   - `tests/unit/account_provisioning/test_steps.py` — per-step unit tests against a real SQLAlchemy session (NO `MagicMock` for the session per Item 1)
   - `tests/unit/account_provisioning/test_eventbridge_emit.py` — emit step covers closed-lookup fail-loud, extras.contacts inclusion
   - `tests/unit/account_provisioning/test_agent_client.py` — agent client rewrite
   - `tests/contract/test_agent_enrich_response_shape.py` — **contract-pinning test** against the live production agent (plan §3.2 finding). This is the load-bearing guard for the agent's response shape until they publish AccountProfile in OpenAPI.
   - `tests/integration/account_provisioning/test_workflow_e2e.py` — full workflow E2E against production Neon (test-tenant scoped per the decided test-infrastructure pattern)
   - `tests/integration/account_provisioning/test_reopen.py` — reopen-path coverage (Codex P3)
   - `tests/integration/account_provisioning/test_crash_recovery.py` — DBOS crash-recovery (Codex P3)

5. **Workflow is DEAD CODE at end of M3.** No route wires to it yet. The /approve cutover is M4. The /map keeps its inline materialization (no workflow needed for /map).

6. **Fold the stashed plan v4 §20 update** into the first commit of M3's PR. Add a v5 entry if M3 surfaces any new design drifts.

7. **Capture the 3 new lessons from prior session into `tasks/lessons.md`** as part of M3's PR:
   - "Run Codex review BEFORE merging, not after" — Codex review is a merge gate, not a follow-up
   - "Imports don't catch keyword-arg removals in transitive dependency upgrades" — smoke-test call sites at runtime
   - "Coordinated multi-repo schema migrations need explicit code-lifecycle sequencing" — additions are forward-compat; removals must follow code that no longer depends on them

**Acceptance criteria (plan §13 M3, must all check):**
- [ ] All `@DBOS.step` functions covered by real-substrate unit tests against production Neon (test-tenant scoped).
- [ ] Contract-pinning test passes against the live production agent.
- [ ] Materialization no longer writes outbox; no in-memory link-dedup.
- [ ] `/map` route's import updated; `/map` integration tests still pass.

**Pre-merge ritual (lesson from prior session, codified):**
- **Run `/codex review` on the M3 diff BEFORE requesting merge.** This is the new gate. If Codex finds P1s, fold them in before merge — not after as a hotfix.
- Re-probe plan §3 contracts before writing SQL (per Item 4 of test-discipline-gaps).

### M3.5 — Drop `account_provisioning_outbox` (small follow-up, cross-repo)

Ships AFTER M3 deploys (when no code writes to outbox anymore). Cross-repo PR in eq-frontend:

1. New Prisma migration directory: `prisma/migrations/{timestamp}_drop_account_provisioning_outbox/migration.sql`
2. Contents: `DROP TABLE IF EXISTS "account_provisioning_outbox" CASCADE;`
3. Apply via Neon MCP prepare/complete flow.

Acceptance: outbox table removed from production Neon; no references in code (verified by `grep -rn account_provisioning_outbox` returning zero hits).

### M4 — Queue route cutover + production canary (RISK milestone — next next session)

Out of scope for this session unless M3 finishes fast. Per plan §11 M4:
- Refactor `routers/queue_actions.py` `/approve`: reserve row synchronously then start workflow via `SetWorkflowID(f"queue-{queue_id}:approval-{approval_attempt_id}")`.
- Delete `workers/__main__.py`, `workers/account_provisioning_worker.py`, `workers/outbox_publisher.py`, `workers/advisory_lock.py` + their dedicated tests.
- **Production canary BEFORE traffic depends on the workflow** (plan §12 + plan §13 M4 acceptance): seed synthetic queue entry via Neon MCP, start workflow via `DBOS.start_workflow_async`, assert end-to-end completion + downstream Neo4j visibility.
- Codex review on the diff before merging.

### M5 — Verified-contract tooling + checklist updates (~half session)

- Ship `scripts/verify_schema.py` and `scripts/verify_consumer_contracts.py` (Items 4 + 5 of test-discipline-gaps).
- Update `/review` skill checklist with "Cross-service contracts" + "Live schema probe" sections.

---

## Decisions that are LOCKED — do NOT re-litigate

These are baked in from prior sessions. If you find NEW evidence contradicting one, STOP and surface.

1. **Substrate is DBOS.** Locked at D7 in the rethink session. Codex confirmed sound. M1 deployed it; runtime verified.
2. **`--workers 1`, single replica V1, multi-replica-ready via `executor_id=RAILWAY_REPLICA_ID`.** Locked in `docs/superpowers/specs/2026-05-15-dbos-scaling-decisions.md`. Do NOT switch to `--workers 2` or multi-replica until a Phase-2 trigger fires.
3. **Path A for EventBridge emission** (`EnvelopeV1.*` events, `Source=com.yourapp.transcription`). Locked in plan §3.3.
4. **Workflow ID = `f"queue-{queue_id}:approval-{approval_attempt_id}"`.** Locked in plan §6.2.
5. **`/approve` reserves the row synchronously before starting the workflow.** Locked in plan §5.3.
6. **`account_provisioning_outbox` is DROPPED post-M3.** Locked. Already deferred from M2.
7. **Account creation idempotency key is `account_domains.(tenant_id, domain)`.** NOT `accounts.name`. Locked in plan §6.4.
8. **Emit `extras.contacts` metadata** per downstream change briefs. Locked in plan §6.6.
9. **Closed `INTERACTION_TYPE_TO_DETAIL_TYPE` lookup, fails-loud on unknown types.** Locked in plan §3.3.
10. **Test infrastructure for M3: Option B (test-tenant scoping in production)** with mandatory teardown per test. Locked 2026-05-15 PM by user. Migration to Option A (Neon test branch) gated on "first real customer data lands."
11. **DBOS v2.x API is sync `launch()` / `destroy()` (NOT async variants).** Confirmed empirically in M1 deploy. Plan v4 §20 documents.
12. **websockets pin: 14.2** (knock-on from DBOS). Compat shim in `services/deepgram_websockets_compat.py` handles deepgram-sdk 2.12.0 calling `extra_headers=`. Production-verified.
13. **`DBOS_SYSTEM_DATABASE_URL` is REQUIRED.** `build_dbos_config()` raises `RuntimeError` if unset. Production env var IS set (M0).
14. **Codex review BEFORE merging** is now the gate (not after). Lesson from M1 hotfix.

---

## What this session does NOT do

- **No Phase 2 design.** Sketched in plan §9; don't expand.
- **No re-evaluating `--workers 1`** unless Phase-2 trigger fires.
- **No touching `action-item-graph` or `eq-structured-graph-core` directly.** Those repos have their own agents.
- **No M4** unless M3 finishes with substantial context budget remaining.
- **No fixing the 50 pre-existing test failures.** Tracked separately under test-discipline-gaps Items 1-3.
- **No touching the eq-frontend `feat/deal-health-v8-chrome` branch** (it's marked `[gone]` on remote; the other agent moved on; do not clean up unless asked).

---

## User posture (load-bearing)

The user is a non-developer founder.

- **Make confident technical decisions.** Surface only product or strategic decisions.
- **Work without stopping for clarifying questions.** Make the reasonable call and continue; the user redirects if needed.
- **The user cares about:** cutting-edge 2026 AI-native architecture; architectural correctness over short-term shortcuts; strict OSS only (no SSPL, no BSL); multi-session continuity; **robust planning + live smoke tests** (explicitly emphasized prior session).
- **The user does NOT care about:** sunk-cost preservation; hitting deadlines over correctness; patterns not representing 2026 best practice.

---

## Verified-contracts discipline (re-probe before writing SQL)

Plan §3 contracts were probed 2026-05-15. Time has passed; the M2 migration changed one of them (UNIQUE INDEX added). Before writing SQL that touches a table the plan cites, **re-probe via `mcp__neon__run_sql`** against project `super-glitter-11265514`. The plan calls this out explicitly. Same for the agent's OpenAPI (the response shape is undeclared in their spec; M3's contract-pinning test is the guard).

For M3 specifically:
- Probe `interaction_contact_links` to confirm UNIQUE INDEX `(interaction_id, contact_id)` is still there (should be — it's in production from M2).
- Probe `account_provisioning_outbox` to confirm it's still there (it is — drop deferred to M3.5).
- Probe `accounts`, `account_domains`, `contacts`, `raw_interactions`, `interaction_summaries`, `pending_account_mappings`, `pending_account_mapping_signals` before writing the workflow's SQL.
- Re-fetch `https://eq-agent-action-core-production.up.railway.app/openapi.json` to confirm the `/api/enrich` contract hasn't drifted.

---

## Stop conditions

Stop and surface to the user if:

- `/context-restore` returns NO_CHECKPOINTS or the wrong checkpoint.
- MEMORY.md status string isn't `PHASE_1.5_M0_M1_M2_SHIPPED_M3_NEXT`.
- Pre-flight reveals state different from this prompt (e.g., git not clean, production unhealthy, stash missing).
- Live contract probes return results that contradict §3 of the plan in a way that would change architectural decisions.
- Codex review on M3 diff substantively disagrees with the plan in a way the plan didn't anticipate.
- You discover anything that suggests one of the 14 LOCKED decisions needs reconsideration with NEW evidence.
- M3.5/M4 production canary fails or shows unexpected behavior.
- Any handoff doc references files that don't exist (sync gap).

---

## Per-milestone deliverables

When M3 finishes:
1. PR opened (with Codex review BEFORE requesting merge).
2. Acceptance criteria from plan §13 M3 checked off in PR description.
3. Test suite passing (delta tracked vs main).
4. Commit pushed; merge after Codex pass + your review.
5. Production redeploy verified (no behavior change since workflow is dead code).
6. MEMORY.md status updated.
7. Save a /context-save checkpoint at end of session.
8. Update this handoff for the next session.

---

## Open coordination items (parallel to M3, non-blocking)

- **Agent team** — coordinate with `eq-agent-action-core` team to publish `AccountProfile` schema in their OpenAPI. Currently the response is bare `{}`. Our contract-pinning test in M3 is the load-bearing backup, but architecturally the contract should be declared. Open an issue or small PR in their repo. Non-blocking for M3.
- **eq-frontend `Live DB Tests` CI** — broken on every PR since 2026-05-11 (DATABASE_URL env var unset in workflow). Worth flagging to whoever owns eq-frontend CI. Not blocking us.

---

## Final note

The plan is the load-bearing artifact. Every M3 component maps to all 5 test-discipline expectations in plan §14. Verified-contracts discipline (plan §3) is baked into design time, not deploy time. Re-probe contracts before writing SQL that touches a table the plan cites.

The scaling-decisions spec is the load-bearing artifact for any decision about replica count > 1. Don't bump replicas without shipping the orphan-detector first.

**Codex review BEFORE merging is the gate.** Run it on the M3 diff before requesting merge.

When in doubt, read the plan. When the plan is silent, read the scaling-decisions spec. When still in doubt, surface to the user as a product/strategic decision.

The user is paying for thinking + correct execution, not typing. M3 is the substantial-thinking milestone. Pace accordingly.
