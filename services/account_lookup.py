"""Postgres-backed account lookup by domain.

Mirrors eq-email-pipeline/src/persistence/postgres.py:lookup_account_by_domain.
Tenant-scoped; never crosses tenant boundaries.
"""

from typing import Optional

from sqlalchemy import text


LOOKUP_SQL = text("""
    SELECT id::text
    FROM accounts
    WHERE tenant_id = :tenant_id
      AND lower(domain) = :domain
    LIMIT 1
""")


async def lookup_account_by_domain(
    session,
    tenant_id: str,
    domain: str,
) -> Optional[str]:
    """Return account_id (str UUID) or None.

    Tenant isolation invariant: always filters by tenant_id; never falls
    back to cross-tenant search.
    """
    normalized = domain.strip().lower()
    result = await session.execute(
        LOOKUP_SQL,
        {"tenant_id": tenant_id, "domain": normalized},
    )
    return result.scalar_one_or_none()
