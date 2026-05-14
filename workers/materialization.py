"""Atomic materialization for queue approval / mapping.

Runs in a single Postgres transaction:
1. INSERT contacts (one per distinct signal email) with the resolved account_id.
2. INSERT interaction_contact_links for every signal that has interaction_id.
3. UPDATE queue entry to status='mapped'.
4. INSERT into account_provisioning_outbox (durable event log).

Caller is responsible for opening the transaction and calling
session.commit() / session.rollback().
"""

import json
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


SELECT_SIGNALS_SQL = text("""
    SELECT id, contact_email, contact_display_name, contact_role,
           interaction_id, source_type
    FROM pending_account_mapping_signals
    WHERE queue_id = :queue_id AND archived_at IS NULL
""")


INSERT_CONTACT_SQL = text("""
    INSERT INTO contacts (id, tenant_id, email, first_name, last_name, account_id,
                          source, validation_status, created_at, updated_at)
    VALUES (gen_random_uuid(), :tenant_id, lower(:email), :first_name, :last_name,
            :account_id, :source, 'validated', NOW(), NOW())
    ON CONFLICT (tenant_id, email) DO UPDATE
        SET first_name = COALESCE(contacts.first_name, EXCLUDED.first_name),
            last_name = COALESCE(contacts.last_name, EXCLUDED.last_name),
            account_id = COALESCE(contacts.account_id, EXCLUDED.account_id),
            updated_at = NOW()
    RETURNING id::text
""")


INSERT_LINK_SQL = text("""
    INSERT INTO interaction_contact_links (link_id, interaction_id, contact_id)
    SELECT gen_random_uuid(), s.summary_id, :contact_id
    FROM interaction_summaries s
    WHERE s.interaction_id = :raw_interaction_id
    ON CONFLICT DO NOTHING
""")


UPDATE_QUEUE_SQL = text("""
    UPDATE pending_account_mappings
    SET status = 'mapped',
        resolved_account_id = :account_id,
        mapped_at = NOW(),
        updated_at = NOW()
    WHERE id = :queue_id
""")


INSERT_OUTBOX_SQL = text("""
    INSERT INTO account_provisioning_outbox
        (id, tenant_id, queue_id, event_type, account_id, payload_json, created_at)
    VALUES
        (gen_random_uuid(), :tenant_id, :queue_id, :event_type, :account_id,
         :payload_json::jsonb, NOW())
    RETURNING id::text
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
) -> None:
    """Materialize all signals for a queue entry.

    IMPORTANT: Caller MUST open a transaction before calling this function and
    call session.commit() or session.rollback() after. This function does NOT
    commit. If called outside a transaction, each statement autocommits and the
    atomic guarantee is lost.

    Raises ValueError if no active signals exist for the queue_id — a queue
    entry being materialized with zero signals is architecturally wrong (signals
    are what materialize into contacts) and would produce a malformed outbox
    row with contact_ids: []. Fail loud so the worker logs + retries.
    """
    signals = (await session.execute(SELECT_SIGNALS_SQL, {"queue_id": queue_id})).all()

    if not signals:
        raise ValueError(
            f"materialize_account_approval called with no active signals "
            f"for queue_id={queue_id!r}"
        )

    contact_ids: list[str] = []
    interaction_ids: list[str] = []
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
        contact_id = result.scalar_one()
        contact_ids.append(contact_id)

        if s.interaction_id is not None:
            await session.execute(
                INSERT_LINK_SQL,
                {
                    "contact_id": contact_id,
                    "raw_interaction_id": s.interaction_id,
                },
            )
            interaction_ids.append(str(s.interaction_id))

    await session.execute(
        UPDATE_QUEUE_SQL,
        {"queue_id": queue_id, "account_id": account_id},
    )

    payload = {
        "account_id": account_id,
        "tenant_id": tenant_id,
        "queue_id": queue_id,
        "contact_ids": list(dict.fromkeys(contact_ids)),
        "interaction_ids": list(dict.fromkeys(interaction_ids)),
    }
    await session.execute(
        INSERT_OUTBOX_SQL,
        {
            "tenant_id": tenant_id,
            "queue_id": queue_id,
            "event_type": event_type,
            "account_id": account_id,
            "payload_json": json.dumps(payload),
        },
    )
