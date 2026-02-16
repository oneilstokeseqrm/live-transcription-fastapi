"""
Batch Event Data Models

This module defines Pydantic models for the BatchProcessingCompleted event schema.
The schema is versioned to support future evolution while maintaining backward compatibility.
"""

from typing import Optional
from pydantic import BaseModel, Field


class EventData(BaseModel):
    """
    Nested data object containing transcript content.
    
    This model encapsulates both the raw and cleaned transcript data
    from the batch processing pipeline.
    """
    cleaned_transcript: str = Field(
        ...,
        description="Cleaned and structured transcript from CleanerService"
    )
    raw_transcript: str = Field(
        ...,
        description="Raw transcript from Deepgram transcription service"
    )


class BatchProcessingCompletedEvent(BaseModel):
    """
    Event schema for batch processing completion.
    
    This schema is versioned to support future evolution. Version 1.0 includes
    basic transcript data with tenant, user, and account context.
    
    All events published to EventBridge must conform to this schema.
    """
    version: str = Field(
        default="1.0",
        description="Event schema version following semantic versioning"
    )
    interaction_id: str = Field(
        ...,
        description="UUID v4 uniquely identifying this processing request"
    )
    tenant_id: str = Field(
        ...,
        description="UUID v4 identifying the tenant/organization"
    )
    user_id: str = Field(
        ...,
        description="User identifier who initiated the processing request"
    )
    account_id: Optional[str] = Field(
        None,
        description="Optional account identifier for additional context"
    )
    pg_user_id: Optional[str] = Field(
        None,
        description="Postgres User UUID from identity bridge"
    )
    timestamp: str = Field(
        ...,
        description="ISO 8601 timestamp of when the event was created"
    )
    status: str = Field(
        default="completed",
        description="Processing status (completed, failed, etc.)"
    )
    data: EventData = Field(
        ...,
        description="Transcript data containing raw and cleaned transcripts"
    )
