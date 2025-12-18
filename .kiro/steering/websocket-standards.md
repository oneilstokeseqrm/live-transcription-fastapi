---
inclusion: always
---

# WebSocket Standards

## Connection Lifecycle

### Initialization

Every WebSocket connection MUST follow this pattern:

```python
@app.websocket("/listen")
async def websocket_endpoint(websocket: WebSocket):
    # 1. Generate session ID
    session_id = str(uuid.uuid4())
    
    # 2. Accept connection
    await websocket.accept()
    
    # 3. Initialize resources
    try:
        deepgram_socket = await process_audio(websocket, session_id)
        
        # 4. Process messages
        while True:
            data = await websocket.receive_bytes()
            deepgram_socket.send(data)
            
    except WebSocketDisconnect:
        logger.info(f"Client disconnected: {session_id}")
    except Exception as e:
        logger.error(f"WebSocket error for {session_id}: {e}")
        await websocket.send_json({"error": str(e)})
    finally:
        # 5. Cleanup
        await cleanup_session(session_id)
        await websocket.close()
```

### Session ID Requirements

- Generate UUID v4 at connection establishment
- Pass session_id to all downstream handlers
- Include in all logs and events
- Use for Redis key namespacing

## Message Formats

### Client → Server (Audio Data)

Binary frames containing audio chunks:

```python
data = await websocket.receive_bytes()  # Raw audio bytes
```

### Server → Client (Transcripts)

Text frames containing transcript results:

```python
# Interim results (optional)
await websocket.send_text(transcript)

# Final results
await websocket.send_json({
    "type": "transcript",
    "text": transcript,
    "is_final": True,
    "timestamp": datetime.utcnow().isoformat()
})
```

### Error Messages

```python
await websocket.send_json({
    "type": "error",
    "message": "Transcription service unavailable",
    "code": "SERVICE_ERROR"
})
```

## Error Handling

### Connection Errors

```python
try:
    await websocket.accept()
except RuntimeError as e:
    logger.error(f"Failed to accept WebSocket: {e}")
    return
```

### Disconnection Handling

```python
from fastapi import WebSocketDisconnect

try:
    while True:
        data = await websocket.receive_bytes()
        # Process data
except WebSocketDisconnect:
    # Normal disconnection - not an error
    logger.info(f"Client disconnected gracefully: {session_id}")
    await finalize_session(session_id)
```

### Deepgram Connection Failures

```python
try:
    deepgram_socket = await connect_to_deepgram(handler)
except Exception as e:
    await websocket.send_json({
        "type": "error",
        "message": "Failed to initialize transcription service"
    })
    await websocket.close(code=1011)  # Internal error
    raise
```

## Cleanup Protocol

Always implement cleanup in a `finally` block:

```python
finally:
    # 1. Retrieve final transcript
    final_transcript = await get_final_transcript(session_id)
    
    # 2. Send to client
    if final_transcript:
        await websocket.send_json({
            "type": "session_complete",
            "transcript": final_transcript
        })
    
    # 3. Close external connections
    if deepgram_socket:
        deepgram_socket.finish()
    
    # 4. Cleanup Redis data
    await cleanup_session_data(session_id)
    
    # 5. Close WebSocket
    await websocket.close()
```

## State Management

WebSocket handlers MUST be stateless. All state should be stored in Redis:

```python
# Bad - state in memory
class WebSocketHandler:
    def __init__(self):
        self.transcripts = {}  # Lost on restart

# Good - state in Redis
async def store_transcript(session_id: str, text: str):
    await redis_client.rpush(f"session:{session_id}:transcript", text)
```

## Concurrency

- Each WebSocket connection runs in its own async task
- Use asyncio locks for shared resources
- Avoid blocking operations in WebSocket handlers
- Use `asyncio.create_task()` for background operations

## Testing

### Unit Tests

```python
from fastapi.testclient import TestClient

def test_websocket_connection():
    with TestClient(app).websocket_connect("/listen") as websocket:
        # Send audio data
        websocket.send_bytes(audio_chunk)
        
        # Receive transcript
        data = websocket.receive_text()
        assert len(data) > 0
```

### Integration Tests

- Test full flow: connect → send audio → receive transcript → disconnect
- Verify session cleanup after disconnection
- Test error scenarios (invalid audio, service failures)

## Performance Considerations

- Set reasonable timeouts for receive operations
- Implement backpressure if client can't keep up
- Monitor active connection count
- Use connection pooling for external services (Deepgram, Redis)

## Security

- Validate audio format before processing
- Implement rate limiting per connection
- Set maximum message size limits
- Use authentication tokens if needed (query params or headers)
- Implement CORS properly for browser clients

## Monitoring

Track these metrics per WebSocket connection:

- Connection duration
- Messages sent/received count
- Error rate
- Cleanup success rate
- Average transcript latency
