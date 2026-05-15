# Contact Quality and Account-Anchoring Initiative — Design

**Date:** 2026-05-12 (revised 2026-05-13; durability machinery re-revised 2026-05-15 to reflect DBOS substrate)
**Status:** **Design approved** — revised per Codex review on 2026-05-13. All 5 CRITICAL, 7 IMPORTANT, and 3 NIT findings from `2026-05-12-contact-quality-initiative-codex-review.md` are integrated. **2026-05-15 update:** Phase 1.5's durability machinery (Sections 7.2 + 8.5) re-revised to reflect the DBOS substrate decision from the architecture rethink (see `docs/superpowers/specs/2026-05-15-async-orchestration-rethink-brief.md` + checkpoint `phase-1.5-rethink-decided-dbos`). Implementation plan: `docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md` (supersedes the Phase 1.5 main scope of `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md`).
**Phase 1 scope decision (2026-05-13):** Option A locked — Phase 1 stops creating unknown-domain contacts entirely. The hard rule on account anchoring is enforceable from Phase 1 ship. See Section 7.1 for the three-state branching contract.
**Owner repos affected:** live-transcription-fastapi, eq-email-pipeline, eq-structured-graph-core, action-item-graph, eq-frontend, eq-agent-action-core
**Primary reference doc:** `docs/contacts-architecture.md` (current-state architecture)
**Related memory:** `~/.claude/projects/-Users-peteroneil-live-transcription-fastapi/memory/` (project context, including `project_transcript_enrichment.md` and `reference_contacts_architecture.md`)

---

## 1. Executive Summary

This initiative imposes data-quality discipline on the contact and account layer of our AI-native customer intelligence platform. It enforces two hard rules (no contact or interaction without an account anchor), introduces two soft-requirement mechanisms (an identity state machine and progressive enrichment via async worker), and operationalizes the rules through a shared approval queue that spans both the email and transcript ingestion pipelines. The initiative leverages existing infrastructure where possible — most notably the `eq-agent-action-core` enrichment pipeline that already creates AI-researched accounts in production for the user's own org during onboarding.

The work is structured as four phases. **Phase 1** tightens the account-anchoring contract end-to-end: every ingestion path either resolves `account_id` at the request boundary or rejects the request, per-attendee domain resolution replaces the current uniform-anchor-application behavior, unknown business domains are captured in the approval queue, and the schema/types/signatures across the codebase converge on `account_id` being required. **Phase 1.5** processes those queue entries asynchronously via a DBOS-orchestrated workflow (decided 2026-05-15 after an architecture rethink — see Section 7.2): when an approval comes in, the workflow calls `eq-agent-action-core` for company research, materializes the account + contacts atomically, and emits `EnvelopeV1.*` events to EventBridge for downstream consumers. DBOS's Postgres-as-durability log (workflow + step checkpoints in the `dbos.*` schema on our existing Neon database) replaces the polling-worker + outbox-publisher pattern, eliminating the separate worker container, the RabbitMQ-or-equivalent message bus, and the application-side outbox table. After Phase 1.5 ships and is validated in production, the project hits an explicit **stopping point** for comprehensive re-planning before any commitment to **Phase 2** (identity state machine and progressive enrichment) or **Phase 3** (advanced policies: conflict resolution, multi-account history, fuzzy matching). Phase 2 and 3 are documented for architectural coherence; they will be built on the same DBOS substrate.

The committed scope (Phase 1 + 1.5) closes out the hard requirement on account anchoring end-to-end, fixes known correctness bugs in the transcript pipeline (including the silent-misattribution-to-anchor behavior), operationalizes the queue-based approval workflow, and lands the durability machinery needed for AI-native systems where Postgres and Neo4j must converge under failures. Throughout, the design is grounded in emerging AI-native patterns (GraphRAG-style account-centric graphs, agentic identity resolution, durable execution as the runtime for AI workflows) rather than legacy CRM patterns.

## 2. Background

A prior initiative (transcript contact enrichment, completed 2026-04-01) built and deployed the cross-service infrastructure for resolving meeting attendees to canonical contacts via calendar matching, persisting them to Postgres, propagating canonical IDs through EventBridge envelopes, and MERGEing Contact nodes in Neo4j. That initiative made the participation graph real and operational. It did not, however, enforce quality constraints — most fields on the contact record remain optional, account anchoring is permitted to be NULL, and there is no mechanism for resolving unknown business domains automatically.

Investigation across this project's discovery sessions surfaced that:

- The `lookup_account_by_domain()` capability exists in `eq-email-pipeline` but is wired into only one of three contact-creation paths (the email orchestrator), leaving the calendar_sync path and the transcript pipeline to produce contacts with NULL `account_id`.
- The transcript pipeline's WebSocket endpoint hardcodes `account_id=None` in at least two locations, bypassing the frontend's account-anchoring intent.
- The transcript pipeline applies the meeting's anchor account_id to all attendees uniformly, which falsely attributes external-org attendees (e.g., a partner consultant on a customer call) to the meeting's anchor account.
- The `eq-agent-action-core` service has a complete, production-deployed account-creation pipeline using Tavily web research and Claude-driven `AccountProfile` generation. It is actively used by the onboarding flow but is not invoked by any pipeline on behalf of unknown business domains discovered during ingestion.
- An admin UI route at `/dashboard/organization/email-pipeline` exists with Map / Create / Ignore actions on a pending-domains queue, but the route is admin-prototype scaffolding (not part of the production product UX), the Create action sets a status flag without invoking the agent, and the queue itself is populated only by the email pipeline (not transcripts).
- The `pending_account_mappings` data model exists but lacks `owner_user_id` for per-user scoping and lacks expiry/lifecycle fields.
- 99.3% of Neo4j Interaction nodes (1,017 of 1,024 in test data) lack a `BELONGS_TO→Account` relationship, reflecting both the test-data seeding pattern AND the absence of enforcement.

All current data is test data and will be wiped as part of the migration, simplifying schema changes considerably.

## 3. The Two Hard Rules

These are non-negotiable invariants the system must enforce going forward. Everything else hangs off them.

### 3.1 Hard Rule 1 — No contact exists without an account anchor

A contact entity is never created in Postgres or Neo4j unless it can be tied to an account. The only exception is the transient queue-resolution window: when a new domain triggers auto-account-creation, the contact data is captured by the queue entry, and the contact and account materialize atomically on user approval. Outside this narrow window, no NULL-`account_id` contacts exist anywhere in the system.

**Schema-level consequences:**

- `contacts.account_id` becomes NOT NULL (enforced during Phase 1.5 schema migrations, with the test-data wipe acting as the migration boundary — see Section 7.2 for timing)
- The unique constraint on `(tenant_id, email)` remains, ensuring deduplication
- Find-or-create behavior is updated to enforce account presence at insert time

### 3.2 Hard Rule 2 — No interaction is persisted without an account anchor

Same principle applied to interactions. The plan formalizes the rule end-to-end: every ingestion path must either resolve `account_id` at the request boundary or reject the request. Frontend behavior is not relied upon as an invariant — backend rejection is the enforcement mechanism. Interactions without a resolvable account either succeed via the auto-account-creation flow (Phase 1.5), get held in queue context for resolution, or are explicitly rejected with a logged reason. They do not silently persist as orphans.

**Backend rejection contract (the new invariant):**

The backend rejects any request that lacks a resolvable `account_id`, with exactly one exemption — the queue-hold path (Phase 1.5+), in which the request is accepted but the resulting contact data lives in `pending_account_mappings` until owner approval rather than in `contacts`/`raw_interactions`. There is no "frontend enforces it" gap. Specifically:

- `RequestContext.account_id` becomes required (not `Optional[str]`) for all auth contexts associated with ingestion endpoints. Backend explicitly rejects requests where the JWT-derived or header-derived account_id is absent.
- `EnvelopeV1.account_id` becomes required (not `Optional[str]`) at validation time. Any path that constructs an envelope without account_id fails at construction, not silently downstream.
- `process_transcript(account_id=...)` becomes a required positional/keyword argument (no default `None`). Compile/runtime fails for any caller that omits it.
- `UploadJob.account_id` becomes required at INSERT time. Job creation rejects jobs without a resolvable account.

**Ingestion paths that must be tightened (the complete list — see Section 7.1 for per-path implementation):**

1. WebSocket `/listen` — `main.py:271` (auth context construction) and `main.py:469` (envelope construction), plus `main.py:491` (process_transcript call)
2. Batch `/batch/process` — `routers/batch.py:236` (process_transcript call)
3. Upload async `/upload/init` → `/upload/complete` — `routers/upload.py:156` (job creation) and `routers/upload.py:508` (process_transcript call)
4. Text `/text/clean` — `routers/text.py` (entire path; `TextCleanRequest` at `models/text_request.py:12` has no account_id concept today)

**Schema-level consequences:**

- `raw_interactions` gets an `account_id` column (currently absent), populated at ingestion, NOT NULL after migration
- WebSocket and all other ingestion paths that previously accepted `account_id=None` are corrected (no more hardcoded `None`; instead, the request is rejected at the auth-context boundary if account_id cannot be resolved)
- The `EnvelopeV1` schema's `account_id` becomes required at validation time
- `UploadJob.account_id` becomes required (currently `Optional[str]` at `models/job_models.py:81`)
- `RequestContext.account_id` becomes required for ingestion auth contexts; `X-Account-ID` header becomes required (currently optional per `utils/context_utils.py:253`)
- `IntelligenceService.process_transcript()` signature drops `account_id: Optional[str] = None` in favor of `account_id: str` required positional

### 3.3 What these rules do NOT enforce

- Name on contacts is NOT a hard requirement (covered by the soft mechanisms in Section 4)
- WORKS_FOR Neo4j edges are produced as a consequence of the account anchor existing — they're not separately enforced
- Cross-tenant isolation is a separate hard rule that already exists in the codebase and remains unchanged

## 4. The Two Soft-Requirement Mechanisms

The hard rules set the floor. The soft rules govern the quality dimension on top of that floor — specifically, how the system handles contacts that have email and account but lack a confident name.

### 4.1 Identity state machine

Every contact carries an explicit identity-completeness state. The state determines how the contact behaves downstream — whether it surfaces in a given UI surface, how scoring algorithms weight it, whether it needs re-enrichment.

**State gradient (proposed; final names locked during plan-writing):**

- `shell` — email + account, no name, no enrichment attempted yet
- `emerging` — email + account, no name, enrichment tried but failed/inconclusive
- `partial` — email + account + name with medium confidence
- `resolved` — email + account + name with high confidence
- `verified` — human-confirmed

**Transition logic:**

- At creation time, initial state is set based on signal quality (calendar `display_name` → `resolved`; high-confidence email heuristic → `partial`; nothing resolvable → `shell`; after 3-tier resolution attempt fails → `emerging`).
- Async enrichment can promote `shell`/`emerging` → `partial`/`resolved` when later signal arrives (LLM extraction from new transcript content, repeat Tavily lookup with additional context, related-contact graph inference).
- Any state can transition to `verified` only via explicit human action — the system never claims certainty about a person's identity without a human signal.
- The system does not downgrade contacts. Once a contact reaches `resolved`, new low-confidence signals don't pull it back. Prevents thrashing.

### 4.2 Progressive enrichment via async worker

A scheduled background job runs periodically (cadence configurable; likely hourly or daily). For each contact in `shell`, `emerging`, or `partial` state, it:

1. Re-examines available signal: new transcripts mentioning a name, new emails with email signatures, related contacts in the same account suggesting a pattern.
2. Re-runs LLM-based name extraction against accumulated context (broader than what was available at first ingestion).
3. Optionally re-invokes Tavily with enriched query (e.g., "John at AcmeCorp" instead of just `j.smith@acme.com`).
4. Updates state if enrichment succeeds. Logs the attempt either way (so we know what's been tried).

The worker is rate-limited and budget-aware (Tavily lookups have cost, LLM calls have cost). All attempts are logged in a `contact_enrichment_attempts` table to support retry logic and observability.

### 4.3 Per-surface gating philosophy

The state on the contact is the source of truth, but every UI/consumer surface decides its own threshold for what to show. There isn't ONE global "show partial contacts" switch — each surface makes its own choice based on what's useful in that context. This is what differentiates a graph-native system from a record-based CRM: the same contact can be visible-and-useful in one view and appropriately hidden in another, without changing the underlying data.

**The backend is designed from day one to support this** — gating thresholds are configurable per consumer/surface, not hardcoded in any single layer. The specific UI behavior (which surfaces show what, what visual treatment partial contacts receive) is out of scope for this backend initiative; what the backend guarantees is that the per-surface configuration capability exists.

**Examples of expected gating behavior** (informational only — UI design is a separate effort):

- Main contacts list / global search: fully-formed contacts only
- Account/interaction/opportunity detail pages: all states including partial, with surface-side visual distinction
- Dedicated review queue: partial contacts only, prompts for human completion
- Internal graph queries: all states (participation signal is preserved completely)
- Scoring algorithms: weighted by state

### 4.4 What these mechanisms do NOT do

- They don't auto-promote to `verified`. Verification requires human action.
- They don't downgrade contacts. Once promoted, contacts don't regress.
- They don't handle conflict resolution (different name on file vs. incoming). That's Phase 3 territory.

### 4.5 Schema-level consequences (Phase 2)

- `ContactValidationStatus` enum and the `pending_validations` table are formally deprecated. Phase 2 introduces `ContactIdentityState` with the five values above as the replacement; Phase 1 + 1.5 leave the old enum/table in place but Phase 1 stops using `validation_status='pending'` as a marker for unknown-account contacts (no such contact exists under Option A). See Section 7.4 for the explicit schema-debt position. The migration is clean because the test-data wipe gates the constraint-tightening.
- `contacts.identity_state` column with the new enum, NOT NULL, defaults to the appropriate state at INSERT.
- Optional `contacts.identity_confidence` numeric column (0.0-1.0) for finer-grained gating.
- A `contact_enrichment_attempts` table to log enrichment runs.

## 5. The Shared Identity-Resolution Surface (Approval Queue)

When the system encounters a person from a company we don't yet know about, it neither silently creates a half-formed account nor blocks the ingestion. It surfaces the situation to the right user (the queue item's owner — typically the user whose ingestion triggered it) and asks "should we create this account?" The user reviews and approves; the existing `eq-agent-action-core` agent does the AI-driven research and creates the account. The participation that was waiting in the queue gets attached automatically.

This same queue mechanism handles unknowns from any source — emails, transcripts, calendar invites, future ingestion paths — funneled into a single unified review surface scoped per user.

### 5.1 Why a unified queue across pipelines

Conceptually it's the same problem (an unknown business domain needs an account). Sharing the queue avoids duplication, presents a single unified view to the user, and keeps dedup logic clean (the same domain showing up via email AND transcript should be one queue entry, not two). The schema generalization is small; the architectural payoff is large — the queue becomes a *shared identity-resolution surface* for the entire system.

### 5.2 Schema additions to `pending_account_mappings` and new signals join table

**`pending_account_mappings` row-level fields (one row per `(tenant_id, domain)`):**

Phase 1 adds these columns (needed at queue-insertion time):

- `owner_user_id` — UUID, references users; populated at queue insertion time, **never reassigned** by routine UPSERT (first-owner-wins under concurrency; see UPSERT semantics below). Future-proofed for tier-based approval (the authorization check is encapsulated in a single function/policy, allowing future expansion to tier leaders).
- `discovered_from_type` — enum (`email | transcript | calendar | manual`) for the **first** signal that created the entry. Subsequent signals from other source types are tracked in the signals join table, not by mutating this field.
- `discovered_from_interaction_id` — nullable UUID; populated from the **first** signal only. Subsequent interaction associations live in the signals join table.
- `expires_at` — timestamp; populated on insert per the configured expiry policy, and refreshed on every new signal (sliding window).
- `archived_at` — nullable timestamp; populated when the entry expires unactioned or is reached via owner Ignore.
- `archive_reason` — nullable enum (`expired_no_activity | owner_ignored | tenant_resolved_other_way`).
- `re_open_count` — integer, default 0; incremented when a new signal arrives on an `archived` or `ignored` entry (see Section 5.9 for re-open semantics).
- `last_reopened_at` — nullable timestamp; set when `re_open_count` is incremented.

Phase 1.5 adds these additional lifecycle columns (needed by the worker and the Approve/Map/Ignore actions):

- `approval_attempt_id` — nullable UUID; idempotency key for the Approve action (Section 5.4 step 2). Set when the row transitions to `status='approved'`; preserved on subsequent retries.
- `creation_started_at` — nullable timestamp; set when the worker transitions `approved → creating` (Section 5.4 step 4).
- `mapped_at` — nullable timestamp; set when the worker transitions to `mapped` (Section 5.4 step 7).
- `ignored_at` — nullable timestamp; set on Ignore action.
- `ignored_by` — nullable UUID; user_id of whoever invoked Ignore.

The `status` enum (existing in the email-pipeline schema today) is extended in Phase 1.5 with the new values used by the worker: `approved`, `creating`, `tenant_review`. Phase 1 only writes `pending` (queue insertion only; no lifecycle transitions until Phase 1.5 ships).

The dedup constraint: `(tenant_id, domain)` is unique. The existing `email_count` field is removed in favor of the signals join table (it cannot represent multi-source evidence faithfully and lacks per-source archival columns).

**New table `pending_account_mapping_signals` (one row per signal contribution):**

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | PK |
| `tenant_id` | UUID | Tenant isolation |
| `queue_id` | UUID | FK to `pending_account_mappings.id` |
| `source_type` | enum (`email | transcript | calendar | manual`) | Signal origin |
| `source_user_id` | UUID | User whose ingestion contributed this signal |
| `interaction_id` | nullable UUID | Set for transcript and email signals |
| `calendar_event_id` | nullable UUID | Set for calendar signals |
| `contact_email` | VARCHAR(255) | The attendee/correspondent email this signal represents |
| `contact_display_name` | nullable VARCHAR(255) | Name signal if available |
| `contact_role` | nullable VARCHAR(50) | `organizer | attendee | sender | recipient` |
| `created_at` | TIMESTAMPTZ | Signal arrival time |
| `archived_at` | nullable TIMESTAMPTZ | Set when this signal is no longer load-bearing (parent queue entry archived, or signal individually deduped/removed) |

Unique constraint: `(queue_id, contact_email, source_type, interaction_id, calendar_event_id)` — prevents duplicate signal insertion under retry. Index on `(tenant_id, queue_id, archived_at)` for queue UI queries.

**UPSERT semantics under concurrency:**

Two ingestion paths can simultaneously discover the same unknown domain with different owners, interaction IDs, and source types. The semantics are:

1. **Parent row insert is `INSERT ... ON CONFLICT (tenant_id, domain) DO UPDATE SET expires_at = GREATEST(expires_at, EXCLUDED.expires_at), updated_at = NOW() RETURNING id`.** The first INSERT wins for `owner_user_id`, `discovered_from_type`, and `discovered_from_interaction_id`. The loser's data does NOT mutate the parent row.
2. **Signal row insert is unconditional** — every ingestion that touches the domain inserts a row into `pending_account_mapping_signals` referencing the (now-known) parent `queue_id`. Duplicates are prevented by the unique constraint; idempotent under retry.
3. **Owner is never reassigned** by routine UPSERT. Owner change is possible only via explicit re-open escalation policy (see Section 5.9) or admin action (out of V1 scope).
4. **Signal count is `SELECT COUNT(*) FROM pending_account_mapping_signals WHERE queue_id = ? AND archived_at IS NULL`** — derived, not stored. Removes the data-integrity risk of an `email_count` field drifting under concurrent writes.

This makes the queue race-safe by construction: the parent row carries the owner and lifecycle state; the signals table carries the evidence; no field is mutated under concurrent insertion except the sliding `expires_at`.

### 5.3 Owner determination rules (locked)

Owner is determined deterministically by the ingestion path. The rule is locked, not deferred to plan-writing.

- **Email pipeline:** owner is the user whose connected `provider_connection` sent or received the triggering email (i.e., `provider_connections.user_id` for the connection that surfaced the email).
- **Transcript pipeline:** owner is the **user who initiated the recording** — the authenticated `pg_user_id` on the WebSocket/upload/batch/text request. This is invariant: even if the anchor account is owned by a different user in the same tenant, the queue entry's owner is the recording user. Rationale: the recording user is the one with context about why the meeting happened and which attendees are relevant; the anchor account's owner may be the person who originally created the account but is not necessarily involved with the meeting.
- **Calendar pipeline (eq-email-pipeline calendar_sync):** owner is the user whose `provider_connection` surfaced the calendar event (i.e., the connected calendar account that contains the event).
- **Manual additions (future scope):** owner is the user who initiated the manual action.

**Owner under concurrency:** first owner wins (see Section 5.2 UPSERT semantics). If user A's transcript discovers `acme.com` first, A is owner. If user B's email arrives 10 seconds later from `bob@acme.com`, B's signal is appended to the same queue entry but A remains owner. B becomes visible in the queue entry's signals table; B does not become owner. Re-open and escalation are covered in Section 5.9.

**Owner authorization scope:** only the owner can act (Approve/Map/Ignore) on the entry in V1. The authorization check is encapsulated in a single helper (`can_act_on_queue_entry(user_id, queue_entry)`) so future tier-based extension is a one-place change.

### 5.4 Queue lifecycle (with outbox-backed event durability)

**Approval workflow (with idempotency boundaries explicit at every step):**

1. User reviews queue (scoped to items they own per Section 5.3 + 5.9).
2. User clicks Approve on an entry. The frontend POST carries an **idempotency key** (`approval_attempt_id`) so duplicate clicks/network retries are deduplicated. The eq-frontend route transitions the queue entry from `status='pending'` to `status='approved'` and writes `approval_attempt_id` to the row; if the row already has an `approval_attempt_id`, the route returns success without state change.
3. Worker polls (or is event-triggered) on `status='approved'` entries. Worker takes an advisory lock (`pg_try_advisory_xact_lock(queue_id_hash)`) so two worker instances cannot process the same entry concurrently. If the lock is taken, the worker skips and retries on the next tick.
4. Worker transitions `status='approved' → status='creating'` and records `creation_started_at`.
5. Worker calls `eq-agent-action-core POST /api/enrich` with the domain, the `queue_id` as a correlation key, and a `worker_attempt_id` as the agent-side idempotency key (so the agent treats duplicate calls for the same `worker_attempt_id` as the same request).
6. Agent runs its 5-step pipeline (URL canonicalization → query generation → Tavily research → reflection → AccountProfile generation) and creates the Account in Postgres atomically. Agent response includes the new `account_id`.
7. Worker opens a **single Postgres transaction** that does all of the following atomically:
   - Materializes contacts for every signal in `pending_account_mapping_signals WHERE queue_id = ? AND archived_at IS NULL`. For each signal, INSERT into `contacts` with the resolved `account_id` and the signal's `contact_email`, `contact_display_name`, `contact_role`. ON CONFLICT `(tenant_id, email)` DO UPDATE to merge any later-arriving name signals.
   - Materializes `interaction_contact_links` for every signal that has `interaction_id IS NOT NULL`.
   - Updates the queue entry to `status='mapped'`, `resolved_account_id = ?`, `mapped_at = NOW()`.
   - Writes a row to the **outbox table** `account_provisioning_outbox` (new — defined below) capturing the `AccountCreated` event payload, the associated contact_ids, the interaction_ids, and the queue_id. This outbox row is the durable event log.
8. After the transaction commits, a separate **outbox publisher** (worker subprocess or scheduled job) reads unpublished outbox rows, publishes the `AccountCreated` event to EventBridge, and marks the outbox row `published_at = NOW()`. If publish fails, the outbox row remains unpublished and is retried on the next publisher tick.
9. eq-structured-graph-core consumes the event. Because the consumer MERGEs in Neo4j by `(tenant_id, account_id)` and by `(tenant_id, contact_id)` (already the established MERGE-everywhere pattern), duplicate event delivery is idempotent: a re-delivered event MERGEs the same nodes/edges with no side effects.

**Rejection workflow (Ignore):**

- Status set to `status='ignored'`, `ignored_at = NOW()`, `ignored_by = current_user_id`.
- Signals associated with the queue entry are **not** archived. They remain in the signals table so that re-open semantics (Section 5.9) can resurface them if new signals arrive later.
- **No contacts to archive** under Option A — Phase 1 + 1.5 do not create orphan contacts. Section 5.5 covers what archive does and does not affect.

**Map-to-existing-account workflow:**

- When the user maps a queue entry to a pre-existing account (Map action instead of Approve), the worker skips the agent call and uses the chosen `account_id` directly. The rest of the transaction (signal materialization, outbox row, event publish) is identical.
- `status` transitions: `pending → mapped` (skipping `approved` and `creating`). `resolved_account_id` is set to the chosen account, not a newly-created one.

**New table `account_provisioning_outbox` (the durable event log):**

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | PK |
| `tenant_id` | UUID | Tenant isolation |
| `queue_id` | UUID | FK to `pending_account_mappings.id` |
| `event_type` | enum (`account_created | account_mapped`) | Event kind |
| `account_id` | UUID | The materialized account |
| `payload_json` | JSONB | Full event payload (account fields, contact_ids, interaction_ids, source signals summary) |
| `created_at` | TIMESTAMPTZ | Row creation time (same transaction as account/contact materialization) |
| `published_at` | nullable TIMESTAMPTZ | Set when the publisher emits to EventBridge |
| `publish_attempts` | INTEGER | Default 0; incremented on each publish attempt for observability |
| `last_publish_error` | nullable TEXT | Most recent publish failure message |

Index: `(published_at NULLS FIRST, created_at)` for the publisher's "find unpublished" query.

**Idempotency contracts (the invariant pair that makes the system replay-safe):**

- Worker → agent: `worker_attempt_id` per worker invocation; agent treats same id as same request.
- Outbox → EventBridge: `outbox_row_id` as the event's idempotency key; consumers MERGE by canonical (tenant_id, account_id/contact_id) keys, making duplicate delivery a no-op.
- Queue Approve action: `approval_attempt_id` from frontend; deduplicated at the queue-row write boundary.

This three-layer idempotency model is why Postgres and Neo4j cannot diverge under failures, and why the worker can be re-run safely.

### 5.5 Expiry policy and archive lifecycle

**Default policy:** Sliding window with a hard cap.

- Each new signal (email, transcript, calendar invite) touching the same domain resets `pending_account_mappings.expires_at` to 30 days from now.
- A hard cap of 90 days from initial `created_at`, regardless of signal activity.
- Expired items are marked `archived` (not deleted) and retain all original context.

**Configurable, not hardcoded.** Expiry windows live in a configuration table or environment variables, allowing tuning without code changes.

**Archive lifecycle — which tables carry archive state (specified, not aspirational):**

Under Option A (Phase 1 + 1.5 never create orphan contacts), the only tables that need archive lifecycle columns are the queue itself and the signals join table.

- `pending_account_mappings.archived_at` — nullable TIMESTAMPTZ. Set when the expiry sweep marks the entry as expired, or when the owner takes the Ignore action.
- `pending_account_mappings.archive_reason` — nullable enum (`expired_no_activity | owner_ignored | tenant_resolved_other_way`). Captures *why* the entry was archived.
- `pending_account_mapping_signals.archived_at` — nullable TIMESTAMPTZ. Set when the parent queue entry is archived (cascade-style on archive, not a true FK cascade — done explicitly in the archive transaction).
- `contacts` and `raw_interactions` **do not gain archive lifecycle** because under Option A they are never created without an account_id. There is no orphan contact or orphan interaction to archive.

**Reader filtering rules (downstream queries must honor):**

- Queue UI: filter `WHERE archived_at IS NULL` for active items; toggle to show archived items separately.
- Signal counts (Section 5.2 derived `signal_count`): always filter `WHERE archived_at IS NULL` so re-opened entries report a fresh signal count.
- Re-open detection (Section 5.9): match incoming signals against `archived_at IS NOT NULL` entries; if found, increment `re_open_count`, set `archived_at = NULL`, set `last_reopened_at = NOW()`, and insert the new signal as an unarchived signal row.
- Expiry sweep query: `SELECT id FROM pending_account_mappings WHERE archived_at IS NULL AND expires_at < NOW()` — daily scheduled job; for each result, set `archived_at = NOW()`, `archive_reason = 'expired_no_activity'`, and mark associated signals archived in the same transaction.

**Archive is non-destructive.** Every archived row retains its data; archived signals retain their email, name, role, interaction reference. Re-open transitions the row back to `pending` with full evidence intact. This preserves the option to resurface the entry later when:

- A new signal (later email or transcript from the same domain) arrives — system re-opens automatically (Section 5.9).
- User later creates an account that matches the archived domain via another flow — system can offer to merge (Phase 2+ scope; data preserved).
- Analytics measure "archived but later relevant" signals to measure queue accuracy.

### 5.6 Backfill semantics — queue-context only, contact materialization on approval

Under Option A (chosen 2026-05-13), Phase 1 + 1.5 never create orphan contacts. The hard rule is enforced from Phase 1 ship: when transcript enrichment or email ingestion encounters an unknown business domain (non-personal, non-internal), the system writes a signal to `pending_account_mapping_signals` capturing the proposed contact data, but does NOT insert into `contacts`. The interaction is recorded with its anchor account; the unknown-domain attendee is not in `interaction_contact_links` until approval.

When the user approves the queue entry (Section 5.4 step 7), the worker executes a single Postgres transaction that materializes everything atomically:

1. **INSERT contacts** — one per unique `contact_email` in the signals table (`WHERE queue_id = ? AND archived_at IS NULL`). Each contact carries the resolved `account_id`. ON CONFLICT `(tenant_id, email)` DO UPDATE merges later-arriving name/role data into already-existing contacts (rare but possible if the queue entry has been re-opened after a previous Map action).
2. **INSERT interaction_contact_links** — one per signal that has `interaction_id IS NOT NULL`, linking the now-materialized contact to the interaction that surfaced it. The signal's `contact_role` is preserved on the link if the schema supports it.
3. **Write outbox row** — the `account_provisioning_outbox` row carries the canonical event payload, including the list of materialized `contact_id`s and `interaction_id`s for downstream consumers.
4. **Mark queue entry mapped** — `status='mapped'`, `resolved_account_id`, `mapped_at = NOW()`.

After commit, the outbox publisher emits the `AccountCreated` event. eq-structured-graph-core consumes the event and MERGEs the corresponding Neo4j Account node, Contact nodes, and `WORKS_FOR` / `BELONGS_TO` / `ATTENDED` / `SENT` / `RECEIVED` edges as appropriate to the source signals.

**Why no domain-pattern-match SQL update:** Codex rightly flagged that `UPDATE contacts SET account_id = ? WHERE email LIKE '%@domain'` is unsafe under multiple queue entries over time, archived/reopened state, or manual Map actions. The signals join table replaces pattern-matching with explicit per-signal evidence. Every materialized contact has a traceable signal lineage in `pending_account_mapping_signals`.

**Signals from multiple users on the same queue entry:** All signals in the table get materialized together on approval. A queue entry that started from a transcript signal by user A and accumulated email signals from user B (Section 5.9 escalation) results in contacts for every signal-distinct email — including signals from both users. The materialized contacts are not user-scoped; they are account-scoped (per the existing `(tenant_id, email)` uniqueness).

### 5.7 Personal-domain handling

Personal email domains (gmail.com, outlook.com, hotmail.com, etc.) are skipped entirely by automated ingestion paths. No contact is created, no participation edge is stored, no queue entry is generated. This applies to both the email pipeline and the transcript pipeline.

**One reserved future path:** the system permits manual creation where a human explicitly attaches a personal-email contact to a known account (e.g., an executive who uses personal email for correspondence with you). This is a manual workflow, not pipeline-driven, and is out of scope for the committed phases — but the data model does not preclude it.

### 5.8 Internal-domain handling

Domains derived from the user's connected `provider_connections` (excluding public email domains) are recognized as internal. Internal participants on a transcript or email are recorded as participants but do NOT trigger account creation — they belong to the user's own organization, not to a customer or partner account.

**Test-data implication:** the primary test tenant uses a personal gmail address as its connected account. For Phase 1.5 testing, we either seed the test tenant with a synthetic business domain in `provider_connections` or use a separate test tenant with realistic internal-domain configuration. To be settled in plan-writing.

### 5.9 V1 approval authority + tenant-level re-open semantics

**Owner-only approval (V1):** Only the queue item's owner (per Section 5.3 — the recording user for transcripts, the connected-provider user for email, etc.) can take Approve/Map/Ignore actions on the entry. The authorization check is encapsulated in `can_act_on_queue_entry(user_id, queue_entry)` so future tier-based extension is a one-place change.

**Tenant-level re-open semantics (closes Codex finding #15):**

The naive owner-only design is a dead-end without a revival policy. Codex's example: user A's transcript discovers `acme.com` → A is owner → A clicks Ignore → 10 days later user B receives an email from `acme.com` → B cannot create the queue entry (dedup violation) and cannot act on A's ignored entry (not owner). Without intervention, `acme.com` is effectively blocked tenant-wide because of one user's action.

Resolution: queue entries are re-openable by new signals. The mechanics:

1. **Trigger:** any new signal (any source, any user in the tenant) arrives for a domain whose queue entry has `archived_at IS NOT NULL`.
2. **Action:** the ingestion path inserts a new row into `pending_account_mapping_signals` (referencing the existing `queue_id`), and in the same transaction:
   - Sets the queue entry's `archived_at = NULL`, `archive_reason = NULL`, `re_open_count = re_open_count + 1`, `last_reopened_at = NOW()`, `status = 'pending'`.
   - Refreshes `expires_at` per the sliding-window policy.
3. **Owner remains unchanged.** The original owner stays attached to the queue entry. This preserves the first-owner-wins invariant from Section 5.3 and avoids ownership churn under repeated re-opens.
4. **Notification (UI scope):** the original owner receives a notification ("Acme.com was re-opened by a new signal from user B; please reconsider"). UI behavior is out of backend scope; the backend exposes the event via the existing notification surface.

**Escalation path for repeatedly-ignored entries:**

- Configuration parameter: `QUEUE_REOPEN_ESCALATION_THRESHOLD` (default: 3).
- When `re_open_count >= threshold`, the queue entry transitions to a new state `status='tenant_review'`. In this state, `can_act_on_queue_entry` returns true for the original owner **and** for any tenant admin user (per eq-frontend's existing admin tier). This is the explicit escape valve — a single ignoring owner cannot block the tenant indefinitely.
- The escalation is a transition, not a deletion. Owner can still act; admin can now also act.
- Re-open count resets to 0 only when the entry transitions to `mapped` (a terminal state). Continued Ignore after escalation increments the count further; the entry remains in `tenant_review`.

**What V1 does NOT include (deferred to Phase 2 re-planning):**

- Owner reassignment via UI (e.g., "transfer to user B"). The data model supports it (single `owner_user_id` column) but the workflow is not in V1 scope.
- Bulk archive/unarchive actions.
- Auto-escalation timers (e.g., "if owner hasn't acted in 14 days, escalate"). Only signal-based re-open is in V1.

**Why this matters for the test-data tenant:** Codex's example is not a hypothetical. The test tenant uses a single user for both transcript and email pipeline today; once we expand to multi-user testing, the re-open semantics are exercised. Phase 1.5 acceptance tests must cover the re-open path.

## 6. Account State Model

Accounts get a minimal extensibility-oriented state column (`active | archived`) for lifecycle reasons unrelated to approval. The "pending approval" state lives in the `pending_account_mappings` queue table, NOT on the account row. The account is created only on approval (atomically by the agent), at which point it enters `active` state.

This is a deliberate asymmetry with contacts. Contacts have a richer state machine because they undergo *progressive enrichment* (multiple stages from email-only to fully-resolved). Accounts are *atomically enriched* by the agent in one call — there's no in-between state for the account itself. Forcing a parallel state machine on accounts would conflate identity completeness (which matters for contacts) with workflow state (which is what account approval is).

The minimal `active | archived` enum leaves room for future expansion (e.g., adding an `unverified` state if a real use case emerges) without overcommitting today.

## 7. Phased Trajectory

### 7.1 Phase 1 — Tighten the contract end-to-end (committed)

**Outcome (in product terms):** The hard rule on account anchoring is operationally enforceable from Phase 1 ship. Every ingestion path either has a resolved `account_id` or rejects the request. Per-attendee domain resolution runs in the transcript pipeline; calendar attendees from known domains get correct account assignment; unknown-domain attendees are captured in the approval queue but do not produce orphan contacts. Personal-domain attendees are filtered out automatically. The API can accept caller-provided participants for manual notes.

**Three-state branching on unknown-domain attendees (the new contract — closes Codex findings #2 and #3):**

When per-attendee domain resolution runs against an attendee's email domain, the result is exactly one of three states. **There is no fallback-to-anchor-account state.** Falling back to anchor was the bug Codex correctly identified — it silently misattributes external attendees (a consultant on a customer call would get attached to the customer's account).

1. **Known account** — `lookup_account_by_domain(tenant_id, domain)` returns an `account_id`. The attendee is added to the interaction normally; contact is created with that `account_id` (find-or-create on `(tenant_id, email)`).
2. **Unknown business domain** — lookup misses, domain is not personal/internal. The attendee is **not** added to `contacts` or `interaction_contact_links`. Instead:
   - Insert/upsert a `pending_account_mappings` row keyed on `(tenant_id, domain)` (per Section 5.2 UPSERT semantics).
   - Insert a `pending_account_mapping_signals` row capturing the attendee's email, display_name, role, and `interaction_id`.
   - The interaction itself is recorded with its anchor `account_id` (the meeting's anchor account); only the unknown-domain attendee skips contact creation.
3. **Personal or internal domain** — domain is in the public-email-domains list (gmail.com, outlook.com, etc.) OR the domain matches one of the tenant's `provider_connections` (excluding public domains, per Section 5.8). Personal and internal domains are skipped entirely — no contact created, no signal queued, no interaction link added.

**Backend rejection (closes Codex finding #10):** every ingestion path validates `account_id` at the auth-context boundary. If the request lacks a resolvable account_id, the request is rejected with `400 Bad Request` (`account_id is required for this endpoint`). The exemption is the queue-hold path only — for transcripts with a resolvable anchor account, unknown-domain attendees go to the queue; for transcripts WITHOUT an anchor account, the request is rejected.

**Technical scope — per ingestion path (closes Codex finding #1 with the complete list):**

- **WebSocket `/listen`** (`main.py:271`, `main.py:469`, `main.py:491`): replace hardcoded `account_id=None` in `RequestContext` construction with `account_id = require_account_id_from_header(request)`. Construct `EnvelopeV1` with `account_id=context.account_id`. Pass `account_id=context.account_id` to `process_transcript()`. If header is missing on WebSocket connection request, reject the upgrade with `1008 (Policy Violation)`.
- **Batch `/batch/process`** (`routers/batch.py:236`): pass `account_id=context.account_id` to `process_transcript()`. Validate `context.account_id` at the route entrypoint via the shared auth-context validator.
- **Upload async `/upload/init`** (`routers/upload.py:89` `UploadInitRequest` + `routers/upload.py:156` `UploadJob` creation): add `account_id: str` to `UploadInitRequest` (currently has only `filename`, `mime_type`, `file_size`). Validate it at request time. Persist `account_id` on `UploadJob` (currently `Optional[str]` at `models/job_models.py:81`; becomes required). The `/upload/complete` flow surfaces the job's `account_id` to the worker via `UploadJob.account_id`; worker passes it to `process_transcript()` at `routers/upload.py:508`.
- **Text `/text/clean`** (`models/text_request.py:12` `TextCleanRequest` + the `/text/clean` route in `routers/text.py`): add `account_id: str` to `TextCleanRequest`. Validate at request time. Pass through to the envelope construction and to `process_transcript()` if intelligence lane runs for this endpoint.

**Caller-provided participants — corrected per Codex finding #9:**

- `TextCleanRequest`: add `participants: Optional[list[ParticipantSpec]]` for manual notes/future workflows.
- `UploadInitRequest`: add `participants: Optional[list[ParticipantSpec]]` — these flow through `UploadJob` (new column `participants_json: Optional[Text]`) to the worker, since the `/init` → `/complete` async job flow cannot accept them at `/complete` time.
- WebSocket `/listen`: participants are not request-model concerns at this endpoint (the WebSocket handshake is query-param-based). Future enhancement only; not in Phase 1 scope.
- Batch `/batch/process`: this endpoint takes raw `UploadFile`, not a Pydantic body. Participants pass via multipart form field `participants` (JSON-encoded) which the route deserializes. Optional; backward-compatible with current callers.

`ParticipantSpec` is `{email: str, display_name: Optional[str], role: Optional[str]}` — minimal shape; wired to the existing `existing_contact_ids` parameter on `TranscriptEnrichmentService.enrich()`.

**Per-attendee domain resolution in transcript enrichment (`services/transcript_enrichment.py:399` area):**

- Replace the current single-account-for-all-attendees behavior with per-attendee `lookup_account_by_domain()`.
- Apply the three-state branching above. **No fallback to anchor.** External-domain attendees who miss lookup go to the queue, not to the anchor account.
- The `validation_status='pending'` code path at `services/transcript_enrichment.py:402` for unknown-account contacts is **removed** in Phase 1 (no contact is created for unknown domains; the queue captures the data instead).

**3-tier name resolution (calendar `display_name` → email heuristic → Tavily):** extracted from `services/transcript_enrichment.py` into a shared utility used by both repos. Called for known-account attendees only (no Tavily spend on queue-pending attendees; they get enriched by `eq-agent-action-core` on approval).

**Personal-domain filter** at all contact-creation entry points: skip creation for domains in the public-email-domains list (canonical list lives in a shared module, used by both pipelines).

**Cross-repo changes (eq-email-pipeline side):**

- `eq-email-pipeline/src/pipeline/calendar_sync.py`: call `lookup_account_by_domain()` before contact creation. Apply the same three-state branching: known account → create contact; unknown business domain → queue signal, no contact; personal/internal → skip.
- `eq-email-pipeline/src/pipeline/orchestrator.py`: confirm the existing email flow follows the same three-state branching for sender/recipient resolution.
- Schema migration on `pending_account_mappings` and creation of `pending_account_mapping_signals` is required in Phase 1 (not Phase 1.5) because the queueing happens here.

**Schema migrations in Phase 1 (pulled forward from Phase 1.5 to support Option A):**

- Create `pending_account_mapping_signals` table (Section 5.2 schema).
- Add `pending_account_mappings.owner_user_id`, `discovered_from_type`, `discovered_from_interaction_id`, `expires_at`, `archived_at`, `archive_reason`, `re_open_count`, `last_reopened_at` columns.
- Add `raw_interactions.account_id` column (NOT NULL after test-data wipe, but the wipe happens at Phase 1.5 schema-tightening cutover; Phase 1 inserts populate it).
- Update `EnvelopeV1.account_id` field to required, `RequestContext.account_id` to required, `process_transcript(account_id)` to required, `UploadJob.account_id` to required at the model layer. The Phase 1 cutover wipes test data before enforcing constraints.

**Dependencies:** None on subsequent phases. Phase 1 is self-contained: it tightens the contract, builds the queueing pathway, and leaves the worker + UI to Phase 1.5.

**Estimated size:** 2-3 weeks of focused work (revised upward from 1-2 weeks because queue insertion logic and signals join table are now in Phase 1 scope, not Phase 1.5).

**What Phase 1 does NOT yet enforce:** Hard rule 1 is enforced (no contact without account anchor). Hard rule 2 is enforced (no interaction without account anchor). What Phase 1 does NOT include is the worker that processes queue entries — that's Phase 1.5. Between Phase 1 ship and Phase 1.5 ship, queue entries accumulate but are not processed; unknown-domain attendees are recorded in the queue but not visible as contacts. This is acceptable because internal testing tenants are the only consumers during the gap, and the gap is intentional — it forces queue UX to be designed alongside the worker rather than retrofitted.

### 7.2 Phase 1.5 — Async orchestration on DBOS (committed; durability machinery revised 2026-05-15)

> **Revision note (2026-05-15):** Section 7.2 originally described a polling-worker + outbox-publisher + separate-publisher-process architecture. After Phase 1 silently regressed for 24h (see `tasks/lessons.md` "Four systemic quality gaps") and the downstream agent-contract gap was discovered (see "Stop and question dated architecture when integration reveals it"), the user paused Phase 1.5 deployment for an architecture rethink. The rethink completed 2026-05-15 with a substrate decision (DBOS) recorded across `docs/superpowers/specs/2026-05-15-async-orchestration-rethink-brief.md`, the durable-execution landscape research, and the checkpoint `phase-1.5-rethink-decided-dbos`. This section now reflects the DBOS architecture; the implementation plan lives at `docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md`.

**Outcome (in product terms — unchanged from the original 2026-05-12 design):** When the user opens the approval queue, they see entries from both email and transcript pipelines, scoped to their ownership. They can Approve, Map to an existing account, or Ignore. On Approve, the AI agent does company research and creates the account in seconds; all queued contacts for that domain materialize atomically. On Map, contacts materialize against the chosen pre-existing account. On Ignore, the entry is archived but re-openable by new signals. The participation graph stays complete; the hard rule on account anchoring is operationally enforced end-to-end. The user watches the workflow's progress with streaming UX feedback (the "watch the AI reason" affordance).

**Substrate (decided 2026-05-15):** **DBOS** (Apache 2.0 / MIT, library-only durable execution; Postgres as the durability layer). Rationale, eliminations, and the Hatchet → DBOS pivot are in the checkpoint and rethink brief; this section does not re-litigate the decision. The relevant operational facts:

- DBOS is a library imported into the same FastAPI process. No new container, no new service, no RabbitMQ, no Helm, no separate worker daemon.
- Workflow + step state is checkpointed to the existing Neon Postgres database under a `dbos.*` schema (created automatically by DBOS on first launch).
- Workflow-level idempotency is provided by application-controlled IDs via the `SetWorkflowID` context manager; per-step retry semantics are configured explicitly per step.
- DBOS provides the Phase 2/3 primitives we'll need (durable sleep, queues, HITL via send/recv/set_event/get_event) without re-architecting.
- V1 ships with one Railway replica running `uvicorn --workers 1`, AND is multi-replica-ready by configuration (`executor_id=os.environ.get("RAILWAY_REPLICA_ID")` in `DBOS.DBOSConfig`). Scaling from 1 → N replicas later is a Railway dashboard change plus shipping the orphan-workflow detector (design + trigger rules in `docs/superpowers/specs/2026-05-15-dbos-scaling-decisions.md`).

**Technical scope:**

- **Schema migration on `accounts`:** add `state` column with `active | archived` enum, NOT NULL, default `active`. (Unchanged from 2026-05-13 design; already applied to Neon eq-dev.)
- **Schema migration on `contacts`:** enforce `account_id` NOT NULL. (Already applied.)
- **Schema migration on `raw_interactions`:** NOT NULL on `account_id`. (Already applied.)
- **NEW (2026-05-15) — schema migration on `interaction_contact_links`:** add UNIQUE INDEX on `(interaction_id, contact_id)`. This replaces the in-memory dedup that the original worker design depended on with SQL-layer replay safety, so the DBOS workflow's materialization step can use `ON CONFLICT DO NOTHING` and survive deterministic step replay. Tracked as a coordinated Prisma migration in `eq-frontend`.
- **NEW (2026-05-15) — drop `account_provisioning_outbox` table.** The original design's outbox + polling publisher pattern is replaced by DBOS's workflow log. Operational observability migrates to `dbos.workflow_status` and `dbos.operation_outputs`. The table is dropped via a coordinated Prisma migration in `eq-frontend`.
- **Build the DBOS workflow** (`services/account_provisioning/workflow.py` in this repo): one `@DBOS.workflow()` function with seven `@DBOS.step` substeps:
  1. Read-only revalidation of queue state (idempotent guard against ignored / already-mapped rows).
  2. SQL transition `pending_account_mappings.status` `'approved' → 'creating'` (idempotent via status filter; the route reserves `'approved'` synchronously before the workflow starts, preserving the HTTP-layer idempotency contract from PR #13).
  3. Call `eq-agent-action-core POST /api/enrich?stream=false` → returns AccountProfile. Retries are explicit (`retries_allowed=True, max_attempts=5, backoff_rate=2.0`). The agent's returned `run_id` is cached via `DBOS.set_event`, so on workflow replay after a crash the step can call `GET /api/enrich/{run_id}` instead of re-running the expensive research (preserves correctness; minimizes duplicate spend; depends on cross-repo coordination — see §10.1 of the plan doc).
  4. Resolve or create account, keyed on `account_domains.(tenant_id, domain)` — the canonical idempotency surface (Phase 1 invariant). If the domain already binds an account, use it; otherwise INSERT `accounts` + INSERT `account_domains` in one transaction with the unique-key conflict guard.
  5. Atomic materialization (existing `materialize_account_approval()` logic, moved from `workers/materialization.py` to `services/account_provisioning/materialization.py`): contacts UPSERT, raw_interactions UPSERT, summaries UPSERT, links INSERT with `ON CONFLICT DO NOTHING` on the new unique index, queue status `→ 'mapped'`.
  6. Emit one `EnvelopeV1.*` event per backfilled interaction to EventBridge. Source `com.yourapp.transcription`, DetailType from a CLOSED lookup table (`{transcript, meeting, note, email}` → `EnvelopeV1.<type>`) that fails loud on unknown types. Body includes `extras.contacts` per the downstream consumer change briefs (`tasks/downstream/action-item-graph.md`, `tasks/downstream/eq-structured-graph-core.md`). At-least-once delivery; consumer-side MERGE-on-canonical-IDs is the dedup mechanism. Retries explicit on transient EventBridge failures.
  7. (No outbox audit row — the original design's outbox is dropped; observability lives in `dbos.workflow_status`.)
- **Refactor `routers/queue_actions.py` `/approve`** to: (a) reserve the row synchronously (`status='approved' + approval_attempt_id` in one UPDATE — preserves Phase 1 invariants 25-30), (b) start the workflow via `SetWorkflowID(f"queue-{queue_id}:approval-{approval_attempt_id}")`. Reopen produces a new attempt_id → new workflow ID; no collision with prior approvals' DBOS state. `/map` and `/ignore` are unchanged.
- **Delete** the polling worker, outbox publisher, advisory-lock helper, and worker entrypoint (`workers/__main__.py`, `workers/account_provisioning_worker.py`, `workers/outbox_publisher.py`, `workers/advisory_lock.py`). The `workers/` package is effectively retired; only the moved `materialization.py` survives, relocated to `services/account_provisioning/`.
- **DBOS Queue** with concurrency cap (V1: 5) bounds simultaneous workflow execution against HTTP request handling.
- **Extend `eq-structured-graph-core`** to consume the emitted `EnvelopeV1.*` events for backfilled interactions and MERGE Neo4j `Account` + `Contact` + edges. The consumer's existing pipeline already MERGE-on-canonical-IDs; the change is to read `extras.contacts` metadata to populate Contact node properties (per `tasks/downstream/eq-structured-graph-core.md`).
- **Build the production queue UI** (the existing `/dashboard/organization/email-pipeline` route is reference-only; the production surface is designed as part of the overall product UX, with per-owner scoping, source-type display, signal evidence breakdown, action context, and Approve/Map/Ignore actions). Polling on `GET /queue/{id}` is the V1 status mechanism; an SSE endpoint for streaming agent-reasoning events is a follow-up refinement.
- **Implement the expiry sweep** (daily scheduled workflow via DBOS queues with delayed enqueue; `@DBOS.scheduled` is flagged for Python deprecation per current docs): mark stale entries `archived` per Section 5.5 policy.
- **Implement re-open trigger** in both ingestion pipelines: when a new signal arrives on an archived queue entry, transition it back to `pending` per Section 5.9. The TODO in `services/pending_account_mappings.py:55-62` (reset `approval_attempt_id`, `creation_started_at`, `mapped_at`, etc. on reopen) lands as part of this work.
- **Authorization helper:** `can_act_on_queue_entry(user_id, queue_entry)` — owner-only V1, future-proofed for tier-based extension (unchanged).
- **eq-agent-action-core integration acceptance tests** (closes Codex finding #11): (1) contract-pinning test that asserts on the live `?stream=false` response shape (the agent's OpenAPI declares response `{}` — the pinning test is the load-bearing contract guard; (2) crash-recovery tests that simulate worker death mid-agent-call and mid-EventBridge-emit and assert DBOS resumes correctly; (3) reopen-path E2E that exercises the distinct-workflow-ID semantics; (4) server-to-server authentication and per-request JWT scoping; (5) consumer Pydantic compatibility for the emitted envelopes via the `scripts/verify_consumer_contracts.py` tool that ships alongside this work.

**Dependencies:** Phase 1 must ship first (already shipped 2026-05-14; silent regression fixed 2026-05-15 at commit `31f513f`). Two coordinated Prisma migrations (UNIQUE INDEX on `interaction_contact_links`; DROP `account_provisioning_outbox`) must land in `eq-frontend` before the workflow's first production run. Railway operational prep (set `DBOS_SYSTEM_DATABASE_URL` to a direct Neon connection; change start command to `--workers 1`) must precede the M1 deploy.

**Estimated size:** ~2 weeks of focused work, across ~5 milestones (substrate install → Prisma migrations → workflow definition + tests → queue route cutover → tooling + checklist updates). Detailed sequencing is in the implementation plan.

**Validation against AI-native thought leadership** (per project principle): research was redone 2026-05-15 (`docs/superpowers/research/2026-05-15-durable-execution-landscape.md`); the rethink brief and Codex consult are the audit trail for the DBOS pick.

### 7.3 Stopping point

After Phase 1.5 ships and is validated in production, the project hits an explicit stopping point. The next session re-plans comprehensively before any commitment to Phase 2. This is a deliberate discipline:

- Production behavior from Phases 1 and 1.5 generates real evidence about partial-contact rates, enrichment success rates, queue throughput, owner-approval response times, etc. — all of which should inform Phase 2 design.
- The AI-native thought-leadership landscape will have evolved by then (Phase 2 is the most architecturally sensitive piece and most exposed to emerging patterns).
- The team's then-current bandwidth and priorities may shift — Phase 2 may be the right next move, or something else may be.
- Forcing comprehensive re-planning prevents the "Phase 2 because we said so" trap.

The handoff infrastructure (Section 8) makes the stopping point and re-planning workflow explicit.

### 7.4 Phase 2 — Identity state machine and progressive enrichment (future scope)

Documented for architectural coherence; requires comprehensive re-planning before commitment.

**Intended scope (for future planning context):**

- Schema changes implementing the identity state machine on contacts (Section 4.1, 4.5)
- Refactoring contact-creation paths to set initial state based on signal quality
- Building the async enrichment worker (Section 4.2)
- Implementing per-surface gating configuration (backend support for the philosophy described in Section 4.3)
- Updating downstream services (eq-structured-graph-core, action-item-graph, opportunity-forecasting) to honor the state when scoring or surfacing contacts
- Re-validation against then-current AI-native thought leadership at planning start

**Acknowledged schema debt going into Phase 2 (closes Codex finding #14):**

The existing `contacts.validation_status` enum (values: `pending | validated | pending_name`) and the `pending_validations` table together form a proto-state-machine that today represents some of what Phase 2's identity state machine will represent. Phase 1 + 1.5 do not remove this debt:

- Under Option A, Phase 1 stops *adding new rows* with `validation_status='pending'` for unknown-account reasons. Unknown-account contacts no longer exist, so the code path at `services/transcript_enrichment.py:402` that sets `validation_status='pending'` for unresolvable names becomes the only remaining producer of `pending` values.
- The `pending_validations` table continues to function as today for name-unresolvable contacts. It is not modified in Phase 1 or 1.5.
- The Phase 2 design must explicitly migrate `validation_status` values to the new `identity_state` enum and either drop `pending_validations` or fold its contents into `contact_enrichment_attempts`. Phase 2 cannot pretend these tables don't exist; the migration is non-trivial because the value semantics partially overlap (`validated` ≈ `resolved` + `verified`; `pending_name` ≈ `shell` or `emerging`).
- This is an explicit acknowledgment that Phase 2 carries inherited schema cost. It does not change Phase 1 or 1.5 scope; it does change Phase 2 sizing.

**Dependencies:** Phase 1.5 must ship first (state machine assumes every contact has an account anchor).

**Estimated size:** 4-6 weeks (subject to re-planning; the schema-debt migration may add a week).

### 7.5 Phase 3 — Advanced policies (future scope)

Documented for completeness; sized by measured need from production behavior of Phases 1-2.

**Intended scope (for future planning context):**

- Conflict resolution policy for same-email-different-name cases (source-confidence rules, conflict logging, optional human review)
- Multi-account contact history (separate `contact_account_history` table; supports a person changing companies)
- Fuzzy duplicate detection across emails (embedding-based or rule-based with confidence threshold; phase-gated by precision/recall measurement)

**Dependencies:** Phase 2 must ship first; Phase 3 sub-pieces are independently sized.

**Estimated size:** Each sub-piece is its own bounded project; whole phase to be re-scoped based on production data.

## 8. Cross-Cutting Concerns

### 8.1 Testing strategy

- TDD for all new code (per `superpowers:test-driven-development` discipline).
- Phase 1 work: extend existing test suites; small targeted unit and integration tests.
- Phase 1.5 work: integration tests against `eq-agent-action-core` (mocked in CI, real in staging); migration tests for schema changes; queue-behavior tests for race conditions on dedup, owner-scoping enforcement, expiry sweeps, backfill atomicity.
- Manual validation for user-facing workflows.
- Test-tenant setup (per Section 5.8) must be designed alongside Phase 1.5 so internal-domain detection is testable.

### 8.2 Multi-session handoff

This project will span multiple sessions across multiple weeks. The handoff infrastructure ensures future agents can resume execution without losing context.

**Artifacts maintained across sessions:**

- This design document (canonical project intent; stable path)
- The implementation plan (output of `superpowers:writing-plans`; canonical execution detail)
- Auto-memory files at `~/.claude/projects/-Users-peteroneil-live-transcription-fastapi/memory/` updated each session
- `tasks/lessons.md` for gotchas worth persisting
- Checkpoint state via `superpowers` checkpoint skill at end of each session

**Session-start procedure for future agents:**

1. Read `MEMORY.md` (auto-loaded)
2. Read this design document
3. Read the implementation plan
4. Read the latest checkpoint
5. Resume execution

### 8.3 Validation against AI-native thought leadership

Per project principle: **before each phase's design or implementation starts, run targeted research on current cutting-edge AI-native thought leadership.** Sources to check:

- Recent academic work on entity resolution with LLMs (arXiv)
- Microsoft Research GraphRAG and successor patterns
- Agentic graph systems research from frontier labs
- Knowledge graph + LLM combinations from emerging systems
- Recent papers on identity-aware AI systems

Sources NOT to rely on as primary reference:

- Modern CRMs (Attio, HubSpot, Salesforce, etc.) — they encode older patterns and our differentiation depends on adopting emerging approaches the CRM market hasn't yet caught up to.

This validation is explicit in the implementation plan as a step, not optional. Most critical at Phase 2 design start (the architecturally-sensitive piece).

### 8.4 Codex usage as recurring quality gate

Per project principle: codex (consult or review mode) is invoked at design checkpoints throughout the project, not as a one-shot.

- After Phase 1 implementation plan is written, before execution: codex consult for adversarial check on the plan
- After Phase 1 implementation, before merge: codex review on the diff
- Same pattern for Phase 1.5
- Same pattern for Phase 2 and 3 if/when committed

Documented in the implementation plan as recurring quality gates.

### 8.5 Schema migration discipline and durability machinery (revised 2026-05-15)

> **Revision note (2026-05-15):** Section 8.5 originally described an application-level outbox table + polling publisher pattern as the durability mechanism. After the architecture rethink (see Section 7.2 revision note), this responsibility shifts to DBOS's workflow log in the `dbos.*` schema. What was kept: the cross-service idempotency invariant (downstream consumer MERGE-on-canonical-IDs), the test-data wipe simplification, and the cross-repo Prisma schema ownership pattern. What changed: the `account_provisioning_outbox` table is dropped (replaced by `dbos.workflow_status`); the durability invariant is now provided by DBOS rather than an application-level outbox; two new coordinated Prisma migrations are required.

Since all current data is test data and will be wiped, migrations are simpler than typical (no complex backfill or dual-write phases).

- Each schema change is an explicit migration step with documented up/down behavior.
- Migration runs are documented in the implementation plan.
- Coordinated through `eq-frontend/schema.prisma` per existing ownership patterns (per `reference_prisma_schema_ownership.md` memory).
- Memory files updated after migrations land.

**Schema deltas across the initiative (consolidated, post-2026-05-15 revision):**

- `pending_account_mappings` — column additions (Section 5.2; shipped in Phase 1).
- `pending_account_mapping_signals` — new table (Section 5.2; shipped in Phase 1).
- `accounts.state` — column addition (Section 6; shipped).
- `contacts.account_id` — NOT NULL after test-data wipe (shipped in Phase 1.5 P2).
- `raw_interactions.account_id` — NOT NULL after test-data wipe (shipped in Phase 1.5 P2).
- **NEW (2026-05-15) — `interaction_contact_links` UNIQUE INDEX on `(interaction_id, contact_id)`** — replaces the in-memory dedup the original worker depended on; makes materialization replay-safe at the SQL layer for DBOS step re-execution.
- **NEW (2026-05-15) — DROP `account_provisioning_outbox` table** — the polling-publisher pattern is replaced by DBOS's workflow log. The table was created during the original Phase 1.5 schema migration but never had its publisher deployed; operational observability migrates to `dbos.workflow_status`.
- **NEW (2026-05-15) — `dbos.*` schema** — created automatically by DBOS on first `DBOS.launch()`. Tables: `dbos.workflow_status`, `dbos.workflow_inputs`, `dbos.operation_outputs`, `dbos.workflow_events`, etc. Lives outside Prisma's introspection scope (Prisma is scoped to `public`); no Prisma model is required.

**Durability invariant (the property that must hold across the cutover):**

For every approved queue entry, the system must reach a terminal state where: (a) the `accounts` and `account_domains` rows exist for the resolved domain; (b) `contacts`, `raw_interactions`, `interaction_summaries`, `interaction_contact_links` rows are populated for every active signal; (c) `pending_account_mappings.status='mapped'`; (d) the downstream Neo4j graph has Account + Contact + edges merged for the materialized rows. No partial state can persist indefinitely under any reasonable failure mode (process crash, network partition, downstream consumer outage).

**Pre-2026-05-15 mechanism:** application-level outbox table + polling publisher + EventBridge + consumer MERGE.

**Post-2026-05-15 mechanism (the DBOS architecture):**

The workflow's seven steps are checkpointed individually to `dbos.workflow_status` and `dbos.operation_outputs`. Crash recovery semantics:

- A process crash mid-Step-3 (agent call) leaves the step uncommitted in DBOS state. On restart, DBOS resumes the workflow; the step re-executes against the cached `run_id` event (`DBOS.set_event` / `DBOS.get_event`), reusing the agent's prior research where possible.
- A crash mid-Step-5 (materialization) leaves the SQL transaction either committed (DBOS step output is recorded) or rolled back (DBOS sees no output). On restart, DBOS re-executes; the step's SQL is idempotent via ON CONFLICT clauses + status filters + the new UNIQUE INDEX on `interaction_contact_links`.
- A crash mid-Step-6 (EventBridge emit) leaves emissions potentially duplicated (the boto3 call returned, but DBOS hadn't recorded the step output). On restart, DBOS re-executes the step; EventBridge receives the events again. Consumer-side MERGE-on-canonical-IDs collapses duplicates — this is the load-bearing dedup surface.
- A consumer crash after receiving the event but before MERGEing Neo4j is recovered by EventBridge redelivery + consumer-side MERGE idempotency. Same property as the original design.

**Postgres and Neo4j cannot permanently diverge** as long as DBOS resumes the workflow (it does, on process restart) and the consumer's MERGE is idempotent (it is, by design — Phase 1 invariant).

**Comparison with the original outbox-style design (preserved for audit-trail purposes):**

| Property | Original (outbox + polling publisher) | Revised (DBOS workflow log) |
|----------|---------------------------------------|------------------------------|
| Durability layer | Application `account_provisioning_outbox` table | `dbos.workflow_status` + `dbos.operation_outputs` |
| Replay safety | App-side advisory locks, ON CONFLICT, in-memory link dedup | DBOS step replay with cached outputs + SQL ON CONFLICT + unique index |
| Failure-window between DB commit and EventBridge publish | Outbox row stays unpublished → publisher retries on next poll | DBOS step retries (explicit `retries_allowed=True` policy) |
| Cross-service dedup | Consumer MERGE-on-canonical-IDs | Consumer MERGE-on-canonical-IDs (unchanged) |
| Process model | Separate worker container + separate publisher | Single FastAPI process; workflows run in-process |
| At-least-once guarantee | Yes (outbox publisher retries) | Yes (DBOS step retries; EventBridge delivery) |
| Operational complexity | Two long-running processes; advisory locks; per-row publisher state | One process; DBOS state tables in Postgres; no separate publisher |

The implementation plan (`docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md`) includes test cases for each crash-recovery scenario above. The verified-contracts discipline ensures the schema deltas above are probed against the live Neon project before code is written (§7.1 of the plan; §3.1 of the plan's verified-contracts section).

### 8.6 Rollout discipline

Each phase ships behind feature flags where reasonable, allowing incremental enablement per tenant.

- Phase 1 fixes: feature-flagged or gated by environment variables (e.g., `ENFORCE_ACCOUNT_ANCHOR`)
- Phase 1.5: queue-data-collection feature-flagged separately from queue-worker behavior to enable phased rollout
- Production validation per-tenant before broader rollout
- Rollback paths documented for each phase

### 8.7 Encapsulated policy discipline

A discipline running through the whole design: **encapsulate policy, don't scatter it.**

- Expiry policy in configuration (not hardcoded throughout the codebase)
- Archive retention as a single rule (not per-table logic)
- Approval authorization in one function/policy (so tier-based extension is one-place change)
- Per-surface gating thresholds in configuration (so adding a UI surface or changing thresholds is config, not code)

This pays back many times over the lifetime of the system.

## 9. Out of Scope

Explicitly NOT part of this initiative (any phase):

- UI design for the queue surface, partial-contact visual treatment, or any other product UX work — backend designed to support future UI flexibility, but UI itself is a separate effort
- Changes to any service's business logic outside the contact/account/interaction layer
- Changes to authentication, authorization framework, or tenant-isolation infrastructure (unchanged from current architecture)
- Changes to the EventBridge envelope structure beyond adding the `AccountCreated` event type
- Migration tooling for production data (test-data wipe simplifies this)
- Performance optimization or scaling work beyond what's needed to make Phases 1 and 1.5 functional
- Documentation of the product or user-facing features — this design covers backend architecture only

## 10. Open Implementation-Detail Decisions

These are settled during plan-writing or implementation, not in this design.

**Newly closed (was open in prior revision; now locked per 2026-05-13 design revision):**

- ~~Queue-entry tracking mechanism~~ → locked: first-class `pending_account_mapping_signals` join table (Section 5.2; closes Codex #7)
- ~~Owner determination rule for transcripts where anchor account owner differs from recording user~~ → locked: recording user is always the owner (Section 5.3; closes Codex #5)
- ~~Tenant escalation/re-open policy for owner-ignored entries~~ → locked: signal-based re-open + admin escalation at threshold (Section 5.9; closes Codex #15)

**Still open (carry into plan-writing):**

- Final state machine value names (Phase 2 only — currently `shell | emerging | partial | resolved | verified`; alternatives may emerge during design)
- Async enrichment worker cadence (hourly vs. daily vs. event-driven) (Phase 2)
- Tavily / LLM enrichment budget defaults
- Worker location for Phase 1.5 (extend eq-email-pipeline vs. new lightweight service vs. extend live-transcription-fastapi)
- Outbox publisher implementation (same process as worker vs. separate scheduled task; same EventBridge bus or dedicated)
- Test tenant configuration for internal-domain testing (synthetic business domain vs. separate test tenant)
- Exact form of the production queue UI (out of backend scope; coordinated via product UX effort)
- `QUEUE_REOPEN_ESCALATION_THRESHOLD` default (Section 5.9 proposes 3; final value validated against early production data)

## 11. Key References

- `docs/contacts-architecture.md` — current-state contacts architecture across all services
- `docs/contact-enrichment.md` — transcript contact enrichment feature documentation
- Memory directory: `~/.claude/projects/-Users-peteroneil-live-transcription-fastapi/memory/`
  - `MEMORY.md` — index
  - `project_transcript_enrichment.md` — completed prior initiative
  - `project_neo4j_contact_architecture.md` — Neo4j Contact MERGE key standardization
  - `reference_contacts_architecture.md` — pointer to the architecture doc
  - `reference_prisma_schema_ownership.md` — schema migration coordination
  - `reference_test_tenant.md` — test tenant ID and seed data
  - `reference_neo4j_shared_instance.md` — Neo4j Aura instance
  - `feedback_contact_id_consistency.md` — UUIDv4 contact_id rule
  - `feedback_tenant_isolation.md` — cross-tenant query prohibition
  - `feedback_branch_safety.md` — feature branch discipline
  - `feedback_downstream_investigation.md` — investigation pattern
- `tasks/lessons.md` — source field validation, FK chain gotchas
- `tasks/downstream/*.md` — investigation docs and agent prompts for downstream services
- Code references for current-state logic:
  - `eq-email-pipeline/src/pipeline/orchestrator.py` — email contact creation with account derivation
  - `eq-email-pipeline/src/pipeline/calendar_sync.py` — calendar attendee contact creation (no account derivation today)
  - `eq-email-pipeline/src/pipeline/skeleton.py` — Neo4j Account/Contact MERGE patterns
  - `eq-email-pipeline/src/pipeline/domain_discovery.py` — pending_account_mappings queue and dead 'create' action
  - `eq-email-pipeline/src/persistence/postgres.py` — `find_or_create_contact`, `lookup_account_by_domain`, `queue_pending_domain`
  - `live-transcription-fastapi/services/transcript_enrichment.py` — calendar matching, contact resolution, 3-tier name resolution, `existing_contact_ids` parameter
  - `live-transcription-fastapi/services/intelligence_service.py` — `_persist_contact_links` with FK chain handling
  - `live-transcription-fastapi/main.py:276`, `main.py:411`, `main.py:479` — WebSocket account_id paths
  - `eq-structured-graph-core/app/db/queries/skeleton.py` — Contact and Account MERGE patterns
  - `eq-agent-action-core/src/eq_agent/db/accounts.py:22-156` — `create_or_update_account_from_enrichment` (the production-deployed account creation function)
  - `eq-frontend/app/onboarding/(flow)/intelligence/` — onboarding flow leveraging the agent for user-org account creation
  - `eq-frontend/app/dashboard/organization/email-pipeline/` — admin queue UI (reference-only)
  - `eq-frontend/lib/trpc/routers/account-capture.ts` — partial scaffolding for manual account creation (not yet wired to agent)

## 12. Acceptance Criteria for the Committed Scope (verifiable invariants)

Acceptance is gated on repo-verifiable invariants, not aspirational behavior statements. Each invariant has a clear verification mechanism (grep, type-check, automated test, or replay test). Codex finding #12 explicitly called the prior wording too weak; this revision uses Codex's recommended invariant style.

### Phase 1 ships when ALL of the following invariants hold:

**Backend contract invariants (verified by static checks + type system):**

- `EnvelopeV1.account_id` field declaration in `models/envelope.py` is required (not `Optional`). Grep: `grep -n "account_id:" models/envelope.py` returns the required form.
- `RequestContext.account_id` field declaration is required for ingestion auth contexts; `get_auth_context()` rejects requests with missing `X-Account-ID` header.
- `process_transcript()` signature in `services/intelligence_service.py` has `account_id: str` as required (not `Optional[str] = None`). Any caller omitting it fails type-check.
- `UploadJob.account_id` field declaration is required (not `Optional`) at `models/job_models.py`.
- `TextCleanRequest` and `UploadInitRequest` both declare `account_id: str` as required.

**Ingestion-path invariants (verified by per-path integration tests):**

- WebSocket `/listen` rejects connection upgrades that lack `X-Account-ID` with WebSocket close code 1008.
- `/text/clean` returns 400 if `account_id` missing from request body.
- `/batch/process` returns 400 if `account_id` missing from form/header.
- `/upload/init` returns 400 if `account_id` missing from request body; `/upload/complete` succeeds only when its parent `UploadJob.account_id` was set.

**No-orphan invariants (verified by grep + integration tests):**

- `grep -rn "account_id=None" services/ routers/ main.py` returns zero hits in code paths that construct entities (acceptable only in test fixtures explicitly testing rejection).
- No call site of `process_transcript()` in routers/ or main.py omits `account_id`.
- No INSERT statement against the `contacts` table omits or NULLs `account_id` in non-test code.
- Personal-domain attendees never produce a `contacts` row or a `pending_account_mappings` row (verified by integration test seeding gmail.com attendees and asserting absence).

**Queueing invariants (Phase 1 surfaces the queue but does not process it):**

- For a transcript with anchor account `acme.com` and attendees `[alice@acme.com, partner@consultingco.com, intern@gmail.com]`: integration test asserts `alice` becomes a contact with `account_id=acme`, `partner` produces a `pending_account_mapping_signals` row with no contact materialized, `intern` produces no row anywhere.
- `pending_account_mapping_signals` insertion is idempotent under retry (same `(queue_id, contact_email, source_type, interaction_id, calendar_event_id)` raises unique violation handled as no-op).
- Owner determination test: a transcript by user A with anchor account owned by user B produces a queue entry with `owner_user_id = A`.

**Code-quality invariants:**

- All new behavior covered by tests (unit + integration where appropriate). Test coverage report shows no regression for touched files.
- Codex consult on the diff returns no CRITICAL findings.
- Documentation updated (memory files, `docs/contacts-architecture.md`).

### Phase 1.5 ships when ALL of the following invariants hold:

**Schema invariants:**

- `contacts.account_id` is NOT NULL at the database level (verified by `\d contacts` showing `NOT NULL`).
- `raw_interactions.account_id` is NOT NULL at the database level.
- `accounts.state` exists with `active | archived` enum, NOT NULL, default `active`.
- `account_provisioning_outbox` table exists with the columns specified in Section 5.4.
- Pre-migration test-data wipe documented and executed; no orphan rows remain.

**Worker durability invariants:**

- Worker is replay-safe: integration test approves a queue entry, kills the worker mid-transaction, restarts the worker, and asserts the final state has exactly one account, the correct contacts, the correct outbox row, and no duplicate side effects.
- Outbox publisher is replay-safe: integration test commits an outbox row, fails the EventBridge publish, retries publish, succeeds; consumer integration test verifies single materialization in Neo4j after duplicate deliveries.
- `eq-agent-action-core` call is idempotent under same `worker_attempt_id`: acceptance test calls the agent twice with the same key and asserts a single account exists.

**End-to-end materialization invariants:**

- Approve flow: integration test approves a queue entry with three signals across two interactions, then asserts:
  - One new row in `accounts` with state=`active`.
  - Three new rows in `contacts` (one per unique signal email), all with the new `account_id`.
  - Correct number of `interaction_contact_links` rows.
  - Outbox row published.
  - Neo4j has the Account node, Contact nodes, and ATTENDED edges via MERGE-everywhere. Verified by Cypher query.
- Map flow: integration test maps a queue entry to a pre-existing account; same invariants minus the new accounts row.
- Ignore flow: integration test sets entry to ignored; no contacts/links materialize; signals remain unarchived for future re-open.

**Re-open invariants:**

- Integration test: user A ignores a queue entry for `acme.com`; user B's email pipeline ingests a new email from `acme.com`; queue entry returns to `status='pending'` with `re_open_count=1` and a new signal row.
- Threshold escalation test: trigger three re-opens; verify `status='tenant_review'`; verify `can_act_on_queue_entry` returns true for tenant admin user, in addition to the original owner.

**Domain-handling invariants:**

- Personal-domain attendees never produce queue entries (verified by gmail.com test in Section 5.7 territory).
- Internal-domain attendees (matching tenant's `provider_connections`) never produce queue entries; they are recorded as interaction participants only.

**Authorization invariants:**

- `can_act_on_queue_entry(non_owner_user_id, queue_entry)` returns `False` in V1 (owner-only); returns `True` in `tenant_review` state for admin users.
- All queue-action API routes call the helper; integration test exercises a non-owner attempting Approve/Map/Ignore and asserts 403.

**Neo4j convergence invariants (replay tests):**

- Replay test: emit AccountCreated twice for the same account_id; verify Neo4j has exactly one Account node, exactly the expected Contact nodes, and no duplicate edges.
- Replay test: emit AccountCreated, drop a Contact node, re-emit AccountCreated; verify Contact node is restored via MERGE.

**Code-quality invariants:**

- All new behavior covered by tests (unit + integration + manual workflow validation).
- Codex consult on the diff returns no CRITICAL findings.
- Production validation in test tenant before broader rollout.
- Documentation updated comprehensively.

After Phase 1.5 acceptance: project hits the explicit stopping point per Section 7.3.
