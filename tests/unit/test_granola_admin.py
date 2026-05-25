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
    app = FastAPI()
    app.include_router(granola.router)
    return TestClient(app)


def _ctx(*, tenant=_TENANT, pg_user=_PG_USER, user_id="auth0|abc", trace=_TRACE):
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
    def __init__(self, fetch_returns=None):
        self.fetch_returns = fetch_returns or []
        self.fetch_calls = []

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return list(self.fetch_returns)


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


def _cred_status(*, status="active", archived_at=None, config=None):
    now = datetime.now(timezone.utc)
    return CredentialStatus(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        provider="granola",
        config=config if config is not None else {"folder_id": "fol_eq", "folder_name": "EQ"},
        status=status,
        last_polled_at=now,
        last_error=None,
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


def test_validate_requires_auth_401(client, monkeypatch):
    """No JWT + legacy header auth disabled → 401 (route is gated)."""
    monkeypatch.delenv("ALLOW_LEGACY_HEADER_AUTH", raising=False)
    # Do NOT patch get_auth_context_polling — exercise the real gate.
    resp = client.post(f"{_BASE}/validate", json={"api_key": "grn_test"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /connect
# ---------------------------------------------------------------------------


def test_connect_happy_new_stores_and_first_polls(client):
    store = AsyncMock()
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
    assert kwargs["config"] == {"folder_id": "fol_eq", "folder_name": "EQ"}
    assert kwargs["caller_module"] == "routers.granola"
    # the test poll ran against the decrypted credential
    run.assert_awaited_once()


def test_connect_reconnect_after_disconnect_uses_reactivate(client):
    """store raises UNIQUE-violation (row exists incl. archived) → reactivate."""
    store = AsyncMock(side_effect=VaultError(VaultErrorCode.VAULT_DB_INSERT_FAILED, "unique"))
    reactivate = AsyncMock(return_value=uuid4())
    load = AsyncMock(return_value=MagicMock())
    run = AsyncMock(return_value=_cycle(notes_processed=1, outcomes={"success": 1}))

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
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


def test_connect_already_active_returns_409(client):
    """store UNIQUE-violation → reactivate reports row is ACTIVE → 409."""
    store = AsyncMock(side_effect=VaultError(VaultErrorCode.VAULT_DB_INSERT_FAILED, "unique"))
    reactivate = AsyncMock(
        side_effect=VaultError(VaultErrorCode.VAULT_DB_INSERT_FAILED, "is active")
    )

    auth, pool = _patch_auth_and_pool()
    with auth, pool, \
         patch.object(granola, "store_credential", store), \
         patch.object(granola, "reactivate_credential", reactivate):
        resp = client.post(
            f"{_BASE}/connect",
            json={"api_key": "grn_dup", "folder_id": "fol_eq"},
        )

    assert resp.status_code == 409
    assert "already connected" in resp.json()["detail"].lower()


def test_connect_first_poll_credential_error_reflected(client):
    """A bad key that slipped past validate fails inside the first poll →
    run_one_cycle returns credential_error_code → status='error', ok=false."""
    store = AsyncMock()
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
    assert body["status"] == "error"
    assert body["error_code"] == "granola_auth_failed"
    assert body["first_poll"]["errors"] == 1


def test_connect_first_poll_raise_is_graceful(client):
    """If run_one_cycle raises (infra error), the credential is still saved;
    connect returns ok:false + first_poll_failed rather than a 500."""
    store = AsyncMock()
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
    assert body["folder"] is None


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


def test_disconnect_requires_auth_401(client, monkeypatch):
    monkeypatch.delenv("ALLOW_LEGACY_HEADER_AUTH", raising=False)
    resp = client.delete(_BASE)
    assert resp.status_code == 401
