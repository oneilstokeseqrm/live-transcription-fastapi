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


def _make_jwt(
    user_id: str = OWNER_USER_ID,
    tenant_id: str = TENANT_ID,
    pg_user_id: str | None = None,
) -> str:
    """Build a signed internal JWT.

    Codex Round 2 P1 #1: production JWTs carry a `pg_user_id` UUID claim
    when the identity bridge has resolved the Auth0 subject to a Postgres
    User UUID. Queue rows are inserted with
    `owner_user_id = context.pg_user_id or context.user_id`, so the auth
    boundary on the route side must compare against the SAME effective id.
    """
    now = int(time.time())
    payload = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "iss": os.environ["INTERNAL_JWT_ISSUER"],
        "aud": os.environ["INTERNAL_JWT_AUDIENCE"],
        "iat": now,
        "exp": now + 300,
    }
    if pg_user_id is not None:
        payload["pg_user_id"] = pg_user_id
    return pyjwt.encode(payload, os.environ["INTERNAL_JWT_SECRET"], algorithm="HS256")


def _fake_queue_row(
    *,
    queue_id: str,
    tenant_id: str = TENANT_ID,
    owner_user_id: str = OWNER_USER_ID,
    status: str = "pending",
    approval_attempt_id: str | None = None,
    resolved_account_id: str | None = None,
    archived_at=None,
) -> MagicMock:
    """Build a mock SQLAlchemy row matching SELECT_QUEUE_SQL's projection."""
    row = MagicMock()
    row.id = queue_id
    row.tenant_id = tenant_id
    row.owner_user_id = owner_user_id
    row.status = status
    row.approval_attempt_id = approval_attempt_id
    row.resolved_account_id = resolved_account_id
    row.archived_at = archived_at
    # SQLAlchemy rows support _mapping for dict-style access.
    row._mapping = {
        "id": queue_id,
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "status": status,
        "approval_attempt_id": approval_attempt_id,
        "resolved_account_id": resolved_account_id,
        "archived_at": archived_at,
    }
    return row


def _fake_account_row(account_id: str) -> MagicMock:
    """Build a mock row for SELECT_ACCOUNT_FOR_TENANT_SQL — only needs .id."""
    row = MagicMock()
    row.id = account_id
    row._mapping = {"id": account_id}
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
    """Owner maps to an existing account → 200 and materialize_account_approval called.

    Execute sequence (post-Codex Round 1 fixes):
      1. SELECT_QUEUE_SQL (_load_and_authorize)
      2. SELECT_ACCOUNT_FOR_TENANT_SQL (P1 #1 tenant scope check)
      3. MAP_RESERVE_SQL (P2 #3 idempotency reservation)
    Materialization is then called.
    """
    queue_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(queue_id=queue_id)),
        _execute_result(one_or_none=_fake_account_row(account_id)),
        _execute_result(one_or_none=MagicMock(id=queue_id), rowcount=1),
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

    # P1 #1: the SELECT_ACCOUNT call carried tenant_id=ctx.tenant_id
    account_lookup_params = session.calls[1][1]
    assert account_lookup_params["account_id"] == account_id
    assert account_lookup_params["tenant_id"] == TENANT_ID

    # P2 #3: the MAP_RESERVE call carried attempt_id
    reserve_params = session.calls[2][1]
    assert reserve_params["queue_id"] == queue_id
    assert reserve_params["attempt_id"] == attempt_id


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
# Codex Round 1 fixes
# ---------------------------------------------------------------------------


# P1 #1 — /map must verify account belongs to current tenant

def test_map_cross_tenant_account_returns_404(client: TestClient):
    """Owner is in-tenant, queue entry is in-tenant, but account_id belongs to
    a DIFFERENT tenant → 404 (don't leak that the account exists elsewhere)
    and materialize is NOT called."""
    queue_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())

    # SELECT_QUEUE_SQL returns the in-tenant row.
    # SELECT_ACCOUNT_FOR_TENANT_SQL returns None (account is in another tenant).
    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(queue_id=queue_id)),
        _execute_result(one_or_none=None),
    ])

    materialize_mock = AsyncMock()
    with _patch_session(session), \
         patch("routers.queue_actions.materialize_account_approval", new=materialize_mock):
        response = client.post(
            f"/queue/{queue_id}/map",
            json={
                "account_id": account_id,
                "approval_attempt_id": str(uuid.uuid4()),
            },
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 404, response.text
    materialize_mock.assert_not_awaited()
    # The account lookup must have been bound with the caller's tenant_id.
    account_lookup_params = session.calls[1][1]
    assert account_lookup_params["account_id"] == account_id
    assert account_lookup_params["tenant_id"] == TENANT_ID


def test_map_nonexistent_account_returns_404(client: TestClient):
    """Account UUID does not exist in any tenant → 404, materialize NOT called."""
    queue_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(queue_id=queue_id)),
        _execute_result(one_or_none=None),  # account doesn't exist
    ])

    materialize_mock = AsyncMock()
    with _patch_session(session), \
         patch("routers.queue_actions.materialize_account_approval", new=materialize_mock):
        response = client.post(
            f"/queue/{queue_id}/map",
            json={
                "account_id": account_id,
                "approval_attempt_id": str(uuid.uuid4()),
            },
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 404, response.text
    materialize_mock.assert_not_awaited()


# P1 #2 — Actions on archived/ignored queue entries must reject (or be idempotent for /ignore)

import datetime as _dt


def test_approve_on_archived_returns_409(client: TestClient):
    """/approve on a row whose archived_at IS NOT NULL → 409, UPDATE not run."""
    queue_id = str(uuid.uuid4())
    archived_at = _dt.datetime(2026, 5, 14, 12, 0, 0, tzinfo=_dt.timezone.utc)

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            status="ignored",
            archived_at=archived_at,
        )),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/approve",
            json={"approval_attempt_id": str(uuid.uuid4())},
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 409, response.text
    # Only the initial SELECT ran — no UPDATE attempt.
    assert len(session.calls) == 1


def test_map_on_archived_returns_409(client: TestClient):
    """/map on a row whose archived_at IS NOT NULL → 409, materialize not run."""
    queue_id = str(uuid.uuid4())
    archived_at = _dt.datetime(2026, 5, 14, 12, 0, 0, tzinfo=_dt.timezone.utc)

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            status="ignored",
            archived_at=archived_at,
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
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 409, response.text
    materialize_mock.assert_not_awaited()
    # Only the initial SELECT ran.
    assert len(session.calls) == 1


def test_ignore_on_already_archived_returns_200_idempotent(client: TestClient):
    """/ignore on an already-ignored+archived row → 200, IGNORE_SQL NOT run again.

    Idempotent replay: status=='ignored' and archived_at IS NOT NULL → noop
    early-return with the same response shape. Re-running IGNORE_SQL would
    re-stamp ignored_at, which is wrong.

    Codex Round 2 P1 #2: caller must carry a UUID-shaped user id.
    """
    queue_id = str(uuid.uuid4())
    archived_at = _dt.datetime(2026, 5, 14, 12, 0, 0, tzinfo=_dt.timezone.utc)

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="ignored",
            archived_at=archived_at,
        )),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ignored"
    assert body["queue_id"] == queue_id
    # IGNORE_SQL was NOT re-executed (would re-stamp ignored_at).
    assert len(session.calls) == 1


# P2 #3 — /map honors approval_attempt_id idempotency


def test_map_idempotent_replay_returns_200_without_re_materializing(client: TestClient):
    """Replay /map with SAME attempt_id on an already-mapped row → 200,
    materialize NOT called a second time.

    First call materialized the queue (status='mapped',
    resolved_account_id=account_id, approval_attempt_id=attempt_id).
    Second call: SELECT_QUEUE shows mapped, SELECT_ACCOUNT confirms tenant,
    MAP_RESERVE_SQL noops (UPDATE WHERE matches NULL OR same attempt_id with
    archived_at IS NULL — but status='mapped' means... actually status='mapped'
    sets archived_at — see below).

    The reservation SQL has `AND archived_at IS NULL`. A 'mapped' status
    typically leaves archived_at NULL (only /ignore archives). The reservation
    update with same attempt_id will match → returns 1 row → materialize is
    still called. But this is fine because materialize itself is idempotent
    via UPSERT_PLACEHOLDER_SUMMARY / contact UPSERT / link UPSERT.

    Wait — let's re-read the spec: "200 (materialize NOT called second time —
    caller has already materialized once and we now read status='mapped')".
    So MAP_RESERVE should NOT match on a row with status='mapped' so that we
    can re-SELECT and detect the replay-success case.

    Per the route handler: when MAP_RESERVE returns 0 rows, we re-SELECT.
    If status=='mapped' and resolved_account_id==account_id → return 200
    without materializing.

    To make MAP_RESERVE return 0 rows for an already-mapped replay, the
    materialization side effect (UPDATE_QUEUE_SQL in materialize_account_approval)
    must clear approval_attempt_id OR the test SQL stub returns no row. The
    simplest design: MAP_RESERVE only matches rows that are still
    materializable (status != 'mapped'). The spec's SQL doesn't filter on
    status, but in test we drive it: we make MAP_RESERVE return None on the
    replay (simulating UPDATE matching 0 rows). Then route re-SELECTs and
    sees status='mapped' + same resolved_account_id → returns 200.
    """
    queue_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        # SELECT_QUEUE_SQL — row is already mapped
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            status="mapped",
            approval_attempt_id=attempt_id,
            resolved_account_id=account_id,
        )),
        # SELECT_ACCOUNT_FOR_TENANT_SQL — account exists in tenant
        _execute_result(one_or_none=_fake_account_row(account_id)),
        # MAP_RESERVE_SQL — 0 rows because materialize sets status='mapped'
        # in real DB. We simulate that by returning None.
        _execute_result(one_or_none=None),
        # Re-SELECT_QUEUE to discriminate
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            status="mapped",
            approval_attempt_id=attempt_id,
            resolved_account_id=account_id,
        )),
    ])

    materialize_mock = AsyncMock()
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
    # CRITICAL: materialize NOT called on replay.
    materialize_mock.assert_not_awaited()


def test_map_different_attempt_id_returns_409(client: TestClient):
    """Replay /map with a DIFFERENT attempt_id on an already-mapped row → 409.

    Different attempt_id signals a different client intent (not a retry of
    the same logical action). The reservation SQL won't match (existing
    attempt_id != requested). Re-SELECT shows mapped row with different
    attempt_id → 409.
    """
    queue_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    original_attempt = str(uuid.uuid4())
    new_attempt = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        # SELECT_QUEUE: row is mapped with the ORIGINAL attempt_id
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            status="mapped",
            approval_attempt_id=original_attempt,
            resolved_account_id=account_id,
        )),
        # SELECT_ACCOUNT: account is in-tenant
        _execute_result(one_or_none=_fake_account_row(account_id)),
        # MAP_RESERVE: 0 rows (different attempt_id)
        _execute_result(one_or_none=None),
        # Re-SELECT: status='mapped' but mismatches our intent
        # (different attempt_id stored) → handler may discriminate by
        # comparing the stored attempt_id OR by detecting account mismatch.
        # The route's check is account match for replay-success; with same
        # account_id but DIFFERENT attempt_id, this still counts as a
        # different intent → 409.
        # To make this unambiguous we simulate the case where a different
        # account_id has been mapped.
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            status="mapped",
            approval_attempt_id=original_attempt,
            resolved_account_id=str(uuid.uuid4()),  # different account
        )),
    ])

    materialize_mock = AsyncMock()
    with _patch_session(session), \
         patch("routers.queue_actions.materialize_account_approval", new=materialize_mock):
        response = client.post(
            f"/queue/{queue_id}/map",
            json={"account_id": account_id, "approval_attempt_id": new_attempt},
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 409, response.text
    materialize_mock.assert_not_awaited()


# P2 #4 — UUID validators at the API boundary


def test_approve_invalid_uuid_attempt_id_returns_422(client: TestClient):
    """Malformed UUID in approval_attempt_id → 422 from Pydantic, never reaches SQL."""
    queue_id = str(uuid.uuid4())

    # No session calls expected — request rejected before route handler runs.
    response = client.post(
        f"/queue/{queue_id}/approve",
        json={"approval_attempt_id": "not-a-uuid"},
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )

    assert response.status_code == 422, response.text


def test_map_invalid_uuid_account_id_returns_422(client: TestClient):
    """Malformed UUID in account_id → 422 from Pydantic."""
    queue_id = str(uuid.uuid4())

    response = client.post(
        f"/queue/{queue_id}/map",
        json={
            "account_id": "not-a-uuid",
            "approval_attempt_id": str(uuid.uuid4()),
        },
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )

    assert response.status_code == 422, response.text


def test_map_invalid_uuid_attempt_id_returns_422(client: TestClient):
    """Malformed UUID in approval_attempt_id on /map → 422 from Pydantic."""
    queue_id = str(uuid.uuid4())

    response = client.post(
        f"/queue/{queue_id}/map",
        json={
            "account_id": str(uuid.uuid4()),
            "approval_attempt_id": "still-not-a-uuid",
        },
        headers={"Authorization": f"Bearer {_make_jwt()}"},
    )

    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# IGNORE route
# ---------------------------------------------------------------------------


def test_ignore_happy_path(client: TestClient):
    """Owner ignores → 200, UPDATE sets archived_at + ignored_by.

    Codex Round 2 P1 #2 — /ignore now requires a UUID-shaped user
    identifier (pg_user_id), reflecting the production insert pattern
    where owner_user_id stores a UUID. This test uses the production
    shape: JWT carries pg_user_id; queue row owner_user_id is that UUID.

    Codex Round 3 P2 #2 — IGNORE_SQL now uses RETURNING; the handler reads
    .one_or_none() to detect 0-row noops. The success path returns the
    queue id row.
    """
    queue_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id, owner_user_id=PG_USER_UUID,
        )),
        # Codex Round 3: IGNORE_SQL returns id::text via RETURNING; handler
        # consumes .one_or_none().
        _execute_result(one_or_none=MagicMock(id=queue_id), rowcount=1),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ignored"
    assert body["queue_id"] == queue_id

    # UPDATE bound params include user_id (for ignored_by) as the UUID
    update_params = session.calls[1][1]
    assert update_params["queue_id"] == queue_id
    assert update_params["user_id"] == PG_USER_UUID


def test_ignore_non_owner_returns_403(client: TestClient):
    """Non-owner with valid pg_user_id → 403.

    Both the caller and the queue row carry UUID-shaped owner_user_ids
    (production pattern). They simply don't match → 403.
    """
    queue_id = str(uuid.uuid4())
    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id, owner_user_id=PG_USER_UUID,
        )),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=OTHER_PG_USER_UUID)}",
            },
        )

    assert response.status_code == 403, response.text


def test_ignore_nonexistent_returns_404(client: TestClient):
    """Non-existent queue_id → 404 (still requires UUID-shaped user id)."""
    queue_id = str(uuid.uuid4())
    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=None),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# Codex Round 2 fixes
# ---------------------------------------------------------------------------

# Production-shape user identity:
# Routers/text.py:101 + routers/batch.py:164 insert queue rows with
# `owner_user_id = context.pg_user_id or context.user_id`. The owner_user_id
# column is UUID NOT NULL in eq-dev. When pg_user_id is present (the
# standard case), the column stores the UUID, NOT the Auth0 subject string.
PG_USER_UUID = "33333333-3333-4333-8333-333333333333"
OTHER_PG_USER_UUID = "44444444-4444-4444-8444-444444444444"


# P1 #1 — Auth check must use _effective_user_id (pg_user_id or user_id)

def test_approve_owner_with_pg_user_id_returns_200(client: TestClient):
    """JWT carries pg_user_id (UUID); queue row owner_user_id is that UUID.

    Pre-fix: route compared ctx.user_id (Auth0 subject string) against
    row.owner_user_id (UUID) → never matched → 403. Post-fix:
    `_effective_user_id(ctx)` returns ctx.pg_user_id (UUID) → matches.
    """
    queue_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())

    # owner_user_id matches the pg_user_id on the JWT (production pattern).
    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
        )),
        _execute_result(one_or_none=MagicMock(id=queue_id), rowcount=1),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/approve",
            json={"approval_attempt_id": attempt_id},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 200, response.text


def test_map_owner_with_pg_user_id_returns_200(client: TestClient):
    """Same as approve test but for /map — owner is the pg_user_id UUID."""
    queue_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
        )),
        _execute_result(one_or_none=_fake_account_row(account_id)),
        _execute_result(one_or_none=MagicMock(id=queue_id), rowcount=1),
    ])

    materialize_mock = AsyncMock(return_value=None)
    with _patch_session(session), \
         patch("routers.queue_actions.materialize_account_approval", new=materialize_mock):
        response = client.post(
            f"/queue/{queue_id}/map",
            json={"account_id": account_id, "approval_attempt_id": attempt_id},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 200, response.text
    materialize_mock.assert_awaited_once()


def test_ignore_owner_with_pg_user_id_returns_200(client: TestClient):
    """Same as approve test but for /ignore — owner is the pg_user_id UUID.

    Combined with P1 #2: the IGNORE_SQL `:user_id::uuid` cast must receive
    a UUID. Post-fix, the route passes _effective_user_id(ctx) which is
    the pg_user_id UUID here → cast succeeds.

    Codex Round 3 P2 #2 — IGNORE_SQL has RETURNING; handler reads .one_or_none().
    """
    queue_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
        )),
        _execute_result(one_or_none=MagicMock(id=queue_id), rowcount=1),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 200, response.text
    # IGNORE_SQL bound :user_id to the pg_user_id UUID, not the Auth0 subject.
    update_params = session.calls[1][1]
    assert update_params["user_id"] == PG_USER_UUID


def test_approve_pg_user_id_mismatch_returns_403(client: TestClient):
    """JWT carries pg_user_id different from owner_user_id → still 403.

    This guards against the bug going in the opposite direction: making
    the auth boundary too permissive. Effective-user-id = pg_user_id; if
    that doesn't match owner_user_id, deny.
    """
    queue_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
        )),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/approve",
            json={"approval_attempt_id": str(uuid.uuid4())},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=OTHER_PG_USER_UUID)}",
            },
        )

    assert response.status_code == 403, response.text


# P1 #2 — /ignore must reject non-UUID user identifiers cleanly (400 not 500)

def test_ignore_without_pg_user_id_and_non_uuid_user_id_returns_400(client: TestClient):
    """JWT lacks pg_user_id AND user_id is Auth0 subject (non-UUID) → 400.

    Pre-fix: the route would pass an Auth0 subject string to
    `:user_id::uuid` → Postgres cast error → 500.

    The defensive 400 also signals "this caller could not have created
    this queue row in the first place" (queue inserts use
    `pg_user_id or user_id` and the column is UUID NOT NULL — so an
    Auth0-subject-only caller would never have an owner_user_id row
    they could legitimately ignore).
    """
    queue_id = str(uuid.uuid4())

    # JWT has only the Auth0 subject "auth0|owner-queue-1" — non-UUID.
    # We don't even need to set up a session because the 400 check fires
    # before the DB transaction opens. But to be robust to ordering, we
    # provide a session that would otherwise succeed.
    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(queue_id=queue_id)),
        _execute_result(rowcount=1),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={"Authorization": f"Bearer {_make_jwt()}"},
        )

    assert response.status_code == 400, response.text
    assert "UUID" in response.json()["detail"]


# P1 #3 — /approve must NOT mutate an already-mapped row

def test_approve_does_not_mutate_mapped_row(client: TestClient):
    """Row is status='mapped' with same approval_attempt_id → 200 replay-success,
    but UPDATE returns 0 rows (status filter in WHERE excludes 'mapped').

    Without the fix, the row would be flipped back to 'approved', clearing
    its mapped status and inviting the worker to re-materialize → duplicate
    materialization + duplicate outbox event.
    """
    queue_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        # SELECT shows row is already mapped
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="mapped",
            approval_attempt_id=attempt_id,
            resolved_account_id=str(uuid.uuid4()),
        )),
        # APPROVE_SQL must NOT match (status='mapped' excluded by filter)
        _execute_result(one_or_none=None, rowcount=0),
        # Re-SELECT to discriminate — sees mapped → handler returns 200
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="mapped",
            approval_attempt_id=attempt_id,
        )),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/approve",
            json={"approval_attempt_id": attempt_id},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "approved"
    # All three calls were made: SELECT, UPDATE (noop), re-SELECT.
    assert len(session.calls) == 3


def test_approve_sql_filters_archived_and_terminal_status():
    """APPROVE_SQL's WHERE clause must include archived_at IS NULL AND
    status IN ('pending', 'approved').

    A direct contract test on the SQL text so future edits can't silently
    weaken the filter.
    """
    from routers.queue_actions import APPROVE_SQL
    sql_text = str(APPROVE_SQL)
    assert "archived_at IS NULL" in sql_text
    assert "status IN ('pending', 'approved')" in sql_text


# P1 #4 — /map replays must NOT re-materialize

def test_map_sql_filters_mapped_creating_ignored():
    """MAP_RESERVE_SQL must exclude rows in terminal/in-flight states.

    Pre-fix Round 2: a same-attempt_id replay on a 'mapped' row would
    re-reserve → handler would call materialize_account_approval again →
    duplicate outbox + duplicate interaction_contact_links.

    Codex Round 3 P1 #1: tightened further from a negative list to the
    positive list `status = 'pending'` so /map does not race the worker
    on status='approved' rows. The pending-only filter still excludes
    ('mapped', 'creating', 'ignored') by construction.
    """
    from routers.queue_actions import MAP_RESERVE_SQL
    sql_text = str(MAP_RESERVE_SQL)
    assert "archived_at IS NULL" in sql_text
    # Round 3 narrowed this from `status NOT IN (...)` to `status = 'pending'`.
    assert "status = 'pending'" in sql_text


def test_map_replay_with_same_attempt_id_skips_materialization(client: TestClient):
    """Same-attempt_id replay on already-mapped row → 200, materialize NOT called.

    Stronger version of test_map_idempotent_replay_returns_200_without_re_materializing
    that specifically exercises the case where attempt_id matches AND
    resolved_account_id matches AND status='mapped' — the MAP_RESERVE
    filter must return 0 rows so the handler falls into the replay-success
    branch.
    """
    queue_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="mapped",
            approval_attempt_id=attempt_id,
            resolved_account_id=account_id,
        )),
        _execute_result(one_or_none=_fake_account_row(account_id)),
        # MAP_RESERVE returns 0 — status filter excludes 'mapped'
        _execute_result(one_or_none=None),
        # Re-SELECT shows mapped with same account → replay-success
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="mapped",
            approval_attempt_id=attempt_id,
            resolved_account_id=account_id,
        )),
    ])

    materialize_mock = AsyncMock()
    with _patch_session(session), \
         patch("routers.queue_actions.materialize_account_approval", new=materialize_mock):
        response = client.post(
            f"/queue/{queue_id}/map",
            json={"account_id": account_id, "approval_attempt_id": attempt_id},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "mapped"
    assert body["account_id"] == account_id
    # CRITICAL: materialize NOT called on replay.
    materialize_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Codex Round 3 fixes
# ---------------------------------------------------------------------------

# P1 #1 — /map must NOT race the worker on status='approved' rows.
#
# Pre-fix, MAP_RESERVE_SQL only excluded ('mapped', 'creating', 'ignored') —
# so a row in status='approved' (worker-owned via advisory lock) was still
# reservable by /map. The route does NOT take the advisory lock, so /map +
# worker could both call materialize_account_approval on the same row →
# duplicate outbox rows + duplicate links.
#
# Fix: restrict MAP_RESERVE_SQL to status='pending'. Approved rows are
# worker-owned and terminal-from-/map's-perspective; mapped rows are
# terminal.


def test_map_sql_restricts_to_pending_only():
    """MAP_RESERVE_SQL must filter status='pending' (not status NOT IN ...).

    Pre-fix the negative filter allowed 'approved', 'tenant_review',
    'failed' to pass. Post-fix only 'pending' rows can be /map-reserved
    so the worker (which processes status='approved') is the sole owner
    of post-approval materialization.
    """
    from routers.queue_actions import MAP_RESERVE_SQL
    sql_text = str(MAP_RESERVE_SQL)
    assert "archived_at IS NULL" in sql_text
    assert "status = 'pending'" in sql_text
    # Negative-list filter must be gone — replace with positive list.
    assert "status NOT IN" not in sql_text


def test_map_on_approved_row_returns_409_does_not_race_worker(client: TestClient):
    """Round 3 P1 #1: /map on a row in status='approved' → 409, materialize NOT called.

    The worker (workers/account_provisioning_worker.process_one_approved_entry)
    takes an advisory lock and materializes approved rows. /map does NOT
    take that lock. Allowing /map on status='approved' would race the worker
    on the same row → duplicate outbox + duplicate links + possibly different
    resolved accounts.

    Fix: MAP_RESERVE_SQL filters status='pending'. The UPDATE noops on an
    approved row → re-SELECT shows status='approved' (not replay-success because
    status != 'mapped') → 409 Conflict.
    """
    queue_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    existing_attempt = str(uuid.uuid4())
    new_attempt = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        # SELECT_QUEUE: row is APPROVED (worker territory)
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="approved",
            approval_attempt_id=existing_attempt,
        )),
        # SELECT_ACCOUNT: account exists in tenant
        _execute_result(one_or_none=_fake_account_row(account_id)),
        # MAP_RESERVE: 0 rows — status filter excludes 'approved'
        _execute_result(one_or_none=None),
        # Re-SELECT: still 'approved' (not 'mapped'), so NOT replay-success → 409
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="approved",
            approval_attempt_id=existing_attempt,
        )),
    ])

    materialize_mock = AsyncMock()
    with _patch_session(session), \
         patch("routers.queue_actions.materialize_account_approval", new=materialize_mock):
        response = client.post(
            f"/queue/{queue_id}/map",
            json={"account_id": account_id, "approval_attempt_id": new_attempt},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 409, response.text
    materialize_mock.assert_not_awaited()
    # The error message names the conflicting status for operator clarity.
    assert "approved" in response.json()["detail"].lower()


# P2 #2 — /ignore must NOT overwrite successfully-mapped or in-flight rows.


def test_ignore_sql_filters_terminal_status():
    """IGNORE_SQL must exclude ('mapped', 'creating', 'ignored') and have RETURNING.

    Pre-fix IGNORE_SQL had no status filter, so a stale or crafted POST
    /queue/{id}/ignore after /map would overwrite status='mapped' to 'ignored'.
    The contacts + outbox rows from materialization stay; the queue state
    lies.
    """
    from routers.queue_actions import IGNORE_SQL
    sql_text = str(IGNORE_SQL)
    assert "status NOT IN ('mapped', 'creating', 'ignored')" in sql_text
    # RETURNING required so the handler can detect 0-row noops.
    assert "RETURNING" in sql_text


def test_ignore_on_mapped_row_returns_409(client: TestClient):
    """Round 3 P2 #2: /ignore on status='mapped' → 409, NOT 200.

    Pre-fix the UPDATE silently flipped status to 'ignored' while the
    contacts + outbox rows stayed materialized — queue state lies.
    Post-fix IGNORE_SQL noops (RETURNING 0 rows) → handler re-SELECTs →
    sees status='mapped' → 409.
    """
    queue_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        # SELECT_QUEUE (_load_and_authorize, actionable_only=False so mapped passes)
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="mapped",
            resolved_account_id=str(uuid.uuid4()),
        )),
        # IGNORE_SQL — 0 rows (status filter excludes 'mapped')
        _execute_result(one_or_none=None),
        # Re-SELECT to discriminate — still 'mapped' → 409
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="mapped",
            resolved_account_id=str(uuid.uuid4()),
        )),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 409, response.text
    assert "mapped" in response.json()["detail"].lower()


def test_ignore_on_creating_row_returns_409(client: TestClient):
    """Round 3 P2 #2: /ignore on status='creating' (worker in flight) → 409.

    The worker's atomic materialization is in-flight; ignoring would lie
    about the queue state once the worker commits status='mapped'.
    """
    queue_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="creating",
        )),
        # IGNORE_SQL — 0 rows
        _execute_result(one_or_none=None),
        # Re-SELECT — still 'creating'
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="creating",
        )),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 409, response.text
    assert "creating" in response.json()["detail"].lower()


def test_ignore_on_pending_row_still_succeeds(client: TestClient):
    """Round 3 P2 #2 regression: the new status filter must NOT break the
    happy-path /ignore on a status='pending' row."""
    queue_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="pending",
        )),
        # IGNORE_SQL — 1 row (RETURNING)
        _execute_result(one_or_none=MagicMock(id=queue_id), rowcount=1),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ignored"
    assert body["queue_id"] == queue_id


# Existing test_ignore_on_already_archived_returns_200_idempotent at line 629
# already covers /ignore on status='ignored' → 200 idempotent (the
# short-circuit fires before IGNORE_SQL runs).


# ---------------------------------------------------------------------------
# Codex Round 4 fixes
# ---------------------------------------------------------------------------

# P2 #2 — /ignore must NOT overwrite rows archived by other flows.
#
# Pre-fix IGNORE_SQL had no `AND archived_at IS NULL` clause, so a row
# archived by the (forthcoming) expiry sweeper — `archived_at != NULL` but
# status still 'pending' — would silently get overwritten by /ignore:
# archive_reason flipped from the sweeper's reason to 'owner_ignored',
# ignored_at/ignored_by stamped over the sweeper's archive_at. The contract
# is that /ignore is a no-op on any already-archived row regardless of who
# archived it; only pending non-archived rows should mutate.


def test_ignore_sql_filters_already_archived():
    """Codex Round 4 P2 #2: IGNORE_SQL must filter `archived_at IS NULL` so
    /ignore cannot overwrite a row that another flow (e.g. expiry sweeper,
    a prior /ignore) has already archived.
    """
    from routers.queue_actions import IGNORE_SQL
    sql_text = str(IGNORE_SQL)
    assert "archived_at IS NULL" in sql_text
    # Existing Round 3 filter remains.
    assert "status NOT IN ('mapped', 'creating', 'ignored')" in sql_text


def test_ignore_on_sweeper_archived_pending_returns_200_no_overwrite(client: TestClient):
    """Round 4 P2 #2: a row in status='pending' with archived_at non-NULL
    (archived by the sweeper, not by /ignore) → IGNORE_SQL noops, handler
    returns 200 idempotent without re-archiving.

    Pre-fix IGNORE_SQL had no archived_at filter, so it would mutate the
    sweeper-archived row, overwriting its archive_reason with 'owner_ignored'.
    Post-fix IGNORE_SQL returns 0 rows; the handler's 0-row branch sees
    archived_at != NULL and returns 200 without further mutation.
    """
    queue_id = str(uuid.uuid4())
    archived_at = _dt.datetime(2026, 5, 14, 12, 0, 0, tzinfo=_dt.timezone.utc)

    session = _SessionStub(execute_results=[
        # SELECT_QUEUE: row is pending but already archived by the sweeper.
        # The /ignore short-circuit only fires when status='ignored', so
        # this falls through to IGNORE_SQL.
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="pending",
            archived_at=archived_at,
        )),
        # IGNORE_SQL: 0 rows (the new `AND archived_at IS NULL` filter
        # blocks the mutation).
        _execute_result(one_or_none=None),
        # Re-SELECT: row is still pending+archived; the handler must see
        # archived_at != None and return 200 without 409.
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="pending",
            archived_at=archived_at,
        )),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ignored"
    assert body["queue_id"] == queue_id

    # CRITICAL: IGNORE_SQL ran (it's the 2nd execute call) but returned 0
    # rows — the row WAS NOT mutated. Assert IGNORE_SQL was attempted (we
    # have to know the filter actually fired, not that the route
    # short-circuited earlier) and that no further write SQL ran.
    from routers.queue_actions import IGNORE_SQL, SELECT_QUEUE_SQL
    executed = [stmt for stmt, _ in session.calls]
    assert SELECT_QUEUE_SQL in executed
    assert IGNORE_SQL in executed
    # Exactly three execute calls: SELECT (auth), IGNORE_SQL (0 rows),
    # SELECT (discrimination). No second IGNORE_SQL, no other UPDATE.
    assert len(session.calls) == 3
    assert executed.count(IGNORE_SQL) == 1


def test_ignore_on_pending_non_archived_still_succeeds(client: TestClient):
    """Round 4 P2 #2 regression: the new `archived_at IS NULL` filter must
    NOT break the happy-path /ignore on a normal pending row (status='pending',
    archived_at=NULL → IGNORE_SQL matches → archives normally).
    """
    queue_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="pending",
            archived_at=None,
        )),
        # IGNORE_SQL: 1 row (filter matches because archived_at IS NULL
        # AND status NOT IN terminal).
        _execute_result(one_or_none=MagicMock(id=queue_id), rowcount=1),
    ])

    with _patch_session(session):
        response = client.post(
            f"/queue/{queue_id}/ignore",
            json={},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ignored"
    # Only 2 calls: SELECT (auth) + IGNORE_SQL (success). No re-SELECT
    # needed because IGNORE_SQL returned a row.
    assert len(session.calls) == 2


# P2 #3 — /map replay-success must require matching approval_attempt_id.
#
# Pre-fix the replay-success branch was:
#   if current.status == "mapped" and current.resolved_account_id == body.account_id:
#       return 200
# This let Bob retry Alice's earlier-failed /map request with a different
# attempt_id but the same account_id and incorrectly get 200. Per the
# contract, only the SAME attempt_id should get 200; a different attempt_id
# on an already-mapped row is a different intent → 409.


def test_map_replay_with_same_account_different_attempt_id_returns_409(client: TestClient):
    """Round 4 P2 #3: /map on an already-mapped row with the SAME
    resolved_account_id but a DIFFERENT approval_attempt_id → 409.

    Scenario: Alice maps queue_id Q with attempt_id=a1 and account=X.
    Materialization commits, queue row becomes status='mapped',
    resolved_account_id=X, approval_attempt_id=a1.

    Bob (no knowledge of Alice's a1) retries the earlier failed /map with
    his own attempt_id=b2 and account=X. The replay-success branch must
    detect that the recorded attempt_id (a1) differs from Bob's request
    (b2) and 409 — NOT silently return 200 as if Bob's call drove the
    map.

    Without this, callers can falsely believe their attempt_id won the
    race when in fact someone else's attempt_id is the audit record.
    """
    queue_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    alice_attempt = str(uuid.uuid4())
    bob_attempt = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        # SELECT_QUEUE: row is mapped to account_id with Alice's attempt_id.
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="mapped",
            approval_attempt_id=alice_attempt,
            resolved_account_id=account_id,
        )),
        # SELECT_ACCOUNT: account in tenant.
        _execute_result(one_or_none=_fake_account_row(account_id)),
        # MAP_RESERVE: 0 rows (status='mapped' excluded by filter).
        _execute_result(one_or_none=None),
        # Re-SELECT: same row, same account, but Alice's attempt_id (not Bob's).
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="mapped",
            approval_attempt_id=alice_attempt,  # NOT Bob's attempt
            resolved_account_id=account_id,
        )),
    ])

    materialize_mock = AsyncMock()
    with _patch_session(session), \
         patch("routers.queue_actions.materialize_account_approval", new=materialize_mock):
        response = client.post(
            f"/queue/{queue_id}/map",
            json={
                "account_id": account_id,
                "approval_attempt_id": bob_attempt,  # Bob's, NOT Alice's
            },
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 409, response.text
    materialize_mock.assert_not_awaited()
    # The error message should signal a different attempt_id was recorded.
    detail = response.json()["detail"].lower()
    assert "attempt" in detail or "conflict" in detail


def test_map_replay_with_same_account_same_attempt_id_returns_200(client: TestClient):
    """Round 4 P2 #3 regression: the new attempt_id check must NOT break
    the legitimate same-attempt-same-account replay-success case.

    Scenario: Alice's /map call materialized but the response was lost
    in flight. Alice retries with the SAME attempt_id + SAME account.
    The row reads status='mapped' with Alice's attempt_id → 200, no
    re-materialization.
    """
    queue_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())

    session = _SessionStub(execute_results=[
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="mapped",
            approval_attempt_id=attempt_id,
            resolved_account_id=account_id,
        )),
        _execute_result(one_or_none=_fake_account_row(account_id)),
        # MAP_RESERVE: 0 rows (status='mapped' excluded).
        _execute_result(one_or_none=None),
        # Re-SELECT: same row, same account, SAME attempt_id → replay-success.
        _execute_result(one_or_none=_fake_queue_row(
            queue_id=queue_id,
            owner_user_id=PG_USER_UUID,
            status="mapped",
            approval_attempt_id=attempt_id,
            resolved_account_id=account_id,
        )),
    ])

    materialize_mock = AsyncMock()
    with _patch_session(session), \
         patch("routers.queue_actions.materialize_account_approval", new=materialize_mock):
        response = client.post(
            f"/queue/{queue_id}/map",
            json={"account_id": account_id, "approval_attempt_id": attempt_id},
            headers={
                "Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "mapped"
    assert body["account_id"] == account_id
    materialize_mock.assert_not_awaited()
