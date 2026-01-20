"""
AWS Event Publisher Service

This service publishes structured events to AWS EventBridge and Kinesis for the batch
processing pipeline. It handles event construction, validation, and publishing
with comprehensive error handling and logging.

The publisher implements a fan-out pattern:
1. Publish to Kinesis for real-time streaming consumers
2. Publish to EventBridge for queue-based processors
"""

import boto3
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional
from botocore.exceptions import ClientError, NoCredentialsError
from models.batch_event import BatchProcessingCompletedEvent, EventData
from models.envelope import EnvelopeV1

logger = logging.getLogger(__name__)


class AWSEventPublisher:
    """
    Service for publishing events to AWS EventBridge and Kinesis.
    
    Handles event construction, validation, and publishing with
    comprehensive error handling and logging. Events are published
    to both Kinesis (for real-time streaming) and EventBridge (for
    queue-based consumers) using a fan-out pattern.
    """
    
    def __init__(self):
        """
        Initialize EventBridge and Kinesis clients with configuration from environment.
        
        Environment Variables:
            AWS_REGION: AWS region (default: us-east-1)
            AWS_ACCESS_KEY_ID: AWS access key (optional if using IAM)
            AWS_SECRET_ACCESS_KEY: AWS secret key (optional if using IAM)
            EVENTBRIDGE_BUS_NAME: Event bus name (default: default)
            EVENT_SOURCE: Event source identifier (default: com.yourapp.transcription)
            KINESIS_STREAM_NAME: Kinesis stream name (default: eq-interactions-stream-dev)
        """
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.bus_name = os.getenv("EVENTBRIDGE_BUS_NAME", "default")
        self.event_source = os.getenv("EVENT_SOURCE", "com.yourapp.transcription")
        self.kinesis_stream = os.getenv("KINESIS_STREAM_NAME", "eq-interactions-stream-dev")
        
        # Initialize EventBridge client
        self.client = self._init_eventbridge_client()
        
        # Initialize Kinesis client
        self.kinesis_client = self._init_kinesis_client()
    
    def _init_eventbridge_client(self):
        """
        Initialize the EventBridge client with graceful failure handling.
        
        Returns:
            boto3 EventBridge client or None if initialization fails
        """
        try:
            client = boto3.client("events", region_name=self.region)
            logger.info(
                f"EventBridge client initialized: region={self.region}, "
                f"bus={self.bus_name}, source={self.event_source}"
            )
            return client
        except NoCredentialsError:
            logger.warning(
                "AWS credentials not found. EventBridge publishing will be disabled. "
                "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
            )
            return None
        except Exception as e:
            logger.error(f"Failed to initialize EventBridge client: {e}")
            return None
    
    def _init_kinesis_client(self):
        """
        Initialize the Kinesis client with graceful failure handling.
        
        Returns:
            boto3 Kinesis client or None if initialization fails
        
        Requirements: 7.1
        """
        try:
            client = boto3.client("kinesis", region_name=self.region)
            logger.info(
                f"Kinesis client initialized: region={self.region}, "
                f"stream={self.kinesis_stream}"
            )
            return client
        except NoCredentialsError:
            logger.warning(
                "AWS credentials not found. Kinesis publishing will be disabled. "
                "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
            )
            return None
        except Exception as e:
            logger.error(f"Failed to initialize Kinesis client: {e}")
            return None
    
    def _build_kinesis_payload(self, envelope: EnvelopeV1) -> Dict[str, Any]:
        """
        Build the Kinesis payload wrapper for an EnvelopeV1.
        
        The wrapper structure duplicates key fields at the top level for easy
        routing by Step Functions and other consumers without parsing the full envelope.
        
        Args:
            envelope: The EnvelopeV1 instance to wrap
            
        Returns:
            Dict with structure:
            {
                "envelope": <complete EnvelopeV1 as JSON>,
                "trace_id": <trace_id for routing>,
                "tenant_id": <tenant_id as string>,
                "schema_version": "v1"
            }
        
        Requirements: 5.2, 6.1, 6.2, 6.3, 6.4, 6.5
        """
        return {
            "envelope": envelope.model_dump(mode="json"),
            "trace_id": envelope.trace_id,
            "tenant_id": str(envelope.tenant_id),
            "schema_version": envelope.schema_version
        }
    
    async def _publish_to_kinesis(self, envelope: EnvelopeV1) -> Optional[str]:
        """
        Publish wrapped envelope to Kinesis stream.
        
        Builds the wrapper payload and publishes to Kinesis using the tenant_id
        as the partition key for ordering guarantees within a tenant.
        
        Args:
            envelope: The EnvelopeV1 instance to publish
            
        Returns:
            Sequence number on success, None on failure
        
        Requirements: 5.1, 5.3, 7.2, 7.4
        """
        if self.kinesis_client is None:
            logger.warning(
                f"Kinesis client not initialized, skipping publish: "
                f"interaction_id={envelope.interaction_id}, "
                f"tenant_id={envelope.tenant_id}"
            )
            return None
        
        try:
            # Build wrapper payload
            wrapper = self._build_kinesis_payload(envelope)
            
            # Use tenant_id as partition key for ordering guarantees (Requirement 5.3)
            partition_key = str(envelope.tenant_id)
            
            # Publish to Kinesis
            response = self.kinesis_client.put_record(
                StreamName=self.kinesis_stream,
                Data=json.dumps(wrapper).encode("utf-8"),
                PartitionKey=partition_key
            )
            
            sequence_number = response["SequenceNumber"]
            logger.info(
                f"Kinesis publish success: "
                f"interaction_id={envelope.interaction_id}, "
                f"tenant_id={envelope.tenant_id}, "
                f"sequence={sequence_number}"
            )
            return sequence_number
            
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            
            logger.error(
                f"Kinesis publish failed (ClientError): "
                f"interaction_id={envelope.interaction_id}, "
                f"tenant_id={envelope.tenant_id}, "
                f"error_code={error_code}, "
                f"error_message={error_message}",
                exc_info=True
            )
            return None
            
        except Exception as e:
            logger.error(
                f"Kinesis publish failed: "
                f"interaction_id={envelope.interaction_id}, "
                f"tenant_id={envelope.tenant_id}, "
                f"error={type(e).__name__}: {str(e)}",
                exc_info=True
            )
            return None
    
    async def _publish_to_eventbridge(self, envelope: EnvelopeV1) -> Optional[str]:
        """
        Publish EnvelopeV1 to EventBridge.
        
        Constructs an EventBridge entry from the envelope and publishes it
        to the configured event bus.
        
        Args:
            envelope: The EnvelopeV1 instance to publish
            
        Returns:
            Event ID on success, None on failure
        
        Requirements: 5.4, 7.3
        """
        if self.client is None:
            logger.warning(
                f"EventBridge client not initialized, skipping publish: "
                f"interaction_id={envelope.interaction_id}, "
                f"tenant_id={envelope.tenant_id}"
            )
            return None
        
        try:
            # Construct EventBridge entry from envelope
            entry = {
                "Source": self.event_source,
                "DetailType": f"EnvelopeV1.{envelope.interaction_type}",
                "Detail": envelope.model_dump_json(),
                "EventBusName": self.bus_name
            }
            
            # Publish to EventBridge
            response = self.client.put_events(Entries=[entry])
            
            # Check for failures
            if response["FailedEntryCount"] > 0:
                failed_entry = response["Entries"][0]
                error_code = failed_entry.get("ErrorCode", "Unknown")
                error_message = failed_entry.get("ErrorMessage", "Unknown error")
                
                logger.error(
                    f"EventBridge publish failed: "
                    f"interaction_id={envelope.interaction_id}, "
                    f"tenant_id={envelope.tenant_id}, "
                    f"error_code={error_code}, "
                    f"error_message={error_message}"
                )
                return None
            
            # Success - extract event ID
            event_id = response["Entries"][0]["EventId"]
            
            logger.info(
                f"EventBridge publish success: "
                f"interaction_id={envelope.interaction_id}, "
                f"tenant_id={envelope.tenant_id}, "
                f"event_id={event_id}"
            )
            return event_id
            
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            
            logger.error(
                f"EventBridge publish failed (ClientError): "
                f"interaction_id={envelope.interaction_id}, "
                f"tenant_id={envelope.tenant_id}, "
                f"error_code={error_code}, "
                f"error_message={error_message}",
                exc_info=True
            )
            return None
            
        except Exception as e:
            logger.error(
                f"EventBridge publish failed: "
                f"interaction_id={envelope.interaction_id}, "
                f"tenant_id={envelope.tenant_id}, "
                f"error={type(e).__name__}: {str(e)}",
                exc_info=True
            )
            return None
    
    async def publish_envelope(self, envelope: EnvelopeV1) -> Dict[str, Optional[str]]:
        """
        Publish envelope to all configured destinations using fan-out pattern.
        
        This method implements the fan-out publishing strategy:
        1. Attempt Kinesis publish first (for real-time streaming)
        2. Attempt EventBridge publish second (for queue-based consumers)
        
        Publishing failures are logged but never raise exceptions - the user
        request should succeed regardless of publishing failures.
        
        Feature Flags:
            ENABLE_KINESIS_PUBLISHING (default: true) - Controls Kinesis publishing
            ENABLE_EVENTBRIDGE_PUBLISHING (default: true) - Controls EventBridge publishing
                (EventBridge routes to SQS via AWS EventBridge Rule)
        
        Args:
            envelope: The EnvelopeV1 instance to publish
            
        Returns:
            Dict with 'kinesis_sequence' and 'eventbridge_id' keys
            (None if publish failed or disabled for that destination)
        
        Requirements: 5.1, 5.4, 5.5, 5.6, 5.7, 7.2, 7.3, 7.5
        """
        # Check individual feature flags
        kinesis_enabled = os.getenv("ENABLE_KINESIS_PUBLISHING", "true").lower() != "false"
        eventbridge_enabled = os.getenv("ENABLE_EVENTBRIDGE_PUBLISHING", "true").lower() != "false"
        
        results: Dict[str, Optional[str]] = {
            "kinesis_sequence": None,
            "eventbridge_id": None
        }
        
        # 1. Attempt Kinesis publish first (Requirement 5.1)
        if kinesis_enabled:
            try:
                results["kinesis_sequence"] = await self._publish_to_kinesis(envelope)
            except Exception as e:
                # Should never happen since _publish_to_kinesis catches all exceptions,
                # but handle defensively
                logger.error(
                    f"Unexpected error in Kinesis publish: "
                    f"interaction_id={envelope.interaction_id}, "
                    f"tenant_id={envelope.tenant_id}, "
                    f"error={type(e).__name__}: {str(e)}",
                    exc_info=True
                )
        else:
            logger.info(
                f"Kinesis publishing disabled via ENABLE_KINESIS_PUBLISHING. "
                f"interaction_id={envelope.interaction_id}, tenant_id={envelope.tenant_id}"
            )
        
        # 2. Attempt EventBridge publish second (Requirement 5.4)
        # Continue even if Kinesis failed (Requirement 5.5)
        if eventbridge_enabled:
            try:
                results["eventbridge_id"] = await self._publish_to_eventbridge(envelope)
            except Exception as e:
                # Should never happen since _publish_to_eventbridge catches all exceptions,
                # but handle defensively
                logger.error(
                    f"Unexpected error in EventBridge publish: "
                    f"interaction_id={envelope.interaction_id}, "
                    f"tenant_id={envelope.tenant_id}, "
                    f"error={type(e).__name__}: {str(e)}",
                    exc_info=True
                )
        else:
            logger.info(
                f"EventBridge publishing disabled via ENABLE_EVENTBRIDGE_PUBLISHING. "
                f"interaction_id={envelope.interaction_id}, tenant_id={envelope.tenant_id}"
            )
        
        # Log summary of publish results (Requirement 5.7)
        kinesis_status = "disabled" if not kinesis_enabled else ("success" if results["kinesis_sequence"] else "failed")
        eventbridge_status = "disabled" if not eventbridge_enabled else ("success" if results["eventbridge_id"] else "failed")
        
        if results["kinesis_sequence"] or results["eventbridge_id"]:
            logger.info(
                f"Envelope published: "
                f"interaction_id={envelope.interaction_id}, "
                f"tenant_id={envelope.tenant_id}, "
                f"kinesis={kinesis_status}, "
                f"eventbridge={eventbridge_status}"
            )
        elif kinesis_enabled or eventbridge_enabled:
            # At least one was enabled but both failed (Requirement 5.6) - log but don't raise
            logger.warning(
                f"All enabled publish destinations failed: "
                f"interaction_id={envelope.interaction_id}, "
                f"tenant_id={envelope.tenant_id}, "
                f"kinesis={kinesis_status}, "
                f"eventbridge={eventbridge_status}"
            )
        else:
            # Both disabled
            logger.info(
                f"All publishing disabled via configuration: "
                f"interaction_id={envelope.interaction_id}, "
                f"tenant_id={envelope.tenant_id}"
            )
        
        return results
    
    def publish_batch_completed_event(
        self,
        interaction_id: str,
        tenant_id: str,
        user_id: str,
        account_id: Optional[str],
        raw_transcript: str,
        cleaned_transcript: str
    ) -> str:
        """
        Publish a BatchProcessingCompleted event to EventBridge.
        
        This method constructs a properly formatted event, validates it against
        the schema, and publishes it to EventBridge. The event will be routed
        to downstream consumers via the configured EventBridge rule and SQS queue.
        
        Args:
            interaction_id: Unique identifier for this processing request
            tenant_id: Tenant/organization identifier (UUID v4)
            user_id: User identifier
            account_id: Optional account identifier
            raw_transcript: Original transcript from Deepgram
            cleaned_transcript: Cleaned transcript from CleanerService
            
        Returns:
            Event ID from EventBridge
            
        Raises:
            Exception: If event publishing fails
        """
        # Check if client is available
        if self.client is None:
            logger.warning(
                f"Event publishing disabled (no AWS credentials). "
                f"interaction_id={interaction_id}"
            )
            raise Exception("EventBridge client not initialized")
        
        # Construct event detail matching schema
        event_detail = {
            "version": "1.0",
            "interaction_id": interaction_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "account_id": account_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "status": "completed",
            "data": {
                "cleaned_transcript": cleaned_transcript,
                "raw_transcript": raw_transcript
            }
        }
        
        # Validate event structure using Pydantic model
        try:
            validated_event = BatchProcessingCompletedEvent(**event_detail)
            logger.debug(
                f"Event validation passed: interaction_id={interaction_id}, "
                f"cleaned_length={len(cleaned_transcript)}, "
                f"raw_length={len(raw_transcript)}"
            )
        except Exception as e:
            logger.error(
                f"Event validation failed: interaction_id={interaction_id}, "
                f"error={str(e)}"
            )
            raise
        
        # Construct EventBridge entry
        entry = {
            "Source": self.event_source,
            "DetailType": "BatchProcessingCompleted",
            "Detail": json.dumps(event_detail),
            "EventBusName": self.bus_name
        }
        
        # Publish to EventBridge
        try:
            response = self.client.put_events(Entries=[entry])
            
            # Check for failures
            if response["FailedEntryCount"] > 0:
                failed_entry = response["Entries"][0]
                error_code = failed_entry.get("ErrorCode", "Unknown")
                error_message = failed_entry.get("ErrorMessage", "Unknown error")
                
                logger.error(
                    f"Event publishing failed: interaction_id={interaction_id}, "
                    f"error_code={error_code}, error_message={error_message}"
                )
                raise Exception(f"EventBridge put_events failed: {error_code} - {error_message}")
            
            # Success - extract event ID
            event_id = response["Entries"][0]["EventId"]
            
            logger.info(
                f"Event published successfully: interaction_id={interaction_id}, "
                f"event_id={event_id}, source={self.event_source}, "
                f"detail_type=BatchProcessingCompleted, "
                f"timestamp={event_detail['timestamp']}"
            )
            
            return event_id
            
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]
            
            logger.error(
                f"AWS API error publishing event: interaction_id={interaction_id}, "
                f"error_code={error_code}, error_message={error_message}",
                exc_info=True
            )
            raise
            
        except Exception as e:
            logger.error(
                f"Unexpected error publishing event: interaction_id={interaction_id}, "
                f"error={type(e).__name__}: {str(e)}",
                exc_info=True
            )
            raise
