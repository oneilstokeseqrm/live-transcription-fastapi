# eq-frontend Phase 1 Schema Migration

## Goal
Update `prisma/schema.prisma` to support Phase 1 of the Contact Quality Initiative. Then run Prisma migrate. Database: Neon Postgres project `super-glitter-11265514` (eq-dev).

## Reference
Design doc Section 5.2 (canonical column list): live-transcription-fastapi/docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md

## Schema changes

### 1. Add columns to existing `pending_account_mappings` model

- `owner_user_id String @db.Uuid` (FK to users; never reassigned by routine UPSERT)
- `discovered_from_type String` (enum-as-string: `email | transcript | calendar | manual`)
- `discovered_from_interaction_id String? @db.Uuid`
- `expires_at DateTime @db.Timestamptz(6)`
- `archived_at DateTime? @db.Timestamptz(6)`
- `archive_reason String?` (enum-as-string: `expired_no_activity | owner_ignored | tenant_resolved_other_way`)
- `re_open_count Int @default(0)`
- `last_reopened_at DateTime? @db.Timestamptz(6)`

Index: `@@index([tenant_id, archived_at])`

### 2. Create new `pending_account_mapping_signals` model

```prisma
model pending_account_mapping_signals {
  id                    String   @id @default(uuid()) @db.Uuid
  tenant_id             String   @db.Uuid
  queue_id              String   @db.Uuid
  source_type           String
  source_user_id        String   @db.Uuid
  interaction_id        String?  @db.Uuid
  calendar_event_id     String?  @db.Uuid
  contact_email         String   @db.VarChar(255)
  contact_display_name  String?  @db.VarChar(255)
  contact_role          String?  @db.VarChar(50)
  created_at            DateTime @default(now()) @db.Timestamptz(6)
  archived_at           DateTime? @db.Timestamptz(6)

  @@unique([queue_id, contact_email, source_type, interaction_id, calendar_event_id], map: "pending_signal_dedup")
  @@index([tenant_id, queue_id, archived_at])
  @@map("pending_account_mapping_signals")
}
```

Optionally also drop the `email_count` field from `pending_account_mappings` if present (it is replaced by a derived COUNT over `pending_account_mapping_signals`). If dropping is risky, leave it in place but mark as deprecated in a comment.

### 3. Add column to existing `raw_interactions` model

- `account_id String? @db.Uuid` (NULLABLE in Phase 1; becomes NOT NULL in Phase 1.5 after test-data wipe)

## Steps for agent

1. Verify you are in the eq-frontend repo on a feature branch.
2. Read the existing `prisma/schema.prisma` file to confirm current shape of `pending_account_mappings` and `raw_interactions`.
3. Apply the schema changes listed above.
4. Run `npx prisma format` then `npx prisma migrate dev --name contact_quality_phase_1`.
5. Verify migration ran successfully against Neon eq-dev.
6. Run `npx prisma generate` to refresh client.
7. Commit: `chore: phase 1 schema for contact-quality initiative`
8. Open PR titled `chore(prisma): contact quality phase 1 schema`.
9. Report back the migration filename + PR URL to the orchestrating agent.

## What NOT to do

- Do NOT enforce NOT NULL on `raw_interactions.account_id` in Phase 1. That happens in Phase 1.5 after the test-data wipe.
- Do NOT enforce NOT NULL on `contacts.account_id` yet. Same reason.
- Do NOT drop `pending_validations` or `validation_status`. Phase 2 handles them.
- Do NOT add `accounts.state`. That is Phase 1.5.

## Acceptance

- Migration file exists and runs cleanly forward and backward.
- `npx prisma validate` passes.
- PR description references this initiative.
