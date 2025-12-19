"""
Property-Based Tests for Context Extraction Fallback Chain

Feature: event-driven-architecture, Property 2: Context Extraction Fallback Chain
Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11

This module tests that context extraction follows the correct fallback chain
and always returns valid context regardless of input combinations.
"""

import os
import uuid
from unittest.mock import Mock
from hypothesis import given, strategies as st, settings, assume
from utils.context_utils import get_request_context
from models.request_context import RequestContext


def create_mock_request(
    tenant_id=None,
    user_id=None,
    account_id=None
):
    """
    Create a mock FastAPI Request object with specified headers.
    
    Args:
        tenant_id: Value for X-Tenant-ID header (None to omit)
        user_id: Value for X-User-ID header (None to omit)
        account_id: Value for X-Account-ID header (None to omit)
        
    Returns:
        Mock Request object
    """
    headers = {}
    
    if tenant_id is not None:
        headers["X-Tenant-ID"] = tenant_id
    if user_id is not None:
        headers["X-User-ID"] = user_id
    if account_id is not None:
        headers["X-Account-ID"] = account_id
    
    request = Mock()
    request.headers = Mock()
    request.headers.get = lambda key, default=None: headers.get(key, default)
    
    return request


@st.composite
def valid_uuid_v4_string(draw):
    """Generate a valid UUID v4 string."""
    return str(uuid.uuid4())


@st.composite
def invalid_uuid_string(draw):
    """Generate an invalid UUID string."""
    # Generate strings that are not valid UUIDs
    return draw(st.one_of(
        st.text(min_size=1, max_size=20).filter(lambda x: x != ""),
        st.just("not-a-uuid"),
        st.just("12345"),
        st.just("invalid-uuid-format")
    ))


@given(
    tenant_header=st.one_of(st.none(), valid_uuid_v4_string()),
    user_header=st.one_of(st.none(), st.text(min_size=1, max_size=100)),
    account_header=st.one_of(st.none(), st.text(min_size=1, max_size=100))
)
@settings(max_examples=100)
def test_context_extraction_always_returns_valid_context(
    tenant_header,
    user_header,
    account_header
):
    """
    Property: Context extraction always returns valid context.
    
    For any combination of request headers and environment variables,
    the context extraction should follow the correct fallback chain:
    headers → environment variables → defaults, and always return a valid
    RequestContext with non-null tenant_id and user_id.
    
    Feature: event-driven-architecture, Property 2: Context Extraction Fallback Chain
    Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11
    """
    # Create mock request with headers
    request = create_mock_request(
        tenant_id=tenant_header,
        user_id=user_header,
        account_id=account_header
    )
    
    # Extract context
    context = get_request_context(request)
    
    # Verify context is valid
    assert isinstance(context, RequestContext), "Must return RequestContext object"
    
    # Verify tenant_id is always present and valid UUID v4
    assert context.tenant_id is not None, "tenant_id must not be None"
    assert isinstance(context.tenant_id, str), "tenant_id must be a string"
    tenant_uuid = uuid.UUID(context.tenant_id, version=4)
    assert tenant_uuid.version == 4, "tenant_id must be valid UUID v4"
    
    # Verify user_id is always present
    assert context.user_id is not None, "user_id must not be None"
    assert isinstance(context.user_id, str), "user_id must be a string"
    assert len(context.user_id) > 0, "user_id must not be empty"
    
    # Verify interaction_id is always present and valid UUID v4
    assert context.interaction_id is not None, "interaction_id must not be None"
    assert isinstance(context.interaction_id, str), "interaction_id must be a string"
    interaction_uuid = uuid.UUID(context.interaction_id, version=4)
    assert interaction_uuid.version == 4, "interaction_id must be valid UUID v4"
    
    # Verify account_id is string or None
    assert context.account_id is None or isinstance(context.account_id, str), \
        "account_id must be string or None"


@given(valid_uuid_v4_string())
@settings(max_examples=50)
def test_tenant_id_from_header_takes_precedence(tenant_id):
    """
    Property: Tenant ID from header takes precedence over environment.
    
    When X-Tenant-ID header is present with a valid UUID v4, it should be
    used regardless of MOCK_TENANT_ID environment variable.
    
    Feature: event-driven-architecture, Property 2: Context Extraction Fallback Chain
    Validates: Requirements 2.1, 2.2
    """
    # Set environment variable
    os.environ["MOCK_TENANT_ID"] = str(uuid.uuid4())
    
    # Create request with header
    request = create_mock_request(tenant_id=tenant_id)
    
    # Extract context
    context = get_request_context(request)
    
    # Verify header value was used
    assert context.tenant_id == tenant_id, \
        "Header value should take precedence over environment"
    
    # Cleanup
    if "MOCK_TENANT_ID" in os.environ:
        del os.environ["MOCK_TENANT_ID"]


@given(st.text(min_size=1, max_size=100))
@settings(max_examples=50)
def test_user_id_from_header_takes_precedence(user_id):
    """
    Property: User ID from header takes precedence over environment.
    
    When X-User-ID header is present, it should be used regardless of
    MOCK_USER_ID environment variable.
    
    Feature: event-driven-architecture, Property 2: Context Extraction Fallback Chain
    Validates: Requirements 2.4, 2.5
    """
    # Set environment variable
    os.environ["MOCK_USER_ID"] = "env-user"
    
    # Create request with header
    request = create_mock_request(user_id=user_id)
    
    # Extract context
    context = get_request_context(request)
    
    # Verify header value was used
    assert context.user_id == user_id, \
        "Header value should take precedence over environment"
    
    # Cleanup
    if "MOCK_USER_ID" in os.environ:
        del os.environ["MOCK_USER_ID"]


@given(valid_uuid_v4_string())
@settings(max_examples=50)
def test_tenant_id_fallback_to_environment(tenant_id):
    """
    Property: Tenant ID falls back to environment when header missing.
    
    When X-Tenant-ID header is not present, MOCK_TENANT_ID environment
    variable should be used if available.
    
    Feature: event-driven-architecture, Property 2: Context Extraction Fallback Chain
    Validates: Requirements 2.2, 2.3
    """
    # Set environment variable
    os.environ["MOCK_TENANT_ID"] = tenant_id
    
    # Create request without tenant header
    request = create_mock_request(tenant_id=None)
    
    # Extract context
    context = get_request_context(request)
    
    # Verify environment value was used
    assert context.tenant_id == tenant_id, \
        "Should use environment variable when header missing"
    
    # Cleanup
    if "MOCK_TENANT_ID" in os.environ:
        del os.environ["MOCK_TENANT_ID"]


@given(st.text(min_size=1, max_size=100))
@settings(max_examples=50)
def test_user_id_fallback_to_environment(user_id):
    """
    Property: User ID falls back to environment when header missing.
    
    When X-User-ID header is not present, MOCK_USER_ID environment
    variable should be used if available.
    
    Feature: event-driven-architecture, Property 2: Context Extraction Fallback Chain
    Validates: Requirements 2.5, 2.6
    """
    # Set environment variable
    os.environ["MOCK_USER_ID"] = user_id
    
    # Create request without user header
    request = create_mock_request(user_id=None)
    
    # Extract context
    context = get_request_context(request)
    
    # Verify environment value was used
    assert context.user_id == user_id, \
        "Should use environment variable when header missing"
    
    # Cleanup
    if "MOCK_USER_ID" in os.environ:
        del os.environ["MOCK_USER_ID"]


def test_tenant_id_generated_when_no_header_or_env():
    """
    Property: Tenant ID is generated when header and environment missing.
    
    When neither X-Tenant-ID header nor MOCK_TENANT_ID environment variable
    is present, a new UUID v4 should be generated.
    
    Feature: event-driven-architecture, Property 2: Context Extraction Fallback Chain
    Validates: Requirements 2.3
    """
    # Clear environment variable
    if "MOCK_TENANT_ID" in os.environ:
        del os.environ["MOCK_TENANT_ID"]
    
    # Create request without tenant header
    request = create_mock_request(tenant_id=None)
    
    # Extract context
    context = get_request_context(request)
    
    # Verify a valid UUID v4 was generated
    assert context.tenant_id is not None
    tenant_uuid = uuid.UUID(context.tenant_id, version=4)
    assert tenant_uuid.version == 4


def test_user_id_defaults_to_system_when_no_header_or_env():
    """
    Property: User ID defaults to "system" when header and environment missing.
    
    When neither X-User-ID header nor MOCK_USER_ID environment variable
    is present, user_id should default to "system".
    
    Feature: event-driven-architecture, Property 2: Context Extraction Fallback Chain
    Validates: Requirements 2.6
    """
    # Clear environment variable
    if "MOCK_USER_ID" in os.environ:
        del os.environ["MOCK_USER_ID"]
    
    # Create request without user header
    request = create_mock_request(user_id=None)
    
    # Extract context
    context = get_request_context(request)
    
    # Verify default value
    assert context.user_id == "system", \
        "Should default to 'system' when header and environment missing"


@given(invalid_uuid_string())
@settings(max_examples=50)
def test_invalid_tenant_id_triggers_fallback(invalid_uuid):
    """
    Property: Invalid tenant ID format triggers fallback chain.
    
    When X-Tenant-ID header contains an invalid UUID format, the system
    should log a warning and fall back to environment or generate new UUID.
    
    Feature: event-driven-architecture, Property 2: Context Extraction Fallback Chain
    Validates: Requirements 2.9, 2.10
    """
    # Ensure the string is not accidentally a valid UUID
    assume(invalid_uuid != "")
    try:
        uuid.UUID(invalid_uuid, version=4)
        assume(False)  # Skip if it's actually valid
    except (ValueError, AttributeError):
        pass  # Good, it's invalid
    
    # Clear environment variable
    if "MOCK_TENANT_ID" in os.environ:
        del os.environ["MOCK_TENANT_ID"]
    
    # Create request with invalid tenant header
    request = create_mock_request(tenant_id=invalid_uuid)
    
    # Extract context
    context = get_request_context(request)
    
    # Verify a valid UUID v4 was generated (fallback)
    assert context.tenant_id != invalid_uuid, \
        "Should not use invalid UUID from header"
    tenant_uuid = uuid.UUID(context.tenant_id, version=4)
    assert tenant_uuid.version == 4, \
        "Should generate valid UUID v4 when header is invalid"


@given(st.text(min_size=1, max_size=100))
@settings(max_examples=50)
def test_account_id_from_header_or_none(account_id):
    """
    Property: Account ID comes from header or is None.
    
    Account ID should be extracted from X-Account-ID header if present,
    otherwise it should be None. There is no fallback to environment.
    
    Feature: event-driven-architecture, Property 2: Context Extraction Fallback Chain
    Validates: Requirements 2.7, 2.8
    """
    # Create request with account header
    request = create_mock_request(account_id=account_id)
    
    # Extract context
    context = get_request_context(request)
    
    # Verify header value was used
    assert context.account_id == account_id, \
        "Should use header value when present"
    
    # Test without header
    request_no_account = create_mock_request(account_id=None)
    context_no_account = get_request_context(request_no_account)
    
    # Verify None when header missing
    assert context_no_account.account_id is None, \
        "Should be None when header missing"


def test_interaction_id_always_unique():
    """
    Property: Interaction ID is always unique for each request.
    
    Each call to get_request_context should generate a new unique
    interaction_id, even with identical headers.
    
    Feature: event-driven-architecture, Property 2: Context Extraction Fallback Chain
    Validates: Requirements 2.11
    """
    # Create same request multiple times
    request = create_mock_request()
    
    # Extract context multiple times
    context1 = get_request_context(request)
    context2 = get_request_context(request)
    
    # Verify interaction_ids are different
    assert context1.interaction_id != context2.interaction_id, \
        "Each request should have unique interaction_id"
    
    # Verify both are valid UUID v4
    uuid.UUID(context1.interaction_id, version=4)
    uuid.UUID(context2.interaction_id, version=4)
