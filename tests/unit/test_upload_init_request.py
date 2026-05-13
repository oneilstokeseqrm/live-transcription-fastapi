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
