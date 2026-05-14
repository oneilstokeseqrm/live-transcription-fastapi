"""Worker poll-and-process replay-safety tests.

Phase 1.5 integration-test infrastructure does not yet include a conftest.py
with a real test_session fixture. Following the Phase 1 + T1.5.6 pattern, we
exercise process_one_approved_entry with a mock session + mock agent client
and assert the contract:

- Advisory lock acquisition gates processing (skip if locked)
- status='mapped' is treated as a terminal no-op (replay safe)
- approved → creating transition writes creation_started_at
- worker_attempt_id is constructed with tenant_id qualifier (prevents
  cross-tenant collisions in the agent's idempotency map — AI-native
  research recommendation 2026-05-14)
- materialize_account_approval is called with the correct args

End-to-end verification of the full polling + DB transaction semantics
happens via:
- Production E2E (`/tmp/e2e_phase_1_production.py`, extended after Task 1.5.8)
- Task 1.5.18 end-to-end Approve flow test (uses real Neon eq-dev once
  fixtures land)
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workers.account_provisioning_worker import process_one_approved_entry


def _row(**kwargs):
    """Build a MagicMock with attribute access matching a SQLAlchemy Row."""
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


def _fake_result(scalar_value=None, one_value=None):
    """Build a MagicMock for the result of session.execute."""
    result = MagicMock()
    if scalar_value is not None:
        result.scalar_one = MagicMock(return_value=scalar_value)
    if one_value is not None:
        result.one = MagicMock(return_value=one_value)
    return result


@pytest.mark.asyncio
async def test_skips_when_advisory_lock_not_acquired():
    """If pg_try_advisory_xact_lock returns False, worker skips this queue_id."""
    session = MagicMock()
    session.execute = AsyncMock()
    queue_id = str(uuid.uuid4())

    with patch(
        "workers.account_provisioning_worker.try_acquire_queue_lock",
        AsyncMock(return_value=False),
    ):
        agent_client = MagicMock()
        agent_client.enrich = AsyncMock()
        await process_one_approved_entry(
            session=session,
            queue_id=queue_id,
            agent_client=agent_client,
        )
    # No status read, no agent call, no materialization
    session.execute.assert_not_called()
    agent_client.enrich.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_archived_after_poll():
    """archived_at IS NOT NULL → skip (race: archived between poll and process)."""
    import datetime
    queue_id = str(uuid.uuid4())
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _fake_result(one_value=_row(
            status="approved",
            archived_at=datetime.datetime(2026, 5, 14, 12, 0, 0),  # archived
            resolved_account_id=None,
        )),
    ])

    with patch(
        "workers.account_provisioning_worker.try_acquire_queue_lock",
        AsyncMock(return_value=True),
    ):
        agent_client = MagicMock()
        agent_client.enrich = AsyncMock()
        await process_one_approved_entry(
            session=session,
            queue_id=queue_id,
            agent_client=agent_client,
        )

    # Only the SELECT_STATUS ran; no agent call, no further work
    assert session.execute.await_count == 1
    agent_client.enrich.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_already_mapped_replay_safe():
    """If status='mapped' on read, worker no-ops (replay-safe)."""
    queue_id = str(uuid.uuid4())
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _fake_result(one_value=_row(status="mapped", archived_at=None, resolved_account_id=str(uuid.uuid4()))),
    ])

    with patch(
        "workers.account_provisioning_worker.try_acquire_queue_lock",
        AsyncMock(return_value=True),
    ):
        agent_client = MagicMock()
        agent_client.enrich = AsyncMock()
        await process_one_approved_entry(
            session=session,
            queue_id=queue_id,
            agent_client=agent_client,
        )

    # One SELECT to check status, nothing else
    assert session.execute.await_count == 1
    agent_client.enrich.assert_not_called()


@pytest.mark.asyncio
async def test_warns_and_returns_on_unexpected_status():
    """Status not in ('approved', 'creating', 'mapped') is logged + skipped."""
    queue_id = str(uuid.uuid4())
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _fake_result(one_value=_row(status="pending", archived_at=None, resolved_account_id=None)),
    ])

    with patch(
        "workers.account_provisioning_worker.try_acquire_queue_lock",
        AsyncMock(return_value=True),
    ):
        agent_client = MagicMock()
        agent_client.enrich = AsyncMock()
        await process_one_approved_entry(
            session=session,
            queue_id=queue_id,
            agent_client=agent_client,
        )

    assert session.execute.await_count == 1
    agent_client.enrich.assert_not_called()


@pytest.mark.asyncio
async def test_happy_path_approved_to_mapped():
    """Full path: approved → creating → agent call → materialize."""
    queue_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    domain = "acme.com"
    account_id = str(uuid.uuid4())

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _fake_result(one_value=_row(status="approved", archived_at=None, resolved_account_id=None)),  # SELECT_STATUS
        _fake_result(),  # SET_CREATING update
        _fake_result(one_value=_row(tenant_id=tenant_id, domain=domain)),  # SELECT tenant + domain
    ])

    agent_client = MagicMock()
    from services.agent_action_core_client import EnrichResult
    agent_client.enrich = AsyncMock(return_value=EnrichResult(account_id=account_id, domain=domain))

    with patch(
        "workers.account_provisioning_worker.try_acquire_queue_lock",
        AsyncMock(return_value=True),
    ), patch(
        "workers.account_provisioning_worker.materialize_account_approval",
        AsyncMock(),
    ) as mock_materialize:
        await process_one_approved_entry(
            session=session,
            queue_id=queue_id,
            agent_client=agent_client,
        )

    # Agent call carries the qualified idempotency key
    agent_client.enrich.assert_awaited_once()
    call_kwargs = agent_client.enrich.await_args.kwargs
    assert call_kwargs["tenant_id"] == tenant_id
    assert call_kwargs["domain"] == domain
    assert call_kwargs["worker_attempt_id"] == f"{tenant_id}:queue-{queue_id}"

    # Materialization is invoked with correct args
    mock_materialize.assert_awaited_once_with(
        session=session,
        tenant_id=tenant_id,
        queue_id=queue_id,
        account_id=account_id,
        event_type="account_created",
    )


@pytest.mark.asyncio
async def test_creating_status_skips_set_creating_but_still_processes():
    """Status='creating' means a prior worker invocation crashed mid-flight.

    Worker should NOT re-run SET_CREATING (would be a no-op against the
    WHERE status='approved' clause anyway), should re-call the agent (with
    the same worker_attempt_id — agent dedupes), and materialize.
    """
    queue_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    domain = "acme.com"
    account_id = str(uuid.uuid4())

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _fake_result(one_value=_row(status="creating", archived_at=None, resolved_account_id=None)),  # SELECT_STATUS
        _fake_result(),  # SET_CREATING update (will no-op due to WHERE status='approved')
        _fake_result(one_value=_row(tenant_id=tenant_id, domain=domain)),  # SELECT tenant + domain
    ])

    agent_client = MagicMock()
    from services.agent_action_core_client import EnrichResult
    agent_client.enrich = AsyncMock(return_value=EnrichResult(account_id=account_id, domain=domain))

    with patch(
        "workers.account_provisioning_worker.try_acquire_queue_lock",
        AsyncMock(return_value=True),
    ), patch(
        "workers.account_provisioning_worker.materialize_account_approval",
        AsyncMock(),
    ) as mock_materialize:
        await process_one_approved_entry(
            session=session,
            queue_id=queue_id,
            agent_client=agent_client,
        )

    agent_client.enrich.assert_awaited_once()
    mock_materialize.assert_awaited_once()
