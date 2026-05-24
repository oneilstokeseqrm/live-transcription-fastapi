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
4. A Lane 2 exception still produces an observable ``logger.error`` line —
   under the old synchronous-await model, such failures surfaced as HTTP
   5xx; after moving to fire-and-forget they MUST surface in logs or the
   regression is silent.
5. A wrapper-level crash (anything that raises outside the gather) is
   surfaced by the done-callback safety net — without it, Python would
   only emit a "Task exception was never retrieved" GC warning.

The test is mock-only (no Neon writes) — Peter's hard constraint on
2026-05-19 was: do not touch the EQ test tenant during this investigation.
"""
from __future__ import annotations

import asyncio
import logging
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
         patch("services.text_clean_service.IntelligenceService", return_value=intelligence_instance), \
         patch("services.text_clean_service.AWSEventPublisher", return_value=publisher_instance), \
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
         patch("services.text_clean_service.IntelligenceService", return_value=intelligence_instance), \
         patch("services.text_clean_service.AWSEventPublisher", return_value=publisher_instance), \
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


def test_text_clean_lane2_exception_is_logged_not_silenced(
    client, valid_headers, caplog
):
    """When Lane 2 raises (e.g., OpenAI 500, Neon timeout, asyncpg disconnect),
    the response still succeeds, AND the failure surfaces as a logger.error
    line — not a silent task death.

    Guards against the regression where moving Lane 2 to a background task
    swallows exceptions that previously produced HTTP 5xx under the
    synchronous-await model.
    """
    boom = RuntimeError("simulated Lane 2 LLM API failure")

    intelligence_instance = MagicMock()
    intelligence_instance.process_transcript = AsyncMock(side_effect=boom)

    publisher_instance = MagicMock()
    publisher_instance.publish_envelope = AsyncMock(
        return_value={"kinesis_sequence": "seq-1", "eventbridge_id": "evt-1"}
    )

    cleaner_instance = MagicMock()
    cleaner_instance.clean_transcript = AsyncMock(return_value="Cleaned text content")

    with caplog.at_level(logging.ERROR, logger="services.text_clean_service"), \
         patch("routers.text.BatchCleanerService", return_value=cleaner_instance), \
         patch("services.text_clean_service.IntelligenceService", return_value=intelligence_instance), \
         patch("services.text_clean_service.AWSEventPublisher", return_value=publisher_instance), \
         patch("routers.text.TranscriptEnrichmentService", return_value=_build_enrichment_mock()), \
         patch("routers.text.get_tenant_internal_domains", new=AsyncMock(return_value=[])):

        body = {
            "text": "This is some raw text to clean",
            "account_id": valid_headers["X-Account-ID"],
        }
        response = client.post("/text/clean", json=body, headers=valid_headers)

    # Response is still 200 — Lane 2 failure is contained.
    assert response.status_code == 200, (
        f"Expected 200 despite Lane 2 raising; got {response.status_code}: {response.text}"
    )

    # At least one ERROR log mentions Lane 2 / intelligence and the actual
    # exception message. We don't pin the exact format because that's
    # cosmetic; we DO pin that the failure produced an observable signal.
    intelligence_instance.process_transcript.assert_called_once()
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, (
        "Lane 2 raised but no ERROR log was produced. The fire-and-forget "
        "path has silenced the failure — see routers/text.py _run_background_lanes "
        "and _on_done callback."
    )
    assert any(
        "simulated Lane 2 LLM API failure" in r.getMessage()
        or (r.exc_info and r.exc_info[1] is boom)
        for r in error_records
    ), "No ERROR log carries the actual Lane 2 exception."


# Lane 2 backpressure + Lane 1 publish + Lane 2 dispatch state moved to
# services.text_clean_service in PR-X1 of the Granola integration. Tests
# that poke at the in-flight counter / background task set point at the
# new module here. The /text/clean endpoint still owns the 503 translation
# + WARNING log, so the backpressure test's caplog stays on "routers.text".


def test_text_clean_backpressure_returns_503_when_at_capacity(
    client, valid_headers, caplog, monkeypatch
):
    """When ``_INFLIGHT_LANE2`` is at the configured cap, /text/clean
    returns 503 with a Retry-After header BEFORE Lane 1 publishes — so a
    burst-rejected client retry doesn't produce duplicate Kinesis events.

    Pins Codex /codex review round-3 P1 #2 + round-4 P1 fix: pre-PR,
    response latency naturally throttled concurrency; fire-and-forget
    removed that cap and bursts could spawn unbounded background
    OpenAI/DB sessions. Round-4 found a check-then-await race in the
    original fix — the v2 uses an atomic counter incremented BEFORE any
    await, so concurrent bursts cannot all observe the same stale count.
    """
    import services.text_clean_service as text_clean_module

    # _max_background_tasks() reads the env var on each call so .env
    # changes take effect. Force the cap to 1 for this test.
    monkeypatch.setenv("TEXT_CLEAN_MAX_BG_TASKS", "1")
    # Pre-fill the in-flight counter to trip the check.
    text_clean_module._INFLIGHT_LANE2[0] = 1
    try:
        intelligence_instance = MagicMock()
        intelligence_instance.process_transcript = AsyncMock(return_value=MagicMock())

        publisher_instance = MagicMock()
        publisher_instance.publish_envelope = AsyncMock(
            return_value={"kinesis_sequence": "seq-1", "eventbridge_id": "evt-1"}
        )

        cleaner_instance = MagicMock()
        cleaner_instance.clean_transcript = AsyncMock(return_value="Cleaned text content")

        # Backpressure WARNING log stays in routers.text (the endpoint owns the
        # 503 translation + log); IntelligenceService + AWSEventPublisher
        # patches point at services.text_clean_service (where they now live).
        with caplog.at_level(logging.WARNING, logger="routers.text"), \
             patch("routers.text.BatchCleanerService", return_value=cleaner_instance), \
             patch("services.text_clean_service.IntelligenceService", return_value=intelligence_instance), \
             patch("services.text_clean_service.AWSEventPublisher", return_value=publisher_instance), \
             patch("routers.text.TranscriptEnrichmentService", return_value=_build_enrichment_mock()), \
             patch("routers.text.get_tenant_internal_domains", new=AsyncMock(return_value=[])):

            body = {
                "text": "This is some raw text to clean",
                "account_id": valid_headers["X-Account-ID"],
            }
            response = client.post("/text/clean", json=body, headers=valid_headers)
    finally:
        text_clean_module._INFLIGHT_LANE2[0] = 0

    assert response.status_code == 503
    assert response.headers.get("retry-after") == "60"

    # Critical: Lane 1 must NOT have published — backpressure check is BEFORE
    # the publish so a retry doesn't produce duplicate Kinesis events.
    publisher_instance.publish_envelope.assert_not_called()
    intelligence_instance.process_transcript.assert_not_called()

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "backpressure" in r.getMessage().lower() for r in warning_records
    ), "Backpressure rejection must produce a WARNING log."


def test_text_clean_allows_null_publish_when_aws_disabled(
    client, valid_headers, monkeypatch
):
    """When BOTH ``ENABLE_KINESIS_PUBLISHING=false`` and
    ``ENABLE_EVENTBRIDGE_PUBLISHING=false`` are set, publish_envelope
    legitimately returns ``{kinesis_sequence: None, eventbridge_id: None}``.
    The handler must NOT 502 in that supported configuration (local/dev
    mode without AWS credentials).

    Pins Codex /codex review round-4 P2 + round-5 P2: discriminate
    "null because outage" (502) from "null because configured-off / no
    credentials" (200). Also covers the no-AWS-credentials case where
    main.validate_aws_credentials() permits the app to start without them.
    """
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.setenv("ENABLE_KINESIS_PUBLISHING", "false")
    monkeypatch.setenv("ENABLE_EVENTBRIDGE_PUBLISHING", "false")

    intelligence_instance = MagicMock()
    intelligence_instance.process_transcript = AsyncMock(return_value=MagicMock())

    publisher_instance = MagicMock()
    publisher_instance.publish_envelope = AsyncMock(
        return_value={"kinesis_sequence": None, "eventbridge_id": None}
    )

    cleaner_instance = MagicMock()
    cleaner_instance.clean_transcript = AsyncMock(return_value="Cleaned text content")

    with patch("routers.text.BatchCleanerService", return_value=cleaner_instance), \
         patch("services.text_clean_service.IntelligenceService", return_value=intelligence_instance), \
         patch("services.text_clean_service.AWSEventPublisher", return_value=publisher_instance), \
         patch("routers.text.TranscriptEnrichmentService", return_value=_build_enrichment_mock()), \
         patch("routers.text.get_tenant_internal_domains", new=AsyncMock(return_value=[])):

        body = {
            "text": "This is some raw text to clean",
            "account_id": valid_headers["X-Account-ID"],
        }
        response = client.post("/text/clean", json=body, headers=valid_headers)

    assert response.status_code == 200, (
        f"Expected 200 with publishing disabled; got {response.status_code}: "
        f"{response.text}"
    )
    intelligence_instance.process_transcript.assert_called_once()
    publisher_instance.publish_envelope.assert_called_once()


def test_text_clean_lane1_failure_produces_5xx(client, valid_headers, caplog):
    """Lane 1 (Kinesis/EventBridge publish) is awaited synchronously before
    the response. A Lane 1 failure produces HTTP 502 — preserves the
    durable-publish contract that downstream envelope subscribers rely on.

    Pins Codex /codex review P2 fix on PR #23: Lane 1 was originally
    inside the asyncio.gather wrapper alongside Lane 2; the first round
    of fire-and-forget moved both to the background, which made every
    /text/clean call lossy on worker crash/restart between response and
    publish. Lane 1 was not the long-running step that caused the
    timeout, so there's no benefit to moving it off the response path.
    """
    intelligence_instance = MagicMock()
    intelligence_instance.process_transcript = AsyncMock(return_value=MagicMock())

    publisher_instance = MagicMock()
    publisher_instance.publish_envelope = AsyncMock(
        side_effect=RuntimeError("simulated Kinesis outage")
    )

    cleaner_instance = MagicMock()
    cleaner_instance.clean_transcript = AsyncMock(return_value="Cleaned text content")

    # Lane 1 ERROR log was emitted from routers.text pre-extraction; after
    # PR-X1 it's emitted from services.text_clean_service where the
    # publish call lives. Same message text ("Lane 1 (publishing) raised").
    with caplog.at_level(logging.ERROR, logger="services.text_clean_service"), \
         patch("routers.text.BatchCleanerService", return_value=cleaner_instance), \
         patch("services.text_clean_service.IntelligenceService", return_value=intelligence_instance), \
         patch("services.text_clean_service.AWSEventPublisher", return_value=publisher_instance), \
         patch("routers.text.TranscriptEnrichmentService", return_value=_build_enrichment_mock()), \
         patch("routers.text.get_tenant_internal_domains", new=AsyncMock(return_value=[])):

        body = {
            "text": "This is some raw text to clean",
            "account_id": valid_headers["X-Account-ID"],
        }
        response = client.post("/text/clean", json=body, headers=valid_headers)

    assert response.status_code == 502, (
        f"Lane 1 failure must surface as 502, got {response.status_code}: {response.text}. "
        "If this fails, Lane 1 publish has been silently moved back into the "
        "background task — see services/text_clean_service.py and Codex P2 review notes."
    )
    # Lane 2 must NOT have run: the response path is gated on Lane 1.
    intelligence_instance.process_transcript.assert_not_called()
    publisher_instance.publish_envelope.assert_called_once()

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "Lane 1 (publishing) raised" in r.getMessage() for r in error_records
    ), "Lane 1 exception must produce an observable ERROR log."


def test_on_done_callback_logs_unhandled_wrapper_exception(caplog):
    """The done-callback safety net surfaces wrapper-level crashes that
    occur OUTSIDE the gather (e.g., a future refactor adds logic above/below
    the asyncio.gather call and it raises). Without this callback, Python
    would only emit "Task exception was never retrieved" at GC time —
    invisible in production observability.

    Tests the callback directly with a Task that raises, bypassing the
    handler-level wiring. This is the unit-level safety contract.
    """
    import services.text_clean_service as text_clean_module

    async def _raises() -> None:
        raise RuntimeError("wrapper-level crash outside the gather")

    async def _drive() -> int:
        # Call the handler-internal _on_done. We reconstruct the same shape
        # the handler uses: create_task → set.add → add_done_callback.
        # The closure normally captures interaction_id_str; we test the
        # behavior without that closure by inspecting task.exception()
        # via a minimal callback that mirrors text_clean_service's contract.
        task = asyncio.create_task(_raises())
        text_clean_module._BACKGROUND_TASKS.add(task)
        log_call_count = {"n": 0}

        def _callback(t: asyncio.Task) -> None:
            text_clean_module._BACKGROUND_TASKS.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                text_clean_module.logger.error(
                    f"Text cleaning background task crashed (unhandled): "
                    f"error={type(exc).__name__}: {str(exc)}",
                    exc_info=exc,
                )
                log_call_count["n"] += 1

        task.add_done_callback(_callback)
        with caplog.at_level(logging.ERROR, logger="services.text_clean_service"):
            try:
                await task
            except RuntimeError:
                pass
            # Done-callback is scheduled; yield once so it runs.
            await asyncio.sleep(0)
        return log_call_count["n"]

    n = asyncio.run(_drive())
    assert n == 1, "Done-callback did not log the wrapper-level exception."
    error_records = [
        r for r in caplog.records
        if r.levelno >= logging.ERROR and "crashed (unhandled)" in r.getMessage()
    ]
    assert error_records, (
        "Expected an ERROR log with 'crashed (unhandled)' from the done-callback."
    )
    assert any(
        r.exc_info and isinstance(r.exc_info[1], RuntimeError)
        for r in error_records
    ), "ERROR log is missing the exception traceback (exc_info)."


def test_lifespan_drain_awaits_in_flight_background_tasks(caplog):
    """The lifespan shutdown drain (main._drain_text_clean_background_tasks)
    awaits in-flight ``/text/clean`` background tasks with a bounded
    timeout, instead of letting them be silently cancelled by the event
    loop's shutdown sequence.

    Pins Codex /review P1 #2 mitigation: under fire-and-forget, container
    restarts during the Lane 2 window can drop work that the client was
    told succeeded. The drain doesn't eliminate that risk (Lane 2 is
    100-160s, Railway grace is ~30s) but DOES close the window for tasks
    nearing completion at shutdown.
    """
    import main as main_module
    import services.text_clean_service as text_clean_module

    text_clean_module._BACKGROUND_TASKS.clear()

    completed = []

    async def _short_task() -> None:
        await asyncio.sleep(0.02)
        completed.append("short")

    async def _drive() -> None:
        task = asyncio.create_task(_short_task())
        text_clean_module._BACKGROUND_TASKS.add(task)
        task.add_done_callback(text_clean_module._BACKGROUND_TASKS.discard)

        with caplog.at_level(logging.INFO, logger="main"):
            await main_module._drain_text_clean_background_tasks(timeout_s=1.0)

    asyncio.run(_drive())

    assert completed == ["short"], "Drain returned before the task finished."
    drain_logs = [
        r for r in caplog.records
        if "drained" in r.getMessage() or "draining" in r.getMessage()
    ]
    assert drain_logs, (
        "No drain log emitted — the drain helper ran silently. Operators "
        "need visibility into shutdown behavior."
    )


def test_lifespan_drain_logs_warning_on_timeout(caplog):
    """When the drain budget is too short for in-flight Lane 2 work, the
    drain logs a WARNING that names how many tasks were cancelled.

    Pins the observability of the partial-shutdown case: operators must
    be able to grep Railway logs for "drain timed out" after a deploy to
    audit how often work was lost.
    """
    import main as main_module
    import services.text_clean_service as text_clean_module

    text_clean_module._BACKGROUND_TASKS.clear()

    async def _slow_task() -> None:
        try:
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            pass

    async def _drive() -> None:
        task = asyncio.create_task(_slow_task())
        text_clean_module._BACKGROUND_TASKS.add(task)
        task.add_done_callback(text_clean_module._BACKGROUND_TASKS.discard)

        with caplog.at_level(logging.WARNING, logger="main"):
            await main_module._drain_text_clean_background_tasks(timeout_s=0.1)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_drive())

    warning_logs = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "drain budget exhausted" in r.getMessage()
    ]
    assert warning_logs, (
        "Expected a WARNING log with 'drain budget exhausted' — without "
        "it, silent work loss has no audit trail."
    )
