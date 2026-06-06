# Next-session opening prompt — Granola EQ-94: the GREENFIELD FRONTEND (F1-F4)

**Written:** 2026-06-06, end of the session that ran + PASSED the prod import E2E (history + forward) and moved
**EQ-92 to Done**. The entire Granola BACKEND (EQ-91 B1+B2 + EQ-92 B3) is SHIPPED, DEPLOYED, and now
**prod-E2E-verified**. The only remaining Phase-3 work is the **greenfield frontend, EQ-94 (F1-F4)** in the
`eq-frontend` repo. Paste the block below to start it.

---

```
You're continuing the multi-session Granola.ai → EQ meeting-ingestion BUILD: EQ-92 / Phase 3. The BACKEND is
DONE and PROD-VERIFIED. EQ-91 (B1+B2) and EQ-92 (B3, the background history-import) are ALL SHIPPED + DEPLOYED
(live-transcription-fastapi main @ 2534598 / B3 061ef37; active Railway deploy 105cd404; /health 200; routes
live + auth-gated) AND the prod import E2E (history + forward) PASSED on 2026-06-06 — so EQ-92 is DONE in Linear.
The ONLY remaining Phase-3 work is EQ-94: the greenfield Granola connect FRONTEND (F1-F4) in eq-frontend.
I'm a non-developer founder — plain English, confident technical calls, surface product/strategic decisions +
the honest tradeoff. I authorize every merge + each Vercel/secret change.

CONTINUITY IS CRITICAL. Read everything before you touch anything. Trust but verify each artifact against the
repo + the DB; if anything is missing or doesn't match, STOP and tell me.

STEP 1 — Run /context-restore FIRST. It must load the checkpoint titled "granola-e2e-passed-eq94-next"
(file prefix 20260606). If it loads anything else, STOP (fallback: this doc).

STEP 2 — Read these IN FULL before acting:
  • docs/superpowers/plans/2026-06-04-granola-phase-3-fe-be.md (live-transcription-fastapi repo) — SOURCE OF
    TRUTH. The BUILD STATUS banner (backend shipped + E2E passed), §0 locked decisions, §1 D1-D6, §1a C11/C12
    (the two FRONTEND corrections), §2 the LIVE cross-repo contract (/validate, /connect, /status shapes), and
    STREAM 2 — FRONTEND §F1-F4 (the executable steps) + the Design notes.
  • tasks/granola-existing-system-map.md (live-transcription-fastapi repo) — §B FRONTEND (the onboarding wizard,
    the two connection patterns, the gateway-JWT rail, the key frontend files) + §C the cross-repo contract.
  • memory/MEMORY.md + memory/project_granola_integration.md (the 2026-06-06 (E2E) "PASSED" entry — the
    authoritative backend state the frontend builds against).
  • feedback memories: branch_safety (LOAD-BEARING — eq-frontend is a SHARED branch-hopping checkout), 
    tenant_isolation, shared_infrastructure_collision, verify_existing_behavior_before_scoping; reference:
    prisma_schema_ownership, railway_proxy_timeout, granola_api_shape.

STEP 3 — Verify state (if any check fails, STOP):
  • Backend health: curl -s https://live-transcription-fastapi-production.up.railway.app/health  → {"status":"ok"}
    (retry past the benign ~15% cold-start 502, EQ-105).
  • The LIVE, prod-verified contracts the frontend consumes (already deployed):
      POST /integrations/granola/validate  {api_key} → {ok, folders:[{id,name}]}
      POST /integrations/granola/connect   {api_key, folders:[{id,name}], mode:"folders", import_scope:"history"|"forward"}
                                           → history: {ok, status:"connected", import:{import_run_id, state:"queued", total:null, done:0}}
                                           → forward: {ok, status:"connected", import:null}
      GET  /integrations/granola/status    → {connected, status, last_polled_at, mode, import_scope, folders:[{id,name,status}],
                                              activity:{ingested_7d,deferred_7d,errors_7d}, import:{state,total,done,deferred,skipped,errors,...}|omitted-for-forward, last_error}
      POST /integrations/granola/rotate    {new_api_key} → {ok}
      DELETE /integrations/granola         → {ok, status:"disconnected"}
    (All JWT-authed via the gateway-JWT rail; /connect+/status+rotate+disconnect REQUIRE pg_user_id in the JWT;
     /validate needs only the bearer JWT. The backend binds the credential to the JWT's pg_user_id.)
  • eq-frontend is a SHARED branch-hopping checkout (~18 worktrees; a chat-modernization agent was active there
    during the E2E session). Run the shared-infra-collision check; STRONGLY prefer a dedicated git worktree for
    EQ-94 so a parallel agent's `git checkout` can't strand your commits. git branch --show-current before EVERY commit.

STEP 4 — BUILD EQ-94 (F1-F4), each phase = its own PR + /codex review pre-merge gate (4-round soft cap):
  • F1 — Gateway proxy + tRPC procedures (lib/trpc/routers/granola.ts: validate/connect/status/disconnect/rotate)
    over the EXISTING gateway-JWT rail (lib/gateway-forward.ts callBackend + lib/internal-jwt.ts mintInternalJwt
    with pg_user_id → BACKEND_SERVICE_TRANSCRIPTION_URL). C11: connect carries importScope → import_scope. C12
    (SECURITY): call isGranolaEnabled(ctx.tenantId) inside EVERY procedure (FORBIDDEN if not allowlisted), not
    just at card render. Create lib/feature-gates/granola.ts (D3 tenant allowlist). MSW tests incl. the FORBIDDEN
    case. BUILD-TIME VERIFY: the frontend's mintInternalJwt token is accepted by routers/granola.py as-is
    (audience/claims) — it is in prod (the E2E minted the same iss=eq-frontend/aud=eq-backend/pg_user_id claim).
  • F2 — Design-partner-gated GranolaConnectCard in the onboarding meeting-connect step + Settings→Connections
    (reuse isGranolaEnabled; card gate is UX-only, the API is already gated in F1). Match the house design system
    (shadcn new-york + eq-tokens.css + GlassPanel + Framer Motion + Inter/Source Serif 4); follow docs/page-creation-guide.md.
  • F3 — Key-paste → user-selected multi-folder picker → import-scope choice wizard (3 steps). D6 default = history.
    On connect: history → card goes to `importing` (F4 progress); forward → straight to `connected` (no bar).
  • F4 — Connected status panel: folder list + per-folder status badges + 7-day activity + import progress
    (poll GET /status every ~4s while import.state ∈ {queued,running}; STOP on complete/failed/cancelled — D2/C18;
    indeterminate while total=null — C14). Affordances: change folders (→ active-row reconfigure), rotate, disconnect.
  • NO Prisma migration needed (config is opaque JSONB; granola_import_runs already live). NO downstream envelope
    changes. The frontend approvals inbox is OUT of scope (separate future project — see plan §0 / F5-removed).

DISCIPLINE: feature branches + git branch --show-current before every commit (eq-frontend is SHARED — use a
worktree); /codex review pre-merge gate on every PR; tenant isolation + the C12 gate in every procedure; match
the house design system (don't invent); I authorize merges + Vercel/secret changes. Honor open tickets (do NOT
re-implement): EQ-105 (sync boto3/KMS off the loop), EQ-109 (per-loop asyncpg pool), EQ-135 (per-activation
import-lifecycle scoping), backlog #21a/#21b. At session end, run the full handoff audit (repo docs + memory +
Linear mutual consistency) + a stale-signature grep.

Don't improvise — if anything is missing or doesn't match, STOP and tell me.
```

---

## State at handoff (2026-06-06, end of the prod-E2E session)
- **Backend EQ-91 + EQ-92 = SHIPPED + DEPLOYED + PROD-E2E-VERIFIED.** live-transcription-fastapi main @ `2534598`
  (B3 = `061ef37`); active Railway deploy `105cd404` (the docs-commit redeploy; `9cda4b1e` REMOVED/superseded —
  SAME B3 code); `/health` 200. EQ-92 = **Done** in Linear (the prod-E2E-PASSED comment is on the issue).
- **Prod import E2E PASSED** (history + forward) on the shared test tenant `11111111-…` / cred `6a727bae-…` /
  folder `fol_sBJi17PeBXpHN7` "Test EQ" (2 notes). Full record: memory `project_granola_integration` 2026-06-06
  (E2E) entry + the plan BUILD STATUS banner. Key results: `/connect` history = 0.26s async ACK → import_run
  queued→running→complete (total=2) on `GRANOLA_IMPORT_QUEUE`; success-note short-circuit (no re-ingest); forward
  = `import:null` + watermark anchored + no import_run + `/status` omits the import block.
- **Test cred end state:** `6a727bae` left **connected in FORWARD scope** (was legacy single-folder). Founder
  standing decision to keep it connected; LOCKED-11 cleanup still deferred until the founder says.
- **NEXT = EQ-94 (the greenfield frontend, F1-F4)** in eq-frontend. Backend contracts LIVE + prod-verified.
- **Open tickets (do NOT re-implement):** EQ-105 (sync boto3/KMS off the loop), EQ-109 (per-loop asyncpg pool),
  EQ-135 (per-activation import-lifecycle scoping). Backlog #21a (reconfigure backfill) / #21b (exact-progress items).
- **eq-frontend caution:** SHARED branch-hopping checkout; a chat-modernization agent (citations/InlineCitation)
  was active there during the E2E session — NOT touching Granola, but use a worktree + branch-check discipline.
