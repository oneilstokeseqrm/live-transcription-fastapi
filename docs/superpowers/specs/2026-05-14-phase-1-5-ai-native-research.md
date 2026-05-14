# Phase 1.5 — AI-Native Thought-Leadership Research Note

**Status:** Research input for Phase 1.5 plan execution
**Author:** Subagent (Task 1.5.0)
**Date:** 2026-05-14
**Branch:** `feat/contact-quality-phase-1-5`
**Scope:** Influence three open design decisions before the queue worker, outbox publisher, and idempotency model are committed in code.

---

## TL;DR (read this if nothing else)

Three open decisions on the table. My recommendations, evidence-backed below:

1. **Worker location → live-transcription-fastapi as a new async worker task in the same OS process as the API, not a separate service, not a scheduled job in eq-email-pipeline.** Reason: the worker's hot path is calling `eq-agent-action-core` (an HTTP call from a service that already has Postgres + agent-core wired up) and doing a Postgres transaction. Spinning up a new service is premature; eq-email-pipeline is the wrong owner because its scope is mail ingestion, not identity resolution. live-transcription-fastapi already owns the queue-write side of transcript-driven signals and is the natural home for the queue-read side.

2. **Outbox publisher placement → separate asyncio task inside the SAME OS process as the worker, NOT a separate scheduled job and NOT a separate service (yet).** Reason: at our scale (one tenant, low signal volume in Phase 1.5), the operational cost of two processes is real and the win of separation is theoretical. Co-located publisher with horizontal-scale-out plan documented for when we cross the threshold (>5 replicas). 2025 production guidance is clear: co-host until scale forces separation.

3. **Idempotency key granularity → KEEP `worker_attempt_id = f"queue-{queue_id}"` (stable per queue, NOT per invocation).** Reason: idempotency keys belong to business intent, not transport attempts. The agent's `/api/enrich` call is the business operation "materialize this queue entry's account." Replay must produce the same result. A per-invocation key would make every worker retry look like a new request to the agent, defeating idempotency. The literature (Morling, microservices.io, AWS) is unanimous on this. The plan's current choice is correct; codify it explicitly.

The rest of this document is the evidence trail.

---

## 1. Sources surveyed

1. **[Microservices Pattern: Transactional outbox (microservices.io / Chris Richardson)](https://microservices.io/patterns/data/transactional-outbox.html)** — canonical reference for outbox + relay separation; foundational for any saga-style durability discussion.
2. **[Transactional Outbox Pattern: A Practical Guide to Trade-offs (softwarecraftsperson.com, 2025-10)](https://www.softwarecraftsperson.com/posts/2025-10-08-transactional-outbox-pattern/)** — recent (Oct 2025) practical writeup explicitly addressing same-process-vs-separate-worker for the outbox relay, including the threshold where co-hosting breaks (>5 replicas with polling-based relay).
3. **[Transactional Outbox Pattern: From Theory to Production (npiontko.pro, 2025-05)](https://www.npiontko.pro/2025/05/19/outbox-pattern)** — production-deployment perspective; covers monitoring, archival, and scaling considerations.
4. **[AWS Prescriptive Guidance — Transactional outbox pattern](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)** — authoritative AWS pattern doc; covers polling-publisher topology and replay safety.
5. **[On Idempotency Keys — Gunnar Morling](https://www.morling.dev/blog/on-idempotency-keys/)** — definitive piece on idempotency-key granularity. The "keys belong to business intent, not transport attempts" framing comes from here.
6. **[Microservices Pattern: Idempotent Consumer](https://microservices.io/patterns/communication-style/idempotent-consumer.html)** — companion pattern; defines the canonical dedup-at-mutation-boundary discipline.
7. **[KARMA: Leveraging Multi-Agent LLMs for Automated Knowledge Graph Enrichment (arXiv 2502.06472)](https://arxiv.org/html/2502.06472v2)** — 2025 multi-agent KG enrichment architecture; relevant because our `eq-agent-action-core` enrich pipeline is structurally a single-agent variant of KARMA's nine-agent ontology-bounded approach.
8. **[AgREE: Agentic Reasoning for Knowledge Graph Completion on Emerging Entities (arXiv 2508.04118)](https://arxiv.org/html/2508.04118v1)** — directly addresses the problem of resolving entities the graph has not seen before (exactly our "unknown business domain → propose new account" flow).
9. **[LLM-empowered knowledge graph construction: A survey (arXiv 2510.20345)](https://arxiv.org/abs/2510.20345)** — Oct 2025 survey covering the broader landscape; useful for sanity-checking that our approach is on-trend.
10. **[Microsoft GraphRAG (microsoft.github.io/graphrag) + 2026 practitioner guides](https://microsoft.github.io/graphrag/)** — entity-resolution-as-bedrock principle (covered below).

---

## 2. Key findings

### Finding 1 — Idempotency keys MUST encode business intent, not transport attempts

This is the single most important finding for our open decision #3.

Morling's piece states the rule directly: "Idempotency keys belong to business intent, not transport attempts, and every key should be scoped by tenant and operation. The dedup decision should be persisted atomically with the mutation, or the design is wrong."

The microservices.io idempotent-consumer pattern says the same thing in different words: the consumer must persist `(idempotency_key → outcome)` atomically with the side-effect. The implication for us:

- The business operation is "materialize the account for queue entry X."
- Every worker invocation that processes the same queue entry — first attempt, retry after a transient agent timeout, replay after a worker crash — is THE SAME business operation.
- The idempotency key the worker passes to `eq-agent-action-core` must be stable across all those attempts.
- Therefore: `worker_attempt_id = f"queue-{queue_id}"` is the correct shape. Per-invocation keys (a fresh UUID each time the worker picks up the row) would break this.

The current plan in `2026-05-12-contact-quality-initiative-design.md` Section 5.4 step 5 uses the phrase "worker_attempt_id as the agent-side idempotency key (so the agent treats duplicate calls for the same `worker_attempt_id` as the same request)." That's the right semantics. The plan's later reference to `f"queue-{queue_id}"` is consistent. **No change needed; just make it explicit in the implementation plan that the key is stable, not per-attempt.**

A practical refinement worth adding: scope the key by tenant explicitly. `worker_attempt_id = f"tenant-{tenant_id}:queue-{queue_id}"` — costs nothing, prevents cross-tenant key collisions if the agent's idempotency store is ever globally scoped.

### Finding 2 — Outbox publisher co-location is fine for our scale; separation is a scale-out concern, not a correctness concern

The softwarecraftsperson.com Oct 2025 piece is the most useful source here because it directly addresses the choice. The summary:

- For small-scale systems with low traffic or a limited number of replicas (typically fewer than five), co-hosting the relay process within the main API/worker service is standard and works well.
- As the service scales horizontally to 10, 15, or 30 replicas, polling-based relays in every replica cause overlapping queries and operational waste. At that point, separation is justified.
- The CDC-based alternative (Debezium 2.5+) sidesteps the polling problem entirely by tailing the WAL, but adds operational dependencies (Kafka Connect or equivalent).

We are at one tenant, one worker replica, one outbox table. Co-locating the publisher as a separate asyncio task in the same OS process as the worker:

- Shares the same DB connection pool (operational simplicity).
- Shares the same observability surface (one log stream, one healthcheck).
- Allows the publisher to wake immediately after a worker transaction commits (via an asyncio Event or a Postgres LISTEN/NOTIFY) — lower latency than a scheduled cron job.
- Costs nothing to refactor into a separate process later, because the publisher's interface is well-defined: it reads `account_provisioning_outbox WHERE published_at IS NULL`, calls EventBridge, marks rows published.

AWS Prescriptive Guidance reinforces this: their default topology is the polling-publisher pattern with no opinion on process boundary — the boundary is an operational choice, not a correctness one.

The npiontko.pro production writeup is useful for what it warns about: monitor outbox table growth and add archival from day one. Our plan should include an archival policy for `account_provisioning_outbox` (e.g., delete rows where `published_at < NOW() - INTERVAL '30 days'`) even though we're starting at low volume.

### Finding 3 — Service-boundary decisions in agentic systems follow the data, not the function

This is the framing for open decision #1 (worker location). Recent agentic-KG work (KARMA, AgREE, the LLM-empowered KG construction survey) consistently treats the entity-resolution agent as a stateless service that is invoked by whichever upstream process owns the trigger. In KARMA's nine-agent architecture, the orchestrator is the process that owns the source-document ingestion; the entity-resolution agent is a callable.

Mapped onto our system:

- **Trigger ownership:** the queue is written by two upstream sources — transcript enrichment (live-transcription-fastapi) and email ingestion (eq-email-pipeline).
- **Resolution ownership:** the agent (`eq-agent-action-core`) is the stateless resolver.
- **Worker ownership:** the question is which OS process owns the polling-and-orchestration loop that pulls `status='approved'` rows from the queue, calls the agent, and commits the materialization transaction.

The literature does not directly answer "which service should own the worker" — but it strongly suggests the worker is just a thin orchestrator and should live where the data it operates on lives. The queue table lives in the shared Postgres (per the design doc + MEMORY.md `reference_contacts_architecture.md`). Both live-transcription-fastapi and eq-email-pipeline can read it. So the decision is operational, not architectural.

Two practical filters:

- **live-transcription-fastapi already has all the dependencies wired**: agent-core HTTP client, Postgres async session, observability, EventBridge publisher (or close kin). eq-email-pipeline is scheduled-job-shaped, not long-running-worker-shaped.
- **Phase 2's progressive enrichment worker (Section 4.2 of the design doc) is also a long-running async worker.** Putting the Phase 1.5 worker in live-transcription-fastapi sets the precedent and avoids a future "let's move the worker to its proper home" refactor.

**Recommendation: live-transcription-fastapi.** Codify it in the plan.

### Finding 4 — Entity-resolution-as-bedrock is the load-bearing GraphRAG insight

From the Microsoft GraphRAG production guidance and the 2026 practitioner guides: "If one document says 'J. Smith' and another says 'John Smith,' the graph must know they are the same node. Failure to implement entity resolution leads to fragmented graphs and missed connections."

Why this matters for Phase 1.5: the queue-and-approval workflow IS our entity resolution layer for accounts. Every architectural choice in Phase 1.5 should be evaluated against "does this make the entity-resolution layer more reliable, or less?" Two implications:

- The atomic materialization transaction (Section 5.6 of the design doc) is the right shape — contacts and interaction links materialize together with the queue-status transition, so the entity-resolution decision is committed as a single point-in-time fact.
- The outbox row is the durable record of "this entity resolution happened." Downstream Neo4j (eq-structured-graph-core) MERGEs by canonical IDs, so duplicate event delivery converges. This is exactly the entity-resolution-as-bedrock discipline applied at the event boundary.

The principle to keep front-of-mind in implementation review: **never let the worker write contacts/interaction-links without also writing the outbox row in the same transaction.** That coupling is the load-bearing invariant.

### Finding 5 — Agentic resolution of "emerging entities" is a recognized pattern; we're on-trend

AgREE (arXiv 2508.04118, 2025) describes exactly our problem class: "agent-powered framework that combines strategic search and action planning to dynamically construct KG triplets for emerging entities."

Translation to our system: when a queue entry references an unknown domain (an emerging entity), the agent's 5-step pipeline (URL canonicalization → query generation → Tavily research → reflection → AccountProfile generation) is structurally identical to AgREE's "strategic search + action planning" for KG completion. The fact that we have a human-approval gate before invoking the agent is a deliberate quality-control choice that AgREE-style fully-automated systems don't have — and is appropriate for our domain (B2B accounts where false positives have real cost).

The takeaway: our pipeline architecture is aligned with current research direction. No course correction needed; just keep the human-approval gate explicit and resist any temptation in future phases to make the agent's account creation fully autonomous without surfacing in the queue.

---

## 3. Recommendations for Phase 1.5 plan

Restated as concrete actions for the next implementation session:

### 3.1 Worker location: live-transcription-fastapi

Add to the Phase 1.5 plan, in the task that creates the worker module:

- Worker lives at `live-transcription-fastapi/app/workers/account_provisioning_worker.py` (or similar — match existing naming).
- Worker is started as a long-running asyncio task from the FastAPI app's startup event (or a separate ASGI lifespan handler), not a one-shot script.
- Worker uses the existing async Postgres session factory.
- Worker uses the existing `eq-agent-action-core` HTTP client (or creates one in the same style as the existing transcript-enrichment caller).
- Worker is replay-safe per Section 5.4 idempotency contracts.

**Why not eq-email-pipeline:** scope mismatch. eq-email-pipeline owns mail ingestion; account provisioning is identity resolution, not mail processing.

**Why not a new dedicated service:** premature. Three deploy targets are already in play (live-transcription-fastapi, eq-email-pipeline, eq-agent-action-core). Adding a fourth right now is operational cost without a corresponding scale-out benefit. Re-evaluate when worker throughput becomes a measurable concern.

### 3.2 Outbox publisher: separate asyncio task in the same OS process as the worker

Add to the Phase 1.5 plan:

- Publisher is an asyncio task started alongside the worker from the same FastAPI lifespan handler.
- Publisher polls `account_provisioning_outbox WHERE published_at IS NULL ORDER BY created_at` every N seconds (N = 2 to start; tune later).
- Publisher uses the SAME Postgres connection pool as the worker (operational simplicity).
- Publisher writes to EventBridge using the existing publisher abstraction.
- Publisher marks rows `published_at = NOW(), publish_attempts = publish_attempts + 1` on success; on failure, increments `publish_attempts` and records `last_publish_error` (already in schema per Section 5.4).
- Add archival: a daily scheduled task deletes `account_provisioning_outbox` rows where `published_at < NOW() - INTERVAL '30 days'`. Same task can run from the same process.
- Document the scale-out threshold in the plan: "If we reach >5 worker replicas OR observe >1000 outbox rows/day, split the publisher into its own process or migrate to CDC."

**Why same-process, not separate scheduled job:** lower latency (publisher can react to commit notifications), simpler observability, lower operational footprint. The separation has no correctness benefit at our current scale and trades implementation simplicity for nothing.

**Why not in-process inside the worker transaction:** absolutely do not do this. The publisher MUST run after the worker's transaction commits. If the publisher's EventBridge call is in the same transaction, a publish failure rolls back the materialization, which is exactly the failure mode the outbox pattern is designed to prevent. The outbox table exists precisely because the DB transaction and the network publish are decoupled.

### 3.3 Idempotency key granularity: keep stable-per-queue, codify the discipline

The plan's current `worker_attempt_id = f"queue-{queue_id}"` is correct. Reinforcements:

- Update the plan to explicitly state: "The `worker_attempt_id` is STABLE across worker invocations for the same `queue_id`. The agent treats two calls with the same `worker_attempt_id` as the same logical request, returning a deterministic result."
- Scope by tenant: change to `worker_attempt_id = f"{tenant_id}:queue-{queue_id}"`. Trivial change, prevents any future cross-tenant key collision.
- Add an integration test (already in Section 7.2 acceptance: "idempotent repeated calls with same `worker_attempt_id`"): explicitly verify that two separate worker invocations on the same queue row produce the same `account_id` from the agent — that's the proof the agent is honoring the idempotency contract.
- Document the parallel principle for the OTHER two idempotency keys:
  - `approval_attempt_id` is per-Approve-click (frontend-generated UUID), persisted on the queue row. This is per-attempt because the business intent is "the user clicked approve once" — distinct from worker replay.
  - `outbox_row_id` as the EventBridge idempotency key is per-event-emission. Each outbox row is one event; the row's UUID is the event's identity. Duplicate EventBridge delivery is handled by downstream MERGE-everywhere on canonical IDs (per the MEMORY.md `project_neo4j_contact_architecture.md` invariant).

The three keys live at different layers because they encode three different business intents. The plan should make this explicit so future readers don't conflate them.

### 3.4 Cross-cutting: add an explicit "advisory lock granularity" decision

While reading Section 5.4 step 3 carefully, I noticed the plan says `pg_try_advisory_xact_lock(queue_id_hash)` without specifying how `queue_id_hash` is computed. This is a small but important detail:

- `pg_try_advisory_xact_lock` takes a `bigint`. The queue_id is a UUID. The hash must be deterministic across worker invocations or the lock is meaningless.
- Recommended: `pg_try_advisory_xact_lock(hashtext(queue_id::text))` or `pg_try_advisory_xact_lock(('x' || substr(md5(queue_id::text), 1, 16))::bit(64)::bigint)`.
- Document the chosen approach in the plan as a one-line note; otherwise the implementer will pick one and reviewers won't catch it.

This is technically out of scope for the three open decisions, but it's a place the plan can be tightened with no cost.

---

## 4. Open questions (to be answered by production behavior)

1. **Publisher latency target.** The plan should set an SLO: "outbox rows published to EventBridge within X seconds of commit." Without a target, the polling interval is arbitrary. Suggest 5 seconds p95 as a starting point; measure and adjust.

2. **Worker poll interval vs. LISTEN/NOTIFY.** The plan defaults to polling (`pg_try_advisory_xact_lock` implies a polling loop). LISTEN/NOTIFY on the queue's `status` column would reduce latency and DB load at our scale. Worth evaluating during implementation; not a blocker for Phase 1.5.

3. **What happens when the agent is down for hours.** The plan should specify: does the worker retry indefinitely, or does it move queue rows to a `creating_failed` state after N attempts? Currently Section 5.4 implies indefinite retry. A circuit-breaker or max-attempts policy may be warranted. Production behavior will reveal whether this matters.

4. **Outbox archival sizing.** 30-day retention is a guess. Once we have production volume data, revisit.

5. **Multi-replica safety in the future.** The advisory-lock approach is correct for one worker replica. For >1 replica, we need to verify the lock truly serializes (it does, but the test should be written). Phase 1.5 ships at one replica; document the multi-replica test as a Phase 2 prerequisite.

6. **Agentic-KG research influence.** KARMA's nine-agent ontology-bounded extraction is more sophisticated than our single-agent enrich pipeline. Not a Phase 1.5 concern, but worth flagging for Phase 2 design: if account-profile quality becomes a measured weakness, the agent could be split into a multi-agent committee (URL canonicalizer + researcher + profile-builder + validator) along KARMA lines. Not now. Just noting the literature points in that direction if/when we need it.

---

## 5. Summary for the implementation plan author

Three decisions, three sentences:

- Worker lives in `live-transcription-fastapi`, started as an asyncio task from the FastAPI lifespan handler.
- Outbox publisher is a sibling asyncio task in the same OS process, polling every 2s, with a documented scale-out threshold.
- Idempotency key is stable per `(tenant_id, queue_id)` and explicitly documented as such; the three idempotency keys in the system (`approval_attempt_id`, `worker_attempt_id`, `outbox_row_id`) encode three distinct business intents and must not be conflated.

Everything else in the Phase 1.5 plan stays as written. The literature backs the architectural choices the design doc already makes; this research note's purpose was to convert the "TBD during plan-writing" notes in Section 7.2 into committed decisions.
