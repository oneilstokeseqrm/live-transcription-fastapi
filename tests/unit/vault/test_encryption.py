"""Unit tests for ``services.vault.encryption``.

AsyncMock-style per ``feedback_test_pattern_no_docker.md`` — no real KMS,
no Docker, no network. The KMS client is injected as a ``MagicMock`` so we
control the GenerateDataKey / Decrypt response shapes deterministically.

Tests cover:

* LOCKED-43 — fresh DEK + fresh nonce on every encrypt
* LOCKED-40 — 4-field EncryptionContext validated BEFORE KMS, and KMS
  AccessDenied surfaces as ``VAULT_KMS_CONTEXT_MISMATCH``
* Round-trip — encrypt then decrypt with the same context returns plaintext
* AES-GCM tag mismatch — tampered ciphertext raises ``VAULT_AES_GCM_TAG_MISMATCH``
* Other KMS failures map to ``VAULT_KMS_ENCRYPT_FAILED`` /
  ``VAULT_KMS_DECRYPT_FAILED``
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from services.vault import encryption
from services.vault.errors import VaultError, VaultErrorCode

# A fixed 32-byte DEK so round-trip tests are deterministic. The real KMS
# returns a random DEK each call; here we mint one constant DEK for the mock
# and check that AES-GCM still round-trips correctly.
_FAKE_DEK = bytes(range(32))

_VALID_CONTEXT = {
    "tenant_id": "11111111-1111-4111-8111-111111111111",
    "user_id": "b0000000-0000-4000-8000-000000000002",
    "provider": "granola",
    "credential_id": "00000000-0000-4000-8000-000000000003",
}


@pytest.fixture(autouse=True)
def _set_kms_env(monkeypatch):
    """Ensure ``EQ_VAULT_KMS_KEY_ALIAS`` is set so ``_get_kms_key_id`` succeeds.

    Tests that exercise the missing-env path override this in-test by deleting
    the env var locally.
    """
    monkeypatch.setenv("EQ_VAULT_KMS_KEY_ALIAS", "alias/eq-user-secrets-test")
    monkeypatch.setenv("EQ_VAULT_AWS_REGION", "us-east-1")


def _make_kms_client(*, decrypt_dek: bytes = _FAKE_DEK) -> MagicMock:
    """Build a KMS mock that returns ``_FAKE_DEK`` for GenerateDataKey and
    the supplied ``decrypt_dek`` for Decrypt."""
    client = MagicMock()
    client.generate_data_key.return_value = {
        "Plaintext": _FAKE_DEK,
        "CiphertextBlob": b"wrapped-dek-bytes",
        "KeyId": "arn:aws:kms:us-east-1:211125681610:key/test",
    }
    client.decrypt.return_value = {
        "Plaintext": decrypt_dek,
        "KeyId": "arn:aws:kms:us-east-1:211125681610:key/test",
    }
    return client


def _client_error(code: str) -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": "test"}},
        operation_name="GenerateDataKey",
    )


class TestContextValidation:
    """LOCKED-40 — fail closed BEFORE any KMS call if context shape is wrong."""

    def test_missing_credential_id_rejected(self):
        bad = {k: v for k, v in _VALID_CONTEXT.items() if k != "credential_id"}
        with pytest.raises(VaultError) as exc_info:
            encryption.encrypt_credential(
                plaintext="grn_test", encryption_context=bad, kms_client=_make_kms_client()
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH

    def test_missing_tenant_id_rejected(self):
        bad = {k: v for k, v in _VALID_CONTEXT.items() if k != "tenant_id"}
        with pytest.raises(VaultError) as exc_info:
            encryption.encrypt_credential(
                plaintext="grn_test", encryption_context=bad, kms_client=_make_kms_client()
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH

    def test_missing_user_id_rejected(self):
        bad = {k: v for k, v in _VALID_CONTEXT.items() if k != "user_id"}
        with pytest.raises(VaultError) as exc_info:
            encryption.encrypt_credential(
                plaintext="grn_test", encryption_context=bad, kms_client=_make_kms_client()
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH

    def test_missing_provider_rejected(self):
        bad = {k: v for k, v in _VALID_CONTEXT.items() if k != "provider"}
        with pytest.raises(VaultError) as exc_info:
            encryption.encrypt_credential(
                plaintext="grn_test", encryption_context=bad, kms_client=_make_kms_client()
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH

    def test_extra_key_rejected(self):
        bad = {**_VALID_CONTEXT, "extra": "value"}
        with pytest.raises(VaultError) as exc_info:
            encryption.encrypt_credential(
                plaintext="grn_test", encryption_context=bad, kms_client=_make_kms_client()
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH

    def test_empty_string_value_rejected(self):
        bad = {**_VALID_CONTEXT, "tenant_id": ""}
        with pytest.raises(VaultError) as exc_info:
            encryption.encrypt_credential(
                plaintext="grn_test", encryption_context=bad, kms_client=_make_kms_client()
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH

    def test_non_string_value_rejected(self):
        bad: dict[str, str] = {**_VALID_CONTEXT, "tenant_id": 42}  # type: ignore[dict-item]
        with pytest.raises(VaultError) as exc_info:
            encryption.encrypt_credential(
                plaintext="grn_test", encryption_context=bad, kms_client=_make_kms_client()
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH

    def test_kms_not_called_when_context_invalid(self):
        kms = _make_kms_client()
        with pytest.raises(VaultError):
            encryption.encrypt_credential(
                plaintext="grn_test",
                encryption_context={"tenant_id": "x"},
                kms_client=kms,
            )
        kms.generate_data_key.assert_not_called()


class TestEncryptionRoundTrip:
    """End-to-end: encrypt then decrypt returns the original plaintext."""

    def test_round_trip_returns_plaintext(self):
        kms = _make_kms_client()
        plaintext = "grn_test_api_key_abcdef123"
        ciphertext, encrypted_dek, nonce = encryption.encrypt_credential(
            plaintext=plaintext, encryption_context=_VALID_CONTEXT, kms_client=kms
        )

        recovered = encryption.decrypt_credential(
            encrypted_api_key=ciphertext,
            encrypted_dek=encrypted_dek,
            nonce=nonce,
            encryption_context=_VALID_CONTEXT,
            kms_client=kms,
        )
        assert recovered == plaintext

    def test_round_trip_with_4_field_context_passed_to_kms(self):
        kms = _make_kms_client()
        encryption.encrypt_credential(
            plaintext="grn_test", encryption_context=_VALID_CONTEXT, kms_client=kms
        )
        kms.generate_data_key.assert_called_once()
        call_kwargs = kms.generate_data_key.call_args.kwargs
        assert call_kwargs["EncryptionContext"] == _VALID_CONTEXT
        assert call_kwargs["KeySpec"] == "AES_256"


class TestFreshDekAndNonce:
    """LOCKED-43 — every encrypt mints a fresh DEK + fresh nonce."""

    def test_two_encrypts_produce_different_nonces(self):
        kms = _make_kms_client()
        _, _, nonce1 = encryption.encrypt_credential(
            plaintext="grn_a", encryption_context=_VALID_CONTEXT, kms_client=kms
        )
        _, _, nonce2 = encryption.encrypt_credential(
            plaintext="grn_b", encryption_context=_VALID_CONTEXT, kms_client=kms
        )
        assert nonce1 != nonce2, "LOCKED-43 violated: nonce reused across encrypts"

    def test_nonce_is_12_bytes(self):
        kms = _make_kms_client()
        _, _, nonce = encryption.encrypt_credential(
            plaintext="grn_test", encryption_context=_VALID_CONTEXT, kms_client=kms
        )
        assert len(nonce) == 12

    def test_each_encrypt_calls_kms_generate_data_key(self):
        """LOCKED-43 — DEK freshness comes from KMS minting a new DEK each call.

        We can't directly observe "different DEK plaintext" without the real
        KMS service (the mock returns the same fake DEK), so we assert the
        contract: every encrypt makes its own GenerateDataKey call.
        """
        kms = _make_kms_client()
        encryption.encrypt_credential(
            plaintext="grn_a", encryption_context=_VALID_CONTEXT, kms_client=kms
        )
        encryption.encrypt_credential(
            plaintext="grn_b", encryption_context=_VALID_CONTEXT, kms_client=kms
        )
        assert kms.generate_data_key.call_count == 2


class TestKmsErrorMapping:
    """LOCKED-40 enforcement via KMS error responses."""

    def test_kms_access_denied_on_encrypt_maps_to_encrypt_failed(self):
        """AccessDeniedException can be IAM denial, key disable, or policy
        rotation — NOT specifically a context mismatch. Map to encrypt_failed
        so operators investigate the IAM/key state, not the credential row."""
        kms = _make_kms_client()
        kms.generate_data_key.side_effect = _client_error("AccessDeniedException")
        with pytest.raises(VaultError) as exc_info:
            encryption.encrypt_credential(
                plaintext="grn_test", encryption_context=_VALID_CONTEXT, kms_client=kms
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_ENCRYPT_FAILED
        assert "AccessDeniedException" in str(exc_info.value)

    def test_kms_access_denied_on_decrypt_maps_to_decrypt_failed(self):
        """Same rationale as encrypt path — AccessDenied is too broad to
        attribute to a context-binding violation."""
        kms = _make_kms_client()
        kms.decrypt.side_effect = _client_error("AccessDeniedException")
        with pytest.raises(VaultError) as exc_info:
            encryption.decrypt_credential(
                encrypted_api_key=b"abc",
                encrypted_dek=b"wrapped",
                nonce=b"\x00" * 12,
                encryption_context=_VALID_CONTEXT,
                kms_client=kms,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_DECRYPT_FAILED
        assert "AccessDeniedException" in str(exc_info.value)

    def test_invalid_ciphertext_maps_to_context_mismatch(self):
        kms = _make_kms_client()
        kms.decrypt.side_effect = _client_error("InvalidCiphertextException")
        with pytest.raises(VaultError) as exc_info:
            encryption.decrypt_credential(
                encrypted_api_key=b"abc",
                encrypted_dek=b"wrapped",
                nonce=b"\x00" * 12,
                encryption_context=_VALID_CONTEXT,
                kms_client=kms,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH

    def test_other_kms_error_on_encrypt_maps_to_encrypt_failed(self):
        kms = _make_kms_client()
        kms.generate_data_key.side_effect = _client_error("ThrottlingException")
        with pytest.raises(VaultError) as exc_info:
            encryption.encrypt_credential(
                plaintext="grn_test", encryption_context=_VALID_CONTEXT, kms_client=kms
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_ENCRYPT_FAILED

    def test_other_kms_error_on_decrypt_maps_to_decrypt_failed(self):
        kms = _make_kms_client()
        kms.decrypt.side_effect = _client_error("ThrottlingException")
        with pytest.raises(VaultError) as exc_info:
            encryption.decrypt_credential(
                encrypted_api_key=b"abc",
                encrypted_dek=b"wrapped",
                nonce=b"\x00" * 12,
                encryption_context=_VALID_CONTEXT,
                kms_client=kms,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_DECRYPT_FAILED


class TestAesGcmTagMismatch:
    """Tampered ciphertext or wrong DEK surfaces ``VAULT_AES_GCM_TAG_MISMATCH``."""

    def test_tampered_ciphertext_rejected(self):
        kms = _make_kms_client()
        ciphertext, encrypted_dek, nonce = encryption.encrypt_credential(
            plaintext="grn_test", encryption_context=_VALID_CONTEXT, kms_client=kms
        )
        tampered = bytearray(ciphertext)
        tampered[0] ^= 0xFF
        with pytest.raises(VaultError) as exc_info:
            encryption.decrypt_credential(
                encrypted_api_key=bytes(tampered),
                encrypted_dek=encrypted_dek,
                nonce=nonce,
                encryption_context=_VALID_CONTEXT,
                kms_client=kms,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_AES_GCM_TAG_MISMATCH

    def test_wrong_dek_rejected(self):
        kms = _make_kms_client()
        ciphertext, encrypted_dek, nonce = encryption.encrypt_credential(
            plaintext="grn_test", encryption_context=_VALID_CONTEXT, kms_client=kms
        )
        # Simulate KMS returning a different DEK on decrypt (e.g., because
        # ciphertext blob was swapped). AES-GCM auth tag will fail.
        kms.decrypt.return_value = {"Plaintext": bytes(reversed(_FAKE_DEK)), "KeyId": "x"}
        with pytest.raises(VaultError) as exc_info:
            encryption.decrypt_credential(
                encrypted_api_key=ciphertext,
                encrypted_dek=encrypted_dek,
                nonce=nonce,
                encryption_context=_VALID_CONTEXT,
                kms_client=kms,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_AES_GCM_TAG_MISMATCH

    def test_wrong_nonce_length_rejected_pre_kms(self):
        kms = _make_kms_client()
        with pytest.raises(VaultError) as exc_info:
            encryption.decrypt_credential(
                encrypted_api_key=b"abc",
                encrypted_dek=b"wrapped",
                nonce=b"\x00" * 8,  # too short
                encryption_context=_VALID_CONTEXT,
                kms_client=kms,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_AES_GCM_TAG_MISMATCH
        kms.decrypt.assert_not_called()


class TestBotoCoreErrorWrapping:
    """Codex R6 [P1]: non-ClientError botocore exceptions (missing AWS
    credentials, network errors, timeouts) must also map to structured
    VaultError so the accessor layer's audit + structured-error contract
    holds during AWS misconfiguration or transient outages."""

    def _make_botocore_error(self) -> Exception:
        """Use a real botocore exception to exercise the BotoCoreError branch."""
        from botocore.exceptions import NoCredentialsError

        return NoCredentialsError()

    def test_botocore_error_on_encrypt_maps_to_encrypt_failed(self):
        kms = _make_kms_client()
        kms.generate_data_key.side_effect = self._make_botocore_error()
        with pytest.raises(VaultError) as exc_info:
            encryption.encrypt_credential(
                plaintext="grn_test",
                encryption_context=_VALID_CONTEXT,
                kms_client=kms,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_ENCRYPT_FAILED
        assert "botocore" in str(exc_info.value).lower() or "NoCredentialsError" in str(
            exc_info.value
        )

    def test_botocore_error_on_decrypt_maps_to_decrypt_failed(self):
        kms = _make_kms_client()
        kms.decrypt.side_effect = self._make_botocore_error()
        with pytest.raises(VaultError) as exc_info:
            encryption.decrypt_credential(
                encrypted_api_key=b"abc",
                encrypted_dek=b"wrapped",
                nonce=b"\x00" * 12,
                encryption_context=_VALID_CONTEXT,
                kms_client=kms,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_DECRYPT_FAILED

    def test_client_construction_error_wrapped(self, monkeypatch):
        """Codex R7 [P2]: boto session-construction failures (e.g.,
        ProfileNotFound) must also surface as structured VaultError.
        The client lookup must run INSIDE the try block.
        """
        from botocore.exceptions import ProfileNotFound

        def _bad_get_kms_client():
            raise ProfileNotFound(profile="nonexistent")

        monkeypatch.setattr(encryption, "_get_kms_client", _bad_get_kms_client)
        # Pass kms_client=None to force the production code path that calls
        # _get_kms_client().
        with pytest.raises(VaultError) as exc_info:
            encryption.encrypt_credential(
                plaintext="grn_test",
                encryption_context=_VALID_CONTEXT,
                kms_client=None,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_ENCRYPT_FAILED
        # Decrypt path mirror
        with pytest.raises(VaultError) as exc_info2:
            encryption.decrypt_credential(
                encrypted_api_key=b"abc",
                encrypted_dek=b"wrapped",
                nonce=b"\x00" * 12,
                encryption_context=_VALID_CONTEXT,
                kms_client=None,
            )
        assert exc_info2.value.code == VaultErrorCode.VAULT_KMS_DECRYPT_FAILED


class TestEnvVarRequirements:
    """``EQ_VAULT_KMS_KEY_ALIAS`` is required for any encrypt call."""

    def test_missing_kms_key_alias_raises(self, monkeypatch):
        monkeypatch.delenv("EQ_VAULT_KMS_KEY_ALIAS", raising=False)
        kms = _make_kms_client()
        with pytest.raises(VaultError) as exc_info:
            encryption.encrypt_credential(
                plaintext="grn_test", encryption_context=_VALID_CONTEXT, kms_client=kms
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_ENCRYPT_FAILED
