# Test-Discipline Gaps Surfaced by account_lookup Bug (2026-05-15)

**Trigger:** The `services/account_lookup.py` SQL bug shipped through six quality gates and was undetected for ~24 hours in production. The fix landed at commit `31f513f`. The bug itself was one SQL change; the systemic gaps it revealed are this document.

**Owner for follow-up:** Whoever runs the architecture rethink session and/or executes the resulting plan should fold these items into the new implementation plan. They are NOT one-off cleanup tasks — they are process changes that prevent recurrence.

**Status:** All items are PENDING. Each item has a "how to do it" and acceptance criteria.

---

## Item 1 — Audit integration tests for import-level mocking of in-service functions

### Problem

Across `tests/integration/`, six different test files patch `services.transcript_enrichment.lookup_account_by_domain` at the import level (`patch("services.transcript_enrichment.lookup_account_by_domain", AsyncMock(...))`). The pattern is common; it makes integration tests fast and deterministic. But it bypasses the real implementation entirely. Combined with unit tests that also mock the session, `lookup_account_by_domain` had zero real-substrate coverage. A SQL error inside it shipped through 132 passing tests.

This is not unique to `lookup_account_by_domain`. Any in-service primitive that is imported and patched at the import level across the integration suite has the same risk.

### Action

1. Audit ALL `patch(...)` calls in `tests/integration/` and `tests/unit/`. For each that mocks a function defined in `services/` or `workers/`, identify whether the underlying function has any test that exercises its real implementation (against a real session, real HTTP server, or at minimum a SQL-text assertion).

2. For each mocked-but-uncovered function, add either:
   - An integration test against a real test DB (preferred for SQL primitives), OR
   - A unit test that asserts on the SQL text literal (cheapest; what the new `test_sql_queries_account_domains_not_accounts` does)

3. Document the audit results in a follow-up doc at `tasks/downstream/integration-mock-audit-results.md`.

### Acceptance criteria

- Audit complete; doc lists every in-service function that is mocked-without-coverage.
- New tests added for the top 5 most-mocked uncovered functions (the long tail can be deferred to its own ticket).
- Test count delta documented in commit message.

### Estimated effort

Half a session (audit + write 5 tests). Could be done as part of the rethink-execution session if the chosen architecture changes which functions matter; could be done standalone earlier if confidence in the existing layer needs restoring sooner.

---

## Item 2 — Add per-attendee branching happy paths to production E2E

### Problem

`/tmp/e2e_phase_1_production.py` reports 20/20 PASS but exercises only auth-rejection, validation-rejection, and bare happy-path-200 cases. The per-attendee three-state branching (PERSONAL / INTERNAL / BUSINESS+known / BUSINESS+unknown) — the most-traveled production path — has zero coverage in the E2E. Every branch must have a happy-path case that exercises real downstream effects.

### Action

1. Extend `/tmp/e2e_phase_1_production.py` with four new cases:

   - **BUSINESS+known happy path:** seed `account_domains` with `(tenant_id, "acmetestcorp.example.com", <account_id>)` via Neon MCP before the test. POST a `/text/clean` request with a calendar event whose attendees include `alice@acmetestcorp.example.com`. After the response, assert (via Neon MCP) that `contacts` has a row for alice with `account_id` matching the seeded value; `raw_interactions` has the interaction row with the same `account_id`; `interaction_contact_links` has the alice→interaction link.

   - **BUSINESS+unknown happy path:** POST a `/text/clean` request with an attendee like `bob@unknownbiztest.example.com` (no seeded account_domains row). After the response, assert `pending_account_mappings` has a row with `status=queued` and `domain=unknownbiztest.example.com`; `pending_account_mapping_signals` has a signal referencing the interaction; `contacts` has NO row for bob (queue-not-orphan invariant).

   - **PERSONAL happy path:** Attendee `someone@gmail.com`. Assert NO `contacts` row, NO `pending_account_mappings` row.

   - **INTERNAL happy path:** Attendee from a tenant-internal domain (requires seeding `provider_connections` for the test tenant). Assert NO `contacts` row, NO `pending_account_mappings` row.

2. Each case must clean up after itself (delete or archive the rows it created) so the suite remains re-runnable.

3. Document the seed/cleanup pattern at the top of the new section so future cases follow it.

### Acceptance criteria

- E2E suite extends from 20 → 24 cases.
- Each new case exercises a real Postgres row write (via the synchronous ingestion path) and asserts via Neon MCP that the right rows landed.
- Re-running the suite back-to-back yields stable 24/24 PASS (cleanup correctness).
- The E2E covers every branch of `three-state branching` at least once.

### Estimated effort

One focused session if the cleanup pattern is straightforward; could span two sessions if cross-tenant seed data for the INTERNAL case is tricky.

### Dependency / sequencing

This task is HIGHEST priority because every future phase ship will rely on the E2E catching regressions. The architecture rethink can proceed without it; but Phase 2 ship discipline depends on the E2E being branch-covering.

---

## Item 3 — Narrow the outer except in transcript_enrichment.enrich()

### Problem

`services/transcript_enrichment.py:399-405` has a `try: ... except Exception:` block that catches everything inside `enrich()` and returns an empty `EnrichmentResult`. This is what silently swallowed the SQL error in `lookup_account_by_domain` and made the bug invisible to users (HTTP 200 returned; rows silently missing). Already flagged as a NIT in prior Phase 1 review; deferred at the time; the deferral cost ~24 hours of production-silently-broken behavior.

### Action

1. Read `services/transcript_enrichment.py:enrich()` to enumerate the exception types that the body can ACTUALLY raise in normal operation (network errors from external API calls; PGSqlError from constraint violations on legitimate inputs; LLM provider errors; etc.).

2. Narrow the outer except to that specific list. Programming errors (SQL syntax / missing column / TypeError / KeyError) should propagate. They surface in logs / Sentry / Railway error reporting and trigger an alert.

3. Add a docstring on the narrowed except explaining WHY each exception type is in the list and what the recovery behavior is.

4. Add a unit test that simulates a programming error inside `enrich()` (e.g., raise `KeyError`) and asserts the exception PROPAGATES rather than being swallowed. This guards against future re-broadening of the except.

### Acceptance criteria

- The outer except names specific exception types (not `Exception`).
- A unit test confirms programming errors propagate.
- Logs / Sentry / Railway error reporting receive the exception with stack trace.
- The /review skill checklist now flags any future `except Exception:` in critical-path code.

### Estimated effort

One quarter-session. Small, well-scoped, high value.

---

## Item 4 — Codify "schema probe at design time" discipline

### Problem

The bug shipped because no one ran a schema probe before writing the SQL. The earlier lesson (2026-05-15) on probing EXTERNAL service contracts at design time generalizes to internal schemas, but no review checklist enforces it.

### Action

1. Update `/review` skill checklist to include the question:
   > "For every new SQL query in this diff: was `information_schema.columns` queried against the target Postgres project to verify table/column names? Cite the verification in the SQL comment or commit message."

2. Update `superpowers:writing-plans` skill (or the project-level equivalent) to include a "verified contract" requirement for any plan that calls a database it didn't design in the same plan. The verified contract must cite the actual schema as of a specific date.

3. Add a `scripts/verify_schema.py` helper that takes a SQL query and runs an EXPLAIN against the live project, surfacing missing-column / missing-table errors at design time. (Lightweight; uses Neon MCP under the hood.)

### Acceptance criteria

- `/review` checklist updated.
- `scripts/verify_schema.py` exists and works against any of our SQL primitives.
- The next session's design doc / plan changes include a "verified contract" section citing schema state.

### Estimated effort

Half-session. The script is small; the discipline change is documentation.

---

---

## Item 5 — Cross-service contract verification at design time (added 2026-05-15 after action-item-graph incident)

### Problem

About an hour after Item 4 was codified, a downstream agent in
`action-item-graph` flagged 422 errors from EnvelopeV1 events with
source values not in its `SourceType` enum (missing `zoom` and
`generic` — both listed as valid in `tasks/lessons.md:6-14`). The
enum at `src/action_item_graph/models/envelope.py:34-43` drifted
from the documented canonical set without any review process
catching it. The 422s were initially mis-attributed to PR #13's
outbox publisher; independent verification proved the publisher
hasn't run in production, and the actual cause was the downstream
enum drift.

This is the SAME failure mode as `account_lookup.py`, on a different
contract surface (cross-service Pydantic model instead of internal
SQL schema). The principle generalizes: **any contract between us
and another system — internal or external, schema or behavior — must
be verified against the actual artifact at design time, not its
documentation.** Codified as a new lesson in `tasks/lessons.md` under
"Cross-service contract verification at design time (2026-05-15)."

### Action

1. Update the `/review` skill checklist (or project-equivalent
   plan-review process) to add a "Cross-service contracts" section
   that lists every contract boundary the diff crosses, and for each
   one, requires citing the verified artifact (file path + line range
   + commit SHA or date stamp).

2. For the Phase 1.5 implementation plan (next session), require a
   "Verified contracts" section that probes and cites:
   - The live `eq-structured-graph-ingest-rule` EventBridge rule
     filter pattern (`aws events describe-rule`)
   - The live `action-item-graph-rule` EventBridge rule filter pattern
   - The consumer-side `EnvelopeV1` Pydantic model in BOTH downstream
     repos (read the actual file, cite the enum values)
   - The Neon database schemas for any tables the new code writes
     (`information_schema.columns`)
   - The `eq-agent-action-core` `/openapi.json` for the agent endpoint
     the workflow calls

3. Add a `scripts/verify_consumer_contracts.py` helper (analogous to
   the proposed `scripts/verify_schema.py` from Item 4) that takes a
   source value / detail-type / event-type and checks it against the
   live EventBridge rules + consumer Pydantic models. Output: pass/fail
   per consumer, with the rejection reason cited inline.

### Acceptance criteria

- `/review` checklist updated with "Cross-service contracts" section.
- New helper script exists and checks at least: EventBridge rule
  patterns, downstream Pydantic enum coverage.
- The next session's Phase 1.5 implementation plan includes the
  "Verified contracts" section with live citations.
- Future PRs that touch event-emission code MUST include verified
  contract citations in the PR description.

### Estimated effort

Half-session. The helper script is small; the discipline change is
documentation + checklist updates.

### Dependency / sequencing

This item is HIGH PRIORITY for the rethink-execution session because
the new architecture (DBOS, decided D7) emits EventBridge events from
the final workflow step. Without this discipline, the same class of
bug ships again on a different surface.

---

## Cross-cutting note

These five items don't independently fix the systemic issue. They compose:

- Item 1 closes the COVERAGE gap (real-substrate tests for in-service primitives).
- Item 2 closes the E2E SCOPE gap (per-branch happy paths).
- Item 3 closes the SILENT-DEGRADATION gap (broad excepts swallow bugs).
- Item 4 closes the DESIGN-TIME gap (probe internal database schema before writing SQL).
- Item 5 closes the CROSS-SERVICE CONTRACT gap (probe downstream consumer artifacts before emitting new wire formats).

Any one of them alone would have caught one of the specific bugs we
observed. All five together substantially reduce the probability of
the silent-regression class of bug shipping again — internally OR
across service boundaries.

The architecture rethink session decided on DBOS (D7, 2026-05-15) as
the new substrate. The implementation plan MUST explicitly NOT bake
"another broad try/except" into the DBOS workflow code — DBOS
provides retry semantics, so a try/except inside a workflow step is
the wrong layer. The plan MUST also bake the cross-service contract
verification (Item 5) into the design-time review for every workflow
step that emits to EventBridge, calls eq-agent-action-core, or writes
to Neon. The same test-discipline gaps (Items 1-3) apply to the DBOS
code: any new in-service primitives need real-substrate coverage,
new branches need per-branch E2E, and broad excepts get flagged.

---

## Status (2026-05-17, M5 ship)

- **Item 1** — pending (existing integration tests still mock at the import level)
- **Item 2** — partially addressed (Phase 1 + 1.5 production E2E suites)
- **Item 3** — pending (broad `except Exception` blocks still present in some paths)
- **Item 4 — SHIPPED.** `scripts/verify_schema.py` lives in this repo with
  unit tests at `tests/scripts/test_verify_schema.py`. Documentation in
  `tasks/lessons.md` ("Review gates for this repo's PRs", 2026-05-17).
- **Item 5 — SHIPPED.** `scripts/verify_consumer_contracts.py` lives in this
  repo with unit tests at `tests/scripts/test_verify_consumer_contracts.py`.
  The script enumerates ALL EventBridge rules filtering for our source
  (catching the documentation gap in plan §3.4, which named only 2 of
  the 3+ consumers). Documentation in `tasks/lessons.md`.

The gstack `/review` skill is global, so per-project verification gates
live in `tasks/lessons.md` (loaded at session start). When this repo's
PRs touch SQL or envelope emission code, the reviewer (human or agent)
runs the appropriate script before merge.
