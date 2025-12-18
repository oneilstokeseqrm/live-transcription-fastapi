import redis.asyncio as redis
import json
import os
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

class EventPublisher:
    def __init__(self):
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis_client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5
        )
        self.stream_name = "transcription_events"
    
    async def publish_transcript_event(
        self, 
        transcript: str, 
        metadata: dict,
        tenant_id: Optional[str] = None,
        session_id: Optional[str] = None
    ):
        """Publish completed transcript to Redis Stream and List (dual-write)"""
        event_data = {
            "event_type": "transcript_completed",
            "transcript": transcript,
            "metadata": json.dumps(metadata),
            "timestamp": datetime.utcnow().isoformat()
        }
        
        if tenant_id:
            event_data["tenant_id"] = tenant_id
        
        if session_id:
            event_data["session_id"] = session_id
        
        # Dual-write: Stream write (real-time)
        try:
            await self.redis_client.xadd(
                self.stream_name,
                event_data,
                maxlen=10000
            )
            logger.info(f"Published transcript to stream: session_id={session_id}")
        except redis.RedisError as e:
            logger.error(f"Stream write failed: session_id={session_id}, error={e}")
        
        # Dual-write: List write (persistence)
        if session_id:
            try:
                list_key = f"session:{session_id}:transcript"
                await self.redis_client.rpush(list_key, transcript)
                await self.redis_client.expire(list_key, 86400)  # 24 hour TTL
                logger.info(f"Appended transcript to list: session_id={session_id}")
            except redis.RedisError as e:
                logger.error(f"List write failed: session_id={session_id}, error={e}")
    
    async def get_final_transcript(self, session_id: str) -> str:
        """Retrieve and reconstruct full session transcript from Redis List"""
        try:
            list_key = f"session:{session_id}:transcript"
            chunks = await self.redis_client.lrange(list_key, 0, -1)
            
            if not chunks:
                logger.warning(f"No transcript chunks found: session_id={session_id}")
                return ""
            
            # Join chunks with single space
            full_transcript = " ".join(chunks)
            
            # Cleanup: delete the list after retrieval
            await self.redis_client.delete(list_key)
            logger.info(f"Retrieved and cleaned up transcript: session_id={session_id}, chunks={len(chunks)}")
            
            return full_transcript
            
        except redis.RedisError as e:
            logger.error(f"Transcript retrieval failed: session_id={session_id}, error={e}")
            return ""
