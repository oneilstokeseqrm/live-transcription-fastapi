"""Shared, race-safe contact find-or-create for ingestion paths.

Standalone helper (NOT a refactor of TranscriptEnrichmentService._resolve_contact)
so the Granola adapter can resolve contacts without re-running calendar matching
or Tavily, and WITHOUT touching the shared transcript/email Lane 2 code.

Atomic ``INSERT ... ON CONFLICT (tenant_id, email) DO UPDATE`` — idempotent and
race-safe (the transcript helper's SELECT-then-INSERT has a TOCTOU window).
Mirrors the proven pattern in account_provisioning/materialization.py:86-97.

``account_id`` is COALESCEd, never reassigned: an existing contact keeps its
account (non-corrupting). Callers compare ``account_matched`` and log divergence
for observability rather than hard-failing (a 5-min poll loop must stay
available; a person from company B legitimately attending a company-A meeting is
not corruption).

Find-or-create lifecycle:

    given (tenant_id, email, account_id, display_name)
        │
        ▼
    INSERT contacts (...) VALUES (...)
        │
        ├── no conflict ──► new row created, returns its id + names
        │
        └── ON CONFLICT (tenant_id, email) ──► DO UPDATE
                fill NULL first/last via COALESCE (never overwrite manual data)
                fill NULL account_id via COALESCE (never reassign a set account)
                RETURNING the (existing) id + names + account_id
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text


@dataclass(frozen=True)
class ResolvedContactRow:
    """One resolved contact. ``contact_id`` is the canonical Postgres UUIDv4."""

    contact_id: str            # canonical UUIDv4 (str)
    email: str                 # normalized lower-case
    name: Optional[str]        # full name or None
    account_id: str            # the account the contact row is actually bound to (str)
    account_matched: bool      # False if an existing contact's account differed from requested


_FIND_OR_CREATE_SQL = text(
    """
    INSERT INTO contacts (
        id, tenant_id, email, first_name, last_name, account_id,
        source, validation_status, created_at, updated_at
    ) VALUES (
        gen_random_uuid(), :tenant_id, lower(:email), :first_name, :last_name, :account_id,
        :source, 'pending', NOW(), NOW()
    )
    ON CONFLICT (tenant_id, email) DO UPDATE
        SET first_name = COALESCE(contacts.first_name, EXCLUDED.first_name),
            last_name  = COALESCE(contacts.last_name,  EXCLUDED.last_name),
            account_id = COALESCE(contacts.account_id, EXCLUDED.account_id),
            updated_at = NOW()
    RETURNING id::text AS contact_id, first_name, last_name, account_id::text AS account_id
    """
)


def _split_display_name(display_name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not display_name:
        return None, None
    parts = display_name.strip().split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _full_name(first: Optional[str], last: Optional[str]) -> Optional[str]:
    return " ".join(p for p in (first, last) if p) or None


async def find_or_create_contact(
    *,
    session,
    tenant_id: str,
    email: str,
    account_id: str,
    display_name: Optional[str] = None,
    source: str = "granola_ingestion",
) -> ResolvedContactRow:
    """Find-or-create a contact by (tenant_id, email), bound to account_id.

    Tenant-scoped via the (tenant_id, email) conflict key. ``account_id`` MUST be
    a tenant-scoped account (the caller resolves it via lookup_account_by_domain).
    Does NOT commit — the caller owns the transaction so many attendees resolve in
    one session. ``account_matched`` is False when an existing contact's account
    differed (COALESCE kept it; the caller logs for observability).
    """
    if not account_id:
        raise ValueError("find_or_create_contact requires a non-empty account_id")
    email_norm = (email or "").strip().lower()
    if not email_norm:
        raise ValueError("find_or_create_contact requires a non-empty email")
    first, last = _split_display_name(display_name)
    result = await session.execute(
        _FIND_OR_CREATE_SQL,
        {
            "tenant_id": uuid.UUID(tenant_id),
            "email": email_norm,
            "first_name": first,
            "last_name": last,
            "account_id": uuid.UUID(account_id),
            "source": source,
        },
    )
    row = result.mappings().one()
    returned_account = row["account_id"]
    return ResolvedContactRow(
        contact_id=row["contact_id"],
        email=email_norm,
        name=_full_name(row["first_name"], row["last_name"]),
        account_id=returned_account,
        account_matched=(returned_account == account_id),
    )
