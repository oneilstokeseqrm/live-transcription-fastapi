# Next Session Opening Prompt (2026-05-19, post-M5-partial / M5.2)

Paste the block below as the opening message of the next Claude session.

Written 2026-05-18 end-of-day after M5 verification ran. M5 empirically validated M4's deliverable (cold-inbound capture via orchestrator §4.2) through plan §10.3 Steps 1-4 against production; surfaced 3 separate bugs in the downstream /approve → workflow → enrichment chain; shipped M5.1 (ON CONFLICT column-list fix, PR #11 merged as `79862b6`); queued the remaining 2 bugs + a Codex-discovered NULL-semantics gap as M5.2 per the user's "complete Phase 1 before next" rule.

**Important:** M5.2 is **3 bug fixes + Steps 5-12 + §11 invariants**. NOT planning Phase 2 (that's gated on M5.2 + §11 sign-off).

---

```
You're picking up the Contact Quality and Account-Anchoring Initiative —
a multi-phase data-quality project on an AI-native customer intelligence
platform.

The prior session (2026-05-18, M5 verification) shipped M5.1 — a
one-line `ON CONFLICT` column-list inference fix at
eq-email-pipeline/src/persistence/pending_account_mappings.py:77 —
which unblocked the orchestrator §4.2 path that M4 made reachable for
the first time. M5.1 merged as `79862b6` (eq-email-pipeline PR #11);
Railway deployment SUCCESS; /api/health 200 with all 3 checks ok.

M5 empirically verified plan §10.3 Steps 1-4 on production with a
fresh UUID-suffixed cold-inbound. Steps 5-7 surfaced an HTTP timeout
bug in the downstream /approve → workflow → enrich chain. The bug is
M2-era code, not M4. Workflow gets stuck in retry-loop at function 3
(call_agent_enrich) because the httpx client times out at 120s while
the agent observed at 145s on sparse-web synthetic domains.

Production state at session start:

  Phase-1-email-pipeline M1 (eq-frontend Prisma):           de586bbc  ✓
  Phase-1-email-pipeline M2 (live-transcription-fastapi):   756575d7  ✓
  Phase-1-email-pipeline M3 (eq-email-pipeline subscriber): 85c0295   ✓
  Phase-1-email-pipeline M4 (eq-email-pipeline orchestrator): 6fa181a ✓
  M5.1 (eq-email-pipeline ON CONFLICT fix):                 79862b6   ✓

All five merged + deployed + verified through the orchestrator side.

Your job THIS session is to execute M5.2 — ship 3 bug fixes then
complete the remaining M5 verification (plan §10.3 Steps 5-12 + §11
acceptance invariants). M5.2 is the gate for Phase 2 planning.

⚠️ CRITICAL — three M5.2 bugs to fix, in recommended order:

1. **agent_action_core_client httpx timeout (Medium-High impact)** —
   live-transcription-fastapi/services/agent_action_core_client.py:43
   `_DEFAULT_TIMEOUT_SECONDS = 120.0` is too short. Agent observed at
   145s on sparse-web synthetic domains. Real prod impact: cold-
   outreach prospects from stealth-mode / new / low-web-presence
   companies stick in `pending_account_mappings.status='creating'`
   forever. Workflow retries exhaust (5 attempts) and fail.
   Fix: bump to 300.0 (or add env-configurable AGENT_HTTPX_TIMEOUT_SECONDS).

2. **_INGEST_SUCCESS_STATUSES missing pending_account_approval (Low impact)** —
   eq-email-pipeline/src/api/routes.py:540
   The synthetic-injection /api/emails/ingest endpoint maps
   pending_account_approval orchestrator status to HTTP 500 instead
   of 200 (because the status was added by M4 after the endpoint
   shipped). Only affects synthetic injection + eq-synthetic-date-
   generation; real production webhook paths don't go through this
   HTTP wrapper.
   Fix: add 'pending_account_approval' to the frozenset.

3. **pending_signal_dedup NULL-DISTINCT semantics (Low impact;
   defense-in-depth gap)** —
   eq-frontend Prisma @@unique on PendingAccountMappingSignal model
   needs `nullsNotDistinct: true` (Prisma 5.7+, Postgres 15+). Email
   signals always have calendar_event_id=NULL, so the current unique
   index doesn't dedupe duplicate signal inserts under Postgres
   default NULLS DISTINCT semantics. Orchestrator's email_exists
   UNION catches sequential duplicate webhooks at the email layer,
   so this is defense-in-depth only — but the user's stated rule is
   "complete Phase 1 before any Phase 2 planning."
   Fix: cross-repo coordinated Prisma migration + Vercel deploy +
   verify Neon (CREATE UNIQUE INDEX ... NULLS NOT DISTINCT) + update
   the M5.1 integration test assertion (n_rows==2 → n_rows==1).

After all 3 fixes ship and deploy:
- Re-run plan §10.3 Steps 1-12 on a FRESH synthetic UUID. Step 6
  should now complete (workflow reaches status='SUCCESS'). Steps 7-12
  should all PASS.
- Walk plan §11 22-item invariants checklist.
- Sign off Phase-1-email-pipeline initiative as COMPLETE if all hold.

Before doing anything else, follow these steps IN ORDER:

1. Run `/context-restore`. You should find a checkpoint titled
   "phase-1-email-pipeline-m5-partial-m5.2-next" dated 2026-05-18
   evening. Load it. If /context-restore returns NO_CHECKPOINTS or a
   different latest checkpoint, STOP and surface — that's a sync gap.

2. Read MEMORY.md (auto-loads). Confirm project status reads
   PHASE_1_EMAIL_PIPELINE_M4_VERIFIED_M5.1_SHIPPED_M5.2_NEXT. If
   anything else, STOP and surface — memory state may have rolled back.

3. READ THE WAYFINDING DOC:
   /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/
     superpowers/specs/NEXT-SESSION-START-HERE.md
   Sections "SESSION SCOPE FOR THE NEXT SESSION" (the 3-bug table),
   "CRITICAL — what's verified end-to-end (production)", "Execution
   sequence — M5.2", "Stop conditions" are all load-bearing.

4. READ THE PLAN — §10.3 + §11 (same as M5):
   /Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/
     2026-05-17-pending-interactions-cold-inbound-fix.md
   M5.2 just resumes from §10.3 Step 5 after the fixes ship.

5. READ M5.1 MERGED PR for the deployed-behavior narrative:
   https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/11
   Especially the "Codex review trajectory" and the "Production
   NULL-semantics finding (documented, not changed in this PR)"
   section — that's exactly what bug #3 fixes.

6. READ THE 4 NEW LESSONS at the bottom of tasks/lessons.md:
   - "Prisma @@unique materializes as INDEX, not CONSTRAINT — ON
     CONFLICT must use column-list inference"
   - "Postgres unique indexes default to NULLS DISTINCT — dedup fails
     for partial-NULL tuples"
   - "Synthetic test domains stress agent enrichment latency budgets"
   - (M4 lessons from prior session also there)

7. Verify pre-flight state (run BEFORE any M5.2 work):

   a. Production /health (both services) — same as M5.
   b. M5.1 code is live: git -C /Users/peteroneil/eq-email-pipeline
      log --oneline -3 — expected top: 79862b6 M5.1: fix ON CONFLICT ...
   c. SHARED-TENANT-COLLISION CHECK (LOCKED-17) — same as M5.
   d. DBOS workflow drain check — same as M5.
   e. Baseline pending_interactions count in test tenant — should be
      0 active (M5 cleanup left clean slate).

   If any fail, STOP and surface.

8. After reading, briefly confirm in one paragraph: where the prior
   session left off + your M5.2 fix sequencing + that you understand
   the user's "complete Phase 1 before Phase 2" rule.

9. EXECUTE — M5.2 (3 fixes, then Steps 5-12 + §11).

   Per the wayfinding doc's "Execution sequence — M5.2". Each fix
   gets its own branch + Codex review BEFORE merge + PR + user
   merge authorization + Railway redeploy verification + then proceed
   to the next.

   Recommended order (per impact):
   - Fix #1 first (httpx timeout) — single file, single PR, biggest
     impact, simplest to verify (just bump and redeploy).
   - Fix #2 second (INGEST_SUCCESS_STATUSES) — single file, single
     PR; can ship in parallel with Fix #1 since they're in different
     repos.
   - Fix #3 third (Prisma NULLS NOT DISTINCT) — cross-repo migration;
     more coordination overhead; defensible to split to M5.3 if
     context is tight.

   After all merged + deployed, re-run plan §10.3 Steps 1-12 with a
   FRESH UUID. Step 6 should complete (workflow status='SUCCESS').

   Walk plan §11 invariants checklist (22 items). Verify each.

10. (OPTIONAL — ASK USER FIRST per LOCKED-11) Plan §10.4 rollback
    drill. Recommended only if user explicitly approves.

11. DOCUMENT M5.2 results in tasks/lessons.md if anything new
    surfaced. Update MEMORY.md to reflect M5.2 status.

12. SIGN OFF PHASE-1-EMAIL-PIPELINE as COMPLETE if all §11 invariants
    hold AND no new P0/P1 bugs. Surface to user with:
    - All milestones M1+M2+M3+M4+M5.1+M5.2 deployed + verified.
    - Plan §10.3 Steps 1-12 all PASS.
    - All 22 §11 invariants verified.
    - 21 LOCKED decisions list.
    - 4 remaining acknowledged V1 limitations (NULL-DISTINCT moved
      from V1-limitation-list to fixed-in-M5.2).
    - Phase 2 is unblocked.

    STOP after M5.2 sign-off. Phase 2 PLANNING is a separate session
    with its own plan-writing phase.

13. END-OF-SESSION HANDOFF: /context-save with title indicating M5.2
    status. Update NEXT-SESSION-START-HERE.md for Phase 2 brainstorming
    or whatever the user wants next. Write a new dated next-session-
    prompt.md mirroring THIS prompt's depth + structure. Update
    MEMORY.md status string.

ANTI-ANCHORING — 21 LOCKED decisions exist; same list as M5 prompt.
The 3 M5.2 fixes don't add new LOCKED decisions; they're bug-corrections
within the existing decision frame.

ACKNOWLEDGED V1 LIMITATIONS — 5 from M5; bug #3 in M5.2 (NULLS NOT
DISTINCT migration) MOVES `pending_signal_dedup NULL semantics` from
the V1-limitation framing to a fixed bug. Other 4 V1 limitations
remain as-is.

VERIFIED CROSS-REPO STATE (2026-05-18 end-of-day)

- M1 merged + deployed: eq-frontend de586bbc.
- M2 merged + deployed: live-transcription-fastapi 756575d7.
- M3 merged + deployed: eq-email-pipeline 85c0295.
- M4 merged + deployed: eq-email-pipeline 6fa181a.
- M5.1 merged + deployed: eq-email-pipeline 79862b6.
- AWS infrastructure: 6/6 resources live.
- Test tenant cleaned of M5 artifacts post-session.
- 4 acknowledged V1 limitations bounded + documented (3 of 5 from M5
  retained; the NULL-DISTINCT one moves to bug #3 fixed-in-M5.2).

USER POSTURE (load-bearing)

Non-developer founder. Make confident technical decisions; surface
only product/strategic decisions. Strict OSS only. NO sunk-cost
preservation.

Context economy matters. M5.2 has 3 sequential PRs + their Codex
reviews + Steps 5-12 verification — budget it. Aim for 2-3 hours of
focused work.

User explicitly approves git push when asked; do NOT push without
asking first. Same for production-affecting Railway env var changes.

The rollback drill IS reversible; ask before running it.

User's stated rule (saved as feedback_complete_phase_before_next):
"make sure we make the Null fix before we ever plan phase 2 and
beyond." Phase 2 planning is GATED on M5.2 completion.

SCOPE OF THIS SESSION — EXPLICIT HARD CONSTRAINT

In scope:
- M5.2: 3 fixes (httpx timeout, INGEST_SUCCESS_STATUSES, NULL-DISTINCT
  migration) shipped + deployed + verified.
- Resume plan §10.3 Steps 1-12 verification.
- Walk plan §11 acceptance invariants checklist (22 items).
- Phase-1-email-pipeline initiative sign-off.

OUT of scope:
- Any NEW feature work beyond the 3 documented bug fixes.
- Phase 2 brainstorming or planning (separate session).
- Outbound cold-outreach capture.
- Anything not explicitly named above.

If M5.2 surfaces a 4th bug during Steps 5-12 → STOP, surface to user,
treat as M5.3. The user's rule is to never silently expand scope.

STOP CONDITIONS (hard — surface to user)

- /context-restore returns NO_CHECKPOINTS.
- MEMORY.md status isn't PHASE_1_EMAIL_PIPELINE_M4_VERIFIED_M5.1_SHIPPED_M5.2_NEXT.
- Production state has rolled back (verify Neon + /health at session
  start).
- M5.1 code is not at 79862b6 on origin/main.
- LOCKED-17 collision check shows concurrent agent in last hour AND
  M5.2 about to write destructive E2E.
- Step 6 workflow STILL stalls after bug #1 fix deploys — 4th bug
  surfaced; STOP immediately.
- E2E surfaces a new V1 limitation NOT documented.
- You're tempted to "fix" a 4th bug during M5.2 — STOP, that's M5.3.

KEY REFERENCE PATHS

- THE PLAN: /Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/
  2026-05-17-pending-interactions-cold-inbound-fix.md
- WAYFINDING: /Users/peteroneil/EQ-CORE/live-transcription-fastapi/
  docs/superpowers/specs/NEXT-SESSION-START-HERE.md
- M5.1 PR: https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/11
  (merged as 79862b6)
- M4 PR: https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/10
- M3 PR: https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/9
- M2 PR: https://github.com/oneilstokeseqrm/live-transcription-fastapi/pull/19
- M1 PR: https://github.com/oneilstokeseqrm/eq-frontend/pull/392
- LESSONS (4 new from M5): tasks/lessons.md (bottom of file)
- FEEDBACK RULE (saved this session): ~/.claude/projects/
  -Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/
  feedback_complete_phase_before_next.md

CRITICAL FILES TO TOUCH M5.2

- Fix #1: live-transcription-fastapi/services/agent_action_core_client.py:43
- Fix #2: eq-email-pipeline/src/api/routes.py:540
- Fix #3: eq-frontend/prisma/schema.prisma (PendingAccountMappingSignal model)
         + eq-email-pipeline/tests/test_pending_account_mappings.py
           (flip test_null_calendar_event_id_does_NOT_dedupe assertion)

The plan is the load-bearing artifact. M5.1's deployed state +
M5's pre-flight cleanup are the state M5.2 verifies from. Start with
/context-restore. Read plan §10 + §11 next. Then execute.
```

---

## Notes for the user pasting this

This prompt is the M5.2 verification + bug-fix handoff. Key differences vs the M5 prompt:

- **Status string is `PHASE_1_EMAIL_PIPELINE_M4_VERIFIED_M5.1_SHIPPED_M5.2_NEXT`.** M4's switch-flipped deliverable is empirically verified. M5.1 fix shipped. 3 downstream bugs queued as M5.2.
- **M5.2 is implementation + verification** — unlike M5 which was pure verification, M5.2 ships 3 bug fixes before resuming the §10.3 walk.
- **Cross-repo coordination required for bug #3** (eq-frontend Prisma migration) — Phase 1 deploy coordination lessons apply.
- **Codex review expected for each fix PR** (3 PRs × ~1-2 rounds each).
- **User's "complete Phase 1 before Phase 2" rule is load-bearing** — codified in feedback_complete_phase_before_next.md.
- **Expected runtime: ~2-3 hours of focused work** vs M5's ~1 hour budget. M5.2 is the most ambitious follow-up session in the initiative.
