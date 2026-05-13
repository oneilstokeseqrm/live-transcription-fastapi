"""WebSocket /listen rejects missing X-Account-ID.

Mirrors the /text/clean auth-context rejection pattern (see
test_account_anchor_rejection.py) but for the WebSocket upgrade path. With a
valid internal JWT supplied, the absence of the X-Account-ID header must trip
a WebSocket close with code 1008 (Policy Violation) BEFORE any audio I/O
begins.
"""

import os
import time
import uuid

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# Set JWT test environment BEFORE importing the app so middleware picks it up.
os.environ.setdefault("INTERNAL_JWT_SECRET", "test-secret-that-is-at-least-32-characters-long")
os.environ.setdefault("INTERNAL_JWT_ISSUER", "eq-frontend")
os.environ.setdefault("INTERNAL_JWT_AUDIENCE", "eq-backend")

from main import app  # noqa: E402


def _make_jwt() -> str:
    now = int(time.time())
    payload = {
        "tenant_id": str(uuid.uuid4()),
        "user_id": "auth0|test-user-ws",
        "iss": os.environ["INTERNAL_JWT_ISSUER"],
        "aud": os.environ["INTERNAL_JWT_AUDIENCE"],
        "iat": now,
        "exp": now + 300,
    }
    return pyjwt.encode(payload, os.environ["INTERNAL_JWT_SECRET"], algorithm="HS256")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_listen_rejects_missing_account_id(client: TestClient):
    """JWT-authenticated WebSocket connection without X-Account-ID must close 1008."""
    token = _make_jwt()
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/listen",
            headers={"Authorization": f"Bearer {token}"},
        ):
            pass
    # Expect close code 1008 (Policy Violation)
    assert exc_info.value.code == 1008
