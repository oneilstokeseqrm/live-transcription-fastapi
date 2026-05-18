# Next Session Opening Prompt (2026-05-19, post-M4-DEPLOYED)

Paste the block below as the opening message of the next Claude session. Written 2026-05-18 evening after M4 of the Phase-1-email-pipeline cold-inbound fix was merged AND deployed (PR #10 → `6fa181a`) with Railway deployment `756b96e4` SUCCESS and `/api/health` 200 (all 3 checks ok). The full cold-inbound capture pipeline is BUILT, DEPLOYED, AND LIVE — M5 is the verification milestone that signs off the whole Phase-1-email-pipeline initiative.

**Important:** the next session is **VERIFICATION** — M5 production E2E + rollback drill + plan §11 acceptance invariants checklist. NOT new feature work (unless an E2E reveals a real bug).

---

```
You're picking up the Contact Quality and Account-Anchoring Initiative —
a multi-phase data-quality project on an AI-native customer intelligence
platform.

The prior session (2026-05-18 evening) shipped Phase-1-email-pipeline M4 —
the eq-email-pipeline orchestrator branch + atomic upsert_thread rewrite.
M4 PR #10 merged as `6fa181a` at 2026-05-18 evening. Railway deployment
`756b96e4` reached SUCCESS. /api/health returns 200 with all 3 checks
(postgres + neo4j + eventbridge) OK. The switch is FLIPPED on
cold-inbound capture — the very next cold-inbound from an unknown
business sender will trigger the full pipeline:

  orchestrator §4.2 → pending_interactions row + queue entry + signals
  → admin /approve → M2's workflow promote step → EmailPromoted fires
  → M3's SQS subscriber → Neo4j + Pinecone + summary enrichment.

Production state at session start:

  Phase-1-email-pipeline M1 (eq-frontend Prisma migration):  de586bbc
  Phase-1-email-pipeline M2 (live-transcription-fastapi):   756575d7
  Phase-1-email-pipeline M3 (eq-email-pipeline subscriber): 85c0295
  Phase-1-email-pipeline M4 (eq-email-pipeline orchestrator): 6fa181a

  All four merged + deployed + verified.

Your job THIS session is to execute M5 — production E2E + rollback drill
+ plan §11 acceptance invariants verification. This is the milestone
that SIGNS OFF THE WHOLE Phase-1-email-pipeline initiative. The plan
is locked at eq-email-pipeline:033626a; M5 is execution of the
verification protocol, NOT plan revision.

CRITICAL: M5 is verification-focused, NOT implementation-focused. If
E2E surfaces a real bug, surface to user — DO NOT silently fix in M5
scope. The decision tree:
  * Plan §11 invariant fails AND it's a real regression in M1/M2/M3/M4
    code → STOP, surface to user, fix in a follow-up PR.
  * Plan §11 invariant fails AND it's an acknowledged V1 limitation
    (5 documented) → log it as "expected per V1 limitation #N",
    continue.
  * E2E surfaces a NEW V1 limitation not in the documented list →
    surface to user before deciding to document + accept vs. fix.

⚠️ CRITICAL — DO NOT WRITE NEW PRODUCTION CODE IN M5

M5 is verification only. The only writes M5 makes are:
* Synthetic emails for the E2E (via eq-email-pipeline synthetic
  injection endpoint).
* Optional rollback-drill git operations (revert + redeploy + revert
  the revert).
* /context-save + handoff doc updates at end of session.

If you find yourself adding columns, methods, SQL, or pipeline steps
to fix something during M5 — STOP. Either surface to user (real bug)
or skip + document (V1 limitation).

What M5 actually does:
1. Pre-flight verification (production health, M4 code live, LOCKED-17
   collision check, DBOS workflow drain check).
2. Synthesize a cold-inbound email from an unknown business sender.
3. Walk plan §10.3's 12-step E2E (verify pending state → duplicate
   webhook test → /approve → verify promote → verify enrichment →
   verify downstream → idempotency re-emit → teardown).
4. Walk plan §11's 22-item acceptance invariants checklist.
5. Optionally (per LOCKED-17): exercise plan §10.4 rollback drill.
6. Document M5 results; update lessons; sign off Phase-1-email-pipeline.

Before doing anything else, follow these steps IN ORDER:

1. Run `/context-restore`. You should find a checkpoint titled
   "phase-1-email-pipeline-m4-shipped-m5-next" dated 2026-05-18 evening.
   Load it. If /context-restore returns NO_CHECKPOINTS or a different
   latest checkpoint, STOP and surface — that's a sync gap.

2. Read MEMORY.md (auto-loads). Confirm project status reads
   PHASE_1_EMAIL_PIPELINE_M1_M2_M3_M4_DEPLOYED_M5_NEXT. If anything
   else (e.g., still says "M4_NEXT"), STOP and surface — memory state
   may have rolled back.

3. READ THE WAYFINDING DOC:
   /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/
     superpowers/specs/NEXT-SESSION-START-HERE.md

   Sections "SESSION SCOPE FOR THE NEXT SESSION", "CRITICAL — what
   already shipped + verified deployed" (with merge SHAs + deploy IDs
   + Railway env var inventory), "Production state verified end-of-
   prior-session", "What M5 verifies", "Execution sequence — M5",
   "21 LOCKED decisions", "Acknowledged V1 limitations" (5 items),
   "Stop conditions", "Phase 2 preview" are all load-bearing.

4. READ THE PLAN DOC (mandatory, ~15-20 min focused on §10 + §11):
   /Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/
     2026-05-17-pending-interactions-cold-inbound-fix.md

   Especially:
   - §10.3 (production E2E — 12 numbered steps; PRIMARY M5 REFERENCE)
   - §10.4 (rollback drill)
   - §11 (acceptance invariants — the ship-when-true checklist)
   - §8 (edge cases — re-skim before E2E so you recognize variants)

5. READ THE M4 PR DESCRIPTION for the deployed-behavior narrative:
   https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/10

   Especially the "Codex review trajectory" section (R1 fixes folded
   in — direction guard, NULL participants COALESCE, anchor TZ comment)
   so you know what's deployed.

6. READ NEW SESSION LESSONS (2 codified during M4 review):
   /Users/peteroneil/EQ-CORE/live-transcription-fastapi/tasks/lessons.md

   - "Postgres array concatenation is NULL-poisoned" — pattern for
     defensive SQL `||` operations.
   - "Scope to plan-explicit framing when Codex flags scope expansion"
     — heuristic for handling Codex P1 scope questions.

7. Verify pre-flight state (run BEFORE any M5 work):

   a. Production /health (both services):
      curl -sS -o /dev/null -w "live-fastapi: %{http_code}\n" \
        https://live-transcription-fastapi-production.up.railway.app/health
      curl -sS -o /dev/null -w "eq-email-pipeline: %{http_code}\n" \
        https://email-pipeline-production.up.railway.app/api/ping
      curl -sS https://email-pipeline-production.up.railway.app/api/health
      Expected: all 200; eq-email-pipeline checks all "ok".

   b. M4 code is live on main:
      git -C /Users/peteroneil/eq-email-pipeline log --oneline -3
      Expected top: 6fa181a M4: orchestrator pending_interactions...

   c. SHARED-TENANT-COLLISION CHECK (LOCKED-17 — critical for M5
      because E2E writes destructive data):
      ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
      Any file modified in last hour = pause + ask user.

   d. DBOS workflow drain check (per plan §9 deploy discipline):
      via mcp__neon__run_sql on project super-glitter-11265514:
      SELECT * FROM dbos.workflow_status
      WHERE status IN ('pending', 'running')
        AND created_at > NOW() - INTERVAL '1 hour';
      Expected: 0 rows.

   e. Baseline pending_interactions count in test tenant:
      SELECT COUNT(*) FROM pending_interactions
      WHERE tenant_id = '11111111-1111-4111-8111-111111111111'
        AND archived_at IS NULL;
      Capture the baseline so you know what M5's E2E adds.

   If any of these fail, STOP and surface.

8. After reading, briefly confirm in one paragraph: where the prior
   session left off + your M5 verification approach + that you
   understand this is VERIFICATION (not new code) + that you will
   surface real bugs rather than silently fix.

9. EXECUTE — M5 (production E2E + rollback drill + acceptance
   invariants).

   Per plan §10.3 + §10.4 + §11. Use MCP tools throughout:
     mcp__neon__run_sql for Postgres verification
     mcp__neo4j_structured__read_neo4j_cypher for Neo4j verification
     mcp__pinecone-custom__describe-index-stats + search-records
       for Pinecone verification
     mcp__railway__deployment_logs for live log streaming

   Step 0: Generate fresh uuid-suffixed prospect identity:
     from_email = "test-prospect-{uuid}@cold-prospect-{uuid}.com"
     internet_message_id = "<m5-e2e-{uuid}@cold-prospect-{uuid}.com>"
     subject = "Q1 partnership inquiry — M5 E2E {uuid}"

   Steps 1-12: walk plan §10.3 sequentially, capturing verification
   results inline. Each step has explicit SQL/Neo4j/Pinecone assertions
   in the plan.

   Step 13: walk plan §11 acceptance invariants. Schema invariants pull
   live via Neon MCP; code invariants verified via grep; behavior
   invariants via the §10.3 E2E results above.

   Step 14 (optional, recommended): plan §10.4 rollback drill.
   - git revert 6fa181a → push → Railway redeploys old code
   - synthetic cold-inbound → drops silently (pre-M4 behavior)
   - git revert HEAD~1 (revert the revert) → push → Railway redeploys
     M4 → cold-inbound capture works again.

   Step 15: document M5 results in tasks/lessons.md if anything new
   surfaced.

   Step 16: sign off Phase-1-email-pipeline as COMPLETE if all §11
   invariants hold AND no new P0/P1 bugs.

   STOP after M5 sign-off. The natural next initiative is Phase 2
   (Neo4j MERGE-everywhere refactor + identity state machine for
   contacts + outbound capture + DLQ observability + queue UI), but
   that's a separate session with its own plan-writing phase.

10. END-OF-SESSION HANDOFF: /context-save with title indicating M5
    status (e.g., "phase-1-email-pipeline-COMPLETE" or
    "phase-1-email-pipeline-m5-in-progress" if not complete).
    Update NEXT-SESSION-START-HERE.md for Phase 2 brainstorming or
    whatever the user wants next. Write a new dated next-session-
    prompt.md mirroring THIS prompt's depth + structure. Update
    MEMORY.md status string.

ANTI-ANCHORING — 21 LOCKED decisions exist; full list in
NEXT-SESSION-START-HERE.md. Most load-bearing for M5:

(6)  Option B test infrastructure (test-tenant scoping in prod Neon)
     + @pytest.mark.requires_db_write + RUN_DESTRUCTIVE_TESTS=1.
     M5 writes to the shared test tenant; teardown discipline matters.
(7)  Two hard rules — no contact without account anchor; no
     interaction without account anchor. M5 verifies these hold for
     promoted cold-inbound emails.
(9)  Materialization REQUIRES Lane 2 raw_interactions before
     materializing. M5 verifies this ordering in plan §10.3 step 7.
(11) Per-batch user confirmation for destructive ops on shared test
     tenant. M5's teardown qualifies.
(17) Shared-tenant collision pre-flight. CRITICAL for M5.
(20) DB CAS TTL > SQS VisibilityTimeout. M5 verifies the handler's
     re-delivery path empirically.
(21) HandlerOutcome tri-state enum {COMPLETE, PERMANENT_SKIP,
     TRANSIENT_SKIP}. M5 verifies COMPLETE on full enrichment + might
     touch the TRANSIENT_SKIP path in the rollback drill.

ACKNOWLEDGED V1 LIMITATIONS (5; M5 verifies they hold; NOT bugs):

1. Personal/internal anchor cold-inbound → log+drop. M5 should NOT
   trigger this path (synthetic email targets BUSINESS only).
2. Neo4j build_skeleton + write_flesh partial-retry corruption.
   Bounded by M3's 2-layer guard. M5 should NOT trigger this path
   on a single-pass E2E.
3. upsert_thread race — FIXED in M2 + M4. M5 verifies via §11.
4. Legacy per-signal loop hardcodes summary_type='meeting'. Cosmetic
   on re-pointed signals; M5 may observe a duplicate 'meeting'
   summary alongside 'email' summary if signal re-points happen.
5. build_skeleton CREATE fallback for missing internet_message_id.
   M5's synthetic email has internet_message_id, so this won't
   trigger.

VERIFIED CROSS-REPO STATE (2026-05-18 evening, end of prior session)

- M1 merged + deployed: eq-frontend de586bbc.
- M2 merged + deployed: live-transcription-fastapi 756575d7.
- M3 merged + deployed: eq-email-pipeline 85c0295.
- M4 merged + deployed: eq-email-pipeline 6fa181a; Railway deploy
  756b96e4 SUCCESS; /api/health 200 with all 3 checks ok.
- AWS infrastructure: 6/6 resources live.
- All known V1 limitations bounded + documented.

USER POSTURE (load-bearing)

Non-developer founder. Make confident technical decisions; surface
only product/strategic decisions. Strict OSS only. NO sunk-cost
preservation. M5 is verification, not exploration.

Context economy matters. M5 should run lean — no Codex review needed
unless E2E surfaces a real bug that requires a fix PR. Plan for ~1
session of focused verification work; total time-on-task should be
substantially less than M2 (7 Codex rounds) or M3 (6 rounds).

User explicitly approves git push when asked; do NOT push without
asking first. Same for production-affecting Railway env var changes.

The rollback drill IS reversible and exercises real infrastructure;
ask before running it (per LOCKED-11 destructive-ops protocol).

SCOPE OF THIS SESSION — EXPLICIT HARD CONSTRAINT

In scope:
- M5: production E2E (plan §10.3), rollback drill (plan §10.4),
  acceptance invariants checklist (plan §11), sign-off.

OUT of scope for this session:
- Any new feature work.
- Phase 2 brainstorming or planning (separate session).
- Fixing newly-surfaced V1 limitations (document, don't fix).
- Outbound cold-outreach capture (Phase 2 enhancement out of M4 scope).

If you finish M5 with substantial context remaining, you CAN start
Phase 2 brainstorming via /office-hours — but treat it as a
deliberate decision (with user confirmation), NOT an automatic
progression.

STOP CONDITIONS (hard — surface to user)

- /context-restore returns NO_CHECKPOINTS.
- MEMORY.md status isn't PHASE_1_EMAIL_PIPELINE_M1_M2_M3_M4_DEPLOYED_M5_NEXT.
- Production state has rolled back (verify Neon + /health at session
  start).
- M4 code is not at 6fa181a on origin/main.
- LOCKED-17 collision check shows concurrent agent in last hour AND
  M5 about to run destructive E2E.
- E2E step fails at a step that worked in M2/M3/M4 integration tests.
- E2E surfaces a new V1 limitation NOT documented.
- You're tempted to "fix" something during M5.

KEY REFERENCE PATHS

- THE PLAN (load-bearing): /Users/peteroneil/eq-email-pipeline/docs/
  superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md
  (eq-email-pipeline:033626a)
- Wayfinding doc: /Users/peteroneil/EQ-CORE/live-transcription-fastapi/
  docs/superpowers/specs/NEXT-SESSION-START-HERE.md
- M4 merged PR: https://github.com/oneilstokeseqrm/eq-email-pipeline/
  pull/10 (merged as 6fa181a)
- M3 merged PR: https://github.com/oneilstokeseqrm/eq-email-pipeline/
  pull/9 (merged as 85c0295)
- M2 merged PR: https://github.com/oneilstokeseqrm/
  live-transcription-fastapi/pull/19 (merged as 756575d7)
- M1 merged PR: https://github.com/oneilstokeseqrm/eq-frontend/pull/392
  (merged as de586bbc)
- M4 orchestrator: /Users/peteroneil/eq-email-pipeline/src/pipeline/
  orchestrator.py
- M4 persistence: /Users/peteroneil/eq-email-pipeline/src/persistence/
  postgres.py (atomic upsert_thread, email_exists UNION,
  persist_pending_interaction)
- M3 handler: /Users/peteroneil/eq-email-pipeline/src/pipeline/
  email_promoted_subscriber.py
- M5 verification scripts: /Users/peteroneil/EQ-CORE/
  live-transcription-fastapi/scripts/verify_schema.py +
  verify_consumer_contracts.py
- New lessons (2026-05-18): tasks/lessons.md — "Postgres array
  concatenation is NULL-poisoned" + "Scope to plan-explicit framing
  when Codex flags scope expansion"

The plan is the load-bearing artifact. M4's deployed state is the
state M5 verifies. Start with /context-restore. Read plan §10 + §11
next. Then execute.
```

---

## Notes for the user pasting this

This prompt is the M5 verification handoff. Key differences vs the M4 prompt:

- **Status string is `PHASE_1_EMAIL_PIPELINE_M1_M2_M3_M4_DEPLOYED_M5_NEXT`.** All four production milestones merged + deployed + verified.
- **M5 is verification-focused, NOT implementation.** The hard constraint at the top ("DO NOT WRITE NEW PRODUCTION CODE IN M5") is the load-bearing scope guard.
- **No Codex review expected** unless an E2E surfaces a real bug requiring a fix PR.
- **Rollback drill IS in scope** (per plan §10.4) but is gated on user approval per LOCKED-11.
- **Phase 2 brainstorming is OUT of scope** but is the natural next initiative once M5 signs off.
- **Lean context expectation:** M5 should be the lightest of the 5 milestones (no Codex rounds, no architecture decisions, just verification).
