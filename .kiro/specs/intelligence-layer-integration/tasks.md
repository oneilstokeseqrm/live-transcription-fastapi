# Tasks Document

## Task 1: Schema Migration - Add New InsightType Enum Values

### Description
Update the Prisma schema to add `product_feedback` and `market_intelligence` to the InsightType enum, then run the migration.

### Acceptance Criteria
- [x] `schema.prisma` updated with `product_feedback` added to InsightType enum
- [x] `schema.prisma` updated with `market_intelligence` added to InsightType enum
- [x] Migration created with `prisma migrate dev --name add_new_insight_types`
- [x] Migration applied successfully to Neon database
- [x] Verify enum values exist in database with SQL query

### Verification Steps
1. Use Neon MCP to list projects: `mcp_neon_list_projects({"limit": 100})`
2. Get the correct Project ID (word-word-numbers format, NOT Endpoint ID)
3. Run SQL to verify: `SELECT enumlabel FROM pg_enum WHERE enumtypid = 'InsightType'::regtype`

### Files to Modify
- `schema.prisma`

### CRITICAL NOTES
- Do NOT use Endpoint ID (ep-random-string) as projectId - causes 404 errors
- Must use Project ID from `mcp_neon_list_projects` response

---

## Task 2: Add Dependencies to requirements.txt

### Description
Add the required dependencies for the Intelligence Layer: instructor, sqlmodel, asyncpg, and greenlet.

### Acceptance Criteria
- [x] instructor>=1.7.0 added to requirements.txt
- [x] sqlmodel>=0.0.22 added to requirements.txt
- [x] asyncpg>=0.30.0 added to requirements.txt
- [x] greenlet>=3.0.0 added to requirements.txt
- [ ] Dependencies install successfully with `pip install -r requirements.txt`

### Files to Modify
- `requirements.txt`

---

## Task 3: Update Environment Configuration

### Description
Add DATABASE_URL to environment validation and update .env.example with the new variable.

### Acceptance Criteria
- [x] DATABASE_URL added to REQUIRED_ENV_VARS in main.py
- [x] .env.example updated with DATABASE_URL placeholder
- [x] Application fails startup with clear error if DATABASE_URL is missing

### Files to Modify
- `main.py`
- `.env.example`

---

## Task 4: Create Extraction Models

### Description
Create Pydantic models for LLM extraction: InteractionAnalysis with nested Summaries, ActionItem, Decision, Risk, ProductFeedback, and MarketIntelligence models.

### Acceptance Criteria
- [x] models/extraction_models.py created
- [x] Summaries model with 5 fields: title, headline, brief, detailed, spotlight
- [x] ActionItem model with description, owner (optional), due_date (optional)
- [x] Decision model with decision, rationale (optional)
- [x] Risk model with risk, severity (enum), mitigation (optional)
- [x] ProductFeedback model with text field
- [x] MarketIntelligence model with text field
- [x] InteractionAnalysis model containing all nested models
- [x] All models have appropriate Field descriptions for LLM guidance

### Files to Create
- `models/extraction_models.py`

---

## Task 5: Create Database Models

### Description
Create SQLModel table models that mirror the existing Postgres schema for InteractionSummaryEntry and InteractionInsight tables.

### Pre-Implementation Verification
Use GitHub MCP to verify SQLModel "Table Model" pattern:
```
mcp_github_get_file_contents({
  owner: "fastapi",
  repo: "sqlmodel",
  path: "docs/tutorial/create-db-and-table.md"
})
```

### Acceptance Criteria
- [x] models/db_models.py created
- [x] SummaryLevelEnum with values: title, headline, brief, detailed, spotlight
- [x] ProfileTypeEnum with values: rich, lite
- [x] InsightTypeEnum with values: action_item, key_takeaway, decision_made, risk, product_feedback, market_intelligence, unknown
- [x] RiskSeverityDBEnum with values: low, medium, high
- [x] InteractionSummaryEntryModel with table=True, matching all columns from schema.prisma
- [x] InteractionInsightModel with table=True, matching all columns from schema.prisma
- [x] PersonaModel with table=True for persona lookup
- [x] All column names use sa_column_kwargs for snake_case mapping
- [x] profile_type defaults to ProfileTypeEnum.rich (REQUIRED - database column requires value)

### Files to Create
- `models/db_models.py`

---

## Task 6: Create Database Module

### Description
Create the database connection management module with async engine and session factory.

### Acceptance Criteria
- [x] services/database.py created
- [x] create_async_engine configured with asyncpg driver
- [x] Connection pool settings appropriate for serverless (pool_size=5, max_overflow=10, pool_recycle=300)
- [x] async_session_maker factory created
- [x] get_async_session() async context manager for session management
- [x] Engine initialization validates DATABASE_URL presence

### Files to Create
- `services/database.py`

---

## Task 7: Create IntelligenceService - Initialization

### Description
Create the IntelligenceService class with instructor client initialization.

### Pre-Implementation Verification
Use GitHub MCP to verify instructor client initialization pattern:
```
mcp_github_search_code({
  q: "from_openai Mode.TOOLS_STRICT repo:567-labs/instructor"
})
```
Then fetch relevant example files to confirm the pattern.

### Acceptance Criteria
- [x] services/intelligence_service.py created
- [x] IntelligenceService class with __init__ method
- [x] instructor.from_provider() used with async_client=True (updated API)
- [x] Mode defaults handled by from_provider
- [x] Timeout configured via instructor defaults
- [x] Model configurable via OPENAI_MODEL env var (default: gpt-4o)
- [x] Logger configured for the service

### Files to Create
- `services/intelligence_service.py`

---

## Task 8: Implement LLM Extraction Method

### Description
Implement the _extract_intelligence method that uses instructor to extract InteractionAnalysis from transcript.

### Acceptance Criteria
- [x] _extract_intelligence async method implemented
- [x] Uses client.create with response_model=InteractionAnalysis
- [x] GTM-focused system prompt included
- [x] max_retries=2 configured
- [x] Returns Optional[InteractionAnalysis]
- [x] Returns None on any exception (does not raise)
- [x] Logs errors with appropriate context

### Files to Modify
- `services/intelligence_service.py`

---

## Task 9: Implement Persona Lookup

### Description
Implement the _get_persona_id method to look up persona UUID by code.

### Acceptance Criteria
- [x] _get_persona_id async method implemented
- [x] Queries personas table by code field
- [x] Returns UUID of matching persona
- [x] Raises ValueError if persona not found
- [x] Default persona_code is "gtm"

### Files to Modify
- `services/intelligence_service.py`

---

## Task 10: Implement Content Hash Generation

### Description
Implement the _generate_content_hash method for insight idempotency.

### Acceptance Criteria
- [x] _generate_content_hash method implemented
- [x] Uses SHA-256 hash algorithm
- [x] Hash input format: "{insight_type}:{content}"
- [x] Returns hex digest string
- [x] Deterministic: same input always produces same output

### Files to Modify
- `services/intelligence_service.py`

---

## Task 11: Implement Database Persistence

### Description
Implement the _persist_intelligence method to write summaries and insights to Postgres.

### Acceptance Criteria
- [x] _persist_intelligence async method implemented
- [x] Creates exactly 5 InteractionSummaryEntryModel rows (one per level)
- [x] Creates InteractionInsightModel rows for each insight type
- [x] Maps action_items to InsightType.action_item
- [x] Maps decisions to InsightType.decision_made
- [x] Maps risks to InsightType.risk
- [x] Maps key_takeaways to InsightType.key_takeaway
- [x] Maps product_feedback to InsightType.product_feedback (DIRECT mapping - NOT key_takeaway)
- [x] Maps market_intelligence to InsightType.market_intelligence (DIRECT mapping - NOT key_takeaway)
- [x] All writes in single transaction (session.begin())
- [x] Rollback on any failure
- [x] Does not raise exceptions (logs and returns)
- [x] profile_type set to 'rich' for all summary entries

### Files to Modify
- `services/intelligence_service.py`

---

## Task 12: Implement Main Process Method

### Description
Implement the process_transcript public method that orchestrates extraction and persistence.

### Acceptance Criteria
- [x] process_transcript async method implemented
- [x] Accepts: cleaned_transcript, interaction_id, tenant_id, trace_id, interaction_type, account_id, interaction_timestamp, persona_code
- [x] Calls _extract_intelligence
- [x] If extraction succeeds, calls _persist_intelligence
- [x] Returns Optional[InteractionAnalysis]
- [x] Returns None on any failure (does not raise)
- [x] Logs start, success, and failure events with context

### Files to Modify
- `services/intelligence_service.py`

---

## Task 13: Integrate into WebSocket Endpoint

### Description
Modify the WebSocket endpoint to execute Lane 1 and Lane 2 concurrently using asyncio.gather.

### Acceptance Criteria
- [x] Import IntelligenceService in main.py
- [x] Create async helper functions for Lane 1 and Lane 2
- [x] Use asyncio.gather(lane1(), lane2(), return_exceptions=True)
- [x] Log exceptions from either lane without failing the request
- [x] Lane 2 receives: cleaned_transcript, interaction_id (session_id), tenant_id, trace_id
- [x] Existing Lane 1 behavior unchanged

### Files to Modify
- `main.py`

---

## Task 14: Integrate into Text Router

### Description
Modify the text cleaning endpoint to execute Lane 1 and Lane 2 concurrently.

### Acceptance Criteria
- [x] Import IntelligenceService in routers/text.py
- [x] Use asyncio.gather for concurrent execution
- [x] Pass interaction_type="note" to intelligence service
- [x] Lane 2 receives: cleaned_text, interaction_id, tenant_id, trace_id
- [x] Existing response behavior unchanged
- [x] Exceptions logged but do not fail the request

### Files to Modify
- `routers/text.py`

---

## Task 14b: Integrate into Batch Router

### Description
Modify the batch recording endpoint (`routers/batch.py`) to implement the Async Fork pattern using `asyncio.gather`. This is a primary use case and must execute Lane 1 (publishing) and Lane 2 (intelligence) concurrently immediately after cleaning completes.

### Acceptance Criteria
- [x] Import IntelligenceService in routers/batch.py
- [x] Import asyncio for gather functionality
- [x] Create async helper functions for Lane 1 and Lane 2
- [x] Use asyncio.gather(lane1(), lane2(), return_exceptions=True)
- [x] Pass interaction_type="batch_upload" to intelligence service
- [x] Lane 2 receives: cleaned_transcript, interaction_id, tenant_id, trace_id
- [x] Exceptions in Lane 2 do NOT fail the HTTP response to the user
- [x] Log exceptions from either lane with processing_id and interaction_id context
- [x] Existing BatchProcessResponse behavior unchanged
- [x] User receives transcripts even if intelligence extraction fails

### Files to Modify
- `routers/batch.py`

### Implementation Notes
The batch router currently has a try/except around the publish step that logs errors but doesn't fail the request. This pattern should be extended to use asyncio.gather for both lanes, maintaining the same resilience guarantee.

---

## Task 15: Create Unit Tests for Extraction Models

### Description
Create unit tests for the Pydantic extraction models.

### Acceptance Criteria
- [x] tests/unit/test_extraction_models.py created
- [x] Test InteractionAnalysis model validation
- [x] Test Summaries model with all 5 fields
- [x] Test ActionItem with optional fields
- [x] Test Risk with severity enum validation
- [x] Test model serialization/deserialization

### Files to Create
- `tests/unit/test_extraction_models.py`

---

## Task 16: Create Unit Tests for IntelligenceService

### Description
Create unit tests for IntelligenceService methods with mocked dependencies.

### Acceptance Criteria
- [x] tests/unit/test_intelligence_service.py created
- [x] Test _extract_intelligence with mocked instructor client
- [x] Test _extract_intelligence returns None on API error
- [x] Test _extract_intelligence returns None on timeout
- [x] Test _generate_content_hash determinism
- [x] Test _generate_content_hash uniqueness for different inputs

### Files to Create
- `tests/unit/test_intelligence_service.py`

---

## Task 17: Create Property-Based Tests

### Description
Create property-based tests using Hypothesis for correctness properties.

### Acceptance Criteria
- [x] tests/unit/test_intelligence_properties.py created
- [x] Property test: content hash is deterministic
- [x] Property test: different content produces different hash
- [x] Property test: summary count always equals 5 for valid analysis
- [x] Property test: insight type mapping is correct for all 6 types (including product_feedback, market_intelligence)

### Files to Create
- `tests/unit/test_intelligence_properties.py`

---

## Task 18: Create Integration Tests for Database Persistence

### Description
Create integration tests for database persistence using a test database.

### Acceptance Criteria
- [x] tests/integration/test_intelligence_persistence.py created
- [x] Test fixture for test database session
- [x] Test _persist_intelligence creates exactly 5 summary entries
- [x] Test _persist_intelligence creates correct insight types
- [x] Test product_feedback persists with InsightType.product_feedback
- [x] Test market_intelligence persists with InsightType.market_intelligence
- [x] Test transaction rollback on failure
- [x] Test idempotency with content_hash

### Files to Create
- `tests/integration/test_intelligence_persistence.py`

---

## Task 19: Create Async Fork Integration Tests

### Description
Create integration tests verifying the async fork pattern and error isolation.

### Acceptance Criteria
- [x] tests/integration/test_async_fork.py created
- [x] Test Lane 2 failure does not block Lane 1
- [x] Test Lane 1 failure does not block Lane 2
- [x] Test both lanes complete successfully in normal case
- [x] Test exceptions are captured and logged

### Files to Create
- `tests/integration/test_async_fork.py`

---

## Task 20: Update models/__init__.py

### Description
Export new models from the models package.

### Acceptance Criteria
- [x] Export InteractionAnalysis and related models from extraction_models
- [x] Export database models from db_models
- [x] Maintain existing exports

### Files to Modify
- `models/__init__.py`

---

## Task 21: Verify Deployment

### Description
Verify the deployment works correctly with the new Intelligence Layer.

### Acceptance Criteria
- [ ] DATABASE_URL configured in Railway environment
- [ ] Deployment succeeds without errors
- [ ] Health check passes
- [ ] Test WebSocket connection with transcript
- [ ] Verify intelligence data persisted to Postgres
- [ ] Verify existing Kinesis/EventBridge flow unaffected
- [ ] Test batch endpoint creates intelligence rows

### Verification Steps
1. Deploy to Railway
2. Check deployment logs for startup errors
3. Test WebSocket endpoint with sample audio
4. Query Postgres to verify InteractionSummaryEntry and InteractionInsight rows created
5. Verify Kinesis events still published
6. **Batch Endpoint Verification**: Send a POST request to `/batch/process` with a sample audio file and verify that `interaction_summary_entries` rows are created in Postgres

### Neon MCP Verification Workflow
```
# Step 1: Get correct Project ID
mcp_neon_list_projects({"limit": 100})

# Step 2: Use Project ID (word-word-numbers format) to query
mcp_neon_run_sql({
  "projectId": "<project-id-from-step-1>",
  "sql": "SELECT * FROM interaction_summary_entries ORDER BY created_at DESC LIMIT 5"
})

# Step 3: Verify batch upload rows specifically
mcp_neon_run_sql({
  "projectId": "<project-id-from-step-1>",
  "sql": "SELECT * FROM interaction_summary_entries WHERE interaction_type = 'batch_upload' ORDER BY created_at DESC LIMIT 5"
})
```

**CRITICAL**: Do NOT use Endpoint ID (ep-random-string) as projectId - this causes 404 errors.

---

## Task 22: Create Live Verification Script

### Description
Create a standalone script (`scripts/verify_intelligence_live.py`) that bypasses the API layer and invokes IntelligenceService directly to verify the intelligence pipeline with a real, high-token-count transcript against the live Neon database and OpenAI API.

### Acceptance Criteria
- [x] `scripts/verify_intelligence_live.py` created
- [x] Script reads from `test_payload.json` in project root
- [x] Script extracts `tenant_id` and `user_id` from payload headers
- [x] Script generates new `trace_id` (UUID v4) for each run
- [x] Script generates new `interaction_id` (UUID v4) for each run
- [x] Script instantiates IntelligenceService directly (bypasses API router)
- [x] Script calls `process_transcript()` with payload text
- [x] Script captures raw InteractionAnalysis output as JSON
- [x] Script queries Neon to verify created rows (summaries and insights)
- [x] Script generates artifact to `test_artifacts/run_{timestamp}.md`
- [x] Artifact contains: config, input snippet (first 500 chars), raw LLM JSON output, DB verification tables
- [x] Script performs teardown: DELETE rows by interaction_id
- [x] Script verifies cleanup complete
- [x] Script exits with non-zero status on any failure
- [x] Script logs all steps with timestamps

### Files to Create
- `scripts/verify_intelligence_live.py`

### Implementation Notes
```python
# Key structure of the script
async def main():
    # 1. SETUP
    payload = load_payload("test_payload.json")
    trace_id = str(uuid4())
    interaction_id = str(uuid4())
    tenant_id = payload["headers"]["X-Tenant-ID"]
    
    # 2. EXTRACT & PERSIST
    service = IntelligenceService()
    analysis = await service.process_transcript(
        cleaned_transcript=payload["text"],
        interaction_id=interaction_id,
        tenant_id=tenant_id,
        trace_id=trace_id,
        interaction_type="meeting"
    )
    
    # 3. VERIFY
    summaries, insights = await verify_db_rows(interaction_id)
    
    # 4. REPORT
    generate_artifact(analysis, summaries, insights, config)
    
    # 5. TEARDOWN
    await cleanup_rows(interaction_id)
```

---

## Task 23: Verify test_payload.json with AML Transcript

### Description
Verify that the `test_payload.json` file exists and contains the realistic, high-token-count AML meeting transcript with proper metadata and headers.

**NOTE**: This file already exists in the project root with the full AML transcript.

### Acceptance Criteria
- [x] `test_payload.json` exists in project root
- [x] Contains full AML meeting transcript (~15,000+ tokens)
- [x] Contains metadata object with: source, priority, tested_by, publisher_service, publisher_run_id, publisher_sent_at
- [x] Contains headers object with: X-Tenant-ID, X-User-ID, X-Trace-Id
- [x] JSON is valid and parseable
- [x] Transcript includes multi-speaker format (A:, B:, C:, etc.)

### Files (Already Exists)
- `test_payload.json`

### Reference Data
The transcript content is the AML (Application Modernization Lab) meeting between AWS and Lightbox discussing:
- AWS AML program overview
- Modernization pathways (EC2 Windows, SQL Server, Oracle RDS)
- Cost neutrality and service credits
- RIMS, PCR, Report Writer, RCM migrations
- Licensing considerations (BYOL, Enterprise Edition)
- EDP (Enterprise Discount Program) implications

---

## Task 24: Create test_artifacts Directory

### Description
Create the `test_artifacts/` directory and add it to `.gitignore` (artifacts should not be committed).

### Acceptance Criteria
- [x] `test_artifacts/` directory created
- [x] `.gitignore` updated to exclude `test_artifacts/` contents but keep directory
- [x] Add `.gitkeep` file to preserve empty directory in git

### Files to Create/Modify
- `test_artifacts/.gitkeep`
- `.gitignore`
