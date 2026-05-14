"""Worker entrypoint: `python -m workers` starts both the account-provisioning loop AND the outbox publisher.

Required environment variables:
- DATABASE_URL — Postgres connection string (Neon eq-dev or production)
- EQ_AGENT_ACTION_CORE_URL — Base URL of the eq-agent-action-core service
- EQ_AGENT_ACTION_CORE_API_KEY — Bearer token for server-to-server auth

Optional:
- LOG_LEVEL — Python logging level (default: INFO)
- WORKER_POLL_INTERVAL_SECONDS — Seconds between worker polls (default: 5)
- PUBLISHER_POLL_INTERVAL_SECONDS — Seconds between publisher polls (default: 2)
- AWS_REGION — EventBridge region (default: us-east-1)
- EVENTBRIDGE_BUS_NAME — EventBridge bus name (default: default)

Architecture (Phase 1.5):
The worker and publisher run as two asyncio tasks in the SAME OS process,
launched together via asyncio.gather. We deliberately co-locate them at this
scale (1 replica) — the threshold for splitting the publisher into its own
service is >5 worker replicas. See
docs/superpowers/specs/2026-05-14-dispatch-patterns-research.md for the
decision record.

If either task raises, asyncio.gather propagates the exception and the
process exits; Railway will then restart the service. Restart-on-failure
is the supervision model.
"""
from __future__ import annotations

import asyncio
import logging
import os

import boto3

from services.agent_action_core_client import AgentActionCoreClient
from services.database import get_session_maker
from workers.account_provisioning_worker import run_worker_loop
from workers.outbox_publisher import AsyncEventBridgeClient, run_publisher_loop


async def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    logger = logging.getLogger(__name__)

    agent_url = os.environ["EQ_AGENT_ACTION_CORE_URL"]
    agent_api_key = os.environ["EQ_AGENT_ACTION_CORE_API_KEY"]
    worker_interval = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "5"))
    publisher_interval = float(os.getenv("PUBLISHER_POLL_INTERVAL_SECONDS", "2"))
    aws_region = os.getenv("AWS_REGION", "us-east-1")

    agent_client = AgentActionCoreClient(
        base_url=agent_url,
        api_key=agent_api_key,
    )
    session_factory = get_session_maker()

    eventbridge_boto = boto3.client("events", region_name=aws_region)
    eventbridge_client = AsyncEventBridgeClient(eventbridge_boto)

    logger.info(
        "Worker starting: agent_url=%s poll_interval=%.1fs",
        agent_url, worker_interval,
    )
    logger.info(
        "Publisher starting: interval=%.1fs region=%s bus=%s",
        publisher_interval, aws_region, os.getenv("EVENTBRIDGE_BUS_NAME", "default"),
    )

    try:
        # asyncio.gather propagates the first exception — if either task
        # crashes, the other is cancelled and the process exits for Railway
        # to restart.
        await asyncio.gather(
            run_worker_loop(
                session_factory=session_factory,
                agent_client=agent_client,
                interval_seconds=worker_interval,
            ),
            run_publisher_loop(
                session_factory=session_factory,
                eventbridge_client=eventbridge_client,
                interval_seconds=publisher_interval,
            ),
        )
    finally:
        await agent_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
