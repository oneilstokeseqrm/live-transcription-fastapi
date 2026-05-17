"""Unit tests for scripts/verify_schema.py pure functions.

The integration behavior (PREPARE against live Neon, exit-code matrix)
was hand-verified during M5 implementation:
  - happy path → exit 0
  - undefined column → exit 1
  - undefined table → exit 1
  - syntax error → exit 1

This module covers the placeholder-translation and DSN-normalization
helpers, which run without a database connection.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `scripts/` importable without installing the project.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.verify_schema import (  # noqa: E402
    normalize_dsn_for_asyncpg,
    translate_named_to_numbered,
)


class TestTranslateNamedToNumbered:
    def test_single_named_param(self) -> None:
        sql, order = translate_named_to_numbered(
            "SELECT id FROM accounts WHERE tenant_id = :t"
        )
        assert sql == "SELECT id FROM accounts WHERE tenant_id = $1"
        assert order == ["t"]

    def test_repeated_named_param_reuses_position(self) -> None:
        sql, order = translate_named_to_numbered(
            "SELECT * FROM x WHERE a = :t AND b = :t"
        )
        assert sql == "SELECT * FROM x WHERE a = $1 AND b = $1"
        assert order == ["t"]

    def test_multiple_distinct_params_keep_first_appearance_order(self) -> None:
        sql, order = translate_named_to_numbered(
            "SELECT * FROM x WHERE a = :alpha AND b = :beta AND c = :alpha"
        )
        assert sql == "SELECT * FROM x WHERE a = $1 AND b = $2 AND c = $1"
        assert order == ["alpha", "beta"]

    def test_postgres_type_cast_is_preserved(self) -> None:
        # `::uuid` is a type cast, not a placeholder. The 2026-05-17 SQL P1
        # was specifically about this not getting consumed by parameter
        # translation. Asserts the negative lookbehind in the regex.
        sql, order = translate_named_to_numbered(
            "SELECT id FROM accounts WHERE tenant_id = '00000000-0000-0000-0000-000000000000'::uuid"
        )
        assert "::uuid" in sql
        assert order == []

    def test_mixed_named_and_type_cast(self) -> None:
        sql, order = translate_named_to_numbered(
            "SELECT id FROM x WHERE tenant_id = :t AND v = '1'::uuid"
        )
        assert sql == "SELECT id FROM x WHERE tenant_id = $1 AND v = '1'::uuid"
        assert order == ["t"]

    def test_no_params_returns_unchanged(self) -> None:
        sql, order = translate_named_to_numbered("SELECT 1")
        assert sql == "SELECT 1"
        assert order == []


class TestNormalizeDsnForAsyncpg:
    def test_strips_channel_binding(self) -> None:
        out = normalize_dsn_for_asyncpg(
            "postgresql://u:p@h/db?sslmode=require&channel_binding=require"
        )
        assert "channel_binding" not in out
        assert "sslmode=require" in out

    def test_strips_sqlalchemy_driver_suffix(self) -> None:
        out = normalize_dsn_for_asyncpg("postgresql+asyncpg://u:p@h/db")
        assert out.startswith("postgresql://")
        assert "+asyncpg" not in out

    def test_preserves_sslmode(self) -> None:
        # sslmode is the one query param asyncpg DOES respect; must survive
        # the normalize step.
        out = normalize_dsn_for_asyncpg(
            "postgresql://u:p@h/db?sslmode=require"
        )
        assert "sslmode=require" in out

    def test_idempotent(self) -> None:
        dsn = "postgresql://u:p@h:5432/db?sslmode=require"
        assert normalize_dsn_for_asyncpg(dsn) == normalize_dsn_for_asyncpg(
            normalize_dsn_for_asyncpg(dsn)
        )
