# Next Session — Start Here

**Project:** Phase 2 — Granola.ai transcript ingestion integration.
**Last session:** 2026-05-24 (Phase 2c — Granola HTTP API client — built + 6 Codex rounds + merged + deployed).
**Status:** ✅ **PHASE_2C_SHIPPED + PHASE_2D_NEXT** — HTTP API client live in production as inert code. Ready to build the adapter.

**Paste-ready opening prompt for the next session:**
`docs/superpowers/specs/2026-05-24-phase-2d-session-prompt.md`

That prompt is self-contained. It walks the new agent through mandatory reads → 2-paragraph confirmation → Phase 2d implementation (adapter + Path 2 logic) → Codex pre-merge review → ship.

---

## What shipped this session (2026-05-24)

### PR #25 — Granola HTTP API client ✅ MERGED + DEPLOYED

- Squash-merged to live-transcription-fastapi main as commit `030523c`
- Railway deployment `9e393c5a-1fcf-4b10-9d11-e62236f53b87` Status=SUCCESS in <1 min
- `/health` 200 verified
- 42 new unit tests via httpx.MockTransport (per `feedback_test_pattern_no_docker`)
- 245 unit tests total passing; 0 regressions
- 6 new files, +2070 lines

### Files shipped (all in `services/granola_ingestion/` + `tests/unit/granola_ingestion/`)

| File | Lines | Role |
|---|---|---|
| `errors.py` | 96 | `GranolaErrorCode` (8 codes) + `GranolaError` exception |
| `models.py` | 169 | `GranolaFolder` / `GranolaNoteSummary` / `GranolaNoteDetail` Pydantic models + 4 sub-models |
| `api_client.py` | 604 | `GranolaAPIClient` async class — 3 methods, retry/pagination/429 budget |
| `__init__.py` | 47 | Public API re-exports |
| `tests/.../test_api_client.py` | 1154 | 42 tests |
| `tests/.../__init__.py` | 0 | Package marker |

### Codex pre-merge gate (6 rounds; gate clean since R2)

| Round | P1 | P2 | Notes |
|---|---|---|---|
| R1 | 2 | 1 | 429 retry-forever; note-vs-folder 404; pagination silently truncates |
| R2 | 0 | 2 | /folders pagination omitted; 429 fallback delay doesn't grow |
| R3 | 0 | 2 | `transcript` should be required; 20-page ceiling fails legit backfills |
| R4 | 0 | 1 | Bumped ceiling leaks into /folders (scope creep from R3) |
| R5 | 0 | 1 | Endpoint cap shadows caller's `max_pages` knob (scope creep from R4) |
| R6 | 0 | 0 | **CONVERGENCE: no introduced bugs** |

R1-R3 ran with `--base main` (full diff). R4-R6 hit the 5.5-min wrapper timeout on cumulative diff and switched to `--base <prior-commit>` per the codified workaround.

### What's now hardened (compared to my initial Phase 2c draft)

- **Retry budgets** are structurally bounded across 4 failure modes (5xx, timeouts, connect errors, sustained 429s); no path can loop indefinitely.
- **429 has a SEPARATE budget** from the main retry counter; fallback delay grows across consecutive 429s (R2 fix); counter resets on any non-429 response.
- **8 error codes** including `GRANOLA_NOTE_NOT_FOUND` so Phase 2d distinguishes a per-note skip (one note deleted between list + detail) from credential-level folder breakage.
- **Pagination is transparent + endpoint-aware**: shared `_get_paginated` helper with per-endpoint ceilings (`/folders=20` hardcoded; `/notes=500` configurable). Effective ceiling = `min(caller, endpoint_cap)` so caller-strict always wins.
- **`transcript` is REQUIRED** in `GranolaNoteDetail` — missing field surfaces as `GRANOLA_PARSE_ERROR` (not silently substituted with `[]`).
- **`api_key` security**: stored privately; `__repr__` never leaks it; empty-key fails at construction.

---

## What's verified live in production

| Layer | Status |
|---|---|
| AWS KMS CMK `59a0e2bc-...` (alias `eq-user-secrets`) | ✅ Auto-rotation enabled, LOCKED-40 enforced |
| IAM user `eq-vault-service` + identity policy | ✅ Empirically verified |
| Vault Python module (`services/vault/`) | ✅ Live on Railway (deploy `2ce20b0e`, prior session) |
| Granola API client (`services/granola_ingestion/`) | ✅ Live on Railway (deploy `9e393c5a`, THIS session) |
| Production Neon `vault` schema + 3 tables | ✅ Live |
| eq-frontend main (vault Prisma models + ignorePattern fix) | ✅ Commit `7905222` |
| live-transcription-fastapi `/health` | ✅ HTTP 200 |
| eq-frontend production deploy | ✅ READY (commit `7905222`) |

---

## What's next: Phase 2d — Granola adapter + Path 2 logic (~1.5 days)

Per `tasks/granola-integration-plan.md` §Phase 2d:

**New files:**
- `services/granola_ingestion/adapter.py` — the per-credential ingestion cycle
- `services/granola_ingestion/path2.py` — attendee classification + Scenario A/B/C/D branching
- `services/granola_ingestion/outcomes.py` — `IngestionOutcome` enum (5 values)

**What it composes (this is Phase 2d's "integration moment"):**
- `services.vault.get_granola_credential_for_user` — decrypted credential snapshot
- `services.granola_ingestion.GranolaAPIClient` — JUST SHIPPED in Phase 2c
- `services.text_clean_service` — LOCKED-41 direct Python call (NOT HTTP)
- existing `account_lookup` / `domain_classification` / `pending_account_mappings` infrastructure
- envelope construction per LOCKED-35 (`source="generic"`, `interaction_type="meeting"`) + LOCKED-36 (six `granola_*` extras)

**Per-credential cycle (high level):**
1. Decrypt credential via vault
2. Construct `GranolaAPIClient` with the decrypted api_key
3. `list_notes(folder_id, created_after=last_polled_at)` → get new notes
4. For each note: `get_note_detail(note_id)` → full payload
5. Path 2 classification: extract business-domain attendees → `lookup_account_by_domain`
6. Scenario branching:
   - **A/B (known accounts)**: build envelope → call `text_clean_service.process(tenant_id=..., envelope=...)`
   - **C (unknown business)**: queue signals + write `external_integration_runs` with `granola_note_snapshot` JSONB (LOCKED-44)
   - **D (no business attendees)**: skip
7. Re-poll deferred-pending-account rows from prior cycles
8. Update credential `last_polled_at`

**Critical disciplines for Phase 2d:**
- **LOCKED-38** (never modify downstream Pydantic envelope contracts) becomes load-bearing — Phase 2d is the first envelope-construction work since Phase 1
- **LOCKED-41** (text_clean_service direct call, NOT HTTP; tenant_id as explicit arg)
- pre-merge `scripts/verify_consumer_contracts.py` mandatory (verify the locked envelope shape against both downstream consumers BEFORE merge)
- Phase 2c's `GRANOLA_NOTE_NOT_FOUND` MUST be treated as per-note skip (`failed`), NOT credential-level breakage (`status='error'`)
- adapter is first code to import `services.vault` — vault's ALLOWLIST already includes `services.granola_ingestion.adapter`
- Tenant isolation: `tenant_id` flows from `credential.tenant_id` (JWT-validated at /connect time per Phase 2f) as explicit argument to every downstream call
- LOCKED-44 `granola_note_snapshot` (JSONB on `external_integration_runs`) populated at defer time so Scenario C remains recoverable if Granola removes the note before approval

---

## What this session's work does NOT include

- Phase 2e (scheduler — Railway cron + DBOS queue) — separate session, ~0.5 day
- Phase 2f (admin endpoints — /validate, /connect, /rotate, /status, /disconnect) — separate session, ~0.5 day
- Phase 2g (transactional email on credential breakage) — separate session, ~0.5 day
- Phase 3 (frontend) — separate session, ~2 days
- Phase 4 (production E2E with Peter as design partner #0) — separate session, ~1 day
- Fixing the live-db CI gap (Linear EQ-11 family)
- Upgrading Prisma 5.22 → 7.x (Linear EQ-11 family)

---

## CRITICAL — What NOT to re-litigate

- LOCKED-23 through LOCKED-44 are non-negotiable without strong new information + explicit user authorization to revisit.
- Phase 2a (Prisma migration) is merged to eq-frontend main; vault tables LIVE in production Neon.
- Phase 2b (vault module) is merged to live-transcription-fastapi main; module live as inert code; KMS smoke test PASSED.
- Phase 2c (HTTP API client) is merged to live-transcription-fastapi main; module live as inert code (Phase 2d's adapter is the first thing that will import it).
- The 8 error codes in Phase 2c's `errors.py` are the canonical wire format; Phase 2d consumes them via the imported enum.
- Phase 2c's `transparent cursor pagination` semantics are locked in; Phase 2d should call `client.list_notes(...)` and trust the returned list is complete.

---

## NEW lesson codified this session (tasks/lessons.md)

1. **Codex round-N convergence: scope-creep follow-ons.** A fix in round N can introduce a narrower bug in round N+1 (R3's ceiling bump → R4's leak to /folders → R5's caller-knob shadow → R6 clean). Audit shared helpers + public knobs for blast radius BEFORE committing a fix that changes a default. Refines [[feedback-codex-pre-merge-gate]].

---

## Stop conditions for the next session

- `/context-restore` returns NO_CHECKPOINTS or wrong checkpoint title
- MEMORY.md Active Work doesn't read "PHASE_2C_SHIPPED" / "PHASE_2D_NEXT"
- live-transcription-fastapi main is NOT at `030523c` (or descendant)
- eq-frontend main is NOT at `7905222` (or descendant)
- Production `/health` returns non-200
- Vault schema + 3 tables NOT present in production Neon
- AWS infrastructure missing
- Another agent actively working in live-transcription-fastapi within the last hour
- User asks you to deviate from a LOCKED decision (LOCKED-23..44) without explicit written confirmation
- Phase 2d code path starts modifying any downstream Pydantic envelope contract → STOP (LOCKED-38)
- Phase 2d code path starts calling text_clean over HTTP instead of via direct Python import → STOP (LOCKED-41)

---

## AWS account state (unchanged this session)

- Account ID: `211125681610`, us-east-1
- IAM user `eq-vault-service` + KMS CMK `59a0e2bc-c636-45e8-bccf-427ad2426ad8` (alias `alias/eq-user-secrets`)
- Auto-rotation enabled (annual; next 2027-05-23)
- Access key `AKIATCKASHXFPCDN6NXX` active; secret in Railway env vars only

## Railway state (verified 2026-05-24 end-of-session)

- Project `847cfa5a-b77c-4fb0-95e4-b20e8773c23e` (live-transcription-fastapi)
- Production environment `e4c5ec15-1931-4632-9e58-92d9c6be4261`
- Service `59a69f3d-9a24-4041-942a-891c4a81c5fb`
- **Latest deployment: `9e393c5a-1fcf-4b10-9d11-e62236f53b87` SUCCESS** (Phase 2c merge)
- `/health` 200 verified at https://live-transcription-fastapi-production.up.railway.app/health
- 4 vault env vars still set + working

## Vercel state (eq-frontend, unchanged)

- Project ID: `prj_0wDppCftk1VrSAsYswI5pnNRHdN8`
- Team ID: `team_Hnnnu6r1trggeAXYWHXpKfMt`
- Production deploy `2he8eDSfSLdapZ1eRXa6mSpjJkdq` READY at 2026-05-24 10:40:22Z

## Neon state (production, unchanged)

- Project `super-glitter-11265514` (eq-dev), branch `br-holy-block-ads5069w`
- Database `neondb`
- Vault schema + 3 tables LIVE with all FKs/UNIQUEs/indexes verified
- **Important:** Vercel preview builds run against the same production Neon DB (lessons.md)

---

## Linear EQ-11 — schema drift investigation (separate work; related)

https://linear.app/eq-core/issue/EQ-11/investigate-prisma-schema-drift-in-eq-frontend-design-cutting-edge

Unchanged this session. Items related to EQ-11's family:
- Comments-generator multiSchema fix RESOLVED via ignorePattern (Path B, prior session)
- Prisma 5.22 → 7.x upgrade in eq-frontend STILL OPEN
- live-db CI workflow `DIRECT_DATABASE_URL` gap STILL OPEN

---

## Build session entry prompt

Paste the contents of `docs/superpowers/specs/2026-05-24-phase-2d-session-prompt.md` as the opening message of the next session.

That prompt contains:
- Mandatory reads list (12 numbered items)
- Phase 2d implementation scope + expected file structure
- User posture rules + critical disciplines
- Stop conditions
- Commits summary
- AWS + Railway + Vercel + Neon state
