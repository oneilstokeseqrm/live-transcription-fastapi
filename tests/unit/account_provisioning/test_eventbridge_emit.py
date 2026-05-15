"""Unit tests for services.account_provisioning.eventbridge_emit.

No DB or boto3 dependency at runtime — boto3 is patched at the factory
boundary; the closed lookup and envelope builder are pure functions.

Covers plan §3.3 (closed lookup, fail-loud), §6.6 (extras.contacts),
§3.4 (downstream Pydantic model compatibility).
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from unittest.mock import MagicMock

import pytest

from services.account_provisioning.eventbridge_emit import (
    INTERACTION_TYPE_TO_DETAIL_TYPE,
    build_envelope,
    emit_envelopes_for_materialization,
    resolve_detail_type,
)
from services.account_provisioning.types import (
    EmittedContact,
    EventBridgeEmissionError,
    InteractionForEmit,
    MaterializationResult,
    UnmappedInteractionTypeError,
)


# ---------------------------------------------------------------------------
# Closed lookup
# ---------------------------------------------------------------------------


class TestResolveDetailType:
    @pytest.mark.parametrize("interaction_type,expected", [
        ("transcript", "EnvelopeV1.transcript"),
        ("meeting", "EnvelopeV1.meeting"),
        ("note", "EnvelopeV1.note"),
        ("email", "EnvelopeV1.email"),
    ])
    def test_known_types_map_correctly(self, interaction_type: str, expected: str):
        assert resolve_detail_type(interaction_type) == expected

    def test_unknown_type_raises_unmapped_error(self):
        with pytest.raises(UnmappedInteractionTypeError) as exc:
            resolve_detail_type("text")  # not in the closed table
        # The error message names the unknown type for operator triage.
        assert "text" in str(exc.value)

    def test_empty_string_raises(self):
        with pytest.raises(UnmappedInteractionTypeError):
            resolve_detail_type("")

    def test_lookup_contents_match_live_eventbridge_rules(self):
        """Closed lookup matches the live rule filter patterns.

        Probed 2026-05-15: both ``action-item-graph-rule`` and
        ``eq-structured-graph-rule`` filter on EnvelopeV1.transcript /
        meeting / note / email. Any addition here MUST be coordinated
        with an upstream rule pattern update.
        """
        assert set(INTERACTION_TYPE_TO_DETAIL_TYPE.values()) == {
            "EnvelopeV1.transcript",
            "EnvelopeV1.meeting",
            "EnvelopeV1.note",
            "EnvelopeV1.email",
        }


# ---------------------------------------------------------------------------
# Envelope construction
# ---------------------------------------------------------------------------


def _make_interaction(
    *,
    interaction_type: str = "transcript",
    raw_text: str = "hello world",
    user_id: str | None = None,
    contacts: list[EmittedContact] | None = None,
) -> InteractionForEmit:
    return InteractionForEmit(
        interaction_id=str(uuid.uuid4()),
        interaction_type=interaction_type,
        raw_text=raw_text,
        user_id=user_id,
        created_at=dt.datetime(2026, 5, 15, 12, 0, 0, tzinfo=dt.timezone.utc),
        contacts=contacts or [],
    )


class TestBuildEnvelope:
    def test_extras_includes_contacts_metadata_array(self):
        """Per tasks/downstream/*-graph*.md: extras.contacts is locked.

        Each entry has {contact_id, email, name, role}. Downstream
        consumers read this for LLM prompts (action-item-graph) and
        Contact-node properties (eq-structured-graph-core).
        """
        tenant_id = str(uuid.uuid4())
        account_id = str(uuid.uuid4())
        queue_id = str(uuid.uuid4())
        contacts = [
            EmittedContact(
                contact_id=str(uuid.uuid4()),
                email="jane@acme.com",
                name="Jane Smith",
                role="organizer",
            ),
            EmittedContact(
                contact_id=str(uuid.uuid4()),
                email="bob@acme.com",
                name=None,  # name absent — downstream tolerates
                role="attendee",
            ),
        ]
        interaction = _make_interaction(contacts=contacts)

        envelope = build_envelope(
            interaction=interaction,
            tenant_id=tenant_id,
            account_id=account_id,
            queue_id=queue_id,
        )

        assert "contacts" in envelope.extras
        assert envelope.extras["contacts"] == [
            {
                "contact_id": contacts[0].contact_id,
                "email": "jane@acme.com",
                "name": "Jane Smith",
                "role": "organizer",
            },
            {
                "contact_id": contacts[1].contact_id,
                "email": "bob@acme.com",
                "name": None,
                "role": "attendee",
            },
        ]

    def test_extras_includes_contact_ids_for_legacy_consumers(self):
        contacts = [
            EmittedContact(contact_id=str(uuid.uuid4()), email="a@x.com"),
            EmittedContact(contact_id=str(uuid.uuid4()), email="b@x.com"),
        ]
        envelope = build_envelope(
            interaction=_make_interaction(contacts=contacts),
            tenant_id=str(uuid.uuid4()),
            account_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
        )
        assert envelope.extras["contact_ids"] == [c.contact_id for c in contacts]

    def test_extras_includes_queue_id_audit_breadcrumb(self):
        queue_id = str(uuid.uuid4())
        envelope = build_envelope(
            interaction=_make_interaction(),
            tenant_id=str(uuid.uuid4()),
            account_id=str(uuid.uuid4()),
            queue_id=queue_id,
        )
        assert envelope.extras["account_provisioning_queue_id"] == queue_id

    def test_account_id_populated(self):
        """Phase 1 invariant 1: backfilled interactions carry the resolved account_id."""
        account_id = str(uuid.uuid4())
        envelope = build_envelope(
            interaction=_make_interaction(),
            tenant_id=str(uuid.uuid4()),
            account_id=account_id,
            queue_id=str(uuid.uuid4()),
        )
        assert envelope.account_id == account_id

    def test_source_is_api(self):
        """Path A backfill: source='api' is in action-item-graph SourceType enum."""
        envelope = build_envelope(
            interaction=_make_interaction(),
            tenant_id=str(uuid.uuid4()),
            account_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
        )
        assert envelope.source == "api"

    def test_user_id_falls_back_to_tenant_when_unknown(self):
        """Backfill rows often lack a user_id; tenant_id is the workflow's identity."""
        tenant_id = str(uuid.uuid4())
        envelope = build_envelope(
            interaction=_make_interaction(user_id=None),
            tenant_id=tenant_id,
            account_id=str(uuid.uuid4()),
            queue_id=str(uuid.uuid4()),
        )
        assert envelope.user_id == tenant_id


# ---------------------------------------------------------------------------
# emit_envelopes_for_materialization fan-out + fail-loud
# ---------------------------------------------------------------------------


def _mock_eventbridge_client(*, fail_first: bool = False):
    """Return a (client, factory) pair simulating boto3.client('events')."""
    client = MagicMock()
    if fail_first:
        client.put_events = MagicMock(return_value={
            "FailedEntryCount": 1,
            "Entries": [{"ErrorCode": "InternalFailure", "ErrorMessage": "boom"}],
        })
    else:
        client.put_events = MagicMock(return_value={
            "FailedEntryCount": 0,
            "Entries": [{"EventId": "evt-1"}],
        })
    factory = MagicMock(return_value=client)
    return client, factory


@pytest.mark.asyncio
async def test_emit_one_envelope_per_interaction():
    tenant_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    queue_id = str(uuid.uuid4())
    iids = [str(uuid.uuid4()) for _ in range(3)]
    interactions = [
        _make_interaction(interaction_type="meeting") for _ in range(3)
    ]
    for itr, iid in zip(interactions, iids):
        itr.interaction_id = iid

    materialization = MaterializationResult(
        queue_id=queue_id,
        tenant_id=tenant_id,
        account_id=account_id,
        contact_ids=[],
        interaction_ids=iids,
    )

    client, factory = _mock_eventbridge_client()
    emissions = await emit_envelopes_for_materialization(
        materialization=materialization,
        interactions=interactions,
        boto3_factory=factory,
    )

    assert len(emissions) == 3
    assert client.put_events.call_count == 3
    for emission in emissions:
        assert emission.detail_type == "EnvelopeV1.meeting"


@pytest.mark.asyncio
async def test_emit_passes_source_and_detail_type():
    """Locked at design time: Source matches BOTH live rules, DetailType is in their filters."""
    iid = str(uuid.uuid4())
    interaction = _make_interaction(interaction_type="transcript")
    interaction.interaction_id = iid

    materialization = MaterializationResult(
        queue_id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        account_id=str(uuid.uuid4()),
        contact_ids=[],
        interaction_ids=[iid],
    )

    client, factory = _mock_eventbridge_client()
    await emit_envelopes_for_materialization(
        materialization=materialization,
        interactions=[interaction],
        boto3_factory=factory,
    )

    entries = client.put_events.call_args.kwargs["Entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["Source"] == "com.yourapp.transcription"
    assert entry["DetailType"] == "EnvelopeV1.transcript"
    detail = json.loads(entry["Detail"])
    assert detail["account_id"] == materialization.account_id
    assert detail["source"] == "api"


@pytest.mark.asyncio
async def test_emit_fails_loud_on_unmapped_interaction_type():
    """An interaction with an out-of-table type aborts emission immediately."""
    iid = str(uuid.uuid4())
    interaction = _make_interaction(interaction_type="document")  # not in lookup
    interaction.interaction_id = iid

    materialization = MaterializationResult(
        queue_id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        account_id=str(uuid.uuid4()),
        contact_ids=[],
        interaction_ids=[iid],
    )

    _, factory = _mock_eventbridge_client()
    with pytest.raises(UnmappedInteractionTypeError):
        await emit_envelopes_for_materialization(
            materialization=materialization,
            interactions=[interaction],
            boto3_factory=factory,
        )


@pytest.mark.asyncio
async def test_emit_raises_on_failed_entry_count():
    """FailedEntryCount > 0 surfaces as EventBridgeEmissionError → DBOS step retry."""
    iid = str(uuid.uuid4())
    interaction = _make_interaction(interaction_type="email")
    interaction.interaction_id = iid

    materialization = MaterializationResult(
        queue_id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        account_id=str(uuid.uuid4()),
        contact_ids=[],
        interaction_ids=[iid],
    )

    _, factory = _mock_eventbridge_client(fail_first=True)
    with pytest.raises(EventBridgeEmissionError):
        await emit_envelopes_for_materialization(
            materialization=materialization,
            interactions=[interaction],
            boto3_factory=factory,
        )


@pytest.mark.asyncio
async def test_fetch_interactions_constrains_signal_role_to_interaction(
    session, test_tenant_id, test_user_id,
):
    """Codex P2 finding 2026-05-15: the signal-role JOIN must constrain
    on interaction_id, not just (queue_id, email).

    Setup: one contact email (jane@x.com) appears in two signals for
    DIFFERENT raw_interactions (with different roles) under the same
    queue. Pre-fix, fetching contacts for interaction A returned
    BOTH signals' roles (Cartesian fan-out), duplicating the contact
    in extras.contacts and potentially attaching the role from
    interaction B's signal. Post-fix, only the role from interaction
    A's signal returns.
    """
    from sqlalchemy import text
    import uuid
    from services.account_provisioning.eventbridge_emit import (
        fetch_interactions_for_emit,
    )
    from services.account_provisioning.types import MaterializationResult

    # Seed two raw_interactions, one queue with two signals (same email,
    # different interactions + roles), two interaction_summaries, two
    # interaction_contact_links (linking the same contact to both).
    iid_a = str(uuid.uuid4())
    iid_b = str(uuid.uuid4())
    queue_id = str(uuid.uuid4())

    async with session.begin():
        # Bootstrap an account so raw_interactions.account_id FK holds.
        account_id = str(uuid.uuid4())
        await session.execute(
            text("""
                INSERT INTO accounts (id, tenant_id, name, state, account_type, created_at, updated_at)
                VALUES (CAST(:id AS uuid), CAST(:t AS uuid), :name, 'active', 'Prospect', NOW(), NOW())
            """),
            {"id": account_id, "t": test_tenant_id, "name": "JoinScopeTest"},
        )
        await session.execute(
            text("""
                INSERT INTO account_domains (id, tenant_id, account_id, domain, created_at)
                VALUES (gen_random_uuid(), CAST(:t AS uuid), CAST(:a AS uuid), :d, NOW())
            """),
            {"t": test_tenant_id, "a": account_id, "d": "joinscope.example.com"},
        )
        # Create a contact.
        contact_id = str(uuid.uuid4())
        await session.execute(
            text("""
                INSERT INTO contacts (id, tenant_id, email, account_id, source, validation_status, created_at, updated_at)
                VALUES (CAST(:id AS uuid), CAST(:t AS uuid), :email, CAST(:a AS uuid), 'test', 'verified', NOW(), NOW())
            """),
            {"id": contact_id, "t": test_tenant_id, "email": "jane@joinscope.example.com", "a": account_id},
        )
        # Two raw_interactions.
        for iid in (iid_a, iid_b):
            await session.execute(
                text("""
                    INSERT INTO raw_interactions (interaction_id, tenant_id, account_id, interaction_type, created_at, updated_at)
                    VALUES (CAST(:id AS uuid), CAST(:t AS uuid), CAST(:a AS uuid), 'meeting', NOW(), NOW())
                """),
                {"id": iid, "t": test_tenant_id, "a": account_id},
            )
        # Two interaction_summaries + two links (same contact, two interactions).
        sid_a = str(uuid.uuid4())
        sid_b = str(uuid.uuid4())
        for iid, sid in ((iid_a, sid_a), (iid_b, sid_b)):
            await session.execute(
                text("""
                    INSERT INTO interaction_summaries (summary_id, tenant_id, interaction_id, summary_type, created_at, updated_at)
                    VALUES (CAST(:sid AS uuid), CAST(:t AS uuid), CAST(:iid AS uuid), 'meeting', NOW(), NOW())
                """),
                {"sid": sid, "t": test_tenant_id, "iid": iid},
            )
            await session.execute(
                text("""
                    INSERT INTO interaction_contact_links (link_id, interaction_id, contact_id)
                    VALUES (gen_random_uuid(), CAST(:sid AS uuid), CAST(:cid AS uuid))
                """),
                {"sid": sid, "cid": contact_id},
            )
        # Queue + 2 signals: same email, DIFFERENT interactions, DIFFERENT roles.
        await session.execute(
            text("""
                INSERT INTO pending_account_mappings (
                    id, tenant_id, domain, status, owner_user_id,
                    discovered_from_type, expires_at, email_count, created_at, updated_at
                ) VALUES (
                    CAST(:q AS uuid), CAST(:t AS uuid), :d, 'mapped', CAST(:u AS uuid),
                    'test', NOW() + INTERVAL '7 days', 2, NOW(), NOW()
                )
            """),
            {"q": queue_id, "t": test_tenant_id, "d": "joinscope.example.com", "u": test_user_id},
        )
        for iid, role in ((iid_a, "organizer"), (iid_b, "attendee")):
            await session.execute(
                text("""
                    INSERT INTO pending_account_mapping_signals (
                        id, queue_id, tenant_id, source_type, source_user_id,
                        contact_email, contact_role, interaction_id, created_at
                    ) VALUES (
                        gen_random_uuid(), CAST(:q AS uuid), CAST(:t AS uuid), 'transcript', CAST(:u AS uuid),
                        :email, :role, CAST(:iid AS uuid), NOW()
                    )
                """),
                {
                    "q": queue_id, "t": test_tenant_id, "u": test_user_id,
                    "email": "jane@joinscope.example.com", "role": role, "iid": iid,
                },
            )

    materialization = MaterializationResult(
        queue_id=queue_id,
        tenant_id=test_tenant_id,
        account_id=account_id,
        contact_ids=[contact_id],
        interaction_ids=[iid_a, iid_b],
    )
    fetched = await fetch_interactions_for_emit(materialization=materialization)

    by_iid = {i.interaction_id: i for i in fetched}
    # Each interaction must have exactly ONE contact (the same contact),
    # with the role for THAT interaction's signal — not a Cartesian
    # cross-product of both signals.
    assert len(by_iid[iid_a].contacts) == 1, (
        f"interaction A had {len(by_iid[iid_a].contacts)} contacts; expected 1"
    )
    assert by_iid[iid_a].contacts[0].role == "organizer"

    assert len(by_iid[iid_b].contacts) == 1, (
        f"interaction B had {len(by_iid[iid_b].contacts)} contacts; expected 1"
    )
    assert by_iid[iid_b].contacts[0].role == "attendee"


@pytest.mark.asyncio
async def test_emit_empty_interactions_returns_empty_list():
    materialization = MaterializationResult(
        queue_id=str(uuid.uuid4()),
        tenant_id=str(uuid.uuid4()),
        account_id=str(uuid.uuid4()),
        contact_ids=[],
        interaction_ids=[],
    )
    client, factory = _mock_eventbridge_client()
    emissions = await emit_envelopes_for_materialization(
        materialization=materialization,
        interactions=[],
        boto3_factory=factory,
    )
    assert emissions == []
    client.put_events.assert_not_called()
