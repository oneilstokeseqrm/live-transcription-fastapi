#!/usr/bin/env python3
"""
Event Flow Verification Script

This script verifies that events flow correctly from EventBridge to SQS:
1. Publishes a test event to EventBridge
2. Polls the SQS queue for up to 30 seconds
3. Verifies the test event arrives with correct structure
4. Cleans up the test message

Exit codes:
- 0: Verification succeeded
- 1: Test event not received within timeout
- 2: AWS credential error
- 3: Queue not found
- 4: Event structure validation failed
"""

import boto3
import json
import time
import uuid
import sys
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class EventFlowVerifier:
    """
    Verifies event flow from EventBridge to SQS.
    
    Process:
    1. Publish test event to EventBridge
    2. Poll SQS queue for up to 30 seconds
    3. Verify test event arrives with correct structure
    4. Clean up test message
    """
    
    def __init__(self, region: str = "us-east-1"):
        """
        Initialize AWS clients.
        
        Args:
            region: AWS region to use
        """
        self.region = region
        
        try:
            self.events_client = boto3.client("events", region_name=region)
            self.sqs_client = boto3.client("sqs", region_name=region)
            logger.info(f"Initialized verifier for region {region}")
        except Exception as e:
            logger.error(f"Failed to initialize AWS clients: {e}")
            sys.exit(2)
    
    def publish_test_event(self) -> str:
        """
        Publish test event to EventBridge.
        
        Returns:
            Test interaction_id for verification
        """
        test_id = str(uuid.uuid4())
        
        # Construct test event matching the schema
        event = {
            "version": "1.0",
            "interaction_id": test_id,
            "tenant_id": str(uuid.uuid4()),
            "user_id": "test-user",
            "account_id": None,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "status": "completed",
            "data": {
                "cleaned_transcript": "Test cleaned transcript for verification",
                "raw_transcript": "Test raw transcript for verification"
            }
        }
        
        try:
            # Publish to EventBridge
            response = self.events_client.put_events(
                Entries=[{
                    "Source": "com.yourapp.transcription",
                    "DetailType": "BatchProcessingCompleted",
                    "Detail": json.dumps(event),
                    "EventBusName": "default"
                }]
            )
            
            # Check for failures
            if response["FailedEntryCount"] > 0:
                logger.error(f"Failed to publish test event: {response['Entries']}")
                sys.exit(2)
            
            event_id = response["Entries"][0]["EventId"]
            logger.info(f"Published test event: interaction_id={test_id}, event_id={event_id}")
            
            return test_id
            
        except ClientError as e:
            logger.error(f"Failed to publish test event: {e}")
            sys.exit(2)
    
    def get_queue_url(self, queue_name: str) -> str:
        """
        Get SQS queue URL by name.
        
        Args:
            queue_name: Name of the queue
            
        Returns:
            Queue URL
        """
        try:
            response = self.sqs_client.get_queue_url(QueueName=queue_name)
            return response["QueueUrl"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "AWS.SimpleQueueService.NonExistentQueue":
                logger.error(f"Queue not found: {queue_name}")
                sys.exit(3)
            else:
                logger.error(f"Failed to get queue URL: {e}")
                sys.exit(2)
    
    def poll_queue(self, queue_url: str, test_id: str, timeout: int = 30) -> bool:
        """
        Poll SQS queue for test event.
        
        Args:
            queue_url: SQS queue URL
            test_id: Test interaction_id to look for
            timeout: Maximum seconds to poll
            
        Returns:
            True if test event found, False otherwise
        """
        logger.info(f"Polling queue for test event: interaction_id={test_id}, timeout={timeout}s")
        
        start_time = time.time()
        poll_interval = 2  # seconds
        
        while time.time() - start_time < timeout:
            try:
                # Receive messages from queue
                response = self.sqs_client.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=10,
                    VisibilityTimeout=10,
                    WaitTimeSeconds=poll_interval
                )
                
                messages = response.get("Messages", [])
                
                if not messages:
                    elapsed = time.time() - start_time
                    logger.info(f"No messages received, continuing to poll... ({elapsed:.1f}s elapsed)")
                    continue
                
                logger.info(f"Received {len(messages)} message(s) from queue")
                
                # Check each message
                for message in messages:
                    try:
                        # Parse EventBridge envelope
                        body = json.loads(message["Body"])
                        
                        # Extract event detail
                        if "detail" in body:
                            detail = body["detail"]
                            
                            # Check if this is our test event
                            if detail.get("interaction_id") == test_id:
                                logger.info(f"Found test event: interaction_id={test_id}")
                                
                                # Validate event structure
                                if self.validate_event_structure(detail):
                                    logger.info("Event structure validation passed")
                                    
                                    # Delete the test message
                                    self.sqs_client.delete_message(
                                        QueueUrl=queue_url,
                                        ReceiptHandle=message["ReceiptHandle"]
                                    )
                                    logger.info("Test message deleted from queue")
                                    
                                    return True
                                else:
                                    logger.error("Event structure validation failed")
                                    sys.exit(4)
                        
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse message body: {e}")
                        continue
                
            except ClientError as e:
                logger.error(f"Failed to receive messages: {e}")
                sys.exit(2)
        
        # Timeout reached
        logger.error(f"Test event not received within {timeout} seconds")
        return False
    
    def validate_event_structure(self, event: Dict[str, Any]) -> bool:
        """
        Validate event structure matches schema.
        
        Args:
            event: Event detail to validate
            
        Returns:
            True if valid, False otherwise
        """
        required_fields = [
            "version",
            "interaction_id",
            "tenant_id",
            "user_id",
            "timestamp",
            "status",
            "data"
        ]
        
        # Check required fields
        for field in required_fields:
            if field not in event:
                logger.error(f"Missing required field: {field}")
                return False
        
        # Check data object
        if "data" in event:
            data = event["data"]
            if "cleaned_transcript" not in data:
                logger.error("Missing cleaned_transcript in data")
                return False
            if "raw_transcript" not in data:
                logger.error("Missing raw_transcript in data")
                return False
        
        # Check version
        if event["version"] != "1.0":
            logger.error(f"Invalid version: {event['version']}")
            return False
        
        # Check status
        if event["status"] != "completed":
            logger.error(f"Invalid status: {event['status']}")
            return False
        
        # Validate UUID format for interaction_id and tenant_id
        try:
            uuid.UUID(event["interaction_id"], version=4)
            uuid.UUID(event["tenant_id"], version=4)
        except ValueError as e:
            logger.error(f"Invalid UUID format: {e}")
            return False
        
        logger.info("Event structure validation successful")
        return True
    
    def verify(self, queue_name: str = "meeting-transcripts-queue") -> bool:
        """
        Run full verification flow.
        
        Args:
            queue_name: Name of the SQS queue to verify
            
        Returns:
            True if verification succeeds, False otherwise
        """
        logger.info("Starting event flow verification...")
        
        # Step 1: Get queue URL
        logger.info(f"Step 1: Getting queue URL for {queue_name}...")
        queue_url = self.get_queue_url(queue_name)
        logger.info(f"Queue URL: {queue_url}")
        
        # Step 2: Publish test event
        logger.info("Step 2: Publishing test event to EventBridge...")
        test_id = self.publish_test_event()
        
        # Wait a moment for event to propagate
        logger.info("Waiting 2 seconds for event to propagate...")
        time.sleep(2)
        
        # Step 3: Poll queue for test event
        logger.info("Step 3: Polling SQS queue for test event...")
        success = self.poll_queue(queue_url, test_id, timeout=30)
        
        if success:
            logger.info("✓ Event flow verification PASSED")
            return True
        else:
            logger.error("✗ Event flow verification FAILED")
            return False


def main():
    """Main entry point for the verification script."""
    import os
    
    # Get region from environment or use default
    region = os.getenv("AWS_REGION", "us-east-1")
    
    # Get queue name from command line or use default
    queue_name = sys.argv[1] if len(sys.argv) > 1 else "meeting-transcripts-queue"
    
    # Create verifier and run
    verifier = EventFlowVerifier(region=region)
    success = verifier.verify(queue_name=queue_name)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
