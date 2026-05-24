"""Lifespan-ordering test for ``main.lifespan`` (Codex PR-#28 R4 P2).

Verifies the shared asyncpg pool is closed AFTER ``dbos_lifespan``'s
``__aexit__`` runs ``DBOS.destroy()`` — not before. Closing earlier
would mark the pool as closing while the DBOS executor is still alive,
so a Granola workflow mid-cycle during shutdown would fail its next
``pool.acquire()`` and abort.

``main`` is imported lazily inside the test after the required env vars
are set, so module-level ``validate_environment()`` doesn't ``sys.exit``
during collection.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_lifespan_closes_pool_after_dbos_destroy(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/test")

    # main.py constructs a Deepgram client at module import time, which
    # validates its key — patch the constructor so the import is inert.
    with patch("deepgram.Deepgram", MagicMock()):
        import main

    order: list[str] = []

    @asynccontextmanager
    async def _fake_dbos_lifespan(_app):
        # __aexit__ (after yield) is DBOS.destroy()'s slot.
        yield
        order.append("dbos_destroy")

    async def _fake_close():
        order.append("close_pool")

    monkeypatch.setattr(main, "dbos_lifespan", _fake_dbos_lifespan)
    monkeypatch.setattr(main, "close_asyncpg_pool", _fake_close)
    monkeypatch.setattr(main, "reap_stuck_jobs", AsyncMock())
    monkeypatch.setattr(main, "_drain_text_clean_background_tasks", AsyncMock())

    async with main.lifespan(main.app):
        pass

    # The pool MUST close after DBOS is destroyed.
    assert order == ["dbos_destroy", "close_pool"], (
        f"expected pool close after DBOS.destroy(), got order={order}"
    )


@pytest.mark.asyncio
async def test_lifespan_closes_pool_even_if_startup_raises(monkeypatch):
    """If a startup task raises, the outer finally still closes the pool
    (and DBOS still tears down via its own contextmanager)."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/test")

    # main.py constructs a Deepgram client at module import time, which
    # validates its key — patch the constructor so the import is inert.
    with patch("deepgram.Deepgram", MagicMock()):
        import main

    order: list[str] = []

    @asynccontextmanager
    async def _fake_dbos_lifespan(_app):
        yield
        order.append("dbos_destroy")

    async def _fake_close():
        order.append("close_pool")

    monkeypatch.setattr(main, "dbos_lifespan", _fake_dbos_lifespan)
    monkeypatch.setattr(main, "close_asyncpg_pool", _fake_close)
    monkeypatch.setattr(
        main, "reap_stuck_jobs", AsyncMock(side_effect=RuntimeError("startup boom"))
    )
    monkeypatch.setattr(main, "_drain_text_clean_background_tasks", AsyncMock())

    with pytest.raises(RuntimeError, match="startup boom"):
        async with main.lifespan(main.app):
            pass

    # Pool still closed despite the startup failure.
    assert "close_pool" in order
