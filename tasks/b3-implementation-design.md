# EQ-92 / B3 — concrete backend implementation design (pre-build)

Background history-import for Granola. The eq-frontend `granola_import_runs` table is ALREADY shipped
(PR #454, live in prod). This is the backend that writes it. Plan = source of truth:
`docs/superpowers/plans/2026-06-04-granola-phase-3-fe-be.md` §Phase B3 (C1/C2/C3/C4/C7/C8/C9/C13/C17/C18).

Source files this touches (real code — read these): `routers/granola.py` (connect 606-824, mode="all"
guard 635-642, save&test 814-824, /status 886-951), `services/granola_ingestion/scheduler.py`
(GRANOLA_POLL_QUEUE:83, run_cycle_step 240-371, granola_poll_one_credential 378-427, _advisory_lock_key
226-237, list_active_credentials 153-218), `services/granola_ingestion/adapter.py` (run_one_cycle 169-457,
_mark_credential_polled_success 2268-2305, _credential_is_active 1884-1924, _UPSERT_INTEGRATION_RUN_SQL),
`services/vault/user_credentials.py` (update_credential_config 1119-1219 = the template), `services/
asyncpg_pool.py` (sizing invariant 72-86), `services/granola_ingestion/outcomes.py` (status enum).

DB truth (Neon `super-glitter-11265514` / `eq-dev` production branch): `granola_import_runs(id,tenant_id,
user_id,credential_id,state,total,started_at,finished_at,created_at,updated_at)` with partial-unique
`(credential_id) WHERE state IN ('queued','running')`, CHECK on state, FK credential→vault.user_credentials
(CASCADE). `external_integration_runs` has NO credential_id; UNIQUE `(tenant_id,user_id,provider,external_id)`;
status ∈ {success, deferred_pending_account, skipped_no_business_attendees, failed, failed_permanent} +
intermediate 'in_progress'. `user_credentials` UNIQUE (tenant_id,user_id,provider); a credential is 1:1 with
(tenant,user,'granola').

---

## 1. `services/granola_ingestion/import_runs.py` (NEW) — lifecycle + DERIVED progress (C1)
Plain asyncpg module (like the adapter's integration-run helpers; NOT a vault accessor → no ALLOWLIST).
All funcs take `pool` + are tenant/user scoped.

- `get_or_create_active_import_run(*, pool, credential_id, tenant_id, user_id) -> (uuid, bool created)`:
  `INSERT INTO granola_import_runs (tenant_id,user_id,credential_id,state) VALUES (...,'queued')
   ON CONFLICT (credential_id) WHERE state IN ('queued','running') DO NOTHING RETURNING id`.
  If a row returned → (new_id, True). If empty (an active run exists) → `SELECT id FROM ... WHERE
  credential_id=$ AND tenant_id=$ AND user_id=$ AND state IN ('queued','running')` → (existing_id, False).
  Idempotent → satisfies enqueue-atomicity (C8) + idempotent /connect retry.
- `mark_running(pool, id)`: `UPDATE ... SET state='running', started_at=COALESCE(started_at,NOW()),
  updated_at=NOW() WHERE id=$ AND state IN ('queued','running')`. started_at anchors the derived counts.
- `set_import_total(pool, id, total)`: `UPDATE ... SET total=$, updated_at=NOW() WHERE id=$`.
- `complete_import_run / fail_import_run / cancel_import_run(pool, id)`: `UPDATE ... SET state=<terminal>,
  finished_at=NOW(), updated_at=NOW() WHERE id=$ AND state='running'` (cancel also allowed from 'queued').
- `read_import_progress(pool, id) -> dict | None`: read the run row; then
  `SELECT status, count(*) FROM external_integration_runs WHERE tenant_id=$ AND user_id=$ AND
   provider='granola' AND created_at >= $started_at GROUP BY status` (only when started_at not null).
  Map: success→done, deferred_pending_account→deferred, skipped_no_business_attendees→skipped,
  failed/failed_permanent→errors; 'in_progress' uncounted. Returns {state,total,done,deferred,skipped,
  errors,started_at,finished_at}. **Idempotent read; no counter mutation.**
- `latest_import_run(pool, credential_id, tenant_id, user_id)`: most-recent row by created_at (for /status).

## 2. `services/granola_ingestion/scheduler.py`
- `GRANOLA_IMPORT_QUEUE = Queue("granola-import", concurrency=2)` (C3 — separate from the poll queue).
- `granola_import_one_credential(credential_id, tenant_id, user_id, import_run_id)` @DBOS.workflow —
  mirrors granola_poll_one_credential; calls run_import_step.
- `run_import_step(*, credential_id, tenant_id, user_id, import_run_id)` @DBOS.step(retries_allowed=False):
  - **Lock-busy (C2):** acquire the per-credential advisory lock. PROPOSED: blocking acquire with a bounded
    wait — `SET LOCAL lock_timeout='30s'; SELECT pg_advisory_xact_lock(key)` inside a txn; on lock_timeout
    error, re-enqueue the SAME workflow id (backoff) leaving import_run state unchanged (still queued/running)
    — never strand. (Open Q2 — alternative: keep pg_try_advisory_lock + re-enqueue on miss.)
  - load credential (vault, ALLOWLIST has scheduler); if inactive → cancel_import_run + return.
  - mark_running(import_run_id); `run_one_cycle(credential, pool, import_run_id=import_run_id)` with the
    credential's last_polled_at left NULL (full backfill).
  - on clean finish → complete_import_run; on `_CredentialDeactivated`/cycle_aborted (C9) → cancel_import_run;
    on raise → fail_import_run.
- **Poll defers to an active import (NEW coordination, Open Q1):** in run_cycle_step (poll path), after
  acquiring the lock, if `latest active import_run` exists for this credential → return skipped
  (reason='import_in_progress'). Because get_or_create_active_import_run runs at /connect BEFORE the import
  dispatch, any poll that fires first sees the active run and defers → the IMPORT owns the backfill, not the
  poll. Once the import is terminal (sets last_polled_at), polls resume normally.

## 3. `services/granola_ingestion/adapter.py`
- `run_one_cycle(..., import_run_id: UUID | None = None)`: when set, after the first `list_notes` completes
  and the deduped note list is known, call `set_import_total(pool, import_run_id, len(notes))`. Everything
  else unchanged. Progress stays DERIVED (no per-note counter). The full-backfill behavior (last_polled_at
  NULL → created_after None) is the existing behavior.
- cancel signalling: run_one_cycle already returns cycle_aborted on `_CredentialDeactivated`; the import step
  reads that to choose cancel vs complete.

## 4. `routers/granola.py` `/connect` restructure
- Capture `forward_anchor_at = datetime.now(UTC)` at ROUTE ENTRY, before any awaits (C4).
- LIFT the mode="all" 400 guard (635-642).
- After the credential is durable (INSERT / reactivate / active-row reconfigure), branch on `import_scope`:
  - `forward`: `anchor_credential_watermark(pool, credential_id, forward_anchor_at)` (new vault helper, under
    the advisory lock already held); NO import_run; return `{ok,status:'connected',import:null}`.
  - `history` (default): `get_or_create_active_import_run(...)`; dispatch `granola_import_one_credential` on
    GRANOLA_IMPORT_QUEUE with `SetWorkflowID(f"granola_import_{credential_id}_{import_run_id}")`; return
    `{ok,status:'connected',import:{import_run_id,state:'queued',total:null,done:0}}`.
  - DELETE the synchronous `_save_and_test_locked` call (814-824).
- **Enqueue atomicity (C8):** get_or_create + SetWorkflowID make dispatch idempotent. ALSO: `/status` (and a
  `/connect` retry) detects "active history credential, last_polled_at NULL, no active/complete import_run" →
  create + dispatch a recovery import.
- **Active-row reconfigure backfill (Step 5b / C17) — Open Q4:** on add-folder through the B2 active-row
  reconfigure path (update_credential_config), backfill ONLY the newly-added folders:
  - `history`: dispatch a scoped import for just the new folders.
  - `forward`: backfill the new folders from now() WITHOUT moving the global last_polled_at (re-anchoring
    would skip existing-folder meetings since the last poll). True per-folder watermarks are a later follow-up.
  - This needs run_one_cycle to accept a `folders_override` (a subset) + a per-folder `created_after`. This is
    the most complex piece — candidate to split to a B3 fast-follow if it balloons.

## 5. `services/vault/user_credentials.py` — NEW `anchor_credential_watermark(*, pool, credential_id,
tenant_id, user_id, ts, caller_module, trace_id)`
Mirrors `update_credential_config` (1119-1219): advisory-lock-gated (caller holds it), 3-field WHERE
`(id,tenant_id,user_id)` + `status='active' AND archived_at IS NULL`, same-txn success audit, separate-conn
failure audit, `RETURNING id` → NOT_FOUND if null. SQL: `UPDATE vault.user_credentials SET last_polled_at=$ts,
updated_at=NOW() WHERE ...`. Writes the EXISTING last_polled_at column (NOT a new column). Forward path only,
at connect, under the lock, before any cycle → no contention with the adapter's mid-cycle watermark writes.

## 6. `/status` import block (C18)
`latest_import_run` + `read_import_progress` → add `import` to the response:
`{import_run_id,state,total,done,deferred,skipped,errors,started_at,finished_at}`. state ∈ {queued,running,
complete,failed,cancelled}. total null until first listing → FE shows indeterminate (C14). OMIT the block
when no import_run exists (forward connections + pre-B3 legacy credentials).

## 7. `services/asyncpg_pool.py` — pool sizing (Open Q6)
Raise `_DEFAULT_MAX_SIZE` 10 → **20** and re-derive the invariant to
`max_size >= 2 × (GRANOLA_POLL_QUEUE.concurrency + GRANOLA_IMPORT_QUEUE.concurrency) = 2×(5+2)=14` (+headroom
for the import's extra transient writes to granola_import_runs + external_integration_runs). Update the
docstring. 20 stays well under Neon's connection ceiling.

---

## POST-CONSULT DECISIONS (BINDING — 2026-06-06; supersede §1-7 where noted)
Codex consult (high reasoning) reviewed the design against the real code. Resolutions:
- **A1 (Q1 backfill race) — poll guard is credential-state-based, NOT import_run-based.** Activation
  (`store_credential` commit) and import-run creation are not one txn, so a poll can fire in the gap with
  `last_polled_at IS NULL`. FIX: in `run_cycle_step` (poll), after the lock, SKIP the credential when it is
  "uninitialized": `config.import_scope=='history' AND last_polled_at IS NULL` (return skipped,
  reason='awaiting_import') OR `config.import_scope=='forward' AND last_polled_at IS NULL` (reason=
  'awaiting_forward_anchor'). This makes the IMPORT (or the forward anchor) the only writer of the first
  watermark, independent of request/dispatch ordering. (Legacy pre-B3 credentials have no import_scope +
  may have NULL watermark briefly — treat missing import_scope as 'history' for this guard ONLY if a
  config has no import_scope AND no folders… actually pre-B3 legacy creds already have last_polled_at SET
  (they've been polling), so the NULL-watermark guard won't trip for them. New B3 connects set import_scope.)
- **A2 (Q2 lock-busy) — keep `pg_try_advisory_lock`** (session lock on a checked-out conn, like
  run_cycle_step). Do NOT hold a txn-scoped lock across the Granola import. On lock-busy: leave the
  import_run `queued`, return reason='lock_busy'; a recovery dispatcher (the cron tick) re-dispatches queued
  imports that have no live workflow, using a NEW workflow id per attempt (NOT the same id — DBOS dedupe
  would no-op it). With A1, lock contention for a fresh import is rare (poll defers), so this is an edge path.
- **A3 (Q7 cancel vs complete) — surface `cycle_aborted` on `CycleResult`.** `run_one_cycle` currently
  swallows `_CredentialDeactivated` into a local flag and returns a normal result. ADD `cycle_aborted: bool`
  to `CycleResult`, set True on every deactivation path (pre-list inactive, mid-note `_CredentialDeactivated`,
  pre-success liveness fail, reprocess deactivation). The import wrapper: `cycle_aborted` → cancel_import_run;
  `credential_error_code` set → fail_import_run; else → complete_import_run. (run_one_cycle returns
  credential-level errors instead of raising, so the wrapper MUST check credential_error_code or it would
  mark auth/folder failures complete.)
- **A4 (Q4 reconfigure backfill) — SPLIT OUT to a fast-follow (Linear EQ-109 sibling; backlog #21a).** B3 v1
  = fresh/reconnect history import + forward anchor ONLY. Active-row reconfigure keeps B2 behavior (update
  config.folders; new folders picked up by the normal poll from the shared watermark). No `folders_override`.
- **A5 (Q3 derived progress) — DERIVED, scoped honestly to fresh imports.** Exact for a fresh first import
  (all rows created during the import → `created_at >= started_at` captures all; no short-circuits). Undercounts
  on re-runs — out of v1 scope with A4 split. Exact re-run progress = items-table fast-follow (backlog #21b).
  Keep C1's no-counter rule.
- **A6 (Q5 forward anchor) — confirmed OK** (no adapter conflict; the poll guard A1 covers the
  activation-before-anchor race). Do NOT re-anchor on active-row folder adds.
- **A7 (Q6 pool) — ticket + proceed (Linear EQ-109).** Keep the single shared pool (mirrors the proven poll
  pattern). Bump `_DEFAULT_MAX_SIZE` 10→20, env-overridable via `GRANOLA_DB_POOL_MAX_SIZE`; re-derive the
  invariant to `>= 2×(poll+import concurrency)=14`. Per-loop pool ownership = EQ-109 fast-follow.

## OPEN QUESTIONS for Codex consult (RESOLVED above — see POST-CONSULT DECISIONS)
- **Q1 (poll vs import coordination):** Is "create the import_run at /connect BEFORE dispatch + the 5-min poll
  skips a credential that has an active import_run" the right way to guarantee the IMPORT (not the poll) does
  the initial backfill on a NULL-watermark credential? Any race where the poll fires + does the backfill
  before the import_run row is visible? (import_run is INSERTed in the /connect request txn before the ACK.)
- **Q2 (lock-busy C2):** Best DBOS-idiomatic way for the import to wait/requeue when the poll holds the
  per-credential advisory lock, staying state='queued' and never stranding — blocking pg_advisory_xact_lock
  with lock_timeout + re-enqueue, or try-lock + re-enqueue with backoff? With Q1's poll-defers rule, does the
  import ever actually find the lock held (only the brief window before the import_run is visible)?
- **Q3 (derived progress):** Counting external_integration_runs by (tenant,user,'granola') + created_at >=
  started_at — correct? Edge: on a newly-added-folder re-import, pre-existing rows have old created_at (not
  counted) — does total (len of new-folder listing) vs done diverge confusingly? Should the reader scope to
  the import somehow (it has no import_run_id column)?
- **Q4 (reconfigure backfill, Step 5b/C17):** Is the scoped-new-folder backfill worth building in this PR, or
  split to a B3 fast-follow? If in-scope, is `folders_override` on run_one_cycle the cleanest seam?
- **Q5 (forward anchor):** Writing last_polled_at via a new vault helper at connect (under the lock, before any
  cycle) — any conflict with the adapter "owning" last_polled_at mid-cycle?
- **Q6 (pool sizing):** max_size 10→20 + the new invariant — right call? Env-configurable?
- **Q7 (cancel C9):** Reusing run_one_cycle's cycle_aborted (_CredentialDeactivated) to pick cancel vs complete
  — correct? Any path where a deactivated-mid-import run is wrongly marked complete?
