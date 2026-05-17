# Next Session Opening Prompt (2026-05-17 PM, post-M5)

Paste the block below as the opening message of the next Claude session. Written 2026-05-17 PM after PR #18 (M5 verified-contract tooling) merged and the eq-email-pipeline gap was recharacterized as finishing committed Phase 1 work.

**Important:** the next session is primarily a **plan-writing + design-review** session, NOT an implementation session. The agent should brainstorm Approach C with the user, run a Codex consult on the chosen design, write an implementation plan, and STOP there. Implementation happens in a subsequent execution session. See "scope of this session" below for the hard constraint.

---

```
You're working in /Users/peteroneil/EQ-CORE/live-transcription-fastapi.

This is a continuation session for the Contact Quality and Account-
Anchoring Initiative — a multi-phase data-quality project on an
AI-native customer intelligence platform. Phase 1 + Phase 1.5 main
code are SHIPPED. PR history: PR #10/#11 (Phase 1, 2026-05-14);
PR #14 (M1), PR #15 (M1 hotfix), PR #17 (M3+M4) all 2026-05-15
and 2026-05-17 AM; PR #18 (M5) 2026-05-17 PM.

Your job THIS session is to **finish Phase 1 for the email pipeline**
and to **write a plan for it, NOT implementation code**. The
implementation happens in a subsequent execution session.

Background: the original Phase 1 plan (Task 1.24) committed the
orchestrator to applying three-state branching for email
sender/recipient resolution + queuing unknown-business senders.
Phase 1 PR #6 shipped logic + a test for the case where at least
one party on the email belongs to a known account, but did NOT
cover the case where ALL parties on the email are unknown (cold
inbound from a totally new prospect). Today, those emails are
silently dropped at `insert_email` (raises ValueError because
raw_interactions.account_id is NOT NULL → outer Exception catches
→ email lost, no queue signal). THIS IS COMMITTED PHASE 1 WORK
INCOMPLETELY DELIVERED, NOT NEW SCOPE.

Full finding + 4 candidate fix approaches at
`tasks/downstream/eq-email-pipeline-unknown-sender.md`. Recommended:
Approach C (separate `pending_interactions` table — explicit
pending state, architecturally honest, symmetric with Phase 2's
identity state machine for contacts, no misattribution). User
posture (2026-05-17 PM): do NOT fake an anchor.

Before doing anything else, follow these steps IN ORDER:

1. Run `/context-restore`. You should find a checkpoint titled
   "phase-1.5-m5-shipped-email-pipeline-gap-next" dated 2026-05-17 PM.
   Load it. If /context-restore returns NO_CHECKPOINTS, STOP and
   surface — that's a sync gap.

2. Read MEMORY.md (auto-loads). Confirm project status reads
   PHASE_1.5_M5_SHIPPED_EMAIL_PIPELINE_GAP_NEXT. If anything else,
   STOP and surface.

3. READ THE COMPREHENSIVE HANDOFF DOC FIRST:
   `docs/superpowers/specs/NEXT-SESSION-START-HERE.md`

   Sections "THE NEXT SESSION'S PRIMARY SCOPE" + "Two scenarios in
   plain English" + "Candidate fix approaches" + "LOCKED decisions"
   + "Status of all unfinished Phase 1 / 1.5 items" are load-bearing.

4. Verify pre-flight state:
   - `gh pr view 18 --json state,mergedAt` → should be MERGED.
   - `git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi
     status` → should be clean on `main`.
   - `git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi
     log --oneline -5` → top should be `5cdc838 docs(handoff):
     recharacterize email-pipeline gap` followed by `fd38880` and
     `95f9084 feat(phase-1.5): M5 verified-contract tooling`.
   - `curl -sS -o /dev/null -w "%{http_code}\n"
     https://live-transcription-fastapi-production.up.railway.app/health`
     should return 200.
   - `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10`
     SHARED-TENANT-COLLISION CHECK. Files modified in last hour =
     concurrent agent hazard. Pause + ask user.

5. READ THESE DOCS IN ORDER (mandatory, ~25-30 min for the
   plan-writing scope of this session):

   a. The checkpoint (loaded via /context-restore) — full record
      of M5 + 6 Codex rounds + the email-pipeline finding + the
      recharacterization context.

   b. NEXT-SESSION-START-HERE.md (from step 3 above; re-read after
      checkpoint for the full picture).

   c. `tasks/downstream/eq-email-pipeline-unknown-sender.md` — THE
      load-bearing finding doc. Approach C is the recommended path
      with full rationale. Read the "Recommended sequence" section
      to understand the multi-step process.

   d. ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-
      fastapi/memory/project_email_pipeline_unknown_sender_drop.md
      — auto-memory complement.

   e. `docs/superpowers/specs/2026-05-12-contact-quality-initiative-
      design.md`:
        §3 — the two hard rules
        §5.2 + §5.3 — queue signals + owner determination
        §6 — account state model (read carefully — explains why
        the data model says contacts get progressive enrichment
        but accounts get atomic enrichment; the pending-interaction
        state is the missing analog for interactions)
        §7.1 — Phase 1 ingestion-path tasks (includes the original
        Task 1.24 acceptance criteria the next session is
        completing)
        §9 — Phase 2 sketch (read for the identity state machine
        + progressive enrichment framing that Approach C
        deliberately mirrors)

   f. `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-
      and-1.5.md`:
        Task 1.24 (line 2276) — the original eq-email-pipeline
        orchestrator audit task + acceptance criteria

   g. `docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md`:
        §3.4 — downstream consumer Pydantic models (relevant for
        envelope contract on the promote-pending step)
        §6.6 — emit step (Approach C's "promote pending → raw +
        emit envelope on approval" needs to integrate here)

   h. Quick scan of the actual code paths:
      - `/Users/peteroneil/eq-email-pipeline/src/pipeline/
        orchestrator.py` lines 174-280 (anchor resolution +
        per-participant loop + insert_email call + signal flush)
      - `/Users/peteroneil/eq-email-pipeline/src/persistence/
        postgres.py` lines 195-225 (insert_email's NOT NULL guard
        — the actual mechanism dropping cold-inbound emails)

   i. `tasks/lessons.md` — bottom entries (shared-infrastructure
      collision, Codex pre-merge gate, the M5 Review gates lesson).

6. After reading, briefly confirm your understanding of where the
   prior session left off + your plan-writing approach for this
   session (one paragraph) before starting work. Especially confirm
   that you understand this is a plan-writing session, NOT an
   implementation session.

7. EXECUTE per `tasks/downstream/eq-email-pipeline-unknown-sender.md`
   Section "Recommended sequence" — but ONLY the design + planning
   half of that sequence in this session:

   Step 1 — Brainstorm fix approach with the user. Approach C
   (separate `pending_interactions` table) is the recommended,
   cutting-edge-2026, architecturally-honest path. Approach A
   (recipient-as-anchor) was explicitly REJECTED by the user
   2026-05-17 PM (misattributes the email to an account it doesn't
   belong to). Approaches B (NULL account_id) and D (column-level
   pending state) are alternatives if Approach C is infeasible.
   Surface to user via AskUserQuestion; do NOT auto-decide.

   In the brainstorm with the user, work through the dozen
   open design questions documented in NEXT-SESSION-START-HERE.md
   §"Open design questions for the plan-writing session". Do NOT
   try to answer all of them — surface the most consequential ones
   to the user for direction.

   Step 2 — Codex consult on the chosen approach. Use
   `codex review` or `codex exec "review this design doc"` against
   the draft design. CSO discipline — design-time review BEFORE
   any code. Use `model_reasoning_effort=medium` (per LOCKED-18).

   Step 3 — Revise design per Codex feedback. Iterate until clean
   OR diminishing returns (soft cap 4 rounds per LOCKED-14).

   Step 4 — Write the implementation plan at
   `eq-email-pipeline/docs/superpowers/plans/2026-05-XX-pending-
   interactions.md` (or similar; pick the right repo for the plan
   based on where most code lives — likely eq-email-pipeline since
   the orchestrator branch is the primary code change).

   Step 5 — STOP. /context-save the plan-writing checkpoint.
   Rewrite NEXT-SESSION-START-HERE.md for the EXECUTION session.
   Create a new dated next-session-prompt.md. Hand off to the
   user.

   Implementation (Steps 6-10 of the "Recommended sequence" doc)
   happens in a subsequent execution session — NOT this one. Trying
   to compress design + implementation into one session will
   either produce half-baked work or run out of context.

8. PRE-MERGE RITUAL — N/A this session (no code changes; plan-only).
   When the execution session runs, use:
   - `codex review --commit HEAD -c model_reasoning_effort=medium`
     for multi-commit PRs once cumulative diff > ~1500 lines.
   - 4-round soft cap; extend when real P1s keep surfacing.

9. SHARED-INFRASTRUCTURE-COLLISION PROTOCOL (LOCKED-17):
   - LAYER 1: ls ~/.claude/projects/-Users-peteroneil-*/*.jsonl.
     Any file modified in last hour = pause + ask.
   - LAYER 2: per-action confirmation before ANY destructive op on
     the shared test tenant. NOT expected this session (plan-only)
     but applies if you run the production canary OR
     RUN_DESTRUCTIVE_TESTS=1 pytest.

10. End-of-session: /context-save + rewrite NEXT-SESSION-START-HERE
    for the EXECUTION session + write 2026-05-XX-next-session-
    prompt.md for that execution session.

ANTI-ANCHORING (LOCKED decisions — do NOT re-litigate)

18 LOCKED decisions exist; full list in NEXT-SESSION-START-HERE.md
§"LOCKED decisions". For this session, the most load-bearing:

(1) DBOS is the substrate.
(2) Single Railway replica + executor_id from RAILWAY_REPLICA_ID.
(3) EventBridge Path A with com.yourapp.transcription source.
(4) Workflow ID format f"queue-{queue_id}:approval-{approval_attempt_id}".
(5) /approve reserves synchronously then enqueues.
(6) Option B test infrastructure (test-tenant scoping in prod Neon).
(7) Two hard rules: no contact without account anchor, no
    interaction without account anchor — Approach C respects both
    (pending_interactions is a separate table; raw_interactions
    still has NOT NULL account_id).
(8) SQLAlchemy 2.0.49 CAST(:name AS uuid) form.
(9) Materialization requires real raw_interactions (no placeholders).
(10) Codex review BEFORE merging (4-round soft cap).
(11) Per-batch user confirmation for destructive ops on test tenant.
(12) Transcripts: frontend forces anchor selection; 400 reject if
     no account_id. By design per user (2026-05-17 PM). Emails are
     categorically different — backend handles no-anchor case.
(13) Recipient-as-anchor REJECTED for emails — misattribution.
(14) Pending-interactions pattern recommended (Approach C) — aligns
     with Phase 2 identity state machine.
(18) Codex multi-round reviews: --commit HEAD past ~1500 lines.

VERIFIED CROSS-REPO STATE (2026-05-17 PM)

- eq-email-pipeline re-open trigger: ✅ DELIVERED. `orchestrator.py:342`
  calls `reopen_archived_entry`.
- eq-frontend `/dashboard/organization/email-pipeline`: ✅ EXISTS
  (admin prototype from design §29).
- eq-frontend `app/(workspace)/agent-queue`: ✅ EXISTS — user-facing
  queue UI (verify mid-session what its current state is).
- eq-agent-action-core `worker_attempt_id`: ❌ NOT in production
  OpenAPI BUT THIS IS N/A in the DBOS world. DBOS step caching
  provides idempotency; agent's run_id + GET /api/enrich/{run_id}
  is the agent-side mechanism. The Phase 1 plan line was inherited
  from the pre-rethink polling-worker design.
- eq-structured-graph-core consumer behavior: ⚠️ Envelope contract
  verified via M5 verify_consumer_contracts.py. Runtime MERGE
  behavior is a production-canary question.

UNFINISHED PHASE 1 + 1.5 WORK — FULL LIST

PRIMARY (this session's plan-writing scope):
1. eq-email-pipeline cold-inbound-unknown-sender drop fix
   (Phase 1 Task 1.24 incomplete) — Approach C recommended.

SECONDARY (other unfinished items; NOT this session's scope):
2. Test-discipline-gaps Item 1 — audit + de-mock integration
   tests that mock lookup_account_by_domain at import level.
3. Test-discipline-gaps Item 2 — complete per-attendee branching
   happy paths in production E2E suite.
4. Test-discipline-gaps Item 3 — narrow outer except Exception:
   blocks in transcript_enrichment.py and similar paths.
5. Production canary (deferred from M3+M4 + M5) — would also
   verify eq-structured-graph-core MERGE behavior runtime.
6. M3.5 outbox drop (optional Prisma migration in eq-frontend).

PHASE 2 (post-Phase-1.5; explicit stopping point per
2026-05-15 plan):
- Identity state machine + progressive enrichment for contacts
  (shell / emerging / partial / resolved / verified). The
  pending_interactions pattern this session designs is the
  symmetric construct for interactions.

PHASE 3 (post-Phase-2):
- Conflict resolution + multi-account history + fuzzy matching.

OPEN DESIGN QUESTIONS FOR THE PLAN-WRITING SESSION

(Documented at length in NEXT-SESSION-START-HERE.md
§"Open design questions". The next agent should NOT try to
answer all of these alone — surface the most consequential
to the user for direction.)

Most consequential:
- Schema for pending_interactions — mirror raw_interactions exactly
  or a leaner subset?
- How does the workflow promote pending → raw on approval? New
  DBOS step? Modify the existing materialization step?
- What happens on /map (map to existing account) vs /approve
  (create new) — does promotion happen in both cases?
- TTL / auto-archive on pending_interactions — 30 days? 90 days?
- EventBridge emission timing — on promote OR once promoted to
  raw_interactions? (Affects every downstream consumer.)

Less consequential but plan-needed:
- Dedup: multiple emails pending for the same domain — first-email-
  wins? all-saved? sliding window?
- Cross-repo migration ordering: schema migration in eq-frontend,
  then orchestrator branch in eq-email-pipeline, then promotion
  step in live-transcription-fastapi.
- Production E2E acceptance criteria.
- Queue UI integration in eq-frontend `app/(workspace)/agent-queue`
  — does it need to surface pending_interactions or just the
  pending_account_mappings queue entry?

PRODUCTION CREDENTIALS + IDS (load-bearing reference, self-contained)

- Neon Postgres (eq-dev): project super-glitter-11265514, branch
  production, database neondb. Direct connection (no -pooler) for
  DBOS_SYSTEM_DATABASE_URL.
- Test tenant: 11111111-1111-4111-8111-111111111111. All data is
  test data. Per LOCKED-17, ask user per-batch for destructive ops.
- Test user (FK target): b0000000-0000-4000-8000-000000000002.
- Railway FastAPI: project 847cfa5a-b77c-4fb0-95e4-b20e8773c23e,
  service 59a69f3d-9a24-4041-942a-891c4a81c5fb, env
  e4c5ec15-1931-4632-9e58-92d9c6be4261, URL
  https://live-transcription-fastapi-production.up.railway.app.
- Railway eq-agent-action-core: URL
  https://eq-agent-action-core-production.up.railway.app, service
  3036ea0f-afc9-4bc4-889d-c98617d81e96.
- eq-email-pipeline: /Users/peteroneil/eq-email-pipeline (NOT
  under EQ-CORE/). Main HEAD `084567a` as of 2026-05-17 PM.
- eq-frontend: /Users/peteroneil/eq-frontend.
- Internal JWT: HS256, INTERNAL_JWT_SECRET, iss=eq-frontend,
  aud=eq-backend, claims: tenant_id, user_id, optional pg_user_id.
- AWS: EventBridge bus 'default' (configurable via
  EVENTBRIDGE_BUS_NAME); AWS_REGION=us-east-1.
- Neo4j: Aura instance c6171c63, URI
  neo4j+s://c6171c63.databases.neo4j.io.

USER POSTURE (load-bearing)

Non-developer founder. Make confident technical decisions; surface
only product/strategic decisions. Strict OSS only (no SSPL, no
BSL). Architectural correctness over short-term shortcuts.
Cutting-edge 2026 AI-native patterns. NO sunk-cost preservation.

Context economy matters. The user has surfaced context concerns
multiple times in prior sessions. Apply the 4-round Codex soft
cap; switch to --commit HEAD past ~1500-line cumulative diff
(LOCKED-18); be tight in updates rather than chatty.

User explicitly REJECTED recipient-as-anchor for emails (2026-05-17
PM). The pattern is to use an explicit pending state, not a fake
anchor. Approach C (separate pending_interactions table) is the
recommended path.

User explicitly ACCEPTED the transcript-pipeline "reject if no
account_id" UX (2026-05-17 PM). The dam is in the right place for
transcripts; the email pipeline is categorically different.

All data in the system is test data; no production users yet.
Architectural choices CAN accept short-term limitations that would
block a production-traffic ship, but the user is reluctant to
ship hacks (see the recipient-as-anchor rejection).

SCOPE OF THIS SESSION — EXPLICIT

In scope:
- Brainstorm Approach C with the user (open design questions)
- Codex consult on the draft design (CSO discipline)
- Iterate on the design until Codex passes OR diminishing returns
- Write the implementation plan in eq-email-pipeline
- /context-save + handoff for the EXECUTION session

HARD CONSTRAINT — out of scope for this session:
- Writing implementation code for the fix
- Schema migrations
- Production E2E
- Production canary
- Any of the secondary unfinished items (test-discipline gaps,
  M3.5 outbox drop) UNLESS the plan-writing finishes very fast
  with substantial context remaining

This session is for PLAN-WRITING, not IMPLEMENTATION. The user
explicitly clarified this 2026-05-17 PM. Compressing design +
implementation into one session will produce half-baked work or
run out of context. The execution session is a separate ship.

STOP CONDITIONS (hard — surface to user)

- /context-restore returns NO_CHECKPOINTS
- MEMORY.md status isn't PHASE_1.5_M5_SHIPPED_EMAIL_PIPELINE_GAP_NEXT
- The user wants a different approach than Approach C and the
  reasoning isn't clear from the rejected-A pattern
- The chosen fix approach requires a schema migration AND
  eq-frontend coordination isn't agreed
- Codex consult on the draft design surfaces a P1 you can't
  resolve in one revision round
- You discover NEW evidence that one of the 18 LOCKED decisions
  needs reconsideration
- You're tempted to start writing implementation code — STOP,
  re-read the "SCOPE OF THIS SESSION" section, surface to user

The plan is the load-bearing artifact. The user is paying for
thinking + correct execution + careful coordination, not typing.
A well-written plan that takes one session is more valuable
than half-baked code that takes one session AND a second session
to fix.
```

---

## Notes for the user pasting this

The key changes vs the 2026-05-15 PM and 2026-05-17 AM prompts:

- **Status string is `PHASE_1.5_M5_SHIPPED_EMAIL_PIPELINE_GAP_NEXT`.**
- **The session is plan-writing, NOT implementation.** This is a hard constraint codified into a section explicitly titled "SCOPE OF THIS SESSION — EXPLICIT". Implementation is a separate later session.
- The eq-email-pipeline gap is **recharacterized as finishing committed Phase 1 work**, NOT new scope. The doc at `tasks/downstream/eq-email-pipeline-unknown-sender.md` explains the recharacterization context for any agent wanting the full history.
- **Approach C (separate pending_interactions table)** is recommended. Approach A (recipient-as-anchor) is explicitly REJECTED by the user.
- 4 additional unfinished Phase 1 + 1.5 items (test-discipline Items 1, 2, 3 + production canary + M3.5) are listed but explicitly NOT in scope.
- Cross-repo state is now verified definitively where verifiable (re-open trigger DELIVERED; queue UI EXISTS; worker_attempt_id is N/A in DBOS world; consumer runtime behavior is a canary question).
- LOCKED decisions grew from 17 to 18 (Codex multi-round pattern from M5's round-6 stall investigation).
- Open design questions for the plan-writing session are documented so the next agent can surface the consequential ones to the user.
