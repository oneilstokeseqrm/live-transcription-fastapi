# Next Session Opening Prompt (2026-05-19, Phase 2 brainstorming)

Paste the block below as the opening message of the next Claude session.

Written 2026-05-18 end-of-day after Phase-1-email-pipeline INITIATIVE COMPLETE sign-off. All 8 milestones shipped + verified; the cold-inbound capture pipeline works end-to-end in production. Phase 2 is now unblocked — this session opens brainstorming.

---

```
You're picking up the Contact Quality and Account-Anchoring Initiative —
a multi-phase data-quality project on an AI-native customer intelligence
platform.

═══════════════════════════════════════════════════════════════════════
WHERE WE ARE
═══════════════════════════════════════════════════════════════════════

Phase 1-email-pipeline: ✅ INITIATIVE COMPLETE (2026-05-18).
  All 8 milestones (M1, M2, M3, M4, M5.1, M5.2, M5.3, M5.4) shipped,
  deployed, and verified end-to-end on a fresh UUID against the test
  tenant. §10.3 PASS; §11 18/22 invariants PASS (1 soft, 3 out-of-scope).
  Multi-writer Neo4j coexistence proven on the Interaction node.
  Cold-inbound from unknown business senders now creates a pending
  queue entry → /approve → AI account research → email promotion →
  full enrichment pipeline. Test tenant atomically cleaned.

Phase 2: 🟡 PLANNING UNBLOCKED.
  This session opens brainstorming. NOT milestone execution. NOT new
  feature code. Brainstorm → CEO review → design doc → eng review →
  THEN execution sessions.

═══════════════════════════════════════════════════════════════════════
SESSION SCOPE — BRAINSTORMING + DIRECTION-SETTING
═══════════════════════════════════════════════════════════════════════

IN scope:
  1. Re-orient with user on Phase 1 (what surprised? what felt easy /
     hard? shared mental model before scoping Phase 2).
  2. Walk Phase 2 candidate backlog (11 items in NEXT-SESSION-START-
     HERE.md). Discuss priority + sequencing with user.
  3. Invoke /office-hours for YC-style 6-forcing-question brainstorm
     (demand reality, status quo, desperate specificity, narrowest
     wedge, observation, future-fit). Build product narrative.
  4. Invoke /plan-ceo-review on the brainstorm output. Decide whether
     to expand scope (10x mode) or hold scope (rigor mode).
  5. Draft Phase 2 design doc at
     `docs/superpowers/specs/2026-05-XX-phase-2-design.md`. NOT an
     implementation plan — design intent + architecture sketch.
  6. /plan-eng-review for architecture (if user is ready to lock).
  7. /plan-design-review if Phase 2 has UI/UX scope.
  8. Locking new decisions builds on the existing 22 LOCKED decisions
     from Phase 1.

OUT of scope:
  - Writing implementation plans (those come in a follow-on session
    once design + review is locked).
  - Coding any Phase 2 features.
  - Deploying anything.
  - Touching production data on test tenant (clean baseline preserved
    from Phase 1 sign-off; preserve for Phase 2 E2E tests).

═══════════════════════════════════════════════════════════════════════
PHASE 2 CANDIDATE BACKLOG (the brainstorm input)
═══════════════════════════════════════════════════════════════════════

From plan §17.11 + V1 limitations + Codex challenge deferrals + user
roadmap signals:

  1.  Neo4j MERGE-everywhere refactor (closes V1 #2 partial-retry
      corruption; Chunk + Thread.message_count idempotency)
  2.  Contact identity state machine (pending → confirmed → merged;
      the contact analog of pending_interactions)
  3.  Outbound pending path (M4 deliberately scoped to inbound/
      internal; outbound currently silent-drops to unknown business)
  4.  Queue UI (user-facing approve/ignore/map screen — currently
      API-only)
  5.  Audit log table (closes V1 #1 personal/internal anchor log+drop)
  6.  Outlook NULL-IMID Postgres-side dedup strategy (Codex consult
      caveat 3)
  7.  `ensure_constraints` hardening (Codex challenge #2 — currently
      swallows DDL errors)
  8.  Shared MERGE-key contract document (architectural; LOCKED-22
      formalized but not yet documented for future writers)
  9.  Cross-queue link fill-in algorithm (plan §5.2)
  10. Re-open after Ignore + new signal lifecycle
  11. 20 orphan Interaction nodes hygiene (low priority; cleanup)

These are CANDIDATES — brainstorm should surface priorities, sequencing,
and any not-yet-listed items.

═══════════════════════════════════════════════════════════════════════
MANDATORY READS BEFORE BRAINSTORM
═══════════════════════════════════════════════════════════════════════

Same discipline as prior sessions: complete EVERY read before any
brainstorm or design action. Per the feedback memory
`complete-all-handoff-reads-before-action`.

1. Run `/context-restore`. Expect a checkpoint titled
   "phase-1-email-pipeline-initiative-complete-phase-2-next" dated
   2026-05-18 end-of-day at:
     ~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/
     checkpoints/<timestamp>-phase-1-email-pipeline-initiative-complete-
     phase-2-next.md
   Load it. If /context-restore returns NO_CHECKPOINTS or different
   title, STOP and surface.

2. Read MEMORY.md (auto-loads). Confirm status reads
   `PHASE_1_EMAIL_PIPELINE_INITIATIVE_COMPLETE`. If anything else,
   STOP and surface — memory state may have drifted.

3. READ THE WAYFINDING DOC (full Phase 2 scope, candidate backlog,
   reference paths):
     /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/
     superpowers/specs/NEXT-SESSION-START-HERE.md

4. Read the PHASE 1 DESIGN DOC (the long-term roadmap context):
     /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/
     superpowers/specs/2026-05-12-contact-quality-initiative-design.md
   §9 phased trajectory documents Phase 2 hooks.

5. Read the closed PHASE 1 PLAN (architectural decisions + V1
   limitations + Codex challenge deferrals — load-bearing for Phase 2
   candidate framing):
     /Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/
     2026-05-17-pending-interactions-cold-inbound-fix.md
   §17.11 has explicit Phase 2 implications.

6. Skim the LESSONS file for any cross-session patterns to apply:
     /Users/peteroneil/EQ-CORE/live-transcription-fastapi/tasks/
     lessons.md

7. After all reads, write ONE paragraph confirming the state:
   Phase 1 complete, baseline preserved, intent to /office-hours
   brainstorm Phase 2 priority. Then proceed.

═══════════════════════════════════════════════════════════════════════
USER POSTURE (load-bearing — do not violate)
═══════════════════════════════════════════════════════════════════════

Non-developer founder. Make confident technical decisions; surface
only product / strategic decisions. Strict OSS only.

User's rules:
  1. Complete Phase N before Phase N+1 planning.
     ✅ Phase 1 closed → Phase 2 planning is now allowed.
  2. Cutting-edge-startup approach. No shortcuts unless the shortcut
     IS the correct architecture (verified by investigation).
  3. AI agent doesn't push or merge without per-action authorization.
     (Mostly N/A for brainstorming — no destructive actions planned.)
  4. Plain-English explanations when user asks "why" / "what
     happened" — user is non-developer; technical accuracy paired
     with clear framing.
  5. Investigate thoroughly; use the right gstack skills:
     `/office-hours`, `/plan-ceo-review`, `/plan-eng-review`,
     `/plan-design-review`, `/codex consult`.
  6. Don't go beyond scope. For brainstorming: don't try to lock
     decisions before user has reviewed brainstorm output. Don't
     write implementation code until design + review is done.

═══════════════════════════════════════════════════════════════════════
STOP CONDITIONS (hard — surface to user)
═══════════════════════════════════════════════════════════════════════

  - /context-restore returns NO_CHECKPOINTS or wrong checkpoint
  - MEMORY.md status isn't PHASE_1_EMAIL_PIPELINE_INITIATIVE_COMPLETE
  - Production /api/health returns non-200 on any service (Phase 1
    regression)
  - Test tenant has leftover M5.4 E2E artifacts (clean baseline must
    be preserved between sessions)
  - User asks to write implementation code before brainstorm + design
    doc + review gauntlet is done

═══════════════════════════════════════════════════════════════════════
KEY REFERENCE PATHS
═══════════════════════════════════════════════════════════════════════

WAYFINDING (Phase 2 brainstorming scope):
  /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/
  specs/NEXT-SESSION-START-HERE.md

ORIGINAL DESIGN DOC (multi-phase trajectory):
  /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/
  specs/2026-05-12-contact-quality-initiative-design.md

CLOSED PHASE 1 PLAN (load-bearing for Phase 2 candidate framing):
  /Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/
  2026-05-17-pending-interactions-cold-inbound-fix.md

LESSONS:
  /Users/peteroneil/EQ-CORE/live-transcription-fastapi/tasks/lessons.md

═══════════════════════════════════════════════════════════════════════

Start with /context-restore. Then the mandatory reads. Then a quick
re-orient with user on Phase 1 (no need to re-walk every milestone —
ask which ones they want to revisit). Then /office-hours to brainstorm
Phase 2 priority. Then CEO + eng review. Then design doc.

The session is ~1-3 hours focused work; design doc + plan-eng-review
may carry into a follow-on session. No deploys. No new code yet.
```

---

## Notes for the user pasting this

This prompt is the Phase 2 brainstorming kickoff. Key differences vs prior milestone-execution prompts:

- **Status is `PHASE_1_EMAIL_PIPELINE_INITIATIVE_COMPLETE`.** The whole multi-week execution arc is closed.
- **No deploy, no code, no production touching.** This is design-mode session, not execution-mode.
- **Brainstorm-first discipline.** Per user posture rule #6 + the brainstorming skill priority, don't lock decisions until /office-hours + CEO review have run.
- **11 candidate Phase 2 items pre-staged.** Brainstorm should reshuffle and prioritize — possibly drop some, possibly add more.
- **Expected runtime: ~1-3 hours.** Design doc + reviews may carry to a follow-on session.
