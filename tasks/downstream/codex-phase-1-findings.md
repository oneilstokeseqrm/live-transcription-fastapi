# Phase 1 Codex Review — Findings + Fix Specs

**Date:** 2026-05-13
**Reviewer:** OpenAI Codex CLI via `/codex review --base main` (model_reasoning_effort=high)
**Diff reviewed:** `feat/contact-quality-phase-1` vs `main` (34 code/test files, ~2,057 lines)
**Outcome:** **GATE: FAIL — 3 P1 findings.** PR #10 must not merge until all P1s close.

This document is the canonical fix plan for the Codex findings. The next session executes these as Tasks 1.26.1 through 1.26.6 (per the original implementation plan's T1.26 Step 3: "If Codex returns CRITICAL findings, treat them as Task 1.26.X and resolve before proceeding to Phase 1.5").

The fixes are small (each 1-10 lines) but they invalidate the prior session's self-review claim of "0 CRITICAL / 0 IMPORTANT." Codex caught two classes of issue the self-review missed:

1. **Caller-side wiring gaps** — new parameters added to internal functions but ingress callers don't pass them, so the new code paths are unreachable in production (silent feature failure).
2. **Body/header trust boundary violations** — body-supplied `account_id` overrides the authenticated `X-Account-ID`, defeating the "backend rejection over frontend trust" invariant the entire Phase 1 effort was designed to establish.

---

## Background — why the self-review missed these

The Phase 1 self-review (also in this directory at `codex-phase-1-review.md`) validated the verifiable invariants from design Section 12:

- ✅ Required `account_id` declarations across 6 models
- ✅ Backend rejection in auth-context layer
- ✅ Three-state branching logic implemented
- ✅ No `account_id=None` writes in production paths
- ✅ Queue-helper SQL race-safe, tenant-isolated

What the self-review did NOT check:

- ❌ Whether the four ingress routes actually pass `recording_user_id` to `enrich()` (the queue feature is **unreachable** without it)
- ❌ Whether `body.account_id` and `context.account_id` agree, or which wins on mismatch (a mismatched request can persist under the **wrong account** despite passing the new auth check)
- ❌ Whether non-ingestion routes (e.g., `GET /upload/status`) broke when `get_auth_context()` started requiring `X-Account-ID` unconditionally
- ❌ Whether request-body fields like `participants` are wired all the way through to the worker

These are integration-completeness checks. The static-invariant approach validated the contract; Codex's independent review caught that the contract isn't honored end-to-end.

**Carry-forward lesson for Phase 1.5 and beyond:** when adding a new parameter to an internal function, the immediate next step is to update every caller. A "Phase X.5 follow-up to wire callers" is a silent-failure bomb — the new code path looks intact in unit tests but never runs in production.

---

## Task 1.26.1 — Wire `recording_user_id` + `tenant_internal_domains` through all four ingress routes

**Codex finding (verbatim):**

> [P1] Thread `recording_user_id` into unknown-domain enrichment — `services/transcript_enrichment.py:223-230`. When an attendee has a business domain that does not resolve to an account, this branch now drops the attendee unless `recording_user_id` is present. None of the updated ingress paths (`/text/clean`, `/batch/process`, `/upload`, `/listen`) pass that argument, so real requests will never create the new `pending_account_mappings` signal for partner/consulting domains—they just disappear with no contact and no review queue entry.

**Severity:** P1. The Phase 1 queue feature is unreachable in production traffic. Unknown-business-domain attendees are silently dropped.

**Root cause:** Task 1.21 added two new parameters to `TranscriptEnrichmentService.enrich()`:
- `recording_user_id: Optional[str] = None`
- `tenant_internal_domains: Optional[set[str]] = None`

Defaulted to None/set() so existing callers don't break. The implementer's report explicitly flagged this as deferred wiring. The orchestrator (me, in the prior session) accepted that deferral as a Phase 1.5 NIT. **That was wrong** — without wiring, the new branch is dead code.

**Files to change:**

1. `main.py` — WebSocket `/listen` route, the `_lane2` block that calls `transcript_enrichment_service.enrich(...)` or wherever the WS path invokes enrichment. Add:
   ```python
   recording_user_id=ws_pg_user_id or ws_user_id,
   tenant_internal_domains=ws_tenant_internal_domains,
   ```
   Capture both into `ws_*` session locals at WebSocket-open time, alongside `ws_account_id`.

2. `routers/text.py` — `/text/clean` handler. Add to the `enrich()` call:
   ```python
   recording_user_id=context.pg_user_id or context.user_id,
   tenant_internal_domains=await get_tenant_internal_domains(context.tenant_id),
   ```

3. `routers/batch.py` — `/batch/process` handler. Same pattern as text.py.

4. `routers/upload.py` — `/upload/complete` worker. Capture `recording_user_id` and `tenant_internal_domains` at `/upload/init` time (when the user is authenticated), persist on `UploadJob`, and pass from the worker:
   ```python
   recording_user_id=job.user_id,  # or job.pg_user_id if available on UploadJob
   tenant_internal_domains=await get_tenant_internal_domains(job.tenant_id),
   ```

5. Create `services/internal_domains.py` (new) — the `get_tenant_internal_domains(tenant_id) -> set[str]` helper. Mirror the pattern from eq-email-pipeline (in that repo's PR #6):
   - Query `provider_connections WHERE tenant_id = :tenant_id`
   - Extract email-host from each connection's identifier
   - Filter out public personal-domains (use `services.domain_classification.PERSONAL_DOMAINS`)
   - Return the resulting set (lowercased)
   - On error or no connections, return `set()` (graceful — the BUSINESS branch still runs)

6. `services/transcript_enrichment.py:223-230` — change the "skip when None" fallback to raise:
   ```python
   if recording_user_id is None:
       raise ValueError(
           "recording_user_id is required for unknown-domain queue insertion. "
           "Caller must pass the authenticated user from the request context."
       )
   ```
   This honors the Phase 1 principle "silent drops are worse than loud errors." Defense in depth, matching the `_resolve_contact` pattern already in the file.

**TDD steps:**

1. Write a failing integration test: a transcript with attendees `[alice@known.com, partner@unknown.com, intern@gmail.com]` and a real authenticated context produces a signal row in `pending_account_mapping_signals` for `partner@unknown.com`. Today this test FAILS because the signal isn't inserted (recording_user_id is None).
2. Implement the wiring per the file list above.
3. Re-run the integration test — it should PASS.
4. Re-run `scripts/verify_phase_1_invariants.sh` — should still pass.
5. Commit: `fix(enrichment): wire recording_user_id + tenant_internal_domains through all ingress routes`

**Acceptance evidence the next session must report back:**

- Diff stats showing changes in `main.py`, `routers/text.py`, `routers/batch.py`, `routers/upload.py`, `services/transcript_enrichment.py`, and new `services/internal_domains.py`
- Test output showing the integration test now passes
- Confirmation that `services/transcript_enrichment.py:~225` raises (not skips) when recording_user_id is None
- Output of `git diff HEAD~1 --stat`

---

## Task 1.26.2 — `/text/clean` must use `context.account_id`, not `body.account_id`

**Codex finding (verbatim):**

> [P1] Use the authenticated account anchor in `/text/clean` — `routers/text.py:130`. This route authenticates `X-Account-ID` into `context.account_id`, but the envelope/intelligence writes still use `body.account_id`. If a caller sends different values in the header and JSON body, the request succeeds and the interaction is persisted under the body account instead of the authenticated one, which defeats the new account-anchor enforcement and can mis-link notes to the wrong account.

**Severity:** P1. A mismatched header/body request persists under the wrong account, defeating backend rejection.

**Root cause:** Task 1.16 in the original plan said: "For Phase 1, prefer the request-body value as the authoritative source for the envelope and the intelligence lane; ignore any mismatch with the header." That guidance was wrong. The auth boundary must win.

**Resolution chosen:** Option (b) — **verify match, reject mismatch with 400.** Reasons:
- Honors "backend rejection over frontend trust" (the design's stated principle)
- Doesn't require dropping the body field (which would be a larger schema change)
- Surfaces inconsistent client behavior loudly instead of silently picking one source
- Mirrors the approach the design doc Section 3.2 already implies for ingestion paths

**Files to change:**

1. `routers/text.py` — at the `/text/clean` handler entry, after body parsing and context construction:
   ```python
   if body.account_id != context.account_id:
       raise HTTPException(
           status_code=400,
           detail=(
               "account_id mismatch: body.account_id and X-Account-ID header must agree. "
               "The authenticated account_id is the source of truth."
           ),
       )
   ```
   Then change every `account_id=body.account_id` in this handler back to `account_id=context.account_id` (envelope construction, process_transcript call, enrich call).

2. Update `tests/integration/test_account_anchor_rejection.py` to add:
   - A test asserting mismatched body/header returns 400 with the new message
   - A test confirming the matching body/header case still succeeds

**TDD steps:**

1. Write failing test: `POST /text/clean` with `{"text": "x", "account_id": "A"}` and header `X-Account-ID: B` returns 400.
2. Implement the mismatch check.
3. Test passes.
4. Confirm no regression in the existing `test_text_clean_does_not_400_when_account_id_present` test (the matching case).
5. Commit: `fix(text-clean): reject account_id mismatch between body and X-Account-ID header`

---

## Task 1.26.3 — `/upload/init` must persist `UploadJob.account_id` from context, not body

**Codex finding (verbatim):**

> [P1] Stop persisting upload jobs with a body-supplied account_id — `routers/upload.py:162-168`. `upload_init()` now requires an authenticated `X-Account-ID`, but the job record is still seeded from `body.account_id`. A mismatched header/body pair means the background worker later publishes and persists the upload under the body account, not the authenticated one, so uploads can still be anchored to the wrong account despite the new auth check.

**Severity:** P1. Same as 1.26.2 but for the upload path.

**Resolution chosen:** Same as 1.26.2 — verify match, reject mismatch with 400.

**Files to change:**

1. `routers/upload.py` — at the `/upload/init` handler:
   ```python
   if body.account_id != context.account_id:
       raise HTTPException(
           status_code=400,
           detail="account_id mismatch: body and X-Account-ID header must agree.",
       )
   ```
   Then change `account_id=body.account_id` to `account_id=context.account_id` in the `UploadJob(...)` construction at line ~157.

2. Add a test mirroring 1.26.2's test in the upload integration test file.

**TDD steps:**

1. Write failing test: `POST /upload/init` with mismatched body/header returns 400.
2. Implement the check.
3. Test passes.
4. Commit: `fix(upload-init): reject account_id mismatch between body and X-Account-ID header`

---

## Task 1.26.4 — Don't require `X-Account-ID` for non-ingestion auth contexts

**Codex finding (verbatim):**

> [P2] Don't require `X-Account-ID` for upload status polling — `utils/context_utils.py:135-141`. This makes `X-Account-ID` mandatory for every `get_auth_context()` caller, including `GET /upload/status/{job_id}`. That endpoint only checks tenant ownership of an existing job, so clients that previously polled with just the JWT will now get a 400 before the lookup. If account anchoring is only meant for ingestion, the requirement needs to be enforced at the mutating routes instead of in the shared auth helper.

**Severity:** P2. Polling endpoints are over-constrained; not a data-integrity issue but breaks legitimate clients.

**Files to change:**

1. `utils/context_utils.py` — add an optional parameter:
   ```python
   def get_validated_context(request: Request, require_account_id: bool = True) -> RequestContext: ...
   def _extract_context_from_jwt(request: Request, claims: ..., require_account_id: bool = True) -> RequestContext: ...
   def get_auth_context(request: Request, require_account_id: bool = True) -> RequestContext: ...
   ```
   Inside, only raise the 400 when `require_account_id is True` AND the header is missing. When `require_account_id is False`, set `context.account_id` to a sentinel (empty string `""` or a typed placeholder) and document that callers using `require_account_id=False` MUST NOT use `context.account_id` for any write.

   Alternative cleaner design: split into two helpers:
   - `get_auth_context_ingestion(request)` — requires account_id (this is what 4 ingestion routes use)
   - `get_auth_context_polling(request)` — doesn't require account_id (status/health routes)

   Pick whichever feels cleaner after reading the surrounding code. Document the chosen pattern in `docs/contacts-architecture.md`.

2. Update `GET /upload/status/{job_id}` and any other read-only routes to use the non-required variant.

**TDD steps:**

1. Write failing test: `GET /upload/status/{job_id}` with valid JWT but no `X-Account-ID` returns 200 (not 400).
2. Implement the variant.
3. Test passes.
4. Commit: `fix(auth-context): make X-Account-ID requirement optional for non-ingestion routes`

**Important caveat:** if you choose the `require_account_id` parameter approach, ensure that `RequestContext.account_id` typing handles the "no account_id required" case cleanly. Either narrow the type at call sites, or make `account_id` `Optional[str]` again and let mypy/pyright catch unsafe reads. The cleanest path is probably the two-helper split.

---

## Task 1.26.5 — Persist `/upload/init` participants through to the worker

**Codex finding (verbatim):**

> [P2] Preserve `/upload/init` participants through the async worker — `routers/upload.py:162-168`. `UploadInitRequest` now advertises `participants`, but the value is dropped as soon as the job record is created. Because `_process_upload_job()` reconstructs its state only from `UploadJob`, any caller-provided participants are lost before `/upload/complete` runs, so the worker can never turn them into contact_ids/front-matter for uploads without a calendar match.

**Severity:** P2. New API field is silently dropped before reaching the worker.

**Files to change:**

1. **eq-frontend prisma schema** — add `participants_json: String? @db.Text` to the `UploadJob` Prisma model. This is a cross-repo migration, similar in scope to the Phase 1 schema PR #349. Dispatch a brief in `tasks/downstream/eq-frontend-codex-fix-upload-participants.md` and run a Prisma migration to add the column. Light addition; should not require a test-data wipe.

2. `models/job_models.py` — add `participants_json: Optional[str] = Field(default=None, sa_column=Column(Text, name="participants_json"))`.

3. `routers/upload.py` `upload_init()`:
   ```python
   import json
   ...
   participants_json = (
       json.dumps([p.model_dump() for p in body.participants])
       if body.participants else None
   )
   job = UploadJob(
       ...
       participants_json=participants_json,
   )
   ```

4. `routers/upload.py` `_process_upload_job()` (the worker):
   ```python
   participants = (
       [ParticipantSpec.model_validate(p) for p in json.loads(job.participants_json)]
       if job.participants_json else None
   )
   # pass `participants=participants` into enrich() if the API supports it
   ```

5. Update `services/transcript_enrichment.py` `enrich()` to accept `participants: Optional[list[ParticipantSpec]] = None` and to seed `existing_contact_ids` from them when calendar-match is absent.

**TDD steps:**

1. Write failing test: `POST /upload/init` with participants → `UploadJob.participants_json` is set → worker deserializes and passes to enrich().
2. Wire the layers.
3. Test passes.
4. Commit (after eq-frontend migration lands): `fix(upload): persist participants through async worker`

**Cross-repo dependency:** This fix requires the Prisma schema change to land first. Same pattern as the original T1.2 dispatch.

---

## Task 1.26.6 — Use `TextCleanRequest.participants` in the `/text/clean` handler

**Codex finding (verbatim):**

> [P2] Actually use `TextCleanRequest.participants` during enrichment — `routers/text.py:72-78`. `TextCleanRequest` now accepts manual participants, but this handler still ignores `body.participants` and only runs calendar-based enrichment. For note/manual workflows without a calendar event, the new field is silently discarded, so callers will not get the expected participant contact_ids or front-matter even though the API now accepts that data.

**Severity:** P2. Same "field accepted, silently dropped" pattern as 1.26.5.

**Files to change:**

1. `routers/text.py` — at the `/text/clean` handler, pass `body.participants` to `enrich()`:
   ```python
   enrichment_result = await transcript_enrichment_service.enrich(
       ...
       participants=body.participants,  # new
       recording_user_id=context.pg_user_id or context.user_id,  # from 1.26.1
       tenant_internal_domains=...,  # from 1.26.1
   )
   ```

2. `services/transcript_enrichment.py` — same `participants` parameter as in 1.26.5. When `participants` is provided AND no calendar match was found, treat them as the attendee list for the three-state branching loop. When BOTH a calendar match AND participants are provided, the behavior should be documented (merge? caller-wins?) — recommend caller-wins (`participants` overrides calendar) for explicit manual-notes use cases.

**TDD steps:**

1. Write failing test: `POST /text/clean` with participants but no calendar event produces contact_ids for the resolved participants.
2. Wire `participants` through enrich.
3. Test passes.
4. Commit: `fix(text-clean): honor request-body participants for manual workflows`

---

## After all six fixes land

1. **Re-run `scripts/verify_phase_1_invariants.sh`** — confirm 12 static invariants still pass.
2. **Run the full test suite** — `pytest tests/ -v` — confirm 122 unit + 30+ integration tests still pass, with new tests passing.
3. **Re-run `/codex review`** on the updated diff — confirm GATE: PASS (0 P1, ideally 0 P2 unless deferred).
4. **Update `tasks/downstream/codex-phase-1-review.md`** — append a "Round 2 results" section noting which findings closed and which (if any) were deliberately deferred.
5. **Comment on PR #10** — note the Codex Round 2 result and that Phase 1 is now ready for merge.

## Optional deferrals

If context budget gets tight in the fix session, the THREE P2s (1.26.4 / 1.26.5 / 1.26.6) can defer to Phase 1.5 — but the three P1s (1.26.1 / 1.26.2 / 1.26.3) MUST close before PR #10 merges. Document any deferrals explicitly in the round-2 review notes.

## Merge order

Recommended once all P1s are fixed and codex Round 2 passes:

1. Merge eq-email-pipeline PR #6 first (independent of these fixes; that repo wired its callers correctly per the cross-repo agent's report).
2. Merge live-transcription-fastapi PR #10 second (after the codex Round 2 round-trip).
3. Confirm Railway auto-deploy via `/canary` or the Railway dashboard.
4. Run `/document-release` to sync README/ARCHITECTURE/CLAUDE.md to the shipped code.

## What this validates about the Phase 1 process

The original implementation plan's T1.26 step ("Codex consult on Phase 1 diff") was the recurring quality gate that the design doc Section 8.4 mandated. The prior session's self-review (in lieu of running actual Codex) missed three P1s. **Running real `/codex review` is non-substitutable.** Carry this forward into Phase 1.5's T1.5.23.
