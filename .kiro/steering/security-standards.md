---
inclusion: always
---

# Security Standards

## Environment Variables & Secrets

### Secret Management

- NEVER commit secrets to version control
- Use `.env` files for local development (gitignored)
- Use Railway's environment variable management for production
- Rotate API keys regularly (quarterly minimum)

### Required Secrets

```bash
# .env.example (safe to commit)
DEEPGRAM_API_KEY=your_api_key_here
REDIS_URL=redis://localhost:6379
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o
MOCK_TENANT_ID=default_org
```

### Validation at Startup

```python
import os
import sys

REQUIRED_ENV_VARS = ["DEEPGRAM_API_KEY", "REDIS_URL", "OPENAI_API_KEY"]

def validate_environment():
    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        print(f"Missing required environment variables: {missing}")
        sys.exit(1)

# Call at app startup
validate_environment()
```

## API Key Security

### Deepgram and OpenAI API Keys

- Store in environment variables only
- Never log the full API key
- Use key rotation strategy
- Monitor usage for anomalies
- Set usage limits in provider dashboards

```python
# Bad - exposes key in logs
logger.info(f"Using API key: {api_key}")

# Good - masks key
logger.info(f"Using API key: {api_key[:8]}...")
```

### OpenAI-Specific Security

- Set monthly spending limits in OpenAI dashboard
- Monitor token usage per session
- Implement request timeouts to prevent runaway costs
- Use GPT-4o (cheaper) instead of GPT-4 Turbo when possible
- Log token counts for cost tracking

## WebSocket Security

### Connection Authentication

Implement token-based authentication for WebSocket connections:

```python
@app.websocket("/listen")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...)
):
    # Validate token before accepting
    if not await validate_token(token):
        await websocket.close(code=1008)  # Policy violation
        return
    
    await websocket.accept()
    # Continue processing
```

### Rate Limiting

Implement per-connection rate limits:

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.websocket("/listen")
@limiter.limit("10/minute")
async def websocket_endpoint(websocket: WebSocket):
    # Connection limited to 10 per minute per IP
    pass
```

### Input Validation

Validate all incoming data:

```python
# Validate audio data size
MAX_CHUNK_SIZE = 1024 * 1024  # 1MB

async def receive_audio(websocket: WebSocket):
    data = await websocket.receive_bytes()
    
    if len(data) > MAX_CHUNK_SIZE:
        await websocket.send_json({
            "error": "Audio chunk too large"
        })
        await websocket.close(code=1009)  # Message too big
        return None
    
    return data
```

## Data Privacy

### Transcript Data

- Transcripts contain potentially sensitive information
- Implement data retention policies (24-hour TTL in Redis)
- Never log full transcript content in production
- Consider encryption at rest for long-term storage

### PII Handling

```python
# Redact PII before logging
def sanitize_for_logging(transcript: str) -> str:
    # Remove email addresses, phone numbers, etc.
    import re
    sanitized = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', transcript)
    sanitized = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '[PHONE]', sanitized)
    return sanitized

logger.info(f"Transcript: {sanitize_for_logging(transcript)}")
```

### Multi-Tenancy

Ensure tenant isolation:

```python
# Always scope Redis keys by tenant
key = f"tenant:{tenant_id}:session:{session_id}:transcript"

# Validate tenant access
async def validate_tenant_access(session_id: str, tenant_id: str) -> bool:
    stored_tenant = await redis_client.hget(
        f"session:{session_id}:metadata",
        "tenant_id"
    )
    return stored_tenant == tenant_id
```

## Redis Security

### Connection Security

```python
# Use TLS for production Redis connections
redis_url = os.getenv("REDIS_URL")
if redis_url.startswith("rediss://"):  # TLS enabled
    redis_client = redis.from_url(
        redis_url,
        decode_responses=True,
        ssl_cert_reqs="required"
    )
```

### Access Control

- Use Redis ACLs to limit command access
- Create separate users for different services
- Restrict dangerous commands (FLUSHALL, CONFIG, etc.)

### Data Expiration

Always set TTL to prevent data leaks:

```python
# Set expiration on all session data
await redis_client.setex(
    f"session:{session_id}:data",
    86400,  # 24 hours
    data
)
```

## CORS Configuration

Configure CORS properly for browser clients:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yourdomain.com"],  # Specific origins only
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
```

## Error Messages

Never expose internal details in error messages:

```python
# Bad - exposes internal structure
raise HTTPException(
    status_code=500,
    detail=f"Redis connection failed at {redis_url}"
)

# Good - generic message
raise HTTPException(
    status_code=500,
    detail="Service temporarily unavailable"
)
```

## Logging Security

### What NOT to Log

- API keys or tokens
- Full Redis URLs with passwords
- Complete transcript content (use excerpts)
- User credentials
- Internal IP addresses

### Safe Logging

```python
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Safe logging examples
logger.info(f"Session started: {session_id}")
logger.info(f"Transcript length: {len(transcript)} chars")
logger.error(f"Redis operation failed", exc_info=True)  # Includes stack trace
```

## Dependency Security

### Regular Updates

```bash
# Check for security vulnerabilities
pip install safety
safety check

# Update dependencies
pip install --upgrade -r requirements.txt
```

### Pinned Versions

Always pin dependency versions in `requirements.txt`:

```
fastapi==0.115.0  # Not fastapi>=0.115.0
```

## Monitoring & Alerting

Set up alerts for security events:

- Failed authentication attempts
- Unusual traffic patterns
- Redis connection failures
- API key usage spikes
- Error rate increases

## Incident Response

Document security incident procedures:

1. Detect: Monitor logs and metrics
2. Contain: Disable compromised keys/tokens
3. Investigate: Review logs and access patterns
4. Remediate: Patch vulnerabilities, rotate secrets
5. Document: Post-mortem and lessons learned

## Compliance

### Data Retention

- Implement automatic data deletion (Redis TTL)
- Document data retention policies
- Provide data export capabilities for users

### Audit Logging

Log security-relevant events:

```python
audit_logger = logging.getLogger("audit")

audit_logger.info(f"Session created: {session_id}, tenant: {tenant_id}, ip: {client_ip}")
audit_logger.info(f"Session ended: {session_id}, duration: {duration}s")
```
