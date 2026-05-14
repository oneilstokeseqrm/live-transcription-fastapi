# Agent Prompt: eq-structured-graph-core Contact Enrichment Changes

## What You Are Doing

You are implementing downstream changes in the `eq-structured-graph-core` repo to support a new contact enrichment system built in `live-transcription-fastapi`. The enrichment system resolves transcript attendees to canonical contacts in Postgres and sends full contact metadata in envelope extras. This pipeline needs to READ that metadata and POPULATE Contact node properties that are currently always NULL.

This is a **high priority** change because:
- Contact.name, Contact.email, Contact.role are always NULL in Neo4j today (this fixes it)
- The Phase 2 bridger (`EntityContactBridger`) queries Contact.name and Contact.email for entity-to-contact scoring — it always gets NULL, making scoring impossible
- A new `(Contact)-[:ENGAGED_ON]->(Deal)` relationship needs to be created

## Context: The Enrichment System

`live-transcription-fastapi` now matches transcripts to calendar events, resolves attendees to canonical contacts in Postgres, and publishes enriched envelopes. The `extras` dict in EnvelopeV1 now contains:

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
    "meeting_title": "Q3 Pipeline Review",
    "calendar_event_id": "uuid-of-calendar-event",
    "enrichment_source": "calendar_match",     # calendar_match | none
    "enrichment_confidence": "high",           # high | medium | none
}
```

Both `live-transcription-fastapi` (transcripts) and `eq-email-pipeline` (emails) send this same format. This pipeline should consume both identically.

## Cross-Cutting Architectural Decisions

These decisions apply across ALL downstream services. Read and internalize them before implementing:

1. **Contact MERGE Key**: This pipeline already uses `(tenant_id, contact_id)` — correct, no change needed. eq-email-pipeline is being updated separately to match this key.

2. **MERGE-Everywhere Pattern**: Multiple services consume the same EventBridge envelope concurrently with NO guaranteed execution order. This pipeline, action-item-graph, and eq-email-pipeline all process the same event independently. Use MERGE for all node and relationship creation — first writer creates, subsequent writers match.

3. **Property SET Pattern**: Use unconditional `SET` with `COALESCE` for properties that should always be populated, not `ON CREATE SET` which only fires if you win the race:
   ```cypher
   -- WRONG: lost if action-item-graph created the Interaction first
   ON CREATE SET i.content_format = $fmt

   -- CORRECT: always applied, preserves existing non-null values
   SET i.content_format = COALESCE(i.content_format, $fmt)
   ```

4. **New Relationship — Contact→Deal (ENGAGED_ON)**: When both `contact_ids` and `opportunity_id` are present, MERGE a `(Contact)-[:ENGAGED_ON]->(Deal)` relationship. This is a BASE relationship (no role properties). action-item-graph will later ENRICH it with role (champion, economic_buyer) using unconditional `SET`.

## Step-by-Step Instructions

### Step 1: Read Investigation Notes

Read the detailed investigation notes at:
- `/Users/peteroneil/live-transcription-fastapi/tasks/downstream/eq-structured-graph-core.md`

This contains the specific changes needed with file paths and rationale.

### Step 2: Read Current Source Code

Read these files to understand the current implementation:

1. **`app/models/envelope.py`** — Focus on:
   - `envelope_to_standard_interaction()` function (around lines 44-73)
   - How `contact_ids` is extracted from `extras` (around line 71)
   - What other fields are extracted from `extras`

2. **`app/models/interaction.py`** — Focus on:
   - `StandardInteraction` model (around lines 22-75)
   - The `contact_ids: list[str]` field (around line 58)
   - What other fields exist

3. **`app/db/queries/skeleton.py`** — This is the main file to modify. Focus on:
   - `_merge_contact()` method (around lines 189-201) — currently sets NO properties
   - `_merge_interaction()` method (around lines 167-187) — has the property loss bug
   - `build_skeleton()` method — the orchestrator that calls everything
   - `_create_contact_to_interaction_relationship()` (around lines 285-307) — ATTENDED/SENT/etc.
   - `_create_interaction_to_deal()` (around lines 309-325) — Interaction→Deal RELATED_TO
   - `_create_deal_to_account()` (around lines 327-343) — Deal→Account RELATED_TO
   - `_create_works_for()` (around lines 267-283) — Contact→Account WORKS_FOR

4. **`app/db/session.py`** — Focus on:
   - `merge_node()` method (around line 162) — understand how `key_props` and `other_props` are translated to Cypher
   - Specifically: does it use `ON CREATE SET` or unconditional `SET` for `other_props`? This determines whether you need to modify the session layer or handle COALESCE in the skeleton builder.
   - `create_relationship()` method — understand how relationships are MERGEd

5. **`app/models/nodes.py`** — Focus on:
   - `ContactNode` model (around lines 51-59) — has name, email, role fields defined but never populated

6. **`app/db/constraints.py`** — Focus on:
   - Contact constraint definition (around lines 101-106) — `(tenant_id, contact_id)` — correct, don't change

7. **`app/phase2/resolution/bridger.py`** — Focus on:
   - Entity-to-contact scoring (around lines 256-272) — queries Contact.name and Contact.email
   - Understand what it does so you can verify it will automatically benefit (no code changes needed here)

### Step 3: Implement Changes

**Change 1 — Read `contacts` array from envelope:**

In the envelope adapter (`envelope.py`), extract `extras.contacts` and `extras.opportunity_id` alongside the existing `extras.contact_ids`.

**Change 2 — Add fields to StandardInteraction:**

Add `contacts: list[dict] = []` and `opportunity_id: str | None = None` fields to the StandardInteraction model.

**Change 3 — Populate Contact node properties on MERGE:**

Modify `_merge_contact()` to accept optional metadata and SET `email_address`, `display_name`, `role` as properties. Build a lookup dict `{contact_id: metadata}` from `interaction.contacts` and pass the matching metadata when calling `_merge_contact()`.

**CRITICAL**: Check how `session.merge_node()` handles `other_props`. If it uses `ON CREATE SET`, you need to either:
- Modify the session layer to support COALESCE, OR
- Run a separate SET query after the MERGE to ensure properties are always applied

Properties must use COALESCE to avoid overwriting existing non-null values with null (e.g., if a later envelope has no name, don't wipe a name that was set by an earlier envelope).

**Change 4 — Add Contact→Deal (ENGAGED_ON) relationship:**

Add a new method `_create_contact_to_deal()` that MERGEs `(Contact)-[:ENGAGED_ON]->(Deal)`. Call it in `build_skeleton()` when both `contact_ids` and `opportunity_id` are present. This is a base relationship — only set `created_at` on CREATE. action-item-graph will enrich it with role properties later.

**Change 5 — Fix Interaction property loss bug:**

`_merge_interaction()` currently uses `ON CREATE SET` for properties like `content_format` and `trace_id`. If action-item-graph creates the Interaction node first (race condition), these properties are never written.

Fix by changing these to unconditional SET with COALESCE:
```cypher
SET i.content_format = COALESCE(i.content_format, $fmt),
    i.trace_id = COALESCE(i.trace_id, $trace)
```

The exact implementation depends on how `TenantSession.merge_node()` works. You may need to add a post-MERGE SET step, or modify `merge_node()` to support a `coalesce_props` parameter.

### Step 4: DO NOT Change These

- **Neo4j constraints** — `contact_unique` on `(tenant_id, contact_id)` is already correct
- **Phase 2 bridger** — No code changes needed. It reads Contact.name/email which will now be populated.
- **Contact→Interaction relationships** (ATTENDED, SENT, RECEIVED, CREATED) — already working correctly
- **WORKS_FOR relationship** — already working correctly
- **Deal→Interaction (RELATED_TO)** — already working correctly
- **Deal→Account (RELATED_TO)** — already working correctly
- **Postgres tables** — this service doesn't write to Postgres

### Step 5: Run Existing Tests

Run the full test suite. Focus on:
- Skeleton builder tests — may need updating for new `_merge_contact()` signature
- Envelope adapter tests — may need updating for new fields
- Any integration tests that verify Contact node creation

### Step 6: Add New Tests

Add tests for:
- Contact MERGE populates `email_address`, `display_name`, `role` from contacts array
- Contact MERGE with missing metadata (no `contacts` array) still works (backward compatible)
- COALESCE behavior: existing non-null values not overwritten by null
- ENGAGED_ON relationship created when both contact_ids and opportunity_id present
- ENGAGED_ON relationship NOT created when opportunity_id is missing
- Interaction property loss fix: content_format and trace_id persisted regardless of creation order

### Step 7: Create Feature Branch and Commit

Create a feature branch (e.g., `feat/contact-enrichment-downstream`), commit with a descriptive message.

## Verification Checklist

Before marking complete, verify:
- [ ] Envelope adapter reads `extras.contacts` array and `extras.opportunity_id`
- [ ] StandardInteraction has `contacts` and `opportunity_id` fields
- [ ] `_merge_contact()` accepts metadata and SETs `email_address`, `display_name`, `role`
- [ ] Properties use COALESCE — never overwrite existing non-null values
- [ ] `build_skeleton()` builds a `contact_lookup` dict and passes metadata to `_merge_contact()`
- [ ] `_create_contact_to_deal()` MERGEs `(Contact)-[:ENGAGED_ON]->(Deal)` base relationship
- [ ] ENGAGED_ON only created when both `contact_ids` and `opportunity_id` present
- [ ] Interaction `content_format` and `trace_id` use unconditional SET with COALESCE
- [ ] Phase 2 bridger NOT modified (it auto-benefits)
- [ ] Contact constraint NOT modified (already correct)
- [ ] All existing tests pass
- [ ] New tests cover contact metadata, ENGAGED_ON, and property loss fix
- [ ] Backward compatible — envelopes without `contacts` array still work (empty list default)
