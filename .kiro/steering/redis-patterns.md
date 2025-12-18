---
inclusion: always
---

# Redis Usage Patterns

## Connection Management

Use `redis.asyncio` for all Redis operations to maintain async compatibility with FastAPI.

### Connection Pool

```python
# Correct - reuse connection pool
class EventPublisher:
    def __init__(self):
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
```

### Connection Lifecycle

- Initialize Redis client once at service startup
- Reuse the same client instance across requests
- Implement graceful shutdown to close connections
- Set connection timeouts (default: 5 seconds)

## Data Structures

### Redis Streams (Real-time Events)

Use Redis Streams for publishing real-time transcription events to downstream consumers.

**Key Pattern**: `transcription_events`

```python
await redis_client.xadd(
    "transcription_events",
    {
        "event_type": "transcript_completed",
        "session_id": session_id,
        "transcript": text,
        "timestamp": datetime.utcnow().isoformat()
    },
    maxlen=10000  # Prevent unbounded growth
)
```

**Stream Configuration**:
- Use `maxlen` to cap stream size (recommended: 10,000 entries)
- Consider `XTRIM` with `MINID` for time-based retention
- Use consumer groups for multiple downstream processors

### Redis Lists (Session Persistence)

Use Redis Lists to accumulate transcript chunks for session reconstruction.

**Key Pattern**: `session:{session_id}:transcript`

```python
# Append transcript chunk
await redis_client.rpush(
    f"session:{session_id}:transcript",
    transcript_text
)

# Set expiration (24 hours)
await redis_client.expire(
    f"session:{session_id}:transcript",
    86400
)

# Retrieve full transcript
chunks = await redis_client.lrange(
    f"session:{session_id}:transcript",
    0,
    -1
)
full_transcript = " ".join(chunks)
```

**List Best Practices**:
- Always set TTL to prevent memory leaks
- Use `RPUSH` for append operations (O(1))
- Use `LRANGE` for retrieval (O(N) where N = list length)
- Delete list after retrieval if no longer needed

### Redis Hashes (Session Metadata)

Use Redis Hashes for storing session metadata.

**Key Pattern**: `session:{session_id}:metadata`

```python
await redis_client.hset(
    f"session:{session_id}:metadata",
    mapping={
        "tenant_id": tenant_id,
        "started_at": datetime.utcnow().isoformat(),
        "language": "en-US"
    }
)
```

## Key Naming Conventions

Follow a hierarchical naming pattern:

- `{resource}:{identifier}:{attribute}`
- Examples:
  - `session:abc-123:transcript`
  - `session:abc-123:metadata`
  - `tenant:org-456:sessions`

## TTL Strategy

Set appropriate expiration times to prevent memory bloat:

- **Session transcripts**: 24 hours (86400 seconds)
- **Session metadata**: 24 hours (86400 seconds)
- **Temporary locks**: 30 seconds
- **Cache entries**: Based on use case (1-60 minutes)

## Error Handling

Always wrap Redis operations in try/except blocks:

```python
try:
    await redis_client.xadd(stream, data)
except redis.RedisError as e:
    logger.error(f"Redis operation failed: {e}")
    # Implement fallback or retry logic
```

## Performance Considerations

- Use pipelining for bulk operations
- Avoid `KEYS` command in production (use `SCAN` instead)
- Monitor memory usage with `INFO memory`
- Use `MULTI/EXEC` for atomic operations when needed

## Monitoring

Track these Redis metrics:

- Connection pool exhaustion
- Command latency (p95, p99)
- Memory usage
- Eviction rate
- Stream length growth

## Local Development

For local development without Redis:

```python
# Optional: Mock Redis client for testing
if os.getenv("ENVIRONMENT") == "test":
    from fakeredis import aioredis
    redis_client = aioredis.FakeRedis(decode_responses=True)
```
