# Next Session — Start Here

**Project:** Phase 2 — Granola.ai transcript ingestion integration.
**Last session:** 2026-05-24 (Phase 2a/2b foundation fully verified end-to-end; eq-frontend#418 merged; KMS smoke test PASSED).
**Status:** ✅ **PHASE_2_FOUNDATION_VERIFIED** — vault + tables + KMS live in production. Ready to build Phase 2c.

**Paste-ready opening prompt for the next session:**
`docs/superpowers/specs/2026-05-24-phase-2c-session-prompt.md`

That prompt is self-contained. It walks the new agent through mandatory reads → 2-paragraph confirmation → Phase 2c implementation (Granola HTTP API client) → Codex pre-merge review → ship.

---

## What shipped this session (2026-05-24)

### Comments-generator bug ROOT-CAUSED and FIXED ✅

**Root cause:** Prisma 5.22's DMMF (sent via JSON-RPC to generators) does NOT include `schema` on Model objects, even for `@@schema()`-annotated multiSchema models. Verified empirically via a probe generator that dumped the full DMMF — 0 `"vault"` references outside the `datasources` block. The `@onozaty/prisma-db-comments-generator@1.5.0` reads `model.schema ?? void 0`, always gets undefined, always emits unqualified `COMMENT ON TABLE "foo"`. For public-schema tables this works (Postgres `search_path` includes `public`); for vault tables it fails relation-not-found.

NOT a bug in the generator. NOT fixable by upgrading to 1.7.0 (changelog 1.5.0→1.7.0 added comment-filtering features, no DMMF changes). The actual deep fix would be upgrading Prisma to 7.x (which DOES emit `model.schema`) — tracked under Linear EQ-11.

**Fix applied (minimum blast radius):** `ignorePattern = "^(user_credentials|credential_access_log)$"` added to the `generator dbComments` block in `prisma/schema.prisma`. The generator's source hard-skips matching models. Vault tables get zero COMMENT ON SQL; their rich docstrings live in schema.prisma + `services/vault/README.md`. 7 lines added; 1 file changed.

### eq-frontend#418 unblocked + merged ✅

- Fix committed as eq-frontend `42e7d48` on `phase-2/granola-vault-schema`
- Hit P3009 on next Vercel build (leftover failed-migration record from prior bad build was blocking Prisma's queue)
- Resolved via Neon MCP `UPDATE _prisma_migrations SET rolled_back_at = NOW()` on row `944fe722-58a4-4d07-99d6-1c321bee058f`
- Pushed empty retrigger commit `8a41dc7`
- Vercel preview SUCCESS
- Admin-override squash-merged as `7905222` on eq-frontend main (live-db check still failing — pre-existing CI gap, separate scope)
- Vercel production deploy `2he8eDSfSLdapZ1eRXa6mSpjJkdq` READY at 2026-05-24 10:40:22Z

### Production Neon migration timeline (post-merge, all clean)

| Migration | Status |
|---|---|
| `20260519100424_update_comments` | Applied (pre-session) |
| `20260523100441_granola_vault_schema` | Applied (vault schema + 3 tables) |
| `20260523235715_update_comments` | rolled_back at 10:16:11 (our resolve) |
| `20260524101648_update_comments` | Applied (preview build of 8a41dc7) |
| `20260524103705_update_comments` | Applied (production deploy of 7905222) |

### Phase 2b KMS smoke test PASSED end-to-end ✅

Run via local `boto3` with `EQ_VAULT_*` credentials fetched from Railway env vars. Three checks green:

1. **Positive** (4-field EncryptionContext: tenant_id + user_id + provider + credential_id) → `GenerateDataKey` succeeded, returned 32-byte AES-256 plaintext + 184-byte KMS-wrapped CiphertextBlob, KeyId matches expected ARN
2. **Negative** (3-field, missing credential_id) → `AccessDeniedException` raised — LOCKED-40 enforced by IAM policy
3. **Identity** — ARN exactly `arn:aws:iam::211125681610:user/eq-vault-service` (confirmed running as the dedicated vault service user, not stale local AWS creds)

---

## What's verified live in production

| Layer | Status |
|---|---|
| AWS KMS CMK `59a0e2bc-c636-45e8-bccf-427ad2426ad8` (alias `eq-user-secrets`) | ✅ Auto-rotation enabled, LOCKED-40 enforced |
| IAM user `eq-vault-service` + identity policy | ✅ Empirically verified — rejects 3-field context |
| Vault Python module (services/vault/, 71 unit tests) | ✅ Live on Railway (deployment `2ce20b0e`) |
| Production Neon `vault` schema | ✅ Exists |
| `vault.user_credentials` (15 cols, PK, UNIQUE(tenant_id,user_id,provider), idx(status,last_polled_at), 2 FKs) | ✅ Live |
| `vault.credential_access_log` (11 cols, PK, 2 idxs, 3 FKs) | ✅ Live |
| `public.external_integration_runs` (17 cols, PK, UNIQUE(tenant_id,user_id,provider,external_id), 3 idxs, 3 FKs) | ✅ Live |
| eq-frontend main (Prisma schema.prisma with vault models + ignorePattern fix) | ✅ Commit `7905222` |
| live-transcription-fastapi `/health` | ✅ HTTP 200 |
| eq-frontend production deploy | ✅ READY (commit `7905222`) |

---

## What's next: Phase 2c — Granola HTTP API client (~0.5 day)

Per `tasks/granola-integration-plan.md` §Phase 2c:

**New file:** `services/granola_ingestion/api_client.py`

**Methods:**
```python
class GranolaAPIClient:
    async def list_folders(self) -> list[GranolaFolder]: ...
    async def list_notes(self, folder_id: str, created_after: datetime | None) -> list[GranolaNoteSummary]: ...
    async def get_note_detail(self, note_id: str) -> GranolaNoteDetail: ...
```

**Configuration:**
- Base URL: `https://public-api.granola.ai/v1` (per Phase 0 empirical verification — brainstorm doc's `api.granola.ai` was wrong)
- Filter param: `created_after` (per Phase 0 empirical verification — brainstorm doc's `since` was wrong)
- Timeout: 30s per request
- Retry strategy on 5xx/network: exponential backoff with jitter (1s → 2s → 4s → 8s, max 4 retries)
- 429 handling: honor `Retry-After` header

**Structured error codes** (`granola_ingestion/errors.py`):
- `auth_failed` (401)
- `folder_not_found` (404 on folder lookup)
- `granola_5xx` (502/503/504)
- `granola_429` (rate limit)
- `granola_timeout` (httpx.TimeoutException)
- `granola_parse_error` (Pydantic validation fails)

**Pydantic models** matching empirically-verified Granola response shapes:
- `GranolaFolder`: `id`, `name`, `parent_folder_id`
- `GranolaNoteSummary`: `id`, `title`, `created_at`, `updated_at`, `folder_membership`
- `GranolaNoteDetail`: full payload incl. `attendees`, `calendar_event`, `transcript`, `summary_markdown`, `summary_text`, `web_url`

**Unit tests** (httpx mock transport):
- Happy path for each method
- 5xx with retry → eventual success
- 5xx exhausted → `granola_5xx`
- 401 → `auth_failed` (no retry)
- 429 with Retry-After → honored
- Malformed response → `granola_parse_error`

**Codex pre-merge review:** mandatory (4-round soft cap; extend if real bugs surface each round, per Phase 2b precedent of 7 rounds).

---

## What this session's work does NOT include

- Phase 2d (adapter + Path 2 logic) — separate session, ~1.5 days
- Phase 2e (scheduler) — separate session, ~0.5 day
- Phase 2f (admin endpoints) — separate session, ~0.5 day
- Phase 2g (transactional email) — separate session, ~0.5 day
- Phase 3 (frontend) — separate session, ~2 days
- Phase 4 (production E2E testing) — separate session, ~1 day
- Fixing the live-db CI gap (Linear EQ-11 family)
- Upgrading Prisma 5.22 → 7.x (Linear EQ-11 family)

---

## CRITICAL — What NOT to re-litigate

- Phase 2a (Prisma migration) is merged to eq-frontend main; vault tables exist in production.
- Phase 2b (vault module) is merged to live-transcription-fastapi main; module live as inert code; KMS smoke test passed.
- The comments-generator multiSchema fix path (Path B / ignorePattern) was chosen after empirical investigation; don't reopen unless you have new information.
- LOCKED-23 through LOCKED-44 are non-negotiable without strong new information + explicit user authorization to revisit.

---

## NEW lessons codified this session (tasks/lessons.md)

1. **Prisma 5.22 DMMF omits `model.schema` for generators** — empirically verified; generators that read `model.schema` always get undefined under 5.x. Public tables resolve via `search_path`; non-default schemas break. The deeper fix is Prisma 7.x upgrade.
2. **P3009 leftover failed-migration record blocks all subsequent migrations** — fix the root cause AND mark the failed row as `rolled-back` (or `applied`). The "two-failure cascade" pattern: build 1 fails on real bug, fix lands, build 2 still fails with P3009.
3. **pnpm + tools with wide major-version ranges may resolve different majors than your project** — `@prisma/generator-helper: ^5||^6||^7` resolved to 7.5.0 while CLI is at 5.22. The helper's type signature is forward-looking; the actual JSON-RPC wire format is what the CLI emits.
4. **Vercel preview builds run against the SAME production Neon DB in this setup** — preview = production for migrations. The PR being unmerged doesn't mean its DB-state changes are unmerged; they may already be applied.

---

## Stop conditions for the next session

- `/context-restore` returns NO_CHECKPOINTS or wrong checkpoint title
- MEMORY.md Active Work doesn't read "PHASE_2_FOUNDATION_VERIFIED" / "PHASE_2C_NEXT"
- live-transcription-fastapi main is NOT at `7f98920` (or descendant)
- eq-frontend main is NOT at `7905222` (or descendant)
- Production `/health` returns non-200
- Vault schema + 3 tables NOT present in production Neon (verify via Neon MCP)
- AWS infrastructure missing (verify via `aws kms describe-key` + `aws iam get-user`)
- Another agent actively working in live-transcription-fastapi within the last hour

---

## Open items NOT in Phase 2c scope (do NOT pull forward)

1. **Federated identity** — blocked on Railway not supporting OIDC. Revisit when Railway adds support.
2. **Second Postgres role + engine for vault** — defense-in-depth above accessor + ALLOWLIST.
3. **Schema separation for credential_access_log via role permissions** — DB-layer enforcement of append-only.
4. **Automated access-key rotation reminder** — warn if access key age > 90 days.
5. **AES-GCM nonce-reuse detection monitoring**.
6. **Cross-region replicated CMK** — currently us-east-1 only.
7. **CloudTrail-based anomaly detection** — alert on unusual KMS access patterns.
8. **Audit-credential reconciliation job** — flag phantom audit rows (audit success with no matching credential row).
9. **Alerting wire-up** (Slack/Resend) for credential breakage events.
10. **Org-admin bulk-onboarding** — Phase 3 trigger at >10 users.
11. **Reverse-sync, webhooks-not-polling** — LOCKED-27 explicit defer.
12. **Prisma schema drift investigation + cutting-edge prevention** — tracked at Linear EQ-11.
13. **eq-frontend live-db CI workflow `DIRECT_DATABASE_URL` gap** — pre-existing; ships PRs UNSTABLE via admin-override; track under Linear EQ-11 family.
14. **Prisma 5.22 → 7.x upgrade in eq-frontend** — would deep-fix the comments-generator multiSchema gap and eliminate the ignorePattern workaround. Major version migration. Track under Linear EQ-11 family.

---

## AWS account state (verified 2026-05-23; no changes this session)

- Account ID: `211125681610`
- Region: us-east-1
- IAM user `eq-vault-service` exists with inline policy `eq-vault-service-kms-policy`
- KMS CMK `59a0e2bc-c636-45e8-bccf-427ad2426ad8` (alias `alias/eq-user-secrets`)
- Auto-rotation enabled (annual; next 2027-05-23)
- Access key `AKIATCKASHXFPCDN6NXX` active; secret in Railway env vars only

---

## Railway state (verified 2026-05-24)

- live-transcription-fastapi project `847cfa5a-b77c-4fb0-95e4-b20e8773c23e`
- Production environment `e4c5ec15-1931-4632-9e58-92d9c6be4261`
- Service `live-transcription-fastapi` `59a69f3d-9a24-4041-942a-891c4a81c5fb`
- Latest deployment: `2ce20b0e-9de3-42a5-ac66-af4cbef982d6` (Phase 2b merge) SUCCESS
- `/health` 200 verified at https://live-transcription-fastapi-production.up.railway.app/health
- 4 vault env vars set: `EQ_VAULT_AWS_ACCESS_KEY_ID`, `EQ_VAULT_AWS_SECRET_ACCESS_KEY`, `EQ_VAULT_KMS_KEY_ALIAS=alias/eq-user-secrets`, `EQ_VAULT_AWS_REGION=us-east-1`

---

## Vercel state (eq-frontend, verified 2026-05-24)

- Project ID: `prj_0wDppCftk1VrSAsYswI5pnNRHdN8`
- Team ID: `team_Hnnnu6r1trggeAXYWHXpKfMt`
- Most recent production deploy: `2he8eDSfSLdapZ1eRXa6mSpjJkdq` READY at 2026-05-24 10:40:22Z (commit 7905222)
- Vercel MCP authenticated and available

---

## Linear EQ-11 — schema drift investigation (separate work; related)

https://linear.app/eq-core/issue/EQ-11/investigate-prisma-schema-drift-in-eq-frontend-design-cutting-edge

Two-phase scope (audit + cutting-edge prevention design). DO NOT execute in Phase 2c session. Items related to EQ-11's family: comments-generator multiSchema fix (RESOLVED via ignorePattern this session), live-db CI workflow `DIRECT_DATABASE_URL` gap (still open), Prisma 5.22 → 7.x upgrade (would deep-fix the multiSchema gap).

---

## Build session entry prompt

Paste the contents of `docs/superpowers/specs/2026-05-24-phase-2c-session-prompt.md` as the opening message of the next session.

That prompt contains:
- Mandatory reads list (12 numbered items)
- Phase 2c implementation scope + expected file structure
- User posture rules + critical disciplines
- Stop conditions
- Commits summary
- AWS + Railway + Vercel + Neon state
