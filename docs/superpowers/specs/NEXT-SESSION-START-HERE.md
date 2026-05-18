# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-18 (M5 verification ran — empirically validated M4's deliverable (cold-inbound capture via orchestrator §4.2) through plan §10.3 Steps 1-4; shipped M5.1 ON-CONFLICT fix (PR #11, merged as `79862b6`); Steps 5-7 blocked by httpx-timeout bug discovered downstream of M4 — queued as M5.2).
**Status:** ⚠️ **PHASE_1_EMAIL_PIPELINE_M4_VERIFIED_M5.1_SHIPPED_M5.2_NEXT** — M4's switch-flipped deliverable is empirically verified end-to-end on the orchestrator side. M5.1 hardened the signal-flush path (one-line `ON CONFLICT` SQL fix to resolve a latent Phase-1 bug exposed by M4's reachability change). Three downstream bugs (one defense-in-depth, two real production) queued as M5.2 — M5.2 gates Phase 2 planning per the user's "complete Phase 1 before next" rule.

---

## SESSION SCOPE FOR THE NEXT SESSION

**This session is M5.2 — ship three bug fixes, then complete M5 verification (Steps 5-12 + §11 invariants).**

| # | Bug | Severity | Where it lives | Fix shape |
|---|---|---|---|---|
| 1 | `agent_action_core_client` httpx 120s timeout < agent's observed 145s on sparse-web domains | **Medium-High** (real prod impact: cold-outreach prospects from low-web-presence companies stick in `status='creating'`) | `live-transcription-fastapi/services/agent_action_core_client.py:43` | Bump `_DEFAULT_TIMEOUT_SECONDS` to 300.0; OR use stream/poll mode if agent supports it |
| 2 | `pending_account_approval` missing from `_INGEST_SUCCESS_STATUSES` | Low (synthetic-injection HTTP wrapper only; production webhook paths unaffected) | `eq-email-pipeline/src/api/routes.py:540` | One-line: add `'pending_account_approval'` to the `frozenset` |
| 3 | `pending_signal_dedup` NULL-DISTINCT semantics doesn't dedupe email signals | Low (defense-in-depth gap; orchestrator's `email_exists` is the actual sequential-dup guard) | eq-frontend Prisma `@@unique` on `PendingAccountMappingSignal` → add `nullsNotDistinct: true` (Prisma 5.7+, Postgres 15+) | Coordinated Prisma migration + Vercel deploy + verify Neon |

Recommended sequencing: **bug #1 first** (highest impact, smallest blast radius — single file in live-transcription-fastapi); then **bug #2** (live-transcription-fastapi or eq-email-pipeline depending on actual layering — verify); then **bug #3** (cross-repo Prisma); then **resume M5 E2E** Steps 5-12.

If context budget is tight, **bug #1 + bug #2 + resume Steps 5-12** is the priority core; bug #3 (NULL semantics) is defensible to split into M5.3.

---

## CRITICAL — what's verified end-to-end (production)

| Plan §10.3 step | Behavior | Status | Evidence |
|---|---|---|---|
| Step 1 | Synthesize cold-inbound email | ✓ | `/api/emails/ingest` POST returns orchestrator status |
| Step 2 | Orchestrator §4.2 BUSINESS path fires | ✓ | Returns `status='pending_account_approval'` (HTTP wrapper currently maps to 500; orchestrator behavior correct) |
| Step 3a | `pending_interactions` row exists with correct fields | ✓ | Verified for `interaction_id=a8f21189-...` (since cleaned up) |
| Step 3b | `pending_account_mappings` queue row, status='pending' | ✓ | Verified for `queue_id=3415f8ba-...` (since cleaned up) |
| Step 3c | `pending_account_mapping_signals` row, interaction_id matches | ✓ | Verified (since cleaned up) |
| Step 3d-f | NO raw_interactions, NO emails, NO interaction_summaries | ✓ | All 0 rows |
| Step 4 | Duplicate webhook returns `skipped_duplicate`, only 1 pending row | ✓ | HTTP 200; pending_interactions count unchanged |
| Step 5 | POST `/approve` returns HTTP 202 + correct workflow_id format | ✓ | `workflow_id = queue-{queue_id}:approval-{attempt_id}` matches LOCKED-4 |
| Step 6 | DBOS workflow reaches `status='success'` | ❌ BLOCKED | Stuck at function 3 (`call_agent_enrich`) — httpx 120s timeout < agent 145s (M5.2 bug #1) |
| Steps 7-12 | Promote → enrichment → idempotency → downstream | ⏳ NOT REACHED | Pending M5.2 fix |

**M4's specific deliverable** — "cold-inbound from unknown business sender stores in pending_interactions + queue + signals, dedupes correctly on duplicate webhooks" — IS verified. The /approve → workflow → enrichment chain is downstream and has its own M2-era bugs.

### Production state at end-of-prior-session

- **eq-email-pipeline main HEAD**: `79862b6` (M5.1 merged; Railway deployment `7c15697e` SUCCESS; /api/health 200; all 3 checks ok).
- **live-transcription-fastapi main HEAD**: `4979cdf` (M5 handoff docs only; M2 code unchanged).
- **eq-frontend**: unchanged from M1 deploy `de586bbc`.
- **Test tenant `11111111-...` cleaned**: 0 active pending_interactions, 0 active queue rows from M5 verification artifacts (post-session cleanup per LOCKED-11; CANCELLED workflow row remains in dbos.workflow_status).
- **Neo4j, Pinecone, EventBridge, SQS**: untouched (Steps 8-12 not reached, so no enrichment writes happened).
- **`Cold Prospect` account** (`a1f9e717-...` created by agent as side-effect of timed-out enrich): DELETED.

### What M5.2 verifies after the 3 fixes

After M5.2 ships, walk the FULL plan §10.3 (Steps 1-12) on a FRESH synthetic email, then §11 (22 invariants). Phase-1-email-pipeline initiative signs off when:
- M5.2 fixes merged + deployed
- Plan §10.3 Steps 1-12 all PASS on a fresh E2E
- Plan §11 invariants all hold (Schema verified in pre-flight; Code via grep; Behavior via E2E)
- No new V1 limitations beyond the 5 already documented

---

## Mandatory read order for the next session (~15 min)

1. **This file.**
2. **The checkpoint** loaded via `/context-restore` (the 2026-05-18 save titled `phase-1-email-pipeline-m5-partial-m5.2-next`).
3. **THE PLAN — §10 + §11**: `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md`. Same load-bearing artifact as M5; M5.2 just resumes from Step 5.
4. **M5.1 merged PR** (https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/11) — the SQL fix narrative, Codex R1+R2 trajectory, NULL-semantics deeper finding.
5. **New lessons codified end-of-M5-session** (4 entries at the bottom of `tasks/lessons.md`):
   - "Prisma @@unique materializes as INDEX, not CONSTRAINT — ON CONFLICT must use column-list inference"
   - "Postgres unique indexes default to NULLS DISTINCT — dedup fails for partial-NULL tuples"
   - "Synthetic test domains stress agent enrichment latency budgets"
   - The pre-existing "Postgres array concatenation is NULL-poisoned" + "Scope to plan-explicit framing" (from M4)

---

## Execution sequence — M5.2

### Pre-flight (run BEFORE any M5.2 work)

Same 5 pre-flight checks as M5 plus one new check (M5.1 in production):

1. **Production health (both services):**
   ```bash
   curl -sS -o /dev/null -w "live-fastapi: %{http_code}\n" \
     https://live-transcription-fastapi-production.up.railway.app/health
   curl -sS -o /dev/null -w "eq-email-pipeline: %{http_code}\n" \
     https://email-pipeline-production.up.railway.app/api/ping
   curl -sS https://email-pipeline-production.up.railway.app/api/health
   ```
   Expected: 200 / 200 / status=ok.

2. **M5.1 code is live on origin/main (NEW):**
   ```bash
   git -C /Users/peteroneil/eq-email-pipeline log --oneline -3
   # Expected top: 79862b6 M5.1: fix ON CONFLICT target for pending_signal_dedup ...
   ```

3. **LOCKED-17 collision check.**
4. **DBOS workflow drain check** (no PENDING/RUNNING).
5. **Baseline pending_interactions count** (should be 0 active in test tenant after M5 cleanup).

### Fix #1 — agent_action_core_client httpx timeout (live-transcription-fastapi)

Branch off main: `fix/m5-2-agent-client-timeout`.

Edit `services/agent_action_core_client.py:43`:
```python
# OLD
_DEFAULT_TIMEOUT_SECONDS = 120.0
# NEW
_DEFAULT_TIMEOUT_SECONDS = 300.0  # M5.2: agent observed at 145s on sparse-web domains
```

Plus consider:
- A test that asserts the timeout config matches a documented invariant (e.g., "must be ≥ 240s to accommodate observed worst-case agent latency").
- Surface the configurable env var (`AGENT_HTTPX_TIMEOUT_SECONDS`) for ops tuning.

Codex review BEFORE merge. Expected: 1 round, CLEAN (it's a one-line change).

Verify post-merge: Railway redeploy SUCCESS + /health 200.

### Fix #2 — _INGEST_SUCCESS_STATUSES adds pending_account_approval (eq-email-pipeline)

Branch off main: `fix/m5-2-ingest-success-pending-status`.

Edit `eq-email-pipeline/src/api/routes.py:540`:
```python
# OLD
_INGEST_SUCCESS_STATUSES = frozenset({
    "processed",
    "processed_light",
    "skipped_duplicate",
    "skipped_internal",
    "skipped_internal_irrelevant",
})
# NEW (add one entry)
_INGEST_SUCCESS_STATUSES = frozenset({
    "processed",
    "processed_light",
    "skipped_duplicate",
    "skipped_internal",
    "skipped_internal_irrelevant",
    "pending_account_approval",  # M5.2: orchestrator §4.2 path (M4)
})
```

Test: update `tests/test_routes_ingest.py` to add a case where the orchestrator returns `pending_account_approval` → expect HTTP 200.

Codex review BEFORE merge. Expected: 1 round, CLEAN.

### Fix #3 — pending_signal_dedup NULLS NOT DISTINCT (eq-frontend Prisma)

Branch off main in eq-frontend: `fix/m5-2-pending-signal-dedup-nulls-not-distinct`.

Edit `eq-frontend/prisma/schema.prisma` — find `PendingAccountMappingSignal` model:
```prisma
// OLD
@@unique([queueId, contactEmail, sourceType, interactionId, calendarEventId], map: "pending_signal_dedup")
// NEW (verify Prisma version supports `nullsNotDistinct` — 5.7+)
@@unique([queueId, contactEmail, sourceType, interactionId, calendarEventId], map: "pending_signal_dedup", nullsNotDistinct: true)
```

Generate migration: `cd eq-frontend && pnpm prisma migrate dev --name pending_signal_dedup_nulls_not_distinct --create-only`.

Review the generated SQL (should be `DROP INDEX` + `CREATE UNIQUE INDEX ... NULLS NOT DISTINCT`).

Update the M5.1 test in `eq-email-pipeline/tests/test_pending_account_mappings.py`:
- `test_null_calendar_event_id_does_NOT_dedupe` becomes `test_null_calendar_event_id_DOES_dedupe_after_nulls_not_distinct_migration`.
- Flip assertion from `n_rows == 2` to `n_rows == 1`.
- This test will only pass after the eq-frontend migration deploys to production Neon.

Codex review BEFORE merge on eq-frontend. Expected: 1-2 rounds.

Coordinate deploy: eq-frontend merges → Vercel `prisma migrate deploy` → verify Neon schema (pg_indexes shows `NULLS NOT DISTINCT`) → then merge eq-email-pipeline test update.

### Step 5-12 re-run after M5.2 fixes ship

Use a FRESH synthetic UUID (don't reuse `f1c4290c2155`). Walk plan §10.3 Steps 1-12 sequentially. Step 6 should now complete (workflow reaches `status='SUCCESS'`). Steps 7-12 should all PASS.

### Step 13 — Plan §11 acceptance invariants checklist

Walk every checkbox. Use the same MCP tools (Neon, Neo4j, Pinecone, Railway).

### Step 14 (optional) — Plan §10.4 rollback drill

Same as M5 prompt. Gated on user approval per LOCKED-11.

### Step 15 — Document M5.2 results + new lessons

Update `tasks/lessons.md` if anything new surfaces. Update `MEMORY.md` to reflect M5.2 status.

### Step 16 — Phase-1-email-pipeline initiative SIGN-OFF

Same criteria as the M5 prompt. If §11 invariants all hold AND no new P0/P1 bugs, the initiative is COMPLETE. Surface to user. Phase 2 is the natural next initiative (with its own planning session).

---

## LOCKED decisions (21 total; do NOT re-litigate)

[same 21 as M5 session — see prior NEXT-SESSION-START-HERE or M5 prompt file for full list]

M5.2 doesn't add any new LOCKED decisions; the 3 fixes are bug-corrections within the existing decision frame.

---

## Acknowledged V1 limitations (NOT regressions; documented + bounded)

[same 5 as M5 session]

M5.2 changes don't add new V1 limitations. Bug #3 (NULL-DISTINCT) is removed from the "acknowledged limitation" framing once the migration deploys — it becomes a fixed bug, not a limitation.

---

## Stop conditions (hard — surface to user)

- `/context-restore` returns NO_CHECKPOINTS or the wrong checkpoint title.
- MEMORY.md status isn't `PHASE_1_EMAIL_PIPELINE_M4_VERIFIED_M5.1_SHIPPED_M5.2_NEXT`.
- Production /api/health returns non-200 OR any of postgres/neo4j/eventbridge is not "ok".
- M5.1 code is not at `79862b6` on eq-email-pipeline origin/main (someone reverted).
- LOCKED-17 collision check shows concurrent agent in last hour AND M5.2 about to write destructive SQL.
- ANY of the 3 fixes introduces test regressions beyond the M4 baseline (459 tests for eq-email-pipeline).
- M5.2 Step 6 workflow STILL stalls after bug #1 fix deploys — that means there's a 4th bug; surface immediately.
- A NEW V1 limitation surfaces during Steps 7-12 verification that isn't in the documented 5.

---

## Handoff artifacts from the prior session (2026-05-18 end-of-day)

- **M5.1 merged**: https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/11 → `79862b6` (2 Codex rounds; R2 CLEAN; 1 commit of R1 fixes for NULL-semantics test restructure).
- **Comprehensive checkpoint**: `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/<timestamp>-phase-1-email-pipeline-m5-partial-m5.2-next.md`.
- **The plan (unchanged)**: `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md`.
- **Next-session prompt** (paste-ready): `docs/superpowers/specs/2026-05-19-m52-next-session-prompt.md`.
- **New lessons codified** in `tasks/lessons.md`: "Prisma @@unique materializes as INDEX", "Postgres NULLS DISTINCT", "Synthetic test domains stress agent latency".
- **Feedback rule saved**: `memory/feedback_complete_phase_before_next.md` — "Discovered defects from verification get their own follow-up milestone (M5.1, M5.2) that gates the next phase; never 'defer to Phase 2' as a parking lot."

---

## Phase 2 preview (still not Phase 2 scope; gated on M5.2 completion)

[same as M5 prompt: Neo4j MERGE-everywhere, identity state machine, outbound capture, EmailPromoted DLQ, queue UI]

Phase 2 PLANNING does not start until M5.2 ships AND M5 §10.3 Steps 1-12 + §11 invariants all PASS.
