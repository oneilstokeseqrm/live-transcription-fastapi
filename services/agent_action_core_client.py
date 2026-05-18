"""HTTP client for eq-agent-action-core ``/api/enrich`` (Phase 1.5 M3).

The Phase 1 worker client was written against an imagined contract
(``{tenant_id, domain, worker_attempt_id}`` body, ``{account_id, domain}``
response). The live agent contract — probed 2026-05-15 against
``https://eq-agent-action-core-production.up.railway.app/openapi.json`` —
is different:

- Request body: ``{url: string, effort: "low"|"medium"|"high"}``.
- Query param: ``stream: bool = true``. We pass ``stream=false`` to get a
  single blocking JSON body instead of SSE.
- Auth: Bearer JWT (HS256 internal JWT, the agent extracts ``tenant_id``
  from claims for tenant scoping).
- Response: the OpenAPI declares ``{}`` (empty). The actual shape is the
  ``AccountProfile`` declared in
  ``services/account_provisioning/types.py``; the contract-pinning test
  at ``tests/contract/test_agent_enrich_response_shape.py`` asserts the
  live response satisfies that shape.

Plan §3.2 + §5.3 + §6.

This client is consumed by the DBOS workflow step
``call_agent_enrich`` (M3) inside an async workflow. Errors are
classified narrowly (Item 3 of test-discipline-gaps):

- ``AgentEnrichTransientError`` — retry-eligible (5xx, network timeouts,
  read errors). DBOS retries the step per its retry policy.
- ``AgentEnrichTerminalError`` — fail-loud (4xx other than 429). The
  workflow surfaces the error; the operator investigates.
"""

from __future__ import annotations

import httpx

from services.account_provisioning.types import (
    AccountProfile,
    AgentEnrichTerminalError,
    AgentEnrichTransientError,
)


# Worst-case agent latency observed on sparse-web synthetic domains (M5 E2E,
# 2026-05-18): 145s for `cold-prospect-{uuid}.com`. Real customer domains
# with rich web presence enrich in 30-90s, but stealth-mode / new-company /
# low-web-presence prospects can stretch toward the 120-150s range as the
# agent retries Tavily searches with progressively broader queries. 300s
# gives headroom for those cases without masking genuine hangs (DBOS retry
# policy still bounds total time at max_attempts × interval × backoff).
# See tasks/lessons.md "Synthetic test domains stress agent enrichment
# latency budgets" for the full diagnosis.
_DEFAULT_TIMEOUT_SECONDS = 300.0


class AgentActionCoreClient:
    """Async HTTP client for ``/api/enrich`` and ``/api/enrich/{run_id}``.

    Instantiated once per workflow step invocation. ``jwt`` is supplied
    per-call rather than at construction so the same client instance can
    serve multiple tenants in a multi-tenant deploy without leaking
    credentials between calls.
    """

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))

    async def enrich(
        self,
        *,
        url: str,
        effort: str = "medium",
        jwt: str,
    ) -> AccountProfile:
        """POST ``/api/enrich?stream=false`` → returns ``AccountProfile``.

        Raises:
          AgentEnrichTransientError: 5xx, 429, timeout, network errors.
          AgentEnrichTerminalError: 4xx (other than 429), unparseable body.
        """
        return await self._post_enrich(url=url, effort=effort, jwt=jwt)

    async def get_run(self, *, run_id: str, jwt: str) -> AccountProfile:
        """GET ``/api/enrich/{run_id}`` → returns the recorded ``AccountProfile``.

        Used by Step 3's crash-recovery replay path: if Step 3 cached a
        ``run_id`` via ``DBOS.set_event`` and then the workflow crashed
        before the step's success was checkpointed, the retry calls this
        endpoint to fetch the already-computed profile instead of paying
        for a second 30-90s enrich.

        Plan §6.4 + §15 item 3.
        """
        endpoint = f"{self.base_url}/api/enrich/{run_id}"
        try:
            response = await self._client.get(
                endpoint,
                headers={"Authorization": f"Bearer {jwt}"},
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise AgentEnrichTransientError(
                f"GET /api/enrich/{{run_id}} timed out or network error: {exc}"
            ) from exc

        self._raise_for_status(response)
        return self._parse_profile(response)

    async def _post_enrich(
        self,
        *,
        url: str,
        effort: str,
        jwt: str,
    ) -> AccountProfile:
        endpoint = f"{self.base_url}/api/enrich"
        try:
            response = await self._client.post(
                endpoint,
                params={"stream": "false"},
                headers={"Authorization": f"Bearer {jwt}"},
                json={"url": url, "effort": effort},
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise AgentEnrichTransientError(
                f"POST /api/enrich timed out or network error: {exc}"
            ) from exc

        self._raise_for_status(response)
        return self._parse_profile(response)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Translate HTTP status → narrow exception types.

        - 2xx: pass.
        - 429 / 5xx: transient — DBOS retries.
        - 4xx (other than 429): terminal — workflow fails loud.
        """
        if 200 <= response.status_code < 300:
            return
        if response.status_code == 429 or response.status_code >= 500:
            raise AgentEnrichTransientError(
                f"Agent returned {response.status_code}: {response.text[:200]}"
            )
        raise AgentEnrichTerminalError(
            f"Agent returned {response.status_code}: {response.text[:200]}"
        )

    @staticmethod
    def _parse_profile(response: httpx.Response) -> AccountProfile:
        try:
            data = response.json()
        except ValueError as exc:
            raise AgentEnrichTerminalError(
                f"Agent response was not valid JSON: {response.text[:200]}"
            ) from exc
        if not isinstance(data, dict):
            raise AgentEnrichTerminalError(
                f"Agent response was not a JSON object: type={type(data).__name__}"
            )
        try:
            return AccountProfile.model_validate(data)
        except ValueError as exc:
            # Pydantic raises ValidationError (a subclass of ValueError).
            # The contract-pinning test in tests/contract/ is the load-bearing
            # guard; if this fires in production it's a real contract drift.
            raise AgentEnrichTerminalError(
                f"Agent response did not match AccountProfile contract: {exc}"
            ) from exc

    async def aclose(self) -> None:
        await self._client.aclose()
