"""Atomic materialization transaction tests.

Phase 1.5 integration-test infrastructure does not yet include a conftest.py
with a real test_session fixture. Following the Phase 1 pattern (see
test_per_attendee_branching.py), we exercise the materialization function
with a mock-driven AsyncMock session and assert the SQL emission pattern.

End-to-end verification of the atomic transaction happens via:
- Production E2E (`/tmp/e2e_phase_1_production.py`, extended after Task 1.5.8)
- Task 1.5.18 end-to-end Approve flow test (uses real Neon eq-dev once fixtures land)
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from workers.materialization import (
    INSERT_CONTACT_SQL,
    INSERT_LINK_SQL,
    INSERT_OUTBOX_SQL,
    SELECT_SIGNALS_SQL,
    UPDATE_QUEUE_SQL,
    _split_name,
    materialize_account_approval,
)


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


class TestSplitName:
    def test_none_returns_none_pair(self):
        assert _split_name(None) == (None, None)

    def test_empty_returns_none_pair(self):
        assert _split_name("") == (None, None)
        assert _split_name("   ") == (None, None)

    def test_single_token(self):
        assert _split_name("Alice") == ("Alice", None)

    def test_first_last(self):
        assert _split_name("Alice Smith") == ("Alice", "Smith")

    def test_three_tokens_groups_last_name(self):
        assert _split_name("Alice Mary Smith") == ("Alice", "Mary Smith")


# ---------------------------------------------------------------------------
# Mock-driven materialization tests
# ---------------------------------------------------------------------------


def _fake_signal(email, display_name=None, role=None, interaction_id=None, source_type="transcript"):
    """Build a mock SQLAlchemy row matching pending_account_mapping_signals."""
    row = MagicMock()
    row.id = uuid.uuid4()
    row.contact_email = email
    row.contact_display_name = display_name
    row.contact_role = role
    row.interaction_id = interaction_id
    row.source_type = source_type
    return row


def _fake_execute_result(returning_value=None, all_rows=None):
    """Build a mock SQLAlchemy result object."""
    result = MagicMock()
    if returning_value is not None:
        result.scalar_one = MagicMock(return_value=returning_value)
    if all_rows is not None:
        result.all = MagicMock(return_value=all_rows)
    return result


@pytest.mark.asyncio
async def test_materialize_emits_5_statement_kinds_in_order():
    """SELECT signals → N INSERT contacts → M INSERT links → 1 UPDATE queue → 1 INSERT outbox."""
    queue_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    interaction_id = str(uuid.uuid4())

    signals = [
        _fake_signal("alice@acme.com", "Alice Smith", interaction_id=interaction_id),
        _fake_signal("bob@acme.com", "Bob"),
        _fake_signal("carol@acme.com", None, interaction_id=interaction_id),
    ]

    new_contact_ids = [str(uuid.uuid4()) for _ in signals]
    new_outbox_id = str(uuid.uuid4())

    execute_results = [
        _fake_execute_result(all_rows=signals),  # SELECT signals
        _fake_execute_result(returning_value=new_contact_ids[0]),  # INSERT contact 0
        _fake_execute_result(),  # INSERT link 0
        _fake_execute_result(returning_value=new_contact_ids[1]),  # INSERT contact 1
        _fake_execute_result(returning_value=new_contact_ids[2]),  # INSERT contact 2
        _fake_execute_result(),  # INSERT link 2
        _fake_execute_result(),  # UPDATE queue
        _fake_execute_result(returning_value=new_outbox_id),  # INSERT outbox
    ]
    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute_results)

    await materialize_account_approval(
        session=session,
        tenant_id=tenant_id,
        queue_id=queue_id,
        account_id=account_id,
        event_type="account_created",
    )

    # 1 SELECT + 3 INSERT contacts + 2 INSERT links + 1 UPDATE + 1 INSERT outbox = 8
    assert session.execute.await_count == 8

    statements = [call.args[0] for call in session.execute.await_args_list]
    assert statements[0] is SELECT_SIGNALS_SQL
    assert statements[1] is INSERT_CONTACT_SQL  # alice
    assert statements[2] is INSERT_LINK_SQL     # alice link (has interaction_id)
    assert statements[3] is INSERT_CONTACT_SQL  # bob
    # bob has no interaction_id → no link
    assert statements[4] is INSERT_CONTACT_SQL  # carol
    assert statements[5] is INSERT_LINK_SQL     # carol link
    assert statements[6] is UPDATE_QUEUE_SQL
    assert statements[7] is INSERT_OUTBOX_SQL


@pytest.mark.asyncio
async def test_materialize_passes_correct_params_to_outbox():
    """Outbox payload carries account_id, contact_ids, interaction_ids."""
    queue_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    interaction_id = uuid.uuid4()

    signals = [_fake_signal("alice@acme.com", "Alice Smith", interaction_id=interaction_id)]
    new_contact_id = str(uuid.uuid4())

    execute_results = [
        _fake_execute_result(all_rows=signals),
        _fake_execute_result(returning_value=new_contact_id),
        _fake_execute_result(),  # link
        _fake_execute_result(),  # update
        _fake_execute_result(returning_value=str(uuid.uuid4())),  # outbox
    ]
    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute_results)

    await materialize_account_approval(
        session=session,
        tenant_id=tenant_id,
        queue_id=queue_id,
        account_id=account_id,
        event_type="account_mapped",
    )

    # Outbox call is the last one
    outbox_call = session.execute.await_args_list[-1]
    assert outbox_call.args[0] is INSERT_OUTBOX_SQL
    params = outbox_call.args[1]
    assert params["tenant_id"] == tenant_id
    assert params["queue_id"] == queue_id
    assert params["account_id"] == account_id
    assert params["event_type"] == "account_mapped"

    payload = json.loads(params["payload_json"])
    assert payload["account_id"] == account_id
    assert payload["tenant_id"] == tenant_id
    assert payload["queue_id"] == queue_id
    assert payload["contact_ids"] == [new_contact_id]
    assert payload["interaction_ids"] == [str(interaction_id)]


@pytest.mark.asyncio
async def test_materialize_does_not_commit():
    """Caller manages the transaction; materialize_account_approval must NOT call session.commit()."""
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _fake_execute_result(all_rows=[]),  # no signals
        _fake_execute_result(),  # UPDATE queue
        _fake_execute_result(returning_value=str(uuid.uuid4())),  # INSERT outbox
    ])

    await materialize_account_approval(
        session=session,
        tenant_id=str(uuid.uuid4()),
        queue_id=str(uuid.uuid4()),
        account_id=str(uuid.uuid4()),
        event_type="account_created",
    )

    session.commit.assert_not_called()
    session.rollback.assert_not_called()


# ---------------------------------------------------------------------------
# Real-DB scaffold (skipped — awaiting conftest.py with test_session)
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="awaiting conftest.py with test_session fixture; production E2E covers end-to-end")
@pytest.mark.asyncio
async def test_materialize_creates_contacts_links_outbox_atomically_REAL_DB():
    """End-to-end scaffold matching the plan's prescribed assertions.

    When conftest.py with test_session + seeded_queue_entry_with_signals lands,
    replace this skip with the real assertions:

    - 3 distinct signals → 3 contacts in `contacts` with the same account_id
    - account_provisioning_outbox row exists with event_type='account_created',
      published_at IS NULL (publisher hasn't run)
    - pending_account_mappings.status = 'mapped', mapped_at IS NOT NULL

    Until then, production E2E (extended after Task 1.5.8) provides the
    real-DB safety net.
    """
    pass
