# Design Document

## Introduction

This document describes the technical design for the Intelligence Layer Integration feature. The design implements a "Sidecar" architecture that extracts structured insights from cleaned transcripts and persists them to Postgres (Neon) without disrupting the existing Kinesis publishing flow.

## Architecture Overview

### Async Fork Pattern

The system implements an "Async Fork" pattern where transcript processing splits into two concurrent lanes after the CleanerService returns a `MeetingOutput`:

```
                    ┌─────────────────────────────────────────┐
                    │         CleanerService                  │
                    │    Returns MeetingOutput                │
                    └─────────────────┬───────────────────────┘
                                      │
                    ┌─────────────────┴───────────────────────┐
                    │         asyncio.gather()                │
                    │      return_exceptions=True             │
                    └─────────────────┬───────────────────────┘
                                      │
              ┌───────────────────────┴───────────────────────┐
              │                                               │
              ▼                                               ▼
┌─────────────────────────┐                   ┌─────────────────────────┐
│        Lane 1           │                   │        Lane 2           │
│  (Existing Flow)        │                   │  (Intelligence Layer)   │
│                         │                   │                         │
│  • Kinesis Publishing   │                   │  • LLM Extraction       │
│  • EventBridge Events   │                   │  • Postgres Persistence │
│  • Redis Operations     │                   │                         │
└─────────────────────────┘                   └─────────────────────────┘
```

### Error Isolation

- Lane 2 failures MUST NOT block Lane 1 completion
- Lane 1 failures MUST NOT block Lane 2 completion
- Both lanes execute concurrently via `asyncio.gather(return_exceptions=True)`
- Exceptions are logged but do not propagate to the client

## Component Design

### IntelligenceService

The `IntelligenceService` is the primary component responsible for extracting structured insights and persisting them to Postgres.

```
services/
└── intelligence_service.py    # Main service class
```

#### Responsibilities

1. Initialize instructor-patched AsyncOpenAI client
2. Extract structured `InteractionAnalysis` from cleaned transcript
3. Persist summaries and insights to Postgres in a single transaction
4. Handle errors gracefully without raising exceptions

#### Class Structure

```python
class IntelligenceService:
    """Service for extracting and persisting structured intelligence from transcripts."""
    
    def __init__(self):
        """Initialize instructor client and database engine."""
        
    async def process_transcript(
        self,
        cleaned_transcript: str,
        interaction_id: str,
        tenant_id: str,
        trace_id: str,
        interaction_type: str = "meeting",
        account_id: Optional[str] = None,
        interaction_timestamp: Optional[datetime] = None
    ) -> Optional[InteractionAnalysis]:
        """Extract intelligence and persist to database."""
        
    async def _extract_intelligence(
        self,
        cleaned_transcript: str
    ) -> Optional[InteractionAnalysis]:
        """Use instructor to extract structured data from transcript."""
        
    async def _persist_intelligence(
        self,
        analysis: InteractionAnalysis,
        interaction_id: str,
        tenant_id: str,
        trace_id: str,
        persona_id: str,
        interaction_type: str,
        account_id: Optional[str],
        interaction_timestamp: datetime
    ) -> None:
        """Persist summaries and insights to Postgres in a single transaction."""
```

### Database Module

The database module provides async connection management using SQLAlchemy's async engine with SQLModel.

```
services/
└── database.py    # Database connection and session management
```

#### Connection Configuration

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Engine configuration for serverless (Neon)
engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=300  # Recycle connections every 5 minutes
)

async_session_maker = sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)
```

### Database Models

SQLModel table models that mirror the existing Postgres schema exactly.

```
models/
├── db_models.py           # SQLModel table definitions
└── extraction_models.py   # Pydantic models for LLM extraction
```

## Data Models

### Extraction Models (Pydantic)

These models define the structure for LLM extraction using instructor.

#### InteractionAnalysis

```python
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date
from enum import Enum

class RiskSeverityEnum(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"

class Summaries(BaseModel):
    """Multi-level summaries of the interaction."""
    title: str = Field(description="5-10 word title capturing the essence")
    headline: str = Field(description="1-2 sentence headline summary")
    brief: str = Field(description="2-3 paragraph executive summary")
    detailed: str = Field(description="Comprehensive summary with all key points")
    spotlight: str = Field(description="Key highlight or most important takeaway")

class ActionItem(BaseModel):
    """An actionable task extracted from the transcript."""
    description: str = Field(description="Clear description of the action item")
    owner: Optional[str] = Field(default=None, description="Person responsible, if mentioned")
    due_date: Optional[date] = Field(default=None, description="Due date, if mentioned")

class Decision(BaseModel):
    """A decision made during the interaction."""
    decision: str = Field(description="The decision that was made")
    rationale: Optional[str] = Field(default=None, description="Reasoning behind the decision")

class Risk(BaseModel):
    """A risk identified in the interaction."""
    risk: str = Field(description="Description of the risk")
    severity: RiskSeverityEnum = Field(description="Severity level: low, medium, or high")
    mitigation: Optional[str] = Field(default=None, description="Suggested mitigation, if mentioned")

class ProductFeedback(BaseModel):
    """Product-related feedback from the interaction."""
    text: str = Field(description="Feature request, pain point, bug, or UX friction")

class MarketIntelligence(BaseModel):
    """Market intelligence extracted from the interaction."""
    text: str = Field(description="Competitor mention, market trend, or macro-economic theme")

class InteractionAnalysis(BaseModel):
    """Complete structured analysis of an interaction transcript."""
    summaries: Summaries
    action_items: List[ActionItem] = Field(default_factory=list)
    decisions: List[Decision] = Field(default_factory=list)
    risks: List[Risk] = Field(default_factory=list)
    key_takeaways: List[str] = Field(default_factory=list)
    product_feedback: List[ProductFeedback] = Field(default_factory=list)
    market_intelligence: List[MarketIntelligence] = Field(default_factory=list)
```

### Database Models (SQLModel)

These models mirror the existing Postgres tables. A schema migration is required to add `product_feedback` and `market_intelligence` to the InsightType enum before implementation.

#### Schema Migration Required

Before implementing the database models, the Prisma schema must be updated:

```prisma
// In schema.prisma - Update InsightType enum
enum InsightType {
  action_item
  key_takeaway
  decision_made
  risk
  product_feedback      // NEW
  market_intelligence   // NEW
  unknown // Fallback for future types
}
```

Run migration: `prisma migrate dev --name add_new_insight_types`

#### InteractionSummaryEntryModel

```python
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, Text, Enum as SAEnum
from typing import Optional
from datetime import datetime
from uuid import UUID, uuid4
import enum

class SummaryLevelEnum(str, enum.Enum):
    title = "title"
    headline = "headline"
    brief = "brief"
    detailed = "detailed"
    spotlight = "spotlight"

class ProfileTypeEnum(str, enum.Enum):
    rich = "rich"
    lite = "lite"

class InteractionSummaryEntryModel(SQLModel, table=True):
    """Mirror of interaction_summary_entries table."""
    __tablename__ = "interaction_summary_entries"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(sa_column_kwargs={"name": "tenant_id"})
    interaction_id: UUID = Field(sa_column_kwargs={"name": "interaction_id"})
    persona_id: UUID = Field(sa_column_kwargs={"name": "persona_id"})
    level: SummaryLevelEnum = Field(sa_column=Column(SAEnum(SummaryLevelEnum)))
    text: str = Field(sa_column=Column(Text))
    word_count: Optional[int] = Field(default=None, sa_column_kwargs={"name": "word_count"})
    profile_type: ProfileTypeEnum = Field(
        default=ProfileTypeEnum.rich,  # REQUIRED: Database column requires a value, default to 'rich'
        sa_column=Column(SAEnum(ProfileTypeEnum), name="profile_type")
    )
    source: Optional[str] = Field(default=None)
    trace_id: UUID = Field(sa_column_kwargs={"name": "trace_id"})
    interaction_type: str = Field(sa_column_kwargs={"name": "interaction_type"})
    account_id: Optional[UUID] = Field(default=None, sa_column_kwargs={"name": "account_id"})
    interaction_timestamp: datetime = Field(sa_column_kwargs={"name": "interaction_timestamp"})
    created_at: datetime = Field(default_factory=datetime.utcnow, sa_column_kwargs={"name": "created_at"})
    updated_at: datetime = Field(default_factory=datetime.utcnow, sa_column_kwargs={"name": "updated_at"})
```

#### InteractionInsightModel

```python
class InsightTypeEnum(str, enum.Enum):
    action_item = "action_item"
    key_takeaway = "key_takeaway"
    decision_made = "decision_made"
    risk = "risk"
    product_feedback = "product_feedback"      # NEW - maps directly from extraction
    market_intelligence = "market_intelligence" # NEW - maps directly from extraction
    unknown = "unknown"

class RiskSeverityDBEnum(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"

class InteractionInsightModel(SQLModel, table=True):
    """Mirror of interaction_insights table."""
    __tablename__ = "interaction_insights"
    
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: UUID = Field(sa_column_kwargs={"name": "tenant_id"})
    interaction_id: UUID = Field(sa_column_kwargs={"name": "interaction_id"})
    persona_id: UUID = Field(sa_column_kwargs={"name": "persona_id"})
    type: InsightTypeEnum = Field(sa_column=Column(SAEnum(InsightTypeEnum)))
    
    # Typed columns (nullable, populated based on type)
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    owner: Optional[str] = Field(default=None, sa_column=Column(Text))
    due_date: Optional[datetime] = Field(default=None, sa_column_kwargs={"name": "due_date"})
    text: Optional[str] = Field(default=None, sa_column=Column(Text))
    decision: Optional[str] = Field(default=None, sa_column=Column(Text))
    rationale: Optional[str] = Field(default=None, sa_column=Column(Text))
    risk: Optional[str] = Field(default=None, sa_column=Column(Text))
    severity: Optional[RiskSeverityDBEnum] = Field(default=None, sa_column=Column(SAEnum(RiskSeverityDBEnum)))
    mitigation: Optional[str] = Field(default=None, sa_column=Column(Text))
    
    # Idempotency
    content_hash: str = Field(sa_column=Column(Text, name="content_hash"))
    
    # Metadata
    trace_id: UUID = Field(sa_column_kwargs={"name": "trace_id"})
    interaction_type: str = Field(sa_column_kwargs={"name": "interaction_type"})
    account_id: Optional[UUID] = Field(default=None, sa_column_kwargs={"name": "account_id"})
    interaction_timestamp: datetime = Field(sa_column_kwargs={"name": "interaction_timestamp"})
    created_at: datetime = Field(default_factory=datetime.utcnow, sa_column_kwargs={"name": "created_at"})
    updated_at: datetime = Field(default_factory=datetime.utcnow, sa_column_kwargs={"name": "updated_at"})
```

## Integration Points

### WebSocket Endpoint (main.py)

The WebSocket endpoint's `finally` block will be modified to execute both lanes concurrently:

```python
# In main.py websocket_endpoint finally block

async def _lane1_publish(meeting_output, session_id, tenant_id):
    """Lane 1: Existing event publishing flow."""
    await aws_publisher.publish_envelope(envelope)

async def _lane2_intelligence(cleaned_transcript, interaction_id, tenant_id, trace_id):
    """Lane 2: Intelligence extraction and persistence."""
    intelligence_service = IntelligenceService()
    await intelligence_service.process_transcript(
        cleaned_transcript=cleaned_transcript,
        interaction_id=interaction_id,
        tenant_id=tenant_id,
        trace_id=trace_id
    )

# Execute both lanes concurrently
results = await asyncio.gather(
    _lane1_publish(meeting_output, session_id, tenant_id),
    _lane2_intelligence(meeting_output.cleaned_transcript, interaction_id, tenant_id, trace_id),
    return_exceptions=True
)

# Log any exceptions without failing the request
for i, result in enumerate(results):
    if isinstance(result, Exception):
        lane_name = "Lane 1 (publishing)" if i == 0 else "Lane 2 (intelligence)"
        logger.error(f"{lane_name} failed: {result}", exc_info=result)
```

### Text Router (routers/text.py)

Similar integration in the text cleaning endpoint:

```python
# After cleaning and before returning response

results = await asyncio.gather(
    publisher.publish_envelope(envelope),
    intelligence_service.process_transcript(
        cleaned_transcript=cleaned_text,
        interaction_id=context.interaction_id,
        tenant_id=context.tenant_id,
        trace_id=context.trace_id,
        interaction_type="note"
    ),
    return_exceptions=True
)
```

### Batch Router (routers/batch.py)

The batch recording endpoint is a primary use case for the Intelligence Layer. It processes uploaded audio files through transcription, cleaning, and then the Async Fork pattern.

#### Current Implementation

The batch router currently:
1. Validates and reads the uploaded audio file
2. Transcribes audio via `BatchService.transcribe_audio()`
3. Cleans transcript via `BatchCleanerService.clean_transcript()`
4. Publishes `EnvelopeV1` via `AWSEventPublisher.publish_envelope()`
5. Returns `BatchProcessResponse` with raw/cleaned transcripts

#### Required Refactoring

The batch router must be refactored to use `asyncio.gather` to run Lane 1 (publishing) and Lane 2 (intelligence) in parallel immediately after cleaning completes:

```python
# In routers/batch.py - after cleaning, replace sequential publish with async fork

from services.intelligence_service import IntelligenceService

# Step 3: Execute Async Fork - Lane 1 (publish) and Lane 2 (intelligence) concurrently
async def _lane1_publish(envelope: EnvelopeV1) -> dict:
    """Lane 1: Publish envelope to Kinesis/EventBridge."""
    event_publisher = AWSEventPublisher()
    return await event_publisher.publish_envelope(envelope)

async def _lane2_intelligence(
    cleaned_transcript: str,
    interaction_id: str,
    tenant_id: str,
    trace_id: str
) -> Optional[InteractionAnalysis]:
    """Lane 2: Extract and persist intelligence."""
    intelligence_service = IntelligenceService()
    return await intelligence_service.process_transcript(
        cleaned_transcript=cleaned_transcript,
        interaction_id=interaction_id,
        tenant_id=tenant_id,
        trace_id=trace_id,
        interaction_type="batch_upload"  # Specific type for batch recordings
    )

# Build envelope
envelope = EnvelopeV1(
    tenant_id=UUID(context.tenant_id),
    user_id=context.user_id,
    interaction_type="transcript",
    content=ContentModel(text=cleaned_transcript, format="diarized"),
    timestamp=datetime.now(timezone.utc),
    source="upload",
    extras={},
    interaction_id=UUID(context.interaction_id),
    trace_id=context.trace_id
)

# Execute both lanes concurrently
results = await asyncio.gather(
    _lane1_publish(envelope),
    _lane2_intelligence(
        cleaned_transcript=cleaned_transcript,
        interaction_id=context.interaction_id,
        tenant_id=context.tenant_id,
        trace_id=context.trace_id
    ),
    return_exceptions=True
)

# Log any exceptions without failing the HTTP response
for i, result in enumerate(results):
    if isinstance(result, Exception):
        lane_name = "Lane 1 (publishing)" if i == 0 else "Lane 2 (intelligence)"
        logger.error(
            f"{lane_name} failed: processing_id={processing_id}, "
            f"interaction_id={context.interaction_id}, error={result}",
            exc_info=result
        )
```

#### Context Passing

The batch router MUST pass `interaction_type="batch_upload"` to the `IntelligenceService` to distinguish batch-uploaded recordings from live WebSocket sessions or text notes. This enables downstream analytics to segment insights by source.

#### Error Isolation

Critical requirement: Exceptions in Lane 2 (intelligence) MUST NOT cause the HTTP response to fail. The user should receive their `BatchProcessResponse` with transcripts even if intelligence extraction/persistence fails. This is achieved by:
1. Using `return_exceptions=True` in `asyncio.gather()`
2. Logging exceptions but not re-raising them
3. Returning the response regardless of Lane 2 outcome

## Instructor Integration

### Client Initialization

Using the verified `instructor.from_openai()` pattern from the 567-labs/instructor repository:

```python
import instructor
from openai import AsyncOpenAI

# Initialize instructor-patched async client
client = instructor.from_openai(
    AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY")),
    mode=instructor.Mode.TOOLS_STRICT  # For OpenAI structured outputs
)
```

### Extraction Call

```python
async def _extract_intelligence(self, cleaned_transcript: str) -> Optional[InteractionAnalysis]:
    """Extract structured intelligence using instructor."""
    try:
        result = await self.client.chat.completions.create(
            model=self.model,
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

### System Prompt (GTM Persona)

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
2. **Action Items**: Capture commitments, follow-ups, and next steps with owners when mentioned
3. **Decisions**: Document any agreements, approvals, or strategic choices made
4. **Risks**: Identify deal risks, relationship concerns, or competitive threats
5. **Key Takeaways**: Highlight insights valuable for account strategy
6. **Product Feedback**: Note feature requests, pain points, or UX issues mentioned
7. **Market Intelligence**: Capture competitor mentions, market trends, or industry themes

Be thorough but precise. Only extract information explicitly present in the transcript."""
```

## Persona Handling

### Default Persona Lookup

The system defaults to "gtm" persona. The persona UUID is looked up from the `personas` table:

```python
async def _get_persona_id(self, session: AsyncSession, persona_code: str = "gtm") -> UUID:
    """Look up persona UUID by code."""
    from sqlmodel import select
    
    result = await session.exec(
        select(PersonaModel).where(PersonaModel.code == persona_code)
    )
    persona = result.first()
    
    if not persona:
        raise ValueError(f"Persona '{persona_code}' not found in database")
    
    return persona.id
```

### Future Extensibility

The `process_transcript` method accepts an optional `persona_code` parameter for future multi-persona support:

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
    persona_code: str = "gtm"  # Parameterized for future extensibility
) -> Optional[InteractionAnalysis]:
```

## Error Handling Strategy

### Extraction Failures

```python
async def _extract_intelligence(self, cleaned_transcript: str) -> Optional[InteractionAnalysis]:
    try:
        # ... extraction logic
        return result
    except openai.APITimeoutError:
        logger.warning("LLM extraction timed out")
        return None
    except openai.APIError as e:
        logger.error(f"OpenAI API error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected extraction error: {e}", exc_info=True)
        return None
```

### Database Failures

```python
async def _persist_intelligence(self, analysis: InteractionAnalysis, ...) -> None:
    async with async_session_maker() as session:
        try:
            async with session.begin():
                # ... create and add all models
                await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Database persistence failed: {e}", exc_info=True)
            # Do not re-raise - fail gracefully
```

### Timeout Configuration

```python
# LLM call timeout (30 seconds)
client = instructor.from_openai(
    AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        timeout=30.0
    ),
    mode=instructor.Mode.TOOLS_STRICT
)
```

## Content Hash Generation

For idempotency, each insight generates a content hash:

```python
import hashlib

def _generate_content_hash(self, insight_type: str, content: str) -> str:
    """Generate SHA-256 hash for insight deduplication."""
    hash_input = f"{insight_type}:{content}"
    return hashlib.sha256(hash_input.encode()).hexdigest()
```

## Correctness Properties

### Property 1: Lane Isolation

**Property**: Lane 2 failure MUST NOT affect Lane 1 completion.

**Verification**: 
- Use `asyncio.gather(return_exceptions=True)` to capture exceptions without propagation
- Test with mocked Lane 2 failures to verify Lane 1 completes successfully

### Property 2: Transaction Atomicity

**Property**: All database writes for a single extraction MUST succeed or fail together.

**Verification**:
- Use SQLAlchemy's `session.begin()` context manager for transaction boundaries
- Test with simulated mid-transaction failures to verify rollback

### Property 3: Summary Completeness

**Property**: Exactly 5 summary entries (one per level) MUST be created per extraction.

**Verification**:
- Property-based test: For any valid InteractionAnalysis, count of persisted summaries equals 5
- Enumerate all SummaryLevel values in persistence loop

### Property 4: Insight Type Mapping

**Property**: Each insight MUST be persisted with the correct InsightType enum value.

**Verification**:
- Property-based test: For any insight, the persisted `type` field matches the source list
- action_items → InsightType.action_item
- decisions → InsightType.decision_made
- risks → InsightType.risk
- key_takeaways → InsightType.key_takeaway
- product_feedback → InsightType.product_feedback (direct mapping, NOT key_takeaway)
- market_intelligence → InsightType.market_intelligence (direct mapping, NOT key_takeaway)

### Property 5: Content Hash Uniqueness

**Property**: Content hash MUST be deterministic and unique per insight content.

**Verification**:
- Same content always produces same hash
- Different content produces different hash (with high probability)

### Property 6: Graceful Degradation

**Property**: Service MUST return None (not raise) on any failure.

**Verification**:
- Test all failure modes (API timeout, invalid response, DB connection failure)
- Verify None return and appropriate logging

## Testing Strategy

### Unit Tests

```python
# tests/unit/test_intelligence_service.py

@pytest.mark.asyncio
async def test_extract_intelligence_returns_valid_model():
    """Test that extraction returns a valid InteractionAnalysis."""
    service = IntelligenceService()
    with patch.object(service.client.chat.completions, 'create') as mock_create:
        mock_create.return_value = mock_analysis
        result = await service._extract_intelligence("Test transcript")
        assert isinstance(result, InteractionAnalysis)

@pytest.mark.asyncio
async def test_extraction_failure_returns_none():
    """Test that extraction failures return None, not raise."""
    service = IntelligenceService()
    with patch.object(service.client.chat.completions, 'create', side_effect=Exception("API Error")):
        result = await service._extract_intelligence("Test transcript")
        assert result is None
```

### Property-Based Tests

```python
# tests/unit/test_intelligence_properties.py

from hypothesis import given, strategies as st

@given(st.text(min_size=10))
def test_content_hash_deterministic(content):
    """Property: Same content always produces same hash."""
    service = IntelligenceService()
    hash1 = service._generate_content_hash("action_item", content)
    hash2 = service._generate_content_hash("action_item", content)
    assert hash1 == hash2

@given(st.text(min_size=10), st.text(min_size=10))
def test_content_hash_different_for_different_content(content1, content2):
    """Property: Different content produces different hash (usually)."""
    assume(content1 != content2)
    service = IntelligenceService()
    hash1 = service._generate_content_hash("action_item", content1)
    hash2 = service._generate_content_hash("action_item", content2)
    assert hash1 != hash2
```

### Integration Tests

```python
# tests/integration/test_intelligence_persistence.py

@pytest.mark.asyncio
async def test_persist_creates_exactly_5_summaries(test_db_session):
    """Integration: Verify exactly 5 summary entries are created."""
    service = IntelligenceService()
    analysis = create_test_analysis()
    
    await service._persist_intelligence(
        analysis=analysis,
        interaction_id=str(uuid4()),
        tenant_id=TEST_TENANT_ID,
        trace_id=str(uuid4()),
        persona_id=GTM_PERSONA_ID,
        interaction_type="meeting",
        account_id=None,
        interaction_timestamp=datetime.utcnow()
    )
    
    # Verify count
    result = await test_db_session.exec(
        select(InteractionSummaryEntryModel).where(
            InteractionSummaryEntryModel.interaction_id == interaction_id
        )
    )
    summaries = result.all()
    assert len(summaries) == 5
```

### Async Fork Tests

```python
# tests/integration/test_async_fork.py

@pytest.mark.asyncio
async def test_lane2_failure_does_not_block_lane1():
    """Test that Lane 2 failure doesn't affect Lane 1."""
    lane1_completed = False
    
    async def lane1():
        nonlocal lane1_completed
        await asyncio.sleep(0.1)
        lane1_completed = True
        return "lane1_success"
    
    async def lane2():
        raise Exception("Lane 2 intentional failure")
    
    results = await asyncio.gather(lane1(), lane2(), return_exceptions=True)
    
    assert lane1_completed is True
    assert results[0] == "lane1_success"
    assert isinstance(results[1], Exception)
```

## Verification Tooling

### Live Verification Harness

The system includes a standalone verification script (`scripts/verify_intelligence_live.py`) that enables end-to-end testing of the intelligence pipeline with real production-scale transcripts against the live Neon database and OpenAI API.

#### Purpose

Testing with a realistic, high-token-count transcript is the only way to verify that:
1. The `gpt-4o` context window handles the length
2. The structured output doesn't truncate or hallucinate format errors
3. The database transaction actually commits the complex nested data

#### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    verify_intelligence_live.py                       │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         SETUP PHASE                                  │
│  • Load test_payload.json                                           │
│  • Extract tenant_id, user_id from headers                          │
│  • Generate new trace_id (UUID v4)                                  │
│  • Generate new interaction_id (UUID v4)                            │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        EXTRACT PHASE                                 │
│  • Instantiate IntelligenceService directly                         │
│  • Call process_transcript() with payload text                      │
│  • Capture raw InteractionAnalysis output                           │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        PERSIST PHASE                                 │
│  • IntelligenceService writes to Neon                               │
│  • Summaries → interaction_summary_entries                          │
│  • Insights → interaction_insights                                  │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        VERIFY PHASE                                  │
│  • Query Neon for created rows by interaction_id                    │
│  • Count summaries (expect 5)                                       │
│  • Count insights by type                                           │
│  • Validate all expected data present                               │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        REPORT PHASE                                  │
│  • Generate test_artifacts/run_{timestamp}.md                       │
│  • Include: config, input snippet, raw LLM output, DB verification  │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       TEARDOWN PHASE                                 │
│  • DELETE FROM interaction_insights WHERE interaction_id = ?        │
│  • DELETE FROM interaction_summary_entries WHERE interaction_id = ? │
│  • Verify cleanup complete                                          │
└─────────────────────────────────────────────────────────────────────┘
```

#### Artifact Structure

The script outputs to `test_artifacts/run_{timestamp}.md` with the following structure:

```markdown
# Intelligence Layer Live Verification Report

## Run Configuration
- **Timestamp**: 2026-01-14T10:30:00Z
- **Trace ID**: 7c3f7b54-6f3a-4b1c-bf6d-0f8b2c3a1d9e
- **Interaction ID**: <generated-uuid>
- **Tenant ID**: 11111111-1111-4111-8111-111111111111
- **User ID**: user_manual_test_001
- **Model**: gpt-4o

## Input Transcript (First 500 chars)
```
A: Hey, folks. How's everybody doing?
B: Good.
C: How about yourself?
...
```

## Raw LLM Output (InteractionAnalysis)
```json
{
  "summaries": {
    "title": "...",
    "headline": "...",
    ...
  },
  "action_items": [...],
  ...
}
```

## Database Verification

### Summaries Created
| Level | Word Count | Preview |
|-------|------------|---------|
| title | 8 | AWS AML Program Overview... |
| headline | 25 | ... |
| brief | 150 | ... |
| detailed | 500 | ... |
| spotlight | 30 | ... |

### Insights Created
| Type | Count |
|------|-------|
| action_item | 3 |
| decision_made | 2 |
| risk | 1 |
| key_takeaway | 5 |
| product_feedback | 0 |
| market_intelligence | 2 |

## Teardown
- ✅ Deleted 5 summary entries
- ✅ Deleted 13 insight entries
- ✅ Cleanup verified
```

#### Teardown Logic

The script uses the returned `interaction_id` to execute cleanup:

```python
async def teardown(session: AsyncSession, interaction_id: UUID) -> dict:
    """Delete all rows created during the test run."""
    
    # Delete insights first (no FK constraint issues)
    insights_result = await session.execute(
        delete(InteractionInsightModel).where(
            InteractionInsightModel.interaction_id == interaction_id
        )
    )
    
    # Delete summaries
    summaries_result = await session.execute(
        delete(InteractionSummaryEntryModel).where(
            InteractionSummaryEntryModel.interaction_id == interaction_id
        )
    )
    
    await session.commit()
    
    return {
        "insights_deleted": insights_result.rowcount,
        "summaries_deleted": summaries_result.rowcount
    }
```

#### Test Payload Format

The `test_payload.json` file follows the production envelope format:

```json
{
  "text": "<full transcript text>",
  "metadata": {
    "source": "manual_e2e_test",
    "priority": "high",
    "tested_by": "Peter",
    "publisher_service": "live-transcription-fastapi",
    "publisher_run_id": "lts-rerun-20260113T031500Z-a1b2c3d4",
    "publisher_sent_at": "2026-01-13T03:15:00Z"
  },
  "headers": {
    "X-Tenant-ID": "11111111-1111-4111-8111-111111111111",
    "X-User-ID": "user_manual_test_001",
    "X-Trace-Id": "7c3f7b54-6f3a-4b1c-bf6d-0f8b2c3a1d9e"
  }
}
```

## Dependencies

### New Dependencies (requirements.txt additions)

```
# Intelligence Layer
instructor>=1.7.0
sqlmodel>=0.0.22
asyncpg>=0.30.0
greenlet>=3.0.0
```

### Dependency Justification

- **instructor**: Structured LLM output extraction with Pydantic validation
- **sqlmodel**: ORM bridging Pydantic and SQLAlchemy for type-safe database operations
- **asyncpg**: Async PostgreSQL driver for SQLAlchemy async engine
- **greenlet**: Required by SQLAlchemy for async context switching

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| DATABASE_URL | Yes | - | Postgres connection string (Neon) |
| OPENAI_API_KEY | Yes | - | OpenAI API key (existing) |
| OPENAI_MODEL | No | gpt-4o | Model for extraction (existing) |

### .env.example Update

```bash
# Existing
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o

# New - Intelligence Layer
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database
```

## File Structure

```
services/
├── intelligence_service.py    # Main IntelligenceService class
└── database.py                # Database connection management

models/
├── db_models.py               # SQLModel table definitions
└── extraction_models.py       # Pydantic models for LLM extraction
```

## Sequence Diagram

```
┌─────────┐     ┌─────────────┐     ┌──────────────────┐     ┌────────────┐     ┌──────────┐
│ Client  │     │  Endpoint   │     │ CleanerService   │     │Intelligence│     │ Postgres │
└────┬────┘     └──────┬──────┘     └────────┬─────────┘     │  Service   │     └────┬─────┘
     │                 │                     │               └─────┬──────┘          │
     │  Request        │                     │                     │                 │
     │────────────────>│                     │                     │                 │
     │                 │                     │                     │                 │
     │                 │  clean_transcript() │                     │                 │
     │                 │────────────────────>│                     │                 │
     │                 │                     │                     │                 │
     │                 │   MeetingOutput     │                     │                 │
     │                 │<────────────────────│                     │                 │
     │                 │                     │                     │                 │
     │                 │         asyncio.gather()                  │                 │
     │                 │─────────────────────────────────────────>│                 │
     │                 │                     │                     │                 │
     │                 │                     │    Lane 1: publish  │                 │
     │                 │                     │    (concurrent)     │                 │
     │                 │                     │                     │                 │
     │                 │                     │    Lane 2: extract  │                 │
     │                 │                     │                     │────────────────>│
     │                 │                     │                     │   OpenAI API    │
     │                 │                     │                     │<────────────────│
     │                 │                     │                     │                 │
     │                 │                     │                     │  persist()      │
     │                 │                     │                     │────────────────>│
     │                 │                     │                     │                 │
     │                 │                     │                     │   commit        │
     │                 │                     │                     │<────────────────│
     │                 │                     │                     │                 │
     │                 │<─────────────────────────────────────────│                 │
     │                 │         results (both lanes)              │                 │
     │                 │                     │                     │                 │
     │   Response      │                     │                     │                 │
     │<────────────────│                     │                     │                 │
     │                 │                     │                     │                 │
```
