"""HTTP-level tests for the queue action routes (Task 1.5.11).

Three routes — `POST /queue/{id}/approve`, `POST /queue/{id}/map`, and
`POST /queue/{id}/ignore` — drive the human-approval surface of Phase 1.5.
This test module pins the contract end-to-end at the FastAPI surface:

- 401 when the JWT is missing or malformed.
- 404 when the entry does not exist OR belongs to another tenant
  (existence is NOT leaked across tenants).
- 403 when the entry exists in the caller's tenant but the caller is not
  the owner (and admin escalation does not apply).
- 200 + correct SQL emission on the owner happy paths.
- Idempotency: replaying APPROVE with the same `approval_attempt_id`
  returns 200 both times. A different `approval_attempt_id` on an
  already-approved row returns 200 (entry IS approved; client can move on)
  rather than 409, per the contract documented in the handoff.

Mock-driven (matches `test_materialization.py` and `test_upload_participants.py`).
No DB required; the tests pass regardless of migration state.

End-to-end verification against a real Neon DB happens via the production
E2E suite (`/tmp/e2e_phase_1_production.py`) and a future Task 1.5.18
end-to-end Approve flow test.
"""
from __future__ import annotations

import os
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

# JWT env must be set BEFORE importing main / app.
os.environ.setdefault("INTERNAL_JWT_SECRET", "test-secret-that-is-at-least-32-characters-long")
os.environ.setdefault("INTERNAL_JWT_ISSUER", "eq-frontend")
os.environ.setdefault("INTERNAL_JWT_AUDIENCE", "eq-backend")


TENANT_ID = "11111111-1111-4111-8111-111111111111"
OWNER_USER_ID = "auth0|owner-queue-1"
OTHER_USER_ID = "auth0|other-user-2"


def _make_jwt(user_id: str = OWNER_USER_ID, tenant_id: str = TENANT_ID) -> str:
    now = int(time.time())
    payload = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "iss": os.environ["INTERNAL_JWT_ISSUER"],
        "aud": os.environ["INTERNAL_JWT_AUDIENCE"],
        "iat": now,
        "exp": now + 300,
    }
    return pyjwt.encode(payload, os.environ["INTERNAL_JWT_SECRET"], algorithm="HS256")


def _fake_queue_row(
    *,
    queue_id: str,
    tenant_id: str = TENANT_ID,
    owner_user_id: str = OWNER_USER_ID,
    status: str = "pending",
    approval_attempt_id: str | None = None,
    resolved_account_id: str | None = None,
) -> MagicMock:
    """Build a mock SQLAlchemy row matching SELECT_QUEUE_SQL's projection."""
    row = MagicMock()
    row.id = queue_id
    row.tenant_id = tenant_id
    row.owner_user_id = owner_user_id
    row.status = status
    row.approval_attempt_id = approval_attempt_id
    row.resolved_account_id = resolved_account_id
    # SQLAlchemy rows support _mapping for dict-style access.
    row._mapping = {
        "id": queue_id,
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "status": status,
        "approval_attempt_id": approval_attempt_id,
        "resolved_account_id": resolved_account_id,
    }
    return row


def _execute_result(*, one_or_none=None, scalar_one=None, rowcount=0, all_rows=None):
    """Build a mock SQLAlchemy result object covering the access patterns
    the queue route handlers need (.one_or_none, .scalar_one, .rowcount)."""
    result = MagicMock()
    result.one_or_none = MagicMock(return_value=one_or_none)
    if scalar_one is not None:
        result.scalar_one = MagicMock(return_value=scalar_one)
    result.rowcount = rowcount
    if all_rows is not None:
        result.all = MagicMock(return_value=all_rows)
    return result


class _SessionStub:
    """A drop-in async-session stub for the queue routes.

    The route handlers call:
      - `await session.execute(stmt, params)` (multiple times)
      - `async with session.begin(): ...`

    The stub records every execute() call in `self.calls` so tests can
    assert on SQL emission and parameter shapes.
    """

    def __init__(self, execute_results: list):
        self._results = list(execute_results)
        self.calls: list = []
        self.execute = AsyncMock(side_effect=self._side_effect)
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def _side_effect(self, stmt, params=None):
        self.calls.append((stmt, params))
        if not self._results:
            raise AssertionError(
                f"_SessionStub ran out of results; unexpected execute call "
                f"#{len(self.calls)}"
            )
        return self._results.pop(0)

    def begin(self):
        outer = self

        class _BeginCM:
            async def __aenter__(self_inner):
                return outer

            async def __aexit__(self_inner, exc_type, exc, tb):
                # Rethrow any exception; commit-on-clean-exit is handled
                # implicitly by SQLAlchemy in real usage, and our tests do
                # not assert on commit/rollback here (the materialization
                # tests already cover transaction discipline).
                return False

        return _BeginCM()


class _SessionCM:
    """Async context manager wrapping a single _SessionStub."""

    def __init__(self, session: _SessionStub):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _patch_session(session: _SessionStub):
    """Patch `get_async_session` in the queue_actions module to yield our stub."""
    return patch(
        "routers.queue_actions.get_async_session",
        new=lambda: _SessionCM(session),
    )


@pytest.fixture
def client() -> TestClient:
    # Import here so JWT env is in place first.
    from main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Auth-boundary tests (apply to all three routes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route,body", [
    ("approve", {"approval_attempt_id": str(uuid.uuid4())}),
    ("map", {"account_id": str(uuid.uuid4()), "approval_attempt_id": str(uuid.uuid4())}),
    ("ignore", {}),
])
def test_route_rejects_missing_jwt(client: TestClient, route: str, body: dict):
    """No Authorization header → 401."""
    queue_id = str(uuid.uuid4())
    response = client.post(f"/queue/{queue_id}/{route}", json=body)
    assert response.status_code == 401, response.text


@pytest.mark.parametrize("route,body", [
    ("approve", {"approval_attempt_id": str(uuid.uuid4())}),
    ("map", {"account_id": str(uuid.uuid4()), "approval_attempt_id": str(uuid.uuid4())}),
    ("ignore", {}),
])
def test_route_rejects_bad_jwt(client: TestClient, route: str, body: dict):
    """Garbage Authorization header → 401."""
    queue_id = str(uuid.uuid4())
    response = client.post(
        f"/queue/{queue_id}/{route}",
        json=body,
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert response.status_code == 401, response.text


# ---------------------------------------------------------------------------
# APPROVE route
# ---------------------------------------------------------------------------


def test_approve_happy_path(client: TestClient):
    """Owner approves with a valid attempt_id → 200 and SQL emission."""
    queue_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())

    # SELECT (returns the entry) → UPDATE (RETURNING 1 row, success)
    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(queue_id=queue_id)),
        _execute_result(one_or_none=MagicMock(id=queue_id), rowcount=1),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/approve",
            json={"approval_attempt_id": attempt_id},
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "approved"
    assert body["queue_id"] == queue_id

    # Two SQL calls: SELECT then UPDATE
    assert len(session.calls) == 2
    # UPDATE bound params include attempt_id
    update_params = session.calls[1][1]
    assert update_params["queue_id"] == queue_id
    assert update_params["attempt_id"] == attempt_id


def test_approve_idempotent_replay_returns_200(client: TestClient):
    """Replay with the same attempt_id → 200 (UPDATE noops because WHERE matches).

    The SQL pattern `WHERE approval_attempt_id IS NULL OR = :attempt_id`
    matches on the second call (same attempt_id), so RETURNING fires again
    and the handler returns 200 cleanly.
    """
    queue_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        # First call: SELECT shows entry already approved with this attempt
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            status="approved",
            approval_attempt_id=attempt_id,
        )),
        # UPDATE still matches (same attempt_id) and returns the row
        _execute_result(one_or_none=MagicMock(id=queue_id), rowcount=1),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/approve",
            json={"approval_attempt_id": attempt_id},
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 200, response.text


def test_approve_different_attempt_id_on_already_approved_returns_200(client: TestClient):
    """Different attempt_id on an already-approved entry → 200 (entry IS approved).

    Contract documented in the Phase 1.5 handoff: the client cares that
    the row IS approved, not that THIS call did the approval. Returning
    409 here would push idempotency handling onto every caller.
    """
    queue_id = str(uuid.uuid4())
    original_attempt = str(uuid.uuid4())
    new_attempt = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            status="approved",
            approval_attempt_id=original_attempt,
        )),
        # UPDATE noops (different attempt_id, WHERE fails) → 0 rows
        _execute_result(one_or_none=None, rowcount=0),
        # Re-SELECT to discriminate: row IS approved → return 200
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            status="approved",
            approval_attempt_id=original_attempt,
        )),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/approve",
            json={"approval_attempt_id": new_attempt},
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 200, response.text


def test_approve_non_owner_returns_403(client: TestClient):
    """Caller is in-tenant but not the owner → 403."""
    queue_id = str(uuid.uuid4())
    session = _SessionStub(execute_results=[
        # Entry exists, owner is someone else
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=OWNER_USER_ID,
        )),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/approve",
            json={"approval_attempt_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {_make_jwt(user_id=OTHER_USER_ID)}"},
        )

    assert response.status_code == 403, response.text


def test_approve_cross_tenant_returns_404(client: TestClient):
    """Entry exists but belongs to another tenant → 404 (no leak of existence)."""
    queue_id = str(uuid.uuid4())
    other_tenant = "22222222-2222-4222-8222-222222222222"

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id, tenant_id=other_tenant,
        )),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/approve",
            json={"approval_attempt_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 404, response.text


def test_approve_nonexistent_returns_404(client: TestClient):
    """Entry does not exist anywhere → 404."""
    queue_id = str(uuid.uuid4())
    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=None),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/approve",
            json={"approval_attempt_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# MAP route
# ---------------------------------------------------------------------------


def test_map_happy_path(client: TestClient):
    """Owner maps to an existing account → 200 and materialize_account_approval called."""
    queue_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())

    # Only the SELECT call is in the session.execute results; the
    # materialize call is patched separately at the function boundary.
    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(queue_id=queue_id)),
    ])

    materialize_mock = AsyncMock(return_value=None)
    with _patch_session(session), \
         patch("routers.queue_actions.materialize_account_approval", new=materialize_mock):
        response = client.post(
            f"/queue/{queue_id}/map",
            json={"account_id": account_id, "approval_attempt_id": attempt_id},
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "mapped"
    assert body["queue_id"] == queue_id
    assert body["account_id"] == account_id

    materialize_mock.assert_awaited_once()
    assert materialize_mock.await_args is not None
    call_kwargs = materialize_mock.await_args.kwargs
    assert call_kwargs["tenant_id"] == TENANT_ID
    assert call_kwargs["queue_id"] == queue_id
    assert call_kwargs["account_id"] == account_id
    assert call_kwargs["event_type"] == "account_mapped"


def test_map_non_owner_returns_403(client: TestClient):
    """Non-owner cannot map → 403, materialize NOT called."""
    queue_id = str(uuid.uuid4())
    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id, owner_user_id=OWNER_USER_ID,
        )),
    ])

    materialize_mock = AsyncMock()
    with _patch_session(session), \
         patch("routers.queue_actions.materialize_account_approval", new=materialize_mock):
        response = client.post(
            f"/queue/{queue_id}/map",
            json={
                "account_id": str(uuid.uuid4()),
                "approval_attempt_id": str(uuid.uuid4()),
            },
            headers={"Authorization": f"Bearer {_make_jwt(user_id=OTHER_USER_ID)}"},
        )

    assert response.status_code == 403, response.text
    materialize_mock.assert_not_awaited()


def test_map_cross_tenant_returns_404(client: TestClient):
    queue_id = str(uuid.uuid4())
    other_tenant = "22222222-2222-4222-8222-222222222222"
    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id, tenant_id=other_tenant,
        )),
    ])

    with _patch_session(session), \
         patch("routers.queue_actions.materialize_account_approval", new=AsyncMock()):
        response = client.post(
            f"/queue/{queue_id}/map",
            json={
                "account_id": str(uuid.uuid4()),
                "approval_attempt_id": str(uuid.uuid4()),
            },
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# IGNORE route
# ---------------------------------------------------------------------------


def test_ignore_happy_path(client: TestClient):
    """Owner ignores → 200, UPDATE sets archived_at + ignored_by."""
    queue_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(queue_id=queue_id)),
        _execute_result(rowcount=1),  # UPDATE succeeded
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ignored"
    assert body["queue_id"] == queue_id

    # UPDATE bound params include user_id (for ignored_by)
    update_params = session.calls[1][1]
    assert update_params["queue_id"] == queue_id
    assert update_params["user_id"] == OWNER_USER_ID


def test_ignore_non_owner_returns_403(client: TestClient):
    queue_id = str(uuid.uuid4())
    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id, owner_user_id=OWNER_USER_ID,
        )),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={"Authorization": f"Bearer {_make_jwt(user_id=OTHER_USER_ID)}"},
        )

    assert response.status_code == 403, response.text


def test_ignore_nonexistent_returns_404(client: TestClient):
    queue_id = str(uuid.uuid4())
    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=None),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 404, response.text
