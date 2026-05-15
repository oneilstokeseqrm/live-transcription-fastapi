"""account_lookup.lookup_account_by_domain — Postgres-backed find."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from services.account_lookup import LOOKUP_SQL, lookup_account_by_domain


@pytest.mark.asyncio
async def test_returns_account_id_on_match():
    """A row in account_domains matching (tenant_id, domain) → returns account_id."""
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: "acct-1"))
    result = await lookup_account_by_domain(
        session=session,
        tenant_id="tenant-1",
        domain="acme.com",
    )
    assert result == "acct-1"


@pytest.mark.asyncio
async def test_returns_none_on_miss():
    """No row matches → returns None (signals route to pending queue)."""
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    result = await lookup_account_by_domain(
        session=session,
        tenant_id="tenant-1",
        domain="unknown.com",
    )
    assert result is None


@pytest.mark.asyncio
async def test_domain_is_normalized_lowercase():
    """Domain case-insensitivity: account_domains stores lowercased domain;
    callers can pass any case and the SQL still matches."""
    session = MagicMock()
    captured = {}

    async def _exec(stmt, params=None):
        captured["params"] = params
        m = MagicMock()
        m.scalar_one_or_none = lambda: None
        return m

    session.execute = _exec
    await lookup_account_by_domain(session=session, tenant_id="t", domain="ACME.com")
    assert captured["params"]["domain"] == "acme.com"


@pytest.mark.asyncio
async def test_tenant_isolation_passes_tenant_id_to_sql():
    """Tenant isolation invariant: tenant_id is always part of the WHERE clause.
    The SQL filter does the isolation work; this test asserts the caller's
    tenant_id flows into the bound params unmodified so DB-side filtering can
    enforce it. If a future refactor drops tenant_id from the params, the
    cross-tenant safety story breaks."""
    session = MagicMock()
    captured = {}

    async def _exec(stmt, params=None):
        captured["params"] = params
        m = MagicMock()
        m.scalar_one_or_none = lambda: None
        return m

    session.execute = _exec
    await lookup_account_by_domain(
        session=session,
        tenant_id="11111111-1111-4111-8111-111111111111",
        domain="acme.com",
    )
    assert captured["params"]["tenant_id"] == "11111111-1111-4111-8111-111111111111"


def test_sql_queries_account_domains_not_accounts():
    """Regression guard for the 2026-05-15 bug: prior to the fix, the SQL
    queried `FROM accounts ... AND lower(domain) = :domain`, but `accounts`
    has no `domain` column — the correct join table is `account_domains`.
    Without this assertion, the unit tests above all pass (they mock the
    session) and a future regression to the wrong table would ship silently
    until production traffic hits a BUSINESS-domain attendee.

    See `services/account_lookup.py` for the full context comment."""
    sql_text = str(LOOKUP_SQL).lower()
    # Must query the join table.
    assert "from account_domains" in sql_text, (
        "lookup_account_by_domain must query account_domains, not accounts. "
        "The accounts table has no domain column; querying it throws a SQL "
        "error that the enclosing try/except in transcript_enrichment.enrich() "
        "silently swallows."
    )
    # Must NOT query accounts directly with a domain filter — that's the
    # specific regression we're guarding against.
    assert "from accounts" not in sql_text, (
        "lookup_account_by_domain must NOT query accounts directly. "
        "Use account_domains (the join table) and select account_id."
    )
    # Selecting account_id (the join-table column) rather than id (the
    # accounts PK) is the correct shape.
    assert "select account_id" in sql_text, (
        "lookup_account_by_domain should select account_id (the FK on the "
        "join table), not id (the accounts PK)."
    )
    # Tenant scoping must be in the SQL — DB-side enforcement, not caller trust.
    assert ":tenant_id" in str(LOOKUP_SQL), (
        "lookup_account_by_domain must filter by :tenant_id. Tenant isolation "
        "is a core invariant of this initiative."
    )
