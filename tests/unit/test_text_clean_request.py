"""TextCleanRequest requires account_id."""

from datetime import datetime, timezone, timedelta

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


# ---------------------------------------------------------------------------
# occurred_at — optional event-time field (EQ-230 / A1)
#
# The field is additive and optional. When omitted it stays None so the route
# defaults the interaction timestamp to now() (the byte-for-byte-unchanged
# real-time path). When supplied it must be an unambiguous instant: the
# validator rejects naive datetimes (422) and normalizes any offset-aware
# value to UTC so every downstream consumer (envelope serializer, front-matter
# strftime, Lane-2) sees a clean aware-UTC value. Trust + bounds are NOT the
# field's job — they depend on request context + now() and live in the route
# (resolve_event_time, EQ-230 U3).
# ---------------------------------------------------------------------------


def _req(**occurred_at) -> TextCleanRequest:
    """Build a request from wire-shaped input (mirrors JSON request parsing).

    model_validate takes a dict[str, Any], so string occurred_at values go
    through the same coercion path a real HTTP request would — and the static
    type checker doesn't complain about ISO strings vs the datetime field.
    """
    payload = {"text": "hello", "account_id": "acct-1", **occurred_at}
    return TextCleanRequest.model_validate(payload)


def test_occurred_at_omitted_defaults_to_none():
    req = TextCleanRequest(text="hello", account_id="acct-1")
    assert req.occurred_at is None


def test_occurred_at_z_suffix_parsed_as_aware_utc():
    req = _req(occurred_at="2026-01-10T09:00:00Z")
    assert req.occurred_at is not None
    assert req.occurred_at == datetime(2026, 1, 10, 9, 0, 0, tzinfo=timezone.utc)
    assert req.occurred_at.utcoffset() == timedelta(0)


def test_occurred_at_offset_aware_normalized_to_utc():
    # 09:00 at -05:00 is 14:00 UTC; the stored value must already be UTC so the
    # envelope serializer (which blindly appends 'Z') and front-matter strftime
    # cannot lie about the offset.
    req = _req(occurred_at="2026-01-10T09:00:00-05:00")
    assert req.occurred_at is not None
    assert req.occurred_at == datetime(2026, 1, 10, 14, 0, 0, tzinfo=timezone.utc)
    assert req.occurred_at.utcoffset() == timedelta(0)


def test_occurred_at_aware_utc_datetime_object_preserved():
    dt = datetime(2025, 12, 1, 8, 30, tzinfo=timezone.utc)
    req = TextCleanRequest(text="hello", account_id="acct-1", occurred_at=dt)
    assert req.occurred_at is not None
    assert req.occurred_at == dt
    assert req.occurred_at.utcoffset() == timedelta(0)


def test_occurred_at_naive_string_rejected():
    # No tzinfo => ambiguous instant => 422 (unprocessable field value).
    with pytest.raises(ValidationError):
        _req(occurred_at="2026-01-10T09:00:00")


def test_occurred_at_naive_datetime_object_rejected():
    with pytest.raises(ValidationError):
        TextCleanRequest(
            text="hello",
            account_id="acct-1",
            occurred_at=datetime(2026, 1, 10, 9, 0, 0),  # naive
        )


def test_occurred_at_malformed_string_rejected():
    with pytest.raises(ValidationError):
        _req(occurred_at="not-a-date")


def test_occurred_at_numeric_epoch_seconds_rejected():
    # Pydantic would otherwise coerce an int to a Unix-seconds datetime. We
    # documented ISO-8601; epoch numbers are an ambiguous footgun (seconds vs
    # millis) so the wire contract rejects them outright.
    with pytest.raises(ValidationError):
        _req(occurred_at=1760000000)


def test_occurred_at_numeric_epoch_millis_rejected():
    with pytest.raises(ValidationError):
        _req(occurred_at=1760000000000)


def test_occurred_at_float_rejected():
    with pytest.raises(ValidationError):
        _req(occurred_at=1.5)


def test_occurred_at_assignment_naive_revalidated():
    # validate_assignment closes the post-construction mutation bypass: a naive
    # value can't be smuggled in by setting the attribute after construction.
    req = _req(occurred_at="2026-01-10T09:00:00Z")
    with pytest.raises(ValidationError):
        req.occurred_at = datetime(2026, 1, 10, 9, 0, 0)  # naive


def test_occurred_at_assignment_numeric_revalidated():
    req = TextCleanRequest(text="hello", account_id="acct-1")
    with pytest.raises(ValidationError):
        req.occurred_at = 1760000000  # type: ignore[assignment]
