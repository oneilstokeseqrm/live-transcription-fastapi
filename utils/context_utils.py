"""
Context Extraction Utilities

This module provides utilities for extracting tenant, user, and account identity
from request headers with fallback to environment variables.

Two extraction modes are available:

1. get_request_context() - Lenient extraction with fallbacks (backward compatible)
   - Falls back to environment variables and defaults
   - Never raises exceptions
   
2. get_validated_context() - Strict validation for production endpoints
   - Requires X-Tenant-ID (valid UUID)
   - Requires X-User-ID (non-empty string)
   - Optional X-Trace-Id (valid UUID if provided, generated if missing)
   - Raises HTTPException 400 on validation failure
"""

import os
import uuid
import logging
from fastapi import Request, HTTPException
from typing import Optional
from models.request_context import RequestContext

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
    
    # Extract account_id (optional, no default)
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
        account_id=account_id,
        interaction_id=interaction_id,
        trace_id=trace_id
    )


def get_validated_context(request: Request) -> RequestContext:
    """
    Extract and validate context from request headers with strict validation.
    
    This function enforces required headers and validates their formats.
    Use this for production endpoints that require proper identity context.
    
    Required Headers:
    - X-Tenant-ID: Must be a valid UUID
    - X-User-ID: Must be a non-empty string
    
    Optional Headers:
    - X-Trace-Id: If provided, must be a valid UUID; generated if missing
    - X-Account-ID: Optional, no validation
    
    Args:
        request: FastAPI Request object containing headers
        
    Returns:
        RequestContext with all identity fields populated
        
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
    
    # Extract account_id (optional, no validation)
    account_id = request.headers.get("X-Account-ID")
    
    # Log extracted context
    logger.info(
        f"Validated context: interaction_id={interaction_id}, "
        f"tenant_id={tenant_id}, user_id={user_id}, "
        f"account_id={account_id or 'None'}, trace_id={trace_id}"
    )
    
    return RequestContext(
        tenant_id=tenant_id,
        user_id=user_id,
        account_id=account_id,
        interaction_id=interaction_id,
        trace_id=trace_id
    )


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
