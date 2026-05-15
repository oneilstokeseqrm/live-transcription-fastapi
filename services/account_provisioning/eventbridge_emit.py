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

from models.envelope import ContentModel, EnvelopeV1
from services.account_provisioning.types import (
    EmissionRecord,
    EventBridgeEmissionError,
    InteractionForEmit,
    MaterializationResult,
    UnmappedInteractionTypeError,
)

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


def _build_eventbridge_client() -> Any:
    """Build a fresh boto3 EventBridge client.

    Built per-call rather than module-singleton so a step retry under
    DBOS picks up rotated credentials (Railway / IAM) and so unit tests
    can patch the boto3 factory directly.
    """
    region = os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client("events", region_name=region)


async def emit_envelopes_for_materialization(
    *,
    materialization: MaterializationResult,
    interactions: Iterable[InteractionForEmit],
    boto3_factory: Any = _build_eventbridge_client,
) -> list[EmissionRecord]:
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
