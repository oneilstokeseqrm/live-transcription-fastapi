# Implementation Plan: Stateless Stitcher Architecture

- [x] 1. Update EventPublisher with dual-write capability
  - Modify `publish_transcript_event` to accept `session_id` parameter
  - Implement Redis List write alongside existing Stream write
  - Add error handling for partial write failures
  - Set 24-hour TTL on session Redis Lists
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

- [x]* 1.1 Write property test for dual-write atomicity
  - **Property 2: Dual-Write Atomicity Attempt**
  - **Validates: Requirements 2.3**

- [x] 2. Implement transcript reconstruction method
  - Add `get_final_transcript(session_id)` method to EventPublisher
  - Retrieve all chunks from Redis List using LRANGE
  - Join chunks with single space characters
  - Delete Redis List after successful retrieval
  - Return empty string on errors with appropriate logging
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x]* 2.1 Write property test for transcript ordering
  - **Property 3: Transcript Reconstruction Ordering**
  - **Validates: Requirements 3.2**

- [ ]* 2.2 Write property test for join consistency
  - **Property 4: Transcript Join Consistency**
  - **Validates: Requirements 3.3**

- [ ]* 2.3 Write property test for cleanup after retrieval
  - **Property 6: Cleanup After Retrieval**
  - **Validates: Requirements 5.2**

- [x] 3. Add session ID generation to WebSocket endpoint
  - Import uuid module in main.py
  - Generate session_id using uuid.uuid4() on connection
  - Pass session_id to process_audio function
  - Propagate session_id to get_transcript handler
  - Include session_id in publish_transcript_event calls
  - _Requirements: 1.1, 1.2, 1.3, 1.5_

- [ ]* 3.1 Write property test for session ID immutability
  - **Property 1: Session ID Immutability**
  - **Validates: Requirements 1.5**

- [x] 4. Integrate transcript retrieval on disconnect
  - Add finally block to WebSocket endpoint
  - Call `get_final_transcript(session_id)` on connection close
  - Log the final transcript with session_id
  - Handle retrieval errors gracefully
  - _Requirements: 3.1, 4.5_

- [x] 5. Enhance error handling and resilience
  - Add timeout configuration to Redis client (5 seconds)
  - Implement try-except blocks for each Redis operation
  - Log errors with session_id context
  - Ensure WebSocket continues on Redis failures
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [ ]* 5.1 Write property test for error isolation
  - **Property 7: Error Isolation**
  - **Validates: Requirements 4.1, 4.2, 4.3**

- [x] 6. Add TTL management for session lists
  - Set EXPIRE on Redis List keys during creation
  - Verify TTL is set to 86400 seconds (24 hours)
  - Add logging for TTL operations
  - _Requirements: 5.1, 5.4_

- [ ]* 6.1 Write property test for TTL application
  - **Property 5: TTL Application**
  - **Validates: Requirements 5.1**

- [ ]* 6.2 Write unit tests for TTL edge cases
  - Test TTL on new session lists
  - Test cleanup on abnormal termination
  - Test expiration after 24 hours
  - _Requirements: 5.1, 5.4_

- [x] 7. Update logging with session context
  - Add session_id to all log statements in EventPublisher
  - Add session_id to WebSocket endpoint logs
  - Include session_id in error logs
  - _Requirements: 1.4_

- [ ]* 7.1 Write unit tests for logging
  - Test session_id appears in all log statements
  - Test error logs include session_id
  - _Requirements: 1.4_

- [ ] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Create deployment verification script
  - Create script using Railway MCP tools
  - Implement deployment status check
  - Implement log analysis for errors
  - Implement service health verification
  - Add verification checklist output
  - _Requirements: 6.2, 6.3, 6.4, 6.5_

- [ ]* 9.1 Write integration test for deployment verification
  - Test Railway MCP deployment status query
  - Test log retrieval and error detection
  - Test service health check
  - _Requirements: 6.2, 6.3, 6.4_

- [x] 10. Update environment variable handling
  - Verify DEEPGRAM_API_KEY is read from environment
  - Verify REDIS_URL with default fallback
  - Verify MOCK_TENANT_ID with default fallback
  - Add startup validation for required variables
  - Log error and exit if required variables missing
  - _Requirements: 7.1, 7.2, 7.3, 7.4_

- [ ]* 10.1 Write unit tests for configuration
  - Test environment variable reading
  - Test default value fallbacks
  - Test startup failure on missing required variables
  - _Requirements: 7.1, 7.2, 7.3, 7.4_

- [ ] 11. Update documentation
  - Update README.md with session management details
  - Document dual-write pattern
  - Document transcript retrieval process
  - Add deployment verification instructions
  - _Requirements: All_

- [x] 12. Create MeetingOutput Pydantic model
  - Create models directory if it doesn't exist
  - Define MeetingOutput with summary, action_items, and cleaned_transcript fields
  - Add Field descriptions for OpenAI Structured Outputs
  - Add type hints and validation
  - _Requirements: 10.1, 10.2, 10.3, 10.4_

- [ ]* 12.1 Write unit tests for MeetingOutput model
  - Test model instantiation with valid data
  - Test field validation
  - Test JSON serialization
  - _Requirements: 10.1, 10.2, 10.3, 10.4_

- [ ] 13. Implement CleanerService
  - Create services/cleaner_service.py
  - Initialize OpenAI async client with API key from environment
  - Read OPENAI_MODEL from environment with default "gpt-4o"
  - Implement RoboScribe-inspired system prompt
  - Implement clean_transcript method with Structured Outputs
  - Use configurable model in API calls
  - Add timeout handling (30s for <5000 words, 60s for larger)
  - Add error handling with fallback to raw transcript
  - Add logging with session_id and duration
  - _Requirements: 7.5, 8.1, 8.2, 8.3, 8.4, 8.5, 11.1, 11.2, 11.3, 11.4, 11.5_

- [ ]* 13.1 Write property test for content preservation
  - **Property 8: Cleaning Preserves Content**
  - **Validates: Requirements 9.5**

- [ ]* 13.2 Write property test for filler word removal
  - **Property 9: Filler Word Removal**
  - **Validates: Requirements 9.1**

- [ ]* 13.3 Write property test for structured output compliance
  - **Property 10: Structured Output Schema Compliance**
  - **Validates: Requirements 10.2, 10.3, 10.4**

- [ ]* 13.4 Write property test for timeout fallback
  - **Property 11: Cleaning Timeout Fallback**
  - **Validates: Requirements 11.3**

- [ ]* 13.5 Write unit tests for CleanerService
  - Test successful cleaning with mock OpenAI
  - Test timeout handling
  - Test API error handling
  - Test empty transcript handling
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 11.3, 11.4_

- [ ] 14. Integrate CleanerService into WebSocket endpoint
  - Import CleanerService in main.py
  - Instantiate CleanerService
  - Call clean_transcript after get_final_transcript in finally block
  - Send structured output to client via WebSocket
  - Include both cleaned and raw transcript in response
  - Handle cleaning errors gracefully
  - _Requirements: 8.1, 8.5_

- [ ]* 14.1 Write integration test for end-to-end cleaning flow
  - Test WebSocket connection through cleaning
  - Verify structured output format
  - Verify both raw and cleaned transcripts returned
  - _Requirements: 8.1, 8.5_

- [ ] 15. Update environment configuration for OpenAI
  - Add OPENAI_API_KEY to required environment variables
  - Add OPENAI_MODEL to optional environment variables with default "gpt-4o"
  - Update startup validation to check for OPENAI_API_KEY
  - Update .env.example with OPENAI_API_KEY and OPENAI_MODEL placeholders
  - Add OpenAI client initialization error handling
  - _Requirements: 7.4, 7.5, 7.6_

- [ ]* 15.1 Write unit tests for OpenAI configuration
  - Test OPENAI_API_KEY validation at startup
  - Test error handling for missing API key
  - _Requirements: 7.4, 7.5_

- [ ] 16. Add openai dependency
  - Add openai>=1.0.0 to requirements.txt
  - Ensure async client support
  - Document version requirements
  - _Requirements: 8.2, 8.3_

- [ ] 17. Create live verification script
  - Create scripts directory if it doesn't exist
  - Create scripts/verify_cleaning_live.py
  - Load environment variables from .env file
  - Define hardcoded messy transcript with filler words and poor punctuation
  - Initialize CleanerService and call clean_transcript
  - Print "Before" (original) transcript to console
  - Print "After" (cleaned) transcript to console
  - Print summary and action items
  - Make real API call to OpenAI (no mocks)
  - Add error handling and clear output formatting
  - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

- [ ] 18. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 19. Update documentation for CleanerService
  - Update README.md with cleaning pipeline details
  - Document MeetingOutput schema
  - Document RoboScribe prompt strategy
  - Document OPENAI_MODEL configuration option
  - Document live verification script usage
  - Add examples of cleaned output
  - Document timeout and error handling
  - _Requirements: All cleaning requirements_

- [ ] 20. Final Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
