# Vault module — KMS envelope encryption for user credentials

Stores per-user third-party API keys (e.g., Granola `grn_…`) encrypted at rest using AWS KMS envelope encryption. Every credential row's plaintext is wrapped by a fresh per-row data encryption key (DEK), which is itself wrapped by a long-lived AWS KMS customer master key (CMK).

**Status:** Phase 1 (AWS infrastructure) PROVISIONED 2026-05-22. Phase 2b Python module SHIPPED 2026-05-23.

**Locked decisions this module implements:** LOCKED-40, LOCKED-42, LOCKED-43.

---

## Python module (Phase 2b, shipped 2026-05-23)

The module lives at `services/vault/`. Public API exported from `services.vault`:

```python
from services.vault import (
    GranolaCredential,                  # decrypted credential snapshot
    get_granola_credential_for_user,    # read + decrypt
    store_credential,                   # encrypt + insert
    rotate_credential_key,              # replace key material in place
    reactivate_credential,              # re-enable a previously archived row
    ALLOWLIST,                          # caller modules permitted to use the accessor
    VaultError, VaultErrorCode,         # structured failure types
    VaultPermissionError,               # caller_module not in ALLOWLIST
)
```

### Signatures

All accessors take `pool: asyncpg.Pool` rather than a single `Connection`. The pool is used to acquire dedicated connections for credential SQL AND for audit writes — separately (see "Audit log" below).

```python
async def get_granola_credential_for_user(
    *,
    tenant_id: UUID,
    user_id: UUID,
    caller_module: str,
    pool: asyncpg.Pool,
    trace_id: str | None = None,
) -> GranolaCredential | None: ...

async def store_credential(
    *,
    tenant_id: UUID,
    user_id: UUID,
    provider: str,
    api_key: str,
    config: dict[str, Any],
    caller_module: str,
    pool: asyncpg.Pool,
    trace_id: str | None = None,
) -> UUID: ...  # new credential row's UUID

async def rotate_credential_key(
    *,
    credential_id: UUID,
    new_api_key: str,
    caller_module: str,
    pool: asyncpg.Pool,
    trace_id: str | None = None,
) -> None: ...

async def reactivate_credential(
    *,
    tenant_id: UUID,
    user_id: UUID,
    provider: str,
    new_api_key: str,
    new_config: dict[str, Any],
    caller_module: str,
    pool: asyncpg.Pool,
    trace_id: str | None = None,
) -> UUID: ...  # archived row's preserved UUID
```

`caller_module` must be one of:

* `services.granola_ingestion.adapter`
* `services.granola_ingestion.scheduler`
* `routers.granola`

Anything else fails with `VaultPermissionError` (`error_code = vault_caller_not_allowed`). Adding a new caller requires editing `ALLOWLIST` in `services/vault/user_credentials.py` and a code review.

### Reconnect-after-disconnect

`store_credential` is INSERT-only and fails with `VAULT_DB_INSERT_FAILED` if a row already exists for `(tenant_id, user_id, provider)` (the schema's UNIQUE constraint covers archived rows too). Callers handling reconnect-after-disconnect should:

1. Try `store_credential` first.
2. If it raises `VAULT_DB_INSERT_FAILED`: call `reactivate_credential` to re-enable the archived row in place. The archived row's UUID is preserved (so any code holding a reference to the credential_id still works).
3. If the row is currently ACTIVE: `reactivate_credential` rejects with `VAULT_DB_INSERT_FAILED` and a "use rotate_credential_key" message. The caller should call `rotate_credential_key` instead.

### Audit log

Every accessor call writes one row to `vault.credential_access_log` via a **dedicated connection acquired from the pool** (NOT the caller's connection, and NOT the connection used for the credential SQL). This means audit rows are durable independent of any transaction the caller may have open elsewhere — the "failure to log = failure to access" guarantee holds unconditionally.

The audit module exposes no UPDATE or DELETE function (append-only invariant, app-layer enforced; a unit test greps the module source to catch regressions).

#### Atomicity of writes (store / rotate / reactivate)

The credential SQL and the success-audit run on the SAME connection inside the SAME transaction (single SQL transaction; strongest possible atomicity). This works without compromising audit durability because the Pool-based API structurally prevents callers from wrapping the vault in their own outer transaction: vault acquires its own connection from the pool, so the caller's connection state cannot influence vault's transaction.

Failure-audits run on a separate connection (acquired from the pool AFTER the credential transaction rolled back, so no nesting risk).

The earlier "audit on separate connection inside the cred_conn transaction" design (Codex R2) introduced a nested-pool-acquire deadlock that Codex R4 caught: holding `cred_conn` while acquiring a second conn from the same pool deadlocks at `pool.max_size=1` or under N concurrent writes on a pool of size N. The single-transaction design (Codex R4 fix) avoids that entirely.

#### Audit on read

`get_granola_credential_for_user` writes one audit row per call. Reads don't need atomicity because the credential is only "accessed" if the caller receives it; any failure path re-raises before the value crosses the API boundary.

#### Failure-audits

Failure paths write `success=false` audit rows on a dedicated connection too, so forensic data persists even when the primary operation was rolled back. A double-fault (the failure-audit also fails) is logged but not re-raised — the original `VaultError` is what the caller sees.

---

## Invariants (must hold across all reads/writes)

### LOCKED-40 — Four-field EncryptionContext binding

Every KMS `Encrypt` / `Decrypt` / `GenerateDataKey` call MUST pass an `EncryptionContext` containing exactly four keys, with all four required:

```python
EncryptionContext = {
    "tenant_id":     str(credential.tenant_id),
    "user_id":       str(credential.user_id),
    "provider":      "granola",                # or future provider
    "credential_id": str(credential.id),       # the vault row's UUID
}
```

Why all four:
- `tenant_id` alone is too coarse — every credential in a tenant could be cross-decrypted.
- Adding `user_id` partitions by user but still permits a tenant-internal user-A→user-B row swap.
- Adding `provider` is harmless (extensibility) but doesn't strengthen.
- Adding `credential_id` makes the binding **per-row** — KMS will refuse Decrypt if the caller substitutes another row's encrypted_dek under the same (tenant_id, user_id, provider).

The KMS key policy AND the IAM identity policy on `eq-vault-service` BOTH enforce this with:
- `ForAllValues:StringEquals` on `kms:EncryptionContextKeys` → no keys outside the 4 allowed.
- `Null: false` on each of the 4 specific keys → each MUST be present.

This is **tighter than the literal text of the plan-locked policy** (which used `StringEquals` without a set-operator prefix — technically incorrect per AWS multi-valued context key semantics, evaluated as a weak subset check). The tightening preserves the LOCKED-40 intent ("binds all FOUR fields"). Applied 2026-05-22 with user approval (see `tasks/granola-integration-plan.md` §LOCKED-40 + this README's audit log below).

### LOCKED-43 — Fresh DEK + fresh nonce on every write

Every credential write — insert AND in-place rotate — MUST:
1. Call `kms:GenerateDataKey` with the full 4-field `EncryptionContext` → returns a fresh 256-bit DEK + the encrypted DEK.
2. Generate a fresh 96-bit nonce: `os.urandom(12)`.
3. Encrypt the credential plaintext with AES-256-GCM using the fresh DEK + fresh nonce → returns ciphertext + 128-bit GCM tag.
4. Persist `{encrypted_api_key=ciphertext||tag, encrypted_dek, nonce}` to `vault.user_credentials`.

**Never reuse a DEK across rows. Never reuse a nonce within a DEK.** Nonce reuse breaks AES-GCM authentication completely (the attacker can forge ciphertexts and recover the authentication key). Since each write mints a fresh DEK *and* a fresh nonce, this is structurally prevented — but the unit tests must assert that consecutive writes to the same credential row produce different `encrypted_dek` AND different `nonce` bytes.

### LOCKED-42 — Single Postgres engine for MVP

The vault schema lives in the same Neon database as `public.*` business tables. Application-layer guard: the audited accessor module (`services/vault/user_credentials.py`, ships in Phase 2b) gates reads via a hardcoded `ALLOWLIST` of caller modules. Anything not in the allowlist raises `VaultPermissionError` before any SQL runs.

A second Postgres role + a second SQLAlchemy engine bound to a role-restricted `DATABASE_URL` would provide defense-in-depth above the app-layer guard — that's deferred to Phase 2.1 hardening.

---

## Infrastructure (Phase 1, provisioned 2026-05-22)

| Resource | Identifier | Region |
|---|---|---|
| KMS CMK | `59a0e2bc-c636-45e8-bccf-427ad2426ad8` | us-east-1 |
| KMS alias | `alias/eq-user-secrets` | us-east-1 |
| KMS CMK ARN | `arn:aws:kms:us-east-1:211125681610:key/59a0e2bc-c636-45e8-bccf-427ad2426ad8` | — |
| IAM user | `eq-vault-service` | global |
| IAM user ARN | `arn:aws:iam::211125681610:user/eq-vault-service` | — |
| IAM access key ID | `AKIATCKASHXFPCDN6NXX` | — |
| Inline policy on user | `eq-vault-service-kms-policy` | — |

The IAM access key SECRET is stored only in Railway env var `EQ_VAULT_AWS_SECRET_ACCESS_KEY`. It is NOT recoverable from AWS (only its hash is stored). To rotate, see "Rotation procedures" below.

### Railway environment variables (set on `live-transcription-fastapi` production)

```
EQ_VAULT_AWS_ACCESS_KEY_ID=AKIATCKASHXFPCDN6NXX
EQ_VAULT_AWS_SECRET_ACCESS_KEY=<set in Railway dashboard only — see rotation procedure>
EQ_VAULT_KMS_KEY_ALIAS=alias/eq-user-secrets
EQ_VAULT_AWS_REGION=us-east-1
```

### Policy JSON

The canonical policy JSON files are checked in alongside this README:

- `policies/kms-key-policy.json` — resource policy attached to the CMK
- `policies/iam-identity-policy.json` — inline identity policy attached to `eq-vault-service`

Both files exactly match what was applied via `aws kms create-key --policy` and `aws iam put-user-policy --policy-document` on 2026-05-22T19:57Z.

---

## Smoke test (runs after Phase 2b vault module ships)

From a Railway shell on `live-transcription-fastapi` (private network has access to AWS):

```python
import boto3, os
kms = boto3.client('kms', region_name=os.environ['EQ_VAULT_AWS_REGION'])
resp = kms.generate_data_key(
    KeyId=os.environ['EQ_VAULT_KMS_KEY_ALIAS'],
    KeySpec='AES_256',
    EncryptionContext={
        'tenant_id': '11111111-1111-4111-8111-111111111111',
        'user_id':   'b0000000-0000-4000-8000-000000000002',
        'provider':  'granola',
        'credential_id': '00000000-0000-4000-8000-000000000000',
    },
)
assert 'Plaintext' in resp
assert 'CiphertextBlob' in resp
print("GenerateDataKey OK; CiphertextBlob length:", len(resp['CiphertextBlob']))

# Negative test: missing required context key → should fail
try:
    kms.generate_data_key(
        KeyId=os.environ['EQ_VAULT_KMS_KEY_ALIAS'],
        KeySpec='AES_256',
        EncryptionContext={'tenant_id': '...', 'provider': 'granola'},  # missing user_id + credential_id
    )
    raise SystemExit("FAIL: expected AccessDenied, got success")
except kms.exceptions.ClientError as e:
    assert 'AccessDenied' in str(e), f"Expected AccessDenied, got: {e}"
    print("Negative test OK; AccessDenied raised as expected")
```

---

## Rotation procedures

### Rotating the AWS access key (eq-vault-service)

1. `aws iam create-access-key --user-name eq-vault-service` → returns a NEW AccessKeyId + SecretAccessKey.
2. Update Railway env vars `EQ_VAULT_AWS_ACCESS_KEY_ID` + `EQ_VAULT_AWS_SECRET_ACCESS_KEY` with the new pair.
3. Wait for Railway to redeploy.
4. Verify smoke test passes against the new key.
5. `aws iam delete-access-key --user-name eq-vault-service --access-key-id <OLD_ACCESS_KEY_ID>`.
6. Update this README's audit log + the Infrastructure table above with the new AccessKeyId.

Cadence recommendation: every 90 days, or immediately on suspected compromise. (AWS Best Practice: programmatic access keys rotated quarterly.)

### Rotating the KMS CMK

**Auto-rotation: ENABLED 2026-05-23.** AWS auto-generates new key material annually. Next rotation: 2027-05-23. Existing ciphertexts remain decryptable indefinitely (KMS tracks all historical key material). Application code is unaffected — keep using `alias/eq-user-secrets`.

To verify: `aws kms get-key-rotation-status --key-id 59a0e2bc-c636-45e8-bccf-427ad2426ad8` → `KeyRotationEnabled: true`.

Manual rotation (replacing the CMK entirely) requires re-encrypting every row of `vault.user_credentials` against the new CMK. Out of scope for V1.

### Compromise response

If the IAM access key is suspected compromised:

1. **Immediately disable the key:** `aws iam update-access-key --user-name eq-vault-service --access-key-id <COMPROMISED_KEY_ID> --status Inactive`.
2. Create + deploy a replacement (see "Rotating the AWS access key" above).
3. Delete the compromised key.
4. Audit CloudTrail for `kms:Decrypt` / `kms:GenerateDataKey` calls made with the compromised credentials. The 4-field EncryptionContext binding means an attacker who exfiltrated the credential row's ciphertext AND the compromised IAM access key STILL cannot decrypt without the EncryptionContext values (`tenant_id`, `user_id`, `provider`, `credential_id`) — those live only in the application-side row metadata, not in the KMS API response.

If the KMS CMK is suspected compromised: the threat model considered (insider abuse, accidental over-permissive policy, exposed access key) doesn't lead to CMK compromise — only AWS could compromise the underlying HSM, and that's outside our threat model.

---

## Phase 2.1 hardening (deferred — do not pull forward without explicit user approval)

1. **Second Postgres role + engine for vault** — currently `services/vault/user_credentials.py`'s allowlist is the only gate; a role-scoped `DATABASE_URL` would harden at the DB layer. **Paired with**: bringing the credential audit log into a separate role-restricted writer (the vault module would have write-only access to `vault.credential_access_log`, never UPDATE/DELETE, even though application code already follows that invariant at the function layer).

**Note on Phase 2a discovery (2026-05-23):** While generating the Phase 2a Prisma migration in eq-frontend, significant pre-existing schema drift was discovered between `prisma/schema.prisma` and the production Neon DB (63 `DROP TABLE`s in the auto-generated diff, plus enum/index/FK drift). The Granola migration was hand-written to bypass this drift cleanly. Investigation + cutting-edge prevention design is tracked separately at **Linear EQ-11** ([Investigate Prisma schema drift in eq-frontend + design cutting-edge prevention approach](https://linear.app/eq-core/issue/EQ-11/investigate-prisma-schema-drift-in-eq-frontend-design-cutting-edge)). This is repo-level Prisma hygiene, not a vault-specific concern.
2. **AES-GCM nonce-reuse detection monitoring** — random 96-bit nonces collide with negligible probability at our scale, but explicit detection costs nothing.
3. **Federated identity (eliminate long-lived AWS access keys)** — would replace `eq-vault-service` user + access keys with an IAM role assumed via OIDC federation from Railway. **Currently blocked**: Railway does not publicly support OIDC federation to AWS (verified 2026-05-23). Workarounds (IAM Roles Anywhere with X.509 certs, sidecar credentials broker, STS AssumeRole chain) all add significantly more complexity than the half-day estimate would suggest. **Status:** revisit when Railway adds OIDC support, OR if/when EQ evaluates platform migration. Until then, MVP hardening = minimum-privilege IAM policy (already applied) + 90-day key rotation cadence + the audit log this README adds in Phase 2a.
4. **Automated access-key rotation reminder** — periodic check that warns if `EQ_VAULT_AWS_ACCESS_KEY_ID` age exceeds 90 days. Trivial implementation; deferred only because not load-bearing for MVP scale (3 design partners) and rotation procedure is documented above.
5. **Cross-region replicated CMK** — currently us-east-1 only; if EQ goes multi-region, replicate the CMK to keep KMS calls in-region.
6. **CloudTrail-based anomaly detection** — alert on unusual `kms:Decrypt` patterns (unexpected source IP, off-hours, burst). Requires alerting infrastructure (Phase 2.1 also defers Slack/Resend wire-up for vault breakage events).
7. **Audit-credential reconciliation job** — daily check that flags phantom audit rows (audit says `success=true` for a credential_id that has no matching row in `vault.user_credentials`) and silent credentials (rows in `user_credentials` with no corresponding audit row). The Phase 2b architecture orders audit-before-credential-commit to make silent credentials structurally impossible, so the realistic mode is phantom audits from rare commit-after-audit races. The job sets the bound on how long a phantom can go undetected.

---

## Credential audit log (added 2026-05-23)

Every call into the vault accessor module writes a row to `vault.credential_access_log` BEFORE returning the decrypted secret. This is the forensic guarantee: post-incident, you can answer "what credential was read, when, by which caller, with what outcome."

**Append-only invariant (enforced at application layer, MVP):** the vault module's audit-writer is the ONLY path that touches `vault.credential_access_log`. The module exposes no UPDATE or DELETE method. Phase 2.1's second-engine + role split MAY enforce this at the Postgres role level (revoke UPDATE/DELETE on the table for the runtime role); for MVP the invariant is application-enforced + documented + tested.

**Row shape:**
- `id` (UUID, PK)
- `timestamp` (timestamptz, NOT NULL, default now())
- `credential_id` (UUID, FK to `vault.user_credentials.id` — nullable for compromised-credential audit gaps)
- `tenant_id`, `user_id`, `provider` (denormalized so audit row stands alone if credential row is deleted)
- `caller_module` (text, NOT NULL — `services.granola_ingestion.adapter`, `services.granola_ingestion.scheduler`, `routers.granola`, etc.)
- `operation` (text, NOT NULL — `read`, `write`, `rotate`, `archive`)
- `success` (boolean, NOT NULL)
- `error_code` (text, nullable — only set when success=false)
- `trace_id` (text, nullable — for tying audit rows to request/workflow IDs)

**Indexes:** `(tenant_id, timestamp DESC)` for tenant-scoped audit views; `(credential_id, timestamp DESC)` for per-credential history.

**Retention:** unlimited for MVP. Phase 2.1+: tier to cold storage after 90 days; full purge after 7 years per typical compliance bounds.

---

## Audit log

| Date | Actor | Action |
|---|---|---|
| 2026-05-22T19:56:18Z | peter-admin-cli | Created IAM user `eq-vault-service` with audit tags |
| 2026-05-22T19:56:59Z | peter-admin-cli | Created KMS CMK `59a0e2bc-c636-45e8-bccf-427ad2426ad8` with tightened LOCKED-40 EncryptionContext binding |
| 2026-05-22T19:57:16Z | peter-admin-cli | Created alias `alias/eq-user-secrets` |
| 2026-05-22T19:57:16Z | peter-admin-cli | Attached inline policy `eq-vault-service-kms-policy` |
| 2026-05-22T19:57:33Z | peter-admin-cli | Created access key `AKIATCKASHXFPCDN6NXX` |
| 2026-05-22 (post-MCP) | peteroneil | Added 4 env vars to Railway production environment |
| 2026-05-23T09:41:00Z | peter-admin-cli | Enabled KMS auto-rotation on CMK `59a0e2bc-...` (annual, next 2027-05-23) |
| 2026-05-23 (Phase 2b) | peteroneil + Claude | Shipped Python vault module: `services/vault/{errors,encryption,audit,user_credentials,__init__}.py` + 46 AsyncMock-based unit tests (`tests/unit/vault/`). Pinned `cryptography>=44.0.0` in requirements.txt. |
| 2026-05-23 (Phase 2b Codex R1) | peteroneil + Claude | Folded Codex round 1 findings: atomic write+audit via `conn.transaction()`; narrowed `AccessDeniedException` mapping so it surfaces as `VAULT_KMS_ENCRYPT_FAILED`/`VAULT_KMS_DECRYPT_FAILED` rather than the misleading `VAULT_KMS_CONTEXT_MISMATCH`. 53 tests pass. |
| 2026-05-23 (Phase 2b Codex R2) | peteroneil + Claude | Folded Codex round 2 findings: audit module refactored to take `asyncpg.Pool` and acquire its own connection per write (unconditional durability vs caller transaction state); audit-before-credential-commit ordering preserves atomicity; added `reactivate_credential` for reconnect-after-disconnect; `rotate_credential_key` UPDATE now resets `status='active'`. Phantom-audit reconciliation job added to Phase 2.1 hardening list. 56 tests pass. |
| 2026-05-23 (Phase 2b Codex R3) | peteroneil + Claude | Folded Codex round 3 findings: `rotate_credential_key` now requires `tenant_id` + `user_id` and filters both at lookup AND UPDATE (tenant-isolation rule enforced); raw asyncpg exceptions converted to structured `VaultError(VAULT_DB_QUERY_FAILED)` at every DB boundary so the API never leaks raw connection errors; `get_granola_credential_for_user` SQL filters `status='active'` so revoked/error credentials are not returned. New `VAULT_DB_QUERY_FAILED` error code. 63 tests pass. |
| 2026-05-23 (Phase 2b Codex R4) | peteroneil + Claude | Folded Codex round 4 P1: write accessors no longer nest pool acquires (held cred_conn while audit acquired second conn → deadlock at pool max_size=1 or N concurrent writes on pool size N). New `audit.write_audit_row_on_conn(conn=...)` variant used inside the cred_conn transaction so audit + credential commit atomically as a single SQL transaction. Pool variant kept for failure-audits + reads (paths where no nesting risk exists). New `test_no_nested_pool_acquire_during_write` locks the invariant. 64 tests pass. |
| 2026-05-23 (Phase 2b Codex R5) | peteroneil + Claude | Folded Codex round 5: R4 deadlock fix was incomplete (only applied to `store_credential`; `rotate_credential_key` and `reactivate_credential` still nested pool acquires). Propagated `write_audit_row_on_conn` to both. New invariant tests `test_no_nested_pool_acquire_during_rotate` + `test_no_nested_pool_acquire_during_reactivate` lock the contract. Also fixed R5 P2: `reactivate_credential` UPDATE now clears `last_polled_at` so reconnecting with a new `folder_id` doesn't silently skip notes older than the prior cursor. 67 tests pass. |
| 2026-05-23 (Phase 2b Codex R6) | peteroneil + Claude | Folded Codex round 6: encryption.py now catches `BotoCoreError` in addition to `ClientError` so non-AWS-service exceptions (missing creds, network drops, timeouts) also map to structured `VaultError` instead of leaking raw botocore exceptions. Also fixed R6 P2: rotate's failure-audits pass `credential_id=None` for the ALLOWLIST + DB-error + not-found paths (where the credential's existence has not been verified), avoiding audit-table FK violations that would silently swallow the forensic record as a double-fault. 70 tests pass. |

---

## References

- `tasks/granola-integration-plan.md` §Phase 1 — the locked Phase 1 spec
- `policies/kms-key-policy.json` — canonical KMS key policy
- `policies/iam-identity-policy.json` — canonical IAM identity policy
- AWS docs: [Using EncryptionContext](https://docs.aws.amazon.com/kms/latest/developerguide/concepts.html#encrypt_context), [IAM multi-valued context keys](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_condition-single-vs-multi-valued-context-keys.html)
