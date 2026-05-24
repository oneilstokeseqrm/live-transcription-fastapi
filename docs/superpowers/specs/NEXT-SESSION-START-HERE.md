# Next Session — Start Here

**Project:** Phase 2 — Granola.ai transcript ingestion integration.
**Last session:** 2026-05-24 (Phase 2b shipped + merged + deployed; eq-frontend#418 blocked by comments-generator multiSchema bug).
**Status:** ✅ **PHASE_2B_SHIPPED** + ⚠️ **EQ_FRONTEND_BLOCKED** — investigate + unblock comments-generator, then Phase 2c.

**Paste-ready opening prompt for the next session:**
`docs/superpowers/specs/2026-05-24-phase-2c-and-comments-generator-investigation-prompt.md`

That prompt is self-contained. It walks the new agent through mandatory reads → 2-paragraph confirmation → `/investigate` on the comments-generator bug → unblock eq-frontend#418 → KMS smoke test → Phase 2c.

---

## What shipped this session (2026-05-24)

### Phase 2b — Python vault module ✅ SHIPPED + MERGED + DEPLOYED

- **PR #24** merged as squash commit `7f98920` on main
- Railway deployment `2ce20b0e-9de3-42a5-ac66-af4cbef982d6` SUCCESS
- `/health` 200 verified on `live-transcription-fastapi-production.up.railway.app`
- Vault module loaded as **inert code** — nothing imports it yet (no production code path), so deploy was safe even without the eq-frontend Prisma tables
- 71 vault unit tests pass; 261 unit suite tests pass

### 7 rounds of Codex pre-merge review

Every round found real bugs. None false positives. Gate passed at R7.

| Round | Findings | Surface |
|---|---|---|
| R1 | atomicity within conn + AccessDenied mapping | P1 + P2 |
| R2 | Pool architecture + reactivate primitive + status reset | 2 P1 + P2 |
| R3 | tenant isolation in rotate + DB-error wrapping + status filter | 2 P1 + P2 |
| R4 | nested-pool deadlock (fixed in store only) | P1 |
| R5 | R4 propagation gap (rotate + reactivate also affected) + reactivate cursor reset | 2 P1 + P2 |
| R6 | BotoCoreError catch + rotate not-found audit FK fix | P1 + P2 |
| R7 | client lookup inside try block (P2 only; **0 P1; gate passes**) | P2 |

### Cross-agent feasibility consult (eq-llm-gateway)

The eq-llm-gateway team asked whether to extract `services/vault/` into a shared package for their LLM provider key storage. **Recommendation: build a parallel vault** (Option B), not extract. The vault was designed around per-user Granola credentials; LLM provider keys are typically per-tenant (org-level), and the EncryptionContext shape + poll-loop metadata fields don't generalize cleanly. Share patterns + AWS infrastructure templates + LOCKED decisions, not code. See full consult in session transcript.

---

## What's BLOCKED: eq-frontend#418 + comments-generator bug

### The bug

When Vercel runs the preview build for eq-frontend#418 (Phase 2a Prisma migration), the `@onozaty/prisma-db-comments-generator@1.5.0` tool runs as part of `prisma generate` and emits a NEW migration on every build named `<timestamp>_update_comments`. That generated migration contains:

```sql
COMMENT ON COLUMN "user_credentials"."some_col" IS '...';
```

Note: **unqualified** `user_credentials`. The vault tables live in the `vault` schema (`@@schema("vault")` in our Prisma models), so this fails:

```
Error: P3018
Database error code: 42P01
Database error: ERROR: relation "user_credentials" does not exist
```

### What we know

- The generator's source code IS multiSchema-aware (uses `model.schema` and a `joinNames(schema, tableName)` helper). So the generator's intent is correct.
- The DMMF integration with Prisma 5.22 + our multiSchema annotations must not be wiring `model.schema = "vault"` through to the generator for the vault models. Root cause unconfirmed.
- Newer version 1.7.0 exists on npm; unknown if it fixes this.
- Same class of issue as Linear EQ-11 (eq-frontend schema drift) — these are eq-frontend repo-config gaps surfacing as merge blockers when our Phase 2 work touches load-bearing Prisma config.

### Bug evidence file (LOAD-BEARING for /investigate)

Full forensic dump at `tasks/comments-generator-multischema-bug-evidence.md`:
Vercel build logs, generator source code with exact disk path, root cause hypothesis, 5 ranked resolution paths with commands, local reproduction steps, what's NOT this bug. Without it, the next agent re-does file-spelunking. With it, they pick a path and execute.

### What this blocks

- **eq-frontend#418 cannot merge** until Vercel preview passes (same build pipeline as production).
- Phase 2a database tables (`vault.user_credentials`, `vault.credential_access_log`, `public.external_integration_runs`) don't exist in production Neon yet.
- **KMS smoke test against real CMK** (which requires Railway shell + the production tables) is blocked until eq-frontend deploys.
- **Phase 2c (Granola HTTP API client) can build code-only**, but should not deploy until vault tables exist; smoke testing Phase 2c would also need the tables.

### What this does NOT block

- Phase 2b (vault module) is shipped + running. No production breakage.
- Production /health 200 verified.
- No Granola customer flow exists yet, so no user-visible degradation.

---

## What's next: this session's job

| Phase | Time | Description |
|---|---|---|
| **/investigate comments-generator bug** | **~30-60 min** | Root-cause why DMMF doesn't pass `model.schema` for vault models. Try upgrading to 1.7.0; check for exclude/skip flag; consider patching. |
| Unblock eq-frontend#418 | ~30 min | Apply the chosen fix in a branch off eq-frontend's main; rebase #418; verify Vercel preview passes; surface for merge auth. |
| eq-frontend#418 merge + deploy | gated on user | Vercel deploys to production; Neon schema updated. |
| Verify production Neon | ~5 min | Neon MCP probe: vault schema + 3 tables present + FKs + indexes correct. |
| **Phase 2b KMS smoke test** | **~5 min** | Railway shell: positive (4-field context → success) + negative (3-field context → AccessDenied). |
| Phase 2c — Granola HTTP API client | ~0.5 day | services/granola_ingestion/api_client.py + Pydantic models + httpx mock tests. |
| Phase 2d | ~1.5 days | Adapter + Path 2 logic — deserves own session. |

---

## CRITICAL — What NOT to re-litigate

- Phase 2b is shipped + merged. Don't reopen the Codex rounds.
- The cross-agent consult recommendation (build parallel vault, don't extract) is locked. Don't re-extract.
- LOCKED-23 through LOCKED-44 are non-negotiable without strong new information.
- The Vercel preview failure was on PR #418 — confirmed; investigated; fix path documented. Don't re-investigate why it failed; investigate the comments-generator multiSchema gap.

---

## 1 commit on main (Phase 2b)

**live-transcription-fastapi** main:
- `7f98920` — feat(vault): Phase 2b — KMS-backed credential vault Python module (services/vault/) (#24)

The squashed commit collapses 8 underlying feature-branch commits across the Phase 2b implementation + 7 Codex review rounds. See the PR description for the round-by-round narrative.

**eq-frontend** branch `phase-2/granola-vault-schema` (PR #418, OPEN, BLOCKED):
- `c674330` — feat(prisma): Phase 2 Granola integration — vault schema + 3 new tables
- `de10461` — chore(prisma): commit Phase 1 comments-generator artifacts

---

## NEW lessons codified this session

1. **Vercel preview failure predicts production failure.** The preview build pipeline is identical to production deploy. If preview fails, production merge would fail the same way. Don't frame preview failures as "just preview." Treat them as production blockers. (Mistake I made; user correctly pushed back.)

2. **Prisma multiSchema exposes brittleness in third-party generators.** Tools like `@onozaty/prisma-db-comments-generator` that pre-date multiSchema may not pass the schema through their DMMF integration even when they're "multiSchema-aware" in their own code. Verify in a real build before assuming any generator handles multiSchema correctly.

3. **Pool-based vault accessors avoid nested-pool-acquire deadlock.** When holding a connection from a pool inside a transaction, never acquire a second connection from the same pool — it deadlocks at `max_size=1` or under N concurrent operations on pool size N. Use `write_audit_row_on_conn(conn=cred_conn)` not `write_audit_row(pool=pool)` when inside a transaction.

4. **Codex rounds find real bugs at progressively deeper layers.** Phase 2b ran 7 rounds; every round found real issues at NEW surfaces (atomicity → architecture → tenant isolation → concurrency → propagation → boundary coverage → initialization scope). The gate-passing round (R7) is the merge authorization signal — earlier rounds finding things doesn't mean "we're done after fixing them"; it means "keep going until clean."

5. **When pushing a branch that triggers Vercel preview, explicitly flag it to the user.** Push isn't a no-op operation in CI-integrated repos. The user deserves to know that their dashboard will light up with a build run.

---

## Stop conditions for the next session

- /context-restore returns NO_CHECKPOINTS or wrong checkpoint title
- MEMORY.md Active Work doesn't read "PHASE_2B_SHIPPED_EQ_FRONTEND_BLOCKED"
- live-transcription-fastapi main is NOT at `7f98920` (or descendant)
- eq-frontend `phase-2/granola-vault-schema` doesn't have `c674330` as tip
- Production `/health` returns non-200
- AWS infrastructure missing (verify via `aws kms describe-key` + `aws iam get-user`)
- Another agent actively working in eq-frontend within last hour

---

## Open items NOT in MVP (Phase 2.1+, do NOT pull forward)

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
13. **Comments-generator multiSchema fix** — THIS SESSION'S WORK; will move to LOCKED list once resolved.

---

## AWS account state (verified 2026-05-23; no changes this session)

- Account ID: `211125681610`
- Region: us-east-1
- IAM user `eq-vault-service` exists with inline policy `eq-vault-service-kms-policy`
- KMS CMK `59a0e2bc-c636-45e8-bccf-427ad2426ad8` (alias `alias/eq-user-secrets`)
- Auto-rotation enabled (annual; next 2027-05-23)
- Access key `AKIATCKASHXFPCDN6NXX` created; secret in Railway env vars only

---

## Linear EQ-11 — schema drift investigation (separate work; related)

https://linear.app/eq-core/issue/EQ-11/investigate-prisma-schema-drift-in-eq-frontend-design-cutting-edge

Two-phase scope (audit + cutting-edge prevention design). DO NOT execute in this session. The comments-generator bug is in the same family (eq-frontend repo-config brittleness exposed by Phase 2's multiSchema introduction); the fix can be tracked as part of EQ-11's scope or as a NEW Linear issue.

---

## Build session entry prompt

Paste the contents of `docs/superpowers/specs/2026-05-24-phase-2c-and-comments-generator-investigation-prompt.md` as the opening message of the next session.

That prompt contains:
- Mandatory reads list (12 numbered items)
- /investigate scope + expected output structure
- Phase 2c scope (after eq-frontend unblocks)
- User posture rules + critical disciplines
- Stop conditions
- Commits summary
- AWS + Railway + Vercel state
