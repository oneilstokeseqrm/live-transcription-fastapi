"""Append-only writer for ``vault.credential_access_log``.

This module is the ONLY path in the codebase that writes to the audit table.
There is no UPDATE or DELETE function here, and there must never be one — the
append-only invariant is enforced at the application layer for MVP (LOCKED-42
defers DB-role-level enforcement to Phase 2.1 hardening).

**Durability model (Codex round 2 finding):** the audit writer takes an
``asyncpg.Pool`` and acquires its own dedicated connection per call. The
audit row is written + autocommitted on that connection, completely
independent of any transaction the caller may have open on a different
connection. This means audit rows survive an outer transaction rollback —
the "failure to log = failure to access" guarantee holds unconditionally,
not just within vault's own transaction scope.

The trade-off is that the audit insert and the credential operation it
records are on DIFFERENT connections, so they cannot be one atomic SQL
transaction. The vault accessor functions in
:mod:`services.vault.user_credentials` ORDER the operations so audit is
committed BEFORE the credential transaction commits, preserving the
"audit always precedes successful access" property; a phantom-audit race
(audit committed + credential commit subsequently failed) is rare and
detectable by reconciliation (a Phase 2.1 follow-up).
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal
from uuid import UUID

import asyncpg
import asyncpg.pool

from .errors import VaultError, VaultErrorCode

logger = logging.getLogger(__name__)

AuditOperation = Literal["read", "write", "rotate", "reactivate", "archive"]

# asyncpg.Pool.acquire() yields a PoolConnectionProxy that proxies the
# Connection interface. They are separate types in asyncpg's stubs but
# share the methods we use. Accept either so callers can pass whichever
# they hold.
ConnectionOrProxy = asyncpg.Connection | asyncpg.pool.PoolConnectionProxy

_INSERT_AUDIT_ROW_SQL = """
INSERT INTO vault.credential_access_log (
    id,
    timestamp,
    credential_id,
    tenant_id,
    user_id,
    provider,
    caller_module,
    operation,
    success,
    error_code,
    trace_id
) VALUES (
    $1, CURRENT_TIMESTAMP, $2, $3, $4, $5, $6, $7, $8, $9, $10
)
"""


async def write_audit_row_on_conn(
    *,
    conn: ConnectionOrProxy,
    credential_id: UUID | None,
    tenant_id: UUID,
    user_id: UUID,
    provider: str,
    caller_module: str,
    operation: AuditOperation,
    success: bool,
    error_code: str | None = None,
    trace_id: str | None = None,
) -> UUID:
    """Append one row to ``vault.credential_access_log`` using ``conn``.

    Use this variant when the audit write must participate in the caller's
    transaction. The vault accessors call this inside their credential
    transaction so the audit row commits atomically with the credential
    INSERT/UPDATE — single SQL transaction, no nested pool acquires (Codex
    R4 [P1] deadlock avoidance).

    Returns the new audit row's UUID. Raises ``VaultError`` with code
    ``VAULT_AUDIT_LOG_WRITE_FAILED`` on any DB error so the surrounding
    transaction rolls back.
    """
    audit_id = uuid.uuid4()
    try:
        await conn.execute(
            _INSERT_AUDIT_ROW_SQL,
            audit_id,
            credential_id,
            tenant_id,
            user_id,
            provider,
            caller_module,
            operation,
            success,
            error_code,
            trace_id,
        )
    except Exception as exc:
        logger.exception(
            "credential_access_log insert failed (tenant_id=%s user_id=%s operation=%s)",
            tenant_id,
            user_id,
            operation,
        )
        raise VaultError(
            VaultErrorCode.VAULT_AUDIT_LOG_WRITE_FAILED,
            f"failed to write credential_access_log row for operation={operation}",
            cause=exc,
        ) from exc

    return audit_id


async def write_audit_row(
    *,
    pool: asyncpg.Pool,
    credential_id: UUID | None,
    tenant_id: UUID,
    user_id: UUID,
    provider: str,
    caller_module: str,
    operation: AuditOperation,
    success: bool,
    error_code: str | None = None,
    trace_id: str | None = None,
) -> UUID:
    """Append one row to ``vault.credential_access_log`` on a fresh connection.

    Acquires a dedicated connection from ``pool`` for this single insert.
    Use this variant for paths that have no caller transaction to attach
    to — read audits (after the read connection is released) and
    failure-audits (after the credential transaction rolled back).

    Do NOT call this while already holding a connection from ``pool``
    inside an open transaction — that nests the pool acquire and can
    deadlock when ``pool.max_size`` is 1 or when N concurrent writes
    saturate a pool of size N (Codex R4 [P1]). For audit writes that
    must participate in an existing transaction, use
    :func:`write_audit_row_on_conn` with the held connection instead.

    Returns the new audit row's UUID. Raises ``VaultError`` with code
    ``VAULT_AUDIT_LOG_WRITE_FAILED`` on any DB error.
    """
    try:
        async with pool.acquire() as conn:
            return await write_audit_row_on_conn(
                conn=conn,
                credential_id=credential_id,
                tenant_id=tenant_id,
                user_id=user_id,
                provider=provider,
                caller_module=caller_module,
                operation=operation,
                success=success,
                error_code=error_code,
                trace_id=trace_id,
            )
    except VaultError:
        raise  # already structured
    except Exception as exc:
        logger.exception(
            "credential_access_log pool.acquire failed (tenant_id=%s user_id=%s operation=%s)",
            tenant_id,
            user_id,
            operation,
        )
        raise VaultError(
            VaultErrorCode.VAULT_AUDIT_LOG_WRITE_FAILED,
            f"failed to acquire pool connection for audit log operation={operation}",
            cause=exc,
        ) from exc
