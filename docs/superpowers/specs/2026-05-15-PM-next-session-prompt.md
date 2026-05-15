# Next Session Opening Prompt — paste at session start

The text below is sized to paste as the opening message of the next Claude session. It mirrors the structure of the prompt that opened the 2026-05-15 AM execution session (which shipped M0 + M1 + M2 + the M1 hotfix). The user wrote this artifact 2026-05-15 PM after that session closed, to ensure the next session can pick up M3 cleanly.

---

```
You're working in /Users/peteroneil/EQ-CORE/live-transcription-fastapi.

This is a continuation session for the Contact Quality and Account-Anchoring
Initiative — a multi-phase data-quality project on an AI-native customer
intelligence platform. The implementation plan for Phase 1.5 was written
2026-05-15 AM and milestones M0 through M2 (plus an M1 hotfix for 2 Codex P1s)
were executed 2026-05-15 PM. DBOS substrate is INSTALLED and RUNNING in
production. Two database changes are LIVE in production Neon. Live
transcription verified post-hotfix.

Your job this session is to EXECUTE M3 — the workflow definition + tests
milestone. Plan §11 explicitly calls M3 "probably a session by itself."
Pace accordingly. M3 is dead-code at end (no route wired); M4 (queue cutover
+ canary) is the risk milestone after M3.

Before doing anything else, follow these steps IN ORDER:

1. Run `/context-restore`. You should find a checkpoint titled
   "phase-1.5-m0-m1-m2-shipped-plus-hotfix-m3-next" dated 2026-05-15.
   Load it. The PRODUCTION_STATE section and the SHIPPED_PRS frontmatter
   are load-bearing. If /context-restore returns NO_CHECKPOINTS, STOP and
   surface — that's a sync gap.

2. Read MEMORY.md (auto-loads). Confirm the project status reads
   PHASE_1.5_M0_M1_M2_SHIPPED_M3_NEXT. If anything else, STOP and surface.

3. Verify pre-flight state:
   - `git status` — should be clean on `main` at `e334638`
   - `git log --oneline -5` — top entry should be the M1 hotfix squash-merge
     (`e334638 fix(phase-1.5/m1-hotfix): close 2 Codex P1s from post-merge review (#15)`)
   - `git stash list` — should show 2 entries; stash@{0} is the plan v4 §20
     update from 2026-05-15 AM
   - `curl -sS -o /dev/null -w "%{http_code}\n" https://live-transcription-fastapi-production.up.railway.app/health`
     should return 200

4. **Pop the stash immediately:**
   `git stash pop stash@{0}`
   This puts `docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md`
   v4 §20 update into your working tree. Commit it as part of M3's first
   commit. If the pop conflicts, the stash content is small enough to
   reconstruct manually from the checkpoint's "What shipped this session"
   section.

5. Read THESE DOCS IN THIS ORDER (mandatory, ~30 minutes total):

   a. `docs/superpowers/specs/NEXT-SESSION-START-HERE.md` — Your specific
      wayfinding for THIS session. Rewritten 2026-05-15 PM for M3
      execution. Has the 14 LOCKED decisions, mandatory read order,
      pre-flight checks, stop conditions, and the "Codex review BEFORE
      merging" gate.

   b. `docs/superpowers/specs/2026-05-15-initiative-context-snapshot.md` —
      Standalone entry point for the whole initiative. Section 5 reflects
      the locked DBOS decision; Section 6 has 30 numbered hard invariants
      any implementation must preserve.

   c. `docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md` —
      THE LOAD-BEARING IMPLEMENTATION PLAN (~1200 lines, v4 after the
      prior session's amendments). Read in full. For M3 specifically:
      §5 (file-by-file plan), §6 (workflow detail), §7 (test-discipline
      expectations per component), §13 M3 (acceptance criteria),
      §14 (component × test-discipline matrix). §20 (revision history)
      records every drift and correction so you don't re-derive — the
      v4 entry covers the M1 hotfix + M2 split.

   d. `docs/superpowers/specs/2026-05-15-dbos-scaling-decisions.md` —
      Locked single-replica V1 + multi-replica-ready posture + Phase-2
      trigger conditions for the orphan-workflow detector. DO NOT
      revisit --workers 1 unless a Phase-2 trigger fires.

   e. `tasks/downstream/test-discipline-gaps-2026-05-15.md` — All five
      expectations with acceptance criteria. M3 implements Items 1, 2, 3
      in the workflow's test coverage.

   f. `tasks/downstream/action-item-graph.md` AND
      `tasks/downstream/eq-structured-graph-core.md` — Consumer change
      briefs. The extras.contacts requirement is locked there; M3's
      emit-step coding must produce it.

   On-demand reference (read when work requires it, NOT all up front):
   - `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md`
     Sections 1, 7.2, 8.5 (DBOS-revised; other sections stable)
   - Previous-session checkpoints at
     `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/`
     — three most recent are from 2026-05-15 (AM + mid + PM)
   - `tasks/lessons.md` — bottom entries; capture 3 NEW lessons from the
     prior session into this file as part of M3's PR (see step 6 below)

6. After reading, briefly confirm your understanding of where the prior
   session left off and what you plan to do this session (one paragraph)
   before starting M3.

7. EXECUTE M3 per plan §11 + per NEXT-SESSION-START-HERE's "M3 — Workflow
   definition + tests" section:

   - Create `services/account_provisioning/` package: `__init__.py`,
     `workflow.py`, `steps.py`, `types.py`, `eventbridge_emit.py`.
   - MOVE `workers/materialization.py` → `services/account_provisioning/materialization.py`.
     Remove INSERT_OUTBOX_SQL (lines 230-251). Remove in-memory `linked_pairs`
     dedup (line 161). Change link INSERT to ON CONFLICT (interaction_id,
     contact_id) DO NOTHING — the M2 unique index is live in production.
   - Update `routers/queue_actions.py` import path from `workers.materialization`
     to `services.account_provisioning.materialization`.
   - REWRITE `services/agent_action_core_client.py` to the verified
     contract from plan §3.2: `enrich(url, effort, jwt) → AccountProfile`
     and `get_run(run_id, jwt) → AccountProfile`.
   - WRITE all M3 tests per plan §7 + §13:
     - tests/unit/account_provisioning/test_workflow.py
     - tests/unit/account_provisioning/test_steps.py (real Neon test
       session — NO MagicMock for the session)
     - tests/unit/account_provisioning/test_eventbridge_emit.py
     - tests/unit/account_provisioning/test_agent_client.py
     - tests/contract/test_agent_enrich_response_shape.py
       (contract-pinning against the live production agent)
     - tests/integration/account_provisioning/test_workflow_e2e.py
     - tests/integration/account_provisioning/test_reopen.py
     - tests/integration/account_provisioning/test_crash_recovery.py
   - Workflow is DEAD CODE at end of M3 — no route refers to it yet.
   - Fold the stashed plan v4 §20 update into M3's first commit.
   - Capture 3 new lessons into tasks/lessons.md as part of M3's PR:
     (a) Codex review BEFORE merging (it's a merge gate, not a follow-up);
     (b) imports don't catch keyword-arg removals in transitive dep upgrades;
     (c) coordinated multi-repo schema migrations need explicit code-lifecycle
     sequencing.

8. PRE-MERGE RITUAL (lesson from prior session, codified for M3+):
   - Run `/codex review` on the M3 diff BEFORE requesting merge. This is
     the new gate. If Codex finds P1s, fold them in BEFORE merge — not
     after as a hotfix (which is what the prior session had to do for M1).

9. After M3 merges + deploys (workflow is dead code; deploy is no-behavior-
   change), EXECUTE M3.5 if context budget allows:
   - Small cross-repo PR in eq-frontend.
   - New Prisma migration directory:
     `prisma/migrations/{timestamp}_drop_account_provisioning_outbox/migration.sql`
   - Contents: `DROP TABLE IF EXISTS "account_provisioning_outbox" CASCADE;`
   - Apply via Neon MCP prepare/complete flow.
   - Acceptance: outbox table removed from production Neon; grep -rn
     `account_provisioning_outbox` returns zero hits.

10. At end of session, run /context-save and update
    `docs/superpowers/specs/NEXT-SESSION-START-HERE.md` for the next session
    (likely M4 — queue route cutover + canary).

ANTI-ANCHORING INSTRUCTIONS

The plan has 14 LOCKED decisions. Do NOT re-litigate unless you find NEW
evidence that contradicts the prior rationale. The 14 items:

(1) Substrate is DBOS.
(2) Single replica V1 + executor_id-from-RAILWAY_REPLICA_ID.
(3) EventBridge Path A (EnvelopeV1.* with Source=com.yourapp.transcription).
(4) Workflow ID = f"queue-{queue_id}:approval-{approval_attempt_id}".
(5) /approve reserves synchronously before workflow start.
(6) Drop account_provisioning_outbox post-M3 (DEFERRED from M2 to M3.5).
(7) account_domains is the idempotency anchor (not accounts.name).
(8) Emit extras.contacts metadata.
(9) Closed INTERACTION_TYPE_TO_DETAIL_TYPE lookup fails-loud on unknown types.
(10) Test infrastructure for M3: Option B (test-tenant scoping in production)
     with mandatory teardown per test. Migration to Option A (Neon test
     branch) gated on first real customer data.
(11) DBOS v2.x is sync launch()/destroy() (NOT async).
(12) websockets pin 14.2 + deepgram compat shim in
     services/deepgram_websockets_compat.py.
(13) DBOS_SYSTEM_DATABASE_URL is REQUIRED (build_dbos_config raises if unset).
(14) Codex review BEFORE merging is the gate.

If a Codex review on M3's PR substantively disagrees with the plan in a
way the plan didn't anticipate, STOP and surface (this is how the prior
session caught + fixed 2 P1s, AND how the original Hatchet pick was
pivoted to DBOS). Routine Codex findings — fold them in per usual review
discipline.

VERIFIED-CONTRACTS DISCIPLINE

The plan's §3 contracts were probed 2026-05-15 AM. Hours have passed. The
M2 migration added a unique index. Before writing SQL that touches a table
the plan cites, re-probe via `mcp__neon__run_sql` against the live project
(`super-glitter-11265514`) — the plan calls this out. Same for the agent's
OpenAPI (M3's contract-pinning test is the guard until they publish
AccountProfile). Same for EventBridge rules.

For M3 specifically, re-probe:
- `interaction_contact_links` — confirm UNIQUE INDEX
  `interaction_contact_links_interaction_id_contact_id_key` on
  (interaction_id, contact_id) is still active.
- `account_provisioning_outbox` — confirm it still exists (drop deferred
  to M3.5).
- `accounts`, `account_domains`, `contacts`, `raw_interactions`,
  `interaction_summaries`, `pending_account_mappings`,
  `pending_account_mapping_signals` — before writing the workflow's SQL.
- `https://eq-agent-action-core-production.up.railway.app/openapi.json` —
  confirm /api/enrich contract is unchanged.

USER POSTURE (load-bearing)

- Non-developer founder. Make confident technical decisions; surface only
  product/strategic decisions. Work without stopping for clarifying
  questions; make the reasonable call and continue; user redirects if
  needed.

- The user cares about: cutting-edge 2026 AI-native architecture;
  architectural correctness over short-term shortcuts; strict OSS only
  (no SSPL, no BSL, no source-available); multi-session continuity;
  ROBUST PLANNING + LIVE SMOKE TESTS (explicitly emphasized prior session).

- The user does NOT care about: sunk-cost preservation; hitting deadlines
  over correctness; patterns not representing 2026 best practice.

SCOPE OF THIS SESSION — EXPLICIT

In scope: M3 (workflow + tests) and M3.5 (drop outbox) if context allows.
Each is a self-contained PR.

NOT in scope:
- M4 (route cutover + canary) — out unless M3 finishes with substantial
  context remaining
- M5 (tooling + checklist updates)
- Phase 2 / Phase 3 design (sketched in plan §9; don't expand)
- Re-evaluating --workers 1
- Fixing the 50 pre-existing test failures (Phase 1 schema-drift;
  tracked separately under test-discipline-gaps Items 1-3)
- Touching action-item-graph or eq-structured-graph-core repos (their
  agents)
- Cleaning up the orphaned `feat/deal-health-v8-chrome` local branch in
  eq-frontend (the other agent's; remote is [gone] but local is harmless)

STOP CONDITIONS

Stop and surface to the user if:
- /context-restore returns NO_CHECKPOINTS
- MEMORY.md status isn't PHASE_1.5_M0_M1_M2_SHIPPED_M3_NEXT
- Any pre-flight reveals state different from this prompt
- Live contract probes return results contradicting §3 of plan in a way
  that would change architectural decisions
- Codex review on M3 PR substantively disagrees with the plan in a way
  the plan didn't anticipate
- You discover anything suggesting one of the 14 LOCKED decisions needs
  reconsideration with NEW evidence
- M3 production canary (in M4, not this session) shows unexpected behavior
- Any handoff doc references files that don't exist (sync gap)
- The stash pop conflicts in a way you can't trivially resolve

REPOSITORY STATE (as of last session end, 2026-05-15 PM)

- Main HEAD: e334638 (M1 hotfix squash-merge)
- Recent commit log:
  e334638 fix(phase-1.5/m1-hotfix): close 2 Codex P1s from post-merge review (#15)
  dc0806c feat(phase-1.5/m1): install DBOS substrate + FastAPI lifespan integration (#14)
  607377d docs(handoff): rewrite NEXT-SESSION-START-HERE for executing session (M0-M5)
  ...
- Working tree: clean (one stash pending to pop)
- Local + remote: both at e334638, 0/0
- Production: FastAPI Railway service running `uvicorn --workers 1` with
  DBOS launched (executor_id from RAILWAY_REPLICA_ID), connected to direct
  Neon Postgres for system database; deepgram compat shim active;
  /listen + /health both verified working.
- Production Neon (super-glitter-11265514, branch production):
  - dbos.* schema (11 tables) — created by M1 deploy
  - interaction_contact_links_interaction_id_contact_id_key UNIQUE INDEX
    — applied by M2
  - account_provisioning_outbox table — still present (drop deferred to
    M3.5)
- Test suite: 338 passing, 50 pre-existing failing (unrelated to anything
  this milestone touches; tracked under test-discipline-gaps)

When you finish each milestone:
1. PR opened (with /codex review run BEFORE merge per the new gate)
2. Acceptance criteria from plan §13 checked off in PR description
3. Commit pushed; merge after Codex pass + your review
4. Production redeploy verified (workflow is dead code in M3; no behavior
   change expected)
5. Updated MEMORY.md status string + project memory frontmatter
6. A clean handoff note in the session at the end

The plan is the load-bearing artifact. The scaling-decisions spec is
load-bearing for any replica-count decision. The 3 new lessons from prior
session are load-bearing for process discipline going forward.

When in doubt, read the plan. When the plan is silent, read the
scaling-decisions spec. When still in doubt, surface to the user as a
product/strategic decision.

The user is paying for thinking + correct execution, not typing. M3 is
the substantial-thinking milestone. Pace accordingly. Don't rush the tail.
```

---

## Notes for the user writing this

If you want to add anything specific to the next session before pasting (e.g., a particular concern, a strategic shift, a new constraint), append it AFTER the closing backticks but BEFORE the agent starts reading. The agent will treat anything outside the prompt block as authoritative.

The prompt block above mirrors the structure of the 2026-05-15 AM session-opening prompt that worked well. Key changes vs that prompt:

- The "STARTING CONTEXT" assumes M0/M1/M2 + hotfix are done (vs the prior version which assumed nothing was shipped).
- The 14 LOCKED decisions list grew from 9 (added the 5 new ones from M1's drifts + hotfix + test infrastructure decision).
- The mandatory read order dropped the substrate-decision audit-trail docs (no longer relevant).
- Added the "Codex review BEFORE merging" pre-merge ritual explicitly.
- M3 scope expanded from a generic "execute M3" to a step-by-step list of files + tests, based on what the prior session learned would have been useful to pre-state.

The /context-save checkpoint at `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/20260515-132118-phase-1.5-m0-m1-m2-shipped-plus-hotfix-m3-next.md` is the load-bearing artifact this prompt points to. If for any reason /context-restore can't find it, the prompt is still usable but the agent will need to read the checkpoint file directly via Read tool.
