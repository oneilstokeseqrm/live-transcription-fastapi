"""
Text Cleaning Request/Response Models

This module defines the Pydantic models for the text cleaning endpoint.
These models handle validation and serialization for the POST /text/clean API.
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, field_validator


class TextCleanRequest(BaseModel):
    """
    Request body for the text cleaning endpoint.
    
    Attributes:
        text: Raw text to clean (required, must not be empty or whitespace-only)
        metadata: Optional metadata to include in the event extras
        source: Content source identifier (default: "api")
    """
    text: str = Field(
        ...,
        description="Raw text to clean"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata to include in extras"
    )
    source: str = Field(
        default="api",
        description="Content source identifier"
    )
    
    @field_validator('text')
    @classmethod
    def text_must_not_be_empty(cls, v: str) -> str:
        """Validate that text is not empty or whitespace-only."""
        if not v or not v.strip():
            raise ValueError("text field cannot be empty or contain only whitespace")
        return v


class TextCleanResponse(BaseModel):
    """
    Response from the text cleaning endpoint.
    
    Attributes:
        raw_text: Original text that was submitted
        cleaned_text: Cleaned/processed text
        interaction_id: Unique identifier for this interaction
    """
    raw_text: str = Field(
        ...,
        description="Original text that was submitted"
    )
    cleaned_text: str = Field(
        ...,
        description="Cleaned/processed text"
    )
    interaction_id: str = Field(
        ...,
        description="Unique identifier for this interaction"
    )
