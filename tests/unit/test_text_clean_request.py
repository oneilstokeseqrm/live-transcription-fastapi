"""TextCleanRequest requires account_id."""

import pytest
from pydantic import ValidationError
from models.text_request import TextCleanRequest
from models.participant_spec import ParticipantSpec


def test_rejects_missing_account_id():
    with pytest.raises(ValidationError):
        TextCleanRequest(text="hello")  # type: ignore[call-arg]


def test_accepts_with_account_id():
    req = TextCleanRequest(text="hello", account_id="acct-1")
    assert req.account_id == "acct-1"


def test_rejects_empty_account_id():
    with pytest.raises(ValidationError):
        TextCleanRequest(text="hello", account_id="")


def test_text_clean_accepts_participants():
    req = TextCleanRequest(
        text="meeting note",
        account_id="acct-1",
        participants=[
            ParticipantSpec(email="alice@acme.com", display_name="Alice"),
            ParticipantSpec(email="bob@acme.com"),
        ],
    )
    assert req.participants is not None
    assert len(req.participants) == 2
    assert req.participants[0].display_name == "Alice"
