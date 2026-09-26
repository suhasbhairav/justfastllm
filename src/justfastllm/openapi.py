from __future__ import annotations


def openapi_schema() -> dict[str, object]:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "justfastllm API",
            "version": "0.1.0",
            "description": "Lightweight LLM gateway for provider-agnostic chat, messages, agents, skills, caching, memory, and guardrails.",
        },
        "paths": {
            "/health": {"get": _operation("Health", "Gateway health and provider registry.")},
            "/v1/models": {"get": _operation("Configured models", "Default model list from configured providers.")},
            "/v1/providers/{provider}/models": {
                "get": {
                    **_operation("Provider models", "Proxy live model discovery to the selected provider."),
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
            "/v1/skills": {"get": _operation("Skills", "List registered gateway skills.")},
            "/v1/skills/{skill_name}/run": {
                "post": {
                    **_operation("Run skill", "Run a registered gateway skill."),
                    "parameters": [_path_parameter("skill_name")],
                }
            },
            "/v1/guardrails": {
                "get": _operation("Guardrails", "Inspect guardrail runtime state."),
                "patch": _operation("Configure guardrails", "Enable or disable guardrails at runtime."),
            },
            "/v1/keys": {
                "get": _operation("List virtual keys", "List proxy virtual keys. Requires master key."),
                "post": _operation("Generate virtual key", "Create a virtual key with model access, budget, RPM, and TPM controls."),
                "delete": _operation("Delete virtual key", "Delete a virtual key by secret. Requires master key."),
            },
            "/v1/keys/info": {
                "post": _operation("Virtual key info", "Inspect virtual key metadata and spend.")
            },
            "/v1/keys/update": {
                "patch": _operation("Update virtual key", "Update key access, budgets, rate limits, aliases, routes, disabled state, or metadata.")
            },
            "/key/generate": {
                "post": _operation("Key generate alias", "Alternate route for virtual key generation.")
            },
            "/key/info": {
                "get": _operation("Key info alias", "Alternate route for virtual key metadata."),
                "post": _operation("Key info alias", "Alternate route for virtual key metadata.")
            },
            "/key/update": {
                "post": _operation("Key update alias", "Alternate route for virtual key updates.")
            },
            "/key/delete": {
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
            "/user/update": {
                "post": _operation("Update user", "Update a proxy user spend bucket. Requires master key.")
            },
            "/user/delete": {
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
            "/team/update": {
                "post": _operation("Update team", "Update a proxy team spend bucket. Requires master key.")
            },
            "/team/delete": {
                "post": _operation("Delete team alias", "Alternate route for deleting a proxy team. Requires master key.")
            },
            "/v1/service-accounts/keys": {
                "post": _operation("Generate service account key", "Create a service-account key for automation. Requires master key.")
            },
            "/v1/metrics": {"get": _operation("Proxy metrics", "Token, cost, latency, cache, provider, and model rollups.")},
            "/v1/usage": {"get": _operation("Usage events", "Recent request-level usage and spend events.")},
            "/v1/audit": {"get": _operation("Audit log", "Recent administrative control-plane changes.")},
            "/v1/providers/health": {"get": _operation("Provider health", "Provider request volume, error rate, latency, tokens, spend, and status.")},
            "/v1/alerts": {"get": _operation("Alerts", "Derived provider health and budget alerts.")},
            "/v1/pricing": {"get": _operation("Pricing", "Configured model pricing used for cost estimates.")},
            "/v1/proxy/features": {"get": _operation("Proxy feature inventory", "Feature map for dashboard and roadmap planning.")},
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
    }


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
