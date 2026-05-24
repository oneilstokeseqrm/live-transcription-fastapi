# Next Session Opening Prompt (Phase 2c — Granola HTTP API client)

**Written:** 2026-05-24 end-of-session, after Phase 2a/2b foundation was fully verified end-to-end against production (vault schema + 3 tables live in Neon, vault module live on Railway, KMS smoke test PASSED, eq-frontend#418 merged with the comments-generator multiSchema fix).

**Paste the block below as the opening message of the next Claude session.**

This is the canonical paste-ready handoff. It mirrors the structure of the prompt that opened the prior two sessions (which delivered Phase 2b and the comments-generator fix). The new agent reads everything mandatory, writes a 2-paragraph confirmation, then starts building Phase 2c (the Granola HTTP API client).

---

```
You're picking up the Granola.ai transcript ingestion adapter — the prior
session fully verified the Phase 2 foundation end-to-end against production.
Vault schema + 3 tables live in Neon. Vault Python module live on Railway.
KMS smoke test PASSED (positive 4-field EncryptionContext succeeds; negative
3-field fails AccessDenied — LOCKED-40 enforced by IAM). eq-frontend#418
merged with the comments-generator multiSchema fix (ignorePattern in
schema.prisma — empirically root-caused as Prisma 5.22 DMMF omitting
`model.schema` for generators). Everything below Phase 2c works.

THIS SESSION'S JOB: build Phase 2c — the Granola HTTP API client. New file
services/granola_ingestion/api_client.py + Pydantic models (GranolaFolder,
GranolaNoteSummary, GranolaNoteDetail) + 3 async methods (list_folders,
list_notes, get_note_detail) + structured error codes + httpx mock
transport tests + Codex pre-merge review. ~0.5 day estimated.

CONTINUITY IS CRITICAL. The prior session's handoff artifacts are
comprehensive and load-bearing. Read everything. Trust but verify each
artifact loads as expected. If anything is missing or unexpected, STOP and
surface to the user — do not improvise.

═══════════════════════════════════════════════════════════════════════
STEP 1 — RUN /context-restore FIRST
═══════════════════════════════════════════════════════════════════════

Before anything else, run /context-restore. It will load the most recent
checkpoint:

  ~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/
  <timestamp>-phase-2-foundation-verified-phase-2c-next.md

If /context-restore returns NO_CHECKPOINTS or a different title (e.g., the
prior session's "phase-2b-shipped-eq-frontend-blocked"), STOP and surface
to the user immediately — the handoff is broken.

═══════════════════════════════════════════════════════════════════════
STEP 2 — VERIFY CURRENT GIT + PRODUCTION STATE
═══════════════════════════════════════════════════════════════════════

Verify both checkouts:

  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi
  git branch --show-current     # expect: main
  git log --oneline -5          # expect tip includes 7f98920 (Phase 2b)
  git status --short            # expect: clean

  git -C /Users/peteroneil/eq-frontend log origin/main --oneline -3
                                # expect tip: 7905222 feat(prisma): Phase 2a
                                # — Granola vault schema (#418)
                                # — DO NOT switch the main checkout's
                                # branch (another agent may be active)

Verify production health (stop conditions):

  curl -s https://live-transcription-fastapi-production.up.railway.app/health
  # expect: {"status":"ok"}

Verify production Neon (via Neon MCP, project super-glitter-11265514,
branch br-holy-block-ads5069w, database neondb):
  - schema 'vault' exists
  - 3 tables present: vault.user_credentials (15 cols), vault.credential_access_log (11 cols), public.external_integration_runs (17 cols)
  - migration 20260523100441_granola_vault_schema has finished_at set
  - migration 20260523235715_update_comments has rolled_back_at set
  - migrations 20260524101648_update_comments + 20260524103705_update_comments both have finished_at set

If any of those is wrong, STOP and surface.

═══════════════════════════════════════════════════════════════════════
STEP 3 — READ THE COMPREHENSIVE HANDOFF DOCUMENT
═══════════════════════════════════════════════════════════════════════

The PRIMARY HANDOFF DOCUMENT is this file itself:

  docs/superpowers/specs/2026-05-24-phase-2c-session-prompt.md

It contains the full sequence below. After STEP 3, proceed to STEP 4 (the
mandatory reads).

═══════════════════════════════════════════════════════════════════════
STEP 4 — MANDATORY READS (in this order; do NOT skip)
═══════════════════════════════════════════════════════════════════════

Per the memory entry feedback_complete_all_handoff_reads_before_action.md:
complete EVERY read BEFORE starting Phase 2c implementation. Don't write
code mid-read. Use parallel tool calls to read fast.

1. WAYFINDING:
   docs/superpowers/specs/NEXT-SESSION-START-HERE.md
   — High-level status dashboard: PHASE_2_FOUNDATION_VERIFIED state, Phase
     2c scope summary, full lookup table of what's live in production.

2. THE EXECUTABLE PLAN (load-bearing; ~1080 lines):
   tasks/granola-integration-plan.md
   — Phase 2c is §Phase 2c. LOCKED-23 through LOCKED-44.
   — Phase 2a (§Phase 2a) and Phase 2b (§Phase 2b) are SHIPPED — don't
     re-execute them. Skim only to refresh context.
   — Phase 0 §step 6 documented the EMPIRICAL Granola API findings: base
     URL is https://public-api.granola.ai/v1 (NOT api.granola.ai); filter
     param is created_after (NOT since); note IDs start "not_"; folder IDs
     start "fol_"; /folders returns {folders, hasMore, cursor}. These
     override the brainstorm doc.

3. THE BRAINSTORM (background only; don't re-litigate):
   docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md

4. THE VAULT README (LOAD-BEARING for understanding what Phase 2c calls
   into in Phase 2d):
   services/vault/README.md
   — Public API exports (get_granola_credential_for_user, store_credential,
     rotate_credential_key, reactivate_credential, ALLOWLIST,
     VaultError/VaultErrorCode, VaultPermissionError).
   — Audit log entry from 2026-05-24 documents the comments-generator fix
     + KMS smoke test results.
   — Phase 2.1 hardening list updated with item #14 RESOLVED.

5. THE PHASE 2b PR (merged commit 7f98920) — context for the vault module
   Phase 2c will eventually consume in Phase 2d:
   gh pr view 24

6. THE EQ-FRONTEND #418 PR (merged commit 7905222) — context for what
   went into production:
   gh pr view 418 -R oneilstokeseqrm/eq-frontend

7. AUTO-MEMORY (auto-loads, but verify):
   ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/
   memory/MEMORY.md
   — Active Work entry should read PHASE_2_FOUNDATION_VERIFIED + PHASE_2C_NEXT
     2026-05-24.

8. PROJECT MEMORY SNAPSHOT:
   ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/
   memory/project_granola_integration.md
   — Foundation verified state + KMS smoke test results + comments-
     generator root-cause forensics.

9. LOAD-BEARING FEEDBACK MEMORIES (read all):
   - feedback_complete_all_handoff_reads_before_action.md
   - feedback_envelope_contract_immutable.md (LOCKED-38)
   - feedback_codex_pre_merge_gate.md (Phase 2b ran 7 rounds; Phase 2c
     likely 4 rounds soft cap)
   - feedback_shared_infrastructure_collision.md
   - feedback_tenant_isolation.md
   - feedback_branch_safety.md
   - feedback_test_pattern_no_docker.md

10. NEW LESSONS FROM PRIOR SESSION (at bottom of tasks/lessons.md):
    - "Prisma 5.22 DMMF omits model.schema for generators" — explains
      why the comments-generator bug exists and why ignorePattern is the
      right Phase 2 fix (Prisma 7.x upgrade is the deeper fix, tracked
      under EQ-11).
    - "P3009 leftover failed-migration record blocks all subsequent
      migrations" — operational gotcha for production Prisma debugging.
    - "pnpm + tools with wide major-version ranges may resolve different
      majors than your project" — explains how Prisma CLI 5.22 ended up
      with @prisma/generator-helper 7.5.0 in the same node_modules.
    - "Vercel preview builds run against the same production Neon DB" —
      changes blast-radius math for preview builds.
    — Plus all 11 earlier lessons (carry forward).

11. THE GRANOLA INTEGRATION PLAN §Phase 2c (this is the executable spec):
    tasks/granola-integration-plan.md §Phase 2c (lines ~404-447)

12. AFTER ALL READS, write a 2-paragraph confirmation:
    - Para 1: What this session executes — Phase 2c (services/granola_
      ingestion/api_client.py + 3 Pydantic models + 3 async methods +
      structured error mapping + httpx mock tests + Codex pre-merge
      review). Cite the empirical Granola API findings: base URL is
      public-api.granola.ai/v1, filter is created_after, IDs start
      "not_" / "fol_", /folders returns {folders, hasMore, cursor}.
      Mention you'll use AsyncMock-style unit tests (per
      feedback_test_pattern_no_docker), NOT Testcontainers.
    - Para 2: Critical disciplines: (a) work on a feature branch
      phase-2c/granola-api-client off live-transcription-fastapi main;
      (b) verify branch via `git branch --show-current` IMMEDIATELY
      before any commit; (c) Codex pre-merge review is MANDATORY (4-round
      soft cap; extendable if real bugs surface each round); (d) per-
      action user authorization for push to main / merge / destructive
      ops; (e) DO NOT modify downstream Pydantic envelope contracts
      (LOCKED-38); (f) DO NOT call vault module from api_client.py — the
      adapter (Phase 2d) calls vault, api_client.py just speaks HTTP.

═══════════════════════════════════════════════════════════════════════
STEP 5 — EXECUTE PHASE 2c
═══════════════════════════════════════════════════════════════════════

Per tasks/granola-integration-plan.md §Phase 2c:

**Branch:** create feature branch `phase-2c/granola-api-client` off main

**New file:** services/granola_ingestion/api_client.py

**Sibling files (NEW):**
- services/granola_ingestion/__init__.py (empty for now; Phase 2d will export)
- services/granola_ingestion/errors.py (structured error codes — separate
  module so Phase 2d can import without dragging the api_client class)
- services/granola_ingestion/models.py (Pydantic models — same reason)

**API client signatures:**

  class GranolaAPIClient:
      def __init__(self, api_key: str, *, base_url: str = "https://public-api.granola.ai/v1",
                   timeout: float = 30.0, http_client: httpx.AsyncClient | None = None) -> None: ...

      async def list_folders(self) -> list[GranolaFolder]: ...

      async def list_notes(
          self,
          *,
          folder_id: str,
          created_after: datetime | None = None,
          limit: int = 100,
      ) -> list[GranolaNoteSummary]: ...

      async def get_note_detail(self, note_id: str) -> GranolaNoteDetail: ...

**Pydantic models (services/granola_ingestion/models.py):**
- GranolaFolder: id, name, parent_folder_id
- GranolaNoteSummary: id, title, created_at, updated_at, folder_membership
- GranolaNoteDetail: id, title, created_at, updated_at, attendees (list),
  calendar_event (optional), transcript (list of speaker turns or string),
  summary_markdown, summary_text, web_url
- Attendee, CalendarEvent, TranscriptTurn helper models as needed

Match the EMPIRICAL Granola API response shapes verified in Phase 0
(see brainstorm doc §"Empirical Granola API findings" and plan §Phase 0
step 6 empirical outcome). If the actual response shape differs from the
brainstorm doc when called against Peter's real Granola account, flag the
divergence and update the models to match what Granola actually returns
(don't force-fit a wrong assumption).

**Structured error codes (services/granola_ingestion/errors.py):**

  class GranolaErrorCode(str, Enum):
      AUTH_FAILED = "granola_auth_failed"
      FOLDER_NOT_FOUND = "granola_folder_not_found"
      RATE_LIMITED = "granola_429"
      OUTAGE_5XX = "granola_5xx"
      TIMEOUT = "granola_timeout"
      PARSE_ERROR = "granola_parse_error"

  class GranolaError(Exception):
      def __init__(self, error_code: GranolaErrorCode, message: str = "", *, http_status: int | None = None): ...

(Module-prefixed names so they don't collide with VaultErrorCode etc.)

**HTTP behavior:**
- Timeout 30s per request
- Retry strategy on 5xx + httpx.TimeoutException + ConnectError:
  exponential backoff with jitter: 1s → 2s → 4s → 8s, max 4 retries
- 429 handling: honor Retry-After header (sleep that long, then retry,
  doesn't count against the 4-retry budget per Granola best practice)
- 401 → GranolaError(AUTH_FAILED), NO retry (auth failures don't get
  better with retry)
- 404 on folder lookup → GranolaError(FOLDER_NOT_FOUND), no retry
- Other 4xx → GranolaError with appropriate code; no retry

**Unit tests (tests/unit/granola_ingestion/test_api_client.py):**
Use httpx.MockTransport — pure Python, no network, no Docker (per
feedback_test_pattern_no_docker).

Required test cases:
- list_folders happy path
- list_notes happy path with created_after
- list_notes without created_after (no filter)
- get_note_detail happy path
- 5xx with retry → eventual success (assert backoff timing minimal)
- 5xx exhausted → granola_5xx after 4 retries
- 401 → granola_auth_failed (no retry; assert call count = 1)
- 429 with Retry-After=N → honored
- 429 without Retry-After → uses default backoff
- httpx.TimeoutException → granola_timeout
- Malformed response (missing required field) → granola_parse_error
- 404 on get_note_detail → appropriate error code (could be note_not_found
  or folder_not_found depending on Granola's response — verify empirically)

Target: ~10-15 unit tests.

**Codex pre-merge review:**
1. Open PR to live-transcription-fastapi main.
2. Run `/codex review` (4-round soft cap; extend if rounds keep finding
   real bugs).
3. Fold all P1 findings; P2 judgment call (fold if small, ticket if
   large).
4. Surface to user for merge authorization after gate passes (0 P1).

**Merge + deploy:**
1. User authorizes merge.
2. Squash-merge to main.
3. Railway deploys.
4. /health 200 verified.
5. Module is inert (Phase 2d will be first to import it).

═══════════════════════════════════════════════════════════════════════
STEP 6 — PHASE 2d (NOT IN THIS SESSION)
═══════════════════════════════════════════════════════════════════════

Phase 2d (~1.5 days; deserves its own session) brings together the vault
module + api_client.py + envelope construction + Path 2 logic:

- services/granola_ingestion/adapter.py — the core per-credential cycle
- services/granola_ingestion/path2.py — attendee classification +
  Scenario A/B/C/D branching
- services/granola_ingestion/outcomes.py — IngestionOutcome enum

DO NOT START PHASE 2d THIS SESSION. Phase 2c ships, gets reviewed +
merged, then Phase 2d is its own session.

═══════════════════════════════════════════════════════════════════════
USER POSTURE (LOAD-BEARING — DO NOT VIOLATE)
═══════════════════════════════════════════════════════════════════════

Non-developer founder. Plain-English explanations always. Confident
technical decisions; surface only product / strategic decisions, scope
deviations, or destructive ops. AI agent doesn't push or merge without
per-action authorization (PR creation is fine; push to feature branches
is fine; push to main + merge + force-push require explicit auth each
time).

═══════════════════════════════════════════════════════════════════════
CRITICAL DISCIPLINES (carry forward from prior sessions)
═══════════════════════════════════════════════════════════════════════

1. **Branch verification before commits.** `git branch --show-current`
   IMMEDIATELY before any commit. The 2026-05-23 session experienced a
   silent branch switch in a shared checkout; this session re-confirmed
   the discipline. Do not skip.

2. **Codex pre-merge gate is MANDATORY.** 4-round soft cap. Phase 2b ran
   7 rounds because each round found real P1s. Phase 2c is HTTP client
   code, likely 4 rounds is enough. Extend if real bugs surface each
   round.

3. **NEVER modify downstream Pydantic envelope contracts** (LOCKED-38).
   Phase 2c doesn't touch envelopes directly (that's Phase 2d), but the
   discipline still applies if you find yourself reaching for it.

4. **Tenant isolation is non-negotiable.** Phase 2c is a stateless HTTP
   client — it doesn't query the DB — so there's no tenant_id in scope
   here. But: the api_key passed to GranolaAPIClient.__init__ is a
   per-USER secret (LOCKED-23). Don't log it. Don't include it in
   error messages. Don't include it in test fixtures committed to git.

5. **No Docker in tests by default.** AsyncMock-style unit tests using
   httpx.MockTransport (or respx if cleaner). Tests/integration testing
   against real Granola happens in Phase 4 (production E2E with Peter
   as design partner #0), not Phase 2c.

6. **Empirical Granola API findings override the brainstorm doc.**
   Phase 0 verified: base URL public-api.granola.ai/v1, filter
   created_after, IDs not_/fol_, /folders returns {folders, hasMore,
   cursor}. If the actual response shape differs when you call the real
   API, surface the divergence and update the models — don't force-fit
   to a wrong assumption.

7. **The vault module is inert until Phase 2d imports it.** Phase 2c
   should NOT import services.vault or trigger any vault accessor. The
   api_client.py constructs are pure HTTP; the api_key comes in as a
   constructor argument from caller (in Phase 2d the caller will be
   the adapter, which uses vault to get the decrypted api_key).

═══════════════════════════════════════════════════════════════════════
STOP CONDITIONS (HARD — SURFACE TO USER IMMEDIATELY)
═══════════════════════════════════════════════════════════════════════

  - /context-restore returns NO_CHECKPOINTS or wrong checkpoint title
  - MEMORY.md Active Work doesn't read "PHASE_2_FOUNDATION_VERIFIED" /
    "PHASE_2C_NEXT"
  - live-transcription-fastapi main is NOT at 7f98920 (or descendant)
  - eq-frontend main is NOT at 7905222 (or descendant)
  - Production /health on live-transcription-fastapi-production.up.railway.app
    returns non-200
  - Vault schema or 3 tables MISSING from production Neon
  - AWS infrastructure missing (verify):
    * aws kms describe-key --key-id 59a0e2bc-c636-45e8-bccf-427ad2426ad8
    * aws iam get-user --user-name eq-vault-service
  - Another agent actively working in live-transcription-fastapi within
    the last hour (run: ls -lt ~/.claude/projects/-Users-peteroneil-EQ-
    CORE-live-transcription-fastapi/*.jsonl | head -3 — files modified
    in last hour = hazard signal; if so, switch to a Conductor worktree)
  - User asks you to deviate from a LOCKED decision (LOCKED-23 through
    LOCKED-44) — confirm in writing before proceeding
  - Phase 2c starts importing services.vault — STOP, you've crossed
    into Phase 2d scope; Phase 2c should be pure HTTP client

═══════════════════════════════════════════════════════════════════════
KEY STATE (verified 2026-05-24)
═══════════════════════════════════════════════════════════════════════

live-transcription-fastapi main:
  ebb0c84 docs(handoff): forensic bug-evidence file
  801f358 docs(handoff): Phase 2b SHIPPED + eq-frontend BLOCKED
  7f98920 feat(vault): Phase 2b — KMS-backed credential vault Python
          module (services/vault/) (#24)
  [...this session: handoff docs only on main, no code commits to lt-fa]

eq-frontend main (post-merge of #418):
  7905222 feat(prisma): Phase 2a — Granola vault schema (vault.user_credentials
          + credential_access_log + external_integration_runs) (#418)
  4cdebec docs(w3): Session 26 kickoff (other agent's work)

AWS (us-east-1, account 211125681610):
  KMS CMK    59a0e2bc-c636-45e8-bccf-427ad2426ad8 (alias eq-user-secrets)
  IAM user   eq-vault-service (Arn arn:aws:iam::211125681610:user/eq-vault-service)
  Auto-rotation enabled; next 2027-05-23

Railway (live-transcription-fastapi production):
  Project    847cfa5a-b77c-4fb0-95e4-b20e8773c23e
  Env        e4c5ec15-1931-4632-9e58-92d9c6be4261
  Service    59a69f3d-9a24-4041-942a-891c4a81c5fb
  Latest deploy 2ce20b0e-9de3-42a5-ac66-af4cbef982d6 SUCCESS
  /health 200 at https://live-transcription-fastapi-production.up.railway.app/health
  4 EQ_VAULT_* env vars set + working (KMS smoke test PASSED)

Vercel (eq-frontend production):
  Project    prj_0wDppCftk1VrSAsYswI5pnNRHdN8
  Team       team_Hnnnu6r1trggeAXYWHXpKfMt
  Latest production deploy 2he8eDSfSLdapZ1eRXa6mSpjJkdq READY at 2026-05-24 10:40:22Z
  Canonical URL eq-frontend-two.vercel.app
  Vercel MCP authenticated

Neon (production Postgres):
  Project    super-glitter-11265514 (eq-dev)
  Branch     br-holy-block-ads5069w (production)
  Database   neondb
  Endpoint   ep-silent-waterfall-adtinpn1.c-2.us-east-1.aws.neon.tech
  Vault schema + 3 tables LIVE (verified end-to-end via Neon MCP)

Linear:
  EQ-11 — schema drift family; comments-generator fix RESOLVED this
  session via ignorePattern (Path B); deeper fix (Prisma 7.x upgrade)
  still tracked under EQ-11.

═══════════════════════════════════════════════════════════════════════

Start with /context-restore. Then verify git states + production health
(Step 2). Then read this comprehensive handoff document (Step 3). Then
the mandatory reads (Step 4). Then write the 2-paragraph confirmation.
Then begin Phase 2c implementation (Step 5).

This session is fundamentally a build task: create the Granola HTTP API
client, test it thoroughly with mocks, get it through Codex pre-merge
review, ship it as PR to live-transcription-fastapi main. Phase 2d
(adapter + Path 2 logic) is the NEXT session — keep this one focused on
the API client alone.

No deploys without per-action user authorization. PR creation is fine;
git push to feature branches is fine; git push to main, force-push, and
gh pr merge require user confirmation each time.

Always verify the current branch via `git branch --show-current`
IMMEDIATELY before any git commit in shared checkout directories.
```
