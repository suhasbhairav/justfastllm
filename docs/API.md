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

## `POST /v1/completions`

OpenAI-compatible text completions proxy. The request body is forwarded to the selected provider after gateway fields such as `provider`, `fallback_providers`, `mirror_provider`, and `tags` are removed.

## `POST /v1/responses`

OpenAI-compatible responses proxy for providers that expose a responses-style endpoint.

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

## Additional OpenAI-Compatible Endpoints

These routes are proxied to the selected provider and use the same virtual-key authentication, model access, budget, rate-limit, fallback, usage, and spend accounting path as chat traffic.

| Route | Notes |
| --- | --- |
| `POST /v1/embeddings` | JSON request body; cacheable for repeated embedding inputs. |
| `POST /v1/images/generations` | JSON request body for image generation. |
| `POST /v1/images/edits` | Supports raw multipart pass-through. |
| `POST /v1/images/variations` | Supports raw multipart pass-through. |
| `POST /v1/audio/transcriptions` | Supports raw multipart pass-through. |
| `POST /v1/audio/translations` | Supports raw multipart pass-through. |
| `POST /v1/moderations` | JSON request body for moderation models. |
| `POST /v1/rerank` | JSON request body; cacheable for repeated rerank inputs. |
| `POST /rerank` | Alias for `/v1/rerank`. |

For JSON bodies, provider selection works through `provider`, `X-LLM-Provider`, model prefix, or `JUSTFASTLLM_DEFAULT_PROVIDER`. For multipart bodies, use `X-LLM-Provider` or the default provider because multipart payloads are forwarded as raw bytes.

## Proxy Control Plane

Set `JUSTFASTLLM_MASTER_KEY` to require a master key for administrative routes and virtual keys for gateway traffic. See [Proxy Control Plane](PROXY_CONTROL_PLANE.md) for end-to-end examples.

### `GET /v1/keys`

Lists virtual keys. Requires the master key when configured.

### `POST /v1/keys`

Creates a virtual key with optional model access, budget, RPM, TPM, aliases, user, team, and metadata.

```json
{
  "name": "production-app",
  "user_id": "user-1",
  "team_id": "platform",
  "models": ["gpt-5-nano"],
  "allowed_routes": ["chat", "embeddings"],
  "budget_usd": 25,
  "rpm_limit": 120,
  "tpm_limit": 120000,
  "aliases": {"fast": "gpt-5-nano"}
}
```

### `POST /v1/keys/info`

Returns public key metadata and spend. `GET /key/info?key=...` is also supported.

### `DELETE /v1/keys`

Deletes a virtual key. `DELETE /v1/keys?key=...` and `POST /key/delete` are also supported.

### `POST /v1/proxy/users`

Creates a user spend bucket.

### `GET /v1/proxy/users`

Lists user spend buckets.

### `GET /v1/proxy/users/info`

Returns user spend and attached keys. Accepts `user_id` as a query parameter.

### `POST /v1/proxy/teams`

Creates a team spend bucket.

### `GET /v1/proxy/teams`

Lists team spend buckets.

### `GET /v1/proxy/teams/info`

Returns team spend and attached keys. Accepts `team_id` as a query parameter.

### `POST /v1/service-accounts/keys`

Creates a service-account key for automation jobs.

### `GET /v1/metrics`

Returns token, cost, latency, cache, provider, model, key, user, and team rollups.

### `GET /v1/usage`

Returns recent request-level usage events.

### `GET /v1/pricing`

Returns the local pricing table used for cost estimates.

### `GET /v1/proxy/features`

Returns the gateway feature inventory used by the dashboard.

### `GET /v1/policies`

Returns runtime request and response policy configuration.

### `GET /v1/plugins`

Returns loaded plugin hook modules.

### `GET /v1/providers/health`

Returns provider status derived from request events: request count, errors, error rate, latency percentiles, tokens, spend, and last-seen timestamp.

### `GET /v1/alerts`

Returns derived operational alerts for provider error rate, provider latency, key budgets, user budgets, and team budgets.

### `POST /v1/config/reload`

Reloads gateway settings from `.env`, environment variables, and the optional structured config file. Requires the master key.

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
