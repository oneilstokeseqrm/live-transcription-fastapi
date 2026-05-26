# Next Session — Start Here

**Project:** Phase 2 — Granola.ai transcript ingestion integration.
**Last session:** 2026-05-26 — WIRED the 5-min EventBridge trigger (scheduled **Rule**, not Scheduler — Scheduler can't POST HTTP; PR #31 infra doc) + ran the FIRST real `/connect` E2E end-to-end. It PASSED (a real meeting ingested + enriched: 5 summaries + 2 insights). Along the way it caught + fixed + shipped **3 latent P0 bugs**: transcript times are ISO strings not floats (PR #32 `ab2ae3d`), calendar event field is `calendar_event_id` not `id` (PR #33 `5441252`), envelope `trace_id` was None → Lane 2 crash (PR #34 `1cd746f`). All merged + deployed + verified live.
**Status:** 🟢 **TRIGGER_LIVE + E2E_PASSED + P0s_SHIPPED.** main @ `1cd746f`; Railway `a4ff3c54` SUCCESS; /health 200. The founder's Granola credential is **CONNECTED** and the trigger is **LIVE** (deliberately left running; LOCKED-11 cleanup NOT run). ⚠️ **CRITICAL GAP found by founder:** Granola-ingested interactions do NOT resolve/create/link **contacts** from attendees the way the other ingestion paths do (known-account attendees never created/linked; unknown-company attendees get an unlinked contact at approval). Root cause: Granola never passes `contact_ids` to Lane 2 → `_persist_contact_links` (which writes `raw_interactions` + links) never runs. See `memory/project_granola_contact_resolution_gap.md`.

**Paste-ready opening prompt for the next session:** `docs/superpowers/specs/2026-05-27-granola-contact-resolution-next-session-prompt.md` — self-contained: /context-restore checkpoint to load ("granola-e2e-passed-contact-resolution-next"), mandatory reads, verify-state, the contact-resolution work (deep investigation → plan → review → build → E2E), disciplines, KEY STATE (SHAs/IDs/connected-credential/EventBridge resources), carryover + known issues. (Superseded: `2026-05-26-next-session-prompt.md` — trigger + E2E, now done.)

## What's next (edge #12 done; trigger + E2E next)

1. **Wire the 5-min cron trigger** (the "flip the switch" step). User chose Railway cron service originally but it's fiddly (curl-image entrypoint + shell env-expansion + dashboard-only `cronSchedule`); **recommended alt: a GitHub Actions scheduled workflow** (`.github/workflows/granola-cron.yml`, `*/5 * * * *`) POSTing `/internal/granola/cron-tick` with `INTERNAL_CRON_SECRET` (already set in Railway; add it to GitHub Actions secrets). Decide approach WITH the user.
2. **First real `/connect` E2E** with Peter's Granola account (design partner #0) — connect → poll → ingest, verify a real meeting lands. Run the §Phase 4c steps + LOCKED-11 cleanup.
3. **2 ticketed fast-follow edges** (plan §Phase 2.1 follow-ups #12/#13, user chose ship-now in Phase 2f):
   - #12 **adapter archived_at-awareness** — an in-flight cycle keeps ingesting a few notes after `/disconnect` (root fix: guard the 3 adapter credential-UPDATE SQLs on `archived_at IS NULL` + re-check before publish; would let the per-endpoint `_credential_poll_lock` gates be removed). Do this BEFORE or WITH wiring the pinger (the pinger makes the race live).
   - #13 **/connect bad-folder recovery** — a bad `folder_id` leaves a stuck non-archived row (must `/disconnect` to retry; no PATCH /folder shipped).
4. **Phase 2g** — transactional email on credential breakage (LOCKED-32). Then Phase 3 (frontend Connect page + Pending Approvals) + Phase 4 (full E2E).

**⚠️ Prod config note (load-bearing):** `ALLOW_LEGACY_HEADER_AUTH=true` in production. `get_auth_context_*` does NOT enforce JWT in prod (falls back to header auth). Any NEW JWT-only endpoint needs an explicit bearer-token gate (stateless) or `pg_user_id` requirement (writes user_id) — see `tasks/lessons.md` bottom.

**Superseded prompts:** `docs/superpowers/specs/2026-05-24-phase-2f-session-prompt.md` (Phase 2f opener, now done); `docs/superpowers/specs/2026-05-24-phase-2e-session-prompt.md` (Phase 2d).

---

## What shipped this session (2026-05-24)

### PR #26 (PR-X1) — `services/text_clean_service.py` extraction ✅ MERGED + DEPLOYED

- Squash-merged to live-transcription-fastapi main as commit `fa97477`
- Railway deployment SUCCESS
- `/health` 200 + `/text/clean` smoke probe HTTP 200 verified (no regression)
- 23 new unit tests in `tests/unit/test_text_clean_service.py`
- 9 existing integration tests in `test_text_clean_response_decoupling.py` updated + still pass
- 5 Codex rounds (R5 CLEAN cumulative)

**Why this PR was added mid-session:** Investigation found that LOCKED-41 referenced `services/text_clean_service.py` but the file didn't exist — the prior Phase 2b and 2c sessions had shipped without doing the extraction LOCKED-41 required. The Phase 2c→2d handoff listed it as a mandatory read but pre-flight discovered the gap. User-authorized a split into two PRs: PR-X1 closes the LOCKED-41 gap; PR-X2 builds on it.

### PR #27 (PR-X2) — Phase 2d Granola adapter + Path 2 logic ✅ MERGED + DEPLOYED

- Squash-merged to live-transcription-fastapi main as commit `607121d`
- Railway deployment `edbcf4ef` SUCCESS
- `/health` 200 + `/text/clean` smoke probe HTTP 200 post-deploy (no regression)
- Production module imports cleanly (first PR to exercise the full vault → adapter import chain; cryptography wheel present on Railway)
- 38 new unit tests (17 path2 + 21 adapter, +6 added during R1/R2 folds = 27 adapter total)
- 84 granola tests pass; 327 unit + integration tests pass overall; 0 regressions
- 3 substantive Codex rounds + 1 cumulative-timeout (R3 CLEAN delta after R1+R2 folds)

---

## Codex full trajectory cross-PR (this session)

| PR | Round | Base | P1 | P2 | P3 | Real findings |
|---|---|---|---|---|---|---|
| X1 | R1 | main | 0 | 1 | 0 | Slot double-release on non-Lane1 raise |
| X1 | R2 | R1 | 0 | 0 | 0 | CLEAN (delta) |
| X1 | R3 | main | 0 | 1 | 1 | LOCKED-41 kwargs; empty-string override |
| X1 | R4 | R3 | 0 | 0 | 0 | CLEAN (delta) |
| X1 | R5 | main | 0 | 0 | 0 | **CLEAN cumulative — convergence** |
| X2 | R1 | main | 2 | 1 | 0 | Stranded failed; watermark race; pub→DB dup |
| X2 | R2 | R1 | 1 | 3 | 0 | SELECT/UPSERT eq_iid; retry budget; C defer |
| X2 | R3 | R2 | 0 | 0 | 0 | **CLEAN (delta) — convergence** |
| X2 | R4 | main | — | — | — | timeout @ 3814 lines (delta scoping works past 1500) |

**Pattern observed (codified as lesson #2 this session):** PR-X1 R1 fix introduced a narrower scope-creep bug (R3 LOCKED-41 + R3 empty-string); PR-X2 R1 fix introduced a narrower bug (R2 SELECT projection + R2 UPSERT COALESCE); both converged at R3 (delta). Round-N convergence is the norm, not the exception.

---

## What's now hardened (compared to the initial Phase 2d draft)

**PR-X1 hardening:**
- Slot lifecycle owned by `text_clean_service.process()` on every exit path (no double-release on non-Lane1 raises)
- LOCKED-41 cross-tenant guard via explicit tenant_id/user_id/account_id kwargs + envelope cross-check; raises `TenantIsolationError` on mismatch
- Empty-string `cleaned_transcript` override preserved (not falling back to envelope.content.text)
- Backpressure state + lifespan-drain target moved to shared service (both /text/clean and Granola adapter use the same cap)

**PR-X2 hardening:**
- `GRANOLA_NOTE_NOT_FOUND` = per-note skip (credential STAYS active) — the load-bearing Phase 2c finding
- LOCKED-44 snapshot recoverability verified by `test_reprocess_deferred_note_recovers_from_snapshot_when_404`
- Failed-row retry (not just deferred) — stranded-failure bug closed
- Cycle-start watermark (not cycle-end) — note-during-cycle-window race closed
- `eq_interaction_id` pre-written before publish + reused on retry — duplicate-publish bug closed
- COALESCE on UPSERT preserves prior eq_interaction_id on retries
- Retry budget converges to FAILED_PERMANENT under sustained outages
- Scenario C reclassification from failed-row replay now defers (LOCKED-44 snapshot captured)

---

## What's verified live in production

| Layer | Status |
|---|---|
| AWS KMS CMK `59a0e2bc-...` (alias `eq-user-secrets`) | ✅ Auto-rotation enabled, LOCKED-40 enforced |
| IAM user `eq-vault-service` + identity policy | ✅ Empirically verified |
| Vault Python module (`services/vault/`) | ✅ Live on Railway |
| Granola API client (`services/granola_ingestion/api_client.py`) | ✅ Live (Phase 2c) |
| **Granola adapter (`services/granola_ingestion/adapter.py`)** | ✅ **NEW: Live as inert code (Phase 2d, this session)** |
| **text_clean_service (`services/text_clean_service.py`)** | ✅ **NEW: Live + actively serving /text/clean (PR-X1, this session)** |
| Production Neon `vault` schema + 3 tables | ✅ Live |
| eq-frontend main (vault Prisma models + ignorePattern fix) | ✅ Commit `7905222` |
| live-transcription-fastapi `/health` | ✅ HTTP 200 |
| live-transcription-fastapi `/text/clean` smoke probe | ✅ HTTP 200 (post-PR-X1 + post-PR-X2 deploys) |
| eq-frontend production deploy | ✅ READY (commit `7905222`) |

---

## What's next: Phase 2e — Granola scheduler (~0.5 day)

Per `tasks/granola-integration-plan.md` §Phase 2e + LOCKED-28 + LOCKED-39:

**New files:**
- `services/granola_ingestion/scheduler.py` — DBOS workflow + @DBOS.step functions
- `routers/granola_cron.py` — HTTP endpoint Railway cron POSTs every 5 min

**What it does:**
1. Railway cron posts to `/internal/granola/cron-tick` every 5 min (auth via X-Internal-Cron-Secret header)
2. The handler lists active credentials via a DBOS step
3. For each credential, dispatches `granola_poll_one_credential` via `DBOS.start_workflow(workflow_id=f"granola_poll_{credential_id}_{cycle_window//5}", ...)` — SetWorkflowID dedup catches overlapping cycles
4. The workflow loads the credential (vault decryption via @DBOS.step), then calls `run_one_cycle(credential=credential, pool=pool)` — the existing Phase 2d adapter
5. PollResult observability surfaced via `/health` (extension) and structured logs

**Critical disciplines for Phase 2e:**
- LOCKED-39 (NO @DBOS.scheduled — use external Railway cron + explicit SetWorkflowID)
- workflow_id is `f"granola_poll_{credential_id}_{cycle_window_minute//5}"` (5-min window dedup)
- Pure orchestration in workflows; all I/O lives in @DBOS.step (matches repo's existing DBOS discipline)
- Cron auth via INTERNAL_CRON_SECRET env var (random 32-byte hex)
- DO NOT manipulate Granola API directly from the cron handler — dispatch to DBOS for durability + retries

**The "switch" status after Phase 2e ships:**
- Scheduler runs every 5 min but finds 0 active credentials (no Phase 2f means no /connect endpoint, so no users have connected Granola yet)
- This is correct + desired: ship the scheduler first so Phase 2f's /connect → run_one_cycle path works end-to-end the day Phase 2f deploys

---

## What this session's work does NOT include

- Phase 2f (admin endpoints — `/validate`, `/connect`, `/rotate`, `/status`, `/disconnect`) — separate session, ~0.5 day
- Phase 2g (transactional email on credential breakage) — separate session, ~0.5 day
- Phase 3 (frontend) — separate session, ~2 days
- Phase 4 (production E2E with Peter as design partner #0) — separate session, ~1 day
- Fixing the live-db CI gap (Linear EQ-11 family)
- Upgrading Prisma 5.22 → 7.x (Linear EQ-11 family)
- Fixing the 16 pre-existing `test_queue_lifecycle.py` failures (unrelated to Granola work)

---

## CRITICAL — What NOT to re-litigate

- LOCKED-23 through LOCKED-44 are non-negotiable without strong new information + explicit user authorization to revisit.
- Phase 2a (Prisma migration) is merged to eq-frontend main; vault tables LIVE in production Neon.
- Phase 2b (vault module) is merged to live-transcription-fastapi main; module live as inert code; KMS smoke test PASSED.
- Phase 2c (HTTP API client) is merged to live-transcription-fastapi main; module live as inert code.
- **PR-X1 (text_clean_service extraction) is merged to live-transcription-fastapi main (`fa97477`); module live + actively serving /text/clean.**
- **PR-X2 (Phase 2d adapter + Path 2) is merged to live-transcription-fastapi main (`607121d`); module live as inert code (the scheduler from Phase 2e will be the first to invoke `run_one_cycle`).**
- The 5-value IngestionOutcome enum is canonical (Phase 2e doesn't add a new value; the 'in_progress' string is an intermediate state managed by the SQL helper, not exposed in the enum).

---

## NEW lessons codified this session (`tasks/lessons.md`)

1. **Verify mandatory-read files exist before declaring them in handoffs.** The Phase 2c→2d handoff listed `services/text_clean_service.py` as a mandatory read but the file didn't exist; LOCKED-41 had locked the decision without scheduling the extraction work. Pre-flight every mandatory-read path with `ls`/`Read` before declaring it readable; when a LOCKED decision references a file, treat its creation as an explicit prerequisite milestone, not implicitly bundled into the first downstream phase.

2. **Adapter-pattern PRs converge in 2-3 Codex rounds with scope-creep follow-ons.** PR-X1 (extraction) and PR-X2 (adapter composition) both had 2-3 substantive Codex rounds before R3/R5 CLEAN. The pattern: R1 surfaces real bugs at the largest blast-radius surface; fix introduces a narrower bug at a smaller surface; R2 catches that; R3 CLEAN. Refines `[[feedback-codex-pre-merge-gate]]` with PR-pattern-specific guidance.

3. **Pre-write idempotency anchor BEFORE the downstream publish call.** The adapter pre-writes `status='in_progress'` + `eq_interaction_id` to `external_integration_runs` BEFORE calling `text_clean_service.process()`. If publish succeeds but the success UPSERT fails, the next cycle's idempotency check recovers the prior interaction_id and re-publishes under the same id — downstream consumers dedup. Without the pre-write, the retry would mint a new interaction_id and downstream would see two interactions for the same Granola note.

---

## Stop conditions for the next session

- `/context-restore` returns NO_CHECKPOINTS or wrong checkpoint title
- MEMORY.md Active Work doesn't read "PHASE_2D_SHIPPED" / "PHASE_2E_NEXT"
- live-transcription-fastapi main is NOT at `607121d` (or descendant)
- eq-frontend main is NOT at `7905222` (or descendant)
- Production `/health` returns non-200
- Vault schema + 3 tables NOT present in production Neon
- AWS infrastructure missing
- Another agent actively working in live-transcription-fastapi within the last hour
- User asks you to deviate from a LOCKED decision (LOCKED-23..44) without explicit written confirmation
- Phase 2e starts using `@DBOS.scheduled` instead of external cron + SetWorkflowID → STOP (LOCKED-39 violation)
- Phase 2e tries to manipulate Granola API directly from the cron handler (instead of dispatching to a DBOS workflow) → STOP (durability + retries come from DBOS)

---

## AWS account state (unchanged this session)

- Account ID: `211125681610`, us-east-1
- IAM user `eq-vault-service` + KMS CMK `59a0e2bc-c636-45e8-bccf-427ad2426ad8` (alias `alias/eq-user-secrets`)
- Auto-rotation enabled (annual; next 2027-05-23)
- Access key `AKIATCKASHXFPCDN6NXX` active; secret in Railway env vars only

## Railway state (verified 2026-05-24 end-of-session)

- Project `847cfa5a-b77c-4fb0-95e4-b20e8773c23e` (live-transcription-fastapi)
- Production environment `e4c5ec15-1931-4632-9e58-92d9c6be4261`
- Service `59a69f3d-9a24-4041-942a-891c4a81c5fb`
- **Latest deployment: `edbcf4ef-8bf3-4be2-80da-b35c98cc267f` SUCCESS** (PR-X2 merge)
- `/health` 200 at https://live-transcription-fastapi-production.up.railway.app/health
- 4 vault env vars still set + working
- Phase 2e adds new env var `INTERNAL_CRON_SECRET` (random 32-byte hex)

## Vercel state (eq-frontend, unchanged)

- Project ID: `prj_0wDppCftk1VrSAsYswI5pnNRHdN8`
- Team ID: `team_Hnnnu6r1trggeAXYWHXpKfMt`
- Production deploy `2he8eDSfSLdapZ1eRXa6mSpjJkdq` READY

## Neon state (production, unchanged)

- Project `super-glitter-11265514` (eq-dev), branch `br-holy-block-ads5069w`
- Database `neondb`
- Vault schema + 3 tables LIVE
- **Important:** Vercel preview builds run against the same production Neon DB (lessons.md)

---

## Linear EQ-11 — schema drift investigation (separate work; related)

https://linear.app/eq-core/issue/EQ-11/investigate-prisma-schema-drift-in-eq-frontend-design-cutting-edge

Unchanged this session. Items related to EQ-11's family:
- Comments-generator multiSchema fix RESOLVED via ignorePattern (Path B, prior session)
- Prisma 5.22 → 7.x upgrade in eq-frontend STILL OPEN
- live-db CI workflow `DIRECT_DATABASE_URL` gap STILL OPEN

---

## Pre-existing environment gaps (NOT introduced this session)

- Local `.venv` is missing `cryptography>=44.0.0` (pinned in `requirements.txt`). Vault tests (`tests/unit/vault/`) skip-fail in local pytest until `pip install -r requirements.txt` runs. Production Railway has the dep. PR-X2's adapter uses `TYPE_CHECKING` guard on the vault import so adapter tests don't require the dep either.
- 16 pre-existing `test_queue_lifecycle.py` failures on main — `_SessionStub` related, unrelated to Granola work.
- 1 deselected pre-existing test (`test_upsert_summary_uses_unique_interaction_id_index`) — old single-column ON CONFLICT migrated to composite during M2/M5.2.

---

## Build session entry prompt

Paste the contents of `docs/superpowers/specs/2026-05-24-phase-2e-session-prompt.md` as the opening message of the next session.

That prompt contains:
- Mandatory reads list (18 numbered items including the DBOS architecture doc + the merged PR-X1 + PR-X2)
- Phase 2e implementation scope + expected file structure + pseudocode
- User posture rules + critical disciplines (with DBOS-specific guardrails)
- Stop conditions
- Full Codex trajectory across both PRs this session
- AWS + Railway + Vercel + Neon state
- Pre-existing env gaps + pre-existing test failures
