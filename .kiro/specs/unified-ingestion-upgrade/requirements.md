# Requirements Document

## Introduction

This specification defines the requirements for upgrading the live-transcription-fastapi service into a **Unified Ingestion Engine**. The upgrade transforms the service from a simple transcription tool into a multi-input ingestion platform that supports audio uploads, raw text ingestion, and deep ecosystem integration via AWS Kinesis and SQS fan-out publishing.

The upgrade addresses three core needs:
1. **Frontend Integration**: Formalize the API contract for "Record-then-Ship" audio uploads with strict identity headers
2. **Text Ingestion**: Add a new route to ingest and clean raw text (notes, legacy documents) without audio processing
3. **Ecosystem Integration**: Refactor the Event Publisher to use a standardized "Envelope V1" schema with fan-out to both Kinesis and SQS/EventBridge

## Glossary

- **Unified_Ingestion_Engine**: The upgraded service capable of processing multiple input types (audio, text) and publishing standardized events
- **EnvelopeV1**: The standardized event schema (version 1) used for all published events across the ecosystem
- **Fan_Out_Publisher**: The service component responsible for publishing events to multiple destinations (Kinesis, SQS/EventBridge)
- **Identity_Headers**: HTTP headers containing tenant, user, and trace context (X-Tenant-ID, X-User-ID, X-Trace-Id)
- **Kinesis_Stream**: AWS Kinesis Data Stream (`eq-interactions-stream-dev`) for real-time event streaming
- **Interaction_Type**: Classification of content source (e.g., "transcript", "note", "document")
- **Content_Model**: Nested structure containing the actual text content and its format
- **Partition_Key**: Kinesis partition key derived from tenant_id for ordering guarantees

## Requirements

### Requirement 1: Identity Header Enforcement

**User Story:** As a platform operator, I want all ingestion endpoints to enforce identity headers, so that every interaction is properly attributed to a tenant and user for audit and routing purposes.

#### Acceptance Criteria

1. WHEN a request is received at `/batch/process`, THE Unified_Ingestion_Engine SHALL require the `X-Tenant-ID` header as a valid UUID
2. WHEN a request is received at `/batch/process`, THE Unified_Ingestion_Engine SHALL require the `X-User-ID` header as a non-empty string
3. WHEN a request is received at `/text/clean`, THE Unified_Ingestion_Engine SHALL require the `X-Tenant-ID` header as a valid UUID
4. WHEN a request is received at `/text/clean`, THE Unified_Ingestion_Engine SHALL require the `X-User-ID` header as a non-empty string
5. WHEN the `X-Trace-Id` header is not provided, THE Unified_Ingestion_Engine SHALL generate a new UUID v4 trace identifier
6. WHEN the `X-Trace-Id` header is provided, THE Unified_Ingestion_Engine SHALL validate it as a valid UUID and use it for tracing
7. IF a required identity header is missing or invalid, THEN THE Unified_Ingestion_Engine SHALL return HTTP 400 with a descriptive error message

### Requirement 2: Batch Audio Processing Endpoint

**User Story:** As a frontend developer, I want to upload recorded audio files for transcription and cleaning, so that I can integrate voice capture into my application.

#### Acceptance Criteria

1. THE Unified_Ingestion_Engine SHALL accept multipart file uploads at `POST /batch/process`
2. WHEN an audio file is uploaded, THE Unified_Ingestion_Engine SHALL accept formats: `audio/webm`, `audio/wav`, `audio/mpeg`, `audio/flac`, `audio/mp4`
3. WHEN processing completes successfully, THE Unified_Ingestion_Engine SHALL return a JSON response containing `raw_transcript`, `cleaned_transcript`, and `interaction_id`
4. WHEN processing completes successfully, THE Unified_Ingestion_Engine SHALL publish an EnvelopeV1 event with `interaction_type` set to "transcript"
5. IF transcription fails, THEN THE Unified_Ingestion_Engine SHALL return HTTP 500 with error details

### Requirement 3: Text Cleaning Endpoint

**User Story:** As a developer, I want to submit raw text for cleaning without audio processing, so that I can ingest notes, legacy documents, and other text content into the ecosystem.

#### Acceptance Criteria

1. THE Unified_Ingestion_Engine SHALL accept JSON requests at `POST /text/clean`
2. WHEN a text cleaning request is received, THE Unified_Ingestion_Engine SHALL accept a JSON body with `text` (required string) and `metadata` (optional object) fields
3. WHEN the `text` field is empty or contains only whitespace, THE Unified_Ingestion_Engine SHALL return HTTP 400 with validation error
4. WHEN text cleaning completes successfully, THE Unified_Ingestion_Engine SHALL return a JSON response containing `raw_text`, `cleaned_text`, and `interaction_id`
5. WHEN text cleaning completes successfully, THE Unified_Ingestion_Engine SHALL publish an EnvelopeV1 event with `interaction_type` set to "note"
6. IF cleaning fails, THEN THE Unified_Ingestion_Engine SHALL return the original text with an error flag rather than failing the request

### Requirement 4: EnvelopeV1 Event Schema

**User Story:** As a platform architect, I want all events to conform to a standardized schema, so that downstream consumers can reliably process events from any source.

#### Acceptance Criteria

1. THE EnvelopeV1 model SHALL include `schema_version` field defaulting to "v1"
2. THE EnvelopeV1 model SHALL include `tenant_id` as a required UUID field
3. THE EnvelopeV1 model SHALL include `user_id` as a required string field to support various ID formats (Auth0 IDs, type-prefixed IDs, etc.)
4. THE EnvelopeV1 model SHALL include `interaction_type` as a required string field (e.g., "transcript", "note")
5. THE EnvelopeV1 model SHALL include `content` as a required nested object with `text` (string) and `format` (string) fields
6. THE EnvelopeV1 model SHALL include `timestamp` as a required datetime field in ISO 8601 format
7. THE EnvelopeV1 model SHALL include `source` as a required string field identifying the origin (e.g., "web-mic", "upload", "api")
8. THE EnvelopeV1 model SHALL include `extras` as an optional dictionary for flexible metadata
9. THE EnvelopeV1 model SHALL include `interaction_id` as an optional UUID field
10. THE EnvelopeV1 model SHALL include `trace_id` as an optional string field for distributed tracing
11. WHEN serializing an EnvelopeV1 to JSON, THE Unified_Ingestion_Engine SHALL produce valid JSON that can be deserialized back to an equivalent EnvelopeV1 object

### Requirement 5: Fan-Out Event Publishing

**User Story:** As a platform operator, I want events published to both Kinesis and SQS/EventBridge, so that real-time streaming consumers and queue-based processors can both receive events.

#### Acceptance Criteria

1. WHEN an event is ready for publishing, THE Fan_Out_Publisher SHALL first attempt to publish to AWS Kinesis stream `eq-interactions-stream-dev`
2. WHEN publishing to Kinesis, THE Fan_Out_Publisher SHALL wrap the EnvelopeV1 in a payload structure containing `envelope`, `trace_id`, `tenant_id`, and `schema_version` fields
3. WHEN publishing to Kinesis, THE Fan_Out_Publisher SHALL use `str(envelope.tenant_id)` as the partition key for ordering guarantees
4. WHEN Kinesis publishing succeeds, THE Fan_Out_Publisher SHALL then publish to SQS/EventBridge using the existing mechanism
5. IF Kinesis publishing fails, THEN THE Fan_Out_Publisher SHALL log the error and continue to attempt SQS/EventBridge publishing
6. IF both Kinesis and SQS/EventBridge publishing fail, THEN THE Fan_Out_Publisher SHALL log errors but NOT fail the user request
7. WHEN publishing succeeds to any destination, THE Fan_Out_Publisher SHALL log the successful publish with event identifiers

### Requirement 6: Kinesis Payload Wrapper

**User Story:** As a Step Functions developer, I want Kinesis records to include metadata outside the envelope, so that I can route and filter events without parsing the full envelope.

#### Acceptance Criteria

1. WHEN publishing to Kinesis, THE Fan_Out_Publisher SHALL structure the payload as: `{"envelope": <envelope_json>, "trace_id": "<trace_id>", "tenant_id": "<tenant_id>", "schema_version": "v1"}`
2. THE Kinesis payload wrapper SHALL include the complete EnvelopeV1 JSON under the `envelope` key
3. THE Kinesis payload wrapper SHALL duplicate `trace_id` at the top level for easy access
4. THE Kinesis payload wrapper SHALL duplicate `tenant_id` at the top level for partition key visibility
5. THE Kinesis payload wrapper SHALL include `schema_version` at the top level for version routing

### Requirement 7: Error Resilience

**User Story:** As a user, I want my requests to succeed even if downstream event publishing fails, so that I always receive my processed content.

#### Acceptance Criteria

1. IF Kinesis client initialization fails due to missing credentials, THEN THE Fan_Out_Publisher SHALL log a warning and disable Kinesis publishing
2. IF a Kinesis put_record call fails, THEN THE Fan_Out_Publisher SHALL log the error with full context and continue processing
3. IF an EventBridge put_events call fails, THEN THE Fan_Out_Publisher SHALL log the error with full context and continue processing
4. WHEN any publishing error occurs, THE Fan_Out_Publisher SHALL include `interaction_id`, `tenant_id`, and error details in the log message
5. THE Unified_Ingestion_Engine SHALL return successful responses to users regardless of publishing failures

### Requirement 8: Context Extraction Enhancement

**User Story:** As a developer, I want robust context extraction that validates headers and provides clear error messages, so that I can quickly diagnose integration issues.

#### Acceptance Criteria

1. WHEN extracting context, THE Unified_Ingestion_Engine SHALL validate `X-Tenant-ID` is a valid UUID v4
2. WHEN extracting context, THE Unified_Ingestion_Engine SHALL validate `X-User-ID` is a non-empty string
3. WHEN extracting context, THE Unified_Ingestion_Engine SHALL validate `X-Trace-Id` (if provided) is a valid UUID
4. IF `X-Tenant-ID` is missing, THEN THE Unified_Ingestion_Engine SHALL raise an HTTP 400 error with message "X-Tenant-ID header is required"
5. IF `X-User-ID` is missing, THEN THE Unified_Ingestion_Engine SHALL raise an HTTP 400 error with message "X-User-ID header is required"
6. IF `X-Tenant-ID` is not a valid UUID, THEN THE Unified_Ingestion_Engine SHALL raise an HTTP 400 error with message "X-Tenant-ID must be a valid UUID"
