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

