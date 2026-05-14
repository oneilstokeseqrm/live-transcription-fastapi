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


# Codex Round 3 P2 #3: ORDER BY publish_attempts ASC, created_at ASC.
#
# Pre-fix ordering was `created_at ASC` only — repeatedly-failing rows at
# the front of the queue (low created_at, failing on every poll) would
# starve newer events forever. With batch_size=10, 10 poison rows blocked
# all subsequent events indefinitely. MARK_FAILED only bumps
# publish_attempts; nothing rotates failed rows toward the back.
#
# Post-fix: publish_attempts ASC ensures all publish_attempts=0 rows
# process first (newest never-attempted rows), then publish_attempts=1, 2,
# .... Failed rows naturally cycle to the back; newer events at the front
# always get a turn. created_at ASC remains the tiebreaker within an
# attempts level, preserving FIFO semantics for the common case where
# nothing fails.
#
# Mirrors the failed-row-rotation pattern from
# workers/account_provisioning_worker.py (queue worker bumps updated_at on
# a failed row in a separate session to push it to the back of the queue
# ORDER BY — same conceptual fix, different mechanism here because the
# publisher doesn't have an updated_at column on outbox rows).
#
# Note: at very high publish_attempts (100+), a dead-letter mechanism or a
# max_attempts ceiling becomes valuable. That's a Phase 2 concern; this
# ORDER BY change resolves the immediate starvation pattern.
SELECT_UNPUBLISHED_SQL = text("""
    SELECT id::text, tenant_id::text, queue_id::text, event_type,
           account_id::text, payload_json, publish_attempts
    FROM account_provisioning_outbox
    WHERE published_at IS NULL
    ORDER BY publish_attempts ASC, created_at ASC
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
        # Codex Round 2 P2 #5: align with the repo-wide env var name. The
        # pre-existing aws_event_publisher integration reads
        # EVENTBRIDGE_BUS_NAME (see main.py:62 + .env.example:55).
        "EventBusName": os.getenv("EVENTBRIDGE_BUS_NAME", "default"),
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
    session_factory: Any,
    eventbridge_client: Any,
    outbox_row_id: str,
) -> None:
    """Publish a single outbox row to EventBridge.

    Each DB write commits in its OWN fresh session so that MARK_FAILED
    persists even when the function raises (the raise rolls back nothing
    we want to keep). Three sessions used:

    1. Read session: SELECTs the row.
    2. On EventBridge failure: a fresh fail-session commits MARK_FAILED
       BEFORE we raise — so publish_attempts and last_publish_error
       become visible to operators.
    3. On EventBridge success: a fresh publish-session commits
       MARK_PUBLISHED.

    Mirrors the failed-row-updated_at-bump-in-separate-session pattern
    from PR #12 (see tasks/lessons.md). Without this, the same failing
    row is re-tried on every poll with no visible state change because
    the caller's `async with session.begin():` rolls back the
    MARK_FAILED write along with the raise.
    """
    # 1. Read session — SELECT the row. Closes (and commits an empty txn)
    # before we make the network call so we don't hold a Postgres txn
    # open across EventBridge latency.
    async with session_factory() as read_session:
        async with read_session.begin():
            row = (await read_session.execute(
                SELECT_SINGLE_SQL, {"id": outbox_row_id},
            )).one()

    event = _build_event(row)

    try:
        response = await eventbridge_client.put_events(Entries=[event])
    except Exception as e:
        # Codex P2 #5: put_events raised (network error, auth error,
        # throttling exception, etc.). Without this branch the row stays
        # unchanged and the publisher retries it forever with zero visible
        # state change (publish_attempts not incremented, last_publish_error
        # empty). MARK_FAILED on a fresh session so the error is durably
        # visible, then re-raise so the run_publisher_loop can log + skip
        # to the next row.
        error_msg = f"EventBridge exception: {type(e).__name__}: {e}"[:1000]
        async with session_factory() as fail_session:
            async with fail_session.begin():
                await fail_session.execute(
                    MARK_FAILED_SQL,
                    {"id": outbox_row_id, "error": error_msg},
                )
        raise

    if response.get("FailedEntryCount", 0) > 0:
        # Encode the failed entries for diagnostic context, truncated to fit
        # the last_publish_error column without an arbitrarily large blob.
        error_msg = json.dumps(response.get("Entries", []))[:1000]
        # 2. MARK_FAILED in its OWN session — commits before we raise so
        # the error is durably visible (publish_attempts increments,
        # last_publish_error is set). If THIS session also fails to
        # commit, the row stays in its prior state and the loop will
        # retry next interval. Acceptable because it's exceptional.
        async with session_factory() as fail_session:
            async with fail_session.begin():
                await fail_session.execute(
                    MARK_FAILED_SQL,
                    {"id": outbox_row_id, "error": error_msg},
                )
        raise RuntimeError(f"EventBridge publish failed: {error_msg}")

    # 3. MARK_PUBLISHED in its OWN session — symmetric with the failure
    # branch. Each successful publish commits independently of siblings
    # in the same poll batch.
    async with session_factory() as publish_session:
        async with publish_session.begin():
            await publish_session.execute(
                MARK_PUBLISHED_SQL, {"id": outbox_row_id},
            )


async def run_publisher_loop(
    session_factory: Any,
    eventbridge_client: Any,
    interval_seconds: float = 2.0,
    batch_size: int = 10,
) -> None:
    """Main publisher loop — polls for unpublished outbox rows and emits them.

    The poll SELECT runs in its own short-lived session that closes before
    per-row processing starts. Each outbox row is then handed to
    `publish_one`, which owns ALL its own session lifecycles (read,
    publish, and fail sessions are each opened+committed independently).

    Per-row transactional isolation: because publish_one commits each
    MARK_PUBLISHED / MARK_FAILED on a fresh session, one row's MARK_FAILED
    cannot roll back a sibling row's MARK_PUBLISHED. The per-row
    try/except below catches RuntimeError raised by publish_one after it
    has already committed the MARK_FAILED write in its own session — so
    the loop can log the failure and move on to the next row.

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

            # Per-row processing — publish_one owns its own sessions.
            for row in rows:
                try:
                    await publish_one(
                        session_factory=session_factory,
                        eventbridge_client=eventbridge_client,
                        outbox_row_id=row.id,
                    )
                except Exception:
                    # publish_one already committed MARK_FAILED in its own
                    # session. Log and move on so the rest of the batch
                    # still gets a shot.
                    logger.exception("Publish failed for outbox row %s", row.id)
        except Exception:
            logger.exception("Publisher loop error")
        await asyncio.sleep(interval_seconds)
