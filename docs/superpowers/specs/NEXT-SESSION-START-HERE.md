# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative
**Last session:** 2026-05-13 (Phase 1 implementation)
**Status:** Phase 1 IMPLEMENTED across both repos. **Both PRs open and ready for review/merge:** live-transcription-fastapi PR #10 + eq-email-pipeline PR #6. eq-frontend PR #349 already merged. Phase 1 ships once both PRs are merged. Phase 1.5 (worker + outbox + queue UI) is the next major scope.

---

## Critical context (READ FIRST)

1. **You're starting from a working Phase 1 implementation.** Branch `feat/contact-quality-phase-1` has 24 commits ahead of `main` implementing the model-layer tightening, ingestion-path rejection, three-state branching, queue insertion machinery, and self-review docs. Don't re-implement what's already there.

2. **Two cross-repo PRs live outside this repo, both READY:**
   - **eq-frontend PR #349** — MERGED. Adds 8 columns to `pending_account_mappings`, creates `pending_account_mapping_signals` table, adds nullable `raw_interactions.account_id`. Schema is live on Neon eq-dev.
   - **eq-email-pipeline PR #6** — OPEN, ready for review. 43 directly-relevant tests pass; 379 total tests pass. Implements three-state branching in `src/pipeline/calendar_sync.py` and `src/pipeline/orchestrator.py`. Mirrors the primitives locally (asyncpg-translated SQL, same semantics as live-transcription-fastapi). No upstream caller changes needed in that repo.

3. **The user is a non-developer founder.** Make confident technical calls on subagent dispatch, error recovery, and review judgments. Surface only product/strategic decisions. The user explicitly said: "Make the reasonable call and continue; they'll redirect if needed."

4. **All Phase 1 scope decisions are locked. Phase 1.5 scope decisions are locked.** The decision log in `project_contact_quality_initiative.md` is authoritative.

---

## What this session does

Both Phase 1 PRs are already open and ready for review. Three steps to ship Phase 1:

### Step 1 — Review and merge PR #6 (eq-email-pipeline)

```bash
gh pr view 6 --repo oneilstokeseqrm/eq-email-pipeline
# Review the diff; check CI status
gh pr merge 6 --repo oneilstokeseqrm/eq-email-pipeline --squash --delete-branch
```

### Step 2 — Review and merge PR #10 (live-transcription-fastapi)

```bash
gh pr view 10 --repo oneilstokeseqrm/live-transcription-fastapi
# Optional: run `/codex review` interactively on the diff for adversarial check
gh pr merge 10 --repo oneilstokeseqrm/live-transcription-fastapi --squash --delete-branch
```

### Step 3 — Confirm Railway auto-deploy

Railway auto-deploys live-transcription-fastapi on main merge. Verify via the `/canary` skill or the Railway dashboard. Once green, Phase 1 is officially shipped.

After Phase 1 ships, update auto-memory project status to `PHASE_1_SHIPPED_PHASE_1_5_PENDING` and proceed to Phase 1.5 in a separate session if context is fresh.

### After Phase 1 ships — Phase 1.5

Start Phase 1.5 ONLY if context is genuinely fresh. Phase 1.5 is ~27 tasks and probably needs its own session. Recommended next-session pattern:

1. **Read this doc.**
2. **Read the design doc (Section 7.2)** — Phase 1.5 outcome and scope.
3. **Read the implementation plan** at `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` from `# PHASE 1.5` (line ~2538) to end.
4. **Run `superpowers:subagent-driven-development`** (same orchestration model that worked for Phase 1).
5. **Start with Task 1.5.0 (AI-native thought leadership research)** before any code. Phase 1.5 is architecturally sensitive; the research informs design choices around outbox semantics, worker concurrency, and queue UI patterns.
6. **The test-data wipe in T1.5.2 is the highest-risk action.** TRUNCATES `contacts`, `raw_interactions`, `interaction_summaries`, `pending_account_mappings`, `accounts` in eq-dev. Per project memory (`reference_test_tenant.md`), all data is test data and safe to wipe, but this is destructive and irreversible. Phase 1 acceptance gates MUST be solidly green before the wipe.

---

## Phase 1.5 scope reminder (locked)

From the design doc Section 7.2 — DO NOT re-litigate:

1. Schema migration enforcing `contacts.account_id NOT NULL` (after test-data wipe).
2. Schema migration enforcing `raw_interactions.account_id NOT NULL`.
3. New `accounts.state` column (`active | archived`).
4. New `account_provisioning_outbox` table (durable event log — same Postgres txn as account materialization).
5. Worker (location TBD: extend eq-email-pipeline vs. new lightweight service vs. extend live-transcription-fastapi). Polls `status='approved'`, takes advisory lock, calls `eq-agent-action-core POST /api/enrich`, performs atomic materialization transaction (Section 5.6), writes outbox row.
6. Outbox publisher emitting to EventBridge.
7. eq-structured-graph-core consumer for `AccountCreated`.
8. Production queue UI in eq-frontend (Approve / Map / Ignore).
9. Expiry sweep daily job.
10. Re-open trigger in both ingestion pipelines.
11. Authorization helper `can_act_on_queue_entry(user_id, queue_entry)` (owner-only V1, with `tenant_review` escalation).
12. eq-agent-action-core acceptance tests (5 tests per Codex finding #11).

---

## Carry-forward items (Phase 1 NITs deferred to Phase 1.5)

1. **`tenant_internal_domains` wiring.** Currently defaulted to empty set; INTERNAL branch in `services/transcript_enrichment.py` is unreachable in production. Phase 1.5 task: derive `internal_domains` from the tenant's `provider_connections` rows (excluding public domains) and pass through to `enrich()` at every call site.

2. **Single-transaction reopen + upsert.** The unknown-business-domain branch in `services/transcript_enrichment.py` opens two separate sessions (reopen, then upsert). Low-probability race; mitigated by the `(tenant_id, domain)` unique constraint. Phase 1.5: refactor to a single session/transaction so the reopen+upsert is atomic.

---

## Critical project invariants (must hold through Phase 1.5)

Pulled forward from the previous session — unchanged:

- **Contact ID consistency:** every contact carries UUIDv4 `contact_id`. Never store a name without an ID.
- **Tenant isolation:** every Postgres + Neo4j query MUST include `tenant_id`. No cross-tenant queries ever.
- **Three-state branching:** known account → contact; unknown business domain → queue signal, no contact; personal/internal → skip. NEVER fall back to anchor.
- **Backend rejection over frontend trust:** every ingestion path validates `account_id` at the auth-context boundary or 400s (WebSocket: 1008). Queue-hold path is the ONLY exemption.
- **First-owner-wins UPSERT:** `pending_account_mappings.owner_user_id` is never reassigned by routine UPSERT.
- **Outbox-backed durability:** `account_provisioning_outbox` is written in the SAME Postgres transaction as account materialization. Publishing to EventBridge happens after commit.
- **Codex usage as recurring quality gate:** Codex consult runs at every phase boundary. T1.26 in this session was a self-review in Codex format; for Phase 1.5, consider running the actual `/codex review` skill before merging.

---

## Repository state (as of 2026-05-13 Phase 1 session end)

- **Current branch (unmerged):** `feat/contact-quality-phase-1` — 24 commits ahead of main
- **Cross-repo state:**
  - eq-frontend PR #349 MERGED — Phase 1 schema migration live on Neon eq-dev
  - eq-email-pipeline PR PENDING — verify status at session start
- **Verification artifacts:**
  - `scripts/verify_phase_1_invariants.sh` — exit 0; 12 static invariants PASS
  - `tasks/downstream/codex-phase-1-review.md` — 0 CRITICAL, 0 IMPORTANT, 2 NITs (deferred)
- **Test results:**
  - Unit tests: 122 PASS
  - Integration tests: 30 PASS, 1 intentional skip (Phase 1.5 DB scaffold)
- **Phase 1.5 first task (T1.5.0):** AI-native thought leadership research — see plan line ~2542

---

## What NOT to do this session

- Do NOT skip the eq-email-pipeline verification. The Phase 1 PR depends on it.
- Do NOT skip the test-data wipe in Phase 1.5 (T1.5.2). The wipe is what gates NOT NULL enforcement; skipping it leaves a broken schema.
- Do NOT proceed past a failed acceptance gate.
- Do NOT proactively start Phase 2 after Phase 1.5 ships. The stopping point is deliberate per design Section 7.3.
- Do NOT modify locked schema decisions. They are closed.
- Do NOT commit subagent work with skipped tests or `--no-verify`.

---

## Reference reading at session start (30-45 min)

1. **Auto-loaded:** `MEMORY.md`
2. **Project memory:** `~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/project_contact_quality_initiative.md` — read the `## Phase 1 ship (2026-05-13)` section in particular
3. **Self-review doc:** `tasks/downstream/codex-phase-1-review.md`
4. **Design doc Section 7.2** — Phase 1.5 scope
5. **Plan** at `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` — read from line ~2538 (PHASE 1.5 section) to end
6. **The Phase 1 diff:** `git log --oneline feat/contact-quality-phase-1 ^main` to see what shipped; `git show <hash>` for details
7. **`docs/contacts-architecture.md` Section 3.4** — three-state branching contract (new in Phase 1)

---

## Final note for the next agent

Phase 1 shipped a precise, surgical implementation of the account-anchoring contract: 6 model-layer tightenings, 4 route handlers tightened, 3 new utility modules, a queue helpers module, a per-attendee three-state branching rewrite, and a defense-in-depth `_resolve_contact` guard. The architectural standard is high; the self-review found 0 critical issues; the cross-repo dispatch model worked.

Phase 1.5 builds the DURABILITY machinery (outbox, worker idempotency, replay-safe materialization) and the USER-FACING surface (queue UI, Approve/Map/Ignore actions). It's where the design's most architecturally-sensitive choices land. Run the AI-native research at T1.5.0 BEFORE coding — the landscape (GraphRAG, outbox/saga patterns, agentic identity resolution) is the validation reference frame, not legacy CRMs.

Hold the architectural standard. Hold the boundary between product decisions (user) and implementation decisions (you). Ship Phase 1.5 with the same precision Phase 1 was shipped with.
