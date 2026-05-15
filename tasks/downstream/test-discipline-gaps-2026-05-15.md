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

## Cross-cutting note

These four items don't independently fix the systemic issue. They compose:

- Item 1 closes the COVERAGE gap (real-substrate tests for in-service primitives).
- Item 2 closes the E2E SCOPE gap (per-branch happy paths).
- Item 3 closes the SILENT-DEGRADATION gap (broad excepts swallow bugs).
- Item 4 closes the DESIGN-TIME gap (probe schema before writing SQL).

Any one of them alone would have caught the specific account_lookup bug. All four together substantially reduce the probability of a similar class of bug shipping again.

The architecture rethink session should explicitly NOT bake "another broad try/except" into whatever new substrate is picked. If the new architecture (Inngest, Temporal, Restate, etc.) provides framework-level retry/error-handling, that REPLACES our try/except — not adds to it. And the same test-discipline gaps (Items 1-2) apply to the new architecture's code: if we add new in-service functions or new branches, they need real-substrate coverage and per-branch E2E.
