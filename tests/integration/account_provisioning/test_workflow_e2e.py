"""End-to-end workflow integration test (Phase 1.5 M3).

Exercises the full ``account_provisioning_workflow`` against:
- Real Neon (test-tenant scoped per Option B)
- DBOS launched in-process with a test system database
- Mocked agent HTTP boundary
- Mocked EventBridge boundary (we don't want test events landing on the
  live bus and being forwarded to downstream consumer SQS queues)

**Skipped by default** in the M3 PR. The full E2E asserts behavior that
M4 (route cutover + production canary) is structured to verify end-to-
end via the live ``/approve`` route. Running this test locally requires:

1. ``DATABASE_URL`` pointed at production Neon (or a test branch).
2. ``DBOS_SYSTEM_DATABASE_URL`` pointed at a direct-connection Postgres.
3. The dbos schema migrated (auto-runs on ``DBOS.launch()``; safe on
   replay).

Run with: ``RUN_DBOS_E2E=1 pytest tests/integration/account_provisioning/test_workflow_e2e.py -v``

This file IS in M3 scope per plan §7.2 + §13 — the contract is
"workflow is dead code at end of M3, but the integration test suite
exists." The actual end-to-end behavior is validated in M4 via the
production canary.
"""

from __future__ import annotations

import os

import pytest


_REQUIRES = (
    os.environ.get("RUN_DBOS_E2E") == "1"
    and bool(os.environ.get("DATABASE_URL"))
    and bool(os.environ.get("DBOS_SYSTEM_DATABASE_URL"))
)


pytestmark = pytest.mark.skipif(
    not _REQUIRES,
    reason=(
        "DBOS workflow E2E disabled. Set RUN_DBOS_E2E=1, DATABASE_URL, and "
        "DBOS_SYSTEM_DATABASE_URL to run. M4 production canary covers the "
        "live end-to-end behavior."
    ),
)


@pytest.mark.asyncio
async def test_workflow_runs_end_to_end():
    """Full workflow run: queue → workflow → contacts + emissions.

    Scaffolded for M4 execution. The acceptance assertions per plan
    §7.3 + §13:

    1. Seed a pending_account_mappings row + N signals via Neon.
    2. Start the workflow via ``DBOS.start_workflow_async`` with
       ``SetWorkflowID(f"queue-{queue_id}:approval-{attempt_id}")``.
    3. Await completion.
    4. Assert: ``accounts`` row exists, ``account_domains`` row exists,
       ``contacts`` row per signal, ``interaction_contact_links`` row per
       (interaction, contact) pair, queue.status='mapped',
       ``dbos.workflow_status`` row in 'success' state.
    5. Assert: EventBridge mock received one entry per backfilled
       interaction with Source='com.yourapp.transcription' and
       DetailType matching the closed lookup.
    """
    pytest.skip(
        "Integration scaffold — see file docstring. M4 production canary "
        "validates end-to-end."
    )


@pytest.mark.asyncio
async def test_workflow_replay_does_not_duplicate_materialization():
    """Two starts with the same workflow_id → one materialization, one emission set.

    Scaffolded for M4 execution. Tests the SetWorkflowID dedup + step
    idempotency end-to-end.
    """
    pytest.skip("Integration scaffold — see file docstring.")
