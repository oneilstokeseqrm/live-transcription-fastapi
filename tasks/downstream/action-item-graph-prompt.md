# Agent Prompt: action-item-graph Contact Enrichment Changes

## What You Are Doing

You are implementing downstream changes in the `action-item-graph` repo (at `/Users/peteroneil/EQ-CORE/action-item-graph`) to leverage a new contact enrichment system built in `live-transcription-fastapi`. The enrichment system resolves transcript attendees to canonical contacts and sends full contact metadata (name, email, role) in envelope extras.

This is a **medium priority** change that significantly improves LLM extraction quality:
- Today the LLM sees `Participants: a1b2c3d4-e5f6-...` (opaque UUIDs) — useless for attribution
- After this change, the LLM sees `Meeting participants: Jane Smith <jane@acme.com> (organizer)` — can attribute action items and identify deal champions
- The owner resolver can pre-seed its cache with known contacts, improving name matching accuracy
- New graph relationships connect action items and deals to contacts

## Context: The Enrichment System

The `extras` dict in EnvelopeV1 now contains:

```python
extras = {
    "contact_ids": ["uuid1", "uuid2"],        # List of Postgres contact UUIDs (existing)
    "contacts": [                              # NEW — full metadata per contact
        {
            "contact_id": "uuid1",             # UUIDv4 from Postgres — ALWAYS present
            "email": "jane@acme.com",          # ALWAYS present
            "name": "Jane Smith",              # May be None for email-only contacts
            "role": "organizer"                # organizer | attendee | optional | sender | recipient | cc
        }
    ],
    "opportunity_id": "uuid-or-null",          # Existing field
    "meeting_title": "Q3 Pipeline Review",     # Existing field
    "user_name": "Pete O'Neil",                # Existing field
}
```

Both `live-transcription-fastapi` (transcripts) and `eq-email-pipeline` (emails) send this same format.

## Cross-Cutting Architectural Decisions

These decisions apply across ALL downstream services:

1. **This service does NOT create Contact nodes in Neo4j.** Contact nodes are created by eq-structured-graph-core and eq-email-pipeline. This service creates relationships TO Contact nodes (Owner→Contact, Contact→Deal) assuming the Contact node already exists or will exist shortly (MERGE handles this).

2. **MERGE-Everywhere Pattern**: This service, eq-structured-graph-core, and eq-email-pipeline all consume the same EventBridge envelope concurrently with NO guaranteed execution order. Use MERGE for relationship creation — first writer creates, subsequent writers match.

3. **Property SET Pattern for Enrichment**: When this service enriches a relationship created by another service (e.g., adding `role=champion` to an ENGAGED_ON relationship), use unconditional `SET` — not `ON CREATE SET`. This ensures enrichment always applies regardless of who created the relationship:
   ```cypher
   -- Enrichment: always applies
   MATCH (c)-[r:ENGAGED_ON]->(d)
   SET r.role = $role, r.confidence = $confidence, r.enriched_at = datetime()
   ```

4. **Interaction Property Loss Bug (EXISTING)**: This service uses `ON CREATE SET` for `user_id`, `pg_user_id`, `title`, `duration_seconds` on the Interaction node MERGE. If eq-structured-graph-core creates the node first (race condition), these properties are never written. Fix: use unconditional SET with COALESCE.

5. **New Relationship — Contact→Deal (ENGAGED_ON)**: When both `contact_ids` and `opportunity_id` are present, MERGE a `(Contact)-[:ENGAGED_ON]->(Deal)` relationship. eq-structured-graph-core also MERGEs this (MERGE-everywhere). After LLM deal extraction, ENRICH the relationship with `role` (champion, economic_buyer) using unconditional `SET`.

## Step-by-Step Instructions

### Step 1: Read Investigation Notes

Read the detailed investigation notes at:
- `/Users/peteroneil/live-transcription-fastapi/tasks/downstream/action-item-graph.md`

This contains the specific changes needed with file paths and rationale.

### Step 2: Read Current Source Code

Read these files to understand the current implementation:

1. **`src/action_item_graph/models/envelope.py`** — Focus on:
   - The `contact_ids` property (around lines 112-114) — how it reads from extras
   - Other properties that read from extras (meeting_title, user_name, etc.)
   - The EnvelopeV1 model structure

2. **`src/action_item_graph/pipeline/extractor.py`** — Focus on:
   - Where `envelope.contact_ids` is passed to the extraction call (around line 139)
   - The `participants` parameter — what it becomes in the LLM prompt
   - The `_extract_action_items()` method signature

3. **`src/action_item_graph/prompts/extract_action_items.py`** — Focus on:
   - How participants are formatted in the prompt (around lines 333-389)
   - The full prompt template structure
   - Where participant names would be most useful for attribution

4. **`src/action_item_graph/pipeline/owner_resolver.py`** — Focus on:
   - How the owner cache is built (around lines 151-317)
   - Where Neo4j owners are loaded
   - The matching algorithm (how it matches extracted owner text to known names)
   - Where you would pre-seed with contact names

5. **`src/action_item_graph/repository.py`** — Focus on:
   - `create_interaction()` (around lines 83-147) — the Interaction MERGE with property loss bug
   - Owner node creation (around lines 595-612) — Owner.contact_id exists but is unused
   - Existing relationship creation patterns — use these as templates

6. **`src/action_item_graph/clients/postgres_client.py`** — Focus on:
   - `link_action_item_to_entity()` (around lines 576-622) — supports entity_type='contact' but never called
   - Understand the signature and parameters

7. **`src/deal_graph/repository.py`** — Focus on:
   - Deal MERGE (around lines 237-252)
   - `ensure_interaction()` (around lines 178-189) — another Interaction MERGE with property loss
   - Existing relationship patterns

8. **`src/deal_graph/prompts/extract_deals.py`** — Focus on:
   - MEDDIC extraction prompt structure
   - Where champion/economic buyer are identified
   - Where contact names would improve extraction

9. **`src/action_item_graph/models/entities.py`** — Focus on:
   - `Contact` class (around lines 193-228) — defined but never used
   - `Owner` class — check for `contact_id` field

### Step 3: Implement Changes

**Change 1 — Read contact metadata from envelope:**

Add properties to the envelope model:
- `contacts` → returns `extras.get('contacts', [])`
- `contact_names` → returns human-readable names list (name if available, email as fallback)
- `opportunity_id` → returns `extras.get('opportunity_id')`

**Change 2 — Pass contact names (not UUIDs) to LLM:**

In `extractor.py`, change:
```python
participants=envelope.contact_ids if envelope.contact_ids else None
```
to:
```python
participants=envelope.contact_names if envelope.contact_names else None
```

**Change 3 — Format participants in extraction prompt:**

In the extraction prompt, when contacts are available, format as:
```
Meeting participants:
  - Jane Smith <jane@acme.com> (organizer)
  - Bob Jones <bob@acme.com> (attendee)
```

This replaces the current format of `Participants: uuid1, uuid2` which gives the LLM nothing to work with.

**Change 4 — Pre-seed owner resolver with contact names:**

Before the Neo4j owner cache load in the resolver, add known contacts from the envelope. This allows the resolver to match extracted owner names (e.g., "Jane" from LLM output) against known contacts ("Jane Smith"). The exact integration depends on the resolver's cache structure — read the code to find the right injection point.

**Change 5 — Create Owner→Contact link:**

Add a method to repository.py:
```python
async def link_owner_to_contact(self, owner_id: str, contact_id: str) -> bool:
```
This MERGEs `(Owner)-[:IDENTIFIES_AS]->(Contact)`. Call it after owner resolution when an Owner matches a known contact.

**Change 6 — MERGE Contact→Deal (ENGAGED_ON) base relationship:**

When both `contact_ids` and `opportunity_id` are present in the envelope, MERGE:
```cypher
MATCH (c:Contact {tenant_id: $tid, contact_id: $cid})
MATCH (d:Deal {tenant_id: $tid, opportunity_id: $oid})
MERGE (c)-[r:ENGAGED_ON]->(d)
ON CREATE SET r.created_at = datetime()
```

eq-structured-graph-core also MERGEs this — MERGE-everywhere pattern. Note: use MATCH (not MERGE) for Contact and Deal nodes — this service doesn't create Contact nodes. If the Contact doesn't exist yet (race condition with eq-structured-graph-core), the MATCH returns nothing and the relationship is skipped. It will be created on the next envelope.

**Change 7 — Enrich ENGAGED_ON with LLM-extracted roles:**

After deal extraction identifies champion/economic_buyer, enrich the relationship:
```cypher
MATCH (c:Contact {tenant_id: $tid, contact_id: $cid})
-[r:ENGAGED_ON]->(d:Deal {tenant_id: $tid, opportunity_id: $oid})
SET r.role = $role, r.confidence = $confidence, r.enriched_at = datetime()
```

Use unconditional SET — always applies regardless of who created the relationship.

To match extracted roles to contacts, use the contact names from the envelope. When the LLM identifies "Jane Smith" as champion, match against `contacts` array to find her `contact_id`.

**Change 8 — Fix Interaction property loss bug:**

In `repository.py` `create_interaction()`: change `user_id`, `pg_user_id`, `title`, `duration_seconds` from `ON CREATE SET` to unconditional `SET` with `COALESCE`.

In `deal_graph/repository.py` `ensure_interaction()`: same fix for `trace_id`.

**Change 9 — Use action_item_links with entity_type='contact':**

After owner→contact resolution succeeds, call:
```python
await postgres_client.link_action_item_to_entity(
    tenant_id=tid, action_item_id=ai_id,
    entity_type='contact', entity_id=contact_id,
)
```

**Change 10 — Pass contact names to deal extraction prompts:**

Include contact names in the MEDDIC prompt context so the LLM can identify champion/economic_buyer by matching against known participants.

### Step 4: DO NOT Change These

- **Do NOT create Contact nodes in Neo4j** — that's eq-structured-graph-core + eq-email-pipeline's job
- **Do NOT modify Postgres contacts table** — contact resolution happens upstream
- **Deal node MERGE** — already correct
- **ActionItem node MERGE** — already correct
- **Account→HAS_INTERACTION relationship** — already correct
- **EXTRACTED_FROM, OWNED_BY relationships** — already correct

### Step 5: Run Existing Tests

Run the full test suite. Focus on:
- Extractor tests — may need updating for `contact_names` instead of `contact_ids`
- Repository tests — may need updating for new methods
- Owner resolver tests — may need updating for pre-seeded cache
- Deal pipeline tests

### Step 6: Add New Tests

Add tests for:
- Envelope `contacts` and `contact_names` properties (with and without contacts array)
- LLM prompt includes participant names (not UUIDs)
- Owner resolver matches "Jane" to pre-seeded "Jane Smith" contact
- `(Owner)-[:IDENTIFIES_AS]->(Contact)` relationship created
- `(Contact)-[:ENGAGED_ON]->(Deal)` base relationship MERGEd
- ENGAGED_ON enriched with role after deal extraction
- Interaction property loss fix — user_id persisted regardless of creation order
- action_item_links with entity_type='contact' written
- Backward compatibility — envelopes without `contacts` array still work

### Step 7: Create Feature Branch and Commit

Create a feature branch (e.g., `feat/contact-enrichment-downstream`), commit with a descriptive message.

## Verification Checklist

Before marking complete, verify:
- [ ] Envelope model reads `contacts`, `contact_names`, `opportunity_id` from extras
- [ ] LLM extraction prompt shows real names (not UUIDs) for participants
- [ ] Owner resolver pre-seeded with contact names from envelope
- [ ] `(Owner)-[:IDENTIFIES_AS]->(Contact)` created when owner matches contact
- [ ] `(Contact)-[:ENGAGED_ON]->(Deal)` base relationship MERGEd when data available
- [ ] ENGAGED_ON enriched with `role`, `confidence`, `enriched_at` after LLM extraction
- [ ] Interaction `user_id`, `pg_user_id`, `title`, `duration_seconds` use COALESCE
- [ ] Deal pipeline `trace_id` uses COALESCE
- [ ] `action_item_links(entity_type='contact')` written when owner→contact resolved
- [ ] Deal extraction prompts include contact names
- [ ] No Contact node creation (only relationships TO Contact nodes)
- [ ] All existing tests pass
- [ ] Backward compatible with envelopes lacking `contacts` array
