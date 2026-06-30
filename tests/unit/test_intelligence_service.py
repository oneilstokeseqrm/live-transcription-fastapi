"""
Unit Tests for IntelligenceService

Feature: intelligence-layer-integration, Task 16
Tests the IntelligenceService methods with mocked dependencies.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

from models.extraction_models import (
    InteractionAnalysis,
    Summaries,
    ActionItem,
    Decision,
    Risk,
    RiskSeverityEnum,
    ProductFeedback,
    MarketIntelligence,
)


@pytest.fixture
def mock_instructor():
    """Mock instructor.from_provider to avoid OpenAI API key requirement."""
    with patch('services.intelligence_service.instructor') as mock:
        mock_client = MagicMock()
        mock.from_provider.return_value = mock_client
        yield mock


@pytest.fixture
def service(mock_instructor):
    """Create IntelligenceService with mocked instructor client."""
    from services.intelligence_service import IntelligenceService
    return IntelligenceService()


class TestContentHashGeneration:
    """Tests for _generate_content_hash method."""
    
    def test_content_hash_determinism(self, service):
        """Test that same input always produces same hash."""
        hash1 = service._generate_content_hash("action_item", "Schedule meeting")
        hash2 = service._generate_content_hash("action_item", "Schedule meeting")
        
        assert hash1 == hash2, "Same input must produce same hash"
    
    def test_content_hash_uniqueness_different_content(self, service):
        """Test that different content produces different hash."""
        hash1 = service._generate_content_hash("action_item", "Schedule meeting")
        hash2 = service._generate_content_hash("action_item", "Review document")
        
        assert hash1 != hash2, "Different content must produce different hash"
    
    def test_content_hash_uniqueness_different_type(self, service):
        """Test that different insight type produces different hash."""
        hash1 = service._generate_content_hash("action_item", "Important task")
        hash2 = service._generate_content_hash("key_takeaway", "Important task")
        
        assert hash1 != hash2, "Different type must produce different hash"
    
    def test_content_hash_format(self, service):
        """Test that hash is a valid hex string."""
        hash_value = service._generate_content_hash("risk", "Security concern")
        
        # SHA-256 produces 64 character hex string
        assert len(hash_value) == 64, "SHA-256 hash must be 64 characters"
        assert all(c in '0123456789abcdef' for c in hash_value), "Hash must be hex"
    
    def test_content_hash_empty_content(self, service):
        """Test hash generation with empty content."""
        hash_value = service._generate_content_hash("action_item", "")
        
        assert len(hash_value) == 64, "Empty content should still produce valid hash"
    
    def test_content_hash_special_characters(self, service):
        """Test hash generation with special characters."""
        hash1 = service._generate_content_hash("action_item", "Test with émojis 🎉")
        hash2 = service._generate_content_hash("action_item", "Test with émojis 🎉")
        
        assert hash1 == hash2, "Special characters should hash consistently"
        assert len(hash1) == 64


class TestExtractIntelligence:
    """Tests for _extract_intelligence method."""
    
    @pytest.fixture
    def mock_analysis(self):
        """Create a mock InteractionAnalysis for testing."""
        return InteractionAnalysis(
            summaries=Summaries(
                title="Test Meeting",
                headline="Test headline",
                brief="Test brief summary",
                detailed="Test detailed summary",
                spotlight="Key highlight"
            ),
            action_items=[ActionItem(description="Test action")],
            decisions=[Decision(decision="Test decision")],
            risks=[Risk(risk="Test risk", severity=RiskSeverityEnum.low)],
            key_takeaways=["Test takeaway"],
            product_feedback=[ProductFeedback(text="Test feedback")],
            market_intelligence=[MarketIntelligence(text="Test intel")]
        )
    
    @pytest.mark.asyncio
    async def test_extract_intelligence_success(self, service, mock_analysis):
        """Test successful intelligence extraction."""
        # Mock the instructor client
        service.client = MagicMock()
        service.client.create = AsyncMock(return_value=mock_analysis)
        
        result = await service._extract_intelligence("Test transcript content")
        
        assert result is not None
        assert result.summaries.title == "Test Meeting"
        assert len(result.action_items) == 1
        service.client.create.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_extract_intelligence_returns_none_on_api_error(self, service):
        """Test that extraction returns None on API error."""
        # Mock the instructor client to raise an exception
        service.client = MagicMock()
        service.client.create = AsyncMock(side_effect=Exception("API Error"))
        
        result = await service._extract_intelligence("Test transcript")
        
        assert result is None, "Should return None on API error"
    
    @pytest.mark.asyncio
    async def test_extract_intelligence_returns_none_on_timeout(self, service):
        """Test that extraction returns None on timeout."""
        import asyncio
        
        # Mock the instructor client to raise timeout
        service.client = MagicMock()
        service.client.create = AsyncMock(side_effect=asyncio.TimeoutError())
        
        result = await service._extract_intelligence("Test transcript")
        
        assert result is None, "Should return None on timeout"
    
    @pytest.mark.asyncio
    async def test_extract_intelligence_returns_none_on_validation_error(self, service):
        """Test that extraction returns None on validation error."""
        from pydantic import ValidationError
        
        # Mock the instructor client to raise validation error
        service.client = MagicMock()
        service.client.create = AsyncMock(
            side_effect=ValidationError.from_exception_data("test", [])
        )
        
        result = await service._extract_intelligence("Test transcript")
        
        assert result is None, "Should return None on validation error"


class TestSystemPrompt:
    """Tests for the system prompt."""
    
    def test_system_prompt_contains_gtm_focus(self, service):
        """Test that system prompt mentions GTM focus."""
        prompt = service._get_system_prompt()
        
        assert "GTM" in prompt or "Go-To-Market" in prompt
    
    def test_system_prompt_contains_extraction_guidelines(self, service):
        """Test that system prompt contains extraction guidelines."""
        prompt = service._get_system_prompt()
        
        assert "Summaries" in prompt
        assert "Action Items" in prompt
        assert "Decisions" in prompt
        assert "Risks" in prompt
    
    def test_system_prompt_mentions_product_feedback(self, service):
        """Test that system prompt mentions product feedback."""
        prompt = service._get_system_prompt()
        
        assert "Product Feedback" in prompt or "product feedback" in prompt
    
    def test_system_prompt_mentions_market_intelligence(self, service):
        """Test that system prompt mentions market intelligence."""
        prompt = service._get_system_prompt()
        
        assert "Market Intelligence" in prompt or "market" in prompt.lower()


class TestProcessTranscript:
    """Tests for the main process_transcript method."""
    
    @pytest.fixture
    def mock_analysis(self):
        """Create a mock InteractionAnalysis for testing."""
        return InteractionAnalysis(
            summaries=Summaries(
                title="Test",
                headline="Test",
                brief="Test",
                detailed="Test",
                spotlight="Test"
            ),
            action_items=[],
            decisions=[],
            risks=[],
            key_takeaways=[],
            product_feedback=[],
            market_intelligence=[]
        )
    
    @pytest.mark.asyncio
    async def test_process_transcript_returns_none_on_extraction_failure(self, service):
        """Test that process_transcript returns None when extraction fails."""
        # Mock extraction to return None
        service._extract_intelligence = AsyncMock(return_value=None)

        result = await service.process_transcript(
            cleaned_transcript="Test transcript",
            interaction_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            # T1.8: account_id is now required (no default); pass a placeholder UUID.
            account_id="550e8400-e29b-41d4-a716-446655440003",
            trace_id="550e8400-e29b-41d4-a716-446655440002",
            interaction_type="meeting"
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_process_transcript_returns_none_on_persistence_failure(self, service, mock_analysis):
        """Test that process_transcript returns None when persistence fails."""
        # Mock extraction to succeed
        service._extract_intelligence = AsyncMock(return_value=mock_analysis)

        # Mock persistence to fail
        service._persist_intelligence = AsyncMock(side_effect=Exception("DB Error"))

        result = await service.process_transcript(
            cleaned_transcript="Test transcript",
            interaction_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            # T1.8: account_id is now required (no default); pass a placeholder UUID.
            account_id="550e8400-e29b-41d4-a716-446655440003",
            trace_id="550e8400-e29b-41d4-a716-446655440002",
            interaction_type="meeting"
        )

        assert result is None

    async def _capture_persisted_timestamp(self, service, mock_analysis, *, interaction_timestamp):
        """Run process_transcript with persistence mocked; return the
        interaction_timestamp handed to _persist_intelligence."""
        service._extract_intelligence = AsyncMock(return_value=mock_analysis)
        captured: dict = {}

        async def _capture(**kwargs):
            captured.update(kwargs)

        service._persist_intelligence = AsyncMock(side_effect=_capture)
        await service.process_transcript(
            cleaned_transcript="t",
            interaction_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="550e8400-e29b-41d4-a716-446655440001",
            account_id="550e8400-e29b-41d4-a716-446655440003",
            trace_id="550e8400-e29b-41d4-a716-446655440002",
            interaction_type="meeting",
            interaction_timestamp=interaction_timestamp,
        )
        return captured["interaction_timestamp"]

    @pytest.mark.asyncio
    async def test_process_transcript_normalizes_aware_utc_to_naive(self, service, mock_analysis):
        """Lane-2 columns are TIMESTAMP WITHOUT TIME ZONE and asyncpg REJECTS
        aware datetimes (DataError). Since /text/clean and Granola now pass an
        aware envelope.timestamp, process_transcript must hand
        _persist_intelligence a NAIVE UTC value — matching the legacy
        datetime.utcnow() storage convention — or the background persist crashes.
        """
        aware = datetime(2026, 3, 1, 14, 30, tzinfo=timezone.utc)
        ts = await self._capture_persisted_timestamp(
            service, mock_analysis, interaction_timestamp=aware
        )
        assert ts.tzinfo is None
        assert ts == datetime(2026, 3, 1, 14, 30)

    @pytest.mark.asyncio
    async def test_process_transcript_normalizes_offset_aware_to_naive_utc(self, service, mock_analysis):
        """An offset-aware value is converted to UTC THEN made naive (UTC wall
        clock), not just tz-stripped at local offset."""
        offset = datetime(2026, 3, 1, 9, 30, tzinfo=timezone(timedelta(hours=-5)))
        ts = await self._capture_persisted_timestamp(
            service, mock_analysis, interaction_timestamp=offset
        )
        assert ts.tzinfo is None
        assert ts == datetime(2026, 3, 1, 14, 30)  # 09:30-05:00 == 14:30Z

    @pytest.mark.asyncio
    async def test_process_transcript_naive_interaction_timestamp_unchanged(self, service, mock_analysis):
        """A naive value (legacy/fallback convention = naive UTC) passes through
        unchanged — byte-for-byte the pre-occurred_at behavior."""
        naive = datetime(2026, 3, 1, 14, 30)
        ts = await self._capture_persisted_timestamp(
            service, mock_analysis, interaction_timestamp=naive
        )
        assert ts.tzinfo is None
        assert ts == naive


class TestServiceInitialization:
    """Tests for IntelligenceService initialization."""
    
    def test_service_initializes_with_default_model(self, mock_instructor):
        """Test that service initializes with default model."""
        from services.intelligence_service import IntelligenceService
        
        with patch.dict('os.environ', {'OPENAI_MODEL': ''}, clear=False):
            service = IntelligenceService()
            # Default model should be gpt-4o when env var is empty or not set
            assert service.model == "" or service.model == "gpt-4o"
    
    def test_service_uses_env_model(self, mock_instructor):
        """Test that service uses OPENAI_MODEL from environment."""
        from services.intelligence_service import IntelligenceService

        with patch.dict('os.environ', {'OPENAI_MODEL': 'gpt-4-turbo'}):
            service = IntelligenceService()
            assert service.model == "gpt-4-turbo"


import inspect
from services.intelligence_service import IntelligenceService


def test_process_transcript_requires_account_id():
    sig = inspect.signature(IntelligenceService.process_transcript)
    param = sig.parameters["account_id"]
    # Required = no default
    assert param.default is inspect.Parameter.empty, (
        "process_transcript(account_id) must be required (no default), "
        f"got default={param.default!r}"
    )
    # And the annotation should not be Optional
    assert "Optional" not in str(param.annotation), (
        f"account_id annotation should not be Optional, got {param.annotation}"
    )


# ---------------------------------------------------------------------------
# Bug #8 regression tests
# ---------------------------------------------------------------------------
# Bug #8a: services/intelligence_service.py _persist_contact_links was
# INSERTing into raw_interactions without account_id (schema is NOT NULL),
# raising NotNullViolationError which was caught as "non-fatal" and rolled
# back the whole _persist_contact_links transaction. Result: 0 rows in
# raw_interactions(transcript), interaction_summaries, interaction_contact_links,
# calendar_event_interaction_links for every transcript reaching the
# conditional path.
#
# Bug #8b: the same function generated a fresh uuid4() for summary_id on
# every call, with no idempotency. Retries would produce duplicate
# interaction_summaries rows (and cascade duplicates into the link tables
# via summary_id FKs). Fixed by ON CONFLICT (tenant_id, interaction_id,
# summary_type) DO UPDATE ... RETURNING summary_id, mirroring the M2 pattern
# in services/account_provisioning/materialization.py.


def test_persist_contact_links_requires_account_id():
    """Bug #8a: account_id must be a required positional parameter."""
    sig = inspect.signature(IntelligenceService._persist_contact_links)
    assert "account_id" in sig.parameters, (
        "_persist_contact_links must accept account_id (required for "
        "raw_interactions.account_id NOT NULL column)"
    )
    param = sig.parameters["account_id"]
    assert param.default is inspect.Parameter.empty, (
        "_persist_contact_links(account_id) must be required (no default), "
        f"got default={param.default!r}"
    )
    assert "Optional" not in str(param.annotation), (
        f"account_id annotation should not be Optional, got {param.annotation}"
    )


def _make_mock_session(summary_id_to_return):
    """Build an AsyncMock session whose execute() returns a result object
    with scalar_one() yielding the given summary_id (used for the upsert
    RETURNING clause).
    """
    from unittest.mock import MagicMock, AsyncMock

    mock_session = MagicMock()

    # Result returned by the interaction_summaries upsert (the 2nd execute call).
    # The other execute calls don't read .scalar_one(); we still need them to
    # be awaitable AsyncMock calls.
    upsert_result = MagicMock()
    upsert_result.scalar_one = MagicMock(return_value=summary_id_to_return)

    mock_session.execute = AsyncMock(return_value=upsert_result)
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    # async context manager returning mock_session
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    return mock_session, mock_ctx


@pytest.mark.asyncio
async def test_persist_contact_links_includes_account_id_in_raw_interactions_insert(service):
    """Bug #8a: the raw_interactions INSERT must include account_id in both
    the column list and the bound parameters.
    """
    from uuid import UUID
    from unittest.mock import patch

    account_uuid_str = "0e49a47e-0200-5e4f-962c-2b3df57e0624"
    interaction_uuid_str = "11111111-1111-4111-8111-111111111111"
    tenant_uuid_str = "22222222-2222-4222-8222-222222222222"
    contact_uuid_str = "33333333-3333-4333-8333-333333333333"
    existing_summary_uuid = UUID("44444444-4444-4444-8444-444444444444")

    mock_session, mock_ctx = _make_mock_session(existing_summary_uuid)

    with patch(
        "services.intelligence_service.get_async_session", return_value=mock_ctx
    ):
        await service._persist_contact_links(
            interaction_id=interaction_uuid_str,
            tenant_id=tenant_uuid_str,
            account_id=account_uuid_str,
            contact_ids=[contact_uuid_str],
            interaction_type="meeting",
        )

    # First execute() call must be the raw_interactions INSERT
    first_call = mock_session.execute.call_args_list[0]
    sql_text_arg = first_call.args[0]
    params = first_call.args[1]
    sql_str = str(sql_text_arg)

    assert "INSERT INTO raw_interactions" in sql_str
    assert "account_id" in sql_str, (
        "raw_interactions INSERT must include account_id column"
    )
    assert ":account_id" in sql_str, (
        "raw_interactions INSERT must bind :account_id placeholder"
    )
    assert "account_id" in params, (
        "raw_interactions INSERT must pass account_id in params dict"
    )
    assert params["account_id"] == UUID(account_uuid_str)


@pytest.mark.asyncio
async def test_persist_contact_links_reuses_returned_summary_id_for_links(service):
    """Bug #8b: the function must read summary_id from the upsert's
    RETURNING clause (which yields the existing summary_id on retry, or the
    candidate one on first call) and bind THAT id to downstream link inserts.
    Proves the code doesn't fall back to a fresh uuid4() that ignores
    RETURNING.
    """
    from uuid import UUID
    from unittest.mock import patch

    account_uuid_str = "0e49a47e-0200-5e4f-962c-2b3df57e0624"
    interaction_uuid_str = "11111111-1111-4111-8111-111111111111"
    tenant_uuid_str = "22222222-2222-4222-8222-222222222222"
    contact_uuid_str = "33333333-3333-4333-8333-333333333333"
    calendar_event_uuid_str = "55555555-5555-4555-8555-555555555555"

    # Simulate retry: existing row has summary_id X; RETURNING gives X back.
    existing_summary_uuid = UUID("44444444-4444-4444-8444-444444444444")

    mock_session, mock_ctx = _make_mock_session(existing_summary_uuid)

    with patch(
        "services.intelligence_service.get_async_session", return_value=mock_ctx
    ):
        await service._persist_contact_links(
            interaction_id=interaction_uuid_str,
            tenant_id=tenant_uuid_str,
            account_id=account_uuid_str,
            contact_ids=[contact_uuid_str],
            interaction_type="meeting",
            calendar_event_id=calendar_event_uuid_str,
        )

    calls = mock_session.execute.call_args_list
    # 1: raw_interactions INSERT, 2: interaction_summaries upsert,
    # 3: interaction_contact_links INSERT, 4: calendar_event_interaction_links INSERT
    assert len(calls) >= 4

    # Step 2: interaction_summaries upsert MUST use ON CONFLICT ... RETURNING
    upsert_sql = str(calls[1].args[0])
    assert "INSERT INTO interaction_summaries" in upsert_sql
    assert "ON CONFLICT" in upsert_sql
    assert "tenant_id, interaction_id, summary_type" in upsert_sql, (
        "Upsert must use the composite UNIQUE on (tenant_id, interaction_id, "
        "summary_type) — column-tuple form, NOT named-constraint form (the "
        "underlying index is unnamed-by-constraint, matching the Bug #1 lesson)"
    )
    assert "RETURNING summary_id" in upsert_sql, (
        "Upsert must RETURN summary_id so the caller reuses existing id on retry"
    )

    # Step 3: interaction_contact_links INSERT must bind the RETURNED summary_id,
    # NOT a fresh uuid4 the caller generated locally.
    contact_link_params = calls[2].args[1]
    assert contact_link_params["summary_id"] == existing_summary_uuid, (
        f"interaction_contact_links.summary_id should be the RETURNING value "
        f"({existing_summary_uuid}), got {contact_link_params['summary_id']!r}"
    )

    # Step 4: calendar_event_interaction_links INSERT must bind the same id.
    cal_link_params = calls[3].args[1]
    assert cal_link_params["summary_id"] == existing_summary_uuid, (
        f"calendar_event_interaction_links.summary_id should be the RETURNING "
        f"value ({existing_summary_uuid}), got {cal_link_params['summary_id']!r}"
    )


@pytest.mark.asyncio
async def test_persist_contact_links_uses_returned_id_when_summary_is_new(service):
    """Bug #8b first-call path: on a fresh INSERT (no prior summary), the
    upsert's RETURNING yields the candidate summary_id we just inserted.
    The function must still read it from RETURNING (not from the local
    uuid4 it generated) — otherwise the contract is brittle and a future
    refactor that changes the candidate generation will silently break
    retries.
    """
    from uuid import UUID
    from unittest.mock import patch

    account_uuid_str = "0e49a47e-0200-5e4f-962c-2b3df57e0624"
    interaction_uuid_str = "11111111-1111-4111-8111-111111111111"
    tenant_uuid_str = "22222222-2222-4222-8222-222222222222"
    contact_uuid_str = "33333333-3333-4333-8333-333333333333"

    # First-call path: RETURNING gives back a fresh UUID (simulating the
    # candidate we just inserted). We control it via the mock.
    returned_uuid = UUID("66666666-6666-4666-8666-666666666666")

    mock_session, mock_ctx = _make_mock_session(returned_uuid)

    with patch(
        "services.intelligence_service.get_async_session", return_value=mock_ctx
    ):
        await service._persist_contact_links(
            interaction_id=interaction_uuid_str,
            tenant_id=tenant_uuid_str,
            account_id=account_uuid_str,
            contact_ids=[contact_uuid_str],
            interaction_type="meeting",
        )

    calls = mock_session.execute.call_args_list

    # The contact_links INSERT must bind the RETURNED uuid, not whatever
    # local uuid4() the function happened to generate.
    contact_link_params = calls[2].args[1]
    assert contact_link_params["summary_id"] == returned_uuid


@pytest.mark.asyncio
async def test_process_transcript_threads_account_id_to_persist_contact_links(service):
    """Bug #8a regression: process_transcript receives account_id and must
    pass it to _persist_contact_links (the silent-param-drop pattern that
    caused Bug #8). This catches the bug at the call site, not just the
    callee signature.
    """
    from unittest.mock import AsyncMock

    account_id = "0e49a47e-0200-5e4f-962c-2b3df57e0624"
    contact_id = "33333333-3333-4333-8333-333333333333"

    mock_analysis = InteractionAnalysis(
        summaries=Summaries(
            title="Test", headline="Test", brief="Test",
            detailed="Test", spotlight="Test"
        ),
        action_items=[], decisions=[], risks=[],
        key_takeaways=[], product_feedback=[], market_intelligence=[]
    )

    service._extract_intelligence = AsyncMock(return_value=mock_analysis)
    service._persist_intelligence = AsyncMock()
    service._persist_contact_links = AsyncMock()

    await service.process_transcript(
        cleaned_transcript="Test transcript",
        interaction_id="550e8400-e29b-41d4-a716-446655440000",
        tenant_id="550e8400-e29b-41d4-a716-446655440001",
        account_id=account_id,
        trace_id="550e8400-e29b-41d4-a716-446655440002",
        interaction_type="meeting",
        contact_ids=[contact_id],
    )

    service._persist_contact_links.assert_called_once()
    call_kwargs = service._persist_contact_links.call_args.kwargs
    assert call_kwargs.get("account_id") == account_id, (
        f"process_transcript must thread account_id={account_id!r} to "
        f"_persist_contact_links, got kwargs={call_kwargs!r}"
    )
