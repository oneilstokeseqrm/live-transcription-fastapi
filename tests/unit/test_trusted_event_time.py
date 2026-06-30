"""RequestContext.trusted_event_time — the occurred_at trust gate (EQ-230 / A1).

The route honors a caller-supplied ``occurred_at`` ONLY when the request came
through the verified internal-JWT path. ``RequestContext`` does not carry the
JWT issuer (``verify_internal_jwt`` validates then discards ``iss``), so the
implementable rule is "trusted iff we went through ``_extract_context_from_jwt``,
NOT the legacy-header or lenient fallbacks." These tests pin that mapping at the
source so trust can never be silently inferred from a header.
"""

import os
import time
import uuid

import jwt as pyjwt
import pytest
from starlette.requests import Request

from models.request_context import RequestContext
from utils.context_utils import (
    get_auth_context_ingestion,
    get_auth_context_polling,
    get_request_context,
)

# verify_internal_jwt reads these at call time.
os.environ.setdefault(
    "INTERNAL_JWT_SECRET", "test-secret-that-is-at-least-32-characters-long"
)
os.environ.setdefault("INTERNAL_JWT_ISSUER", "eq-frontend")
os.environ.setdefault("INTERNAL_JWT_AUDIENCE", "eq-backend")


def _make_request(headers: dict[str, str]) -> Request:
    """Build a real Starlette Request with case-insensitive headers."""
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/text/clean",
        "headers": raw,
    }
    return Request(scope)


def _valid_jwt(tenant_id: str) -> str:
    now = int(time.time())
    payload = {
        "tenant_id": tenant_id,
        "user_id": "auth0|trusted-user",
        "iss": os.environ["INTERNAL_JWT_ISSUER"],
        "aud": os.environ["INTERNAL_JWT_AUDIENCE"],
        "iat": now,
        "exp": now + 300,
    }
    return pyjwt.encode(payload, os.environ["INTERNAL_JWT_SECRET"], algorithm="HS256")


def test_request_context_trusted_event_time_defaults_false():
    """The flag is opt-in: any context built without it is untrusted."""
    ctx = RequestContext(
        tenant_id="tenant-1",
        user_id="user-1",
        account_id="acct-1",
        interaction_id="int-1",
        trace_id="trace-1",
    )
    assert ctx.trusted_event_time is False


def test_jwt_ingestion_path_marks_trusted():
    tenant_id = str(uuid.uuid4())
    request = _make_request(
        {
            "Authorization": f"Bearer {_valid_jwt(tenant_id)}",
            "X-Account-ID": "acct-1",
        }
    )
    ctx = get_auth_context_ingestion(request)
    assert ctx.tenant_id == tenant_id
    assert ctx.trusted_event_time is True


def test_jwt_polling_path_marks_trusted():
    """Same verified-JWT helper backs polling; it is trusted too (consistency)."""
    tenant_id = str(uuid.uuid4())
    request = _make_request(
        {"Authorization": f"Bearer {_valid_jwt(tenant_id)}"}
    )
    ctx = get_auth_context_polling(request)
    assert ctx.trusted_event_time is True


def test_legacy_header_path_not_trusted():
    """Legacy-header auth (no verified JWT) must never be trusted for event-time."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ALLOW_LEGACY_HEADER_AUTH", "true")
        request = _make_request(
            {
                "X-Tenant-ID": str(uuid.uuid4()),
                "X-User-ID": "legacy-user",
                "X-Account-ID": "acct-1",
            }
        )
        ctx = get_auth_context_ingestion(request)
    assert ctx.trusted_event_time is False


def test_lenient_context_not_trusted():
    """The lenient/websocket path is never a trusted event-time source."""
    request = _make_request({"X-Tenant-ID": str(uuid.uuid4()), "X-User-ID": "u"})
    ctx = get_request_context(request)
    assert ctx.trusted_event_time is False
