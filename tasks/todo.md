# B3 / EQ-92 — Background Granola history-import (execution plan)

**Source of truth:** `docs/superpowers/plans/2026-06-04-granola-phase-3-fe-be.md` §Phase B3 (C1/C2/C3/C4/C7/C8/C9/C13/C17/C18).
**Status:** 🟢 PR 1 (migration) SHIPPED + deployed + verified in prod (2026-06-06). PR 2 (backend) IN PROGRESS.
- **PR 1 ✅** — eq-frontend #454, squash `54b9dbc8`, merged + Vercel-deployed; `public.granola_import_runs` live in `eq-dev`/super-glitter `production` branch (table + partial-unique + CHECK + 3 FKs verified). Codex gate PASS (1 P2 folded). Worktree removed.

## What I verified before planning (trust-but-verify)
- [x] Backend `922660b` present on `main`; `/health` 200 (intermittent cold-start 502 root-caused → benign; latent sync-boto3 ticketed EQ-105 / backlog #19 as fast-follow — NOT a B3 blocker).
- [x] Neon truth: `vault.user_credentials` PK = `id` (uuid), UNIQUE `(tenant_id,user_id,provider)`. `external_integration_runs` has **no credential_id** — keyed `(tenant_id,user_id,provider,external_id)` UNIQUE + `(provider,status,created_at)` index. `granola_import_runs` does NOT exist yet.
- [x] Live test credential `6a727bae-…` still LEGACY single-folder config (`{folder_id,folder_name}`, no import_scope) — B3 must tolerate missing `import_scope` on pre-B3 rows; its `/status` shows no import block (no import_run row).
- [x] eq-frontend Prisma: multiSchema ON (`schemas=["public","vault"]`); new-table PK convention = `uuidv7()`.
- [x] Code anchors mapped (current lines): connect_granola 606–824, mode="all" guard 635–642 (LIFT), save&test 814–824 (REMOVE), /status 886–951; scheduler GRANOLA_POLL_QUEUE:83, run_cycle_step 240–371, granola_poll_one_credential 378–427, _advisory_lock_key 226–237; adapter run_one_cycle 169–457, _mark_credential_polled_success 2268–2305, _credential_is_active 1884–1924; vault update_credential_config 1119–1219, ALLOWLIST 68–82.

## Deploy/merge order (plan §5) — each step = 1 PR + Codex gate + founder-authorized merge
1. **eq-frontend `granola_import_runs` Prisma migration** → Vercel deploy applies to Neon → verify. (MUST deploy before the backend PR.)
2. **Backend B3 (EQ-92)** → Railway deploy → `/health` 200.

---

## PR 1 — eq-frontend: `granola_import_runs` migration
- [ ] Work in an ISOLATED git worktree off `origin/main` (shared checkout is on another agent's branch `eq-81-…` with uncommitted work). `git branch --show-current` before every commit.
- [ ] Add `granola_import_runs` model to `prisma/schema.prisma` in **`public`** schema (sibling of `external_integration_runs`), cross-schema FK to `vault.user_credentials(id)`:
  - `id uuid @id @default(dbgenerated("uuidv7()"))` (repo convention), `tenant_id uuid`, `user_id uuid`, `credential_id uuid` (FK → vault.user_credentials.id), `state text`, `total int?`, `started_at timestamptz?`, `finished_at timestamptz?`, `created_at @default(now())`, `updated_at @updatedAt`.
  - `@@index([credential_id, state])`.
  - state ∈ {queued,running,complete,failed,cancelled} (app-enforced; optional CHECK).
- [ ] Generate migration; **hand-add the partial-unique via raw SQL** (Prisma can't express it): `CREATE UNIQUE INDEX granola_import_runs_one_active ON public.granola_import_runs (credential_id) WHERE state IN ('queued','running');`
- [ ] Mirror `external_integration_runs` RLS/role posture (verify how the backend asyncpg role accesses it; add RLS policy if its sibling has one).
- [ ] `/// ` doc-comments on key columns per the 5-layer semantic convention.
- [ ] PR → `/codex review` (4-round cap) → founder authorizes merge → Vercel deploy → **verify in Neon** (table + partial-unique exist).

## PR 2 — backend EQ-92 (live-transcription-fastapi), feature branch `phase-3/granola-be-b3`
> **Codex consult done (2026-06-06) — corrections folded into `tasks/b3-implementation-design.md` §POST-CONSULT (A1-A7).** Key: A1 poll skips uninitialized creds (`import_scope=history/forward AND last_polled_at NULL`); A2 try-lock + lock_busy + cron recovery (new wf id); A3 expose `cycle_aborted` on CycleResult + check credential_error_code; A4 SPLIT reconfigure-backfill out (→ backlog #21a); A5 derived progress exact-for-fresh-import only (#21b items-table fast-follow); A7 pool max 10→20 env-overridable, per-loop ownership → EQ-109. Pool concern ticketed EQ-109 (founder: ticket+proceed).
TDD throughout (AsyncMock, no Docker). Tests: `tests/unit/granola_ingestion/test_import_runs.py`, `test_scheduler.py`, `tests/unit/test_granola_admin.py`, `tests/unit/test_granola_cron.py`, `tests/unit/vault/test_user_credentials.py`, `tests/unit/test_asyncpg_pool.py`.
- [x] **`services/granola_ingestion/import_runs.py`** (NEW): `get_or_create_active_import_run`, `mark_running`, `set_import_total`, `complete/fail/cancel_import_run`, `read_import_progress`, `latest_import_run` — DERIVED progress (C1). Shipped in the prior session (`4e4346d`, 11 tests).
- [x] **`services/asyncpg_pool.py`** (A7) — `0410a53`: `_DEFAULT_MAX_SIZE` 10→20, env-overridable `GRANOLA_DB_POOL_MAX_SIZE` clamped up to the invariant floor `2×(poll5+import2)=14`; docstring re-derived.
- [x] **`services/vault/user_credentials.py`** (A6) — `d4c1615`: NEW `anchor_credential_watermark(*, pool, credential_id, tenant_id, user_id, ts, caller_module, trace_id)` mirrors `update_credential_config` (advisory-lock-gated, 3-field WHERE + status='active' AND archived_at IS NULL, same-txn audit, NOT_FOUND on null). Exported from `services/vault/__init__.py`.
- [x] **`adapter.py`** (A3/A5) — `7afb0b0`: `cycle_aborted` on `CycleResult` set on every edge-#12 deactivation path + threaded into the result; optional `import_run_id` → `set_import_total(len(deduped notes))` after first listing; progress stays DERIVED. (Also fixed the `_credential_is_active` docstring stale-ref in `4b1e4e7`.)
- [x] **`scheduler.py`** (A1/A2) — `5866173`: `GRANOLA_IMPORT_QUEUE` (concurrency=2, C3); `granola_import_one_credential` workflow + `run_import_step` (try-lock; lock-busy leaves queued → `state='lock_busy'`; cancel/fail/complete via cycle_aborted+credential_error_code A3; raise→fail+re-raise); POLL-DEFERS A1 guard in `run_cycle_step`; `enqueue_import_workflow` + deterministic/window-stamped id helpers + `list_recoverable_import_runs` (A2 backstop).
- [x] **`routers/granola_cron.py`** (A2 backstop) — `1ce1da1`: cron tick re-dispatches stale queued imports (window-stamped id); non-fatal; returns `imports_recovered`.
- [x] **`routers/granola.py`** (C4/C8/C18) — `4b1e4e7`: `/connect` async restructure (forward_anchor_at at route entry C4; mode="all" guard LIFTED; branch on import_scope → history dispatch + ACK / forward anchor + import:null; deleted `_save_and_test_locked`; reconfigure keeps B2 behavior per A4 + doubles as /connect-retry C8 recovery); `/status` import block (C18) + import_scope + C8/A2 best-effort recovery (window-stamped to dedup with cron).
- [x] DO NOT BUILD (split fast-follows): #21a reconfigure-backfill (active-row reconfigure keeps B2 behavior), #21b exact re-import progress items-table.
- [x] `scripts/verify_consumer_contracts.py` → **0 drift** (envelope UNCHANGED — LOCKED-38). Full unit suite **603 passed / 0 new failures** (1 pre-existing `account_provisioning` failure, identical on `main`). **0 Pyright errors** on all changed source.
- [x] `/codex review` — **4 rounds → CLEAN** (7 P1s folded across rounds 1-3; round 4 NO P1s). Founder authorized merge → **PR #39 squash `061ef37` → main** → Railway **`9cda4b1e` SUCCESS** → `/health` **200** → routes live + gated.
- [x] **Prod import E2E PASSED (2026-06-06)** — history (0.26s async ACK → import_run queued→running→complete, total=2, on `GRANOLA_IMPORT_QUEUE`, success-note short-circuit, no DLQ) + forward (`import:null`, watermark anchored, no import_run); A1 resume observed, defer by-effect. **EQ-92 = DONE in Linear.** Active deploy `105cd404` (supersedes `9cda4b1e`). Test cred `6a727bae` left connected in FORWARD scope.
- [ ] **NEXT (fresh session): EQ-94 (frontend, F1-F4)** — paste-ready prompt `docs/superpowers/specs/2026-06-06-granola-eq94-frontend-next-session-prompt.md`; resume checkpoint `granola-e2e-passed-eq94-next`.

## Session end
- [x] Updated memory (project + index), repo docs (plan banner/VERDICT, system-map, this todo), the next-session prompt; filed the residual-P2 ticket + EQ-92 in Linear; ran the stale-signature cross-check.

## Review (filled after implementation) — ✅ SHIPPED + DEPLOYED 2026-06-06
All 5 remaining PR-2 components built TDD-first (AsyncMock, no Docker), then both review gates folded:
- **commits:** `0410a53` A7 → `d4c1615` A6 → `7afb0b0` A3/A5 → `5866173` A1/A2 → `1ce1da1` A2-cron → `4b1e4e7`
  C4/C8/C18 → `e7fddd2` pre-Codex fold → `cea5578` Codex-r1 → `7665c4d` Codex-r2 → `0056024` Codex-r3 → `57c5edd` docs.
- **Founder decision:** lock-busy recovery on **both surfaces** (cron backstop + /status), per binding A2.
- **pre-Codex multi-agent review** (6 dimensions → adversarial verify): 7 findings folded.
- **Codex pre-merge gate: 4 rounds → CLEAN** (R1-R3 folded 7 P1s — anchor-lock docstring, queued-vs-running,
  RuntimeError-500, forward-watermark-overwrite [LEAST], terminal-import-strand [A1 proceeds-on-terminal],
  reconnect-lifecycle [forward-bail + cancel-active + /status scope-gate]; R4 NO P1s).
- **Shipped:** PR #39 squash `061ef37` → main; Railway `9cda4b1e` SUCCESS; `/health` 200; routes live + gated.
- **Residual P2 (ticketed):** per-activation import-lifecycle scoping (rare crashed-reconnect → poll re-lists
  dedup-safe without a fresh progress row — data correct, progress UI missing).
- **Deferred (deliberate, heavy-context):** prod import E2E → DONE 2026-06-06 (PASSED, see above; EQ-92 Done). Next = EQ-94 (frontend), its own fresh session.
