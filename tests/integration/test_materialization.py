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
    UPSERT_PLACEHOLDER_SUMMARY_SQL,
    UPSERT_RAW_INTERACTION_SQL,
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


_SENTINEL = object()  # distinguishes "not set" from explicit None for one_or_none_value


def _fake_execute_result(
    returning_value=None,
    all_rows=None,
    returning_row=None,
    one_or_none_value=_SENTINEL,
):
    """Build a mock SQLAlchemy result object.

    - returning_value:    configures .scalar_one() (single-column RETURNING)
    - returning_row:      configures .one() (multi-column RETURNING — contacts)
    - all_rows:           configures .all() (signals SELECT)
    - one_or_none_value:  configures .one_or_none() (existing-summary lookup);
                          pass explicit None to mean "no row exists"
    """
    result = MagicMock()
    if returning_value is not None:
        result.scalar_one = MagicMock(return_value=returning_value)
    if returning_row is not None:
        result.one = MagicMock(return_value=returning_row)
    if all_rows is not None:
        result.all = MagicMock(return_value=all_rows)
    if one_or_none_value is not _SENTINEL:
        result.one_or_none = MagicMock(return_value=one_or_none_value)
    return result


def _fake_contact_row(contact_id, account_id):
    """Build a mock SQLAlchemy row with .id and .account_id (matches INSERT_CONTACT_SQL RETURNING)."""
    row = MagicMock()
    row.id = contact_id
    row.account_id = account_id
    return row


@pytest.mark.asyncio
async def test_materialize_emits_statement_sequence_in_order():
    """SELECT signals → contact + (raw/summary placeholders on first interaction ref + link) → ...

    With the placeholder-summary pattern: the FIRST signal with a given
    raw_interaction_id triggers UPSERT raw_interactions + INSERT placeholder
    summary + INSERT link. Subsequent signals with the SAME raw_interaction_id
    reuse the cached summary_id and only INSERT link.
    """
    queue_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    interaction_id = uuid.uuid4()  # shared between alice and carol

    signals = [
        _fake_signal("alice@acme.com", "Alice Smith", interaction_id=interaction_id),
        _fake_signal("bob@acme.com", "Bob"),
        _fake_signal("carol@acme.com", None, interaction_id=interaction_id),
    ]

    new_contact_ids = [str(uuid.uuid4()) for _ in signals]
    new_outbox_id = str(uuid.uuid4())

    # Expected sequence (10 calls). For each NEW raw_interaction_id we do
    # UPSERT raw + UPSERT placeholder summary (returns summary_id) + INSERT
    # link. The shared interaction caches summary_id so carol reuses it.
    # 1. SELECT signals
    # 2. INSERT contact alice
    # 3. UPSERT raw_interactions
    # 4. UPSERT placeholder summary (returning summary_id)
    # 5. INSERT link (alice)
    # 6. INSERT contact bob (no interaction)
    # 7. INSERT contact carol
    # 8. INSERT link (carol ↔ cached summary)
    # 9. UPDATE queue
    # 10. INSERT outbox
    placeholder_summary_id = str(uuid.uuid4())
    execute_results = [
        _fake_execute_result(all_rows=signals),
        _fake_execute_result(returning_row=_fake_contact_row(new_contact_ids[0], account_id)),
        _fake_execute_result(),  # UPSERT raw_interactions
        _fake_execute_result(returning_value=placeholder_summary_id),  # UPSERT summary
        _fake_execute_result(),  # INSERT link (alice)
        _fake_execute_result(returning_row=_fake_contact_row(new_contact_ids[1], account_id)),
        _fake_execute_result(returning_row=_fake_contact_row(new_contact_ids[2], account_id)),
        _fake_execute_result(),  # INSERT link (carol, cached summary)
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

    assert session.execute.await_count == 10

    statements = [call.args[0] for call in session.execute.await_args_list]
    assert statements[0] is SELECT_SIGNALS_SQL
    assert statements[1] is INSERT_CONTACT_SQL                 # alice
    assert statements[2] is UPSERT_RAW_INTERACTION_SQL         # first ref to interaction
    assert statements[3] is UPSERT_PLACEHOLDER_SUMMARY_SQL     # race-safe upsert
    assert statements[4] is INSERT_LINK_SQL                    # alice link
    assert statements[5] is INSERT_CONTACT_SQL                 # bob
    assert statements[6] is INSERT_CONTACT_SQL                 # carol
    assert statements[7] is INSERT_LINK_SQL                    # carol link (cached summary)
    assert statements[8] is UPDATE_QUEUE_SQL
    assert statements[9] is INSERT_OUTBOX_SQL


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
        _fake_execute_result(returning_row=_fake_contact_row(new_contact_id, account_id)),
        _fake_execute_result(),  # UPSERT raw_interactions
        _fake_execute_result(returning_value=str(uuid.uuid4())),  # UPSERT placeholder summary
        _fake_execute_result(),  # INSERT link
        _fake_execute_result(),  # UPDATE queue
        _fake_execute_result(returning_value=str(uuid.uuid4())),  # INSERT outbox
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
    account_id = str(uuid.uuid4())
    signals = [_fake_signal("alice@acme.com")]
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _fake_execute_result(all_rows=signals),
        _fake_execute_result(returning_row=_fake_contact_row(str(uuid.uuid4()), account_id)),
        _fake_execute_result(),  # UPDATE queue
        _fake_execute_result(returning_value=str(uuid.uuid4())),  # INSERT outbox
    ])

    await materialize_account_approval(
        session=session,
        tenant_id=str(uuid.uuid4()),
        queue_id=str(uuid.uuid4()),
        account_id=account_id,
        event_type="account_created",
    )

    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_materialize_raises_on_empty_signals():
    """Materializing a queue entry with no active signals is a contract violation.

    The outbox row would carry contact_ids: [] which is architecturally meaningless
    (signals are what become contacts). Fail loud so the worker logs + retries.
    """
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _fake_execute_result(all_rows=[]),  # no signals
    ])

    with pytest.raises(ValueError, match="no active signals"):
        await materialize_account_approval(
            session=session,
            tenant_id=str(uuid.uuid4()),
            queue_id="queue-empty-001",
            account_id=str(uuid.uuid4()),
            event_type="account_created",
        )

    # Only the SELECT should have run; no UPDATE/INSERT past the guard
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_materialize_dedupes_contact_ids_in_outbox_payload():
    """Same contact email across multiple signals → single contact_id in payload.

    ON CONFLICT DO UPDATE RETURNING returns the SAME contact_id for repeated
    emails. The payload must dedupe so downstream consumers don't count or loop
    over duplicates.
    """
    queue_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())

    # Alice appears in three signals (different interactions, same email)
    signals = [
        _fake_signal("alice@acme.com", interaction_id=uuid.uuid4()),
        _fake_signal("alice@acme.com", interaction_id=uuid.uuid4()),
        _fake_signal("alice@acme.com"),
    ]
    alice_contact_id = str(uuid.uuid4())  # ON CONFLICT returns same id thrice

    # Two of alice's signals have distinct interaction_ids; the third has none.
    # Each distinct interaction triggers: UPSERT raw + UPSERT placeholder
    # summary + INSERT link. So 12 calls total.
    execute_results = [
        _fake_execute_result(all_rows=signals),
        _fake_execute_result(returning_row=_fake_contact_row(alice_contact_id, account_id)),
        _fake_execute_result(),  # UPSERT raw 1
        _fake_execute_result(returning_value=str(uuid.uuid4())),  # UPSERT summary 1
        _fake_execute_result(),  # INSERT link 1
        _fake_execute_result(returning_row=_fake_contact_row(alice_contact_id, account_id)),
        _fake_execute_result(),  # UPSERT raw 2
        _fake_execute_result(returning_value=str(uuid.uuid4())),  # UPSERT summary 2
        _fake_execute_result(),  # INSERT link 2
        _fake_execute_result(returning_row=_fake_contact_row(alice_contact_id, account_id)),
        _fake_execute_result(),  # UPDATE queue
        _fake_execute_result(returning_value=str(uuid.uuid4())),  # INSERT outbox
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

    outbox_call = session.execute.await_args_list[-1]
    payload = json.loads(outbox_call.args[1]["payload_json"])
    assert payload["contact_ids"] == [alice_contact_id]  # deduplicated


@pytest.mark.asyncio
async def test_materialize_raises_on_cross_account_collision():
    """Existing contact under a different account → fail loud, do not silently misroute.

    The ON CONFLICT branch preserves existing account_id via COALESCE. When the
    contact already belongs to account A and the worker is materializing against
    account B, the RETURNED account_id is A; the function must raise rather than
    write a queue + outbox event pointing at B. Cross-account reassignment is
    Phase 3 scope.
    """
    queue_id = str(uuid.uuid4())
    tenant_id = str(uuid.uuid4())
    new_account_id = str(uuid.uuid4())
    pre_existing_account_id = str(uuid.uuid4())

    signals = [_fake_signal("alice@acme.com")]
    pre_existing_contact_id = str(uuid.uuid4())

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        _fake_execute_result(all_rows=signals),
        # Contact already belongs to pre_existing_account_id; COALESCE preserves it
        _fake_execute_result(returning_row=_fake_contact_row(
            pre_existing_contact_id, pre_existing_account_id,
        )),
    ])

    with pytest.raises(ValueError, match="cross-account|already belongs"):
        await materialize_account_approval(
            session=session,
            tenant_id=tenant_id,
            queue_id=queue_id,
            account_id=new_account_id,
            event_type="account_created",
        )

    # Only SELECT signals + 1 INSERT contact (which detected the collision) should run
    # — no UPDATE queue, no INSERT outbox
    assert session.execute.await_count == 2


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
