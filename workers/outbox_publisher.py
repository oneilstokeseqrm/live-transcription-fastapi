"""Outbox publisher: emits unpublished account_provisioning_outbox rows to EventBridge.

Phase 1.5 dispatch strategy (validated against 2024-2026 frontier research —
see docs/superpowers/specs/2026-05-14-dispatch-patterns-research.md):

- Polling is the right default at our scale (1 worker replica).
- Per-row transactional isolation: each outbox row is published in its OWN
  Postgres transaction so one row's MARK_FAILED commit does NOT roll back
  a sibling row's MARK_PUBLISHED commit.
- The outbox_row_id IS the idempotency key — Replay-safe by construction:
  re-publishing an already-marked-published row is a no-op (the poll SELECT
  filters by published_at IS NULL).

Architectural note on the EventBridge client:
- The repo's existing EventBridge integration (services/aws_event_publisher.py)
  uses synchronous boto3. This module wraps a sync boto3 client with
  asyncio.to_thread so we don't introduce an aioboto3 dependency just for
  the publisher. If a future iteration moves to aioboto3, drop the wrapper
  and pass the aioboto3 client directly — the protocol is identical
  (`async def put_events(Entries)`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from sqlalchemy import text


logger = logging.getLogger(__name__)


SELECT_UNPUBLISHED_SQL = text("""
    SELECT id::text, tenant_id::text, queue_id::text, event_type,
           account_id::text, payload_json, publish_attempts
    FROM account_provisioning_outbox
    WHERE published_at IS NULL
    ORDER BY created_at ASC
    LIMIT :limit
""")


MARK_PUBLISHED_SQL = text("""
    UPDATE account_provisioning_outbox
    SET published_at = NOW(),
        publish_attempts = publish_attempts + 1,
        last_publish_error = NULL
    WHERE id = :id
""")


MARK_FAILED_SQL = text("""
    UPDATE account_provisioning_outbox
    SET publish_attempts = publish_attempts + 1,
        last_publish_error = :error
    WHERE id = :id
""")


SELECT_SINGLE_SQL = text("""
    SELECT id::text, tenant_id::text, queue_id::text, event_type,
           account_id::text, payload_json
    FROM account_provisioning_outbox
    WHERE id = :id
""")


def _build_event(row: Any) -> dict:
    """Build the EventBridge entry dict from an outbox row.

    Source/DetailType convention:
    - Source = "com.eq.contact-quality" (this initiative; distinct from the
      pre-existing "com.yourapp.transcription" used by services/aws_event_publisher).
    - DetailType = "AccountProvisioning.<event_type>" so EventBridge rules can
      route on the event_type suffix without parsing Detail.

    Detail carries:
    - outbox_row_id (the idempotency key for downstream consumers)
    - tenant_id (every event MUST carry tenant_id — tenant isolation invariant)
    - queue_id (pending_account_mappings row that produced this event)
    - account_id (the materialized account)
    - event_type (denormalized for filtering)
    - payload (the original payload_json — contact_ids, interaction_ids, etc.)
    """
    return {
        "Source": "com.eq.contact-quality",
        "DetailType": f"AccountProvisioning.{row.event_type}",
        "Detail": json.dumps({
            "outbox_row_id": row.id,
            "tenant_id": row.tenant_id,
            "queue_id": row.queue_id,
            "account_id": row.account_id,
            "event_type": row.event_type,
            "payload": row.payload_json,
        }),
        "EventBusName": os.getenv("EVENT_BUS_NAME", "default"),
    }


class AsyncEventBridgeClient:
    """Thin async wrapper around a synchronous boto3 events client.

    Wraps put_events with asyncio.to_thread so the publisher loop stays
    cooperative without taking on an aioboto3 dependency. If aioboto3 lands
    later, replace this wrapper with the aioboto3 client directly — the
    publisher only uses `async def put_events(Entries)`.
    """

    def __init__(self, boto_client: Any) -> None:
        self._client = boto_client

    async def put_events(self, *, Entries: list[dict]) -> dict:
        return await asyncio.to_thread(self._client.put_events, Entries=Entries)


async def publish_one(
    session: Any,
    eventbridge_client: Any,
    outbox_row_id: str,
) -> None:
    """Publish a single outbox row to EventBridge.

    Reads the row, calls EventBridge put_events, and either marks the row
    published (FailedEntryCount=0) or records the failure (FailedEntryCount>0)
    and raises RuntimeError so the caller logs + moves on.

    The caller manages the session's transaction lifecycle. Both the
    success (MARK_PUBLISHED) and failure (MARK_FAILED) writes happen on
    the SAME session that read the row, so the per-row transaction either
    commits both `SELECT + UPDATE` together or rolls back both together.
    """
    row = (await session.execute(SELECT_SINGLE_SQL, {"id": outbox_row_id})).one()
    event = _build_event(row)

    response = await eventbridge_client.put_events(Entries=[event])

    if response.get("FailedEntryCount", 0) > 0:
        # Encode the failed entries for diagnostic context, truncated to fit
        # the last_publish_error column without an arbitrarily large blob.
        error_msg = json.dumps(response.get("Entries", []))[:1000]
        await session.execute(
            MARK_FAILED_SQL,
            {"id": outbox_row_id, "error": error_msg},
        )
        raise RuntimeError(f"EventBridge publish failed: {error_msg}")

    await session.execute(MARK_PUBLISHED_SQL, {"id": outbox_row_id})


async def run_publisher_loop(
    session_factory: Any,
    eventbridge_client: Any,
    interval_seconds: float = 2.0,
    batch_size: int = 10,
) -> None:
    """Main publisher loop — polls for unpublished outbox rows and emits them.

    Fresh session per outbox row (NOT a shared session across the batch):

    SQLAlchemy 2.0's AsyncSession.execute() autobegins a transaction on first
    read. Sharing the poll-SELECT session with the per-row UPDATEs would mean
    one row's MARK_FAILED could roll back a sibling row's MARK_PUBLISHED inside
    the same transaction. We solve this with two patterns:

    1. The poll SELECT runs in its own short-lived session that closes before
       per-row processing starts.
    2. Each outbox row runs in its OWN fresh session_factory() lifecycle with
       its OWN begin()/commit() block.

    This mirrors workers/account_provisioning_worker.run_worker_loop's
    per-entry isolation (carry-forward invariant from PR #12 / T1.5.7).
    """
    while True:
        try:
            # Poll in its own session — closes before per-row work begins.
            async with session_factory() as poll_session:
                async with poll_session.begin():
                    rows = (await poll_session.execute(
                        SELECT_UNPUBLISHED_SQL, {"limit": batch_size},
                    )).all()

            # Per-row processing — fresh session per row for txn isolation.
            for row in rows:
                try:
                    async with session_factory() as session:
                        async with session.begin():
                            await publish_one(
                                session=session,
                                eventbridge_client=eventbridge_client,
                                outbox_row_id=row.id,
                            )
                except Exception:
                    # publish_one already recorded MARK_FAILED in its own
                    # session (committed independently). Log and move on
                    # so the rest of the batch still gets a shot.
                    logger.exception("Publish failed for outbox row %s", row.id)
        except Exception:
            logger.exception("Publisher loop error")
        await asyncio.sleep(interval_seconds)
