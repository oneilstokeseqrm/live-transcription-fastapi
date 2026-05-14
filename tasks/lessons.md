# Lessons

## Source field validation (2026-03-17)

Downstream consumers (eq-structured-graph-core, action-item-graph) validate the `source` field on EnvelopeV1 against a strict Pydantic Literal enum. Valid values:

```
zoom | gmail | generic | web-mic | upload | api | import | email-pipeline | outlook
```

live-transcription-fastapi does NOT validate this field — any string is accepted. If an invalid value reaches downstream, the envelope is silently rejected at payload validation (logged as error, message acknowledged and discarded — does NOT go to DLQ).

**Rule:** Always use `source='api'` for test submissions via `/text/clean`. Custom source strings like `smoke-test-xyz` will pass upstream but fail downstream.

## FK chain for interaction_contact_links (2026-03-17)

The FK chain is 3 levels deep:

```
raw_interactions.interaction_id  (must exist)
  → interaction_summaries.interaction_id  (FK to raw_interactions)
    → interaction_contact_links.interaction_id  (FK to interaction_summaries.summary_id)
```

The `interaction_contact_links.interaction_id` column name is misleading — it actually holds `interaction_summaries.summary_id`, not the raw interaction_id. The Prisma schema names it `interactionSummaryId` but maps it to column `interaction_id`.

When creating contact links from the intelligence service, we must:
1. INSERT into `raw_interactions` first (ON CONFLICT DO NOTHING)
2. INSERT into `interaction_summaries` with a new `summary_id`
3. Use that `summary_id` in `interaction_contact_links.interaction_id`

Both `raw_interactions.interaction_type` and `interaction_summaries.summary_type` are NOT NULL with no default — must be explicitly provided.

## Multiple ingestion paths drop account_id (2026-05-12)

The transcript pipeline does NOT enforce account_id propagation as cleanly as a casual reading suggests. Beyond the known WebSocket hardcoded `account_id=None` (`main.py:469`, `main.py:491`), the same omission exists at:

- `routers/upload.py:508` — calls `process_transcript()` without `account_id`
- `routers/batch.py:236` — calls `process_transcript()` without `account_id`
- `services/intelligence_service.py:59` — persistence layer accepts NULL by design
- `models/db_models.py:93` — schema permits NULL
- `models/envelope.py:92` — `EnvelopeV1.account_id` is Optional
- `models/job_models.py:81` — upload jobs persist optional account_id
- `utils/context_utils.py:253` — `get_auth_context()` treats `X-Account-ID` as optional

**Rule:** Any "fix WebSocket account_id" work must extend to ALL these paths. The contract is end-to-end (request context → envelope → job model → process_transcript → persistence). Fixing one path while others remain permissive doesn't close the orphan-contact loophole.

Discovered via Codex consult on the contact quality initiative design doc, 2026-05-12.

## Fallback-to-anchor for per-attendee account resolution is structurally wrong (2026-05-12)

When per-attendee domain lookup misses for a meeting attendee, falling back to the meeting's anchor account_id PRESERVES the misattribution bug it claims to fix. Example: meeting anchored to BigCo, attendee `partner@consultingco.com` — fallback assigns Partner to BigCo, which is factually wrong.

**Rule:** On per-attendee domain lookup miss, branch to one of three explicit states only — queue for account creation, drop the attendee with logged reason, or skip as internal/personal. Never fall back to the meeting anchor account.

This applies to any future code that does per-attendee account resolution in the transcript pipeline.

## Run Codex consult BEFORE writing implementation plans for substantial designs (2026-05-12)

Codex consult on a design document caught 5 CRITICAL and 7 IMPORTANT findings that would have propagated into the implementation plan if the plan had been written first. The cost of one Codex invocation is much smaller than the cost of building the wrong contract for two weeks and discovering it during code review.

**Rule:** For any multi-phase architectural design, the sequence is: brainstorming → design doc → Codex consult → revise design → THEN writing-plans. Do not skip the Codex step.

Codex was sandboxed to one repo; cross-repo claims still need to be flagged as assumptions (not Codex-verified).
