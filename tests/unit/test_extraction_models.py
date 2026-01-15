"""
Unit Tests for Extraction Models

Feature: intelligence-layer-integration, Task 15
Tests the Pydantic extraction models used for LLM intelligence extraction.
"""

import pytest
from datetime import date
from pydantic import ValidationError

from models.extraction_models import (
    RiskSeverityEnum,
    Summaries,
    ActionItem,
    Decision,
    Risk,
    ProductFeedback,
    MarketIntelligence,
    InteractionAnalysis,
)


class TestSummariesModel:
    """Tests for the Summaries model."""
    
    def test_summaries_with_all_fields(self):
        """Test Summaries model with all 5 required fields."""
        summaries = Summaries(
            title="AWS Migration Discussion",
            headline="Team discussed migrating to AWS with focus on cost optimization.",
            brief="The meeting covered AWS migration strategies including EC2, RDS, and Lambda options. Key decisions were made about timeline and budget.",
            detailed="Comprehensive discussion about AWS migration covering infrastructure, security, compliance, and cost considerations. Multiple stakeholders provided input on timeline expectations.",
            spotlight="Cost neutrality achieved through service credits"
        )
        
        assert summaries.title == "AWS Migration Discussion"
        assert summaries.headline.startswith("Team discussed")
        assert "migration" in summaries.brief.lower()
        assert len(summaries.detailed) > len(summaries.brief)
        assert summaries.spotlight == "Cost neutrality achieved through service credits"
    
    def test_summaries_rejects_missing_fields(self):
        """Test that Summaries rejects missing required fields."""
        with pytest.raises(ValidationError):
            Summaries(
                title="Test",
                headline="Test headline"
                # Missing brief, detailed, spotlight
            )
    
    def test_summaries_accepts_empty_strings(self):
        """Test that Summaries accepts empty strings (valid but not recommended)."""
        summaries = Summaries(
            title="",
            headline="",
            brief="",
            detailed="",
            spotlight=""
        )
        assert summaries.title == ""


class TestActionItemModel:
    """Tests for the ActionItem model."""
    
    def test_action_item_with_all_fields(self):
        """Test ActionItem with description, owner, and due_date."""
        action = ActionItem(
            description="Schedule follow-up meeting with AWS team",
            owner="John Smith",
            due_date=date(2026, 1, 20)
        )
        
        assert action.description == "Schedule follow-up meeting with AWS team"
        assert action.owner == "John Smith"
        assert action.due_date == date(2026, 1, 20)
    
    def test_action_item_with_only_description(self):
        """Test ActionItem with only required description field."""
        action = ActionItem(description="Review migration plan")
        
        assert action.description == "Review migration plan"
        assert action.owner is None
        assert action.due_date is None
    
    def test_action_item_rejects_missing_description(self):
        """Test that ActionItem rejects missing description."""
        with pytest.raises(ValidationError):
            ActionItem(owner="John")


class TestDecisionModel:
    """Tests for the Decision model."""
    
    def test_decision_with_rationale(self):
        """Test Decision with decision and rationale."""
        decision = Decision(
            decision="Proceed with AWS migration in Q2",
            rationale="Cost analysis shows 30% savings over current infrastructure"
        )
        
        assert decision.decision == "Proceed with AWS migration in Q2"
        assert "30% savings" in decision.rationale
    
    def test_decision_without_rationale(self):
        """Test Decision with only decision field."""
        decision = Decision(decision="Use PostgreSQL for the database")
        
        assert decision.decision == "Use PostgreSQL for the database"
        assert decision.rationale is None


class TestRiskModel:
    """Tests for the Risk model."""
    
    def test_risk_with_all_fields(self):
        """Test Risk with risk, severity, and mitigation."""
        risk = Risk(
            risk="Potential data loss during migration",
            severity=RiskSeverityEnum.high,
            mitigation="Implement comprehensive backup strategy before migration"
        )
        
        assert risk.risk == "Potential data loss during migration"
        assert risk.severity == RiskSeverityEnum.high
        assert "backup" in risk.mitigation.lower()
    
    def test_risk_severity_enum_values(self):
        """Test all RiskSeverityEnum values."""
        low_risk = Risk(risk="Minor UI inconsistency", severity=RiskSeverityEnum.low)
        medium_risk = Risk(risk="Timeline delay possible", severity=RiskSeverityEnum.medium)
        high_risk = Risk(risk="Security vulnerability", severity=RiskSeverityEnum.high)
        
        assert low_risk.severity.value == "low"
        assert medium_risk.severity.value == "medium"
        assert high_risk.severity.value == "high"
    
    def test_risk_rejects_invalid_severity(self):
        """Test that Risk rejects invalid severity values."""
        with pytest.raises(ValidationError):
            Risk(risk="Some risk", severity="critical")  # Invalid enum value
    
    def test_risk_without_mitigation(self):
        """Test Risk without optional mitigation."""
        risk = Risk(risk="Budget overrun possible", severity=RiskSeverityEnum.medium)
        
        assert risk.mitigation is None


class TestProductFeedbackModel:
    """Tests for the ProductFeedback model."""
    
    def test_product_feedback_creation(self):
        """Test ProductFeedback with text field."""
        feedback = ProductFeedback(text="Need better export functionality for reports")
        
        assert feedback.text == "Need better export functionality for reports"
    
    def test_product_feedback_rejects_missing_text(self):
        """Test that ProductFeedback rejects missing text."""
        with pytest.raises(ValidationError):
            ProductFeedback()


class TestMarketIntelligenceModel:
    """Tests for the MarketIntelligence model."""
    
    def test_market_intelligence_creation(self):
        """Test MarketIntelligence with text field."""
        intel = MarketIntelligence(text="Competitor X launched similar feature last month")
        
        assert intel.text == "Competitor X launched similar feature last month"
    
    def test_market_intelligence_rejects_missing_text(self):
        """Test that MarketIntelligence rejects missing text."""
        with pytest.raises(ValidationError):
            MarketIntelligence()


class TestInteractionAnalysisModel:
    """Tests for the InteractionAnalysis model."""
    
    def test_interaction_analysis_with_all_fields(self):
        """Test InteractionAnalysis with all nested models populated."""
        analysis = InteractionAnalysis(
            summaries=Summaries(
                title="Q1 Planning Meeting",
                headline="Team aligned on Q1 priorities",
                brief="Discussed roadmap and resource allocation",
                detailed="Full discussion of Q1 goals, milestones, and team assignments",
                spotlight="Budget approved for new hires"
            ),
            action_items=[
                ActionItem(description="Draft hiring plan", owner="HR Lead"),
                ActionItem(description="Update roadmap document")
            ],
            decisions=[
                Decision(decision="Hire 3 engineers in Q1", rationale="Support growth targets")
            ],
            risks=[
                Risk(risk="Hiring timeline may slip", severity=RiskSeverityEnum.medium)
            ],
            key_takeaways=["Team is aligned on priorities", "Budget is approved"],
            product_feedback=[
                ProductFeedback(text="Users want dark mode")
            ],
            market_intelligence=[
                MarketIntelligence(text="Competitor raised Series B")
            ]
        )
        
        assert analysis.summaries.title == "Q1 Planning Meeting"
        assert len(analysis.action_items) == 2
        assert len(analysis.decisions) == 1
        assert len(analysis.risks) == 1
        assert len(analysis.key_takeaways) == 2
        assert len(analysis.product_feedback) == 1
        assert len(analysis.market_intelligence) == 1
    
    def test_interaction_analysis_with_empty_lists(self):
        """Test InteractionAnalysis with only summaries (empty lists for others)."""
        analysis = InteractionAnalysis(
            summaries=Summaries(
                title="Brief Update",
                headline="Quick status update",
                brief="Short meeting",
                detailed="Detailed notes",
                spotlight="All on track"
            )
        )
        
        assert analysis.summaries.title == "Brief Update"
        assert analysis.action_items == []
        assert analysis.decisions == []
        assert analysis.risks == []
        assert analysis.key_takeaways == []
        assert analysis.product_feedback == []
        assert analysis.market_intelligence == []
    
    def test_interaction_analysis_serialization(self):
        """Test InteractionAnalysis serialization to JSON."""
        analysis = InteractionAnalysis(
            summaries=Summaries(
                title="Test",
                headline="Test headline",
                brief="Test brief",
                detailed="Test detailed",
                spotlight="Test spotlight"
            ),
            action_items=[ActionItem(description="Test action")],
            key_takeaways=["Key insight"]
        )
        
        # Serialize to JSON
        json_str = analysis.model_dump_json()
        
        # Deserialize back
        restored = InteractionAnalysis.model_validate_json(json_str)
        
        assert restored.summaries.title == analysis.summaries.title
        assert len(restored.action_items) == 1
        assert restored.action_items[0].description == "Test action"
        assert restored.key_takeaways == ["Key insight"]
    
    def test_interaction_analysis_rejects_missing_summaries(self):
        """Test that InteractionAnalysis rejects missing summaries."""
        with pytest.raises(ValidationError):
            InteractionAnalysis(
                action_items=[ActionItem(description="Test")]
                # Missing required summaries
            )
