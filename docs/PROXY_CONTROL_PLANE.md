# Proxy Control Plane

`justfastllm` provides a self-hosted control plane for operating a shared LLM gateway across products, users, teams, and automation jobs.

## Admin Security

Set `JUSTFASTLLM_MASTER_KEY` to require authenticated administrative access and virtual-key authentication for gateway traffic.

```bash
JUSTFASTLLM_MASTER_KEY=change-me
JUSTFASTLLM_KEY_HEADER_NAME=authorization
JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH=.justfastllm/proxy-control.json
```

By default, clients authenticate with:

```http
Authorization: Bearer <key>
```

If `JUSTFASTLLM_KEY_HEADER_NAME=x-justfastllm-key` is set, clients can instead send:

```http
x-justfastllm-key: <key>
```

## Virtual Keys

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
    "allowed_routes": ["chat", "embeddings"],
    "budget_usd": 25,
    "rpm_limit": 120,
    "tpm_limit": 120000,
    "aliases": {"fast": "gpt-5-nano"},
    "metadata": {"environment": "prod"}
  }'
```

Supported `allowed_routes` values are `chat`, `messages`, `completions`, `responses`, `embeddings`, `images`, `audio`, `moderations`, `rerank`, `agents`, and `*`.

List keys:

```bash
curl http://localhost:8000/v1/keys \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY"
```

Inspect a key:

```bash
curl -X POST http://localhost:8000/v1/keys/info \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"key":"sk-jfl-..."}'
```

Delete a key:

```bash
curl -X DELETE 'http://localhost:8000/v1/keys?key=sk-jfl-...' \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY"
```

## Users

Users are spend buckets and metadata records for applications that want per-user reporting.

```bash
curl -X POST http://localhost:8000/v1/proxy/users \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-1",
    "user_email": "user@example.com",
    "models": ["gpt-5-nano"],
    "max_budget": 10
  }'
```

Inspect user spend and attached keys:

```bash
curl 'http://localhost:8000/v1/proxy/users/info?user_id=user-1' \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY"
```

## Teams

Teams group keys and usage by business unit, customer, workspace, or environment.

```bash
curl -X POST http://localhost:8000/v1/proxy/teams \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "team_id": "platform",
    "team_alias": "Platform Engineering",
    "models": ["gpt-5-nano"],
    "max_budget": 100
  }'
```

List teams:

```bash
curl http://localhost:8000/v1/proxy/teams \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY"
```

## Service Accounts

Service-account keys are virtual keys with `key_type=service_account`, intended for scheduled jobs, agents, and backend workers.

```bash
curl -X POST http://localhost:8000/v1/service-accounts/keys \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "nightly-agent",
    "team_id": "platform",
    "models": ["gpt-5-nano"],
    "budget_usd": 15
  }'
```

## Analytics

| Endpoint | Purpose |
| --- | --- |
| `/v1/metrics` | Request counts, token totals, estimated spend, latency percentiles, cache hits/misses, key/user/team totals |
| `/v1/usage` | Recent request-level usage records |
| `/v1/pricing` | Local pricing table used for cost estimates |
| `/v1/proxy/features` | Machine-readable feature inventory for dashboards |

## Routing Controls

Configure global fallbacks:

```bash
JUSTFASTLLM_FALLBACK_PROVIDERS=anthropic,deepseek
```

Or per request:

```json
{
  "provider": "openai",
  "fallback_providers": ["anthropic", "deepseek"],
  "messages": [{"role": "user", "content": "Hello"}]
}
```

Configure traffic mirroring:

```bash
JUSTFASTLLM_MIRROR_PROVIDER=deepseek
```

Or per request:

```json
{
  "provider": "openai",
  "mirror_provider": "deepseek",
  "messages": [{"role": "user", "content": "Hello"}]
}
```

Mirrored calls are fire-and-forget and do not alter the client response.

## Current Persistence Model

The control plane runs in memory by default. Set `JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH` to persist keys, users, teams, spend, and recent request events to an atomic JSON snapshot.

The JSON snapshot is suitable for local development and single-node deployments. For horizontally scaled production deployments, keep one writer process for this file-backed mode or add a shared database backend before running multiple gateway replicas.

Response cache and user memory already support Redis.
