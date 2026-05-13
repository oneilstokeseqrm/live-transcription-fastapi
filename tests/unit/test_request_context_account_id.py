"""RequestContext.account_id is required.

RequestContext is a stdlib @dataclass (not Pydantic), so omitting a required
field raises TypeError at construction time rather than pydantic.ValidationError.
We assert TypeError here to match the actual model surface.
"""

import pytest

from models.request_context import RequestContext


def test_request_context_rejects_missing_account_id():
    with pytest.raises(TypeError):
        RequestContext(  # type: ignore[call-arg]
            tenant_id="tenant-1",
            user_id="user-1",
            interaction_id="int-1",
            trace_id="trace-1",
        )


def test_request_context_accepts_account_id():
    ctx = RequestContext(
        tenant_id="tenant-1",
        user_id="user-1",
        account_id="acct-1",
        interaction_id="int-1",
        trace_id="trace-1",
    )
    assert ctx.account_id == "acct-1"
