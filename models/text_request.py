"""
Text Cleaning Request/Response Models

This module defines the Pydantic models for the text cleaning endpoint.
These models handle validation and serialization for the POST /text/clean API.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

from models.participant_spec import ParticipantSpec


class TextCleanRequest(BaseModel):
    """
    Request body for the text cleaning endpoint.
    
    Attributes:
        text: Raw text to clean (required, must not be empty or whitespace-only)
        metadata: Optional metadata to include in the event extras
        source: Content source identifier (default: "api")
    """
    # validate_assignment re-runs field validators on attribute assignment, not
    # just at construction. This closes a bypass where ``req.occurred_at = <naive
    # or numeric>`` after construction would otherwise skip the validators below
    # and smuggle an un-normalized value downstream. (No code path mutates this
    # model today, so enabling it is purely defensive.)
    model_config = ConfigDict(validate_assignment=True)

    text: str = Field(
        ...,
        description="Raw text to clean"
    )
    account_id: str = Field(
        ...,
        min_length=1,
        description="Account anchor for the interaction. Required.",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional metadata to include in extras"
    )
    source: str = Field(
        default="api",
        description="Content source identifier"
    )
    interaction_type: str = Field(
        default="note",
        description="Interaction type for envelope and intelligence"
    )
    participants: Optional[list[ParticipantSpec]] = Field(
        default=None,
        description="Caller-provided participants",
    )
    occurred_at: Optional[datetime] = Field(
        default=None,
        description=(
            "Optional event-time: when the interaction actually occurred "
            "(ISO-8601, must be timezone-aware). When omitted, the server "
            "stamps the interaction with now() — behavior is byte-for-byte "
            "identical to the pre-occurred_at contract. Only honored from "
            "trusted internal callers and within bounds (see the route's "
            "resolve_event_time); otherwise ignored. Naive datetimes are "
            "rejected (422); offset-aware values are normalized to UTC."
        ),
    )

    @field_validator('text')
    @classmethod
    def text_must_not_be_empty(cls, v: str) -> str:
        """Validate that text is not empty or whitespace-only."""
        if not v or not v.strip():
            raise ValueError("text field cannot be empty or contain only whitespace")
        return v

    @field_validator('occurred_at', mode='before')
    @classmethod
    def occurred_at_must_be_iso_or_datetime(cls, v: Any) -> Any:
        """Narrow the wire contract to ISO-8601 strings (or real datetimes).

        Runs BEFORE Pydantic's datetime coercion. Without this, Pydantic would
        happily coerce an int/float to a Unix-timestamp datetime — and its
        magnitude heuristic treats large ints as milliseconds, so a caller that
        sends epoch-seconds vs epoch-millis can't tell which it got. We
        documented ISO-8601; reject numeric input outright (=> 422) rather than
        silently guessing an instant. None and ``datetime`` (internal Python
        construction) pass through to the aware-UTC normalizer below; strings
        flow to Pydantic's ISO parser (malformed => 422).
        """
        if v is None or isinstance(v, (datetime, str)):
            return v
        raise ValueError(
            "occurred_at must be an ISO-8601 string; numeric/epoch timestamps "
            "are not accepted"
        )

    @field_validator('occurred_at')
    @classmethod
    def occurred_at_must_be_aware_utc(
        cls, v: Optional[datetime]
    ) -> Optional[datetime]:
        """Require a timezone-aware occurred_at and normalize it to UTC.

        Owns the *value*-level contract for the field (HTTP 422 on bad input):

        * ``None`` (omitted) passes through untouched — the route defaults to
          now() and the real-time path is unchanged.
        * A **naive** datetime (no tzinfo) is an ambiguous instant — we refuse
          to guess a timezone and reject it. Pydantic surfaces the ``ValueError``
          as a 422.
        * An **offset-aware** datetime is converted to UTC so every downstream
          consumer sees an aware-UTC value. This matters because the envelope
          serializer (``models/envelope.py``) blindly appends ``Z`` and the
          front-matter formatter (``transcript_enrichment.py``) uses
          ``strftime('...Z')`` — both would silently mislabel a non-UTC offset
          as UTC without this normalization.

        Trust and freshness bounds are deliberately NOT decided here — they
        depend on the request's auth context and the current time, which a
        field validator cannot see; they live in ``utils.event_time``.
        """
        if v is None:
            return None
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError(
                "occurred_at must be timezone-aware (include a UTC offset or "
                "'Z'); naive datetimes are rejected"
            )
        return v.astimezone(timezone.utc)


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
