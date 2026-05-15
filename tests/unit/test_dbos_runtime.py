"""Unit tests for services.dbos_runtime.

Covers config construction across env-var permutations. Does NOT
launch DBOS in pytest — DBOS.launch() initializes a system database
which would create side-effect files (SQLite) and is verified
empirically at deploy time per plan §13 M1.
"""

from __future__ import annotations

import inspect

import pytest

from services.dbos_runtime import build_dbos_config, dbos_lifespan


class TestBuildDbosConfig:
    def test_name_is_fixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DBOS_SYSTEM_DATABASE_URL", raising=False)
        monkeypatch.delenv("RAILWAY_REPLICA_ID", raising=False)
        config = build_dbos_config()
        assert config["name"] == "live-transcription-fastapi"

    def test_admin_server_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DBOS_SYSTEM_DATABASE_URL", raising=False)
        monkeypatch.delenv("RAILWAY_REPLICA_ID", raising=False)
        config = build_dbos_config()
        assert config["run_admin_server"] is False

    def test_executor_id_from_railway_replica_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RAILWAY_REPLICA_ID", "replica-abc-123")
        config = build_dbos_config()
        assert config["executor_id"] == "replica-abc-123"

    def test_executor_id_is_none_when_railway_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("RAILWAY_REPLICA_ID", raising=False)
        config = build_dbos_config()
        # DBOS's config translator skips the field when None per
        # dbos/_dbos.py:445 — so passing None is the correct local-dev
        # behavior (DBOS picks its own executor identity).
        assert config["executor_id"] is None

    def test_system_database_url_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "DBOS_SYSTEM_DATABASE_URL",
            "postgresql://user:pass@host.example.com/db",
        )
        config = build_dbos_config()
        assert (
            config["system_database_url"]
            == "postgresql://user:pass@host.example.com/db"
        )

    def test_system_database_url_is_none_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DBOS_SYSTEM_DATABASE_URL", raising=False)
        config = build_dbos_config()
        # DBOS falls back to SQLite when the URL is None (verified
        # against dbos/_dbos_config.py:437-439).
        assert config["system_database_url"] is None


class TestDbosLifespanShape:
    def test_lifespan_wraps_async_generator(self) -> None:
        # asynccontextmanager-decorated functions wrap an async
        # generator (`async def` with `yield`). FastAPI's lifespan
        # protocol expects an async context manager that yields once.
        # Assert the shape without entering the context (which would
        # launch DBOS and create side-effect SQLite files).
        wrapped = dbos_lifespan.__wrapped__  # type: ignore[attr-defined]
        assert inspect.isasyncgenfunction(wrapped)
