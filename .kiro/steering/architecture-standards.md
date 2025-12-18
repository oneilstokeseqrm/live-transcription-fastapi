---
inclusion: always
---

# Architecture Standards for Live Transcription Service

## Session Management Protocol

All WebSocket connections MUST generate a unique `session_id` (UUID v4) upon connection establishment. This identifier:

- Must be created at the WebSocket endpoint level
- Must propagate through all internal handlers and service layers
- Must be included in all logging, events, and persistence operations
- Must remain immutable for the lifetime of the connection

### Current Implementation Gap

**CRITICAL**: The current `main.py` WebSocket endpoint does NOT implement session ID generation. This must be added:

```python
import uuid

@app.websocket("/listen")
async def websocket_endpoint(websocket: WebSocket):
    session_id = str(uuid.uuid4())  # ADD THIS
    await websocket.accept()
    
    # Pass session_id to all downstream functions
    try:
        deepgram_socket = await process_audio(websocket, session_id)
        # ... rest of implementation
```

## The Stateless Stitcher Pattern

The system follows a "Stateless Stitcher" architecture where transcript reconstruction happens at session termination rather than maintaining in-memory state. After reconstruction, the raw transcript is immediately processed through the CleanerService to produce a polished, structured output.

### Redis Dual-Write Strategy

The `EventPublisher` service MUST implement a dual-write pattern for every final transcript chunk:

1. **Stream Write (Real-time)**: Publish the complete event object to the Redis Stream for downstream consumers
2. **List Write (Persistence)**: Append the plain text transcript to a Redis List keyed by `session:{session_id}:transcript`

Both writes must succeed atomically. If either fails, the system must log the error and continue processing.

### Current Implementation Gap

**CRITICAL**: The current `EventPublisher` only implements Stream writes. The List write for persistence is missing:

```python
# In EventPublisher.publish_transcript_event(), ADD:
await self.redis_client.rpush(
    f"session:{session_id}:transcript",
    transcript
)
await self.redis_client.expire(
    f"session:{session_id}:transcript",
    86400  # 24 hour TTL
)
```

### Transcript Retrieval Standard

Upon WebSocket disconnection, the system MUST:

1. Call `get_final_transcript(session_id)` to reconstruct the full conversation
2. Retrieve all chunks from the Redis List in order
3. Join chunks with appropriate spacing
4. Return the complete transcript for final processing or storage

The Redis List should be set with an expiration (TTL) of 24 hours to prevent unbounded growth.

### Current Implementation Gap

**CRITICAL**: The current WebSocket endpoint does NOT retrieve or return the final transcript on disconnection. Add to the `finally` block:

```python
finally:
    # Step 1: Retrieve raw transcript
    chunks = await redis_client.lrange(
        f"session:{session_id}:transcript",
        0,
        -1
    )
    if chunks:
        raw_transcript = " ".join(chunks)
        
        # Step 2: Clean and structure the transcript
        cleaner = CleanerService()
        meeting_output = await cleaner.clean_transcript(raw_transcript, session_id)
        
        # Step 3: Send structured output to client
        await websocket.send_json({
            "type": "session_complete",
            "summary": meeting_output.summary,
            "action_items": meeting_output.action_items,
            "cleaned_transcript": meeting_output.cleaned_transcript,
            "raw_transcript": raw_transcript
        })
    
    await websocket.close()
```

## The CleanerService Component

### Purpose

The CleanerService is the final stage of the transcript processing pipeline. It transforms raw, stitched transcripts into polished, structured documents using OpenAI's GPT-4o with Structured Outputs.

### Design Philosophy

Based on the RoboScribe project, the CleanerService follows these principles:

1. **Editor, Not Author**: The LLM cleans existing content without adding new words
2. **Preserve Authenticity**: Maintains speaker voice and natural patterns
3. **Improve Readability**: Removes filler words, fixes grammar, adds punctuation
4. **Structured Output**: Returns summary, action items, and cleaned text

### Integration Point

The CleanerService MUST be invoked in the WebSocket endpoint's `finally` block, immediately after `get_final_transcript()` returns the raw text:

```python
# In main.py finally block
raw_transcript = await event_publisher.get_final_transcript(session_id)
if raw_transcript:
    cleaner = CleanerService()
    meeting_output = await cleaner.clean_transcript(raw_transcript, session_id)
    # Send to client...
```

### Output Schema

The CleanerService returns a `MeetingOutput` Pydantic model:

```python
class MeetingOutput(BaseModel):
    summary: str  # 2-3 sentence summary
    action_items: List[str]  # Extracted tasks
    cleaned_transcript: str  # Polished text
```

### Error Handling

The CleanerService MUST handle failures gracefully:

- **Timeout**: Return raw transcript with timeout message
- **API Error**: Return raw transcript with error message
- **Invalid Response**: Return raw transcript with parsing error

This ensures the client always receives usable output, even if cleaning fails.

## Error Handling

- Connection failures must not lose transcript data already written to Redis
- Partial transcripts must be retrievable even if the session terminates unexpectedly
- All Redis operations must include timeout and retry logic

## Performance Considerations

- Redis List operations (RPUSH, LRANGE) are O(1) and O(N) respectively
- Session lists should be cleaned up after retrieval to prevent memory bloat
- Stream maxlen should be configured to prevent unbounded growth (default: 10,000 entries)
