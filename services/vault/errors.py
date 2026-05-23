"""Vault module structured error codes + exception types.

Per LOCKED-33 — all error codes are module-prefixed strings, persisted as-is
to ``external_integration_runs.error_code`` and ``credential_access_log.error_code``.
The enum value (not the enum member name) is the canonical wire format.
"""

from __future__ import annotations

from enum import Enum


class VaultErrorCode(str, Enum):
    """Structured error codes emitted by the vault module.

    Members map 1:1 to operational failure modes. The string value is the
    persisted wire form; downstream filters/dashboards match on the value.
    """

    VAULT_KMS_ENCRYPT_FAILED = "vault_kms_encrypt_failed"
    VAULT_KMS_DECRYPT_FAILED = "vault_kms_decrypt_failed"
    VAULT_KMS_CONTEXT_MISMATCH = "vault_kms_context_mismatch"
    VAULT_AES_GCM_TAG_MISMATCH = "vault_aes_gcm_tag_mismatch"
    VAULT_DB_INSERT_FAILED = "vault_db_insert_failed"
    VAULT_DB_NOT_FOUND = "vault_db_not_found"
    VAULT_CALLER_NOT_ALLOWED = "vault_caller_not_allowed"
    VAULT_AUDIT_LOG_WRITE_FAILED = "vault_audit_log_write_failed"


class VaultError(Exception):
    """Base exception for all vault module failures.

    Carries a ``VaultErrorCode`` so callers can branch on the structured code
    without parsing the message text.
    """

    def __init__(self, code: VaultErrorCode, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.__cause__ = cause

    def __repr__(self) -> str:
        return f"VaultError(code={self.code.value!r}, message={self.message!r})"


class VaultPermissionError(VaultError):
    """Raised when ``caller_module`` is not in the vault module's ALLOWLIST.

    Per LOCKED-42, the ALLOWLIST is the load-bearing app-layer guard. This
    exception is the boundary between "trusted caller" and everything else;
    no SQL or KMS call runs before the check passes.
    """

    def __init__(self, caller_module: str) -> None:
        super().__init__(
            VaultErrorCode.VAULT_CALLER_NOT_ALLOWED,
            f"Caller module {caller_module!r} is not in the vault ALLOWLIST",
        )
        self.caller_module = caller_module
