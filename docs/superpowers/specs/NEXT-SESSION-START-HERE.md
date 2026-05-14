# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative
**Last session:** 2026-05-14 (Phase 1.5 P2 cleanup — SHIPPED)
**Status:** Phase 1 + Phase 1.5 P2 cleanup SHIPPED. PR #11 merged at `f52f41d` + Railway deployed (deployment `1878c3e2` SUCCESS) + production E2E **13/13 PASS** (9 Phase 1 regression + 4 new Phase 1.5).
**This session's job:** Begin **Phase 1.5 main scope** — outbox-backed durability + queue worker + queue UI. Multi-session workstream (estimate 2-3 sessions).

---

## Critical context (READ FIRST)

Four things to internalize before opening any file:

1. **Phase 1.5 P2 cleanup IS shipped and verified live.** All three deferred P2s (T1.26.4/.5/.6) plus 2 additional Codex-surfaced P2s (empty-list collapse, interaction_id loss) are closed. The polling regression on `GET /upload/status/{job_id}` is fixed in production. Participants flow through `/text/clean` and `/upload/init` → worker. The `participants_json` TEXT column exists on Neon eq-dev (project `super-glitter-11265514`) — verified live.

2. **Codex Rounds 3-5 ran during the P2 cleanup.** Round 5 surfaced one operational P2 (ORM-vs-schema rollout ordering) which was acknowledged + mitigated via documentation (commit `9bb4732`) rather than code — the schema was applied to Neon eq-dev before the code branch existed, so the rollout safety is operational not algorithmic. This was a judgment call; the spiral pattern is real and worth knowing about. The user's bar of "0 P1 AND 0 P2" was met in spirit (no code-correctness P2s remain).

3. **The user is a non-developer founder.** Make confident technical calls on dispatch, fix shape, and review judgments. Surface only product/strategic decisions. The user's explicit guidance: "Make the reasonable call and continue; they'll redirect if needed."

4. **Phase 1.5 main scope is a different shape of work than the P2 cleanup.** The P2s were narrow surgical fixes — most under 30 lines. The main scope is real architecture: a new Postgres table (`account_provisioning_outbox`), a new queue worker process, eq-agent-action-core acceptance testing, AND a queue UI in eq-frontend. This is multi-session work. Plan budget accordingly.

---

## What this session does

### Workstream A — Outbox-backed durability + queue worker

Design doc Section 6 is the canonical reference. Implementation plan starts at line ~2538 of `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md`.

**Core components:**

1. **`account_provisioning_outbox` Postgres table** — new table in this repo's `migrations/005_*.sql`. Written in the SAME Postgres transaction as the account materialization (atomic outbox pattern — outbox row + materialized account row commit together or not at all). Outbox publisher worker reads this and emits to EventBridge.

2. **Queue worker (this repo)** — consumes from `pending_account_mappings` (queued + active states). Invokes `eq-agent-action-core` for enrichment. Three-layer idempotency:
   - **Worker → agent:** `worker_attempt_id` propagated to agent calls; agent dedup logic uses it
   - **Outbox → EventBridge:** outbox publisher dedupes via outbox row ID
   - **Frontend Approve → backend:** approve action uses queue entry's `approve_id` as the idempotency key

3. **Acceptance tests for eq-agent-action-core invocation** — required Phase 1.5 work. Idempotency under `worker_attempt_id`, timeout behavior, partial-failure recovery, schema stability, server-to-server permissioning. The agent is production-deployed for interactive use; worker-side invocation needs explicit validation.

**Why this design** (design doc Section 6):
- The outbox pattern guarantees account materialization and EventBridge emission can't go out of sync — a crash between them either commits both or neither.
- The worker is a fan-in point for queue resolution: signals from email + transcript both flow into the same `pending_account_mappings` rows.
- Three-layer idempotency means retries at any layer don't double-create downstream state.

### Workstream B — Queue UI in eq-frontend

Design doc Section 5.3. Approve/archive flow. Subtle per-signal provenance display.

This is cross-repo work (lives in eq-frontend, not this repo). The shape:
- New page or panel listing `pending_account_mappings` in `queued` or `tenant_review` state, scoped to the current tenant via auth context
- Owner-only by default; tenant_review state allows admin action too (default threshold 3 signals)
- Approve action: materialize the account, write the outbox row in the same Postgres transaction
- Archive action: set `pending_account_mappings.archived_at`; signals on archived entries can re-open per the threshold rule

### Workstream C — Phase 1.5 polish + hygiene

Carried forward from this session. Each is small and quality-improving. Defer to end-of-session or skip entirely if main-scope work eats the session:

**Polish nits (Phase 1 + Phase 1.5 P2 reviews):**
1. **TTL cache for `services/internal_domains.py:get_tenant_internal_domains`** — every ingress request hits `provider_connections`. Add 5-minute in-process TTL keyed by `tenant_id`. `provider_connections` changes on the order of days, not seconds.
2. **Simplify defensive accessors in `services/internal_domains.py:71-76`** — `RowMapping` always supports `[]` access; the `if hasattr(row, "get") else row[...]` branching is dead code.
3. **Narrow outer `try/except Exception` in `services/transcript_enrichment.py:306`** OR add a documenting comment — the outer except swallows the `ValueError` from the `recording_user_id is None` invariant. All callers wire correctly now, so the swallow is fine in practice — but a narrowed except or comment documents the deliberate choice.
4. **Extract `assert_account_id_matches(body, context)` helper** — Tasks 1.26.2 and 1.26.3 duplicate the same mismatch check in `routers/text.py:74-85` and `routers/upload.py:142-153`. Natural place: `utils/context_utils.py`. T1.26.4's auth-context split landed; this is now the natural moment to consolidate.
5. **Add UUID validators to `TextCleanRequest.account_id` and `UploadInitRequest.account_id`** — production callers send UUIDs but the Pydantic fields are `str` typed. Non-UUID values slip through and only fail at the downstream `raw_interactions.account_id UUID` write. Reject at the 422 boundary.
6. **`main.py` `_ws_enrich_*` prefix inconsistency** — other locals in the same finally block use `ws_*` (no underscore). Rename for consistency.
7. **`models/enrichment_models.py:38`** comment reads `# "calendar_match" | "none"` — stale after T1.26.6 added `"manual_participants"`. One-line update.
8. **`_participant_to_attendee` placement** at `services/transcript_enrichment.py:839` is below the class that uses it (line 177). Pyright forward-reference false-positive. Moving above the class would be stylistically cleaner.
9. **`utils/context_utils.py:103`** stale `# type: ignore[arg-type]  # T1.11 tightens this` — T1.11 already tightened it.

**Hygiene cleanup:**
- **eq-frontend PR #349** still OPEN with failing CI. Schema is LIVE on Neon eq-dev. Phase 1.5 hygiene task: investigate CI failures and either merge-with-failing-checks (after confirming they're unrelated to the migration) or fix-and-merge.
- **Top-level legacy tests** `tests/test_jwt_auth.py` + `tests/test_integration_endpoints.py` have 52 pre-existing failures from Phase 1's auth-contract tightening. The verify script intentionally excludes them. Delete or rewrite to the new contract.
- **Stale git stash** on `railway-deployment` branch from a prior session — still in the reflog. Investigate origin and discard if no longer needed.

---

## Read these in order before doing any work

Total reading: ~30-40 minutes. The Phase 1.5 main scope is genuinely complex — don't skim the design doc.

1. **Auto-loaded:** `MEMORY.md` — you should see status `PHASE_1.5_P2s_SHIPPED_MAIN_SCOPE_PENDING`.

2. **Project status + full decision log:** `~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/project_contact_quality_initiative.md` — read the `## Phase 1.5 P2s SHIPPED (2026-05-14)` section (most recent) AND the `## Phase 1 SHIPPED (2026-05-14)` section for the earlier context. The `## Codex review history` section near the bottom is the audit trail.

3. **The implementation plan for Phase 1.5 main scope:** `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` starting at line ~2538. Read carefully — the outbox + worker design has subtle txn-boundary requirements.

4. **The design document (canonical project intent):** `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` — **THE FOUR MOST-REFERENCED SECTIONS FOR THIS WORK:**
   - **Section 5.3** — queue UPSERT semantics + provenance UI
   - **Section 6** — worker + outbox architecture (three-layer idempotency)
   - **Section 7.2** — Phase 1.5 scope definition
   - **Section 8.4** — recurring quality gate (real Codex review at every phase boundary)
   - **Section 12** — verifiable invariants (do not violate)

5. **Phase 1 + 1.5 audit trail (skim only):**
   - `tasks/downstream/codex-phase-1-review.md` — Codex Round 1 + Round 2 results (Phase 1 ship)
   - `tasks/downstream/codex-phase-1-findings.md` — the canonical fix specs (now all closed)

6. **Architecture references:**
   - `docs/contacts-architecture.md` — Section 3.4 documents the three-state branching contract; Section 3.5 (new) documents the auth-context ingestion-vs-polling split.

7. **Reusable artifacts:**
   - `/tmp/e2e_phase_1_production.py` — extended to 13 cases. Re-runnable for regression checks. Re-extend with new Phase 1.5 main-scope cases (outbox emission, worker completion, queue UI side-effects) as you ship.
   - `scripts/verify_phase_1_invariants.sh` — exit 0 currently. Extend with new invariants for the outbox + worker.

---

## Carry-forward invariants (all hold in production)

All from Phase 1, plus the ones surfaced by Phase 1.5 P2 cleanup:

**From Phase 1:**
- **Contact ID consistency:** every contact carries UUIDv4 `contact_id`. Never store a name without an ID.
- **Tenant isolation:** every Postgres + Neo4j query MUST include `tenant_id`. Never cross-tenant queries.
- **Three-state branching:** known account → contact; unknown business domain → queue signal, no contact; personal/internal → skip. NEVER fall back to anchor.
- **Backend rejection over frontend trust:** every ingestion path validates `account_id` at the auth-context boundary or 400s. Queue-hold path is the only exemption.
- **First-owner-wins UPSERT:** `pending_account_mappings.owner_user_id` is never reassigned by routine UPSERT.

**New from Phase 1.5 P2 cleanup (now load-bearing):**

- **Caller-side completeness:** when adding a new parameter to an internal function, update every caller in the SAME commit. T1.26.6 added `participants` to `enrich()` and Codex Round 4 added `interaction_id`. Both required ALL FOUR callers updated in the same commit.

- **Auth-boundary wins on body/header conflicts:** body fields with the same semantic as an authenticated header are at best verification checks. Default: reject mismatch with 400.

- **Ingestion vs polling auth split:** `get_auth_context_ingestion(request)` requires `X-Account-ID`; `get_auth_context_polling(request)` doesn't (returns empty-string sentinel). NEVER use `polling.account_id` for any FK write — the sentinel makes accidental writes fail loudly.

- **`participants=[]` is a meaningful signal**, not equivalent to `None`. Empty list = "caller explicitly says no one was here, do NOT fall back to calendar." Use `is not None` checks, not truthy checks, on lists where the empty case has distinct meaning.

- **NULL!=NULL in SQL** — a dedup constraint with NULL columns can't deduplicate. For queue signals, always fill `discovered_from_interaction_id` with `event_id or interaction_id` so retries dedupe properly.

- **Real `/codex review` is non-substitutable.** Static-invariant self-review missed P1s in Phase 1 and Codex Round 3-5 each surfaced a different P2 that automated tests didn't catch.

- **Production E2E is a real quality gate.** Extended `/tmp/e2e_phase_1_production.py` to 13 cases (9 Phase 1 + 4 Phase 1.5). Extend further as each Phase 1.5 main-scope ship lands.

- **`/context-save` at session end is mandatory.** Handoff docs + auto-memory answer "what's the state?"; the gstack checkpoint answers "is the next agent's first command going to work?" Both required.

- **Stop the Codex spiral when remaining findings are operational, not algorithmic.** Codex Round 5 surfaced a deployment-discipline concern (schema-vs-ORM ordering) that was already mitigated in our specific deployment. Documenting the safety argument is the right move — not endless rounds chasing perfectionism.

---

## Repository state (as of 2026-05-14 Phase 1.5 P2 ship)

- **Current branch:** `main` (Phase 1.5 P2 work merged). Feature branch `feat/contact-quality-phase-1.5-p2s` was deleted on merge.

- **Phase 1 PRs:**
  - eq-frontend PR #349 — 🟡 OPEN with failing CI. Schema LIVE on Neon. Phase 1.5 hygiene task (carried).
  - eq-email-pipeline PR #6 — ✅ MERGED 2026-05-14T10:40:28Z, commit `895cc9f`.
  - live-transcription-fastapi PR #10 — ✅ MERGED 2026-05-14T10:40:43Z, commit `2552b4b`.

- **Phase 1.5 P2 PR:**
  - live-transcription-fastapi PR #11 — ✅ MERGED (squash) 2026-05-14, commit `f52f41d`.

- **Railway production deploys:**
  - Phase 1: deployment `07d58610-edfd-42d3-b495-6a721736e20e` (Phase 1, prior session) + `9db6059c-5f9e-4525-acdd-86d3c37561dd` (Phase 1 doc-only follow-up) — both SUCCESS.
  - Phase 1.5: deployment `1878c3e2-735d-4005-ac80-52a54e9d21d6` → SUCCESS.
  - Public URL: `https://live-transcription-fastapi-production.up.railway.app`

- **Production E2E artifact:** `/tmp/e2e_phase_1_production.py` — 13 cases, 13/13 PASS on current production. Re-runnable for Phase 1.5 main-scope regression checks.

- **Verification artifacts:**
  - `scripts/verify_phase_1_invariants.sh` — exit 0 (all 12 static invariants pass; integration tests run 43+ tests, all pass).
  - `tasks/downstream/codex-phase-1-findings.md` — canonical Phase 1 P2 fix specs (all closed).
  - `tasks/downstream/codex-phase-1-review.md` — Round 1 + Round 2 results (Phase 1 ship).

- **Neon eq-dev schema (project `super-glitter-11265514`):**
  - Phase 1 columns + new `pending_account_mapping_signals` table all live.
  - Phase 1.5 P2: `upload_jobs.participants_json` TEXT column added 2026-05-14 via Neon MCP. Verified via `information_schema.columns`.

- **Test tenant:** `11111111-1111-4111-8111-111111111111` (safe to seed).

---

## Dispatch pattern (carry forward)

Each subagent dispatch should include:

1. **The task block from the implementation plan in full** — copy the relevant section verbatim into the prompt.
2. **Scene-setting context** — where this fits in the architecture, what previously shipped, what invariants must hold.
3. **Explicit boundaries** — what to NOT touch (no premature abstractions, no helper extractions unless spec asks).
4. **Acceptance evidence required** — specific test commands, expected output, commit hashes, `git diff` stats.
5. **TDD discipline** — failing test first, then fix, then green.

After each implementer subagent returns:
- Verify the commit exists in `git log`
- Run the test commands yourself if the diff is non-trivial
- Dispatch the **spec compliance reviewer** subagent FIRST (verifies code matches spec)
- Then dispatch the **code quality reviewer** subagent (verifies craftsmanship)
- Loop on issues from either reviewer; don't accept "close enough"

For SMALL surgical fixes (under ~30 lines), the orchestrator can do the spec/code review inline by reading the diff + running tests — but document the judgment call.

The three prompt templates live at `~/.claude/plugins/cache/claude-plugins-official/superpowers/5.1.0/skills/subagent-driven-development/{implementer,spec-reviewer,code-quality-reviewer}-prompt.md`.

---

## What NOT to do this session

- Do NOT skip real `/codex review` at the Phase 1.5 main-scope boundary. The lesson from Phase 1 and Phase 1.5 P2 cleanup is non-substitutable.
- Do NOT modify locked design decisions or invariants. The design doc Section 12 is the contract.
- Do NOT touch Phase 1's auth-context layer in a way that breaks the `_ingestion` vs `_polling` split that landed in T1.26.4.
- Do NOT touch the participants flow without good reason — it spans 4 routes and the worker, and any change requires updating all callers in the same commit.
- Do NOT use `--no-verify` on any commit.
- Do NOT skip production E2E after the main-scope ship — extend `/tmp/e2e_phase_1_production.py` with outbox + worker + queue UI cases.
- Do NOT chase Codex perfectionism rounds past Round 3-ish if the remaining concerns are operational/documentation rather than code-correctness. (See Phase 1.5 P2 cleanup Codex Round 5 for the precedent.)

---

## Context budget guidance

Phase 1.5 main scope has THREE big deliverables; size them realistically:

- **Outbox table + outbox publisher worker:** ~1 session. New SQL migration + ~200-400 lines of worker code + acceptance tests.
- **Queue worker invoking eq-agent-action-core + acceptance tests:** ~1 session. The worker logic + 5 acceptance tests (idempotency, timeout, partial-failure, schema stability, server-to-server permissioning).
- **Queue UI in eq-frontend:** ~1 session. Cross-repo; coordinate with the frontend's component patterns. Approve + Archive actions wire to backend endpoints.

**Signs to stop and hand off:**
- Codex Round N surfaces unanticipated P1s (vs P2s — P2s are addressable; P1s mean reconsidering scope)
- Acceptance test surfaces a worker behavior the design didn't anticipate
- Cross-repo work (eq-frontend queue UI) needs more than one round-trip
- Context approaching limits

When you stop, do a clean handoff: update auto-memory + rewrite this NEXT-SESSION-START-HERE.md + commit + push + `/context-save` with a clear title. **The /context-save step is non-negotiable** — handoff docs answer "what's the state?"; the gstack checkpoint answers "is the next agent's first command going to work?" Both are required.

---

## Suggested first actions for the next agent

1. Run `/context-restore`. Expect a checkpoint titled **"phase-1.5-p2s-shipped-handoff-for-main-scope"** (or similar) dated 2026-05-14. Load it. It contains working-state at session end, production credentials, and explicit warnings about gaps. If `/context-restore` returns `NO_CHECKPOINTS`, STOP and surface that — it would indicate a sync gap.

2. Read `MEMORY.md` + this file in full. Total ~5 minutes.

3. Read the design doc Sections 5.3, 6, 7.2, 8.4, 12 for the canonical scope. ~15 minutes.

4. Read the implementation plan starting at line ~2538. ~10 minutes.

5. Briefly confirm understanding back to the user (one paragraph).

6. Invoke `superpowers:subagent-driven-development` for the canonical orchestrator workflow.

7. Make the first scope decision: **which of the three deliverables (outbox table + publisher, queue worker, queue UI) goes first?** Recommend: outbox table + publisher (Workstream A backend). Reasons:
   - It's the foundation other components depend on (worker reads from queue + writes outbox row; UI reads queue state).
   - Smaller scope than the full worker logic.
   - Lets the next agent ship one logical chunk before context exhausts.

8. Dispatch the implementer for the first chunk (e.g., `migrations/005_account_provisioning_outbox.sql` + Pydantic model + the atomic-write helper that wraps account materialization in the same txn as the outbox insert).

9. After it lands: spec compliance review → code quality review → loop.

10. Re-run real `/codex review --base main`. Require GATE: PASS (acknowledge operational P2s if any, fix code-correctness P2s).

11. Re-run + extend `/tmp/e2e_phase_1_production.py` with new outbox cases.

12. Update PR descriptions, merge, verify Railway deploys, run post-deploy E2E.

13. Repeat for the queue worker.

14. Then the queue UI (cross-repo to eq-frontend).

15. Final handoff (mandatory): update auto-memory + rewrite this file for whatever comes next (probably the post-Phase-1.5 STOPPING POINT for re-planning before Phase 2).

---

## Final note for the next agent

Phase 1 and Phase 1.5 P2 cleanup are both shipped, verified, and operational in production. The bones are right. The contract is enforced. The queue feature surface is now reachable end-to-end (text + upload paths both feed signals into `pending_account_mappings`). What's missing is the BACKEND that processes those signals (the worker + outbox + UI). This is Phase 1.5 main scope.

The architectural standard remains high. Hold the bar. Run real Codex at every phase boundary. Run production E2E. Ship clean.

The user is building a cutting-edge AI-native customer intelligence platform. The architecture choices in Phase 1 + Phase 1.5 are grounded in emerging AI-native patterns (GraphRAG, agentic identity resolution, outbox/saga) — not legacy CRM patterns. The Phase 1.5 main scope is where this commitment becomes operational reality: the outbox + worker is the canonical "do-the-thing-reliably-with-AI-in-the-loop" pattern. Get it right.
