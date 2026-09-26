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
| Cache and memory | Database or Redis-backed response cache plus database or Redis-backed user preferences and feedback. |
| Provider adapters | Direct HTTP adapters for OpenAI-compatible providers and a native messages adapter. |
| Dashboard | Browser UI for usage analytics, pricing, speed, guardrails, keys, users, teams, and audit history. |
| Compliance controls | Retention, subject export, subject erasure, compliance status, contact metadata, and audit evidence. |

## Production Topology

Recommended single-region deployment:

| Component | Recommendation |
| --- | --- |
| Gateway | One active container replica when using the built-in control-plane state store. Add an external multi-writer database before scaling write traffic horizontally. |
| Control-plane database | Postgres, MySQL, MSSQL, MongoDB, Redis, or atomic JSON file snapshot on operator-managed encrypted storage depending on deployment maturity. |
| Redis | Managed Redis with persistence, private networking, and TLS when available. |
| Control-plane state | Database or Redis-backed state snapshot for keys, users, teams, usage, spend, audit, privacy requests, and erasure evidence. |
| Secrets | Platform secret manager or encrypted environment variables. |
| Dashboard | Internal-only Next.js deployment or private network route. |
| Logs | Centralized log sink with `x-request-id` indexed. |

For compliance-sensitive deployments, run one active gateway replica with the built-in Redis or file snapshot store. The built-in Redis store is durable shared storage, but it is still a whole-state snapshot, not a transactional multi-writer database. Add an external database-backed control plane before scaling write traffic horizontally.

## Security Baseline

Required production controls:

- Set `JUSTFASTLLM_MASTER_KEY`.
- Keep provider API keys out of application services.
- Run the dashboard behind SSO, VPN, private networking, or another trusted access layer.
- Restrict admin endpoints to internal networks where possible.
- Enable HTTPS at the edge.
- Set `JUSTFASTLLM_CORS_ALLOW_ORIGIN` only for approved browser origins.
- Use database-backed control-plane storage in managed production deployments when available.
- Supported database URLs include Postgres, MySQL, MSSQL, and MongoDB.
- Keep `JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH` on encrypted storage when file-backed mode is enabled.
- Back up the control-plane state according to the published retention and evidence policy.
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
- Redis or database-backed cache for repeatable non-streaming chat, embedding, completion, and rerank traffic when policy allows response storage.
- Dashboard spend views for monitoring drift.

## Observability

Core endpoints:

| Endpoint | Use |
| --- | --- |
| `/health` | Health checks and deployment readiness. |
| `/openapi.json` | Contract validation and client generation. |
| `/v1/metrics` | Aggregate requests, tokens, cost, latency, cache, key, user, and team statistics. |
| `/v1/usage` | Recent request-level gateway events. |
| `/v1/audit` | Administrative changes to keys, users, teams, privacy requests, and erasure workflows. |
| `/v1/access` | Compact endpoint access evidence across gateway routes without headers or request bodies. |
| `/v1/auth/events` | Sanitized authentication success, failure, and bypass evidence without raw tokens or headers. |
| `/v1/providers/health` | Provider health, error-rate, latency, spend, and throughput status. |
| `/v1/alerts` | Derived operational alerts for provider health and budget risk. |
| `/v1/compliance/status` | Compliance-readiness controls, retention windows, contact metadata, and required operator tasks. |
| `/v1/compliance/evidence` | Machine-readable control evidence map for privacy, security, SOC 2 readiness, DPA, and India DPDP readiness. |
| `/v1/compliance/integrity` | Counts and SHA-256 digests for sanitized evidence categories to match release and audit exports. |
| `/v1/compliance/report` | Deployment-facing readiness report with pass/fail checks, evidence endpoints, and operator-required legal/audit gates. |
| `/v1/privacy/users/{user_id}/export` | User-linked data export for access and portability workflows. |
| `/v1/privacy/users/{user_id}/erase` | User-linked erasure and audit pseudonymization workflow. |
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
4. Inspect `/v1/auth/events` and `/v1/usage` for anomalous authentication and traffic activity.
5. Rotate related provider credentials if provider keys may also be exposed.

### Privacy Rights Request

1. Verify the requester's authority through your identity workflow.
2. Export gateway-held records with `/v1/privacy/users/{user_id}/export`.
3. Review connected application, provider, log, backup, and analytics systems for matching records.
4. Erase gateway-held records with `/v1/privacy/users/{user_id}/erase` when erasure is valid.
5. Retain the pseudonymized audit trail for security evidence.

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
- Confirm `/v1/compliance/status` shows the expected contacts, retention windows, and controls.
- Export `/v1/compliance/report` and `/v1/compliance/integrity` after every production deployment and retain them with release evidence.

## Compliance Readiness

See [Compliance Readiness](COMPLIANCE.md) for GDPR, SOC 2, and India DPDP readiness mappings.

`justfastllm` automates gateway-level technical controls such as retention, export, erasure, audit, auth, redaction, policies, and guardrails. Legal compliance and SOC 2 attestation still require the deploying organization to maintain privacy notices, contracts, subprocessors, incident processes, access reviews, cloud controls, and independent audit evidence.

Use [Data Processing Addendum Template](DPA_TEMPLATE.md) as a counsel-reviewed starting point for customer processing terms.

## Current Enterprise Limits

- Built-in control-plane persistence is single-writer. Use Redis-backed state for managed durability, and add an external database-backed control plane before running multiple active writer replicas.
- Role-based dashboard users should be enforced by the deployment access layer.
- Long-term warehouse exports are not yet built in.
- Legal compliance and SOC 2 reports are not created automatically; the gateway supplies technical controls and evidence surfaces.
