# Next Session Opening Prompt (Phase 2b — Vault Python Module)

**Written:** 2026-05-23 end-of-session, after Phase 0 + Phase 1 + Phase 2a all completed in a single multi-hour session.

**Paste the block below as the opening message of the next Claude session.**

This is the canonical, paste-ready handoff. It mirrors the structure of the prompt that opened the prior session (which delivered Phase 0 + 1 + 2a). The new agent reads everything mandatory, writes a 2-paragraph confirmation, then executes Phase 2b.

---

```
You're picking up the Granola.ai transcript ingestion adapter — Phase 2b of a
multi-phase data-quality initiative on an AI-native customer intelligence
platform. The prior session shipped Phase 0 (pre-flight verification), Phase 1
(AWS infrastructure for the vault), and Phase 2a (Prisma schema + migration in
eq-frontend). All four commits are local-only on feature branches; nothing has
been pushed. Phase 2b implements the actual Python vault module that uses the
AWS infrastructure we just provisioned.

═══════════════════════════════════════════════════════════════════════
STEP 1 — RUN /context-restore FIRST
═══════════════════════════════════════════════════════════════════════

Before anything else, run /context-restore. It will load the most recent
checkpoint:

  ~/.gstack/projects/oneilstokeseqrm-live-transcription-fastapi/checkpoints/
  20260523-XXXXXX-phase-2a-complete-phase-2b-next.md

That checkpoint summarizes the prior session: Phase 0/1/2a all shipped, 4
commits across 2 repos, EQ-11 Linear issue created for schema drift
investigation, federated identity discovered blocked on Railway, several new
lessons codified. If /context-restore returns NO_CHECKPOINTS or a different
title, STOP and surface to the user — the handoff is broken.

═══════════════════════════════════════════════════════════════════════
STEP 2 — MANDATORY READS (in this order; do NOT skip)
═══════════════════════════════════════════════════════════════════════

Per the memory entry `feedback_complete_all_handoff_reads_before_action.md`:
complete EVERY read BEFORE starting Phase 2b implementation. Don't write code
mid-read. Use parallel tool calls to read fast.

1. THE EXECUTABLE PLAN (load-bearing; ~1060 lines AFTER Phase 0 amendments):
   tasks/granola-integration-plan.md
   - Read §Phase 2b in full
   - Note that §Phase 2a is now SHIPPED — don't re-execute it
   - Internalize LOCKED-40, LOCKED-41, LOCKED-42, LOCKED-43 (all directly
     load-bearing for Phase 2b)

2. THE PRIOR-SESSION HANDOFF + COMMITS (4 commits to internalize):
   - live-transcription-fastapi branch `phase-2/granola-integration`:
     • bd458ec — Phase 2 brainstorm + plan locked (predates this session)
     • cbc5112 — Phase 0 plan amendments (Granola base URL + filter param)
     • 8f3127f — Phase 1 AWS infrastructure + audit docs
     • 0f86cba — Phase 2a discoveries (KMS auto-rotation enabled + audit log
       spec + EQ-11 reference)
   - eq-frontend branch `phase-2/granola-vault-schema`:
     • 556b046 — Phase 1 comments-generator cleanup
     • cf870b4 — Phase 2a Prisma migration (vault schema + 3 new tables)
   - All 6 commits are local-only on feature branches. NOT pushed.

3. THE VAULT README (load-bearing for Phase 2b implementation):
   services/vault/README.md
   - LOCKED-40/42/43 invariants
   - The new "Credential audit log" section (added 2026-05-23)
   - Infrastructure inventory: KMS CMK 59a0e2bc-c636-45e8-bccf-427ad2426ad8
     (alias/eq-user-secrets), IAM user eq-vault-service, AccessKeyId
     AKIATCKASHXFPCDN6NXX, Railway env vars set
   - Smoke test (runs after Phase 2b ships)
   - Rotation procedures
   - Phase 2.1 hardening (do NOT pull forward without user approval)

4. THE BRAINSTORM BACKGROUND (~450 lines; reference, don't re-litigate):
   docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md
   - Note the empirical-correction blockquote near "Empirical Granola API
     findings" — corrects the original base URL + filter param errors that
     were caught in Phase 0

5. THE DBOS ARCHITECTURE DOC (mandatory before Phase 2e — NOT 2b — but skim
   §6.3 workflow vs step boundaries so the vault module's interface fits
   cleanly when Phase 2e's adapter calls it):
   docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md

6. AUTO-MEMORY INDEX (auto-loads, but verify):
   ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/MEMORY.md
   - Confirm Active Work entry reads "PHASE_2A_COMPLETE_PHASE_2B_NEXT"

7. PROJECT MEMORY SNAPSHOT:
   ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/
   project_granola_integration.md
   - Phase 2a complete state + lessons codified

8. LOAD-BEARING FEEDBACK MEMORIES (read in this order):
   - feedback_complete_all_handoff_reads_before_action.md (this is HOW you
     start the session — read everything before action)
   - feedback_envelope_contract_immutable.md (LOCKED-38 enforcement)
   - feedback_codex_pre_merge_gate.md (4-round soft cap; mandatory for the
     eventual Phase 2 PRs)
   - feedback_shared_infrastructure_collision.md (before any test-tenant write)
   - feedback_tenant_isolation.md (every query has tenant_id in WHERE)
   - feedback_branch_safety.md (feature branch protocol — and a NEW lesson
     codified from prior session: ALWAYS git branch --show-current
     IMMEDIATELY before any commit, in shared checkouts)
   - feedback_test_pattern_no_docker.md (AsyncMock + production E2E)

9. NEW LESSONS FROM THE PRIOR SESSION (2026-05-23):
   tasks/lessons.md sections to read:
   - "Always re-verify branch state before commits in shared checkouts"
     (NEW — prior session experienced a branch silently switching to main
     between commit prep and commit execution; recovery required
     user-authorized git reset --hard)
   - "Prisma migrate diff against real DB reveals drift — NEVER auto-apply
     generated migrations to drifted production" (NEW — prior session
     discovered 63 DROP TABLE in auto-generated diff; hand-wrote the
     migration with only additive statements)
   - "Cutting-edge security posture is platform-relative" (NEW — federated
     identity vs long-lived AWS keys depends on what the deployment
     platform supports; Railway doesn't support OIDC federation today, so
     long-lived keys + minimum-privilege + audit log + rotation is the
     genuine cutting-edge MVP pattern on Railway)
   - All prior lesson sections still apply (Source field validation, FK
     chain, Codex gate, Prisma @@unique, tenant DELETE, etc.)

10. LINEAR EQ-11 (the schema drift investigation):
    https://linear.app/eq-core/issue/EQ-11/investigate-prisma-schema-drift-in-eq-frontend-design-cutting-edge
    - DO NOT execute the drift investigation in this session
    - Just understand it exists + that the Phase 2a migration was hand-
      written to bypass the drift cleanly

11. AFTER ALL READS, write a 2-paragraph confirmation:
    - Para 1: What Phase 2b executes (files to create, KMS calls, AES-GCM
      flow, audit-log integration, allowlist gating)
    - Para 2: Critical LOCKED decisions for Phase 2b specifically
      (LOCKED-40, 41, 42, 43 — all four are directly load-bearing here)
    Then proceed with Phase 2b implementation.

═══════════════════════════════════════════════════════════════════════
STEP 3 — TOP LOCKED DECISIONS THAT GATE PHASE 2B
═══════════════════════════════════════════════════════════════════════

LOCKED-40: KMS EncryptionContext binds all FOUR fields:
  {tenant_id, user_id, provider, credential_id}.
  - Every Encrypt + Decrypt + GenerateDataKey call MUST pass this 4-key
    context (with the credential's actual UUID values)
  - KMS will refuse Decrypt if any of the 4 don't match what was bound at
    Encrypt time (per-row binding, not just per-tenant)
  - The IAM policy was tightened beyond the locked plan text to actually
    enforce this (ForAllValues:StringEquals + Null:false). See
    services/vault/policies/iam-identity-policy.json + kms-key-policy.json

LOCKED-41: The Granola adapter calls services/text_clean_service.py
  (extracted from routers/text.py core) DIRECTLY — NOT HTTP. This is a
  Phase 2d concern, NOT Phase 2b — but Phase 2b's vault accessor signature
  should accept tenant_id + user_id as explicit arguments (NOT pulled from
  any global / request context) so the Phase 2d adapter can pass them
  cleanly without HTTP indirection. The vault module is downstream of the
  caller; tenant_id flows IN via function arguments.

LOCKED-42: Postgres role split SIMPLIFIED for MVP. Single Postgres engine,
  single role, schema separation (the vault schema does exist; we did NOT
  defer that) + audited accessor + ALLOWLIST. The ALLOWLIST is the
  load-bearing app-layer guard — Phase 2b's user_credentials.py module
  enforces it. Do NOT add a CREATE ROLE inside Phase 2b. Second role +
  engine = Phase 2.1 hardening, not Phase 2b.

LOCKED-43: AES-GCM rotate path mints a FRESH DEK + FRESH NONCE on every
  write (insert AND update-in-place). Nonce reuse silently breaks
  AES-GCM authentication. The vault module's store_credential() and
  rotate_credential_key() functions MUST both call kms.generate_data_key()
  + os.urandom(12) on every invocation. Unit tests MUST assert that two
  consecutive writes to the same credential produce different encrypted_dek
  AND different nonce values.

LOCKED-44 (Phase 2d concern but worth flagging): the
  external_integration_runs.granola_note_snapshot column is now provisioned
  in the database (Phase 2a migration). Phase 2d's adapter will write to
  it at defer time. Phase 2b doesn't touch this.

The audit log (vault.credential_access_log) is NEW since the prior session
— added when the user asked "are we cutting-edge enough" and we surfaced
that no credential-access audit was a real shortcut. The table is now
provisioned. Phase 2b's vault module MUST write a row to it on every
accessor call (read + write + rotate + archive). The audit-writer is
inside the same Python transaction as the credential read/write so failure
to log = failure to access.

═══════════════════════════════════════════════════════════════════════
STEP 4 — EXECUTE PHASE 2B (after all reads + 2-paragraph confirmation)
═══════════════════════════════════════════════════════════════════════

Per the plan at tasks/granola-integration-plan.md §Phase 2b:

Phase 2b adds the Python vault module that uses the AWS infrastructure
already provisioned. Estimated 0.5 day.

New files to create:
- services/vault/__init__.py (public API exports)
- services/vault/encryption.py — KMS GenerateDataKey + AES-256-GCM encrypt/decrypt
- services/vault/user_credentials.py — typed accessor module with ALLOWLIST
- services/vault/audit.py (NEW since the plan was locked — added per the
  Phase 2a credential audit log work) — writes vault.credential_access_log
  row on every accessor call
- services/vault/errors.py — structured error code enum
- services/vault/db.py (optional helper for asyncpg connection acquisition)
- tests/unit/vault/test_encryption.py — round-trip + 4-field context tests
- tests/unit/vault/test_user_credentials.py — allowlist + accessor tests
- tests/unit/vault/test_audit.py — credential_access_log writer tests

Implementation order (each step has explicit exit criteria):

1. errors.py — define VaultErrorCode enum
   Per the plan + audit log additions:
   - VAULT_KMS_ENCRYPT_FAILED
   - VAULT_KMS_DECRYPT_FAILED
   - VAULT_KMS_CONTEXT_MISMATCH
   - VAULT_AES_GCM_TAG_MISMATCH
   - VAULT_DB_INSERT_FAILED
   - VAULT_DB_NOT_FOUND
   - VAULT_CALLER_NOT_ALLOWED
   - VAULT_AUDIT_LOG_WRITE_FAILED (new — propagates audit-write failures)

2. encryption.py — pure crypto module (no DB; no allowlist; no audit)
   - encrypt_credential(plaintext: str, encryption_context: dict[str, str])
     → returns (encrypted_api_key: bytes, encrypted_dek: bytes, nonce: bytes)
   - decrypt_credential(encrypted_api_key: bytes, encrypted_dek: bytes,
     nonce: bytes, encryption_context: dict[str, str]) → returns plaintext: str
   - Internally: boto3 KMS GenerateDataKey + AES-256-GCM via cryptography
     library (already a dep? verify in requirements.txt)
   - Tests: AsyncMock the boto3 KMS client; round-trip; assert fresh DEK +
     fresh nonce on each call (LOCKED-43)

3. audit.py — the credential_access_log writer
   - async def write_audit_row(
       *, conn: asyncpg.Connection,
       credential_id: UUID | None,
       tenant_id: UUID, user_id: UUID, provider: str,
       caller_module: str, operation: str,  # 'read'|'write'|'rotate'|'archive'
       success: bool,
       error_code: str | None = None,
       trace_id: str | None = None,
     )
   - INSERT INTO vault.credential_access_log (...) VALUES (...)
   - Append-only: this is the ONLY function that writes to that table;
     no UPDATE or DELETE methods exist in the codebase
   - Tests: asyncpg connection via real Neon test branch (or AsyncMock if
     unit only); verify INSERT shape; verify no DELETE/UPDATE methods exist
     in the module (grep test)

4. user_credentials.py — the high-level accessor
   - ALLOWLIST = {"services.granola_ingestion.adapter",
                 "services.granola_ingestion.scheduler",
                 "routers.granola"}
   - async def get_granola_credential_for_user(
       *, tenant_id: UUID, user_id: UUID, caller_module: str,
       conn: asyncpg.Connection, trace_id: str | None = None,
     ) → Optional[GranolaCredential]
     - VALIDATE caller_module IS IN ALLOWLIST (else raise VaultPermissionError)
     - SELECT FROM vault.user_credentials WHERE tenant_id=? AND user_id=?
       AND provider='granola' AND archived_at IS NULL
     - Call encryption.decrypt_credential() with the 4-field EncryptionContext
       (credential.id is one of the 4 fields)
     - Call audit.write_audit_row(...) — same transaction
     - Return the GranolaCredential dataclass (or None)
   - async def store_credential(
       *, tenant_id: UUID, user_id: UUID, provider: str, api_key: str,
       config: dict, caller_module: str, conn: asyncpg.Connection,
     ) → UUID (the new credential_id)
     - Validate caller_module
     - Generate new UUID for the credential row
     - Call encryption.encrypt_credential() with 4-field EncryptionContext
       (using the new UUID)
     - INSERT INTO vault.user_credentials (...)
     - Call audit.write_audit_row(operation='write', ...)
     - Return the UUID
   - async def rotate_credential_key(
       *, credential_id: UUID, new_api_key: str, caller_module: str,
       conn: asyncpg.Connection,
     ) → None
     - LOCKED-43: fresh DEK + fresh nonce; existing credential_id stays
       in EncryptionContext so decryptors don't break
     - UPDATE vault.user_credentials SET encrypted_api_key=?, encrypted_dek=?,
       nonce=? WHERE id=?
     - Call audit.write_audit_row(operation='rotate', ...)
   - Tests: AsyncMock-based; cover allowlist gate, 4-field context flow,
     fresh-DEK/nonce on rotate, audit-row written on every call

5. __init__.py — public API surface
   - Re-export GranolaCredential dataclass
   - Re-export get_granola_credential_for_user, store_credential,
     rotate_credential_key
   - Re-export VaultErrorCode, VaultPermissionError, VaultError

6. Smoke test (manual; after the unit tests pass):
   Run the smoke test from services/vault/README.md against the real
   production KMS + a placeholder credential row in the test tenant.
   Verify both happy path (4-field context → success) AND negative path
   (3-field context → AccessDenied).

7. Codex pre-merge review (4-round soft cap per
   feedback_codex_pre_merge_gate.md). Phase 2b's diff is small enough that
   1-2 rounds should be clean. Do NOT merge until 0 P1 findings.

Exit criteria:
- All Phase 2b unit tests pass locally
- Smoke test passes against real KMS + test tenant
- /codex review on the diff returns 0 P1 findings
- prisma-side migration has NOT been deployed yet (eq-frontend PR still
  awaiting your authorization to push); the smoke test runs only AFTER
  the Phase 2a Prisma deploys to test tenant
- services/vault/README.md updated to reflect the actual Python module
  shapes (replace the "PYTHON MODULE SHIPS IN PHASE 2B" notes with
  real signatures)

═══════════════════════════════════════════════════════════════════════
PROJECT CONTEXT (unchanged from prior session, surfaced for completeness)
═══════════════════════════════════════════════════════════════════════

What we're building:
- Granola.ai transcript ingestion adapter for EQ
- 3 design partners + Peter (design partner #0), all on Granola Business plan
- Each user generates a personal grn_… API key + creates an "EQ" folder in
  Granola
- EQ polls every 5 min and ingests new transcripts via the existing pipeline
- Total est: ~6-7 days across 3-5 sessions. We've done ~1 days of work
  across Phase 0/1/2a in 1 session; Phase 2b is ~0.5 day. Phase 2d
  (adapter logic) at 1.5 days is the largest remaining chunk and likely
  needs its own session.

Architecture (Phase 2b's place):
- Vault module is the cryptographic-security-critical layer
- Phase 2d's adapter calls vault.get_granola_credential_for_user() to get
  the API key, then calls Granola's API, then text_clean_service.process()
- Vault module is the ONLY place that talks to KMS
- Vault module is the ONLY place that reads/writes vault.user_credentials
- Vault module is the ONLY place that writes vault.credential_access_log

═══════════════════════════════════════════════════════════════════════
USER POSTURE (load-bearing — do NOT violate)
═══════════════════════════════════════════════════════════════════════

Non-developer founder. Make confident technical decisions; surface only
product or strategic decisions, or scope deviations.

Rules:
1. Complete Phase N before Phase N+1 planning. Phase 2a is COMPLETE in this
   commit set; Phase 2b is now the active work.
2. Cutting-edge-startup approach. No shortcuts unless the shortcut IS the
   correct architecture (verified by investigation). The prior session
   discovered that federated identity is blocked by Railway's lack of OIDC
   support, so long-lived keys + minimum-privilege + audit log + rotation
   IS the cutting-edge pattern on Railway. Don't pull federated identity
   forward into Phase 2b.
3. AI agent doesn't push or merge without per-action authorization. PRs can
   be created locally; git push and gh pr merge require user confirmation.
4. Plain-English explanations when user asks "why" or "what happened" — user
   is non-developer; technical accuracy paired with clear framing.
5. Investigate thoroughly; use the right gstack skills: /investigate,
   /codex consult, /codex review, /codex challenge, /review (before merge),
   /ship (after PR approval).
6. Don't go beyond locked scope. If you find a real issue mid-build, surface
   to user and treat as plan amendment, not silent expansion. (The prior
   session amended the plan twice — Phase 0 URL/param corrections, and
   the audit-log addition + IAM policy tightening — both with explicit
   user approval each time.)
7. Tenant isolation is non-negotiable — every query MUST include tenant_id
   in WHERE. Phase 2b's accessor takes tenant_id as an explicit argument
   per LOCKED-41's entity-sourced identity pattern.
8. *NEW from prior session:* ALWAYS git branch --show-current immediately
   before any git commit in shared checkouts. The prior session experienced
   a silent branch switch caused by another active agent in the same
   directory; recovery required a user-authorized git reset --hard.
9. *NEW from prior session:* When pausing or running /context-save in a
   shared checkout, courtesy-switch the checkout back to main so the next
   agent doesn't inherit your feature branch. After Phase 2b commits land
   on eq-frontend's phase-2/granola-vault-schema (if any Phase 2b work
   touches eq-frontend; most Phase 2b is in live-transcription-fastapi),
   run `git -C /Users/peteroneil/eq-frontend checkout main` before
   /context-save. Resume the feature branch with `git checkout
   phase-2/granola-vault-schema` next session. See
   feedback_branch_safety memory + lessons.md "Return to main when
   pausing in shared checkouts".

═══════════════════════════════════════════════════════════════════════
STOP CONDITIONS (hard — surface to user immediately)
═══════════════════════════════════════════════════════════════════════

- /context-restore returns NO_CHECKPOINTS or the wrong checkpoint title
- MEMORY.md Active Work doesn't read "PHASE_2A_COMPLETE_PHASE_2B_NEXT"
- tasks/granola-integration-plan.md doesn't reflect the Phase 0 amendments
  (you should see `created_after` not `since`, and `public-api.granola.ai`
  not `api.granola.ai`)
- The Phase 1 AWS infrastructure is missing (verify via
  `aws kms describe-key --key-id 59a0e2bc-...` returns success +
  `aws iam get-user --user-name eq-vault-service` returns success)
- Railway env vars are NOT set on live-transcription-fastapi production
  (verify via Railway dashboard; EQ_VAULT_AWS_ACCESS_KEY_ID + 3 others
  should exist)
- The eq-frontend Phase 2a PR has been merged ALREADY (Phase 2b's smoke
  test against real KMS works without the Prisma migration, but the
  end-to-end smoke test against the test tenant requires the migration to
  have deployed first — coordinate with the user)
- Production /api/health returns non-200 (Phase 1 regression)
- Test tenant has leftover artifacts from a prior session that haven't
  been cleaned
- Another agent is actively working in this repo within the last hour
  (per the prior session's shared-infrastructure-collision finding, this is
  HIGHLY relevant)

═══════════════════════════════════════════════════════════════════════
WHERE WE ARE — ONE-PARAGRAPH STATUS
═══════════════════════════════════════════════════════════════════════

Phase 1 (email pipeline cold-inbound capture) shipped 2026-05-18.
Phase 2 Granola integration brainstorm + plan locked 2026-05-22.
Phase 2 Granola integration Phase 0 (pre-flight verification), Phase 1
(AWS infrastructure), and Phase 2a (Prisma schema + migration) ALL
shipped in a single session on 2026-05-23 — 4 commits across 2 repos,
all local-only, none pushed. Key Phase 2a discoveries: (a) the
brainstorm doc had factual errors in Granola API base URL + filter
param (fixed in cbc5112 plan amendment); (b) federated identity was
discovered blocked by Railway's lack of OIDC federation support
(documented as Phase 2.1 hardening with platform constraint context);
(c) significant pre-existing Prisma schema drift in eq-frontend was
discovered (63 DROP TABLEs in auto-generated migration diff); the
Phase 2a migration was hand-written to bypass the drift cleanly, and
the drift itself is tracked at Linear EQ-11 for separate investigation.
KMS auto-rotation is enabled; CredentialAccessLog audit table is now
provisioned. Phase 2b (~0.5 day) is the immediate next step. After
Phase 2b ships, Phase 2c (Granola HTTP API client, ~0.5 day) is next,
likely in the same session if energy permits. Phase 2d (the adapter
with Path 2 logic, ~1.5 days) is the architecturally densest piece
and deserves its own dedicated session.

═══════════════════════════════════════════════════════════════════════
COMMITS SUMMARY (for git log familiarity)
═══════════════════════════════════════════════════════════════════════

live-transcription-fastapi  branch `phase-2/granola-integration`:
  0f86cba — Phase 2a discoveries: KMS auto-rotation enabled + audit log
            spec + EQ-11 reference
  8f3127f — Phase 1 AWS infrastructure provisioned + audit docs
            (services/vault/{README.md, policies/*.json} created)
  cbc5112 — Phase 0 plan amendments (Granola base URL + filter param
            empirical corrections)
  bd458ec — Phase 2 brainstorm + plan locked (predates this session)

eq-frontend  branch `phase-2/granola-vault-schema`:
  cf870b4 — Phase 2a Prisma migration: vault schema + 3 new tables
            (UserCredential, CredentialAccessLog, ExternalIntegrationRun)
  556b046 — Phase 1 comments-generator cleanup (pre-existing tech debt
            committed alongside Phase 2 work to unblock new migrations)

AWS infrastructure (us-east-1, account 211125681610):
  KMS CMK     59a0e2bc-c636-45e8-bccf-427ad2426ad8 (alias/eq-user-secrets)
  IAM user    eq-vault-service (Arn arn:aws:iam::211125681610:user/eq-vault-service)
  Access key  AKIATCKASHXFPCDN6NXX (secret in Railway env var only)
  Auto-rotation enabled; next rotation 2027-05-23

Railway env vars set on live-transcription-fastapi production:
  EQ_VAULT_AWS_ACCESS_KEY_ID
  EQ_VAULT_AWS_SECRET_ACCESS_KEY
  EQ_VAULT_KMS_KEY_ALIAS=alias/eq-user-secrets
  EQ_VAULT_AWS_REGION=us-east-1

Linear issue created:
  EQ-11 — Investigate Prisma schema drift in eq-frontend + design
          cutting-edge prevention approach (Backlog, Medium priority)

═══════════════════════════════════════════════════════════════════════

Start with /context-restore. Then the mandatory reads (steps 1-10). Then
write the 2-paragraph confirmation. Then begin Phase 2b implementation
in implementation-order (errors → encryption → audit → user_credentials
→ __init__ → smoke test → Codex review).

Phase 2b is a focused ~0.5-day chunk. If energy permits after 2b, Phase 2c
(Granola HTTP client) is a natural same-session continuation. Phase 2d
(adapter logic, 1.5 days) is the architecturally densest piece and almost
certainly deserves its own session.

No deploys without per-action authorization. PR creation is fine; git push
and gh pr merge require user confirmation each time.

Always verify the current branch via `git branch --show-current` IMMEDIATELY
before any git commit, in shared checkout directories. The prior session
experienced a silent branch switch that required a user-authorized recovery.
```
