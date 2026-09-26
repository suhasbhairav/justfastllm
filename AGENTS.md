# justfastllm Agent Guide

This file is the project-level operating guide for human and AI agents working on `justfastllm`.

## Product Standard

`justfastllm` is a production LLM gateway and control plane. Every feature must be real, documented, tested, and wired end to end across the API surface, configuration, OpenAPI contract, dashboard where relevant, and deployment docs.

The repository must not contain references to competing gateway products in source code, documentation, screenshots, tests, examples, comments, package metadata, or generated assets.

## Architecture

The backend is a Python ASGI gateway under `src/justfastllm`.

- `app.py` owns HTTP routing, request lifecycle, admin APIs, provider routes, control-plane APIs, CORS, policy execution, plugin execution, caching, metrics recording, and config reload.
- `config.py` owns `.env`, environment variables, YAML/JSON config files, provider config, secret references, policy config, plugin config, and deployment-facing settings.
- `providers/` owns provider adapters. `base.py` defines the provider contract, `openai_compatible.py` handles generic OpenAI-compatible provider traffic, and provider-specific adapters only add protocol-specific behavior.
- `proxy_control.py` owns virtual keys, users, teams, service-account keys, budgets, RPM/TPM limits, spend, pricing, audit, provider health, alerts, persistence, and usage records.
- `policies.py` owns request and response policy enforcement.
- `plugins.py` owns importable request and response hooks.
- `guardrails.py`, `memory.py`, `agents.py`, and `skills.py` own their named runtime capabilities.
- `openapi.py` owns the public API contract. Any endpoint addition or contract change must update this file.

The dashboard is a Next.js JavaScript App Router app under `dashboard`.

- `app/` owns App Router entry points and global styling.
- `components/DashboardClient.js` owns gateway configuration, admin actions, analytics loading, and dashboard rendering.
- Dashboard features must call real backend endpoints. Do not add mocked panels, placeholder workflows, or static-only controls for shipped features.

Documentation lives in `README.md` and `docs/`.

- `README.md` is the executive product page and must stay deployment-focused.
- `docs/API.md` documents public and admin endpoints.
- `docs/CONFIGURATION.md` documents environment variables and config files.
- `docs/OPERATIONS.md` documents production operations.
- `docs/ARCHITECTURE.md` documents system design.
- `docs/ENTERPRISE.md` documents enterprise controls.
- `docs/PROXY_CONTROL_PLANE.md` documents keys, users, teams, budgets, metrics, audit, and admin workflows.

## Endpoint Feature Rules

Every endpoint feature must be implemented end to end:

- Backend route in `src/justfastllm/app.py`.
- Domain logic in the correct module instead of inline ad hoc behavior.
- Authentication and authorization behavior when the route is administrative or key-scoped.
- CORS preflight support when the route is called by the dashboard.
- OpenAPI path/schema in `src/justfastllm/openapi.py`.
- Unit or integration tests in `tests/test_gateway.py` or a more specific test file.
- Documentation in `docs/API.md` and, when operationally relevant, `docs/OPERATIONS.md` or `docs/CONFIGURATION.md`.
- Dashboard wiring for user-facing analytics or control-plane features.

Supported gateway feature areas include chat, messages, completions, responses, embeddings, images, audio, moderations, rerank, agents, skills, virtual keys, service-account keys, users, teams, model access, aliases, budgets, RPM/TPM limits, caching, memory, guardrails, policies, plugin hooks, fallbacks, traffic mirroring, pricing, usage, spend, audit, provider health, alerts, config reload, OpenAPI, and deployment descriptors.

## Mandatory E2E Expectations

Every new or changed endpoint feature must have tests that prove the full behavior, not just route existence.

Minimum required coverage:

- Success path with realistic request and response bodies.
- Authentication failure for protected routes.
- Authorization or access-control failure when applicable.
- Validation failure for malformed or policy-violating input.
- Metrics, spend, audit, cache, or health side effects when applicable.
- Dashboard CORS preflight coverage for dashboard-called routes.
- Config-file or environment-variable coverage for configurable behavior.
- OpenAPI contract coverage for newly introduced public paths.

Provider-facing features should use deterministic fake HTTP clients in tests. Do not call paid or external provider APIs from default tests.

## Dashboard Rules

Dashboard work must be functional against the live gateway APIs.

- Use JavaScript and App Router.
- Keep controls connected to real endpoints.
- Show real loading, error, and empty states.
- Use admin authentication for protected resources.
- Keep analytics grounded in `/v1/metrics`, `/v1/usage`, `/v1/pricing`, `/v1/providers/health`, `/v1/alerts`, and related control-plane endpoints.
- Capture README screenshots with Playwright after meaningful visual changes.

## Verification Commands

Run these before handing off meaningful backend, dashboard, or documentation changes:

```bash
PYTHONPATH=src python3 -m unittest
PYTHONPATH=src python3 -m compileall -q src tests benchmarks
cd dashboard && npm run build
cd dashboard && npm audit --json
```

When screenshots are part of the change, run the local gateway and dashboard, seed realistic control-plane data through the public admin APIs, and use Playwright to update files under `docs/assets/`.

Also run the repository hygiene scan for forbidden competitor references, placeholder chip headings, and multi-author wording before handoff.

## Deployment Standard

Deployment must stay simple and production-ready.

- Keep Docker, Docker Compose, Render, Railway, AWS App Runner, and GCP Cloud Run paths documented.
- Keep `.env.example` and `config.example.yaml` synchronized with runtime settings.
- Prefer environment variables and config files over code changes for deployment behavior.
- Never commit secrets, provider keys, local control-plane state, `.venv`, `.next`, caches, or generated dependency folders.

## Change Discipline

Keep changes cohesive and easy to review.

- Match existing code style.
- Prefer small domain helpers over large route-local logic.
- Update tests and docs with the feature.
- Avoid unrelated refactors.
- Do not weaken auth, budgets, rate limits, guardrails, policies, or audit behavior to make a test pass.
