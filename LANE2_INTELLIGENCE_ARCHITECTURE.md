# Lane 2 Intelligence System Architecture

> A comprehensive technical guide to the async intelligence extraction layer built with instructor, SQLModel, and GPT-4o.

---

## Table of Contents

1. [Executive Overview](#1-executive-overview)
2. [Core Components](#2-core-components)
3. [Instructor Library Integration](#3-instructor-library-integration)
4. [LLM System Prompt Design](#4-llm-system-prompt-design)
5. [Async Fork Pattern](#5-async-fork-pattern)
6. [Database Persistence](#6-database-persistence)
7. [Error Handling Patterns](#7-error-handling-patterns)
8. [Configuration & Dependencies](#8-configuration--dependencies)
9. [Replication Guide](#9-replication-guide)

---

## 1. Executive Overview

### Purpose

Lane 2 is the **Intelligence Extraction Layer** in a dual-lane async architecture. While Lane 1 handles event publishing (Kinesis/EventBridge), Lane 2 extracts structured intelligence from transcripts using LLM and persists it to Postgres.

### High-Level Data Flow

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         Cleaned Transcript                                │
│                              (input)                                      │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                    asyncio.gather(return_exceptions=True)
                                 │
                 ┌───────────────┴───────────────┐
                 │                               │
          ┌──────▼──────┐               ┌───────▼───────┐
          │   Lane 1    │               │    Lane 2     │
          │  Publishing │               │ Intelligence  │
          │             │               │               │
          │ - Kinesis   │               │ - GPT-4o      │
          │ - EventBrdg │               │ - instructor  │
          └──────┬──────┘               │ - Postgres    │
                 │                      └───────┬───────┘
                 │                              │
                 │    ┌─────────────────────────┘
                 │    │
                 │    ▼
                 │  ┌────────────────────────────────────────┐
                 │  │           IntelligenceService          │
                 │  │                                        │
                 │  │  1. _extract_intelligence()            │
                 │  │     └─► instructor + GPT-4o            │
                 │  │     └─► Returns InteractionAnalysis    │
                 │  │                                        │
                 │  │  2. _persist_intelligence()            │
                 │  │     └─► SQLModel + asyncpg             │
                 │  │     └─► 5 summaries + N insights       │
                 │  └────────────────────────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                        Response returned to client                         │
│                    (Lane errors logged but don't fail)                     │
└────────────────────────────────────────────────────────────────────────────┘
```

### Key Technologies

| Component | Technology | Purpose |
|-----------|------------|---------|
| LLM Client | `instructor` | Structured output enforcement |
| LLM Model | GPT-4o (configurable) | Intelligence extraction |
| ORM | SQLModel | Async database operations |
| Database | Postgres (Neon) + asyncpg | Persistence layer |
| Async | Python asyncio | Concurrent lane execution |

---

## 2. Core Components

### 2.1 IntelligenceService (`services/intelligence_service.py`)

The service follows a **three-method architecture** for clean separation of concerns:

```python
class IntelligenceService:
    """Service for extracting and persisting structured intelligence from transcripts.

    This service implements Lane 2 of the Async Fork pattern, running concurrently
    with the existing event publishing flow (Lane 1).
    """

    def __init__(self):
        """Initialize instructor client with async OpenAI."""
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")

        # Create async instructor client using from_provider
        self.client = instructor.from_provider(
            f"openai/{self.model}",
            async_client=True,
        )
```

#### Method 1: `process_transcript()` - Orchestrator

The main entry point that coordinates extraction and persistence:

```python
async def process_transcript(
    self,
    cleaned_transcript: str,
    interaction_id: str,
    tenant_id: str,
    trace_id: str,
    interaction_type: str = "meeting",
    account_id: Optional[str] = None,
    interaction_timestamp: Optional[datetime] = None,
    persona_code: str = "gtm"
) -> Optional[InteractionAnalysis]:
    """Extract intelligence and persist to database.

    Returns:
        InteractionAnalysis if successful, None on any failure.
    """
    try:
        # Step 1: Extract intelligence using LLM
        analysis = await self._extract_intelligence(cleaned_transcript)

        if analysis is None:
            return None

        # Step 2: Persist to database
        await self._persist_intelligence(
            analysis=analysis,
            interaction_id=interaction_id,
            tenant_id=tenant_id,
            trace_id=trace_id,
            persona_code=persona_code,
            interaction_type=interaction_type,
            account_id=account_id,
            interaction_timestamp=interaction_timestamp or datetime.utcnow()
        )

        return analysis

    except Exception as e:
        logger.error(f"Intelligence processing failed: {str(e)}", exc_info=True)
        return None  # Never raise - return None on any failure
```

#### Method 2: `_extract_intelligence()` - LLM Extraction

Uses instructor for structured output:

```python
async def _extract_intelligence(
    self,
    cleaned_transcript: str
) -> Optional[InteractionAnalysis]:
    """Use instructor to extract structured data from transcript."""
    try:
        result = await self.client.create(
            response_model=InteractionAnalysis,
            messages=[
                {"role": "system", "content": self._get_system_prompt()},
                {"role": "user", "content": f"Analyze this transcript:\n\n{cleaned_transcript}"}
            ],
            max_retries=2
        )
        return result

    except Exception as e:
        logger.error(f"Intelligence extraction failed: {e}", exc_info=True)
        return None
```

#### Method 3: `_persist_intelligence()` - Database Persistence

Single-transaction atomic writes to Postgres:

```python
async def _persist_intelligence(
    self,
    analysis: InteractionAnalysis,
    interaction_id: str,
    tenant_id: str,
    # ... additional params
) -> None:
    """Persist summaries and insights to Postgres in a single transaction."""
    async with get_async_session() as session:
        try:
            # Create 5 summary entries (one per level)
            # Create N insight entries (action_items, decisions, risks, etc.)
            await session.commit()
        except Exception as e:
            await session.rollback()
            raise  # Re-raise to signal failure
```

### 2.2 Extraction Models (`models/extraction_models.py`)

Pydantic models with `Field(description=...)` guide the LLM's structured output:

```python
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
```

### 2.3 Database Models (`models/db_models.py`)

SQLModel tables mirror the existing Postgres schema (no migrations needed):

```python
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


class RiskSeverityDBEnum(str, enum.Enum):
    """Severity levels for risks in database."""
    low = "low"
    medium = "medium"
    high = "high"


# --- Table Models ---

class PersonaModel(SQLModel, table=True):
    """Mirror of personas table for persona lookup."""
    __tablename__ = "personas"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    code: str = Field(unique=True)
    label: str
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class InteractionSummaryEntryModel(SQLModel, table=True):
    """Stores normalized 5-level summaries per persona for each interaction."""
    __tablename__ = "interaction_summary_entries"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID
    interaction_id: UUID
    persona_id: UUID
    level: SummaryLevelEnum = Field(sa_column=Column(SAEnum(SummaryLevelEnum, name="SummaryLevel")))
    text: str = Field(sa_column=Column(Text))
    word_count: Optional[int] = Field(default=None)
    profile_type: ProfileTypeEnum = Field(default=ProfileTypeEnum.rich)
    source: Optional[str] = Field(default=None)
    trace_id: UUID
    interaction_type: str
    account_id: Optional[UUID] = Field(default=None)
    interaction_timestamp: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class InteractionInsightModel(SQLModel, table=True):
    """Polymorphic insight table - stores all insight types with typed columns."""
    __tablename__ = "interaction_insights"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID
    interaction_id: UUID
    persona_id: UUID
    type: InsightTypeEnum = Field(sa_column=Column(SAEnum(InsightTypeEnum, name="InsightType")))

    # Typed columns (nullable, populated based on type)
    description: Optional[str] = Field(default=None, sa_column=Column(Text))  # action_item
    owner: Optional[str] = Field(default=None, sa_column=Column(Text))        # action_item
    due_date: Optional[datetime] = Field(default=None)                         # action_item
    text: Optional[str] = Field(default=None, sa_column=Column(Text))          # key_takeaway, product_feedback, market_intelligence
    decision: Optional[str] = Field(default=None, sa_column=Column(Text))      # decision_made
    rationale: Optional[str] = Field(default=None, sa_column=Column(Text))     # decision_made
    risk: Optional[str] = Field(default=None, sa_column=Column(Text))          # risk
    severity: Optional[RiskSeverityDBEnum] = Field(default=None)               # risk
    mitigation: Optional[str] = Field(default=None, sa_column=Column(Text))    # risk

    # Idempotency
    content_hash: str = Field(sa_column=Column(Text))

    # Metadata
    trace_id: UUID
    interaction_type: str
    account_id: Optional[UUID] = Field(default=None)
    interaction_timestamp: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

---

## 3. Instructor Library Integration

### `from_provider()` Pattern

The modern instructor pattern uses `from_provider()` for simplified initialization:

```python
import instructor

class IntelligenceService:
    def __init__(self):
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")

        # Key: from_provider() with async_client=True
        self.client = instructor.from_provider(
            f"openai/{self.model}",  # Provider/model string format
            async_client=True,        # Enable async support
        )
```

### Async Client Configuration

The `async_client=True` flag is critical for non-blocking LLM calls:

```python
# This is async and non-blocking
result = await self.client.create(
    response_model=InteractionAnalysis,
    messages=[...],
    max_retries=2
)
```

### Structured Output Enforcement

Instructor uses the Pydantic model's JSON schema to:
1. Generate a function-calling schema for the LLM
2. Validate the response against the schema
3. Auto-retry on validation failures (up to `max_retries`)

```python
result = await self.client.create(
    response_model=InteractionAnalysis,  # Pydantic model = schema
    messages=[
        {"role": "system", "content": self._get_system_prompt()},
        {"role": "user", "content": f"Analyze this transcript:\n\n{cleaned_transcript}"}
    ],
    max_retries=2  # Retry on validation failure
)
```

### Retry Strategy

The `max_retries=2` configuration handles:
- JSON parsing errors
- Schema validation failures
- Truncated responses

---

## 4. LLM System Prompt Design

### Complete GTM-Focused Prompt

```python
def _get_system_prompt(self) -> str:
    """GTM-focused system prompt for intelligence extraction."""
    return """You are an expert Go-To-Market (GTM) analyst reviewing customer interaction transcripts.

Your role is to extract actionable intelligence that helps GTM teams:
- Identify sales opportunities and deal risks
- Track customer commitments and action items
- Capture competitive intelligence and market signals
- Surface product feedback for roadmap prioritization

**Extraction Guidelines:**

1. **Summaries**: Write from a GTM leader's perspective, focusing on business impact
   - title: 5-10 word title capturing the essence
   - headline: 1-2 sentence headline for quick scanning
   - brief: 2-3 paragraph executive summary
   - detailed: Comprehensive summary with all key points
   - spotlight: The single most important takeaway

2. **Action Items**: Capture commitments, follow-ups, and next steps with owners when mentioned

3. **Decisions**: Document any agreements, approvals, or strategic choices made

4. **Risks**: Identify deal risks, relationship concerns, or competitive threats with severity levels

5. **Key Takeaways**: Highlight insights valuable for account strategy

6. **Product Feedback**: Note feature requests, pain points, bugs, or UX issues mentioned

7. **Market Intelligence**: Capture competitor mentions, market trends, or industry themes

Be thorough but precise. Only extract information explicitly present in the transcript.
Do not invent or assume information not stated."""
```

### Prompt Structure

| Section | Purpose |
|---------|---------|
| Role Definition | Sets the persona (GTM analyst) |
| Goals | Lists what to extract and why |
| Extraction Guidelines | Maps to Pydantic model fields |
| Constraints | "Only extract what's present" |

### Customization Strategy

To adapt for different domains, modify:

1. **Role Definition**: Change "GTM analyst" to domain expert
2. **Goals**: Update extraction objectives
3. **Guidelines**: Align with your Pydantic model fields
4. **Constraints**: Adjust based on accuracy requirements

---

## 5. Async Fork Pattern

### Architecture Overview

The async fork runs Lane 1 (publishing) and Lane 2 (intelligence) concurrently:

```python
# Execute both lanes concurrently with error isolation
results = await asyncio.gather(
    _lane1_publish(),
    _lane2_intelligence(),
    return_exceptions=True  # Critical: convert exceptions to return values
)
```

### Error Isolation with `return_exceptions=True`

This flag ensures:
- Lane 1 failure doesn't block Lane 2
- Lane 2 failure doesn't block Lane 1
- Neither lane failure fails the HTTP response
- All errors are captured for logging

### Code Examples from All Three Invocation Points

#### Example 1: Batch Processing (`routers/batch.py:189-224`)

```python
async def _lane1_publish() -> Optional[dict]:
    """Lane 1: Publish envelope to Kinesis/EventBridge."""
    try:
        event_publisher = AWSEventPublisher()
        return await event_publisher.publish_envelope(envelope)
    except Exception as e:
        logger.error(
            f"Lane 1 (publishing) error: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}, error={e}"
        )
        raise

async def _lane2_intelligence() -> Optional[object]:
    """Lane 2: Extract and persist intelligence."""
    try:
        intelligence_service = IntelligenceService()
        return await intelligence_service.process_transcript(
            cleaned_transcript=cleaned_transcript,
            interaction_id=context.interaction_id,
            tenant_id=context.tenant_id,
            trace_id=context.trace_id,
            interaction_type="batch_upload"
        )
    except Exception as e:
        logger.error(
            f"Lane 2 (intelligence) error: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}, error={e}"
        )
        raise

# Execute both lanes concurrently with error isolation
results = await asyncio.gather(
    _lane1_publish(),
    _lane2_intelligence(),
    return_exceptions=True
)

# Log results without failing the request
for i, result in enumerate(results):
    if isinstance(result, Exception):
        lane_name = "Lane 1 (publishing)" if i == 0 else "Lane 2 (intelligence)"
        logger.error(
            f"{lane_name} failed (non-critical): processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}, "
            f"error={type(result).__name__}: {str(result)}",
            exc_info=result
        )
```

#### Example 2: Text Cleaning (`routers/text.py:103-156`)

```python
async def _lane1_publish() -> Optional[dict]:
    """Lane 1: Publish envelope to Kinesis/EventBridge."""
    try:
        publisher = AWSEventPublisher()
        return await publisher.publish_envelope(envelope)
    except Exception as e:
        logger.error(
            f"Lane 1 (publishing) error: interaction_id={context.interaction_id}, error={e}"
        )
        raise

async def _lane2_intelligence() -> Optional[object]:
    """Lane 2: Extract and persist intelligence."""
    try:
        intelligence_service = IntelligenceService()
        return await intelligence_service.process_transcript(
            cleaned_transcript=cleaned_text,
            interaction_id=context.interaction_id,
            tenant_id=context.tenant_id,
            trace_id=context.trace_id,
            interaction_type="note"
        )
    except Exception as e:
        logger.error(
            f"Lane 2 (intelligence) error: interaction_id={context.interaction_id}, error={e}"
        )
        raise

results = await asyncio.gather(
    _lane1_publish(),
    _lane2_intelligence(),
    return_exceptions=True
)
```

#### Example 3: WebSocket (`main.py:206-259`)

```python
async def _lane1_publish() -> Optional[dict]:
    """Lane 1: Publish envelope to Kinesis/EventBridge."""
    try:
        envelope = EnvelopeV1(
            tenant_id=uuid.UUID(tenant_id) if len(tenant_id) == 36 else uuid.uuid4(),
            user_id="websocket_user",
            interaction_type="meeting",
            content=ContentModel(text=meeting_output.cleaned_transcript, format="diarized"),
            timestamp=datetime.now(timezone.utc),
            source="websocket",
            extras={},
            interaction_id=uuid.UUID(session_id),
            trace_id=trace_id,
            account_id=None
        )
        aws_publisher = AWSEventPublisher()
        return await aws_publisher.publish_envelope(envelope)
    except Exception as e:
        logger.error(f"Lane 1 (publishing) error: session_id={session_id}, error={e}")
        raise

async def _lane2_intelligence() -> Optional[object]:
    """Lane 2: Extract and persist intelligence."""
    try:
        intelligence_service = IntelligenceService()
        return await intelligence_service.process_transcript(
            cleaned_transcript=meeting_output.cleaned_transcript,
            interaction_id=session_id,
            tenant_id=tenant_id,
            trace_id=trace_id,
            interaction_type="meeting"
        )
    except Exception as e:
        logger.error(f"Lane 2 (intelligence) error: session_id={session_id}, error={e}")
        raise

results = await asyncio.gather(
    _lane1_publish(),
    _lane2_intelligence(),
    return_exceptions=True
)
```

---

## 6. Database Persistence

### Session Management with Context Managers

```python
from services.database import get_async_session

async with get_async_session() as session:
    try:
        # All database operations here
        await session.commit()
    except Exception as e:
        await session.rollback()
        raise
```

### Async Session Factory (`services/database.py`)

```python
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for database sessions."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
        except Exception as e:
            await session.rollback()
            logger.error(f"Database session error: {e}", exc_info=True)
            raise
        finally:
            await session.close()
```

### Single-Transaction Atomic Writes

All summaries and insights are written in a single transaction:

```python
async def _persist_intelligence(self, analysis, ...):
    async with get_async_session() as session:
        try:
            # 1. Create 5 summary entries
            summary_mappings = [
                (SummaryLevelEnum.title, analysis.summaries.title),
                (SummaryLevelEnum.headline, analysis.summaries.headline),
                (SummaryLevelEnum.brief, analysis.summaries.brief),
                (SummaryLevelEnum.detailed, analysis.summaries.detailed),
                (SummaryLevelEnum.spotlight, analysis.summaries.spotlight),
            ]

            for level, text in summary_mappings:
                summary_entry = InteractionSummaryEntryModel(
                    tenant_id=tenant_uuid,
                    interaction_id=interaction_uuid,
                    persona_id=persona_id,
                    level=level,
                    text=text,
                    word_count=len(text.split()),
                    profile_type=ProfileTypeEnum.rich,
                    source=f"openai:{self.model}",
                    trace_id=trace_uuid,
                    interaction_type=interaction_type,
                    account_id=account_uuid,
                    interaction_timestamp=interaction_timestamp,
                )
                session.add(summary_entry)

            # 2. Create insight entries for each category
            for item in analysis.action_items:
                insight = InteractionInsightModel(
                    type=InsightTypeEnum.action_item,
                    description=item.description,
                    owner=item.owner,
                    due_date=datetime.combine(item.due_date, datetime.min.time()) if item.due_date else None,
                    content_hash=self._generate_content_hash("action_item", item.description),
                    # ... other fields
                )
                session.add(insight)

            # ... similar for decisions, risks, key_takeaways, etc.

            # Single commit for all
            await session.commit()

        except Exception as e:
            await session.rollback()
            raise
```

### Content Hashing for Idempotency

```python
def _generate_content_hash(self, insight_type: str, content: str) -> str:
    """Generate SHA-256 hash for insight deduplication."""
    hash_input = f"{insight_type}:{content}"
    return hashlib.sha256(hash_input.encode()).hexdigest()
```

### Persona Lookup Pattern

```python
async def _get_persona_id(self, session, persona_code: str = "gtm") -> UUID:
    """Look up persona UUID by code."""
    result = await session.execute(
        select(PersonaModel).where(PersonaModel.code == persona_code)
    )
    persona = result.scalar_one_or_none()

    if not persona:
        raise ValueError(f"Persona '{persona_code}' not found in database")

    return persona.id
```

---

## 7. Error Handling Patterns

### Pattern 1: LLM Extraction Errors (Return None)

```python
async def _extract_intelligence(self, cleaned_transcript: str) -> Optional[InteractionAnalysis]:
    try:
        result = await self.client.create(...)
        return result
    except Exception as e:
        logger.error(f"Intelligence extraction failed: {e}", exc_info=True)
        return None  # Never raise - caller handles None
```

**Philosophy**: LLM failures are expected (rate limits, timeouts). Return `None` and let the caller decide.

### Pattern 2: Database Persistence Errors (Rollback + Re-raise)

```python
async def _persist_intelligence(self, ...):
    async with get_async_session() as session:
        try:
            # ... add models
            await session.commit()
        except Exception as e:
            await session.rollback()  # Clean up partial state
            logger.error(f"Database persistence failed: {str(e)}", exc_info=True)
            raise  # Re-raise to signal failure
```

**Philosophy**: Database failures indicate data integrity issues. Rollback partial writes, then re-raise.

### Pattern 3: Main Processing Isolation

```python
async def process_transcript(self, ...) -> Optional[InteractionAnalysis]:
    try:
        analysis = await self._extract_intelligence(cleaned_transcript)

        if analysis is None:
            return None  # Extraction failed, don't persist

        await self._persist_intelligence(analysis, ...)
        return analysis

    except Exception as e:
        logger.error(f"Intelligence processing failed: {str(e)}", exc_info=True)
        return None  # Never raise from public method
```

**Philosophy**: The public method never raises. All failures convert to `None`.

### Pattern 4: Async Fork Isolation

```python
results = await asyncio.gather(
    _lane1_publish(),
    _lane2_intelligence(),
    return_exceptions=True  # Exceptions become return values
)

for i, result in enumerate(results):
    if isinstance(result, Exception):
        logger.error(f"Lane failed: {result}", exc_info=result)
    # Continue processing - don't fail the request
```

**Philosophy**: Lane failures are non-critical. Log them, but don't fail the user's request.

---

## 8. Configuration & Dependencies

### Required Environment Variables

```bash
# LLM Configuration
OPENAI_API_KEY=sk-...          # Required: OpenAI API key
OPENAI_MODEL=gpt-4o            # Optional: Model name (default: gpt-4o)

# Database Configuration
DATABASE_URL=postgresql://...   # Required: Postgres connection string (Neon)

# AWS (for Lane 1 publishing - optional)
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
```

### Python Dependencies

```
# requirements.txt

# LLM
instructor>=1.0.0     # Structured output from LLMs
openai>=1.0.0         # OpenAI client

# Database
sqlmodel>=0.0.14      # ORM with Pydantic integration
sqlalchemy>=2.0.0     # Async engine support
asyncpg>=0.29.0       # Async Postgres driver

# Web Framework
fastapi>=0.100.0      # API framework
uvicorn>=0.23.0       # ASGI server
pydantic>=2.0.0       # Data validation
```

### Database Schema Requirements

The following tables must exist in Postgres:

```sql
-- Persona lookup table
CREATE TABLE personas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR UNIQUE NOT NULL,
    label VARCHAR NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Insert default persona
INSERT INTO personas (code, label, description)
VALUES ('gtm', 'Go-To-Market', 'GTM-focused intelligence extraction');

-- Summary entries table
CREATE TABLE interaction_summary_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    interaction_id UUID NOT NULL,
    persona_id UUID NOT NULL REFERENCES personas(id),
    level VARCHAR NOT NULL,  -- title, headline, brief, detailed, spotlight
    text TEXT NOT NULL,
    word_count INTEGER,
    profile_type VARCHAR DEFAULT 'rich',
    source VARCHAR,
    trace_id UUID NOT NULL,
    interaction_type VARCHAR NOT NULL,
    account_id UUID,
    interaction_timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Insights table (polymorphic)
CREATE TABLE interaction_insights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    interaction_id UUID NOT NULL,
    persona_id UUID NOT NULL REFERENCES personas(id),
    type VARCHAR NOT NULL,  -- action_item, decision_made, risk, etc.

    -- Type-specific columns
    description TEXT,       -- action_item
    owner TEXT,             -- action_item
    due_date TIMESTAMP,     -- action_item
    text TEXT,              -- key_takeaway, product_feedback, market_intelligence
    decision TEXT,          -- decision_made
    rationale TEXT,         -- decision_made
    risk TEXT,              -- risk
    severity VARCHAR,       -- risk
    mitigation TEXT,        -- risk

    -- Idempotency
    content_hash TEXT NOT NULL,

    -- Metadata
    trace_id UUID NOT NULL,
    interaction_type VARCHAR NOT NULL,
    account_id UUID,
    interaction_timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_summaries_interaction ON interaction_summary_entries(interaction_id);
CREATE INDEX idx_summaries_tenant ON interaction_summary_entries(tenant_id);
CREATE INDEX idx_insights_interaction ON interaction_insights(interaction_id);
CREATE INDEX idx_insights_tenant ON interaction_insights(tenant_id);
CREATE INDEX idx_insights_type ON interaction_insights(type);
```

---

## 9. Replication Guide

### Step 1: Set Up Dependencies

```bash
pip install instructor openai sqlmodel sqlalchemy asyncpg
```

### Step 2: Define Your Extraction Models

Create your Pydantic models in `models/extraction_models.py`:

```python
from pydantic import BaseModel, Field
from typing import List, Optional

class YourInsight(BaseModel):
    """Define your insight type."""
    content: str = Field(description="Clear description for LLM guidance")
    category: Optional[str] = Field(default=None, description="Optional categorization")

class YourAnalysis(BaseModel):
    """Complete extraction output."""
    summary: str = Field(description="Brief summary of the content")
    insights: List[YourInsight] = Field(
        default_factory=list,
        description="List of extracted insights"
    )
```

### Step 3: Define Database Models

Create SQLModel tables in `models/db_models.py`:

```python
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, Text
from uuid import UUID, uuid4
from datetime import datetime

class YourInsightModel(SQLModel, table=True):
    __tablename__ = "your_insights"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    content: str = Field(sa_column=Column(Text))
    category: Optional[str] = Field(default=None)
    # ... add your metadata fields
```

### Step 4: Create Your Service

```python
import instructor
import os
from services.database import get_async_session
from models.extraction_models import YourAnalysis
from models.db_models import YourInsightModel

class YourIntelligenceService:
    def __init__(self):
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")
        self.client = instructor.from_provider(
            f"openai/{self.model}",
            async_client=True,
        )

    async def process(self, text: str) -> Optional[YourAnalysis]:
        try:
            analysis = await self._extract(text)
            if analysis:
                await self._persist(analysis)
            return analysis
        except Exception as e:
            logger.error(f"Processing failed: {e}")
            return None

    async def _extract(self, text: str) -> Optional[YourAnalysis]:
        try:
            return await self.client.create(
                response_model=YourAnalysis,
                messages=[
                    {"role": "system", "content": self._get_prompt()},
                    {"role": "user", "content": text}
                ],
                max_retries=2
            )
        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            return None

    def _get_prompt(self) -> str:
        return """Your domain-specific prompt here..."""

    async def _persist(self, analysis: YourAnalysis) -> None:
        async with get_async_session() as session:
            try:
                for insight in analysis.insights:
                    model = YourInsightModel(
                        content=insight.content,
                        category=insight.category,
                    )
                    session.add(model)
                await session.commit()
            except Exception as e:
                await session.rollback()
                raise
```

### Step 5: Integrate with Async Fork

```python
async def _lane2_your_intelligence():
    try:
        service = YourIntelligenceService()
        return await service.process(your_text)
    except Exception as e:
        logger.error(f"Lane 2 error: {e}")
        raise

results = await asyncio.gather(
    _lane1_other_processing(),
    _lane2_your_intelligence(),
    return_exceptions=True
)
```

### Customization Points

| Component | File | What to Change |
|-----------|------|----------------|
| Extraction Schema | `models/extraction_models.py` | Add/modify Pydantic models |
| System Prompt | `services/intelligence_service.py` | `_get_system_prompt()` |
| Insight Types | `models/db_models.py` | `InsightTypeEnum` enum |
| Database Tables | `models/db_models.py` | SQLModel classes |
| Persistence Logic | `services/intelligence_service.py` | `_persist_intelligence()` |

### Testing Strategy

1. **Unit Test Extraction Models**
   ```python
   def test_analysis_schema():
       # Verify Pydantic model accepts valid data
       data = {"summaries": {...}, "action_items": [...]}
       analysis = InteractionAnalysis(**data)
       assert analysis.summaries.title
   ```

2. **Mock LLM for Service Tests**
   ```python
   async def test_extract_intelligence(mocker):
       mock_client = mocker.patch.object(service, 'client')
       mock_client.create.return_value = mock_analysis
       result = await service._extract_intelligence("test")
       assert result is not None
   ```

3. **Integration Test with Real Database**
   ```python
   async def test_persist_intelligence():
       async with get_async_session() as session:
           # Test actual database writes
           await service._persist_intelligence(analysis, ...)
           # Query and verify
   ```

---

## Summary

The Lane 2 Intelligence System demonstrates a production-ready pattern for:

1. **Structured LLM Extraction** using instructor's `from_provider()` pattern
2. **Async Database Persistence** with SQLModel and asyncpg
3. **Error-Isolated Concurrent Processing** via `asyncio.gather(return_exceptions=True)`
4. **Domain-Specific Prompting** for GTM intelligence extraction

This architecture is designed to be replicated and customized for different domains while maintaining the same robust error handling and observability patterns.
