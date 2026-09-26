# API Reference

## `GET /health`

Returns gateway health and registered providers.

## `GET /openapi.json`

Returns the gateway OpenAPI 3.1 contract, including `MasterKeyAuth` and `GatewayAuth` security schemes for admin, gateway, and user-memory routes.

The test suite compares the app route inventory to the OpenAPI path list so new HTTP routes must be documented before release.

## `GET /v1/models`

Returns one default model entry per configured provider. Requires gateway authentication when configured.

## `GET /v1/providers/{provider}/models`

Proxies live model discovery to a provider. OpenAI-compatible providers call `GET {BASE_URL}/models`; Anthropic calls `GET {ANTHROPIC_BASE_URL}/models` with Anthropic headers. Requires gateway authentication when configured.

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

Lists registered gateway skills. Requires the master key when configured.

## `POST /v1/skills/{skill_name}/run`

Runs a gateway skill. Requires the master key when configured.

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

Returns the current guardrail state. Requires the master key when configured.

### `PATCH /v1/guardrails`

Enables or disables all guardrails or specific checks at runtime. Requires the master key when configured.

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

### `GET /v1/keys/info`

Returns public key metadata and spend. `POST /v1/keys/info`, `GET /key/info?key=...`, and `POST /key/info` are also supported.

### `PATCH /v1/keys/update`

Updates key access, budgets, rate limits, aliases, route access, disabled state, or metadata. `POST /v1/keys/update`, `PATCH /key/update`, and `POST /key/update` are also supported.

### `DELETE /v1/keys`

Deletes a virtual key. `DELETE /v1/keys?key=...` and `POST /key/delete` are also supported.

### `POST /v1/proxy/users`

Creates a user spend bucket.

### `GET /v1/proxy/users`

Lists user spend buckets.

### `GET /v1/proxy/users/info`

Returns user spend and attached keys. Accepts `user_id` as a query parameter.

### `PATCH /v1/proxy/users/update`

Updates a user spend bucket for correction or rectification workflows. Accepts `user_id` plus fields such as `user_email`, `models`, `max_budget`, limits, and `metadata`. `POST /v1/proxy/users/update` and legacy `POST /user/update` are also supported. Requires the master key.

### `POST /v1/proxy/teams`

Creates a team spend bucket.

### `GET /v1/proxy/teams`

Lists team spend buckets.

### `GET /v1/proxy/teams/info`

Returns team spend and attached keys. Accepts `team_id` as a query parameter.

### `PATCH /v1/proxy/teams/update`

Updates a team spend bucket for correction or rectification workflows. Accepts `team_id` plus fields such as `team_alias`, `models`, `max_budget`, and `metadata`. `POST /v1/proxy/teams/update` and legacy `POST /team/update` are also supported. Requires the master key.

### `POST /v1/service-accounts/keys`

Creates a service-account key for automation jobs.

Canonical update/delete routes and compatibility aliases are included in `/openapi.json` and require the master key: `/user/new`, `/user/info`, `/user/update`, `/user/delete`, `/v1/proxy/users/delete`, `/v1/proxy/teams/update`, `/v1/proxy/teams/delete`, `/team/new`, `/team/info`, `/team/list`, `/team/update`, `/team/delete`, and `/service_account/key/generate`.

### `GET /v1/metrics`

Returns token, cost, latency, cache, provider, model, key, user, and team rollups.

### `GET /v1/usage`

Returns recent request-level usage events.

### `GET /v1/access`

Returns compact endpoint access events with request ID, method, path, status code, latency, auth context, and timestamp. Events do not store headers, prompts, completions, or request bodies. Requires the master key.

### `GET /v1/auth/events`

Returns sanitized authentication events with auth type, outcome, reason, route, model, key preview, user ID, team ID, and timestamp. Events do not store raw tokens, headers, prompts, completions, or request bodies. Requires the master key.

### `GET /v1/pricing`

Returns the local pricing table used for cost estimates. Requires the master key when configured.

### `GET /v1/proxy/features`

Returns the gateway feature inventory used by the dashboard. Requires the master key when configured.

### `GET /v1/policies`

Returns runtime request and response policy configuration.

### `GET /v1/plugins`

Returns loaded plugin hook modules.

### `GET /v1/providers/health`

Returns provider status derived from request events: request count, errors, error rate, latency percentiles, tokens, spend, and last-seen timestamp.

### `GET /v1/alerts`

Returns derived operational alerts for provider error rate, provider latency, key budgets, user budgets, and team budgets.

### `GET /v1/compliance/status`

Returns compliance-readiness metadata, official framework source metadata, enabled technical controls, retention status, privacy-request summary counts, privacy/security contacts, subprocessor and DPA URLs, and operator-required tasks. Requires the master key.

### `GET /v1/compliance/evidence`

Returns a machine-readable compliance evidence map covering automated controls and operator-owned controls for GDPR, SOC 2, DPA, and India DPDP readiness. Evidence items include `mapped_requirements` and, where the gateway cannot automate the requirement, `operator_responsibilities`. Requires the master key.

### `GET /v1/compliance/integrity`

Returns counts and SHA-256 digests for sanitized evidence categories, including keys, users, teams, usage, audit, access, auth, and privacy-request records. Store this with release or audit evidence to match later exports without exposing raw tokens, prompts, completions, headers, or bodies. Requires the master key.

### `GET /v1/compliance/report`

Returns a deployment-facing readiness report with pass/fail runtime checks, implemented technical controls, evidence endpoints, official source metadata, and operator-required legal, rights-request, infrastructure, retention, incident, DPA, subprocessor, and SOC 2 attestation gates. Requires the master key.

### `GET /v1/privacy/requests`

Lists tracked privacy, subject-rights, DPDP, and grievance workflow requests. Supports `user_id`, `status`, and `limit` query parameters. The response also includes `supported_request_types` and `supported_statuses` for client validation. Requires the master key.

### `GET /v1/privacy/consents`

Lists durable consent records. Supports `user_id`, `status`, `purpose`, and `limit` query parameters. Requires the master key.

### `POST /v1/privacy/consents`

Creates a durable consent record with `user_id`, `purpose`, `lawful_basis`, `notice_version`, `source`, optional timestamps, status, and metadata. Supported statuses are `granted`, `withdrawn`, `revoked`, and `expired`. Requires the master key.

### `PATCH /v1/privacy/consents/{consent_id}`

Updates a consent record. `POST /v1/privacy/consents/{consent_id}/withdraw` marks consent as withdrawn and records a withdrawal timestamp. Requires the master key.

### `POST /v1/privacy/requests`

Creates a durable privacy request record with `user_id`, `request_type`, `source`, `notes`, optional `due_at`, and `metadata`. Supported request types include `access`, `erasure`, `correction`, `restriction`, `objection`, `portability`, `withdrawal`, `grievance`, `nomination`, and `appeal`. Invalid request types return `400`. Requires the master key.

### `PATCH /v1/privacy/requests/{request_id}`

Updates request `status`, `notes`, `due_at`, `completed_at`, or `metadata`. Supported statuses are `open`, `in_progress`, `completed`, `closed`, `denied`, and `canceled`; invalid statuses return `400`. `POST /v1/privacy/requests/{request_id}` is also supported. Requires the master key.

### `GET /v1/privacy/users/{user_id}/export`

Exports user-linked data for access and portability workflows. The response includes the user spend bucket, attached keys, usage events, endpoint access references, auth references tied through JSON `user_id`/`user` fields, virtual keys, or `X-User-ID`, audit target and actor references, tracked privacy requests, consent records, stored preferences, and feedback. Requires the master key.

### `DELETE /v1/privacy/users/{user_id}/erase`

Erases user-linked data from the control plane, user memory, and response cache. User records, attached keys, usage events tied through JSON `user_id`/`user` fields, virtual keys, or `X-User-ID`, preferences, and feedback are deleted; response-cache entries under the gateway cache prefix are cleared; audit target and actor references, endpoint access, auth, and privacy-request references are pseudonymized to preserve security and rights-request evidence without retaining the user's identifier. `POST /v1/privacy/users/{user_id}/erase` is also supported. Requires the master key.

### `POST /v1/config/reload`

Reloads gateway settings from `.env`, environment variables, and the optional structured config file. Requires the master key.

## User Memory

The gateway can store user preferences and feedback so future requests remember them across sessions. User memory supports database, Redis, and in-memory backends. Database URLs can target Postgres, MySQL, MSSQL, or MongoDB.

### `GET /v1/users/{user_id}/preferences`

Returns stored preferences. Requires a valid master key or a virtual key scoped to the same `user_id` when gateway auth is configured.

### `PUT /v1/users/{user_id}/preferences`

Creates or merges preferences. `PATCH` and `POST` on the same path are also supported. Requires a valid master key or a virtual key scoped to the same `user_id` when gateway auth is configured.

```json
{
  "preferences": {
    "tone": "warm",
    "format": "bullets"
  }
}
```

### `POST /v1/users/{user_id}/feedback`

Stores feedback for later analysis. Requires a valid master key or a virtual key scoped to the same `user_id` when gateway auth is configured.

```json
{
  "feedback": {
    "rating": 5,
    "comment": "Great response"
  }
}
```

### `GET /v1/users/{user_id}/feedback`

Returns stored feedback entries. Requires a valid master key or a virtual key scoped to the same `user_id` when gateway auth is configured.

Requests that include `user`, `user_id`, or `X-User-ID` automatically receive remembered preferences as a leading system message.

## Response Headers

All HTTP responses include `x-request-id`. If the request includes `X-Request-ID`, the gateway echoes it; otherwise it generates one.

When `JUSTFASTLLM_CORS_ALLOW_ORIGIN` is configured, responses include CORS headers for browser clients and `OPTIONS` preflight requests. The allowed methods cover `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, and `OPTIONS`. Allowed headers include `Authorization`, `Content-Type`, `X-Request-ID`, `X-User-ID`, `X-Team-ID`, `X-LLM-Provider`, `X-JustFastLLM-Key`, and the configured `JUSTFASTLLM_KEY_HEADER_NAME`.

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
