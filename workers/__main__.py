"""Worker entrypoint: `python -m workers` starts the account-provisioning loop.

Required environment variables:
- DATABASE_URL — Postgres connection string (Neon eq-dev or production)
- EQ_AGENT_ACTION_CORE_URL — Base URL of the eq-agent-action-core service
- EQ_AGENT_ACTION_CORE_API_KEY — Bearer token for server-to-server auth

Optional:
- LOG_LEVEL — Python logging level (default: INFO)
- WORKER_POLL_INTERVAL_SECONDS — Seconds between polls (default: 5)
"""
from __future__ import annotations

import asyncio
import logging
import os

from services.agent_action_core_client import AgentActionCoreClient
from services.database import get_session_maker
from workers.account_provisioning_worker import run_worker_loop


async def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    logger = logging.getLogger(__name__)

    agent_url = os.environ["EQ_AGENT_ACTION_CORE_URL"]
    agent_api_key = os.environ["EQ_AGENT_ACTION_CORE_API_KEY"]
    interval = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "5"))

    agent_client = AgentActionCoreClient(
        base_url=agent_url,
        api_key=agent_api_key,
    )
    session_factory = get_session_maker()

    logger.info(
        "Worker starting: agent_url=%s poll_interval=%.1fs",
        agent_url, interval,
    )

    try:
        await run_worker_loop(
            session_factory=session_factory,
            agent_client=agent_client,
            interval_seconds=interval,
        )
    finally:
        await agent_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
