# Phase 1.5 Implementation Plan — Async Orchestration on DBOS

**Status:** DRAFT v2 — Codex consult REVISE folded in; substrate decision (DBOS) unchanged.
**Date:** 2026-05-15
**Branch baseline:** `main` @ `7b12b89`
**Supersedes:** Polling-worker + outbox-publisher architecture from `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` Phase 1.5 main scope (Tasks 1.5.6–1.5.21). Phase 1 and Phase 1.5 P2 cleanup remain shipped; this plan only revises the durability machinery.
**Substrate decision audit trail:** `docs/superpowers/specs/2026-05-15-async-orchestration-rethink-brief.md` (Step 8 decision process) → checkpoint `phase-1.5-rethink-decided-dbos` (D1–D7) → this plan (Step 6).
**Codex consult audit trail:** `/tmp/codex-dbos-plan-consult-output.md` (2026-05-15). Verdict: REVISE. Specific findings folded in are recorded in §20 "Revision history".

---

## 0. One-paragraph summary

The Phase 1.5 worker, outbox publisher, advisory-lock helper, and worker entrypoint are deleted. Account-provisioning becomes a DBOS workflow running in-process inside the FastAPI Railway service. DBOS provides durable execution (workflow + step checkpoints to a `dbos.*` schema in Neon Postgres), per-step retry with deterministic replay, an explicit `SetWorkflowID` context for application-level workflow identity, and library-native primitives for Phase 2/3 (durable sleep, queues, HITL via `send`/`recv`/`set_event`/`get_event`). HTTP queue routes (`/queue/{id}/approve`, `/map`, `/ignore`) keep their request/response contracts; `/approve` reserves the queue row synchronously (status `pending` → `approved`, idempotency at the route boundary) and THEN starts the workflow; `/map` keeps its inline materialization (no workflow needed); `/ignore` is unchanged. EventBridge stays as the cross-service notification layer via `EnvelopeV1.*` events whose Source matches the existing live rules (`com.yourapp.transcription`). Two Prisma migrations are needed on the `eq-frontend` side: (a) add a UNIQUE INDEX on `interaction_contact_links (interaction_id, contact_id)` to make materialization replay-safe at the database layer; (b) drop the `account_provisioning_outbox` table (replaced by `dbos.workflow_status` for observability).

---

## 1. What this plan delivers

1. The async surface Phase 1 deliberately left for Phase 1.5 — when a tenant approves an unknown business domain from the queue, the AI agent researches it (30-90s), the account materializes, every signal becomes a contact, downstream consumers are notified — and the user watches it happen with streaming progress.
2. A substrate that compounds across Phase 2 (progressive re-enrichment workflows) and Phase 3 (HITL conflict resolution) without re-architecting.
3. Operational simplicity: one FastAPI process on Railway, one Postgres database (Neon, shared with the rest of the app), no RabbitMQ, no Helm, no separate worker container, no extra service surface to monitor.
4. Closure on every test-discipline gap codified 2026-05-15 (Items 1–5 in `tasks/downstream/test-discipline-gaps-2026-05-15.md`) — including the cross-service contract gap that surfaced an hour after the SQL gap was fixed.
5. Two coordinated Prisma migrations (cross-repo to `eq-frontend`) that make materialization replay-safe at the SQL layer and retire the no-longer-needed outbox table.

---

## 2. Out of scope (explicit)

- Phase 2 / Phase 3 design detail. The plan sketches how DBOS primitives compound (§9), but does not specify state-machine values, re-enrichment cadences, or conflict-resolution policy.
- Queue UI work in `eq-frontend`. The HTTP contract this plan exposes is what the UI consumes; UI work tracks separately.
- Streaming SSE wire format for the front-end. Section 4.4 documents the user-visible affordance ("watch the AI reason") and the boundary between the workflow and a future SSE endpoint, but the on-the-wire SSE event schema is a UX detail finalized during execution.
- Migration of historical data. All current data in Neon eq-dev is test data; the plan assumes a clean baseline.
- Changes to ingestion paths (transcript / email / calendar / upload). Phase 1 + Phase 1.5 P2 already shipped per-attendee three-state branching; this plan does not revisit it.

---

## 3. Verified contracts (probed live 2026-05-15)

This section is non-negotiable. Per the cross-service-contract lesson (`tasks/lessons.md` "Cross-service contract verification at design time (2026-05-15)"), every contract the new code crosses is probed and cited inline. Drift between this section and reality at execution time is itself a finding the executing session must resolve before shipping.

### 3.1 Neon Postgres schema (project `super-glitter-11265514`, branch `default`, database `neondb`)

Probed via `information_schema.columns` AND `pg_indexes` against the live database 2026-05-15.

#### Tables the new code reads or writes

**`pending_account_mappings`** — 22 columns. Key columns:
- `id uuid NOT NULL` (PK)
- `tenant_id uuid NOT NULL`
- `domain varchar NOT NULL`
- `status text NOT NULL DEFAULT 'pending'` — conventional values: `pending`, `approved`, `creating`, `mapped`, `ignored` (no CHECK constraint; values are application-defined)
- `resolved_account_id uuid NULL`
- `approval_attempt_id uuid NULL` — HTTP idempotency on `/approve`
- `creation_started_at timestamptz NULL`, `mapped_at timestamptz NULL`, `ignored_at timestamptz NULL` — lifecycle stamps
- `archived_at timestamptz NULL`, `archive_reason text NULL`, `re_open_count int NOT NULL DEFAULT 0`, `last_reopened_at timestamptz NULL` — reopen lifecycle
- `owner_user_id uuid NOT NULL`, `discovered_from_type text NOT NULL`, `discovered_from_interaction_id uuid NULL`
- `expires_at timestamptz NOT NULL`, `email_count int NOT NULL DEFAULT 1`
- Free-form: `discovered_from_email text NULL`, `discovered_context text NULL`, `ignored_by uuid NULL`
- `created_at`, `updated_at` (existing pattern)

**Unique indexes (relevant):**
- `pending_account_mappings_pkey` on `id`
- `pending_account_mappings_tenant_id_domain_key` on `(tenant_id, domain)` — first-owner-wins UPSERT key (Phase 1 invariant 5)
- Indexes on `(tenant_id, archived_at)` and `(tenant_id, status)` for queue UI listing

**`pending_account_mapping_signals`** — 13 columns. The workflow READs these to enumerate materialization signals.
- `id`, `queue_id`, `tenant_id`, `source_type text NOT NULL`, `source_user_id`, `contact_email varchar NOT NULL`, `contact_display_name`, `contact_role`, `interaction_id uuid NULL`, `calendar_event_id uuid NULL`, `created_at`, `archived_at`
- Unique index: `pending_signal_dedup` on `(queue_id, contact_email, source_type, interaction_id, calendar_event_id)`

**`accounts`** — workflow INSERTs new account rows. Material columns:
- `id uuid NOT NULL` (PK), `tenant_id uuid NOT NULL`, `name text NOT NULL`
- `state varchar NOT NULL DEFAULT 'active'` (Phase 1.5 column)
- Optional enrichment: `industry text`, `company_size varchar`, `region varchar`, `website text`, `address text`, `phone varchar`, `description text`, `number_of_employees int`, `annual_revenue numeric`, `account_type varchar DEFAULT 'Prospect'`, `parent_account_id uuid`, `owner_id uuid`, etc.
- `ai_workflow_trigger boolean NOT NULL DEFAULT false`
- **Confirmed: `accounts` has NO `domain` column.** The 2026-05-15 regression already validated this; `account_domains` is the canonical domain → account binding.
- **Unique constraints:** `accounts_pkey` on `id` ONLY. **There is NO unique index on `(tenant_id, name)`.** The plan-v1 idea of `ON CONFLICT (tenant_id, name)` would have failed at SQL execution time. See §6.4 for the corrected idempotency anchor.

**`account_domains`** — workflow INSERTs the canonical domain → account binding here:
- `id uuid NOT NULL`, `tenant_id uuid NOT NULL`, `account_id uuid NOT NULL`, `domain varchar NOT NULL`, `created_at timestamp NOT NULL`
- **Unique index:** `account_domains_tenant_id_domain_key` on `(tenant_id, domain)`. This IS the canonical idempotency anchor for the workflow's account-creation step.

**`contacts`** — workflow INSERTs materialized contacts. Material columns: `id`, `tenant_id`, `email`, `first_name`, `last_name`, `account_id NOT NULL` (Phase 1 enforcement), `source text DEFAULT 'manual'`, `validation_status` USER-DEFINED enum (`pending | verified | discarded`).
- **Unique index:** `contacts_tenant_id_email_key` on `(tenant_id, email)`. Materialization's `ON CONFLICT (tenant_id, email)` works against this.

**`raw_interactions`** — workflow UPSERTs backfill rows AND READs columns for envelope assembly. Material columns: `interaction_id uuid NOT NULL` (PK), `tenant_id uuid NOT NULL`, `account_id uuid NOT NULL` (Phase 1 enforcement), `interaction_type text NOT NULL`, `raw_text text NULL`, `media_url text NULL`, `user_id uuid NULL`, `created_at`, `updated_at`.
- **Unique index:** `raw_interactions_pkey` on `interaction_id` (the UPSERT's ON CONFLICT key).

**`interaction_summaries`** — workflow UPSERTs placeholder summaries (race-safe via ON CONFLICT). Material columns: `summary_id uuid NOT NULL` (PK), `tenant_id uuid NOT NULL`, `interaction_id uuid NOT NULL`, `summary_type text NOT NULL`.
- **Unique index:** `interaction_summaries_interaction_id_key` on `interaction_id`. The materialization's `ON CONFLICT (interaction_id)` works against this. (See `tasks/lessons.md:122-139` — the lesson that ON CONFLICT inference works against unique indexes, not only unique constraints.)

**`interaction_contact_links`** — workflow INSERTs links. Material columns: `link_id uuid NOT NULL` (PK), `interaction_id uuid NOT NULL` (per `tasks/lessons.md:15-32`, this actually holds `summary_id`; Prisma naming artifact), `contact_id uuid NOT NULL`, `entity_specific_summary text NULL`.
- **Unique index:** `interaction_contact_links_pkey` on `link_id` ONLY. **There is NO unique index on `(interaction_id, contact_id)`.** Replay-safety in materialization currently depends on an in-memory `linked_pairs` set in `workers/materialization.py:161` — this is replay-broken across step re-execution. The plan introduces a coordinated Prisma migration (§10.4) to add UNIQUE INDEX on `(interaction_id, contact_id)` so the link insert can use `ON CONFLICT DO NOTHING` and be truly replay-safe.

**`calendar_event_interaction_links`** — workflow DOES NOT WRITE. Included for completeness because materialization references it for backfill. Material columns: `id`, `calendar_event_id`, `interaction_id`, `tenant_id`, `match_confidence`, `match_method`, `created_at`.

**`account_provisioning_outbox`** — exists but **dropped by this plan** (see §5.5). The polling publisher pattern is replaced by `dbos.workflow_status` for observability. The table is removed via a coordinated Prisma migration (§10.4).

#### New schema this plan introduces (DBOS-managed)

The `dbos.*` schema for workflow/step state. DBOS creates it automatically on first `DBOS.launch()` per its docs. Tables include `dbos.workflow_status`, `dbos.workflow_inputs`, `dbos.operation_outputs`, `dbos.workflow_events`, and others. **No application changes required.** Prisma is scoped to the `public` schema by configuration; `dbos.*` is invisible to Prisma introspection.

### 3.2 eq-agent-action-core HTTP contract

Probed via `GET https://eq-agent-action-core-production.up.railway.app/openapi.json` 2026-05-15:

**Title/version:** `eq-agent-action-core 0.1.0`

**Endpoint:** `POST /api/enrich`

**Request body schema (`#/components/schemas/EnrichRequest`):**

```json
{
  "properties": {
    "url": {"type": "string", "title": "Url", "description": "The URL or domain to enrich"},
    "effort": {
      "type": "string",
      "enum": ["low", "medium", "high"],
      "default": "medium",
      "description": "Research effort level: low (fast), medium (balanced), high (thorough)"
    }
  },
  "type": "object",
  "required": ["url"]
}
```

**Query parameter:** `stream: bool = true` (default). With `stream=true`, the response is SSE; with `stream=false`, the response is a single blocking JSON body.

**Authentication:** Bearer token (HS256 internal JWT, `INTERNAL_JWT_SECRET`, `iss=eq-frontend`, `aud=eq-backend`, `tenant_id` claim is read by the agent for tenant scoping). The endpoint's OpenAPI does NOT formally declare a security scheme, but operational practice and the endpoint description (`"Scoped to tenant_id from JWT for tenant isolation"`) confirm Bearer is required. **Plan finding:** the agent's OpenAPI should declare a security scheme; tracked as cross-repo coordination (§10.1).

**Response shape:** OpenAPI declares `200` response as `application/json` with empty schema `{}`. The endpoint description says: *"Enrich a company URL into a structured AccountProfile."* **There is no `AccountProfile` schema in `components.schemas`.** Other schemas include `AbandonResponse`, `ChipEdit`, `CreateConversationRequest`, ... `EnrichRequest`, `FinalizeResponse`, `HTTPValidationError`, etc. — none named `AccountProfile`.

**Plan finding (load-bearing):** the actual response shape is unspecified in the contract surface we can probe. The executing session MUST (a) coordinate with the agent team to publish `AccountProfile` in the agent's OpenAPI (§10.1), AND (b) write a contract-pinning test (§7.2) that asserts on the live `?stream=false` response JSON and treats it as the canonical shape. Without (b), any agent-side change to the response silently breaks us.

**Confirmed wrong contract in current code:** `services/agent_action_core_client.py` POSTs `{tenant_id, domain, worker_attempt_id}` and reads `{account_id, domain}` from the response. Both are imagined; the agent silently drops `tenant_id`+`worker_attempt_id` (it reads tenant from JWT and does not currently document an idempotency-key behavior), and the agent does NOT return `{account_id, domain}`. The new client REWRITES this contract; see §6.

**Companion endpoint:** `GET /api/enrich/{run_id}`. Description: *"Retrieve a past enrichment result by run_id (job_id). Scoped to tenant_id from JWT for tenant isolation."* Response schema also bare `{}`. Useful for the workflow's agent-call replay strategy (§6.4) IF the response contains the full AccountProfile after run completion — to be verified at execution time and coordinated if it doesn't (§10.1).

### 3.3 EventBridge rules (live AWS account `211125681610`, region `us-east-1`)

Probed via `aws events list-rules` + `aws events describe-rule` 2026-05-15.

**Total rules on `default` bus:** 14 (one disabled). Only `default` event bus exists.

**Relevant rule #1: `action-item-graph-rule`**
- Description: "Routes transcript and email events to action-item-graph SQS queue"
- EventPattern: `{"source": ["com.yourapp.transcription", "com.eq.email-pipeline"], "detail-type": ["EnvelopeV1.transcript", "EnvelopeV1.note", "EnvelopeV1.meeting", "EnvelopeV1.email"]}`

**Relevant rule #2: `eq-structured-graph-rule`** (NOTE: NOT `eq-structured-graph-ingest-rule` — the handoff doc referenced the old name; the live name is `eq-structured-graph-rule`)
- Description: "Routes transcript and email events to eq-structured-graph-core SQS queue"
- EventPattern: `{"source": ["com.yourapp.transcription", "com.eq.email-pipeline"], "detail-type": ["EnvelopeV1.transcript", "EnvelopeV1.note", "EnvelopeV1.meeting", "EnvelopeV1.email"]}`

**Plan finding (load-bearing):** the existing `workers/outbox_publisher.py` emits events with `Source="com.eq.contact-quality"` and `DetailType="AccountProvisioning.{event_type}"` — **NEITHER live rule matches that source.** The events would land on the bus and be silently dropped. Since the publisher has never fired in production, this is a latent design gap — but it confirms that the previous architecture was emitting events nobody could consume.

**Implication for the plan:**

The new workflow's final step emits to EventBridge for the same product purpose: tell downstream consumers (eq-structured-graph-core, action-item-graph) that an account was created and its contacts/interactions need downstream processing. Two paths:

1. **Path A — emit `EnvelopeV1.*` events for each backfilled interaction.** Each `interaction_contact_links` row produced by the materialization corresponds to a transcript/meeting/email already in `raw_interactions`. Emit one envelope per backfilled interaction with `Source="com.yourapp.transcription"` and `DetailType="EnvelopeV1.<interaction_type>"`. Existing rules forward to both consumer SQS queues. Consumer MERGE on canonical IDs is idempotent. NO new rule required.
2. **Path B — introduce a new event type (`AccountProvisioned`).** Requires adding two new rules (one per consumer repo's SQS) AND changing both consumer codepaths to accept the new wire format.

**Plan picks Path A.** Rationale:
- Path A consumes only contract surfaces already in production.
- Path B requires cross-repo coordination across THREE repos (rules + Pydantic + ingestion code in both `eq-structured-graph-core` and `action-item-graph`).
- The product intent ("downstream consumers know about the newly-anchored interactions") is achieved by Path A — the downstream MERGE-on-canonical-IDs converges the Neo4j graph correctly.
- Path A respects the cross-service-contract lesson: we DON'T emit a new wire format whose downstream contract hasn't been verified.

**Detail-type mapping (locked at design time, not deferred):** `raw_interactions.interaction_type` is a free `text` column with conventional values. The workflow's emit step MUST map `interaction_type` → DetailType against a **closed lookup table** defined in code:

```python
INTERACTION_TYPE_TO_DETAIL_TYPE = {
    "transcript": "EnvelopeV1.transcript",
    "meeting":    "EnvelopeV1.meeting",
    "note":       "EnvelopeV1.note",
    "email":      "EnvelopeV1.email",
}
```

If a signal references an interaction whose type is not in the table (e.g. `text`, `document`, an unexpected new value), the emit step RAISES (does NOT default to a synthetic DetailType). The workflow fails loud; the operator decides whether to extend the table or fix the upstream type assignment. This is the explicit anti-pattern guard against the "EnvelopeV1.text branch silently doesn't match the rule" failure mode Codex flagged.

For backfill signals where `raw_interactions` doesn't yet have a row, materialization currently writes `interaction_type='meeting'` (see `workers/materialization.py:201`). This is in the table; backfill emissions route correctly.

### 3.4 Downstream consumer Pydantic models

Read directly from sibling repos 2026-05-15:

**`action-item-graph` — `src/action_item_graph/models/envelope.py:34-43`:**

```python
class SourceType(str, Enum):
    WEB_MIC = 'web-mic'
    UPLOAD = 'upload'
    API = 'api'
    IMPORT = 'import'
    EMAIL_PIPELINE = 'email-pipeline'
    GMAIL = 'gmail'
    OUTLOOK = 'outlook'
```

**MISSING values vs. canonical set in `tasks/lessons.md:6-14`:** `zoom`, `generic`. Being fixed by `action-item-graph`'s own agent — NOT this plan's responsibility (checkpoint `phase-1.5-rethink-decided-dbos` lines 137-142).

**Path A emits `source="api"`** for backfilled signals — in the enum, NOT affected by the drift.

**Cross-check on `EnvelopeV1`** (`models/envelope.py:56-103`): `tenant_id`, `user_id`, `interaction_type` (`InteractionType` enum: `transcript | note | document | email | meeting`), `content`, `timestamp`, `source` (the enum above), optional `interaction_id`, `account_id`, `trace_id`, `extras: dict`. **`account_id` is optional**; Path A emits with `account_id` set to the resolved value. **`extras` is a free-form dict.**

**The action-item-graph downstream task brief** (`tasks/downstream/action-item-graph.md:5`) says: *"live-transcription-fastapi now sends enriched envelopes with `extras.contacts` metadata array containing `{contact_id, email, name, role}` per contact."* The consumer's pipeline reads `extras.contacts` (not just `extras.contact_ids`) to populate LLM prompts and seed owner-resolver caches. **The workflow's emit step MUST include `extras.contacts`** — see §6.6.

**`eq-structured-graph-core` — `app/models/envelope.py:23-42`:**

```python
class EnvelopeV1(BaseModel):
    schema_version: str = "v1"
    tenant_id: UUID
    user_id: str
    interaction_type: str
    content: ContentBlock
    timestamp: datetime
    source: str
    interaction_id: Optional[str] = None
    trace_id: Optional[str] = None
    account_id: Optional[str] = None
    extras: dict[str, Any] = Field(default_factory=dict)
```

**No enum constraint on `source` or `interaction_type`** — looser than action-item-graph. Path A is compatible regardless.

**The eq-structured-graph-core downstream task brief** (`tasks/downstream/eq-structured-graph-core.md:5`) similarly requires `extras.contacts`. The `_merge_contact()` MERGE uses the contact's `email`, `name`, `role` from `extras.contacts` to populate Neo4j Contact node properties. Same emit-step requirement as action-item-graph.

### 3.5 DBOS substrate contract (verified against `docs.dbos.dev` 2026-05-15)

**License:** Apache 2.0 (LICENSE file at https://github.com/dbos-inc/dbos-transact-py) — OSI-compliant strict OSS.

**Required Python:** 3.10+. Our service runs 3.11 (verified at execution time against `runtime.txt` / Python version in CI).

**Configuration fields (Python `DBOSConfig`):**

- `name: str` (required) — application name
- `system_database_url: str` (the canonical field) — connection string to the system database where DBOS stores workflow/step state. **NOTE: `database_url` is NOT the canonical field name.** Plan-v1 was wrong; v2 uses `system_database_url`.
- `application_database_url: str | None` (optional) — separate connection for legacy `@DBOS.transaction` functions; not used by our plan
- `executor_id: str | None` (optional) — unique process ID for multi-instance recovery; if not set, DBOS uses a default

**Workflow / step / identity primitives:**

- `@DBOS.workflow()` — decorator for workflow functions
- `@DBOS.step(retries_allowed: bool = False, interval_seconds: float = 1.0, max_attempts: int = 3, backoff_rate: float = 2.0)` — step decorator. **Default `retries_allowed=False`.** Plan-v1 incorrectly stated "DBOS default (3 retries, exp backoff)" as if retries were on. v2 sets `retries_allowed=True` explicitly on the steps that need it (agent call, EventBridge emit).
- `SetWorkflowID("...")` — context manager that sets the workflow ID for the workflow started inside the block. Workflow IDs must be globally unique. Plan-v1 incorrectly used a `workflow_id=` kwarg on `start_workflow_async`; v2 uses the documented `SetWorkflowID` context.
- `DBOS.start_workflow(func, input)` — start a workflow in the background; returns `WorkflowHandle`. `DBOS.start_workflow_async(...)` is the async variant.
- `DBOS.set_event` / `DBOS.get_event` — workflow-state event mechanism used for HITL and agent-replay caching.

**Workflow recovery model:**

DBOS workflows persist state to `dbos.workflow_status` on each step. On process restart, DBOS resumes in-progress workflows. In multi-instance setups, `executor_id` distinguishes instances; without explicit configuration, **the safest default for V1 is single-instance.** Plan-v2 ships V1 with `uvicorn --workers 1` (one OS process, async concurrency only). The multi-worker story is a Phase 2 scale concern with a dedicated design (see §4.3).

**Operational mode for this plan:** library only. No `dbos start`. The workflow definitions live in the FastAPI process; `DBOS.launch()` is called once at FastAPI startup; workflows execute in the same event loop as HTTP handlers.

**Connection-string discipline (Neon-specific):** Neon offers a pooled connection (port 5432 via PgBouncer) and a direct connection (no pooler). DBOS's system database MUST use a direct connection — pooler interferes with workflow state and locking. The executing session confirms which `DATABASE_URL` style Railway has wired and provisions a separate `DBOS_SYSTEM_DATABASE_URL` env var (direct connection) if needed. Tracked in §10.1.

---

## 4. Architecture overview

### 4.1 The workflow (skeleton, verified DBOS API)

```python
@DBOS.workflow()
async def account_provisioning_workflow(
    *,
    queue_id: str,
    tenant_id: str,
    approval_attempt_id: str,
    re_open_count: int,
    effort: Literal["low", "medium", "high"] = "medium",
) -> AccountProvisioningResult:
    """
    Async workflow for approving an unknown business domain.

    Steps:
    1. Re-validate queue state (read-only).
    2. Transition status 'approved' → 'creating' (idempotent guard).
    3. Call agent /api/enrich → AccountProfile (retried; run_id cached via DBOS event).
    4. Resolve account_id via account_domains (canonical idempotency anchor);
       INSERT accounts + account_domains rows if domain not yet bound.
    5. Materialize signals (contacts UPSERT, raw_interactions UPSERT,
       summaries UPSERT, links UPSERT via ON CONFLICT after migration §10.4,
       queue status → 'mapped').
    6. Emit per-interaction EnvelopeV1 events to EventBridge (one per
       backfilled interaction; closed detail-type lookup; retries on
       transient failure).
    """
```

The workflow is started via:

```python
with SetWorkflowID(f"queue-{queue_id}:approval-{approval_attempt_id}"):
    handle = await DBOS.start_workflow_async(
        account_provisioning_workflow,
        queue_id=queue_id,
        tenant_id=tenant_id,
        approval_attempt_id=approval_attempt_id,
        re_open_count=re_open_count,
        effort=effort,
    )
```

### 4.2 Why one workflow

- Steps 3 (agent network call) and 6 (EventBridge emit) are durability-load-bearing. DBOS's per-step retry + checkpoint semantics ensure each runs to completion at least once with deterministic input on replay.
- Steps 4-5 (database writes) are in the same workflow scope so workflow status reflects "did we materialize?" cleanly. Splitting would push idempotency complexity to the caller.
- The workflow ID (`SetWorkflowID(f"queue-{queue_id}:approval-{approval_attempt_id}")`) is stable across replays of the SAME approval attempt. Reopen of a queue row (which increments `re_open_count` and produces a new `approval_attempt_id` per `services/pending_account_mappings.py:61` semantics) produces a DISTINCT workflow ID. Codex's reopen-collision finding is closed.

### 4.3 In-process, single Uvicorn worker (V1)

V1 ships with `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1` (changed from `--workers 2`). Rationale:

- DBOS recovery in a multi-process deployment requires explicit `executor_id` design; without it, two processes both `DBOS.launch()`-ing against the same Postgres can compete for in-progress workflow resumption.
- Our HTTP concurrency need is modest (~100-1000 approvals/day per the volume target in checkpoint `phase-1.5-rethink-decided-dbos` D2). A single Uvicorn worker handles that with async concurrency only.
- If FastAPI request throughput becomes a bottleneck independently of the workflow, we scale horizontally with Railway replicas and assign distinct `executor_id`s. That design is documented as a Phase 2 scale concern in §15.

**FastAPI lifespan integration:** `DBOS.launch_async()` is called from the FastAPI lifespan handler. Each Uvicorn worker process (V1: only one) calls launch independently.

### 4.4 Streaming UX (the "watch the AI reason" affordance)

The workflow does NOT stream to the client directly. The HTTP `/queue/{id}/approve` route:

1. Validates auth + tenant + 404/403/409 (UNCHANGED from PR #13).
2. Synchronously reserves the queue row: `UPDATE pending_account_mappings SET status='approved', approval_attempt_id=:attempt WHERE id=:queue_id AND status='pending' AND (approval_attempt_id IS NULL OR approval_attempt_id=:attempt)` — idempotency at the route boundary, matching the PR #13 contract (Phase 1 invariants 25-30 are preserved verbatim).
3. Starts the workflow via the `SetWorkflowID` context (above).
4. Returns `202 Accepted` with `{queue_id, workflow_id, approval_attempt_id, status: "approved"}`.

The client polls a status endpoint (`GET /queue/{id}` returns the current row including `status` and lifecycle timestamps) OR connects to a separate SSE endpoint (`GET /queue/{id}/events`) that pipes DBOS workflow events to the client. **V1 ships with polling.** The SSE endpoint is a follow-up refinement; sketched in §9.1 but not in this plan's scope.

---

## 5. File-by-file plan

### 5.1 Files DELETED

- `workers/__main__.py` — both loops launched here. No longer needed.
- `workers/account_provisioning_worker.py` — polling loop replaced by workflow.
- `workers/outbox_publisher.py` — polling publisher replaced by `@DBOS.step` emit.
- `workers/advisory_lock.py` — DBOS workflow ID provides identity; no advisory lock.
- Dedicated test files for the above (enumerated during execution).

### 5.2 Files KEPT verbatim or near-verbatim

- `workers/materialization.py` (moved per §5.6) — real product logic. SQL stays. The `INSERT_OUTBOX_SQL` write at lines 230-251 is REMOVED. The in-memory `linked_pairs` dedup at line 161 is REMOVED and replaced by `ON CONFLICT DO NOTHING` after the §10.4 migration.
- `routers/queue_actions.py` — `/map` and `/ignore` keep their current implementation. `/approve` is refactored (§5.3).
- `services/queue_authorization.py` — auth helper unchanged.
- `services/domain_classification.py`, `services/name_resolution.py`, `services/account_lookup.py`, `services/pending_account_mappings.py` — Phase 1 services, unchanged.
- `services/aws_event_publisher.py` — synchronous boto3 EventBridge wrapper used by the existing transcript pipeline. KEPT, but the workflow's emit step calls `asyncio.to_thread(publisher.put_events, ...)` to bridge sync to the workflow's async context. Sync boto3 from inside an async workflow without the `to_thread` bridge blocks the event loop (Codex P2 finding).

### 5.3 Files REFACTORED

- `services/agent_action_core_client.py` — body rewrite. New contract:

  ```python
  class AgentActionCoreClient:
      async def enrich(
          self,
          *,
          url: str,
          effort: Literal["low", "medium", "high"] = "medium",
          jwt: str,
      ) -> AccountProfile:
          """POST /api/enrich?stream=false → returns AccountProfile."""

      async def get_run(self, *, run_id: str, jwt: str) -> AccountProfile:
          """GET /api/enrich/{run_id} → returns the recorded AccountProfile."""
  ```

  `AccountProfile` is a local Pydantic model defined in `services/account_provisioning/types.py`. A contract-pinning test (§7.2) asserts on the live `?stream=false` response.

- `routers/queue_actions.py` `/approve` — new body. Two-phase: reserve the row first, then start the workflow.

  ```python
  @router.post("/queue/{queue_id}/approve", status_code=202)
  async def approve(queue_id: str, body: ApproveRequest, request: Request):
      ctx = get_auth_context_polling(request)
      async with get_async_session() as session:
          async with session.begin():
              row = await _load_and_authorize(session, queue_id, ctx)  # 401/403/404/409 unchanged
              # Reserve synchronously (HTTP-layer idempotency on approval_attempt_id).
              reserved = await session.execute(APPROVE_SQL, {
                  "queue_id": queue_id,
                  "attempt_id": body.approval_attempt_id,
              })
              if reserved.rowcount == 0:
                  raise HTTPException(...)
              # Re-read for the workflow's input (incl. re_open_count).
              fresh = (await session.execute(SELECT_QUEUE_SQL, {"queue_id": queue_id})).one()
      # Workflow starts AFTER commit. SetWorkflowID makes start idempotent on
      # replays of the same (queue_id, approval_attempt_id).
      workflow_id = f"queue-{queue_id}:approval-{body.approval_attempt_id}"
      with SetWorkflowID(workflow_id):
          handle = await DBOS.start_workflow_async(
              account_provisioning_workflow,
              queue_id=str(queue_id),
              tenant_id=ctx.tenant_id,
              approval_attempt_id=body.approval_attempt_id,
              re_open_count=fresh.re_open_count,
          )
      return {
          "queue_id": str(queue_id),
          "workflow_id": workflow_id,
          "approval_attempt_id": body.approval_attempt_id,
          "status": "approved",
      }
  ```

  Critical: the row's `status` is `'approved'` AFTER the route returns. The workflow's Step 2 transitions `'approved'` → `'creating'`. Phase 1 invariants 25-30 (canonical UUID validators, status filters, idempotency) are preserved. `_load_and_authorize` and the SQL constants are reused as-is.

### 5.4 New files

- `services/dbos_runtime.py` — owns `DBOS` initialization and FastAPI lifespan.

  ```python
  from contextlib import asynccontextmanager
  from dbos import DBOS, DBOSConfig

  _CONFIG = DBOSConfig(
      name="live-transcription-fastapi",
      system_database_url=os.environ["DBOS_SYSTEM_DATABASE_URL"],
      # executor_id intentionally unset — V1 is single-instance.
  )

  @asynccontextmanager
  async def dbos_lifespan(app):
      DBOS(config=_CONFIG)
      await DBOS.launch_async()
      try:
          yield
      finally:
          await DBOS.destroy_async()
  ```

  Wired into `main.py` as the FastAPI `lifespan`.

- `services/account_provisioning/__init__.py`, `workflow.py`, `steps.py`, `types.py`, `eventbridge_emit.py`, `materialization.py` (moved from `workers/`).

- `tests/unit/account_provisioning/test_workflow.py`, `test_steps.py`, `test_eventbridge_emit.py`, `test_agent_client.py` — see §7.

- `tests/contract/test_agent_enrich_response_shape.py` — contract-pinning test for the agent's `?stream=false` response.

- `tests/integration/account_provisioning/test_workflow_e2e.py` — full workflow against a Neon test branch + mocked agent HTTP.

- `tests/integration/account_provisioning/test_reopen.py` — reopen-path E2E (Codex P3 finding).

- `tests/integration/account_provisioning/test_crash_recovery.py` — simulate crash points (mid-agent-call, mid-EventBridge-emit) and assert DBOS resumes correctly.

### 5.5 Disposition of `account_provisioning_outbox`

**The table is DROPPED via Prisma migration (§10.4).** Rationale changed from plan-v1:

- The table was an artifact of the polling-publisher pattern. Without the publisher, it has no consumer.
- Plan-v1 proposed repurposing it as an audit log with a fabricated `outbox_row_id` column — but that column does not exist in the live schema, and adding it requires a migration anyway. If we're doing a migration, dropping the table is cleaner than retrofitting it.
- DBOS provides workflow observability via `dbos.workflow_status` and `dbos.operation_outputs`. Operational queries for "how many account-provisioning workflows ran today, p95 duration, retry rate" all run against the DBOS state tables.
- Should we need application-level audit (e.g., to power a queue UI listing of recent emissions), we add a purpose-built table at that time — but that's not Phase 1.5 scope.

### 5.6 Module reorganization

Move `workers/materialization.py` → `services/account_provisioning/materialization.py`. Update the import in `routers/queue_actions.py` (used by `/map` for inline materialization). Update tests accordingly.

Rationale: `workers/` is being emptied. Materialization is product logic, not "worker plumbing." Keep it under `services/account_provisioning/` co-located with the workflow.

After this move, the `workers/` directory contains only `__pycache__/` and `__init__.py`; the executing session may delete the entire directory.

---

## 6. The workflow in detail

### 6.1 Step contract (explicit retry policy per step)

Per `@DBOS.step(retries_allowed=..., max_attempts=..., interval_seconds=..., backoff_rate=...)` semantics:

| Step | Side effect | Idempotency mechanism | Retry policy |
|------|-------------|----------------------|--------------|
| 1. `revalidate_queue_state` | Read-only SQL | Read-only | `retries_allowed=False` (default) |
| 2. `transition_to_creating` | UPDATE on `pending_account_mappings` | `WHERE status = 'approved'` makes UPDATE a no-op on replay | `retries_allowed=False` (idempotent SQL; let it surface) |
| 3. `call_agent_enrich` | HTTP POST to eq-agent-action-core; OR GET if run_id cached via `DBOS.get_event` | `run_id` cached in workflow event; on retry GET-by-run_id | `retries_allowed=True, max_attempts=5, interval_seconds=2.0, backoff_rate=2.0` |
| 4. `resolve_or_create_account` | INSERT into `accounts` (no ON CONFLICT — see §6.4); INSERT into `account_domains` with ON CONFLICT (tenant_id, domain) DO NOTHING; SELECT to fetch resolved account_id | The `account_domains` unique key is the idempotency anchor | `retries_allowed=True, max_attempts=3` (DB transient failures only) |
| 5. `materialize_signals` | Calls `materialize_account_approval()` — contacts UPSERT, raw_interactions UPSERT, summaries UPSERT, links INSERT with ON CONFLICT (after §10.4 migration), queue status → mapped | All SQL ON CONFLICT-driven; queue update no-op on `status='mapped'` | `retries_allowed=True, max_attempts=3` |
| 6. `emit_eventbridge_events` | EventBridge `put_events` per backfilled interaction | At-least-once delivery; consumer-side MERGE-on-canonical-IDs is the dedup mechanism | `retries_allowed=True, max_attempts=5, interval_seconds=2.0, backoff_rate=2.0` |

Steps 4 and 5 retry on DB transient failures only (e.g., connection drops). Programming errors (missing column, type mismatch) propagate to workflow failure as designed (Item 3 in test-discipline-gaps).

### 6.2 Workflow ID strategy

`workflow_id = f"queue-{queue_id}:approval-{approval_attempt_id}"`. Replaying `/approve` with the same `approval_attempt_id` for the same queue_id returns the existing workflow handle (DBOS deduplicates by ID via `SetWorkflowID`).

Reopen-path correctness: reopen sets `archived_at=NULL`, increments `re_open_count`, and (per the TODO at `services/pending_account_mappings.py:55-62`) eventually resets `approval_attempt_id=NULL`. A second `/approve` after reopen supplies a new `approval_attempt_id`, producing a distinct workflow ID. No collision with the prior approval's workflow execution.

Phase 1 HTTP-layer idempotency (`approval_attempt_id` persisted in `pending_account_mappings.approval_attempt_id` per Phase 1 invariants 25-30) is preserved — the route reserves the row synchronously before the workflow starts.

### 6.3 Workflow vs. step boundaries

Workflows are pure orchestration; steps own side effects. The workflow function reads no external state directly. All I/O is inside a step. This is a DBOS discipline that keeps replays deterministic.

### 6.4 Step 4 — account creation (corrected idempotency anchor)

`account_domains` is the canonical surface (Phase 1 invariant 15 — the same lesson the 2026-05-15 silent regression was about).

```python
@DBOS.step(retries_allowed=True, max_attempts=3)
async def resolve_or_create_account(*, tenant_id: str, domain: str, profile: AccountProfile) -> str:
    """Returns account_id. Domain-keyed idempotency."""
    async with get_async_session() as session:
        async with session.begin():
            # First: does the domain already resolve?
            existing = await session.execute(
                text("SELECT account_id::text FROM account_domains "
                     "WHERE tenant_id=:tenant AND lower(domain)=lower(:domain)"),
                {"tenant": tenant_id, "domain": domain},
            )
            row = existing.one_or_none()
            if row:
                return row.account_id

            # New account. Insert accounts + account_domains in one txn.
            account_id = str(uuid.uuid4())
            await session.execute(
                INSERT_ACCOUNT_SQL,  # explicit id; no ON CONFLICT
                {"id": account_id, "tenant_id": tenant_id,
                 "name": profile.name, "industry": profile.industry, ...},
            )
            inserted = await session.execute(
                text("INSERT INTO account_domains (id, tenant_id, account_id, domain, created_at) "
                     "VALUES (gen_random_uuid(), :tenant, :account, lower(:domain), NOW()) "
                     "ON CONFLICT (tenant_id, domain) DO NOTHING RETURNING account_id::text"),
                {"tenant": tenant_id, "account": account_id, "domain": domain},
            )
            domain_row = inserted.one_or_none()
            if domain_row is None:
                # Race: another concurrent provisioning won the domain insert.
                # Roll back our account insert and re-resolve.
                await session.rollback()
                resolved = await session.execute(
                    text("SELECT account_id::text FROM account_domains "
                         "WHERE tenant_id=:tenant AND lower(domain)=lower(:domain)"),
                    {"tenant": tenant_id, "domain": domain},
                )
                return resolved.one().account_id
            return account_id
```

Race-safety: two simultaneous workflows for the same `(tenant_id, domain)` (extremely unlikely given the `pending_account_mappings.(tenant_id, domain)` unique index keeping the queue serialized, but possible if domains differ only in case) — one wins the domain insert; the other sees the conflict and resolves to the winner's account_id. Both workflows' downstream Steps 5+6 materialize against the same account_id. No orphaned accounts (the loser's account row is rolled back).

The agent-returned `AccountProfile.name` is used for the `accounts.name` insert; no domain-based uniqueness on `accounts.name` is required.

### 6.5 Step 5 — materialization (replay-safe at SQL layer)

`materialize_account_approval()` from the moved `services/account_provisioning/materialization.py`:

- Inputs: `tenant_id`, `queue_id`, `account_id`, `event_type` (kept for signature compatibility; workflow passes `"account_created"`).
- Behavior unchanged from `workers/materialization.py` EXCEPT:
  - `INSERT_OUTBOX_SQL` write (lines 230-251) REMOVED. Outbox table is dropped.
  - In-memory `linked_pairs` set (line 161) REMOVED.
  - `INSERT_LINK_SQL` (line 82) changed from bare `INSERT` to:

    ```sql
    INSERT INTO interaction_contact_links (link_id, interaction_id, contact_id)
    VALUES (gen_random_uuid(), :summary_id, :contact_id)
    ON CONFLICT (interaction_id, contact_id) DO NOTHING
    ```

    This requires the §10.4 Prisma migration (UNIQUE INDEX on `(interaction_id, contact_id)`). The migration MUST land BEFORE the workflow first runs in production.
- Cross-account contact reassignment still raises `ValueError` (existing behavior; Phase 3 scope).
- Zero-signals still raises `ValueError` (existing behavior).

The function still takes an open session from the caller and runs in one transaction. DBOS @step caches the step's return value on success; on replay, ON CONFLICT semantics guarantee SQL re-execution is idempotent.

### 6.6 Step 6 — EventBridge emit (Path A, with `extras.contacts`)

```python
@DBOS.step(retries_allowed=True, max_attempts=5, interval_seconds=2.0, backoff_rate=2.0)
async def emit_eventbridge_events(
    *,
    materialization: MaterializationResult,
    tenant_id: str,
) -> list[EmissionRecord]:
    emissions = []
    for interaction_id in materialization.interaction_ids:
        interaction = await _fetch_interaction(interaction_id)  # raw_interactions row + contacts

        # Closed lookup; raises on unknown type (per §3.3).
        detail_type = INTERACTION_TYPE_TO_DETAIL_TYPE[interaction.interaction_type]

        envelope = EnvelopeV1(
            schema_version="v1",
            tenant_id=tenant_id,
            user_id=str(interaction.user_id) if interaction.user_id else tenant_id,
            interaction_type=interaction.interaction_type,
            content=ContentBlock(text=interaction.raw_text or "", format="plain"),
            timestamp=interaction.created_at,
            source="api",
            interaction_id=str(interaction_id),
            account_id=materialization.account_id,
            extras={
                "contact_ids": [c.contact_id for c in interaction.contacts],
                "contacts": [
                    {
                        "contact_id": c.contact_id,
                        "email": c.email,
                        "name": c.display_name,
                        "role": c.role,
                    }
                    for c in interaction.contacts
                ],
                "account_provisioning_queue_id": materialization.queue_id,
            },
        )

        entry = {
            "Source": "com.yourapp.transcription",
            "DetailType": detail_type,
            "Detail": envelope.model_dump_json(),
            "EventBusName": os.environ.get("EVENTBRIDGE_BUS_NAME", "default"),
        }
        # Bridge sync boto3 into the async workflow loop.
        response = await asyncio.to_thread(boto3_eventbridge.put_events, Entries=[entry])
        if response.get("FailedEntryCount", 0) > 0:
            raise EventBridgeEmissionError(json.dumps(response.get("Entries", [])))
        emissions.append(EmissionRecord(interaction_id=interaction_id))
    return emissions
```

**Locked contract guarantees:**
- `Source="com.yourapp.transcription"` — matches BOTH live rules.
- `DetailType` from the closed lookup — every emission's DetailType is in BOTH rule filters; unknown types FAIL LOUD.
- `source="api"` in the envelope body — present in `action-item-graph` SourceType enum AND accepted by `eq-structured-graph-core`'s loose `source: str`.
- `account_id` populated.
- **`extras.contacts` populated** with `{contact_id, email, name, role}` per downstream brief at `tasks/downstream/action-item-graph.md:33-46` and `tasks/downstream/eq-structured-graph-core.md:33-36`. Plan-v1 emitted only `contact_ids`; v2 emits both.

### 6.7 Concurrency control (DBOS queue, V1 default)

Approvals can burst during onboarding (D2 in the rethink). To bound concurrent workflow execution and avoid contending with HTTP requests:

```python
APPROVAL_QUEUE = Queue("account-provisioning", concurrency=5)
```

`/approve` route's `start_workflow_async` is replaced with `APPROVAL_QUEUE.enqueue_async(...)` after the row-reservation step. The queue serializes execution at up to 5 concurrent workflows. Tunable per traffic patterns observed in production.

For V1 a `concurrency=5` cap is well above expected steady-state (100-1000/day → max ~10/hour average) but bounded enough to prevent runaway during a burst.

### 6.8 Workflow versioning (deploy-time discipline)

DBOS recovery is sensitive to step-order changes across deploys. If a workflow with steps [A, B, C] is in flight and a deploy lands code with steps [A, B, X, C], DBOS may resume against the new code and skip step X (because the workflow's checkpoint records A, B done) — producing semantic drift.

**Discipline:**
- Step names (the decorated function names) are NEVER renamed or reordered without a coordinated drain: pause new approvals at the queue route, wait for in-flight workflows to drain via `DBOS.workflow_status`, then deploy.
- Adding a NEW step at the END of a workflow is safe — in-flight workflows complete with the old shape; new workflows pick up the new step.
- Adding a step in the MIDDLE is unsafe; treat as a workflow-name change (e.g., `account_provisioning_workflow_v2`) and run both in parallel until v1 drains.

This discipline is documented as part of the Phase 1.5 ship checklist (§13).

---

## 7. Test-discipline expectations addressed per-component

Per the five expectations in `tasks/downstream/test-discipline-gaps-2026-05-15.md`:

### 7.1 Expectation 1: Live-schema verification at design time

**Status:** done at design time. §3.1 cites every column the new code reads or writes, and every unique index the code's `ON CONFLICT` clauses depend on — probed via `information_schema.columns` AND `pg_indexes` against the live Neon project on 2026-05-15.

**Execution-time discipline:** before any new SQL the executing session writes, re-run the probe. If the schema has drifted, update the plan and re-probe. `scripts/verify_schema.py` (§10.4) ships alongside this work.

### 7.2 Expectation 2: Real-substrate coverage for in-service primitives

For each new `@DBOS.step` function:
- A unit test against a **real test session** (SQLAlchemy session bound to a Neon test branch). NOT a `MagicMock` for the session, NOT a `patch("services.account_provisioning.steps.fn", AsyncMock(...))` import-level mock.
- A unit test that asserts on the SQL text (catches mock-only coverage gaps; mirrors `test_sql_queries_account_domains_not_accounts` from the 2026-05-15 fix).
- An integration test that drives the workflow end-to-end against a Neon test branch, with the agent service mocked at the HTTP boundary.

**Required test files (listed in §5.4):**
- `tests/unit/account_provisioning/test_steps.py`
- `tests/unit/account_provisioning/test_workflow.py`
- `tests/integration/account_provisioning/test_workflow_e2e.py`
- `tests/contract/test_agent_enrich_response_shape.py` — pinning test for the agent's `?stream=false` response (§3.2 finding)
- `tests/integration/account_provisioning/test_reopen.py` — reopen-path coverage (Codex P3)
- `tests/integration/account_provisioning/test_crash_recovery.py` — DBOS crash-recovery (Codex P3)

### 7.3 Expectation 3: Per-branch E2E coverage

The workflow has fan-out at Step 5 (per-signal materialization) and Step 6 (per-interaction emission). E2E exercises each branch.

**Production E2E extensions to `/tmp/e2e_phase_1_production.py`** — adds at least 6 cases (final count 26+):

- **APPROVE → workflow success (single-signal queue):** seed `pending_account_mappings` + 1 signal; call `/queue/{id}/approve`; poll until `status='mapped'`; assert `accounts` row + `account_domains` row + `contacts` row + `interaction_contact_links` row + `dbos.workflow_status` row in `success` state. Inspect EventBridge emission via CloudTrail (or via a synthetic SQS consumer in test mode).
- **APPROVE → workflow success (multi-signal queue):** 3+ signals → 3+ contacts + 3+ EventBridge emissions.
- **APPROVE → replay safety:** call `/approve` twice with same `approval_attempt_id`; assert workflow runs once (`dbos.workflow_status` has one row; materialization rows are not duplicated).
- **APPROVE → agent transient failure:** mock the agent to fail twice then succeed; assert workflow completes with one successful materialization (DBOS retried Step 3).
- **REOPEN → re-approve:** seed an archived queue row, simulate re-open (`archived_at=NULL`, `re_open_count=1`); call `/approve` with a NEW `approval_attempt_id`; assert a SECOND workflow ID runs end-to-end and the row reaches `mapped`. Codex P3 finding closed.
- **MAP / IGNORE happy paths:** unchanged from PR #13; included for regression coverage.

Per `tasks/downstream/test-discipline-gaps-2026-05-15.md` Item 2 acceptance criteria — the BUSINESS+known / BUSINESS+unknown / PERSONAL / INTERNAL ingestion branches stay separately tracked and add four more cases. Final E2E count after Item 2 + this plan: 30+.

### 7.4 Expectation 4: Narrow exception handling

DBOS provides retry semantics; we do NOT wrap step bodies in broad `try/except Exception:`. The deferred lesson from Phase 1 (`services/transcript_enrichment.py:399-405` broad except — Item 3 in test-discipline-gaps) is explicitly NOT repeated in this code.

Rules:
- No `except Exception:` inside any `@DBOS.step` function body.
- The agent client catches `httpx.HTTPStatusError`, `httpx.TimeoutException`, `httpx.NetworkError` narrowly and translates to typed `AgentEnrichTransientError` (workflow retries via DBOS) vs. `AgentEnrichTerminalError` (workflow fails-loud).
- The EventBridge emit step catches `botocore.exceptions.ClientError` narrowly and propagates others.
- `/review` checklist updates ship in M4 (§11).

### 7.5 Expectation 5: Cross-service contract verification at design time

§3 of this plan IS this expectation in document form. Every cross-service contract is cited with the live artifact:
- Neon schema (§3.1) — `information_schema.columns` + `pg_indexes` 2026-05-15
- Agent OpenAPI (§3.2) — `/openapi.json` 2026-05-15
- EventBridge rules (§3.3) — `aws events describe-rule` 2026-05-15
- Consumer Pydantic models (§3.4) — file:line refs to live repo state 2026-05-15
- DBOS substrate (§3.5) — `docs.dbos.dev` 2026-05-15

`scripts/verify_consumer_contracts.py` (§10.4) ships alongside this work.

---

## 8. Idempotency analysis

Three layers, top-to-bottom:

### Layer 1: HTTP idempotency

`approval_attempt_id` (UUIDv4, client-supplied) is persisted in `pending_account_mappings.approval_attempt_id` BY THE ROUTE (synchronously, before workflow start). Replays with the same body return 202 with the same workflow_id; replays with a different attempt_id for an already-approved row return 409. UNCHANGED from PR #13 semantically.

### Layer 2: Workflow idempotency

`workflow_id = f"queue-{queue_id}:approval-{approval_attempt_id}"` via `SetWorkflowID`. DBOS deduplicates workflow starts by ID. Reopen produces distinct attempt_ids → distinct workflow IDs.

### Layer 3: Step-side side-effect dedup

Per step:
- Step 2 SQL update guard (`WHERE status = 'approved'`) → no-op on replay
- Step 3 agent call → `run_id` cached via `DBOS.set_event` after first call; on retry the step calls `GET /api/enrich/{run_id}` (subject to §10.1 coordination on response shape parity)
- Step 4 `account_domains.(tenant_id, domain)` unique key → canonical anchor; race-loser rolls back
- Step 5 materialization → all SQL ON CONFLICT-driven after §10.4 migration; queue update has status filter
- Step 6 EventBridge emit → at-least-once delivery; consumer-side MERGE-on-canonical-IDs is the dedup mechanism (Phase 1 invariant)

**Consumer-side dedup is the load-bearing surface for cross-service idempotency.** The plan does NOT add an application-level publication ledger (plan-v1 proposed this; v2 drops it because the table doesn't exist with a usable schema and DBOS `workflow_status` covers operational observability).

---

## 9. Phase 2 / Phase 3 compounding (sketch)

### 9.1 Phase 2 — Identity state machine + progressive enrichment

Phase 2 introduces a `contacts.identity_state` enum (`shell | emerging | partial | resolved | verified`) and re-enrichment workflows. DBOS primitives that compound:

- A periodic workflow (manually started via cron from outside DBOS, OR via DBOS Queues with delayed enqueue) scans `contacts` for state transitions due (e.g., contacts in `partial` whose `last_enriched_at` is older than 30 days). `@DBOS.scheduled` exists but its Python-binding deprecation status is flagged in current DBOS docs — Phase 2 uses Queues + delayed enqueue as the supported alternative.
- Workflow-per-contact for re-enrichment; reuses the same agent client; same idempotency primitives.
- `DBOS.send` / `DBOS.recv` for progressive-enrichment workflows that wait for external signals (e.g., a new email arrives → wake up the contact's enrichment workflow).
- The SSE streaming UX endpoint sketched in §4.4 builds on `DBOS.workflow_events` (the same mechanism that backs `set_event`/`get_event`) — when V2 wants to stream agent reasoning to the front-end, that's the substrate.

### 9.2 Phase 3 — Conflict resolution + HITL

Phase 3 introduces multi-step decision workflows with human-in-the-loop:

- `@DBOS.workflow` for conflict-resolution policy (e.g., same email, different account history).
- `DBOS.get_event` for HITL: workflow blocks on `await DBOS.get_event(workflow_id, "human_decision")`; UI surfaces the conflict; on user action, a route calls `DBOS.set_event(workflow_id, "human_decision", decision)`; workflow resumes.
- `DBOS.sleep(timedelta)` for "escalate to admin after 48h" semantics — no separate cron, no separate queue.

### 9.3 The compounding argument

A single DBOS install + a single Neon Postgres + a single set of test-discipline expectations carries Phase 1.5, Phase 2, and Phase 3 without architecture re-litigation. Adding scheduled re-enrichment or HITL conflict resolution is feature-flag work, not new infrastructure.

---

## 10. Cross-repo coordination tasks (identified — NOT executed in this plan)

### 10.1 eq-agent-action-core

- Publish `AccountProfile` schema in `components.schemas` of `/openapi.json`. Currently missing; response 200 is bare `{}`. Without it, our contract-pinning test is the only guard against silent agent-side breaking changes.
- Declare a security scheme on `/api/enrich` (Bearer token). Currently undocumented.
- Confirm `GET /api/enrich/{run_id}` returns the full AccountProfile after run completion (needed for §6.4 replay strategy). If it doesn't, the workflow's replay falls back to re-running the expensive agent call once per crash — correctness preserved, cost increased.

### 10.2 action-item-graph

- `SourceType` enum at `src/action_item_graph/models/envelope.py:34-43` is missing `zoom` and `generic`. Already in flight by that repo's agent. This plan does NOT depend on the fix — Path A emits `source="api"` for backfilled signals, which is already in the enum.

### 10.3 eq-structured-graph-core

- No coordination needed for the workflow itself. The consumer's `EnvelopeV1` accepts loose `source: str` and `interaction_type: str`.
- Cross-track: `tasks/downstream/eq-structured-graph-core.md` describes Contact-node MERGE work that depends on `extras.contacts`; the workflow emits this metadata per §6.6.

### 10.4 eq-frontend (Prisma schema owner)

Two Prisma migrations are required BEFORE the M3 cutover (§11):

- **Migration A — add UNIQUE INDEX on `interaction_contact_links (interaction_id, contact_id)`.** Replaces the in-memory `linked_pairs` dedup with SQL-level idempotency. Coordinate via `eq-frontend` PR.
- **Migration B — DROP the `account_provisioning_outbox` table.** Replaced by `dbos.workflow_status` for observability. Coordinate via the same `eq-frontend` PR.

Both migrations are tracked via the existing schema-ownership process (`reference_prisma_schema_ownership.md` memory).

### 10.5 Tooling (this repo)

- `scripts/verify_schema.py` (Item 4 in test-discipline-gaps) — ships with M4.
- `scripts/verify_consumer_contracts.py` (Item 5 in test-discipline-gaps) — ships with M4.
- `/review` skill checklist updates (Items 4 + 5) — ship with M4.

### 10.6 Railway operational (this service)

- `DBOS_SYSTEM_DATABASE_URL` env var — direct (non-pooler) Neon connection for DBOS's system database. Set in Railway BEFORE M1 deploy.
- `--workers 1` change in the start command (from `--workers 2`). Set in Railway BEFORE M1 deploy.
- DBOS admin port (default 3001) is NOT exposed externally; either bind to `127.0.0.1` or disable via DBOS config.

### 10.7 Dependency manifest

This repo uses `requirements.txt` (NOT `pyproject.toml`). Adding `dbos-transact-py` happens in `requirements.txt`. The executing session pins a version and commits the lockfile changes per existing convention.

---

## 11. Sequencing (milestones, each a self-contained PR)

### Milestone 0 — Operational prep (pre-flight, no code change)

- Provision `DBOS_SYSTEM_DATABASE_URL` env var in Railway (direct Neon connection).
- Change Railway start command to `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`.
- Verify both via Railway dashboard. No application change.

### Milestone 1 — Substrate install (1 PR)

- Add `dbos-transact-py` to `requirements.txt` with a pinned version.
- Add `services/dbos_runtime.py` with the `DBOS` singleton + FastAPI lifespan.
- Wire the lifespan into `main.py`. FastAPI boots with DBOS launched; no workflows defined.
- Production deploy is safe (no behavior change).
- Acceptance: `uvicorn` boots locally with no errors; `dbos.workflow_status` table exists in the Neon test branch; live deploy shows DBOS schema created in production Neon; `--workers 1` start command is in effect.

### Milestone 2 — Prisma migrations (cross-repo PR in eq-frontend)

- Migration A — UNIQUE INDEX on `interaction_contact_links (interaction_id, contact_id)`.
- Migration B — DROP `account_provisioning_outbox`.
- Apply to Neon eq-dev; verify no application-side regression (no current code path reads or writes the outbox table; the unique index is new and any pre-existing duplicate rows must be cleaned first — assess via `SELECT (interaction_id, contact_id), COUNT(*) ... GROUP BY ... HAVING COUNT(*) > 1` and remediate if any).
- Acceptance: migrations applied cleanly; `pg_indexes` shows the new unique index; outbox table is gone; existing test suite still passes.

### Milestone 3 — Workflow definition + tests (1 PR in live-transcription-fastapi)

- Add `services/account_provisioning/` (new package): `workflow.py`, `steps.py`, `types.py`, `eventbridge_emit.py`, `materialization.py` (moved from `workers/`).
- Remove `INSERT_OUTBOX_SQL` from materialization. Replace in-memory `linked_pairs` with `ON CONFLICT DO NOTHING` on the new unique index.
- Rewrite `services/agent_action_core_client.py` to the new contract.
- Add all unit + integration + contract-pinning tests per §7.
- DO NOT wire the workflow into any route yet. Workflow is dead code at end of M3.
- Acceptance: all new tests pass against the Neon test branch; existing 286-test suite still passes; contract-pinning test passes against the live production agent.

### Milestone 4 — Queue route cutover (1 PR)

- Update `routers/queue_actions.py` `/approve` to reserve synchronously + start workflow via `SetWorkflowID`.
- DELETE `workers/__main__.py`, `workers/account_provisioning_worker.py`, `workers/outbox_publisher.py`, `workers/advisory_lock.py`, and their dedicated tests.
- Mark `tasks/downstream/railway-phase-1-5-worker.md` as superseded.
- Production deploy: the FastAPI service is the only thing changing.
- Acceptance:
  - Extended production E2E passes ≥26/26 (including reopen-path + crash-recovery).
  - Manual workflow run via the UI completes end-to-end.
  - Live Railway logs show `dbos.workflow_status` rows for each completed workflow.
  - One real-tenant approval flows end-to-end with the expected Neo4j MERGE behavior visible in `eq-structured-graph-core`'s consumer.

### Milestone 5 — Verified-contract tooling + checklist updates (1 PR)

- Ship `scripts/verify_schema.py` and `scripts/verify_consumer_contracts.py`.
- Update `/review` skill checklist with the "Cross-service contracts" + "Live schema probe" sections (Items 4 + 5 in test-discipline-gaps).
- Acceptance: scripts work against our SQL primitives + live agent OpenAPI + live EventBridge rules. Checklist updated and exercised on the next PR landing after merge.

### Milestone 6 — Operational backfill (only if needed)

If `pending_account_mappings.status='approved'` entries exist at cutover, manually run the workflow for each via a one-shot script using `DBOS.start_workflow_async` directly. At-most-once via the `workflow_id` rule.

### Cutover discipline

Each milestone is shippable independently. M3 is dark (unreachable code) until M4 lands. If M4 reveals a bug, revert; the rest of the system keeps working with workflows installed but idle.

---

## 12. Rollback plan

If M4 ships and a critical bug surfaces:

1. **Forward-fix preferred.** The legacy polling worker is deleted; reverting M4 leaves `/approve` setting `status='approved'` with no consumer. Workflows in `dbos.workflow_status` in `pending` / `running` states must be hand-resolved.
2. **Pre-cutover canary:** before M4 ships, run a synthetic queue entry through the workflow manually (insert via Neon MCP, start workflow via DBOS API, assert end-to-end). This proves the workflow works in production state before any traffic depends on it.
3. **Emergency operator pathway:** if production approvals are blocked, operators directly call `DBOS.start_workflow_async` (via a small admin endpoint, gated by an internal-only auth check) to push affected queue entries through. Hotfix afterwards.

The "true rollback" (back to polling worker) is intentionally NOT supported — it would require resurrecting deleted files and the Railway worker container.

---

## 13. Acceptance criteria (the ship gate)

### Milestone 0
- [ ] `DBOS_SYSTEM_DATABASE_URL` set in Railway with a direct Neon connection.
- [ ] Railway start command is `--workers 1`.

### Milestone 1
- [ ] `dbos-transact-py` pinned in `requirements.txt`.
- [ ] FastAPI boots locally + in Railway production with `DBOS.launch_async()` succeeding.
- [ ] `dbos.workflow_status` (and friends) visible in production Neon.
- [ ] Existing 286-test suite still passes.

### Milestone 2
- [ ] `interaction_contact_links (interaction_id, contact_id)` UNIQUE INDEX exists.
- [ ] `account_provisioning_outbox` table dropped.
- [ ] No duplicate `(interaction_id, contact_id)` pairs remain (verified before constraint creation).

### Milestone 3
- [ ] All `@DBOS.step` functions covered by real-substrate unit tests against a Neon test branch.
- [ ] Contract-pinning test passes against the live production agent.
- [ ] Materialization no longer writes outbox; no in-memory link-dedup.
- [ ] `/map` route's import updated; `/map` integration tests still pass.

### Milestone 4
- [ ] `/approve` returns 202 with `{queue_id, workflow_id, approval_attempt_id, status: "approved"}`.
- [ ] `workers/` directory effectively empty (only `__init__.py` and maybe a placeholder).
- [ ] Production E2E passes ≥26 cases including reopen-path + crash-recovery + multi-worker startup test.
- [ ] Production canary completes end-to-end.
- [ ] `dbos.workflow_status` shows correct workflow IDs in `success` state.
- [ ] At least one real-tenant approval visible in downstream Neo4j (`Account` node MERGE + `Contact` MERGE with `extras.contacts`-populated properties).
- [ ] Codex review on the M4 diff is PASS.

### Milestone 5
- [ ] `scripts/verify_schema.py` works against an arbitrary SQL query and surfaces missing-column errors at design time.
- [ ] `scripts/verify_consumer_contracts.py` validates an envelope source/detail-type against live EventBridge rules + downstream Pydantic models.
- [ ] `/review` checklist updated and exercised on the first post-merge PR.

---

## 14. Test-discipline expectations — per-component checklist

| Component | Item 1 (schema probe) | Item 2 (real-substrate test) | Item 3 (E2E branch) | Item 4 (narrow except) | Item 5 (cross-service probe) |
|-----------|----------------------|----------------------------|---------------------|----------------------|----------------------------|
| `services/dbos_runtime.py` | n/a (no SQL) | n/a (lifecycle) | M1 boot test (single + multi-attempt) | DBOS owns errors | n/a (in-process) |
| `services/account_provisioning/workflow.py` | n/a (orchestration only) | M3 integration tests | M4 workflow E2E + replay + reopen | No `except Exception:` | §3 covers all crossings |
| `services/account_provisioning/steps.py` | §3.1 cites all schemas read/written | M3 unit tests against real session | M4 per-step assertions | Narrow `except` per step | §3.2/3.3/3.4 cite contracts |
| `services/account_provisioning/eventbridge_emit.py` | n/a (no SQL) | M3 unit test against consumer Pydantic | M4 E2E with real rule | Narrow `except botocore` | §3.3/3.4 |
| `services/agent_action_core_client.py` (rewritten) | n/a (no SQL) | M3 contract-pinning test | M4 E2E with real agent | Narrow HTTP exceptions | §3.2 |
| `services/account_provisioning/materialization.py` (moved) | §3.1 + §10.4 migration | Existing real-session tests + new SQL-text assertion | M4 multi-signal + reopen cases | Already narrow | §3.4 (no cross-service surface) |
| `routers/queue_actions.py` `/approve` (refactored) | n/a (no new SQL) | Existing route tests | M4 E2E approve replay | Existing narrow handling | n/a |
| `services/aws_event_publisher.py` (reused) | n/a | Existing tests | M4 via emit step | n/a (existing narrow) | §3.3 |

---

## 15. Open questions handed to execution

Items this plan does NOT decide; the executing session resolves and records:

1. **DBOS version pin.** The executing session pins the latest stable at install time and records it in the M1 commit message.
2. **Postgres connection-string convention.** `DBOS_SYSTEM_DATABASE_URL` must be a direct connection per §3.5. The executing session confirms Railway has the direct URL available and records the env var convention.
3. **Workflow-side AccountProfile validation.** Until §10.1 ships, `AccountProfile` is observed-not-declared. The contract-pinning test is the load-bearing guard; any agent-side change to the response fails it loudly.
4. **DBOS admin port exposure.** Bound to `127.0.0.1` or disabled. Executing session picks; records in M1.
5. **DBOS Queues concurrency cap.** §6.7 picks `concurrency=5`. Executing session validates against observed traffic and tunes if needed.
6. **Multi-replica future plan.** Out of Phase 1.5 scope. When request throughput drives a scale-out, the executing session at that time designs `executor_id` allocation and recovery handoff.
7. **`tasks/lessons.md` Phase 2 `validation_status` schema-debt migration** (per design doc Section 7.4) — not Phase 1.5 scope; flagged for Phase 2 planning.

---

## 16. Why this plan is shippable

- **No new service on Railway.** Same FastAPI container, same Postgres, same EventBridge.
- **No new infrastructure dependency.** DBOS + Neon Postgres is the whole substrate.
- **OSS-strict.** Apache 2.0. Aligned with the user's hard constraint.
- **Operationally bounded.** Failure modes: Postgres unavailable (system-wide), EventBridge unavailable (DBOS retries), agent unavailable (DBOS retries with run_id-replay).
- **Compounds.** Phase 2 + 3 work on the same substrate.
- **Disciplined.** Every contract probed; every component mapped to all 5 test-discipline expectations; reopen-path covered; crash-recovery covered; cross-service contract gaps (action-item-graph SourceType, agent OpenAPI's missing AccountProfile, EventBridge source mismatch) surfaced in §3+§10.

---

## 17. Pre-commit checklist (per milestone)

Before opening each milestone's PR:

- [ ] All §3 contracts re-probed; section updated if anything drifted.
- [ ] All §7 / §13 test files for this milestone written and passing locally.
- [ ] No `except Exception:` in any file under `services/account_provisioning/`.
- [ ] Codex review on the diff (per `tasks/lessons.md` "Real /codex review is non-substitutable").
- [ ] Production E2E run against the deployed service (per `tasks/lessons.md` "Production E2E with a Railway-signed JWT is non-substitutable").
- [ ] `MEMORY.md` status string updated to reflect the milestone shipped.

---

## 18. References

- `docs/superpowers/specs/2026-05-15-initiative-context-snapshot.md` — initiative entry point + 30 hard invariants
- `docs/superpowers/specs/2026-05-15-async-orchestration-rethink-brief.md` — rethink decision process (Step 8)
- `docs/superpowers/research/2026-05-15-durable-execution-landscape.md` — 2026 substrate landscape
- `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` — design doc (Section 7.2 + 8.5 revised by this session's Deliverable 2)
- `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` — Phase 1 plan (shipped) + Phase 1.5 main scope (superseded by this plan)
- `tasks/lessons.md` — bottom umbrella lessons
- `tasks/downstream/test-discipline-gaps-2026-05-15.md` — 5 expectations
- `tasks/downstream/action-item-graph.md` — consumer change brief (extras.contacts requirement)
- `tasks/downstream/eq-structured-graph-core.md` — consumer change brief (Contact node MERGE)
- `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/20260515-095506-phase-1.5-rethink-decided-dbos.md` — D1–D7 decision record
- `/tmp/codex-dbos-plan-consult-output.md` — Codex consult on plan-v1 (REVISE verdict; corrections folded into v2)
- DBOS docs: https://docs.dbos.dev/ (accessed 2026-05-15)
- DBOS source: https://github.com/dbos-inc/dbos-transact-py (accessed 2026-05-15)

---

## 19. End-of-plan note

This document is the load-bearing artifact of session 2026-05-15. The executing session inherits this plan; if anything drifts between this plan and reality at execution time, the executing session updates the plan (with a dated revision in §20) BEFORE shipping.

---

## 20. Revision history

### v2 — 2026-05-15 (post-Codex consult)

Codex consult on v1 returned REVISE verdict. Substrate decision (DBOS) confirmed sound. The following P1/P2/P3 findings were folded in:

**P1 corrections:**
- §4.4 + §5.3: `/approve` now reserves the row synchronously (`status='approved' + approval_attempt_id`) BEFORE starting the workflow. v1 attempted to push status responsibility into the workflow while keeping the workflow's `WHERE status IN ('approved', 'creating')` guard — internally contradictory.
- §4.1 + §6.2: workflow_id is `f"queue-{queue_id}:approval-{approval_attempt_id}"`, NOT bare `queue_id`. Reopen produces distinct attempt_ids → distinct workflow IDs. v1's bare queue_id collided with the reopen lifecycle in `services/pending_account_mappings.py:55-62`.
- §3.5 + §5.4: DBOS Python API corrected — `SetWorkflowID` context manager (not `workflow_id=` kwarg); `system_database_url` config field (not `database_url`).
- §6.1: `@DBOS.step` retry policy explicit per step. v1 stated "DBOS default (3 retries)" — the actual default is `retries_allowed=False`.
- §4.3 + §10.6: V1 runs `--workers 1`. v1 retained `--workers 2` without addressing executor-id / multi-instance recovery. The multi-worker story is deferred to Phase 2 scale work.
- §6.5 + §10.4: `interaction_contact_links` replay-safety moved to SQL via a coordinated UNIQUE INDEX migration. v1 relied on in-memory `linked_pairs` dedup, which is replay-broken across step re-execution.
- §5.5 + §10.4: `account_provisioning_outbox` is DROPPED, not retrofitted. v1 referenced an `outbox_row_id` column that doesn't exist (schema fiction).
- §6.4: Account creation idempotency keyed on `account_domains.(tenant_id, domain)`, the canonical surface. v1 keyed on `accounts.name`, which has no unique index in the live schema.

**P2 corrections:**
- §5.2 + §6.6: sync boto3 bridged via `asyncio.to_thread` inside the async workflow.
- §6.7: DBOS Queue with concurrency cap added for flow control.
- §3.5 + §10.6: Neon direct-connection requirement documented; `DBOS_SYSTEM_DATABASE_URL` introduced.
- §3.4 + §6.6: emit `extras.contacts` (full metadata) AND `extras.contact_ids`. v1 emitted only `contact_ids`, silently degrading downstream Contact-node and LLM-prompt enrichment.
- §3.3: `INTERACTION_TYPE_TO_DETAIL_TYPE` lookup is a CLOSED table with fail-loud semantics on unknown types. v1 deferred the `EnvelopeV1.text` branch to execution.
- §9.1: `@DBOS.scheduled` deprecation flagged; Phase 2 alternative noted.
- §6.8: workflow versioning discipline added.

**P3 corrections:**
- §3.1: schema probe extended to include `raw_interactions` columns Step 6 reads (`raw_text`, `created_at`, `user_id`) AND `pg_indexes` for all affected unique constraints. v1's §3.1 didn't cover read-only columns or unique-index inventory.
- §7.3 + §13: reopen-path E2E added.
- §7.3 + §13: crash-recovery test added.
- §13: multi-worker / single-worker startup test added.
- §10.7: dependency manifest is `requirements.txt`, not `pyproject.toml`. v1 referenced `pyproject.toml` repeatedly.

**Kept from v1 (Codex confirmed sound):**
- DBOS as the substrate decision.
- Account creation in this repo; agent as research-only.
- Killing the `com.eq.contact-quality` / `AccountProvisioning.*` EventBridge contract (dead-on-arrival).
- `/map` stays inline (no workflow).
- "Verified contracts" section as cultural requirement.
- At-least-once semantics for external side effects; downstream dedup as the truth.

### v1 — 2026-05-15

Initial draft. Pre-Codex consult.
