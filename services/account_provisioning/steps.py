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
    emit_email_promoted_for_materialization,
    emit_for_materialization_result,
)
from services.account_provisioning.materialization import (
    materialize_account_approval,
)
from services.account_provisioning.types import (
    AccountProfile,
    AgentEnrichTerminalError,
    EmissionRecord,
    MaterializationResult,
    QueueState,
)
from services.agent_action_core_client import AgentActionCoreClient
from services.database import get_async_session
from services.tenant_scope import tenant_session

logger = logging.getLogger(__name__)


_AGENT_RUN_EVENT_KEY = "agent_enrich_run"


SELECT_QUEUE_STATE_SQL = text("""
    SELECT id::text AS queue_id,
           tenant_id::text AS tenant_id,
           domain,
           status,
           approval_attempt_id::text AS approval_attempt_id,
           archived_at,
           re_open_count
    FROM pending_account_mappings
    WHERE id = CAST(:queue_id AS uuid)
""")
# SQLAlchemy 2.0.49 parses ``:name::uuid`` as a truncated bindname
# (one char dropped at the second-to-last colon). The CAST(:name AS
# uuid) form is the portable workaround for explicit casts.


TRANSITION_TO_CREATING_SQL = text("""
    UPDATE pending_account_mappings
    SET status = 'creating',
        creation_started_at = COALESCE(creation_started_at, NOW()),
        updated_at = NOW()
    WHERE id = CAST(:queue_id AS uuid)
      AND archived_at IS NULL
      AND status IN ('approved', 'creating')
    RETURNING status
""")
# Replay-safe AND race-tight: status IN ('approved','creating') lets a
# legitimate replay (status='creating' from prior partial run) pass; the
# archived_at filter blocks the race where /ignore archives the row
# between Step 1's revalidate and Step 2's UPDATE. The step body checks
# rowcount: 0 rows = the row drifted into archived/mapped/ignored state
# between Step 1 and now → raise to abort the workflow before Step 5
# would flip the queue back to 'mapped'.


SELECT_ACCOUNT_BY_DOMAIN_SQL = text("""
    SELECT account_id::text
    FROM account_domains
    WHERE tenant_id = CAST(:tenant_id AS uuid) AND lower(domain) = lower(:domain)
""")


INSERT_ACCOUNT_SQL = text("""
    INSERT INTO accounts (
        id, tenant_id, name, state, account_type,
        industry, company_size, region, website, description,
        ai_workflow_trigger, created_at, updated_at
    ) VALUES (
        CAST(:id AS uuid), CAST(:tenant_id AS uuid), :name, 'active', 'Prospect',
        :industry, :company_size, :region, :website, :description,
        false, NOW(), NOW()
    )
""")


INSERT_ACCOUNT_DOMAIN_SQL = text("""
    INSERT INTO account_domains (id, tenant_id, account_id, domain, created_at)
    VALUES (gen_random_uuid(), CAST(:tenant_id AS uuid), CAST(:account_id AS uuid),
            lower(:domain), NOW())
    ON CONFLICT (tenant_id, domain) DO NOTHING
    RETURNING account_id::text
""")


# Note: fetch helpers (SELECT_INTERACTIONS_FOR_EMIT_SQL,
# SELECT_CONTACTS_FOR_INTERACTION_SQL, fetch_interactions_for_emit) moved
# to services.account_provisioning.eventbridge_emit so the /map route's
# inline emission path can reuse them without an indirect dependency on
# the DBOS step decorators in this module.


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
    # Codex P2 finding 2026-05-15: between /approve starting the workflow
    # and Step 1 running, an operator could call /ignore (which archives
    # the row). Step 2's UPDATE_QUEUE_SQL would no-op (status != 'approved')
    # but Step 5's UPDATE_QUEUE_SQL inside materialize_account_approval
    # has no status filter — it would flip the ignored row back to
    # 'mapped'. Fail loud at Step 1 so the workflow surfaces the race.
    if row.archived_at is not None:
        raise ValueError(
            f"Queue {queue_id!r} was archived (archived_at={row.archived_at}) "
            f"between /approve and workflow start. The /ignore route fired "
            f"between the two. The workflow refuses to materialize an "
            f"archived row."
        )
    if row.status not in ("approved", "creating"):
        raise ValueError(
            f"Queue {queue_id!r} status drift: expected 'approved' or "
            f"'creating' at workflow start, got {row.status!r}. A racing "
            f"action (e.g., /ignore, /map) ran between /approve and Step 1."
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

    Codex P2 finding 2026-05-15: detect the 0-row case so /ignore
    archiving the row between Step 1's revalidate and Step 2's UPDATE
    doesn't silently fall through to Step 5 (which would flip the
    ignored row back to 'mapped' via materialize's UPDATE_QUEUE_SQL).

    1-row match: first-time transition OR legitimate replay
    (status='creating' from a prior partial run). Either is fine.

    0-row match: row was archived, ignored, mapped, or otherwise moved
    out of ('approved','creating') between Step 1 and now. The
    workflow MUST NOT proceed to Step 3 (agent call) because Step 5's
    materialization has no status guard and would clobber the queue's
    terminal state.
    """
    async with get_async_session() as session:
        async with session.begin():
            result = await session.execute(
                TRANSITION_TO_CREATING_SQL, {"queue_id": queue_id}
            )
            if result.one_or_none() is None:
                raise ValueError(
                    f"Queue {queue_id!r} drifted out of ('approved','creating') "
                    f"between Step 1 (revalidate) and Step 2 (transition). A "
                    f"racing /ignore, /map, or expiry-sweep fired in the "
                    f"window. The workflow aborts to avoid clobbering the "
                    f"row's terminal state in Step 5."
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
    # Codex P0 2026-05-16: DBOS 2.x has both sync (get_event/set_event)
    # and async (get_event_async/set_event_async) variants. The sync
    # methods raise RuntimeError when called from a running event loop —
    # which is exactly the context an async @DBOS.step runs in. Use
    # the _async variants.
    cached: Optional[dict] = await DBOS.get_event_async(
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
            await DBOS.set_event_async(_AGENT_RUN_EVENT_KEY, {"run_id": run_id})

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
    winner's account_id and rolls back its own ``accounts`` insert in
    a fresh session.
    """
    # Fast-path read in its own short transaction — avoids holding a
    # row-locking transaction open across the agent profile inspection.
    # EQ-120: tenant_session pins app.tenant_id and OWNS the transaction so the
    # RLS-armed account_domains read doesn't fail closed in prod (replaces the
    # explicit session.begin()).
    async with tenant_session(tenant_id) as read_session:
        existing = (
            await read_session.execute(
                SELECT_ACCOUNT_BY_DOMAIN_SQL,
                {"tenant_id": tenant_id, "domain": domain},
            )
        ).one_or_none()
    if existing is not None:
        return existing.account_id

    # Write path. INSERT accounts + INSERT account_domains in one
    # transaction; race-loser raises ``_DomainRaceLost`` which rolls
    # back the accounts insert via SQLAlchemy's begin() context-manager
    # exit-on-exception behavior, then we re-resolve in a fresh session.
    account_id = str(uuid.uuid4())
    # EQ-120: tenant_session pins app.tenant_id and OWNS the transaction (commits
    # on clean exit, rolls back on exception — same as the prior session.begin())
    # so the RLS-armed accounts + account_domains writes don't fail closed in prod.
    try:
        async with tenant_session(tenant_id) as write_session:
            await write_session.execute(
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
                await write_session.execute(
                    INSERT_ACCOUNT_DOMAIN_SQL,
                    {
                        "tenant_id": tenant_id,
                        "account_id": account_id,
                        "domain": domain,
                    },
                )
            ).one_or_none()
            if domain_row is None:
                raise _DomainRaceLost()
            return account_id
    except _DomainRaceLost:
        # Race: another concurrent provisioning won the domain bind.
        # Our accounts insert was rolled back; resolve to the winner.
        # EQ-120: tenant_session pins app.tenant_id and OWNS the transaction so
        # the RLS-armed account_domains read doesn't fail closed in prod.
        async with tenant_session(tenant_id) as resolve_session:
            row = (
                await resolve_session.execute(
                    SELECT_ACCOUNT_BY_DOMAIN_SQL,
                    {"tenant_id": tenant_id, "domain": domain},
                )
            ).one()
            return row.account_id


class _DomainRaceLost(Exception):
    """Internal sentinel: rolls back the write txn so we can re-resolve."""


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
    # EQ-120: tenant_session pins app.tenant_id and OWNS the transaction
    # (materialize_account_approval does NOT commit; the block commits on clean
    # exit, rolls back on exception — same as the prior session.begin()) so the
    # RLS-armed contacts writes inside it don't fail closed in prod.
    async with tenant_session(tenant_id) as session:
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


@DBOS.step(retries_allowed=True, max_attempts=5, interval_seconds=2.0, backoff_rate=2.0)
async def emit_eventbridge_events(
    *,
    materialization: MaterializationResult,
) -> list[EmissionRecord]:
    """Emit one EnvelopeV1 per materialized interaction.

    Plan §6.6 + §3.3. Wraps the shared
    :func:`services.account_provisioning.eventbridge_emit.emit_for_materialization_result`
    helper so the workflow and the /map inline path share the same
    fetch + fan-out logic. Consumer-side MERGE is the dedup mechanism;
    DBOS retries are safe at the consumer.
    """
    return await emit_for_materialization_result(materialization=materialization)


# ---------------------------------------------------------------------------
# Phase-1-email-pipeline M2 — EmailPromoted emit step (plan §5.4)
# ---------------------------------------------------------------------------


@DBOS.step(retries_allowed=True, max_attempts=5, interval_seconds=2.0, backoff_rate=2.0)
async def emit_email_promoted_events(
    *,
    materialization: MaterializationResult,
) -> list[EmissionRecord]:
    """Emit one EmailPromoted EventBridge event per promoted interaction.

    Plan §5.4 + §6. Notifies eq-email-pipeline that a cold-inbound email
    was just promoted to ``emails`` and needs its local enrichment
    pipeline run retroactively (Neo4j flesh + LLM extraction + Pinecone
    embed + thread summary). The handler-side two-layer idempotency
    guard (``local_enrichment_started_at`` 5-min soft TTL +
    ``local_enrichment_completed_at`` hard marker) makes DBOS step
    retries replay-safe at the consumer.

    Empty list when ``promoted_interaction_ids`` is empty (legacy
    meeting-only approval): the step is a cheap no-op for queues that
    had no cold-inbound emails attached.

    Appended at the END of the workflow per DBOS plan §6.8 (appending
    steps at end is safe under deploy-while-in-flight; mid-workflow
    insertions are not).
    """
    return await emit_email_promoted_for_materialization(materialization=materialization)
