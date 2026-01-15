# Requirements Document

## Introduction

This document specifies the requirements for the Intelligence Layer Integration feature. The feature implements a "Sidecar" Intelligence Layer that extracts structured insights from cleaned transcripts and persists them directly to Postgres (Neon) without disrupting the existing Kinesis publishing flow. The architecture follows an "Async Fork" pattern where transcript processing splits into two concurrent lanes: the existing event publishing lane and a new intelligence extraction/persistence lane.

## Glossary

- **Intelligence_Service**: A new service component responsible for extracting structured insights from cleaned transcripts using LLM (GPT-4o via instructor library) and persisting results to Postgres.
- **Async_Fork**: The architectural pattern where transcript processing splits into two concurrent lanes after cleaning, executed via `asyncio.gather()`.
- **Lane_1**: The existing event publishing flow (Kinesis/EventBridge) that must remain unblocked.
- **Lane_2**: The new intelligence extraction and persistence flow to Neon Postgres.
- **Interaction_Analysis**: The Pydantic model representing the complete extracted intelligence from a transcript, including summaries and insights.
- **Summary_Level**: An enumeration of the five summary granularities: title, headline, brief, detailed, spotlight.
- **Insight_Type**: An enumeration of insight categories: action_item, key_takeaway, decision_made, risk, product_feedback, market_intelligence.
- **Persona**: The context lens for extraction, defaulting to "GTM" (Go-To-Market).
- **Mirror_Pattern**: The strategy of creating SQLModel classes that exactly match existing Postgres tables without running migrations.
- **Instructor**: The Python library for structured LLM output extraction using Pydantic models.
- **SQLModel**: The ORM library bridging Pydantic and SQLAlchemy for database operations.

## Requirements

### Requirement 1: Database Connection Infrastructure

**User Story:** As a system operator, I want the service to establish and manage async database connections, so that intelligence data can be persisted reliably to Neon Postgres.

#### Acceptance Criteria

1. WHEN the application starts, THE Database_Module SHALL create an AsyncEngine with connection pooling configured for serverless environments.
2. WHEN DATABASE_URL environment variable is missing, THE Application SHALL fail startup with a clear error message.
3. WHEN a database operation is requested, THE Database_Module SHALL provide an async session from the connection pool.
4. WHEN the application shuts down, THE Database_Module SHALL gracefully close all database connections.
5. IF a database connection fails, THEN THE Database_Module SHALL log the error with connection details (excluding credentials).

### Requirement 2: Database Model Mirroring

**User Story:** As a developer, I want SQLModel classes that exactly mirror the existing Postgres schema, so that I can persist intelligence data without running migrations.

#### Acceptance Criteria

1. THE InteractionSummaryEntry_Model SHALL define fields matching the `interaction_summary_entries` table columns exactly.
2. THE InteractionInsight_Model SHALL define fields matching the `interaction_insights` table columns exactly.
3. WHEN mapping Python types to Postgres types, THE Models SHALL use correct type mappings for UUID, Text, DateTime, and Enum fields.
4. THE Models SHALL include the `table=True` parameter to indicate they are database table models.
5. THE Models SHALL use `sa_column` for fields requiring specific SQLAlchemy column configurations.

### Requirement 3: Intelligence Extraction Model Design

**User Story:** As a product manager, I want a comprehensive extraction model that captures multi-level summaries and diverse insight types, so that downstream consumers receive rich, structured intelligence.

#### Acceptance Criteria

1. THE InteractionAnalysis_Model SHALL contain a nested Summaries model with exactly five fields: title, headline, brief, detailed, spotlight.
2. THE InteractionAnalysis_Model SHALL contain lists for: action_items, decisions, risks, key_takeaways, product_feedback, market_intelligence.
3. WHEN extracting action_items, THE Model SHALL capture description, owner (if mentioned), and due_date (if mentioned).
4. WHEN extracting decisions, THE Model SHALL capture the decision text and rationale (if provided).
5. WHEN extracting risks, THE Model SHALL capture the risk description, severity level, and mitigation (if mentioned).
6. WHEN extracting product_feedback, THE Model SHALL capture feature requests, pain points, bugs, or UX friction mentioned.
7. WHEN extracting market_intelligence, THE Model SHALL capture competitor mentions, market trends, or macro-economic themes.

### Requirement 4: LLM Extraction Implementation

**User Story:** As a developer, I want the Intelligence_Service to use the instructor library for reliable structured extraction, so that LLM outputs are guaranteed to match our Pydantic models.

#### Acceptance Criteria

1. THE Intelligence_Service SHALL initialize an instructor-patched AsyncOpenAI client.
2. WHEN extracting intelligence, THE Intelligence_Service SHALL use GPT-4o model with structured output mode.
3. THE Intelligence_Service SHALL use a system prompt optimized for GTM persona context.
4. WHEN the LLM returns a response, THE Intelligence_Service SHALL validate it against the InteractionAnalysis model.
5. IF extraction fails, THEN THE Intelligence_Service SHALL log the error and return None without raising an exception.

### Requirement 5: Database Persistence Implementation

**User Story:** As a developer, I want the Intelligence_Service to persist extracted data atomically, so that partial writes do not corrupt the database state.

#### Acceptance Criteria

1. WHEN persisting intelligence data, THE Intelligence_Service SHALL write all summary entries and insights in a single database transaction.
2. THE Intelligence_Service SHALL create exactly 5 InteractionSummaryEntry rows (one per summary level) per extraction.
3. THE Intelligence_Service SHALL create one InteractionInsight row per extracted insight item.
4. WHEN mapping summaries to database rows, THE Intelligence_Service SHALL use the correct SummaryLevel enum values.
5. WHEN mapping insights to database rows, THE Intelligence_Service SHALL use the correct InsightType enum values.
6. THE Intelligence_Service SHALL generate a content_hash for each insight to support idempotency.
7. IF a database write fails, THEN THE Intelligence_Service SHALL rollback the transaction and log the error.

### Requirement 6: Async Fork Integration

**User Story:** As a system architect, I want the intelligence processing to run concurrently with event publishing, so that neither lane blocks the other.

#### Acceptance Criteria

1. WHEN a transcript is cleaned in the WebSocket endpoint, THE System SHALL execute Lane_1 (event publishing) and Lane_2 (intelligence) concurrently using asyncio.gather().
2. WHEN a transcript is cleaned in the text router, THE System SHALL execute Lane_1 and Lane_2 concurrently using asyncio.gather().
3. WHEN a transcript is cleaned in the batch recording endpoint (`routers/batch.py`), THE System SHALL execute Lane_1 (publishing) and Lane_2 (intelligence) concurrently using asyncio.gather().
4. IF Lane_2 fails, THEN THE System SHALL log the error but Lane_1 SHALL complete successfully.
5. IF Lane_1 fails, THEN THE System SHALL log the error but Lane_2 SHALL complete successfully.
6. THE System SHALL pass return_exceptions=True to asyncio.gather() to prevent one lane's failure from canceling the other.

### Requirement 7: Error Isolation and Resilience

**User Story:** As a system operator, I want intelligence layer failures to be isolated, so that the primary transcription and publishing flow remains reliable.

#### Acceptance Criteria

1. IF the Intelligence_Service extraction fails, THEN THE primary endpoint response SHALL still succeed.
2. IF the database connection is unavailable, THEN THE Intelligence_Service SHALL log the error and return gracefully.
3. WHEN an error occurs in Lane_2, THE System SHALL NOT return a 500 error to the client.
4. THE Intelligence_Service SHALL implement timeout handling for LLM calls to prevent hanging.
5. WHEN logging errors, THE Intelligence_Service SHALL include interaction_id, tenant_id, and error type for debugging.

### Requirement 8: Configuration and Environment

**User Story:** As a DevOps engineer, I want all intelligence layer configuration to be environment-driven, so that I can deploy to different environments without code changes.

#### Acceptance Criteria

1. THE Application SHALL require DATABASE_URL environment variable for Postgres connection.
2. THE Application SHALL use existing OPENAI_API_KEY for instructor client initialization.
3. THE Application SHALL use existing OPENAI_MODEL (defaulting to gpt-4o) for extraction.
4. WHEN DATABASE_URL is missing, THE Application startup validation SHALL fail with a descriptive error.
5. THE .env.example file SHALL be updated to include DATABASE_URL with a placeholder value.

### Requirement 9: Persona Context Handling

**User Story:** As a product manager, I want the extraction to use GTM persona context by default, so that insights are relevant for go-to-market teams while remaining useful for other roles.

#### Acceptance Criteria

1. THE Intelligence_Service SHALL default to "gtm" persona code when extracting insights.
2. WHEN persisting to database, THE Intelligence_Service SHALL look up the persona UUID from the Persona table using the code.
3. THE extraction system prompt SHALL be written from a GTM leader perspective.
4. THE code architecture SHALL allow persona to be parameterized for future extensibility.

### Requirement 10: Dependency Management

**User Story:** As a developer, I want all required dependencies properly specified, so that the application can be deployed consistently.

#### Acceptance Criteria

1. THE requirements.txt SHALL include instructor library with a pinned version.
2. THE requirements.txt SHALL include sqlmodel library with a pinned version.
3. THE requirements.txt SHALL include asyncpg library with a pinned version.
4. THE requirements.txt SHALL include greenlet library (required by SQLAlchemy async).

### Requirement 11: Implementation Directives

**User Story:** As a developer, I want clear guidance on library usage patterns, so that the implementation follows current best practices and verified API methods.

#### Acceptance Criteria

1. THE implementation SHALL use the Instructor library pattern from the 567-labs/instructor repository.
2. WHEN implementing LLM extraction, THE Developer SHALL reference the examples/ directory in the instructor repository for Mode.TOOLS_STRICT usage patterns.
3. THE implementation SHALL use the SQLModel library pattern from the fastapi/sqlmodel repository.
4. WHEN implementing database models, THE Developer SHALL reference the SQLModel "Table Model" documentation for correct table model patterns.
5. BEFORE writing implementation code, THE Developer SHALL verify the latest API methods for instructor and sqlmodel libraries using GitHub MCP tools rather than relying on training data.
6. THE Developer SHALL NOT hallucinate API methods and SHALL verify client initialization patterns (e.g., `instructor.from_openai`) against the latest repository code.

### Requirement 12: Live Verification Harness

**User Story:** As a developer, I want a standalone script to run the intelligence pipeline with real production transcripts, so that I can verify token limits, schema adherence, and database persistence with visual proof.

#### Acceptance Criteria

1. THE System SHALL include a standalone script (`scripts/verify_intelligence_live.py`) that bypasses the API layer and invokes IntelligenceService directly.
2. WHEN executed, THE Script SHALL read transcript data from `test_payload.json` in the project root.
3. THE Script SHALL use a consistent `tenant_id` and `user_id` from the payload headers, but SHALL generate a new `trace_id` (UUID v4) for every run.
4. WHEN the intelligence pipeline completes, THE Script SHALL generate a Run Artifact (Markdown file) in `test_artifacts/run_{timestamp}.md`.
5. THE Run Artifact SHALL contain: configuration used, input transcript snippet (first 500 chars), raw JSON output from the LLM (Instructor), and verification of persisted DB rows (Summaries and Insights).
6. AFTER verification, THE Script SHALL perform a Teardown step that deletes the created rows from Neon using the returned `interaction_id` to ensure test repeatability.
7. IF any step fails, THEN THE Script SHALL log the error with full context and exit with a non-zero status code.
8. THE `test_payload.json` file SHALL contain a realistic, high-token-count transcript (the AML meeting transcript) with metadata and headers matching the production envelope format.
