"""Transcript Contact Enrichment Service.

Matches transcripts to calendar events by time window, resolves attendees
to canonical contact records in Postgres, and composes YAML front-matter
for downstream LLM context.

Contact resolution happens ONCE here, before the envelope is published.
Downstream pipelines receive already-resolved contacts — they do NOT create contacts.

Critical invariant: Every contact ALWAYS carries a UUIDv4 contact_id from Postgres.
Names and emails are metadata attached to that ID, never standalone.
"""
import os
import re
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy import text

from models.enrichment_models import ResolvedContact, EnrichmentResult
from services.database import get_async_session

logger = logging.getLogger(__name__)

# --- Feature flags ---
ENABLE_TRANSCRIPT_ENRICHMENT = os.getenv("ENABLE_TRANSCRIPT_ENRICHMENT", "false").lower() == "true"
ENRICHMENT_INCLUDE_FRONT_MATTER = os.getenv("ENRICHMENT_INCLUDE_FRONT_MATTER", "true").lower() == "true"
ENRICHMENT_TIME_WINDOW_PRE_MINUTES = int(os.getenv("ENRICHMENT_TIME_WINDOW_PRE_MINUTES", "5"))
ENRICHMENT_TIME_WINDOW_POST_MINUTES = int(os.getenv("ENRICHMENT_TIME_WINDOW_POST_MINUTES", "15"))
ENRICHMENT_MAX_ATTENDEES = int(os.getenv("ENRICHMENT_MAX_ATTENDEES", "20"))
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# Generic/service email patterns to skip name resolution
_GENERIC_EMAIL_PREFIXES = frozenset({
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "info", "support", "admin", "sales", "help", "contact",
    "hello", "team", "notifications", "mailer-daemon",
    "postmaster", "webmaster", "billing", "feedback",
})


class TranscriptEnrichmentService:
    """Enriches transcripts with contact metadata from calendar events.

    Insertion point: AFTER raw transcript, BEFORE cleaning.
    """

    async def enrich(
        self,
        tenant_id: str,
        transcript_timestamp: datetime,
        raw_transcript: str,
        existing_contact_ids: Optional[list[str]] = None,
        conference_url: Optional[str] = None,
        user_name: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> EnrichmentResult:
        """Enrich a transcript with calendar event contacts and front-matter.

        Args:
            tenant_id: Tenant UUID string.
            transcript_timestamp: When the transcript was recorded.
            raw_transcript: The raw transcript text.
            existing_contact_ids: Pre-existing contact_ids (if any).
            conference_url: Conference join URL (desktop mode, strong match signal).
            user_name: Name of the recording user (for front-matter).
            account_id: Optional account UUID for new contacts.

        Returns:
            EnrichmentResult with resolved contacts, front-matter, and metadata.
        """
        if not ENABLE_TRANSCRIPT_ENRICHMENT:
            logger.debug("Transcript enrichment disabled via feature flag")
            return EnrichmentResult()

        try:
            # Step 1: Match calendar event
            event = await self._match_calendar_event(
                tenant_id=tenant_id,
                transcript_ts=transcript_timestamp,
                conference_url=conference_url,
            )

            if not event:
                logger.info(
                    f"No calendar event match: tenant_id={tenant_id[:8]}..., "
                    f"ts={transcript_timestamp.isoformat()}"
                )
                return EnrichmentResult()

            event_id = str(event["id"])
            meeting_title = event["title"]
            match_method = event.get("_match_method", "time_window")
            match_confidence = "high" if match_method == "conference_url" else "medium"

            logger.info(
                f"Calendar event matched: event_id={event_id[:8]}..., "
                f"title={meeting_title}, method={match_method}, "
                f"confidence={match_confidence}"
            )

            # Step 2: Get attendees
            attendees = await self._get_attendees(event_id, tenant_id)

            if not attendees:
                logger.info(f"No attendees for event: event_id={event_id[:8]}...")
                return EnrichmentResult(
                    meeting_title=meeting_title,
                    calendar_event_id=event_id,
                    match_confidence=match_confidence,
                    match_method=match_method,
                    enrichment_source="calendar_match",
                )

            # Cap attendees to prevent latency blowup
            if len(attendees) > ENRICHMENT_MAX_ATTENDEES:
                logger.warning(
                    f"Attendee count {len(attendees)} exceeds max {ENRICHMENT_MAX_ATTENDEES}, "
                    f"truncating for event_id={event_id[:8]}..."
                )
                attendees = attendees[:ENRICHMENT_MAX_ATTENDEES]

            # Step 3: Resolve each attendee to a contact
            contacts: list[ResolvedContact] = []
            tavily_lookups = 0

            for att in attendees:
                email = att["email"].lower().strip()
                display_name = att.get("display_name") or ""
                is_organizer = att.get("is_organizer", False)
                is_optional = att.get("is_optional", False)

                role = "organizer" if is_organizer else ("optional" if is_optional else "attendee")

                resolved = await self._resolve_contact(
                    tenant_id=tenant_id,
                    email=email,
                    display_name=display_name,
                    account_id=account_id,
                    tavily_lookups=tavily_lookups,
                )
                tavily_lookups = resolved.get("tavily_lookups", tavily_lookups)

                contacts.append(ResolvedContact(
                    contact_id=resolved["contact_id"],
                    email=email,
                    name=resolved["name"],
                    role=role,
                    is_new=resolved["is_new"],
                ))

            contact_ids = [c.contact_id for c in contacts]
            new_count = sum(1 for c in contacts if c.is_new)

            logger.info(
                f"Contact resolution complete: resolved={len(contacts)}, "
                f"new={new_count}, event_id={event_id[:8]}..."
            )

            # Step 4: Build front-matter
            front_matter = None
            if ENRICHMENT_INCLUDE_FRONT_MATTER:
                front_matter = self._compose_front_matter(
                    meeting_title=meeting_title,
                    transcript_timestamp=transcript_timestamp,
                    contacts=contacts,
                    user_name=user_name,
                    account_name=None,  # Could resolve from account_id later
                )

            return EnrichmentResult(
                contacts=contacts,
                contact_ids=contact_ids,
                meeting_title=meeting_title,
                calendar_event_id=event_id,
                front_matter=front_matter,
                match_confidence=match_confidence,
                match_method=match_method,
                new_contacts_created=new_count,
                enrichment_source="calendar_match",
            )

        except Exception as e:
            logger.error(
                f"Enrichment failed (non-fatal): tenant_id={tenant_id[:8]}..., "
                f"error={type(e).__name__}: {e}",
                exc_info=True,
            )
            return EnrichmentResult()

    # ------------------------------------------------------------------
    # Calendar event matching
    # ------------------------------------------------------------------

    async def _match_calendar_event(
        self,
        tenant_id: str,
        transcript_ts: datetime,
        conference_url: Optional[str] = None,
    ) -> Optional[dict]:
        """Find the best-matching calendar event for a transcript timestamp.

        Matching strategy:
        1. If conference_url provided, match on that first (strong signal).
        2. Fall back to time window match (start_time - 5min to end_time + 15min).

        Returns:
            dict with event columns, or None if no match.
        """
        tid = uuid.UUID(tenant_id)

        # Try conference URL match first (strong signal)
        if conference_url:
            event = await self._match_by_conference_url(tid, conference_url, transcript_ts)
            if event:
                event["_match_method"] = "conference_url"
                return event

        # Fall back to time window match
        event = await self._match_by_time_window(tid, transcript_ts)
        if event:
            event["_match_method"] = "time_window"
            return event

        return None

    async def _match_by_conference_url(
        self,
        tenant_id: uuid.UUID,
        conference_url: str,
        transcript_ts: datetime,
    ) -> Optional[dict]:
        """Match by conference join URL within a generous time window."""
        async with get_async_session() as session:
            result = await session.execute(
                text("""
                    SELECT id, title, start_time, end_time, conference_join_url, status
                    FROM calendar_events
                    WHERE tenant_id = :tenant_id
                      AND conference_join_url = :conference_url
                      AND start_time <= :ts_upper
                      AND end_time >= :ts_lower
                      AND status = 'confirmed'
                    ORDER BY ABS(EXTRACT(EPOCH FROM (start_time - :transcript_ts)))
                    LIMIT 1
                """),
                {
                    "tenant_id": tenant_id,
                    "conference_url": conference_url,
                    "ts_upper": transcript_ts + timedelta(minutes=30),
                    "ts_lower": transcript_ts - timedelta(minutes=30),
                    "transcript_ts": transcript_ts,
                },
            )
            row = result.mappings().first()
            return dict(row) if row else None

    async def _match_by_time_window(
        self,
        tenant_id: uuid.UUID,
        transcript_ts: datetime,
    ) -> Optional[dict]:
        """Match by time window overlap."""
        async with get_async_session() as session:
            result = await session.execute(
                text("""
                    SELECT id, title, start_time, end_time, conference_join_url, status
                    FROM calendar_events
                    WHERE tenant_id = :tenant_id
                      AND start_time <= :ts_upper
                      AND end_time >= :ts_lower
                      AND status = 'confirmed'
                    ORDER BY ABS(EXTRACT(EPOCH FROM (start_time - :transcript_ts)))
                    LIMIT 1
                """),
                {
                    "tenant_id": tenant_id,
                    "ts_upper": transcript_ts + timedelta(minutes=ENRICHMENT_TIME_WINDOW_PRE_MINUTES),
                    "ts_lower": transcript_ts - timedelta(minutes=ENRICHMENT_TIME_WINDOW_POST_MINUTES),
                    "transcript_ts": transcript_ts,
                },
            )
            row = result.mappings().first()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Attendee retrieval
    # ------------------------------------------------------------------

    async def _get_attendees(self, calendar_event_id: str, tenant_id: str) -> list[dict]:
        """Retrieve non-resource attendees for a calendar event.

        Uses a JOIN back to calendar_events for tenant isolation defense-in-depth.
        """
        event_uuid = uuid.UUID(calendar_event_id)
        tid = uuid.UUID(tenant_id)
        async with get_async_session() as session:
            result = await session.execute(
                text("""
                    SELECT cea.email, cea.display_name, cea.is_organizer,
                           cea.response_status, cea.is_resource, cea.is_optional
                    FROM calendar_event_attendees cea
                    JOIN calendar_events ce ON ce.id = cea.calendar_event_id
                    WHERE cea.calendar_event_id = :event_id
                      AND ce.tenant_id = :tenant_id
                      AND cea.is_resource = false
                    ORDER BY cea.is_organizer DESC, cea.email ASC
                """),
                {"event_id": event_uuid, "tenant_id": tid},
            )
            return [dict(row) for row in result.mappings().all()]

    # ------------------------------------------------------------------
    # Contact resolution (find-or-create in Postgres)
    # ------------------------------------------------------------------

    async def _resolve_contact(
        self,
        tenant_id: str,
        email: str,
        display_name: str = "",
        account_id: Optional[str] = None,
        tavily_lookups: int = 0,
    ) -> dict:
        """Find or create a contact by (tenant_id, email).

        Mirrors eq-email-pipeline's find_or_create_contact pattern:
        - On match: fill NULL name fields only (never overwrite manual data).
        - On create: generate UUIDv4, set source="transcript_enrichment".

        Returns:
            dict with contact_id, name, is_new, tavily_lookups.
        """
        tid = uuid.UUID(tenant_id)
        email_lower = email.lower().strip()
        first_name, last_name = self._split_display_name(display_name)

        # If no name from display_name, try heuristic and Tavily
        if not first_name:
            heuristic_name = self._name_from_email_heuristic(email_lower)
            if heuristic_name:
                first_name, last_name = heuristic_name
            elif tavily_lookups < 5 and TAVILY_API_KEY:
                tavily_name = await self._tavily_name_lookup(email_lower)
                tavily_lookups += 1
                if tavily_name:
                    first_name, last_name = tavily_name

        async with get_async_session() as session:
            # Try to find existing contact
            result = await session.execute(
                text("""
                    SELECT id, first_name, last_name FROM contacts
                    WHERE tenant_id = :tenant_id AND email = :email
                """),
                {"tenant_id": tid, "email": email_lower},
            )
            row = result.mappings().first()

            if row:
                contact_id = str(row["id"])
                # Fill NULL name fields only
                updates = {}
                if not row["first_name"] and first_name:
                    updates["first_name"] = first_name
                if not row["last_name"] and last_name:
                    updates["last_name"] = last_name

                if updates:
                    set_parts = []
                    params: dict = {"tid": tid, "cid": uuid.UUID(contact_id)}
                    for key, val in updates.items():
                        set_parts.append(f"{key} = :{key}")
                        params[key] = val
                    set_clause = ", ".join(set_parts)
                    await session.execute(
                        text(
                            f"UPDATE contacts SET {set_clause}, updated_at = NOW() "
                            f"WHERE tenant_id = :tid AND id = :cid"
                        ),
                        params,
                    )
                    await session.commit()

                existing_name = _build_full_name(
                    row["first_name"] or first_name,
                    row["last_name"] or last_name,
                )
                return {
                    "contact_id": contact_id,
                    "name": existing_name,
                    "is_new": False,
                    "tavily_lookups": tavily_lookups,
                }

            # Create new contact
            new_id = uuid.uuid4()
            aid = uuid.UUID(account_id) if account_id else None
            # Always "pending" — Prisma enum only allows pending|verified|discarded.
            # Name-unresolvable contacts are flagged via pending_validations table below.
            validation_status = "pending"

            await session.execute(
                text("""
                    INSERT INTO contacts (
                        id, tenant_id, email, first_name, last_name, account_id,
                        source, validation_status, created_at, updated_at
                    ) VALUES (
                        :id, :tenant_id, :email, :first_name, :last_name, :account_id,
                        :source, :validation_status, NOW(), NOW()
                    )
                """),
                {
                    "id": new_id,
                    "tenant_id": tid,
                    "email": email_lower,
                    "first_name": first_name or None,
                    "last_name": last_name or None,
                    "account_id": aid,
                    "source": "transcript_enrichment",
                    "validation_status": validation_status,
                },
            )

            # Flag for review if name unresolvable
            if not first_name:
                await self._flag_pending_validation(
                    session, tid, new_id, "name_unresolvable"
                )

            await session.commit()

            return {
                "contact_id": str(new_id),
                "name": _build_full_name(first_name, last_name),
                "is_new": True,
                "tavily_lookups": tavily_lookups,
            }

    async def _flag_pending_validation(
        self,
        session,
        tenant_id: uuid.UUID,
        contact_id: uuid.UUID,
        reason: str,
    ) -> None:
        """Insert a pending_validations row for contacts needing review."""
        try:
            await session.execute(
                text("""
                    INSERT INTO pending_validations (
                        id, tenant_id, entity_type, entity_id,
                        validation_reason, validation_status, created_at, updated_at
                    ) VALUES (
                        :id, :tenant_id, :entity_type, :entity_id,
                        :reason, :status, NOW(), NOW()
                    )
                """),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "entity_type": "contact",
                    "entity_id": contact_id,
                    "reason": reason,
                    "status": "pending",
                },
            )
        except Exception as e:
            logger.warning(f"Failed to flag pending validation: {e}")

    # ------------------------------------------------------------------
    # Name resolution helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_display_name(display_name: Optional[str]) -> tuple[str, str]:
        """Split display_name into (first_name, last_name).

        Matches eq-email-pipeline's split_display_name pattern.
        """
        if not display_name:
            return ("", "")
        parts = display_name.strip().split()
        if not parts:
            return ("", "")
        if len(parts) == 1:
            return (parts[0], "")
        return (" ".join(parts[:-1]), parts[-1])

    @staticmethod
    def _name_from_email_heuristic(email: str) -> Optional[tuple[str, str]]:
        """Try to extract a name from the email local part.

        Examples:
            jane.smith@company.com → ("Jane", "Smith")
            jsmith@company.com → None (not confident enough)
            noreply@company.com → None (generic prefix)
        """
        local_part = email.split("@")[0].lower()

        # Skip generic/service emails
        if local_part in _GENERIC_EMAIL_PREFIXES:
            return None

        # Split on common separators
        parts = re.split(r'[.\-_]', local_part)
        parts = [p for p in parts if p]

        if len(parts) < 2:
            return None

        # Skip if any part looks like a number or single char abbreviation
        if any(len(p) <= 1 or p.isdigit() for p in parts):
            return None

        # Title-case each part
        first = " ".join(p.capitalize() for p in parts[:-1])
        last = parts[-1].capitalize()
        return (first, last)

    @staticmethod
    async def _tavily_name_lookup(email: str) -> Optional[tuple[str, str]]:
        """Use Tavily Search API for a lightweight public name lookup.

        Bounded: 3-second timeout, fire-and-forget on failure.
        """
        if not TAVILY_API_KEY:
            return None

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": TAVILY_API_KEY,
                        "query": f'"{email}" site:linkedin.com OR site:crunchbase.com',
                        "search_depth": "basic",
                        "max_results": 1,
                    },
                )

                if resp.status_code != 200:
                    return None

                data = resp.json()
                results = data.get("results", [])
                if not results:
                    return None

                # Try to extract a name from the top result title
                title = results[0].get("title", "")
                # LinkedIn titles are often "Jane Smith - Title | LinkedIn"
                name_match = re.match(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)', title)
                if name_match:
                    name_str = name_match.group(1)
                    parts = name_str.split()
                    if len(parts) >= 2:
                        return (" ".join(parts[:-1]), parts[-1])

                return None

        except Exception as e:
            logger.debug(f"Tavily lookup failed for {email}: {e}")
            return None

    # ------------------------------------------------------------------
    # Front-matter composition
    # ------------------------------------------------------------------

    @staticmethod
    def _compose_front_matter(
        meeting_title: Optional[str],
        transcript_timestamp: datetime,
        contacts: list[ResolvedContact],
        user_name: Optional[str] = None,
        account_name: Optional[str] = None,
    ) -> str:
        """Compose YAML front-matter block for transcript context.

        Format mirrors eq-email-pipeline's front-matter pattern, adapted
        for meeting transcripts.
        """
        lines = ["---"]
        lines.append("type: meeting")

        if meeting_title:
            escaped = meeting_title.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'title: "{escaped}"')

        lines.append(f"date: {transcript_timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')}")

        if contacts:
            lines.append("attendees:")
            for c in contacts:
                parts = [f"  - {c.email}"]
                if c.name:
                    parts[0] += f" ({c.name})"
                if c.role == "organizer":
                    parts[0] += " [organizer]"
                elif c.role == "recorder":
                    parts[0] += " [recorder]"
                lines.append(parts[0])

        if user_name:
            lines.append(f"recorder: {user_name}")

        if account_name:
            lines.append(f"account: {account_name}")

        lines.append("---")
        return "\n".join(lines)


# --- Module-level helpers ---

def _build_full_name(first: Optional[str], last: Optional[str]) -> Optional[str]:
    """Build a full name string from first and last, or None if both empty."""
    parts = [p for p in (first, last) if p]
    return " ".join(parts) if parts else None
