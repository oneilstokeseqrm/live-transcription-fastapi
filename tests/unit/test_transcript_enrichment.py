"""Unit Tests for TranscriptEnrichmentService.

Tests calendar matching, contact resolution, front-matter composition,
name heuristic, and enrichment result assembly with mocked DB.
"""
import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from models.enrichment_models import ResolvedContact, EnrichmentResult


# --- Name Heuristic Tests ---

class TestNameFromEmailHeuristic:
    """Tests for _name_from_email_heuristic static method."""

    @pytest.fixture
    def service(self):
        with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
            from services.transcript_enrichment import TranscriptEnrichmentService
            return TranscriptEnrichmentService()

    def test_two_part_name(self, service):
        """jane.smith@company.com → ('Jane', 'Smith')"""
        result = service._name_from_email_heuristic("jane.smith@company.com")
        assert result == ("Jane", "Smith")

    def test_hyphenated_name(self, service):
        """jean-luc.picard@starfleet.com → ('Jean Luc', 'Picard')"""
        result = service._name_from_email_heuristic("jean-luc.picard@starfleet.com")
        assert result == ("Jean Luc", "Picard")

    def test_underscore_name(self, service):
        """bob_jones@acme.com → ('Bob', 'Jones')"""
        result = service._name_from_email_heuristic("bob_jones@acme.com")
        assert result == ("Bob", "Jones")

    def test_single_part_low_confidence(self, service):
        """jsmith@company.com → None (not confident enough)"""
        result = service._name_from_email_heuristic("jsmith@company.com")
        assert result is None

    def test_generic_noreply(self, service):
        """noreply@company.com → None (generic prefix)"""
        result = service._name_from_email_heuristic("noreply@company.com")
        assert result is None

    def test_generic_support(self, service):
        """support@company.com → None"""
        result = service._name_from_email_heuristic("support@company.com")
        assert result is None

    def test_generic_info(self, service):
        """info@company.com → None"""
        result = service._name_from_email_heuristic("info@company.com")
        assert result is None

    def test_single_char_abbreviation(self, service):
        """j.smith@company.com → None (single char part)"""
        result = service._name_from_email_heuristic("j.smith@company.com")
        assert result is None

    def test_number_part(self, service):
        """john.123@company.com → None (numeric part)"""
        result = service._name_from_email_heuristic("john.123@company.com")
        assert result is None


# --- Display Name Splitting Tests ---

class TestSplitDisplayName:
    """Tests for _split_display_name static method."""

    @pytest.fixture
    def service(self):
        with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
            from services.transcript_enrichment import TranscriptEnrichmentService
            return TranscriptEnrichmentService()

    def test_two_parts(self, service):
        assert service._split_display_name("Jane Smith") == ("Jane", "Smith")

    def test_three_parts(self, service):
        assert service._split_display_name("Mary Jane Watson") == ("Mary Jane", "Watson")

    def test_single_part(self, service):
        assert service._split_display_name("Madonna") == ("Madonna", "")

    def test_empty_string(self, service):
        assert service._split_display_name("") == ("", "")

    def test_none_value(self, service):
        assert service._split_display_name(None) == ("", "")

    def test_whitespace_only(self, service):
        assert service._split_display_name("   ") == ("", "")


# --- Front-Matter Composition Tests ---

class TestComposeFrontMatter:
    """Tests for _compose_front_matter static method."""

    @pytest.fixture
    def service(self):
        with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
            from services.transcript_enrichment import TranscriptEnrichmentService
            return TranscriptEnrichmentService()

    def test_basic_front_matter(self, service):
        ts = datetime(2026, 3, 12, 14, 0, 0, tzinfo=timezone.utc)
        contacts = [
            ResolvedContact(
                contact_id="uuid1", email="jane@acme.com",
                name="Jane Smith", role="organizer", is_new=False,
            ),
            ResolvedContact(
                contact_id="uuid2", email="bob@acme.com",
                name="Bob Jones", role="attendee", is_new=False,
            ),
        ]
        result = service._compose_front_matter(
            meeting_title="Q3 Pipeline Review",
            transcript_timestamp=ts,
            contacts=contacts,
            user_name="Pete O'Neil",
        )
        assert "---" in result
        assert "type: meeting" in result
        assert 'title: "Q3 Pipeline Review"' in result
        assert "date: 2026-03-12T14:00:00Z" in result
        assert "jane@acme.com (Jane Smith) [organizer]" in result
        assert "bob@acme.com (Bob Jones)" in result
        assert "[organizer]" not in result.split("bob@acme.com")[1].split("\n")[0]
        assert "recorder: Pete O'Neil" in result

    def test_front_matter_no_contacts(self, service):
        ts = datetime(2026, 3, 12, 14, 0, 0, tzinfo=timezone.utc)
        result = service._compose_front_matter(
            meeting_title="Test", transcript_timestamp=ts, contacts=[],
        )
        assert "attendees:" not in result
        assert "type: meeting" in result

    def test_front_matter_email_only_contact(self, service):
        ts = datetime(2026, 3, 12, 14, 0, 0, tzinfo=timezone.utc)
        contacts = [
            ResolvedContact(
                contact_id="uuid1", email="unknown@co.com",
                name=None, role="attendee", is_new=True,
            ),
        ]
        result = service._compose_front_matter(
            meeting_title="Test", transcript_timestamp=ts, contacts=contacts,
        )
        assert "unknown@co.com" in result
        # No parenthetical name when name is None
        assert "(None)" not in result

    def test_front_matter_escapes_yaml_special_chars(self, service):
        ts = datetime(2026, 3, 12, 14, 0, 0, tzinfo=timezone.utc)
        result = service._compose_front_matter(
            meeting_title='Meeting with "Quotes" and \\backslash',
            transcript_timestamp=ts, contacts=[],
        )
        assert r'\"Quotes\"' in result
        assert "\\\\" in result


# --- Enrichment Result Model Tests ---

class TestEnrichmentResult:
    """Tests for EnrichmentResult defaults and payload contract."""

    def test_default_result_is_empty(self):
        result = EnrichmentResult()
        assert result.contacts == []
        assert result.contact_ids == []
        assert result.meeting_title is None
        assert result.calendar_event_id is None
        assert result.front_matter is None
        assert result.match_confidence == "none"
        assert result.new_contacts_created == 0
        assert result.enrichment_source == "none"

    def test_contact_id_always_present(self):
        """Every ResolvedContact MUST have a contact_id."""
        contact = ResolvedContact(
            contact_id=str(uuid.uuid4()),
            email="test@test.com",
            name=None,
            role="attendee",
            is_new=True,
        )
        assert contact.contact_id is not None
        assert len(contact.contact_id) == 36  # UUID format

    def test_email_always_present(self):
        """Every ResolvedContact MUST have an email."""
        contact = ResolvedContact(
            contact_id=str(uuid.uuid4()),
            email="test@test.com",
            name=None,
            role="attendee",
            is_new=True,
        )
        assert contact.email is not None

    def test_name_is_nullable(self):
        """Name MAY be None for email-only contacts."""
        contact = ResolvedContact(
            contact_id=str(uuid.uuid4()),
            email="test@test.com",
            name=None,
            role="attendee",
            is_new=True,
        )
        assert contact.name is None


# --- Feature Flag Tests ---

class TestFeatureFlags:
    """Tests for enrichment feature flag behavior."""

    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self):
        """When ENABLE_TRANSCRIPT_ENRICHMENT=false, enrich() returns empty result."""
        with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", False):
            from services.transcript_enrichment import TranscriptEnrichmentService
            service = TranscriptEnrichmentService()
            result = await service.enrich(
                tenant_id=str(uuid.uuid4()),
                transcript_timestamp=datetime.now(timezone.utc),
                raw_transcript="test transcript",
            )
            assert result.contacts == []
            assert result.contact_ids == []
            assert result.enrichment_source == "none"


# --- Calendar Matching Tests ---

class TestCalendarMatching:
    """Tests for calendar event matching logic."""

    @pytest.fixture
    def service(self):
        with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
            from services.transcript_enrichment import TranscriptEnrichmentService
            return TranscriptEnrichmentService()

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, service):
        """No calendar event match → empty enrichment."""
        with patch.object(service, "_match_calendar_event", new_callable=AsyncMock, return_value=None):
            with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
                result = await service.enrich(
                    tenant_id=str(uuid.uuid4()),
                    transcript_timestamp=datetime.now(timezone.utc),
                    raw_transcript="test",
                )
                assert result.contacts == []
                assert result.enrichment_source == "none"

    @pytest.mark.asyncio
    async def test_match_with_no_attendees(self, service):
        """Calendar match but no attendees → enrichment with title only."""
        event = {
            "id": uuid.uuid4(),
            "title": "Test Meeting",
            "_match_method": "time_window",
        }
        with patch.object(service, "_match_calendar_event", new_callable=AsyncMock, return_value=event):
            with patch.object(service, "_get_attendees", new_callable=AsyncMock, return_value=[]):
                with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
                    result = await service.enrich(
                        tenant_id=str(uuid.uuid4()),
                        transcript_timestamp=datetime.now(timezone.utc),
                        raw_transcript="test",
                    )
                    assert result.meeting_title == "Test Meeting"
                    assert result.contacts == []
                    assert result.enrichment_source == "calendar_match"

    @pytest.mark.asyncio
    async def test_conference_url_match_is_high_confidence(self, service):
        """Conference URL match → high confidence."""
        event_id = uuid.uuid4()
        contact_id = str(uuid.uuid4())
        event = {
            "id": event_id,
            "title": "Zoom Call",
            "_match_method": "conference_url",
        }
        attendees = [
            {"email": "jane@acme.com", "display_name": "Jane Smith", "is_organizer": True, "is_optional": False}
        ]
        resolve_result = {"contact_id": contact_id, "name": "Jane Smith", "is_new": False, "tavily_lookups": 0}

        with patch.object(service, "_match_calendar_event", new_callable=AsyncMock, return_value=event):
            with patch.object(service, "_get_attendees", new_callable=AsyncMock, return_value=attendees):
                with patch.object(service, "_resolve_contact", new_callable=AsyncMock, return_value=resolve_result):
                    with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
                        result = await service.enrich(
                            tenant_id=str(uuid.uuid4()),
                            transcript_timestamp=datetime.now(timezone.utc),
                            raw_transcript="test",
                            conference_url="https://zoom.us/j/12345",
                        )
                        assert result.match_confidence == "high"
                        assert len(result.contacts) == 1
                        assert result.contacts[0].role == "organizer"

    @pytest.mark.asyncio
    async def test_time_window_match_is_medium_confidence(self, service):
        """Time window match → medium confidence."""
        event_id = uuid.uuid4()
        contact_id = str(uuid.uuid4())
        event = {
            "id": event_id,
            "title": "Meeting",
            "_match_method": "time_window",
        }
        attendees = [
            {"email": "bob@co.com", "display_name": "Bob Jones", "is_organizer": False, "is_optional": False}
        ]
        resolve_result = {"contact_id": contact_id, "name": "Bob Jones", "is_new": True, "tavily_lookups": 0}

        with patch.object(service, "_match_calendar_event", new_callable=AsyncMock, return_value=event):
            with patch.object(service, "_get_attendees", new_callable=AsyncMock, return_value=attendees):
                with patch.object(service, "_resolve_contact", new_callable=AsyncMock, return_value=resolve_result):
                    with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
                        result = await service.enrich(
                            tenant_id=str(uuid.uuid4()),
                            transcript_timestamp=datetime.now(timezone.utc),
                            raw_transcript="test",
                        )
                        assert result.match_confidence == "medium"
                        assert result.new_contacts_created == 1

    @pytest.mark.asyncio
    async def test_attendee_cap_enforced(self, service):
        """Attendees over ENRICHMENT_MAX_ATTENDEES are truncated."""
        event = {"id": uuid.uuid4(), "title": "Big Meeting", "_match_method": "time_window"}
        many_attendees = [
            {"email": f"user{i}@co.com", "display_name": f"User {i}", "is_organizer": False, "is_optional": False}
            for i in range(30)
        ]
        resolve_result = {"contact_id": str(uuid.uuid4()), "name": "User", "is_new": False, "tavily_lookups": 0}

        with patch.object(service, "_match_calendar_event", new_callable=AsyncMock, return_value=event):
            with patch.object(service, "_get_attendees", new_callable=AsyncMock, return_value=many_attendees):
                with patch.object(service, "_resolve_contact", new_callable=AsyncMock, return_value=resolve_result):
                    with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
                        with patch("services.transcript_enrichment.ENRICHMENT_MAX_ATTENDEES", 5):
                            result = await service.enrich(
                                tenant_id=str(uuid.uuid4()),
                                transcript_timestamp=datetime.now(timezone.utc),
                                raw_transcript="test",
                            )
                            assert len(result.contacts) == 5

    @pytest.mark.asyncio
    async def test_enrichment_error_returns_empty(self, service):
        """If enrichment raises, return empty result (non-fatal)."""
        with patch.object(service, "_match_calendar_event", new_callable=AsyncMock, side_effect=Exception("DB error")):
            with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
                result = await service.enrich(
                    tenant_id=str(uuid.uuid4()),
                    transcript_timestamp=datetime.now(timezone.utc),
                    raw_transcript="test",
                )
                assert result.contacts == []
                assert result.enrichment_source == "none"


# --- Contact Resolution Tests ---

class TestContactResolution:
    """Tests for contact resolution logic (mocked DB)."""

    @pytest.fixture
    def service(self):
        with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
            from services.transcript_enrichment import TranscriptEnrichmentService
            return TranscriptEnrichmentService()

    @pytest.mark.asyncio
    async def test_roles_correctly_assigned(self, service):
        """Organizer, attendee, and optional roles are correctly assigned."""
        event = {"id": uuid.uuid4(), "title": "Test", "_match_method": "time_window"}
        attendees = [
            {"email": "org@co.com", "display_name": "Org", "is_organizer": True, "is_optional": False},
            {"email": "att@co.com", "display_name": "Att", "is_organizer": False, "is_optional": False},
            {"email": "opt@co.com", "display_name": "Opt", "is_organizer": False, "is_optional": True},
        ]
        resolve_results = iter([
            {"contact_id": str(uuid.uuid4()), "name": "Org", "is_new": False, "tavily_lookups": 0},
            {"contact_id": str(uuid.uuid4()), "name": "Att", "is_new": False, "tavily_lookups": 0},
            {"contact_id": str(uuid.uuid4()), "name": "Opt", "is_new": False, "tavily_lookups": 0},
        ])

        async def mock_resolve(**kwargs):
            return next(resolve_results)

        with patch.object(service, "_match_calendar_event", new_callable=AsyncMock, return_value=event):
            with patch.object(service, "_get_attendees", new_callable=AsyncMock, return_value=attendees):
                with patch.object(service, "_resolve_contact", side_effect=mock_resolve):
                    with patch("services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True):
                        result = await service.enrich(
                            tenant_id=str(uuid.uuid4()),
                            transcript_timestamp=datetime.now(timezone.utc),
                            raw_transcript="test",
                        )
                        assert result.contacts[0].role == "organizer"
                        assert result.contacts[1].role == "attendee"
                        assert result.contacts[2].role == "optional"


# --- Payload Contract Tests ---

class TestPayloadContract:
    """Tests verifying the enrichment payload contract for downstream consumers."""

    def test_contact_ids_matches_contacts(self):
        """contact_ids must be the same set as [c.contact_id for c in contacts]."""
        cid1, cid2 = str(uuid.uuid4()), str(uuid.uuid4())
        contacts = [
            ResolvedContact(contact_id=cid1, email="a@b.com", name="A", role="attendee", is_new=False),
            ResolvedContact(contact_id=cid2, email="c@d.com", name="C", role="organizer", is_new=True),
        ]
        result = EnrichmentResult(
            contacts=contacts,
            contact_ids=[cid1, cid2],
        )
        assert result.contact_ids == [c.contact_id for c in result.contacts]

    def test_extras_dict_format(self):
        """Verify to_extras_dict() matches the contract from Part 2.4."""
        cid = str(uuid.uuid4())
        cal_id = str(uuid.uuid4())
        contacts = [
            ResolvedContact(contact_id=cid, email="jane@acme.com", name="Jane", role="organizer", is_new=False),
        ]
        result = EnrichmentResult(
            contacts=contacts,
            contact_ids=[cid],
            meeting_title="Test",
            calendar_event_id=cal_id,
            enrichment_source="calendar_match",
            match_confidence="high",
        )

        extras = result.to_extras_dict()

        assert extras["contact_ids"] == [cid]
        assert extras["contacts"][0]["contact_id"] == cid
        assert extras["contacts"][0]["email"] == "jane@acme.com"
        assert extras["enrichment_source"] == "calendar_match"
        assert extras["enrichment_confidence"] == "high"
        assert extras["meeting_title"] == "Test"
        assert extras["calendar_event_id"] == cal_id

    def test_extras_dict_empty_when_no_enrichment(self):
        """to_extras_dict() returns {} when enrichment_source is 'none'."""
        result = EnrichmentResult()
        assert result.to_extras_dict() == {}

    def test_extras_dict_title_only_no_contacts(self):
        """Calendar match with title but no contacts still includes metadata."""
        result = EnrichmentResult(
            meeting_title="Board Meeting",
            calendar_event_id=str(uuid.uuid4()),
            enrichment_source="calendar_match",
            match_confidence="medium",
        )
        extras = result.to_extras_dict()
        assert extras["enrichment_source"] == "calendar_match"
        assert extras["meeting_title"] == "Board Meeting"
        assert "contact_ids" not in extras  # No contacts resolved

    def test_has_enrichment_property(self):
        """has_enrichment is True when enrichment_source != 'none'."""
        assert not EnrichmentResult().has_enrichment
        assert EnrichmentResult(enrichment_source="calendar_match").has_enrichment

    def test_match_method_field(self):
        """match_method defaults to 'none' and is set correctly."""
        assert EnrichmentResult().match_method == "none"
        r = EnrichmentResult(match_method="conference_url")
        assert r.match_method == "conference_url"
