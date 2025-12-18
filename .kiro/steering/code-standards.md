---
inclusion: always
---

# Code Standards for Live Transcription Service

## Language & Framework

This project uses Python 3.9+ with FastAPI as the web framework.

## Code Style

- Follow PEP 8 style guidelines
- Use type hints for all function signatures
- Maximum line length: 100 characters
- Use async/await for all I/O operations (Redis, WebSocket, Deepgram)

## Naming Conventions

- **Variables & Functions**: `snake_case` (e.g., `session_id`, `get_transcript`)
- **Classes**: `PascalCase` (e.g., `EventPublisher`, `TranscriptService`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `REDIS_URL`, `MAX_STREAM_LENGTH`)
- **Private Methods**: Prefix with single underscore (e.g., `_validate_session`)

## Async Patterns

All I/O operations MUST be async:

```python
# Correct
async def publish_event(self, data: dict):
    await self.redis_client.xadd(...)

# Incorrect - blocks event loop
def publish_event(self, data: dict):
    self.redis_client.xadd(...)
```

## Error Handling

- Use specific exception types, not bare `except:`
- Always log errors with context (session_id, tenant_id, operation)
- Gracefully handle connection failures without crashing the service
- Use try/finally blocks to ensure cleanup (WebSocket close, Redis connections)

Example:
```python
try:
    await redis_client.xadd(stream, data)
except redis.RedisError as e:
    logger.error(f"Redis write failed for session {session_id}: {e}")
    # Continue processing, don't crash
```

## Logging Standards

- Use structured logging with context
- Include `session_id` in all WebSocket-related logs
- Log levels:
  - `DEBUG`: Detailed flow information
  - `INFO`: Key events (connection established, transcript published)
  - `WARNING`: Recoverable errors (Redis timeout, retry attempts)
  - `ERROR`: Unrecoverable errors requiring attention

## Dependency Management

- All dependencies MUST be pinned in `requirements.txt`
- Use `pip-tools` or similar for dependency resolution
- Document why each dependency is needed (inline comments in requirements.txt)

## Testing Requirements

- Unit tests for all service classes
- Integration tests for Redis operations
- WebSocket connection tests using FastAPI TestClient
- Mock external services (Deepgram) in tests
- Minimum 80% code coverage for business logic

## File Organization

```
/
├── main.py              # FastAPI app and WebSocket endpoints
├── services/            # Business logic services
│   ├── event_publisher.py
│   └── transcript_service.py
├── models/              # Data models and schemas
├── utils/               # Helper functions
└── tests/               # Test files mirroring src structure
```

## Environment Variables

- Never commit `.env` files
- All environment variables MUST be documented in `.env.example`
- Use `python-dotenv` for local development
- Validate required env vars at startup

## WebSocket Best Practices

- Always accept the WebSocket connection before processing
- Use try/finally to ensure WebSocket closure
- Send error messages to client before closing on errors
- Implement heartbeat/ping for long-lived connections
