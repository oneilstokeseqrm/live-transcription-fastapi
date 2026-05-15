"""Unit tests for services.account_provisioning.materialization.

**Real-substrate** per plan §7.2 + Item 1 of test-discipline-gaps:
NO MagicMock for the SQLAlchemy session. The session is bound to
production Neon under the locked Option B test infrastructure (test
tenant scoping + mandatory teardown via conftest.session fixture).

Covers:
- SQL text assertions (defends against import-level mocking gaps —
  mirrors the post-2026-05-15-regression discipline).
- M3-required ON CONFLICT (interaction_id, contact_id) replay-safety
  using the live M2 unique index.
- M3-required removal of INSERT_OUTBOX_SQL.
- Cross-account contact reassignment fail-loud (Phase 3 scope guard).
- Zero-signals fail-loud.
- Pure-function _split_name behavior.

Tests requiring DATABASE_URL skip cleanly when unset.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.account_provisioning import materialization as M
from services.account_provisioning.materialization import (
    INSERT_CONTACT_SQL,
    INSERT_LINK_SQL,
    SELECT_SIGNALS_SQL,
    UPDATE_QUEUE_SQL,
    UPSERT_PLACEHOLDER_SUMMARY_SQL,
    UPSERT_RAW_INTERACTION_SQL,
    _split_name,
    materialize_account_approval,
)
from services.account_provisioning.types import MaterializationResult


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


class TestSplitName:
    def test_none_returns_none_pair(self):
        assert _split_name(None) == (None, None)

    def test_empty_returns_none_pair(self):
        assert _split_name("") == (None, None)
        assert _split_name("   ") == (None, None)

    def test_single_token(self):
        assert _split_name("Alice") == ("Alice", None)

    def test_first_last(self):
        assert _split_name("Alice Smith") == ("Alice", "Smith")

    def test_three_tokens_groups_last_name(self):
        assert _split_name("Alice Mary Smith") == ("Alice", "Mary Smith")


# ---------------------------------------------------------------------------
# SQL-text assertions
# ---------------------------------------------------------------------------


class TestSqlTextSanity:
    """Belt-and-suspenders defenses against mock-only test gaps.

    Mirrors `test_sql_queries_account_domains_not_accounts` from the
    2026-05-15 regression fix (`tasks/lessons.md` "Four systemic quality
    gaps"). Any SQL change should produce a deliberate test update.
    """

    def test_insert_contact_uses_canonical_unique_key(self):
        """ON CONFLICT (tenant_id, email) matches contacts_tenant_id_email_key."""
        sql = str(INSERT_CONTACT_SQL.text)
        assert "ON CONFLICT (tenant_id, email)" in sql

    def test_insert_link_uses_m2_unique_index_via_on_conflict(self):
        """M3-required: link insert uses ON CONFLICT (interaction_id, contact_id)
        DO NOTHING against the M2 unique index."""
        sql = str(INSERT_LINK_SQL.text)
        assert "ON CONFLICT (interaction_id, contact_id) DO NOTHING" in sql

    def test_no_outbox_sql_constant(self):
        """M3-required: account_provisioning_outbox SQL constant is gone."""
        assert not hasattr(M, "INSERT_OUTBOX_SQL")

    def test_no_outbox_table_in_emitted_sql(self):
        """Belt-and-suspenders: no SQL constant in the module mentions the dropped table.

        Only the module-level docstring and code comments can mention
        ``account_provisioning_outbox`` (explaining why it was removed).
        Any ``sqlalchemy.TextClause`` actually pointing at the table is
        the regression we're guarding against.
        """
        from sqlalchemy.sql.elements import TextClause
        for attr_name in dir(M):
            attr = getattr(M, attr_name)
            if isinstance(attr, TextClause):
                sql = str(attr.text)
                assert "account_provisioning_outbox" not in sql, (
                    f"SQL constant {attr_name} references dropped outbox table"
                )

    def test_no_in_memory_linked_pairs_dedup(self):
        """M3-required: in-memory dedup replaced by SQL-level ON CONFLICT.

        Asserts on the materialize function's source — we want the
        legacy `linked_pairs: set[...]` initialization gone. Test
        passes if the literal pattern is absent.
        """
        import inspect
        body = inspect.getsource(M.materialize_account_approval)
        # Old pattern declared this typed local at the top of the
        # function. The replacement comment ("dedup at the SQL layer")
        # contains the word "linked" but not the literal pattern.
        assert "linked_pairs: set" not in body
        assert "linked_pairs = set()" not in body

    def test_upsert_raw_interaction_uses_pk_conflict(self):
        sql = str(UPSERT_RAW_INTERACTION_SQL.text)
        assert "ON CONFLICT (interaction_id) DO NOTHING" in sql

    def test_upsert_summary_uses_unique_interaction_id_index(self):
        sql = str(UPSERT_PLACEHOLDER_SUMMARY_SQL.text)
        assert "ON CONFLICT (interaction_id) DO UPDATE" in sql

    def test_select_signals_excludes_archived(self):
        sql = str(SELECT_SIGNALS_SQL.text)
        assert "archived_at IS NULL" in sql

    def test_update_queue_sets_resolved_account_id(self):
        sql = str(UPDATE_QUEUE_SQL.text)
        assert "status = 'mapped'" in sql
        assert "resolved_account_id = :account_id" in sql


# ---------------------------------------------------------------------------
# Real-DB tests (skip when DATABASE_URL unset)
# ---------------------------------------------------------------------------


async def _seed_queue_with_signals(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    domain: str,
    signals: list[dict],
) -> tuple[str, list[str]]:
    """Seed a pending_account_mappings row + signals. Returns (queue_id, signal_ids)."""
    queue_id = str(uuid.uuid4())
    async with session.begin():
        await session.execute(
            text("""
                INSERT INTO pending_account_mappings (
                    id, tenant_id, domain, status, owner_user_id,
                    discovered_from_type, expires_at, email_count,
                    created_at, updated_at
                ) VALUES (
                    CAST(:id AS uuid), CAST(:tenant AS uuid), :domain, 'approved', CAST(:owner AS uuid),
                    'test', NOW() + INTERVAL '7 days', :count,
                    NOW(), NOW()
                )
            """),
            {
                "id": queue_id,
                "tenant": tenant_id,
                "domain": domain,
                "owner": user_id,
                "count": len(signals),
            },
        )
        signal_ids = []
        for s in signals:
            sid = str(uuid.uuid4())
            await session.execute(
                text("""
                    INSERT INTO pending_account_mapping_signals (
                        id, queue_id, tenant_id, source_type, source_user_id,
                        contact_email, contact_display_name, contact_role,
                        interaction_id, calendar_event_id, created_at
                    ) VALUES (
                        CAST(:id AS uuid), CAST(:queue_id AS uuid), CAST(:tenant AS uuid),
                        :source_type, CAST(:owner AS uuid),
                        :email, :name, :role,
                        :iid, :cid, NOW()
                    )
                """),
                {
                    "id": sid,
                    "queue_id": queue_id,
                    "tenant": tenant_id,
                    "source_type": s.get("source_type", "transcript"),
                    "owner": user_id,
                    "email": s["email"],
                    "name": s.get("name"),
                    "role": s.get("role", "attendee"),
                    "iid": s.get("interaction_id"),
                    "cid": s.get("calendar_event_id"),
                },
            )
            signal_ids.append(sid)
    return queue_id, signal_ids


async def _create_test_account(
    session: AsyncSession,
    *,
    tenant_id: str,
    name: str,
    domain: str,
) -> str:
    account_id = str(uuid.uuid4())
    async with session.begin():
        await session.execute(
            text("""
                INSERT INTO accounts (id, tenant_id, name, state, account_type, created_at, updated_at)
                VALUES (CAST(:id AS uuid), CAST(:tenant AS uuid), :name, 'active', 'Prospect', NOW(), NOW())
            """),
            {"id": account_id, "tenant": tenant_id, "name": name},
        )
        await session.execute(
            text("""
                INSERT INTO account_domains (id, tenant_id, account_id, domain, created_at)
                VALUES (gen_random_uuid(), CAST(:tenant AS uuid), CAST(:account AS uuid), :domain, NOW())
                ON CONFLICT (tenant_id, domain) DO NOTHING
            """),
            {"tenant": tenant_id, "account": account_id, "domain": domain},
        )
    return account_id


@pytest.mark.asyncio
async def test_materialize_empty_signals_fails_loud(
    session: AsyncSession, test_tenant_id: str, test_user_id: str,
):
    """Materializing a queue with no active signals raises ValueError."""
    queue_id, _ = await _seed_queue_with_signals(
        session,
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        domain="empty.example.com",
        signals=[],
    )
    account_id = await _create_test_account(
        session, tenant_id=test_tenant_id, name="EmptyCorp", domain="empty.example.com",
    )

    with pytest.raises(ValueError, match="no active signals"):
        async with session.begin():
            await materialize_account_approval(
                session=session,
                tenant_id=test_tenant_id,
                queue_id=queue_id,
                account_id=account_id,
                event_type="account_created",
            )


@pytest.mark.asyncio
async def test_materialize_single_signal_creates_contact(
    session: AsyncSession, test_tenant_id: str, test_user_id: str,
):
    queue_id, _ = await _seed_queue_with_signals(
        session,
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        domain="solocorp.example.com",
        signals=[{"email": "alice@solocorp.example.com", "name": "Alice Smith"}],
    )
    account_id = await _create_test_account(
        session, tenant_id=test_tenant_id, name="SoloCorp",
        domain="solocorp.example.com",
    )

    async with session.begin():
        result = await materialize_account_approval(
            session=session,
            tenant_id=test_tenant_id,
            queue_id=queue_id,
            account_id=account_id,
            event_type="account_created",
        )

    assert isinstance(result, MaterializationResult)
    assert result.account_id == account_id
    assert result.queue_id == queue_id
    assert len(result.contact_ids) == 1

    # Verify side effects:
    contact_row = (await session.execute(
        text("SELECT id::text, account_id::text, email FROM contacts WHERE tenant_id = CAST(:t AS uuid)"),
        {"t": test_tenant_id},
    )).one()
    assert contact_row.account_id == account_id
    assert contact_row.email == "alice@solocorp.example.com"

    # Queue row moved to 'mapped'.
    queue_row = (await session.execute(
        text("SELECT status, resolved_account_id::text FROM pending_account_mappings WHERE id = CAST(:q AS uuid)"),
        {"q": queue_id},
    )).one()
    assert queue_row.status == "mapped"
    assert queue_row.resolved_account_id == account_id


@pytest.mark.asyncio
async def test_materialize_replay_safe_link_insert(
    session: AsyncSession, test_tenant_id: str, test_user_id: str,
):
    """Running materialize twice for the same queue produces 1 link row, not 2.

    M3-required behavior: ON CONFLICT (interaction_id, contact_id) DO
    NOTHING against the M2 unique index. The previous implementation
    used in-memory ``linked_pairs`` which was replay-broken across
    DBOS step retries.
    """
    interaction_id = uuid.uuid4()
    # Seed an account that will be the raw_interactions FK anchor — Phase 1
    # invariant 2: raw_interactions.account_id is NOT NULL. Use the
    # eventual materialized account to keep FK happy without two-step.
    bootstrap_account_id = await _create_test_account(
        session, tenant_id=test_tenant_id, name="ReplayBootstrap",
        domain="replaycorp-bootstrap.example.com",
    )
    async with session.begin():
        await session.execute(
            text("""
                INSERT INTO raw_interactions (
                    interaction_id, tenant_id, account_id, interaction_type, created_at, updated_at
                ) VALUES (
                    CAST(:iid AS uuid), CAST(:t AS uuid), CAST(:account AS uuid), 'meeting', NOW(), NOW()
                )
            """),
            {
                "iid": interaction_id,
                "t": test_tenant_id,
                "account": bootstrap_account_id,
            },
        )

    queue_id, _ = await _seed_queue_with_signals(
        session,
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        domain="replaycorp.example.com",
        signals=[
            {
                "email": "rod@replaycorp.example.com",
                "name": "Rod Stewart",
                "interaction_id": str(interaction_id),
            }
        ],
    )
    account_id = await _create_test_account(
        session, tenant_id=test_tenant_id, name="ReplayCorp",
        domain="replaycorp.example.com",
    )

    # First materialization run.
    async with session.begin():
        result1 = await materialize_account_approval(
            session=session,
            tenant_id=test_tenant_id,
            queue_id=queue_id,
            account_id=account_id,
            event_type="account_created",
        )

    # Second materialization run (simulating a DBOS step retry).
    async with session.begin():
        result2 = await materialize_account_approval(
            session=session,
            tenant_id=test_tenant_id,
            queue_id=queue_id,
            account_id=account_id,
            event_type="account_created",
        )

    # Both return the same contact id (ON CONFLICT DO UPDATE preserves identity).
    assert result1.contact_ids == result2.contact_ids

    # Exactly ONE interaction_contact_links row exists for this (interaction_id, contact_id).
    link_count = (await session.execute(
        text("""
            SELECT COUNT(*) AS n FROM interaction_contact_links
            WHERE contact_id = CAST(:cid AS uuid)
        """),
        {"cid": result1.contact_ids[0]},
    )).one().n
    assert link_count == 1, f"expected 1 link row, found {link_count} (replay-safety broken)"


@pytest.mark.asyncio
async def test_materialize_cross_account_collision_raises(
    session: AsyncSession, test_tenant_id: str, test_user_id: str,
):
    """Contact already bound to another account → raise; do NOT silently misroute."""
    # Seed two accounts under the same tenant.
    acct_a = await _create_test_account(
        session, tenant_id=test_tenant_id, name="AccountA", domain="a.example.com",
    )
    acct_b = await _create_test_account(
        session, tenant_id=test_tenant_id, name="AccountB", domain="b.example.com",
    )
    # Pre-existing contact anchored to A.
    async with session.begin():
        await session.execute(
            text("""
                INSERT INTO contacts (id, tenant_id, email, account_id, source, validation_status, created_at, updated_at)
                VALUES (gen_random_uuid(), CAST(:t AS uuid), lower(:email), CAST(:acct AS uuid), 'test', 'verified', NOW(), NOW())
            """),
            {"t": test_tenant_id, "email": "shared@x.example.com", "acct": acct_a},
        )

    queue_id, _ = await _seed_queue_with_signals(
        session,
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        domain="x.example.com",
        signals=[{"email": "shared@x.example.com"}],
    )

    with pytest.raises(ValueError, match="already belongs"):
        async with session.begin():
            await materialize_account_approval(
                session=session,
                tenant_id=test_tenant_id,
                queue_id=queue_id,
                account_id=acct_b,
                event_type="account_created",
            )
