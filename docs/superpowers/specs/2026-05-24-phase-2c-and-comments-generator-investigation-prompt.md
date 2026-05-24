# Next Session Opening Prompt (Phase 2c + comments-generator investigation)

**Written:** 2026-05-24 end-of-session, after Phase 2b shipped + merged + deployed and the eq-frontend#418 Vercel preview failure exposed a comments-generator multiSchema bug that blocks Phase 2a's database migration.

**Paste the block below as the opening message of the next Claude session.**

This is the canonical paste-ready handoff. It mirrors the structure of the prompt that opened the prior session (which delivered Phase 2b). The new agent reads everything mandatory, writes a 2-paragraph confirmation, then runs `/investigate` on the comments-generator bug as the first concrete action.

---

```
You're picking up the Granola.ai transcript ingestion adapter — the prior
session shipped Phase 2b (the Python vault module) all the way through Codex
gate to merged + deployed in production, but exposed a tooling bug on
eq-frontend that BLOCKS the Phase 2a database migration deploy. The vault
module is shipped as inert code (nothing imports it yet, so production
/health is 200), but its companion database tables don't exist in production
because eq-frontend#418 cannot deploy. THIS SESSION'S JOB: investigate +
resolve the comments-generator multiSchema bug via /investigate, get
eq-frontend#418 unblocked + merged + deployed, then run the long-deferred
KMS smoke test against the real production CMK, then begin Phase 2c
(Granola HTTP API client).

CONTINUITY IS CRITICAL. The prior session went through 7 rounds of Codex
review on Phase 2b (every round found real bugs; gate passed at R7), did a
cross-agent feasibility consult for an eq-llm-gateway team that wanted to
extract the vault module (recommendation: build a parallel vault, don't
extract yet — see memory), and discovered the comments-generator bug only
after pushing the eq-frontend PR. The bug is NOT in our vault code; it's
in @onozaty/prisma-db-comments-generator@1.5.0 and was exposed by our
multiSchema introduction. Same class of issue as Linear EQ-11 (eq-frontend
schema drift). You MUST internalize all of this before writing any code.
Read everything. Do not skip steps.

═══════════════════════════════════════════════════════════════════════
STEP 1 — RUN /context-restore FIRST
═══════════════════════════════════════════════════════════════════════

Before anything else, run /context-restore. It will load the most recent
checkpoint:

  ~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/
  20260524-XXXXXX-phase-2b-shipped-eq-frontend-blocked-comments-generator-bug.md

That checkpoint enumerates: Phase 2b's 8 commits (squash-merged as #24 to
main `7f98920`), Railway deploy SUCCESS, 7 rounds of Codex pre-merge fixes,
the cross-agent eq-llm-gateway consult (build parallel vault not extract),
the eq-frontend#418 PR creation + Vercel preview failure, the
comments-generator bug investigation, and the path forward.

If /context-restore returns NO_CHECKPOINTS or a different title, STOP and
surface to the user immediately — the handoff is broken.

═══════════════════════════════════════════════════════════════════════
STEP 2 — VERIFY CURRENT GIT STATE
═══════════════════════════════════════════════════════════════════════

Both primary checkouts should be on `main` per cross-agent courtesy.

  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi
  git branch --show-current     # expect: main
  git log --oneline -3          # expect: 7f98920 feat(vault): Phase 2b ... (#24)
  git status --short            # expect: clean

  cd /Users/peteroneil/eq-frontend
  git branch --show-current     # may be on another agent's branch
                                # — DO NOT switch unless absolutely necessary
  git log phase-2/granola-vault-schema --oneline -3
                                # expect: c674330 feat(prisma): Phase 2 Granola
                                # integration — vault schema + 3 new tables

If live-transcription-fastapi is not on `main`, STOP and verify what state
it's in before proceeding. If eq-frontend's primary checkout is on a branch
you don't recognize, DO NOT switch it — another agent may be active.

═══════════════════════════════════════════════════════════════════════
STEP 3 — READ THE COMPREHENSIVE HANDOFF DOCUMENT
═══════════════════════════════════════════════════════════════════════

The PRIMARY HANDOFF DOCUMENT is this file itself:

  docs/superpowers/specs/2026-05-24-phase-2c-and-comments-generator-investigation-prompt.md

It contains the full sequence below. After STEP 3, proceed to STEP 4 (the
mandatory reads).

═══════════════════════════════════════════════════════════════════════
STEP 4 — MANDATORY READS (in this order; do NOT skip)
═══════════════════════════════════════════════════════════════════════

Per the memory entry `feedback_complete_all_handoff_reads_before_action.md`:
complete EVERY read BEFORE starting the investigation. Don't write code
mid-read. Use parallel tool calls to read fast.

1. WAYFINDING:
   docs/superpowers/specs/NEXT-SESSION-START-HERE.md
   — High-level status dashboard for Phase 2; Phase 2b SHIPPED; eq-frontend
     blocked; investigation needed.

2. THE EXECUTABLE PLAN (load-bearing; ~1080 lines):
   tasks/granola-integration-plan.md
   — Phase 2c specifics in §Phase 2c. LOCKED-23 through LOCKED-44. Note
     that §Phase 2a and §Phase 2b are now SHIPPED — don't re-execute them.

3. THE PHASE 2b PR (merged commit `7f98920`):
   gh pr view 24 --comments
   Read the PR description for the architecture + locked decisions summary.

4. THE EQ-FRONTEND #418 PR (still open, blocked):
   gh pr view 418 -R oneilstokeseqrm/eq-frontend --comments
   Look at the Vercel preview failure URL + the build logs from the prior
   session (extract via `vercel inspect <preview-url> --logs`).

5. THE VAULT README (LOAD-BEARING; documents what shipped + Phase 2.1
   hardening list including the new comments-generator entry):
   services/vault/README.md

6. THE BRAINSTORM (background; don't re-litigate):
   docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md

7. AUTO-MEMORY (auto-loads, but verify):
   ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/
   memory/MEMORY.md
   — Confirm Active Work entry reads "PHASE_2B_SHIPPED_EQ_FRONTEND_BLOCKED"

8. PROJECT MEMORY SNAPSHOT:
   ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/
   memory/project_granola_integration.md
   — Phase 2b complete state + 7 Codex rounds + cross-agent consult outcome
     + comments-generator blocker.

9. LOAD-BEARING FEEDBACK MEMORIES (read all):
   - feedback_complete_all_handoff_reads_before_action.md
   - feedback_envelope_contract_immutable.md (LOCKED-38)
   - feedback_codex_pre_merge_gate.md (Phase 2b ran 7 rounds; gate passed)
   - feedback_shared_infrastructure_collision.md
   - feedback_tenant_isolation.md
   - feedback_branch_safety.md (verify-branch-before-commit discipline)
   - feedback_test_pattern_no_docker.md

10. NEW LESSONS FROM PRIOR SESSION (in tasks/lessons.md at the bottom):
    - "Vercel preview failure predicts production failure — treat it as a
      production-blocker, not a 'just a preview' issue"
    - "Prisma multiSchema introduction exposes brittleness in third-party
      generators (e.g., @onozaty/prisma-db-comments-generator@1.5.0)"
    - "Pool-based vault accessors avoid nested-pool-acquire deadlock; audit
      + credential commit atomically as single SQL transaction"
    - "Codex rounds find real bugs at progressively deeper layers — don't
      stop early; the gate-passing round (R7) is the merge authorization"

11. THE COMMENTS-GENERATOR BUG EVIDENCE:
    Vercel build logs from preview `eq-frontend-6cuw79l2a-...vercel.app`:
    ```
    Comments generation completed: 20260523235715_update_comments
    [...]
    Applying migration `20260523235715_update_comments`
    Error: P3018
    Database error code: 42P01
    Database error: ERROR: relation "user_credentials" does not exist
    ```
    Generator source `@onozaty/prisma-db-comments-generator@1.5.0` IS
    multiSchema-aware in its code (uses model.schema and joinNames helper)
    but DMMF integration with Prisma 5.22 + our multiSchema annotations
    must not wire through correctly for the vault models.
    Newer version 1.7.0 exists on npm; unknown if it fixes this.

12. AFTER ALL READS, write a 2-paragraph confirmation:
    - Para 1: What this session executes — investigate comments-generator
      bug via /investigate; the 3 candidate fixes (upgrade to 1.7.0,
      exclude vault tables, switch tools); how you'll get eq-frontend#418
      to a clean preview; the Phase 2b Railway-shell smoke test plan;
      Phase 2c scope (Granola HTTP API client).
    - Para 2: What's NOT in scope — Phase 2c implementation should not
      start until eq-frontend#418 deploys to production (you need real
      tables for Phase 2d's smoke test). DO NOT touch the eq-frontend
      checkout if another agent is active on a different branch (use the
      worktree pattern that prior session used: git worktree add at
      /tmp/eq-frontend-investigation, work there, push, remove worktree).
    Then proceed with /investigate.

═══════════════════════════════════════════════════════════════════════
STEP 5 — EXECUTE: /investigate THE COMMENTS-GENERATOR BUG
═══════════════════════════════════════════════════════════════════════

Run /investigate with this scope:

  "Why does @onozaty/prisma-db-comments-generator@1.5.0 emit unqualified
  SQL ('COMMENT ON COLUMN user_credentials...' instead of 'vault.user_credentials')
  for models annotated with @@schema('vault') in our eq-frontend
  schema.prisma? Identify the root cause + propose a fix."

The investigation should produce:
- Root cause (likely: DMMF doesn't populate `model.schema` for vault models
  under our Prisma 5.22 + multiSchema configuration, OR the generator
  doesn't read `.schema` at the right introspection layer)
- Candidate fixes ranked by cost + risk:
  (a) Upgrade @onozaty/prisma-db-comments-generator to 1.7.0
      (test if it fixes; lowest risk if it works)
  (b) Configure the generator to skip vault.* models
      (search node_modules README for an exclude/skip flag)
  (c) Patch the generator to correctly populate schema for multiSchema models
      (PR back to upstream; longest path)
  (d) Switch to a different generator that handles multiSchema correctly
      (highest scope)
  (e) Disable the generator entirely and check in static comment SQL
      (loses automation; not recommended unless others fail)

After /investigate completes, surface findings to user with recommendation.
User chooses path. Implement the chosen fix in a focused branch (e.g.,
`fix/comments-generator-multischema`), rebase + push, ensure eq-frontend#418's
Vercel preview passes, then surface for merge authorization.

After eq-frontend#418 merges + Vercel deploys + Neon schema updates:
- Verify via Neon MCP probe: vault schema + 3 tables present
- Run Railway-shell smoke test on live-transcription-fastapi:
  Connect via `railway run --service live-transcription-fastapi --environment production python3 -c "..."`
  Execute the smoke test from `services/vault/README.md` — positive
  (4-field context → success) AND negative (3-field context → AccessDenied)
- Both must pass before Phase 2c starts.

═══════════════════════════════════════════════════════════════════════
STEP 6 — PHASE 2c (Granola HTTP API client) — AFTER 2a deploys + smoke test
═══════════════════════════════════════════════════════════════════════

Per the plan at tasks/granola-integration-plan.md §Phase 2c:

Phase 2c adds the Granola.ai HTTP API client (~0.5 day):
- New file: services/granola_ingestion/api_client.py
- Pydantic models: GranolaFolder, GranolaNoteSummary, GranolaNoteDetail
- Methods: list_folders, list_notes, get_note_detail
- Base URL: https://public-api.granola.ai/v1 (per Phase 0 empirical correction)
- Filter param: created_after (per Phase 0 empirical correction)
- Structured error codes: auth_failed, folder_not_found, granola_5xx,
  granola_429, granola_timeout, granola_parse_error
- httpx mock transport for unit tests
- Codex pre-merge review (mandatory; 4-round soft cap)

═══════════════════════════════════════════════════════════════════════
USER POSTURE (LOAD-BEARING — DO NOT VIOLATE)
═══════════════════════════════════════════════════════════════════════

User is a non-developer founder. Make confident technical decisions;
surface only product / strategic decisions, scope deviations, or items
requiring user authorization (push, merge, destructive ops). Plain-English
explanations when user asks "why" or "what happened."

CRITICAL DISCIPLINES:

1. ALWAYS run `git branch --show-current` IMMEDIATELY before any
   `git commit` in a shared checkout. The 2026-05-23 session experienced
   a silent branch switch caused by another active agent in the same
   directory.

2. NEVER force-push to main. NEVER bypass CI on merge unless explicitly
   authorized for that specific PR.

3. When pushing a branch that triggers Vercel preview builds, EXPLICITLY
   state to the user: "this push will trigger Vercel preview builds; if
   they fail, that predicts a production failure on merge." (Lesson from
   prior session — I framed the preview failure too casually and the user
   correctly pushed back.)

4. Codex pre-merge review is MANDATORY (4-round soft cap, can extend with
   user authorization for security-critical code). Phase 2b ran 7 rounds;
   every round found real bugs.

5. Never modify downstream Pydantic envelope contracts (LOCKED-38).

6. Tenant isolation is non-negotiable — every query MUST include tenant_id
   in WHERE. The vault module's rotate_credential_key signature was
   tightened in R3 of Codex review to enforce this.

7. When pausing in a shared checkout, courtesy-switch back to main so the
   next agent doesn't inherit your feature branch.

═══════════════════════════════════════════════════════════════════════
STOP CONDITIONS (HARD — SURFACE TO USER IMMEDIATELY)
═══════════════════════════════════════════════════════════════════════

  - /context-restore returns NO_CHECKPOINTS or wrong checkpoint title
  - MEMORY.md Active Work doesn't read "PHASE_2B_SHIPPED_EQ_FRONTEND_BLOCKED"
  - live-transcription-fastapi main is NOT at `7f98920` (or descendant)
  - eq-frontend#418's branch (`phase-2/granola-vault-schema`) doesn't
    have commit `c674330` as its tip
  - Production /health on live-transcription-fastapi-production.up.railway.app
    returns non-200
  - AWS infrastructure missing — verify both:
    * aws kms describe-key --key-id 59a0e2bc-c636-45e8-bccf-427ad2426ad8
    * aws iam get-user --user-name eq-vault-service
  - Railway env vars missing on live-transcription-fastapi production:
    EQ_VAULT_AWS_ACCESS_KEY_ID, EQ_VAULT_AWS_SECRET_ACCESS_KEY,
    EQ_VAULT_KMS_KEY_ALIAS, EQ_VAULT_AWS_REGION
  - Another agent actively working in eq-frontend within the last hour
    (run: ls -lt ~/.claude/projects/-Users-peteroneil-eq-frontend/*.jsonl
    | head -3 — files modified in last hour = hazard signal)
  - User asks you to deviate from a LOCKED decision — confirm in writing
    before proceeding

═══════════════════════════════════════════════════════════════════════
COMMIT SUMMARY (Phase 2b session, all merged to main as #24)
═══════════════════════════════════════════════════════════════════════

live-transcription-fastapi main (after squash-merge of #24):
  7f98920 feat(vault): Phase 2b — KMS-backed credential vault Python module
          (services/vault/) (#24)

The squashed commit collapses 8 underlying feature-branch commits
(7892df8 → 0465b29) representing the Phase 2b code + 7 rounds of Codex
review fixes. See PR #24 description for the round-by-round narrative.

eq-frontend  branch `phase-2/granola-vault-schema` (PR #418, OPEN, BLOCKED):
  c674330 feat(prisma): Phase 2 Granola integration — vault schema + 3 new tables
  de10461 chore(prisma): commit Phase 1 comments-generator artifacts

Blocker: @onozaty/prisma-db-comments-generator@1.5.0 multiSchema gap;
Vercel preview build fails at `prisma migrate deploy` of the auto-generated
update_comments migration. Production deploy on merge would fail identically.

═══════════════════════════════════════════════════════════════════════
AWS state (verified 2026-05-23, no changes this session)
═══════════════════════════════════════════════════════════════════════

- Account 211125681610, us-east-1
- IAM user `eq-vault-service` (Arn `arn:aws:iam::211125681610:user/eq-vault-service`)
- KMS CMK `59a0e2bc-c636-45e8-bccf-427ad2426ad8` (alias `alias/eq-user-secrets`)
- IAM policy enforces 4-field EncryptionContext via `ForAllValues:StringEquals + Null:false`
- Auto-rotation ENABLED (annual; next 2027-05-23)
- Access key `AKIATCKASHXFPCDN6NXX` active; secret in Railway env vars only
- Live KMS key policy + IAM identity policy match `services/vault/policies/*.json` byte-for-byte

═══════════════════════════════════════════════════════════════════════
Railway state (verified 2026-05-24, post-merge)
═══════════════════════════════════════════════════════════════════════

- live-transcription-fastapi project `847cfa5a-b77c-4fb0-95e4-b20e8773c23e`
- Production environment `e4c5ec15-1931-4632-9e58-92d9c6be4261`
- Service `live-transcription-fastapi` `59a69f3d-9a24-4041-942a-891c4a81c5fb`
- Latest deployment: 2ce20b0e-9de3-42a5-ac66-af4cbef982d6 SUCCESS
- /health 200 verified; vault module loaded as inert code (nothing imports it yet)
- 4 env vars set: `EQ_VAULT_AWS_ACCESS_KEY_ID`, `EQ_VAULT_AWS_SECRET_ACCESS_KEY`,
  `EQ_VAULT_KMS_KEY_ALIAS=alias/eq-user-secrets`, `EQ_VAULT_AWS_REGION=us-east-1`

═══════════════════════════════════════════════════════════════════════
Vercel state (eq-frontend project, verified 2026-05-24)
═══════════════════════════════════════════════════════════════════════

- Project ID: `prj_0wDppCftk1VrSAsYswI5pnNRHdN8`
- Team ID: `team_Hnnnu6r1trggeAXYWHXpKfMt`
- Most recent production deploy: 2026-05-23 06:03:17 (ready; pre-vault work)
- PR #418 preview deploy: `eq-frontend-6cuw79l2a-...` ERROR (the blocker)
- Vercel MCP authenticated and available for next session

═══════════════════════════════════════════════════════════════════════
Linear issues (acknowledge; not in scope)
═══════════════════════════════════════════════════════════════════════

- EQ-11 — Investigate Prisma schema drift in eq-frontend + design
          cutting-edge prevention approach (Backlog, Medium)
  https://linear.app/eq-core/issue/EQ-11/...
  Related: the comments-generator bug is a SECOND eq-frontend tooling
  brittleness exposed by Phase 2a's multiSchema introduction. Consider
  whether the resolution should be tracked as part of EQ-11's scope or
  as a NEW Linear issue.

═══════════════════════════════════════════════════════════════════════

Start with /context-restore. Then verify both git states (Step 2). Then
read the comprehensive handoff document (this file, Step 3). Then the
mandatory reads in order (Step 4). Then write the 2-paragraph
confirmation. Then run /investigate on the comments-generator bug.

This session's job is fundamentally a debugging + unblocking task, NOT a
new-feature build. The cutting-edge play is: root-cause it, fix it, ship
eq-frontend#418, smoke test the vault module against real KMS, then move
to Phase 2c with confidence.

No deploys without per-action user authorization. PR creation is fine;
git push to feature branches is fine (but explicitly flag that Vercel
preview will build); git push to main, force-push, and gh pr merge
require user confirmation each time.

Always verify the current branch via `git branch --show-current` IMMEDIATELY
before any git commit, in shared checkout directories.
```
