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
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import asyncpg
from dbos import DBOS, Queue, SetWorkflowID

from services.asyncpg_pool import get_asyncpg_pool
from services.granola_ingestion.adapter import run_one_cycle
from services.granola_ingestion.import_runs import (
    cancel_import_run,
    complete_import_run,
    fail_import_run,
    get_or_create_active_import_run,
    latest_import_run,
    mark_running,
)
from services.vault import anchor_credential_watermark, get_granola_credential_for_user

logger = logging.getLogger(__name__)


# Mirror :data:`services.account_provisioning.workflow.APPROVAL_QUEUE`.
# concurrency=5 matches that workflow's cap; at the LOCKED-28 5-min
# cadence with O(10-100) initial design-partner users, peak
# in-flight workflows stay well under this. Future tuning via the
# Queue constructor if observed onboarding bursts demand it.
GRANOLA_POLL_QUEUE: Queue = Queue("granola-poll", concurrency=5)

# EQ-92/B3 (C3): a dedicated queue for the background history-import. A
# single import is a 33-83 min sequential backfill; running it on
# GRANOLA_POLL_QUEUE would occupy a poll slot for the whole time and starve
# other users' 5-min polls. Low concurrency (2) because an import is heavy
# (LLM clean per note) and design-partner connect volume is small; tune via
# the constructor if onboarding bursts demand it. The pool invariant in
# services.asyncpg_pool accounts for poll+import concurrency (= 2×(5+2)=14).
GRANOLA_IMPORT_QUEUE: Queue = Queue("granola-import", concurrency=2)

# A2 strand-recovery: a background import whose run_import_step returned
# 'lock_busy' (a poll briefly held the advisory lock) leaves its
# granola_import_runs row 'queued' with no live workflow. The cron tick +
# /status re-dispatch such runs with a FRESH workflow id. A run is treated as
# recoverable once it has been 'queued' longer than this window — a freshly
# dispatched import calls mark_running within ~1-2s, so a still-'queued' row
# well past this almost certainly never ran (crash before dispatch) or returned
# lock_busy. (A run legitimately waiting behind GRANOLA_IMPORT_QUEUE saturation
# — 3+ simultaneous connects — may get a harmless duplicate re-dispatch: the
# advisory lock serializes them and the lifecycle is keyed on import_run_id, so
# at most one extra lock_busy no-op results. Exact per-credential tracking is
# the #21b items-table follow-up.)
_IMPORT_RECOVERY_STALE_SECONDS = 300
_IMPORT_RECOVERY_LIMIT = 50

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


@dataclass(frozen=True)
class ImportResult:
    """Outcome of one :func:`run_import_step` (EQ-92 / B3).

    ``state`` is the terminal disposition the step applied to the
    ``granola_import_runs`` row:

    * ``"complete"`` — the backfill cycle finished cleanly.
    * ``"failed"`` — the cycle returned a credential-level error
      (``credential_error_code`` set) or raised; the row is marked failed.
    * ``"cancelled"`` — the credential was deactivated before/at/mid import
      (``cycle_aborted`` / not-active); the row is cancelled, not completed.
    * ``"lock_busy"`` — the per-credential advisory lock was held (a poll's
      brief A1-check window or another import attempt); the row is left
      ``queued`` and the cron-tick / ``/status`` recovery re-dispatches it with
      a fresh workflow id (A2). No lifecycle transition happened.

    No secrets — safe to persist via DBOS's step-output pickling.
    """

    state: str
    import_run_id: UUID
    reason: Optional[str] = None
    notes_processed: int = 0
    credential_error_code: Optional[str] = None


@dataclass(frozen=True)
class RecoverableImport:
    """A stale ``queued`` import run the cron/status recovery re-dispatches.

    Identity-only (no secrets) — :func:`run_import_step` re-loads the
    credential via vault when the recovery workflow runs.
    """

    import_run_id: UUID
    credential_id: UUID
    tenant_id: UUID
    user_id: UUID


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

    The retry loop wraps :func:`get_asyncpg_pool` too (Codex PR-#28 R2
    P2): the pool is created lazily on first call, so a transient
    failure during ``asyncpg.create_pool`` on cold start — or right
    after the DB drops all connections — is exactly the case this
    hardening targets. Keeping pool creation inside the loop means the
    3-attempt budget covers initial-connection failures, not just
    query failures on an already-built pool.
    """
    last_exc: Optional[BaseException] = None
    for attempt in range(1, _LIST_RETRY_ATTEMPTS + 1):
        try:
            pool = await get_asyncpg_pool()
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

            # A1: defer to a pending background import / forward anchor. A
            # freshly-connected B3 credential is "uninitialized" — last_polled_at
            # is still NULL. If a poll wins the advisory lock before the import
            # set the watermark it MUST NOT run the cycle (it would advance the
            # shared watermark past history the import hasn't ingested). Legacy
            # pre-B3 credentials have no import_scope (and an already-set
            # watermark), so this never trips for them.
            #
            # BUT defer only while the import is still RESPONSIBLE (forward:
            # awaiting its anchor; history: no run yet, or a queued/running run).
            # Once a history import is TERMINAL the import is done — proceed even
            # with a NULL watermark. (run_one_cycle can complete an import while
            # holding the watermark at NULL when a watched folder was not_found
            # — B2 preserves the shared watermark on a skip. Deferring forever
            # there would strand the credential; instead the poll proceeds,
            # re-lists from NULL (dedup-safe via external_integration_runs), and
            # advances the watermark once the folders recover.) — Codex P1.
            cfg = credential.config or {}
            import_scope = cfg.get("import_scope")
            if credential.last_polled_at is None and import_scope in ("history", "forward"):
                if import_scope == "forward":
                    logger.info(
                        "run_cycle_step: credential_id=%s uninitialized forward "
                        "(last_polled_at NULL); deferring until the forward anchor",
                        credential_id,
                    )
                    return PollResult(skipped=True, reason="awaiting_forward_anchor")
                latest = await latest_import_run(
                    credential_id=credential_id, tenant_id=tenant_id, user_id=user_id
                )
                if latest is None or latest["state"] in ("queued", "running"):
                    logger.info(
                        "run_cycle_step: credential_id=%s uninitialized history "
                        "(import %s); deferring poll to the import",
                        credential_id,
                        "not yet created" if latest is None else latest["state"],
                    )
                    return PollResult(skipped=True, reason="awaiting_import")
                # The import is terminal (complete/failed/cancelled) but the
                # watermark is still NULL (e.g. a not_found folder) — the import
                # is done; let the poll take over rather than defer forever.
                logger.info(
                    "run_cycle_step: credential_id=%s history import terminal "
                    "(state=%s) with NULL watermark; poll proceeds (re-lists, "
                    "dedup-safe)",
                    credential_id, latest["state"],
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


# ---------------------------------------------------------------------------
# Background history-import (EQ-92 / B3): step + workflow + dispatch/recovery
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=False)
async def run_import_step(
    *,
    credential_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    import_run_id: UUID,
) -> ImportResult:
    """Run the background history-import backfill for one credential.

    Mirrors :func:`run_cycle_step` but drives the ``granola_import_runs``
    lifecycle:

    * Acquires the SAME per-credential advisory lock the poll uses, so an import
      and a 5-min poll never run the credential concurrently (which would bypass
      the adapter's per-note idempotency anchor and double-publish). Session
      lock on a dedicated pooled connection, released in ``finally``.
    * **Lock-busy (A2):** if the lock is held (a poll's brief A1-check window or
      another import attempt) DO NOT strand or fail the run — leave it ``queued``
      and return ``state='lock_busy'``. The cron-tick / ``/status`` recovery
      re-dispatches it with a FRESH workflow id (the deterministic id already
      completed, so reusing it would DBOS-dedup to a no-op). With A1 the poll
      defers an uninitialized credential, so this window is tiny.
    * Loads the credential; if gone/inactive (disconnected before the import got
      the lock) → ``cancel_import_run`` (C9).
    * ``mark_running`` then ``run_one_cycle(..., import_run_id=...)`` with
      ``last_polled_at`` left NULL → a full backfill; the cycle records the
      import total after the first listing (A5).
    * Terminal disposition (A3): ``cycle_aborted`` / ``credential_skipped`` →
      ``cancel``; ``credential_error_code`` set → ``fail`` (run_one_cycle
      returns credential errors rather than raising, so we MUST check this or
      we'd mark an auth/folder failure complete); else → ``complete``. An
      unexpected raise → ``fail`` + re-raise (DBOS records the workflow failed).

    ``retries_allowed=False``: the adapter owns its per-note + consecutive-cycle
    budgets; DBOS step retries would inflate them. Secret confinement matches
    :func:`run_cycle_step` — the decrypted key never crosses a step boundary.
    """
    pool = await get_asyncpg_pool()
    lock_key = _advisory_lock_key(credential_id)

    async with pool.acquire() as lock_conn:
        got_lock = await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_key)
        if not got_lock:
            logger.info(
                "run_import_step: advisory lock held for credential_id=%s "
                "(poll or concurrent import); leaving import_run=%s queued for "
                "cron/status recovery",
                credential_id, import_run_id,
            )
            return ImportResult(
                state="lock_busy", import_run_id=import_run_id, reason="lock_busy"
            )
        try:
            credential = await get_granola_credential_for_user(
                tenant_id=tenant_id,
                user_id=user_id,
                caller_module=_CALLER_MODULE,
                pool=pool,
            )
            if credential is None or credential.status != "active":
                # Disconnected / flipped before the import got the lock → cancel,
                # not complete (C9). Also self-heals a stale 'queued' row whose
                # credential was since disconnected: the cron recovery
                # re-dispatches blindly, and this cancels it cleanly.
                logger.info(
                    "run_import_step: credential_id=%s not active at import start "
                    "(disconnected?); cancelling import_run=%s",
                    credential_id, import_run_id,
                )
                await cancel_import_run(
                    import_run_id=import_run_id, tenant_id=tenant_id, user_id=user_id
                )
                return ImportResult(
                    state="cancelled",
                    import_run_id=import_run_id,
                    reason="credential_not_active",
                )

            claimed = await mark_running(
                import_run_id=import_run_id, tenant_id=tenant_id, user_id=user_id
            )
            if not claimed:
                # The run is already TERMINAL — a recovery re-dispatch (A2) raced
                # the original workflow and the original finished first. Do NOT
                # run a redundant backfill (it would re-poll Granola, hold the
                # lock for the whole backfill, and set_import_total would mutate
                # the terminal row). Bail; the run is already done (Codex P1).
                logger.info(
                    "run_import_step: import_run=%s already terminal (mark_running "
                    "claimed nothing) for credential_id=%s; skipping redundant cycle",
                    import_run_id, credential_id,
                )
                return ImportResult(
                    state="superseded",
                    import_run_id=import_run_id,
                    reason="already_terminal",
                )
            try:
                cycle_result = await run_one_cycle(
                    credential=credential, pool=pool, import_run_id=import_run_id
                )
            except Exception:
                # A3 "on raise → fail": mark the run failed so the FE shows
                # 'failed' (not a stuck 'running'), then propagate so DBOS records
                # the workflow failed too.
                logger.exception(
                    "run_import_step: import cycle raised for credential_id=%s "
                    "import_run=%s; marking failed",
                    credential_id, import_run_id,
                )
                await fail_import_run(
                    import_run_id=import_run_id, tenant_id=tenant_id, user_id=user_id
                )
                raise

            if cycle_result.cycle_aborted or cycle_result.credential_skipped:
                await cancel_import_run(
                    import_run_id=import_run_id, tenant_id=tenant_id, user_id=user_id
                )
                return ImportResult(
                    state="cancelled",
                    import_run_id=import_run_id,
                    reason="cycle_aborted",
                    notes_processed=cycle_result.notes_processed,
                )
            if cycle_result.credential_error_code is not None:
                await fail_import_run(
                    import_run_id=import_run_id, tenant_id=tenant_id, user_id=user_id
                )
                return ImportResult(
                    state="failed",
                    import_run_id=import_run_id,
                    reason="credential_error",
                    notes_processed=cycle_result.notes_processed,
                    credential_error_code=cycle_result.credential_error_code,
                )
            await complete_import_run(
                import_run_id=import_run_id, tenant_id=tenant_id, user_id=user_id
            )
            return ImportResult(
                state="complete",
                import_run_id=import_run_id,
                notes_processed=cycle_result.notes_processed,
            )
        finally:
            try:
                await lock_conn.execute("SELECT pg_advisory_unlock($1)", lock_key)
            except Exception:  # noqa: BLE001 — unlock must not mask the result
                logger.exception(
                    "run_import_step: pg_advisory_unlock failed for "
                    "credential_id=%s (lock_key=%d). Lock may leak on this pooled "
                    "connection until recycled.",
                    credential_id, lock_key,
                )


@DBOS.workflow()
async def granola_import_one_credential(
    credential_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    import_run_id: UUID,
) -> ImportResult:
    """One DBOS workflow per background history-import.

    Pure orchestration; all I/O lives in :func:`run_import_step`. Inputs are
    positional to match the dispatch site (:func:`enqueue_import_workflow`).
    The dispatch ``workflow_id`` (deterministic at /connect, window-stamped on
    recovery) controls DBOS dedup — see the id helpers below.
    """
    logger.info(
        "granola_import start: credential_id=%s import_run=%s tenant_id=%s user_id=%s",
        credential_id, import_run_id, tenant_id, user_id,
    )
    result = await run_import_step(
        credential_id=credential_id,
        tenant_id=tenant_id,
        user_id=user_id,
        import_run_id=import_run_id,
    )
    logger.info(
        "granola_import done: credential_id=%s import_run=%s state=%s notes=%d err=%s",
        credential_id, import_run_id, result.state, result.notes_processed,
        result.credential_error_code,
    )
    return result


def import_workflow_id(credential_id: UUID, import_run_id: UUID) -> str:
    """Deterministic dispatch id for a credential's import run. /connect and the
    /status "no live import" recovery both use it, so a duplicate dispatch
    DBOS-dedups (enqueue-atomicity, C8)."""
    return f"granola_import_{credential_id}_{import_run_id}"


def import_recovery_workflow_id(
    credential_id: UUID, import_run_id: UUID, cycle_window: int
) -> str:
    """Window-stamped recovery id for re-dispatching a lock-busy strand (A2).

    Distinct from the deterministic id (whose workflow already completed /
    returned lock_busy, so DBOS would dedup the deterministic id to a no-op),
    and stable within a 5-min ``cycle_window`` so repeated /status polls in one
    window dedup to a single re-dispatch.
    """
    return f"granola_import_{credential_id}_{import_run_id}_r{cycle_window}"


async def enqueue_import_workflow(
    *,
    credential_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    import_run_id: UUID,
    workflow_id: str,
) -> None:
    """Dispatch the import workflow on :data:`GRANOLA_IMPORT_QUEUE` under
    ``workflow_id``. The single dispatch site for /connect, the /status
    recovery, and the cron backstop — keeps the enqueue shape + queue choice in
    one place. The enqueue is durable (DBOS persists the input before
    returning), so a handler crash after enqueue still leaves the workflow
    runnable.
    """
    with SetWorkflowID(workflow_id):
        await GRANOLA_IMPORT_QUEUE.enqueue_async(
            granola_import_one_credential,
            credential_id,
            tenant_id,
            user_id,
            import_run_id,
        )


# A2 recovery: a still-'queued' run past the staleness window has no live
# workflow making progress (never dispatched, or run_import_step returned
# lock_busy). Re-dispatch it with a fresh window-stamped id. Re-dispatching a
# run whose credential was since disconnected self-heals (run_import_step
# cancels it).
_LIST_RECOVERABLE_IMPORTS_SQL = """
SELECT id, credential_id, tenant_id, user_id
FROM public.granola_import_runs
WHERE state = 'queued'
  AND created_at < NOW() - ($1 * INTERVAL '1 second')
ORDER BY created_at ASC
LIMIT $2
"""


async def list_recoverable_import_runs(
    *,
    stale_seconds: int = _IMPORT_RECOVERY_STALE_SECONDS,
    limit: int = _IMPORT_RECOVERY_LIMIT,
) -> list[RecoverableImport]:
    """Find stale ``queued`` import runs to re-dispatch (A2 strand recovery).

    Plain async helper (like :func:`list_active_credentials`) — called from the
    cron handler / a request handler, OUTSIDE a workflow context. A single
    attempt: a transient failure just means recovery waits for the next 5-min
    cron tick (the caller treats a failure here as non-fatal so it never blocks
    the poll dispatch). The cross-credential scan is bounded by ``limit``; every
    downstream re-dispatch carries ``tenant_id`` explicitly (tenant isolation).
    """
    pool = await get_asyncpg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_LIST_RECOVERABLE_IMPORTS_SQL, stale_seconds, limit)
    return [
        RecoverableImport(
            import_run_id=row["id"],
            credential_id=row["credential_id"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
        )
        for row in rows
    ]


@dataclass(frozen=True)
class UninitializedCredential:
    """An ACTIVE credential with a NULL watermark that the A1 poll-defer guard
    skips, and which has no live initialization in flight:

    * ``history`` → no ``granola_import_runs`` row in queued/running/complete (the
      import was never created/dispatched — a crash, or the rare get_or_create
      failure swallowed at /connect). Stale-queued strands are handled separately
      by :func:`list_recoverable_import_runs`.
    * ``forward`` → never has an import run; a NULL watermark means the forward
      anchor failed.

    The cron tick re-initializes each (create+dispatch / anchor) so the credential
    starts polling — the headless backstop for the C8/A2/forward recovery the
    /status surface does instantly.
    """

    credential_id: UUID
    tenant_id: UUID
    user_id: UUID
    import_scope: str


# Active + NULL-watermark credentials needing headless re-initialization:
#
# * forward → ALWAYS qualifies when the watermark is NULL: it needs anchoring,
#   regardless of any import run from a PRIOR history lifecycle (a forward
#   reconnect of a once-history credential keeps the old completed run, which
#   must NOT mask the missing forward anchor — Codex P1).
# * history → only when there is NO live/complete run (the NOT EXISTS): the
#   no-row case (crash / swallowed get_or_create at /connect). A stale-QUEUED
#   strand is handled by list_recoverable_import_runs; a TERMINAL run that left
#   the watermark NULL (a not_found folder) is handled by run_cycle_step's A1
#   guard (the poll proceeds once the import is terminal) — not here.
_LIST_UNINITIALIZED_CREDS_SQL = """
SELECT uc.id, uc.tenant_id, uc.user_id, uc.config->>'import_scope' AS import_scope
FROM vault.user_credentials uc
WHERE uc.provider = 'granola'
  AND uc.status = 'active'
  AND uc.archived_at IS NULL
  AND uc.last_polled_at IS NULL
  AND uc.config->>'import_scope' IN ('history', 'forward')
  AND (
    uc.config->>'import_scope' = 'forward'
    OR NOT EXISTS (
      SELECT 1 FROM public.granola_import_runs r
      WHERE r.credential_id = uc.id
        AND r.state IN ('queued', 'running', 'complete')
    )
  )
ORDER BY uc.id ASC
LIMIT $1
"""


async def list_uninitialized_credentials(
    *, limit: int = _IMPORT_RECOVERY_LIMIT,
) -> list[UninitializedCredential]:
    """Active credentials stuck uninitialized (NULL watermark, no live init) —
    the headless recovery target for the cron (Codex P1: a crash/anchor-failure
    leaves no row for :func:`list_recoverable_import_runs` to find, and the poll
    defers them forever). Plain async helper; single attempt (a transient failure
    waits for the next 5-min tick). Bounded by ``limit``."""
    pool = await get_asyncpg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(_LIST_UNINITIALIZED_CREDS_SQL, limit)
    return [
        UninitializedCredential(
            credential_id=row["id"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            import_scope=row["import_scope"],
        )
        for row in rows
    ]


async def recover_uninitialized_credential(
    *,
    credential_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    import_scope: str,
) -> Optional[str]:
    """Re-initialize an uninitialized ACTIVE credential so the A1-deferred poll
    resumes. The shared recovery action for the /status, /connect-retry, and cron
    surfaces. Idempotent + safe to call repeatedly:

    * ``history`` → ensure an import run exists (``get_or_create_active_import_run``,
      idempotent) + dispatch it under the DETERMINISTIC id (a live dispatch
      DBOS-dedups). Headless equivalent of the /connect history path; heals a
      credential whose import was never created/dispatched. (A stale-QUEUED run is
      a different case — re-dispatched with a window-stamped id by the caller via
      :func:`list_recoverable_import_runs`; the deterministic id would DBOS-dedup.)
    * ``forward`` → anchor ``last_polled_at`` to NOW(). The C4 route-entry
      precision is lost on this rare recovery path (the original anchor failed),
      but a forward credential only needs "from roughly now on"; a meeting created
      in the brief anchor-failure window is an accepted edge.

    Returns a short action string (for logging) or None. The caller wraps this in
    try/except — best-effort recovery must not break /status, /connect, or the
    cron poll dispatch.
    """
    if import_scope == "forward":
        pool = await get_asyncpg_pool()
        await anchor_credential_watermark(
            pool=pool,
            credential_id=credential_id,
            tenant_id=tenant_id,
            user_id=user_id,
            ts=datetime.now(timezone.utc),
            caller_module=_CALLER_MODULE,
        )
        return "forward_anchored"

    run_id, created = await get_or_create_active_import_run(
        credential_id=credential_id, tenant_id=tenant_id, user_id=user_id
    )
    await enqueue_import_workflow(
        credential_id=credential_id,
        tenant_id=tenant_id,
        user_id=user_id,
        import_run_id=run_id,
        workflow_id=import_workflow_id(credential_id, run_id),
    )
    return "history_created" if created else "history_dispatched"
