"""account_lookup.lookup_account_by_domain — Postgres-backed find."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from services.account_lookup import lookup_account_by_domain


@pytest.mark.asyncio
async def test_returns_account_id_on_match():
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
