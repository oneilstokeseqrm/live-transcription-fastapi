"""Outbox publisher tests — emits unpublished account_provisioning_outbox rows to EventBridge.

Phase 1.5 integration-test infrastructure does not yet include a conftest.py
with a real test_session fixture. Following the Phase 1 + T1.5.6 + T1.5.8 pattern,
we exercise publish_one / run_publisher_loop / _build_event with mock-driven
sessions and an AsyncMock EventBridge wrapper and assert the contract:

- _build_event emits the correct Source/DetailType/Detail/EventBusName shape
- publish_one on FailedEntryCount=0 marks the row published
- publish_one on FailedEntryCount>0 records the error and raises RuntimeError
- run_publisher_loop uses a fresh session PER outbox row (per-row transactional
  isolation — carry-forward invariant from PR #12 / T1.5.7)
- run_publisher_loop's per-row try/except does NOT propagate one row's failure
  to the rest of the batch

End-to-end verification of the full polling + EventBridge round-trip happens via:
- Production E2E (`/tmp/e2e_phase_1_production.py`, extended after Task 1.5.9)
- Task 1.5.18 end-to-end Approve flow test (uses real Neon eq-dev once
  fixtures land)
"""
from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from workers.outbox_publisher import (
    MARK_FAILED_SQL,
    MARK_PUBLISHED_SQL,
    SELECT_SINGLE_SQL,
    SELECT_UNPUBLISHED_SQL,
    _build_event,
    publish_one,
    run_publisher_loop,
)


def _row(**kwargs):
    """Build a MagicMock with attribute access matching a SQLAlchemy Row."""
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


def _fake_result(one_value=None, all_rows=None):
    result = MagicMock()
    if one_value is not None:
        result.one = MagicMock(return_value=one_value)
    if all_rows is not None:
        result.all = MagicMock(return_value=all_rows)
    return result


# ---------------------------------------------------------------------------
# _build_event shape
# ---------------------------------------------------------------------------


class TestBuildEvent:
    def test_event_has_source_detail_type_and_bus(self):
        row = _row(
            id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
            event_type="account_created",
            account_id=str(uuid.uuid4()),
            payload_json={"contact_ids": ["c1"], "interaction_ids": ["i1"]},
        )

        event = _build_event(row)

        assert event["Source"] == "com.eq.contact-quality"
        assert event["DetailType"] == "AccountProvisioning.account_created"
        assert "EventBusName" in event
        assert isinstance(event["Detail"], str)  # JSON-encoded

    def test_event_detail_carries_full_outbox_payload(self):
        outbox_id = str(uuid.uuid4())
        tenant_id = str(uuid.uuid4())
        queue_id = str(uuid.uuid4())
        account_id = str(uuid.uuid4())
        payload = {"contact_ids": ["alice", "bob"], "interaction_ids": ["i1"]}

        row = _row(
            id=outbox_id,
            tenant_id=tenant_id,
            queue_id=queue_id,
            event_type="account_mapped",
            account_id=account_id,
            payload_json=payload,
        )

        event = _build_event(row)
        detail = json.loads(event["Detail"])

        assert detail["outbox_row_id"] == outbox_id
        assert detail["tenant_id"] == tenant_id
        assert detail["queue_id"] == queue_id
        assert detail["account_id"] == account_id
        assert detail["event_type"] == "account_mapped"
        assert detail["payload"] == payload

    def test_event_bus_name_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("EVENT_BUS_NAME", raising=False)
        row = _row(
            id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
            event_type="account_created",
            account_id=str(uuid.uuid4()),
            payload_json={},
        )
        assert _build_event(row)["EventBusName"] == "default"

    def test_event_bus_name_respects_env_override(self, monkeypatch):
        monkeypatch.setenv("EVENT_BUS_NAME", "eq-events-dev")
        row = _row(
            id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
            event_type="account_created",
            account_id=str(uuid.uuid4()),
            payload_json={},
        )
        assert _build_event(row)["EventBusName"] == "eq-events-dev"


# ---------------------------------------------------------------------------
# publish_one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_one_success_marks_published():
    """FailedEntryCount=0 → call MARK_PUBLISHED_SQL with the row id."""
    outbox_id = str(uuid.uuid4())
    row = _row(
        id=outbox_id,
        tenant_id=str(uuid.uuid4()),
        queue_id=str(uuid.uuid4()),
        event_type="account_created",
        account_id=str(uuid.uuid4()),
        payload_json={"contact_ids": []},
    )

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _fake_result(one_value=row),  # SELECT_SINGLE_SQL
        _fake_result(),                # MARK_PUBLISHED_SQL
    ])

    eb = MagicMock()
    eb.put_events = AsyncMock(return_value={"FailedEntryCount": 0, "Entries": [{"EventId": "abc"}]})

    await publish_one(session=session, eventbridge_client=eb, outbox_row_id=outbox_id)

    # SELECT_SINGLE + put_events + MARK_PUBLISHED
    assert session.execute.await_count == 2
    statements = [call.args[0] for call in session.execute.await_args_list]
    assert statements[0] is SELECT_SINGLE_SQL
    assert statements[1] is MARK_PUBLISHED_SQL

    eb.put_events.assert_awaited_once()
    # MARK_PUBLISHED called with the row id
    mark_call = session.execute.await_args_list[1]
    assert mark_call.args[1] == {"id": outbox_id}


@pytest.mark.asyncio
async def test_publish_one_failure_marks_failed_and_raises():
    """FailedEntryCount>0 → MARK_FAILED_SQL with truncated error, then RuntimeError."""
    outbox_id = str(uuid.uuid4())
    row = _row(
        id=outbox_id,
        tenant_id=str(uuid.uuid4()),
        queue_id=str(uuid.uuid4()),
        event_type="account_created",
        account_id=str(uuid.uuid4()),
        payload_json={},
    )

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _fake_result(one_value=row),  # SELECT_SINGLE_SQL
        _fake_result(),                # MARK_FAILED_SQL
    ])

    eb = MagicMock()
    eb.put_events = AsyncMock(return_value={
        "FailedEntryCount": 1,
        "Entries": [{"ErrorCode": "ThrottlingException", "ErrorMessage": "rate exceeded"}],
    })

    with pytest.raises(RuntimeError, match="EventBridge publish failed"):
        await publish_one(session=session, eventbridge_client=eb, outbox_row_id=outbox_id)

    # SELECT_SINGLE + put_events + MARK_FAILED (no MARK_PUBLISHED)
    assert session.execute.await_count == 2
    statements = [call.args[0] for call in session.execute.await_args_list]
    assert statements[0] is SELECT_SINGLE_SQL
    assert statements[1] is MARK_FAILED_SQL

    mark_failed_params = session.execute.await_args_list[1].args[1]
    assert mark_failed_params["id"] == outbox_id
    assert "ThrottlingException" in mark_failed_params["error"]


@pytest.mark.asyncio
async def test_publish_one_failure_truncates_long_error_messages():
    """Long EventBridge error blobs are truncated to <= 1000 chars before INSERT."""
    outbox_id = str(uuid.uuid4())
    row = _row(
        id=outbox_id,
        tenant_id=str(uuid.uuid4()),
        queue_id=str(uuid.uuid4()),
        event_type="account_created",
        account_id=str(uuid.uuid4()),
        payload_json={},
    )

    long_msg = "x" * 5000
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _fake_result(one_value=row),
        _fake_result(),
    ])
    eb = MagicMock()
    eb.put_events = AsyncMock(return_value={
        "FailedEntryCount": 1,
        "Entries": [{"ErrorCode": "InternalFailure", "ErrorMessage": long_msg}],
    })

    with pytest.raises(RuntimeError):
        await publish_one(session=session, eventbridge_client=eb, outbox_row_id=outbox_id)

    mark_failed_params = session.execute.await_args_list[1].args[1]
    assert len(mark_failed_params["error"]) <= 1000


# ---------------------------------------------------------------------------
# run_publisher_loop — per-row fresh-session pattern
# ---------------------------------------------------------------------------


class _SessionRecorder:
    """Records every (session_factory()) call and exposes the AsyncMock sessions it produced.

    Each session_factory() invocation yields a fresh MagicMock session whose
    .execute = AsyncMock(side_effect=...) — script the side_effect list to
    simulate the SELECT for the poll session and the SELECT_SINGLE+MARK pair
    for each per-row session.
    """

    def __init__(self):
        self.sessions: list[MagicMock] = []
        self.next_side_effects: list[list] = []

    def factory(self):
        @asynccontextmanager
        async def _scoped():
            session = MagicMock()
            if self.next_side_effects:
                session.execute = AsyncMock(side_effect=self.next_side_effects.pop(0))
            else:
                session.execute = AsyncMock(side_effect=[])
            # session.begin() context manager
            begin_cm = MagicMock()
            begin_cm.__aenter__ = AsyncMock(return_value=None)
            begin_cm.__aexit__ = AsyncMock(return_value=None)
            session.begin = MagicMock(return_value=begin_cm)
            self.sessions.append(session)
            yield session
        return _scoped()


@pytest.mark.asyncio
async def test_run_publisher_loop_uses_fresh_session_per_row():
    """Per-row transactional isolation: each row gets its own session_factory() call.

    This carry-forward invariant from PR #12 prevents one row's MARK_FAILED
    from rolling back a sibling row's MARK_PUBLISHED inside the same batch.
    """
    row_a_id = str(uuid.uuid4())
    row_b_id = str(uuid.uuid4())

    recorder = _SessionRecorder()
    # 1) Poll session — returns the two outbox rows.
    recorder.next_side_effects.append([
        _fake_result(all_rows=[
            _row(
                id=row_a_id,
                tenant_id=str(uuid.uuid4()),
                queue_id=str(uuid.uuid4()),
                event_type="account_created",
                account_id=str(uuid.uuid4()),
                payload_json={},
                publish_attempts=0,
            ),
            _row(
                id=row_b_id,
                tenant_id=str(uuid.uuid4()),
                queue_id=str(uuid.uuid4()),
                event_type="account_created",
                account_id=str(uuid.uuid4()),
                payload_json={},
                publish_attempts=0,
            ),
        ]),
    ])
    # 2) Per-row session for row A — success path.
    recorder.next_side_effects.append([
        _fake_result(one_value=_row(
            id=row_a_id,
            tenant_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
            event_type="account_created",
            account_id=str(uuid.uuid4()),
            payload_json={},
        )),
        _fake_result(),  # MARK_PUBLISHED
    ])
    # 3) Per-row session for row B — also success path.
    recorder.next_side_effects.append([
        _fake_result(one_value=_row(
            id=row_b_id,
            tenant_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
            event_type="account_created",
            account_id=str(uuid.uuid4()),
            payload_json={},
        )),
        _fake_result(),  # MARK_PUBLISHED
    ])

    eb = MagicMock()
    eb.put_events = AsyncMock(return_value={"FailedEntryCount": 0, "Entries": [{"EventId": "x"}]})

    # Run one iteration: use a tiny sleep + cancel trick — patch asyncio.sleep
    # to raise after the first call.
    import workers.outbox_publisher as op_mod
    original_sleep = op_mod.asyncio.sleep

    sleep_calls = {"count": 0}

    async def _one_shot_sleep(_seconds):
        sleep_calls["count"] += 1
        raise StopAsyncIteration  # break out of the while True loop

    op_mod.asyncio.sleep = _one_shot_sleep
    try:
        with pytest.raises(StopAsyncIteration):
            await run_publisher_loop(
                session_factory=recorder.factory,
                eventbridge_client=eb,
                interval_seconds=0.0,
                batch_size=10,
            )
    finally:
        op_mod.asyncio.sleep = original_sleep

    # 3 sessions total: 1 poll + 2 per-row
    assert len(recorder.sessions) == 3
    assert eb.put_events.await_count == 2


@pytest.mark.asyncio
async def test_run_publisher_loop_one_row_failure_does_not_skip_others():
    """If row A's publish raises, the loop logs + continues to row B.

    Per-row try/except inside the loop ensures isolation. The fresh-session
    pattern means row A's MARK_FAILED commit is independent of row B's
    MARK_PUBLISHED commit.
    """
    row_a_id = str(uuid.uuid4())
    row_b_id = str(uuid.uuid4())

    recorder = _SessionRecorder()
    # 1) Poll session returns both rows.
    recorder.next_side_effects.append([
        _fake_result(all_rows=[
            _row(
                id=row_a_id,
                tenant_id=str(uuid.uuid4()),
                queue_id=str(uuid.uuid4()),
                event_type="account_created",
                account_id=str(uuid.uuid4()),
                payload_json={},
                publish_attempts=0,
            ),
            _row(
                id=row_b_id,
                tenant_id=str(uuid.uuid4()),
                queue_id=str(uuid.uuid4()),
                event_type="account_created",
                account_id=str(uuid.uuid4()),
                payload_json={},
                publish_attempts=0,
            ),
        ]),
    ])
    # 2) Per-row session for row A — failure path.
    recorder.next_side_effects.append([
        _fake_result(one_value=_row(
            id=row_a_id,
            tenant_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
            event_type="account_created",
            account_id=str(uuid.uuid4()),
            payload_json={},
        )),
        _fake_result(),  # MARK_FAILED
    ])
    # 3) Per-row session for row B — success path.
    recorder.next_side_effects.append([
        _fake_result(one_value=_row(
            id=row_b_id,
            tenant_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
            event_type="account_created",
            account_id=str(uuid.uuid4()),
            payload_json={},
        )),
        _fake_result(),  # MARK_PUBLISHED
    ])

    eb = MagicMock()
    # Row A: fail. Row B: succeed.
    eb.put_events = AsyncMock(side_effect=[
        {"FailedEntryCount": 1, "Entries": [{"ErrorCode": "Throttled", "ErrorMessage": "no"}]},
        {"FailedEntryCount": 0, "Entries": [{"EventId": "x"}]},
    ])

    import workers.outbox_publisher as op_mod
    original_sleep = op_mod.asyncio.sleep

    async def _one_shot_sleep(_seconds):
        raise StopAsyncIteration

    op_mod.asyncio.sleep = _one_shot_sleep
    try:
        with pytest.raises(StopAsyncIteration):
            await run_publisher_loop(
                session_factory=recorder.factory,
                eventbridge_client=eb,
                interval_seconds=0.0,
                batch_size=10,
            )
    finally:
        op_mod.asyncio.sleep = original_sleep

    # Both rows attempted independently
    assert eb.put_events.await_count == 2
    assert len(recorder.sessions) == 3  # 1 poll + 2 per-row


@pytest.mark.asyncio
async def test_run_publisher_loop_empty_batch_sleeps_and_loops():
    """When no rows are returned, the loop sleeps and goes back around — no per-row sessions opened."""
    recorder = _SessionRecorder()
    recorder.next_side_effects.append([
        _fake_result(all_rows=[]),
    ])

    eb = MagicMock()
    eb.put_events = AsyncMock()

    import workers.outbox_publisher as op_mod
    original_sleep = op_mod.asyncio.sleep

    async def _one_shot_sleep(_seconds):
        raise StopAsyncIteration

    op_mod.asyncio.sleep = _one_shot_sleep
    try:
        with pytest.raises(StopAsyncIteration):
            await run_publisher_loop(
                session_factory=recorder.factory,
                eventbridge_client=eb,
                interval_seconds=0.0,
                batch_size=10,
            )
    finally:
        op_mod.asyncio.sleep = original_sleep

    # Only the poll session opened; no put_events.
    assert len(recorder.sessions) == 1
    eb.put_events.assert_not_called()


# ---------------------------------------------------------------------------
# SQL pinning — guard against accidental SQL drift
# ---------------------------------------------------------------------------


def test_select_unpublished_filters_by_null_published_at():
    sql = str(SELECT_UNPUBLISHED_SQL)
    assert "published_at IS NULL" in sql
    assert "ORDER BY created_at ASC" in sql
    assert ":limit" in sql


def test_mark_published_increments_attempts_and_clears_error():
    sql = str(MARK_PUBLISHED_SQL)
    assert "published_at = NOW()" in sql
    assert "publish_attempts = publish_attempts + 1" in sql
    assert "last_publish_error = NULL" in sql


def test_mark_failed_increments_attempts_and_records_error():
    sql = str(MARK_FAILED_SQL)
    assert "publish_attempts = publish_attempts + 1" in sql
    assert "last_publish_error = :error" in sql
    # MARK_FAILED must NOT set published_at — failed rows stay unpublished
    # for the next poll cycle's retry attempt.
    assert "published_at" not in sql or "published_at = NOW()" not in sql
