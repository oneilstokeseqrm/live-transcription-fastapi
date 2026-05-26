# Next Session Opening Prompt — Granola contact resolution/linking (CRITICAL) + carryover

**Written:** 2026-05-26, after the first real Granola `/connect` E2E passed end-to-end (3 P0 parse/persist bugs found + fixed + shipped: PR #32 transcript times, #33 calendar_event_id, #34 envelope trace_id; EventBridge 5-min trigger wired + live via PR #31). The founder then identified a **critical missing capability**: Granola-ingested interactions are NOT resolving/creating/linking **contacts** the way every other ingestion path does. This is the #1 next-session priority.

**Paste the block below as the opening message of the next session.**

---

```
You're picking up a multi-session project: the Granola.ai → EQ transcript
ingestion integration, in live-transcription-fastapi (/Users/peteroneil/EQ-CORE/
live-transcription-fastapi). The previous session WIRED the 5-min trigger (AWS
EventBridge scheduled Rule) and ran the first real /connect E2E end-to-end — it
PASSED (a real meeting ingested, enriched, visible in the frontend), and along
the way found + fixed + shipped 3 latent P0 bugs. The founder's Granola credential
is STILL CONNECTED and the trigger is LIVE (deliberately left running; NOT cleaned
up). This session adds a CRITICAL missing capability the founder identified:
Granola-ingested interactions do NOT resolve/create/link CONTACTS from meeting
attendees the way the other ingestion paths do. This must be designed + built
PERFECTLY (deep investigation → plan → review → execute → E2E verify), without
breaking existing workflows.

CONTINUITY IS CRITICAL. Read everything before you touch anything. Trust but
verify each artifact; if anything is missing or doesn't match, STOP and tell me.

═══════════════════════════════════════════════════════════════════════
STEP 1 — RUN /context-restore FIRST
═══════════════════════════════════════════════════════════════════════
Run /context-restore. It must load the checkpoint titled
"granola-e2e-passed-contact-resolution-next" (2026-05-26). If it loads
NO_CHECKPOINTS or a different title, STOP and tell me (or fall back to this repo
doc: docs/superpowers/specs/2026-05-27-granola-contact-resolution-next-session-prompt.md).

═══════════════════════════════════════════════════════════════════════
STEP 2 — READ THESE (top to bottom; complete ALL before acting)
═══════════════════════════════════════════════════════════════════════
1. memory/project_granola_contact_resolution_gap.md — THE headline. The full
   contact-resolution gap (known + unknown company), root cause, verified
   file:line evidence, recommended Option A, and the OPEN questions to
   investigate. Load-bearing.
2. memory/MEMORY.md + memory/project_granola_integration.md — Active Work =
   TRIGGER_LIVE + P0_FIXES_SHIPPED + E2E_PASSED; credential CONNECTED.
3. memory/feedback_transcript_ingest_not_raw_interactions.md — raw_interactions
   is NOT the "landed" signal; it's the contact-link FK parent. + the Lane 2
   "did it land" check (interaction_summary_entries=5 + interaction_insights>0).
4. memory/reference_granola_api_shape.md + reference_eventbridge_scheduler_no_http.md.
5. The contact pipeline code (read for the design): services/transcript_enrichment.py
   (enrich + _resolve_contact + the known/unknown/personal branch + pending queue),
   services/intelligence_service.py (_persist_intelligence + _persist_contact_links
   + the `if contact_ids:` gate at ~line 120 + raw_interactions INSERT at ~462),
   services/text_clean_service.py (Lane2Extras ~170 + process() contact_ids flow),
   services/account_provisioning/materialization.py (CHECK_RAW_INTERACTION_EXISTS
   ~61 + the per-signal contact-create ~737 + link ~761/810), services/account_lookup.py,
   services/pending_account_mappings.py, docs/contacts-architecture.md.
6. The Granola side: services/granola_ingestion/adapter.py (_ingest_scenario_a +
   _build_envelope + _classify_and_resolve + _defer_pending_account +
   _queue_unknown_domain_signals), path2.py.
7. feedback memories: feedback_shared_infrastructure_collision (LOAD-BEARING
   before any E2E), feedback_branch_safety, feedback_tenant_isolation,
   feedback_codex_pre_merge_gate, feedback_test_pattern_no_docker,
   feedback_contact_id_consistency, feedback_envelope_contract_immutable.
8. reference_railway_project_ids + reference_railway_proxy_timeout +
   reference_test_tenant + reference_contacts_architecture.
9. tasks/granola-integration-plan.md (§Phase 2.1 follow-ups; LOCKED-23..44) +
   tasks/lessons.md (bottom).

═══════════════════════════════════════════════════════════════════════
STEP 3 — VERIFY STATE
═══════════════════════════════════════════════════════════════════════
  cd /Users/peteroneil/EQ-CORE/live-transcription-fastapi
  git branch --show-current        # expect: main
  git log --oneline -5             # tip includes 1cd746f (#34 trace_id),
                                   #   5441252 (#33 calendar), ab2ae3d (#32 times),
                                   #   0a16a25 (#31 infra doc)
  git status --short               # ignore tasks/llm-modernization-investigation.md
  curl -s https://live-transcription-fastapi-production.up.railway.app/health
  # expect {"status":"ok"}
If main lacks 1cd746f or /health is non-200, STOP.

═══════════════════════════════════════════════════════════════════════
STEP 4 — THE WORK (this session): GRANOLA CONTACT RESOLUTION/LINKING
═══════════════════════════════════════════════════════════════════════
GOAL: Granola-ingested interactions must resolve/create/link contacts (and feed
the Neo4j contact graph) exactly like the email/transcript paths — for BOTH
known-account and unknown-company attendees. Build it PERFECTLY; do NOT break
/text/clean, /upload, /batch or the shared Lane 2 (intelligence_service).

This is NOT a quick patch. Use the discipline:
  (a) DEEP INVESTIGATION first (use subagents to keep context clean). Map the
      COMPLETE contact + raw_interactions lifecycle: EVERY writer of
      raw_interactions (the comments reference a separate "summaries-writer
      service" — find it), the all-unknown-attendees edge (does the non-Granola
      path even write raw_interactions if NO known-account attendee is present?),
      how downstream (eq-structured-graph-core) builds Neo4j contact edges from
      the envelope's contact_ids/contacts[], and exactly what the post-approval
      re-ingest flow does for a deferred Granola note.
  (b) Then WRITE A PLAN (use the writing-plans skill / a plan doc). Run
      /plan-eng-review + /codex consult on the plan before building.
  (c) Then BUILD (TDD; AsyncMock; no Docker), with the Codex pre-merge gate.
  (d) Then E2E VERIFY against the founder's connected credential (the meeting is
      already there) + LOCKED-11 cleanup.

KNOWN STARTING POINT (verified last session, but RE-VERIFY — I erred on
raw_interactions once before correcting):
  - Known-company attendee: enrich() find-or-CREATES the contact at ingest →
    contact_ids → Lane 2 _persist_contact_links writes raw_interactions +
    interaction_contact_links. Granola passes lane2_extras=None → none of this runs.
  - Unknown-company attendee: enrich() queues a signal (NO contact at ingest);
    contact is CREATED at approval (materialization.py:737), and LINKED only if
    the signal has interaction_id AND raw_interactions exists. Granola queues with
    interaction_id=NULL → contact created on approval but NOT linked.
  - Shared thread: the LINK + raw_interactions are written ONLY in
    _persist_contact_links (gated `if contact_ids:`); Granola never produces
    contact_ids → never writes raw_interactions → linking blocked both ways.
  - Recommended (subagent Option A): resolve contacts INSIDE the Granola adapter
    via the emails it already has (extract _resolve_contact's find-or-create core
    into a SHARED helper; do NOT call full enrich() — it re-triggers calendar
    matching + Tavily), pass Lane2Extras(contact_ids=[...], calendar_event_id=None).
    This also makes raw_interactions get written. VERIFY the unknown/deferred path
    (re-ingest as Scenario A after approval) links too; the Scenario-C
    interaction_id=NULL design may need revisiting once raw_interactions exists.

CARRYOVER (after / alongside the contact work, founder to prioritize):
  - Phase 2g: transactional email on credential breakage (LOCKED-32).
  - Phase 3: frontend Connect page + Pending Approvals UI (no self-serve onboarding
    until this; today connecting is a manual backend JWT-mint step). The Pending
    Approvals backend IS wired (POST /queue/{id}/approve) — only the UI is missing.
  - Deferred hardening tickets: §2.1 #13 /connect bad-folder recovery; #14
    defer-path write atomicity; #15 credential-generation token; NEW watermark/
    indexing-lag (a note can strand behind the cycle-start watermark if Granola
    indexes it after the poll — see below); shared intelligence_service trace_id
    auto-generation hardening (today it crashes on empty trace_id; Granola fix was
    adapter-local in PR #34).

═══════════════════════════════════════════════════════════════════════
NON-NEGOTIABLE DISCIPLINES
═══════════════════════════════════════════════════════════════════════
- git branch --show-current immediately before every commit (shared checkout).
- Per-action founder authorization for: push-to-main, merge, Railway changes, AWS
  changes, GitHub-secret changes. Feature branch + PR + branch push are fine.
- Codex pre-merge gate mandatory before any merge. /plan-eng-review + /codex
  consult on the PLAN before building this one (it's design-heavy + touches shared
  Lane 2 code).
- NEVER break the shared contact pipeline used by /text/clean, /upload, /batch.
  Prefer Granola-local changes + a SHARED extracted helper over editing
  intelligence_service/transcript_enrichment in place. Tenant isolation on every
  query. NEVER modify downstream envelope contracts (LOCKED-38); verify via
  scripts/verify_consumer_contracts.py.
- No Docker in tests; AsyncMock patterns in tests/unit/granola_ingestion/. Run
  tests with DBOS_SYSTEM_DATABASE_URL set; use .venv/bin/python (pure-stdlib where
  possible — local .venv lacks some deps like cryptography).
- ⚠️ ALLOW_LEGACY_HEADER_AUTH=true IN PROD: get_auth_context_* does NOT enforce JWT.
- E2E auth (no frontend Connect page yet): mint an internal HS256 JWT with
  INTERNAL_JWT_SECRET (iss=eq-frontend, aud=eq-backend, tenant_id=test 1111…,
  pg_user_id=061ae392-47d5-4f04-9ea8-afa241f23555 [stokeseqrm@gmail.com], iat/exp).
  Last session used a throwaway /tmp/granola_e2e.py (pure-stdlib mint, NOT
  committed — recreate if gone). To re-test the existing meeting: the credential is
  connected; force a tick via POST /internal/granola/cron-tick with the
  X-Internal-Cron-Secret header; reset the credential's last_polled_at=NULL for a
  full re-scan; to re-run a successful note, flip its external_integration_runs row
  to status='failed' (keep eq_interaction_id) so reprocess_pending_notes retries it.

USER POSTURE: Non-developer founder. Plain-English always. Make confident
technical calls; surface product/strategic decisions, scope deviations, and
risky/destructive ops. The founder is careful, asks sharp architecture questions,
and wants the HONEST tradeoff + correctness — verify claims against code, don't
assert from memory (esp. anything about raw_interactions / contacts).

═══════════════════════════════════════════════════════════════════════
KEY STATE (verified 2026-05-26 end-of-session)
═══════════════════════════════════════════════════════════════════════
live-transcription-fastapi main: 1cd746f. eq-frontend main: 7905222 (untouched).
Railway prod: project 847cfa5a-b77c-4fb0-95e4-b20e8773c23e, env
  e4c5ec15-1931-4632-9e58-92d9c6be4261, service 59a69f3d-9a24-4041-942a-891c4a81c5fb.
  Latest deploy a4ff3c54 SUCCESS. /health 200. INTERNAL_CRON_SECRET set (value only
  in Railway). ALLOW_LEGACY_HEADER_AUTH=true.
AWS EventBridge TRIGGER (LIVE, acct 211125681610, us-east-1) — scheduled RULE, not
  Scheduler (Scheduler can't POST HTTP): Rule granola-poll-5min (rate(5 minutes),
  ENABLED) → API destination granola-cron-tick → Connection granola-cron-connection
  (X-Internal-Cron-Secret header) via role eq-granola-cron-invoke-role, DLQ
  eq-granola-cron-dlq. Doc: docs/infrastructure/granola-eventbridge-scheduler.md.
Neon prod: project super-glitter-11265514, branch br-holy-block-ads5069w, db neondb.
CONNECTED TEST CREDENTIAL (NOT cleaned up — founder kept it): vault.user_credentials
  id 6a727bae-5140-4f9e-a65e-4ea8d0523f7d, tenant 11111111-1111-4111-8111-111111111111,
  user 061ae392-47d5-4f04-9ea8-afa241f23555 (stokeseqrm@gmail.com), provider granola,
  folder fol_sBJi17PeBXpHN7 ("Test EQ"). Granola key is in the repo .env as
  GRANOLA_KEY (do NOT print/commit it).
INGESTED TEST MEETING: not_ZxkJDxRRKZNPSE "EQ Test 5.26 v1", interaction
  bca60296-cfa3-4886-885c-02b8c8284735, status=success, 5 summary entries + 2
  insights. Attendees: stokeseqrm@gmail.com (personal) + matt.scanlan@palantir.example.com
  (business, matched the known Palantir account → Scenario A). The OTHER note in the
  folder (not_LnrF1lSqAl8XUD "Second Rodeo") is skipped_no_business_attendees (solo).
Test-tenant known accounts use SYNTHETIC domains: anthropic/linear/palantir/
  snowflake .example.com (real meetings won't auto-match Scenario A unless the
  attendee uses one of these).

═══════════════════════════════════════════════════════════════════════
KNOWN ISSUES / CARRYOVER
═══════════════════════════════════════════════════════════════════════
- Pre-existing test failures UNRELATED to Granola (do NOT fix here): 1 unit
  (test_upsert_summary_uses_unique_interaction_id_index), 16 integration
  (test_queue_lifecycle).
- verify_consumer_contracts.py exits 1 on a pre-existing stale-rule-registry
  WARNING with --no-aws it's clean (exit 0, all 3 consumers accept).
- Watermark/indexing-lag edge: a Granola note created just before a poll can strand
  behind the cycle-start watermark if Granola's list API indexes it slowly. Workaround:
  reset last_polled_at=NULL. Real fix (ticket): lag the watermark or advance to max
  seen created_at.
- The /tmp/granola_e2e.py harness is throwaway (gone next session — recreate the JWT
  mint from the pattern above).
- LOCKED-11 cleanup of the test meeting + credential was NOT run (founder kept it
  connected for more testing). Run it when the founder is ready.
```

---

## Why this is the priority

The integration *works* (ingest + enrich + downstream publish, proven E2E). But a meeting is only useful if it connects to the people on it. Today a Granola meeting attaches to the company (account) but not the person (contact) — known-account attendees aren't created/linked, and unknown-company attendees get an unlinked contact at approval. The other ingestion paths (email/transcript) do full contact resolution. Closing this gap makes Granola a first-class ingestion source. It's design-heavy (shared Lane 2 code, FK chains, the approval/materialization path) and must not break existing flows — hence: investigate deeply, plan, review, then build.
