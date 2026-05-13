"""UploadInitRequest requires account_id."""

import pytest
from pydantic import ValidationError
from routers.upload import UploadInitRequest


def test_rejects_missing_account_id():
    with pytest.raises(ValidationError):
        UploadInitRequest(filename="x.wav")  # type: ignore[call-arg]


def test_accepts_with_account_id():
    req = UploadInitRequest(filename="x.wav", account_id="acct-1")
    assert req.account_id == "acct-1"


def test_rejects_empty_account_id():
    with pytest.raises(ValidationError):
        UploadInitRequest(filename="x.wav", account_id="")


from models.job_models import UploadJob, JobStatus, JobType
import uuid
from datetime import datetime, timezone


def test_upload_job_requires_account_id():
    # SQLModel raises ValidationError if account_id missing, similar to pydantic.
    # Note: with table=True SQLModel skips validation in __init__, so we use
    # model_validate() to assert the field is declared required at the schema level.
    with pytest.raises(Exception):  # accept ValueError or ValidationError variants
        UploadJob.model_validate({
            "id": uuid.uuid4(),
            "tenant_id": uuid.uuid4(),
            "user_id": "user-1",
            "pg_user_id": "pg-1",
            "user_name": "U",
            "job_type": JobType.audio_transcription,
            "status": JobStatus.queued,
            "file_key": "k",
            "interaction_id": uuid.uuid4(),
            "created_at": datetime.now(timezone.utc),
        })
