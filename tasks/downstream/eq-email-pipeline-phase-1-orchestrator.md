# eq-email-pipeline Phase 1: orchestrator.py three-state verification

## Repo
/Users/peteroneil/eq-email-pipeline

## Goal
Audit `src/pipeline/orchestrator.py` for compliance with three-state branching for email sender/recipient resolution. The current orchestrator already calls `lookup_account_by_domain()` for sender/recipient domains, but does it correctly handle the unknown-business case?

## Reference
Design Section 5.2, 7.1.
`/Users/peteroneil/EQ-CORE/live-transcription-fastapi/docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md`

## Steps
1. Read `src/pipeline/orchestrator.py` and trace the email-ingestion path for sender + each recipient.
2. For each domain resolution miss, verify it inserts into `pending_account_mappings` + `pending_account_mapping_signals` instead of creating a NULL-account_id contact.
3. If the current code creates a NULL-account_id contact on lookup miss, replace that with the queue insertion.
4. Update owner determination: for email signals, `owner_user_id` is the user whose `provider_connection` sent/received the email (`provider_connections.user_id`).
5. Apply the same personal-domain skip + internal-domain skip rules.

## Acceptance
- A test inbox containing an email from `acme.com` (known) plus `unknown-startup.io` (unknown business) plus `gmail.com` (personal) produces:
  - acme.com -> contact with account_id=acme
  - unknown-startup.io -> signal row, no contact
  - gmail.com -> no row anywhere
- PR titled `feat(orchestrator): three-state email-domain branching`.

## Schema dependency
Same as `tasks/downstream/eq-email-pipeline-phase-1-calendar-sync.md`. eq-frontend PR #349 is the source of truth for the schema as of 2026-05-13.
