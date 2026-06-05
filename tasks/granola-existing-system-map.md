# Granola Integration — "What Exists Today" System Map (both repos)

**Date:** 2026-06-04
**Purpose:** A continuity reference for the multi-session Granola Phase 3 work. Captures what is
ALREADY BUILT in (a) the backend (`live-transcription-fastapi`) and (b) the frontend (`eq-frontend`),
plus the cross-repo contract between them, with file pointers. Distilled from two deep investigations
this session (onboarding-placement + multi-folder feasibility). Read this to understand the ground
truth before planning/building Phase 3.

**Companion docs:** `tasks/granola-multi-folder-investigation.md` (multi-folder feasibility + data
model), `tasks/granola-integration-plan.md` (the original Phase 2/3 plan + §2.1 backlog),
`docs/contacts-architecture.md` (the contacts/accounts/queue data the UI surfaces).

---

## A. BACKEND — `live-transcription-fastapi` (the Granola engine; SHIPPED + LIVE)

**State:** Functionally complete and live in production. main tip `e0aafbe` (#36 contact resolution)
+ `fafaee2` (docs). Railway prod deploy `eca82628` SUCCESS; `/health` 200. Prod has
`ALLOW_LEGACY_HEADER_AUTH=true`.

**The end-to-end flow that works today:** connect a Granola account (currently via a hand-minted JWT,
no UI) → a 5-min EventBridge Rule pings a cron endpoint → the scheduler polls the connected folder →
new meetings are ingested, cleaned (LLM), summarized, and linked to the right company AND people →
published downstream to the relationship graph. Proven E2E in prod on a real meeting (`bca60296`).

### Key backend files

| File | Role |
|---|---|
| `routers/granola.py` | Admin endpoints the UI will consume: `POST /validate` (key→folders), `POST /connect` (store + synchronous "save & test" first poll — LOCKED-31), `POST /rotate`, `GET /status` (7-day activity, no decrypt), `DELETE` (soft-delete). All JWT-authed; require `pg_user_id`. **No `PATCH /folder` exists.** |
| `routers/queue_actions.py` | Pending-approvals actions: `POST /queue/{id}/approve`, `/map`, `/ignore`. **No GET/list endpoint** — the UI must read the queue another way (Prisma direct or a new endpoint). First-owner-wins, per-user. |
| `routers/granola_cron.py` | `POST /internal/granola/cron-tick` (X-Internal-Cron-Secret) — the 5-min EventBridge Rule hits this. |
| `services/granola_ingestion/adapter.py` | `run_one_cycle` — the per-credential poll. **Polls ONE folder** (`list_notes(folder_id=config["folder_id"])`); processes notes **SEQUENTIALLY** (`for note in notes: await process_note(...)`). Per-note idempotency anchor in `external_integration_runs`. `_resolve_known_account_contacts` + `_build_envelope` (contact resolution, #36). |
| `services/granola_ingestion/scheduler.py` | DBOS workflow + per-credential advisory lock (serializes overlapping cycles). |
| `services/granola_ingestion/api_client.py` | Granola HTTP client. `public-api.granola.ai/v1`. `list_folders`/`list_notes(folder_id, created_after)`/`get_note_detail`. Cursor pagination, retry/429 handling. |
| `services/granola_ingestion/path2.py` | Scenario A (known account → ingest) / C (unknown → defer to approval queue) / D (no business attendee → skip). |
| `services/granola_ingestion/contact_resolution.py` | (#36) race-safe `find_or_create_contact` (`INSERT … ON CONFLICT (tenant,email)`). |
| `services/vault/` | KMS-envelope-encrypted credential store. `user_credentials.py` — `store_credential`, `get_granola_credential_for_user`, `reactivate_credential` (nulls `last_polled_at` on folder change), `archive_credential`. `config` is opaque JSONB. |
| `services/text_clean_service.py` | `process(*, tenant_id, user_id, account_id, envelope, lane2_extras)` — the LLM clean + Lane 1 publish + Lane 2 dispatch. Backpressure cap (`TEXT_CLEAN_MAX_BG_TASKS=50`). |
| `services/intelligence_service.py` | Lane 2: writes Postgres `raw_interactions`/`interaction_summaries`/`interaction_contact_links`. `_persist_intelligence` has a known re-ingest non-idempotency (§2.1 #16). |

### Backend data model (Prisma-owned by eq-frontend, written by this service)

- `vault.user_credentials` — one row per `(tenant, user, provider='granola')` (UNIQUE). `config` JSONB
  currently `{folder_id, folder_name}` (**singular** — multi-folder changes this to a list). Encrypted
  key + status lifecycle + `last_polled_at` watermark (single, per-credential).
- `public.external_integration_runs` — per-note dedup ledger. UNIQUE `(tenant,user,provider,external_id=note_id)`.
  This is what makes overlapping folders / re-ingests safe.

### Two architecture facts that matter for Phase 3

1. **Downstream is async + queued + parallel.** Ingest publishes an EnvelopeV1 to EventBridge (Lane 1)
   → SQS fan-out → independent consumers (eq-structured-graph-core, action-item-graph,
   opportunity-forecasting, thematic-lm) each process off their own queue, concurrently. They do NOT
   hold the Granola connection open. (See `docs/contacts-architecture.md` §4.)
2. **Intake is SEQUENTIAL + the first poll is SYNCHRONOUS.** Within a poll cycle, meetings are
   processed one-at-a-time (the slow part is the LLM clean, seconds each). And `/connect` runs the
   first backfill *inside the HTTP request* (LOCKED-31) for instant confirmation. Combined with
   Railway's hard ~5-min request cap, a real design partner's existing history (hundreds of meetings)
   would time out. **This is the load-bearing reason Phase 3 must move the first backfill to the
   background + (fast-follow) parallelize intake.**

---

## B. FRONTEND — `eq-frontend` (Next.js 16, App Router, Vercel; Granola = GREENFIELD here)

**State:** Granola has **ZERO frontend code** today (only Prisma models the backend writes). The
checkout is SHARED and branch-hops frequently (~18 active worktrees; was on `docs/chat-modernization-handoff`
@ `1e55dabb` during investigation). **Always `git branch --show-current` before any commit.**

### The existing onboarding wizard (the natural home for Granola)

A real, functional 5-step wizard runs after org creation:
**create org → ① company interview → ② email-connect → ③ meeting-connect → ④ persona → ⑤ seed → /home.**

- Step ③ `app/onboarding/(flow)/meeting-connect/` asks literally **"How should EQ capture your
  meetings?"** and offers exactly **EQ Desktop App / Google Meet / Zoom** — all functional, no Granola.
  This is where the gated Granola card goes.
- It's a "soft" wizard (per-step `router.push` navigation; only persona is a hard gate). Every step is
  **skippable**. The flow is **per-USER** (persona, seed, connections all keyed by user) and
  re-enterable. `meeting-connect` cards are ALSO reused in **Settings → Connections → "Meetings"**.

### The two connection patterns (and which one Granola uses)

| Pattern | Used by | Mechanism | Granola? |
|---|---|---|---|
| **OAuth connect-token** | gmail/outlook/zoom/google_meet | `provider-connections.mintConnectToken` → ConnectSession nonce + signed JWT → redirect to `EMAIL_PIPELINE_URL` OAuth → returns `?status=` → poll `ProviderConnection` | **NO** — Granola has no OAuth API |
| **Gateway-JWT proxy** | context-capture + other backend calls | `lib/gateway-forward.ts` `callBackend` mints an internal JWT carrying **`pg_user_id`** (`lib/internal-jwt.ts`) → POST to `BACKEND_SERVICE_TRANSCRIPTION_URL` (= live-transcription-fastapi) | **YES — this is Granola's rail** |

**Critical finding:** the frontend ALREADY has the exact primitive the Granola backend wants (an
internal JWT with `pg_user_id`, pointed at the transcription service). The earlier-feared "no way to
call the Granola backend" worry is FALSE. Granola connect = a new proxy route/tRPC procedures over the
EXISTING gateway-JWT rail (key-paste → validate → pick folder(s) → connect), NOT the OAuth pattern.
**Build-time verify:** the live-transcription `routers/granola.py` JWT audience/claims accept the
frontend's `mintInternalJwt` token as-is.

### Key frontend files

| File | Role |
|---|---|
| `app/onboarding/(flow)/meeting-connect/{page,meeting-connect-client}.tsx` | The "capture your meetings" step — add the gated Granola card here. |
| `app/onboarding/(flow)/email-connect/*` | Sibling pattern (single-select, gates Continue on connect). |
| `app/(workspace)/settings/connections/{page,connections-client}.tsx` | Settings home for connections (disconnect/last-sync/status) — the Granola management surface. |
| `components/eq/onboarding/MeetingProviderCard.tsx` | Card shell. **Single-value** state machine — needs a new `GranolaConnectCard`/`GranolaFolderPicker` sub-component (key-paste + multi-folder list). |
| `components/eq/ui/filter-panel/*` | House-style **multi-select** primitive (checkbox list + "N selected" chips + per-item remove) to reuse for the folder multi-picker. Also `components/ui/{checkbox,badge,command}.tsx`. |
| `lib/gateway-forward.ts`, `lib/internal-jwt.ts`, `lib/gateway-config.ts` | The gateway-JWT rail (`callBackend`, `mintInternalJwt`, `BACKEND_SERVICE_TRANSCRIPTION_URL`). |
| `lib/trpc/routers/provider-connections.ts` | tRPC connection router pattern to mirror. |
| `prisma/schema.prisma` | OWNS the schema. `vault.user_credentials` (~L4559), `external_integration_runs` (~L4665), `ProviderConnection` (~L3410). |

### Design system

shadcn/ui (new-york) + `styles/eq-tokens.css` (CSS-var tokens) + `GlassPanel` + Framer Motion + Inter/
Source Serif 4. No `DESIGN.md`; there's a `docs/page-creation-guide.md`. Match this; don't invent.

---

## C. Cross-repo contract (don't break)

- **EnvelopeV1 is immutable from upstream** (LOCKED-38). Granola ingest fits the existing `source`/
  `interaction_type` enum (`generic`/`meeting`); verify via `scripts/verify_consumer_contracts.py`.
- **Prisma schema is owned by eq-frontend.** Any column/table change goes through its migration
  pipeline. (Multi-folder needs NO migration — `config` is opaque JSONB.)
- **Tenant isolation everywhere**; per-user credentials; the internal JWT carries tenant + pg_user_id.
