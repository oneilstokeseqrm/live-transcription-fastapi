# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — a multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-17 PM (M5 verified-contract tooling shipped as PR #18).
**Status:** ✅ **PHASE_1.5_M5_SHIPPED_EMAIL_PIPELINE_GAP_NEXT** — Phase 1.5 main code complete (M0–M5 all shipped). New HIGH-priority finding surfaced + documented: `eq-email-pipeline` silently drops cold inbound emails from unknown business senders. This is the next session's primary scope.

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

## THE NEXT SESSION'S PRIMARY SCOPE: eq-email-pipeline unknown-sender drop

**Discovered:** 2026-05-17 PM during M5 design discussion when the user pushed back on the empty-`content.text` decision and asked to verify the email pipeline's actual behavior.

**The gap (full doc at `tasks/downstream/eq-email-pipeline-unknown-sender.md`):**

`eq-email-pipeline/src/pipeline/orchestrator.py:174-196` resolves the email anchor from the TARGET domain (sender for inbound; first recipient for outbound). If the target domain is unknown, `account_id=None`. Then `eq-email-pipeline/src/persistence/postgres.py:200-204` raises `ValueError("requires a resolved account_id; raw_interactions.account_id is NOT NULL")`. The outer `except Exception` in the orchestrator (line 546-549) catches, logs, returns `status="error"`. **Email is NOT saved, NOT processed, NOT queued.** Pending signal proposals are discarded because they flush AFTER `insert_email` succeeds.

**Concrete asymmetry:**
- Email from `alice@known.com` + cc `bob@unknown.com` → email saved + processed; Bob → queue signal. ✓
- Email from `bob@unknown.com` to internal user → email DROPPED entirely. Bob NEVER queued. ✗

This means Phase 1.5's queue mechanism captures unknown-business signals only for SECONDARY participants on emails where the SENDER is known. **Cold inbound from unknown business domains — the actual common business scenario — falls on the floor.**

**Why it wasn't caught in Phase 1:**
The plan §2 explicitly puts ingestion-path changes out of scope for Phase 1.5. The Phase 1 work that shipped 2026-05-14 added three-state branching for SECONDARY participants but did NOT change the anchor-lookup behavior. The "what do we do when the sender is the unknown party" question is unaddressed in both the design doc and the implementation. Phase 1 design doc §314 says "the interaction is recorded with its anchor account" but doesn't explicitly answer what happens when NO anchor is resolvable.

### 4 candidate fix approaches (documented at `tasks/downstream/eq-email-pipeline-unknown-sender.md`)

| Approach | Pros | Cons |
|---|---|---|
| A — Recipient-as-anchor for inbound | Email saved + processed; sender becomes signal; downstream gets Day-1 emission with full content | Anchor semantics shift — interaction "anchored to YOU" until approval; backfill must handle anchor-change |
| B — Allow `account_id NULL` temporarily | Cleanest semantics ("pending anchor") | Touches Phase-1-shipped hard invariant; schema migration; downstream needs to handle NULL |
| C — Headless `pending_interactions` table | Keeps `raw_interactions` clean | New table + dedup logic + extra hops |
| D — Hybrid (A for inbound, current for outbound) | Matches user intent (outbound to unknown = user knows the recipient) | Direction-conditional anchor logic |

**Recommended sequence for the next session:**
1. Brainstorm fix approach with user (this is a product/strategic decision)
2. Codex consult on the chosen approach (CSO discipline — design-time review)
3. Write implementation plan in `eq-email-pipeline/docs/superpowers/plans/`
4. Schema migration in `eq-frontend` if Approach B is chosen
5. Production E2E that asserts a cold-inbound-from-unknown email gets queued, then approved → contact materialized → backfill envelope fires

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
