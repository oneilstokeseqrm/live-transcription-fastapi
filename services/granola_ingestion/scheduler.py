"""Phase 2e — DBOS workflow + Queue + steps that drive the Granola adapter.

Wires the inert Phase 2d :func:`services.granola_ingestion.adapter.run_one_cycle`
to a 5-minute Railway-cron + DBOS-workflow cadence per LOCKED-28 +
LOCKED-39.

**Architecture (LOCKED-39 + DBOS arch doc §6.2 / §768):**

* Railway cron POSTs every 5 min to ``/internal/granola/cron-tick``
  (:mod:`routers.granola_cron`).
* The cron handler calls :func:`list_active_credentials` to find
  all active credentials, then for each row enqueues
  :func:`granola_poll_one_credential` via
  :data:`GRANOLA_POLL_QUEUE` + ``SetWorkflowID(f"granola_poll_{credential_id}_{cycle_window}")``.
* :func:`granola_poll_one_credential` is the ``@DBOS.workflow``. It is
  pure orchestration: it calls :func:`run_cycle_step` which loads the
  credential via the vault accessor and invokes the existing
  :func:`run_one_cycle` adapter.

**Why ``@DBOS.scheduled`` is NOT used (LOCKED-39):** the
``@DBOS.scheduled`` decorator's Python binding is deprecated per the
repo's DBOS architecture doc (``docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md``
§9.1) — the supported pattern is external cron + explicit
``SetWorkflowID``. The cron handler running outside the workflow scope
also keeps the dispatch decision auditable (we log the cycle window +
the enqueued count per tick).

**Why ``workflow_id = f"granola_poll_{credential_id}_{cycle_window}"``:**
DBOS deduplicates workflow starts by id. Same credential + same 5-min
window → same id → second dispatch is a no-op. This catches the case
where a cycle takes longer than 5 min and the next cron tick would
otherwise start a second concurrent run. Successive windows produce
distinct ids → fresh runs each window (per LOCKED-28 cadence).

**Why ``run_one_cycle`` is wrapped in a single ``@DBOS.step``:** the
adapter is internally idempotent at every boundary (the in_progress
pre-write, the composite UNIQUE on ``external_integration_runs``, the
cycle-start watermark). Splitting it into per-note steps would force
DBOS to checkpoint each note's state into ``dbos.operation_outputs``
— costly, and the adapter's own SQL-level idempotency is already the
load-bearing dedup mechanism. One step = one retry unit; the
adapter's per-note retry budget handles the rest.

**Why the credential is loaded INSIDE the step (not as a step's
return value):** ``@DBOS.step`` persists each step's return value
into ``dbos.operation_outputs`` via pickle for replay. If we returned
a :class:`~services.vault.GranolaCredential` from a step, the
decrypted ``api_key`` cleartext would land in that table —
defeating the encryption-at-rest model. Instead the step loads the
credential locally, uses it to call :func:`run_one_cycle`, and lets
it fall out of scope on return. The step returns only
:class:`PollResult` (no secrets).

**Until Phase 2f adds ``/connect``:** ``vault.user_credentials`` is
empty; ``list_active_credentials`` returns ``[]``; no workflows
run. The scheduler ships dormant but proves the dispatch path works
end-to-end the day Phase 2f deploys.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import asyncpg
from dbos import DBOS, Queue

from services.asyncpg_pool import get_asyncpg_pool
from services.granola_ingestion.adapter import run_one_cycle
from services.vault import get_granola_credential_for_user

logger = logging.getLogger(__name__)


# Mirror :data:`services.account_provisioning.workflow.APPROVAL_QUEUE`.
# concurrency=5 matches that workflow's cap; at the LOCKED-28 5-min
# cadence with O(10-100) initial design-partner users, peak
# in-flight workflows stay well under this. Future tuning via the
# Queue constructor if observed onboarding bursts demand it.
GRANOLA_POLL_QUEUE: Queue = Queue("granola-poll", concurrency=5)

# Caller-module identifier passed to the vault ALLOWLIST gate
# (LOCKED-42). Must match an entry in
# :data:`services.vault.user_credentials.ALLOWLIST` exactly —
# the allowlist already includes ``"services.granola_ingestion.scheduler"``.
_CALLER_MODULE = "services.granola_ingestion.scheduler"

_PROVIDER = "granola"

# Explicit retry budget for list_active_credentials (Codex PR-#28 R1 P2).
# The cron handler calls it OUTSIDE a workflow context, so a @DBOS.step
# decorator's retry semantics would not fire — retries must be explicit.
_LIST_RETRY_ATTEMPTS = 3
_LIST_RETRY_BASE_DELAY_S = 0.5


@dataclass(frozen=True)
class CredentialMetadata:
    """Identity triple returned by :func:`list_active_credentials`.

    Carries only ``(id, tenant_id, user_id)`` — no encrypted key
    material, no folder config. The workflow uses this triple as
    workflow input; :func:`run_cycle_step` decrypts the actual
    credential via vault when it runs.
    """

    id: UUID
    tenant_id: UUID
    user_id: UUID


@dataclass(frozen=True)
class PollResult:
    """Per-workflow result for the cron handler + dashboards.

    ``skipped`` is True when the workflow short-circuited because the
    credential wasn't active at workflow-start time (concurrent revoke
    between cron-tick and step). ``reason`` carries the cause for
    diagnostics. On a real run, ``notes_processed`` /
    ``deferred_reprocessed`` reflect the adapter's
    :class:`~services.granola_ingestion.adapter.CycleResult`.

    ``credential_error_code`` is set when the cycle ended in a
    credential-level error (auth failed, folder deleted, sustained
    5xx) so observability can alert.
    """

    skipped: bool = False
    reason: Optional[str] = None
    notes_processed: int = 0
    deferred_reprocessed: int = 0
    credential_error_code: Optional[str] = None


# ---------------------------------------------------------------------------
# Credential listing (plain async helper — called from the cron handler)
# ---------------------------------------------------------------------------


_LIST_ACTIVE_CREDENTIALS_SQL = """
SELECT id, tenant_id, user_id
FROM vault.user_credentials
WHERE provider = $1
  AND status = 'active'
  AND archived_at IS NULL
ORDER BY id ASC
"""


async def list_active_credentials() -> list[CredentialMetadata]:
    """List all active Granola credentials across all tenants.

    Plain async helper — NOT a ``@DBOS.step``. The cron handler
    (:mod:`routers.granola_cron`) calls this directly, OUTSIDE any
    workflow context, where a ``@DBOS.step`` decorator's retry
    semantics would not fire (the decorator degrades to a passthrough
    coroutine off-workflow, so a transient ``asyncpg`` failure would
    abort the whole tick on the first exception). Codex PR-#28 R1 P2.

    Retries are therefore explicit: a transient ``asyncpg`` /
    connection error is retried up to :data:`_LIST_RETRY_ATTEMPTS`
    times with linear backoff so a connection blip doesn't skip an
    entire 5-min poll interval. If every attempt fails, the exception
    propagates → the cron tick returns 5xx → the next tick (5 min)
    retries naturally.

    The single cross-tenant query in the scheduler — necessary because
    the cron handler must enumerate every active credential to
    dispatch per-credential workflows. Every downstream call carries
    ``tenant_id`` explicitly (the vault accessor + the adapter's
    tenant-scoped SQL) so tenant isolation is preserved from this
    point onward.

    Returns :class:`CredentialMetadata` triples; encrypted key material
    is NOT loaded here. :func:`run_cycle_step` decrypts on demand
    inside its own step scope so the cleartext never crosses a DBOS
    step return boundary.
    """
    pool = await get_asyncpg_pool()
    last_exc: Optional[BaseException] = None
    for attempt in range(1, _LIST_RETRY_ATTEMPTS + 1):
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(_LIST_ACTIVE_CREDENTIALS_SQL, _PROVIDER)
            return [
                CredentialMetadata(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    user_id=row["user_id"],
                )
                for row in rows
            ]
        except (asyncpg.PostgresError, OSError) as exc:
            last_exc = exc
            if attempt < _LIST_RETRY_ATTEMPTS:
                logger.warning(
                    "list_active_credentials attempt %d/%d failed: %r; retrying",
                    attempt, _LIST_RETRY_ATTEMPTS, exc,
                )
                await asyncio.sleep(_LIST_RETRY_BASE_DELAY_S * attempt)
    assert last_exc is not None  # loop only exits via return or exhausted attempts
    logger.error(
        "list_active_credentials exhausted %d attempts; cron tick aborts, "
        "next 5-min tick retries. Last error: %r",
        _LIST_RETRY_ATTEMPTS, last_exc,
    )
    raise last_exc


# ---------------------------------------------------------------------------
# Per-credential cycle step (DBOS step — called from inside the workflow)
# ---------------------------------------------------------------------------


def _advisory_lock_key(credential_id: UUID) -> int:
    """Derive a stable signed int64 lock key for ``pg_try_advisory_lock``.

    Postgres advisory locks key on a ``bigint``. We take the UUID's
    first 8 bytes as a signed big-endian int — deterministic per
    credential, with negligible collision probability across the
    credential population (a 64-bit space drawn from a v4 UUID's random
    bits). Two credentials colliding would merely serialize their
    cycles against each other — a throughput nit, never a correctness
    bug.
    """
    return int.from_bytes(credential_id.bytes[:8], "big", signed=True)


@DBOS.step(retries_allowed=False)
async def run_cycle_step(
    *,
    credential_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
) -> PollResult:
    """Load credential via vault and drive one adapter cycle.

    **Per-credential serialization (Codex PR-#28 R1 P1).** The
    dispatch ``workflow_id`` is ``granola_poll_{credential_id}_{cycle_window}``
    — it dedups duplicate dispatches WITHIN a 5-min window but NOT
    across windows. A cycle that overruns 5 min would otherwise let the
    next cron tick (a new ``cycle_window`` → a new ``workflow_id`` →
    no DBOS dedup) start a SECOND concurrent cycle for the same
    credential. Two overlapping cycles race in
    :func:`adapter.process_note`: both can read "no
    ``external_integration_runs`` row" for the same new note BEFORE
    either writes the ``in_progress`` anchor, mint different
    ``eq_interaction_id`` values, and double-publish to Lane 1 / Lane 2
    — and downstream consumers do NOT dedup because the ids differ.
    The adapter's idempotency anchor only protects SEQUENTIAL retries
    (crash + replay), not CONCURRENT cycles.

    A session-scoped Postgres advisory lock keyed on ``credential_id``
    serializes cycles: if a prior cycle still holds the lock, this
    workflow exits as ``skipped`` and the next cron tick retries. The
    lock is held on a dedicated pooled connection for the whole cycle
    and released in ``finally`` (a process crash ends the session and
    auto-releases it).

    **Secret confinement.** The single step body keeps the decrypted
    ``api_key`` confined to local scope — it never appears in a step
    return value, so it can't be persisted to
    ``dbos.operation_outputs`` via DBOS's pickle replay machinery
    (which would defeat encryption-at-rest by co-locating ciphertext +
    cleartext in the same Postgres).

    ``retries_allowed=False``: the adapter has its own per-note retry
    budget (5 attempts → ``FAILED_PERMANENT``) and its own
    consecutive-failures budget (3 cycles → ``credential.status=error``).
    Letting DBOS retry the cycle on transient asyncpg errors would
    inflate both budgets simultaneously. Workflow-level resumption on
    process crash still happens (DBOS resumes from the workflow's last
    completed step); just no per-step exception retries.

    ``credential_id`` is unused at the vault-SQL level (the accessor
    keys on ``(tenant_id, user_id, provider, status=active)``) but it
    IS the advisory-lock key + the dispatch ``workflow_id`` component,
    and it anchors the log lines to what the cron handler dispatched.
    """
    pool = await get_asyncpg_pool()
    lock_key = _advisory_lock_key(credential_id)

    async with pool.acquire() as lock_conn:
        got_lock = await lock_conn.fetchval(
            "SELECT pg_try_advisory_lock($1)", lock_key
        )
        if not got_lock:
            logger.info(
                "run_cycle_step: credential_id=%s cycle already running "
                "(advisory lock held by a prior overlapping cycle); "
                "skipping this window — next tick retries",
                credential_id,
            )
            return PollResult(skipped=True, reason="cycle_already_running")

        try:
            credential = await get_granola_credential_for_user(
                tenant_id=tenant_id,
                user_id=user_id,
                caller_module=_CALLER_MODULE,
                pool=pool,
            )
            if credential is None:
                # No active credential row. Happens if /disconnect
                # (Phase 2f soft-delete) ran between the cron-tick's
                # list_active_credentials and this workflow's actual
                # execution. Not an error; the next cron tick will
                # simply not dispatch this credential_id.
                logger.info(
                    "run_cycle_step: no active credential for tenant=%s "
                    "user=%s (dispatched credential_id=%s); short-circuiting",
                    tenant_id, user_id, credential_id,
                )
                return PollResult(
                    skipped=True, reason="credential_not_active_or_archived"
                )

            # The vault accessor's WHERE clause already filters
            # status='active' AND archived_at IS NULL, but a future
            # relaxation of that filter would let this guard kick in.
            # Defensive but correct now.
            if credential.status != "active":
                return PollResult(
                    skipped=True, reason=f"credential_status={credential.status!r}"
                )

            cycle_result = await run_one_cycle(credential=credential, pool=pool)

            if cycle_result.credential_skipped:
                # The adapter's own early-out (status check inside
                # run_one_cycle). In practice dead-coded by the status
                # check above, but the adapter is the source of truth
                # for cycle state — surface its decision.
                return PollResult(skipped=True, reason="cycle_skipped_at_adapter")

            return PollResult(
                notes_processed=cycle_result.notes_processed,
                deferred_reprocessed=cycle_result.deferred_reprocessed,
                credential_error_code=cycle_result.credential_error_code,
            )
        finally:
            # Release the advisory lock BEFORE the connection returns to
            # the pool. Session-scoped advisory locks persist on the
            # physical connection across pool checkout/checkin, so an
            # explicit unlock is required — without it the lock would
            # leak on a pooled connection and block this credential's
            # future cycles until that connection is recycled. A unlock
            # failure (rare) is logged loudly; a process crash ends the
            # session and auto-releases the lock.
            try:
                await lock_conn.execute("SELECT pg_advisory_unlock($1)", lock_key)
            except Exception:  # noqa: BLE001 — unlock must not mask the cycle result
                logger.exception(
                    "run_cycle_step: pg_advisory_unlock failed for "
                    "credential_id=%s (lock_key=%d). Lock may leak on this "
                    "pooled connection until recycled; next cycle for this "
                    "credential could skip as 'cycle_already_running'.",
                    credential_id, lock_key,
                )


# ---------------------------------------------------------------------------
# DBOS workflow (pure orchestration)
# ---------------------------------------------------------------------------


@DBOS.workflow()
async def granola_poll_one_credential(
    credential_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
) -> PollResult:
    """One DBOS workflow per (credential, 5-min cycle window).

    Pure orchestration. All I/O lives in :func:`run_cycle_step`.

    The workflow_id is set at the cron-handler call site as
    ``f"granola_poll_{credential_id}_{cycle_window}"``. Same
    credential_id + same cycle_window → same workflow_id → DBOS
    dedups. A cycle that overruns the 5-min window is fine — the
    in-flight workflow keeps running; the next tick's dispatch with a
    different ``cycle_window`` produces a different ``workflow_id``
    that starts a fresh workflow (the prior workflow's idempotency
    anchors in ``external_integration_runs`` keep the two from
    duplicating downstream work).

    Returns :class:`PollResult` so the cron handler / dashboards /
    DBOS state inspector can observe per-workflow outcomes.

    Workflow inputs are positional (not keyword-only) because the
    dispatch site uses
    ``await GRANOLA_POLL_QUEUE.enqueue_async(granola_poll_one_credential,
    cred.id, cred.tenant_id, cred.user_id)`` — positional matches
    the dataclass field order of :class:`CredentialMetadata`.
    """
    logger.info(
        "granola_poll start: credential_id=%s tenant_id=%s user_id=%s",
        credential_id, tenant_id, user_id,
    )
    result = await run_cycle_step(
        credential_id=credential_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    logger.info(
        "granola_poll done: credential_id=%s skipped=%s reason=%s "
        "notes=%d deferred=%d err=%s",
        credential_id,
        result.skipped,
        result.reason,
        result.notes_processed,
        result.deferred_reprocessed,
        result.credential_error_code,
    )
    return result
