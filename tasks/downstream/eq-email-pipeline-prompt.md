# Agent Prompt: eq-email-pipeline Contact Enrichment Changes

## What You Are Doing

You are implementing downstream changes in the `eq-email-pipeline` repo to support a new contact enrichment system built in `live-transcription-fastapi`. This is the **highest priority** downstream change because it fixes two root-cause problems:

1. **Contact nodes in Neo4j have NULL name/email** — because the email pipeline only sends bare `contact_ids` (UUIDs) in envelope extras, not the contact metadata (email, name, role). Downstream consumers (eq-structured-graph-core) can only populate what they receive.

2. **Duplicate Contact nodes in Neo4j** — this pipeline MERGEs Contact nodes by `(tenant_id, email_address)` while eq-structured-graph-core MERGEs by `(tenant_id, contact_id)`. Same person ends up as two separate nodes.

## Context: The Enrichment System

`live-transcription-fastapi` now matches transcripts to calendar events, resolves attendees to canonical contacts in Postgres, and publishes enriched envelopes with a `contacts` metadata array in `extras`:

```python
extras = {
    "contact_ids": ["uuid1", "uuid2"],        # List of Postgres contact UUIDs
    "contacts": [                              # Full metadata per contact
        {
            "contact_id": "uuid1",             # UUIDv4 from Postgres — ALWAYS present
            "email": "jane@acme.com",          # ALWAYS present
            "name": "Jane Smith",              # May be None for email-only contacts
            "role": "organizer"                # organizer | attendee | optional | sender | recipient | cc
        }
    ],
    "meeting_title": "Q3 Pipeline Review",
    "calendar_event_id": "uuid-of-calendar-event",
    "enrichment_source": "calendar_match",     # calendar_match | none
    "enrichment_confidence": "high",           # high | medium | none
    "opportunity_id": "uuid-or-null",
}
```

The email pipeline needs to send this **same `contacts` array format** so that downstream consumers (eq-structured-graph-core, action-item-graph) can process emails and transcripts identically.

## Cross-Cutting Architectural Decisions

These decisions apply across ALL downstream services. Read and internalize them before implementing:

1. **Contact MERGE Key Standardization**: ALL services must MERGE Contact nodes in Neo4j by `(tenant_id, contact_id)` — never by `(tenant_id, email_address)`. `contact_id` is the Postgres canonical identity. `email_address` is a property, not a key. This pipeline currently uses the WRONG key and must change.

2. **MERGE-Everywhere Pattern**: Multiple services (eq-structured-graph-core, eq-email-pipeline, action-item-graph) consume the same EventBridge envelope concurrently with NO guaranteed execution order. Any service that has the data to create a node or relationship should MERGE it. MERGE is idempotent — first writer creates, subsequent writers match. No coordination needed.

3. **Property SET Pattern**: Use unconditional `SET` with `COALESCE` for properties that should always be populated, not `ON CREATE SET` which only fires if you win the race. Example:
   ```cypher
   -- WRONG: lost if another service created the node first
   ON CREATE SET c.display_name = $name

   -- CORRECT: always applied, preserves existing non-null values
   SET c.display_name = COALESCE(c.display_name, $name)
   ```

## Step-by-Step Instructions

### Step 1: Read Investigation Notes

Read the detailed investigation notes at:
- `/Users/peteroneil/live-transcription-fastapi/tasks/downstream/eq-email-pipeline.md`

This contains the specific changes needed with file paths and rationale.

### Step 2: Read Current Source Code

Read these files in the eq-email-pipeline repo to understand the current implementation:

1. **`src/pipeline/skeleton.py`** — Focus on:
   - The Contact node MERGE query (around lines 75-102) — this is what needs to change from email_address to contact_id key
   - The Contact→Interaction relationship creation (SENT, RECEIVED, ATTENDED — around lines 220-296)
   - How `contact_id_map` is used to look up contact UUIDs by email

2. **`src/persistence/neo4j.py`** — Focus on:
   - The `contact_email_unique` constraint definition (around lines 52-54) — this needs to change
   - The `ensure_constraints()` method

3. **`src/pipeline/emit.py`** — Focus on:
   - `build_email_envelope()` (around lines 145-198) — where `contact_ids` is added to extras
   - `build_calendar_envelope()` (around lines 91-143) — also needs `contacts` parameter

4. **`src/pipeline/orchestrator.py`** — Focus on:
   - Contact resolution loop (around lines 172-180) — where `find_or_create_contact()` is called per participant
   - Envelope building (around lines 249-267) — where `contact_ids` is passed to `build_email_envelope()`
   - What metadata is available during the loop (email, display_name, role)

5. **`src/persistence/postgres.py`** — Focus on:
   - `find_or_create_contact()` (around lines 72-135) — DO NOT MODIFY this function, just understand what it returns

6. **`src/pipeline/calendar_sync.py`** — Focus on:
   - Where `build_calendar_envelope()` is called — currently does NOT pass contact_ids

### Step 3: Implement Changes

**Change 1 — Fix Contact MERGE key in `skeleton.py`:**

Change the Contact node MERGE from:
```cypher
MERGE (c:Contact {tenant_id: $tenant_id, email_address: $email})
```
to:
```cypher
MERGE (c:Contact {tenant_id: $tenant_id, contact_id: $contact_id})
```

Set `email_address` as a property (ON CREATE SET + COALESCE on match). The contact_id is always available because `find_or_create_contact()` always returns a UUID.

Make sure the function signature accepts `contact_id` as a required parameter (it's currently available via `contact_id_map`).

**Change 2 — Update Neo4j constraint in `neo4j.py`:**

Drop `contact_email_unique` on `(tenant_id, email_address)`. Create `contact_unique` on `(tenant_id, contact_id)` if it doesn't already exist (eq-structured-graph-core may have already created it).

**Change 3 — Add `contacts` metadata to envelope in `emit.py`:**

Add a `contacts` parameter to `build_email_envelope()` and `build_calendar_envelope()`. Include it in the `extras` dict alongside `contact_ids`.

**Change 4 — Build `contacts` list in `orchestrator.py`:**

During the existing contact resolution loop where you iterate over participants and call `find_or_create_contact()`, also build a `contacts` metadata list:
```python
{
    "contact_id": contact_uuid,
    "email": participant.email.lower(),
    "name": participant.display_name or None,
    "role": "sender" | "recipient" | "cc"  # determined by participant position
}
```

Pass this list to `build_email_envelope()`.

**Change 5 — Calendar sync contact resolution in `calendar_sync.py`:**

When building calendar envelopes, resolve attendees to contacts and include both `contact_ids` and `contacts` metadata. Currently this passes no contact data at all.

### Step 4: DO NOT Change These

- `find_or_create_contact()` in `postgres.py` — already correct
- Contact→Interaction relationships (SENT, RECEIVED, ATTENDED) — these already MATCH by `contact_id` which is correct
- WORKS_FOR relationship — already correct
- Any Postgres table schemas

### Step 5: Run Existing Tests

Run the existing test suite. Fix any tests that break due to the MERGE key change or the new `contacts` parameter. Existing tests may mock the Contact MERGE query — update the expected Cypher.

### Step 6: Add New Tests

Add tests for:
- Contact node MERGE uses `contact_id` key (not `email_address`)
- Envelope extras contain both `contact_ids` AND `contacts` array
- `contacts` array entries have all required fields (`contact_id`, `email`, `name`, `role`)
- Calendar sync envelopes include contact data
- Constraint creation/dropping works correctly

### Step 7: Create Feature Branch and Commit

Create a feature branch (e.g., `feat/contact-enrichment-downstream`), commit all changes with a descriptive message explaining the MERGE key standardization and contacts metadata threading.

## Verification Checklist

Before marking complete, verify:
- [ ] Contact MERGE in Neo4j uses `(tenant_id, contact_id)` key
- [ ] `email_address` is a property on Contact node (SET with COALESCE)
- [ ] Old `contact_email_unique` constraint dropped
- [ ] New `contact_unique` constraint on `(tenant_id, contact_id)` created
- [ ] `build_email_envelope()` accepts and includes `contacts` metadata array in extras
- [ ] `build_calendar_envelope()` accepts and includes `contacts` metadata array in extras
- [ ] Orchestrator builds `contacts` list during contact resolution loop
- [ ] Calendar sync resolves attendees to contacts
- [ ] All existing tests pass (updated as needed)
- [ ] New tests cover the changes
- [ ] `find_or_create_contact()` is NOT modified
- [ ] Contact→Interaction relationships (SENT, RECEIVED, ATTENDED) still work
