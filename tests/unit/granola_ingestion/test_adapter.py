"""Unit tests for :mod:`services.granola_ingestion.adapter`.

AsyncMock + monkeypatch per ``feedback_test_pattern_no_docker.md``. Tests
cover the LOCKED-decision-bearing branches of Path 2 — Scenario A/C/D,
credential-level error classification (auth/folder/transient), per-note
error classification (NOTE_NOT_FOUND as per-note skip vs credential
breakage), envelope construction per LOCKED-35/36, granola_note_snapshot
recoverability per LOCKED-44, and the LOCKED-41 tenant-isolation flow.

Real Neon + real Granola E2E lives in Phase 4 with Peter as design
partner #0 (per the plan); these tests pin the local-code regressions.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from services import text_clean_service
from services.contact_resolution import ResolvedContactRow
from services.domain_classification import DomainClass
from services.granola_ingestion import adapter
from services.granola_ingestion.api_client import GranolaAPIClient
from services.granola_ingestion.errors import GranolaError, GranolaErrorCode
from services.granola_ingestion.models import (
    Attendee,
    CalendarEvent,
    GranolaNoteDetail,
    GranolaNoteSummary,
    TranscriptTurn,
)
from services.granola_ingestion.outcomes import IngestionOutcome
from services.granola_ingestion.path2 import (
    AttendeeClassification,
    PathTwoDecision,
    Scenario,
)


# ---------------------------------------------------------------------------
# Mock infrastructure
# ---------------------------------------------------------------------------


class _FakeConn:
    """Stand-in asyncpg Connection for the adapter's SQL helpers.

    Tests configure ``fetchrow_returns`` (a single dict-like for SELECT
    by composite UNIQUE) and ``fetch_returns`` (a list for the
    deferred-rows query). The mock records every call so assertions can
    inspect the SQL parameters.
    """

    def __init__(
        self,
        *,
        fetchrow_returns: Optional[dict[str, Any]] = None,
        fetchrow_seq: Optional[list[Optional[dict[str, Any]]]] = None,
        fetch_returns: Optional[list[dict[str, Any]]] = None,
        fetchval_returns: Any = True,
        fetchval_seq: Optional[list[Any]] = None,
    ) -> None:
        # ``fetchrow_seq`` lets tests return DIFFERENT rows for sequential
        # fetchrow calls (e.g. _get_integration_run before vs after a
        # cycle records a new state). When provided, takes precedence
        # over ``fetchrow_returns``.
        self.fetchrow_returns = fetchrow_returns
        self.fetchrow_seq = list(fetchrow_seq) if fetchrow_seq else None
        self.fetch_returns = fetch_returns or []
        # ``fetchval`` backs the edge #12 per-note active-recheck
        # (``_credential_is_active`` issues ``SELECT EXISTS(...)``). Defaults
        # to True ("credential still active") so cycle tests that don't care
        # about mid-cycle deactivation behave exactly as before. ``fetchval_seq``
        # returns DIFFERENT values for sequential calls (active for note 1,
        # archived for note 2, etc.).
        self.fetchval_returns = fetchval_returns
        self.fetchval_seq = list(fetchval_seq) if fetchval_seq else None
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchval_calls: list[tuple[str, tuple]] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.execute_calls.append((sql, args))
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> Optional[dict[str, Any]]:
        self.fetchrow_calls.append((sql, args))
        if self.fetchrow_seq is not None:
            if not self.fetchrow_seq:
                return None
            return self.fetchrow_seq.pop(0)
        return self.fetchrow_returns

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append((sql, args))
        return list(self.fetch_returns)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.fetchval_calls.append((sql, args))
        if self.fetchval_seq is not None:
            if not self.fetchval_seq:
                return None
            return self.fetchval_seq.pop(0)
        return self.fetchval_returns


class _FakeAcquireCM:
    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


class _FakePool:
    """Stand-in asyncpg Pool — returns the SAME _FakeConn for every acquire."""

    def __init__(self, conn: _FakeConn) -> None:
        self.conn = conn

    def acquire(self) -> _FakeAcquireCM:
        return _FakeAcquireCM(self.conn)


@dataclass
class _FakeCredential:
    """Duck-typed stand-in for :class:`services.vault.GranolaCredential`.

    Adapter only reads attributes; tests don't need the real dataclass
    (which would require importing vault → cryptography). Construction
    is keyword-only so unused fields stay defaulted.
    """

    id: UUID
    tenant_id: UUID
    user_id: UUID
    provider: str = "granola"
    api_key: str = "grn_fake_key"
    config: dict = field(default_factory=lambda: {"folder_id": "fol_test", "folder_name": "EQ"})
    status: str = "active"
    last_polled_at: Optional[datetime] = None
    last_error: Optional[dict] = None
    consecutive_failures: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    archived_at: Optional[datetime] = None


def _build_credential(
    *,
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    status: str = "active",
    consecutive_failures: int = 0,
    last_polled_at: Optional[datetime] = None,
) -> _FakeCredential:
    return _FakeCredential(
        id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        user_id=user_id or uuid4(),
        status=status,
        consecutive_failures=consecutive_failures,
        last_polled_at=last_polled_at,
    )


def _make_note_summary(note_id: str = "not_test_1") -> GranolaNoteSummary:
    return GranolaNoteSummary(
        id=note_id,
        title=f"Meeting {note_id}",
        created_at=datetime(2026, 5, 24, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 24, 10, 30, tzinfo=timezone.utc),
        folder_membership=[],
    )


def _make_note_detail(
    *,
    note_id: str = "not_test_1",
    attendees: Optional[list[Attendee]] = None,
    transcript: Optional[list[TranscriptTurn]] = None,
    title: str = "Quarterly review",
    calendar_event_id: Optional[str] = "cal_evt_1",
    summary_text: Optional[str] = "Discussed Q3 numbers.",
    web_url: Optional[str] = "https://granola.ai/notes/not_test_1",
) -> GranolaNoteDetail:
    return GranolaNoteDetail(
        id=note_id,
        title=title,
        created_at=datetime(2026, 5, 24, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 24, 10, 30, tzinfo=timezone.utc),
        attendees=attendees if attendees is not None else [Attendee(email="alice@bigco.com", name="Alice")],
        calendar_event=CalendarEvent(calendar_event_id=calendar_event_id) if calendar_event_id else None,
        transcript=transcript if transcript is not None else [
            TranscriptTurn(text="Hello.", speaker={"source": "microphone"}),
            TranscriptTurn(text="Hi there.", speaker={"source": "speaker"}),
        ],
        summary_markdown=None,
        summary_text=summary_text,
        web_url=web_url,
        folder_membership=[],
    )


# A canned resolved contact for Scenario-A-path tests that exercise
# _ingest_scenario_a's OTHER behaviors (success row, retry, cycle abort,
# reprocess) and don't care about contact-resolution internals (those are
# covered by the dedicated tests at the bottom of this file). These tests
# patch _resolve_known_account_contacts to return this, so the path doesn't
# hit real DB work via the bare-MagicMock session. account_id matches the
# lookup_account_by_domain patch return used across these tests.
_FAKE_RESOLVED_CONTACTS = [
    ResolvedContactRow(
        contact_id="11111111-cccc-4111-8111-cccccccccccc",
        email="alice@bigco.com",
        name="Alice",
        account_id="11111111-aaaa-4111-8111-111111111111",
        account_matched=True,
    )
]


@pytest.fixture(autouse=True)
def _reset_text_clean_state():
    """Each test gets a fresh Lane 2 counter + task set + clean patches."""
    text_clean_service._INFLIGHT_LANE2[0] = 0
    text_clean_service._BACKGROUND_TASKS.clear()
    yield
    text_clean_service._INFLIGHT_LANE2[0] = 0
    text_clean_service._BACKGROUND_TASKS.clear()


# ---------------------------------------------------------------------------
# Envelope construction (LOCKED-25/35/36)
# ---------------------------------------------------------------------------


def test_build_envelope_matches_locked_35_36():
    """Envelope shape exactly matches the locked decisions.

    Wire format that ``scripts/verify_consumer_contracts.py`` checks
    pre-merge: source="generic", interaction_type="meeting",
    content.format="plain", and exactly six granola_* extras keys.
    """
    credential = _build_credential()
    detail = _make_note_detail(
        attendees=[Attendee(email="alice@bigco.com", name="Alice Adams")],
    )
    decision = SimpleNamespace(  # minimal stand-in
        scenario=Scenario.A_KNOWN_ANCHOR,
        anchor_account_id="11111111-aaaa-4111-8111-111111111111",
        known_account_attendees=[
            AttendeeClassification(
                email="alice@bigco.com",
                name="Alice Adams",
                domain="bigco.com",
                klass=DomainClass.BUSINESS,
                account_id="11111111-aaaa-4111-8111-111111111111",
            )
        ],
        unknown_business_attendees=[],
        personal_attendees=[],
        internal_attendees=[],
    )

    envelope = adapter._build_envelope(
        credential=credential,  # type: ignore[arg-type]
        detail=detail,
        anchor_account_id="11111111-aaaa-4111-8111-111111111111",
        decision=decision,  # type: ignore[arg-type]
    )

    assert envelope.source == "generic"
    assert envelope.interaction_type == "meeting"
    assert envelope.content.format == "plain"
    assert envelope.account_id == "11111111-aaaa-4111-8111-111111111111"
    assert envelope.tenant_id == credential.tenant_id
    assert envelope.user_id == str(credential.user_id)
    assert envelope.interaction_id is not None
    # trace_id MUST be a valid UUID string: the shared Lane 2 path does
    # UUID(trace_id) in intelligence_service._persist_intelligence (via
    # text_clean_service passing ``envelope.trace_id or ""``). A None/empty
    # trace_id crashed Lane 2 persistence for the first real Granola ingest
    # (2026-05-26 E2E). Granola has no request-scoped trace_id, so the adapter
    # mints one. (/text/clean passes its own context.trace_id, unaffected.)
    assert envelope.trace_id is not None
    UUID(envelope.trace_id)  # raises if not a valid UUID

    expected_extras_keys = {
        "granola_note_id",
        "granola_web_url",
        "granola_folder_name",
        "granola_summary_text",
        "granola_calendar_event_id",
        "granola_attendees_raw",
    }
    assert set(envelope.extras.keys()) == expected_extras_keys
    assert envelope.extras["granola_note_id"] == detail.id
    assert envelope.extras["granola_web_url"] == detail.web_url
    assert envelope.extras["granola_folder_name"] == "EQ"
    assert envelope.extras["granola_calendar_event_id"] == "cal_evt_1"
    assert envelope.extras["granola_summary_text"] == detail.summary_text
    assert len(envelope.extras["granola_attendees_raw"]) == 1


def test_render_content_text_includes_front_matter_and_speaker_labels():
    """content.text = YAML front-matter + speaker-tagged transcript turns."""
    credential = _build_credential()
    detail = _make_note_detail(
        attendees=[Attendee(email="alice@bigco.com", name="Alice"), Attendee(email="bob@bigco.com")],
        transcript=[
            TranscriptTurn(text="First line.", speaker={"source": "microphone"}),
            TranscriptTurn(text="Second.", speaker={"source": "speaker"}),
        ],
        title='With "quotes" in title',
    )
    # Cheap synthetic decision: both attendees are known.
    decision = SimpleNamespace(
        scenario=Scenario.A_KNOWN_ANCHOR,
        anchor_account_id="11111111-aaaa-4111-8111-111111111111",
        known_account_attendees=[
            AttendeeClassification(
                email="alice@bigco.com", name="Alice", domain="bigco.com",
                klass=DomainClass.BUSINESS, account_id="11111111-aaaa-4111-8111-111111111111",
            ),
            AttendeeClassification(
                email="bob@bigco.com", name=None, domain="bigco.com",
                klass=DomainClass.BUSINESS, account_id="11111111-aaaa-4111-8111-111111111111",
            ),
        ],
        unknown_business_attendees=[],
        personal_attendees=[],
        internal_attendees=[],
    )

    text = adapter._render_content_text(
        detail=detail, decision=decision, credential=credential,  # type: ignore[arg-type]
    )

    # Front-matter shape
    assert text.startswith("---\n")
    assert "type: meeting" in text
    # Quotes are escaped
    assert 'title: "With \\"quotes\\" in title"' in text
    assert "date: 2026-05-24T10:00:00Z" in text
    assert "attendees:" in text
    assert "  - alice@bigco.com (Alice)" in text
    assert "  - bob@bigco.com" in text  # no name → just email
    # Separator
    assert "---\n\n[microphone] First line." in text
    assert "[speaker] Second." in text


def test_render_transcript_turns_handles_empty_speaker():
    """Turn with no speaker dict defaults to [speaker] label."""
    turns = [
        TranscriptTurn(text="No speaker dict"),
        TranscriptTurn(text="Wrong shape", speaker={"not_source": "x"}),
    ]
    text = adapter._render_transcript_turns(turns)
    assert text == "[speaker] No speaker dict\n[speaker] Wrong shape"


def test_render_transcript_empty_produces_just_front_matter():
    """Zero-audio captures are legitimate (Phase 2c spec). No \\n\\n trailer."""
    credential = _build_credential()
    detail = _make_note_detail(transcript=[])
    decision = SimpleNamespace(
        scenario=Scenario.D_NO_BUSINESS,
        anchor_account_id=None,
        known_account_attendees=[],
        unknown_business_attendees=[],
        personal_attendees=[],
        internal_attendees=[],
    )
    text = adapter._render_content_text(
        detail=detail, decision=decision, credential=credential,  # type: ignore[arg-type]
    )
    assert text.startswith("---\ntype: meeting\n")
    assert text.endswith("---")
    assert "\n\n[" not in text


# ---------------------------------------------------------------------------
# Scenario A: known account → ingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_a_calls_text_clean_with_credential_tenant_id():
    """LOCKED-41 cross-tenant guard: text_clean_service.process must see
    credential.tenant_id as the explicit kwarg.

    Load-bearing for Phase 2d's tenant-isolation contract — a regression
    here would publish under the wrong tenant.
    """
    tenant_a = uuid4()
    credential = _build_credential(tenant_id=tenant_a)
    note_summary = _make_note_summary("not_a1")
    note_detail = _make_note_detail(
        note_id="not_a1",
        attendees=[Attendee(email="bob@bigco.com", name="Bob")],
    )

    conn = _FakeConn(fetchrow_returns=None)  # no existing run
    pool = _FakePool(conn)

    captured: dict = {}

    async def _fake_process(**kwargs):
        captured.update(kwargs)
        return text_clean_service.ProcessResult(
            interaction_id="00000000-0000-4000-8000-000000000001",
            lane1_published=True,
            lane2_dispatched=True,
        )

    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(return_value=note_detail)

    with patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value="11111111-aaaa-4111-8111-111111111111")), \
         patch.object(adapter, "get_async_session") as get_sess, \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "process", new=AsyncMock(side_effect=_fake_process)):

        # get_async_session is used in _classify_and_resolve for the
        # per-domain account lookups; provide a contextmanager mock.
        sess_cm = MagicMock()
        sess_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        sess_cm.__aexit__ = AsyncMock(return_value=None)
        get_sess.return_value = sess_cm

        outcome = await adapter.process_note(
            credential=credential,  # type: ignore[arg-type]
            note_summary=note_summary,
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    assert outcome is IngestionOutcome.SUCCESS
    # Critical: tenant_id propagated explicitly from credential
    assert captured["tenant_id"] == tenant_a
    assert captured["user_id"] == str(credential.user_id)
    assert captured["account_id"] == "11111111-aaaa-4111-8111-111111111111"
    # Lane 2 extras now carries the resolved contact_ids (the fix): this is
    # what drives _persist_contact_links to write raw_interactions +
    # interaction_contact_links. cleaned_transcript stays unset (Granola is
    # pre-clean; envelope.content.text is the source of truth).
    assert captured["lane2_extras"] is not None
    assert captured["lane2_extras"].contact_ids == ["11111111-cccc-4111-8111-cccccccccccc"]
    assert captured["lane2_extras"].cleaned_transcript is None


@pytest.mark.asyncio
async def test_scenario_a_records_success_row_in_external_integration_runs():
    """After ingest, UPSERT external_integration_runs with status='success'."""
    credential = _build_credential()
    note_summary = _make_note_summary("not_a2")
    note_detail = _make_note_detail(
        note_id="not_a2",
        attendees=[Attendee(email="bob@bigco.com")],
    )

    conn = _FakeConn(fetchrow_returns=None)
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(return_value=note_detail)

    with patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value="11111111-aaaa-4111-8111-111111111111")), \
         patch.object(adapter, "get_async_session") as get_sess, \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "process", new=AsyncMock(return_value=text_clean_service.ProcessResult(
             interaction_id="00000000-0000-4000-8000-000000000002",
             lane1_published=True, lane2_dispatched=True))):
        sess_cm = MagicMock()
        sess_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        sess_cm.__aexit__ = AsyncMock(return_value=None)
        get_sess.return_value = sess_cm

        await adapter.process_note(
            credential=credential,  # type: ignore[arg-type]
            note_summary=note_summary,
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    # At least one execute() with the UPSERT SQL + status='success'
    upserts = [c for c in conn.execute_calls if "external_integration_runs" in c[0]]
    assert any("success" in c[1] for c in upserts)


@pytest.mark.asyncio
async def test_scenario_a_with_mixed_attendees_queues_signals_for_unknowns():
    """Scenario A (known anchor + unknown business attendees in same meeting):
    queue signals for the unknowns mirroring transcript_enrichment behavior."""
    credential = _build_credential()
    note_summary = _make_note_summary("not_a3")
    note_detail = _make_note_detail(
        note_id="not_a3",
        attendees=[
            Attendee(email="alice@knownco.com"),  # known anchor
            Attendee(email="dan@unknown-co-1.com"),
            Attendee(email="eve@unknown-co-2.com"),
            Attendee(email="frank@unknown-co-1.com"),  # dedup
        ],
    )

    conn = _FakeConn(fetchrow_returns=None)
    pool = _FakePool(conn)
    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(return_value=note_detail)

    async def _lookup(*, session, tenant_id, domain):
        return "44444444-aaaa-4111-8111-444444444444" if domain == "knownco.com" else None

    reopen_calls: list = []
    upsert_calls: list = []
    insert_signal_calls: list = []

    async def _reopen(*, session, tenant_id, domain):
        reopen_calls.append(domain)
        return None  # no archived entry; goes through upsert

    async def _upsert(*, session, tenant_id, domain, owner_user_id,
                       discovered_from_type, discovered_from_interaction_id,
                       expires_in_days=30):
        upsert_calls.append((domain, owner_user_id))
        queue_uuids = {"unknown-co-1.com": "11111111-cccc-4111-8111-111111111111", "unknown-co-2.com": "22222222-cccc-4111-8111-222222222222"}
        return queue_uuids[domain]

    async def _insert_signal(*, session, tenant_id, queue_id, proposal):
        insert_signal_calls.append((queue_id, proposal.contact_email))

    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    sess_cm.__aexit__ = AsyncMock(return_value=None)
    sess_cm.commit = AsyncMock()
    # session.commit is called inside _queue_unknown_domain_signals;
    # need it on the entered value too.
    entered = MagicMock()
    entered.commit = AsyncMock()
    sess_cm.__aenter__ = AsyncMock(return_value=entered)

    with patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(side_effect=_lookup)), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(adapter, "reopen_archived_entry", new=AsyncMock(side_effect=_reopen)), \
         patch.object(adapter, "upsert_queue_entry", new=AsyncMock(side_effect=_upsert)), \
         patch.object(adapter, "insert_signal", new=AsyncMock(side_effect=_insert_signal)), \
         patch.object(text_clean_service, "process", new=AsyncMock(return_value=text_clean_service.ProcessResult(
             interaction_id="00000000-0000-4000-8000-000000000003",
             lane1_published=True, lane2_dispatched=True))):

        outcome = await adapter.process_note(
            credential=credential,  # type: ignore[arg-type]
            note_summary=note_summary,
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    assert outcome is IngestionOutcome.SUCCESS
    # Two unique unknown domains queued (dedup)
    upsert_domains = sorted({d for d, _ in upsert_calls})
    assert upsert_domains == ["unknown-co-1.com", "unknown-co-2.com"]
    # Three signal rows (frank from already-queued unknown-co-1.com)
    assert len(insert_signal_calls) == 3


# ---------------------------------------------------------------------------
# Scenario C: defer + capture snapshot (LOCKED-44)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_c_records_deferred_with_snapshot():
    """Scenario C: defer the note; capture full snapshot per LOCKED-44.

    The snapshot must contain the load-bearing fields needed to re-run
    Path 2 against a future cycle if Granola removes the live note
    (transcript turns + attendees + title + summary + web_url +
    calendar_event_id + created_at).
    """
    credential = _build_credential()
    note_summary = _make_note_summary("not_c1")
    note_detail = _make_note_detail(
        note_id="not_c1",
        attendees=[
            Attendee(email="x@unknown-bidder.com", name="Unknown Bidder"),
        ],
    )

    conn = _FakeConn(fetchrow_returns=None)
    pool = _FakePool(conn)
    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(return_value=note_detail)

    entered = MagicMock()
    entered.commit = AsyncMock()
    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=entered)
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value=None)), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(adapter, "reopen_archived_entry", new=AsyncMock(return_value=None)), \
         patch.object(adapter, "upsert_queue_entry", new=AsyncMock(return_value="11111111-bbbb-4111-8111-111111111111")), \
         patch.object(adapter, "insert_signal", new=AsyncMock(return_value=None)), \
         patch.object(text_clean_service, "process", new=AsyncMock()) as proc:
        outcome = await adapter.process_note(
            credential=credential,  # type: ignore[arg-type]
            note_summary=note_summary,
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    assert outcome is IngestionOutcome.DEFERRED_PENDING_ACCOUNT
    # text_clean_service.process MUST NOT be called for Scenario C
    proc.assert_not_called()

    # Find the UPSERT with status='deferred_pending_account'
    upserts = [c for c in conn.execute_calls if "external_integration_runs" in c[0]]
    deferred = [c for c in upserts if "deferred_pending_account" in c[1]]
    assert len(deferred) == 1

    # The snapshot JSONB is the 14th positional arg (granola_note_snapshot)
    # per _UPSERT_INTEGRATION_RUN_SQL. Verify it carries the required fields.
    snapshot_json = deferred[0][1][13]
    snapshot = json.loads(snapshot_json)
    assert snapshot["title"] == note_detail.title
    assert snapshot["summary_text"] == note_detail.summary_text
    assert snapshot["web_url"] == note_detail.web_url
    assert snapshot["calendar_event_id"] == "cal_evt_1"
    assert "captured_at" in snapshot
    assert len(snapshot["attendees"]) == 1
    assert snapshot["attendees"][0]["email"] == "x@unknown-bidder.com"
    assert len(snapshot["transcript_turns"]) == 2


# ---------------------------------------------------------------------------
# Scenario D: no business attendees
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_d_no_business_attendees_returns_skipped():
    credential = _build_credential()
    note_summary = _make_note_summary("not_d1")
    note_detail = _make_note_detail(
        note_id="not_d1",
        attendees=[
            Attendee(email="user@gmail.com"),  # PERSONAL
        ],
    )

    conn = _FakeConn(fetchrow_returns=None)
    pool = _FakePool(conn)
    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(return_value=note_detail)

    with patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value=None)), \
         patch.object(adapter, "get_async_session") as get_sess, \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "process", new=AsyncMock()) as proc:
        sess_cm = MagicMock()
        sess_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        sess_cm.__aexit__ = AsyncMock(return_value=None)
        get_sess.return_value = sess_cm

        outcome = await adapter.process_note(
            credential=credential,  # type: ignore[arg-type]
            note_summary=note_summary,
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    assert outcome is IngestionOutcome.SKIPPED_NO_BUSINESS_ATTENDEES
    proc.assert_not_called()


# ---------------------------------------------------------------------------
# Credential-level error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_failure_marks_credential_revoked():
    credential = _build_credential()
    conn = _FakeConn()
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(side_effect=GranolaError(
        GranolaErrorCode.GRANOLA_AUTH_FAILED, "bad api key"
    ))
    client.aclose = AsyncMock()

    with patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())):
        result = await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    assert result.credential_error_code == GranolaErrorCode.GRANOLA_AUTH_FAILED.value
    # _UPDATE_CREDENTIAL_STATUS_SQL invoked with status='revoked'
    status_updates = [c for c in conn.execute_calls if "vault.user_credentials" in c[0] and "status" in c[0].lower()]
    assert any("revoked" in c[1] for c in status_updates)


@pytest.mark.asyncio
async def test_folder_not_found_marks_credential_error():
    credential = _build_credential()
    conn = _FakeConn()
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(side_effect=GranolaError(
        GranolaErrorCode.GRANOLA_FOLDER_NOT_FOUND, "folder gone"
    ))
    client.aclose = AsyncMock()

    with patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())):
        result = await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    assert result.credential_error_code == GranolaErrorCode.GRANOLA_FOLDER_NOT_FOUND.value
    status_updates = [c for c in conn.execute_calls if "vault.user_credentials" in c[0] and "status" in c[0].lower()]
    assert any("error" in c[1] for c in status_updates)


@pytest.mark.asyncio
async def test_transient_5xx_increments_consecutive_failures():
    """One 5xx → consecutive_failures += 1; credential stays active."""
    credential = _build_credential(consecutive_failures=0)
    conn = _FakeConn(fetchrow_returns={"consecutive_failures": 1})
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(side_effect=GranolaError(
        GranolaErrorCode.GRANOLA_5XX, "boom"
    ))
    client.aclose = AsyncMock()

    with patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())):
        result = await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    assert result.credential_error_code == GranolaErrorCode.GRANOLA_5XX.value
    # _INCREMENT_CREDENTIAL_FAILURES_SQL called (the only fetchrow on cred path)
    assert any("consecutive_failures + 1" in c[0] for c in conn.fetchrow_calls)
    # status was NOT set to 'error' (only 1 consecutive failure)
    status_updates = [c for c in conn.execute_calls if "vault.user_credentials" in c[0] and "status =" in c[0]]
    assert not any("error" in c[1] for c in status_updates)


@pytest.mark.asyncio
async def test_transient_failure_at_threshold_flips_status_to_error():
    """Third consecutive failure → status='error' (would trigger Phase 2g email)."""
    credential = _build_credential(consecutive_failures=2)
    # After increment, count = 3 = threshold → flip
    conn = _FakeConn(fetchrow_returns={"consecutive_failures": 3})
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(side_effect=GranolaError(
        GranolaErrorCode.GRANOLA_TIMEOUT, "slow"
    ))
    client.aclose = AsyncMock()

    with patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())):
        await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    status_updates = [c for c in conn.execute_calls if "vault.user_credentials" in c[0] and "status =" in c[0]]
    assert any("error" in c[1] for c in status_updates)


# ---------------------------------------------------------------------------
# Per-note error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_note_not_found_is_per_note_skip_not_credential_breakage():
    """LOCKED-distinct behavior: NOTE_NOT_FOUND ≠ FOLDER_NOT_FOUND.

    Phase 2c added GRANOLA_NOTE_NOT_FOUND specifically so a deleted-note
    race (note listed, deleted before detail fetch) does NOT take the
    whole credential offline. Per-note skip; credential stays active.
    """
    credential = _build_credential()
    note_summary = _make_note_summary("not_gone")

    conn = _FakeConn(fetchrow_returns=None)
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(side_effect=GranolaError(
        GranolaErrorCode.GRANOLA_NOTE_NOT_FOUND, "deleted between list and detail"
    ))

    outcome = await adapter.process_note(
        credential=credential,  # type: ignore[arg-type]
        note_summary=note_summary,
        client=client,
        pool=pool,  # type: ignore[arg-type]
        internal_domains=set(),
    )

    # Per-note skip; credential state is not touched
    assert outcome is IngestionOutcome.SKIPPED_NO_BUSINESS_ATTENDEES
    cred_updates = [c for c in conn.execute_calls if "vault.user_credentials" in c[0]]
    assert cred_updates == []
    # external_integration_runs IS updated with skip reason
    upserts = [c for c in conn.execute_calls if "external_integration_runs" in c[0]]
    assert any("skipped_no_business_attendees" in c[1] for c in upserts)
    # error_code captures the per-note reason
    assert any("note_deleted_before_detail_fetch" in c[1] for c in upserts)


@pytest.mark.asyncio
async def test_transient_note_failure_increments_retry_count():
    """A 5xx on get_note_detail → status='failed', retry_count += 1, credential active."""
    credential = _build_credential()
    note_summary = _make_note_summary("not_flaky")

    # Existing row with retry_count=2 → next attempt is 3
    conn = _FakeConn(fetchrow_returns={"id": uuid4(), "account_id": None,
                                         "status": "failed", "retry_count": 2,
                                         "granola_note_snapshot": None})
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(side_effect=GranolaError(
        GranolaErrorCode.GRANOLA_5XX, "upstream boom"
    ))

    outcome = await adapter.process_note(
        credential=credential,  # type: ignore[arg-type]
        note_summary=note_summary,
        client=client,
        pool=pool,  # type: ignore[arg-type]
        internal_domains=set(),
    )

    assert outcome is IngestionOutcome.FAILED
    upserts = [c for c in conn.execute_calls if "external_integration_runs" in c[0]]
    assert any("failed" in c[1] for c in upserts)
    # retry_count column (13th positional) = 3
    # Find the failed upsert and inspect param 12 (0-indexed)
    failed = [c for c in upserts if "failed" in c[1]]
    assert any(3 in c[1] for c in failed)


@pytest.mark.asyncio
async def test_per_note_retry_budget_exhausts_to_failed_permanent():
    """After _PER_NOTE_RETRY_LIMIT attempts, the note is marked permanent."""
    credential = _build_credential()
    note_summary = _make_note_summary("not_dead")

    # Existing row at the retry limit; next attempt pushes to FAILED_PERMANENT
    conn = _FakeConn(fetchrow_returns={"id": uuid4(), "account_id": None,
                                         "status": "failed",
                                         "retry_count": adapter._PER_NOTE_RETRY_LIMIT,
                                         "granola_note_snapshot": None})
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(side_effect=GranolaError(
        GranolaErrorCode.GRANOLA_5XX, "still boom"
    ))

    outcome = await adapter.process_note(
        credential=credential,  # type: ignore[arg-type]
        note_summary=note_summary,
        client=client,
        pool=pool,  # type: ignore[arg-type]
        internal_domains=set(),
    )

    assert outcome is IngestionOutcome.FAILED_PERMANENT
    upserts = [c for c in conn.execute_calls if "external_integration_runs" in c[0]]
    assert any("failed_permanent" in c[1] for c in upserts)


@pytest.mark.asyncio
async def test_idempotent_replay_success_short_circuits():
    """If a prior cycle recorded status='success' for this note, skip re-fetch."""
    credential = _build_credential()
    note_summary = _make_note_summary("not_already_done")

    conn = _FakeConn(fetchrow_returns={"id": uuid4(), "account_id": "11111111-aaaa-4111-8111-111111111111",
                                         "status": "success", "retry_count": 0,
                                         "granola_note_snapshot": None})
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock()

    outcome = await adapter.process_note(
        credential=credential,  # type: ignore[arg-type]
        note_summary=note_summary,
        client=client,
        pool=pool,  # type: ignore[arg-type]
        internal_domains=set(),
    )

    assert outcome is IngestionOutcome.SUCCESS
    # No detail fetch
    client.get_note_detail.assert_not_called()


# ---------------------------------------------------------------------------
# LOCKED-44 snapshot recoverability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reprocess_deferred_note_recovers_from_snapshot_when_404():
    """Scenario C recoverability: deferred note deleted on Granola side.

    Re-poll cycle: get_note_detail returns NOTE_NOT_FOUND; the adapter
    falls back to the granola_note_snapshot captured at defer time
    (LOCKED-44) and re-classifies. If the once-unknown domain is now
    known, the meeting completes.
    """
    credential = _build_credential()

    snapshot = {
        "title": "Deferred deal call",
        "summary_text": "Discussed pricing.",
        "web_url": "https://granola.ai/notes/not_deleted",
        "attendees": [{"email": "bidder@unknown-co.com", "name": "Bidder"}],
        "transcript_turns": [
            {"text": "First.", "speaker": {"source": "microphone"}}
        ],
        "calendar_event_id": "cal-evt-restored",
        "created_at": datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc).isoformat(),
        "captured_at": datetime(2026, 5, 23, 9, 5, tzinfo=timezone.utc).isoformat(),
    }
    deferred_row = {
        "id": uuid4(),
        "external_id": "not_deleted",
        "status": "deferred_pending_account",
        "granola_note_snapshot": json.dumps(snapshot),
        "retry_count": 0,
        "eq_interaction_id": None,
    }

    # Sequential fetchrow responses:
    #   1. _get_integration_run (idempotency check inside process_note) → None
    #   2. _INCREMENT_CREDENTIAL_FAILURES (not expected here, but defensive)
    # _get_deferred_runs uses fetch (not fetchrow), so it's separate.
    conn = _FakeConn(
        fetchrow_seq=[None],
        fetch_returns=[deferred_row],
    )
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(side_effect=GranolaError(
        GranolaErrorCode.GRANOLA_NOTE_NOT_FOUND, "gone"
    ))

    entered = MagicMock()
    entered.commit = AsyncMock()
    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=entered)
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    process_called: dict = {}

    async def _fake_process(**kwargs):
        process_called.update(kwargs)
        return text_clean_service.ProcessResult(
            interaction_id="00000000-0000-4000-8000-0000000000aa",
            lane1_published=True, lane2_dispatched=True,
        )

    # Now the domain unknown-co.com IS known.
    with patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value="55555555-aaaa-4111-8111-555555555555")), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "process", new=AsyncMock(side_effect=_fake_process)):
        count = await adapter.reprocess_deferred_notes(
            credential=credential,  # type: ignore[arg-type]
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    assert count == 1
    # The recovered detail flowed through scenario A — text_clean called.
    assert process_called.get("account_id") == "55555555-aaaa-4111-8111-555555555555"
    assert process_called.get("envelope") is not None
    # The envelope.content.text should include the snapshot's transcript text.
    envelope = process_called["envelope"]
    assert "First." in envelope.content.text


# ---------------------------------------------------------------------------
# Cycle-level orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_one_cycle_skips_inactive_credential():
    """status != 'active' → cycle is a no-op."""
    credential = _build_credential(status="revoked")
    conn = _FakeConn()
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock()
    client.aclose = AsyncMock()

    result = await adapter.run_one_cycle(
        credential=credential,  # type: ignore[arg-type]
        pool=pool,  # type: ignore[arg-type]
        api_client=client,
    )

    assert result.credential_skipped is True
    client.list_notes.assert_not_called()
    # No SQL writes for an inactive credential cycle
    assert conn.execute_calls == []


@pytest.mark.asyncio
async def test_run_one_cycle_marks_polled_success_with_cycle_start_timestamp():
    """Codex PR-X2 R1 P1 fix: last_polled_at = cycle-START timestamp, not
    cycle-end / CURRENT_TIMESTAMP.

    Notes created during the cycle window (between list_notes and the
    end-of-cycle UPDATE) MUST be reachable by the next cycle's
    ``created_after=last_polled_at`` filter. Cycle-end timestamping
    would skip them forever.
    """
    credential = _build_credential()
    conn = _FakeConn(fetchrow_returns=None, fetch_returns=[])  # no deferred rows
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(return_value=[])  # no new notes
    client.aclose = AsyncMock()

    before = datetime.now(timezone.utc)
    with patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())):
        result = await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )
    after = datetime.now(timezone.utc)

    assert result.notes_processed == 0
    assert result.credential_error_code is None
    success_updates = [
        c for c in conn.execute_calls
        if "last_polled_at = $4" in c[0]
        and "consecutive_failures = 0" in c[0]
    ]
    assert len(success_updates) == 1
    # The 4th positional arg (index 3) is the cycle_start_at timestamp.
    last_polled_at_arg = success_updates[0][1][3]
    assert isinstance(last_polled_at_arg, datetime)
    assert before <= last_polled_at_arg <= after, (
        "last_polled_at must be a cycle-START timestamp, not cycle-end "
        "(Codex PR-X2 R1 P1 fix)."
    )


# ---------------------------------------------------------------------------
# Codex PR-X2 R1 P1 fixes: failed-row retry + cycle-start watermark
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reprocess_pending_notes_retries_failed_rows():
    """Codex PR-X2 R1 P1 fix: 'failed' rows are retried by the end-of-cycle
    reprocess pass — previously only 'deferred_pending_account' rows were
    retried, leaving 'failed' rows stranded once last_polled_at advanced
    past their created_at."""
    credential = _build_credential()
    failed_row = {
        "id": uuid4(),
        "external_id": "not_was_5xx",
        "status": "failed",
        "granola_note_snapshot": None,
        "retry_count": 2,
        "eq_interaction_id": None,
    }
    conn = _FakeConn(fetchrow_seq=[None], fetch_returns=[failed_row])
    pool = _FakePool(conn)

    # Now Granola is healthy again and the note has a known business
    # attendee (domain was approved in the meantime). The retry should
    # promote it to success.
    note_detail = _make_note_detail(
        note_id="not_was_5xx",
        attendees=[Attendee(email="dan@nowknown.com")],
    )

    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(return_value=note_detail)

    entered = MagicMock()
    entered.commit = AsyncMock()
    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=entered)
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value="44444444-aaaa-4111-8111-444444444444")), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "process", new=AsyncMock(return_value=text_clean_service.ProcessResult(
             interaction_id="00000000-0000-4000-8000-0000000000bb",
             lane1_published=True, lane2_dispatched=True))):
        count = await adapter.reprocess_pending_notes(
            credential=credential,  # type: ignore[arg-type]
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    assert count == 1
    # The failed row was promoted to success (UPSERT with status='success').
    upserts = [c for c in conn.execute_calls if "external_integration_runs" in c[0]]
    assert any("success" in c[1] for c in upserts)


@pytest.mark.asyncio
async def test_reprocess_pending_notes_marks_failed_note_skipped_when_deleted():
    """A 'failed' row whose note has since been deleted on Granola
    transitions to 'skipped_no_business_attendees' (with reason
    'note_deleted_before_retry_succeeded') rather than re-failing
    forever."""
    credential = _build_credential()
    failed_row = {
        "id": uuid4(),
        "external_id": "not_gone",
        "status": "failed",
        "granola_note_snapshot": None,
        "retry_count": 3,
        "eq_interaction_id": None,
    }
    conn = _FakeConn(fetchrow_seq=[None], fetch_returns=[failed_row])
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(side_effect=GranolaError(
        GranolaErrorCode.GRANOLA_NOTE_NOT_FOUND, "gone"
    ))

    count = await adapter.reprocess_pending_notes(
        credential=credential,  # type: ignore[arg-type]
        client=client,
        pool=pool,  # type: ignore[arg-type]
        internal_domains=set(),
    )

    assert count == 1
    upserts = [c for c in conn.execute_calls if "external_integration_runs" in c[0]]
    # status='skipped_no_business_attendees', error_code='note_deleted_before_retry_succeeded'
    assert any("skipped_no_business_attendees" in c[1] for c in upserts)
    assert any("note_deleted_before_retry_succeeded" in c[1] for c in upserts)


# ---------------------------------------------------------------------------
# Codex PR-X2 R1 P2: in_progress idempotency anchor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_a_writes_in_progress_row_before_publish():
    """Pre-publish 'in_progress' UPSERT records the eq_interaction_id so a
    crash between Lane 1 publish and the success UPSERT preserves the
    idempotency anchor (Codex PR-X2 R1 P2)."""
    credential = _build_credential()
    note_summary = _make_note_summary("not_safe")
    note_detail = _make_note_detail(
        note_id="not_safe",
        attendees=[Attendee(email="alice@knownco.com")],
    )

    conn = _FakeConn(fetchrow_returns=None)
    pool = _FakePool(conn)
    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(return_value=note_detail)

    captured_envelopes: list = []

    async def _fake_process(**kwargs):
        captured_envelopes.append(kwargs["envelope"])
        return text_clean_service.ProcessResult(
            interaction_id=str(kwargs["envelope"].interaction_id),
            lane1_published=True, lane2_dispatched=True,
        )

    entered = MagicMock()
    entered.commit = AsyncMock()
    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=entered)
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value="11111111-aaaa-4111-8111-111111111111")), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "process", new=AsyncMock(side_effect=_fake_process)):
        outcome = await adapter.process_note(
            credential=credential,  # type: ignore[arg-type]
            note_summary=note_summary,
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    assert outcome is IngestionOutcome.SUCCESS
    assert len(captured_envelopes) == 1
    envelope_interaction_id = captured_envelopes[0].interaction_id

    upserts = [c for c in conn.execute_calls if "external_integration_runs" in c[0]]
    # 'in_progress' UPSERT happens BEFORE 'success' UPSERT, and both
    # carry the SAME eq_interaction_id (positional arg index 6).
    in_progress = [c for c in upserts if "in_progress" in c[1]]
    success = [c for c in upserts if "success" in c[1]]
    assert len(in_progress) >= 1
    assert len(success) >= 1
    in_progress_iid = in_progress[0][1][6]
    success_iid = success[0][1][6]
    assert in_progress_iid == envelope_interaction_id
    assert success_iid == envelope_interaction_id


@pytest.mark.asyncio
async def test_scenario_a_retry_reuses_existing_interaction_id():
    """On retry, the prior 'in_progress' (or 'failed') row's
    eq_interaction_id is reused as envelope.interaction_id so duplicate
    Lane 1 events deduplicate at downstream consumers."""
    credential = _build_credential()
    note_summary = _make_note_summary("not_retry")
    note_detail = _make_note_detail(
        note_id="not_retry",
        attendees=[Attendee(email="bob@knownco.com")],
    )

    prior_iid = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
    existing_row = {
        "id": uuid4(),
        "account_id": "11111111-aaaa-4111-8111-111111111111",
        "status": "failed",
        "retry_count": 1,
        "granola_note_snapshot": None,
        "eq_interaction_id": prior_iid,
    }
    conn = _FakeConn(fetchrow_returns=existing_row)
    pool = _FakePool(conn)
    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(return_value=note_detail)

    captured: list = []

    async def _fake_process(**kwargs):
        captured.append(kwargs["envelope"])
        return text_clean_service.ProcessResult(
            interaction_id=str(kwargs["envelope"].interaction_id),
            lane1_published=True, lane2_dispatched=True,
        )

    entered = MagicMock()
    entered.commit = AsyncMock()
    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=entered)
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value="11111111-aaaa-4111-8111-111111111111")), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "process", new=AsyncMock(side_effect=_fake_process)):
        await adapter.process_note(
            credential=credential,  # type: ignore[arg-type]
            note_summary=note_summary,
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    assert len(captured) == 1
    assert captured[0].interaction_id == prior_iid, (
        "Retry must re-use the prior eq_interaction_id so downstream "
        "consumers dedup the duplicate publish (Codex PR-X2 R1 P2)."
    )


@pytest.mark.asyncio
async def test_scenario_a_backpressure_failures_converge_to_failed_permanent():
    """Codex PR-X2 R2 P2 fix: repeated Scenario A failures (backpressure /
    Lane1 publish) MUST converge to FAILED_PERMANENT after
    _PER_NOTE_RETRY_LIMIT attempts.

    Pre-fix, _record_failed used default retry_count=1 regardless of the
    row's prior retry_count, so a row replayed through
    reprocess_pending_notes was stuck in FAILED forever under sustained
    outages.
    """
    credential = _build_credential()
    note_summary = _make_note_summary("not_stuck")
    note_detail = _make_note_detail(
        note_id="not_stuck",
        attendees=[Attendee(email="alice@knownco.com")],
    )

    # Existing row at the retry limit; next attempt pushes to permanent.
    existing_row = {
        "id": uuid4(),
        "account_id": "11111111-aaaa-4111-8111-111111111111",
        "status": "failed",
        "retry_count": adapter._PER_NOTE_RETRY_LIMIT,
        "granola_note_snapshot": None,
        "eq_interaction_id": None,
    }
    conn = _FakeConn(fetchrow_returns=existing_row)
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(return_value=note_detail)

    entered = MagicMock()
    entered.commit = AsyncMock()
    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=entered)
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value="11111111-aaaa-4111-8111-111111111111")), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "try_reserve_lane2_slot", return_value=False):

        outcome = await adapter.process_note(
            credential=credential,  # type: ignore[arg-type]
            note_summary=note_summary,
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    assert outcome is IngestionOutcome.FAILED_PERMANENT
    upserts = [c for c in conn.execute_calls if "external_integration_runs" in c[0]]
    assert any("failed_permanent" in c[1] for c in upserts)


@pytest.mark.asyncio
async def test_reprocess_pending_failed_row_classifies_to_scenario_c_defers():
    """Codex PR-X2 R2 P2 fix: a failed row that replays into Scenario C
    MUST call _defer_pending_account so the granola_note_snapshot is
    captured + pending-domain signals are queued (LOCKED-44
    recoverability)."""
    credential = _build_credential()
    failed_row = {
        "id": uuid4(),
        "external_id": "not_reclass_c",
        "status": "failed",
        "granola_note_snapshot": None,
        "retry_count": 2,
        "eq_interaction_id": None,
    }
    conn = _FakeConn(fetchrow_seq=[None], fetch_returns=[failed_row])
    pool = _FakePool(conn)

    # Note has unknown-business attendees only → Scenario C on this replay.
    note_detail = _make_note_detail(
        note_id="not_reclass_c",
        attendees=[Attendee(email="x@new-bidder.com", name="New Bidder")],
    )

    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(return_value=note_detail)

    entered = MagicMock()
    entered.commit = AsyncMock()
    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=entered)
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value=None)), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(adapter, "reopen_archived_entry", new=AsyncMock(return_value=None)), \
         patch.object(adapter, "upsert_queue_entry", new=AsyncMock(return_value="11111111-bbbb-4111-8111-111111111111")), \
         patch.object(adapter, "insert_signal", new=AsyncMock(return_value=None)):
        count = await adapter.reprocess_pending_notes(
            credential=credential,  # type: ignore[arg-type]
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    assert count == 1
    # Row transitioned from 'failed' → 'deferred_pending_account' with
    # a snapshot captured.
    upserts = [c for c in conn.execute_calls if "external_integration_runs" in c[0]]
    deferred = [c for c in upserts if "deferred_pending_account" in c[1]]
    assert len(deferred) >= 1, (
        "A failed row that replays into Scenario C must call "
        "_defer_pending_account, not stay 'failed' (Codex PR-X2 R2 P2)."
    )


@pytest.mark.asyncio
async def test_scenario_a_lane2_backpressure_records_failed_not_success():
    """When try_reserve_lane2_slot returns False, the note records as
    transient failure (retry next cycle), NOT as success."""
    credential = _build_credential()
    note_summary = _make_note_summary("not_busy")
    note_detail = _make_note_detail(
        note_id="not_busy",
        attendees=[Attendee(email="alice@knownco.com")],
    )

    conn = _FakeConn(fetchrow_returns=None)
    pool = _FakePool(conn)
    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(return_value=note_detail)

    entered = MagicMock()
    entered.commit = AsyncMock()
    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=entered)
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value="11111111-aaaa-4111-8111-111111111111")), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "try_reserve_lane2_slot", return_value=False), \
         patch.object(text_clean_service, "process", new=AsyncMock()) as proc:

        outcome = await adapter.process_note(
            credential=credential,  # type: ignore[arg-type]
            note_summary=note_summary,
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    assert outcome is IngestionOutcome.FAILED
    proc.assert_not_called()
    upserts = [c for c in conn.execute_calls if "external_integration_runs" in c[0]]
    # status='failed' + error_code='lane2_backpressure'
    assert any("lane2_backpressure" in c[1] for c in upserts)


# ---------------------------------------------------------------------------
# Edge #12 (plan §2.1 #12): adapter archived_at-awareness — a credential
# archived/disconnected mid-cycle must abort the in-flight cycle cleanly
# (stop publishing), and the 3 credential-state UPDATEs must no-op on an
# archived row so a stale cycle can't write back onto a just-disconnected
# credential.
# ---------------------------------------------------------------------------


def test_credential_state_update_sqls_guard_on_archived_at():
    """The 3 credential-state UPDATE SQLs must filter ``archived_at IS NULL``
    so an in-flight cycle's terminal write-back is a no-op once the credential
    is archived/disconnected (plan §2.1 #12)."""
    for name, sql in (
        ("_UPDATE_CREDENTIAL_POLL_SUCCESS_SQL", adapter._UPDATE_CREDENTIAL_POLL_SUCCESS_SQL),
        ("_UPDATE_CREDENTIAL_STATUS_SQL", adapter._UPDATE_CREDENTIAL_STATUS_SQL),
        ("_INCREMENT_CREDENTIAL_FAILURES_SQL", adapter._INCREMENT_CREDENTIAL_FAILURES_SQL),
    ):
        assert "archived_at IS NULL" in sql, (
            f"{name} must guard on archived_at IS NULL (edge #12)"
        )


@pytest.mark.asyncio
async def test_credential_is_active_queries_active_and_not_archived():
    """``_credential_is_active`` returns True only for an active, non-archived
    row, scoped to (id, tenant_id, user_id)."""
    credential = _build_credential()

    conn_active = _FakeConn(fetchval_returns=True)
    is_active = await adapter._credential_is_active(
        pool=_FakePool(conn_active),  # type: ignore[arg-type]
        credential_id=credential.id,
        tenant_id=credential.tenant_id,
        user_id=credential.user_id,
    )
    assert is_active is True
    sql, args = conn_active.fetchval_calls[0]
    assert "status = 'active'" in sql
    assert "archived_at IS NULL" in sql
    assert args == (credential.id, credential.tenant_id, credential.user_id)

    conn_archived = _FakeConn(fetchval_returns=False)
    is_active2 = await adapter._credential_is_active(
        pool=_FakePool(conn_archived),  # type: ignore[arg-type]
        credential_id=credential.id,
        tenant_id=credential.tenant_id,
        user_id=credential.user_id,
    )
    assert is_active2 is False


def _liveness_gate(state):
    """Patch target for ``adapter._credential_is_active``: returns the current
    value of ``state['active']`` on every call, regardless of how many internal
    recheck sites exist. Lets a test flip the credential inactive at a precise
    moment (on publish, on detail-fetch, etc.) and assert the cycle's response —
    robust to where the liveness gates are placed."""
    async def _check(**_kwargs):
        return state["active"]
    return _check


@pytest.mark.asyncio
async def test_run_one_cycle_aborts_remaining_notes_when_disconnected_after_a_note():
    """Edge #12 core: once the credential is disconnected mid-cycle, the cycle
    stops — later notes are NOT fetched or published. Here the disconnect lands
    right after note1 publishes; note2 must be skipped entirely."""
    credential = _build_credential()
    note1 = _make_note_summary("not_mid_1")
    note2 = _make_note_summary("not_mid_2")
    detail1 = _make_note_detail(note_id="not_mid_1", attendees=[Attendee(email="bob@bigco.com")])
    detail2 = _make_note_detail(note_id="not_mid_2", attendees=[Attendee(email="carol@bigco.com")])

    conn = _FakeConn(fetchrow_returns=None, fetch_returns=[])
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(return_value=[note1, note2])
    client.get_note_detail = AsyncMock(side_effect=[detail1, detail2])
    client.aclose = AsyncMock()

    # Active until note1's publish completes, then /disconnect lands.
    state = {"active": True}

    async def _publish_then_disconnect(**_kwargs):
        state["active"] = False
        return text_clean_service.ProcessResult(
            interaction_id="00000000-0000-4000-8000-00000000ab01",
            lane1_published=True, lane2_dispatched=True)

    process_mock = AsyncMock(side_effect=_publish_then_disconnect)

    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "_credential_is_active", new=_liveness_gate(state)), \
         patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())), \
         patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value="22222222-aaaa-4111-8111-222222222222")), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "process", new=process_mock):
        result = await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    assert process_mock.await_count == 1            # only note1 published
    assert result.notes_processed == 1
    assert client.get_note_detail.await_count == 1  # note2 never fetched


@pytest.mark.asyncio
async def test_run_one_cycle_does_not_publish_when_disconnected_during_a_note():
    """Edge #12 (Codex R1 [P1]): a /disconnect landing DURING a note — after the
    cycle starts the note but before the publish — must NOT emit downstream.
    Modeled by flipping the credential inactive when the note detail is fetched
    (mid-process_note), so the final pre-publish gate trips."""
    credential = _build_credential()
    note1 = _make_note_summary("not_dur_1")
    detail1 = _make_note_detail(note_id="not_dur_1", attendees=[Attendee(email="bob@bigco.com")])

    conn = _FakeConn(fetchrow_returns=None, fetch_returns=[])
    pool = _FakePool(conn)

    state = {"active": True}

    async def _fetch_then_disconnect(_note_id):
        state["active"] = False  # /disconnect lands during the detail fetch
        return detail1

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(return_value=[note1])
    client.get_note_detail = AsyncMock(side_effect=_fetch_then_disconnect)
    client.aclose = AsyncMock()

    process_mock = AsyncMock(return_value=text_clean_service.ProcessResult(
        interaction_id="00000000-0000-4000-8000-00000000ad01",
        lane1_published=True, lane2_dispatched=True))

    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "_credential_is_active", new=_liveness_gate(state)), \
         patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())), \
         patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value="33333333-aaaa-4111-8111-333333333333")), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "process", new=process_mock):
        result = await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    assert process_mock.await_count == 0           # publish never fired
    assert result.notes_processed == 0
    assert text_clean_service.get_lane2_in_flight() == 0  # reserved slot released


@pytest.mark.asyncio
async def test_run_one_cycle_skips_granola_api_when_disconnected_before_listing():
    """Edge #12 (Codex R2 [P2]): a credential disconnected before the cycle's
    first Granola call aborts BEFORE list_notes — no upstream API request with a
    just-disconnected key."""
    credential = _build_credential()
    conn = _FakeConn(fetchrow_returns=None, fetch_returns=[])
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(return_value=[])   # must NOT be called
    client.get_note_detail = AsyncMock()
    client.aclose = AsyncMock()

    process_mock = AsyncMock()

    with patch.object(adapter, "_credential_is_active", new=AsyncMock(return_value=False)), \
         patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())), \
         patch.object(text_clean_service, "process", new=process_mock):
        result = await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    assert result.credential_skipped is True
    client.list_notes.assert_not_called()
    process_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_one_cycle_skips_detail_fetch_when_disconnected_during_idempotency_lookup():
    """Edge #12 (Codex R9 [P2]): a /disconnect landing during process_note's
    idempotency lookup (_get_integration_run) — after the loop-top check but
    before get_note_detail — must NOT fire the per-note Granola detail API call."""
    credential = _build_credential()
    note = _make_note_summary("not_idem_1")

    conn = _FakeConn(fetchrow_returns=None, fetch_returns=[])
    pool = _FakePool(conn)

    state = {"active": True}

    async def _idempotency_then_disconnect(**_kwargs):
        state["active"] = False  # /disconnect lands during the idempotency lookup
        return None

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(return_value=[note])
    client.get_note_detail = AsyncMock()  # must NOT be called
    client.aclose = AsyncMock()

    process_mock = AsyncMock()
    with patch.object(adapter, "_credential_is_active", new=_liveness_gate(state)), \
         patch.object(adapter, "_get_integration_run", new=AsyncMock(side_effect=_idempotency_then_disconnect)), \
         patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())), \
         patch.object(text_clean_service, "process", new=process_mock):
        result = await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    assert result.notes_processed == 0
    client.get_note_detail.assert_not_called()
    process_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_one_cycle_scenario_c_writes_nothing_when_disconnected_mid_note():
    """Edge #12 (Codex R3 [P2]): a /disconnect during a note that classifies to
    Scenario C (unknown business domain) must NOT write the deferred row or
    queue pending-approval signals — every outcome branch is gated, not just the
    Scenario A publish."""
    credential = _build_credential()
    note = _make_note_summary("not_c_1")
    detail = _make_note_detail(note_id="not_c_1", attendees=[Attendee(email="zoe@unknown-biz.com")])

    conn = _FakeConn(fetchrow_returns=None, fetch_returns=[])
    pool = _FakePool(conn)

    state = {"active": True}

    async def _fetch_then_disconnect(_note_id):
        state["active"] = False  # /disconnect lands during the detail fetch
        return detail

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(return_value=[note])
    client.get_note_detail = AsyncMock(side_effect=_fetch_then_disconnect)
    client.aclose = AsyncMock()

    queue_mock = AsyncMock(return_value=[])  # must NOT be called
    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "_credential_is_active", new=_liveness_gate(state)), \
         patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())), \
         patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value=None)), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(adapter, "_queue_unknown_domain_signals", new=queue_mock):
        result = await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    assert result.notes_processed == 0
    queue_mock.assert_not_called()
    # No deferred row written for the disconnected credential.
    deferred_upserts = [
        c for c in conn.execute_calls
        if "external_integration_runs" in c[0] and "deferred_pending_account" in c[1]
    ]
    assert deferred_upserts == []


@pytest.mark.asyncio
async def test_run_one_cycle_scenario_c_writes_nothing_when_disconnected_during_classify():
    """Edge #12 (Codex R5 [P2]): a /disconnect during the account-lookup step of
    classification (AFTER a successful detail fetch) on a Scenario C note must
    still abort before _defer_pending_account writes. The post-classify gate
    covers the classify window, which the post-fetch gate alone does not."""
    credential = _build_credential()
    note = _make_note_summary("not_c2_1")
    detail = _make_note_detail(note_id="not_c2_1", attendees=[Attendee(email="zoe@unknown-biz.com")])

    conn = _FakeConn(fetchrow_returns=None, fetch_returns=[])
    pool = _FakePool(conn)

    state = {"active": True}

    async def _lookup_then_disconnect(*, session, tenant_id, domain):
        state["active"] = False  # /disconnect lands during the account lookup
        return None  # unknown domain → Scenario C

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(return_value=[note])
    client.get_note_detail = AsyncMock(return_value=detail)
    client.aclose = AsyncMock()

    queue_mock = AsyncMock(return_value=[])  # must NOT be called
    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "_credential_is_active", new=_liveness_gate(state)), \
         patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())), \
         patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(side_effect=_lookup_then_disconnect)), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(adapter, "_queue_unknown_domain_signals", new=queue_mock):
        result = await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    assert result.notes_processed == 0
    queue_mock.assert_not_called()
    deferred_upserts = [
        c for c in conn.execute_calls
        if "external_integration_runs" in c[0] and "deferred_pending_account" in c[1]
    ]
    assert deferred_upserts == []


@pytest.mark.asyncio
async def test_run_one_cycle_error_path_writes_nothing_when_disconnected_mid_fetch():
    """Edge #12 (Codex R4 [P2]): if get_note_detail RAISES while the credential
    is being disconnected, the error handler must NOT record a skipped/failed
    row for the archived credential — the single post-fetch gate covers the
    error path too, not just successful fetches."""
    credential = _build_credential()
    note = _make_note_summary("not_err_1")

    conn = _FakeConn(fetchrow_returns=None, fetch_returns=[])
    pool = _FakePool(conn)

    state = {"active": True}

    async def _raise_and_disconnect(_note_id):
        state["active"] = False  # /disconnect lands during the failing fetch
        raise GranolaError(GranolaErrorCode.GRANOLA_NOTE_NOT_FOUND, "gone")

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(return_value=[note])
    client.get_note_detail = AsyncMock(side_effect=_raise_and_disconnect)
    client.aclose = AsyncMock()

    with patch.object(adapter, "_credential_is_active", new=_liveness_gate(state)), \
         patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())):
        result = await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    assert result.notes_processed == 0
    # No skipped/failed row recorded for the disconnected credential.
    eir_writes = [c for c in conn.execute_calls if "external_integration_runs" in c[0]]
    assert eir_writes == []


@pytest.mark.asyncio
async def test_reprocess_failed_row_writes_nothing_when_disconnected_mid_fetch():
    """Edge #12 (Codex R4 [P2]): the reprocess error path is gated too — a
    /disconnect during a failed-row re-fetch must not record skipped/failed
    retry state for the archived credential."""
    credential = _build_credential()
    failed_row = {
        "id": uuid4(),
        "external_id": "not_failed_1",
        "status": "failed",
        "granola_note_snapshot": None,
        "retry_count": 1,
        "eq_interaction_id": None,
    }
    conn = _FakeConn(fetch_returns=[failed_row])
    pool = _FakePool(conn)

    state = {"active": True}

    async def _raise_and_disconnect(_external_id):
        state["active"] = False
        raise GranolaError(GranolaErrorCode.GRANOLA_NOTE_NOT_FOUND, "gone")

    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock(side_effect=_raise_and_disconnect)

    with patch.object(adapter, "_credential_is_active", new=_liveness_gate(state)):
        await adapter.reprocess_pending_notes(
            credential=credential,  # type: ignore[arg-type]
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    eir_writes = [c for c in conn.execute_calls if "external_integration_runs" in c[0]]
    assert eir_writes == []


@pytest.mark.asyncio
async def test_run_one_cycle_skips_success_bookkeeping_on_mid_cycle_deactivation():
    """Edge #12 (Codex R6 [P2] #1): when a cycle aborts on mid-cycle
    deactivation, it must NOT run the end-of-cycle success UPDATE — that UPDATE
    only guards archived_at IS NULL, so on a revoked/error-mid-cycle row
    (archived_at still NULL) it would wrongly clear last_error, reset
    consecutive_failures, and advance last_polled_at."""
    credential = _build_credential()
    note1 = _make_note_summary("not_rev_1")
    note2 = _make_note_summary("not_rev_2")
    detail1 = _make_note_detail(note_id="not_rev_1", attendees=[Attendee(email="bob@bigco.com")])
    detail2 = _make_note_detail(note_id="not_rev_2", attendees=[Attendee(email="carol@bigco.com")])

    conn = _FakeConn(fetchrow_returns=None, fetch_returns=[])
    pool = _FakePool(conn)

    state = {"active": True}

    async def _publish_then_deactivate(**_kwargs):
        state["active"] = False  # credential goes revoked/error after note1
        return text_clean_service.ProcessResult(
            interaction_id="00000000-0000-4000-8000-00000000ae01",
            lane1_published=True, lane2_dispatched=True)

    process_mock = AsyncMock(side_effect=_publish_then_deactivate)

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(return_value=[note1, note2])
    client.get_note_detail = AsyncMock(side_effect=[detail1, detail2])
    client.aclose = AsyncMock()

    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "_credential_is_active", new=_liveness_gate(state)), \
         patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())), \
         patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value="22222222-aaaa-4111-8111-222222222222")), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "process", new=process_mock):
        await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    # The end-of-cycle success UPDATE (clears last_error/consecutive_failures,
    # advances last_polled_at) must NOT have run after the deactivation abort.
    success_marks = [
        c for c in conn.execute_calls
        if "last_polled_at = $4" in c[0] and "consecutive_failures = 0" in c[0]
    ]
    assert success_marks == []


@pytest.mark.asyncio
async def test_run_one_cycle_skips_success_bookkeeping_when_deactivated_during_reprocess():
    """Edge #12 (Codex R7 [P2]): if the credential deactivates DURING the
    reprocess pass (not the main note loop), the cycle must still skip the
    success UPDATE. reprocess breaks internally without signalling abort, so
    run_one_cycle re-checks liveness right before the success bookkeeping."""
    credential = _build_credential()
    failed_row = {
        "id": uuid4(),
        "external_id": "not_rp_1",
        "status": "failed",
        "granola_note_snapshot": None,
        "retry_count": 1,
        "eq_interaction_id": None,
    }
    detail = _make_note_detail(note_id="not_rp_1", attendees=[Attendee(email="bob@bigco.com")])

    # No main-loop notes; one pending (failed) row to reprocess.
    conn = _FakeConn(fetchrow_returns=None, fetch_returns=[failed_row])
    pool = _FakePool(conn)

    state = {"active": True}

    async def _fetch_then_deactivate(_external_id):
        state["active"] = False  # deactivation lands during the reprocess re-fetch
        return detail

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(return_value=[])
    client.get_note_detail = AsyncMock(side_effect=_fetch_then_deactivate)
    client.aclose = AsyncMock()

    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "_credential_is_active", new=_liveness_gate(state)), \
         patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())), \
         patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value="22222222-aaaa-4111-8111-222222222222")), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "process", new=AsyncMock()):
        await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    success_marks = [
        c for c in conn.execute_calls
        if "last_polled_at = $4" in c[0] and "consecutive_failures = 0" in c[0]
    ]
    assert success_marks == []


@pytest.mark.asyncio
async def test_run_one_cycle_publishes_all_notes_when_credential_stays_active():
    """Regression guard for edge #12: credential active the whole cycle → every
    note is processed + published (no behavior change from the guards)."""
    credential = _build_credential()
    note1 = _make_note_summary("not_act_1")
    note2 = _make_note_summary("not_act_2")
    detail1 = _make_note_detail(note_id="not_act_1", attendees=[Attendee(email="bob@bigco.com")])
    detail2 = _make_note_detail(note_id="not_act_2", attendees=[Attendee(email="carol@bigco.com")])

    conn = _FakeConn(fetchrow_returns=None, fetch_returns=[])
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(return_value=[note1, note2])
    client.get_note_detail = AsyncMock(side_effect=[detail1, detail2])
    client.aclose = AsyncMock()

    process_mock = AsyncMock(return_value=text_clean_service.ProcessResult(
        interaction_id="00000000-0000-4000-8000-00000000ac01",
        lane1_published=True, lane2_dispatched=True))

    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    sess_cm.__aexit__ = AsyncMock(return_value=None)

    with patch.object(adapter, "_credential_is_active", new=AsyncMock(return_value=True)), \
         patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())), \
         patch.object(adapter, "lookup_account_by_domain", new=AsyncMock(return_value="22222222-aaaa-4111-8111-222222222222")), \
         patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=_FAKE_RESOLVED_CONTACTS)), \
         patch.object(text_clean_service, "process", new=process_mock):
        result = await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    assert process_mock.await_count == 2
    assert result.notes_processed == 2


@pytest.mark.asyncio
async def test_reprocess_pending_notes_aborts_when_credential_archived():
    """Edge #12: the end-of-cycle reprocess pass also re-checks active-state —
    an archived credential does not re-publish deferred/failed rows."""
    credential = _build_credential()
    pending_row = {
        "id": uuid4(),
        "external_id": "not_pending_1",
        "status": "failed",
        "granola_note_snapshot": None,
        "retry_count": 1,
        "eq_interaction_id": None,
    }
    conn = _FakeConn(fetch_returns=[pending_row], fetchval_returns=False)  # archived
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.get_note_detail = AsyncMock()  # must NOT be called

    process_mock = AsyncMock()
    with patch.object(text_clean_service, "process", new=process_mock):
        count = await adapter.reprocess_pending_notes(
            credential=credential,  # type: ignore[arg-type]
            client=client,
            pool=pool,  # type: ignore[arg-type]
            internal_domains=set(),
        )

    assert count == 0
    client.get_note_detail.assert_not_called()
    process_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Contact resolution + linking (phase-2.1/granola-contact-resolution)
# ---------------------------------------------------------------------------

_ACCT_PAL = "22222222-2222-4222-8222-222222222222"
_ACCT_SNO = "33333333-3333-4333-8333-333333333333"


def _known_att(email: str, name: Optional[str], account_id: Optional[str]) -> AttendeeClassification:
    return AttendeeClassification(
        email=email,
        name=name,
        domain=email.split("@")[1],
        klass=DomainClass.BUSINESS,
        account_id=account_id,
    )


def _decision_a(
    known: list[AttendeeClassification],
    unknown: Optional[list[AttendeeClassification]] = None,
) -> PathTwoDecision:
    return PathTwoDecision(
        scenario=Scenario.A_KNOWN_ANCHOR,
        anchor_account_id=known[0].account_id,
        known_account_attendees=known,
        unknown_business_attendees=unknown or [],
    )


def _resolved(contact_id: str, email: str, name: Optional[str] = "X",
              account_id: str = _ACCT_PAL, matched: bool = True) -> ResolvedContactRow:
    return ResolvedContactRow(
        contact_id=contact_id, email=email, name=name,
        account_id=account_id, account_matched=matched,
    )


@pytest.mark.asyncio
async def test_resolve_known_account_contacts_dedupes_and_binds_per_attendee_account():
    """One contact per unique email, each bound to its OWN account (not anchor)."""
    cred: Any = _build_credential()  # _FakeCredential duck-types GranolaCredential
    known = [
        _known_att("matt@palantir.com", "Matt Scanlan", _ACCT_PAL),
        _known_att("matt@palantir.com", "Matt Scanlan", _ACCT_PAL),  # duplicate email
        _known_att("amy@snowflake.com", "Amy R", _ACCT_SNO),
    ]
    decision = _decision_a(known)

    session = MagicMock()
    session.commit = AsyncMock()
    sess_cm = MagicMock()
    sess_cm.__aenter__ = AsyncMock(return_value=session)
    sess_cm.__aexit__ = AsyncMock(return_value=False)

    calls: list[tuple[str, str]] = []

    async def fake_focc(*, session, tenant_id, email, account_id, display_name):
        calls.append((email, account_id))
        return _resolved(f"cid-{email}", email, display_name, account_id)

    with patch.object(adapter, "get_async_session", return_value=sess_cm), \
         patch.object(adapter, "find_or_create_contact", side_effect=fake_focc):
        resolved = await adapter._resolve_known_account_contacts(decision=decision, credential=cred)

    assert [r.email for r in resolved] == ["matt@palantir.com", "amy@snowflake.com"]  # deduped, ordered
    assert calls == [("matt@palantir.com", _ACCT_PAL), ("amy@snowflake.com", _ACCT_SNO)]  # per-attendee acct
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_known_account_contacts_empty_when_no_known():
    """No known attendees → no session opened, no find-or-create, empty list."""
    cred: Any = _build_credential()  # _FakeCredential duck-types GranolaCredential
    decision = PathTwoDecision(
        scenario=Scenario.C_DEFER_PENDING_ACCOUNT,
        anchor_account_id=None,
        known_account_attendees=[],
        unknown_business_attendees=[_known_att("x@unknown.com", "X", None)],
    )
    with patch.object(adapter, "get_async_session") as gs, \
         patch.object(adapter, "find_or_create_contact") as focc:
        resolved = await adapter._resolve_known_account_contacts(decision=decision, credential=cred)
    assert resolved == []
    gs.assert_not_called()
    focc.assert_not_called()


def test_build_envelope_includes_contact_ids_and_contacts_when_resolved():
    cred: Any = _build_credential()  # _FakeCredential duck-types GranolaCredential
    detail = _make_note_detail(attendees=[Attendee(email="matt@palantir.com", name="Matt Scanlan")])
    decision = _decision_a([_known_att("matt@palantir.com", "Matt Scanlan", _ACCT_PAL)])
    resolved = [_resolved("cid-1", "matt@palantir.com", "Matt Scanlan", _ACCT_PAL)]

    env = adapter._build_envelope(
        credential=cred, detail=detail, anchor_account_id=_ACCT_PAL,
        decision=decision, interaction_id=uuid4(), resolved_contacts=resolved,
    )
    assert env.extras["contact_ids"] == ["cid-1"]
    assert env.extras["contacts"] == [
        {"contact_id": "cid-1", "email": "matt@palantir.com", "name": "Matt Scanlan", "role": "attendee"}
    ]
    # the six granola_* keys are still present (no regression)
    for k in ("granola_note_id", "granola_web_url", "granola_folder_name",
              "granola_summary_text", "granola_calendar_event_id", "granola_attendees_raw"):
        assert k in env.extras


def test_build_envelope_omits_contact_keys_when_none_resolved():
    cred: Any = _build_credential()  # _FakeCredential duck-types GranolaCredential
    detail = _make_note_detail(attendees=[Attendee(email="m@palantir.com", name="M")])
    decision = _decision_a([_known_att("m@palantir.com", "M", _ACCT_PAL)])
    env = adapter._build_envelope(
        credential=cred, detail=detail, anchor_account_id=_ACCT_PAL,
        decision=decision, interaction_id=uuid4(), resolved_contacts=[],
    )
    assert "contact_ids" not in env.extras
    assert "contacts" not in env.extras


@pytest.mark.asyncio
async def test_scenario_a_feeds_contact_ids_to_lane2_and_envelope():
    """The fix: contact_ids reach BOTH Lane2Extras (Postgres FK) and envelope.extras (Neo4j)."""
    cred: Any = _build_credential()  # _FakeCredential duck-types GranolaCredential
    note = _make_note_summary("not_ca")
    detail = _make_note_detail(note_id="not_ca",
                               attendees=[Attendee(email="matt@palantir.com", name="Matt Scanlan")])
    decision = _decision_a([_known_att("matt@palantir.com", "Matt Scanlan", _ACCT_PAL)])
    resolved = [_resolved("c-1", "matt@palantir.com", "Matt Scanlan", _ACCT_PAL)]
    captured: dict = {}

    async def fake_process(*, tenant_id, user_id, account_id, envelope, lane2_extras):
        captured["lane2_extras"] = lane2_extras
        captured["envelope_extras"] = envelope.extras

    with patch.object(text_clean_service, "try_reserve_lane2_slot", return_value=True), \
         patch.object(text_clean_service, "process", new=AsyncMock(side_effect=fake_process)), \
         patch.object(text_clean_service, "release_lane2_slot"), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=resolved)), \
         patch.object(adapter, "_credential_is_active", new=AsyncMock(return_value=True)), \
         patch.object(adapter, "_record_in_progress", new=AsyncMock()), \
         patch.object(adapter, "_record_success", new=AsyncMock()), \
         patch.object(adapter, "_queue_unknown_domain_signals", new=AsyncMock(return_value=[])):
        out = await adapter._ingest_scenario_a(
            credential=cred, note_summary=note, detail=detail, decision=decision, pool=MagicMock(),
        )

    assert out is IngestionOutcome.SUCCESS
    assert captured["lane2_extras"] is not None
    assert captured["lane2_extras"].contact_ids == ["c-1"]
    assert captured["lane2_extras"].calendar_event_id is None
    assert captured["envelope_extras"]["contact_ids"] == ["c-1"]  # both channels, single source


@pytest.mark.asyncio
async def test_scenario_a_zero_resolved_contacts_retries_without_publishing():
    """Invariant violation (0 contacts in Scenario A) → transient failure, no publish."""
    cred: Any = _build_credential()  # _FakeCredential duck-types GranolaCredential
    note = _make_note_summary("not_zero")
    detail = _make_note_detail(note_id="not_zero", attendees=[Attendee(email="m@palantir.com", name="M")])
    decision = _decision_a([_known_att("m@palantir.com", "M", _ACCT_PAL)])
    process_mock = AsyncMock()

    with patch.object(text_clean_service, "try_reserve_lane2_slot", return_value=True), \
         patch.object(text_clean_service, "process", new=process_mock), \
         patch.object(text_clean_service, "release_lane2_slot"), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=[])), \
         patch.object(adapter, "_credential_is_active", new=AsyncMock(return_value=True)), \
         patch.object(adapter, "_record_in_progress", new=AsyncMock()), \
         patch.object(adapter, "_record_scenario_a_failure",
                      new=AsyncMock(return_value=IngestionOutcome.FAILED)) as fail:
        out = await adapter._ingest_scenario_a(
            credential=cred, note_summary=note, detail=detail, decision=decision, pool=MagicMock(),
        )

    process_mock.assert_not_called()  # never published a half-broken meeting
    fail.assert_awaited_once()
    assert fail.call_args.kwargs["error_code"] == "contact_resolution_empty"
    assert out is IngestionOutcome.FAILED


@pytest.mark.asyncio
async def test_scenario_a_gate2_aborts_if_disconnected_during_resolution():
    """Gate #2: a disconnect during contact resolution aborts before publish."""
    cred: Any = _build_credential()  # _FakeCredential duck-types GranolaCredential
    note = _make_note_summary("not_g2")
    detail = _make_note_detail(note_id="not_g2", attendees=[Attendee(email="m@palantir.com", name="M")])
    decision = _decision_a([_known_att("m@palantir.com", "M", _ACCT_PAL)])
    resolved = [_resolved("c-1", "m@palantir.com", "M", _ACCT_PAL)]
    process_mock = AsyncMock()

    with patch.object(text_clean_service, "try_reserve_lane2_slot", return_value=True), \
         patch.object(text_clean_service, "process", new=process_mock), \
         patch.object(text_clean_service, "release_lane2_slot"), \
         patch.object(adapter, "_resolve_known_account_contacts", new=AsyncMock(return_value=resolved)), \
         patch.object(adapter, "_record_in_progress", new=AsyncMock()), \
         patch.object(adapter, "_credential_is_active",
                      new=AsyncMock(side_effect=[True, False])):  # gate1 ok, gate2 disconnected
        with pytest.raises(adapter._CredentialDeactivated):
            await adapter._ingest_scenario_a(
                credential=cred, note_summary=note, detail=detail, decision=decision, pool=MagicMock(),
            )
    process_mock.assert_not_called()  # gate #2 stopped the publish


# ---------------------------------------------------------------------------
# B1 (EQ-91) — adapter reads the folder-LIST config (folders[0]) with the
# legacy singular folder_id/folder_name as a one-release fallback.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_one_cycle_polls_folder_id_from_folders_list_first():
    """When config carries the NEW folders[] list (no legacy folder_id), the
    poll reads folders[0].id — not "" — so B1 stays correct after the legacy
    singular mirror is eventually dropped. (Full multi-folder loop is B2.)"""
    credential = _build_credential()
    credential.config = {
        "mode": "folders",
        "import_scope": "history",
        "folders": [{"id": "fol_a", "name": "A"}, {"id": "fol_b", "name": "B"}],
    }
    conn = _FakeConn(fetchrow_returns=None, fetch_returns=[])
    pool = _FakePool(conn)

    client = MagicMock(spec=GranolaAPIClient)
    client.list_notes = AsyncMock(return_value=[])
    client.aclose = AsyncMock()

    with patch.object(adapter, "get_tenant_internal_domains", new=AsyncMock(return_value=set())):
        await adapter.run_one_cycle(
            credential=credential,  # type: ignore[arg-type]
            pool=pool,  # type: ignore[arg-type]
            api_client=client,
        )

    client.list_notes.assert_awaited_once()
    assert client.list_notes.await_args.kwargs["folder_id"] == "fol_a"


def test_build_envelope_folder_name_from_folders_list_first():
    """granola_folder_name derives from folders[0].name when config carries the
    folders[] list (no legacy folder_name), preserving the single-string
    downstream contract (LOCKED-36). Membership-aware derivation is B2/C16."""
    credential = _build_credential()
    credential.config = {
        "mode": "folders",
        "import_scope": "history",
        "folders": [{"id": "fol_a", "name": "Sales"}],
    }
    detail = _make_note_detail(
        attendees=[Attendee(email="alice@bigco.com", name="Alice Adams")],
    )
    decision = SimpleNamespace(
        scenario=Scenario.A_KNOWN_ANCHOR,
        anchor_account_id="11111111-aaaa-4111-8111-111111111111",
        known_account_attendees=[
            AttendeeClassification(
                email="alice@bigco.com",
                name="Alice Adams",
                domain="bigco.com",
                klass=DomainClass.BUSINESS,
                account_id="11111111-aaaa-4111-8111-111111111111",
            )
        ],
        unknown_business_attendees=[],
        personal_attendees=[],
        internal_attendees=[],
    )

    envelope = adapter._build_envelope(
        credential=credential,  # type: ignore[arg-type]
        detail=detail,
        anchor_account_id="11111111-aaaa-4111-8111-111111111111",
        decision=decision,  # type: ignore[arg-type]
    )

    assert envelope.extras["granola_folder_name"] == "Sales"
