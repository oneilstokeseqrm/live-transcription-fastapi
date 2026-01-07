#!/usr/bin/env python3
"""
Live Kinesis Connectivity Verification Script

This script verifies that the AWSEventPublisher can successfully publish
EnvelopeV1 events to the configured Kinesis stream. It performs a real
publish operation (not mocked) to confirm end-to-end connectivity.

Usage:
    python scripts/verify_kinesis_live.py

Requirements:
    - AWS credentials configured (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    - KINESIS_STREAM_NAME environment variable (or uses default)
    - Valid AWS region configuration
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from uuid import uuid4

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from models.envelope import ContentModel, EnvelopeV1
from services.aws_event_publisher import AWSEventPublisher

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def verify_kinesis_live() -> bool:
    """
    Verify live Kinesis connectivity by publishing a test envelope.
    
    Returns:
        True if publish succeeded, False otherwise
    """
    # Load environment variables
    load_dotenv()
    
    logger.info("=" * 60)
    logger.info("LIVE KINESIS CONNECTIVITY VERIFICATION")
    logger.info("=" * 60)
    
    # Step 1: Initialize AWSEventPublisher
    logger.info("Step 1: Initializing AWSEventPublisher...")
    publisher = AWSEventPublisher()
    
    if publisher.kinesis_client is None:
        logger.error("FAILED: Kinesis client not initialized")
        logger.error("Check AWS credentials and region configuration")
        return False
    
    logger.info(f"  ✓ Kinesis client initialized")
    logger.info(f"  ✓ Stream: {publisher.kinesis_stream}")
    logger.info(f"  ✓ Region: {publisher.region}")
    
    # Step 2: Construct test EnvelopeV1
    logger.info("\nStep 2: Constructing test EnvelopeV1...")
    
    test_tenant_id = uuid4()
    test_interaction_id = uuid4()
    test_trace_id = f"test-trace-{uuid4().hex[:8]}"
    
    envelope = EnvelopeV1(
        tenant_id=test_tenant_id,
        user_id="test-user-verification",
        interaction_type="test",
        content=ContentModel(
            text="Live Kinesis verification test message",
            format="plain"
        ),
        timestamp=datetime.now(timezone.utc),
        source="api",
        interaction_id=test_interaction_id,
        trace_id=test_trace_id,
        extras={"verification": True, "script": "verify_kinesis_live.py"}
    )
    
    logger.info(f"  ✓ tenant_id: {test_tenant_id}")
    logger.info(f"  ✓ interaction_id: {test_interaction_id}")
    logger.info(f"  ✓ interaction_type: test")
    logger.info(f"  ✓ trace_id: {test_trace_id}")
    
    # Step 3: Call publish_envelope
    logger.info("\nStep 3: Publishing envelope to Kinesis...")
    
    try:
        results = await publisher.publish_envelope(envelope)
    except Exception as e:
        logger.error(f"FAILED: Exception during publish: {e}")
        return False
    
    # Step 4: Assert kinesis_sequence is NOT None
    logger.info("\nStep 4: Verifying publish results...")
    
    kinesis_sequence = results.get("kinesis_sequence")
    eventbridge_id = results.get("eventbridge_id")
    
    if kinesis_sequence is None:
        logger.error("FAILED: kinesis_sequence is None")
        logger.error("The publish operation did not return a sequence number")
        logger.error("Check Kinesis stream permissions and configuration")
        return False
    
    # Step 5: Log success details
    logger.info("\n" + "=" * 60)
    logger.info("✅ VERIFICATION SUCCESSFUL!")
    logger.info("=" * 60)
    logger.info(f"  Kinesis Sequence Number: {kinesis_sequence}")
    logger.info(f"  EventBridge Event ID: {eventbridge_id or 'N/A'}")
    logger.info(f"  Stream: {publisher.kinesis_stream}")
    logger.info(f"  Partition Key: {str(test_tenant_id)}")
    logger.info(f"  Tenant ID: {test_tenant_id}")
    logger.info(f"  Interaction ID: {test_interaction_id}")
    logger.info("=" * 60)
    
    return True


def main():
    """Main entry point."""
    logger.info("Starting live Kinesis verification...")
    
    success = asyncio.run(verify_kinesis_live())
    
    if success:
        logger.info("\n🎉 Live Kinesis connectivity verified successfully!")
        sys.exit(0)
    else:
        logger.error("\n❌ Live Kinesis verification failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
