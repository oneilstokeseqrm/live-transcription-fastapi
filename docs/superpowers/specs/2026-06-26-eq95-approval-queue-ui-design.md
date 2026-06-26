# EQ-95 — Account-Creation Approval-Queue Dropdown (per-user, source-agnostic)

**Date:** 2026-06-26
**Status:** Design approved; `/codex consult` hardening folded in 2026-06-26 (2 P1s + P2s) — pending founder spec review
**Linear:** EQ-95 (currently filed under the Granola Integration project but **explicitly de-scoped** from it — founder decision 2026-06-04 — as a separate future project)
**Repos:** `live-transcription-fastapi` (backend read API) + `eq-frontend` (UI). Smoke runs against **dev only**.

---

## 0. Plain-English summary (for the founder)

When we ingest an interaction (a meeting transcript today; email and Granola too) and the sender/attendee's company **isn't an account we already have**, the backend already parks it in a per-user "approval queue" and waits for a human. Approving it triggers the real workflow that **researches the company, creates the account, enriches it, and links the people**. That engine is live. What's missing is a **front door**: a place for *you* (the owner) to see your pending items and approve them.

This spec builds that front door as a small **dropdown button in the top nav, next to "Canvas,"** visible on every page. Click it → a sleek panel lists your pending unknown-company items (meeting title + who triggered it) → **Approve** kicks off the real account creation/enrichment (you watch it say "Creating account… → ✓ added"), or **Ignore** dismisses it. It is a thin UI over the existing backend; it adds no new business logic.

**Two deliberate boundaries:** (1) **"Map to an existing account" is NOT in this version** and **there is no committed plan to add it** — that case stays on the existing admin page only. (2) The live end-to-end test runs **only against dev infrastructure**, never `eq-prod`.

---

## 1. Goals / Non-goals

### Goals
- A per-user, source-agnostic approvals **inbox** reachable from the global top nav on every workspace page.
- Read + act **entirely through the backend** `live-transcription-fastapi` (no direct Prisma mutation of `pending_account_mappings` from the UI).
- Honor the existing, already-live behavior: per-user owner scoping; Approve drives the real DBOS account-provisioning workflow.
- Be **meeting-aware** (show meeting title/attendees when available), degrading gracefully to domain + contacts when not.
- Ship behind a feature flag; verify end-to-end on **dev**.

### Non-goals (explicitly out of scope)
- **"Map to existing account" action** — deferred, **no committed plan/ticket/schedule to add it to this dropdown later**. Remains available only via the existing admin page `app/dashboard/organization/email-pipeline`.
- **Outbound-email** unknown-account capture (a separate, pre-existing backend gap — EQ-220-adjacent).
- **Auto-approve** (that's EQ-219, a separate effort).
- An **orphan-approval reaper** (a deferred backend reliability item; see Risk R-Stall).
- Cursor pagination (V1 uses a simple `LIMIT`).
- Touching the existing admin `email-pipeline` page (left exactly as-is).

---

## 2. Locked decisions (from brainstorming Q&A)

1. **Actions = Approve + Ignore only.** Map deferred (no committed plan — see §1 Non-goals).
2. **Approve UX = in-place "Provisioning…".** The row stays, shows "Creating account…", and on workflow completion shows "✓ {domain} added" (or an error), then drops. Panel polls the item's status while open.
3. **Rows are meeting-aware.** Headline = the unknown **domain** + a source icon; context line = meeting title + attendee count + time (transcripts) / sender + subject (email); plus the triggering contacts. **Title/time/attendees are best-effort** (see §5).
4. **Button = always visible** in the nav + a count **badge** when pending > 0 + a clean empty state. Cheap owner-scoped count.
5. **Placement = next to Canvas (Option A)** — inserted into all five per-page top bars (NOT the sidebar).
6. **Backend-owned (Option 1)** — read *and* write go through `live-transcription-fastapi`; the email-centric Prisma model is left untouched.
7. **Smoke = dev only**, script-first then UI click-through, with test-tenant cleanup. Button behind a **feature flag**.

---

## 3. Architecture — 3 slices, 2 repos

| Slice | Repo | What |
|---|---|---|
| **1. Backend read API** | `live-transcription-fastapi` | New owner-scoped `GET /queue`, `GET /queue/count`, `GET /queue/{id}`. Existing `POST /queue/{id}/approve` + `/ignore` unchanged. |
| **2. Frontend UI** | `eq-frontend` (worktree) | One shared `<ApprovalsQueueButton>` (Popover + panel) + a thin `approvalsQueue` tRPC router cloned from `granola.ts`, inserted next to Canvas in all 5 top bars, behind a feature flag. |
| **3. E2E smoke** | both, **dev only** | Script-first (real dev routes + DB asserts) then UI click-through; atomic test-tenant cleanup. |

The backend remains the single owner of the queue's read, write, auth, and the meeting join. The frontend never reads/writes `pending_account_mappings` via Prisma.

---

## 4. Verified facts (live truth, with citations)

> Source of truth is the **live DB + backend raw SQL**, NOT Prisma. The eq-frontend `PendingAccountMapping` Prisma model is stale/email-era (10 cols, missing `owner_user_id` + 10 others; no signal/calendar models) — do not use it for this feature. (`eq-frontend/prisma/schema.prisma:3344`)

### 4.1 Schema (Neon dev `super-glitter-11265514`; structure identical dev↔prod)
- **`pending_account_mappings`** — 21 cols incl. `owner_user_id UUID NOT NULL`, `discovered_from_type TEXT NOT NULL`, `discovered_from_interaction_id UUID`, `expires_at TIMESTAMPTZ NOT NULL`, `archived_at`, `archive_reason`, `re_open_count INT NOT NULL DEFAULT 0`, `resolved_account_id`, `status TEXT`. `status` is **plain TEXT (no enum/CHECK)** — observed values `pending|approved|creating|mapped|ignored|tenant_review` (`queue_actions.py:184,224,296`; `queue_authorization.py:50`). Indexes: PK(id), UNIQUE(tenant_id,domain), idx(tenant_id), idx(tenant_id,status), idx(tenant_id,archived_at) — **no `owner_user_id` index**.
- **`pending_account_mapping_signals`** — 12 cols: `queue_id`, `source_type TEXT NOT NULL`, `source_user_id UUID NOT NULL`, `interaction_id UUID` (nullable), `calendar_event_id UUID` (nullable), `contact_email NOT NULL`, `contact_display_name`, `contact_role`, `archived_at`. No title/subject on the signal. UNIQUE `(queue_id, contact_email, source_type, interaction_id, calendar_event_id) NULLS NOT DISTINCT`.
- **`raw_interactions`** — has **NO title / occurred_at / attendees** (9 cols; `raw_text` = body). Confirmed twice.
- **`calendar_events`** — `title TEXT NOT NULL`, `start_time/end_time TIMESTAMPTZ`, `organizer_*`, `status` (matcher requires `'confirmed'`). (`transcript_enrichment.py:456-462,485-491`)
- **`calendar_event_attendees`** — `(calendar_event_id, email, display_name, is_organizer, is_resource, is_optional)`; attendee count excludes `is_resource=true` (`transcript_enrichment.py:518-526`).
- **Signal anchor subtlety:** for live-recording transcripts, `signal.interaction_id == calendar_events.id` and `calendar_event_id` is set (proven 29/29 in dev). The **Granola path always sets `calendar_event_id = NULL`** and `interaction_id` = a real transcript interaction (`granola_ingestion/adapter.py:1751-1752, 894-898`).

### 4.2 Backend auth
- `/queue` router `prefix="/queue"`, no extra mount prefix → new routes are literally `GET /queue`, `GET /queue/count`, `GET /queue/{id}` (`queue_actions.py:73`, `main.py:195`).
- Auth dependency: `ctx = get_auth_context_polling(request)` (read variant, no `X-Account-ID`) (`context_utils.py:221`; used `queue_actions.py:452,616,902`).
- JWT: `verify_internal_jwt` — HS256, `INTERNAL_JWT_SECRET`, iss `eq-frontend`, aud `eq-backend`, required `tenant_id` (UUID) + `user_id`, optional `pg_user_id` (`jwt_auth.py:106-205`).
- **Owner identity = `_effective_user_id(ctx)` = `ctx.pg_user_id or ctx.user_id`** — the exact expression that inserts `owner_user_id` (`queue_actions.py:396-418`). So `pg_user_id` is load-bearing; `owner_user_id` stores the pg UUID.
- Owner check today: `can_act_on_queue_entry` → `user_id == owner_user_id` (`queue_authorization.py:29-52`). For a LIST this collapses to a WHERE clause.
- Reads use plain `get_async_session()` (NOT `tenant_session`): `pending_account_mappings` is **not** in the FORCE-RLS set (`tenant_scope.py:5-9`) → **app-level tenant filtering is the security boundary** (mirrors `upload.py:374-388`).
- SQL conventions: module-level `text("""...""")`; **`CAST(:x AS uuid)` binds — never `:x::uuid`** (SQLAlchemy 2.0.49 bug, `queue_actions.py:195-198`); project UUIDs `::text`. Response-model precedent: `GET /upload/status/{job_id}` → `response_model=JobStatusResponse` (`upload.py:342`). **No list/pagination endpoint exists** in the service today.

### 4.3 Frontend auth (the pattern to clone)
- `mintInternalJwt` (`lib/internal-jwt.ts:37`) — jose `SignJWT`, HS256, `INTERNAL_JWT_SECRET`, iss `INTERNAL_JWT_ISSUER`, aud default `eq-backend`, exp 300s; emits `tenant_id + user_id + pg_user_id` when truthy. **Mints exactly the claim set the backend verifies — 1:1, no mismatch.**
- `pg_user_id` carried end-to-end: `protectedProcedure` **throws FORBIDDEN if `pgUserId` is unresolved** (`lib/trpc/init.ts:77-84`) → guaranteed present for any `/queue` call.
- **Copyable precedent: `lib/trpc/routers/granola.ts`** — already proxies to **this** backend (`gatewayConfig.transcriptionServiceUrl`) via `callBackend({... authContext: buildAuthContext(ctx)})` at the **default audience**; registered `granola: granolaRouter` (`_app.ts:63`).
- **Watch-outs:** do NOT mirror `lib/trpc/routers/agent-queue.ts` (it hits Prisma `agentActionQueue` directly — a red herring). Do NOT copy a synthesize/workspace router (it overrides the audience → would 401).

### 4.4 Frontend UI conventions
- Popover: `components/ui/popover.tsx`; list-in-popover precedent `components/organization-switcher.tsx:51-132`.
- Count-badge precedent: **`AgentQueueBadge` (`sidebar.tsx:92-109`)** — `trpc.agentQueue.readyCount.useQuery(undefined,{refetchInterval:30_000})`, null at 0, `99+` cap, classes `text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-ui-blue-600 text-white`.
- Canvas pin badge (visual parity): `px-1.5 py-0.5 text-[10px] font-medium bg-ui-blue-500 text-white rounded-full leading-none` (identical across all 5 bars).
- tRPC client `@/lib/trpc/client`; polled-query precedent `agent-queue/page.tsx:30-38` (15s); mutation+invalidate `AnomalyCards.tsx:52-58`.
- Feature flag: `process.env.NEXT_PUBLIC_..._ENABLED === 'true'` inline (`accounts/layout.tsx:15`) + server `requireAddAccountEnabled()` FORBIDDEN guard (`account-capture.ts:19-26`).
- Toast: `sonner` (mounted `app/layout.tsx:51`).
- The 5 top bars do **not** share a right-actions component: `account-top-bar.tsx:42-44` + `pipeline-top-bar.tsx:38-40` accept a `rightActions` prop (insert before Canvas); `home-top-bar.tsx:63`, `trends-top-bar.tsx:26`, and `app/(workspace)/intelligence/layout.tsx:54` need direct insertion.

---

## 5. Backend read API contract

All three are **owner-scoped and JWT-only**. **Auth hardening (Codex P1 + P2):**
- **JWT-only — reject legacy header auth.** `get_auth_context_polling` will honor spoofable `X-Tenant-ID`/`X-User-ID` headers when `ALLOW_LEGACY_HEADER_AUTH=true` (`context_utils.py:286,452`). On a **non-RLS** table those headers would be the *entire* owner/tenant boundary. These read endpoints MUST require a verified internal JWT and **401 if the context was resolved via legacy headers** (no Bearer token).
- **Require `pg_user_id`** — do NOT fall back to `user_id`. `owner_user_id` was inserted as `pg_user_id or user_id` (`queue_actions.py:396`); requiring `pg_user_id` on read makes the owner identity unambiguous and UUID-typed. Return **403/empty** if absent (the frontend `protectedProcedure` already guarantees it). The UUID-shape guard stays as defense-in-depth (malformed id → empty, never a 500).
- **Owner/tenant predicate enforced IN SQL on every query** (never a bare-id SELECT + Python check like the existing action path at `queue_actions.py:147,333`): `WHERE tenant_id = CAST(:tenant AS uuid) AND owner_user_id = CAST(:owner AS uuid) AND archived_at IS NULL` (+ `status='pending'` for list/count).

Conventions: `get_auth_context_polling` (+ the JWT-only/`pg_user_id` guards above), `CAST(:x AS uuid)` binds, `::text` projections, plain session.

### `GET /queue/count` → `{ "count": int }`
`SELECT count(*) FROM pending_account_mappings WHERE <owner-scope> AND status='pending'`. Single-table, no joins. Feeds the badge (polled 30s).

### `GET /queue` → `{ "entries": [ Entry ] }` (pending-only, `ORDER BY created_at DESC LIMIT 50`)
`Entry`:
- **Guaranteed (always present):** `queueId, domain, status, sourceType, createdAt, expiresAt, reOpenCount, contactCount, contacts:[{email,displayName,role}]`. Contacts come from the signals. **One batched query, NOT per-row (Codex P2 — N+1):** a 50-row list must NOT fire 51 queries. Use a single statement — a CTE of the owner-scoped pending queue ids, then a `LEFT JOIN LATERAL`/aggregate over `pending_account_mapping_signals` (with `archived_at IS NULL`) that yields `contacts` (json_agg, capped, e.g. first 3) + `contactCount = COUNT(DISTINCT contact_email)` per queue id, plus the context join (below) in the same statement. No per-row scalar subqueries for attendee_count either. **This is the load-bearing display data.**
- **Best-effort (may be null):** `contextSource ∈ {calendar,email,interaction_summary,none}`, and when resolvable `meetingTitle, occurredAt, attendeeCount` (calendar) / `subject, sender, sentAt` (email).

**Context resolution (per a representative active signal — earliest by `created_at`):**

| `contextSource` | Condition | Title / time source | Verified? |
|---|---|---|---|
| **calendar** | transcript + `calendar_event_id IS NOT NULL` | `LEFT JOIN calendar_events ce ON ce.id = s.calendar_event_id` → `ce.title`, `ce.start_time`; attendee_count = `count(*) FROM calendar_event_attendees WHERE calendar_event_id = s.calendar_event_id AND is_resource=false` | ✅ proven on live data (29/29) |
| **email** | `source_type='email'` | `LEFT JOIN pending_interactions pi ON pi.interaction_id = s.interaction_id` → `subject`, `sent_at`, `from_name/from_email` | ⚠️ schema-plausible, **unverified** (0 email rows) |
| **interaction_summary** | Granola: `calendar_event_id IS NULL` + `interaction_id` resolves to a real interaction | `interaction_summaries.summary_title` keyed `(tenant_id, interaction_id, summary_type)`; occurred_at via `raw_interactions.created_at` | ⚠️ async/often-null, **untested** (0 Granola rows) |
| **none** | no match | domain + contacts only | reasoned |

**Design rule:** the branch selector keys on `(source_type, calendar_event_id IS NOT NULL, whether interaction_id resolves to calendar_events vs a real interaction)` — **not** `calendar_event_id` alone (that misroutes Granola). `meetingTitle/occurredAt/attendeeCount/subject` are best-effort; the frontend MUST render gracefully when null (domain + contactCount). **V1 ships the calendar branch fully; email + interaction_summary ride the same graceful fallback** until confirmed.

### `GET /queue/{id}` → `{ status, resolvedAccountId, domain }`
Owner-scoped single-row status read, for the in-place "Provisioning…" poll after Approve. The owner/tenant predicate is **in the SQL** (`WHERE id = CAST(:id AS uuid) AND tenant_id = … AND owner_user_id = …`) — do NOT copy the existing bare-id SELECT that defers the owner check to Python (`queue_actions.py:147,333`). **Register `/queue/count` before `/queue/{id}`** (static before dynamic; don't rely on FastAPI specificity).

**Completion signal — CORRECTED (Codex P1):** terminal success is **`status = 'mapped' AND resolved_account_id IS NOT NULL`**, NOT merely `resolvedAccountId` present. Reopening a queue row sets `status='pending'` but **does not clear** `resolved_account_id`/`mapped_at` (acknowledged TODO at `pending_account_mappings.py:54,61`), so a reopened-but-pending row carries a *stale* `resolved_account_id`. The UI must treat the row as still pending unless `status='mapped'`. (Verified terminal write: `steps.py:73` → `creating`; `materialization.py:510` sets `status='mapped'` + `resolved_account_id` + `mapped_at`.)

### Index
`CREATE INDEX ON pending_account_mappings (tenant_id, owner_user_id, archived_at)` — none exists today; ship with EQ-95 (cheap, correctness-neutral, perf-positive).

---

## 6. Frontend component plan (4 new files; Option A placement)

1. **`lib/trpc/routers/approvals-queue.ts`** — clone `granola.ts`. `protectedProcedure`: `list` (GET /queue), `count` (GET /queue/count), `status` (GET /queue/{id}), `approve` (POST /queue/{id}/approve), `ignore` (POST /queue/{id}/ignore). Register `approvalsQueue: approvalsQueueRouter` in `_app.ts`. Server-side `requireApprovalsQueueEnabled()` FORBIDDEN guard (copy `account-capture.ts:19-26`). Reuse granola's `unwrap()`/`backendErrorCode()`. **No new env var, no new mint helper, no audience override.**
2. **`components/eq/approvals/approvals-queue-button.tsx`** — `<Popover>` + trigger `<button>` styled to match Canvas (parity classes), with a count badge mirroring `AgentQueueBadge` (poll `count` 30s, hide at 0, `99+` cap). `<PopoverContent className="w-[360px] p-0" align="end">`.
3. **`components/eq/approvals/approvals-queue-panel.tsx`** — scrollable list (mirror `agent-queue/page.tsx` patterns: Skeleton, empty state), `list` polled 15s; per-row Approve/Ignore via `useMutation({ onSuccess: () => { listQuery.refetch(); utils.approvalsQueue.count.invalidate() } })` + `toast`; in-place provisioning via the `status` poll.
4. **Feature gate** — `NEXT_PUBLIC_APPROVALS_QUEUE_ENABLED` inline + the server FORBIDDEN guard.

**Insertion (Option A — 5 points):** `account-top-bar.tsx` + `pipeline-top-bar.tsx` via `rightActions` (before Canvas); `home-top-bar.tsx`, `trends-top-bar.tsx`, `intelligence/layout.tsx` via direct edit. New shared `<ApprovalsQueueButton/>` keeps our code DRY; we deliberately do NOT refactor the pre-existing duplicated Canvas button.

---

## 7. Data flow

```
INGEST   unknown business domain → pending_account_mappings (owner=recording user) + signal
BADGE    every page → button polls GET /queue/count (30s) → badge (hidden at 0)
OPEN     click → Popover → GET /queue → rows (domain + meeting title|contacts); re-poll 15s
APPROVE  POST /queue/{id}/approve (202) → row → "Creating account…" (local state)
         → poll GET /queue/{id} → completion (status='mapped' AND resolvedAccountId) → "✓ {domain} added" → drop
         → invalidate count.  Failure → error + Retry, row stays.
IGNORE   POST /queue/{id}/ignore → drop optimistically → invalidate count
```

The list is **pending-only**; the per-item `status` poll in local state owns the "Provisioning…" display until the account exists, then the row drops. Clean inbox + real confirmation.

---

## 8. Error handling & edge cases

| Case | Handling |
|---|---|
| Best-effort context null (Granola/email/no-calendar) | Render domain + contacts + source icon; never a broken title. |
| Acted-on elsewhere / expired (`expires_at`) | Backend idempotent + 404/409 on archived/already-acted → frontend refetch list + "already handled" toast. |
| Malformed effective user id | Backend UUID-shape guard → empty result, not 500. |
| Provisioning stalls (deferred orphan-approval edge) | After a timeout: "Still working — check back," stop spinner (non-blocking), no auto-retry; admin page is the backstop. **Documented V1 limitation.** |
| Empty inbox | "No pending approvals." |
| Feature flag off | Button hidden + server FORBIDDEN on direct calls. |
| >50 pending (rare per-user) | `LIMIT 50` + "+N more" hint; cursor pagination = follow-up. |

---

## 9. Testing

- **Backend unit/integration:** the 3 GET handlers — owner-scoping (only your rows), tenant isolation, calendar join, null-context fallback, UUID guard, pending-only filter, count correctness. Use the repo's DB-test markers.
- **Frontend:** panel states (loading/empty/rows/approve→provisioning→done/ignore), badge hide-at-0, flag gate; tRPC router tests mirroring granola's.
- **Live e2e smoke — DEV only** (never `eq-prod`):
  1. **Pre-smoke gate:** verify dev `live-transcription-fastapi`'s `AGENT_ACTION_CORE_BASE_URL` + DB point at **dev** (non-secret config check).
  2. **Script-first:** disposable test tenant → ingest a transcript **with a calendar event** (proven branch) for an unknown domain → assert queue row + signal (owner=test user) → `GET /queue` with a minted **dev** JWT → assert row + meeting title → `POST .../approve` → poll → assert account + `account_domains` + contact link → **atomic cleanup** (LOCKED-11).
  3. **UI click-through:** dev frontend as the test user → open dropdown → see row → Approve → "Provisioning… → ✓ added" → badge decrements.
  - The **calendar branch is the verifiable path**; email/Granola branches are best-effort, not hard smoke assertions.

---

## 10. Open risks & resolutions

| # | Risk | Resolution |
|---|---|---|
| R1 | Context discriminator misroutes Granola rows (`calendar_event_id` alone is wrong) | Model `contextSource ∈ {calendar,email,interaction_summary,none}`; branch on `(source_type, calendar_event_id IS NOT NULL, interaction_id resolves to calendar vs real interaction)`; all title fields best-effort with domain+contacts fallback. |
| R2 | Email branch unverified (0 email rows in dev; out-of-repo writer) | Ship calendar branch first; email branch behind the same fallback. **Verify-first:** confirm the exact `interaction_id` value the eq-email-pipeline orchestrator writes before locking the email JOIN. |
| R3 | `interaction_summary` (Granola) branch untested; `summary_title` async | Best-effort/often-null; never block a row on it. |
| R4 | No `owner_user_id` index | Add `(tenant_id, owner_user_id, archived_at)` index with EQ-95. |
| R5 | `pg_user_id` is an *optional* backend claim → fallback to Auth0 `user_id` would zero-match/CAST-error | Frontend `protectedProcedure` guarantees it; **add a UUID-shape guard in the read handler** → empty result. |
| R6 | Which statuses the dropdown shows | V1 = `status='pending'`; treat `status` as an open TEXT set. |
| R7 | No pagination convention | V1 `LIMIT 50` + "+N more"; cursor pagination = follow-up. |
| R-Stall | Orphan/stranded approval (workflow never runs) | Out of scope (deferred reaper). UI shows non-blocking "Still working"; admin page backstop. |
| R8 | Verify dev↔prod schema parity before lock | **Verify-first** in the plan (structure stated identical). |
| R-Terminal | Completion signal for the in-place provisioning poll | ✅ **RESOLVED (Codex-verified):** the approve workflow writes the terminal state — `steps.py:73` → `creating`; `materialization.py:510` sets `status='mapped'` + `resolved_account_id` + `mapped_at`. Completion predicate = `status='mapped' AND resolved_account_id IS NOT NULL`. |
| **R-Auth** (Codex **P1**) | Legacy header auth (`ALLOW_LEGACY_HEADER_AUTH`) would let spoofed `X-Tenant-ID`/`X-User-ID` headers be the entire boundary on a no-RLS table | Read endpoints are **JWT-only**; 401 if context came from legacy headers; **require `pg_user_id`**. (§5) |
| **R-Reopen** (Codex **P1**) | Reopened rows carry **stale** `resolved_account_id`/`mapped_at` (`pending_account_mappings.py:54,61` TODO) → a `resolvedAccountId != null` signal would false-positive "✓ added" | Completion = `status='mapped' AND resolved_account_id IS NOT NULL` (not `resolvedAccountId` alone). The pending list filters `status='pending'` so stale fields never render as done. |
| **R-N+1** (Codex P2) | Per-row contacts/attendee subqueries → 51 queries for a 50-row list | Single batched query (CTE + aggregate); no per-row scalar subqueries. (§5) |
| **R-OwnerSQL** (Codex P2) | Copying the existing bare-id SELECT + Python owner check into `GET /queue/{id}` would drop the owner predicate | Enforce tenant+owner **in SQL** on every read query. (§5) |

---

## 11. Verify-first checklist (carried into the implementation plan)
1. **R2** — the exact `signal.interaction_id` value written by the eq-email-pipeline orchestrator for email rows.
2. **R8** — dev↔prod schema parity for `pending_account_mappings` / signals / calendar tables.
3. **Pre-smoke** — dev service downstream wiring (`AGENT_ACTION_CORE_BASE_URL`, DB URL) points at dev, not `eq-prod`.

*(R-Terminal removed — Codex closed it: completion = `status='mapped' AND resolved_account_id`.)*

---

## 12. Out of scope / deferred (with status)
- **Map-to-existing-account in the dropdown** — deferred, **no committed plan**.
- **Outbound-email** unknown-account capture — pre-existing backend gap.
- **Auto-approve** — EQ-219.
- **Orphan-approval reaper** — deferred backend reliability.
- **Cursor pagination** — follow-up.
- **Prisma model reconciliation** — not needed (backend owns the read; UI reads the DTO).
