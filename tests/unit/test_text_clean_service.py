"""Unit tests for :mod:`services.text_clean_service`.

PR-X1 of the Granola integration extracted the Lane 1 publish + Lane 2
dispatch + backpressure logic out of ``routers/text.py`` so the Granola
ingestion adapter (PR-X2, LOCKED-41) can call into the same Python module
instead of issuing intra-service HTTP requests against ``/text/clean``.

These tests pin the **module-level** behavior the integration suite
(``tests/integration/test_text_clean_response_decoupling.py``) already
pins at the route-handler level. Together they enforce both halves of
the contract:

* Integration tests prove the route behavior is preserved end-to-end.
* These unit tests prove the helpers behave correctly when called from
  outside the route handler (i.e. from the Granola adapter).

AsyncMock + monkeypatch per ``feedback_test_pattern_no_docker.md`` — no
Docker, no network, no Neon writes.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.envelope import ContentModel, EnvelopeV1
from services import text_clean_service
from services.text_clean_service import (
    Lane1PublishError,
    Lane2Extras,
    ProcessResult,
    TenantIsolationError,
    process,
    release_lane2_slot,
    try_reserve_lane2_slot,
)


async def _call_process(envelope: EnvelopeV1, lane2_extras: Optional[Lane2Extras] = None):
    """Test helper: call :func:`process` with identity kwargs sourced from envelope.

    Most tests are exercising Lane 1 / Lane 2 / backpressure behavior and want
    the LOCKED-41 cross-check to pass silently. Tests that exercise the
    cross-check itself call :func:`process` directly with intentionally
    mismatched kwargs.
    """
    return await process(
        tenant_id=envelope.tenant_id,
        user_id=envelope.user_id,
        account_id=envelope.account_id,
        envelope=envelope,
        lane2_extras=lane2_extras,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_envelope(*, text: str = "raw text", interaction_id: Optional[uuid.UUID] = None) -> EnvelopeV1:
    """Construct an EnvelopeV1 with sensible defaults."""
    return EnvelopeV1(
        tenant_id=uuid.uuid4(),
        user_id="auth0|test-user",
        interaction_type="note",
        content=ContentModel(text=text, format="plain"),
        timestamp=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        source="api",
        extras={},
        interaction_id=interaction_id or uuid.uuid4(),
        trace_id="trace-xyz",
        account_id=str(uuid.uuid4()),
        pg_user_id=None,
    )


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset Lane 2 counter + task set between tests.

    Without this, a slot leak in one test cascades into the next and the
    backpressure assertions go non-deterministic.
    """
    text_clean_service._INFLIGHT_LANE2[0] = 0
    text_clean_service._BACKGROUND_TASKS.clear()
    yield
    text_clean_service._INFLIGHT_LANE2[0] = 0
    text_clean_service._BACKGROUND_TASKS.clear()


# ---------------------------------------------------------------------------
# Backpressure helpers
# ---------------------------------------------------------------------------


def test_try_reserve_lane2_slot_succeeds_when_below_cap(monkeypatch):
    """When in-flight count < cap, reservation succeeds and increments the counter."""
    monkeypatch.setenv("TEXT_CLEAN_MAX_BG_TASKS", "5")
    assert text_clean_service.get_lane2_in_flight() == 0
    assert try_reserve_lane2_slot() is True
    assert text_clean_service.get_lane2_in_flight() == 1


def test_try_reserve_lane2_slot_fails_at_cap(monkeypatch):
    """When in-flight count >= cap, reservation returns False without incrementing."""
    monkeypatch.setenv("TEXT_CLEAN_MAX_BG_TASKS", "2")
    assert try_reserve_lane2_slot() is True
    assert try_reserve_lane2_slot() is True
    # At cap now.
    assert text_clean_service.get_lane2_in_flight() == 2
    assert try_reserve_lane2_slot() is False
    # Counter unchanged on failed reservation.
    assert text_clean_service.get_lane2_in_flight() == 2


def test_release_lane2_slot_decrements_counter():
    """release_lane2_slot decrements the counter unconditionally."""
    text_clean_service._INFLIGHT_LANE2[0] = 3
    release_lane2_slot()
    assert text_clean_service.get_lane2_in_flight() == 2


def test_get_lane2_cap_reads_env_at_call_time(monkeypatch):
    """get_lane2_cap() reads the env var fresh — not frozen at import.

    Mirrors the round-6 P2 fix on PR #23: main.py imports text_clean_service
    BEFORE calling load_dotenv() — capturing at import would freeze the
    default and ignore .env overrides.
    """
    monkeypatch.setenv("TEXT_CLEAN_MAX_BG_TASKS", "7")
    assert text_clean_service.get_lane2_cap() == 7
    monkeypatch.setenv("TEXT_CLEAN_MAX_BG_TASKS", "11")
    assert text_clean_service.get_lane2_cap() == 11


def test_try_reserve_atomic_under_concurrency(monkeypatch):
    """Two near-simultaneous reservations against a cap of 1: only one succeeds.

    Codex round-4 P1 (on PR #23) pinned the atomic check + increment
    pattern. Under cooperative scheduling, the event loop guarantees no
    ``await`` between the read and the increment, so concurrent coroutines
    can't observe the same stale count and overshoot. We simulate
    concurrency by interleaving via ``asyncio.gather``.
    """
    monkeypatch.setenv("TEXT_CLEAN_MAX_BG_TASKS", "1")

    async def _attempt() -> bool:
        # No awaits between check + reserve — atomic by virtue of the
        # event loop being single-threaded.
        return try_reserve_lane2_slot()

    async def _drive() -> tuple[bool, bool]:
        a, b = await asyncio.gather(_attempt(), _attempt())
        return a, b

    a, b = asyncio.run(_drive())
    assert (a, b) in ((True, False), (False, True)), (
        f"Expected exactly one reservation to succeed; got {a=}, {b=}"
    )
    assert text_clean_service.get_lane2_in_flight() == 1


# ---------------------------------------------------------------------------
# process() — happy path
# ---------------------------------------------------------------------------


def _patch_aws_publisher(return_value=None):
    """Helper that returns a context-managed patch on AWSEventPublisher."""
    publisher_instance = MagicMock()
    publisher_instance.publish_envelope = AsyncMock(
        return_value=return_value or {"kinesis_sequence": "seq-1", "eventbridge_id": "evt-1"}
    )
    return patch(
        "services.text_clean_service.AWSEventPublisher",
        return_value=publisher_instance,
    ), publisher_instance


def _patch_intelligence_service(side_effect=None, return_value=None):
    intelligence_instance = MagicMock()
    intelligence_instance.process_transcript = AsyncMock(
        side_effect=side_effect,
        return_value=return_value if side_effect is None else None,
    )
    return patch(
        "services.text_clean_service.IntelligenceService",
        return_value=intelligence_instance,
    ), intelligence_instance


@pytest.mark.asyncio
async def test_process_happy_path_publishes_and_dispatches_lane2():
    """Lane 1 + Lane 2 both fire; ProcessResult flags both True."""
    # Caller has reserved a slot (process() doesn't reserve internally).
    assert try_reserve_lane2_slot()
    pub_patch, publisher_instance = _patch_aws_publisher()
    int_patch, intelligence_instance = _patch_intelligence_service(return_value=MagicMock())

    envelope = _build_envelope(text="cleaned content")
    with pub_patch, int_patch:
        result = await _call_process(envelope, lane2_extras=None)
        # Stay inside the patch context until Lane 2 has actually run —
        # otherwise the patch reverts and the background task constructs
        # the real (un-patched) IntelligenceService.
        for _ in range(5):
            await asyncio.sleep(0)
            if intelligence_instance.process_transcript.await_count > 0:
                break

    assert isinstance(result, ProcessResult)
    assert result.interaction_id == str(envelope.interaction_id)
    assert result.lane1_published is True
    assert result.lane2_dispatched is True
    publisher_instance.publish_envelope.assert_awaited_once_with(envelope)
    intelligence_instance.process_transcript.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_lane2_uses_envelope_content_text_when_extras_missing():
    """When lane2_extras is None, Lane 2 receives envelope.content.text.

    Granola adapter case: it builds the envelope's content.text directly
    and doesn't set Lane2Extras.cleaned_transcript. Lane 2 should still
    receive the right transcript.
    """
    assert try_reserve_lane2_slot()
    pub_patch, _ = _patch_aws_publisher()
    int_patch, intelligence_instance = _patch_intelligence_service()

    envelope = _build_envelope(text="granola-built content")
    with pub_patch, int_patch:
        await _call_process(envelope, lane2_extras=None)
        for _ in range(5):
            await asyncio.sleep(0)
            if intelligence_instance.process_transcript.await_count > 0:
                break

    call_kwargs = intelligence_instance.process_transcript.await_args.kwargs
    assert call_kwargs["cleaned_transcript"] == "granola-built content"
    assert call_kwargs["interaction_id"] == str(envelope.interaction_id)
    assert call_kwargs["tenant_id"] == str(envelope.tenant_id)
    assert call_kwargs["account_id"] == envelope.account_id
    assert call_kwargs["interaction_type"] == envelope.interaction_type
    # None for fields the caller didn't provide (Granola path).
    assert call_kwargs["contact_ids"] is None
    assert call_kwargs["calendar_event_id"] is None
    assert call_kwargs["enrichment_confidence"] is None
    assert call_kwargs["enrichment_match_method"] is None


@pytest.mark.asyncio
async def test_process_lane2_uses_cleaned_transcript_override():
    """When lane2_extras.cleaned_transcript is set, Lane 2 sees that string.

    /text/clean case: BatchCleanerService produces a different string than
    what's on the envelope (envelope carries the raw input + front-matter
    per pre-extraction contract); Lane 2 should analyze the cleaned form.
    """
    assert try_reserve_lane2_slot()
    pub_patch, _ = _patch_aws_publisher()
    int_patch, intelligence_instance = _patch_intelligence_service()

    envelope = _build_envelope(text="raw + frontmatter")
    extras = Lane2Extras(
        cleaned_transcript="LLM-cleaned form",
        contact_ids=["c1", "c2"],
        calendar_event_id="cal-1",
        enrichment_confidence="high",
        enrichment_match_method="conference_url",
    )
    with pub_patch, int_patch:
        await _call_process(envelope, lane2_extras=extras)
        for _ in range(5):
            await asyncio.sleep(0)
            if intelligence_instance.process_transcript.await_count > 0:
                break

    call_kwargs = intelligence_instance.process_transcript.await_args.kwargs
    assert call_kwargs["cleaned_transcript"] == "LLM-cleaned form"
    assert call_kwargs["contact_ids"] == ["c1", "c2"]
    assert call_kwargs["calendar_event_id"] == "cal-1"
    assert call_kwargs["enrichment_confidence"] == "high"
    assert call_kwargs["enrichment_match_method"] == "conference_url"


@pytest.mark.asyncio
async def test_process_passes_envelope_tenant_id_to_lane2():
    """Tenant isolation: Lane 2 sees envelope.tenant_id, not a substituted value.

    Critical for the Granola adapter (LOCKED-41 tenant isolation): the
    cross-tenant guard test in Phase 2d depends on this — if process()
    ever substituted a different tenant_id between envelope and Lane 2,
    the Granola adapter's tenant-isolation contract would silently break.
    """
    assert try_reserve_lane2_slot()
    pub_patch, _ = _patch_aws_publisher()
    int_patch, intelligence_instance = _patch_intelligence_service()

    tenant_a = uuid.uuid4()
    envelope = _build_envelope()
    object.__setattr__(envelope, "tenant_id", tenant_a)

    with pub_patch, int_patch:
        await _call_process(envelope, lane2_extras=None)
        for _ in range(5):
            await asyncio.sleep(0)
            if intelligence_instance.process_transcript.await_count > 0:
                break

    call_kwargs = intelligence_instance.process_transcript.await_args.kwargs
    assert call_kwargs["tenant_id"] == str(tenant_a)


# ---------------------------------------------------------------------------
# process() — slot lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_consumes_slot_on_success():
    """After Lane 2 dispatch, the in-flight counter stays incremented.

    The slot was reserved before process() and is consumed by the
    dispatched task; ``_on_done`` releases it when the task completes.
    Between dispatch and task completion, the counter must reflect the
    consumed slot.
    """
    assert try_reserve_lane2_slot()
    assert text_clean_service.get_lane2_in_flight() == 1

    pub_patch, _ = _patch_aws_publisher()
    # Slow Lane 2 so we can observe state mid-flight.
    lane2_block = asyncio.Event()

    async def _block(**_kwargs):
        await lane2_block.wait()
        return MagicMock()

    int_patch, _ = _patch_intelligence_service(side_effect=_block)

    envelope = _build_envelope()
    with pub_patch, int_patch:
        result = await _call_process(envelope, lane2_extras=None)
        # process() returned but Lane 2 is still pending — slot still consumed.
        assert result.lane2_dispatched is True
        assert text_clean_service.get_lane2_in_flight() == 1

        # Release Lane 2 and let _on_done run.
        lane2_block.set()
        for _ in range(5):
            await asyncio.sleep(0)

    # After Lane 2 completes, _on_done has released the slot.
    assert text_clean_service.get_lane2_in_flight() == 0


@pytest.mark.asyncio
async def test_process_releases_slot_on_lane1_failure():
    """Lane 1 raise → Lane1PublishError raised + slot released by process()."""
    assert try_reserve_lane2_slot()
    assert text_clean_service.get_lane2_in_flight() == 1

    publisher_instance = MagicMock()
    publisher_instance.publish_envelope = AsyncMock(
        side_effect=RuntimeError("simulated Kinesis outage")
    )
    pub_patch = patch(
        "services.text_clean_service.AWSEventPublisher",
        return_value=publisher_instance,
    )
    int_patch, intelligence_instance = _patch_intelligence_service()

    envelope = _build_envelope()
    with pub_patch, int_patch:
        with pytest.raises(Lane1PublishError):
            await _call_process(envelope, lane2_extras=None)

    # Slot released by process()'s internal finally.
    assert text_clean_service.get_lane2_in_flight() == 0
    # Lane 2 must NOT have been dispatched.
    intelligence_instance.process_transcript.assert_not_called()


@pytest.mark.asyncio
async def test_process_releases_slot_on_non_lane1_failure():
    """A non-Lane1 raise from process() (e.g. asyncio.create_task fails during
    shutdown) MUST release the slot exactly once via process()'s own finally.

    Codex PR-X1 R1 P2 caught a double-decrement bug: pre-fix, the router's
    outer finally only cleared ``slot_held`` for ``Lane1PublishError``, so any
    other raise from process() (after the Lane 1 publish completes but before
    Lane 2 is dispatched) decremented the counter twice — once in process()'s
    finally, once in the router's. With the fix, process() always owns the
    slot and the router stays at exactly one decrement per failure.
    """
    assert try_reserve_lane2_slot()
    assert text_clean_service.get_lane2_in_flight() == 1

    publisher_instance = MagicMock()
    publisher_instance.publish_envelope = AsyncMock(
        return_value={"kinesis_sequence": "seq-1", "eventbridge_id": "evt-1"}
    )
    pub_patch = patch(
        "services.text_clean_service.AWSEventPublisher",
        return_value=publisher_instance,
    )

    # Trigger a non-Lane1 failure in the post-publish path: replace
    # _BACKGROUND_TASKS with a mock whose ``add`` raises. This simulates
    # the realistic class of failure (asyncio.create_task / task tracking
    # blowing up during shutdown, OOM, or a future refactor adding code
    # in the gap between Lane 1 publish and slot_handed_off=True).
    fake_tasks = MagicMock()
    fake_tasks.add.side_effect = RuntimeError("simulated post-publish failure")

    int_patch, intelligence_instance = _patch_intelligence_service()

    with pub_patch, int_patch, \
         patch.object(text_clean_service, "_BACKGROUND_TASKS", fake_tasks):
        with pytest.raises(RuntimeError, match="simulated post-publish failure"):
            await _call_process(_build_envelope(), lane2_extras=None)

    # Lane 1 ran (publish succeeded before the failure).
    publisher_instance.publish_envelope.assert_awaited_once()
    # The slot was released exactly ONCE by process()'s finally — counter
    # back to 0, not -1.
    assert text_clean_service.get_lane2_in_flight() == 0


@pytest.mark.asyncio
async def test_lane1_publish_error_preserves_underlying_cause():
    """Lane1PublishError chains the original exception via __cause__."""
    assert try_reserve_lane2_slot()
    boom = RuntimeError("simulated Kinesis outage")

    publisher_instance = MagicMock()
    publisher_instance.publish_envelope = AsyncMock(side_effect=boom)
    pub_patch = patch(
        "services.text_clean_service.AWSEventPublisher",
        return_value=publisher_instance,
    )
    int_patch, _ = _patch_intelligence_service()

    with pub_patch, int_patch:
        with pytest.raises(Lane1PublishError) as exc_info:
            await _call_process(_build_envelope(), lane2_extras=None)

    assert exc_info.value.__cause__ is boom


# ---------------------------------------------------------------------------
# process() — observability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_logs_lane1_error_when_publish_raises(caplog):
    """Lane 1 raise produces 'Lane 1 (publishing) raised' ERROR log.

    Same string the integration test
    (``test_text_clean_lane1_failure_produces_5xx``) asserts on so the
    contract is locked at both layers.
    """
    assert try_reserve_lane2_slot()
    publisher_instance = MagicMock()
    publisher_instance.publish_envelope = AsyncMock(
        side_effect=RuntimeError("kaboom")
    )
    pub_patch = patch(
        "services.text_clean_service.AWSEventPublisher",
        return_value=publisher_instance,
    )
    int_patch, _ = _patch_intelligence_service()

    with caplog.at_level(logging.ERROR, logger="services.text_clean_service"), \
         pub_patch, int_patch:
        with pytest.raises(Lane1PublishError):
            await _call_process(_build_envelope(), lane2_extras=None)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        "Lane 1 (publishing) raised" in r.getMessage() for r in error_records
    )


@pytest.mark.asyncio
async def test_process_lane2_exception_surfaces_via_on_done(caplog):
    """When Lane 2 raises, _on_done logs ERROR (not silent task death)."""
    assert try_reserve_lane2_slot()
    pub_patch, _ = _patch_aws_publisher()

    int_patch, _ = _patch_intelligence_service(
        side_effect=RuntimeError("Lane 2 boom")
    )

    envelope = _build_envelope()
    with caplog.at_level(logging.ERROR, logger="services.text_clean_service"), \
         pub_patch, int_patch:
        await _call_process(envelope, lane2_extras=None)
        # Let the background task run + _on_done fire.
        for _ in range(10):
            await asyncio.sleep(0)

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    # Two ERROR logs: one from _lane2_intelligence's inner except, one from
    # _on_done's safety net (the latter is the load-bearing one).
    assert any(
        "Lane 2 background task crashed" in r.getMessage()
        or "Lane 2 (intelligence) failed" in r.getMessage()
        for r in error_records
    ), "Lane 2 exception must produce an observable ERROR log."


@pytest.mark.asyncio
async def test_process_lane2_completion_logs_info(caplog):
    """Successful Lane 2 completion logs an INFO line via _on_done."""
    assert try_reserve_lane2_slot()
    pub_patch, _ = _patch_aws_publisher()
    int_patch, _ = _patch_intelligence_service(return_value=MagicMock())

    envelope = _build_envelope()
    with caplog.at_level(logging.INFO, logger="services.text_clean_service"), \
         pub_patch, int_patch:
        await _call_process(envelope, lane2_extras=None)
        for _ in range(10):
            await asyncio.sleep(0)

    info_records = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert any(
        "Lane 2 (intelligence) completed" in r.getMessage() for r in info_records
    )


@pytest.mark.asyncio
async def test_process_lane1_disabled_returns_lane1_published_true(caplog):
    """publish_envelope returning {None, None} is NOT treated as failure.

    Pins ``test_text_clean_allows_null_publish_when_aws_disabled`` at the
    service layer: when both Kinesis + EventBridge publishing are disabled
    via env vars, publish_envelope returns {None, None}. Process must
    treat that as a successful publish (not raise Lane1PublishError).
    """
    assert try_reserve_lane2_slot()
    pub_patch, _ = _patch_aws_publisher(
        return_value={"kinesis_sequence": None, "eventbridge_id": None}
    )
    int_patch, _ = _patch_intelligence_service()

    envelope = _build_envelope()
    with caplog.at_level(logging.INFO, logger="services.text_clean_service"), \
         pub_patch, int_patch:
        result = await _call_process(envelope, lane2_extras=None)
        for _ in range(3):
            await asyncio.sleep(0)

    assert result.lane1_published is True
    assert result.lane2_dispatched is True
    # Log should record per-destination "failed-or-disabled".
    info_records = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert any(
        "failed-or-disabled" in r.getMessage() for r in info_records
    )


# ---------------------------------------------------------------------------
# Lane 2 fire-and-forget property
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_does_not_await_lane2():
    """process() returns BEFORE the Lane 2 task completes.

    Pins the response-decoupling contract that the integration test
    enforces at the route layer
    (``test_text_clean_returns_before_lane_2_completes``). If a future
    refactor accidentally awaits the Lane 2 task before returning, this
    fails — at the layer where the bug would actually live.
    """
    assert try_reserve_lane2_slot()
    pub_patch, _ = _patch_aws_publisher()

    lane2_started = asyncio.Event()
    lane2_done = asyncio.Event()

    async def _slow(**_kwargs):
        lane2_started.set()
        await asyncio.sleep(0.3)
        lane2_done.set()
        return MagicMock()

    int_patch, _ = _patch_intelligence_service(side_effect=_slow)

    envelope = _build_envelope()
    with pub_patch, int_patch:
        result = await _call_process(envelope, lane2_extras=None)

        # process() returned. Lane 2 should have started but NOT completed.
        assert result.lane2_dispatched is True
        # Yield once for the task body's first statement to run (the
        # lane2_started.set() line is before the sleep). Stay inside the
        # patch context so IntelligenceService is still mocked when the
        # background task actually executes.
        await asyncio.sleep(0)
        assert lane2_started.is_set(), (
            "Lane 2 task didn't start — fire-and-forget dispatch is broken."
        )
        assert not lane2_done.is_set(), (
            "Lane 2 task completed before process() returned — fire-and-forget "
            "regression. process() is awaiting Lane 2."
        )

        # Drain inside the patch context.
        for _ in range(50):
            if lane2_done.is_set():
                break
            await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# LOCKED-41 explicit identity cross-check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_raises_tenant_isolation_error_on_tenant_mismatch():
    """tenant_id kwarg ≠ envelope.tenant_id → TenantIsolationError, no side effects.

    LOCKED-41 cross-tenant defense-in-depth (Codex PR-X1 R3 P2). A caller
    bug that constructs an envelope under the wrong tenant must surface as
    a loud error BEFORE Lane 1 publishes — otherwise the bug would persist
    cross-tenant data and only be detected via downstream forensics.
    """
    assert try_reserve_lane2_slot()
    assert text_clean_service.get_lane2_in_flight() == 1

    pub_patch, publisher_instance = _patch_aws_publisher()
    int_patch, intelligence_instance = _patch_intelligence_service()

    envelope = _build_envelope()
    wrong_tenant = uuid.uuid4()
    assert wrong_tenant != envelope.tenant_id

    with pub_patch, int_patch:
        with pytest.raises(TenantIsolationError, match="tenant_id mismatch"):
            await process(
                tenant_id=wrong_tenant,
                user_id=envelope.user_id,
                account_id=envelope.account_id,
                envelope=envelope,
                lane2_extras=None,
            )

    # No side effects: Lane 1 not called, Lane 2 not dispatched, slot released.
    publisher_instance.publish_envelope.assert_not_called()
    intelligence_instance.process_transcript.assert_not_called()
    assert text_clean_service.get_lane2_in_flight() == 0


@pytest.mark.asyncio
async def test_process_raises_tenant_isolation_error_on_user_mismatch():
    """user_id kwarg ≠ envelope.user_id → TenantIsolationError."""
    assert try_reserve_lane2_slot()
    pub_patch, publisher_instance = _patch_aws_publisher()
    int_patch, _ = _patch_intelligence_service()

    envelope = _build_envelope()
    with pub_patch, int_patch:
        with pytest.raises(TenantIsolationError, match="user_id mismatch"):
            await process(
                tenant_id=envelope.tenant_id,
                user_id="auth0|some-other-user",
                account_id=envelope.account_id,
                envelope=envelope,
                lane2_extras=None,
            )

    publisher_instance.publish_envelope.assert_not_called()
    assert text_clean_service.get_lane2_in_flight() == 0


@pytest.mark.asyncio
async def test_process_raises_tenant_isolation_error_on_account_mismatch():
    """account_id kwarg ≠ envelope.account_id → TenantIsolationError."""
    assert try_reserve_lane2_slot()
    pub_patch, publisher_instance = _patch_aws_publisher()
    int_patch, _ = _patch_intelligence_service()

    envelope = _build_envelope()
    with pub_patch, int_patch:
        with pytest.raises(TenantIsolationError, match="account_id mismatch"):
            await process(
                tenant_id=envelope.tenant_id,
                user_id=envelope.user_id,
                account_id=str(uuid.uuid4()),
                envelope=envelope,
                lane2_extras=None,
            )

    publisher_instance.publish_envelope.assert_not_called()
    assert text_clean_service.get_lane2_in_flight() == 0


@pytest.mark.asyncio
async def test_process_accepts_tenant_id_as_str_or_uuid():
    """tenant_id comparison normalizes via str() so caller can pass either form.

    Granola adapter passes credential.tenant_id (UUID); /text/clean passes
    UUID(context.tenant_id) — both are UUID-typed at the call site, but the
    envelope might be constructed with the same UUID. Comparison normalizes
    so a caller bug substituting str(uuid) wouldn't fail the cross-check.
    """
    assert try_reserve_lane2_slot()
    pub_patch, _ = _patch_aws_publisher()
    int_patch, intelligence_instance = _patch_intelligence_service()

    envelope = _build_envelope()
    # Pass str form of tenant_id; envelope has the UUID form. The
    # comparison should still pass.
    with pub_patch, int_patch:
        await process(
            tenant_id=envelope.tenant_id,  # already UUID
            user_id=envelope.user_id,
            account_id=envelope.account_id,
            envelope=envelope,
            lane2_extras=None,
        )
        for _ in range(5):
            await asyncio.sleep(0)
            if intelligence_instance.process_transcript.await_count > 0:
                break

    intelligence_instance.process_transcript.assert_awaited_once()


# ---------------------------------------------------------------------------
# Empty-string cleaned_transcript override (Codex R3 P3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_preserves_empty_string_cleaned_transcript_override():
    """An explicit empty-string override is NOT treated as missing.

    Codex PR-X1 R3 P3: pre-fix, ``(lane2_extras.cleaned_transcript or
    envelope.content.text)`` evaluated ``""`` as falsy and fell back to
    envelope.content.text. The pre-extraction route handler passed
    BatchCleanerService's exact output (including ``""``) to Lane 2; this
    test pins that semantics post-extraction.
    """
    assert try_reserve_lane2_slot()
    pub_patch, _ = _patch_aws_publisher()
    int_patch, intelligence_instance = _patch_intelligence_service()

    envelope = _build_envelope(text="this is the envelope text, NOT the Lane 2 input")
    extras = Lane2Extras(cleaned_transcript="")  # explicit empty-string override

    with pub_patch, int_patch:
        await _call_process(envelope, lane2_extras=extras)
        for _ in range(5):
            await asyncio.sleep(0)
            if intelligence_instance.process_transcript.await_count > 0:
                break

    call_kwargs = intelligence_instance.process_transcript.await_args.kwargs
    assert call_kwargs["cleaned_transcript"] == "", (
        "Empty-string override was treated as missing — Lane 2 received the "
        "envelope text instead of the explicit empty string. See Codex R3 P3."
    )
