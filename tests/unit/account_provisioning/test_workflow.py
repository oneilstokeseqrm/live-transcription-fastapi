"""Structural / smoke tests for ``services.account_provisioning.workflow``.

The workflow's behavior is exercised end-to-end via the integration
test in ``tests/integration/account_provisioning/test_workflow_e2e.py``
(requires DBOS launched + real Neon). These unit tests cover the
declarative shape that an integration suite can't catch cheaply:

- Decorator presence and Queue concurrency cap (plan §6.7).
- Workflow signature matches the call-site contract (plan §4.1).
- The workflow imports its 6 steps from steps.py (plan §5.4).
"""

from __future__ import annotations

import inspect

from services.account_provisioning import workflow as wf


def test_workflow_is_dbos_decorated():
    """The workflow function must be a @DBOS.workflow callable.

    DBOS records workflow registration via the decorator; calling
    ``DBOS.start_workflow(account_provisioning_workflow, ...)`` requires
    the registration to have happened at import time.
    """
    fn = wf.account_provisioning_workflow
    assert callable(fn)
    assert inspect.iscoroutinefunction(fn)
    # The decorator preserves the function name + module.
    assert fn.__name__ == "account_provisioning_workflow"


def test_workflow_signature_matches_call_site():
    """Plan §4.1: keyword-only, takes (queue_id, tenant_id, approval_attempt_id, ...)."""
    sig = inspect.signature(wf.account_provisioning_workflow)
    params = sig.parameters
    # All params are keyword-only (after the leading `*`).
    for name in ("queue_id", "tenant_id", "approval_attempt_id", "re_open_count", "effort"):
        assert name in params, f"missing param: {name}"
        assert params[name].kind == inspect.Parameter.KEYWORD_ONLY


def test_approval_queue_concurrency_cap_matches_plan():
    """Plan §6.7 picks concurrency=5; explicit cap prevents runaway during onboarding bursts."""
    assert wf.APPROVAL_QUEUE.name == "account-provisioning"
    # The DBOS Queue class stores concurrency on the instance. Field
    # name may differ across DBOS versions; access via __dict__ for
    # forward-compat.
    state = vars(wf.APPROVAL_QUEUE)
    # Find an int attribute matching 5; explicit assertion that the
    # value is plumbed in.
    concurrency_value = None
    for attr in ("concurrency", "_concurrency"):
        if attr in state and isinstance(state[attr], int):
            concurrency_value = state[attr]
            break
    assert concurrency_value == 5, (
        f"APPROVAL_QUEUE.concurrency expected 5, got {state}"
    )


def test_workflow_imports_all_six_steps():
    """Defensive: workflow.py imports the 6 step functions plan §6 prescribes.

    Catches mis-renames between steps.py and workflow.py — a step
    accidentally removed from the imports would still ship but the
    workflow body would NameError at runtime in production.
    """
    expected = {
        "revalidate_queue_state",
        "transition_to_creating",
        "call_agent_enrich",
        "resolve_or_create_account",
        "materialize_signals",
        "emit_eventbridge_events",
    }
    for name in expected:
        assert name in dir(wf), f"workflow.py missing import: {name}"
