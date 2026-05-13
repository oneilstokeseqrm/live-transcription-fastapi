"""ParticipantSpec — caller-provided participants on ingestion endpoints."""

import pytest
from pydantic import ValidationError
from models.participant_spec import ParticipantSpec


def test_minimal_spec_requires_email():
    with pytest.raises(ValidationError):
        ParticipantSpec()  # type: ignore[call-arg]


def test_email_only_is_valid():
    spec = ParticipantSpec(email="alice@acme.com")
    assert spec.email == "alice@acme.com"
    assert spec.display_name is None
    assert spec.role is None


def test_full_spec():
    spec = ParticipantSpec(
        email="alice@acme.com",
        display_name="Alice Smith",
        role="organizer",
    )
    assert spec.display_name == "Alice Smith"
    assert spec.role == "organizer"


def test_invalid_email():
    with pytest.raises(ValidationError):
        ParticipantSpec(email="not-an-email")


def test_role_must_be_allowed_value():
    with pytest.raises(ValidationError):
        ParticipantSpec(email="a@b.com", role="random-role")  # type: ignore[arg-type]
