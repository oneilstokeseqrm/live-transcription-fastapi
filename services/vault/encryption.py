"""KMS envelope encryption for the vault module.

Pure crypto layer. No DB, no allowlist, no audit. Wraps a credential plaintext
with a fresh per-call data encryption key (DEK) that is itself wrapped by the
AWS KMS customer master key configured via ``EQ_VAULT_KMS_KEY_ALIAS``.

Invariants:

* **LOCKED-40** — every KMS call passes an ``EncryptionContext`` with exactly
  four keys ``{tenant_id, user_id, provider, credential_id}``. The IAM and key
  policies enforce ``ForAllValues:StringEquals + Null:false`` so KMS rejects
  any call missing or adding a key.
* **LOCKED-43** — every write mints a fresh 256-bit DEK via
  ``kms:GenerateDataKey`` and a fresh 96-bit nonce via ``os.urandom``. Nonce
  reuse on the same DEK is structurally impossible: the DEK is freshly minted
  alongside the nonce on each call.

Boto3 is synchronous; the repo's convention is to call it directly from async
functions (see ``services/aws_event_publisher.py``). KMS round-trip is
~10-30ms per call, acceptable for the credential-access volumes Phase 2
targets.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .errors import VaultError, VaultErrorCode

logger = logging.getLogger(__name__)

# AWS KMS access-denied error codes that surface as a 4-field EncryptionContext
# binding violation. KMS maps both context mismatches and policy denials to
# AccessDeniedException; we treat any AccessDenied on Decrypt/GenerateDataKey
# as VAULT_KMS_CONTEXT_MISMATCH because under our deployed policy, the policy
# layer is satisfied by IAM credentials before the call reaches KMS.
_CONTEXT_MISMATCH_ERROR_CODES = frozenset(
    {"AccessDeniedException", "InvalidCiphertextException"}
)

_REQUIRED_CONTEXT_KEYS = frozenset({"tenant_id", "user_id", "provider", "credential_id"})

_AES_GCM_NONCE_BYTES = 12
_DEK_SPEC = "AES_256"

_kms_client: Any = None


def _get_kms_client() -> Any:
    """Lazily create a process-wide KMS client.

    Module-level singleton to avoid re-establishing the boto3 client on every
    call (boto3 clients are thread-safe and cache connections internally).
    Tests inject a mock via the ``kms_client`` parameter on the public
    functions instead of monkey-patching this getter.
    """
    global _kms_client
    if _kms_client is None:
        region = os.environ.get("EQ_VAULT_AWS_REGION", "us-east-1")
        kwargs: dict[str, Any] = {"region_name": region}
        access_key = os.environ.get("EQ_VAULT_AWS_ACCESS_KEY_ID")
        secret_key = os.environ.get("EQ_VAULT_AWS_SECRET_ACCESS_KEY")
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key
        _kms_client = boto3.client("kms", **kwargs)
    return _kms_client


def _get_kms_key_id() -> str:
    alias = os.environ.get("EQ_VAULT_KMS_KEY_ALIAS")
    if not alias:
        raise VaultError(
            VaultErrorCode.VAULT_KMS_ENCRYPT_FAILED,
            "EQ_VAULT_KMS_KEY_ALIAS env var is not set",
        )
    return alias


def _validate_context(encryption_context: dict[str, str]) -> None:
    """Reject contexts that don't match the LOCKED-40 4-field shape.

    Fails closed BEFORE the KMS call so a misconfigured caller surfaces a
    structured ``VaultError`` rather than an opaque AccessDeniedException.
    """
    actual_keys = frozenset(encryption_context.keys())
    if actual_keys != _REQUIRED_CONTEXT_KEYS:
        missing = _REQUIRED_CONTEXT_KEYS - actual_keys
        extra = actual_keys - _REQUIRED_CONTEXT_KEYS
        raise VaultError(
            VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH,
            (
                "encryption_context must contain exactly "
                f"{sorted(_REQUIRED_CONTEXT_KEYS)}; missing={sorted(missing)}, "
                f"extra={sorted(extra)}"
            ),
        )
    for key, value in encryption_context.items():
        if not isinstance(value, str) or not value:
            raise VaultError(
                VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH,
                f"encryption_context[{key!r}] must be a non-empty string",
            )


def encrypt_credential(
    *,
    plaintext: str,
    encryption_context: dict[str, str],
    kms_client: Any | None = None,
) -> tuple[bytes, bytes, bytes]:
    """Encrypt a credential string with a fresh DEK + fresh nonce.

    Per LOCKED-43, this mints a new DEK and a new 12-byte nonce on every call;
    there is no path that reuses either. Per LOCKED-40, the 4-field
    ``encryption_context`` is bound to the DEK at GenerateDataKey time, and
    KMS will refuse to decrypt the DEK later under a different context.

    Returns ``(encrypted_api_key, encrypted_dek, nonce)``:

    * ``encrypted_api_key`` — AES-256-GCM ciphertext with the 16-byte GCM tag
      appended (the standard ``cryptography`` library output shape).
    * ``encrypted_dek`` — KMS-wrapped DEK (the ``CiphertextBlob`` from
      GenerateDataKey).
    * ``nonce`` — the 12-byte random nonce used for AES-GCM.

    All three are persisted to ``vault.user_credentials``.
    """
    _validate_context(encryption_context)
    client = kms_client if kms_client is not None else _get_kms_client()
    key_id = _get_kms_key_id()

    try:
        resp = client.generate_data_key(
            KeyId=key_id,
            KeySpec=_DEK_SPEC,
            EncryptionContext=encryption_context,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in _CONTEXT_MISMATCH_ERROR_CODES:
            raise VaultError(
                VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH,
                f"KMS rejected GenerateDataKey: {error_code}",
                cause=exc,
            ) from exc
        raise VaultError(
            VaultErrorCode.VAULT_KMS_ENCRYPT_FAILED,
            f"KMS GenerateDataKey failed: {error_code or 'unknown'}",
            cause=exc,
        ) from exc

    dek_plaintext = resp["Plaintext"]
    encrypted_dek = resp["CiphertextBlob"]
    nonce = os.urandom(_AES_GCM_NONCE_BYTES)

    try:
        aesgcm = AESGCM(dek_plaintext)
        encrypted_api_key = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    finally:
        # Best-effort zero-out of the DEK plaintext. CPython will not reclaim
        # the original buffer immediately, but explicitly dropping the
        # reference shortens the window the secret lives in process memory.
        del dek_plaintext

    return encrypted_api_key, encrypted_dek, nonce


def decrypt_credential(
    *,
    encrypted_api_key: bytes,
    encrypted_dek: bytes,
    nonce: bytes,
    encryption_context: dict[str, str],
    kms_client: Any | None = None,
) -> str:
    """Decrypt a credential previously written by ``encrypt_credential``.

    KMS will refuse the unwrap if ``encryption_context`` doesn't match the
    context that was bound at GenerateDataKey time (LOCKED-40 — the binding
    is per-row because ``credential_id`` is one of the four fields).
    """
    _validate_context(encryption_context)
    if len(nonce) != _AES_GCM_NONCE_BYTES:
        raise VaultError(
            VaultErrorCode.VAULT_AES_GCM_TAG_MISMATCH,
            f"nonce must be {_AES_GCM_NONCE_BYTES} bytes, got {len(nonce)}",
        )

    client = kms_client if kms_client is not None else _get_kms_client()

    try:
        resp = client.decrypt(
            CiphertextBlob=encrypted_dek,
            EncryptionContext=encryption_context,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in _CONTEXT_MISMATCH_ERROR_CODES:
            raise VaultError(
                VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH,
                f"KMS rejected Decrypt: {error_code}",
                cause=exc,
            ) from exc
        raise VaultError(
            VaultErrorCode.VAULT_KMS_DECRYPT_FAILED,
            f"KMS Decrypt failed: {error_code or 'unknown'}",
            cause=exc,
        ) from exc

    dek_plaintext = resp["Plaintext"]
    try:
        aesgcm = AESGCM(dek_plaintext)
        try:
            plaintext_bytes = aesgcm.decrypt(nonce, encrypted_api_key, None)
        except InvalidTag as exc:
            # GCM auth tag failed — either ciphertext was tampered with or
            # nonce/key don't match what was used to encrypt. Either way the
            # row is unusable; the caller should mark the credential as
            # corrupt rather than retry.
            raise VaultError(
                VaultErrorCode.VAULT_AES_GCM_TAG_MISMATCH,
                "AES-GCM tag mismatch on credential decrypt",
                cause=exc,
            ) from exc
    finally:
        del dek_plaintext

    return plaintext_bytes.decode("utf-8")
