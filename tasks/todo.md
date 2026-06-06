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
TDD throughout (AsyncMock, no Docker). Tests: `tests/unit/granola_ingestion/test_import_runs.py`, `test_scheduler.py`, `tests/unit/test_granola_admin.py`.
- [ ] **`services/granola_ingestion/import_runs.py`** (NEW): `create_import_run`, `mark_running`, `set_import_total`, `complete/fail/cancel_import_run`, `read_import_progress(id)` — progress **DERIVED** (C1) via COUNT/GROUP-BY over `external_integration_runs` scoped `(tenant_id,user_id,provider='granola')` + `created_at >= started_at`. Status→bucket map (from `outcomes.py`): success→done, deferred_pending_account→deferred, skipped_*→skipped, failed/failed_permanent→errors. NO mutable counter. All tenant+user scoped. Add module to vault `ALLOWLIST` (or route reads through scheduler).
- [ ] **`scheduler.py`**: add `GRANOLA_IMPORT_QUEUE = Queue("granola-import", concurrency=2)` (C3 — NOT the poll queue) + `granola_import_one_credential` (@DBOS.workflow) + `run_import_step` (@DBOS.step) mirroring poll. Lock-busy (C2): block-with-retry / requeue, stay `queued` (don't strand). On `_CredentialDeactivated`/inactive mid-import → `cancel_import_run` (C9). **VERIFY pool invariant** (`asyncpg_pool.py:72-86`): max_size ≥ 2×(poll+import concurrency)=2×7=14 vs current 10 → bump max_size if required.
- [ ] **`adapter.py`**: thread optional `import_run_id` into `run_one_cycle`; set `total` after first listing (`set_import_total`); progress stays DERIVED (no counters).
- [ ] **`routers/granola.py`**:
  - LIFT the `mode="all"` 400 guard (635–642).
  - Capture `forward_anchor_at = datetime.now(UTC)` at ROUTE ENTRY (C4), before any awaits.
  - Rewrite `/connect` post-store branch on `import_scope`: `history` → leave `last_polled_at` NULL, `create_import_run`, enqueue on GRANOLA_IMPORT_QUEUE with `SetWorkflowID(f"granola_import_{credential_id}_{import_run_id}")`, return `{import:{...}}` ACK; `forward` → `anchor_credential_watermark(last_polled_at=forward_anchor_at)`, NO import, return `{import:null}`. Delete the synchronous `_save_and_test_locked` call (814–824).
  - Enqueue atomicity (C8): `/connect` retry + `/status` recover "active history cred, NULL watermark, no running/complete import" → create+enqueue.
  - Active-row reconfigure (B3 Step 5b / C17): on add-folder, `history`→backfill only NEW folders; `forward`→backfill new folders from now() WITHOUT moving global `last_polled_at`.
  - `/status`: add `import` block (C18) — latest import_run + derived progress; `state ∈ {queued,running,complete,failed,cancelled}`; indeterminate until `total` known (C14); omit block when no import_run (forward / pre-B3 rows).
- [ ] **`services/vault/user_credentials.py`**: NEW `anchor_credential_watermark(credential_id, ts)` — writes `last_polled_at = ts` (NOT a new column), mirrors `update_credential_config` (advisory-lock-gated, 3-field WHERE, status='active' AND archived_at IS NULL guard, same-txn audit). Forward path only; runs at connect under the lock before any cycle.
- [ ] `scripts/verify_consumer_contracts.py` → 0 drift (envelope UNCHANGED — LOCKED-38). `/codex review` 4-round cap → founder merge → Railway deploy → `/health` 200.

## Session end
- [ ] Update Linear EQ-92 + audit handoff docs (repo + Linear + memory) for mutual consistency; grep for stale signatures.

## Review (filled after implementation)
_(pending)_
