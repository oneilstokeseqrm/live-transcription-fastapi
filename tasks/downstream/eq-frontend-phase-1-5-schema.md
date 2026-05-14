# eq-frontend Phase 1.5 Schema Migration

## Repo
/Users/peteroneil/eq-frontend

## Reference
Design Sections 5.2 (Phase 1.5 column additions), 5.4 (account_provisioning_outbox), 6 (accounts.state), 12 (enforcement gate).

## Schema changes

### 1. `accounts` model
Add `state String @default("active")` (enum-as-string: `active | archived`).

### 2. `pending_account_mappings` model — Phase 1.5 lifecycle columns
- `approval_attempt_id String? @db.Uuid`
- `creation_started_at DateTime? @db.Timestamptz(6)`
- `mapped_at DateTime? @db.Timestamptz(6)`
- `ignored_at DateTime? @db.Timestamptz(6)`
- `ignored_by String? @db.Uuid`

Extend the `status` field's allowed values to include: `approved`, `creating`, `tenant_review`.

### 3. New `account_provisioning_outbox` model

```prisma
model account_provisioning_outbox {
  id                  String   @id @default(uuid()) @db.Uuid
  tenant_id           String   @db.Uuid
  queue_id            String   @db.Uuid
  event_type          String  // account_created | account_mapped
  account_id          String   @db.Uuid
  payload_json        Json
  created_at          DateTime @default(now()) @db.Timestamptz(6)
  published_at        DateTime? @db.Timestamptz(6)
  publish_attempts    Int      @default(0)
  last_publish_error  String?

  @@index([published_at, created_at])
  @@map("account_provisioning_outbox")
}
```

### 4. Enforce NOT NULL (after test-data wipe — see below)
- `contacts.account_id` -> NOT NULL
- `raw_interactions.account_id` -> NOT NULL

### 5. Test-data wipe (run BEFORE adding NOT NULL constraints)
```sql
TRUNCATE TABLE
    interaction_contact_links,
    interaction_summary_entries,
    interaction_insights,
    interaction_summaries,
    raw_interactions,
    calendar_event_interaction_links,
    contacts,
    pending_account_mapping_signals,
    pending_account_mappings,
    pending_validations,
    accounts
RESTART IDENTITY CASCADE;
```
This is the test-data wipe gate per design Section 7.2.

## Steps
1. Apply schema changes in two migrations: (a) additive changes only; (b) run the wipe + NOT NULL constraints.
2. `npx prisma migrate dev --name contact_quality_phase_1_5_additive` and `--name contact_quality_phase_1_5_enforce`.
3. Verify both migrations applied cleanly.
4. Commit + PR titled `chore(prisma): contact quality phase 1.5 schema + enforcement`.

## Acceptance
- After migrations: `\d contacts` shows `account_id uuid NOT NULL`.
- `\d raw_interactions` shows `account_id uuid NOT NULL`.
- `\d accounts` shows `state varchar NOT NULL DEFAULT 'active'`.
- `\d account_provisioning_outbox` shows the new table.
- `\d pending_account_mappings` shows the new Phase 1.5 lifecycle columns.
