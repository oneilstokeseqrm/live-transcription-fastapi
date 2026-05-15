"""DBOS substrate initialization and FastAPI lifespan integration.

Phase 1.5 (Contact Quality Initiative) uses DBOS for durable execution of
the account-provisioning workflow. See
``docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md``.

The deploy is multi-replica-ready by configuration: ``executor_id``
derives from Railway's auto-injected ``RAILWAY_REPLICA_ID``. V1 ships on
one Railway replica with ``uvicorn --workers 1``; scaling out is gated on
shipping the orphan-workflow detector per
``docs/superpowers/specs/2026-05-15-dbos-scaling-decisions.md``.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from dbos import DBOS, DBOSConfig
from fastapi import FastAPI

logger = logging.getLogger(__name__)


def build_dbos_config() -> DBOSConfig:
    """Construct ``DBOSConfig`` from environment.

    ``DBOS_SYSTEM_DATABASE_URL`` is REQUIRED. It must be a direct
    (non-pooler) Postgres connection in production; Neon's pooler
    interferes with DBOS workflow state and locking. Tests that exercise
    lifespan must set this env var to a test-safe connection (Phase 1.5
    uses production with test-tenant scoping; M3 tests will set it
    explicitly).

    If the env var is unset, DBOS would silently fall back to a local
    SQLite file that gets blown away on every container restart —
    defeating the durability guarantee Phase 1.5 is buying. Fail fast
    with a clear error instead.

    ``RAILWAY_REPLICA_ID`` is auto-injected by Railway. Locally it is
    unset and DBOS picks its own executor identity. When ``None`` is
    passed, DBOS's config translator skips the field via a
    ``"executor_id" in config and config["executor_id"] is not None``
    guard (see ``dbos/_dbos.py:445``).
    """
    system_database_url = os.environ.get("DBOS_SYSTEM_DATABASE_URL")
    if not system_database_url:
        raise RuntimeError(
            "DBOS_SYSTEM_DATABASE_URL is required for the DBOS workflow "
            "runtime but is unset. In production this env var must point "
            "at a direct (non-pooler) Postgres connection — see Railway "
            "service config for live-transcription-fastapi. For tests, "
            "set the variable in your fixture or environment before "
            "importing the app."
        )
    return DBOSConfig(
        name="live-transcription-fastapi",
        system_database_url=system_database_url,
        executor_id=os.environ.get("RAILWAY_REPLICA_ID"),
        # Phase 1.5 does not need the admin server; revisit when adding
        # operator tooling that benefits from it.
        run_admin_server=False,
    )


@asynccontextmanager
async def dbos_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Launch DBOS at app startup and tear it down at shutdown.

    ``DBOS.launch()`` / ``DBOS.destroy()`` are synchronous in DBOS v2.x
    (verified against ``dbos==2.22.0`` source at ``dbos/_dbos.py:519`` and
    ``:362`` on 2026-05-15). Calling them inside an async lifespan is the
    documented pattern — they are quick startup/shutdown bookkeeping with
    no blocking I/O concerns at the event-loop level.

    No workflows are defined yet at this milestone — DBOS launches but
    does nothing. M3 introduces the account-provisioning workflow.
    """
    config = build_dbos_config()
    DBOS(config=config)
    DBOS.launch()
    logger.info(
        "DBOS launched (executor_id=%s, system_db=%s)",
        os.environ.get("RAILWAY_REPLICA_ID") or "<unset; DBOS default>",
        "configured"
        if os.environ.get("DBOS_SYSTEM_DATABASE_URL")
        else "sqlite-fallback",
    )
    try:
        yield
    finally:
        DBOS.destroy()
        logger.info("DBOS destroyed")
