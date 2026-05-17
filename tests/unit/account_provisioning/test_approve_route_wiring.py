"""Structural tests for the /approve route's workflow wiring (Phase 1.5 M4 fold-forward).

The route was wired to the DBOS workflow as part of M3 (M4 main work
brought forward to resolve a Codex P1 pre-merge finding). Full
end-to-end behavior is verified by the production canary, not unit
tests — DBOS workflow execution requires a launched DBOS instance +
real Neon, which collides with the test-tenant collision lesson
codified 2026-05-16.

These tests pin the wiring contract:

- The route imports the workflow + APPROVAL_QUEUE.
- The workflow_id formula matches plan §6.2.
- The route module's APPROVE_SQL no longer assumes worker-driven
  consumption (was previously "stamp + return"; now "stamp + enqueue
  + return 202").
"""

from __future__ import annotations

import inspect
import os

# DBOS imports require DBOS_SYSTEM_DATABASE_URL set before module load.
os.environ.setdefault("DBOS_SYSTEM_DATABASE_URL", "postgresql://t:t@localhost/t")


def test_route_imports_workflow_and_approval_queue():
    """The /approve handler reaches the DBOS workflow + queue at import time."""
    from routers import queue_actions
    from services.account_provisioning.workflow import (
        APPROVAL_QUEUE, account_provisioning_workflow,
    )

    assert queue_actions.APPROVAL_QUEUE is APPROVAL_QUEUE
    assert queue_actions.account_provisioning_workflow is account_provisioning_workflow


def test_approve_handler_returns_202_status_code():
    """FastAPI decorator on approve_entry pins status_code=202.

    Plan §5.3: /approve returns 202 Accepted — the workflow is queued,
    not completed. Pre-M4 the route returned 200 (the worker did the
    work). Post-wiring, 202 communicates "accepted; check status later."
    """
    from routers.queue_actions import router

    approve_route = None
    for route in router.routes:
        if route.path.endswith("/approve"):
            approve_route = route
            break
    assert approve_route is not None, "approve route not found on router"
    assert approve_route.status_code == 202


def test_approve_handler_signature_unchanged():
    """The route's external contract (queue_id path, ApproveRequest body) is preserved."""
    from routers.queue_actions import approve_entry, ApproveRequest

    sig = inspect.signature(approve_entry)
    params = list(sig.parameters.keys())
    assert params == ["queue_id", "body", "request"]
    # ApproveRequest still requires approval_attempt_id (M4 doesn't add new
    # client-facing fields).
    fields = ApproveRequest.model_fields
    assert "approval_attempt_id" in fields


def test_workflow_id_formula_uses_approval_attempt_id():
    """Plan §6.2: workflow_id = f"queue-{queue_id}:approval-{approval_attempt_id}".

    The route's enqueue path constructs the workflow_id this way; the
    reopen lifecycle relies on approval_attempt_id being NEW after a
    reopen (so a distinct workflow runs). The literal formula being
    present in the route module is a structural guard against accidental
    drift.
    """
    import routers.queue_actions as qa_module
    source = inspect.getsource(qa_module.approve_entry)
    assert 'f"queue-{queue_id}:approval-{body.approval_attempt_id}"' in source


def test_select_queue_sql_now_returns_re_open_count():
    """The workflow's input needs re_open_count from the queue row."""
    from routers.queue_actions import SELECT_QUEUE_SQL

    sql = str(SELECT_QUEUE_SQL.text)
    assert "re_open_count" in sql
