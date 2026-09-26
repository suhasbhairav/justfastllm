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
| `JUSTFASTLLM_CACHE_BACKEND` | `redis` | `redis` for shared production cache or `memory` for local tests. |
| `JUSTFASTLLM_CACHE_TTL_SECONDS` | `60` | Cache entry lifetime. |
| `JUSTFASTLLM_CACHE_MAX_ITEMS` | `1024` | Maximum cache entries. |
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
| `JUSTFASTLLM_USER_MEMORY_BACKEND` | `redis` | `redis` for persistent memory or `memory` for tests/dev. |
| `JUSTFASTLLM_USER_MEMORY_KEY_PREFIX` | `justfastllm:memory:` | Redis key prefix for user preferences and feedback. |
| `JUSTFASTLLM_POLICY_DENIED_MODELS` | empty | Comma-separated model IDs blocked before provider calls. |
| `JUSTFASTLLM_POLICY_ALLOWED_PROVIDERS` | empty | Optional comma-separated provider allow-list. |
| `JUSTFASTLLM_POLICY_REQUIRED_TAGS` | empty | Comma-separated tags required on JSON requests. |
| `JUSTFASTLLM_POLICY_MAX_PROMPT_TOKENS` | `0` | Estimated prompt-token ceiling. `0` disables this check. |
| `JUSTFASTLLM_POLICY_RESPONSE_BLOCK_PATTERNS` | empty | Comma-separated regexes blocked in successful provider responses. |
| `JUSTFASTLLM_PLUGIN_MODULES` | empty | Comma-separated Python modules with `before_request` and/or `after_response` hooks. |
| `JUSTFASTLLM_MASTER_KEY` | empty | Enables admin-route protection and requires virtual-key auth for gateway traffic. |
| `JUSTFASTLLM_KEY_HEADER_NAME` | `authorization` | Header used for master and virtual keys. Custom headers can send raw keys. |
| `JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH` | empty | Optional JSON snapshot path for keys, users, teams, spend, and recent events. |
| `JUSTFASTLLM_FALLBACK_PROVIDERS` | empty | Comma-separated providers to try after upstream 5xx responses. |
| `JUSTFASTLLM_MIRROR_PROVIDER` | empty | Optional provider that receives fire-and-forget mirrored traffic. |

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

Guardrail configuration can also be inspected and updated at runtime with `GET /v1/guardrails` and `PATCH /v1/guardrails`.

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
