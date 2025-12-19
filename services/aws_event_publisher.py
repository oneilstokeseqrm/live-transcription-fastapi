"""
AWS Event Publisher Service

This service publishes structured events to AWS EventBridge for the batch
processing pipeline. It handles event construction, validation, and publishing
with comprehensive error handling and logging.
"""

import boto3
import json
import logging
import os
from datetime import datetime
from typing import Optional
from botocore.exceptions import ClientError, NoCredentialsError
from models.batch_event import BatchProcessingCompletedEvent, EventData

logger = logging.getLogger(__name__)


class AWSEventPublisher:
    """
    Service for publishing events to AWS EventBridge.
    
    Handles event construction, validation, and publishing with
    comprehensive error handling and logging. Events are published
    to EventBridge which routes them to downstream consumers via SQS.
    """
    
    def __init__(self):
        """
        Initialize EventBridge client with configuration from environment.
        
        Environment Variables:
            AWS_REGION: AWS region (default: us-east-1)
            AWS_ACCESS_KEY_ID: AWS access key (optional if using IAM)
            AWS_SECRET_ACCESS_KEY: AWS secret key (optional if using IAM)
            EVENTBRIDGE_BUS_NAME: Event bus name (default: default)
            EVENT_SOURCE: Event source identifier (default: com.yourapp.transcription)
        """
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.bus_name = os.getenv("EVENTBRIDGE_BUS_NAME", "default")
        self.event_source = os.getenv("EVENT_SOURCE", "com.yourapp.transcription")
        
        # Initialize boto3 client
        try:
            self.client = boto3.client("events", region_name=self.region)
            
            logger.info(
                f"EventPublisher initialized: region={self.region}, "
                f"bus={self.bus_name}, source={self.event_source}"
            )
        except NoCredentialsError:
            logger.warning(
                "AWS credentials not found. Event publishing will be disabled. "
                "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables."
            )
            self.client = None
        except Exception as e:
            logger.error(f"Failed to initialize EventBridge client: {e}")
            self.client = None
    
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
