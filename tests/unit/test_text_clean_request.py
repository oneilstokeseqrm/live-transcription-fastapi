"""TextCleanRequest requires account_id."""

import pytest
from pydantic import ValidationError
from models.text_request import TextCleanRequest


def test_rejects_missing_account_id():
    with pytest.raises(ValidationError):
        TextCleanRequest(text="hello")  # type: ignore[call-arg]


def test_accepts_with_account_id():
    req = TextCleanRequest(text="hello", account_id="acct-1")
    assert req.account_id == "acct-1"


def test_rejects_empty_account_id():
    with pytest.raises(ValidationError):
        TextCleanRequest(text="hello", account_id="")
