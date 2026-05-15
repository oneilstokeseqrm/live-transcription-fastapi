# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — a multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-15 (DBOS implementation plan written + Codex consult REVISE folded in + user's multi-replica scaling decision baked in; design doc revised; ZERO code changes).
**Status:** ✅ **PHASE_1.5_DBOS_PLAN_WRITTEN_MULTI_REPLICA_READY** — The implementation plan is finalized. The substrate (DBOS) is locked. The scaling posture (single Railway replica V1 + `executor_id`-from-`RAILWAY_REPLICA_ID` multi-replica-ready by configuration + orphan-detector deferred to Phase 2) is locked. **This session executes the plan: milestones M0 through M5.** Code-writing IS in scope.

---

## CRITICAL — this is a multi-session, multi-repo, long-arc project

The Contact Quality Initiative is foundational hardening of the contact + account entity layer that the entire AI-native customer intelligence platform stands on. Phase 1 SHIPPED 2026-05-14 (silent regression fixed 2026-05-15 at `31f513f`); Phase 1.5 is what this session implements; Phase 2 + Phase 3 are documented for architectural coherence and explicitly out of scope.

Decisions made for Phase 1.5 must compound across Phase 2 + Phase 3. The DBOS substrate was chosen specifically because it carries all three phases on the same primitives.

---

## Mandatory read order at session start

Approximate total reading time: 40 minutes. Tight, but every document is load-bearing for a different reason.

1. **`/context-restore`** — Should load the checkpoint `20260515-144528-phase-1.5-dbos-plan-written.md`. **READ THE AMENDMENT SECTION AT THE TOP FIRST** — it captures the multi-replica scaling decision that landed AFTER plan v2 was written. The pre-amendment content below is also context-bearing.

2. **`docs/superpowers/specs/2026-05-15-initiative-context-snapshot.md`** (~10 min) — The standalone entry point for the WHOLE initiative. Section 5 reflects the locked DBOS decision. Section 6 has 30 numbered hard invariants any implementation must preserve.

3. **THIS document** (~3 min) — Wayfinding + per-milestone scope.

4. **`docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md`** (~15 min, ~1100 lines, v3) — **THE LOAD-BEARING IMPLEMENTATION PLAN.** Read in full. §3 (verified contracts) and §14 (component × test-discipline matrix) are the parts to internalize most carefully. §20 (revision history) records every Codex correction and the user's scaling decision so you don't re-derive.

5. **`docs/superpowers/specs/2026-05-15-dbos-scaling-decisions.md`** (~5 min, ~150 lines) — The locked record of single-replica V1 + multi-replica-ready posture + Phase-2 trigger conditions for the orphan-workflow detector + orphan-detector design sketch. **Do NOT revisit the `--workers 1` decision unless a Phase-2 trigger fires** (see §5 of that spec).

6. **`tasks/lessons.md`** bottom TWO umbrella entries (~5 min) — "Four systemic quality gaps" + "Cross-service contract verification at design time." These are the 5 test-discipline expectations the plan addresses in §7.

7. **`tasks/downstream/test-discipline-gaps-2026-05-15.md`** (~3 min) — All five expectations with acceptance criteria.

**On-demand reference (read when the relevant work requires it):**
- `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` — design doc. Sections 1, 7.2, 8.5 reflect the DBOS architecture. Other sections are stable.
- `docs/superpowers/specs/2026-05-15-async-orchestration-rethink-brief.md` — substrate decision audit trail.
- `docs/superpowers/research/2026-05-15-durable-execution-landscape.md` — substrate landscape research.
- `tasks/downstream/action-item-graph.md` + `tasks/downstream/eq-structured-graph-core.md` — downstream consumer change briefs (relevant for M3 emit-step coding).
- `/tmp/codex-dbos-plan-consult-output.md` — full Codex consult output (if you want the raw findings; they're already folded into v3).

---

## This session's work — execute M0 through M5 in order

Each milestone is a self-contained PR. M0 is operational-only (no code). M1-M5 are code milestones.

### Pre-flight (one-time, before any milestone)

- Confirm working tree clean (`git status`).
- Confirm on `main` branch.
- Confirm local + remote synced (`git pull origin main` should be a no-op).
- Verify the test suite still passes (286 tests prior to this session).

### Milestone 0 — Railway operational prep (no code change)

Pure Railway dashboard work. Done in ~15 minutes.

- Provision `DBOS_SYSTEM_DATABASE_URL` env var with a **direct (non-pooler) Neon connection string.** This is distinct from the existing `DATABASE_URL`. DBOS needs the direct connection for its system database; pooler interferes with workflow state.
- Change Railway start command from `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 2` to `--workers 1`.
- Confirm Railway replica count is **1** (default).
- Confirm `RAILWAY_REPLICA_ID` is being auto-injected (echo it from a deploy log or a one-off Railway shell to verify).

**Acceptance:** Plan §13 M0. **Do not proceed to M1 until M0 passes acceptance.**

### Milestone 1 — Substrate install (1 PR)

- Add `dbos-transact-py` to `requirements.txt` (pin a version; record it in the commit message).
- Create `services/dbos_runtime.py` with `DBOSConfig(name="live-transcription-fastapi", system_database_url=os.environ["DBOS_SYSTEM_DATABASE_URL"], executor_id=os.environ.get("RAILWAY_REPLICA_ID"))` + FastAPI lifespan integration.
- Wire the lifespan into `main.py`. No workflows defined yet — DBOS launches but does nothing.
- Verify locally that FastAPI boots and `dbos.workflow_status` tables exist in the Neon test branch.
- Deploy to Railway. Verify production Neon shows the `dbos.*` schema and Railway logs show DBOS launched with a non-null `executor_id`.

**Acceptance:** Plan §13 M1.

### Milestone 2 — Prisma migrations in eq-frontend (cross-repo PR)

This is NOT a `live-transcription-fastapi` PR. It's coordinated work in `/Users/peteroneil/eq-frontend`.

Two migrations:
- **A:** Add UNIQUE INDEX on `interaction_contact_links (interaction_id, contact_id)`. Before adding, verify no existing duplicates with `SELECT (interaction_id, contact_id), COUNT(*) FROM interaction_contact_links GROUP BY 1 HAVING COUNT(*) > 1`. Remediate any duplicates before constraint creation.
- **B:** DROP `account_provisioning_outbox` table (replaced by `dbos.workflow_status`).

Apply to Neon eq-dev first. Verify the existing live-transcription-fastapi test suite still passes against the post-migration database.

**Acceptance:** Plan §13 M2.

### Milestone 3 — Workflow definition + tests (1 PR in live-transcription-fastapi)

The largest milestone. Probably a session by itself.

- Create `services/account_provisioning/` package: `workflow.py`, `steps.py`, `types.py`, `eventbridge_emit.py`, plus the moved `materialization.py` (from `workers/`).
- Remove the outbox INSERT from `materialize_account_approval`. Change the link INSERT to use `ON CONFLICT (interaction_id, contact_id) DO NOTHING` (depends on M2's UNIQUE INDEX being live).
- Rewrite `services/agent_action_core_client.py` to the verified-via-OpenAPI contract (Plan §3.2).
- Write all tests per Plan §7 + §13 M3: unit tests against real Neon test branch (no import-level mocks), contract-pinning test against the live agent, integration tests, reopen-path test, crash-recovery test.
- Workflow is DEAD CODE at end of M3 — no route refers to it yet.

**Acceptance:** Plan §13 M3.

### Milestone 4 — Queue route cutover + production deploy (1 PR)

The change-of-behavior milestone. Highest risk.

- Refactor `routers/queue_actions.py` `/approve` per Plan §5.3: reserve row synchronously then start workflow via `SetWorkflowID(f"queue-{queue_id}:approval-{approval_attempt_id}")`.
- Delete `workers/__main__.py`, `workers/account_provisioning_worker.py`, `workers/outbox_publisher.py`, `workers/advisory_lock.py`, plus their dedicated tests.
- Mark `tasks/downstream/railway-phase-1-5-worker.md` as superseded.
- Run Codex review on the diff before merging (per `tasks/lessons.md` "Real /codex review is non-substitutable").
- Production canary BEFORE traffic depends on the workflow: insert a synthetic queue entry via Neon MCP, manually start the workflow via `DBOS.start_workflow_async`, assert end-to-end completion. Plan §12.
- Extend `/tmp/e2e_phase_1_production.py` per Plan §7.3 (add ~6 cases incl. reopen-path + crash-recovery + replay safety).

**Acceptance:** Plan §13 M4. **All boxes must check.** This includes "at least one real-tenant approval visible in downstream Neo4j with `extras.contacts`-populated Contact properties."

### Milestone 5 — Verified-contract tooling + checklist updates (1 PR)

- Ship `scripts/verify_schema.py` (Item 4 in test-discipline-gaps) and `scripts/verify_consumer_contracts.py` (Item 5).
- Update `/review` skill checklist per the lessons.
- Exercise the updated checklist on the next post-merge PR after this lands.

**Acceptance:** Plan §13 M5.

---

## Decisions that are LOCKED — do NOT re-litigate

- **Substrate is DBOS.** Locked at D7 in the prior rethink session (checkpoint `phase-1.5-rethink-decided-dbos.md`). Codex confirmed sound in plan v1 consult. Do not re-evaluate.
- **`workers 1`, single replica V1, multi-replica-ready via `executor_id=RAILWAY_REPLICA_ID`.** Locked by user 2026-05-15 (`docs/superpowers/specs/2026-05-15-dbos-scaling-decisions.md`). Do not switch to `--workers 2` or multi-replica until a Phase-2 trigger fires (spec §5).
- **Path A for EventBridge emission (`EnvelopeV1.*` events, `Source=com.yourapp.transcription`).** Locked in plan §3.3. The pre-DBOS `com.eq.contact-quality` / `AccountProvisioning.*` contract is dead.
- **Workflow ID = `f"queue-{queue_id}:approval-{approval_attempt_id}"`.** Locked in plan §6.2. Don't use bare `queue_id` (collides with reopen semantics).
- **`/approve` reserves the row synchronously before starting the workflow.** Locked in plan §5.3. Don't push status responsibility into the workflow.
- **Drop the `account_provisioning_outbox` table.** Locked in plan §5.5 + scaling-decisions context. Don't retrofit it with a fabricated `outbox_row_id`.
- **Account creation idempotency key is `account_domains.(tenant_id, domain)`.** Locked in plan §6.4. NOT `accounts.name` (which has no unique constraint).
- **Emit `extras.contacts` metadata** per downstream change briefs. Locked in plan §6.6.
- **Closed `INTERACTION_TYPE_TO_DETAIL_TYPE` lookup, fails-loud on unknown types.** Locked in plan §3.3. Don't default to a synthetic DetailType.

If you genuinely find NEW evidence that contradicts any of these, **STOP and surface to the user** before acting. "Codex disagreed with the plan" → surface (this is how Hatchet was pivoted to DBOS). "I think we should use a different pattern" without new evidence → don't.

---

## What this session does NOT do

- **No Phase 2 design.** The DBOS primitives compound; sketched in plan §9. Don't design Phase 2 features.
- **No orphan-workflow detector.** Locked deferred per scaling-decisions spec.
- **No revisiting the `--workers 1` choice.** See above.
- **No touching `action-item-graph` or `eq-structured-graph-core` directly.** Those repos have their own agents.

---

## The user

Non-developer founder. Make confident technical decisions; surface only product / strategic decisions for the user to weigh in on. Work without stopping for clarifying questions; make the reasonable call and continue; the user will redirect if needed.

**The user cares about:** cutting-edge 2026 AI-native architecture; architectural correctness over short-term shortcuts; strict OSS only (no SSPL, no BSL, no source-available); multi-session continuity across agents.

**The user does NOT care about:** sunk-cost preservation; hitting deadlines over correctness; patterns that don't represent 2026 best practice.

---

## Pre-flight check at session start

1. Run `/context-restore`. Expect the `phase-1.5-dbos-plan-written` checkpoint dated 2026-05-15. The amendment section at the top captures the multi-replica scaling decision.
2. Confirm `MEMORY.md` status reads `PHASE_1.5_DBOS_PLAN_WRITTEN_MULTI_REPLICA_READY`.
3. Read this handoff + the initiative context snapshot + the plan + the scaling-decisions spec + the bottom two lessons.
4. `git status` clean. `git log --oneline -5` should show `8fdf86e` at top (scaling decision commit).
5. Confirm with user that the plan + scaling decision are still the right approach before starting M0 (cheap check in case context has changed).
6. EXECUTE M0 → M1 → M2 → M3 → M4 → M5.

---

## Final note

The plan is the load-bearing artifact. Every component is mapped to all 5 test-discipline expectations in plan §14. The verified-contracts discipline (plan §3) is baked into design time, not deploy time. **Re-probe the contracts before writing SQL** that touches a table the plan cites — they were probed 2026-05-15 and drift is possible.

The scaling-decisions spec is the load-bearing artifact for any decision about replica count > 1. Don't bump replicas without shipping the orphan-detector first.

The user is paying for thinking + correct execution, not typing. M3 (workflow + tests) is the substantial-thinking milestone. M0 and M2 are 15-30 minutes each. M1 is small. M4 is the risk milestone. M5 is cleanup + tooling.

When in doubt, read the plan. When the plan is silent, read the scaling-decisions spec. When still in doubt, surface to the user as a product/strategic decision.
