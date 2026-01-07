# Implementation Plan: Unified Ingestion Engine Upgrade

## Overview

This implementation plan transforms the live-transcription-fastapi service into a Unified Ingestion Engine with EnvelopeV1 schema, fan-out publishing to Kinesis/EventBridge, and a new text cleaning endpoint. Tasks are organized into 4 phases with property-based tests integrated throughout.

## Tasks

- [ ] 1. Phase 1: The Envelope Foundation
  - Create the core data models and enhance context extraction

- [x] 1.1 Create EnvelopeV1 and ContentModel in `models/envelope.py`
  - Define `ContentModel` with `text` (str) and `format` (str) fields
  - Define `EnvelopeV1` with all required fields per design
  - Implement JSON serialization config for datetime and UUID
  - Add `KinesisPayloadWrapper` model for Kinesis publishing
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10_

- [x] 1.2 Write property test for EnvelopeV1 schema validation
  - **Property 4: EnvelopeV1 Schema Validation**
  - Test that valid field combinations create valid instances
  - Test schema_version defaults to "v1"
  - Test extras defaults to empty dict
  - **Validates: Requirements 4.1-4.10**

- [x] 1.3 Write property test for EnvelopeV1 round-trip serialization
  - **Property 5: EnvelopeV1 Round-Trip Serialization**
  - Generate random valid EnvelopeV1 instances
  - Serialize to JSON, deserialize back
  - Verify all fields match original
  - **Validates: Requirements 4.11**

- [x] 1.4 Update `models/request_context.py` to add `trace_id` field
  - Add `trace_id: str` field to RequestContext dataclass
  - _Requirements: 1.5, 1.6_

- [x] 1.5 Refactor `utils/context_utils.py` for strict header validation
  - Create new `get_validated_context()` function
  - Implement X-Tenant-ID validation (required, must be UUID)
  - Implement X-User-ID validation (required, non-empty string)
  - Implement X-Trace-Id validation (optional, generate if missing)
  - Raise HTTPException 400 with descriptive messages on validation failure
  - Keep existing `get_request_context()` for backward compatibility
  - _Requirements: 1.1, 1.2, 1.5, 1.6, 1.7, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

- [x] 1.6 Write property tests for context extraction
  - **Property 1: Tenant ID UUID Validation**
  - **Property 2: User ID Required Validation**
  - **Property 3: Trace ID Generation and Preservation**
  - Test valid UUIDs are accepted, invalid strings rejected
  - Test non-empty strings accepted, empty/whitespace rejected
  - Test trace_id preservation and generation
  - **Validates: Requirements 1.1-1.7, 8.1-8.6**

- [x] 2. Checkpoint - Ensure foundation tests pass
  - Run all property tests for Phase 1
  - Ensure all tests pass, ask the user if questions arise

- [x] 3. Phase 2: The Publisher Upgrade
  - Refactor AWSEventPublisher for fan-out publishing

- [x] 3.1 Add boto3 Kinesis client initialization to `services/aws_event_publisher.py`
  - Add `KINESIS_STREAM_NAME` environment variable support (default: `eq-interactions-stream-dev`)
  - Initialize Kinesis client with graceful failure handling
  - Log warning if credentials not found, disable Kinesis publishing
  - _Requirements: 7.1_

- [x] 3.2 Implement `_build_kinesis_payload()` helper method
  - Accept EnvelopeV1 as input
  - Return wrapper dict with `envelope`, `trace_id`, `tenant_id`, `schema_version`
  - Serialize envelope using `model_dump(mode="json")`
  - _Requirements: 5.2, 6.1, 6.2, 6.3, 6.4, 6.5_

- [x] 3.3 Write property test for Kinesis wrapper structure
  - **Property 6: Kinesis Wrapper Structure**
  - Generate random EnvelopeV1 instances
  - Verify wrapper contains all required top-level fields
  - Verify envelope is complete JSON representation
  - **Validates: Requirements 5.2, 6.1-6.5**

- [x] 3.4 Implement `_publish_to_kinesis()` async method
  - Build wrapper payload using helper
  - Use `str(envelope.tenant_id)` as partition key
  - Call `put_record` on Kinesis client
  - Return sequence number on success, None on failure
  - Log errors with full context (interaction_id, tenant_id, error)
  - _Requirements: 5.1, 5.3, 7.2, 7.4_

- [x] 3.5 Write property test for partition key derivation
  - **Property 10: Partition Key Derivation**
  - Generate random tenant_id UUIDs
  - Verify partition key equals `str(tenant_id)`
  - **Validates: Requirements 5.3**

- [x] 3.6 Implement `publish_envelope()` async method with fan-out logic
  - Attempt Kinesis publish first
  - Attempt EventBridge publish second (using existing logic)
  - Return dict with `kinesis_sequence` and `eventbridge_id`
  - Never raise exceptions - log errors and continue
  - _Requirements: 5.1, 5.4, 5.5, 5.6, 5.7, 7.2, 7.3, 7.5_

- [x] 4. Checkpoint - Ensure publisher tests pass
  - Run all property tests for Phase 2
  - Ensure all tests pass, ask the user if questions arise

- [x] 5. Phase 3: The Endpoints
  - Update batch router and create text router

- [x] 5.1 Create request/response models in `models/text_request.py`
  - Define `TextCleanRequest` with `text` (required), `metadata` (optional), `source` (default "api")
  - Define `TextCleanResponse` with `raw_text`, `cleaned_text`, `interaction_id`
  - Add Pydantic validation for non-empty text
  - _Requirements: 3.2_

- [x] 5.2 Create `routers/text.py` with `POST /text/clean` endpoint
  - Import and use `get_validated_context()` for header validation
  - Validate text is not empty/whitespace-only (return 400)
  - Call `BatchCleanerService.clean_transcript()` for cleaning
  - Build EnvelopeV1 with `interaction_type="note"`
  - Call `publisher.publish_envelope()` (non-blocking on failure)
  - Return `TextCleanResponse`
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 5.3 Write property test for whitespace text rejection
  - **Property 9: Whitespace Text Rejection**
  - Generate strings of only whitespace characters
  - Verify HTTP 400 response
  - **Validates: Requirements 3.3**

- [x] 5.4 Refactor `routers/batch.py` to use EnvelopeV1 and new publisher
  - Replace `get_request_context()` with `get_validated_context()`
  - Build EnvelopeV1 with `interaction_type="transcript"` after cleaning
  - Call `publisher.publish_envelope()` instead of `publish_batch_completed_event()`
  - Update response to include `interaction_id`
  - _Requirements: 1.1, 1.2, 2.1, 2.2, 2.3, 2.4, 2.5_

- [x] 5.5 Write property tests for response schema and interaction type
  - **Property 7: Response Schema Completeness**
  - **Property 8: Interaction Type Assignment**
  - Verify batch responses contain required fields
  - Verify text responses contain required fields
  - Verify interaction_type is "transcript" for batch, "note" for text
  - **Validates: Requirements 2.3, 2.4, 3.4, 3.5**

- [x] 6. Checkpoint - Ensure endpoint tests pass
  - Run all property tests for Phase 3
  - Ensure all tests pass, ask the user if questions arise

- [x] 7. Phase 4: Wiring and Integration
  - Register new router and update environment configuration

- [x] 7.1 Register text router in `main.py`
  - Import `router` from `routers.text`
  - Add `app.include_router(router, prefix="/text", tags=["text"])`
  - _Requirements: 3.1_

- [x] 7.2 Update `.env.example` with new environment variables
  - Add `KINESIS_STREAM_NAME=eq-interactions-stream-dev`
  - Document the new variable purpose
  - _Requirements: 5.1_

- [x] 7.3 Update `requirements.txt` if needed
  - Verify boto3 is already included (should be)
  - Add any missing dependencies

- [x] 7.4 Write integration tests for full endpoint flows
  - Test `/batch/process` with valid headers returns expected response
  - Test `/text/clean` with valid headers returns expected response
  - Test missing headers return 400 errors
  - Mock AWS clients for isolation

- [x] 8. Final Checkpoint - Full test suite
  - Run complete test suite including all property tests
  - Verify all 10 correctness properties pass
  - Ensure all tests pass, ask the user if questions arise

## Notes

- All tasks including property-based tests are required for comprehensive coverage
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties using Hypothesis
- Unit tests validate specific examples and edge cases
- The existing `publish_batch_completed_event()` method should be preserved for backward compatibility but marked as deprecated
