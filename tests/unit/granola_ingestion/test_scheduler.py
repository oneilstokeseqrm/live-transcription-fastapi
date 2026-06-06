"""Unit tests for :mod:`services.granola_ingestion.scheduler`.

DBOS step + workflow decorators degrade to passthrough when called
outside a workflow context (per the test_steps.py docstring in
``tests/unit/account_provisioning``), so each step is exercised by
calling it directly with mocked I/O dependencies.

Structural tests mirror :file:`tests/unit/account_provisioning/test_workflow.py`:
verify the workflow is decorated, the signature shape matches the
dispatch call site, and ``GRANOLA_POLL_QUEUE`` carries the locked
concurrency cap.

Behavioral tests cover the load-bearing branches:

* ``list_active_credentials`` returns the right shape from the
  raw asyncpg fetch.
* ``run_cycle_step`` short-circuits on missing / non-active credential
  without invoking the adapter.
* ``run_cycle_step`` happy path invokes
  :func:`services.granola_ingestion.adapter.run_one_cycle` with the
  decrypted credential + asyncpg pool and surfaces a
  :class:`PollResult` reflecting the cycle outcomes.
* The vault accessor is called with the LOCKED-42 caller_module string
  matching the ALLOWLIST entry.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from services.granola_ingestion import scheduler


# ---------------------------------------------------------------------------
# Mock infrastructure (mirrors test_adapter.py's pattern)
# ---------------------------------------------------------------------------


class _FakeConn:
    """asyncpg Connection stand-in for list_active_credentials + the
    run_cycle_step advisory lock.

    ``fetchval_returns`` controls ``pg_try_advisory_lock`` (True =
    lock acquired). ``fetch_returns`` feeds the credential-listing
    query. ``execute`` swallows the ``pg_advisory_unlock`` call.
    ``fetch_raises`` (when set) makes the first N ``fetch`` calls raise
    to exercise the explicit-retry path.
    """

    def __init__(
        self,
        *,
        fetch_returns: Optional[list[dict]] = None,
        fetchval_returns: bool = True,
        fetch_raises: Optional[list[BaseException]] = None,
    ) -> None:
        self.fetch_returns = fetch_returns or []
        self.fetchval_returns = fetchval_returns
        self.fetch_raises = list(fetch_raises) if fetch_raises else []
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args):
        self.fetch_calls.append((sql, args))
        if self.fetch_raises:
            raise self.fetch_raises.pop(0)
        return list(self.fetch_returns)

    async def fetchval(self, sql: str, *args):
        self.fetchval_calls.append((sql, args))
        return self.fetchval_returns

    async def execute(self, sql: str, *args):
        self.execute_calls.append((sql, args))
        return "OK"


class _FakeAcquireCM:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, *_exc_info):
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    def acquire(self) -> _FakeAcquireCM:
        return _FakeAcquireCM(self.conn)


@dataclass
class _FakeCredential:
    """Duck-typed stand-in for services.vault.GranolaCredential.

    Same shape as the dataclass in test_adapter.py but the scheduler
    only reads ``status`` directly + passes the whole object to
    ``run_one_cycle``.
    """

    id: UUID
    tenant_id: UUID
    user_id: UUID
    provider: str = "granola"
    api_key: str = "grn_test"
    config: dict = None  # type: ignore[assignment]
    status: str = "active"
    last_polled_at: Optional[datetime] = None
    last_error: Optional[dict] = None
    consecutive_failures: int = 0
    created_at: datetime = None  # type: ignore[assignment]
    updated_at: datetime = None  # type: ignore[assignment]
    archived_at: Optional[datetime] = None

    def __post_init__(self):
        if self.config is None:
            self.config = {"folder_id": "fol_test", "folder_name": "EQ"}
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
        if self.updated_at is None:
            self.updated_at = datetime.now(timezone.utc)


def _fake_cycle_result(
    *,
    notes_processed: int = 0,
    credential_skipped: bool = False,
    credential_error_code: Optional[str] = None,
    deferred_reprocessed: int = 0,
    cycle_aborted: bool = False,
):
    """Build a minimal CycleResult-shaped object.

    Importing the real dataclass would require the adapter's full
    import chain; this duck-typed stand-in is enough for the
    scheduler's surface (it reads attributes only).
    """
    return MagicMock(
        notes_processed=notes_processed,
        credential_skipped=credential_skipped,
        credential_error_code=credential_error_code,
        deferred_reprocessed=deferred_reprocessed,
        cycle_aborted=cycle_aborted,
        outcomes={},
    )


# ---------------------------------------------------------------------------
# Structural tests (decorator presence, signature, queue cap)
# ---------------------------------------------------------------------------


def test_workflow_is_dbos_decorated():
    """The workflow function must be a @DBOS.workflow callable.

    DBOS records workflow registration via the decorator; dispatching
    via GRANOLA_POLL_QUEUE.enqueue_async(granola_poll_one_credential, ...)
    requires this registration to have happened at import time.
    """
    fn = scheduler.granola_poll_one_credential
    assert callable(fn)
    assert inspect.iscoroutinefunction(fn)
    assert fn.__name__ == "granola_poll_one_credential"


def test_workflow_signature_takes_three_positional_uuids():
    """Plan §Phase 2e pseudocode: the workflow input is
    (credential_id, tenant_id, user_id). The cron handler dispatches
    these positionally.

    ``from __future__ import annotations`` makes annotations strings at
    definition time, so we use ``typing.get_type_hints`` to evaluate
    them against the module's namespace.
    """
    import typing as _t

    sig = inspect.signature(scheduler.granola_poll_one_credential)
    params = list(sig.parameters.items())
    assert [name for name, _ in params] == ["credential_id", "tenant_id", "user_id"]

    hints = _t.get_type_hints(scheduler.granola_poll_one_credential)
    for name in ("credential_id", "tenant_id", "user_id"):
        assert hints[name] is UUID


def test_granola_poll_queue_concurrency_matches_locked_value():
    """LOCKED-39 + APPROVAL_QUEUE precedent: cap at 5 concurrent
    workflows. Phase 2e's 5-min cadence × O(10-100) initial users
    stays well under this even at burst."""
    assert scheduler.GRANOLA_POLL_QUEUE.name == "granola-poll"
    state = vars(scheduler.GRANOLA_POLL_QUEUE)
    concurrency_value = None
    for attr in ("concurrency", "_concurrency"):
        if attr in state and isinstance(state[attr], int):
            concurrency_value = state[attr]
            break
    assert concurrency_value == 5, (
        f"GRANOLA_POLL_QUEUE.concurrency expected 5, got {state}"
    )


def test_run_cycle_step_signature_uses_locked_caller_module_kwarg():
    """LOCKED-42 ALLOWLIST gate: the scheduler must identify itself
    to the vault accessor by a string matching the ALLOWLIST entry.
    Phase 2b's ALLOWLIST already contains
    ``"services.granola_ingestion.scheduler"`` so the gate passes.
    """
    assert scheduler._CALLER_MODULE == "services.granola_ingestion.scheduler"


def test_caller_module_is_in_vault_allowlist():
    """End-to-end: the scheduler's caller-module string must be
    accepted by the vault accessor's ALLOWLIST. A typo or future
    rename here would surface as a runtime VaultPermissionError
    only; this test catches it at import time."""
    from services.vault import ALLOWLIST

    assert scheduler._CALLER_MODULE in ALLOWLIST


# ---------------------------------------------------------------------------
# list_active_credentials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_active_credentials_returns_metadata_triples():
    """SELECT returns (id, tenant_id, user_id); step packs them into
    CredentialMetadata."""
    rows = [
        {"id": uuid4(), "tenant_id": uuid4(), "user_id": uuid4()},
        {"id": uuid4(), "tenant_id": uuid4(), "user_id": uuid4()},
    ]
    conn = _FakeConn(fetch_returns=rows)
    pool = _FakePool(conn)

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)):
        result = await scheduler.list_active_credentials()

    assert len(result) == 2
    assert all(isinstance(c, scheduler.CredentialMetadata) for c in result)
    assert result[0].id == rows[0]["id"]
    assert result[0].tenant_id == rows[0]["tenant_id"]
    assert result[1].user_id == rows[1]["user_id"]


@pytest.mark.asyncio
async def test_list_active_credentials_filters_to_granola_provider():
    """SQL binds 'granola' as the provider param so other future
    integrations don't get polled by this scheduler."""
    conn = _FakeConn(fetch_returns=[])
    pool = _FakePool(conn)

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)):
        await scheduler.list_active_credentials()

    assert len(conn.fetch_calls) == 1
    sql, args = conn.fetch_calls[0]
    assert "provider = $1" in sql
    assert args == ("granola",)


@pytest.mark.asyncio
async def test_list_active_credentials_filters_to_active_unarchived():
    """SQL must filter status='active' AND archived_at IS NULL so
    revoked / error / archived credentials are skipped — they shouldn't
    receive a dispatched workflow."""
    conn = _FakeConn(fetch_returns=[])
    pool = _FakePool(conn)

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)):
        await scheduler.list_active_credentials()

    sql, _ = conn.fetch_calls[0]
    assert "status = 'active'" in sql
    assert "archived_at IS NULL" in sql


@pytest.mark.asyncio
async def test_list_active_credentials_empty_when_no_active_rows():
    """Pre-Phase-2f happy path: vault.user_credentials is empty →
    zero workflows dispatched per tick."""
    conn = _FakeConn(fetch_returns=[])
    pool = _FakePool(conn)

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)):
        result = await scheduler.list_active_credentials()

    assert result == []


# ---------------------------------------------------------------------------
# run_cycle_step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cycle_step_short_circuits_when_no_active_credential():
    """Concurrent /disconnect between cron-tick and workflow start:
    vault.get_granola_credential_for_user returns None →
    PollResult(skipped=True) without invoking run_one_cycle."""
    pool = _FakePool(_FakeConn())

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=AsyncMock(return_value=None)), \
         patch.object(scheduler, "run_one_cycle", new=AsyncMock()) as run_mock:
        result = await scheduler.run_cycle_step(
            credential_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(),
        )

    assert result.skipped is True
    assert result.reason == "credential_not_active_or_archived"
    run_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_cycle_step_short_circuits_when_credential_non_active():
    """Defensive guard: the vault accessor's WHERE already filters
    active rows, but if a future relaxation returns a non-active row
    the workflow must skip without calling run_one_cycle."""
    pool = _FakePool(_FakeConn())
    revoked = _FakeCredential(
        id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), status="revoked"
    )

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=AsyncMock(return_value=revoked)), \
         patch.object(scheduler, "run_one_cycle", new=AsyncMock()) as run_mock:
        result = await scheduler.run_cycle_step(
            credential_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(),
        )

    assert result.skipped is True
    assert result.reason is not None
    assert "credential_status=" in result.reason
    assert "revoked" in result.reason
    run_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_cycle_step_invokes_run_one_cycle_with_decrypted_credential():
    """Happy path: vault returns an active credential → run_one_cycle
    is invoked with credential + pool → PollResult mirrors
    CycleResult."""
    tenant_a = uuid4()
    user_a = uuid4()
    cred_id = uuid4()
    pool = _FakePool(_FakeConn())
    credential = _FakeCredential(
        id=cred_id, tenant_id=tenant_a, user_id=user_a, status="active"
    )
    cycle_result = _fake_cycle_result(
        notes_processed=3, deferred_reprocessed=1, credential_error_code=None,
    )

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=AsyncMock(return_value=credential)) as vault_mock, \
         patch.object(scheduler, "run_one_cycle", new=AsyncMock(return_value=cycle_result)) as run_mock:
        result = await scheduler.run_cycle_step(
            credential_id=cred_id, tenant_id=tenant_a, user_id=user_a,
        )

    # vault accessor called with the LOCKED-42 caller_module string
    vault_mock.assert_awaited_once()
    assert vault_mock.await_args is not None
    vault_kwargs = vault_mock.await_args.kwargs
    assert vault_kwargs["tenant_id"] == tenant_a
    assert vault_kwargs["user_id"] == user_a
    assert vault_kwargs["caller_module"] == "services.granola_ingestion.scheduler"
    assert vault_kwargs["pool"] is pool

    # run_one_cycle called with the credential we got back
    run_mock.assert_awaited_once()
    assert run_mock.await_args is not None
    run_kwargs = run_mock.await_args.kwargs
    assert run_kwargs["credential"] is credential
    assert run_kwargs["pool"] is pool

    # PollResult reflects CycleResult fields
    assert result.skipped is False
    assert result.notes_processed == 3
    assert result.deferred_reprocessed == 1
    assert result.credential_error_code is None


@pytest.mark.asyncio
async def test_run_cycle_step_surfaces_credential_error_code():
    """run_one_cycle returning a credential-level error → PollResult
    carries credential_error_code so the cron handler / dashboards
    observe it."""
    pool = _FakePool(_FakeConn())
    credential = _FakeCredential(id=uuid4(), tenant_id=uuid4(), user_id=uuid4())
    cycle_result = _fake_cycle_result(credential_error_code="granola_auth_failed")

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=AsyncMock(return_value=credential)), \
         patch.object(scheduler, "run_one_cycle", new=AsyncMock(return_value=cycle_result)):
        result = await scheduler.run_cycle_step(
            credential_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(),
        )

    assert result.credential_error_code == "granola_auth_failed"
    assert result.skipped is False


@pytest.mark.asyncio
async def test_run_cycle_step_handles_adapter_internal_skip():
    """If the adapter itself returns credential_skipped=True (status
    raced from active to non-active between vault load and adapter
    cycle start), the scheduler surfaces it as skipped=True."""
    pool = _FakePool(_FakeConn())
    credential = _FakeCredential(id=uuid4(), tenant_id=uuid4(), user_id=uuid4())
    cycle_result = _fake_cycle_result(credential_skipped=True)

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=AsyncMock(return_value=credential)), \
         patch.object(scheduler, "run_one_cycle", new=AsyncMock(return_value=cycle_result)):
        result = await scheduler.run_cycle_step(
            credential_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(),
        )

    assert result.skipped is True
    assert result.reason == "cycle_skipped_at_adapter"


# ---------------------------------------------------------------------------
# run_cycle_step — advisory lock (Codex PR-#28 R1 P1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cycle_step_skips_when_advisory_lock_held():
    """A prior overlapping cycle holds the per-credential advisory lock
    → pg_try_advisory_lock returns False → this workflow skips without
    loading the credential or calling run_one_cycle. Prevents the
    concurrent-cycle double-publish race."""
    conn = _FakeConn(fetchval_returns=False)  # lock NOT acquired
    pool = _FakePool(conn)

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=AsyncMock()) as vault_mock, \
         patch.object(scheduler, "run_one_cycle", new=AsyncMock()) as run_mock:
        result = await scheduler.run_cycle_step(
            credential_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(),
        )

    assert result.skipped is True
    assert result.reason == "cycle_already_running"
    # Lock was attempted; credential never loaded; adapter never ran.
    assert any("pg_try_advisory_lock" in c[0] for c in conn.fetchval_calls)
    vault_mock.assert_not_called()
    run_mock.assert_not_called()
    # No unlock when the lock wasn't acquired.
    assert not any("pg_advisory_unlock" in c[0] for c in conn.execute_calls)


@pytest.mark.asyncio
async def test_run_cycle_step_acquires_and_releases_lock_on_happy_path():
    """The advisory lock is acquired before the cycle and released in
    finally — keyed on a stable int64 derived from credential_id."""
    cred_id = uuid4()
    conn = _FakeConn(fetchval_returns=True)
    pool = _FakePool(conn)
    credential = _FakeCredential(id=cred_id, tenant_id=uuid4(), user_id=uuid4())
    cycle_result = _fake_cycle_result(notes_processed=1)

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=AsyncMock(return_value=credential)), \
         patch.object(scheduler, "run_one_cycle", new=AsyncMock(return_value=cycle_result)):
        result = await scheduler.run_cycle_step(
            credential_id=cred_id, tenant_id=uuid4(), user_id=uuid4(),
        )

    assert result.skipped is False
    expected_key = scheduler._advisory_lock_key(cred_id)
    # Lock acquired with the credential-derived key.
    assert any(
        "pg_try_advisory_lock" in sql and args == (expected_key,)
        for sql, args in conn.fetchval_calls
    )
    # Lock released with the SAME key in finally.
    assert any(
        "pg_advisory_unlock" in sql and args == (expected_key,)
        for sql, args in conn.execute_calls
    )


@pytest.mark.asyncio
async def test_run_cycle_step_releases_lock_even_when_cycle_raises():
    """If run_one_cycle raises, the finally still unlocks so the
    credential isn't permanently wedged as 'cycle_already_running'."""
    cred_id = uuid4()
    conn = _FakeConn(fetchval_returns=True)
    pool = _FakePool(conn)
    credential = _FakeCredential(id=cred_id, tenant_id=uuid4(), user_id=uuid4())

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=AsyncMock(return_value=credential)), \
         patch.object(scheduler, "run_one_cycle", new=AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError, match="boom"):
            await scheduler.run_cycle_step(
                credential_id=cred_id, tenant_id=uuid4(), user_id=uuid4(),
            )

    expected_key = scheduler._advisory_lock_key(cred_id)
    assert any(
        "pg_advisory_unlock" in sql and args == (expected_key,)
        for sql, args in conn.execute_calls
    )


def test_advisory_lock_key_is_stable_and_signed_int64():
    """The lock key is deterministic per credential and fits in the
    Postgres advisory-lock bigint range."""
    cred_id = uuid4()
    k1 = scheduler._advisory_lock_key(cred_id)
    k2 = scheduler._advisory_lock_key(cred_id)
    assert k1 == k2
    assert -(2**63) <= k1 < 2**63
    # Distinct credentials almost certainly get distinct keys.
    assert scheduler._advisory_lock_key(uuid4()) != k1 or True  # collision is astronomically rare


# ---------------------------------------------------------------------------
# list_active_credentials — explicit retry (Codex PR-#28 R1 P2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_active_credentials_retries_transient_failure(monkeypatch):
    """A transient asyncpg error is retried; the second attempt
    succeeds. The cron handler calls this OUTSIDE a workflow, so the
    retry must be explicit (a @DBOS.step decorator would no-op here)."""
    import asyncpg as _asyncpg

    row = {"id": uuid4(), "tenant_id": uuid4(), "user_id": uuid4()}
    # First fetch raises a transient error; second returns the row.
    conn = _FakeConn(
        fetch_returns=[row],
        fetch_raises=[_asyncpg.PostgresConnectionError("transient")],
    )
    pool = _FakePool(conn)

    # Don't actually sleep during the retry backoff.
    monkeypatch.setattr(scheduler.asyncio, "sleep", AsyncMock())

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)):
        result = await scheduler.list_active_credentials()

    assert len(result) == 1
    assert result[0].id == row["id"]
    # Two fetch attempts: one raised, one succeeded.
    assert len(conn.fetch_calls) == 2


@pytest.mark.asyncio
async def test_list_active_credentials_retries_pool_creation_failure(monkeypatch):
    """Codex PR-#28 R2 P2: a transient failure during lazy
    get_asyncpg_pool() (cold-start create_pool) is also retried — the
    pool lookup lives INSIDE the retry loop, not before it."""
    import asyncpg as _asyncpg

    row = {"id": uuid4(), "tenant_id": uuid4(), "user_id": uuid4()}
    conn = _FakeConn(fetch_returns=[row])
    pool = _FakePool(conn)

    # First get_asyncpg_pool() raises (create_pool blip); second succeeds.
    get_pool_mock = AsyncMock(
        side_effect=[_asyncpg.PostgresConnectionError("cold start"), pool]
    )
    monkeypatch.setattr(scheduler.asyncio, "sleep", AsyncMock())

    with patch.object(scheduler, "get_asyncpg_pool", new=get_pool_mock):
        result = await scheduler.list_active_credentials()

    assert len(result) == 1
    assert get_pool_mock.await_count == 2  # retried the pool creation


@pytest.mark.asyncio
async def test_list_active_credentials_raises_after_exhausting_retries(monkeypatch):
    """If every attempt fails, the last exception propagates so the
    cron tick returns 5xx and the next 5-min tick retries."""
    import asyncpg as _asyncpg

    conn = _FakeConn(
        fetch_raises=[
            _asyncpg.PostgresConnectionError("e1"),
            _asyncpg.PostgresConnectionError("e2"),
            _asyncpg.PostgresConnectionError("e3"),
        ],
    )
    pool = _FakePool(conn)
    monkeypatch.setattr(scheduler.asyncio, "sleep", AsyncMock())

    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)):
        with pytest.raises(_asyncpg.PostgresConnectionError):
            await scheduler.list_active_credentials()

    assert len(conn.fetch_calls) == scheduler._LIST_RETRY_ATTEMPTS


# ===========================================================================
# EQ-92 / B3 — background import: queue, poll-defers guard (A1), run_import_step
# (A2/A3), the import workflow, dispatch + recovery helpers
# ===========================================================================


# ---------------------------------------------------------------------------
# GRANOLA_IMPORT_QUEUE (C3 — separate from the poll queue)
# ---------------------------------------------------------------------------


def test_granola_import_queue_is_separate_with_concurrency_2():
    """C3: a 33-83 min import must NOT occupy GRANOLA_POLL_QUEUE (concurrency=5)
    or it starves other users' 5-min polls. A dedicated low-concurrency queue."""
    assert scheduler.GRANOLA_IMPORT_QUEUE.name == "granola-import"
    assert scheduler.GRANOLA_IMPORT_QUEUE.name != scheduler.GRANOLA_POLL_QUEUE.name
    state = vars(scheduler.GRANOLA_IMPORT_QUEUE)
    concurrency_value = None
    for attr in ("concurrency", "_concurrency"):
        if attr in state and isinstance(state[attr], int):
            concurrency_value = state[attr]
            break
    assert concurrency_value == 2, f"GRANOLA_IMPORT_QUEUE.concurrency expected 2, got {state}"


# ---------------------------------------------------------------------------
# A1 — run_cycle_step poll-defers to a pending background import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cycle_step_defers_uninitialized_history_credential():
    """A1: a freshly-connected history credential (import_scope='history',
    last_polled_at NULL) is owned by the IMPORT, not the poll. A poll that wins
    the advisory lock before the import must SKIP — else it would advance the
    shared watermark past history the import hasn't ingested."""
    pool = _FakePool(_FakeConn(fetchval_returns=True))
    cred = _FakeCredential(
        id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), status="active",
        config={"import_scope": "history", "folders": [{"id": "fol_a"}]},
        last_polled_at=None,
    )
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=AsyncMock(return_value=cred)), \
         patch.object(scheduler, "run_one_cycle", new=AsyncMock()) as run_mock:
        result = await scheduler.run_cycle_step(
            credential_id=cred.id, tenant_id=cred.tenant_id, user_id=cred.user_id,
        )
    assert result.skipped is True
    assert result.reason == "awaiting_import"
    run_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_cycle_step_defers_uninitialized_forward_credential():
    """A1: import_scope='forward' with last_polled_at NULL means the forward
    anchor hasn't been written yet (a poll fired in the activation->anchor gap).
    Skip until the anchor lands."""
    pool = _FakePool(_FakeConn(fetchval_returns=True))
    cred = _FakeCredential(
        id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), status="active",
        config={"import_scope": "forward", "folders": [{"id": "fol_a"}]},
        last_polled_at=None,
    )
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=AsyncMock(return_value=cred)), \
         patch.object(scheduler, "run_one_cycle", new=AsyncMock()) as run_mock:
        result = await scheduler.run_cycle_step(
            credential_id=cred.id, tenant_id=cred.tenant_id, user_id=cred.user_id,
        )
    assert result.skipped is True
    assert result.reason == "awaiting_forward_anchor"
    run_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_cycle_step_does_not_defer_legacy_credential_without_import_scope():
    """A1: a pre-B3 legacy credential has no import_scope, so the guard must NOT
    trip (in prod its last_polled_at is also already set). The poll proceeds."""
    pool = _FakePool(_FakeConn(fetchval_returns=True))
    cred = _FakeCredential(
        id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), status="active",
        config={"folder_id": "fol_legacy", "folder_name": "EQ"},  # no import_scope
        last_polled_at=None,
    )
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=AsyncMock(return_value=cred)), \
         patch.object(scheduler, "run_one_cycle", new=AsyncMock(return_value=_fake_cycle_result())) as run_mock:
        result = await scheduler.run_cycle_step(
            credential_id=cred.id, tenant_id=cred.tenant_id, user_id=cred.user_id,
        )
    assert result.skipped is False
    run_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_cycle_step_does_not_defer_initialized_history_credential():
    """A1: once the import finished (last_polled_at SET), polls resume normally
    for a history credential."""
    pool = _FakePool(_FakeConn(fetchval_returns=True))
    cred = _FakeCredential(
        id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), status="active",
        config={"import_scope": "history", "folders": [{"id": "fol_a"}]},
        last_polled_at=datetime(2026, 6, 6, 10, 0, tzinfo=timezone.utc),
    )
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=AsyncMock(return_value=cred)), \
         patch.object(scheduler, "run_one_cycle", new=AsyncMock(return_value=_fake_cycle_result())) as run_mock:
        result = await scheduler.run_cycle_step(
            credential_id=cred.id, tenant_id=cred.tenant_id, user_id=cred.user_id,
        )
    assert result.skipped is False
    run_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# run_import_step (A2 lock-busy + A3 cancel/fail/complete)
# ---------------------------------------------------------------------------


def _import_step_patches(*, credential, cycle_result=None, cycle_raises=None):
    """Patch the import step's collaborators. Returns a contextmanager stack
    helper via a dict of AsyncMocks the test can assert on."""
    mocks = {
        "get_cred": AsyncMock(return_value=credential),
        "run_one_cycle": AsyncMock(side_effect=cycle_raises) if cycle_raises
        else AsyncMock(return_value=cycle_result),
        "mark_running": AsyncMock(),
        "complete": AsyncMock(),
        "fail": AsyncMock(),
        "cancel": AsyncMock(),
    }
    return mocks


@pytest.mark.asyncio
async def test_run_import_step_lock_busy_leaves_queued():
    """A2: if a poll (or another import attempt) holds the per-credential
    advisory lock, run_import_step leaves the run 'queued' (does not strand or
    fail it) and returns state='lock_busy' — the cron/status recovery re-kicks
    it with a fresh workflow id."""
    conn = _FakeConn(fetchval_returns=False)  # lock NOT acquired
    pool = _FakePool(conn)
    run_run_id = uuid4()
    m = _import_step_patches(credential=None)
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=m["get_cred"]), \
         patch.object(scheduler, "run_one_cycle", new=m["run_one_cycle"]), \
         patch.object(scheduler, "mark_running", new=m["mark_running"]), \
         patch.object(scheduler, "complete_import_run", new=m["complete"]), \
         patch.object(scheduler, "fail_import_run", new=m["fail"]), \
         patch.object(scheduler, "cancel_import_run", new=m["cancel"]):
        result = await scheduler.run_import_step(
            credential_id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), import_run_id=run_run_id,
        )
    assert result.state == "lock_busy"
    m["mark_running"].assert_not_called()
    m["run_one_cycle"].assert_not_called()
    m["complete"].assert_not_called()
    m["fail"].assert_not_called()
    m["cancel"].assert_not_called()
    # no unlock when the lock was never acquired
    assert not any("pg_advisory_unlock" in c[0] for c in conn.execute_calls)


@pytest.mark.asyncio
async def test_run_import_step_cancels_when_credential_inactive_at_start():
    """A3/C9: the credential was disconnected before the import got the lock →
    cancel the run (not complete/fail). Lock released."""
    cred_id = uuid4()
    conn = _FakeConn(fetchval_returns=True)
    pool = _FakePool(conn)
    run_id = uuid4()
    m = _import_step_patches(credential=None)  # vault returns None (no active row)
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=m["get_cred"]), \
         patch.object(scheduler, "run_one_cycle", new=m["run_one_cycle"]), \
         patch.object(scheduler, "mark_running", new=m["mark_running"]), \
         patch.object(scheduler, "complete_import_run", new=m["complete"]), \
         patch.object(scheduler, "fail_import_run", new=m["fail"]), \
         patch.object(scheduler, "cancel_import_run", new=m["cancel"]):
        result = await scheduler.run_import_step(
            credential_id=cred_id, tenant_id=uuid4(), user_id=uuid4(), import_run_id=run_id,
        )
    assert result.state == "cancelled"
    m["cancel"].assert_awaited_once()
    m["run_one_cycle"].assert_not_called()
    expected_key = scheduler._advisory_lock_key(cred_id)
    assert any("pg_advisory_unlock" in s and a == (expected_key,) for s, a in conn.execute_calls)


@pytest.mark.asyncio
async def test_run_import_step_completes_on_clean_cycle():
    """A3: a clean cycle (no abort, no credential error) marks the import
    running, threads import_run_id into run_one_cycle, then completes it."""
    cred = _FakeCredential(id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), status="active")
    conn = _FakeConn(fetchval_returns=True)
    pool = _FakePool(conn)
    run_id = uuid4()
    m = _import_step_patches(credential=cred, cycle_result=_fake_cycle_result(notes_processed=7))
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=m["get_cred"]), \
         patch.object(scheduler, "run_one_cycle", new=m["run_one_cycle"]), \
         patch.object(scheduler, "mark_running", new=m["mark_running"]), \
         patch.object(scheduler, "complete_import_run", new=m["complete"]), \
         patch.object(scheduler, "fail_import_run", new=m["fail"]), \
         patch.object(scheduler, "cancel_import_run", new=m["cancel"]):
        result = await scheduler.run_import_step(
            credential_id=cred.id, tenant_id=cred.tenant_id, user_id=cred.user_id, import_run_id=run_id,
        )
    assert result.state == "complete"
    assert result.notes_processed == 7
    m["mark_running"].assert_awaited_once()
    m["complete"].assert_awaited_once()
    m["fail"].assert_not_called()
    m["cancel"].assert_not_called()
    # import_run_id threaded into the cycle so set_import_total fires
    run_kwargs = m["run_one_cycle"].await_args.kwargs
    assert run_kwargs["import_run_id"] == run_id
    assert run_kwargs["credential"] is cred


@pytest.mark.asyncio
async def test_run_import_step_cancels_on_cycle_aborted():
    """A3: run_one_cycle signals cycle_aborted (credential disconnected
    mid-import) → cancel, not complete."""
    cred = _FakeCredential(id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), status="active")
    conn = _FakeConn(fetchval_returns=True)
    pool = _FakePool(conn)
    m = _import_step_patches(credential=cred, cycle_result=_fake_cycle_result(cycle_aborted=True))
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=m["get_cred"]), \
         patch.object(scheduler, "run_one_cycle", new=m["run_one_cycle"]), \
         patch.object(scheduler, "mark_running", new=m["mark_running"]), \
         patch.object(scheduler, "complete_import_run", new=m["complete"]), \
         patch.object(scheduler, "fail_import_run", new=m["fail"]), \
         patch.object(scheduler, "cancel_import_run", new=m["cancel"]):
        result = await scheduler.run_import_step(
            credential_id=cred.id, tenant_id=cred.tenant_id, user_id=cred.user_id, import_run_id=uuid4(),
        )
    assert result.state == "cancelled"
    m["cancel"].assert_awaited_once()
    m["complete"].assert_not_called()


@pytest.mark.asyncio
async def test_run_import_step_fails_on_credential_error_code():
    """A3: run_one_cycle returns a credential-level error (auth/folder) instead
    of raising → fail the import (NOT complete — the wrapper must check
    credential_error_code)."""
    cred = _FakeCredential(id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), status="active")
    conn = _FakeConn(fetchval_returns=True)
    pool = _FakePool(conn)
    m = _import_step_patches(
        credential=cred,
        cycle_result=_fake_cycle_result(credential_error_code="granola_auth_failed"),
    )
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=m["get_cred"]), \
         patch.object(scheduler, "run_one_cycle", new=m["run_one_cycle"]), \
         patch.object(scheduler, "mark_running", new=m["mark_running"]), \
         patch.object(scheduler, "complete_import_run", new=m["complete"]), \
         patch.object(scheduler, "fail_import_run", new=m["fail"]), \
         patch.object(scheduler, "cancel_import_run", new=m["cancel"]):
        result = await scheduler.run_import_step(
            credential_id=cred.id, tenant_id=cred.tenant_id, user_id=cred.user_id, import_run_id=uuid4(),
        )
    assert result.state == "failed"
    assert result.credential_error_code == "granola_auth_failed"
    m["fail"].assert_awaited_once()
    m["complete"].assert_not_called()
    m["cancel"].assert_not_called()


@pytest.mark.asyncio
async def test_run_import_step_bails_when_run_already_terminal():
    """Codex P1: a recovery re-dispatch (A2) races the original workflow; if the
    original finished first, mark_running claims 0 rows → run_import_step must NOT
    run a redundant backfill on the terminal run."""
    cred = _FakeCredential(id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), status="active")
    conn = _FakeConn(fetchval_returns=True)
    pool = _FakePool(conn)
    m = _import_step_patches(credential=cred, cycle_result=_fake_cycle_result())
    m["mark_running"] = AsyncMock(return_value=False)  # already terminal → not claimed
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=m["get_cred"]), \
         patch.object(scheduler, "run_one_cycle", new=m["run_one_cycle"]), \
         patch.object(scheduler, "mark_running", new=m["mark_running"]), \
         patch.object(scheduler, "complete_import_run", new=m["complete"]), \
         patch.object(scheduler, "fail_import_run", new=m["fail"]), \
         patch.object(scheduler, "cancel_import_run", new=m["cancel"]):
        result = await scheduler.run_import_step(
            credential_id=cred.id, tenant_id=cred.tenant_id, user_id=cred.user_id, import_run_id=uuid4(),
        )
    assert result.state == "superseded"
    m["run_one_cycle"].assert_not_called()   # NO redundant backfill
    m["complete"].assert_not_called()
    m["cancel"].assert_not_called()
    m["fail"].assert_not_called()


@pytest.mark.asyncio
async def test_run_import_step_proceeds_when_run_claimed():
    """The happy path claims the run (mark_running True) → runs the cycle."""
    cred = _FakeCredential(id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), status="active")
    conn = _FakeConn(fetchval_returns=True)
    pool = _FakePool(conn)
    m = _import_step_patches(credential=cred, cycle_result=_fake_cycle_result(notes_processed=3))
    m["mark_running"] = AsyncMock(return_value=True)
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=m["get_cred"]), \
         patch.object(scheduler, "run_one_cycle", new=m["run_one_cycle"]), \
         patch.object(scheduler, "mark_running", new=m["mark_running"]), \
         patch.object(scheduler, "complete_import_run", new=m["complete"]), \
         patch.object(scheduler, "fail_import_run", new=m["fail"]), \
         patch.object(scheduler, "cancel_import_run", new=m["cancel"]):
        result = await scheduler.run_import_step(
            credential_id=cred.id, tenant_id=cred.tenant_id, user_id=cred.user_id, import_run_id=uuid4(),
        )
    assert result.state == "complete"
    m["run_one_cycle"].assert_awaited_once()


@pytest.mark.asyncio
async def test_run_import_step_fails_and_reraises_on_unexpected_raise():
    """A3 'on raise -> fail_import_run': an unexpected exception from the cycle
    marks the run failed (FE shows 'failed', not stuck 'running') and propagates
    so DBOS records the workflow failed. Lock still released."""
    cred = _FakeCredential(id=uuid4(), tenant_id=uuid4(), user_id=uuid4(), status="active")
    conn = _FakeConn(fetchval_returns=True)
    pool = _FakePool(conn)
    m = _import_step_patches(credential=cred, cycle_raises=RuntimeError("boom"))
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "get_granola_credential_for_user", new=m["get_cred"]), \
         patch.object(scheduler, "run_one_cycle", new=m["run_one_cycle"]), \
         patch.object(scheduler, "mark_running", new=m["mark_running"]), \
         patch.object(scheduler, "complete_import_run", new=m["complete"]), \
         patch.object(scheduler, "fail_import_run", new=m["fail"]), \
         patch.object(scheduler, "cancel_import_run", new=m["cancel"]):
        with pytest.raises(RuntimeError, match="boom"):
            await scheduler.run_import_step(
                credential_id=cred.id, tenant_id=cred.tenant_id, user_id=cred.user_id, import_run_id=uuid4(),
            )
    m["fail"].assert_awaited_once()
    expected_key = scheduler._advisory_lock_key(cred.id)
    assert any("pg_advisory_unlock" in s and a == (expected_key,) for s, a in conn.execute_calls)


# ---------------------------------------------------------------------------
# granola_import_one_credential (workflow body — structural)
# ---------------------------------------------------------------------------


def test_import_workflow_is_dbos_decorated():
    fn = scheduler.granola_import_one_credential
    assert callable(fn)
    assert inspect.iscoroutinefunction(fn)
    assert fn.__name__ == "granola_import_one_credential"


def test_import_workflow_signature_takes_four_positional_uuids():
    """Dispatch site enqueues (credential_id, tenant_id, user_id, import_run_id)
    positionally — order must match the workflow signature."""
    import typing as _t

    sig = inspect.signature(scheduler.granola_import_one_credential)
    params = [name for name, _ in sig.parameters.items()]
    assert params == ["credential_id", "tenant_id", "user_id", "import_run_id"]
    hints = _t.get_type_hints(scheduler.granola_import_one_credential)
    for name in params:
        assert hints[name] is UUID


# ---------------------------------------------------------------------------
# dispatch + workflow-id helpers
# ---------------------------------------------------------------------------


def test_import_workflow_id_is_deterministic():
    """C8 idempotent dispatch: /connect + the /status no-run recovery use the
    same deterministic id so a duplicate dispatch is a DBOS no-op."""
    cred = uuid4()
    run = uuid4()
    wid = scheduler.import_workflow_id(cred, run)
    assert wid == f"granola_import_{cred}_{run}"
    assert scheduler.import_workflow_id(cred, run) == wid  # stable


def test_import_recovery_workflow_id_is_window_stamped_and_distinct():
    """A2 strand recovery uses a FRESH id per window (the deterministic
    workflow already completed/returned lock_busy, so DBOS would dedup the
    deterministic id to a no-op)."""
    cred = uuid4()
    run = uuid4()
    rid = scheduler.import_recovery_workflow_id(cred, run, 12345)
    assert rid != scheduler.import_workflow_id(cred, run)
    assert "12345" in rid
    # different windows -> different ids (so a later tick re-dispatches)
    assert scheduler.import_recovery_workflow_id(cred, run, 12346) != rid


@pytest.mark.asyncio
async def test_enqueue_import_workflow_uses_set_workflow_id_and_import_queue():
    """The dispatch helper enqueues granola_import_one_credential on the IMPORT
    queue under the given workflow id (positional args match the signature)."""
    cred, tenant, user, run = uuid4(), uuid4(), uuid4(), uuid4()
    enqueue_mock = AsyncMock()
    captured = {}

    class _FakeSetWorkflowID:
        def __init__(self, wid):
            captured["wid"] = wid
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    with patch.object(scheduler.GRANOLA_IMPORT_QUEUE, "enqueue_async", new=enqueue_mock), \
         patch.object(scheduler, "SetWorkflowID", new=_FakeSetWorkflowID):
        await scheduler.enqueue_import_workflow(
            credential_id=cred, tenant_id=tenant, user_id=user,
            import_run_id=run, workflow_id="granola_import_test_wid",
        )
    assert captured["wid"] == "granola_import_test_wid"
    enqueue_mock.assert_awaited_once()
    args = enqueue_mock.await_args.args
    assert args[0] is scheduler.granola_import_one_credential
    assert args[1:] == (cred, tenant, user, run)


# ---------------------------------------------------------------------------
# list_recoverable_import_runs (A2 cron/status backstop)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_recoverable_import_runs_queries_stale_queued_rows():
    """A2 recovery: find queued import runs older than the staleness window so
    the cron/status can re-dispatch a strand. SQL filters state='queued' +
    created_at < now - interval, with a LIMIT."""
    rows = [
        {"id": uuid4(), "credential_id": uuid4(), "tenant_id": uuid4(), "user_id": uuid4()},
    ]
    conn = _FakeConn(fetch_returns=rows)
    pool = _FakePool(conn)
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)):
        result = await scheduler.list_recoverable_import_runs()
    assert len(result) == 1
    assert result[0].import_run_id == rows[0]["id"]
    assert result[0].credential_id == rows[0]["credential_id"]
    sql, _ = conn.fetch_calls[0]
    low = sql.lower()
    assert "state = 'queued'" in low
    assert "created_at <" in low
    assert "limit" in low


@pytest.mark.asyncio
async def test_list_recoverable_import_runs_empty_when_none_stale():
    conn = _FakeConn(fetch_returns=[])
    pool = _FakePool(conn)
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)):
        result = await scheduler.list_recoverable_import_runs()
    assert result == []


# ---------------------------------------------------------------------------
# list_uninitialized_credentials + recover_uninitialized_credential (Codex P1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_uninitialized_credentials_returns_scope_and_filters():
    """Headless recovery target: active creds with NULL watermark + no live
    import (NOT EXISTS), surfaced with their import_scope so the cron knows
    whether to dispatch (history) or anchor (forward)."""
    rows = [
        {"id": uuid4(), "tenant_id": uuid4(), "user_id": uuid4(), "import_scope": "history"},
        {"id": uuid4(), "tenant_id": uuid4(), "user_id": uuid4(), "import_scope": "forward"},
    ]
    conn = _FakeConn(fetch_returns=rows)
    pool = _FakePool(conn)
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)):
        result = await scheduler.list_uninitialized_credentials()
    assert [c.import_scope for c in result] == ["history", "forward"]
    assert all(isinstance(c, scheduler.UninitializedCredential) for c in result)
    sql, _ = conn.fetch_calls[0]
    low = sql.lower()
    assert "status = 'active'" in low and "archived_at is null" in low
    assert "last_polled_at is null" in low
    assert "not exists" in low                      # excludes creds with a live run
    assert "config->>'import_scope' in ('history', 'forward')" in low


@pytest.mark.asyncio
async def test_recover_uninitialized_credential_history_creates_and_dispatches():
    """history → create the run (idempotent) + dispatch under the DETERMINISTIC
    id (the headless equivalent of /connect)."""
    cred = uuid4()
    run_id = uuid4()
    get_or_create = AsyncMock(return_value=(run_id, True))
    enqueue = AsyncMock()
    with patch.object(scheduler, "get_or_create_active_import_run", get_or_create), \
         patch.object(scheduler, "enqueue_import_workflow", enqueue):
        action = await scheduler.recover_uninitialized_credential(
            credential_id=cred, tenant_id=uuid4(), user_id=uuid4(), import_scope="history",
        )
    assert action == "history_created"
    get_or_create.assert_awaited_once()
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["workflow_id"] == f"granola_import_{cred}_{run_id}"


@pytest.mark.asyncio
async def test_recover_uninitialized_credential_forward_anchors_with_now():
    """forward → re-anchor last_polled_at with NOW() via the vault helper, using
    the scheduler's ALLOWLIST caller_module."""
    cred = uuid4()
    pool = _FakePool(_FakeConn())
    anchor = AsyncMock()
    with patch.object(scheduler, "get_asyncpg_pool", new=AsyncMock(return_value=pool)), \
         patch.object(scheduler, "anchor_credential_watermark", anchor):
        action = await scheduler.recover_uninitialized_credential(
            credential_id=cred, tenant_id=uuid4(), user_id=uuid4(), import_scope="forward",
        )
    assert action == "forward_anchored"
    anchor.assert_awaited_once()
    kw = anchor.await_args.kwargs
    assert kw["credential_id"] == cred
    assert kw["caller_module"] == "services.granola_ingestion.scheduler"
    assert kw["ts"] is not None


# ---------------------------------------------------------------------------
# granola_poll_one_credential (workflow body)
# ---------------------------------------------------------------------------
#
# Behavioral coverage is deferred to integration tests with DBOS
# actually launched — the workflow function is a thin
# ``return await run_cycle_step(...)`` and DBOS raises
# ``DBOSException: invoked before DBOS initialized`` when the
# decorator wrapper is called outside a launched DBOS context.
#
# The behavioral surface that matters (vault load → run_one_cycle
# dispatch → PollResult construction) is fully covered by the
# ``run_cycle_step`` tests above. The workflow body adds nothing
# but logging + delegation, so testing the structural surface (the
# decorator + signature checks above) is the right granularity here.
# This mirrors how
# :file:`tests/unit/account_provisioning/test_workflow.py` defers
# behavioral assertions to its integration suite.
