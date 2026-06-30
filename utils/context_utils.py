"""
Context Extraction Utilities

This module provides utilities for extracting tenant, user, and account identity
from request headers, internal JWTs, or environment variables.

Four extraction modes are available:

1. get_request_context() - Lenient extraction with fallbacks (backward compatible)
   - Falls back to environment variables and defaults
   - Never raises exceptions

2. get_validated_context() - Legacy-header strict path; accepts a
   require_account_id flag for ingestion vs polling contracts. Called by
   get_auth_context_ingestion and get_auth_context_polling.
   - Requires X-Tenant-ID (valid UUID), X-User-ID (non-empty)
   - Requires X-Account-ID when require_account_id=True (ingestion);
     allows it to be absent when require_account_id=False (polling).
   - Optional X-Trace-Id (valid UUID if provided, generated if missing)
   - Raises HTTPException 400 on validation failure

3. get_auth_context_ingestion() - Unified auth for INGESTION (writes/mutations).
   - Tries JWT from Authorization header first (gateway requests)
   - Falls back to header-based auth when ALLOW_LEGACY_HEADER_AUTH=true
   - REQUIRES X-Account-ID and raises HTTPException 400 if absent
   - Raises HTTPException 401 for invalid JWT
   - Use for: /text/clean, /batch/process, /upload/init, /upload/complete

4. get_auth_context_polling() - Unified auth for POLLING / read-only routes.
   - Same JWT/header verification as get_auth_context_ingestion
   - DOES NOT require X-Account-ID; returns RequestContext.account_id = ""
   - Callers MUST NOT use context.account_id for any write/persist operation
     (the sentinel empty-string makes accidental writes loud rather than silent)
   - Use for: GET /upload/status/{job_id}, any other non-mutating route

The ingestion/polling split (T1.26.4, 2026-05-14) replaces the legacy
get_auth_context() helper. The split is documented in
docs/contacts-architecture.md Section 3.5.
"""

import os
import uuid
import logging
from fastapi import Request, HTTPException
from typing import Optional
from models.request_context import RequestContext
from middleware.jwt_auth import (
    verify_internal_jwt,
    extract_bearer_token,
    JWTVerificationError,
    is_jwt_auth_configured,
)

logger = logging.getLogger(__name__)


def get_request_context(request: Request) -> RequestContext:
    """
    Extract context from request headers with fallback to environment variables.
    
    This function implements a robust fallback chain to ensure that context
    information is always available, even in development or testing scenarios
    where headers may not be present.
    
    Priority order:
    1. Request headers (X-Tenant-ID, X-User-ID, X-Account-ID, X-Trace-Id)
    2. Environment variables (MOCK_TENANT_ID, MOCK_USER_ID)
    3. Generated/default values
    
    Args:
        request: FastAPI Request object containing headers
        
    Returns:
        RequestContext with all identity fields populated
        
    Raises:
        None - always returns valid context with fallbacks
    """
    # Generate interaction_id for this request
    interaction_id = str(uuid.uuid4())
    
    # Extract tenant_id with fallback chain
    tenant_id = _extract_tenant_id(request, interaction_id)
    
    # Extract user_id with fallback chain
    user_id = _extract_user_id(request, interaction_id)
    
    # Extract account_id (optional in this lenient path; WebSocket /listen
    # is the sole caller and gets tightened in Task 1.11 of the Contact
    # Quality Initiative, after which this path will also require it).
    account_id = _extract_account_id(request)

    # Extract trace_id (optional, generate if not provided)
    trace_id = _extract_trace_id(request, interaction_id)

    # Log extracted context
    logger.info(
        f"Context extracted: interaction_id={interaction_id}, "
        f"tenant_id={tenant_id}, user_id={user_id}, "
        f"account_id={account_id or 'None'}, trace_id={trace_id}"
    )

    return RequestContext(
        tenant_id=tenant_id,
        user_id=user_id,
        account_id=account_id,  # type: ignore[arg-type]  # T1.11 tightens this
        interaction_id=interaction_id,
        trace_id=trace_id
    )


def get_validated_context(
    request: Request,
    require_account_id: bool = True,
) -> RequestContext:
    """
    Extract and validate context from request headers with strict validation.

    This function enforces required headers and validates their formats.
    Use this for legacy header-auth endpoints that require proper identity
    context. New endpoints should prefer get_auth_context_ingestion() /
    get_auth_context_polling().

    Required Headers:
    - X-Tenant-ID: Must be a valid UUID
    - X-User-ID: Must be a non-empty string
    - X-Account-ID: Required when require_account_id is True (ingestion routes)

    Optional Headers:
    - X-Trace-Id: If provided, must be a valid UUID; generated if missing

    Args:
        request: FastAPI Request object containing headers
        require_account_id: When True (default), missing X-Account-ID raises 400.
            When False, the missing header is allowed and the returned
            RequestContext.account_id is the empty string. Polling/read-only
            routes should pass require_account_id=False.

    Returns:
        RequestContext with all identity fields populated. When
        require_account_id is False and the header is absent, account_id is "".

    Raises:
        HTTPException: 400 if required headers are missing or invalid
    """
    # Generate interaction_id for this request
    interaction_id = str(uuid.uuid4())

    # Validate X-Tenant-ID (required, must be valid UUID)
    tenant_id = _validate_tenant_id(request)

    # Validate X-User-ID (required, non-empty string)
    user_id = _validate_user_id(request)

    # Validate X-Trace-Id (optional, generate if missing)
    trace_id = _validate_trace_id(request, interaction_id)

    # Account anchor contract: required for ingestion (raises 400 when missing
    # or whitespace-only); relaxed to "" sentinel for polling/read-only routes.
    # Callers MUST NOT persist the "" sentinel.
    account_id = _require_or_relax_account_id_header(
        request, required=require_account_id
    )

    # Log extracted context
    logger.info(
        f"Validated context: interaction_id={interaction_id}, "
        f"tenant_id={tenant_id}, user_id={user_id}, "
        f"account_id={account_id or '<polling-no-account>'}, trace_id={trace_id}"
    )

    return RequestContext(
        tenant_id=tenant_id,
        user_id=user_id,
        account_id=account_id,
        interaction_id=interaction_id,
        trace_id=trace_id
    )


def get_auth_context_ingestion(request: Request) -> RequestContext:
    """
    Extract auth context for INGESTION (mutating / write) routes.

    Use this for endpoints that persist data tied to an account anchor:
    /text/clean, /batch/process, /upload/init, /upload/complete.

    Behavior is identical to get_auth_context_polling() except that the
    X-Account-ID header is REQUIRED. Missing X-Account-ID raises HTTP 400
    BEFORE any business logic executes.

    Authentication strategy (same as polling variant):
    1. If Authorization header contains a Bearer token, verify it as an
       internal JWT (signature, issuer, audience, expiration). On any
       verification failure raise 401.
    2. If no JWT present and ALLOW_LEGACY_HEADER_AUTH=true, fall back to
       header-based auth (X-Tenant-ID, X-User-ID). On missing/invalid
       headers raise 400.
    3. If no JWT present and ALLOW_LEGACY_HEADER_AUTH=false, raise 401.

    Environment Variables:
    - ALLOW_LEGACY_HEADER_AUTH: "true" enables header fallback (default: false)
    - INTERNAL_JWT_SECRET: Required for JWT verification
    - INTERNAL_JWT_ISSUER: Expected JWT issuer (default: "eq-frontend")
    - INTERNAL_JWT_AUDIENCE: Expected JWT audience (default: "eq-backend")

    Args:
        request: FastAPI Request object

    Returns:
        RequestContext with validated identity fields. account_id is always
        a non-empty string (the X-Account-ID header value).

    Raises:
        HTTPException: 401 for JWT failures, 400 for missing X-Account-ID or
        invalid headers.
    """
    return _resolve_auth_context(request, require_account_id=True)


def get_auth_context_polling(request: Request) -> RequestContext:
    """
    Extract auth context for POLLING / read-only routes.

    Use this for endpoints that read but do NOT mutate account-anchored
    data, e.g. GET /upload/status/{job_id}. The handler is still expected
    to enforce tenant ownership on the resource it returns.

    Behavior is identical to get_auth_context_ingestion() except that the
    X-Account-ID header is OPTIONAL. When absent, the returned
    RequestContext.account_id is the empty string (sentinel). Callers MUST
    NOT use that field for any persist/write operation — the sentinel is
    intentionally invalid as a foreign key so accidental writes fail loudly.

    Args:
        request: FastAPI Request object

    Returns:
        RequestContext with validated identity fields. account_id may be ""
        (when X-Account-ID is absent) or the header value if supplied.

    Raises:
        HTTPException: 401 for JWT failures, 400 for invalid headers (other
        than X-Account-ID).
    """
    return _resolve_auth_context(request, require_account_id=False)


def _resolve_auth_context(
    request: Request,
    *,
    require_account_id: bool,
) -> RequestContext:
    """
    Shared implementation behind get_auth_context_ingestion / _polling.

    Performs JWT or legacy-header auth and either enforces or relaxes the
    X-Account-ID requirement based on `require_account_id`.

    Args:
        request: FastAPI Request object
        require_account_id: True for ingestion routes; False for polling.

    Returns:
        RequestContext with validated identity fields.

    Raises:
        HTTPException: 401 for JWT failures, 400 for header validation failures.
    """
    # Generate interaction_id for this request
    interaction_id = str(uuid.uuid4())

    # Check for JWT in Authorization header
    auth_header = request.headers.get("Authorization")
    token = extract_bearer_token(auth_header)

    if token:
        # JWT present - verify it
        return _extract_context_from_jwt(
            request,
            token,
            interaction_id,
            require_account_id=require_account_id,
        )

    # No JWT - check if legacy header auth is allowed
    allow_legacy = os.getenv("ALLOW_LEGACY_HEADER_AUTH", "false").lower() == "true"

    if not allow_legacy:
        # In production mode, JWT is required
        logger.warning(
            f"No JWT provided and legacy header auth is disabled. "
            f"interaction_id={interaction_id}"
        )
        raise HTTPException(
            status_code=401,
            detail="Authorization required: Bearer token expected"
        )

    # Legacy header auth is allowed - use strict validation
    logger.info(
        f"Using legacy header auth (ALLOW_LEGACY_HEADER_AUTH=true). "
        f"interaction_id={interaction_id}"
    )
    return get_validated_context(request, require_account_id=require_account_id)


def _extract_context_from_jwt(
    request: Request,
    token: str,
    interaction_id: str,
    *,
    require_account_id: bool = True,
) -> RequestContext:
    """
    Extract RequestContext from a verified JWT.

    Args:
        request: FastAPI Request object (for additional headers like trace_id)
        token: The JWT string (without Bearer prefix)
        interaction_id: Generated interaction ID for this request
        require_account_id: When True (default), missing X-Account-ID raises
            HTTP 400 (ingestion contract). When False, the missing header is
            allowed and the returned RequestContext.account_id is "".

    Returns:
        RequestContext with JWT-derived tenant_id and user_id. account_id is
        the X-Account-ID header value, or "" when require_account_id is False
        and the header is absent.

    Raises:
        HTTPException: 401 on JWT verification failure, 400 on missing
        X-Account-ID when require_account_id is True.
    """
    try:
        claims = verify_internal_jwt(token)
    except JWTVerificationError as e:
        logger.warning(
            f"JWT verification failed: {e.code}. "
            f"interaction_id={interaction_id}"
        )
        raise HTTPException(
            status_code=401,
            detail=e.message
        )

    # Extract trace_id from header or generate
    trace_id = _validate_trace_id(request, interaction_id)

    # Account anchor contract: required for ingestion (raises 400 when missing
    # or whitespace-only); relaxed to "" sentinel for polling/read-only routes.
    # Callers MUST NOT persist the "" sentinel.
    account_id = _require_or_relax_account_id_header(
        request, required=require_account_id
    )

    logger.info(
        f"JWT auth context: interaction_id={interaction_id}, "
        f"tenant_id={claims.tenant_id[:8]}..., user_id={claims.user_id[:20]}..., "
        f"account_id={account_id or '<polling-no-account>'}, trace_id={trace_id}"
    )

    return RequestContext(
        tenant_id=claims.tenant_id,
        user_id=claims.user_id,
        account_id=account_id,
        interaction_id=interaction_id,
        trace_id=trace_id,
        pg_user_id=claims.pg_user_id,
        user_name=claims.user_name,
        # This is the ONLY constructor that sets trusted_event_time=True:
        # identity was proven by a verified internal JWT. The legacy-header
        # path (get_validated_context) and the lenient path
        # (get_request_context) leave it at the False default so a
        # caller-supplied occurred_at is honored only from trusted callers.
        trusted_event_time=True,
    )


def _require_or_relax_account_id_header(request: Request, *, required: bool) -> str:
    """Resolve X-Account-ID per the ingestion-vs-polling contract.

    Centralizes the contract for the X-Account-ID header so the JWT path
    (`_extract_context_from_jwt`) and the legacy-header path
    (`get_validated_context`) stay in lockstep. Whitespace-only values are
    treated as missing — a whitespace string is functionally the same as
    absent for FK/account-anchor purposes and would otherwise propagate
    invalid identifiers to the DB layer.

    Args:
        request: FastAPI Request object
        required: True for ingestion routes (missing/whitespace raises 400);
            False for polling routes (missing returns "" sentinel).

    Returns:
        The trimmed header value when present and non-whitespace; "" when
        absent or whitespace-only AND required is False.

    Raises:
        HTTPException: 400 when required is True and the header is missing
        or whitespace-only.
    """
    header_account_id = request.headers.get("X-Account-ID")
    trimmed = header_account_id.strip() if header_account_id else ""
    if not trimmed:
        if required:
            raise HTTPException(
                status_code=400,
                detail="X-Account-ID header is required for this endpoint",
            )
        return ""
    return trimmed


def _validate_tenant_id(request: Request) -> str:
    """
    Validate X-Tenant-ID header is present and is a valid UUID.
    
    Args:
        request: FastAPI Request object
        
    Returns:
        Valid UUID string for tenant_id
        
    Raises:
        HTTPException: 400 if header is missing or invalid
    """
    tenant_id = request.headers.get("X-Tenant-ID")
    
    if not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID header is required"
        )
    
    if not _is_valid_uuid(tenant_id):
        raise HTTPException(
            status_code=400,
            detail="X-Tenant-ID must be a valid UUID"
        )
    
    return tenant_id


def _validate_user_id(request: Request) -> str:
    """
    Validate X-User-ID header is present and is a non-empty string.
    
    Args:
        request: FastAPI Request object
        
    Returns:
        Non-empty user identifier string
        
    Raises:
        HTTPException: 400 if header is missing or empty
    """
    user_id = request.headers.get("X-User-ID")
    
    if not user_id or not user_id.strip():
        raise HTTPException(
            status_code=400,
            detail="X-User-ID header is required"
        )
    
    return user_id


def _validate_trace_id(request: Request, interaction_id: str) -> str:
    """
    Validate X-Trace-Id header if provided, or generate a new one.
    
    Args:
        request: FastAPI Request object
        interaction_id: Current interaction ID for logging
        
    Returns:
        Valid UUID string for trace_id
        
    Raises:
        HTTPException: 400 if header is provided but invalid
    """
    trace_id = request.headers.get("X-Trace-Id")
    
    if trace_id:
        if not _is_valid_uuid(trace_id):
            raise HTTPException(
                status_code=400,
                detail="X-Trace-Id must be a valid UUID if provided"
            )
        logger.debug(f"Trace ID from header: {trace_id}")
        return trace_id
    
    # Generate new UUID v4 if not provided
    trace_id = str(uuid.uuid4())
    logger.debug(
        f"Generated new trace_id: {trace_id}. "
        f"interaction_id={interaction_id}"
    )
    return trace_id


def _extract_tenant_id(request: Request, interaction_id: str) -> str:
    """
    Extract tenant_id from request headers or environment with validation.
    
    Priority:
    1. X-Tenant-ID header
    2. MOCK_TENANT_ID environment variable
    3. Generate new UUID v4
    
    Args:
        request: FastAPI Request object
        interaction_id: Current interaction ID for logging
        
    Returns:
        Valid UUID v4 string for tenant_id
    """
    # Try request header first
    tenant_id = request.headers.get("X-Tenant-ID")
    
    if tenant_id:
        # Validate UUID format
        if _is_valid_uuid_v4(tenant_id):
            logger.debug(f"Tenant ID from header: {tenant_id}")
            return tenant_id
        else:
            logger.warning(
                f"Invalid tenant_id format in header: {tenant_id}. "
                f"interaction_id={interaction_id}. Falling back to environment."
            )
    
    # Try environment variable
    tenant_id = os.getenv("MOCK_TENANT_ID")
    
    if tenant_id:
        # Validate UUID format
        if _is_valid_uuid_v4(tenant_id):
            logger.debug(f"Tenant ID from environment: {tenant_id}")
            return tenant_id
        else:
            logger.warning(
                f"Invalid MOCK_TENANT_ID format in environment: {tenant_id}. "
                f"interaction_id={interaction_id}. Generating new UUID."
            )
    
    # Generate new UUID v4 as fallback
    tenant_id = str(uuid.uuid4())
    logger.info(
        f"Generated new tenant_id: {tenant_id}. "
        f"interaction_id={interaction_id}"
    )
    
    return tenant_id


def _extract_user_id(request: Request, interaction_id: str) -> str:
    """
    Extract user_id from request headers or environment.
    
    Priority:
    1. X-User-ID header
    2. MOCK_USER_ID environment variable
    3. Default to "system"
    
    Args:
        request: FastAPI Request object
        interaction_id: Current interaction ID for logging
        
    Returns:
        User identifier string
    """
    # Try request header first
    user_id = request.headers.get("X-User-ID")
    
    if user_id:
        logger.debug(f"User ID from header: {user_id}")
        return user_id
    
    # Try environment variable
    user_id = os.getenv("MOCK_USER_ID")
    
    if user_id:
        logger.debug(f"User ID from environment: {user_id}")
        return user_id
    
    # Default to "system"
    user_id = "system"
    logger.info(
        f"Using default user_id: {user_id}. "
        f"interaction_id={interaction_id}"
    )
    
    return user_id


def _extract_account_id(request: Request) -> Optional[str]:
    """
    Extract account_id from request headers.
    
    This is an optional field with no fallback - if not present in headers,
    it remains None.
    
    Args:
        request: FastAPI Request object
        
    Returns:
        Account identifier string or None
    """
    account_id = request.headers.get("X-Account-ID")
    
    if account_id:
        logger.debug(f"Account ID from header: {account_id}")
    
    return account_id


def _extract_trace_id(request: Request, interaction_id: str) -> str:
    """
    Extract trace_id from request headers or generate a new one.
    
    Priority:
    1. X-Trace-Id header (if valid UUID)
    2. Generate new UUID v4
    
    Args:
        request: FastAPI Request object
        interaction_id: Current interaction ID for logging
        
    Returns:
        Valid UUID v4 string for trace_id
    """
    # Try request header first
    trace_id = request.headers.get("X-Trace-Id")
    
    if trace_id:
        # Validate UUID format (accept any valid UUID, not just v4)
        if _is_valid_uuid(trace_id):
            logger.debug(f"Trace ID from header: {trace_id}")
            return trace_id
        else:
            logger.warning(
                f"Invalid trace_id format in header: {trace_id}. "
                f"interaction_id={interaction_id}. Generating new UUID."
            )
    
    # Generate new UUID v4 as fallback
    trace_id = str(uuid.uuid4())
    logger.debug(
        f"Generated new trace_id: {trace_id}. "
        f"interaction_id={interaction_id}"
    )
    
    return trace_id


def _is_valid_uuid(value: str) -> bool:
    """
    Validate that a string is a valid UUID (any version).
    
    Args:
        value: String to validate
        
    Returns:
        True if valid UUID, False otherwise
    """
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def _is_valid_uuid_v4(value: str) -> bool:
    """
    Validate that a string is a valid UUID v4.
    
    Args:
        value: String to validate
        
    Returns:
        True if valid UUID v4, False otherwise
    """
    try:
        parsed_uuid = uuid.UUID(value, version=4)
        # Verify it's actually version 4
        return parsed_uuid.version == 4
    except (ValueError, AttributeError):
        return False
