# Next Session — Start Here

**Project:** Phase 2 — Granola.ai transcript ingestion integration.
**Last session:** 2026-05-23 (Phase 0 + Phase 1 + Phase 2a all shipped in a single multi-hour session).
**Status:** ✅ PHASE_2A_COMPLETE — Phase 2b (vault Python module) is the next step.

**Paste-ready opening prompt for the next session:**
`docs/superpowers/specs/2026-05-23-phase-2b-next-session-prompt.md`

That prompt is self-contained. It mirrors the structure of the prompt that opened the prior session and walks the new agent through mandatory reads → 2-paragraph confirmation → Phase 2b execution.

---

## What shipped this session (2026-05-23)

### Phase 0 — Pre-flight verification (~0.5 day) ✅

- `scripts/verify_consumer_contracts.py` confirmed LOCKED-35 envelope shape accepted by all 3 known consumers (action-item-graph strict enum, eq-structured-graph-core loose, eq-interaction-threads loose).
- Granola API verified empirically against Peter's real key. **Two factual errors in the brainstorm doc caught + amended:**
  - Base URL: `api.granola.ai` → `public-api.granola.ai/v1` (correct per docs.granola.ai)
  - Filter param: `since` → `created_after` (correct per docs)
- Neon schema probe: vault schema + new tables absent; LOCKED-25 FK landmine reconfirmed (`meeting` lowercase exists in interaction_types lookup).
- Feature branch `phase-2/granola-integration` created in live-transcription-fastapi off origin/main.

### Phase 1 — AWS infrastructure (~0.5 day) ✅

- IAM user `eq-vault-service` created (Arn `arn:aws:iam::211125681610:user/eq-vault-service`).
- KMS CMK `59a0e2bc-c636-45e8-bccf-427ad2426ad8` created with alias `alias/eq-user-secrets`.
- KMS key policy + IAM identity policy applied with **tightened** LOCKED-40 binding (`ForAllValues:StringEquals + Null:false` on Encrypt/Decrypt/GenerateDataKey — strictly more secure than the plan's literal text; user-approved tightening).
- AWS access keys created; 4 Railway env vars set via Railway MCP (`EQ_VAULT_AWS_ACCESS_KEY_ID/SECRET/KMS_KEY_ALIAS/REGION`).
- **KMS auto-rotation ENABLED** (annual cadence; next 2027-05-23). Layer 3 master key rotation only — user's Granola API keys (Layer 1) are NEVER touched.
- `services/vault/` directory created with comprehensive README + canonical policy JSON files for audit.

### Phase 2a — Prisma schema + migration (~0.5 day, +30 min audit log addition) ✅

- 4 new Phase 2a-related items added beyond the locked plan, all user-approved:
  - **CredentialAccessLog audit table** added to address discovered shortcut "no credential-read forensic log". Phase 2a scope expanded to include this table in the migration.
  - **IAM policy tightening** beyond the locked plan literal text (`ForAllValues:StringEquals + Null:false` vs the loose `StringEquals` in the plan).
  - **KMS auto-rotation enabled** during Phase 2a (had been documented but not enabled).
  - **Linear EQ-11** created for the discovered Prisma schema drift investigation.
- multiSchema preview feature enabled; `schemas = ["public", "vault"]` declared.
- 176 existing models/enums/views bulk-annotated with `@@schema("public")` via Python script (mechanical; required by Prisma 5.22 multiSchema enforcement).
- 3 new models added: `vault.UserCredential`, `vault.CredentialAccessLog`, `public.ExternalIntegrationRun` (with `granola_note_snapshot` JSONB per LOCKED-44).
- Back-refs added on `Tenant`, `User`, `Account`.
- **Migration hand-written** (NOT generated via `prisma migrate dev`) because the auto-generated diff between schema.prisma and production Neon contained 350+ statements of pre-existing drift (63 DROP TABLEs, enum/index/FK changes) unrelated to this work. The drift is tracked at Linear EQ-11 for separate investigation.

---

## What's next: Phase 2b

| Phase | Time | Description |
|---|---|---|
| **Phase 2b** | **~0.5 day** | **Vault Python module** — encryption.py + audit.py + user_credentials.py + errors.py + __init__.py + AsyncMock unit tests. Uses the AWS infrastructure already provisioned in Phase 1. Implements LOCKED-40/41/42/43 at the Python layer. |
| Phase 2c | ~0.5 day | Granola API client (httpx + structured errors + Pydantic models for the corrected `public-api.granola.ai/v1` URL and `created_after` filter). |
| Phase 2d | ~1.5 days | Adapter + Path 2 scenario logic (the architecturally densest piece — likely deserves its own session). |
| Phase 2e | ~0.5 day | Scheduler (external Railway cron + DBOS queue + SetWorkflowID per LOCKED-39). |
| Phase 2f | ~0.5 day | Admin endpoints (`/validate`, `/connect`, `/rotate`, `/folder`, `/status`, `/disconnect`). |
| Phase 2g | ~0.5 day | Transactional email on credential breakage (Resend). |
| Phase 3 | ~2 days | Frontend (Granola Connect settings page + EQ-native Pending Approvals). |
| Phase 4 | ~1 day | Testing (unit + integration + production E2E). |
| Phase 5 | varies | Codex pre-merge review gate (4-round soft cap). |
| Phase 6 | ~0.5 day | Deploy + verify (cross-repo order: eq-frontend Prisma first → live-transcription-fastapi). |

**Estimated remaining:** ~6 days across 3-4 more sessions.

**Realistic next session:** Phase 2b is a focused ~0.5-day chunk. If energy permits after 2b, Phase 2c (Granola HTTP client) is a natural same-session continuation. Phase 2d (adapter logic, 1.5 days) deserves its own dedicated session.

---

## CRITICAL — What NOT to relitigate

The plan's 22 LOCKED decisions (LOCKED-23 through LOCKED-44) are non-negotiable without strong new information. Phase 2a added zero new LOCKED decisions; it only validated existing ones.

Phase 0 amendments to the plan (commit `cbc5112`):
- Base URL is `public-api.granola.ai/v1` (NOT `api.granola.ai/v1`)
- Time filter param is `created_after` (NOT `since`)

Phase 2a amendments (in services/vault/README.md, NOT yet in granola-integration-plan.md):
- IAM policy uses `ForAllValues:StringEquals + Null:false` (tighter than plan's literal `StringEquals`)
- `vault.credential_access_log` table is now part of the design (not in the original plan)
- Federated identity (Phase 2.1 candidate #3) is blocked by Railway lacking OIDC support — documented as platform constraint, not a free choice

---

## 4 commits to internalize before Phase 2b

**live-transcription-fastapi** branch `phase-2/granola-integration`:
- `0f86cba` — Phase 2a discoveries: KMS auto-rotation enabled + audit log spec + EQ-11 reference
- `8f3127f` — Phase 1 AWS infrastructure provisioned + audit docs
- `cbc5112` — Phase 0 plan amendments (Granola base URL + filter param empirical corrections)
- `bd458ec` — Phase 2 brainstorm + plan locked (predates this session)

**eq-frontend** branch `phase-2/granola-vault-schema`:
- `cf870b4` — Phase 2a Prisma migration: vault schema + 3 new tables
- `556b046` — Phase 1 comments-generator cleanup

All 6 commits are **local-only**. Nothing pushed. Future PR pushes require explicit user authorization.

---

## NEW lessons codified this session

1. **Always verify branch BEFORE commits in shared checkouts.** Prior session experienced a silent branch switch (likely caused by another active agent in the same checkout). Recovery required a user-authorized `git reset --hard`. Discipline: `git branch --show-current` immediately before every `git commit`.

2. **Never auto-apply Prisma migrate diff against drifted production.** Prior session generated a 52KB / 375-statement diff that included 63 DROP TABLEs unrelated to our work. Hand-write the migration with only additive statements. The drift itself goes to a dedicated investigation issue.

3. **Cutting-edge security posture is platform-relative.** Federated identity is the cutting-edge pattern when the deployment platform supports it. Railway doesn't (today). The cutting-edge MVP move on Railway becomes: long-lived AWS keys + minimum-privilege IAM + Encryption Context binding + audit log + rotation cadence. Match the pattern to the platform.

4. **Cross-repo deploy coordination matters.** Phase 2a Prisma migration in eq-frontend must deploy BEFORE Phase 2b vault module in live-transcription-fastapi can be smoke-tested against the test tenant. The Phase 6 deploy ordering is non-negotiable. Document this in PR descriptions when they go up.

---

## Stop conditions for the next session

- /context-restore returns NO_CHECKPOINTS or the wrong checkpoint title
- MEMORY.md Active Work doesn't read "PHASE_2A_COMPLETE_PHASE_2B_NEXT"
- AWS infrastructure missing (verify via `aws kms describe-key --key-id 59a0e2bc-...` + `aws iam get-user --user-name eq-vault-service`)
- Railway env vars missing (`EQ_VAULT_AWS_ACCESS_KEY_ID` + 3 others)
- Another agent actively working in the same repo within last hour (shared-infra collision)
- Production /api/health returns non-200

---

## Open items NOT in MVP (Phase 2.1+, do NOT pull forward)

1. **Federated identity** — blocked on Railway not supporting OIDC. Revisit when Railway adds support OR when EQ evaluates platform migration.
2. **Second Postgres role + engine for vault** — defense-in-depth above the accessor + ALLOWLIST.
3. **Schema separation for credential_access_log via role permissions** — DB-layer enforcement of append-only (currently app-layer only).
4. **Automated access-key rotation reminder** — periodic check that warns if access key age > 90 days.
5. **AES-GCM nonce-reuse detection monitoring**.
6. **Cross-region replicated CMK** — currently us-east-1 only.
7. **CloudTrail-based anomaly detection** — alert on unusual KMS access patterns.
8. **Alerting wire-up** (Slack webhook OR Resend) for credential breakage events.
9. **Org-admin bulk-onboarding** — Phase 3 trigger at >10 users.
10. **Reverse-sync, webhooks-not-polling** — LOCKED-27 explicit defer.
11. **Prisma schema drift investigation + cutting-edge prevention** — tracked at Linear EQ-11.

---

## AWS account state (verified 2026-05-23)

- Account ID: `211125681610`
- Region: us-east-1
- IAM user `eq-vault-service` exists with inline policy `eq-vault-service-kms-policy`
- KMS CMK `59a0e2bc-c636-45e8-bccf-427ad2426ad8` (alias `alias/eq-user-secrets`)
- Auto-rotation enabled (annual; next 2027-05-23)
- Access key `AKIATCKASHXFPCDN6NXX` created; secret in Railway env vars only

---

## Linear EQ-11 — schema drift investigation (separate work)

https://linear.app/eq-core/issue/EQ-11/investigate-prisma-schema-drift-in-eq-frontend-design-cutting-edge

Two-phase scope (audit + cutting-edge prevention design). DO NOT execute in Phase 2b. Acknowledge it exists; understand the Phase 2a migration was hand-written to bypass the drift cleanly.

---

## Build session entry prompt

Paste the contents of `docs/superpowers/specs/2026-05-23-phase-2b-next-session-prompt.md` as the opening message of the next session.

That prompt contains:
- Mandatory reads list (11 numbered items)
- Top LOCKED decisions for Phase 2b (LOCKED-40/41/42/43)
- Phase 2b implementation order (errors → encryption → audit → user_credentials → __init__ → smoke test → Codex)
- User posture rules (including NEW branch-verify discipline)
- Stop conditions
- Commits summary
- AWS + Railway state
