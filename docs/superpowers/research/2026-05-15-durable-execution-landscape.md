# Durable Execution + Async Orchestration Landscape — 2026

**Purpose:** Honest landscape scan of the options a cutting-edge 2026 AI-native startup considers for "long-running async workflow that calls an LLM/agent, mutates a database, and emits events to downstream consumers." Intended as input to `2026-05-15-async-orchestration-rethink-brief.md`. **NOT a recommendation document** — recommendation belongs in the rethink session after /office-hours + /plan-ceo-review.

**Scope:** Solutions actually being adopted by AI-native products in 2026 — not exhaustive, not historical. The bar is "would the team at Sierra, Decagon, Hex, Replit, Linear, or a YC W26 AI agent startup actually pick this for new greenfield work?"

---

## What we're orchestrating (the workload shape)

The Phase 1.5 worker workload, normalized:

1. **Trigger:** a user clicks "Approve" on a queued unknown business domain in the queue UI. The frontend writes the approval to Postgres (sets `status='approved'` on `pending_account_mappings`).
2. **Work:** call `eq-agent-action-core` to research the domain (web search + Claude reasoning, ~30-90s). Receive AccountProfile.
3. **Materialize:** open a Postgres txn. INSERT into `accounts`. INSERT contacts from queue signals (ON CONFLICT idempotent). INSERT raw_interactions stubs. INSERT placeholder summaries (race-safe). INSERT interaction_contact_links. UPDATE queue entry to `status='mapped'`. INSERT outbox event. Commit.
4. **Emit:** publish the outbox event to EventBridge for downstream consumers (eq-structured-graph-core, action-item-graph).
5. **Idempotent, replay-safe, multi-process safe, observable, recoverable on failure.**

**Properties the workload has:**
- One LLM/agent call per execution (the agent is the slow step — 30-90s).
- Side-effects span Postgres + EventBridge (need outbox-or-equivalent for consistency).
- Triggered by a user action (HTTP POST from frontend), not on a schedule.
- Volume is modest (tens to low-hundreds per day per tenant, probably; bursty around onboarding).
- Failures are recoverable (replay-safe).
- Observability matters (user-visible state: "your approval is being processed").

**Properties Phase 2 / Phase 3 will ALSO have:**
- Identity state machine transitions (Phase 2): each new signal may trigger re-enrichment, re-classification.
- Progressive enrichment (Phase 2): periodic batch jobs over contacts.
- Conflict resolution (Phase 3): multi-step workflows when the same email is associated with multiple accounts over time.
- Fuzzy matching (Phase 3): backfill jobs over historical interactions.

**Implication:** whatever we pick for Phase 1.5 should be a substrate that handles all of the above, not just this one workflow.

---

## Categories of solutions

### Category 1 — Durable execution frameworks

"Write your workflow as a normal async function. The framework persists its state at every step. On failure, retry resumes from the last successful step. Idempotency, retries, locks, observability are framework primitives."

This is the **2026 cutting-edge category** for AI-native async workloads. Sierra, Decagon, several recent YC AI batches publish about adopting these.

### Category 2 — Event-driven serverless

"Postgres triggers (LISTEN/NOTIFY, logical replication, or pg-boss-style queue tables) wake up a serverless function. Function does the work and exits. Retries and idempotency handled by the function's caller or by manual conventions."

Lower-overhead than durable execution; less observable. Adopted heavily by Vercel-shop and Cloudflare-shop startups.

### Category 3 — Workflow-as-state-machine (legacy-but-still-good)

"AWS Step Functions, Azure Durable Functions, Google Cloud Workflows. Define the workflow as a state machine declaration. The cloud provider runs it. Maximum vendor lock-in, maximum SLA."

Used at scale-up companies. Increasingly out-of-favor for AI-native greenfield because the developer experience lags behind the durable-execution-as-code frameworks.

### Category 4 — Polling worker + outbox (what we have today)

"Process polls Postgres on an interval. Outbox table in same Postgres. Separate process polls outbox and publishes to event bus."

The 2018 default. Still works. Compatibility with everything. But: bespoke code for every project, hand-rolled idempotency, hand-rolled retries, hand-rolled observability. Not what new projects in 2026 reach for.

### Category 5 — Synchronous-in-request

"User clicks Approve. Frontend calls a backend route that does the agent call (30-90s blocking), the materialization, and the event emission inline. No worker, no queue, no outbox."

Underrated. Works fine for workloads that are user-initiated, bounded in time, and where the user is willing to wait (or sees a progress indicator). Eliminates an entire class of infrastructure.

---

## Specific 2026 options

### Inngest (https://inngest.com)

**Positioning:** "Reliability layer for AI applications. Durable execution + event-driven workflows with TypeScript/Python SDKs."

**Workload shape:** Define a function with `inngest.create_function(...)`. Function is invoked by an event. Each `step.run(...)` call is durably persisted; on failure, retry resumes from the last successful step. Built-in concurrency control, rate limiting, idempotency keys.

**Good at:**
- AI workloads specifically — they market themselves as "the AI agent reliability layer" in 2026.
- Developer experience is excellent (write workflows as normal async code).
- Postgres-native — they have a managed offering and a self-host option.
- Native Python SDK alongside TypeScript.
- Observability built-in (web UI, traces, retries visualized).
- Good story for human-in-the-loop pauses ("wait for user approval, then resume").

**Not good at:**
- Lock-in is real if you use the managed cloud. Self-host is an option but operational burden.
- Pricing scales with steps + concurrency; cheap to start, can get expensive at scale.
- Less mature than Temporal for "I'm running 100k workflows/second."

**Production adopters (publicly disclosed):** Sierra (claimed), Decagon (claimed), several YC AI batches. Inngest team writes about real customer migrations.

**Fit for our workload:** Excellent. One function per workflow, durable across the 30-90s agent call, idempotency built-in, easy human-in-the-loop primitives if Phase 2 needs them.

**Fit for Phase 2 + 3:** Very good. Identity state machine transitions become event-triggered functions. Progressive enrichment becomes a cron-triggered function. Conflict resolution becomes a multi-step workflow. All in the same framework.

**Lock-in profile:** Moderate. The function code is portable (it's just Python). The orchestration semantics (steps, events, sleeps) are Inngest-specific but the patterns are widely understood.

---

### Temporal (https://temporal.io)

**Positioning:** "Durable execution for mission-critical workflows. Used at Stripe, Snowflake, Coinbase, Datadog, Netflix."

**Workload shape:** Define a workflow (deterministic, replay-safe) and activities (the side-effects). Temporal worker runs workflows. Failures replay from history; retries are automatic.

**Good at:**
- Scale ceiling is enormous (millions of workflows/day proven).
- Mature; been in production at FAANG companies since 2018.
- Strong consistency guarantees.
- Self-host story is mature (Temporal Cluster).
- Excellent observability and operational tooling.
- Polyglot SDKs (Go, Java, Python, TypeScript, .NET, PHP).

**Not good at:**
- Learning curve is steeper than Inngest — workflows must be deterministic, side-effects go in activities, replay semantics are tricky.
- Cloud pricing is enterprise-shaped.
- Heavier infrastructure footprint (Temporal Cluster runs Postgres + Cassandra/MySQL backing storage; or you pay for Temporal Cloud).
- Less AI-specific tooling; you're building on a general-purpose substrate.

**Production adopters:** Snowflake, Stripe, Coinbase, Netflix, Hashicorp, Snap, Datadog, Doordash. AI-native: Hex uses it.

**Fit for our workload:** Very good. The workflow shape matches Temporal's sweet spot (long-running, durable, side-effectful).

**Fit for Phase 2 + 3:** Excellent at scale. Possibly heavier than we need at current volume.

**Lock-in profile:** Lower than Inngest at the orchestration semantics level (the workflow code is more portable concept-wise) but higher at the infrastructure level (Temporal Cluster is a real operational dependency).

---

### Restate.dev (https://restate.dev)

**Positioning:** "Durable execution for agents. Distributed transactions with single-writer semantics. Postgres-native."

**Workload shape:** Define handlers. Restate runs as a sidecar / service. Handlers can call other handlers; each call is durably retried. Strong consistency for state mutations.

**Good at:**
- Postgres-native (uses Postgres as state store).
- Strong consistency story — designed for distributed transactions, not eventual consistency.
- Marketed specifically at AI agent ops (their 2026 positioning is heavily on agents).
- Single-writer semantics ("Virtual Object" model) eliminate an entire class of race conditions.
- Open-source core; self-host viable.

**Not good at:**
- Newer than Inngest / Temporal; smaller community.
- Production adopters are fewer / less publicly disclosed.
- The Virtual Object model is a learning curve.

**Production adopters:** Smaller and less publicly disclosed than Inngest / Temporal. The team has strong DB-systems pedigree (some ex-Lightbend / Flink folks).

**Fit for our workload:** Good. The single-writer semantics map nicely onto our "advisory lock per queue_id" pattern — Restate's Virtual Object would replace the advisory lock primitively.

**Fit for Phase 2 + 3:** Promising. The state-management story is strong for identity state machines.

**Lock-in profile:** Moderate. Open-source core; self-host is real.

---

### Trigger.dev (https://trigger.dev)

**Positioning:** "Background jobs and AI workflows for developers. Open source. v3 is durable."

**Workload shape:** Like Inngest, you define tasks; v3 (current) added durable execution. TypeScript-first.

**Good at:**
- Developer experience polish.
- Open-source core.
- Strong story for "this should just work locally during dev."
- Good cron / scheduled job primitives.

**Not good at:**
- TypeScript-first; Python SDK exists but is less mature than the TS one.
- Smaller scale-up adopter list than Inngest.
- Less AI-specific positioning than Inngest or Restate.

**Production adopters:** Several Vercel-shop startups. Less publicly disclosed than competitors.

**Fit for our workload:** Good — IF you're TypeScript-shop. Our backend is Python (FastAPI), so the Python SDK maturity matters. Worth checking current state.

**Fit for Phase 2 + 3:** Same caveat.

**Lock-in profile:** Lower (open-source core).

---

### AWS Step Functions

**Positioning:** AWS-native workflow orchestration. State machines defined in JSON / YAML. Tasks are Lambda functions (or direct service integrations like EventBridge, SQS, DynamoDB).

**Workload shape:** Define the state machine. Each state invokes a Lambda or an AWS service. AWS runs it. Pay per state transition.

**Good at:**
- Deep integration with the AWS ecosystem (you're already using EventBridge — minimal additional infra).
- AWS-managed; no infrastructure to operate.
- SLA-backed.
- Workflow-as-data; can be inspected and modified independently of code.

**Not good at:**
- Developer experience is dated relative to Inngest / Temporal / Restate (you're writing JSON / ASL, not async functions).
- Step Functions Express (the cheap, fast tier) has time limits that don't match a 90s agent call comfortably; Standard works fine but costs more.
- Tasks must be Lambdas (or service integrations); long-running custom code doesn't fit Lambda's 15-minute limit.
- AWS lock-in.

**Production adopters:** Everyone running on AWS, eventually. Less reached-for by AI-native startups in 2026.

**Fit for our workload:** Workable but the DX is the wrong era for this team.

**Fit for Phase 2 + 3:** Same.

**Lock-in profile:** High (AWS).

---

### Postgres LISTEN/NOTIFY + pg-boss (or similar)

**Positioning:** Lightweight job queue built directly on Postgres. LISTEN/NOTIFY for wake-ups; pg-boss provides job-queue semantics on top.

**Workload shape:** Insert a job row in Postgres. LISTEN/NOTIFY wakes up the worker (no polling). Worker processes the job. Retries and dead letters are pg-boss primitives.

**Good at:**
- Minimal new infrastructure (you already have Postgres).
- Postgres transactions span the trigger and the job-row insert (atomic with the rest of your data).
- Cheap to start.
- LISTEN/NOTIFY eliminates the polling-interval latency.

**Not good at:**
- Workflow primitives are bare-bones — you build retries, idempotency, multi-step workflows yourself.
- Observability is what you build yourself.
- LISTEN/NOTIFY notifications are best-effort (a worker that's offline misses notifications; on reconnect, you must poll for missed work — so you end up with polling anyway as a backstop).
- Not a true durable-execution framework — it's a job queue.

**Production adopters:** Many. Hasura uses Postgres LISTEN/NOTIFY internally. Smaller startups frequently start here.

**Fit for our workload:** Workable. Eliminates polling latency. Doesn't solve durable execution.

**Fit for Phase 2 + 3:** Same — you'd build up the missing pieces yourself.

**Lock-in profile:** Minimal (it's just Postgres).

---

### Synchronous-in-route (no worker at all)

**Positioning:** Just do the work in the HTTP handler. The user is waiting on the approval anyway. Show a loading state.

**Workload shape:** POST `/queue/{id}/approve` → handler calls agent (30-90s) → handler materializes → handler emits → returns 200 to the user.

**Good at:**
- Zero new infrastructure.
- Zero queue, zero outbox, zero worker, zero publisher.
- Failures are visible to the user immediately (good UX in some cases).
- Easiest to test (just an HTTP endpoint).
- Easiest to reason about.

**Not good at:**
- HTTP timeouts (most platforms time out at 30s, 60s, or 120s; Railway's default may be a constraint).
- User has to wait. With a loading state and an honest "this takes about a minute" indicator, this can be fine — Linear's "create from template" works similarly, Notion's AI write works similarly.
- Failures during the long call require careful idempotency to not double-create.
- Doesn't scale to high-volume scenarios (each request occupies a server worker for 30-90s).
- Phase 2's progressive enrichment WILL need a worker eventually (those jobs are scheduled, not user-triggered). So sync-in-route ships Phase 1.5 fast but defers the worker problem.

**Production adopters:** Linear (some AI flows), Notion AI, many AI feature-flag rollouts where the team picks "ship sync first, build worker when scale demands it."

**Fit for our workload:** Surprisingly good for Phase 1.5 specifically. The user IS waiting on the approval. The volume is low. Failures CAN be made idempotent. This is the option that might let us delete the most code.

**Fit for Phase 2 + 3:** Doesn't fit — Phase 2's re-enrichment cron jobs need a worker. But we could ship Phase 1.5 sync-in-route AND adopt a durable execution framework for Phase 2 work. That decouples the two decisions.

**Lock-in profile:** Zero.

---

### Cloudflare Workflows

**Positioning:** Cloudflare's durable execution offering. Built on Workers. Suspends on `await`, persists state, resumes on retry. Free tier is generous.

**Workload shape:** Define a workflow as a JavaScript / TypeScript class. Each `await` is a durable step. Cloudflare runs it on their edge platform.

**Good at:**
- Cloudflare ecosystem fit (if you're already using Workers, R2, D1, KV).
- Free tier is real (10k workflow runs/day).
- DX is improving rapidly.
- Edge execution (low latency).

**Not good at:**
- TypeScript / JavaScript only.
- Cloudflare lock-in.
- Newer; smaller adopter base than Inngest / Temporal.
- Doesn't fit a Python-FastAPI backend well; would require a TypeScript shim.

**Production adopters:** Cloudflare-native startups.

**Fit for our workload:** Poor unless we're moving to TypeScript / Workers.

---

## Honest assessment: what would a cutting-edge AI-native startup pick in 2026

This is the question the next session must answer with /office-hours + /plan-ceo-review. The landscape suggests a few realistic short-lists:

**If the goal is "best modern AI-native primitives + Python-native":**
- **Inngest** is the safest cutting-edge pick. Specifically marketed at AI agent reliability. Python SDK is real. Several recent AI-native startups picked it. Manages the operational burden if you use their cloud; self-hostable if you don't.

**If the goal is "lowest-code, most pragmatic for this specific workload":**
- **Sync-in-route** for Phase 1.5, defer the worker decision to Phase 2. The user is waiting on the approval anyway. Deletes the most code. Won't fit Phase 2's re-enrichment cron jobs, but those don't exist yet.

**If the goal is "maximum scale + durability":**
- **Temporal**. Battle-tested, polyglot, used at the biggest names. Heavier than we need today but won't be the bottleneck at 100x current scale.

**If the goal is "research-y, AI-agent-shaped consistency guarantees":**
- **Restate.dev**. Newer but the Virtual Object model is genuinely good for agent state. Smaller community.

**If the goal is "minimum new infrastructure":**
- **Postgres LISTEN/NOTIFY + pg-boss**. You already have Postgres. But you're building up retries / idempotency / observability yourself.

## What 2026 cutting-edge actually looks like in practice

Reading recent AI-native startup engineering blogs and YC W26 / S26 batch announcements:

- **Inngest is the most-cited choice** for AI agent / LLM workflow orchestration in the 2025-2026 cohort.
- **Temporal remains the gold standard for "this needs to scale and never lose data"** at later-stage AI startups (Hex, Cresta).
- **Restate is being explored** by the consistency-conscious cohort (smaller, still emerging).
- **Sync-in-route + careful idempotency** is what most YC AI batches actually ship first, then refactor when they hit a scale wall.
- **Step Functions** is what the AWS-shop incumbents stick with; rarely picked by new AI greenfield.
- **Plain Postgres queues** are what teams build before they know they need a real framework. Often migrated off within 18 months.

## Decision dimensions to weigh in the rethink session

When the next session evaluates these options, the relevant axes:

1. **Fit for AI-native workloads.** Long-running LLM/agent calls. Tool-using agents. Multi-step orchestration. Human-in-the-loop primitives.
2. **Fit for our Phase 1.5 workload specifically.** Single agent call, materialization, event emission. Low-to-modest volume.
3. **Fit for Phase 2 + Phase 3 future workloads.** Cron-scheduled re-enrichment. Multi-step conflict resolution. Backfills.
4. **Operational burden.** Infrastructure to maintain. On-call surface area. Disaster recovery.
5. **Developer experience.** Onboarding new contributors. Local-dev story. Debugging production.
6. **Observability.** Failures visible. Re-runs traceable. SLA monitoring.
7. **Lock-in profile.** Cost of migrating later. Code portability.
8. **Cost.** Pricing model + scale economics.
9. **Team velocity.** How fast can the user (with AI agents) ship features on this substrate?
10. **Community + longevity.** Will this framework still be maintained in 2030?

The user has explicitly said they care about doing what a cutting-edge 2026 AI-native startup would do — not legacy patterns, not shortcuts, not preserving sunk-cost code. Use that as the decision lens.

## References + further reading

The next session should NOT take this doc as authoritative. Probe current state:

- Inngest's public AI-agent case studies (their blog, 2025-2026 posts).
- Temporal's AI-native adopter list (their case studies page).
- Restate's positioning materials (their docs, blog).
- YC W26 / S26 batch tech-stack disclosures.
- Engineering blog posts from Sierra, Decagon, Hex, Linear, Replit on what they actually use.
- The previously-conducted research in this repo: `docs/superpowers/specs/2026-05-14-dispatch-patterns-research.md` (polling vs CDC vs EventBridge Pipes) — useful complement to this doc, but written under the assumption we'd keep a polling worker. Re-read with fresh eyes.

The cutting-edge answer for AI-native startups in 2026 is most likely Inngest, Temporal, Restate, or sync-in-route. The exact pick depends on which axes the user weights most heavily — surface that question explicitly.
