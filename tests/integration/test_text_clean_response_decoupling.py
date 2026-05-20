"""`/text/clean` returns its response BEFORE Lane 2 (intelligence extraction) completes.

Pins the fix for the 2026-05-19 server-disconnect pattern observed during
sustained synthetic injection (Anthropic 6/6 OK → Linear 3/3 OK → Snowflake
1/7 OK). Root cause: Lane 2 ran synchronously under ``asyncio.gather`` before
the HTTP response was returned, so Railway's edge proxy killed the client TCP
connection while the LLM call continued server-side and wrote to Neon. The
client saw ``RemoteProtocolError`` even though the work succeeded.

Contract pinned here:

1. ``/text/clean`` returns its response in a bounded budget (well under the
   Lane 2 latency), proving Lane 2 is not awaited before the response.
2. After the response is returned, Lane 2 still runs — proving fire-and-forget
   completes the side-effect work.
3. Lane 1 (publish) is also fired without blocking the response.

The test is mock-only (no Neon writes) — Peter's hard constraint on
2026-05-19 was: do not touch the EQ test tenant during this investigation.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# Legacy-header auth path: lets the test skip JWT issuance entirely. Must be
# set before ``main`` is imported because ``get_auth_context_ingestion``
# reads the env var on every request, but the JWT secret env vars are read at
# import time by ``middleware.jwt_auth``.
os.environ.setdefault("ALLOW_LEGACY_HEADER_AUTH", "true")
os.environ.setdefault("INTERNAL_JWT_SECRET", "test-secret-that-is-at-least-32-characters-long")
os.environ.setdefault("INTERNAL_JWT_ISSUER", "eq-frontend")
os.environ.setdefault("INTERNAL_JWT_AUDIENCE", "eq-backend")
os.environ.setdefault("DEEPGRAM_API_KEY", "test-deepgram-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("DATABASE_URL", os.environ.get("DATABASE_URL", "postgresql+asyncpg://noop"))


# Lane 2 simulated wall-clock. Real Lane 2 takes 100-220s in production; we
# scale to 0.5s for a fast test. The assertion budget is ``LANE_2_DURATION_S
# / 5`` — generous enough to avoid CI flakes, tight enough to fail loudly
# if Lane 2 ever moves back onto the response path.
LANE_2_DURATION_S = 0.5
RESPONSE_BUDGET_S = LANE_2_DURATION_S / 5  # = 0.1s


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


@pytest.fixture
def valid_headers():
    return {
        "X-Tenant-ID": str(uuid.uuid4()),
        "X-User-ID": "auth0|test-user-001",
        "X-Trace-Id": str(uuid.uuid4()),
        "X-Account-ID": str(uuid.uuid4()),
    }


def _build_enrichment_mock():
    """Patch TranscriptEnrichmentService so enrich() returns an empty
    front-matter result and does not touch Neon."""
    enrichment_result = MagicMock()
    enrichment_result.front_matter = ""
    enrichment_result.contact_ids = []
    enrichment_result.calendar_event_id = None
    enrichment_result.match_confidence = None
    enrichment_result.match_method = None
    enrichment_result.to_extras_dict = MagicMock(return_value={})

    enrich_instance = MagicMock()
    enrich_instance.enrich = AsyncMock(return_value=enrichment_result)
    return enrich_instance


def test_text_clean_returns_before_lane_2_completes(client, valid_headers):
    """The response arrives in << Lane 2 wall-clock — proves Lane 2 is not
    awaited on the response path.

    Failure mode this guards against (regression from before the fix):
    if a future refactor re-introduces ``await asyncio.gather(_lane1,
    _lane2)`` before ``return TextCleanResponse(...)``, this assertion
    will fail.
    """
    lane_2_started = asyncio.Event()
    lane_2_completed = asyncio.Event()

    async def slow_process_transcript(**_kwargs):
        # Mark started, sleep, mark completed. The sleep is what makes
        # this test load-bearing: if Lane 2 were awaited, the response
        # would block for LANE_2_DURATION_S.
        lane_2_started.set()
        await asyncio.sleep(LANE_2_DURATION_S)
        lane_2_completed.set()
        return MagicMock()

    intelligence_instance = MagicMock()
    intelligence_instance.process_transcript = AsyncMock(side_effect=slow_process_transcript)

    publisher_instance = MagicMock()
    publisher_instance.publish_envelope = AsyncMock(
        return_value={"kinesis_sequence": "seq-1", "eventbridge_id": "evt-1"}
    )

    cleaner_instance = MagicMock()
    cleaner_instance.clean_transcript = AsyncMock(return_value="Cleaned text content")

    with patch("routers.text.BatchCleanerService", return_value=cleaner_instance), \
         patch("routers.text.IntelligenceService", return_value=intelligence_instance), \
         patch("routers.text.AWSEventPublisher", return_value=publisher_instance), \
         patch("routers.text.TranscriptEnrichmentService", return_value=_build_enrichment_mock()), \
         patch("routers.text.get_tenant_internal_domains", new=AsyncMock(return_value=[])):

        body = {
            "text": "This is some raw text to clean",
            "account_id": valid_headers["X-Account-ID"],
        }

        t0 = time.perf_counter()
        response = client.post("/text/clean", json=body, headers=valid_headers)
        elapsed = time.perf_counter() - t0

    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )

    data = response.json()
    assert data["raw_text"] == body["text"]
    assert data["cleaned_text"] == "Cleaned text content"
    assert uuid.UUID(data["interaction_id"])  # valid UUID

    assert elapsed < RESPONSE_BUDGET_S, (
        f"/text/clean response took {elapsed:.3f}s; budget is {RESPONSE_BUDGET_S:.3f}s "
        f"(Lane 2 simulated at {LANE_2_DURATION_S:.3f}s). If this assertion fails, "
        "Lane 2 has been re-introduced onto the response path — see "
        "tests/integration/test_text_clean_response_decoupling.py docstring."
    )


def test_text_clean_lane2_still_completes_after_response(client, valid_headers):
    """Companion to the budget test: fire-and-forget must actually fire.

    Asserts that after the response returns, Lane 2's ``process_transcript``
    DID get called (and would have written intelligence to Neon in
    production). This guards against the opposite regression: returning
    fast by silently dropping Lane 2 entirely.
    """
    intelligence_called = asyncio.Event()
    captured_kwargs: dict = {}

    async def fast_process_transcript(**kwargs):
        captured_kwargs.update(kwargs)
        intelligence_called.set()
        return MagicMock()

    intelligence_instance = MagicMock()
    intelligence_instance.process_transcript = AsyncMock(side_effect=fast_process_transcript)

    publisher_instance = MagicMock()
    publisher_instance.publish_envelope = AsyncMock(
        return_value={"kinesis_sequence": "seq-1", "eventbridge_id": "evt-1"}
    )

    cleaner_instance = MagicMock()
    cleaner_instance.clean_transcript = AsyncMock(return_value="Cleaned text content")

    with patch("routers.text.BatchCleanerService", return_value=cleaner_instance), \
         patch("routers.text.IntelligenceService", return_value=intelligence_instance), \
         patch("routers.text.AWSEventPublisher", return_value=publisher_instance), \
         patch("routers.text.TranscriptEnrichmentService", return_value=_build_enrichment_mock()), \
         patch("routers.text.get_tenant_internal_domains", new=AsyncMock(return_value=[])):

        body = {
            "text": "This is some raw text to clean",
            "account_id": valid_headers["X-Account-ID"],
        }
        response = client.post("/text/clean", json=body, headers=valid_headers)

        assert response.status_code == 200

        # TestClient ran the request in a private event loop. The Lane 2 task
        # was scheduled on that loop and SHOULD have completed before the
        # loop was torn down — Starlette's TestClient awaits all
        # outstanding async work in the response lifecycle. The mock having
        # been called is sufficient proof; we don't poll because the loop
        # is already closed by this point.
        intelligence_instance.process_transcript.assert_called_once()

    # Verify Lane 2 received the right inputs (would-be-written to Neon).
    assert captured_kwargs["cleaned_transcript"] == "Cleaned text content"
    assert captured_kwargs["account_id"] == valid_headers["X-Account-ID"]
    assert captured_kwargs["tenant_id"] == valid_headers["X-Tenant-ID"]
    publisher_instance.publish_envelope.assert_called_once()
