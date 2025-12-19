# Requirements Document: Event-Driven Architecture for Batch Pipeline

## Introduction

This specification defines the implementation of an event-driven architecture for the batch (pre-recorded) audio processing pipeline. The system will decouple the batch processing service from downstream consumers (CRM systems, analytics platforms, data warehouses) using Amazon EventBridge and SQS. This architecture enables asynchronous processing, improves system resilience, and allows multiple consumers to process transcript events independently without modifying the core transcription service.

**Scope Constraint:** This architecture applies ONLY to the `routers/batch.py` endpoint (file uploads). The real-time WebSocket streaming pipeline remains unchanged.

## Glossary

- **Batch Pipeline**: The audio file upload and processing workflow exposed via the `/batch/process` endpoint
- **Interaction ID**: A UUID v4 identifier uniquely identifying a single batch processing request
- **Tenant ID**: A UUID v4 identifier representing the organization or workspace owning the transcript
- **User ID**: A string identifier representing the user who initiated the processing request
- **Account ID**: An optional string identifier for additional account-level context
- **EventBridge**: AWS service for event routing and filtering using event patterns
- **SQS Queue**: AWS Simple Queue Service for reliable message delivery and buffering
- **Event Publisher**: A service component responsible for publishing events to AWS EventBridge
- **BatchProcessingCompleted Event**: An event emitted when batch transcription and cleaning completes successfully
- **Event Schema Version**: A semantic version string indicating the event payload structure version
- **Event Pattern**: A JSON structure defining which events should be routed to specific targets
- **Standard Queue**: An SQS queue type offering high throughput with at-least-once delivery
- **Visibility Timeout**: The duration an SQS message is hidden from other consumers after being received
- **Message Retention**: The duration SQS retains messages before automatic deletion
- **Dead Letter Queue (DLQ)**: An SQS queue for messages that fail processing after maximum retry attempts
- **Infrastructure as Code (IaC)**: Automated provisioning of cloud resources using code (boto3)

## Requirements

### Requirement 1: Event Schema Definition

**User Story:** As a downstream consumer, I want a standardized, versioned event schema, so that I can reliably parse and process transcript events across different system versions.

#### Acceptance Criteria

1. WHEN defining the event schema, THE system SHALL include a version field with value "1.0"
2. WHEN defining the event schema, THE system SHALL include an interaction_id field of type UUID v4
3. WHEN defining the event schema, THE system SHALL include a tenant_id field of type UUID v4
4. WHEN defining the event schema, THE system SHALL include a user_id field of type string
5. WHEN defining the event schema, THE system SHALL include an account_id field of type string or null
6. WHEN defining the event schema, THE system SHALL include a timestamp field in ISO 8601 format
7. WHEN defining the event schema, THE system SHALL include a status field with value "completed"
8. WHEN defining the event schema, THE system SHALL include a data object containing cleaned_transcript and raw_transcript
9. WHEN publishing events, THE system SHALL validate the event structure matches the schema before sending to EventBridge
10. WHEN the schema version changes, THE system SHALL increment the version number following semantic versioning

### Requirement 2: Multi-Tenancy and Identity Extraction

**User Story:** As a platform operator, I want tenant and user identity extracted from request headers or environment variables, so that events can be properly attributed and routed in multi-tenant scenarios.

#### Acceptance Criteria

1. WHEN processing a batch request, THE system SHALL attempt to read X-Tenant-ID header from the request
2. IF X-Tenant-ID header is not present, THEN THE system SHALL read MOCK_TENANT_ID from environment variables
3. IF neither X-Tenant-ID nor MOCK_TENANT_ID is available, THEN THE system SHALL generate a new UUID v4 as tenant_id
4. WHEN processing a batch request, THE system SHALL attempt to read X-User-ID header from the request
5. IF X-User-ID header is not present, THEN THE system SHALL read MOCK_USER_ID from environment variables
6. IF neither X-User-ID nor MOCK_USER_ID is available, THEN THE system SHALL use "system" as the default user_id
7. WHEN processing a batch request, THE system SHALL attempt to read X-Account-ID header from the request
8. IF X-Account-ID header is not present, THEN THE system SHALL set account_id to null
9. WHEN extracting identity values, THE system SHALL validate that tenant_id is a valid UUID v4 format
10. WHEN validation fails, THE system SHALL log a warning and generate a new UUID v4
11. WHEN identity values are extracted, THE system SHALL log them with the interaction_id for traceability

### Requirement 3: Event Publishing Integration

**User Story:** As a backend developer, I want the batch processing endpoint to publish events when processing completes, so that downstream systems can react to new transcripts without polling.

#### Acceptance Criteria

1. WHEN the BatchCleanerService returns a cleaned transcript, THE system SHALL immediately publish a BatchProcessingCompleted event to EventBridge
2. WHEN publishing an event, THE system SHALL generate a new UUID v4 as the interaction_id
3. WHEN publishing an event, THE system SHALL extract tenant_id, user_id, and account_id from request headers or environment
4. WHEN publishing an event, THE system SHALL generate an ISO 8601 timestamp
5. WHEN publishing an event, THE system SHALL set status to "completed"
6. WHEN publishing an event, THE system SHALL set version to "1.0"
7. WHEN publishing an event, THE system SHALL nest raw_transcript and cleaned_transcript within a data object
8. WHEN publishing an event, THE system SHALL use the source identifier "com.yourapp.transcription"
9. WHEN publishing an event, THE system SHALL use the detail-type "BatchProcessingCompleted"
10. IF event publishing fails, THEN THE system SHALL still return the transcription results to the client

### Requirement 4: Event Publisher Service

**User Story:** As a system architect, I want a dedicated service for event publishing, so that event logic is separated from business logic and can be reused across endpoints.

#### Acceptance Criteria

1. WHEN creating the EventPublisher service, THE system SHALL initialize an EventBridge client using boto3
2. WHEN the EventPublisher is instantiated, THE system SHALL read AWS credentials from environment variables
3. WHEN the EventPublisher is instantiated, THE system SHALL read the AWS region from environment variables with default "us-east-1"
4. WHEN the EventPublisher is instantiated, THE system SHALL read the EventBridge bus name from environment variables with default "default"
5. WHEN the publish method is called, THE system SHALL accept interaction_id, tenant_id, user_id, account_id, and data as parameters
6. WHEN the publish method is called, THE system SHALL construct an event matching the schema defined in Requirement 1
7. WHEN the publish method is called, THE system SHALL validate all required fields are present
8. WHEN the publish method completes successfully, THE system SHALL log the event_id returned by EventBridge
9. IF the publish operation fails, THEN THE system SHALL log the error with interaction_id context and raise an exception
10. WHEN logging event operations, THE system SHALL never log the full transcript content (use length instead)

### Requirement 5: SQS Queue Infrastructure

**User Story:** As a DevOps engineer, I want an SQS queue provisioned to receive transcript events, so that downstream consumers can reliably process events at their own pace.

#### Acceptance Criteria

1. WHEN provisioning infrastructure, THE system SHALL create an SQS Standard Queue named "meeting-transcripts-queue"
2. WHEN creating the queue, THE system SHALL set the visibility timeout to 30 seconds
3. WHEN creating the queue, THE system SHALL set the message retention period to 14 days (1209600 seconds)
4. WHEN creating the queue, THE system SHALL configure a Dead Letter Queue for failed messages
5. WHEN creating the queue, THE system SHALL set the maximum receive count to 3 before moving to DLQ
6. WHEN creating the queue, THE system SHALL configure an access policy allowing events.amazonaws.com to SendMessage
7. WHEN the queue is created, THE system SHALL return the queue URL for use in EventBridge configuration

### Requirement 6: EventBridge Rule Configuration

**User Story:** As a system architect, I want an EventBridge rule that routes transcript events to the SQS queue, so that events are automatically delivered to consumers.

#### Acceptance Criteria

1. WHEN provisioning infrastructure, THE system SHALL create an EventBridge rule named "capture-transcripts-rule"
2. WHEN creating the rule, THE system SHALL define an event pattern matching source "com.yourapp.transcription"
3. WHEN creating the rule, THE system SHALL define an event pattern matching detail-type "BatchProcessingCompleted"
4. WHEN creating the rule, THE system SHALL set the rule state to "ENABLED"
5. WHEN creating the rule, THE system SHALL add the SQS queue as a target
6. WHEN adding the SQS target, THE system SHALL use the queue ARN as the target identifier
7. WHEN the rule is created, THE system SHALL verify the rule is active and routing events

### Requirement 7: Infrastructure Provisioning Automation

**User Story:** As a DevOps engineer, I want automated scripts to provision AWS infrastructure, so that the event-driven architecture can be deployed consistently across environments.

#### Acceptance Criteria

1. WHEN running the setup script, THE system SHALL create the Dead Letter Queue first
2. WHEN running the setup script, THE system SHALL create the main SQS queue with DLQ configuration
3. WHEN running the setup script, THE system SHALL create the EventBridge rule with event pattern
4. WHEN running the setup script, THE system SHALL add the SQS queue as a target to the rule
5. WHEN running the setup script, THE system SHALL verify all resources were created successfully
6. IF any resource already exists, THEN THE system SHALL skip creation and log a warning
7. WHEN the setup completes, THE system SHALL output the queue URL and rule ARN for verification

### Requirement 8: Event Flow Verification

**User Story:** As a developer, I want automated verification that events flow from EventBridge to SQS, so that I can confirm the infrastructure is correctly configured.

#### Acceptance Criteria

1. WHEN running the verification script, THE system SHALL publish a test event to EventBridge matching the schema
2. WHEN the test event is published, THE system SHALL include a unique test_interaction_id in the event detail
3. WHEN the test event is published, THE system SHALL poll the SQS queue for up to 30 seconds
4. WHEN polling the queue, THE system SHALL receive messages with a visibility timeout of 10 seconds
5. WHEN a message is received, THE system SHALL parse the EventBridge envelope and extract the event detail
6. WHEN the test event is found, THE system SHALL verify the interaction_id matches the published value
7. WHEN verification succeeds, THE system SHALL delete the test message from the queue
8. IF the test event is not received within 30 seconds, THEN THE system SHALL report a failure and exit with error code

### Requirement 9: Dependency Management

**User Story:** As a developer, I want AWS SDK dependencies properly managed, so that the application can interact with AWS services.

#### Acceptance Criteria

1. WHEN updating dependencies, THE system SHALL add boto3 to requirements.txt
2. WHEN specifying boto3, THE system SHALL pin the version to ensure reproducible builds
3. WHEN the application starts, THE system SHALL import boto3 without errors
4. WHEN boto3 is imported, THE system SHALL use the default credential chain (environment variables, IAM roles, or AWS config)
5. IF AWS credentials are not available, THEN THE system SHALL log a warning at startup and disable event publishing

### Requirement 10: Error Handling and Resilience

**User Story:** As a system operator, I want the batch endpoint to handle event publishing failures gracefully, so that transcription results are still returned to users even if event delivery fails.

#### Acceptance Criteria

1. IF EventBridge publishing fails, THEN THE system SHALL log the error with full context
2. IF EventBridge publishing fails, THEN THE system SHALL still return the transcription results to the client
3. WHEN logging event publishing errors, THE system SHALL include the interaction_id
4. WHEN logging event publishing errors, THE system SHALL include the exception type and message
5. WHEN logging event publishing errors, THE system SHALL include the full stack trace
6. IF AWS credentials are missing, THEN THE system SHALL disable event publishing and log a warning
7. WHEN event publishing is disabled, THE system SHALL log a warning for each batch request

### Requirement 11: Configuration Management

**User Story:** As a DevOps engineer, I want AWS configuration managed through environment variables, so that the service can be deployed across different AWS accounts and regions.

#### Acceptance Criteria

1. WHEN the service starts, THE system SHALL read AWS_REGION from environment variables with default "us-east-1"
2. WHEN the service starts, THE system SHALL read AWS_ACCESS_KEY_ID from environment variables (optional if using IAM roles)
3. WHEN the service starts, THE system SHALL read AWS_SECRET_ACCESS_KEY from environment variables (optional if using IAM roles)
4. WHEN the service starts, THE system SHALL read EVENTBRIDGE_BUS_NAME from environment variables with default "default"
5. WHEN the service starts, THE system SHALL read EVENT_SOURCE from environment variables with default "com.yourapp.transcription"
6. WHEN environment variables are updated in Railway, THE system SHALL use new values after redeployment
7. WHEN running locally, THE system SHALL support loading AWS credentials from ~/.aws/credentials

### Requirement 12: Logging and Observability

**User Story:** As a system operator, I want comprehensive logging of event publishing operations, so that I can monitor event delivery and troubleshoot issues.

#### Acceptance Criteria

1. WHEN an event is published successfully, THE system SHALL log the event_id at INFO level
2. WHEN an event is published successfully, THE system SHALL log the interaction_id for correlation
3. WHEN an event publishing fails, THE system SHALL log the error at ERROR level with full context
4. WHEN the EventPublisher is initialized, THE system SHALL log the AWS region and bus name
5. WHEN the batch endpoint completes, THE system SHALL log whether event publishing succeeded or failed
6. WHEN logging event operations, THE system SHALL include the event source and detail-type
7. WHEN logging event operations, THE system SHALL include the timestamp of the operation
8. WHEN logging transcript data, THE system SHALL log only the length, not the full content

### Requirement 13: Security and Access Control

**User Story:** As a security engineer, I want proper IAM policies and access controls, so that only authorized services can publish events and consume messages.

#### Acceptance Criteria

1. WHEN creating the SQS queue, THE system SHALL configure a resource policy allowing EventBridge to send messages
2. WHEN creating the SQS queue, THE system SHALL restrict SendMessage permission to the EventBridge service principal
3. WHEN the application publishes events, THE system SHALL use IAM credentials with PutEvents permission on EventBridge
4. WHEN consumers read from SQS, THE system SHALL require IAM credentials with ReceiveMessage and DeleteMessage permissions
5. WHEN running in Railway, THE system SHALL use environment variable credentials rather than IAM roles
6. WHEN storing AWS credentials, THE system SHALL never commit them to version control
7. WHEN logging AWS operations, THE system SHALL never log access keys or secret keys

### Requirement 14: Testing Strategy

**User Story:** As a developer, I want comprehensive tests for event publishing, so that I can verify the integration works correctly before deployment.

#### Acceptance Criteria

1. WHEN writing unit tests, THE system SHALL mock the boto3 EventBridge client
2. WHEN testing the EventPublisher, THE system SHALL verify the correct event structure is sent
3. WHEN testing the EventPublisher, THE system SHALL verify error handling for AWS API failures
4. WHEN testing the batch endpoint, THE system SHALL verify events are published after cleaning completes
5. WHEN testing the batch endpoint, THE system SHALL verify the endpoint returns results even if event publishing fails
6. WHEN writing integration tests, THE system SHALL use localstack or actual AWS resources
7. WHEN running integration tests, THE system SHALL verify events appear in the SQS queue with correct schema

### Requirement 15: Dead Letter Queue Handling

**User Story:** As a system operator, I want failed messages automatically moved to a Dead Letter Queue, so that I can investigate and reprocess failures without losing data.

#### Acceptance Criteria

1. WHEN creating the DLQ, THE system SHALL name it "meeting-transcripts-dlq"
2. WHEN creating the DLQ, THE system SHALL set the message retention period to 14 days
3. WHEN configuring the main queue, THE system SHALL set the DLQ ARN in the redrive policy
4. WHEN configuring the main queue, THE system SHALL set maxReceiveCount to 3
5. WHEN a message fails processing 3 times, THE system SHALL automatically move it to the DLQ
6. WHEN messages are in the DLQ, THE system SHALL retain them for manual inspection
7. WHEN the setup script runs, THE system SHALL output the DLQ URL for monitoring
