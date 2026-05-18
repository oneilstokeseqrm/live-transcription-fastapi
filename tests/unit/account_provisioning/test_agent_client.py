"""Unit tests for services.agent_action_core_client.AgentActionCoreClient.

Asserts:
- New contract body shape ({url, effort}) — plan §3.2
- Bearer JWT auth header — plan §3.2
- stream=false query param — plan §3.2
- Narrow exception classification (transient vs terminal) — plan §7.4
- AccountProfile parsing — plan §6.4

Uses httpx's MockTransport for HTTP-level injection without patching at
the client method level (which would bypass the real serialization path).
"""

from __future__ import annotations

import httpx
import pytest

from services.account_provisioning.types import (
    AccountProfile,
    AgentEnrichTerminalError,
    AgentEnrichTransientError,
)
from services.agent_action_core_client import AgentActionCoreClient


# Tests inline the (AgentActionCoreClient + httpx.MockTransport) setup because
# pytest-asyncio's event loop scoping makes a sync helper that aclose's the
# auto-built client awkward. The duplication is intentional and small.


@pytest.mark.asyncio
async def test_enrich_posts_url_and_effort_to_api_enrich():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "run_id": "abc-123",
                "status": "completed",
                "result": {
                    "company_name": "Acme Inc",
                    "website_domain": "acme.com",
                },
                "metadata": {"duration_ms": 1000},
            },
        )

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        profile = await client.enrich(url="acme.com", effort="high", jwt="tok-abc")
    finally:
        await client.aclose()

    # Body matches the live agent contract (no tenant_id, no worker_attempt_id).
    import json
    body = json.loads(captured["body"])
    assert body == {"url": "acme.com", "effort": "high"}

    # Bearer JWT in auth header.
    assert captured["headers"]["authorization"] == "Bearer tok-abc"

    # stream=false is on the query string.
    assert "stream=false" in captured["url"]

    # Method + path.
    assert captured["method"] == "POST"
    assert captured["url"].startswith("http://test.example.com/api/enrich")

    # Response parsed to AccountProfile.
    assert isinstance(profile, AccountProfile)
    assert profile.name == "Acme Inc"


@pytest.mark.asyncio
async def test_get_run_returns_account_profile():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "/api/enrich/run-xyz" in str(request.url)
        return httpx.Response(
            200,
            json={
                "run_id": "run-xyz",
                "status": "completed",
                "result": {"company_name": "Acme Inc"},
                "metadata": {"duration_ms": 1000},
            },
        )

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        profile = await client.get_run(run_id="run-xyz", jwt="tok-abc")
    finally:
        await client.aclose()

    assert profile.name == "Acme Inc"


@pytest.mark.asyncio
async def test_5xx_raises_transient():
    """5xx → AgentEnrichTransientError → DBOS retries the step."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        with pytest.raises(AgentEnrichTransientError):
            await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_429_raises_transient():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        with pytest.raises(AgentEnrichTransientError):
            await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_4xx_raises_terminal():
    """4xx (other than 429) → AgentEnrichTerminalError → workflow fails loud."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid url")

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        with pytest.raises(AgentEnrichTerminalError):
            await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_invalid_json_raises_terminal():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        with pytest.raises(AgentEnrichTerminalError):
            await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_non_object_body_raises_terminal():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["a", "b"])

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        with pytest.raises(AgentEnrichTerminalError):
            await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_missing_required_field_raises_terminal():
    """Pydantic validation fail → terminal. AccountProfile.name (aliased
    from ``company_name``) is required inside the v2 envelope.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        # `result` envelope present but missing company_name (the v2 name
        # for our required `name` field).
        return httpx.Response(
            200,
            json={
                "run_id": "abc-123",
                "status": "completed",
                "result": {"website_domain": "acme.com"},
            },
        )

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        with pytest.raises(AgentEnrichTerminalError):
            await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_account_profile_tolerates_extra_fields():
    """``extra='allow'`` on AccountProfile keeps us forward-compat with agent additions.

    The v2 envelope's top-level keys (``run_id``, ``status``, ``metadata``,
    ``account_id``) live OUTSIDE the AccountProfile (the parser strips
    the envelope before validation). Unknown fields INSIDE the result
    envelope are tolerated via ``extra='allow'``.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "run_id": "abc-123",
            "status": "completed",
            "result": {
                "company_name": "Acme Inc",
                "industry": "Manufacturing",
                "unknown_future_field": {"some": "stuff"},
                "founded_year": 1999,
            },
        })

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        profile = await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()

    assert profile.name == "Acme Inc"
    assert profile.industry == "Manufacturing"
    # Extra fields INSIDE the envelope are preserved via model_dump (forward-compat).
    dumped = profile.model_dump()
    assert dumped.get("unknown_future_field") == {"some": "stuff"}
    assert dumped.get("founded_year") == 1999


@pytest.mark.asyncio
async def test_enrich_handles_v2_envelope_shape():
    """M5.3 (2026-05-19): the agent's v2 schema (in production since
    2026-03-04) wraps the enrichment payload under ``.result``. Parser
    must unwrap the envelope and apply field aliases (``company_name``
    → ``name``, ``website_domain`` → ``domain``/``website``, etc.).
    Regression guard for Bug #4 surfaced at M5.2 production E2E.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "run_id": "abc-123",
            "status": "completed",
            "result": {
                "tenant_id": "11111111-1111-4111-8111-111111111111",
                "input_url": "https://acme.com",
                "company_name": "Acme Inc",
                "website_domain": "acme.com",
                "industry": "Software",
                "headquarters": "San Francisco",
                "employee_count_range": "50-200",
                "company_type": "Private",
                "one_line_description": "Industrial automation, reimagined.",
                "schema_version": "2.0.0",
            },
            "metadata": {"duration_ms": 5000, "sources_count": 8},
            "account_id": "00000000-0000-0000-0000-000000000acc",
        })

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        profile = await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()

    # Required field — aliased from company_name.
    assert profile.name == "Acme Inc"
    # Aliased fields all populated.
    assert profile.domain == "acme.com"
    assert profile.website == "acme.com"
    assert profile.industry == "Software"
    assert profile.region == "San Francisco"
    assert profile.company_size == "50-200"
    assert profile.company_type == "Private"
    assert profile.description == "Industrial automation, reimagined."


@pytest.mark.asyncio
async def test_envelope_run_id_preserved_for_crash_recovery_replay():
    """M5.3 R1 fold (Codex P2): ``run_id`` is injected from envelope into
    the result before AccountProfile validation, so
    ``call_agent_enrich`` keeps caching it via ``DBOS.set_event_async``
    for the crash-recovery replay path (``GET /api/enrich/{run_id}``).
    Without this, every retry would re-issue POST and pay 30-90s of
    redundant enrich. Plan §6.4 + §15 item 3.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "run_id": "abc-123",
            "status": "completed",
            "result": {"company_name": "Acme Inc"},
            "metadata": {"duration_ms": 1000},
            "account_id": "acc-xyz",
        })

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        profile = await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()

    # The replay-cache contract: profile.model_dump() carries run_id.
    dumped = profile.model_dump()
    assert dumped.get("run_id") == "abc-123", (
        "envelope run_id must be preserved into the profile so "
        "call_agent_enrich → DBOS.set_event_async caches it for replay"
    )


@pytest.mark.asyncio
async def test_envelope_run_id_not_overwritten_if_result_carries_one():
    """Defensive: if the agent ever embeds a ``run_id`` inside ``result``
    (future contract change), the parser must not overwrite it with the
    envelope-level value. The ``result.run_id`` wins because it's more
    specific to the payload.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "run_id": "envelope-run",
            "status": "completed",
            "result": {
                "company_name": "Acme Inc",
                "run_id": "inner-run",
            },
        })

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        profile = await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()

    assert profile.model_dump().get("run_id") == "inner-run"


@pytest.mark.asyncio
async def test_enrich_rejects_v2_response_missing_result_envelope():
    """If the agent ever drops the ``result`` envelope (returns flat or
    a status-only response), the parser must fail loud rather than
    silently accept a response that can't be validated.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "run_id": "abc-123",
            "status": "completed",
            "metadata": {"duration_ms": 1000},
        })

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        with pytest.raises(AgentEnrichTerminalError, match="missing 'result' envelope"):
            await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_enrich_rejects_v2_response_missing_company_name():
    """Bug #4 (M5.2): agent's ``result`` envelope present but missing
    ``company_name`` (the v2 source for our required ``name`` field).
    Parser must fail loud with the AccountProfile-contract message.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "run_id": "abc-123",
            "status": "completed",
            "result": {
                "website_domain": "acme.com",
                "industry": "Software",
            },
            "metadata": {"duration_ms": 1000},
        })

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        with pytest.raises(AgentEnrichTerminalError, match="did not match AccountProfile"):
            await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()


def test_default_timeout_accommodates_observed_worst_case_agent_latency():
    """Lock-in that the default HTTP timeout exceeds observed agent latency.

    M5 E2E (2026-05-18) caught the workflow hanging at function 3
    (`call_agent_enrich`) because the agent took ~145s on a sparse-web
    synthetic domain (`cold-prospect-{uuid}.com`) while httpx defaulted to
    120s. The agent completed and side-effected an `accounts` row, but
    the workflow never received the response and retried in a loop.

    The 240s floor here gives 95s of headroom over the observed 145s
    worst case so future agent slowdowns surface as a test failure
    instead of a silent retry-loop. If the agent's expected latency
    budget changes, this floor should change with it — DO NOT lower this
    floor without first widening it on the agent side.
    """
    from services.agent_action_core_client import _DEFAULT_TIMEOUT_SECONDS

    assert _DEFAULT_TIMEOUT_SECONDS >= 240.0, (
        f"_DEFAULT_TIMEOUT_SECONDS={_DEFAULT_TIMEOUT_SECONDS}s is below the "
        "240s floor required to accommodate observed sparse-web enrichment "
        "latency (~145s). See tasks/lessons.md 'Synthetic test domains "
        "stress agent enrichment latency budgets' (2026-05-18)."
    )


def test_default_connect_timeout_caps_outage_amplification():
    """Lock-in that connect timeout is short so outages fail fast.

    Without a bounded connect timeout, a bad ``AGENT_ACTION_CORE_BASE_URL``
    or DNS failure would tie up each DBOS retry for the full read budget
    (300s × 5 retries = 25 minutes). Connect timeout must stay short
    enough that a connectivity outage surfaces within seconds, not
    minutes. Codex M5.2 fix #1 R1 P2 finding.
    """
    from services.agent_action_core_client import _DEFAULT_CONNECT_TIMEOUT_SECONDS

    assert _DEFAULT_CONNECT_TIMEOUT_SECONDS <= 30.0, (
        f"_DEFAULT_CONNECT_TIMEOUT_SECONDS={_DEFAULT_CONNECT_TIMEOUT_SECONDS}s "
        "is above the 30s ceiling required to keep connectivity outages from "
        "amplifying into multi-minute workflow hangs across DBOS retries."
    )


def test_client_applies_per_phase_timeouts_not_single_global():
    """Lock-in that httpx.Timeout splits connect from read.

    Guards against a future refactor reverting to ``httpx.Timeout(value)``
    (single value applied to all 4 phases) which silently regresses the
    connect-timeout bound.
    """
    from services.agent_action_core_client import (
        AgentActionCoreClient,
        _DEFAULT_CONNECT_TIMEOUT_SECONDS,
        _DEFAULT_TIMEOUT_SECONDS,
    )

    client = AgentActionCoreClient(base_url="http://example.com")
    try:
        timeout = client._client.timeout
        # httpx.Timeout types connect/read as Optional[float]; assertions
        # below also assert non-None.
        assert timeout.connect is not None
        assert timeout.read is not None
        assert timeout.connect == _DEFAULT_CONNECT_TIMEOUT_SECONDS
        assert timeout.read == _DEFAULT_TIMEOUT_SECONDS
        # Connect must be strictly less than read; otherwise the per-phase
        # split is cosmetic.
        assert timeout.connect < timeout.read
    finally:
        # AsyncClient.close() is sync via httpx public API; aclose() is async.
        # Avoid asyncio noise — manually nil out the client reference.
        client._client = None  # type: ignore[assignment]
