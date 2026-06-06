# Granola Phase 3 (Frontend + Backend) Implementation Plan

> ## 🟢 BUILD STATUS (updated 2026-06-06) — EQ-92/B3 BACKEND SHIPPED + **PROD E2E PASSED**; EQ-94 (FRONTEND) NEXT
> **Backend B1+B2+B3 are all COMPLETE, MERGED, DEPLOYED, `/health` 200, `main @ 2534598` (B3 = `061ef37`).** The B3
> background history-import shipped as **PR #39 (squash `061ef37`)**; active Railway deploy **`105cd404`** (the
> `2534598` docs-commit redeploy; `9cda4b1e` REMOVED/superseded — same B3 code); B3 routes live + auth-gated.
> **Codex pre-merge gate ran 4 ROUNDS → CLEAN** (7 P1s folded) + a pre-Codex multi-agent review; 603 unit tests /
> 0 new failures, 0 Pyright errors on changed source, 0 envelope drift.
> **✅ PROD IMPORT E2E PASSED (2026-06-06)** against the live deploy + Neon `super-glitter` `production` (test cred
> `6a727bae`, folder "Test EQ" = 2 notes): **history** `/connect`=0.26s async ACK → `import_run` queued→running→
> complete (`total=2`) on the dedicated `GRANOLA_IMPORT_QUEUE`, success-note short-circuit (no re-ingest, the §6 #16
> landmine avoided), no DLQ, derived `done/skipped=0` = the documented re-run undercount (#21b); **forward**
> `/connect`=`import:null` + watermark anchored (C4) + no import_run + `/status` omits the import block (C18). A1
> resume-side observed; defer-side proven by effect + Codex code. **EQ-92 = DONE in Linear.**
> **NEXT = EQ-94 (the greenfield frontend, F1-F4)** in eq-frontend (SHARED branch-hopping checkout — use a worktree /
> `git branch --show-current` before every commit; a chat-modernization agent was active there during the E2E session).
> Backend contracts are LIVE + now prod-verified. Resume: checkpoint `granola-e2e-passed-eq94-next` +
> `docs/superpowers/specs/2026-06-06-granola-eq94-frontend-next-session-prompt.md`. Residual P2 ticketed (EQ-135,
> per-activation import-lifecycle scoping). **EQ-91 + EQ-92 backend = shipped + E2E-verified.** Test cred `6a727bae`
> left connected in FORWARD scope (founder standing decision to keep connected; LOCKED-11 cleanup still deferred).
> **Backend EQ-91 is COMPLETE, MERGED, DEPLOYED, `/health` 200, `main @ 922660b` (now `061ef37` after B3). EQ-91 = Done in Linear.**
> - **Phase B1 ✅ SHIPPED** — PR #37, squash `de3b1f3`: folder-LIST model + array `/connect`&`/status` with
>   legacy back-compat on BOTH request and response (singular `folder_id`/`folder_name` coalesced into
>   `folders[0]`; `/status` returns a legacy `folder` mirror alongside `folders[]`); `ConnectRequest` gained
>   `mode`+`folders[]`+`import_scope`; `/connect` REJECTS `mode="all"` (400) until B3; adapter reads
>   `folders[0]` w/ legacy fallback.
> - **Phase B2 ✅ SHIPPED** — PR #38, squash `922660b`: multi-folder poll loop + in-cycle seen-set dedup;
>   `api_client.list_notes` omits `folder_id` when falsy; per-folder `not_found` → `config.folders[].status`
>   + skip (cycle continues), all-folders-gone → credential error; membership-aware `granola_folder_name`
>   (C16); active-row `/connect` RECONFIGURE via NEW vault `update_credential_config` (different key → 409
>   "use /rotate"); success-update guards `status='active'`; **shared `last_polled_at` watermark HELD on any
>   folder skip**.
> - **EQ-92 / B3 ✅ BACKEND SHIPPED + DEPLOYED (updated 2026-06-06):**
>   - **PR 1 ✅ SHIPPED** — eq-frontend `granola_import_runs` migration: PR #454, squash `54b9dbc8`, merged +
>     Vercel-deployed; table LIVE in Neon `super-glitter-11265514` (`eq-dev`) `production` branch (partial-unique
>     + CHECK + 3 FKs verified). Codex gate PASS.
>   - **PR 2 ✅ SHIPPED** — backend: PR #39, squash `061ef37`, merged + Railway-deployed (`9cda4b1e` SUCCESS,
>     `/health` 200). All A1-A7 + C4/C8/C18: `import_runs.py` lifecycle/DERIVED progress; `GRANOLA_IMPORT_QUEUE`
>     + `granola_import_one_credential` + `run_import_step`; A1 poll-defers-uninitialized + proceeds-on-terminal;
>     vault `anchor_credential_watermark` (LEAST earliest-wins); adapter `cycle_aborted` + `set_import_total`;
>     `/connect` async restructure (mode="all" LIFTED; branch on import_scope; reconnect cancels prior-lifecycle
>     runs); `/status` import block (scoped to current history); full headless recovery (cron + /status). **Codex
>     gate 4 ROUNDS → CLEAN** (7 P1s folded) + pre-Codex multi-agent review. 603 tests/0 new fail; 0 Pyright; 0 drift.
>   - **Split out of B3 per the consult (fast-follows):** newly-added-folder reconfigure backfill (backlog #21a)
>     + exact re-import progress items-table (#21b). Sibling tickets: EQ-105 (sync boto3 off the loop), EQ-109
>     (per-loop asyncpg pool); **NEW residual-P2** per-activation import-lifecycle scoping (Codex r3).
>   - **✅ Prod import E2E PASSED (2026-06-06)** (history + forward; see the top BUILD STATUS banner). **EQ-92 = DONE.**
>     **NEXT = EQ-94 (frontend).** Resume checkpoint: `granola-e2e-passed-eq94-next`; next-session prompt:
>     `docs/superpowers/specs/2026-06-06-granola-eq94-frontend-next-session-prompt.md`. (Active Railway deploy
>     `105cd404` supersedes the ship-time `9cda4b1e` above — same B3 code.)
> - **Deferred into B3/later:** per-folder watermarks (B2 holds the shared watermark on any skip); live-API
>   multi-folder probes (multi-folder doc §6); newly-added-folder backfill on reconfigure (B3 Step 5b, C17).
> - The **B1/B2 phase sections below are the BUILD RECORD** (shipped — their step checkboxes are left
>   as-built). The **B3/B4/frontend (F1–F4) sections + §1a/§2/§5 are the forward plan** and remain authoritative.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.
> This is a MULTI-SESSION plan across TWO repos. Each Phase below is a single PR with its own Codex
> pre-merge gate (4-round soft cap). **Build BACKEND first** — the frontend depends on the backend's
> array-shaped contracts + the import-progress signal.

**Goal:** Ship a self-serve, design-partner-gated Granola connection — a user pastes their Granola key,
picks the folders to watch, chooses whether to import past meetings or only go forward, and EQ imports
the history in the background (with live progress) and keeps polling every 5 minutes — surfaced in the
existing onboarding "meeting-connect" step and in Settings → Connections. (The backend already routes
unknown-account meetings to the per-user account-creation approval queue; the frontend approvals UI is a
SEPARATE future project, not in this scope.)

**Architecture:** The Granola backend engine is COMPLETE + LIVE (main @ `061ef37` after EQ-91/B1+B2 +
EQ-92/B3; `/health` 200; pre-Phase-3 baseline was `e0aafbe`+`fafaee2`). B1+B2 delivered the **folder-LIST
model + array-shaped `/connect`/`/status` + the multi-folder poll loop**; B3 (now SHIPPED) delivered the
**background history-import with an "importing N of M" progress signal** (retiring the synchronous first
poll). Phase 3's REMAINING work is the **prod E2E** (then) the **greenfield frontend** over the **existing, production-proven
gateway-JWT rail** (`callBackend` + `mintInternalJwt(pg_user_id)` → `BACKEND_SERVICE_TRANSCRIPTION_URL`).
No OAuth. No downstream envelope changes. No Prisma migration for multi-folder (`config` is opaque JSONB).

**Tech Stack:** Backend — Python 3.11, FastAPI, DBOS, asyncpg + SQLAlchemy async, AWS KMS vault, pytest
+ AsyncMock. Frontend — Next.js 16 (App Router), tRPC, React, shadcn/ui (new-york) + `eq-tokens.css` +
`GlassPanel` + Framer Motion, Vitest/RTL + Playwright. Prisma schema owned by eq-frontend.

---

## 0. Locked decisions & scope (DO NOT re-litigate)

**The 6 Phase-3 decisions (2026-06-04):**
1. **Placement:** design-partner-GATED Granola card in the existing onboarding `meeting-connect` step +
   Settings → Connections. Per-org flag/allowlist gates visibility. **Supersedes LOCKED-30** (the
   standalone `/dashboard/settings/integrations/granola` page).
2. **Connect rail = existing gateway-JWT proxy** (`lib/gateway-forward` `callBackend` + `mintInternalJwt`
   carrying `pg_user_id` → `BACKEND_SERVICE_TRANSCRIPTION_URL`), NOT OAuth. Granola = API-key paste +
   folder pick. **Verified this session:** the rail is already used by `/text/clean`, `/upload`,
   `/batch` in prod; backend `middleware/jwt_auth.py` verifies the SAME `INTERNAL_JWT_SECRET` /
   `INTERNAL_JWT_ISSUER` (eq-frontend) / `INTERNAL_JWT_AUDIENCE` (eq-backend). Granola endpoints are on
   the same service → no audience override.
3. **Account-creation approval queue = per-user** (first-owner-wins; `owner_user_id` NOT NULL on every
   row; matches the whole system; zero backend change — already live). Tenant-admin-wide is a deferred
   scale feature (§2.1 #3). **The frontend approvals inbox is OUT of this project's scope** (separate
   future build, founder decision 2026-06-04) — this decision only describes the existing backend queue
   that Granola already routes into.
4. **On approval, re-process deferred notes on the next 5-min poll** (not instant). Instant is a
   documented fast-follow (§2.1 #9).
5. **Full multi-folder in v1.** Data model stores a **LIST** of folders; `/connect` + `/status` are
   **array-shaped**; backend loops over folders. (See `tasks/granola-multi-folder-investigation.md`.)
6. **Background history-import replaces the synchronous first poll** (amends **LOCKED-31**) with a
   UI-visible "importing N of M" progress signal. **In v1 scope.**

**Carried-forward LOCKED (Phase 2, unchanged):** LOCKED-23..29, 32..44 hold. Most load-bearing here:
LOCKED-38 (NEVER modify downstream envelope contracts), LOCKED-35/36 (`source="generic"`,
`interaction_type="meeting"`, 6 `granola_*` extras), LOCKED-41 (`text_clean_service.process()` direct
call), LOCKED-39 (external cron + DBOS `SetWorkflowID`, advisory lock), LOCKED-40 (KMS 4-field
EncryptionContext), LOCKED-42 (single Postgres role for MVP). **LOCKED-31 is AMENDED by decision #6**
(synchronous first poll → background import).

**v1 IN scope:** backend folder-LIST model + multi-folder poll loop + empty-`folder_id` "ingest
everything" fix + array `/connect`/`/status` + per-folder error state + **import-scope choice (import
full history vs only-going-forward)** + background history-import (history scope only) + import-progress
signal; frontend gated card (onboarding + settings) + key-paste → **user-selected** multi-folder picker
wizard + **import-scope choice in the wizard** + status panel (folders + import progress + disconnect) +
tRPC/proxy procedures.

**Backend account-routing is ALREADY DONE (not in this project's build, but load-bearing context):** when a
Granola meeting is ingested, the live backend already handles both cases — known account → link
(`Scenario A`); unknown business account → route to the **per-user account-creation approval queue**
(`pending_account_mappings`, `owner_user_id` NOT NULL; `Scenario C`); no outside business attendee → skip
(`Scenario D`). This is the SAME shared, source-tagged queue the email pipeline uses (`source_type` /
`discovered_from_type` columns distinguish origins). This project adds nothing here — it's already live.

**Two orthogonal connect axes (keep distinct):** (1) **folders** — WHICH folders, user multi-selects in
the UI; (2) **import_scope** — HOW FAR BACK, `"history"` (full backfill, default) vs `"forward"` (watermark
set to connect-time; NO backfill, the 5-min poll just picks up new meetings). `"forward"` is the safe
path: it sidesteps the backfill entirely (no Railway-timeout pressure, no LLM cost on old meetings, never
needs the parallel-intake fast-follow).

**v1 OUT (fast-follow, ticketed):** parallel/bounded-concurrency intake (EQ-93, see
`tasks/granola-parallel-intake-investigation.md`); instant reprocess on approval (§2.1 #9); tenant-wide
approvals (§2.1 #3); per-folder watermarks; `PATCH /folder` as a separate endpoint (folded into
array-`/connect` reconnect path — see §2.1 #13).

**EXPLICITLY OUT OF SCOPE — the frontend account-creation approval queue UI (a SEPARATE future project,
founder decision 2026-06-04):** building a user-facing approvals inbox for Granola/transcript meetings is
NOT part of this project. The BACKEND already routes unknown-account meetings to the per-user queue
(above), so meetings are safely captured; the dedicated meeting-aware approvals UI is its own future
build. (Note for that future build: an admin-only email-framed "Pending Domain Queue" already exists at
`/dashboard/organization/email-pipeline` and reads the same shared table via direct Prisma; a proper
Granola/meeting approvals UI should (a) be meeting-aware using `source_type`, (b) be per-user accessible,
and (c) drive approvals through the backend `/queue/{id}/approve` endpoint — which runs the full DBOS
account-provisioning workflow — NOT the email UI's direct-Prisma shortcut, which would mark a row done
without provisioning the account. **RESOLVED (founder 2026-06-04): the queue STAYS one shared,
source-tagged, per-user table that email + Granola/transcripts both write into — do NOT physically
separate.**)

**Ground truth reference:** `tasks/granola-existing-system-map.md` (both repos, file pointers).

---

## 1. Open decisions for the reviews (4C) — recommendations embedded

These are genuine architecture/product choices. My recommendation is embedded; `/plan-eng-review`,
`/plan-design-review`, and `/codex consult` confirm or adjust.

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | **Pending Approvals read path** — _DEFERRED with the inbox (out of scope; see §0)_ | n/a this project | The frontend approvals inbox is a separate future project. For that future build: read via Prisma scoped `tenant_id` + `owner_user_id == pg_user_id`, but drive ACTIONS through the backend `/queue/{id}/approve` (full provisioning workflow), NOT the email UI's direct-Prisma shortcut. |
| D2 | **Import-progress transport** | (a) FE polls `GET /status` (extended with import-progress fields) every ~3–5s while importing; (b) SSE; (c) WebSocket | **(a) polling `/status`**. Matches the existing connect-status polling pattern; survives the Railway proxy; no new infra. Stop polling when `import.state != 'running'`. |
| D3 | **Design-partner gate mechanism** | (a) env/config tenant allowlist; (b) a per-org boolean flag column (Prisma) | **(a) tenant allowlist** for v1 (a handful of partners; zero migration; gate in the tRPC layer + the card's server component). Evolve to a per-org flag (b) when partners > ~10. Locked decision #1 explicitly allows "a tenant allowlist suffices." |
| D4 | **"Ingest everything" mode in the model** | (a) `config.folders = []` empty-list means all; (b) explicit `{mode: 'all'\|'folders', folder_ids}` discriminator | **(b) explicit discriminator** stored in `config`, so "watch everything" is unambiguous and the empty-`folder_id` bug can't recur. UI v1 may not expose "all", but the model captures it cleanly. |
| D5 | **Folder edit / add-remove path** | (a) ~~reuse `reactivate_credential`~~ (REJECTED — Codex C5: reactivate only handles ARCHIVED rows; an ACTIVE credential 409s); (b) **active-row reconfigure path** under the advisory lock | **(b) active-row reconfigure** — see **C5 + C17** + B3 Step 5b. Update `config.folders` in place; backfill ONLY the newly-added folders from now() (a scoped one-shot). **Do NOT move the global `last_polled_at`** — in forward mode that would skip existing-folder meetings (C17). Folds §2.1 #13 in. |
| D6 | **Import scope default (history vs forward)** | (a) default `"history"` (full backfill) with `"forward"` as a prominent toggle; (b) default `"forward"`; (c) no default, force a choice | **(a) default `"history"`** — EQ is a customer-intelligence CRM; the value is relationship history, so a design partner sees a populated CRM on day one. `"forward"` stays a clear, prominent option (and is the safe/cheap path). v1 is a binary choice; a "last 30/90 days" slider is a later refinement, not v1. Design review confirms the wizard UX. |

---

## 1a. Codex review corrections (BINDING — 2026-06-04; fold into the named phase before building)

Independent Codex review (gpt-5.5, read-only, high reasoning) — TWO rounds. Round 1 found 13 gaps in the
new B1/B2/B3 work (C1–C16). Round 2 verified the fixes and flagged that they had to be FOLDED INTO the
executable phase steps (not left as a separate overlay), plus 2 new P1s (C17–C18). **All C1–C18 are now
folded directly into the phase steps below** — this section is the changelog/index, the steps are
authoritative. (Round-2 verdict driving the inline fold: "binding corrections must be folded into the
executable phase steps.")

**P0 (must-fix, blocking):**
- **C1 — Idempotent import progress (B3).** `bump_import_progress(done += 1)` double-counts under DBOS
  step crash/replay. **Fix:** derive `done/deferred/skipped/errors` from a COUNT over the durable
  `external_integration_runs` rows for this credential since `import.started_at` (idempotent), NOT an
  incrementing counter. Progress = a derived read, not a mutable tally.
- **C2 — Import lock-busy behavior (B3).** If the 5-min poll holds the per-credential advisory lock when
  the import starts, the import must NOT silently skip or strand `queued/running`. **Fix:** import lock
  acquisition is blocking-with-timeout or requeues with backoff, leaving `state='queued'` until it wins
  the lock.
- **C3 — Separate import queue (B3).** A 33–83 min import must NOT occupy `GRANOLA_POLL_QUEUE`
  (`Queue("granola-poll", concurrency=5)`, scheduler.py:83) — it would starve other users' 5-min polls.
  **Fix:** add a dedicated `GRANOLA_IMPORT_QUEUE = Queue("granola-import", concurrency=<low>)`; keep the
  poll queue independent.

**P1 (fix before build):**
- **C4 — Forward-anchor boundary (B3 step 5).** `last_polled_at = NOW()` written AFTER connect work can
  skip a meeting created during the connect round-trip. **Fix:** capture `forward_anchor_at` at route
  ENTRY (before any awaits) and persist that exact timestamp in the credential transaction; test the
  boundary.
- **C5 — Active-row folder reconfigure (D5 / B3 / F4) — CORRECTION.** "Reuse `/connect` for folder edit"
  is FALSE against the live `routers/granola.py`: an ACTIVE credential returns **409** ("already
  connected"), and `reactivate_credential` only handles ARCHIVED rows. **Fix:** add an active-row
  reconfigure path (a `/connect` reconfigure branch or small endpoint) that, under the advisory lock,
  updates `config.folders` + re-applies `import_scope` (forward → re-anchor `last_polled_at=NOW()`;
  history → NULL + re-import new folders). D5's "reuse reactivate" is wrong as written.
- **C6 — Per-folder errors survive the success update (B2).** `_mark_credential_polled_success`
  (adapter.py:~1688) wipes `last_error`, so per-folder error state stored there is lost each cycle.
  **Fix:** store per-folder status in `config.folders[].status` (or a preserved `last_error.folder_errors`
  the success update doesn't clear).
- **C7 — `granola_import_runs` schema (B3).** Add `credential_id` (FK + index) and a partial unique
  `UNIQUE (credential_id) WHERE state IN ('queued','running')` so a credential can't have two active
  imports.
- **C8 — `/connect` enqueue atomicity (B3 step 5).** A crash after credential activation but before the
  import enqueue leaves a history-scope credential connected with no import. **Fix:** `/connect` (and/or
  `/status`) idempotently recovers "active history credential with no running/completed import" by
  creating + enqueuing one on retry.
- **C9 — Disconnect-during-import (B3).** `disconnect` doesn't take the advisory lock and `run_one_cycle`
  has no aborted flag, so an import could be marked `complete` after mid-import deactivation. **Fix:**
  propagate the adapter's existing `_CredentialDeactivated`/`_credential_is_active` abort into the import
  workflow → mark the import_run `cancelled`; (reuses edge-#12 machinery).
- **C10 — Deploy order: empty `folder_id` fix moves to B1 (was B2).** B1 accepts `mode:"all"` but the
  omit-empty `list_notes` fix is in B2 → a mid-deploy `mode:"all"` would send `folder_id=""` (400).
  **Fix:** ship the omit-empty fix in **B1**, OR reject `mode:"all"` until B2 is deployed. (v1 UI always
  sends explicit `folders`, so this only matters for the `all` capability — but fix it in B1 regardless.)
- **C11 — F1 `connect` must carry `import_scope` (F1/F3).** The F1 tRPC `connect` input omitted
  `import_scope` while F3 passes it. **Fix:** add `importScope: z.enum(["history","forward"])` to F1
  `connect` and map to the backend `import_scope`.
- **C12 — Enforce the design-partner gate IN every tRPC procedure (F1/F2) — SECURITY.** Gating only at
  card render (F2) leaves a window where any authenticated tenant can call the Granola tRPC procedures
  directly. **Fix:** call `isGranolaEnabled(ctx.tenantId)` inside EVERY Granola procedure in F1 (validate
  /connect/status/disconnect/rotate) → 403 when not allowlisted; the card gate (F2) is UX-only.

**P2 (fold in; low risk):**
- **C13 — B1 transitional contract.** B1 can't return a real `import` block (the import lands in B3).
  **Fix:** B1 keeps the existing `first_poll`-shaped response (or a transitional sync poll); the `import`
  block contract ships in B3 with the workflow. Don't expose a fake `import` contract in B1.
- **C14 — Indeterminate progress until `total` known (B3/F4).** `total` is `null` until the first listing
  completes; the progress UI must show an indeterminate state until `total` is known, then switch to
  "N of M".
- **C15 — Correct test target.** The repo's Granola router tests live in `tests/unit/test_granola_admin.py`
  (verified), NOT `tests/unit/test_granola_router.py`. Use the real file.
- **C16 — `granola_folder_name` for multi-folder (B2).** A note in multiple watched folders would keep
  using `folders[0].name` in the envelope extra. **Fix:** derive the folder name from the note's
  membership intersected with the selected folders, preserving the single-string downstream contract
  (LOCKED-36).

**Round-2 P1 (new; folded into the steps):**
- **C17 — Forward-mode add-folder must not skip existing-folder meetings (B3 Step 5b).** Re-anchoring the
  single `last_polled_at` to NOW() on a forward-mode folder edit would skip meetings created in
  ALREADY-watched folders since the last poll. **Fix:** on a forward-mode add-folder, backfill ONLY the
  new folders from now() (scoped one-shot) and leave the global watermark untouched (true per-folder
  watermarks are a later follow-up).
- **C18 — `cancelled` import state threaded through the contract + UI (B3/§2/F4).** C9 introduced a
  `cancelled` import state; it's now in the `/status` `import.state` enum (§2), the import_runs lifecycle
  (B3 Step 3/6), and the F4 polling-stop condition (stop on any state ≠ `queued`/`running`).

---

## 2. Cross-repo contract (the seam both streams build against)

These are the wire contracts. **Backend defines them (Phase B1); frontend consumes them (Phase F1).**
Lock these shapes before either side starts so the streams converge.

**`POST /integrations/granola/validate`** (UNCHANGED — already array-shaped): `{api_key}` →
`{ok: true, folders: [{id, name}]}` or `{ok: false, reason}`. Folders are returned flat (subfolders
included server-side by Granola).

**`POST /integrations/granola/connect`** (WIDENED to arrays):
```jsonc
// request
{ "api_key": "grn_…",
  "folders": [{ "id": "fol_…", "name": "Sales" }, { "id": "fol_…", "name": "EQ" }],
  "mode": "folders",         // "folders" | "all"  (D4; "all" ignores folders[]; v1 UI always sends "folders")
  "import_scope": "history"  // "history" (full backfill) | "forward" (D6; watermark=now(), NO backfill)
}
// response when import_scope="history" (background import — decision #6; NO synchronous first-poll result)
// state is "queued" at ACK: the granola_import_runs row is created queued; the dispatched workflow
// flips it to "running" (mark_running) and /status then reports the live lifecycle state. (Reconciled
// 2026-06-06 from an earlier "running" example to the accurate just-created value — B3 build + review.)
{ "ok": true, "status": "connected",
  "import": { "import_run_id": "uuid", "state": "queued", "total": null, "done": 0 }
}
// response when import_scope="forward" (no backfill; the 5-min poll picks up new meetings)
{ "ok": true, "status": "connected", "import": null }
```
Back-compat for one release: `/connect` ALSO accepts the legacy singular `{folder_id, folder_name}` and
writes BOTH `config.folders` AND legacy `config.folder_id/folder_name = folders[0]` (so a mid-deploy
old-client request still works, and the adapter's legacy fallback reads).

**`GET /integrations/granola/status`** (WIDENED — `folder` → `folders[]`, + `import` block):
```jsonc
{ "connected": true,
  "status": "active",                  // active | revoked | error
  "last_polled_at": "2026-06-04T…Z",
  "mode": "folders",                   // D4
  "import_scope": "history",           // D6 — "history" | "forward"; import is null for "forward"
  "folders": [                          // array (len ≥ 1; was singular `folder`)
    { "id": "fol_…", "name": "Sales", "status": "ok" },          // per-folder error state
    { "id": "fol_…", "name": "EQ",   "status": "not_found" }     // ok | not_found | error
  ],
  "activity": { "ingested_7d": 12, "deferred_7d": 3, "errors_7d": 0 },
  "import": {                           // present while/after a background import (null for "forward")
    "import_run_id": "uuid", "state": "running",   // queued | running | complete | failed | cancelled
    "total": 240, "done": 87, "deferred": 4, "skipped": 9, "errors": 0,  // total:null until listing done → FE shows indeterminate; counts are DERIVED
    "started_at": "…Z", "finished_at": null
  },
  "last_error": null
}
```

**`POST /integrations/granola/rotate`** — `{new_api_key}` → `{ok:true}` (UNCHANGED).
**`DELETE /integrations/granola`** — soft-delete → `{ok:true, status:"disconnected"}` (UNCHANGED).

**Queue (backend, ALREADY LIVE — not built in this project):** `POST /queue/{id}/approve` (202 +
workflow_id), `/map`, `/ignore`. Granola unknown-account meetings already route here per-user
(`owner_user_id`). The frontend approvals inbox that would read/act on this queue is a SEPARATE future
project (out of scope; see §0).

**Merge/deploy order (per repo, per phase):** backend B1 (contracts) → B2 → B3 must be DEPLOYED and
`/health` 200 before the frontend F-phases that consume them merge. Each phase = its own PR + Codex gate.

---

## STREAM 1 — BACKEND (`live-transcription-fastapi`)

> Branch convention: `phase-3/granola-be-<phase>`; `git branch --show-current` before every commit
> ([[feature-branch-safety-protocol]]). All work on feature branches; founder authorizes each merge.

### Phase B1 — Folder-LIST data model + array-shaped contracts (EQ-91) — ✅ SHIPPED (PR #37 `de3b1f3`, deployed)

> **✅ All B1 steps below SHIPPED** (boxes left as the build record). Notable as-built detail beyond the
> steps: `/status` returns a legacy `folder` mirror alongside `folders[]` (one-release back-compat) — B3
> must preserve it. `/connect` rejects `mode="all"` (400) until B3 lifts the guard.

**Files:**
- Modify: `routers/granola.py` (ConnectRequest → arrays; `/status` folder→folders; back-compat)
- Modify: `services/granola_ingestion/adapter.py:252,1187` (read `config.folders` w/ `folder_id` fallback — minimal here; full loop in B2)
- Modify: `services/vault/user_credentials.py` (config write/read helpers if any assume singular)
- Test: `tests/unit/test_granola_router.py`, `tests/unit/granola_ingestion/test_adapter.py`

**No Prisma migration** — `config` is opaque JSONB (`schema.prisma:4623`, `Json @db.JsonB`).

- [ ] **Step 1 — Write failing tests for the array `ConnectRequest` + back-compat.**
```python
# tests/unit/test_granola_router.py
def test_connect_request_accepts_folders_array():
    body = ConnectRequest(api_key="grn_x", folders=[{"id": "fol_a", "name": "A"}], mode="folders")
    assert [f.id for f in body.folders] == ["fol_a"]

def test_connect_request_back_compat_singular_folder_id():
    # legacy client sends folder_id/folder_name; normalize into folders[0]
    body = ConnectRequest(api_key="grn_x", folder_id="fol_a", folder_name="A")
    assert body.normalized_folders() == [{"id": "fol_a", "name": "A"}]

def test_connect_request_mode_all_ignores_folders():
    body = ConnectRequest(api_key="grn_x", mode="all", folders=[])
    assert body.mode == "all"
```
- [ ] **Step 2 — Run, verify FAIL** (`pytest tests/unit/test_granola_router.py -k connect_request -v`).
- [ ] **Step 3 — Implement the array request model + normalizer** in `routers/granola.py`:
```python
class FolderRef(BaseModel):
    id: str = Field(..., min_length=1)
    name: str | None = None

class ConnectRequest(BaseModel):
    api_key: str = Field(..., min_length=1)
    mode: Literal["folders", "all"] = "folders"
    folders: list[FolderRef] = Field(default_factory=list)
    import_scope: Literal["history", "forward"] = "history"   # D6
    # back-compat (one release): accept legacy singular and fold into folders[0]
    folder_id: str | None = None
    folder_name: str | None = None

    @model_validator(mode="after")
    def _coalesce_legacy(self):
        if not self.folders and self.folder_id:
            self.folders = [FolderRef(id=self.folder_id, name=self.folder_name)]
        if self.mode == "folders" and not self.folders:
            raise ValueError("folders[] required when mode='folders'")
        return self

    def config(self) -> dict:
        cfg: dict = {"mode": self.mode,
                     "import_scope": self.import_scope,   # D6 — persisted so folder-edits preserve it
                     "folders": [f.model_dump(exclude_none=True) for f in self.folders]}
        if self.folders:                       # legacy mirror for one release
            cfg["folder_id"] = self.folders[0].id
            cfg["folder_name"] = self.folders[0].name
        return cfg
```
- [ ] **Step 4 — Update `/connect`** to write `body.config()` (replaces the singular `{"folder_id": …}`
  at `granola.py:569-571`). **B1 KEEPS the existing `first_poll`-shaped response** (the synchronous
  one-folder "save & test"); the `import` block + the background import land in B3 — do NOT fake an
  `import` contract in B1 (C13). **Deploy-order (C10):** B1's `ConnectRequest` accepts `mode:"all"`,
  which would drive the adapter to call `list_notes(folder_id="")` (400). Ship the omit-empty `folder_id`
  fix (`api_client.py:277-280`, the B2 Step 3 change) IN B1, OR reject `mode:"all"` in `ConnectRequest`
  until B2 deploys. **Update `/status`** (`granola.py:766-778`) to return `folders: [...]` + `mode` from
  `status_row.config`, with each folder's `status` defaulting to `"ok"` (per-folder error fills in B2):
```python
cfg = status_row.config or {}
folders_cfg = cfg.get("folders") or (
    [{"id": cfg["folder_id"], "name": cfg.get("folder_name")}] if cfg.get("folder_id") else []
)
return { ..., "mode": cfg.get("mode", "folders"),
         "folders": [{"id": f["id"], "name": f.get("name"), "status": "ok"} for f in folders_cfg],
         ... }
```
- [ ] **Step 5 — Run all router + adapter unit tests, verify PASS.**
- [ ] **Step 6 — Commit** `feat(granola): folder-LIST data model + array-shaped /connect & /status (EQ-91)`.

**Exit criteria:** `/connect` accepts `folders[]` + `mode` (and legacy singular); `/status` returns
`folders[]` + `mode`; config persists `folders` + legacy mirror; 0 envelope-contract drift; unit tests pass.

---

### Phase B2 — Multi-folder poll loop + "ingest everything" fix + per-folder error state (EQ-91) — ✅ SHIPPED (PR #38 `922660b`, deployed)

> **✅ All B2 steps below SHIPPED** (boxes left as the build record). As-built notes for B3: the active-row
> folder reconfigure (Step 7 / C5) landed via NEW vault helper `update_credential_config` (same key →
> reconfigure in place; different key → 409 "use /rotate") — B3 Step 5b builds the watermark/import_scope
> handling ON TOP of it. B2 also already HOLDS the shared `last_polled_at` watermark on any partial folder
> skip (does not advance on a per-folder skip); B3 Step 5b only adds add-folder/import_scope handling.

**Files:**
- Modify: `services/granola_ingestion/adapter.py` (`run_one_cycle` loop over folders; in-cycle seen-set; per-folder error capture)
- Modify: `services/granola_ingestion/api_client.py:277-280` (OMIT `folder_id` param when falsy — the empty-string bug)
- Modify: `routers/granola.py` (`/status` per-folder `status` from `last_error`/config)
- Test: `tests/unit/granola_ingestion/test_adapter.py`, `tests/unit/test_granola_api_client.py`

- [ ] **Step 1 — Failing test: empty/`all` mode omits `folder_id`.**
```python
# tests/unit/test_granola_api_client.py
async def test_list_notes_omits_folder_id_when_falsy(httpx_mock):
    httpx_mock.add_response(json={"notes": [], "hasMore": False})
    await GranolaAPIClient(api_key="grn_x").list_notes(folder_id=None, created_after=None)
    req = httpx_mock.get_request()
    assert "folder_id" not in dict(parse_qsl(urlparse(str(req.url)).query))  # MUST omit, not send ""
```
- [ ] **Step 2 — Run, verify FAIL.**
- [ ] **Step 3 — Fix the client** (`api_client.py:277-280`): build params dict and only set `folder_id`
  when truthy; never send `folder_id=""` (which 400s as `VALIDATION_ERROR: Invalid folder ID format`).
- [ ] **Step 4 — Failing test: cycle loops folders + dedups across overlap.**
```python
async def test_run_one_cycle_loops_folders_and_dedups_overlap(...):
    # config.folders = [fol_a, fol_b]; fol_a & fol_b both return note_X
    # assert process_note called ONCE for note_X (external_integration_runs UNIQUE absorbs the overlap)
    # assert get_note_detail called once for note_X (in-cycle seen-set skips redundant detail-fetch)
```
- [ ] **Step 5 — Implement the loop** in `run_one_cycle`: snapshot `cycle_start_at` BEFORE listing;
  for `mode=='all'` call `list_notes(folder_id=None, created_after=last_polled_at)` once; else
  `for folder in config.folders: list_notes(folder_id=folder.id, created_after=last_polled_at)` and
  concatenate; maintain an in-cycle `seen: set[note_id]` to skip the redundant detail-fetch for a note
  in two folders (dedup is already cross-folder-safe via `external_integration_runs` UNIQUE +
  `process_note` short-circuit — the seen-set is a cost optimization, not a correctness requirement).
  Feed the deduped note list into the EXISTING per-note loop unchanged (still sequential in v1 —
  parallelization is EQ-93). On a per-folder `GranolaErrorCode.GRANOLA_FOLDER_NOT_FOUND`, record that
  folder's status into **`config.folders[].status`** (C6 — NOT `last_error`, which
  `_mark_credential_polled_success` wipes each cycle) and CONTINUE other folders (don't fail the cycle).
  Also derive each note's `granola_folder_name` envelope extra from the note's membership ∩ the selected
  folders (C16), keeping the single-string downstream contract (LOCKED-36) — don't hardcode `folders[0]`.
- [ ] **Step 6 — `/status` per-folder status** reads `config.folders[].status` → `"ok"` / `"not_found"`
  / `"error"` (persisted in config; survives the cycle-success update).
- [ ] **Step 7 — Folder edit = ACTIVE-ROW RECONFIGURE (C5; NOT `reactivate_credential`, which only
  handles ARCHIVED rows — an active credential 409s).** Update `config.folders` in place under the
  advisory lock; the watermark handling per `import_scope` lives in **B3 Step 5b** (`history` → backfill
  only the new folders; `forward` → backfill new folders from now() WITHOUT moving the global watermark,
  so existing-folder meetings aren't skipped). Add tests for both scopes.
- [ ] **Step 8 — Run all unit tests, verify PASS; run `scripts/verify_consumer_contracts.py` (0 drift).**
- [ ] **Step 9 — Commit** `feat(granola): multi-folder poll loop + empty-folder_id fix + per-folder error (EQ-91)`.

**Exit criteria:** cycle polls N folders (or all); overlap ingests once; deleted folder → that folder
`status='not_found'` (in `config`, survives a successful cycle) while others ingest; `folder_id=""` never
sent; folder edit goes through the active-row reconfigure (no 409) and does NOT skip existing-folder
meetings; `granola_folder_name` reflects the note's actual folder; 0 envelope drift.

**Build-time probes (run against the live API with the test key BEFORE relying — multi-folder doc §6):**
(1) does `folder_id=""` 400 vs all-notes (fix = omit); (2) does `/notes` accept repeated/comma
`folder_id` for one-call multi-folder (if yes, could replace the loop — do NOT assume); (3) does the
`notes[]` summary carry `folder_membership`; (4) `page_size` vs `limit` cap (client hardcodes `limit=100`,
docs cap `page_size=30` — confirm name/max/clamp); (5) realistic folder counts.

---

### Phase B3 — Background history-import + "importing N of M" progress signal (EQ-92, amends LOCKED-31, ~1–1.5 days)

**Goal:** Replace the synchronous "save & test" first poll (`_save_and_test_locked`, `granola.py:340-493`)
with a fast `/connect` ACK + a **background import** dispatched as a DBOS workflow, exposing a
UI-pollable progress signal. This is the load-bearing fix for the Railway ~5-min cap (a 200–500-meeting
partner's sequential first import is 33–83 min — far past 300s; see
`tasks/granola-parallel-intake-investigation.md` §2).

**Files:**
- Create: `services/granola_ingestion/import_runs.py` (import-run lifecycle + DERIVED progress reader)
- Modify: `routers/granola.py` (`/connect` → ACK + dispatch; active-row reconfigure; `/status` → `import` block)
- Modify: `services/granola_ingestion/scheduler.py` (new `granola_import_one_credential` workflow + new `GRANOLA_IMPORT_QUEUE`)
- Modify: `services/granola_ingestion/adapter.py` (`run_one_cycle(..., import_run_id=None)` — sets `total` + lifecycle; progress is DERIVED, not counted)
- Prisma (eq-frontend): add `granola_import_runs` table (schema below)
- Test: `tests/unit/granola_ingestion/test_import_runs.py`, `test_scheduler.py`, `tests/unit/test_granola_admin.py` (NOT `test_granola_router.py` — that file doesn't exist)

**`granola_import_runs` schema** — columns `id, tenant_id, user_id, credential_id (FK + index), state,
total, started_at, finished_at, created_at, updated_at`. `state ∈ {queued, running, complete, failed,
cancelled}`. **Partial unique `UNIQUE (credential_id) WHERE state IN ('queued','running')`** (one active
import per credential; Prisma can't express partial-unique declaratively → add via raw SQL in the
migration). **Progress counts are DERIVED, not stored:** `done/deferred/skipped/errors` come from a
COUNT/GROUP-BY over `external_integration_runs` for the credential since `started_at` — NOT mutable
counter columns, because a stored counter double-counts under DBOS step crash/replay. Additive Prisma
migration; coordinate per [reference_prisma_schema_ownership]; **deploys BEFORE the backend B3 PR.**

- [ ] **Step 1 — (eq-frontend) Prisma migration: `granola_import_runs`** per the schema above (FK
  tenant/user/credential, `@@index([credential_id, state])`, the partial-unique on active state via raw
  SQL in the migration). `prisma migrate create`, PR, Vercel deploy applies it, verify in Neon. Deploys
  BEFORE the backend B3 PR.
- [ ] **Step 2 — Failing test for `import_runs` accessors** (create → running → complete/fail/cancelled;
  the DERIVED progress reader returns correct counts; replaying a progress read is idempotent).
- [ ] **Step 3 — Implement `services/granola_ingestion/import_runs.py`** (progress is DERIVED, not a
  mutable tally): `create_import_run(*, credential_id, tenant_id, user_id) -> uuid` (state='queued',
  total=None); `mark_running(id)`; `set_import_total(id, total)`; `complete_import_run(id)` /
  `fail_import_run(id)` / `cancel_import_run(id)`; `read_import_progress(id) -> {state, total, done,
  deferred, skipped, errors}` where the counts are a COUNT/GROUP-BY over `external_integration_runs` for
  the credential since the run's `started_at` (idempotent under replay). **No `bump_*` counter mutation.**
  All tenant+user scoped.
- [ ] **Step 4 — Failing test: `/connect` (history) returns ACK + dispatches on `GRANOLA_IMPORT_QUEUE`,
  not synchronously** (assert state queued/running, an `import_run_id`, and a dispatch of
  `granola_import_one_credential` — NOT inline `run_one_cycle`).
- [ ] **Step 4b — Failing test: `import_scope="forward"` anchors the watermark (captured at route ENTRY)
  and does NOT import** — assert `resp["import"] is None`, `last_polled_at` ≈ the route-entry timestamp,
  no dispatch.
- [ ] **Step 5 — Rewrite `/connect`’s post-store branch, branching on `import_scope` (D6):**
  - **Capture `forward_anchor_at = datetime.now(UTC)` at the TOP of the route, BEFORE any awaits**, and
    use THAT exact timestamp (not a fresh `NOW()` after the store/encrypt round-trip) so a meeting
    created during the connect round-trip isn't skipped.
  - **`import_scope == "forward"`**: set `last_polled_at = forward_anchor_at` (new vault helper
    `anchor_credential_watermark(credential_id, ts)`) so the first 5-min poll's `created_after` excludes
    existing history. Create NO `import_run`; dispatch NO import. Return `{"ok": true, "status":
    "connected", "import": null}`.
  - **`import_scope == "history"`** (default): leave `last_polled_at = NULL`; `create_import_run(...)`;
    dispatch `granola_import_one_credential` on the **dedicated `GRANOLA_IMPORT_QUEUE`** (= `Queue(
    "granola-import", concurrency=2)` — a 33–83 min import must NOT occupy `GRANOLA_POLL_QUEUE`
    concurrency=5 or it starves other users' 5-min polls) with `SetWorkflowID(f"granola_import_
    {credential_id}_{import_run_id}")` (idempotent dispatch). Return the `import` ACK.
  - **Both:** delete the synchronous `_save_and_test_locked` call.
  - **Enqueue atomicity:** if the process dies after activation but before the import enqueues, `/status`
    (and a `/connect` retry) recovers "active history credential with no running/complete import" by
    creating + enqueuing one.
- [ ] **Step 5b — Active-row folder reconfigure** (NOT `reactivate_credential` — active rows 409): route
  an active-credential folder change through a reconfigure branch that updates `config.folders` UNDER the
  advisory lock and re-applies `import_scope`:
  - `history` → backfill the NEWLY-ADDED folders (a scoped import for just those folders).
  - `forward` → **⚠️ do NOT move the global `last_polled_at` forward** — re-anchoring the single watermark
    to NOW() would SKIP meetings created in ALREADY-watched folders since the last poll. Instead backfill
    only the NEW folders from `now()` (scoped one-shot), leaving the existing watermark untouched. (True
    per-folder watermarks are a later follow-up; v1 takes the scoped-new-folder approach.)
- [ ] **Step 6 — Implement `granola_import_one_credential`** (DBOS workflow, pure orchestration; I/O in steps):
  - **Lock-busy behavior:** acquire the per-credential advisory lock with block-and-retry / requeue — if
    the 5-min poll holds it, the import waits or requeues with backoff and stays `state='queued'`; it must
    NOT silently skip or strand.
  - `mark_running`; `run_one_cycle(credential, pool, import_run_id=...)` with `last_polled_at=NULL` (full
    backfill); set `total` after the first listing.
  - **Progress is read-derived** from `external_integration_runs`; the workflow does NOT increment counters.
  - on clean completion `complete_import_run`; on raise `fail_import_run`; **if the credential is
    deactivated mid-import (`_CredentialDeactivated`, reusing edge-#12), `cancel_import_run`** (don't mark
    complete).
  - **v1 keeps the per-note loop SEQUENTIAL** (background; EQ-93/B4 adds bounded concurrency later).
- [ ] **Step 7 — `/status` `import` block:** read the latest `granola_import_runs` row + the derived
  progress; `state ∈ {queued,running,complete,failed,cancelled}`. While `total` is null (listing not
  done) the FE shows INDETERMINATE progress; once `total` is known, "N of M". Omit the block when no
  import_run exists (e.g. forward connections).
- [ ] **Step 8 — Run unit tests + `verify_consumer_contracts.py`; verify PASS / 0 drift.**
- [ ] **Step 9 — Commit** `feat(granola): background history-import + progress signal; retire sync first poll (EQ-92)`.

**Exit criteria:** `/connect` returns in <~2s with an `import` ACK (history) or `import:null` (forward);
the import runs in the background **on `GRANOLA_IMPORT_QUEUE`** under the advisory lock (never the poll
queue); `/status.import` shows derived progress (indeterminate→N-of-M) and reaches a terminal
`complete/failed/cancelled`; a 300-note import never touches the request thread; the 5-min scheduler and
the import never double-run a credential; a forward-mode add-folder does NOT skip existing-folder
meetings; LOCKED-31 superseded note recorded in the plan + memory.

---

### Phase B4 — (FAST-FOLLOW) Bounded-concurrency intake (EQ-93, ~1–1.5 days, AFTER B3 + frontend v1)

**Do NOT build in the v1 sequence.** Full design + the 6 mandatory corrections live in
`tasks/granola-parallel-intake-investigation.md` §4. Summary of prerequisites (each a hard blocker):
1. **Raise BOTH pools first** (asyncpg `max_size` 10→≥32 + re-derive the documented invariant
   `asyncpg_pool.py:72-86`; SQLAlchemy engine `database.py:97-101` to cover ~25 concurrent classify
   sessions; verify Neon ceiling). Pool sizing is a prerequisite, not a post-hoc check.
2. **Process-GLOBAL token bucket** sized to Granola 5 req/s (NOT a per-cycle Semaphore — the queue runs
   5 credentials concurrently; per-cycle × cross-cycle = burst-cap blowout).
3. **Atomic idempotency claim** (`INSERT … ON CONFLICT (tenant,user,provider,external_id) DO NOTHING
   RETURNING id`) reconciled with the `eq_interaction_id` recovery path; new `SKIPPED_CONCURRENT` outcome.
4. **Import + 5-min scheduler exclusion** via the same advisory lock (already required by B3 step 5).
5. **Keep `_CredentialDeactivated` a true cycle-abort** (don't let `gather(return_exceptions=True)`
   swallow it; shared `asyncio.Event`).
6. **Coarse resumption watermark** for the long import (advance per list-page) so a crash doesn't re-pay
   every `get_note_detail`.
N=5 fan-out width; streaming `asyncio.wait(FIRST_COMPLETED)` refill loop for the backfill. The background
import (B3) is the first consumer. Steady-state 5-min polling stays sequential.

---

## STREAM 2 — FRONTEND (`eq-frontend`)

> **SHARED CHECKOUT — `git branch --show-current` before EVERY commit** ([[feature-branch-safety-protocol]]).
> Currently on `main` @ `202a691f`. Branch `phase-3/granola-fe-<phase>`. Greenfield: zero Granola code today.
> Match the house design system (shadcn new-york + `eq-tokens.css` + `GlassPanel` + Framer Motion +
> Inter/Source Serif 4); follow `docs/page-creation-guide.md`; do NOT invent a new aesthetic.
> Do NOT start F-phases until the backend contracts they consume are DEPLOYED + `/health` 200.

### Phase F1 — Gateway proxy + tRPC procedures over the JWT rail (~0.5–1 day)

**Goal:** Mirror the existing `provider-connections` tRPC router pattern to add Granola procedures that
call the backend via the proven `callBackend` rail. No new auth.

**Files:**
- Create: `lib/trpc/routers/granola.ts` (tRPC router: `validate`, `connect`, `status`, `disconnect`, `rotate`)
- Modify: `lib/trpc/routers/_app.ts` (register `granola` router)
- Reference (mirror, do not modify): `lib/trpc/routers/provider-connections.ts`, `lib/gateway-forward.ts`
  (`callBackend`), `lib/internal-jwt.ts` (`mintInternalJwt`), `lib/gateway-config.ts`
  (`transcriptionServiceUrl`)
- Test: `lib/trpc/routers/__tests__/granola.test.ts` (MSW mocking `callBackend`)

- [ ] **Step 0 — Create the design-partner gate helper** `lib/feature-gates/granola.ts`
  (`isGranolaEnabled(tenantId): boolean`, reads the tenant allowlist — D3). Created HERE because F1's
  procedures need it; F2 reuses it for card rendering.
- [ ] **Step 1 — Failing test:** `granola.validate` mutation calls `callBackend('/integrations/granola/
  validate', { method:'POST', body:{api_key}, service:'transcription' })` and returns `{ok, folders}`;
  the procedure is `protectedProcedure` (carries `ctx.tenantId` + `ctx.pgUserId`); **a NON-allowlisted
  tenant gets `FORBIDDEN` (C12)**.
- [ ] **Step 2 — Run, verify FAIL.**
- [ ] **Step 3 — Implement `lib/trpc/routers/granola.ts`** — 5 procedures, each a thin `callBackend`
  wrapper to the transcription service over the gateway-JWT rail (auto-mints the JWT with `pg_user_id` —
  satisfies `routers/granola.py`’s `pg_user_id` requirement + the `/validate` bearer gate). **C12
  (security): EVERY procedure first calls the gate — the allowlist is enforced on the API, not just the
  rendered card. C11: `connect` carries `importScope` → backend `import_scope`:**
```ts
import { isGranolaEnabled } from "@/lib/feature-gates/granola"
const gate = (ctx) => { if (!isGranolaEnabled(ctx.tenantId)) throw new TRPCError({ code: "FORBIDDEN" }) }

export const granolaRouter = router({
  validate: protectedProcedure.input(z.object({ apiKey: z.string().min(1) }))
    .mutation(({ ctx, input }) => { gate(ctx); return callBackend(ctx, {
      method: "POST", path: "/integrations/granola/validate",
      service: "transcription", body: { api_key: input.apiKey } }) }),
  connect: protectedProcedure.input(z.object({
      apiKey: z.string().min(1),
      mode: z.enum(["folders", "all"]).default("folders"),
      folders: z.array(z.object({ id: z.string(), name: z.string().nullish() })).default([]),
      importScope: z.enum(["history", "forward"]).default("history"),   // C11 / D6
    })).mutation(({ ctx, input }) => { gate(ctx); return callBackend(ctx, {
      method: "POST", path: "/integrations/granola/connect",
      service: "transcription",
      body: { api_key: input.apiKey, mode: input.mode, folders: input.folders,
              import_scope: input.importScope } }) }),       // C11 — snake_case to the backend
  status: protectedProcedure.query(({ ctx }) => { gate(ctx); return callBackend(ctx, {
      method: "GET", path: "/integrations/granola/status", service: "transcription" }) }),
  disconnect: protectedProcedure.mutation(({ ctx }) => { gate(ctx); return callBackend(ctx, {
      method: "DELETE", path: "/integrations/granola", service: "transcription" }) }),
  rotate: protectedProcedure.input(z.object({ newApiKey: z.string().min(1) }))
    .mutation(({ ctx, input }) => { gate(ctx); return callBackend(ctx, {
      method: "POST", path: "/integrations/granola/rotate",
      service: "transcription", body: { new_api_key: input.newApiKey } }) }),
})
```
  (Adapt `callBackend`'s exact option names to its real signature — `gateway-forward.ts:133` — during the
  build; the `service`/URL selection must resolve to `transcriptionServiceUrl`.)
- [ ] **Step 4 — Register** in `_app.ts`; run typecheck + the router test (incl. the FORBIDDEN case);
  verify PASS.
- [ ] **Step 5 — Commit** `feat(granola): tRPC procedures over the gateway-JWT rail`.

(The approvals-queue READ/actions are NOT in this project — the frontend approvals inbox is a separate
future build. The backend already routes unknown-account meetings to the per-user queue.)

**Exit criteria:** all 5 Granola procedures typecheck and pass MSW tests; no new env vars (the rail’s
`INTERNAL_JWT_*` + `BACKEND_SERVICE_TRANSCRIPTION_URL` already exist in prod).

---

### Phase F2 — Design-partner-gated Granola card (onboarding + Settings) (~0.5–1 day)

**Files:**
- Create: `components/eq/onboarding/GranolaConnectCard.tsx` (the card shell + entry CTA; gated)
- Reuse: `lib/feature-gates/granola.ts` (`isGranolaEnabled` — CREATED IN F1 Step 0; the tRPC procedures
  already enforce it server-side per C12)
- Modify: `app/onboarding/(flow)/meeting-connect/meeting-connect-client.tsx` (render the gated card alongside Desktop/Meet/Zoom)
- Modify: `app/(workspace)/settings/connections/connections-client.tsx` (render the gated card in the "Meetings" section)
- Reference (mirror): `components/eq/onboarding/MeetingProviderCard.tsx`, `app/onboarding/(flow)/email-connect/email-connect-client.tsx` (connect-state pattern), `GlassPanel`
- Test: `components/eq/onboarding/__tests__/GranolaConnectCard.test.tsx`

- [ ] **Step 1 — Use the F1 gate helper** `isGranolaEnabled(tenantId)` (tenant allowlist, env
  `GRANOLA_DESIGN_PARTNER_TENANT_IDS` or a small config — D3; evolve to a per-org flag at >10 partners).
  Card rendering is gated on it; the API itself is ALREADY gated in F1 (C12), so this is UX-only —
  defense in depth, not the only check.
- [ ] **Step 2 — Failing RTL test:** the card renders only when gated-on; shows "Connect Granola" CTA in
  the idle state; matches the `MeetingProviderCard` visual shell (GlassPanel).
- [ ] **Step 3 — Implement `GranolaConnectCard`** as a NEW component (the existing `MeetingProviderCard`
  is a single-value OAuth-redirect state machine and cannot host key-paste + folder-pick). States:
  `idle → connecting(wizard) → importing → connected → error`. In F2 it stubs the wizard (F3) and status
  (F4); here it just renders the gated entry + reads `granola.status` to decide idle vs connected.
- [ ] **Step 4 — Wire into both surfaces** (onboarding `meeting-connect`, Settings `connections`),
  rendering only when `isGranolaEnabled`. Keep both skippable (the onboarding flow is soft per
  system-map §B). Reuse the existing card grid.
- [ ] **Step 5 — Run RTL + typecheck; verify PASS. Commit** `feat(granola): gated connect card in onboarding + settings (EQ-94)`.

**Exit criteria:** allowlisted tenants see the Granola card in both surfaces; non-allowlisted see nothing;
matches house design; both surfaces reuse one component.

---

### Phase F3 — Key-paste → user-selected multi-folder picker + import-scope choice (~1 day)

**Files:**
- Create: `components/eq/onboarding/GranolaConnectWizard.tsx` (3-step: paste key → user multi-selects folders → choose import scope)
- Create: `components/eq/onboarding/GranolaFolderPicker.tsx` (multi-select list + chips — the user actively picks folders)
- Create: `components/eq/onboarding/GranolaImportScopeChoice.tsx` (D6 — "Import past meetings" vs "Only going forward")
- Reference (reuse): `components/eq/ui/filter-panel/*` (checkbox list + "N selected" chips pattern),
  `components/ui/{checkbox,badge,command}.tsx` (Radix Checkbox, Badge chips, cmdk searchable combobox)
- Test: RTL tests for both components

- [ ] **Step 1 — Failing test:** paste key → `granola.validate` → on `{ok:true}` advance to folder step
  rendering the returned `folders[]`; on `{ok:false, reason}` show inline reason; multi-select disables
  Continue when 0 selected; the import-scope step defaults to "history" and Connect passes the chosen
  `import_scope`.
- [ ] **Step 2 — Implement the wizard (3 steps):**
  - *Step 1 — paste key* → `validate` mutation (loading ~2s).
  - *Step 2 — user selects folders* (`GranolaFolderPicker`): a multi-select over `folders[]` (reuse the
    FilterPanel checkbox+chip pattern or a lighter inline multi-select over `command.tsx`; folder list
    arrives pre-flattened, subfolders included server-side). "N selected" chips with per-item remove.
    Continue disabled at 0 selected.
  - *Step 3 — import scope* (`GranolaImportScopeChoice`, D6): a 2-option choice —
    **"Import past meetings"** (default, recommended) vs **"Only meetings from now on."** Plain-English
    helper copy (e.g. "Import past meetings: we'll bring in your history from the selected folders so your
    CRM is populated right away. Only from now on: we'll start with your next meeting.").
  - *Connect* calls `granola.connect` with `{ folders, import_scope }`; on success → if `import_scope ===
    "history"` transition the card to `importing` (F4 renders progress); if `"forward"` transition
    straight to `connected` (no progress bar — there's no backfill).
- [ ] **Step 3 — Edge cases:** zero folders selected → Continue disabled; account with no folders →
  empty-state ("create a folder in Granola"); validate error reasons (auth_failed/rate_limited/outage)
  → inline copy; 409 "already connected" → route to the status panel.
- [ ] **Step 4 — Run RTL + typecheck; verify PASS. Commit** `feat(granola): key-paste + multi-folder picker + import-scope wizard (EQ-94)`.

**Exit criteria:** paste→validate→user-picks-folders→choose-scope→connect works against the deployed
backend; "history" shows the import progress, "forward" goes straight to connected with no backfill;
multi-select reuses house primitives; all listed edge cases handled.

---

### Phase F4 — Connected status panel (folders + import progress + disconnect) (~0.5–1 day)

**Files:**
- Create: `components/eq/onboarding/GranolaStatusPanel.tsx`
- Test: RTL test

- [ ] **Step 1 — Failing test:** when `granola.status` returns `connected:true`, render the folder list
  (with per-folder `status` badges: ok / "Needs attention" for not_found/error), the 7-day activity,
  and — when `import.state === 'running'` — an "Importing {done} of {total}" progress bar that POLLS
  `granola.status` every ~4s and STOPS when `state` becomes `complete`/`failed`/`cancelled` — i.e. any
  state ≠ `queued`/`running` (D2). While `total` is null, show INDETERMINATE progress (C14).
- [ ] **Step 2 — Implement the panel:** folder list + statuses; activity (ingested/deferred/errors_7d);
  import progress (poll while running, then a "Imported N meetings" done state); affordances: change
  folders (re-opens the F3 picker → the backend's active-row reconfigure path, D5/C5 — backfills only the
  newly-added folders, does NOT reset the global watermark, C17), rotate key, disconnect (confirm modal →
  `granola.disconnect`). Banners: `status==='revoked'` → "Reconnect"; `status==='error'` → reason + CTA.
- [ ] **Step 3 — Run RTL + typecheck; verify PASS. Commit** `feat(granola): connected status panel + import progress (EQ-94)`.

**Exit criteria:** connected state shows folders + per-folder status + live import progress + activity +
disconnect/rotate/change-folders; polling stops when import completes.

---

### Phase F5 — REMOVED FROM SCOPE (frontend approvals inbox = separate future project)

**Founder decision 2026-06-04:** the user-facing account-creation approval queue UI is **not part of this
project**. It was never scoped here. The BACKEND already routes unknown-account Granola meetings to the
per-user queue (`pending_account_mappings`, `owner_user_id`; live today), so meetings are safely captured;
the dedicated meeting-aware approvals inbox is its own future build. See the "EXPLICITLY OUT OF SCOPE"
note in §0 for the design notes to carry into that future project (meeting-aware via `source_type`,
per-user accessible, approvals through the backend `/queue/{id}/approve` endpoint — NOT the email UI's
direct-Prisma shortcut — and the open shared-vs-separate-queue question). **Linear: EQ-95 moves out of
this project to a future-work issue.**

---

## 3. Testing strategy

- **Unit (both repos):** AsyncMock (backend) / MSW + RTL (frontend), no Docker
  ([feedback_test_pattern_no_docker]). Per-phase tests enumerated above.
- **Integration (backend, Neon test tenant `11111111-…`):** array-`/connect` → multi-folder cycle →
  `external_integration_runs` rows; background import → `granola_import_runs` advances; folder overlap →
  single ingest; folder-set edit → watermark reset. Honor the shared-infra collision protocol
  ([feedback_shared_infrastructure_collision]) before any destructive SQL — check for active agents;
  prefer a Neon branch.
- **Prod E2E (design partner #0 = Peter, real key, folder `fol_sBJi17PeBXpHN7`):** connect via the UI →
  background import progresses ("history" scope) → known-account meetings ingest → downstream consumed
  (no DLQ); verify an unknown-account meeting (Scenario C) lands a row in `pending_account_mappings`
  (`owner_user_id` = Peter) — approve it via the backend `/queue/{id}/approve` endpoint directly (no
  frontend inbox in scope) → next 5-min poll completes ingestion. Also E2E the "forward" scope (connect →
  `import` null, watermark anchored, no backfill; a NEW meeting picked up on the next poll). The test
  credential is KEPT connected + the trigger LIVE (founder standing decision); LOCKED-11 cleanup deferred
  until the founder says.
- **Contract gate (mandatory pre-merge, every backend PR):** `scripts/verify_consumer_contracts.py
  --source generic --interaction-type meeting --extras-keys "granola_note_id,granola_web_url,
  granola_folder_name,granola_summary_text,granola_calendar_event_id,granola_attendees_raw"` → 0 drift;
  `scripts/verify_schema.py` on any new SQL.

## 4. Codex pre-merge gate (every PR)

Per [feedback_codex_pre_merge_gate]: open the PR, run `/codex review --base main`, fold P0/P1, re-run to
0 P1 or the 4-round soft cap (then surface diminishing returns to the founder). Non-negotiable for PRs
touching durability (DBOS/import workflow), schema migrations (the `granola_import_runs` table), and
cross-service contracts (the array `/connect`/`/status`). Founder authorizes each merge + each
Railway/Vercel/AWS/secret change.

## 5. Deploy + merge order (cross-repo)

1. **B1** (backend array contracts; back-compat preserves old clients) → Railway deploy → `/health` 200.
2. **B2** (multi-folder loop) → deploy.
3. **eq-frontend Prisma migration** for `granola_import_runs` (B3 step 1) → Vercel deploy → Neon verified.
4. **B3** (background import; writes `granola_import_runs`) → deploy. **Now `/connect` is array + background-import.**
5. **F1** (tRPC procedures) → **F2** (gated card) → **F3** (wizard) → **F4** (status panel). Each after
   the backend contracts it consumes are live. (F5 approvals inbox is out of scope — separate project.)
6. **B4 (EQ-93 fast-follow)** — after v1 is shipped + the prereqs (pool sizing, global limiter) land.
7. Post-deploy: `/health` 200 across services; one full prod E2E; update memory + Linear + handoff.

## 6. §2.1 backlog carried forward (reference only — address on-trigger, not in v1)

#3 tenant-wide approvals · #9 instant reprocess on approval · #13 bad-folder recovery (folded into
array-`/connect` reconnect, D5) · #16 `_persist_intelligence` re-ingest non-idempotency (shared Lane 2 —
the background import must route idempotent paths; careful review) · #14/#15 defer-path atomicity +
credential-generation token · #17 `/map` 500-vs-503 · #18 Granola Lane 2 fire-and-forget residual.

---

## Design notes (plan-stage design review, 2026-06-04)

- **AI-native progress framing (differentiator):** the import progress should show *value accruing*, not
  just a meeting count — e.g. "Imported 87 of 240 · 24 people linked to accounts." Makes the design
  partner watch their CRM populate in real time (F4). Reuse the existing `GlassPanel` + house tokens; the
  Granola card must look native beside Desktop/Meet/Zoom (`MeetingProviderCard` shell).
- **Import-scope choice:** two-option control, recommended default pre-selected (D6 = history), plain copy
  selling the value ("bring in your history so your CRM is populated right away" vs "start with your next
  meeting"). Keep the wizard to 3 lean steps; don't overwhelm the folder step.
- **Forward path:** no progress bar (no backfill) → straight to "Connected · watching N folders."
- **States to design (F3/F4):** zero folders selected (disable), no folders in account (empty state),
  per-folder "Needs attention" (deleted folder), revoked/error banners, indeterminate→determinate progress.

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | ISSUES_FOLDED | Scope challenge → founder de-scoped the FE approvals inbox (F5) + confirmed the shared per-user queue; DB-verified the queue schema (`source_type`, `owner_user_id`); JWT rail + Prisma-queue + multi-folder contracts verified. Architecture/test/perf walked; findings folded. |
| Codex Review | `/codex consult` | Independent 2nd opinion (gpt-5.5, high) | 4 | CLEAN (build-ready) | R1: 13 findings (C1–C16). R2: corrections must be FOLDED INTO the executable steps (not an overlay) + 2 new P1s (C17–C18). R3: inline fold verified PRESENT-IN-STEPS for all 9 spot-checks; 2 stale watermark-reset references flagged. R4: both fixed; **0 remaining P0/P1; verdict build-ready.** |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | SKIPPED | Scope is founder-locked (6 decisions + this session's de-scope of F5 + import-scope addition). |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | LOW_RISK | Plan-stage assessment (no built UI yet). Small surface reusing house components; key notes captured above (value-framed progress, import-scope copy, wizard steps, empty/error states) + cutting-edge connect-UX research. |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | N/A | Internal integration, not a developer-facing product. |

**CODEX:** 4 rounds to clean. 18 substantive corrections (C1–C18) FOLDED INTO the executable phase steps
(verified PRESENT-IN-STEPS by round-3, confirmed clean by round-4). Most load-bearing: a **separate
`GRANOLA_IMPORT_QUEUE`** (C3 — a long import on `GRANOLA_POLL_QUEUE` would starve other users' polls);
**derived, not-counter import progress** (C1 — DBOS replay double-counts); the **active-row
folder-reconfigure** correction (C5 — `/connect` 409s on active rows, so "reuse reactivate" was false);
the **forward-anchor boundary** (C4) + **forward add-folder watermark** (C17 — don't skip existing-folder
meetings); and a **security gate** fix (C12 — enforce the design-partner allowlist inside every tRPC
procedure, not just card render).

**CROSS-MODEL:** The earlier multi-agent adversarial pass (parallel-intake investigation) and Codex
covered DIFFERENT surfaces — the adversarial pass hardened the EQ-93 fast-follow (pools, global rate
limiter, atomic claim), Codex hardened the new v1 B1/B2/B3 (import queue, progress idempotency, active-row
reconfigure). Together they cover both the v1 and the fast-follow. No direct contradiction.

**UNRESOLVED:** 0 founder-facing decisions open. (F5 scope = out; queue = shared per-user; import-scope
default = history [defaulted; founder may flip to "forward" — a UI default, confirmable at build]; D1–D5
technical calls made; C1–C18 are engineering corrections, now folded into the steps.)

**VERDICT:** ENG + CODEX REVIEWED — was BUILD-READY; **BACKEND COMPLETE + DEPLOYED + PROD-E2E-VERIFIED (2026-06-06):**
**EQ-91 (B1+B2) + EQ-92 (B3) ALL SHIPPED + DEPLOYED** (main @ `2534598` / B3 `061ef37`, active Railway deploy
`105cd404`, `/health` 200; B1 PR #37 `de3b1f3`, B2 PR #38 `922660b`, B3 PR #39 `061ef37` — each TDD'd + Codex
pre-merge-gated; B3's gate ran 4 rounds → clean, 7 P1s folded, on top of a pre-Codex multi-agent review). C1–C18 +
A1–A7 folded into the shipped code. **✅ The prod import E2E (history + forward) PASSED on 2026-06-06** (see the
BUILD STATUS banner + memory `project_granola_integration` 2026-06-06 (E2E) entry); **EQ-92 = DONE in Linear.**
**NEXT = EQ-94 (the greenfield frontend, F1–F4)** now that the backend contracts are LIVE + prod-verified. Residual P2
ticketed (EQ-135, per-activation import-lifecycle scoping). Backend-first build order held; the Codex pre-merge gate
was kept on every backend PR.
