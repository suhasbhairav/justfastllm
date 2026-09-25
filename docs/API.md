# API Reference

## `GET /health`

Returns gateway health and registered providers.

## `GET /openapi.json`

Returns the gateway OpenAPI 3.1 contract.

## `GET /v1/models`

Returns one default model entry per configured provider.

## `GET /v1/providers/{provider}/models`

Proxies live model discovery to a provider. OpenAI-compatible providers call `GET {BASE_URL}/models`; Anthropic calls `GET {ANTHROPIC_BASE_URL}/models` with Anthropic headers.

## `POST /v1/chat/completions`

OpenAI-compatible chat completion proxy.

```json
{
  "provider": "openai",
  "model": "gpt-5-nano",
  "messages": [{"role": "user", "content": "Hello"}],
  "temperature": 0.2
}
```

Provider can also be selected through `X-LLM-Provider` or a model prefix such as `ollama/llama3.1`.

## `POST /v1/messages`

Messages-style proxy. Anthropic requests are sent to `/v1/messages`; OpenAI-compatible providers are routed to chat completions.

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4-5",
  "messages": [{"role": "user", "content": "Hello"}],
  "max_tokens": 512
}
```

## `GET /v1/skills`

Lists registered gateway skills.

## `POST /v1/skills/{skill_name}/run`

Runs a gateway skill.

```json
{
  "arguments": {
    "text": "Contact test@example.com with Bearer secret"
  }
}
```

Built-in skills:

- `echo`
- `redact`
- `utc_time`

## Guardrails

### `GET /v1/guardrails`

Returns the current guardrail state.

### `PATCH /v1/guardrails`

Enables or disables all guardrails or specific checks at runtime.

```json
{
  "enabled": true,
  "disable": ["block_patterns"],
  "enable": ["message_length"]
}
```

Supported checks:

- `required_messages`
- `message_length`
- `block_patterns`

## `POST /v1/agents/runs`

Runs a minimal gateway-managed agent request. The gateway can execute selected skills, append their results to context, and call the selected provider.

```json
{
  "provider": "openai",
  "instructions": "Use skill results when helpful.",
  "input": "What time is it?",
  "skills": [
    {"name": "utc_time", "arguments": {}}
  ]
}
```

The provider response is returned directly.

## User Memory

The gateway can store user preferences and feedback so future requests remember them across sessions. Redis is the default backend.

### `GET /v1/users/{user_id}/preferences`

Returns stored preferences.

### `PUT /v1/users/{user_id}/preferences`

Creates or merges preferences.

```json
{
  "preferences": {
    "tone": "warm",
    "format": "bullets"
  }
}
```

### `POST /v1/users/{user_id}/feedback`

Stores feedback for later analysis.

```json
{
  "feedback": {
    "rating": 5,
    "comment": "Great response"
  }
}
```

### `GET /v1/users/{user_id}/feedback`

Returns stored feedback entries.

Requests that include `user`, `user_id`, or `X-User-ID` automatically receive remembered preferences as a leading system message.

## Response Headers

All HTTP responses include `x-request-id`. If the request includes `X-Request-ID`, the gateway echoes it; otherwise it generates one.

When `JUSTFASTLLM_CORS_ALLOW_ORIGIN` is configured, responses include CORS headers for browser clients and `OPTIONS` preflight requests.

## Error Shape

Gateway errors use a stable JSON envelope:

```json
{
  "error": {
    "code": "upstream_transport_error",
    "message": "connection failed"
  }
}
```

Provider transport failures return HTTP `502` with `code: upstream_transport_error`.

## Ollama Verification

Major gateway changes should be checked against a real local Ollama model when available:

```bash
JUSTFASTLLM_RUN_OLLAMA_TESTS=1 \
JUSTFASTLLM_OLLAMA_TEST_MODEL=qwen3:8b \
PYTHONPATH=src python3 -m unittest tests.test_ollama_integration
```

If `JUSTFASTLLM_OLLAMA_TEST_MODEL` is not set, the test selects the first local Ollama model with completion capability.
