"""Outbox publisher tests — emits unpublished account_provisioning_outbox rows to EventBridge.

Phase 1.5 integration-test infrastructure does not yet include a conftest.py
with a real test_session fixture. Following the Phase 1 + T1.5.6 + T1.5.8 pattern,
we exercise publish_one / run_publisher_loop / _build_event with mock-driven
sessions and an AsyncMock EventBridge wrapper and assert the contract:

- _build_event emits the correct Source/DetailType/Detail/EventBusName shape
- publish_one on FailedEntryCount=0 marks the row published in its OWN session
- publish_one on FailedEntryCount>0 records the error in its OWN session
  (committed independently of the raising transaction) and raises RuntimeError
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
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from workers.outbox_publisher import (
    MARK_FAILED_SQL,
    MARK_PUBLISHED_SQL,
    SELECT_FOR_UPDATE_SQL,
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


_MISSING = object()


def _fake_result(one_value=None, one_or_none_value=_MISSING, all_rows=None):
    result = MagicMock()
    if one_value is not None:
        result.one = MagicMock(return_value=one_value)
    # Default: .one_or_none() mirrors the one_value sentinel so tests that
    # only pass one_value=... continue to work without code changes.
    if one_or_none_value is _MISSING:
        result.one_or_none = MagicMock(return_value=one_value)
    else:
        result.one_or_none = MagicMock(return_value=one_or_none_value)
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
        # Codex Round 2 P2 #5: publisher reads EVENTBRIDGE_BUS_NAME, matching
        # the repo-wide convention (main.py:62, .env.example:55).
        monkeypatch.delenv("EVENTBRIDGE_BUS_NAME", raising=False)
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
        monkeypatch.setenv("EVENTBRIDGE_BUS_NAME", "eq-events-dev")
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


def _make_recording_session_factory(execute_results_per_session):
    """Build a callable session_factory returning a fresh AsyncSession-style mock.

    Each call to the returned factory yields a NEW MagicMock session with its
    own execute AsyncMock. The factory records every session it produced on
    `factory.sessions` so tests can assert how many sessions were opened —
    the regression check for the rollback bug (MARK_FAILED must commit on a
    different session than the SELECT, so we expect TWO factory invocations
    for a failed publish: one for SELECT, one for MARK_FAILED).

    execute_results_per_session: list[list]. Outer list = one entry per
    expected session_factory() call. Inner list = side_effect values for
    that session's .execute calls in order.
    """
    sessions: list = []
    pending = list(execute_results_per_session)

    @asynccontextmanager
    async def _scoped():
        session = MagicMock()
        side_effects = pending.pop(0) if pending else []
        session.execute = AsyncMock(side_effect=side_effects)
        begin_cm = MagicMock()
        begin_cm.__aenter__ = AsyncMock(return_value=None)
        begin_cm.__aexit__ = AsyncMock(return_value=None)
        session.begin = MagicMock(return_value=begin_cm)
        sessions.append(session)
        yield session

    def factory():
        return _scoped()

    factory.sessions = sessions  # type: ignore[attr-defined]
    return factory


@pytest.mark.asyncio
async def test_publish_one_success_marks_published():
    """Codex Round 4 P1 #1: success path uses a SINGLE lock-holding session.

    The session opens with SELECT_FOR_UPDATE_SQL (which takes a row-level
    advisory lock via FOR UPDATE SKIP LOCKED), puts the event, and then
    MARK_PUBLISHED commits IN THE SAME SESSION so the row lock is held for
    the entire publish lifetime. Sibling publishers polling concurrently
    skip this row (SKIP LOCKED) and pick up something else.

    Pre-Round-4 the success path opened TWO sessions (read + publish) and
    held no row lock — during a deploy window two publisher containers
    could read the same row and both call put_events before either committed
    MARK_PUBLISHED, producing duplicate downstream events.
    """
    outbox_id = str(uuid.uuid4())
    row = _row(
        id=outbox_id,
        tenant_id=str(uuid.uuid4()),
        queue_id=str(uuid.uuid4()),
        event_type="account_created",
        account_id=str(uuid.uuid4()),
        payload_json={"contact_ids": []},
        published_at=None,
    )

    factory = _make_recording_session_factory([
        # ONE session: SELECT_FOR_UPDATE_SQL → put_events → MARK_PUBLISHED.
        [_fake_result(one_or_none_value=row), _fake_result()],
    ])

    eb = MagicMock()
    eb.put_events = AsyncMock(return_value={"FailedEntryCount": 0, "Entries": [{"EventId": "abc"}]})

    await publish_one(session_factory=factory, eventbridge_client=eb, outbox_row_id=outbox_id)

    # ONE session total (was 2 pre-Round-4). The lock spans the put_events
    # call so concurrent publishers cannot read this row.
    assert len(factory.sessions) == 1  # type: ignore[attr-defined]
    lock_session = factory.sessions[0]  # type: ignore[attr-defined]

    stmts = [call.args[0] for call in lock_session.execute.await_args_list]
    assert stmts == [SELECT_FOR_UPDATE_SQL, MARK_PUBLISHED_SQL]

    # MARK_PUBLISHED bound to the same outbox_row_id.
    assert lock_session.execute.await_args_list[1].args[1] == {"id": outbox_id}

    # MARK_FAILED never executed.
    assert MARK_FAILED_SQL not in stmts

    eb.put_events.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_one_failure_marks_failed_and_raises():
    """FailedEntryCount>0 → MARK_FAILED_SQL commits in its OWN fresh session, then raises.

    Regression check for the rollback bug: the MARK_FAILED write must commit
    on a DIFFERENT session than the one that raised. We assert that the
    session_factory was invoked TWICE (lock session + fail session) — if the
    bug returns and the MARK_FAILED is written on the same session that the
    raise rolls back, factory.sessions will have length 1 (or MARK_FAILED
    will never appear in execute history).

    Codex Round 4 P1 #1: lock session SELECT now uses SELECT_FOR_UPDATE_SQL
    (FOR UPDATE SKIP LOCKED). The failure path opens a SECOND fresh session
    for MARK_FAILED so the failure-state UPDATE commits independently of
    the rollback that the raise unwinds.
    """
    outbox_id = str(uuid.uuid4())
    row = _row(
        id=outbox_id,
        tenant_id=str(uuid.uuid4()),
        queue_id=str(uuid.uuid4()),
        event_type="account_created",
        account_id=str(uuid.uuid4()),
        payload_json={},
        published_at=None,
    )

    factory = _make_recording_session_factory([
        [_fake_result(one_or_none_value=row)],  # session 1: SELECT_FOR_UPDATE_SQL
        [_fake_result()],                        # session 2: MARK_FAILED_SQL
    ])

    eb = MagicMock()
    eb.put_events = AsyncMock(return_value={
        "FailedEntryCount": 1,
        "Entries": [{"ErrorCode": "ThrottlingException", "ErrorMessage": "rate exceeded"}],
    })

    with pytest.raises(RuntimeError, match="EventBridge publish failed"):
        await publish_one(session_factory=factory, eventbridge_client=eb, outbox_row_id=outbox_id)

    # CRITICAL regression assertion: the MARK_FAILED must have happened on a
    # SECOND session, distinct from the lock session that raised. If
    # publish_one ever writes MARK_FAILED back to the lock session (or to a
    # caller-supplied session inside a transaction that the raise rolls
    # back), factory.sessions will be length 1 and this fails.
    assert len(factory.sessions) == 2  # type: ignore[attr-defined]
    lock_session, fail_session = factory.sessions  # type: ignore[attr-defined]
    assert lock_session is not fail_session

    lock_stmts = [call.args[0] for call in lock_session.execute.await_args_list]
    assert lock_stmts == [SELECT_FOR_UPDATE_SQL]

    fail_stmts = [call.args[0] for call in fail_session.execute.await_args_list]
    assert fail_stmts == [MARK_FAILED_SQL]

    mark_failed_params = fail_session.execute.await_args_list[0].args[1]
    assert mark_failed_params["id"] == outbox_id
    assert "ThrottlingException" in mark_failed_params["error"]

    # MARK_PUBLISHED never executed across any session.
    all_stmts = lock_stmts + fail_stmts
    assert MARK_PUBLISHED_SQL not in all_stmts


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
        published_at=None,
    )

    long_msg = "x" * 5000
    factory = _make_recording_session_factory([
        [_fake_result(one_or_none_value=row)],  # session 1: SELECT_FOR_UPDATE_SQL
        [_fake_result()],                        # session 2: MARK_FAILED_SQL
    ])
    eb = MagicMock()
    eb.put_events = AsyncMock(return_value={
        "FailedEntryCount": 1,
        "Entries": [{"ErrorCode": "InternalFailure", "ErrorMessage": long_msg}],
    })

    with pytest.raises(RuntimeError):
        await publish_one(session_factory=factory, eventbridge_client=eb, outbox_row_id=outbox_id)

    fail_session = factory.sessions[1]  # type: ignore[attr-defined]
    mark_failed_params = fail_session.execute.await_args_list[0].args[1]
    assert len(mark_failed_params["error"]) <= 1000


@pytest.mark.asyncio
async def test_publish_one_marks_failed_when_put_events_raises():
    """Codex P2 #5: put_events raises (network, auth, throttle exception) →
    MARK_FAILED commits in its OWN fresh session, then the exception re-raises.

    Without this fix, an exception from put_events leaves the row unchanged
    (publish_attempts not incremented, last_publish_error empty) and the
    publisher retries the same row forever with zero visible state change.
    """
    outbox_id = str(uuid.uuid4())
    row = _row(
        id=outbox_id,
        tenant_id=str(uuid.uuid4()),
        queue_id=str(uuid.uuid4()),
        event_type="account_created",
        account_id=str(uuid.uuid4()),
        payload_json={},
        published_at=None,
    )

    factory = _make_recording_session_factory([
        [_fake_result(one_or_none_value=row)],   # session 1: SELECT_FOR_UPDATE
        [_fake_result()],                         # session 2: MARK_FAILED (exception path)
    ])

    class _BotoConnectionError(Exception):
        pass

    eb = MagicMock()
    eb.put_events = AsyncMock(side_effect=_BotoConnectionError("network down"))

    with pytest.raises(_BotoConnectionError):
        await publish_one(
            session_factory=factory,
            eventbridge_client=eb,
            outbox_row_id=outbox_id,
        )

    # Two sessions: lock + fail. The fail session committed MARK_FAILED
    # BEFORE the raise propagated. If the fix isn't applied, factory.sessions
    # has length 1 (no fail session opened) and this assert fails.
    assert len(factory.sessions) == 2  # type: ignore[attr-defined]
    lock_session, fail_session = factory.sessions  # type: ignore[attr-defined]
    assert lock_session is not fail_session

    fail_stmts = [c.args[0] for c in fail_session.execute.await_args_list]
    assert fail_stmts == [MARK_FAILED_SQL]

    mark_failed_params = fail_session.execute.await_args_list[0].args[1]
    assert mark_failed_params["id"] == outbox_id
    assert "network down" in mark_failed_params["error"]
    assert "_BotoConnectionError" in mark_failed_params["error"]
    # Truncation invariant still holds.
    assert len(mark_failed_params["error"]) <= 1000

    # MARK_PUBLISHED never executed.
    lock_stmts = [c.args[0] for c in lock_session.execute.await_args_list]
    assert MARK_PUBLISHED_SQL not in (lock_stmts + fail_stmts)


@pytest.mark.asyncio
async def test_publish_one_marks_failed_with_truncated_long_exception_message():
    """The exception-path MARK_FAILED honors the same <=1000 truncation rule
    as the FailedEntryCount-path."""
    outbox_id = str(uuid.uuid4())
    row = _row(
        id=outbox_id,
        tenant_id=str(uuid.uuid4()),
        queue_id=str(uuid.uuid4()),
        event_type="account_created",
        account_id=str(uuid.uuid4()),
        payload_json={},
        published_at=None,
    )

    long_msg = "y" * 5000
    factory = _make_recording_session_factory([
        [_fake_result(one_or_none_value=row)],
        [_fake_result()],
    ])

    eb = MagicMock()
    eb.put_events = AsyncMock(side_effect=RuntimeError(long_msg))

    with pytest.raises(RuntimeError):
        await publish_one(
            session_factory=factory,
            eventbridge_client=eb,
            outbox_row_id=outbox_id,
        )

    fail_session = factory.sessions[1]  # type: ignore[attr-defined]
    mark_failed_params = fail_session.execute.await_args_list[0].args[1]
    assert len(mark_failed_params["error"]) <= 1000


@pytest.mark.asyncio
async def test_publish_one_failure_uses_distinct_session_for_mark_failed():
    """Regression test for the rollback bug (commit d218cd4 → fix).

    If MARK_FAILED is written on the same session that subsequently raises
    inside a caller-managed `async with session.begin():`, the rollback
    discards the MARK_FAILED write — publish_attempts never increments and
    last_publish_error never persists. Operators see a row that re-tries
    every poll forever with zero state change.

    The contract: publish_one MUST open a SECOND fresh session for the
    MARK_FAILED write so the failure-state UPDATE commits independently of
    whatever transaction the raise unwinds.

    This test asserts that contract directly by counting session_factory()
    invocations and verifying the MARK_FAILED execute lands on a session
    object distinct from the SELECT session.
    """
    outbox_id = str(uuid.uuid4())
    row = _row(
        id=outbox_id,
        tenant_id=str(uuid.uuid4()),
        queue_id=str(uuid.uuid4()),
        event_type="account_created",
        account_id=str(uuid.uuid4()),
        payload_json={},
        published_at=None,
    )

    factory = _make_recording_session_factory([
        [_fake_result(one_or_none_value=row)],   # lock session
        [_fake_result()],                         # fail session — MUST be distinct
    ])

    eb = MagicMock()
    eb.put_events = AsyncMock(return_value={
        "FailedEntryCount": 1,
        "Entries": [{"ErrorCode": "ValidationException", "ErrorMessage": "bad"}],
    })

    with pytest.raises(RuntimeError):
        await publish_one(session_factory=factory, eventbridge_client=eb, outbox_row_id=outbox_id)

    # Multi-session-commit semantics: factory called at least twice for the
    # failure path. This is THE assertion that catches the rollback bug.
    assert len(factory.sessions) >= 2  # type: ignore[attr-defined]

    # And MARK_FAILED appears in execute history on a session that is NOT
    # the lock session.
    lock_session = factory.sessions[0]  # type: ignore[attr-defined]
    lock_stmts = [call.args[0] for call in lock_session.execute.await_args_list]
    assert MARK_FAILED_SQL not in lock_stmts, (
        "MARK_FAILED was written to the lock session — this is the "
        "rollback bug: the caller's transaction will discard the write."
    )

    mark_failed_seen_on_other_session = any(
        MARK_FAILED_SQL in [call.args[0] for call in s.execute.await_args_list]
        for s in factory.sessions[1:]  # type: ignore[attr-defined]
    )
    assert mark_failed_seen_on_other_session


# ---------------------------------------------------------------------------
# Codex Round 4 P1 #1 — sibling-lock + already-published race regression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_one_skips_when_sibling_holds_lock():
    """SELECT_FOR_UPDATE_SQL with FOR UPDATE SKIP LOCKED can return None when
    a sibling publisher already holds the row lock. In that case publish_one
    must noop: NO put_events call, NO MARK_PUBLISHED, NO MARK_FAILED, NO raise.

    The sibling publisher will publish the row and commit MARK_PUBLISHED on
    its own session. Our publisher will see the row as `published_at IS NOT
    NULL` (or no longer in the unpublished SELECT) on the next poll and
    correctly skip it.

    Without this branch a multi-replica deploy would crash on .one() when
    the SKIP-LOCKED SELECT returns zero rows, or worse, would re-execute
    put_events on a stale read of a row that is mid-publish in the sibling
    container — producing duplicate downstream events.
    """
    outbox_id = str(uuid.uuid4())

    factory = _make_recording_session_factory([
        # Lock session: SELECT_FOR_UPDATE returns None (sibling has the lock).
        [_fake_result(one_or_none_value=None)],
    ])

    eb = MagicMock()
    eb.put_events = AsyncMock()

    # Must NOT raise.
    await publish_one(
        session_factory=factory,
        eventbridge_client=eb,
        outbox_row_id=outbox_id,
    )

    # Exactly one session opened — the SELECT_FOR_UPDATE session — and only
    # the SELECT_FOR_UPDATE_SQL ran.
    assert len(factory.sessions) == 1  # type: ignore[attr-defined]
    lock_session = factory.sessions[0]  # type: ignore[attr-defined]
    stmts = [call.args[0] for call in lock_session.execute.await_args_list]
    assert stmts == [SELECT_FOR_UPDATE_SQL]

    # put_events must NOT have been called.
    eb.put_events.assert_not_called()


@pytest.mark.asyncio
async def test_publish_one_skips_when_row_already_published():
    """If the SELECT_FOR_UPDATE row's published_at is non-NULL, another
    publisher just published the row between our poll-SELECT and our
    per-row lock acquisition. publish_one must noop without re-publishing.

    Without this guard a deploy-window race could re-publish the same
    outbox row twice: replica A reads the unpublished row in its poll
    batch, then replica B publishes + commits MARK_PUBLISHED, then replica
    A acquires the FOR UPDATE lock on the now-published row and proceeds
    to put_events again.
    """
    outbox_id = str(uuid.uuid4())
    already_published_row = _row(
        id=outbox_id,
        tenant_id=str(uuid.uuid4()),
        queue_id=str(uuid.uuid4()),
        event_type="account_created",
        account_id=str(uuid.uuid4()),
        payload_json={},
        # CRITICAL: published_at is non-NULL → sibling already published.
        published_at="2026-05-14T12:00:00Z",
    )

    factory = _make_recording_session_factory([
        [_fake_result(one_or_none_value=already_published_row)],
    ])

    eb = MagicMock()
    eb.put_events = AsyncMock()

    # Must NOT raise.
    await publish_one(
        session_factory=factory,
        eventbridge_client=eb,
        outbox_row_id=outbox_id,
    )

    # One session opened (lock session); only SELECT_FOR_UPDATE_SQL ran.
    assert len(factory.sessions) == 1  # type: ignore[attr-defined]
    lock_session = factory.sessions[0]  # type: ignore[attr-defined]
    stmts = [call.args[0] for call in lock_session.execute.await_args_list]
    assert stmts == [SELECT_FOR_UPDATE_SQL]

    # put_events must NOT have been called.
    eb.put_events.assert_not_called()


def test_select_for_update_uses_skip_locked():
    """SELECT_FOR_UPDATE_SQL must use FOR UPDATE SKIP LOCKED so concurrent
    publishers (deploy window: old + new container both live) do NOT pick
    up the same row.

    Per docs/superpowers/specs/2026-05-14-dispatch-patterns-research.md
    Section 2.4: "FOR UPDATE SKIP LOCKED on the outbox query — allows safe
    parallel publishers when we go multi-replica."
    """
    sql = str(SELECT_FOR_UPDATE_SQL)
    assert "FOR UPDATE SKIP LOCKED" in sql
    # The single-row lock SELECT must include published_at so the handler
    # can short-circuit when a sibling already published mid-flight.
    assert "published_at" in sql


def test_select_unpublished_uses_skip_locked():
    """SELECT_UNPUBLISHED_SQL (the poll) must also use FOR UPDATE SKIP LOCKED.

    Without this, two publisher replicas polling concurrently would each
    return the same batch of unpublished rows. Per-row FOR UPDATE SKIP
    LOCKED still serializes the publish itself, but having the poll skip
    locked rows means each replica's batch is disjoint to begin with —
    less wasted work and clearer operational behavior.
    """
    sql = str(SELECT_UNPUBLISHED_SQL)
    assert "FOR UPDATE SKIP LOCKED" in sql


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
    """Per-row transactional isolation: each row gets its own publish_one call.

    Codex Round 4 P1 #1: publish_one now uses ONE lock-holding session per
    row for the success path (SELECT_FOR_UPDATE_SQL + put_events +
    MARK_PUBLISHED all on the same session) so the row lock spans the
    entire publish lifetime. The loop opens the poll-SELECT session;
    publish_one opens its own lock session per row.

    Session count for two successful rows:
      1 poll + 1 lock for row A + 1 lock for row B = 3
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
    # 2) Row A — lock session: SELECT_FOR_UPDATE + MARK_PUBLISHED.
    recorder.next_side_effects.append([
        _fake_result(one_or_none_value=_row(
            id=row_a_id,
            tenant_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
            event_type="account_created",
            account_id=str(uuid.uuid4()),
            payload_json={},
            published_at=None,
        )),
        _fake_result(),
    ])
    # 3) Row B — lock session: SELECT_FOR_UPDATE + MARK_PUBLISHED.
    recorder.next_side_effects.append([
        _fake_result(one_or_none_value=_row(
            id=row_b_id,
            tenant_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
            event_type="account_created",
            account_id=str(uuid.uuid4()),
            payload_json={},
            published_at=None,
        )),
        _fake_result(),
    ])

    eb = MagicMock()
    eb.put_events = AsyncMock(return_value={"FailedEntryCount": 0, "Entries": [{"EventId": "x"}]})

    # Run one iteration: use a tiny sleep + cancel trick — patch asyncio.sleep
    # to raise after the first call.
    import workers.outbox_publisher as op_mod
    original_sleep = op_mod.asyncio.sleep

    sleep_calls = {"count": 0}

    async def _one_shot_sleep(_):
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

    # 3 sessions total: 1 poll + 1 lock per success row.
    assert len(recorder.sessions) == 3
    assert eb.put_events.await_count == 2


@pytest.mark.asyncio
async def test_run_publisher_loop_one_row_failure_does_not_skip_others():
    """If row A's publish raises, the loop logs + continues to row B.

    Per-row try/except inside the loop ensures isolation. publish_one's
    fresh-session pattern means row A's MARK_FAILED commit is independent
    of row B's MARK_PUBLISHED commit.

    Codex Round 4 P1 #1 session count (1 lock session for success, 2 for
    failure — lock + fail):
      1 poll + (1 lock + 1 fail) for row A + (1 lock) for row B = 4.
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
    # 2) Row A — lock session: SELECT_FOR_UPDATE.
    recorder.next_side_effects.append([
        _fake_result(one_or_none_value=_row(
            id=row_a_id,
            tenant_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
            event_type="account_created",
            account_id=str(uuid.uuid4()),
            payload_json={},
            published_at=None,
        )),
    ])
    # 3) Row A — fail session: MARK_FAILED.
    recorder.next_side_effects.append([_fake_result()])
    # 4) Row B — lock session: SELECT_FOR_UPDATE + MARK_PUBLISHED.
    recorder.next_side_effects.append([
        _fake_result(one_or_none_value=_row(
            id=row_b_id,
            tenant_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
            event_type="account_created",
            account_id=str(uuid.uuid4()),
            payload_json={},
            published_at=None,
        )),
        _fake_result(),
    ])

    eb = MagicMock()
    # Row A: fail. Row B: succeed.
    eb.put_events = AsyncMock(side_effect=[
        {"FailedEntryCount": 1, "Entries": [{"ErrorCode": "Throttled", "ErrorMessage": "no"}]},
        {"FailedEntryCount": 0, "Entries": [{"EventId": "x"}]},
    ])

    import workers.outbox_publisher as op_mod
    original_sleep = op_mod.asyncio.sleep

    async def _one_shot_sleep(_):
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

    # Both rows attempted independently.
    assert eb.put_events.await_count == 2
    # 1 poll + 2 (lock+fail) for failing row A + 1 lock for success row B = 4.
    assert len(recorder.sessions) == 4

    # Row A's MARK_FAILED landed on a distinct session from its SELECT
    # (this is the rollback-bug regression check at the loop level).
    row_a_lock, row_a_fail = recorder.sessions[1], recorder.sessions[2]
    assert row_a_lock is not row_a_fail
    row_a_lock_stmts = [c.args[0] for c in row_a_lock.execute.await_args_list]
    row_a_fail_stmts = [c.args[0] for c in row_a_fail.execute.await_args_list]
    assert row_a_lock_stmts == [SELECT_FOR_UPDATE_SQL]
    assert row_a_fail_stmts == [MARK_FAILED_SQL]


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

    async def _one_shot_sleep(_):
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
    # Codex Round 3 P2 #3: ORDER BY must favor fresh attempts first to prevent
    # poison rows from starving the queue head. publish_attempts ASC rotates
    # repeatedly-failing rows to the back of the batch so newer events at
    # publish_attempts=0 always get a turn. created_at ASC remains the
    # tiebreaker for rows at the same attempts count.
    assert "ORDER BY publish_attempts ASC, created_at ASC" in sql
    assert ":limit" in sql
    # Codex Round 4 P1 #1: FOR UPDATE SKIP LOCKED ensures concurrent
    # publisher replicas pull disjoint batches.
    assert "FOR UPDATE SKIP LOCKED" in sql


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


# ---------------------------------------------------------------------------
# Codex Round 5 P1 #1 — publisher self-deadlock regression tests
# ---------------------------------------------------------------------------
#
# Bug pre-Round-5: the failure-path opened the fresh fail_session INSIDE
# the lock_session's `async with begin():` block. In production this would
# block on a real Postgres lock — the fail_session UPDATE waits for the
# lock_session's FOR UPDATE row lock to release, but the lock_session
# can't release because its `async with begin():` block is waiting for
# the inner code (i.e. the fail_session) to return. Python control-flow
# deadlock. Postgres doesn't detect it as a true deadlock because
# lock_session isn't waiting on a Postgres lock — it's waiting on the
# async call stack to unwind.
#
# Fix: capture error inside the lock_session block, raise to unwind it
# (rolls back + releases the row lock), THEN open fail_session AFTER
# lock_session has exited.
#
# These regression tests record the lifecycle ordering of __aenter__ /
# __aexit__ on each session_factory() invocation and assert that
# lock_session __aexit__ happens BEFORE fail_session __aenter__.


def _make_lifecycle_tracking_session_factory(
    execute_results_per_session,
    eventbridge_response=None,
    eventbridge_exception=None,
):
    """session_factory variant that records ('enter', label) / ('exit', label)
    events on a shared list so tests can assert lifecycle ordering.

    Returns (factory, events) where events is the shared list.
    """
    from contextlib import asynccontextmanager

    events: list = []
    sessions: list = []
    pending = list(execute_results_per_session)
    call_count = {"n": 0}

    @asynccontextmanager
    async def _scoped():
        call_count["n"] += 1
        label = f"session{call_count['n']}"
        events.append(("enter", label))

        session = MagicMock()
        side_effects = pending.pop(0) if pending else []
        session.execute = AsyncMock(side_effect=side_effects)
        begin_cm = MagicMock()
        begin_cm.__aenter__ = AsyncMock(return_value=None)
        begin_cm.__aexit__ = AsyncMock(return_value=None)
        session.begin = MagicMock(return_value=begin_cm)
        sessions.append(session)
        try:
            yield session
        finally:
            events.append(("exit", label))

    def factory():
        return _scoped()

    factory.sessions = sessions  # type: ignore[attr-defined]
    factory.events = events  # type: ignore[attr-defined]
    return factory


@pytest.mark.asyncio
async def test_publish_one_releases_lock_before_mark_failed_on_exception():
    """REGRESSION (Codex Round 5 P1 #1): prevent self-deadlock when put_events raises.

    The fix: lock_session MUST rollback and release the FOR UPDATE lock
    BEFORE the fail_session opens to write MARK_FAILED. Otherwise the
    fail_session blocks on our own row lock and the call hangs in
    production (the test would deadlock against a real DB).

    Verifies: lock_session __aexit__ happens BEFORE fail_session __aenter__.
    """
    outbox_id = str(uuid.uuid4())
    row = _row(
        id=outbox_id,
        tenant_id=str(uuid.uuid4()),
        queue_id=str(uuid.uuid4()),
        event_type="account_created",
        account_id=str(uuid.uuid4()),
        payload_json={},
        published_at=None,
    )

    factory = _make_lifecycle_tracking_session_factory([
        [_fake_result(one_or_none_value=row)],  # lock_session
        [_fake_result()],                        # fail_session
    ])

    eb = MagicMock()
    eb.put_events = AsyncMock(side_effect=RuntimeError("network down"))

    with pytest.raises(RuntimeError, match="network down"):
        await publish_one(
            session_factory=factory,
            eventbridge_client=eb,
            outbox_row_id=outbox_id,
        )

    events = factory.events  # type: ignore[attr-defined]

    # Both sessions opened.
    assert ("enter", "session1") in events
    assert ("exit", "session1") in events
    assert ("enter", "session2") in events
    assert ("exit", "session2") in events

    exit_session1_idx = events.index(("exit", "session1"))
    enter_session2_idx = events.index(("enter", "session2"))

    assert exit_session1_idx < enter_session2_idx, (
        "Deadlock regression: fail_session opened while lock_session still "
        f"held its FOR UPDATE lock. Events: {events}"
    )


@pytest.mark.asyncio
async def test_publish_one_releases_lock_before_mark_failed_on_failed_entries():
    """REGRESSION (Codex Round 5 P1 #1): same deadlock guard for the
    FailedEntryCount > 0 path.

    The original Round 4 fix opened fail_session inside the lock-session
    begin() block for BOTH the exception path and the FailedEntryCount > 0
    path. Both deadlocked. This test pins the FailedEntryCount path.
    """
    outbox_id = str(uuid.uuid4())
    row = _row(
        id=outbox_id,
        tenant_id=str(uuid.uuid4()),
        queue_id=str(uuid.uuid4()),
        event_type="account_created",
        account_id=str(uuid.uuid4()),
        payload_json={},
        published_at=None,
    )

    factory = _make_lifecycle_tracking_session_factory([
        [_fake_result(one_or_none_value=row)],  # lock_session
        [_fake_result()],                        # fail_session
    ])

    eb = MagicMock()
    eb.put_events = AsyncMock(return_value={
        "FailedEntryCount": 1,
        "Entries": [{"ErrorCode": "Throttled", "ErrorMessage": "nope"}],
    })

    with pytest.raises(RuntimeError, match="EventBridge publish failed"):
        await publish_one(
            session_factory=factory,
            eventbridge_client=eb,
            outbox_row_id=outbox_id,
        )

    events = factory.events  # type: ignore[attr-defined]

    exit_session1_idx = events.index(("exit", "session1"))
    enter_session2_idx = events.index(("enter", "session2"))

    assert exit_session1_idx < enter_session2_idx, (
        "Deadlock regression (FailedEntryCount path): fail_session opened "
        "while lock_session still held its FOR UPDATE lock. "
        f"Events: {events}"
    )


# ---------------------------------------------------------------------------
# Codex Round 6 P2 #2 — MARK_FAILED must no-op on already-published rows
# ---------------------------------------------------------------------------
#
# Pre-fix: MARK_FAILED unconditionally wrote publish_attempts++ and
# last_publish_error. During a deploy overlap, publisher A could fail to
# publish row X, release its FOR UPDATE lock, and then publisher B could
# acquire the lock, publish X successfully (setting published_at), and
# release its lock — BEFORE publisher A's separate fail_session ran
# MARK_FAILED. Publisher A's MARK_FAILED then wrote last_publish_error
# on top of the now-published row, producing a row with BOTH
# published_at IS NOT NULL AND last_publish_error IS NOT NULL —
# contradictory state for downstream observability.
#
# Post-fix: MARK_FAILED carries `AND published_at IS NULL`. If a sibling
# publishes the row between publisher A's lock release and publisher A's
# fail_session open, MARK_FAILED matches 0 rows; the fail_session commits
# cleanly; the outbox row reflects the sibling's success only.


def test_mark_failed_sql_excludes_already_published():
    """Round 6 P2 #2: MARK_FAILED_SQL must filter `published_at IS NULL`
    so a fail-write that lands after a sibling publisher's success-commit
    is a no-op rather than producing contradictory state
    (published_at IS NOT NULL AND last_publish_error IS NOT NULL).
    """
    sql_text = str(MARK_FAILED_SQL)
    assert "published_at IS NULL" in sql_text
    # Existing Round 1 invariants stay.
    assert "publish_attempts = publish_attempts + 1" in sql_text
    assert "last_publish_error = :error" in sql_text
