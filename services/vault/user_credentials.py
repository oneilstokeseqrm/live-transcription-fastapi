"""High-level credential accessor module.

This is the only path Phase 2 code uses to read or write third-party
credentials. It composes :mod:`services.vault.encryption` (KMS envelope crypto)
and :mod:`services.vault.audit` (forensic log) behind an ALLOWLIST app-layer
gate.

Invariants:

* **LOCKED-40** — every Encrypt/Decrypt pass the same 4-field EncryptionContext
  ``{tenant_id, user_id, provider, credential_id}`` that was bound at
  GenerateDataKey time. KMS refuses Decrypt if any field is missing or changed.
* **LOCKED-41** — ``tenant_id`` and ``user_id`` are explicit function
  arguments. There is no global, request, or thread-local context they are
  pulled from. Phase 2d's adapter passes them in from the credential row it
  read at scheduler time.
* **LOCKED-42** — the :data:`ALLOWLIST` is the load-bearing app-layer gate.
  Anything not in the set fails closed with
  :class:`~services.vault.errors.VaultPermissionError` before SQL or KMS runs.
* **LOCKED-43** — :func:`store_credential`, :func:`rotate_credential_key`, and
  :func:`reactivate_credential` all mint a fresh DEK + fresh nonce via the
  encryption module on every call.

**Pool-based architecture (Codex round 2 [P1] fix):** accessors take
``asyncpg.Pool`` rather than ``Connection``. The pool is used to acquire
dedicated connections for credential SQL AND for audit writes — separately.
This gives audit writes unconditional durability: the audit row commits on
its own connection regardless of any transaction the caller may have open on
a different connection.

**Atomicity of writes** (store / rotate / reactivate) is enforced by ordering
the operations: credential SQL runs inside an inner transaction, and the
success-audit is committed on a SEPARATE connection BEFORE the credential
transaction commits. If the audit fails, the credential transaction rolls
back. If the audit succeeds and the credential commit subsequently fails
(rare; typically a network drop during commit), we get a phantom audit row
referring to a non-existent credential — operationally a false positive,
detectable by reconciliation (Phase 2.1 follow-up).

**Failure-audits** are written via the same separate-connection path, so
they survive an outer transaction rollback. A double-fault (failure-audit
ALSO fails) is logged but not re-raised — the original ``VaultError`` is
what the caller sees.

**Reads** (:func:`get_granola_credential_for_user`) do not need atomicity:
the credential is not "accessed" at the API boundary unless the caller
receives it, and any failure path re-raises before the value crosses the
API boundary. The audit write still happens on every code path.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from . import audit, encryption
from .errors import VaultError, VaultErrorCode, VaultPermissionError

logger = logging.getLogger(__name__)


ALLOWLIST: frozenset[str] = frozenset(
    {
        "services.granola_ingestion.adapter",
        "services.granola_ingestion.scheduler",
        "routers.granola",
    }
)
"""Caller modules permitted to use the vault accessor.

The LOCKED-42 app-layer gate. Strings are compared literally; callers must
identify themselves by passing their own ``__name__`` (or a stable string that
matches one of these entries). Adding new callers requires a code review that
threads through this set.
"""

_GRANOLA_PROVIDER = "granola"


@dataclass(frozen=True)
class GranolaCredential:
    """Decrypted Granola credential snapshot.

    ``api_key`` is the cleartext ``grn_…`` Granola API key. It MUST NOT be
    persisted, logged, or returned over HTTP. The adapter passes it directly
    into an :class:`~services.granola_ingestion.api_client.GranolaAPIClient`
    constructor and lets it fall out of scope.
    """

    id: UUID
    tenant_id: UUID
    user_id: UUID
    provider: str
    api_key: str
    config: dict[str, Any]
    status: str
    last_polled_at: datetime | None
    last_error: dict[str, Any] | None
    consecutive_failures: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


_SELECT_GRANOLA_CREDENTIAL_SQL = """
SELECT
    id,
    tenant_id,
    user_id,
    provider,
    encrypted_api_key,
    encrypted_dek,
    nonce,
    config,
    status,
    last_polled_at,
    last_error,
    consecutive_failures,
    created_at,
    updated_at,
    archived_at
FROM vault.user_credentials
WHERE tenant_id = $1
  AND user_id = $2
  AND provider = $3
  AND archived_at IS NULL
"""

_SELECT_ANY_CREDENTIAL_ID_SQL = """
SELECT id, archived_at
FROM vault.user_credentials
WHERE tenant_id = $1
  AND user_id = $2
  AND provider = $3
"""

_INSERT_CREDENTIAL_SQL = """
INSERT INTO vault.user_credentials (
    id,
    tenant_id,
    user_id,
    provider,
    encrypted_api_key,
    encrypted_dek,
    nonce,
    config,
    status,
    consecutive_failures,
    created_at,
    updated_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, 0,
    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
)
"""

_SELECT_ROTATE_IDENTITY_SQL = """
SELECT tenant_id, user_id, provider
FROM vault.user_credentials
WHERE id = $1
  AND archived_at IS NULL
"""

_UPDATE_ROTATE_SQL = """
UPDATE vault.user_credentials
SET encrypted_api_key = $1,
    encrypted_dek = $2,
    nonce = $3,
    status = 'active',
    last_error = NULL,
    consecutive_failures = 0,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $4
  AND archived_at IS NULL
RETURNING tenant_id
"""

_UPDATE_REACTIVATE_SQL = """
UPDATE vault.user_credentials
SET encrypted_api_key = $1,
    encrypted_dek = $2,
    nonce = $3,
    config = $4::jsonb,
    status = 'active',
    archived_at = NULL,
    last_error = NULL,
    consecutive_failures = 0,
    updated_at = CURRENT_TIMESTAMP
WHERE id = $5
  AND archived_at IS NOT NULL
RETURNING tenant_id
"""


def _build_encryption_context(
    *, tenant_id: UUID, user_id: UUID, provider: str, credential_id: UUID
) -> dict[str, str]:
    """Materialize the LOCKED-40 4-field EncryptionContext.

    All four values are stringified UUIDs / strings exactly as bound by the
    KMS key policy's ``Null:false`` condition.
    """
    return {
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "provider": provider,
        "credential_id": str(credential_id),
    }


def _check_allowlist(caller_module: str) -> None:
    """Reject callers not in :data:`ALLOWLIST`.

    Raises :class:`VaultPermissionError` BEFORE any SQL or KMS work runs.
    """
    if caller_module not in ALLOWLIST:
        raise VaultPermissionError(caller_module)


async def _best_effort_failure_audit(
    *,
    pool: asyncpg.Pool,
    credential_id: UUID | None,
    tenant_id: UUID,
    user_id: UUID,
    provider: str,
    caller_module: str,
    operation: audit.AuditOperation,
    error_code: str,
    trace_id: str | None,
) -> None:
    """Write a failure-audit row on a dedicated connection.

    Acquires its own connection from ``pool`` so the write survives any
    outer transaction rollback. If the audit write itself fails (DB
    unreachable, table missing), log + swallow — the original
    ``VaultError`` is the operationally-meaningful signal; double-faulting
    on the forensic write would mask it.
    """
    try:
        await audit.write_audit_row(
            pool=pool,
            credential_id=credential_id,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider,
            caller_module=caller_module,
            operation=operation,
            success=False,
            error_code=error_code,
            trace_id=trace_id,
        )
    except VaultError:
        logger.exception(
            "double-fault: failure-audit write failed for operation=%s "
            "credential_id=%s tenant_id=%s",
            operation,
            credential_id,
            tenant_id,
        )


def _coerce_jsonb(value: Any) -> dict[str, Any] | None:
    """Normalize asyncpg's JSONB return shape to a dict-or-None.

    asyncpg may return JSONB as a parsed dict (when the codec is registered)
    or as a raw JSON string (default). We handle both so callers see a
    consistent shape.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        return json.loads(value)
    raise VaultError(
        VaultErrorCode.VAULT_DB_NOT_FOUND,
        f"unexpected JSONB shape: {type(value).__name__}",
    )


def _dumps_jsonb(value: dict[str, Any]) -> str:
    """Serialize a dict for the ``$N::jsonb`` cast on INSERT."""
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))


async def get_granola_credential_for_user(
    *,
    tenant_id: UUID,
    user_id: UUID,
    caller_module: str,
    pool: asyncpg.Pool,
    trace_id: str | None = None,
) -> GranolaCredential | None:
    """Look up and decrypt a user's active Granola credential.

    Returns ``None`` if no active credential exists (the row may be archived
    or never created). Raises :class:`VaultPermissionError` if the caller is
    not in :data:`ALLOWLIST`; raises :class:`VaultError` on KMS or DB failure.

    Writes one audit row to ``vault.credential_access_log`` per call (on a
    dedicated connection acquired from ``pool``, independent of any
    transaction the caller may have open):

      * ALLOWLIST rejection → ``success=false, error_code=VAULT_CALLER_NOT_ALLOWED``
      * No credential row → ``success=true, credential_id=NULL``
      * Decrypt success → ``success=true, credential_id=<row id>``
      * Decrypt failure → ``success=false, error_code=<KMS/AES code>``
    """
    try:
        _check_allowlist(caller_module)
    except VaultPermissionError as exc:
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=None,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=_GRANOLA_PROVIDER,
            caller_module=caller_module,
            operation="read",
            error_code=exc.code.value,
            trace_id=trace_id,
        )
        raise

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _SELECT_GRANOLA_CREDENTIAL_SQL,
            tenant_id,
            user_id,
            _GRANOLA_PROVIDER,
        )

    if row is None:
        await audit.write_audit_row(
            pool=pool,
            credential_id=None,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=_GRANOLA_PROVIDER,
            caller_module=caller_module,
            operation="read",
            success=True,
            trace_id=trace_id,
        )
        return None

    credential_id = row["id"]
    context = _build_encryption_context(
        tenant_id=tenant_id,
        user_id=user_id,
        provider=_GRANOLA_PROVIDER,
        credential_id=credential_id,
    )

    try:
        api_key = encryption.decrypt_credential(
            encrypted_api_key=bytes(row["encrypted_api_key"]),
            encrypted_dek=bytes(row["encrypted_dek"]),
            nonce=bytes(row["nonce"]),
            encryption_context=context,
        )
    except VaultError as exc:
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=credential_id,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=_GRANOLA_PROVIDER,
            caller_module=caller_module,
            operation="read",
            error_code=exc.code.value,
            trace_id=trace_id,
        )
        raise

    await audit.write_audit_row(
        pool=pool,
        credential_id=credential_id,
        tenant_id=tenant_id,
        user_id=user_id,
        provider=_GRANOLA_PROVIDER,
        caller_module=caller_module,
        operation="read",
        success=True,
        trace_id=trace_id,
    )

    return GranolaCredential(
        id=credential_id,
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        provider=row["provider"],
        api_key=api_key,
        config=_coerce_jsonb(row["config"]) or {},
        status=row["status"],
        last_polled_at=row["last_polled_at"],
        last_error=_coerce_jsonb(row["last_error"]),
        consecutive_failures=row["consecutive_failures"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        archived_at=row["archived_at"],
    )


async def store_credential(
    *,
    tenant_id: UUID,
    user_id: UUID,
    provider: str,
    api_key: str,
    config: dict[str, Any],
    caller_module: str,
    pool: asyncpg.Pool,
    trace_id: str | None = None,
) -> UUID:
    """Encrypt and insert a new credential row.

    Mints a fresh DEK + fresh nonce per LOCKED-43. The new row's UUID is
    bound into the 4-field EncryptionContext at GenerateDataKey time, so the
    row's keys can only be decrypted by callers passing that same UUID later.

    Ordering for atomicity: INSERT runs inside a transaction on a dedicated
    credential-connection; the success-audit then commits on a SEPARATE
    connection from the same pool; finally the credential transaction
    commits. If the audit fails, the credential transaction rolls back —
    no credential is persisted without a forensic record.

    Returns the new credential UUID. If a row already exists for
    ``(tenant_id, user_id, provider)`` and is not archived, the INSERT fails
    with a uniqueness violation surfaced as :class:`VaultError` with code
    ``VAULT_DB_INSERT_FAILED``. Callers handling reconnect-after-disconnect
    should call :func:`reactivate_credential` for archived rows.
    """
    try:
        _check_allowlist(caller_module)
    except VaultPermissionError as exc:
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=None,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider,
            caller_module=caller_module,
            operation="write",
            error_code=exc.code.value,
            trace_id=trace_id,
        )
        raise

    credential_id = uuid.uuid4()
    context = _build_encryption_context(
        tenant_id=tenant_id,
        user_id=user_id,
        provider=provider,
        credential_id=credential_id,
    )

    try:
        encrypted_api_key, encrypted_dek, nonce = encryption.encrypt_credential(
            plaintext=api_key,
            encryption_context=context,
        )
    except VaultError as exc:
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=None,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider,
            caller_module=caller_module,
            operation="write",
            error_code=exc.code.value,
            trace_id=trace_id,
        )
        raise

    config_json = _dumps_jsonb(config)

    # Atomic critical section. Ordering:
    #   1. INSERT credential inside cred_conn.transaction (UNCOMMITTED)
    #   2. Write success-audit on a SEPARATE connection (autocommits)
    #   3. Commit cred_conn.transaction
    # If step 2 fails, cred_conn transaction rolls back (audit raise propagates).
    # If step 2 succeeds and step 3 fails, we get a phantom audit (rare;
    # detectable by reconciliation).
    try:
        async with pool.acquire() as cred_conn:
            async with cred_conn.transaction():
                try:
                    await cred_conn.execute(
                        _INSERT_CREDENTIAL_SQL,
                        credential_id,
                        tenant_id,
                        user_id,
                        provider,
                        encrypted_api_key,
                        encrypted_dek,
                        nonce,
                        config_json,
                        "active",
                    )
                except Exception as insert_exc:
                    raise VaultError(
                        VaultErrorCode.VAULT_DB_INSERT_FAILED,
                        f"insert into vault.user_credentials failed: {insert_exc.__class__.__name__}",
                        cause=insert_exc,
                    ) from insert_exc
                # Audit on a SEPARATE connection — autocommits independent
                # of cred_conn's transaction. Must succeed before the
                # credential transaction commits.
                await audit.write_audit_row(
                    pool=pool,
                    credential_id=credential_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    provider=provider,
                    caller_module=caller_module,
                    operation="write",
                    success=True,
                    trace_id=trace_id,
                )
    except VaultError as exc:
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=None,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider,
            caller_module=caller_module,
            operation="write",
            error_code=exc.code.value,
            trace_id=trace_id,
        )
        raise

    return credential_id


async def rotate_credential_key(
    *,
    credential_id: UUID,
    new_api_key: str,
    caller_module: str,
    pool: asyncpg.Pool,
    trace_id: str | None = None,
) -> None:
    """Replace the encrypted key material on an existing credential row.

    Mints a fresh DEK + fresh nonce per LOCKED-43. ``credential_id`` stays the
    same so the 4-field EncryptionContext (and therefore existing reader
    invariants) does not change — only the wrapped key material rotates.

    Resets ``status='active'``, ``last_error=NULL``, and
    ``consecutive_failures=0`` so the next poll cycle starts clean after
    a manual rotation. An operator rotating a ``revoked`` or ``error``
    credential signals intent to retry, and the credential becomes
    immediately eligible for polling again.

    Raises :class:`VaultError` with ``VAULT_DB_NOT_FOUND`` if the credential
    row is missing or already archived. Same atomicity ordering as
    :func:`store_credential`.
    """
    try:
        _check_allowlist(caller_module)
    except VaultPermissionError as exc:
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=credential_id,
            tenant_id=_NULL_UUID,
            user_id=_NULL_UUID,
            provider=_UNKNOWN_PROVIDER,
            caller_module=caller_module,
            operation="rotate",
            error_code=exc.code.value,
            trace_id=trace_id,
        )
        raise

    # Look up the credential row's identity fields first so the audit row
    # and the EncryptionContext both have authoritative tenant_id/user_id/
    # provider values. Identity lookup uses its own short-lived connection.
    async with pool.acquire() as lookup_conn:
        identity_row = await lookup_conn.fetchrow(
            _SELECT_ROTATE_IDENTITY_SQL,
            credential_id,
        )
    if identity_row is None:
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=credential_id,
            tenant_id=_NULL_UUID,
            user_id=_NULL_UUID,
            provider=_UNKNOWN_PROVIDER,
            caller_module=caller_module,
            operation="rotate",
            error_code=VaultErrorCode.VAULT_DB_NOT_FOUND.value,
            trace_id=trace_id,
        )
        raise VaultError(
            VaultErrorCode.VAULT_DB_NOT_FOUND,
            f"no active credential row with id={credential_id}",
        )

    tenant_id: UUID = identity_row["tenant_id"]
    user_id: UUID = identity_row["user_id"]
    provider: str = identity_row["provider"]
    context = _build_encryption_context(
        tenant_id=tenant_id,
        user_id=user_id,
        provider=provider,
        credential_id=credential_id,
    )

    try:
        encrypted_api_key, encrypted_dek, nonce = encryption.encrypt_credential(
            plaintext=new_api_key,
            encryption_context=context,
        )
    except VaultError as exc:
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=credential_id,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider,
            caller_module=caller_module,
            operation="rotate",
            error_code=exc.code.value,
            trace_id=trace_id,
        )
        raise

    try:
        async with pool.acquire() as cred_conn:
            async with cred_conn.transaction():
                updated = await cred_conn.fetchrow(
                    _UPDATE_ROTATE_SQL,
                    encrypted_api_key,
                    encrypted_dek,
                    nonce,
                    credential_id,
                )
                if updated is None:
                    # Lost to a concurrent archive between identity lookup
                    # and the UPDATE.
                    raise VaultError(
                        VaultErrorCode.VAULT_DB_NOT_FOUND,
                        f"credential id={credential_id} archived between lookup and rotate",
                    )
                await audit.write_audit_row(
                    pool=pool,
                    credential_id=credential_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    provider=provider,
                    caller_module=caller_module,
                    operation="rotate",
                    success=True,
                    trace_id=trace_id,
                )
    except VaultError as exc:
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=credential_id,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider,
            caller_module=caller_module,
            operation="rotate",
            error_code=exc.code.value,
            trace_id=trace_id,
        )
        raise


async def reactivate_credential(
    *,
    tenant_id: UUID,
    user_id: UUID,
    provider: str,
    new_api_key: str,
    new_config: dict[str, Any],
    caller_module: str,
    pool: asyncpg.Pool,
    trace_id: str | None = None,
) -> UUID:
    """Reactivate a previously archived credential row.

    Used by the /connect endpoint when a user re-connects after disconnecting.
    The schema's ``UNIQUE(tenant_id, user_id, provider)`` covers archived
    rows too, so :func:`store_credential` can't simply INSERT a fresh row
    on reconnect; this function handles that case by UPDATING the archived
    row in place.

    The archived row's ``id`` is preserved (so the EncryptionContext binding
    semantics stay consistent — anyone who held a reference to the
    credential UUID can still decrypt after a reactivate). The encrypted
    key material AND config are replaced; ``status`` is reset to ``active``,
    ``archived_at`` is cleared, ``last_error`` and ``consecutive_failures``
    are reset.

    Raises ``VAULT_DB_NOT_FOUND`` if no row exists for the given
    ``(tenant_id, user_id, provider)``. Raises ``VAULT_DB_INSERT_FAILED``
    with descriptive message if a row exists but is currently ACTIVE —
    caller should call :func:`rotate_credential_key` instead.
    """
    try:
        _check_allowlist(caller_module)
    except VaultPermissionError as exc:
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=None,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider,
            caller_module=caller_module,
            operation="reactivate",
            error_code=exc.code.value,
            trace_id=trace_id,
        )
        raise

    # SELECT existing row to get its id + verify it is archived.
    async with pool.acquire() as lookup_conn:
        existing = await lookup_conn.fetchrow(
            _SELECT_ANY_CREDENTIAL_ID_SQL,
            tenant_id,
            user_id,
            provider,
        )

    if existing is None:
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=None,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider,
            caller_module=caller_module,
            operation="reactivate",
            error_code=VaultErrorCode.VAULT_DB_NOT_FOUND.value,
            trace_id=trace_id,
        )
        raise VaultError(
            VaultErrorCode.VAULT_DB_NOT_FOUND,
            f"no credential row for tenant_id={tenant_id} user_id={user_id} "
            f"provider={provider}; use store_credential for new connections",
        )

    if existing["archived_at"] is None:
        # Active row; caller should rotate, not reactivate.
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=existing["id"],
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider,
            caller_module=caller_module,
            operation="reactivate",
            error_code=VaultErrorCode.VAULT_DB_INSERT_FAILED.value,
            trace_id=trace_id,
        )
        raise VaultError(
            VaultErrorCode.VAULT_DB_INSERT_FAILED,
            f"credential id={existing['id']} is active; use rotate_credential_key",
        )

    credential_id: UUID = existing["id"]
    context = _build_encryption_context(
        tenant_id=tenant_id,
        user_id=user_id,
        provider=provider,
        credential_id=credential_id,
    )

    try:
        encrypted_api_key, encrypted_dek, nonce = encryption.encrypt_credential(
            plaintext=new_api_key,
            encryption_context=context,
        )
    except VaultError as exc:
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=credential_id,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider,
            caller_module=caller_module,
            operation="reactivate",
            error_code=exc.code.value,
            trace_id=trace_id,
        )
        raise

    config_json = _dumps_jsonb(new_config)

    try:
        async with pool.acquire() as cred_conn:
            async with cred_conn.transaction():
                updated = await cred_conn.fetchrow(
                    _UPDATE_REACTIVATE_SQL,
                    encrypted_api_key,
                    encrypted_dek,
                    nonce,
                    config_json,
                    credential_id,
                )
                if updated is None:
                    # Lost to a concurrent reactivate (another caller
                    # already cleared archived_at).
                    raise VaultError(
                        VaultErrorCode.VAULT_DB_NOT_FOUND,
                        f"credential id={credential_id} reactivated by another caller "
                        f"between lookup and update",
                    )
                await audit.write_audit_row(
                    pool=pool,
                    credential_id=credential_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    provider=provider,
                    caller_module=caller_module,
                    operation="reactivate",
                    success=True,
                    trace_id=trace_id,
                )
    except VaultError as exc:
        await _best_effort_failure_audit(
            pool=pool,
            credential_id=credential_id,
            tenant_id=tenant_id,
            user_id=user_id,
            provider=provider,
            caller_module=caller_module,
            operation="reactivate",
            error_code=exc.code.value,
            trace_id=trace_id,
        )
        raise

    return credential_id


# Sentinel values for audit rows written when the credential identity is
# unknown (ALLOWLIST violation on rotate, where the caller passes only
# credential_id and we haven't read the row yet). The schema requires NOT
# NULL tenant_id/user_id; the all-zero UUID is a recognizable forensic
# marker.
_NULL_UUID = UUID("00000000-0000-0000-0000-000000000000")
_UNKNOWN_PROVIDER = "unknown"
