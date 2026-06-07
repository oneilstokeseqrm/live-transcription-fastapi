# `infra/` — prod AWS IaC for live-transcription-fastapi (EQ-120)

Per-service prod AWS for the live-transcription service. Account **009907129037**
("eq-prod"), region **us-east-1**. Builds on `eq-prod-foundation` (the shared OIDC
deploy role + permissions boundary + KMS CMKs).

This service is the **first heavy AWS producer** in prod, so it also establishes the
prod EventBridge/queue isolation pattern every later async producer inherits:
**a producer publishes to the account `default` bus with a scoped IAM key; each
*consumer* owns its own rule + queue + Lambda** (created at that consumer's repoint, not
here).

## Two execution paths (and why)

The foundation's `eq-prod-deploy` CI role can manage **roles** (which can be OIDC-assumed,
no long-lived secret) but is deliberately denied `iam:CreateUser` / `iam:CreateAccessKey`
and `kms:PutKeyPolicy` on the foundation keys. Railway can't do OIDC, so this producer
needs a **static-key IAM user**. So Phase B is a hybrid (mirrors how EQ-54 built the gateway):

### 1. Keyless CI (this Pulumi stack) — `__main__.py`
Deployed by `.github/workflows/deploy-prod-infra.yml` (GitHub OIDC → `eq-prod-deploy`).
Stack `oneilstokeseqrm/eq-live-transcription-prod/prod`. Contains:
- **S3** `eq-live-transcription-uploads-prod` — presigned PUT/GET/HEAD, tenant-scoped keys
  (`tenant/{tenant_id}/uploads/...`), block-public, SSE-S3, versioning, TLS-only, 1-day
  expiry, CORS for `https://app.eqrm.io`.
- **Granola self-poll EventBridge chain** — DLQ + Connection + API destination + invoke
  role + scheduled rule + target. **Gated** on config `granola_cron_endpoint`: Phase B
  applies S3 only; the chain rides **Phase D** (after the prod Railway URL exists).

Repo Actions secrets the workflow needs (set out-of-band, no-echo):
`AWS_DEPLOY_ROLE_ARN` = `arn:aws:iam::009907129037:role/eq-prod-deploy`,
`PULUMI_ACCESS_TOKEN` = the `oneilstokeseqrm` Pulumi Cloud token.

### 2. Admin path (assume `OrganizationAccountAccessRole`) — `admin/provision_prod_iam.sh`
What CI can't do:
- IAM **user** `eq-live-transcription-prod` + permissions boundary `eq-prod-service-boundary`
  + 3 inline policies (S3 / `events:PutEvents` / KMS vault-use on `eq-user-secrets` under the
  4-field context `{tenant_id,user_id,provider,credential_id}`) + a static access key
  (→ `~/.config/neon/eq-prod-live-transcription-aws-key.env`, chmod 600, never echoed).
  The key becomes `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` on the prod Railway service;
  `EQ_VAULT_AWS_*` are left unset so the vault KMS client reuses this same identity.
- The **`eq-user-secrets` CMK key policy** parity grant — added in `eq-prod-foundation`
  (`user_secrets_key` `make_cmk(..., policy=...)`, mirrors the gateway D3), applied via the
  admin path because the deploy role is denied `kms:PutKeyPolicy` on the foundation keys.
  Defense-in-depth on top of the IAM identity policy (the default key policy already
  delegates to IAM, so encryption works on the IAM policy alone).

## Apply order (each prod-mutating step is founder-GO-gated — see EQ-120-REPOINT-PLAN.md)

```
# Phase B (S3 + IAM user/key + KMS policy):
1. admin/provision_prod_iam.sh           # via assumed OrganizationAccountAccessRole
2. eq-prod-foundation key-policy edit      # pulumi up via the admin path; commit
3. set the 2 repo Actions secrets; pulumi stack init; merge infra/ to main  # CI: pulumi up (S3 only)

# Phase D (after the prod Railway service exists, its URL known):
   pulumi config set eq-live-transcription-prod:granola_cron_endpoint https://<host>/internal/granola/cron-tick
   pulumi config set --secret eq-live-transcription-prod:cron_secret <INTERNAL_CRON_SECRET>
   # merge / re-run CI -> adds the Granola chain
```

## Rollback
`pulumi destroy` the per-service stack (the bucket is empty / 1-day-expiring); delete the
IAM user + key via the admin path; revert the foundation key-policy edit (admin path).
Nothing depends on these until the hub flip (Phase F).
