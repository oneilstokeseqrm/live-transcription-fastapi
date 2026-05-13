# Agent Prompt: eq-structured-graph-core — Post-Implementation Cleanup

## Context

The contact enrichment downstream changes for eq-structured-graph-core are fully implemented and merged via PR #9 (merged 2026-03-16). This prompt addresses remaining housekeeping: syncing the local branch and ensuring documentation is current.

## Tasks

### 1. Sync Local Main Branch

The local main branch is behind origin/main. The feature branch `oneilstokeseqrm/contact-enrichment` has been merged via PR #9. Run:
```
git checkout main
git pull origin main
```

Verify the contact enrichment changes are now in local main:
```
git log --oneline -5
```

You should see the merge commit for PR #9 and/or commit `6ee6b10` (feat: populate Contact properties from envelope metadata and add ENGAGED_ON).

### 2. Clean Up Feature Branch

Since PR #9 is merged, delete the local feature branch if it still exists:
```
git branch -d oneilstokeseqrm/contact-enrichment
```

### 3. Review and Update Documentation

Check for the following documentation and update as needed:

**Check if CLAUDE.md exists.** If it does, ensure it documents:
- Contact nodes are MERGEd by `(tenant_id, contact_id)` — the canonical key
- `_merge_contact()` now populates `email_address`, `display_name`, `role` from envelope `extras.contacts` array
- New `(Contact)-[:ENGAGED_ON]->(Deal)` relationship created during skeleton building when `opportunity_id` is present
- Phase 2 bridger auto-benefits from populated Contact.name/email (no code changes needed)

If CLAUDE.md does not exist, check for alternative documentation files (README.md, ARCHITECTURE.md, docs/ directory). If the project has a tasks/todo.md or similar tracking file, ensure the contact enrichment work is documented as complete.

**Check if tasks/todo.md or tasks/lessons.md exists.** If so, ensure contact enrichment is documented with:
- Envelope adapter reads `extras.contacts` array and `extras.opportunity_id`
- `_merge_contact()` uses filter-then-SET pattern (filters None at Python level, uses unconditional SET)
- ENGAGED_ON relationship only created when both contact_ids and opportunity_id present
- Backward compatible — envelopes without contacts array still work

### 4. Verify

After updates:
- `git status` should show only documentation changes (if any)
- Local main matches origin/main
- Feature branch cleaned up

### DO NOT

- Do not modify any source code
- Do not change tests
- Do not push unless explicitly asked
