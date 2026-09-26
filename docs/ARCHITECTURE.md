# Architecture

`justfastllm` is organized around narrow contracts:

- `GatewayASGI` is the centralized HTTP entry point.
- `InMemoryProxyControlPlane` owns virtual keys, users, teams, service-account keys, budgets, rate limits, request events, auth events, metrics, pricing estimates, privacy request records, and database, Redis, or JSON snapshot persistence.
- `ProviderFactory` owns provider creation and routing.
- `ProviderClient` is the common provider interface.
- `OpenAICompatibleProvider` handles providers with OpenAI-compatible chat completions.
- `AnthropicProvider` adapts OpenAI-style gateway requests to Anthropic's Messages API.
- `AgentRunner` prepares lightweight agent runs and appends skill results before provider execution.
- `SkillRegistry` owns built-in and user-registered skills.
- `UserMemoryStore` persists user preferences and feedback with database, Redis, or in-memory backends.
- `RedisResponseCache` provides shared response caching, with `MemoryResponseCache` available for tests and single-process development.
- `Guardrails` validates and rejects requests before they leave the gateway, with global and per-check toggles.
- `PooledHttpClient` reuses stdlib HTTP connections per upstream origin.

## Request Flow

1. Client calls `POST /v1/chat/completions`.
2. Gateway parses the provider from `provider`, `X-LLM-Provider`, model prefix, or default config.
3. Virtual-key authentication is enforced when `JUSTFASTLLM_MASTER_KEY` is configured.
4. Key model aliases are applied when present.
5. Stored user preferences are injected for requests carrying `user`, `user_id`, or `X-User-ID`.
6. Guardrails inspect request shape, message length, and configured block patterns.
7. Cache key is computed for non-streaming requests and checked in the configured cache backend.
8. Provider is resolved through `ProviderFactory`.
9. Provider creates a direct HTTP request using the configured API key and base URL.
10. Usage, spend, latency, cache status, key, user, team, endpoint access, and sanitized auth decision data are recorded.
11. Gateway returns upstream JSON or Anthropic-normalized OpenAI-style JSON with a stable `x-request-id`.

Agent runs follow the same provider path after `AgentRunner` converts `instructions`, `input`, existing `messages`, and requested skill calls into a provider-ready message list.

The same control-plane path is used by OpenAI-compatible routes for text completions, responses, embeddings, image generation/editing/variations, audio transcription/translation, moderation, and rerank. JSON requests are inspected for provider/model selection and key aliases. Multipart image/audio requests are forwarded as raw bytes and should select providers with `X-LLM-Provider` or the default provider setting.

## Design Patterns

### Factory Pattern

Provider construction lives in `ProviderFactory`. Built-in providers and custom OpenAI-compatible providers are registered from configuration, so the gateway routing layer does not need provider-specific conditionals.

### Adapter Pattern

Anthropic's Messages API differs from OpenAI-compatible chat completions. `AnthropicProvider` adapts request and response shapes while keeping the gateway interface stable.

### Dependency Injection

The app accepts injected settings, factory, cache, guardrails, and logger instances. Tests can run entirely with fake providers and fake transports.

## Performance Choices

- Raw ASGI avoids framework object graphs.
- Provider responses are cached as bytes to avoid repeated JSON serialization.
- Request bodies are size-limited before parsing.
- The default HTTP transport uses a small stdlib connection pool to avoid SDK and heavyweight runtime dependencies while reducing repeated TLS/TCP setup.
- Upstream socket and protocol failures are normalized to `502 upstream_transport_error`.
- Production deployments can run with `uvicorn[standard]` for faster event loop and HTTP parsing.

## Current Limits

- The proxy control plane can persist to Postgres, MySQL, MSSQL, MongoDB, or Redis for production evidence storage, with JSON snapshot mode available for single-node deployments.
- Streaming requests are proxied as upstream bytes, but the first implementation does not chunk-token stream through ASGI yet.
- Redis caching is implemented with a small RESP client to avoid adding a Redis SDK to the runtime footprint.
- The default HTTP transport favors minimal dependencies and persistent upstream connections behind the `HttpClient` protocol.
