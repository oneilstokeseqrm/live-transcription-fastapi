"""Unit tests for ``services.vault.user_credentials``.

AsyncMock-style per ``feedback_test_pattern_no_docker.md``. The asyncpg
``Connection`` is mocked, ``encryption`` is monkeypatched to deterministic
fakes, and ``audit.write_audit_row`` is replaced with a spy so we can assert
on every audit-row write.

Coverage:

* LOCKED-42 — ALLOWLIST gate fails closed on every accessor; audit row
  is still written
* LOCKED-40 — 4-field EncryptionContext built correctly (including the
  credential row's UUID as the fourth field)
* LOCKED-41 — ``tenant_id`` / ``user_id`` flow as explicit arguments;
  the accessor never reaches for any global
* LOCKED-43 — ``store_credential`` and ``rotate_credential_key`` both call
  ``encryption.encrypt_credential`` once per invocation (which itself mints a
  fresh DEK + fresh nonce, asserted in test_encryption.py)
* Every accessor call results in at least one audit-row write
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.vault import user_credentials
from services.vault.errors import VaultError, VaultErrorCode, VaultPermissionError

_ALLOWED_CALLER = "services.granola_ingestion.adapter"
_BLOCKED_CALLER = "not.in.allowlist"


@pytest.fixture
def fake_conn() -> AsyncMock:
    """asyncpg ``Connection`` mock with overridable fetchrow + execute."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def audit_spy(monkeypatch):
    """Replace audit.write_audit_row with an AsyncMock spy.

    The spy mimics the real function's return type so callers receive a UUID.
    Tests inspect ``spy.await_args_list`` to assert on each audit-row write.
    """
    spy = AsyncMock(side_effect=lambda **kw: uuid.uuid4())
    monkeypatch.setattr(user_credentials.audit, "write_audit_row", spy)
    return spy


@pytest.fixture
def encrypt_spy(monkeypatch):
    """Stub encryption.encrypt_credential to return deterministic bytes."""
    counter = {"n": 0}

    def fake_encrypt(*, plaintext: str, encryption_context: dict[str, str], **_kw):
        counter["n"] += 1
        # Different bytes per call so test_fresh_dek_and_nonce assertions hold.
        return (
            f"ciphertext-{counter['n']}-{plaintext}".encode(),
            f"wrapped-dek-{counter['n']}".encode(),
            bytes([counter["n"]] * 12),
        )

    spy = MagicMock(side_effect=fake_encrypt)
    monkeypatch.setattr(user_credentials.encryption, "encrypt_credential", spy)
    return spy


@pytest.fixture
def decrypt_spy(monkeypatch):
    """Stub encryption.decrypt_credential to return a fixed plaintext."""

    def fake_decrypt(*, encrypted_api_key: bytes, encryption_context: dict[str, str], **_kw):
        return f"grn_decrypted_{encryption_context['credential_id'][:8]}"

    spy = MagicMock(side_effect=fake_decrypt)
    monkeypatch.setattr(user_credentials.encryption, "decrypt_credential", spy)
    return spy


def _credential_row(*, credential_id: uuid.UUID, tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    """Fake asyncpg ``Record`` (dict access works on real Records too)."""
    return {
        "id": credential_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "provider": "granola",
        "encrypted_api_key": b"ciphertext",
        "encrypted_dek": b"wrapped-dek",
        "nonce": b"\x00" * 12,
        "config": {"folder_id": "fol_abc"},
        "status": "active",
        "last_polled_at": None,
        "last_error": None,
        "consecutive_failures": 0,
        "created_at": MagicMock(),
        "updated_at": MagicMock(),
        "archived_at": None,
    }


class TestGetGranolaCredential:
    @pytest.mark.asyncio
    async def test_caller_not_in_allowlist_raises_and_audits(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock
    ):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        with pytest.raises(VaultPermissionError):
            await user_credentials.get_granola_credential_for_user(
                tenant_id=tenant_id,
                user_id=user_id,
                caller_module=_BLOCKED_CALLER,
                conn=fake_conn,
            )
        # Audit row written even though access was denied
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "read"
        assert kw["success"] is False
        assert kw["error_code"] == VaultErrorCode.VAULT_CALLER_NOT_ALLOWED.value
        assert kw["caller_module"] == _BLOCKED_CALLER
        assert kw["credential_id"] is None
        # No SQL ran
        fake_conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_row_returns_none_and_audits_success(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        fake_conn.fetchrow.return_value = None
        result = await user_credentials.get_granola_credential_for_user(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            caller_module=_ALLOWED_CALLER,
            conn=fake_conn,
        )
        assert result is None
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "read"
        assert kw["success"] is True
        assert kw["credential_id"] is None
        decrypt_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_returns_credential_with_decrypted_key(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        fake_conn.fetchrow.return_value = _credential_row(
            credential_id=credential_id, tenant_id=tenant_id, user_id=user_id
        )

        result = await user_credentials.get_granola_credential_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
            caller_module=_ALLOWED_CALLER,
            conn=fake_conn,
            trace_id="trace-1",
        )

        assert result is not None
        assert result.id == credential_id
        assert result.tenant_id == tenant_id
        assert result.user_id == user_id
        assert result.api_key.startswith("grn_decrypted_")
        assert result.config == {"folder_id": "fol_abc"}

        # Audit row: success, credential_id matches
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "read"
        assert kw["success"] is True
        assert kw["credential_id"] == credential_id
        assert kw["trace_id"] == "trace-1"

    @pytest.mark.asyncio
    async def test_decrypt_passes_4_field_context_with_row_id(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        """LOCKED-40 — credential_id from the row, not the caller."""
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        fake_conn.fetchrow.return_value = _credential_row(
            credential_id=credential_id, tenant_id=tenant_id, user_id=user_id
        )

        await user_credentials.get_granola_credential_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
            caller_module=_ALLOWED_CALLER,
            conn=fake_conn,
        )

        decrypt_spy.assert_called_once()
        ctx = decrypt_spy.call_args.kwargs["encryption_context"]
        assert ctx == {
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "provider": "granola",
            "credential_id": str(credential_id),
        }

    @pytest.mark.asyncio
    async def test_decrypt_failure_audits_and_reraises(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        credential_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        fake_conn.fetchrow.return_value = _credential_row(
            credential_id=credential_id, tenant_id=tenant_id, user_id=user_id
        )
        decrypt_spy.side_effect = VaultError(
            VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH, "tampered"
        )

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.get_granola_credential_for_user(
                tenant_id=tenant_id,
                user_id=user_id,
                caller_module=_ALLOWED_CALLER,
                conn=fake_conn,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["success"] is False
        assert kw["error_code"] == VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH.value
        assert kw["credential_id"] == credential_id

    @pytest.mark.asyncio
    async def test_select_filters_archived_at_is_null(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        """The accessor must not return archived rows."""
        fake_conn.fetchrow.return_value = None
        await user_credentials.get_granola_credential_for_user(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            caller_module=_ALLOWED_CALLER,
            conn=fake_conn,
        )
        sql = fake_conn.fetchrow.await_args.args[0]
        assert "archived_at IS NULL" in sql
        assert "provider = $3" in sql

    @pytest.mark.asyncio
    async def test_select_filters_tenant_and_user(self, fake_conn, audit_spy, decrypt_spy):
        """LOCKED-41 — the SELECT MUST include tenant_id in the WHERE."""
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        fake_conn.fetchrow.return_value = None
        await user_credentials.get_granola_credential_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
            caller_module=_ALLOWED_CALLER,
            conn=fake_conn,
        )
        args = fake_conn.fetchrow.await_args.args
        assert args[1] == tenant_id
        assert args[2] == user_id
        assert args[3] == "granola"


class TestStoreCredential:
    @pytest.mark.asyncio
    async def test_caller_not_in_allowlist_raises_and_audits(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        with pytest.raises(VaultPermissionError):
            await user_credentials.store_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                api_key="grn_test",
                config={"folder_id": "fol_x"},
                caller_module=_BLOCKED_CALLER,
                conn=fake_conn,
            )
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "write"
        assert kw["success"] is False
        assert kw["error_code"] == VaultErrorCode.VAULT_CALLER_NOT_ALLOWED.value
        encrypt_spy.assert_not_called()
        fake_conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_inserts_and_audits(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        new_id = await user_credentials.store_credential(
            tenant_id=tenant_id,
            user_id=user_id,
            provider="granola",
            api_key="grn_test_123",
            config={"folder_id": "fol_abc", "folder_name": "EQ"},
            caller_module=_ALLOWED_CALLER,
            conn=fake_conn,
        )

        assert isinstance(new_id, uuid.UUID)
        encrypt_spy.assert_called_once()
        ctx = encrypt_spy.call_args.kwargs["encryption_context"]
        # LOCKED-40 — the new row's UUID is the credential_id in the context
        assert ctx == {
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "provider": "granola",
            "credential_id": str(new_id),
        }
        # INSERT call
        fake_conn.execute.assert_awaited_once()
        execute_args = fake_conn.execute.await_args.args
        assert "INSERT INTO vault.user_credentials" in execute_args[0]
        assert execute_args[1] == new_id
        assert execute_args[2] == tenant_id
        assert execute_args[3] == user_id
        assert execute_args[4] == "granola"
        # Audit row
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "write"
        assert kw["success"] is True
        assert kw["credential_id"] == new_id

    @pytest.mark.asyncio
    async def test_encrypt_failure_audits_and_reraises_without_insert(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        encrypt_spy.side_effect = VaultError(
            VaultErrorCode.VAULT_KMS_ENCRYPT_FAILED, "kms down"
        )
        with pytest.raises(VaultError) as exc_info:
            await user_credentials.store_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                api_key="grn_test",
                config={"folder_id": "fol_x"},
                caller_module=_ALLOWED_CALLER,
                conn=fake_conn,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_ENCRYPT_FAILED
        fake_conn.execute.assert_not_called()
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["success"] is False

    @pytest.mark.asyncio
    async def test_insert_failure_audits_and_wraps_as_db_insert_failed(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        fake_conn.execute = AsyncMock(side_effect=RuntimeError("unique violation"))
        with pytest.raises(VaultError) as exc_info:
            await user_credentials.store_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                api_key="grn_test",
                config={"folder_id": "fol_x"},
                caller_module=_ALLOWED_CALLER,
                conn=fake_conn,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_INSERT_FAILED
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["success"] is False
        assert (
            audit_spy.await_args.kwargs["error_code"]
            == VaultErrorCode.VAULT_DB_INSERT_FAILED.value
        )

    @pytest.mark.asyncio
    async def test_two_consecutive_stores_call_encrypt_twice(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """LOCKED-43 contract — every write triggers its own encrypt call.

        The encrypt function itself is what mints a fresh DEK + nonce on
        every call (verified in test_encryption.py). user_credentials's
        contract is: never short-circuit, never reuse stored encryption
        output.
        """
        kwargs = dict(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider="granola",
            api_key="grn_test",
            config={"folder_id": "fol_x"},
            caller_module=_ALLOWED_CALLER,
            conn=fake_conn,
        )
        await user_credentials.store_credential(**kwargs)
        await user_credentials.store_credential(**kwargs)
        assert encrypt_spy.call_count == 2


class TestRotateCredentialKey:
    @pytest.mark.asyncio
    async def test_caller_not_in_allowlist_raises_and_audits(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        with pytest.raises(VaultPermissionError):
            await user_credentials.rotate_credential_key(
                credential_id=uuid.uuid4(),
                new_api_key="grn_new",
                caller_module=_BLOCKED_CALLER,
                conn=fake_conn,
            )
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "rotate"
        assert kw["success"] is False
        assert kw["error_code"] == VaultErrorCode.VAULT_CALLER_NOT_ALLOWED.value
        encrypt_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_credential_not_found_raises_db_not_found_and_audits(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        fake_conn.fetchrow.return_value = None
        with pytest.raises(VaultError) as exc_info:
            await user_credentials.rotate_credential_key(
                credential_id=uuid.uuid4(),
                new_api_key="grn_new",
                caller_module=_ALLOWED_CALLER,
                conn=fake_conn,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_NOT_FOUND
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["operation"] == "rotate"
        assert audit_spy.await_args.kwargs["success"] is False
        encrypt_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_rotates_with_same_credential_id_in_context(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()

        # First fetchrow: identity lookup; second fetchrow: UPDATE RETURNING
        fake_conn.fetchrow.side_effect = [
            {"tenant_id": tenant_id, "user_id": user_id, "provider": "granola"},
            {"tenant_id": tenant_id, "user_id": user_id, "provider": "granola"},
        ]

        await user_credentials.rotate_credential_key(
            credential_id=credential_id,
            new_api_key="grn_new_key",
            caller_module=_ALLOWED_CALLER,
            conn=fake_conn,
        )

        encrypt_spy.assert_called_once()
        ctx = encrypt_spy.call_args.kwargs["encryption_context"]
        assert ctx == {
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "provider": "granola",
            "credential_id": str(credential_id),
        }
        # Audit row: success rotate
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "rotate"
        assert kw["success"] is True
        assert kw["credential_id"] == credential_id

    @pytest.mark.asyncio
    async def test_archive_race_between_lookup_and_update_audits_not_found(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        # Identity lookup succeeds; UPDATE RETURNING returns nothing
        fake_conn.fetchrow.side_effect = [
            {"tenant_id": tenant_id, "user_id": user_id, "provider": "granola"},
            None,
        ]

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.rotate_credential_key(
                credential_id=credential_id,
                new_api_key="grn_new",
                caller_module=_ALLOWED_CALLER,
                conn=fake_conn,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_NOT_FOUND
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["success"] is False

    @pytest.mark.asyncio
    async def test_two_consecutive_rotates_each_call_encrypt(
        self, fake_conn: AsyncMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """LOCKED-43 — each rotate mints a fresh DEK + nonce via encrypt."""
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        identity = {"tenant_id": tenant_id, "user_id": user_id, "provider": "granola"}
        update_returning = {"tenant_id": tenant_id, "user_id": user_id, "provider": "granola"}
        fake_conn.fetchrow.side_effect = [
            identity, update_returning, identity, update_returning,
        ]

        await user_credentials.rotate_credential_key(
            credential_id=credential_id,
            new_api_key="grn_a",
            caller_module=_ALLOWED_CALLER,
            conn=fake_conn,
        )
        await user_credentials.rotate_credential_key(
            credential_id=credential_id,
            new_api_key="grn_b",
            caller_module=_ALLOWED_CALLER,
            conn=fake_conn,
        )
        assert encrypt_spy.call_count == 2


class TestAllowlistDefaults:
    def test_allowlist_contains_expected_three_callers(self):
        assert user_credentials.ALLOWLIST == frozenset(
            {
                "services.granola_ingestion.adapter",
                "services.granola_ingestion.scheduler",
                "routers.granola",
            }
        )

    def test_allowlist_is_frozen(self):
        # frozenset has no .add method — guards against in-process mutation
        assert not hasattr(user_credentials.ALLOWLIST, "add")
