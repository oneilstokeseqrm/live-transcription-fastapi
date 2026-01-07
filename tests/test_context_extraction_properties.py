"""
Property-Based Tests for Context Extraction

This module contains two sets of property tests:

1. Lenient Context Extraction (get_request_context)
   Feature: event-driven-architecture, Property 2: Context Extraction Fallback Chain
   Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11

2. Strict Context Validation (get_validated_context)
   Feature: unified-ingestion-upgrade
   - Property 1: Tenant ID UUID Validation
   - Property 2: User ID Required Validation
   - Property 3: Trace ID Generation and Preservation
   Validates: Requirements 1.1-1.7, 8.1-8.6
"""

import os
import uuid
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from hypothesis import given, strategies as st, settings, assume
from utils.context_utils import get_request_context, get_validated_context
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


@given(st.text(min_size=1, max_size=100, alphabet=st.characters(blacklist_characters='\x00')))
@settings(max_examples=50)
def test_user_id_fallback_to_environment(user_id):
    """
    Property: User ID falls back to environment when header missing.
    
    When X-User-ID header is not present, MOCK_USER_ID environment
    variable should be used if available.
    
    Feature: event-driven-architecture, Property 2: Context Extraction Fallback Chain
    Validates: Requirements 2.5, 2.6
    """
    # Skip null bytes which can't be set as environment variables
    assume('\x00' not in user_id)
    
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


# =============================================================================
# STRICT VALIDATION TESTS (get_validated_context)
# Feature: unified-ingestion-upgrade
# Properties 1, 2, 3: Context Extraction Validation
# Validates: Requirements 1.1-1.7, 8.1-8.6
# =============================================================================


def create_mock_request_with_trace(
    tenant_id=None,
    user_id=None,
    account_id=None,
    trace_id=None
):
    """
    Create a mock FastAPI Request object with specified headers including trace_id.
    
    Args:
        tenant_id: Value for X-Tenant-ID header (None to omit)
        user_id: Value for X-User-ID header (None to omit)
        account_id: Value for X-Account-ID header (None to omit)
        trace_id: Value for X-Trace-Id header (None to omit)
        
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
    if trace_id is not None:
        headers["X-Trace-Id"] = trace_id
    
    request = Mock()
    request.headers = Mock()
    request.headers.get = lambda key, default=None: headers.get(key, default)
    
    return request


@st.composite
def valid_uuid_string(draw):
    """Generate a valid UUID string (any version)."""
    return str(uuid.uuid4())


@st.composite
def non_uuid_string(draw):
    """Generate strings that are definitely not valid UUIDs."""
    return draw(st.one_of(
        st.just("not-a-uuid"),
        st.just("12345"),
        st.just("invalid-uuid-format"),
        st.just("abc-def-ghi"),
        st.text(min_size=1, max_size=10, alphabet=st.characters(whitelist_categories=('L',))),
    ))


@st.composite
def whitespace_only_string(draw):
    """Generate strings containing only whitespace characters."""
    return draw(st.one_of(
        st.just(""),
        st.just(" "),
        st.just("  "),
        st.just("\t"),
        st.just("\n"),
        st.just("   \t\n  "),
    ))


@st.composite
def non_empty_string(draw):
    """Generate non-empty strings with at least one non-whitespace character."""
    # Generate a string with at least one printable non-whitespace character
    base = draw(st.text(
        min_size=1, 
        max_size=100,
        alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S'))
    ))
    assume(base.strip() != "")
    return base


# -----------------------------------------------------------------------------
# Property 1: Tenant ID UUID Validation
# For any string value provided as X-Tenant-ID header, the context extractor
# SHALL accept it if and only if it is a valid UUID format, and SHALL reject
# with HTTP 400 otherwise.
# Validates: Requirements 1.1, 1.3, 8.1, 8.6
# -----------------------------------------------------------------------------

@given(valid_uuid_string())
@settings(max_examples=100)
def test_property1_valid_uuid_tenant_id_accepted(tenant_id):
    """
    Feature: unified-ingestion-upgrade, Property 1: Tenant ID UUID Validation
    
    For any valid UUID string provided as X-Tenant-ID, the context extractor
    SHALL accept it and return a context with that tenant_id.
    
    **Validates: Requirements 1.1, 1.3, 8.1**
    """
    request = create_mock_request_with_trace(
        tenant_id=tenant_id,
        user_id="test-user"
    )
    
    context = get_validated_context(request)
    
    assert context.tenant_id == tenant_id, \
        "Valid UUID tenant_id should be accepted and preserved"
    assert isinstance(context, RequestContext), \
        "Should return RequestContext object"


@given(non_uuid_string())
@settings(max_examples=100)
def test_property1_invalid_uuid_tenant_id_rejected(invalid_tenant_id):
    """
    Feature: unified-ingestion-upgrade, Property 1: Tenant ID UUID Validation
    
    For any invalid UUID string provided as X-Tenant-ID, the context extractor
    SHALL reject with HTTP 400 and message "X-Tenant-ID must be a valid UUID".
    
    **Validates: Requirements 1.7, 8.6**
    """
    # Ensure the string is not accidentally a valid UUID
    try:
        uuid.UUID(invalid_tenant_id)
        assume(False)  # Skip if it's actually valid
    except (ValueError, AttributeError):
        pass  # Good, it's invalid
    
    request = create_mock_request_with_trace(
        tenant_id=invalid_tenant_id,
        user_id="test-user"
    )
    
    with pytest.raises(HTTPException) as exc_info:
        get_validated_context(request)
    
    assert exc_info.value.status_code == 400, \
        "Should return HTTP 400 for invalid UUID"
    assert exc_info.value.detail == "X-Tenant-ID must be a valid UUID", \
        "Should return correct error message"


def test_property1_missing_tenant_id_rejected():
    """
    Feature: unified-ingestion-upgrade, Property 1: Tenant ID UUID Validation
    
    When X-Tenant-ID header is missing, the context extractor SHALL reject
    with HTTP 400 and message "X-Tenant-ID header is required".
    
    **Validates: Requirements 1.7, 8.4**
    """
    request = create_mock_request_with_trace(
        tenant_id=None,
        user_id="test-user"
    )
    
    with pytest.raises(HTTPException) as exc_info:
        get_validated_context(request)
    
    assert exc_info.value.status_code == 400, \
        "Should return HTTP 400 for missing tenant_id"
    assert exc_info.value.detail == "X-Tenant-ID header is required", \
        "Should return correct error message"


# -----------------------------------------------------------------------------
# Property 2: User ID Required Validation
# For any request to protected endpoints, the context extractor SHALL accept
# the X-User-ID header if and only if it is a non-empty string (after trimming
# whitespace), and SHALL reject with HTTP 400 otherwise.
# Validates: Requirements 1.2, 1.4, 8.2, 8.5
# -----------------------------------------------------------------------------

@given(non_empty_string())
@settings(max_examples=100)
def test_property2_non_empty_user_id_accepted(user_id):
    """
    Feature: unified-ingestion-upgrade, Property 2: User ID Required Validation
    
    For any non-empty string (after trimming) provided as X-User-ID, the context
    extractor SHALL accept it and return a context with that user_id.
    
    **Validates: Requirements 1.2, 1.4, 8.2**
    """
    tenant_id = str(uuid.uuid4())
    request = create_mock_request_with_trace(
        tenant_id=tenant_id,
        user_id=user_id
    )
    
    context = get_validated_context(request)
    
    assert context.user_id == user_id, \
        "Non-empty user_id should be accepted and preserved"
    assert isinstance(context, RequestContext), \
        "Should return RequestContext object"


@given(whitespace_only_string())
@settings(max_examples=50)
def test_property2_whitespace_only_user_id_rejected(whitespace_user_id):
    """
    Feature: unified-ingestion-upgrade, Property 2: User ID Required Validation
    
    For any string composed entirely of whitespace provided as X-User-ID,
    the context extractor SHALL reject with HTTP 400.
    
    **Validates: Requirements 1.7, 8.5**
    """
    tenant_id = str(uuid.uuid4())
    request = create_mock_request_with_trace(
        tenant_id=tenant_id,
        user_id=whitespace_user_id
    )
    
    with pytest.raises(HTTPException) as exc_info:
        get_validated_context(request)
    
    assert exc_info.value.status_code == 400, \
        "Should return HTTP 400 for whitespace-only user_id"
    assert exc_info.value.detail == "X-User-ID header is required", \
        "Should return correct error message"


def test_property2_missing_user_id_rejected():
    """
    Feature: unified-ingestion-upgrade, Property 2: User ID Required Validation
    
    When X-User-ID header is missing, the context extractor SHALL reject
    with HTTP 400 and message "X-User-ID header is required".
    
    **Validates: Requirements 1.7, 8.5**
    """
    tenant_id = str(uuid.uuid4())
    request = create_mock_request_with_trace(
        tenant_id=tenant_id,
        user_id=None
    )
    
    with pytest.raises(HTTPException) as exc_info:
        get_validated_context(request)
    
    assert exc_info.value.status_code == 400, \
        "Should return HTTP 400 for missing user_id"
    assert exc_info.value.detail == "X-User-ID header is required", \
        "Should return correct error message"


# -----------------------------------------------------------------------------
# Property 3: Trace ID Generation and Preservation
# For any request, if X-Trace-Id header is provided and is a valid UUID, the
# resulting context SHALL contain that exact trace_id; if not provided, the
# context SHALL contain a newly generated valid UUID v4.
# Validates: Requirements 1.5, 1.6, 8.3
# -----------------------------------------------------------------------------

@given(valid_uuid_string())
@settings(max_examples=100)
def test_property3_valid_trace_id_preserved(trace_id):
    """
    Feature: unified-ingestion-upgrade, Property 3: Trace ID Generation and Preservation
    
    For any valid UUID provided as X-Trace-Id, the context extractor SHALL
    preserve that exact trace_id in the returned context.
    
    **Validates: Requirements 1.6, 8.3**
    """
    tenant_id = str(uuid.uuid4())
    request = create_mock_request_with_trace(
        tenant_id=tenant_id,
        user_id="test-user",
        trace_id=trace_id
    )
    
    context = get_validated_context(request)
    
    assert context.trace_id == trace_id, \
        "Valid trace_id should be preserved exactly"


def test_property3_missing_trace_id_generates_new_uuid():
    """
    Feature: unified-ingestion-upgrade, Property 3: Trace ID Generation and Preservation
    
    When X-Trace-Id header is not provided, the context extractor SHALL
    generate a new valid UUID v4 for trace_id.
    
    **Validates: Requirements 1.5**
    """
    tenant_id = str(uuid.uuid4())
    request = create_mock_request_with_trace(
        tenant_id=tenant_id,
        user_id="test-user",
        trace_id=None
    )
    
    context = get_validated_context(request)
    
    assert context.trace_id is not None, \
        "trace_id should be generated when not provided"
    
    # Verify it's a valid UUID
    parsed_uuid = uuid.UUID(context.trace_id)
    assert parsed_uuid is not None, \
        "Generated trace_id should be a valid UUID"


@given(non_uuid_string())
@settings(max_examples=100)
def test_property3_invalid_trace_id_rejected(invalid_trace_id):
    """
    Feature: unified-ingestion-upgrade, Property 3: Trace ID Generation and Preservation
    
    For any invalid UUID string provided as X-Trace-Id, the context extractor
    SHALL reject with HTTP 400 and message "X-Trace-Id must be a valid UUID if provided".
    
    **Validates: Requirements 1.6, 8.3**
    """
    # Ensure the string is not accidentally a valid UUID
    try:
        uuid.UUID(invalid_trace_id)
        assume(False)  # Skip if it's actually valid
    except (ValueError, AttributeError):
        pass  # Good, it's invalid
    
    tenant_id = str(uuid.uuid4())
    request = create_mock_request_with_trace(
        tenant_id=tenant_id,
        user_id="test-user",
        trace_id=invalid_trace_id
    )
    
    with pytest.raises(HTTPException) as exc_info:
        get_validated_context(request)
    
    assert exc_info.value.status_code == 400, \
        "Should return HTTP 400 for invalid trace_id"
    assert exc_info.value.detail == "X-Trace-Id must be a valid UUID if provided", \
        "Should return correct error message"


def test_property3_trace_id_uniqueness_when_generated():
    """
    Feature: unified-ingestion-upgrade, Property 3: Trace ID Generation and Preservation
    
    When trace_id is generated (not provided), each call should produce
    a unique trace_id.
    
    **Validates: Requirements 1.5**
    """
    tenant_id = str(uuid.uuid4())
    
    # Make multiple requests without trace_id
    trace_ids = set()
    for _ in range(10):
        request = create_mock_request_with_trace(
            tenant_id=tenant_id,
            user_id="test-user",
            trace_id=None
        )
        context = get_validated_context(request)
        trace_ids.add(context.trace_id)
    
    assert len(trace_ids) == 10, \
        "Each generated trace_id should be unique"


# -----------------------------------------------------------------------------
# Combined Validation Tests
# Test that all validations work together correctly
# -----------------------------------------------------------------------------

@given(valid_uuid_string(), non_empty_string(), valid_uuid_string())
@settings(max_examples=100)
def test_validated_context_with_all_valid_headers(tenant_id, user_id, trace_id):
    """
    Feature: unified-ingestion-upgrade, Properties 1, 2, 3
    
    For any valid combination of headers, the context extractor SHALL return
    a valid RequestContext with all fields properly populated.
    
    **Validates: Requirements 1.1-1.6, 8.1-8.3**
    """
    request = create_mock_request_with_trace(
        tenant_id=tenant_id,
        user_id=user_id,
        trace_id=trace_id
    )
    
    context = get_validated_context(request)
    
    assert isinstance(context, RequestContext), \
        "Should return RequestContext object"
    assert context.tenant_id == tenant_id, \
        "tenant_id should match header"
    assert context.user_id == user_id, \
        "user_id should match header"
    assert context.trace_id == trace_id, \
        "trace_id should match header"
    assert context.interaction_id is not None, \
        "interaction_id should be generated"
    
    # Verify interaction_id is a valid UUID
    uuid.UUID(context.interaction_id)


def test_validated_context_interaction_id_always_unique():
    """
    Feature: unified-ingestion-upgrade, Properties 1, 2, 3
    
    Each call to get_validated_context should generate a unique interaction_id.
    
    **Validates: Requirements 1.1-1.6**
    """
    tenant_id = str(uuid.uuid4())
    
    interaction_ids = set()
    for _ in range(10):
        request = create_mock_request_with_trace(
            tenant_id=tenant_id,
            user_id="test-user"
        )
        context = get_validated_context(request)
        interaction_ids.add(context.interaction_id)
    
    assert len(interaction_ids) == 10, \
        "Each call should generate a unique interaction_id"
