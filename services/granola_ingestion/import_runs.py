"""Background-import run lifecycle + DERIVED progress (EQ-92 / Phase 3 B3).

One ``public.granola_import_runs`` row tracks each background Granola
history-import for a credential. ``/connect`` (import_scope='history') creates a
row and dispatches :func:`services.granola_ingestion.scheduler.
granola_import_one_credential`; the workflow drives :func:`run_one_cycle` and
calls the lifecycle helpers here.

**Progress is DERIVED, never counted** (plan §1a C1): ``done/deferred/skipped/
errors`` come from a COUNT/GROUP-BY over ``public.external_integration_runs`` for
the credential's notes since the run's ``started_at``. A stored counter would
double-count under DBOS step crash/replay; the derived read is idempotent.

This is a plain asyncpg module (NOT a vault accessor → no ALLOWLIST gate). Every
statement is tenant + user scoped (tenant isolation). The table's partial-unique
``(credential_id) WHERE state IN ('queued','running')`` enforces at-most-one
active import per credential at the DB layer (C7); :func:`get_or_create_active_
import_run` relies on it for idempotent dispatch / enqueue-atomicity (C8).
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from services.asyncpg_pool import get_asyncpg_pool

logger = logging.getLogger(__name__)

# external_integration_runs.status -> progress bucket. 'in_progress' (the
# adapter's pre-publish idempotency anchor) and any unknown value are uncounted.
_DONE_STATUS = "success"
_DEFERRED_STATUS = "deferred_pending_account"
_ERROR_STATUSES = ("failed", "failed_permanent")
# skip statuses are matched by the 'skipped' prefix (today: skipped_no_business_attendees).

# Idempotent get-or-create. The ON CONFLICT targets the partial-unique index
# `granola_import_runs_one_active_per_credential` via its predicate; a concurrent
# /connect (or a retry) that loses the race gets DO NOTHING -> NULL -> we SELECT
# the winning active row (C8).
_INSERT_ACTIVE_RUN_SQL = """
INSERT INTO public.granola_import_runs (tenant_id, user_id, credential_id, state)
VALUES ($1, $2, $3, 'queued')
ON CONFLICT (credential_id) WHERE state IN ('queued', 'running') DO NOTHING
RETURNING id
"""

_SELECT_ACTIVE_RUN_ID_SQL = """
SELECT id
FROM public.granola_import_runs
WHERE credential_id = $1 AND tenant_id = $2 AND user_id = $3
  AND state IN ('queued', 'running')
"""

_MARK_RUNNING_SQL = """
UPDATE public.granola_import_runs
SET state = 'running',
    started_at = COALESCE(started_at, NOW()),
    updated_at = NOW()
WHERE id = $1 AND tenant_id = $2 AND user_id = $3
  AND state IN ('queued', 'running')
"""

_SET_TOTAL_SQL = """
UPDATE public.granola_import_runs
SET total = $4, updated_at = NOW()
WHERE id = $1 AND tenant_id = $2 AND user_id = $3
  AND state IN ('queued', 'running')
"""

# Terminal transitions only fire from an active state, so a double-call (e.g. a
# DBOS step replay after the workflow already finished) is a harmless no-op.
_TERMINAL_SQL_TEMPLATE = """
UPDATE public.granola_import_runs
SET state = '{terminal}',
    finished_at = NOW(),
    updated_at = NOW()
WHERE id = $1 AND tenant_id = $2 AND user_id = $3
  AND state IN ('queued', 'running')
"""

_SELECT_RUN_SQL = """
SELECT state, total, started_at, finished_at
FROM public.granola_import_runs
WHERE id = $1 AND tenant_id = $2 AND user_id = $3
"""

# DERIVED progress. Scoped to the credential's notes (a credential is 1:1 with
# (tenant,user,'granola')) since the run started. Exact for a fresh first import
# (all rows created during the import); re-runs may undercount (created_at is
# preserved on UPSERT; already-success notes short-circuit) — exact re-run
# progress is the items-table fast-follow (backlog #21b).
_COUNT_BY_STATUS_SQL = """
SELECT status, count(*) AS n
FROM public.external_integration_runs
WHERE tenant_id = $1 AND user_id = $2 AND provider = 'granola'
  AND created_at >= $3
GROUP BY status
"""

_LATEST_RUN_SQL = """
SELECT id, state, total, started_at, finished_at, created_at
FROM public.granola_import_runs
WHERE credential_id = $1 AND tenant_id = $2 AND user_id = $3
ORDER BY created_at DESC
LIMIT 1
"""


async def get_or_create_active_import_run(
    *, credential_id: UUID, tenant_id: UUID, user_id: UUID
) -> tuple[UUID, bool]:
    """Return the active (queued/running) import run for the credential,
    creating one if none exists. Returns ``(import_run_id, created)``.

    Idempotent (C8): two concurrent callers race on the partial-unique index;
    the loser's INSERT does nothing and reads back the winner's row. If the
    active row went terminal between our INSERT and SELECT, we retry the INSERT
    once (the credential now has no active import).
    """
    pool = await get_asyncpg_pool()
    async with pool.acquire() as conn:
        inserted = await conn.fetchval(
            _INSERT_ACTIVE_RUN_SQL, tenant_id, user_id, credential_id
        )
        if inserted is not None:
            return inserted, True
        existing = await conn.fetchval(
            _SELECT_ACTIVE_RUN_ID_SQL, credential_id, tenant_id, user_id
        )
        if existing is not None:
            return existing, False
        # Rare: the conflicting active run reached a terminal state between the
        # INSERT and the SELECT. There is now no active run -> create one.
        inserted = await conn.fetchval(
            _INSERT_ACTIVE_RUN_SQL, tenant_id, user_id, credential_id
        )
        if inserted is not None:
            return inserted, True
        existing = await conn.fetchval(
            _SELECT_ACTIVE_RUN_ID_SQL, credential_id, tenant_id, user_id
        )
        if existing is None:
            raise RuntimeError(
                "get_or_create_active_import_run: could not create or find an "
                f"active import run for credential_id={credential_id}"
            )
        return existing, False


async def mark_running(*, import_run_id: UUID, tenant_id: UUID, user_id: UUID) -> bool:
    """Transition queued/running -> running and anchor ``started_at`` (idempotent
    — a replay keeps the original ``started_at`` via COALESCE).

    Returns ``True`` iff a row was actually claimed (state was queued/running).
    Returns ``False`` when the UPDATE matched 0 rows — the run is already TERMINAL
    (complete/failed/cancelled) or gone. A recovery re-dispatch (A2) can race the
    original workflow: if the original finished first, the recovery's mark_running
    matches nothing, and the caller MUST bail rather than run a redundant backfill
    on a terminal run (Codex P1)."""
    pool = await get_asyncpg_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(_MARK_RUNNING_SQL, import_run_id, tenant_id, user_id)
    # asyncpg returns the command tag, e.g. "UPDATE 1" / "UPDATE 0".
    return not result.strip().endswith(" 0")


async def set_import_total(
    *, import_run_id: UUID, tenant_id: UUID, user_id: UUID, total: int
) -> None:
    """Record the total note count once the first listing completes (C14: the
    FE shows indeterminate progress until this is set)."""
    pool = await get_asyncpg_pool()
    async with pool.acquire() as conn:
        await conn.execute(_SET_TOTAL_SQL, import_run_id, tenant_id, user_id, total)


async def complete_import_run(
    *, import_run_id: UUID, tenant_id: UUID, user_id: UUID
) -> None:
    pool = await get_asyncpg_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            _TERMINAL_SQL_TEMPLATE.format(terminal="complete"),
            import_run_id,
            tenant_id,
            user_id,
        )


async def fail_import_run(*, import_run_id: UUID, tenant_id: UUID, user_id: UUID) -> None:
    pool = await get_asyncpg_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            _TERMINAL_SQL_TEMPLATE.format(terminal="failed"),
            import_run_id,
            tenant_id,
            user_id,
        )


async def cancel_import_run(
    *, import_run_id: UUID, tenant_id: UUID, user_id: UUID
) -> None:
    """Mark an import cancelled (credential disconnected mid-import, C9)."""
    pool = await get_asyncpg_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            _TERMINAL_SQL_TEMPLATE.format(terminal="cancelled"),
            import_run_id,
            tenant_id,
            user_id,
        )


_CANCEL_ACTIVE_RUNS_SQL = """
UPDATE public.granola_import_runs
SET state = 'cancelled', finished_at = NOW(), updated_at = NOW()
WHERE credential_id = $1 AND tenant_id = $2 AND user_id = $3
  AND state IN ('queued', 'running')
"""


async def cancel_active_import_runs(
    *, credential_id: UUID, tenant_id: UUID, user_id: UUID
) -> None:
    """Cancel ALL active (queued/running) import runs for a credential.

    Called on the /connect RECONNECT path (Codex round-3 P1): the credential id
    is reused across disconnect→reconnect, so a queued/running import from a
    PRIOR lifecycle can linger. Without this, a forward reconnect could let a
    stale history import backfill against the user's forward choice, and a
    history reconnect would collide with the partial-unique and reuse the stale
    run. Cancelling them lets the new lifecycle start clean (a fresh import for
    history; none for forward). The per-credential advisory lock the reconnect
    holds keeps this from racing a live cycle."""
    pool = await get_asyncpg_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            _CANCEL_ACTIVE_RUNS_SQL, credential_id, tenant_id, user_id
        )


def _bucket_counts(rows: list) -> dict[str, int]:
    done = deferred = skipped = errors = 0
    for row in rows:
        status = row["status"]
        n = row["n"]
        if status == _DONE_STATUS:
            done += n
        elif status == _DEFERRED_STATUS:
            deferred += n
        elif status in _ERROR_STATUSES:
            errors += n
        elif isinstance(status, str) and status.startswith("skipped"):
            skipped += n
        # 'in_progress' + any unknown status: uncounted (in-flight / not terminal)
    return {"done": done, "deferred": deferred, "skipped": skipped, "errors": errors}


async def read_import_progress(
    *, import_run_id: UUID, tenant_id: UUID, user_id: UUID
) -> Optional[dict]:
    """DERIVED progress for one import run. Returns ``None`` if the run doesn't
    exist. Counts are 0 until ``started_at`` is set (queued)."""
    pool = await get_asyncpg_pool()
    async with pool.acquire() as conn:
        run = await conn.fetchrow(_SELECT_RUN_SQL, import_run_id, tenant_id, user_id)
        if run is None:
            return None
        started_at = run["started_at"]
        counts = {"done": 0, "deferred": 0, "skipped": 0, "errors": 0}
        if started_at is not None:
            rows = await conn.fetch(
                _COUNT_BY_STATUS_SQL, tenant_id, user_id, started_at
            )
            counts = _bucket_counts(rows)
        return {
            "state": run["state"],
            "total": run["total"],
            "started_at": started_at,
            "finished_at": run["finished_at"],
            **counts,
        }


async def latest_import_run(
    *, credential_id: UUID, tenant_id: UUID, user_id: UUID
) -> Optional[dict]:
    """Most-recent import run for a credential (for the /status import block).
    Returns ``None`` when the credential has never had an import."""
    pool = await get_asyncpg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_LATEST_RUN_SQL, credential_id, tenant_id, user_id)
        return dict(row) if row is not None else None
