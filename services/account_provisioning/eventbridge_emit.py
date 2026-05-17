"""EventBridge emission for the account-provisioning workflow (Phase 1.5 M3).

Per-interaction fan-out of ``EnvelopeV1.*`` events for every interaction
materialized by Step 5. Path A from plan §3.3: reuse the existing
``com.yourapp.transcription`` source so the live ``action-item-graph-rule``
and ``eq-structured-graph-rule`` rules forward to both consumer SQS
queues without any new rule wiring.

The ``INTERACTION_TYPE_TO_DETAIL_TYPE`` lookup is a **closed table**.
Unknown ``interaction_type`` values raise ``UnmappedInteractionTypeError``
rather than emitting a synthetic DetailType that would be silently
dropped by the rule filters. Operator decides whether to extend the
table or fix the upstream type assignment. Plan §3.3.

``extras.contacts`` (full ``{contact_id, email, name, role}`` per contact)
is populated alongside ``extras.contact_ids`` for downstream consumers
that read names for LLM prompts + Contact-node MERGE. Plan §3.4 + §6.6 +
``tasks/downstream/action-item-graph.md`` + ``tasks/downstream/eq-structured-graph-core.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Iterable
from uuid import UUID

import boto3
from botocore.exceptions import NoCredentialsError
from sqlalchemy import text

from models.envelope import ContentModel, EnvelopeV1
from services.account_provisioning.types import (
    EmissionRecord,
    EmittedContact,
    EventBridgeEmissionError,
    InteractionForEmit,
    MaterializationResult,
    UnmappedInteractionTypeError,
)
from services.database import get_async_session

logger = logging.getLogger(__name__)


# Closed mapping: ``raw_interactions.interaction_type`` → EventBridge DetailType.
# The live ``action-item-graph-rule`` + ``eq-structured-graph-rule`` patterns
# filter on these four DetailTypes (probed via ``aws events describe-rule``
# 2026-05-15). New types here require coordinated rule pattern updates
# upstream OR they will be silently dropped by the rule filters.
INTERACTION_TYPE_TO_DETAIL_TYPE: dict[str, str] = {
    "transcript": "EnvelopeV1.transcript",
    "meeting": "EnvelopeV1.meeting",
    "note": "EnvelopeV1.note",
    "email": "EnvelopeV1.email",
    # ``batch_upload`` is what routers/batch.py + routers/upload.py
    # write to raw_interactions.interaction_type for transcript-content
    # ingestion paths (file upload + batch processing). Downstream
    # consumers should treat these as transcript interactions — the
    # source is the same kind of content, just delivered async vs
    # streamed live. Codex P1 2026-05-16: queue entries originating
    # from these paths would have failed Step 6 with
    # ``UnmappedInteractionTypeError`` before this mapping was added.
    "batch_upload": "EnvelopeV1.transcript",
}


_EVENT_SOURCE = "com.yourapp.transcription"


def resolve_detail_type(interaction_type: str) -> str:
    """Look up the EventBridge DetailType for an ``interaction_type``.

    Raises ``UnmappedInteractionTypeError`` if ``interaction_type`` is
    not in the closed table. Tests assert this is a hard error, not a
    silent default.
    """
    try:
        return INTERACTION_TYPE_TO_DETAIL_TYPE[interaction_type]
    except KeyError as exc:
        raise UnmappedInteractionTypeError(
            f"interaction_type={interaction_type!r} is not in the closed "
            f"INTERACTION_TYPE_TO_DETAIL_TYPE lookup. Either extend the "
            f"lookup AND the live EventBridge rule patterns to include "
            f"the new type, or fix the upstream type assignment."
        ) from exc


def build_envelope(
    *,
    interaction: InteractionForEmit,
    tenant_id: str,
    account_id: str,
    queue_id: str,
) -> EnvelopeV1:
    """Construct the ``EnvelopeV1`` for one backfilled interaction.

    ``extras`` carries the contact metadata downstream consumers depend
    on:

    - ``contact_ids`` — bare UUID list (legacy field; still used).
    - ``contacts`` — ``[{contact_id, email, name, role}]`` — required by
      ``action-item-graph`` (LLM prompts, owner resolver) and
      ``eq-structured-graph-core`` (Contact node property population).
    - ``account_provisioning_queue_id`` — audit breadcrumb so a
      downstream consumer that sees the event in CloudTrail can trace
      back to the originating queue entry.
    """
    return EnvelopeV1(
        schema_version="v1",
        tenant_id=UUID(tenant_id),
        user_id=interaction.user_id or tenant_id,
        interaction_type=interaction.interaction_type,
        content=ContentModel(text=interaction.raw_text or "", format="plain"),
        timestamp=interaction.created_at,
        source="api",
        interaction_id=UUID(interaction.interaction_id),
        account_id=account_id,
        trace_id=None,
        pg_user_id=None,
        extras={
            "contact_ids": [c.contact_id for c in interaction.contacts],
            "contacts": [
                {
                    "contact_id": c.contact_id,
                    "email": c.email,
                    "name": c.name,
                    "role": c.role,
                }
                for c in interaction.contacts
            ],
            "account_provisioning_queue_id": queue_id,
        },
    )


def _serialize_envelope(envelope: EnvelopeV1) -> str:
    """Serialize an envelope to the JSON string EventBridge expects.

    EnvelopeV1's ``timestamp`` and UUID serializers run inside
    ``model_dump_json``; calling that directly preserves the
    Z-suffixed timestamp and UUID-as-string conventions other
    consumers depend on.
    """
    return envelope.model_dump_json()


def _build_eventbridge_client() -> Any | None:
    """Build a fresh boto3 EventBridge client; returns ``None`` if
    credentials are unavailable (dev/CI without AWS configured).

    Built per-call rather than module-singleton so a step retry under
    DBOS picks up rotated credentials (Railway / IAM) and so unit tests
    can patch the boto3 factory directly.

    Mirrors ``services.aws_event_publisher.AWSEventPublisher._init_eventbridge_client``'s
    graceful-degradation pattern: in environments where EventBridge is
    intentionally disabled (no AWS creds in env), build returns
    ``None`` and the emit step logs + skips rather than crashing.
    Production deploys (Railway) have credentials in env; local dev
    + CI without secrets get a no-op emission path. Codex P2 2026-05-16.
    """
    region = os.environ.get("AWS_REGION", "us-east-1")
    try:
        return boto3.client("events", region_name=region)
    except NoCredentialsError:
        logger.warning(
            "EventBridge client not built: AWS credentials missing. "
            "Account-provisioning emissions will be skipped. Set "
            "AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY to enable."
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "EventBridge client failed to initialize: %s. Account-"
            "provisioning emissions will be skipped.",
            exc,
        )
        return None


async def emit_envelopes_for_materialization(
    *,
    materialization: MaterializationResult,
    interactions: Iterable[InteractionForEmit],
    boto3_factory: Any = _build_eventbridge_client,
) -> list[EmissionRecord]:  # noqa: D401 — see emit_for_materialization for the higher-level entrypoint
    """Emit one ``EnvelopeV1`` per materialized interaction to EventBridge.

    Returns one ``EmissionRecord`` per successful emission. Raises
    ``EventBridgeEmissionError`` on the first failed entry — DBOS
    retries the whole step. The retry is replay-safe because consumer-
    side MERGE on canonical IDs (interaction_id, contact_id) makes
    duplicate deliveries no-ops at the downstream layer.

    ``boto3_factory`` defaults to building a fresh client; tests inject
    a stub returning a mock client.
    """
    bus_name = os.environ.get("EVENTBRIDGE_BUS_NAME", "default")
    client = boto3_factory()
    if client is None:
        # Graceful no-op when EventBridge is intentionally disabled
        # (no AWS credentials in env). Caller treats this as success:
        # /map returns 200, the workflow's Step 6 records empty
        # emissions, and DBOS proceeds to workflow_status='success'.
        # In production where emission failure would be a real bug,
        # the boto3 client builds successfully and a put_events
        # failure surfaces via FailedEntryCount → EventBridgeEmissionError.
        logger.info(
            "EventBridge emission skipped (client unavailable): "
            "interaction_count=%d. This is expected in dev/CI without "
            "AWS credentials.",
            sum(1 for _ in interactions),
        )
        return []
    emissions: list[EmissionRecord] = []

    for interaction in interactions:
        detail_type = resolve_detail_type(interaction.interaction_type)
        envelope = build_envelope(
            interaction=interaction,
            tenant_id=materialization.tenant_id,
            account_id=materialization.account_id,
            queue_id=materialization.queue_id,
        )
        entry = {
            "Source": _EVENT_SOURCE,
            "DetailType": detail_type,
            "Detail": _serialize_envelope(envelope),
            "EventBusName": bus_name,
        }
        # boto3 is sync; bridge into the async workflow without blocking
        # the event loop. Plan §5.2 + Codex P2 finding.
        response = await asyncio.to_thread(client.put_events, Entries=[entry])

        failed_count = response.get("FailedEntryCount", 0)
        if failed_count:
            # Surface the offending entries; DBOS step retry policy
            # decides whether to retry. Consumer-side MERGE makes
            # duplicate retries safe.
            raise EventBridgeEmissionError(
                f"EventBridge put_events returned FailedEntryCount={failed_count} "
                f"for interaction_id={interaction.interaction_id} "
                f"detail_type={detail_type}: "
                f"{json.dumps(response.get('Entries', []), default=str)}"
            )

        # Extract the assigned EventId if EventBridge provided one
        # (success entries have ``EventId``).
        event_id = None
        entries_resp = response.get("Entries", [])
        if entries_resp and isinstance(entries_resp[0], dict):
            event_id = entries_resp[0].get("EventId")

        emissions.append(
            EmissionRecord(
                interaction_id=interaction.interaction_id,
                detail_type=detail_type,
                event_id=event_id,
            )
        )
        logger.info(
            "Emitted EnvelopeV1 to EventBridge: interaction_id=%s "
            "detail_type=%s event_id=%s",
            interaction.interaction_id, detail_type, event_id,
        )

    return emissions


# ---------------------------------------------------------------------------
# Fetch helpers (shared by workflow Step 6 + /map inline path)
# ---------------------------------------------------------------------------


SELECT_INTERACTIONS_FOR_EMIT_SQL = text("""
    SELECT interaction_id::text AS interaction_id,
           interaction_type,
           raw_text,
           user_id::text AS user_id,
           created_at
    FROM raw_interactions
    WHERE interaction_id = ANY(CAST(:interaction_ids AS uuid[]))
      AND tenant_id = CAST(:tenant_id AS uuid)
""")


# Codex P2 finding 2026-05-15: the signal-role join was previously scoped
# only to ``(queue_id, email)``. If the same contact appeared in multiple
# signals (different interactions, same email — common for the meeting
# organizer who has multiple touchpoints), the join produced one row per
# signal. That duplicated ``extras.contacts`` entries AND could attach a
# role from a different interaction to the emitted envelope. The
# ``interaction_id`` join on ``s.interaction_id = :raw_interaction_id``
# constrains the role to the interaction we're emitting for.
SELECT_CONTACTS_FOR_INTERACTION_SQL = text("""
    SELECT DISTINCT ON (c.id)
           c.id::text AS contact_id,
           c.email,
           CASE
             WHEN c.first_name IS NOT NULL AND c.last_name IS NOT NULL
                  THEN c.first_name || ' ' || c.last_name
             WHEN c.first_name IS NOT NULL THEN c.first_name
             WHEN c.last_name IS NOT NULL THEN c.last_name
             ELSE NULL
           END AS display_name,
           s.contact_role AS role
    FROM interaction_contact_links l
    JOIN interaction_summaries summ ON summ.summary_id = l.interaction_id
    JOIN contacts c ON c.id = l.contact_id
    LEFT JOIN pending_account_mapping_signals s
           ON s.queue_id = CAST(:queue_id AS uuid)
          AND s.interaction_id = CAST(:raw_interaction_id AS uuid)
          AND lower(s.contact_email) = lower(c.email)
          AND s.archived_at IS NULL
    WHERE summ.interaction_id = CAST(:raw_interaction_id AS uuid)
      AND c.tenant_id = CAST(:tenant_id AS uuid)
    ORDER BY c.id, s.created_at DESC NULLS LAST
""")
# Two-layer dedup:
# 1. ``s.interaction_id = :raw_interaction_id`` (added in the first
#    Codex pass): blocks Cartesian fan-out across signals for the
#    SAME contact in DIFFERENT interactions.
# 2. ``DISTINCT ON (c.id) ... ORDER BY c.id, s.created_at DESC``
#    (Codex P2 2026-05-16): the ``pending_signal_dedup`` UNIQUE INDEX
#    on (queue_id, contact_email, source_type, interaction_id,
#    calendar_event_id) ALLOWS multiple signals for the same
#    (interaction_id, contact_email) pair if source_type differs
#    (e.g., one transcript signal + one calendar signal for the same
#    meeting attendee). Without the DISTINCT ON, those would
#    produce duplicate contact entries in extras.contacts with
#    potentially conflicting roles. DISTINCT ON picks the most
#    recent signal's role.


async def fetch_interactions_for_emit(
    *,
    materialization: MaterializationResult,
) -> list[InteractionForEmit]:
    """Read ``raw_interactions`` + per-interaction contact metadata.

    Plan §6.6 + Codex P2 (signal-role join scope): each interaction
    gets its OWN contacts list, filtered by signal.interaction_id so
    multi-touchpoint contacts don't bleed roles across interactions.

    Materialization (round-5 Codex P1 fix) now requires real
    ``raw_interactions`` rows before materializing, so every
    interaction_id here is guaranteed emit-safe (no placeholder
    filtering needed).
    """
    if not materialization.interaction_ids:
        return []

    interactions: list[InteractionForEmit] = []
    async with get_async_session() as session:
        rows = (
            await session.execute(
                SELECT_INTERACTIONS_FOR_EMIT_SQL,
                {
                    "interaction_ids": materialization.interaction_ids,
                    "tenant_id": materialization.tenant_id,
                },
            )
        ).all()
        for row in rows:
            contact_rows = (
                await session.execute(
                    SELECT_CONTACTS_FOR_INTERACTION_SQL,
                    {
                        "raw_interaction_id": row.interaction_id,
                        "queue_id": materialization.queue_id,
                        "tenant_id": materialization.tenant_id,
                    },
                )
            ).all()
            contacts = [
                EmittedContact(
                    contact_id=c.contact_id,
                    email=c.email,
                    name=c.display_name,
                    role=c.role,
                )
                for c in contact_rows
            ]
            interactions.append(
                InteractionForEmit(
                    interaction_id=row.interaction_id,
                    interaction_type=row.interaction_type,
                    raw_text=row.raw_text,
                    user_id=row.user_id,
                    created_at=row.created_at,
                    contacts=contacts,
                )
            )
    return interactions


async def emit_for_materialization_result(
    *,
    materialization: MaterializationResult,
    boto3_factory: Any = _build_eventbridge_client,
) -> list[EmissionRecord]:
    """Fetch interactions for ``materialization`` then emit one EnvelopeV1 each.

    Higher-level entrypoint used by BOTH the workflow's Step 6 AND the
    ``/map`` route's inline path (so /map gets downstream notification
    parity with /approve). Plan §6.6 codifies the per-interaction
    fan-out; without this helper, /map silently skipped emission after
    the outbox was removed (Codex P1 finding 2026-05-15).
    """
    interactions = await fetch_interactions_for_emit(materialization=materialization)
    return await emit_envelopes_for_materialization(
        materialization=materialization,
        interactions=interactions,
        boto3_factory=boto3_factory,
    )
