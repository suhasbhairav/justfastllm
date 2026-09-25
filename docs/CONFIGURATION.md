# Configuration

Configuration is loaded from `.env` and then overridden by real environment variables.

## Server

| Variable | Default | Purpose |
| --- | --- | --- |
| `JUSTFASTLLM_HOST` | `0.0.0.0` | Bind host for the CLI runner. |
| `JUSTFASTLLM_PORT` | `8000` | Bind port for the CLI runner. |
| `JUSTFASTLLM_LOG_LEVEL` | `INFO` | Python logging level. |
| `JUSTFASTLLM_DEFAULT_PROVIDER` | `openai` | Provider used when a request does not specify one. |

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
