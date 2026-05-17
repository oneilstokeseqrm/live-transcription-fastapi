# Contact Quality Initiative — Context Snapshot (2026-05-15, last updated 2026-05-17 evening)

**Purpose:** This document is the **standalone entry point** for any agent picking up the Contact Quality and Account-Anchoring Initiative cold. A new agent should be able to read this single document and understand: what the project is, why it exists, what's shipped, what's in flight, what's broken, what's next, and where to read the canonical details.

**Audience:** Any future Claude session (architecture rethink, code execution, planning, review). The user is a non-developer founder; the agent makes confident technical decisions and surfaces only product / strategic decisions.

**Use:** Read this first. Then read `NEXT-SESSION-START-HERE.md` for the specific work the next session does. Then read the canonical detail docs as the work requires them.

---

## 0. CURRENT STATE — 2026-05-17 evening (read this section first; the rest is historical)

**Status:** `PHASE_1_EMAIL_PIPELINE_M1_M2_DEPLOYED_M3_NEXT`.

**What changed since this doc was originally written (2026-05-15):**

- **Phase 1.5 fully shipped** across 2026-05-15 and 2026-05-17 — DBOS-based async account-provisioning workflow + verified-contract tooling. M0/M1/M2/M3/M4/M5 all merged and deployed (PRs #14, #15, eq-frontend #373, #17, #18).
- **Phase-1-email-pipeline** introduced as a new sub-initiative finishing committed Phase 1 work (Task 1.24 acceptance criteria around cold-inbound unknown-sender emails). The plan lives at `eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` (v4, committed as `033626a`, locked after 4 Codex plan-review rounds + 11 substantive findings).
  - **M1 (eq-frontend Prisma migration)**: SHIPPED + DEPLOYED 2026-05-17 evening. PR #392, merged as `de586bbc`. Vercel applied the migration; Neon production has the new `pending_interactions` table + 3 new `emails` columns + composite UNIQUE+FK on `interaction_summaries` (replacing single-column UNIQUE that was blocking the multi-variant model) + composite UNIQUE on `raw_interactions`.
  - **M2 (live-transcription-fastapi workflow promote-step + EmailPromoted emit)**: SHIPPED + DEPLOYED 2026-05-17 evening. PR #19, merged as `756575d7` 49 seconds after M1. Railway deployment `809679fc` SUCCESS; /health 200.
  - **M3 (eq-email-pipeline EmailPromoted subscriber)**: NEXT. Implementation in `/Users/peteroneil/eq-email-pipeline` per plan §6.2. New helpers + handler with two-layer idempotency guard. Subscriber pattern is **SQS-from-EventBridge** (resolved 2026-05-17 evening — plan §14 #2). Requires AWS infrastructure setup (rule + SQS queue + DLQ + IAM) before deploy — full spec in `NEXT-SESSION-START-HERE.md` section "AWS infrastructure for M3."
  - **M4 (eq-email-pipeline orchestrator branch + atomic upsert_thread rewrite)**: FUTURE SESSION. FLIPS THE SWITCH on cold-inbound capture.
  - **M5 (production E2E + rollback drill)**: FUTURE SESSION.

**18 LOCKED decisions** (do NOT re-litigate). Full list in `NEXT-SESSION-START-HERE.md` section "LOCKED decisions" — includes DBOS substrate, EventBridge Path A with closed detail-type lookup, hard rules (no contact / no interaction without account anchor), Approach C (pending_interactions separate table), lean+typed schema (not JSONB), Path B (full reprocess on promote via EmailPromoted), shared-tenant collision protocol, Codex multi-round defaults.

**Verified cross-repo state** (2026-05-17 evening):
- eq-frontend `main` HEAD includes M1 merge `de586bbc`. Production Neon reflects the schema changes (verified by direct query).
- live-transcription-fastapi `main` HEAD `cea72cd` (M2 + handoff docs). Production Railway deployment `809679fc` SUCCESS; /health 200.
- eq-email-pipeline `main` HEAD `033626a` (only the plan doc; no code work yet).
- Production EventBridge: NO `EmailPromoted` rule exists yet — M3 agent must create it before M3 ships (full AWS CLI spec is in `NEXT-SESSION-START-HERE.md`).

**Acknowledged V1 limitations** (NOT regressions; documented + bounded):
1. Personal/internal anchor cold-inbound → log+drop. V2: audit log table.
2. Neo4j build_skeleton + write_flesh partial-retry corruption — mitigated by M3's 2-layer guard (atomic CAS + 5-min soft TTL). V2: MERGE patterns + edge-count thread counters.
3. `upsert_thread` race — FIXED in M2 for workflow promote path; M4 will fix it for eq-email-pipeline orchestrator known-account path.
4. Legacy per-signal loop hardcodes `summary_type='meeting'` — for re-pointed email signals creates cosmetic duplicate 'meeting' summary alongside 'email' summary. Cosmetic; downstream filters by summary_type='email' get the correct link. Future cleanup.
5. eq-frontend `live-db` CI workflow missing `DIRECT_DATABASE_URL` env var. Pre-existing; not a regression. Follow-up PR in eq-frontend.

**Key recent lessons** (codified to `tasks/lessons.md` 2026-05-17 evening):
- Cross-repo constraint-relaxation requires SQL audit before plan lock — GREP all repos for `ON CONFLICT` clauses referencing the to-be-relaxed UNIQUE before locking the plan.
- Codex round-N convergence pattern — extend the 4-round soft cap when severity is decreasing AND each round adds NEW non-redundant findings.

**Load-bearing handoff artifacts (2026-05-17 evening):**
- `docs/superpowers/specs/NEXT-SESSION-START-HERE.md` — wayfinding doc for M3.
- `docs/superpowers/specs/2026-05-17-evening-m3-next-session-prompt.md` — paste-ready M3 opening prompt (comprehensive; self-contained pre-flight + production credentials + helper signatures + handler skeleton + AWS infra spec).
- `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/20260517-183707-phase-1-email-pipeline-m1-m2-deployed-m3-next.md` — final checkpoint.

**The rest of this document (sections 1-N below) describes the project as of 2026-05-15.** That historical narrative is still useful for understanding why the initiative exists and what its phases were originally planned to be, but treat anything inconsistent with the section above as superseded.

---

## 1. What this project is

The **Contact Quality and Account-Anchoring Initiative** imposes data-quality discipline on the contact and account layer of an AI-native customer intelligence platform. The platform ingests business interactions — meeting transcripts, emails, calendar events — and converts them into structured intelligence about who customers are, what they care about, and what to do next. Contacts and accounts are the load-bearing entities; if their integrity is wrong, every downstream intelligence layer (briefings, recommendations, executive runs, forecasts) inherits the corruption.

The initiative establishes two non-negotiable invariants:

- **Hard Rule 1:** No contact exists without an account anchor.
- **Hard Rule 2:** No interaction is persisted without an account anchor.

And introduces mechanisms to enforce them across all ingestion paths and downstream consumers.

The initiative is **multi-phase**:

- **Phase 1** — Tighten the account-anchoring contract end-to-end at every ingestion path. **SHIPPED 2026-05-14 (PR #10, PR #11).**
- **Phase 1.5** — Async worker + outbox + queue UI for handling unknown business domains discovered during ingestion. **IN FLIGHT, blocked by architecture-rethink decision as of 2026-05-15.**
- **STOPPING POINT** — Comprehensive re-planning before any Phase 2 commitment.
- **Phase 2** (future, not committed) — Identity state machine + progressive enrichment.
- **Phase 3** (future, not committed) — Conflict resolution, multi-account history, fuzzy matching.

## 2. Why this project exists

A prior initiative (transcript contact enrichment, completed 2026-04-01) made meeting-attendee-to-canonical-contact resolution real and operational. That initiative shipped the participation graph (calendar matching, contact persistence, canonical ID propagation through EventBridge, Neo4j Contact node MERGE), but did NOT enforce quality constraints. Investigation surfaced six specific gaps:

1. The `lookup_account_by_domain()` capability exists in eq-email-pipeline but was wired into only one of three contact-creation paths — calendar_sync and the transcript pipeline produced contacts with NULL account_id.
2. The transcript pipeline's WebSocket endpoint hardcoded `account_id=None` in two locations, bypassing frontend's account-anchoring intent.
3. The transcript pipeline applied the meeting's anchor account_id to all attendees uniformly, falsely attributing external-org attendees (e.g., a partner consultant on a customer call) to the meeting's anchor account.
4. The eq-agent-action-core service has production-deployed account-research using Tavily web research + Claude-driven AccountProfile generation, but is not invoked by any ingestion pipeline for unknown business domains.
5. An admin UI prototype at `/dashboard/organization/email-pipeline` had Map / Create / Ignore actions on a pending-domains queue, but Create just set a status flag without invoking the agent; the queue was populated only by email, not transcripts.
6. 99.3% of Neo4j Interaction nodes (1,017 of 1,024 in test data) lacked a `BELONGS_TO→Account` relationship — both a seeding pattern and the absence of enforcement.

The initiative closes these gaps end-to-end across 6 repositories.

## 3. The phasing in detail

### Phase 1 — Account-anchoring contract tightening (SHIPPED 2026-05-14)

Every ingestion path now either resolves `account_id` at the request boundary or rejects the request. Per-attendee domain resolution replaces the uniform-anchor-application behavior. Unknown business domains are captured in the queue (no orphan contacts). Types and signatures across the codebase converge on `account_id` being required.

**Three-state per-attendee branching** (replaces uniform-anchor fallback):

- PERSONAL → skip (gmail.com, yahoo.com, etc.)
- INTERNAL → skip (a tenant's own domains from provider_connections)
- BUSINESS+known → contact created with looked-up account_id
- BUSINESS+unknown → queue signal, no contact

**Shipped scope:**
- `EnvelopeV1.account_id`, `RequestContext.account_id`, `process_transcript(account_id)`, `UploadJob.account_id` all required at validation/signature.
- Backend rejection (400 / WebSocket close 1008) at every ingestion path on missing account_id.
- `services/transcript_enrichment.py` orphan-creation path REMOVED.
- New shared utilities: `services/domain_classification.py`, `services/name_resolution.py`, `services/account_lookup.py`, `services/pending_account_mappings.py`, `models/participant_spec.py`.
- Cross-repo: eq-frontend Prisma schema migration (8 new columns on `pending_account_mappings`, new `pending_account_mapping_signals` table, nullable→NOT NULL on `raw_interactions.account_id`); eq-email-pipeline three-state branching in calendar_sync + orchestrator.

**Codex review verdict:** Round 1 found 3 P1 + 3 P2. P1s fixed in Tasks 1.26.1/.2/.3. Round 2: GATE PASS, 0 P1.

**PRs merged:**
- live-transcription-fastapi PR #10 at `2552b4b`
- live-transcription-fastapi PR #11 at `f52f41d` (Phase 1.5 P2 cleanup: auth-context split, participants flow-through, interaction_id threading)
- eq-email-pipeline PR #6 at `895cc9f`

**Production E2E:** 13/13 PASS post-Phase-1-merge; 13/13 PASS after Phase 1.5 P2 cleanup with 4 new cases.

### Phase 1.5 — Worker, outbox, queue UI (IN FLIGHT — current focus)

**Original architecture (now under rethink):**

- Poll-based worker (`workers/account_provisioning_worker.py`) polls Postgres every 5s for queue entries with `status='approved'`.
- Worker takes advisory lock per queue_id (multi-process safety).
- Worker calls eq-agent-action-core for account-research-and-creation.
- Worker runs atomic materialization in single Postgres txn: contacts INSERT (ON CONFLICT) → raw_interactions UPSERT → placeholder summary UPSERT → interaction_contact_links → queue UPDATE → outbox INSERT.
- Separate publisher (`workers/outbox_publisher.py`) polls the outbox every 2s, emits to EventBridge with FOR UPDATE SKIP LOCKED for multi-process safety.
- Queue action HTTP routes (`routers/queue_actions.py`): POST /queue/{id}/approve, /map, /ignore with idempotency on `approval_attempt_id`.

**What's shipped to main:**
- PR #12 at `11b3b30` — worker foundation (advisory lock, agent client, materialization, worker loop, entrypoint). 6 rounds of Codex review.
- PR #13 at `ad7c710` — outbox publisher + queue authorization + approve/map/ignore routes. 6 rounds of Codex review; 22 real bugs fixed with permanent regression tests; 2 Round 7 findings deferred-by-design with inline TODOs.
- Production E2E 20/20 PASS post-PR-#13-merge (13 prior + 7 new queue route smoke cases).
- All Phase 1.5 schema live in Neon eq-dev via cross-repo eq-frontend PR #366.

**What's NOT running:** the worker process itself. The Railway service to run `python -m workers` was scheduled to be deployed 2026-05-15 (this session) but was BLOCKED. See section 5 below.

### Phase 2 (future, NOT committed scope)

Identity state machine + progressive enrichment. Contacts gain explicit identity-completeness state (`shell` / `emerging` / `partial` / `resolved` / `verified`). State determines downstream behavior (UI surfacing, scoring weight, re-enrichment cadence). Re-enrichment runs async; new signals trigger state transitions.

### Phase 3 (future, NOT committed scope)

Advanced policies: conflict resolution (same email, different accounts over time), multi-account history (contact moves companies), fuzzy matching (email-domain ambiguity, personal-email-on-business-conversation).

## 4. Cross-repo dependencies

Six repositories participate in the initiative. Understanding which one owns what is load-bearing.

| Repo | Role in initiative | Status |
|------|-------------------|--------|
| `live-transcription-fastapi` | Primary repo. Transcript + text + upload ingestion. Queue routes. Worker. Publisher. | Phase 1 + Phase 1.5 P2 + Phase 1.5 main scope CODE all in main. Worker not deployed. |
| `eq-email-pipeline` | Email ingestion. Three-state branching in calendar_sync + orchestrator. | Phase 1 changes shipped 2026-05-14 in PR #6. |
| `eq-structured-graph-core` | Neo4j Account/Contact MERGE. AccountCreated consumer (will read from EventBridge once Phase 1.5 worker is live). | Existing service unchanged for Phase 1. |
| `action-item-graph` | Downstream consumer of state-aware contacts. | Existing service unchanged for Phase 1. |
| `eq-frontend` | Prisma schema owner. Queue UI (cross-repo Phase 1.5 work, separately tracked). Auth (Auth0 + INTERNAL_JWT mint). | Phase 1.5 schema applied to Neon eq-dev (PR #349 still open with non-migration CI hygiene issues; PR #366 applied schema). |
| `eq-agent-action-core` | AI-powered company-research service. Tavily web search + Claude AccountProfile generation. **Production-deployed for onboarding, NOT for queue-driven account creation.** | Discovered 2026-05-15 that the worker code in PR #12 was written against an imagined contract. Service is research-only; never writes to our Postgres accounts table. |

Postgres (Neon project `super-glitter-11265514`, eq-dev) is shared across services. Neo4j (Aura instance `c6171c63`) is shared across graph services. EventBridge bus is the cross-service event substrate (Kinesis is also in use for transcript chunks).

## 5. Current status: where we are RIGHT NOW (2026-05-15, end-of-day)

**✅ RETHINK DECISION LOCKED. IMPLEMENTATION PLAN WRITING PENDING.**

The Phase 1.5 architecture rethink completed Steps 1-5 of the 8-step decision process from `2026-05-15-async-orchestration-rethink-brief.md` Section 8. The substrate is decided: **DBOS** (Apache 2.0 / MIT, library-only durable execution with Postgres-as-durability layer). Steps 6 (write the DBOS implementation plan) and 7 (revise design doc Section 6) are deferred to the next session to preserve plan-authoring quality under fresh context.

### Decisions made in the rethink session (chronological)

- **D1 — Approve UX:** Async + visible streaming progress (user watches the AI reason during the 30-90s agent call). Eliminated sync-in-route.
- **D2 — Volume:** 100-1000 approvals/day across tenants, with onboarding bursts. Eliminated Temporal as operationally heavy for solo-founder + Railway shop.
- **D3 — Undoable:** Archive-only (no compensating-action primitives required from the substrate).
- **D4 — Phase 2 visibility:** Invisible-by-default progressive enrichment.
- **D5 — Substrate scope:** ONE substrate across Phase 1.5 + 2 + 3 (the "AI workflow runtime for the platform," not just queue infrastructure).
- **D6 — Initial substrate pick:** Hatchet (subsequently revised).
- **HARD CONSTRAINT (user interjection):** Strictly open-source only. NO SSPL, NO BSL. Eliminated Inngest (SSPL) and Restate (BSL).
- **D7 — Substrate revision after Codex consult:** PIVOT to **DBOS**. Codex's design-time consult surfaced material risks with Hatchet: Railway's official deployment is `hatchet-lite` which Hatchet's own docs limit to "development and low-volume use-cases"; RabbitMQ stays a real dependency; at-least-once retry semantics mean the outbox-or-equivalent ledger isn't fully replaced; open production-stability issues (DB corruption, stuck-RUNNING workflows, unbounded session growth, Python SDK reconnection critical issue). DBOS aligns more cleanly with solo-founder + Railway + Neon + FastAPI constraints.

### Why DBOS

- **License:** Apache 2.0 / MIT (verified at https://github.com/dbos-inc/dbos-transact-py) — strictly OSI-compliant open source.
- **Architecture:** Library-only. `pip install dbos-transact` + connect to existing Neon Postgres = done. NO new service to deploy on Railway.
- **Durability:** Postgres IS the workflow log. DBOS checkpoints workflow state to a `dbos.*` schema on every step.
- **Phase 2/3 primitives:** Queues, scheduled workflows (cron), durable sleep, human-in-the-loop (`recv` / `send` / `set_event` / `get_event`) — all library-native and documented.
- **Cross-service notification:** EventBridge stays as the inter-service notification layer. DBOS's final workflow step calls `put_events` synchronously inside a DBOS step. At-least-once retry semantics mean the publication-identity / consumer-dedup discipline still applies (see Item 5 in `tasks/downstream/test-discipline-gaps-2026-05-15.md`).

### What was eliminated, with reasons

| Option | Status | Reason |
|--------|--------|--------|
| Sync-in-route | ❌ | D1 eliminated: streaming UX over 30-90s exceeds Railway HTTP timeouts |
| AWS Step Functions | ❌ | DX wrong era; Lambda-task constraint conflicts w/ Python-FastAPI; AWS lock-in |
| Cloudflare Workflows | ❌ | TypeScript-only |
| Trigger.dev | ❌ | Python SDK less mature than alternatives |
| Postgres LISTEN/NOTIFY + pg-boss | ❌ | pg-boss is Node-only; Python equivs are job queues not workflow runtimes; D5 altitude eliminates them |
| Polling worker + outbox (legacy) | ❌ | The pattern being replaced |
| Inngest | ❌ | SSPL license (source-available, not OSS) |
| Restate.dev | ❌ | BSL license (source-available) |
| Temporal | ❌ | MIT (genuine OSS) but operationally too heavy for solo-founder + Railway |
| Hatchet | ❌ | D7 reversal — Railway production story is `hatchet-lite` (dev-only per docs); RabbitMQ stays a real dep; open production-stability issues |
| **DBOS** | ✅ | **D7 pick.** Library-only + Postgres-native + Apache 2.0/MIT + Python-primary + lowest operational burden + all required workflow primitives |

### What's NOT done yet (next session scope)

- **Step 6** — Write the DBOS implementation plan at `docs/superpowers/plans/2026-05-XX-async-orchestration-dbos.md` with explicit "Verified contracts" section probing Neon schema + downstream EventBridge rules + consumer Pydantic models + agent OpenAPI.
- **Step 7** — Revise this design doc's Section 6 (durability machinery) to match the DBOS architecture.
- **Step 8** — Fresh checkpoint after the plan is written.
- **A separate later session** executes the new plan (code-writing — out of rethink scope).

## 6. The hard invariants that must hold across phases

These are load-bearing across ALL phases. Any new architecture must preserve them:

### Product invariants (from design doc Section 3)

1. **No contact without an account anchor** (with the queue-resolution transient exception).
2. **No interaction without an account anchor** (same exception).
3. **Backend rejection is the enforcement mechanism**, not frontend trust. Every ingestion path validates `account_id` at the auth-context boundary or returns 400 (WebSocket: close 1008).
4. **Three-state branching with NO fallback-to-anchor** for unknown-domain attendees (per-attendee domain lookup → known account, queue signal, or skip; never anchor-fallback).
5. **First-owner-wins UPSERT** on `(tenant_id, domain)` for queue entries.
6. **Tenant isolation** is absolute. No cross-tenant queries ever. TRUNCATE ignores tenant_id — verify FK topology before destructive ops.

### Data invariants

7. **Contact_id is always UUIDv4.** Never store a name without an ID.
8. **Cross-account contact reassignment fails loud** (Phase 3 scope; current code raises ValueError if a contact would move accounts).
9. **`participants=[]` is meaningful**, not equivalent to None. Empty list = "the caller explicitly told us no participants"; None = "the caller didn't tell us."
10. **NULL ≠ NULL in SQL.** For dedup constraints, fill the keys with the request's actual `interaction_id` as fallback when no calendar event exists.

### Engineering invariants

11. **Caller-side completeness**: when adding a new parameter to an internal function, update every caller in the SAME commit. Never defer wiring.
12. **Auth boundary wins on body/header conflicts.** Body fields with same semantic as auth-header values are at best verification checks; reject mismatch.
13. **Real /codex review is non-substitutable** at every phase boundary. Static-invariant self-review has missed P1s.
14. **Production E2E with a Railway-signed JWT is the final quality gate**, wired into every phase ship.
15. **External service contracts must be probed at design time**, not deploy time. Read the live `/openapi.json` (or equivalent) before designing code against the service. THIS WAS VIOLATED — the lesson is in `tasks/lessons.md`.
16. **/context-save at session end is mandatory** for multi-session continuity.

### Idempotency invariants (worker-specific; any new architecture must preserve)

17. **Three-layer idempotency:**
    - `worker_attempt_id` (`{tenant_id}:queue-{queue_id}`) — stable per (tenant, queue) so the agent dedupes replays
    - `outbox_row_id` — durable event log entry
    - `approval_attempt_id` — frontend approval idempotency
18. **Replay-safe via terminal-status guards.** `status='mapped'` and `archived_at IS NOT NULL` are treated as no-ops.
19. **Per-entry transaction isolation.** Each queue entry processes in its own session with its own commit. One entry's failure does not roll back others.
20. **Race-safe placeholder summary** via ON CONFLICT (interaction_id) DO UPDATE pattern.

### Outbox + publisher invariants (current implementation; may move to different framework)

21. **Per-row FOR UPDATE SKIP LOCKED** in publisher — multi-process safe.
22. **MARK_FAILED in fresh session AFTER lock_session releases** — no self-deadlock.
23. **`WHERE published_at IS NULL`** on MARK_FAILED — no contradictory post-publish failure stamps.
24. **`ORDER BY publish_attempts ASC, created_at ASC`** — failed-row rotation prevents starvation.

### Queue action invariants (current implementation; may simplify)

25. **Canonical-UUID Pydantic validators** on ApproveRequest + MapRequest — replays survive uppercase/braced inputs.
26. **`_effective_user_id(ctx) = pg_user_id or user_id`** for auth check — matches the insert pattern from ingestion routes.
27. **/ignore requires UUID-shaped effective user_id** (400 guard before SQL) — Auth0-only JWTs cannot ignore.
28. **Status filters** on APPROVE_SQL + MAP_RESERVE_SQL + IGNORE_SQL — replays don't mutate terminal rows.
29. **Tenant-scoped account lookup before /map materializes** — prevents cross-tenant attachment.
30. **/ignore cascades archive to child signals** — prevents re-consumption on reopen.

The invariants in sections 1-16 are **load-bearing at the product and engineering level** and must hold across phases regardless of architecture. The invariants in sections 17-30 are **load-bearing at the current implementation level** — a new architecture that achieves the same product guarantees through different primitives is acceptable; a new architecture that drops a product guarantee is not.

## 7. The product trajectory in one paragraph

We are building an AI-native customer intelligence platform that ingests business interactions (transcripts, emails, calendar) and produces structured intelligence about customers. The Contact Quality Initiative is a foundational hardening of the entity layer — contacts and accounts — that everything else stands on. Phase 1 tightened the contract end-to-end (every ingestion path resolves `account_id` or rejects; per-attendee domain resolution replaces uniform-anchor; unknown business domains captured in queue). Phase 1.5 builds the async machinery that handles those queued unknowns (research the domain, create the account, materialize contacts, notify downstream consumers). After Phase 1.5 lands, the project hits an explicit stopping point for comprehensive re-planning before Phase 2 (identity state machine + progressive enrichment) or Phase 3 (advanced conflict-resolution policies). Throughout, the design is grounded in emerging AI-native patterns (GraphRAG-style account-centric graphs, agentic identity resolution, outbox/saga durability) — not legacy CRM patterns. The system aims to be the kind of customer intelligence tool that a cutting-edge 2026 company would actually want to use, not a fancier version of Salesforce.

## 8. Canonical detail docs (read by need, not all at once)

| Doc | Purpose | When to read |
|-----|---------|--------------|
| `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` | Full design doc (revised 2026-05-13; approved). 15 Codex findings integrated. | When designing or re-planning any phase |
| `docs/superpowers/specs/2026-05-12-contact-quality-initiative-codex-review.md` | Codex's 15 findings on the original design. Audit trail; all integrated. | Historical reference only |
| `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` | Implementation plan (~55 tasks, Phase 1 + 1.5). Includes "Phase 1.5 Production E2E Discipline" section. | When executing Phase 1.5 tasks |
| `docs/superpowers/specs/NEXT-SESSION-START-HERE.md` | The next session's specific work order. Rewritten each session. | FIRST thing each session reads |
| `docs/superpowers/specs/2026-05-15-async-orchestration-rethink-brief.md` | The architecture rethink scope. Neutral framing — does not anchor on any option. | Next session reads after this snapshot |
| `docs/superpowers/research/2026-05-15-durable-execution-landscape.md` | 2026 landscape of durable execution / async orchestration frameworks. | During the rethink, as reference |
| `docs/superpowers/specs/2026-05-14-dispatch-patterns-research.md` | Earlier research on polling vs CDC vs EventBridge Pipes (for the previous-architecture context). | Reference when comparing what we had vs. what we might pick |
| `docs/superpowers/specs/2026-05-14-phase-1-5-ai-native-research.md` | AI-native thought-leadership scan that informed Phase 1.5. | Reference when grounding choices in 2026 patterns |
| `tasks/downstream/blocker-agent-contract-mismatch.md` | Path A tactical fix — superseded by rethink decision. | Historical / fallback reference only |
| `tasks/downstream/railway-phase-1-5-worker.md` | 6-step Railway deployment recipe — superseded by whatever rethink picks. | Historical reference |
| `tasks/lessons.md` | Codified lessons across the initiative. READ THE NEW ONES at bottom for current pitfalls. | Each session start |
| `docs/contacts-architecture.md` | Full cross-service contacts architecture (Postgres + Neo4j + service-by-service). | When the work touches a downstream service |
| `docs/contact-enrichment.md` | Contact enrichment feature (prior initiative). | Reference for understanding the participation graph |

Memory (auto-loaded at session start):
- `~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/MEMORY.md` (index)
- `~/.claude/projects/.../memory/project_contact_quality_initiative.md` (this initiative's full history)
- Plus feedback / reference memory files (test tenant, Prisma ownership, Neo4j instance, destructive-ops blast radius, etc.)

## 9. What's working in production right now

Even though Phase 1.5 worker is blocked, **a lot is working in production:**

- FastAPI service at `https://live-transcription-fastapi-production.up.railway.app` serving all ingestion routes (WebSocket /listen, /text/clean, /batch/process, /upload/init + /upload/complete) with full Phase 1 + Phase 1.5 P2 enforcement.
- Queue routes (POST /queue/{id}/approve, /map, /ignore) all serving correctly with full Codex-reviewed auth/idempotency.
- Production E2E suite at `/tmp/e2e_phase_1_production.py`: 20/20 PASS against the live service. **Important caveat — see Section 9.1 below.**
- All shipped code (PRs #10, #11, #12, #13, and the 2026-05-15 fix at commit `31f513f`) intact and merged in main.
- Postgres schema (Neon eq-dev) has all Phase 1.5 columns + tables.
- Three-state branching live for all four ingestion paths and **verified working** as of 2026-05-15.

What's not in production: the worker process that consumes the queue. Approved queue entries currently accumulate without being processed because there is no worker container running `python -m workers`. The queue UI (eq-frontend) may also be incomplete (it was tracked as separate cross-repo work).

### 9.1 Phase 1 had a 24-hour silent regression — fixed 2026-05-15

**What happened:** Phase 1 shipped at commit `2552b4b` (2026-05-14) introduced a bug in `services/account_lookup.py` — the SQL queried `FROM accounts WHERE ... AND lower(domain) = :domain`, but the `accounts` table has no `domain` column (the correct join table is `account_domains`). Every transcript whose calendar event had BUSINESS-domain attendees triggered a SQL error inside `lookup_account_by_domain`. The error was silently swallowed by the outer try/except at `services/transcript_enrichment.py:399-405`, causing `enrich()` to return an empty `EnrichmentResult`. The downstream effect: ZERO rows produced in `raw_interactions`, `interaction_contact_links`, and `calendar_event_interaction_links` for the affected meetings. The /text/clean response was still 200; intelligence-extraction tables (`interaction_summary_entries`, `interaction_insights`) still populated because they're independent of the contact path.

**How long it was live:** ~24 hours, from PR #10 merge on 2026-05-14 to fix on 2026-05-15.

**How it was found:** Not by our own test suites or production E2E. The eq-synthetic-date-generation agent (a downstream observer in a separate repo) traced the bug while investigating why their calendar-event matching was dropping data.

**How it shipped through six quality gates:** the lessons doc has the full breakdown (`tasks/lessons.md` → "Four systemic quality gaps that let a silent regression ship Phase 1"). Summary: Codex review can't see live schema; all unit tests used mocks that didn't execute SQL; all integration tests patched `lookup_account_by_domain` at the import level so the real query never ran; production E2E covered auth/validation boundaries but no happy-path-through-branching cases.

**The fix:** commit `31f513f` on main (deployed at Railway deployment `0ac9010d-7ddd-4d86-af0d-285fcb71e675`, SUCCESS 2026-05-15). SQL changed from `accounts.domain` to `account_domains` (the correct join table). Two new regression tests added including a SQL-text assertion that catches mock-only test coverage gaps.

**What this means for the rethink:** Phase 1's product behavior is now verified-correct. The rethink proceeds on a foundation that we KNOW works end-to-end (not just "tests pass"). But the four systemic quality gaps that let this ship apply equally to whatever architecture the rethink picks — see `tasks/downstream/test-discipline-gaps-2026-05-15.md` for follow-up actions that should fold into the new implementation plan.

## 10. Production credentials + IDs

Locked in across the initiative:

- **Neon Postgres (eq-dev):** project `super-glitter-11265514`, branch `default`, database `neondb`.
- **Test tenant:** `11111111-1111-4111-8111-111111111111` (note: column is `tenants.id`, NOT `tenants.tenant_id`). All current data is test data; safe to seed.
- **Railway FastAPI service:** project `inspiring-upliftment` (`847cfa5a-b77c-4fb0-95e4-b20e8773c23e`), service `59a69f3d-9a24-4041-942a-891c4a81c5fb`, env `e4c5ec15-1931-4632-9e58-92d9c6be4261`, URL `https://live-transcription-fastapi-production.up.railway.app`.
- **Railway eq-agent-action-core service:** project `eq-agent-action-core` (`421e079f-2e46-4c22-83c4-0fe6208e6aff`), service `3036ea0f-afc9-4bc4-889d-c98617d81e96`, env `f2c0a13f-40c6-4514-9c02-acac2a22c05c`, URL `https://eq-agent-action-core-production.up.railway.app`.
- **Internal JWT:** HS256, secret shared across FastAPI ↔ agent (`INTERNAL_JWT_SECRET`), `iss=eq-frontend`, `aud=eq-backend`, claims: `tenant_id` (UUID), `user_id`, optional `pg_user_id`.
- **AWS:** EventBridge bus name configurable via `EVENTBRIDGE_BUS_NAME` (default `default`); `AWS_REGION=us-east-1`; access keys in Railway env.
- **Neo4j:** Aura instance `c6171c63`, URI `neo4j+s://c6171c63.databases.neo4j.io`, shared across graph services.

## 11. Active feedback rules (carry forward, all sessions)

From auto-memory `feedback_*.md` files:

- **Tenant isolation:** never cross-tenant queries or checks. Core invariant.
- **Branch safety:** use feature branches; rebase before merge; test everything; document as you go.
- **Destructive ops blast radius:** before ANY TRUNCATE / DROP / DELETE-without-WHERE / CASCADE / rm -rf / git reset --hard / force push — even on "test data" — verify FK cascade chain and CONFIRM WITH USER.
- **Contact_id consistency:** every contact must always carry UUIDv4 contact_id; never store name without ID.
- **Downstream investigation:** investigate downstream thoroughly but don't refactor; document for each service's agent.

## 12. The user

A non-developer founder. **Make confident technical recommendations and decisions directly. Surface only product or strategic decisions for the user to weigh in on.** Do NOT ask the user to validate fix patterns, configuration details, or task sequencing. Work without stopping for clarifying questions; make the reasonable call and continue; the user will redirect if needed.

**The user explicitly cares about:** building what a cutting-edge 2026 AI-native startup would actually build, not legacy CRM patterns. Architectural correctness over short-term shortcuts. Maintaining full project context across sessions so any new agent can pick up where the prior left off.

**The user does NOT care about:** preserving sunk-cost code, hitting an arbitrary deadline over correctness, or maintaining patterns that don't represent 2026 best practice.

## 13. How to use this snapshot

1. **Session start:** Read this doc first (~10 min). Then read `NEXT-SESSION-START-HERE.md` (~5 min) for the specific work this session does. Then load the canonical detail docs as the work requires them.

2. **Before designing anything new:** check this doc's "hard invariants" section. Any design that violates a numbered invariant must explicitly justify the change in the design doc.

3. **Before making infrastructure / architecture decisions:** check the "phasing in detail" section to understand what's already shipped vs. what's in flight vs. what's future scope. Decisions for Phase 1.5 should consider Phase 2 + Phase 3 needs.

4. **When the work is done:** update this doc if the project state changed. Update `MEMORY.md` status string. Update `NEXT-SESSION-START-HERE.md` for the next session. Save a /context-save checkpoint. These are the load-bearing handoff artifacts.

5. **When in doubt:** the design doc (`2026-05-12-contact-quality-initiative-design.md`) is the canonical product reference. The plan doc (`2026-05-13-contact-quality-phase-1-and-1.5.md`) is the canonical execution reference. This snapshot is the orientation layer above both.
