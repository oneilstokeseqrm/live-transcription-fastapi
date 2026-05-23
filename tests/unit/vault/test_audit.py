"""Unit tests for ``services.vault.audit``.

The audit writer takes an ``asyncpg.Pool`` and acquires a dedicated
connection per call, so the audit row is durable independent of any
transaction the caller may have open on a different connection.

Tests verify:

* INSERT SQL shape (column list + parameter order)
* DB failure surfaces as ``VAULT_AUDIT_LOG_WRITE_FAILED``
* The audit writer acquires from the pool exactly once per call (so audit
  never piggybacks on a caller's connection)
* No UPDATE / DELETE functions exist in the module (append-only invariant)
"""

from __future__ import annotations

import inspect
import re
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.vault import audit
from services.vault.errors import VaultError, VaultErrorCode


class _FakeAcquireContextManager:
    """Mimics ``asyncpg.Pool.acquire()``'s async context manager.

    ``async with pool.acquire() as conn:`` should yield a Connection.
    asyncpg.Pool.acquire returns a PoolAcquireContext, which is what we mimic.
    """

    def __init__(self, conn: AsyncMock) -> None:
        self._conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


@pytest.fixture
def fake_audit_conn() -> AsyncMock:
    """Mock asyncpg connection for audit inserts (autocommits)."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    return conn


@pytest.fixture
def fake_pool(fake_audit_conn: AsyncMock) -> MagicMock:
    """Mock asyncpg.Pool that hands out fake_audit_conn on acquire()."""
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=lambda: _FakeAcquireContextManager(fake_audit_conn))
    return pool


class TestWriteAuditRow:
    @pytest.mark.asyncio
    async def test_insert_called_with_expected_parameters(
        self, fake_pool: MagicMock, fake_audit_conn: AsyncMock
    ):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()

        returned_id = await audit.write_audit_row(
            pool=fake_pool,
            credential_id=credential_id,
            tenant_id=tenant_id,
            user_id=user_id,
            provider="granola",
            caller_module="services.granola_ingestion.adapter",
            operation="read",
            success=True,
            trace_id="trace-abc",
        )

        assert isinstance(returned_id, uuid.UUID)
        fake_pool.acquire.assert_called_once()
        fake_audit_conn.execute.assert_awaited_once()
        args = fake_audit_conn.execute.await_args.args
        sql = args[0]
        assert "INSERT INTO vault.credential_access_log" in sql
        assert args[1] == returned_id
        assert args[2] == credential_id
        assert args[3] == tenant_id
        assert args[4] == user_id
        assert args[5] == "granola"
        assert args[6] == "services.granola_ingestion.adapter"
        assert args[7] == "read"
        assert args[8] is True
        assert args[9] is None
        assert args[10] == "trace-abc"

    @pytest.mark.asyncio
    async def test_credential_id_nullable_for_pre_lookup_failures(
        self, fake_pool: MagicMock, fake_audit_conn: AsyncMock
    ):
        """ALLOWLIST violations log before a credential row is known."""
        await audit.write_audit_row(
            pool=fake_pool,
            credential_id=None,
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider="granola",
            caller_module="malicious.module",
            operation="read",
            success=False,
            error_code="vault_caller_not_allowed",
        )
        args = fake_audit_conn.execute.await_args.args
        assert args[2] is None  # credential_id position
        assert args[8] is False

    @pytest.mark.asyncio
    async def test_db_failure_raises_audit_log_write_failed(
        self, fake_pool: MagicMock, fake_audit_conn: AsyncMock
    ):
        fake_audit_conn.execute = AsyncMock(side_effect=RuntimeError("connection reset"))
        with pytest.raises(VaultError) as exc_info:
            await audit.write_audit_row(
                pool=fake_pool,
                credential_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                caller_module="services.granola_ingestion.adapter",
                operation="write",
                success=True,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_AUDIT_LOG_WRITE_FAILED
        assert exc_info.value.__cause__ is not None

    @pytest.mark.asyncio
    async def test_pool_acquire_failure_raises_audit_log_write_failed(
        self, fake_pool: MagicMock, fake_audit_conn: AsyncMock
    ):
        """If the pool can't hand out a connection (exhausted, network), the
        audit failure is still surfaced as a structured VaultError."""

        class _FailingAcquire:
            async def __aenter__(self):
                raise RuntimeError("pool exhausted")

            async def __aexit__(self, *_):
                return False

        fake_pool.acquire = MagicMock(return_value=_FailingAcquire())
        with pytest.raises(VaultError) as exc_info:
            await audit.write_audit_row(
                pool=fake_pool,
                credential_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                caller_module="services.granola_ingestion.adapter",
                operation="write",
                success=True,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_AUDIT_LOG_WRITE_FAILED


class TestAppendOnlyInvariant:
    """The audit module must expose only INSERT; no UPDATE or DELETE."""

    def test_no_update_or_delete_function_defined(self):
        names = {name for name, _ in inspect.getmembers(audit, inspect.isfunction)}
        forbidden_substrings = ("update_audit", "delete_audit", "modify_audit", "remove_audit")
        leaks = [n for n in names if any(s in n.lower() for s in forbidden_substrings)]
        assert leaks == [], f"append-only invariant violated: {leaks}"

    def test_module_sql_has_no_update_or_delete(self):
        """Defense-in-depth: grep the module source for forbidden SQL verbs."""
        source = inspect.getsource(audit)
        # Strip docstrings + comments so words like "UPDATE" inside docs
        # don't trip this test.
        sql_only = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
        sql_only = re.sub(r"#.*", "", sql_only)
        assert "UPDATE vault.credential_access_log" not in sql_only
        assert "DELETE FROM vault.credential_access_log" not in sql_only
