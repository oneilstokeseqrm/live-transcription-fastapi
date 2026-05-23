"""Vault module — KMS-backed credential storage for third-party API keys.

Phase 2b of the Granola integration. See ``services/vault/README.md`` for the
architectural overview, LOCKED-40/42/43 invariants, infrastructure inventory,
and rotation procedures.

Public API:

* :class:`GranolaCredential` — decrypted credential snapshot returned by reads.
* :func:`get_granola_credential_for_user` — read-and-decrypt accessor.
* :func:`store_credential` — encrypt-and-insert accessor.
* :func:`rotate_credential_key` — replace key material on an existing row.
* :data:`ALLOWLIST` — caller modules permitted to use the accessor.
* :class:`VaultError`, :class:`VaultPermissionError`, :class:`VaultErrorCode`
  — structured failure signaling.

Callers MUST pass their own ``__name__`` (or a string matching one of the
:data:`ALLOWLIST` entries) as the ``caller_module`` argument. Adding a new
caller requires editing :data:`ALLOWLIST` in
:mod:`services.vault.user_credentials`.
"""

from __future__ import annotations

from .errors import VaultError, VaultErrorCode, VaultPermissionError
from .user_credentials import (
    ALLOWLIST,
    GranolaCredential,
    get_granola_credential_for_user,
    rotate_credential_key,
    store_credential,
)

__all__ = [
    "ALLOWLIST",
    "GranolaCredential",
    "VaultError",
    "VaultErrorCode",
    "VaultPermissionError",
    "get_granola_credential_for_user",
    "rotate_credential_key",
    "store_credential",
]
