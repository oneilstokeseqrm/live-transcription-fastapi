# Agent Prompt: opportunity-forecasting — Post-Implementation Cleanup

## Context

The contact enrichment downstream changes for opportunity-forecasting are fully implemented and merged to origin/main via PR #14 (commit 3311795, merging feature commit 46a2295). All 6 expected changes are present:

1. ✅ Neo4j query for Contact→ENGAGED_ON→Deal + Contact→ATTENDED→Interaction
2. ✅ ContactEvidence.interaction_count populated from ATTENDED relationship counts
3. ✅ New fields: last_interaction_date, engagement_recency_days, deal_role, role_confidence
4. ✅ Enhanced LLM prompts with engagement frequency, recency, and [RISK] flags
5. ✅ Graceful degradation when upstream Neo4j data not available
6. ✅ Read-only — no writes to Neo4j or Postgres

This prompt addresses syncing the local branch and ensuring documentation is current.

## Tasks

### 1. Sync Local Main Branch

The local main (commit 63a6a70) is 2 commits behind origin/main. Run:
```
git checkout main
git pull origin main
```

Verify the contact enrichment PR is in local main:
```
git log --oneline -5
```

You should see commit `3311795` (Merge PR #14) and/or `46a2295` (feat: consume Neo4j contact engagement data).

### 2. Review and Update Documentation

This repo has minimal documentation (no README.md, no CLAUDE.md). Check:

**Check for PHILOSOPHY.md** — the review mentioned this exists. Read it and verify it's still current. If it discusses the forecasting pipeline, add a note about the new contact engagement signals:
- Per-contact `interaction_count` computed from Neo4j ATTENDED relationships
- `deal_role` from ENGAGED_ON relationship (champion, economic_buyer — LLM-extracted by action-item-graph)
- Risk flags for dormant or unengaged contacts in LLM prompts
- Graceful degradation when upstream hasn't deployed contact enrichment yet

**Check tasks/todo.md** — if it exists, ensure the contact enrichment work is documented as complete.

**Check tasks/lessons.md** — if it exists, verify any lessons from the contact enrichment implementation are captured.

If the repo has no documentation infrastructure at all (no README, no CLAUDE.md, no PHILOSOPHY.md), then create a brief note in `tasks/todo.md` documenting:
```
## Contact Enrichment Integration (Complete)
- Added Neo4j queries for Contact→ENGAGED_ON→Deal and Contact→ATTENDED→Interaction
- ContactEvidence now includes interaction_count, engagement_recency_days, deal_role
- LLM prompts show engagement signals with [RISK: DORMANT] and [RISK: NO ENGAGEMENT] flags
- Graceful degradation when upstream Neo4j data not yet available
- stakeholder_familiarity scorer now produces real scores
```

### 3. Verify Tests Pass

Run the test suite to confirm everything is healthy:
```
cd /Users/peteroneil/opportunity-forecasting
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

- Do not modify any source code (this is a read-only consumer — no code changes expected)
- Do not modify tests
- Do not push unless explicitly asked
- Do not create branches
