# BLOCKER — Worker built against an agent contract that doesn't exist

**Surfaced:** 2026-05-14, during Workstream D (Railway worker service deployment)
**Status:** ARCHITECTURE DECISION NEEDED before worker can be deployed

## What happened

Phase 1.5 worker code (PR #12, merged at `11b3b30`) ships
`services/agent_action_core_client.py` that calls eq-agent-action-core's
`POST /api/enrich` with a request shape that doesn't match the live agent
service. The deployment session was the first time the worker was tested
end-to-end against the live agent, and it failed on payload validation.

## Evidence

All probed against `https://eq-agent-action-core-production.up.railway.app`
on 2026-05-14:

### Auth (solvable)

Agent requires a real JWT signed with `INTERNAL_JWT_SECRET` (HS256, `iss`,
`aud`, claims present). Raw secret as bearer fails with
`{"detail":"JWT verification failed: Not enough segments"}`. A minted JWT
with `iss=eq-frontend`, `aud=eq-backend`, `tenant_id`, `user_id` claims
passes auth — confirmed.

### Request shape: WRONG

Worker sends:
```json
{"tenant_id": "...", "domain": "...", "worker_attempt_id": "..."}
```

Agent rejects with 422:
```json
{"detail":[{"type":"missing","loc":["body","url"],"msg":"Field required"}]}
```

Agent's actual `EnrichRequest` schema (from `/openapi.json`):
```json
{
  "type": "object",
  "required": ["url"],
  "properties": {
    "url": {"type": "string", "description": "The URL or domain to enrich"},
    "effort": {"type": "string", "enum": ["low","medium","high"], "default": "medium"}
  }
}
```

`tenant_id` is derived from the JWT, not the body. `worker_attempt_id` is
dropped silently (no `X-Idempotency-Key` route in agent's code).

### Response shape AND service purpose: WRONG

Worker expects synchronous JSON `{account_id, domain}`
(`services/agent_action_core_client.py:16-17`, `:47-48`).

Agent's `POST /api/enrich`:
- Default (`stream=true`): returns `Content-Type: text/event-stream` SSE
  stream of research progress events.
- With `?stream=false`: returns AccountProfile (structured research data)
  after blocking 30–90+ seconds.

Either way, the response is **research data about a company**, not an
`account_id` in this app's Postgres. The agent's `/api/enrich` description
(verbatim from OpenAPI): "Enrich a company URL into a structured
AccountProfile."

### Architectural gap

`workers/materialization.py:127` `materialize_account_approval` takes a
pre-existing `account_id: str` and uses it to attach contacts /
interactions / outbox events. **It does not INSERT into the `accounts`
table.** The worker was designed assuming the agent service is the
account-creation point. **The agent service does no such thing — it's a
research-only service.**

### No alternative endpoint exists

Inspection of the agent's `/openapi.json` (44 endpoints total):
- `POST /api/jobs` (`SubmitJobRequest`): takes `account_id` as input,
  never creates one.
- `POST /api/onboarding/sessions`, all `/api/executive/*`, `/api/summary/*`,
  `/api/context-capture/*`: onboarding / executive briefing / summary
  workflows. None create an account.
- No `POST /api/accounts/...` route exists.
- Notable schemas in spec: `EnrichRequest`, `SubmitJobRequest`,
  `SubmitJobResponse`. No `CreateAccountRequest` or similar.

## Why this wasn't caught earlier

Phase 1.5 worker code (PR #12) had 6 Codex review rounds focused on
internal correctness: advisory lock semantics, autobegin pitfalls, race
conditions in placeholder summary insertion, idempotency under replays.
Codex review couldn't probe the external agent service. The integration
contract with eq-agent-action-core was scaffolded against an imagined
contract because no one read the agent's `/openapi.json` at design time.

The contract was first tested live during this deployment session
(Workstream D, Phase 1.5 stopping point work). It failed.

This is the second time in this initiative a contract was imagined
rather than verified before code was written. The first was Phase 1's
caller-side completeness gap (Codex Round 1; lesson codified 2026-05-14
in `tasks/lessons.md`). The pattern is: when a downstream service is
"production-deployed" for a different use case, the assumption that its
API matches a new use case's needs is unverified.

## Three architectural paths forward

### A) Move account creation into the worker (RECOMMENDED)

- Worker calls agent's `/api/enrich?stream=false` with `{url: domain,
  effort: "medium"}`, mints per-tenant JWT for each call, receives
  AccountProfile.
- Worker INSERTs into our Postgres `accounts` table itself using fields
  from the AccountProfile.
- Worker then calls `materialize_account_approval` with the new
  `account_id`.
- Agent service stays research-only; no changes there.

**Pros:** zero cross-repo coordination; agent service stays clean; one
PR; account creation lives in code that has the DATABASE_URL connection
string (the agent doesn't).

**Cons:** account creation logic lives in worker, not agent; long
enrichment time (30–90s) requires worker timeout >= 90s (currently
configured at 90.0s — see `agent_action_core_client.py:22`, so already
sufficient).

### B) Add a new endpoint to eq-agent-action-core

- e.g., `POST /api/accounts/create-from-domain` that does research +
  INSERTs into a shared Postgres `accounts` table + returns
  `{account_id, domain}`.

**Pros:** matches worker's current contract; no worker code changes.

**Cons:** cross-repo work in eq-agent-action-core; the `accounts` table
lives in this app's Neon DB, which the agent currently doesn't connect to
— requires giving the agent a DATABASE_URL with write access, multiplying
the trust surface; mixes concerns (research service starts doing data
plane writes).

### C) Hybrid — agent owns research, worker owns DB write, with a shared schema contract

- Agent's `/api/enrich` stays research-only.
- Add a thin wrapper endpoint at the agent: returns AccountProfile in a
  worker-friendly synchronous JSON shape with stable field names.
- Worker INSERTs into accounts table from that response.

**Cons:** does both — touches both repos for what (A) accomplishes in one.

## Recommendation: Path A

1. Agent service is deployed and stable as research-only; touching it
   is risk no-go.
2. Account creation in our Postgres belongs in code that has the
   connection string (worker does; agent doesn't).
3. `materialize_account_approval` is already designed to take an
   `account_id`; wrapping an INSERT around it is a one-function
   addition.
4. One repo, one PR, no cross-repo coordination.

## Concrete next-session execution plan (Path A)

1. **Update `services/agent_action_core_client.py`:**
   - Replace `enrich(tenant_id, domain, worker_attempt_id)` signature.
   - New signature: `enrich(domain) -> AccountProfile` (or a typed
     subset of fields the worker actually uses).
   - POST `/api/enrich?stream=false` with `{url: domain, effort:
     "medium"}`.
   - Header: `Authorization: Bearer <per-tenant JWT>`. The JWT must be
     freshly minted per request (since `tenant_id` is in the JWT claim).
   - Remove `X-Idempotency-Key` (agent doesn't support it).
   - Parse the response into a typed result (likely keeping
     `domain` + adding fields needed for accounts INSERT: legal name,
     industry, description, etc.).

2. **Add account-creation step in `workers/account_provisioning_worker.py`:**
   - Before calling `materialize_account_approval`, INSERT into
     `accounts` table using AccountProfile data. Use `ON CONFLICT
     (tenant_id, domain) DO NOTHING RETURNING id` for idempotency
     under replays.
   - If conflict-no-return (account already exists), SELECT to get the
     existing `account_id`.
   - Pass that `account_id` to `materialize_account_approval`.

3. **Per-tenant JWT minting in the worker:**
   - Pass `INTERNAL_JWT_SECRET` to `AgentActionCoreClient` constructor
     (not a static API key).
   - Worker mints a JWT per `enrich()` call with the tenant_id claim
     matching the request.
   - JWT expiry: short (5 min) since it's minted just-in-time.

4. **Tests:**
   - Update `tests/test_agent_action_core_client.py` (if exists) to
     match new contract.
   - Unit-test the new "INSERT account before materialize" flow.
   - Verify idempotency under replay (same domain twice → one account
     row, two materializations both succeed).

5. **Codex review** after code changes (per the recurring quality-gate
   discipline).

6. **Resume Workstream D** after the new PR merges: env vars on worker
   service drop `EQ_AGENT_ACTION_CORE_API_KEY` (no longer used; just
   need `INTERNAL_JWT_SECRET`), keep `EQ_AGENT_ACTION_CORE_URL`. Deploy.
   Smoke-test.

## What was already done this session

Before the blocker surfaced (~30 minutes of probing):

- Pre-flight checks all PASS: test tenant exists, schema intact, e2e
  file present, FastAPI endpoint reachable.
- Located eq-agent-action-core service in Railway project
  `421e079f-2e46-4c22-83c4-0fe6208e6aff`, service
  `3036ea0f-afc9-4bc4-889d-c98617d81e96`, public URL
  `eq-agent-action-core-production.up.railway.app`.
- Inspected agent env vars: `INTERNAL_JWT_SECRET` shared with FastAPI
  service (same value).
- Tested JWT auth: minted a service-identity JWT with the shared secret
  + `iss=eq-frontend` + `aud=eq-backend` + `tenant_id` claim. Confirmed
  the agent verifies it (got past auth to payload validation).
- Read agent's full `/openapi.json` (44 endpoints) — confirmed there is
  no "create account from domain" route anywhere.
- Read worker code in detail: `workers/account_provisioning_worker.py`,
  `workers/materialization.py`, `services/agent_action_core_client.py`.
- Confirmed materialization expects pre-existing `account_id` and never
  creates accounts itself.

## Files touched

- `tasks/downstream/blocker-agent-contract-mismatch.md` (this file)
- `docs/superpowers/specs/NEXT-SESSION-START-HERE.md` (rewritten for
  the architecture decision session)
- `tasks/lessons.md` (new lesson: probe external service contracts at
  design time)
- Auto-memory files (project status, blocker section)

No code changes this session; the worker remains undeployed.
