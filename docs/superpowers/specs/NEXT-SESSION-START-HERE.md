# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative
**Last session:** 2026-05-13 (design revisions + Phase 1 + 1.5 implementation plan)
**Status:** Design approved, implementation plan written and self-reviewed, ready to begin Phase 1 implementation via subagent-driven execution.
**Execution mode chosen:** Subagent-Driven (orchestrator dispatches a fresh subagent per task and reviews between tasks).

---

## Critical context (READ FIRST, BEFORE EVERYTHING ELSE)

Three things you must internalize before opening any artifact:

1. **You are an orchestrator now, not an implementer.** This session executes a written plan via subagent dispatch. You read the plan, dispatch a subagent per task with a self-contained prompt, review the subagent's output, then update task state and move to the next task. You do not implement tasks yourself except for trivial coordination steps. The `superpowers:subagent-driven-development` sub-skill is the canonical reference for how to do this; invoke it before dispatching the first task.

2. **The user is a non-developer founder.** Make confident technical calls on subagent dispatch prompts, task selection, error recovery, and review judgments. Surface only product/strategic decisions for the user. Do not ask the user to validate enums, schema details, or implementation patterns. The user explicitly said: "Make the reasonable call and continue; they'll redirect if needed."

3. **All scope decisions are locked.** The full decision log is in `project_contact_quality_initiative.md` in auto-memory. Do NOT re-litigate locked decisions — they were aligned across many turns of prior conversation. If a subagent suggests deviating from a locked decision, the locked decision wins; document why in the subagent's review and proceed.

---

## Read these in order before dispatching any subagent

Total reading: ~30-45 minutes. The plan is dense; skim sections you've already encountered but read the plan in full because every subagent dispatch references it.

1. **Auto-loaded:** `MEMORY.md` (loads automatically; index of all project memory)

2. **Project status & decision log:** `~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/project_contact_quality_initiative.md`
   - Authoritative list of locked decisions (general + 2026-05-13 additions)
   - Phasing, repos affected, trajectory

3. **The revised design document (canonical project intent):** `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md`
   - Header should read "Design approved" — if it still says "REVISIONS PENDING," stop and check the git history; something went wrong
   - Section 12 is the verifiable-invariants spec that acceptance tasks check against
   - Section 5.2-5.4 contains the queue/signals/outbox schemas
   - Section 7.1 + 7.2 are the phase scopes

4. **The implementation plan (your execution document):** `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md`
   - ~55 tasks split across Phase 1 (~28) and Phase 1.5 (~27)
   - File Structure table at top maps responsibilities
   - Each task has Files + Steps + acceptance criteria

5. **The Codex review (historical audit trail, NOT action items):** `docs/superpowers/specs/2026-05-12-contact-quality-initiative-codex-review.md`
   - All 15 findings are already integrated into the design doc. Do not re-integrate.
   - Reference only when a subagent surfaces something that smells like a Codex finding — check whether it's a NEW issue or a REPEAT of one already closed.

6. **Current-state architecture reference:** `docs/contacts-architecture.md`
   - Cross-service contact data flow (Postgres + Neo4j + 5 services)
   - FK chain gotcha (`interaction_contact_links.interaction_id` actually holds `interaction_summaries.summary_id`)
   - Source field validation, NOT NULL columns without defaults, etc.

7. **Prior-initiative downstream pattern (reference for cross-repo dispatch):** `tasks/downstream/` directory
   - The contact-enrichment initiative's downstream investigation docs and agent prompts. Same pattern as the cross-repo briefs the Phase 1 + 1.5 plan dispatches.

---

## What this session does

Execute the implementation plan via `superpowers:subagent-driven-development`. Specifically:

### Step 1 — Invoke the sub-skill

Before doing anything else, invoke `superpowers:subagent-driven-development`. It will load the canonical workflow for orchestrator-subagent execution: one task per subagent, fresh subagent per task, two-stage review (subagent self-review + your review), state updates between tasks. Follow it exactly.

### Step 2 — Walk Phase 1 task-by-task, in order

Phase 1 tasks run in dependency order. The critical ordering constraint is:

```
T0 (pre-flight branch + baseline tests)
  → T1.1, T1.2 (eq-frontend Phase 1 schema migration — BLOCKING for many later tasks)
    → T1.3-T1.10 (model-layer changes — can run in parallel within batches if subagents are dispatched concurrently)
      → T1.11-T1.16 (ingestion-path tightening — depends on model-layer changes)
        → T1.17-T1.19 (domain utilities — independent of ingestion-path work; can run in parallel)
          → T1.20-T1.22 (transcript queue insertion — depends on T1.17-T1.19)
            → T1.23, T1.24 (cross-repo dispatch to eq-email-pipeline)
              → T1.25 (Phase 1 invariant verification — gates Phase 1 acceptance)
                → T1.26 (Codex consult on Phase 1 diff — recurring quality gate)
                  → T1.27 (docs + memory update)
                    → T1.28 (Phase 1 PR + merge + deploy)
```

**Cross-repo blocking:** Tasks T1.20-T1.22 and T1.23-T1.24 cannot land until the eq-frontend Phase 1 schema migration (T1.1, T1.2) has been applied to Neon eq-dev. If the eq-frontend agent hasn't finished, pause Phase 1 work on this repo and wait. Do not work around the missing schema — the model layer in this repo references the new tables/columns.

**Parallelization opportunity:** T1.3 through T1.10 are model-layer changes that don't depend on each other. You can dispatch multiple subagents in parallel here. Same for T1.17 through T1.19 (domain utilities). Don't parallelize ingestion-path tightening (T1.11-T1.16) — those touch overlapping import lines.

### Step 3 — Phase 1 acceptance gate (do not skip)

Before transitioning to Phase 1.5, run all of:

1. `scripts/verify_phase_1_invariants.sh` exits 0
2. `pytest tests/ -v` passes
3. Codex consult (T1.26) returns no CRITICAL findings; integrate any IMPORTANT findings inline before Phase 1.5
4. eq-frontend Phase 1 schema migration is merged and applied
5. eq-email-pipeline calendar_sync + orchestrator Phase 1 changes are merged
6. Phase 1 PR is merged to main and deployed to Railway via auto-deploy

If any of these fail, do NOT proceed to Phase 1.5. Pause, surface the blocker, and let the user decide next steps. A failed acceptance gate is an explicit stopping point.

### Step 4 — AI-native thought leadership research (Task 1.5.0)

Before starting Phase 1.5 plan execution, run targeted research on:
- Microsoft GraphRAG production patterns (outbox semantics, account-centric graph indexing)
- Agentic entity-resolution literature (FastER successors, semantic ER)
- Outbox/saga durability in distributed data systems
- Recent papers on LLM-driven graph maintenance and convergence

Save findings to `docs/superpowers/specs/<YYYY-MM-DD>-phase-1-5-ai-native-research.md`. If the research surfaces design changes, update the implementation plan inline before dispatching Phase 1.5 tasks. Do not silently deviate from the design without updating the doc.

### Step 5 — Walk Phase 1.5 task-by-task

Same orchestration model as Phase 1. Critical ordering:

```
T1.5.0 (research)
  → T1.5.1 (Phase 1.5 feature branch)
    → T1.5.2, T1.5.3 (eq-frontend Phase 1.5 schema + test-data wipe — BLOCKING)
      → T1.5.4-T1.5.10 (worker, outbox, authorization — can be partially parallelized)
        → T1.5.11 (queue action routes — depends on materialization + authz)
          → T1.5.12-T1.5.14 (expiry, re-open — depends on schema)
            → T1.5.15, T1.5.16 (cross-repo: eq-structured-graph-core + eq-frontend)
              → T1.5.17 (eq-agent acceptance tests — depends on agent client)
                → T1.5.18-T1.5.21 (E2E tests)
                  → T1.5.22 (invariant verification)
                    → T1.5.23 (Codex consult)
                      → T1.5.24 (production validation in test tenant)
                        → T1.5.25 (docs + memory)
                          → T1.5.26 (PR + merge + deploy)
                            → T1.5.27 (stopping-point handoff for Phase 2 re-planning)
```

**The test-data wipe (T1.5.2) is the most consequential single action.** It TRUNCATES `contacts`, `raw_interactions`, `interaction_summaries`, `pending_account_mappings`, `accounts`, and related tables in eq-dev. After the wipe, the NOT NULL constraints land. If anything went wrong with Phase 1, the wipe erases the evidence. Run Phase 1 acceptance gates BEFORE the wipe.

### Step 6 — Phase 1.5 acceptance gate

Identical structure to Phase 1's gate. All invariants must hold; Codex consult must surface no CRITICAL findings; production validation in test tenant must demonstrate the full Approve / Map / Ignore / Re-open flow end-to-end before merging.

### Step 7 — Stopping point (after Phase 1.5 ships)

Per design Section 7.3, the project hits an explicit stopping point. The next session does NOT start Phase 2 — it does comprehensive re-planning. Task 1.5.27 rewrites this `NEXT-SESSION-START-HERE.md` to instruct the post-Phase-1.5 session to:

- Research current AI-native thought leadership (the landscape will have evolved)
- Pull metrics from production behavior of Phase 1.5 (partial-contact rates, queue throughput, owner-approval response times)
- Decide whether Phase 2 is the right next move

Do NOT proactively start Phase 2 work even if there is context budget left. The stopping point is deliberate.

---

## Subagent dispatch best practices

Each subagent dispatch must include:

1. **The task block from the plan, in full.** Copy the entire `### Task N.M` section including Files, all Steps, code blocks, and commands. Do not paraphrase — the plan is self-contained for a reason.

2. **Required prior context.** The relevant File Structure entries from the plan, plus links to the design doc + this handoff doc. The subagent reads these before starting.

3. **Boundaries explicit.** What the subagent should NOT do: invent new schema fields, refactor adjacent unrelated code, skip the TDD red→green→commit cycle, deviate from the plan's commit messages.

4. **Acceptance evidence required.** The subagent must report back: (a) the exact commands run, (b) test output proving the failing test failed and the passing test passed, (c) the commit hash. Without this evidence, treat the task as not done.

5. **Cross-repo subagents have their own conventions.** When dispatching to `eq-frontend`, `eq-email-pipeline`, `eq-structured-graph-core`, or `eq-agent-action-core`, give the subagent the brief from `tasks/downstream/<repo>-<phase>-<scope>.md` plus the repo path. The cross-repo agent runs in that repo's working directory.

### Reviewing subagent output

After each subagent returns, check:

- Did the test red→green cycle actually happen? (The subagent could lie or skip it; evidence in the report is your only ground truth.)
- Does the commit hash exist in `git log`?
- Does the diff match what the plan asked for? `git show <hash>` is your friend.
- Did the subagent touch any files outside the plan's Files list? If yes, ask why before accepting.
- Run the test command yourself if the diff is non-trivial: `pytest <test path> -v`. Trust but verify.

If a subagent fails or returns incomplete work:
- Update the task status to `in_progress` (not `completed`)
- Dispatch a new subagent with feedback on what was missed
- Do not mark `completed` until the work actually passes verification

---

## Critical project invariants (must hold across all tasks)

Pulled from the project memory's feedback rules and locked decisions. Any task that violates these is wrong even if it appears to "work":

- **Contact ID consistency (`feedback_contact_id_consistency.md`):** Every contact always carries a UUIDv4 `contact_id`. Never store a name without an ID. Never MERGE on email in Neo4j (must MERGE on `(tenant_id, contact_id)`).

- **Tenant isolation (`feedback_tenant_isolation.md`):** Every Postgres query and every Neo4j query MUST include `tenant_id`. Never do cross-tenant lookups, joins, or fallbacks. If data doesn't exist within the tenant's scope, it doesn't exist — period.

- **Branch safety (`feedback_branch_safety.md`):** All work on feature branches. Rebase before merge. Test everything. Document as you go.

- **Downstream investigation (`feedback_downstream_investigation.md`):** When the plan dispatches cross-repo work, investigate the target repo thoroughly via the brief but do NOT refactor it broadly. Each cross-repo agent stays scoped.

- **Three-state branching (locked 2026-05-13):** Known account → contact; unknown business → queue signal, no contact; personal/internal → skip. NEVER fallback to anchor account for unknown attendees. This is the Option A decision and is non-negotiable.

- **Backend rejection over frontend trust:** Every ingestion path must reject when `account_id` cannot be resolved. The queue-hold path is the ONLY exemption.

- **First-owner-wins UPSERT:** `pending_account_mappings.owner_user_id` is never reassigned by routine UPSERT. Owner change only via explicit re-open escalation policy.

- **Outbox-backed durability:** `account_provisioning_outbox` is written in the SAME Postgres transaction as account materialization. Publishing to EventBridge happens after commit. Three-layer idempotency model documented in design Section 5.4.

- **Codex usage as recurring quality gate:** Codex consult runs at every phase boundary (Tasks 1.26 and 1.5.23). Plus after any non-trivial design deviation discovered during execution.

- **AI-native validation:** When making architectural calls during implementation, reference GraphRAG, agentic identity resolution, outbox/saga literature — NOT modern CRMs (Attio, HubSpot, Salesforce). This is the founder's product differentiation principle.

---

## Repository state (as of 2026-05-13 session end)

- **Current branch:** `feat/interim-results-param-add-account-v1` (committed: design doc revisions + implementation plan + handoff updates)
- **Main branch:** `main` (where Phase 1 will merge after acceptance)
- **Test tenant ID:** `11111111-1111-4111-8111-111111111111` (per `reference_test_tenant.md` — all data is test data, safe to seed and wipe)
- **Neon Postgres:** project `super-glitter-11265514` (eq-dev). Connection string in environment; never hard-code.
- **Neo4j Aura:** `neo4j+s://c6171c63.databases.neo4j.io` (shared by all graph services). Use `neo4j_structured` MCP only — never `neo4j_action`.
- **Prisma schema source of truth:** `/Users/peteroneil/eq-frontend/prisma/schema.prisma` (per `reference_prisma_schema_ownership.md`). All schema changes coordinate through eq-frontend.
- **Cross-repo paths:**
  - `/Users/peteroneil/eq-frontend` — schema + queue UI
  - `/Users/peteroneil/eq-email-pipeline` — calendar_sync, orchestrator, re-open trigger
  - `/Users/peteroneil/EQ-CORE/eq-structured-graph-core` — AccountCreated consumer (verify path; project layout may differ)
  - `/Users/peteroneil/EQ-CORE/eq-agent-action-core` — backend-worker invocation contract verification

---

## What NOT to do this session

- Do NOT re-open the Option A decision. It's locked.
- Do NOT skip the TDD red→green→commit cycle on any task. The plan was written assuming TDD; deviating produces untested code that the acceptance gates will catch later, painfully.
- Do NOT skip Codex consult after each phase. It's the recurring quality gate documented in design Section 8.4. Non-optional.
- Do NOT skip the test-data wipe in Phase 1.5 (T1.5.2). The wipe is what gates NOT NULL enforcement; skipping it leaves a broken schema.
- Do NOT proceed past a failed acceptance gate. Pause, surface the issue, get user input on next steps.
- Do NOT proactively start Phase 2 after Phase 1.5 ships. The stopping point is deliberate.
- Do NOT modify the design doc unless a subagent surfaces a genuinely new contradiction. If you do modify it, log the change in `project_contact_quality_initiative.md` decision log.
- Do NOT modify locked schema decisions (signals join table, outbox table shape, first-owner-wins UPSERT, archive lifecycle scope). These are closed.
- Do NOT commit subagent work with skipped tests or `--no-verify`. Hook failures must be fixed at root cause.
- Do NOT push directly to main. Phase 1 + Phase 1.5 each open a PR and merge after review.

---

## Context budget guidance

The orchestration mode is heavier on the orchestrator's context than the previous design-revision session was. Realistic expectations:

- **Phase 1 (~28 tasks) in a single session:** Plausible if subagents stay tight and you don't get pulled into deep review on every task. The plan was written to be self-contained per task, so subagents shouldn't need long back-and-forth.
- **Phase 1 + Phase 1.5 in one session:** Unlikely. Plan to hand off after Phase 1 acceptance.
- **One phase per session:** The expected cadence. Each phase ends at a natural acceptance gate that produces a clean handoff.

**Signs you should stop and hand off:**

- Context approaching budget limits (look for warnings or sluggish responses)
- A subagent has surfaced a finding that requires deep design re-engagement
- You're about to invoke writing-plans or another heavy-context skill mid-execution
- An acceptance gate has failed and the recovery work is non-trivial

When you stop, do a clean handoff: update auto-memory project file, rewrite this `NEXT-SESSION-START-HERE.md` to reflect the new state (which tasks landed, which are next, what blockers exist), commit the handoff changes.

---

## If something doesn't make sense

The conversation that produced these artifacts ran for many turns across multiple sessions. If you encounter a contradiction, surprise, or unclear instruction:

1. **First, check `project_contact_quality_initiative.md` Decision log.** The locked decisions take precedence over inferences from code or memory.
2. **Second, check the design doc.** It's been pressure-tested and Codex-reviewed; most contradictions resolve there.
3. **Third, check this handoff doc.** Look for explicit guidance on the scenario.
4. **Fourth, ask the user.** Surface the issue with a clear ELI10 framing per the AskUserQuestion conventions in `~/.claude/skills/gstack/context-restore/SKILL.md`. Default to asking when it's a product or strategic question; default to deciding when it's an implementation detail.

If a subagent's output conflicts with a locked decision, the locked decision wins. Flag the conflict in the subagent's review, document why the subagent's suggestion doesn't apply, proceed.

If a subagent surfaces a Codex-style finding that resembles one of the 15 already integrated, check the Codex review doc to see if it's a repeat or a new variant. Repeats are dismissed; new variants get logged as additions to the project memory.

---

## Suggested first actions for the next agent

1. Run `/context-restore` (gstack skill) to load any checkpoint state.
2. Read this doc in full.
3. Read `MEMORY.md`, `project_contact_quality_initiative.md`, the design doc, the implementation plan, in that order. Codex review and architecture doc are skim-only.
4. Briefly confirm understanding back to the user (one paragraph).
5. Invoke `superpowers:subagent-driven-development` for the canonical orchestration workflow.
6. Dispatch the first subagent for Task 0 (pre-flight). Verify the baseline test suite passes before going any further.
7. Proceed through Phase 1 in dependency order. Use parallel dispatch where the plan allows (T1.3-T1.10, T1.17-T1.19).
8. At Phase 1 acceptance gate, run the full verification suite + Codex consult. Hand off after Phase 1 ships unless context budget is genuinely fresh.

---

## Final note for the next agent

The user is a non-developer founder building a cutting-edge AI-native customer intelligence platform. The architectural standard is high. The work is grounded in emerging AI-native patterns (GraphRAG, agentic identity resolution, outbox/saga durability) — not legacy CRM patterns.

Your job as orchestrator is to keep the architectural standard high while moving through ~55 tasks efficiently. The plan is detailed enough that subagents should mostly Just Work; your value-add is selection, sequencing, parallelization, review, and recovery from subagent failures.

If you find yourself wanting to "improve" the plan during execution rather than executing it, stop. The plan was Codex-reviewed and self-reviewed; mid-execution improvements introduce risk. Genuine plan defects should be flagged to the user, not silently fixed.

Hold the architectural integrity. Hold the boundary between product decisions (user) and implementation decisions (you). Execute the plan; ship the work.
