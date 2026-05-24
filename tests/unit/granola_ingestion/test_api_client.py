"""Unit tests for :mod:`services.granola_ingestion.api_client`.

Per :doc:`feedback_test_pattern_no_docker`: no Docker, no real network,
no real Granola account. The :class:`httpx.MockTransport` plugin lets us
script HTTP-level responses while the client exercises its real
serialization, retry, and Pydantic-validation paths.

All retry tests pass a tiny ``retry_base_delay_s`` so the exponential
backoff fires in milliseconds instead of seconds — verified by checking
the durations passed to ``asyncio.sleep`` via monkeypatch.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from services.granola_ingestion.api_client import (
    GranolaAPIClient,
    _build_jittered_delay,
    _format_created_after,
    _parse_retry_after,
)
from services.granola_ingestion.errors import GranolaError, GranolaErrorCode
from services.granola_ingestion.models import (
    GranolaFolder,
    GranolaNoteDetail,
    GranolaNoteSummary,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


_TEST_API_KEY = "grn_test_key_DO_NOT_LOG"
_TEST_BASE_URL = "https://public-api.granola.ai/v1"


def _client_with_handler(
    handler,
    *,
    max_retries: int = 4,
    retry_base_delay_s: float = 0.001,
    max_consecutive_429s: int = 3,
    max_pages: int = 500,
) -> GranolaAPIClient:
    """Build a client whose underlying httpx talks to ``handler``.

    The retry base delay is forced to 1ms so retry-exhaustion tests
    finish in milliseconds; durations are still observable via the
    ``sleep_calls`` monkeypatch fixture below.
    """
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30.0))
    return GranolaAPIClient(
        _TEST_API_KEY,
        base_url=_TEST_BASE_URL,
        http_client=http_client,
        max_retries=max_retries,
        retry_base_delay_s=retry_base_delay_s,
        max_consecutive_429s=max_consecutive_429s,
        max_pages=max_pages,
    )


@pytest.fixture
def sleep_calls(monkeypatch):
    """Capture ``asyncio.sleep`` durations without actually sleeping.

    Returns a list that records each call's argument so retry tests
    can assert backoff sequencing (e.g., that 429 honors Retry-After
    and that 5xx uses exponential backoff).
    """
    calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return calls


# ---------------------------------------------------------------------------
# Construction + lifecycle
# ---------------------------------------------------------------------------


def test_construct_with_empty_api_key_raises():
    with pytest.raises(ValueError, match="non-empty api_key"):
        GranolaAPIClient("")


def test_repr_does_not_leak_api_key():
    """LOCKED-23: api_key is per-USER secret material; never leaks via repr.

    A naive default-dataclass repr would expose ``self._api_key`` — guarding
    against that with an explicit ``__repr__`` is the discipline this test
    locks in.
    """
    client = GranolaAPIClient(_TEST_API_KEY)
    rendered = repr(client)
    assert _TEST_API_KEY not in rendered
    assert "grn_" not in rendered
    assert _TEST_BASE_URL in rendered


@pytest.mark.asyncio
async def test_context_manager_closes_owned_client():
    """Owned client is closed on __aexit__; injected client is left alone."""
    async with GranolaAPIClient(_TEST_API_KEY) as client:
        owned = client._client
    assert owned.is_closed

    injected = httpx.AsyncClient()
    try:
        async with GranolaAPIClient(_TEST_API_KEY, http_client=injected) as client:
            pass
        assert not injected.is_closed
    finally:
        await injected.aclose()


# ---------------------------------------------------------------------------
# Helper utilities (lightweight unit tests; cheap correctness anchors)
# ---------------------------------------------------------------------------


def test_format_created_after_naive_assumed_utc():
    naive = datetime(2026, 5, 24, 7, 0, 0)
    assert _format_created_after(naive) == "2026-05-24T07:00:00Z"


def test_format_created_after_aware_converted_to_utc():
    """An aware datetime in another tz is converted to UTC before formatting."""
    from datetime import timedelta

    aware = datetime(2026, 5, 24, 9, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    # 09:00 +02:00 == 07:00 UTC
    assert _format_created_after(aware) == "2026-05-24T07:00:00Z"


def test_parse_retry_after_numeric():
    assert _parse_retry_after("3", fallback_s=99.0) == 3.0


def test_parse_retry_after_missing_uses_fallback():
    assert _parse_retry_after(None, fallback_s=2.5) == 2.5


def test_parse_retry_after_non_numeric_uses_fallback():
    assert _parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", fallback_s=4.0) == 4.0


def test_parse_retry_after_capped():
    # 600s exceeds the 60s cap; expect cap value back.
    assert _parse_retry_after("600", fallback_s=1.0) == 60.0


def test_build_jittered_delay_within_expected_range():
    """1s base, attempt 2 → expected interval is [4.0, 6.0)."""
    for _ in range(50):
        d = _build_jittered_delay(1.0, 2)
        assert 4.0 <= d < 6.0


# ---------------------------------------------------------------------------
# list_folders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_folders_happy_path():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "folders": [
                    {"id": "fol_a", "name": "EQ", "parent_folder_id": None},
                    {"id": "fol_b", "name": "Archive", "parent_folder_id": "fol_a"},
                ],
                "hasMore": False,
                "cursor": "",
            },
        )

    client = _client_with_handler(handler)
    try:
        folders = await client.list_folders()
    finally:
        await client.aclose()

    assert captured["method"] == "GET"
    assert captured["url"].rstrip("?") == f"{_TEST_BASE_URL}/folders"
    assert captured["authorization"] == f"Bearer {_TEST_API_KEY}"
    assert len(folders) == 2
    assert isinstance(folders[0], GranolaFolder)
    assert folders[0].id == "fol_a"
    assert folders[1].parent_folder_id == "fol_a"


@pytest.mark.asyncio
async def test_list_folders_accepts_bare_list_response():
    """Defensive: if Granola ever drops the {folders, hasMore, cursor} wrapper,
    we still parse a bare list rather than failing."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "fol_a", "name": "EQ"}])

    client = _client_with_handler(handler)
    try:
        folders = await client.list_folders()
    finally:
        await client.aclose()

    assert len(folders) == 1
    assert folders[0].id == "fol_a"


# ---------------------------------------------------------------------------
# list_notes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_notes_happy_path_with_created_after():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "notes": [
                    {
                        "id": "not_1",
                        "title": "Sync with Acme",
                        "created_at": "2026-05-24T07:05:00Z",
                        "updated_at": "2026-05-24T07:10:00Z",
                        "folder_membership": [{"id": "fol_a", "name": "EQ"}],
                    }
                ],
                "hasMore": False,
                "cursor": "",
            },
        )

    client = _client_with_handler(handler)
    try:
        notes = await client.list_notes(
            folder_id="fol_a",
            created_after=datetime(2026, 5, 24, 0, 0, 0, tzinfo=timezone.utc),
            limit=50,
        )
    finally:
        await client.aclose()

    assert captured["params"]["folder_id"] == "fol_a"
    assert captured["params"]["created_after"] == "2026-05-24T00:00:00Z"
    assert captured["params"]["limit"] == "50"
    assert len(notes) == 1
    assert isinstance(notes[0], GranolaNoteSummary)
    assert notes[0].id == "not_1"


@pytest.mark.asyncio
async def test_list_notes_omits_created_after_when_none():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"notes": [], "hasMore": False, "cursor": ""})

    client = _client_with_handler(handler)
    try:
        await client.list_notes(folder_id="fol_a")
    finally:
        await client.aclose()

    # Filter parameter MUST be absent — sending an empty value would be
    # mis-interpreted by Granola.
    assert "created_after" not in captured["params"]
    assert captured["params"]["folder_id"] == "fol_a"
    assert captured["params"]["limit"] == "100"  # default


# ---------------------------------------------------------------------------
# get_note_detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_note_detail_happy_path():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "id": "not_1",
                "title": "Sync with Acme",
                "created_at": "2026-05-24T07:05:00Z",
                "updated_at": "2026-05-24T07:10:00Z",
                "attendees": [
                    {"name": "Peter ONeil", "email": "peter@eq.example"},
                    {"name": "Acme Rep", "email": "rep@acme.example"},
                ],
                "calendar_event": {"id": "evt_xyz"},
                "transcript": [
                    {
                        "text": "Hello.",
                        "start_time": 0.0,
                        "end_time": 1.2,
                        "speaker": {"source": "microphone"},
                    }
                ],
                "summary_markdown": "## Notes\n\n- Discussed pricing",
                "summary_text": "Discussed pricing",
                "web_url": "https://granola.ai/notes/not_1",
                "folder_membership": [{"id": "fol_a", "name": "EQ"}],
            },
        )

    client = _client_with_handler(handler)
    try:
        detail = await client.get_note_detail("not_1")
    finally:
        await client.aclose()

    assert f"{_TEST_BASE_URL}/notes/not_1" in captured["url"]
    assert captured["params"]["include"] == "transcript"
    assert isinstance(detail, GranolaNoteDetail)
    assert detail.id == "not_1"
    assert len(detail.attendees) == 2
    assert detail.attendees[1].email == "rep@acme.example"
    assert detail.calendar_event is not None
    assert detail.calendar_event.id == "evt_xyz"
    assert detail.transcript[0].speaker == {"source": "microphone"}
    assert detail.web_url == "https://granola.ai/notes/not_1"


# ---------------------------------------------------------------------------
# Retry + error mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_5xx_retried_then_success(sleep_calls):
    """503 twice, then 200; the client returns the successful payload and
    asleep was called exactly twice (one per retry)."""

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return httpx.Response(503, json={"error": "transient"})
        return httpx.Response(200, json={"folders": [{"id": "fol_a", "name": "EQ"}]})

    client = _client_with_handler(handler)
    try:
        folders = await client.list_folders()
    finally:
        await client.aclose()

    assert call_count["n"] == 3
    assert len(folders) == 1
    # Two retry-sleeps (between attempts 0+1 and attempts 1+2). The exact
    # durations include jitter; assert range bounds.
    assert len(sleep_calls) == 2
    assert 0.001 <= sleep_calls[0] < 0.0015  # 1ms base, attempt 0 → [1ms, 1.5ms)
    assert 0.002 <= sleep_calls[1] < 0.003  # attempt 1 → [2ms, 3ms)


@pytest.mark.asyncio
async def test_5xx_exhausted_raises_granola_5xx(sleep_calls):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(502, json={"error": "down"})

    client = _client_with_handler(handler, max_retries=4)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_folders()
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_5XX
    assert exc_info.value.http_status == 502
    # max_retries=4 + 1 initial attempt = 5 calls; 4 sleeps between them.
    assert call_count["n"] == 5
    assert len(sleep_calls) == 4


@pytest.mark.asyncio
async def test_401_raises_auth_failed_no_retry(sleep_calls):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(401, json={"error": "Unauthorized"})

    client = _client_with_handler(handler)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_folders()
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_AUTH_FAILED
    assert exc_info.value.http_status == 401
    assert call_count["n"] == 1  # NOT retried
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_403_also_raises_auth_failed(sleep_calls):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Forbidden"})

    client = _client_with_handler(handler)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_folders()
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_AUTH_FAILED
    assert exc_info.value.http_status == 403
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_404_on_list_folders_raises_folder_not_found(sleep_calls):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Not Found"})

    client = _client_with_handler(handler)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_folders()
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_FOLDER_NOT_FOUND
    assert "folder" in exc_info.value.message.lower()
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_404_on_get_note_detail_raises_note_not_found(sleep_calls):
    """Codex R1 P1: a deleted note must NOT raise GRANOLA_FOLDER_NOT_FOUND,
    or Phase 2d will treat a single-note race as credential breakage and
    take the whole connection offline."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Not Found"})

    client = _client_with_handler(handler)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.get_note_detail("not_missing")
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_NOTE_NOT_FOUND
    assert exc_info.value.http_status == 404
    assert "note" in exc_info.value.message.lower()
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_400_other_4xx_raises_granola_http_error(sleep_calls):
    """400 VALIDATION_ERROR (the empirically-observed shape for a bogus
    folder_id) becomes :attr:`GRANOLA_HTTP_ERROR`, not retried."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": "VALIDATION_ERROR", "message": "Invalid folder ID format"},
        )

    client = _client_with_handler(handler)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_notes(folder_id="bogus")
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_HTTP_ERROR
    assert exc_info.value.http_status == 400
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_429_with_retry_after_honored_and_does_not_consume_retry_budget(
    sleep_calls,
):
    """429 → sleep Retry-After; THEN 200. No retry-budget consumed (so
    subsequent 5xx can still retry to exhaustion)."""

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "7"},
                json={"error": "rate limited"},
            )
        return httpx.Response(200, json={"folders": []})

    client = _client_with_handler(handler, max_retries=4)
    try:
        folders = await client.list_folders()
    finally:
        await client.aclose()

    assert folders == []
    assert call_count["n"] == 2
    # Exactly one sleep, with the honored Retry-After value (in seconds).
    assert sleep_calls == [7.0]


@pytest.mark.asyncio
async def test_429_without_retry_after_uses_jittered_fallback(sleep_calls):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"folders": []})

    client = _client_with_handler(handler)
    try:
        await client.list_folders()
    finally:
        await client.aclose()

    # One sleep, drawn from the jitter range for attempt 0 (1ms base):
    # [0.001, 0.0015).
    assert len(sleep_calls) == 1
    assert 0.001 <= sleep_calls[0] < 0.0015


@pytest.mark.asyncio
async def test_timeout_exception_retried_then_raises_granola_timeout(sleep_calls):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        raise httpx.TimeoutException("simulated read timeout")

    client = _client_with_handler(handler, max_retries=2)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_folders()
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_TIMEOUT
    assert call_count["n"] == 3  # 1 initial + 2 retries
    assert len(sleep_calls) == 2


@pytest.mark.asyncio
async def test_connect_error_retried_then_raises_granola_5xx(sleep_calls):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        raise httpx.ConnectError("simulated TCP failure")

    client = _client_with_handler(handler, max_retries=2)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_folders()
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_5XX
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_malformed_response_raises_parse_error(sleep_calls):
    """A 200 OK body that doesn't match the Pydantic shape → PARSE_ERROR,
    not retried (retrying the same Granola endpoint will keep returning
    the same malformed body)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "folders": [
                    # Missing required ``name`` field.
                    {"id": "fol_a"},
                ]
            },
        )

    client = _client_with_handler(handler)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_folders()
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_PARSE_ERROR
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_non_json_2xx_body_raises_parse_error(sleep_calls):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html>not json</html>",
            headers={"content-type": "text/html"},
        )

    client = _client_with_handler(handler)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_folders()
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_PARSE_ERROR
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_get_note_detail_validation_error_includes_cause(sleep_calls):
    """Pydantic ValidationError survives as ``__cause__`` so debugging the
    actual shape mismatch is possible without parsing the message."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "not_x",
                # Missing required `created_at` — Pydantic will reject.
                "title": "Bad note",
            },
        )

    client = _client_with_handler(handler)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.get_note_detail("not_x")
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_PARSE_ERROR
    assert exc_info.value.__cause__ is not None
    # Pydantic ValidationError carries error_count() ≥ 1
    assert exc_info.value.__cause__.__class__.__name__ == "ValidationError"


# ---------------------------------------------------------------------------
# Codex R1 fixes: consecutive-429 budget + cursor pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_consecutive_budget_exhausted_raises_granola_rate_limited(
    sleep_calls,
):
    """Codex R1 P1: sustained 429s must surface as a structured failure
    instead of looping forever. After ``max_consecutive_429s + 1`` 429
    responses in a row, the client raises GRANOLA_RATE_LIMITED."""

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "1"})

    client = _client_with_handler(handler, max_consecutive_429s=2)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_folders()
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_RATE_LIMITED
    assert exc_info.value.http_status == 429
    # 1st 429 counts to 1 (sleep), 2nd to 2 (sleep), 3rd hits >2 → raise.
    # So we made 3 HTTP calls and slept twice between them.
    assert call_count["n"] == 3
    assert len(sleep_calls) == 2


@pytest.mark.asyncio
async def test_429_followed_by_non_429_resets_consecutive_counter(sleep_calls):
    """A 5xx or 200 between 429s clears the consecutive counter — a brief
    rate-limit window followed by recovery + a fresh window shouldn't trip
    the cap any faster than a clean 429 sequence would."""

    sequence = iter(
        [
            429,
            429,
            503,  # non-429 — resets counter
            429,  # back to 1
            429,  # 2
            200,  # success path
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(sequence)
        if status == 429:
            return httpx.Response(429, headers={"Retry-After": "1"})
        if status == 503:
            return httpx.Response(503)
        return httpx.Response(200, json={"folders": []})

    # max_consecutive_429s=2 would normally fire after 3 in a row; the
    # 503 mid-stream resets so we never accumulate 3 in a row even though
    # we see 4 total 429s.
    client = _client_with_handler(handler, max_consecutive_429s=2, max_retries=4)
    try:
        folders = await client.list_folders()
    finally:
        await client.aclose()

    assert folders == []


@pytest.mark.asyncio
async def test_list_notes_paginates_with_cursor(sleep_calls):
    """Codex R1 P2: hasMore=true responses must be paged through via
    ?cursor=<value> instead of silently truncating at page 1."""

    page_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page_count["n"] += 1
        cursor_param = request.url.params.get("cursor")
        if page_count["n"] == 1:
            assert cursor_param is None  # first page, no cursor
            return httpx.Response(
                200,
                json={
                    "notes": [
                        {"id": "not_1", "title": "A", "created_at": "2026-05-24T00:00:00Z"},
                        {"id": "not_2", "title": "B", "created_at": "2026-05-24T00:01:00Z"},
                    ],
                    "hasMore": True,
                    "cursor": "cur_page2",
                },
            )
        if page_count["n"] == 2:
            assert cursor_param == "cur_page2"
            return httpx.Response(
                200,
                json={
                    "notes": [
                        {"id": "not_3", "title": "C", "created_at": "2026-05-24T00:02:00Z"},
                    ],
                    "hasMore": True,
                    "cursor": "cur_page3",
                },
            )
        assert cursor_param == "cur_page3"
        return httpx.Response(
            200,
            json={
                "notes": [
                    {"id": "not_4", "title": "D", "created_at": "2026-05-24T00:03:00Z"},
                ],
                "hasMore": False,
                "cursor": None,
            },
        )

    client = _client_with_handler(handler)
    try:
        notes = await client.list_notes(folder_id="fol_a")
    finally:
        await client.aclose()

    assert page_count["n"] == 3
    assert [n.id for n in notes] == ["not_1", "not_2", "not_3", "not_4"]


@pytest.mark.asyncio
async def test_list_notes_pagination_missing_cursor_raises_parse_error(sleep_calls):
    """hasMore=true with no cursor is a malformed pagination response —
    fail loud rather than truncate to first page (data-loss prevention)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "notes": [{"id": "not_1", "title": "A", "created_at": "2026-05-24T00:00:00Z"}],
                "hasMore": True,
                # cursor intentionally omitted
            },
        )

    client = _client_with_handler(handler)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_notes(folder_id="fol_a")
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_PARSE_ERROR
    assert "cursor" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_list_notes_pagination_non_advancing_cursor_raises_parse_error(
    sleep_calls,
):
    """A cursor that doesn't advance between pages would spin forever —
    fail loud after the second page returns the same cursor as the first."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "notes": [{"id": "not_x", "title": "X", "created_at": "2026-05-24T00:00:00Z"}],
                "hasMore": True,
                "cursor": "stuck",
            },
        )

    client = _client_with_handler(handler)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_notes(folder_id="fol_a")
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_PARSE_ERROR
    assert "advance" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_list_notes_pagination_max_pages_exceeded_raises_parse_error(
    sleep_calls,
):
    """A misbehaving cursor that advances but never sets hasMore=false
    would exhaust memory; cap at max_pages and surface as parse error."""

    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            200,
            json={
                "notes": [
                    {
                        "id": f"not_{counter['n']}",
                        "title": "x",
                        "created_at": "2026-05-24T00:00:00Z",
                    }
                ],
                "hasMore": True,
                "cursor": f"cur_{counter['n']}",
            },
        )

    client = _client_with_handler(handler, max_consecutive_429s=99)  # unrelated
    # Override max_pages via direct attr for compactness; in real use
    # this would be the constructor argument.
    client._max_pages = 3
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_notes(folder_id="fol_a")
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_PARSE_ERROR
    assert "exceeded" in exc_info.value.message.lower()
    assert counter["n"] == 3


# ---------------------------------------------------------------------------
# Codex R2 fixes: /folders pagination + 429 fallback grows with consecutive_429s
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_folders_paginates_with_cursor(sleep_calls):
    """Codex R2 P2: /folders uses the same {folders, hasMore, cursor}
    wrapper as /notes; an account with enough folders to set hasMore=true
    must NOT have later pages silently dropped."""

    page_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        page_count["n"] += 1
        cursor_param = request.url.params.get("cursor")
        if page_count["n"] == 1:
            assert cursor_param is None
            return httpx.Response(
                200,
                json={
                    "folders": [
                        {"id": "fol_1", "name": "EQ"},
                        {"id": "fol_2", "name": "Archive"},
                    ],
                    "hasMore": True,
                    "cursor": "cur_p2",
                },
            )
        assert cursor_param == "cur_p2"
        return httpx.Response(
            200,
            json={
                "folders": [{"id": "fol_3", "name": "Misc"}],
                "hasMore": False,
                "cursor": None,
            },
        )

    client = _client_with_handler(handler)
    try:
        folders = await client.list_folders()
    finally:
        await client.aclose()

    assert page_count["n"] == 2
    assert [f.id for f in folders] == ["fol_1", "fol_2", "fol_3"]


@pytest.mark.asyncio
async def test_429_fallback_backoff_grows_with_consecutive_429s(sleep_calls):
    """Codex R2 P2: when Retry-After is missing, the fallback delay must
    grow across consecutive 429s. Otherwise every retry sleeps the same
    base interval and the consecutive-429 budget fires far earlier than
    the intended 1s → 2s → 4s ramp."""

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] <= 3:
            # 429 with NO Retry-After header — forces the fallback path.
            return httpx.Response(429, json={"error": "throttled"})
        return httpx.Response(200, json={"folders": []})

    # max_consecutive_429s=5 (room to observe the ramp) and base 1ms
    # delay so the jittered range stays observable.
    client = _client_with_handler(handler, max_consecutive_429s=5)
    try:
        await client.list_folders()
    finally:
        await client.aclose()

    assert call_count["n"] == 4  # 3 × 429 then 1 × 200
    assert len(sleep_calls) == 3
    # Attempt 0 → [1ms, 1.5ms); attempt 1 → [2ms, 3ms); attempt 2 → [4ms, 6ms).
    assert 0.001 <= sleep_calls[0] < 0.0015
    assert 0.002 <= sleep_calls[1] < 0.003
    assert 0.004 <= sleep_calls[2] < 0.006


# ---------------------------------------------------------------------------
# Codex R3 fixes: transcript required + raise max_pages ceiling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_note_detail_missing_transcript_raises_parse_error(sleep_calls):
    """Codex R3 P2: the client always requests ?include=transcript, so a
    response missing the transcript field is a real shape mismatch — must
    NOT default to []. Phase 2d would otherwise silently ingest a blank
    meeting where the API actually misbehaved."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "not_x",
                "title": "Sync",
                "created_at": "2026-05-24T07:00:00Z",
                # transcript intentionally absent
                "summary_text": "Quick chat",
            },
        )

    client = _client_with_handler(handler)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.get_note_detail("not_x")
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_PARSE_ERROR


@pytest.mark.asyncio
async def test_get_note_detail_empty_transcript_list_is_accepted(sleep_calls):
    """A legitimately empty transcript list (zero-audio capture) must still
    validate — the required-field check only triggers on the field being
    absent, not on an empty list."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "not_empty",
                "title": "Joined, no audio",
                "created_at": "2026-05-24T07:00:00Z",
                "transcript": [],
            },
        )

    client = _client_with_handler(handler)
    try:
        detail = await client.get_note_detail("not_empty")
    finally:
        await client.aclose()

    assert detail.id == "not_empty"
    assert detail.transcript == []


@pytest.mark.asyncio
async def test_list_notes_pagination_accommodates_large_backfill(sleep_calls):
    """Codex R3 P2: the previous max_pages=20 ceiling would deterministically
    fail real first-poll backfills against active accounts. With the bumped
    default of 500, a 50-page run completes cleanly. The non-advancing-cursor
    guard remains the load-bearing runaway protection (verified by a
    separate test)."""

    counter = {"n": 0}
    TOTAL_PAGES = 50

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        last_page = counter["n"] >= TOTAL_PAGES
        return httpx.Response(
            200,
            json={
                "notes": [
                    {
                        "id": f"not_{counter['n']}",
                        "title": "x",
                        "created_at": "2026-05-24T00:00:00Z",
                    }
                ],
                "hasMore": not last_page,
                "cursor": None if last_page else f"cur_{counter['n']}",
            },
        )

    client = _client_with_handler(handler)
    try:
        notes = await client.list_notes(folder_id="fol_big")
    finally:
        await client.aclose()

    assert counter["n"] == TOTAL_PAGES
    assert len(notes) == TOTAL_PAGES


# ---------------------------------------------------------------------------
# Codex R4 fix: per-endpoint pagination ceilings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_folders_pagination_ceiling_is_tight(sleep_calls):
    """Codex R4 P2: /folders uses a hardcoded tight ceiling (20 pages)
    independent of the constructor max_pages. Folders are organizational
    primitives — a stuck cursor here should fail in <20 requests, not
    after 500. Verified by constructing with a large max_pages (which
    would mask the tight folder ceiling if it leaked through to the
    shared helper)."""

    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            200,
            json={
                "folders": [{"id": f"fol_{counter['n']}", "name": "x"}],
                "hasMore": True,
                "cursor": f"cur_{counter['n']}",
            },
        )

    # Constructor max_pages=500 (the default for /notes). If the tight
    # /folders ceiling leaks, this test would make 500 HTTP requests.
    client = _client_with_handler(handler, max_pages=500)
    try:
        with pytest.raises(GranolaError) as exc_info:
            await client.list_folders()
    finally:
        await client.aclose()

    assert exc_info.value.code is GranolaErrorCode.GRANOLA_PARSE_ERROR
    assert "exceeded" in exc_info.value.message.lower()
    # Tight ceiling = 20; should fire well before 500.
    assert counter["n"] == 20
