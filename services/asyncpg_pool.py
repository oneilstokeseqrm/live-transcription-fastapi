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

Pooler handling after derivation depends on whether the host is Neon
(Codex PR-#28 R7 + R8):

* A ``.neon.tech`` ``-pooler`` host that reaches the pool unrewritten
  (only possible via an explicit ``GRANOLA_DB_DIRECT_URL`` pointed at a
  Neon pooler — a ``DATABASE_URL`` would have been auto-rewritten):
  RAISE. Neon's ``-pooler`` IS transaction pooling, which is unsafe for
  the session-scoped advisory lock.
* A non-Neon host whose DNS name contains ``-pooler.``: FAIL CLOSED by
  default, UNIFORMLY for both ``DATABASE_URL``-derived and explicit
  ``GRANOLA_DB_DIRECT_URL`` sources. The hostname can't prove the
  pooling mode and a copy-pasted transaction-pooler URL is a real
  mistake risk, so a loud startup error beats silent double-publish.
  An operator who has VERIFIED the endpoint is session-mode pooling (or
  is a genuinely-direct host that merely has ``-pooler`` in its DNS
  name) opts in with ``GRANOLA_DB_ALLOW_POOLER=true`` — the single,
  hostname-independent mode signal. For this Neon-only deployment the
  branch is unreachable (Neon poolers auto-derive to direct above).

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

Sizing: the pool defaults to ``min_size=1, max_size=20`` (B3 raised the
default from 10). TWO DBOS queues now share this pool: the 5-min poll
(:data:`services.granola_ingestion.scheduler.GRANOLA_POLL_QUEUE`,
``concurrency=5``) and the background history-import
(:data:`~services.granola_ingestion.scheduler.GRANOLA_IMPORT_QUEUE`,
``concurrency=2``). Each concurrent poll cycle AND each concurrent
import holds ONE connection for its whole duration — the per-credential
advisory lock that serializes overlapping cycles (Codex PR-#28 R1 P1) —
PLUS up to one transient connection at a time for the
``external_integration_runs`` / credential / ``granola_import_runs`` SQL
(the adapter's other DB access goes through the separate SQLAlchemy
engine, not this pool). Peak is therefore
``2 × (poll + import concurrency) = 2 × (5 + 2) = 14``. The invariant
``max_size >= 2 × (poll + import concurrency)`` MUST hold or the held
lock connections can starve the transient acquires and deadlock the
cron tick — :func:`_resolve_max_size` enforces this floor. The default
20 leaves headroom above the floor and stays well under Neon's
per-database connection limit. Operators tune via the
``GRANOLA_DB_POOL_MAX_SIZE`` env var (clamped up to the floor; per-loop
pool ownership is the EQ-109 follow-up).
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
# Default max pool size. Env-overridable via GRANOLA_DB_POOL_MAX_SIZE (A7).
# B3 raised the default 10 -> 20: a second worker queue (the background
# history-import) now shares this pool, so peak concurrency grew, and the
# import does extra transient writes (granola_import_runs +
# external_integration_runs) on top of its held advisory-lock connection.
# 20 gives headroom over the hard floor below and stays well under Neon's
# per-database connection ceiling.
_DEFAULT_MAX_SIZE = 20

# Hard invariant floor (see module docstring). Each concurrent poll cycle
# AND each concurrent import holds ONE advisory-lock connection for its
# whole duration PLUS up to one transient connection for SQL, so peak is
# 2 × (poll + import concurrency). With GRANOLA_POLL_QUEUE.concurrency=5 +
# GRANOLA_IMPORT_QUEUE.concurrency=2 that is 2 × (5 + 2) = 14. A max_size
# below this can starve the transient acquires and deadlock the cron tick,
# so a too-small GRANOLA_DB_POOL_MAX_SIZE override is clamped UP to it.
# KEEP IN SYNC with the two queue concurrencies in
# services/granola_ingestion/scheduler.py (not imported here to avoid a
# circular import — scheduler imports this module).
_POOL_MAX_SIZE_FLOOR = 14

_MAX_SIZE_ENV = "GRANOLA_DB_POOL_MAX_SIZE"


def _resolve_max_size() -> int:
    """Resolve the pool ``max_size`` from ``GRANOLA_DB_POOL_MAX_SIZE``.

    Defaults to :data:`_DEFAULT_MAX_SIZE`. A non-integer value is ignored
    (warn + default) rather than crashing pool creation at startup. A value
    below :data:`_POOL_MAX_SIZE_FLOOR` is clamped UP to the floor (warn) —
    the invariant ``max_size >= 2 × (poll + import concurrency)`` MUST hold
    or the held advisory-lock connections starve the transient acquires and
    deadlock the cron tick, so a too-small override is corrected, not
    honored.
    """
    raw = os.environ.get(_MAX_SIZE_ENV)
    if raw is None or not raw.strip():
        return _DEFAULT_MAX_SIZE
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning(
            "%s=%r is not an integer; using default max_size=%d",
            _MAX_SIZE_ENV, raw, _DEFAULT_MAX_SIZE,
        )
        return _DEFAULT_MAX_SIZE
    if value < _POOL_MAX_SIZE_FLOOR:
        logger.warning(
            "%s=%d is below the pool invariant floor %d "
            "(2 × (poll=5 + import=2 concurrency)); clamping up to %d to "
            "avoid starving the held advisory-lock connections.",
            _MAX_SIZE_ENV, value, _POOL_MAX_SIZE_FLOOR, _POOL_MAX_SIZE_FLOOR,
        )
        return _POOL_MAX_SIZE_FLOOR
    return value


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

    # A hostname is only a reliable pooling-MODE signal for Neon
    # (Codex PR-#28 R8 P2). Neon's `-pooler.` endpoint is documented
    # transaction pooling, which silently defeats the session-scoped
    # pg_try_advisory_lock in run_cycle_step (the lock can be lost
    # between statements → overlapping cycles double-publish). For a
    # non-Neon host, `-pooler.` in the DNS name tells us nothing about
    # the mode — session pooling preserves advisory locks just fine.
    host = parsed.hostname or ""
    if "-pooler." in host:
        if host.endswith(".neon.tech"):
            # A Neon pooler endpoint reached the pool. For a DATABASE_URL
            # this can't happen (the derivation above strips -pooler); it
            # means an explicit GRANOLA_DB_DIRECT_URL was pointed at a
            # Neon POOLER endpoint. Neon's -pooler is DEFINITIVELY
            # transaction pooling, so even an explicit "direct" URL here
            # is a clear misconfiguration — fail fast (Codex PR-#28 R7
            # P1) rather than run with an unreliable lock.
            raise RuntimeError(
                f"asyncpg_pool resolved a Neon POOLER host ({host}). "
                f"Neon's -pooler endpoint uses transaction pooling, which "
                f"breaks the scheduler's session-scoped "
                f"pg_try_advisory_lock (overlapping cycles could "
                f"double-publish). Point GRANOLA_DB_DIRECT_URL at the "
                f"DIRECT Neon endpoint (same host without '-pooler'), or "
                f"just set DATABASE_URL — its -pooler host is "
                f"auto-rewritten to the direct endpoint."
            )
        # Non-Neon host whose DNS name contains '-pooler.'. The hostname
        # CANNOT prove the pooling MODE for non-Neon endpoints — only an
        # explicit mode signal can. So we FAIL CLOSED by default,
        # UNIFORMLY for both DATABASE_URL-derived and explicit
        # GRANOLA_DB_DIRECT_URL sources: a copy-pasted transaction-pooler
        # URL is a real mistake risk, and silent double-publish is worse
        # than a loud startup error. An operator who has VERIFIED the
        # endpoint is session-mode pooling (or is genuinely a direct host
        # that merely has '-pooler' in its DNS name) opts in explicitly
        # with GRANOLA_DB_ALLOW_POOLER=true. This is the single
        # mode-override signal — separate from the hostname, which is the
        # only thing that can actually disambiguate the two cases.
        #
        # (Codex PR-#28 trajectory on this branch: R7 fail-fast, R8 don't
        # over-reject, R9 fail-closed+opt-in, R10 trust-explicit, R11
        # reject-explicit. The reviewer oscillated because no hostname
        # rule can separate a direct host named '-pooler' from a
        # transaction pooler. Frozen here on the maximally-safe,
        # mistake-proof design: fail closed unless an explicit,
        # hostname-independent opt-in says otherwise. For this Neon-only
        # deployment the branch is unreachable — Neon poolers auto-derive
        # to direct above.)
        allow_pooler = os.environ.get("GRANOLA_DB_ALLOW_POOLER", "").lower() in (
            "1", "true", "yes",
        )
        if not allow_pooler:
            raise RuntimeError(
                f"asyncpg_pool resolved a non-Neon pooler-looking host "
                f"({host}). The scheduler's pg_try_advisory_lock is unsafe "
                f"under TRANSACTION-mode pooling (overlapping cycles could "
                f"double-publish) and the hostname can't prove the mode. "
                f"Point GRANOLA_DB_DIRECT_URL (or DATABASE_URL) at a DIRECT "
                f"connection to the application database; or, if you have "
                f"VERIFIED this endpoint is SESSION-mode pooling, set "
                f"GRANOLA_DB_ALLOW_POOLER=true to proceed."
            )
        logger.warning(
            "asyncpg_pool: proceeding on pooler-looking host %s because "
            "GRANOLA_DB_ALLOW_POOLER is set. The scheduler's advisory lock "
            "is only reliable if this is a SESSION-mode pooler; a "
            "TRANSACTION-mode pooler will let overlapping cycles "
            "double-publish.",
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
            max_size = _resolve_max_size()
            logger.info(
                "asyncpg_pool: creating pool (min=%d max=%d, ssl=%s)",
                _DEFAULT_MIN_SIZE, max_size, "yes" if "ssl" in kwargs else "no",
            )
            _pool = await asyncpg.create_pool(
                dsn,
                min_size=_DEFAULT_MIN_SIZE,
                max_size=max_size,
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
