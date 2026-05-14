"""Account-provisioning worker.

Polls pending_account_mappings WHERE status='approved', takes advisory
lock, calls eq-agent-action-core, runs materialization transaction.

Replay-safe via:
- Advisory lock prevents concurrent processing of the same queue_id.
- worker_attempt_id is stable per (tenant_id, queue_id) so the agent
  treats replayed calls as the same request (AI-native research
  recommendation 2026-05-14).
- Materialization is one atomic transaction with the outbox row.
- ON CONFLICT idempotency in contacts.
- status='mapped' is treated as a terminal no-op.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.agent_action_core_client import AgentActionCoreClient
from workers.advisory_lock import try_acquire_queue_lock
from workers.materialization import materialize_account_approval

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger(__name__)


SELECT_APPROVED_SQL = text("""
    SELECT id::text, tenant_id::text, domain, status, resolved_account_id::text
    FROM pending_account_mappings
    WHERE status IN ('approved', 'creating')
    ORDER BY updated_at ASC
    LIMIT :limit
""")


SET_CREATING_SQL = text("""
    UPDATE pending_account_mappings
    SET status = 'creating',
        creation_started_at = COALESCE(creation_started_at, NOW()),
        updated_at = NOW()
    WHERE id = :queue_id AND status = 'approved'
""")


SELECT_STATUS_SQL = text("""
    SELECT status, resolved_account_id::text AS resolved_account_id
    FROM pending_account_mappings
    WHERE id = :queue_id
""")


SELECT_INFO_SQL = text("""
    SELECT tenant_id::text AS tenant_id, domain
    FROM pending_account_mappings
    WHERE id = :queue_id
""")


async def process_one_approved_entry(
    session: AsyncSession,
    queue_id: str,
    agent_client: AgentActionCoreClient,
) -> None:
    """Process a single approved queue entry. Caller manages transaction."""
    got_lock = await try_acquire_queue_lock(session, queue_id)
    if not got_lock:
        logger.info("Skipping queue_id=%s — another worker holds the lock", queue_id)
        return

    row = (await session.execute(SELECT_STATUS_SQL, {"queue_id": queue_id})).one()
    if row.status == "mapped":
        logger.info("Queue %s already mapped; skip (replay-safe)", queue_id)
        return
    if row.status not in ("approved", "creating"):
        logger.warning("Queue %s status=%s; not processing", queue_id, row.status)
        return

    # Idempotent: SET_CREATING's WHERE status='approved' clause makes this
    # a no-op if status is already 'creating' (replay case).
    await session.execute(SET_CREATING_SQL, {"queue_id": queue_id})

    info = (await session.execute(SELECT_INFO_SQL, {"queue_id": queue_id})).one()

    # Stable per (tenant_id, queue_id) for cross-tenant safety in the agent's
    # idempotency map. Same key across replays means the agent dedupes.
    worker_attempt_id = f"{info.tenant_id}:queue-{queue_id}"

    enrich_result = await agent_client.enrich(
        tenant_id=info.tenant_id,
        domain=info.domain,
        worker_attempt_id=worker_attempt_id,
    )

    await materialize_account_approval(
        session=session,
        tenant_id=info.tenant_id,
        queue_id=queue_id,
        account_id=enrich_result.account_id,
        event_type="account_created",
    )


async def run_worker_loop(
    session_factory: "async_sessionmaker",
    agent_client: AgentActionCoreClient,
    interval_seconds: float = 5.0,
    batch_size: int = 10,
) -> None:
    """Main worker loop — polls for approved entries and processes them."""
    while True:
        try:
            async with session_factory() as session:
                async with session.begin():
                    rows = (await session.execute(
                        SELECT_APPROVED_SQL, {"limit": batch_size},
                    )).all()
                    for row in rows:
                        await process_one_approved_entry(
                            session=session,
                            queue_id=row.id,
                            agent_client=agent_client,
                        )
        except Exception:
            logger.exception("Worker loop error")
        await asyncio.sleep(interval_seconds)
