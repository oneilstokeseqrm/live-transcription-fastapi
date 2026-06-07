#!/usr/bin/env bash
#
# Mint the live-transcription prod IAM user + access key + inline policies (EQ-120 Phase B).
#
# WHY THE ADMIN PATH (not the keyless CI stack): the eq-prod-deploy role can manage ROLES
# but is denied iam:CreateUser / iam:CreateAccessKey (long-lived static creds get a human/
# admin gate) and kms:PutKeyPolicy on the foundation keys. Railway can't do OIDC, so this
# producer needs a static-key USER. This mirrors how EQ-54 minted eq-llm-gateway-vault-service.
#
# RUN AS: OrganizationAccountAccessRole in prod 009907129037. Assume it first and export the
# temp creds, e.g.:
#   eval "$(aws sts assume-role --role-arn arn:aws:iam::009907129037:role/OrganizationAccountAccessRole \
#            --role-session-name eq120-iam --duration-seconds 1800 \
#            --query 'Credentials.[printf("export AWS_ACCESS_KEY_ID=%s",AccessKeyId)...]' ...)"
# (the operator script that calls this handles the assume + no-echo export). The script
# asserts the caller account == prod before doing anything.
#
# NO SECRET IS EVER ECHOED. The access key is written only to a chmod-600 file under
# ~/.config/neon/. Idempotent: existing user/policies are updated in place; a second access
# key is NOT minted if one already exists (delete the old one first to rotate).
set -euo pipefail

ACCOUNT=009907129037
REGION=us-east-1
USER=eq-live-transcription-prod
BOUNDARY="arn:aws:iam::${ACCOUNT}:policy/eq-prod-service-boundary"
BUCKET=eq-live-transcription-uploads-prod
KMS_KEY_ARN="arn:aws:kms:${REGION}:${ACCOUNT}:key/d975f0e5-209c-424d-9165-f334e197149b"
KEYFILE="${HOME}/.config/neon/eq-prod-live-transcription-aws-key.env"

caller=$(aws sts get-caller-identity --query Account --output text)
if [ "$caller" != "$ACCOUNT" ]; then
  echo "Refusing: caller account $caller != prod $ACCOUNT. Assume OrganizationAccountAccessRole in prod first." >&2
  exit 1
fi

# 1. The IAM user — with the permissions boundary (caps it to the service ceiling; required
#    hygiene for any boundaried prod principal).
if aws iam get-user --user-name "$USER" >/dev/null 2>&1; then
  echo "user $USER already exists — re-asserting permissions boundary"
  # Fail-closed hardening (Codex P1): never leave an existing user un-boundaried or
  # on a different boundary. put-user-permissions-boundary is idempotent.
  aws iam put-user-permissions-boundary --user-name "$USER" --permissions-boundary "$BOUNDARY"
  echo "boundary re-asserted on $USER"
else
  aws iam create-user --user-name "$USER" --permissions-boundary "$BOUNDARY" \
    --tags Key=Environment,Value=prod Key=ManagedBy,Value=admin-cli \
           Key=Service,Value=live-transcription-fastapi >/dev/null
  echo "created IAM user $USER (boundary attached)"
fi

# 2. Inline policy — S3 presigned PUT/GET (HeadObject is covered by s3:GetObject) on
#    tenant-scoped keys only.
aws iam put-user-policy --user-name "$USER" --policy-name eq-live-transcription-prod-s3 \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"UploadObjects\",\"Effect\":\"Allow\",\"Action\":[\"s3:PutObject\",\"s3:GetObject\"],\"Resource\":\"arn:aws:s3:::${BUCKET}/tenant/*\"}]}"

# 3. Inline policy — EventBridge PutEvents on the account default bus.
aws iam put-user-policy --user-name "$USER" --policy-name eq-live-transcription-prod-events \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"PutEvents\",\"Effect\":\"Allow\",\"Action\":\"events:PutEvents\",\"Resource\":\"arn:aws:events:${REGION}:${ACCOUNT}:event-bus/default\"}]}"

# 4. Inline policy — KMS vault use on eq-user-secrets under the 4-field bound context
#    (mirrors services/vault/policies/iam-identity-policy.json, retargeted to the prod CMK).
aws iam put-user-policy --user-name "$USER" --policy-name eq-live-transcription-prod-kms \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"UseEqUserSecretsKeyWithBoundContext\",\"Effect\":\"Allow\",\"Action\":[\"kms:Encrypt\",\"kms:Decrypt\",\"kms:GenerateDataKey\"],\"Resource\":\"${KMS_KEY_ARN}\",\"Condition\":{\"ForAllValues:StringEquals\":{\"kms:EncryptionContextKeys\":[\"tenant_id\",\"user_id\",\"provider\",\"credential_id\"]},\"Null\":{\"kms:EncryptionContext:tenant_id\":\"false\",\"kms:EncryptionContext:user_id\":\"false\",\"kms:EncryptionContext:provider\":\"false\",\"kms:EncryptionContext:credential_id\":\"false\"}}},{\"Sid\":\"DescribeKeyMetadata\",\"Effect\":\"Allow\",\"Action\":\"kms:DescribeKey\",\"Resource\":\"${KMS_KEY_ARN}\"}]}"

echo "inline policies applied: eq-live-transcription-prod-{s3,events,kms}"

# 5. Access key — only if none exists. Written no-echo to a chmod-600 file (single local copy).
existing=$(aws iam list-access-keys --user-name "$USER" --query 'AccessKeyMetadata[].AccessKeyId' --output text)
if [ -n "$existing" ]; then
  echo "access key already exists ($existing) — NOT minting a second. Backup: $KEYFILE"
else
  mkdir -p "$(dirname "$KEYFILE")"
  umask 077
  ak_json=$(aws iam create-access-key --user-name "$USER" --output json)
  akid=$(printf '%s' "$ak_json" | python3 -c "import sys,json;print(json.load(sys.stdin)['AccessKey']['AccessKeyId'])")
  asec=$(printf '%s' "$ak_json" | python3 -c "import sys,json;print(json.load(sys.stdin)['AccessKey']['SecretAccessKey'])")
  {
    echo "# eq-live-transcription-prod IAM access key (EQ-120 Phase B). Single local copy; not recoverable. chmod 600."
    echo "# Used as AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY on the prod Railway service (S3 + EventBridge + KMS vault)."
    echo "AWS_ACCESS_KEY_ID=${akid}"
    echo "AWS_SECRET_ACCESS_KEY=${asec}"
  } > "$KEYFILE"
  chmod 600 "$KEYFILE"
  unset ak_json asec
  echo "minted access key ${akid} -> backed up no-echo to $KEYFILE"
fi

echo "DONE. User ARN: arn:aws:iam::${ACCOUNT}:user/${USER}"
