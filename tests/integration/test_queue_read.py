"""HTTP-level tests for the queue READ routes (Task A1, EQ-95).

Tests the owner-scoped GET /queue/count endpoint and the JWT-only
read-auth guard (_require_owner_identity). Mock-driven, no DB required.
Mirrors the harness in test_queue_lifecycle.py.
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


def _execute_result(*, one_or_none=None, scalar_one=None, rowcount=0, all_rows=None):
    """Build a mock SQLAlchemy result object covering the access patterns
    the queue route handlers need (.one_or_none, .scalar_one, .rowcount).

    Carry-forward A1 review (Important): when scalar_one / all_rows are NOT
    passed, accessing .scalar_one() / .all() raises AssertionError instead of
    returning a truthy MagicMock — prevents silent false-passes where the test
    never exercises the real return value.
    """
    result = MagicMock()
    result.one_or_none = MagicMock(return_value=one_or_none)
    if scalar_one is not None:
        result.scalar_one = MagicMock(return_value=scalar_one)
    else:
        result.scalar_one = MagicMock(
            side_effect=AssertionError(
                "_execute_result: scalar_one not configured for this result — "
                "pass scalar_one=<value> or the test is exercising an unexpected path"
            )
        )
    result.rowcount = rowcount
    if all_rows is not None:
        result.all = MagicMock(return_value=all_rows)
    else:
        result.all = MagicMock(
            side_effect=AssertionError(
                "_execute_result: all_rows not configured for this result — "
                "pass all_rows=<list> or the test is exercising an unexpected path"
            )
        )
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
# Task A1: GET /queue/count tests
# ---------------------------------------------------------------------------

PG_USER_UUID = "33333333-3333-4333-8333-333333333333"


def test_count_rejects_missing_jwt(client):
    """No Authorization header → 401."""
    r = client.get("/queue/count")
    assert r.status_code == 401, r.text


def test_count_requires_pg_user_id(client):
    """JWT without pg_user_id → 403 (JWT-only / unambiguous owner)."""
    token = _make_jwt(pg_user_id=None)  # mints tenant_id + user_id only
    r = client.get("/queue/count", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403, r.text


def test_count_happy_path(client):
    session = _SessionStub(execute_results=[_execute_result(scalar_one=7)])
    with _patch_session(session):
        r = client.get(
            "/queue/count",
            headers={"Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}"},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"count": 7}
    # owner + tenant + pending filter must be bound
    params = session.calls[0][1]
    assert params["owner_user_id"] == PG_USER_UUID


def test_count_sql_is_owner_scoped_and_pending():
    """Contract: the count SQL must filter owner, tenant, archived, pending."""
    from routers.queue_actions import COUNT_QUEUE_SQL
    sql = str(COUNT_QUEUE_SQL)
    assert "owner_user_id = CAST(:owner_user_id AS uuid)" in sql
    assert "tenant_id = CAST(:tenant_id AS uuid)" in sql
    assert "archived_at IS NULL" in sql
    assert "status = 'pending'" in sql


def test_count_rejects_legacy_header_auth(client, monkeypatch):
    """Even with legacy header auth ENABLED, spoofed X-* headers (no JWT) are denied.
    Legacy auth never populates pg_user_id, so the owner-identity guard rejects it —
    this pins the no-RLS read surface to JWT-only."""
    monkeypatch.setenv("ALLOW_LEGACY_HEADER_AUTH", "true")
    r = client.get("/queue/count", headers={
        "X-Tenant-ID": "11111111-1111-4111-8111-111111111111",
        "X-User-ID": "b0000000-0000-4000-8000-000000000002",
    })
    # 403 is the owner-guard path (legacy ctx has no pg_user_id). If the legacy
    # header set is incomplete it may 400/401 — either way, spoofed headers grant
    # NO access. The security property under test is "not 200".
    assert r.status_code in (400, 401, 403), r.text


# Carry-forward A1 review item 2: non-UUID pg_user_id → 403 from
# _require_owner_identity's uuid.UUID branch. _make_jwt can mint arbitrary
# pg_user_id values so this is straightforward.
def test_count_rejects_non_uuid_pg_user_id(client):
    """JWT with a non-UUID pg_user_id claim → 403 from _require_owner_identity."""
    token = _make_jwt(pg_user_id="not-a-uuid")
    r = client.get("/queue/count", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Task A2: GET /queue/{queue_id} tests
# ---------------------------------------------------------------------------


def test_status_rejects_bad_uuid(client):
    token = _make_jwt(pg_user_id=PG_USER_UUID)
    r = client.get("/queue/not-a-uuid", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400, r.text


def test_status_not_found_returns_404(client):
    session = _SessionStub(execute_results=[_execute_result(one_or_none=None)])
    qid = str(uuid.uuid4())
    with _patch_session(session):
        r = client.get(
            f"/queue/{qid}",
            headers={"Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}"},
        )
    assert r.status_code == 404, r.text


def test_status_happy_path_pending(client):
    qid = str(uuid.uuid4())
    row = MagicMock(queue_id=qid, domain="acme.com", status="pending",
                    resolved_account_id=None)
    session = _SessionStub(execute_results=[_execute_result(one_or_none=row)])
    with _patch_session(session):
        r = client.get(
            f"/queue/{qid}",
            headers={"Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"queueId": qid, "domain": "acme.com",
                    "status": "pending", "resolvedAccountId": None}


def test_status_sql_is_owner_and_tenant_scoped():
    from routers.queue_actions import STATUS_QUEUE_SQL
    sql = str(STATUS_QUEUE_SQL)
    assert "id = CAST(:queue_id AS uuid)" in sql
    assert "tenant_id = CAST(:tenant_id AS uuid)" in sql
    assert "owner_user_id = CAST(:owner_user_id AS uuid)" in sql


# ---------------------------------------------------------------------------
# Task A3: GET /queue (list) tests
# ---------------------------------------------------------------------------


def test_list_rejects_missing_jwt(client):
    assert client.get("/queue").status_code == 401


def test_list_requires_pg_user_id(client):
    token = _make_jwt(pg_user_id=None)
    assert client.get("/queue", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_list_happy_path(client):
    qid = str(uuid.uuid4())
    row = MagicMock(
        queue_id=qid, domain="acme.com", status="pending", source_type="transcript",
        created_at="2026-06-26T00:00:00+00:00", expires_at="2026-07-26T00:00:00+00:00",
        re_open_count=0, contact_count=2,
        contacts=[{"email": "jane@acme.com", "displayName": "Jane", "role": "VP"}],
        context_source="calendar", meeting_title="Q3 Sync",
        occurred_at="2026-06-20T10:00:00+00:00", attendee_count=3,
    )
    session = _SessionStub(execute_results=[_execute_result(all_rows=[row])])
    with _patch_session(session):
        r = client.get("/queue", headers={"Authorization": f"Bearer {_make_jwt(pg_user_id=PG_USER_UUID)}"})
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    assert len(entries) == 1
    e = entries[0]
    assert e["queueId"] == qid and e["domain"] == "acme.com"
    assert e["contactCount"] == 2 and e["contextSource"] == "calendar"
    assert e["meetingTitle"] == "Q3 Sync"


def test_list_sql_is_owner_scoped_pending_and_batched():
    from routers.queue_actions import LIST_QUEUE_SQL
    sql = str(LIST_QUEUE_SQL)
    assert "owner_user_id = CAST(:owner_user_id AS uuid)" in sql
    assert "tenant_id = CAST(:tenant_id AS uuid)" in sql
    assert "archived_at IS NULL" in sql
    assert "status = 'pending'" in sql
    assert "LIMIT" in sql
    # batched: contacts aggregated, not per-row fetched
    assert "jsonb_agg" in sql or "json_agg" in sql
    # NO email/interaction_summary branch in V1 — calendar only
    assert "pending_interactions" not in sql
