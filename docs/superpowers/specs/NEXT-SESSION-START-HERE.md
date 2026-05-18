# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-18 (M5.4 shipped + deployed + verified end-to-end; **Phase-1-email-pipeline INITIATIVE COMPLETE**; Phase 2 PLANNING unblocked.)
**Status:** ✅ **PHASE_1_EMAIL_PIPELINE_INITIATIVE_COMPLETE** — All 8 milestones shipped. §10.3 PASS on fresh UUID. §11 18/22 invariants PASS (1 soft, 3 out-of-scope). Multi-writer Neo4j coexistence verified. Test tenant atomically cleaned. Production state stable.

**Next session scope:** Phase 2 BRAINSTORMING — explore what to build next, in what order, against what success criteria. NOT milestone execution. NOT new feature code. Brainstorming → CEO review → design doc → eng review → THEN execution sessions.

---

## SESSION SCOPE FOR THE NEXT SESSION

This is a **transition session.** Phase 1 closed; Phase 2 hasn't been scoped. The next session's job:

| Item | Scope | Description |
|---|---|---|
| 1 | **Re-orient** | Walk through Phase 1 milestone-by-milestone with the user. What surprised them? What felt easy vs. hard? Establish shared mental model before scoping Phase 2. |
| 2 | **Phase 2 candidate scope** | The plan documents 7+ candidate Phase 2 features (Neo4j MERGE-everywhere, contact identity state machine, outbound, queue UI, audit log table, Outlook NULL-IMID dedup, `ensure_constraints` hardening). Discuss priority + sequencing with user. |
| 3 | **YC office-hours brainstorm** | Invoke `/office-hours` skill to apply the 6 forcing questions: demand reality, status quo, desperate specificity, narrowest wedge, observation, future-fit. Build product narrative. |
| 4 | **CEO review** | Invoke `/plan-ceo-review` with the brainstorm output. Decide whether to expand scope (10x mode) or hold scope (rigor mode). |
| 5 | **Design doc draft** | Once direction is locked, write Phase 2 design doc to `docs/superpowers/specs/2026-05-XX-phase-2-design.md`. NOT yet an implementation plan. |
| 6 | **Eng + design review** | `/plan-eng-review` for architecture; `/plan-design-review` if UI/UX is in scope. |
| 7 | **Lock decisions** | Decisions land as LOCKED-23 onwards, building on the 22 Phase 1 LOCKED decisions. |

**Out of scope for the next session:** writing implementation plans, coding any Phase 2 features, deploying anything. The plan-locked execution gate is held until brainstorming → design → review is done.

Estimated work: **1-3 hours** for re-orient + brainstorm + CEO review. Design doc + plan-eng-review may run into a follow-on session.

---

## What's done (Phase 1 complete)

| Milestone | Repo | PR | Merge SHA | What it shipped |
|---|---|---|---|---|
| M1 | eq-frontend | #392 | `de586bbc` | Prisma migration: pending_interactions table + 3 emails cols + composite UNIQUE |
| M2 | live-transcription-fastapi | #19 | `756575d7` | Workflow promote step + EmailPromoted EventBridge emit |
| M3 | eq-email-pipeline | #9 | `85c0295` | EmailPromoted SQS subscriber + 2-layer idempotency guard + 21 LOCKED decisions |
| M4 | eq-email-pipeline | #10 | `6fa181a` | Orchestrator pending_interactions branch + atomic upsert_thread (FLIPPED THE SWITCH) |
| M5.1 | eq-email-pipeline | #11 | `79862b6` | ON CONFLICT column-list fix (post-M4 deploy blocker) |
| M5.2 | eq-frontend, live-tx-fastapi, eq-email-pipeline | #398, #20, #12, #13 | `c3bc162`, `929472e`, `ceea064`, `8b2c67a` | httpx timeout + NULLS NOT DISTINCT + INGEST_SUCCESS_STATUSES |
| M5.3 | live-transcription-fastapi | #21 | `aa0fd23` | Agent /api/enrich v2-envelope parser adapter |
| M5.4 | eq-email-pipeline | #14 | `4693de3` | Neo4j Interaction MERGE-key alignment to (tenant_id, interaction_id) |

**Production verification (this session's E2E walk):**
- Fresh UUID `1a163ab75df` against test tenant `11111111-1111-4111-8111-111111111111`
- §10.3 Steps 1-12 all PASS
- §11 22-invariant walk: 18 PASS, 1 soft, 3 out-of-scope
- Multi-writer Neo4j coexistence proven on the Interaction node
- DLQ drained (the M5.3 leftover); 0 DLQ depth post-E2E
- Test tenant atomically cleaned (LOCKED-11)

---

## What's deferred to Phase 2 (the candidate backlog)

Pulled from plan §17.11 + V1 limitations + Codex challenge deferrals + user's own roadmap signals:

| # | Item | Source | Notes |
|---|---|---|---|
| 1 | **Neo4j MERGE-everywhere refactor** | V1 #2 + Codex challenge #3 | Convert Chunk CREATEs to MERGE on `(tenant_id, interaction_id, chunk_index)`; switch Thread.message_count to an edge-count query. Closes V1 #2. |
| 2 | **Contact identity state machine** | Plan §1.2 Phase 2 trajectory | The contact-side analog of pending_interactions. Pending → confirmed → merged. |
| 3 | **Outbound pending path** | M4 §4.1 direction guard (Codex R1 P1) | Currently outbound preserves pre-M4 silent-drop fallthrough. Phase 2 enhancement to handle outbound-to-unknown-business. |
| 4 | **Queue UI (user-facing approve/ignore/map)** | Long-standing roadmap | The screen where the user actually triages the pending queue. Currently /approve is API-only. |
| 5 | **Audit log table** | V1 #1 roadmap | Personal/internal anchor cold-inbound is currently log+drop. V2 = audit log so we can re-process if user wants. |
| 6 | **Outlook NULL-IMID dedup** | Codex consult caveat 3 | Postgres-side duplicate risk for IMID-less Outlook ingests. Phase 2 deduplication strategy. |
| 7 | **`ensure_constraints` hardening** | Codex challenge #2 | Currently swallows all DDL errors silently. Phase 2 hardening for observable schema-state errors. |
| 8 | **Shared MERGE-key contract document** | Plan §17.11 | A platform deliverable that documents the Neo4j Interaction MERGE-key convention (LOCKED-22) so future Neo4j writers don't drift. |
| 9 | **Cross-queue link fill-in algorithm** | Plan §5.2 / §11 invariant #5 | When the same prospect is in two queues, link-summary join after both are approved. |
| 10 | **Re-open after Ignore + new signal lifecycle** | Plan §11 invariant #8 | The reopen path that un-archives both queue and pending interactions. |
| 11 | **20 orphan Interaction nodes hygiene** | Plan §17.7 | Cleanup of test-tenant leftovers from prior E2E runs. Low priority; hygiene only. |

These are CANDIDATES — the brainstorming session will surface priorities + sequencing + any not-yet-listed items.

---

## LOCKED decisions (22, post-Phase 1)

The full LOCKED list lives in plan `2026-05-17-pending-interactions-cold-inbound-fix.md` §13 + §17.10. Quick reference for the most-load-bearing ones the next session will need:

- **LOCKED-7:** `raw_interactions.account_id NOT NULL` invariant (the core architectural constraint Phase 1 protected)
- **LOCKED-10:** Per-action merge authorization required (push, merge auth ask each time)
- **LOCKED-11:** Test-tenant destructive ops require user authorization first
- **LOCKED-15-16:** Lean+typed pending_interactions payload + EventBridge EmailPromoted event coordination pattern
- **LOCKED-17:** Shared-tenant collision protocol (check concurrent agents before destructive writes)
- **LOCKED-18-21:** SQS-from-EventBridge subscription + DB CAS TTL > SQS VT + HandlerOutcome tri-state + verify_*.py tooling
- **LOCKED-22 (new):** Neo4j Interaction MERGE-key convention — `(tenant_id, interaction_id)` with defensive COALESCE pattern. M5.4 brought eq-email-pipeline into compliance.

---

## User posture (carries forward)

Non-developer founder. Make confident technical decisions; surface only product/strategic decisions. Strict OSS only.

User's rules (load-bearing):
1. Complete Phase N before Phase N+1 planning. **Phase 1 is now complete — Phase 2 planning is unblocked.**
2. Cutting-edge-startup approach. No shortcuts unless the shortcut IS the correct architecture.
3. AI agent doesn't push or merge without per-action authorization. (Doesn't apply this session — brainstorming has no destructive actions.)
4. Plain-English explanations when user asks "why" / "what happened".
5. Investigate thoroughly; use the right gstack skills (`/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/codex consult`).
6. Don't go beyond scope. **For brainstorming session: don't try to lock decisions before user has reviewed brainstorm output.**

---

## STOP conditions (hard — surface to user)

- /context-restore returns NO_CHECKPOINTS or wrong title.
- MEMORY.md status isn't `PHASE_1_EMAIL_PIPELINE_INITIATIVE_COMPLETE`.
- Production /api/health returns non-200 anywhere (regression of Phase 1 deploy).
- Test tenant has leftover artifacts from this session's M5.4 E2E (verify clean baseline before any Phase 2 brainstorm sample-data work).
- User asks to write implementation code before brainstorm + design doc + review gauntlet is done.

---

## Reference artifacts

- **The Phase 1 plan (closed):** `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md`. §17 is the M5.4 design addendum.
- **The original design doc:** `/Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md`. §9 phased trajectory has Phase 2 hooks.
- **Phase 1.5 DBOS plan:** `/Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md`. Foundation for what Phase 2 builds on.
- **M5.4 bug evidence (closed bug):** `/Users/peteroneil/EQ-CORE/live-transcription-fastapi/tasks/m5.4-bug-evidence.md`. Useful reference for understanding cross-service Neo4j writer coordination.
- **Lessons:** `/Users/peteroneil/EQ-CORE/live-transcription-fastapi/tasks/lessons.md`. 9+ load-bearing lessons from Phase 1.
- **Railway project IDs:** `~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/reference_railway_project_ids.md`.
- **Feedback memories:** the project's memory directory has 2 critical ones — `feedback_test_pattern_no_docker.md` and `feedback_complete_all_handoff_reads_before_action.md`.

---

## Tools available for Phase 2 brainstorming

- **`/office-hours`** — YC office-hours-style brainstorm with 6 forcing questions. Use FIRST.
- **`/plan-ceo-review`** — CEO/founder-mode plan review for scope expansion vs holding. Use after `/office-hours`.
- **`/plan-eng-review`** — Architecture review once direction is locked.
- **`/plan-design-review`** — Designer's-eye plan review if Phase 2 has UI scope.
- **`/codex consult`** — For surfacing constraints + risks BEFORE writing the implementation plan.

NOT applicable for brainstorming: `/qa`, `/ship`, `/review` — those are execution-phase tools.
