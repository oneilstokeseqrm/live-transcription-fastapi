"""
Integration Tests for Database Persistence

Feature: intelligence-layer-integration, Task 18
Tests database persistence using mocked sessions to avoid live data conflicts.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from uuid import UUID, uuid4

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
from models.db_models import (
    InteractionSummaryEntryModel,
    InteractionInsightModel,
    SummaryLevelEnum,
    InsightTypeEnum,
    ProfileTypeEnum,
    PersonaModel,
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


@pytest.fixture
def sample_analysis():
    """Create a sample InteractionAnalysis for testing."""
    return InteractionAnalysis(
        summaries=Summaries(
            title="Test Meeting Title",
            headline="Test headline for the meeting",
            brief="Brief summary of the test meeting",
            detailed="Detailed summary with all the important points discussed",
            spotlight="Key highlight from the meeting"
        ),
        action_items=[
            ActionItem(description="Follow up with client", owner="John"),
            ActionItem(description="Send proposal")
        ],
        decisions=[
            Decision(decision="Proceed with Phase 1", rationale="Budget approved")
        ],
        risks=[
            Risk(risk="Timeline may slip", severity=RiskSeverityEnum.medium, mitigation="Add buffer")
        ],
        key_takeaways=["Client is interested", "Budget is flexible"],
        product_feedback=[
            ProductFeedback(text="Need better reporting features")
        ],
        market_intelligence=[
            MarketIntelligence(text="Competitor launched new product")
        ]
    )


@pytest.fixture
def mock_persona():
    """Create a mock persona for testing."""
    persona = MagicMock(spec=PersonaModel)
    persona.id = uuid4()
    persona.code = "gtm"
    return persona


class TestPersistIntelligence:
    """Tests for _persist_intelligence method."""
    
    @pytest.mark.asyncio
    async def test_persist_creates_exactly_five_summary_entries(self, service, sample_analysis, mock_persona):
        """Test that persistence creates exactly 5 summary entries."""
        added_models = []
        
        mock_session = AsyncMock()
        mock_session.add = MagicMock(side_effect=lambda m: added_models.append(m))
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_persona)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        
        with patch('services.intelligence_service.get_async_session', return_value=mock_context):
            await service._persist_intelligence(
                analysis=sample_analysis,
                interaction_id=str(uuid4()),
                tenant_id=str(uuid4()),
                trace_id=str(uuid4()),
                persona_code="gtm",
                interaction_type="meeting",
                account_id=None,
                interaction_timestamp=datetime.utcnow()
            )
        
        summary_entries = [m for m in added_models if isinstance(m, InteractionSummaryEntryModel)]
        assert len(summary_entries) == 5, "Must create exactly 5 summary entries"
        
        levels = {e.level for e in summary_entries}
        expected_levels = {
            SummaryLevelEnum.title,
            SummaryLevelEnum.headline,
            SummaryLevelEnum.brief,
            SummaryLevelEnum.detailed,
            SummaryLevelEnum.spotlight
        }
        assert levels == expected_levels, "Must have all 5 summary levels"
    
    @pytest.mark.asyncio
    async def test_persist_creates_correct_insight_types(self, service, sample_analysis, mock_persona):
        """Test that persistence creates correct insight types."""
        added_models = []
        
        mock_session = AsyncMock()
        mock_session.add = MagicMock(side_effect=lambda m: added_models.append(m))
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_persona)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        
        with patch('services.intelligence_service.get_async_session', return_value=mock_context):
            await service._persist_intelligence(
                analysis=sample_analysis,
                interaction_id=str(uuid4()),
                tenant_id=str(uuid4()),
                trace_id=str(uuid4()),
                persona_code="gtm",
                interaction_type="meeting",
                account_id=None,
                interaction_timestamp=datetime.utcnow()
            )
        
        insights = [m for m in added_models if isinstance(m, InteractionInsightModel)]
        
        action_items = [i for i in insights if i.type == InsightTypeEnum.action_item]
        decisions = [i for i in insights if i.type == InsightTypeEnum.decision_made]
        risks = [i for i in insights if i.type == InsightTypeEnum.risk]
        key_takeaways = [i for i in insights if i.type == InsightTypeEnum.key_takeaway]
        product_feedback = [i for i in insights if i.type == InsightTypeEnum.product_feedback]
        market_intel = [i for i in insights if i.type == InsightTypeEnum.market_intelligence]
        
        assert len(action_items) == 2, "Must create 2 action_item insights"
        assert len(decisions) == 1, "Must create 1 decision_made insight"
        assert len(risks) == 1, "Must create 1 risk insight"
        assert len(key_takeaways) == 2, "Must create 2 key_takeaway insights"
        assert len(product_feedback) == 1, "Must create 1 product_feedback insight"
        assert len(market_intel) == 1, "Must create 1 market_intelligence insight"
    
    @pytest.mark.asyncio
    async def test_product_feedback_persists_with_correct_type(self, service, mock_persona):
        """Test that product_feedback persists with InsightType.product_feedback."""
        analysis = InteractionAnalysis(
            summaries=Summaries(
                title="Test", headline="Test", brief="Test",
                detailed="Test", spotlight="Test"
            ),
            product_feedback=[
                ProductFeedback(text="Feature request 1"),
                ProductFeedback(text="Bug report 2")
            ]
        )
        
        added_models = []
        
        mock_session = AsyncMock()
        mock_session.add = MagicMock(side_effect=lambda m: added_models.append(m))
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_persona)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        
        with patch('services.intelligence_service.get_async_session', return_value=mock_context):
            await service._persist_intelligence(
                analysis=analysis,
                interaction_id=str(uuid4()),
                tenant_id=str(uuid4()),
                trace_id=str(uuid4()),
                persona_code="gtm",
                interaction_type="meeting",
                account_id=None,
                interaction_timestamp=datetime.utcnow()
            )
        
        insights = [m for m in added_models if isinstance(m, InteractionInsightModel)]
        product_feedback_insights = [i for i in insights if i.type == InsightTypeEnum.product_feedback]
        
        assert len(product_feedback_insights) == 2, \
            "Product feedback must persist with InsightType.product_feedback"
        
        key_takeaway_insights = [i for i in insights if i.type == InsightTypeEnum.key_takeaway]
        assert len(key_takeaway_insights) == 0, \
            "Product feedback must NOT be mapped to key_takeaway"
    
    @pytest.mark.asyncio
    async def test_market_intelligence_persists_with_correct_type(self, service, mock_persona):
        """Test that market_intelligence persists with InsightType.market_intelligence."""
        analysis = InteractionAnalysis(
            summaries=Summaries(
                title="Test", headline="Test", brief="Test",
                detailed="Test", spotlight="Test"
            ),
            market_intelligence=[
                MarketIntelligence(text="Competitor analysis"),
                MarketIntelligence(text="Market trend")
            ]
        )
        
        added_models = []
        
        mock_session = AsyncMock()
        mock_session.add = MagicMock(side_effect=lambda m: added_models.append(m))
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_persona)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        
        with patch('services.intelligence_service.get_async_session', return_value=mock_context):
            await service._persist_intelligence(
                analysis=analysis,
                interaction_id=str(uuid4()),
                tenant_id=str(uuid4()),
                trace_id=str(uuid4()),
                persona_code="gtm",
                interaction_type="meeting",
                account_id=None,
                interaction_timestamp=datetime.utcnow()
            )
        
        insights = [m for m in added_models if isinstance(m, InteractionInsightModel)]
        market_intel_insights = [i for i in insights if i.type == InsightTypeEnum.market_intelligence]
        
        assert len(market_intel_insights) == 2, \
            "Market intelligence must persist with InsightType.market_intelligence"
        
        key_takeaway_insights = [i for i in insights if i.type == InsightTypeEnum.key_takeaway]
        assert len(key_takeaway_insights) == 0, \
            "Market intelligence must NOT be mapped to key_takeaway"
    
    @pytest.mark.asyncio
    async def test_transaction_rollback_on_failure(self, service, sample_analysis, mock_persona):
        """Test that transaction rolls back on failure."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock(side_effect=Exception("DB Error"))
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_persona)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        
        with patch('services.intelligence_service.get_async_session', return_value=mock_context):
            with pytest.raises(Exception):
                await service._persist_intelligence(
                    analysis=sample_analysis,
                    interaction_id=str(uuid4()),
                    tenant_id=str(uuid4()),
                    trace_id=str(uuid4()),
                    persona_code="gtm",
                    interaction_type="meeting",
                    account_id=None,
                    interaction_timestamp=datetime.utcnow()
                )
        
        mock_session.rollback.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_content_hash_idempotency(self, service, mock_persona):
        """Test that content_hash is set for idempotency."""
        analysis = InteractionAnalysis(
            summaries=Summaries(
                title="Test", headline="Test", brief="Test",
                detailed="Test", spotlight="Test"
            ),
            action_items=[ActionItem(description="Test action")]
        )
        
        added_models = []
        
        mock_session = AsyncMock()
        mock_session.add = MagicMock(side_effect=lambda m: added_models.append(m))
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_persona)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        
        with patch('services.intelligence_service.get_async_session', return_value=mock_context):
            await service._persist_intelligence(
                analysis=analysis,
                interaction_id=str(uuid4()),
                tenant_id=str(uuid4()),
                trace_id=str(uuid4()),
                persona_code="gtm",
                interaction_type="meeting",
                account_id=None,
                interaction_timestamp=datetime.utcnow()
            )
        
        insights = [m for m in added_models if isinstance(m, InteractionInsightModel)]
        
        for insight in insights:
            assert insight.content_hash is not None, "content_hash must be set"
            assert len(insight.content_hash) == 64, "content_hash must be SHA-256 (64 chars)"
    
    @pytest.mark.asyncio
    async def test_profile_type_defaults_to_rich(self, service, sample_analysis, mock_persona):
        """Test that profile_type defaults to 'rich' for all summary entries."""
        added_models = []
        
        mock_session = AsyncMock()
        mock_session.add = MagicMock(side_effect=lambda m: added_models.append(m))
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_persona)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        
        with patch('services.intelligence_service.get_async_session', return_value=mock_context):
            await service._persist_intelligence(
                analysis=sample_analysis,
                interaction_id=str(uuid4()),
                tenant_id=str(uuid4()),
                trace_id=str(uuid4()),
                persona_code="gtm",
                interaction_type="meeting",
                account_id=None,
                interaction_timestamp=datetime.utcnow()
            )
        
        summary_entries = [m for m in added_models if isinstance(m, InteractionSummaryEntryModel)]
        
        for entry in summary_entries:
            assert entry.profile_type == ProfileTypeEnum.rich, \
                "profile_type must default to 'rich'"


class TestPersonaLookup:
    """Tests for _get_persona_id method."""
    
    @pytest.mark.asyncio
    async def test_persona_lookup_success(self, service, mock_persona):
        """Test successful persona lookup."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_persona)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        result = await service._get_persona_id(mock_session, "gtm")
        
        assert result == mock_persona.id
    
    @pytest.mark.asyncio
    async def test_persona_lookup_not_found_raises_error(self, service):
        """Test that persona lookup raises ValueError when not found."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)
        
        with pytest.raises(ValueError, match="Persona 'unknown' not found"):
            await service._get_persona_id(mock_session, "unknown")
