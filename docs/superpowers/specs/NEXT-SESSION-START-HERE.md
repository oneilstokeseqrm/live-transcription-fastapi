# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — a multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-15 (architecture rethink decision; no code changes; durable lessons committed).
**Status:** ✅ **PHASE_1.5_RETHINK_DECIDED_DBOS — IMPLEMENTATION_PLAN_WRITING_PENDING** — Substrate is locked: DBOS (Apache 2.0/MIT, library-only, Postgres-as-durability). This session writes the implementation plan + revises the design doc + saves a fresh checkpoint. **Do NOT write code yet.** Code execution is a separate session beyond this one.

---

## CRITICAL — this is a multi-session, multi-repo, long-arc project

The Contact Quality Initiative is foundational hardening of the contact + account entity layer that the entire AI-native customer intelligence platform stands on. **Decisions made for Phase 1.5 must compound across Phase 2 + Phase 3, which together represent ~6-12+ months of additional work.** Treat this session's deliverables as load-bearing for that whole arc.

### Project trajectory

- **Phase 1 — SHIPPED 2026-05-14:** Account-anchoring contract tightened end-to-end at every ingestion path. Per-attendee three-state branching (PERSONAL / INTERNAL / BUSINESS+known / BUSINESS+unknown). Backend rejection on missing `account_id`. PR #10 (live-transcription-fastapi), PR #11 (P2 cleanup), PR #6 (eq-email-pipeline) all merged. Production E2E 20/20 PASS. **Phase 1 silent regression fixed 2026-05-15** at commit `31f513f` (account_lookup SQL was querying wrong table for 24h before downstream agent surfaced it). Production now verified working end-to-end, not just "tests pass."
- **Phase 1.5 — IN FLIGHT, architecture rethink complete:** Async workflow handling queued unknown business domains. **DBOS-based architecture decided 2026-05-15.** Implementation plan pending — THIS SESSION'S WORK.
- **STOPPING POINT** — Comprehensive re-planning before any Phase 2 commitment.
- **Phase 2 — FUTURE (not committed):** Identity state machine + progressive enrichment. Contacts gain explicit state (`shell` → `emerging` → `partial` → `resolved` → `verified`). Re-enrichment runs async; new signals trigger state transitions. **Will run on the same DBOS substrate.**
- **Phase 3 — FUTURE (not committed):** Conflict resolution, multi-account history, fuzzy matching. Multi-step decision workflows with human-in-the-loop. **Will run on the same DBOS substrate.**

### Cross-repo scope (load-bearing context)

Six repositories participate. Decisions in this session affect coordination across them:

| Repo | Role | Status as of 2026-05-15 |
|------|------|--------------------------|
| `live-transcription-fastapi` (this repo) | Primary. Transcript + text + upload ingestion. Queue routes. Soon: DBOS workflows. | Phase 1 + Phase 1.5 P2 + Phase 1.5 main-scope CODE all in main. DBOS workflows not written yet. |
| `eq-email-pipeline` | Email ingestion. Three-state branching live. | Phase 1 changes shipped (PR #6 / `895cc9f`). |
| `eq-structured-graph-core` | Neo4j Account/Contact MERGE. AccountCreated consumer (will read from EventBridge once workflows ship). Owns one of the two Lambda forwarders. | Unchanged for Phase 1. |
| `action-item-graph` | Downstream consumer. Owns the second Lambda forwarder. | Unchanged for Phase 1. Has separate `SourceType` enum fix in flight by its own agent (missing `zoom`+`generic`) — NOT this session's problem. |
| `eq-frontend` | Prisma schema owner. Queue UI (cross-repo Phase 1.5). | Phase 1.5 schema applied to Neon eq-dev. |
| `eq-agent-action-core` | AI-powered company-research service. Tavily + Claude AccountProfile generation. | Production-deployed; research-only; never INSERTs into our accounts table. |

### The five test-discipline expectations the new plan must address

The 2026-05-15 quality-gap incidents codified five expectations any architecture must explicitly handle. Plans that don't explicitly address all five are repeating the exact mistakes. They are:

1. **Live-schema verification at design time** — Neon MCP probe for any new SQL.
2. **Real-substrate coverage for in-service primitives** — no mock-at-import-level coverage holes.
3. **Per-branch E2E coverage** — production E2E exercises every fan-out branch with a happy-path case.
4. **Narrow exception handling** — broad `except Exception:` is a code smell that masks bugs.
5. **Cross-service contract verification at design time** — probe live EventBridge rules + downstream Pydantic models + agent OpenAPI before writing code that crosses the boundary.

See `tasks/lessons.md` (bottom two umbrella lessons) and `tasks/downstream/test-discipline-gaps-2026-05-15.md` for full how-to-apply guidance on each.

---

## Mandatory read order at session start

1. **`docs/superpowers/specs/2026-05-15-initiative-context-snapshot.md`** (~10 min) — Standalone entry point for the WHOLE initiative. Read Section 5 (current status, now reflects DBOS decision) and Section 6 (30 numbered hard invariants).
2. **This handoff** (~5 min) — Specific work for this session.
3. **The latest checkpoint** (loaded automatically by `/context-restore`) — `phase-1.5-rethink-decided-dbos` from 2026-05-15. Captures D1-D7 decisions + full eliminations + new lesson + Steps 6-8 scope.
4. **`tasks/lessons.md` bottom TWO umbrella lessons** — "Four systemic quality gaps" + "Cross-service contract verification at design time." These are the five expectations the implementation plan must address.
5. **`tasks/downstream/test-discipline-gaps-2026-05-15.md`** — All five items with how-to-apply guidance and acceptance criteria.
6. **`docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` Section 6** — The CURRENT (now-stale) durability machinery design. Read to understand what the polling-worker + outbox + publisher architecture was solving, so the DBOS architecture can solve the same problems differently rather than skip them.
7. **Reference on-demand:** `docs/superpowers/research/2026-05-15-durable-execution-landscape.md` if you need to recall why DBOS over the alternatives.

---

## This session's work — three deliverables

### Deliverable 1 (Step 6): Write the DBOS implementation plan

File: `docs/superpowers/plans/2026-05-XX-async-orchestration-dbos.md` (substitute today's date)

The plan must include:

- **Revised Phase 1.5 design on DBOS primitives** — `@DBOS.workflow`, `@DBOS.step`, `@DBOS.scheduled`, `recv`/`send`/`set_event`/`get_event` for HITL.
- **File-by-file deletion list** for the polling worker + outbox publisher + worker entrypoint. KEEP `workers/materialization.py` (real product logic — INSERT contacts, INSERT raw_interactions, UPSERT placeholder summaries, INSERT links). DELETE `workers/outbox_publisher.py` + `workers/__main__.py` + `workers/account_provisioning_worker.py` + `workers/advisory_lock.py` + `services/agent_action_core_client.py`. The `account_provisioning_outbox` table itself may or may not stay depending on the publication-identity-and-consumer-dedup analysis (see "Verified contracts" section below).
- **Queue routes refactor** — `routers/queue_actions.py` HTTP contract stays the same; the body changes to start a DBOS workflow via `await Workflow.start(...)` with an idempotency key derived from `approval_attempt_id`.
- **"Verified contracts" section** — explicit, baked-in design-time verification, NOT afterthoughts. Probe and cite each of:
  - **Neon Postgres schema** — `information_schema.columns` queries for every table the new code reads or writes (`pending_account_mappings`, `pending_account_mapping_signals`, `accounts`, `account_domains`, `contacts`, `raw_interactions`, `interaction_summaries`, `interaction_contact_links`, `calendar_event_interaction_links`, optionally `account_provisioning_outbox`)
  - **eq-agent-action-core OpenAPI** — re-probe `/openapi.json` for the enrich endpoint. The current shape (per the prior session's blocker discovery) is `POST /api/enrich` body `{url, effort?}`; SSE response by default; `?stream=false` returns AccountProfile blocking 30-90s. Confirm this is still current.
  - **EventBridge rules** — `aws events describe-rule --name eq-structured-graph-ingest-rule` and `--name action-item-graph-rule`. Cite their current Source + DetailType filters verbatim. The new workflow's final-step EventBridge emit MUST flow through whichever rules are intended (`eq-structured-graph-ingest-rule` for the Neo4j consumer; new `AccountProvisioning.*` events may need new rules in those repos).
  - **Downstream consumer Pydantic models** — `action-item-graph/src/action_item_graph/models/envelope.py` and `eq-structured-graph-core/app/models/envelope.py`. Cite the relevant enum / required-field shapes verbatim with file:line references and current commit SHA.
- **Idempotency analysis** — DBOS provides workflow-level idempotency keys but tasks are at-least-once. The final EventBridge emit step can retry. Decide: (a) keep an outbox-style publication ledger in Postgres, or (b) push dedup responsibility to consumers via stable event IDs. Document the choice with reasoning.
- **All five test-discipline expectations addressed explicitly** — for each new component, name the live-substrate test, the per-branch E2E case, the narrow exception handling, the schema probe, and the cross-service contract probe.
- **Phase 2 + 3 compounding considerations** — sketch how `@DBOS.scheduled` cron + `set_event`/`get_event` HITL primitives serve Phase 2 progressive enrichment and Phase 3 conflict resolution. Not detailed design — just confirm the substrate compounds.
- **Cross-repo coordination tasks IDENTIFIED but NOT executed** — frontend queue-UI implications, any new EventBridge rules needed in downstream consumer repos.
- **Sequencing** — DBOS install + Neon `dbos.*` schema migration → workflow definition → queue routes refactor → final-step EventBridge emit → cutover plan → production E2E extension with new branch cases.

### Deliverable 2 (Step 7): Revise design doc Section 6

File: `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` Section 6 (durability machinery)

Replace the polling-worker + outbox + publisher description with the DBOS architecture. Document:

- **What was kept** — `workers/materialization.py` SQL, EventBridge cross-service notification, three-layer idempotency invariants (now via DBOS keys + publication ledger).
- **What changed** — durable execution via DBOS in-process instead of bespoke worker + outbox + publisher process. No new Railway service. No RabbitMQ.
- **Why** — Codex consult outcome + OSS-strict constraint + solo-founder + Railway operational fit.

### Deliverable 3 (Step 8): Fresh checkpoint

After Deliverables 1 + 2 are done, run `/context-save` (or write the checkpoint file directly to `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/`) with title `phase-1.5-dbos-plan-written` or similar. The checkpoint should capture: which plan file was written, which design-doc section was revised, what cross-repo coordination tasks were identified, and what the NEXT-NEXT session's scope is (plan execution = code-writing).

---

## What this session does NOT do

- **Write code.** None. Zero. The plan describes what code to write; the next session writes it.
- **Migrate data.** Schema stays.
- **Touch action-item-graph or eq-structured-graph-core.** Those repos have their own agents.
- **Detailed Phase 2/3 design.** Sketch how DBOS compounds; don't design Phase 2/3.
- **Cross-repo execution.** Identify what needs coordination; don't execute.

---

## The user

Non-developer founder. Make confident technical decisions; surface only product / strategic decisions for the user to weigh in on. Work without stopping for clarifying questions; make the reasonable call and continue; the user redirects if needed.

**The user cares about:** What a cutting-edge 2026 AI-native startup would actually build. Architectural correctness over short-term shortcuts. Maintaining full project context across sessions so any new agent can pick up where the prior left off. Strict OSS-only stance (no SSPL, no BSL, no source-available).

**The user does NOT care about:** Preserving sunk-cost code. Hitting arbitrary deadlines over correctness. Patterns that don't represent 2026 best practice.

---

## Pre-flight checks at session start

1. Run `/context-restore`. Expect the `phase-1.5-rethink-decided-dbos` checkpoint dated 2026-05-15.
2. Confirm `MEMORY.md` status reads `PHASE_1.5_RETHINK_DECIDED_DBOS — IMPLEMENTATION_PLAN_WRITING_PENDING`.
3. Read this handoff + the snapshot Section 5 + the bottom two lessons.
4. `git status` — should be clean. `git log --oneline -5` should show the doc-only commits from this session at top.
5. Confirm with the user that the substrate is still DBOS before plan-writing (cheap check in case context has changed).
6. Execute Deliverable 1, 2, 3 in order.

---

## Final note

The user is paying for thinking, not typing. The implementation plan is the load-bearing artifact of this session. Take time to actually probe the live contracts — that's what the "Verified contracts" section is for, and the discipline that produced this session's hard-won lesson is the discipline that protects the plan from shipping with another silent gap. Don't shortcut.

When in doubt, the checkpoint at `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/20260515-095506-phase-1.5-rethink-decided-dbos.md` has the full decision record, all eliminations with reasons, and the rationale for every choice. Future sessions should know not just what was picked but what was explicitly rejected, so they don't re-litigate.
