# eq-email-pipeline: cold inbound from unknown business — finishing Phase 1

**Recharacterized:** 2026-05-17 PM after re-reading the Phase 1 implementation plan + design doc. This is NOT new scope — it is **incomplete delivery of the Phase 1 acceptance criterion in Task 1.24** (orchestrator.py three-state branching for sender/recipient resolution).
**Priority:** HIGH — first thing the next session executes after pre-flight.
**Owner repo:** `eq-email-pipeline` (this repo coordinates from `live-transcription-fastapi`).
**Status:** Documented + 4 approaches sketched. NOT YET FIXED.

---

## What Phase 1 intended (verbatim from the plans + design doc)

**Phase 1 plan, Task 1.24** (`docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` line 2276):

> "Audit `src/pipeline/orchestrator.py` for compliance with three-state branching for email sender/recipient resolution. The current orchestrator already calls `lookup_account_by_domain()` for sender/recipient domains, but does it correctly handle the unknown-business case?"

Acceptance criterion (line 2303):
> "A test inbox containing an email from `acme.com` (known) plus `unknown-startup.io` (unknown business) plus `gmail.com` (personal) produces:
> - acme.com → contact with account_id=acme
> - **unknown-startup.io → signal row, no contact**
> - gmail.com → no row anywhere"

**Design doc §5.3 (locked rules):**
> "Email pipeline: owner is the user whose connected `provider_connection` **sent or received** the triggering email"

**Design doc §7.1:**
> "eq-email-pipeline/src/pipeline/orchestrator.py: confirm the existing email flow follows the same three-state branching for **sender/recipient resolution**."

The unknown-business-sender → queue-signal path was unambiguously committed Phase 1 work.

## What Phase 1 actually delivered (incomplete)

PR #6 (`895cc9f` in eq-email-pipeline) shipped:
- Per-participant three-state classification loop in `orchestrator.py` (lines 232-279). Each external person gets classified (known/unknown-business/personal/internal) and unknown-business participants get added to a `pending_signal_proposals` list.
- A test (`tests/test_orchestrator_three_state.py`) covering the case where the sender is from a KNOWN account (`alice@acme.com`) and a CC is from an unknown business (`founder@unknown-startup.io`). The signal gets queued correctly. ✅
- A `lookup_account_by_domain()` call against the email's "target" domain (sender on inbound, first recipient on outbound) to find the anchor account for `raw_interactions.account_id`.

What Phase 1 did NOT deliver:
- **Handling for the case where the email's target domain (the anchor candidate) is itself an unknown business.** When this happens, `account_id` stays `None`, and the call to `insert_email` raises a `ValueError` at `eq-email-pipeline/src/persistence/postgres.py:200-204` (because `raw_interactions.account_id` is `NOT NULL` in production). The outer `except Exception` in `orchestrator.py:546-549` catches the raise, logs an error, returns `status="error"`. **The email is dropped: not saved, not processed, not queued.** Any `pending_signal_proposals` accumulated by the per-participant loop are also discarded — they're only flushed AFTER `insert_email` returns successfully (line 335).

So: emails where at least one party belongs to a known account work as designed. Cold inbound from a totally unknown business is silently dropped.

## Why the transcript pipeline doesn't have this gap

Per the user's explicit accepted-limitation (2026-05-17 PM):
- Transcripts are user-initiated. The user knows they're about to record, so requiring them to pick the anchor account in the frontend UI before recording starts is acceptable UX.
- Backend rejects 400 if no `account_id` on the transcript request.
- The "no anchor yet" question never reaches the backend.

Emails are NOT user-initiated — they arrive autonomously from the gmail/outlook integration. There is no UI moment at which to ask "which account?" before the email lands. **The email pipeline genuinely needs to handle the no-anchor-yet case at the backend level.**

## Candidate fix approaches

User feedback (2026-05-17 PM): **do NOT fake an anchor.** Tying an unknown-business email to the recipient's own org account misattributes data; future analytics, account intelligence, and graph relationships would inherit the misattribution. Same critique applies to "borrow" any unrelated account. The right pattern is an explicit pending state for the in-flight interaction.

### Approach C — separate `pending_interactions` table  *(recommended, cutting-edge 2026 pattern)*

Add a new table `pending_interactions` parallel to `raw_interactions` but explicitly scoped to interactions whose account is awaiting approval. Schema mirrors `raw_interactions` minus the `account_id NOT NULL` requirement (the column may be omitted entirely — the row is by definition pending an account decision).

Flow:
1. Email arrives. Per-participant loop runs. If at least one party belongs to a known account → existing `raw_interactions` path (Phase 1 case A). If no party belongs to any known account → `pending_interactions` path.
2. The pending interaction is saved with full content + a foreign key to the matching `pending_account_mappings` queue entry (the one that's about to be created for the unknown sender's domain).
3. The queue signal for the sender's domain references the `pending_interaction_id`.
4. On approval: workflow creates the account; materializes contacts; **moves the pending_interaction row to `raw_interactions`** with the now-known `account_id`; deletes the pending row.
5. On reject (user clicks Ignore): the pending_interaction row is archived alongside the queue entry.
6. On expire (TTL — e.g., 30 days): the pending_interaction can be auto-archived.

**Pros:**
- Architecturally honest. The pending state is explicit; nothing pretends to be what it isn't.
- Preserves the `raw_interactions.account_id NOT NULL` invariant Phase 1 just locked in.
- Symmetric with Phase 2's identity state machine for contacts (`shell → emerging → partial → resolved → verified`). The interaction's pending state mirrors the contact's `shell` state.
- Downstream services aren't affected. They only see the interaction after it's promoted to `raw_interactions` (with full content + the real account_id + materialized contacts in the same EventBridge emission).
- No misattribution risk for analytics, billing, or graph relationships.

**Cons:**
- New table → Prisma migration → cross-repo coordination with eq-frontend.
- `/map` route needs awareness (a user mapping a queue entry to an EXISTING account should also promote any pending_interactions).
- Backfill semantics: the Phase 1.5 workflow's emit step currently only handles `raw_interactions`. Need to add a "promote pending → raw + emit envelope" step.

### Approach B — allow `raw_interactions.account_id NULL` temporarily

Drop the `NOT NULL` constraint; allow `raw_interactions.account_id` to be `NULL` for the pending window; queue the sender's domain; on approval, `UPDATE raw_interactions SET account_id = :new WHERE ...`.

**Pros:**
- No new table.
- Single materialization path.

**Cons:**
- Violates a Phase 1 hard invariant ("no interaction without an account anchor") at the schema layer, weakening the contract that Phase 1 just established.
- Downstream services need to handle `account_id=NULL` envelopes (or get filtered out by EventBridge rules) — adds cross-service coordination.
- All existing queries `WHERE account_id IS NOT NULL` need auditing.

### Approach D — column-level state machine on `raw_interactions`

Add a `state` column (`pending_account_approval | active | archived`). Keep `account_id NOT NULL` but allow a sentinel "pending" account row that's never user-visible.

**Pros:**
- Minor schema change vs. Approach B.

**Cons:**
- The "pending sentinel account" is fundamentally fake — same misattribution critique as recipient-as-anchor, just with a synthetic placeholder.
- Adds coupling between the state column + downstream filters.

### Approach A (rejected) — recipient-as-anchor for inbound

Use the recipient's (your user's own org) account as the temporary anchor.

**Rejected** because it misattributes external emails to the user's own organization. Analytics, account intelligence, and graph relationships would all inherit the wrong attribution.

## Recommended sequence for the next session

1. **Brainstorm with user** — surface Approach C as the recommended; confirm direction. (Product/strategic decision; do NOT auto-decide.)
2. **Codex consult on the chosen approach** (CSO discipline — design-time review before any code).
3. **Write a focused implementation plan** at `eq-email-pipeline/docs/superpowers/plans/2026-05-XX-pending-interactions.md`.
4. **Schema migration** in eq-frontend (`prisma/schema.prisma`) if Approach C is chosen — new `pending_interactions` table.
5. **Implementation** in eq-email-pipeline: orchestrator.py branch to pending_interactions path when no party is known; new persistence helper.
6. **Implementation** in live-transcription-fastapi: workflow's emit step learns to promote pending → raw + emit envelope.
7. **Production E2E test**: cold-inbound-from-unknown email → queue entry visible to user → user approves → contact materialized → email becomes a normal interaction → downstream Neo4j MERGE visible.
8. **Run `scripts/verify_consumer_contracts.py` + `scripts/verify_schema.py`** (M5 tooling) before merge. Use them as the gate.

## Why this is finishing Phase 1, not new scope

- The Phase 1 plan's Task 1.24 already required this work.
- The Phase 1 acceptance criteria already required this scenario to pass.
- The design doc §5.3 already named email senders as queue-signal sources.
- The DBOS plan §2 said "ingestion path changes are out of scope **for Phase 1.5 main scope**" — because Phase 1.5 assumed Phase 1 had already delivered them. Nothing in any plan explicitly deferred this to a later phase.

## Related context (don't re-litigate)

- The empty-`content.text` Codex round-6 P1 finding (deferred from PR #17) turned out to be irrelevant for emails. `eq-email-pipeline/src/persistence/postgres.py:211-221` populates `raw_text=content_text`, so email-source interactions always have content. Inline doc shipped in PR #18.
- Plan §3.4 documented only 2 downstream consumers; the M5 verify_consumer_contracts.py probe surfaces all 4-5. Already corrected.

## Discovery + recharacterization context

Surfaced 2026-05-17 PM during M5 design discussion when the user pushed back on a content.text design decision and asked to verify the email-pipeline's actual behavior. Original handoff (commit `fd38880`) characterized this as a "newly discovered" gap; the user pointed out that the workflow was part of the original Phase 1 plan. This document was rewritten 2026-05-17 PM to reflect the correct framing.
