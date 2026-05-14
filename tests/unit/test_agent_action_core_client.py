"""HTTP client for eq-agent-action-core POST /api/enrich."""

import pytest
import httpx
from unittest.mock import AsyncMock, patch
from services.agent_action_core_client import AgentActionCoreClient, EnrichResult


@pytest.mark.asyncio
async def test_enrich_returns_account_id():
    client = AgentActionCoreClient(base_url="http://test", api_key="key")
    fake_response = httpx.Response(
        200,
        json={"account_id": "acct-new-1", "domain": "acme.com"},
        request=httpx.Request("POST", "http://test/api/enrich"),
    )
    with patch.object(client._client, "post", AsyncMock(return_value=fake_response)):
        result = await client.enrich(
            tenant_id="t1",
            domain="acme.com",
            worker_attempt_id="attempt-1",
        )
    assert isinstance(result, EnrichResult)
    assert result.account_id == "acct-new-1"


@pytest.mark.asyncio
async def test_enrich_sends_worker_attempt_id_header():
    client = AgentActionCoreClient(base_url="http://test", api_key="key")
    captured = {}

    async def fake_post(url, **kwargs):
        captured["headers"] = kwargs.get("headers", {})
        captured["json"] = kwargs.get("json", {})
        return httpx.Response(
            200,
            json={"account_id": "x", "domain": "acme.com"},
            request=httpx.Request("POST", url),
        )

    with patch.object(client._client, "post", side_effect=fake_post):
        await client.enrich(tenant_id="t1", domain="acme.com", worker_attempt_id="abc")
    assert captured["headers"].get("X-Idempotency-Key") == "abc" \
        or captured["json"].get("worker_attempt_id") == "abc"
