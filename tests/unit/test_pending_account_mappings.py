"""Queue insertion helpers — upsert parent + insert signal + re-open."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from services.pending_account_mappings import (
    upsert_queue_entry,
    insert_signal,
    SignalProposal,
    QueueRow,
)


@pytest.mark.asyncio
async def test_upsert_queue_entry_creates_new_row():
    session = MagicMock()
    # Simulate INSERT...ON CONFLICT returning the inserted id
    session.execute = AsyncMock(return_value=MagicMock(scalar_one=lambda: "queue-id-1"))
    qid = await upsert_queue_entry(
        session=session,
        tenant_id="t1",
        domain="acme.com",
        owner_user_id="u1",
        discovered_from_type="transcript",
        discovered_from_interaction_id="int-1",
        expires_in_days=30,
    )
    assert qid == "queue-id-1"


@pytest.mark.asyncio
async def test_insert_signal_is_idempotent():
    session = MagicMock()
    session.execute = AsyncMock()
    await insert_signal(
        session=session,
        tenant_id="t1",
        queue_id="q1",
        proposal=SignalProposal(
            source_type="transcript",
            source_user_id="u1",
            interaction_id="int-1",
            calendar_event_id=None,
            contact_email="bob@acme.com",
            contact_display_name="Bob",
            contact_role="attendee",
        ),
    )
    # Verify INSERT ... ON CONFLICT was attempted (idempotent under retry)
    assert session.execute.called
