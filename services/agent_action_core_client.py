"""HTTP client for eq-agent-action-core POST /api/enrich.

Sends worker_attempt_id as both a JSON field AND an X-Idempotency-Key header
so the agent can deduplicate either way (defensive double-write; the agent
side may choose either as canonical).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass
class EnrichResult:
    account_id: str
    domain: str


class AgentActionCoreClient:
    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 90.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))

    async def enrich(
        self,
        tenant_id: str,
        domain: str,
        worker_attempt_id: str,
    ) -> EnrichResult:
        url = f"{self.base_url}/api/enrich"
        response = await self._client.post(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-Idempotency-Key": worker_attempt_id,
                "Content-Type": "application/json",
            },
            json={
                "tenant_id": tenant_id,
                "domain": domain,
                "worker_attempt_id": worker_attempt_id,
            },
        )
        response.raise_for_status()
        data = response.json()
        return EnrichResult(account_id=data["account_id"], domain=data["domain"])

    async def aclose(self) -> None:
        await self._client.aclose()
