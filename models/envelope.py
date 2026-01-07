"""
Envelope V1 Data Models

This module defines the standardized EnvelopeV1 schema for all ecosystem events.
The schema provides a consistent structure for events published to Kinesis and EventBridge.
"""

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_serializer


class ContentModel(BaseModel):
    """
    Nested content structure containing the actual text and format.
    
    This model encapsulates the primary content payload for any interaction,
    supporting multiple format types for different content sources.
    """
    text: str = Field(
        ...,
        description="The actual content text"
    )
    format: str = Field(
        default="plain",
        description="Content format: 'plain', 'markdown', 'diarized'"
    )


class EnvelopeV1(BaseModel):
    """
    Standardized event envelope for all ecosystem events (Version 1).
    
    This schema provides a consistent structure for events published to
    Kinesis and EventBridge, enabling reliable downstream processing.
    
    Structure:
    - Strict Core: Required fields that must always be present
    - Flexible Edges: Optional fields for extensibility
    - Processing Metadata: Optional fields for tracing and identification
    """
    # Schema Version
    schema_version: str = Field(
        default="v1",
        description="Event schema version"
    )
    
    # Strict Core - Identity
    tenant_id: UUID = Field(
        ...,
        description="Tenant/organization UUID"
    )
    user_id: str = Field(
        ...,
        description="User identifier (supports Auth0 IDs, type-prefixed IDs)"
    )
    
    # Strict Core - Content
    interaction_type: str = Field(
        ...,
        description="Type of interaction: 'transcript', 'note', 'document'"
    )
    content: ContentModel = Field(
        ...,
        description="The actual content payload"
    )
    timestamp: datetime = Field(
        ...,
        description="Event creation timestamp (UTC)"
    )
    source: str = Field(
        ...,
        description="Origin of content: 'web-mic', 'upload', 'api', 'import'"
    )
    
    # Flexible Edges - Extensibility
    extras: Dict[str, Any] = Field(
        default_factory=dict,
        description="Flexible metadata for domain-specific extensions"
    )
    
    # Processing Metadata
    interaction_id: Optional[UUID] = Field(
        None,
        description="Unique identifier for this interaction"
    )
    trace_id: Optional[str] = Field(
        None,
        description="Distributed tracing identifier"
    )
    
    @field_serializer('timestamp')
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize datetime to ISO 8601 format with Z suffix for UTC."""
        iso_str = value.isoformat()
        # Replace +00:00 with Z for cleaner UTC representation
        if iso_str.endswith('+00:00'):
            return iso_str[:-6] + 'Z'
        elif not iso_str.endswith('Z'):
            return iso_str + 'Z'
        return iso_str
    
    @field_serializer('tenant_id', 'interaction_id')
    def serialize_uuid(self, value: Optional[UUID]) -> Optional[str]:
        """Serialize UUID to string."""
        return str(value) if value else None


class KinesisPayloadWrapper(BaseModel):
    """
    Wrapper structure for Kinesis records.
    
    When publishing to Kinesis, the EnvelopeV1 is wrapped in this structure
    to provide easy access to routing fields without parsing the full envelope.
    This enables Step Functions and other consumers to route events efficiently.
    """
    envelope: Dict[str, Any] = Field(
        ...,
        description="Complete EnvelopeV1 as JSON object"
    )
    trace_id: Optional[str] = Field(
        None,
        description="Duplicated from envelope for routing"
    )
    tenant_id: str = Field(
        ...,
        description="Duplicated from envelope for partition key visibility"
    )
    schema_version: str = Field(
        default="v1",
        description="For version-based routing"
    )
