"""Schema reference for calendar tables (read-only).

These tables are owned by eq-email-pipeline's calendar ingestion.
We only read from them (via raw SQL) to match transcripts to calendar events
and retrieve attendee information for contact resolution.

These models are NOT registered as SQLAlchemy tables (no table=True).
They exist purely as schema documentation. All queries use sqlalchemy.text().

DO NOT write to these tables from this service.
"""
from dataclasses import dataclass


@dataclass
class CalendarEventSchema:
    """Schema reference for calendar_events table.

    Columns:
        id: UUID PK
        tenant_id: UUID (FK to tenants)
        provider_connection_id: UUID
        provider_event_id: text
        title: text (nullable)
        description: text (nullable)
        start_time: timestamptz
        end_time: timestamptz
        timezone: text (nullable)
        location: text (nullable)
        status: text (nullable, e.g. "confirmed")
        organizer_email: text (nullable)
        is_recurring: boolean
        recurrence_rule: text (nullable)
        conference_join_url: text (nullable)
        conference_type: text (nullable)
        raw_data: text (nullable)
        created_at: timestamptz
        updated_at: timestamptz

    Indexes:
        idx_cal_events_conference on (conference_join_url)
        idx_cal_events_tenant_time on (tenant_id, start_time, end_time)
    """
    pass


@dataclass
class CalendarEventAttendeeSchema:
    """Schema reference for calendar_event_attendees table.

    Columns:
        id: UUID PK
        calendar_event_id: UUID (FK to calendar_events)
        email: text
        display_name: text (nullable)
        is_organizer: boolean
        response_status: text (nullable)
        is_resource: boolean
        is_optional: boolean
        created_at: timestamptz
    """
    pass
