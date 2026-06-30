"""resolve_event_time — trust + freshness policy for occurred_at (EQ-230 / A1).

This is the route-level half of the occurred_at contract. The TextCleanRequest
validator already guarantees ``occurred_at`` is either None or aware-UTC (naive
=> 422 at the boundary). This helper decides *whether to honor it*:

* omitted  -> now()  (the byte-for-byte-unchanged real-time path)
* untrusted caller -> now()  (ignore + warn; never reject — non-breaking)
* trusted + in-bounds -> the supplied instant (normalized to UTC)
* trusted + out-of-bounds -> HTTP 400 (loud, never silently clamped)

``now`` is injected so the bounds are deterministic under test.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from utils.event_time import (
    MAX_FUTURE_SKEW,
    MAX_PAST_AGE,
    resolve_event_time,
)

NOW = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)


def test_omitted_returns_now():
    assert resolve_event_time(None, trusted=True, now=NOW) == NOW


def test_untrusted_present_is_ignored_and_returns_now(caplog):
    past = NOW - timedelta(days=100)
    with caplog.at_level("WARNING"):
        result = resolve_event_time(past, trusted=False, now=NOW)
    assert result == NOW
    # Ignoring a supplied event-time is a security-relevant decision: it must
    # be observable, not silent.
    assert any("occurred_at" in r.message for r in caplog.records)


def test_trusted_valid_past_is_returned():
    past = NOW - timedelta(days=100)
    assert resolve_event_time(past, trusted=True, now=NOW) == past


def test_trusted_offset_aware_is_normalized_to_utc():
    # 2026-06-01T09:00:00-05:00 == 2026-06-01T14:00:00Z
    offset = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    result = resolve_event_time(offset, trusted=True, now=NOW)
    assert result == datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc)
    assert result.utcoffset() == timedelta(0)


def test_trusted_future_beyond_skew_rejected():
    future = NOW + MAX_FUTURE_SKEW + timedelta(seconds=1)
    with pytest.raises(HTTPException) as exc:
        resolve_event_time(future, trusted=True, now=NOW)
    assert exc.value.status_code == 400


def test_trusted_future_within_skew_accepted():
    future = NOW + MAX_FUTURE_SKEW - timedelta(seconds=1)
    assert resolve_event_time(future, trusted=True, now=NOW) == future


def test_trusted_future_exactly_at_skew_accepted():
    future = NOW + MAX_FUTURE_SKEW
    assert resolve_event_time(future, trusted=True, now=NOW) == future


def test_trusted_older_than_max_age_rejected():
    too_old = NOW - MAX_PAST_AGE - timedelta(seconds=1)
    with pytest.raises(HTTPException) as exc:
        resolve_event_time(too_old, trusted=True, now=NOW)
    assert exc.value.status_code == 400


def test_trusted_exactly_at_max_age_accepted():
    boundary = NOW - MAX_PAST_AGE
    assert resolve_event_time(boundary, trusted=True, now=NOW) == boundary


def test_naive_now_rejected_on_omitted_path():
    """A naive ``now`` is a caller (EQ-231) bug — fail loud, even when occurred_at
    is omitted, so a naive timestamp never reaches the 'append Z' serializer."""
    naive_now = datetime(2026, 6, 30, 12, 0, 0)  # no tzinfo
    with pytest.raises(ValueError):
        resolve_event_time(None, trusted=True, now=naive_now)


def test_naive_now_rejected_before_comparison():
    """Guard fires before the aware-vs-naive comparison would TypeError."""
    naive_now = datetime(2026, 6, 30, 12, 0, 0)
    past = datetime(2026, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        resolve_event_time(past, trusted=True, now=naive_now)


def test_naive_input_guarded_defense_in_depth():
    """Naive can't reach here via HTTP (validator => 422), but guard anyway.

    A non-HTTP/future caller must not be able to slip a naive (ambiguous)
    instant past the bounds check, where astimezone() would silently assume
    local time. The guard fails loud with 400 rather than mis-converting.
    """
    naive = datetime(2026, 6, 1, 9, 0, 0)  # no tzinfo
    with pytest.raises(HTTPException) as exc:
        resolve_event_time(naive, trusted=True, now=NOW)
    assert exc.value.status_code == 400
