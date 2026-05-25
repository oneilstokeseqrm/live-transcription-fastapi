# Granola 5-minute poll trigger — AWS EventBridge infrastructure

**What this is:** the AWS-side timer that fires the Granola ingestion cycle every
5 minutes. It POSTs `POST /internal/granola/cron-tick` on
`live-transcription-fastapi` with the `X-Internal-Cron-Secret` header; the handler
(`routers/granola_cron.py`) lists active credentials and dispatches one DBOS
workflow per credential. Until a user connects Granola, the endpoint returns
`202 {"enqueued": 0, ...}` — the trigger runs dormant but proves the path.

**Provisioned:** 2026-05-25 (account `211125681610`, region `us-east-1`, by
principal `arn:aws:iam::211125681610:user/peter-admin-cli`).

> **⚠️ Filename note — "scheduler" is historical.** This file keeps the name the
> session handoff referenced (`granola-eventbridge-scheduler.md`), but the actual
> AWS primitive is a **scheduled EventBridge _Rule_**, NOT _EventBridge
> Scheduler_. See "Why a Rule, not EventBridge Scheduler" below. The repo has no
> IaC framework today; this doc IS the source of truth + the bridge to future IaC.

---

## Why a Rule, not EventBridge Scheduler

The original plan named **EventBridge Scheduler** (the newer `aws scheduler`
service). During provisioning we confirmed against the live AWS API that
**EventBridge Scheduler cannot target an HTTP endpoint** — its target types are
AWS-service actions only (Lambda, ECS, SQS, Step Functions, Kinesis, SageMaker,
EventBridge `PutEvents`). There is no API-destination / HTTP target in Scheduler.

Only **EventBridge Rules** (`aws events`) can invoke an **API destination** (an
HTTPS endpoint with connection-based auth). So the canonical AWS pattern for
"POST an HTTPS endpoint on a timer with a secret header" is:

```
scheduled EventBridge Rule  →  API destination  →  HTTPS POST (+ Connection auth)
```

This is the same `events` service the email pipeline already uses (it has ~16
rules), so the trigger is consistent with existing infra rather than a net-new
service. `aws scheduler create-schedule` was attempted once and rejected the
API-destination ARN with `ValidationException` — that call created nothing.

---

## Live resources (the chain)

| # | Resource | Name / ARN |
|---|----------|------------|
| 1 | **Scheduled Rule** | `granola-poll-5min` — `arn:aws:events:us-east-1:211125681610:rule/granola-poll-5min` (`rate(5 minutes)`, `ENABLED`, default bus) |
| 2 | **API destination** | `granola-cron-tick` — `arn:aws:events:us-east-1:211125681610:api-destination/granola-cron-tick/d4052140-2057-492f-b8fd-ea037d37ed3b` (POST) |
| 3 | **Connection** | `granola-cron-connection` — `arn:aws:events:us-east-1:211125681610:connection/granola-cron-connection/f8c20e1a-4930-4bb3-aeec-27991aa9126c` (API_KEY auth; header `X-Internal-Cron-Secret`) |
| 4 | **Invoke role** | `eq-granola-cron-invoke-role` — `arn:aws:iam::211125681610:role/eq-granola-cron-invoke-role` (trust `events.amazonaws.com`; inline policy `granola-cron-invoke-policy` → `events:InvokeApiDestination`) |
| 5 | **DLQ** | `eq-granola-cron-dlq` — `arn:aws:sqs:us-east-1:211125681610:eq-granola-cron-dlq` (14-day retention; resource policy lets `events.amazonaws.com` send from the rule ARN) |

**Target wiring:** rule `granola-poll-5min` → target id `granola-cron-tick` →
API-destination ARN, `RoleArn` = the invoke role, `Input` = `{}` (the endpoint
reads no body), `RetryPolicy` = 2 attempts / 120s, `DeadLetterConfig` = the DLQ.

**Endpoint:** `https://live-transcription-fastapi-production.up.railway.app/internal/granola/cron-tick`

**The secret:** the `X-Internal-Cron-Secret` value is `INTERNAL_CRON_SECRET` from
Railway (`live-transcription-fastapi` production env). It is stored ONLY in (a)
Railway env and (b) the EventBridge Connection (which keeps it in an
AWS-managed Secrets Manager secret). **This doc never contains the value.** To
rotate, see "Modify" below — you must update BOTH places.

**Cost:** free at our volume (~8,700 invocations/month vs the 14M EventBridge
free tier). The only recurring charge is the Secrets Manager secret the
Connection auto-creates (~$0.40/month). SQS DLQ cost is negligible.

---

## Create (full sequence, in dependency order)

Run as an admin principal in `us-east-1`. Capture each ARN for the next step.

```bash
ACCOUNT=211125681610
REGION=us-east-1
ENDPOINT=https://live-transcription-fastapi-production.up.railway.app/internal/granola/cron-tick
SECRET='<value of INTERNAL_CRON_SECRET from Railway>'   # do NOT commit this

# 1. DLQ for failed invocations (14-day retention)
aws sqs create-queue --queue-name eq-granola-cron-dlq \
  --attributes MessageRetentionPeriod=1209600 \
  --tags project=granola-integration,purpose=eventbridge-scheduler-dlq \
  --region "$REGION"
DLQ_ARN="arn:aws:sqs:$REGION:$ACCOUNT:eq-granola-cron-dlq"

# 2. Connection holding the secret as the X-Internal-Cron-Secret header
aws events create-connection --name granola-cron-connection \
  --description "Holds INTERNAL_CRON_SECRET as the X-Internal-Cron-Secret header for the Granola 5-min poll trigger" \
  --authorization-type API_KEY \
  --auth-parameters "{\"ApiKeyAuthParameters\":{\"ApiKeyName\":\"X-Internal-Cron-Secret\",\"ApiKeyValue\":\"$SECRET\"}}" \
  --region "$REGION"
# -> capture ConnectionArn

# 3. API destination → the cron-tick URL (bind to the Connection from step 2)
aws events create-api-destination --name granola-cron-tick \
  --description "POST /internal/granola/cron-tick on live-transcription-fastapi every 5 min" \
  --connection-arn "<ConnectionArn from step 2>" \
  --invocation-endpoint "$ENDPOINT" \
  --http-method POST --invocation-rate-limit-per-second 1 \
  --region "$REGION"
# -> capture ApiDestinationArn
APIDEST_ARN="<ApiDestinationArn from step 3>"

# 4. IAM role EventBridge assumes to invoke the API destination
aws iam create-role --role-name eq-granola-cron-invoke-role \
  --description "Assumed by EventBridge (events.amazonaws.com) to invoke the granola-cron-tick API destination" \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"events.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"211125681610"}}}]}' \
  --tags Key=project,Value=granola-integration --region "$REGION"
ROLE_ARN="arn:aws:iam::$ACCOUNT:role/eq-granola-cron-invoke-role"

aws iam put-role-policy --role-name eq-granola-cron-invoke-role \
  --policy-name granola-cron-invoke-policy \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"InvokeApiDestination\",\"Effect\":\"Allow\",\"Action\":\"events:InvokeApiDestination\",\"Resource\":\"$APIDEST_ARN\"}]}" \
  --region "$REGION"

# 5. The scheduled rule
aws events put-rule --name granola-poll-5min \
  --schedule-expression "rate(5 minutes)" --state ENABLED \
  --description "Granola 5-min poll trigger: invokes the granola-cron-tick API destination (POST /internal/granola/cron-tick)" \
  --tags Key=project,Value=granola-integration --region "$REGION"
RULE_ARN="arn:aws:events:$REGION:$ACCOUNT:rule/granola-poll-5min"

# 6. DLQ resource policy — let EventBridge send to the DLQ from THIS rule
aws sqs set-queue-attributes \
  --queue-url "https://sqs.$REGION.amazonaws.com/$ACCOUNT/eq-granola-cron-dlq" \
  --attributes "{\"Policy\":\"{\\\"Version\\\":\\\"2012-10-17\\\",\\\"Statement\\\":[{\\\"Sid\\\":\\\"AllowEventBridgeRuleDLQ\\\",\\\"Effect\\\":\\\"Allow\\\",\\\"Principal\\\":{\\\"Service\\\":\\\"events.amazonaws.com\\\"},\\\"Action\\\":\\\"sqs:SendMessage\\\",\\\"Resource\\\":\\\"$DLQ_ARN\\\",\\\"Condition\\\":{\\\"ArnEquals\\\":{\\\"aws:SourceArn\\\":\\\"$RULE_ARN\\\"}}}]}\"}" \
  --region "$REGION"

# 7. Wire the rule → API destination target (role + input + retry + DLQ)
aws events put-targets --rule granola-poll-5min --region "$REGION" \
  --targets "[{\"Id\":\"granola-cron-tick\",\"Arn\":\"$APIDEST_ARN\",\"RoleArn\":\"$ROLE_ARN\",\"Input\":\"{}\",\"RetryPolicy\":{\"MaximumRetryAttempts\":2,\"MaximumEventAgeInSeconds\":120},\"DeadLetterConfig\":{\"Arn\":\"$DLQ_ARN\"}}]"
# -> expect FailedEntryCount: 0
```

---

## Verify

```bash
# Rule is enabled + scheduled
aws events describe-rule --name granola-poll-5min --region us-east-1

# Target points at the API destination with role + DLQ + retry
aws events list-targets-by-rule --rule granola-poll-5min --region us-east-1

# A real tick (within ~5 min) shows in Railway runtime logs:
#   "granola cron tick: enqueued=0 cycle_window=<int> active_credentials=0"
# (via the Railway MCP deployment_logs on the active deployment)

# DLQ should be empty in normal operation (depth > 0 == failing invocations)
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/211125681610/eq-granola-cron-dlq \
  --attribute-names ApproximateNumberOfMessages --region us-east-1
```

---

## Modify

**Pause / resume the trigger** (no teardown needed):

```bash
aws events disable-rule --name granola-poll-5min --region us-east-1   # pause
aws events enable-rule  --name granola-poll-5min --region us-east-1   # resume
```

**Change the cadence** (e.g. to 10 min): re-run `put-rule` with the same name and
a new `--schedule-expression` (e.g. `rate(10 minutes)` or a `cron(...)`).
`put-rule` is an upsert; the target wiring is preserved.

**Rotate the secret** (must update BOTH Railway and the Connection, in this order
to avoid a window where ticks 401):

```bash
# 1. New secret in Railway (triggers a redeploy; verify /health 200 after)
#    via Railway MCP variable_set INTERNAL_CRON_SECRET=<new>
# 2. Update the Connection's auth header to match
aws events update-connection --name granola-cron-connection \
  --authorization-type API_KEY \
  --auth-parameters '{"ApiKeyAuthParameters":{"ApiKeyName":"X-Internal-Cron-Secret","ApiKeyValue":"<new>"}}' \
  --region us-east-1
```

---

## Teardown (full, reverse order)

```bash
aws events remove-targets --rule granola-poll-5min --ids granola-cron-tick --region us-east-1
aws events delete-rule --name granola-poll-5min --region us-east-1
aws iam delete-role-policy --role-name eq-granola-cron-invoke-role --policy-name granola-cron-invoke-policy --region us-east-1
aws iam delete-role --role-name eq-granola-cron-invoke-role --region us-east-1
aws events delete-api-destination --name granola-cron-tick --region us-east-1
aws events delete-connection --name granola-cron-connection --region us-east-1   # also removes its Secrets Manager secret
aws sqs delete-queue --queue-url https://sqs.us-east-1.amazonaws.com/211125681610/eq-granola-cron-dlq --region us-east-1
```

Deleting the rule stops the timer instantly — the safest first move if anything
looks wrong.

---

## Related

- `routers/granola_cron.py` — the endpoint this trigger POSTs (503 if the secret
  is unset, 401 on wrong/missing secret, 202 + `{"enqueued":N,...}` on success).
- `services/granola_ingestion/scheduler.py` — `list_active_credentials` +
  the per-credential DBOS workflow the tick dispatches.
- `tasks/granola-integration-plan.md` — LOCKED-28 (5-min cadence) + LOCKED-39
  (external cron + DBOS `SetWorkflowID`, NOT `@DBOS.scheduled`).
