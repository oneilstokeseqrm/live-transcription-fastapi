# Next Session Kickoff — Granola Phase 3: FRONTEND (brainstorm + plan, not build)

**Written:** 2026-05-26, immediately after the Granola **contact-resolution/linking** work shipped (PR #36 `e0aafbe`) and was **E2E-verified in production**. The Granola **backend ingestion engine is now functionally COMPLETE** (connect → 5-min auto-poll → ingest → enrich → link company + people → downstream graph, all proven on a real meeting). The remaining big chunk is **Phase 3 — the frontend**, which turns this from a founder-operated/manual tool into a **self-serve** capability (a design partner connects their own Granola account and approves unknown companies with zero engineer involvement).

**THIS session is a PLANNING session, not a build session:** brainstorm the product/scope, design the UI, lock the architecture, and produce a build-ready plan — leveraging the gstack planning skills. Build happens in a *later* session.

**Paste the block below as the opening message of the next session.**

---

```
You're picking up a multi-session project: the Granola.ai → EQ transcript ingestion
integration. The BACKEND is functionally complete and live in production; the Granola
contact-resolution/linking work just shipped (PR #36 `e0aafbe`) and was E2E-verified.
This session starts PHASE 3 — the FRONTEND — and it is a PLANNING/BRAINSTORM session,
NOT a build session. Goal: design + plan the two user-facing surfaces (a Granola
"Connect" settings page and a source-agnostic "Pending Approvals" inbox) so a design
partner can self-serve onboard, then produce a build-ready plan. Build is a later
session.

CONTINUITY IS CRITICAL. This is a multi-session project. Read everything before you
touch anything. Trust but verify each artifact; if anything is missing or doesn't
match, STOP and tell me.

═══════════════════════════════════════════════════════════════════════
STEP 1 — RUN /context-restore FIRST
═══════════════════════════════════════════════════════════════════════
Run /context-restore. It must load the checkpoint titled
"granola-contact-resolution-shipped-e2e-passed" (file prefix 20260526-121638). If it
loads NO_CHECKPOINTS or a different title, STOP and tell me (or fall back to this repo
doc: docs/superpowers/specs/2026-05-27-granola-phase-3-frontend-kickoff.md).

═══════════════════════════════════════════════════════════════════════
STEP 2 — READ THESE (top to bottom; complete ALL before acting)
═══════════════════════════════════════════════════════════════════════
Project state + trajectory:
1. memory/MEMORY.md + memory/project_granola_integration.md — the FULL Granola arc;
   current state = CONTACT-RESOLUTION SHIPPED + PROD E2E PASSED, credential CONNECTED,
   trigger LIVE.
2. memory/project_granola_contact_resolution_gap.md — RESOLVED; what the just-shipped
   work did + the pre-existing non-idempotency it surfaced (§2.1 #16).

The Phase 3 spec + backlog (in tasks/granola-integration-plan.md):
3. §Phase 3 (3a Granola Connect settings page; 3b Pending Approvals component) — the
   component/route/endpoint OUTLINE + UX decisions (LOCKED-30/31/34). NOTE: this is a
   spec, NOT a build-ready plan — that's what THIS session produces.
4. §Post-implementation follow-ups (Phase 2.1+), items #1-18. Three are UI-adjacent and
   are PLANNING INPUTS for this session (decide them here, don't pre-fix):
   - #13 /connect bad-folder recovery + PATCH /folder endpoint (does v1 include
     "change folder", or just disconnect+reconnect?)
   - #3 cross-user queue visibility (is the Pending Approvals inbox per-user
     [first-owner-wins, current] or tenant-admin-wide? Shapes the UI + a backend change)
   - #9 event-driven deferred-note re-process (approve → immediate re-ingest vs wait
     ≤5 min for the next poll — a UX-latency call)
   The rest of §2.1 (#1,2,4,5,6,7,8,10,11,14,15,16,17,18) is backend backlog — reference
   only; do NOT fix in this frontend session. (#16 = the non-idempotency found 2026-05-26;
   #4 re-open lifecycle is the realest reachable backend bug, best fixed WITH the 3b UI.)

Data + contracts the UI surfaces:
5. docs/contacts-architecture.md — how contacts/accounts/the pending-approvals queue
   (pending_account_mappings + signals) work; the data the UI reads/writes.
6. docs/frontend-ingestion-integration-plan.md — the GENERAL frontend auth/ingestion-
   contract plan (JWT middleware, endpoint contracts). Background, not the Granola UI.
7. The backend endpoints the UI consumes (skim the signatures):
   - routers/granola.py: POST /validate, POST /connect, POST /rotate, GET /status,
     DELETE (disconnect). All JWT-authed. (PATCH /folder does NOT exist yet — §2.1 #13.)
   - routers/queue_actions.py: POST /queue/{id}/approve, /map, /ignore (already wired).

The frontend repo (THE main investigation target this session):
8. /Users/peteroneil/eq-frontend — INVESTIGATE its conventions before designing: Next.js
   app-router structure, the design system / any DESIGN.md, existing settings pages +
   integration pages, the API-client pattern, auth (how it mints the JWT the backend
   expects), and how it currently talks to live-transcription-fastapi. DON'T assume the
   backend repo's patterns — investigate eq-frontend's. (Prisma schema is owned here:
   /Users/peteroneil/eq-frontend/prisma/schema.prisma — see memory/reference_prisma_schema_ownership.)

Disciplines (memory):
9. feedback_branch_safety, feedback_tenant_isolation, feedback_codex_pre_merge_gate,
   feedback_shared_infrastructure_collision, feedback_complete_all_handoff_reads_before_action,
   reference_railway_project_ids, reference_prisma_schema_ownership.

═══════════════════════════════════════════════════════════════════════
STEP 3 — VERIFY STATE
═══════════════════════════════════════════════════════════════════════
  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi
  git branch --show-current        # expect: main
  git log --oneline -4             # tip includes e0aafbe (#36 contact resolution)
  curl -s https://live-transcription-fastapi-production.up.railway.app/health  # {"status":"ok"}
  cd /Users/peteroneil/eq-frontend && git branch --show-current && git log --oneline -3
  # (eq-frontend was UNTOUCHED this session; confirm its tip + that it's clean)
If main lacks e0aafbe or /health is non-200, STOP.

═══════════════════════════════════════════════════════════════════════
STEP 4 — THE WORK (Phase 3 frontend — PLAN, don't build)
═══════════════════════════════════════════════════════════════════════
GOAL: design + plan two user-facing surfaces so a design partner self-serve onboards
Granola with NO engineer in the loop:
  (A) Granola Connect settings page (/dashboard/settings/integrations/granola): two-step
      wizard (paste key → pick folder) + connected status panel (rotate/disconnect/
      reconnect, 7-day activity, revoked/error banners). LOCKED-30/31/34.
  (B) EQ-native Pending Approvals inbox (source-agnostic — serves email + Granola +
      future Fireflies/Otter): list pending companies/domains, approve/ignore, multi-
      source attribution. Decoupled from Granola (a general inbox).

Use the gstack PLANNING flow (this is design-heavy + user-facing):
  1. /office-hours OR /plan-ceo-review — frame product/scope: who is the user, what's
     the narrowest self-serve wedge, what's v1 vs later. (Surface the three UI-adjacent
     decisions: folder-change, queue visibility, instant re-process.)
  2. /design-consultation or /design-shotgun + /plan-design-review — the UI: it's the
     first thing a customer touches, so explore variants + lock the visual design. Check
     for an existing eq-frontend DESIGN.md / design system first.
  3. /plan-eng-review — architecture: eq-frontend integration, API-client design, auth/
     JWT, what data the UI reads, any Prisma/schema needs, tests (RTL + Playwright/MSW).
  4. writing-plans — produce the build-ready implementation plan for eq-frontend.
  5. /codex consult on the plan. Build (with /codex review gate) is a LATER session.

DO NOT build this session. The deliverable is: a reviewed, build-ready Phase 3 plan +
the design + the resolved UI-adjacent decisions.

═══════════════════════════════════════════════════════════════════════
FULL PROJECT TRAJECTORY (multi-session arc — internalize this)
═══════════════════════════════════════════════════════════════════════
VISION: Connect a Granola.ai account once; thereafter every business meeting auto-flows
into EQ — ingested, enriched (summaries + insights), linked to the right company AND the
right people, and fed into the Neo4j relationship graph — self-serve, no engineer.

  Phase 1   AWS KMS infra (credential encryption) .......................... ✅ shipped
  Phase 2a  Vault schema (3 Neon tables, eq-frontend Prisma) ............... ✅ shipped
  Phase 2b  Vault module (encrypt/decrypt, audited accessors) ............. ✅ shipped
  Phase 2c  Granola API client ........................................... ✅ shipped
  Phase 2d  Adapter + Path 2 (Scenario A known-anchor / C defer / D skip) . ✅ shipped
  Phase 2e  Scheduler (DBOS workflow + cron endpoint) .................... ✅ shipped
  Phase 2f  Admin endpoints (validate/connect/rotate/status/disconnect) .. ✅ shipped
  Trigger   5-min EventBridge Rule → cron-tick (LIVE, auto-polling) ....... ✅ shipped+LIVE
  E2E #1    First real /connect ingest + 3 P0 parse fixes (#32-#34) ...... ✅ shipped+verified
  CONTACTS  Resolve/create/link contacts (Postgres FK chain + Neo4j edge)  ✅ shipped+E2E (PR #36)
  Phase 2g  Transactional "your connection broke" email (LOCKED-32) ...... ⬜ deferred
  Phase 3   FRONTEND — Connect page + Pending Approvals UI ............... ⬜ ◄── THIS SESSION (plan)
  Phase 4   Production E2E sign-off ..................................... ✅ effectively done via real E2Es
  Backlog   §2.1 #1-18 backend polish/hardening (mostly low-priority) .... ⬜ triggered/deferred

What "complete" means after Phase 3: a design partner connects their own Granola account
through the UI and approves unknown companies from the inbox — no manual JWT-mint, no
engineer. That is the self-serve unlock.

═══════════════════════════════════════════════════════════════════════
DISCIPLINES + POSTURE
═══════════════════════════════════════════════════════════════════════
- USER = non-developer founder. Plain-English always. Make confident technical calls;
  surface product/strategic decisions, scope changes, risky/destructive ops. Give the
  HONEST tradeoff. Verify claims against the repo/code — do NOT assert from memory.
- THIS IS A PLANNING SESSION: brainstorm → design → review → build-ready plan. Do NOT
  write production frontend code this session. Use the gstack skills (Step 4).
- The frontend is a DIFFERENT repo (eq-frontend; Next.js, deployed on Vercel at
  eq-frontend-two.vercel.app) with its OWN conventions — investigate them; don't port
  the backend repo's patterns blindly.
- Prisma schema owned by eq-frontend (/Users/peteroneil/eq-frontend/prisma/schema.prisma).
  Any new table/column goes through its migration pipeline (see reference_prisma_schema_ownership).
- Branch safety: `git branch --show-current` before every commit; eq-frontend is a SHARED
  checkout — beware concurrent agents (an eq-frontend conductor workspace was active
  2026-05-26). Per-action founder auth for: push-to-main, merge, Vercel/Railway/AWS
  changes, GitHub-secret changes. Feature branch + PR + branch push are fine.
- For any UI testing use the gstack /browse skill; E2E login creds are in
  /Users/peteroneil/eq-frontend/.env.e2e.local (E2E_BASE_URL, E2E_EMAIL_USER2,
  E2E_PASSWORD_USER2) per the global CLAUDE.md. NEVER use mcp__claude-in-chrome__*.
- Codex: /codex consult on the plan before building; /codex review on the eventual diff.
- NEVER break the existing backend contracts; the UI consumes the endpoints listed in
  STEP 2 #7. Tenant isolation everywhere.

═══════════════════════════════════════════════════════════════════════
KEY STATE (verified 2026-05-26 end-of-session)
═══════════════════════════════════════════════════════════════════════
live-transcription-fastapi main: `e0aafbe` (+ a docs commit for this kickoff + §2.1).
  Railway prod deploy `eca82628` SUCCESS; /health 200. Contact-resolution LIVE.
eq-frontend main: ~`7905222` (last known; UNTOUCHED this session — verify the tip).
Backend endpoints for the UI (live-transcription-fastapi, all JWT-authed; prod has
  ALLOW_LEGACY_HEADER_AUTH=true): routers/granola.py {/validate,/connect,/rotate,/status,
  DELETE}; routers/queue_actions.py {/queue/{id}/approve,/map,/ignore}. PATCH /folder
  NOT built (§2.1 #13 — a v1 decision).
CONNECTED test credential (KEPT connected, founder choice): vault.user_credentials id
  `6a727bae-5140-4f9e-a65e-4ea8d0523f7d`, tenant `11111111-1111-4111-8111-111111111111`,
  user `061ae392-47d5-4f04-9ea8-afa241f23555` (stokeseqrm@gmail.com), provider granola,
  folder `fol_sBJi17PeBXpHN7` ("Test EQ"). Trigger LIVE (Rule granola-poll-5min, acct
  211125681610 us-east-1). GRANOLA_KEY in repo .env (do NOT print/commit).
VERIFIED test data (from the contact-resolution E2E — LOCKED-11 cleanup PENDING): contact
  `c86d60de` (matt.scanlan@palantir.example.com) bound to Palantir acct
  `0e49a47e-0200-5e4f-962c-2b3df57e0624`; interaction `bca60296-cfa3-4886-885c-02b8c8284735`
  has full FK chain + Neo4j Contact node + [:ATTENDED] edge. (Note `not_ZxkJDxRRKZNPSE`,
  run row `307ce8d9-025c-4fdf-8b1a-c405e30c60a3`.)
Neon prod: project `super-glitter-11265514`, branch `br-holy-block-ads5069w`, db `neondb`.
Railway live-transcription IDs: project `847cfa5a-b77c-4fb0-95e4-b20e8773c23e`, env
  `e4c5ec15-1931-4632-9e58-92d9c6be4261`, service `59a69f3d-9a24-4041-942a-891c4a81c5fb`.
Test-tenant synthetic known-account domains: anthropic/linear/palantir/snowflake
  .example.com.

═══════════════════════════════════════════════════════════════════════
KNOWN ISSUES / CARRYOVER
═══════════════════════════════════════════════════════════════════════
- Backend backlog §2.1 #1-18 — reference only; do NOT fix in this frontend planning
  session (except DECIDING the three UI-adjacent items #3/#9/#13, and optionally folding
  #4 re-open-lifecycle into the 3b build later).
- LOCKED-11 cleanup of the verified test data + credential is PENDING (founder chose to
  keep it connected for testing/frontend review — leave it).
- Pre-existing test failures UNRELATED to Granola: 1 unit
  (test_upsert_summary_uses_unique_interaction_id_index), 16 integration
  (test_queue_lifecycle). Do NOT fix.
- Phase 2g (breakage email) is independent and deferred; not part of Phase 3.
```

---

## Why Phase 3 is the right next chunk

The backend engine is done and proven, but today *connecting* a Granola account is a manual backend step (mint a JWT, hit the endpoints) and there's no UI to approve unknown companies. Phase 3 is the **self-serve unlock**: it's the difference between "the founder operates this by hand" and "a design partner onboards themselves." It's also the first surface a customer touches, so it deserves real product + design thinking — hence a dedicated brainstorm/plan/design/review session before any code. The backend backlog (§2.1) is mostly latent edges on a low-volume, founder-operated system; it does not block Phase 3 and is best addressed on-trigger or alongside the UI work it touches (the re-open-lifecycle bug #4 lives in the same approvals queue Phase 3b surfaces).
