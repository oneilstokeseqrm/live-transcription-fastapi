"""`/text/clean` honors `body.participants` for manual-notes workflows.

Codex Round 2 finding (P2 — Task 1.26.6): `TextCleanRequest.participants`
was accepted by Pydantic but silently dropped by the handler. Manual-notes
workflows (no calendar event in the time window) lost all participant
context — callers got no `contact_ids`, no front-matter, no queue signal.

These tests pin the new contract:

1. **No-calendar + participants flow**: when no calendar match is found
   AND `participants` are provided in the body, the three-state branching
   loop runs against the request-body participants. Known business-domain
   participants resolve to contacts; unknown business-domain participants
   produce queue signals.

2. **Caller-wins**: when BOTH a calendar match AND `body.participants`
   are present, the request-body participants override the calendar
   attendees for the three-state loop. Manual notes flows are an explicit
   "here are the people who were in the room" signal that should win over
   the calendar's snapshot.

3. **Empty-list semantic**: `participants=[]` means "explicitly no
   participants", do NOT fall back to calendar.

Mock-driven (matches the existing `test_per_attendee_branching.py` and
`test_recording_user_id_wiring.py` patterns); no DB required.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest

# Set JWT test environment BEFORE importing the app.
os.environ.setdefault("INTERNAL_JWT_SECRET", "test-secret-that-is-at-least-32-characters-long")
os.environ.setdefault("INTERNAL_JWT_ISSUER", "eq-frontend")
os.environ.setdefault("INTERNAL_JWT_AUDIENCE", "eq-backend")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _mock_session_ctx():
    """Return a patched get_async_session that yields a fake AsyncMock session."""
    fake_session = MagicMock()
    fake_session.execute = AsyncMock()
    fake_session.commit = AsyncMock()

    class _AsyncCM:
        async def __aenter__(self_inner):
            return fake_session

        async def __aexit__(self_inner, exc_type, exc, tb):
            return False

    return patch(
        "services.transcript_enrichment.get_async_session",
        new=lambda: _AsyncCM(),
    )


@pytest.fixture
def service():
    """Service with ENABLE_TRANSCRIPT_ENRICHMENT patched true for the test duration."""
    with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
        from services.transcript_enrichment import TranscriptEnrichmentService
        yield TranscriptEnrichmentService()


# ---------------------------------------------------------------------------
# Test 1: enrich() — no calendar match + participants → three-state branching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_uses_participants_when_no_calendar_match(service):
    """No calendar event in window + body.participants provided →
    three-state branching runs against participants.

    Setup: anchor account_id is `acme`. Two participants:
      - alice@acme.com         (BUSINESS, known domain → contact created)
      - partner@consultingco.com (BUSINESS, unknown domain → queue signal)

    Asserts:
      - _resolve_contact called once (alice only)
      - upsert_queue_entry + insert_signal called once (partner only)
      - result.contacts has exactly one entry (alice)
      - No fallback to anchor account for partner.
    """
    from models.participant_spec import ParticipantSpec

    tenant_id = str(uuid.uuid4())
    acme_account_id = str(uuid.uuid4())
    alice_contact_id = str(uuid.uuid4())
    queue_id = str(uuid.uuid4())

    participants = [
        ParticipantSpec(email="alice@acme.com", display_name="Alice"),
        ParticipantSpec(email="partner@consultingco.com", display_name="Partner"),
    ]

    async def fake_lookup(session, tenant_id, domain):
        return acme_account_id if domain == "acme.com" else None

    async def fake_resolve(**kwargs):
        assert kwargs["email"] == "alice@acme.com"
        assert kwargs["account_id"] == acme_account_id, (
            "known-domain participant must be created with the looked-up "
            "account_id, never the anchor as a fallback"
        )
        return {
            "contact_id": alice_contact_id,
            "name": "Alice",
            "is_new": True,
            "tavily_lookups": 0,
        }

    with patch.object(service, "_match_calendar_event",
                      new_callable=AsyncMock, return_value=None), \
         patch.object(service, "_get_attendees",
                      new_callable=AsyncMock) as mock_get_attendees, \
         patch.object(service, "_resolve_contact",
                      side_effect=fake_resolve) as mock_resolve, \
         _mock_session_ctx(), \
         patch("services.transcript_enrichment.lookup_account_by_domain",
               side_effect=fake_lookup) as mock_lookup, \
         patch("services.transcript_enrichment.reopen_archived_entry",
               new_callable=AsyncMock, return_value=None) as mock_reopen, \
         patch("services.transcript_enrichment.upsert_queue_entry",
               new_callable=AsyncMock, return_value=queue_id) as mock_upsert, \
         patch("services.transcript_enrichment.insert_signal",
               new_callable=AsyncMock) as mock_insert_signal:
        result = await service.enrich(
            tenant_id=tenant_id,
            transcript_timestamp=datetime.now(timezone.utc),
            raw_transcript="manual note text",
            account_id=acme_account_id,
            recording_user_id="user-recording",
            tenant_internal_domains=set(),
            participants=participants,
        )

    # No calendar match → _get_attendees must NOT be called.
    mock_get_attendees.assert_not_called()

    # alice → resolved to contact via known-domain lookup.
    # Both participants hit the lookup (alice=acme, partner=consultingco).
    assert mock_lookup.await_count == 2
    lookup_domains = {
        call.kwargs["domain"] for call in mock_lookup.await_args_list
    }
    assert lookup_domains == {"acme.com", "consultingco.com"}
    mock_resolve.assert_awaited_once()

    # partner → queued, not resolved to a contact.
    mock_reopen.assert_awaited_once()
    mock_upsert.assert_awaited_once()
    upsert_call = mock_upsert.await_args
    assert upsert_call.kwargs["owner_user_id"] == "user-recording"
    assert upsert_call.kwargs["domain"] == "consultingco.com"
    assert upsert_call.kwargs["discovered_from_type"] == "transcript"

    mock_insert_signal.assert_awaited_once()
    proposal = mock_insert_signal.await_args.kwargs["proposal"]
    assert proposal.contact_email == "partner@consultingco.com"
    assert proposal.source_user_id == "user-recording"

    # Result: one contact (alice), no orphan for partner.
    assert len(result.contacts) == 1
    assert result.contacts[0].contact_id == alice_contact_id
    assert result.contacts[0].email == "alice@acme.com"
    assert result.contact_ids == [alice_contact_id]


# ---------------------------------------------------------------------------
# Test 1b: enrich() — no-calendar + unknown-domain participant → queue rows
#                      capture the REQUEST'S interaction_id, not NULL.
#                      Codex Round 4 P2 fix.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_threads_interaction_id_into_queue_signals_when_no_calendar(service):
    """Codex Round 4 P2: in the manual-notes flow (no calendar match) with an
    unknown-business-domain participant, both `upsert_queue_entry` and
    `SignalProposal.interaction_id` MUST receive the request's interaction_id —
    not None.

    Why this matters: `pending_signal_dedup` is a unique constraint on
    (queue_id, contact_email, source_type, interaction_id, calendar_event_id).
    SQL semantics treat NULL != NULL, so a NULL interaction_id + NULL
    calendar_event_id means retries of the same manual-notes interaction
    will produce duplicate signal rows. The fix is to fall back to the
    request's interaction_id when there is no calendar event to anchor to.
    """
    from models.participant_spec import ParticipantSpec

    tenant_id = str(uuid.uuid4())
    acme_account_id = str(uuid.uuid4())
    queue_id = str(uuid.uuid4())
    request_interaction_id = str(uuid.uuid4())

    participants = [
        ParticipantSpec(email="partner@consultingco.com", display_name="Partner"),
    ]

    async def fake_lookup(session, tenant_id, domain):
        # consultingco.com is unknown → queue signal path
        return None

    with patch.object(service, "_match_calendar_event",
                      new_callable=AsyncMock, return_value=None), \
         patch.object(service, "_get_attendees",
                      new_callable=AsyncMock) as mock_get_attendees, \
         patch.object(service, "_resolve_contact",
                      new_callable=AsyncMock) as mock_resolve, \
         _mock_session_ctx(), \
         patch("services.transcript_enrichment.lookup_account_by_domain",
               side_effect=fake_lookup), \
         patch("services.transcript_enrichment.reopen_archived_entry",
               new_callable=AsyncMock, return_value=None), \
         patch("services.transcript_enrichment.upsert_queue_entry",
               new_callable=AsyncMock, return_value=queue_id) as mock_upsert, \
         patch("services.transcript_enrichment.insert_signal",
               new_callable=AsyncMock) as mock_insert_signal:
        await service.enrich(
            tenant_id=tenant_id,
            transcript_timestamp=datetime.now(timezone.utc),
            raw_transcript="manual note text",
            account_id=acme_account_id,
            recording_user_id="user-recording",
            tenant_internal_domains=set(),
            participants=participants,
            interaction_id=request_interaction_id,
        )

    # No calendar → _get_attendees / _resolve_contact never called.
    mock_get_attendees.assert_not_called()
    mock_resolve.assert_not_called()

    # Queue parent row captures the request's interaction_id.
    mock_upsert.assert_awaited_once()
    upsert_kwargs = mock_upsert.await_args.kwargs
    assert upsert_kwargs["discovered_from_interaction_id"] == request_interaction_id, (
        "no-calendar manual-notes flow must thread the request's "
        "interaction_id into discovered_from_interaction_id so retries "
        "deduplicate via pending_signal_dedup. Got "
        f"{upsert_kwargs['discovered_from_interaction_id']!r}"
    )

    # Signal row captures the request's interaction_id (calendar_event_id
    # stays None because there's no calendar event).
    mock_insert_signal.assert_awaited_once()
    proposal = mock_insert_signal.await_args.kwargs["proposal"]
    assert proposal.interaction_id == request_interaction_id, (
        "no-calendar manual-notes flow must thread the request's "
        "interaction_id into SignalProposal.interaction_id so the dedup "
        f"unique constraint matches across retries. Got {proposal.interaction_id!r}"
    )
    assert proposal.calendar_event_id is None, (
        "calendar_event_id must remain None — there's no calendar event "
        "in the no-calendar path. Only interaction_id is filled in."
    )


@pytest.mark.asyncio
async def test_enrich_uses_event_id_when_calendar_match_present(service):
    """Regression: when a calendar match IS present, the queue rows must
    continue to use event_id (the existing behavior), NOT the request's
    interaction_id. Calendar event is the more specific anchor when
    available; interaction_id is only the fallback for no-calendar flows.
    """
    from models.participant_spec import ParticipantSpec

    tenant_id = str(uuid.uuid4())
    acme_account_id = str(uuid.uuid4())
    queue_id = str(uuid.uuid4())
    request_interaction_id = str(uuid.uuid4())

    event_uuid = uuid.uuid4()
    event = {
        "id": event_uuid,
        "title": "Cross-co sync",
        "_match_method": "time_window",
    }
    participants = [
        ParticipantSpec(email="partner@consultingco.com", display_name="Partner"),
    ]

    async def fake_lookup(session, tenant_id, domain):
        return None  # unknown domain → queue path

    with patch.object(service, "_match_calendar_event",
                      new_callable=AsyncMock, return_value=event), \
         patch.object(service, "_get_attendees",
                      new_callable=AsyncMock, return_value=[]), \
         patch.object(service, "_resolve_contact",
                      new_callable=AsyncMock), \
         _mock_session_ctx(), \
         patch("services.transcript_enrichment.lookup_account_by_domain",
               side_effect=fake_lookup), \
         patch("services.transcript_enrichment.reopen_archived_entry",
               new_callable=AsyncMock, return_value=None), \
         patch("services.transcript_enrichment.upsert_queue_entry",
               new_callable=AsyncMock, return_value=queue_id) as mock_upsert, \
         patch("services.transcript_enrichment.insert_signal",
               new_callable=AsyncMock) as mock_insert_signal:
        await service.enrich(
            tenant_id=tenant_id,
            transcript_timestamp=datetime.now(timezone.utc),
            raw_transcript="note",
            account_id=acme_account_id,
            recording_user_id="user-recording",
            tenant_internal_domains=set(),
            participants=participants,
            interaction_id=request_interaction_id,
        )

    mock_upsert.assert_awaited_once()
    upsert_kwargs = mock_upsert.await_args.kwargs
    assert upsert_kwargs["discovered_from_interaction_id"] == str(event_uuid), (
        "When a calendar event matches, queue rows must anchor to event_id, "
        "not the request's interaction_id."
    )

    mock_insert_signal.assert_awaited_once()
    proposal = mock_insert_signal.await_args.kwargs["proposal"]
    assert proposal.interaction_id == str(event_uuid)
    assert proposal.calendar_event_id == str(event_uuid)


# ---------------------------------------------------------------------------
# Test 2: enrich() — participants override calendar attendees (caller-wins)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_participants_override_calendar_attendees(service):
    """When BOTH calendar match AND participants are provided, participants
    win for the three-state branching loop.

    Setup: calendar event has attendee `wrong@calendar.com`. Request body
    provides `right@acme.com` (known) as the only participant. The handler
    must branch over the request participants, NOT the calendar attendees.
    """
    from models.participant_spec import ParticipantSpec

    tenant_id = str(uuid.uuid4())
    acme_account_id = str(uuid.uuid4())
    right_contact_id = str(uuid.uuid4())

    event = {
        "id": uuid.uuid4(),
        "title": "Acme sync",
        "_match_method": "time_window",
    }
    calendar_attendees = [
        {"email": "wrong@calendar.com", "display_name": "Wrong Person",
         "is_organizer": True, "is_optional": False},
    ]
    participants = [
        ParticipantSpec(email="right@acme.com", display_name="Right Person"),
    ]

    async def fake_lookup(session, tenant_id, domain):
        # Only acme.com is known. If "calendar.com" ever shows up here,
        # the override didn't work.
        assert domain == "acme.com", (
            f"caller-wins violated: lookup ran on {domain!r}, "
            "but participants should have replaced calendar attendees"
        )
        return acme_account_id

    async def fake_resolve(**kwargs):
        assert kwargs["email"] == "right@acme.com", (
            "caller-wins violated: resolve was called on the calendar "
            "attendee instead of the request-body participant"
        )
        return {
            "contact_id": right_contact_id,
            "name": "Right Person",
            "is_new": True,
            "tavily_lookups": 0,
        }

    with patch.object(service, "_match_calendar_event",
                      new_callable=AsyncMock, return_value=event), \
         patch.object(service, "_get_attendees",
                      new_callable=AsyncMock, return_value=calendar_attendees), \
         patch.object(service, "_resolve_contact",
                      side_effect=fake_resolve) as mock_resolve, \
         _mock_session_ctx(), \
         patch("services.transcript_enrichment.lookup_account_by_domain",
               side_effect=fake_lookup) as mock_lookup, \
         patch("services.transcript_enrichment.upsert_queue_entry",
               new_callable=AsyncMock) as mock_upsert, \
         patch("services.transcript_enrichment.insert_signal",
               new_callable=AsyncMock) as mock_insert_signal:
        result = await service.enrich(
            tenant_id=tenant_id,
            transcript_timestamp=datetime.now(timezone.utc),
            raw_transcript="note with both",
            account_id=acme_account_id,
            recording_user_id="user-recording",
            tenant_internal_domains=set(),
            participants=participants,
        )

    # Lookup ran exactly once — only on the participant, not the
    # calendar attendee. (fake_lookup asserts the domain.)
    assert mock_lookup.await_count == 1
    mock_resolve.assert_awaited_once()
    mock_upsert.assert_not_called()
    mock_insert_signal.assert_not_called()

    # The calendar match metadata is still preserved (meeting_title, event_id).
    assert result.calendar_event_id == str(event["id"])
    assert result.meeting_title == "Acme sync"

    # The contact is the request-body participant, not the calendar attendee.
    assert len(result.contacts) == 1
    assert result.contacts[0].contact_id == right_contact_id
    assert result.contacts[0].email == "right@acme.com"


# ---------------------------------------------------------------------------
# Test 3: enrich() — empty-list participants == "no participants"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_empty_participants_does_not_fall_back_to_calendar(service):
    """`participants=[]` is an explicit "no participants" signal and must
    NOT fall back to the calendar attendee list. Distinct from
    `participants=None` which means "I'm not providing any signal —
    use whatever you've got."
    """
    from typing import List

    from models.participant_spec import ParticipantSpec

    tenant_id = str(uuid.uuid4())
    acme_account_id = str(uuid.uuid4())

    event = {
        "id": uuid.uuid4(),
        "title": "Acme sync",
        "_match_method": "time_window",
    }
    calendar_attendees = [
        {"email": "alice@acme.com", "display_name": "Alice",
         "is_organizer": True, "is_optional": False},
    ]
    participants: List[ParticipantSpec] = []  # explicit empty list

    with patch.object(service, "_match_calendar_event",
                      new_callable=AsyncMock, return_value=event), \
         patch.object(service, "_get_attendees",
                      new_callable=AsyncMock, return_value=calendar_attendees), \
         patch.object(service, "_resolve_contact",
                      new_callable=AsyncMock) as mock_resolve, \
         _mock_session_ctx(), \
         patch("services.transcript_enrichment.lookup_account_by_domain",
               new_callable=AsyncMock, return_value=acme_account_id) as mock_lookup, \
         patch("services.transcript_enrichment.upsert_queue_entry",
               new_callable=AsyncMock) as mock_upsert, \
         patch("services.transcript_enrichment.insert_signal",
               new_callable=AsyncMock) as mock_insert_signal:
        result = await service.enrich(
            tenant_id=tenant_id,
            transcript_timestamp=datetime.now(timezone.utc),
            raw_transcript="explicit no-participants note",
            account_id=acme_account_id,
            recording_user_id="user-recording",
            tenant_internal_domains=set(),
            participants=participants,
        )

    # Empty list == explicit "no participants": no lookup, no resolve,
    # no queue, no contact. Calendar attendees are NOT used as fallback.
    mock_lookup.assert_not_called()
    mock_resolve.assert_not_called()
    mock_upsert.assert_not_called()
    mock_insert_signal.assert_not_called()
    assert result.contacts == []
    # But the calendar match metadata is still preserved.
    assert result.calendar_event_id == str(event["id"])


# ---------------------------------------------------------------------------
# Test 4: /text/clean handler passes body.participants to enrich()
# ---------------------------------------------------------------------------


def _make_jwt() -> str:
    now = int(time.time())
    payload = {
        "tenant_id": str(uuid.uuid4()),
        "user_id": "auth0|test-participants-wiring",
        "iss": os.environ["INTERNAL_JWT_ISSUER"],
        "aud": os.environ["INTERNAL_JWT_AUDIENCE"],
        "iat": now,
        "exp": now + 300,
    }
    return pyjwt.encode(payload, os.environ["INTERNAL_JWT_SECRET"], algorithm="HS256")


@pytest.mark.asyncio
async def test_text_clean_handler_passes_body_participants_to_enrich():
    """The /text/clean handler must pass body.participants to enrich().

    This is the wiring half of Task 1.26.6 — the model accepted the field,
    but the handler dropped it. We capture the kwargs at the enrich()
    boundary and assert participants are forwarded with the correct shape.
    """
    from fastapi.testclient import TestClient
    from models.participant_spec import ParticipantSpec
    from models.request_context import RequestContext
    from main import app

    captured_kwargs: dict = {}

    class _FakeEnrichment:
        contact_ids = None
        calendar_event_id = None
        match_confidence = None
        match_method = None
        front_matter = None

        def to_extras_dict(self):
            return {}

    async def fake_enrich(self, **kwargs):
        captured_kwargs.update(kwargs)
        return _FakeEnrichment()

    async def fake_clean(text: str):
        return text

    async def fake_publish(envelope):
        return {"kinesis_sequence": "seq-1", "eventbridge_id": "ev-1"}

    async def fake_intel(**kwargs):
        return None

    async def fake_internal_domains(tenant_id: str):
        return set()

    account_id = str(uuid.uuid4())
    request_payload = {
        "text": "manual note",
        "interaction_type": "note",
        "source": "test",
        "account_id": account_id,
        "participants": [
            {"email": "alice@acme.com", "display_name": "Alice"},
            {"email": "partner@consultingco.com"},
        ],
    }
    tenant_id = "11111111-1111-4111-8111-111111111111"
    user_id = "auth-user-id"
    pg_user_id = str(uuid.uuid4())

    fake_context = RequestContext(
        tenant_id=tenant_id,
        user_id=user_id,
        pg_user_id=pg_user_id,
        account_id=account_id,
        interaction_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        user_name="Test User",
    )

    with patch(
        "routers.text.get_auth_context_ingestion", return_value=fake_context,
    ), patch(
        "services.transcript_enrichment.TranscriptEnrichmentService.enrich",
        new=fake_enrich,
    ), patch(
        "services.batch_cleaner_service.BatchCleanerService.clean_transcript",
        new=AsyncMock(side_effect=fake_clean),
    ), patch(
        "services.aws_event_publisher.AWSEventPublisher.publish_envelope",
        new=AsyncMock(side_effect=fake_publish),
    ), patch(
        "services.intelligence_service.IntelligenceService.process_transcript",
        new=AsyncMock(side_effect=fake_intel),
    ), patch(
        "routers.text.get_tenant_internal_domains",
        new=AsyncMock(side_effect=fake_internal_domains),
    ):
        client = TestClient(app)
        response = client.post(
            "/text/clean",
            json=request_payload,
            headers={
                "Authorization": "Bearer fake",
                "X-Account-ID": account_id,
            },
        )

    assert response.status_code == 200, response.text
    forwarded = captured_kwargs.get("participants")
    assert forwarded is not None, (
        "/text/clean dropped body.participants on the floor — handler "
        "must forward the field to enrich()"
    )
    assert isinstance(forwarded, list), (
        f"participants must be forwarded as a list; got {type(forwarded)!r}"
    )
    assert len(forwarded) == 2
    # Each element must be a ParticipantSpec (Pydantic-parsed), not a raw dict.
    assert all(isinstance(p, ParticipantSpec) for p in forwarded), (
        "participants must be forwarded as ParticipantSpec instances "
        "(the Pydantic model), not raw dicts"
    )
    emails = {p.email for p in forwarded}
    assert emails == {"alice@acme.com", "partner@consultingco.com"}
