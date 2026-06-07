# PROD RLS tenant-scoping invariant (READ before adding DB access)

**Added with the EQ-120 prod repoint (Linear EQ-120).** This service now runs in a
prod environment where it connects to Postgres as a **non-owner role**
(`eqprod_transcription`, `NOBYPASSRLS`). In **dev** it still connects as the DB
**owner** (which bypasses RLS), so the rule below is a **no-op in dev but
load-bearing in prod**. Same code, both environments.

## The invariant

> Any read OR write of a **strict** (RLS+FORCE) or **vault** table MUST run inside a
> transaction that has set the `app.tenant_id` GUC first — via the helpers in
> [`services/tenant_scope.py`](../services/tenant_scope.py). Otherwise it **works in
> dev but FAILS CLOSED in prod** (reads error or return 0 rows; writes are rejected
> by the policy WITH CHECK).

If you add code that touches one of the tables below and you do NOT go through a
helper, you will ship a bug that passes every dev test and breaks the first time it
runs in prod.

## Tables that REQUIRE scoping (verified against the prod `pg_policy` set, 2026-06)

- **Strict (RLS + FORCE, policy on `app.tenant_id`):** `accounts`, `contacts`,
  `interaction_insights`, `interaction_summary_entries`. (The full prod needs-GUC set
  is larger — ~26 tables — but these four are the ones THIS service touches today.)
- **Vault:** `vault.user_credentials`, `vault.credential_access_log`.
- **NOT scoped (safe as-is):** `granola_import_runs`, `external_integration_runs`,
  `upload_jobs`, `emails`, `email_threads`, `calendar_events*`, `personas`,
  `pending_*`, `interaction_*_links`, `raw_interactions`, `interaction_summaries`,
  `draft_interactions` — these are RLS-off or `app_service`-permissive (the non-owner
  role reaches them without the GUC). Over-scoping them is harmless but unnecessary.

When in doubt whether a NEW table is strict: check prod —
`SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='<t>'` and
`SELECT polname, qual FROM pg_policies WHERE tablename='<t>'`. A policy referencing
`current_setting('app.tenant_id'...)` means it needs the GUC.

## How to do it right

```python
from services.tenant_scope import tenant_session, scoped_acquire, set_tenant_guc

# SQLAlchemy (ORM or text()) — one unit of work; the helper OWNS the transaction,
# so do NOT call session.commit()/rollback() yourself.
async with tenant_session(tenant_id) as session:
    await session.execute(...)        # accounts / contacts / interaction_insights / ...

# raw asyncpg, standalone statement (no explicit tx of your own):
async with scoped_acquire(pool, tenant_id) as conn:
    await conn.execute(...)           # vault.user_credentials / credential_access_log

# raw asyncpg, when you ALREADY open `async with conn.transaction():`
async with conn.transaction():
    await set_tenant_guc(conn, tenant_id)   # FIRST statement, before any vault SQL
    await conn.execute(...)
```

`set_config('app.tenant_id', <t>, true)` is `is_local=true` — it only survives the
current transaction, which is exactly why the helpers open one (a bare `set_config`
before a standalone statement is a silent no-op).

## Cross-tenant cron reads are the one exception

The Granola poll cron must enumerate **every** tenant's credentials. Those two scans
go through `SECURITY DEFINER` functions (`vault.list_active_credentials_xt()` /
`vault.list_uninitialized_granola_creds_xt()`, in `infra/sql/eq120_cron_enumerate.sql`)
that bypass RLS for just that read; each per-credential poll then re-scopes with
`set_tenant_guc`. If you add another genuinely cross-tenant scan of a strict/vault
table, add a hardened definer function — do NOT remove the GUC from the per-tenant path.

## Verifying before you ship to prod

A change is prod-safe when, run as a non-owner role on a prod-copy DB branch: a scoped
read/write succeeds, an unscoped one fails closed, and a cross-tenant write is rejected.
See `EQ-CORE/tasks/environments/EQ-120-REPOINT-PLAN.md` (the "Live RLS proof" section)
for the exact probe pattern.
