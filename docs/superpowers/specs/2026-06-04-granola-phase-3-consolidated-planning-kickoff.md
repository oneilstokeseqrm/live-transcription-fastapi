# Next Session Kickoff — Granola Phase 3: CONSOLIDATED PLANNING (frontend + backend)

> ✅ **THIS PLANNING SESSION IS COMPLETE (2026-06-04 late).** The consolidated build-ready plan is written
> and reviewed (Codex 4-round clean). **GO HERE NEXT:**
> - **BUILD-session kickoff (paste this into the next session):**
>   `docs/superpowers/specs/2026-06-05-granola-phase-3-BUILD-session-kickoff.md`
> - **Build-ready plan (SOURCE OF TRUTH for the build):** `docs/superpowers/plans/2026-06-04-granola-phase-3-fe-be.md`
>   (C1–C18 corrections are folded INTO the phase steps; §1a is the changelog).
> - **Parallel-intake assessment (EQ-93, fast-follow):** `tasks/granola-parallel-intake-investigation.md`.
> - **What changed from this kickoff:** ADDED an import-scope choice (history vs forward, D6); DE-SCOPED the
>   frontend approvals inbox (now a SEPARATE future project — backend already routes to the shared per-user
>   queue); the approval queue STAYS one shared, source-tagged, per-user table (not separated).
> - **Next = BUILD (backend-first, each PR + Codex gate):** EQ-91 → EQ-92 → EQ-94 → EQ-93. EQ-90 = Done.
> The rest of this doc is the historical kickoff that launched the planning session.

**Written:** 2026-06-04, at the end of a Phase 3 brainstorm/decision session. The project SHIFTED this
session from "frontend only" to a **coordinated frontend + backend capability**. Every framing/product
decision is now locked; the next session writes the build-ready plan across BOTH repos and runs it
through the gstack reviews + Codex. **Still PLANNING, not building.**

**Paste the block below as the opening message of the next session.**

---

```
You're picking up a multi-session project: the Granola.ai → EQ transcript-ingestion integration.
This is a PLANNING session (brainstorm/decide is DONE; build is a LATER session). The job:
produce a reviewed, build-ready plan that spans the BACKEND (live-transcription-fastapi) and the
FRONTEND (eq-frontend), then run it through the gstack planning reviews + /codex consult.

CONTINUITY IS CRITICAL (multi-session). Read everything before you touch anything. Trust but verify
each artifact against the repo; if anything is missing or doesn't match, STOP and tell me.

═══ STEP 1 — RUN /context-restore FIRST ═══
Load the checkpoint titled "granola-phase-3-decisions-fe-plus-be-shape" (2026-06-04). If it loads a
different title or NO_CHECKPOINTS, STOP and tell me (fallback: this doc).

═══ STEP 2 — READ THESE (top to bottom, ALL before acting) ═══
THIS doc (the driver). Then the mandatory reads:
  1. memory/MEMORY.md + memory/project_granola_integration.md — full Granola arc; current state.
  2. tasks/granola-existing-system-map.md — WHAT EXISTS today (both repos, file pointers). Read fully.
  3. tasks/granola-multi-folder-investigation.md — multi-folder feasibility + the LOCKED data model.
  4. tasks/granola-integration-plan.md — original plan + §Phase 3 + §2.1 backlog (esp. #3/#9/#13/#16).
  5. docs/contacts-architecture.md — the contacts/accounts/approval-queue data the UI surfaces +
     the Lane 1/Lane 2 + downstream (async/queued/parallel) architecture.
  6. The backend endpoints the UI consumes: routers/granola.py + routers/queue_actions.py.
  7. /Users/peteroneil/EQ-CORE/eq-frontend — RE-VERIFY its state (shared checkout; branch hops). The
     onboarding map is in tasks/granola-existing-system-map.md §B; confirm it still matches.
  8. feedback memories: branch_safety, tenant_isolation, codex_pre_merge_gate,
     shared_infrastructure_collision, complete_all_handoff_reads_before_action,
     cutting_edge_ai_native_differentiator (the guiding principle), linear_tracking_and_handoff_audit;
     reference: prisma_schema_ownership, railway_project_ids, granola_api_shape.

═══ STEP 3 — VERIFY STATE ═══
  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi && git log --oneline -3   # e0aafbe present
  curl -s https://live-transcription-fastapi-production.up.railway.app/health        # {"status":"ok"}
  cd /Users/peteroneil/EQ-CORE/eq-frontend && git branch --show-current && git status --short
If main lacks e0aafbe or /health is non-200, STOP.

═══ STEP 4 — THE WORK (consolidated Phase 3 planning) ═══
A. SCOPED BACKEND INVESTIGATION first (the one open technical question): parallelizing the SEQUENTIAL
   Granola intake loop. Assess whether bounded-concurrency intake is worth implementing, careful about
   Granola's ~5 req/s rate limit, LLM rate/cost limits, downstream queue pressure, and the per-note
   idempotency anchor. Research June-2026 cutting-edge patterns for parallelizing this kind of
   ingest/backfill. Output: a recommendation (v1 vs fast-follow) + a design sketch. (Per founder: this
   is at minimum an INVESTIGATION/ASSESSMENT; likely a fast-follow to initial implementation.)
B. WRITE THE BUILD-READY PLAN spanning both repos (use the writing-plans skill). Two coordinated
   streams (backend first — the frontend depends on its contracts):
   • Backend (live-transcription-fastapi): folder-LIST data model + multi-folder poll loop; BACKGROUND
     history-import to replace the synchronous first poll (amends LOCKED-31) with a UI-visible
     "importing N of M" progress signal; the "ingest everything" empty-folder_id fix; array-shaped
     /connect + /status contracts; per-folder error state; (fast-follow) parallel intake.
   • Frontend (eq-frontend): the design-partner-GATED Granola card in the onboarding meeting-connect
     step AND Settings→Connections; key-paste → MULTI-folder picker wizard; connected status panel
     (folder list + import progress + disconnect); the per-user Pending Approvals inbox; tRPC/proxy
     procedures over the gateway-JWT rail.
C. RUN THE REVIEWS: /plan-eng-review (architecture, both repos) + /plan-design-review (the UI) +
   /codex consult on the plan. Also do PUBLIC RESEARCH on what a cutting-edge AI-native startup would
   do as of June 2026 (per the guiding principle) — esp. for the connect UX + parallel intake.
D. SET UP / UPDATE LINEAR for the trajectory (see "Linear & continuity" below) and AUDIT all handoff
   docs at session end (the per-session handoff-audit discipline).

DO NOT build this session. Deliverable: a reviewed, build-ready Phase 3 plan (FE+BE) + the parallel-
intake assessment + updated Linear + a clean handoff.
```

---

## LOCKED DECISIONS (this session 2026-06-04) — do NOT re-litigate

1. **Placement: design-partner-GATED Granola card in the existing onboarding "meeting-connect" step
   + Settings→Connections management.** Shown ONLY to design-partner orgs (per-org flag/allowlist;
   for a few partners a tenant allowlist suffices). **Supersedes LOCKED-30** (the standalone
   `/dashboard/settings/integrations/granola` page).
2. **Connect rail = the existing gateway-JWT proxy** (`lib/gateway-forward` + `mintInternalJwt` with
   `pg_user_id` → `BACKEND_SERVICE_TRANSCRIPTION_URL`), NOT the OAuth connect-token pattern. Granola is
   API-key paste + folder pick (no OAuth API).
3. **Pending Approvals queue = per-user** (first-owner-wins, matches the whole system; zero backend
   change). Tenant-admin-wide is a deferred scale feature (§2.1 #3).
4. **On approval, re-process deferred notes on the next 5-min poll** (not instant). Instant is a
   documented fast-follow (§2.1 #9).
5. **Full multi-folder in v1.** Data model stores a **LIST** of folders; `/connect` + `/status` are
   **array-shaped**; backend loops over folders. (See `tasks/granola-multi-folder-investigation.md`.)
6. **Background history-import replaces the synchronous first poll** (amends **LOCKED-31**). Real
   design partners have existing history → a synchronous backfill would hit Railway's ~5-min cap.
   Connect = fast acknowledge + background import with a UI-visible progress state. **In v1 scope.**

---

## NEW PROJECT SHAPE (the shift this session)

**Before:** Phase 3 = two frontend screens (~2 days).
**Now:** Phase 3 = a coordinated **backend + frontend** capability, multi-session:

- **Backend stream** (live-transcription-fastapi): multi-folder data model + poll loop; background
  history-import + progress signal; "ingest everything" fix; array contracts; per-folder error state;
  **fast-follow: parallelize the sequential intake**.
- **Frontend stream** (eq-frontend): gated Granola card (onboarding + settings); key-paste → multi-
  folder wizard; status panel w/ import progress; per-user Pending Approvals inbox; tRPC/proxy routes.

Build order: **backend first** (frontend depends on its array contracts + progress signal). Each build
session ends with a `/codex review` gate before merge.

---

## GUIDING PRINCIPLE (founder, capture forever)

EQ is a **next-gen, cutting-edge customer-intelligence tool / next-gen CRM that replaces legacy
tools.** Adopting emerging trends / thought-leadership / cutting-edge architecture **as of June 2026**
is a deliberate differentiator. **But it must be stable in production.** During planning, do PUBLIC
research on what a cutting-edge AI-native startup would do (especially for the connect UX and for
parallelizing intake). This does NOT change the Granola ingestion shape already decided (backend
multi-folder + the frontend we scoped) — it informs HOW we build, especially the parallel-intake
design. (Memory: `cutting_edge_ai_native_differentiator`.)

---

## LINEAR & CONTINUITY DISCIPLINE (founder, standing)

- This is a **multi-session project**; trajectory tracking is critical. Maintain BOTH (a) the doc set
  in-repo (this kickoff + the task docs + memory) AND (b) **Linear** (via the Linear MCP) — a centralized
  project with connected issues mirroring the workstreams. The founder sometimes runs a broader Linear
  project with many issues; keep the in-repo docs and Linear mutually consistent.
- **Every session: AUDIT all handoff documentation** at session end to ensure correct cross-session
  handoff (docs + Linear + memory mutually consistent; dates/pointers/SHAs current) — analogous to the
  EQ-CORE environment-split HANDOFF-PROTOCOL, applied to this project. (Memory:
  `linear_tracking_and_handoff_audit`.)
- Linear state as of this session: see the checkpoint / the Linear project created 2026-06-04
  (verify in-app). Granular build tasks get created during the build-plan phase, not before.

---

## KEY STATE (verified 2026-06-04)

- **Backend** `live-transcription-fastapi` main `e0aafbe` (+ `fafaee2` docs). Railway prod `eca82628`
  SUCCESS; `/health` 200. Prod `ALLOW_LEGACY_HEADER_AUTH=true`. The Granola engine is COMPLETE + LIVE.
- **Frontend** `eq-frontend`: SHARED checkout, branch-hops (~18 worktrees; seen on
  `docs/chat-modernization-handoff` @ `1e55dabb`). Granola = **greenfield on FE**. Prisma owned here.
- **CONNECTED test credential KEPT** (founder choice): vault id `6a727bae-…`, tenant `11111111-…`,
  user `061ae392-…` (stokeseqrm@gmail.com), folder `fol_sBJi17PeBXpHN7` ("Test EQ"). Trigger LIVE
  (Rule `granola-poll-5min`, acct 211125681610). LOCKED-11 cleanup of verified test data still PENDING
  (founder choice — leave it).
- Neon prod `super-glitter-11265514` / `br-holy-block-ads5069w`. Railway live-transcription:
  project `847cfa5a-…`, env `e4c5ec15-…`, service `59a69f3d-…`.

---

## DISCIPLINES / POSTURE

- USER = non-developer founder. Plain English. Make confident technical calls; surface product/
  strategic decisions + the honest tradeoff. Verify against the repo — don't assert from memory.
- PLANNING session — no production code. Use the gstack planning skills + /codex consult.
- Per-action founder auth for: push-to-main, merge, Vercel/Railway/AWS/GitHub-secret changes. Feature
  branch + PR + branch push are fine. eq-frontend is a SHARED checkout — `git branch --show-current`
  before EVERY commit.
- NEVER modify downstream envelope contracts (LOCKED-38). Tenant isolation everywhere. Prisma owned by
  eq-frontend. Codex pre-merge gate (4-round soft cap) on every build PR.
- Use the gstack /browse skill for UI testing (E2E creds in eq-frontend/.env.e2e.local).

---

## OPEN INVESTIGATIONS / BUILD-TIME PROBES (carry forward)

- **Parallel intake** (Step 4A) — the scoped assessment; likely a fast-follow.
- **Granola API build-time probes** (from the multi-folder doc §6): empty `folder_id` behavior;
  multi-`folder_id` in one call?; `folder_membership` on summaries; `page_size` vs `limit`; realistic
  folder counts.
- **JWT compat** — confirm `routers/granola.py` accepts the frontend's `mintInternalJwt` (audience +
  `pg_user_id`) as-is.
- **Backend backlog §2.1** (#16 `_persist_intelligence` non-idempotency, #4 re-open lifecycle, etc.) —
  reference only; address on-trigger, not in this planning session.
