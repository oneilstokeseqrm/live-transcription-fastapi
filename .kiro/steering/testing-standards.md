---
inclusion: always
---

# Testing Standards

## Testing Philosophy

- Write tests that validate behavior, not implementation
- Test the public API, not internal details
- Use property-based testing for complex logic
- Mock external services (Deepgram, Redis) in unit tests
- Use integration tests for critical paths

## Test Organization

```
tests/
├── unit/
│   ├── test_event_publisher.py
│   └── test_transcript_service.py
├── integration/
│   ├── test_websocket_flow.py
│   └── test_redis_operations.py
└── conftest.py  # Shared fixtures
```

## Unit Testing

### Service Layer Tests

```python
import pytest
from unittest.mock import AsyncMock, patch
from services.event_publisher import EventPublisher

@pytest.mark.asyncio
async def test_publish_transcript_event():
    publisher = EventPublisher()
    
    with patch.object(publisher.redis_client, 'xadd', new_callable=AsyncMock) as mock_xadd:
        await publisher.publish_transcript_event(
            transcript="Hello world",
            metadata={"is_final": True},
            tenant_id="test-org"
        )
        
        mock_xadd.assert_called_once()
        call_args = mock_xadd.call_args
        assert call_args[0][0] == "transcription_events"
        assert "Hello world" in str(call_args[1])
```

### Testing Async Functions

Always use `@pytest.mark.asyncio` for async tests:

```python
@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_function()
    assert result is not None
```

## Integration Testing

### WebSocket Tests

```python
from fastapi.testclient import TestClient
from main import app

def test_websocket_transcript_flow():
    client = TestClient(app)
    
    with client.websocket_connect("/listen") as websocket:
        # Send audio data
        audio_chunk = b"fake_audio_data"
        websocket.send_bytes(audio_chunk)
        
        # Receive transcript
        response = websocket.receive_text()
        assert isinstance(response, str)
```

### Redis Integration Tests

Use a test Redis instance or fakeredis:

```python
import pytest
from fakeredis import aioredis

@pytest.fixture
async def redis_client():
    client = aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.flushall()

@pytest.mark.asyncio
async def test_redis_transcript_storage(redis_client):
    session_id = "test-session"
    
    await redis_client.rpush(f"session:{session_id}:transcript", "Hello")
    await redis_client.rpush(f"session:{session_id}:transcript", "world")
    
    chunks = await redis_client.lrange(f"session:{session_id}:transcript", 0, -1)
    assert chunks == ["Hello", "world"]
```

## Mocking External Services

### Deepgram Mock

```python
from unittest.mock import AsyncMock, MagicMock

@pytest.fixture
def mock_deepgram():
    mock_socket = MagicMock()
    mock_socket.send = MagicMock()
    mock_socket.finish = MagicMock()
    
    mock_dg = AsyncMock()
    mock_dg.transcription.live = AsyncMock(return_value=mock_socket)
    
    return mock_dg
```

### Redis Mock

```python
@pytest.fixture
def mock_redis():
    mock = AsyncMock()
    mock.xadd = AsyncMock()
    mock.rpush = AsyncMock()
    mock.lrange = AsyncMock(return_value=["chunk1", "chunk2"])
    return mock
```

## Test Fixtures

Define reusable fixtures in `conftest.py`:

```python
import pytest
from fastapi.testclient import TestClient
from main import app

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def sample_transcript_data():
    return {
        "channel": {
            "alternatives": [{
                "transcript": "This is a test transcript"
            }]
        },
        "is_final": True
    }

@pytest.fixture
def session_id():
    return "test-session-123"
```

## Property-Based Testing

Use Hypothesis for property-based tests:

```python
from hypothesis import given, strategies as st

@given(st.text(min_size=1))
def test_transcript_never_empty(transcript):
    # Property: processed transcripts should never be empty
    result = process_transcript(transcript)
    assert len(result) > 0

@given(st.lists(st.text(), min_size=1))
def test_transcript_joining(chunks):
    # Property: joining then splitting should preserve count
    joined = " ".join(chunks)
    split = joined.split()
    assert len(split) >= len(chunks)
```

## Coverage Requirements

- Minimum 80% code coverage for services
- 100% coverage for critical paths (event publishing, session management)
- Exclude test files and configuration from coverage

Run coverage:
```bash
pytest --cov=services --cov-report=html --cov-report=term
```

## Test Naming Conventions

- Test files: `test_*.py`
- Test functions: `test_<what>_<condition>_<expected>`
- Examples:
  - `test_publish_event_with_valid_data_succeeds`
  - `test_websocket_connection_without_auth_fails`
  - `test_transcript_retrieval_for_missing_session_returns_empty`

## Continuous Integration

Tests MUST pass before merging to main:

```yaml
# .github/workflows/test.yml
- name: Run tests
  run: |
    pytest tests/ -v --cov=services --cov-fail-under=80
```

## Test Data Management

- Use factories for test data generation
- Avoid hardcoded test data when possible
- Clean up test data after each test
- Use unique identifiers to prevent test interference

## Performance Testing

For critical paths, add performance benchmarks:

```python
import pytest

@pytest.mark.benchmark
def test_transcript_processing_performance(benchmark):
    result = benchmark(process_large_transcript, sample_data)
    assert result is not None
```

## Error Case Testing

Always test error scenarios:

```python
@pytest.mark.asyncio
async def test_redis_connection_failure():
    publisher = EventPublisher()
    
    with patch.object(publisher.redis_client, 'xadd', side_effect=redis.RedisError):
        # Should not raise, should log error
        await publisher.publish_transcript_event("test", {})
```

## Test Environment

Set up test environment variables:

```python
# conftest.py
import os
import pytest

@pytest.fixture(autouse=True)
def test_env():
    os.environ["REDIS_URL"] = "redis://localhost:6379/1"
    os.environ["DEEPGRAM_API_KEY"] = "test-key"
    yield
    # Cleanup if needed
```
