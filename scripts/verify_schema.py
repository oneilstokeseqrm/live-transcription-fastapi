#!/usr/bin/env python3
"""Verify SQL against the live Neon schema at design time.

Closes Item 4 of `tasks/downstream/test-discipline-gaps-2026-05-15.md`:
catches the bug class that produced the 2026-05-15 Phase 1 silent
regression (a SQL constant referenced `accounts.domain` after the
column had moved to `account_domains`, but mock-only tests never
executed the query, and the production failure was swallowed by an
outer try/except). EXPLAIN/PREPARE the SQL against live Postgres
to validate column + table references BEFORE the code ships.

Usage:
    python scripts/verify_schema.py --sql-text "SELECT id FROM accounts WHERE tenant_id=:t"
    python scripts/verify_schema.py --sql-file path/to/query.sql
    echo "SELECT ..." | python scripts/verify_schema.py --stdin
    python scripts/verify_schema.py --sql-text "..." --dsn-env NEON_VERIFY_DSN

Connection:
    Reads DATABASE_URL by default. Override with --dsn-env or --dsn.
    Recommended: point at a Neon test branch, not production, so
    design-time verification has zero impact on prod load.

Placeholders:
    SQLAlchemy-style :name placeholders are auto-translated to
    Postgres $N positional placeholders. Postgres-style $N is
    passed through. Postgres type casts (::uuid, etc.) are preserved.

Exit codes:
    0 = SQL prepared successfully (schema-valid)
    1 = Schema mismatch (UndefinedColumn / UndefinedTable / etc.) OR syntax error
    2 = Connection or configuration error
    3 = CLI argument error
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from typing import Final
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import asyncpg


# Strip pooler / SQLAlchemy-only query params asyncpg doesn't recognize.
_ASYNCPG_INCOMPATIBLE_PARAMS: Final = frozenset({
    "channel_binding",  # pgbouncer + pg17 handshake hint; asyncpg ignores
    "options",          # pooler-only options
})


def normalize_dsn_for_asyncpg(dsn: str) -> str:
    """Strip query params asyncpg doesn't recognize.

    Mirrors the logic in services/database.py:get_database_url() — keep
    sslmode (asyncpg respects it) and drop the rest so the connect call
    doesn't error with "unknown parameter".
    """
    parsed = urlparse(dsn)
    query = parse_qs(parsed.query)
    clean = {
        k: v for k, v in query.items()
        if k not in _ASYNCPG_INCOMPATIBLE_PARAMS
    }
    new_query = urlencode(clean, doseq=True)
    # Strip the `+driver` suffix if present (postgresql+asyncpg → postgresql).
    scheme = parsed.scheme.split("+", 1)[0] if "+" in parsed.scheme else parsed.scheme
    return urlunparse(parsed._replace(scheme=scheme, query=new_query))


# Match :name placeholders, but NOT ::cast or := assignment.
# Negative lookbehind for `:` prevents matching the second colon of ::cast.
_NAMED_PARAM_RE: Final = re.compile(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)\b")


def translate_named_to_numbered(sql: str) -> tuple[str, list[str]]:
    """Convert SQLAlchemy `:name` placeholders to Postgres `$N` positional.

    The same `:name` always maps to the same `$N` (first-appearance order).
    Returns the rewritten SQL and the ordered list of param names found,
    so callers can report which params the script substituted.
    """
    name_to_num: dict[str, int] = {}
    order: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in name_to_num:
            order.append(name)
            name_to_num[name] = len(name_to_num) + 1
        return f"${name_to_num[name]}"

    return _NAMED_PARAM_RE.sub(replace, sql), order


async def verify_sql(sql: str, dsn: str, *, verbose: bool = False) -> int:
    """Prepare the SQL against Postgres without executing it.

    asyncpg.Connection.prepare() asks Postgres to parse + plan the query;
    Postgres validates every column and relation reference at that point.
    UndefinedColumn / UndefinedTable errors raise here, catching the bug
    class we care about. Actual data isn't read; this is safe to run
    against any branch including production read-replicas (no writes).
    """
    rewritten, params = translate_named_to_numbered(sql)
    if verbose and params:
        print(
            f"[info] translated {len(params)} named placeholder(s) "
            f"to positional: {params}",
            file=sys.stderr,
        )

    try:
        conn = await asyncpg.connect(normalize_dsn_for_asyncpg(dsn))
    except (OSError, asyncpg.PostgresError) as exc:
        print(f"CONNECTION ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    try:
        await conn.prepare(rewritten)
    except asyncpg.UndefinedColumnError as exc:
        print(f"SCHEMA ERROR (undefined column): {exc}", file=sys.stderr)
        return 1
    except asyncpg.UndefinedTableError as exc:
        print(f"SCHEMA ERROR (undefined table/relation): {exc}", file=sys.stderr)
        return 1
    except asyncpg.UndefinedObjectError as exc:
        print(f"SCHEMA ERROR (undefined object): {exc}", file=sys.stderr)
        return 1
    except asyncpg.UndefinedFunctionError as exc:
        print(f"SCHEMA ERROR (undefined function): {exc}", file=sys.stderr)
        return 1
    except asyncpg.PostgresSyntaxError as exc:
        print(f"SQL SYNTAX ERROR: {exc}", file=sys.stderr)
        return 1
    except asyncpg.PostgresError as exc:
        print(
            f"PREPARE FAILED ({type(exc).__name__}): {exc}",
            file=sys.stderr,
        )
        return 1
    finally:
        await conn.close()

    print("OK: SQL is schema-valid against the connected Neon database")
    return 0


def _read_sql_from_args(args: argparse.Namespace) -> str | None:
    if args.sql_text is not None:
        return args.sql_text
    if args.sql_file is not None:
        try:
            return args.sql_file.read_text()
        except OSError as exc:
            print(f"FILE ERROR: {exc}", file=sys.stderr)
            return None
    if args.stdin:
        return sys.stdin.read()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_schema.py",
        description=(
            "Verify SQL against the live Neon schema via PREPARE. "
            "Exits 0 if schema-valid, 1 if schema mismatch, 2 on connection error."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sql-text", help="SQL query text")
    source.add_argument(
        "--sql-file",
        type=lambda p: __import__("pathlib").Path(p),
        help="Path to file containing SQL",
    )
    source.add_argument(
        "--stdin",
        action="store_true",
        help="Read SQL from standard input",
    )
    parser.add_argument(
        "--dsn",
        help="Postgres DSN to verify against (overrides --dsn-env)",
    )
    parser.add_argument(
        "--dsn-env",
        default="DATABASE_URL",
        help="Env var holding the Postgres DSN (default: DATABASE_URL)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print parameter substitution info",
    )

    args = parser.parse_args(argv)

    sql = _read_sql_from_args(args)
    if sql is None or not sql.strip():
        print("ARGUMENT ERROR: no SQL provided", file=sys.stderr)
        return 3

    dsn = args.dsn or os.getenv(args.dsn_env)
    if not dsn:
        print(
            f"ARGUMENT ERROR: --dsn not given and {args.dsn_env} is unset",
            file=sys.stderr,
        )
        return 3

    return asyncio.run(verify_sql(sql, dsn, verbose=args.verbose))


if __name__ == "__main__":
    sys.exit(main())
