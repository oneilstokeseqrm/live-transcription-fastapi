# Agent Prompt: opportunity-forecasting Contact Enrichment Changes

## What You Are Doing

You are implementing downstream changes in the `opportunity-forecasting` repo to leverage new contact-related graph data that upstream pipelines (eq-structured-graph-core, action-item-graph) now create. This pipeline is a **read-only consumer** — it does NOT create or modify Contact nodes or relationships. It reads them to build richer forecasting evidence.

This is a **medium priority** change that depends on upstream deployments:
- eq-structured-graph-core must be deployed with Contact metadata population + ENGAGED_ON relationship
- action-item-graph must be deployed with ENGAGED_ON role enrichment

Once upstream changes are live, this pipeline can:
- Compute per-contact `interaction_count` (currently always 0, making the `stakeholder_familiarity` scorer effectively disabled)
- Show engagement frequency and recency in LLM prompts
- Flag dormant champions and unengaged economic buyers as risk signals

## Context: What Upstream Pipelines Now Provide

### New Neo4j Data Available

**Contact nodes** (created by eq-structured-graph-core + eq-email-pipeline):
```
(:Contact {
    tenant_id: "uuid",
    contact_id: "uuid",          # Canonical Postgres ID
    email_address: "jane@co.com", # NOW POPULATED (was NULL)
    display_name: "Jane Smith",   # NOW POPULATED (was NULL)
    role: "organizer"             # NOW POPULATED (was NULL)
})
```

**New relationship — Contact→Deal (ENGAGED_ON)**:
```
(:Contact)-[:ENGAGED_ON {
    created_at: datetime,
    role: "champion",              # LLM-extracted by action-item-graph
    confidence: 0.85,              # LLM confidence score
    enriched_at: datetime
}]->(:Deal)
```

**Existing relationship — Contact→Interaction (ATTENDED)**:
```
(:Contact)-[:ATTENDED]->(:Interaction)
```
This already existed but Contact nodes lacked metadata. Now Contact.display_name and Contact.email_address are populated, making this traversal useful.

### Postgres Data (Unchanged)

`get_opportunity_contacts()` via `contact_opportunity_rel` still works as before, returning name, title, role. The Neo4j data supplements this with engagement metrics.

## Cross-Cutting Architectural Decisions

1. **This is a read-only consumer.** Do NOT create, modify, or delete any nodes or relationships in Neo4j. Do NOT write to the contacts table in Postgres.

2. **Merge Postgres + Neo4j data**: Postgres has static contact metadata (name, title, role from CRM). Neo4j has engagement data (interaction counts, ENGAGED_ON roles). The evidence assembler should merge both sources.

3. **Graceful degradation**: If Neo4j Contact data isn't available yet (upstream not deployed), fall back to current behavior (interaction_count = 0). Don't fail hard.

## Step-by-Step Instructions

### Step 1: Read Investigation Notes

Read the detailed investigation notes at:
- `/Users/peteroneil/live-transcription-fastapi/tasks/downstream/opportunity-forecasting.md`

This contains the specific changes needed with file paths, queries, and rationale.

### Step 2: Read Current Source Code

Read these files to understand the current implementation:

1. **`src/opportunity_forecasting/clients/neo4j.py`** — Focus on:
   - All existing Cypher queries — understand the pattern (how tenant_id is handled, how results are returned)
   - What nodes are currently read (Deal, Interaction, ActionItem, DealVersion, Entity)
   - Note: Contact nodes are NOT read today — you're adding this
   - The client's method signatures and return types

2. **`src/opportunity_forecasting/clients/postgres.py`** — Focus on:
   - `get_opportunity_contacts()` (around lines 93-112) — current contact data source
   - Return format: list of dicts with contact_id, name, title, role, email
   - This continues to work — Neo4j data supplements it

3. **`src/opportunity_forecasting/evidence/assembler.py`** — Focus on:
   - `_build_contacts()` (around lines 333-346) — where ContactEvidence is created
   - The overall assembly flow (around lines 52-206) — where parallel queries happen
   - How Neo4j and Postgres data are combined for other evidence types (use as pattern)
   - `_build_interactions()` — how InteractionEvidence.participants is populated

4. **`src/opportunity_forecasting/models/evidence.py`** — Focus on:
   - `ContactEvidence` model (around lines 83-91) — has `interaction_count` field (always 0)
   - `InteractionEvidence` model — has `participants` field (freeform strings)
   - Other evidence models — understand the pattern

5. **`src/opportunity_forecasting/engine/prompts.py`** — Focus on:
   - "Contacts Engaged" section (around lines 702-708) — how contacts are presented to LLM
   - Overall prompt structure — understand how evidence sections are composed
   - What fields are shown vs. omitted

6. **`src/opportunity_forecasting/computed_scorers.py`** — Focus on:
   - `score_stakeholder_familiarity()` (around line 232) — reads `interaction_count`
   - What score it produces and how it's used
   - Whether any other scorers reference contact data

### Step 3: Implement Changes

**Change 1 — Add Neo4j queries for Contact data:**

Add methods to the Neo4j client:

a. **Get contacts engaged on a deal** (via ENGAGED_ON):
```cypher
MATCH (c:Contact {tenant_id: $tenant_id})-[r:ENGAGED_ON]->(d:Deal {opportunity_id: $opportunity_id})
RETURN c.contact_id AS contact_id,
       c.display_name AS name,
       c.email_address AS email,
       r.role AS deal_role,
       r.confidence AS role_confidence
```

b. **Get per-contact interaction counts** (via ATTENDED):
```cypher
MATCH (c:Contact {tenant_id: $tenant_id})-[:ENGAGED_ON]->(d:Deal {opportunity_id: $opportunity_id})
OPTIONAL MATCH (c)-[:ATTENDED]->(i:Interaction)
WHERE i.tenant_id = $tenant_id
RETURN c.contact_id AS contact_id,
       count(i) AS interaction_count,
       max(i.timestamp) AS last_interaction
```

c. Optionally, a combined query that returns both in one round-trip.

**Important:** These queries should handle the case where ENGAGED_ON relationships don't exist yet (upstream not deployed). Return empty results, don't raise errors.

**Change 2 — Merge Postgres + Neo4j contact data in assembler:**

In `_build_contacts()`, merge Postgres contact metadata with Neo4j engagement data:
- Postgres provides: name, title, role (from CRM)
- Neo4j provides: interaction_count, last_interaction, deal_role (from graph)
- Prefer Neo4j `deal_role` over Postgres `role` when available (LLM-extracted is more accurate)

Build a lookup dict from Neo4j results by `contact_id`, then merge during ContactEvidence construction.

**Change 3 — Add fields to ContactEvidence model:**

Add to the existing model:
```python
last_interaction_date: str | None = None
engagement_recency_days: int | None = None  # Computed from last_interaction_date
deal_role: str | None = None                # champion, economic_buyer (from ENGAGED_ON)
role_confidence: float | None = None        # LLM confidence on role
```

Compute `engagement_recency_days` from `last_interaction_date` during evidence assembly.

**Change 4 — Enhance LLM prompts with engagement signals:**

Update the "Contacts Engaged" section to show:
```
### Contacts Engaged
- Sarah Chen, VP Engineering (champion) — 3 interactions in last 14d
- Michael Lopez, CTO (economic_buyer) — 0 interactions [RISK: NO ENGAGEMENT]
- David Kim, Senior PM (evaluator) — 1 interaction (last 45d ago) [RISK: DORMANT]
```

Use `deal_role` (from ENGAGED_ON) if available, falling back to `role` (from Postgres).
Flag contacts with 0 interactions or > 30 days since last interaction as risks.

**Change 5 — Verify stakeholder_familiarity scorer:**

This scorer already reads `interaction_count`. Once populated, it will start producing real scores automatically. Verify the scoring logic is still appropriate with real data (it may have been tuned assuming 0 — check thresholds).

### Step 4: DO NOT Change These

- **Do NOT write Contact nodes to Neo4j** — read-only consumer
- **Do NOT modify Postgres contacts table** — read-only
- **Do NOT create ENGAGED_ON relationships** — that's upstream's job
- **Trigger mechanism** (`pg_notify`) — unchanged
- **Deal, Interaction, ActionItem reads** — unchanged
- **Forecast persistence** — unchanged
- **DealEvent, DealVersion, ReadinessSnapshot** — unchanged

### Step 5: Run Existing Tests

Run the full test suite. Focus on:
- Evidence assembler tests — may need updating for new parameters to `_build_contacts()`
- Prompt tests — verify new format
- Scorer tests — verify stakeholder_familiarity with non-zero interaction_count

### Step 6: Add New Tests

Add tests for:
- Neo4j contact queries return correct data format
- Neo4j queries return empty results gracefully (no ENGAGED_ON relationships)
- Evidence assembler merges Postgres + Neo4j data correctly
- ContactEvidence.interaction_count populated from Neo4j
- ContactEvidence.engagement_recency_days computed correctly
- LLM prompt shows engagement frequency and risk flags
- Dormant contact detection (ENGAGED_ON exists, no recent ATTENDED)
- No-engagement detection (ENGAGED_ON exists, zero ATTENDED)
- stakeholder_familiarity scorer produces non-zero score with real data
- Backward compatibility — works when Neo4j has no Contact data (falls back to Postgres only)

### Step 7: Create Feature Branch and Commit

Create a feature branch (e.g., `feat/contact-enrichment-downstream`), commit with a descriptive message.

## Deployment Order

This pipeline should be deployed AFTER:
1. eq-email-pipeline (MERGE key fix + contacts metadata)
2. eq-structured-graph-core (Contact metadata population + ENGAGED_ON base)
3. action-item-graph (ENGAGED_ON role enrichment)

The code should be written to degrade gracefully if upstream hasn't been deployed yet (empty Neo4j results → interaction_count stays at 0).

## Verification Checklist

Before marking complete, verify:
- [ ] Neo4j client has queries for Contact→ENGAGED_ON→Deal and Contact→ATTENDED→Interaction
- [ ] Queries handle missing ENGAGED_ON relationships gracefully (empty results, not errors)
- [ ] Evidence assembler merges Postgres contact metadata with Neo4j engagement data
- [ ] ContactEvidence.interaction_count populated from ATTENDED relationship counts
- [ ] ContactEvidence has `last_interaction_date`, `engagement_recency_days`, `deal_role` fields
- [ ] LLM prompt shows engagement frequency, recency, and risk flags
- [ ] Dormant contacts (>30d since last interaction) flagged as [RISK: DORMANT]
- [ ] Unengaged contacts (0 interactions) flagged as [RISK: NO ENGAGEMENT]
- [ ] stakeholder_familiarity scorer verified with real data
- [ ] No writes to Neo4j or Postgres contacts table
- [ ] Backward compatible — works when Neo4j has no Contact data
- [ ] All existing tests pass
- [ ] New tests cover all changes
