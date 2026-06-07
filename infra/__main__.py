"""
eq-live-transcription-prod — per-service prod AWS IaC for live-transcription-fastapi (EQ-120 Phase B).

Account 009907129037 ("eq-prod"), region us-east-1. Provisions ONLY this service's own
producer surface, deployed KEYLESSLY by GitHub Actions OIDC assuming the foundation's
`eq-prod-deploy` role. The org-wildcard trust `repo:oneilstokeseqrm/*:ref:refs/heads/main`
already covers this repo — no foundation trust change needed.

What lives here (everything the eq-prod-deploy role CAN manage — roles/policies/s3/events/sqs,
region-locked to us-east-1):
  1. S3 upload bucket `eq-live-transcription-uploads-prod` — presigned browser/desktop PUT,
     tenant-scoped keys (`tenant/{tenant_id}/uploads/...`), block-public, SSE-S3, versioning,
     TLS-only, 1-day expiry, CORS for the prod hub origin https://app.eqrm.io.
  2. The Granola self-poll EventBridge chain (DLQ + Connection + API destination + invoke
     role + scheduled rule + target) — the prod twin of dev's
     docs/infrastructure/granola-eventbridge-scheduler.md, promoted from that CLI runbook into
     declarative IaC. Built ONLY when the prod service URL is known (config
     `granola_cron_endpoint`): Phase B applies S3 ONLY; Phase D (after the Railway service
     exists) sets the URL + cron secret and re-applies to add the chain.

What does NOT live here — by foundation design, the eq-prod-deploy role is denied
`iam:CreateUser` / `iam:CreateAccessKey` (long-lived static creds need an admin gate) and
`kms:PutKeyPolicy` on the foundation keys. Railway can't do OIDC, so this producer needs a
static-key USER. Those are admin-path artifacts (assume OrganizationAccountAccessRole),
mirroring how EQ-54 built the gateway:
  - The scoped IAM USER `eq-live-transcription-prod` + access key + inline policies
    (S3 / events:PutEvents / KMS vault-use) → infra/admin/provision_prod_iam.sh
  - The `eq-user-secrets` CMK key-policy parity grant → eq-prod-foundation (mirrors gateway D3)

See EQ-CORE/tasks/environments/EQ-120-REPOINT-PLAN.md (Phase B) + infra/README.md.
"""

import json

import pulumi
import pulumi_aws as aws

EXPECTED_ACCOUNT = "009907129037"
REGION = "us-east-1"
UPLOAD_BUCKET = "eq-live-transcription-uploads-prod"
HUB_ORIGIN = "https://app.eqrm.io"
TAGS = {
    "Environment": "prod",
    "ManagedBy": "pulumi",
    "Project": "eq-live-transcription-prod",
    "Service": "live-transcription-fastapi",
}

config = pulumi.Config()
account_id = aws.get_caller_identity().account_id

# Account guard: refuse to deploy anywhere but the prod account (mirrors the foundation).
# get_caller_identity() is a synchronous invoke, so this runs before any resource is created.
if account_id != EXPECTED_ACCOUNT:
    raise Exception(
        f"Refusing to deploy eq-live-transcription-prod: caller account {account_id} "
        f"!= expected prod account {EXPECTED_ACCOUNT}. Check your AWS credentials."
    )


# ============================================================
# 1. S3 upload bucket — presigned PUT/GET/HEAD, tenant-scoped, hardened
#    Mirrors the foundation's CI/CD bucket hardening + adds CORS (browser PUT from the
#    hub) and a 1-day upload expiry (uploads are transient: PUT -> transcribe -> done).
# ============================================================
bucket = aws.s3.Bucket(UPLOAD_BUCKET, bucket=UPLOAD_BUCKET, tags={**TAGS, "Name": UPLOAD_BUCKET})

aws.s3.BucketPublicAccessBlock(
    f"{UPLOAD_BUCKET}-pab",
    bucket=bucket.id,
    block_public_acls=True,
    block_public_policy=True,
    ignore_public_acls=True,
    restrict_public_buckets=True,
)
aws.s3.BucketServerSideEncryptionConfiguration(
    f"{UPLOAD_BUCKET}-sse",
    bucket=bucket.id,
    rules=[{"apply_server_side_encryption_by_default": {"sse_algorithm": "AES256"}}],
)
aws.s3.BucketVersioning(
    f"{UPLOAD_BUCKET}-versioning",
    bucket=bucket.id,
    versioning_configuration={"status": "Enabled"},
)
aws.s3.BucketOwnershipControls(
    f"{UPLOAD_BUCKET}-ownership",
    bucket=bucket.id,
    rule={"object_ownership": "BucketOwnerEnforced"},
)
# Uploads are transient — expire them after 1 day. Versioning is ON (hardening
# parity with the foundation buckets), so a plain current-version expiry only adds a
# delete marker while the object BYTES survive as a noncurrent version indefinitely
# (Codex P1). Expire noncurrent versions too, abort stale multipart uploads, and
# sweep the leftover delete markers so the bucket is genuinely transient.
aws.s3.BucketLifecycleConfiguration(
    f"{UPLOAD_BUCKET}-lifecycle",
    bucket=bucket.id,
    rules=[
        {
            "id": "expire-uploads",
            "status": "Enabled",
            "filter": {"prefix": "tenant/"},
            "expiration": {"days": 1},
            "noncurrent_version_expiration": {"noncurrent_days": 1},
            "abort_incomplete_multipart_upload": {"days_after_initiation": 1},
        },
        {
            "id": "expire-delete-markers",
            "status": "Enabled",
            "filter": {"prefix": "tenant/"},
            "expiration": {"expired_object_delete_marker": True},
        },
    ],
)
# CORS: the browser does a raw HTTPS PUT to the presigned URL with a signed Content-Type.
# The desktop companion streams over the WebSocket and does NOT PUT to S3 — so the only
# CORS origin is the prod hub.
aws.s3.BucketCorsConfiguration(
    f"{UPLOAD_BUCKET}-cors",
    bucket=bucket.id,
    cors_rules=[
        {
            "allowed_methods": ["PUT", "GET", "HEAD"],
            "allowed_origins": [HUB_ORIGIN],
            "allowed_headers": ["*"],
            "expose_headers": ["ETag"],
            "max_age_seconds": 3000,
        }
    ],
)
aws.s3.BucketPolicy(
    f"{UPLOAD_BUCKET}-policy",
    bucket=bucket.id,
    policy=bucket.arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "DenyInsecureTransport",
                        "Effect": "Deny",
                        "Principal": "*",
                        "Action": "s3:*",
                        "Resource": [arn, f"{arn}/*"],
                        "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                    }
                ],
            }
        )
    ),
)

pulumi.export("upload_bucket_name", bucket.bucket)
pulumi.export("upload_bucket_arn", bucket.arn)


# ============================================================
# 2. Granola self-poll EventBridge chain (Phase D — gated on the prod service URL)
#    Prod twin of docs/infrastructure/granola-eventbridge-scheduler.md. A scheduled
#    EventBridge RULE -> API destination -> HTTPS POST /internal/granola/cron-tick, with
#    API_KEY connection auth (X-Internal-Cron-Secret), an invoke role, and a DLQ.
#    EventBridge Scheduler can't target HTTP, so this uses an `events` Rule (see the doc).
# ============================================================
granola_endpoint = config.get("granola_cron_endpoint")  # full URL incl. /internal/granola/cron-tick
if granola_endpoint:
    # Validate the endpoint before wiring the cron secret to it — a typo would point
    # prod EventBridge (carrying the shared cron secret) at the wrong URL/path (Codex P2).
    if not (
        granola_endpoint.startswith("https://")
        and granola_endpoint.endswith("/internal/granola/cron-tick")
    ):
        raise Exception(
            "granola_cron_endpoint must be https://<prod-host>/internal/granola/cron-tick, "
            f"got: {granola_endpoint!r}"
        )
    cron_secret = config.require_secret("cron_secret")
    # Boundary ARN from the foundation stack — required on every CI-created role.
    foundation = pulumi.StackReference("oneilstokeseqrm/eq-prod-foundation/prod")
    boundary_arn = foundation.get_output("service_permissions_boundary_arn")

    dlq = aws.sqs.Queue(
        "eq-granola-cron-dlq",
        name="eq-granola-cron-dlq",
        message_retention_seconds=1209600,  # 14 days
        tags={**TAGS, "Name": "eq-granola-cron-dlq"},
    )

    connection = aws.cloudwatch.EventConnection(
        "granola-cron-connection",
        name="granola-cron-connection",
        description=(
            "Holds INTERNAL_CRON_SECRET as the X-Internal-Cron-Secret header for the "
            "Granola 5-min poll trigger (prod)."
        ),
        authorization_type="API_KEY",
        auth_parameters={"api_key": {"key": "X-Internal-Cron-Secret", "value": cron_secret}},
    )

    api_dest = aws.cloudwatch.EventApiDestination(
        "granola-cron-tick",
        name="granola-cron-tick",
        description="POST /internal/granola/cron-tick on live-transcription-fastapi every 5 min (prod).",
        connection_arn=connection.arn,
        invocation_endpoint=granola_endpoint,
        http_method="POST",
        invocation_rate_limit_per_second=1,
    )

    # Rule is created BEFORE the invoke role so the role's trust can pin aws:SourceArn
    # to exactly this rule (confused-deputy hardening — only THIS rule may assume it).
    rule = aws.cloudwatch.EventRule(
        "granola-poll-5min",
        name="granola-poll-5min",
        description=(
            "Granola 5-min poll trigger: invokes the granola-cron-tick API destination "
            "(POST /internal/granola/cron-tick) (prod)."
        ),
        schedule_expression="rate(5 minutes)",
        state="ENABLED",
        tags={**TAGS, "Name": "granola-poll-5min"},
    )

    invoke_role = aws.iam.Role(
        "eq-granola-cron-invoke-role",
        name="eq-granola-cron-invoke-role",
        description=(
            "Assumed by EventBridge (events.amazonaws.com) to invoke the granola-cron-tick "
            "API destination (prod)."
        ),
        permissions_boundary=boundary_arn,
        assume_role_policy=rule.arn.apply(
            lambda rule_arn: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "events.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                            "Condition": {
                                "StringEquals": {
                                    "aws:SourceAccount": EXPECTED_ACCOUNT,
                                    "aws:SourceArn": rule_arn,
                                }
                            },
                        }
                    ],
                }
            )
        ),
        tags={**TAGS, "Name": "eq-granola-cron-invoke-role"},
    )
    aws.iam.RolePolicy(
        "granola-cron-invoke-policy",
        name="granola-cron-invoke-policy",
        role=invoke_role.id,
        policy=api_dest.arn.apply(
            lambda arn: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "InvokeApiDestination",
                            "Effect": "Allow",
                            "Action": "events:InvokeApiDestination",
                            "Resource": arn,
                        }
                    ],
                }
            )
        ),
    )

    # Let EventBridge send failed invocations from THIS rule (this account) to the DLQ.
    aws.sqs.QueuePolicy(
        "eq-granola-cron-dlq-policy",
        queue_url=dlq.id,
        policy=pulumi.Output.all(dlq.arn, rule.arn).apply(
            lambda a: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "AllowEventBridgeRuleDLQ",
                            "Effect": "Allow",
                            "Principal": {"Service": "events.amazonaws.com"},
                            "Action": "sqs:SendMessage",
                            "Resource": a[0],
                            "Condition": {
                                "ArnEquals": {"aws:SourceArn": a[1]},
                                "StringEquals": {"aws:SourceAccount": EXPECTED_ACCOUNT},
                            },
                        }
                    ],
                }
            )
        ),
    )

    aws.cloudwatch.EventTarget(
        "granola-cron-target",
        rule=rule.name,
        target_id="granola-cron-tick",
        arn=api_dest.arn,
        role_arn=invoke_role.arn,
        input="{}",
        retry_policy={"maximum_retry_attempts": 2, "maximum_event_age_in_seconds": 120},
        dead_letter_config={"arn": dlq.arn},
    )

    pulumi.export("granola_rule_arn", rule.arn)
    pulumi.export("granola_api_destination_arn", api_dest.arn)
    pulumi.export("granola_dlq_arn", dlq.arn)
    pulumi.export("granola_invoke_role_arn", invoke_role.arn)
else:
    pulumi.export(
        "granola_chain",
        "deferred to Phase D — set config granola_cron_endpoint + cron_secret, then `pulumi up`",
    )
