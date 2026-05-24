"""Lazy-singleton asyncpg pool for modules that need raw connections.

The vault accessors (:mod:`services.vault.user_credentials`) and the
Granola adapter (:mod:`services.granola_ingestion.adapter`) both take an
``asyncpg.Pool`` as an explicit kwarg. Prior to Phase 2e no caller
constructed one — nothing in the repo invoked ``run_one_cycle`` at
runtime. Phase 2e is the first runtime caller of the full chain, so it
owns the pool's lifecycle.

The pool is created lazily on first call to :func:`get_asyncpg_pool` so
import-time has no DB cost and tests don't need to set up Neon to
import the module. :func:`close_asyncpg_pool` is wired into the FastAPI
lifespan so connections drain on graceful shutdown.

DSN handling: the SQLAlchemy engine in :mod:`services.database` uses
``DATABASE_URL`` with a ``postgresql+asyncpg`` driver prefix and accepts
SQLAlchemy-style query params (``sslmode``, ``channel_binding``,
``options``). asyncpg's native ``create_pool`` doesn't recognize the
driver prefix or those params, so we strip them here and promote
``sslmode=require`` to an explicit SSL context — mirroring the
SQLAlchemy engine's setup in :func:`services.database.get_database_url`.

Sizing: the pool defaults to ``min_size=1, max_size=10``. Phase 2e's
peak load is one workflow per active credential per 5 min, capped by
the DBOS Queue at 5 concurrent workflows (per LOCKED-39 +
:data:`services.granola_ingestion.scheduler.GRANOLA_POLL_QUEUE`'s
``concurrency=5``). Each concurrent cycle holds ONE connection for its
whole duration — the per-credential advisory lock that serializes
overlapping cycles (Codex PR-#28 R1 P1) — PLUS up to one transient
connection at a time for the ``external_integration_runs`` / credential
SQL (the adapter's other DB access goes through the separate SQLAlchemy
engine, not this pool). Peak is therefore ``2 × concurrency`` = 10. The
invariant ``max_size >= 2 × GRANOLA_POLL_QUEUE.concurrency`` MUST hold or
the held lock connections can starve the transient acquires and
deadlock the cron tick. 10 stays well under Neon's per-database
connection limit. Future tuning via env var if observed traffic
demands it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import asyncpg

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None
_lock: asyncio.Lock = asyncio.Lock()

_DEFAULT_MIN_SIZE = 1
# Invariant: must be >= 2 × GRANOLA_POLL_QUEUE.concurrency (see module
# docstring) — each concurrent cycle holds 1 advisory-lock connection
# for its whole duration plus up to 1 transient connection for SQL.
_DEFAULT_MAX_SIZE = 10


def _resolve_dsn_and_kwargs() -> tuple[str, dict]:
    """Translate ``DATABASE_URL`` into an asyncpg-acceptable DSN + connect kwargs.

    Steps:

    1. Strip the SQLAlchemy ``+asyncpg`` driver suffix from the scheme.
    2. Remove SQLAlchemy/libpq query params asyncpg doesn't understand
       (``sslmode``, ``channel_binding``, ``options``).
    3. Promote ``sslmode=require`` / ``verify-ca`` / ``verify-full`` to
       an explicit SSL context kwarg (asyncpg's native form). Neon
       presents a self-signed cert chain so ``check_hostname=False`` +
       ``verify_mode=CERT_NONE`` matches the existing SQLAlchemy
       engine's setup (services/database.py).
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is required for the asyncpg pool but is unset. "
            "Phase 2e's scheduler + vault accessors need this set in "
            "Railway env config."
        )
    parsed = urlparse(url)
    scheme = parsed.scheme
    if scheme == "postgresql+asyncpg":
        scheme = "postgresql"
    elif scheme not in ("postgresql", "postgres"):
        # Defensive: any other scheme prefix is a misconfiguration we
        # surface immediately rather than letting asyncpg fail mid-cycle.
        raise RuntimeError(
            f"DATABASE_URL has unexpected scheme {parsed.scheme!r}; expected "
            f"'postgresql' or 'postgresql+asyncpg'"
        )

    query_params = parse_qs(parsed.query)
    ssl_required = False
    if "sslmode" in query_params:
        sslmode = query_params["sslmode"][0]
        if sslmode in ("require", "verify-ca", "verify-full"):
            ssl_required = True

    incompatible = ("sslmode", "channel_binding", "options")
    filtered = {k: v for k, v in query_params.items() if k not in incompatible}
    new_query = urlencode(filtered, doseq=True) if filtered else ""

    dsn = urlunparse(
        (scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )

    kwargs: dict = {}
    if ssl_required:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl"] = ssl_ctx

    return dsn, kwargs


async def get_asyncpg_pool() -> asyncpg.Pool:
    """Return the process-wide asyncpg pool, creating it on first call.

    Concurrent first-callers race-safely through :data:`_lock`; only one
    pool is created. Subsequent calls return the cached pool without
    locking (the ``is not None`` check before the lock is the fast path).
    """
    global _pool
    if _pool is not None:
        return _pool
    async with _lock:
        if _pool is None:
            dsn, kwargs = _resolve_dsn_and_kwargs()
            logger.info(
                "asyncpg_pool: creating pool (min=%d max=%d, ssl=%s)",
                _DEFAULT_MIN_SIZE, _DEFAULT_MAX_SIZE, "yes" if "ssl" in kwargs else "no",
            )
            _pool = await asyncpg.create_pool(
                dsn,
                min_size=_DEFAULT_MIN_SIZE,
                max_size=_DEFAULT_MAX_SIZE,
                **kwargs,
            )
    return _pool


async def close_asyncpg_pool() -> None:
    """Close the pool, returning all connections. Idempotent.

    Wired into ``main.py``'s lifespan ``finally`` block so a graceful
    shutdown drains connections before the process exits. Safe to call
    when the pool was never created (a startup that never hit a
    credential lookup).
    """
    global _pool
    if _pool is not None:
        logger.info("asyncpg_pool: closing pool")
        try:
            await _pool.close()
        finally:
            _pool = None


def _reset_for_tests() -> None:
    """Test-only escape hatch — drops the cached pool reference.

    Does NOT close the underlying connections. Tests that need a fresh
    lazy-init path should call this in fixture teardown. Production code
    must NOT call this; use :func:`close_asyncpg_pool` instead.
    """
    global _pool
    _pool = None
