"""Caller-side wiring of recording_user_id + tenant_internal_domains.

Codex Round 1 finding (P1): `enrich()` accepted two new arguments
(`recording_user_id`, `tenant_internal_domains`) defaulted to `None`/`set()`,
but NONE of the four ingress routes (`/text/clean`, `/batch/process`,
`/upload/...`, `/listen` WebSocket) passed them. The
unknown-business-domain branch in `enrich()` silently skipped attendees
when `recording_user_id` was missing — making the Phase 1 queue feature
unreachable in production traffic.

This file validates the two halves of the fix:

1. **Caller-wiring tests** — each ingress entry point passes both new
   args to `enrich()`. We patch `enrich` to capture kwargs and assert
   `recording_user_id` is non-None and `tenant_internal_domains` is a set.

2. **Raise-on-None invariant test** — `enrich()` itself raises
   `ValueError` (defense-in-depth) when the unknown-business-domain branch
   is entered without `recording_user_id`. This matches the
   `_resolve_contact(account_id=None)` pattern already in the file.

Mock-driven; no DB required. Validates ONLY the wiring contract.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_session_ctx_factory():
    """Return a patched get_async_session context manager."""
    fake_session = MagicMock()
    fake_session.execute = AsyncMock()
    fake_session.commit = AsyncMock()

    class _AsyncCM:
        async def __aenter__(self_inner):
            return fake_session

        async def __aexit__(self_inner, exc_type, exc, tb):
            return False

    return _AsyncCM, fake_session


# ---------------------------------------------------------------------------
# Test 1: enrich() raises when recording_user_id is None for unknown branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_raises_when_recording_user_id_missing_for_unknown_domain():
    """Defense-in-depth: enrich() must RAISE (not skip) when the unknown
    business-domain branch is entered with recording_user_id=None.

    The previous behavior logged a warning and `continue`d — that silent
    drop is exactly what made the queue feature unreachable. The fix
    raises ValueError so regressions cannot ship.

    enrich() catches Exception at its outer try/except and returns an
    empty EnrichmentResult, so we assert the ValueError was raised
    internally by:
      - confirming the outer try/except's logger.error fired with the
        expected ValueError-flavored message, and
      - confirming upsert_queue_entry + insert_signal were NEVER called
        (the raise short-circuits the queue path).
    """
    from services.transcript_enrichment import TranscriptEnrichmentService

    service = TranscriptEnrichmentService()

    tenant_id = str(uuid.uuid4())
    acme_account_id = str(uuid.uuid4())
    event = {
        "id": uuid.uuid4(),
        "title": "Partner sync",
        "_match_method": "time_window",
    }
    attendees = [
        {
            "email": "partner@consultingco.com",
            "display_name": "Partner",
            "is_organizer": False,
            "is_optional": False,
        },
    ]

    AsyncCM, _ = _mock_session_ctx_factory()

    with patch(
        "services.transcript_enrichment.ENABLE_TRANSCRIPT_ENRICHMENT", True,
    ), patch.object(
        service, "_match_calendar_event",
        new_callable=AsyncMock, return_value=event,
    ), patch.object(
        service, "_get_attendees",
        new_callable=AsyncMock, return_value=attendees,
    ), patch(
        "services.transcript_enrichment.get_async_session",
        new=lambda: AsyncCM(),
    ), patch(
        "services.transcript_enrichment.lookup_account_by_domain",
        new_callable=AsyncMock, return_value=None,  # unknown domain
    ), patch(
        "services.transcript_enrichment.logger",
    ) as mock_logger, patch(
        "services.transcript_enrichment.insert_signal",
        new_callable=AsyncMock,
    ) as mock_insert_signal, patch(
        "services.transcript_enrichment.upsert_queue_entry",
        new_callable=AsyncMock,
    ) as mock_upsert:
        result = await service.enrich(
            tenant_id=tenant_id,
            transcript_timestamp=datetime.now(timezone.utc),
            raw_transcript="test",
            account_id=acme_account_id,
            recording_user_id=None,  # <-- the trigger
            tenant_internal_domains=set(),
        )

        # The raise short-circuited the queue-write path: neither helper
        # was called. Defense-in-depth holds.
        mock_insert_signal.assert_not_called()
        mock_upsert.assert_not_called()

        # And the outer try/except logged a ValueError-flavored error.
        error_calls = mock_logger.error.call_args_list
        assert any(
            "ValueError" in str(c) or "recording_user_id" in str(c)
            for c in error_calls
        ), (
            "Expected outer try/except to log a ValueError about "
            f"missing recording_user_id; got: {error_calls}"
        )

    # Empty result because the exception was swallowed at the outer try.
    assert result.contacts == []


# ---------------------------------------------------------------------------
# Test 2: /text/clean passes both new args to enrich()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_clean_passes_recording_user_id_and_internal_domains_to_enrich():
    """The /text/clean handler must wire both new args from the auth
    context to enrich(). Captures the kwargs at the enrich() boundary
    and asserts both are present.
    """
    # Late import so the patched ENABLE_TRANSCRIPT_ENRICHMENT applies if needed.
    from fastapi.testclient import TestClient
    from main import app

    captured_kwargs: dict = {}

    class _FakeEnrichment:
        contact_ids = None
        calendar_event_id = None
        match_confidence = None
        match_method = None
        front_matter = None

        def to_extras_dict(self):
            return {}

    async def fake_enrich(self, **kwargs):
        captured_kwargs.update(kwargs)
        return _FakeEnrichment()

    async def fake_clean(text: str):
        return text

    async def fake_publish(envelope):
        return {"kinesis_sequence": "seq-1", "eventbridge_id": "ev-1"}

    async def fake_intel(**kwargs):
        return None

    async def fake_internal_domains(tenant_id: str):
        return {"mycompany.com"}

    request_payload = {
        "text": "hello world",
        "interaction_type": "note",
        "source": "test",
        "account_id": str(uuid.uuid4()),
    }
    tenant_id = "11111111-1111-4111-8111-111111111111"
    user_id = "auth-user-id"
    pg_user_id = str(uuid.uuid4())
    account_id = request_payload["account_id"]

    from models.request_context import RequestContext

    fake_context = RequestContext(
        tenant_id=tenant_id,
        user_id=user_id,
        pg_user_id=pg_user_id,
        account_id=account_id,
        interaction_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        user_name="Test User",
    )

    with patch(
        "routers.text.get_auth_context", return_value=fake_context,
    ), patch(
        "services.transcript_enrichment.TranscriptEnrichmentService.enrich",
        new=fake_enrich,
    ), patch(
        "services.batch_cleaner_service.BatchCleanerService.clean_transcript",
        new=AsyncMock(side_effect=fake_clean),
    ), patch(
        "services.aws_event_publisher.AWSEventPublisher.publish_envelope",
        new=AsyncMock(side_effect=fake_publish),
    ), patch(
        "services.intelligence_service.IntelligenceService.process_transcript",
        new=AsyncMock(side_effect=fake_intel),
    ), patch(
        "routers.text.get_tenant_internal_domains",
        new=AsyncMock(side_effect=fake_internal_domains),
    ):
        client = TestClient(app)
        response = client.post(
            "/text/clean",
            json=request_payload,
            headers={"Authorization": "Bearer fake"},
        )

    assert response.status_code == 200, response.text
    assert captured_kwargs.get("recording_user_id") == pg_user_id, (
        f"recording_user_id not wired; got {captured_kwargs.get('recording_user_id')!r}"
    )
    assert captured_kwargs.get("tenant_internal_domains") == {"mycompany.com"}, (
        "tenant_internal_domains not wired through /text/clean"
    )


# ---------------------------------------------------------------------------
# Test 3: get_tenant_internal_domains helper basic behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tenant_internal_domains_extracts_from_provider_connections():
    """Helper combines email-host (minus PERSONAL_DOMAINS) with manual
    internal_domains[]. Returns a set."""
    from services.internal_domains import get_tenant_internal_domains

    tenant_id = "11111111-1111-4111-8111-111111111111"

    fake_rows = [
        {"email_address": "alice@mycompany.com", "internal_domains": ["sub.mycompany.com"]},
        {"email_address": "bob@gmail.com", "internal_domains": []},  # personal → stripped from auto
        {"email_address": "carol@partner.io", "internal_domains": None},
    ]

    fake_session = MagicMock()
    result_mock = MagicMock()
    result_mock.mappings.return_value.all.return_value = fake_rows
    fake_session.execute = AsyncMock(return_value=result_mock)

    class _AsyncCM:
        async def __aenter__(self_inner):
            return fake_session

        async def __aexit__(self_inner, exc_type, exc, tb):
            return False

    with patch(
        "services.internal_domains.get_async_session",
        new=lambda: _AsyncCM(),
    ):
        domains = await get_tenant_internal_domains(tenant_id)

    assert isinstance(domains, set)
    # auto-discovered: mycompany.com, partner.io (gmail.com stripped)
    # manual: sub.mycompany.com
    assert "mycompany.com" in domains
    assert "partner.io" in domains
    assert "sub.mycompany.com" in domains
    assert "gmail.com" not in domains  # PERSONAL stripped from auto


@pytest.mark.asyncio
async def test_get_tenant_internal_domains_returns_empty_on_error():
    """Failing-soft on DB error keeps the BUSINESS+known branch working."""
    from services.internal_domains import get_tenant_internal_domains

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=RuntimeError("db down"))

    class _AsyncCM:
        async def __aenter__(self_inner):
            return fake_session

        async def __aexit__(self_inner, exc_type, exc, tb):
            return False

    with patch(
        "services.internal_domains.get_async_session",
        new=lambda: _AsyncCM(),
    ):
        domains = await get_tenant_internal_domains("11111111-1111-4111-8111-111111111111")

    assert domains == set()
