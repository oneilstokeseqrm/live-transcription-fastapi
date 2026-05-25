# Phase 2f — Granola admin endpoints + cron pinger

Branch: `phase-2f/granola-admin` off `main` (63af319).
Plan spec: `tasks/granola-integration-plan.md` §Phase 2f (lines 689-731). LOCKED-30/31/34.

## 1. Vault module — 2 new accessors (services/vault/user_credentials.py)
Both stay behind the ALLOWLIST gate; `"archive"` is already a valid AuditOperation.
- [ ] `get_credential_status(*, tenant_id, user_id, caller_module, pool, trace_id=None) -> CredentialStatus | None`
      — non-decrypting SELECT of lifecycle columns for ANY status (active/revoked/error/archived). Serves /rotate, /status, /disconnect. Audit op="read".
- [ ] `archive_credential(*, tenant_id, user_id, credential_id, caller_module, pool, trace_id=None) -> bool`
      — soft-delete: status='archived', archived_at=NOW, WHERE id+tenant+user AND archived_at IS NULL. Idempotent. Audit op="archive".
- [ ] Export both + CredentialStatus from services/vault/__init__.py.

## 2. routers/granola.py (NEW) — 5 JWT-authed endpoints, prefix `/integrations/granola`
Auth: `get_auth_context_polling` (no X-Account-ID). tenant_id=UUID(ctx.tenant_id); user_id via `_resolve_user_uuid(ctx)` (pg_user_id; 400 if not UUID-shaped). caller_module="routers.granola".
- [ ] `POST /validate` {api_key} → GranolaAPIClient.list_folders() validates auth + returns folders. Map GranolaError→{ok:false, reason}. Does NOT store. HTTP 200 with ok flag.
- [ ] `POST /connect` {api_key, folder_id, folder_name?} → store_credential; on UNIQUE-violation fall to reactivate_credential; if active→409. Then load via get_granola_credential_for_user (decrypt round-trip) + run_one_cycle once (LOCKED-31 save&test). Return {ok, status, first_poll}.
- [ ] `POST /rotate` {new_api_key} → get_credential_status for id; 404 if none/archived; rotate_credential_key.
- [ ] `GET /status` → get_credential_status + 7d activity counts from external_integration_runs. No decryption.
- [ ] `DELETE /` → archive_credential (soft-delete); idempotent.

## 3. main.py
- [ ] `app.include_router(granola.router)` (router carries its own prefix).

## 4. Tests (AsyncMock + FastAPI TestClient, NO Docker — mirror test_granola_cron.py)
- [ ] tests/unit/test_granola_admin.py — validate (happy/auth_failed/outage/401), connect (happy/reconnect-reactivate/already-active-409/bad-key-first-poll-error), rotate (happy/404), status (connected/not-connected shape), disconnect (soft-delete/idempotent), auth-401 on each.
- [ ] tests/unit/vault/test_user_credentials.py — get_credential_status (active/archived/none/allowlist-reject), archive_credential (happy/idempotent/cross-tenant-noop/allowlist-reject).

## 5. Pre-merge gates
- [ ] `DBOS_SYSTEM_DATABASE_URL=... pytest` granola + vault suites pass; 0 regressions (baseline: 1 unit + 16 integration pre-existing failures unrelated).
- [ ] `verify_consumer_contracts.py --source generic --interaction-type meeting` → 0 drift (envelopes untouched; sanity).
- [ ] Codex pre-merge review — 4-round soft cap; STOP on oscillation (lessons.md).
- [ ] Open PR. DO NOT merge / push-to-main / change Railway without per-action user auth.

## 6. Cron pinger — DECISION NEEDED (surface to user after endpoints ready)
- [ ] Railway cron service (curlimages/curl, `*/5 * * * *`) vs external cron (cron-job.org / GitHub Actions). INTERNAL_CRON_SECRET already set. Needs Railway dashboard (MCP can't set cronSchedule).

## Review (filled at end)
- (pending)
