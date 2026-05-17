# eq-email-pipeline: unknown-sender emails are silently dropped

**Discovered:** 2026-05-17 during Phase 1.5 M5 design discussion (live-transcription-fastapi session).
**Priority:** HIGH — the next priority after Phase 1.5 closes.
**Owner repo:** `eq-email-pipeline` (this repo coordinates from `live-transcription-fastapi`).
**Status:** Documented finding. No code change yet.

## The gap

The email pipeline's orchestrator (`eq-email-pipeline/src/pipeline/orchestrator.py:174-196`) resolves an "anchor account" for the email by looking up the **target domain**:

- For **inbound** emails: target = the sender's email.
- For **outbound** emails: target = the first recipient's email.

If the target domain is unknown (returns `None` from `lookup_account_by_domain`), `account_id` stays `None`.

Then `insert_email` (`eq-email-pipeline/src/persistence/postgres.py:200-204`) is called. Because `raw_interactions.account_id` is `NOT NULL` in production, `insert_email` explicitly raises:

```python
if aid is None:
    raise ValueError(
        "insert_email requires a resolved account_id; "
        "raw_interactions.account_id is NOT NULL"
    )
```

The orchestrator's outer `except Exception` catches it (`orchestrator.py:546-549`), logs, and returns `status="error"`. The email is:

- **NOT saved** in `raw_interactions`.
- **NOT processed** (no Neo4j, no LLM extraction, no EventBridge emit, no embedding).
- **NOT queued.** The `pending_signal_proposals` accumulated for unknown-business participants are flushed AFTER `insert_email` succeeds (`orchestrator.py:335-382`). If `insert_email` raises, the proposals are discarded.

## Why this is a real problem

The whole point of Phase 1.5's queue mechanism is to capture exactly these signals — emails from unknown business senders that need an account-creation decision from the user. As-implemented, the queue today captures unknown-business signals **only for secondary participants** (CCs / non-target recipients) on emails where some OTHER party is the known anchor.

### Concrete asymmetry

**Works (secondary unknown):**
- Email from `alice@known-customer.com` to `me@yourcompany.com`, CC `bob@unknown-corp.com`.
- Anchor = `known-customer.com` → resolved.
- Email saved. Bob → queue signal. Alice → contact + linked.

**Broken (primary unknown):**
- Email from `bob@unknown-corp.com` to `me@yourcompany.com`.
- Anchor lookup for `unknown-corp.com` → None.
- `insert_email` raises. Email dropped. **Bob never gets queued.**

The "broken" case is arguably the more common business scenario: cold inbound from a new prospect / partner / vendor. Today the system silently misses it.

## Design-doc vs. implementation mismatch

The design doc (`live-transcription-fastapi/docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md:314`) says:

> "Under Option A (chosen 2026-05-13), Phase 1 + 1.5 never create orphan contacts. The hard rule is enforced from Phase 1 ship: when transcript enrichment or email ingestion encounters an unknown business domain (non-personal, non-internal), the system writes a signal to `pending_account_mapping_signals` capturing the proposed contact data, but does NOT insert into `contacts`. **The interaction is recorded with its anchor account**; the unknown-domain attendee is not in `interaction_contact_links` until approval."

The design doc assumes "the interaction is recorded with its anchor account" — but for inbound-from-unknown-sender, there IS no resolvable anchor. The design doc does not explicitly answer what to do in this case.

The implementation chose: drop the email entirely. This was probably not a deliberate decision; it's a side effect of the `account_id NOT NULL` constraint combined with the target-domain-only anchor lookup.

## Candidate fixes (sketched, not designed)

### Approach A — Use the recipient as the anchor for inbound-from-unknown

For inbound emails whose target (sender) domain is unknown, fall back to the recipient's account as the anchor. The recipient is typically an internal user; their organization's account is known.

- **Pro:** Email gets saved + processed. Sender becomes a queue signal via the per-participant loop. Downstream gets a normal Day-1 emission with full content.
- **Con:** Anchor semantics shift — now an inbound email is "anchored to YOU" until approval, then re-anchored to the sender's newly-created account on approval. Backfill envelope semantics need to handle the anchor-change correctly. Also, the recipient's account isn't always meaningful (e.g., multi-tenant scenarios).

### Approach B — Allow `account_id NULL` temporarily; resolve on approval

Relax the `NOT NULL` constraint on `raw_interactions.account_id`. Save the email with `account_id=NULL`. Create the queue signal. When approval happens, the materialization workflow updates `account_id` to the new account.

- **Pro:** Clean semantics — "this interaction's account is pending."
- **Con:** Touches the Phase-1-shipped hard invariant ("no interaction without an account anchor"). Schema migration (Prisma). Downstream consumers need to handle NULL account_id (or be filtered out by EventBridge rules until approval). Larger blast radius.

### Approach C — Headless interaction state

Create a separate "pending_interactions" table that holds emails awaiting account approval. Move them to `raw_interactions` post-approval.

- **Pro:** Keeps `raw_interactions` clean (always has an account).
- **Con:** New table + new code paths + new dedup logic. Most complex of the three.

### Approach D — Hybrid: A for inbound, current behavior for outbound

For outbound emails to unknown domains, the system probably SHOULDN'T be capturing them as signals (the user explicitly composed an email — they know who they're sending to). For inbound, Approach A.

- **Pro:** Behavior matches user intent.
- **Con:** Adds direction-conditional logic in the anchor resolution.

## What this is NOT in scope for

- **Not Phase 1.5 M5.** M5 ships verified-contract tooling (`scripts/verify_schema.py` + `scripts/verify_consumer_contracts.py`) + checklist updates. Touching email ingestion paths is explicitly out of scope per `docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md` §2.
- **Not the empty-content.text Codex round-6 finding.** That was a separate concern about transcript backfill content. Confirmed not a real problem.

## Next steps (after Phase 1.5 closes)

1. Decide between Approach A / B / C / D via a brainstorming session.
2. Codex consult on the chosen approach (CSO discipline — design-time review).
3. Write an implementation plan in `eq-email-pipeline/docs/superpowers/plans/`.
4. Schema migration in `eq-frontend` if Approach B is chosen.
5. Production E2E test that asserts a cold-inbound-from-unknown email gets queued, then approved → contact materialized → backfill envelope fires.

## Discovery context

Found during the M5 design discussion when the user (founder) pushed back on a content.text design decision and asked me to verify the email-pipeline's actual behavior. The session's investigation surfaced this asymmetry. Original conversation: `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/` checkpoint for 2026-05-17 PM M5 session.
