# opportunity-forecasting — Contact Enrichment Downstream Changes

**Priority:** MEDIUM — depends on upstream changes in eq-structured-graph-core + action-item-graph
**Repo:** `/Users/peteroneil/opportunity-forecasting`
**Context:** Once eq-structured-graph-core and action-item-graph populate Contact nodes and create Contact→Deal (ENGAGED_ON) + Contact→Interaction (ATTENDED) relationships in Neo4j, this pipeline can leverage them for significantly better forecasting signals.

---

## Background

Today this pipeline:
- Reads contacts from Postgres via `contact_opportunity_rel` JOIN — gets name, title, role but NO engagement metrics
- `ContactEvidence.interaction_count` is always 0 — the field exists but is never populated
- `stakeholder_familiarity` scorer uses `interaction_count` but always gets 0 — effectively disabled
- Neo4j queries read Deal, Interaction, ActionItem, Entity nodes but **never Contact nodes**
- No path from Deal → Contact exists in Neo4j queries
- Interaction `participants` field is stored but never correlated with contacts
- LLM prompts show "Contacts Engaged" with name + title + role but no engagement frequency

**This is a read-only consumer.** It doesn't create or modify Contact nodes. All changes are about READING richer data that upstream pipelines now provide.

---

## Prerequisites

These upstream changes must be deployed first:

1. **eq-structured-graph-core** — Must populate Contact.email_address and Contact.display_name (currently NULL)
2. **eq-structured-graph-core + action-item-graph** — Must create `(Contact)-[:ENGAGED_ON]->(Deal)` relationships
3. **eq-structured-graph-core + eq-email-pipeline** — Must create `(Contact)-[:ATTENDED]->(Interaction)` relationships (already exists, but Contact nodes need metadata)

---

## Changes Required

### 1. Add Neo4j Queries for Contact Data

**File:** `src/opportunity_forecasting/clients/neo4j.py`

**New query — Contacts engaged on a deal (via ENGAGED_ON):**
```cypher
MATCH (c:Contact {tenant_id: $tenant_id})-[r:ENGAGED_ON]->(d:Deal {opportunity_id: $opportunity_id})
RETURN c.contact_id AS contact_id,
       c.display_name AS name,
       c.email_address AS email,
       r.role AS deal_role,
       r.confidence AS role_confidence
```

**New query — Per-contact interaction counts:**
```cypher
MATCH (c:Contact {tenant_id: $tenant_id})-[:ENGAGED_ON]->(d:Deal {opportunity_id: $opportunity_id})
OPTIONAL MATCH (c)-[:ATTENDED]->(i:Interaction)
WHERE i.tenant_id = $tenant_id
  AND i.timestamp >= datetime($since)
RETURN c.contact_id AS contact_id,
       c.display_name AS name,
       count(i) AS interaction_count,
       max(i.timestamp) AS last_interaction
```

**New query — Contact engagement timeline (for recency signals):**
```cypher
MATCH (c:Contact {tenant_id: $tenant_id})-[:ENGAGED_ON]->(d:Deal {opportunity_id: $opportunity_id})
MATCH (c)-[:ATTENDED]->(i:Interaction)
WHERE i.tenant_id = $tenant_id
RETURN c.contact_id AS contact_id,
       c.display_name AS name,
       i.interaction_id AS interaction_id,
       i.timestamp AS timestamp,
       i.interaction_type AS type
ORDER BY i.timestamp DESC
```

### 2. Populate ContactEvidence.interaction_count

**File:** `src/opportunity_forecasting/evidence/assembler.py:333-346`

**Current:**
```python
def _build_contacts(self, raw: list[dict]) -> list[ContactEvidence]:
    return [
        ContactEvidence(
            contact_id=str(c["contact_id"]),
            name=c.get("name", ""),
            title=c.get("title"),
            role=c.get("role"),
        )
        for c in raw
    ]
```

**Change:** Merge Postgres contact data with Neo4j interaction counts:
```python
def _build_contacts(self, pg_contacts: list[dict], neo4j_counts: list[dict]) -> list[ContactEvidence]:
    # Build lookup from Neo4j interaction counts
    count_lookup = {r["contact_id"]: r for r in neo4j_counts}

    contacts = []
    for c in pg_contacts:
        cid = str(c["contact_id"])
        neo4j_data = count_lookup.get(cid, {})
        contacts.append(ContactEvidence(
            contact_id=cid,
            name=c.get("name", ""),
            title=c.get("title"),
            role=neo4j_data.get("deal_role") or c.get("role"),  # Prefer LLM-extracted role
            interaction_count=neo4j_data.get("interaction_count", 0),
            last_interaction_date=neo4j_data.get("last_interaction"),
        ))
    return contacts
```

### 3. Add Fields to ContactEvidence Model

**File:** `src/opportunity_forecasting/models/evidence.py:83-91`

**Current:**
```python
class ContactEvidence(BaseModel):
    contact_id: str
    name: str = ""
    title: str | None = None
    role: str | None = None
    interaction_count: int = 0
```

**Add:**
```python
class ContactEvidence(BaseModel):
    contact_id: str
    name: str = ""
    title: str | None = None
    role: str | None = None
    interaction_count: int = 0
    last_interaction_date: str | None = None      # NEW
    engagement_recency_days: int | None = None     # NEW (computed)
    deal_role: str | None = None                   # NEW (champion, economic_buyer from ENGAGED_ON)
    role_confidence: float | None = None           # NEW (LLM confidence)
```

### 4. Enhance LLM Prompts with Engagement Signals

**File:** `src/opportunity_forecasting/engine/prompts.py:702-708`

**Current:**
```python
if bundle.contacts:
    parts.append("\n### Contacts Engaged\n")
    for c in bundle.contacts:
        role = f" ({c.role})" if c.role else ""
        title = f", {c.title}" if c.title else ""
        parts.append(f"- {c.name}{title}{role}")
```

**Change to:**
```python
if bundle.contacts:
    parts.append("\n### Contacts Engaged\n")
    for c in bundle.contacts:
        role = f" ({c.deal_role or c.role})" if (c.deal_role or c.role) else ""
        title = f", {c.title}" if c.title else ""

        # Engagement signal
        engagement = ""
        if c.interaction_count > 0:
            engagement = f" — {c.interaction_count} interactions"
            if c.engagement_recency_days is not None:
                if c.engagement_recency_days > 30:
                    engagement += f" (last {c.engagement_recency_days}d ago) [RISK: DORMANT]"
                else:
                    engagement += f" in last {c.engagement_recency_days}d"
        else:
            engagement = " — 0 interactions [RISK: NO ENGAGEMENT]"

        parts.append(f"- {c.name}{title}{role}{engagement}")
```

**Example output:**
```
### Contacts Engaged
- Sarah Chen, VP Engineering (champion) — 3 interactions in last 14d
- Michael Lopez, CTO (economic_buyer) — 0 interactions [RISK: NO ENGAGEMENT]
- David Kim, Senior PM (evaluator) — 1 interactions (last 45d ago) [RISK: DORMANT]
```

### 5. Stakeholder Familiarity Scorer

**File:** `src/opportunity_forecasting/computed_scorers.py:232`

This scorer already reads `interaction_count` — it will automatically start producing real scores once the field is populated. No code change needed, but verify the scoring logic is appropriate now that it receives real data.

---

## What NOT to Change

- **Do NOT write Contact nodes to Neo4j** — this is a read-only consumer
- **Do NOT modify Postgres contacts table** — read-only
- **Do NOT create ENGAGED_ON relationships** — that's eq-structured-graph-core + action-item-graph's job
- **Trigger mechanism** (`pg_notify` on opportunities) — unchanged
- **Deal, Interaction, ActionItem reads** — unchanged

---

## Neo4j Relationship Map (What This Pipeline Can Now Traverse)

```
Contact --ENGAGED_ON--> Deal              (direct, 1 hop — NEW)
Contact --ATTENDED--> Interaction          (engagement history)
Contact --WORKS_FOR--> Account             (contact's organization)

Deal --RELATED_TO--> Account               (existing)
Account --HAS_INTERACTION--> Interaction   (existing)
Interaction --RELATED_TO--> Deal           (existing)

Full chain:
Contact --ENGAGED_ON--> Deal --RELATED_TO--> Account --HAS_INTERACTION--> Interaction
Contact --ATTENDED--> Interaction --RELATED_TO--> Deal
```

---

## Testing

1. Verify `get_opportunity_contacts()` from Postgres still works (existing behavior)
2. Query Neo4j for `(Contact)-[:ENGAGED_ON]->(Deal)` → verify returns contacts with roles
3. Query Neo4j for per-contact interaction counts → verify non-zero values
4. Verify `ContactEvidence.interaction_count` is populated in evidence assembly
5. Verify LLM prompt shows engagement frequency and risk flags
6. Run forecast for a deal with known contacts → verify `stakeholder_familiarity` scorer produces non-zero score
7. Verify dormant contact detection: contact with ENGAGED_ON but no recent ATTENDED interactions flagged as [RISK]
