"""Unit tests for :mod:`routers.granola_cron`.

Uses ``fastapi.testclient.TestClient`` against a minimal FastAPI app
that includes only the granola_cron router — no DBOS launch, no
DATABASE_URL needed because the dispatch helpers are mocked at the
module level. Covers:

* Cron secret env var unset → 503.
* Missing ``X-Internal-Cron-Secret`` header → 401.
* Wrong ``X-Internal-Cron-Secret`` value → 401.
* Correct secret + zero credentials → 202 with ``enqueued=0``
  (the Phase-2e dormant-until-Phase-2f scenario).
* Correct secret + N credentials → 202 with ``enqueued=N`` and each
  dispatched with a distinct workflow_id derived from
  ``(credential_id, cycle_window)``.
* Same credential dispatched twice within the same cycle window
  produces the same workflow_id → DBOS dedup catches the second
  enqueue at the DBOS layer (we verify that the cron handler
  produces deterministic workflow_ids; DBOS's actual dedup is its
  contract).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import granola_cron
from services.granola_ingestion.scheduler import CredentialMetadata, RecoverableImport


_VALID_SECRET = "a" * 64  # 32-byte hex (64 chars); same shape as production


@pytest.fixture
def _app_with_cron_router():
    """Minimal FastAPI app exposing only /internal/granola/cron-tick."""
    app = FastAPI()
    app.include_router(granola_cron.router)
    return app


@pytest.fixture
def client(_app_with_cron_router):
    return TestClient(_app_with_cron_router)


# ---------------------------------------------------------------------------
# Auth: env var / header validation
# ---------------------------------------------------------------------------


def test_cron_tick_returns_503_when_secret_env_unset(client, monkeypatch):
    """INTERNAL_CRON_SECRET unset → operator misconfiguration. Loud
    503 so the Railway cron failure is diagnosable."""
    monkeypatch.delenv("INTERNAL_CRON_SECRET", raising=False)
    resp = client.post(
        "/internal/granola/cron-tick",
        headers={"X-Internal-Cron-Secret": "anything"},
    )
    assert resp.status_code == 503
    assert "cron auth not configured" in resp.json()["detail"]


def test_cron_tick_returns_401_when_header_missing(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_CRON_SECRET", _VALID_SECRET)
    resp = client.post("/internal/granola/cron-tick")
    assert resp.status_code == 401
    assert "missing X-Internal-Cron-Secret" in resp.json()["detail"]


def test_cron_tick_returns_401_when_secret_mismatches(client, monkeypatch):
    monkeypatch.setenv("INTERNAL_CRON_SECRET", _VALID_SECRET)
    resp = client.post(
        "/internal/granola/cron-tick",
        headers={"X-Internal-Cron-Secret": "wrong-secret"},
    )
    assert resp.status_code == 401
    assert "invalid X-Internal-Cron-Secret" in resp.json()["detail"]


def test_cron_tick_constant_time_compare_handles_length_mismatch(client, monkeypatch):
    """secrets.compare_digest is constant-time + length-safe; supplying
    a shorter value mustn't raise."""
    monkeypatch.setenv("INTERNAL_CRON_SECRET", _VALID_SECRET)
    resp = client.post(
        "/internal/granola/cron-tick",
        headers={"X-Internal-Cron-Secret": "short"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Dispatch: cycle_window + workflow_id construction
# ---------------------------------------------------------------------------


def _patch_dispatch(*, credentials: list[CredentialMetadata]):
    """Patch list_active_credentials + GRANOLA_POLL_QUEUE.enqueue_async.

    Returns a tuple (list_mock, enqueue_mock, captured_workflow_ids).
    The SetWorkflowID context manager is wrapped to capture the
    workflow_id passed at each enqueue site so tests can assert on it.
    """
    list_mock = AsyncMock(return_value=credentials)
    enqueue_mock = AsyncMock()
    captured_ids: list[str] = []

    # SetWorkflowID is a context manager; mock it to capture the
    # workflow_id passed on enter. The real SetWorkflowID stores the
    # id on a context var so DBOS reads it during enqueue; the mock
    # just records the value.
    def _set_workflow_id(workflow_id: str):
        captured_ids.append(workflow_id)
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=None)
        cm.__exit__ = MagicMock(return_value=None)
        return cm

    set_id_mock = MagicMock(side_effect=_set_workflow_id)

    return list_mock, enqueue_mock, set_id_mock, captured_ids


def test_cron_tick_computes_cycle_window_before_listing_credentials(client, monkeypatch):
    """Codex PR-#28 R1 P2: cycle_window MUST be captured BEFORE the
    list_active_credentials await, else a tick near a :00/:05 boundary
    could stamp workflow_ids with the next window and the following
    real tick would dedup them away — silently dropping a 5-min poll
    interval.

    We assert call ordering: _current_cycle_window fires before
    list_active_credentials."""
    monkeypatch.setenv("INTERNAL_CRON_SECRET", _VALID_SECRET)
    order: list[str] = []

    def _window():
        order.append("window")
        return 999

    async def _list():
        order.append("list")
        return []

    with patch.object(granola_cron, "_current_cycle_window", side_effect=_window), \
         patch.object(granola_cron, "list_active_credentials", new=_list):
        resp = client.post(
            "/internal/granola/cron-tick",
            headers={"X-Internal-Cron-Secret": _VALID_SECRET},
        )

    assert resp.status_code == 202
    assert resp.json()["cycle_window"] == 999
    assert order == ["window", "list"], (
        "cycle_window must be computed before the credential-listing await"
    )


def test_cron_tick_with_zero_credentials_returns_enqueued_zero(client, monkeypatch):
    """Pre-Phase-2f happy path: no active credentials → 202 with
    ``enqueued=0``. The scheduler runs every 5 min and exits cleanly
    until Phase 2f adds /connect."""
    monkeypatch.setenv("INTERNAL_CRON_SECRET", _VALID_SECRET)
    list_mock, enqueue_mock, set_id_mock, _captured = _patch_dispatch(credentials=[])

    with patch.object(granola_cron, "list_active_credentials", new=list_mock), \
         patch.object(granola_cron.GRANOLA_POLL_QUEUE, "enqueue_async", new=enqueue_mock), \
         patch.object(granola_cron, "SetWorkflowID", new=set_id_mock):
        resp = client.post(
            "/internal/granola/cron-tick",
            headers={"X-Internal-Cron-Secret": _VALID_SECRET},
        )

    assert resp.status_code == 202
    body = resp.json()
    assert body["enqueued"] == 0
    assert isinstance(body["cycle_window"], int)
    enqueue_mock.assert_not_called()


def test_cron_tick_dispatches_one_workflow_per_credential(client, monkeypatch):
    """N active credentials → N enqueue_async calls; each with a
    distinct workflow_id derived from credential_id + cycle_window."""
    monkeypatch.setenv("INTERNAL_CRON_SECRET", _VALID_SECRET)
    creds = [
        CredentialMetadata(id=uuid4(), tenant_id=uuid4(), user_id=uuid4()),
        CredentialMetadata(id=uuid4(), tenant_id=uuid4(), user_id=uuid4()),
        CredentialMetadata(id=uuid4(), tenant_id=uuid4(), user_id=uuid4()),
    ]
    list_mock, enqueue_mock, set_id_mock, captured_ids = _patch_dispatch(credentials=creds)

    with patch.object(granola_cron, "list_active_credentials", new=list_mock), \
         patch.object(granola_cron.GRANOLA_POLL_QUEUE, "enqueue_async", new=enqueue_mock), \
         patch.object(granola_cron, "SetWorkflowID", new=set_id_mock):
        resp = client.post(
            "/internal/granola/cron-tick",
            headers={"X-Internal-Cron-Secret": _VALID_SECRET},
        )

    assert resp.status_code == 202
    assert resp.json()["enqueued"] == 3

    # Three distinct workflow_ids captured
    assert len(captured_ids) == 3
    assert len(set(captured_ids)) == 3, f"workflow_ids must be unique: {captured_ids}"

    # Each workflow_id has the locked-in shape per LOCKED-39
    pattern = re.compile(r"^granola_poll_[0-9a-f-]{36}_\d+$")
    for wf_id in captured_ids:
        assert pattern.match(wf_id), f"{wf_id} doesn't match locked shape"

    # Each enqueue passed (workflow_fn, credential_id, tenant_id, user_id)
    assert enqueue_mock.await_count == 3
    for call, cred in zip(enqueue_mock.await_args_list, creds):
        args = call.args
        # args[0] is the workflow function; positional args follow
        assert args[1] == cred.id
        assert args[2] == cred.tenant_id
        assert args[3] == cred.user_id


def test_cron_tick_same_credential_same_window_produces_same_workflow_id(client, monkeypatch):
    """LOCKED-39 idempotency: two cron ticks in the same 5-min
    window with the same credential_id produce the same workflow_id —
    DBOS dedups at its layer. We verify the cron handler is
    deterministic; the DBOS dedup itself is its contract."""
    monkeypatch.setenv("INTERNAL_CRON_SECRET", _VALID_SECRET)

    cred = CredentialMetadata(id=uuid4(), tenant_id=uuid4(), user_id=uuid4())
    # Freeze the cycle_window so two ticks land in the same window.
    fixed_window = 12345
    list_mock, enqueue_mock, set_id_mock, captured_ids = _patch_dispatch(credentials=[cred])

    with patch.object(granola_cron, "list_active_credentials", new=list_mock), \
         patch.object(granola_cron.GRANOLA_POLL_QUEUE, "enqueue_async", new=enqueue_mock), \
         patch.object(granola_cron, "SetWorkflowID", new=set_id_mock), \
         patch.object(granola_cron, "_current_cycle_window", return_value=fixed_window):
        # Tick #1
        resp1 = client.post(
            "/internal/granola/cron-tick",
            headers={"X-Internal-Cron-Secret": _VALID_SECRET},
        )
        # Tick #2 in the same window
        resp2 = client.post(
            "/internal/granola/cron-tick",
            headers={"X-Internal-Cron-Secret": _VALID_SECRET},
        )

    assert resp1.status_code == resp2.status_code == 202
    assert resp1.json()["cycle_window"] == resp2.json()["cycle_window"] == fixed_window
    # Both ticks produce the SAME workflow_id
    assert len(captured_ids) == 2
    assert captured_ids[0] == captured_ids[1]
    expected = f"granola_poll_{cred.id}_{fixed_window}"
    assert captured_ids[0] == expected


def test_cron_tick_same_credential_different_window_produces_distinct_ids(client, monkeypatch):
    """Successive 5-min windows produce distinct workflow_ids → fresh
    workflow runs each window, regardless of prior dispatches."""
    monkeypatch.setenv("INTERNAL_CRON_SECRET", _VALID_SECRET)

    cred = CredentialMetadata(id=uuid4(), tenant_id=uuid4(), user_id=uuid4())
    list_mock, enqueue_mock, set_id_mock, captured_ids = _patch_dispatch(credentials=[cred])

    with patch.object(granola_cron, "list_active_credentials", new=list_mock), \
         patch.object(granola_cron.GRANOLA_POLL_QUEUE, "enqueue_async", new=enqueue_mock), \
         patch.object(granola_cron, "SetWorkflowID", new=set_id_mock), \
         patch.object(granola_cron, "_current_cycle_window", side_effect=[100, 101]):
        client.post("/internal/granola/cron-tick",
                    headers={"X-Internal-Cron-Secret": _VALID_SECRET})
        client.post("/internal/granola/cron-tick",
                    headers={"X-Internal-Cron-Secret": _VALID_SECRET})

    assert len(captured_ids) == 2
    assert captured_ids[0] != captured_ids[1]
    assert captured_ids[0].endswith("_100")
    assert captured_ids[1].endswith("_101")


# ---------------------------------------------------------------------------
# A2 strand recovery (EQ-92/B3): cron re-dispatches stale 'queued' imports
# ---------------------------------------------------------------------------


def test_cron_tick_redispatches_stale_queued_imports(client, monkeypatch):
    """A2 backstop: the cron tick re-dispatches stale 'queued' import runs with
    a window-stamped recovery workflow id, and reports the count. Independent of
    the poll dispatch."""
    monkeypatch.setenv("INTERNAL_CRON_SECRET", _VALID_SECRET)
    stale = [
        RecoverableImport(
            import_run_id=uuid4(), credential_id=uuid4(),
            tenant_id=uuid4(), user_id=uuid4(),
        ),
        RecoverableImport(
            import_run_id=uuid4(), credential_id=uuid4(),
            tenant_id=uuid4(), user_id=uuid4(),
        ),
    ]
    enqueue_import_mock = AsyncMock()
    with patch.object(granola_cron, "list_active_credentials", new=AsyncMock(return_value=[])), \
         patch.object(granola_cron, "list_recoverable_import_runs", new=AsyncMock(return_value=stale)), \
         patch.object(granola_cron, "enqueue_import_workflow", new=enqueue_import_mock), \
         patch.object(granola_cron, "_current_cycle_window", return_value=777):
        resp = client.post(
            "/internal/granola/cron-tick",
            headers={"X-Internal-Cron-Secret": _VALID_SECRET},
        )
    assert resp.status_code == 202
    assert resp.json()["imports_recovered"] == 2
    assert enqueue_import_mock.await_count == 2
    for call, r in zip(enqueue_import_mock.await_args_list, stale):
        kw = call.kwargs
        assert kw["credential_id"] == r.credential_id
        assert kw["tenant_id"] == r.tenant_id
        assert kw["user_id"] == r.user_id
        assert kw["import_run_id"] == r.import_run_id
        # window-stamped recovery id (distinct from the deterministic /connect id)
        assert kw["workflow_id"].endswith("_r777")
        assert str(r.import_run_id) in kw["workflow_id"]


def test_cron_tick_recovery_failure_is_non_fatal(client, monkeypatch):
    """A recovery-query blip must NOT block the poll dispatch or 5xx the tick —
    it's swallowed + logged; imports_recovered=0."""
    monkeypatch.setenv("INTERNAL_CRON_SECRET", _VALID_SECRET)
    cred = CredentialMetadata(id=uuid4(), tenant_id=uuid4(), user_id=uuid4())
    list_mock, enqueue_mock, set_id_mock, _captured = _patch_dispatch(credentials=[cred])
    with patch.object(granola_cron, "list_active_credentials", new=list_mock), \
         patch.object(granola_cron.GRANOLA_POLL_QUEUE, "enqueue_async", new=enqueue_mock), \
         patch.object(granola_cron, "SetWorkflowID", new=set_id_mock), \
         patch.object(granola_cron, "list_recoverable_import_runs",
                      new=AsyncMock(side_effect=RuntimeError("db blip"))):
        resp = client.post(
            "/internal/granola/cron-tick",
            headers={"X-Internal-Cron-Secret": _VALID_SECRET},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert body["enqueued"] == 1          # poll dispatch unaffected
    assert body["imports_recovered"] == 0  # recovery swallowed


# ---------------------------------------------------------------------------
# cycle_window math
# ---------------------------------------------------------------------------


def test_current_cycle_window_advances_every_5_minutes():
    """Two timestamps 5 min apart land in adjacent windows."""
    # Frozen times: 2026-05-24T10:00:00 and 10:05:00.
    t0 = datetime(2026, 5, 24, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 24, 10, 5, 0, tzinfo=timezone.utc)
    with patch("routers.granola_cron.datetime") as dt_mock:
        dt_mock.now.return_value = t0
        dt_mock.side_effect = lambda *a, **k: datetime(*a, **k)
        w0 = granola_cron._current_cycle_window()
        dt_mock.now.return_value = t1
        w1 = granola_cron._current_cycle_window()
    assert w1 == w0 + 1


def test_current_cycle_window_stable_within_5_minutes():
    """Timestamps inside the same 5-min boundary share a window."""
    t0 = datetime(2026, 5, 24, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 24, 10, 4, 59, tzinfo=timezone.utc)
    with patch("routers.granola_cron.datetime") as dt_mock:
        dt_mock.now.return_value = t0
        dt_mock.side_effect = lambda *a, **k: datetime(*a, **k)
        w0 = granola_cron._current_cycle_window()
        dt_mock.now.return_value = t1
        w1 = granola_cron._current_cycle_window()
    assert w0 == w1


# ---------------------------------------------------------------------------
# verify_internal_cron_secret as a unit (without TestClient)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_internal_cron_secret_success(monkeypatch):
    """Happy-path call returns None (no raise)."""
    monkeypatch.setenv("INTERNAL_CRON_SECRET", _VALID_SECRET)
    # Direct invocation as a function — dependency-injection is the
    # wiring concern, not the helper's behavior.
    result = await granola_cron.verify_internal_cron_secret(
        x_internal_cron_secret=_VALID_SECRET,
    )
    assert result is None
