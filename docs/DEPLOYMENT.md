# Deployment

Created by [Suhas Bhairav](https://suhasbhairav.com).

`justfastllm` is self-hosted by default. The Docker image is the common deployment artifact across Render, Railway, AWS, GCP, and any container platform.

## Common Requirements

- Docker image built from `Dockerfile`.
- Managed database URL available for control-plane state, user memory, and any enabled response cache.
- Provider API keys configured as environment variables.
- Health check path: `/health`.
- API contract: `/openapi.json`.

Production descriptors enable `JUSTFASTLLM_REQUIRE_COMPLIANCE_READY=true`. With that gate enabled, `/health` returns `503` until these variables are configured:

```bash
JUSTFASTLLM_MASTER_KEY=...
JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND=database
JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL=postgresql+psycopg://user:pass@db.example.com:5432/justfastllm?sslmode=require
JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE=justfastllm_control_plane_state
JUSTFASTLLM_USER_MEMORY_BACKEND=database
JUSTFASTLLM_USER_MEMORY_DATABASE_URL=postgresql+psycopg://user:pass@db.example.com:5432/justfastllm?sslmode=require
JUSTFASTLLM_USER_MEMORY_DATABASE_TABLE=justfastllm_user_memory
JUSTFASTLLM_COMPLIANCE_MODE=true
JUSTFASTLLM_REQUIRE_COMPLIANCE_READY=true
JUSTFASTLLM_CACHE_PERSONAL_DATA_ALLOWED=false
JUSTFASTLLM_USAGE_RETENTION_DAYS=90
JUSTFASTLLM_AUDIT_RETENTION_DAYS=365
JUSTFASTLLM_PRIVACY_CONTACT=privacy@example.com
JUSTFASTLLM_SECURITY_CONTACT=security@example.com
JUSTFASTLLM_SUBPROCESSORS_URL=https://example.com/subprocessors
JUSTFASTLLM_DPA_URL=https://example.com/dpa
```

This makes deployments fail closed until the gateway has the minimum runtime settings needed for privacy/security readiness, including write/read verified durable storage for control-plane evidence, enabled user memory, any enabled response cache, and encrypted transport indicators on remote database URLs. `JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL`, `JUSTFASTLLM_USER_MEMORY_DATABASE_URL`, and `JUSTFASTLLM_CACHE_DATABASE_URL` can target Postgres, MySQL, MSSQL, or MongoDB; Redis is also supported for managed deployments. Use `sslmode=require` or stronger for Postgres, `ssl_mode=REQUIRED` or stronger for MySQL/MariaDB, `encrypt=true` or stronger for MSSQL, and `tls=true` / `mongodb+srv` for MongoDB.

After deployment, export the machine-readable readiness report and evidence-integrity digest, then store both with release evidence:

```bash
justfastllm compliance-check \
  --url https://gateway.example.com \
  --master-key "$JUSTFASTLLM_MASTER_KEY"

curl -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  https://gateway.example.com/v1/compliance/report

curl -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  https://gateway.example.com/v1/compliance/integrity
```

The CLI check verifies deployed technical readiness evidence from `/health`, `/v1/compliance/report`, and `/v1/compliance/integrity`. It exits nonzero when the deployment is not ready or the master key is missing. It does not replace legal review, customer contracts, or SOC 2 attestation.

## Render

Use `render.yaml`:

```bash
render blueprint launch
```

The blueprint defines:

- Docker web service.
- Database-backed persistence through `JUSTFASTLLM_DATABASE_URL`.
- `/health` health check.
- Database-backed control-plane state, user memory, and response-cache settings.
- Generated `JUSTFASTLLM_MASTER_KEY`.
- Fail-closed compliance readiness gate.

Set `JUSTFASTLLM_DATABASE_URL`, privacy contact, security contact, subprocessor URL, and DPA URL in Render before production traffic.

## Railway

Use `railway.toml` with the Dockerfile builder:

```bash
railway up
```

Configure these variables in Railway:

```bash
JUSTFASTLLM_DATABASE_URL=...
JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND=database
JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL=env:JUSTFASTLLM_DATABASE_URL
JUSTFASTLLM_CACHE_BACKEND=database
JUSTFASTLLM_CACHE_DATABASE_URL=env:JUSTFASTLLM_DATABASE_URL
JUSTFASTLLM_USER_MEMORY_BACKEND=database
JUSTFASTLLM_USER_MEMORY_DATABASE_URL=env:JUSTFASTLLM_DATABASE_URL
JUSTFASTLLM_MASTER_KEY=...
JUSTFASTLLM_PRIVACY_CONTACT=...
JUSTFASTLLM_SECURITY_CONTACT=...
JUSTFASTLLM_SUBPROCESSORS_URL=...
JUSTFASTLLM_DPA_URL=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
DEEPSEEK_API_KEY=...
XAI_API_KEY=...
QWEN_API_KEY=...
KIMI_API_KEY=...
```

`railway.toml` declares the required database, master-key, privacy-contact, security-contact, subprocessor, and DPA variables with empty placeholders. Keep those placeholders empty in source control; set real values in Railway environment variables or secrets. With `JUSTFASTLLM_REQUIRE_COMPLIANCE_READY=true`, the service stays unhealthy until the values are present and the database-backed control plane, user memory, and response-cache stores pass write/read verification.

## AWS

Use `deploy/aws/AppRunner.yaml` after pushing an image to ECR:

The template includes an App Runner autoscaling configuration with `MaxSize: 1` so the built-in control-plane snapshot has one active writer.

```bash
aws cloudformation deploy \
  --template-file deploy/aws/AppRunner.yaml \
  --stack-name justfastllm \
  --parameter-overrides \
    ImageIdentifier=ACCOUNT_ID.dkr.ecr.REGION.amazonaws.com/justfastllm:latest \
    DatabaseUrl=postgresql+psycopg://... \
    MasterKey=... \
    PrivacyContact=privacy@example.com \
    SecurityContact=security@example.com \
    SubprocessorsUrl=https://example.com/subprocessors \
    DpaUrl=https://example.com/dpa
```

For production, use AWS Secrets Manager or SSM Parameter Store for provider keys and database credentials.

## GCP

Use `deploy/gcp/cloudrun-service.yaml` after pushing an image to Artifact Registry.

The included manifest sets Cloud Run `maxScale` to `1` so the built-in state snapshot has one active writer. Use the `justfastllm-database-url` secret for database-backed control-plane state, user memory, and response-cache settings before production traffic.

Replace:

- `REGION`
- `PROJECT_ID`
- `REPOSITORY`

Then deploy:

```bash
gcloud run services replace deploy/gcp/cloudrun-service.yaml --region REGION
```

Store required deployment values in Secret Manager:

- `justfastllm-database-url`
- `justfastllm-master-key`
- `justfastllm-privacy-contact`
- `justfastllm-security-contact`
- `justfastllm-subprocessors-url`
- `justfastllm-dpa-url`

## Generic Docker Host

```bash
docker build -t justfastllm:latest .
docker run --rm \
  --env-file .env \
  -p 8000:8000 \
  justfastllm:latest
```

For a production Docker host, set `JUSTFASTLLM_REQUIRE_COMPLIANCE_READY=true` and verify `/health` returns `200` before routing traffic.

## Kubernetes

The GCP Cloud Run manifest is Knative-compatible. For Kubernetes, use the same container image and configure:

- `containerPort: 8000`
- readiness/liveness probe path `/health`
- environment variables from ConfigMaps/Secrets
