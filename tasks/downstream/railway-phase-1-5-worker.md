# Railway deployment — Phase 1.5 worker service

## What's needed

A new Railway service that runs `python -m workers` (the
account-provisioning worker poll loop) against the same Postgres database
as the FastAPI app. Source: this repo.

## Steps (next-session orchestrator work)

The Railway MCP is available; use it from the next session.

### 1. Create a new service pointed at this repo

```
# Via Railway MCP
mcp__railway__service_create_from_repo
  project_id: 847cfa5a-b77c-4fb0-95e4-b20e8773c23e
  repo: github.com/oneilstokeseqrm/live-transcription-fastapi
  branch: main
  service_name: live-transcription-fastapi-worker
```

### 2. Override the start command

The default NIXPACKS detection will run `uvicorn main:app` because of
`railway.json`. Override via Railway dashboard or MCP:

```
mcp__railway__service_update
  service_id: <new-worker-service-id>
  start_command: python -m workers
```

### 3. Copy required environment variables from the FastAPI service

The worker shares the same DB. Copy from the FastAPI service
(`59a69f3d-9a24-4041-942a-891c4a81c5fb`) to the new worker service:

- `DATABASE_URL` — Postgres connection string (same Neon project as eq-dev/prod)

Add new variables specific to the worker:

- `EQ_AGENT_ACTION_CORE_URL` — base URL of the eq-agent-action-core
  service. The agent is already production-deployed for onboarding;
  retrieve its Railway URL via `mcp__railway__service_list`.
- `EQ_AGENT_ACTION_CORE_API_KEY` — server-to-server bearer token. The
  agent likely already has an API-key pattern in place for onboarding; reuse
  it or coordinate generating a new key on the agent side.

Optional:
- `LOG_LEVEL` — default `INFO`
- `WORKER_POLL_INTERVAL_SECONDS` — default `5`

Use `mcp__railway__variable_bulk_set` for the copy + additions.

### 4. Verify the worker starts cleanly

After variables are set, trigger a deploy
(`mcp__railway__deployment_trigger`). The worker should boot, log
`Worker starting: agent_url=... poll_interval=5.0s`, then enter the poll
loop. Confirm via `mcp__railway__deployment_logs`.

### 5. Smoke-test the materialization path

Seed a queue entry in `approved` state via Neon MCP using the test tenant
`11111111-1111-4111-8111-111111111111`:

```sql
-- Seed a pending_account_mapping row
INSERT INTO pending_account_mappings
  (id, tenant_id, domain, status, owner_user_id, approval_attempt_id, created_at, updated_at)
VALUES
  (gen_random_uuid(), '11111111-1111-4111-8111-111111111111', 'smoketest.example.com',
   'approved', '...some-test-user-id...', gen_random_uuid(), NOW(), NOW());

-- Add a signal so materialization has something to materialize
-- (queue_id is the id returned by the INSERT above)
INSERT INTO pending_account_mapping_signals
  (id, queue_id, tenant_id, contact_email, contact_display_name, source_type, created_at)
VALUES
  (gen_random_uuid(), <queue_id>, '11111111-1111-4111-8111-111111111111',
   'alice@smoketest.example.com', 'Alice Smith', 'transcript', NOW());
```

Wait for the worker poll interval (5 seconds). Verify the materialization
happened:

```sql
SELECT status, resolved_account_id, mapped_at
FROM pending_account_mappings WHERE id = <queue_id>;
-- Expect: status='mapped', resolved_account_id NOT NULL, mapped_at NOT NULL

SELECT account_id, event_type, published_at
FROM account_provisioning_outbox WHERE queue_id = <queue_id>;
-- Expect: one row, event_type='account_created', published_at IS NULL (publisher not yet shipped)

SELECT email, account_id FROM contacts
WHERE email = 'alice@smoketest.example.com';
-- Expect: one row, account_id matches resolved_account_id above
```

### 6. Extend `/tmp/e2e_phase_1_production.py`

Per the plan's "Phase 1.5 Production E2E Discipline" section: add a case
that exercises this materialization path end-to-end and re-run the full
suite. Commit the script update in the same PR as the deployment-trigger
commit.

## Why this is a next-session task, not this-PR

This PR ships the worker CODE (advisory-lock, agent client, materialization,
worker loop, entrypoint). Deploying the worker requires:

- A new Railway service (one-time manual or MCP-driven setup)
- API key coordination with the eq-agent-action-core service
- A real smoke test against the deployed agent endpoint

Each of these is operational rather than algorithmic; safer to do them as
a deliberate next-session step with the Railway MCP than to combine them
with code-only work in this PR.

The existing 13-case production E2E (against the FastAPI service) confirms
the auth-boundary contracts still hold — that's the regression check for
this PR. The worker-specific E2E case ships with the Railway-service
creation work.
