# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — part of a broader AI-native customer intelligence platform.
**Last session:** 2026-05-15 (architecture rethink decision; no code changes).
**Status:** 🛑 **PHASE_1.5_ASYNC_ORCHESTRATION_RETHINK_PENDING** — The Phase 1.5 worker's contract mismatch with eq-agent-action-core surfaced a deeper question: the polling-worker + outbox-publisher architecture is a 2018 pattern, not what a cutting-edge 2026 AI-native startup would build. Rather than patch the contract (the previous session's incorrect Path A recommendation), we're rethinking the async orchestration substrate at the right altitude.
**This session's job:** Run the architecture rethink. **Do NOT write code.** Use `/office-hours` → `/plan-ceo-review` → `/plan-eng-review` → Codex consult → new implementation plan.

---

## CRITICAL — preserve full project context

The Contact Quality Initiative is **multi-phase, multi-repo, and load-bearing for everything downstream in the AI-native customer intelligence platform**. The Phase 1.5 worker is one piece of a project that already shipped Phase 1 and has Phase 2 + Phase 3 in the pipeline. Decisions made for Phase 1.5 must compound to those phases.

**Mandatory first read:**

1. **`docs/superpowers/specs/2026-05-15-initiative-context-snapshot.md`** (~10 min). Standalone entry point for the whole initiative. A new agent should read this and understand the project cold before touching anything else.

After reading the snapshot, read in this order:

2. **This handoff** (~5 min).
3. **`docs/superpowers/specs/2026-05-15-async-orchestration-rethink-brief.md`** (~10 min) — the canonical scope for this session's work. NEUTRAL framing — does not anchor on any option.
4. **`docs/superpowers/research/2026-05-15-durable-execution-landscape.md`** (~10 min) — 2026 landscape of orchestration options. Honest about what AI-native startups are picking.
5. **`tasks/lessons.md`** bottom entries — especially the 2026-05-15 lesson "Probe external service contracts at design time" and the older Codex-spiral discipline lessons.

On-demand / as the work requires:
- `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` Section 6 (durability machinery design — what problem the old architecture was solving).
- `docs/superpowers/specs/2026-05-14-dispatch-patterns-research.md` (earlier dispatch research — useful complement to landscape doc).
- `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` (current implementation plan — needs Phase 1.5 revision after rethink).
- `tasks/downstream/blocker-agent-contract-mismatch.md` (audit-trail only — Path A tactical fix, superseded by this rethink).

---

## What this session does — the decision process

**The rethink is not "pick an option." The rethink is "run a real decision process."** Skip steps and we'll end up where we are now.

### Step 1 — `/office-hours` (product-level)

Interrogate from first principles. The user is a non-developer founder; office-hours surfaces product intent. Specifically:

- What does the user EXPERIENCE when they click "Approve" on a queued domain?
- What's the volume — approvals per day per tenant, bursty or steady?
- Should the approval be undoable?
- Should the agent's research progress stream to the UI?
- What about Phase 2's progressive enrichment — visible or invisible?

Output: a one-page product brief.

### Step 2 — `/plan-ceo-review` (scope challenge)

Challenge whether we're solving the right problem. Is the queue itself the right product surface? Is agent enrichment the right user value? Should the architecture be ambitious or pragmatic? CEO-review surfaces 10-star-product thinking.

### Step 3 — Read the landscape doc with the product brief in hand

Eliminate options that don't fit. The viable short-list is usually 2-3 options, not 7.

### Step 4 — `/plan-eng-review` (engineering tradeoffs)

For the surviving short-list, evaluate engineering axes: operational burden, DX, observability, lock-in, cost, test story, upgrade story. Output: recommended architecture with explicit rationale.

### Step 5 — Codex consult on the architecture decision

Per the recurring quality-gate discipline ("Real /codex review is non-substitutable at every phase boundary"; "Run Codex consult BEFORE writing implementation plans for substantial designs"). This is design-time review, not implementation-time review.

### Step 6 — Write the new implementation plan

`docs/superpowers/plans/2026-05-XX-async-orchestration-revised.md`. Covers revised Phase 1.5 + considerations for Phase 2 + Phase 3.

### Step 7 — Update the design doc

`docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` Section 6 needs revision. Document what we kept, what we changed, why.

### Step 8 — `/context-save`

Checkpoint titled with the chosen architecture. Example: `phase-1.5-rethink-inngest-decided` or `phase-1.5-rethink-sync-in-route-decided`.

**A subsequent session executes the new plan. This session does the DECISION.**

---

## Anti-anchoring instructions (load-bearing)

The previous session recommended Path A (patch the agent contract, keep the polling worker). That was wrong — it preserved a 2018 pattern. The next session must:

- **NOT anchor on what's already shipped.** ~700-900 LoC of code is small relative to picking the right substrate for an 18-month build.
- **NOT anchor on Path A.** The rethink brief does not recommend any option for this reason.
- **NOT shortcut to "obviously Inngest."** Inngest is a strong candidate for AI-native 2026, but the rethink should genuinely evaluate sync-in-route + Temporal + Restate before picking.
- **NOT anchor on existing infrastructure.** "We already use Postgres and EventBridge" is the legacy-pattern trap.

**Right posture:** "If we were a 2026 AI-native startup starting today, knowing Phase 2 + Phase 3 are coming, what would we pick?"

---

## Repository state (as of 2026-05-15 end-of-session)

- **Main HEAD:** `31f513f fix(account-lookup): query account_domains, not accounts.domain` — plus docs-only commits from this session. Working tree clean. All commits pushed to `origin/main`.
- **All shipped code intact** — PR #10, #11, #12, #13 all merged in main. Production E2E still 20/20 PASS for shipped routes. (Caveat — see "Phase 1 silent regression fixed" section below.)
- **Worker process not running in production.** Approved queue entries currently accumulate without being processed; this is by design until the rethink picks a substrate.
- **FastAPI service** serving all ingestion + queue routes correctly. Last Railway deploy: `0ac9010d-7ddd-4d86-af0d-285fcb71e675` SUCCESS — picked up the account_lookup fix.
- **Neon eq-dev schema** all Phase 1.5 columns + tables present.
- **Production E2E** at `/tmp/e2e_phase_1_production.py` (486 lines, 20/20 PASS). Will need updates after rethink picks substrate, AND should be extended with per-attendee-branching happy-path cases per `tasks/downstream/test-discipline-gaps-2026-05-15.md` Item 2.

### Phase 1 silent regression fixed 2026-05-15 — important context for the rethink

A bug in `services/account_lookup.py` (introduced 2026-05-14 in PR #10 merge) made calendar-event matching and contact resolution silently fail for every transcript with BUSINESS-domain attendees. The bug was undetected by 6 layers of quality gates (Codex review, unit tests, integration tests, self-review, production E2E reporting 20/20 PASS) and was traced by a downstream agent (eq-synthetic-date-generation). Fixed at commit `31f513f` 2026-05-15.

**Why this matters for the rethink session:**

1. **The rethink starts on a verified-working Phase 1 layer.** Before the fix, the rethink was proceeding on top of a foundation we hadn't verified end-to-end. Now we have.

2. **The four systemic quality gaps that let this bug ship apply equally to whatever architecture the rethink picks.** The new architecture must:
   - Probe live schema at design time (don't assume table/column names).
   - Avoid mock-at-import-level for in-service functions without real-substrate coverage.
   - Exercise every fan-out branch in production E2E, not just auth/validation boundaries.
   - NOT bake in broad try/except blocks that silently degrade on bugs. If the new framework provides retry/error-handling primitives, those REPLACE our excepts — not add to them.

3. **`tasks/downstream/test-discipline-gaps-2026-05-15.md`** has four concrete follow-up actions that should fold into the new implementation plan (or be done as standalone work before the rethink-execution session, if the user wants confidence restored sooner).

4. **`tasks/lessons.md`** bottom entry "Four systemic quality gaps that let a silent regression ship Phase 1" has the full breakdown of how the bug shipped through six quality gates. Read it before the rethink — it informs which architecture properties matter more than they might appear from the landscape doc alone (observability, real-substrate testability, explicit error propagation).

## Production credentials + IDs

Locked in across the initiative (also in `2026-05-15-initiative-context-snapshot.md` Section 10):

- Neon: project `super-glitter-11265514`. Test tenant `11111111-1111-4111-8111-111111111111` (column is `tenants.id`).
- Railway FastAPI: project `847cfa5a-b77c-4fb0-95e4-b20e8773c23e`, service `59a69f3d-9a24-4041-942a-891c4a81c5fb`, env `e4c5ec15-1931-4632-9e58-92d9c6be4261`, URL `https://live-transcription-fastapi-production.up.railway.app`.
- Railway eq-agent-action-core: project `421e079f-2e46-4c22-83c4-0fe6208e6aff`, service `3036ea0f-afc9-4bc4-889d-c98617d81e96`, env `f2c0a13f-40c6-4514-9c02-acac2a22c05c`, URL `https://eq-agent-action-core-production.up.railway.app`.
- Internal JWT: HS256, secret shared (`INTERNAL_JWT_SECRET`), `iss=eq-frontend`, `aud=eq-backend`, claims `tenant_id` (UUID), `user_id`, optional `pg_user_id`.

---

## The user

A non-developer founder. Make confident technical decisions; surface only product / strategic decisions for the user to weigh in on. Work without stopping for clarifying questions; make the reasonable call and continue; the user redirects if needed.

**The user explicitly cares about:** what a cutting-edge 2026 AI-native startup would actually build. Architectural correctness over short-term shortcuts. Maintaining full project context across sessions so any new agent can pick up where the prior left off.

**The user does NOT care about:** preserving sunk-cost code, hitting an arbitrary deadline over correctness, or maintaining patterns that don't represent 2026 best practice.

---

## What's NOT in scope this session

- **Writing code.** None. Zero. The rethink is a decision session.
- **Migrating data.** Schema stays.
- **Executing on the chosen architecture.** Separate session.
- **Phase 2 / Phase 3 detailed design.** Pick a substrate that COMPOUNDS into them; don't design them.
- **Cross-repo coordination work.** Identify what needs coordination; don't execute it.

---

## Suggested first actions

1. Run `/context-restore`. Expect a checkpoint titled "phase-1.5-async-orchestration-rethink-pending" dated 2026-05-15.
2. Read `2026-05-15-initiative-context-snapshot.md` first (mandatory).
3. Read this handoff.
4. Read `2026-05-15-async-orchestration-rethink-brief.md`.
5. Read `2026-05-15-durable-execution-landscape.md`.
6. Run `/office-hours` to interrogate the product-level questions in the rethink brief Section 7.
7. Run `/plan-ceo-review`.
8. Run `/plan-eng-review` on the short-list.
9. Run Codex consult on the recommended architecture.
10. Write the new implementation plan.
11. Update the design doc.
12. Save checkpoint. End session.

A subsequent session executes the chosen plan.

---

## Final note for the next agent

The user is paying for thinking, not typing. The value of this session is in the decision quality, not the doc volume. Take time to actually run the workflow skills. Don't shortcut.

The reason this rethink exists: the previous session shortcut to "obviously keep the worker." Don't make the same mistake at a different level (e.g., "obviously Inngest").

When you reach a decision, document the alternatives considered and why they lost. Future sessions need to know not just what we picked but what we explicitly rejected, so they don't re-litigate.
