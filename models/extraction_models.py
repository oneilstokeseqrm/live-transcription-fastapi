"""Pydantic models for LLM extraction using instructor.

These models define the structure for extracting intelligence from transcripts.
They are used with the instructor library to ensure structured LLM outputs.
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date
from enum import Enum


class RiskSeverityEnum(str, Enum):
    """Severity levels for identified risks."""
    low = "low"
    medium = "medium"
    high = "high"


class Summaries(BaseModel):
    """Multi-level summaries of the interaction."""
    title: str = Field(
        description="5-10 word title capturing the essence of the interaction"
    )
    headline: str = Field(
        description="1-2 sentence headline summary for quick scanning"
    )
    brief: str = Field(
        description="2-3 paragraph executive summary covering key points"
    )
    detailed: str = Field(
        description="Comprehensive summary with all key points and context"
    )
    spotlight: str = Field(
        description="Key highlight or most important takeaway from the interaction"
    )


class ActionItem(BaseModel):
    """An actionable task extracted from the transcript."""
    description: str = Field(
        description="Clear description of the action item or task to be completed"
    )
    owner: Optional[str] = Field(
        default=None,
        description="Person responsible for the action item, if mentioned"
    )
    due_date: Optional[date] = Field(
        default=None,
        description="Due date for the action item, if mentioned"
    )


class Decision(BaseModel):
    """A decision made during the interaction."""
    decision: str = Field(
        description="The decision that was made during the interaction"
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Reasoning or justification behind the decision, if provided"
    )


class Risk(BaseModel):
    """A risk identified in the interaction."""
    risk: str = Field(
        description="Description of the risk or concern identified"
    )
    severity: RiskSeverityEnum = Field(
        description="Severity level of the risk: low, medium, or high"
    )
    mitigation: Optional[str] = Field(
        default=None,
        description="Suggested mitigation strategy or next steps, if mentioned"
    )


class ProductFeedback(BaseModel):
    """Product-related feedback from the interaction."""
    text: str = Field(
        description="Feature request, pain point, bug report, or UX friction mentioned"
    )


class MarketIntelligence(BaseModel):
    """Market intelligence extracted from the interaction."""
    text: str = Field(
        description="Competitor mention, market trend, or macro-economic theme discussed"
    )


class InteractionAnalysis(BaseModel):
    """Complete structured analysis of an interaction transcript.
    
    This model represents the full extraction output from the LLM,
    containing multi-level summaries and categorized insights.
    """
    summaries: Summaries = Field(
        description="Multi-level summaries at different granularities"
    )
    action_items: List[ActionItem] = Field(
        default_factory=list,
        description="Actionable tasks and follow-ups identified in the interaction"
    )
    decisions: List[Decision] = Field(
        default_factory=list,
        description="Decisions made or agreements reached during the interaction"
    )
    risks: List[Risk] = Field(
        default_factory=list,
        description="Risks, concerns, or potential issues identified"
    )
    key_takeaways: List[str] = Field(
        default_factory=list,
        description="Important insights and learnings from the interaction"
    )
    product_feedback: List[ProductFeedback] = Field(
        default_factory=list,
        description="Product-related feedback including feature requests and pain points"
    )
    market_intelligence: List[MarketIntelligence] = Field(
        default_factory=list,
        description="Market insights including competitor mentions and industry trends"
    )
