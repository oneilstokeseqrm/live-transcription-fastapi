"""Atomic materialization for queue approval / mapping (Phase 1.5 M3).

Runs in a single Postgres transaction:
1. INSERT contacts (one per distinct signal email) with the resolved account_id.
2. INSERT interaction_contact_links for every signal that has interaction_id.
3. UPDATE queue entry to status='mapped'.

Moved from ``workers/materialization.py`` in M3. Two M3-required changes
relative to the prior version:

- The ``INSERT_OUTBOX_SQL`` write is REMOVED. ``account_provisioning_outbox``
  is dropped post-M3 (M3.5) — DBOS ``workflow_status`` is the observability
  surface going forward.
- The in-memory ``linked_pairs`` set is REMOVED. The link INSERT uses
  ``ON CONFLICT (interaction_id, contact_id) DO NOTHING`` against the
  ``interaction_contact_links_interaction_id_contact_id_key`` UNIQUE INDEX
  added by M2 — SQL-level dedup is the only correct replay-safety under
  DBOS step retries.

Caller (the workflow step OR the inline ``/map`` path) is responsible for
opening the transaction and calling session.commit() / session.rollback().
"""

import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.account_provisioning.types import MaterializationResult


SELECT_SIGNALS_SQL = text("""
    SELECT id, contact_email, contact_display_name, contact_role,
           interaction_id, source_type
    FROM pending_account_mapping_signals
    WHERE queue_id = :queue_id AND archived_at IS NULL
""")


CHECK_RAW_INTERACTION_EXISTS_SQL = text("""
    SELECT 1 FROM raw_interactions
    WHERE interaction_id = :interaction_id
    LIMIT 1
""")
# Codex P2 2026-05-16: detect whether raw_interactions has a real
# row before the materialization writes the placeholder. Placeholder
# rows carry hard-coded interaction_type='meeting' + empty raw_text;
# emitting them downstream would corrupt the EnvelopeV1 contract
# (wrong DetailType, empty content). The MaterializationResult
# tracks placeholder interaction_ids so the emit step can skip them.
# The DB writes for placeholders still happen (interaction_contact_links
# needs a parent summary which needs a parent raw_interactions row);
# only the downstream emission is filtered.


INSERT_CONTACT_SQL = text("""
    INSERT INTO contacts (id, tenant_id, email, first_name, last_name, account_id,
                          source, validation_status, created_at, updated_at)
    VALUES (gen_random_uuid(), :tenant_id, lower(:email), :first_name, :last_name,
            :account_id, :source, 'verified', NOW(), NOW())
    ON CONFLICT (tenant_id, email) DO UPDATE
        SET first_name = COALESCE(contacts.first_name, EXCLUDED.first_name),
            last_name = COALESCE(contacts.last_name, EXCLUDED.last_name),
            account_id = COALESCE(contacts.account_id, EXCLUDED.account_id),
            updated_at = NOW()
    RETURNING id::text, account_id::text
""")
# validation_status='verified' matches the live ContactValidationStatus enum
# (pending | verified | discarded). Materialization runs only when the queue
# resolves an account; the contact is therefore verified, not pending.
#
# account_id is preserved on conflict via COALESCE; the caller checks the
# RETURNED account_id against the materialization's input account_id. A
# mismatch means the contact already belongs to a different account
# (cross-account reassignment is Phase 3 scope — fail loud).


UPSERT_RAW_INTERACTION_SQL = text("""
    INSERT INTO raw_interactions (
        interaction_id, tenant_id, account_id, interaction_type, updated_at
    ) VALUES (
        :interaction_id, :tenant_id, :account_id, :interaction_type, NOW()
    )
    ON CONFLICT (interaction_id) DO NOTHING
""")
# account_id is included because raw_interactions.account_id is NOT NULL
# (enforced in Phase 1.5 schema). For backfill cases (the queue's signal
# references an interaction that doesn't have a raw_interactions row yet),
# we use the materialization's account_id as the anchor.


UPSERT_PLACEHOLDER_SUMMARY_SQL = text("""
    INSERT INTO interaction_summaries (
        summary_id, tenant_id, interaction_id, summary_type, created_at, updated_at
    ) VALUES (
        :summary_id, :tenant_id, :interaction_id, :summary_type, NOW(), NOW()
    )
    ON CONFLICT (interaction_id) DO UPDATE
        SET updated_at = interaction_summaries.updated_at
    RETURNING summary_id::text
""")
# interaction_summaries.interaction_id has a UNIQUE INDEX
# (interaction_summaries_interaction_id_key). The ON CONFLICT clause makes
# this race-safe: if the summaries-writer service inserts a row between
# our intent to insert and our actual write, the conflict path fires a
# no-op UPDATE (preserving the existing updated_at) and RETURNING gives
# us the existing summary_id.


INSERT_LINK_SQL = text("""
    INSERT INTO interaction_contact_links (link_id, interaction_id, contact_id)
    VALUES (gen_random_uuid(), :summary_id, :contact_id)
    ON CONFLICT (interaction_id, contact_id) DO NOTHING
""")
# Phase 1.5 M2 added UNIQUE INDEX (interaction_id, contact_id), making this
# INSERT replay-safe at the SQL layer. Previously the in-memory linked_pairs
# set provided dedup within a single call; that was replay-broken across
# DBOS step retries (each retry starts with a fresh set). ON CONFLICT
# DO NOTHING is the canonical idempotency mechanism.
#
# Note: the column literally named ``interaction_id`` on this table stores
# ``summary_id`` — Prisma naming artifact, documented in tasks/lessons.md.
# We bind ``:summary_id`` to it for that reason.


UPDATE_QUEUE_SQL = text("""
    UPDATE pending_account_mappings
    SET status = 'mapped',
        resolved_account_id = :account_id,
        mapped_at = NOW(),
        updated_at = NOW()
    WHERE id = :queue_id
""")


def _split_name(display_name: str | None) -> tuple[str | None, str | None]:
    if not display_name:
        return (None, None)
    parts = display_name.strip().split()
    if not parts:
        return (None, None)
    if len(parts) == 1:
        return (parts[0], None)
    return (parts[0], " ".join(parts[1:]))


async def materialize_account_approval(
    session: AsyncSession,
    tenant_id: str,
    queue_id: str,
    account_id: str,
    event_type: str,  # "account_created" | "account_mapped"
) -> MaterializationResult:
    """Materialize all signals for a queue entry.

    IMPORTANT: Caller MUST open a transaction before calling this function and
    call session.commit() or session.rollback() after. This function does NOT
    commit. If called outside a transaction, each statement autocommits and the
    atomic guarantee is lost.

    The DBOS workflow step (M3) and the inline ``/map`` route both call this
    function; both wrap it in ``async with session.begin():``. The workflow's
    Step 6 (emit) consumes the returned ``MaterializationResult`` to fan out
    per-interaction EnvelopeV1 events; ``/map`` ignores the return.

    Raises ValueError if no active signals exist for the queue_id — a queue
    entry being materialized with zero signals is architecturally wrong (signals
    are what materialize into contacts) and would produce an empty result. Fail
    loud so the caller logs + the workflow surfaces.
    """
    # event_type is retained for signature parity with the prior worker call
    # site; with the outbox removed, the workflow records lifecycle in
    # dbos.workflow_status instead, and /map's local audit lives in the row's
    # mapped_at + resolved_account_id stamps.
    del event_type

    signals = (await session.execute(SELECT_SIGNALS_SQL, {"queue_id": queue_id})).all()

    if not signals:
        raise ValueError(
            f"materialize_account_approval called with no active signals "
            f"for queue_id={queue_id!r}"
        )

    contact_ids: list[str] = []
    interaction_ids: list[str] = []
    placeholder_interaction_ids: set[str] = set()
    # Lazy cache: at most one placeholder interaction_summaries row per
    # raw_interaction_id, created on first reference. All contacts for that
    # interaction link to the same summary_id; the link INSERT uses
    # ON CONFLICT DO NOTHING against the (interaction_id, contact_id) unique
    # index, so re-execution under DBOS replay is a no-op.
    summary_id_by_raw_id: dict[str, str] = {}

    for s in signals:
        first, last = _split_name(s.contact_display_name)
        result = await session.execute(
            INSERT_CONTACT_SQL,
            {
                "tenant_id": tenant_id,
                "email": s.contact_email,
                "first_name": first,
                "last_name": last,
                "account_id": account_id,
                "source": s.source_type,
            },
        )
        row = result.one()
        contact_id = row.id
        returned_account_id = row.account_id
        if returned_account_id != account_id:
            raise ValueError(
                f"Contact {s.contact_email!r} already belongs to account "
                f"{returned_account_id!r}; cannot materialize against account "
                f"{account_id!r} for queue_id={queue_id!r}. Cross-account "
                f"contact reassignment is Phase 3 scope; materialization "
                f"fails loud so the operator can investigate."
            )
        contact_ids.append(contact_id)

        if s.interaction_id is not None:
            raw_id = str(s.interaction_id)
            summary_id = summary_id_by_raw_id.get(raw_id)
            if summary_id is None:
                # Codex P2 2026-05-16: detect whether the upstream
                # ingestion (Lane 2 / intelligence_service) has
                # already written a real raw_interactions row. If
                # NOT, we still need a placeholder for the
                # interaction_summaries + interaction_contact_links
                # FK chain — BUT the placeholder's content
                # (interaction_type='meeting', empty raw_text) is
                # WRONG for downstream emission. Track the
                # interaction_id as a placeholder so Step 6's emit
                # path skips it. Lane 2 will eventually write the
                # real row; a future re-emission tool (M5) can
                # backfill the downstream notification.
                existing_check = (
                    await session.execute(
                        CHECK_RAW_INTERACTION_EXISTS_SQL,
                        {"interaction_id": s.interaction_id},
                    )
                ).first()
                if existing_check is None:
                    placeholder_interaction_ids.add(raw_id)
                # Ensure parent raw_interactions row exists (summaries-writer
                # may have already created it; ON CONFLICT DO NOTHING is safe).
                await session.execute(
                    UPSERT_RAW_INTERACTION_SQL,
                    {
                        "interaction_id": s.interaction_id,
                        "tenant_id": tenant_id,
                        "account_id": account_id,
                        "interaction_type": "meeting",
                    },
                )
                # UPSERT the placeholder summary atomically. The ON CONFLICT
                # (interaction_id) branch handles the race where the
                # summaries-writer service inserts a row concurrently — we
                # get the existing summary_id back without aborting our txn.
                proposed_summary_id = str(uuid.uuid4())
                result = await session.execute(
                    UPSERT_PLACEHOLDER_SUMMARY_SQL,
                    {
                        "summary_id": proposed_summary_id,
                        "tenant_id": tenant_id,
                        "interaction_id": s.interaction_id,
                        "summary_type": "meeting",
                    },
                )
                summary_id = result.scalar_one()
                summary_id_by_raw_id[raw_id] = summary_id

            await session.execute(
                INSERT_LINK_SQL,
                {"summary_id": summary_id, "contact_id": contact_id},
            )
            interaction_ids.append(raw_id)

    await session.execute(
        UPDATE_QUEUE_SQL,
        {"queue_id": queue_id, "account_id": account_id},
    )

    # Dedupe via dict.fromkeys (preserves order; one contact per email,
    # one interaction per raw_id, even if multiple signals share them).
    return MaterializationResult(
        queue_id=queue_id,
        tenant_id=tenant_id,
        account_id=account_id,
        contact_ids=list(dict.fromkeys(contact_ids)),
        interaction_ids=list(dict.fromkeys(interaction_ids)),
        placeholder_interaction_ids=sorted(placeholder_interaction_ids),
    )
