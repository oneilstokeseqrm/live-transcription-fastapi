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

Direct connection required (Codex PR-#28 R4 P1): the
:func:`services.granola_ingestion.scheduler.run_cycle_step` advisory
lock (``pg_try_advisory_lock``) is SESSION-scoped, and Neon's default
``DATABASE_URL`` is the PgBouncer ``-pooler`` endpoint running in
TRANSACTION pooling mode. Under transaction pooling, session state
(including advisory locks) is NOT preserved across statements — the
lock can land on a different backend than the queries that follow, so
the per-credential serialization silently provides no protection and
overlapping cycles can still double-publish. asyncpg's prepared-
statement cache is also unsafe through PgBouncer. So this pool resolves
a DIRECT (non-pooler) connection, preferring (in order):

1. ``GRANOLA_DB_DIRECT_URL`` — explicit override if an operator wants a
   dedicated direct connection string (must point at the APPLICATION
   database — same ``vault`` / ``public`` schemas as ``DATABASE_URL``).
2. otherwise derive a direct endpoint FROM ``DATABASE_URL`` by stripping
   ``-pooler`` from the Neon host. This keeps the SAME database +
   credentials and only swaps the PgBouncer endpoint for the direct
   one. We deliberately do NOT reuse ``DBOS_SYSTEM_DATABASE_URL`` —
   that variable is the DBOS *system* database contract
   (:mod:`services.dbos_runtime`), which a deployment may point at a
   dedicated database or role; binding the Granola pool there would
   query the wrong database for ``vault.user_credentials`` (Codex
   PR-#28 R5 P1). Deriving from ``DATABASE_URL`` guarantees same-DB.

If the resolved host is STILL a ``-pooler`` host after derivation (an
explicit ``GRANOLA_DB_DIRECT_URL`` that points at a pooler, or a
non-Neon pooler whose host we can't rewrite), we log a loud warning
that advisory-lock serialization will be unreliable.

``statement_cache_size=0`` is set unconditionally — it's the asyncpg
setting required for PgBouncer compatibility and costs almost nothing
for the scheduler's low query volume, so it removes the prepared-
statement footgun on any connection.

DSN handling: the resolved URL may carry a ``postgresql+asyncpg``
driver prefix and SQLAlchemy-style query params (``sslmode``,
``channel_binding``, ``options``). asyncpg's native ``create_pool``
doesn't recognize the driver prefix or those params, so we strip them
here and promote ``sslmode=require`` to an explicit SSL context —
mirroring the SQLAlchemy engine's setup in
:func:`services.database.get_database_url`.

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


def _to_direct_neon_url(url: str) -> str:
    """Rewrite a Neon ``-pooler`` host to its direct endpoint, preserving
    the database, credentials, port, and query string.

    Neon's pooled endpoint is ``<ep>-pooler.<rest>`` and the direct
    endpoint is ``<ep>.<rest>`` — same database, different connection
    route. Stripping ``-pooler`` keeps us on the SAME application
    database (Codex PR-#28 R5 P1) while giving us the direct connection
    the advisory lock needs (R4 P1).

    The rewrite is gated on a verified Neon hostname (``.neon.tech``
    suffix) — Codex PR-#28 R6 P2. A non-Neon custom pooler whose name
    merely contains ``-pooler.`` (e.g. ``pg-pooler.internal``) is left
    intact: rewriting it to ``pg.internal`` would point at a
    non-existent host. Those deployments instead hit the pooler warning
    in :func:`_resolve_dsn_and_kwargs` and should set
    ``GRANOLA_DB_DIRECT_URL`` explicitly. A host that's already direct
    (no ``-pooler.``) is also returned unchanged.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host.endswith(".neon.tech"):
        return url
    if "-pooler." not in host:
        return url
    direct_host = host.replace("-pooler.", ".", 1)
    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    portpart = f":{parsed.port}" if parsed.port else ""
    new_netloc = f"{userinfo}{direct_host}{portpart}"
    return urlunparse(
        (parsed.scheme, new_netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


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
    # Resolve a DIRECT (non-pooler) connection to the APPLICATION
    # database — see module docstring (Codex PR-#28 R4 P1 + R5 P1). The
    # advisory lock that serializes Granola cycles is session-scoped and
    # is silently defeated by PgBouncer transaction pooling, so we need
    # a direct endpoint; but it MUST be the same database DATABASE_URL
    # points at (vault.user_credentials lives there), so we derive the
    # direct host from DATABASE_URL rather than borrowing the DBOS
    # system database URL.
    explicit = os.environ.get("GRANOLA_DB_DIRECT_URL")
    database_url = os.environ.get("DATABASE_URL")
    if explicit:
        url = explicit
    elif database_url:
        url = _to_direct_neon_url(database_url)
    else:
        raise RuntimeError(
            "No database URL for the asyncpg pool. Set DATABASE_URL "
            "(its -pooler host is auto-rewritten to the direct Neon "
            "endpoint) or GRANOLA_DB_DIRECT_URL (an explicit direct "
            "connection to the application database) in Railway env "
            "config."
        )
    parsed = urlparse(url)
    scheme = parsed.scheme
    if scheme == "postgresql+asyncpg":
        scheme = "postgresql"
    elif scheme not in ("postgresql", "postgres"):
        # Defensive: any other scheme prefix is a misconfiguration we
        # surface immediately rather than letting asyncpg fail mid-cycle.
        raise RuntimeError(
            f"resolved database URL has unexpected scheme {parsed.scheme!r}; "
            f"expected 'postgresql' or 'postgresql+asyncpg'"
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

    # statement_cache_size=0 is required for PgBouncer compatibility and
    # harmless on a direct connection at the scheduler's low query
    # volume — set unconditionally to remove the prepared-statement
    # footgun (Codex PR-#28 R4 P1).
    kwargs: dict = {"statement_cache_size": 0}
    if ssl_required:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl"] = ssl_ctx

    # Loud warning if we ended up on a pooler host: the advisory lock in
    # run_cycle_step is session-scoped and unreliable under PgBouncer
    # transaction pooling, so overlapping cycles could still
    # double-publish. statement_cache_size=0 covers the prepared-
    # statement issue but NOT the lock — only a direct endpoint fixes
    # that.
    host = parsed.hostname or ""
    if "-pooler." in host:
        logger.warning(
            "asyncpg_pool: resolved a POOLER host (%s). The Granola "
            "scheduler's pg_try_advisory_lock is session-scoped and is "
            "NOT reliable under PgBouncer transaction pooling — "
            "overlapping cycles could double-publish. Set "
            "GRANOLA_DB_DIRECT_URL to a DIRECT (non-pooler) connection "
            "to the application database. (A Neon DATABASE_URL is "
            "auto-rewritten to its direct endpoint; a non-Neon pooler "
            "needs the explicit GRANOLA_DB_DIRECT_URL.)",
            host,
        )

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
