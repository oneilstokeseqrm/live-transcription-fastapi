"""Per-tenant connected-provider internal-domain auto-discovery.

Mirrors `eq-email-pipeline/src/persistence/postgres.py::get_tenant_internal_domains`
but adapted to this repo's SQLAlchemy AsyncSession layer.

Returns the union of:
  - auto-discovered domains (email-host of every active provider_connection,
    minus PERSONAL_DOMAINS — manual config wins, so public personal domains
    are stripped from auto only)
  - manually configured internal_domains[] on each row (no stripping)

Fails soft: returns set() on any DB error so the BUSINESS+known branch
of per-attendee enrichment keeps working even if classification can't load.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import text

from services.database import get_async_session
from services.domain_classification import PERSONAL_DOMAINS

logger = logging.getLogger(__name__)


_INTERNAL_DOMAINS_SQL = text("""
    SELECT email_address, internal_domains
    FROM provider_connections
    WHERE tenant_id = :tenant_id
      AND status = 'active'
""")


async def get_tenant_internal_domains(tenant_id: str) -> set[str]:
    """Return per-tenant connected-provider internal domains.

    Args:
        tenant_id: Tenant UUID string.

    Returns:
        Set of lower-cased domains. Empty set on error or no rows.
    """
    try:
        tid = uuid.UUID(tenant_id)
    except (ValueError, TypeError) as exc:
        logger.warning(
            f"get_tenant_internal_domains: invalid tenant_id={tenant_id!r}: {exc}"
        )
        return set()

    try:
        async with get_async_session() as session:
            result = await session.execute(
                _INTERNAL_DOMAINS_SQL,
                {"tenant_id": tid},
            )
            rows = list(result.mappings().all())
    except Exception as exc:
        logger.warning(
            f"get_tenant_internal_domains failed (returning empty set): "
            f"tenant_id={tenant_id[:8]}..., error={type(exc).__name__}: {exc}"
        )
        return set()

    auto_domains: set[str] = set()
    manual_domains: set[str] = set()

    for row in rows:
        email_addr = row.get("email_address") if hasattr(row, "get") else row["email_address"]
        if email_addr and "@" in email_addr:
            domain = email_addr.rsplit("@", 1)[-1].lower()
            auto_domains.add(domain)

        manual = row["internal_domains"] if "internal_domains" in row.keys() else None
        for d in (manual or []):
            if d:
                manual_domains.add(d.lower())

    # Strip public personal-email domains from auto-discovered only.
    # Manual overrides always win — if a tenant manually configured
    # `gmail.com` as internal, we honor it.
    auto_domains -= PERSONAL_DOMAINS

    return auto_domains | manual_domains
