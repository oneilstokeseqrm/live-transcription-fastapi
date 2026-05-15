# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative
**Last session:** 2026-05-15 (Workstream D pre-deploy probe — BLOCKED)
**Status:** 🛑 **PHASE_1.5_BLOCKED_AGENT_CONTRACT_MISMATCH** — worker code (PR #12) was scaffolded against an imagined eq-agent-action-core contract. Workstream D deployment STOPPED before any Railway changes were made. Architecture decision + code-change PR required before deployment can resume.
**This session's job:** Decide the architectural path (recommendation: Path A in `tasks/downstream/blocker-agent-contract-mismatch.md`), then execute the code-change PR. After it ships, resume Workstream D (Railway worker deployment) + Workstream E (production E2E worker case).

---

## Critical context (READ FIRST)

1. **The block is purely the worker↔agent contract.** Everything else verified GREEN:
   - Test tenant exists in Neon eq-dev.
   - Phase 1.5 schema intact (contacts.account_id NOT NULL, account_provisioning_outbox + all 10 cols, pending_account_mappings + all 8 lifecycle cols).
   - `/tmp/e2e_phase_1_production.py` (486 lines) present.
   - FastAPI production endpoint reachable.
   - Railway projects + service IDs all located.
   - INTERNAL_JWT_SECRET shared between FastAPI and agent services.
   - JWT auth scheme verified working against the agent (HS256, iss=eq-frontend, aud=eq-backend).

2. **What's wrong:** worker sends `{tenant_id, domain, worker_attempt_id}` to `POST /api/enrich` and expects synchronous `{account_id, domain}`. The agent service requires `{url, effort?}`, returns either SSE stream or AccountProfile after 30–90s blocking, and **never INSERTs into our `accounts` table** — it's a research-only service. The worker's `materialize_account_approval` requires a pre-existing account_id but no agent endpoint creates accounts.

3. **The fix lives entirely in THIS repo.** Path A (recommended): update `services/agent_action_core_client.py` to call the actual contract, and add `INSERT INTO accounts ... ON CONFLICT` in the worker before materialize. No cross-repo work required.

4. **The user is a non-developer founder.** Make confident technical decisions; surface only product/strategic decisions. Work without stopping for clarifying questions.

5. **All prior shipped scope is intact.** PR #10 (Phase 1), PR #11 (Phase 1.5 P2), PR #12 (worker foundation), PR #13 (publisher + queue actions) all still merged. Production E2E 20/20 PASS valid for shipped routes. Only the worker's end-to-end materialization path is broken — the FastAPI service is unaffected.

---

## What this session does

### Workstream C (NEW — code-change PR to fix the contract)

Per `tasks/downstream/blocker-agent-contract-mismatch.md`, Path A recommendation:

1. **Read the blocker doc in full** (it has the full evidence, contract details, three options A/B/C analyzed, and a step-by-step execution plan).

2. **Update `services/agent_action_core_client.py`:**
   - Replace signature: `enrich(domain) -> AccountProfile` (or a typed subset of fields the worker needs).
   - POST to `/api/enrich?stream=false` with `{url: domain, effort: "medium"}`.
   - Per-tenant JWT minting in the client (since `tenant_id` is in the JWT claim, not body). JWT expiry: short (~5 min) since minted just-in-time per call.
   - Constructor takes `internal_jwt_secret: str` instead of `api_key: str`.
   - Remove `X-Idempotency-Key` header (agent doesn't support it).
   - Parse AccountProfile response into a typed dataclass with the fields the accounts INSERT will need.

3. **Add account creation in `workers/account_provisioning_worker.py`:**
   - Before calling `materialize_account_approval`, INSERT into `accounts` table using AccountProfile data.
   - Use `INSERT ... ON CONFLICT (tenant_id, domain) DO NOTHING RETURNING id` for idempotency under replays + concurrent workers.
   - On conflict-no-return, SELECT to get the existing account_id.
   - Pass that account_id to `materialize_account_approval`.

4. **Update `workers/__main__.py`:**
   - Drop `EQ_AGENT_ACTION_CORE_API_KEY` env var requirement.
   - Add `INTERNAL_JWT_SECRET` env var requirement (same value as FastAPI service has).

5. **Tests:**
   - Update / write unit tests for the new client signature.
   - Unit-test the "INSERT account before materialize" flow.
   - Regression test: replay safety (same domain twice → one account row, two materializations both succeed).
   - Test JWT minting per-tenant with the correct claims.

6. **Codex review** on the diff (recurring quality-gate discipline).

7. **Production E2E extension:** add a worker materialization case to `/tmp/e2e_phase_1_production.py` (the case the prior session deferred as Workstream E — now it can ship with this PR).

8. **Ship the PR.** Once merged, Railway auto-deploys the FastAPI service (no harm — it doesn't run the worker).

### Workstream D (after PR ships) — Railway worker deployment

Per `tasks/downstream/railway-phase-1-5-worker.md` 6-step recipe, with TWO env var changes from the original recipe:

- DROP: `EQ_AGENT_ACTION_CORE_API_KEY`
- ADD: `INTERNAL_JWT_SECRET` (copy value from FastAPI service)
- KEEP: `EQ_AGENT_ACTION_CORE_URL` = `https://eq-agent-action-core-production.up.railway.app`
- KEEP: `DATABASE_URL`, `AWS_REGION`, `EVENTBRIDGE_BUS_NAME` (or use default `default`)

Then deploy, verify worker + publisher startup logs, smoke-test with a seeded approved queue entry, watch for materialized account + outbox row + published_at within ~30-100 seconds (longer than original 15s estimate because agent enrichment takes 30–90+ seconds).

### Workstream E — production E2E worker case

If not shipped as part of Workstream C's PR, add it post-deploy. Re-run the full suite expecting 21/21 PASS.

---

## Repository state (as of 2026-05-15 session end)

- **Main HEAD:** `18d0907 docs(lessons): codify Codex spiral discipline for phasing-conditional bugs` (plus this session's docs commits if shipped)

- **All Phase 1 + Phase 1.5 P2 + Phase 1.5 worker foundation + publisher + queue action code lives in main.** None of it is broken in isolation. The break is only at the worker→agent integration layer.

- **Production state:**
  - FastAPI Railway service (`59a69f3d-9a24-4041-942a-891c4a81c5fb`) running `uvicorn main:app` in project `inspiring-upliftment` (`847cfa5a-b77c-4fb0-95e4-b20e8773c23e`).
  - Worker Railway service: **does not exist yet** (blocked).
  - Production endpoint `https://live-transcription-fastapi-production.up.railway.app` health: ✅ (404 on /healthz, service up).

- **eq-agent-action-core (in separate Railway project `421e079f-2e46-4c22-83c4-0fe6208e6aff`):**
  - URL: `https://eq-agent-action-core-production.up.railway.app`
  - Service ID: `3036ea0f-afc9-4bc4-889d-c98617d81e96`
  - Auth: HS256 JWT signed with `INTERNAL_JWT_SECRET` (shared value), claims `iss=eq-frontend`, `aud=eq-backend`, `tenant_id` (UUID), `user_id`, `exp`, `iat`.
  - Endpoints relevant to the worker: `POST /api/enrich?stream=false` body `{url, effort?}` returns AccountProfile.
  - The agent does NOT write to our Postgres. It's a research-only service backed by Tavily + OpenAI.

- **Neon eq-dev (`super-glitter-11265514`)** schema all Phase 1.5 columns present. Test tenant `11111111-1111-4111-8111-111111111111` present.

- **Production E2E artifact** at `/tmp/e2e_phase_1_production.py` — 20/20 PASS against production (as of 2026-05-14 post-merge).

---

## Reading order at session start

1. **Auto-loaded:** `MEMORY.md` — expect `PHASE_1.5_BLOCKED_AGENT_CONTRACT_MISMATCH`
2. **This file** — handoff
3. **`tasks/downstream/blocker-agent-contract-mismatch.md`** — THE EXECUTION PLAN. Full evidence + three options analyzed + step-by-step Path A plan.
4. **Project memory:** `~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/project_contact_quality_initiative.md` — read the `## Phase 1.5 worker deployment BLOCKED — agent contract mismatch (2026-05-15)` section at the bottom.
5. **Lessons:** `tasks/lessons.md` — read the new `## Probe external service contracts at design time, not deploy time (2026-05-15)` entry at the bottom.
6. **Deployment recipe (for when Workstream D resumes):** `tasks/downstream/railway-phase-1-5-worker.md` — 6-step recipe (with the env var changes noted above).

---

## Suggested first actions

1. Run `/context-restore`. Expect a checkpoint titled **"phase-1.5-blocked-agent-contract-mismatch"** dated 2026-05-15.
2. Read `tasks/downstream/blocker-agent-contract-mismatch.md` in full.
3. Confirm Path A (my recommendation) or pick B / C with rationale.
4. Run pre-flight checks (test tenant + schema + e2e file + production endpoint) — they were GREEN this session; should still be GREEN.
5. Open a feature branch. Execute Path A's 5 sub-steps (client update → account INSERT → __main__ env var swap → tests → Codex review).
6. Ship the PR. Railway auto-deploys the FastAPI service.
7. Resume Workstream D per `tasks/downstream/railway-phase-1-5-worker.md` with the env var changes documented above.
8. Smoke-test materialization end-to-end.
9. Extend `/tmp/e2e_phase_1_production.py` with the worker case (Workstream E).
10. End with `/context-save` — mandatory load-bearing invariant.

---

## Carry-forward invariants (all still load-bearing in main)

Everything from prior sessions remains correct. Adding one for this session:

- **External service contract verification at design time.** Before designing code against a live external service's contract, probe its `/openapi.json` (or equivalent — Swagger, GraphQL introspection, gRPC reflection, hand-written API doc). The cost of one curl is ~5 seconds. Phase 1.5's worker code was scaffolded against a contract that doesn't exist; the integration mismatch was first detectable at deploy time. Future plans calling external services must include a "Verified contract" section citing the actual request/response shape from the service's spec.

All other invariants from prior phases unchanged. See the project memory file for the full list.

---

## Final note for the next agent

The work this session is a tightly-scoped code-change PR (Path A) followed by the Railway deployment work that was originally scheduled for this session. The blocker doc is comprehensive — read it first, follow its execution plan, and the work is one to two PRs of focused engineering. No re-design required; the architectural reset is small (the agent is a research service; the worker also owns account creation; that's it).

After Workstream C ships and Workstream D deploys with a clean smoke test, the explicit Phase 1.5 STOPPING POINT per design Section 7.3 will kick in. Re-plan Phase 2 comprehensively before any further commitment.
