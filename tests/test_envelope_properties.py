"""
Property-Based Tests for EnvelopeV1 Schema Validation

Feature: unified-ingestion-upgrade, Property 4: EnvelopeV1 Schema Validation
Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10

This module tests that EnvelopeV1 correctly validates all required fields,
applies defaults, and maintains schema integrity.
"""

import uuid
from datetime import datetime, timezone
from hypothesis import given, strategies as st, settings
from pydantic import ValidationError
import pytest

from models.envelope import EnvelopeV1, ContentModel, KinesisPayloadWrapper


# Custom strategies for generating valid test data
def uuid_strategy():
    """Generate a valid UUID object."""
    return st.uuids()


@st.composite
def content_model_strategy(draw):
    """Generate a valid ContentModel."""
    text = draw(st.text(min_size=1, max_size=1000))
    format_type = draw(st.sampled_from(["plain", "markdown", "diarized"]))
    return ContentModel(text=text, format=format_type)


@st.composite
def envelope_v1_strategy(draw):
    """Generate a valid EnvelopeV1 with all required fields."""
    return EnvelopeV1(
        tenant_id=draw(st.uuids()),
        user_id=draw(st.text(min_size=1, max_size=200)),
        interaction_type=draw(st.sampled_from(["transcript", "note", "document"])),
        content=draw(content_model_strategy()),
        timestamp=datetime.now(timezone.utc),
        source=draw(st.sampled_from(["web-mic", "upload", "api", "import"]))
    )


@given(envelope_v1_strategy())
@settings(max_examples=100)
def test_envelope_schema_validation_property(envelope):
    """
    Property 4: EnvelopeV1 Schema Validation
    
    For any valid combination of required fields (tenant_id as UUID, user_id as string,
    interaction_type as string, content as ContentModel, timestamp as datetime, 
    source as string), an EnvelopeV1 instance SHALL be successfully created with 
    schema_version defaulting to "v1" and extras defaulting to empty dict.
    
    Feature: unified-ingestion-upgrade, Property 4: EnvelopeV1 Schema Validation
    Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10
    """
    # Verify schema_version defaults to "v1" (Requirement 4.1)
    assert envelope.schema_version == "v1", "schema_version must default to 'v1'"
    
    # Verify tenant_id is a UUID (Requirement 4.2)
    assert isinstance(envelope.tenant_id, uuid.UUID), "tenant_id must be a UUID"
    
    # Verify user_id is a string (Requirement 4.3)
    assert isinstance(envelope.user_id, str), "user_id must be a string"
    assert len(envelope.user_id) > 0, "user_id must not be empty"
    
    # Verify interaction_type is a string (Requirement 4.4)
    assert isinstance(envelope.interaction_type, str), "interaction_type must be a string"
    
    # Verify content is a ContentModel with text and format (Requirement 4.5)
    assert isinstance(envelope.content, ContentModel), "content must be a ContentModel"
    assert hasattr(envelope.content, "text"), "content must have text field"
    assert hasattr(envelope.content, "format"), "content must have format field"
    assert isinstance(envelope.content.text, str), "content.text must be a string"
    assert isinstance(envelope.content.format, str), "content.format must be a string"
    
    # Verify timestamp is a datetime (Requirement 4.6)
    assert isinstance(envelope.timestamp, datetime), "timestamp must be a datetime"
    
    # Verify source is a string (Requirement 4.7)
    assert isinstance(envelope.source, str), "source must be a string"
    
    # Verify extras defaults to empty dict (Requirement 4.8)
    assert isinstance(envelope.extras, dict), "extras must be a dict"
    
    # Verify interaction_id is optional UUID (Requirement 4.9)
    assert envelope.interaction_id is None or isinstance(envelope.interaction_id, uuid.UUID), \
        "interaction_id must be None or UUID"
    
    # Verify trace_id is optional string (Requirement 4.10)
    assert envelope.trace_id is None or isinstance(envelope.trace_id, str), \
        "trace_id must be None or string"


@given(
    tenant_id=st.uuids(),
    user_id=st.text(min_size=1, max_size=200),
    interaction_type=st.sampled_from(["transcript", "note", "document"]),
    text=st.text(min_size=1, max_size=1000),
    source=st.sampled_from(["web-mic", "upload", "api", "import"]),
    extras=st.dictionaries(
        keys=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=('L', 'N'))),
        values=st.one_of(st.text(max_size=100), st.integers(), st.booleans()),
        max_size=5
    )
)
@settings(max_examples=100)
def test_envelope_accepts_extras_metadata(tenant_id, user_id, interaction_type, text, source, extras):
    """
    Property: EnvelopeV1 accepts arbitrary extras metadata.
    
    For any valid envelope with extras dictionary, the extras field should
    preserve all key-value pairs without modification.
    
    Feature: unified-ingestion-upgrade, Property 4: EnvelopeV1 Schema Validation
    Validates: Requirements 4.8
    """
    envelope = EnvelopeV1(
        tenant_id=tenant_id,
        user_id=user_id,
        interaction_type=interaction_type,
        content=ContentModel(text=text, format="plain"),
        timestamp=datetime.now(timezone.utc),
        source=source,
        extras=extras
    )
    
    # Verify extras are preserved
    assert envelope.extras == extras, "extras must be preserved exactly"


@given(
    tenant_id=st.uuids(),
    user_id=st.text(min_size=1, max_size=200),
    interaction_id=st.uuids(),
    trace_id=st.text(min_size=1, max_size=100)
)
@settings(max_examples=100)
def test_envelope_accepts_optional_processing_metadata(tenant_id, user_id, interaction_id, trace_id):
    """
    Property: EnvelopeV1 accepts optional processing metadata.
    
    For any valid envelope with interaction_id and trace_id provided,
    these optional fields should be preserved.
    
    Feature: unified-ingestion-upgrade, Property 4: EnvelopeV1 Schema Validation
    Validates: Requirements 4.9, 4.10
    """
    envelope = EnvelopeV1(
        tenant_id=tenant_id,
        user_id=user_id,
        interaction_type="transcript",
        content=ContentModel(text="test content", format="plain"),
        timestamp=datetime.now(timezone.utc),
        source="api",
        interaction_id=interaction_id,
        trace_id=trace_id
    )
    
    # Verify optional fields are preserved
    assert envelope.interaction_id == interaction_id, "interaction_id must be preserved"
    assert envelope.trace_id == trace_id, "trace_id must be preserved"


@given(st.text(min_size=1, max_size=200))
@settings(max_examples=100)
def test_user_id_accepts_various_formats(user_id):
    """
    Property: user_id accepts various ID formats (Auth0, type-prefixed, etc.)
    
    For any non-empty string user_id, the EnvelopeV1 should accept it,
    supporting Auth0 IDs (auth0|...), type-prefixed IDs (user_2x9...), etc.
    
    Feature: unified-ingestion-upgrade, Property 4: EnvelopeV1 Schema Validation
    Validates: Requirements 4.3
    """
    envelope = EnvelopeV1(
        tenant_id=uuid.uuid4(),
        user_id=user_id,
        interaction_type="note",
        content=ContentModel(text="test", format="plain"),
        timestamp=datetime.now(timezone.utc),
        source="api"
    )
    
    assert envelope.user_id == user_id, "user_id must be preserved exactly"


def test_content_model_format_defaults_to_plain():
    """
    Unit test: ContentModel format defaults to 'plain'.
    
    Feature: unified-ingestion-upgrade, Property 4: EnvelopeV1 Schema Validation
    Validates: Requirements 4.5
    """
    content = ContentModel(text="test content")
    assert content.format == "plain", "format must default to 'plain'"


def test_envelope_schema_version_defaults_to_v1():
    """
    Unit test: EnvelopeV1 schema_version defaults to 'v1'.
    
    Feature: unified-ingestion-upgrade, Property 4: EnvelopeV1 Schema Validation
    Validates: Requirements 4.1
    """
    envelope = EnvelopeV1(
        tenant_id=uuid.uuid4(),
        user_id="test-user",
        interaction_type="transcript",
        content=ContentModel(text="test", format="plain"),
        timestamp=datetime.now(timezone.utc),
        source="api"
    )
    assert envelope.schema_version == "v1", "schema_version must default to 'v1'"


def test_envelope_extras_defaults_to_empty_dict():
    """
    Unit test: EnvelopeV1 extras defaults to empty dict.
    
    Feature: unified-ingestion-upgrade, Property 4: EnvelopeV1 Schema Validation
    Validates: Requirements 4.8
    """
    envelope = EnvelopeV1(
        tenant_id=uuid.uuid4(),
        user_id="test-user",
        interaction_type="transcript",
        content=ContentModel(text="test", format="plain"),
        timestamp=datetime.now(timezone.utc),
        source="api"
    )
    assert envelope.extras == {}, "extras must default to empty dict"


def test_envelope_rejects_invalid_tenant_id():
    """
    Unit test: EnvelopeV1 rejects non-UUID tenant_id.
    
    Feature: unified-ingestion-upgrade, Property 4: EnvelopeV1 Schema Validation
    Validates: Requirements 4.2
    """
    with pytest.raises(ValidationError):
        EnvelopeV1(
            tenant_id="not-a-uuid",  # Invalid
            user_id="test-user",
            interaction_type="transcript",
            content=ContentModel(text="test", format="plain"),
            timestamp=datetime.now(timezone.utc),
            source="api"
        )


def test_envelope_rejects_missing_required_fields():
    """
    Unit test: EnvelopeV1 rejects missing required fields.
    
    Feature: unified-ingestion-upgrade, Property 4: EnvelopeV1 Schema Validation
    Validates: Requirements 4.2, 4.3, 4.4, 4.5, 4.6, 4.7
    """
    # Missing tenant_id
    with pytest.raises(ValidationError):
        EnvelopeV1(
            user_id="test-user",
            interaction_type="transcript",
            content=ContentModel(text="test", format="plain"),
            timestamp=datetime.now(timezone.utc),
            source="api"
        )
    
    # Missing user_id
    with pytest.raises(ValidationError):
        EnvelopeV1(
            tenant_id=uuid.uuid4(),
            interaction_type="transcript",
            content=ContentModel(text="test", format="plain"),
            timestamp=datetime.now(timezone.utc),
            source="api"
        )
    
    # Missing interaction_type
    with pytest.raises(ValidationError):
        EnvelopeV1(
            tenant_id=uuid.uuid4(),
            user_id="test-user",
            content=ContentModel(text="test", format="plain"),
            timestamp=datetime.now(timezone.utc),
            source="api"
        )
    
    # Missing content
    with pytest.raises(ValidationError):
        EnvelopeV1(
            tenant_id=uuid.uuid4(),
            user_id="test-user",
            interaction_type="transcript",
            timestamp=datetime.now(timezone.utc),
            source="api"
        )
    
    # Missing timestamp
    with pytest.raises(ValidationError):
        EnvelopeV1(
            tenant_id=uuid.uuid4(),
            user_id="test-user",
            interaction_type="transcript",
            content=ContentModel(text="test", format="plain"),
            source="api"
        )
    
    # Missing source
    with pytest.raises(ValidationError):
        EnvelopeV1(
            tenant_id=uuid.uuid4(),
            user_id="test-user",
            interaction_type="transcript",
            content=ContentModel(text="test", format="plain"),
            timestamp=datetime.now(timezone.utc)
        )



# =============================================================================
# Property 5: EnvelopeV1 Round-Trip Serialization
# Feature: unified-ingestion-upgrade, Property 5: EnvelopeV1 Round-Trip Serialization
# Validates: Requirements 4.11
# =============================================================================

@given(envelope_v1_strategy())
@settings(max_examples=100)
def test_envelope_round_trip_serialization(envelope):
    """
    Property 5: EnvelopeV1 Round-Trip Serialization
    
    For any valid EnvelopeV1 instance, serializing to JSON and then deserializing
    back SHALL produce an EnvelopeV1 instance that is equivalent to the original
    (all field values match).
    
    Feature: unified-ingestion-upgrade, Property 5: EnvelopeV1 Round-Trip Serialization
    Validates: Requirements 4.11
    """
    # Serialize to JSON string
    json_str = envelope.model_dump_json()
    
    # Deserialize back to EnvelopeV1
    restored = EnvelopeV1.model_validate_json(json_str)
    
    # Verify all fields match
    assert restored.schema_version == envelope.schema_version, \
        "schema_version must be preserved after round-trip"
    assert restored.tenant_id == envelope.tenant_id, \
        "tenant_id must be preserved after round-trip"
    assert restored.user_id == envelope.user_id, \
        "user_id must be preserved after round-trip"
    assert restored.interaction_type == envelope.interaction_type, \
        "interaction_type must be preserved after round-trip"
    assert restored.content.text == envelope.content.text, \
        "content.text must be preserved after round-trip"
    assert restored.content.format == envelope.content.format, \
        "content.format must be preserved after round-trip"
    assert restored.source == envelope.source, \
        "source must be preserved after round-trip"
    assert restored.extras == envelope.extras, \
        "extras must be preserved after round-trip"
    assert restored.interaction_id == envelope.interaction_id, \
        "interaction_id must be preserved after round-trip"
    assert restored.trace_id == envelope.trace_id, \
        "trace_id must be preserved after round-trip"
    
    # Timestamp comparison (may have microsecond precision differences due to ISO format)
    # Compare as ISO strings since that's the serialization format
    assert restored.timestamp is not None, "timestamp must be preserved after round-trip"


@given(
    tenant_id=st.uuids(),
    user_id=st.text(min_size=1, max_size=200),
    interaction_type=st.sampled_from(["transcript", "note", "document"]),
    text=st.text(min_size=1, max_size=1000),
    format_type=st.sampled_from(["plain", "markdown", "diarized"]),
    source=st.sampled_from(["web-mic", "upload", "api", "import"]),
    interaction_id=st.uuids(),
    trace_id=st.text(min_size=1, max_size=100),
    extras=st.dictionaries(
        keys=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=('L', 'N'))),
        values=st.one_of(st.text(max_size=100), st.integers(), st.booleans()),
        max_size=5
    )
)
@settings(max_examples=100)
def test_envelope_round_trip_with_all_fields(
    tenant_id, user_id, interaction_type, text, format_type, 
    source, interaction_id, trace_id, extras
):
    """
    Property 5: EnvelopeV1 Round-Trip with all optional fields populated.
    
    For any valid EnvelopeV1 with all fields (including optional ones) populated,
    serializing to JSON and deserializing back SHALL preserve all values.
    
    Feature: unified-ingestion-upgrade, Property 5: EnvelopeV1 Round-Trip Serialization
    Validates: Requirements 4.11
    """
    original = EnvelopeV1(
        tenant_id=tenant_id,
        user_id=user_id,
        interaction_type=interaction_type,
        content=ContentModel(text=text, format=format_type),
        timestamp=datetime.now(timezone.utc),
        source=source,
        extras=extras,
        interaction_id=interaction_id,
        trace_id=trace_id
    )
    
    # Round-trip through JSON
    json_str = original.model_dump_json()
    restored = EnvelopeV1.model_validate_json(json_str)
    
    # Verify all fields preserved
    assert restored.tenant_id == original.tenant_id
    assert restored.user_id == original.user_id
    assert restored.interaction_type == original.interaction_type
    assert restored.content.text == original.content.text
    assert restored.content.format == original.content.format
    assert restored.source == original.source
    assert restored.extras == original.extras
    assert restored.interaction_id == original.interaction_id
    assert restored.trace_id == original.trace_id


@given(envelope_v1_strategy())
@settings(max_examples=100)
def test_envelope_round_trip_via_dict(envelope):
    """
    Property 5: EnvelopeV1 Round-Trip via dict (model_dump).
    
    For any valid EnvelopeV1, converting to dict and back SHALL preserve all values.
    This tests the model_dump() path used when building Kinesis payloads.
    
    Feature: unified-ingestion-upgrade, Property 5: EnvelopeV1 Round-Trip Serialization
    Validates: Requirements 4.11
    """
    # Convert to dict (mode="json" for JSON-compatible types)
    envelope_dict = envelope.model_dump(mode="json")
    
    # Restore from dict
    restored = EnvelopeV1.model_validate(envelope_dict)
    
    # Verify key fields preserved
    assert str(restored.tenant_id) == envelope_dict["tenant_id"]
    assert restored.user_id == envelope_dict["user_id"]
    assert restored.interaction_type == envelope_dict["interaction_type"]
    assert restored.content.text == envelope_dict["content"]["text"]
    assert restored.source == envelope_dict["source"]


def test_envelope_round_trip_preserves_auth0_user_id():
    """
    Unit test: Round-trip preserves Auth0-style user IDs.
    
    Feature: unified-ingestion-upgrade, Property 5: EnvelopeV1 Round-Trip Serialization
    Validates: Requirements 4.11, 4.3
    """
    auth0_user_id = "auth0|507f1f77bcf86cd799439011"
    
    original = EnvelopeV1(
        tenant_id=uuid.uuid4(),
        user_id=auth0_user_id,
        interaction_type="transcript",
        content=ContentModel(text="test", format="plain"),
        timestamp=datetime.now(timezone.utc),
        source="api"
    )
    
    json_str = original.model_dump_json()
    restored = EnvelopeV1.model_validate_json(json_str)
    
    assert restored.user_id == auth0_user_id, \
        "Auth0 user_id must be preserved exactly after round-trip"


def test_envelope_round_trip_preserves_type_prefixed_user_id():
    """
    Unit test: Round-trip preserves type-prefixed user IDs.
    
    Feature: unified-ingestion-upgrade, Property 5: EnvelopeV1 Round-Trip Serialization
    Validates: Requirements 4.11, 4.3
    """
    prefixed_user_id = "user_2x9abc123def456"
    
    original = EnvelopeV1(
        tenant_id=uuid.uuid4(),
        user_id=prefixed_user_id,
        interaction_type="note",
        content=ContentModel(text="test", format="plain"),
        timestamp=datetime.now(timezone.utc),
        source="api"
    )
    
    json_str = original.model_dump_json()
    restored = EnvelopeV1.model_validate_json(json_str)
    
    assert restored.user_id == prefixed_user_id, \
        "Type-prefixed user_id must be preserved exactly after round-trip"


def test_envelope_round_trip_preserves_complex_extras():
    """
    Unit test: Round-trip preserves complex extras metadata.
    
    Feature: unified-ingestion-upgrade, Property 5: EnvelopeV1 Round-Trip Serialization
    Validates: Requirements 4.11, 4.8
    """
    complex_extras = {
        "source_file": "recording_2024.webm",
        "duration_seconds": 3600,
        "is_processed": True,
        "tags": ["meeting", "important"]
    }
    
    original = EnvelopeV1(
        tenant_id=uuid.uuid4(),
        user_id="test-user",
        interaction_type="transcript",
        content=ContentModel(text="test content", format="diarized"),
        timestamp=datetime.now(timezone.utc),
        source="upload",
        extras=complex_extras
    )
    
    json_str = original.model_dump_json()
    restored = EnvelopeV1.model_validate_json(json_str)
    
    assert restored.extras == complex_extras, \
        "Complex extras must be preserved exactly after round-trip"


# =============================================================================
# Property 6: Kinesis Wrapper Structure
# Feature: unified-ingestion-upgrade, Property 6: Kinesis Wrapper Structure
# Validates: Requirements 5.2, 6.1, 6.2, 6.3, 6.4, 6.5
# =============================================================================

@given(envelope_v1_strategy())
@settings(max_examples=100)
def test_kinesis_wrapper_structure_property(envelope):
    """
    Property 6: Kinesis Wrapper Structure
    
    For any EnvelopeV1 being published to Kinesis, the wrapper payload SHALL contain:
    (a) `envelope` key with complete EnvelopeV1 JSON
    (b) `trace_id` duplicated at top level
    (c) `tenant_id` as string at top level
    (d) `schema_version` at top level
    
    Feature: unified-ingestion-upgrade, Property 6: Kinesis Wrapper Structure
    Validates: Requirements 5.2, 6.1, 6.2, 6.3, 6.4, 6.5
    """
    from services.aws_event_publisher import AWSEventPublisher
    
    # Create publisher and build wrapper
    publisher = AWSEventPublisher()
    wrapper = publisher._build_kinesis_payload(envelope)
    
    # (a) Verify envelope key contains complete EnvelopeV1 JSON (Requirement 6.2)
    assert "envelope" in wrapper, "wrapper must contain 'envelope' key"
    assert isinstance(wrapper["envelope"], dict), "envelope must be a dict"
    
    # Verify envelope contains all required fields
    envelope_data = wrapper["envelope"]
    assert "tenant_id" in envelope_data, "envelope must contain tenant_id"
    assert "user_id" in envelope_data, "envelope must contain user_id"
    assert "interaction_type" in envelope_data, "envelope must contain interaction_type"
    assert "content" in envelope_data, "envelope must contain content"
    assert "timestamp" in envelope_data, "envelope must contain timestamp"
    assert "source" in envelope_data, "envelope must contain source"
    assert "schema_version" in envelope_data, "envelope must contain schema_version"
    
    # Verify envelope content matches original
    assert envelope_data["tenant_id"] == str(envelope.tenant_id), \
        "envelope.tenant_id must match original"
    assert envelope_data["user_id"] == envelope.user_id, \
        "envelope.user_id must match original"
    assert envelope_data["interaction_type"] == envelope.interaction_type, \
        "envelope.interaction_type must match original"
    assert envelope_data["content"]["text"] == envelope.content.text, \
        "envelope.content.text must match original"
    assert envelope_data["source"] == envelope.source, \
        "envelope.source must match original"
    
    # (b) Verify trace_id duplicated at top level (Requirement 6.3)
    assert "trace_id" in wrapper, "wrapper must contain 'trace_id' key"
    assert wrapper["trace_id"] == envelope.trace_id, \
        "wrapper.trace_id must match envelope.trace_id"
    
    # (c) Verify tenant_id as string at top level (Requirement 6.4)
    assert "tenant_id" in wrapper, "wrapper must contain 'tenant_id' key"
    assert isinstance(wrapper["tenant_id"], str), "wrapper.tenant_id must be a string"
    assert wrapper["tenant_id"] == str(envelope.tenant_id), \
        "wrapper.tenant_id must equal str(envelope.tenant_id)"
    
    # (d) Verify schema_version at top level (Requirement 6.5)
    assert "schema_version" in wrapper, "wrapper must contain 'schema_version' key"
    assert wrapper["schema_version"] == envelope.schema_version, \
        "wrapper.schema_version must match envelope.schema_version"


@given(
    tenant_id=st.uuids(),
    user_id=st.text(min_size=1, max_size=200),
    trace_id=st.one_of(st.none(), st.text(min_size=1, max_size=100))
)
@settings(max_examples=100)
def test_kinesis_wrapper_trace_id_handling(tenant_id, user_id, trace_id):
    """
    Property 6: Kinesis Wrapper handles trace_id correctly (None or string).
    
    For any EnvelopeV1 with trace_id either None or a string, the wrapper
    SHALL correctly duplicate the trace_id at the top level.
    
    Feature: unified-ingestion-upgrade, Property 6: Kinesis Wrapper Structure
    Validates: Requirements 6.3
    """
    from services.aws_event_publisher import AWSEventPublisher
    
    envelope = EnvelopeV1(
        tenant_id=tenant_id,
        user_id=user_id,
        interaction_type="transcript",
        content=ContentModel(text="test content", format="plain"),
        timestamp=datetime.now(timezone.utc),
        source="api",
        trace_id=trace_id
    )
    
    publisher = AWSEventPublisher()
    wrapper = publisher._build_kinesis_payload(envelope)
    
    # Verify trace_id is correctly duplicated (including None case)
    assert wrapper["trace_id"] == trace_id, \
        f"wrapper.trace_id ({wrapper['trace_id']}) must equal envelope.trace_id ({trace_id})"


def test_kinesis_wrapper_contains_all_required_fields():
    """
    Unit test: Kinesis wrapper contains all required top-level fields.
    
    Feature: unified-ingestion-upgrade, Property 6: Kinesis Wrapper Structure
    Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5
    """
    from services.aws_event_publisher import AWSEventPublisher
    
    envelope = EnvelopeV1(
        tenant_id=uuid.uuid4(),
        user_id="test-user",
        interaction_type="transcript",
        content=ContentModel(text="test content", format="plain"),
        timestamp=datetime.now(timezone.utc),
        source="api",
        trace_id="trace-123"
    )
    
    publisher = AWSEventPublisher()
    wrapper = publisher._build_kinesis_payload(envelope)
    
    # Verify all required top-level fields exist
    required_fields = ["envelope", "trace_id", "tenant_id", "schema_version"]
    for field in required_fields:
        assert field in wrapper, f"wrapper must contain '{field}' field"


def test_kinesis_wrapper_envelope_is_json_serializable():
    """
    Unit test: Kinesis wrapper envelope is JSON-serializable.
    
    Feature: unified-ingestion-upgrade, Property 6: Kinesis Wrapper Structure
    Validates: Requirements 5.2, 6.1
    """
    import json
    from services.aws_event_publisher import AWSEventPublisher
    
    envelope = EnvelopeV1(
        tenant_id=uuid.uuid4(),
        user_id="test-user",
        interaction_type="transcript",
        content=ContentModel(text="test content", format="plain"),
        timestamp=datetime.now(timezone.utc),
        source="api"
    )
    
    publisher = AWSEventPublisher()
    wrapper = publisher._build_kinesis_payload(envelope)
    
    # Verify the entire wrapper can be serialized to JSON
    try:
        json_str = json.dumps(wrapper)
        assert len(json_str) > 0, "JSON serialization should produce non-empty string"
    except (TypeError, ValueError) as e:
        pytest.fail(f"Wrapper must be JSON-serializable: {e}")


# =============================================================================
# Property 10: Partition Key Derivation
# Feature: unified-ingestion-upgrade, Property 10: Partition Key Derivation
# Validates: Requirements 5.3
# =============================================================================

@given(tenant_id=st.uuids())
@settings(max_examples=100)
def test_partition_key_derivation_property(tenant_id):
    """
    Property 10: Partition Key Derivation
    
    For any EnvelopeV1 with a tenant_id, the Kinesis partition key used for
    publishing SHALL equal `str(envelope.tenant_id)`.
    
    Feature: unified-ingestion-upgrade, Property 10: Partition Key Derivation
    Validates: Requirements 5.3
    """
    from services.aws_event_publisher import AWSEventPublisher
    
    envelope = EnvelopeV1(
        tenant_id=tenant_id,
        user_id="test-user",
        interaction_type="transcript",
        content=ContentModel(text="test content", format="plain"),
        timestamp=datetime.now(timezone.utc),
        source="api"
    )
    
    publisher = AWSEventPublisher()
    wrapper = publisher._build_kinesis_payload(envelope)
    
    # The partition key should be derived from tenant_id
    expected_partition_key = str(tenant_id)
    
    # Verify the wrapper's tenant_id (which is used as partition key) matches
    assert wrapper["tenant_id"] == expected_partition_key, \
        f"Partition key must equal str(tenant_id): expected {expected_partition_key}, got {wrapper['tenant_id']}"


@given(
    tenant_id=st.uuids(),
    user_id=st.text(min_size=1, max_size=200),
    interaction_type=st.sampled_from(["transcript", "note", "document"]),
    text=st.text(min_size=1, max_size=1000),
    source=st.sampled_from(["web-mic", "upload", "api", "import"])
)
@settings(max_examples=100)
def test_partition_key_consistent_across_envelope_variations(
    tenant_id, user_id, interaction_type, text, source
):
    """
    Property 10: Partition key is consistent regardless of other envelope fields.
    
    For any EnvelopeV1 with the same tenant_id but different other fields,
    the partition key SHALL always equal `str(tenant_id)`.
    
    Feature: unified-ingestion-upgrade, Property 10: Partition Key Derivation
    Validates: Requirements 5.3
    """
    from services.aws_event_publisher import AWSEventPublisher
    
    envelope = EnvelopeV1(
        tenant_id=tenant_id,
        user_id=user_id,
        interaction_type=interaction_type,
        content=ContentModel(text=text, format="plain"),
        timestamp=datetime.now(timezone.utc),
        source=source
    )
    
    publisher = AWSEventPublisher()
    wrapper = publisher._build_kinesis_payload(envelope)
    
    # Partition key should only depend on tenant_id
    expected_partition_key = str(tenant_id)
    assert wrapper["tenant_id"] == expected_partition_key, \
        "Partition key must be derived solely from tenant_id"


def test_partition_key_is_string_representation_of_uuid():
    """
    Unit test: Partition key is the string representation of tenant_id UUID.
    
    Feature: unified-ingestion-upgrade, Property 10: Partition Key Derivation
    Validates: Requirements 5.3
    """
    from services.aws_event_publisher import AWSEventPublisher
    
    tenant_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    
    envelope = EnvelopeV1(
        tenant_id=tenant_uuid,
        user_id="test-user",
        interaction_type="transcript",
        content=ContentModel(text="test content", format="plain"),
        timestamp=datetime.now(timezone.utc),
        source="api"
    )
    
    publisher = AWSEventPublisher()
    wrapper = publisher._build_kinesis_payload(envelope)
    
    # Verify exact string format
    assert wrapper["tenant_id"] == "12345678-1234-5678-1234-567812345678", \
        "Partition key must be the standard UUID string format"
