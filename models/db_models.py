"""SQLModel table definitions mirroring the existing Postgres schema.

These models use the Mirror Pattern - they exactly match existing Postgres tables
without running migrations. They are used for persisting intelligence data.
"""
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, Text, Enum as SAEnum
from typing import Optional
from datetime import datetime
from uuid import UUID, uuid4
import enum


# --- Enums matching Prisma schema ---

class SummaryLevelEnum(str, enum.Enum):
    """Summary granularity levels."""
    title = "title"
    headline = "headline"
    brief = "brief"
    detailed = "detailed"
    spotlight = "spotlight"
    unknown = "unknown"


class ProfileTypeEnum(str, enum.Enum):
    """Profile type for summaries."""
    rich = "rich"
    lite = "lite"


class InsightTypeEnum(str, enum.Enum):
    """Types of insights that can be extracted."""
    action_item = "action_item"
    key_takeaway = "key_takeaway"
    decision_made = "decision_made"
    risk = "risk"
    product_feedback = "product_feedback"
    market_intelligence = "market_intelligence"
    unknown = "unknown"


class RiskSeverityDBEnum(str, enum.Enum):
    """Severity levels for risks in database."""
    low = "low"
    medium = "medium"
    high = "high"
    unknown = "unknown"


# --- Table Models ---

class PersonaModel(SQLModel, table=True):
    """Mirror of personas table for persona lookup."""
    __tablename__ = "personas"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    code: str = Field(unique=True)
    label: str
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    is_active: bool = Field(default=True, sa_column_kwargs={"name": "is_active"})
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs={"name": "created_at"}
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs={"name": "updated_at"}
    )


class InteractionSummaryEntryModel(SQLModel, table=True):
    """Mirror of interaction_summary_entries table.
    
    Stores normalized 5-level summaries per persona for each interaction.
    """
    __tablename__ = "interaction_summary_entries"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(sa_column_kwargs={"name": "tenant_id"})
    interaction_id: UUID = Field(sa_column_kwargs={"name": "interaction_id"})
    persona_id: UUID = Field(sa_column_kwargs={"name": "persona_id"})
    level: SummaryLevelEnum = Field(sa_column=Column(SAEnum(SummaryLevelEnum, name="SummaryLevel")))
    text: str = Field(sa_column=Column(Text))
    word_count: Optional[int] = Field(default=None, sa_column_kwargs={"name": "word_count"})
    profile_type: ProfileTypeEnum = Field(
        default=ProfileTypeEnum.rich,
        sa_column=Column(SAEnum(ProfileTypeEnum, name="ProfileType"), name="profile_type")
    )
    source: Optional[str] = Field(default=None)
    trace_id: UUID = Field(sa_column_kwargs={"name": "trace_id"})
    interaction_type: str = Field(sa_column_kwargs={"name": "interaction_type"})
    account_id: Optional[UUID] = Field(default=None, sa_column_kwargs={"name": "account_id"})
    interaction_timestamp: datetime = Field(sa_column_kwargs={"name": "interaction_timestamp"})
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs={"name": "created_at"}
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs={"name": "updated_at"}
    )


class InteractionInsightModel(SQLModel, table=True):
    """Mirror of interaction_insights table.
    
    Stores typed insights (action_items, key_takeaways, decisions, risks,
    product_feedback, market_intelligence) extracted from interactions.
    """
    __tablename__ = "interaction_insights"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(sa_column_kwargs={"name": "tenant_id"})
    interaction_id: UUID = Field(sa_column_kwargs={"name": "interaction_id"})
    persona_id: UUID = Field(sa_column_kwargs={"name": "persona_id"})
    type: InsightTypeEnum = Field(sa_column=Column(SAEnum(InsightTypeEnum, name="InsightType")))
    
    # Typed columns (nullable, populated based on type)
    description: Optional[str] = Field(default=None, sa_column=Column(Text))  # For action_item
    owner: Optional[str] = Field(default=None, sa_column=Column(Text))  # For action_item
    due_date: Optional[datetime] = Field(default=None, sa_column_kwargs={"name": "due_date"})  # For action_item
    text: Optional[str] = Field(default=None, sa_column=Column(Text))  # For key_takeaway, product_feedback, market_intelligence
    decision: Optional[str] = Field(default=None, sa_column=Column(Text))  # For decision_made
    rationale: Optional[str] = Field(default=None, sa_column=Column(Text))  # For decision_made
    risk: Optional[str] = Field(default=None, sa_column=Column(Text))  # For risk
    severity: Optional[RiskSeverityDBEnum] = Field(
        default=None,
        sa_column=Column(SAEnum(RiskSeverityDBEnum, name="RiskSeverity"))
    )  # For risk
    mitigation: Optional[str] = Field(default=None, sa_column=Column(Text))  # For risk
    
    # Idempotency
    content_hash: str = Field(sa_column=Column(Text, name="content_hash"))
    
    # Metadata
    trace_id: UUID = Field(sa_column_kwargs={"name": "trace_id"})
    interaction_type: str = Field(sa_column_kwargs={"name": "interaction_type"})
    account_id: Optional[UUID] = Field(default=None, sa_column_kwargs={"name": "account_id"})
    interaction_timestamp: datetime = Field(sa_column_kwargs={"name": "interaction_timestamp"})
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs={"name": "created_at"}
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs={"name": "updated_at"}
    )
