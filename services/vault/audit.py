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

from .errors import VaultError, VaultErrorCode

logger = logging.getLogger(__name__)

AuditOperation = Literal["read", "write", "rotate", "reactivate", "archive"]

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
    asyncpg autocommits per statement on a fresh connection, so the audit
    row is durable the moment ``pool.acquire().__aexit__`` returns —
    regardless of any transaction the caller may have open elsewhere.

    ``credential_id`` is nullable so audit rows can survive a credential row
    deletion (the FK is ON DELETE SET NULL) AND so we can log access attempts
    that fail BEFORE a credential row is identified (e.g., ALLOWLIST rejection
    on read).

    The denormalized identity fields (``tenant_id``, ``user_id``,
    ``provider``) are NOT NULL by design — every audit row must stand alone
    if the credential row it referenced is later deleted.

    Returns the new audit row's UUID. Raises ``VaultError`` with code
    ``VAULT_AUDIT_LOG_WRITE_FAILED`` on any DB error.
    """
    audit_id = uuid.uuid4()
    try:
        async with pool.acquire() as conn:
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
    except Exception as exc:  # asyncpg + pool can raise many subclasses
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
