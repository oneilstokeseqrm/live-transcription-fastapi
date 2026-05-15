"""DBOS workflow steps for account provisioning (Phase 1.5 M3).

Each ``@DBOS.step`` function is a single side-effecting operation that
DBOS checkpoints to ``dbos.operation_outputs``. On replay (workflow
resumes after crash), DBOS reads the cached output instead of re-running
the step — so each step's external effect must be idempotent OR cheap
to re-run.

Step retry policy is per plan §6.1:

| Step                       | retries | rationale                                          |
|----------------------------|---------|----------------------------------------------------|
| revalidate_queue_state     | off     | read-only; replays are free, no retry needed       |
| transition_to_creating     | off     | idempotent SQL (WHERE status='approved' is no-op)  |
| call_agent_enrich          | 5 × 2^n | 30-90s network call; transient failures normal     |
| resolve_or_create_account  | 3 × 2^n | DB transient only; DB constraint = terminal        |
| materialize_signals        | 3 × 2^n | all ON CONFLICT; DB transient only                 |
| emit_eventbridge_events    | 5 × 2^n | at-least-once; consumer-side MERGE dedupes         |

Plan §6 + §7.4 (narrow exception handling).
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from dbos import DBOS
from sqlalchemy import text

from services.account_provisioning.eventbridge_emit import (
    emit_envelopes_for_materialization,
)
from services.account_provisioning.materialization import (
    materialize_account_approval,
)
from services.account_provisioning.types import (
    AccountProfile,
    AgentEnrichTerminalError,
    EmissionRecord,
    EmittedContact,
    InteractionForEmit,
    MaterializationResult,
    QueueState,
)
from services.agent_action_core_client import AgentActionCoreClient
from services.database import get_async_session

logger = logging.getLogger(__name__)


_AGENT_RUN_EVENT_KEY = "agent_enrich_run"


SELECT_QUEUE_STATE_SQL = text("""
    SELECT id::text AS queue_id,
           tenant_id::text AS tenant_id,
           domain,
           status,
           approval_attempt_id::text AS approval_attempt_id,
           re_open_count
    FROM pending_account_mappings
    WHERE id = :queue_id::uuid
""")


TRANSITION_TO_CREATING_SQL = text("""
    UPDATE pending_account_mappings
    SET status = 'creating',
        creation_started_at = COALESCE(creation_started_at, NOW()),
        updated_at = NOW()
    WHERE id = :queue_id::uuid AND status = 'approved'
""")
# Replay-safe: a retry after status moved to 'creating' is a no-op
# (WHERE status='approved' matches nothing). If status moved past
# 'creating' (e.g., 'mapped' from /map racing), the workflow's later
# materialize step's UPDATE_QUEUE_SQL is the next state transition;
# this step doesn't need to assert pre-state beyond 'approved'.


SELECT_ACCOUNT_BY_DOMAIN_SQL = text("""
    SELECT account_id::text
    FROM account_domains
    WHERE tenant_id = :tenant_id::uuid AND lower(domain) = lower(:domain)
""")


INSERT_ACCOUNT_SQL = text("""
    INSERT INTO accounts (
        id, tenant_id, name, state, account_type,
        industry, company_size, region, website, description,
        ai_workflow_trigger, created_at, updated_at
    ) VALUES (
        :id::uuid, :tenant_id::uuid, :name, 'active', 'Prospect',
        :industry, :company_size, :region, :website, :description,
        false, NOW(), NOW()
    )
""")


INSERT_ACCOUNT_DOMAIN_SQL = text("""
    INSERT INTO account_domains (id, tenant_id, account_id, domain, created_at)
    VALUES (gen_random_uuid(), :tenant_id::uuid, :account_id::uuid,
            lower(:domain), NOW())
    ON CONFLICT (tenant_id, domain) DO NOTHING
    RETURNING account_id::text
""")


SELECT_INTERACTIONS_FOR_EMIT_SQL = text("""
    SELECT interaction_id::text AS interaction_id,
           interaction_type,
           raw_text,
           user_id::text AS user_id,
           created_at
    FROM raw_interactions
    WHERE interaction_id = ANY(:interaction_ids::uuid[])
      AND tenant_id = :tenant_id::uuid
""")
# tenant_id filter is belt-and-suspenders. The interaction_ids list comes
# from materialize_signals' MaterializationResult, which itself only wrote
# rows under the tenant — but cross-tenant defense in depth is cheap.


SELECT_CONTACTS_FOR_INTERACTION_SQL = text("""
    SELECT c.id::text AS contact_id,
           c.email,
           CASE
             WHEN c.first_name IS NOT NULL AND c.last_name IS NOT NULL
                  THEN c.first_name || ' ' || c.last_name
             WHEN c.first_name IS NOT NULL THEN c.first_name
             WHEN c.last_name IS NOT NULL THEN c.last_name
             ELSE NULL
           END AS display_name,
           s.contact_role AS role
    FROM interaction_contact_links l
    JOIN interaction_summaries summ ON summ.summary_id = l.interaction_id
    JOIN contacts c ON c.id = l.contact_id
    LEFT JOIN pending_account_mapping_signals s
           ON s.queue_id = :queue_id::uuid
          AND lower(s.contact_email) = lower(c.email)
          AND s.archived_at IS NULL
    WHERE summ.interaction_id = :raw_interaction_id::uuid
      AND c.tenant_id = :tenant_id::uuid
""")
# Joins via summaries because interaction_contact_links.interaction_id
# stores summary_id (Prisma naming artifact, see tasks/lessons.md). The
# signal role lookup is LEFT JOIN — if the queue's signals were already
# archived (reopen path) the role just comes back NULL. Downstream
# consumers tolerate role=None.


# ---------------------------------------------------------------------------
# Step 1: re-validate queue state
# ---------------------------------------------------------------------------


@DBOS.step()
async def revalidate_queue_state(
    *,
    queue_id: str,
    tenant_id: str,
    expected_approval_attempt_id: str,
) -> QueueState:
    """Read the queue row and re-confirm preconditions.

    The route's ``/approve`` handler reserved the row synchronously
    (status='approved' + approval_attempt_id stamped) BEFORE starting
    this workflow. Step 1 confirms the row still exists, is in the
    right tenant, and carries the expected attempt_id. A drift here
    (tenant moved, attempt_id replaced) is a hard error.
    """
    async with get_async_session() as session:
        row = (
            await session.execute(SELECT_QUEUE_STATE_SQL, {"queue_id": queue_id})
        ).one_or_none()

    if row is None:
        raise ValueError(
            f"Queue entry {queue_id!r} no longer exists at workflow start"
        )
    if row.tenant_id != tenant_id:
        raise ValueError(
            f"Queue {queue_id!r} tenant mismatch: workflow has "
            f"{tenant_id!r}; row has {row.tenant_id!r}"
        )
    if row.approval_attempt_id != expected_approval_attempt_id:
        raise ValueError(
            f"Queue {queue_id!r} approval_attempt_id drift: workflow has "
            f"{expected_approval_attempt_id!r}; row has "
            f"{row.approval_attempt_id!r}. A different /approve call "
            f"reserved this row between the workflow start and Step 1."
        )

    return QueueState(
        queue_id=row.queue_id,
        tenant_id=row.tenant_id,
        domain=row.domain,
        status=row.status,
        approval_attempt_id=row.approval_attempt_id,
        re_open_count=row.re_open_count,
    )


# ---------------------------------------------------------------------------
# Step 2: transition status approved → creating
# ---------------------------------------------------------------------------


@DBOS.step()
async def transition_to_creating(*, queue_id: str) -> None:
    """Idempotent ``status='approved' → 'creating'`` transition.

    The WHERE status='approved' clause makes this a no-op when the row
    is already in 'creating' (replay). The route reserved status
    synchronously, so on first run the row WILL be 'approved' here.
    """
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                TRANSITION_TO_CREATING_SQL, {"queue_id": queue_id}
            )


# ---------------------------------------------------------------------------
# Step 3: agent enrichment
# ---------------------------------------------------------------------------


def _build_agent_client() -> AgentActionCoreClient:
    base = os.environ.get(
        "AGENT_ACTION_CORE_BASE_URL",
        "https://eq-agent-action-core-production.up.railway.app",
    )
    return AgentActionCoreClient(base_url=base)


def _mint_internal_jwt(tenant_id: str) -> str:
    """Internal JWT for agent calls (HS256, ``INTERNAL_JWT_SECRET``).

    Phase 1 codified `tenant_id` claim discipline; the agent reads it for
    tenant isolation. Imported lazily to keep this module testable without
    pulling auth utilities in.
    """
    import jwt

    secret = os.environ.get("INTERNAL_JWT_SECRET")
    if not secret:
        raise AgentEnrichTerminalError(
            "INTERNAL_JWT_SECRET is unset; cannot mint internal JWT for "
            "eq-agent-action-core. Configure the Railway env var."
        )
    payload = {
        "iss": "eq-frontend",
        "aud": "eq-backend",
        "tenant_id": tenant_id,
        "user_id": "account-provisioning-workflow",
    }
    return jwt.encode(payload, secret, algorithm="HS256")


@DBOS.step(retries_allowed=True, max_attempts=5, interval_seconds=2.0, backoff_rate=2.0)
async def call_agent_enrich(
    *,
    tenant_id: str,
    domain: str,
    effort: str = "medium",
) -> AccountProfile:
    """Call eq-agent-action-core ``/api/enrich`` and return the profile.

    Crash-recovery strategy (plan §6.4): on the first call, the result
    is cached via ``DBOS.set_event`` keyed by ``_AGENT_RUN_EVENT_KEY``.
    If the workflow crashes after the enrich completes but before the
    step's success is durably checkpointed, the retry first checks the
    cached event — if the agent returned a ``run_id`` in its response
    body's ``extras`` (or a future field we control), the retry can
    short-circuit to ``GET /api/enrich/{run_id}`` instead of paying for
    a second 30-90s enrich. For M3, the cache is best-effort; absent a
    cached ``run_id``, the retry simply re-issues POST. Cost: 30-90s of
    redundant enrich on a crash window. Correctness: preserved.
    """
    cached: Optional[dict] = await DBOS.get_event(  # type: ignore[func-returns-value]
        DBOS.workflow_id, _AGENT_RUN_EVENT_KEY, timeout_seconds=0
    )
    client = _build_agent_client()
    jwt_token = _mint_internal_jwt(tenant_id)
    try:
        if cached and cached.get("run_id"):
            try:
                return await client.get_run(run_id=cached["run_id"], jwt=jwt_token)
            except Exception as exc:  # noqa: BLE001
                # If GET-by-run_id fails for any reason (agent doesn't
                # remember the run, transient error), fall through to
                # POST. Correctness preserved at the cost of one extra
                # enrich. Don't narrow further here — any failure is OK
                # to recover via POST.
                logger.warning(
                    "GET /api/enrich/{run_id} failed (run_id=%s); falling back "
                    "to POST: %s",
                    cached["run_id"], exc,
                )

        profile = await client.enrich(url=domain, effort=effort, jwt=jwt_token)

        # Cache best-effort. AccountProfile.extra ``run_id`` is allowed
        # by extra="allow"; if the agent eventually exposes a run_id
        # field, this picks it up.
        run_id = None
        try:
            run_id = profile.model_dump().get("run_id")
        except Exception:  # noqa: BLE001
            run_id = None
        if run_id:
            await DBOS.set_event(_AGENT_RUN_EVENT_KEY, {"run_id": run_id})

        return profile
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Step 4: resolve or create account (domain-keyed idempotency)
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=1.0, backoff_rate=2.0)
async def resolve_or_create_account(
    *,
    tenant_id: str,
    domain: str,
    profile: AccountProfile,
) -> str:
    """Resolve the account_id for ``(tenant_id, domain)``, creating if absent.

    Idempotency anchor: ``account_domains.(tenant_id, domain)`` UNIQUE
    INDEX (plan §3.1 + §6.4). Replay-safe: a second invocation finds
    the existing binding and returns the same account_id without
    inserting twice.

    Race-safe: two concurrent workflows for the same ``(tenant_id,
    domain)`` (unlikely given ``pending_account_mappings.(tenant_id,
    domain)`` UNIQUE INDEX gating, but possible) — one wins the
    ``account_domains`` insert; the other's INSERT gets the ON CONFLICT
    DO NOTHING branch (returns 0 rows), then re-SELECTs to fetch the
    winner's account_id and rolls back its own ``accounts`` insert.
    """
    async with get_async_session() as session:
        # Fast path: domain already bound. Avoids creating an orphan
        # accounts row in the rare case the workflow ran once, was
        # retried, but the prior run's account row was rolled back
        # before checkpointing.
        existing = (
            await session.execute(
                SELECT_ACCOUNT_BY_DOMAIN_SQL,
                {"tenant_id": tenant_id, "domain": domain},
            )
        ).one_or_none()
        if existing is not None:
            return existing.account_id

        async with session.begin():
            account_id = str(uuid.uuid4())
            await session.execute(
                INSERT_ACCOUNT_SQL,
                {
                    "id": account_id,
                    "tenant_id": tenant_id,
                    "name": profile.name,
                    "industry": profile.industry,
                    "company_size": profile.company_size,
                    "region": profile.region,
                    "website": profile.website,
                    "description": profile.description,
                },
            )
            domain_row = (
                await session.execute(
                    INSERT_ACCOUNT_DOMAIN_SQL,
                    {
                        "tenant_id": tenant_id,
                        "account_id": account_id,
                        "domain": domain,
                    },
                )
            ).one_or_none()

            if domain_row is None:
                # Race: another concurrent provisioning won the domain
                # bind. Roll back our accounts insert and resolve to
                # the winner.
                await session.rollback()
                winner = (
                    await session.execute(
                        SELECT_ACCOUNT_BY_DOMAIN_SQL,
                        {"tenant_id": tenant_id, "domain": domain},
                    )
                ).one()
                return winner.account_id

            return account_id


# ---------------------------------------------------------------------------
# Step 5: materialize signals
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=1.0, backoff_rate=2.0)
async def materialize_signals(
    *,
    tenant_id: str,
    queue_id: str,
    account_id: str,
) -> MaterializationResult:
    """Run materialization under a single transaction.

    Wraps :func:`services.account_provisioning.materialization.materialize_account_approval`
    so the existing SQL primitives (with M3's outbox-removed +
    ON CONFLICT-on-link changes) are reused without duplication.
    """
    async with get_async_session() as session:
        async with session.begin():
            return await materialize_account_approval(
                session=session,
                tenant_id=tenant_id,
                queue_id=queue_id,
                account_id=account_id,
                event_type="account_created",
            )


# ---------------------------------------------------------------------------
# Step 6: emit EventBridge events
# ---------------------------------------------------------------------------


async def _fetch_interactions_for_emit(
    *,
    materialization: MaterializationResult,
) -> list[InteractionForEmit]:
    """Read raw_interactions + contact metadata for each materialized interaction."""
    if not materialization.interaction_ids:
        return []

    interactions: list[InteractionForEmit] = []
    async with get_async_session() as session:
        rows = (
            await session.execute(
                SELECT_INTERACTIONS_FOR_EMIT_SQL,
                {
                    "interaction_ids": materialization.interaction_ids,
                    "tenant_id": materialization.tenant_id,
                },
            )
        ).all()
        for row in rows:
            contact_rows = (
                await session.execute(
                    SELECT_CONTACTS_FOR_INTERACTION_SQL,
                    {
                        "raw_interaction_id": row.interaction_id,
                        "queue_id": materialization.queue_id,
                        "tenant_id": materialization.tenant_id,
                    },
                )
            ).all()
            contacts = [
                EmittedContact(
                    contact_id=c.contact_id,
                    email=c.email,
                    name=c.display_name,
                    role=c.role,
                )
                for c in contact_rows
            ]
            interactions.append(
                InteractionForEmit(
                    interaction_id=row.interaction_id,
                    interaction_type=row.interaction_type,
                    raw_text=row.raw_text,
                    user_id=row.user_id,
                    created_at=row.created_at,
                    contacts=contacts,
                )
            )
    return interactions


@DBOS.step(retries_allowed=True, max_attempts=5, interval_seconds=2.0, backoff_rate=2.0)
async def emit_eventbridge_events(
    *,
    materialization: MaterializationResult,
) -> list[EmissionRecord]:
    """Emit one EnvelopeV1 per materialized interaction.

    Plan §6.6 + §3.3. Consumer-side MERGE is the dedup mechanism;
    DBOS retries are safe at the consumer.
    """
    interactions = await _fetch_interactions_for_emit(materialization=materialization)
    return await emit_envelopes_for_materialization(
        materialization=materialization,
        interactions=interactions,
    )
