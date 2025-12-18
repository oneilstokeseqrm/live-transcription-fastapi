"""Property-based tests for EventPublisher.

**Feature: stateless-stitcher, Property 2: Dual-Write Atomicity Attempt**
**Feature: stateless-stitcher, Property 3: Transcript Reconstruction Ordering**
"""
import pytest
from hypothesis import given, strategies as st
from services.event_publisher import EventPublisher
import fakeredis.aioredis
import uuid


@pytest.fixture
async def event_publisher():
    """Create EventPublisher with fake Redis client."""
    publisher = EventPublisher()
    # Replace with fake Redis for testing
    publisher.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return publisher


@pytest.mark.asyncio
@given(
    transcript=st.text(min_size=1, max_size=100),
    session_id=st.uuids()
)
async def test_dual_write_atomicity_both_operations_attempted(transcript, session_id):
    """
    **Feature: stateless-stitcher, Property 2: Dual-Write Atomicity Attempt**
    **Validates: Requirements 2.3**
    
    For any final transcript chunk, both Redis Stream write and Redis List write 
    operations should be attempted regardless of individual operation success.
    """
    publisher = EventPublisher()
    publisher.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    
    session_id_str = str(session_id)
    
    # Publish transcript
    await publisher.publish_transcript_event(
        transcript=transcript,
        metadata={"is_final": True},
        tenant_id="test_tenant",
        session_id=session_id_str
    )
    
    # Verify Stream write occurred
    stream_entries = await publisher.redis_client.xrange(publisher.stream_name)
    assert len(stream_entries) > 0, "Stream write should have occurred"
    
    # Verify List write occurred
    list_key = f"session:{session_id_str}:transcript"
    list_entries = await publisher.redis_client.lrange(list_key, 0, -1)
    assert len(list_entries) > 0, "List write should have occurred"
    assert list_entries[0] == transcript, "List should contain the transcript"


@pytest.mark.asyncio
@given(
    transcripts=st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=10)
)
async def test_transcript_reconstruction_ordering(transcripts):
    """
    **Feature: stateless-stitcher, Property 3: Transcript Reconstruction Ordering**
    **Validates: Requirements 3.2**
    
    For any session with N transcript chunks written to Redis List, retrieving via 
    get_final_transcript should return chunks in the exact order they were written.
    """
    publisher = EventPublisher()
    publisher.redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    
    session_id = str(uuid.uuid4())
    
    # Write transcripts in order
    for transcript in transcripts:
        await publisher.publish_transcript_event(
            transcript=transcript,
            metadata={"is_final": True},
            tenant_id="test_tenant",
            session_id=session_id
        )
    
    # Retrieve final transcript
    final_transcript = await publisher.get_final_transcript(session_id)
    
    # Verify order is preserved
    expected = " ".join(transcripts)
    assert final_transcript == expected, \
        f"Expected '{expected}' but got '{final_transcript}'"
