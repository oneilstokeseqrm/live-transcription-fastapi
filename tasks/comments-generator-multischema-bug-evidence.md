# Bug evidence: @onozaty/prisma-db-comments-generator@1.5.0 multiSchema gap

**Created:** 2026-05-24 — Phase 2b session end. Load-bearing forensic dump for next session's `/investigate`.

**Status:** Blocker for eq-frontend#418 (Phase 2a Prisma migration). Vercel preview build fails; production deploy on merge would fail identically.

**Symptom:** the auto-generated `<timestamp>_update_comments` migration emits unqualified `COMMENT ON COLUMN user_credentials...` instead of `vault.user_credentials...`. The migration fails on `prisma migrate deploy` because Postgres can't find `user_credentials` in the public schema (it lives in vault).

---

## 1. Failing Vercel preview build (the symptom)

**Deployment:** `eq-frontend-6cuw79l2a-peter-oneils-projects.vercel.app`
**Branch:** `phase-2/granola-vault-schema`
**Commit:** `c674330` (rebased from cf870b4 onto origin/main during merge prep)
**Status:** ERROR
**Created:** 2026-05-23T23:57:00Z

**Pull the logs:**
```bash
cd /Users/peteroneil/eq-frontend
vercel inspect https://eq-frontend-6cuw79l2a-peter-oneils-projects.vercel.app --logs
```

**Key log excerpt:**
```
2026-05-23T23:57:15.292Z  Comments generation completed: 20260523235715_update_comments
2026-05-23T23:57:15.316Z  ✔ Generated Prisma Database Comments (v1.5.0) to ./prisma/migrations in 857ms
[...]
2026-05-23T23:57:23.691Z  51 migrations found in prisma/migrations
2026-05-23T23:57:23.786Z  Applying migration `20260519100424_update_comments`
2026-05-23T23:57:23.797Z  Applying migration `20260523100441_granola_vault_schema`
2026-05-23T23:57:23.862Z  Applying migration `20260523235715_update_comments`
2026-05-23T23:57:23.868Z  Error: P3018
2026-05-23T23:57:23.868Z  A migration failed to apply.
2026-05-23T23:57:23.869Z  Migration name: 20260523235715_update_comments
2026-05-23T23:57:23.869Z  Database error code: 42P01
2026-05-23T23:57:23.870Z  Database error:
2026-05-23T23:57:23.870Z  ERROR: relation "user_credentials" does not exist
2026-05-23T23:57:23.872Z  DbError { severity: "ERROR", code: SqlState(E42P01), message: "relation \"user_credentials\" does not exist", file: Some("namespace.c"), line: Some(637), routine: Some("RangeVarGetRelidExtended") }
```

**What this tells us:**
- The granola_vault_schema migration succeeded (creates `vault.user_credentials`)
- The very next migration — the auto-generated `update_comments` — failed with "relation user_credentials does not exist" because it referenced the table WITHOUT the `vault.` qualifier
- The `update_comments` migration was generated at the EXACT moment of the build (`20260523235715` matches the build time), so it's regenerated on every Vercel build

---

## 2. Where the generator runs

In `eq-frontend/prisma/schema.prisma`:

```prisma
generator dbComments {
  provider = "prisma-db-comments-generator"
}
```

The generator runs whenever `prisma generate` runs. In Vercel build, `prisma generate` runs as part of the `postinstall: prisma generate` script. So every `pnpm install` re-emits the comments migration with a fresh timestamp.

`package.json` build script:
```
"build": "prisma generate && prisma migrate deploy && next build",
"postinstall": "prisma generate"
```

`package.json` dependency:
```
"@onozaty/prisma-db-comments-generator": "^1.5.0"
```

---

## 3. Generator source code (multiSchema awareness confirmed)

**Disk location:**
```
/Users/peteroneil/eq-frontend/node_modules/.pnpm/@onozaty+prisma-db-comments-generator@1.5.0_typescript@5.9.3/node_modules/@onozaty/prisma-db-comments-generator/dist/generator.cjs
```

(There's also `/Users/peteroneil/eq-frontend/node_modules/@onozaty/.ignored_prisma-db-comments-generator/` — that's pnpm's symlink target.)

**Key code paths confirming multiSchema-aware INTENT:**

Line 47 — keys comments dict by `${schema}.${tableName}`:
```js
comments[`${model.schema ? model.schema + "." : ""}${model.dbName}`] = {
  table: {
    schema: model.schema,
    tableName: model.dbName,
    ...
  },
  columns: {
    ...{
      schema: model.schema,
      tableName: model.dbName,
      ...
    }
  }
};
```

Line 198 — uses joinNames helper for SQL:
```js
`COMMENT ON TABLE ${joinNames(table.schema, table.tableName)} IS ${toStringLiteral(table.comment)};`
```

Line 223 — joinNames helper:
```js
var joinNames = (schema, tableName, columnName) => {
  let name = "";
  if (schema) {
    name += `"${schema}".`;
  }
  name += `"${tableName}"`;
  if (columnName) {
    name += `.${"\"" + columnName + "\""}`;
  }
  return name;
};
```

**Conclusion:** the generator's source CORRECTLY handles multiSchema IF `model.schema` is populated. The bug is that `model.schema` must be null/undefined for our vault models in the DMMF that this version of the generator receives.

---

## 4. Schema annotations are correct in our schema.prisma

**File:** `/Users/peteroneil/eq-frontend/prisma/schema.prisma`

Generator block (line 1-9):
```prisma
generator client {
  provider        = "prisma-client-js"
  previewFeatures = ["views"]
}

generator dbComments {
  provider = "prisma-db-comments-generator"
}
```

Datasource (around line 10):
```prisma
datasource db {
  provider  = "postgresql"
  url       = env("DATABASE_URL")
  directUrl = env("DIRECT_DATABASE_URL")
  schemas   = ["public", "vault"]
}
```

(Verify with `git show phase-2/granola-vault-schema:prisma/schema.prisma | head -25`.)

UserCredential model (lines 4546-4584):
```prisma
model UserCredential {
  id                  String    @id @default(uuid()) @db.Uuid
  tenantId            String    @map("tenant_id") @db.Uuid
  userId              String    @map("user_id") @db.Uuid
  ...
  @@unique([tenantId, userId, provider])
  @@index([status, lastPolledAt])
  @@map("user_credentials")
  @@schema("vault")    ← THE ANNOTATION IS PRESENT
}
```

CredentialAccessLog has `@@schema("vault")` too. ExternalIntegrationRun has `@@schema("public")`.

All 176 pre-existing models have `@@schema("public")` (added by the bulk-annotation script in the prior session, required by Prisma 5.22 multiSchema validation).

**Conclusion:** the annotations are correct. The bug is downstream of the schema declaration.

---

## 5. Root cause hypothesis (unconfirmed)

The generator reads `model.schema` from Prisma's DMMF (the parsed schema model). For some reason, DMMF does NOT populate `.schema = "vault"` for our vault models even though `@@schema("vault")` is declared.

Possible causes (any of these or some combination):
- Prisma 5.22's DMMF API for multiSchema may have moved the schema field to a different path the generator doesn't read
- The generator may be reading from `model.dbSchema` instead of `model.schema` (or vice versa)
- `multiSchema` is a `previewFeatures` flag; the generator's DMMF integration may pre-date multiSchema being supported and not look for the schema field at all
- Prisma may only populate `model.schema` when the schema is non-default; for `public` it's null, for `vault` it might depend on DMMF version

To confirm: add a `console.log(model)` patch to the generator inside `node_modules` and re-run `prisma generate`. Check what `model.schema` actually contains for `UserCredential` vs `Tenant` (which is in public).

---

## 6. Resolution paths (ranked)

### Path A: Upgrade to 1.7.0 (start here — lowest risk if it works)

```bash
cd /Users/peteroneil/eq-frontend
pnpm add -D @onozaty/prisma-db-comments-generator@1.7.0
pnpm install
DATABASE_URL=postgresql://placeholder npx prisma generate
# Inspect the generated migration:
ls -t prisma/migrations/ | head -3
cat prisma/migrations/<timestamp>_update_comments/migration.sql | grep -A1 "user_credentials\|credential_access_log"
# Should see: COMMENT ON TABLE "vault"."user_credentials" IS '...';
```

Compare against the generator's CHANGELOG between 1.5.0 → 1.7.0:
- npm view @onozaty/prisma-db-comments-generator versions  → 1.0.0, 1.0.1, 1.0.2, 1.0.3, 1.1.0, 1.2.0, 1.3.0, 1.4.0, 1.5.0, 1.6.0, 1.7.0

If 1.6.0 / 1.7.0 release notes mention multiSchema, this is likely the fix.

### Path B: Check for an exclude/skip configuration option

Read the generator's README:
```
cat /Users/peteroneil/eq-frontend/node_modules/.pnpm/@onozaty+prisma-db-comments-generator@1.5.0_typescript@5.9.3/node_modules/@onozaty/prisma-db-comments-generator/README.md
```

If there's an option like `excludeModels` or `excludeTables`, configure it to skip vault tables in `schema.prisma`:

```prisma
generator dbComments {
  provider     = "prisma-db-comments-generator"
  excludeModels = ["UserCredential", "CredentialAccessLog"]  // syntax TBD per README
}
```

Trade-off: vault tables don't get COMMENT ON SQL applied. Acceptable for MVP; can be hand-applied later.

### Path C: Patch the generator's DMMF integration

Fork the generator, find where it reads `model.schema`, fix to use the correct DMMF API path (likely `model.dbSchema` or `prismaSchema.models[i].dbSchema` or similar), submit upstream PR. Pinned fork in package.json until upstream merges.

Effort: 2-4 hours; longest path but most thorough.

### Path D: Switch to a different generator that handles multiSchema natively

Search for alternatives:
- `prisma-comments-generator` (different package)
- `@kanlukasz/prisma-db-comments-generator`
- Native Prisma `///` doc comments may produce comments without a generator

Trade-off: behavior change; possibly different output format.

### Path E: Disable the generator entirely + check in static comment SQL

Remove the `generator dbComments` block from schema.prisma. Hand-write a one-time migration with all COMMENT ON SQL (extracted from current `comments-latest.json`). New comments require manual SQL going forward.

Trade-off: loses automation. Not recommended unless paths A-D fail.

---

## 7. Local reproduction (verify the fix works before pushing)

After applying a fix (Path A-D), reproduce the build locally to verify:

```bash
# In an isolated worktree to avoid polluting the active checkout
git -C /Users/peteroneil/eq-frontend worktree add /tmp/eq-frontend-fix-verify phase-2/granola-vault-schema

cd /tmp/eq-frontend-fix-verify
pnpm install
# (apply the fix here if not already on the branch)

# Set up a test database (Neon branch is easiest)
# OR use a local Postgres with the vault schema pre-created

DATABASE_URL=<test-db-url> DIRECT_DATABASE_URL=<test-db-direct-url> pnpm run build

# If the build succeeds without P3018 errors, the fix works.
# Inspect the generated update_comments migration to confirm
# COMMENT ON TABLE "vault"."user_credentials" appears qualified.

# Clean up
cd ~
git -C /Users/peteroneil/eq-frontend worktree remove /tmp/eq-frontend-fix-verify
```

---

## 8. Related context

- **Linear EQ-11:** https://linear.app/eq-core/issue/EQ-11/investigate-prisma-schema-drift-in-eq-frontend-design-cutting-edge — eq-frontend has known Prisma config brittleness (schema drift); this multiSchema generator gap is in the same family. Consider tracking the resolution under EQ-11 or as a new linked issue.

- **Prior session's hand-written Phase 2a migration:** the `20260523100441_granola_vault_schema` migration was hand-written (not auto-generated) precisely to bypass EQ-11 drift. The comments-generator bug is a NEW eq-frontend repo-config gap discovered when our multiSchema introduction exposed it.

- **Other agents active in eq-frontend:** the user runs multiple parallel agents using git worktrees. The main checkout `/Users/peteroneil/eq-frontend` is often on a non-main branch (other agent's work). DO NOT switch the main checkout's branch. Use the worktree pattern (Section 7 above) for any eq-frontend operations.

- **Vercel access:** Vercel MCP is authenticated (next session can use `mcp__vercel__list_deployments`, `mcp__vercel__get_deployment`, `mcp__vercel__get_deployment_build_logs` directly). Project ID `prj_0wDppCftk1VrSAsYswI5pnNRHdN8`; Team ID `team_Hnnnu6r1trggeAXYWHXpKfMt`.

---

## 9. What's NOT this bug

To save investigation time, here's what's been ruled out:

- ❌ It's not in our vault Python code (Phase 2b shipped + deployed + `/health` 200; vault module loads as inert code).
- ❌ It's not in the Phase 2a migration SQL itself (the `20260523100441_granola_vault_schema` migration creates `vault.user_credentials` correctly; that step SUCCEEDS in the build log).
- ❌ It's not a `@@schema("vault")` annotation problem (verified the annotations are present in schema.prisma on the PR's branch).
- ❌ It's not a "comments-generator is not multiSchema-aware" problem (the source code shows it IS multiSchema-aware; the bug is in the DMMF wire-up).
- ❌ It's not the live-db CI workflow's `DIRECT_DATABASE_URL` env var gap (that's a SEPARATE pre-existing CI config issue documented in `lessons.md`; same PR also fails that check; fix is unrelated to the Vercel preview failure).
- ❌ It's not specific to PR #418's branch state (any branch that introduces multiSchema to eq-frontend would hit this).

---

## 10. Definition of done

The investigation is complete when:

1. Root cause identified (which DMMF field the generator reads vs which one Prisma 5.22 populates for multiSchema models)
2. Fix chosen from Paths A-E above (or a new path the investigation discovers)
3. Fix applied in a focused branch off eq-frontend's main
4. PR #418 rebased onto the fix
5. Vercel preview build succeeds on PR #418
6. User authorizes merge
7. Vercel production deploy succeeds + Prisma migration applies to Neon
8. Neon MCP probe confirms: `vault` schema exists, 3 tables present, FKs + indexes correct
9. live-transcription-fastapi Phase 2b KMS smoke test passes from Railway shell

Then Phase 2c (Granola HTTP API client) is unblocked.
