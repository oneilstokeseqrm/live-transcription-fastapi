# action-item-graph — Contact Enrichment Downstream Changes

**Priority:** MEDIUM — quality improvement for LLM extraction + new graph relationships
**Repo:** `/Users/peteroneil/EQ-CORE/action-item-graph`
**Context:** live-transcription-fastapi now sends enriched envelopes with `extras.contacts` metadata array containing `{contact_id, email, name, role}` per contact. This pipeline can use contact names to dramatically improve LLM extraction accuracy and build new graph relationships.

---

## Background

Today this pipeline:
- Reads `extras.contact_ids` but only gets opaque UUIDs — passes them to the LLM as `Participants: a1b2c3d4-...` (useless)
- Owner resolver works blind — extracts names like "Jane" from transcript but has no known contact list to match against
- Has a `Contact` model defined in `models/entities.py:193-228` but never instantiates it
- Has `action_item_links` support for `entity_type='contact'` but never calls it
- Does NOT create Contact nodes in Neo4j (correct — that's eq-structured-graph-core + eq-email-pipeline's job)

---

## Changes Required

### 1. Read Contact Metadata from Envelope

**File:** `src/action_item_graph/models/envelope.py:112-114`

**Current:**
```python
@property
def contact_ids(self) -> list[str]:
    return self.extras.get('contact_ids', [])
```

**Add:**
```python
@property
def contacts(self) -> list[dict]:
    """Full contact metadata: [{contact_id, email, name, role}]."""
    return self.extras.get('contacts', [])

@property
def contact_names(self) -> list[str]:
    """Human-readable names for LLM prompts."""
    return [
        c.get('name') or c.get('email', 'Unknown')
        for c in self.contacts
    ]

@property
def opportunity_id(self) -> str | None:
    return self.extras.get('opportunity_id')
```

### 2. Pass Contact Names to LLM Extraction Prompt

**File:** `src/action_item_graph/pipeline/extractor.py:139-141`

**Current:**
```python
participants=envelope.contact_ids if envelope.contact_ids else None,
```

**Change to:**
```python
participants=envelope.contact_names if envelope.contact_names else None,
```

### 3. Format Participants in LLM Prompt

**File:** `src/action_item_graph/prompts/extract_action_items.py:333-389`

When contacts are available, format as:
```
Meeting participants:
  - Jane Smith <jane@acme.com> (organizer)
  - Bob Jones <bob@acme.com> (attendee)
  - Pete O'Neil <pete@company.com> (recorder)
```

Build this from `envelope.contacts`:
```python
if envelope.contacts:
    lines = []
    for c in envelope.contacts:
        name = c.get('name') or c.get('email')
        email = c.get('email', '')
        role = c.get('role', '')
        if c.get('name'):
            lines.append(f"  - {name} <{email}> ({role})")
        else:
            lines.append(f"  - {email} ({role})")
    participants_block = "Meeting participants:\n" + "\n".join(lines)
```

This gives the LLM actual names to work with for action item attribution (e.g., "Jane will send the proposal by Friday" → owner = Jane Smith).

### 4. Seed Owner Resolver with Contact Names

**File:** `src/action_item_graph/pipeline/owner_resolver.py:151-317`

**Current:** The resolver loads Owner names from Neo4j and matches extracted owner text against that cache. It has NO access to contact names.

**Change:** Before the Neo4j owner cache load, pre-seed with known contacts from the envelope:
```python
# Pre-seed owner cache with known contacts
if envelope.contacts:
    for c in envelope.contacts:
        if c.get('name'):
            # Add to cache so "Jane" from LLM output matches "Jane Smith" from contacts
            owner_cache.add_candidate(
                name=c['name'],
                contact_id=c['contact_id'],
                source='envelope_contact',
            )
```

This allows the resolver to match extracted owner names (e.g., "Jane") against known contacts (e.g., "Jane Smith") without needing a Neo4j lookup.

### 5. Create Owner→Contact Link

**File:** `src/action_item_graph/repository.py`

**New method:**
```python
async def link_owner_to_contact(self, owner_id: str, contact_id: str) -> bool:
    """Create (Owner)-[:IDENTIFIES_AS]->(Contact) relationship."""
    query = """
    MATCH (o:Owner {tenant_id: $tenant_id, owner_id: $owner_id})
    MATCH (c:Contact {tenant_id: $tenant_id, contact_id: $contact_id})
    MERGE (o)-[r:IDENTIFIES_AS]->(c)
    ON CREATE SET r.created_at = datetime()
    RETURN r IS NOT NULL as created
    """
    ...
```

**When to call:** After owner resolution, when an Owner is matched to a known contact (via the pre-seeded cache from step 4).

**Value:** Enables traversal: `ActionItem → OWNED_BY → Owner → IDENTIFIES_AS → Contact`. This bridges the gap between extracted action items and canonical contact records.

### 6. MERGE Contact→Deal (ENGAGED_ON) Base Relationship

**File:** `src/action_item_graph/repository.py` or `src/deal_graph/repository.py`

When both `contact_ids` and `opportunity_id` are present in the envelope, MERGE the base relationship:

```python
async def link_contacts_to_deal(self, contact_ids: list[str], opportunity_id: str) -> None:
    """MERGE (Contact)-[:ENGAGED_ON]->(Deal) for each contact."""
    query = """
    MATCH (c:Contact {tenant_id: $tenant_id, contact_id: $contact_id})
    MATCH (d:Deal {tenant_id: $tenant_id, opportunity_id: $opportunity_id})
    MERGE (c)-[r:ENGAGED_ON]->(d)
    ON CREATE SET r.created_at = datetime()
    RETURN r IS NOT NULL as created
    """
    for cid in contact_ids:
        await self._run(query, contact_id=cid, opportunity_id=opportunity_id)
```

**Note:** eq-structured-graph-core also MERGEs this relationship (MERGE-everywhere pattern). Whoever runs first creates it — the other is a no-op. This is intentional for race condition safety.

### 7. Enrich ENGAGED_ON with LLM-Extracted Roles

**File:** `src/deal_graph/repository.py` (after deal extraction)

After LLM extraction identifies champion/economic_buyer roles, enrich the ENGAGED_ON relationship:

```python
async def enrich_contact_deal_role(
    self, contact_id: str, opportunity_id: str, role: str, confidence: float
) -> None:
    """Enrich ENGAGED_ON relationship with LLM-extracted role."""
    query = """
    MATCH (c:Contact {tenant_id: $tenant_id, contact_id: $contact_id})
    MATCH (d:Deal {tenant_id: $tenant_id, opportunity_id: $opportunity_id})
    MATCH (c)-[r:ENGAGED_ON]->(d)
    SET r.role = $role,
        r.confidence = $confidence,
        r.enriched_at = datetime()
    RETURN r IS NOT NULL as updated
    """
    ...
```

**IMPORTANT:** Uses unconditional `SET` (not `ON CREATE SET`) — always applies regardless of which service created the relationship. This is the enrichment timing pattern: MERGE for existence, SET for enrichment.

### 8. Fix Interaction Property Loss Bug

**File:** `src/action_item_graph/repository.py:100-120`

**Problem:** `user_id`, `pg_user_id`, `title`, `duration_seconds` are in `ON CREATE SET`. If eq-structured-graph-core creates the Interaction node first (race condition), these properties are never written.

**Fix:** Change to unconditional SET with COALESCE for properties this service uniquely provides:

```cypher
MERGE (i:Interaction {tenant_id: $tenant_id, interaction_id: $interaction_id})
ON CREATE SET
    i.content_text = $content_text,
    i.interaction_type = $interaction_type,
    i.timestamp = $timestamp,
    i.source = $source,
    i.created_at = datetime()
ON MATCH SET
    i.action_item_count = $action_item_count,
    i.processed_at = datetime()
-- ADD: unconditional SET for properties this service uniquely provides
SET i.user_id = COALESCE(i.user_id, $user_id),
    i.pg_user_id = COALESCE(i.pg_user_id, $pg_user_id),
    i.title = COALESCE(i.title, $title),
    i.duration_seconds = COALESCE(i.duration_seconds, $duration_seconds)
```

Same fix needed in `src/deal_graph/repository.py:178-189` for `trace_id`.

### 9. Use action_item_links with entity_type='contact'

**File:** `src/action_item_graph/clients/postgres_client.py:576-622`

The `link_action_item_to_entity()` function already supports `entity_type='contact'` but is never called with it.

**When to call:** After owner resolution successfully matches an Owner to a Contact (via the pre-seeded cache), link the action item:
```python
await postgres_client.link_action_item_to_entity(
    tenant_id=tenant_id,
    action_item_id=action_item_id,
    entity_type='contact',
    entity_id=contact_id,  # from owner→contact resolution
)
```

### 10. Pass Contact Names to Deal Extraction Prompts

**File:** `src/deal_graph/prompts/extract_deals.py`

When contacts are available, include them in the MEDDIC extraction context so the LLM can identify:
- Champion (by matching transcript mentions to known contacts)
- Economic buyer (from meeting context + contact roles)
- Other stakeholders

---

## What NOT to Change

- **Do NOT create Contact nodes in Neo4j** — that's eq-structured-graph-core + eq-email-pipeline's responsibility
- **Do NOT modify Postgres contacts table** — contact resolution is done upstream in live-transcription-fastapi / eq-email-pipeline
- **Deal node MERGE** — already correct
- **ActionItem node MERGE** — already correct
- **Account→HAS_INTERACTION relationship** — already correct

---

## Envelope Contract

The `extras` dict in EnvelopeV1 now contains:

```python
{
    "contact_ids": ["uuid1", "uuid2"],           # Existing (bare UUIDs)
    "contacts": [                                 # NEW — full metadata
        {
            "contact_id": "uuid1",
            "email": "jane@acme.com",
            "name": "Jane Smith",                 # May be None
            "role": "organizer"
        }
    ],
    "opportunity_id": "uuid-or-null",             # Existing
    "meeting_title": "Q3 Pipeline Review",        # Existing (from both email + transcript)
    "user_name": "Pete O'Neil",                   # Existing
}
```

---

## Testing

1. Submit transcript with contacts → verify LLM prompt shows real names (not UUIDs)
2. Extract action item with owner "Jane" → verify owner resolver matches to "Jane Smith" contact
3. Verify `(Owner)-[:IDENTIFIES_AS]->(Contact)` relationship created in Neo4j
4. Submit transcript with opportunity_id + contacts → verify `(Contact)-[:ENGAGED_ON]->(Deal)` exists
5. After deal extraction identifies champion → verify ENGAGED_ON relationship has `role=champion`
6. Process two envelopes rapidly → verify Interaction node has `user_id` and `title` regardless of which service ran first
7. Verify `action_item_links` row with `entity_type='contact'` when owner→contact resolved
