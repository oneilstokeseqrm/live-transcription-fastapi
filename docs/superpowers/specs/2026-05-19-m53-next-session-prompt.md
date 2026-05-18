# Next Session Opening Prompt (2026-05-19, post-M5.2 / M5.3)

Paste the block below as the opening message of the next Claude session.

Written 2026-05-18 end-of-day after M5.2 verification ran. M5.2 shipped + deployed + verified all 3 documented bug fixes (httpx timeout per-phase split, INGEST_SUCCESS_STATUSES adds pending_account_approval, Prisma NULLS NOT DISTINCT migration). Plan §10.3 Steps 1-5 PASS empirically on a fresh UUID; Step 6 surfaced **Bug #4** — a contract drift in eq-agent-action-core's `/api/enrich` response shape (v2 envelope since 2026-03-04 vs our `AccountProfile` model expecting flat v1). Bug #4 is NOT the concurrent cpo-mvp refactor's fault (different files, different scope). Strategic decision after full investigation: fix is in live-transcription-fastapi (the consumer), NOT in eq-agent-action-core (the producer), because both systems already coordinate correctly via `account_domains.(tenant_id, domain)` UNIQUE constraint.

**Important:** M5.3 is **THE FINAL Phase-1-email-pipeline blocker**. Once it ships + §10.3 + §11 verify clean, Phase 1 is COMPLETE and Phase 2 PLANNING is unblocked.

---

```
You're picking up the Contact Quality and Account-Anchoring Initiative —
a multi-phase data-quality project on an AI-native customer intelligence
platform.

The prior session (2026-05-18, M5.2) shipped, merged, and deployed all
3 documented M5.2 bug fixes:

  Fix #1 (live-transcription-fastapi PR #20, merged 929472e):
    Agent client httpx timeout 120s → 300s with per-phase split
    (connect=10s, read=300s). Codex R3 CLEAN (R1 caught the per-phase
    issue; R2 caught comment accuracy + contract test pin; R3 clean).
    Production: workflow patiently waited 8.8min for the agent; no
    httpx timeout.

  Fix #2 (eq-email-pipeline PR #12, merged ceea064):
    Added 'pending_account_approval' to _INGEST_SUCCESS_STATUSES.
    Codex deferred due to rate-limit window; merged with mechanical
    1-line frozenset add covered by 5 sibling per-status tests.
    Production: synthetic injection returned HTTP 200 (was HTTP 500).

  Fix #3 (eq-frontend PR #398, merged c3bc162):
    Raw-SQL Prisma migration `20260518142500_m5_2_pending_signal_dedup_nulls_not_distinct/`
    promoting pending_signal_dedup unique index to NULLS NOT DISTINCT.
    Pre-verified safe on a Neon temp branch (0 existing signals,
    Postgres 17.8 supports NULLS NOT DISTINCT). Production index
    confirmed updated post-Vercel deploy.

  Fix #3 follow-up (eq-email-pipeline PR #13, merged 8b2c67a):
    Aligned tests/schema.sql + flipped test_null_calendar_event_id
    assertion from n_rows==2 to n_rows==1 after the eq-frontend
    migration deployed.

M5.2 production E2E re-run on a fresh UUID (15f6b36318b8) verified
plan §10.3 Steps 1-5 PASS empirically:
  Step 1 (synthesize cold-inbound)             ✓ PASS
  Step 2 (POST /api/emails/ingest)             ✓ PASS — HTTP 200 (Fix #2 verified)
  Step 3a-3f (pending state assertions)        ✓ PASS — all 6 checks
  Step 4 (duplicate webhook → skipped_duplicate) ✓ PASS — n=1 (Fix #3 verified)
  Step 5 (POST /approve → workflow_id)         ✓ PASS — HTTP 202 + correct format

Step 6 (DBOS workflow → status='SUCCESS') was BLOCKED by **Bug #4**:
agent contract drift. The workflow ran 8.8 minutes without timing out
(Fix #1's 300s budget worked), but errored at the AccountProfile
validation step. Diagnosis:

  - The agent's /api/enrich response is a v2 envelope shape:
    `{"run_id", "status", "result": {"company_name", "website_domain", ...}, "metadata", "account_id"}`
  - Our AccountProfile expects the flat v1 shape:
    `{"name", "domain", "industry", ...}` (no envelope)
  - Pydantic correctly says "name field required" — the top level has
    no `name`, just `run_id`, `status`, `result`, `metadata`, `account_id`.

Bug #4 has been latent in production since **2026-03-04** — the day
the agent's `/api/enrich` endpoint first shipped (commit e301b38 in
eq-agent-action-core). Three forces hid it:
  1. The contract-pinning test (tests/contract/test_agent_enrich_response_shape.py)
     is marked @needs_internal_jwt. CI doesn't inject INTERNAL_JWT_SECRET,
     so the test SKIPS silently in CI. Nobody saw it skip; nobody ran
     it locally.
  2. Every prior production /api/enrich attempt timed out at the old
     120s httpx budget BEFORE the workflow read the response body.
     Pydantic never got to validate. The bug was unreachable.
  3. M5.2's Fix #1 (300s timeout) is the first reachability change
     that let the workflow wait long enough to actually receive and
     validate the response. The latent bug surfaced on the very first
     end-to-end run.

**Critical context — Bug #4 is NOT the concurrent refactor's fault.**
There's a `feat/cpo-mvp-enhancements` branch in eq-agent-action-core
being actively developed by another agent. NONE of its commits touch
`/api/enrich` or the response shape. The v2 shape lives in
`src/eq_agent/api/enrich_routes.py` + `src/eq_agent/agent_url_enrichment/
schemas.py` + `src/eq_agent/agent_url_enrichment/finalize.py`. Those
files haven't been modified since commit `e301b38` on 2026-03-04.
The cpo-mvp work is on persona composition / CitationPool / MCP tool
wrappers — entirely different scope.

**Strategic decision: M5.3 fix is in live-transcription-fastapi, not
in eq-agent-action-core.** Both systems use the same idempotency key
for account creation: `account_domains.(tenant_id, domain)` UNIQUE.
The agent's create-or-update path at
`src/eq_agent/db/accounts.py:25-155` looks up account_domains by
(tenant_id, domain) before INSERT; the workflow's Step 4
`resolve_or_create_account` does the same. Whichever runs first
creates; the other sees the existing row and reuses the account_id.
No duplicates, no race, no coordination needed. The "fix at the
producer" path would lose richer v2 fields (founded_year, primary_
products, customer_segments, differentiators, crm_summary, etc.) that
Phase 2 will want, and would coordinate against the cpo-mvp work in
flight. Fix at the consumer (us); let the agent's contract evolve.

Production state at session start:

  Phase-1-email-pipeline M1 (eq-frontend Prisma):           de586bbc  ✓
  Phase-1-email-pipeline M2 (live-transcription-fastapi):   756575d7  ✓
  Phase-1-email-pipeline M3 (eq-email-pipeline subscriber): 85c0295   ✓
  Phase-1-email-pipeline M4 (eq-email-pipeline orchestrator): 6fa181a ✓
  M5.1 (eq-email-pipeline ON CONFLICT fix):                 79862b6   ✓
  M5.2 Fix #1 (live-transcription-fastapi httpx timeout):   929472e   ✓
  M5.2 Fix #2 (eq-email-pipeline INGEST_SUCCESS):           ceea064   ✓
  M5.2 Fix #3 (eq-frontend Prisma NULLS NOT DISTINCT):      c3bc162   ✓
  M5.2 Fix #3 follow-up (eq-email-pipeline test align):     8b2c67a   ✓

All eight commits merged + deployed + production-verified through
Step 5 of the §10.3 walk. Phase-1-email-pipeline is at "1 bug fix
+ 1 E2E run from initiative sign-off."

Your job THIS session is to execute M5.3 — ship the agent v2-shape
adapter fix in live-transcription-fastapi, then complete plan §10.3
Steps 6-12 verification + walk §11 22-item invariants checklist +
sign off Phase-1-email-pipeline initiative as COMPLETE.

⚠️ M5.3 scope (3 items IN, 2 items DEFERRED with rationale):

**IN SCOPE:**

1. Update `services/account_provisioning/types.py:AccountProfile` to
   mirror the agent's v2 schema using Pydantic field aliases. The
   class becomes:

     class AccountProfile(BaseModel):
         model_config = ConfigDict(extra="allow", populate_by_name=True)
         name: str = Field(..., alias="company_name")
         domain: Optional[str] = Field(default=None, alias="website_domain")
         industry: Optional[str] = None
         company_size: Optional[str] = Field(default=None, alias="employee_count_range")
         region: Optional[str] = Field(default=None, alias="headquarters")
         website: Optional[str] = Field(default=None, alias="website_domain")
         description: Optional[str] = Field(default=None, alias="one_line_description")
         company_type: Optional[str] = None

2. Update `services/agent_action_core_client.py:_parse_profile` to
   unwrap the v2 envelope:

     result = data.get("result")
     if not isinstance(result, dict):
         raise AgentEnrichTerminalError(...)
     return AccountProfile.model_validate(result)

3. Add 3 unit tests in `tests/unit/account_provisioning/test_agent_client.py`:
   - `test_enrich_handles_v2_envelope_shape` — happy path with the
     full v2 response shape.
   - `test_enrich_rejects_v2_response_missing_result_envelope` — fail
     loud if `result` key is missing.
   - `test_enrich_rejects_v2_response_missing_company_name` — fail
     loud if `result` is present but `company_name` is missing.
   - Update `test_missing_required_field_raises_terminal` to use the
     v2 envelope shape (it pre-M5.3 sent {"domain": "acme.com"} which
     wouldn't even reach AccountProfile under the new parser).

**DEFERRED (with rationale):**

3. Use the agent's returned `account_id` to short-circuit Step 4's
   `resolve_or_create_account` lookup. PURE PERF OPTIMIZATION — not
   correctness. The DB UNIQUE constraint on account_domains makes
   the current double-lookup correct; this is just removing a DB
   roundtrip. Revisit when load data shows it matters.

4. Make `tests/contract/test_agent_enrich_response_shape.py` actually
   run in CI (today @needs_internal_jwt-marked, silently skips). This
   is the ROOT CAUSE of why Bug #4 went undetected for 2+ months. The
   M5.3 unit tests in item 5 provide regression coverage at the parser
   layer; making the contract test CI-runnable touches CI infrastructure
   + secrets management + warrants its own PR review surface. Captured
   as a Phase 2 follow-up issue.

After M5.3 ships and deploys:
- Clean up any Bug #4 E2E artifacts from this session (M5.2's E2E
  artifacts were already cleaned at session close).
- Re-run plan §10.3 Steps 1-12 on a FRESH synthetic UUID. Step 6
  should reach status='SUCCESS'. Steps 7-12 should all PASS.
- Walk plan §11 22-item invariants checklist (Schema/Code/Contracts/
  Behavior categories).
- Sign off Phase-1-email-pipeline initiative as COMPLETE if all hold.

Before doing anything else, follow these steps IN ORDER:

1. Run `/context-restore`. You should find a checkpoint titled
   "phase-1-email-pipeline-m5.2-shipped-bug4-m5.3-next" dated
   2026-05-18 end-of-day. Load it. If /context-restore returns
   NO_CHECKPOINTS or a different latest checkpoint, STOP and surface.

2. Read MEMORY.md (auto-loads). Confirm project status reads
   PHASE_1_EMAIL_PIPELINE_M5.2_SHIPPED_BUG4_FOUND_M5.3_NEXT. If
   anything else, STOP and surface.

3. READ THE WAYFINDING DOC (load-bearing; M5.3 scope, execution
   sequence, stop conditions all live here):
   /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/
     superpowers/specs/NEXT-SESSION-START-HERE.md

4. READ THE PLAN — §10.3 + §11 (same as M5/M5.2):
   /Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/
     2026-05-17-pending-interactions-cold-inbound-fix.md
   M5.3 resumes from §10.3 Step 6 after the parser fix ships.

5. READ THE 3 NEW LESSONS at the bottom of tasks/lessons.md
   (codified end-of-M5.2 session):
   - "Skip-marked contract tests silently lose contract enforcement"
   - "Two-system idempotency via shared DB UNIQUE constraint
     coordinates dual creators"
   - "Timeout fixes can expose latent shape bugs in downstream APIs"

6. READ the M5.2 merged PRs for deployed-behavior narrative:
   - live-transcription-fastapi #20: https://github.com/oneilstokeseqrm/live-transcription-fastapi/pull/20
   - eq-email-pipeline #12: https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/12
   - eq-email-pipeline #13: https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/13
   - eq-frontend #398: https://github.com/oneilstokeseqrm/eq-frontend/pull/398

7. READ the agent's account-creation code for context (DO NOT MODIFY):
   /Users/peteroneil/EQ-CORE/eq-agent-action-core/src/eq_agent/db/
     accounts.py (lines 25-155)
   Confirms the agent uses account_domains.(tenant_id, domain) UNIQUE
   for idempotency — same key our workflow uses. This is why the
   "duplicate creator" architecture is actually safe.

8. PRE-FLIGHT VERIFICATION (run BEFORE any M5.3 work):

   a. Production health (all 3 services):
      curl -sS -o /dev/null -w "live-fastapi: %{http_code}\n" \
        https://live-transcription-fastapi-production.up.railway.app/health
      curl -sS -o /dev/null -w "eq-email-pipeline: %{http_code}\n" \
        https://email-pipeline-production.up.railway.app/api/ping
      curl -sS https://email-pipeline-production.up.railway.app/api/health
      curl -sS -o /dev/null -w "eq-agent-action-core: %{http_code}\n" \
        https://eq-agent-action-core-production.up.railway.app/openapi.json
      Expected: all 200; eq-email-pipeline checks all "ok".

   b. M5.2 code is live on all repos:
      git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi log --oneline -3
      Expected: top is docs(handoff) commit, then 929472e M5.2 Fix #1.
      git -C /Users/peteroneil/eq-email-pipeline log --oneline -3
      Expected: 8b2c67a → ceea064 → 79862b6.
      git -C /Users/peteroneil/eq-frontend log origin/main --oneline -3
      Expected: top c3bc162 (Fix #3 NULLS NOT DISTINCT).

   c. SHARED-TENANT-COLLISION CHECK (LOCKED-17):
      ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
      Any file modified in last hour = pause + ask user. KNOWN
      concurrent agent: cpo-mvp work on eq-agent-action-core's
      persona/composition code (NOT /api/enrich or test tenant data).
      Safe to proceed.

   d. DBOS workflow drain check (no PENDING/RUNNING):
      SELECT * FROM dbos.workflow_status
      WHERE status IN ('PENDING', 'RUNNING')
        AND created_at > (EXTRACT(EPOCH FROM NOW()) * 1000 - 3600000);
      Expected: 0 rows.

   e. Baseline pending_interactions count in test tenant:
      SELECT COUNT(*) FROM pending_interactions
      WHERE tenant_id = '11111111-1111-4111-8111-111111111111'
        AND archived_at IS NULL;
      Expected: 0 (M5.2 cleanup left clean slate).

   f. Verify production Neon NULLS NOT DISTINCT is in place:
      SELECT indexdef FROM pg_indexes WHERE indexname = 'pending_signal_dedup';
      Expected: contains "NULLS NOT DISTINCT".

   If any fail, STOP and surface.

9. After reading + pre-flight passing, briefly confirm in one paragraph:
   where the prior session left off + your M5.3 fix sequencing + that
   you understand the "fix in consumer, not producer" decision + that
   you understand the deferred items 3 and 4 with rationale + that you
   will surface a 5th bug as M5.4 if one appears during Steps 6-12.

10. EXECUTE — M5.3 fix:

    a. Branch off main: `fix/m5-3-agent-v2-response-adapter`.
    b. Update services/account_provisioning/types.py (AccountProfile
       with field aliases — see wayfinding doc for exact shape).
    c. Update services/agent_action_core_client.py (_parse_profile
       unwraps .result envelope — see wayfinding doc).
    d. Add the 3 unit tests in tests/unit/account_provisioning/
       test_agent_client.py + update the test_missing_required_field
       test for the new envelope shape.
    e. Run the unit tests locally — all should pass.
    f. Run the full non-integration test suite — verify 0 regressions
       vs M5.2 baseline.
    g. Commit. Codex review BEFORE merge per LOCKED-10. Codex CLI
       rate-limit should be lifted by 11:31 AM EDT (already past).
    h. Push + open PR + ask user merge auth.
    i. Verify Railway redeploy SUCCESS + /health 200 post-merge.

11. RE-RUN plan §10.3 Steps 1-12 on a FRESH synthetic UUID:

    Use a new uuid12 (NOT 15f6b36318b8 from M5.2). Walk Steps 1-12
    sequentially per the wayfinding doc + plan. Step 6 should reach
    status='SUCCESS' this time. Steps 7-12 should all PASS:
    - Step 7: account + raw_interactions + emails + summaries + links
    - Step 8: emails.local_enrichment_completed_at populated (M3
      subscriber processed EmailPromoted)
    - Step 9: Neo4j Interaction → Account; Pinecone vector; summary
      non-null
    - Step 10: handler idempotency (re-emit, no changes)
    - Step 11: action-item-graph + eq-structured-graph-core downstream
    - Step 12: teardown per LOCKED-11 (after user approval)

12. WALK plan §11 22-item invariants checklist. Verify each via
    Neon pg_indexes / information_schema / grep / verify_*.py scripts.

13. (OPTIONAL — ASK USER FIRST per LOCKED-11) Plan §10.4 rollback
    drill. Recommended only if user explicitly approves.

14. DOCUMENT M5.3 results in tasks/lessons.md if anything new
    surfaces. Update MEMORY.md to reflect M5.3 + initiative-complete
    status.

15. SIGN OFF PHASE-1-EMAIL-PIPELINE as COMPLETE if all §11 invariants
    hold AND §10.3 Steps 1-12 PASS AND no new P0/P1 bugs. Surface to
    user with:
    - All milestones M1+M2+M3+M4+M5.1+M5.2+M5.3 deployed + verified.
    - Plan §10.3 Steps 1-12 all PASS.
    - All 22 §11 invariants verified.
    - 21 LOCKED decisions list unchanged.
    - 4 remaining acknowledged V1 limitations.
    - 2 deferred items from M5.3 (perf optimization, CI contract test)
      tracked as Phase-2 follow-ups.
    - Phase 2 PLANNING is unblocked.

    STOP after Phase 1 sign-off. Phase 2 PLANNING is a separate
    session with its own plan-writing phase.

16. END-OF-SESSION HANDOFF:
    a. /context-save with title indicating Phase 1 complete + Phase 2
       brainstorming next (e.g., "phase-1-email-pipeline-INITIATIVE-
       COMPLETE-phase-2-planning-next").
    b. Rewrite NEXT-SESSION-START-HERE.md for Phase 2 brainstorming.
    c. Write a new dated next-session-prompt.md mirroring THIS
       prompt's depth + structure.
    d. Update MEMORY.md top entry.
    e. Commit + push handoff docs to live-transcription-fastapi main.

ANTI-ANCHORING — 21 LOCKED decisions exist from prior sessions; do
not re-litigate. M5.3 doesn't add new LOCKED decisions; the parser-
adapter fix is a bug-correction within the existing decision frame.
The "fix in consumer, not producer" decision was made with full
investigation during M5.2 and is documented in the wayfinding doc;
do not second-guess unless evidence surfaces that contradicts the
account_domains UNIQUE coordination model.

ACKNOWLEDGED V1 LIMITATIONS — 4 remaining (post-M5.2 fix #3 moved
NULL-DISTINCT from limitation to fixed bug). M5.3 does NOT add new
limitations. Items 3+4 from M5.3 are DEFERRED with rationale (perf
not correctness, CI infra warrants own PR), tracked separately as
Phase-2 follow-ups, NOT as V1 limitations.

VERIFIED CROSS-REPO STATE (2026-05-18 end-of-day)

- M1 merged + deployed: eq-frontend de586bbc.
- M2 merged + deployed: live-transcription-fastapi 756575d7.
- M3 merged + deployed: eq-email-pipeline 85c0295.
- M4 merged + deployed: eq-email-pipeline 6fa181a.
- M5.1 merged + deployed: eq-email-pipeline 79862b6.
- M5.2 Fix #1: live-transcription-fastapi 929472e (Codex R3 CLEAN).
- M5.2 Fix #2: eq-email-pipeline ceea064.
- M5.2 Fix #3: eq-frontend c3bc162 (production Neon index updated).
- M5.2 Fix #3 follow-up: eq-email-pipeline 8b2c67a.
- AWS infrastructure: 6/6 resources live (unchanged).
- Test tenant cleaned of M5.2 Bug #4 E2E artifacts at session close
  (per LOCKED-11 atomic transaction).
- 4 acknowledged V1 limitations bounded + documented.
- 2 deferred items from M5.3 tracked.

USER POSTURE (load-bearing)

Non-developer founder. Make confident technical decisions; surface
only product/strategic decisions. Strict OSS only. NO sunk-cost
preservation.

Context economy matters. M5.3 = code (1hr) + Codex (15min) + E2E
re-run + §11 walk + handoff. Budget ~2 hours of focused work.

User explicitly approves git push when asked; do NOT push without
asking first. Same for production-affecting Railway env var changes.

The rollback drill IS reversible; ask before running it.

User's stated rules:
  1. Complete Phase N before Phase N+1 planning (feedback_complete_phase_
     before_next; saved 2026-05-18). All open Phase 1 work ships
     before Phase 2 work begins.
  2. Take the approach a cutting-edge startup would take. No shortcuts
     unless the shortcut IS the correct architecture (verified by
     investigation, not assumption).
  3. The user merges PRs; the AI agent doesn't. EXCEPT when user
     explicitly authorizes — in M5.2 the user authorized "you should
     merge them" for the 4 PRs in the merge sequence. Don't generalize
     that authorization to all PRs; ask first for each new initiative.
  4. Plain-English explanations when the user asks "why" or "what
     happened" — the user is non-developer; technical accuracy must
     be paired with clear framing.

SCOPE OF THIS SESSION — EXPLICIT HARD CONSTRAINT

In scope:
- M5.3: 3 code changes (AccountProfile aliases, _parse_profile
  envelope unwrap, 3 new unit tests) shipped + deployed + verified.
- Re-run plan §10.3 Steps 1-12 on fresh UUID.
- Walk plan §11 acceptance invariants checklist (22 items).
- Phase-1-email-pipeline initiative sign-off.

OUT of scope:
- Any NEW feature work beyond the 3 documented M5.3 code changes.
- Phase 2 brainstorming or planning (separate session).
- Outbound cold-outreach capture.
- The deferred items 3 (perf optimization using agent's account_id)
  and 4 (CI-running contract test).
- Modifying eq-agent-action-core (the v2 shape is stable; fix is in
  our consumer).

If M5.3 surfaces a 5th bug during Steps 6-12 → STOP, surface to user,
treat as M5.4. The user's rule is to never silently expand scope.

STOP CONDITIONS (hard — surface to user)

- /context-restore returns NO_CHECKPOINTS.
- MEMORY.md status isn't PHASE_1_EMAIL_PIPELINE_M5.2_SHIPPED_BUG4_FOUND_M5.3_NEXT.
- Any of the 8 prior-session commit SHAs is not at the top of its
  repo's origin/main (rollback detected).
- Production state has rolled back (verify Neon + /health at session
  start).
- LOCKED-17 collision check shows another agent recently active on
  the test tenant or on /api/enrich code paths.
- Step 6 workflow STILL stalls after M5.3 parser fix deploys → 5th
  bug surfaced; STOP immediately.
- E2E surfaces a new V1 limitation NOT documented.
- You're tempted to "fix" the agent's v2 shape on eq-agent-action-core
  side — STOP, re-read the strategic decision in the wayfinding doc;
  the fix is in the consumer.
- The cpo-mvp refactor's commits start touching src/eq_agent/api/
  enrich_routes.py or src/eq_agent/agent_url_enrichment/ files.

KEY REFERENCE PATHS

- THE PLAN: /Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/
  2026-05-17-pending-interactions-cold-inbound-fix.md
- WAYFINDING: /Users/peteroneil/EQ-CORE/live-transcription-fastapi/
  docs/superpowers/specs/NEXT-SESSION-START-HERE.md
- M5.2 merged PRs:
  - live-transcription-fastapi #20 (929472e, Codex R3 CLEAN)
  - eq-email-pipeline #12 (ceea064)
  - eq-email-pipeline #13 (8b2c67a)
  - eq-frontend #398 (c3bc162)
- LESSONS (3 new from M5.2): tasks/lessons.md (bottom of file)
- AGENT'S ACCOUNT CREATION (read-only context): /Users/peteroneil/
  EQ-CORE/eq-agent-action-core/src/eq_agent/db/accounts.py:25-155
- AGENT'S /api/enrich HANDLER (read-only context): /Users/peteroneil/
  EQ-CORE/eq-agent-action-core/src/eq_agent/api/enrich_routes.py
- AGENT'S RESPONSE SCHEMA (v2): /Users/peteroneil/EQ-CORE/
  eq-agent-action-core/src/eq_agent/agent_url_enrichment/schemas.py
- FEEDBACK RULE (saved 2026-05-18, loads with MEMORY.md):
  ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-
  fastapi/memory/feedback_complete_phase_before_next.md

CRITICAL FILES TO TOUCH M5.3

- live-transcription-fastapi/services/account_provisioning/types.py
  (AccountProfile field aliases for v2 shape)
- live-transcription-fastapi/services/agent_action_core_client.py
  (_parse_profile unwraps .result envelope)
- live-transcription-fastapi/tests/unit/account_provisioning/
  test_agent_client.py (add 3 unit tests for v2 envelope handling)

DO NOT TOUCH

- eq-agent-action-core (the producer; v2 shape is intentional)
- eq-frontend (M5.2 work complete)
- eq-email-pipeline (M5.2 work complete)
- The deferred items 3+4 (perf + CI contract test) — Phase 2.

The plan is the load-bearing artifact. M5.2's deployed state +
M5.3's parser fix unblocks Step 6. Steps 7-12 + §11 invariants are
the final verification. Phase-1-email-pipeline COMPLETE after
sign-off. Start with /context-restore. Read the wayfinding doc
next. Then execute.
```

---

## Notes for the user pasting this

This prompt is the M5.3 verification + parser-fix handoff. Key differences vs the M5.2 prompt:

- **Status string is `PHASE_1_EMAIL_PIPELINE_M5.2_SHIPPED_BUG4_FOUND_M5.3_NEXT`.** All 3 M5.2 fixes are deployed + verified through Step 5. Bug #4 (agent v2 shape) blocks Step 6.
- **M5.3 is a small, focused code change** — ~3 files in live-transcription-fastapi, no cross-repo coordination needed. The bigger work is the §10.3 + §11 verification walk and sign-off.
- **The strategic decision (fix in consumer, not producer)** is documented in BOTH the wayfinding doc AND the opening prompt's "Strategic decision" paragraph + the "Two-system idempotency" lesson. The next session should NOT re-litigate this.
- **The 2 deferred items (perf optimization + CI contract test)** are documented with rationale in the wayfinding doc AND mentioned in the opening prompt's "DEFERRED" section. Tracked as Phase 2 follow-ups.
- **The session's terminal state is Phase 1 SIGN-OFF** — the first session in this initiative's trajectory to actually close out Phase 1 (M1 through M5.3 all shipped + verified + invariants checked).
- **Expected runtime: ~2 hours** of focused work. Less than M5.2 because the code change is smaller AND we have full clarity on what's blocking sign-off.
