# Next Session Opening Prompt (2026-05-17 PM, post-M5)

Paste the block below as the opening message of the next Claude session. Written 2026-05-17 PM after PR #18 (M5 verified-contract tooling) merged. Phase 1.5 main scope is now complete; the next session's primary work is fixing the eq-email-pipeline unknown-sender drop discovered during M5.

---

```
You're working in /Users/peteroneil/EQ-CORE/live-transcription-fastapi.

This is a continuation session for the Contact Quality and Account-
Anchoring Initiative — a multi-phase data-quality project on an
AI-native customer intelligence platform. Phase 1 SHIPPED 2026-05-14.
Phase 1.5 M0-M5 ALL SHIPPED 2026-05-15 + 2026-05-17. As of last
session end, Phase 1.5 main code is complete.

Your job THIS session is to FINISH PHASE 1 for the email pipeline.
The original Phase 1 plan (Task 1.24) committed the orchestrator to
applying three-state branching for sender/recipient resolution +
queuing unknown-business senders. Phase 1 PR #6 shipped logic + a
test for the case where at least one party on the email belongs to
a known account, but did NOT cover the case where ALL parties are
unknown. Cold inbound from a totally new prospect is currently
silently dropped at `insert_email` (raises ValueError → outer
Exception catches → email lost, no queue signal).

This was a Phase 1 commitment — NOT new scope. Full doc with the 4
candidate fix approaches at `tasks/downstream/
eq-email-pipeline-unknown-sender.md`. Recommended approach: C
(separate `pending_interactions` table — explicit pending state,
architecturally honest, no misattribution). User's posture
(2026-05-17 PM): do NOT fake an anchor by tying the email to an
account it doesn't belong to.

Secondary: optional production canary (deferred from M3+M4) and
optional M3.5 outbox drop.

Before doing anything else, follow these steps IN ORDER:

1. Run `/context-restore`. You should find a checkpoint titled
   "phase-1.5-m5-shipped-email-pipeline-gap-next" dated 2026-05-17 PM.
   Load it. If /context-restore returns NO_CHECKPOINTS, STOP and
   surface — that's a sync gap.

2. Read MEMORY.md (auto-loads). Confirm project status reads
   PHASE_1.5_M5_SHIPPED_EMAIL_PIPELINE_GAP_NEXT. If anything else,
   STOP and surface.

3. READ THE COMPREHENSIVE HANDOFF DOC FIRST:
   `docs/superpowers/specs/NEXT-SESSION-START-HERE.md`

   This document was rewritten 2026-05-17 PM specifically for this
   session. Sections "THE NEXT SESSION'S PRIMARY SCOPE" + "4 candidate
   fix approaches" + "LOCKED decisions" are load-bearing.

4. Verify pre-flight state:
   - `gh pr view 18 --json state,mergedAt` → should be MERGED.
   - `git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi status`
     → should be clean on `main`.
   - `git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi log
     --oneline -5` → top should be `95f9084 feat(phase-1.5): M5`.
   - `curl -sS -o /dev/null -w "%{http_code}\n"
     https://live-transcription-fastapi-production.up.railway.app/health`
     should return 200.
   - `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10`
     SHARED-TENANT-COLLISION CHECK. Files modified in last hour =
     concurrent agent hazard. Pause + ask user.

5. READ THESE DOCS IN ORDER (mandatory, ~15-20 min):

   a. The checkpoint (already loaded via /context-restore) — full
      record of M5 + 6 Codex rounds + the email-pipeline finding.

   b. NEXT-SESSION-START-HERE.md (from step 3 above; re-read after
      checkpoint for the full picture).

   c. `tasks/downstream/eq-email-pipeline-unknown-sender.md` — THE
      load-bearing finding. 4 candidate fix approaches sketched.

   d. ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-
      fastapi/memory/project_email_pipeline_unknown_sender_drop.md —
      auto-memory complement.

   e. `docs/superpowers/specs/2026-05-12-contact-quality-initiative-
      design.md` §3 (Hard Rules) + §314 (Option A: never create
      orphan contacts). The design doc says "the interaction is
      recorded with its anchor account" but doesn't address the
      no-anchor-resolvable case for inbound emails.

   f. Quick scan of the actual code paths:
      - `/Users/peteroneil/eq-email-pipeline/src/pipeline/orchestrator.py`
        lines 174-280 (anchor resolution + per-participant loop +
        insert_email call + signal flush)
      - `/Users/peteroneil/eq-email-pipeline/src/persistence/
        postgres.py` lines 195-225 (insert_email's NOT NULL guard
        that's the actual mechanism dropping emails)

   g. `tasks/lessons.md` — bottom entries (shared-infrastructure
      collision, Codex pre-merge gate, the new Review gates lesson
      from M5).

6. After reading, briefly confirm your understanding of where the
   prior session left off and what you plan to do this session (one
   paragraph) before starting work.

7. EXECUTE per `tasks/downstream/eq-email-pipeline-unknown-sender.md`
   Section "Recommended sequence":

   Step 1 — Brainstorm fix approach with the user. Approach C
   (separate `pending_interactions` table) is the recommended,
   cutting-edge-2026, architecturally-honest path. Approach A
   (recipient-as-anchor) was explicitly REJECTED by the user
   2026-05-17 PM (misattributes the email to an account it doesn't
   belong to). Approaches B (NULL account_id) and D (column-level
   pending state) are alternatives if Approach C is infeasible.
   Surface to user via AskUserQuestion; do NOT auto-decide.

   Step 2 — Codex consult on the chosen approach (CSO discipline —
   design-time review BEFORE writing code). Use `codex review` or
   `codex consult` against a design doc / brainstorm output.

   Step 3 — Write implementation plan in
   `eq-email-pipeline/docs/superpowers/plans/2026-05-XX-...md`.

   Step 4 — Schema migration in eq-frontend Prisma IF approach B
   chosen. Coordinate with whoever owns eq-frontend.

   Step 5 — Implement + tests (the M5 verify_consumer_contracts.py
   should now catch any envelope-contract drift introduced by the
   fix).

   Step 6 — Production E2E that asserts: cold-inbound-from-unknown
   email gets queued → user approves → contact materialized →
   backfill envelope fires successfully → downstream MERGE visible.

   Step 7 — Use `scripts/verify_consumer_contracts.py` BEFORE merge
   to confirm no envelope-contract drift. Use `scripts/
   verify_schema.py` for any new SQL in the fix.

8. PRE-MERGE RITUAL (LOCKED-14):
   - `codex review --commit HEAD -c model_reasoning_effort=medium`
     for multi-commit PRs once the cumulative diff > ~1500 lines
     (lesson learned 2026-05-17 PM — the full --base main diff
     caused API timeouts at round 6 of M5; commit-scope cleared in
     <1min). For PR-opening reviews, --base main is still right.
   - SOFT CAP: 4 rounds; extend when real P1s keep surfacing (M5
     went 6 rounds on user authorization).

9. SHARED-INFRASTRUCTURE-COLLISION PROTOCOL (LOCKED-17):
   - LAYER 1: ls ~/.claude/projects/-Users-peteroneil-*/*.jsonl.
     Any file modified in last hour = pause + ask.
   - LAYER 2: per-action confirmation before ANY destructive op on
     the shared test tenant (RUN_DESTRUCTIVE_TESTS=1, ad-hoc DELETE,
     production canary seeding, mcp__neon__run_sql with mutations).

10. End-of-session: /context-save, rewrite NEXT-SESSION-START-HERE.md
    for the NEXT session, write 2026-05-XX-next-session-prompt.md.

ANTI-ANCHORING (LOCKED decisions — do NOT re-litigate)

Full list in NEXT-SESSION-START-HERE.md §"LOCKED decisions". For this
session, the most load-bearing:

(1) DBOS is the substrate.
(2) Single Railway replica + executor_id from RAILWAY_REPLICA_ID.
(3) EventBridge Path A with com.yourapp.transcription source.
(4) Workflow ID format f"queue-{queue_id}:approval-{approval_attempt_id}".
(5) /approve reserves synchronously then enqueues.
(6) Option B test infrastructure (test-tenant scoping in prod Neon).
(8) SQLAlchemy 2.0.49 CAST(:name AS uuid) form.
(9) Materialization requires real raw_interactions (no placeholders).
(10) Codex review BEFORE merging (4-round soft cap).
(11) Per-batch user confirmation for destructive ops on test tenant.
(12) NEW — Codex multi-round reviews: --commit HEAD past ~1500 lines.

PRODUCTION STATE AT THIS SESSION START

- live-transcription-fastapi: main HEAD `95f9084` (M5 verified-contract
  tooling); Railway deploy live + /health 200; DBOS launched with
  executor_id from RAILWAY_REPLICA_ID; queue account-provisioning
  listening with concurrency=5.
- Neon (super-glitter-11265514, branch production): dbos.* schema
  intact + UNIQUE INDEX live + account_provisioning_outbox table
  still present (drop is optional M3.5).
- Test tenant `11111111-1111-4111-8111-111111111111`: state unknown
  at this session start — verify with a quick `SELECT COUNT(*) FROM
  accounts WHERE tenant_id=...` before any destructive op.
- eq-email-pipeline: /Users/peteroneil/eq-email-pipeline at commit
  `084567a` (NOT under EQ-CORE/).

USER POSTURE (load-bearing)

Non-developer founder. Make confident technical decisions; surface
only product/strategic decisions. Strict OSS only. Architectural
correctness over short-term shortcuts. Cutting-edge 2026 AI-native
patterns. NO sunk-cost preservation.

Context economy matters. The 6 Codex rounds in PR #18 each cost
context; the 5-round-into-stall-into-commit-scope-into-CLEAN dance
shipped fine but used significant budget. Apply lessons learned
(--commit HEAD past ~1500 lines).

All data in the system is test data; no production users yet.

SCOPE OF THIS SESSION — EXPLICIT

In scope: eq-email-pipeline unknown-sender drop fix (brainstorm →
Codex consult → plan → implement → ship).

Optional / time-permitting: production canary + M3.5 outbox drop.

NOT in scope: Phase 2 design, --workers 1 re-evaluation, the M5
tooling once merged (it's done), touching action-item-graph /
eq-structured-graph-core / eq-interaction-threads repos beyond
coordination touchpoints for the email-pipeline fix.

STOP CONDITIONS (hard — surface to user)

- /context-restore returns NO_CHECKPOINTS
- MEMORY.md status isn't PHASE_1.5_M5_SHIPPED_EMAIL_PIPELINE_GAP_NEXT
- The chosen fix approach requires a schema migration AND
  eq-frontend coordination isn't agreed
- Codex review on the email-pipeline PR surfaces a P1 you can't
  fold in one round
- The user's clarifying questions indicate a different scope

The plan is the load-bearing artifact. The user is paying for
thinking + correct execution + careful coordination, not typing.
Take the email-pipeline fix as seriously as M3+M4 — it's
architectural and affects every email from an unknown business
sender.
```

---

## Notes for the user writing this

Key changes vs prior session prompts:

- Status string updated: `PHASE_1.5_M5_SHIPPED_EMAIL_PIPELINE_GAP_NEXT`.
- Primary scope is now the email-pipeline fix, not M5 (which shipped).
- LOCKED-12 added: Codex multi-round reviews switch to `--commit HEAD` past ~1500-line cumulative diff. Hard-won lesson from M5's round 6.
- The 4 candidate fix approaches for the email-pipeline gap are documented in `tasks/downstream/eq-email-pipeline-unknown-sender.md` — the next session reads that doc and decides WITH the user.
