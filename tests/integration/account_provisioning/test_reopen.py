"""Reopen-path integration test (Phase 1.5 M3).

Codex P3 finding (plan §20 v2 P3 corrections + plan §7.3): a queue row
that was archived (e.g., owner_ignored) and then re-opened (archived_at
cleared + re_open_count bumped) must accept a new ``/approve`` cleanly,
producing a SECOND workflow instance with a DISTINCT workflow_id and
end-to-end completion.

Workflow ID stability:
- First approval: workflow_id = ``f"queue-{queue_id}:approval-{attempt_id_1}"``
- Reopen + new approval: workflow_id = ``f"queue-{queue_id}:approval-{attempt_id_2}"``

Distinct attempt_ids → distinct workflow_ids → no DBOS dedup collision.

**Skipped by default**, same gating as ``test_workflow_e2e.py``.
"""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DBOS_E2E") != "1",
    reason=(
        "DBOS workflow E2E disabled. Set RUN_DBOS_E2E=1 + DBOS env vars to "
        "run. M4 production canary covers reopen end-to-end."
    ),
)


@pytest.mark.asyncio
async def test_reopen_after_ignore_produces_distinct_workflow():
    """Reopen + re-approve runs a SECOND workflow with a distinct id.

    Scaffolded for M4 execution. Assertions:

    1. Seed queue row, archive it (archived_at + status='ignored').
    2. Reopen the row (archived_at=NULL, re_open_count=1,
       approval_attempt_id=NULL).
    3. POST /queue/{id}/approve with a NEW approval_attempt_id.
    4. Assert: workflow_id is f"queue-{id}:approval-{new_attempt}".
    5. Assert: two ``dbos.workflow_status`` rows exist for this
       queue_id, both in terminal states.
    6. Assert: contact + interaction materialization happens against
       the resolved account_id correctly on the second run.
    """
    pytest.skip("Integration scaffold — see file docstring.")
