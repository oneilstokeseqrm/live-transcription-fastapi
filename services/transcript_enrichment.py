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
from models.participant_spec import ParticipantSpec
from services.database import get_async_session
from services.domain_classification import (
    DomainClass,
    classify_domain,
    email_domain,
)
from services.account_lookup import lookup_account_by_domain
from services.pending_account_mappings import (
    SignalProposal,
    insert_signal,
    reopen_archived_entry,
    upsert_queue_entry,
)

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
        recording_user_id: Optional[str] = None,
        tenant_internal_domains: Optional[set[str]] = None,
        participants: Optional[list[ParticipantSpec]] = None,
        interaction_id: Optional[str] = None,
    ) -> EnrichmentResult:
        """Enrich a transcript with calendar event contacts and front-matter.

        Per-attendee three-state branching (Option A — no orphan contacts):
        - PERSONAL domain → skip entirely (no contact, no signal).
        - INTERNAL domain → skip entirely (Phase 2 wires internal users).
        - BUSINESS + known account → create contact with looked-up account_id.
        - BUSINESS + unknown account → upsert pending_account_mappings + signal,
          NO contact. NEVER fall back to anchor account_id for unknown domains.

        Attendee-source semantics (Task 1.26.6 — caller-wins for `participants`):
        - `participants is None` + calendar match → use calendar attendees
          (the default flow; calendar is the source of truth).
        - `participants is None` + NO calendar match → no attendees,
          return calendar metadata-only enrichment (empty before Task 1.26.6;
          unchanged here).
        - `participants is not None` (including empty list) + NO calendar match
          → use `participants` as the attendee list (manual-notes flow). This
          unlocks contact resolution and queue signals for notes without a
          calendar event.
        - `participants is not None` (including empty list) + calendar match
          → `participants` OVERRIDES the calendar attendees for the three-state
          loop (caller-wins). Manual-notes flows are an explicit "here are the
          people who were in the room" signal that should win over the
          calendar's snapshot. Calendar match metadata (meeting_title,
          calendar_event_id, match_confidence) is still preserved.
        - `participants == []` is an explicit "no participants" — it does NOT
          fall back to calendar attendees. Distinct from `None`.

        Args:
            tenant_id: Tenant UUID string.
            transcript_timestamp: When the transcript was recorded.
            raw_transcript: The raw transcript text.
            existing_contact_ids: Pre-existing contact_ids (if any).
            conference_url: Conference join URL (desktop mode, strong match signal).
            user_name: Name of the recording user (for front-matter).
            account_id: Anchor account UUID (used ONLY for attendees whose
                email-domain resolves to this account; never as a fallback).
            recording_user_id: Postgres user_id of the recording user
                (`pg_user_id` if available, else upstream user_id). Owner of
                any pending_account_mappings rows created during enrichment.
            tenant_internal_domains: Per-tenant connected-provider domains.
                Defaults to empty (no internal-domain matches). Wiring this
                from provider_connections is a follow-up.
            participants: Caller-provided participants from the request body.
                See "Attendee-source semantics" above. `None` means "no signal,
                use calendar"; empty list means "explicit no participants,
                do not fall back". Trust note: this is a semi-trusted client
                signal (callers can claim arbitrary participants the same way
                they can fake a calendar invite); it does NOT override the
                authenticated account_id anchor.
            interaction_id: The request's interaction_id (per the ingress
                route's RequestContext / UploadJob / WS session). Used as the
                queue-signal anchor when no calendar event matched: the source
                of the signal IS the interaction itself in the manual-notes
                flow. Without this, `discovered_from_interaction_id` and
                `SignalProposal.interaction_id` would be NULL, which breaks
                `pending_signal_dedup` (SQL NULL != NULL) and lets retries
                duplicate rows. (Codex Round 4 P2 fix.)

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

            # Step 1b: Determine attendee source (Task 1.26.6 — caller-wins).
            # See docstring "Attendee-source semantics".
            event_id: Optional[str] = None
            meeting_title: Optional[str] = None
            match_method: str = "none"
            match_confidence: str = "none"
            enrichment_source: str = "none"
            attendees: list[dict] = []

            if event:
                event_id = str(event["id"])
                meeting_title = event["title"]
                match_method = event.get("_match_method", "time_window")
                match_confidence = "high" if match_method == "conference_url" else "medium"
                enrichment_source = "calendar_match"
                logger.info(
                    f"Calendar event matched: event_id={event_id[:8]}..., "
                    f"title={meeting_title}, method={match_method}, "
                    f"confidence={match_confidence}"
                )
            else:
                logger.info(
                    f"No calendar event match: tenant_id={tenant_id[:8]}..., "
                    f"ts={transcript_timestamp.isoformat()}"
                )
                if participants is None:
                    # No calendar AND no caller-provided participants — nothing
                    # to enrich. Unchanged pre-Task-1.26.6 behavior.
                    return EnrichmentResult()

            # Caller-wins: if participants were provided (even an empty list),
            # they REPLACE calendar attendees for the three-state loop.
            # `None` means "no signal — use calendar".
            if participants is not None:
                attendees = [_participant_to_attendee(p) for p in participants]
                if event_id:
                    logger.info(
                        f"Using caller-provided participants over calendar "
                        f"attendees: event_id={event_id[:8]}..., "
                        f"participant_count={len(attendees)}"
                    )
                else:
                    enrichment_source = "manual_participants"
                    logger.info(
                        f"Using caller-provided participants (no calendar match): "
                        f"tenant_id={tenant_id[:8]}..., "
                        f"participant_count={len(attendees)}"
                    )
            elif event_id:
                # Step 2: Get attendees from calendar (default path).
                attendees = await self._get_attendees(event_id, tenant_id)

            if not attendees:
                # Either calendar had no attendees, or caller explicitly passed
                # an empty participants list. Preserve calendar match metadata
                # if we have it; otherwise return empty.
                if event_id:
                    logger.info(
                        f"No attendees to process: event_id={event_id[:8]}..., "
                        f"participants_provided={participants is not None}"
                    )
                    return EnrichmentResult(
                        meeting_title=meeting_title,
                        calendar_event_id=event_id,
                        match_confidence=match_confidence,
                        match_method=match_method,
                        enrichment_source=enrichment_source,
                    )
                logger.info(
                    f"No attendees and no calendar match — returning empty enrichment: "
                    f"tenant_id={tenant_id[:8]}..."
                )
                return EnrichmentResult()

            # Cap attendees to prevent latency blowup
            if len(attendees) > ENRICHMENT_MAX_ATTENDEES:
                logger.warning(
                    f"Attendee count {len(attendees)} exceeds max {ENRICHMENT_MAX_ATTENDEES}, "
                    f"truncating (event_id={event_id[:8] + '...' if event_id else 'none'})"
                )
                attendees = attendees[:ENRICHMENT_MAX_ATTENDEES]

            # Step 3: Per-attendee three-state branching (Option A).
            # Domain classification decides:
            #   PERSONAL  → skip entirely (no contact, no signal).
            #   INTERNAL  → skip entirely (Phase 2 territory).
            #   BUSINESS  → lookup account by domain:
            #               HIT  → create contact with resolved account_id.
            #               MISS → queue signal-only, NEVER fall back to anchor.
            contacts: list[ResolvedContact] = []
            tavily_lookups = 0
            internal_domains: set[str] = (
                tenant_internal_domains if tenant_internal_domains is not None else set()
            )

            for att in attendees:
                email = att["email"].lower().strip()
                display_name = att.get("display_name") or ""
                is_organizer = att.get("is_organizer", False)
                is_optional = att.get("is_optional", False)

                role = "organizer" if is_organizer else ("optional" if is_optional else "attendee")

                domain = email_domain(email)
                if not domain:
                    logger.debug(f"Skipping malformed attendee email: {email!r}")
                    continue

                klass = classify_domain(domain, internal_domains=internal_domains)
                event_id_log = f"{event_id[:8]}..." if event_id else "none"
                if klass == DomainClass.PERSONAL:
                    logger.info(
                        f"Skipping personal-domain attendee: email={email}, "
                        f"event_id={event_id_log}"
                    )
                    continue
                if klass == DomainClass.INTERNAL:
                    logger.info(
                        f"Skipping internal-domain attendee (Phase 1 no-op): "
                        f"email={email}, event_id={event_id_log}"
                    )
                    # Phase 2 will wire internal-user contacts.
                    continue

                # BUSINESS domain — look up the account for this domain.
                async with get_async_session() as session:
                    resolved_account_id = await lookup_account_by_domain(
                        session=session,
                        tenant_id=tenant_id,
                        domain=domain,
                    )

                if resolved_account_id is not None:
                    # KNOWN ACCOUNT — create contact normally.
                    resolved = await self._resolve_contact(
                        tenant_id=tenant_id,
                        email=email,
                        display_name=display_name,
                        account_id=resolved_account_id,
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
                    continue

                # UNKNOWN BUSINESS DOMAIN — queue a signal; NO contact.
                # Defense-in-depth: silent drops are worse than loud errors.
                # All four ingress routes (/text/clean, /batch/process,
                # /upload/..., /listen) MUST pass recording_user_id wired
                # from the authenticated request context. A None here means
                # a caller skipped the wiring — fail loudly so the
                # regression cannot ship.
                if recording_user_id is None:
                    raise ValueError(
                        "recording_user_id is required for unknown-domain "
                        "queue insertion. Caller must pass the authenticated "
                        "user from the request context."
                    )

                # Codex Round 4 P2: in the manual-notes flow there's no
                # calendar event, so anchor the queue+signal rows to the
                # request's interaction_id. Without this fallback both
                # discovered_from_interaction_id and SignalProposal.interaction_id
                # would be NULL when event_id is None, and pending_signal_dedup
                # (unique on queue_id+contact_email+source_type+interaction_id+
                # calendar_event_id) cannot dedupe NULLs — retries would
                # accumulate duplicate signal rows. calendar_event_id stays
                # bound to event_id only (NULL when no calendar match), which
                # is the correct semantic for that column.
                signal_anchor_id = event_id or interaction_id
                async with get_async_session() as session:
                    reopened_id = await reopen_archived_entry(
                        session=session,
                        tenant_id=tenant_id,
                        domain=domain,
                    )
                    if reopened_id is not None:
                        queue_id = reopened_id
                    else:
                        queue_id = await upsert_queue_entry(
                            session=session,
                            tenant_id=tenant_id,
                            domain=domain,
                            owner_user_id=recording_user_id,
                            discovered_from_type="transcript",
                            discovered_from_interaction_id=signal_anchor_id,
                        )

                    await insert_signal(
                        session=session,
                        tenant_id=tenant_id,
                        queue_id=queue_id,
                        proposal=SignalProposal(
                            source_type="transcript",
                            source_user_id=recording_user_id,
                            interaction_id=signal_anchor_id,
                            calendar_event_id=event_id,
                            contact_email=email,
                            contact_display_name=display_name or None,
                            contact_role=role,
                        ),
                    )
                    await session.commit()
                    logger.info(
                        f"Queued pending_account_mappings signal: "
                        f"domain={domain}, email={email}, queue_id={queue_id}"
                    )

            contact_ids = [c.contact_id for c in contacts]
            new_count = sum(1 for c in contacts if c.is_new)

            logger.info(
                f"Contact resolution complete: resolved={len(contacts)}, "
                f"new={new_count}, "
                f"event_id={(event_id[:8] + '...') if event_id else 'none'}"
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
                enrichment_source=enrichment_source,
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

            # Create new contact.
            #
            # Under Option A (T1.21+T1.22), `_resolve_contact` is ONLY
            # reached for BUSINESS-domain attendees whose domain resolves
            # to a known account_id; the caller passes that resolved
            # account_id here. The orphan path that previously inherited
            # the anchor account for unknown-domain attendees is GONE —
            # those attendees now route to pending_account_mappings via
            # the per-attendee branching in enrich(), with NO contact
            # created.
            #
            # `validation_status="pending"` remains as the default for
            # newly created contacts because the Prisma enum only permits
            # pending|verified|discarded. Phase 2 design Section 7.4 covers
            # the schema-debt cleanup for a richer state model. The
            # name-unresolvable flag below is independent of the (now
            # removed) account-orphan concept.
            if not account_id:
                # Defense-in-depth: post-T1.21, only known-account paths
                # call into this helper. Loud failure makes regressions
                # impossible to ship silently.
                raise ValueError(
                    "transcript_enrichment: _resolve_contact called without "
                    "account_id; Option A forbids orphan contact creation."
                )
            new_id = uuid.uuid4()
            aid = uuid.UUID(account_id)

            await session.execute(
                text("""
                    INSERT INTO contacts (
                        id, tenant_id, email, first_name, last_name, account_id,
                        source, validation_status, created_at, updated_at
                    ) VALUES (
                        :id, :tenant_id, :email, :first_name, :last_name, :account_id,
                        :source, 'pending', NOW(), NOW()
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
                },
            )

            # Flag for review if name unresolvable (independent of account
            # resolution; this is Phase 2 schema-debt territory).
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


def _participant_to_attendee(p: ParticipantSpec) -> dict:
    """Adapt a ParticipantSpec to the attendee-dict shape the three-state
    branching loop already consumes.

    The loop reads `email`, `display_name`, `is_organizer`, `is_optional`.
    Calendar attendees include `response_status` and `is_resource` too, but
    the loop ignores those.

    ParticipantSpec.role is a richer enum (organizer/attendee/optional/
    sender/recipient); we collapse it back to the two boolean flags the
    loop expects. `sender`/`recipient` (email-context roles) fold to
    `attendee` since this is the meeting-context branching loop.
    """
    role = p.role
    return {
        "email": str(p.email),
        "display_name": p.display_name or "",
        "is_organizer": role == "organizer",
        "is_optional": role == "optional",
    }
