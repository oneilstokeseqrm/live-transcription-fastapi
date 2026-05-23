"""Unit tests for ``services.vault.audit``.

The audit writer is append-only and runs inside the caller's asyncpg
transaction. Tests verify:

* INSERT SQL shape (column list + parameter order)
* DB failure surfaces as ``VAULT_AUDIT_LOG_WRITE_FAILED``
* No UPDATE / DELETE functions exist in the module (append-only invariant)
"""

from __future__ import annotations

import inspect
import re
import uuid
from unittest.mock import AsyncMock

import pytest

from services.vault import audit
from services.vault.errors import VaultError, VaultErrorCode


@pytest.fixture
def fake_conn() -> AsyncMock:
    """Mock asyncpg connection with a no-op ``execute``."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    return conn


class TestWriteAuditRow:
    @pytest.mark.asyncio
    async def test_insert_called_with_expected_parameters(self, fake_conn: AsyncMock):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()

        returned_id = await audit.write_audit_row(
            conn=fake_conn,
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
        fake_conn.execute.assert_awaited_once()
        args = fake_conn.execute.await_args.args
        # args[0] is the SQL string; args[1:] are the positional parameters
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
    async def test_credential_id_nullable_for_pre_lookup_failures(self, fake_conn: AsyncMock):
        """ALLOWLIST violations log before a credential row is known."""
        await audit.write_audit_row(
            conn=fake_conn,
            credential_id=None,
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider="granola",
            caller_module="malicious.module",
            operation="read",
            success=False,
            error_code="vault_caller_not_allowed",
        )
        args = fake_conn.execute.await_args.args
        assert args[2] is None  # credential_id position
        assert args[8] is False

    @pytest.mark.asyncio
    async def test_db_failure_raises_audit_log_write_failed(self, fake_conn: AsyncMock):
        fake_conn.execute = AsyncMock(side_effect=RuntimeError("connection reset"))
        with pytest.raises(VaultError) as exc_info:
            await audit.write_audit_row(
                conn=fake_conn,
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
        # Strip comments + docstrings before grepping for SQL verbs (so the
        # word "UPDATE" inside a docstring explaining the rule doesn't trip
        # this test).
        sql_only = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
        sql_only = re.sub(r"#.*", "", sql_only)
        assert "UPDATE vault.credential_access_log" not in sql_only
        assert "DELETE FROM vault.credential_access_log" not in sql_only
