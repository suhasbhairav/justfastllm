from __future__ import annotations


def openapi_schema() -> dict[str, object]:
    return _with_security({
        "openapi": "3.1.0",
        "info": {
            "title": "justfastllm API",
            "version": "0.1.0",
            "description": "Lightweight LLM gateway for provider-agnostic chat, messages, agents, skills, caching, memory, and guardrails.",
        },
        "paths": {
            "/health": {"get": _operation("Health", "Gateway health and provider registry.")},
            "/openapi.json": {"get": _operation("OpenAPI schema", "Gateway OpenAPI 3.1 contract.")},
            "/v1/models": {"get": _operation("Configured models", "Default model list from configured providers. Requires gateway authentication when configured.")},
            "/v1/providers/{provider}/models": {
                "get": {
                    **_operation("Provider models", "Proxy live model discovery to the selected provider. Requires gateway authentication when configured."),
                    "parameters": [_path_parameter("provider")],
                }
            },
            "/v1/chat/completions": {
                "post": _operation("Chat completions", "OpenAI-compatible chat completion gateway.")
            },
            "/v1/completions": {
                "post": _operation("Text completions", "OpenAI-compatible text completion gateway.")
            },
            "/v1/responses": {
                "post": _operation("Responses", "OpenAI-compatible responses gateway.")
            },
            "/v1/messages": {
                "post": _operation("Messages", "Messages-style gateway. Anthropic is proxied natively.")
            },
            "/v1/embeddings": {
                "post": _operation("Embeddings", "OpenAI-compatible embeddings gateway with optional response caching.")
            },
            "/v1/images/generations": {
                "post": _operation("Image generations", "OpenAI-compatible image generation gateway.")
            },
            "/v1/images/edits": {
                "post": _operation("Image edits", "OpenAI-compatible image edit gateway, including multipart uploads.")
            },
            "/v1/images/variations": {
                "post": _operation("Image variations", "OpenAI-compatible image variation gateway, including multipart uploads.")
            },
            "/v1/audio/transcriptions": {
                "post": _operation("Audio transcriptions", "OpenAI-compatible audio transcription gateway, including multipart uploads.")
            },
            "/v1/audio/translations": {
                "post": _operation("Audio translations", "OpenAI-compatible audio translation gateway, including multipart uploads.")
            },
            "/v1/moderations": {
                "post": _operation("Moderations", "OpenAI-compatible moderation gateway.")
            },
            "/v1/rerank": {
                "post": _operation("Rerank", "OpenAI-compatible rerank gateway with optional response caching.")
            },
            "/rerank": {
                "post": _operation("Rerank alias", "Alternate route for the rerank gateway.")
            },
            "/v1/agents/runs": {
                "post": _operation("Agent runs", "Gateway-managed lightweight agent run with optional skills.")
            },
            "/v1/skills": {"get": _operation("Skills", "List registered gateway skills. Requires master key when configured.")},
            "/v1/skills/{skill_name}/run": {
                "post": {
                    **_operation("Run skill", "Run a registered gateway skill. Requires master key when configured."),
                    "parameters": [_path_parameter("skill_name")],
                }
            },
            "/v1/guardrails": {
                "get": _operation("Guardrails", "Inspect guardrail runtime state. Requires master key when configured."),
                "patch": _operation("Configure guardrails", "Enable or disable guardrails at runtime. Requires master key when configured."),
            },
            "/v1/keys": {
                "get": _operation("List virtual keys", "List proxy virtual keys. Requires master key."),
                "post": _operation("Generate virtual key", "Create a virtual key with model access, budget, RPM, and TPM controls."),
                "delete": _operation("Delete virtual key", "Delete a virtual key by secret. Requires master key."),
            },
            "/v1/keys/info": {
                "get": _operation("Virtual key info", "Inspect virtual key metadata and spend."),
                "post": _operation("Virtual key info", "Inspect virtual key metadata and spend.")
            },
            "/v1/keys/update": {
                "patch": _operation("Update virtual key", "Update key access, budgets, rate limits, aliases, routes, disabled state, or metadata."),
                "post": _operation("Update virtual key", "POST alias for updating key access, budgets, rate limits, aliases, routes, disabled state, or metadata."),
            },
            "/key/generate": {
                "post": _operation("Key generate alias", "Alternate route for virtual key generation.")
            },
            "/key/info": {
                "get": _operation("Key info alias", "Alternate route for virtual key metadata."),
                "post": _operation("Key info alias", "Alternate route for virtual key metadata.")
            },
            "/key/update": {
                "patch": _operation("Key update alias", "Alternate route for virtual key updates."),
                "post": _operation("Key update alias", "Alternate route for virtual key updates.")
            },
            "/key/delete": {
                "delete": _operation("Key delete alias", "Alternate route for virtual key deletion."),
                "post": _operation("Key delete alias", "Alternate route for virtual key deletion.")
            },
            "/v1/proxy/users": {
                "get": _operation("List users", "List proxy users and spend buckets. Requires master key."),
                "post": _operation("Create user", "Create a proxy user with model access and budget metadata. Requires master key."),
                "delete": _operation("Delete user", "Delete a proxy user spend bucket. Requires master key."),
            },
            "/v1/proxy/users/info": {
                "get": _operation("User info", "Inspect user spend and attached keys. Requires master key."),
                "post": _operation("User info", "Inspect user spend and attached keys. Requires master key."),
            },
            "/v1/proxy/users/update": {
                "patch": _operation("Update user", "Update a proxy user for correction or rectification workflows. Requires master key."),
                "post": _operation("Update user", "Update a proxy user for correction or rectification workflows. Requires master key."),
            },
            "/v1/proxy/users/delete": {
                "delete": _operation("Delete user alias", "Alternate route for deleting a proxy user. Requires master key."),
                "post": _operation("Delete user alias", "Alternate route for deleting a proxy user. Requires master key.")
            },
            "/user/new": {
                "post": _operation("Create user alias", "Alternate route for creating a proxy user. Requires master key.")
            },
            "/user/info": {
                "get": _operation("User info alias", "Alternate route for proxy user metadata. Requires master key."),
                "post": _operation("User info alias", "Alternate route for proxy user metadata. Requires master key."),
            },
            "/user/update": {
                "patch": _operation("Update user", "Update a proxy user spend bucket. Requires master key."),
                "post": _operation("Update user", "Update a proxy user spend bucket. Requires master key.")
            },
            "/user/delete": {
                "delete": _operation("Delete user alias", "Alternate route for deleting a proxy user. Requires master key."),
                "post": _operation("Delete user alias", "Alternate route for deleting a proxy user. Requires master key.")
            },
            "/v1/proxy/teams": {
                "get": _operation("List teams", "List proxy teams and spend buckets. Requires master key."),
                "post": _operation("Create team", "Create a proxy team with model access and budget metadata. Requires master key."),
                "delete": _operation("Delete team", "Delete a proxy team spend bucket. Requires master key."),
            },
            "/v1/proxy/teams/info": {
                "get": _operation("Team info", "Inspect team spend and attached keys. Requires master key."),
                "post": _operation("Team info", "Inspect team spend and attached keys. Requires master key."),
            },
            "/v1/proxy/teams/update": {
                "patch": _operation("Update team", "Update a proxy team for correction or rectification workflows. Requires master key."),
                "post": _operation("Update team", "POST alias for updating a proxy team for correction or rectification workflows. Requires master key."),
            },
            "/v1/proxy/teams/delete": {
                "delete": _operation("Delete team alias", "Alternate route for deleting a proxy team. Requires master key."),
                "post": _operation("Delete team alias", "Alternate route for deleting a proxy team. Requires master key.")
            },
            "/team/new": {
                "post": _operation("Create team alias", "Alternate route for creating a proxy team. Requires master key.")
            },
            "/team/info": {
                "get": _operation("Team info alias", "Alternate route for proxy team metadata. Requires master key."),
                "post": _operation("Team info alias", "Alternate route for proxy team metadata. Requires master key."),
            },
            "/team/list": {
                "get": _operation("Team list alias", "Alternate route for listing proxy teams. Requires master key.")
            },
            "/team/update": {
                "patch": _operation("Update team", "Update a proxy team spend bucket. Requires master key."),
                "post": _operation("Update team", "Update a proxy team spend bucket. Requires master key.")
            },
            "/team/delete": {
                "delete": _operation("Delete team alias", "Alternate route for deleting a proxy team. Requires master key."),
                "post": _operation("Delete team alias", "Alternate route for deleting a proxy team. Requires master key.")
            },
            "/v1/service-accounts/keys": {
                "post": _operation("Generate service account key", "Create a service-account key for automation. Requires master key.")
            },
            "/service_account/key/generate": {
                "post": _operation("Generate service account key alias", "Alternate route for creating a service-account key. Requires master key.")
            },
            "/v1/metrics": {"get": _operation("Proxy metrics", "Token, cost, latency, cache, provider, and model rollups.")},
            "/v1/usage": {"get": _operation("Usage events", "Recent request-level usage and spend events.")},
            "/v1/audit": {"get": _operation("Audit log", "Recent administrative control-plane changes.")},
            "/v1/access": {"get": _operation("Access events", "Recent compact endpoint access events with method, path, status, latency, request ID, and auth context.")},
            "/v1/auth/events": {"get": _operation("Authentication events", "Recent sanitized authentication successes, failures, bypasses, reasons, routes, models, and key, user, and team references. Requires master key.")},
            "/v1/providers/health": {"get": _operation("Provider health", "Provider request volume, error rate, latency, tokens, spend, and status.")},
            "/v1/alerts": {"get": _operation("Alerts", "Derived provider health and budget alerts.")},
            "/v1/compliance/status": {"get": _operation("Compliance status", "Runtime privacy, security, retention, and operator-readiness status. Requires master key.")},
            "/v1/compliance/evidence": {"get": _operation("Compliance evidence", "Machine-readable GDPR, SOC 2, DPA, and India DPDP evidence map. Requires master key.")},
            "/v1/compliance/integrity": {"get": _operation("Compliance evidence integrity", "SHA-256 digests, counts, and tamper-evident hash-chain validation for sanitized evidence categories so exported evidence can be matched to release or audit records. Requires master key.")},
            "/v1/compliance/report": {"get": _operation("Compliance report", "Deployment-facing readiness report with automated checks, evidence, and operator-required compliance gates. Requires master key.")},
            "/v1/privacy/consents": {
                "get": _operation("List consents", "List consent and withdrawal records by user, status, or purpose. Requires master key."),
                "post": _operation("Create consent", "Create a durable consent record with user, purpose, lawful basis, notice version, source, status, and metadata. Requires master key."),
            },
            "/v1/privacy/consents/{consent_id}": {
                "patch": {
                    **_operation("Update consent", "Update a durable consent record. Requires master key."),
                    "parameters": [_path_parameter("consent_id")],
                },
                "post": {
                    **_operation("Update consent", "POST alias for updating a durable consent record. Requires master key."),
                    "parameters": [_path_parameter("consent_id")],
                },
            },
            "/v1/privacy/consents/{consent_id}/withdraw": {
                "post": {
                    **_operation("Withdraw consent", "Mark a consent record as withdrawn and set a withdrawal timestamp. Requires master key."),
                    "parameters": [_path_parameter("consent_id")],
                },
            },
            "/v1/privacy/requests": {
                "get": _operation("List privacy requests", "List tracked subject-rights, DPDP, and privacy workflow requests. Requires master key."),
                "post": _operation("Create privacy request", "Create a tracked privacy request with user, type, status, due date, source, notes, and metadata. Requires master key."),
            },
            "/v1/privacy/requests/{request_id}": {
                "patch": {
                    **_operation("Update privacy request", "Update status, notes, due date, completion timestamp, or metadata for a tracked privacy request. Requires master key."),
                    "parameters": [_path_parameter("request_id")],
                },
                "post": {
                    **_operation("Update privacy request", "POST alias for updating a tracked privacy request. Requires master key."),
                    "parameters": [_path_parameter("request_id")],
                },
            },
            "/v1/privacy/users/{user_id}/export": {
                "get": {
                    **_operation("Export user subject data", "Export control-plane, usage, audit, auth, preference, and feedback data for a user. Requires master key."),
                    "parameters": [_path_parameter("user_id")],
                },
            },
            "/v1/privacy/users/{user_id}/erase": {
                "delete": {
                    **_operation("Erase user subject data", "Erase or pseudonymize user-linked control-plane, usage, key, preference, and feedback data. Requires master key."),
                    "parameters": [_path_parameter("user_id")],
                },
                "post": {
                    **_operation("Erase user subject data", "POST alias for erasing user subject data. Requires master key."),
                    "parameters": [_path_parameter("user_id")],
                },
            },
            "/v1/pricing": {"get": _operation("Pricing", "Configured model pricing used for cost estimates. Requires master key when configured.")},
            "/v1/proxy/features": {"get": _operation("Proxy feature inventory", "Feature map for dashboard and roadmap planning. Requires master key when configured.")},
            "/v1/policies": {"get": _operation("Policies", "Runtime request and response policy configuration.")},
            "/v1/plugins": {"get": _operation("Plugins", "Loaded request and response plugin hooks.")},
            "/v1/config/reload": {"post": _operation("Reload config", "Reload gateway settings from environment and config file. Requires master key.")},
            "/v1/users/{user_id}/preferences": {
                "get": {
                    **_operation("Get preferences", "Read stored user preferences."),
                    "parameters": [_path_parameter("user_id")],
                },
                "put": {
                    **_operation("Save preferences", "Create or merge stored user preferences."),
                    "parameters": [_path_parameter("user_id")],
                },
                "patch": {
                    **_operation("Save preferences", "PATCH alias for creating or merging stored user preferences."),
                    "parameters": [_path_parameter("user_id")],
                },
                "post": {
                    **_operation("Save preferences", "POST alias for creating or merging stored user preferences."),
                    "parameters": [_path_parameter("user_id")],
                },
            },
            "/v1/users/{user_id}/feedback": {
                "get": {
                    **_operation("Get feedback", "Read stored user feedback entries."),
                    "parameters": [_path_parameter("user_id")],
                },
                "post": {
                    **_operation("Save feedback", "Store user feedback."),
                    "parameters": [_path_parameter("user_id")],
                },
            },
        },
        "components": {
            "securitySchemes": {
                "MasterKeyAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Administrative master key sent as a Bearer token, or through the configured key header.",
                },
                "GatewayAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Gateway virtual key or master key sent as a Bearer token, or through the configured key header.",
                },
            },
            "schemas": {
                "Error": {
                    "type": "object",
                    "properties": {
                        "error": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "message": {"type": "string"},
                            },
                        }
                    },
                }
            }
        },
    })


def _operation(summary: str, description: str) -> dict[str, object]:
    return {
        "summary": summary,
        "description": description,
        "responses": {
            "200": {"description": "OK"},
            "400": {"description": "Bad request"},
            "401": {"description": "Provider authentication missing"},
            "404": {"description": "Not found"},
            "422": {"description": "Guardrail violation"},
            "502": {"description": "Upstream transport error"},
        },
    }


def _path_parameter(name: str) -> dict[str, object]:
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }


def _with_security(schema: dict[str, object]) -> dict[str, object]:
    paths = schema.get("paths")
    if not isinstance(paths, dict):
        return schema
    admin_paths = {
        "/v1/skills",
        "/v1/skills/{skill_name}/run",
        "/v1/guardrails",
        "/v1/keys",
        "/v1/keys/info",
        "/v1/keys/update",
        "/key/generate",
        "/key/info",
        "/key/update",
        "/key/delete",
        "/v1/proxy/users",
        "/v1/proxy/users/info",
        "/v1/proxy/users/update",
        "/v1/proxy/users/delete",
        "/user/new",
        "/user/info",
        "/user/update",
        "/user/delete",
        "/v1/proxy/teams",
        "/v1/proxy/teams/info",
        "/v1/proxy/teams/update",
        "/v1/proxy/teams/delete",
        "/team/new",
        "/team/info",
        "/team/list",
        "/team/update",
        "/team/delete",
        "/v1/service-accounts/keys",
        "/service_account/key/generate",
        "/v1/metrics",
        "/v1/usage",
        "/v1/audit",
        "/v1/access",
        "/v1/auth/events",
        "/v1/providers/health",
        "/v1/alerts",
        "/v1/compliance/status",
        "/v1/compliance/evidence",
        "/v1/compliance/integrity",
        "/v1/compliance/report",
        "/v1/privacy/consents",
        "/v1/privacy/consents/{consent_id}",
        "/v1/privacy/consents/{consent_id}/withdraw",
        "/v1/privacy/requests",
        "/v1/privacy/requests/{request_id}",
        "/v1/privacy/users/{user_id}/export",
        "/v1/privacy/users/{user_id}/erase",
        "/v1/pricing",
        "/v1/proxy/features",
        "/v1/policies",
        "/v1/plugins",
        "/v1/config/reload",
    }
    gateway_paths = {
        "/v1/models",
        "/v1/providers/{provider}/models",
        "/v1/chat/completions",
        "/v1/completions",
        "/v1/responses",
        "/v1/messages",
        "/v1/embeddings",
        "/v1/images/generations",
        "/v1/images/edits",
        "/v1/images/variations",
        "/v1/audio/transcriptions",
        "/v1/audio/translations",
        "/v1/moderations",
        "/v1/rerank",
        "/rerank",
        "/v1/agents/runs",
    }
    user_memory_paths = {
        "/v1/users/{user_id}/preferences",
        "/v1/users/{user_id}/feedback",
    }
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        if path in admin_paths:
            security = [{"MasterKeyAuth": []}]
        elif path in gateway_paths:
            security = [{"GatewayAuth": []}]
        elif path in user_memory_paths:
            security = [{"MasterKeyAuth": []}, {"GatewayAuth": []}]
        else:
            security = None
        if security is None:
            continue
        for operation in methods.values():
            if isinstance(operation, dict):
                operation["security"] = security
                responses = operation.get("responses")
                if isinstance(responses, dict):
                    responses.setdefault("403", {"description": "Forbidden"})
    return schema
