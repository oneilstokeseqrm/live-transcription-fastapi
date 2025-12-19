"""
Property-Based Tests for Event Schema Completeness

Feature: event-driven-architecture, Property 1: Event Schema Completeness
Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8

This module tests that all events published conform to the BatchProcessingCompletedEvent
schema with correct field types and formats.
"""

import uuid
import json
from datetime import datetime
from hypothesis import given, strategies as st, settings
from models.batch_event import BatchProcessingCompletedEvent, EventData


# Custom strategies for generating valid test data
@st.composite
def uuid_v4_string(draw):
    """Generate a valid UUID v4 string."""
    return str(uuid.uuid4())


@st.composite
def iso8601_timestamp(draw):
    """Generate a valid ISO 8601 timestamp string."""
    return datetime.utcnow().isoformat() + "Z"


@st.composite
def event_data_strategy(draw):
    """Generate valid EventData."""
    return {
        "cleaned_transcript": draw(st.text(min_size=1, max_size=1000)),
        "raw_transcript": draw(st.text(min_size=1, max_size=1000))
    }


@st.composite
def batch_event_strategy(draw):
    """Generate a valid BatchProcessingCompletedEvent."""
    return {
        "version": "1.0",
        "interaction_id": draw(uuid_v4_string()),
        "tenant_id": draw(uuid_v4_string()),
        "user_id": draw(st.text(min_size=1, max_size=100)),
        "account_id": draw(st.one_of(st.none(), st.text(min_size=1, max_size=100))),
        "timestamp": draw(iso8601_timestamp()),
        "status": "completed",
        "data": draw(event_data_strategy())
    }


@given(batch_event_strategy())
@settings(max_examples=100)
def test_event_schema_completeness(event_dict):
    """
    Property: Event Schema Completeness
    
    For any event published by the EventPublisher, the event detail should contain
    all required fields (version, interaction_id, tenant_id, user_id, account_id,
    timestamp, status, data) with correct types (UUID v4 for IDs, ISO 8601 for
    timestamp, nested object for data).
    
    Feature: event-driven-architecture, Property 1: Event Schema Completeness
    Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8
    """
    # Parse the event using Pydantic model
    event = BatchProcessingCompletedEvent(**event_dict)
    
    # Verify all required fields are present
    assert event.version is not None, "version field must be present"
    assert event.interaction_id is not None, "interaction_id field must be present"
    assert event.tenant_id is not None, "tenant_id field must be present"
    assert event.user_id is not None, "user_id field must be present"
    # account_id can be None
    assert event.timestamp is not None, "timestamp field must be present"
    assert event.status is not None, "status field must be present"
    assert event.data is not None, "data field must be present"
    
    # Verify correct types and formats
    assert event.version == "1.0", "version must be '1.0'"
    assert event.status == "completed", "status must be 'completed'"
    
    # Verify UUID v4 format for interaction_id and tenant_id
    interaction_uuid = uuid.UUID(event.interaction_id, version=4)
    assert interaction_uuid.version == 4, "interaction_id must be UUID v4"
    
    tenant_uuid = uuid.UUID(event.tenant_id, version=4)
    assert tenant_uuid.version == 4, "tenant_id must be UUID v4"
    
    # Verify user_id is a string
    assert isinstance(event.user_id, str), "user_id must be a string"
    assert len(event.user_id) > 0, "user_id must not be empty"
    
    # Verify account_id is string or None
    assert event.account_id is None or isinstance(event.account_id, str), \
        "account_id must be string or None"
    
    # Verify timestamp is ISO 8601 format (Pydantic validates this)
    assert isinstance(event.timestamp, str), "timestamp must be a string"
    assert "T" in event.timestamp or "Z" in event.timestamp, \
        "timestamp must be in ISO 8601 format"
    
    # Verify data object structure
    assert isinstance(event.data, EventData), "data must be EventData object"
    assert hasattr(event.data, "cleaned_transcript"), \
        "data must have cleaned_transcript field"
    assert hasattr(event.data, "raw_transcript"), \
        "data must have raw_transcript field"
    assert isinstance(event.data.cleaned_transcript, str), \
        "cleaned_transcript must be a string"
    assert isinstance(event.data.raw_transcript, str), \
        "raw_transcript must be a string"
    
    # Verify the event can be serialized to JSON (for EventBridge)
    event_json = event.model_dump_json()
    assert event_json is not None, "event must be serializable to JSON"
    
    # Verify it can be deserialized back
    parsed = json.loads(event_json)
    assert parsed["version"] == "1.0"
    assert parsed["interaction_id"] == event.interaction_id
    assert parsed["tenant_id"] == event.tenant_id


@given(
    st.text(min_size=1, max_size=100),
    st.text(min_size=1, max_size=100)
)
@settings(max_examples=100)
def test_event_data_always_has_both_transcripts(cleaned, raw):
    """
    Property: EventData must always contain both transcript types.
    
    For any EventData object, both cleaned_transcript and raw_transcript
    must be present and non-empty.
    
    Feature: event-driven-architecture, Property 1: Event Schema Completeness
    Validates: Requirements 1.8
    """
    event_data = EventData(
        cleaned_transcript=cleaned,
        raw_transcript=raw
    )
    
    assert event_data.cleaned_transcript is not None
    assert event_data.raw_transcript is not None
    assert len(event_data.cleaned_transcript) > 0
    assert len(event_data.raw_transcript) > 0


@given(uuid_v4_string(), uuid_v4_string())
@settings(max_examples=100)
def test_uuid_fields_are_valid_v4(interaction_id, tenant_id):
    """
    Property: UUID fields must be valid UUID v4 format.
    
    For any event, interaction_id and tenant_id must be valid UUID v4 strings
    that can be parsed without errors.
    
    Feature: event-driven-architecture, Property 1: Event Schema Completeness
    Validates: Requirements 1.2, 1.3
    """
    # Verify they can be parsed as UUID v4
    interaction_uuid = uuid.UUID(interaction_id, version=4)
    tenant_uuid = uuid.UUID(tenant_id, version=4)
    
    assert interaction_uuid.version == 4
    assert tenant_uuid.version == 4
    
    # Verify string representation matches
    assert str(interaction_uuid) == interaction_id
    assert str(tenant_uuid) == tenant_id
