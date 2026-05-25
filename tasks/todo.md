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

## Progress
- [x] §1 vault accessors (get_credential_status, archive_credential) + exports — DONE
- [x] §2 routers/granola.py — 5 endpoints — DONE
- [x] §3 main.py register — DONE
- [x] §4 tests (93 granola+vault; full unit suite green, 1 pre-existing unrelated failure) — DONE
- [x] §5 verify_consumer_contracts.py 0 drift — DONE
- [x] §5 Codex pre-merge review — 9 rounds, all folded (see trajectory below)
- [x] §5 PR #29 opened + MERGED (squash) as `260b863`
- [x] §6 deploy verified — Railway `eb2d4c81` SUCCESS; prod endpoints live + auth-gated
- [ ] §6 cron pinger — DEFERRED by user to a focused next session (see NEXT-SESSION-START-HERE.md)

## PHASE 2f COMPLETE (2026-05-25)
Admin endpoints shipped + deployed + prod-verified. Cron pinger held for next session.
2 edges ticketed (plan §2.1 #12/#13). Next: wire trigger + first real /connect E2E + Phase 2g.

## Codex review trajectory (branch phase-2f/granola-admin)
R1: 2 P2 + 1 P3 — /connect insert disambiguation, post-store load wrap, status mapping → folded
R2: 3 P2 — activity rollup updated_at not created_at; _activity_counts 503; /disconnect wrap → folded
R3: 2 P1 + 2 P2 — require pg_user_id (no Auth0 fallback; also enforces JWT); transient→connected; reconnect-race 409 → folded
R4: 2 P2 — /validate JWT gate; /connect read-back-None graceful + advisory lock around the test poll (closes connect/scheduler concurrent-poll race) → folded
R5: 1 P1 (oscillation) + 1 P2 — lock-setup graceful; /validate auth flip (freeze) → folded + frozen
R6: 2 P2 — audit-failure→503; /validate JWT oscillation RESOLVED via bearer-token gate (JWT required, pg_user_id not) → folded
R7: 1 P1 — reconnect-during-in-flight-cycle stale write-back → gate reactivate on advisory lock → folded
R8: 1 P1 + 1 P2 — /rotate same race (shared _credential_poll_lock helper) → folded; bad-folder recovery (P2) → SURFACE to user
R9: final confirmation of the R8 refactor — (running)

NOTE: rounds far exceeded the 4-round soft cap because this is a genuinely
concurrency-rich surface (credential mutations + a 5-min scheduler). R1-R8
were real bugs (gate doing its job); the only oscillation was /validate auth
(R4↔R5↔R6), resolved at the root in R6 (bearer-token gate). Per the
codex-oscillation lesson, stopping the loop after R9 and surfacing remaining
PRODUCT decisions (bad-folder recovery; cron pinger) to the user.

## Open decisions for the user
1. **Bad-folder recovery (Codex R8 P2):** /connect stores the credential before
   the first poll proves folder_id is valid. A bad folder_id leaves a stuck
   non-archived row (must /disconnect to retry). Options: validate folder in
   /connect before store; allow /connect to re-configure a broken (revoked/error)
   row; or ship + ticket. (No PATCH /folder endpoint in this phase.)
2. **Cron pinger:** Railway cron service vs external cron. Needs Railway change
   (user auth). INTERNAL_CRON_SECRET already set.
