"""Backend rejects ingestion requests that lack account_id at the auth-context boundary.

These tests cover the JWT auth path (the production path) for /text/clean. We
issue a valid internal JWT and verify that the absence of the X-Account-ID
header trips the 400 rejection inside get_auth_context_ingestion() before any
business logic runs. With the header present, we expect anything OTHER than
400-for-account_id (the 200 happy path is fine; other downstream validation
failures are also fine for this test's purpose).

The polling counterpart (get_auth_context_polling) is also exercised here for
GET /upload/status/{job_id} — Phase 1.5 / T1.26.4 split the helper so polling
routes do NOT require X-Account-ID. Ingestion routes still do.
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


def test_text_clean_rejects_missing_body_account_id(client: TestClient):
    """Pydantic layer rejects bodies without account_id with 422 (Phase 1 / T1.5)."""
    token = _make_jwt()
    response = client.post(
        "/text/clean",
        json={"text": "hello world"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Account-ID": "acct-1",  # present so we test the body layer, not the header layer
        },
    )
    assert response.status_code == 422, response.text
    assert "account_id" in response.text.lower()


def test_text_clean_rejects_missing_account_id_header(client: TestClient):
    """Auth-context layer rejects missing X-Account-ID header with 400 (Phase 1 / T1.4).

    Body contains a valid account_id so Pydantic validation passes; the 400
    must come from get_auth_context_ingestion()'s header check.
    """
    token = _make_jwt()
    response = client.post(
        "/text/clean",
        json={"text": "hello world", "account_id": "acct-1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400, response.text
    assert "x-account-id" in response.text.lower()


@pytest.mark.usefixtures("mock_services")
def test_text_clean_does_not_400_when_account_id_present(client: TestClient):
    """With X-Account-ID header present, the auth-context 400 must NOT fire.

    Downstream validation (e.g. request-body shape, Task 1.5+) may still return
    a non-200; what matters here is that we do not get the auth-context's
    "X-Account-ID header is required" 400.
    """
    token = _make_jwt()
    response = client.post(
        "/text/clean",
        json={"text": "hello world", "account_id": "acct-1"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Account-ID": "acct-1",
        },
    )
    # 200 (happy path) is fine; 4xx for downstream reasons is also fine. The
    # auth-context layer must not have rejected us for a missing account_id.
    assert "x-account-id header is required" not in response.text.lower()


def test_text_clean_rejects_account_id_mismatch(client: TestClient):
    """Backend rejects requests where body.account_id != X-Account-ID header.

    The auth-context account_id is the source of truth. A mismatch indicates
    inconsistent client behavior or a tampering attempt; we 400 loudly rather
    than silently picking one source.
    """
    token = _make_jwt()
    response = client.post(
        "/text/clean",
        json={"text": "hello world", "account_id": "acct-A"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Account-ID": "acct-B",
        },
    )
    assert response.status_code == 400, response.text
    assert "account_id mismatch" in response.text.lower()


@pytest.mark.usefixtures("mock_services")
def test_text_clean_accepts_matching_account_id(client: TestClient):
    """When body.account_id matches X-Account-ID header, request proceeds normally."""
    token = _make_jwt()
    response = client.post(
        "/text/clean",
        json={"text": "hello world", "account_id": "acct-1"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Account-ID": "acct-1",
        },
    )
    # The auth-context boundary must NOT 400, and the mismatch check must NOT 400.
    assert "account_id mismatch" not in response.text.lower()
    assert "x-account-id header is required" not in response.text.lower()


# --- /upload/init tests (T1.26.3) ---

def test_upload_init_rejects_missing_account_id_header(client: TestClient):
    """Auth-context layer rejects missing X-Account-ID header with 400 for /upload/init."""
    token = _make_jwt()
    response = client.post(
        "/upload/init",
        json={
            "filename": "test.wav",
            "mime_type": "audio/wav",
            "file_size": 1024,
            "account_id": "acct-1",  # body has it; header missing → 400 from auth-context
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400, response.text
    assert "x-account-id" in response.text.lower()


def test_upload_init_rejects_account_id_mismatch(client: TestClient):
    """Backend rejects /upload/init requests where body.account_id != X-Account-ID header.

    The auth-context account_id is the source of truth. UploadJob.account_id
    must be persisted from context, not body — a mismatch indicates inconsistent
    client behavior and the worker would otherwise publish under the wrong account.
    """
    token = _make_jwt()
    response = client.post(
        "/upload/init",
        json={
            "filename": "test.wav",
            "mime_type": "audio/wav",
            "file_size": 1024,
            "account_id": "acct-A",
        },
        headers={
            "Authorization": f"Bearer {token}",
            "X-Account-ID": "acct-B",
        },
    )
    assert response.status_code == 400, response.text
    assert "account_id mismatch" in response.text.lower()


@pytest.fixture
def mock_upload_services():
    """Stub S3 + DB so /upload/init can return without hitting external systems."""
    from unittest.mock import AsyncMock, MagicMock
    with patch("routers.upload.S3Service") as mock_s3_cls, \
         patch("routers.upload.get_async_session") as mock_session:
        mock_s3 = MagicMock()
        mock_s3.generate_file_key.return_value = "tenants/foo/jobs/bar/test.wav"
        mock_s3.generate_presigned_put_url.return_value = (
            "https://example.com/upload",
            "2026-01-01T00:00:00Z",
        )
        mock_s3_cls.return_value = mock_s3
        # Mock the async context manager for get_async_session()
        async_session_cm = MagicMock()
        async_session_instance = AsyncMock()
        async_session_instance.add = MagicMock()
        async_session_cm.__aenter__.return_value = async_session_instance
        async_session_cm.__aexit__.return_value = None
        mock_session.return_value = async_session_cm
        yield


@pytest.mark.usefixtures("mock_upload_services")
def test_upload_init_accepts_matching_account_id(client: TestClient):
    """When body.account_id matches X-Account-ID header, /upload/init proceeds normally."""
    token = _make_jwt()
    response = client.post(
        "/upload/init",
        json={
            "filename": "test.wav",
            "mime_type": "audio/wav",
            "file_size": 1024,
            "account_id": "acct-1",
        },
        headers={
            "Authorization": f"Bearer {token}",
            "X-Account-ID": "acct-1",
        },
    )
    assert "account_id mismatch" not in response.text.lower()
    assert "x-account-id header is required" not in response.text.lower()


# --- Polling routes (T1.26.4) ---
#
# Polling/read-only routes must NOT require X-Account-ID. The auth-context
# helper that gates ingestion writes (get_auth_context_ingestion) is too
# strict for clients polling job status with just a JWT. Phase 1.5 splits
# the helper so polling routes use get_auth_context_polling instead — same
# JWT validation, no X-Account-ID gate. The route handler still enforces
# tenant ownership on the job record.

def test_upload_status_does_not_require_account_id_header(client: TestClient):
    """GET /upload/status/{job_id} must NOT reject requests missing X-Account-ID.

    Phase 1.5 / T1.26.4: polling endpoints use get_auth_context_polling and
    therefore must NOT trip the auth-context 400 when X-Account-ID is absent.
    Passing a bogus job_id is fine here — we expect 404 (job not found) or
    similar non-auth-context failure, never the 400 "X-Account-ID header is
    required" rejection that ingestion routes raise.
    """
    token = _make_jwt()
    bogus_job_id = str(uuid.uuid4())
    response = client.get(
        f"/upload/status/{bogus_job_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    # The exact response code depends on whether the job exists. What matters
    # is that the auth-context layer did NOT reject us for missing X-Account-ID.
    assert response.status_code != 400 or "x-account-id" not in response.text.lower(), (
        f"GET /upload/status should not require X-Account-ID but got: "
        f"status={response.status_code}, body={response.text}"
    )


def test_upload_status_rejects_missing_jwt(client: TestClient):
    """GET /upload/status/{job_id} still requires JWT authentication.

    The polling helper relaxes X-Account-ID, NOT JWT verification.
    """
    bogus_job_id = str(uuid.uuid4())
    response = client.get(f"/upload/status/{bogus_job_id}")
    assert response.status_code == 401, response.text
