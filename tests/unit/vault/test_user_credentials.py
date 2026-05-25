"""Unit tests for ``services.vault.user_credentials``.

AsyncMock-style per ``feedback_test_pattern_no_docker.md``. The asyncpg
``Pool`` is mocked to hand out separate connections per ``acquire()`` call
so tests can assert on the audit-on-separate-connection contract.

``encryption`` is monkeypatched to deterministic fakes and
``audit.write_audit_row`` is replaced with a spy so we can assert on every
audit-row write.

Coverage:

* LOCKED-42 — ALLOWLIST gate fails closed on every accessor; audit row is
  still written (via the failure-audit path) on a dedicated connection
* LOCKED-40 — 4-field EncryptionContext built correctly (including the
  credential row's UUID as the fourth field)
* LOCKED-41 — ``tenant_id`` / ``user_id`` flow as explicit arguments; the
  accessor never reaches for any global
* LOCKED-43 — ``store_credential``, ``rotate_credential_key``, and
  ``reactivate_credential`` all call ``encryption.encrypt_credential``
  once per invocation (which mints a fresh DEK + fresh nonce; asserted in
  test_encryption.py)
* Audit-before-credential-commit ordering: every successful write path
  acquires a SEPARATE connection for the success-audit and writes it
  BEFORE the credential transaction commits
* Reconnect-after-disconnect: ``reactivate_credential`` handles archived
  rows; ``store_credential`` does not
* Rotate resets ``status='active'`` (Codex round 2 P2 fix)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.vault import user_credentials
from services.vault.errors import VaultError, VaultErrorCode, VaultPermissionError

_ALLOWED_CALLER = "services.granola_ingestion.adapter"
_BLOCKED_CALLER = "not.in.allowlist"


class _FakeTransaction:
    """Mimics asyncpg's Transaction async context manager."""

    def __init__(self) -> None:
        self.entered = False
        self.exited = False
        self.rolled_back = False

    async def __aenter__(self) -> "_FakeTransaction":
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.exited = True
        if exc_type is not None:
            self.rolled_back = True
        return False  # propagate exceptions


class _FakeAcquireContextManager:
    """Mimics ``asyncpg.Pool.acquire()``'s async context manager."""

    def __init__(self, conn: AsyncMock) -> None:
        self._conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _make_fake_conn() -> AsyncMock:
    """Build one fake asyncpg connection with txn + execute + fetchrow."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    conn.fetchrow = AsyncMock(return_value=None)
    transactions: list[_FakeTransaction] = []

    def _new_txn() -> _FakeTransaction:
        tx = _FakeTransaction()
        transactions.append(tx)
        return tx

    conn.transaction = MagicMock(side_effect=_new_txn)
    conn._transactions = transactions  # type: ignore[attr-defined]
    return conn


@pytest.fixture
def fake_pool() -> MagicMock:
    """Mock ``asyncpg.Pool`` that hands out a fresh connection per acquire().

    Each acquire() yields a NEW AsyncMock connection so tests can assert
    that audit and credential operations use SEPARATE connections.

    The pool exposes ``_acquired_conns`` (list, in acquisition order) and
    ``_conn_factory`` (callable; tests can override to return a specific
    pre-configured conn).
    """
    pool = MagicMock()
    acquired_conns: list[AsyncMock] = []

    def _default_factory() -> AsyncMock:
        return _make_fake_conn()

    pool._conn_factory = _default_factory  # type: ignore[attr-defined]

    def _acquire() -> _FakeAcquireContextManager:
        conn = pool._conn_factory()  # type: ignore[attr-defined]
        acquired_conns.append(conn)
        return _FakeAcquireContextManager(conn)

    pool.acquire = MagicMock(side_effect=_acquire)
    pool._acquired_conns = acquired_conns  # type: ignore[attr-defined]
    return pool


@pytest.fixture
def audit_spy(monkeypatch):
    """Replace both audit writer variants with the same AsyncMock spy.

    Tests don't care which variant the accessor uses; the spy catches
    both. After Codex R4 the write accessors use ``write_audit_row_on_conn``
    (same-transaction, no nested acquire); reads + failure-audits still
    use the pool-based ``write_audit_row``.
    """
    spy = AsyncMock(side_effect=lambda **kw: uuid.uuid4())
    monkeypatch.setattr(user_credentials.audit, "write_audit_row", spy)
    monkeypatch.setattr(user_credentials.audit, "write_audit_row_on_conn", spy)
    return spy


@pytest.fixture
def encrypt_spy(monkeypatch):
    """Stub encryption.encrypt_credential to return deterministic bytes."""
    counter = {"n": 0}

    def fake_encrypt(*, plaintext: str, encryption_context: dict[str, str], **_kw):
        counter["n"] += 1
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
    """Fake asyncpg Record (dict access works on real Records too)."""
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


def _configure_pool_to_yield(pool: MagicMock, conns: list[AsyncMock]) -> None:
    """Configure the pool to yield specific conns in order, then default factory."""
    iterator = iter(conns)

    def _factory() -> AsyncMock:
        try:
            return next(iterator)
        except StopIteration:
            return _make_fake_conn()

    pool._conn_factory = _factory  # type: ignore[attr-defined]


class TestGetGranolaCredential:
    @pytest.mark.asyncio
    async def test_caller_not_in_allowlist_raises_and_audits(
        self, fake_pool: MagicMock, audit_spy: AsyncMock
    ):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        with pytest.raises(VaultPermissionError):
            await user_credentials.get_granola_credential_for_user(
                tenant_id=tenant_id,
                user_id=user_id,
                caller_module=_BLOCKED_CALLER,
                pool=fake_pool,
            )
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "read"
        assert kw["success"] is False
        assert kw["error_code"] == VaultErrorCode.VAULT_CALLER_NOT_ALLOWED.value
        assert kw["caller_module"] == _BLOCKED_CALLER
        assert kw["credential_id"] is None
        assert kw["pool"] is fake_pool
        # Pool was NOT acquired for any credential SQL — ALLOWLIST short-circuits
        # before any DB work
        fake_pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_row_returns_none_and_audits_success(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = None
        _configure_pool_to_yield(fake_pool, [cred_conn])

        result = await user_credentials.get_granola_credential_for_user(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
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
        self, fake_pool: MagicMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = _credential_row(
            credential_id=credential_id, tenant_id=tenant_id, user_id=user_id
        )
        _configure_pool_to_yield(fake_pool, [cred_conn])

        result = await user_credentials.get_granola_credential_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
            trace_id="trace-1",
        )

        assert result is not None
        assert result.id == credential_id
        assert result.tenant_id == tenant_id
        assert result.user_id == user_id
        assert result.api_key.startswith("grn_decrypted_")
        assert result.config == {"folder_id": "fol_abc"}
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "read"
        assert kw["success"] is True
        assert kw["credential_id"] == credential_id
        assert kw["trace_id"] == "trace-1"

    @pytest.mark.asyncio
    async def test_decrypt_passes_4_field_context_with_row_id(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        """LOCKED-40 — credential_id from the row, not the caller."""
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = _credential_row(
            credential_id=credential_id, tenant_id=tenant_id, user_id=user_id
        )
        _configure_pool_to_yield(fake_pool, [cred_conn])

        await user_credentials.get_granola_credential_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
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
        self, fake_pool: MagicMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        credential_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = _credential_row(
            credential_id=credential_id, tenant_id=tenant_id, user_id=user_id
        )
        _configure_pool_to_yield(fake_pool, [cred_conn])
        decrypt_spy.side_effect = VaultError(
            VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH, "tampered"
        )

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.get_granola_credential_for_user(
                tenant_id=tenant_id,
                user_id=user_id,
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["success"] is False
        assert kw["error_code"] == VaultErrorCode.VAULT_KMS_CONTEXT_MISMATCH.value
        assert kw["credential_id"] == credential_id

    @pytest.mark.asyncio
    async def test_select_filters_archived_at_is_null(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        """The accessor must not return archived rows."""
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = None
        _configure_pool_to_yield(fake_pool, [cred_conn])

        await user_credentials.get_granola_credential_for_user(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
        )
        sql = cred_conn.fetchrow.await_args.args[0]
        assert "archived_at IS NULL" in sql
        assert "provider = $3" in sql

    @pytest.mark.asyncio
    async def test_select_filters_tenant_and_user(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        """LOCKED-41 — the SELECT MUST include tenant_id in the WHERE."""
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = None
        _configure_pool_to_yield(fake_pool, [cred_conn])

        await user_credentials.get_granola_credential_for_user(
            tenant_id=tenant_id,
            user_id=user_id,
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
        )
        args = cred_conn.fetchrow.await_args.args
        assert args[1] == tenant_id
        assert args[2] == user_id
        assert args[3] == "granola"


class TestStoreCredential:
    @pytest.mark.asyncio
    async def test_caller_not_in_allowlist_raises_and_audits(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        with pytest.raises(VaultPermissionError):
            await user_credentials.store_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                api_key="grn_test",
                config={"folder_id": "fol_x"},
                caller_module=_BLOCKED_CALLER,
                pool=fake_pool,
            )
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "write"
        assert kw["success"] is False
        assert kw["error_code"] == VaultErrorCode.VAULT_CALLER_NOT_ALLOWED.value
        encrypt_spy.assert_not_called()
        fake_pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_inserts_and_audits(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
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
            pool=fake_pool,
        )

        assert isinstance(new_id, uuid.UUID)
        encrypt_spy.assert_called_once()
        ctx = encrypt_spy.call_args.kwargs["encryption_context"]
        assert ctx == {
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "provider": "granola",
            "credential_id": str(new_id),
        }
        # Exactly one acquire for the credential connection. Audit goes
        # through the spy (which is monkeypatched, so no real acquire).
        fake_pool.acquire.assert_called_once()
        cred_conn = fake_pool._acquired_conns[0]
        cred_conn.execute.assert_awaited_once()
        execute_args = cred_conn.execute.await_args.args
        assert "INSERT INTO vault.user_credentials" in execute_args[0]
        assert execute_args[1] == new_id
        assert execute_args[2] == tenant_id
        # Success-audit happened
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "write"
        assert kw["success"] is True
        assert kw["credential_id"] == new_id
        # Audit was passed the cred_conn (same-transaction, no nested
        # pool acquire — Codex R4 [P1] deadlock fix)
        assert kw["conn"] is cred_conn

    @pytest.mark.asyncio
    async def test_encrypt_failure_audits_and_reraises_without_insert(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
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
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_KMS_ENCRYPT_FAILED
        fake_pool.acquire.assert_not_called()
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["success"] is False

    @pytest.mark.asyncio
    async def test_insert_failure_audits_and_wraps_as_db_insert_failed(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        cred_conn = _make_fake_conn()
        cred_conn.execute = AsyncMock(side_effect=RuntimeError("unique violation"))
        _configure_pool_to_yield(fake_pool, [cred_conn])

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.store_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                api_key="grn_test",
                config={"folder_id": "fol_x"},
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_INSERT_FAILED
        # Transaction rolled back
        tx = cred_conn._transactions[0]
        assert tx.rolled_back is True
        # Failure-audit written
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["success"] is False
        assert (
            audit_spy.await_args.kwargs["error_code"]
            == VaultErrorCode.VAULT_DB_INSERT_FAILED.value
        )

    @pytest.mark.asyncio
    async def test_two_consecutive_stores_call_encrypt_twice(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """LOCKED-43 contract — every write triggers its own encrypt call."""
        kwargs = dict(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider="granola",
            api_key="grn_test",
            config={"folder_id": "fol_x"},
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
        )
        await user_credentials.store_credential(**kwargs)
        await user_credentials.store_credential(**kwargs)
        assert encrypt_spy.call_count == 2

    @pytest.mark.asyncio
    async def test_no_nested_pool_acquire_during_write(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """Codex R4 [P1] deadlock fix: success-audit must NOT acquire a
        second connection from the pool while cred_conn is still held.

        Verified by asserting (a) pool.acquire is called exactly once per
        successful store, and (b) the success-audit was passed the
        cred_conn directly (not the pool).
        """
        await user_credentials.store_credential(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider="granola",
            api_key="grn_test",
            config={"folder_id": "fol_x"},
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
        )
        # Exactly ONE pool.acquire — the cred_conn for the INSERT.
        # If this were 2, the second acquire would nest inside the first's
        # transaction and deadlock under realistic pool sizing.
        assert fake_pool.acquire.call_count == 1, (
            "store_credential must not nest pool.acquire calls (Codex R4 P1)"
        )
        # Success-audit was passed a Connection (cred_conn), not a Pool.
        audit_kw = audit_spy.await_args.kwargs
        assert "conn" in audit_kw and "pool" not in audit_kw, (
            "success-audit must use write_audit_row_on_conn(conn=...) "
            "not write_audit_row(pool=...) to avoid nested acquires"
        )


class TestStoreAtomicityAndOrdering:
    """Audit-before-credential-commit ordering enforces the durability claim."""

    @pytest.mark.asyncio
    async def test_audit_runs_before_credential_commit(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """The success-audit must complete BEFORE the credential transaction
        exits. Operationally: if audit fails, txn rolls back; we never commit
        a credential without first having committed the audit."""
        call_order: list[str] = []

        # Subclass _FakeTransaction so we can override __aexit__ on the
        # CLASS (Python dunder methods are class-resolved, not
        # instance-resolved, so per-instance assignment doesn't work).
        class _TracedTransaction(_FakeTransaction):
            async def __aexit__(self_, exc_type, exc, tb):
                call_order.append("credential_commit")
                return await super().__aexit__(exc_type, exc, tb)

        async def _audit_record(**_kw):
            call_order.append("audit")
            return uuid.uuid4()

        audit_spy.side_effect = _audit_record

        cred_conn = AsyncMock()
        cred_conn.fetchrow = AsyncMock(return_value=None)
        cred_conn._transactions = []  # type: ignore[attr-defined]

        async def _trace_execute(*_args, **_kw):
            call_order.append("credential_insert")
            return "INSERT 0 1"

        cred_conn.execute = AsyncMock(side_effect=_trace_execute)

        def _new_txn():
            tx = _TracedTransaction()
            cred_conn._transactions.append(tx)
            return tx

        cred_conn.transaction = MagicMock(side_effect=_new_txn)
        _configure_pool_to_yield(fake_pool, [cred_conn])

        await user_credentials.store_credential(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider="granola",
            api_key="grn_test",
            config={"folder_id": "fol_x"},
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
        )

        # Order must be: INSERT → audit → commit
        assert call_order == ["credential_insert", "audit", "credential_commit"], (
            f"audit must commit BEFORE credential commit; got {call_order}"
        )

    @pytest.mark.asyncio
    async def test_audit_failure_inside_txn_rolls_back_credential(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """If the success-audit (separate conn) raises, the cred_conn txn
        must roll back AND a failure-audit is attempted on its own conn."""
        audit_spy.side_effect = [
            VaultError(VaultErrorCode.VAULT_AUDIT_LOG_WRITE_FAILED, "audit died"),
            uuid.uuid4(),  # outside-txn failure audit succeeds
        ]

        cred_conn = _make_fake_conn()
        _configure_pool_to_yield(fake_pool, [cred_conn])

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.store_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                api_key="grn_test",
                config={"folder_id": "fol_x"},
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_AUDIT_LOG_WRITE_FAILED
        tx = cred_conn._transactions[0]
        assert tx.rolled_back is True
        assert audit_spy.await_count == 2
        # Second call is the failure-audit
        second_kw = audit_spy.await_args_list[1].kwargs
        assert second_kw["success"] is False

    @pytest.mark.asyncio
    async def test_double_fault_does_not_mask_original_error(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        audit_spy.side_effect = [
            VaultError(VaultErrorCode.VAULT_AUDIT_LOG_WRITE_FAILED, "primary failure"),
            VaultError(VaultErrorCode.VAULT_AUDIT_LOG_WRITE_FAILED, "secondary failure"),
        ]
        cred_conn = _make_fake_conn()
        _configure_pool_to_yield(fake_pool, [cred_conn])

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.store_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                api_key="grn_test",
                config={"folder_id": "fol_x"},
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert "primary failure" in str(exc_info.value)
        assert audit_spy.await_count == 2


class TestRotateCredentialKey:
    @pytest.mark.asyncio
    async def test_caller_not_in_allowlist_raises_and_audits(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        with pytest.raises(VaultPermissionError):
            await user_credentials.rotate_credential_key(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                credential_id=uuid.uuid4(),
                new_api_key="grn_new",
                caller_module=_BLOCKED_CALLER,
                pool=fake_pool,
            )
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "rotate"
        assert kw["success"] is False
        assert kw["error_code"] == VaultErrorCode.VAULT_CALLER_NOT_ALLOWED.value
        encrypt_spy.assert_not_called()
        fake_pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_credential_not_found_raises_db_not_found_and_audits(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        lookup_conn = _make_fake_conn()
        lookup_conn.fetchrow.return_value = None
        _configure_pool_to_yield(fake_pool, [lookup_conn])

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.rotate_credential_key(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                credential_id=uuid.uuid4(),
                new_api_key="grn_new",
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_NOT_FOUND
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "rotate"
        assert kw["success"] is False
        # Codex R6 [P2] fix: failure-audit must pass credential_id=None
        # because the credential definitively does not exist (we just
        # SELECTed and got nothing). Passing the unverified UUID would
        # violate the audit table's FK to vault.user_credentials.id and
        # cause _best_effort_failure_audit to swallow the FK violation
        # as a double-fault, leaving no forensic record.
        assert kw["credential_id"] is None, (
            "rotate's not-found failure-audit must pass credential_id=None "
            "to avoid audit-table FK violation (Codex R6 P2)"
        )
        encrypt_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_allowlist_violation_audits_credential_id_as_none(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """Codex R6 [P2]: ALLOWLIST violation audit must pass
        credential_id=None because we have not verified the credential
        exists (and the audit FK would reject a nonexistent UUID)."""
        with pytest.raises(VaultPermissionError):
            await user_credentials.rotate_credential_key(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                credential_id=uuid.uuid4(),
                new_api_key="grn_new",
                caller_module=_BLOCKED_CALLER,
                pool=fake_pool,
            )
        kw = audit_spy.await_args.kwargs
        assert kw["credential_id"] is None
        assert kw["error_code"] == VaultErrorCode.VAULT_CALLER_NOT_ALLOWED.value

    @pytest.mark.asyncio
    async def test_cross_tenant_credential_id_returns_not_found(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """Codex R3 [P1] tenant-isolation fix: caller passing a credential_id
        that belongs to a different (tenant_id, user_id) tuple gets
        VAULT_DB_NOT_FOUND, not access to the other tenant's row.

        We assert this by feeding the lookup SQL the WRONG tenant_id/user_id
        and confirming the lookup is parameterized with all three (the live
        SQL filters on id + tenant_id + user_id; a real DB would return
        nothing)."""
        attacker_tenant = uuid.uuid4()
        attacker_user = uuid.uuid4()
        victim_credential_id = uuid.uuid4()
        # The fake lookup returns None (mimicking real DB filter rejecting
        # the wrong-tenant lookup):
        lookup_conn = _make_fake_conn()
        lookup_conn.fetchrow.return_value = None
        _configure_pool_to_yield(fake_pool, [lookup_conn])

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.rotate_credential_key(
                tenant_id=attacker_tenant,
                user_id=attacker_user,
                credential_id=victim_credential_id,
                new_api_key="grn_attacker",
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_NOT_FOUND
        # Lookup SQL was called with all three (id, tenant_id, user_id)
        lookup_args = lookup_conn.fetchrow.await_args.args
        assert lookup_args[1] == victim_credential_id
        assert lookup_args[2] == attacker_tenant
        assert lookup_args[3] == attacker_user
        # Lookup SQL filters on all three identity fields
        sql = lookup_args[0]
        assert "WHERE id = $1" in sql
        assert "AND tenant_id = $2" in sql
        assert "AND user_id = $3" in sql
        # No mutation happened
        encrypt_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_rotates_with_same_credential_id_in_context(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        lookup_conn = _make_fake_conn()
        lookup_conn.fetchrow.return_value = {"provider": "granola"}
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = {"tenant_id": tenant_id}
        _configure_pool_to_yield(fake_pool, [lookup_conn, cred_conn])

        await user_credentials.rotate_credential_key(
            tenant_id=tenant_id,
            user_id=user_id,
            credential_id=credential_id,
            new_api_key="grn_new_key",
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
        )

        encrypt_spy.assert_called_once()
        ctx = encrypt_spy.call_args.kwargs["encryption_context"]
        assert ctx == {
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "provider": "granola",
            "credential_id": str(credential_id),
        }
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "rotate"
        assert kw["success"] is True
        assert kw["credential_id"] == credential_id

    @pytest.mark.asyncio
    async def test_rotate_update_sql_resets_status_and_filters_tenant(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """Codex R2 P2: status reset. Codex R3 P1: UPDATE filters tenant_id."""
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        lookup_conn = _make_fake_conn()
        lookup_conn.fetchrow.return_value = {"provider": "granola"}
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = {"tenant_id": tenant_id}
        _configure_pool_to_yield(fake_pool, [lookup_conn, cred_conn])

        await user_credentials.rotate_credential_key(
            tenant_id=tenant_id,
            user_id=user_id,
            credential_id=credential_id,
            new_api_key="grn_new",
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
        )

        sql = cred_conn.fetchrow.await_args.args[0]
        assert "status = 'active'" in sql, (
            "rotate must reset status to 'active' so revoked/error credentials "
            "become active again after manual rotation"
        )
        assert "last_error = NULL" in sql
        assert "consecutive_failures = 0" in sql
        # Codex R3 [P1] tenant-isolation: UPDATE filters all three identity
        # fields, not just id.
        assert "AND tenant_id = $5" in sql
        assert "AND user_id = $6" in sql
        update_args = cred_conn.fetchrow.await_args.args
        assert update_args[4] == credential_id
        assert update_args[5] == tenant_id
        assert update_args[6] == user_id

    @pytest.mark.asyncio
    async def test_archive_race_between_lookup_and_update_audits_not_found(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        lookup_conn = _make_fake_conn()
        lookup_conn.fetchrow.return_value = {"provider": "granola"}
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = None  # UPDATE returned nothing
        _configure_pool_to_yield(fake_pool, [lookup_conn, cred_conn])

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.rotate_credential_key(
                tenant_id=tenant_id,
                user_id=user_id,
                credential_id=credential_id,
                new_api_key="grn_new",
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_NOT_FOUND
        tx = cred_conn._transactions[0]
        assert tx.rolled_back is True
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["success"] is False

    @pytest.mark.asyncio
    async def test_two_consecutive_rotates_each_call_encrypt(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()

        def _factory():
            conn = _make_fake_conn()
            conn.fetchrow.return_value = {"provider": "granola"}
            return conn

        fake_pool._conn_factory = _factory

        await user_credentials.rotate_credential_key(
            tenant_id=tenant_id, user_id=user_id,
            credential_id=credential_id, new_api_key="grn_a",
            caller_module=_ALLOWED_CALLER, pool=fake_pool,
        )
        await user_credentials.rotate_credential_key(
            tenant_id=tenant_id, user_id=user_id,
            credential_id=credential_id, new_api_key="grn_b",
            caller_module=_ALLOWED_CALLER, pool=fake_pool,
        )
        assert encrypt_spy.call_count == 2

    @pytest.mark.asyncio
    async def test_no_nested_pool_acquire_during_rotate(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """Codex R5 [P1] deadlock fix: rotate's success-audit must use
        write_audit_row_on_conn(conn=cred_conn) so it does NOT acquire a
        second pool connection while cred_conn is still held in a txn.

        rotate acquires TWO conns total: one for the identity lookup, one
        for the UPDATE+audit transaction. They never overlap (the lookup
        conn is released before the cred_conn is acquired)."""
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        lookup_conn = _make_fake_conn()
        lookup_conn.fetchrow.return_value = {"provider": "granola"}
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = {"tenant_id": tenant_id}
        _configure_pool_to_yield(fake_pool, [lookup_conn, cred_conn])

        await user_credentials.rotate_credential_key(
            tenant_id=tenant_id,
            user_id=user_id,
            credential_id=credential_id,
            new_api_key="grn_new",
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
        )
        # Exactly TWO pool.acquire calls (lookup + cred_conn). If this were
        # 3, the third would nest inside cred_conn's transaction = deadlock.
        assert fake_pool.acquire.call_count == 2, (
            "rotate_credential_key must not nest pool.acquire (Codex R5 P1)"
        )
        audit_kw = audit_spy.await_args.kwargs
        assert "conn" in audit_kw and "pool" not in audit_kw, (
            "rotate success-audit must use write_audit_row_on_conn(conn=...) "
            "to avoid nested pool acquires"
        )


class TestReactivateCredential:
    """Codex round 2 P1: reconnect-after-disconnect needs a working primitive."""

    @pytest.mark.asyncio
    async def test_caller_not_in_allowlist_raises_and_audits(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        with pytest.raises(VaultPermissionError):
            await user_credentials.reactivate_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                new_api_key="grn_x",
                new_config={"folder_id": "fol_x"},
                caller_module=_BLOCKED_CALLER,
                pool=fake_pool,
            )
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "reactivate"
        assert kw["success"] is False
        assert kw["error_code"] == VaultErrorCode.VAULT_CALLER_NOT_ALLOWED.value
        encrypt_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_row_raises_db_not_found(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        lookup_conn = _make_fake_conn()
        lookup_conn.fetchrow.return_value = None
        _configure_pool_to_yield(fake_pool, [lookup_conn])

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.reactivate_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                new_api_key="grn_x",
                new_config={},
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_NOT_FOUND
        assert "store_credential" in str(exc_info.value)
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["operation"] == "reactivate"
        encrypt_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_active_row_rejects_with_db_insert_failed(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """An active (non-archived) row means caller should rotate instead."""
        existing_id = uuid.uuid4()
        lookup_conn = _make_fake_conn()
        lookup_conn.fetchrow.return_value = {"id": existing_id, "archived_at": None}
        _configure_pool_to_yield(fake_pool, [lookup_conn])

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.reactivate_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                new_api_key="grn_x",
                new_config={},
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_INSERT_FAILED
        assert "rotate_credential_key" in str(exc_info.value)
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["credential_id"] == existing_id
        encrypt_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_preserves_credential_id_in_context(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """Reactivating preserves the archived row's id (per docstring)."""
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        existing_id = uuid.uuid4()
        archived_at = MagicMock()  # any truthy datetime-like
        lookup_conn = _make_fake_conn()
        lookup_conn.fetchrow.return_value = {"id": existing_id, "archived_at": archived_at}
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = {"tenant_id": tenant_id}
        _configure_pool_to_yield(fake_pool, [lookup_conn, cred_conn])

        returned_id = await user_credentials.reactivate_credential(
            tenant_id=tenant_id,
            user_id=user_id,
            provider="granola",
            new_api_key="grn_new",
            new_config={"folder_id": "fol_y"},
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
        )
        assert returned_id == existing_id
        encrypt_spy.assert_called_once()
        ctx = encrypt_spy.call_args.kwargs["encryption_context"]
        assert ctx["credential_id"] == str(existing_id)
        assert ctx["tenant_id"] == str(tenant_id)
        # UPDATE SQL targets archived rows
        sql = cred_conn.fetchrow.await_args.args[0]
        assert "archived_at IS NOT NULL" in sql
        assert "archived_at = NULL" in sql
        assert "status = 'active'" in sql
        # Audit success
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "reactivate"
        assert kw["success"] is True
        assert kw["credential_id"] == existing_id

    @pytest.mark.asyncio
    async def test_race_concurrent_reactivate_audits_not_found(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """If another caller reactivates between lookup and UPDATE, the
        WHERE archived_at IS NOT NULL clause makes UPDATE return nothing."""
        existing_id = uuid.uuid4()
        archived_at = MagicMock()
        lookup_conn = _make_fake_conn()
        lookup_conn.fetchrow.return_value = {"id": existing_id, "archived_at": archived_at}
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = None  # UPDATE matched nothing
        _configure_pool_to_yield(fake_pool, [lookup_conn, cred_conn])

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.reactivate_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                new_api_key="grn_new",
                new_config={},
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_NOT_FOUND
        tx = cred_conn._transactions[0]
        assert tx.rolled_back is True

    @pytest.mark.asyncio
    async def test_no_nested_pool_acquire_during_reactivate(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """Codex R5 [P1] deadlock fix: reactivate's success-audit must use
        write_audit_row_on_conn(conn=cred_conn) so it does NOT acquire a
        second pool connection while cred_conn is still held in a txn.

        reactivate acquires TWO conns total: one for the identity lookup,
        one for the UPDATE+audit transaction. They never overlap."""
        existing_id = uuid.uuid4()
        archived_at = MagicMock()
        lookup_conn = _make_fake_conn()
        lookup_conn.fetchrow.return_value = {"id": existing_id, "archived_at": archived_at}
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = {"tenant_id": uuid.uuid4()}
        _configure_pool_to_yield(fake_pool, [lookup_conn, cred_conn])

        await user_credentials.reactivate_credential(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider="granola",
            new_api_key="grn_new",
            new_config={"folder_id": "fol_new"},
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
        )
        assert fake_pool.acquire.call_count == 2, (
            "reactivate_credential must not nest pool.acquire (Codex R5 P1)"
        )
        audit_kw = audit_spy.await_args.kwargs
        assert "conn" in audit_kw and "pool" not in audit_kw, (
            "reactivate success-audit must use write_audit_row_on_conn(conn=...) "
            "to avoid nested pool acquires"
        )

    @pytest.mark.asyncio
    async def test_reactivate_resets_last_polled_at_cursor(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        """Codex R5 [P2] fix: reconnect with new folder_id must clear the
        old polled cursor; otherwise next poll skips notes in new folder
        older than the cursor."""
        existing_id = uuid.uuid4()
        archived_at = MagicMock()
        lookup_conn = _make_fake_conn()
        lookup_conn.fetchrow.return_value = {"id": existing_id, "archived_at": archived_at}
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = {"tenant_id": uuid.uuid4()}
        _configure_pool_to_yield(fake_pool, [lookup_conn, cred_conn])

        await user_credentials.reactivate_credential(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider="granola",
            new_api_key="grn_new",
            new_config={"folder_id": "fol_new"},
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
        )
        sql = cred_conn.fetchrow.await_args.args[0]
        assert "last_polled_at = NULL" in sql, (
            "reactivate must clear last_polled_at so the polled cursor "
            "doesn't skip notes in the new folder (Codex R5 P2)"
        )


class TestGetFiltersByStatus:
    """Codex R3 [P2] fix: get returns only ACTIVE credentials.

    The SQL must filter status='active' so revoked/error/archived
    credentials are all hidden from the read accessor — callers shouldn't
    have to remember to check status themselves before using the API key.
    """

    @pytest.mark.asyncio
    async def test_select_sql_filters_status_active(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow.return_value = None
        _configure_pool_to_yield(fake_pool, [cred_conn])

        await user_credentials.get_granola_credential_for_user(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            caller_module=_ALLOWED_CALLER,
            pool=fake_pool,
        )
        sql = cred_conn.fetchrow.await_args.args[0]
        assert "status = 'active'" in sql, (
            "get must filter status='active' so revoked/error credentials "
            "are not returned as if they were active"
        )
        assert "archived_at IS NULL" in sql


class TestRawDbErrorConversion:
    """Codex R3 [P1] fix: raw asyncpg exceptions are converted to
    structured VaultError at every DB boundary, AND failure-audit is
    written. Callers never see a non-VaultError from the vault module."""

    @pytest.mark.asyncio
    async def test_get_wraps_pool_acquire_failure(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        class _FailingAcquire:
            async def __aenter__(self):
                raise ConnectionError("pool exhausted")

            async def __aexit__(self, *_):
                return False

        fake_pool.acquire = MagicMock(return_value=_FailingAcquire())

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.get_granola_credential_for_user(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_QUERY_FAILED
        assert isinstance(exc_info.value.__cause__, ConnectionError)
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["success"] is False
        assert (
            audit_spy.await_args.kwargs["error_code"]
            == VaultErrorCode.VAULT_DB_QUERY_FAILED.value
        )

    @pytest.mark.asyncio
    async def test_get_wraps_fetchrow_failure(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        cred_conn = _make_fake_conn()
        cred_conn.fetchrow = AsyncMock(side_effect=ConnectionError("network drop"))
        _configure_pool_to_yield(fake_pool, [cred_conn])

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.get_granola_credential_for_user(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_QUERY_FAILED
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["success"] is False

    @pytest.mark.asyncio
    async def test_store_wraps_pool_acquire_failure(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        class _FailingAcquire:
            async def __aenter__(self):
                raise ConnectionError("pool exhausted")

            async def __aexit__(self, *_):
                return False

        fake_pool.acquire = MagicMock(return_value=_FailingAcquire())

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.store_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                api_key="grn_test",
                config={"folder_id": "fol_x"},
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_QUERY_FAILED
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["success"] is False

    @pytest.mark.asyncio
    async def test_rotate_wraps_pool_acquire_failure(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        class _FailingAcquire:
            async def __aenter__(self):
                raise ConnectionError("pool exhausted")

            async def __aexit__(self, *_):
                return False

        fake_pool.acquire = MagicMock(return_value=_FailingAcquire())

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.rotate_credential_key(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                credential_id=uuid.uuid4(),
                new_api_key="grn_new",
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_QUERY_FAILED
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["success"] is False
        encrypt_spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_reactivate_wraps_pool_acquire_failure(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, encrypt_spy: MagicMock
    ):
        class _FailingAcquire:
            async def __aenter__(self):
                raise ConnectionError("pool exhausted")

            async def __aexit__(self, *_):
                return False

        fake_pool.acquire = MagicMock(return_value=_FailingAcquire())

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.reactivate_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                provider="granola",
                new_api_key="grn_new",
                new_config={},
                caller_module=_ALLOWED_CALLER,
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_QUERY_FAILED
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["success"] is False
        encrypt_spy.assert_not_called()


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
        assert not hasattr(user_credentials.ALLOWLIST, "add")


def _status_row(
    *,
    credential_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str = "active",
    archived_at=None,
    config: dict | None = None,
) -> dict:
    """Fake Record for the non-decrypting status SELECT (no encrypted cols)."""
    return {
        "id": credential_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "provider": "granola",
        "config": config if config is not None else {"folder_id": "fol_abc", "folder_name": "EQ"},
        "status": status,
        "last_polled_at": None,
        "last_error": None,
        "consecutive_failures": 0,
        "created_at": MagicMock(),
        "updated_at": MagicMock(),
        "archived_at": archived_at,
    }


class TestGetCredentialStatus:
    """Phase 2f non-decrypting lifecycle read for /status, /rotate, /disconnect."""

    @pytest.mark.asyncio
    async def test_caller_not_in_allowlist_raises_and_audits(
        self, fake_pool: MagicMock, audit_spy: AsyncMock
    ):
        with pytest.raises(VaultPermissionError):
            await user_credentials.get_credential_status(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                caller_module=_BLOCKED_CALLER,
                pool=fake_pool,
            )
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "read"
        assert kw["success"] is False
        assert kw["error_code"] == VaultErrorCode.VAULT_CALLER_NOT_ALLOWED.value
        assert kw["credential_id"] is None
        fake_pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_row_returns_none_and_audits_success(
        self, fake_pool: MagicMock, audit_spy: AsyncMock
    ):
        conn = _make_fake_conn()
        conn.fetchrow.return_value = None
        _configure_pool_to_yield(fake_pool, [conn])

        result = await user_credentials.get_credential_status(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            caller_module="routers.granola",
            pool=fake_pool,
        )
        assert result is None
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "read"
        assert kw["success"] is True
        assert kw["credential_id"] is None

    @pytest.mark.asyncio
    async def test_happy_path_returns_status_without_decrypting(
        self, fake_pool: MagicMock, audit_spy: AsyncMock, decrypt_spy: MagicMock
    ):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        conn = _make_fake_conn()
        conn.fetchrow.return_value = _status_row(
            credential_id=credential_id, tenant_id=tenant_id, user_id=user_id
        )
        _configure_pool_to_yield(fake_pool, [conn])

        result = await user_credentials.get_credential_status(
            tenant_id=tenant_id,
            user_id=user_id,
            caller_module="routers.granola",
            pool=fake_pool,
            trace_id="trace-status",
        )

        assert result is not None
        assert result.id == credential_id
        assert result.status == "active"
        assert result.config == {"folder_id": "fol_abc", "folder_name": "EQ"}
        assert result.archived_at is None
        # No key material is ever materialized for a status read.
        decrypt_spy.assert_not_called()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "read"
        assert kw["success"] is True
        assert kw["credential_id"] == credential_id
        assert kw["trace_id"] == "trace-status"

    @pytest.mark.asyncio
    async def test_returns_archived_and_revoked_rows_unlike_active_accessor(
        self, fake_pool: MagicMock, audit_spy: AsyncMock
    ):
        """Unlike get_granola_credential_for_user, the status accessor returns
        rows in ANY status (archived / revoked) so /status can render a broken
        or disconnected connection."""
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        conn = _make_fake_conn()
        conn.fetchrow.return_value = _status_row(
            credential_id=credential_id,
            tenant_id=tenant_id,
            user_id=user_id,
            status="archived",
            archived_at=MagicMock(),
        )
        _configure_pool_to_yield(fake_pool, [conn])

        result = await user_credentials.get_credential_status(
            tenant_id=tenant_id,
            user_id=user_id,
            caller_module="routers.granola",
            pool=fake_pool,
        )
        assert result is not None
        assert result.status == "archived"
        assert result.archived_at is not None

    @pytest.mark.asyncio
    async def test_select_sql_does_not_filter_status_or_archived(
        self, fake_pool: MagicMock, audit_spy: AsyncMock
    ):
        """The status SELECT must NOT filter status='active' or
        archived_at IS NULL — that's what makes it return ANY lifecycle
        state. It selects archived_at as a COLUMN but never in the WHERE."""
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        conn = _make_fake_conn()
        conn.fetchrow.return_value = None
        _configure_pool_to_yield(fake_pool, [conn])

        await user_credentials.get_credential_status(
            tenant_id=tenant_id,
            user_id=user_id,
            caller_module="routers.granola",
            pool=fake_pool,
        )
        args = conn.fetchrow.await_args.args
        sql = args[0]
        assert "status = 'active'" not in sql
        assert "archived_at IS NULL" not in sql
        # Tenant isolation: scoped to tenant + user + provider.
        assert "tenant_id = $1" in sql
        assert "user_id = $2" in sql
        assert "provider = $3" in sql
        assert args[1] == tenant_id
        assert args[2] == user_id
        assert args[3] == "granola"

    @pytest.mark.asyncio
    async def test_wraps_raw_db_error_and_audits(
        self, fake_pool: MagicMock, audit_spy: AsyncMock
    ):
        conn = _make_fake_conn()
        conn.fetchrow = AsyncMock(side_effect=ConnectionError("network drop"))
        _configure_pool_to_yield(fake_pool, [conn])

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.get_credential_status(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                caller_module="routers.granola",
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_QUERY_FAILED
        audit_spy.assert_awaited_once()
        assert audit_spy.await_args.kwargs["success"] is False
        assert audit_spy.await_args.kwargs["operation"] == "read"


class TestArchiveCredential:
    """Phase 2f /disconnect soft-delete (LOCKED-34)."""

    @pytest.mark.asyncio
    async def test_caller_not_in_allowlist_raises_and_audits(
        self, fake_pool: MagicMock, audit_spy: AsyncMock
    ):
        with pytest.raises(VaultPermissionError):
            await user_credentials.archive_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                credential_id=uuid.uuid4(),
                caller_module=_BLOCKED_CALLER,
                pool=fake_pool,
            )
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "archive"
        assert kw["success"] is False
        assert kw["error_code"] == VaultErrorCode.VAULT_CALLER_NOT_ALLOWED.value
        assert kw["credential_id"] is None
        fake_pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_soft_deletes_and_audits(
        self, fake_pool: MagicMock, audit_spy: AsyncMock
    ):
        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        credential_id = uuid.uuid4()
        conn = _make_fake_conn()
        conn.fetchrow.return_value = {"id": credential_id}  # RETURNING id
        _configure_pool_to_yield(fake_pool, [conn])

        result = await user_credentials.archive_credential(
            tenant_id=tenant_id,
            user_id=user_id,
            credential_id=credential_id,
            caller_module="routers.granola",
            pool=fake_pool,
            trace_id="trace-disc",
        )
        assert result is True

        sql = conn.fetchrow.await_args.args[0]
        assert "status = 'archived'" in sql
        assert "archived_at = CURRENT_TIMESTAMP" in sql
        # Idempotency + tenant isolation in the WHERE.
        assert "archived_at IS NULL" in sql
        assert "AND tenant_id = $2" in sql
        assert "AND user_id = $3" in sql
        update_args = conn.fetchrow.await_args.args
        assert update_args[1] == credential_id
        assert update_args[2] == tenant_id
        assert update_args[3] == user_id

        kw = audit_spy.await_args.kwargs
        assert kw["operation"] == "archive"
        assert kw["success"] is True
        assert kw["credential_id"] == credential_id
        assert kw["trace_id"] == "trace-disc"
        # Same-transaction audit (no nested pool acquire).
        assert "conn" in kw and "pool" not in kw

    @pytest.mark.asyncio
    async def test_idempotent_noop_returns_false_without_audit(
        self, fake_pool: MagicMock, audit_spy: AsyncMock
    ):
        """Already-archived / not-found → UPDATE matches 0 rows → False,
        no state change, so no audit row."""
        conn = _make_fake_conn()
        conn.fetchrow.return_value = None  # RETURNING matched nothing
        _configure_pool_to_yield(fake_pool, [conn])

        result = await user_credentials.archive_credential(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            credential_id=uuid.uuid4(),
            caller_module="routers.granola",
            pool=fake_pool,
        )
        assert result is False
        audit_spy.assert_not_awaited()
        # Transaction committed cleanly (no rollback) on the no-op path.
        tx = conn._transactions[0]
        assert tx.rolled_back is False

    @pytest.mark.asyncio
    async def test_only_one_pool_acquire(
        self, fake_pool: MagicMock, audit_spy: AsyncMock
    ):
        """archive's success-audit must use write_audit_row_on_conn(conn=...)
        so it doesn't nest a second pool acquire inside the open txn."""
        credential_id = uuid.uuid4()
        conn = _make_fake_conn()
        conn.fetchrow.return_value = {"id": credential_id}
        _configure_pool_to_yield(fake_pool, [conn])

        await user_credentials.archive_credential(
            tenant_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            credential_id=credential_id,
            caller_module="routers.granola",
            pool=fake_pool,
        )
        assert fake_pool.acquire.call_count == 1

    @pytest.mark.asyncio
    async def test_wraps_raw_db_error_and_audits_failure(
        self, fake_pool: MagicMock, audit_spy: AsyncMock
    ):
        credential_id = uuid.uuid4()
        conn = _make_fake_conn()
        conn.fetchrow = AsyncMock(side_effect=ConnectionError("network drop"))
        _configure_pool_to_yield(fake_pool, [conn])

        with pytest.raises(VaultError) as exc_info:
            await user_credentials.archive_credential(
                tenant_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                credential_id=credential_id,
                caller_module="routers.granola",
                pool=fake_pool,
            )
        assert exc_info.value.code == VaultErrorCode.VAULT_DB_QUERY_FAILED
        audit_spy.assert_awaited_once()
        kw = audit_spy.await_args.kwargs
        assert kw["success"] is False
        assert kw["operation"] == "archive"
        assert kw["credential_id"] == credential_id
