# Enterprise Guide

Created by [Suhas Bhairav](https://suhasbhairav.com).

This guide describes how to run `justfastllm` as an enterprise LLM gateway with centralized access, usage governance, auditability, cost controls, provider routing, and operator visibility.

## Enterprise Architecture

`justfastllm` separates application traffic from provider credentials and policy logic.

| Layer | Responsibility |
| --- | --- |
| Client applications | Send OpenAI-compatible requests to the gateway. |
| Gateway API | Authenticates requests, applies budgets and route access, resolves providers, records usage, and forwards traffic. |
| Control plane | Manages virtual keys, service-account keys, users, teams, audit events, spend, and rate limits. |
| Policy layer | Enforces provider allow-lists, denied models, required tags, token ceilings, and response blocking. |
| Plugin layer | Runs importable Python request/response hooks for organization-specific transformations. |
| Cache and memory | Redis-backed response cache plus Redis-backed user preferences and feedback. |
| Provider adapters | Direct HTTP adapters for OpenAI-compatible providers and a native messages adapter. |
| Dashboard | Browser UI for usage analytics, pricing, speed, guardrails, keys, users, teams, and audit history. |

## Production Topology

Recommended single-region deployment:

| Component | Recommendation |
| --- | --- |
| Gateway | 1-3 container replicas behind a managed HTTPS load balancer. |
| Redis | Managed Redis with persistence, private networking, and TLS when available. |
| Control-plane snapshot | Single-writer JSON snapshot volume for current file-backed mode. |
| Secrets | Platform secret manager or encrypted environment variables. |
| Dashboard | Internal-only Next.js deployment or private network route. |
| Logs | Centralized log sink with `x-request-id` indexed. |

For multi-replica deployments, use one writer process for file-backed control-plane state. Redis-backed cache and user memory are already shared across replicas. Move control-plane persistence to a shared database before running multi-writer administrative traffic.

## Security Baseline

Required production controls:

- Set `JUSTFASTLLM_MASTER_KEY`.
- Keep provider API keys out of application services.
- Run the dashboard behind SSO, VPN, private networking, or another trusted access layer.
- Restrict admin endpoints to internal networks where possible.
- Enable HTTPS at the edge.
- Set `JUSTFASTLLM_CORS_ALLOW_ORIGIN` only for approved browser origins.
- Keep `JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH` on encrypted storage.
- Back up the control-plane snapshot when file-backed mode is enabled.
- Rotate provider keys and virtual keys on a predictable schedule.
- Use `env:` or `file:` secret references instead of storing raw secrets in config files.

## Access Model

| Credential | Purpose |
| --- | --- |
| Master key | Administrative access to keys, users, teams, metrics, usage, audit logs, and dashboard APIs. |
| Virtual key | Application access with model allow-lists, route allow-lists, budgets, RPM/TPM limits, and metadata. |
| Service-account key | Automation or backend-worker access with the same controls as virtual keys. |

Recommended pattern:

1. Create one team per product, customer, or environment.
2. Create one user bucket per end-user, workspace, or application identity when per-user spend is required.
3. Create a virtual key per application deployment.
4. Use `allowed_routes` to restrict each key to only the endpoint families it needs.
5. Set explicit `budget_usd`, `rpm_limit`, and `tpm_limit` on every production key.

## Endpoint Governance

Route names used by key access policies:

| Route name | Gateway endpoints |
| --- | --- |
| `chat` | `/v1/chat/completions` |
| `messages` | `/v1/messages` |
| `completions` | `/v1/completions` |
| `responses` | `/v1/responses` |
| `embeddings` | `/v1/embeddings` |
| `images` | `/v1/images/generations`, `/v1/images/edits`, `/v1/images/variations` |
| `audio` | `/v1/audio/transcriptions`, `/v1/audio/translations` |
| `moderations` | `/v1/moderations` |
| `rerank` | `/v1/rerank`, `/rerank` |
| `agents` | `/v1/agents/runs` |

## Policies And Plugins

Policies are configured by environment variables or `config.example.yaml` fields:

| Control | Purpose |
| --- | --- |
| `JUSTFASTLLM_POLICY_ALLOWED_PROVIDERS` | Restrict traffic to approved providers. |
| `JUSTFASTLLM_POLICY_DENIED_MODELS` | Block specific model IDs. |
| `JUSTFASTLLM_POLICY_REQUIRED_TAGS` | Require request tags for ownership, environment, or billing. |
| `JUSTFASTLLM_POLICY_MAX_PROMPT_TOKENS` | Reject oversized requests before provider calls. |
| `JUSTFASTLLM_POLICY_RESPONSE_BLOCK_PATTERNS` | Block successful responses containing disallowed content. |

Plugins are importable Python modules listed in `JUSTFASTLLM_PLUGIN_MODULES`. Use them for internal headers, metadata insertion, response shaping, analytics tags, or policy-adjacent transformations.

Production plugin guidance:

- Keep plugin modules in the gateway image.
- Treat plugin code as privileged.
- Add tests for each plugin.
- Roll out plugin changes behind `POST /v1/config/reload` or a normal deployment.
- Log plugin version metadata in request tags where useful.

## Secret Resolution

Provider keys and the master key support direct values, environment references, and file references.

```bash
OPENAI_API_KEY=env:OPENAI_SECRET_NAME
ANTHROPIC_API_KEY=file:/run/secrets/anthropic_api_key
JUSTFASTLLM_MASTER_KEY=env:GATEWAY_MASTER_KEY
```

This supports platform secret managers that inject environment variables or mounted secret files.

## Cost Controls

Use all available layers together:

- Key-level budgets with `budget_usd`.
- User and team budgets with `max_budget`.
- RPM and TPM limits on high-volume keys.
- Model allow-lists to keep expensive models behind explicit approval.
- Model aliases to move applications between models without code changes.
- Redis cache for repeatable non-streaming chat, embedding, completion, and rerank traffic.
- Dashboard spend views for monitoring drift.

## Observability

Core endpoints:

| Endpoint | Use |
| --- | --- |
| `/health` | Health checks and deployment readiness. |
| `/openapi.json` | Contract validation and client generation. |
| `/v1/metrics` | Aggregate requests, tokens, cost, latency, cache, key, user, and team statistics. |
| `/v1/usage` | Recent request-level gateway events. |
| `/v1/audit` | Administrative changes to keys, users, and teams. |
| `/v1/providers/health` | Provider health, error-rate, latency, spend, and throughput status. |
| `/v1/alerts` | Derived operational alerts for provider health and budget risk. |
| `/v1/pricing` | Pricing table used for spend estimates. |
| `/v1/proxy/features` | Machine-readable gateway capability map. |

Log index fields:

- `request_id`
- HTTP method
- path
- elapsed milliseconds
- sanitized headers
- `x-justfastllm-call-id`
- `x-justfastllm-provider`
- `x-justfastllm-cache`
- `x-justfastllm-cost-usd`

## Availability And Resilience

Use fallback providers for provider outages:

```bash
JUSTFASTLLM_FALLBACK_PROVIDERS=anthropic,deepseek
```

Use traffic mirroring to test a secondary provider before migration:

```bash
JUSTFASTLLM_MIRROR_PROVIDER=deepseek
```

Operational guidance:

- Set provider timeouts with `JUSTFASTLLM_REQUEST_TIMEOUT_SECONDS`.
- Use health checks at `/health`.
- Keep Redis in the same region as the gateway.
- Keep dashboard access separate from public application traffic.
- Keep fallback providers configured with compatible request shapes.

## Backup And Restore

Back up file-backed control-plane state:

```bash
cp .justfastllm/proxy-control.json .justfastllm/proxy-control.backup.json
```

Restore:

```bash
cp .justfastllm/proxy-control.backup.json .justfastllm/proxy-control.json
```

Restart the gateway process after restoring a snapshot.

## Incident Runbooks

### Provider Outage

1. Confirm `/health`.
2. Check `/v1/usage` for elevated provider errors.
3. Add or update `JUSTFASTLLM_FALLBACK_PROVIDERS`.
4. Restart or redeploy the gateway if configuration changed.
5. Monitor `/v1/metrics` for recovery.

### Cost Spike

1. Inspect `/v1/metrics` by model and provider.
2. Inspect `/v1/usage` for high-volume keys, users, teams, and tags.
3. Disable or update the offending key through `/v1/keys/update`.
4. Lower key budgets or RPM/TPM limits.
5. Review model access and route access.

### Provider Degradation

1. Inspect `/v1/providers/health`.
2. Inspect `/v1/alerts`.
3. Confirm whether the provider error rate or p95 latency triggered the alert.
4. Enable or adjust fallback providers.
5. Move high-priority applications to a healthier model alias if needed.

### Suspected Key Exposure

1. Delete the virtual key with `/v1/keys`.
2. Create a replacement key with narrower `models` and `allowed_routes`.
3. Inspect `/v1/audit`.
4. Inspect `/v1/usage` for anomalous activity.
5. Rotate related provider credentials if provider keys may also be exposed.

### Guardrail Escalation

1. Inspect `/v1/guardrails`.
2. Add block patterns with `JUSTFASTLLM_BLOCK_PATTERNS`.
3. Disable only the specific check causing false positives when needed.
4. Re-enable the check after prompt or policy fixes are deployed.

## Change Management

Before production deployment:

```bash
PYTHONPATH=src python3 -m unittest
PYTHONPATH=src python3 -m compileall -q src tests benchmarks
cd dashboard
npm run build
npm audit --json
```

Recommended release checklist:

- Review OpenAPI changes.
- Review dashboard build output.
- Review dependency audit.
- Confirm environment variables and secret references.
- Confirm Redis connectivity.
- Confirm control-plane backup path.
- Confirm deployment health check.

## Current Enterprise Limits

- File-backed control-plane persistence is single-writer.
- Shared Redis persistence exists for cache and user memory, not yet for the full control plane.
- Role-based dashboard users should be enforced by the deployment access layer.
- Long-term warehouse exports are not yet built in.
