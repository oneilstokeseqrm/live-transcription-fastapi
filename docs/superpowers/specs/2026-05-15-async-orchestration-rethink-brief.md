# Async Orchestration Rethink — Brief for Next Session

**Purpose:** Neutral framing for the next session's architecture rethink. Sets up divergent thinking, captures constraints, lists candidates without anchoring. The next session does this work via `/office-hours` → `/plan-ceo-review` → `/plan-eng-review` → Codex consult → write new implementation plan.

**Critical: this brief does NOT recommend an option.** Pre-deciding here would defeat the purpose. The previous session (2026-05-15) recommended Path A (move account creation into the existing worker) as a tactical fix; the user correctly rejected that as preserving a 2018-era architecture. The next session evaluates the question from scratch.

**Author posture for the next session:** the user has explicitly said they want what a cutting-edge 2026 AI-native startup would actually build, and they do not care about sunk-cost preservation of the ~700 LoC of worker / publisher / queue-route code currently in main. Take that seriously. Do not anchor on what's already shipped.

---

## 1. Read these first (in this order)

1. **`docs/superpowers/specs/2026-05-15-initiative-context-snapshot.md`** (~10 min) — what the whole Contact Quality Initiative is, what's shipped, what's the trajectory across Phase 1 / 1.5 / 2 / 3. Standalone entry point.

2. **This brief** (~5 min).

3. **`docs/superpowers/research/2026-05-15-durable-execution-landscape.md`** (~10 min) — landscape of 2026 options. Honest about what cutting-edge AI-native startups actually pick.

4. **`tasks/lessons.md`** — read the bottom entries, especially:
   - "Probe external service contracts at design time, not deploy time (2026-05-15)" — the lesson that surfaced this rethink.
   - "Codex spiral discipline" + earlier lessons for context on prior decision discipline.

5. **`docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` Section 6** — the existing outbox-backed durability machinery design. Read it to understand WHAT problem the worker + outbox + publisher were solving, so the new architecture can solve it differently rather than skip it.

6. **`docs/superpowers/specs/2026-05-14-dispatch-patterns-research.md`** — prior research on polling vs CDC vs EventBridge Pipes. Useful complement but written under the assumption we'd keep a polling worker.

Optional / on-demand:
- `tasks/downstream/blocker-agent-contract-mismatch.md` — the tactical Path A fix (superseded by this rethink, kept as audit trail).
- `tasks/downstream/railway-phase-1-5-worker.md` — the original 6-step deploy recipe (superseded; new recipe depends on rethink outcome).
- `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` — current implementation plan (will need significant Phase 1.5 revision after rethink).

## 2. The product-level problem we're solving

A user clicks "Approve" on a queued unknown business domain in the queue UI. The system must, in some order:

1. **Research** the domain (call eq-agent-action-core, which does web search + Claude reasoning, takes 30-90 seconds).
2. **Create** an account in our Postgres database using the research data.
3. **Materialize** the queued signals: insert contacts for each signal email, link them to interactions where present, ensure raw_interactions stubs exist.
4. **Mark** the queue entry as `mapped` with `resolved_account_id` set.
5. **Notify** downstream consumers (eq-structured-graph-core, action-item-graph) via EventBridge so their Neo4j / action-item graphs converge.

All of this must be:
- **Idempotent** (the user can click Approve twice; we don't create two accounts).
- **Replay-safe** (a partial failure can be safely retried).
- **Multi-process safe** (if we eventually run >1 worker container, they don't process the same queue entry twice).
- **Observable** (the user wants to see "your approval is being processed" → "done" or "failed: reason").
- **Recoverable** (if the agent service is down, we retry; if Postgres conflicts, we resolve; if EventBridge throws, we don't lose the event).

That's the product surface. The current architecture (polling worker + outbox + separate publisher) is one way to achieve this. There are several others.

## 3. The broader project context

This is the critical framing. The Phase 1.5 worker is ONE piece of a multi-phase initiative.

- **Phase 1 (shipped):** Tightens the account-anchoring contract end-to-end. Synchronous; no async machinery needed.
- **Phase 1.5 (in flight):** Async account creation from queued unknown business domains. **This is what the rethink is about.**
- **Phase 2 (future, not committed):** Identity state machine + progressive enrichment. Will need:
  - Event-triggered state transitions (new signal arrives → re-classify contact).
  - Scheduled batch re-enrichment (sweep all `shell` contacts, try enrichment, transition state).
  - Long-running multi-step workflows (research → classify → notify → re-research-if-uncertain).
- **Phase 3 (future, not committed):** Conflict resolution, multi-account history, fuzzy matching. Will need:
  - Multi-step decision workflows.
  - Backfill jobs over historical interactions.
  - Human-in-the-loop primitives (admin reviews conflict).

**Implication:** the orchestration substrate we pick for Phase 1.5 should compound across Phase 2 + Phase 3. Picking a substrate that ONLY handles Phase 1.5 means we're back to this conversation in 2-3 months.

The current architecture (bespoke polling worker) doesn't compound — every Phase 2 / Phase 3 workflow would be another hand-rolled worker + outbox + lock pattern.

A durable-execution framework (Inngest, Temporal, Restate) provides primitives that scale to all three phases.

A sync-in-route approach for Phase 1.5 specifically would still require picking a substrate for Phase 2 work, just deferred.

## 4. Hard invariants any new architecture must preserve

From `2026-05-15-initiative-context-snapshot.md` section 6:

### Product invariants (cannot regress)
- Hard Rule 1: no contact without account anchor.
- Hard Rule 2: no interaction without account anchor.
- Tenant isolation absolute (no cross-tenant queries).
- Three-state branching with NO fallback-to-anchor.
- Backend rejection over frontend trust.

### Engineering invariants (cannot regress)
- Caller-side completeness when changing function signatures.
- Auth boundary wins on body/header conflicts.
- Real Codex review at phase boundaries.
- Production E2E with Railway-signed JWT as final quality gate.
- External service contracts probed at design time.
- /context-save mandatory at session end.

### Idempotency invariants (must be preserved THROUGH SOME mechanism)
- Three-layer idempotency: agent-call dedup + outbox-style consistency + frontend approval dedup.
- Replay-safe via terminal-status guards.
- Race-safe writes when multiple processes may write the same row.
- Cross-account contact reassignment fails loud.

### What's NOT invariant (open for redesign)
- The specific outbox table (`account_provisioning_outbox`) — could be replaced by any equivalent at-least-once delivery to EventBridge.
- The specific advisory lock (`pg_try_advisory_xact_lock`) — could be replaced by single-writer semantics, Virtual Objects, or framework-level dedup.
- The specific polling cadence — could be replaced by event-driven wake-up, push-based, or eliminated entirely.
- The specific publisher process — could be replaced by direct emission, framework-managed emission, or framework-native event publication.
- The specific `python -m workers` entrypoint — could be replaced by serverless functions, framework workers, or no worker at all.

The rethink decides what's in the "could be replaced by" column. The invariant columns stay.

### Test-discipline expectations the new architecture must meet (added 2026-05-15)

The 2026-05-15 `account_lookup` bug surfaced four systemic quality gaps that affect any architecture choice. Whatever substrate the rethink picks, the new code must:

1. **Live-schema verification at design time.** Any new SQL (or framework-native data access pattern) must be probed against the live Postgres project via Neon MCP at design time, with the probe result cited inline in the code. Codex review CANNOT verify live schema; only an actual probe does.

2. **Real-substrate coverage for in-service primitives.** If the new architecture has functions that wrap DB queries, agent calls, or other side-effects, they must have at least one test that exercises the real implementation (real test DB, real HTTP server, or at minimum a SQL-text / payload assertion). Mock-at-import-level testing for in-service functions is a coverage hole that hides shipping bugs.

3. **Per-branch E2E coverage.** Every fan-out branch in critical-path code (e.g., per-attendee three-state branching, workflow decision arms, retry-vs-fail policies) must have at least one happy-path case in the production E2E suite that exercises real downstream effects (writes, events, state transitions). Auth/validation/error cases are necessary but not sufficient.

4. **Narrow exception handling.** Broad `except Exception:` blocks that silently degrade behavior on bugs are the smell that hid the account_lookup bug for 24 hours. If the new framework provides retry/error-handling primitives, those REPLACE our excepts — they don't add to them. Programming errors should propagate to Sentry / Railway / observability rather than be swallowed.

These four expectations are codified in `tasks/lessons.md` ("Four systemic quality gaps that let a silent regression ship Phase 1") with concrete how-to-apply guidance, and in `tasks/downstream/test-discipline-gaps-2026-05-15.md` as four follow-up action items with acceptance criteria.

**The new implementation plan must explicitly address all four.** Plans that don't (and just say "we'll test it") are repeating the exact mistake.

## 5. Frozen-state inventory: what we'd potentially throw away

If the rethink picks something other than "keep the polling worker," here's what gets deleted:

| File | LoC | Tests | Notes |
|------|-----|-------|-------|
| `workers/__main__.py` | ~70 | 0 | Entrypoint |
| `workers/account_provisioning_worker.py` | ~200 | ~12 | Worker poll loop + per-entry processing |
| `workers/advisory_lock.py` | ~30 | ~5 | Postgres advisory lock helpers |
| `workers/materialization.py` | ~250 | ~15 | Atomic materialization txn (PARTIALLY reusable — see note below) |
| `workers/outbox_publisher.py` | ~280 | ~20 | EventBridge publisher with FOR UPDATE SKIP LOCKED |
| `services/agent_action_core_client.py` | ~50 | ~5 | Agent HTTP client (broken contract anyway) |
| `services/queue_authorization.py` | ~50 | ~8 | Queue auth helper (USED BY queue routes — keep) |
| `routers/queue_actions.py` | ~720 | ~30 | Approve/Map/Ignore HTTP routes (LIKELY KEEP, see note) |
| Database tables: `account_provisioning_outbox` | — | — | Could be dropped if framework handles event delivery |
| **Total candidate for replacement: ~1,650 LoC + ~95 tests** | | | |

**Important caveats:**

- **`materialization.py` is partially reusable** in any architecture. The actual SQL — INSERT contacts ON CONFLICT, UPSERT raw_interactions, UPSERT placeholder summaries, INSERT links, UPDATE queue, INSERT outbox — represents real product logic. Even if we move to a different orchestration framework, the materialization logic itself stays.
- **`queue_actions.py` routes likely stay** — the frontend already calls POST `/queue/{id}/approve`. Whether the route enqueues a workflow (Inngest event), invokes a Temporal workflow, or just does the work inline determines what the route body becomes. The HTTP contract probably stays.
- **`queue_authorization.py` stays** — auth helpers are framework-independent.
- **The outbox table could go** if the framework handles event delivery to EventBridge with its own durability story.

So the actual "potentially deletable" might be closer to ~700-900 LoC + ~50 tests, depending on the choice. Still substantial, still worth it for a better substrate.

**On the 6 Codex review rounds invested in this code:** the bug findings (22 of them) were real. The regression tests that caught them defend against real failure modes. If we move to a different framework, MANY of those failure modes become framework-handled (idempotency, locks, retries, race conditions in publishing). The Codex investment wasn't wasted — it taught us where the edge cases are. The next architecture should preserve test coverage for the failure modes that remain in our code (the materialization SQL, the queue auth logic, the agent client).

## 6. Candidate approaches (neutral framing, no recommendation)

These are the realistic options. The landscape doc has the full evaluation; here's the one-paragraph framing for each.

### Option 1 — Durable execution framework (Inngest, Temporal, or Restate)

Pick one durable-execution framework. The Phase 1.5 "approve a queue entry" workflow becomes a function in that framework. The framework handles retries, idempotency, multi-process locks, observability. Replaces the polling worker, advisory locks, and the outbox publisher with framework primitives. Compounds across Phase 2 + Phase 3 workloads. New operational dependency (managed cloud or self-host). Strong fit for AI-native 2026 patterns.

### Option 2 — Synchronous-in-route for Phase 1.5, defer worker decision

Drop the worker entirely for Phase 1.5. The POST `/queue/{id}/approve` route synchronously calls the agent (30-90s), materializes, emits to EventBridge, returns 200. Frontend shows a progress indicator. Eliminates ~900 LoC. Phase 2's re-enrichment cron jobs would need a worker eventually — pick the framework when Phase 2 starts, not now. Smallest change. Highest user-visible impact (the loading state).

### Option 3 — Lightweight: Postgres LISTEN/NOTIFY + pg-boss (or just pg-boss)

Drop the bespoke polling loop. Replace with pg-boss for job queue semantics (retries, dead letters, scheduled jobs) and LISTEN/NOTIFY for low-latency wake-ups. Still build idempotency / observability ourselves. Cheapest new infrastructure. Doesn't compound across Phase 2 + Phase 3 nearly as well as Option 1.

### Option 4 — AWS Step Functions

Workflow becomes a Step Functions state machine. Tasks are Lambda functions. AWS-managed. Deep integration with EventBridge (which we already use). Developer experience is the wrong era for a 2026 AI-native team. High AWS lock-in. Probably the wrong pick but should be considered for completeness.

### Option 5 — Hybrid: sync for Phase 1.5 + durable execution for Phase 2

Ship Phase 1.5 sync-in-route (fastest). Pick durable execution for Phase 2 work. This sequences the decisions: ship the immediate user value now with minimal infrastructure; pick the right substrate when Phase 2 actually defines the workloads it needs.

### Option 6 — Keep the polling worker; just fix the contract

This was the Path A recommendation from the prior session. Rejected by the user as preserving a 2018 pattern. Included here for audit-trail completeness; not a real option in the rethink.

## 7. Open questions for the next session to explore in /office-hours and /plan-ceo-review

These are the questions that should drive divergent thinking BEFORE any option is picked:

### Product-level questions (CEO-review territory)

1. **What does the user actually experience when they click Approve?** Today (with no worker running) it's broken. What's the IDEAL experience? Is it instant feedback ("approving...") followed by a notification in 60 seconds? Is it a synchronous loading state? Is it a queue dashboard showing pending? The answer to this constrains the architecture.

2. **What's the volume?** Approvals per day per tenant. Bursty around onboarding? Steady? If volume is <100/day across all tenants, sync-in-route works. If it's 10,000/day, we need a real framework.

3. **Should the approval be undoable?** If yes, the architecture needs a "compensating action" pattern (durable execution frameworks handle this well; sync-in-route is harder).

4. **Should the frontend show the agent's research progress to the user?** ("Researching company...", "Found 12 sources...", "Generating profile..."). If yes, we want to stream the agent's SSE events through to the UI — which changes what the orchestration layer looks like.

5. **What about Phase 2's progressive enrichment?** Is THAT supposed to be visible to the user (notifications for re-enriched contacts)? Or invisible (background sweep)? This shapes whether we need a workflow framework or just a cron.

### Architecture-level questions (eng-review territory)

6. **How important is observability of in-flight workflows?** Inngest / Temporal give you a UI showing every running workflow with its current step. Sync-in-route gives you HTTP request logs. Different tradeoffs.

7. **How important is local-dev parity?** Some frameworks have great local-dev stories (Inngest local dev runs in-process). Others need real infrastructure even for dev (Temporal Cluster).

8. **What's the migration path off if we pick wrong?** Inngest's lock-in is medium; Temporal's is lower; AWS Step Functions's is high. Sync-in-route has no lock-in.

9. **How does the choice affect the queue UI (cross-repo eq-frontend work)?** Different orchestration choices may show different state to the user. Coordinate with whoever owns the frontend.

10. **Is the EventBridge dependency load-bearing or accidental?** Today's design uses EventBridge because that's how eq-structured-graph-core consumes events. If the new architecture is Inngest-native, do downstream services consume from Inngest events directly, or do we still bridge to EventBridge?

### Practical-execution questions

11. **What's the cost of the chosen framework at our current and 10x-current scale?**

12. **What's the operational burden? Who's the on-call for it? Where do alerts go?**

13. **What's the test story? Can workflows be tested in CI without spinning up the framework?**

14. **What's the upgrade story? Will this framework still be maintained in 2030?**

## 8. The decision process the next session should follow

This is rigid. Do not skip steps.

**Step 1 — `/office-hours`** to interrogate the product-level questions (1-5 above) from first principles. The user is a non-developer founder; office hours surfaces the product intent. Output: a one-page product brief on what the approval experience should be, what volume to plan for, etc.

**Step 2 — `/plan-ceo-review`** to challenge scope and ambition. Should we even be building this the way the design doc envisioned? Is the queue itself the right product surface? Is the agent enrichment the right user value? CEO review's job is to push back on architectural decisions that aren't 10-star product decisions.

**Step 3 — Read the landscape doc carefully.** Note which options match the product brief's answers. Eliminate options that don't fit.

**Step 4 — `/plan-eng-review`** with the surviving 2-3 options to evaluate engineering tradeoffs. Output: a recommended architecture with explicit rationale.

**Step 5 — Codex consult** on the recommended architecture BEFORE writing code. Per the recurring lesson, Codex consult at design time catches what implementation review can't.

**Step 6 — Write a new implementation plan** at `docs/superpowers/plans/2026-05-XX-async-orchestration-revised.md` covering Phase 1.5 (revised) + considerations for Phase 2 + Phase 3.

**Step 7 — Update the design doc.** `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` Section 6 (durability machinery) needs revision to match the new architecture. Document what we kept, what we changed, why.

**Step 8 — `/context-save`** with a checkpoint titled with the chosen architecture (e.g., "phase-1.5-rethink-inngest-decided" or "phase-1.5-rethink-sync-in-route-decided").

After the rethink session, a SEPARATE session executes the new implementation plan. That session does the code-level work: tearing out the old worker, building on the new substrate, updating tests, redeploying.

## 9. Anti-anchoring instructions

The next session will be tempted to:

- **Anchor on what's already shipped.** Don't. ~700-900 LoC of code is small relative to the value of getting the architecture right. The user has explicitly said sunk cost is not a factor.
- **Anchor on what I (this session) recommended.** This brief intentionally avoids recommending. The previous session recommended Path A; that was wrong (it preserved a 2018 pattern). Don't carry that forward.
- **Anchor on what's familiar.** "We already use Postgres and EventBridge, so let's just keep building on those." That's the legacy-pattern trap. Pick what fits 2026 AI-native, not what fits existing infrastructure.
- **Anchor on the first viable option.** Inngest is the obvious starting point for AI-native 2026, but the rethink should genuinely consider sync-in-route + Temporal + Restate before picking.

**The right posture:** "We're a 2026 AI-native startup picking infrastructure for an initiative we'll be building on for the next 12-18 months. What would we pick if we were starting today, knowing what we know about Phase 2 + Phase 3? What would we pick if we cared about correctness AND developer velocity AND scaling AND maintainability — not just shipping fast?"

## 10. What's NOT in scope for the rethink session

- **Executing the new architecture.** That's a follow-up session.
- **Migrating data.** Schema is intact; we're changing the code that operates on it.
- **Phase 2 / Phase 3 detailed design.** This rethink picks a substrate that COMPOUNDS into Phase 2 + 3, but doesn't design those phases.
- **Cross-repo coordination.** The chosen architecture might require eq-frontend changes (queue UI shape) or eq-agent-action-core changes (none required if we keep agent as research-only). Those become follow-up tasks identified by the rethink, not done in the rethink session.

## 11. Final note for the next agent

The user is paying for thinking, not typing. The rethink session's value is in the decisions made, not the docs written. Take time to actually run the workflow skills (`/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, Codex consult). Don't shortcut to "obviously Inngest." The reason this whole rethink exists is that the prior session shortcut to "obviously keep the worker." Don't make the same mistake at a different level.

When you reach a decision, document the alternatives considered and why they lost. Future sessions need to know not just what we picked but what we explicitly rejected, so they don't re-litigate.
