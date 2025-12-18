# Requirements Document: Stateless Stitcher Architecture

## Introduction

This specification defines the architectural upgrade to the live-transcription-fastapi service to implement a "Stateless Stitcher" pattern. The system will maintain transcript persistence through Redis dual-write operations, enabling full conversation reconstruction without maintaining in-memory state. This upgrade ensures transcript durability, enables session replay capabilities, and provides a foundation for multi-tenant transcript management.

## Glossary

- **Session**: A single WebSocket connection lifecycle from establishment to termination
- **Session ID**: A UUID v4 identifier uniquely identifying a WebSocket session
- **Transcript Chunk**: A single final transcript segment received from Deepgram
- **Redis Stream**: An append-only log structure for real-time event distribution
- **Redis List**: An ordered collection structure for sequential data storage
- **EventPublisher**: The service component responsible for publishing transcript events to Redis
- **Stateless Stitcher**: An architecture pattern where transcript reconstruction occurs at session end rather than maintaining in-memory state
- **Dual-Write**: The pattern of writing the same logical data to two different storage locations simultaneously
- **CleanerService**: The service component responsible for processing raw transcripts through an LLM to produce cleaned, structured output
- **MeetingOutput**: A Pydantic model defining the structured output schema (summary, action_items, cleaned_transcript)
- **RoboScribe Prompt**: An LLM system prompt strategy that cleans transcripts while preserving speaker authenticity

## Requirements

### Requirement 1: Session Identification

**User Story:** As a system architect, I want every WebSocket connection to have a unique identifier, so that transcripts can be associated with specific conversation sessions.

#### Acceptance Criteria

1. WHEN a WebSocket connection is established, THE system SHALL generate a unique session_id using UUID v4 format
2. WHEN the session_id is generated, THE system SHALL propagate it to all internal handlers and service layers
3. WHEN transcript events are published, THE system SHALL include the session_id in all event metadata
4. WHEN logging operations occur, THE system SHALL include the session_id for traceability
5. WHILE a WebSocket connection remains active, THE session_id SHALL remain immutable

### Requirement 2: Redis Dual-Write Pattern

**User Story:** As a developer, I want transcript chunks written to both a stream and a persistent list, so that real-time consumers can process events while maintaining full session history.

#### Acceptance Criteria

1. WHEN a final transcript chunk is received, THE EventPublisher SHALL write the complete event object to the Redis Stream
2. WHEN a final transcript chunk is received, THE EventPublisher SHALL append the plain text transcript to a Redis List keyed by `session:{session_id}:transcript`
3. WHEN performing dual-write operations, THE system SHALL attempt both writes regardless of individual operation success
4. IF either write operation fails, THEN THE system SHALL log the error with session_id and continue processing
5. WHEN writing to the Redis List, THE system SHALL set a TTL of 24 hours on the key

### Requirement 3: Transcript Reconstruction

**User Story:** As a system operator, I want to retrieve the complete conversation transcript when a session ends, so that full conversations can be stored or analyzed.

#### Acceptance Criteria

1. WHEN a WebSocket connection closes, THE system SHALL call `get_final_transcript` with the session_id
2. WHEN `get_final_transcript` is invoked, THE system SHALL retrieve all chunks from the Redis List in order
3. WHEN chunks are retrieved, THE system SHALL join them with single space characters
4. WHEN the final transcript is assembled, THE system SHALL return the complete text as a single string
5. WHEN transcript retrieval completes, THE system SHALL delete the Redis List to prevent memory bloat

### Requirement 4: Error Handling and Resilience

**User Story:** As a system administrator, I want the system to handle failures gracefully, so that partial transcripts are not lost and the service remains available.

#### Acceptance Criteria

1. IF a Redis connection fails during dual-write, THEN THE system SHALL log the error and continue accepting audio
2. IF the Redis Stream write fails, THEN THE system SHALL still attempt the Redis List write
3. IF the Redis List write fails, THEN THE system SHALL still attempt the Redis Stream write
4. WHEN Redis operations are performed, THE system SHALL include timeout values of 5 seconds
5. IF transcript retrieval fails, THEN THE system SHALL return an empty string and log the error

### Requirement 5: Session Cleanup and Resource Management

**User Story:** As a system operator, I want session data to be automatically cleaned up, so that Redis memory usage remains bounded.

#### Acceptance Criteria

1. WHEN creating a Redis List for a session, THE system SHALL set an expiration time of 24 hours
2. WHEN a session transcript is successfully retrieved, THE system SHALL delete the Redis List immediately
3. WHEN the Redis Stream exceeds 10,000 entries, THE system SHALL automatically trim older entries
4. IF a session terminates abnormally, THEN THE Redis List SHALL expire automatically after 24 hours
5. WHEN cleanup operations fail, THE system SHALL log the error but not interrupt service operation

### Requirement 6: Deployment Verification

**User Story:** As a DevOps engineer, I want automated deployment verification using Railway MCP, so that I can confirm successful deployments without manual intervention.

#### Acceptance Criteria

1. WHEN code is merged to main branch, THE system SHALL automatically deploy to Railway
2. WHEN deployment completes, THE verification process SHALL query deployment status using Railway MCP
3. WHEN checking deployment status, THE system SHALL verify the status is "SUCCESS"
4. WHEN reviewing logs, THE system SHALL check the last 100 log lines for errors
5. IF deployment verification fails, THEN THE system SHALL document the failure and trigger rollback procedures

### Requirement 7: Configuration Management

**User Story:** As a developer, I want environment-specific configuration managed through environment variables, so that the service can run in different environments without code changes.

#### Acceptance Criteria

1. WHEN the service starts, THE system SHALL read DEEPGRAM_API_KEY from environment variables
2. WHEN the service starts, THE system SHALL read REDIS_URL from environment variables with default "redis://localhost:6379"
3. WHEN the service starts, THE system SHALL read MOCK_TENANT_ID from environment variables with default "default_org"
4. WHEN the service starts, THE system SHALL read OPENAI_API_KEY from environment variables
5. WHEN the service starts, THE system SHALL read OPENAI_MODEL from environment variables with default "gpt-4o"
6. IF required environment variables are missing, THEN THE system SHALL log an error and fail to start
7. WHEN environment variables are updated in Railway, THE system SHALL use new values after redeployment

### Requirement 8: Transcript Cleaning and Structuring

**User Story:** As a user, I want my raw transcripts automatically cleaned and structured into a polished document with summary and action items, so that I can immediately use the output without manual editing.

#### Acceptance Criteria

1. WHEN a WebSocket session terminates, THE CleanerService SHALL receive the raw stitched transcript
2. WHEN the CleanerService processes a transcript, THE system SHALL send it to OpenAI GPT-4o with the RoboScribe-inspired cleaning prompt
3. WHEN requesting LLM processing, THE system SHALL use OpenAI Structured Outputs with a Pydantic model to ensure reliable parsing
4. WHEN the LLM returns results, THE system SHALL extract the cleaned transcript, summary, and action items
5. WHEN cleaning completes, THE system SHALL return a structured MeetingOutput object containing all three components

### Requirement 9: Transcript Cleaning Quality

**User Story:** As a content editor, I want the cleaning process to preserve speaker authenticity while improving readability, so that transcripts remain accurate and quotable.

#### Acceptance Criteria

1. WHEN cleaning a transcript, THE system SHALL remove filler words such as "um", "uh", and "like"
2. WHEN cleaning a transcript, THE system SHALL remove word duplications such as "the the"
3. WHEN cleaning a transcript, THE system SHALL add appropriate punctuation for readability
4. WHEN cleaning a transcript, THE system SHALL fix basic grammar errors while preserving speaker voice
5. WHEN cleaning a transcript, THE system SHALL NOT add words or content not present in the original
6. WHEN cleaning a transcript, THE system SHALL preserve speaker tags exactly as written
7. WHEN cleaning a transcript, THE system SHALL maintain natural speech patterns and self-corrections

### Requirement 10: Structured Output Schema

**User Story:** As a downstream system, I want transcript outputs in a consistent structured format, so that I can reliably parse and process the results.

#### Acceptance Criteria

1. WHEN defining the output schema, THE system SHALL create a Pydantic model named MeetingOutput
2. WHEN defining MeetingOutput, THE system SHALL include a summary field of type string
3. WHEN defining MeetingOutput, THE system SHALL include an action_items field of type List[str]
4. WHEN defining MeetingOutput, THE system SHALL include a cleaned_transcript field of type string
5. WHEN calling OpenAI, THE system SHALL use the response_format parameter with the Pydantic model
6. WHEN parsing LLM responses, THE system SHALL validate against the Pydantic schema automatically

### Requirement 11: Cleaning Service Performance

**User Story:** As a system operator, I want transcript cleaning to complete within reasonable time limits, so that users receive results promptly after session termination.

#### Acceptance Criteria

1. WHEN processing transcripts under 5000 words, THE CleanerService SHALL complete within 30 seconds
2. WHEN processing transcripts over 5000 words, THE CleanerService SHALL complete within 60 seconds
3. IF cleaning exceeds timeout limits, THEN THE system SHALL return the raw transcript with an error flag
4. WHEN LLM requests fail, THE system SHALL log the error and return the raw transcript
5. WHEN cleaning completes, THE system SHALL log the processing duration with session_id context

### Requirement 12: Live Cleaning Verification

**User Story:** As a developer, I want to verify the cleaning prompt quality against the real OpenAI API, so that I can validate the prompt produces high-quality results before deployment.

#### Acceptance Criteria

1. WHEN running the verification script, THE system SHALL load credentials from the local .env file
2. WHEN the verification script executes, THE system SHALL make a real API call to OpenAI without mocks
3. WHEN processing the test transcript, THE system SHALL use a hardcoded messy transcript with filler words and poor punctuation
4. WHEN the cleaning completes, THE system SHALL print both the original and cleaned transcripts to the console
5. WHEN the verification script runs, THE system SHALL display the summary and action items extracted from the transcript
