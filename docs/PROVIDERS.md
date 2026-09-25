# Providers

All providers are reached with direct HTTP calls. Provider SDKs are not used.

## OpenAI-Compatible Providers

The following providers use `OpenAICompatibleProvider`:

- `openai`
- `deepseek`
- `grok`
- `qwen`
- `kimi`
- `ollama`

The gateway sends:

```http
POST {BASE_URL}/chat/completions
Authorization: Bearer {API_KEY}
Content-Type: application/json
```

The upstream response body is returned unchanged. Ollama does not require an API key by default, so the Authorization header is omitted unless `OLLAMA_API_KEY` is configured.

Model discovery for OpenAI-compatible providers is available through:

```http
GET /v1/providers/{provider}/models
```

The gateway proxies this to `{BASE_URL}/models`.

## Custom Providers

Any OpenAI-compatible provider can be added through `.env` without code changes:

```bash
JUSTFASTLLM_OPENAI_COMPATIBLE_PROVIDERS=openrouter
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_DEFAULT_MODEL=openai/gpt-4.1-mini
```

Requests can then use `"provider": "openrouter"` or a model prefix such as `openrouter/openai/gpt-4.1-mini`.

## Anthropic

Anthropic uses `POST /v1/messages` with these headers:

```http
x-api-key: {ANTHROPIC_API_KEY}
anthropic-version: 2023-06-01
Content-Type: application/json
```

The gateway accepts OpenAI-style `messages` and normalizes Anthropic's response back to an OpenAI-style `chat.completion` object.

Anthropic model discovery is available through `GET /v1/providers/anthropic/models`.

## Adding A Provider

1. Prefer `.env` configuration when the provider is OpenAI-compatible.
2. Implement `ProviderClient.chat_completion` and `ProviderClient.messages` when the provider has a different native API.
3. Register it through `ProviderFactory`.
4. Add env defaults in `config.py`.
5. Add unit tests for request construction, auth headers, and response handling.
