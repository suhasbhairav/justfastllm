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
            "/v1/messages": {
                "post": _operation("Messages", "Messages-style gateway. Anthropic is proxied natively.")
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

