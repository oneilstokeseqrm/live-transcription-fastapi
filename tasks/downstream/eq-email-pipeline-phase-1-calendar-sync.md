# eq-email-pipeline Phase 1: calendar_sync.py three-state branching

## Repo
/Users/peteroneil/eq-email-pipeline

## Goal
Apply the same three-state per-attendee branching to `src/pipeline/calendar_sync.py` that lives in `live-transcription-fastapi/services/transcript_enrichment.py` (Phase 1, Task 1.21).

## Reference
Design Section 5.2, 5.3, 7.1 (canonical):
`/Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md`

## Mechanics
For each calendar attendee:
- Extract email domain.
- Classify domain (personal / internal / business). Use a shared module if eq-email-pipeline has one, otherwise mirror the list from
  `live-transcription-fastapi/services/domain_classification.py`.
- If personal or internal: skip (no contact creation, no queue insertion).
- If business:
  - Call `lookup_account_by_domain(tenant_id, domain)` (already exists in `src/persistence/postgres.py`).
  - On hit (known account): create contact normally with that account_id via `find_or_create_contact()`.
  - On miss (unknown business): insert into `pending_account_mappings` + `pending_account_mapping_signals` using the upsert+signal pattern. Owner = the user whose `provider_connection` surfaced this calendar event.

## Schema dependency
This task requires the eq-frontend Phase 1 migration to have landed
(adds `pending_account_mapping_signals` table + new columns to
`pending_account_mappings`). See `tasks/downstream/eq-frontend-phase-1-schema.md`.

eq-frontend PR #349 (merged into eq-dev Neon as of 2026-05-13) provides:
- New columns on `pending_account_mappings`: `owner_user_id`, `discovered_from_type`, `discovered_from_interaction_id`, `expires_at`, `archived_at`, `archive_reason`, `re_open_count`, `last_reopened_at`
- New table `pending_account_mapping_signals` with unique constraint `pending_signal_dedup`
- New nullable column on `raw_interactions`: `account_id`

## Acceptance
- For a calendar event with attendees [alice@acme.com, partner@external.com, intern@gmail.com] and a known account for `acme.com`:
  - alice -> contact with account_id=acme
  - partner -> signal row (no contact)
  - intern -> no row anywhere
- All new behavior covered by tests.
- PR titled `feat(calendar-sync): three-state attendee branching` opened.

## What NOT to do
- Do NOT preserve fallback-to-anchor for unknown-domain attendees.
- Do NOT touch the email orchestrator yet (Task 1.24 covers that).
