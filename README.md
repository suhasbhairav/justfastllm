# justfastllm

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Runtime](https://img.shields.io/badge/runtime-ASGI-purple)
![SDKs](https://img.shields.io/badge/provider%20SDKs-none-black)
![Cache](https://img.shields.io/badge/cache-Redis-red)
![Docker](https://img.shields.io/badge/docker-supported-2496ED)
![Ollama](https://img.shields.io/badge/Ollama-verified-111111)
![Render](https://img.shields.io/badge/deploy-Render-46E3B7)
![Railway](https://img.shields.io/badge/deploy-Railway-7B61FF)
![AWS](https://img.shields.io/badge/deploy-AWS%20App%20Runner-FF9900)
![GCP](https://img.shields.io/badge/deploy-GCP%20Cloud%20Run-4285F4)
![LLM Gateway](https://img.shields.io/badge/LLM-Gateway-111827)
![Provider Proxy](https://img.shields.io/badge/Provider-Proxy-2563EB)
![OpenAI Compatible](https://img.shields.io/badge/OpenAI-Compatible-10A37F)
![Anthropic Messages](https://img.shields.io/badge/Anthropic-Messages-D97706)
![DeepSeek](https://img.shields.io/badge/DeepSeek-Provider-4F46E5)
![Grok](https://img.shields.io/badge/Grok-xAI-000000)
![Qwen](https://img.shields.io/badge/Qwen-Provider-1D4ED8)
![Kimi](https://img.shields.io/badge/Kimi-Moonshot-7C3AED)
![Ollama](https://img.shields.io/badge/Ollama-Local-111111)
![Redis Cache](https://img.shields.io/badge/Redis-Cache-DC2626)
![User Memory](https://img.shields.io/badge/User-Memory-0F766E)
![Guardrails](https://img.shields.io/badge/Guardrails-Configurable-B45309)
![Agents](https://img.shields.io/badge/Agents-Ready-4338CA)
![Skills](https://img.shields.io/badge/Skills-Ready-0891B2)
![OpenAPI](https://img.shields.io/badge/OpenAPI-Contract-16A34A)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED)
![Self Hosted](https://img.shields.io/badge/Self--Hosted-Ready-111827)
![Docker](https://img.shields.io/badge/Docker-Image-2496ED)
![Docker Compose](https://img.shields.io/badge/Docker%20Compose-Stack-2496ED)
![Render Blueprint](https://img.shields.io/badge/Render-Blueprint-46E3B7)
![Railway Dockerfile](https://img.shields.io/badge/Railway-Dockerfile-7B61FF)
![AWS App Runner](https://img.shields.io/badge/AWS-App%20Runner-FF9900)
![GCP Cloud Run](https://img.shields.io/badge/GCP-Cloud%20Run-4285F4)
![Redis Ready](https://img.shields.io/badge/Redis-Ready-DC2626)

**The ultra-light Python LLM gateway for teams that want one fast, self-hosted control plane for every model.**

`justfastllm` gives you a centralized API gateway for routing requests to OpenAI, Anthropic, DeepSeek, Grok, Qwen, Kimi, Ollama, and any OpenAI-compatible provider without provider SDKs in the runtime path. It is built for teams that want speed, operational control, clean abstractions, Redis-backed scale, and a deployment story that works from a laptop to cloud production.

Created, built, and maintained solely by [Suhas Bhairav](https://suhasbhairav.com).

## Why justfastllm Exists

Most LLM integrations start simple and become expensive to operate: every product team adds its own provider keys, retry behavior, logging, model routing, guardrails, caching, and memory layer. `justfastllm` turns that sprawl into one focused gateway.

Use it when you want:

- One internal API surface for many LLM providers.
- Provider flexibility without rewriting application code.
- Direct HTTP calls instead of heavy provider SDKs.
- Redis-backed caching and user memory that can scale horizontally.
- Runtime guardrail controls without redeploying the service.
- A self-hosted gateway that can run on your infrastructure.

## What You Get

| Layer | Capability |
| --- | --- |
| Gateway | OpenAI-style `/v1/chat/completions`, `/v1/messages`, model listing, provider routing |
| Endpoint Coverage | Chat, messages, completions, responses, embeddings, images, audio, moderation, rerank |
| Providers | OpenAI, Anthropic, DeepSeek, xAI Grok, Qwen, Kimi / Moonshot, Ollama |
| Extensibility | Factory Pattern via `ProviderFactory`, plus env-driven OpenAI-compatible providers |
| Operations | Health checks, structured request logging, request IDs, Docker health checks |
| Performance | Raw ASGI app, pooled stdlib HTTP, dataclasses, minimal dependency footprint |
| Scale | Database or Redis-backed cache, memory, and control-plane state for multi-instance deployments |
| Safety | Request validation, configurable guardrails, runtime guardrail enable/disable endpoint |
| Product Features | Agents, skills, persistent preferences, feedback capture |
| Control Plane | Master key, virtual keys, service-account keys, model access, budgets, RPM/TPM limits |
| Databases | Control-plane persistence for Postgres, MySQL, MongoDB, MSSQL, Redis, and file snapshots |
| Analytics | Token, pricing, spend, cache, latency, user, team, model, and provider rollups |
| Dashboard | Next.js App Router dashboard for operations, pricing, speed, guardrails, usage, and keys |
| Configuration | `.env` plus optional structured YAML/JSON config file |
| Enterprise Controls | Secret references, policies, plugin hooks, audit log, retention, privacy export/erasure, and runtime config reload |
| Compliance Readiness | GDPR, SOC 2, DPA, and India DPDP technical-control guide with operator checklist |
| Contracts | `/openapi.json`, documented endpoints, `.env.example`, deployment descriptors |

## Gateway Control Plane

`justfastllm` includes an admin control plane for operating a shared LLM gateway across applications, teams, and service accounts.

| Feature | Endpoint |
| --- | --- |
| Generate/list/delete virtual keys | `/v1/keys`, `/key/generate`, `/key/info`, `/key/delete` |
| Per-key model access | `models` on key creation |
| Per-key budgets | `budget_usd` on key creation |
| Per-key RPM and TPM limits | `rpm_limit`, `tpm_limit` on key creation |
| Model aliases | `aliases` on key creation |
| Users and spend buckets | `/v1/proxy/users`, `/user/new`, `/user/info` |
| Teams and spend buckets | `/v1/proxy/teams`, `/team/new`, `/team/info`, `/team/list` |
| Service-account keys | `/v1/service-accounts/keys`, `/service_account/key/generate` |
| Metrics and usage | `/v1/metrics`, `/v1/usage`, `/v1/pricing`, `/v1/proxy/features` |
| Compliance status, report, and integrity | `/v1/compliance/status`, `/v1/compliance/evidence`, `/v1/compliance/integrity`, `/v1/compliance/report` |
| Privacy export and erasure | `/v1/privacy/users/{user_id}/export`, `/v1/privacy/users/{user_id}/erase` |
| Fallback routing | `JUSTFASTLLM_FALLBACK_PROVIDERS` or `fallback_providers` in request JSON |
| Traffic mirroring | `JUSTFASTLLM_MIRROR_PROVIDER` or `mirror_provider` in request JSON |

Set a master key to protect administrative routes and require virtual-key authentication for gateway traffic:

```bash
JUSTFASTLLM_MASTER_KEY=change-me
JUSTFASTLLM_KEY_HEADER_NAME=authorization
JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND=database
JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/justfastllm?sslmode=require
JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE=justfastllm_control_plane_state
```

Create a key:

```bash
curl -X POST http://localhost:8000/v1/keys \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "production-app",
    "user_id": "user-1",
    "team_id": "platform",
    "models": ["gpt-5-nano"],
    "budget_usd": 25,
    "rpm_limit": 120,
    "tpm_limit": 120000,
    "aliases": {"fast": "gpt-5-nano"}
  }'
```

Route-level controls are available with `allowed_routes`. Supported route names are `chat`, `messages`, `completions`, `responses`, `embeddings`, `images`, `audio`, `moderations`, `rerank`, and `agents`.

Policies and plugin hooks are executable at runtime:

```bash
JUSTFASTLLM_POLICY_ALLOWED_PROVIDERS=openai,ollama
JUSTFASTLLM_POLICY_REQUIRED_TAGS=prod
JUSTFASTLLM_PLUGIN_MODULES=my_gateway_plugin
```

Provider keys and the master key can use secret references:

```bash
OPENAI_API_KEY=env:OPENAI_SECRET_NAME
JUSTFASTLLM_MASTER_KEY=file:/run/secrets/gateway_master_key
```

Compliance-readiness controls are documented in [Compliance Readiness](docs/COMPLIANCE.md), with a counsel-review starting point in [Data Processing Addendum Template](docs/DPA_TEMPLATE.md). The gateway automates technical controls such as retention, export, erasure, audit, auth, redaction, policies, guardrails, and evidence mapping; the deploying organization still owns legal notices, contracts, subprocessors, incident process, and any independent SOC 2 examination.

## Dashboard

The repository includes a Next.js + JavaScript + App Router dashboard in [`dashboard`](dashboard). It connects to the gateway APIs and visualizes requests, tokens, estimated spend, latency percentiles, provider/model distribution, virtual keys, guardrails, pricing, users, and teams.

```bash
cd dashboard
npm install
npm run dev
```

By default the dashboard connects to `http://localhost:8000`. Set `NEXT_PUBLIC_GATEWAY_URL` for another gateway URL.

## Benchmark Snapshot

Measured locally on September 26, 2026 with Python's in-process ASGI path. The added-latency benchmark isolates gateway overhead by using a deterministic in-process upstream provider, so the numbers below exclude OpenAI network time and model generation time.

| Metric | Result |
| --- | ---: |
| Benchmark iterations | 10,000 |
| Warmup requests | 500 |
| Mean added latency | `0.0649 ms` |
| Median added latency | `0.0623 ms` |
| p95 added latency | `0.0786 ms` |
| p99 added latency | `0.0891 ms` |
| Max added latency | `0.2454 ms` |
| Throughput | `15,354.17 req/s` |
| Memory before app | `31.484 MB` |
| Memory at rest | `31.828 MB` |
| App memory delta | `0.344 MB` |
| Provider SDKs in runtime path | `none` |

The p99 added gateway latency in this run is `0.0891 ms`, which is below the `1 ms` overhead target.

Command:

```bash
PYTHONPATH=src python3 benchmarks/benchmark_gateway.py \
  --iterations 10000 \
  --warmup 500 \
  --json
```

Real OpenAI verification was also run through the gateway using `gpt-5-nano`.

| Real Model Verification | Result |
| --- | ---: |
| Provider | `openai` |
| Model | `gpt-5-nano` |
| Calls requested | `50` |
| Calls verified | `50` |
| Failures | `0` |
| Reasoning effort | `minimal` |
| Max completion tokens | `128` |
| Mean end-to-end latency | `758.472 ms` |
| Median end-to-end latency | `664.447 ms` |
| p95 end-to-end latency | `977.734 ms` |
| p99 end-to-end latency | `2694.137 ms` |

Command:

```bash
PYTHONPATH=src python3 benchmarks/benchmark_gateway.py \
  --real-openai-calls 50 \
  --model gpt-5-nano \
  --json
```

The real model latency numbers are end-to-end gateway-to-OpenAI timings. They validate real provider execution; they are not used as the gateway-added-overhead measurement.

## Provider Coverage

| Provider | Style | Notes |
| --- | --- | --- |
| OpenAI | Chat Completions | Native OpenAI-compatible chat gateway |
| Anthropic | Messages API | Dedicated Anthropic request/response adapter |
| DeepSeek | OpenAI-compatible | Direct HTTP provider adapter |
| xAI Grok | OpenAI-compatible | Direct HTTP provider adapter |
| Qwen | OpenAI-compatible | Direct HTTP provider adapter |
| Kimi / Moonshot | OpenAI-compatible | Direct HTTP provider adapter |
| Ollama | Local OpenAI-compatible | Real local model verification supported |
| Custom providers | OpenAI-compatible | Add via environment variables without new code |

## Built For Enterprise Control

`justfastllm` is designed around the boring things that matter in production: predictable routing, simple deployment, runtime observability, low memory pressure, clear abstractions, and minimal hidden magic.

| Enterprise Need | justfastllm Approach |
| --- | --- |
| Centralized access | Route app traffic through one gateway instead of scattering provider integrations |
| Provider independence | Swap providers or models with config and routing conventions |
| Cost discipline | Cache repeatable responses with database or Redis storage and keep runtime dependencies small |
| Operational visibility | Request logs, request IDs, `/health`, and provider error normalization |
| Deployment ownership | Run it yourself on Docker, Render, Railway, AWS, or GCP |
| Memory across sessions | Store preferences and feedback by user ID |
| Safety controls | Toggle guardrails through API instead of hardcoding behavior |
| Clean engineering | Provider clients are isolated behind a common contract and factory |

## Deployment Matrix

| Platform | Included Artifact | Best For |
| --- | --- | --- |
| Docker | `Dockerfile` | Portable production image |
| Docker Compose | `docker-compose.yml` | Local production-like gateway plus Postgres |
| Render | `render.yaml` | Managed Docker web service with database-backed persistence |
| Railway | `railway.toml` | Fast Dockerfile-based deployment |
| AWS | `deploy/aws/AppRunner.yaml` | App Runner service backed by an ECR image |
| GCP | `deploy/gcp/cloudrun-service.yaml` | Cloud Run service with Secret Manager database URL |

See [Enterprise](docs/ENTERPRISE.md), [Deployment](docs/DEPLOYMENT.md), [Docker](docs/DOCKER.md), and [Operations](docs/OPERATIONS.md) for complete platform guidance.

## Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[speedups]"
cp .env.example .env
justfastllm
```

The gateway starts on:

```text
http://0.0.0.0:8000
```

## Run With Docker

```bash
docker build -t justfastllm:latest .
docker run --rm --env-file .env -p 8000:8000 justfastllm:latest
```

For a Redis-backed local stack:

```bash
docker compose up --build
```

## First Request

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "openai",
    "model": "gpt-5-nano",
    "messages": [{"role": "user", "content": "Say hello in five words."}]
  }'
```

You can also route by model prefix:

```json
{
  "model": "anthropic/claude-sonnet-4-5",
  "messages": [{"role": "user", "content": "Summarize this gateway."}]
}
```

## Configuration

API keys belong in `.env`:

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5-nano
ANTHROPIC_API_KEY=...
DEEPSEEK_API_KEY=...
XAI_API_KEY=...
QWEN_API_KEY=...
KIMI_API_KEY=...

# Ollama is local and keyless unless you put it behind auth.
OLLAMA_API_KEY=
```

`OPENAI_MODEL` is supported as a convenience alias for the OpenAI default model used by the gateway. `OPENAI_DEFAULT_MODEL` is also supported for explicit provider naming.

Redis powers the default shared cache for simple deployments. Compliance-sensitive deployments can use SQLAlchemy or MongoDB database URLs for response cache, user memory, and the control plane across Postgres, MySQL, MSSQL, and MongoDB:

```bash
JUSTFASTLLM_CACHE_BACKEND=database
JUSTFASTLLM_CACHE_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/justfastllm?sslmode=require
JUSTFASTLLM_CACHE_DATABASE_TABLE=justfastllm_response_cache
JUSTFASTLLM_USER_MEMORY_BACKEND=database
JUSTFASTLLM_USER_MEMORY_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/justfastllm?sslmode=require
JUSTFASTLLM_USER_MEMORY_DATABASE_TABLE=justfastllm_user_memory
```

In compliance mode, response-cache storage remains disabled unless `JUSTFASTLLM_CACHE_PERSONAL_DATA_ALLOWED=true` is set by policy.

For local tests or single-process development:

```bash
JUSTFASTLLM_CACHE_BACKEND=memory
JUSTFASTLLM_USER_MEMORY_BACKEND=memory
```

Use a structured config file when environment variables become too noisy:

```bash
JUSTFASTLLM_CONFIG_FILE=config.example.yaml
```

Add any OpenAI-compatible provider without writing code:

```bash
JUSTFASTLLM_OPENAI_COMPATIBLE_PROVIDERS=openrouter
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_DEFAULT_MODEL=openai/gpt-5-nano
```

## Gateway Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Runtime health check |
| `GET /openapi.json` | OpenAPI contract |
| `GET /v1/models` | List known gateway models; requires gateway auth when configured |
| `GET /v1/providers/{provider}/models` | List models for a provider; requires gateway auth when configured |
| `POST /v1/chat/completions` | OpenAI-style chat completion |
| `POST /v1/messages` | Anthropic-style messages entry point |
| `GET /v1/skills` | List gateway skills; requires the master key when configured |
| `POST /v1/skills/{skill_name}/run` | Run a skill; requires the master key when configured |
| `GET /v1/guardrails` | Inspect guardrail settings; requires the master key when configured |
| `PATCH /v1/guardrails` | Enable or disable guardrails; requires the master key when configured |
| `POST /v1/agents/runs` | Run an agent workflow |
| `GET /v1/users/{user_id}/preferences` | Read user preferences |
| `PUT /v1/users/{user_id}/preferences` | Update user preferences |
| `GET /v1/users/{user_id}/feedback` | Read user feedback |
| `POST /v1/users/{user_id}/feedback` | Store user feedback |

## Quality Gates

The core test suite uses Python's standard library:

```bash
PYTHONPATH=src python3 -m unittest
```

Compile-check the source, tests, and benchmark script:

```bash
PYTHONPATH=src python3 -m compileall -q src tests benchmarks
```

Run the local gateway-overhead benchmark:

```bash
PYTHONPATH=src python3 benchmarks/benchmark_gateway.py \
  --iterations 10000 \
  --warmup 500 \
  --json
```

Run 50 real OpenAI gateway calls with `gpt-5-nano`:

```bash
PYTHONPATH=src python3 benchmarks/benchmark_gateway.py \
  --real-openai-calls 50 \
  --model gpt-5-nano \
  --json
```

For major gateway changes, run the real Ollama integration test when Ollama is available:

```bash
JUSTFASTLLM_RUN_OLLAMA_TESTS=1 \
JUSTFASTLLM_OLLAMA_TEST_MODEL=qwen3:8b \
PYTHONPATH=src python3 -m unittest tests.test_ollama_integration
```

## Documentation

- [Deployment](docs/DEPLOYMENT.md)
- [Architecture](docs/ARCHITECTURE.md)
- [API Reference](docs/API.md)
- [Configuration](docs/CONFIGURATION.md)
- [Docker](docs/DOCKER.md)
- [Operations](docs/OPERATIONS.md)
- [Performance](docs/PERFORMANCE.md)
- [Providers](docs/PROVIDERS.md)
- [Testing](docs/TESTING.md)

## Design Principles

- Keep the runtime small.
- Keep providers isolated.
- Keep provider SDKs out of the gateway path.
- Keep deployment self-hosted and portable.
- Keep controls visible through documented APIs.
- Keep user preferences and feedback available across sessions.

## Provider API Notes

This project is designed around the current provider direction as documented by official sources:

- OpenAI Chat Completions: https://developers.openai.com/api/reference
- Anthropic Messages API: https://platform.claude.com/docs/en/api/messages/create
- DeepSeek Chat Completions: https://api-docs.deepseek.com/api/create-chat-completion/
- xAI REST API: https://docs.x.ai/developers/rest-api-reference/inference
- Alibaba/Qwen OpenAI-compatible interfaces: https://help.aliyun.com/en/model-studio/
- Kimi API overview: https://www.kimi.ai/help/kimi-api/api-overview
- Ollama OpenAI compatibility: https://github.com/ollama/ollama/blob/main/docs/openai.md

## License

MIT

Copyright (c) 2026 Suhas Bhairav. All rights reserved under the MIT License.
