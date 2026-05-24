"""Unit tests for :mod:`services.asyncpg_pool`.

The pool itself is created lazily against a real DSN, so the
behavioral tests stub out :func:`asyncpg.create_pool`. The DSN-
translation logic is pure (no IO) and exercised against the
SQLAlchemy DSN shapes the repo actually sees in production.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import asyncpg_pool


@pytest.fixture(autouse=True)
def _reset_pool_state():
    """Each test starts with a fresh lazy-singleton state."""
    asyncpg_pool._reset_for_tests()
    yield
    asyncpg_pool._reset_for_tests()


def test_resolve_dsn_strips_asyncpg_driver_prefix(monkeypatch):
    """SQLAlchemy DSN uses ``postgresql+asyncpg://``; asyncpg's native
    ``create_pool`` expects ``postgresql://`` without the driver
    suffix."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://user:pw@host:5432/db?sslmode=require",
    )
    dsn, _kwargs = asyncpg_pool._resolve_dsn_and_kwargs()
    assert dsn.startswith("postgresql://")
    assert "+asyncpg" not in dsn


def test_resolve_dsn_strips_incompatible_libpq_params(monkeypatch):
    """sslmode / channel_binding / options are libpq-only; remove from
    the DSN so asyncpg doesn't reject the connection."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:pw@host:5432/db?sslmode=require&channel_binding=require&options=-csearch_path=foo",
    )
    dsn, _ = asyncpg_pool._resolve_dsn_and_kwargs()
    assert "sslmode" not in dsn
    assert "channel_binding" not in dsn
    assert "options" not in dsn


def test_resolve_dsn_promotes_sslmode_require_to_ssl_kwarg(monkeypatch):
    """sslmode=require → asyncpg ssl kwarg = SSLContext.

    Matches services/database.py's existing SQLAlchemy setup —
    Neon presents a self-signed chain so check_hostname=False +
    verify_mode=CERT_NONE."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://user:pw@host:5432/db?sslmode=require",
    )
    _, kwargs = asyncpg_pool._resolve_dsn_and_kwargs()
    assert "ssl" in kwargs
    ssl_ctx = kwargs["ssl"]
    assert ssl_ctx.check_hostname is False


def test_resolve_dsn_without_sslmode_omits_ssl_kwarg(monkeypatch):
    """Local dev DSNs without sslmode shouldn't get a forced SSL context."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/dev_db")
    _, kwargs = asyncpg_pool._resolve_dsn_and_kwargs()
    assert "ssl" not in kwargs


def test_resolve_dsn_raises_when_database_url_unset(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        asyncpg_pool._resolve_dsn_and_kwargs()


def test_resolve_dsn_rejects_unexpected_scheme(monkeypatch):
    """Defensive: any non-postgres scheme is a misconfiguration we
    surface loudly rather than passing through to asyncpg."""
    monkeypatch.setenv("DATABASE_URL", "mysql://user@host/db")
    with pytest.raises(RuntimeError, match="unexpected scheme"):
        asyncpg_pool._resolve_dsn_and_kwargs()


@pytest.mark.asyncio
async def test_get_pool_creates_on_first_call_and_caches(monkeypatch):
    """First call creates the pool; subsequent calls return the same
    instance without re-invoking ``asyncpg.create_pool``."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/dev_db")

    fake_pool = MagicMock(name="FakeAsyncpgPool")
    create_pool_mock = AsyncMock(return_value=fake_pool)
    with patch.object(asyncpg_pool.asyncpg, "create_pool", create_pool_mock):
        pool_a = await asyncpg_pool.get_asyncpg_pool()
        pool_b = await asyncpg_pool.get_asyncpg_pool()

    assert pool_a is pool_b is fake_pool
    create_pool_mock.assert_called_once()


@pytest.mark.asyncio
async def test_close_pool_is_idempotent_when_uninitialized():
    """Calling close before any get_asyncpg_pool is a no-op."""
    # No pool created yet; should not raise.
    await asyncpg_pool.close_asyncpg_pool()
    await asyncpg_pool.close_asyncpg_pool()  # second call also no-op


@pytest.mark.asyncio
async def test_close_pool_after_creation_invokes_underlying_close(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/dev_db")

    fake_pool = MagicMock(name="FakeAsyncpgPool")
    fake_pool.close = AsyncMock()
    create_pool_mock = AsyncMock(return_value=fake_pool)
    with patch.object(asyncpg_pool.asyncpg, "create_pool", create_pool_mock):
        await asyncpg_pool.get_asyncpg_pool()
        await asyncpg_pool.close_asyncpg_pool()

    fake_pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_pool_then_get_pool_recreates(monkeypatch):
    """After close, the next get_asyncpg_pool builds a fresh pool —
    lifecycle re-entrancy for the test scenario where a fixture
    closes the pool and a follow-up test re-initializes."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/dev_db")

    fake_a = MagicMock(name="FakeA")
    fake_a.close = AsyncMock()
    fake_b = MagicMock(name="FakeB")
    create_pool_mock = AsyncMock(side_effect=[fake_a, fake_b])

    with patch.object(asyncpg_pool.asyncpg, "create_pool", create_pool_mock):
        first = await asyncpg_pool.get_asyncpg_pool()
        await asyncpg_pool.close_asyncpg_pool()
        second = await asyncpg_pool.get_asyncpg_pool()

    assert first is fake_a
    assert second is fake_b
    assert create_pool_mock.await_count == 2
