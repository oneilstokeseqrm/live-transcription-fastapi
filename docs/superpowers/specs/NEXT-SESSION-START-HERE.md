# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-17 evening (M1 + M2 of the Phase-1-email-pipeline cold-inbound fix shipped + merged + deployed to production; 10 Codex review rounds total across both; 14 substantive findings resolved).
**Status:** ✅ **PHASE_1_EMAIL_PIPELINE_M1_M2_DEPLOYED_M3_NEXT** — Both PRs merged and deployed. Production schema is in the post-M1 state; production code is in the post-M2 state. M3 (eq-email-pipeline EmailPromoted subscriber) is the next milestone, in a DIFFERENT repo with no overlap with this session's edited files.

---

## SESSION SCOPE FOR THE NEXT SESSION

**This session is EXECUTION of M3.** Implementation only, NOT plan revision. The plan at `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` (v4, committed as eq-email-pipeline:`033626a`) is the load-bearing artifact. Read §6 (EmailPromoted handler design) before any code.

Recommended scope: **M3 alone**. M4 (orchestrator branch + atomic `upsert_thread` rewrite) FLIPS THE SWITCH on cold-inbound capture and warrants its own session per the plan. The user previously chose to stop at M3 to preserve context budget.

---

## CRITICAL — what already shipped this prior session

| Milestone | Shipped | PR | Merge SHA |
|---|---|---|---|
| Phase 1 — account-anchor contract end-to-end | ✅ 2026-05-14 | PR #10/#11 | (legacy) |
| M0-M2 (DBOS + Prisma) — Phase 1.5 | ✅ 2026-05-15 | PR #14/#15 + eq-frontend PR #373 | (legacy) |
| M3 + M4 — workflow + /approve cutover (DBOS) | ✅ 2026-05-17 AM | PR #17 | ae45737 |
| M5 — verified-contract tooling | ✅ 2026-05-17 PM | PR #18 | 95f9084 |
| **Phase-1-email-pipeline M1** | ✅ 2026-05-17 evening | eq-frontend PR #392 | **`de586bbc`** |
| **Phase-1-email-pipeline M2** | ✅ 2026-05-17 evening | live-transcription-fastapi PR #19 | **`756575d7`** |
| **Phase-1-email-pipeline M3** | ⏳ NEXT (this session) | TBD | — |
| Phase-1-email-pipeline M4 | ⏸ Future session: orchestrator branch + atomic upsert_thread (FLIPS THE SWITCH) | TBD | — |
| Phase-1-email-pipeline M5 | ⏸ Future session: production E2E + rollback drill | TBD | — |

### Production state verified end-of-prior-session

- **Neon Postgres (eq-dev)**: M1 schema applied. `pending_interactions` table exists. `emails` has 3 new columns (`account_provisioning_queue_id`, `local_enrichment_started_at`, `local_enrichment_completed_at`). `interaction_summaries_tenant_id_interaction_id_summary_type_key` UNIQUE exists; old single-column `interaction_summaries_interaction_id_key` is GONE. Composite FK `interaction_summaries_tenant_id_interaction_id_fkey` exists; old single-column FK is GONE. `raw_interactions_tenant_id_interaction_id_key` UNIQUE exists.
- **Railway live-transcription-fastapi**: M2 code deployed. Deployment `809679fc-057f-4580-984a-093d01552bb0` Status=SUCCESS. `/health` returns 200 with `{"status":"ok"}`.
- **M1↔M2 deploy window closed**: 49 seconds between M1 merge (22:28:49Z) and M2 merge (22:29:38Z); Railway deploy completed shortly after; production is consistent. No real-user impact (test-data only).

### What this means for M3

M3 can now safely:
- Write code that references `emails.local_enrichment_started_at` and `emails.local_enrichment_completed_at` — both exist in production.
- Subscribe to `EmailPromoted` EventBridge events — M2's emit step is deployed and live. **But no events will fire until M4 ships in eq-email-pipeline** (M4 is what writes to `pending_interactions`).
- Test with synthetic event injection (handler-side) before M4 ships.

M3 deploys safely the moment it's merged. No coordination needed (handler is dormant until M4).

---

## Mandatory read order for the next session (~25 min)

1. **This file.**
2. **The checkpoint** loaded via `/context-restore` (the comprehensive 2026-05-17 evening save titled `phase-1-email-pipeline-m1-m2-shipped-m3-next` — note the title says "shipped" because it was saved before merge; merge happened ~10 min after the checkpoint).
3. **`/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md`** — THE plan (1207 lines). Especially:
   - §6 (handler design) — primary M3 reference.
   - §10 (test plan) — relevant cases.
   - §11 (acceptance invariants) — the ship-when-true checklist.
   - §14 #2 (open question: subscription pattern) — resolve in M3.
   - §14 #4 (open question: light-tier handler behavior) — confirm in M3.
4. **M2 emit step**: `services/account_provisioning/eventbridge_emit.py:emit_email_promoted_for_materialization` — the upstream contract. What `EmailPromoted` events look like on the wire (Source, DetailType, JSON payload).
5. **M1+M2 merged PR descriptions** (linked above) for the comprehensive narrative + verified-shipped state.
6. Quick code scan in `/Users/peteroneil/eq-email-pipeline`:
   - `src/persistence/postgres.py` — existing helper conventions (naming, asyncpg patterns).
   - `src/pipeline/orchestrator.py` — existing async patterns.
   - `src/pipeline/skeleton.py:186` + `flesh.py:173` — the non-idempotent Neo4j writes the two-layer guard bounds.
   - `src/providers/` — existing inbound webhook handlers (informs the subscription-pattern decision).

---

## Execution sequence — M3

Per plan §6 + §12.

### Pre-flight (run BEFORE any M3 code)

1. **Confirm production state stable** (5 min after this session ends should be fine):
   ```bash
   curl -sS -o /dev/null -w "%{http_code}\n" https://live-transcription-fastapi-production.up.railway.app/health
   # Expected: 200
   ```
2. **Verify M1+M2 production state** (sanity check that nothing rolled back):
   - Use Neon MCP `run_sql` against project `super-glitter-11265514` to confirm:
     - `SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='emails' AND column_name LIKE 'local_enrichment%';` → 2
     - `SELECT 1 FROM information_schema.tables WHERE table_name='pending_interactions';` → 1 row
3. **SHARED-TENANT-COLLISION CHECK (LOCKED-17)**:
   ```bash
   ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
   ```
   Any file modified in last hour = concurrent agent hazard. M3 is non-destructive scope so this is informational only.
4. **eq-email-pipeline state check**:
   ```bash
   git -C /Users/peteroneil/eq-email-pipeline status      # should be clean on main
   git -C /Users/peteroneil/eq-email-pipeline log --oneline -3
   # Expected: top is 033626a docs(plan): pending_interactions design...
   ```

### M3 — eq-email-pipeline EmailPromoted subscriber (2-3 days, medium risk)

**Step 0 — Resolve open question #2 (subscription pattern)**:

Inspect existing inbound webhook handlers in `/Users/peteroneil/eq-email-pipeline`:
- Look for existing SQS subscriber patterns.
- Check `src/providers/` and `src/api/` for inbound event handling.
- If SQS-from-EventBridge: extend the existing subscriber for the new `EmailPromoted` detail-type.
- If direct EventBridge: set up a new subscriber (Lambda or direct HTTP receiver).
- Document the choice in the M3 PR description.

**Step 1 — Helper functions in `src/persistence/postgres.py`**:

```python
async def try_claim_local_enrichment(email_id: UUID) -> bool:
    """Atomic compare-and-set: claim the email for enrichment.

    UPDATE emails
    SET local_enrichment_started_at = NOW()
    WHERE id = $email_id
      AND local_enrichment_completed_at IS NULL
      AND (local_enrichment_started_at IS NULL
           OR local_enrichment_started_at < NOW() - INTERVAL '5 minutes')
    RETURNING id;

    Returns True if RETURNING produced a row (we got the claim).
    Returns False if no row (someone else has it, or it's already done).
    """

async def mark_local_enrichment_completed(email_id: UUID) -> None:
    """UPDATE emails SET local_enrichment_completed_at = NOW() WHERE id = $email_id"""

async def fetch_email_by_interaction_id(tenant_id: UUID, interaction_id: UUID) -> EmailRow | None:
    """SELECT * FROM emails WHERE tenant_id = $1 AND interaction_id = $2 LIMIT 1"""

async def fetch_raw_interaction(tenant_id: UUID, interaction_id: UUID) -> RawInteractionRow | None:
    """SELECT * FROM raw_interactions WHERE tenant_id = $1 AND interaction_id = $2 LIMIT 1"""

async def fetch_contacts_for_interaction(tenant_id: UUID, interaction_id: UUID) -> dict[str, UUID]:
    """Returns {email: contact_id} for contacts linked to this interaction's
    email-summary. JOIN interaction_summaries (summary_type='email') →
    interaction_contact_links → contacts."""
```

**Step 2 — EmailPromoted handler per plan §6.2**:

```python
async def handle_email_promoted(event: EmailPromotedEvent) -> None:
    tenant_id = event.tenant_id
    interaction_id = event.interaction_id
    account_id = event.account_id

    # Step 0 — two-layer idempotency guard
    email_row = await pg.fetch_email_by_interaction_id(tenant_id, interaction_id)
    if email_row is None:
        logger.warning("EmailPromoted received for unknown interaction_id; possible event drift", interaction_id=interaction_id)
        return  # consumer-side MERGE: don't fail, log and skip
    if email_row.local_enrichment_completed_at is not None:
        logger.info("Skip EmailPromoted re-delivery — already enriched", interaction_id=interaction_id)
        return
    if (
        email_row.local_enrichment_started_at is not None
        and email_row.local_enrichment_started_at > datetime.utcnow() - timedelta(minutes=5)
    ):
        logger.info("Skip EmailPromoted — likely in-flight retry within 5min TTL", interaction_id=interaction_id)
        return

    claimed = await pg.try_claim_local_enrichment(email_row.id)
    if not claimed:
        logger.info("Skip EmailPromoted — claim lost to concurrent handler", interaction_id=interaction_id)
        return

    # Step 1-2: read raw_interaction + emails. thread_id already set by M2's Step 4c.
    interaction_row = await pg.fetch_raw_interaction(tenant_id, interaction_id)
    content_text = interaction_row.raw_text
    thread_id = email_row.thread_id

    # Step 3: contacts (subset; cross-queue participants may not have contacts yet)
    contact_id_map = await pg.fetch_contacts_for_interaction(tenant_id, interaction_id)

    # Step 4: branch on processing_tier
    if email_row.processing_tier == "light":
        # Light: no LLM/Neo4j/Pinecone. Just mark complete.
        await pg.mark_local_enrichment_completed(email_row.id)
        return

    # FULL TIER — Steps 5-8 — see plan §6.2 for the full pipeline (Neo4j build_skeleton +
    # write_flesh + LLM extract + headline/summary on Neo4j Interaction node + Pinecone
    # embed + thread summary update).

    # Step 9: mark complete LAST, after all writes succeed
    await pg.mark_local_enrichment_completed(email_row.id)
```

**Important per plan §3.5**: headline + summary live on Neo4j `Interaction.headline` / `Interaction.summary` ONLY. Do NOT add `headline` / `summary` columns to the `emails` Postgres table.

**Step 3 — Tests**:

- **Unit tests** for each new helper in `src/persistence/postgres.py`:
  - `try_claim_local_enrichment`: claim succeeds when both columns NULL; claim succeeds when `_started_at` is > 5min old; claim fails when `_completed_at` is set; claim fails when `_started_at` is < 5min old.
  - `mark_local_enrichment_completed`: sets the column to NOW().
  - `fetch_email_by_interaction_id`: returns the row; returns None when not found.
  - `fetch_raw_interaction`: returns the row; returns None when not found.
  - `fetch_contacts_for_interaction`: returns the dict; respects the email-summary filter (NOT meeting summaries).
- **Integration test** for idempotency:
  - Synthesize a cold-inbound email path (manually INSERT into `pending_interactions`).
  - Manually fire an EmailPromoted event into the subscriber.
  - Verify Neo4j has exactly one Interaction node, Pinecone has one vector, `message_count` incremented exactly once.
  - Re-fire the same event. Verify: no new Neo4j nodes, no new Pinecone vectors, `message_count` unchanged.

**Step 4 — Codex review BEFORE merge** per LOCKED-10:

```bash
codex review --base main -c 'model_reasoning_effort="medium"' --enable web_search_cached
```

4-round soft cap. Extend if real P1s keep surfacing (M2 ran 7 rounds with real findings through R6).

**Step 5 — Open M3 PR**; surface to user for approval before merge.

### M4 + M5 — DEFERRED to separate sessions

Per plan §12: M4 flips the switch on cold-inbound capture; M5 verifies end-to-end. Both warrant their own pre-merge ritual + production canary. Do NOT continue past M3 in this session unless context budget is genuinely generous + user explicitly approves.

---

## LOCKED decisions (18 total; do NOT re-litigate)

1. DBOS substrate.
2. Single Railway replica + `executor_id=RAILWAY_REPLICA_ID`.
3. EventBridge Path A with `source="com.yourapp.transcription"` and closed `INTERACTION_TYPE_TO_DETAIL_TYPE` lookup.
4. Workflow ID = `f"queue-{queue_id}:approval-{approval_attempt_id}"`.
5. `/approve` reserves synchronously then enqueues.
6. Option B test infrastructure (test-tenant scoping in prod Neon) + `@pytest.mark.requires_db_write` + `RUN_DESTRUCTIVE_TESTS=1`.
7. **Two hard rules** — no contact / no interaction without account anchor.
8. SQLAlchemy 2.0.49 `CAST(:name AS uuid)` form.
9. Materialization REQUIRES Lane 2 raw_interactions before materializing.
10. Codex review BEFORE merging (4-round soft cap; extendable when real P1s keep surfacing — M2 ran 7 rounds with real findings through R6, R7 CLEAN).
11. Per-batch user confirmation for destructive ops on shared test tenant.
12. Transcripts: frontend forces anchor; emails: backend handles via pending state.
13. Recipient-as-anchor REJECTED for emails.
14. Pending-interactions pattern (Approach C).
15. Lean payload + typed columns for pending_interactions schema.
16. Path B full reprocess on promote via EventBridge `EmailPromoted` event.
17. Shared-tenant collision protocol: pre-flight `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl`.
18. Codex multi-round: `--commit HEAD` past ~1500 lines; `model_reasoning_effort=medium` default.

---

## Acknowledged V1 limitations (NOT regressions; documented + bounded)

1. **Personal/internal anchor cold-inbound → log+drop.** V2 roadmap: audit log table.
2. **Neo4j build_skeleton + write_flesh partial-retry corruption.** Mitigation: M3 implements the 2-layer guard (atomic CAS + 5-min soft TTL). V2 roadmap: MERGE patterns + edge-count thread counters.
3. **`upsert_thread` known race** — FIXED in M2 for the workflow promote path (atomic INSERT...ON CONFLICT DO UPDATE inlined). M4 will fix it for eq-email-pipeline's orchestrator known-account path too.
4. **Legacy per-signal loop hardcodes `summary_type='meeting'`** — for re-pointed email signals (M2 4-pre-1) it creates a duplicate 'meeting' summary alongside the existing 'email' summary. Cosmetic data inconsistency, NOT functionally broken. Future cleanup: type-aware legacy loop.

---

## Pre-existing CI gotcha to surface (NOT M3 scope)

- **eq-frontend `live-db` workflow** is missing `DIRECT_DATABASE_URL`. PR #392 had this check fail (`Error: Environment variable not found: DIRECT_DATABASE_URL`). The Vercel preview deploy IS the meaningful migration validation and it passed; main branch isn't protected so the merge succeeded. **Any future eq-frontend PR with a Prisma migration will hit the same failure** until the workflow is updated to add the env var from GitHub secrets. Worth fixing in a small follow-up PR in eq-frontend — but NOT M3 scope.

---

## AWS infrastructure for M3 — must be created before M3 deploys

Verified end-of-prior-session via `aws events list-rules` against account `211125681610`:

- **NO `EmailPromoted` EventBridge rule exists.** Existing rules cover EnvelopeV1 detail-types only (e.g., `eq-structured-graph-rule`, `action-item-graph-rule`).
- **AWS account:** `211125681610`.
- **Default event bus ARN:** `arn:aws:events:us-east-1:211125681610:event-bus/default`.
- **Existing route-to-sqs pattern** (use as template — see `route-summary-generated-to-sqs`): a single rule with one target ARN of an SQS queue.
- **SQS naming convention** in this account: `eq-{service}-queue` + `eq-{service}-dlq`. Following the convention, M3 needs `eq-email-promoted-queue` + `eq-email-promoted-dlq`.

### AWS resources M3 needs (recommended order)

1. **Create the DLQ first:**
   ```
   aws sqs create-queue --queue-name eq-email-promoted-dlq --region us-east-1
   ```

2. **Create the main queue with DLQ redrive policy:**
   ```
   aws sqs create-queue --queue-name eq-email-promoted-queue --region us-east-1 \
     --attributes '{
       "MessageRetentionPeriod": "1209600",
       "VisibilityTimeout": "300",
       "RedrivePolicy": "{\"deadLetterTargetArn\":\"arn:aws:sqs:us-east-1:211125681610:eq-email-promoted-dlq\",\"maxReceiveCount\":\"5\"}"
     }'
   ```
   - 1209600s = 14 days retention.
   - 300s = 5min VisibilityTimeout: matches the 2-layer guard's soft TTL so an in-flight handler can complete (or hit Layer 2) before re-delivery.
   - maxReceiveCount=5 = 5 delivery attempts before DLQ.

3. **Configure SQS queue policy to allow EventBridge to send messages:**
   ```
   aws sqs set-queue-attributes --queue-url https://sqs.us-east-1.amazonaws.com/211125681610/eq-email-promoted-queue \
     --attributes Policy='{
       "Version": "2012-10-17",
       "Statement": [{
         "Effect": "Allow",
         "Principal": {"Service": "events.amazonaws.com"},
         "Action": "sqs:SendMessage",
         "Resource": "arn:aws:sqs:us-east-1:211125681610:eq-email-promoted-queue",
         "Condition": {
           "ArnEquals": {
             "aws:SourceArn": "arn:aws:events:us-east-1:211125681610:rule/route-email-promoted-to-sqs"
           }
         }
       }]
     }'
   ```

4. **Create the EventBridge rule:**
   ```
   aws events put-rule --name route-email-promoted-to-sqs --region us-east-1 \
     --event-bus-name default \
     --event-pattern '{"source":["com.yourapp.transcription"],"detail-type":["EmailPromoted"]}' \
     --description "Routes EmailPromoted events to the eq-email-pipeline subscriber queue" \
     --state ENABLED
   ```

5. **Attach the queue as the rule's target:**
   ```
   aws events put-targets --rule route-email-promoted-to-sqs --region us-east-1 \
     --targets '[{
       "Id": "send-to-email-promoted-queue",
       "Arn": "arn:aws:sqs:us-east-1:211125681610:eq-email-promoted-queue"
     }]'
   ```

6. **Grant eq-email-pipeline's IAM principal SQS read perms** (the Railway service's AWS creds). Add to the existing IAM policy used by eq-email-pipeline:
   ```
   {
     "Effect": "Allow",
     "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:GetQueueUrl"],
     "Resource": "arn:aws:sqs:us-east-1:211125681610:eq-email-promoted-queue"
   }
   ```

### Verification commands (run after setup, before deploying M3 code)

```
aws events describe-rule --name route-email-promoted-to-sqs --region us-east-1
aws events list-targets-by-rule --rule route-email-promoted-to-sqs --region us-east-1
aws sqs get-queue-attributes --queue-url https://sqs.us-east-1.amazonaws.com/211125681610/eq-email-promoted-queue --attribute-names All --region us-east-1
```

### End-to-end smoke test (optional, after setup)

Manually fire a synthetic EmailPromoted event and confirm it lands in the queue:

```
aws events put-events --region us-east-1 \
  --entries '[{
    "Source": "com.yourapp.transcription",
    "DetailType": "EmailPromoted",
    "Detail": "{\"tenant_id\":\"11111111-1111-4111-8111-111111111111\",\"interaction_id\":\"00000000-0000-4000-8000-000000000001\",\"account_id\":\"00000000-0000-4000-8000-000000000002\",\"queue_id\":\"00000000-0000-4000-8000-000000000003\",\"promoted_at\":\"2026-05-17T23:00:00Z\"}",
    "EventBusName": "default"
  }]'

# Verify in queue:
aws sqs receive-message --queue-url https://sqs.us-east-1.amazonaws.com/211125681610/eq-email-promoted-queue --region us-east-1
```

### M3's subscriber pattern (decided this prior session)

eq-email-pipeline currently has **NO existing SQS-from-EventBridge OR direct-EventBridge subscriber pattern** — confirmed by source inspection of `src/main.py`, `src/api/webhooks.py`, `src/pipeline/`. Inbound emails come via Gmail/Outlook OAuth pull (background polling tasks in `main.py` lifespan: `email_task` + `calendar_task`). `src/pipeline/emit.py` has `EventPublisher` for OUTBOUND EventBridge writes only.

**M3 introduces a NEW background task** in `main.py`'s lifespan (mirroring `email_task` and `calendar_task` patterns) that long-polls the `eq-email-promoted-queue` SQS queue and dispatches messages to the handler. Reference implementation outline:

```python
# In src/main.py lifespan, after email_task/calendar_task:
email_promoted_subscriber = EmailPromotedSubscriber(
    pg=pg_client,
    neo4j_driver=neo4j_driver,
    openai_client=openai_client,
    embedder=pinecone_embedder,
    queue_url=settings.email_promoted_queue_url,
    region=settings.aws_region,
)
email_promoted_task = asyncio.create_task(email_promoted_subscriber.run_polling())
```

The `EmailPromotedSubscriber` class lives in a new `src/pipeline/email_promoted_subscriber.py`. Its `run_polling()` method:

1. Long-polls SQS (`receive_message` with `WaitTimeSeconds=20`).
2. For each message:
   - Parse the EventBridge envelope (`{"version":"0","id":"...","source":"com.yourapp.transcription","detail-type":"EmailPromoted","detail":{...}}`).
   - Extract the `detail` payload → `EmailPromotedEvent`.
   - Call `handle_email_promoted(event)` (the handler implementing plan §6.2).
   - On success: `sqs.delete_message(...)`.
   - On exception: log + DO NOT delete → SQS retries up to 5 times → DLQ.

The handler does NOT need its own retry logic — SQS + handler's two-layer idempotency guard cover it. The 5-min VisibilityTimeout ≈ the 5-min `local_enrichment_started_at` soft TTL, so the guard correctly skips re-deliveries.

### Why SQS-from-EventBridge over direct EventBridge

1. **At-least-once + DLQ semantics** for free, without bespoke retry logic in the subscriber.
2. **Matches the existing eq-email-pipeline polling pattern** (Gmail/Outlook pull). No new architectural shape.
3. **Decoupled from EventBridge retry budget** — eq-email-pipeline can be down for up to 14 days (queue retention) and recover.
4. **Same operational shape** as Phase 1.5 M3's downstream consumers (action-item-graph, eq-structured-graph-core) which use SQS-from-EventBridge.

### Settings additions (eq-email-pipeline)

Add to `src/models/config.py`:

```python
class Settings(BaseSettings):
    # ... existing fields ...
    email_promoted_queue_url: str = Field(
        default="https://sqs.us-east-1.amazonaws.com/211125681610/eq-email-promoted-queue",
        alias="EMAIL_PROMOTED_QUEUE_URL",
    )
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
```

Railway env vars to set: `EMAIL_PROMOTED_QUEUE_URL`, `AWS_REGION`, plus AWS creds (likely already set from the existing EventBridge publish flow).

---

## Production credentials + IDs (load-bearing reference)

- **Neon Postgres (eq-dev):** project `super-glitter-11265514`, branch `production`, database `neondb`. Direct connection (no `-pooler`) for `DBOS_SYSTEM_DATABASE_URL`.
- **Test tenant:** `11111111-1111-4111-8111-111111111111`. All test data. Per LOCKED-11.
- **Test user (FK target):** `b0000000-0000-4000-8000-000000000002`.
- **Railway FastAPI:** project `847cfa5a-b77c-4fb0-95e4-b20e8773c23e`, service `59a69f3d-9a24-4041-942a-891c4a81c5fb`, env `e4c5ec15-1931-4632-9e58-92d9c6be4261`, URL `https://live-transcription-fastapi-production.up.railway.app`. M2 deployment SHA `756575d7` is `809679fc-057f-4580-984a-093d01552bb0` (SUCCESS).
- **Railway eq-agent-action-core:** URL `https://eq-agent-action-core-production.up.railway.app`, service `3036ea0f-afc9-4bc4-889d-c98617d81e96`.
- **eq-email-pipeline:** `/Users/peteroneil/eq-email-pipeline` (NOT under EQ-CORE/). Main HEAD `033626a` as of M2 ship.
- **eq-frontend:** `/Users/peteroneil/eq-frontend`. M1 merged at `de586bbc` on origin/main.
- **Internal JWT:** HS256, `INTERNAL_JWT_SECRET`, `iss=eq-frontend`, `aud=eq-backend`.
- **AWS:** EventBridge bus `default` (configurable via `EVENTBRIDGE_BUS_NAME`); `AWS_REGION=us-east-1`. **`EmailPromoted` rule must be configured before M3 produces handler load** — M3's subscriber depends on the rule routing the event to it. Operator task (Terraform / AWS console). Pattern: `Source=com.yourapp.transcription + DetailType=EmailPromoted`, target = eq-email-pipeline's subscriber.
- **Neo4j:** Aura `c6171c63`, URI `neo4j+s://c6171c63.databases.neo4j.io`.

---

## Open questions deferred to execution

1. **eq-email-pipeline EventBridge subscription pattern** (plan §14 #2) — resolve during M3 implementation. Inspect existing inbound webhook handlers; document the choice in M3 PR.
2. **Light tier handler behavior** (plan §14 #4) — confirm during M3 whether light-tier emails write any summaries today. If no, handler is a complete no-op for light tier.
3. **EmailPromoted DLQ + observability** (plan §14 #5) — operations setup, separate from plan.
4. **Backfill of historical dropped emails** (plan §14 #6) — confirm in M5 that no backfill needed (test data only).
5. **Queue UI integration** (plan §14 #7) — defer to eq-frontend session.

---

## Stop conditions (hard — surface to user)

- `/context-restore` returns NO_CHECKPOINTS.
- MEMORY.md status isn't `PHASE_1_EMAIL_PIPELINE_M1_M2_DEPLOYED_M3_NEXT`.
- Production state has rolled back (M1 migration reverted, M2 code reverted) — verify Neon + /health at session start. If reverted, STOP and surface.
- The plan claims something about existing eq-email-pipeline code that doesn't match what M3 actually finds. STOP, surface, revise plan ONLY after user explicit approval.
- M3's Codex pre-merge review surfaces a P1 you can't resolve in one revision round (after the round-4 false-positive recognition heuristic).
- LOCKED-17 collision check shows a concurrent agent in another repo within the last hour AND the work is destructive (M3 alone is non-destructive; would matter for M5 canary).
- You're tempted to revise the plan doc instead of surfacing a plan issue — STOP, surface the issue.

---

## Handoff artifacts from the prior session

- **M1 merged**: https://github.com/oneilstokeseqrm/eq-frontend/pull/392 → `de586bbc` (3 Codex rounds, CLEAN at R3, 3 findings resolved).
- **M2 merged**: https://github.com/oneilstokeseqrm/live-transcription-fastapi/pull/19 → `756575d7` (7 Codex rounds, CLEAN at R7, 11 substantive findings resolved).
- **Comprehensive checkpoint**: `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/20260517-164251-phase-1-email-pipeline-m1-m2-shipped-m3-next.md` (saved pre-merge; merge happened shortly after).
- **The plan (unchanged)**: `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md` (eq-email-pipeline:`033626a`).
- **Next-session prompt** (paste-ready): `docs/superpowers/specs/2026-05-17-evening-m3-next-session-prompt.md`.

---

## Session lessons codified

1. **Cross-repo deploy coordination** is non-optional when a schema migration relaxes a constraint that downstream `ON CONFLICT` clauses reference. The plan-writing session can miss this because it focuses on the new schema, not on existing SQL depending on the old schema. **Rule**: before locking a plan that drops or relaxes a UNIQUE constraint, GREP all repos for `ON CONFLICT (<constraint cols>)` SQL referencing it.

2. **Codex round-N convergence pattern** is a real signal: when findings remain non-redundant + decrease in severity across rounds (P1→P2→0), the design is converging and rounds 5-7 are still valuable. The 4-round soft cap is a default; extending is justified when severity is decreasing AND each round adds NEW unique findings. M2 hit this pattern — 11 substantive findings across 7 rounds, 0 redundant, R7 CLEAN.

3. **`live-db` CI workflow needs `DIRECT_DATABASE_URL`** — first Prisma migration PR after the workflow was added (PR #392) failed this check. Fix in a small follow-up PR before the next migration ships.
