import redis.asyncio as redis
import json
import os
import logging
from datetime import datetime
from typing import Optional, List

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
    
    async def publish_structured_segment(
        self,
        channel: int,
        speaker: str,
        text: str,
        timestamp: float,
        confidence: float,
        metadata: dict,
        tenant_id: str,
        session_id: str,
    ):
        """Publish a structured transcript segment for desktop multichannel sessions.

        Stores JSON segments in Redis List for later assembly into a diarized
        transcript. Each segment includes channel, speaker label, timestamp,
        and confidence score.
        """
        segment = json.dumps({
            "ch": channel,
            "speaker": speaker,
            "text": text,
            "ts": timestamp,
            "conf": confidence,
        })

        # Stream write (real-time consumers)
        event_data = {
            "event_type": "transcript_completed",
            "transcript": text,
            "metadata": json.dumps(metadata),
            "timestamp": datetime.utcnow().isoformat(),
            "channel": str(channel),
            "speaker": speaker,
        }

        if tenant_id:
            event_data["tenant_id"] = tenant_id
        if session_id:
            event_data["session_id"] = session_id

        try:
            await self.redis_client.xadd(
                self.stream_name,
                event_data,
                maxlen=10000
            )
        except redis.RedisError as e:
            logger.error(f"Stream write failed: session_id={session_id}, error={e}")

        # List write (structured JSON segment)
        if session_id:
            try:
                list_key = f"session:{session_id}:transcript"
                await self.redis_client.rpush(list_key, segment)
                await self.redis_client.expire(list_key, 86400)
            except redis.RedisError as e:
                logger.error(f"List write failed: session_id={session_id}, error={e}")

    async def get_final_transcript(self, session_id: str) -> str:
        """Retrieve and reconstruct full session transcript from Redis List.

        Handles both plain string chunks (legacy browser sessions) and
        structured JSON segments (desktop multichannel sessions).
        """
        try:
            list_key = f"session:{session_id}:transcript"
            chunks = await self.redis_client.lrange(list_key, 0, -1)

            if not chunks:
                logger.warning(f"No transcript chunks found: session_id={session_id}")
                return ""

            # Detect format: structured JSON segments start with '{'
            if chunks[0].startswith('{'):
                full_transcript = self._assemble_diarized_transcript(chunks)
            else:
                full_transcript = " ".join(chunks)

            # Cleanup: delete the list after retrieval
            await self.redis_client.delete(list_key)
            logger.info(f"Retrieved and cleaned up transcript: session_id={session_id}, chunks={len(chunks)}")

            return full_transcript

        except redis.RedisError as e:
            logger.error(f"Transcript retrieval failed: session_id={session_id}, error={e}")
            return ""

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        """Format seconds as MM:SS."""
        minutes = int(seconds) // 60
        secs = int(seconds) % 60
        return f"{minutes:02d}:{secs:02d}"

    def _assemble_diarized_transcript(self, raw_segments: List[str]) -> str:
        """Assemble structured JSON segments into a diarized transcript string.

        Sorts by timestamp (primary) then channel (secondary) to ensure
        deterministic ordering regardless of Redis insertion order.
        """
        segments = []
        for s in raw_segments:
            try:
                segments.append(json.loads(s))
            except json.JSONDecodeError:
                continue

        # Sort by timestamp, then channel (user ch0 before others ch1 on tie)
        segments.sort(key=lambda seg: (seg.get("ts", 0), seg.get("ch", 0)))

        lines = []
        for seg in segments:
            ts = self._format_timestamp(seg.get("ts", 0))
            speaker = seg.get("speaker", "Unknown")
            text = seg.get("text", "")
            lines.append(f"[{ts}] {speaker}: {text}")

        return "\n".join(lines)
