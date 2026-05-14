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

## Production E2E with a Railway-signed JWT is non-substitutable (2026-05-14)

Automated unit + integration tests + Codex review catch ~90% of issues. The last ~10% requires hitting the live API with a real short-lived JWT signed by the production secret. This was the proof:

- **Phase 1 ship (2026-05-14, prior session):** static-invariant self-review missed 3 P1s. Real Codex caught them. The real production E2E (added at user insistence after the prior session declared "done") then caught a 4th regression mode that even Codex didn't flag — a polling pattern with JWT-only auth that broke when `X-Account-ID` became universally mandatory.
- **Phase 1.5 P2 cleanup (2026-05-14, this session):** Codex Rounds 3, 4, 5 each surfaced a different issue automated tests missed. Production E2E confirmed the polling regression existed before the fix shipped (1 FAIL in the 13-case suite) and PASSED 13/13 after deploy.

**Rule:** Wire production E2E into every phase boundary, not just the final ship. The artifact lives at `/tmp/e2e_phase_1_production.py` — extend it incrementally as new endpoints/behaviors ship; commit the extension in the same PR as the code. Plan section "Phase 1.5 Production E2E Discipline" in `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md` is the canonical reference for when/what to extend.

Manual workflow validation (e.g., Task 1.5.24 in that plan) is COMPLEMENTARY, not a replacement. The automated suite catches regressions fast; the manual workflow catches integration gaps the automated suite couldn't have anticipated.

## When to stop the Codex review spiral (2026-05-14)

Codex finds the next-deepest issue every round. After ~3 rounds of code-correctness P2 fixes, remaining findings tend to drift into operational/documentation concerns rather than algorithmic defects. The user's "GATE: PASS with 0 P1 AND 0 P2" bar isn't always literally achievable in finite Codex rounds — it's a quality SIGNAL, not an immutable rule.

**Examples from Phase 1.5 P2 cleanup:**
- Round 3 P2 (empty-list collapse) — real code defect, fixed.
- Round 4 P2 (interaction_id loss) — real code defect, fixed.
- Round 5 P2 (ORM-vs-schema rollout ordering) — operational concern; schema was already applied to Neon eq-dev before the code branch existed, so the rollout safety was operational not algorithmic. Acknowledged + mitigated via documentation comment, not code change.

**Rule:** Address code-correctness P2s aggressively. When remaining findings are about deployment discipline, documentation, or theoretical safety concerns that are already mitigated operationally, stop the spiral and document the judgment call (commit message + handoff doc). Future sessions should treat this as the precedent.

If a session finds itself on Round 5+ with no new code-correctness findings, ship.
