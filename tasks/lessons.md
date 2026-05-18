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

## TRUNCATE CASCADE blast radius hides cascading wipes (2026-05-14)

A subagent ran `TRUNCATE TABLE ... RESTART IDENTITY CASCADE` against 11 explicit tables to enable a Phase 1.5 `NOT NULL` migration. The CASCADE silently followed FK references and also wiped ~6 additional tables that hold FKs into the listed ones: `opportunities`, `opportunity_pipeline`, `pipeline_forecast`, `forecast_snapshots`, `deal_events`, `emails`, and several `opportunity_*` analytic tables. The subagent's own success report mentioned only the 11 it explicitly listed; the cascade impact was invisible from the report.

The user's pipeline page demo briefly appeared broken because the cascaded tables held the demo's deals/opportunities data — technically "test data" but functionally what made the product look alive.

**Rule:** Before any TRUNCATE/CASCADE/DROP/DELETE-without-WHERE operation, even on test data:

1. **Query FK topology first.** `SELECT conname, conrelid::regclass, confrelid::regclass FROM pg_constraint WHERE contype='f' AND confrelid IN (<target tables>);` enumerates the cascade chain.
2. **Surface the full blast radius BEFORE the operation, not after.** "TRUNCATE here will also cascade-wipe X, Y, Z — proceed?" costs 30 seconds.
3. **Prefer narrower alternatives.** `DELETE FROM table WHERE col IS NULL` (only nulls, one table). `UPDATE ... WHERE col IS NULL SET col = <sentinel>` (preserves rows). `ALTER TABLE ... ADD CONSTRAINT ... NOT VALID; VALIDATE CONSTRAINT` (no data wipe). Per-tenant `DELETE` with `WHERE tenant_id = ...` (bounded).
4. **"Authorized for test data" ≠ "authorized to destroy with surprise side effects."** Scope is bound to what was envisioned.
5. **TRUNCATE ignores tenant_id.** All tenant-isolation discipline goes out the window.

See also: `~/.claude/projects/.../memory/feedback_destructive_ops_blast_radius.md`.

## SQLAlchemy 2.0 AsyncSession.execute() autobegins a transaction (2026-05-14)

Discovered during Codex Round 1 of Phase 1.5 main-scope worker:

```python
async with session_factory() as session:
    rows = (await session.execute(SELECT_SQL, ...)).all()  # AUTOBEGINS a txn
    async with session.begin():                            # FAILS: already in txn
        ...
```

SQLAlchemy 2.0's `AsyncSession.execute()` autobegins a transaction on first read. The next explicit `async with session.begin():` raises `InvalidRequestError: A transaction is already begun on this Session`. Pre-2.0 sessions were implicit-begin-on-flush; the 2.0 change is easy to miss.

**Rule:** For workers that do `SELECT batch + per-entry transaction`, use a **fresh session per entry**:

```python
async with session_factory() as poll_session:
    async with poll_session.begin():
        rows = (await poll_session.execute(SELECT_SQL, ...)).all()
# poll_session closed here; transaction committed

for row in rows:
    async with session_factory() as session:
        async with session.begin():
            await process_one(session=session, row=row)
```

This pattern was forced by P1 Codex feedback on `workers/account_provisioning_worker.py` at line 138-143.

## Codex's static analysis can't see live schema state (2026-05-14)

Discovered during Codex Rounds 2 and 6 of Phase 1.5 main-scope worker:

Codex reported "ON CONFLICT (tenant_id, email) requires unique constraint; this repo's schema does not add one" — as a P1 in TWO separate rounds. Verification via Neon MCP showed `contacts_tenant_id_email_key` IS a UNIQUE INDEX on `(tenant_id, email)`. PostgreSQL's ON CONFLICT inference works with unique indexes (per docs), not only unique constraints. The index is declared in eq-frontend's Prisma schema via `@@unique([tenant_id, email])` and rendered as a unique index by Prisma. Codex looks at this repo's `migrations/` directory only and misses the Prisma-managed constraint.

**Rule:** Before accepting any schema-related Codex P1, verify against the live schema via Neon MCP. Run both `pg_indexes` AND `pg_constraint` queries — `ON CONFLICT` inference matches against either.

```sql
-- Check constraints
SELECT conname, contype, pg_get_constraintdef(oid)
FROM pg_constraint WHERE conrelid = 'tablename'::regclass;

-- Check unique indexes (also valid for ON CONFLICT inference)
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'tablename';
```

If Codex repeats the same false positive across rounds, document it in the PR description as a verified false positive and do NOT act on it. This is one of the "stop the spiral" signals.

## When to stop the Codex review spiral (2026-05-14)

Codex finds the next-deepest issue every round. After ~3 rounds of code-correctness P2 fixes, remaining findings tend to drift into operational/documentation concerns rather than algorithmic defects. The user's "GATE: PASS with 0 P1 AND 0 P2" bar isn't always literally achievable in finite Codex rounds — it's a quality SIGNAL, not an immutable rule.

**Examples from Phase 1.5 P2 cleanup:**
- Round 3 P2 (empty-list collapse) — real code defect, fixed.
- Round 4 P2 (interaction_id loss) — real code defect, fixed.
- Round 5 P2 (ORM-vs-schema rollout ordering) — operational concern; schema was already applied to Neon eq-dev before the code branch existed, so the rollout safety was operational not algorithmic. Acknowledged + mitigated via documentation comment, not code change.

**Rule:** Address code-correctness P2s aggressively. When remaining findings are about deployment discipline, documentation, or theoretical safety concerns that are already mitigated operationally, stop the spiral and document the judgment call (commit message + handoff doc). Future sessions should treat this as the precedent.

If a session finds itself on Round 5+ with no new code-correctness findings, ship.

## Codex spiral discipline — defer-by-design vs keep fixing (2026-05-14)

PR #13 ran 6 rounds of Codex review with the publisher + queue actions diff,
closing 14 P1 and 8 P2 findings with permanent regression tests. Round 7
surfaced 2 more findings, both real bugs but **forward-looking**:

- P1: stale `approval_attempt_id` survives archive+reopen — triggers only
  when the reopen path is wired up (Task 1.5.12, separate PR)
- P2: EventBridge 256KB Detail cap not enforced — bounded by scale we
  won't hit at 1 replica (typical ~1KB, cap requires ~250 signals/entry)

**Decision:** STOP the spiral. Document both as inline TODO comments
pointing at the responsible follow-up phase. Ship Phase 1.5 main scope
without fixing them in this PR.

**Why this isn't slop:**

1. Each Round 1-6 finding was a real bug Codex caught that the implementer
   missed. The TDD regression test density per fix means those bugs can
   never silently regress.
2. Round 7's findings are real but their **triggers don't ship in this PR**.
   The P1 needs the reopen flow (Task 1.5.12); the P2 needs payload sizes
   we won't hit at Phase 1.5 scale.
3. Documenting deferred bugs as inline TODOs is more honest than fixing
   them prematurely and shipping unused defensive code.
4. The user's pattern at every prior phase boundary (Phase 1, Phase 1.5 P2)
   was to ship clean after Codex finding categories stabilized into
   operational/phasing concerns. Round 7 hit that pattern.

**Rule:** at the phase boundary, the cost-benefit shifts. Real code-correctness
bugs with reachable triggers must be fixed before ship. Real bugs with triggers
gated by NOT-yet-shipped code should be documented in code (TODO comments)
and ticketed against the PR that will exercise the trigger. Cost: 30 seconds
of TODO writing. Benefit: the next session author sees the deferral the
moment they touch that file. This is the inline equivalent of "Stop the
Codex spiral when remaining findings are operational/phasing decisions"
from the prior Phase 1.5 P2 session lesson — applied to phasing-conditional
findings specifically.

The judgment: don't ship UNREACHABLE-bug fixes. Ship the documented deferral.

## Probe external service contracts at design time, not deploy time (2026-05-14)

Phase 1.5 worker (PR #12) was scaffolded against an imagined
eq-agent-action-core contract. The worker sent `{tenant_id, domain,
worker_attempt_id}` to `POST /api/enrich` and expected `{account_id, domain}`
back synchronously, treating the agent as the account-creation point.

The actual agent contract (from probing live `/openapi.json` during
Workstream D deployment):

- `POST /api/enrich` body schema is `{url, effort?}` — `url` required;
  `tenant_id` comes from JWT claim; `worker_attempt_id` is silently dropped.
- Default response is `Content-Type: text/event-stream` SSE; `?stream=false`
  returns AccountProfile blocking 30–90+ seconds.
- The agent service is "Enrich a company URL into a structured AccountProfile"
  — research-only. It never INSERTs into our Postgres `accounts` table.
- No `/api/accounts/create-from-domain` route exists anywhere in the 44
  endpoints of the agent's API.

The worker code, having 6 rounds of Codex review for internal correctness,
nonetheless cannot succeed end-to-end because Codex couldn't see the live
external service. The mismatch was first detectable at deploy time —
discovered in the deployment session itself.

**Rule:** Before designing code against a live external service's contract,
probe its `/openapi.json` (or equivalent — Swagger, GraphQL introspection,
gRPC reflection, or a hand-written API doc). The cost of one curl is ~5
seconds. The cost of designing 700 LoC + 6 review rounds against a fabricated
contract is what we hit on 2026-05-14: an architectural reset before
deployment.

**How to apply:**
1. At design time for ANY plan that calls an external service, the design
   doc must include a "Verified contract" section that cites the actual
   request/response shape from the service's spec, not the spec's name
   ("OpenAPI says...") or the service's title ("the enrichment service does X").
2. If the spec doesn't expose what you need, that's a finding — surface it
   in the design doc as a cross-repo coordination dependency BEFORE code is
   written, not after.
3. If the service is "production-deployed for use case A" and you're
   inventing use case B, explicitly verify B's needs against the spec.
4. Same rule for cross-repo coordination dependencies: check what other
   services actually call vs. what their docs say they accept.

This is the second time in this initiative a contract was imagined rather
than verified (first was Phase 1's caller-side completeness gap; see
"Multiple ingestion paths drop account_id" lesson). Different failure
modes, same underlying cause: assumed contract without verification.

## Stop and question dated architecture when integration reveals it (2026-05-15)

The Phase 1.5 worker contract-mismatch blocker surfaced more than a tactical
fix — it surfaced that the underlying architecture (polling worker + outbox
table + separate publisher process) is a 2018 pattern. The prior-session
response was Path A: patch the agent contract, keep the polling worker. The
user correctly rejected Path A with: "you're also saying the architecture we
chose was dated."

**The lesson is not "rethink everything on every blocker."** Most blockers
are tactical and warrant tactical fixes. The lesson is about the SPECIFIC
moment when an integration-layer blocker reveals that the layer below it is
itself obsolete relative to current best practice.

**Signals to watch for:**

1. The blocker is at a service boundary you control on one side and consume
   on the other (e.g., worker calls external agent).
2. The fix would preserve infrastructure that you'd describe as "dated" or
   "what we'd have built in [N-years-ago]."
3. Current best practice in your domain (AI-native, in our case) has
   meaningfully diverged from what you have, and you're aware of the
   divergence.
4. The thing being rethought is load-bearing across future phases (not
   just the current slice).
5. Sunk cost feels heavy — you've already written hundreds or thousands of
   LoC + dozens of regression tests on the current pattern.

When all five signals are present: STOP. Do the rethink at the right
altitude in a fresh session. Resist the urge to patch the contract and
move on.

**How to apply:**

1. Surface the architectural question explicitly to the user. Frame it as
   "is the underlying pattern still right, or has the field moved past
   it?" Don't bury it under a tactical recommendation.

2. If the answer is "rethink," write a NEUTRAL rethink brief that does not
   recommend any option. Anchoring the brief to a recommendation defeats
   the rethink (the prior session's instinct to scope Path A's execution
   was exactly this anti-pattern).

3. Build a comprehensive handoff: standalone project-context snapshot,
   honest landscape scan of the alternatives, neutral scope brief,
   anti-anchoring instructions, decision-process steps. The handoff is
   the thing the rethink session inherits; its quality determines the
   decision quality.

4. Don't do the rethink in the same session that surfaced the blocker.
   Context saturation biases toward incremental thinking. Fresh session,
   fresh perspective, fresh /office-hours.

5. Once the rethink is done, document what was considered AND rejected,
   with reasons. Future sessions need to know not just what was picked
   but what was explicitly ruled out, so they don't re-litigate.

**Why this isn't analysis paralysis:** the trigger is specific
(integration-layer blocker + dated underlying architecture + future-phase
implications + cross-domain best-practice divergence). It's not "rethink
everything on every blocker." It's "when an integration reveals you're
patching a legacy pattern, stop patching."

The cost: one session of rethink instead of one session of patching.
The benefit: a substrate that compounds across the remaining 2-3 phases
of the initiative, rather than another session like this one in 3 months
when Phase 2 hits the same wall.

## Four systemic quality gaps that let a silent regression ship Phase 1 (2026-05-15)

A downstream agent (eq-synthetic-date-generation) traced a bug in
`services/account_lookup.py` where the SQL queried `FROM accounts` for
a `domain` column that doesn't exist on that table — the correct table
is `account_domains` (a join table). The bug was introduced in commit
2552b4b (Phase 1 contract-tightening merge, 2026-05-14) and was live
in production for ~24 hours before being detected. During that window,
every transcript whose calendar event had BUSINESS-domain attendees
produced ZERO rows in `raw_interactions`, `interaction_contact_links`,
and `calendar_event_interaction_links` because the SQL error was
silently swallowed by the outer try/except in
`TranscriptEnrichmentService.enrich()`.

**Six quality gates did not catch it:**

1. The Phase 1 implementation session (didn't probe live schema)
2. 2 rounds of Codex review on the diff (Codex can't see live schema)
3. A self-review in Codex format (claimed everything was wired correctly)
4. 122 unit tests passing (3 of them mocked the function; mocks don't run SQL)
5. ~10 integration tests passing (ALL patched `lookup_account_by_domain`
   at the import level, so the real query never ran in tests)
6. Production E2E reporting 9/9 → 13/13 → 20/20 PASS across phase ships
   (no case exercised a BUSINESS-domain attendee with a known account)

This is the worst kind of bug: silent, in critical-path data, undetected
by every quality gate the team built, and visible only to a downstream
observer. The fix itself is one SQL change. The systemic gaps it
revealed are four separate disciplines that need codifying.

### Gap 1 — Probe live schema at design time

Internal database schema must be probed at design time, not at runtime.
This is the same lesson as "probe external service contracts at design
time" (2026-05-15, earlier) but generalized to internal schemas — same
failure mode, different surface.

**How to apply:**
1. Before writing any new SQL that references columns / tables you didn't
   personally write in this same diff, run a probe query via Neon MCP:
   ```sql
   SELECT column_name, data_type FROM information_schema.columns
   WHERE table_name = '<your_table>' AND table_schema = 'public';
   ```
2. The probe takes ~5 seconds. The cost of NOT probing is the multi-day,
   multi-quality-gate failure mode above.
3. Document the probe result inline in the SQL comment, with a date stamp.
   ("Schema verified via information_schema on YYYY-MM-DD against
   project <project_id>.") This makes future drift auditable.
4. Codex review CANNOT verify live schema. Self-review in Codex format
   CANNOT verify live schema. Only an actual probe verifies it.

### Gap 2 — Mock-at-import-level is a coverage hole for in-service functions

When integration tests patch `services.foo.bar` at the import level
(`patch("services.foo.bar", AsyncMock(return_value=...))`), the real
implementation of `bar` is bypassed entirely. If `bar` has no direct
unit test against a real-ish substrate (or its unit tests are also
mocked at the import level), the function's real behavior is uncovered
across the entire test suite. Bugs in `bar` ship through every quality
gate.

In our case: `lookup_account_by_domain` was mocked at the import level
in 6 different integration test files. Its only direct unit tests used
`MagicMock` for the session, so the SQL was never executed. Total real
coverage of the function: zero. A SQL error in the function shipped
through 132 passing tests.

**How to apply:**
1. When patching an in-service function in an integration test, audit
   whether that function has DIRECT real-substrate coverage elsewhere.
   "Real-substrate" means: tests that actually execute the function's
   side effects against something other than a `MagicMock` (a test DB,
   a fixture, a SQL parser).
2. If no real-substrate coverage exists, add at least one — either an
   integration test against a test DB, or (cheapest) a SQL-text assertion
   in the unit test (as the new `test_sql_queries_account_domains_not_accounts`
   demonstrates).
3. Boundary functions (third-party HTTP clients, external services) ARE
   appropriate to mock at the import level. In-service primitives are
   not — they're owned by the same codebase and should be tested against
   real substrates.
4. The /review skill checklist should include: "for every import-level
   mock of an in-service function, name the test that exercises the
   real implementation."

### Gap 3 — Production E2E must exercise happy paths through fan-out branches, not just boundaries

The production E2E suite at `/tmp/e2e_phase_1_production.py` reports
20/20 PASS but only covers: auth rejection (401/400), validation rejection
(422), missing-header rejection (400), and bare happy-path 200s with
minimal participants. It does NOT cover: a happy path with a calendar
event that has BUSINESS-domain attendees whose domain is already in
`account_domains`. That branch — three-state branching → BUSINESS+known
→ `lookup_account_by_domain` returns a hit → contact created with
resolved account_id — is the most-traveled production path and had
zero coverage.

**How to apply:**
1. For every fan-out branch in critical-path code (per-attendee
   classification PERSONAL/INTERNAL/BUSINESS+known/BUSINESS+unknown,
   three-state state machines, multi-arm decision logic), the production
   E2E suite MUST include at least one happy-path case per arm that
   exercises real downstream effects (writes the expected rows, emits
   the expected events).
2. Boundary tests (auth, validation, error mapping) are necessary but
   not sufficient. A 20/20 pass on boundary cases means we know we
   correctly reject bad input; it does NOT mean we know we correctly
   process good input.
3. Each phase ship must add E2E cases for the new branches it introduces.
   This is already codified in the "Phase 1.5 Production E2E Discipline"
   section of the plan doc but only as "extend the suite incrementally"
   — make the rule stronger: "extend the suite with one happy-path-per-arm
   case for every new branch."

### Gap 4 — Broad try/except blocks silently degrade behavior on bugs

`services/transcript_enrichment.py:399-405` has a `try: ... except
Exception:` block around the enrichment side-effects that catches and
silently degrades on any failure. When `lookup_account_by_domain`
threw a SQL error from the missing column, the except caught it,
logged something, and returned an empty `EnrichmentResult`. The user
saw `/text/clean` return 200; the deeper pipeline silently produced
zero rows.

This was already noted as a NIT in prior Phase 1 code review (project
memory "Phase 1 minor nits noted (defer to Phase 1.5 polish)" item 3).
The NIT said: "outer `try/except Exception` swallows the new `ValueError`
from the recording_user_id-None invariant. Either narrow the outer
except or add a comment documenting the intentional swallow." The
NIT was deferred. The bug we just fixed is a concrete case of why
deferring it cost ~24 hours of production silently-broken behavior.

**How to apply:**
1. In critical-path code, `except Exception:` is a code smell. Either
   narrow it to the specific exception types we expect (and let
   everything else propagate to surface as a real error), or write a
   comment documenting WHY the broad swallow is intentional.
2. The /review skill checklist should flag every `except Exception:`
   in code under `services/`, `workers/`, `routers/` and require an
   inline justification.
3. NITs from prior reviews that have shipped become DEBT, not "deferred."
   Treat them as such — when a NIT comment becomes load-bearing for a
   bug, the next ship cycle should narrow the except in the same PR as
   the bug fix. (This fix does NOT narrow `transcript_enrichment.py`'s
   except per the prompt's instruction to limit scope; it's tracked in
   `tasks/downstream/test-discipline-gaps-2026-05-15.md` for follow-up.)

### Why the existing lessons didn't catch this

The 2026-05-15 earlier lesson ("Probe external service contracts at
design time") was about EXTERNAL services (the eq-agent-action-core
agent). The principle generalizes to INTERNAL schemas but the prior
lesson didn't explicitly say so. Future external-vs-internal contract
verification should be one umbrella lesson, not two.

The Production E2E lesson (2026-05-14, "Production E2E with a Railway-
signed JWT is non-substitutable") asserted the E2E suite is the final
quality gate. It IS — but the suite is only as good as its coverage,
and 20/20 PASS on auth/validation boundaries doesn't mean coverage of
happy-path branches.

The Codex review lessons asserted that Codex review is non-substitutable.
It IS — but Codex can't see live database schema. "Codex review caught
3 P1s in Round 1" implies Codex is sufficient; the right framing is
"Codex review is necessary but insufficient — must be combined with
live schema probing AND real-substrate test coverage AND branch-
covering E2E."

All three prior lessons remain true. They just don't compose to "this
bug should have been caught." This new lesson explicitly closes the
composition gap.

## Cross-service contract verification at design time (2026-05-15)

Roughly an hour after the `account_lookup.py` silent regression was
fixed, a downstream agent in `action-item-graph` flagged 422 errors on
its `/process` endpoint and initially hypothesized that PR #13's outbox
publisher was emitting AccountProvisioning events that were leaking
through an unfiltered Lambda forwarder. Independent verification (live
deployment logs, `railway.json` start command, and a SELECT against
`account_provisioning_outbox` showing zero rows) proved the outbox
publisher has never fired in production — the worker process isn't
deployed. The actual cause was a different cross-service contract gap:
`action-item-graph`'s `SourceType` enum (at
`src/action_item_graph/models/envelope.py:34-43`) is missing `zoom` and
`generic` — two values listed as canonically valid in
`tasks/lessons.md:6-14`. A synthetic injection test emitted EnvelopeV1
events with one of those source values; the downstream consumer
rejected them with 422; the DLQ accumulated 11 stuck messages.

This is the SAME failure mode as `account_lookup.py`, but on a
different surface: a contract assumption (the downstream `SourceType`
enum) was not verified against the live consumer artifact at design
time. The Pydantic model lives in a separate repo, was authored
without `zoom` and `generic`, and drifted from upstream's documented
canonical set. Nothing in our review process required probing it.

The principle that survives is broader than "probe external service
contracts" (2026-05-14) and broader than "probe live schema" (2026-05-15).
**The composed principle is: any contract between us and another
system — internal or external, schema or behavior, database or HTTP
or message-bus — must be verified against the actual artifact, not
its documentation, not its expected shape, not its name.** Three
incidents now corroborate the principle on three different surfaces:

1. **External HTTP contract** (`eq-agent-action-core` enrich endpoint,
   2026-05-14) — `OpenAPI.json` probe would have caught the worker's
   imagined contract before code was written. Cost ~700 LoC + 6 review
   rounds.
2. **Internal database schema** (`account_lookup.py` SQL, 2026-05-15) —
   `information_schema.columns` probe via Neon MCP would have caught
   `accounts.domain` vs `account_domains` before code was written.
   Cost ~24 hours of production silently-broken behavior.
3. **Cross-service Pydantic model contract** (`action-item-graph`
   SourceType enum, 2026-05-15) — reading the downstream consumer's
   `models/envelope.py` would have surfaced the missing `zoom`/`generic`
   values. Cost 11 stuck DLQ messages + downstream regression triage.

### How to apply

The unified rule for any plan that emits or consumes data across a
service or system boundary:

1. **List every contract boundary the plan crosses.** A boundary is
   any place where one component's output becomes another's input via
   a wire format. Common boundaries:
   - Database schemas (Postgres / Neo4j / etc.)
   - HTTP / GraphQL / gRPC API contracts (request + response shapes)
   - Message-bus envelopes (EventBridge / Kinesis / Kafka / SQS)
   - Consumer-side Pydantic / JSONSchema models in OTHER repos
   - EventBridge rule patterns (filters between bus and consumer)
   - Function signatures across module boundaries (less critical but
     same principle)

2. **For each boundary, cite the verified artifact inline.** Don't
   say "the consumer accepts EnvelopeV1" — say "the consumer at
   `action-item-graph/src/action_item_graph/models/envelope.py:34-43`
   accepts `SourceType ∈ {web-mic, upload, api, import, email-pipeline,
   gmail, outlook}` as of 2026-05-15 (commit SHA)." This makes drift
   auditable at future review time.

3. **Probe the artifact at design time, not deploy time.** Concrete
   probes per surface:
   - Postgres schema → `information_schema.columns` / `pg_constraint` /
     `pg_indexes` via Neon MCP
   - HTTP contracts → `/openapi.json` curl or live request
   - EventBridge rules → `aws events list-rules` + `describe-rule`
   - Consumer Pydantic models → grep / read the consumer repo's
     `models/*.py` and quote the relevant enum / field definitions

4. **EventBridge has two contract layers, not one.** The wire format
   (Source / DetailType / Detail) is the OUTER contract; the
   consumer's Pydantic model is the INNER contract. The outer
   contract decides whether an event reaches a consumer (via rule
   patterns); the inner contract decides whether the consumer accepts
   it. Both can fail independently. Verify both.

5. **Verification scope expands with the change surface.** A change
   to a single in-service function that doesn't cross boundaries
   doesn't need cross-service probing. A change that emits a new
   event type / source value / detail-type, or that consumes from a
   new source, or that adds a new field to a wire format — needs the
   full boundary audit.

### How this composes with the four systemic quality gaps

The "Four systemic quality gaps" lesson (2026-05-15) covered:

1. Live-schema verification at design time
2. Real-substrate coverage for in-service primitives
3. Per-branch E2E coverage
4. Narrow exception handling

This lesson generalizes #1 to "all contract verification at design
time," and adds a new architectural concern: **EventBridge rule
patterns are a contract surface in their own right**. The
`action-item-graph-rule` already filters on `source:
["com.yourapp.transcription", "com.eq.email-pipeline"]` — verifying
this saved triage time when validating the downstream agent's
analysis. Future implementation plans that emit to EventBridge MUST
list every consumer rule pattern they expect to flow through, with
the rule names + filters cited.

### Concrete implication for the Phase 1.5 implementation plan

The new architecture (DBOS, decided D7 2026-05-15) emits EventBridge
events from the final workflow step. The implementation plan must
include a "verified contracts" section that explicitly cites:

- The live `eq-structured-graph-ingest-rule` filter pattern + the
  consumer's live `EnvelopeV1` model
- The live `action-item-graph-rule` filter pattern + the consumer's
  live `EnvelopeV1` model
- The Neon `pending_account_mappings` + `account_provisioning_outbox`
  + related-table schemas via `information_schema.columns`
- The `eq-agent-action-core` `/openapi.json` for any new agent calls

Plans that don't include this section are repeating the exact
mistake all three incidents share.

---

## Codex review is a merge gate, not a follow-up (2026-05-15)

### Lesson

Run `/codex review` on the diff **before requesting merge**, not
after. Treat its P1 findings as merge blockers: fold them into the
same PR before the merge button is pressed. Post-merge Codex review
is allowed but ONLY catches what a pre-merge review would have at the
cost of a hotfix PR.

### Why

Phase 1.5 M1 merged at commit `dc0806c` then surfaced two real P1
findings on the post-merge Codex pass — `websockets>=14.0`'s rename
of `extra_headers` → `additional_headers` broke deepgram-sdk 2.12.0
at runtime, and the missing `DBOS_SYSTEM_DATABASE_URL` env var would
have silently fallen back to ephemeral SQLite, defeating the entire
durability buy. Both shipped to production with the deploy succeeding
(import-time, but call-site failure on the first `/listen`). The fix
was PR #15 `e334638`. Cost: a hotfix PR, an extra production deploy,
extra time in the merge cycle, and one user-visible failure window
between the M1 deploy and the hotfix.

Pre-merge would have been a normal review iteration — one extra
commit on the open PR before merging — instead of "merge, deploy,
hotfix, deploy again."

### How to apply

For every phase-1.5 milestone PR (M3 onwards) and every comparable
phase boundary:

1. Open the PR with the proposed diff.
2. Before clicking merge, run `/codex review` on the diff or the PR
   URL.
3. Read Codex's output:
   - P1 findings → fold into the same PR; commit, push, re-run Codex
     until P1 count is zero.
   - P2/P3 → judgment call. If the work is small, fold in; if it's
     genuinely follow-up, ticket it and proceed.
4. Only after the P1 gate passes, merge.

This rule is non-negotiable for milestones that touch durability,
schema migrations, dependency upgrades, or cross-service contracts.
For ergonomic PRs (docstring fixes, README edits) the gate is overkill.

The four systemic quality gaps that shipped the 2026-05-15 silent
regression all could have been caught by the disciplines in the
Phase 1.5 implementation plan. Don't repeat them by skipping the
explicit Codex pass between code being written and code shipping.

---

## Imports don't catch keyword-arg removals in transitive dependency upgrades (2026-05-15)

### Lesson

When a dependency upgrade renames or removes a public keyword
argument, the failure mode is **at call-time, not import-time**.
Smoke-test the actual call sites at runtime — don't trust that
imports succeeding means the code still works.

### Why

DBOS install pulled `websockets>=14.0` (versus our pinned 13.1).
`deepgram-sdk==2.12.0` imports cleanly under websockets 14.2, AND
`websockets.client.WebSocketClientProtocol` still works under
websockets 14.x as a deprecated alias — so two of three smoke checks
passed. But `websockets.connect(extra_headers=...)` was renamed to
`additional_headers=` in websockets 14.0, and deepgram's
`_utils.py:230` calls `extra_headers=`. The TypeError surfaces only
when `/listen` actually negotiates a connection — neither imports
nor unit tests with mocked websockets exercise it.

The fix was `services/deepgram_websockets_compat.py`: a kwarg-
translating shim imported BEFORE deepgram. The shim is small, but
spotting the need for it required reading the dependency's source —
which we wouldn't have done if the M1 hotfix Codex review hadn't
flagged the version-skew risk.

### How to apply

When bumping a transitive dependency for an unrelated reason:

1. **Grep all in-tree call sites** for the deprecated/removed
   signature — search the dependency's source AND your own.
2. **Run a real call-path smoke test** in CI or a Railway one-off:
   the actual HTTP request, WebSocket handshake, or whatever the
   bumped dependency mediates. Importing the module is not enough.
3. **If the dependency is a leaf in your dependency tree** (you
   import it directly), upgrade it deliberately and check the
   changelog. If it's a transitive (forced by another upgrade),
   verify the *transitive* dependency's call sites against the new
   version's API.
4. **Pin the surfaced incompatibilities** as a compat shim or a
   coordinated upstream upgrade — don't paper over with a try/except.

A passing unit test suite with mocked transport is the same
silent-regression risk class as the 2026-05-15 schema bug: import-
level mocks make real-call-site breakage invisible.

---

## Coordinated multi-repo schema migrations need explicit code-lifecycle sequencing (2026-05-15)

### Lesson

Schema migrations that **add** (columns, tables, indexes) are
forward-compatible — old code keeps working against new schema.
Schema migrations that **remove** (drop column, drop table, NOT
NULL flip) are backward-incompatible — old code breaks against new
schema. Bundle them at your peril. Each must ship in a milestone
whose code lifecycle no longer depends on the removed thing.

### Why

Plan §11 v2/v3 bundled M2 (Prisma migrations) as "add UNIQUE INDEX
AND drop `account_provisioning_outbox`." At execution time the
ordering caught us: `workers/materialization.py:243` still INSERTed
into the outbox on every `/map` call. Dropping the table in M2 would
have broken `/map` until M3 deployed. The mid-flight fix was to
restore the table within ~3 minutes (zero rows lost; queue UI not
exercising /map yet in production) and split M2: UNIQUE INDEX (an
addition) ships in M2; DROP TABLE (a removal) moves to M3.5,
post-M3-deploy when no code path writes to outbox.

The plan's "verify the existing test suite passes against post-
migration schema" gate would have caught this if integration tests
weren't import-mocking the materialization path (Item 1 of test-
discipline-gaps).

### How to apply

For any coordinated cross-repo / cross-service migration:

1. **Classify each migration as ADDITION or REMOVAL.** Adding a
   column / index / table / constraint = ADDITION. Dropping anything
   or tightening nullability/typing = REMOVAL.
2. **Bundle only ADDITIONS together.** A single PR with N additions
   is safe regardless of deploy order across consumers.
3. **Sequence REMOVALS post-deploy of all code paths that referenced
   the removed thing.** The acceptance gate for any removal migration
   is "grep the codebase, every production code path that referenced
   the removed thing has been deployed in its updated form."
4. **When a plan accidentally bundles both, split it at execution
   time.** The cost of one extra milestone PR is small; the cost of
   a broken-production deploy window is high.
5. **Document the split in the plan's revision history.** Future
   sessions reading the plan should see the original intent + the
   correction + why.

This is the third rule in the family: **deployments are sequenced,
not atomic**; the migration's safety window depends on which
service's code is "ahead" at any given moment.

---

## Tenant-scoped DELETE is NOT session-scoped on shared test infrastructure (2026-05-16)

### Lesson

A correctly-scoped `DELETE FROM table WHERE tenant_id=:test_tenant`
is safe in isolation. It is NOT safe when multiple agents — across
multiple repos — share that test tenant as their working surface.
"Scoped to a tenant" means "scoped to everyone using that tenant,"
not "scoped to my own work." Before any destructive operation on
shared test infrastructure, check for concurrent agents and either
pause for explicit user confirmation or use isolation (Neon branches,
advisory locks).

### Why

2026-05-15 session: ran an 8-table cleanup transaction three times
against test tenant `11111111-1111-4111-8111-111111111111` to clean
up leftover data from failing test runs. The DELETE was correctly
tenant-scoped. But the eq-synthetic-date-generation agent in another
repo had an active inject running in the same test tenant — it had
just seeded a Palantir account row, four contacts, eight email_threads,
and was about to write summary entries + intelligence insights.

Forensic trail (from the affected agent's investigation):
- Cascade fingerprint: 8 email_threads rows with `account_id IS NULL`
  (SET NULL cascade signature that only fires when parent accounts
  row is DELETEd).
- pg_stat_user_tables.last_autovacuum on accounts = 7 minutes after
  the last DELETE cycle.
- Timing: 10 summary_entries + 20 insights written with NULL
  account_id correspond to transcripts processed BEFORE the first
  DELETE fired — the only Lane 2 writes that survived the FK
  requirement.

My session-log timestamps for the three DELETE cycles correlate
exactly with their incident window. The other agent's Bug #1
verification failed as collateral damage.

The misconception that caused this: I asked "did my WHERE clause
scope correctly?" (answer: yes) instead of "could my correctly-
scoped DELETE have collided with another active session's work on
the same scope?" (answer: yes, any time the scope is shared
infrastructure).

The `feedback_destructive_ops_blast_radius.md` auto-memory rule
("verify FK cascade chain and confirm with user before TRUNCATE /
DROP / DELETE / CASCADE — even on test data") was in force AND
was violated. The session ran three DELETE cycles without
confirming once. The rule needed to be specific about
shared-infrastructure collisions, not just FK cascade chains.

### How to apply

Before ANY destructive SQL on shared test infrastructure (the
`11111111-1111-4111-8111-111111111111` tenant, or anything else
shared across repos):

1. **Check for active sessions in other repos:**
   ```bash
   ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
   ```
   Any file modified in the last hour is a hazard signal. Look
   especially for the eq-synthetic-date-generation,
   eq-email-pipeline, and eq-frontend project directories.

2. **Honor advisory locks where they exist.** The
   eq-synthetic-date-generation repo's cleanup script uses
   `pg_advisory_lock(hashtext('eq-cleanup-test-tenant'))` to
   serialize destructive cleanup. Raw SQL via Neon MCP doesn't
   acquire that lock — it bypasses the serialization. Either
   acquire the same lock OR pause and ask the user.

3. **Prefer Neon branches for write-heavy test isolation.**
   `mcp__neon__create_branch` creates an isolated branch in
   seconds. Work targets the branch; the user merges or discards
   when done. Zero collision risk.

4. **Test fixtures that DELETE on teardown** (the pattern in
   `tests/conftest.py:_teardown_test_tenant_rows`) inherit the
   same collision risk. Either:
   - Run them only when no other agent is active
   - Switch them to a Neon-branch-per-test-session strategy
   - Replace with insert-only fixtures + accept some data accumulation

5. **For one-shot cleanup (not in test fixtures):** announce the
   intent + table list to the user BEFORE running. Wait for
   explicit confirmation. Do not run if any other agent's session
   was active in the last hour without that confirmation.

6. **The right self-audit question** is not "did my WHERE clause
   scope correctly?" but "could my correctly-scoped DELETE have
   collided with another active session's work on the same scope?"
   The first is necessary but not sufficient.


---

## Review gates for this repo's PRs — verified-contract tooling (2026-05-17)

### Lesson

This repo's PRs that touch SQL or EventBridge emission MUST run two
verification scripts BEFORE merge, in addition to the gstack `/review`
checklist:

1. **`scripts/verify_schema.py`** — for any new or modified SQL constant.
   Runs `PREPARE` against the live Neon database; surfaces undefined
   column / table / function errors at design time. Closes the bug class
   that produced the 2026-05-15 Phase 1 silent regression (`accounts.domain`
   referenced after the column had moved to `account_domains`; the SQL
   error was swallowed by an outer `except Exception`, the response was
   200, downstream rows were never created).

2. **`scripts/verify_consumer_contracts.py`** — for any change to the
   envelope shape emitted to EventBridge (source value, detail-type,
   interaction_type, extras shape). Statically validates the envelope
   against each downstream consumer's Pydantic model via AST parsing;
   reports drift per-consumer (e.g., `action-item-graph`'s `SourceType`
   enum missing a value the producer wants to emit). Closes the bug
   class that produced the 2026-05-15 action-item-graph SourceType drift.

### Why

Two real Phase 1 / Phase 1.5 incidents shipped because contracts —
internal SQL and cross-service envelope — were trusted by documentation
or grep rather than verified against the live artifact at design time.
Both were exactly the kind of mistake `/codex review` and the static
test suite miss: SQL constants in Python files don't execute until
runtime; consumer Pydantic models live in repos that aren't on this
PR's reviewer's screen.

Codifying both as scripts makes the discipline cheap (one command, ~1s)
and CI-friendly (exit 1 on drift; fold into pre-merge GH Actions later).

### How to apply

For every PR in this repo:

1. If the diff touches `*.py` files containing SQL strings (look for
   `sa_text(`, `"""SELECT`, `"""INSERT`, `"""UPDATE`, `"""DELETE`,
   `"""WITH`), run `scripts/verify_schema.py --sql-text "..."` against
   each constant. Or pipe each constant in via `--stdin`.
2. If the diff touches `services/account_provisioning/eventbridge_emit.py`
   OR introduces a new `source=` or `interaction_type=` value OR a new
   detail-type, run `scripts/verify_consumer_contracts.py` against the
   proposed envelope (with `--source` / `--interaction-type` /
   `--envelope-file` overrides as appropriate).
3. Cite the verification result in the PR description ("verify_schema:
   OK on 4 new SQL constants" / "verify_consumer_contracts: all 3
   consumers accept the envelope").
4. If `verify_consumer_contracts.py` reports drift, surface to the user
   — the fix may require a coordinated change in a sibling repo's
   Pydantic model + EventBridge rule + this repo's emit step. NOT a
   silent fix.

Future improvement: wire both scripts into a pre-commit hook or CI
workflow so the gate is automatic.

## Cross-repo constraint-relaxation requires SQL audit before plan lock (2026-05-17)

The Phase-1-email-pipeline M1 PR (eq-frontend) dropped the single-column
UNIQUE on `interaction_summaries.interaction_id` to enable a new composite
UNIQUE. Plan §7 explicitly claimed M1 was "Safe to deploy independently."

That claim was false. The Phase-1.5-M3 materialize code in
live-transcription-fastapi had been using:

```sql
INSERT INTO interaction_summaries (...) VALUES (...)
ON CONFLICT (interaction_id) DO UPDATE ...
```

That `ON CONFLICT (interaction_id)` clause requires a UNIQUE constraint or
index matching the exact column tuple. Once M1 deployed and dropped the
single-column UNIQUE, every meeting approval would have failed at runtime
with:

```
ERROR: there is no unique or exclusion constraint matching the
       ON CONFLICT specification
```

The plan-writing session missed this because it focused on the new schema,
not on existing SQL that depended on the old schema. The 4-Codex-round
plan review missed it for the same reason — reviewers were reading the
plan, not grep-ing the repos.

**Rule for future plans that drop or relax a UNIQUE constraint:**

Before locking the plan, GREP every repo that talks to this Postgres
instance for `ON CONFLICT` clauses that reference the constraint's columns
(in any order). For interaction_summaries.interaction_id specifically:

```bash
grep -rn "ON CONFLICT.*interaction_id" \
  /Users/peteroneil/EQ-CORE/live-transcription-fastapi \
  /Users/peteroneil/eq-email-pipeline \
  /Users/peteroneil/eq-frontend
```

Document the affected call sites in the plan and pair each one with the
update needed for the new constraint. If the update lands in a different
repo than the migration, document the deploy coordination explicitly.

**What we did when we discovered it mid-execution (M2 session):**
- M2 included the SQL fix (`ON CONFLICT (interaction_id)` →
  `ON CONFLICT (tenant_id, interaction_id, summary_type)`).
- Coordinated merge: M1 merged at 22:28:49Z, M2 merged at 22:29:38Z
  (49-second window of risk). Window closed cleanly. Test-data only, no
  real-user impact.

## Codex round-N convergence pattern: extend the 4-round soft cap when severity is decreasing (2026-05-17)

LOCKED-10 says "Codex review BEFORE merging (4-round soft cap; extendable
when real P1s keep surfacing)." This lesson codifies WHEN extending is
justified vs. when it's diminishing returns.

Pattern observed during Phase-1-email-pipeline M2 review:

| Round | Findings | Severity |
|-------|----------|----------|
| R1 | 2 (1 P1 + 1 P2) | High — orphan race, /map symmetry |
| R2 | 2 (1 P1 + 1 P2) | High — signals guard, lifecycle scoping |
| R3 | 2 (2 P1) | High — Step 4 concurrency, Step 5 subquery |
| R4 | 2 (1 P1 + 1 P2) | High — orphaned signals, lifecycle slack |
| R5 | 2 (2 P2) | Medium — user attribution, cross-queue roles |
| R6 | 1 (1 P2) | Medium — email-link batch guard |
| R7 | 0 | CLEAN |

**The signal that extending past 4 was justified:**
1. Each round surfaced NEW, non-redundant findings (no false positives;
   no re-flagging the same issue).
2. Severity was DECREASING (P1+P1+P1+P1 → P2+P2 → P2 → 0).
3. Each fix engaged with a specific concrete bug, not a stylistic concern.

**The signal that would have justified stopping early:**
1. Round-4 false-positive pattern: Codex flags fixes that aren't in the
   diff yet (this happened during the plan-writing rounds; not during M2).
2. Findings reduced to nits, naming, or stylistic improvements.
3. The same issue gets re-flagged with slightly different framing.

**Heuristic:** if rounds N and N+1 both surface real P1s, run round N+2.
If rounds N+1 and N+2 both produce only P2s or P3s, stop and ship. If
round N+1 is CLEAN, ship.

Applied this heuristic to M2 R7 (CLEAN) — shipped. Applied to M1 R3
(CLEAN) — shipped. Both shipped without subsequent regressions.

**Counter-example from earlier sessions:** Phase-1.5 M5 review went 6
rounds; R5 found a P2 that was scope-creep (type-validation rabbit hole)
and was rejected, R6 was CLEAN. So extending past 4 is the right default
when findings are real, but use trajectory analysis (severity decrease +
non-redundancy) — not just "Codex found something" — as the extension
signal.

## eq-frontend live-db CI workflow needs DIRECT_DATABASE_URL env var (2026-05-17)

PR #392 (Phase-1-email-pipeline M1) was the first Prisma migration after
the `live-db` CI workflow was added. The check failed:

```
Error: Prisma schema validation - (get-config wasm)
Error code: P1012
error: Environment variable not found: DIRECT_DATABASE_URL.
```

The workflow runs `npx prisma migrate deploy` against a Neon branch to
validate the migration. `prisma/schema.prisma` declares:

```
datasource db {
  provider  = "postgresql"
  url       = env("DATABASE_URL")
  directUrl = env("DIRECT_DATABASE_URL")
}
```

Both env vars must be set in the CI environment. The workflow had
`DATABASE_URL` configured from a GitHub secret but not `DIRECT_DATABASE_URL`.

**Why we merged anyway:** Vercel preview deploy passed (which IS the
meaningful migration validation against a real preview DB). Main branch
isn't branch-protected so the failing check didn't block merge. Verified
post-merge via Neon MCP that the migration applied cleanly.

**Required follow-up:** add `DIRECT_DATABASE_URL` to the `live-db` workflow
from the same GitHub secret that supplies it to Vercel. Otherwise every
future Prisma migration PR will trip this check. Small fix; the workflow
yaml lives in `.github/workflows/` in eq-frontend.

## Anchor Codex with on-the-ground comments when schema lives upstream (2026-05-18)

For PRs in repo A that reference tables/columns whose schema is owned by
upstream Prisma in repo B, Codex's static analysis CANNOT see the upstream
schema and will repeatedly flag "missing migration in this repo" as a P1
finding across multiple review rounds. This is the same family as
"Codex's static analysis can't see live schema state" (2026-05-14) but on
a slightly different surface — cross-repo schema ownership instead of
live-vs-static.

### Evidence: Phase-1-email-pipeline M3 review (2026-05-18)

Codex round-1 P1 flagged "Add the schema migration for local enrichment
columns" against eq-email-pipeline. The columns live in eq-frontend's
Prisma schema (added by M1, PR #392, merge `de586bbc`, deployed
2026-05-17). Pre-flight Neon MCP query confirmed they're live. Round-2
re-flagged the SAME finding (FP repeat). After adding an inline docstring
in `handle_email_promoted` citing the upstream PR + schema-ownership
reference (`reference_prisma_schema_ownership.md`) + verified-deployed
state, round-5 did NOT re-flag it.

### How to apply

When writing SQL in repo A that references upstream-Prisma schema:

1. **First Codex round catches the FP.** Don't fix by adding a migration
   to repo A — that would duplicate / conflict with the canonical schema.

2. **Anchor with an inline comment.** Add a docstring or `# NOTE:` block
   near the SQL citing:
   - The upstream PR + merge SHA + deploy date
   - The Neon MCP query that verified the columns are live
   - The auto-memory reference (`reference_prisma_schema_ownership.md`)
   - The lesson reference ("see tasks/lessons.md 'Codex's static
     analysis can't see live schema state'")

3. **The comment helps both Codex AND humans.** Codex's next pass on the
   diff appears to incorporate inline comments into its analysis — it
   stopped re-flagging the M3 finding after the anchor comment landed.
   And future reviewers reading the SQL won't have to re-discover the
   cross-repo ownership.

4. **PR description still documents the FP explicitly.** Even after
   anchoring, list the carry FP in the PR description so the reviewer
   knows what Codex previously flagged and why it's a non-issue.

## DB CAS TTL must be strictly longer than SQS VisibilityTimeout (2026-05-18)

When a Postgres CAS guard (`UPDATE ... SET claimed_at = NOW() WHERE ... AND
(claimed_at IS NULL OR claimed_at < NOW() - INTERVAL 'N minutes') RETURNING id`)
and an SQS VisibilityTimeout both bound the same race, the TWO TTLs MUST
differ — equal values fire the race at the exact boundary.

### Why it matters

At the boundary:
- T0:     Worker-A receives msg (SQS VT starts), wins CAS (DB TTL starts).
- T+5min: BOTH timers expire simultaneously.
- T+5min: SQS makes msg visible → Worker-B receives.
- T+5min: Worker-B reads row: `started_at` exactly 5 min ago. Comparison
          on the second boundary is non-deterministic with clock skew.
- T+5min+ε: Worker-B's CAS wins (started_at < NOW() - 5min by clock skew),
          starts CONCURRENT non-idempotent writes with Worker-A who is
          still running.

### How to apply

For any SQS consumer with a DB-side claim guard:

1. **Make DB-TTL strictly larger than VT.** E.g., DB-TTL = 2×VT or
   VT + safety margin. Phase-1-email-pipeline M3 uses DB-TTL = 10 min,
   VT = 5 min.

2. **The redelivered worker at T+VT then sees the claim within DB-TTL.**
   It returns TRANSIENT_SKIP (leaves message in queue). The concurrent-
   claim window opens only at T+DB-TTL, by which point a non-
   pathologically-hung worker has either completed or genuinely crashed.

3. **Document the relationship in both the SQL and the Python constant.**
   The Python-side TTL (used for early-return guard) and the SQL CAS
   interval (used for atomic claim) MUST stay in lockstep. Add a
   cross-reference comment in both places. Use a test to assert the
   Python constant matches the documented value.

4. **The race is bounded, not eliminated.** A worker that takes longer
   than DB-TTL still triggers the concurrent-claim window. For truly
   safe re-entry, either (a) make the downstream writes idempotent
   (MERGE patterns + edge-count counters), or (b) use SQS
   ChangeMessageVisibility heartbeat to extend VT while work is in
   progress. Plan §6.3 documents the V1 acceptance + V2 roadmap.

### Codex round-5 P1 (2026-05-18)

Initial M3 design had DB-TTL = SQS VT = 5 min (matched intentionally for
"alignment"). Codex flagged the boundary race. Fix: bumped DB-TTL to
10 min; documented the asymmetry in both the SQL comment + the `_CLAIM_TTL`
constant + the handler docstring. Race is now bounded by 10 min instead
of 5 min — same V1 limitation magnitude, requires deeper hang to trigger.

## Postgres array concatenation is NULL-poisoned — COALESCE each side BEFORE the `||` operator (2026-05-18)

PG's array `||` operator returns NULL if EITHER operand is NULL, not the
non-null side. Any `INSERT ... ON CONFLICT DO UPDATE` that unions two
arrays must COALESCE each side INSIDE the unnest, not just the outer
aggregate, or legacy rows with a NULL array will silently lose the
newly-supplied values.

### Evidence: Phase-1-email-pipeline M4 Codex round-1 P2 (2026-05-18)

First M4 cut of the atomic `upsert_thread` rewrite:

```sql
participant_emails = COALESCE(
    (
        SELECT array_agg(DISTINCT e ORDER BY e)
        FROM unnest(
            email_threads.participant_emails || EXCLUDED.participant_emails
        ) AS t(e)
    ),
    ARRAY[]::text[]
)
```

The OUTER COALESCE looks defensive but the failure path is:

1. Legacy row has `participant_emails IS NULL`.
2. `NULL || ARRAY[new...]` evaluates to NULL.
3. `unnest(NULL)` yields zero rows.
4. `array_agg(DISTINCT ...)` over zero rows returns NULL.
5. Outer COALESCE rewrites the column to `[]` — newly-supplied emails
   are also dropped.

Fix: COALESCE BEFORE the concatenation:

```sql
participant_emails = COALESCE(
    (
        SELECT array_agg(DISTINCT e ORDER BY e)
        FROM unnest(
            COALESCE(email_threads.participant_emails, ARRAY[]::text[])
            || COALESCE(EXCLUDED.participant_emails, ARRAY[]::text[])
        ) AS t(e)
    ),
    ARRAY[]::text[]
)
```

### How to apply

1. **Any time you write `array_a || array_b` in PG**, ask "could either
   side be NULL?" Even when the column has a DEFAULT of `'{}'::text[]`,
   legacy rows pre-dating that default can hold NULL — the default only
   applies to inserts.

2. **The pattern generalizes:** PG's `||` for jsonb, hstore, and tsvector
   has similar NULL-propagating semantics. `to_tsvector(NULL)` =
   NULL → `tsvector || NULL` = NULL → losing the index entry.

3. **Test with a NULL-column fixture** when adding `||` to a write path.
   The bug doesn't fire if your test DB always has non-NULL arrays.

4. **In-tree precedent:** the PRE-rewrite SELECT-then-UPDATE pattern at
   `src/persistence/postgres.py:288-356` (M4 pre-rewrite) DID handle
   this correctly via Python's `row["participant_emails"] or []`. The
   bug was introduced by the SQL rewrite. SQL semantics ≠ Python
   semantics — port the defensive defaulting too.

### Generalizes to other DB operations

The same class of bug applies anywhere a NULLable value participates in
an operator that returns NULL on NULL: numeric `+`/`-` (`NULL + 1 =
NULL`), string `||`, boolean `AND`/`OR` (3-valued logic), `LIKE` against
NULL pattern. ANSI-SQL's NULL semantics are uniform. The general rule:
when writing UPDATE expressions over potentially-NULL columns, audit
every operator for NULL propagation.

## Scope to plan-explicit framing when Codex flags scope expansion (2026-05-18)

Plan says "cold-inbound." Code implementation accidentally also handles
outbound. Codex flags as P1. The right move is to ADD an explicit
direction guard matching plan scope, NOT to argue Codex out of the
finding by claiming the plan is implicitly direction-agnostic.

### Why

Implementation can drift from plan in two directions: (a) UNDER-fulfilling
the plan (missing a documented case), (b) OVER-fulfilling the plan
(handling cases the plan didn't enumerate). The second mode is
SEDUCTIVE: "the code handles more cases, what's the harm?"

The harm: every additional case is a behavior the user hasn't reviewed,
hasn't seen a test for, and might not want. For Phase-1-email-pipeline
M4, the plan's framing was "cold-inbound from unknown business." When
the §4.1 branch fired for outbound too, the user might have seen sent
emails (their own outgoing!) sitting in the admin queue waiting for
approval — surprising at best, broken-feeling at worst.

The right scope-down move:

```python
# Before (Codex R1 P1):
if account_id is None:
    target_domain_class = classify_domain(...)
    ...

# After:
if account_id is None and direction in ("inbound", "internal"):
    target_domain_class = classify_domain(...)
    ...
```

Adds one condition. Locks behavior to plan scope. Future expansion
(Phase 2: outbound capture for cold-outreach intelligence) is opt-in
via an explicit user decision, not a side effect of M4 shipping.

### How to apply

1. **When Codex P1 raises a scope question, scope-DOWN first.** The
   guard usually costs one line; the behavior unlocks a future
   conversation about whether to expand.

2. **Pair the scope-down with a comment + test.** The comment cites
   the plan section. The test (here:
   `test_outbound_to_unknown_business_does_not_enter_pending_path`)
   locks the boundary so a future "let me handle outbound too"
   refactor surfaces immediately.

3. **Add the "Phase 2 enhancement" mention in the comment.** Signals
   to future readers that the gap is INTENTIONAL, not an oversight.

4. **Don't argue the plan is "implicitly broader" without explicit
   user confirmation.** Plans are written quickly; if a case wasn't
   enumerated, treat that as intentional unless the user says otherwise.

### Counter-example (when expanding scope IS right)

If the plan-narrow scope produces a clearly broken behavior (e.g.,
"handle inbound only" but inbound + outbound share a critical data
invariant that both must satisfy), then scope expansion is justified —
BUT surface to the user before shipping. The default is scope-down
+ document; expansion requires explicit approval.

## SQS consumer receipt-deletion is a tri-state decision, not binary (2026-05-18)

Naive SQS consumer pattern: `handle(msg); delete_message(msg)`. The
implicit assumption is "no exception = success = delete." This loses
messages when the handler returns successfully but has not actually
completed the work — specifically when it "skipped" because another
worker is in flight.

### Evidence: Phase-1-email-pipeline M3 Codex round-2 P1 (2026-05-18)

Handler had a Layer-2 in-flight guard:
```python
if started_at is not None and started_at > now - 5min:
    return  # another handler holds the claim
```

The `_process_message()` wrapper then deleted the receipt because no
exception was raised. The race:

- T0:     Worker-A receives msg, wins CAS (started_at = T0).
- T0+ε:   Worker-B receives same msg (duplicate / VT expiry).
- T0+ε:   Worker-B sees started_at within TTL → returns "skip" →
          `_process_message` deletes the receipt.
- T+X:    Worker-A crashes before completing enrichment.
- Result: started_at is set, completed_at NULL, NO SQS message to retry.
          Email is stuck "started but never completed" forever.

### How to apply

1. **Return an outcome enum from the handler, not None.** Three states:
   - `COMPLETE` — work done (or already done) — delete the receipt.
   - `PERMANENT_SKIP` — cannot succeed on retry (unknown id, perma-bad
     state) — delete the receipt to prevent DLQ loop.
   - `TRANSIENT_SKIP` — lost the race against a concurrent worker —
     DO NOT delete; let SQS redeliver after VisibilityTimeout.

2. **`_process_message` switches on the outcome.** Delete on COMPLETE
   or PERMANENT_SKIP. Leave receipt for TRANSIENT_SKIP. Handler
   exceptions also leave the receipt (SQS handles retry via maxReceiveCount).

3. **Write a regression test for each branch.** Mock the SQS client,
   call `_process_message` with a handler that returns each outcome,
   assert `delete_message` was/wasn't called.

4. **Document the failure mode in the enum's docstring.** The race
   scenario is non-obvious; the enum + its docstring is the most
   readable place to capture WHY the tri-state exists. Future
   contributors looking at the SQS code will see it immediately.

### Generalizes beyond SQS

The same pattern applies to any at-least-once message queue where the
consumer can explicitly ack/leave. RabbitMQ, Kafka, NATS JetStream,
Google Pub/Sub — all have ack/nack semantics where naive "no exception
= ack" loses transient-skip messages. The HandlerOutcome enum is the
right contract.

## Prisma @@unique materializes as INDEX, not CONSTRAINT — ON CONFLICT must use column-list inference (2026-05-18)

Prisma's `@@unique([col1, col2, ...])` directive generates Postgres
`CREATE UNIQUE INDEX`, NOT `ALTER TABLE ... ADD CONSTRAINT ... UNIQUE`.
Unique indexes live in `pg_indexes`; unique constraints live in both
`pg_constraint` AND `pg_indexes`. SQL `ON CONFLICT ON CONSTRAINT <name>`
ONLY resolves against `pg_constraint`. Application code using the
`ON CONSTRAINT` form against a Prisma-managed unique will fail with
`asyncpg.exceptions.UndefinedObjectError: constraint "..." does not exist`.

### Evidence

Phase-1-email-pipeline M5 E2E (2026-05-18). `pending_account_mapping_signals.pending_signal_dedup`
was declared via Prisma `@@unique` → materialized as a unique INDEX
only. The Phase-1 SQL at `eq-email-pipeline/src/persistence/pending_account_mappings.py:77`
used `ON CONFLICT ON CONSTRAINT pending_signal_dedup` — which crashed
in production on the very first cold-inbound signal flush. The codepath
had been latent for the entire Phase 1 era because the only callers
also wrote known-account signals via paths that didn't reach
insert_signal. M4 made the §4.2 cold-inbound branch reachable; the
bug surfaced immediately.

### How to apply

1. **For Prisma-managed schemas, always use column-list inference:**
   `ON CONFLICT (col1, col2, ...) DO NOTHING/UPDATE`. The column list
   matches against the unique INDEX directly. Same semantics as named
   constraint, no schema dependency.

2. **If you need a NAMED constraint:** explicitly write
   `ALTER TABLE ... ADD CONSTRAINT name UNIQUE USING INDEX name;`
   after Prisma's CREATE INDEX, to promote the index to a constraint.
   But this is fragile — every Prisma reset/migrate cycle requires
   re-applying it. Prefer column-list inference.

3. **Test schema MUST match production schema generation pattern.**
   `tests/schema.sql` declaring `pending_signal_dedup` as a CONSTRAINT
   masked the production INDEX form in CI. Restate it as
   `CREATE UNIQUE INDEX` so future ON-CONSTRAINT mistakes fail in
   tests, not in production.

4. **AsyncMock-based unit tests asserting "ON CONFLICT ON CONSTRAINT X"
   via string-match lock the wrong shape silently.** Pair every mock
   test with an integration test against the actual schema. The unit
   test catches a developer changing the string accidentally; the
   integration test catches the string being wrong from day one.

### Related

[[feedback_codex_pre_merge_gate]] — Codex review caught the related
NULL-DISTINCT semantics issue in the integration test (Codex R1 P1).
The verified-contract scripts shipped in Phase-1.5 M5 don't catch
this class of bug because they verify SCHEMA presence, not
ON-CONFLICT-target alignment. Phase-2 candidate: extend
`scripts/verify_consumer_contracts.py` to grep for `ON CONFLICT ON
CONSTRAINT <name>` patterns and assert the named constraint exists
in `pg_constraint` (not just `pg_indexes`).

## Postgres unique indexes default to NULLS DISTINCT — dedup fails for partial-NULL tuples (2026-05-18)

A Postgres unique index on `(a, b, c)` does NOT dedupe two rows where
some indexed column is NULL. Default NULLS DISTINCT semantics mean
`NULL ≠ NULL` even when the rest of the tuple matches. So
`INSERT ... ON CONFLICT (a, b, c) DO NOTHING` will INSERT both rows
even if `a, b` match and `c` is NULL on both.

### Evidence

`pending_signal_dedup` is on `(queue_id, contact_email, source_type,
interaction_id, calendar_event_id)`. Email signals always have
`calendar_event_id=NULL`. Two duplicate webhook deliveries of the
same email signal → both rows insert (n_rows=2, not 1). Surfaced by
Codex R1 P1 in M5.1 review (2026-05-18) and confirmed empirically.

Bounded blast radius: the orchestrator's email-level dedup (via
`email_exists` UNION at `eq-email-pipeline/src/pipeline/orchestrator.py:~113`)
catches sequential duplicate webhooks BEFORE reaching `insert_signal`.
The signal-level unique index is genuinely defense-in-depth — its
NULL hole only matters for truly concurrent webhook races OR manual
workflow replays. The cosmetic-only failure mode (duplicate signal
rows for the same observation) doesn't corrupt downstream materialization
because contacts dedupe by email at the join layer.

### How to apply

1. **Audit every Prisma `@@unique([...])` on tables with nullable
   indexed columns.** If the table participates in INSERT...ON
   CONFLICT dedup, the gap is real. Document it OR fix it.

2. **Fix options:**
   - **`nullsNotDistinct: true` on the `@@unique` directive** (Prisma
     5.7+, Postgres 15+). Generates `CREATE UNIQUE INDEX ...
     NULLS NOT DISTINCT`. Clean fix; no application-side change.
   - **COALESCE NULL columns to a sentinel UUID in the dedup tuple.**
     E.g., `ON CONFLICT (queue_id, contact_email, source_type,
     COALESCE(interaction_id, '00000000-...'), COALESCE(calendar_event_id, '00000000-...'))`.
     Cosmetic; pollutes the dedup key.
   - **Refactor to source-type-keyed partial indexes:** separate unique
     constraint per source_type, with NULL columns excluded. Cleaner
     but requires schema change.
   - **Document as a known limitation** if defense-in-depth gap is
     acceptable (because primary dedup happens elsewhere).

3. **Always write a test that asserts the EXPECTED behavior under
   NULL columns.** If you expect dedup → `assert n_rows == 1`. If you
   expect NULLS DISTINCT pass-through → `assert n_rows == 2` (with a
   docstring explaining why this is current Postgres reality). When
   semantics change (NULLS NOT DISTINCT migration), the test fails
   loudly.

### Related

[[postgres-array-concat-null-poisoning]] — Same family of NULL-semantics
landmines. ANSI-SQL's NULL semantics propagate through operators and
comparisons in ways that are non-obvious; always audit explicitly.

## Synthetic test domains stress agent enrichment latency budgets (2026-05-18)

Real customer domains usually have rich web presence (homepages,
LinkedIn pages, news articles) that AI account-enrichment agents can
research in 15-90 seconds. Synthetic test domains with UUID suffixes
(e.g., `cold-prospect-{uuid12}.com`) have ZERO web presence — Tavily
returns "no results" for every query, and the agent retries with
increasingly broad queries until it has SOME synthesis material.
Observed: 145s for `cold-prospect-f1c4290c2155.com` vs the agent's
nominal 30-90s budget.

### Evidence

Phase-1-email-pipeline M5 E2E (2026-05-18). HTTP client at
`live-transcription-fastapi/services/agent_action_core_client.py:43`
had `_DEFAULT_TIMEOUT_SECONDS=120.0`. Agent took 145s; workflow's
`client.enrich()` raised `httpx.ReadTimeout`; DBOS step retried;
each retry hit the same timeout; queue stuck in `status='creating'`;
account created as side-effect of the timed-out HTTP call (agent
completed but workflow couldn't receive response).

### How to apply

1. **Production E2E with synthetic domains validates the FAILURE
   case, not the happy path.** Synthetic test domains are great for
   "no web presence" edge case verification but cause artificially
   long agent runs. Either:
   - Use a known-real domain for happy-path E2E (e.g., a public
     company's domain that the agent can enrich quickly), OR
   - Pre-seed the synthetic domain in `accounts` + `account_domains`
     before the test runs so the orchestrator's `lookup_account_by_domain`
     short-circuits before reaching the agent path.

2. **HTTP client timeout must accommodate worst-case agent latency,
   not nominal.** 120s default was set for the 30-90s nominal range.
   Real customer scenarios that stress this: stealth-mode startups,
   new companies, internationalized domains, sites blocked from
   crawlers. Bump to 300s (5 min) at minimum; consider stream-mode
   or async run_id polling for genuine long-running enrichment.

3. **Workflow step retries don't help if every attempt hits the
   same timeout.** DBOS `max_attempts=5` with exponential backoff
   sounds robust but is useless when every retry calls the same slow
   endpoint with the same insufficient timeout. The retry budget
   exists for transient errors, not for "we always need 145s and
   you give us 120s."

4. **Side-effecting calls that "time out" but actually complete are
   silent data-quality risks.** The agent created an `accounts` row,
   but the workflow never recorded function 3 success. The row is
   orphaned (no link from any workflow state). A future workflow
   retry could create a SECOND account for the same domain (depending
   on agent dedup). Always check side-effects when a long-running
   downstream call appears to fail.

### Related

This lesson sits next to [[feedback_codex_pre_merge_gate]] and
[[production-e2e-non-substitutable]]: production E2E surfaced this
bug too. Mock-based unit tests can't simulate agent latency
characteristics for synthetic test data.

## Skip-marked contract tests silently lose contract enforcement (2026-05-18)

A contract test that gates its execution on environment state (e.g.,
`@pytest.mark.skipif(not os.environ.get("X"))`) silently passes when
the environment isn't configured. In CI the marker fires, the test is
skipped, CI reports green. Nobody sees the skip; nobody runs the test
locally because the contract feels "already covered." Over months, the
upstream service's contract can drift arbitrarily far from what the
test asserts. When something finally runs the test against production
(or, more likely, hits production directly and discovers the drift),
the bug looks brand-new but has been latent the whole time.

### Evidence

Phase-1-email-pipeline M5.2 production E2E (2026-05-18) discovered
that eq-agent-action-core's `/api/enrich` response shape had been a v2
envelope (`{"run_id", "status", "result": {"company_name", ...}, "metadata", "account_id"}`)
since 2026-03-04, while live-transcription-fastapi's `AccountProfile`
Pydantic model still expected the v1 flat shape (`{"name", "domain", ...}`).
The contract-pinning test at `tests/contract/test_agent_enrich_response_shape.py`
was marked `@needs_internal_jwt` — it ran only when `INTERNAL_JWT_SECRET`
was set. In CI that env var isn't injected; in local development nobody
ran the marker manually. The drift went undetected for 2+ months. M5.2's
timeout fix (Fix #1) was the first time a production workflow waited
long enough to actually validate the response body, surfacing the bug.

### How to apply

1. **Skip-markers on contract tests are a contract anti-pattern.** A
   skipped test is not a contract. Pick one of:
   - **Run the test in CI** with a long-lived JWT injected via secrets,
     or with the secret rotated per-run. Cost: secrets management.
     Benefit: drift surfaces on every PR.
   - **Replace with a Testcontainers-style synthetic** that stubs the
     upstream service and asserts the parser handles the documented
     shape. Cost: stub maintenance. Benefit: tests run unconditionally
     but only verify "our parser matches the documented contract"
     (doesn't catch upstream drift in real prod).
   - **Both:** unit-test the parser against a recorded sample of the
     documented shape (always runs); separately have a manual or
     scheduled live contract test (catches real drift).
2. **Inventory all `@needs_*` skip markers.** Anywhere a contract test
   gates on auth/secrets/external-state, the contract enforcement is
   load-bearing on whoever remembers to run it. Migrate to one of the
   patterns above.
3. **Whenever you write a skip marker for a contract test, also write
   a `# WARNING: this test SKIPS in CI without X. Drift will go undetected
   until something runs it manually.` comment** so the next reader knows
   the test isn't actually defending the contract.

### Related

[[production-e2e-non-substitutable]] — the M5.2 E2E also surfaced this.
Production end-to-end remains the only way to discover certain classes
of bugs, but it's a slow signal; contract tests in CI catch drift fast
IF they actually run.

## Two-system idempotency via shared DB UNIQUE constraint coordinates dual creators (2026-05-18)

When two independent systems both create rows in the same table for
the same logical entity, the safe design is a **single shared
idempotency key enforced by a Postgres UNIQUE constraint** — not code
coordination, not message-passing, not feature flags. The database
becomes the source of truth for "who got there first," and both
systems can be written as if they were the only creator. Whichever
runs first inserts; whichever runs second sees a conflict and either
updates or no-ops.

### Evidence

Phase-1-email-pipeline M5.2 (2026-05-18) discovered that
eq-agent-action-core's `/api/enrich` creates `accounts` + `account_domains`
rows as a side-effect (since 2026-03-04). The live-transcription-fastapi
workflow's Step 4 `resolve_or_create_account` ALSO creates accounts.
Initial reaction: "this is a duplicate-creator bug, we need to centralize
ownership." Investigation: both systems gate on the same key —
`account_domains.(tenant_id, domain)` UNIQUE INDEX. The agent's create
path uses `SELECT account_id FROM account_domains WHERE tenant_id=$1 AND domain=$2`
before INSERT; the workflow does the same. Whichever runs first creates;
the other sees the existing row and reuses the account_id. **No
duplicates, no race, no coordination needed.**

### How to apply

1. **Before "centralizing" duplicate writers, check for a shared UNIQUE
   constraint.** If both systems gate on the same DB-enforced key, the
   "duplicate" architecture is actually two independently-correct
   readers/writers of a single source of truth. Centralization would
   add code without adding correctness.
2. **The UNIQUE constraint must be on the LOGICAL identity, not the
   surrogate key.** A UUID PK doesn't coordinate two creators because
   each creator generates its own UUID. The right key is the
   business-domain tuple — `(tenant_id, domain)` here. Postgres
   UNIQUE on (tenant_id, domain) makes "one account per business
   domain per tenant" an invariant, regardless of who creates it.
3. **Both creators must be designed as upsert-readers**, not pure
   inserts. The pattern is: `SELECT by-business-key → if exists,
   UPDATE that row → if not, INSERT new row + new business-key row`.
   Single-INSERT writers would crash on conflict; upsert-readers
   handle both first-write and second-write cleanly.
4. **Don't paper over this pattern with code centralization.** A
   review reflex "two writers = bad, centralize" can lead you to
   introduce a service or queue that adds latency, code, and failure
   modes — for a correctness problem that didn't exist.

### Related

[[cross_repo_deploy_coordination_for_constraint_relaxation]] — the
M1+M2 session's lesson is the inverse of this: when relaxing a UNIQUE,
audit every code path that depended on it. Here, ADDING a second writer
on top of an existing UNIQUE is safe because the constraint absorbs
the new writer's race.

## Timeout fixes can expose latent shape bugs in downstream APIs (2026-05-18)

A timeout fix that lets a slow downstream service finish responding
can expose latent bugs in the downstream's response shape — bugs that
were hidden because earlier timeouts cut the connection before the
response body was read or validated. The timeout fix doesn't CAUSE
the shape bug; it makes the bug REACHABLE. Diagnosing the post-fix
behavior as "a new bug introduced by the timeout fix" wastes time;
the bug is older than the fix.

### Evidence

Phase-1-email-pipeline M5.2 Fix #1 (2026-05-18) bumped the agent
client's read timeout from 120s to 300s. The next production E2E
errored at the agent-validation step with "name field required" —
which looked like the fix had broken something. Investigation: the
agent had returned a v2 envelope shape (no top-level `name`) since
2026-03-04 — over 2 months earlier. Every prior workflow call timed
out at 120s before reading the response body, so the workflow's
Pydantic validator never saw the response. The 300s timeout was the
first time the validator could fire end-to-end; the latent shape
mismatch surfaced immediately.

### How to apply

1. **When a timeout fix exposes a new failure mode at a downstream
   service, the first hypothesis should be "latent bug newly reachable,"
   NOT "the timeout fix broke something."** Check the downstream's
   git log for the response-handling code path; if it hasn't changed
   recently, the shape was always wrong, just unreachable.
2. **Run the downstream's contract test (if any) against production
   manually.** A skipped contract test (see "Skip-marked contract
   tests silently lose contract enforcement") might be the bug class
   responsible for the latent drift.
3. **Test directly with curl before assuming the bug is in your code.**
   For HTTP integrations, a `curl + auth + same payload` smoke test
   answers the question "is the downstream returning what I think?"
   in 30 seconds. Much faster than re-reading your own client code.
4. **Document the latency vs shape failure-mode distinction in the
   fix's PR.** The timeout fix's PR should call out "this fix exposes
   the system to seeing responses it couldn't see before; downstream
   bugs may surface as a result." Future incident responders see this
   and don't waste time blaming the timeout fix.

### Related

[[postgres-unique-indexes-default-nulls-distinct]] — same family: M4's
reachability change exposed M5.1's latent ON CONFLICT bug, just as
M5.2's timeout fix exposed Bug #4's latent shape drift. Any
"reachability-increasing" change creates a wave of newly-visible
latent bugs downstream of where it lands.

## Multi-writer Neo4j MERGE-key coordination is its own bug class (2026-05-18)

When two services both write to the same logical Neo4j node but use
different MERGE keys, the system is structurally broken: the second
writer can neither find nor create the node, and idempotency guards
designed for single-writer partial retries don't help because the
problem is upstream of "retry" semantics. This is a DIFFERENT bug
class from single-writer partial-retry corruption (which a 2-layer
guard correctly bounds). Multi-writer collision can stay invisible
for months if the second writer's path was previously unreachable
(e.g., gated by an earlier failure that's now been fixed).

### Evidence

Phase-1-email-pipeline M5.3 production E2E (2026-05-18) discovered
that eq-email-pipeline's `EmailPromoted` handler can never complete
local enrichment because eq-structured-graph-core's earlier
`EnvelopeV1.email` consumer ALREADY created the Neo4j Interaction
node — by `(tenant_id, interaction_id)`, WITHOUT setting
`internet_message_id`. eq-email-pipeline's `build_skeleton` then
`MERGE (i:Interaction {tenant_id, internet_message_id})` can't find
the existing node (no internet_message_id property), falls through
to its CREATE fallback, and trips the `(tenant_id, interaction_id)`
UNIQUE constraint. Every retry hits the same wall → DLQ. Latent
since M3 deployed 2026-05-17; surfaced only when M5.3 made the
workflow finally reach SUCCESS (the first production run that
exercised both Step 5 emit → eq-structured-graph-core AND Step 6
emit → eq-email-pipeline). The 2-layer guard (Layer 1: completed_at
hard guard, Layer 2: started_at TTL) prevents data corruption but
not completion.

### How to apply

1. **When two services write to the same Neo4j node type, document
   the MERGE-key contract explicitly.** A single source of truth
   for "how do we identify this node uniquely" — both writers must
   share it. RFC 5322 internet_message_id is fine for emails, but
   if the producer doesn't set it, MERGE-by-it can't reconcile.
2. **The fix can be at consumer (Option A: change MERGE key), producer
   (Option B: set the missing field), or shared (Option C: define a
   contract document). Choose by blast radius.** Cross-service code
   change to a producer that has multiple consumers (transcripts +
   emails + future signals) has higher blast radius than a
   single-consumer change.
3. **Before coding, use /codex consult + /plan-eng-review.** Cross-
   service architectural changes warrant adversarial review. /codex
   challenge mode is especially valuable: explicitly ask Codex to
   find ways the fix could break OTHER consumers of the shared graph.
4. **Don't conflate this with documented V1 limitations on
   single-writer partial retry.** Those limitations cover within-a-
   single-handler retry corruption (bounded by 2-layer guard).
   Multi-writer collision is upstream of retry; the 2-layer guard
   doesn't help.
5. **When a "newly-reachable" bug surfaces in production after an
   upstream fix, check whether the downstream path has changed in
   the same window OR whether something new is now arriving at it
   that never arrived before.** Both can produce the same symptom.

### Related

[[timeout-fixes-expose-latent-shape-bugs]] — same general pattern:
M5.3's workflow-SUCCESS landing is what made the eq-email-pipeline
handler's Step 5+6 emit path reachable for the first time, exposing
the multi-writer collision that had been latent since M3 deployed.
Reachability-increasing changes (timeout fixes, parser fixes,
workflow-completion fixes) create waves of newly-visible bugs.

## Railway MCP `deployment_logs` returns runtime logs despite description (2026-05-18)

The `mcp__railway__deployment_logs` tool's description says it's for
"build output" and "Not for: Service runtime logs" — but it ACTUALLY
returns runtime logs from the running container. This discovery
unblocked M5.4's investigation; without it, runtime-log access would
have required `railway login` (interactive auth not available to AI
agents) or GCP Application Default Credentials (also interactive).

### Evidence

Phase-1-email-pipeline M5.3 session (2026-05-18) couldn't get
runtime logs for eq-email-pipeline via:
- `railway logs` CLI → "Unauthorized. Please login with `railway login`."
- `mcp__observability__list_log_entries` → "Could not load the
  default credentials" (needs `gcloud auth application-default
  login`).

Trying `mcp__railway__deployment_logs` on the latest SUCCESS
deployment (despite the tool description) returned full runtime
stack traces including the `neo4j.exceptions.ConstraintError` that
proved M5.4's hypothesis. The tool's actual behavior: it returns
ALL logs from the deployment (build + runtime), not just build.

### How to apply

1. **For any Railway service diagnostics, try
   `mcp__railway__deployment_logs` FIRST** — bypass the interactive
   auth flows the CLI and GCP MCP require.
2. **Use the latest SUCCESS deployment ID** from
   `mcp__railway__deployment_list`. Older "REMOVED" deployments
   may have less detailed runtime data.
3. **Increase the `limit` parameter** (default 50 is too few for
   serious diagnostics). 200-500 is reasonable for an investigation;
   1000+ for a deep dive.
4. **Tool descriptions can lie.** Always try the tool before
   concluding it can't do what you need.
5. **Save the Railway project + service + environment IDs once**
   to a reference memory entry. Searching the project list every
   session wastes context.

### Related

Saved a [[reference-railway-project-ids]] memory entry with all
relevant project + service + environment IDs for the Contact
Quality initiative's 5 services (live-transcription-fastapi,
eq-email-pipeline, eq-agent-action-core, action-item-graph,
eq-structured-graph-core).

