"""
Context Extraction Utilities

This module provides utilities for extracting tenant, user, and account identity
from request headers with fallback to environment variables.

The extraction follows a priority chain:
1. Request headers (X-Tenant-ID, X-User-ID, X-Account-ID)
2. Environment variables (MOCK_TENANT_ID, MOCK_USER_ID)
3. Generated/default values
"""

import os
import uuid
import logging
from fastapi import Request
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
    1. Request headers (X-Tenant-ID, X-User-ID, X-Account-ID)
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
    
    # Log extracted context
    logger.info(
        f"Context extracted: interaction_id={interaction_id}, "
        f"tenant_id={tenant_id}, user_id={user_id}, "
        f"account_id={account_id or 'None'}"
    )
    
    return RequestContext(
        tenant_id=tenant_id,
        user_id=user_id,
        account_id=account_id,
        interaction_id=interaction_id
    )


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
