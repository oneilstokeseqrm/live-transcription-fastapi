"""Per-attendee three-state branching in transcript enrichment.

For a transcript with anchor account `acme.com` and attendees
[alice@acme.com, partner@consultingco.com, intern@gmail.com],
asserts:

- alice becomes a contact with account_id=acme
- partner produces a pending_account_mapping_signals row (no contact)
- intern produces no row anywhere

Implementation note (Option A — no orphan contacts):
DB-fixture infrastructure for full end-to-end integration tests is not yet
wired in this repo (no conftest.py, no test_session fixture). This file
implements the branching invariants as MOCK-DRIVEN unit-style integration
tests on the public surface of TranscriptEnrichmentService.enrich(). The
"real-DB" variant is captured as a skipped scaffold to be filled in by the
Phase 1.5 acceptance-gate work.

The PRIORITY here is to verify the LOGIC of three-state branching:
- PERSONAL domain → skip entirely (no contact, no signal)
- INTERNAL domain → skip entirely (Phase 1 no-op)
- BUSINESS + known account → create contact with the resolved account_id
- BUSINESS + unknown account → upsert queue + insert signal (no contact)

NEVER fall back to the anchor account for unknown-domain attendees.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Mock-driven unit-style integration tests (run today)
# ---------------------------------------------------------------------------


@pytest.fixture
def service():
    """Yields the service with ENABLE_TRANSCRIPT_ENRICHMENT patched for the
    full test duration (existing fixtures expired the patch on return)."""
    with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
        from services.transcript_enrichment import TranscriptEnrichmentService
        yield TranscriptEnrichmentService()


def _mock_session_ctx():
    """Return a patched get_async_session that yields a fake AsyncMock session.

    The session has execute() and commit() as AsyncMocks; tests that mock
    lookup_account_by_domain / upsert_queue_entry / insert_signal at module
    level bypass any SQL the session would otherwise emit.
    """
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


@pytest.mark.asyncio
async def test_personal_domain_attendee_is_skipped(service):
    """gmail.com attendee → no contact, no signal."""
    tenant_id = str(uuid.uuid4())
    acme_account_id = str(uuid.uuid4())

    event = {"id": uuid.uuid4(), "title": "Acme sync", "_match_method": "time_window"}
    attendees = [
        {"email": "intern@gmail.com", "display_name": "Intern",
         "is_organizer": False, "is_optional": False},
    ]

    with patch.object(service, "_match_calendar_event",
                      new_callable=AsyncMock, return_value=event), \
         patch.object(service, "_get_attendees",
                      new_callable=AsyncMock, return_value=attendees), \
         patch.object(service, "_resolve_contact",
                      new_callable=AsyncMock) as mock_resolve, \
         _mock_session_ctx(), \
         patch("services.transcript_enrichment.lookup_account_by_domain",
               new_callable=AsyncMock) as mock_lookup, \
         patch("services.transcript_enrichment.upsert_queue_entry",
               new_callable=AsyncMock) as mock_upsert, \
         patch("services.transcript_enrichment.insert_signal",
               new_callable=AsyncMock) as mock_insert_signal:
        result = await service.enrich(
            tenant_id=tenant_id,
            transcript_timestamp=datetime.now(timezone.utc),
            raw_transcript="test",
            account_id=acme_account_id,
            recording_user_id="user-1",
            tenant_internal_domains=set(),
        )

    # No contact created for the personal-domain attendee.
    mock_resolve.assert_not_called()
    # No queue/signal entry either — personal domains are pure skip.
    mock_lookup.assert_not_called()
    mock_upsert.assert_not_called()
    mock_insert_signal.assert_not_called()
    assert result.contacts == []


@pytest.mark.asyncio
async def test_internal_domain_attendee_is_skipped(service):
    """Internal-tenant-domain attendee → no contact, no signal (Phase 1)."""
    tenant_id = str(uuid.uuid4())
    acme_account_id = str(uuid.uuid4())

    event = {"id": uuid.uuid4(), "title": "Internal sync", "_match_method": "time_window"}
    attendees = [
        {"email": "alice@mycompany.com", "display_name": "Alice",
         "is_organizer": True, "is_optional": False},
    ]

    with patch.object(service, "_match_calendar_event",
                      new_callable=AsyncMock, return_value=event), \
         patch.object(service, "_get_attendees",
                      new_callable=AsyncMock, return_value=attendees), \
         patch.object(service, "_resolve_contact",
                      new_callable=AsyncMock) as mock_resolve, \
         _mock_session_ctx(), \
         patch("services.transcript_enrichment.lookup_account_by_domain",
               new_callable=AsyncMock) as mock_lookup, \
         patch("services.transcript_enrichment.upsert_queue_entry",
               new_callable=AsyncMock) as mock_upsert, \
         patch("services.transcript_enrichment.insert_signal",
               new_callable=AsyncMock) as mock_insert_signal:
        result = await service.enrich(
            tenant_id=tenant_id,
            transcript_timestamp=datetime.now(timezone.utc),
            raw_transcript="test",
            account_id=acme_account_id,
            recording_user_id="user-1",
            tenant_internal_domains={"mycompany.com"},
        )

    mock_resolve.assert_not_called()
    mock_lookup.assert_not_called()
    mock_upsert.assert_not_called()
    mock_insert_signal.assert_not_called()
    assert result.contacts == []


@pytest.mark.asyncio
async def test_known_business_domain_creates_contact_with_resolved_account(service):
    """alice@acme.com with known acme account → contact created with acme account_id."""
    tenant_id = str(uuid.uuid4())
    acme_account_id = str(uuid.uuid4())
    resolved_contact_id = str(uuid.uuid4())

    event = {"id": uuid.uuid4(), "title": "Acme sync", "_match_method": "time_window"}
    attendees = [
        {"email": "alice@acme.com", "display_name": "Alice",
         "is_organizer": True, "is_optional": False},
    ]

    async def fake_resolve(**kwargs):
        # The branching contract is that account_id passed in must be the
        # resolved (looked-up) account_id, NOT the anchor unmodified.
        assert kwargs["account_id"] == acme_account_id, (
            "known-domain attendee must be created with the looked-up account_id"
        )
        return {
            "contact_id": resolved_contact_id,
            "name": "Alice",
            "is_new": True,
            "tavily_lookups": 0,
        }

    with patch.object(service, "_match_calendar_event",
                      new_callable=AsyncMock, return_value=event), \
         patch.object(service, "_get_attendees",
                      new_callable=AsyncMock, return_value=attendees), \
         patch.object(service, "_resolve_contact",
                      side_effect=fake_resolve) as mock_resolve, \
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
            raw_transcript="test",
            account_id=acme_account_id,
            recording_user_id="user-1",
            tenant_internal_domains=set(),
        )

    mock_lookup.assert_awaited_once()
    mock_resolve.assert_awaited_once()
    mock_upsert.assert_not_called()
    mock_insert_signal.assert_not_called()
    assert len(result.contacts) == 1
    assert result.contacts[0].contact_id == resolved_contact_id


@pytest.mark.asyncio
async def test_unknown_business_domain_queues_signal_no_contact(service):
    """partner@consultingco.com (unknown domain) → signal only, NO contact."""
    tenant_id = str(uuid.uuid4())
    acme_account_id = str(uuid.uuid4())
    queue_id = str(uuid.uuid4())

    event = {"id": uuid.uuid4(), "title": "Partner sync", "_match_method": "time_window"}
    attendees = [
        {"email": "partner@consultingco.com", "display_name": "Partner",
         "is_organizer": False, "is_optional": False},
    ]

    with patch.object(service, "_match_calendar_event",
                      new_callable=AsyncMock, return_value=event), \
         patch.object(service, "_get_attendees",
                      new_callable=AsyncMock, return_value=attendees), \
         patch.object(service, "_resolve_contact",
                      new_callable=AsyncMock) as mock_resolve, \
         _mock_session_ctx(), \
         patch("services.transcript_enrichment.lookup_account_by_domain",
               new_callable=AsyncMock, return_value=None) as mock_lookup, \
         patch("services.transcript_enrichment.reopen_archived_entry",
               new_callable=AsyncMock, return_value=None) as mock_reopen, \
         patch("services.transcript_enrichment.upsert_queue_entry",
               new_callable=AsyncMock, return_value=queue_id) as mock_upsert, \
         patch("services.transcript_enrichment.insert_signal",
               new_callable=AsyncMock) as mock_insert_signal:
        result = await service.enrich(
            tenant_id=tenant_id,
            transcript_timestamp=datetime.now(timezone.utc),
            raw_transcript="test",
            account_id=acme_account_id,
            recording_user_id="user-recording",
            tenant_internal_domains=set(),
        )

    mock_lookup.assert_awaited_once()
    # No contact for unknown-domain attendee.
    mock_resolve.assert_not_called()
    # Reopen attempted first, then upsert because no archived row.
    mock_reopen.assert_awaited_once()
    mock_upsert.assert_awaited_once()
    upsert_call = mock_upsert.await_args
    assert upsert_call.kwargs["owner_user_id"] == "user-recording"
    assert upsert_call.kwargs["discovered_from_type"] == "transcript"
    assert upsert_call.kwargs["domain"] == "consultingco.com"
    # Signal inserted with correct contact metadata.
    mock_insert_signal.assert_awaited_once()
    signal_call = mock_insert_signal.await_args
    proposal = signal_call.kwargs["proposal"]
    assert proposal.source_type == "transcript"
    assert proposal.source_user_id == "user-recording"
    assert proposal.contact_email == "partner@consultingco.com"
    # And critically: result.contacts is empty for the partner attendee.
    assert result.contacts == []


@pytest.mark.asyncio
async def test_three_state_mixed_attendees(service):
    """alice@acme.com (known) + partner@consultingco.com (unknown) + intern@gmail.com (personal).

    Asserts:
    - alice → contact created with account_id=acme
    - partner → signal-only path, no contact
    - intern → fully skipped
    NEVER: partner falling back to anchor account.
    """
    tenant_id = str(uuid.uuid4())
    acme_account_id = str(uuid.uuid4())
    alice_contact_id = str(uuid.uuid4())
    queue_id = str(uuid.uuid4())

    event = {"id": uuid.uuid4(), "title": "Mixed sync", "_match_method": "time_window"}
    attendees = [
        {"email": "alice@acme.com", "display_name": "Alice",
         "is_organizer": True, "is_optional": False},
        {"email": "partner@consultingco.com", "display_name": "Partner",
         "is_organizer": False, "is_optional": False},
        {"email": "intern@gmail.com", "display_name": "Intern",
         "is_organizer": False, "is_optional": False},
    ]

    # acme.com → known; everything else (consultingco) → None.
    async def fake_lookup(session, tenant_id, domain):
        return acme_account_id if domain == "acme.com" else None

    async def fake_resolve(**kwargs):
        # Verify the resolved account_id is acme — never falls back wrongly.
        assert kwargs["email"] == "alice@acme.com"
        assert kwargs["account_id"] == acme_account_id
        return {
            "contact_id": alice_contact_id,
            "name": "Alice",
            "is_new": True,
            "tavily_lookups": 0,
        }

    with patch.object(service, "_match_calendar_event",
                      new_callable=AsyncMock, return_value=event), \
         patch.object(service, "_get_attendees",
                      new_callable=AsyncMock, return_value=attendees), \
         patch.object(service, "_resolve_contact",
                      side_effect=fake_resolve) as mock_resolve, \
         _mock_session_ctx(), \
         patch("services.transcript_enrichment.lookup_account_by_domain",
               side_effect=fake_lookup) as mock_lookup, \
         patch("services.transcript_enrichment.reopen_archived_entry",
               new_callable=AsyncMock, return_value=None), \
         patch("services.transcript_enrichment.upsert_queue_entry",
               new_callable=AsyncMock, return_value=queue_id) as mock_upsert, \
         patch("services.transcript_enrichment.insert_signal",
               new_callable=AsyncMock) as mock_insert_signal:
        result = await service.enrich(
            tenant_id=tenant_id,
            transcript_timestamp=datetime.now(timezone.utc),
            raw_transcript="test",
            account_id=acme_account_id,
            recording_user_id="user-recording",
            tenant_internal_domains=set(),
        )

    # Lookup called twice (acme + consultingco). gmail.com skipped before lookup.
    assert mock_lookup.await_count == 2
    # Resolve called once for alice; never for partner or intern.
    assert mock_resolve.await_count == 1
    # Upsert + signal called once for partner; never for intern.
    mock_upsert.assert_awaited_once()
    mock_insert_signal.assert_awaited_once()
    sig = mock_insert_signal.await_args.kwargs["proposal"]
    assert sig.contact_email == "partner@consultingco.com"
    # Only alice in resolved-contacts list.
    assert len(result.contacts) == 1
    assert result.contacts[0].contact_id == alice_contact_id


# ---------------------------------------------------------------------------
# Real-DB integration scaffold (skipped until Phase 1.5 DB fixtures land)
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="DB fixtures TBD for integration (Phase 1.5 acceptance gate)")
@pytest.mark.asyncio
async def test_three_state_branching_real_db():
    """Real-DB version of the mixed-attendee branching test.

    Pending: conftest.py with test_session, seeded_acme_account,
    test_tenant fixtures. When those land, replace this scaffold with:

      - Insert tenant, accounts(acme.com), provider_connections (internal domain).
      - Insert calendar_event + 3 attendees.
      - Call enrich() with no mocks.
      - Assert:
          * SELECT FROM contacts WHERE email='alice@acme.com' returns 1 row
            with account_id = acme.
          * SELECT FROM contacts WHERE email IN ('partner@consultingco.com',
            'intern@gmail.com') returns 0 rows.
          * SELECT FROM pending_account_mapping_signals WHERE contact_email=
            'partner@consultingco.com' returns 1 row.
          * SELECT FROM pending_account_mappings WHERE domain='consultingco.com'
            returns 1 row with owner_user_id = recording user.
    """
    pass
