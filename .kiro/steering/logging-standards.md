---
inclusion: always
---

# Logging Standards

## Logging Configuration

Use Python's built-in `logging` module with structured logging:

```python
import logging
import sys

# Configure at application startup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Create module-specific loggers
logger = logging.getLogger(__name__)
```

## Log Levels

Use appropriate log levels:

- **DEBUG**: Detailed diagnostic information (disabled in production)
- **INFO**: General informational messages about system operation
- **WARNING**: Recoverable errors or unexpected situations
- **ERROR**: Errors that prevent specific operations but don't crash the service
- **CRITICAL**: Severe errors that may cause service failure

## Structured Logging

Always include context in log messages:

```python
# Good - includes context
logger.info(f"WebSocket connected: session_id={session_id}, tenant_id={tenant_id}")

# Bad - missing context
logger.info("WebSocket connected")
```

### Required Context Fields

Include these fields when available:

- `session_id`: For all WebSocket-related operations
- `tenant_id`: For multi-tenant operations
- `operation`: The operation being performed
- `duration_ms`: For performance-sensitive operations
- `error_type`: For error logs

## What to Log

### Connection Events

```python
logger.info(f"WebSocket connection established: session_id={session_id}")
logger.info(f"WebSocket disconnected: session_id={session_id}, duration={duration}s")
```

### Business Events

```python
logger.info(f"Transcript published: session_id={session_id}, length={len(transcript)}")
logger.info(f"Session finalized: session_id={session_id}, total_chunks={chunk_count}")
```

### Error Events

```python
logger.error(
    f"Redis operation failed: session_id={session_id}, operation=xadd",
    exc_info=True  # Include stack trace
)
```

### Performance Metrics

```python
import time

start = time.time()
# ... operation ...
duration_ms = (time.time() - start) * 1000

logger.info(f"Transcript processed: session_id={session_id}, duration_ms={duration_ms:.2f}")
```

## What NOT to Log

### Sensitive Data

Never log:

- API keys or tokens (even partially)
- Full Redis URLs with passwords
- Complete transcript content (use length or excerpt)
- User credentials
- PII without redaction

```python
# Bad - exposes sensitive data
logger.info(f"Using API key: {api_key}")
logger.info(f"Transcript: {full_transcript}")

# Good - safe logging
logger.info(f"Using API key: {api_key[:8]}...")
logger.info(f"Transcript length: {len(full_transcript)} chars")
```

### High-Volume Debug Data

Avoid logging in tight loops:

```python
# Bad - logs every audio chunk
while True:
    data = await websocket.receive_bytes()
    logger.debug(f"Received {len(data)} bytes")  # Too verbose

# Good - log summary
chunk_count = 0
while True:
    data = await websocket.receive_bytes()
    chunk_count += 1

logger.info(f"Session complete: session_id={session_id}, chunks_received={chunk_count}")
```

## Error Logging

### Exception Handling

Always include `exc_info=True` for exceptions:

```python
try:
    await redis_client.xadd(stream, data)
except redis.RedisError as e:
    logger.error(
        f"Redis write failed: session_id={session_id}",
        exc_info=True  # Includes full stack trace
    )
```

### Error Context

Provide actionable context:

```python
# Good - includes what failed and why
logger.error(
    f"Failed to publish transcript: session_id={session_id}, "
    f"stream={stream_name}, error={str(e)}"
)

# Bad - vague error
logger.error("Something went wrong")
```

## Performance Logging

Log slow operations:

```python
SLOW_OPERATION_THRESHOLD_MS = 1000

start = time.time()
result = await slow_operation()
duration_ms = (time.time() - start) * 1000

if duration_ms > SLOW_OPERATION_THRESHOLD_MS:
    logger.warning(
        f"Slow operation detected: operation=transcript_retrieval, "
        f"session_id={session_id}, duration_ms={duration_ms:.2f}"
    )
```

## Audit Logging

Create a separate audit logger for security events:

```python
audit_logger = logging.getLogger("audit")
audit_logger.setLevel(logging.INFO)

# Log security-relevant events
audit_logger.info(
    f"Session created: session_id={session_id}, "
    f"tenant_id={tenant_id}, ip={client_ip}"
)

audit_logger.info(
    f"Session ended: session_id={session_id}, "
    f"duration={duration}s, chunks={chunk_count}"
)
```

## Log Aggregation

### Railway Logs

Railway automatically captures stdout/stderr. Ensure:

- All logs go to stdout (not files)
- Use JSON format for structured logging in production
- Include timestamps in all log messages

### Production Configuration

```python
import json
import logging

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data)

# Use in production
if os.getenv("ENVIRONMENT") == "production":
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logging.root.addHandler(handler)
```

## Monitoring Integration

Log messages should be parseable by monitoring tools:

```python
# Use consistent field names
logger.info(
    "event=transcript_published "
    f"session_id={session_id} "
    f"tenant_id={tenant_id} "
    f"length={len(transcript)} "
    f"duration_ms={duration_ms}"
)
```

This format allows easy parsing and alerting in log aggregation tools.

## Log Rotation

For local development, use rotating file handlers:

```python
from logging.handlers import RotatingFileHandler

if os.getenv("ENVIRONMENT") == "development":
    file_handler = RotatingFileHandler(
        "app.log",
        maxBytes=10_000_000,  # 10MB
        backupCount=5
    )
    logging.root.addHandler(file_handler)
```

## Testing Logs

In tests, capture logs for assertions:

```python
import logging

def test_operation_logs_success(caplog):
    with caplog.at_level(logging.INFO):
        perform_operation()
    
    assert "Operation successful" in caplog.text
    assert "session_id=test-123" in caplog.text
```

## Common Patterns

### Request Logging

```python
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start) * 1000
    
    logger.info(
        f"request_completed: method={request.method} "
        f"path={request.url.path} status={response.status_code} "
        f"duration_ms={duration_ms:.2f}"
    )
    
    return response
```

### Startup Logging

```python
@app.on_event("startup")
async def startup_event():
    logger.info("Application starting")
    logger.info(f"Environment: {os.getenv('ENVIRONMENT', 'development')}")
    logger.info(f"Redis URL: {os.getenv('REDIS_URL', 'not set')[:20]}...")
    logger.info("Application ready")
```

### Shutdown Logging

```python
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application shutting down")
    # Log cleanup operations
    logger.info("Redis connections closed")
    logger.info("Application stopped")
```
