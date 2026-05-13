"""Verify EnvelopeV1 requires account_id at construction time."""

import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from datetime import datetime, timezone
from models.envelope import EnvelopeV1, ContentModel


def _base_kwargs() -> dict[str, Any]:
    return dict(
        tenant_id=uuid.uuid4(),
        user_id="user-1",
        interaction_type="meeting",
        content=ContentModel(text="hi", format="diarized"),
        timestamp=datetime.now(timezone.utc),
        source="api",
        interaction_id=uuid.uuid4(),
        trace_id="trace-1",
    )


def test_envelope_rejects_missing_account_id():
    with pytest.raises(ValidationError):
        EnvelopeV1(**_base_kwargs())


def test_envelope_accepts_string_account_id():
    env = EnvelopeV1(**_base_kwargs(), account_id="acct-123")
    assert env.account_id == "acct-123"


def test_envelope_rejects_none_account_id():
    with pytest.raises(ValidationError):
        EnvelopeV1(**_base_kwargs(), account_id=None)  # type: ignore[arg-type]
