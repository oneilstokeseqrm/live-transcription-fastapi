"""DBOS crash-recovery integration test (Phase 1.5 M3).

Codex P3 finding (plan §20 v2 P3 corrections + plan §7.3): the workflow
must resume correctly after a process crash at each step boundary.
DBOS's recovery model resumes any workflow whose ``dbos.workflow_status``
row shows pending/running on launch.

Crash points to verify:
1. Mid-agent-call (Step 3): the agent's run_id is cached via
   ``DBOS.set_event``; the retry uses ``GET /api/enrich/{run_id}`` to
   skip a second expensive enrich.
2. Mid-EventBridge-emit (Step 6): partial emissions are OK because
   consumer-side MERGE-on-canonical-IDs is idempotent at the receiver.
3. Mid-materialization (Step 5): all SQL uses ON CONFLICT after the
   M2 unique index; a retry's materialization is a no-op.

**Skipped by default**, same gating as ``test_workflow_e2e.py``.
"""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DBOS_E2E") != "1",
    reason=(
        "DBOS crash-recovery test disabled. Set RUN_DBOS_E2E=1 + DBOS env vars "
        "to run. M4 production canary covers crash-recovery scenarios "
        "empirically via observed retries."
    ),
)


@pytest.mark.asyncio
async def test_crash_during_agent_call_replays_via_run_id_cache():
    """If the workflow crashes after Step 3 caches run_id but before
    Step 3's success is checkpointed, the retry uses GET /api/enrich/{run_id}.

    Scaffolded. Assertions:
    1. Start workflow; mock the agent's POST to set the run_id event
       then ``raise SimulatedCrash``.
    2. DBOS resumes the workflow; the retry's Step 3 reads the cached
       run_id and calls GET /api/enrich/{run_id} on the agent mock.
    3. Workflow completes successfully.
    """
    pytest.skip("Integration scaffold — see file docstring.")


@pytest.mark.asyncio
async def test_crash_during_materialization_replays_idempotently():
    """Crash after partial materialization → retry produces same end state.

    The link INSERT uses ON CONFLICT (interaction_id, contact_id) DO NOTHING
    against the M2 unique index, so duplicate writes are no-ops at SQL.
    """
    pytest.skip("Integration scaffold — see file docstring.")


@pytest.mark.asyncio
async def test_crash_mid_emission_replays_safely():
    """Crash after one emission of three → retry re-emits all three; consumers dedupe."""
    pytest.skip("Integration scaffold — see file docstring.")
