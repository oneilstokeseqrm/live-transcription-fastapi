"""
Request Context Data Model

This module defines the RequestContext dataclass for extracting and storing
tenant, user, and account identity information from request headers.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RequestContext:
    """
    Context information extracted from request headers and environment.

    This dataclass holds identity information for multi-tenant scenarios,
    enabling proper event attribution and routing.

    Attributes:
        tenant_id: UUID v4 identifying the tenant/organization
        user_id: String identifying the user who initiated the request
        pg_user_id: Optional Postgres User UUID from identity bridge
        account_id: Account anchor; required for ingestion auth contexts.
        interaction_id: UUID v4 uniquely identifying this specific request
        trace_id: UUID v4 for distributed tracing (from X-Trace-Id header or generated)
        trusted_event_time: True only when identity came through the verified
            internal-JWT path. Gates whether a caller-supplied ``occurred_at``
            is honored (EQ-230). Defaults False so any context built outside
            the verified-JWT path — legacy headers, the lenient/websocket
            fallback, or a bare construction — is untrusted by default. Trust
            is never inferred from a header.
    """
    tenant_id: str
    user_id: str
    account_id: str
    interaction_id: str
    trace_id: str
    pg_user_id: Optional[str] = None
    user_name: Optional[str] = None
    trusted_event_time: bool = False
