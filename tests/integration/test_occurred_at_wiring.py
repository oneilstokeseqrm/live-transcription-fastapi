"""End-to-end wiring of occurred_at through /text/clean (EQ-231 / A2).

EQ-230 built the parts (the occurred_at field + validator, the
trusted_event_time flag, resolve_event_time bounds). EQ-231 wires them into the
route so the resolved event-time becomes the SINGLE source feeding BOTH:

* ``enrich(transcript_timestamp=...)`` — which drives the calendar match window
  (``_match_by_time_window`` / ``_match_by_conference_url`` both key off the
  passed ts) AND the front-matter date (``strftime`` at transcript_enrichment).
* ``EnvelopeV1.timestamp`` — Lane-1 / downstream event-time.

The safety property (tested by the omitted-occurred_at characterization test):
when occurred_at is omitted, the route stamps now() exactly as before, and the
two sinks above receive the SAME value (single source).

These tests patch the auth context directly so they can pin trusted_event_time
without minting JWTs (the flag-setting itself is covered in
tests/unit/test_trusted_event_time.py). They capture the timestamp at the
enrich() boundary and the envelope at the Lane-1 publish boundary.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class _FakeEnrichment:
    contact_ids = None
    calendar_event_id = None
    match_confidence = None
    match_method = None
    front_matter = None

    def to_extras_dict(self):
        return {}


def _run_clean(*, occurred_at=None, trusted=True):
    """POST /text/clean with a controlled context; capture the wired timestamps.

    Returns (response, captured) where captured has 'enrich_ts' (the
    transcript_timestamp passed to enrich) and 'envelope' (the EnvelopeV1 handed
    to Lane-1 publish).
    """
    from main import app
    from models.request_context import RequestContext

    captured: dict = {}
    account_id = str(uuid.uuid4())

    fake_context = RequestContext(
        tenant_id="11111111-1111-4111-8111-111111111111",
        user_id="auth-user-id",
        pg_user_id=str(uuid.uuid4()),
        account_id=account_id,
        interaction_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        user_name="Test User",
        trusted_event_time=trusted,
    )

    async def fake_enrich(self, **kwargs):
        captured["enrich_ts"] = kwargs.get("transcript_timestamp")
        return _FakeEnrichment()

    async def fake_clean(text: str):
        return text

    async def fake_publish(envelope):
        captured["envelope"] = envelope
        return {"kinesis_sequence": "seq-1", "eventbridge_id": "ev-1"}

    async def fake_intel(**kwargs):
        captured["lane2_interaction_timestamp"] = kwargs.get("interaction_timestamp")
        return None

    async def fake_internal_domains(tenant_id: str):
        return set()

    payload = {"text": "hello world", "interaction_type": "note", "account_id": account_id}
    if occurred_at is not None:
        payload["occurred_at"] = occurred_at

    with patch(
        "routers.text.get_auth_context_ingestion", return_value=fake_context,
    ), patch(
        "services.transcript_enrichment.TranscriptEnrichmentService.enrich",
        new=fake_enrich,
    ), patch(
        "services.batch_cleaner_service.BatchCleanerService.clean_transcript",
        new=AsyncMock(side_effect=fake_clean),
    ), patch(
        "services.aws_event_publisher.AWSEventPublisher.publish_envelope",
        new=AsyncMock(side_effect=fake_publish),
    ), patch(
        "services.intelligence_service.IntelligenceService.process_transcript",
        new=AsyncMock(side_effect=fake_intel),
    ), patch(
        "routers.text.get_tenant_internal_domains",
        new=AsyncMock(side_effect=fake_internal_domains),
    ):
        client = TestClient(app)
        response = client.post(
            "/text/clean", json=payload, headers={"Authorization": "Bearer fake"}
        )
    return response, captured


def test_omitted_occurred_at_stamps_now_single_source():
    """Characterization: omitted occurred_at => now(), and enrich + envelope get
    the SAME value (single source). This is the byte-for-byte-unchanged path."""
    before = datetime.now(timezone.utc)
    response, captured = _run_clean(occurred_at=None, trusted=True)
    after = datetime.now(timezone.utc)

    assert response.status_code == 200, response.text
    enrich_ts = captured["enrich_ts"]
    envelope_ts = captured["envelope"].timestamp
    # Single source: the same instant flows to both sinks.
    assert enrich_ts == envelope_ts
    # It's now() — aware-UTC and bracketed by the call.
    assert enrich_ts.tzinfo is not None
    assert before <= enrich_ts <= after


def test_trusted_past_occurred_at_preserved_in_enrich_and_envelope():
    past = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
    response, captured = _run_clean(occurred_at=past.isoformat(), trusted=True)

    assert response.status_code == 200, response.text
    assert captured["enrich_ts"] == past
    assert captured["envelope"].timestamp == past


def test_trusted_offset_aware_occurred_at_normalized_to_utc():
    # 2026-03-01T09:30:00-05:00 == 2026-03-01T14:30:00Z
    response, captured = _run_clean(
        occurred_at="2026-03-01T09:30:00-05:00", trusted=True
    )
    expected = datetime(2026, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
    assert response.status_code == 200, response.text
    assert captured["enrich_ts"] == expected
    assert captured["envelope"].timestamp == expected
    assert captured["envelope"].timestamp.utcoffset() == timedelta(0)


def test_untrusted_occurred_at_is_ignored_and_uses_now():
    past = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    before = datetime.now(timezone.utc)
    response, captured = _run_clean(occurred_at=past.isoformat(), trusted=False)
    after = datetime.now(timezone.utc)

    assert response.status_code == 200, response.text
    # The historical date is NOT honored for an untrusted caller.
    assert captured["enrich_ts"] != past
    assert before <= captured["enrich_ts"] <= after
    assert captured["envelope"].timestamp == captured["enrich_ts"]


def test_trusted_future_occurred_at_rejected_400():
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    response, _ = _run_clean(occurred_at=future.isoformat(), trusted=True)
    assert response.status_code == 400, response.text


def test_trusted_too_old_occurred_at_rejected_400():
    too_old = datetime.now(timezone.utc) - timedelta(days=800)
    response, _ = _run_clean(occurred_at=too_old.isoformat(), trusted=True)
    assert response.status_code == 400, response.text


def test_naive_occurred_at_rejected_422():
    # Validator rejects naive at the boundary (before any route policy).
    response, _ = _run_clean(occurred_at="2026-03-01T14:30:00", trusted=True)
    assert response.status_code == 422, response.text
