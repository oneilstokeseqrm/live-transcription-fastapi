"""Backend rejects ingestion requests that lack account_id at the auth-context boundary.

These tests cover the JWT auth path (the production path) for /text/clean. We
issue a valid internal JWT and verify that the absence of the X-Account-ID
header trips the 400 rejection inside get_auth_context() before any business
logic runs. With the header present, we expect anything OTHER than 400-for-
account_id (the 200 happy path is fine; other downstream validation failures
are also fine for this test's purpose).
"""

import os
import time
import uuid

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

# Set JWT test environment BEFORE importing the app so middleware picks it up.
os.environ.setdefault("INTERNAL_JWT_SECRET", "test-secret-that-is-at-least-32-characters-long")
os.environ.setdefault("INTERNAL_JWT_ISSUER", "eq-frontend")
os.environ.setdefault("INTERNAL_JWT_AUDIENCE", "eq-backend")

from main import app  # noqa: E402


def _make_jwt() -> str:
    now = int(time.time())
    payload = {
        "tenant_id": str(uuid.uuid4()),
        "user_id": "auth0|test-user-account-anchor",
        "iss": os.environ["INTERNAL_JWT_ISSUER"],
        "aud": os.environ["INTERNAL_JWT_AUDIENCE"],
        "iat": now,
        "exp": now + 300,
    }
    return pyjwt.encode(payload, os.environ["INTERNAL_JWT_SECRET"], algorithm="HS256")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def mock_services():
    """Stub the downstream services so a happy-path request doesn't hit external systems."""
    with patch("services.batch_cleaner_service.BatchCleanerService.clean_transcript") as mock_clean, \
         patch("services.aws_event_publisher.AWSEventPublisher.publish_envelope") as mock_publish, \
         patch("services.intelligence_service.IntelligenceService.process_transcript") as mock_intel:
        mock_clean.return_value = "Cleaned text"
        mock_publish.return_value = {"kinesis_sequence": "123", "eventbridge_id": "456"}
        mock_intel.return_value = None
        yield


def test_text_clean_rejects_missing_account_id(client: TestClient):
    """Auth succeeds, but missing X-Account-ID must produce 400 from the auth-context layer."""
    token = _make_jwt()
    response = client.post(
        "/text/clean",
        json={"text": "hello world"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400, response.text
    assert "x-account-id" in response.text.lower()


def test_text_clean_does_not_400_when_account_id_present(
    client: TestClient, mock_services
):
    """With X-Account-ID header present, the auth-context 400 must NOT fire.

    Downstream validation (e.g. request-body shape, Task 1.5+) may still return
    a non-200; what matters here is that we do not get the auth-context's
    "X-Account-ID header is required" 400.
    """
    token = _make_jwt()
    response = client.post(
        "/text/clean",
        json={"text": "hello world"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Account-ID": "acct-1",
        },
    )
    # 200 (happy path) is fine; 4xx for downstream reasons is also fine. The
    # auth-context layer must not have rejected us for a missing account_id.
    assert "x-account-id header is required" not in response.text.lower()
