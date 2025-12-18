#!/usr/bin/env python3
"""
Local verification script for Stateless Stitcher logic.
Tests the dual-write and transcript reconstruction using fakeredis.
"""
import asyncio
import os
import sys
import uuid
from dotenv import load_dotenv

# Add parent directory to path to import services
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.event_publisher import EventPublisher
import fakeredis.aioredis

# Load environment variables
load_dotenv()


async def main():
    """Run local verification of stitcher logic."""
    print("=" * 80)
    print("STATELESS STITCHER LOCAL VERIFICATION")
    print("=" * 80)
    print("(Using fakeredis for testing - no Redis server required)")
    
    # Initialize EventPublisher with fake Redis
    publisher = EventPublisher()
    publisher.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    
    # Generate a test session ID
    session_id = str(uuid.uuid4())
    print(f"\n📝 Test Session ID: {session_id}")
    
    # Define test transcript chunks
    test_chunks = [
        "Hello, this is the first chunk.",
        "This is the second chunk of the transcript.",
        "And finally, this is the third chunk."
    ]
    
    print("\n🔄 Writing transcript chunks to Redis...")
    print("-" * 80)
    
    # Manually push chunks to Redis List (simulating dual-write)
    for i, chunk in enumerate(test_chunks, 1):
        print(f"  {i}. Writing: '{chunk}'")
        await publisher.publish_transcript_event(
            transcript=chunk,
            metadata={"is_final": True, "chunk_number": i},
            tenant_id="test_org",
            session_id=session_id
        )
    
    print("-" * 80)
    print("✅ All chunks written successfully")
    
    # Retrieve the final transcript
    print("\n🔍 Retrieving final transcript using get_final_transcript()...")
    print("-" * 80)
    
    final_transcript = await publisher.get_final_transcript(session_id)
    
    print("\n📄 FINAL TRANSCRIPT:")
    print("-" * 80)
    print(final_transcript)
    print("-" * 80)
    
    # Verify correctness
    expected = " ".join(test_chunks)
    
    print("\n🧪 VERIFICATION:")
    print("-" * 80)
    print(f"Expected: '{expected}'")
    print(f"Got:      '{final_transcript}'")
    print("-" * 80)
    
    if final_transcript == expected:
        print("\n✅ VERIFICATION PASSED!")
        print("   - Chunks were written in order")
        print("   - Chunks were joined with single spaces")
        print("   - Transcript reconstruction works correctly")
    else:
        print("\n❌ VERIFICATION FAILED!")
        print("   - Transcript does not match expected output")
        sys.exit(1)
    
    # Verify cleanup (list should be deleted)
    print("\n🧹 Verifying cleanup...")
    list_key = f"session:{session_id}:transcript"
    remaining = await publisher.redis_client.lrange(list_key, 0, -1)
    
    if not remaining:
        print("✅ Redis List was properly cleaned up after retrieval")
    else:
        print(f"⚠️  Warning: Redis List still contains {len(remaining)} items")
    
    print("\n" + "=" * 80)
    print("VERIFICATION COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
