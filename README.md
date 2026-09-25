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
| Providers | OpenAI, Anthropic, DeepSeek, xAI Grok, Qwen, Kimi / Moonshot, Ollama |
| Extensibility | Factory Pattern via `ProviderFactory`, plus env-driven OpenAI-compatible providers |
| Operations | Health checks, structured request logging, request IDs, Docker health checks |
| Performance | Raw ASGI app, pooled stdlib HTTP, dataclasses, minimal dependency footprint |
| Scale | Redis cache and Redis-backed user memory for multi-instance deployments |
| Safety | Request validation, configurable guardrails, runtime guardrail enable/disable endpoint |
| Product Features | Agents, skills, persistent preferences, feedback capture |
| Contracts | `/openapi.json`, documented endpoints, `.env.example`, deployment descriptors |

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
| Cost discipline | Cache repeatable responses with Redis and keep runtime dependencies small |
| Operational visibility | Request logs, request IDs, `/health`, and provider error normalization |
| Deployment ownership | Run it yourself on Docker, Render, Railway, AWS, or GCP |
| Memory across sessions | Store preferences and feedback by user ID |
| Safety controls | Toggle guardrails through API instead of hardcoding behavior |
| Clean engineering | Provider clients are isolated behind a common contract and factory |

## Deployment Matrix

| Platform | Included Artifact | Best For |
| --- | --- | --- |
| Docker | `Dockerfile` | Portable production image |
| Docker Compose | `docker-compose.yml` | Local production-like gateway plus Redis |
| Render | `render.yaml` | Managed Docker web service with Redis |
| Railway | `railway.toml` | Fast Dockerfile-based deployment |
| AWS | `deploy/aws/AppRunner.yaml` | App Runner service backed by an ECR image |
| GCP | `deploy/gcp/cloudrun-service.yaml` | Cloud Run service with Secret Manager Redis URL |

See [Deployment](docs/DEPLOYMENT.md), [Docker](docs/DOCKER.md), and [Operations](docs/OPERATIONS.md) for complete platform guidance.

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
    "model": "gpt-4.1-mini",
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
ANTHROPIC_API_KEY=...
DEEPSEEK_API_KEY=...
XAI_API_KEY=...
QWEN_API_KEY=...
KIMI_API_KEY=...

# Ollama is local and keyless unless you put it behind auth.
OLLAMA_API_KEY=
```

Redis powers shared cache and user memory:

```bash
REDIS_URL=redis://localhost:6379/0
JUSTFASTLLM_CACHE_BACKEND=redis
JUSTFASTLLM_USER_MEMORY_BACKEND=redis
```

For local tests or single-process development:

```bash
JUSTFASTLLM_CACHE_BACKEND=memory
JUSTFASTLLM_USER_MEMORY_BACKEND=memory
```

Add any OpenAI-compatible provider without writing code:

```bash
JUSTFASTLLM_OPENAI_COMPATIBLE_PROVIDERS=openrouter
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_DEFAULT_MODEL=openai/gpt-4.1-mini
```

## Gateway Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Runtime health check |
| `GET /openapi.json` | OpenAPI contract |
| `GET /v1/models` | List known gateway models |
| `GET /v1/providers/{provider}/models` | List models for a provider |
| `POST /v1/chat/completions` | OpenAI-style chat completion |
| `POST /v1/messages` | Anthropic-style messages entry point |
| `GET /v1/skills` | List gateway skills |
| `POST /v1/skills/{skill_name}/run` | Run a skill |
| `GET /v1/guardrails` | Inspect guardrail settings |
| `PATCH /v1/guardrails` | Enable or disable guardrails |
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
