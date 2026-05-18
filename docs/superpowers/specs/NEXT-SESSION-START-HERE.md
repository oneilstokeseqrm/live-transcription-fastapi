# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-18 (M3 of the Phase-1-email-pipeline cold-inbound fix shipped, merged, AND deployed to production; AWS infrastructure provisioned + IAM verified end-to-end; 6 Codex review rounds with all P0/P1 folded into PR #9).
**Status:** ✅ **PHASE_1_EMAIL_PIPELINE_M1_M2_M3_DEPLOYED_M4_NEXT** — M3 is live in production. Subscriber is actively long-polling SQS but will receive ZERO events until M4 ships (M4 writes to `pending_interactions`, which is what makes M2's workflow emit `EmailPromoted` events). M4 (orchestrator branch + atomic `upsert_thread` rewrite) is the next milestone — **FLIPS THE SWITCH on cold-inbound capture**. Same repo as M3 (`/Users/peteroneil/eq-email-pipeline`).

---

## SESSION SCOPE FOR THE NEXT SESSION

**This session is EXECUTION of M4.** Implementation only, NOT plan revision. The plan at `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` (v4, committed as eq-email-pipeline:`033626a`) is the load-bearing artifact. Read §4 (orchestrator changes) + §5 (workflow promote step) + §6 (handler — already shipped by M3, read for context) before any code.

Recommended scope: **M4 alone**. M5 (production E2E + rollback drill per plan §10.3 + §10.4) warrants its own session — it's the first time real `EmailPromoted` events flow end-to-end through the pipeline + verifies the §11 acceptance invariants, and is high-leverage signal-gathering work that benefits from undivided attention.

---

## CRITICAL — what already shipped + verified deployed

| Milestone | Shipped | PR | Merge SHA | Deploy verification |
|---|---|---|---|---|
| Phase 1 — account-anchor contract end-to-end | ✅ 2026-05-14 | PR #10/#11 | (legacy) | (legacy) |
| M0-M2 (DBOS + Prisma) — Phase 1.5 | ✅ 2026-05-15 | PR #14/#15 + eq-frontend PR #373 | (legacy) | (legacy) |
| M3 + M4 — workflow + /approve cutover (DBOS) | ✅ 2026-05-17 AM | PR #17 | `ae45737` | (legacy) |
| M5 — verified-contract tooling | ✅ 2026-05-17 PM | PR #18 | `95f9084` | (legacy) |
| Phase-1-email-pipeline M1 | ✅ 2026-05-17 evening | eq-frontend PR #392 | **`de586bbc`** | Vercel: Prisma migrate deploy applied; Neon schema verified |
| Phase-1-email-pipeline M2 | ✅ 2026-05-17 evening | live-transcription-fastapi PR #19 | **`756575d7`** | Railway deployment `809679fc` SUCCESS; /health 200 |
| **Phase-1-email-pipeline M3** | **✅ 2026-05-18** | eq-email-pipeline PR #9 | **`85c0295`** | **Railway deployment `5c013fd3` SUCCESS; /api/health 200; subscriber long-polling SQS** |
| **Phase-1-email-pipeline M4** | ⏳ **NEXT (this session)** | TBD | TBD | TBD |
| Phase-1-email-pipeline M5 | ⏸ Future session: production E2E + rollback drill | TBD | TBD | TBD |

### Production state verified end-of-prior-session (2026-05-18)

- **Neon Postgres (eq-dev, super-glitter-11265514)**: M1 schema applied. `pending_interactions` table exists. `emails` has 3 new columns (`account_provisioning_queue_id` uuid, `local_enrichment_started_at` timestamp without time zone, `local_enrichment_completed_at` timestamp without time zone). `interaction_summaries_tenant_id_interaction_id_summary_type_key` UNIQUE exists; old single-column `interaction_summaries_interaction_id_key` is GONE. Composite FK `interaction_summaries_tenant_id_interaction_id_fkey` exists; old single-column FK is GONE. `raw_interactions_tenant_id_interaction_id_key` UNIQUE exists.
- **Railway live-transcription-fastapi**: M2 code at `756575d7`; deployment `809679fc` SUCCESS; `/health` 200.
- **Railway eq-email-pipeline**: M3 code at `85c0295`; deployment `5c013fd3` SUCCESS; `/api/ping` 200; `/api/health` 200 (postgres + neo4j + eventbridge all OK).
- **EMAIL_PROMOTED_QUEUE_URL** set on Railway eq-email-pipeline production env → subscriber's `run_polling()` is active.
- **AWS resources (account 211125681610, region us-east-1)**:
  - SQS `eq-email-promoted-queue` (300s VT, 14d retention, redrive to DLQ after 5 attempts)
  - SQS `eq-email-promoted-dlq`
  - Queue policy: `events.amazonaws.com` `SendMessage` from rule
  - EventBridge rule `route-email-promoted-to-sqs` (Source `com.yourapp.transcription`, DetailType `EmailPromoted`) → SQS target
  - IAM inline policy `SQSEmailPromotedReader` on `eq-bff-kinesis-writer` (Railway IAM principal; verified end-to-end)
- **End-to-end wire test PASSED** during M3 setup — synthetic `put-events` → SQS routed with correct envelope shape; Railway IAM creds successfully `ReceiveMessage`.

### What this means for M4

M4 can now safely:
- Branch the orchestrator's `process_email` to write to `pending_interactions` for unknown-business cold-inbound emails (instead of silently dropping them).
- Trust that `emails.account_provisioning_queue_id`, `emails.local_enrichment_started_at`, `emails.local_enrichment_completed_at` all exist in production schema.
- Trust that the M3 EmailPromoted subscriber is ready to receive events — the moment M4 writes to `pending_interactions` and M2's workflow promotes one, an `EmailPromoted` event will flow through EventBridge → SQS → M3 handler → full local enrichment.

M4 deploys safely the moment it's merged. **But this is the milestone that FLIPS THE SWITCH on real cold-inbound capture** — first time the end-to-end pipeline produces non-zero events. Treat the deploy as the moment-of-truth for the entire initiative.

---

## ⚠️ CRITICAL — M3 already shipped 4 of the 5 persistence helpers M4 was originally planned to add

The original M4 plan (§12 M4 bullet 3) lists these helpers to add to `src/persistence/postgres.py`:
- `mark_local_enrichment_started`
- `mark_local_enrichment_completed`
- `fetch_email_by_interaction_id`
- `fetch_raw_interaction`
- `fetch_contacts_for_interaction`

**M3 already shipped all 5 of these** (with `try_claim_local_enrichment` instead of the bare `mark_local_enrichment_started` — atomic CAS via `UPDATE...WHERE...RETURNING` is the race-safe form, per plan §6.2 Codex round-3 P1). They live in `src/persistence/postgres.py` lines ~488-628 (after `update_thread_summary`, before the "Provider connection helpers" section).

**M4 must NOT re-add these.** Verify in pre-flight via `grep -n "async def try_claim_local_enrichment\|async def mark_local_enrichment_completed\|async def fetch_email_by_interaction_id\|async def fetch_raw_interaction\|async def fetch_contacts_for_interaction" src/persistence/postgres.py` — expect 5 matches. If any are missing, STOP and surface (production code may have rolled back).

### What M4 actually adds to `src/persistence/postgres.py`

1. **`persist_pending_interaction(pgconn, *, interaction_id, tenant_id, queue_id, connected_user_id, content_text, email, direction, thread_key, processing_tier, filter_reason, response_time_seconds, expires_at) -> None`** — INSERT into `pending_interactions`. Plan §4.2 step 2. Takes a connection (not pool) so it can participate in the orchestrator's transaction.

2. **Rewrite `upsert_thread`** to atomic `INSERT ... ON CONFLICT (tenant_id, thread_key) DO UPDATE`. Closes the pre-existing SELECT-then-UPSERT race documented in plan §6.3 + acknowledged V1 limitation #3 (FIXED in M2 for the workflow promote path; M4 closes it for the orchestrator known-account path too). Requires the existing UNIQUE index on `email_threads.(tenant_id, thread_key)` (per `eq-email-pipeline/docs/architecture.md:854` — confirmed already in production per plan §11 acceptance invariants).

3. **Extend `email_exists`** to UNION emails + pending_interactions (plan §4.2 step 0 / §14 #1). One-liner SQL change; M4 implementation detail. Verify column type + collation match across the two tables.

### What M4 adds to `src/pipeline/orchestrator.py`

1. **Pre-allocate `interaction_id`** at the top of `process_email` (after `direction` resolution, before the `--- DEDUP ---` block at line ~112). Plan §4.3.

2. **§4.1 decision branch** after `--- ACCOUNT RESOLUTION ---` (currently at `src/pipeline/orchestrator.py:174-196`) — when `account_id is None`:
   - `target_domain_class == PERSONAL` → log + return `{"status": "dropped_personal_anchor"}` (acknowledged V1 limitation #1).
   - `target_domain_class == INTERNAL` → log warning + return `{"status": "dropped_internal_misconfigured"}` (tenant config error).
   - `target_domain_class == BUSINESS` → branch to §4.2 pending path.

3. **§4.2 pending path** — inside `process_email()`, after the §4.1 BUSINESS branch:
   - Open a single transaction on the existing `pg._pool`.
   - Call `reopen_archived_entry` / `upsert_queue_entry` to ensure queue entry exists.
   - Call `persist_pending_interaction` with the pre-allocated `interaction_id`.
   - Flush ALL `pending_signal_proposals` (sender's own signal + any other unknown-business participants).
   - Return `{"status": "pending_account_approval", "interaction_id", "queue_id", ...}`.
   - **Do NOT call** `upsert_thread`, `insert_email`, `build_skeleton`, `extract`, `write_flesh`, `embed_and_upsert`, or `update_thread_summary` — these all happen retroactively via the M3 EmailPromoted handler on promotion (§4.4 + §6.2).

4. **`insert_email` signature change** — accept caller-provided `interaction_id` instead of allocating internally. Backward-compat default: generate one if caller doesn't pass it. Plan §4.3.

### What M4 adds to tests

- Extend `tests/test_orchestrator_three_state.py` with the §10.2 cases:
  - `test_cold_inbound_unknown_sender_pending`
  - `test_cold_inbound_with_multiple_unknown_participants`
  - `test_cold_inbound_personal_anchor_dropped`
  - `test_cold_inbound_internal_anchor_misconfigured`
  - `test_duplicate_webhook_before_approval`
  - `test_cross_queue_cold_inbound_link_fill`
- Unit tests for `persist_pending_interaction`, extended `email_exists`, rewritten `upsert_thread` (atomic semantics, concurrent-call test).
- **Do NOT re-add** unit tests for `try_claim_local_enrichment` / `mark_local_enrichment_completed` / `fetch_email_by_interaction_id` / `fetch_raw_interaction` / `fetch_contacts_for_interaction` — M3 already shipped 33 tests in `tests/test_email_promoted_subscriber_unit.py` covering these.

---

## Mandatory read order for the next session (~25 min)

1. **This file.**
2. **The checkpoint** loaded via `/context-restore` (the 2026-05-18 save titled `phase-1-email-pipeline-m3-deployed-m4-next`).
3. **THE PLAN**: `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` (1207 lines). Especially:
   - §4 (orchestrator changes) — primary M4 reference.
   - §5 (workflow promote step — already shipped by M2, read for context on Step 4c thread upsert + Step 5 cross-queue link fill).
   - §6 (handler — already shipped by M3, read for context on what consumes M4's pending_interactions writes).
   - §7 (cross-repo migration ordering) — M4 is "Phase 4 — FLIPS THE SWITCH".
   - §8 (edge cases) — especially §8.1 reopen-after-ignore, §8.2 mid-promotion crash, §8.6 cross-queue cold-inbound.
   - §10.2 (integration tests) — required test additions.
   - §11 (acceptance invariants) — the ship-when-true checklist. M4 is the milestone that lets all of §11 pass.
4. **M3 PR description** (https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/9) — the comprehensive narrative for what M3 shipped + the 6-round Codex trajectory + AWS infra + the 21 LOCKED decisions.
5. **M3 code** to understand what M4 inherits:
   - `src/persistence/postgres.py` lines 488-628 — the 5 helpers M3 added.
   - `src/pipeline/email_promoted_subscriber.py` — the full M3 handler. Read `HandlerOutcome` enum docstring for the SQS receipt-deletion contract M4's pending-write must work with.
   - `src/main.py` lifespan additions — confirm M4 doesn't need to touch this.
6. **Existing code M4 modifies**:
   - `src/pipeline/orchestrator.py:1-200` — current `process_email` head + §4.1 decision point.
   - `src/persistence/postgres.py:288-356` — current `upsert_thread` (the SELECT-then-UPSERT race).
   - `src/persistence/postgres.py:362-375` — current `email_exists` (the UNION extension).
   - `src/persistence/postgres.py:189-282` — current `insert_email` (signature change).

---

## Execution sequence — M4

Per plan §4 + §12.

### Pre-flight (run BEFORE any M4 code)

1. **Confirm production state stable**:
   ```bash
   curl -sS -o /dev/null -w "live-transcription-fastapi: %{http_code}\n" https://live-transcription-fastapi-production.up.railway.app/health
   curl -sS -o /dev/null -w "eq-email-pipeline: %{http_code}\n" https://email-pipeline-production.up.railway.app/api/ping
   curl -sS https://email-pipeline-production.up.railway.app/api/health
   # Expected all 200; eq-email-pipeline checks all "ok"
   ```

2. **Verify M3 helpers + EMAIL_PROMOTED_QUEUE_URL are live**:
   ```bash
   grep -nE "async def try_claim_local_enrichment|async def mark_local_enrichment_completed|async def fetch_email_by_interaction_id|async def fetch_raw_interaction|async def fetch_contacts_for_interaction" /Users/peteroneil/eq-email-pipeline/src/persistence/postgres.py
   # Expect 5 matches.
   ```
   Verify EMAIL_PROMOTED_QUEUE_URL on Railway via mcp__railway__list_service_variables (project `f7d26745-7722-4946-aa3f-9dfc3664426f`, service `92d55588-e548-4188-a179-1d3fa9ea38d2`, env `845e3772-e146-439f-b5f5-cbdfcab6087c`) — expect `https://sqs.us-east-1.amazonaws.com/211125681610/eq-email-promoted-queue`.

3. **Verify M1/M2 schema didn't roll back** via Neon MCP `run_sql` against project `super-glitter-11265514`:
   ```sql
   SELECT (SELECT COUNT(*) FROM information_schema.tables WHERE table_name='pending_interactions') AS pending_interactions_table,
          (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='emails' AND column_name LIKE 'local_enrichment%') AS local_enrichment_cols,
          (SELECT COUNT(*) FROM pg_indexes WHERE indexname='interaction_summaries_tenant_id_interaction_id_summary_type_key') AS composite_unique_index,
          (SELECT COUNT(*) FROM pg_indexes WHERE indexname='interaction_summaries_interaction_id_key') AS old_single_unique_should_be_zero,
          (SELECT COUNT(*) FROM pg_indexes WHERE indexname='raw_interactions_tenant_id_interaction_id_key') AS raw_composite_unique;
   ```
   Expected: `{pending_interactions_table: 1, local_enrichment_cols: 2, composite_unique_index: 1, old_single_unique_should_be_zero: 0, raw_composite_unique: 1}`.

4. **Verify `email_threads.(tenant_id, thread_key)` UNIQUE index exists** (required for M4's atomic upsert_thread rewrite):
   ```sql
   SELECT indexname FROM pg_indexes WHERE tablename='email_threads' AND (indexdef LIKE '%(tenant_id, thread_key)%' OR indexdef LIKE '%(tenant_id,thread_key)%');
   ```
   Expected: at least one matching index. If missing, M4 needs a coordinated eq-frontend Prisma migration FIRST — surface to user before proceeding. Per plan §12 M1 bullet "(REMOVED v4: email_threads.(tenant_id, thread_key) UNIQUE — confirmed already exists at eq-email-pipeline/docs/architecture.md:854)" this should be present already, but verify.

5. **SHARED-TENANT-COLLISION CHECK (LOCKED-17)**:
   ```bash
   ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
   ```
   Any file modified in last hour = concurrent agent hazard. M4 itself is non-destructive (writes to test tenant via tests), but the upsert_thread rewrite touches a shared-code-path table; informational caution only unless tests are running.

6. **eq-email-pipeline state check**:
   ```bash
   git -C /Users/peteroneil/eq-email-pipeline status      # should be clean on main
   git -C /Users/peteroneil/eq-email-pipeline log --oneline -3
   # Expected top: 85c0295 M3: EmailPromoted SQS subscriber...
   ```

### M4 — eq-email-pipeline orchestrator branch + atomic upsert_thread (4-5 days, medium-high risk)

**Step 0 — Open feature branch**:
```bash
cd /Users/peteroneil/eq-email-pipeline
git checkout -b phase-1-email-pipeline/m4-orchestrator-flip-switch
```

**Step 1 — Atomic `upsert_thread` rewrite** (plan §6.3 acknowledged V1 limitation #3; the "FIXED in M2 for workflow / M4 for orchestrator" half):

Current code at `src/persistence/postgres.py:288-356` is SELECT-then-UPDATE-or-INSERT — has a race window where two concurrent calls for the same `thread_key` can either (a) both hit the SELECT-miss path and both INSERT (UNIQUE constraint violation on one), or (b) one INSERTs and the other UPDATEs but reads stale `participant_emails`.

Rewrite as a single atomic statement:
```python
async def upsert_thread(
    self, *, tenant_id: str, thread_key: str, subject: str,
    participant_emails: list[str], sent_at: datetime, account_id: str | None,
) -> str:
    """Atomic INSERT ... ON CONFLICT DO UPDATE for email_threads.

    Closes the pre-existing SELECT-then-UPSERT race documented in plan
    §6.3 limitation #3. Single Postgres statement = no race window.
    Relies on UNIQUE constraint on (tenant_id, thread_key).
    """
    tid = uuid.UUID(tenant_id)
    aid = uuid.UUID(account_id) if account_id else None
    new_id = uuid.uuid4()

    row = await self._pool.fetchrow(
        """
        INSERT INTO email_threads (
            id, tenant_id, thread_key, account_id, subject,
            participant_emails, first_message_at, last_message_at,
            message_count, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6::text[], $7, $7, 1, NOW(), NOW()
        )
        ON CONFLICT (tenant_id, thread_key) DO UPDATE SET
            message_count = email_threads.message_count + 1,
            last_message_at = GREATEST(email_threads.last_message_at, EXCLUDED.last_message_at),
            participant_emails = ARRAY(
                SELECT DISTINCT unnest(email_threads.participant_emails || EXCLUDED.participant_emails)
            ),
            account_id = COALESCE(email_threads.account_id, EXCLUDED.account_id),
            updated_at = NOW()
        RETURNING id
        """,
        new_id, tid, thread_key, aid, subject,
        sorted(set(participant_emails)), _naive_utc(sent_at),
    )
    return str(row["id"])
```

**Behavioral invariants preserved**:
- First call for a (tenant_id, thread_key): inserts new row with `message_count=1`.
- Second+ call: increments `message_count` by exactly 1, takes max of `last_message_at`, unions participant emails, preserves first non-NULL `account_id`.
- Returns the thread UUID (either new or existing).
- No race window — single statement.

**Verify all callers behave identically** after the rewrite:
- `src/pipeline/orchestrator.py:283` (known-account path) — semantics unchanged.
- M2 workflow Step 4c at `live-transcription-fastapi/services/account_provisioning/materialization.py` — calls this helper indirectly via the shared schema; verify M2's expectations on `message_count` increment match.

**Step 2 — Extend `email_exists`** to UNION emails + pending_interactions:
```python
async def email_exists(self, tenant_id: str, internet_message_id: str) -> bool:
    """Check if an email with this internet_message_id already exists in
    emails OR pending_interactions. Phase-1-email-pipeline M4: prevents
    duplicate cold-inbound retries from creating duplicate pending rows.
    """
    if not internet_message_id:
        return False
    row = await self._pool.fetchrow(
        """
        SELECT 1
        FROM (
            SELECT 1 FROM emails
            WHERE tenant_id = $1 AND internet_message_id = $2
            UNION ALL
            SELECT 1 FROM pending_interactions
            WHERE tenant_id = $1 AND internet_message_id = $2 AND archived_at IS NULL
        ) hits
        LIMIT 1
        """,
        uuid.UUID(tenant_id), internet_message_id,
    )
    return row is not None
```

Note `archived_at IS NULL` on pending_interactions — promoted/expired rows should NOT block retries.

**Step 3 — `persist_pending_interaction`** (plan §4.2 step 2):

New helper in `src/persistence/postgres.py`. Takes a connection (not pool) so it can participate in the orchestrator's transaction. Pseudo:
```python
async def persist_pending_interaction(
    conn, *, interaction_id, tenant_id, queue_id, connected_user_id,
    content_text, email, direction, thread_key, processing_tier,
    filter_reason, response_time_seconds, expires_at,
) -> None:
    """INSERT into pending_interactions. Plan §4.2 step 2."""
    await conn.execute(
        """
        INSERT INTO pending_interactions (
            interaction_id, tenant_id, queue_id, connected_user_id,
            internet_message_id, provider_message_id, provider, subject,
            from_email, from_name, to_emails, cc_emails, direction,
            has_attachments, sent_at, thread_key, content_text,
            processing_tier, filter_reason, response_time_seconds,
            expires_at, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
            $13, $14, $15, $16, $17, $18, $19, $20, $21,
            NOW(), NOW()
        )
        """,
        uuid.UUID(interaction_id), uuid.UUID(tenant_id), uuid.UUID(queue_id),
        uuid.UUID(connected_user_id), email.internet_message_id,
        email.provider_message_id, email.provider, email.subject,
        email.from_address.email, email.from_address.display_name or None,
        [a.email for a in email.to], [a.email for a in email.cc],
        direction, email.has_attachments, _naive_utc(email.sent_at),
        thread_key, content_text, processing_tier, filter_reason,
        response_time_seconds, _naive_utc(expires_at),
    )
```

Verify the exact column list against the M1 Prisma schema before writing — these column names must match. Grep eq-frontend `prisma/schema.prisma` for `model PendingInteraction` to confirm.

**Step 4 — Pre-allocate `interaction_id`** at the top of `process_email` (after `direction` resolution, before the `--- DEDUP ---` block). Plan §4.3. Pass it as an argument to both the existing `insert_email` (known path) and the new `persist_pending_interaction` (pending path).

`insert_email` signature change:
- From: `insert_email(*, tenant_id, email, content_text, ...) -> (interaction_id, email_id, summary_id)`
- To: `insert_email(*, interaction_id, tenant_id, email, content_text, ...) -> (email_id, summary_id)`
- Backward compat default: if caller passes `interaction_id=None`, generate internally (legacy callers don't break).

**Step 5 — §4.1 decision branch** in `process_email`. After the existing `--- ACCOUNT RESOLUTION ---` block at `src/pipeline/orchestrator.py:174-196`:

```python
# --- §4.1 decision branch (Phase-1-email-pipeline M4) ---
if account_id is None:
    target_domain_class = classify_domain(
        target_domain, internal_domains=internal_domains
    )
    if target_domain_class == DomainClass.PERSONAL:
        logger.info(
            "Dropping cold-inbound from personal domain — acknowledged V1 limitation",
            target_domain=target_domain, from_email=email.from_address.email,
            subject=(email.subject or "")[:80],
        )
        result.update({
            "status": "dropped_personal_anchor",
            "reason": "cold inbound from personal domain",
        })
        return result
    if target_domain_class == DomainClass.INTERNAL:
        logger.warning(
            "Dropping cold-inbound from internal-classified domain without anchor account "
            "— check tenant provider_connections + account_domains",
            target_domain=target_domain, tenant_id=tenant_id,
        )
        result.update({
            "status": "dropped_internal_misconfigured",
            "reason": "internal domain without anchor account",
        })
        return result
    # ELSE BUSINESS — fall through to §4.2 pending path (Step 6 below).
```

**Step 6 — §4.2 pending path** (when `account_id is None AND target_domain_class == BUSINESS`):

Wrap in a single transaction. Mirror the plan §4.2 pseudo-code. The transaction:
1. Calls `reopen_archived_entry` / `upsert_queue_entry` to get `queue_id`.
2. Calls `persist_pending_interaction(interaction_id=pre_allocated_id, ...)`.
3. Flushes ALL pending_signal_proposals (sender + other unknown participants).
4. Returns `{"status": "pending_account_approval", "interaction_id": ..., "queue_id": ..., ...}` — **DOES NOT** call `upsert_thread`, `insert_email`, `build_skeleton`, `extract`, `write_flesh`, `embed`, or `update_thread_summary`.

**Step 7 — Tests** per plan §10.2. Required cases:
- `test_cold_inbound_unknown_sender_pending`
- `test_cold_inbound_with_multiple_unknown_participants` (one pending row, two queue entries, signals for both)
- `test_cold_inbound_personal_anchor_dropped`
- `test_cold_inbound_internal_anchor_misconfigured`
- `test_duplicate_webhook_before_approval` (second delivery short-circuits at email_exists)
- `test_cross_queue_cold_inbound_link_fill` (Alice in cold-1.com email; Bob in cold-2.com email; approve cold-2 first → Bob's contact exists, NO link yet; approve cold-1 → interaction promoted, both Alice + Bob's links created via M2's Step 5 cross-queue OR clause)

Plus unit tests for `persist_pending_interaction`, extended `email_exists`, rewritten `upsert_thread` (especially concurrent-call atomic test).

**Step 8 — Codex review BEFORE merge** per LOCKED-10:
```bash
codex review --base main -c 'model_reasoning_effort="medium"' --enable web_search_cached
```
4-round soft cap. Extend per round-N convergence pattern (severity decreasing + non-redundant findings = real). M3 ran 6 rounds; M4 may run similar given the upsert_thread rewrite is subtle Postgres work. Past ~1500 cumulative lines, switch to `--commit HEAD` per LOCKED-18.

**Step 9 — Open M4 PR**; surface to user for merge approval. PR description should call out:
- The pending-path branching at §4.1 + §4.2.
- The atomic upsert_thread rewrite (this is the highest-risk change — Codex will scrutinize it).
- All callers verified post-rewrite.
- `email_exists` UNION extension.
- The `insert_email` signature change + backward-compat default.
- That M4 FLIPS THE SWITCH — first time real cold-inbound emails will create pending rows in production.

**Step 10 — POST-MERGE: confirm deploy succeeds + verify subscriber sees real events.** After Railway redeploy with M4 code:
- `/api/health` 200.
- Inspect SQS queue depth via `aws sqs get-queue-attributes` — expect 0 initially (no cold-inbounds yet).
- The next real cold-inbound from an unknown business sender to a connected mailbox WILL create a pending row + queue entry + signals. When that queue is `/approve`d (manually or via existing UI), the workflow promotes → EmailPromoted fires → M3 subscriber processes → Neo4j + Pinecone writes.

### M5 — DEFERRED to separate session

Per plan §12. M5 = full production E2E per §10.3 (12 numbered steps) + rollback drill per §10.4. Treat M5 as its own session — it's the verification milestone that signs off the whole Phase-1-email-pipeline initiative. LOCKED-17 Layer-1 check before running.

---

## LOCKED decisions (21 total; do NOT re-litigate)

1. DBOS substrate.
2. Single Railway replica + `executor_id=RAILWAY_REPLICA_ID`.
3. EventBridge Path A with `source="com.yourapp.transcription"` and closed `INTERACTION_TYPE_TO_DETAIL_TYPE` lookup.
4. Workflow ID = `f"queue-{queue_id}:approval-{approval_attempt_id}"`.
5. `/approve` reserves synchronously then enqueues.
6. Option B test infrastructure (test-tenant scoping in prod Neon) + `@pytest.mark.requires_db_write` + `RUN_DESTRUCTIVE_TESTS=1`.
7. **Two hard rules** — no contact / no interaction without account anchor.
8. SQLAlchemy 2.0.49 `CAST(:name AS uuid)` form.
9. Materialization REQUIRES Lane 2 raw_interactions before materializing.
10. Codex review BEFORE merging (4-round soft cap; extendable when real P1s keep surfacing — M2 ran 7, M3 ran 6, both with real findings).
11. Per-batch user confirmation for destructive ops on shared test tenant.
12. Transcripts: frontend forces anchor; emails: backend handles via pending state.
13. Recipient-as-anchor REJECTED for emails.
14. Pending-interactions pattern (Approach C).
15. Lean payload + typed columns for pending_interactions schema.
16. Path B full reprocess on promote via EventBridge `EmailPromoted` event.
17. Shared-tenant collision protocol: pre-flight `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl`.
18. Codex multi-round: `--commit HEAD` past ~1500 lines; `model_reasoning_effort=medium` default.
19. **(NEW M3) SQS-from-EventBridge** for the consumer subscription pattern. Resolved plan §14 #2 in M3 (PR #9). Chosen over direct EventBridge for at-least-once + DLQ + matching the existing Gmail/Outlook polling shape.
20. **(NEW M3) DB CAS TTL strictly > SQS VisibilityTimeout.** 10-min DB TTL vs 5-min SQS VT. Codex round-5 P1 fix; race at the boundary if equal. See `tasks/lessons.md` "DB CAS TTL must be strictly longer than SQS VisibilityTimeout".
21. **(NEW M3) `HandlerOutcome` tri-state enum** {COMPLETE, PERMANENT_SKIP, TRANSIENT_SKIP} for SQS consumer receipt-deletion decisions. Naive "no exception = delete" semantics lose messages in transient-skip paths. Codex round-2 P1 fix. See `tasks/lessons.md` "SQS consumer receipt-deletion is a tri-state decision".

---

## Acknowledged V1 limitations (NOT regressions; documented + bounded)

1. **Personal/internal anchor cold-inbound → log+drop.** V2 roadmap: audit log table.
2. **Neo4j build_skeleton + write_flesh partial-retry corruption.** Mitigation: M3 implements the 2-layer guard (atomic CAS + 10-min soft TTL > 5-min SQS VT). V2 roadmap: MERGE patterns + edge-count thread counters. M4 inherits this; not introduced by M4.
3. **`upsert_thread` race** — FIXED in M2 for the workflow promote path. **M4 closes it for the orchestrator known-account path** via the atomic INSERT...ON CONFLICT DO UPDATE rewrite (Step 1 above).
4. **Legacy per-signal loop hardcodes `summary_type='meeting'`** — for re-pointed email signals (M2 4-pre-1) it creates a duplicate 'meeting' summary alongside the existing 'email' summary. Cosmetic data inconsistency, NOT functionally broken. Future cleanup: type-aware legacy loop.
5. **(NEW M3) `build_skeleton` `CREATE` fallback for missing `internet_message_id`** — extends V1 limitation #2 to the case where MERGE-on-`internet_message_id` falls back to CREATE on missing header. Same bound (2-layer guard), same V2 roadmap (MERGE on `(tenant_id, interaction_id)` as fallback). NOT introduced by M3 — orchestrator hot path has the same property; only the workflow's promote step exposes it to cold-inbound retries.

---

## Production credentials + IDs (load-bearing reference)

- **Neon Postgres (eq-dev):** project `super-glitter-11265514`, branch `production`, database `neondb`. Direct connection (no `-pooler`) for `DBOS_SYSTEM_DATABASE_URL`.
- **Test tenant:** `11111111-1111-4111-8111-111111111111`. All test data. Per LOCKED-11.
- **Test user (FK target):** `b0000000-0000-4000-8000-000000000002`.
- **Real stokeseqrm user** (for production cold-inbound flow when real emails come through): `061ae392-47d5-4f04-9ea8-afa241f23555`.
- **Railway live-transcription-fastapi:** project `847cfa5a-b77c-4fb0-95e4-b20e8773c23e`, service `59a69f3d-9a24-4041-942a-891c4a81c5fb`, env `e4c5ec15-1931-4632-9e58-92d9c6be4261`. M2 SHA `756575d7` is deployment `809679fc` (SUCCESS).
- **Railway eq-email-pipeline:** project `f7d26745-7722-4946-aa3f-9dfc3664426f`, service `92d55588-e548-4188-a179-1d3fa9ea38d2`, env `845e3772-e146-439f-b5f5-cbdfcab6087c`, URL `https://email-pipeline-production.up.railway.app`. M3 SHA `85c0295` is deployment `5c013fd3` (SUCCESS). EMAIL_PROMOTED_QUEUE_URL set on this service.
- **Railway eq-agent-action-core:** URL `https://eq-agent-action-core-production.up.railway.app`, service `3036ea0f-afc9-4bc4-889d-c98617d81e96`.
- **eq-email-pipeline:** `/Users/peteroneil/eq-email-pipeline` (NOT under EQ-CORE/). Main HEAD `85c0295` (post-M3 merge).
- **eq-frontend:** `/Users/peteroneil/eq-frontend`. M1 merged at `de586bbc` on origin/main.
- **Internal JWT:** HS256, `INTERNAL_JWT_SECRET`, `iss=eq-frontend`, `aud=eq-backend`.
- **AWS** (account `211125681610`, region `us-east-1`):
  - EventBridge bus `default`; rule `route-email-promoted-to-sqs` (Source `com.yourapp.transcription`, DetailType `EmailPromoted`).
  - SQS `eq-email-promoted-queue` (URL `https://sqs.us-east-1.amazonaws.com/211125681610/eq-email-promoted-queue`), `eq-email-promoted-dlq`.
  - IAM principal: `eq-bff-kinesis-writer` (access key `AKIATCKASHXFGKNQ476O`, same key Railway uses for eq-email-pipeline). Inline policies include `EventBridgePutEvents`, `S3UploadBucketAccess`, `SQSBriefingEventsAccess`, **`SQSEmailPromotedReader`** (new from M3).
- **Neo4j:** Aura `c6171c63`, URI `neo4j+s://c6171c63.databases.neo4j.io`.

---

## Open questions deferred to execution

1. **`email_exists` UNION ALL exact SQL** (plan §14 #1) — resolve during M4 implementation. Verify column type + collation match across `emails.internet_message_id` and `pending_interactions.internet_message_id`. Pre-flight: `SELECT column_name, data_type, collation_name FROM information_schema.columns WHERE table_name IN ('emails','pending_interactions') AND column_name='internet_message_id';`
2. **EmailPromoted DLQ + observability** (plan §14 #5) — operations setup, separate from M4 code scope.
3. **Backfill of historical dropped emails** (plan §14 #6) — confirm in M5 that no backfill needed (test data only).
4. **Queue UI integration** (plan §14 #7) — `app/(workspace)/agent-queue` may want to surface a count of pending_interactions per queue entry. Defer to separate eq-frontend session.

---

## Stop conditions (hard — surface to user)

- `/context-restore` returns NO_CHECKPOINTS or the wrong checkpoint title.
- MEMORY.md status isn't `PHASE_1_EMAIL_PIPELINE_M1_M2_M3_DEPLOYED_M4_NEXT`.
- Pre-flight `/api/health` for eq-email-pipeline returns non-200 OR any of postgres/neo4j/eventbridge is not "ok". (M3 may have been hot-fixed or reverted.)
- The 5 M3 persistence helpers are missing from `src/persistence/postgres.py`. (M3 may have been reverted.)
- `EMAIL_PROMOTED_QUEUE_URL` is unset on Railway eq-email-pipeline production env. (M3 deploy state may have rolled back.)
- The Neon schema verification queries return unexpected values (M1/M2 may have rolled back).
- `email_threads.(tenant_id, thread_key)` UNIQUE index is missing in production. (Requires upstream eq-frontend Prisma migration FIRST; do not proceed with M4 upsert_thread rewrite until in place.)
- LOCKED-17 collision check shows a concurrent agent in another repo within the last hour AND M4 work involves running integration tests on the shared test tenant.
- You're tempted to revise the plan doc instead of surfacing a plan issue — STOP, surface the issue.
- M4's Codex pre-merge review surfaces a P1 you can't resolve in one revision round AND it's not in the known-FP family (upstream schema, hypothetical TZ flip).

---

## Handoff artifacts from the prior session (2026-05-18)

- **M3 merged**: https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/9 → `85c0295` (6 Codex rounds; R4 and R6 CLEAN; 4 commits of fixes).
- **AWS infrastructure provisioned end-to-end** (account `211125681610`):
  - `eq-email-promoted-queue` SQS, `eq-email-promoted-dlq` SQS, queue policy, `route-email-promoted-to-sqs` rule, target, `SQSEmailPromotedReader` IAM inline policy.
  - End-to-end synthetic `put-events` → SQS routing test PASSED.
  - Railway IAM creds end-to-end `ReceiveMessage` test PASSED.
- **Railway env var set**: `EMAIL_PROMOTED_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/211125681610/eq-email-promoted-queue` on eq-email-pipeline production.
- **Comprehensive checkpoint**: `~/.gstack/projects/oneilstokeseqrm-eq-email-pipeline/checkpoints/<timestamp>-phase-1-email-pipeline-m3-deployed-m4-next.md` (saved end-of-session).
- **The plan (unchanged)**: `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` (eq-email-pipeline:`033626a`).
- **Next-session prompt** (paste-ready): `docs/superpowers/specs/2026-05-18-m4-next-session-prompt.md` (this directory).

---

## Session lessons codified (in `tasks/lessons.md`)

1. **Anchor Codex with on-the-ground comments when schema lives upstream** — inline docstring near SQL referencing upstream PR + Neon-verified live state stops Codex from re-flagging the same upstream-Prisma "missing migration" FP across rounds. M3 R2 → R5: comment in R2's fix appears to have informed R5's analysis (which did NOT re-flag it). Apply to any cross-repo schema reference.

2. **DB CAS TTL must be strictly longer than SQS VisibilityTimeout** — equal values fire the race at the exact boundary. M3 R5: DB-TTL 5 min == SQS VT 5 min was a real race. Bumped DB-TTL to 10 min. Document the asymmetry in BOTH the SQL and the Python constant; add a test asserting they match.

3. **SQS consumer receipt-deletion is a tri-state decision** — `HandlerOutcome` enum {COMPLETE, PERMANENT_SKIP, TRANSIENT_SKIP} makes the receipt-delete decision explicit + reviewable. M3 R2: naive "no exception = delete" lost messages when a transient-skip handler returned after another worker won the claim. Generalizes to RabbitMQ, Kafka, NATS, Pub/Sub.
