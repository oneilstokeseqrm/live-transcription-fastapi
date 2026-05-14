"""Caller-provided participant specification.

Allows the API to accept manually-attached participants (e.g., notes typed
into an interaction without a calendar match) on ingestion endpoints.
Wired to TranscriptEnrichmentService.enrich(existing_contact_ids=...).
"""

from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


ParticipantRole = Literal["organizer", "attendee", "optional", "sender", "recipient"]


class ParticipantSpec(BaseModel):
    """Minimal caller-provided participant.

    Resolution rules:
    - email is the unique key (combined with tenant for find-or-create)
    - display_name is optional; if absent, 3-tier name resolution runs
    - role defaults to None (interpreted as 'attendee' if needed downstream)
    """

    email: EmailStr = Field(..., description="Participant email (canonical lower-case)")
    display_name: Optional[str] = Field(default=None, max_length=255)
    role: Optional[ParticipantRole] = Field(default=None)
