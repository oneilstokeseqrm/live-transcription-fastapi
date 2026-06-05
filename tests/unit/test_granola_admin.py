"""Unit tests for :mod:`routers.granola` (Phase 2f admin endpoints).

Uses ``fastapi.testclient.TestClient`` against a minimal app that mounts
only the granola admin router. No DBOS launch, no DATABASE_URL, no network:
the vault accessors, the asyncpg pool, ``run_one_cycle``, and the Granola
HTTP client are all patched at the module level (per
``feedback_test_pattern_no_docker`` — AsyncMock unit tests, no Docker).

Coverage maps to the plan §Phase 2f endpoint table + the enumerated unit
tests there:

* /validate — happy (folders), auth_failed → ok:false, outage → ok:false,
  401 without a JWT.
* /connect — happy new (store + first poll), reconnect-after-disconnect
  uses reactivate (UPDATE not INSERT), already-active → 409, first-poll
  credential error reflected, first-poll raise is graceful.
* /rotate — happy (looks up id then rotates), no credential → 404,
  archived → 404.
* /status — connected shape with 7-day activity, not-connected (none),
  not-connected (archived).
* /disconnect — soft-delete, idempotent on none + already-archived.
* Identity — non-UUID pg_user_id → 400.
"""

from __future__ import annotations

import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from routers import granola
from services.granola_ingestion.errors import GranolaError, GranolaErrorCode
from services.vault import CredentialStatus, VaultError, VaultErrorCode


_TENANT = "11111111-1111-4111-8111-111111111111"
_PG_USER = "22222222-2222-4222-8222-222222222222"
_TRACE = "33333333-3333-4333-8333-333333333333"
_BASE = "/integrations/granola"


# ---------------------------------------------------------------------------
# Fixtures + fakes
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Carries a default bearer token so /validate's JWT gate passes; the
    happy-path tests patch get_auth_context_polling so the token isn't
    actually verified."""
    app = FastAPI()
    app.include_router(granola.router)
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


@pytest.fixture
def noauth_client():
    """No default Authorization header — for exercising the unauthenticated
    401 paths (missing-JWT / legacy-without-bearer)."""
    app = FastAPI()
    app.include_router(granola.router)
    return TestClient(app)


def _ctx(*, tenant=_TENANT, pg_user: str | None = _PG_USER, user_id="auth0|abc", trace=_TRACE):
    """A RequestContext-shaped stand-in (only the attrs the router reads)."""
    return types.SimpleNamespace(
        tenant_id=tenant,
        user_id=user_id,
        pg_user_id=pg_user,
        trace_id=trace,
        account_id="",
        user_name="Test User",
    )


class _FakeConn:
    """asyncpg stand-in: `fetch` feeds activity rollups; `fetchval` answers
    the /connect advisory-lock acquire (True = lock free → poll proceeds);
    `execute` swallows the unlock."""

    def __init__(self, fetch_returns=None, fetchval_returns=True, fetchval_raises=None):
        self.fetch_returns = fetch_returns or []
        self.fetchval_returns = fetchval_returns
        self.fetchval_raises = fetchval_raises
        self.fetch_calls = []
        self.fetchval_calls = []
        self.execute_calls = []

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return list(self.fetch_returns)

    async def fetchval(self, sql, *args):
        self.fetchval_calls.append((sql, args))
        if self.fetchval_raises is not None:
            raise self.fetchval_raises
        return self.fetchval_returns

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        return "OK"


class _FakeAcquireCM:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_exc):
        return None


class _FakePool:
    def __init__(self, conn=None):
        self.conn = conn or _FakeConn()

    def acquire(self):
        return _FakeAcquireCM(self.conn)


def _cred_status(*, status="active", archived_at=None, config=None, last_error=None):
    now = datetime.now(timezone.utc)
    return CredentialStatus(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        provider="granola",
        config=config if config is not None else {"folder_id": "fol_eq", "folder_name": "EQ"},
        status=status,
        last_polled_at=now,
        last_error=last_error,
        consecutive_failures=0,
        created_at=now,
        updated_at=now,
        archived_at=archived_at,
    )


def _cycle(*, notes_processed=0, deferred_reprocessed=0, credential_error_code=None, outcomes=None):
    return MagicMock(
        notes_processed=notes_processed,
        deferred_reprocessed=deferred_reprocessed,
        credential_error_code=credential_error_code,
        outcomes=outcomes or {},
    )


def _patch_auth_and_pool(*, ctx=None, pool=None):
    """Patch the auth helper + pool getter the handlers call. Returns a
    list of patchers the test enters via contextlib or stacked `with`."""
    ctx = ctx or _ctx()
    pool = pool or _FakePool()
    return (
        patch.object(granola, "get_auth_context_polling", MagicMock(return_value=ctx)),
        patch.object(granola, "get_asyncpg_pool", AsyncMock(return_value=pool)),
    )


# ---------------------------------------------------------------------------
# /validate
# ---------------------------------------------------------------------------


def test_validate_happy_returns_folders(client):
    folders = [
        types.SimpleNamespace(id="fol_eq", name="EQ"),
        types.SimpleNamespace(id="fol_x", name="Other"),
    ]
    fake_client = MagicMock()
    fake_client.list_folders = AsyncMock(return_value=folders)
    fake_client.aclose = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, patch.object(granola, "GranolaAPIClient", MagicMock(return_value=fake_client)):
        resp = client.post(f"{_BASE}/validate", json={"api_key": "grn_test"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["folders"] == [
        {"id": "fol_eq", "name": "EQ"},
        {"id": "fol_x", "name": "Other"},
    ]
    fake_client.aclose.assert_awaited_once()


def test_validate_auth_failed_returns_ok_false(client):
    fake_client = MagicMock()
    fake_client.list_folders = AsyncMock(
        side_effect=GranolaError(GranolaErrorCode.GRANOLA_AUTH_FAILED, "401")
    )
    fake_client.aclose = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, patch.object(granola, "GranolaAPIClient", MagicMock(return_value=fake_client)):
        resp = client.post(f"{_BASE}/validate", json={"api_key": "grn_bad"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "reason": "auth_failed"}
    fake_client.aclose.assert_awaited_once()  # client closed even on error


def test_validate_outage_returns_ok_false(client):
    fake_client = MagicMock()
    fake_client.list_folders = AsyncMock(
        side_effect=GranolaError(GranolaErrorCode.GRANOLA_5XX, "503")
    )
    fake_client.aclose = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, patch.object(granola, "GranolaAPIClient", MagicMock(return_value=fake_client)):
        resp = client.post(f"{_BASE}/validate", json={"api_key": "grn_test"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "reason": "outage"}


def test_validate_rate_limited_maps_reason(client):
    fake_client = MagicMock()
    fake_client.list_folders = AsyncMock(
        side_effect=GranolaError(GranolaErrorCode.GRANOLA_RATE_LIMITED, "429")
    )
    fake_client.aclose = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, patch.object(granola, "GranolaAPIClient", MagicMock(return_value=fake_client)):
        resp = client.post(f"{_BASE}/validate", json={"api_key": "grn_test"})

    assert resp.json() == {"ok": False, "reason": "rate_limited"}


def test_validate_empty_api_key_rejected_422(client):
    """Pydantic min_length=1 rejects an empty key before the handler runs."""
    auth, pool = _patch_auth_and_pool()
    with auth, pool:
        resp = client.post(f"{_BASE}/validate", json={"api_key": ""})
    assert resp.status_code == 422


def test_validate_requires_auth_401(noauth_client, monkeypatch):
    """No bearer token → 401 (route is gated)."""
    monkeypatch.delenv("ALLOW_LEGACY_HEADER_AUTH", raising=False)
    # Do NOT patch get_auth_context_polling — exercise the real gate.
    resp = noauth_client.post(f"{_BASE}/validate", json={"api_key": "grn_test"})
    assert resp.status_code == 401


def test_validate_rejects_legacy_header_request_without_bearer(noauth_client):
    """Even if legacy header auth WOULD resolve, /validate requires a bearer
    token (JWT), so it can't be reached via X-Tenant-ID/X-User-ID headers when
    ALLOW_LEGACY_HEADER_AUTH=true (Codex R4/R6). The bearer gate fires BEFORE
    get_auth_context_polling, so even a patched (would-succeed) auth helper is
    never reached."""
    auth = patch.object(granola, "get_auth_context_polling", MagicMock(return_value=_ctx()))
    with auth:
        resp = noauth_client.post(f"{_BASE}/validate", json={"api_key": "grn_test"})
    assert resp.status_code == 401


def test_validate_accepts_jwt_without_pg_user_id(client):
    """/validate is stateless and must accept a valid JWT that omits the
    optional pg_user_id claim — it must NOT require pg_user_id the way the
    mutation routes do (Codex R5 P1)."""
    folders = [types.SimpleNamespace(id="fol_eq", name="EQ")]
    fake_client = MagicMock()
    fake_client.list_folders = AsyncMock(return_value=folders)
    fake_client.aclose = AsyncMock()

    # JWT present but pg_user_id absent (a valid prod case).
    ctx_no_pg = _ctx(pg_user=None, user_id="auth0|abc")
    auth = patch.object(granola, "get_auth_context_polling", MagicMock(return_value=ctx_no_pg))
    pool = patch.object(granola, "get_asyncpg_pool", AsyncMock(return_value=_FakePool()))
    with auth, pool, patch.object(granola, "GranolaAPIClient", MagicMock(return_value=fake_client)):
        resp = client.post(f"{_BASE}/validate", json={"api_key": "grn_test"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# /connect
# ---------------------------------------------------------------------------


def test_connect_happy_new_stores_and_first_polls(client):
    store = AsyncMock(return_value=uuid4())
    load = AsyncMock(return_value=MagicMock())  # decrypted credential (opaque here)
    cycle = _cycle(notes_processed=2, outcomes={"success": 2})
    run = AsyncMock(return_value=cycle)

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "run_one_cycle", run):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_test", "folder_id": "fol_eq", "folder_name": "EQ"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "connected"
    assert body["first_poll"]["ingested"] == 2
    assert body["first_poll"]["notes_processed"] == 2
    assert body["error_code"] is None

    # store called with the pg_user UUID + config carrying folder_id/name
    store.assert_awaited_once()
    kwargs = store.await_args.kwargs
    assert str(kwargs["tenant_id"]) == _TENANT
    assert str(kwargs["user_id"]) == _PG_USER
    assert kwargs["provider"] == "granola"
    # B1: config is now the folder-LIST shape (mode + import_scope + folders[])
    # with the legacy singular folder_id/folder_name MIRRORED for one release
    # (so the not-yet-updated adapter + any old client keep working).
    assert kwargs["config"]["folders"] == [{"id": "fol_eq", "name": "EQ"}]
    assert kwargs["config"]["mode"] == "folders"
    assert kwargs["config"]["import_scope"] == "history"
    assert kwargs["config"]["folder_id"] == "fol_eq"
    assert kwargs["config"]["folder_name"] == "EQ"
    assert kwargs["caller_module"] == "routers.granola"
    # the test poll ran against the decrypted credential
    run.assert_awaited_once()


def test_connect_reconnect_after_disconnect_uses_reactivate(client):
    """store UNIQUE-violation + an ARCHIVED row exists → reactivate (UPDATE)."""
    store = AsyncMock(side_effect=VaultError(VaultErrorCode.VAULT_DB_INSERT_FAILED, "unique"))
    get_status = AsyncMock(
        return_value=_cred_status(status="archived", archived_at=datetime.now(timezone.utc))
    )
    reactivate = AsyncMock(return_value=uuid4())
    load = AsyncMock(return_value=MagicMock())
    run = AsyncMock(return_value=_cycle(notes_processed=1, outcomes={"success": 1}))

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "reactivate_credential", reactivate), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "run_one_cycle", run):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_new", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "connected"
    reactivate.assert_awaited_once()
    # reactivate is an UPDATE path, not a second INSERT
    assert reactivate.await_args.kwargs["new_api_key"] == "grn_new"


def test_connect_active_same_key_reconfigures_folders(client):
    """C5: ACTIVE row + the SAME key + new folders → in-place folder reconfigure
    (update_credential_config) + save-and-test over the new set — NOT a 409 and
    NOT reactivate_credential (which only handles archived rows)."""
    store = AsyncMock(side_effect=VaultError(VaultErrorCode.VAULT_DB_INSERT_FAILED, "unique"))
    existing = _cred_status(status="active", archived_at=None)
    get_status = AsyncMock(return_value=existing)
    current = MagicMock(api_key="grn_same")          # decrypted stored credential
    load = AsyncMock(return_value=current)
    reconfigure = AsyncMock(return_value=existing.id)
    reactivate = AsyncMock()
    run = AsyncMock(return_value=_cycle(notes_processed=2, outcomes={"success": 2}))

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "update_credential_config", reconfigure), \
         patch.object(granola, "reactivate_credential", reactivate), \
         patch.object(granola, "run_one_cycle", run):
        resp = client.post(
            f"{_BASE}/connect",
            json={
                "api_key": "grn_same",
                "folders": [{"id": "fol_a", "name": "A"}, {"id": "fol_b", "name": "B"}],
            },
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "connected"
    reconfigure.assert_awaited_once()
    cfg = reconfigure.await_args.kwargs["new_config"]
    assert cfg["folders"] == [{"id": "fol_a", "name": "A"}, {"id": "fol_b", "name": "B"}]
    reactivate.assert_not_awaited()   # reconfigure path, NOT reactivate
    run.assert_awaited_once()         # save-and-test ran over the new folder set


def test_connect_active_different_key_returns_409_use_rotate(client):
    """C5: ACTIVE row but the submitted key DIFFERS from the connected one →
    409 'use /rotate' — a key change is /rotate's job; never rotate keys silently
    through /connect. No reconfigure."""
    store = AsyncMock(side_effect=VaultError(VaultErrorCode.VAULT_DB_INSERT_FAILED, "unique"))
    get_status = AsyncMock(return_value=_cred_status(status="active", archived_at=None))
    load = AsyncMock(return_value=MagicMock(api_key="grn_original"))
    reconfigure = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "update_credential_config", reconfigure):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_dup", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 409
    assert "rotate" in resp.json()["detail"].lower()
    reconfigure.assert_not_awaited()


def test_connect_revoked_row_returns_409(client):
    """A revoked (non-active, non-archived) row → 409; reconfigure is only for
    ACTIVE rows. The key never even gets loaded for a non-active row."""
    store = AsyncMock(side_effect=VaultError(VaultErrorCode.VAULT_DB_INSERT_FAILED, "unique"))
    get_status = AsyncMock(return_value=_cred_status(status="revoked", archived_at=None))
    load = AsyncMock()
    reconfigure = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "update_credential_config", reconfigure):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_x", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 409
    assert "already connected" in resp.json()["detail"].lower()
    load.assert_not_awaited()          # non-active row → never loads the key
    reconfigure.assert_not_awaited()


def test_connect_non_uniqueness_insert_failure_returns_502(client):
    """INSERT_FAILED with NO existing row (e.g. a stale pg_user_id failing the
    users FK) must NOT masquerade as a reconnect → 502, not 409/500 (Codex P2)."""
    store = AsyncMock(side_effect=VaultError(VaultErrorCode.VAULT_DB_INSERT_FAILED, "fk violation"))
    get_status = AsyncMock(return_value=None)  # no row → not a uniqueness collision
    reactivate = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "reactivate_credential", reactivate):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_x", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 502
    reactivate.assert_not_awaited()


def test_connect_load_failure_after_store_is_graceful(client):
    """A vault read failure AFTER the credential committed must not 500
    (a retry would hit 409) — report it like a failed first poll (Codex P2)."""
    store = AsyncMock(return_value=uuid4())
    load = AsyncMock(side_effect=VaultError(VaultErrorCode.VAULT_DB_QUERY_FAILED, "db blip"))
    run = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "run_one_cycle", run):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_test", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "connected"
    assert body["error_code"] == "first_poll_failed"
    run.assert_not_awaited()  # never reached the poll


def test_connect_first_poll_auth_failure_reports_revoked(client):
    """First-poll auth failure flips the credential to 'revoked' in the
    adapter; the response must report 'revoked', not 'error' (Codex P3)."""
    store = AsyncMock(return_value=uuid4())
    load = AsyncMock(return_value=MagicMock())
    run = AsyncMock(return_value=_cycle(credential_error_code="granola_auth_failed"))

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "run_one_cycle", run):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_bad", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "revoked"
    assert body["error_code"] == "granola_auth_failed"
    assert body["first_poll"]["errors"] == 1


def test_connect_first_poll_folder_error_reports_error_status(client):
    """A non-auth credential error (folder deleted, sustained outage) →
    status='error' (the adapter marks it 'error', not 'revoked')."""
    store = AsyncMock(return_value=uuid4())
    load = AsyncMock(return_value=MagicMock())
    run = AsyncMock(return_value=_cycle(credential_error_code="granola_folder_not_found"))

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "run_one_cycle", run):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_test", "folder_id": "fol_gone"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "error"
    assert body["error_code"] == "granola_folder_not_found"


def test_connect_first_poll_transient_error_stays_connected(client):
    """A transient first-poll error (429/5xx/timeout) leaves the credential
    'active' in the adapter (retries until the threshold); the connection
    must report 'connected', not a broken 'error' (Codex R3 P2)."""
    store = AsyncMock(return_value=uuid4())
    load = AsyncMock(return_value=MagicMock())
    run = AsyncMock(return_value=_cycle(credential_error_code="granola_5xx"))

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "run_one_cycle", run):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_test", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "connected"  # NOT "error"
    assert body["ok"] is False  # the poll itself didn't fully succeed
    assert body["error_code"] == "granola_5xx"
    assert body["first_poll"]["errors"] == 1


def test_connect_defers_first_poll_when_scheduler_holds_lock(client):
    """If a 5-min scheduler cycle already holds the per-credential advisory
    lock, /connect skips the synchronous poll (no concurrent double-publish)
    and reports the credential connected — the scheduler is already on it."""
    store = AsyncMock(return_value=uuid4())
    load = AsyncMock()
    run = AsyncMock()
    # Lock NOT free: pg_try_advisory_lock returns False.
    pool = _FakePool(_FakeConn(fetchval_returns=False))

    auth, pool_patch = _patch_auth_and_pool(pool=pool)
    with auth, pool_patch, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "run_one_cycle", run):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_test", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "connected"
    assert body["first_poll"]["notes_processed"] == 0
    # Poll skipped: neither the load nor the cycle ran.
    load.assert_not_awaited()
    run.assert_not_awaited()


def test_connect_readback_non_active_reports_real_state(client):
    """If the credential is saved but a prior scheduler cycle flipped it
    non-active, get_granola_credential_for_user (active-only) returns None.
    /connect must report the real state (via get_credential_status), not 500
    a retry into a spurious 409 (Codex R4 P2)."""
    store = AsyncMock(return_value=uuid4())
    load = AsyncMock(return_value=None)  # active-only read finds nothing
    revoked = _cred_status(status="revoked", last_error={"error_code": "granola_auth_failed"})
    get_status = AsyncMock(return_value=revoked)
    run = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "run_one_cycle", run):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_test", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "revoked"
    assert body["ok"] is False
    assert body["error_code"] == "granola_auth_failed"
    run.assert_not_awaited()


def test_connect_lock_setup_failure_is_graceful(client):
    """A transient asyncpg failure acquiring the advisory lock AFTER the
    credential committed must report 'connected, first poll failed', not 500
    (which a retry would turn into a spurious 409) — Codex R5 P2."""
    import asyncpg as _asyncpg

    store = AsyncMock(return_value=uuid4())
    load = AsyncMock()
    run = AsyncMock()
    # pg_try_advisory_lock raises a transient error.
    pool = _FakePool(_FakeConn(fetchval_raises=_asyncpg.PostgresError("pool blip")))

    auth, pool_patch = _patch_auth_and_pool(pool=pool)
    with auth, pool_patch, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "run_one_cycle", run):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_test", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "connected"
    assert body["error_code"] == "first_poll_failed"
    load.assert_not_awaited()
    run.assert_not_awaited()


def test_connect_reconnect_blocked_while_cycle_in_flight(client):
    """Reconnect while a stale scheduler cycle still holds the per-credential
    advisory lock → 409 'sync running'. Reactivating underneath the running
    cycle would let its terminal last_polled_at write-back clobber the reset
    and skip notes in the new folder (Codex R7 P1)."""
    store = AsyncMock(side_effect=VaultError(VaultErrorCode.VAULT_DB_INSERT_FAILED, "unique"))
    get_status = AsyncMock(
        return_value=_cred_status(status="archived", archived_at=datetime.now(timezone.utc))
    )
    reactivate = AsyncMock()
    # Advisory lock held by an in-flight cycle → pg_try_advisory_lock False.
    pool = _FakePool(_FakeConn(fetchval_returns=False))

    auth, pool_patch = _patch_auth_and_pool(pool=pool)
    with auth, pool_patch, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "reactivate_credential", reactivate):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_new", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 409
    assert "currently running" in resp.json()["detail"].lower()
    reactivate.assert_not_awaited()  # never reactivate under a running cycle


def test_connect_concurrent_reconnect_race_returns_409(client):
    """A reconnect double-submit: store UNIQUE-fails, status read sees the
    row archived, but reactivate races a concurrent reconnect that already
    flipped it active → 409, not a 500 (Codex R3 P2)."""
    store = AsyncMock(side_effect=VaultError(VaultErrorCode.VAULT_DB_INSERT_FAILED, "unique"))
    get_status = AsyncMock(
        return_value=_cred_status(status="archived", archived_at=datetime.now(timezone.utc))
    )
    # The other request won the race: reactivate's own check sees it active.
    reactivate = AsyncMock(
        side_effect=VaultError(VaultErrorCode.VAULT_DB_INSERT_FAILED, "is active")
    )

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "reactivate_credential", reactivate):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_new", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 409
    assert "already connected" in resp.json()["detail"].lower()


def test_connect_first_poll_raise_is_graceful(client):
    """If run_one_cycle raises (infra error), the credential is still saved;
    connect returns ok:false + first_poll_failed rather than a 500."""
    store = AsyncMock(return_value=uuid4())
    load = AsyncMock(return_value=MagicMock())
    run = AsyncMock(side_effect=RuntimeError("db blip"))

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "run_one_cycle", run):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_test", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "connected"
    assert body["error_code"] == "first_poll_failed"
    assert body["first_poll"]["errors"] == 1


def test_connect_kms_failure_maps_to_502(client):
    """A non-UNIQUE VaultError (KMS encrypt) → clean 502, not a raw 500."""
    store = AsyncMock(
        side_effect=VaultError(VaultErrorCode.VAULT_KMS_ENCRYPT_FAILED, "kms down")
    )

    auth, pool = _patch_auth_and_pool()
    with auth, pool, patch.object(granola, "store_credential", store):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_test", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 502


def test_connect_non_uuid_pg_user_returns_400(client):
    """A JWT with no UUID-shaped pg_user_id can't own a credential → 400."""
    bad_ctx = _ctx(pg_user=None, user_id="auth0|not-a-uuid")
    auth = patch.object(granola, "get_auth_context_polling", MagicMock(return_value=bad_ctx))
    pool = patch.object(granola, "get_asyncpg_pool", AsyncMock(return_value=_FakePool()))
    with auth, pool:
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_test", "folder_id": "fol_eq"},
        )
    assert resp.status_code == 400


def test_connect_does_not_fall_back_to_auth0_user_id(client):
    """Even when user_id is UUID-shaped, a missing pg_user_id must 400 — we
    never bind a credential to the Auth0 subject (Codex R3 P1)."""
    store = AsyncMock(return_value=uuid4())
    # pg_user_id absent; user_id is a valid UUID string (the dangerous case
    # the old `pg_user_id or user_id` fallback would have silently accepted).
    bad_ctx = _ctx(pg_user=None, user_id=str(uuid4()))
    auth = patch.object(granola, "get_auth_context_polling", MagicMock(return_value=bad_ctx))
    pool = patch.object(granola, "get_asyncpg_pool", AsyncMock(return_value=_FakePool()))
    with auth, pool, patch.object(granola, "store_credential", store):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_test", "folder_id": "fol_eq"},
        )
    assert resp.status_code == 400
    store.assert_not_awaited()  # rejected before any mutation


# ---------------------------------------------------------------------------
# /rotate
# ---------------------------------------------------------------------------


def test_rotate_happy(client):
    status_row = _cred_status(status="active")
    get_status = AsyncMock(return_value=status_row)
    rotate = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "rotate_credential_key", rotate):
        resp = client.post(f"{_BASE}/rotate", json={"new_api_key": "grn_rotated"})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    rotate.assert_awaited_once()
    assert rotate.await_args.kwargs["credential_id"] == status_row.id
    assert rotate.await_args.kwargs["new_api_key"] == "grn_rotated"


def test_rotate_no_credential_returns_404(client):
    get_status = AsyncMock(return_value=None)
    rotate = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "rotate_credential_key", rotate):
        resp = client.post(f"{_BASE}/rotate", json={"new_api_key": "grn_x"})

    assert resp.status_code == 404
    rotate.assert_not_awaited()


def test_rotate_archived_credential_returns_404(client):
    """An archived (disconnected) credential is a reconnect, not a rotate."""
    get_status = AsyncMock(
        return_value=_cred_status(status="archived", archived_at=datetime.now(timezone.utc))
    )
    rotate = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "rotate_credential_key", rotate):
        resp = client.post(f"{_BASE}/rotate", json={"new_api_key": "grn_x"})

    assert resp.status_code == 404
    rotate.assert_not_awaited()


def test_http_from_vault_error_maps_audit_failure_to_503():
    """A vault audit-log write failure is transient (write txn rolled back) →
    503, not a generic 500 (Codex R6 P2)."""
    http = granola._http_from_vault_error(
        VaultError(VaultErrorCode.VAULT_AUDIT_LOG_WRITE_FAILED, "audit down")
    )
    assert http.status_code == 503
    # The query-failed code keeps mapping to 503 too.
    http2 = granola._http_from_vault_error(
        VaultError(VaultErrorCode.VAULT_DB_QUERY_FAILED, "db down")
    )
    assert http2.status_code == 503


def test_rotate_blocked_while_cycle_in_flight(client):
    """Rotate while a scheduler cycle holds the per-credential advisory lock →
    409. Rotating underneath a stale cycle (polling the old key) would let that
    cycle write revoked/error back over the fresh status='active' (Codex R8 P1)."""
    get_status = AsyncMock(return_value=_cred_status(status="revoked"))
    rotate = AsyncMock()
    pool = _FakePool(_FakeConn(fetchval_returns=False))  # lock held by a cycle

    auth, pool_patch = _patch_auth_and_pool(pool=pool)
    with auth, pool_patch, \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "rotate_credential_key", rotate):
        resp = client.post(f"{_BASE}/rotate", json={"new_api_key": "grn_rotated"})

    assert resp.status_code == 409
    assert "currently running" in resp.json()["detail"].lower()
    rotate.assert_not_awaited()


def test_rotate_status_read_failure_maps_to_503(client):
    """A transient vault read failure on the status lookup → 503 (retryable),
    not a generic 500 (Codex P2 consistency)."""
    get_status = AsyncMock(
        side_effect=VaultError(VaultErrorCode.VAULT_DB_QUERY_FAILED, "db down")
    )
    rotate = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "rotate_credential_key", rotate):
        resp = client.post(f"{_BASE}/rotate", json={"new_api_key": "grn_x"})

    assert resp.status_code == 503
    rotate.assert_not_awaited()


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------


def test_status_connected_with_activity(client):
    status_row = _cred_status(status="active")
    activity_rows = [
        {"status": "success", "n": 3},
        {"status": "deferred_pending_account", "n": 1},
        {"status": "failed", "n": 2},
        {"status": "failed_permanent", "n": 1},
    ]
    pool = _FakePool(_FakeConn(fetch_returns=activity_rows))
    get_status = AsyncMock(return_value=status_row)

    auth, pool_patch = _patch_auth_and_pool(pool=pool)
    with auth, pool_patch, patch.object(granola, "get_credential_status", get_status):
        resp = client.get(f"{_BASE}/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert body["status"] == "active"
    assert body["activity"] == {"ingested_7d": 3, "deferred_7d": 1, "errors_7d": 3}
    # B1: array-shaped `folders[]` + `mode` (was singular `folder`). Here the
    # stored config is the LEGACY singular shape, so /status must synthesize the
    # one-element folders list from folder_id/folder_name (back-compat read).
    assert body["mode"] == "folders"
    assert body["folders"] == [{"id": "fol_eq", "name": "EQ", "status": "ok"}]
    # Expand-contract: the legacy singular `folder` (= folders[0]) is ALSO
    # returned during the expand window so a pre-B1 /status reader still works.
    assert body["folder"] == {"id": "fol_eq", "name": "EQ"}


def test_status_not_connected_when_no_row(client):
    get_status = AsyncMock(return_value=None)

    auth, pool = _patch_auth_and_pool()
    with auth, pool, patch.object(granola, "get_credential_status", get_status):
        resp = client.get(f"{_BASE}/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    assert body["status"] == "none"
    assert body["activity"] == {"ingested_7d": 0, "deferred_7d": 0, "errors_7d": 0}
    assert body["folders"] == []
    assert body["folder"] is None  # legacy singular mirror, null when disconnected


def test_status_not_connected_when_archived(client):
    get_status = AsyncMock(
        return_value=_cred_status(status="archived", archived_at=datetime.now(timezone.utc))
    )

    auth, pool = _patch_auth_and_pool()
    with auth, pool, patch.object(granola, "get_credential_status", get_status):
        resp = client.get(f"{_BASE}/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    assert body["status"] == "archived"


def test_status_revoked_is_connected_but_flagged(client):
    """A revoked credential still 'exists' (connected=True) so the UI can
    render a reconnect banner; the real state is in `status`."""
    status_row = _cred_status(status="revoked")
    pool = _FakePool(_FakeConn(fetch_returns=[]))
    get_status = AsyncMock(return_value=status_row)

    auth, pool_patch = _patch_auth_and_pool(pool=pool)
    with auth, pool_patch, patch.object(granola, "get_credential_status", get_status):
        resp = client.get(f"{_BASE}/status")

    body = resp.json()
    assert body["connected"] is True
    assert body["status"] == "revoked"


def test_status_activity_db_failure_maps_to_503(client):
    """A transient failure in the 7-day activity rollup → 503, not 500
    (Codex R2 P2)."""
    import asyncpg as _asyncpg

    class _RaisingConn:
        async def fetch(self, *_a):
            raise _asyncpg.PostgresError("db down")

    pool = _FakePool(_RaisingConn())
    get_status = AsyncMock(return_value=_cred_status(status="active"))

    auth, pool_patch = _patch_auth_and_pool(pool=pool)
    with auth, pool_patch, patch.object(granola, "get_credential_status", get_status):
        resp = client.get(f"{_BASE}/status")

    assert resp.status_code == 503


def test_activity_sql_filters_updated_at_not_created_at():
    """external_integration_runs is UPSERTed in place, so the 7-day window
    must key off updated_at (last status change), not created_at (Codex R2 P2)."""
    sql = granola._ACTIVITY_COUNTS_7D_SQL
    assert "updated_at >= NOW()" in sql
    assert "created_at >=" not in sql


# ---------------------------------------------------------------------------
# /disconnect
# ---------------------------------------------------------------------------


def test_disconnect_soft_deletes(client):
    status_row = _cred_status(status="active")
    get_status = AsyncMock(return_value=status_row)
    archive = AsyncMock(return_value=True)

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "archive_credential", archive):
        resp = client.delete(_BASE)

    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "disconnected"}
    archive.assert_awaited_once()
    assert archive.await_args.kwargs["credential_id"] == status_row.id


def test_disconnect_idempotent_when_no_row(client):
    get_status = AsyncMock(return_value=None)
    archive = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "archive_credential", archive):
        resp = client.delete(_BASE)

    assert resp.status_code == 200
    assert resp.json()["status"] == "disconnected"
    archive.assert_not_awaited()  # nothing to archive


def test_disconnect_idempotent_when_already_archived(client):
    get_status = AsyncMock(
        return_value=_cred_status(status="archived", archived_at=datetime.now(timezone.utc))
    )
    archive = AsyncMock()

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "archive_credential", archive):
        resp = client.delete(_BASE)

    assert resp.status_code == 200
    archive.assert_not_awaited()


def test_disconnect_archive_failure_maps_to_http(client):
    """A transient vault failure on the soft-delete → clean retryable HTTP,
    not a generic 500 (Codex R2 P2)."""
    get_status = AsyncMock(return_value=_cred_status(status="active"))
    archive = AsyncMock(
        side_effect=VaultError(VaultErrorCode.VAULT_DB_QUERY_FAILED, "db down")
    )

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "get_credential_status", get_status), \
         patch.object(granola, "archive_credential", archive):
        resp = client.delete(_BASE)

    assert resp.status_code == 503


def test_disconnect_requires_auth_401(noauth_client, monkeypatch):
    monkeypatch.delenv("ALLOW_LEGACY_HEADER_AUTH", raising=False)
    resp = noauth_client.delete(_BASE)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# B1 (EQ-91) — folder-LIST data model + array-shaped /connect & /status
# ---------------------------------------------------------------------------


def test_connect_request_accepts_folders_array():
    body = granola.ConnectRequest(
        api_key="grn_x", folders=[{"id": "fol_a", "name": "A"}], mode="folders"
    )
    assert [f.id for f in body.folders] == ["fol_a"]
    assert body.import_scope == "history"  # D6 default


def test_connect_request_back_compat_singular_folder_id():
    # a legacy client sends folder_id/folder_name; normalize into folders[0]
    body = granola.ConnectRequest(api_key="grn_x", folder_id="fol_a", folder_name="A")
    assert body.normalized_folders() == [{"id": "fol_a", "name": "A"}]


def test_connect_request_mode_all_accepted_at_model_level():
    # the contract field accepts "all" (watch everything); the ENDPOINT gates it
    # until the multi-folder loop ships (see the rejection test below).
    body = granola.ConnectRequest(api_key="grn_x", mode="all", folders=[])
    assert body.mode == "all"
    assert body.normalized_folders() == []


def test_connect_request_mode_folders_requires_a_folder():
    with pytest.raises(ValidationError):
        granola.ConnectRequest(api_key="grn_x", mode="folders", folders=[])


def test_connect_request_config_has_folders_and_legacy_mirror():
    body = granola.ConnectRequest(
        api_key="grn_x",
        folders=[{"id": "fol_a", "name": "A"}, {"id": "fol_b", "name": "B"}],
        import_scope="forward",
    )
    cfg = body.config()
    assert cfg["mode"] == "folders"
    assert cfg["import_scope"] == "forward"
    assert cfg["folders"] == [{"id": "fol_a", "name": "A"}, {"id": "fol_b", "name": "B"}]
    # legacy singular mirror (folders[0]) preserved for one release
    assert cfg["folder_id"] == "fol_a"
    assert cfg["folder_name"] == "A"


def test_connect_accepts_folders_array_and_stores_list_config(client):
    store = AsyncMock(return_value=uuid4())
    load = AsyncMock(return_value=MagicMock())
    run = AsyncMock(return_value=_cycle(notes_processed=1, outcomes={"success": 1}))

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "get_granola_credential_for_user", load), \
         patch.object(granola, "run_one_cycle", run):
        resp = client.post(
            f"{_BASE}/connect",
            json={
                "api_key": "grn_test",
                "mode": "folders",
                "folders": [{"id": "fol_a", "name": "A"}, {"id": "fol_b", "name": "B"}],
                "import_scope": "history",
            },
        )

    assert resp.status_code == 200
    cfg = store.await_args.kwargs["config"]
    assert cfg["folders"] == [{"id": "fol_a", "name": "A"}, {"id": "fol_b", "name": "B"}]
    assert cfg["mode"] == "folders"
    assert cfg["import_scope"] == "history"
    assert cfg["folder_id"] == "fol_a"  # legacy mirror = folders[0]


def test_connect_mode_all_rejected_until_loop_ships(client):
    """C10: B1 accepts mode='all' in the *contract*, but the synchronous
    save-&-test can't safely backfill 'everything' (Railway ~5-min cap) and the
    multi-folder loop lands in B2 — so the endpoint rejects mode='all' (4xx)
    WITHOUT storing, which also prevents a folder_id="" → 400 to Granola."""
    store = AsyncMock(return_value=uuid4())

    auth, pool = _patch_auth_and_pool()
    with auth, pool, patch.object(granola, "store_credential", store):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_x", "mode": "all", "folders": []},
        )

    assert resp.status_code == 400
    store.assert_not_awaited()


def test_status_returns_folders_array_from_new_config(client):
    # config carries the NEW folders[] list → /status returns it array-shaped,
    # each folder defaulting to status "ok" (per-folder error fills in B2 / C6).
    status_row = _cred_status(
        status="active",
        config={
            "mode": "folders",
            "import_scope": "history",
            "folders": [{"id": "fol_a", "name": "A"}, {"id": "fol_b", "name": "B"}],
            "folder_id": "fol_a",
            "folder_name": "A",
        },
    )
    pool = _FakePool(_FakeConn(fetch_returns=[]))
    get_status = AsyncMock(return_value=status_row)

    auth, pool_patch = _patch_auth_and_pool(pool=pool)
    with auth, pool_patch, patch.object(granola, "get_credential_status", get_status):
        resp = client.get(f"{_BASE}/status")

    body = resp.json()
    assert body["mode"] == "folders"
    assert body["folders"] == [
        {"id": "fol_a", "name": "A", "status": "ok"},
        {"id": "fol_b", "name": "B", "status": "ok"},
    ]
    # legacy singular mirror = folders[0] during the expand window
    assert body["folder"] == {"id": "fol_a", "name": "A"}


def test_status_surfaces_persisted_per_folder_status(client):
    """B2/C6: /status surfaces config.folders[].status — a folder the poll cycle
    marked not_found shows through, not a default 'ok' (the per-folder status is
    persisted in config and survives the cycle-success update)."""
    status_row = _cred_status(
        status="active",
        config={
            "mode": "folders",
            "import_scope": "history",
            "folders": [
                {"id": "fol_a", "name": "A", "status": "ok"},
                {"id": "fol_b", "name": "B", "status": "not_found"},
            ],
        },
    )
    pool = _FakePool(_FakeConn(fetch_returns=[]))
    get_status = AsyncMock(return_value=status_row)

    auth, pool_patch = _patch_auth_and_pool(pool=pool)
    with auth, pool_patch, patch.object(granola, "get_credential_status", get_status):
        resp = client.get(f"{_BASE}/status")

    body = resp.json()
    assert body["folders"] == [
        {"id": "fol_a", "name": "A", "status": "ok"},
        {"id": "fol_b", "name": "B", "status": "not_found"},
    ]
