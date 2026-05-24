"""Internal cron endpoint that dispatches Granola poll workflows.

Phase 2e per LOCKED-28 (5-min cadence) + LOCKED-39 (external Railway
cron + DBOS Queue with explicit SetWorkflowID — NOT @DBOS.scheduled).

Railway cron POSTs to ``/internal/granola/cron-tick`` every 5 min,
authenticated by the ``X-Internal-Cron-Secret`` header. The handler:

1. Validates the cron secret (constant-time compare via
   :func:`secrets.compare_digest`).
2. Computes the current cycle_window (= unix_minute // 5) so the
   dispatched ``workflow_id`` values dedup across overlapping cycles.
3. Calls
   :func:`services.granola_ingestion.scheduler.list_active_credentials_step`
   to discover active credentials.
4. For each credential, dispatches
   :func:`~services.granola_ingestion.scheduler.granola_poll_one_credential`
   via :data:`~services.granola_ingestion.scheduler.GRANOLA_POLL_QUEUE`
   with ``SetWorkflowID(f"granola_poll_{credential_id}_{cycle_window}")``.
5. Returns ``{"enqueued": N, "cycle_window": <int>}``.

**Auth: defense-in-depth**, despite Railway's internal-network
assumption. The cron endpoint is for Railway's scheduler service, NOT
for end users — no JWT, no per-tenant scoping. If
``INTERNAL_CRON_SECRET`` is unset the endpoint returns 503 so an
operator misconfiguration is loud (rather than silently allowing
unauthenticated dispatches if a future code change accidentally
removed the check).

**Until Phase 2f adds ``/connect``:** ``vault.user_credentials`` is
empty; step 3 returns ``[]``; step 4 is a no-op; the endpoint returns
``{"enqueued": 0, "cycle_window": ...}``. The scheduler ships dormant
but proves the dispatch path works end-to-end the day Phase 2f lands.

**Operator setup** (post-merge, user-authorized):

* Set Railway env var ``INTERNAL_CRON_SECRET`` to a random 32-byte hex
  value (e.g. ``python -c "import secrets; print(secrets.token_hex(32))"``).
* Register a Railway cron job that POSTs to
  ``/internal/granola/cron-tick`` every 5 min with the
  ``X-Internal-Cron-Secret: <secret>`` header.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone

from dbos import SetWorkflowID
from fastapi import APIRouter, Depends, Header, HTTPException, status

from services.granola_ingestion.scheduler import (
    GRANOLA_POLL_QUEUE,
    granola_poll_one_credential,
    list_active_credentials_step,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/granola", tags=["internal", "granola"])


_CRON_SECRET_ENV = "INTERNAL_CRON_SECRET"


async def verify_internal_cron_secret(
    x_internal_cron_secret: str | None = Header(
        default=None, alias="X-Internal-Cron-Secret"
    ),
) -> None:
    """FastAPI dependency that authorizes Railway's cron POSTs.

    Behavior:

    * **503** if the server isn't configured (env var unset). Surfaces
      operator misconfiguration loudly rather than silently rejecting
      every request — operators see a Railway-side cron failure they
      can diagnose.
    * **401** if the caller sends the wrong / missing secret.
    * **Constant-time compare** via :func:`secrets.compare_digest`
      defeats timing oracles that could leak the expected secret one
      byte at a time.

    Raises :class:`fastapi.HTTPException`; FastAPI converts to the
    appropriate HTTP response automatically.
    """
    expected = os.environ.get(_CRON_SECRET_ENV)
    if not expected:
        logger.error(
            "%s env var unset; rejecting cron call. Operator must set "
            "INTERNAL_CRON_SECRET in Railway env config before Phase 2e "
            "scheduler runs.",
            _CRON_SECRET_ENV,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="cron auth not configured",
        )
    if not x_internal_cron_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-Internal-Cron-Secret header",
        )
    if not secrets.compare_digest(x_internal_cron_secret, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid X-Internal-Cron-Secret",
        )


def _current_cycle_window() -> int:
    """Compute the 5-min cycle window index for the current UTC time.

    The window is ``floor(unix_minute / 5)`` — a stable integer that
    increments every 5 minutes at clock-aligned boundaries (e.g.
    ``cycle_window`` flips at :00, :05, :10, …). Two cron ticks
    landing in the same window produce the same ``workflow_id`` per
    credential → DBOS dedups; a slow cycle that overruns the window
    boundary yields a fresh ``workflow_id`` on the next tick.

    The trade-off is clock-edge sensitivity: a tick at :04:59.7 vs
    :05:00.1 lands in different windows even though they're 0.4s
    apart. Railway's cron-tick precision is well under 1s, so this is
    fine in practice; the only concern would be a manual operator
    re-triggering the endpoint at a window boundary, in which case
    the second tick correctly produces a fresh run.
    """
    utc_now = datetime.now(timezone.utc)
    utc_minute = int(utc_now.timestamp() // 60)
    return utc_minute // 5


@router.post("/cron-tick", status_code=status.HTTP_202_ACCEPTED)
async def granola_cron_tick(
    _: None = Depends(verify_internal_cron_secret),
) -> dict:
    """Railway cron POSTs here every 5 min; dispatch one workflow per active credential.

    The cron job runs the dispatch loop synchronously inside the
    request handler (per credential: ``with SetWorkflowID(...): await
    GRANOLA_POLL_QUEUE.enqueue_async(...)``). The enqueue itself is
    durable — DBOS persists the workflow input to its system
    database before returning, so a handler crash AFTER enqueue but
    BEFORE returning a 202 still leaves the workflow runnable.

    Pre-Phase-2f: returns ``{"enqueued": 0, ...}``. Post-Phase-2f:
    returns ``{"enqueued": N, ...}`` where N is the count of
    dispatched workflows. Idempotent at the workflow-id layer: a
    duplicate tick within the same 5-min window produces the same
    workflow_ids and DBOS dedups (the second
    ``enqueue_async`` returns the existing handle).
    """
    credentials = await list_active_credentials_step()
    cycle_window = _current_cycle_window()
    enqueued = 0

    for credential in credentials:
        workflow_id = f"granola_poll_{credential.id}_{cycle_window}"
        with SetWorkflowID(workflow_id):
            await GRANOLA_POLL_QUEUE.enqueue_async(
                granola_poll_one_credential,
                credential.id,
                credential.tenant_id,
                credential.user_id,
            )
        enqueued += 1

    logger.info(
        "granola cron tick: enqueued=%d cycle_window=%d active_credentials=%d",
        enqueued, cycle_window, len(credentials),
    )
    return {"enqueued": enqueued, "cycle_window": cycle_window}
