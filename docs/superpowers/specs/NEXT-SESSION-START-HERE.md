# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — a multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-17 PM (M5 verified-contract tooling shipped as PR #18).
**Status:** ✅ **PHASE_1.5_M5_SHIPPED_EMAIL_PIPELINE_GAP_NEXT** — Phase 1.5 main code complete (M0–M5 all shipped). New HIGH-priority finding surfaced + documented: `eq-email-pipeline` silently drops cold inbound emails from unknown business senders. This is the next session's primary scope.

---

## HARD CONSTRAINT FOR THIS SESSION

**This session is plan-writing, NOT implementation.** The user explicitly clarified 2026-05-17 PM. The eq-email-pipeline fix involves a new schema (new `pending_interactions` table or equivalent), cross-repo coordination (eq-frontend Prisma + eq-email-pipeline orchestrator + live-transcription-fastapi workflow), and at least a dozen open design questions (listed below). Compressing design + implementation into one session will produce half-baked work or run out of context.

The execution session is a separate ship that happens AFTER:
1. The user agrees with the chosen approach
2. Codex consult on the design passes
3. The implementation plan is written

If you finish plan-writing fast with substantial context remaining, the secondary unfinished items can be addressed (see "Unfinished Phase 1 + 1.5 — full list" below). But do NOT cram implementation work into this session at the expense of the plan quality.

---

## CRITICAL — multi-session, multi-repo, long-arc project

The Contact Quality Initiative is foundational hardening of the contact + account entity layer the AI-native platform stands on. Phase 1 SHIPPED 2026-05-14. Phase 1.5 milestones:

| Milestone | Status | PR |
|---|---|---|
| M0 — Railway operational prep | ✅ Shipped 2026-05-15 | (Railway-side change) |
| M1 — DBOS install | ✅ Shipped 2026-05-15 | PR #14 (dc0806c) |
| M1 hotfix — Codex P1s | ✅ Shipped 2026-05-15 | PR #15 (e334638) |
| M2 — Prisma UNIQUE INDEX | ✅ Shipped 2026-05-15 | eq-frontend PR #373 (6fbe4eb) |
| M3 + M4 — workflow + /approve cutover | ✅ Shipped 2026-05-17 AM | PR #17 (ae45737) |
| **M5 — verified-contract tooling** | ✅ **Shipped 2026-05-17 PM** | **PR #18 (95f9084)** |
| M3.5 — drop account_provisioning_outbox | ⏸ Optional, deferred | next session if context allows |

After Phase 1.5 closes: explicit stopping point for comprehensive re-planning before Phase 2 (identity state machine + progressive enrichment).

---

## THE NEXT SESSION'S PRIMARY SCOPE: finishing Phase 1 for the email pipeline

**Surfaced + recharacterized:** 2026-05-17 PM. Original handoff framed this as "newly discovered." User pointed out this was actually part of the original Phase 1 plan (Task 1.24) — the orchestrator was supposed to apply three-state branching to email sender/recipient resolution + queue unknown-business senders. Phase 1 PR #6 shipped logic + tests for the case where at least one party on the email belongs to a known account, but did NOT cover the case where ALL parties are unknown. **This is finishing committed Phase 1 work, NOT new scope.**

### The two scenarios in plain English

For any external person on an incoming email, the system needs to handle one of two cases:

1. **The person's email domain maps to an existing account.** → Tie the email to that account, create or update the contact. *(Works today.)*
2. **The person's email domain doesn't map to any account (but the email passed our spam/relevance filters — it's credible).** → Save the email in a coherent pending state, queue the domain for the user to approve account creation, and on approval: create the account + tie the email to it + materialize the contact. *(This is what's broken when no party on the email is from any known account.)*

### What Phase 1 actually delivered (incomplete)

`eq-email-pipeline` orchestrator does loop over every external person + classify them. When at least ONE person belongs to a known account, that account anchors the email; any OTHER unknown-business parties get queued. ✅

When NO person belongs to any known account (e.g., cold inbound from a totally new prospect), the orchestrator can't find an anchor; `insert_email` raises `ValueError` because `raw_interactions.account_id` is NOT NULL; the outer `Exception` catches, logs, returns `status="error"`. The email is silently dropped; the pending signal proposals built earlier in the function are never flushed (they flush AFTER `insert_email` succeeds). ✗

The Phase 1 acceptance test (`tests/test_orchestrator_three_state.py` in eq-email-pipeline) only covered the case-1 scenario (sender = known acme.com; CC = unknown-startup.io). The case where the SENDER itself is the unknown party was never tested or fixed.

### Why the transcript pipeline doesn't have this gap

(User explicitly accepted this limitation 2026-05-17 PM.) Transcripts are user-initiated. The frontend forces the user to pick an anchor account before recording starts; the backend rejects 400 if no account_id. The "no anchor yet" question never reaches the backend. The dam is in the right place for transcripts.

**Emails are categorically different.** They arrive autonomously from gmail/outlook integration. There's no UI moment to ask "which account?" before the email lands. The backend genuinely needs to handle the no-anchor case for emails.

### Candidate fix approaches (full doc at `tasks/downstream/eq-email-pipeline-unknown-sender.md`)

| Approach | Pros | Cons |
|---|---|---|
| **C — separate `pending_interactions` table** ⭐ **recommended** | Architecturally honest; preserves the `raw_interactions.account_id NOT NULL` invariant; symmetric with Phase 2 identity state machine; no misattribution risk | New table → Prisma migration → `/map` needs awareness; workflow gains a "promote pending → raw" step |
| B — allow `account_id NULL` temporarily | Single materialization path; no new table | Violates Phase 1 hard invariant; downstream needs to handle NULL envelopes; existing queries need auditing |
| D — column-level pending state on `raw_interactions` | Minor schema change | Requires a synthetic "pending sentinel" account — same misattribution critique |
| ~~A — recipient-as-anchor for inbound~~ | (rejected) | Misattributes external emails to user's own org; analytics/billing/graph relationships inherit the wrong attribution |

**The right pattern (2026 AI-native posture):** explicit pending state, not a fake anchor. Approach C mirrors what Phase 2 was already going to introduce for contacts (`shell / emerging / partial / resolved / verified`) — an interaction whose account is in-flight gets an explicit state, not a hack.

### Recommended sequence for the next session (PLAN-WRITING ONLY)

1. **Brainstorm with user** — surface Approach C as the recommended; confirm direction. Product/strategic decision; do NOT auto-decide.
2. **Work through open design questions** (below) with the user. Surface the most consequential; don't try to answer all alone.
3. **Codex consult on the chosen approach** (CSO discipline — design-time review BEFORE writing code).
4. **Iterate on the design** per Codex feedback. 4-round soft cap (LOCKED-14).
5. **Write the implementation plan** at `eq-email-pipeline/docs/superpowers/plans/2026-05-XX-pending-interactions.md`.
6. **STOP. /context-save + handoff for the execution session.**

Steps 7-10 (schema migration, orchestrator code, workflow promote step, production E2E) happen in a separate EXECUTION session — NOT this one.

### Open design questions for the plan-writing session

The next agent should NOT try to answer all of these alone. Surface the most consequential to the user for direction; capture the resolved answers in the implementation plan.

**Most consequential (raise with user):**
- **Schema for `pending_interactions`** — mirror `raw_interactions` exactly, or a leaner subset (just `interaction_id` + `tenant_id` + the body fields + a foreign key to the queue entry)?
- **Promote pending → raw mechanics** — new DBOS step in the workflow, or modify the existing materialization step? On promote, is it INSERT + DELETE, or a state transition on the same row?
- **`/map` vs `/approve` behavior** — when the user clicks Map (map to existing account) instead of Approve (create new), does promotion happen in both cases? It probably should.
- **EventBridge emission timing** — emit on promote (so downstream sees a "new interaction" event), OR once promoted to raw_interactions normally? Affects every downstream consumer's Day-1-vs-backfill behavior.
- **TTL / auto-archive on `pending_interactions`** — 30 days? 90 days? Aligned with the queue entry's TTL? Hard delete or archive?

**Less consequential but plan-needed:**
- Dedup semantics: multiple emails from the same unknown sender pile up before approval — all preserved, or first-only, or sliding window?
- Cross-repo migration ordering: schema in eq-frontend, then orchestrator branch in eq-email-pipeline, then promotion step in live-transcription-fastapi. What's the rollback story if any step fails?
- Production E2E acceptance criteria — the test that proves the fix actually works.
- Queue UI integration in eq-frontend `app/(workspace)/agent-queue` — does it need to surface pending_interactions to the user, or just the pending_account_mappings queue entry as today?
- Migration path: are there existing dropped emails to backfill? Probably not (test data only), but worth confirming with user.
- How does this interact with Phase 2's identity state machine for contacts (`shell / emerging / partial / resolved / verified`)? The pending_interaction state is the symmetric construct.

---

## Verified cross-repo state (2026-05-17 PM)

| Item | Status |
|---|---|
| eq-email-pipeline re-open trigger | ✅ DELIVERED. `orchestrator.py:342` calls `reopen_archived_entry`; helper at `pending_account_mappings.py:112`. |
| eq-frontend `/dashboard/organization/email-pipeline` admin route | ✅ EXISTS (the admin prototype from design §29). |
| eq-frontend `app/(workspace)/agent-queue` user-facing UI | ✅ EXISTS — verify current state in the next session if relevant to the plan. |
| eq-agent-action-core `worker_attempt_id` idempotency | ❌ NOT in production OpenAPI — BUT N/A in the DBOS world. DBOS step caching + the agent's `run_id` (via `GET /api/enrich/{run_id}`) provide idempotency. The "agent accepts worker_attempt_id" line was inherited from the pre-rethink polling-worker design. |
| eq-structured-graph-core consumer runtime behavior | ⚠️ Envelope contract verified via M5 `verify_consumer_contracts.py`. Runtime `_merge_contact()` MERGE behavior is a production-canary question (deferred). |

---

## Unfinished Phase 1 + 1.5 — full list

**PRIMARY (this session's plan-writing scope):**

1. **eq-email-pipeline cold-inbound-unknown-sender drop fix** (Phase 1 Task 1.24 incomplete). Approach C recommended. Plan-writing only this session.

**SECONDARY (other unfinished items; NOT this session's scope unless plan-writing finishes early with substantial context remaining):**

2. **Test-discipline-gaps Item 1** — audit + de-mock integration tests that mock `lookup_account_by_domain` at import level. The 2026-05-15 Phase 1 silent regression shipped because these mocks made the import-level patching invisible. M5 added schema-probe tooling to catch this design-time, but the existing tests still mock and the audit was never done.
3. **Test-discipline-gaps Item 2** (partial) — complete per-attendee branching happy paths in the production E2E suite (all four ingestion paths × known/unknown/personal/internal matrix).
4. **Test-discipline-gaps Item 3** — narrow the outer `except Exception:` block in `services/transcript_enrichment.py:399-405` (the swallow that hid the Phase 1 silent regression). Similar broad excepts exist elsewhere.
5. **Production canary** (deferred from M3+M4 + M5). Per-batch destructive-op confirmation required. Would also verify eq-structured-graph-core MERGE behavior runtime.
6. **M3.5 outbox drop** — optional Prisma migration in eq-frontend; cross-repo coordination needed; truly optional cleanup.

**Phase 2 (post-Phase-1.5; explicit stopping point per 2026-05-15 plan):**

- Identity state machine + progressive enrichment for contacts (`shell / emerging / partial / resolved / verified`). The pending_interactions pattern this session designs is the symmetric construct for interactions — design it with Phase 2 in mind.

**Phase 3 (post-Phase-2):**

- Conflict resolution + multi-account history + fuzzy matching.

---

## Secondary scope (do AFTER the email-pipeline fix or if time permits)

### A. Production canary (deferred from M3+M4)

Per-batch destructive-op confirmation required (LOCKED-decision-17). Seed synthetic queue entry under test tenant → `/approve` → poll `dbos.workflow_status` → verify accounts + account_domains + contacts + interaction_contact_links rows + EventBridge emission. Teardown afterwards.

**Before any destructive Neon write, run the Layer-1 collision check:**
```bash
ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
```
Files modified in last hour = pause + ask user.

### B. M3.5 — drop `account_provisioning_outbox`

Cross-repo Prisma migration in eq-frontend. Safe now that materialization no longer writes to outbox. Confirm with `grep -rn account_provisioning_outbox` returning zero hits across all repos before dropping.

---

## Production credentials + IDs (load-bearing reference)

- **Neon Postgres (eq-dev):** project `super-glitter-11265514`, branch `production`, database `neondb`. Direct connection (no `-pooler`) for `DBOS_SYSTEM_DATABASE_URL`.
- **Test tenant:** `11111111-1111-4111-8111-111111111111`. All data under this tenant is test data. Per LOCKED-decision-17, ask the user per-batch before any destructive op.
- **Test user (FK target for `pending_account_mappings.owner_user_id`):** `b0000000-0000-4000-8000-000000000002`.
- **Railway FastAPI service:** project `847cfa5a-b77c-4fb0-95e4-b20e8773c23e`, service `59a69f3d-9a24-4041-942a-891c4a81c5fb`, env `e4c5ec15-1931-4632-9e58-92d9c6be4261`, URL `https://live-transcription-fastapi-production.up.railway.app`.
- **Railway eq-agent-action-core:** URL `https://eq-agent-action-core-production.up.railway.app`, service `3036ea0f-afc9-4bc4-889d-c98617d81e96`.
- **eq-email-pipeline:** local path `/Users/peteroneil/eq-email-pipeline` (NOT under EQ-CORE/). Main branch HEAD at `084567a` as of 2026-05-17 PM.
- **Internal JWT:** HS256, `INTERNAL_JWT_SECRET`, `iss=eq-frontend`, `aud=eq-backend`.
- **AWS:** EventBridge bus `default` (configurable via `EVENTBRIDGE_BUS_NAME`); `AWS_REGION=us-east-1`.
- **Neo4j:** Aura instance `c6171c63`, URI `neo4j+s://c6171c63.databases.neo4j.io`.

---

## LOCKED decisions (do NOT re-litigate)

Carried forward from prior sessions. Full list in earlier checkpoints; the load-bearing ones for the next session:

1. **DBOS** is the substrate (Apache 2.0, library-only, Postgres-native).
2. **Single Railway replica + `executor_id=RAILWAY_REPLICA_ID`** — multi-replica-ready by configuration; orphan-detector deferred to Phase 2 scale work.
3. **EventBridge Path A** with `source="com.yourapp.transcription"` and closed `INTERACTION_TYPE_TO_DETAIL_TYPE` lookup.
4. **Workflow ID = `f"queue-{queue_id}:approval-{approval_attempt_id}"`** — stable across replays of the same approval attempt; reopen produces a distinct workflow ID.
5. **`/approve` reserves synchronously then enqueues** via `SetWorkflowID` + `APPROVAL_QUEUE.enqueue_async`. Phase 1 invariants 25-30 preserved.
6. **Test infrastructure:** Option B (test-tenant scoping in prod Neon) + `@pytest.mark.requires_db_write` opt-in marker + `RUN_DESTRUCTIVE_TESTS=1` env var.
7. **DBOS v2.x sync `launch()`/`destroy()` at FastAPI lifespan** + `get_event_async`/`set_event_async` INSIDE async `@DBOS.step`.
8. **SQLAlchemy 2.0.49 uses `CAST(:name AS uuid)`** form (NOT `:name::uuid` which truncates the bindparam).
9. **Materialization REQUIRES Lane 2 raw_interactions** before materializing. No placeholders.
10. **Codex review BEFORE merging** per LOCKED-14 (4-round soft cap; extendable when real P1s keep surfacing — proven this session through round 6).
11. **PER-BATCH user confirmation** for destructive ops on shared test tenant (LOCKED-17).
12. **NEW — Codex multi-round reviews use `--commit HEAD`** (not `--base main`) once cumulative diff > ~1500 lines to avoid API timeouts. `model_reasoning_effort=medium` is the default; reserve `xhigh` for very small diffs.

---

## M5 deliverables (now in main)

- `scripts/verify_schema.py` — PREPARE-based SQL schema gate
- `scripts/verify_consumer_contracts.py` — AST-based consumer envelope.py validator + live EventBridge rule probe
- `tests/scripts/` — 40 unit tests covering both scripts
- `tasks/lessons.md` — "Review gates for this repo's PRs" lesson
- `services/account_provisioning/eventbridge_emit.py` — inline `content.text` semantics doc
- `tasks/downstream/eq-email-pipeline-unknown-sender.md` — THE next-session document
- `tasks/downstream/test-discipline-gaps-2026-05-15.md` — Items 4+5 marked SHIPPED

---

## Mandatory read order for the next session (~15-20 min)

1. This file
2. `tasks/downstream/eq-email-pipeline-unknown-sender.md` — the load-bearing finding
3. `~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/project_email_pipeline_unknown_sender_drop.md` — auto-memory complement
4. `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` §3 (Hard Rules) + §314 (Option A scope statement)
5. Quick scan: `eq-email-pipeline/src/pipeline/orchestrator.py` lines 174-280 + `eq-email-pipeline/src/persistence/postgres.py` lines 195-225 (the load-bearing code paths)
6. `tasks/lessons.md` — bottom entries, especially the shared-infrastructure-collision protocol + Codex pre-merge gate + the new "Review gates" lesson

---

## STOP CONDITIONS

- The user's clarifying questions during the email-pipeline fix indicate a different scope (e.g., they want to do something else first)
- The chosen fix approach requires a schema migration (Approach B) and you haven't confirmed coordination with eq-frontend
- Codex review on the email-pipeline PR surfaces a P1 you can't fold in one round
- Production canary fails or shows unexpected behavior

The plan is the load-bearing artifact. When in doubt, surface to user.
