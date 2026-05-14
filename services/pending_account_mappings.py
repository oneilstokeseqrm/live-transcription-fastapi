"""Helpers for inserting/upserting into pending_account_mappings + signals.

Implements UPSERT semantics from design Section 5.2:
- Parent row: first-owner-wins on (tenant_id, domain); only expires_at refreshes.
- Signal row: unconditional insert with idempotency via unique constraint.
- Re-open: archived entry + new signal -> transitions back to pending.

All operations are tenant-scoped. No cross-tenant queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text


@dataclass
class SignalProposal:
    source_type: str  # email | transcript | calendar | manual
    source_user_id: str
    interaction_id: Optional[str]
    calendar_event_id: Optional[str]
    contact_email: str
    contact_display_name: Optional[str]
    contact_role: Optional[str]


@dataclass
class QueueRow:
    id: str
    archived_at: Optional[datetime]
    re_open_count: int


UPSERT_PARENT_SQL = text("""
    INSERT INTO pending_account_mappings
        (id, tenant_id, domain, status,
         owner_user_id, discovered_from_type, discovered_from_interaction_id,
         expires_at, created_at, updated_at)
    VALUES
        (gen_random_uuid(), :tenant_id, lower(:domain), 'pending',
         :owner_user_id, :discovered_from_type, :discovered_from_interaction_id,
         :expires_at, NOW(), NOW())
    ON CONFLICT (tenant_id, domain) DO UPDATE
        SET expires_at = GREATEST(pending_account_mappings.expires_at, EXCLUDED.expires_at),
            updated_at = NOW()
    RETURNING id::text
""")


# TODO(task-1.5.12 reopen lifecycle): also reset the approval/materialization
# fields here so a reopened entry doesn't 409 forever on /approve|/map with a
# new attempt_id. Add: approval_attempt_id = NULL, creation_started_at = NULL,
# mapped_at = NULL, resolved_account_id = NULL, ignored_at = NULL,
# ignored_by = NULL. Codex Round 7 P1 finding 2026-05-14 — deferred to the
# Task 1.5.12 expiry-sweep PR that wires reopen end-to-end (without that PR,
# the reopen path is not exercised in production today).
REOPEN_PARENT_SQL = text("""
    UPDATE pending_account_mappings
    SET archived_at = NULL,
        archive_reason = NULL,
        re_open_count = re_open_count + 1,
        last_reopened_at = NOW(),
        status = 'pending',
        expires_at = :expires_at,
        updated_at = NOW()
    WHERE tenant_id = :tenant_id
      AND lower(domain) = lower(:domain)
      AND archived_at IS NOT NULL
    RETURNING id::text
""")


INSERT_SIGNAL_SQL = text("""
    INSERT INTO pending_account_mapping_signals
        (id, tenant_id, queue_id, source_type, source_user_id,
         interaction_id, calendar_event_id,
         contact_email, contact_display_name, contact_role, created_at)
    VALUES
        (gen_random_uuid(), :tenant_id, :queue_id, :source_type, :source_user_id,
         :interaction_id, :calendar_event_id,
         :contact_email, :contact_display_name, :contact_role, NOW())
    ON CONFLICT ON CONSTRAINT pending_signal_dedup DO NOTHING
""")


async def upsert_queue_entry(
    session,
    tenant_id: str,
    domain: str,
    owner_user_id: str,
    discovered_from_type: str,
    discovered_from_interaction_id: Optional[str],
    expires_in_days: int = 30,
) -> str:
    """Insert-or-update the parent queue row; returns queue_id.

    First-owner-wins on owner_user_id, discovered_from_type, discovered_from_interaction_id.
    Re-open of an archived row uses `reopen_archived_entry()` instead.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
    result = await session.execute(
        UPSERT_PARENT_SQL,
        {
            "tenant_id": tenant_id,
            "domain": domain,
            "owner_user_id": owner_user_id,
            "discovered_from_type": discovered_from_type,
            "discovered_from_interaction_id": discovered_from_interaction_id,
            "expires_at": expires_at,
        },
    )
    return result.scalar_one()


async def reopen_archived_entry(
    session,
    tenant_id: str,
    domain: str,
    expires_in_days: int = 30,
) -> Optional[str]:
    """If an archived entry exists for this (tenant, domain), transition it
    back to pending and return its id. Returns None when no archived entry.
    """
    expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
    result = await session.execute(
        REOPEN_PARENT_SQL,
        {"tenant_id": tenant_id, "domain": domain, "expires_at": expires_at},
    )
    row = result.first()
    return row[0] if row else None


async def insert_signal(
    session,
    tenant_id: str,
    queue_id: str,
    proposal: SignalProposal,
) -> None:
    """Insert a signal row; idempotent under retry via unique constraint."""
    await session.execute(
        INSERT_SIGNAL_SQL,
        {
            "tenant_id": tenant_id,
            "queue_id": queue_id,
            "source_type": proposal.source_type,
            "source_user_id": proposal.source_user_id,
            "interaction_id": proposal.interaction_id,
            "calendar_event_id": proposal.calendar_event_id,
            "contact_email": proposal.contact_email.strip().lower(),
            "contact_display_name": proposal.contact_display_name,
            "contact_role": proposal.contact_role,
        },
    )
