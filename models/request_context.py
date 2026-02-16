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
        account_id: Optional string for additional account-level context
        interaction_id: UUID v4 uniquely identifying this specific request
        trace_id: UUID v4 for distributed tracing (from X-Trace-Id header or generated)
    """
    tenant_id: str
    user_id: str
    account_id: Optional[str]
    interaction_id: str
    trace_id: str
    pg_user_id: Optional[str] = None
