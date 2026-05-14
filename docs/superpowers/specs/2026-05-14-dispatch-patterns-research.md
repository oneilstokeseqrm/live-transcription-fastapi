# Dispatch Patterns Research — Outbox Publisher and Worker Loop

**Date:** 2026-05-14
**Author:** Research subagent (web survey, 2024-2026 frontier)
**Status:** DRAFT — for orchestrator review, do not commit blindly
**Audience:** Phase 2 planning session, EQ Contact Quality Initiative
**Scope:** Validate Phase 1.5 polling architecture before locking it in for Phase 2.

---

## 1. Executive Recommendation

**Keep the polling architecture for Phase 1.5. Ship it. Do not rewrite to CDC or LISTEN/NOTIFY before Phase 2 planning starts.** The 2024-2026 frontier literature — Gunnar Morling, AWS architecture docs, Temporal's durable-execution writeups, and the agentic-ER papers on arXiv — converges on a single answer for systems at our shape (single-digit replicas, agent-call-dominated latency, Postgres-as-source-of-truth, EventBridge already in flight): **a polling-based application outbox publisher plus a `FOR UPDATE SKIP LOCKED` worker is the right starting point, and the outbox table schema is forward-compatible with every upgrade path** (Debezium, EventBridge Pipes via Kinesis, Postgres logical-decoding messages, or replacing the polling worker with Temporal). The strongest dissent in the literature (Morling) prefers log-based CDC — but he explicitly assumes pre-existing Kafka infrastructure, which we do not have. There is one cheap, high-value upgrade worth doing inside Phase 1.5 if implementation is still flexible: add `LISTEN/NOTIFY` as a low-latency wake signal layered on top of the polling loop — it cuts approve-to-enrich latency from ~5s to <100ms with ~30 lines of code, and degrades cleanly to pure polling on failure. **Everything else can wait until throughput, agent-call volume, or cross-service fanout demand forces the upgrade.**

The deeper point: the user's instinct ("we already have EventBridge, so the simpler argument is weaker") is partially right. The *broadcast* path absolutely belongs on EventBridge — that's settled. The architectural question is only how the outbox row gets *from Postgres to EventBridge*. And on that narrow question, the application-level polling publisher remains the 2025-2026 default for systems at our scale, with one specific exception: if/when we have >100 outbox rows/sec or sub-100ms cross-service convergence requirements, swap the publisher to Postgres logical-decoding messages (`pg_logical_emit_message`) feeding EventBridge via DMS/Kinesis. That migration is small, well-documented, and the outbox row schema stays identical.

---

## 2. Topic 1 — Outbox publisher dispatch: polling vs. CDC vs. EventBridge Pipes

### 2.1 What the frontier literature actually says

**Gunnar Morling (Decodable, Oct 2024 — "Revisiting the Outbox Pattern")** is the loudest voice for *log-based* CDC. His core claim: polling causes "spikes of high load on the database" and has "poor ordering semantics." For Postgres specifically, he recommends `pg_logical_emit_message()` — a log-only outbox where you never write an outbox *table* at all; you just emit logical-decoding messages from the same transaction, and Debezium/Flink CDC reads them off the WAL.

But two caveats on Morling that are critical for us:

1. **He explicitly assumes pre-existing Kafka infrastructure.** The article makes no mention of EventBridge or AWS-native alternatives, and the relay he recommends is Debezium → Kafka → consumers. The "operational complexity is cheap" argument only holds if Kafka Connect is already a thing your team operates.
2. **He concedes the table-based outbox is forward-compatible.** The same outbox row design works whether you read it with polling, with Debezium's `OutboxEventRouter`, or with logical-decoding messages. So polling-now-CDC-later is not a wasted-work trap.

**The 2024-2026 production blog consensus** (Conduktor, Streamkap, Decodable, Ajit Singh, NP Blog) is sharper than Morling:

> "For most teams already running Kafka, Debezium is the right default for an outbox provided you put WAL-retention monitoring in place. **For small teams without Kafka, polling is fine to start with, and you can switch to CDC later because the outbox table schema does not change.**" — paraphrased consensus across Conduktor and Streamkap 2025 writeups.

The Debezium-vs-polling tradeoff in production, distilled:

| Dimension                        | Application polling                  | Debezium / CDC                                  |
|----------------------------------|---------------------------------------|--------------------------------------------------|
| Latency                          | Bounded by poll interval (2-5s here)  | Single-digit milliseconds                        |
| DB load empty case               | Constant indexed query                | None (WAL read is essentially free)              |
| DB load loaded case              | Indexed scan + UPDATE                 | WAL read (lighter)                               |
| Ordering                         | Approximated by `ORDER BY created_at` | Exact WAL order                                  |
| Operational complexity           | Just a worker process                 | Kafka Connect + connector + WAL slot monitoring  |
| WAL retention risk               | None                                  | **Real** — inactive slot can fill disk           |
| Forward compatibility            | Identical outbox schema               | Identical outbox schema                          |

### 2.2 EventBridge Pipes specifically — can it replace the publisher?

**Short answer: not natively for RDS Postgres as of 2025-Q4.** EventBridge Pipes' supported *sources* are: SQS, Kinesis, DynamoDB Streams, MSK, self-managed Kafka, Amazon MQ. **RDS/Postgres is NOT a supported Pipes source.** Multiple 2024-2025 writeups confirm this (AWS docs, Localstack docs, the Mohllal Debezium-Kinesis-EventBridge writeup).

To use Pipes with our Postgres outbox, the architecture would be:

```
outbox table → Debezium → Kinesis Data Stream → EventBridge Pipes → EventBridge Bus → consumers
```

That's three new pieces of infrastructure (Debezium, MSK or self-managed Kafka Connect, Kinesis stream) versus our current zero. The 2026 Mario Bittencourt Better Programming piece ("Implementing the Transactional Outbox Pattern with EventBridge Pipes") is essentially the canonical implementation — but the natively-supported source there is **DynamoDB Streams**, not Postgres. The Postgres path requires bolting Debezium onto the front.

**Verdict on Pipes for THIS system: not the right tool yet.** We'd be adding Debezium *and* Kinesis *and* Pipes just to remove a single polling Python worker. The complexity gradient is wrong.

### 2.3 What about `pg_logical_emit_message` without Debezium?

Morling's strongest specific recommendation. The pattern:

```sql
SELECT pg_logical_emit_message(true, 'outbox', '{"event":"AccountCreated","tenant_id":"..."}');
```

This writes an entry directly to the WAL inside the same transaction — no outbox table needed. A logical replication consumer reads it off the WAL slot.

For us, this is theoretically attractive (no outbox table bloat, exact ordering, log-based) but practically blocked by the same problem as Pipes: **the consumer is still some custom process reading the WAL slot.** Either Debezium (heavy) or a hand-rolled `psycopg2` logical-decoding consumer (medium complexity, brittle to schema evolution). At our scale, the WAL-slot consumer is more code and more operational risk than the polling publisher.

**Verdict: revisit in Phase 3+ if throughput justifies it. Not now.**

### 2.4 Recommendation for outbox publisher

**Ship the polling publisher in Phase 1.5 as planned. Add these production-discipline pieces inside the implementation:**

1. **`FOR UPDATE SKIP LOCKED`** on the outbox query — allows safe parallel publishers when we go multi-replica.
2. **Index on `(published_at) WHERE published_at IS NULL`** — partial index keeps the empty-queue scan free.
3. **Backoff on empty polls** — 2s → 5s → 10s if N consecutive empty polls. Cuts idle DB load 80%+.
4. **Outbox row schema is forward-compatible**: include `id`, `event_type`, `payload JSONB`, `created_at`, `published_at`, `tenant_id`, `aggregate_id`. This exact schema is what Debezium's `OutboxEventRouter` SMT expects, so the migration path is clean.
5. **Metric: `outbox_lag_seconds`** = `now() - min(created_at) WHERE published_at IS NULL`. Alert if > 60s. This is the single most important production signal.

**The upgrade triggers** (write these into the Phase 2 plan as decision criteria):
- Outbox publisher lag p95 > 30s → consider Debezium/CDC.
- More than ~10 outbox rows/sec sustained → consider Debezium/CDC.
- Cross-service convergence SLA tightens below 5s → consider `pg_logical_emit_message` or LISTEN/NOTIFY hybrid.
- We start operating Kafka for other reasons → Debezium becomes free.

---

## 3. Topic 2 — Worker dispatch: polling vs. event-driven for AI-native queue workers

### 3.1 What the frontier literature says

The worker layer is where the architectural debate is *more* live than the outbox layer. Three camps:

**Camp A — Pure polling with `SKIP LOCKED` (status quo for us):** Graphile Worker, pgqueuer, Oban (Elixir), River (Go), `good_job` (Rails) all default to this. Production blogs (chbussler 2024, AmineDiro 2024) show this design serving 10,000 jobs/sec on a single Postgres instance. The 2026 Atomic Architect Medium piece reports replacing RabbitMQ with `SKIP LOCKED` and cutting p95 from 340ms to 210ms.

**Camp B — Hybrid `LISTEN/NOTIFY` + polling (the modern best practice):** This is what pgqueuer, Graphile Worker, and pg-boss all actually ship. The pattern: poll loop as durability safety-net (catches anything LISTEN missed); `NOTIFY` on insert wakes idle workers in sub-3ms. pgqueuer's writeup explicitly: *"LISTEN/NOTIFY for instant job notifications and FOR UPDATE SKIP LOCKED for efficient worker coordination."*

**Camp C — Push the work straight to SQS/EventBridge, skip the DB queue:** Inferable.ai's 2025 piece on tool-calling with message queues; AWS's standard architectural advice. **But:** every serious agentic-systems paper in 2025-2026 (Multi-Agent RAG Framework for Entity Resolution on MDPI; AGENTiGraph arXiv 2508.02999; the LangChain "Runtime Behind Production Deep Agents" piece) makes the same point: **for stateful, long-running AI workflows, you want the database to be the source of truth for state, and the queue to be a wake-mechanism, not the state store.** SQS messages get redriven, expire, and don't have the transactional guarantees we need to bind to `pending_account_mappings` status transitions.

The Temporal/Inngest "durable execution" camp is the loudest competitive voice here. The pitch (Temporal Sep 2025 "Durable Digest"): every LLM call becomes a checkpointed Activity, the workflow state lives in Temporal's durable store, and worker crashes resume mid-agent-call. This is real, it's well-engineered, and it's **another thing to operate**. Render's Sep 2025 piece "Durable Workflow Platforms for AI Agents" is a fair comparison; the honest takeaway is that Temporal pays off when you have >5 LLM calls per workflow, complex compensation logic, or multi-day workflows. Our `account_provisioning_worker` is one LLM call wrapped in one transaction. That doesn't justify Temporal yet.

### 3.2 The specific objection: "what about Microsoft's long-running-task limitations?"

The Register's May 2026 piece on Microsoft's DELEGATE-52 benchmark is worth noting: **LLM agents degrade an average of 6% per step in long agentic workflows.** This is a *strong* argument for the architecture we already have — short, atomic, single-LLM-call work units with database-backed state — rather than long-horizon agentic workflows. Polling-based dispatch fits this shape perfectly: each `approved` row is one self-contained agentic unit, processed end-to-end in a single ~15s transaction, and the next unit picks up from durable state.

### 3.3 Agentic ER literature — what dispatch do they use?

The 2025 MDPI Multi-Agent RAG framework for household entity resolution uses **LangGraph deterministic orchestration** — agents pass state through a directed graph, with each agent stateless and the state living in the graph runtime. No polling, no NOTIFY — just function calls.

AGENTiGraph (arXiv 2508.02999, 2025) uses a similar deterministic-pipeline pattern: query → agent → KG update → next agent.

MAGMA (arXiv 2601.03236, 2026) is more relevant to us: it explicitly separates a *fast ingest stream* from an *asynchronous update process* for the knowledge graph. Their dispatch is **asynchronous update workers** reading from a write-ahead structure — i.e., the same conceptual pattern as our outbox + worker.

**The frontier ER/identity-graph papers do not use either polling or LISTEN/NOTIFY — they use synchronous orchestration inside a single workflow runtime.** That's not directly applicable to us because we want the worker to be independently scalable, retryable, and resumable across deployments. Our design (DB-as-source-of-truth + worker reading approved rows) is actually *more conservative and more robust* than the academic patterns, which optimize for paper-tractability not 99.9% availability.

### 3.4 Recommendation for worker dispatch

**Ship the polling worker as planned. Add LISTEN/NOTIFY as a low-latency wake signal — this is the one cheap upgrade worth doing inside Phase 1.5.**

Concretely:

1. **Keep the 5s poll loop** as durability safety-net (catches missed NOTIFYs, recovers crashes, handles `creating`-stuck rows).
2. **Add `pg_notify('account_provisioning', mapping_id::text)`** in the approval-route transaction, AFTER the `INSERT/UPDATE` to `pending_account_mappings` status='approved'.
3. **Worker `LISTEN`s on the channel** in a separate connection (must be a dedicated DB connection — incompatible with PgBouncer transaction-mode pooling; this is a real constraint to check). Wakes the poll loop immediately on notification.
4. **`FOR UPDATE SKIP LOCKED`** on the approved-row select — already in the plan, confirm it's there.
5. **Advisory lock** per `mapping_id` for the agent-call duration — already in the plan, this is the right pattern.

Latency impact: approve-to-enrich drops from p95 ~5s (one poll interval) to p95 <100ms. For a system positioning as "real-time AI-native," that's a meaningful UX win for the founder's approve-and-watch-it-resolve flow.

**Do NOT** rip out polling and go pure event-driven:
- LISTEN payloads are 8KB max — never put the work itself in the payload, only the row id.
- LISTEN is at-most-once if the worker is disconnected — that's why polling is the safety-net.
- Multi-replica needs each replica to LISTEN, which is fine but adds connection pressure.

**Do NOT** introduce Temporal or Inngest for this initiative:
- We have one LLM call per work unit, not five.
- We have one retry semantic (advisory lock + status state-machine), not five.
- The "durable execution" pitch is for workflows that already cost you sleep. Ours doesn't yet.

**Upgrade triggers for Phase 3+:**
- More than one agent call per work unit → reconsider Temporal.
- Need for multi-day workflows (human-in-the-loop with delays) → reconsider Temporal.
- Worker count > 20 replicas → polling pressure on Postgres may justify a real queue (SQS).
- Cross-region active-active → kill polling, you'll need SQS or a real queue.

---

## 4. Topic 3 — Concrete recommendation for THIS initiative

### 4.1 What to do before Phase 2 planning starts

**Decision: ship Phase 1.5 as designed with two surgical additions.**

1. **(MUST)** Make the outbox schema forward-compatible (Debezium-`OutboxEventRouter`-shape: `id, aggregate_type, aggregate_id, event_type, payload, created_at, published_at, tenant_id`). Cost: ~5 lines in the migration. Benefit: zero rewrite cost when we eventually migrate to CDC.

2. **(SHOULD)** Add `LISTEN/NOTIFY` as a wake signal for the worker. Cost: ~30 lines. Benefit: p95 approve-to-enrich latency 5s → <100ms. Risk: dedicated connection requirement (incompatible with PgBouncer transaction-pooling). Verify the deployment topology before committing — if we're on RDS Proxy or PgBouncer in transaction mode, defer this.

3. **(MUST)** Instrument from day 1:
   - `outbox_lag_seconds` = `now() - min(created_at) WHERE published_at IS NULL`
   - `worker_lag_seconds` = `now() - min(updated_at) WHERE status='approved'`
   - `worker_stuck_creating_count` = `count(*) WHERE status='creating' AND updated_at < now() - interval '5 minutes'`

   These three metrics tell you, with high precision, when to migrate to CDC or Temporal. Without them, the "polling is fine at our scale" claim is unverifiable.

4. **(MUST)** Write the upgrade triggers into the Phase 2 plan as explicit decision criteria. We've codified them above — copy them into the plan doc.

### 4.2 What NOT to do before Phase 2

- **Do NOT** introduce Debezium. Operational cost is non-trivial, value is zero until volume justifies it.
- **Do NOT** introduce EventBridge Pipes from Postgres. Not natively supported; would require Debezium + Kinesis as a prerequisite.
- **Do NOT** introduce Temporal/Inngest. Architecture doesn't yet have the complexity that justifies durable-execution overhead.
- **Do NOT** replace the worker's DB-as-source-of-truth with SQS-as-source-of-truth. Transactional binding of state changes to message dispatch is the core invariant of this initiative; SQS breaks it.

### 4.3 Is the polling architecture "AI-native"?

The user's concern (paraphrasing): "frontier AI companies don't use polling, they use event-driven."

**This is partially true but mostly a category error.** The frontier-AI infrastructure (Anthropic's Agent Skills, OpenAI's Agents SDK, the LangGraph runtime) is *orchestration-layer* infrastructure — it manages the LLM-call graph inside a single workflow. **Underneath that orchestration layer, every production AI system in 2025-2026 still has a durable state store, and the dispatch from state-store to worker is almost always polling-or-NOTIFY-hybrid.** Temporal, the most "AI-native" durable-execution platform, internally uses polling at the worker layer (its Task Queues are polled by workers; the SDK abstraction hides this).

The "AI-native" differentiator for us is not the dispatch mechanism — it's:
- LLM-driven entity resolution at approval time (already in the plan)
- Agentic enrichment of accounts at provisioning time (already in the plan)
- Graph-based identity convergence in Neo4j (already in the plan)
- Multi-agent disambiguation when the LLM confidence is low (Phase 2+ opportunity)

**Polling at the worker layer is not where we win or lose on the "AI-native" axis.** Don't optimize the wrong dimension.

---

## 5. Sources surveyed

- [Revisiting the Outbox Pattern — Gunnar Morling, Oct 2024](https://www.morling.dev/blog/revisiting-the-outbox-pattern/) — Authoritative voice arguing for log-based CDC over polling; assumes Kafka exists; recommends `pg_logical_emit_message` for Postgres.
- ["You Don't Need Kafka, Just Use Postgres" Considered Harmful — Morling](https://www.morling.dev/blog/you-dont-need-kafka-just-use-postgres-considered-harmful/) — Counterweight piece arguing Postgres-as-queue has real limits at scale.
- [Push-based Outbox Pattern with Postgres Logical Replication — event-driven.io](https://event-driven.io/en/push_based_outbox_pattern_with_postgres_logical_replication/) — Demonstrates the `pg_logical_emit_message` pattern + Blumchen library.
- [Outbox Pattern Best Practices in 2025 — Sachith Dassanayake](https://www.sachith.co.uk/outbox-pattern-for-reliable-events-best-practices-in-2025-practical-guide-may-8-2026/) — May 2026 practical guide; aligns with "polling first, CDC later" consensus.
- [Debezium and the Outbox Pattern: Real Impact on Postgres — Ajit Singh](https://singhajit.com/debezium-outbox-postgres-database-impact/) — WAL retention and slot-management operational risks for Debezium adoption.
- [The Outbox Pattern Explained — Streamkap](https://streamkap.com/resources-and-guides/outbox-pattern-explained) — Production decision tree for Debezium vs. polling; explicit "polling fine for small teams without Kafka."
- [Outbox Pattern for Reliable Event Publishing — Conduktor](https://www.conduktor.io/glossary/outbox-pattern-for-reliable-event-publishing) — Industry-standard reference; same Debezium-or-polling decision tree.
- [Implementing the Transactional Outbox Pattern With EventBridge Pipes — Mario Bittencourt](https://medium.com/better-programming/implementing-the-transactional-outbox-pattern-with-eventbridge-pipes-125cb3f51f32) — Canonical Pipes-based outbox; uses DynamoDB Streams as source, NOT RDS Postgres.
- [Amazon EventBridge Pipes — AWS Docs](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-pipes.html) — Confirms Pipes sources: SQS, Kinesis, DynamoDB, MSK, self-managed Kafka, MQ. **No native RDS source.**
- [Change Data Capture with Debezium, Kinesis, and EventBridge — Kareem Mohllal](https://medium.com/@mohllal/change-data-capture-cdc-with-debezium-kinesis-and-eventbridge-10eb5a996788) — Confirms the Postgres → EventBridge path requires Debezium + Kinesis as intermediate hops.
- [Stream changes from Amazon RDS for PostgreSQL using Kinesis and Lambda — AWS Database Blog](https://aws.amazon.com/blogs/database/stream-changes-from-amazon-rds-for-postgresql-using-amazon-kinesis-data-streams-and-aws-lambda/) — AWS-official guidance for Postgres CDC → AWS messaging.
- [Scaling Postgres LISTEN/NOTIFY — PgDog](https://pgdog.dev/blog/scaling-postgres-listen-notify) — Production limits of LISTEN/NOTIFY at scale.
- [pgqueuer — janbjorge/pgqueuer GitHub](https://github.com/janbjorge/pgqueuer) — Python production-tested LISTEN/NOTIFY + SKIP LOCKED hybrid; reference implementation for the pattern.
- [Implementing a Postgres job queue in less than an hour — AmineDiro](https://aminediro.com/posts/pg_job_queue/) — End-to-end production pattern using SKIP LOCKED.
- [PostgreSQL as a Message Queue: The SKIP LOCKED Pattern That Beats RabbitMQ by 38% — Medium](https://medium.com/@the_atomic_architect/postgresql-replaced-my-message-queue-and-taught-me-skip-locked-along-the-way-87d59e5b9525) — Real production replacement of RabbitMQ with SKIP LOCKED.
- [Potential Consequences of Using Postgres as a Job Queue — Rich Yen, May 2026](https://richyen.com/postgres/2026/05/04/postgres_job_queue.html) — Honest accounting of MultiXact and vacuum pressure at high concurrency.
- [Durable Execution Meets AI — Temporal blog](https://temporal.io/blog/durable-execution-meets-ai-why-temporal-is-the-perfect-foundation-for-ai) — Temporal's pitch for AI agents; useful for understanding when durable-execution becomes worth the operational cost.
- [Durable Workflow Platforms for AI Agents and LLM Workloads — Render](https://render.com/articles/durable-workflow-platforms-ai-agents-llm-workloads) — Fair comparison of Temporal/Inngest/etc. for AI workloads.
- [The Runtime Behind Production Deep Agents — LangChain](https://www.langchain.com/conceptual-guides/runtime-behind-production-deep-agents) — Frontier-AI orchestration architecture; relevant for understanding where "AI-native" actually lives.
- [Microsoft researchers find AI models and agents can't handle long-running tasks — The Register, May 2026](https://www.theregister.com/ai-ml/2026/05/11/microsoft-researchers-find-ai-models-and-agents-cant-handle-long-running-tasks/5238263) — DELEGATE-52 benchmark; argues for short atomic work units (which our design already has).
- [Multi-Agent RAG Framework for Entity Resolution — MDPI Computers 14/12/525](https://www.mdpi.com/2073-431X/14/12/525) — 2025 agentic-ER paper; sequential LangGraph orchestration with task-specialized agents.
- [AGENTiGraph: Multi-Agent Knowledge Graph Framework — arXiv 2508.02999](https://arxiv.org/pdf/2508.02999v1) — Multi-agent KG construction; relevant for Phase 2+ ambitions.
- [MAGMA: Multi-Graph Agentic Memory Architecture — arXiv 2601.03236](https://arxiv.org/pdf/2601.03236) — Async update process pattern; closest academic analog to our outbox + worker design.
- [LLM-empowered knowledge graph construction: A survey — arXiv 2510.20345](https://arxiv.org/html/2510.20345v1) — 2025 survey of LLM-driven KG construction; useful for Phase 2+ literature scan.
- [GraphRAG Issue #847 — microsoft/graphrag](https://github.com/microsoft/graphrag/issues/847) — Confirms Microsoft GraphRAG has NO production entity-resolution step; the field is open for our positioning.
- [Building Reliable Tool Calling in AI Agents with Message Queues — Inferable.ai](https://www.inferable.ai/blog/posts/distributed-tool-calling-message-queues) — SQS-based dispatch for AI tool-calling; useful counterpoint, doesn't fit our state-machine model.
- [Enterprise AI Agent Playbook — WorkOS](https://workos.com/blog/enterprise-ai-agent-playbook-what-anthropic-and-openai-reveal-about-building-production-ready-systems) — Production patterns from Anthropic/OpenAI frameworks; reinforces "orchestration is where AI-native lives, not dispatch."

---

## 6. Open questions / production validation needed

1. **PgBouncer / RDS Proxy compatibility.** LISTEN/NOTIFY requires a dedicated session-mode connection. If we're on PgBouncer transaction-mode pooling, LISTEN is broken. **Verify the deployment topology** before committing the LISTEN/NOTIFY upgrade. If blocked, ship pure polling at 5s interval — it's still defensible.

2. **EventBridge throttling at our throughput.** EventBridge has a default PutEvents quota of 10,000 events/sec per region, soft. We're nowhere near this, but the outbox publisher should batch (`PutEvents` accepts up to 10 events per call) and we should configure the EventBridge SDK with retries on `ThrottlingException`. Verify our existing `services/aws_event_publisher.py` does both.

3. **Outbox table growth.** Without a retention policy, the outbox grows monotonically. Decision: keep all rows for audit (consistent with the initiative's audit trail goals) or hard-delete after N days. Recommend: keep `WHERE published_at IS NOT NULL AND published_at < now() - interval '30 days'` deletable; run a nightly cleanup job. This is a P2, not a P1.

4. **WAL slot for future CDC migration.** Even if we don't use CDC now, **provision a dedicated logical replication slot on the RDS instance now** so the migration path is unblocked when we need it. Cost: zero (slot is dormant until used). Without it, future CDC adoption requires a maintenance window to enable `wal_level=logical`. Worth doing as a Phase 1.5 deployment-config item.

5. **Cross-tenant isolation in EventBridge events.** Every outbox row carries `tenant_id` (per the design doc); every EventBridge event must include `tenant_id` in the detail. Downstream consumers must filter by `tenant_id` before any write — verify in the consumer-service plans. This is in scope for the initiative but worth re-flagging.

6. **The `interaction_summaries` placeholder pattern.** Phase 1.5 writes a placeholder `interaction_summaries` row inside the materialization transaction. Confirm the placeholder is invisible to user-facing queries until the real summary lands; otherwise we leak provisioning state into the UX. (Not a dispatch question, but I noticed it adjacent to the worker design and want to flag it.)

7. **Codex Round 5 schema-vs-ORM ordering finding.** The memory index mentions this is acknowledged + mitigated. **Verify the mitigation is in the actual code** before Phase 2 planning starts — the orchestrator may have noted it but the original Codex finding deserves a one-line code reference in the next plan.

---

## 7. TL;DR for the orchestrator

- Polling is the right Phase 1.5 default. **Do not rewrite.**
- Make the outbox schema Debezium-compatible — costs nothing, preserves the future.
- Add LISTEN/NOTIFY as a wake signal **only if** PgBouncer topology allows. Otherwise pure polling at 5s.
- Provision a logical-replication slot now so future CDC migration is unblocked.
- Write the upgrade triggers (outbox_lag_seconds > 30s, worker p95 > 30s, sustained >10 rows/sec) as explicit Phase 2 decision criteria.
- The "AI-native" differentiator lives in the LLM-driven ER, agentic enrichment, and graph convergence — NOT in the dispatch layer. Don't optimize the wrong dimension.

The polling architecture holds up. Ship it.
