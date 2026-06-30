"""Event-time resolution for the optional ``occurred_at`` field (EQ-230 / A1).

Decides the interaction's event-time from a caller-supplied ``occurred_at``,
the request's trust flag, and the current time. This is the route-level policy
half of the occurred_at contract; the *value*-level half (parse / naive / UTC
normalization, HTTP 422) lives in the ``TextCleanRequest`` validator.

Design notes:

* ``now`` is injected (must be aware-UTC) so bounds are deterministic in tests
  and the route stays the single owner of "what time is it".
* Out-of-bounds values are rejected **loudly** (HTTP 400), never silently
  clamped — a clamped historical date would corrupt the timeline invisibly.
* An untrusted caller's ``occurred_at`` is **ignored** (fall back to now()),
  not rejected — the field is additive and non-breaking; an untrusted caller
  that happens to send it must behave exactly like today.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Freshness guardrails (DECISIONS-LOG #4/#14, widened in #25): accept event-times
# up to ~3 years in the past; allow a small future skew for clock drift between
# the caller and this service. Reject anything outside that window.
# Widened 2y->3y (730->1095d) so multi-year synthetic CRM histories (the
# long_term_customer archetype reaches ~2.1y back) aren't rejected. Dev-only need.
MAX_PAST_AGE = timedelta(days=1095)  # ~3 years
MAX_FUTURE_SKEW = timedelta(minutes=5)


def resolve_event_time(
    occurred_at: Optional[datetime],
    *,
    trusted: bool,
    now: datetime,
) -> datetime:
    """Resolve the interaction event-time.

    Args:
        occurred_at: The caller-supplied event-time, or None. When it comes from
            the HTTP route it is already aware-UTC (the TextCleanRequest
            validator rejects naive values with 422). The naive guard below is
            defense-in-depth for any non-HTTP caller.
        trusted: True only when identity was proven via the verified internal-JWT
            path (``RequestContext.trusted_event_time``). Never inferred from a
            header.
        now: The current time as an aware-UTC datetime (the caller passes
            ``datetime.now(timezone.utc)``). Used as the default event-time and
            as the reference for bounds.

    Returns:
        The resolved event-time as an aware-UTC datetime: ``now`` when
        ``occurred_at`` is omitted or the caller is untrusted; otherwise the
        supplied instant normalized to UTC.

    Raises:
        HTTPException: 400 when a trusted, well-formed ``occurred_at`` is outside
            the accepted window (older than ~3 years or more than ~5 minutes in
            the future), or — defense-in-depth — if a naive ``occurred_at``
            reaches this helper on the trusted path.
        ValueError: when ``now`` is naive. ``now`` is supplied by our own code
            (the route passes ``datetime.now(timezone.utc)``), so a naive value
            is a programmer error — fail loud rather than return a naive
            timestamp (which the 'append Z' envelope serializer would mislabel)
            or hit a TypeError in the aware-vs-naive bounds comparison.
    """
    # ``now`` must be aware-UTC. Validate FIRST so even the omitted/untrusted
    # fast-paths can't return a naive timestamp.
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(
            "resolve_event_time: 'now' must be timezone-aware "
            "(pass datetime.now(timezone.utc))"
        )

    # Omitted => today's behavior, unchanged. This is the path real-time
    # ingestion takes and it must stay byte-for-byte identical to pre-occurred_at.
    if occurred_at is None:
        return now

    # Untrusted callers may not backdate. Ignore (don't reject) so the field
    # stays additive/non-breaking, but make the decision observable. ``%s``
    # (not ``.isoformat()``) so the log can't itself crash on an off-type value.
    if not trusted:
        logger.warning(
            "Ignoring occurred_at from untrusted caller; falling back to now(). "
            "occurred_at=%s",
            occurred_at,
        )
        return now

    # Defense-in-depth: a naive datetime is an ambiguous instant. The HTTP route
    # can't reach here with one (the validator already returned 422), but a
    # direct/future caller could — fail loud rather than let astimezone() assume
    # local time and silently shift the value.
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise HTTPException(
            status_code=400,
            detail="occurred_at must be timezone-aware",
        )

    candidate = occurred_at.astimezone(timezone.utc)

    if candidate > now + MAX_FUTURE_SKEW:
        raise HTTPException(
            status_code=400,
            detail=(
                "occurred_at is too far in the future "
                f"(max +{int(MAX_FUTURE_SKEW.total_seconds() // 60)} minutes)"
            ),
        )
    if candidate < now - MAX_PAST_AGE:
        raise HTTPException(
            status_code=400,
            detail=(
                "occurred_at is too far in the past "
                f"(max {MAX_PAST_AGE.days} days ago)"
            ),
        )

    return candidate
