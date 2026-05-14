# Agent Prompt: action-item-graph — Post-Implementation Cleanup

## Context

The contact enrichment downstream changes for action-item-graph are fully implemented and merged to origin/main. All 10 expected changes are present and working:

1. ✅ Envelope reads `contacts`, `contact_names`, `contact_labels`, `opportunity_id`
2. ✅ Extractor passes contact names to LLM (not UUIDs)
3. ✅ Extraction prompt formats names with email and role
4. ✅ Owner resolver pre-seeded with contacts via `add_contact()` / `get_contact_id()`
5. ✅ `(Owner)-[:IDENTIFIES_AS]->(Contact)` relationship created via `link_owner_to_contact()`
6. ✅ `(Contact)-[:ENGAGED_ON]->(Deal)` base relationship MERGEd (shared module)
7. ✅ ENGAGED_ON enriched with champion/economic_buyer roles after deal extraction
8. ✅ Interaction property loss fix (COALESCE for user_id, title, etc.)
9. ✅ action_item_links with entity_type='contact' (via contact_map in pipeline)
10. ✅ Deal prompts include contact names for MEDDIC extraction

This prompt addresses documentation verification only.

## Tasks

### 1. Verify Local Branch is Current

Confirm local main matches origin/main:
```
git fetch origin
git log --oneline -1 main
git log --oneline -1 origin/main
```

If behind, pull:
```
git checkout main
git pull origin main
```

### 2. Verify Documentation is Current

**Read ARCHITECTURE.md** — the review found this was updated in commit `ea69ab6`. Verify it documents:
- Contact metadata flow from envelope extras
- Owner→Contact (IDENTIFIES_AS) relationship
- Contact→Deal (ENGAGED_ON) relationship with role enrichment
- MERGE-everywhere pattern for concurrent consumers
- Interaction property loss fix (COALESCE pattern)

**Read README.md** — verify it mentions contact enrichment capabilities.

**Check for CLAUDE.md or .claude/ directory.** If CLAUDE.md exists, ensure it's current with the contact enrichment architecture. If it doesn't exist, no action needed — ARCHITECTURE.md serves this purpose.

**Check tasks/todo.md or tasks/lessons.md** — if they exist, verify contact enrichment work is documented as complete.

### 3. Verify Tests Pass

Run the test suite to confirm everything is healthy:
```
cd /Users/peteroneil/EQ-CORE/action-item-graph
python -m pytest tests/ -x -q
```

If any tests fail, investigate and report — do NOT fix code without understanding why.

### 4. Report

After verification, provide a summary of:
- Is the repo in sync with origin/main?
- Is documentation current and accurate?
- Do all tests pass?
- Any issues found?

### DO NOT

- Do not modify any source code
- Do not modify tests
- Do not push unless explicitly asked
- Do not create branches
