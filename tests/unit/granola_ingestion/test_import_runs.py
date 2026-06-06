"""Unit tests for services.granola_ingestion.import_runs (EQ-92 / B3).

AsyncMock-style, no DB / no Docker ([[feedback-test-pattern-no-docker]]). The
module is a plain asyncpg helper (not a vault accessor), so we mock the pool +
a routing connection that dispatches by SQL keyword and records every call.

Covers: the get-or-create idempotency (C8), the lifecycle transitions, and the
DERIVED progress reader (C1 — counts come from external_integration_runs, never
a stored counter; the status->bucket mapping per outcomes.py).
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from services.granola_ingestion import import_runs

TENANT = UUID("11111111-1111-4111-8111-111111111111")
USER = UUID("061ae392-47d5-4f04-9ea8-afa241f23555")
CRED = UUID("6a727bae-5140-4f9e-a65e-4ea8d0523f7d")
RUN = UUID("647a03ae-944e-4656-8354-21ba634cff9c")


class _RoutingConn:
    """asyncpg Connection stand-in that routes by SQL keyword.

    ``fetchval_for`` / ``fetchrow_for`` / ``fetch_for`` map a lowercase
    substring of the SQL to the value the corresponding method returns.
    First matching key wins; default falls through to ``None`` / ``[]``.
    Every call is recorded in ``calls`` as (method, sql, args).
    """

    def __init__(
        self,
        *,
        fetchval_for: Optional[dict[str, Any]] = None,
        fetchrow_for: Optional[dict[str, Any]] = None,
        fetch_for: Optional[dict[str, Any]] = None,
    ) -> None:
        self.fetchval_for = fetchval_for or {}
        self.fetchrow_for = fetchrow_for or {}
        self.fetch_for = fetch_for or {}
        self.calls: list[tuple[str, str, tuple]] = []

    def _match(self, table: dict[str, Any], sql: str, default: Any) -> Any:
        low = sql.lower()
        for key, val in table.items():
            if key in low:
                return val
        return default

    async def fetchval(self, sql: str, *args):
        self.calls.append(("fetchval", sql, args))
        return self._match(self.fetchval_for, sql, None)

    async def fetchrow(self, sql: str, *args):
        self.calls.append(("fetchrow", sql, args))
        return self._match(self.fetchrow_for, sql, None)

    async def fetch(self, sql: str, *args):
        self.calls.append(("fetch", sql, args))
        return self._match(self.fetch_for, sql, [])

    async def execute(self, sql: str, *args):
        self.calls.append(("execute", sql, args))
        return "UPDATE 1"


class _AcquireCM:
    def __init__(self, conn: _RoutingConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _RoutingConn:
        return self.conn

    async def __aexit__(self, *_exc):
        return None


class _Pool:
    def __init__(self, conn: _RoutingConn) -> None:
        self.conn = conn

    def acquire(self) -> _AcquireCM:
        return _AcquireCM(self.conn)


def _patch_pool(conn: _RoutingConn):
    pool = _Pool(conn)
    return patch.object(import_runs, "get_asyncpg_pool", new=AsyncMock(return_value=pool))


def _sql_calls(conn: _RoutingConn, method: str) -> list[tuple[str, tuple]]:
    return [(sql, args) for (m, sql, args) in conn.calls if m == method]


# ---------------------------------------------------------------------------
# get_or_create_active_import_run (C8 idempotency)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_or_create_inserts_new_run_returns_created_true():
    # INSERT ... ON CONFLICT DO NOTHING RETURNING id -> a fresh uuid (created).
    conn = _RoutingConn(fetchval_for={"insert into": RUN})
    with _patch_pool(conn):
        run_id, created = await import_runs.get_or_create_active_import_run(
            credential_id=CRED, tenant_id=TENANT, user_id=USER
        )
    assert run_id == RUN
    assert created is True
    insert_sql = _sql_calls(conn, "fetchval")[0][0].lower()
    assert "insert into" in insert_sql and "granola_import_runs" in insert_sql
    # idempotency via the partial-unique inference + DO NOTHING
    assert "on conflict" in insert_sql and "do nothing" in insert_sql
    assert "state in ('queued', 'running')" in insert_sql or "state in ('queued','running')" in insert_sql
    # state seeded to 'queued'
    assert "'queued'" in insert_sql


@pytest.mark.asyncio
async def test_get_or_create_returns_existing_active_run_on_conflict():
    # INSERT RETURNING id -> None (conflict); SELECT the existing active run.
    conn = _RoutingConn(
        fetchval_for={"insert into": None, "select id": RUN},
    )
    with _patch_pool(conn):
        run_id, created = await import_runs.get_or_create_active_import_run(
            credential_id=CRED, tenant_id=TENANT, user_id=USER
        )
    assert run_id == RUN
    assert created is False
    # the recovery SELECT scopes by credential + tenant + user + active state
    select_sql = [s for s, _ in _sql_calls(conn, "fetchval") if "select id" in s.lower()][0].lower()
    assert "credential_id" in select_sql and "tenant_id" in select_sql and "user_id" in select_sql
    assert "state in ('queued', 'running')" in select_sql or "state in ('queued','running')" in select_sql


# ---------------------------------------------------------------------------
# lifecycle transitions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_running_sets_started_at_and_state():
    conn = _RoutingConn()
    with _patch_pool(conn):
        await import_runs.mark_running(import_run_id=RUN, tenant_id=TENANT, user_id=USER)
    sql, args = _sql_calls(conn, "execute")[0]
    low = sql.lower()
    assert "update" in low and "granola_import_runs" in low
    assert "state = 'running'" in low or "state='running'" in low
    assert "started_at = coalesce(started_at" in low  # idempotent start anchor
    # tenant/user scoped
    assert args[0] == RUN and TENANT in args and USER in args


@pytest.mark.asyncio
async def test_mark_running_returns_true_when_row_claimed():
    """mark_running returns True when it claims a queued/running row (the command
    tag is 'UPDATE 1')."""
    conn = _RoutingConn()  # _RoutingConn.execute returns "UPDATE 1"
    with _patch_pool(conn):
        claimed = await import_runs.mark_running(
            import_run_id=RUN, tenant_id=TENANT, user_id=USER
        )
    assert claimed is True


@pytest.mark.asyncio
async def test_mark_running_returns_false_when_already_terminal():
    """A2/Codex P1: mark_running on an already-terminal run matches 0 rows
    ('UPDATE 0') → False, so run_import_step bails rather than re-running a
    backfill on a terminal run."""
    conn = _RoutingConn()
    conn.execute = AsyncMock(return_value="UPDATE 0")  # nothing claimed
    with _patch_pool(conn):
        claimed = await import_runs.mark_running(
            import_run_id=RUN, tenant_id=TENANT, user_id=USER
        )
    assert claimed is False


@pytest.mark.asyncio
async def test_set_import_total_writes_total():
    conn = _RoutingConn()
    with _patch_pool(conn):
        await import_runs.set_import_total(import_run_id=RUN, tenant_id=TENANT, user_id=USER, total=240)
    sql, args = _sql_calls(conn, "execute")[0]
    assert "total" in sql.lower()
    assert 240 in args
    # defense-in-depth: never overwrite the total on a terminal run (Codex P1).
    assert "state in ('queued', 'running')" in sql.lower() or "state in ('queued','running')" in sql.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fn,terminal",
    [
        ("complete_import_run", "complete"),
        ("fail_import_run", "failed"),
        ("cancel_import_run", "cancelled"),
    ],
)
async def test_terminal_transitions(fn, terminal):
    conn = _RoutingConn()
    with _patch_pool(conn):
        await getattr(import_runs, fn)(import_run_id=RUN, tenant_id=TENANT, user_id=USER)
    sql, args = _sql_calls(conn, "execute")[0]
    low = sql.lower()
    assert f"state = '{terminal}'" in low or f"state='{terminal}'" in low
    assert "finished_at = now()" in low
    assert args[0] == RUN and TENANT in args and USER in args


# ---------------------------------------------------------------------------
# read_import_progress — DERIVED (C1), status->bucket mapping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_import_progress_derives_counts_from_integration_runs():
    started = "2026-06-06T10:00:00+00:00"
    conn = _RoutingConn(
        fetchrow_for={
            "from public.granola_import_runs": {
                "state": "running",
                "total": 240,
                "started_at": started,
                "finished_at": None,
            }
        },
        fetch_for={
            "from public.external_integration_runs": [
                {"status": "success", "n": 87},
                {"status": "deferred_pending_account", "n": 4},
                {"status": "skipped_no_business_attendees", "n": 9},
                {"status": "failed", "n": 1},
                {"status": "failed_permanent", "n": 2},
                {"status": "in_progress", "n": 5},  # uncounted (in-flight)
            ]
        },
    )
    with _patch_pool(conn):
        prog = await import_runs.read_import_progress(import_run_id=RUN, tenant_id=TENANT, user_id=USER)
    assert prog["state"] == "running"
    assert prog["total"] == 240
    assert prog["done"] == 87
    assert prog["deferred"] == 4
    assert prog["skipped"] == 9
    assert prog["errors"] == 3  # failed + failed_permanent
    # the COUNT query is scoped to this credential's notes since started_at
    count_sql = [s for s, _ in _sql_calls(conn, "fetch") if "external_integration_runs" in s.lower()][0].lower()
    assert "provider = 'granola'" in count_sql or "provider='granola'" in count_sql
    assert "created_at >=" in count_sql
    assert "group by status" in count_sql


@pytest.mark.asyncio
async def test_read_import_progress_zero_counts_before_started():
    # state='queued', started_at is NULL -> no count query, all buckets 0.
    conn = _RoutingConn(
        fetchrow_for={
            "from public.granola_import_runs": {
                "state": "queued",
                "total": None,
                "started_at": None,
                "finished_at": None,
            }
        }
    )
    with _patch_pool(conn):
        prog = await import_runs.read_import_progress(import_run_id=RUN, tenant_id=TENANT, user_id=USER)
    assert prog["state"] == "queued"
    assert prog["total"] is None
    assert prog["done"] == prog["deferred"] == prog["skipped"] == prog["errors"] == 0
    # must NOT query external_integration_runs when there's no started_at anchor
    assert not [s for s, _ in _sql_calls(conn, "fetch") if "external_integration_runs" in s.lower()]


@pytest.mark.asyncio
async def test_read_import_progress_returns_none_for_missing_run():
    conn = _RoutingConn()  # fetchrow -> None
    with _patch_pool(conn):
        prog = await import_runs.read_import_progress(import_run_id=RUN, tenant_id=TENANT, user_id=USER)
    assert prog is None


@pytest.mark.asyncio
async def test_latest_import_run_orders_desc_and_scopes():
    conn = _RoutingConn(
        fetchrow_for={
            "from public.granola_import_runs": {
                "id": RUN,
                "state": "complete",
                "total": 12,
                "started_at": None,
                "finished_at": None,
                "created_at": None,
            }
        }
    )
    with _patch_pool(conn):
        row = await import_runs.latest_import_run(credential_id=CRED, tenant_id=TENANT, user_id=USER)
    assert row["id"] == RUN
    sql = _sql_calls(conn, "fetchrow")[0][0].lower()
    assert "order by created_at desc" in sql and "limit 1" in sql
    assert "credential_id" in sql and "tenant_id" in sql and "user_id" in sql
