"""Persist `/upload/init` participants through the async worker (T1.26.5).

Codex Round 2 finding (P2 — Task 1.26.5): `UploadInitRequest.participants`
was accepted by Pydantic in Task 1.26.6 but dropped before the async worker
ran. Because `_process_upload_job()` reconstructs state only from
`UploadJob`, caller-provided participants never reached `enrich()` for
upload-without-calendar-match (manual-notes) workflows.

These tests pin the new contract:

1. **/upload/init persists body.participants**: caller sends participants
   on `/upload/init`; the `UploadJob` row gets `participants_json` set to a
   valid JSON encoding of the participants.

2. **Worker deserializes + forwards to enrich()**: when `_process_upload_job`
   runs against a job with `participants_json` populated, it deserializes
   the JSON and forwards `participants=[ParticipantSpec(...), ...]` to
   `TranscriptEnrichmentService.enrich()`.

3. **Null-case regression guard**: `/upload/init` without `body.participants`
   stores `participants_json=None`; the worker passes `participants=None`
   to `enrich()` (matches Task 1.26.6 caller-side-completeness contract).

4. **Corrupt-JSON recovery**: a row with malformed `participants_json` is
   logged + the worker falls back to `participants=None` (no crash).

Mock-driven (matches the existing `test_text_clean_participants.py` and
`test_account_anchor_rejection.py` patterns); no DB required, the tests
pass regardless of whether the migration has been applied.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest

# Set JWT test environment BEFORE importing the app.
os.environ.setdefault("INTERNAL_JWT_SECRET", "test-secret-that-is-at-least-32-characters-long")
os.environ.setdefault("INTERNAL_JWT_ISSUER", "eq-frontend")
os.environ.setdefault("INTERNAL_JWT_AUDIENCE", "eq-backend")


def _make_jwt() -> str:
    now = int(time.time())
    payload = {
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "user_id": "auth0|test-upload-participants",
        "iss": os.environ["INTERNAL_JWT_ISSUER"],
        "aud": os.environ["INTERNAL_JWT_AUDIENCE"],
        "iat": now,
        "exp": now + 300,
    }
    return pyjwt.encode(payload, os.environ["INTERNAL_JWT_SECRET"], algorithm="HS256")


# ---------------------------------------------------------------------------
# Test 1: POST /upload/init serializes body.participants to JSON on UploadJob
# ---------------------------------------------------------------------------


def test_upload_init_persists_body_participants_as_json():
    """`/upload/init` with body.participants populates UploadJob.participants_json.

    Capture the UploadJob instance handed to session.add() and assert
    participants_json round-trips through `json.loads` to the original
    participant shapes.
    """
    from fastapi.testclient import TestClient
    from main import app

    captured_jobs: list = []

    class _AsyncSessionStub:
        def __init__(self):
            self.add = MagicMock(side_effect=captured_jobs.append)
            self.commit = AsyncMock()

    class _AsyncCM:
        def __init__(self):
            self._session = _AsyncSessionStub()

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    account_id = "acct-participants-1"
    request_payload = {
        "filename": "test.wav",
        "mime_type": "audio/wav",
        "file_size": 1024,
        "account_id": account_id,
        "participants": [
            {"email": "alice@acme.com", "display_name": "Alice"},
            {"email": "partner@consultingco.com"},
        ],
    }

    with patch("routers.upload.S3Service") as mock_s3_cls, \
         patch("routers.upload.get_async_session", new=lambda: _AsyncCM()):
        mock_s3 = MagicMock()
        mock_s3.generate_file_key.return_value = "tenants/foo/jobs/bar/test.wav"
        mock_s3.generate_presigned_put_url.return_value = (
            "https://example.com/upload",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        mock_s3_cls.return_value = mock_s3

        token = _make_jwt()
        client = TestClient(app)
        response = client.post(
            "/upload/init",
            json=request_payload,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Account-ID": account_id,
            },
        )

    assert response.status_code == 200, response.text
    assert len(captured_jobs) == 1, (
        f"expected exactly one UploadJob persisted; got {len(captured_jobs)}"
    )

    job = captured_jobs[0]
    assert hasattr(job, "participants_json"), (
        "UploadJob is missing the participants_json field — migration + "
        "model field not wired"
    )
    assert job.participants_json is not None, (
        "/upload/init dropped body.participants on the floor — handler must "
        "serialize and persist to UploadJob.participants_json"
    )

    decoded = json.loads(job.participants_json)
    assert isinstance(decoded, list)
    assert len(decoded) == 2
    emails = {p["email"] for p in decoded}
    assert emails == {"alice@acme.com", "partner@consultingco.com"}
    # display_name preserved for alice; partner has no display_name.
    by_email = {p["email"]: p for p in decoded}
    assert by_email["alice@acme.com"]["display_name"] == "Alice"


# ---------------------------------------------------------------------------
# Test 2: /upload/init without participants → participants_json is None
# ---------------------------------------------------------------------------


def test_upload_init_without_participants_leaves_participants_json_null():
    """Legacy path: no body.participants → UploadJob.participants_json is None.

    Regression guard so we don't silently encode an empty list (`"[]"`)
    where the legacy contract expects NULL.
    """
    from fastapi.testclient import TestClient
    from main import app

    captured_jobs: list = []

    class _AsyncSessionStub:
        def __init__(self):
            self.add = MagicMock(side_effect=captured_jobs.append)
            self.commit = AsyncMock()

    class _AsyncCM:
        def __init__(self):
            self._session = _AsyncSessionStub()

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    account_id = "acct-legacy-1"
    request_payload = {
        "filename": "legacy.wav",
        "mime_type": "audio/wav",
        "file_size": 2048,
        "account_id": account_id,
        # NO participants key
    }

    with patch("routers.upload.S3Service") as mock_s3_cls, \
         patch("routers.upload.get_async_session", new=lambda: _AsyncCM()):
        mock_s3 = MagicMock()
        mock_s3.generate_file_key.return_value = "tenants/foo/jobs/bar/legacy.wav"
        mock_s3.generate_presigned_put_url.return_value = (
            "https://example.com/upload",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        mock_s3_cls.return_value = mock_s3

        token = _make_jwt()
        client = TestClient(app)
        response = client.post(
            "/upload/init",
            json=request_payload,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Account-ID": account_id,
            },
        )

    assert response.status_code == 200, response.text
    assert len(captured_jobs) == 1
    job = captured_jobs[0]
    assert job.participants_json is None, (
        "legacy path (no body.participants) must leave participants_json "
        f"as None; got {job.participants_json!r}"
    )


def test_upload_init_preserves_explicit_empty_participants_as_empty_json_array():
    """Explicit `participants: []` round-trips as `"[]"` (not collapsed to None).

    Regression guard for Codex Round 3 P2: collapsing `[]` to `None` defeats
    the "explicit no participants — do NOT fall back to calendar" semantic
    Task 1.26.6 established for /text/clean. The upload path must honor
    the same contract so the worker passes `participants=[]` to enrich()
    instead of `participants=None`.
    """
    from fastapi.testclient import TestClient
    from main import app

    captured_jobs: list = []

    class _AsyncSessionStub:
        def __init__(self):
            self.add = MagicMock(side_effect=captured_jobs.append)
            self.commit = AsyncMock()

    class _AsyncCM:
        def __init__(self):
            self._session = _AsyncSessionStub()

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    account_id = "acct-empty-1"
    request_payload = {
        "filename": "empty.wav",
        "mime_type": "audio/wav",
        "file_size": 4096,
        "account_id": account_id,
        "participants": [],  # explicit empty list
    }

    with patch("routers.upload.S3Service") as mock_s3_cls, \
         patch("routers.upload.get_async_session", new=lambda: _AsyncCM()):
        mock_s3 = MagicMock()
        mock_s3.generate_file_key.return_value = "tenants/foo/jobs/bar/empty.wav"
        mock_s3.generate_presigned_put_url.return_value = (
            "https://example.com/upload",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        mock_s3_cls.return_value = mock_s3

        token = _make_jwt()
        client = TestClient(app)
        response = client.post(
            "/upload/init",
            json=request_payload,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Account-ID": account_id,
            },
        )

    assert response.status_code == 200, response.text
    assert len(captured_jobs) == 1
    job = captured_jobs[0]
    assert job.participants_json == "[]", (
        "explicit empty participants list MUST round-trip as the JSON string "
        "'[]' so the worker can distinguish 'caller explicitly said no one' "
        f"from 'caller did not provide the field'; got {job.participants_json!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: _process_upload_job deserializes participants_json and forwards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_deserializes_participants_json_and_forwards_to_enrich():
    """_process_upload_job reads UploadJob.participants_json, deserializes to
    a list[ParticipantSpec], and forwards `participants=` to enrich().

    Captures kwargs at the enrich() boundary.
    """
    from models.job_models import JobStatus, JobType, UploadJob
    from models.participant_spec import ParticipantSpec
    from routers.upload import _process_upload_job

    tenant_id = "11111111-1111-4111-8111-111111111111"
    account_id = "acct-participants-2"
    job_id = uuid.uuid4()
    interaction_id = uuid.uuid4()

    participants_payload = [
        {"email": "alice@acme.com", "display_name": "Alice"},
        {"email": "bob@example.com"},
    ]
    participants_json = json.dumps(participants_payload)

    job = UploadJob(
        id=job_id,
        tenant_id=uuid.UUID(tenant_id),
        user_id="auth0|user-1",
        pg_user_id=str(uuid.uuid4()),
        account_id=account_id,
        user_name="Test User",
        job_type=JobType.audio_transcription,
        status=JobStatus.queued,
        file_key="tenants/foo/jobs/bar/test.wav",
        file_name="test.wav",
        mime_type="audio/wav",
        file_size=1024,
        interaction_id=interaction_id,
        trace_id="trace-1",
        participants_json=participants_json,
    )

    captured_enrich_kwargs: dict = {}

    class _FakeEnrichment:
        contact_ids = None
        calendar_event_id = None
        match_confidence = None
        match_method = None
        front_matter = None

        def to_extras_dict(self):
            return {}

    async def fake_enrich(self, **kwargs):
        captured_enrich_kwargs.update(kwargs)
        return _FakeEnrichment()

    # Session that returns our `job` once for the processing-update fetch,
    # and again for the success-update fetch. Also tolerates the failure path.
    def _make_session_cm():
        sess = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        sess.execute = AsyncMock(return_value=result_mock)
        sess.commit = AsyncMock()

        class _CM:
            async def __aenter__(self_inner):
                return sess

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _CM()

    # Transcribe result with non-empty transcript so we hit the enrich() path.
    tx_result = MagicMock()
    tx_result.transcript = "hello world"
    tx_result.duration_seconds = 5
    tx_result.channels = 1
    tx_result.words = 2

    with patch("routers.upload.get_async_session", new=lambda: _make_session_cm()), \
         patch("routers.upload.S3Service") as mock_s3_cls, \
         patch("services.batch_service.BatchService.transcribe_from_url",
               new=AsyncMock(return_value=tx_result)), \
         patch("services.batch_cleaner_service.BatchCleanerService.clean_transcript",
               new=AsyncMock(return_value="hello world cleaned")), \
         patch("services.transcript_enrichment.TranscriptEnrichmentService.enrich",
               new=fake_enrich), \
         patch("services.aws_event_publisher.AWSEventPublisher.publish_envelope",
               new=AsyncMock(return_value={"kinesis_sequence": "1", "eventbridge_id": "2"})), \
         patch("services.intelligence_service.IntelligenceService.process_transcript",
               new=AsyncMock(return_value=None)), \
         patch("routers.upload.get_tenant_internal_domains",
               new=AsyncMock(return_value=set())):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_get_url.return_value = "https://example.com/get"
        mock_s3_cls.return_value = mock_s3

        await _process_upload_job(str(job_id), tenant_id)

    forwarded = captured_enrich_kwargs.get("participants")
    assert forwarded is not None, (
        "worker dropped participants_json on the floor — _process_upload_job "
        "must deserialize and forward to enrich()"
    )
    assert isinstance(forwarded, list)
    assert len(forwarded) == 2
    assert all(isinstance(p, ParticipantSpec) for p in forwarded), (
        "participants must be forwarded as ParticipantSpec instances, not raw dicts"
    )
    emails = {p.email for p in forwarded}
    assert emails == {"alice@acme.com", "bob@example.com"}


# ---------------------------------------------------------------------------
# Test 4: Worker passes participants=None when participants_json is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_passes_none_when_participants_json_is_null():
    """Legacy/null path: participants_json=None → enrich(participants=None).

    Caller-side completeness (Task 1.26.6): the worker must still pass
    `participants=None` (not omit the kwarg) so the enrich() contract is
    explicit at every call site.
    """
    from models.job_models import JobStatus, JobType, UploadJob
    from routers.upload import _process_upload_job

    tenant_id = "11111111-1111-4111-8111-111111111111"
    account_id = "acct-legacy-2"
    job_id = uuid.uuid4()
    interaction_id = uuid.uuid4()

    job = UploadJob(
        id=job_id,
        tenant_id=uuid.UUID(tenant_id),
        user_id="auth0|user-1",
        pg_user_id=str(uuid.uuid4()),
        account_id=account_id,
        user_name="Test User",
        job_type=JobType.audio_transcription,
        status=JobStatus.queued,
        file_key="tenants/foo/jobs/bar/legacy.wav",
        file_name="legacy.wav",
        mime_type="audio/wav",
        file_size=1024,
        interaction_id=interaction_id,
        trace_id="trace-2",
        participants_json=None,  # explicit null
    )

    captured_enrich_kwargs: dict = {}

    class _FakeEnrichment:
        contact_ids = None
        calendar_event_id = None
        match_confidence = None
        match_method = None
        front_matter = None

        def to_extras_dict(self):
            return {}

    async def fake_enrich(self, **kwargs):
        captured_enrich_kwargs.update(kwargs)
        return _FakeEnrichment()

    def _make_session_cm():
        sess = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        sess.execute = AsyncMock(return_value=result_mock)
        sess.commit = AsyncMock()

        class _CM:
            async def __aenter__(self_inner):
                return sess

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _CM()

    tx_result = MagicMock()
    tx_result.transcript = "hello world"
    tx_result.duration_seconds = 5
    tx_result.channels = 1
    tx_result.words = 2

    with patch("routers.upload.get_async_session", new=lambda: _make_session_cm()), \
         patch("routers.upload.S3Service") as mock_s3_cls, \
         patch("services.batch_service.BatchService.transcribe_from_url",
               new=AsyncMock(return_value=tx_result)), \
         patch("services.batch_cleaner_service.BatchCleanerService.clean_transcript",
               new=AsyncMock(return_value="hello world cleaned")), \
         patch("services.transcript_enrichment.TranscriptEnrichmentService.enrich",
               new=fake_enrich), \
         patch("services.aws_event_publisher.AWSEventPublisher.publish_envelope",
               new=AsyncMock(return_value={"kinesis_sequence": "1", "eventbridge_id": "2"})), \
         patch("services.intelligence_service.IntelligenceService.process_transcript",
               new=AsyncMock(return_value=None)), \
         patch("routers.upload.get_tenant_internal_domains",
               new=AsyncMock(return_value=set())):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_get_url.return_value = "https://example.com/get"
        mock_s3_cls.return_value = mock_s3

        await _process_upload_job(str(job_id), tenant_id)

    assert "participants" in captured_enrich_kwargs, (
        "worker omitted the participants kwarg — caller-side completeness "
        "(Task 1.26.6) requires explicit participants=None"
    )
    assert captured_enrich_kwargs["participants"] is None, (
        f"expected participants=None for legacy null path; got "
        f"{captured_enrich_kwargs['participants']!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: Corrupt participants_json → log + fall back to None (no crash)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_recovers_from_corrupt_participants_json():
    """A row with malformed participants_json must NOT crash the worker.

    Recommended fallback: log loudly + pass participants=None to enrich().
    The upload still processes; the participant signal is lost but the
    transcript is not.
    """
    from models.job_models import JobStatus, JobType, UploadJob
    from routers.upload import _process_upload_job

    tenant_id = "11111111-1111-4111-8111-111111111111"
    account_id = "acct-corrupt-1"
    job_id = uuid.uuid4()
    interaction_id = uuid.uuid4()

    job = UploadJob(
        id=job_id,
        tenant_id=uuid.UUID(tenant_id),
        user_id="auth0|user-1",
        pg_user_id=str(uuid.uuid4()),
        account_id=account_id,
        user_name="Test User",
        job_type=JobType.audio_transcription,
        status=JobStatus.queued,
        file_key="tenants/foo/jobs/bar/corrupt.wav",
        file_name="corrupt.wav",
        mime_type="audio/wav",
        file_size=1024,
        interaction_id=interaction_id,
        trace_id="trace-3",
        participants_json="{not valid json",  # malformed
    )

    captured_enrich_kwargs: dict = {}

    class _FakeEnrichment:
        contact_ids = None
        calendar_event_id = None
        match_confidence = None
        match_method = None
        front_matter = None

        def to_extras_dict(self):
            return {}

    async def fake_enrich(self, **kwargs):
        captured_enrich_kwargs.update(kwargs)
        return _FakeEnrichment()

    def _make_session_cm():
        sess = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = job
        sess.execute = AsyncMock(return_value=result_mock)
        sess.commit = AsyncMock()

        class _CM:
            async def __aenter__(self_inner):
                return sess

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _CM()

    tx_result = MagicMock()
    tx_result.transcript = "hello world"
    tx_result.duration_seconds = 5
    tx_result.channels = 1
    tx_result.words = 2

    with patch("routers.upload.get_async_session", new=lambda: _make_session_cm()), \
         patch("routers.upload.S3Service") as mock_s3_cls, \
         patch("services.batch_service.BatchService.transcribe_from_url",
               new=AsyncMock(return_value=tx_result)), \
         patch("services.batch_cleaner_service.BatchCleanerService.clean_transcript",
               new=AsyncMock(return_value="hello world cleaned")), \
         patch("services.transcript_enrichment.TranscriptEnrichmentService.enrich",
               new=fake_enrich), \
         patch("services.aws_event_publisher.AWSEventPublisher.publish_envelope",
               new=AsyncMock(return_value={"kinesis_sequence": "1", "eventbridge_id": "2"})), \
         patch("services.intelligence_service.IntelligenceService.process_transcript",
               new=AsyncMock(return_value=None)), \
         patch("routers.upload.get_tenant_internal_domains",
               new=AsyncMock(return_value=set())):
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_get_url.return_value = "https://example.com/get"
        mock_s3_cls.return_value = mock_s3

        # Must NOT raise — the worker should swallow the JSON error and
        # continue with participants=None.
        await _process_upload_job(str(job_id), tenant_id)

    assert "participants" in captured_enrich_kwargs
    assert captured_enrich_kwargs["participants"] is None, (
        "corrupt participants_json must fall back to participants=None; "
        f"got {captured_enrich_kwargs['participants']!r}"
    )
