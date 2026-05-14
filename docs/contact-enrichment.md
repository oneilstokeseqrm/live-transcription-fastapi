# Contact Enrichment

**Date**: 2026-03-17
**Status**: Implemented, E2E verified in production, all downstream services redeployed 2026-04-01
**Related**: eq-frontend Prisma schema (CalendarEventInteractionLink migration), downstream consumers (eq-structured-graph-core, action-item-graph, opportunity-forecasting, eq-email-pipeline)
**See also**: [contacts-architecture.md](contacts-architecture.md) — comprehensive cross-service contacts architecture

## Context

Transcripts ingested by this service carry no attendee/contact metadata. Meanwhile, eq-email-pipeline's calendar ingestion persists events with full attendee lists to Postgres (`calendar_events`, `calendar_event_attendees`). By matching transcripts to calendar events and resolving attendees to canonical contact records, we attach structured contact metadata to every enriched transcript.

## What It Does

1. **Calendar event matching** — Matches transcript timestamp against `calendar_events` using a configurable time window (default: start-5min to end+15min). Conference URL match is a strong signal when available.

2. **Contact resolution** — For each attendee, queries Postgres `contacts` by `(tenant_id, email)`. Creates new contacts if not found (with UUIDv4 `contact_id`, `source='transcript_enrichment'`). Every contact always carries a canonical `contact_id`.

3. **Front-matter composition** — Prepends a YAML header to the transcript before LLM cleaning, giving GPT-4o attendee context for speaker attribution:
   ```yaml
   ---
   type: meeting
   title: "Q3 Pipeline Review"
   date: 2026-03-12T14:00:00Z
   attendees:
     - jane@acme.com (Jane Smith) [organizer]
     - bob@acme.com (Bob Jones)
   ---
   ```

4. **Enriched envelope** — Lane 1 publishes to EventBridge with `extras.contact_ids` (UUID list) and `extras.contacts` (full metadata array with contact_id, email, name, role). Downstream consumers use this structured data.

5. **Postgres contact links** — Lane 2 (intelligence service) persists `interaction_contact_links` and `calendar_event_interaction_links` after creating placeholder rows in `raw_interactions` and `interaction_summaries` to satisfy the FK chain.

## Data Flow

```
Transcript submitted
       |
       v
TranscriptEnrichmentService.enrich()
  1. Match calendar event (time window + tenant_id)
  2. Resolve each attendee → Postgres contact (find-or-create)
  3. Build front-matter (YAML header)
  4. Return EnrichmentResult with contacts, contact_ids, meeting_title
       |
       v
Front-matter + transcript → GPT-4o cleaning
       |
  +----+----+
  |         |
  v         v
Lane 1    Lane 2
Envelope  IntelligenceService
  → EventBridge    → interaction_insights
  extras:          → interaction_summary_entries
    contact_ids    → raw_interactions (placeholder)
    contacts[]     → interaction_summaries (placeholder)
    meeting_title  → interaction_contact_links
    calendar_event_id  → calendar_event_interaction_links
```

## Files

| File | Purpose |
|------|---------|
| `services/transcript_enrichment.py` | Core: calendar match, contact resolution, front-matter |
| `models/enrichment_models.py` | ResolvedContact, EnrichmentResult, to_extras_dict() |
| `models/calendar_models.py` | Read-only schema docs for calendar tables |
| `services/intelligence_service.py` | _persist_contact_links (FK chain: raw_interactions → interaction_summaries → contact links) |

## Integration Points

| Endpoint | Where enrichment is called |
|----------|--------------------------|
| `/text/clean` | `routers/text.py` — before cleaning |
| `/listen` (WebSocket) | `main.py` — during finalization |
| `/batch/process` | `routers/batch.py` — before cleaning |
| `/upload/complete` | `routers/upload.py` — during processing |

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `ENABLE_TRANSCRIPT_ENRICHMENT` | `false` | Kill switch (set `true` on Railway) |
| `ENRICHMENT_INCLUDE_FRONT_MATTER` | `true` | Toggle YAML front-matter prepend |
| `TAVILY_API_KEY` | — | Optional: public name lookup for email-only contacts |

## FK Chain (Important)

`interaction_contact_links.interaction_id` FKs to `interaction_summaries.summary_id` (not to raw interaction_id). The full chain:

```
raw_interactions.interaction_id       ← must exist first
  → interaction_summaries.interaction_id  (FK to raw_interactions)
    → interaction_contact_links.interaction_id  (FK to interaction_summaries.summary_id)
```

The intelligence service creates placeholder rows with `ON CONFLICT DO NOTHING` for `raw_interactions` (safe for concurrent consumers) and a new `summary_id` for `interaction_summaries`.

## Downstream Consumers

All consume the enriched EnvelopeV1 via EventBridge → SQS:

| Service | What it does with contacts | PR |
|---------|--------------------------|-----|
| eq-structured-graph-core | MERGEs Contact nodes with metadata, creates ATTENDED/ENGAGED_ON/WORKS_FOR relationships | #9 |
| action-item-graph | Uses contact names in LLM extraction prompts, seeds owner resolver | #8 |
| opportunity-forecasting | Reads Contact→ENGAGED_ON→Deal and Contact→ATTENDED→Interaction from Neo4j | #14 |
| eq-email-pipeline | Standardized Contact MERGE key to contact_id, added contacts metadata to envelopes | #2 |

## Future Work

- **Owner→Contact linking quality** — action-item-graph's fuzzy matching for Owner→IDENTIFIES_AS→Contact needs tuning before it produces reliable results. Should not be forced.
- **ENGAGED_ON role enrichment** — LLM extraction of champion/economic_buyer roles on the Contact→Deal relationship is aspirational. Base ENGAGED_ON relationship works; role enrichment depends on transcript content quality.
- **opportunity-forecasting live verification** — Code merged but pipeline is pg_notify-triggered, not EventBridge. Needs a real opportunity update to exercise the new Neo4j contact engagement queries.
- **interaction_summary_contacts** — Deferred; wrong granularity for calendar-based enrichment.
