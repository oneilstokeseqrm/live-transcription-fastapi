# Next Session Opening Prompt — paste at session start

The text below is sized to paste as the opening message of the next Claude session. It mirrors the structure of the 2026-05-15 PM prompt that opened the M3 execution session (which shipped M3 + M4 wiring as PR #17). The user wrote this artifact 2026-05-17 after the M3+M4 session closed, to ensure the next session can pick up cleanly.

---

```
You're working in /Users/peteroneil/EQ-CORE/live-transcription-fastapi.

This is a continuation session for the Contact Quality and Account-Anchoring
Initiative — a multi-phase data-quality project on an AI-native customer
intelligence platform. The implementation plan for Phase 1.5 was written
2026-05-15 AM. Since then:

- M0 + M1 + M2 + M1-hotfix shipped 2026-05-15 PM (DBOS substrate +
  UNIQUE INDEX live in production Neon).
- M3 (workflow definition + tests) + M4 (/approve route cutover)
  shipped together in PR #17 (2026-05-17). M4 was brought forward to
  resolve a pre-merge Codex P1 (the original M3 left /approve as a
  silently-broken endpoint with workers/ already deleted).
- Net code change since main: ~3700 / -2700 lines across the M3+M4
  branch.

Your job this session is to (a) verify the PR #17 production deploy,
(b) execute the production canary (deferred from M3+M4), (c) ship M5
(verified-contract tooling + the empty-content.text backfill fix), and
optionally (d) M3.5 (drop account_provisioning_outbox).

Before doing anything else, follow these steps IN ORDER:

1. Run `/context-restore`. You should find a checkpoint titled
   "phase-1.5-m3-m4-shipped-as-pr-17" dated 2026-05-17. Load it — the
   PR_STATE + LIMITATIONS frontmatter sections are load-bearing. If
   /context-restore returns NO_CHECKPOINTS, STOP and surface.

2. Read MEMORY.md (auto-loads). Confirm the project status reads
   PHASE_1.5_M3_M4_SHIPPED_M5_NEXT. If anything else, STOP and surface.

3. Verify pre-flight state:
   - `git status` — should be clean on `main` IF PR #17 was merged
     between sessions, OR on `phase-1.5/m3-workflow-tests` if not yet.
   - `gh pr view 17 --json state,mergedAt` — note whether merged.
   - `gh pr view 17 --json reviews,statusCheckRollup` — check Codex
     status if a re-review happened post-session.
   - `git stash list` — should be empty (stash@{0} was popped + merged
     into M3's first commit).
   - `curl -sS -o /dev/null -w "%{http_code}\n"
     https://live-transcription-fastapi-production.up.railway.app/health`
     should return 200.

4. **If PR #17 is NOT merged yet:** review it, address any post-session
   feedback, then merge. If it IS merged: verify production deployed
   successfully (Railway dashboard or `mcp__railway__deployment_status`)
   AND that the DBOS launch banner appears in logs with the expected
   executor_id pattern.

5. Read THESE DOCS IN THIS ORDER (mandatory, ~25 minutes total):

   a. THE CHECKPOINT (already loaded via /context-restore) — full
      record of the M3+M4 session's decisions, the 6 Codex rounds, the
      shared-test-tenant collision incident + handoff, and the
      empty-content.text limitation deferred to M5.

   b. THIS FILE — wayfinding + M5 scope + the 16 LOCKED decisions
      (grew from 14 last session).

   c. `docs/superpowers/specs/2026-05-15-initiative-context-snapshot.md`
      — standalone entry point for the whole initiative. Sections 5 +
      6 are load-bearing (DBOS substrate + 30 hard invariants).

   d. `docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md`
      — THE LOAD-BEARING IMPLEMENTATION PLAN (~1400 lines, now v6).
      For M5: §11 M5 + §13 M5 acceptance criteria + §10.5 tooling
      list. For the empty-content.text follow-up: §3.4 (consumer
      Pydantic models) + §6.6 (emit step contract).

   e. `tasks/lessons.md` — bottom entries. CRITICAL: read the
      shared-infrastructure collision lesson (2026-05-16). The
      previous session's test infrastructure DELETEs against the
      shared test tenant wiped another agent's seed data. The
      `requires_db_write` marker now enforces opt-in; honor it.

   f. PR #17 description — comprehensive narrative of what shipped,
      the 6 Codex rounds + their fold-ins, the known limitations
      deferred to M5.

   On-demand reference (read when work requires it, NOT all up front):
   - `docs/superpowers/specs/2026-05-15-dbos-scaling-decisions.md` —
     locked single-replica V1 + multi-replica-ready posture. DO NOT
     revisit --workers 1.
   - `tasks/downstream/test-discipline-gaps-2026-05-15.md` — five
     expectations. M5 implements Items 4 + 5 (verify_schema.py +
     verify_consumer_contracts.py).
   - `tasks/downstream/action-item-graph.md` +
     `eq-structured-graph-core.md` — consumer change briefs. M5's
     empty-content.text fix coordinates with these.

6. After reading, briefly confirm your understanding of where the
   prior session left off and what you plan to do this session (one
   paragraph) before starting work.

7. EXECUTE in this order:

   STEP 1 — Verify PR #17 production deploy
   - Confirm Railway deployed the merge commit.
   - Confirm DBOS launched with executor_id matching RAILWAY_REPLICA_ID.
   - Confirm /health 200.
   - If deploy failed, investigate before proceeding.

   STEP 2 — Production canary (deferred from M3+M4)
   - Seed a synthetic queue entry via Neon MCP under test tenant
     11111111-1111-4111-8111-111111111111. CRITICAL: announce the
     write to the user FIRST + check for concurrent agents via
     `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head`.
     The shared-tenant collision protocol applies.
   - Mint an internal JWT for the test tenant.
   - POST /queue/{id}/approve with the JWT.
   - Verify 202 response + workflow_id.
   - Poll Neon's dbos.workflow_status for the workflow_id until
     terminal state (success/error). Expected runtime: 30-90s
     dominated by the agent enrich call.
   - Verify: accounts row + account_domains row + contacts rows +
     interaction_contact_links rows all exist for the workflow.
   - Verify EventBridge emission (CloudTrail OR synthetic SQS
     consumer — TBD per available tooling).
   - Teardown the test rows. Mandatory.

   STEP 3 — Ship M5 (verified-contract tooling + content fix)

   Three sub-deliverables in one PR:

   (i) `scripts/verify_schema.py` (test-discipline-gaps Item 4) — runs
       EXPLAIN against the live Neon project for an arbitrary SQL
       text + reports missing-column/missing-table errors. Catches
       the class of bug that produced the 2026-05-15 silent
       regression at design time. Plan §10.5.

   (ii) `scripts/verify_consumer_contracts.py` (Item 5) — validates a
        proposed envelope (source + detail-type + extras shape)
        against live EventBridge rules + downstream Pydantic models.
        Catches the class of bug that produced the
        action-item-graph SourceType drift incident. Plan §10.5.

   (iii) Empty-content.text fix for backfill emission. Options
         documented in PR #17 + the round-6 Codex review. User
         agreed last session to ship M3+M4 with the limitation +
         address in M5. Pick ONE of:
         - Pull content from interaction_summaries.summary_content
           (Lane 2's post-processing output)
         - Add an extras flag `is_backfill=true` + coordinate with
           downstream consumers (action-item-graph,
           eq-structured-graph-core) to detect + skip content-
           dependent processing
         - Some other approach surfaced by re-reading the consumer
           change briefs.
         Surface to user before committing to one — this is a
         strategic decision about the downstream contract.

   STEP 4 — `/review` skill checklist update (test-discipline-gaps
   Items 4 + 5) — add "Live schema probe" + "Cross-service contracts"
   sections to the project's review checklist. Half-session of
   documentation work.

   STEP 5 — M3.5 if context allows: drop `account_provisioning_outbox`
   from production Neon via a coordinated Prisma migration in
   eq-frontend. The outbox is now dead code; no writers in M3.
   Acceptance: `grep -rn account_provisioning_outbox` returns zero
   hits across all repos.

8. PRE-MERGE RITUAL (codified from prior sessions):
   - Run `codex review --base main --title "Phase 1.5 M5: ..."` on
     the M5 diff BEFORE requesting merge. Treat P0/P1 as merge
     blockers. P2/P3: judgment call.
   - 6-round-cap rule from PR #17: if you've done 4+ Codex rounds
     and findings are narrow edges (P2/P3 only), surface to user
     about ship-vs-keep-iterating. The user explicitly stopped the
     prior session at round 6 because diminishing returns.

9. SHARED-INFRASTRUCTURE-COLLISION PROTOCOL (LOCKED 2026-05-16,
   STRENGTHENED 2026-05-17)

   Two layers of protection:

   Layer 1 — Other agents:
   - Before ANY destructive SQL on production Neon test tenant:
     `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head`
     Files modified in last hour = hazard. Pause + ask user.
   - The `eq-synthetic-date-generation` agent had an active inject
     during the 2026-05-16 session that was wiped by the test
     teardown. This rule prevents a recurrence.

   Layer 2 — The user's own seed data:
   - The PR #17 session ran `RUN_DESTRUCTIVE_TESTS=1` pytest
     multiple times AND a one-shot 8-DELETE cleanup. Each run
     wiped accounts under the test tenant → opportunities + other
     dependents CASCADE-deleted. User said 2026-05-17: "you
     cleaned my account and opportunities again. You weren't
     supposed to do that."
   - LOCKED decision 17: **ask the user PER BATCH** of destructive
     runs, not just per-session. The env-var gate is necessary
     but not sufficient. Examples requiring confirmation:
       - Running `RUN_DESTRUCTIVE_TESTS=1 pytest` (the teardown wipes data)
       - Running an ad-hoc DELETE cleanup
       - Running any tool that triggers `mcp__neon__run_sql` with
         a DELETE/UPDATE/TRUNCATE statement
       - The production canary (Step 2 of this session) — seed +
         teardown both require confirmation

   The TEST TENANT IS EMPTY AT START OF THIS SESSION (per the
   PR #17 checkpoint). If the user re-seeded data between sessions,
   treat as live data — do not touch without explicit confirmation.

10. After M5 deploys, run /context-save and update
    `docs/superpowers/specs/NEXT-SESSION-START-HERE.md` +
    `docs/superpowers/specs/{new-date}-next-session-prompt.md` for
    the next session (likely Phase 2 design or operational tooling).

ANTI-ANCHORING

The plan has 17 LOCKED decisions (grew from 14 in PR #17 + 1 added
post-session 2026-05-17). Do NOT re-litigate unless you find NEW
evidence contradicting the prior rationale. The 17 items:

(1) Substrate is DBOS.
(2) Single replica V1 + executor_id-from-RAILWAY_REPLICA_ID.
(3) EventBridge Path A (EnvelopeV1.* with Source=com.yourapp.transcription).
(4) Workflow ID = f"queue-{queue_id}:approval-{approval_attempt_id}".
(5) /approve reserves synchronously then enqueues workflow.
(6) Drop account_provisioning_outbox post-M3 (M3.5).
(7) account_domains as idempotency anchor.
(8) Emit extras.contacts metadata.
(9) Closed INTERACTION_TYPE_TO_DETAIL_TYPE lookup (5 entries:
    transcript / meeting / note / email / batch_upload).
(10) Test infrastructure: Option B + @pytest.mark.requires_db_write
     opt-in + mandatory teardown per test.
(11) DBOS v2.x sync launch()/destroy() AT FastAPI lifespan;
     get_event_async/set_event_async INSIDE async @DBOS.step.
(12) websockets pin 14.2 + deepgram compat shim.
(13) DBOS_SYSTEM_DATABASE_URL is REQUIRED.
(14) Codex review BEFORE merging is the gate. **4-round soft cap**
     on iterations before surfacing diminishing-returns to user.
(15) SQLAlchemy 2.0.49 truncates `:name::uuid` bindparam to
     `:name_minus_last_char`. Use `CAST(:name AS uuid)` form.
(16) Materialization REQUIRES real raw_interactions row before
     materializing. Placeholder pattern REMOVED. If absent → raise
     ValueError → DBOS retry OR /map 503.
(17) **PER-ACTION confirmation for destructive ops on shared test
     tenant.** Env-var gating (RUN_DESTRUCTIVE_TESTS=1) is necessary
     but NOT sufficient — the marker prevents accidental writes by
     other agents but does NOT prevent ME from wiping the user's
     own seed data. Ask the user PER BATCH of destructive runs.
     (NEW 2026-05-17 post-PR-#17; user flagged test-tenant
     accounts/opportunities were wiped during that session.)

VERIFIED-CONTRACTS DISCIPLINE — RE-PROBE LIST

Plan §3 contracts were probed 2026-05-15 + re-probed 2026-05-15 PM +
verified-unchanged 2026-05-17 (PR #17). For M5 you re-probe AGAIN
before writing code — discipline is non-substitutable per
tasks/lessons.md "Cross-service contract verification at design
time."

Tables to re-probe via mcp__neon__run_sql (project
super-glitter-11265514, branch production, database neondb):

| Table | Why M5 cares |
|---|---|
| pending_account_mappings | verify_schema.py target |
| pending_account_mapping_signals | M5 emit-fix may pull source_type |
| accounts | verify_schema.py probes this |
| account_domains | UNIQUE INDEX (tenant_id, domain) — LOCKED 7 |
| contacts | verify_schema.py target |
| raw_interactions | M5 P1 candidate: confirm raw_text column exists + how populated (currently NULL per intelligence_service.py) |
| interaction_summaries | UNIQUE INDEX (interaction_id); potential source for M5 backfill content fix |
| interaction_contact_links | M2 UNIQUE INDEX (interaction_id, contact_id) MUST still be live |
| interaction_account_links | tenant-less link table; teardown chain dependency |
| account_provisioning_outbox | M3.5 drop target; confirm still present |

External contracts to re-probe:

- https://eq-agent-action-core-production.up.railway.app/openapi.json
  — for verify_consumer_contracts.py + AccountProfile schema
  (still missing per plan §10.1)
- EventBridge rules via mcp__aws-api__call_aws:
  `aws events list-rules --event-bus-name default`
  Two live rules: action-item-graph-rule + eq-structured-graph-rule.
  Both filter on source ["com.yourapp.transcription",
  "com.eq.email-pipeline"] + detail-type ["EnvelopeV1.transcript",
  "EnvelopeV1.note", "EnvelopeV1.meeting", "EnvelopeV1.email"].
- Consumer Pydantic models in sibling repos:
  - action-item-graph/src/action_item_graph/models/envelope.py:34-43
    (SourceType enum; missing zoom+generic 2026-05-15; that repo's
    agent is fixing — independent of M5)
  - eq-structured-graph-core/app/models/envelope.py:23-42
    (loose source: str, no enum constraint)

If ANY drift from PR #17's assumptions, that's a P0 finding for M5
— fix BEFORE merging.

PRODUCTION CREDENTIALS + IDS (load-bearing reference)

Restated here so the prompt is self-contained:

- Neon Postgres (eq-dev): project super-glitter-11265514, branch
  production, database neondb. Direct connection (no -pooler) for
  DBOS_SYSTEM_DATABASE_URL.
- Test tenant: 11111111-1111-4111-8111-111111111111. ALL DATA
  UNDER THIS TENANT IS TEST DATA. See LOCKED decision 17 before
  any destructive op.
- Test user (FK target for owner_user_id):
  b0000000-0000-4000-8000-000000000002.
- Railway FastAPI: project 847cfa5a-b77c-4fb0-95e4-b20e8773c23e,
  service 59a69f3d-9a24-4041-942a-891c4a81c5fb,
  env e4c5ec15-1931-4632-9e58-92d9c6be4261,
  URL https://live-transcription-fastapi-production.up.railway.app.
- Railway eq-agent-action-core:
  URL https://eq-agent-action-core-production.up.railway.app,
  service 3036ea0f-afc9-4bc4-889d-c98617d81e96.
- Internal JWT: HS256, INTERNAL_JWT_SECRET, iss=eq-frontend,
  aud=eq-backend, claims: tenant_id, user_id, optional pg_user_id.
- AWS: EventBridge bus 'default' (EVENTBRIDGE_BUS_NAME);
  AWS_REGION=us-east-1; keys in Railway env.
- Neo4j: Aura instance c6171c63, neo4j+s://c6171c63.databases.neo4j.io.

TEST TENANT STATE AT THIS SESSION START

EMPTY as of PR #17 session end (per the checkpoint). The PR #17
session ran multiple destructive ops. If the user has re-seeded
data between sessions, treat as live — do NOT touch without
explicit confirmation per LOCKED decision 17.

USER POSTURE (load-bearing)

Non-developer founder. Make confident technical decisions; surface
only product/strategic decisions. Work without stopping for
clarifying questions; make the reasonable call and continue; user
redirects if needed.

Strict OSS only (no SSPL, no BSL).

Strong preference for architectural correctness over short-term
shortcuts. Strong preference for cutting-edge 2026 AI-native
patterns. NO sunk-cost preservation.

**Context economy matters.** The user surfaced this 2026-05-16:
"how heavy is the context in this session?" The 6 Codex rounds in
PR #17 each consumed significant context. Per the new 4-round soft
cap (decision #14), surface to the user when iterations are
producing diminishing returns rather than burning context
indefinitely.

The user said 2026-05-16: "all data in the system is test data and
I don't have any production users." Architectural choices CAN
accept short-term limitations that would block a production-traffic
ship. The empty-content.text limitation in PR #17 was deferred on
this basis.

SCOPE OF THIS SESSION — EXPLICIT

In scope: verify PR #17 deploy → production canary → M5 (tooling +
content fix) → optional M3.5 (drop outbox).

NOT in scope:
- Phase 2 design (sketched in plan §9; don't expand)
- Re-evaluating --workers 1
- Touching action-item-graph or eq-structured-graph-core repos
  beyond the M5 coordination touchpoints
- Fixing the 50 pre-existing test failures
- Touching the eq-frontend feat/deal-health-v8-chrome branch

STOP CONDITIONS

Stop and surface to the user if:
- /context-restore returns NO_CHECKPOINTS
- MEMORY.md status isn't PHASE_1.5_M3_M4_SHIPPED_M5_NEXT
- PR #17 has unmerged conflicts or new Codex findings
- Production deploy from PR #17 merge FAILED or shows errors
- Canary fails or shows unexpected behavior (likely indicates a
  design gap, not a bug)
- The empty-content.text fix decision needs your input on which of
  the three approaches to take
- You discover NEW evidence that any of the 16 LOCKED decisions
  needs reconsideration
- More than 4 Codex rounds without a clean pass during M5

REPOSITORY STATE (as of session end 2026-05-17)

- Branch `phase-1.5/m3-workflow-tests`: 11 commits, pushed to origin
- PR #17 OPEN at https://github.com/oneilstokeseqrm/live-transcription-fastapi/pull/17
  (state as of session end — may have merged or evolved by next
   session start; check via `gh pr view 17`)
- Local main: NOT yet at the PR's HEAD if not merged.
- Working tree: clean
- Stash list: empty
- Production Railway: running e334638 (the M1 hotfix) since last
  session; M3+M4 deploys on PR #17 merge. /health 200 as of last
  check.
- Production Neon (super-glitter-11265514, branch production):
  - dbos.* schema (11 tables)
  - interaction_contact_links UNIQUE INDEX live
  - account_provisioning_outbox table still present (drop in M3.5)
  - test tenant 11111111-1111-4111-8111-111111111111: empty as of
    session end (teardown ran after the final RUN_DESTRUCTIVE_TESTS=1
    run)
- Test suite: 50 default pass + 17 skipped; 67 pass with
  RUN_DESTRUCTIVE_TESTS=1; 50 pre-existing failures unrelated

When you finish M5:
1. PR opened (with codex review BEFORE merge per the new gate)
2. Production deploy verified
3. /context-save checkpoint
4. MEMORY.md status updated to next state
5. New paste-ready prompt for the next session

The plan is the load-bearing artifact. PR #17 description is the
detailed narrative of what shipped (read it for context). The
shared-infrastructure-collision lesson is load-bearing for any
write to production Neon's test tenant.

When in doubt, read the plan. When the plan is silent, surface to
user. The user pays for thinking + correct execution + careful
coordination, not typing.
```

---

## Notes for the user writing this

If you want to add anything specific to the next session before pasting (e.g., feedback on PR #17 review, new scope adjustments, a strategic shift), append it AFTER the closing backticks but BEFORE the agent starts reading. The agent will treat anything outside the prompt block as authoritative.

The prompt block above mirrors the structure of the 2026-05-15 PM session-opening prompt. Key changes vs that prompt:

- The "STARTING CONTEXT" assumes M3 + M4 are in PR #17 (vs the prior version which assumed M3 hadn't started).
- The 16 LOCKED decisions list grew from 14 (added the SQLAlchemy CAST rule + the no-placeholder-pattern rule).
- Mandatory read order added PR #17's description.
- New explicit "shared-infrastructure-collision protocol" section codifying the 2026-05-16 incident response.
- New "4-round Codex cap" soft rule based on the 6-round PR #17 experience.
- M5 scope is more concrete than M3+M4 was (three sub-deliverables identified).

The /context-save checkpoint at `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/<timestamp>-phase-1.5-m3-m4-shipped-as-pr-17.md` is the load-bearing artifact this prompt points to.
