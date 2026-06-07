"""Tenant RLS scoping helpers for the prod least-privilege repoint (EQ-120).

In PROD live-transcription connects as the non-owner Neon role
``eqprod_transcription`` (NOBYPASSRLS). The strict tenant tables
(``accounts``, ``contacts``, ``account_domains``) carry RLS ENABLE+FORCE +
``tenant_isolation`` policies keyed on the ``app.tenant_id`` GUC (EQ-67), and
``vault.user_credentials`` / ``vault.credential_access_log`` are armed the same
way at this repoint. Under that role, EVERY tenant-scoped statement MUST set the
GUC inside the SAME transaction or it fails closed.

Postgres ``set_config('app.tenant_id', <t>, is_local=true)`` ONLY survives within
the current transaction (Codex plan-consult P1-a). Most asyncpg call sites here
issue a single statement on a freshly-acquired pool connection with NO explicit
transaction, so a bare ``set_config(local)`` before such a statement would be a
no-op. These helpers therefore always open an explicit transaction around the
scoped work; ``is_local=true`` then auto-clears the GUC at transaction end, so a
pooled connection never leaks tenant scope to the next checkout.

In DEV the service connects as the BYPASSRLS owner, so RLS is inert and these
helpers are functionally no-ops — but the SAME code runs in both environments
(prod-mirrors-dev parity). This mirrors the hub's ``withRlsTransaction`` (TS) and
the eq-user-insights ``set_tenant_scope`` (Python) patterns.

Surfaces:
  * SQLAlchemy ORM / ``text()``  -> :func:`tenant_session`
  * raw asyncpg pool             -> :func:`scoped_acquire` (standalone statements)
                                    + :func:`set_tenant_guc` (accessors that open
                                      their own ``conn.transaction()``)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

import asyncpg
import asyncpg.pool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.database import get_session_maker

# asyncpg form (positional $1). The RLS policies cast
# current_setting('app.tenant_id')::uuid, so we always pass the tenant as text.
_SET_GUC_SQL_ASYNCPG = "SELECT set_config('app.tenant_id', $1, true)"
# SQLAlchemy form (named bind).
_SET_GUC_SQL_SA = text("SELECT set_config('app.tenant_id', :tenant_id, true)")

ConnectionOrProxy = asyncpg.Connection | asyncpg.pool.PoolConnectionProxy


def _as_text(tenant_id: str | UUID) -> str:
    """Normalize a tenant id (UUID or str) to the text form set_config wants."""
    return str(tenant_id)


async def set_tenant_guc(conn: ConnectionOrProxy, tenant_id: str | UUID) -> None:
    """Pin ``app.tenant_id`` for the CURRENT transaction on ``conn``.

    MUST be called INSIDE an open asyncpg transaction (``async with
    conn.transaction():``) and BEFORE any statement that touches an
    RLS-enforced table (``vault.user_credentials``,
    ``vault.credential_access_log``). ``is_local=true`` scopes the GUC to the
    enclosing transaction, so it auto-clears on commit/rollback — no pooled-
    connection leak. Use this from accessors that already manage their own
    transaction; use :func:`scoped_acquire` for standalone statements.
    """
    await conn.execute(_SET_GUC_SQL_ASYNCPG, _as_text(tenant_id))


@asynccontextmanager
async def scoped_acquire(
    pool: asyncpg.Pool, tenant_id: str | UUID
) -> AsyncIterator[ConnectionOrProxy]:
    """Acquire a pooled connection in a tenant-scoped transaction.

    Opens an explicit transaction, pins ``app.tenant_id`` (is_local), and yields
    the connection. The caller's statements run inside that transaction and
    commit together at block exit (or roll back on exception). Use for
    standalone vault SQL that is NOT already inside an explicit transaction —
    the single-statement credential accessors and the fresh-connection audit
    writer (Codex plan-consult P1-a).
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await set_tenant_guc(conn, tenant_id)
            yield conn


@asynccontextmanager
async def tenant_session(tenant_id: str | UUID) -> AsyncIterator[AsyncSession]:
    """A SQLAlchemy session bound to ONE transaction with ``app.tenant_id`` pinned.

    Use for ALL access — reads AND writes — to RLS-enforced strict tables
    (``accounts`` / ``contacts`` / ``account_domains`` and any other
    tenant_isolation table). Under FORCE RLS an UNSCOPED read THROWS because the
    policy evaluates an unset ``current_setting('app.tenant_id')`` (Codex
    plan-consult P1-b), so reads must be scoped too, not just writes.

    The helper OWNS the transaction (``session.begin()``): callers do one unit of
    work and must NOT call ``session.commit()`` / ``session.rollback()``
    themselves — the block commits on success (and rolls back on exception), and
    the ``is_local`` GUC auto-clears at transaction end so the pooled connection
    never leaks tenant scope. Mirrors the hub's ``withRlsTransaction``.
    """
    session_maker = get_session_maker()
    async with session_maker() as session:
        async with session.begin():
            await session.execute(_SET_GUC_SQL_SA, {"tenant_id": _as_text(tenant_id)})
            yield session
