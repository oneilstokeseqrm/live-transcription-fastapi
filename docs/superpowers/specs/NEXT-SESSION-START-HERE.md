# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative
**Last session:** 2026-05-14 (Phase 1 Codex-fix session — SHIPPED)
**Status:** Phase 1 SHIPPED. PR #6 + PR #10 merged + Railway deployed + production E2E 9/9 PASS. Three P2s deferred to Phase 1.5 (canonical fix specs already exist).
**This session's job:** Execute Phase 1.5 — start with the three deferred P2s, then move into the worker / outbox / queue UI scope.

---

## Critical context (READ FIRST)

Three things to internalize before opening any file:

1. **Phase 1 IS shipped and verified live.** Both PRs are merged (`895cc9f` for eq-email-pipeline, `2552b4b` for live-transcription-fastapi). Railway auto-deploys are SUCCESS. Production E2E verified the three P1 fixes work against the live endpoint with a real JWT. You are NOT starting from a broken state — you are extending a working one.

2. **Codex Round 2 returned the three P2s by name** — same findings as Round 1, no new P1s. The P2s have explicit fix specs already written (`tasks/downstream/codex-phase-1-findings.md` Tasks 1.26.4 / 1.26.5 / 1.26.6). The execution plan is locked. Don't re-litigate.

3. **The user is a non-developer founder.** Make confident technical calls on dispatch, fix shape, and review judgments. Surface only product/strategic decisions. The user explicitly said: "Make the reasonable call and continue; they'll redirect if needed."

---

## What this session does

Phase 1.5 spans two distinct workstreams. Both are documented; do them in this order:

### Workstream A — Close the three Phase 1 P2s (Tasks 1.26.4 / .5 / .6)

These are the immediate carry-forward from Phase 1. They have ready-to-execute specs in `tasks/downstream/codex-phase-1-findings.md`. The session orchestrator should execute via `superpowers:subagent-driven-development`.

**Recommended order:**

1. **Task 1.26.4 — `X-Account-ID` optional for non-ingestion routes.** Highest-priority P2 because it's an active regression: `GET /upload/status/{job_id}` returns 400 for any client polling with just a JWT (no `X-Account-ID` header). Fix shape: split `get_auth_context_ingestion()` (requires account_id) from `get_auth_context_polling()` (doesn't), OR add `require_account_id: bool = True` parameter. The two-helper split is cleaner.

2. **Task 1.26.6 — `/text/clean` honor `body.participants`.** Smaller change, single repo. Pass `participants` into `enrich()`. Decide caller-wins-vs-merge semantics when BOTH a calendar match AND `body.participants` exist (recommend caller-wins for explicit manual-notes use cases).

3. **Task 1.26.5 — Persist `/upload/init` participants through the worker.** **CROSS-REPO** dependency — requires an eq-frontend Prisma migration to add `UploadJob.participants_json` column. Dispatch the cross-repo agent first (same pattern as the prior session's T1.2 migration). Don't start the live-transcription-fastapi side until the schema column is live on Neon.

**After all three land:** re-run real `/codex review --base main`. Expected outcome: GATE: PASS, zero P1 AND zero P2 findings. If new findings surface, address them before continuing.

### Workstream B — Phase 1.5 main scope (worker, outbox, queue UI)

Implementation plan lives at `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` starting at line ~2538. Highlights:

- **Outbox-backed durability:** new `account_provisioning_outbox` table written in the same Postgres transaction as account materialization. Outbox publisher emits to EventBridge. Three-layer idempotency (worker→agent, outbox→EventBridge, frontend Approve action).
- **Queue worker:** consumes from `pending_account_mappings` (queued + active state), invokes `eq-agent-action-core` for enrichment with acceptance tests (idempotency under `worker_attempt_id`, timeout, partial-failure, schema stability, server-to-server permissioning).
- **Queue UI (eq-frontend):** approve/archive flow for the queue. Subtle provenance display (per-signal evidence).

This is a multi-session workstream. Plan to ship Phase 1.5 in 2-3 sessions.

### Workstream C — Phase 1 polish nits (defer to end of session, or skip entirely)

Captured in this session's review cycle. Each is small and quality-improving:

1. **TTL cache for `services/internal_domains.py:get_tenant_internal_domains`** — currently hits `provider_connections` every request. Add a 5-minute in-process TTL keyed by `tenant_id`. `provider_connections` changes on the order of days, not seconds.
2. **Simplify defensive accessors in `services/internal_domains.py:71-76`** — `RowMapping` always supports `[]` access; the `if hasattr(row, "get") else row[...]` branching is dead code. Direct `row["..."]` is cleaner.
3. **Narrow outer `try/except` in `services/transcript_enrichment.py:306`** OR add a comment — the outer `except Exception:` swallows the `ValueError` raised at line 230-235 (the new `recording_user_id is None` invariant). All callers now wire correctly, so the swallow is fine in practice — but a comment or narrowed except would document the deliberate choice.
4. **Extract `assert_account_id_matches(body, context)` helper** — Tasks 1.26.2 and 1.26.3 duplicate the same 5-line mismatch check in `routers/text.py:74-85` and `routers/upload.py:142-153`. Natural place: `utils/context_utils.py`. Cleaner once Task 1.26.4 lands (the two-helper auth-context split is the natural moment to consolidate).
5. **Add UUID validators to `TextCleanRequest.account_id` and `UploadInitRequest.account_id`** — production callers send UUIDs but the Pydantic fields are `str` typed. Non-UUID values slip through and only fail at the downstream `raw_interactions.account_id UUID` write. Reject at the 422 boundary.
6. **`main.py` `_ws_enrich_*` prefix inconsistency** — other locals in the same finally block use `ws_*` (no underscore). Rename for consistency.

### Workstream D — Phase 1 hygiene cleanup

Capture and dispatch where appropriate:

- **eq-frontend PR #349 still OPEN with failing CI** (Live DB Tests + Vercel preview). The schema migration is LIVE on Neon eq-dev (verified). The PR is paperwork at this point. Either investigate the CI failures and merge-with-failing-checks, OR fix the CI and merge. Don't leave it open indefinitely.
- **Top-level legacy tests** `tests/test_jwt_auth.py` and `tests/test_integration_endpoints.py` have 52 pre-existing failures from Phase 1's auth-contract tightening. The verify script intentionally excludes them. Delete or rewrite to the new contract.
- **Stale git stash** on `railway-deployment` branch left `.env.example` and `requirements.txt` in conflict state during the prior session. Recovered with `git checkout HEAD --`. The stash is still in the reflog. Investigate origin and discard if no longer needed.

---

## Read these in order before doing any work

Total reading: ~25-35 minutes. Most of it is reading what already shipped + Phase 1.5 specs.

1. **Auto-loaded:** `MEMORY.md` (you should see status `PHASE_1_SHIPPED_PHASE_1_5_PENDING`).

2. **Project status + full decision log:** `~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/project_contact_quality_initiative.md` — read the `## Phase 1 SHIPPED (2026-05-14)` section for the ship summary, including production E2E results and the 6 captured polish nits.

3. **The P2 fix specs (your immediate workstream):** `tasks/downstream/codex-phase-1-findings.md` — Tasks 1.26.4, 1.26.5, 1.26.6. **READ THESE FIRST among the project files** — they're your starting point.

4. **The implementation plan for Phase 1.5 main scope:** `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` starting at line ~2538. Outbox, worker, queue UI, eq-agent-action-core acceptance tests.

5. **The design document (canonical project intent):** `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` — Section 5.3 (queue UPSERT), Section 6 (worker + outbox), Section 7.2 (Phase 1.5 scope), Section 8.4 (recurring quality gate), Section 12 (verifiable invariants).

6. **Phase 1 audit trail (skim only):** `tasks/downstream/codex-phase-1-review.md` includes both the original self-review (Round 1) and the appended `## Round 2 results (2026-05-14)` section. Useful for understanding what Phase 1 shipped with.

7. **Architecture reference:** `docs/contacts-architecture.md` — Section 3.4 documents the three-state branching contract Phase 1 introduced.

---

## Carry-forward invariants (these now hold in production)

All from Phase 1, plus four new ones surfaced by Codex Round 1:

- **Contact ID consistency:** every contact carries UUIDv4 `contact_id`. Never store a name without an ID.
- **Tenant isolation:** every Postgres + Neo4j query MUST include `tenant_id`. Never cross-tenant queries.
- **Three-state branching:** known account → contact; unknown business domain → queue signal, no contact; personal/internal → skip. NEVER fall back to anchor.
- **Backend rejection over frontend trust:** every ingestion path validates `account_id` at the auth-context boundary or 400s (WebSocket: 1008). Queue-hold path is the ONLY exemption.
- **First-owner-wins UPSERT:** `pending_account_mappings.owner_user_id` is never reassigned by routine UPSERT.

**New invariants from Codex Round 1 + this session (now load-bearing):**

- **Caller-side completeness:** when adding a new parameter to an internal function, update every caller in the SAME commit. A "wire callers in Phase X.5" deferral is a silent-failure bomb — unit tests pass but production traffic never reaches the new code path. (T1.26.1 was the proof.)
- **Auth-boundary wins on body/header conflicts:** body fields with the same semantic as an authenticated header value are at best verification checks, at worst security regressions. Default: reject mismatch with 400. (T1.26.2 + T1.26.3 were the proofs.)
- **Real `/codex review` is non-substitutable.** Static-invariant self-review missed three P1s in the prior session; real Codex caught them. Always run real Codex at every phase boundary.
- **Production E2E is a real quality gate.** Automated tests + Codex catch 90%+ of issues; the last 10% requires hitting the live API with a Railway-issued short-lived JWT. Wire this into every phase ship from now on. Reusable script at `/tmp/e2e_phase_1_production.py`.
- **`/context-save` at session end is mandatory.** Handoff docs (this file + auto-memory) answer "what's the state?"; the gstack checkpoint answers "is the next agent's first command going to work?" Both are required. The prior session shipped a great handoff doc but no checkpoint — when this session ran `/context-restore`, it returned `NO_CHECKPOINTS` and started cold. This session fixed it by saving a checkpoint at the end. Every session must do the same before declaring done.

---

## Repository state (as of 2026-05-14 Phase 1 ship)

- **Current branch:** `main` (merged Phase 1 work). The feature branch `feat/contact-quality-phase-1` was deleted as part of `gh pr merge --delete-branch`.
- **Phase 1 PRs:**
  - eq-frontend PR #349 — 🟡 OPEN with failing CI. Schema is LIVE on Neon. Phase 1.5 hygiene task.
  - eq-email-pipeline PR #6 — ✅ MERGED (squash) at 2026-05-14T10:40:28Z, commit `895cc9f`.
  - live-transcription-fastapi PR #10 — ✅ MERGED (squash) at 2026-05-14T10:40:43Z, commit `2552b4b`.
- **Railway production deploys:**
  - live-transcription-fastapi: deployment `07d58610-edfd-42d3-b495-6a721736e20e` → SUCCESS
  - eq-email-pipeline: deployment `376d5f79-5950-4184-a373-2da742548c23` → SUCCESS
  - Public URL: `https://live-transcription-fastapi-production.up.railway.app`
- **Production E2E artifact:** `/tmp/e2e_phase_1_production.py` (Python script that issues a short-lived JWT and verifies the auth-boundary fixes). Re-runnable for Phase 1.5 regression checks.
- **Verification artifacts:**
  - `scripts/verify_phase_1_invariants.sh` — exit 0 (all 12 static invariants PASS)
  - `tasks/downstream/codex-phase-1-findings.md` — canonical Phase 1.5 P2 fix specs
  - `tasks/downstream/codex-phase-1-review.md` — Round 1 self-review + Round 2 GATE: PASS results
- **Neon eq-dev schema (project `super-glitter-11265514`):** Phase 1 columns + new `pending_account_mapping_signals` table all live and verified via Neon MCP.
- **Test tenant:** `11111111-1111-4111-8111-111111111111` (safe to seed).

---

## Dispatch pattern (carry forward from the prior session)

Each subagent dispatch must include:

1. **The task block from `codex-phase-1-findings.md` in full** — copy the entire `### Task 1.26.X` section verbatim into the prompt.
2. **Scene-setting context** — where this fits in the architecture, what previously shipped, what invariants must hold.
3. **Explicit boundaries** — what to NOT touch (e.g., don't refactor adjacent unrelated code, don't extract helpers unless the spec asks for it, don't deviate from the fix spec).
4. **Acceptance evidence required** — specific test commands, expected output, commit hashes, `git diff` stats.
5. **TDD discipline** — each fix has a failing test that demonstrates the bug today, then becomes green after the fix.

After each implementer subagent returns:
- Verify the commit exists in `git log`
- Run the test commands yourself if the diff is non-trivial
- Dispatch the **spec compliance reviewer** subagent FIRST (verifies code matches spec)
- Then dispatch the **code quality reviewer** subagent (verifies craftsmanship)
- Loop on issues from either reviewer; don't accept "close enough"

The three prompt templates live at `~/.claude/plugins/cache/claude-plugins-official/superpowers/5.1.0/skills/subagent-driven-development/{implementer,spec-reviewer,code-quality-reviewer}-prompt.md`.

---

## What NOT to do this session

- Do NOT skip real `/codex review` at the Phase 1.5 boundary. The lesson from Phase 1 is non-substitutable.
- Do NOT defer P2 fixes "to a future session" if context allows tackling them. They have ready-to-execute specs.
- Do NOT modify locked design decisions or invariants. The design doc Section 12 is the contract.
- Do NOT touch the Phase 1 auth-context layer (`utils/context_utils.py`) in a way that breaks the existing `X-Account-ID required` enforcement for ingestion routes. Task 1.26.4's fix is a PARAMETER OR HELPER SPLIT, not a removal.
- Do NOT use `--no-verify` on any commit. Pre-commit hooks exist for reasons.
- Do NOT skip production E2E. Once Phase 1.5 ships, re-run `/tmp/e2e_phase_1_production.py` and add Phase 1.5-specific test cases (e.g., `GET /upload/status` with JWT-only after Task 1.26.4 lands → expect 200).

---

## Context budget guidance

Phase 1.5 has THREE distinct deliverables; size them realistically:

- **Three P2 fixes (Tasks 1.26.4/.5/.6) + Codex Round 3 + merge + canary:** Plausible in one session. Each P2 is 5-30 lines of production code + a TDD pair. Task 1.26.5 requires a cross-repo Prisma migration round-trip; budget accordingly.
- **Outbox + worker + queue UI:** Multi-session workstream. Likely 2-3 sessions.
- **Phase 1 polish nits + hygiene cleanup:** Defer to end-of-session or skip entirely. Don't let them bloat the P2-fix session.

**Signs to stop and hand off:**
- Codex Round 3 surfaces unanticipated P1s
- Cross-repo migration for Task 1.26.5 takes more than one round-trip
- You're about to invoke a heavy-context skill mid-execution (e.g., `/codex challenge`)
- Context approaching limits

When you stop, do a clean handoff: update auto-memory + rewrite this NEXT-SESSION-START-HERE.md + commit the handoff changes. **This file is the template for what a good handoff looks like.**

---

## Suggested first actions for the next agent

1. Run `/context-restore`. You should see a checkpoint titled **"phase-1-shipped-handoff-for-phase-1.5"** dated 2026-05-14 — load it. It contains the working state at session end, including production credentials reference, project/service/environment IDs, and explicit warnings about gaps not to repeat (e.g., the production-E2E timing). If `/context-restore` returns `NO_CHECKPOINTS`, something went wrong — surface that immediately before doing any work.
2. Read `MEMORY.md` + this file in full.
3. Read `tasks/downstream/codex-phase-1-findings.md` Tasks 1.26.4, 1.26.5, 1.26.6 in order.
4. Skim Phase 1 ship summary in the auto-memory project file (`## Phase 1 SHIPPED` section).
5. Briefly confirm understanding back to the user (one paragraph).
6. Invoke `superpowers:subagent-driven-development` for the canonical orchestrator workflow.
7. Dispatch the cross-repo agent first for Task 1.26.5's Prisma migration (it's the long-lead item — runs in parallel while you do Tasks 1.26.4 and 1.26.6 in this repo).
8. Dispatch the implementer for Task 1.26.4 (highest-priority P2 — active regression on polling routes).
9. Then Task 1.26.6 (smaller, single-repo).
10. Wait for the cross-repo migration to land, then dispatch the implementer for Task 1.26.5.
11. Re-run real `/codex review --base main`. Require GATE: PASS, zero P1 AND P2 findings.
12. Re-run `/tmp/e2e_phase_1_production.py` (or extend it with Phase 1.5 cases). Production E2E must PASS.
13. Update PR descriptions, merge in order (cross-repo first), verify Railway deploys.
14. `/document-release` to sync docs.
15. Update auto-memory + rewrite this handoff file for whatever comes next (Phase 1.5 main scope or the worker workstream).

---

## Final note for the next agent

Phase 1 is shipped, verified, and operational in production. The bones are right. The Codex Round 2 GATE: PASS validates the architectural decisions and the careful caller-wiring done in T1.26.1/.2/.3. The next session's job is much lower-risk: closing the three accepted-but-deferred P2s and starting the Phase 1.5 main scope.

The user is building a cutting-edge AI-native customer intelligence platform. The architectural standard remains high. Hold the bar. Run real Codex. Run production E2E. Ship clean.
