# Deployment

Created by [Suhas Bhairav](https://suhasbhairav.com).

`justfastllm` is self-hosted by default. The Docker image is the common deployment artifact across Render, Railway, AWS, GCP, and any container platform.

## Common Requirements

- Docker image built from `Dockerfile`.
- Redis URL available as `REDIS_URL`.
- Provider API keys configured as environment variables.
- Health check path: `/health`.
- API contract: `/openapi.json`.

## Render

Use `render.yaml`:

```bash
render blueprint launch
```

The blueprint defines:

- Docker web service.
- Redis service.
- `/health` health check.
- Redis-backed cache and user memory.

## Railway

Use `railway.toml` with the Dockerfile builder:

```bash
railway up
```

Configure these variables in Railway:

```bash
REDIS_URL=...
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
DEEPSEEK_API_KEY=...
XAI_API_KEY=...
QWEN_API_KEY=...
KIMI_API_KEY=...
```

## AWS

Use `deploy/aws/AppRunner.yaml` after pushing an image to ECR:

```bash
aws cloudformation deploy \
  --template-file deploy/aws/AppRunner.yaml \
  --stack-name justfastllm \
  --parameter-overrides \
    ImageIdentifier=ACCOUNT_ID.dkr.ecr.REGION.amazonaws.com/justfastllm:latest \
    RedisUrl=redis://...
```

For production, use AWS Secrets Manager or SSM Parameter Store for provider keys and Redis credentials.

## GCP

Use `deploy/gcp/cloudrun-service.yaml` after pushing an image to Artifact Registry.

Replace:

- `REGION`
- `PROJECT_ID`
- `REPOSITORY`

Then deploy:

```bash
gcloud run services replace deploy/gcp/cloudrun-service.yaml --region REGION
```

Store `REDIS_URL` in Secret Manager as `justfastllm-redis-url`.

## Generic Docker Host

```bash
docker build -t justfastllm:latest .
docker run --rm \
  --env-file .env \
  -p 8000:8000 \
  justfastllm:latest
```

## Kubernetes

The GCP Cloud Run manifest is Knative-compatible. For Kubernetes, use the same container image and configure:

- `containerPort: 8000`
- readiness/liveness probe path `/health`
- environment variables from ConfigMaps/Secrets

