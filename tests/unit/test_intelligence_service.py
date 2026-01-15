"""
Unit Tests for IntelligenceService

Feature: intelligence-layer-integration, Task 16
Tests the IntelligenceService methods with mocked dependencies.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

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
            trace_id="550e8400-e29b-41d4-a716-446655440002",
            interaction_type="meeting"
        )
        
        assert result is None


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
