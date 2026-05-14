# eq-email-pipeline — Contact Enrichment Downstream Changes

**Priority:** HIGH — root cause of Contact data gaps in Neo4j
**Repo:** `/Users/peteroneil/eq-email-pipeline`
**Context:** live-transcription-fastapi now sends enriched envelopes with `extras.contacts` metadata array + `extras.contact_ids`. The email pipeline needs to match this contract AND fix its Neo4j Contact MERGE key to prevent duplicate nodes.

---

## Background

Two problems originate in this pipeline:

1. **Contact.name and Contact.email are always NULL in Neo4j** — because the envelope only sends bare `contact_ids` UUIDs, not the metadata (email, name, role) that eq-structured-graph-core needs to populate Contact node properties.

2. **Duplicate Contact nodes** — this pipeline MERGEs Contact nodes by `(tenant_id, email_address)` while eq-structured-graph-core MERGEs by `(tenant_id, contact_id)`. Same person → two nodes.

---

## Changes Required

### 1. Fix Contact Node MERGE Key (CRITICAL)

**File:** `src/pipeline/skeleton.py:84-99`

**Current:**
```cypher
MERGE (c:Contact {tenant_id: $tenant_id, email_address: $email})
ON CREATE SET c.contact_id = $contact_id,
              c.display_name = $display_name,
              c.created_at = datetime()
ON MATCH SET  c.last_seen_at = datetime(),
              c.contact_id = COALESCE(c.contact_id, $contact_id),
              c.display_name = COALESCE(c.display_name, $display_name)
```

**Change to:**
```cypher
MERGE (c:Contact {tenant_id: $tenant_id, contact_id: $contact_id})
ON CREATE SET c.email_address = $email,
              c.display_name = $display_name,
              c.created_at = datetime()
ON MATCH SET  c.last_seen_at = datetime(),
              c.email_address = COALESCE(c.email_address, $email),
              c.display_name = COALESCE(c.display_name, $display_name)
```

**Why:** Standardizing on `(tenant_id, contact_id)` as the single MERGE key across all services. `contact_id` is the Postgres canonical identity. `email_address` becomes a property, not a key. This prevents duplicate Contact nodes when both this pipeline and eq-structured-graph-core process the same contact.

### 2. Update Neo4j Constraint

**File:** `src/persistence/neo4j.py:52-54`

**Current:**
```cypher
CREATE CONSTRAINT contact_email_unique IF NOT EXISTS
FOR (c:Contact) REQUIRE (c.tenant_id, c.email_address) IS UNIQUE
```

**Change to:**
```cypher
-- Drop old constraint
DROP CONSTRAINT contact_email_unique IF EXISTS

-- Use the same constraint as eq-structured-graph-core
CREATE CONSTRAINT contact_unique IF NOT EXISTS
FOR (c:Contact) REQUIRE (c.tenant_id, c.contact_id) IS UNIQUE
```

**Note:** eq-structured-graph-core already creates `contact_unique` on `(tenant_id, contact_id)`. If that constraint already exists in the database, the CREATE is a no-op. The important thing is dropping the old `contact_email_unique` constraint.

### 3. Thread `contacts` Metadata Array into Envelope Extras

**File:** `src/pipeline/emit.py:145-198`

**Current:** `build_email_envelope()` includes `contact_ids` but not contact metadata.

**Add `contacts` parameter:**
```python
def build_email_envelope(
    ...,
    contact_ids: list[str] | None = None,
    contacts: list[dict] | None = None,  # NEW
) -> EnvelopeV1:
    extras = {
        ...existing fields...,
        "contact_ids": contact_ids or [],
        "contacts": contacts or [],  # NEW: [{contact_id, email, name, role}]
    }
```

**Contract for `contacts` array:**
```python
[
    {
        "contact_id": "uuid-string",   # ALWAYS present (from Postgres)
        "email": "jane@acme.com",       # ALWAYS present
        "name": "Jane Smith",           # May be None for email-only contacts
        "role": "sender"                # sender | recipient | cc
    },
    ...
]
```

This is the same format live-transcription-fastapi uses, so eq-structured-graph-core can consume both flows identically.

### 4. Build `contacts` List in Orchestrator

**File:** `src/pipeline/orchestrator.py:172-180,249-267`

During contact resolution, the orchestrator already iterates over participants and calls `find_or_create_contact()`. It already has:
- `email` (from EmailAddress)
- `display_name` (from EmailAddress)
- Role (sender, recipient, cc — determinable from position)
- `contact_id` (returned by `find_or_create_contact()`)

**Build the list during the existing loop:**
```python
contacts_metadata = []
for participant in participants:
    contact_id = find_or_create_contact(...)
    contact_id_map[participant.email.lower()] = contact_id
    contacts_metadata.append({
        "contact_id": contact_id,
        "email": participant.email.lower(),
        "name": participant.display_name or None,
        "role": determine_role(participant),  # sender | recipient | cc
    })
```

Pass to `build_email_envelope()`:
```python
envelope = build_email_envelope(
    ...,
    contact_ids=list(contact_id_map.values()),
    contacts=contacts_metadata,
)
```

### 5. Calendar Sync Contact Resolution

**File:** `src/pipeline/calendar_sync.py`

**Current:** Calls `build_calendar_envelope()` without passing `contact_ids` — always empty list.

**Change:** When building calendar envelopes, resolve attendees to contacts:
1. For each attendee in `calendar_event_attendees`, call `find_or_create_contact(tenant_id, email, display_name)`
2. Build `contacts` metadata list
3. Pass both `contact_ids` and `contacts` to `build_calendar_envelope()`

---

## What NOT to Change

- **`find_or_create_contact()` in `src/persistence/postgres.py`** — This is already correct. Don't modify the Postgres resolution logic.
- **Contact→Interaction relationships** (SENT, RECEIVED, ATTENDED in `skeleton.py:220-296`) — These already MATCH by `contact_id`, which is correct. The relationship creation logic doesn't need changes.
- **WORKS_FOR relationship** — Already correct.

---

## Testing

1. Process an email with the changes → verify Contact node in Neo4j has `contact_id` as MERGE key AND `email_address` as property
2. Process a second email from the same sender → verify same Contact node is matched (no duplicate)
3. Submit a transcript via live-transcription-fastapi for the same contact → verify eq-structured-graph-core hits the SAME Contact node (MERGE on contact_id matches)
4. Verify envelope on EventBridge contains both `extras.contact_ids` AND `extras.contacts` array with full metadata
