# Proxy Control Plane

`justfastllm` provides a self-hosted control plane for operating a shared LLM gateway across products, users, teams, and automation jobs.

## Admin Security

Set `JUSTFASTLLM_MASTER_KEY` to require authenticated administrative access and virtual-key authentication for gateway traffic.

```bash
JUSTFASTLLM_MASTER_KEY=change-me
JUSTFASTLLM_KEY_HEADER_NAME=authorization
JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND=database
JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL=env:JUSTFASTLLM_DATABASE_URL
JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE=justfastllm_control_plane_state
```

By default, clients authenticate with:

```http
Authorization: Bearer <key>
```

If `JUSTFASTLLM_KEY_HEADER_NAME=x-justfastllm-key` is set, clients can instead send:

```http
x-justfastllm-key: <key>
```

Every HTTP request records a sanitized access event with request ID, method, path, status, latency, auth context, key preview, user ID, and team ID. Access events are saved immediately to the configured durable control-plane backend and can be inspected through `/v1/access` without storing raw headers, prompts, completions, or request bodies.

Authentication decisions are recorded as sanitized auth events. Operators can inspect `/v1/auth/events` to review admin and virtual-key successes, failures, bypasses, reasons, routes, models, key previews, user IDs, and team IDs without storing raw tokens or headers.

Audit, endpoint access, and authentication events include tamper-evident hash-chain fields. `/v1/compliance/integrity` reports `hash_chain.valid`, `latest_hash`, and `broken_index` for each category so deployment and audit evidence can detect modified or reordered records.

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

Correct a user record:

```bash
curl -X PATCH http://localhost:8000/v1/proxy/users/update \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user-1","user_email":"corrected@example.com","metadata":{"name":"Corrected"}}'
```

## Teams

Teams group keys and usage by business unit, customer, workspace, or environment.

Correct a team record:

```bash
curl -X PATCH http://localhost:8000/v1/proxy/teams/update \
  -H "Authorization: Bearer $JUSTFASTLLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"team_id":"platform","team_alias":"Platform","metadata":{"owner":"ops"}}'
```

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

The control plane runs in memory by default. Use database-backed storage when you need durable control-plane records outside the gateway process:

```bash
JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND=database
JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/justfastllm
JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE=justfastllm_control_plane_state
```

Supported database URL families:

| Database | Driver package | URL example |
| --- | --- | --- |
| Postgres | `psycopg[binary]` | `postgresql+psycopg://user:pass@host:5432/justfastllm` |
| MySQL | `PyMySQL` | `mysql+pymysql://user:pass@host:3306/justfastllm` |
| MSSQL | `pymssql` | `mssql+pymssql://user:pass@host:1433/justfastllm` |
| MongoDB | `pymongo` | `mongodb://host:27017/justfastllm` |

`/health` and `/v1/compliance/status` include the detected database family and driver dependency for database-backed control-plane, cache, and user-memory stores.

Redis-backed storage is also available for single-writer managed deployments:

```bash
JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND=redis
JUSTFASTLLM_CONTROL_PLANE_REDIS_URL=$REDIS_URL
JUSTFASTLLM_CONTROL_PLANE_REDIS_KEY=justfastllm:control-plane:state
```

Database and Redis storage persist keys, users, teams, spend, usage, endpoint access, authentication, audit, privacy requests, and erasure evidence in a shared state snapshot. For local development and single-node deployments, set `JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND=file` plus `JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH` to persist an atomic JSON snapshot.

SQL state snapshots use an unbounded text-style column where the database supports it; MySQL uses `LONGTEXT` for the JSON state payload so audit-heavy deployments are not constrained by the smaller default `TEXT` size.

Built-in control-plane persistence is single-writer. Use one active gateway replica for compliance-sensitive deployments, or add an external database-backed control plane before running multiple active writer replicas.
