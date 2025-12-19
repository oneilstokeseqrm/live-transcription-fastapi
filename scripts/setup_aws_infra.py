#!/usr/bin/env python3
"""
AWS Infrastructure Provisioning Script for Event-Driven Architecture

This script provisions all AWS resources required for the event-driven batch pipeline:
1. Dead Letter Queue (DLQ) for failed messages
2. Main SQS Queue with DLQ configuration
3. EventBridge Rule with event pattern
4. SQS Queue as target for EventBridge Rule

All operations are idempotent - safe to run multiple times.
"""

import boto3
import json
import logging
import sys
from typing import Dict, Any, Optional
from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AWSInfrastructureProvisioner:
    """
    Provisions AWS infrastructure for event-driven architecture.
    
    Creates:
    1. Dead Letter Queue (DLQ)
    2. Main SQS Queue with DLQ configuration
    3. EventBridge Rule with event pattern
    4. SQS Queue as target for EventBridge Rule
    
    All operations are idempotent - safe to run multiple times.
    """
    
    def __init__(self, region: str = "us-east-1"):
        """
        Initialize AWS clients.
        
        Args:
            region: AWS region to provision resources in
        """
        self.region = region
        self.sqs_client = boto3.client("sqs", region_name=region)
        self.events_client = boto3.client("events", region_name=region)
        
        # Get AWS account ID
        try:
            sts_client = boto3.client("sts")
            self.account_id = sts_client.get_caller_identity()["Account"]
            logger.info(f"Initialized provisioner for account {self.account_id} in region {region}")
        except Exception as e:
            logger.error(f"Failed to get AWS account ID: {e}")
            raise
    
    def create_dlq(self) -> Dict[str, str]:
        """
        Create Dead Letter Queue.
        
        Returns:
            Dictionary with queue_url and queue_arn
        """
        queue_name = "meeting-transcripts-dlq"
        
        try:
            # Try to create the queue
            response = self.sqs_client.create_queue(
                QueueName=queue_name,
                Attributes={
                    "MessageRetentionPeriod": "1209600"  # 14 days
                }
            )
            queue_url = response["QueueUrl"]
            logger.info(f"Created DLQ: {queue_name}")
            
        except ClientError as e:
            if e.response["Error"]["Code"] == "QueueAlreadyExists":
                logger.warning(f"DLQ already exists: {queue_name}")
                # Get existing queue URL
                response = self.sqs_client.get_queue_url(QueueName=queue_name)
                queue_url = response["QueueUrl"]
            else:
                logger.error(f"Failed to create DLQ: {e}")
                raise
        
        # Get queue ARN
        attributes = self.sqs_client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["QueueArn"]
        )
        queue_arn = attributes["Attributes"]["QueueArn"]
        
        logger.info(f"DLQ URL: {queue_url}")
        logger.info(f"DLQ ARN: {queue_arn}")
        
        return {
            "queue_url": queue_url,
            "queue_arn": queue_arn
        }
    
    def create_main_queue(self, dlq_arn: str) -> Dict[str, str]:
        """
        Create main SQS queue with DLQ configuration.
        
        Args:
            dlq_arn: ARN of the Dead Letter Queue
            
        Returns:
            Dictionary with queue_url and queue_arn
        """
        queue_name = "meeting-transcripts-queue"
        
        # Configure redrive policy
        redrive_policy = {
            "deadLetterTargetArn": dlq_arn,
            "maxReceiveCount": "3"
        }
        
        try:
            # Try to create the queue
            response = self.sqs_client.create_queue(
                QueueName=queue_name,
                Attributes={
                    "VisibilityTimeout": "30",  # 30 seconds
                    "MessageRetentionPeriod": "1209600",  # 14 days
                    "RedrivePolicy": json.dumps(redrive_policy)
                }
            )
            queue_url = response["QueueUrl"]
            logger.info(f"Created main queue: {queue_name}")
            
        except ClientError as e:
            if e.response["Error"]["Code"] == "QueueAlreadyExists":
                logger.warning(f"Main queue already exists: {queue_name}")
                # Get existing queue URL
                response = self.sqs_client.get_queue_url(QueueName=queue_name)
                queue_url = response["QueueUrl"]
            else:
                logger.error(f"Failed to create main queue: {e}")
                raise
        
        # Get queue ARN
        attributes = self.sqs_client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=["QueueArn"]
        )
        queue_arn = attributes["Attributes"]["QueueArn"]
        
        # Set queue policy to allow EventBridge to send messages
        queue_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "events.amazonaws.com"
                    },
                    "Action": "sqs:SendMessage",
                    "Resource": queue_arn,
                    "Condition": {
                        "ArnEquals": {
                            "aws:SourceArn": f"arn:aws:events:{self.region}:{self.account_id}:rule/capture-transcripts-rule"
                        }
                    }
                }
            ]
        }
        
        self.sqs_client.set_queue_attributes(
            QueueUrl=queue_url,
            Attributes={
                "Policy": json.dumps(queue_policy)
            }
        )
        logger.info("Set queue policy to allow EventBridge access")
        
        logger.info(f"Main queue URL: {queue_url}")
        logger.info(f"Main queue ARN: {queue_arn}")
        
        return {
            "queue_url": queue_url,
            "queue_arn": queue_arn
        }
    
    def create_eventbridge_rule(self) -> str:
        """
        Create EventBridge rule with event pattern.
        
        Returns:
            Rule ARN
        """
        rule_name = "capture-transcripts-rule"
        
        # Define event pattern
        event_pattern = {
            "source": ["com.yourapp.transcription"],
            "detail-type": ["BatchProcessingCompleted"]
        }
        
        try:
            # Try to create the rule
            response = self.events_client.put_rule(
                Name=rule_name,
                EventPattern=json.dumps(event_pattern),
                State="ENABLED",
                Description="Routes batch transcription completion events to SQS queue"
            )
            rule_arn = response["RuleArn"]
            logger.info(f"Created EventBridge rule: {rule_name}")
            
        except ClientError as e:
            logger.error(f"Failed to create EventBridge rule: {e}")
            raise
        
        logger.info(f"Rule ARN: {rule_arn}")
        
        return rule_arn
    
    def add_queue_target(self, rule_name: str, queue_arn: str):
        """
        Add SQS queue as target to EventBridge rule.
        
        Args:
            rule_name: Name of the EventBridge rule
            queue_arn: ARN of the SQS queue
        """
        try:
            # Add target to rule
            self.events_client.put_targets(
                Rule=rule_name,
                Targets=[
                    {
                        "Id": "1",
                        "Arn": queue_arn
                    }
                ]
            )
            logger.info(f"Added SQS queue as target to rule: {rule_name}")
            
        except ClientError as e:
            logger.error(f"Failed to add queue target: {e}")
            raise
    
    def provision_all(self) -> Dict[str, str]:
        """
        Provision all infrastructure components.
        
        Returns:
            Dictionary with all resource identifiers
        """
        logger.info("Starting infrastructure provisioning...")
        
        # Step 1: Create DLQ
        logger.info("Step 1: Creating Dead Letter Queue...")
        dlq_info = self.create_dlq()
        
        # Step 2: Create main queue with DLQ configuration
        logger.info("Step 2: Creating main SQS queue...")
        queue_info = self.create_main_queue(dlq_info["queue_arn"])
        
        # Step 3: Create EventBridge rule
        logger.info("Step 3: Creating EventBridge rule...")
        rule_arn = self.create_eventbridge_rule()
        
        # Step 4: Add queue as target to rule
        logger.info("Step 4: Adding SQS queue as target...")
        self.add_queue_target("capture-transcripts-rule", queue_info["queue_arn"])
        
        logger.info("Infrastructure provisioning completed successfully!")
        
        # Return all resource identifiers
        result = {
            "dlq_url": dlq_info["queue_url"],
            "dlq_arn": dlq_info["queue_arn"],
            "queue_url": queue_info["queue_url"],
            "queue_arn": queue_info["queue_arn"],
            "rule_arn": rule_arn
        }
        
        logger.info("\n=== Resource Summary ===")
        for key, value in result.items():
            logger.info(f"{key}: {value}")
        
        return result


def main():
    """Main entry point for the provisioning script."""
    try:
        # Get region from environment or use default
        import os
        region = os.getenv("AWS_REGION", "us-east-1")
        
        # Create provisioner and run
        provisioner = AWSInfrastructureProvisioner(region=region)
        result = provisioner.provision_all()
        
        # Exit successfully
        sys.exit(0)
        
    except Exception as e:
        logger.error(f"Provisioning failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
