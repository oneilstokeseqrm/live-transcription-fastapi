# Implementation Plan: Event-Driven Architecture for Batch Pipeline

## Phase 1: Infrastructure Automation

- [x] 1. Set up AWS infrastructure provisioning script
  - Create `scripts/setup_aws_infra.py` with AWSInfrastructureProvisioner class
  - Implement Dead Letter Queue creation with idempotency
  - Implement main SQS queue creation with DLQ configuration
  - Implement EventBridge rule creation with event pattern
  - Implement SQS target addition to EventBridge rule
  - Add comprehensive error handling and logging
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

- [ ]* 1.1 Write property test for infrastructure idempotency
  - **Property 6: Infrastructure Provisioning Idempotency**
  - **Validates: Requirements 7.6**

- [x] 2. Create event flow verification script
  - Create `scripts/verify_event_flow.py` with EventFlowVerifier class
  - Implement test event publishing to EventBridge
  - Implement SQS queue polling with timeout
  - Implement event structure validation
  - Implement test message cleanup
  - Add exit codes for different failure scenarios
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8_

- [ ]* 2.1 Write property test for event flow end-to-end
  - **Property 7: Event Flow End-to-End**
  - **Validates: Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 8.6**

- [x] 3. Update dependencies
  - Add boto3 to requirements.txt with pinned version
  - Update .env.example with AWS configuration variables
  - Update .env.example with multi-tenancy variables (MOCK_TENANT_ID, MOCK_USER_ID)
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

## Phase 2: Core Components

- [x] 4. Create data models
  - Create `models/request_context.py` with RequestContext dataclass
  - Create `models/batch_event.py` with BatchProcessingCompletedEvent Pydantic model
  - Create EventData nested model for transcript data
  - Add field validation and descriptions
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8_

- [x] 4.1 Write property test for event schema completeness
  - **Property 1: Event Schema Completeness**
  - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8**

- [x] 5. Implement context extraction utility
  - Create `utils/context_utils.py` with get_request_context function
  - Implement X-Tenant-ID header extraction with fallback to MOCK_TENANT_ID env var
  - Implement X-User-ID header extraction with fallback to MOCK_USER_ID env var
  - Implement X-Account-ID header extraction with null fallback
  - Implement UUID v4 validation for tenant_id
  - Implement interaction_id generation
  - Add comprehensive logging with interaction_id
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11_

- [x] 5.1 Write property test for context extraction fallback chain
  - **Property 2: Context Extraction Fallback Chain**
  - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11**

- [x] 6. Implement Event Publisher service
  - Create `services/aws_event_publisher.py` with AWSEventPublisher class
  - Initialize boto3 EventBridge client with region and credentials
  - Implement publish_batch_completed_event method
  - Construct event matching schema from Requirement 1
  - Implement event structure validation before publishing
  - Add comprehensive error handling for AWS API failures
  - Add logging for success and failure cases (never log full transcripts)
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10_

- [ ]* 6.1 Write unit tests for Event Publisher
  - Mock boto3 EventBridge client
  - Test successful event publishing
  - Test AWS API error handling
  - Test credential error handling
  - Test logging behavior
  - _Requirements: 14.1, 14.2, 14.3_

- [ ]* 6.2 Write property test for configuration defaults
  - **Property 4: Configuration Defaults**
  - **Validates: Requirements 11.1, 11.4, 11.5**

- [ ]* 6.3 Write property test for event publisher logging
  - **Property 5: Event Publisher Logging**
  - **Validates: Requirements 12.1, 12.2, 12.3, 12.8**

## Phase 3: Integration

- [x] 7. Integrate event publishing into batch router
  - Import get_request_context in routers/batch.py
  - Extract context at start of process_batch_audio function
  - Import AWSEventPublisher after cleaning completes
  - Call publish_batch_completed_event with context and transcripts
  - Wrap event publishing in try/except to ensure resilience
  - Log event publishing success/failure with interaction_id
  - Ensure response format unchanged (still returns raw and cleaned transcripts)
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10_

- [ ]* 7.1 Write property test for event publishing resilience
  - **Property 3: Event Publishing Resilience**
  - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5**

- [ ]* 7.2 Write unit tests for batch router integration
  - Mock EventPublisher
  - Test event publishing after cleaning
  - Test resilience to publishing failures
  - Test context extraction integration
  - Test response format unchanged
  - _Requirements: 14.4, 14.5_

- [x] 8. Add startup validation and configuration logging
  - Add AWS credential check at application startup
  - Log warning if credentials missing (disable event publishing)
  - Log EventPublisher configuration (region, bus name, source)
  - Add environment variable reading with defaults
  - _Requirements: 10.6, 10.7, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7_

## Phase 4: Verification and Testing

- [ ] 9. Run infrastructure setup
  - Execute scripts/setup_aws_infra.py locally or in CI/CD
  - Verify all AWS resources created successfully
  - Verify idempotency by running script again
  - Document queue URLs and rule ARN
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

- [ ] 10. Run event flow verification
  - Execute scripts/verify_event_flow.py
  - Verify test event flows from EventBridge to SQS
  - Verify event structure matches schema
  - Verify test message cleanup
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8_

- [ ]* 11. Run integration tests
  - Set up localstack for AWS services
  - Run integration tests for infrastructure provisioning
  - Run integration tests for event flow
  - Run integration tests for batch endpoint with events
  - Verify 80% code coverage
  - _Requirements: 14.6, 14.7_

- [ ] 12. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Phase 5: Deployment

- [ ] 13. Update Railway environment variables
  - Add AWS_ACCESS_KEY_ID to Railway
  - Add AWS_SECRET_ACCESS_KEY to Railway
  - Add AWS_REGION to Railway
  - Add MOCK_TENANT_ID to Railway
  - Add MOCK_USER_ID to Railway
  - Verify IAM permissions for PutEvents
  - _Requirements: 11.1, 11.2, 11.3, 11.6_

- [ ] 14. Deploy to Railway and verify
  - Merge to main branch
  - Verify deployment using Railway MCP
  - Check deployment logs for EventPublisher initialization
  - Test batch endpoint with real audio file
  - Verify event appears in SQS queue
  - Monitor CloudWatch metrics for event publishing
  - _Requirements: 3.10, 12.4, 12.5_

- [ ] 15. Final checkpoint - Production verification
  - Ensure all tests pass, ask the user if questions arise.
