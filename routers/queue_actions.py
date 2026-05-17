"""Queue action routes: Approve / Map / Ignore (Task 1.5.11).

Three HTTP endpoints that drive the human-approval surface of Phase 1.5:

- `POST /queue/{id}/approve` — transitions a pending_account_mappings row
  to `status='approved'`. The worker (Task 1.5.7) picks it up to
  materialize contacts via the agent.

- `POST /queue/{id}/map` — inline-materializes against an existing
  account_id, skipping the worker hop. Used when the operator already
  knows which account this queue entry should resolve to.

- `POST /queue/{id}/ignore` — archives the queue entry so it no longer
  appears in the inbox.

Auth boundary:
- 401 for missing/bad JWT (`get_auth_context_polling` enforces).
- 404 for nonexistent entries OR cross-tenant access (no existence leak).
- 403 for in-tenant non-owner callers (admin escalation reserved for
  `status='tenant_review'`, see `services/queue_authorization.py`).
- 409 for actions on archived (terminal) entries — see Codex P1 #2 fix.

Idempotency:
- /approve and /map both use a client-supplied `approval_attempt_id`
  (UUID v4). The SQL UPDATE only fires if the existing row's
  `approval_attempt_id` is NULL or matches the request — so replays of the
  same call are safe.
- /map additionally re-SELECTs after a 0-row reservation to discriminate
  between "already mapped with our same intent" (200) and "mapped with a
  different account / attempt_id" (409).
- /ignore is idempotent on already-archived rows — replay returns 200
  without re-executing IGNORE_SQL (would re-stamp ignored_at).

Tenant boundary:
- /map verifies the target account_id belongs to the caller's tenant
  INSIDE the route transaction, BEFORE materialization. Without this
  check, a caller could attach contacts to a foreign tenant's account
  because contacts.account_id FK references accounts(id) without a
  compound tenant_id constraint (see Codex P1 #1).

Polling-style auth (no X-Account-ID required): queue actions don't anchor
to a per-account header. The MAP route accepts an `account_id` in the
request BODY for inline materialization — that account_id is then
tenant-scoped via SELECT_ACCOUNT_FOR_TENANT_SQL.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

from services.database import get_async_session
from services.queue_authorization import can_act_on_queue_entry
from utils.context_utils import get_auth_context_polling
from services.account_provisioning.eventbridge_emit import (
    emit_for_materialization_result,
)
from services.account_provisioning.materialization import materialize_account_approval
from services.account_provisioning.workflow import (
    APPROVAL_QUEUE,
    account_provisioning_workflow,
)


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/queue", tags=["queue"])


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


def _validate_uuid_field(field_name: str):
    """Pydantic v2 validator factory — rejects non-UUID strings at the API
    boundary so the SQL binder never sees garbage, AND canonicalizes valid
    UUIDs to their lowercase, brace-free, hyphenated form via
    `str(uuid.UUID(v))`.

    Without canonicalization, raw string equality on body fields
    (`current.approval_attempt_id == body.approval_attempt_id`) FAILS for
    valid retries that send a non-canonical form (uppercase, braced),
    because the DB-stored value is canonical (Postgres's `::uuid` cast
    normalizes). A same-attempt-id retry would receive 409 instead of the
    contractually-required 200. See Codex Round 5 P2 #2.

    Without UUID validation, malformed UUIDs raised 500 errors from
    Postgres's UUID cast (`:attempt_id::uuid`) instead of clean 422
    validation errors. See Codex P2 #4.
    """
    def _validator(v: str) -> str:
        try:
            return str(uuid.UUID(v))
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"{field_name} must be a valid UUID")
    return _validator


class ApproveRequest(BaseModel):
    """Body for `POST /queue/{id}/approve`."""
    approval_attempt_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Client-supplied UUID v4. Same value on retry → idempotent. "
            "Persisted in pending_account_mappings.approval_attempt_id."
        ),
    )

    _validate_attempt_id = field_validator("approval_attempt_id")(
        _validate_uuid_field("approval_attempt_id")
    )


class MapRequest(BaseModel):
    """Body for `POST /queue/{id}/map`."""
    account_id: str = Field(..., min_length=1)
    approval_attempt_id: str = Field(..., min_length=1)

    _validate_account_id = field_validator("account_id")(
        _validate_uuid_field("account_id")
    )
    _validate_attempt_id = field_validator("approval_attempt_id")(
        _validate_uuid_field("approval_attempt_id")
    )


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------


# Projected columns must match what _fake_queue_row exposes in the tests
# and what the route handlers read (.tenant_id, .owner_user_id, .status,
# .approval_attempt_id, .resolved_account_id, .archived_at, ._mapping for
# the auth helper).
#
# archived_at is included so _load_and_authorize can reject actions on
# terminal (ignored/archived) entries (Codex P1 #2).
SELECT_QUEUE_SQL = text("""
    SELECT id::text AS id,
           tenant_id::text AS tenant_id,
           owner_user_id::text AS owner_user_id,
           status,
           approval_attempt_id::text AS approval_attempt_id,
           resolved_account_id::text AS resolved_account_id,
           archived_at,
           re_open_count
    FROM pending_account_mappings
    WHERE id = :queue_id
""")
# re_open_count is read for the workflow's input (logged in Step 1; not
# load-bearing — workflow_id strategy keys on approval_attempt_id, not
# re_open_count).


# Idempotency: WHERE matches when NO attempt_id has been recorded yet OR
# the SAME attempt_id is recorded. Different attempt_id → noop (RETURNING
# returns zero rows; the handler re-SELECTs to decide 200 vs 409).
#
# Codex Round 2 P1 #3: status filter pins the transition source. Without
# the filter, /approve replays with the same attempt_id on a row that has
# since moved to status='mapped' (worker materialized it) would flip the
# row back to 'approved' — re-queueing it for the worker → duplicate
# materialization + duplicate outbox event. The `status IN ('pending',
# 'approved')` clause allows first-approve AND same-attempt_id idempotent
# replay on a still-pending row, but stops mutating mapped/creating/
# ignored rows. The 0-row branch in approve_entry already re-SELECTs and
# returns 200 if the row is in ('approved', 'creating', 'mapped'), so this
# is purely defensive: the SQL stops mutating; the handler decides what
# 0-row means.
#
# `archived_at IS NULL` is the same defensive belt-and-suspenders: the
# auth helper already blocks archived rows with 409, but a future code
# path that bypasses _load_and_authorize must NOT be able to resurrect an
# archived entry.
APPROVE_SQL = text("""
    UPDATE pending_account_mappings
    SET status = 'approved',
        approval_attempt_id = CAST(:attempt_id AS uuid),
        updated_at = NOW()
    WHERE id = :queue_id
      AND archived_at IS NULL
      AND status IN ('pending', 'approved')
      AND (approval_attempt_id IS NULL OR approval_attempt_id = CAST(:attempt_id AS uuid))
    RETURNING id::text
""")
# SQLAlchemy 2.0.49 parses the bindname in ``:attempt_id::uuid`` as
# ``attempt_i`` (one char truncation at the second-to-last colon), so
# the route's ``{"attempt_id": ...}`` kwarg never bound. The CAST(...)
# form is the portable workaround. Pre-existing P1 fixed during M3.


# Codex Round 3 P2 #2: status filter prevents /ignore from overwriting
# successfully-mapped or in-flight rows. Pre-fix, a stale or crafted POST
# /queue/{id}/ignore after /map (status='mapped') or while the worker was
# processing (status='creating') silently flipped status to 'ignored' — the
# contacts + outbox rows from materialization stayed put, so queue state
# lied. Post-fix: terminal/in-flight rows noop (RETURNING 0 rows), and the
# handler re-SELECTs to return 409 for mapped/creating or fall through to
# the idempotent 200 for already-ignored.
#
# Codex Round 4 P2 #2: `AND archived_at IS NULL` filter prevents /ignore
# from overwriting rows archived by other flows. The expiry sweeper
# (Task 1.5.12) sets archived_at without necessarily changing status to
# 'ignored'. Pre-fix, a sweeper-archived pending row would fall through to
# IGNORE_SQL because the early /ignore short-circuit only fires on
# status='ignored', and IGNORE_SQL had no archived_at filter — so the
# UPDATE clobbered the sweeper's archive_reason with 'owner_ignored' and
# overwrote ignored_at + ignored_by. Post-fix: any non-NULL archived_at
# (regardless of who archived it) makes IGNORE_SQL noop (0 rows) and the
# handler returns 200 idempotently without further mutation.
#
# RETURNING id::text is required so the handler can detect 0-row noops via
# .one_or_none() without depending on result.rowcount (which has varying
# semantics across async drivers).
IGNORE_SQL = text("""
    UPDATE pending_account_mappings
    SET status = 'ignored',
        ignored_at = NOW(),
        ignored_by = CAST(:user_id AS uuid),
        archived_at = NOW(),
        archive_reason = 'owner_ignored',
        updated_at = NOW()
    WHERE id = :queue_id
      AND archived_at IS NULL
      AND status NOT IN ('mapped', 'creating', 'ignored')
    RETURNING id::text
""")


# Codex Round 6 P1 #1: archive child signals whenever /ignore archives the
# parent. Without this, a future signal for the same domain that reopens
# the parent (services/pending_account_mappings.REOPEN_PARENT_SQL clears
# the parent's archived_at but leaves children untouched) would let the
# worker's SELECT_SIGNALS_SQL (workers/materialization.py:19) return BOTH
# the previously-dismissed signals AND the new one — re-materializing
# contacts the user explicitly dismissed.
#
# Scoping: signals are scoped to a queue_id, and _load_and_authorize has
# already verified the queue belongs to the caller's tenant. So tenant
# isolation is transitive through queue_id; no tenant_id filter is needed
# here (and adding one would couple this query to schema assumptions about
# whether signals carry tenant_id directly).
#
# The `AND archived_at IS NULL` filter is belt-and-suspenders: idempotent
# replays won't re-stamp signals already archived by an earlier /ignore.
ARCHIVE_SIGNALS_SQL = text("""
    UPDATE pending_account_mapping_signals
    SET archived_at = NOW()
    WHERE queue_id = :queue_id
      AND archived_at IS NULL
""")


# Tenant-scoped account lookup (Codex P1 #1). Without this, a caller could
# attach contacts to a foreign tenant's account because the contacts
# table's FK on account_id references accounts(id) only — there is no
# compound (tenant_id, account_id) constraint.
SELECT_ACCOUNT_FOR_TENANT_SQL = text("""
    SELECT id::text FROM accounts
    WHERE id = CAST(:account_id AS uuid) AND tenant_id = CAST(:tenant_id AS uuid)
""")


# /map idempotency reservation (Codex P2 #3). Mirrors APPROVE_SQL but
# scoped to non-archived rows and binding the attempt_id. Caller checks
# the RETURNING result: 1 row → proceed to materialize, 0 rows → re-SELECT
# to discriminate replay (200) vs different intent (409).
#
# Codex Round 2 P1 #4: status filter prevents replays from re-materializing.
# After /map commits, the row is status='mapped', resolved_account_id=<X>,
# approval_attempt_id=<id>. A retry with the SAME attempt_id would
# otherwise re-match WHERE → return 1 row → handler calls
# materialize_account_approval AGAIN → duplicate outbox + duplicate
# interaction_contact_links.
#
# Codex Round 3 P1 #1: tighten to status='pending' (positive list) rather
# than a negative list. Pre-fix the negative list allowed 'approved' to pass,
# but worker (workers/account_provisioning_worker.process_one_approved_entry)
# takes an advisory lock and processes status='approved' rows — /map does NOT
# take that lock. Concurrent /map + worker on the same approved row → two
# parallel materialize_account_approval calls → duplicate outbox rows +
# duplicate links + possibly different resolved accounts. /map is the
# "I know the account, skip the AI worker" path; once a row is /approve'd,
# the worker owns it. The 0-row branch in map_entry handles the conflict
# via re-SELECT → returns replay-success 200 if status='mapped' with matching
# resolved_account_id, else 409.
MAP_RESERVE_SQL = text("""
    UPDATE pending_account_mappings
    SET approval_attempt_id = CAST(:attempt_id AS uuid),
        updated_at = NOW()
    WHERE id = :queue_id
      AND archived_at IS NULL
      AND status = 'pending'
      AND (approval_attempt_id IS NULL OR approval_attempt_id = CAST(:attempt_id AS uuid))
    RETURNING id::text
""")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _load_and_authorize(
    session,
    queue_id: str,
    *,
    tenant_id: str,
    user_id: str,
    actionable_only: bool = True,
) -> dict:
    """Load the queue row, enforce tenant + ownership, return its mapping.

    `actionable_only` (default True): reject archived rows with 409 Conflict
    — the entry exists but is terminal (ignored/archived) and no longer
    accepts state-changing actions. Pass `actionable_only=False` from
    /ignore so an idempotent replay against an already-ignored entry
    short-circuits cleanly.

    Returns the row as a plain dict for the caller. Raises HTTPException
    on 404 (not found or cross-tenant), 403 (in-tenant non-owner), or 409
    (archived & actionable_only=True).
    """
    result = await session.execute(SELECT_QUEUE_SQL, {"queue_id": queue_id})
    row = result.one_or_none()

    if row is None:
        # Entry truly does not exist.
        raise HTTPException(status_code=404, detail="Queue entry not found")

    if row.tenant_id != tenant_id:
        # Cross-tenant lookup: deliberately mirror the "not found" response
        # so we don't leak existence across tenants.
        logger.warning(
            "Cross-tenant queue access blocked: queue_id=%s, request_tenant=%s, "
            "row_tenant=%s",
            queue_id, tenant_id, row.tenant_id,
        )
        raise HTTPException(status_code=404, detail="Queue entry not found")

    # SQLAlchemy 1.x/2.x rows expose ._mapping for dict-style access; the
    # authorization helper takes a Mapping so route handlers can pass either
    # the raw row mapping or a plain dict.
    row_mapping = dict(row._mapping)

    # is_admin=False until admin escalation lands (design Section 8.7).
    if not can_act_on_queue_entry(
        user_id=user_id, queue_entry=row_mapping, is_admin=False,
    ):
        logger.info(
            "Queue action forbidden for non-owner: queue_id=%s, user_id=%s, "
            "owner_user_id=%s, status=%s",
            queue_id, user_id, row.owner_user_id, row.status,
        )
        raise HTTPException(status_code=403, detail="Not authorized for this queue entry")

    if actionable_only and row.archived_at is not None:
        # Codex P1 #2: actions on archived rows still succeeded prior to
        # this check, so a follow-up /map or /approve after /ignore would
        # materialize contacts + an outbox row for an item the user
        # explicitly dismissed. 409 (not 404) because the entry IS findable
        # — it's just terminal.
        logger.info(
            "Queue action rejected on archived entry: queue_id=%s, "
            "archived_at=%s, archive_reason=%s",
            queue_id, row.archived_at, row_mapping.get("archive_reason"),
        )
        raise HTTPException(
            status_code=409,
            detail="Queue entry is archived and no longer actionable",
        )

    return row_mapping


def _validate_uuid_path_param(value: str, field_name: str) -> str:
    """Reject non-UUID queue_ids at the boundary so SQL never sees garbage."""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=400, detail=f"{field_name} must be a valid UUID",
        )
    return value


def _effective_user_id(ctx) -> str:
    """Return pg_user_id when present, falling back to the JWT subject.

    Codex Round 2 P1 #1: Queue rows are inserted with
    `owner_user_id = context.pg_user_id or context.user_id` (see
    routers/text.py:101 and routers/batch.py:164). The owner_user_id
    column is UUID NOT NULL in eq-dev. When pg_user_id is present (the
    standard production case), the column stores a UUID — NOT the Auth0
    subject string like "auth0|<sub>".

    Pre-fix: the auth helper compared ctx.user_id (Auth0 subject) against
    row.owner_user_id (UUID) → never matched → 403 for the legitimate
    owner. Post-fix: the auth helper compares the EFFECTIVE user id —
    mirroring the insert pattern. First-owner-wins on owner_user_id stays
    intact; we just match the insert format on read.

    This also satisfies P1 #2: IGNORE_SQL binds `:user_id::uuid`. When
    pg_user_id is present, _effective_user_id returns the UUID and the
    cast succeeds. When pg_user_id is absent AND user_id is non-UUID,
    /ignore early-rejects with 400 before opening the DB transaction so
    the cast error never surfaces.
    """
    return ctx.pg_user_id or ctx.user_id


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/{queue_id}/approve", status_code=202)
async def approve_entry(queue_id: str, body: ApproveRequest, request: Request):
    """Approve a queue entry and start the account-provisioning workflow.

    Two-phase: reserve the queue row synchronously inside the route's
    transaction (status='approved' + approval_attempt_id stamped), THEN
    enqueue the DBOS workflow on the ``APPROVAL_QUEUE`` after the txn
    commits.

    The workflow ID is ``f"queue-{queue_id}:approval-{approval_attempt_id}"``
    (plan §6.2), so:

    - Replays of the same `/approve` call with the same approval_attempt_id
      return 202 and DBOS deduplicates the workflow start (the existing
      handle is returned).
    - Reopen lifecycles produce a NEW approval_attempt_id → distinct
      workflow ID → fresh workflow run, no collision.

    Idempotency: replaying with the same `approval_attempt_id` returns 202.
    Calling with a different `approval_attempt_id` on a row that's already
    been approved returns 202 if the row IS in approved/creating/mapped
    (idempotent satisfaction of "this queue entry is approved"), 409
    otherwise.
    """
    from dbos import SetWorkflowID

    ctx = get_auth_context_polling(request)
    _validate_uuid_path_param(queue_id, "queue_id")

    re_open_count = 0
    enqueue_workflow = False
    # The winning attempt_id is what the workflow ID is keyed on. For
    # first-approve + same-attempt replays, this equals the request's
    # attempt_id. For different-attempt replays on an already-approved
    # row, this comes from the row's recorded approval_attempt_id so
    # the caller receives the REAL workflow_id (not a phantom keyed on
    # their losing attempt_id) — Codex P2 finding 2026-05-16.
    winning_attempt_id = body.approval_attempt_id

    async with get_async_session() as session:
        async with session.begin():
            await _load_and_authorize(
                session, queue_id,
                tenant_id=ctx.tenant_id,
                user_id=_effective_user_id(ctx),
            )

            update_result = await session.execute(
                APPROVE_SQL,
                {"queue_id": queue_id, "attempt_id": body.approval_attempt_id},
            )
            updated_row = update_result.one_or_none()

            if updated_row is not None:
                # 1 row → either first approve, or replay with same
                # attempt_id. Re-read for the workflow's input
                # (re_open_count is logged but not load-bearing — Step 1
                # re-reads it from the DB on workflow start).
                fresh = (await session.execute(
                    SELECT_QUEUE_SQL, {"queue_id": queue_id},
                )).one()
                re_open_count = fresh.re_open_count
                enqueue_workflow = True
            else:
                # 0 rows → either (a) row got an approval with a DIFFERENT
                # attempt_id, or (b) row vanished between SELECT and UPDATE.
                # Re-SELECT to discriminate.
                re_result = await session.execute(SELECT_QUEUE_SQL, {"queue_id": queue_id})
                current = re_result.one_or_none()
                if current is None:
                    raise HTTPException(status_code=404, detail="Queue entry not found")

                if current.status not in ("approved", "creating", "mapped"):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Approve conflict: queue entry is in "
                            f"status='{current.status}' with a different "
                            f"approval_attempt_id recorded. Reload the queue "
                            f"entry and retry with the latest state."
                        ),
                    )
                # Idempotent satisfaction: the queue is approved (or
                # beyond) under SOME attempt_id. We do NOT enqueue a
                # second workflow — SetWorkflowID would dedupe on the
                # winner's attempt_id anyway. Return 202 with the
                # WINNING workflow_id so the caller can poll real
                # DBOS state.
                if current.approval_attempt_id:
                    winning_attempt_id = current.approval_attempt_id

    # Workflow start AFTER the route's transaction commits. Plan §5.3:
    # this ordering preserves the contract that the row is durably
    # 'approved' before any background work begins.
    #
    # Stranding window: if the process dies between commit and
    # enqueue_async, the row is 'approved' with no workflow. The
    # natural recovery is client-side: same-attempt retry hits the
    # APPROVE_SQL replay path (1-row match), re-enters this code, and
    # re-enqueues. For non-retrying clients OR permanent enqueue
    # failure, an orphan-approval reaper is needed — tracked under
    # plan §9 Phase-2 trigger conditions. Codex P1 2026-05-16.
    workflow_id = f"queue-{queue_id}:approval-{winning_attempt_id}"
    if enqueue_workflow:
        # SetWorkflowID makes the enqueue idempotent on retries of the
        # same (queue_id, approval_attempt_id) pair. Concurrent
        # /approve calls with the same attempt_id produce the same
        # workflow_id; DBOS deduplicates.
        try:
            with SetWorkflowID(workflow_id):
                await APPROVAL_QUEUE.enqueue_async(
                    account_provisioning_workflow,
                    queue_id=str(queue_id),
                    tenant_id=ctx.tenant_id,
                    approval_attempt_id=body.approval_attempt_id,
                    re_open_count=re_open_count,
                )
        except Exception as exc:
            # Enqueue failure AFTER the row was reserved. Surface as
            # 503 so the client retries; the same-attempt retry will
            # replay through APPROVE_SQL (1-row match) and re-attempt
            # the enqueue. The row stays in 'approved' state — that's
            # the desired durability anchor.
            logger.exception(
                "DBOS enqueue failed AFTER /approve committed reservation; "
                "queue_id=%s attempt_id=%s workflow_id=%s. Row remains in "
                "status='approved'; client should retry with the same "
                "attempt_id.",
                queue_id, body.approval_attempt_id, workflow_id,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "Queue entry reserved but workflow enqueue failed. "
                    "Retry the same /approve call with the same "
                    "approval_attempt_id; reservation is replay-safe."
                ),
            ) from exc

    return {
        "status": "approved",
        "queue_id": queue_id,
        "workflow_id": workflow_id,
        "approval_attempt_id": body.approval_attempt_id,
    }


@router.post("/{queue_id}/map")
async def map_entry(queue_id: str, body: MapRequest, request: Request):
    """Inline-materialize a queue entry against an existing account_id.

    Skips the worker hop: this handler runs the materialization in the
    same transaction as the auth check, so the contact rows + outbox row
    are durable before the response returns.

    Tenant boundary: the `account_id` in the body MUST belong to the
    caller's tenant. We verify with SELECT_ACCOUNT_FOR_TENANT_SQL before
    materialization. Without this, a caller with knowledge of another
    tenant's UUID could attach contacts to a foreign account because
    contacts.account_id FK has no compound tenant_id constraint
    (Codex P1 #1).

    Idempotency: MAP_RESERVE_SQL applies the same NULL-or-equal
    approval_attempt_id pattern as APPROVE_SQL. Replay with the same
    attempt_id is safe; a different attempt_id on an already-mapped row
    returns 409 (Codex P2 #3).

    `materialize_account_approval` validates that signals exist; calling
    map on a queue entry with zero active signals will 500 (architecturally
    invalid state — see workers/materialization.py).
    """
    ctx = get_auth_context_polling(request)
    _validate_uuid_path_param(queue_id, "queue_id")
    _validate_uuid_path_param(body.account_id, "account_id")

    is_replay = False  # set in the 0-row branch when this is a replay

    async with get_async_session() as session:
        async with session.begin():
            await _load_and_authorize(
                session, queue_id,
                tenant_id=ctx.tenant_id,
                user_id=_effective_user_id(ctx),
            )

            # P1 #1: tenant-scoped account lookup. Returning 404 (not 403)
            # on cross-tenant accounts mirrors the cross-tenant queue
            # behaviour: don't leak existence across tenants.
            acct_result = await session.execute(
                SELECT_ACCOUNT_FOR_TENANT_SQL,
                {"account_id": body.account_id, "tenant_id": ctx.tenant_id},
            )
            if acct_result.one_or_none() is None:
                logger.info(
                    "Map blocked: account_id=%s not in tenant=%s (or does not exist)",
                    body.account_id, ctx.tenant_id,
                )
                raise HTTPException(status_code=404, detail="Account not found")

            # P2 #3: idempotency reservation. The UPDATE WHERE clause is:
            #   archived_at IS NULL
            #   AND (approval_attempt_id IS NULL OR = :attempt_id)
            # A first-attempt OR same-attempt_id replay passes through.
            # Different attempt_id → 0 rows → re-SELECT to discriminate.
            reserve = await session.execute(
                MAP_RESERVE_SQL,
                {"queue_id": queue_id, "attempt_id": body.approval_attempt_id},
            )
            if reserve.one_or_none() is None:
                # 0 rows — re-SELECT to discriminate.
                current_result = await session.execute(
                    SELECT_QUEUE_SQL, {"queue_id": queue_id},
                )
                current = current_result.one_or_none()
                if current is None:
                    raise HTTPException(status_code=404, detail="Queue entry not found")

                # Codex Round 4 P2 #3: replay-success requires ALL THREE
                # of (status='mapped', resolved_account_id matches request,
                # AND approval_attempt_id matches request). Pre-fix the
                # attempt_id was not checked, so Bob retrying Alice's
                # earlier-failed /map with Bob's own attempt_id but the
                # same account_id falsely received 200 — as if Bob's call
                # drove the map. Per the idempotency contract, only the
                # SAME attempt_id should get 200; a different attempt_id
                # on an already-mapped row is a different intent → 409.
                if (current.status == "mapped"
                        and current.resolved_account_id == body.account_id
                        and current.approval_attempt_id == body.approval_attempt_id):
                    # Codex P1 2026-05-16: replay must re-attempt
                    # emission. The ORIGINAL /map may have committed
                    # materialization then failed at emit (transient
                    # EventBridge outage). Without a re-attempt,
                    # downstream consumers would never see the event
                    # even though the API reports success.
                    # Reconstruct the MaterializationResult from DB
                    # state (queue's signals + mapped account) and
                    # re-emit. At-least-once + consumer MERGE = dedup.
                    materialization = await _materialization_for_replay(
                        session=session,
                        queue_id=queue_id,
                        tenant_id=ctx.tenant_id,
                        account_id=body.account_id,
                    )
                    # Defer the actual emit + return to the post-txn
                    # block at the bottom of the route. Marker:
                    # the only way ``materialization`` is set without
                    # going through reservation is the replay branch.
                    is_replay = True
                else:
                    # Codex Round 3 P1 #1 + Round 4 P2 #3: explicit
                    # conflict messaging. /map only operates on pending
                    # entries with a matching attempt_id for replay.
                    # Approved entries are owned by the workflow;
                    # mapped entries with a different attempt_id signal
                    # a different caller intent.
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Map conflict: queue entry is in status='{current.status}' "
                            f"with a different approval_attempt_id or resolved_account_id "
                            f"recorded. /map only operates on pending entries; mapped "
                            f"entries replay successfully ONLY when the same "
                            f"approval_attempt_id + account_id are presented. Reload and "
                            f"retry."
                        ),
                    )
            else:
                # Reservation succeeded — inline-materialize.
                # UPDATE_QUEUE_SQL inside materialize_account_approval
                # sets status='mapped' + resolved_account_id. The
                # context manager auto-commits on clean exit (or rolls
                # back if materialize_account_approval raises).
                materialization = await materialize_account_approval(
                    session=session,
                    tenant_id=ctx.tenant_id,
                    queue_id=queue_id,
                    account_id=body.account_id,
                    event_type="account_mapped",
                )

    # Emit downstream EnvelopeV1 events for the backfilled interactions
    # AFTER the route's transaction commits (so emission failures cannot
    # roll back the contacts + queue update). Plan §6.6 + Codex P1
    # 2026-05-15: pre-fix /map skipped emission entirely because the
    # outbox + publisher pair was deleted in M3.
    #
    # Codex P1 2026-05-16: on emission failure, raise 503 rather than
    # swallow + return 200. The DB state is durable ('mapped'); a
    # same-attempt retry replays the entire /map flow including the
    # replay-emit path above, giving downstream consumers another
    # chance to receive the event. Returning 200 with no event silently
    # breaks the contract that the consumer learns about every
    # materialized interaction.
    try:
        await emit_for_materialization_result(materialization=materialization)
    except Exception as exc:
        logger.exception(
            "Inline /map emission failed AFTER materialization committed; "
            "queue_id=%s account_id=%s is_replay=%s. DB state is correct "
            "(status='mapped'); raising 503 so the client retries — replay "
            "will reconstruct the MaterializationResult from DB state and "
            "re-emit.",
            queue_id, body.account_id, is_replay,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Queue entry mapped; downstream emission failed. Retry "
                "the same /map call with the same approval_attempt_id; "
                "the replay path re-emits."
            ),
        ) from exc

    return {
        "status": "mapped",
        "queue_id": queue_id,
        "account_id": body.account_id,
    }


# ---------------------------------------------------------------------------
# /map replay materialization reconstruction
# ---------------------------------------------------------------------------


REPLAY_INTERACTION_IDS_SQL = text("""
    SELECT DISTINCT interaction_id::text AS interaction_id
    FROM pending_account_mapping_signals
    WHERE queue_id = CAST(:queue_id AS uuid)
      AND interaction_id IS NOT NULL
""")


REPLAY_CONTACT_IDS_SQL = text("""
    SELECT DISTINCT c.id::text AS contact_id
    FROM contacts c
    JOIN pending_account_mapping_signals s
        ON lower(s.contact_email) = lower(c.email)
        AND s.tenant_id = c.tenant_id
    WHERE s.queue_id = CAST(:queue_id AS uuid)
      AND c.account_id = CAST(:account_id AS uuid)
      AND c.tenant_id = CAST(:tenant_id AS uuid)
""")


async def _materialization_for_replay(
    *,
    session,
    queue_id: str,
    tenant_id: str,
    account_id: str,
):
    """Reconstruct ``MaterializationResult`` from DB state for /map replay.

    A same-attempt /map replay can land on an already-mapped row; the
    original call may have committed materialization but failed
    emission. To re-emit, we need the contact_ids + interaction_ids
    the original call produced. We derive them from the queue's
    signals (which are the source of truth for what the original
    materialize call processed) joined against contacts under the
    mapped account.
    """
    from services.account_provisioning.types import MaterializationResult

    interaction_rows = await session.execute(
        REPLAY_INTERACTION_IDS_SQL, {"queue_id": queue_id},
    )
    interaction_ids = [r.interaction_id for r in interaction_rows.all()]

    contact_rows = await session.execute(
        REPLAY_CONTACT_IDS_SQL,
        {
            "queue_id": queue_id,
            "account_id": account_id,
            "tenant_id": tenant_id,
        },
    )
    contact_ids = [r.contact_id for r in contact_rows.all()]

    return MaterializationResult(
        queue_id=queue_id,
        tenant_id=tenant_id,
        account_id=account_id,
        contact_ids=contact_ids,
        interaction_ids=interaction_ids,
    )


@router.post("/{queue_id}/ignore")
async def ignore_entry(queue_id: str, request: Request):
    """Archive a queue entry (status='ignored', archived_at=NOW).

    Idempotent: a replay against an already-archived/ignored row returns
    200 without re-executing IGNORE_SQL (which would re-stamp ignored_at).
    """
    ctx = get_auth_context_polling(request)
    _validate_uuid_path_param(queue_id, "queue_id")

    # Codex Round 2 P1 #2: IGNORE_SQL binds `ignored_by = :user_id::uuid`.
    # The queue row's owner_user_id is itself a UUID (inserted from
    # `pg_user_id or user_id`). If the caller has neither a pg_user_id nor
    # a UUID-shaped user_id, the cast in Postgres would raise → 500. Reject
    # cleanly with 400 here: a JWT without a UUID-shaped identifier could
    # not have created this queue row in the first place, so this branch
    # is itself anomalous. Surfacing as 400 rather than 500 lets callers
    # diagnose the JWT shape mismatch.
    effective_id = _effective_user_id(ctx)
    try:
        uuid.UUID(effective_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=400,
            detail="ignore requires a UUID-shaped user identifier (pg_user_id)",
        )

    async with get_async_session() as session:
        async with session.begin():
            # Pass actionable_only=False so we can short-circuit on
            # already-archived rows (idempotency) instead of raising 409.
            row = await _load_and_authorize(
                session, queue_id,
                tenant_id=ctx.tenant_id,
                user_id=effective_id,
                actionable_only=False,
            )

            if row.get("archived_at") is not None and row.get("status") == "ignored":
                # Already archived AND status='ignored' — idempotent noop.
                # Don't re-execute IGNORE_SQL (would re-stamp ignored_at).
                logger.info(
                    "Ignore replay on already-archived entry: queue_id=%s",
                    queue_id,
                )
                return {"status": "ignored", "queue_id": queue_id}

            # Codex Round 3 P2 #2: IGNORE_SQL filters
            # `status NOT IN ('mapped', 'creating', 'ignored')` and uses
            # RETURNING. Codex Round 4 P2 #2: IGNORE_SQL also filters
            # `archived_at IS NULL` so rows archived by other flows
            # (e.g. the expiry sweeper) are never overwritten. If the
            # UPDATE matches 0 rows, the entry is in one of:
            #   (a) terminal mapped/creating — 409
            #   (b) already-archived (by ANY flow) — 200 idempotent
            #   (c) vanished — 404
            ignore_result = await session.execute(
                IGNORE_SQL,
                {"queue_id": queue_id, "user_id": effective_id},
            )
            ignored_row = ignore_result.one_or_none()
            if ignored_row is not None:
                # Codex Round 6 P1 #1: parent archived successfully. Cascade
                # to child signals in the SAME transaction so a future
                # reopen + worker materialization doesn't re-consume the
                # dismissed contacts. Parent → children order matches the
                # logical lifecycle (parent decision first, then ripple to
                # children); both write paths atomically commit-or-rollback
                # together since they share `async with session.begin()`.
                #
                # Why ONLY on the 1-row branch: we deliberately do NOT
                # cascade on the 0-row paths (mapped/creating → 409;
                # already-archived → 200 idempotent). For mapped/creating,
                # the materializer already consumed the signals — re-archiving
                # is meaningless. For sweeper-archived rows, the signals may
                # have been archived under a different archive_reason and
                # re-stamping their archived_at would lose audit fidelity.
                await session.execute(
                    ARCHIVE_SIGNALS_SQL,
                    {"queue_id": queue_id},
                )
                return {"status": "ignored", "queue_id": queue_id}

            # 0 rows — re-SELECT to discriminate the cases.
            current = (await session.execute(
                SELECT_QUEUE_SQL, {"queue_id": queue_id},
            )).one_or_none()
            if current is None:
                raise HTTPException(
                    status_code=404, detail="Queue entry not found",
                )

            # Codex Round 4 P2 #2: ANY archived row (regardless of who
            # archived it — owner_ignored, expiry sweeper, etc.) is
            # idempotently a 200. /ignore on an already-archived row is
            # a no-op; do NOT mutate. The early /ignore short-circuit at
            # the top of this handler only fires on status='ignored',
            # so a sweeper-archived pending row reaches here. We MUST
            # NOT return 409 (operators didn't do anything wrong) and
            # MUST NOT re-run IGNORE_SQL (would overwrite archive_reason).
            if current.archived_at is not None:
                logger.info(
                    "Ignore noop on already-archived entry: "
                    "queue_id=%s, status=%s, archive_reason=%s",
                    queue_id, current.status,
                    current._mapping.get("archive_reason"),
                )
                return {"status": "ignored", "queue_id": queue_id}

            if current.status in ("mapped", "creating"):
                logger.info(
                    "Ignore blocked on terminal/in-flight entry: "
                    "queue_id=%s, status=%s",
                    queue_id, current.status,
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot ignore queue entry in status='{current.status}'; "
                        f"materialization already succeeded or is in progress."
                    ),
                )

            # Defensive: status that doesn't match terminal AND
            # archived_at IS NULL but IGNORE_SQL still 0-rowed. This
            # shouldn't happen with the current schema; surface as
            # 200 so callers can move on. If we hit this in
            # production, the log line below will be the breadcrumb.
            logger.warning(
                "Ignore noop with unexpected state: queue_id=%s, status=%s",
                queue_id, current.status,
            )
            return {"status": "ignored", "queue_id": queue_id}

    return {"status": "ignored", "queue_id": queue_id}
