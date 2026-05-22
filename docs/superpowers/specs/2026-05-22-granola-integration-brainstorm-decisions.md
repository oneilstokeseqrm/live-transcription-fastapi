# Granola Integration — Brainstorm Decisions & Handoff

**Date:** 2026-05-22
**Status:** Brainstorm ~70% complete. Q1–Q5 + major framing decisions resolved. Empirical investigations complete. Q6–Q8 still open.
**Next session job:** Read this doc → complete Q6/Q7/Q8 → write formal implementation plan → optionally codex consult → start build.
**Author:** Captured from a single brainstorming session (2026-05-22).

---

## Background

### What we're building

A Granola.ai transcript ingestion adapter for EQ. Three design partners on Granola's Business plan each generate a personal API key + create a designated folder in Granola called "EQ" (or similar). EQ polls each user's folder every 5 minutes and ingests new transcripts into the EQ pipeline, treating them identically to transcripts captured via the existing WebSocket / upload / batch paths.

### Why this is straightforward (compared to a year ago)

Granola released an **official Personal API** in late March 2026 (Series C + API launch, March 25). The API is **included in the Business plan at no extra cost**. Each user generates a `grn_…` API key in Granola settings; no OAuth flow is needed. The reverse-engineering era is over (the leading repo was archived Feb 5, 2026, explicitly because the official API obsoleted it).

### Design partner context

- 3 design partners, all on Granola Business plan (not Enterprise)
- User (Peter) has a personal Business plan account — used as design partner #0 for testing
- Goal: MVP-scale; not public production
- Must be automatic (no manual copy/paste per meeting)
- Must filter out personal/shared content (the folder filter handles this)

### Empirical Granola API findings (verified against Peter's account on 2026-05-21)

Key facts confirmed by direct API calls:

- `GET /v1/notes?folder_id=<id>` works as a server-side filter. Sending a bogus folder_id returns `VALIDATION_ERROR: Invalid folder ID format` — proving the param is recognized.
- `GET /v1/notes/{id}?include=transcript` returns full payload including: `attendees`, `calendar_event`, `folder_membership`, `transcript` (array of {text, start_time, end_time, speaker.source}), `summary_markdown`, `summary_text`, `web_url`.
- Folder IDs use `fol_` prefix; note IDs use `not_` prefix.
- `folder_membership` on a note's detail returns full objects: `[{object, id, name, parent_folder_id}]`.
- Speaker labels in transcripts are by audio source only (`microphone` for API key holder; `speaker` for everything else). No name-level diarization in the transcript itself.
- `attendees` on the note carries names + emails when the meeting was linked to a Google Calendar event; only the API key holder when ad-hoc.
- Rate limits (5 req/sec sustained, 300/min) are vastly more than this use case needs.

### Existing EQ infrastructure (verified, not assumed)

**Already built and battle-tested:**
- `/queue/{queue_id}/approve` endpoint at `routers/queue_actions.py`
- DBOS workflow at `services/account_provisioning/workflow.py` — 7 steps from approve → account created + contacts materialized + downstream events emitted
- Step 3 (`call_agent_enrich`) calls `eq-agent-action-core` `/api/enrich` for company enrichment
- `lookup_account_by_domain(tenant_id, domain)` in `services/account_lookup.py`
- `classify_domain(domain, internal_domains)` in `services/domain_classification.py` (returns personal/internal/business)
- `get_tenant_internal_domains(tenant_id)` in `services/internal_domains.py` (reads from `provider_connections` table)
- `upsert_queue_entry()` + `insert_signal()` in `services/pending_account_mappings.py`
- `TranscriptEnrichmentService` in `services/transcript_enrichment.py` (calendar matching, attendee resolution, signal queuing for unknown business attendees)

**Workflow is provider-agnostic.** Per the investigation, it handles transcript-source signals identically to email-source signals. Existing `TranscriptEnrichmentService` already writes `source_type="transcript"` signals to production today (for unknown secondary attendees on WebSocket-recorded meetings). Granola adds volume to this pattern but no new code path inside the workflow.

---

## Architectural Decisions (Q1–Q5)

### Q1 — Where adapter code lives

**Decision:** New module in this repo at `services/granola_ingestion/`. Thin admin/health router at `routers/granola.py`.

**Reasoning:**
- This repo already owns every ingestion path (`/text/clean`, `/upload`, `/batch`, `/listen`, queue admin).
- DBOS, asyncpg, enrichment + cleaner services all already wired.
- The poller is a DBOS scheduled workflow (substrate already initialized via `dbos_lifespan`).
- A separate repo would add operational overhead (deploy, env vars, logging) disproportionate to the ~200-line adapter.

### Q2 — Credential storage

**Decision:** AWS KMS envelope encryption + Postgres `vault` schema (cutting-edge 2026 pattern).

**Architecture:**
- AWS KMS holds one master key (CMK), aliased `alias/eq-user-secrets`. Shared across tenants.
- Each credential row gets its own one-time data encryption key (DEK), generated by KMS via `GenerateDataKey`.
- The DEK encrypts the actual `grn_…` API key using AES-256-GCM.
- The DEK itself is encrypted by KMS and stored alongside the ciphertext.
- `EncryptionContext={tenant_id, provider}` is bound to each ciphertext — KMS will refuse to decrypt with a different context, providing cryptographic per-tenant isolation.
- Audited accessor module `vault.get_granola_key(tenant_id)` with hardcoded allowlist of permitted callers (only the poller).
- Schema separation: `vault` schema is isolated from `public` (business tables) — separate Postgres role for the poller.

**Tables:**

```sql
vault.user_credentials
  id                    uuid PK
  tenant_id             uuid FK → public.tenants.id
  user_id               uuid FK → public.users.id
  provider              text  -- "granola"
  encrypted_api_key     bytea
  encrypted_dek         bytea
  nonce                 bytea  -- for AES-GCM
  config                jsonb  -- {"folder_id": "fol_..."}
  status                text   -- "active" | "revoked" | "error"
  last_polled_at        timestamptz
  last_error            text
  created_at, updated_at, archived_at

  UNIQUE(tenant_id, user_id, provider)
```

Note: `default_account_id` was originally proposed and removed. The account is resolved per-ingestion from attendees, not pre-set on the credential.

**Why not other options:**
- Fernet + env var: too brittle, no audit, no rotation story
- AWS Secrets Manager per user: $0.40/secret/month, scales badly
- Infisical Agent Vault: research preview as of April 2026, too new
- Composio: AI-agent-flavored, overkill
- Per-user CMKs: unmanageable past ~100 tenants

### Q3 — Polling cadence

**Decision:** Every 5 minutes.

**Reasoning:**
- Granola rate limits permit easily (5 req/sec sustained = orders of magnitude more than needed)
- AWS KMS Decrypt costs at 5-min × 3 users = ~$0.08/month. Negligible vs OpenAI per-meeting costs.
- 5 min "feels live" to design partners; 10 min started to feel laggy.
- Lower risk of overlapping poll cycles (a slow 8-min poll on a 10-min schedule is uncomfortably close).
- DBOS scheduled workflow handles cron-style scheduling natively.

### Q4 — Dedup strategy

**Decision:** New table `public.external_integration_runs` that maps Granola note IDs to EQ interaction IDs.

```sql
public.external_integration_runs
  id                       uuid PK
  tenant_id                uuid FK → public.tenants.id
  user_id                  uuid FK → public.users.id
  account_id               uuid FK → public.accounts.id  -- nullable
  provider                 text                            -- "granola"
  external_id              text                            -- Granola's "not_..." ID
  eq_interaction_id        uuid                            -- our EQ UUID (nullable until ingested)
  granola_updated_at       timestamptz                     -- observability only
  ingested_at              timestamptz
  status                   text                            -- "success" | "deferred_pending_account" | "failed" | "skipped"
  error                    text
  created_at, updated_at

  UNIQUE(tenant_id, user_id, provider, external_id)
```

**Why UNIQUE includes user_id:** Each Granola user has their own ID space; `not_xxx` in Alice's account is different from `not_xxx` in Bob's account. Defense in depth.

**Why granola_updated_at is observability-only (not in UNIQUE):** Per Q5 below, we don't re-ingest on Granola edits. The updated_at is logged so we can surface "this note was edited after we ingested" but we never act on it.

### Q5 — Note lifecycle semantics

**Decision:** Snapshot-on-ingest. Do nothing on Granola edit/delete/move events.

**Reasoning:**
- Each ingestion is a snapshot. EQ is source of truth from that moment forward.
- Building reverse-sync ("note removed from folder → archive in EQ") is significant work; not justified at MVP.
- Cleaner mental model for design partners: "What's in the EQ folder gets ingested. Once it's in EQ, you manage it in EQ."
- Aligns with cutting-edge 2026 event-ingestion thinking (events are append-only; downstream is source of truth).

---

## Major Framing Correction (Critical)

Mid-brainstorm I (incorrectly) framed the approval flow as "Granola-flavored." User corrected this. The correct framing:

**The "Pending Approvals" UI is a NATIVE EQ feature, not a Granola feature.**

Whether a pending domain was discovered via cold-inbound email, a WebSocket recording, a Granola transcript, an uploaded audio file, or a future Fireflies/Otter integration — the EQ user just sees: "you have a pending account to approve."

| Granola-specific | NOT Granola-specific |
|---|---|
| Granola backend adapter (poller, KMS decrypt, API client) | The Pending Approvals UI component |
| "Connect Granola" settings page in eq-frontend | `pending_account_mappings` table |
| Credential row with `provider="granola"` | `/queue/{id}/approve` API endpoint |
| | DBOS account-provisioning workflow |
| | Signal queueing logic |
| | `eq-agent-action-core` enrichment integration |
| | Contact + account materialization |
| | Downstream event emission (EventBridge, Kinesis) |

When we later add Fireflies, the only new frontend work will be a "Connect Fireflies" settings page. The Pending Approvals component already does its job.

---

## Account Resolution Architecture (Path 2)

Path 2 is the chosen architecture for account resolution. The adapter inspects attendees BEFORE deciding what to do with each Granola note:

### Step-by-step per Granola note

1. **Pull note from Granola** via `GET /v1/notes/{id}?include=transcript`
2. **Extract attendees** from Granola's `attendees` field (or fall back to calendar matching via existing `TranscriptEnrichmentService` if Granola gave us nothing)
3. **Classify each attendee's domain** using existing `classify_domain()` + `get_tenant_internal_domains()`:
   - PERSONAL (gmail, etc.) → skip
   - INTERNAL (tenant's connected mailbox domains) → skip
   - BUSINESS → eligible for account match
4. **Look up known accounts via DOMAIN** (not email) using `lookup_account_by_domain(tenant_id, domain)`
5. **Branch:**

### Scenario A — At least one business attendee maps to a known account

Pick the best as anchor (heuristic: first-found for MVP; can refine later).

- POST to `/text/clean` (or call its internal Python equivalent directly) with `account_id=<the known account's id>` and `participants=<Granola attendees>`.
- Existing pipeline runs: enrichment → cleaner → Lane 1 publish → Lane 2 intelligence
- For OTHER attendees in the same meeting from unknown business domains, existing `TranscriptEnrichmentService` queues signals (this is existing behavior already in production today)
- Write `external_integration_runs` row with `status="success"`

### Scenario B — Mix of known and unknown business accounts

Same as A. The known account is the anchor; unknown ones become queued signals via existing enrichment service. **No new code path.**

### Scenario C — Only unknown business attendees (no known accounts at all)

- **Do NOT call /text/clean** (the existing pipeline requires a known anchor account)
- Queue a signal via `upsert_queue_entry()` + `insert_signal()` with `interaction_id=NULL` (verified safe — see investigation below)
- Write `external_integration_runs` row with `status="deferred_pending_account"` + note the unknown domain
- User (in their own EQ tenant) sees the queued domain in their Pending Approvals UI
- User clicks Approve → existing DBOS workflow runs:
  - Step 3 calls eq-agent-action-core for company enrichment
  - Step 4 creates the account
  - Step 5 creates the contact (no interaction link, because no interaction yet)
  - Workflow emits events as usual
- **Next poll cycle** (max ~5 min later), Granola adapter re-checks `external_integration_runs` rows with `status="deferred_pending_account"`. For each, it re-queries `lookup_account_by_domain` for the now-known domain.
- If account exists, re-fetch the Granola note (still in the folder), run Scenario A flow, update the row to `status="success"`.

### Scenario D — No business attendees at all (only internal + personal)

- Skip. Log `status="skipped_no_business_attendees"` in `external_integration_runs`.
- Shouldn't happen often (the EQ folder filter excludes personal meetings), but safe default.

### Critical matching rule

The match is **domain → existing account** (via `account_domains` table), NOT email → existing contact. These are different lookups:
- Domain match decides "does an account exist for this person's company?"
- Email match (which happens later inside `TranscriptEnrichmentService._resolve_contact`) decides "does this person already exist as a contact within that account?"
- A new contact on an existing account is handled by the existing find-or-create logic. That's separate from the Path 2 decision tree.

---

## CRITICAL: The `raw_interactions` Trap

User flagged this from prior pain: the transcript pipeline doesn't write `raw_interactions` the way one might assume.

### The facts (verified by code investigation)

- The `interaction_types` table is a FK lookup that constrains what `interaction_type` values are valid.
- `interaction_type="note"` is **NOT** in this lookup table on production (per code comment at `routers/text.py:80-95`).
- When Lane 2 (`IntelligenceService._persist_contact_links`) tries to INSERT into `raw_interactions` with an invalid `interaction_type`, the FK constraint fires and the INSERT silently rolls back via `ON CONFLICT DO NOTHING`.
- The exception handler at `intelligence_service.py:564-570` logs "Contact link persistence failed (non-fatal)" without distinguishing FK errors from other failures — **masking the bug**.
- `interaction_type="meeting"` IS in the lookup table (used by WebSocket `/listen` at `main.py:589,614` and by smoke tests).
- `interaction_type="transcript"` — STATUS UNKNOWN. Investigator could not verify without DB access.

### Why this matters for Granola

If we used `interaction_type="transcript"` (semantically accurate), and that value isn't in the lookup table, every Granola ingestion would:
1. Successfully reach Lane 2
2. Silently fail to write `raw_interactions`
3. Be invisible because of the "non-fatal" log
4. Later, when admin approves a secondary attendee's signal from that meeting, `CHECK_RAW_INTERACTION_EXISTS_SQL` would fail in the workflow

### Mitigation

**Use `interaction_type="meeting"` for Granola transcripts.**

- Proven to work (in active production via WebSocket flow)
- Semantically accurate (Granola transcripts ARE recorded meetings)
- Zero schema migration dependency
- Eliminates the FK landmine risk entirely

### Additional load-bearing details for the future

- Document the FK landmine in code comments where future authors might pick a new `interaction_type`
- Phase-2 enhancement candidate: Replace the bare `ON CONFLICT DO NOTHING` with an explicit FK error catch that logs at ERROR level with a clear message
- Anyone considering adding new `interaction_type` values to the codebase: VERIFY the lookup table contents first

---

## Investigation Findings Summary

Three separate investigations were run during this brainstorm. Cross-cutting summary:

### Investigation 1 — Existing account-resolution infrastructure (verified what exists)
- Domain classification: exists, works
- `lookup_account_by_domain`: exists, works
- Signal queueing (`upsert_queue_entry` + `insert_signal`): exists, works, generic
- DBOS account-provisioning workflow: exists, provider-agnostic, no code changes needed for Granola
- `pending_interactions` table: exists for emails only; NO equivalent for transcripts (this asymmetry is why Path 2 uses "defer ingestion + re-poll" rather than "park transcript body")

### Investigation 2 — Frontend queue UI in eq-frontend
- Admin-only "Email Pipeline" page exists at `/dashboard/organization/email-pipeline` but is not what we want to extend
- Email Pipeline page is for org admins; not the right surface for design-partner workflow
- Need to build: a fresh, minimalist "Pending Approvals" component on a page the user actually visits (EQ-native, not Granola-specific)

### Investigation 3 — No-anchor signal path + raw_interactions actual behavior
- **No-anchor signal path (Scenario C) WORKS.** Schema allows `interaction_id=NULL` on signals. Workflow's `materialize_signals` step has explicit guard at `materialization.py:761`: `if s.interaction_id is not None:`. Contact created, link skipped.
- **`raw_interactions` FK landmine confirmed.** See dedicated section above.

---

## Build Scope Summary

### Backend (live-transcription-fastapi) — most work

| Component | Effort |
|---|---|
| Granola adapter module (`services/granola_ingestion/`) | ~2 days |
| - Poller (DBOS scheduled workflow, 5-min cadence) | |
| - Granola API client | |
| - Vault accessor (decrypt API key via KMS at poll time) | |
| - Attendee classification + account resolution | |
| - Scenario A/B/C/D branching | |
| - Re-poll mechanism for deferred ingestions | |
| New tables + Prisma migrations | ~0.5 day |
| - `vault.user_credentials` | |
| - `public.external_integration_runs` | |
| AWS setup | ~0.5 day |
| - Create KMS CMK (alias `alias/eq-user-secrets`) | |
| - Create `eq-vault-service` IAM user with least-privilege KMS policy | |
| - Set new access keys in Railway env vars | |
| Vault encryption module (Python) | ~0.5 day |
| Tests | ~1 day |
| **Total backend** | **~4-5 days** |

### Frontend (eq-frontend) — small work

| Component | Effort |
|---|---|
| Connect Granola settings page (paste API key, pick folder, status) | ~1 day |
| Pending Approvals UI component (EQ-native; benefits all sources) | ~1 day |
| **Total frontend** | **~2 days** |

### DBOS Workflow — zero work

Existing `services/account_provisioning/workflow.py` is provider-agnostic. Handles transcript signals identically to email signals. **No changes needed.**

### Total

**~6-7 days** of focused engineering work, split across backend + frontend.

---

## AWS Account State (Audited 2026-05-22)

- Account ID: `211125681610`
- Root: MFA enabled (iPhone), no access keys — clean
- IAM user `peter-admin-cli`: AdministratorAccess, currently in use by both CLI and AWS MCP — clean
- 7 existing users (5 service accounts, 1 admin = peter, 2 possibly stale)
- 1 custom KMS key (`kmsKeyv1` from June 2024, experimental — unrelated to this work) + AWS-managed keys for various services
- No further AWS hygiene needed before build

**Post-implementation housekeeping (Task #7, non-blocking):**
- Add MFA to `peter-admin-cli` console (5 min, manual)
- Investigate `aws iam get-access-key-last-used` on stale-looking keys: `flowise-v1` (~21 months old), `s3-EQDev1` (~20 months old)
- Optionally rotate `peter-admin-cli`'s 11-month-old access key

---

## Open Questions (Next Session)

### Q6 — Granola Connect Settings Page UX

The fresh UI we'll build in eq-frontend. To discuss:
- Form layout: paste API key field + folder picker + status panel
- "Save & Test" flow: synchronously call Granola `/v1/notes?page_size=1` before storing the key
- Folder auto-population: after key validates, call `/v1/folders` and present as dropdown
- Status display: "Connected. Last polled X min ago. N transcripts ingested today. M deferred awaiting account approval."
- No "view key" button after onboarding (per security best practice; show last-4 only)
- Disconnect / rotate-key affordances

### Q7 — Error handling

- Granola API outage / 5xx → retry strategy with exponential backoff?
- API key revoked (401 from Granola) → mark credential `status="revoked"`, surface to user
- Folder deleted on Granola side → `status="error"`, surface to user
- Rate limit hit → backoff/wait
- KMS decrypt failure → critical alert
- Race condition: same domain has multiple deferred Granola notes from same poll cycle
- Poll cycle takes longer than 5 minutes → overlap handling

### Q8 — Envelope labels

- `source="granola"` (clear)
- `interaction_type="meeting"` (per FK landmine mitigation above — load-bearing decision)
- `extras` content: what Granola-specific metadata to propagate? Candidates: `granola_note_id`, `granola_web_url`, `granola_folder_name`, `granola_summary_text` (for downstream LLMs)
- Any other envelope metadata to thread through?

---

## Next Session Plan

1. Read this doc (~5 min)
2. Walk through Q6, Q7, Q8 with user (~45-60 min)
3. Optional: Codex consult on the overall design (~10 min)
4. Write formal implementation plan as `tasks/granola-integration-plan.md` (~45 min)
5. End that session with a clean plan ready to execute

## Sessions After That

1. **Backend build session** — adapter module + new tables + AWS setup
2. **Frontend build session** — Connect Granola settings page + Pending Approvals component
3. **End-to-end test session** — Peter as design partner #0, real meeting → folder → ingestion
4. **Onboard design partners #1, #2, #3** — share API key + folder setup instructions

---

## What NOT To Do

Documented to prevent relitigation or repeating mistakes:

- **DO NOT use `interaction_type="note"` for Granola** — will trip the FK landmine (silent rollback of raw_interactions INSERT)
- **DO NOT use `interaction_type="transcript"` without verifying it's in the `interaction_types` lookup table first** — see FK landmine section above
- **DO NOT extend the existing `/dashboard/organization/email-pipeline` admin page** — build a fresh, minimalist Pending Approvals component on a page the user actually uses
- **DO NOT try to mirror Granola lifecycle events** (edits, deletions, folder moves) — snapshot-on-ingest semantics; reverse-sync is Phase 2
- **DO NOT reuse the existing transcript pipeline's "anchor account_id required upfront" pattern for Granola** — use Path 2 (resolve account from attendees, defer ingestion if no known account exists)
- **DO NOT try to match email → contact to decide account resolution** — match by DOMAIN against `account_domains`
- **DO NOT use root AWS credentials** — `peter-admin-cli` IAM user is already in use; create a separate `eq-vault-service` IAM user with least-privilege KMS-only policy for the Railway runtime
- **DO NOT store API keys in plain env vars** — use the `vault.user_credentials` table with KMS envelope encryption
- **DO NOT bypass the audited vault accessor module** — every credential read should be logged and gated by an allowlist of permitted callers (only the Granola poller)
- **DO NOT modify the existing DBOS account-provisioning workflow** — it's already provider-agnostic and handles transcript signals correctly. Don't touch it.
- **DO NOT trust comments in code over actual code behavior** — the brainstorm uncovered contradictory comments about raw_interactions; the actual code revealed the FK landmine

---

## Rejected Alternatives (with reasoning, to prevent relitigation)

- **Use Granola's MCP server instead of polling API:** MCP is designed for ad-hoc agent pulls, not 24/7 backend ingestion. Wrong tool.
- **Reverse-engineered Granola backend API:** Repo was archived Feb 5, 2026 explicitly because the official API obsoleted it. ToS-grey, unsupported.
- **Local desktop SQLite scrape:** Per-machine, depends on stable on-disk format, doesn't work for a server-side backend.
- **Granola webhooks (Zapier):** Works but adds dependency + cost. Polling is fine at this scale.
- **Fernet + env var for credential storage:** Single key, no rotation story, no audit. User said "no shortcuts."
- **AWS Secrets Manager per user:** $0.40/secret/month — scales badly. Wrong tool for per-user secrets.
- **Per-tenant KMS CMKs:** Unmanageable past ~100 tenants. Use single CMK with EncryptionContext.
- **Infisical Agent Vault:** Released April 22, 2026 (research preview). Too new to bet on.
- **Composio:** AI-agent-flavored vault SaaS, vendor lock-in, overkill.
- **HashiCorp Vault:** Operationally heavy for 3-user MVP.
- **A "Default account" on the credential row:** Conflated "what account is the Granola owner's organization?" (doesn't exist in EQ's data model) with "what account is each meeting about?" (resolved per-ingestion from attendees). Removed.
- **Building a `pending_transcripts` table:** Considered as Path 1 alternative. Would require extending DBOS workflow's promotion step. Path 2's "defer ingestion + re-poll" gives most of the benefit with far less work.
- **Forcing all transcripts through the same UI as the admin Email Pipeline page:** Wrong layer of abstraction. The Pending Approvals UI is EQ-native; Granola is one of N sources.

---

## Key Locked Decisions (for the LOCKED-N tally)

Building on Phase 1's 22 LOCKED decisions, this brainstorm adds (pending the rest of the brainstorm + the formal plan):

- **LOCKED-23 (pending Q6-Q8):** Granola adapter lives in `services/granola_ingestion/` inside live-transcription-fastapi.
- **LOCKED-24 (pending):** Credentials use AWS KMS envelope encryption + `vault.user_credentials` Postgres table with per-tenant `EncryptionContext`.
- **LOCKED-25 (pending):** Granola transcripts ingest with `interaction_type="meeting"` (not "transcript" or "note") to avoid the FK landmine.
- **LOCKED-26 (pending):** Path 2 architecture for account resolution: known account → ingest with anchor; no known accounts → defer + re-poll.
- **LOCKED-27 (pending):** Snapshot-on-ingest semantics for Granola lifecycle (no reverse-sync).
- **LOCKED-28 (pending):** Polling cadence = 5 minutes.
- **LOCKED-29 (pending):** External integration dedup via `public.external_integration_runs` table; UNIQUE on `(tenant_id, user_id, provider, external_id)`.

To be formalized when the implementation plan is written.
