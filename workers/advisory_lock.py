"""Postgres advisory-lock helpers for worker coordination."""

import hashlib
from sqlalchemy import text


def lock_key_for_queue_id(queue_id: str) -> int:
    """Deterministically map a queue UUID to an int8 advisory-lock key.

    Postgres `pg_try_advisory_xact_lock(bigint)` takes a signed 64-bit
    integer. We hash the queue_id and fold to int8 range.
    """
    h = hashlib.sha256(queue_id.encode("utf-8")).digest()
    # First 8 bytes as signed int8
    return int.from_bytes(h[:8], byteorder="big", signed=True)


TRY_LOCK_SQL = text("SELECT pg_try_advisory_xact_lock(:key)")


async def try_acquire_queue_lock(session, queue_id: str) -> bool:
    """Try to acquire a transaction-scoped advisory lock for this queue_id.

    Returns True on acquisition; False if another worker holds it.
    Auto-released at transaction commit/rollback.
    """
    key = lock_key_for_queue_id(queue_id)
    result = await session.execute(TRY_LOCK_SQL, {"key": key})
    return bool(result.scalar_one())
