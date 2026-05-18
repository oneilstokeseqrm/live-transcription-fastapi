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
            json={"name": "Acme Inc", "domain": "acme.com"},
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
        return httpx.Response(200, json={"name": "Acme Inc"})

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
    """Pydantic validation fail → terminal. AccountProfile.name is required."""
    def handler(_request: httpx.Request) -> httpx.Response:
        # Missing 'name'
        return httpx.Response(200, json={"domain": "acme.com"})

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
    """``extra='allow'`` on AccountProfile keeps us forward-compat with agent additions."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "name": "Acme Inc",
            "industry": "Manufacturing",
            "unknown_future_field": {"some": "stuff"},
            "run_id": "abc-123",
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
    # Extra fields preserved via model_dump (forward-compat).
    dumped = profile.model_dump()
    assert dumped.get("run_id") == "abc-123"


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
