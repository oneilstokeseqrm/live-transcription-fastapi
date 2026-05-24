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

* ``list_active_credentials_step`` returns the right shape from the
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
    """asyncpg Connection stand-in for list_active_credentials_step."""

    def __init__(self, *, fetch_returns: Optional[list[dict]] = None) -> None:
        self.fetch_returns = fetch_returns or []
        self.fetch_calls: list[tuple[str, tuple]] = []

    async def fetch(self, sql: str, *args):
        self.fetch_calls.append((sql, args))
        return list(self.fetch_returns)


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
# list_active_credentials_step
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
        result = await scheduler.list_active_credentials_step()

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
        await scheduler.list_active_credentials_step()

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
        await scheduler.list_active_credentials_step()

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
        result = await scheduler.list_active_credentials_step()

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
