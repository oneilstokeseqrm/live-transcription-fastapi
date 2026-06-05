# Build-Session Kickoff — Granola Phase 3 (frontend + backend)

**Written:** 2026-06-05, at the end of the Phase-3 CONSOLIDATED PLANNING session. Planning + review are
DONE. The plan is build-ready and verified clean by Codex across 4 rounds. **This is the handoff for the
BUILD — a new session. The build, not more planning.**

**Paste the block below as the opening message of the next (build) session.**

---

```
You're picking up a multi-session project: the Granola.ai → EQ meeting-ingestion integration. PLANNING
and REVIEW are DONE; this is a BUILD session. There is a reviewed, build-ready plan. Build it phase by
phase, backend-first, each phase its own PR with a Codex pre-merge gate. I'm a non-developer founder —
plain English, make confident technical calls, surface product/strategic decisions + the honest tradeoff.

CONTINUITY IS CRITICAL (multi-session). Read everything before you touch anything. Trust but verify each
artifact against the repo; if anything is missing or doesn't match, STOP and tell me.

═══ STEP 1 — RUN /context-restore FIRST ═══
It must load the checkpoint titled "granola-phase-3-plan-reviewed-build-ready" (file prefix
20260604-161018). If it loads NO_CHECKPOINTS or a different title, STOP and tell me (fallback: this doc).

═══ STEP 2 — READ THESE (top to bottom, ALL before acting) ═══
THE build-ready plan = SOURCE OF TRUTH (read it FULLY, including §1a "Codex review corrections" and the
inline steps that absorbed them):
  docs/superpowers/plans/2026-06-04-granola-phase-3-fe-be.md
Then the mandatory reads:
  1. memory/MEMORY.md + memory/project_granola_integration.md (full Granola arc + current state).
  2. tasks/granola-existing-system-map.md (WHAT EXISTS today, both repos, file pointers). Read fully.
  3. tasks/granola-multi-folder-investigation.md (multi-folder data model + build-time API probes §6).
  4. tasks/granola-parallel-intake-investigation.md (EQ-93 fast-follow design + its 6 prerequisites —
     ONLY relevant when you reach B4; do not build it in the v1 sequence).
  5. The backend endpoints you'll modify: routers/granola.py + services/granola_ingestion/{adapter,
     scheduler,api_client}.py + services/text_clean_service.py + services/asyncpg_pool.py.
  6. /Users/peteroneil/EQ-CORE/eq-frontend — RE-VERIFY its state (SHARED checkout that branch-hops; it
     was on `main` @ 202a691f at plan time). The FE map is in tasks/granola-existing-system-map.md §B.
  7. feedback memories: branch_safety, tenant_isolation, codex_pre_merge_gate,
     shared_infrastructure_collision, complete_all_handoff_reads_before_action,
     verify_existing_behavior_before_scoping, cutting_edge_ai_native_differentiator,
     linear_tracking_and_handoff_audit; reference: prisma_schema_ownership, railway_project_ids,
     granola_api_shape.

═══ STEP 3 — VERIFY STATE ═══
  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi && git log --oneline -3   # e0aafbe present
  curl -s https://live-transcription-fastapi-production.up.railway.app/health        # {"status":"ok"}
  cd /Users/peteroneil/EQ-CORE/eq-frontend && git branch --show-current && git status --short
If main lacks e0aafbe or /health is non-200, STOP.

═══ STEP 4 — THE BUILD (backend-first; each phase = 1 PR + Codex pre-merge gate; founder authorizes each merge) ═══
Build order (deploy + merge order is in plan §5):
  • EQ-91 — Backend Phase B1 (folder-LIST model + array `/connect`/`/status`, keeps first_poll) then
    Phase B2 (multi-folder poll loop + empty-folder_id fix + per-folder status in config + folder-name
    derivation). Apply the inline §1a corrections (already folded into the steps): C5, C6, C10, C13, C15, C16.
  • eq-frontend Prisma migration for `granola_import_runs` (plan B3 Step 1) — deploys BEFORE the B3 PR.
  • EQ-92 — Backend Phase B3 (background history-import + progress signal). The corrections that matter:
    C1 derived progress, C2 lock-busy, C3 dedicated GRANOLA_IMPORT_QUEUE, C4 forward-anchor at route
    entry, C7 table schema (credential_id + partial-unique), C8 enqueue atomicity, C9 cancelled state,
    C17 forward add-folder watermark. (All already inline in B3's steps.)
  • EQ-94 — Frontend Phases F1 (tRPC procedures + the design-partner gate IN every procedure, C11/C12) →
    F2 (gated card, onboarding + settings) → F3 (paste-key → user multi-folder pick → import-scope
    wizard) → F4 (status panel + import progress). Start only after the backend contracts each consumes
    are DEPLOYED + /health 200.
  • EQ-93 — Backend Phase B4 (parallel-intake fast-follow), ONLY after v1 ships + the 6 prerequisites
    (pool sizing FIRST, global token bucket, atomic claim, etc.) land. See its investigation doc.

DISCIPLINE: feature branch + PR + Codex /codex review (4-round soft cap) before each merge;
verify_consumer_contracts.py (0 drift) on every backend PR; founder authorizes push-to-main / merge /
Railway-Vercel-AWS-secret changes. eq-frontend is a SHARED checkout — `git branch --show-current` before
EVERY commit there. Update Linear + audit the handoff docs at the end of each build session.
```

---

## LOCKED DECISIONS (do NOT re-litigate)

The 6 from 2026-06-04 + this session's refinements:
1. **Placement:** design-partner-GATED Granola card in the existing onboarding `meeting-connect` step +
   Settings → Connections (per-org allowlist; supersedes LOCKED-30 standalone page).
2. **Connect rail = the existing gateway-JWT proxy** (`callBackend` + `mintInternalJwt(pg_user_id)` →
   `BACKEND_SERVICE_TRANSCRIPTION_URL`), NOT OAuth. Verified: backend `middleware/jwt_auth.py` checks the
   same `INTERNAL_JWT_*`; no audience override; the rail already serves `/text/clean` in prod.
3. **Account-creation approval queue = per-user**, ONE SHARED, source-tagged table (`pending_account_
   mappings`; email + Granola/transcripts). The backend already routes unknown-account meetings there
   (Scenario C; live). The FRONTEND approvals inbox is OUT of this project (separate future build, EQ-95).
4. **On approval → re-process on the next 5-min poll** (instant is a fast-follow, §2.1 #9).
5. **Full multi-folder in v1.** Folder LIST in `config` (no migration); array `/connect`/`/status`.
6. **Background history-import replaces the synchronous first poll** (amends LOCKED-31), with a progress
   signal. **+ Import-scope choice (D6):** "import past meetings" (history, default) vs "only going
   forward" (forward = anchor `last_polled_at`=now() at connect, NO backfill).

**One small open item:** the import-scope DEFAULT is set to "history" (my recommendation). It's a UI
default, confirmable at build/design time — the founder may flip it to "forward."

## KEY STATE (verified 2026-06-04)

- **Backend** `live-transcription-fastapi` main `fafaee2` (engine tip `e0aafbe`). Railway prod `/health`
  200. Prod `ALLOW_LEGACY_HEADER_AUTH=true`. The Granola engine is COMPLETE + LIVE.
- **Frontend** `eq-frontend`: SHARED checkout, branch-hops; was `main` @ `202a691f`. Granola = greenfield.
  Prisma owned here. **Re-verify its branch/state at build start.**
- **CONNECTED test credential KEPT** (founder choice): tenant `11111111-…`, user `061ae392-…`
  (stokeseqrm@gmail.com), folder `fol_sBJi17PeBXpHN7` ("Test EQ"). 5-min trigger LIVE. LOCKED-11 cleanup
  of verified test data DEFERRED until the founder says.
- Neon prod `super-glitter-11265514`. Railway live-transcription: project `847cfa5a-…`, env `e4c5ec15-…`,
  service `59a69f3d-…`.

## LINEAR (project "Granola Integration", team Eq-core)

- **EQ-90** — planning session — **Done.**
- **EQ-91** — Backend B1+B2 (multi-folder) — Backlog; carries the §1a corrections.
- **EQ-92** — Backend B3 (background import) — Backlog; carries the §1a corrections.
- **EQ-94** — Frontend F1–F4 — Backlog; carries C11/C12.
- **EQ-93** — Backend B4 (parallel-intake) — Backlog; investigation done, fast-follow, 6 prerequisites.
- **EQ-95** — Frontend approvals inbox — retitled "[Future / OUT OF GRANOLA SCOPE]"; separate future
  project; backend routing already live.

## DISCIPLINES / POSTURE

- USER = non-developer founder. Plain English. Make confident technical calls; surface product/strategic
  decisions + the honest tradeoff. Verify against the repo + the DB — don't assert from memory or a
  branch-drifted Prisma file (see `verify_existing_behavior_before_scoping`).
- Per-action founder auth for: push-to-main, merge, Vercel/Railway/AWS/GitHub-secret changes. Feature
  branch + PR + branch push are fine. eq-frontend is a SHARED checkout — `git branch --show-current`
  before EVERY commit.
- NEVER modify downstream envelope contracts (LOCKED-38). Tenant isolation everywhere. Prisma owned by
  eq-frontend. **Codex pre-merge gate (4-round soft cap) on every build PR.** `verify_consumer_
  contracts.py` 0-drift on every backend PR.
- Guiding principle: EQ is a next-gen AI-native CRM; adopt June-2026 cutting-edge patterns as a
  differentiator, balanced with production stability.
- Standing founder decisions: the test credential stays CONNECTED + the 5-min trigger LIVE; LOCKED-11
  cleanup deferred until the founder says.

## NOTE ON THE UNTRACKED DOCS

The 5 Granola planning docs (this kickoff + the plan + the 3 task docs) are UNTRACKED on `main` — the
founder controls commits. They are on disk, so the next session reads them from the working tree.
**Recommend the founder commit them** so they're durable in git history (not just the working tree),
especially given the shared-checkout branch-hopping.
