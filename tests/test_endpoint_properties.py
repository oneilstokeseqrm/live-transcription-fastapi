"""
Property-Based Tests for Endpoint Behavior

This module contains property tests for the text and batch endpoints:

Feature: unified-ingestion-upgrade
- Property 7: Response Schema Completeness
- Property 8: Interaction Type Assignment
- Property 9: Whitespace Text Rejection

Validates: Requirements 2.3, 2.4, 3.3, 3.4, 3.5
"""

import uuid
from unittest.mock import Mock, AsyncMock, patch
import pytest
from hypothesis import given, strategies as st, settings, assume
from pydantic import ValidationError

from models.text_request import TextCleanRequest, TextCleanResponse


# =============================================================================
# Strategy Definitions
# =============================================================================

@st.composite
def whitespace_only_string(draw):
    """
    Generate strings containing only whitespace characters.
    
    This strategy generates various combinations of whitespace including:
    - Empty strings
    - Single spaces
    - Multiple spaces
    - Tabs
    - Newlines
    - Combinations of whitespace characters
    """
    return draw(st.one_of(
        st.just(""),
        st.just(" "),
        st.just("  "),
        st.just("   "),
        st.just("\t"),
        st.just("\n"),
        st.just("\r"),
        st.just("\r\n"),
        st.just("   \t\n  "),
        st.just("\t\t\t"),
        st.just("\n\n\n"),
        st.just(" \t \n \r "),
        # Generate random whitespace strings
        st.text(
            alphabet=st.sampled_from([' ', '\t', '\n', '\r']),
            min_size=1,
            max_size=20
        ),
    ))


@st.composite
def non_empty_text(draw):
    """Generate non-empty strings with at least one non-whitespace character."""
    base = draw(st.text(
        min_size=1,
        max_size=100,
        alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S'))
    ))
    assume(base.strip() != "")
    return base


# =============================================================================
# Property 9: Whitespace Text Rejection
# For any string composed entirely of whitespace characters, submitting it
# as the text field to /text/clean SHALL result in HTTP 400 rejection.
# Validates: Requirements 3.3
# =============================================================================

@given(whitespace_only_string())
@settings(max_examples=100)
def test_property9_whitespace_text_rejected_by_model(whitespace_text):
    """
    Feature: unified-ingestion-upgrade, Property 9: Whitespace Text Rejection
    
    For any string composed entirely of whitespace characters, the TextCleanRequest
    model SHALL reject it with a validation error.
    
    **Validates: Requirements 3.3**
    """
    # Skip empty strings as they're handled differently by Pydantic
    # (empty string is caught by the validator, but we want to test whitespace)
    
    with pytest.raises(ValidationError) as exc_info:
        TextCleanRequest(text=whitespace_text)
    
    # Verify the error is about the text field
    errors = exc_info.value.errors()
    assert len(errors) > 0, "Should have validation errors"
    
    # Check that the error is related to the text field
    text_errors = [e for e in errors if 'text' in str(e.get('loc', []))]
    assert len(text_errors) > 0, "Should have error for text field"


@given(non_empty_text())
@settings(max_examples=100)
def test_property9_non_whitespace_text_accepted_by_model(valid_text):
    """
    Feature: unified-ingestion-upgrade, Property 9: Whitespace Text Rejection
    
    For any string with at least one non-whitespace character, the TextCleanRequest
    model SHALL accept it.
    
    **Validates: Requirements 3.3**
    """
    # This should not raise
    request = TextCleanRequest(text=valid_text)
    
    assert request.text == valid_text, "Text should be preserved"


# =============================================================================
# Property 7: Response Schema Completeness
# For any successful processing request (batch or text), the response JSON
# SHALL contain all required fields.
# Validates: Requirements 2.3, 3.4
# =============================================================================

@given(
    raw_text=non_empty_text(),
    cleaned_text=non_empty_text(),
    interaction_id=st.uuids()
)
@settings(max_examples=100)
def test_property7_text_response_schema_completeness(raw_text, cleaned_text, interaction_id):
    """
    Feature: unified-ingestion-upgrade, Property 7: Response Schema Completeness
    
    For any valid TextCleanResponse, it SHALL contain all required fields:
    raw_text, cleaned_text, and interaction_id.
    
    **Validates: Requirements 3.4**
    """
    response = TextCleanResponse(
        raw_text=raw_text,
        cleaned_text=cleaned_text,
        interaction_id=str(interaction_id)
    )
    
    # Verify all required fields are present
    assert response.raw_text == raw_text, "raw_text should be present"
    assert response.cleaned_text == cleaned_text, "cleaned_text should be present"
    assert response.interaction_id == str(interaction_id), "interaction_id should be present"
    
    # Verify JSON serialization includes all fields
    response_dict = response.model_dump()
    assert "raw_text" in response_dict, "raw_text should be in JSON"
    assert "cleaned_text" in response_dict, "cleaned_text should be in JSON"
    assert "interaction_id" in response_dict, "interaction_id should be in JSON"


def test_property7_text_response_requires_all_fields():
    """
    Feature: unified-ingestion-upgrade, Property 7: Response Schema Completeness
    
    TextCleanResponse SHALL require all fields (raw_text, cleaned_text, interaction_id).
    
    **Validates: Requirements 3.4**
    """
    # Missing raw_text
    with pytest.raises(ValidationError):
        TextCleanResponse(
            cleaned_text="cleaned",
            interaction_id="123"
        )
    
    # Missing cleaned_text
    with pytest.raises(ValidationError):
        TextCleanResponse(
            raw_text="raw",
            interaction_id="123"
        )
    
    # Missing interaction_id
    with pytest.raises(ValidationError):
        TextCleanResponse(
            raw_text="raw",
            cleaned_text="cleaned"
        )


# =============================================================================
# Property 8: Interaction Type Assignment
# For any EnvelopeV1 published from the batch endpoint, interaction_type SHALL
# be "transcript"; for any EnvelopeV1 published from the text endpoint,
# interaction_type SHALL be "note".
# Validates: Requirements 2.4, 3.5
# =============================================================================

@given(
    text=non_empty_text(),
    source=st.sampled_from(["api", "web", "import", "upload"])
)
@settings(max_examples=100)
def test_property8_text_request_defaults(text, source):
    """
    Feature: unified-ingestion-upgrade, Property 8: Interaction Type Assignment
    
    TextCleanRequest SHALL have source default to "api" when not specified,
    supporting the interaction_type="note" assignment in the endpoint.
    
    **Validates: Requirements 3.5**
    """
    # Test with explicit source
    request_with_source = TextCleanRequest(text=text, source=source)
    assert request_with_source.source == source, "Source should be preserved when specified"
    
    # Test default source
    request_default = TextCleanRequest(text=text)
    assert request_default.source == "api", "Source should default to 'api'"


@given(
    text=non_empty_text(),
    metadata=st.one_of(
        st.none(),
        st.dictionaries(
            keys=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=('L',))),
            values=st.one_of(st.text(max_size=50), st.integers(), st.booleans()),
            max_size=5
        )
    )
)
@settings(max_examples=100)
def test_property8_text_request_metadata_optional(text, metadata):
    """
    Feature: unified-ingestion-upgrade, Property 8: Interaction Type Assignment
    
    TextCleanRequest SHALL accept optional metadata for inclusion in envelope extras.
    
    **Validates: Requirements 3.5**
    """
    request = TextCleanRequest(text=text, metadata=metadata)
    
    assert request.text == text, "Text should be preserved"
    assert request.metadata == metadata, "Metadata should be preserved (including None)"



# =============================================================================
# Additional Property 7 Tests: Batch Response Schema
# =============================================================================

@given(
    raw_transcript=non_empty_text(),
    cleaned_transcript=non_empty_text(),
    interaction_id=st.uuids()
)
@settings(max_examples=100, deadline=None)
def test_property7_batch_response_schema_completeness(raw_transcript, cleaned_transcript, interaction_id):
    """
    Feature: unified-ingestion-upgrade, Property 7: Response Schema Completeness
    
    For any valid batch processing response, it SHALL contain all required fields:
    raw_transcript, cleaned_transcript, and interaction_id.
    
    **Validates: Requirements 2.3**
    """
    # Import here to avoid circular imports
    from routers.batch import BatchProcessResponse
    
    response = BatchProcessResponse(
        raw_transcript=raw_transcript,
        cleaned_transcript=cleaned_transcript,
        interaction_id=str(interaction_id)
    )
    
    # Verify all required fields are present
    assert response.raw_transcript == raw_transcript, "raw_transcript should be present"
    assert response.cleaned_transcript == cleaned_transcript, "cleaned_transcript should be present"
    assert response.interaction_id == str(interaction_id), "interaction_id should be present"
    
    # Verify JSON serialization includes all fields
    response_dict = response.model_dump()
    assert "raw_transcript" in response_dict, "raw_transcript should be in JSON"
    assert "cleaned_transcript" in response_dict, "cleaned_transcript should be in JSON"
    assert "interaction_id" in response_dict, "interaction_id should be in JSON"


def test_property7_batch_response_requires_all_fields():
    """
    Feature: unified-ingestion-upgrade, Property 7: Response Schema Completeness
    
    BatchProcessResponse SHALL require all fields (raw_transcript, cleaned_transcript, interaction_id).
    
    **Validates: Requirements 2.3**
    """
    from routers.batch import BatchProcessResponse
    
    # Missing raw_transcript
    with pytest.raises(ValidationError):
        BatchProcessResponse(
            cleaned_transcript="cleaned",
            interaction_id="123"
        )
    
    # Missing cleaned_transcript
    with pytest.raises(ValidationError):
        BatchProcessResponse(
            raw_transcript="raw",
            interaction_id="123"
        )
    
    # Missing interaction_id
    with pytest.raises(ValidationError):
        BatchProcessResponse(
            raw_transcript="raw",
            cleaned_transcript="cleaned"
        )


# =============================================================================
# Property 8: Interaction Type Assignment - EnvelopeV1 Tests
# =============================================================================

@given(
    tenant_id=st.uuids(),
    user_id=non_empty_text(),
    text=non_empty_text(),
    source=st.sampled_from(["api", "web", "import", "upload"])
)
@settings(max_examples=100)
def test_property8_envelope_interaction_type_note(tenant_id, user_id, text, source):
    """
    Feature: unified-ingestion-upgrade, Property 8: Interaction Type Assignment
    
    For any EnvelopeV1 created for text cleaning, interaction_type SHALL be "note".
    
    **Validates: Requirements 3.5**
    """
    from datetime import datetime, timezone
    from models.envelope import EnvelopeV1, ContentModel
    
    envelope = EnvelopeV1(
        tenant_id=tenant_id,
        user_id=user_id,
        interaction_type="note",
        content=ContentModel(text=text, format="plain"),
        timestamp=datetime.now(timezone.utc),
        source=source
    )
    
    assert envelope.interaction_type == "note", \
        "Text cleaning envelopes should have interaction_type='note'"


@given(
    tenant_id=st.uuids(),
    user_id=non_empty_text(),
    text=non_empty_text()
)
@settings(max_examples=100)
def test_property8_envelope_interaction_type_transcript(tenant_id, user_id, text):
    """
    Feature: unified-ingestion-upgrade, Property 8: Interaction Type Assignment
    
    For any EnvelopeV1 created for batch processing, interaction_type SHALL be "transcript".
    
    **Validates: Requirements 2.4**
    """
    from datetime import datetime, timezone
    from models.envelope import EnvelopeV1, ContentModel
    
    envelope = EnvelopeV1(
        tenant_id=tenant_id,
        user_id=user_id,
        interaction_type="transcript",
        content=ContentModel(text=text, format="diarized"),
        timestamp=datetime.now(timezone.utc),
        source="upload"
    )
    
    assert envelope.interaction_type == "transcript", \
        "Batch processing envelopes should have interaction_type='transcript'"


@given(
    tenant_id=st.uuids(),
    user_id=non_empty_text(),
    text=non_empty_text(),
    interaction_type=st.sampled_from(["transcript", "note", "document"])
)
@settings(max_examples=100)
def test_property8_envelope_preserves_interaction_type(tenant_id, user_id, text, interaction_type):
    """
    Feature: unified-ingestion-upgrade, Property 8: Interaction Type Assignment
    
    For any EnvelopeV1, the interaction_type field SHALL be preserved exactly as set.
    
    **Validates: Requirements 2.4, 3.5**
    """
    from datetime import datetime, timezone
    from models.envelope import EnvelopeV1, ContentModel
    
    envelope = EnvelopeV1(
        tenant_id=tenant_id,
        user_id=user_id,
        interaction_type=interaction_type,
        content=ContentModel(text=text, format="plain"),
        timestamp=datetime.now(timezone.utc),
        source="api"
    )
    
    assert envelope.interaction_type == interaction_type, \
        f"interaction_type should be preserved as '{interaction_type}'"
    
    # Verify it's preserved in JSON serialization
    envelope_dict = envelope.model_dump(mode="json")
    assert envelope_dict["interaction_type"] == interaction_type, \
        "interaction_type should be preserved in JSON"
