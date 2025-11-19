import redis.asyncio as redis
import json
import os
from datetime import datetime
from typing import Optional

class EventPublisher:
    def __init__(self):
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.stream_name = "transcription_events"
    
    async def publish_transcript_event(
        self, 
        transcript: str, 
        metadata: dict,
        tenant_id: Optional[str] = None
    ):
        """Publish completed transcript to Redis Stream"""
        event_data = {
            "event_type": "transcript_completed",
            "transcript": transcript,
            "metadata": json.dumps(metadata),
            "timestamp": datetime.utcnow().isoformat()
        }
        
        if tenant_id:
            event_data["tenant_id"] = tenant_id
        
        await self.redis_client.xadd(
            self.stream_name,
            event_data,
            maxlen=10000
        )
        
        print(f"✓ Published transcript to {self.stream_name}")
