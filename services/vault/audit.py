"""Append-only writer for ``vault.credential_access_log``.

This module is the ONLY path in the codebase that writes to the audit table.
There is no UPDATE or DELETE function here, and there must never be one — the
append-only invariant is enforced at the application layer for MVP (LOCKED-42
defers DB-role-level enforcement to Phase 2.1 hardening).

The audit row is written in the same asyncpg connection as the credential
read/write/rotate it accompanies. The caller passes its connection in so the
write participates in the caller's transaction; a failure to log audit ==
a failure to access the credential (the caller's transaction rolls back).
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal
from uuid import UUID

import asyncpg

from .errors import VaultError, VaultErrorCode

logger = logging.getLogger(__name__)

AuditOperation = Literal["read", "write", "rotate", "archive"]

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
    conn: asyncpg.Connection,
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
    """Append one row to ``vault.credential_access_log``.

    ``credential_id`` is nullable so audit rows can survive a credential row
    deletion (the FK is ON DELETE SET NULL) AND so we can log access attempts
    that fail BEFORE a credential row is identified (e.g., ALLOWLIST rejection
    on read).

    The other denormalized identity fields (``tenant_id``, ``user_id``,
    ``provider``) are NOT NULL by design — every audit row must stand alone if
    the credential row it referenced is later deleted.

    Returns the new audit row's UUID. Raises ``VaultError`` with code
    ``VAULT_AUDIT_LOG_WRITE_FAILED`` on any DB error; the caller's transaction
    should roll back so the credential operation does not silently complete
    without an audit trail.
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
    except Exception as exc:  # asyncpg raises a wide variety of subclasses
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
