#!/usr/bin/env python3
"""
Seed Script: Calendar Events + Attendees + Contacts for Enrichment Testing

Seeds calendar_events, calendar_event_attendees, and contacts tables in Neon
for the test tenant 11111111-1111-4111-8111-111111111111.

This enables integration and E2E testing of the TranscriptEnrichmentService.

Usage:
    python3 scripts/seed_enrichment_test.py seed       # Seed test data
    python3 scripts/seed_enrichment_test.py verify      # Verify seeded data
    python3 scripts/seed_enrichment_test.py clean       # Remove seeded data
"""

import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

os.environ["PYTHONUNBUFFERED"] = "1"

import psycopg2

# --- Configuration ---

NEON_DSN = os.getenv("DATABASE_URL")
if not NEON_DSN:
    print("ERROR: DATABASE_URL environment variable is required")
    sys.exit(1)

TENANT_ID = "11111111-1111-4111-8111-111111111111"

# Use a real provider_connection for FK constraint
# This is looked up at runtime; fallback to a fixed UUID if no connections exist
PROVIDER_CONNECTION_ID = None  # resolved in seed()
CALENDAR_EVENT_1_ID = "33333333-3333-4333-8333-333333333333"
CALENDAR_EVENT_2_ID = "33333333-3333-4333-8333-333333333334"
EXISTING_CONTACT_1_ID = "44444444-4444-4444-8444-444444444441"
EXISTING_CONTACT_2_ID = "44444444-4444-4444-8444-444444444442"

# Event 1: Recent meeting with known + unknown attendees
# Starts "now" so integration tests can match with current timestamp
EVENT_1_TITLE = "Q3 Pipeline Review - Enrichment Test"
EVENT_1_CONFERENCE_URL = "https://zoom.us/j/enrichment-test-12345"

# Event 2: Meeting 2 hours ago (for time window boundary testing)
EVENT_2_TITLE = "Technical Deep Dive - Enrichment Test"

# Attendees for Event 1 (mix of known + unknown)
EVENT_1_ATTENDEES = [
    {
        "email": "jane.smith@acme-test.com",
        "display_name": "Jane Smith",
        "is_organizer": True,
        "response_status": "accepted",
        "is_resource": False,
        "is_optional": False,
    },
    {
        "email": "bob.jones@acme-test.com",
        "display_name": "Bob Jones",
        "is_organizer": False,
        "response_status": "accepted",
        "is_resource": False,
        "is_optional": False,
    },
    {
        "email": "unknown.person@mystery-corp.com",
        "display_name": "",  # No display name (test name heuristic)
        "is_organizer": False,
        "response_status": "tentative",
        "is_resource": False,
        "is_optional": True,
    },
    {
        # Conference room - should be filtered out (is_resource=True)
        "email": "conf-room-1@acme-test.com",
        "display_name": "Main Conference Room",
        "is_organizer": False,
        "response_status": "accepted",
        "is_resource": True,
        "is_optional": False,
    },
]

# Attendees for Event 2
EVENT_2_ATTENDEES = [
    {
        "email": "jane.smith@acme-test.com",
        "display_name": "Jane Smith",
        "is_organizer": False,
        "response_status": "accepted",
        "is_resource": False,
        "is_optional": False,
    },
    {
        "email": "alex.chen@partner-test.com",
        "display_name": "Alex Chen",
        "is_organizer": True,
        "response_status": "accepted",
        "is_resource": False,
        "is_optional": False,
    },
]


def seed(conn):
    """Seed calendar events, attendees, and contacts for enrichment testing."""
    cur = conn.cursor()
    now = datetime.now(timezone.utc)

    # Resolve a real provider_connection for FK constraint
    cur.execute("SELECT id FROM provider_connections LIMIT 1")
    row = cur.fetchone()
    if not row:
        print("ERROR: No provider_connections found — cannot seed calendar events (FK constraint)")
        sys.exit(1)
    connection_id = str(row[0])
    print(f"Using connection_id: {connection_id[:8]}...")

    print("Seeding calendar events...")

    # Event 1: Current time (for happy-path matching)
    cur.execute(
        """
        INSERT INTO calendar_events (
            id, tenant_id, connection_id, provider, provider_event_id,
            title, start_time, end_time, status,
            organizer_email, conference_join_url, conference_provider,
            created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (id) DO UPDATE SET
            title = EXCLUDED.title,
            start_time = EXCLUDED.start_time,
            end_time = EXCLUDED.end_time,
            updated_at = NOW()
        """,
        (
            CALENDAR_EVENT_1_ID, TENANT_ID, connection_id,
            "google",
            "enrichment-test-event-1",
            EVENT_1_TITLE,
            now - timedelta(minutes=5),   # Started 5 min ago
            now + timedelta(minutes=55),  # Ends in 55 min (1hr meeting)
            "confirmed",
            "jane.smith@acme-test.com",
            EVENT_1_CONFERENCE_URL,
            "zoom",
        ),
    )

    # Event 2: 2 hours ago (for time window boundary testing)
    cur.execute(
        """
        INSERT INTO calendar_events (
            id, tenant_id, connection_id, provider, provider_event_id,
            title, start_time, end_time, status,
            organizer_email,
            created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (id) DO UPDATE SET
            title = EXCLUDED.title,
            start_time = EXCLUDED.start_time,
            end_time = EXCLUDED.end_time,
            updated_at = NOW()
        """,
        (
            CALENDAR_EVENT_2_ID, TENANT_ID, connection_id,
            "google",
            "enrichment-test-event-2",
            EVENT_2_TITLE,
            now - timedelta(hours=2, minutes=30),
            now - timedelta(hours=1, minutes=30),
            "confirmed",
            "alex.chen@partner-test.com",
        ),
    )

    print(f"  Event 1: {EVENT_1_TITLE} (current)")
    print(f"  Event 2: {EVENT_2_TITLE} (2h ago)")

    # Seed attendees
    print("\nSeeding calendar event attendees...")

    # Clear existing attendees for these events first
    cur.execute(
        "DELETE FROM calendar_event_attendees WHERE calendar_event_id IN (%s, %s)",
        (CALENDAR_EVENT_1_ID, CALENDAR_EVENT_2_ID),
    )

    for att in EVENT_1_ATTENDEES:
        cur.execute(
            """
            INSERT INTO calendar_event_attendees (
                id, calendar_event_id, tenant_id, email, display_name,
                is_organizer, response_status, is_resource, is_optional
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (calendar_event_id, email) DO NOTHING
            """,
            (
                str(uuid.uuid4()), CALENDAR_EVENT_1_ID, TENANT_ID,
                att["email"], att["display_name"] or None,
                att["is_organizer"], att["response_status"],
                att["is_resource"], att["is_optional"],
            ),
        )

    for att in EVENT_2_ATTENDEES:
        cur.execute(
            """
            INSERT INTO calendar_event_attendees (
                id, calendar_event_id, tenant_id, email, display_name,
                is_organizer, response_status, is_resource, is_optional
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (calendar_event_id, email) DO NOTHING
            """,
            (
                str(uuid.uuid4()), CALENDAR_EVENT_2_ID, TENANT_ID,
                att["email"], att["display_name"] or None,
                att["is_organizer"], att["response_status"],
                att["is_resource"], att["is_optional"],
            ),
        )

    print(f"  Event 1: {len(EVENT_1_ATTENDEES)} attendees (inc. 1 resource)")
    print(f"  Event 2: {len(EVENT_2_ATTENDEES)} attendees")

    # Seed pre-existing contacts (for find-existing test path)
    print("\nSeeding pre-existing contacts...")
    cur.execute(
        """
        INSERT INTO contacts (
            id, tenant_id, email, first_name, last_name,
            source, validation_status, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (tenant_id, email) DO NOTHING
        """,
        (
            EXISTING_CONTACT_1_ID, TENANT_ID,
            "jane.smith@acme-test.com", "Jane", "Smith",
            "manual", "verified",
        ),
    )

    # Second contact: email only, no name (test fill-NULL-names path)
    cur.execute(
        """
        INSERT INTO contacts (
            id, tenant_id, email, first_name, last_name,
            source, validation_status, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (tenant_id, email) DO NOTHING
        """,
        (
            EXISTING_CONTACT_2_ID, TENANT_ID,
            "bob.jones@acme-test.com", None, None,
            "email_pipeline", "pending",
        ),
    )

    print(f"  jane.smith@acme-test.com (verified, has name)")
    print(f"  bob.jones@acme-test.com (pending, no name)")

    conn.commit()
    print("\nSeed complete!")


def verify(conn):
    """Verify seeded data exists."""
    cur = conn.cursor()

    print("\n--- Calendar Events (test tenant) ---")
    cur.execute(
        """
        SELECT id, title, start_time, end_time, status, conference_join_url
        FROM calendar_events
        WHERE tenant_id = %s AND title LIKE '%%Enrichment Test%%'
        ORDER BY start_time DESC
        """,
        (TENANT_ID,),
    )
    for row in cur.fetchall():
        print(f"  {row}")

    print("\n--- Attendees for Test Events ---")
    cur.execute(
        """
        SELECT cea.email, cea.display_name, cea.is_organizer, cea.is_resource,
               ce.title
        FROM calendar_event_attendees cea
        JOIN calendar_events ce ON ce.id = cea.calendar_event_id
        WHERE ce.tenant_id = %s AND ce.title LIKE '%%Enrichment Test%%'
        ORDER BY ce.title, cea.is_organizer DESC
        """,
        (TENANT_ID,),
    )
    for row in cur.fetchall():
        print(f"  {row}")

    print("\n--- Contacts (enrichment-related) ---")
    cur.execute(
        """
        SELECT id, email, first_name, last_name, source, validation_status
        FROM contacts
        WHERE tenant_id = %s AND email IN (
            'jane.smith@acme-test.com', 'bob.jones@acme-test.com',
            'unknown.person@mystery-corp.com', 'alex.chen@partner-test.com'
        )
        ORDER BY email
        """,
        (TENANT_ID,),
    )
    for row in cur.fetchall():
        print(f"  {row}")

    print("\n--- Contacts created by enrichment ---")
    cur.execute(
        """
        SELECT id, email, first_name, last_name, source, validation_status
        FROM contacts
        WHERE tenant_id = %s AND source = 'transcript_enrichment'
        ORDER BY created_at DESC LIMIT 10
        """,
        (TENANT_ID,),
    )
    rows = cur.fetchall()
    if rows:
        for row in rows:
            print(f"  {row}")
    else:
        print("  (none yet)")

    print("\n--- Interaction Contact Links ---")
    cur.execute(
        """
        SELECT icl.link_id, icl.interaction_id, c.email, c.first_name
        FROM interaction_contact_links icl
        JOIN contacts c ON c.id = icl.contact_id
        WHERE c.tenant_id = %s
        ORDER BY icl.link_id DESC LIMIT 10
        """,
        (TENANT_ID,),
    )
    rows = cur.fetchall()
    if rows:
        for row in rows:
            print(f"  {row}")
    else:
        print("  (none yet)")


def clean(conn):
    """Remove seeded test data."""
    cur = conn.cursor()

    print("Cleaning enrichment test data...")

    # Remove contacts created by enrichment
    cur.execute(
        "DELETE FROM contacts WHERE tenant_id = %s AND source = 'transcript_enrichment'",
        (TENANT_ID,),
    )
    print(f"  Deleted {cur.rowcount} enrichment-created contacts")

    # Remove seeded pre-existing contacts
    cur.execute(
        "DELETE FROM contacts WHERE id IN (%s, %s)",
        (EXISTING_CONTACT_1_ID, EXISTING_CONTACT_2_ID),
    )
    print(f"  Deleted {cur.rowcount} seeded contacts")

    # Remove attendees (CASCADE from events handles this too)
    cur.execute(
        "DELETE FROM calendar_event_attendees WHERE calendar_event_id IN (%s, %s)",
        (CALENDAR_EVENT_1_ID, CALENDAR_EVENT_2_ID),
    )
    print(f"  Deleted {cur.rowcount} attendees")

    # Remove events
    cur.execute(
        "DELETE FROM calendar_events WHERE id IN (%s, %s)",
        (CALENDAR_EVENT_1_ID, CALENDAR_EVENT_2_ID),
    )
    print(f"  Deleted {cur.rowcount} events")

    conn.commit()
    print("Clean complete!")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()
    conn = psycopg2.connect(NEON_DSN)

    try:
        if cmd == "seed":
            seed(conn)
        elif cmd == "verify":
            verify(conn)
        elif cmd == "clean":
            clean(conn)
        else:
            print(f"Unknown command: {cmd}")
            print(__doc__)
            sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
