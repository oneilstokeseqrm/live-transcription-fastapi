# Agent Prompt: eq-email-pipeline — Post-Implementation Cleanup

## Context

The contact enrichment downstream changes for eq-email-pipeline are fully implemented and merged to origin/main (commit b84efdd). This prompt addresses remaining housekeeping: syncing the local branch and updating documentation.

## Tasks

### 1. Sync Local Main Branch

The local main branch is 1 commit behind origin/main. Run:
```
git checkout main
git pull origin main
```

Verify the contact enrichment commit is now in local main:
```
git log --oneline -3
```

You should see commit `b84efdd` (or its merge commit) with message about standardizing Contact MERGE key and adding contacts metadata to envelopes.

### 2. Update Documentation

The contact enrichment changes are implemented but not documented as a project phase. Update the following files:

**File: `tasks/todo.md`**

Add a new phase section (Phase 8b or Phase 9, depending on existing numbering) documenting the contact enrichment standardization work. Include entries for:
- Contact MERGE key changed from `(tenant_id, email_address)` to `(tenant_id, contact_id)`
- Neo4j constraint updated (dropped `contact_email_unique`, created `contact_unique`)
- `contacts` metadata array added to both email and calendar envelope extras
- Orchestrator builds contacts metadata during contact resolution loop
- Calendar sync now resolves attendees to contacts and includes in envelopes

Mark all items as complete since the code is already merged.

**File: `CLAUDE.md`** (if it exists)

Add a note in the appropriate section that:
- Contact nodes in Neo4j are now keyed by `(tenant_id, contact_id)` not `(tenant_id, email_address)`
- Envelope extras now include a `contacts` metadata array alongside `contact_ids`
- The `contacts` array format is: `[{contact_id, email, name, role}]`
- This matches the format used by `live-transcription-fastapi` for transcript enrichment

Read the existing CLAUDE.md first to understand the structure and add the note in the right place. If there's an "Architecture" or "Data Flow" section, that's the right location.

### 3. Verify

After updates:
- `git status` should show only documentation changes
- All existing tests should still pass (no code changes)
- Documentation accurately reflects the implemented changes

### DO NOT

- Do not modify any source code
- Do not change tests
- Do not create new branches — commit directly to main (these are documentation updates only)
- Do not push unless explicitly asked
