# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative — multi-phase data-quality foundation for an AI-native customer intelligence platform.
**Last session:** 2026-05-18 (M5.2 verification ran — shipped, merged, and deployed all 3 documented bug fixes (httpx timeout, INGEST_SUCCESS_STATUSES, NULLS NOT DISTINCT). Re-ran plan §10.3 on a fresh UUID: Steps 1-5 PASS empirically. Step 6 BLOCKED by **Bug #4** — a previously-undetected contract drift in eq-agent-action-core that has been latent since 2026-03-04. M5.3 queued: adapter fix in live-transcription-fastapi (NOT in eq-agent-action-core).)
**Status:** ⚠️ **PHASE_1_EMAIL_PIPELINE_M5.2_SHIPPED_BUG4_FOUND_M5.3_NEXT** — All 3 M5.2 fixes deployed + production-verified through Step 5. Bug #4 is the LAST blocker for Phase-1-email-pipeline sign-off.

---

## SESSION SCOPE FOR THE NEXT SESSION

**This session is M5.3 — ship the adapter fix for the eq-agent-action-core v2 response shape, then complete the §10.3 + §11 verification.**

The strategic decision (made during M5.2 with full investigation, no shortcuts):
- **Fix is in live-transcription-fastapi** (the consumer), **NOT in eq-agent-action-core** (the producer).
- The agent's v2 shape has been in production since 2026-03-04 (NOT new, NOT the concurrent cpo-mvp refactor's fault).
- Both systems already coordinate correctly via `account_domains.(tenant_id, domain)` UNIQUE constraint — no architectural reconciliation needed.
- Two items deferred from M5.3 with rationale documented: see "Deferred items" section below.

| Item | M5.3 scope | Description |
|---|---|---|
| 1 | **IN SCOPE** | Update `services/account_provisioning/types.py:AccountProfile` — mirror agent v2 schema with Pydantic field aliases (`name` ← `company_name`, `website` ← `website_domain`, plus optional new fields like `headquarters`, `founded_year`, `employee_count_range`, `company_type`). |
| 2 | **IN SCOPE** | Update `services/agent_action_core_client.py:_parse_profile` — unwrap the v2 envelope: validate `response.json()["result"]` instead of `response.json()` against `AccountProfile`. Handle the new top-level fields (`run_id`, `status`, `metadata`, `account_id`) — log `run_id` for traceability; ignore the agent's `account_id` (our workflow's `resolve_or_create_account` does its own idempotent lookup). |
| 5 | **IN SCOPE** | Add a unit test in `tests/unit/account_provisioning/test_agent_client.py` that uses `httpx.MockTransport` to inject a v2-envelope-shaped response and asserts the parser correctly extracts the AccountProfile. This is the regression guard that locks our parser to the documented v2 contract. |
| 3 | **DEFERRED** | Use the agent's `account_id` in the response to short-circuit Step 4's `resolve_or_create_account` lookup. **Why deferred:** pure perf optimization, not a correctness fix. Revisit when we have load data showing the extra DB roundtrip matters. |
| 4 | **DEFERRED** | Make the contract-pinning test `test_agent_enrich_response_shape.py` actually run in CI (today it's `@needs_internal_jwt`-marked and silently skips). **Why deferred:** touches CI infrastructure + secrets management; warrants its own PR with its own review surface. Tracked as a follow-up issue. The unit test in item 5 provides regression coverage at the parser layer without needing the CI plumbing. |

Estimated work: **~1 hour code + ~15 min Codex review + ~30 min E2E re-run + §11 walk + handoff**.

---

## CRITICAL — what's verified empirically end-to-end (production)

| Plan §10.3 step | Behavior | M5 (pre-M5.2) | M5.2 (this session) | Status |
|---|---|---|---|---|
| Step 1 | Synthesize cold-inbound email | ✓ | ✓ | PASS |
| Step 2 | Orchestrator §4.2 BUSINESS path fires | ✓ (HTTP 500 wrapper bug) | ✓ HTTP 200 (Fix #2 verified) | PASS |
| Step 3a | `pending_interactions` row exists | ✓ | ✓ (re-verified fresh UUID) | PASS |
| Step 3b | `pending_account_mappings` row, status='pending' | ✓ | ✓ | PASS |
| Step 3c | `pending_account_mapping_signals` row | ✓ | ✓ | PASS |
| Step 3d-f | NO raw_interactions/emails/summaries | ✓ | ✓ | PASS |
| Step 4 | Duplicate webhook → `skipped_duplicate`, n_pending=1 | ✓ | ✓ (Fix #3's NULLS NOT DISTINCT verified — pending_signal_dedup now correctly applies to email signals with calendar_event_id=NULL) | PASS |
| Step 5 | POST `/approve` returns 202 + correct workflow_id | ✓ | ✓ | PASS |
| Step 6 | DBOS workflow reaches `status='success'` | ❌ httpx timeout at function 3 | ❌ workflow ran 532s (Fix #1's 300s budget worked — no timeout), but errored at function 3's response validation: `AgentEnrichTerminalError: name field required` | **BLOCKED by Bug #4** |
| Steps 7-12 | Promote → enrichment → idempotency → downstream | ⏳ NOT REACHED | ⏳ NOT REACHED | Pending M5.3 |

**M5.2 specifically verified:**
- Fix #1 (httpx timeout 120 → 300s with per-phase split): workflow patiently waited 8.8 minutes; agent calls completed without timeout. Read budget gives ~107% headroom over observed worst case.
- Fix #2 (`_INGEST_SUCCESS_STATUSES` includes `pending_account_approval`): synthetic injection returned HTTP 200 (was HTTP 500 in M5).
- Fix #3 (Prisma migration `NULLS NOT DISTINCT` on `pending_signal_dedup`): production index updated; duplicate webhook test (Step 4) confirms email-signal dedup now functions.

### Production state at session close (2026-05-18 end-of-day)

- **eq-frontend main HEAD**: `c3bc162` (M5.2 Fix #3 PR #398 merged + Vercel deployed; Prisma migration `20260518142500_m5_2_pending_signal_dedup_nulls_not_distinct` applied to production Neon).
- **live-transcription-fastapi main HEAD**: `929472e` (M5.2 Fix #1 PR #20 merged + Railway deployed; per-phase timeouts live). Plus the M5.3-handoff commit (this commit, see "Handoff artifacts" below).
- **eq-email-pipeline main HEAD**: `8b2c67a` (M5.2 Fix #2 PR #12 + M5.2 Fix #3 test follow-up PR #13 merged + Railway deployed).
- **Test tenant `11111111-...` baseline restored**: 0 active pending_interactions, 0 active queue rows, 0 active signals, 0 orphan account (Bug #4's E2E artifacts cleaned per LOCKED-11 atomic transaction). DBOS workflow `queue-ef3251a0-...:approval-ae2ede13-...` set to CANCELLED.
- **Neo4j, Pinecone, EventBridge, SQS**: untouched (Steps 7-12 not reached, so no enrichment writes happened).

---

## BUG #4 — full diagnostic (load-bearing for the next session)

### Plain English summary

The agent service `eq-agent-action-core` returns a different response shape than our `AccountProfile` Pydantic model expects. Our model expects a flat `{"name": "...", "domain": "...", ...}`. The agent has been returning a v2 envelope `{"run_id": "...", "status": "completed", "result": {"company_name": "...", "website_domain": "...", ...}, "metadata": {...}, "account_id": "..."}` **since 2026-03-04**. Pydantic correctly says "name field required" because the top level only has `run_id`, `status`, `result`, `metadata`, `account_id` — no `name`.

### Why it was undetected for 2+ months

1. **Contract test silently skipped.** `tests/contract/test_agent_enrich_response_shape.py` is marked `@needs_internal_jwt`. CI doesn't inject `INTERNAL_JWT_SECRET`, so the test SKIPS in CI without failing or warning. Nobody saw the skip; nobody ran it locally.
2. **Prior production attempts timed out.** Every prior call to `/api/enrich` for a sparse-web synthetic domain timed out at httpx's old 120s budget BEFORE the workflow read the response body. Pydantic never validated. The bug was unreachable.
3. **M5.2's Fix #1 was the first reachability change** that let the workflow wait long enough to actually receive and validate the response. The latent bug surfaced on the very first end-to-end run.

### Not the concurrent refactor's fault

There is a `feat/cpo-mvp-enhancements` branch in eq-agent-action-core actively being developed by another agent. None of its commits touch `/api/enrich` or the response shape. The v2 shape lives in `src/eq_agent/api/enrich_routes.py` + `src/eq_agent/agent_url_enrichment/schemas.py` + `src/eq_agent/agent_url_enrichment/finalize.py`. Git log shows those files haven't been modified since **2026-03-04** (commit `e301b38`).

### Why the fix is in live-transcription-fastapi, not eq-agent-action-core

The agent's v2 response includes its own `account_id` field. We checked: the agent's account-creation code at `src/eq_agent/db/accounts.py:25-155` uses `account_domains.(tenant_id, domain)` UNIQUE for idempotency. Our workflow's Step 4 `resolve_or_create_account` uses the same key. The two systems coordinate correctly via the DB constraint. There's no architectural problem to fix; only a parser-shape problem.

If we tried to "fix" the agent (revert v2 → v1), we'd:
- Break other consumers of the agent that may already depend on v2 (executive intelligence agent etc.).
- Lose the v2 schema's richer fields (`founded_year`, `primary_products`, `customer_segments`, `differentiators`, `crm_summary`, etc.) that future Phase 2 work will want.
- Introduce coordination overhead with the cpo-mvp work in flight.

Fix in our consumer; let the agent's contract evolve forward. This is the cutting-edge-startup move.

### What the v2 response shape actually looks like

```json
{
  "run_id": "bfd58a9e-11f2-477a-a1a7-2d3c9407e648",
  "status": "completed",
  "result": {
    "tenant_id": "...",
    "input_url": "https://cold-prospect-15f6b36318b8.com",
    "company_name": "Cold Prospect",
    "website_domain": "cold-prospect-15f6b36318b8.com",
    "headquarters": null,
    "industry": null,
    "founded_year": null,
    "employee_count_range": null,
    "company_type": null,
    "primary_products": [],
    "customer_segments": [],
    "data_delivery_channels": [],
    "one_line_description": null,
    "crm_summary": null,
    "differentiators": [],
    "recent_notable_updates": [],
    "crm_fields": [{"key": "Company Name", "value": "Cold Prospect"}],
    "sources": [{"url": "...", "title": "..."}, ...],
    "enriched_at": "2026-05-18T15:25:16.417655+00:00",
    "schema_version": "2.0.0"
  },
  "metadata": {
    "duration_ms": 124314,
    "sources_count": 10,
    "research_loops": 3
  },
  "account_id": "02cd2d65-78ad-4a97-9c9f-e0bc41d407bf"
}
```

Field mapping from agent → our `AccountProfile`:
| Agent field (under `.result`) | Our `AccountProfile` field |
|---|---|
| `company_name` | `name` (required) |
| `website_domain` | `domain` + `website` |
| `industry` | `industry` |
| `headquarters` | `region` |
| `employee_count_range` | `company_size` |
| `company_type` | (new, optional, add to AccountProfile) |
| `one_line_description` | `description` |
| `founded_year`, `primary_products`, `customer_segments`, `differentiators`, etc. | Optionally add as new fields; `ConfigDict(extra="allow")` accepts them as forward-compat anyway |

---

## Mandatory read order for the next session (~15 min)

1. **This file.**
2. **The checkpoint** loaded via `/context-restore` (the 2026-05-18 save titled `phase-1-email-pipeline-m5.2-shipped-bug4-m5.3-next`).
3. **THE PLAN — §10.3 + §11**: `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md`. Same load-bearing artifact as M5/M5.2; M5.3 resumes from Step 6 once the fix ships.
4. **The 3 new lessons codified end-of-M5.2** at the bottom of `tasks/lessons.md`:
   - "Skip-marked contract tests silently lose contract enforcement"
   - "Two-system idempotency via shared DB UNIQUE constraint coordinates dual creators"
   - "Timeout fixes can expose latent shape bugs in downstream APIs"
5. **The agent's account-creation code** (read for context, NOT to modify):
   `/Users/peteroneil/EQ-CORE/eq-agent-action-core/src/eq_agent/db/accounts.py:25-155` — confirms agent uses `account_domains` UNIQUE for idempotency.
6. **M5.2 merged PRs** for deployed-behavior narrative:
   - eq-frontend #398 (Fix #3): https://github.com/oneilstokeseqrm/eq-frontend/pull/398
   - live-transcription-fastapi #20 (Fix #1, Codex R3 CLEAN): https://github.com/oneilstokeseqrm/live-transcription-fastapi/pull/20
   - eq-email-pipeline #12 (Fix #2): https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/12
   - eq-email-pipeline #13 (Fix #3 test follow-up): https://github.com/oneilstokeseqrm/eq-email-pipeline/pull/13

---

## Execution sequence — M5.3

### Pre-flight (run BEFORE any M5.3 work)

1. **Production health (all 3 services):**
   ```bash
   curl -sS -o /dev/null -w "live-fastapi: %{http_code}\n" \
     https://live-transcription-fastapi-production.up.railway.app/health
   curl -sS -o /dev/null -w "eq-email-pipeline: %{http_code}\n" \
     https://email-pipeline-production.up.railway.app/api/ping
   curl -sS https://email-pipeline-production.up.railway.app/api/health
   curl -sS -o /dev/null -w "eq-agent-action-core: %{http_code}\n" \
     https://eq-agent-action-core-production.up.railway.app/openapi.json
   ```
   Expected: 200 / 200 / status=ok / 200.

2. **M5.2 code is live on each origin/main:**
   ```bash
   git -C /Users/peteroneil/EQ-CORE/live-transcription-fastapi log --oneline -3
   # Expected: top is the docs(handoff) commit, then 929472e M5.2 Fix #1
   git -C /Users/peteroneil/eq-email-pipeline log --oneline -3
   # Expected top: 8b2c67a test follow-up, then ceea064 Fix #2, then 79862b6 M5.1
   git -C /Users/peteroneil/eq-frontend log origin/main --oneline -3
   # Expected top: c3bc162 Fix #3 (NULLS NOT DISTINCT migration)
   ```

3. **LOCKED-17 collision check:**
   ```bash
   ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head -10
   ```
   Any file modified in last hour = pause + ask user. **KNOWN concurrent agent: cpo-mvp work on eq-agent-action-core's persona/composition code** — does NOT touch `/api/enrich` or the test tenant. Safe to proceed.

4. **DBOS workflow drain check:**
   ```sql
   SELECT * FROM dbos.workflow_status
   WHERE status IN ('PENDING', 'RUNNING')
     AND created_at > (EXTRACT(EPOCH FROM NOW()) * 1000 - 3600000);
   ```
   Expected: 0 rows.

5. **Baseline pending_interactions count in test tenant:**
   ```sql
   SELECT COUNT(*) FROM pending_interactions
   WHERE tenant_id = '11111111-1111-4111-8111-111111111111'
     AND archived_at IS NULL;
   ```
   Expected: 0 (M5.2 cleanup left clean slate).

6. **Verify production Neon index is NULLS NOT DISTINCT:**
   ```sql
   SELECT indexdef FROM pg_indexes WHERE indexname = 'pending_signal_dedup';
   ```
   Expected: contains `NULLS NOT DISTINCT`.

If any fail, STOP and surface.

### Step 1 — Code changes (live-transcription-fastapi)

Branch off main: `fix/m5-3-agent-v2-response-adapter`.

**File 1: `services/account_provisioning/types.py`**

Update `AccountProfile`:

```python
class AccountProfile(BaseModel):
    """Agent enrichment payload (v2 schema as of 2026-03-04).

    Mirrors the agent's `/api/enrich` response shape under the `result`
    envelope key. Field aliases map the agent's v2 names to our local
    semantics (e.g., `company_name` → `name`). Fields not used by the
    workflow are tolerated via `extra="allow"` for forward-compat.
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # Required.
    name: str = Field(..., alias="company_name")

    # Optional — used by Step 5 materialization to populate the accounts row.
    domain: Optional[str] = Field(default=None, alias="website_domain")
    industry: Optional[str] = None
    company_size: Optional[str] = Field(default=None, alias="employee_count_range")
    region: Optional[str] = Field(default=None, alias="headquarters")
    website: Optional[str] = Field(default=None, alias="website_domain")
    description: Optional[str] = Field(default=None, alias="one_line_description")
    company_type: Optional[str] = None
```

**File 2: `services/agent_action_core_client.py`**

Update `_parse_profile` to unwrap the v2 envelope:

```python
@staticmethod
def _parse_profile(response: httpx.Response) -> AccountProfile:
    try:
        data = response.json()
    except ValueError as exc:
        raise AgentEnrichTerminalError(
            f"Agent response was not valid JSON: {response.text[:200]}"
        ) from exc
    if not isinstance(data, dict):
        raise AgentEnrichTerminalError(
            f"Agent response was not a JSON object: type={type(data).__name__}"
        )
    # M5.3 (2026-05-19): the agent's v2 schema (since 2026-03-04) wraps the
    # enrichment payload under .result. The top-level has run_id, status,
    # metadata, account_id — we use run_id for traceability/logging but
    # validate AccountProfile against the .result subobject.
    result = data.get("result")
    if not isinstance(result, dict):
        raise AgentEnrichTerminalError(
            f"Agent response missing 'result' envelope: keys={list(data.keys())}"
        )
    try:
        profile = AccountProfile.model_validate(result)
    except ValueError as exc:
        raise AgentEnrichTerminalError(
            f"Agent response did not match AccountProfile contract: {exc}"
        ) from exc
    return profile
```

(Optionally log `data.get("run_id")` + `data.get("account_id")` for traceability — keep it minimal.)

**File 3: `tests/unit/account_provisioning/test_agent_client.py`**

Add a new test that injects a v2-envelope response shape and asserts the parser extracts the right fields:

```python
@pytest.mark.asyncio
async def test_enrich_handles_v2_envelope_shape():
    """M5.3: agent returns a v2 envelope (since 2026-03-04). Parser must
    unwrap the .result subobject and apply field aliases.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "run_id": "abc-123",
            "status": "completed",
            "result": {
                "tenant_id": "...",
                "input_url": "https://acme.com",
                "company_name": "Acme Inc",
                "website_domain": "acme.com",
                "industry": "Software",
                "headquarters": "San Francisco",
                "employee_count_range": "50-200",
                "company_type": "Private",
                "schema_version": "2.0.0",
            },
            "metadata": {"duration_ms": 5000},
            "account_id": "acc-xyz",
        })

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        profile = await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()

    # Required field.
    assert profile.name == "Acme Inc"
    # Aliased fields.
    assert profile.domain == "acme.com"
    assert profile.industry == "Software"
    assert profile.region == "San Francisco"
    assert profile.company_size == "50-200"
    assert profile.website == "acme.com"
    # Unknown fields (run_id, status, metadata, account_id) are ignored
    # by AccountProfile but should be accessible via model_dump if forward-compat
    # callers need them. We don't assert on extras here — just that parsing
    # succeeded.


@pytest.mark.asyncio
async def test_enrich_rejects_v2_response_missing_result_envelope():
    """If the agent ever drops the `result` envelope, fail loud."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"run_id": "abc", "status": "completed"})

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        with pytest.raises(AgentEnrichTerminalError, match="missing 'result' envelope"):
            await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_enrich_rejects_v2_response_missing_company_name():
    """If agent's .result is present but missing company_name (the v2 name
    of `name`), fail loud — this is the bug #4 surface from M5.2.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "run_id": "abc",
            "status": "completed",
            "result": {"website_domain": "acme.com", "industry": "Software"},
        })

    transport = httpx.MockTransport(handler)
    client = AgentActionCoreClient(base_url="http://test.example.com")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(30))

    try:
        with pytest.raises(AgentEnrichTerminalError, match="did not match AccountProfile"):
            await client.enrich(url="acme.com", jwt="tok")
    finally:
        await client.aclose()
```

Also delete or update `test_missing_required_field_raises_terminal` if it relied on the old top-level shape — pre-M5.3 it sent `{"domain": "acme.com"}` (no `name`); post-M5.3 the parser would now reject that BEFORE seeing AccountProfile because there's no `.result` envelope. Adapt the test to use the envelope shape with missing `company_name`.

### Step 2 — Codex review BEFORE merge (LOCKED-10)

Codex CLI rate-limit should be lifted by 11:31 AM EDT 2026-05-18 (well before this next session opens). Run `/codex review` and fold all P0/P1 findings; consider P2/P3 per trajectory.

### Step 3 — Push + open PR + user merge auth + Railway deploy verify

Same pattern as M5.2 PR #20. Railway redeploy typically 1-2 minutes; verify `/health` 200.

### Step 4 — Re-run plan §10.3 Steps 1-12 on a FRESH UUID

Use a NEW UUID (not `15f6b36318b8` from M5.2's run — that one was cleaned up but tracker discipline says fresh anyway). Walk Steps 1-12 sequentially. With M5.3's parser fix:
- Step 6 should reach `status='SUCCESS'`.
- Step 7 should show: account row with `name='Cold Prospect <uuid>'`, raw_interactions row, emails row with thread_id populated, interaction_summaries row, interaction_contact_links row(s).
- Steps 8-9 verify the EmailPromoted handler runs end-to-end: Neo4j Interaction node + BELONGS_TO → Account; Pinecone vector; `emails.local_enrichment_completed_at` set.
- Step 10: handler idempotency (re-emit EmailPromoted via boto3; assert no new Neo4j nodes; message_count unchanged).
- Step 11: action-item-graph + eq-structured-graph-core downstream consumers received and processed.

### Step 5 — Walk plan §11 22-item invariants checklist

Verify each:
- **Schema (8 items)**: via Neon pg_indexes + information_schema.columns.
- **Code (15 items)**: via grep across the 3 repos. Most should pass at M5.2 baseline + the M5.3 parser fix.
- **Contracts (3 items)**: via `scripts/verify_consumer_contracts.py` + `scripts/verify_schema.py`.
- **Behavior/E2E (8 items)**: covered by the §10.3 walk in Step 4 above.

### Step 6 (OPTIONAL — ASK USER FIRST per LOCKED-11) — Plan §10.4 rollback drill

Recommended only if user explicitly approves. Out of scope for default sign-off.

### Step 7 — Phase-1-email-pipeline initiative SIGN-OFF

If §10.3 Steps 1-12 all PASS AND §11 invariants all hold AND no new P0/P1 bugs surfaced, surface to user with:
- All milestones M1+M2+M3+M4+M5.1+M5.2+M5.3 deployed + verified.
- Plan §10.3 Steps 1-12 all PASS.
- All 22 §11 invariants verified.
- 21 LOCKED decisions list unchanged.
- 4 remaining acknowledged V1 limitations (NULL-DISTINCT moved from limitation framing to fixed bug at M5.2).
- 2 deferred items from M5.3 (items 3 + 4) tracked as follow-ups.
- Phase 2 PLANNING is unblocked.

STOP after sign-off. Phase 2 PLANNING is a separate session.

### Step 8 — END-OF-SESSION HANDOFF

Same pattern as today's handoff: cleanup test tenant if needed → /context-save → rewrite NEXT-SESSION-START-HERE.md for the next initiative → write a dated next-session-prompt.md → update MEMORY.md status → commit + push handoff docs.

---

## LOCKED decisions (21 total; do NOT re-litigate)

[Same 21 as M5/M5.2 sessions — see prior NEXT-SESSION-START-HERE history. M5.3 doesn't add any new LOCKED decisions; the parser-adapter fix is a bug-correction within the existing decision frame.]

---

## Acknowledged V1 limitations (post-M5.2 state)

4 remaining (post-M5.2 fix #3 NULL-DISTINCT moved to fixed-bug status):
1. **Personal/internal anchor cold-inbound log+drop** — V2 roadmap: audit log table.
2. **Neo4j build_skeleton/write_flesh partial-retry corruption** — bounded by 2-layer guard (`local_enrichment_started_at` TTL + `completed_at` hard guard).
3. **Legacy per-signal loop cosmetic duplicate** — re-pointed email signals create cosmetic duplicate 'meeting' summary.
4. **build_skeleton CREATE-fallback for missing internet_message_id** — extends limitation #2 to the missing-header case.

M5.3 does NOT add new V1 limitations. The deferred items (#3 perf, #4 CI contract test) are tracked separately as Phase-2 follow-ups, not V1 limitations.

---

## Stop conditions (hard — surface to user)

- `/context-restore` returns NO_CHECKPOINTS or the wrong checkpoint title.
- MEMORY.md status isn't `PHASE_1_EMAIL_PIPELINE_M5.2_SHIPPED_BUG4_FOUND_M5.3_NEXT`.
- Production /api/health or /health returns non-200.
- Any of the 3 M5.2 commit SHAs is not at the top of its repo's origin/main.
- LOCKED-17 shows another agent recently active in the test tenant or on `/api/enrich` code paths.
- ANY of the M5.3 code changes introduces test regressions beyond the M5.2 baseline.
- §10.3 Step 6 STILL stalls after the M5.3 parser fix deploys → 5th bug surfaced; STOP immediately.
- §10.3 surfaces a new V1 limitation NOT in the documented 4.
- The cpo-mvp refactor's commits start touching `src/eq_agent/api/enrich_routes.py` or `src/eq_agent/agent_url_enrichment/` files — coordinate with the user before proceeding (would invalidate M5.3 scope assumptions).

---

## Handoff artifacts from the prior session (2026-05-18 end-of-day)

- **M5.2 PRs merged + deployed:**
  - eq-frontend #398: `c3bc162` (Prisma `NULLS NOT DISTINCT` migration applied)
  - live-transcription-fastapi #20: `929472e` (httpx per-phase timeout; Codex R3 CLEAN)
  - eq-email-pipeline #12: `ceea064` (`_INGEST_SUCCESS_STATUSES` adds `pending_account_approval`)
  - eq-email-pipeline #13: `8b2c67a` (test/schema follow-up for NULLS NOT DISTINCT)
- **Comprehensive checkpoint**: `~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/<timestamp>-phase-1-email-pipeline-m5.2-shipped-bug4-m5.3-next.md`.
- **The plan (unchanged)**: `/Users/peteroneil/eq-email-pipeline/docs/superpowers/plans/2026-05-17-pending-interactions-cold-inbound-fix.md`.
- **Paste-ready next-session prompt**: `docs/superpowers/specs/2026-05-19-m53-next-session-prompt.md`.
- **3 new lessons codified** in `tasks/lessons.md`:
  - "Skip-marked contract tests silently lose contract enforcement"
  - "Two-system idempotency via shared DB UNIQUE constraint coordinates dual creators"
  - "Timeout fixes can expose latent shape bugs in downstream APIs"

---

## Phase 2 preview (still not Phase 2 scope; gated on Phase 1 sign-off)

Same as M5.2 preview: Neo4j MERGE-everywhere refactor, identity state machine for contacts, outbound cold-outreach capture, EmailPromoted DLQ + observability, queue UI integration. Plus 2 cleanup items lifted from M5.3 deferred:
- Use agent's `account_id` as a perf optimization for Step 4 (defer; revisit with load data).
- Move contract-pinning test to CI with secrets injection (`CONTRACT_TEST_JWT` env var or Testcontainers stub agent).

Phase 2 PLANNING does not start until M5.3 ships AND §10.3 Steps 1-12 + §11 invariants all PASS.
