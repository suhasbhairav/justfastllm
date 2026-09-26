# Configuration

Configuration is loaded from `.env` and then overridden by real environment variables.

## Server

| Variable | Default | Purpose |
| --- | --- | --- |
| `JUSTFASTLLM_HOST` | `0.0.0.0` | Bind host for the CLI runner. |
| `JUSTFASTLLM_PORT` | `8000` | Bind port for the CLI runner. |
| `JUSTFASTLLM_LOG_LEVEL` | `INFO` | Python logging level. |
| `JUSTFASTLLM_DEFAULT_PROVIDER` | `openai` | Provider used when a request does not specify one. |
| `JUSTFASTLLM_CONFIG_FILE` | empty | Optional YAML or JSON config file. Environment variables override config-file values. |

## Gateway Controls

| Variable | Default | Purpose |
| --- | --- | --- |
| `JUSTFASTLLM_CACHE_ENABLED` | `true` | Enables response cache for non-streaming chat requests. |
| `JUSTFASTLLM_CACHE_BACKEND` | `redis` | `database`, `redis`, or `memory` backend for non-streaming response cache. Use `database` for Postgres, MySQL, MSSQL, or MongoDB persistence. |
| `JUSTFASTLLM_CACHE_TTL_SECONDS` | `60` | Cache entry lifetime. |
| `JUSTFASTLLM_CACHE_MAX_ITEMS` | `1024` | Maximum cache entries. |
| `JUSTFASTLLM_CACHE_PERSONAL_DATA_ALLOWED` | `false` | Allows response-cache storage while compliance mode is enabled. Keep `false` unless prompts/responses are proven non-personal or covered by explicit policy and retention. |
| `JUSTFASTLLM_CACHE_DATABASE_URL` | empty | SQLAlchemy or MongoDB URL when `JUSTFASTLLM_CACHE_BACKEND=database`. Supports Postgres, MySQL, MSSQL, and MongoDB. |
| `JUSTFASTLLM_CACHE_DATABASE_TABLE` | `justfastllm_response_cache` | SQL table name or MongoDB collection name for response-cache entries. |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis endpoint for cache storage. |
| `JUSTFASTLLM_REDIS_KEY_PREFIX` | `justfastllm:cache:` | Prefix for gateway cache keys. |
| `JUSTFASTLLM_REQUEST_TIMEOUT_SECONDS` | `60` | Upstream HTTP timeout. |
| `JUSTFASTLLM_MAX_BODY_BYTES` | `1048576` | Maximum request body size. |
| `JUSTFASTLLM_MAX_MESSAGE_CHARS` | `200000` | Maximum combined text in messages. |
| `JUSTFASTLLM_BLOCK_PATTERNS` | empty | Comma-separated regexes blocked before upstream calls. |
| `JUSTFASTLLM_CORS_ALLOW_ORIGIN` | empty | Optional `Access-Control-Allow-Origin` value. |
| `JUSTFASTLLM_GUARDRAILS_ENABLED` | `true` | Enables or disables all guardrails. |
| `JUSTFASTLLM_DISABLED_GUARDRAILS` | empty | Comma-separated checks to disable: `required_messages`, `message_length`, `block_patterns`. |
| `JUSTFASTLLM_USER_MEMORY_ENABLED` | `true` | Enables stored user preferences and feedback. |
| `JUSTFASTLLM_USER_MEMORY_BACKEND` | `redis` | `database`, `redis`, or `memory` backend for preferences and feedback. Use `database` for Postgres, MySQL, MSSQL, or MongoDB persistence. |
| `JUSTFASTLLM_USER_MEMORY_KEY_PREFIX` | `justfastllm:memory:` | Redis key prefix for user preferences and feedback. |
| `JUSTFASTLLM_USER_MEMORY_DATABASE_URL` | empty | SQLAlchemy or MongoDB URL when `JUSTFASTLLM_USER_MEMORY_BACKEND=database`. |
| `JUSTFASTLLM_USER_MEMORY_DATABASE_TABLE` | `justfastllm_user_memory` | SQL table name or MongoDB collection name for preferences and feedback. |
| `JUSTFASTLLM_POLICY_DENIED_MODELS` | empty | Comma-separated model IDs blocked before provider calls. |
| `JUSTFASTLLM_POLICY_ALLOWED_PROVIDERS` | empty | Optional comma-separated provider allow-list. |
| `JUSTFASTLLM_POLICY_REQUIRED_TAGS` | empty | Comma-separated tags required on JSON requests. |
| `JUSTFASTLLM_POLICY_MAX_PROMPT_TOKENS` | `0` | Estimated prompt-token ceiling. `0` disables this check. |
| `JUSTFASTLLM_POLICY_RESPONSE_BLOCK_PATTERNS` | empty | Comma-separated regexes blocked in successful provider responses. |
| `JUSTFASTLLM_PLUGIN_MODULES` | empty | Comma-separated Python modules with `before_request` and/or `after_response` hooks. |
| `JUSTFASTLLM_MASTER_KEY` | empty | Enables admin-route protection and requires virtual-key auth for gateway traffic. |
| `JUSTFASTLLM_KEY_HEADER_NAME` | `authorization` | Header used for master and virtual keys. Custom headers can send raw keys. |
| `JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND` | `memory` or `file` when a path is set | `database`, `redis`, `file`, or `memory` storage for keys, users, teams, spend, usage, audit, privacy requests, and erasure evidence. Use `database` for production multi-service durability. |
| `JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH` | empty | JSON snapshot path when `JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND=file`. |
| `JUSTFASTLLM_CONTROL_PLANE_REDIS_URL` | `REDIS_URL` | Redis endpoint for control-plane state when `JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND=redis`. |
| `JUSTFASTLLM_CONTROL_PLANE_REDIS_KEY` | `justfastllm:control-plane:state` | Redis key for the control-plane state snapshot. |
| `JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL` | empty | SQLAlchemy or MongoDB URL when `JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND=database`. Supports Postgres, MySQL, MSSQL, and MongoDB. |
| `JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE` | `justfastllm_control_plane_state` | SQL table name or MongoDB collection name for the control-plane state snapshot. |

When `JUSTFASTLLM_COMPLIANCE_MODE=true` or `JUSTFASTLLM_REQUIRE_COMPLIANCE_READY=true`, database-backed control-plane, cache, and user-memory stores run in strict mode. Database load/save failures raise errors instead of silently falling back to in-memory behavior, so deployments fail visibly rather than losing auth, audit, access, privacy, memory, or cache evidence.

The compliance readiness gate treats Postgres, MySQL, MSSQL, and MongoDB as supported production database families. Unsupported database URL families, such as SQLite, are reported in `/health` and `/v1/compliance/status` through `database_families` and fail the `database_families_supported` check when the relevant store is enabled. Remote database URLs must explicitly request encrypted transport; local `localhost`, `127.0.0.1`, and `::1` URLs pass for development and CI.

| Database | Encrypted URL indicator accepted by readiness |
| --- | --- |
| Postgres | `?sslmode=require`, `?sslmode=verify-ca`, or `?sslmode=verify-full` |
| MySQL / MariaDB | `?ssl_mode=REQUIRED`, `?ssl_mode=VERIFY_CA`, `?ssl_mode=VERIFY_IDENTITY`, or explicit `ssl_ca` / `ssl_cert` / `ssl_key` parameters |
| MSSQL | `?encrypt=true`, `?encrypt=yes`, `?encrypt=mandatory`, or `?encrypt=strict` |
| MongoDB | `?tls=true`, `?ssl=true`, or `mongodb+srv://...` without disabling TLS |

| Variable | Default | Purpose |
| --- | --- | --- |
| `JUSTFASTLLM_FALLBACK_PROVIDERS` | empty | Comma-separated providers to try after upstream 5xx responses. |
| `JUSTFASTLLM_MIRROR_PROVIDER` | empty | Optional provider that receives fire-and-forget mirrored traffic. |
| `JUSTFASTLLM_COMPLIANCE_MODE` | `true` | Enables compliance-readiness metadata and keeps privacy controls visible in `/v1/compliance/status`. |
| `JUSTFASTLLM_USAGE_RETENTION_DAYS` | `90` | Automatic retention window for request-level usage events. Use `0` only when the operator has a separate lawful retention process. |
| `JUSTFASTLLM_AUDIT_RETENTION_DAYS` | `365` | Automatic retention window for administrative audit events, endpoint access events, and authentication events. Use a longer value when required by customer contracts, incident response, or audit policy. |
| `JUSTFASTLLM_PRIVACY_CONTACT` | empty | Contact displayed in compliance status for privacy requests. |
| `JUSTFASTLLM_SECURITY_CONTACT` | empty | Contact displayed in compliance status for security or incident reporting. |
| `JUSTFASTLLM_SUBPROCESSORS_URL` | empty | Operator-maintained subprocessor page URL. |
| `JUSTFASTLLM_DPA_URL` | empty | Operator-maintained data-processing agreement URL. |
| `JUSTFASTLLM_REQUIRE_COMPLIANCE_READY` | `false` | When `true`, `/health` returns `503` until core privacy/security readiness checks pass, including durable control-plane storage, a real control-plane evidence write with matching readback, write/read verified durable enabled user memory, write/read verified enabled response-cache storage, contacts, DPA/subprocessor URLs, retention, CORS, and cache minimization. Production deployment descriptors enable this. |

## API Keys

| Provider | Key Variable | Base URL Variable |
| --- | --- | --- |
| OpenAI | `OPENAI_API_KEY` | `OPENAI_BASE_URL` |
| Anthropic | `ANTHROPIC_API_KEY` | `ANTHROPIC_BASE_URL` |
| DeepSeek | `DEEPSEEK_API_KEY` | `DEEPSEEK_BASE_URL` |
| xAI Grok | `XAI_API_KEY` | `XAI_BASE_URL` |
| Qwen | `QWEN_API_KEY` | `QWEN_BASE_URL` |
| Kimi / Moonshot | `KIMI_API_KEY` | `KIMI_BASE_URL` |
| Ollama | `OLLAMA_API_KEY` | `OLLAMA_BASE_URL` |

Key values can be direct strings, environment references, or file references:

```bash
OPENAI_API_KEY=env:OPENAI_SECRET_NAME
ANTHROPIC_API_KEY=file:/run/secrets/anthropic_api_key
JUSTFASTLLM_MASTER_KEY=env:GATEWAY_MASTER_KEY
```

## Provider Selection

Provider selection order:

1. `provider` in JSON request body.
2. `X-LLM-Provider` HTTP header.
3. Prefix in `model`, such as `qwen/qwen-max`.
4. `JUSTFASTLLM_DEFAULT_PROVIDER`.

## Custom OpenAI-Compatible Providers

Use `JUSTFASTLLM_OPENAI_COMPATIBLE_PROVIDERS` to add any provider that implements the OpenAI chat completions format.

```bash
JUSTFASTLLM_OPENAI_COMPATIBLE_PROVIDERS=openrouter,local-ai

OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_DEFAULT_MODEL=openai/gpt-5-nano

LOCAL_AI_REQUIRES_API_KEY=false
LOCAL_AI_BASE_URL=http://localhost:8080/v1
LOCAL_AI_DEFAULT_MODEL=tinyllama
```

Provider names are normalized to lowercase route names. Environment variable prefixes are uppercased and non-alphanumeric characters become underscores, so `local-ai` uses `LOCAL_AI_*`.

Guardrail configuration can also be inspected and updated at runtime with `GET /v1/guardrails` and `PATCH /v1/guardrails`. These routes require the master key when configured.

## Control Plane

Set a master key before exposing administrative routes:

```bash
JUSTFASTLLM_MASTER_KEY=change-me
```

Use the default `Authorization: Bearer <key>` header, or configure a custom header:

```bash
JUSTFASTLLM_KEY_HEADER_NAME=x-justfastllm-key
```

Virtual keys can carry model allow-lists, budgets, RPM/TPM limits, aliases, user IDs, team IDs, and metadata. User and team spend buckets are available through the proxy control-plane routes.

See [Proxy Control Plane](PROXY_CONTROL_PLANE.md).

## Privacy And Compliance Controls

The gateway includes runtime controls for data minimization, retention, access, erasure, and compliance evidence.

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/compliance/status` | Returns enabled technical controls, retention windows, contact metadata, and operator-required compliance tasks. Requires master key. |
| `GET /v1/compliance/integrity` | Returns counts and SHA-256 digests for sanitized evidence categories so release and audit exports can be matched later. Requires master key. |
| `GET /v1/compliance/report` | Returns a deployment-facing compliance readiness report with pass/fail runtime checks, evidence endpoints, and non-automatable operator gates. Requires master key. |
| `GET /v1/privacy/requests` | Lists tracked subject-rights, DPDP, and privacy workflow requests. Requires master key. |
| `POST /v1/privacy/requests` | Creates a durable privacy request record with request type, source, status, due date, notes, and metadata. Requires master key. |
| `PATCH /v1/privacy/requests/{request_id}` | Updates status, notes, due date, completion timestamp, or metadata for a tracked privacy request. Requires master key. |
| `GET /v1/privacy/users/{user_id}/export` | Exports user-linked control-plane records, keys, usage events, audit references, auth references, tracked privacy requests, preferences, and feedback. Requires master key. |
| `DELETE /v1/privacy/users/{user_id}/erase` | Erases or pseudonymizes user-linked user records, keys, usage events, audit references, auth references, tracked privacy requests, preferences, and feedback. Requires master key. |

These controls help operators meet privacy-by-design and subject-rights requirements, but they do not replace the operator's legal notices, customer data-processing agreement, transfer assessments, breach process, or independent SOC 2 examination.

When `JUSTFASTLLM_COMPLIANCE_MODE=true`, response caching is disabled unless `JUSTFASTLLM_CACHE_PERSONAL_DATA_ALLOWED=true`. This keeps prompt and response bodies out of Redis by default. Usage analytics still record metadata such as tokens, cost, model, provider, user ID, and key preview.

When `JUSTFASTLLM_REQUIRE_COMPLIANCE_READY=true`, `/health` checks for compliance mode, master key, durable control-plane storage, a real control-plane evidence write with matching readback, write/read verified durable enabled user memory, write/read verified enabled response-cache storage, retention windows, privacy/security contacts, subprocessor URL, DPA URL, non-wildcard CORS, and minimized response-cache behavior. Missing checks make `/health` return `503` so deployment platforms fail closed.

## Structured Config File

`JUSTFASTLLM_CONFIG_FILE` can point to a YAML or JSON file. Values from real environment variables override values loaded from the config file, so production secrets can stay in the platform secret manager.

```bash
JUSTFASTLLM_CONFIG_FILE=config.example.yaml
```

The sample file [`config.example.yaml`](../config.example.yaml) includes server settings, cache settings, guardrails, memory, control-plane settings, provider defaults, and custom OpenAI-compatible providers.

## Policies

Policies run before provider calls and after successful provider responses.

```bash
JUSTFASTLLM_POLICY_ALLOWED_PROVIDERS=openai,ollama
JUSTFASTLLM_POLICY_DENIED_MODELS=gpt-5
JUSTFASTLLM_POLICY_REQUIRED_TAGS=prod
JUSTFASTLLM_POLICY_MAX_PROMPT_TOKENS=12000
JUSTFASTLLM_POLICY_RESPONSE_BLOCK_PATTERNS=internal-only
```

## Plugin Hooks

Plugin modules are normal importable Python modules. A plugin can expose either or both hooks:

```python
def before_request(context, payload):
    payload["metadata"] = {"source": context["route"]}
    return payload

def after_response(context, payload):
    payload["gateway_provider"] = context["provider"]
    return payload
```

Enable plugins with:

```bash
JUSTFASTLLM_PLUGIN_MODULES=my_gateway_plugin
```

Use `POST /v1/config/reload` with the master key to reload policy, plugin, provider, cache, memory, and guardrail settings at runtime.
