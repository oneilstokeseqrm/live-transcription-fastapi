"""Postgres-backed account lookup by domain.

Mirrors eq-email-pipeline/src/persistence/postgres.py:lookup_account_by_domain.
Tenant-scoped; never crosses tenant boundaries.
"""

from typing import Optional

from sqlalchemy import text


LOOKUP_SQL = text("""
    SELECT account_id::text
    FROM account_domains
    WHERE tenant_id = :tenant_id
      AND domain = :domain
    LIMIT 1
""")
# Domain → account routing lives in account_domains (a join table),
# not on accounts itself. The accounts table has no `domain` column —
# verified via information_schema.columns on the eq-dev project
# (super-glitter-11265514) on 2026-05-15. The prior version of this
# SQL referenced accounts.domain and threw a "column does not exist"
# error on every BUSINESS-domain attendee, which was caught by the
# enclosing try/except in TranscriptEnrichmentService.enrich() and
# silently returned an empty EnrichmentResult — meaning calendar match,
# contact resolution, and link writes all silently failed for any
# transcript with BUSINESS-domain calendar attendees. See the
# eq-email-pipeline mirror for the historically-correct version.


async def lookup_account_by_domain(
    session,
    tenant_id: str,
    domain: str,
) -> Optional[str]:
    """Return account_id (str UUID) or None.

    Tenant isolation invariant: always filters by tenant_id; never falls
    back to cross-tenant search. Domain is lower-cased to match the
    account_domains write convention (eq-email-pipeline writes lower(domain)).
    """
    normalized = domain.strip().lower()
    result = await session.execute(
        LOOKUP_SQL,
        {"tenant_id": tenant_id, "domain": normalized},
    )
    return result.scalar_one_or_none()
