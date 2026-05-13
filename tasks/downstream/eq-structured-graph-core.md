# eq-structured-graph-core — Contact Enrichment Downstream Changes

**Priority:** HIGH — enables Contact node metadata population + new relationships
**Repo:** `/Users/peteroneil/eq-structured-graph-core`
**Context:** live-transcription-fastapi now sends enriched envelopes with `extras.contacts` metadata array containing `{contact_id, email, name, role}` per contact. This pipeline needs to read that array and populate Contact node properties that are currently always NULL.

---

## Background

This pipeline creates Contact nodes in Neo4j via `_merge_contact()` during skeleton building. Currently:
- MERGE key: `(tenant_id, contact_id)` — **correct, no change needed**
- Properties set: **none** — Contact.name, Contact.email, Contact.role are always NULL
- Root cause: envelope adapter only reads `extras.contact_ids` (bare UUIDs), ignores `extras.contacts` metadata

The envelope now carries full metadata in `extras.contacts`. This pipeline needs to read it and populate Contact nodes.

---

## Changes Required

### 1. Read `contacts` Array from Envelope Extras

**File:** `app/models/envelope.py:44-73`

**Current (line 71):**
```python
"contact_ids": envelope.extras.get("contact_ids", []),
```

**Add:**
```python
"contact_ids": envelope.extras.get("contact_ids", []),
"contacts": envelope.extras.get("contacts", []),       # NEW
"opportunity_id": envelope.extras.get("opportunity_id"),  # NEW
```

### 2. Add Contact Metadata to StandardInteraction Model

**File:** `app/models/interaction.py:22-75`

**Add field (around line 58):**
```python
contact_ids: list[str] = []     # existing
contacts: list[dict] = []       # NEW: [{contact_id, email, name, role}]
opportunity_id: str | None = None  # NEW
```

### 3. Populate Contact Node Properties on MERGE

**File:** `app/db/queries/skeleton.py:189-201`

**Current:**
```python
def _merge_contact(self, contact_id: str) -> dict:
    return self.session.merge_node(
        label="Contact",
        key_props={"contact_id": contact_id},
    )
```

**Change to:**
```python
def _merge_contact(self, contact_id: str, metadata: dict | None = None) -> dict:
    other_props = {}
    if metadata:
        if metadata.get("email"):
            other_props["email_address"] = metadata["email"]
        if metadata.get("name"):
            other_props["display_name"] = metadata["name"]
        if metadata.get("role"):
            other_props["role"] = metadata["role"]
    return self.session.merge_node(
        label="Contact",
        key_props={"contact_id": contact_id},
        other_props=other_props if other_props else None,
    )
```

**IMPORTANT:** The `session.merge_node()` implementation must use `COALESCE` for `other_props` — never overwrite existing values with NULL. Check `app/db/session.py` to verify how `other_props` are applied. If it uses unconditional `SET`, change to:
```cypher
SET n.email_address = COALESCE($email_address, n.email_address),
    n.display_name = COALESCE($display_name, n.display_name)
```

This prevents a later envelope with missing metadata from wiping out values set by an earlier envelope.

**Build lookup dict in `build_skeleton()`:**
```python
# Build contact metadata lookup
contact_lookup = {c["contact_id"]: c for c in interaction.contacts}

# In the participant loop (around line 109):
for cid in interaction.contact_ids:
    metadata = contact_lookup.get(cid)
    self._merge_contact(cid, metadata=metadata)
```

### 4. Add Contact→Deal (ENGAGED_ON) Relationship

**File:** `app/db/queries/skeleton.py`

**New method:**
```python
def _create_contact_to_deal(self, contact_id: str, opportunity_id: str) -> bool:
    """MERGE [:ENGAGED_ON] from Contact to Deal."""
    return self.session.create_relationship(
        from_label="Contact",
        from_key_props={"contact_id": contact_id},
        rel_type="ENGAGED_ON",
        to_label="Deal",
        to_key_props={"opportunity_id": opportunity_id},
    )
```

**Call in `build_skeleton()` — after Contact and Deal nodes exist:**
```python
if interaction.opportunity_id:
    for cid in interaction.contact_ids:
        self._create_contact_to_deal(cid, interaction.opportunity_id)
```

**Note:** This creates the BASE relationship only (no role properties). action-item-graph enriches it later with `role` (champion, economic_buyer, etc.) using unconditional `SET`. Both services use MERGE — whoever runs first creates it, the other is a no-op.

### 5. Fix Interaction Property Loss Bug

**File:** `app/db/queries/skeleton.py:167-187`

**Problem:** This pipeline uses `ON CREATE SET` for `content_format` and `trace_id`. If action-item-graph creates the Interaction node first (race condition — both consume the same EventBridge event), these properties are never written.

**Fix:** Change unique properties to unconditional `SET` with `COALESCE`:
```python
def _merge_interaction(self, interaction: StandardInteraction) -> dict:
    node = self.session.merge_node(
        label="Interaction",
        key_props={"interaction_id": interaction.interaction_id},
        # These should always be set, using COALESCE to avoid overwriting
    )
    # Unconditional SET for properties this service uniquely provides
    self.session.run("""
        MATCH (i:Interaction {tenant_id: $tid, interaction_id: $iid})
        SET i.content_format = COALESCE(i.content_format, $fmt),
            i.trace_id = COALESCE(i.trace_id, $trace)
    """, tid=..., iid=..., fmt=interaction.content.format, trace=interaction.trace_id)
    return node
```

The exact implementation depends on how `TenantSession.merge_node()` works — the point is that `content_format` and `trace_id` must not be in `ON CREATE SET` only.

### 6. No Constraint Changes Needed

The existing `contact_unique` constraint on `(tenant_id, contact_id)` is the canonical one. eq-email-pipeline is being updated to use this same key and drop its `contact_email_unique` constraint.

---

## What NOT to Change

- **Phase 2 bridger** (`app/phase2/resolution/bridger.py:256-272`) — No code changes needed. It already queries `Contact.name` and `Contact.email`. Once those properties are populated (by this change), the bridger will automatically start returning real data for entity-to-contact scoring.
- **Contact→Interaction relationships** (ATTENDED, SENT, RECEIVED, CREATED) — Already working correctly.
- **WORKS_FOR relationship** — Already working correctly.
- **Deal→Interaction (RELATED_TO)** — Already working correctly.
- **Deal→Account (RELATED_TO)** — Already working correctly.

---

## Envelope Contract

The `extras` dict in EnvelopeV1 now contains:

```python
{
    "contact_ids": ["uuid1", "uuid2"],           # List of UUIDv4 strings (existing)
    "contacts": [                                 # NEW — full metadata per contact
        {
            "contact_id": "uuid1",                # ALWAYS present
            "email": "jane@acme.com",             # ALWAYS present
            "name": "Jane Smith",                 # May be None
            "role": "organizer"                   # organizer | attendee | sender | recipient | cc
        }
    ],
    "opportunity_id": "uuid-or-null",             # Existing field
    "meeting_title": "Q3 Pipeline Review",        # From transcript enrichment
    "calendar_event_id": "uuid-or-null",          # From transcript enrichment
    "enrichment_source": "calendar_match",        # calendar_match | none
    "enrichment_confidence": "high"               # high | medium | none
}
```

Both live-transcription-fastapi (transcripts) and eq-email-pipeline (emails) send this same format. This pipeline can consume both identically.

---

## Testing

1. Submit a transcript with calendar enrichment → verify Contact nodes have `email_address` and `display_name` populated (not NULL)
2. Submit an email for the same contact → verify the SAME Contact node is matched (MERGE on contact_id)
3. Submit a transcript with `opportunity_id` in extras → verify `(Contact)-[:ENGAGED_ON]->(Deal)` relationship exists
4. Verify Phase 2 bridger returns real names when querying Contact nodes
5. Process two envelopes rapidly → verify Interaction node has BOTH `content_format` (from this service) AND `user_id` (from action-item-graph) — no property loss
