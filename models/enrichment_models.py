"""Data models for transcript contact enrichment.

These models represent the enrichment result from matching transcripts
to calendar events and resolving attendees to canonical contact records.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ResolvedContact:
    """A contact resolved from a calendar event attendee.

    Every resolved contact ALWAYS has a contact_id (UUIDv4 from Postgres)
    and an email. Name may be null for email-only contacts.
    """
    contact_id: str       # UUIDv4 — ALWAYS present
    email: str            # ALWAYS present (how we found/created them)
    name: Optional[str]   # From calendar, heuristic, or None
    role: str             # "organizer" | "attendee" | "optional" | "recorder"
    is_new: bool          # True if we just created this contact


@dataclass
class EnrichmentResult:
    """Result of transcript enrichment — contacts, calendar match, front-matter.

    This is the return type of TranscriptEnrichmentService.enrich().
    """
    contacts: list[ResolvedContact] = field(default_factory=list)
    contact_ids: list[str] = field(default_factory=list)
    meeting_title: Optional[str] = None
    calendar_event_id: Optional[str] = None
    front_matter: Optional[str] = None
    match_confidence: str = "none"       # "high" | "medium" | "none"
    match_method: str = "none"           # "conference_url" | "time_window" | "none"
    new_contacts_created: int = 0
    enrichment_source: str = "none"      # "calendar_match" | "none"

    @property
    def has_enrichment(self) -> bool:
        """True if enrichment produced any useful metadata (contacts or calendar match)."""
        return self.enrichment_source != "none"

    def to_extras_dict(self) -> dict:
        """Build the extras dict for the EnvelopeV1 payload.

        Returns an empty dict if no enrichment data is available.
        """
        if not self.has_enrichment:
            return {}
        extras: dict = {
            "enrichment_source": self.enrichment_source,
            "enrichment_confidence": self.match_confidence,
        }
        if self.contact_ids:
            extras["contact_ids"] = self.contact_ids
            extras["contacts"] = [
                {
                    "contact_id": c.contact_id,
                    "email": c.email,
                    "name": c.name,
                    "role": c.role,
                }
                for c in self.contacts
            ]
        if self.meeting_title:
            extras["meeting_title"] = self.meeting_title
        if self.calendar_event_id:
            extras["calendar_event_id"] = self.calendar_event_id
        return extras
