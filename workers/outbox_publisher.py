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
#
# Codex Round 4 P1 #1: FOR UPDATE SKIP LOCKED on the poll SELECT. Without
# this, two publisher replicas polling concurrently (e.g. during a deploy
# window with old + new container both live for ~30s) would each pull the
# same unpublished rows into their batch. The per-row SELECT_FOR_UPDATE_SQL
# below would still serialize the publish itself, but having the poll skip
# locked rows means each replica's batch is disjoint to begin with — less
# wasted work and clearer operational behavior. See
# docs/superpowers/specs/2026-05-14-dispatch-patterns-research.md §2.4.
SELECT_UNPUBLISHED_SQL = text("""
    SELECT id::text, tenant_id::text, queue_id::text, event_type,
           account_id::text, payload_json, publish_attempts
    FROM account_provisioning_outbox
    WHERE published_at IS NULL
    ORDER BY publish_attempts ASC, created_at ASC
    LIMIT :limit
    FOR UPDATE SKIP LOCKED
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


# Codex Round 4 P1 #1: per-row SELECT FOR UPDATE SKIP LOCKED used inside
# publish_one. The lock is held for the duration of the surrounding
# transaction — so we hold it from SELECT through put_events through
# MARK_PUBLISHED, ensuring a sibling publisher that polled the same row
# pre-lock cannot ALSO call put_events on it.
#
# Returning published_at lets the handler short-circuit if a sibling
# publisher already published the row between our poll batch and our
# per-row lock acquisition (the SKIP LOCKED returns the row to us — we
# just need to see it's already done and bail without re-publishing).
SELECT_FOR_UPDATE_SQL = text("""
    SELECT id::text, tenant_id::text, queue_id::text, event_type,
           account_id::text, payload_json, published_at
    FROM account_provisioning_outbox
    WHERE id = :id
    FOR UPDATE SKIP LOCKED
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

    Concurrency model: a per-row SELECT FOR UPDATE SKIP LOCKED holds a
    Postgres row lock for the duration of the EventBridge call +
    MARK_PUBLISHED. This prevents duplicate emissions during multi-process
    overlap (e.g., deploy windows). Concurrent siblings calling publish_one
    on the same row see None from SELECT FOR UPDATE and no-op.

    Codex Round 4 P1 #1 — multi-replica safe via per-row row lock:

    The publish lifecycle for a single row is wrapped in ONE lock-holding
    transaction:

        BEGIN
        SELECT ... WHERE id=:id FOR UPDATE SKIP LOCKED  -- 1 row or none
        (if no row: sibling has the lock OR row already published → ROLLBACK/return)
        put_events(...)
        UPDATE ... SET published_at = NOW() WHERE id = :id  -- MARK_PUBLISHED
        COMMIT  -- lock + published_at flip atomic w.r.t. sibling replicas

    Why same-session for the success path: holding the row lock from the
    SELECT through MARK_PUBLISHED COMMIT means a sibling publisher (e.g.
    the OTHER replica during a deploy window) attempting to lock the same
    row via SKIP LOCKED will see ZERO rows and bail. After we commit, the
    sibling's next poll won't return this row at all (published_at IS NULL
    filter). No duplicate put_events.

    Codex Round 5 P1 #1 — failure-path deadlock fix:

    If put_events raises (or returns FailedEntryCount > 0), we CAPTURE the
    error message via the `captured_error` closure variable, RAISE to exit
    the `lock_session.begin()` block (rolls back the txn AND releases the
    FOR UPDATE row lock), and THEN open a fresh fail_session for
    MARK_FAILED — AFTER lock_session has already exited.

    Why: doing MARK_FAILED inside the lock-holding block would deadlock
    the fresh fail_session against our own row lock. The fail_session
    UPDATE would block waiting for the row lock to release; the
    lock_session's transaction can't release the lock until its
    `async with begin():` block exits; that block can't exit because the
    inner code (the fresh fail_session UPDATE) is still pending. Postgres
    does NOT detect this as a true deadlock because lock_session isn't
    waiting on a Postgres lock — it's waiting on the async call stack to
    unwind. Production result: the call hangs forever (or until the
    container is killed).

    Carry-forward invariant: MARK_FAILED still commits on a SEPARATE
    session from the SELECT/MARK_PUBLISHED session, so a sibling row's
    success-commit is not coupled to this row's failure-commit. The
    timing of the fail_session open is what changed (after lock_session
    exits, not inside its begin block).

    No-op paths (return without put_events):

    - SELECT FOR UPDATE returns None: a sibling publisher holds the lock.
      They're publishing it; we let them finish.
    - SELECT FOR UPDATE returns a row but published_at IS NOT NULL: a
      sibling already finished publishing between our poll batch and our
      lock acquisition. Nothing to do.
    """
    # Closure variable captured by the inner block on the failure paths
    # and consumed by the post-block MARK_FAILED write. None on success
    # paths and on the no-op paths (returns without raising).
    captured_error: str | None = None

    try:
        # Open the lock session. We hold it across put_events and
        # MARK_PUBLISHED so a sibling replica cannot also publish this row.
        async with session_factory() as lock_session:
            async with lock_session.begin():
                row = (await lock_session.execute(
                    SELECT_FOR_UPDATE_SQL, {"id": outbox_row_id},
                )).one_or_none()

                if row is None:
                    # SKIP LOCKED returned zero rows — either a sibling
                    # publisher holds the lock, or the row was deleted (rare,
                    # not currently a code path). Either way: noop.
                    logger.debug(
                        "publish_one noop: outbox row %s not lockable "
                        "(sibling has lock or row absent)",
                        outbox_row_id,
                    )
                    return

                if row.published_at is not None:
                    # Race: a sibling publisher published this row between our
                    # poll batch SELECT and our per-row lock acquisition.
                    logger.debug(
                        "publish_one noop: outbox row %s already published",
                        outbox_row_id,
                    )
                    return

                event = _build_event(row)

                # The put_events call happens INSIDE the lock session's
                # transaction. The row lock spans this network call, which
                # bounds how long a publisher container can hold a row lock to
                # the EventBridge put_events latency (typically <500ms). This
                # is acceptable because (a) we want sibling replicas to back
                # off cleanly via SKIP LOCKED, and (b) a stuck publisher will
                # release the lock when the container restarts, freeing the
                # row for retry on the next poll cycle.
                try:
                    response = await eventbridge_client.put_events(Entries=[event])
                except Exception as e:
                    # Capture the error message so the post-lock block can
                    # write MARK_FAILED in a fresh session AFTER this
                    # session's row lock releases. Re-raise to unwind
                    # `lock_session.begin()` (rollback + lock release).
                    captured_error = (
                        f"EventBridge exception: {type(e).__name__}: {e}"[:1000]
                    )
                    raise

                if response.get("FailedEntryCount", 0) > 0:
                    # Encode the failed entries for diagnostic context,
                    # truncated to fit the last_publish_error column.
                    captured_error = json.dumps(response.get("Entries", []))[:1000]
                    # Raise so the lock_session begin() block unwinds; the
                    # post-lock branch writes MARK_FAILED in a fresh session.
                    raise RuntimeError(
                        f"EventBridge publish failed: {captured_error}"
                    )

                # Success — MARK_PUBLISHED IN THE SAME LOCK SESSION so the
                # row's published_at flip is atomic w.r.t. concurrent
                # publishers. When this session's COMMIT lands, the row's
                # published_at is set AND its row lock is released; any
                # sibling now-polling for unpublished rows will skip it.
                await lock_session.execute(
                    MARK_PUBLISHED_SQL, {"id": outbox_row_id},
                )
    except Exception:
        # By the time we land here, the lock_session has exited its
        # `begin()` block AND its `session_factory()` context manager.
        # The transaction rolled back; the FOR UPDATE row lock is
        # released. We can safely open a fresh session to commit
        # MARK_FAILED without blocking on our own (now-released) lock.
        #
        # captured_error is set iff the exception came from the
        # put_events failure paths. Other unexpected exceptions (e.g. DB
        # connection drops mid-SELECT) propagate without writing
        # MARK_FAILED — the publisher loop's per-row try/except logs
        # them and the next poll cycle picks the row up unchanged.
        if captured_error is not None:
            async with session_factory() as fail_session:
                async with fail_session.begin():
                    await fail_session.execute(
                        MARK_FAILED_SQL,
                        {"id": outbox_row_id, "error": captured_error},
                    )
        raise


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
