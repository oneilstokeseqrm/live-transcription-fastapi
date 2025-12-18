---
inclusion: always
---

# Deployment & Verification Standards

## Target Environment

All code merged to the `main` branch is automatically deployed to Railway via continuous deployment pipeline.

## Service Details

**Service Name:** `inspiring-upliftment`  
**Service ID:** `847cfa5a-b77c-4fb0-95e4-b20e8773c23e`

These identifiers are used in Railway MCP commands for deployment verification and monitoring.

## Deployment Verification Protocol

Post-deployment verification MUST be conducted using the Railway MCP (Model Context Protocol) integration. The standard verification workflow includes:

1. **Deployment Status Check**: Query Railway API to confirm deployment completion
2. **Log Analysis**: Review deployment logs for errors or warnings
3. **Health Check**: Verify the service is responding to health check endpoints
4. **Smoke Test**: Confirm WebSocket connections can be established

### Railway MCP Usage

Use the following Railway MCP tools for verification with the service ID `847cfa5a-b77c-4fb0-95e4-b20e8773c23e`:

- `mcp_railway_deployment_list`: Check recent deployments for the service
- `mcp_railway_deployment_status`: Verify deployment reached "SUCCESS" state
- `mcp_railway_deployment_logs`: Review logs for errors or startup issues
- `mcp_railway_service_info`: Confirm service configuration and health

### Verification Checklist

After each deployment to `main`:

- [ ] Deployment status shows "SUCCESS"
- [ ] No error logs in the last 100 lines
- [ ] Service is in "RUNNING" state
- [ ] Environment variables are correctly configured
- [ ] Redis connection is established (check logs for connection confirmation)

## Rollback Protocol

If verification fails:

1. Document the failure in deployment logs
2. Use Railway dashboard or MCP to trigger rollback to previous deployment
3. Create incident report with failure details
4. Fix issues in a new branch before re-attempting merge to `main`

## Environment Configuration

The following environment variables MUST be configured in Railway:

- `DEEPGRAM_API_KEY`: API key for Deepgram transcription service (REQUIRED)
- `REDIS_URL`: Connection string for Redis instance (REQUIRED)
- `OPENAI_API_KEY`: API key for OpenAI transcript cleaning (REQUIRED)
- `OPENAI_MODEL`: OpenAI model to use for cleaning (OPTIONAL, defaults to "gpt-4o")
- `MOCK_TENANT_ID`: (Optional) Tenant identifier for multi-tenant scenarios

Missing required environment variables will cause deployment to fail health checks.

### Validation

Add startup validation to `main.py`:

```python
import sys

REQUIRED_ENV_VARS = ["DEEPGRAM_API_KEY", "REDIS_URL", "OPENAI_API_KEY"]

def validate_environment():
    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        print(f"ERROR: Missing required environment variables: {missing}")
        sys.exit(1)

# Call before app initialization
validate_environment()
```

### .env.example

Ensure `.env.example` is up to date:

```bash
DEEPGRAM_API_KEY=your_deepgram_api_key_here
REDIS_URL=redis://localhost:6379
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4o
MOCK_TENANT_ID=default_org
```
