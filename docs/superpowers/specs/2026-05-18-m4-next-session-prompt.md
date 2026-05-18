# Next Session Opening Prompt (2026-05-18, post-M3-DEPLOYED)

Paste the block below as the opening message of the next Claude session. Written 2026-05-18 after M3 of the Phase-1-email-pipeline cold-inbound fix was merged AND deployed (PR #9 → `85c0295`) with AWS infrastructure provisioned + Railway env var set + subscriber confirmed long-polling SQS in production. Production state: end-to-end pipeline is BUILT but DORMANT — M4 is what flips the switch and produces real `EmailPromoted` event load.

**Important:** the next session is **EXECUTION** of M4 only — implementing the eq-email-pipeline orchestrator branch to `pending_interactions` for cold-inbound unknown-business emails, plus the atomic `upsert_thread` rewrite. NOT plan revision. The plan is locked.

---

```
You're picking up the Contact Quality and Account-Anchoring Initiative —
a multi-phase data-quality project on an AI-native customer intelligence
platform.

The prior session (2026-05-18) shipped Phase-1-email-pipeline M3 —
the eq-email-pipeline EmailPromoted SQS subscriber. M3 PR #9 merged as
`85c0295` at 2026-05-18T09:34:22Z. Railway deployment `5c013fd3` reached
SUCCESS. /api/health returns 200 with all checks (postgres + neo4j +
eventbridge) OK. The subscriber is actively long-polling the eq-email-
promoted-queue SQS queue in production — but receives ZERO events until
M4 ships, because M4 is what writes to pending_interactions, which is
what makes M2's workflow emit EmailPromoted events.

The full AWS infrastructure for the consumer side was provisioned end-
to-end during M3 setup:
- SQS queue `eq-email-promoted-queue` (300s VT, 14d retention, redrive
  to DLQ after 5 attempts), URL
  https://sqs.us-east-1.amazonaws.com/211125681610/eq-email-promoted-queue
- SQS DLQ `eq-email-promoted-dlq`
- Queue policy allowing `events.amazonaws.com` SendMessage from the rule
- EventBridge rule `route-email-promoted-to-sqs` (Source
  `com.yourapp.transcription`, DetailType `EmailPromoted`) → SQS target
- IAM inline policy `SQSEmailPromotedReader` on `eq-bff-kinesis-writer`
  (the IAM principal Railway uses for eq-email-pipeline)
- Railway env var EMAIL_PROMOTED_QUEUE_URL set on the email-pipeline
  service in production

Synthetic put-events smoke test PASSED during AWS setup (event routed
to SQS with correct envelope shape). Railway IAM creds end-to-end
ReceiveMessage test PASSED. The wire is proven.

Your job THIS session is to EXECUTE M4 — the eq-email-pipeline orchestrator
branch + atomic upsert_thread rewrite. This is the milestone that FLIPS
THE SWITCH on cold-inbound capture. Implementation, NOT plan revision.

CRITICAL: this session is EXECUTION. The plan is locked at
eq-email-pipeline:033626a. If you find issues with the plan during
implementation, surface them via AskUserQuestion rather than silently
revising the plan doc.

⚠️ CRITICAL — M3 already shipped 4 of the 5 persistence helpers M4 was
originally planned to add:

The original M4 plan (§12 M4 bullet 3) lists these helpers to add to
src/persistence/postgres.py:
- mark_local_enrichment_started (M3 shipped try_claim_local_enrichment
  instead — atomic CAS via UPDATE...WHERE...RETURNING is the race-safe
  form per plan §6.2 Codex round-3 P1)
- mark_local_enrichment_completed
- fetch_email_by_interaction_id
- fetch_raw_interaction
- fetch_contacts_for_interaction

M3 shipped all 5 of these. They live in src/persistence/postgres.py
lines ~488-628 (after update_thread_summary, before "Provider connection
helpers"). M4 must NOT re-add them. Pre-flight verifies they exist.

What M4 actually adds:
1. persist_pending_interaction helper in src/persistence/postgres.py
2. Atomic upsert_thread rewrite in src/persistence/postgres.py (closes
   the SELECT-then-UPSERT race; plan §6.3 V1 limitation #3)
3. Extended email_exists to UNION emails + pending_interactions
4. §4.1 decision branch in src/pipeline/orchestrator.py (BUSINESS →
   pending; PERSONAL/INTERNAL → log+drop)
5. Pre-allocate interaction_id at top of process_email (§4.3)
6. §4.2 pending path inside process_email
7. insert_email signature change (caller-provided interaction_id)
8. 6 integration tests + unit tests per plan §10.2

Before doing anything else, follow these steps IN ORDER:

1. Run `/context-restore`. You should find a checkpoint titled
   "phase-1-email-pipeline-m3-deployed-m4-next" dated 2026-05-18.
   Load it. If /context-restore returns NO_CHECKPOINTS or a different
   latest checkpoint, STOP and surface — that's a sync gap.

2. Read MEMORY.md (auto-loads). Confirm project status reads
   PHASE_1_EMAIL_PIPELINE_M1_M2_M3_DEPLOYED_M4_NEXT. If anything else
   (e.g., still says "M3_NEXT"), STOP and surface — memory state may
   have rolled back.

3. READ THE WAYFINDING DOC:
   /Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/
     superpowers/specs/NEXT-SESSION-START-HERE.md

   Sections "SESSION SCOPE FOR THE NEXT SESSION", "CRITICAL — what
   already shipped + verified deployed" (with merge SHAs + AWS infra
   + Railway env var inventory), "Production state verified end-of-
   prior-session", "⚠️ CRITICAL — M3 already shipped 4 of the 5
   persistence helpers", "Execution sequence — M4" (with concrete
   helper signatures + pseudocode + caller verification list), "21
   LOCKED decisions" (with the 3 new from M3), "Acknowledged V1
   limitations" (5 items including the new #5 from M3), and "Stop
   conditions" are load-bearing.

4. READ THE PLAN DOC (mandatory, ~20-25 min focused on M4 sections):
   /Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/
     2026-05-17-pending-interactions-cold-inbound-fix.md

   Especially:
   - §0 Revision history (v1 → v4 across 4 plan-writing Codex rounds)
   - §1.2 Recharacterization (this is finishing Phase 1, not new scope)
   - §4 Orchestrator changes — PRIMARY M4 REFERENCE
   - §4.1 The decision point (PERSONAL/INTERNAL/BUSINESS branching)
   - §4.2 The pending-business path (transaction + helpers)
   - §4.3 Pre-allocate interaction_id design rationale
   - §4.4 Thread handling for pending interactions (defer to promotion)
   - §4.5 Light vs Full tier — preserved through promotion
   - §4.6 Signal flush is hoisted, NOT removed
   - §5 (workflow promote step — already shipped by M2; read for context
     on what consumes M4's pending_interactions writes)
   - §6 (handler — already shipped by M3; read for context on the
     two-layer guard + HandlerOutcome enum)
   - §6.3 Idempotency contract — honest accounting
   - §7 Cross-repo migration ordering — M4 = Phase 4 (FLIPS THE SWITCH)
   - §8.1 Re-open after Ignore
   - §8.2 Mid-promotion crash (idempotency via archived_at IS NULL)
   - §8.6 Cold-inbound with multiple unknown domains (revised — round 1)
   - §10.2 Integration tests (in eq-email-pipeline)
   - §11 Acceptance invariants — the ship-when-true checklist
   - §12 M4 milestone scope
   - §16 What this plan does NOT do

5. READ THE M3 PR DESCRIPTION for the comprehensive narrative:
   https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/9

   Especially the "Idempotency contract" section (10-min DB-TTL > 5-min
   SQS VT rationale), the HandlerOutcome enum design, and the Codex
   trajectory summary.

6. READ M3 CODE to understand what M4 inherits:
   - src/persistence/postgres.py lines 488-628 (the 5 M3 helpers).
     M4 must NOT re-add these.
   - src/pipeline/email_promoted_subscriber.py (the M3 handler).
     Read HandlerOutcome enum docstring for the SQS contract.
   - src/main.py lifespan additions — confirm M4 doesn't need to
     touch this (orchestrator is already wired).

7. Read existing code M4 modifies:
   - src/pipeline/orchestrator.py:1-200 (process_email head + §4.1 decision)
   - src/persistence/postgres.py:288-356 (upsert_thread — to be rewritten)
   - src/persistence/postgres.py:362-375 (email_exists — to be extended)
   - src/persistence/postgres.py:189-282 (insert_email — signature change)

8. Verify pre-flight state (run BEFORE any M4 code):

   a. Production /health (both services):
      curl -sS -o /dev/null -w "live-fastapi: %{http_code}\n" \
        https://live-transcription-fastapi-production.up.railway.app/health
      curl -sS -o /dev/null -w "eq-email-pipeline: %{http_code}\n" \
        https://email-pipeline-production.up.railway.app/api/ping
      curl -sS https://email-pipeline-production.up.railway.app/api/health
      Expected: all 200; eq-email-pipeline checks all "ok".

   b. M3 helpers still in code:
      grep -nE "async def try_claim_local_enrichment|async def \
mark_local_enrichment_completed|async def fetch_email_by_interaction_id\
|async def fetch_raw_interaction|async def fetch_contacts_for_interaction" \
        /Users/peteroneil/eq-email-pipeline/src/persistence/postgres.py
      Expected: 5 matches.

   c. EMAIL_PROMOTED_QUEUE_URL set on Railway:
      Use mcp__railway__list_service_variables (project
      f7d26745-7722-4946-aa3f-9dfc3664426f, service
      92d55588-e548-4188-a179-1d3fa9ea38d2, env
      845e3772-e146-439f-b5f5-cbdfcab6087c). Expect
      https://sqs.us-east-1.amazonaws.com/211125681610/eq-email-promoted-queue.

   d. Production schema (Neon MCP run_sql against project
      super-glitter-11265514, database neondb):
      SELECT
        (SELECT COUNT(*) FROM information_schema.tables WHERE table_name='pending_interactions') AS p_table,
        (SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='emails' AND column_name LIKE 'local_enrichment%') AS local_cols,
        (SELECT COUNT(*) FROM pg_indexes WHERE indexname='interaction_summaries_tenant_id_interaction_id_summary_type_key') AS comp_unique,
        (SELECT COUNT(*) FROM pg_indexes WHERE indexname='interaction_summaries_interaction_id_key') AS old_unique_zero,
        (SELECT COUNT(*) FROM pg_indexes WHERE indexname='raw_interactions_tenant_id_interaction_id_key') AS raw_comp;
      Expected: {p_table:1, local_cols:2, comp_unique:1, old_unique_zero:0, raw_comp:1}

   e. email_threads.(tenant_id, thread_key) UNIQUE index exists
      (required for M4's atomic upsert_thread rewrite):
      SELECT indexname FROM pg_indexes WHERE tablename='email_threads'
        AND (indexdef LIKE '%(tenant_id, thread_key)%' OR indexdef LIKE '%(tenant_id,thread_key)%');
      Expected: at least one matching index. If missing, STOP and surface
      — M4 needs a coordinated eq-frontend Prisma migration FIRST.

   f. eq-email-pipeline git state:
      git -C /Users/peteroneil/eq-email-pipeline status
      Expected: clean on main.
      git -C /Users/peteroneil/eq-email-pipeline log --oneline -3
      Expected top: 85c0295 M3: EmailPromoted SQS subscriber...

   g. SHARED-TENANT-COLLISION CHECK (LOCKED-17):
      ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
      Any file modified in last hour = pause + ask user. M4 is mostly
      non-destructive (writes to test tenant only via tests) but the
      upsert_thread rewrite touches a shared-code-path table.

   If any of these fail, STOP and surface — production may have rolled
   back.

9. After reading, briefly confirm in one paragraph: where the prior
   session left off + your M4 implementation approach for this session
   + that you understand this is EXECUTION (not plan revision) + that
   you will NOT re-add the 5 M3 persistence helpers.

10. EXECUTE — M4 (eq-email-pipeline orchestrator branch + atomic
    upsert_thread rewrite).

    Per plan §4 + §12, in this order (each step in the wayfinding
    doc has more detail):

    Step 0: Open branch phase-1-email-pipeline/m4-orchestrator-flip-switch
    Step 1: Atomic upsert_thread rewrite (highest-risk change; verify
            all callers behave identically; concurrent-call test
            required)
    Step 2: Extend email_exists to UNION emails + pending_interactions
            (with `archived_at IS NULL` on the pending side so
            promoted/expired rows don't block retries)
    Step 3: Add persist_pending_interaction helper (takes connection,
            not pool; participates in caller's transaction)
    Step 4: Pre-allocate interaction_id at top of process_email;
            change insert_email signature (with backward-compat
            default for legacy callers)
    Step 5: §4.1 decision branch (PERSONAL/INTERNAL log+drop;
            BUSINESS → fall through to pending path)
    Step 6: §4.2 pending path (single transaction: queue entry
            reopen-or-upsert → persist_pending_interaction → flush
            signal proposals → return pending_account_approval).
            Do NOT call upsert_thread, insert_email, build_skeleton,
            extract, write_flesh, embed_and_upsert, or
            update_thread_summary.
    Step 7: Tests per plan §10.2 (6 integration cases + helper units +
            atomic upsert_thread concurrent-call test)
    Step 8: Codex review BEFORE merge (LOCKED-10). 4-round soft cap;
            extend per round-N convergence (severity decreasing +
            non-redundant findings = real). Past ~1500 cumulative
            lines, switch to --commit HEAD per LOCKED-18.
    Step 9: Open M4 PR; surface to user for merge approval.
            PR description must call out:
            - §4.1 + §4.2 branching design
            - Atomic upsert_thread rewrite + caller verification
            - email_exists UNION extension (with archived_at filter)
            - insert_email signature change + backward-compat default
            - That M4 FLIPS THE SWITCH — first time real cold-inbound
              emails will create pending rows in production

    STOP after M4. M5 (production E2E + rollback drill per plan §10.3
    + §10.4) is a separate session — it's the verification milestone
    that signs off the whole Phase-1-email-pipeline initiative.

11. PRE-MERGE RITUAL (per LOCKED-10 + LOCKED-18):
    codex review --base main -c 'model_reasoning_effort="medium"' \
      --enable web_search_cached
    Past ~1500 cumulative lines: codex review --commit HEAD ...
    4-round soft cap. Extend when real P1s keep surfacing.

12. SHARED-INFRASTRUCTURE-COLLISION PROTOCOL (LOCKED-17):
    - LAYER 1: ls ~/.claude/projects/-Users-peteroneil-*/*.jsonl.
      Any file modified in last hour = pause + ask. M4 is mostly
      non-destructive; running integration tests on the shared test
      tenant warrants confirmation.
    - LAYER 2: per-action confirmation before ANY destructive op on
      the shared test tenant. Tests in §10.2 write but should clean
      up after themselves (per @pytest.mark.requires_db_write +
      RUN_DESTRUCTIVE_TESTS=1 convention).

13. POST-MERGE — confirm M4 deploy succeeds:
    - Railway redeploys eq-email-pipeline with M4 code.
    - /api/health 200 with all checks ok.
    - Check SQS queue depth via aws sqs get-queue-attributes — expect
      0 initially (no cold-inbounds yet; the switch is flipped but
      no real event has arrived yet).
    - The NEXT real cold-inbound from an unknown business sender to
      a connected mailbox WILL trigger the full pipeline:
        cold email arrives → orchestrator §4.1 → pending_interactions
        row + queue entry created → admin /approve → workflow
        promotes → EmailPromoted fires → M3 subscriber processes →
        Neo4j + Pinecone writes → complete.

14. End-of-session: /context-save with title indicating M4 status
    (e.g., "phase-1-email-pipeline-m4-shipped-m5-next" or
    "phase-1-email-pipeline-m4-in-progress" if not complete).
    Rewrite NEXT-SESSION-START-HERE.md for M5 scope. Write a new
    dated next-session-prompt.md following the depth + structure of
    THIS prompt. Update MEMORY.md status string. Codify any new
    session lessons in tasks/lessons.md.

ANTI-ANCHORING — 21 LOCKED decisions exist; full list in
NEXT-SESSION-START-HERE.md "LOCKED decisions" section. Most load-bearing
for M4:

(7)  Two hard rules — no contact without account anchor; no interaction
     without account anchor. M4's §4.1 branching enforces this for
     cold-inbound (pending state IS the transient queue-resolution
     exception per the design doc).
(9)  Materialization REQUIRES Lane 2 raw_interactions before
     materializing. M4 writes to pending_interactions; M2's workflow
     promote step (already deployed) writes raw_interactions during
     promotion BEFORE emit. Order preserved.
(10) Codex review BEFORE merging (4-round soft cap; round-N convergence
     pattern: severity-decrease + non-redundant findings → continue).
(14) Pending-interactions pattern (Approach C). M4 IS the producer side.
(15) Lean payload + typed columns. M4's persist_pending_interaction
     writes the typed columns; the pending_interactions table has no
     JSONB blob field for the email body — content_text goes in a
     dedicated TEXT column.
(16) Path B (full reprocess on promote) via EventBridge EmailPromoted.
     M4 produces the pending rows that M2's workflow promotes, which
     triggers the M3 subscriber.
(17) Shared-tenant collision pre-flight.
(18) Codex multi-round defaults: --commit HEAD past ~1500 lines;
     model_reasoning_effort=medium default.
(19) SQS-from-EventBridge subscription pattern (M3 shipped this;
     M4 produces events that flow through it).
(20) DB CAS TTL strictly > SQS VisibilityTimeout (10 min vs 5 min).
(21) HandlerOutcome tri-state enum {COMPLETE, PERMANENT_SKIP,
     TRANSIENT_SKIP} (M3 shipped this; affects how M4's pending writes
     interact with the M3 handler's idempotency contract).

ACKNOWLEDGED V1 LIMITATIONS (documented in plan; NOT regressions):

1. Personal/internal anchor cold-inbound → log+drop. Reason: queue is
   for unknown businesses to become accounts; personal/internal don't
   fit. V2 roadmap: audit log table. M4's §4.1 PERSONAL/INTERNAL
   branches ARE this V1 behavior — explicit + categorized.
2. Neo4j build_skeleton + write_flesh partial-retry corruption.
   Mitigation: M3 implements the 2-layer guard (atomic CAS + 10-min
   soft TTL > 5-min SQS VT). M4 inherits this; not introduced by M4.
3. upsert_thread pre-existing race — FIXED in M2 for workflow promote
   path. **M4 closes it for the orchestrator known-account path** via
   the atomic INSERT...ON CONFLICT DO UPDATE rewrite.
4. Legacy per-signal loop hardcodes summary_type='meeting'. For
   re-pointed email signals (M2 4-pre-1) it creates a duplicate
   'meeting' summary alongside the existing 'email' summary. Cosmetic
   data inconsistency, NOT functionally broken — downstream filters
   by summary_type='email' get the correct link from M2's Step 5
   batch. Future cleanup: type-aware legacy loop.
5. build_skeleton CREATE fallback for missing internet_message_id —
   extends limitation #2 to the case where MERGE-on-internet_message_id
   falls back to CREATE on missing header. Same bound (2-layer guard),
   same V2 roadmap. NOT introduced by M3 or M4 — orchestrator hot path
   has the same property.

VERIFIED CROSS-REPO STATE (2026-05-18, end of prior session)

- M1 merged + deployed: eq-frontend de586bbc on origin/main; Vercel
  prisma migrate deploy succeeded; Neon production schema reflects all
  M1 changes (verified by direct query).
- M2 merged + deployed: live-transcription-fastapi 756575d7 on
  origin/main; Railway deployment 809679fc Status=SUCCESS; /health 200.
- M3 merged + deployed: eq-email-pipeline 85c0295 on origin/main;
  Railway deployment 5c013fd3 Status=SUCCESS; /api/health 200 with all
  checks ok; subscriber long-polling SQS with EMAIL_PROMOTED_QUEUE_URL
  set.
- AWS infrastructure: 6/6 resources live + smoke-tested end-to-end
  (synthetic put-events → SQS routing PASSED; Railway IAM creds
  end-to-end ReceiveMessage PASSED).
- eq-frontend live-db CI workflow MISSING DIRECT_DATABASE_URL env var
  (pre-existing CI gotcha from M1; Vercel preview deploy IS the
  meaningful migration validation). Carry-forward to a future
  follow-up PR; NOT M4 scope.

UNFINISHED PHASE 1 + 1.5 WORK — FULL LIST

PRIMARY (this session's execution scope):
1. M4 — eq-email-pipeline orchestrator branch + atomic upsert_thread
   (4-5 days, medium-high risk; FLIPS THE SWITCH on cold-inbound
   capture).

NEXT (separate session):
2. M5 — production E2E + rollback drill per plan §10.3 + §10.4 (1-2
   days; LOCKED-17 Layer-1 check first). The verification milestone
   that signs off the Phase-1-email-pipeline initiative.

SECONDARY (other unfinished items; NOT this session):
3. Test-discipline-gaps Item 1 — audit + de-mock integration tests
   that mock lookup_account_by_domain at import level.
4. Test-discipline-gaps Item 2 — complete per-attendee branching happy
   paths in production E2E suite.
5. Test-discipline-gaps Item 3 — narrow outer except Exception: blocks
   in services/transcript_enrichment.py:399-405 and similar.
6. M3.5 outbox drop — optional Prisma migration in eq-frontend.
7. eq-frontend live-db CI workflow DIRECT_DATABASE_URL fix.
8. M2-deferred: legacy per-signal loop type-awareness (drops cosmetic
   duplicate 'meeting' summary for re-pointed email signals).
9. M2-deferred: comprehensive unit + integration tests for the new
   Step 4 promote + Step 5 batch + emit_email_promoted_events.

PHASE 2 (post-Phase-1-email-pipeline-completion; explicit stopping
point per 2026-05-15 plan):
- Neo4j build_skeleton + write_flesh MERGE-based idempotency (replaces
  V1's 2-layer guard mitigation; eliminates bounded corruption window
  + the CREATE-fallback for missing internet_message_id case).
- Personal/internal anchor audit log table (currently log+drop).
- Identity state machine for contacts (shell / emerging / partial /
  resolved / verified). The pending_interactions pattern is the
  symmetric construct for interactions; design Phase 2 with awareness.

PHASE 3 (post-Phase-2):
- Conflict resolution + multi-account history + fuzzy matching.

OPEN QUESTIONS DEFERRED TO EXECUTION (from plan §14)

1. email_exists extension exact SQL — M4 resolves. UNION ALL on
   (tenant_id, internet_message_id) between emails and
   pending_interactions, with archived_at IS NULL filter on the
   pending side. Verify column type + collation match before writing.
2. (M3 resolved §14 #2 — SQS-from-EventBridge subscription pattern.)
3. interaction_summaries.interaction_id UNIQUE constraint —
   M1 resolved (composite (tenant_id, interaction_id, summary_type)
   shipped; old single-column gone).
4. (M3 resolved §14 #4 — light tier handler is complete no-op except
   mark_local_enrichment_completed.)
5. EmailPromoted DLQ + observability — operations setup, separate from
   plan.
6. Backfill of historical dropped emails — confirm in M5 that no
   backfill needed (test data only).
7. Queue UI integration — defer to eq-frontend session.

PRODUCTION CREDENTIALS + IDS (self-contained reference)

- Neon Postgres (eq-dev): project super-glitter-11265514, branch
  production, database neondb. Direct connection (no -pooler) for
  DBOS_SYSTEM_DATABASE_URL.
- Test tenant: 11111111-1111-4111-8111-111111111111. All test data.
- Test user (FK target): b0000000-0000-4000-8000-000000000002.
- Peter's actual stokeseqrm user (for production cold-inbound flow):
  061ae392-47d5-4f04-9ea8-afa241f23555.
- Railway live-transcription-fastapi: project
  847cfa5a-b77c-4fb0-95e4-b20e8773c23e, service
  59a69f3d-9a24-4041-942a-891c4a81c5fb, env
  e4c5ec15-1931-4632-9e58-92d9c6be4261, URL
  https://live-transcription-fastapi-production.up.railway.app.
  M2 deployed as 809679fc (SUCCESS).
- Railway eq-email-pipeline: project
  f7d26745-7722-4946-aa3f-9dfc3664426f, service
  92d55588-e548-4188-a179-1d3fa9ea38d2, env
  845e3772-e146-439f-b5f5-cbdfcab6087c, URL
  https://email-pipeline-production.up.railway.app.
  M3 deployed as 5c013fd3 (SUCCESS). EMAIL_PROMOTED_QUEUE_URL set.
- Railway eq-agent-action-core: URL
  https://eq-agent-action-core-production.up.railway.app, service
  3036ea0f-afc9-4bc4-889d-c98617d81e96.
- eq-email-pipeline: /Users/peteroneil/eq-email-pipeline (NOT under
  EQ-CORE/). Main HEAD 85c0295 (post-M3 merge).
- eq-frontend: /Users/peteroneil/eq-frontend. M1 merged at de586bbc.
- Internal JWT: HS256, INTERNAL_JWT_SECRET, iss=eq-frontend,
  aud=eq-backend.
- AWS (account 211125681610, region us-east-1):
  - EventBridge bus 'default'; rule route-email-promoted-to-sqs
    (Source com.yourapp.transcription, DetailType EmailPromoted).
  - SQS eq-email-promoted-queue (URL
    https://sqs.us-east-1.amazonaws.com/211125681610/eq-email-promoted-queue),
    eq-email-promoted-dlq.
  - IAM principal eq-bff-kinesis-writer (access key
    AKIATCKASHXFGKNQ476O — same key Railway uses for eq-email-pipeline).
    Inline policies: EventBridgePutEvents, S3UploadBucketAccess,
    SQSBriefingEventsAccess, SQSEmailPromotedReader (new from M3).
- Neo4j: Aura instance c6171c63, URI
  neo4j+s://c6171c63.databases.neo4j.io.

USER POSTURE (load-bearing)

Non-developer founder. Make confident technical decisions; surface
only product/strategic decisions. Strict OSS only (no SSPL, no BSL).
Architectural correctness over short-term shortcuts. Cutting-edge
2026 AI-native patterns. NO sunk-cost preservation.

Context economy matters. M3 took 6 Codex rounds (heavy context); M4
may run similar given the upsert_thread rewrite is subtle Postgres
work. Plan for ~4-6 Codex rounds maximum unless real P1s keep
surfacing.

User explicitly REJECTED recipient-as-anchor for emails.
User explicitly LOCKED IN lean+typed schema (not JSONB).
User explicitly LOCKED IN Path B via EventBridge (not new sync API).
User explicitly LOCKED IN cross-queue link-fill-in via M2 Step 5
OR clause.
User explicitly LOCKED IN personal/internal anchor log+drop as V1
limitation.
User explicitly chose to coordinate cross-repo merges when deploy
coupling exists.

All data is test data; no production users yet. Architectural choices
CAN accept short-term limitations that would block a production-traffic
ship, but the user is reluctant to ship hacks.

User explicitly approves git push when asked; do NOT push without
asking first. Same for production-affecting Railway env var changes.

SCOPE OF THIS SESSION — EXPLICIT HARD CONSTRAINT

In scope:
- M4 (eq-email-pipeline orchestrator branch + atomic upsert_thread).

OUT of scope for this session:
- M5 (production E2E + rollback drill) — separate session per plan
  §12; the verification milestone that signs off the initiative.
- M1 / M2 / M3 fixes (all CLEAN at session end; if downstream review
  surfaces new findings, surface to user — don't silently fix).
- eq-frontend live-db CI fix (DIRECT_DATABASE_URL) — small follow-up
  PR; surface to user as a pre-existing gotcha.
- Test-discipline-gaps items, M3.5 outbox drop, queue UI work, all
  Phase 2/3 work.

If you finish M4 fast with substantial context remaining, you CAN
proceed to M5 in the same session — but treat it as a deliberate
decision, NOT an automatic progression. M5 is high-leverage signal-
gathering work that benefits from undivided attention.

STOP CONDITIONS (hard — surface to user)

- /context-restore returns NO_CHECKPOINTS.
- MEMORY.md status isn't PHASE_1_EMAIL_PIPELINE_M1_M2_M3_DEPLOYED_M4_NEXT.
- Production state has rolled back (M1 migration reverted, M2/M3 code
  reverted) — verify Neon + /health at session start. If reverted,
  STOP and surface.
- The 5 M3 persistence helpers are missing from
  src/persistence/postgres.py. (M3 may have been reverted.)
- EMAIL_PROMOTED_QUEUE_URL is unset on Railway eq-email-pipeline
  production env. (M3 deploy state may have rolled back.)
- email_threads.(tenant_id, thread_key) UNIQUE index is missing in
  production. (Requires upstream eq-frontend Prisma migration FIRST;
  do not proceed with M4 upsert_thread rewrite until in place.)
- The plan claims something about existing eq-email-pipeline code that
  doesn't match what M4 actually finds. STOP, surface, revise plan
  ONLY after user explicit approval.
- M4's Codex pre-merge review surfaces a P1 you can't resolve in one
  revision round AND it's not in the known-FP family (upstream schema,
  hypothetical TZ flip).
- LOCKED-17 collision check shows a concurrent agent in another repo
  within the last hour AND M4 work involves running integration tests
  on the shared test tenant.
- You're tempted to revise the plan doc instead of surfacing a plan
  issue — STOP, surface the issue.

KEY REFERENCE PATHS (all on origin/main as of 2026-05-18)

- THE PLAN (load-bearing): /Users/peteroneil/eq-email-pipeline/docs/
  superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md
  (eq-email-pipeline:033626a)
- Wayfinding doc: /Users/peteroneil/EQ-CORE/live-transcription-fastapi/
  docs/superpowers/specs/NEXT-SESSION-START-HERE.md
- M1 merged PR: https://github.com/oneilstokeseqrm/eq-frontend/pull/392
  (merged as de586bbc)
- M2 merged PR: https://github.com/oneilstokeseqrm/
  live-transcription-fastapi/pull/19 (merged as 756575d7)
- M3 merged PR: https://github.com/oneilstokeseqrm/eq-email-pipeline/
  pull/9 (merged as 85c0295)
- M2 emit contract: /Users/peteroneil/EQ-CORE/live-transcription-fastapi/
  services/account_provisioning/eventbridge_emit.py
  (function emit_email_promoted_for_materialization)
- M3 handler: /Users/peteroneil/eq-email-pipeline/src/pipeline/
  email_promoted_subscriber.py
- M3 persistence helpers: /Users/peteroneil/eq-email-pipeline/src/
  persistence/postgres.py lines 488-628
- Design doc: /Users/peteroneil/EQ-CORE/live-transcription-fastapi/
  docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md
- DBOS plan: /Users/peteroneil/EQ-CORE/live-transcription-fastapi/
  docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md
- M5 verification tooling: /Users/peteroneil/EQ-CORE/
  live-transcription-fastapi/scripts/verify_schema.py +
  verify_consumer_contracts.py (usable from M4 for cross-repo
  verification)
- Comprehensive checkpoint: ~/.gstack/projects/
  oneilstokeseqrm-eq-email-pipeline/checkpoints/<timestamp>-
  phase-1-email-pipeline-m3-deployed-m4-next.md
- Auto-memory: ~/.claude/projects/
  -Users-peteroneil-eq-email-pipeline/memory/MEMORY.md
- Cross-repo auto-memory: ~/.claude/projects/
  -Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/MEMORY.md

eq-email-pipeline source paths M4 touches:
- src/persistence/postgres.py (atomic upsert_thread rewrite,
  persist_pending_interaction helper, email_exists UNION extension,
  insert_email signature change)
- src/pipeline/orchestrator.py (pre-allocate interaction_id, §4.1
  decision branch, §4.2 pending path)
- tests/test_orchestrator_three_state.py (extend with §10.2 cases)
- New tests for persist_pending_interaction + atomic upsert_thread
  concurrent-call

The plan is the load-bearing artifact. M3's handler is the downstream
contract M4's pending writes feed into. Start with /context-restore.
Read the plan §4 next. Then execute M4.
```

---

## Notes for the user pasting this

This prompt is the comprehensive M4 handoff. Key differences vs the M3 prompt (which framed work as "M3 NEXT"):

- **Status string is `PHASE_1_EMAIL_PIPELINE_M1_M2_M3_DEPLOYED_M4_NEXT`.** All three prior milestones merged + deployed + verified.
- **Concrete merge SHAs** for M1 (`de586bbc`), M2 (`756575d7`), M3 (`85c0295`) included throughout.
- **AWS infrastructure** documented as PROVISIONED with all 6 resources + IAM principal + Railway env var change.
- **CRITICAL warning** about M3 already shipping 4 of the 5 originally-planned M4 persistence helpers (`try_claim_local_enrichment` shipped instead of `mark_local_enrichment_started` — atomic CAS form). M4 must NOT re-add.
- **21 LOCKED decisions** (grew from 18; +3 from M3: SQS pattern, DB-TTL > VT, HandlerOutcome enum).
- **5 acknowledged V1 limitations** (grew from 4; +1 from M3: build_skeleton CREATE fallback).
- **Scope is M4 only**, same repo as M3. M5 (production E2E) deferred to its own session.
- **Pre-flight verifies M3 ground truth** (helpers exist, env var set, subscriber active) — catches rollback scenarios immediately.
- **Self-contained reference** — no external lookups needed to bootstrap.
- **Email_threads UNIQUE index pre-flight** — critical because the atomic upsert_thread rewrite depends on it; M4 cannot proceed without it.

Paste the prompt block at the start of the next session. The first action will be `/context-restore`, which loads the comprehensive checkpoint. The rest follows from there.
