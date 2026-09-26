# Operations

Created by [Suhas Bhairav](https://suhasbhairav.com).

This guide is for running `justfastllm` as an enterprise gateway service.

For production architecture, access controls, SLO guidance, backup/restore, and incident runbooks, see [Enterprise Guide](ENTERPRISE.md).

## Runtime Profile

- Process model: ASGI app served by Uvicorn.
- Runtime dependency: `uvicorn`.
- Provider access: direct HTTP calls only; no provider SDKs.
- Cache: Redis by default, or database-backed for Postgres, MySQL, MSSQL, and MongoDB deployments.
- User memory: database or Redis-backed by default for production, with in-memory fallback for tests.
- Control-plane state: database or Redis-backed production snapshot, or an atomic JSON snapshot on operator-managed encrypted storage for single-node deployments.
- Local fallback for tests: in-memory cache and memory store.

## Deployment Checklist

1. Create `.env` from `.env.example`.
2. Configure provider API keys.
3. Configure a managed database URL for Postgres, MySQL, MSSQL, or MongoDB.
4. Set `JUSTFASTLLM_CACHE_BACKEND=database` with Postgres, MySQL, MSSQL, or MongoDB for durable cache storage, or `redis` for simpler shared cache deployments.
5. Set `JUSTFASTLLM_USER_MEMORY_BACKEND=database` with Postgres, MySQL, MSSQL, or MongoDB, or `redis` for simpler deployments.
6. Set `JUSTFASTLLM_MASTER_KEY` before exposing admin routes.
7. Set `JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND=database` with Postgres, MySQL, MSSQL, or MongoDB for durable production state; use `redis` for single-writer managed state, or `file` plus `JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH` for single-node persistence.
8. Set privacy/security contacts, subprocessor URL, and DPA URL.
9. Set `JUSTFASTLLM_REQUIRE_COMPLIANCE_READY=true` for production deployment health checks.
10. Configure CORS only when browser clients need it.
11. Run the standard test suite.
12. Run Ollama integration tests for major gateway changes.
13. Build and deploy the Docker image.

## Recommended Production Settings

```bash
JUSTFASTLLM_LOG_LEVEL=INFO
JUSTFASTLLM_CACHE_ENABLED=true
JUSTFASTLLM_CACHE_BACKEND=database
JUSTFASTLLM_CACHE_DATABASE_URL=postgresql+psycopg://user:pass@postgres:5432/justfastllm
JUSTFASTLLM_CACHE_DATABASE_TABLE=justfastllm_response_cache
JUSTFASTLLM_CACHE_TTL_SECONDS=60
JUSTFASTLLM_CACHE_MAX_ITEMS=1024
JUSTFASTLLM_USER_MEMORY_ENABLED=true
JUSTFASTLLM_USER_MEMORY_BACKEND=database
JUSTFASTLLM_USER_MEMORY_DATABASE_URL=postgresql+psycopg://user:pass@postgres:5432/justfastllm
JUSTFASTLLM_USER_MEMORY_DATABASE_TABLE=justfastllm_user_memory
JUSTFASTLLM_GUARDRAILS_ENABLED=true
JUSTFASTLLM_MASTER_KEY=change-me
JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND=database
JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL=postgresql+psycopg://user:pass@postgres:5432/justfastllm
JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE=justfastllm_control_plane_state
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

Use `JUSTFASTLLM_CONFIG_FILE=config.example.yaml` when structured configuration is easier to review than a long environment-variable list. Keep production secrets in your platform secret manager and let environment variables override config-file defaults.

## Observability

Every request log includes:

- `request_id`
- HTTP method
- path
- elapsed milliseconds
- sanitized headers

Every response includes `x-request-id`. Clients can send `X-Request-ID` to preserve trace IDs across systems.

## Health And Contracts

```bash
curl http://localhost:8000/health
curl http://localhost:8000/openapi.json
```

When `JUSTFASTLLM_REQUIRE_COMPLIANCE_READY=true`, `/health` returns `503` until required compliance-readiness settings are configured. The gate checks compliance mode, master key, retention windows, durable control-plane storage, a real control-plane evidence write with matching readback, write/read verified durable enabled user memory, write/read verified enabled response-cache storage, privacy/security contacts, subprocessor URL, DPA URL, CORS, and cache minimization. Inspect the missing checks:

```bash
curl http://localhost:8000/health
```

Use `/openapi.json` for client generation, API review, and gateway contract validation.

## Guardrail Operations

Inspect guardrails:

```bash
curl http://localhost:8000/v1/guardrails \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY"
```

Disable a specific check:

```bash
curl -X PATCH http://localhost:8000/v1/guardrails \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"disable":["block_patterns"]}'
```

Disable all guardrails temporarily:

```bash
curl -X PATCH http://localhost:8000/v1/guardrails \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"enabled":false}'
```

## Proxy Control Plane Operations

Create a virtual key:

```bash
curl -X POST http://localhost:8000/v1/keys \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"app","models":["gpt-5-nano"],"budget_usd":25,"rpm_limit":120}'
```

Inspect metrics:

```bash
curl http://localhost:8000/v1/metrics \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY"
```

Inspect provider health and alerts:

```bash
curl http://localhost:8000/v1/providers/health \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY"

curl http://localhost:8000/v1/alerts \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY"
```

Inspect policies and plugins:

```bash
curl http://localhost:8000/v1/policies \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY"

curl http://localhost:8000/v1/plugins \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY"
```

Reload gateway configuration:

```bash
curl -X POST http://localhost:8000/v1/config/reload \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY"
```

Back up the control-plane snapshot if file-backed mode is enabled:

```bash
cp .justfastllm/proxy-control.json .justfastllm/proxy-control.backup.json
```

Run the dashboard:

```bash
cd dashboard
npm install
npm run dev
```

Open `http://localhost:3000` and enter the gateway URL plus master key.

## Redis Operations

The gateway uses Redis for:

- Response caching.
- User preferences.
- User feedback.

Keep cache and memory keys namespaced:

```bash
JUSTFASTLLM_REDIS_KEY_PREFIX=justfastllm:cache:
JUSTFASTLLM_USER_MEMORY_KEY_PREFIX=justfastllm:memory:
```

## Docker Operations

Build:

```bash
docker build -t justfastllm:local .
```

Run with Postgres:

```bash
docker compose --env-file .env up --build
```

The Docker image runs as a non-root user and exposes a container healthcheck against `/health`.

## Self-Hosted Platform Targets

Deployment descriptors are included for:

- Render: `render.yaml`
- Railway: `railway.toml`
- AWS App Runner: `deploy/aws/AppRunner.yaml`
- GCP Cloud Run: `deploy/gcp/cloudrun-service.yaml`

See [Deployment](DEPLOYMENT.md).

## Verification Gates

Standard:

```bash
PYTHONPATH=src python3 -m unittest
PYTHONPATH=src python3 -m compileall -q src tests benchmarks
```

Ollama real-time verification:

```bash
JUSTFASTLLM_RUN_OLLAMA_TESTS=1 \
JUSTFASTLLM_OLLAMA_TEST_MODEL=qwen3:8b \
PYTHONPATH=src python3 -m unittest tests.test_ollama_integration
```

Performance smoke:

```bash
PYTHONPATH=src python3 benchmarks/benchmark_gateway.py --iterations 1000 --warmup 50
```
