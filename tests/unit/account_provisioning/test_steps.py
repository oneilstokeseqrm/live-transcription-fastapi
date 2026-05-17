"""Real-substrate unit tests for ``services.account_provisioning.steps``.

Per plan §7.2 + Item 1 of test-discipline-gaps: each ``@DBOS.step``
function is exercised against a real Neon session (Option B: production
Neon, test-tenant scoped, mandatory teardown via conftest). DBOS step
decorators degrade to passthrough when called outside a workflow
context — the checkpointing is the workflow's concern, not the step's.

Scope:
- revalidate_queue_state: real SQL, real row presence + tenant + attempt_id drift detection.
- transition_to_creating: real SQL, idempotent on replay (status='creating' = no-op).
- resolve_or_create_account: real SQL, domain-keyed idempotency + race recovery.
- materialize_signals: thin wrapper over materialize_account_approval (tested in test_materialization).
- emit_eventbridge_events: covered by test_eventbridge_emit (mocked boto3).
- call_agent_enrich: covered by test_agent_client (mocked HTTP transport).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.account_provisioning.steps import (
    resolve_or_create_account,
    revalidate_queue_state,
    transition_to_creating,
)
from services.account_provisioning.types import AccountProfile


# All tests in this module use the ``session`` fixture, which writes to
# the shared production Neon test tenant and DELETEs on teardown. The
# shared-infrastructure-collision lesson (2026-05-16, tasks/lessons.md)
# means these tests must be opt-in via ``RUN_DESTRUCTIVE_TESTS=1``.
pytestmark = pytest.mark.requires_db_write


async def _seed_pending_account_mapping(
    session: AsyncSession,
    *,
    tenant_id: str,
    user_id: str,
    domain: str,
    status: str = "approved",
    approval_attempt_id: str | None = None,
) -> str:
    queue_id = str(uuid.uuid4())
    async with session.begin():
        await session.execute(
            text("""
                INSERT INTO pending_account_mappings (
                    id, tenant_id, domain, status, owner_user_id,
                    discovered_from_type, expires_at, email_count,
                    approval_attempt_id, created_at, updated_at
                ) VALUES (
                    CAST(:id AS uuid), CAST(:t AS uuid), :domain, :status, CAST(:owner AS uuid),
                    'test', NOW() + INTERVAL '7 days', 1,
                    :attempt, NOW(), NOW()
                )
            """),
            {
                "id": queue_id,
                "t": tenant_id,
                "domain": domain,
                "status": status,
                "owner": user_id,
                "attempt": approval_attempt_id,
            },
        )
    return queue_id


# ---------------------------------------------------------------------------
# revalidate_queue_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revalidate_returns_queue_state_for_valid_row(
    session: AsyncSession, test_tenant_id: str, test_user_id: str,
):
    attempt = str(uuid.uuid4())
    queue_id = await _seed_pending_account_mapping(
        session,
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        domain="valid.example.com",
        status="approved",
        approval_attempt_id=attempt,
    )

    state = await revalidate_queue_state(
        queue_id=queue_id,
        tenant_id=test_tenant_id,
        expected_approval_attempt_id=attempt,
    )

    assert state.queue_id == queue_id
    assert state.tenant_id == test_tenant_id
    assert state.domain == "valid.example.com"
    assert state.status == "approved"
    assert state.approval_attempt_id == attempt


@pytest.mark.asyncio
async def test_revalidate_raises_on_missing_row(test_tenant_id: str, session: AsyncSession):
    # session fixture ensures teardown even though the test itself doesn't write.
    nonexistent = str(uuid.uuid4())
    with pytest.raises(ValueError, match="no longer exists"):
        await revalidate_queue_state(
            queue_id=nonexistent,
            tenant_id=test_tenant_id,
            expected_approval_attempt_id=str(uuid.uuid4()),
        )


@pytest.mark.asyncio
async def test_revalidate_raises_on_tenant_drift(
    session: AsyncSession, test_tenant_id: str, test_user_id: str,
):
    """Phase 1 invariant 6: cross-tenant access is a hard error."""
    attempt = str(uuid.uuid4())
    queue_id = await _seed_pending_account_mapping(
        session,
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        domain="tenantdrift.example.com",
        approval_attempt_id=attempt,
    )

    other_tenant = "22222222-2222-4222-8222-222222222222"
    with pytest.raises(ValueError, match="tenant mismatch"):
        await revalidate_queue_state(
            queue_id=queue_id,
            tenant_id=other_tenant,
            expected_approval_attempt_id=attempt,
        )


@pytest.mark.asyncio
async def test_revalidate_raises_on_archived_row(
    session: AsyncSession, test_tenant_id: str, test_user_id: str,
):
    """Codex P2 finding 2026-05-15: /ignore between /approve and Step 1.

    If an operator archives the queue row via /ignore between the
    /approve handler starting the workflow and Step 1 actually running,
    Step 1 must fail loud — Step 5's UPDATE_QUEUE_SQL has no status
    filter and would otherwise flip the ignored row back to 'mapped'.
    """
    attempt = str(uuid.uuid4())
    queue_id = await _seed_pending_account_mapping(
        session,
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        domain="archived.example.com",
        status="ignored",
        approval_attempt_id=attempt,
    )
    # Mark the row archived (simulates /ignore having fired).
    async with session.begin():
        await session.execute(
            text("""
                UPDATE pending_account_mappings
                SET archived_at = NOW(), archive_reason = 'owner_ignored'
                WHERE id = CAST(:q AS uuid)
            """),
            {"q": queue_id},
        )

    with pytest.raises(ValueError, match="archived"):
        await revalidate_queue_state(
            queue_id=queue_id,
            tenant_id=test_tenant_id,
            expected_approval_attempt_id=attempt,
        )


@pytest.mark.asyncio
async def test_revalidate_raises_on_unexpected_status(
    session: AsyncSession, test_tenant_id: str, test_user_id: str,
):
    """If status drifted to 'mapped' (a racing /map fired), workflow refuses.

    Codex P2 finding 2026-05-15: revalidate must check status as well
    as archived_at — a status='mapped' row from a racing /map call
    shouldn't be re-materialized by the workflow.
    """
    attempt = str(uuid.uuid4())
    queue_id = await _seed_pending_account_mapping(
        session,
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        domain="raced.example.com",
        status="mapped",
        approval_attempt_id=attempt,
    )

    with pytest.raises(ValueError, match="status drift"):
        await revalidate_queue_state(
            queue_id=queue_id,
            tenant_id=test_tenant_id,
            expected_approval_attempt_id=attempt,
        )


@pytest.mark.asyncio
async def test_revalidate_raises_on_attempt_id_drift(
    session: AsyncSession, test_tenant_id: str, test_user_id: str,
):
    """Different attempt_id reserved the row between workflow start and Step 1."""
    queue_id = await _seed_pending_account_mapping(
        session,
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        domain="attemptdrift.example.com",
        approval_attempt_id=str(uuid.uuid4()),
    )

    workflow_attempt = str(uuid.uuid4())  # different from seeded
    with pytest.raises(ValueError, match="approval_attempt_id drift"):
        await revalidate_queue_state(
            queue_id=queue_id,
            tenant_id=test_tenant_id,
            expected_approval_attempt_id=workflow_attempt,
        )


# ---------------------------------------------------------------------------
# transition_to_creating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transition_to_creating_moves_approved_to_creating(
    session: AsyncSession, test_tenant_id: str, test_user_id: str,
):
    queue_id = await _seed_pending_account_mapping(
        session,
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        domain="transitioncorp.example.com",
        status="approved",
    )

    await transition_to_creating(queue_id=queue_id)

    row = (await session.execute(
        text("""
            SELECT status, creation_started_at FROM pending_account_mappings
            WHERE id = CAST(:q AS uuid)
        """),
        {"q": queue_id},
    )).one()
    assert row.status == "creating"
    assert row.creation_started_at is not None


@pytest.mark.asyncio
async def test_transition_to_creating_raises_on_archived_race(
    session: AsyncSession, test_tenant_id: str, test_user_id: str,
):
    """Codex P2 finding 2026-05-15: race between Step 1 and Step 2.

    If /ignore archives the row between Step 1 (revalidate succeeded
    because not-yet-archived) and Step 2 (UPDATE), the UPDATE matches
    0 rows. Without a 0-row guard, the workflow would proceed to Step
    3 (30-90s agent call) then Step 5 (materialize) which would flip
    the ignored row back to 'mapped' (UPDATE_QUEUE_SQL has no status
    filter).
    """
    queue_id = await _seed_pending_account_mapping(
        session,
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        domain="raceabort.example.com",
        status="approved",
    )
    # Simulate /ignore having just fired.
    async with session.begin():
        await session.execute(
            text("""
                UPDATE pending_account_mappings
                SET status='ignored', archived_at=NOW()
                WHERE id = CAST(:q AS uuid)
            """),
            {"q": queue_id},
        )

    with pytest.raises(ValueError, match="drifted out of"):
        await transition_to_creating(queue_id=queue_id)


@pytest.mark.asyncio
async def test_transition_to_creating_is_idempotent_on_replay(
    session: AsyncSession, test_tenant_id: str, test_user_id: str,
):
    """Calling twice doesn't bounce status or re-stamp creation_started_at."""
    queue_id = await _seed_pending_account_mapping(
        session,
        tenant_id=test_tenant_id,
        user_id=test_user_id,
        domain="idempot.example.com",
        status="approved",
    )

    await transition_to_creating(queue_id=queue_id)
    started_first = (await session.execute(
        text("SELECT creation_started_at FROM pending_account_mappings WHERE id = CAST(:q AS uuid)"),
        {"q": queue_id},
    )).scalar_one()

    # Simulate a replay.
    await transition_to_creating(queue_id=queue_id)
    started_second = (await session.execute(
        text("SELECT status, creation_started_at FROM pending_account_mappings WHERE id = CAST(:q AS uuid)"),
        {"q": queue_id},
    )).one()

    assert started_second.status == "creating"
    # COALESCE preserves the first creation_started_at.
    assert started_second.creation_started_at == started_first


# ---------------------------------------------------------------------------
# resolve_or_create_account
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_creates_new_account_and_domain_binding(
    session: AsyncSession, test_tenant_id: str,
):
    domain = "newresolve.example.com"
    profile = AccountProfile(name="NewResolve Inc", domain=domain, industry="SaaS")

    account_id = await resolve_or_create_account(
        tenant_id=test_tenant_id,
        domain=domain,
        profile=profile,
    )

    # accounts row exists.
    acct = (await session.execute(
        text("SELECT name, industry FROM accounts WHERE id = CAST(:id AS uuid)"),
        {"id": account_id},
    )).one()
    assert acct.name == "NewResolve Inc"
    assert acct.industry == "SaaS"

    # account_domains binding exists.
    bind = (await session.execute(
        text("SELECT account_id::text FROM account_domains WHERE tenant_id = CAST(:t AS uuid) AND domain = :d"),
        {"t": test_tenant_id, "d": domain},
    )).one()
    assert bind.account_id == account_id


@pytest.mark.asyncio
async def test_resolve_returns_existing_account_for_known_domain(
    session: AsyncSession, test_tenant_id: str,
):
    """Idempotency anchor: account_domains.(tenant_id, domain). Replay returns same id."""
    domain = "knownresolve.example.com"
    profile = AccountProfile(name="KnownResolve", domain=domain)

    first = await resolve_or_create_account(
        tenant_id=test_tenant_id, domain=domain, profile=profile,
    )

    # Replay: should return the SAME account_id without creating a duplicate.
    second = await resolve_or_create_account(
        tenant_id=test_tenant_id, domain=domain, profile=profile,
    )
    assert first == second

    # Exactly one accounts row + one account_domains binding for this domain.
    acct_count = (await session.execute(
        text("""
            SELECT COUNT(*) AS n FROM account_domains
            WHERE tenant_id = CAST(:t AS uuid) AND lower(domain) = lower(:d)
        """),
        {"t": test_tenant_id, "d": domain},
    )).scalar_one()
    assert acct_count == 1


@pytest.mark.asyncio
async def test_resolve_is_case_insensitive_on_domain(
    session: AsyncSession, test_tenant_id: str,
):
    """account_domains query uses lower(); replay with different casing resolves to same id."""
    domain = "CaseTest.example.com"
    profile = AccountProfile(name="CaseTest")

    first = await resolve_or_create_account(
        tenant_id=test_tenant_id, domain=domain, profile=profile,
    )
    second = await resolve_or_create_account(
        tenant_id=test_tenant_id, domain=domain.upper(), profile=profile,
    )
    assert first == second
