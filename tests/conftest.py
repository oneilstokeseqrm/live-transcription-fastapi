"""Shared pytest fixtures for Phase 1.5 M3 tests.

Test infrastructure: **Option B** (test-tenant scoping in production
Neon) with **mandatory teardown per test**. Locked decision documented
in ``docs/superpowers/specs/NEXT-SESSION-START-HERE.md`` (item 10 of
the 14 LOCKED decisions). Migration to Option A (Neon test branch)
is gated on first real customer data landing.

Tests that touch the database skip when ``DATABASE_URL`` is unset
(typical CI without secrets). Local dev with the repo's ``.env`` has
``DATABASE_URL`` pointed at production Neon (eq-dev project,
``super-glitter-11265514``).

The fixture loads ``.env`` if python-dotenv is available — matches the
existing service runtime behavior (``main.py`` calls ``load_dotenv``
at import).
"""

from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# Phase 1.5 test tenant ID (also see memory/reference_test_tenant.md).
# All data in eq-dev under this tenant is test data; safe to seed and tear down.
TEST_TENANT_ID = "11111111-1111-4111-8111-111111111111"


# A pre-existing user under the test tenant. ``users.id`` is a FK target for
# ``pending_account_mappings.owner_user_id``; tests that seed queue rows
# must use a real user_id. This ID was confirmed present in production
# Neon (eq-dev, project super-glitter-11265514) on 2026-05-15.
TEST_USER_ID = "b0000000-0000-4000-8000-000000000002"


def _database_available() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


_needs_database = pytest.mark.skipif(
    not _database_available(),
    reason=(
        "DATABASE_URL not set; tests using a real Neon session are skipped. "
        "Set DATABASE_URL in your environment to run these locally."
    ),
)


def needs_database(item):
    """Marker: test requires DATABASE_URL to be set against production Neon."""
    return _needs_database(item)


@pytest.fixture
def test_tenant_id() -> str:
    return TEST_TENANT_ID


@pytest.fixture
def test_user_id() -> str:
    return TEST_USER_ID


@pytest_asyncio.fixture
async def session(test_tenant_id: str) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession bound to ``DATABASE_URL``, then tear down test data.

    Cleanup runs AFTER the test, regardless of pass/fail. It deletes all
    rows in the relevant tables WHERE ``tenant_id = TEST_TENANT_ID``.
    The list of tables covers the M3 workflow's write surface; new
    tables introduced by future milestones must be added here.

    The session uses ``services.database.get_async_session`` so we
    exercise the same connection setup the production code uses. The
    engine is intentionally NOT disposed between tests — disposing on
    a per-test loop iteration triggers asyncpg "Task got Future
    attached to a different loop" errors. The engine survives the
    test session and is GC'd when the process exits.
    """
    if not _database_available():
        pytest.skip("DATABASE_URL not set")

    from services.database import get_async_session

    try:
        async with get_async_session() as s:
            yield s
    finally:
        # Mandatory teardown. Order matters: FK dependents first.
        # We delete only rows tagged with the test tenant, never the
        # tenant row itself.
        await _teardown_test_tenant_rows(test_tenant_id)


async def _teardown_test_tenant_rows(tenant_id: str) -> None:
    """Delete all rows tagged with ``tenant_id`` from M3-relevant tables.

    Order: child rows before parents to satisfy FKs.
    """
    from services.database import get_async_session

    async with get_async_session() as session:
        async with session.begin():
            # interaction_contact_links: no tenant_id column; scope via
            # parent summaries.
            await session.execute(
                text("""
                    DELETE FROM interaction_contact_links
                    WHERE interaction_id IN (
                        SELECT summary_id FROM interaction_summaries
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                    )
                """),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("DELETE FROM interaction_summaries WHERE tenant_id = CAST(:tenant_id AS uuid)"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("DELETE FROM raw_interactions WHERE tenant_id = CAST(:tenant_id AS uuid)"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("DELETE FROM pending_account_mapping_signals WHERE tenant_id = CAST(:tenant_id AS uuid)"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("DELETE FROM pending_account_mappings WHERE tenant_id = CAST(:tenant_id AS uuid)"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("DELETE FROM contacts WHERE tenant_id = CAST(:tenant_id AS uuid)"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("DELETE FROM account_domains WHERE tenant_id = CAST(:tenant_id AS uuid)"),
                {"tenant_id": tenant_id},
            )
            await session.execute(
                text("DELETE FROM accounts WHERE tenant_id = CAST(:tenant_id AS uuid)"),
                {"tenant_id": tenant_id},
            )
