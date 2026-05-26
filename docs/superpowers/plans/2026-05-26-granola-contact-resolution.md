# Granola Contact Resolution & Linking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Granola-ingested meetings resolve, create, and link contacts (Postgres + Neo4j graph) for known-company attendees exactly like the email/transcript paths — without editing the shared Lane 2 code.

**Architecture:** A new standalone, race-safe `find_or_create_contact` helper resolves each known-account attendee to a `contacts` row. Inside `_ingest_scenario_a`, we resolve once into a single `resolved_contacts` list, then feed it down BOTH channels: (Lane 1) into the published envelope's `extras.contact_ids`/`extras.contacts[]` so downstream builds the Neo4j contact graph, and (Lane 2) into `Lane2Extras(contact_ids=...)` so `intelligence_service._persist_contact_links` writes the `raw_interactions` → `interaction_summaries` → `interaction_contact_links` FK chain. We do NOT modify `transcript_enrichment.py`, `intelligence_service.py`, or `text_clean_service.py`.

**Tech Stack:** Python 3.11, FastAPI, asyncpg + SQLAlchemy async sessions (`services.database.get_async_session`), Pydantic EnvelopeV1, AWS EventBridge (Lane 1), DBOS workflows, pytest + AsyncMock (no Docker).

---

## Context & Verified Evidence (confirmed against code at main `6138fa3`, 2026-05-26)

The investigation (3 subagents + cold re-verification + `/codex consult`) established:

- **Two channels, both caller-populated.** The transcript router populates contact data in two independent places: `routers/text.py:186` `extras.update(enrichment.to_extras_dict())` (→ envelope `extras.contact_ids`/`contacts[]`, published verbatim by `text_clean_service.process` at `text_clean_service.py:343` → consumed by downstream Neo4j) AND `routers/text.py:241` `Lane2Extras(contact_ids=...)` (→ `text_clean_service.py:395` → `intelligence_service.process_transcript` → `_persist_contact_links`). `process()` does NOT copy `lane2_extras` into the envelope. **Granola must populate both.**
- **Downstream iterates `extras.contact_ids`, NOT `contacts[]`** (eq-structured-graph-core `app/db/queries/skeleton.py:109`). `contacts[]` is a metadata lookup table keyed by `contact_id`. Relationship type derives from `interaction_type="meeting"` → `[:ATTENDED]`; `role`/`account_id`/`calendar_event_id` are not required downstream. So `contact_ids` is load-bearing; `contacts[]` enriches.
- **FK chain** in `_persist_contact_links` (`intelligence_service.py:427-571`), gated `if contact_ids:` (`:120`): `raw_interactions` INSERT `ON CONFLICT(interaction_id) DO NOTHING` (`:462`) → `interaction_summaries` (`:489`, new `summary_id`, `summary_type=interaction_type`) → `interaction_contact_links` (`:515`, its `interaction_id` column stores the `summary_id`). `calendar_event_interaction_links` only `if calendar_event_id:` (`:533`, tolerates None). The whole body swallows exceptions (`:564`, no re-raise).
- **The classifier already did the work.** `path2.PathTwoDecision.known_account_attendees` each carry `email`, `name`, and their OWN resolved `account_id` (per-attendee domain → account). Scenario A is chosen only when ≥1 known account exists (`path2.py:204-224`). Anchor = first known (`_pick_anchor`).
- **No pipeline creates accounts inline** — accounts are only created at approval (`account_provisioning/steps.py resolve_or_create_account`). Unknown-company attendees are queued to `pending_account_mappings`.
- **Granola today** (`adapter.py`): `_ingest_scenario_a` (598-748) calls `process(..., lane2_extras=None)` at `:709`; `_build_envelope` (1098-1161) sets only 6 `granola_*` extras keys. So zero contacts, zero `raw_interactions`, zero Neo4j edges.
- **Latent bug, confirmed permanent:** Scenario A queues co-occurring unknown-domain signals with the meeting's NON-NULL `interaction_id` (`adapter.py:738-743`), but with `lane2_extras=None` no `raw_interactions` is ever written → approving that company later raises `ValueError` at `materialization.py:782` (CHECK_RAW_INTERACTION_EXISTS) → the DBOS approval workflow retries 3× then **fails permanently** (`steps.py:434`). This fix closes it (writing `contact_ids` makes Lane 2 write `raw_interactions`).
- **Scenario C (no known account) self-heals:** defer → approve (creates account) → next poll `reprocess_pending_notes` (`adapter.py:815-1030`, watermark-independent) re-classifies as Scenario A → routes through the SAME `_ingest_scenario_a` (`adapter.py:983-991`) → this fix fires. Keep the Scenario-C signal `interaction_id=NULL` (do NOT set it — would trip the ValueError before Lane 2 writes `raw_interactions`).

### Codex consult refinements folded into this plan
1. Derive both channels from ONE `resolved_contacts` object in the same frame (no recompute → no Neo4j/Postgres divergence).
2. "Scenario A ⟹ ≥1 contact" is not airtight at the DB layer → treat zero-resolved as an **invariant violation → transient retry**, never publish a half-broken meeting.
3. **Add a SECOND liveness gate after contact resolution, immediately before publish** (the existing `:674` gate stops being "final" once resolution is inserted after it).
4. `account_id` on conflict uses `COALESCE(existing, excluded)` — **never reassigns** an existing contact's account (non-corrupting); log when the returned account differs from the requested one (observability), do NOT hard-raise (a poll loop must stay available; divergence is legitimate for cross-company attendees).
5. **Dedupe attendees by email** before building payloads (duplicate emails → duplicate `contact_ids`; downstream iteration would double-process).
6. The fix lives INSIDE `_ingest_scenario_a` so `reprocess_pending_notes` (Scenario-C-after-approval) inherits it automatically.
7. **Known residual (document + ticket, do NOT fix shared code):** Lane 2 is fire-and-forget and `_persist_contact_links` swallows exceptions, so if Lane 2 crashes after Scenario A is marked success, `raw_interactions` may never land. Contacts (sync, in adapter) + Neo4j (sync Lane 1 publish) are reliable; only the Postgres link/`raw_interactions` write is best-effort — **the same fragility the transcript path already has.** Net: no worse than transcript, far better than today (today fails always).

---

## Non-Negotiable Constraints (apply to every task)

- **Do NOT edit** `services/transcript_enrichment.py`, `services/intelligence_service.py`, `services/text_clean_service.py`, or any downstream consumer repo. Granola-local changes + the new shared module only.
- **Tenant isolation:** every `contacts` query scoped by `(tenant_id, email)`; `account_id` is always tenant-resolved via `lookup_account_by_domain`.
- **LOCKED-38:** never modify downstream Pydantic envelope contracts. `extras` is `Dict[str, Any]`; adding `contact_ids`/`contacts` is additive. **Verify pre-merge:** `python scripts/verify_consumer_contracts.py --no-aws` must show 0 drift (exit 0).
- **Branch safety:** all work on a feature branch; run `git branch --show-current` immediately before every commit (shared checkout).
- **Tests:** AsyncMock unit tests, NO Docker. Run with `DBOS_SYSTEM_DATABASE_URL` set, using `.venv/bin/python -m pytest`. Place Granola tests in `tests/unit/granola_ingestion/`; the new helper's tests in `tests/unit/test_contact_resolution.py`.
- **Codex pre-merge gate** mandatory before merge (4-round soft cap).
- Pre-existing failures to IGNORE (not ours): 1 unit (`test_upsert_summary_uses_unique_interaction_id_index`), 16 integration (`test_queue_lifecycle`).

---

## File Structure

- **Create:** `services/contact_resolution.py` — `find_or_create_contact()` + `ResolvedContactRow`. One responsibility: race-safe contact find-or-create bound to a tenant-scoped account.
- **Create:** `tests/unit/test_contact_resolution.py` — unit tests for the helper.
- **Modify:** `services/granola_ingestion/adapter.py` — add `_resolve_known_account_contacts()`; wire resolution + second liveness gate + dual-channel feed into `_ingest_scenario_a`; add `resolved_contacts` param to `_build_envelope`.
- **Modify:** `tests/unit/granola_ingestion/test_adapter.py` — new tests + update existing assertions that pin `lane2_extras=None` / the 6-key extras shape.
- **Reference only (read, never edit):** `services/account_lookup.py`, `services/granola_ingestion/path2.py`, `models/enrichment_models.py`.

---

### Task 1: Shared `find_or_create_contact` helper

**Files:**
- Create: `services/contact_resolution.py`
- Test: `tests/unit/test_contact_resolution.py`

- [ ] **Step 1: Write the failing test (new contact + existing contact + different-account + guards)**

```python
# tests/unit/test_contact_resolution.py
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.contact_resolution import find_or_create_contact, ResolvedContactRow

TENANT = "11111111-1111-4111-8111-111111111111"
ACCOUNT_A = "22222222-2222-4222-8222-222222222222"
ACCOUNT_B = "33333333-3333-4333-8333-333333333333"


def _session_returning(row: dict) -> MagicMock:
    """An AsyncMock SQLAlchemy session whose execute().mappings().one() == row."""
    session = MagicMock()
    result = MagicMock()
    result.mappings.return_value.one.return_value = row
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_creates_new_contact_returns_uuid_and_name():
    cid = str(uuid.uuid4())
    session = _session_returning(
        {"contact_id": cid, "first_name": "Matt", "last_name": "Scanlan", "account_id": ACCOUNT_A}
    )
    row = await find_or_create_contact(
        session=session, tenant_id=TENANT, email="Matt.Scanlan@Palantir.com",
        account_id=ACCOUNT_A, display_name="Matt Scanlan",
    )
    assert isinstance(row, ResolvedContactRow)
    assert row.contact_id == cid
    assert row.email == "matt.scanlan@palantir.com"   # normalized lower-case
    assert row.name == "Matt Scanlan"
    assert row.account_id == ACCOUNT_A
    assert row.account_matched is True
    # bound params include normalized email + uuids
    _, kwargs = session.execute.call_args
    params = session.execute.call_args[0][1]
    assert params["email"] == "matt.scanlan@palantir.com"
    assert params["tenant_id"] == uuid.UUID(TENANT)
    assert params["account_id"] == uuid.UUID(ACCOUNT_A)


@pytest.mark.asyncio
async def test_existing_contact_with_different_account_is_not_reassigned():
    cid = str(uuid.uuid4())
    # DB kept the existing account (COALESCE) — returns ACCOUNT_B though we asked for A
    session = _session_returning(
        {"contact_id": cid, "first_name": "Jane", "last_name": None, "account_id": ACCOUNT_B}
    )
    row = await find_or_create_contact(
        session=session, tenant_id=TENANT, email="jane@acme.com",
        account_id=ACCOUNT_A, display_name=None,
    )
    assert row.account_id == ACCOUNT_B
    assert row.account_matched is False     # caller logs this for observability


@pytest.mark.asyncio
async def test_missing_account_id_raises():
    with pytest.raises(ValueError):
        await find_or_create_contact(session=MagicMock(), tenant_id=TENANT, email="x@y.com", account_id="")


@pytest.mark.asyncio
async def test_blank_email_raises():
    with pytest.raises(ValueError):
        await find_or_create_contact(session=MagicMock(), tenant_id=TENANT, email="   ", account_id=ACCOUNT_A)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DBOS_SYSTEM_DATABASE_URL="${DBOS_SYSTEM_DATABASE_URL:-postgresql://localhost/dbos}" .venv/bin/python -m pytest tests/unit/test_contact_resolution.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.contact_resolution'`

- [ ] **Step 3: Write minimal implementation**

```python
# services/contact_resolution.py
"""Shared, race-safe contact find-or-create for ingestion paths.

Standalone helper (NOT a refactor of TranscriptEnrichmentService._resolve_contact)
so the Granola adapter can resolve contacts without re-running calendar matching
or Tavily, and WITHOUT touching the shared transcript/email Lane 2 code.

Atomic INSERT ... ON CONFLICT (tenant_id, email) DO UPDATE — idempotent and
race-safe (the transcript helper's SELECT-then-INSERT has a TOCTOU window).
Mirrors the proven pattern in account_provisioning/materialization.py:86-97.
account_id is COALESCEd, never reassigned: an existing contact keeps its account.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text


@dataclass(frozen=True)
class ResolvedContactRow:
    contact_id: str            # canonical UUIDv4 (str)
    email: str                 # normalized lower-case
    name: Optional[str]        # full name or None
    account_id: str            # the account the contact row is actually bound to (str)
    account_matched: bool      # False if an existing contact's account differed from requested


_FIND_OR_CREATE_SQL = text(
    """
    INSERT INTO contacts (
        id, tenant_id, email, first_name, last_name, account_id,
        source, validation_status, created_at, updated_at
    ) VALUES (
        gen_random_uuid(), :tenant_id, lower(:email), :first_name, :last_name, :account_id,
        :source, 'pending', NOW(), NOW()
    )
    ON CONFLICT (tenant_id, email) DO UPDATE
        SET first_name = COALESCE(contacts.first_name, EXCLUDED.first_name),
            last_name  = COALESCE(contacts.last_name,  EXCLUDED.last_name),
            account_id = COALESCE(contacts.account_id, EXCLUDED.account_id),
            updated_at = NOW()
    RETURNING id::text AS contact_id, first_name, last_name, account_id::text AS account_id
    """
)


def _split_display_name(display_name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not display_name:
        return None, None
    parts = display_name.strip().split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def _full_name(first: Optional[str], last: Optional[str]) -> Optional[str]:
    return " ".join(p for p in (first, last) if p) or None


async def find_or_create_contact(
    *,
    session,
    tenant_id: str,
    email: str,
    account_id: str,
    display_name: Optional[str] = None,
    source: str = "granola_ingestion",
) -> ResolvedContactRow:
    """Find-or-create a contact by (tenant_id, email), bound to account_id.

    Tenant-scoped via the (tenant_id, email) conflict key. ``account_id`` MUST be
    a tenant-scoped account (caller resolves it via lookup_account_by_domain).
    Does NOT commit — the caller owns the transaction so many attendees resolve
    in one session. ``account_matched`` is False when an existing contact's
    account differed (COALESCE kept it; the caller logs for observability).
    """
    if not account_id:
        raise ValueError("find_or_create_contact requires a non-empty account_id")
    email_norm = (email or "").strip().lower()
    if not email_norm:
        raise ValueError("find_or_create_contact requires a non-empty email")
    first, last = _split_display_name(display_name)
    result = await session.execute(
        _FIND_OR_CREATE_SQL,
        {
            "tenant_id": uuid.UUID(tenant_id),
            "email": email_norm,
            "first_name": first,
            "last_name": last,
            "account_id": uuid.UUID(account_id),
            "source": source,
        },
    )
    row = result.mappings().one()
    returned_account = row["account_id"]
    return ResolvedContactRow(
        contact_id=row["contact_id"],
        email=email_norm,
        name=_full_name(row["first_name"], row["last_name"]),
        account_id=returned_account,
        account_matched=(returned_account == account_id),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DBOS_SYSTEM_DATABASE_URL="${DBOS_SYSTEM_DATABASE_URL:-postgresql://localhost/dbos}" .venv/bin/python -m pytest tests/unit/test_contact_resolution.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git branch --show-current   # verify: phase-2.1/granola-contact-resolution
git add services/contact_resolution.py tests/unit/test_contact_resolution.py
git commit -m "feat(granola): shared race-safe find_or_create_contact helper"
```

---

### Task 2: Adapter `_resolve_known_account_contacts` (dedupe + per-attendee account)

**Files:**
- Modify: `services/granola_ingestion/adapter.py` (add helper near `_queue_unknown_domain_signals`)
- Test: `tests/unit/granola_ingestion/test_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/granola_ingestion/test_adapter.py  (add)
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from services.granola_ingestion.path2 import AttendeeClassification, PathTwoDecision, Scenario
from services.domain_classification import DomainClass
from services.contact_resolution import ResolvedContactRow

TENANT = "11111111-1111-4111-8111-111111111111"
ACCT_PAL = "22222222-2222-4222-8222-222222222222"
ACCT_SNO = "33333333-3333-4333-8333-333333333333"


def _known(email, name, account_id):
    return AttendeeClassification(email=email, name=name, domain=email.split("@")[1],
                                  klass=DomainClass.BUSINESS, account_id=account_id)


def _decision_scenario_a(known):
    return PathTwoDecision(scenario=Scenario.A_KNOWN_ANCHOR, anchor_account_id=known[0].account_id,
                           known_account_attendees=known)


@pytest.mark.asyncio
async def test_resolve_known_contacts_dedupes_by_email_and_binds_per_attendee_account():
    from services.granola_ingestion import adapter
    cred = MagicMock(tenant_id=uuid.UUID(TENANT), user_id=uuid.uuid4())
    known = [
        _known("matt@palantir.com", "Matt Scanlan", ACCT_PAL),
        _known("matt@palantir.com", "Matt Scanlan", ACCT_PAL),   # duplicate email
        _known("amy@snowflake.com", "Amy R", ACCT_SNO),
    ]
    decision = _decision_scenario_a(known)

    session = MagicMock()
    session.commit = AsyncMock()
    cm = MagicMock(); cm.__aenter__ = AsyncMock(return_value=session); cm.__aexit__ = AsyncMock(return_value=False)

    calls = []
    async def fake_focc(*, session, tenant_id, email, account_id, display_name):
        calls.append((email, account_id))
        return ResolvedContactRow(contact_id=str(uuid.uuid4()), email=email, name=display_name,
                                  account_id=account_id, account_matched=True)

    with patch.object(adapter, "get_async_session", return_value=cm), \
         patch.object(adapter, "find_or_create_contact", side_effect=fake_focc):
        resolved = await adapter._resolve_known_account_contacts(decision=decision, credential=cred)

    assert [r.email for r in resolved] == ["matt@palantir.com", "amy@snowflake.com"]  # deduped, order preserved
    assert calls == [("matt@palantir.com", ACCT_PAL), ("amy@snowflake.com", ACCT_SNO)]  # per-attendee account
    session.commit.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DBOS_SYSTEM_DATABASE_URL="${DBOS_SYSTEM_DATABASE_URL:-postgresql://localhost/dbos}" .venv/bin/python -m pytest tests/unit/granola_ingestion/test_adapter.py::test_resolve_known_contacts_dedupes_by_email_and_binds_per_attendee_account -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_resolve_known_account_contacts'`

- [ ] **Step 3: Write minimal implementation**

Add the import near the top of `adapter.py` (with the other `services.` imports):

```python
from services.contact_resolution import ResolvedContactRow, find_or_create_contact
```

Add the helper (place it just above `_queue_unknown_domain_signals`):

```python
async def _resolve_known_account_contacts(
    *,
    decision: PathTwoDecision,
    credential: GranolaCredential,
) -> list[ResolvedContactRow]:
    """Find-or-create a contact for each known-account attendee.

    Dedupes by normalized email (Granola may list an attendee twice). Each
    contact binds to that attendee's OWN resolved account_id (NOT the meeting
    anchor) — a multi-company meeting links contacts from several accounts.
    Resolves all attendees in ONE session/transaction; tenant-scoped throughout.
    Returns the resolved rows used to derive BOTH the envelope extras and the
    Lane 2 contact_ids (single source — never recompute).
    """
    known = decision.known_account_attendees
    if not known:
        return []
    seen: set[str] = set()
    resolved: list[ResolvedContactRow] = []
    async with get_async_session() as session:
        for att in known:
            email_norm = (att.email or "").strip().lower()
            if not email_norm or email_norm in seen:
                continue
            if not att.account_id:
                # decide_scenario only places BUSINESS+resolved attendees here,
                # so account_id is non-None; defensive skip keeps it explicit.
                logger.warning(
                    "granola_adapter: known attendee %s missing account_id; skipping",
                    email_norm,
                )
                continue
            seen.add(email_norm)
            row = await find_or_create_contact(
                session=session,
                tenant_id=str(credential.tenant_id),
                email=email_norm,
                account_id=att.account_id,
                display_name=att.name,
            )
            if not row.account_matched:
                logger.warning(
                    "granola_adapter: contact %s already bound to account %s, not the "
                    "meeting-resolved account %s; kept existing (no reassignment)",
                    email_norm, row.account_id, att.account_id,
                )
            resolved.append(row)
        await session.commit()
    return resolved
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DBOS_SYSTEM_DATABASE_URL="${DBOS_SYSTEM_DATABASE_URL:-postgresql://localhost/dbos}" .venv/bin/python -m pytest tests/unit/granola_ingestion/test_adapter.py::test_resolve_known_contacts_dedupes_by_email_and_binds_per_attendee_account -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git branch --show-current
git add services/granola_ingestion/adapter.py tests/unit/granola_ingestion/test_adapter.py
git commit -m "feat(granola): resolve known-account attendees to contacts (dedupe, per-attendee account)"
```

---

### Task 3: `_build_envelope` carries contact_ids + contacts[] in extras

**Files:**
- Modify: `services/granola_ingestion/adapter.py` (`_build_envelope`, 1098-1161)
- Test: `tests/unit/granola_ingestion/test_adapter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/granola_ingestion/test_adapter.py  (add)
@pytest.mark.asyncio
async def test_build_envelope_includes_contact_ids_and_contacts_when_resolved():
    from services.granola_ingestion import adapter
    cred = MagicMock(tenant_id=uuid.UUID(TENANT), user_id=uuid.uuid4())
    cred.config = {"folder_name": "Test EQ"}
    detail = MagicMock(id="not_X", web_url="http://x", summary_text="s",
                       calendar_event=None, attendees=[], created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    decision = _decision_scenario_a([_known("matt@palantir.com", "Matt Scanlan", ACCT_PAL)])
    iid = uuid.uuid4()
    resolved = [ResolvedContactRow(contact_id=str(uuid.uuid4()), email="matt@palantir.com",
                                   name="Matt Scanlan", account_id=ACCT_PAL, account_matched=True)]

    env = adapter._build_envelope(credential=cred, detail=detail, anchor_account_id=ACCT_PAL,
                                  decision=decision, interaction_id=iid, resolved_contacts=resolved)

    assert env.extras["contact_ids"] == [resolved[0].contact_id]
    assert env.extras["contacts"] == [
        {"contact_id": resolved[0].contact_id, "email": "matt@palantir.com",
         "name": "Matt Scanlan", "role": "attendee"}
    ]
    # the six granola_* keys still present (no regression)
    for k in ("granola_note_id", "granola_web_url", "granola_folder_name",
              "granola_summary_text", "granola_calendar_event_id", "granola_attendees_raw"):
        assert k in env.extras


@pytest.mark.asyncio
async def test_build_envelope_omits_contact_keys_when_none_resolved():
    from services.granola_ingestion import adapter
    cred = MagicMock(tenant_id=uuid.UUID(TENANT), user_id=uuid.uuid4()); cred.config = {}
    detail = MagicMock(id="not_X", web_url="u", summary_text="s", calendar_event=None,
                       attendees=[], created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    decision = _decision_scenario_a([_known("m@palantir.com", "M", ACCT_PAL)])
    env = adapter._build_envelope(credential=cred, detail=detail, anchor_account_id=ACCT_PAL,
                                  decision=decision, interaction_id=uuid.uuid4(), resolved_contacts=[])
    assert "contact_ids" not in env.extras
    assert "contacts" not in env.extras
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DBOS_SYSTEM_DATABASE_URL="${DBOS_SYSTEM_DATABASE_URL:-postgresql://localhost/dbos}" .venv/bin/python -m pytest tests/unit/granola_ingestion/test_adapter.py -k build_envelope -v`
Expected: FAIL with `TypeError: _build_envelope() got an unexpected keyword argument 'resolved_contacts'`

- [ ] **Step 3: Write minimal implementation**

Change the `_build_envelope` signature (add the new param, default `None` for back-compat):

```python
def _build_envelope(
    *,
    credential: GranolaCredential,
    detail: GranolaNoteDetail,
    anchor_account_id: str,
    decision: PathTwoDecision,
    interaction_id: Optional[UUID] = None,
    resolved_contacts: Optional[list[ResolvedContactRow]] = None,
) -> EnvelopeV1:
```

Right after the existing `extras: dict[str, Any] = { ... 6 keys ... }` block (after `adapter.py:1139`), add:

```python
    # Contact enrichment (Lane 1 → downstream Neo4j). Downstream
    # (eq-structured-graph-core) iterates extras.contact_ids to MERGE Contact
    # nodes + [:ATTENDED] edges; contacts[] is the metadata lookup keyed by
    # contact_id. Shape mirrors models/enrichment_models.py:45-71
    # (EnrichmentResult.to_extras_dict) so it matches the transcript path
    # exactly. Same resolved_contacts object also feeds Lane2Extras in
    # _ingest_scenario_a (single source — no recompute).
    if resolved_contacts:
        extras["contact_ids"] = [c.contact_id for c in resolved_contacts]
        extras["contacts"] = [
            {
                "contact_id": c.contact_id,
                "email": c.email,
                "name": c.name,
                "role": "attendee",
            }
            for c in resolved_contacts
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `DBOS_SYSTEM_DATABASE_URL="${DBOS_SYSTEM_DATABASE_URL:-postgresql://localhost/dbos}" .venv/bin/python -m pytest tests/unit/granola_ingestion/test_adapter.py -k build_envelope -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git branch --show-current
git add services/granola_ingestion/adapter.py tests/unit/granola_ingestion/test_adapter.py
git commit -m "feat(granola): carry contact_ids+contacts[] in envelope extras for downstream graph"
```

---

### Task 4: Wire `_ingest_scenario_a` — resolve, second liveness gate, dual-channel feed

**Files:**
- Modify: `services/granola_ingestion/adapter.py` (`_ingest_scenario_a`, 598-748)
- Test: `tests/unit/granola_ingestion/test_adapter.py`

**Behavioral target:** after `_record_in_progress` and the existing liveness gate (now "gate #1, before resolution"), resolve contacts; if zero resolved (invariant violation), record transient failure and retry; build the envelope with the resolved contacts; re-check liveness (gate #2, before publish); call `process()` with `Lane2Extras(contact_ids=[...], calendar_event_id=None)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/granola_ingestion/test_adapter.py  (add)
@pytest.mark.asyncio
async def test_scenario_a_feeds_contact_ids_to_lane2_and_envelope():
    from services.granola_ingestion import adapter
    from services.granola_ingestion.outcomes import IngestionOutcome

    cred = MagicMock(tenant_id=uuid.UUID(TENANT), user_id=uuid.uuid4(), id=uuid.uuid4()); cred.config = {}
    note = MagicMock(id="not_X", updated_at=None)
    detail = MagicMock(id="not_X", web_url="u", summary_text="s", calendar_event=None,
                       attendees=[], created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    decision = _decision_scenario_a([_known("matt@palantir.com", "Matt Scanlan", ACCT_PAL)])
    resolved = [ResolvedContactRow(contact_id="c-1", email="matt@palantir.com",
                                   name="Matt Scanlan", account_id=ACCT_PAL, account_matched=True)]

    captured = {}
    async def fake_process(*, tenant_id, user_id, account_id, envelope, lane2_extras):
        captured["lane2_extras"] = lane2_extras
        captured["envelope_extras"] = envelope.extras

    with patch.object(adapter.text_clean_service, "try_reserve_lane2_slot", return_value=True), \
         patch.object(adapter.text_clean_service, "process", side_effect=fake_process), \
         patch.object(adapter.text_clean_service, "release_lane2_slot"), \
         patch.object(adapter, "_resolve_known_account_contacts", AsyncMock(return_value=resolved)), \
         patch.object(adapter, "_credential_is_active", AsyncMock(return_value=True)), \
         patch.object(adapter, "_record_in_progress", AsyncMock()), \
         patch.object(adapter, "_record_success", AsyncMock()), \
         patch.object(adapter, "_queue_unknown_domain_signals", AsyncMock(return_value=[])):
        out = await adapter._ingest_scenario_a(credential=cred, note_summary=note, detail=detail,
                                               decision=decision, pool=MagicMock())

    assert out == IngestionOutcome.SUCCESS
    assert captured["lane2_extras"] is not None
    assert captured["lane2_extras"].contact_ids == ["c-1"]
    assert captured["lane2_extras"].calendar_event_id is None
    assert captured["envelope_extras"]["contact_ids"] == ["c-1"]   # both channels, same source


@pytest.mark.asyncio
async def test_scenario_a_zero_resolved_contacts_retries_without_publishing():
    from services.granola_ingestion import adapter
    process_mock = AsyncMock()
    with patch.object(adapter.text_clean_service, "try_reserve_lane2_slot", return_value=True), \
         patch.object(adapter.text_clean_service, "process", process_mock), \
         patch.object(adapter.text_clean_service, "release_lane2_slot"), \
         patch.object(adapter, "_resolve_known_account_contacts", AsyncMock(return_value=[])), \
         patch.object(adapter, "_credential_is_active", AsyncMock(return_value=True)), \
         patch.object(adapter, "_record_in_progress", AsyncMock()), \
         patch.object(adapter, "_record_scenario_a_failure", AsyncMock(return_value=adapter.IngestionOutcome.FAILED)) as fail:
        cred = MagicMock(tenant_id=uuid.UUID(TENANT), user_id=uuid.uuid4(), id=uuid.uuid4()); cred.config = {}
        note = MagicMock(id="not_X", updated_at=None)
        detail = MagicMock(id="not_X", web_url="u", summary_text="s", calendar_event=None, attendees=[],
                           created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        decision = _decision_scenario_a([_known("m@palantir.com", "M", ACCT_PAL)])
        await adapter._ingest_scenario_a(credential=cred, note_summary=note, detail=detail,
                                         decision=decision, pool=MagicMock())
    process_mock.assert_not_awaited()              # never published a half-broken meeting
    fail.assert_awaited_once()
    assert fail.call_args.kwargs["error_code"] == "contact_resolution_empty"


@pytest.mark.asyncio
async def test_scenario_a_gate2_aborts_if_disconnected_during_resolution():
    from services.granola_ingestion import adapter
    process_mock = AsyncMock()
    resolved = [ResolvedContactRow(contact_id="c-1", email="m@palantir.com", name="M",
                                   account_id=ACCT_PAL, account_matched=True)]
    with patch.object(adapter.text_clean_service, "try_reserve_lane2_slot", return_value=True), \
         patch.object(adapter.text_clean_service, "process", process_mock), \
         patch.object(adapter.text_clean_service, "release_lane2_slot"), \
         patch.object(adapter, "_resolve_known_account_contacts", AsyncMock(return_value=resolved)), \
         patch.object(adapter, "_record_in_progress", AsyncMock()), \
         patch.object(adapter, "_credential_is_active", AsyncMock(side_effect=[True, False])):  # gate1 ok, gate2 disconnected
        cred = MagicMock(tenant_id=uuid.UUID(TENANT), user_id=uuid.uuid4(), id=uuid.uuid4()); cred.config = {}
        note = MagicMock(id="not_X", updated_at=None)
        detail = MagicMock(id="not_X", web_url="u", summary_text="s", calendar_event=None, attendees=[],
                           created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        decision = _decision_scenario_a([_known("m@palantir.com", "M", ACCT_PAL)])
        with pytest.raises(adapter._CredentialDeactivated):
            await adapter._ingest_scenario_a(credential=cred, note_summary=note, detail=detail,
                                             decision=decision, pool=MagicMock())
    process_mock.assert_not_awaited()              # gate #2 stopped the publish
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DBOS_SYSTEM_DATABASE_URL="${DBOS_SYSTEM_DATABASE_URL:-postgresql://localhost/dbos}" .venv/bin/python -m pytest tests/unit/granola_ingestion/test_adapter.py -k scenario_a -v`
Expected: FAIL (contact_ids None / process still called / no gate #2)

- [ ] **Step 3: Edit `_ingest_scenario_a`**

(a) Move the envelope build OUT of its current position at `adapter.py:649-656`. After the `interaction_id = existing_interaction_id or uuid4()` line, REMOVE the `envelope = _build_envelope(...)` call (it moves below the resolution).

(b) The existing liveness gate at `adapter.py:685-696` stays where it is (now "gate #1, before contact resolution"). Update its comment from "FINAL liveness gate" to "Gate #1: before contact resolution + publish (a second gate runs after resolution)".

(c) Immediately AFTER that gate's closing block (after `raise _CredentialDeactivated(...)` at ~`:696`), and BEFORE the `try:` that wraps `process()` (`:698`), insert:

```python
        # Resolve known-account attendees to contacts (find-or-create). Single
        # source: the same resolved_contacts feeds BOTH the envelope extras
        # (Lane 1 → downstream Neo4j) and Lane2Extras (Lane 2 → Postgres FK
        # chain). Scenario A guarantees >=1 known attendee, but DB resolution
        # can still yield zero (UUID error, FK gone, blank email). Treat zero
        # as an INVARIANT VIOLATION: retry rather than publish a meeting whose
        # co-occurring unknown-domain approval would later fail on a missing
        # raw_interactions row.
        resolved_contacts = await _resolve_known_account_contacts(
            decision=decision, credential=credential
        )
        if not resolved_contacts:
            logger.error(
                "granola_adapter: Scenario A resolved 0 contacts for note %s "
                "(invariant violation); recording transient failure to retry",
                note_summary.id,
            )
            return await _record_scenario_a_failure(
                pool=pool,
                credential=credential,
                note_summary=note_summary,
                error_code="contact_resolution_empty",
                error_detail={"note_id": note_summary.id},
                existing_retry_count=existing_retry_count,
            )
        contact_ids = [c.contact_id for c in resolved_contacts]

        envelope = _build_envelope(
            credential=credential,
            detail=detail,
            anchor_account_id=anchor_account_id,
            decision=decision,
            interaction_id=interaction_id,
            resolved_contacts=resolved_contacts,
        )

        # Gate #2 (Codex consult): contact resolution above is an async window;
        # re-check liveness immediately before the publish so a /disconnect that
        # landed during resolution does not emit a Lane 1/Lane 2 event.
        if not await _credential_is_active(
            pool=pool,
            credential_id=credential.id,
            tenant_id=credential.tenant_id,
            user_id=credential.user_id,
        ):
            logger.info(
                "granola_adapter: credential %s deactivated during contact "
                "resolution for note %s; aborting before publish",
                credential.id, note_summary.id,
            )
            raise _CredentialDeactivated(credential.id)
```

(d) Change the `process()` call (`:698-710`) `lane2_extras` argument from `None` to:

```python
                await text_clean_service.process(
                    tenant_id=credential.tenant_id,
                    user_id=str(credential.user_id),
                    account_id=anchor_account_id,
                    envelope=envelope,
                    # Granola transcripts are pre-clean; do NOT override
                    # cleaned_transcript (Lane 2 analyzes envelope.content.text).
                    # contact_ids drives the Lane 2 FK chain (raw_interactions →
                    # interaction_summaries → interaction_contact_links); the SAME
                    # resolved_contacts populated envelope.extras above.
                    lane2_extras=text_clean_service.Lane2Extras(
                        contact_ids=contact_ids,
                        calendar_event_id=None,
                    ),
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `DBOS_SYSTEM_DATABASE_URL="${DBOS_SYSTEM_DATABASE_URL:-postgresql://localhost/dbos}" .venv/bin/python -m pytest tests/unit/granola_ingestion/test_adapter.py -k scenario_a -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git branch --show-current
git add services/granola_ingestion/adapter.py tests/unit/granola_ingestion/test_adapter.py
git commit -m "feat(granola): wire contact resolution + 2nd liveness gate into Scenario A ingest"
```

---

### Task 5: Update existing adapter tests that pinned the old behavior

**Files:**
- Modify: `tests/unit/granola_ingestion/test_adapter.py`

- [ ] **Step 1: Find the pinned assertions**

Run: `grep -n "lane2_extras=None\|lane2_extras is None\|lane2_extras\b" tests/unit/granola_ingestion/test_adapter.py`
Also: `grep -n "extras ==\|set(.*extras\|len(.*extras\|granola_note_id" tests/unit/granola_ingestion/test_adapter.py`
Expected: existing Scenario A tests assert `lane2_extras=None` and/or the exact 6-key extras set.

- [ ] **Step 2: Update each to the new contract**

For any test asserting `process` was called with `lane2_extras=None` on the Scenario A path: change to assert `lane2_extras.contact_ids` is the resolved list (mock `_resolve_known_account_contacts` to return a known list, as in Task 4). For any test asserting `envelope.extras` equals exactly the 6 keys on Scenario A: either mock zero resolved contacts (then 6 keys hold) or assert the 6 keys are a SUBSET (`set(six).issubset(env.extras)`). Do NOT weaken Scenario C / Scenario D tests — those still pass no contacts.

- [ ] **Step 3: Run the full granola suite**

Run: `DBOS_SYSTEM_DATABASE_URL="${DBOS_SYSTEM_DATABASE_URL:-postgresql://localhost/dbos}" .venv/bin/python -m pytest tests/unit/granola_ingestion/ tests/unit/test_contact_resolution.py -v`
Expected: PASS (all green; no Scenario A regression)

- [ ] **Step 4: Commit**

```bash
git branch --show-current
git add tests/unit/granola_ingestion/test_adapter.py
git commit -m "test(granola): update Scenario A assertions for contact-resolution contract"
```

---

### Task 6: Full suite + contract verification + Codex pre-merge gate

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit suite**

Run: `DBOS_SYSTEM_DATABASE_URL="${DBOS_SYSTEM_DATABASE_URL:-postgresql://localhost/dbos}" .venv/bin/python -m pytest tests/unit/ -q`
Expected: all PASS except the 1 known pre-existing failure (`test_upsert_summary_uses_unique_interaction_id_index`). Zero NEW failures.

- [ ] **Step 2: Verify downstream envelope contract (LOCKED-37/38)**

Run: `.venv/bin/python scripts/verify_consumer_contracts.py --no-aws`
Expected: exit 0, 0 drift — adding `contact_ids`/`contacts` to extras is additive (extras is `Dict[str, Any]`; downstream already reads `extras.contact_ids`). If non-zero, STOP and reconcile (do NOT modify downstream).

- [ ] **Step 3: Codex pre-merge gate**

Run `/codex review` against the feature branch diff. Fold all P1 findings (4-round soft cap). The diff touches a new module + the Granola adapter only (no shared Lane 2 edits), so review should be tight.

- [ ] **Step 4: Commit any fixes from the gate, then open PR**

```bash
git branch --show-current
git add -A && git commit -m "fix(granola): fold codex pre-merge findings"   # only if findings
```
(PR creation + merge are founder-authorized actions — do NOT self-merge.)

---

### Task 7: Production E2E verification (founder-gated; after merge + deploy)

**Files:** none (verification only). Requires founder authorization for the poll trigger + shared-infra collision check first.

- [ ] **Step 1: Shared-infra collision check** — `ls -lt ~/.claude/projects/-Users-peteroneil-*/*.jsonl | head` ; confirm no other agent active in the test tenant in the last hour.
- [ ] **Step 2: Force a poll** on the connected credential (mint internal JWT; `POST /internal/granola/cron-tick` with `X-Internal-Cron-Secret`). Reset `last_polled_at=NULL` if re-scanning the existing meeting; to re-run a `success` note, flip its `external_integration_runs` row to `status='failed'` (keep `eq_interaction_id`).
- [ ] **Step 3: Verify known-account linking** (interaction `bca60296-...` / Palantir): `contacts` row for `matt.scanlan@palantir.example.com` (source=`granola_ingestion`); `interaction_contact_links` row; `raw_interactions` row for the interaction; Lane 2 still wrote 5 `interaction_summary_entries` + ≥1 `interaction_insights`.
- [ ] **Step 4: Verify Neo4j** (neo4j_structured MCP): a `Contact` node `(tenant_id, contact_id)` + a `[:ATTENDED]` edge to the meeting Interaction.
- [ ] **Step 5 (optional, unknown-company path):** add an unknown-company attendee meeting → defer (Scenario C) → approve the domain via `POST /queue/{id}/approve` → next poll re-ingests → verify the contact is created AND linked.
- [ ] **Step 6: LOCKED-11 cleanup** when the founder is ready (announce table list first; tenant-scoped deletes only).

---

## Self-Review

**1. Spec coverage:**
- Known-company contact create+link → Tasks 1, 2, 4 (Lane 2 channel) ✓
- Neo4j contact graph → Task 3 (envelope extras channel) + Task 7 Step 4 ✓
- Unknown-company (Scenario C) → no code change; self-heals via reprocess; covered by Task 7 Step 5 + the "do not set interaction_id" note ✓
- Mixed-meeting latent bug → fixed by Task 4 (raw_interactions now written); the fix lives in `_ingest_scenario_a` so reprocess inherits it ✓
- Don't break shared pipeline → constraint enforced; new module + adapter only; Task 6 Step 1 full suite ✓
- Codex refinements (single source, dedupe, gate #2, zero-resolved retry, COALESCE+log) → Tasks 2, 3, 4 ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases" without code. Every code step has complete code. E2E steps (Task 7) are verification actions, not code.

**3. Type consistency:** `ResolvedContactRow` (contact_id, email, name, account_id, account_matched) defined in Task 1, used identically in Tasks 2-4. `find_or_create_contact` signature stable. `_build_envelope` new param `resolved_contacts` matches the call in Task 4. `Lane2Extras(contact_ids=, calendar_event_id=)` matches the verified dataclass fields.

## Open items for /plan-eng-review (decisions I made; flag if wrong)
- **account_id COALESCE + log (not raise).** Diverges from materialization's hard-raise on mismatch. Rationale: COALESCE never reassigns (non-corrupting), and a 5-min poll loop must stay available. Confirm acceptable.
- **`source="granola_ingestion"`, `validation_status="pending"`.** Matches the transcript path's auto-created posture. Confirm vs. reusing `transcript_enrichment`.
- **Lane 2 fire-and-forget residual (documented, ticketed, not fixed here):** if Lane 2 crashes after Scenario A success, `raw_interactions` may not land → a later co-occurring-unknown approval could fail. Same fragility as the transcript path. Ticket a hardening follow-up; do not touch shared code this PR.
- **`/map` returns 500 (not 503) on the missing-`raw_interactions` ValueError** (pre-existing inaccuracy at `queue_actions.py`; comment claims 503). Out of scope; noted for a separate ticket.
