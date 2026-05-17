"""Account provisioning DBOS workflow (Phase 1.5 M3).

Defines the workflow function. The ``/approve`` route (M4) starts an
instance via ``SetWorkflowID(f"queue-{queue_id}:approval-{approval_attempt_id}")``
+ ``APPROVAL_QUEUE.enqueue_async``. At end of M3 the workflow is dead
code — no route references it.

Workflow recovery: DBOS replays each step from its checkpoint. Step
side effects must be idempotent OR cheap to repeat. See plan §6 and
``services/account_provisioning/steps.py`` docstring.
"""

from __future__ import annotations

import logging
from typing import Literal

from dbos import DBOS, Queue

from services.account_provisioning.steps import (
    call_agent_enrich,
    emit_eventbridge_events,
    materialize_signals,
    resolve_or_create_account,
    revalidate_queue_state,
    transition_to_creating,
)
from services.account_provisioning.types import (
    AccountProvisioningResult,
)

logger = logging.getLogger(__name__)


# Concurrency cap for in-flight workflows. Plan §6.7 picks 5 — well
# above expected steady state (100-1000 approvals/day → ~10/hour peak)
# but bounded enough to keep agent calls and DB load predictable
# during onboarding bursts.
APPROVAL_QUEUE = Queue("account-provisioning", concurrency=5)


_EffortLevel = Literal["low", "medium", "high"]


@DBOS.workflow()
async def account_provisioning_workflow(
    *,
    queue_id: str,
    tenant_id: str,
    approval_attempt_id: str,
    re_open_count: int = 0,
    effort: _EffortLevel = "medium",
) -> AccountProvisioningResult:
    """Provision an account for an approved pending_account_mappings row.

    Steps:
      1. Revalidate queue state.
      2. Transition status 'approved' → 'creating'.
      3. Call agent /api/enrich (retried with run_id cache).
      4. Resolve or create account (account_domains-keyed idempotency).
      5. Materialize signals (contacts UPSERT, raw_interactions UPSERT,
         summaries UPSERT, links INSERT ON CONFLICT, queue → 'mapped').
      6. Emit per-interaction EnvelopeV1 events to EventBridge.

    The workflow ID set at the call site is
    ``f"queue-{queue_id}:approval-{approval_attempt_id}"`` (plan §6.2),
    so DBOS deduplicates concurrent starts AND replays-after-crash
    against the same approval attempt.

    Re-open paths produce DISTINCT ``approval_attempt_id`` values → a
    NEW workflow ID → a fresh workflow run; no collision with the
    prior attempt's checkpointed state.
    """
    logger.info(
        "account_provisioning_workflow start: queue_id=%s tenant_id=%s "
        "approval_attempt_id=%s re_open_count=%d effort=%s",
        queue_id, tenant_id, approval_attempt_id, re_open_count, effort,
    )

    # Step 1: re-validate queue state.
    queue_state = await revalidate_queue_state(
        queue_id=queue_id,
        tenant_id=tenant_id,
        expected_approval_attempt_id=approval_attempt_id,
    )

    # Step 2: transition status approved → creating (idempotent).
    await transition_to_creating(queue_id=queue_id)

    # Step 3: call agent enrich.
    profile = await call_agent_enrich(
        tenant_id=tenant_id,
        domain=queue_state.domain,
        effort=effort,
    )

    # Step 4: resolve or create account.
    account_id = await resolve_or_create_account(
        tenant_id=tenant_id,
        domain=queue_state.domain,
        profile=profile,
    )

    # Step 5: materialize.
    materialization = await materialize_signals(
        tenant_id=tenant_id,
        queue_id=queue_id,
        account_id=account_id,
    )

    # Step 6: emit.
    emissions = await emit_eventbridge_events(materialization=materialization)

    logger.info(
        "account_provisioning_workflow done: queue_id=%s account_id=%s "
        "contacts=%d interactions=%d emissions=%d",
        queue_id, account_id,
        len(materialization.contact_ids),
        len(materialization.interaction_ids),
        len(emissions),
    )

    return AccountProvisioningResult(
        queue_id=queue_id,
        account_id=account_id,
        domain=queue_state.domain,
        contact_ids=materialization.contact_ids,
        interaction_ids=materialization.interaction_ids,
        emissions=emissions,
    )
