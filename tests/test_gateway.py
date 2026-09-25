from __future__ import annotations

import logging
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from justfastllm.agents import AgentRunner
from justfastllm.app import create_app
from justfastllm.cache import MemoryResponseCache, RedisResponseCache, _command, cache_key
from justfastllm.config import load_settings
from justfastllm.errors import UpstreamTransportError
from justfastllm.guardrails import Guardrails
from justfastllm.http import HttpClient, PooledHttpClient, split_url
from justfastllm.jsonutil import dumps_bytes, loads_bytes
from justfastllm.memory import MemoryUserMemoryStore, RedisUserMemoryStore
from justfastllm.models import HttpRequest, UpstreamResponse
from justfastllm.providers.anthropic import AnthropicProvider
from justfastllm.providers.factory import ProviderFactory
from justfastllm.providers.openai_compatible import OpenAICompatibleProvider
from justfastllm.skills import SkillRegistry


class FakeHttpClient(HttpClient):
    def __init__(self, response: UpstreamResponse | None = None) -> None:
        self.requests: list[HttpRequest] = []
        self.response = response or UpstreamResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=dumps_bytes(
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                }
            ),
        )

    def send(self, request: HttpRequest) -> UpstreamResponse:
        self.requests.append(request)
        return self.response


class FailingHttpClient(HttpClient):
    def send(self, request: HttpRequest) -> UpstreamResponse:
        raise UpstreamTransportError("connection failed")


class FakeRedisConnection:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.sent = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)

    def recv(self, count: int) -> bytes:
        chunk = self.response[:count]
        self.response = self.response[count:]
        return chunk

    def close(self) -> None:
        return


def settings(**overrides: str):
    env = {
        "JUSTFASTLLM_CACHE_BACKEND": "memory",
        "OPENAI_API_KEY": "openai-key",
        "ANTHROPIC_API_KEY": "anthropic-key",
        "DEEPSEEK_API_KEY": "deepseek-key",
        "XAI_API_KEY": "xai-key",
        "QWEN_API_KEY": "qwen-key",
        "KIMI_API_KEY": "kimi-key",
        **overrides,
    }
    return load_settings(env_file=Path("/tmp/justfastllm-missing.env"), environ=env)


async def asgi_request(
    app,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
):
    body = dumps_bytes(payload) if payload is not None else b""
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[dict[str, Any]] = []
    raw_headers = [(b"content-type", b"application/json")]
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": raw_headers,
        },
        receive,
        send,
    )
    status = sent[0]["status"]
    headers = {key.decode(): value.decode() for key, value in sent[0].get("headers", [])}
    response_body = sent[1].get("body", b"")
    return status, headers, response_body


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_provider_uses_bearer_auth(self) -> None:
        cfg = settings().providers["openai"]
        http = FakeHttpClient()
        provider = OpenAICompatibleProvider(cfg, http)

        await provider.chat_completion({"messages": [{"role": "user", "content": "hi"}]})

        request = http.requests[0]
        self.assertEqual(request.url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer openai-key")
        self.assertEqual(loads_bytes(request.body)["model"], "gpt-5-nano")

    async def test_openai_model_alias_is_supported(self) -> None:
        cfg = settings(OPENAI_MODEL="gpt-5-nano").providers["openai"]
        http = FakeHttpClient()
        provider = OpenAICompatibleProvider(cfg, http)

        await provider.chat_completion({"messages": [{"role": "user", "content": "hi"}]})

        self.assertEqual(loads_bytes(http.requests[0].body)["model"], "gpt-5-nano")

    async def test_ollama_provider_does_not_require_auth(self) -> None:
        cfg = settings().providers["ollama"]
        http = FakeHttpClient()
        provider = OpenAICompatibleProvider(cfg, http)

        await provider.chat_completion({"messages": [{"role": "user", "content": "hi"}]})

        request = http.requests[0]
        self.assertEqual(request.url, "http://localhost:11434/v1/chat/completions")
        self.assertNotIn("Authorization", request.headers)

    async def test_openai_compatible_provider_lists_models(self) -> None:
        cfg = settings().providers["openai"]
        http = FakeHttpClient()
        provider = OpenAICompatibleProvider(cfg, http)

        await provider.models()

        request = http.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.url, "https://api.openai.com/v1/models")
        self.assertEqual(request.body, b"")
        self.assertEqual(request.headers["Authorization"], "Bearer openai-key")

    async def test_anthropic_chat_normalizes_to_openai_shape(self) -> None:
        response = UpstreamResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=dumps_bytes(
                {
                    "id": "msg_123",
                    "model": "claude-test",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "hello"}],
                    "usage": {"input_tokens": 3, "output_tokens": 2},
                }
            ),
        )
        http = FakeHttpClient(response)
        provider = AnthropicProvider(settings().providers["anthropic"], http)

        result = await provider.chat_completion(
            {
                "messages": [
                    {"role": "system", "content": "be brief"},
                    {"role": "user", "content": "hi"},
                ]
            }
        )

        upstream_body = loads_bytes(http.requests[0].body)
        normalized = loads_bytes(result.body)
        self.assertEqual(http.requests[0].url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(http.requests[0].headers["x-api-key"], "anthropic-key")
        self.assertEqual(upstream_body["system"], "be brief")
        self.assertEqual(normalized["object"], "chat.completion")
        self.assertEqual(normalized["choices"][0]["message"]["content"], "hello")
        self.assertEqual(normalized["usage"]["total_tokens"], 5)

    async def test_anthropic_provider_lists_models(self) -> None:
        http = FakeHttpClient()
        provider = AnthropicProvider(settings().providers["anthropic"], http)

        await provider.models()

        request = http.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.url, "https://api.anthropic.com/v1/models")
        self.assertEqual(request.headers["x-api-key"], "anthropic-key")


class FactoryAndCacheTests(unittest.TestCase):
    def test_factory_uses_pooled_http_client_by_default(self) -> None:
        factory = ProviderFactory(settings())

        self.assertIsInstance(factory.http_client, PooledHttpClient)

    def test_factory_routes_by_model_prefix(self) -> None:
        factory = ProviderFactory(settings())

        provider, payload = factory.resolve(
            {"model": "qwen/qwen-max", "messages": [{"role": "user", "content": "hi"}]},
            {},
        )

        self.assertEqual(provider, "qwen")
        self.assertEqual(payload["model"], "qwen-max")

    def test_factory_registers_custom_openai_compatible_provider(self) -> None:
        resolved = settings(
            JUSTFASTLLM_OPENAI_COMPATIBLE_PROVIDERS="openrouter, local-ai",
            OPENROUTER_BASE_URL="https://openrouter.ai/api/v1",
            OPENROUTER_API_KEY="openrouter-key",
            OPENROUTER_DEFAULT_MODEL="openai/gpt-5-nano",
            LOCAL_AI_BASE_URL="http://localhost:8080/v1",
            LOCAL_AI_REQUIRES_API_KEY="false",
        )
        factory = ProviderFactory(resolved)

        provider, payload = factory.resolve(
            {"model": "local-ai/tinyllama", "messages": [{"role": "user", "content": "hi"}]},
            {},
        )

        self.assertIn("openrouter", factory.names())
        self.assertIn("local-ai", factory.names())
        self.assertEqual(provider, "local-ai")
        self.assertEqual(payload["model"], "tinyllama")
        self.assertFalse(resolved.providers["local-ai"].requires_api_key)

    def test_cache_key_ignores_stream_and_metadata(self) -> None:
        left = cache_key("openai", {"model": "x", "messages": [], "stream": False})
        right = cache_key("openai", {"model": "x", "messages": [], "stream": True, "metadata": {"trace": "1"}})
        self.assertEqual(left, right)

    def test_redis_command_encoder(self) -> None:
        self.assertEqual(_command("GET", "key"), b"*2\r\n$3\r\nGET\r\n$3\r\nkey\r\n")

    def test_redis_cache_uses_resp_get_and_setex(self) -> None:
        connections: list[FakeRedisConnection] = []
        responses = [
            b"+OK\r\n+OK\r\n+OK\r\n",
            b"+OK\r\n+OK\r\n$5\r\nvalue\r\n",
        ]

        def connect(address, timeout):
            connection = FakeRedisConnection(responses.pop(0))
            connections.append(connection)
            return connection

        cache = RedisResponseCache(
            url="redis://:secret@localhost:6379/2",
            ttl_seconds=9,
            key_prefix="jfl:",
        )
        with patch("justfastllm.cache.socket.create_connection", side_effect=connect):
            cache.set("abc", b"value")
            value = cache.get("abc")

        self.assertEqual(value, b"value")
        set_command = bytes(connections[0].sent)
        get_command = bytes(connections[1].sent)
        self.assertIn(b"AUTH", set_command)
        self.assertIn(b"SELECT", set_command)
        self.assertIn(b"SETEX", set_command)
        self.assertIn(b"jfl:abc", set_command)
        self.assertIn(b"GET", get_command)
        self.assertIn(b"jfl:abc", get_command)

    def test_redis_user_memory_store_persists_preferences(self) -> None:
        stored = dumps_bytes({"tone": "warm"})
        connections: list[FakeRedisConnection] = []
        responses = [
            b"$-1\r\n",
            b"+OK\r\n",
            f"${len(stored)}\r\n".encode("ascii") + stored + b"\r\n",
        ]

        def connect(address, timeout):
            connection = FakeRedisConnection(responses.pop(0))
            connections.append(connection)
            return connection

        store = RedisUserMemoryStore(
            url="redis://localhost:6379/0",
            key_prefix="jfl:memory:",
        )
        with patch("justfastllm.memory.socket.create_connection", side_effect=connect):
            saved = store.save_preferences("user-1", {"tone": "warm"})
            loaded = store.get_preferences("user-1")

        self.assertEqual(saved, {"tone": "warm"})
        self.assertEqual(loaded, {"tone": "warm"})
        self.assertIn(b"SET", bytes(connections[1].sent))
        self.assertIn(b"jfl:memory:preferences:user-1", bytes(connections[1].sent))

    def test_split_url_preserves_query_and_default_port(self) -> None:
        origin, target = split_url("https://api.example.test/v1/chat/completions?trace=1", 30)

        self.assertEqual(origin.scheme, "https")
        self.assertEqual(origin.host, "api.example.test")
        self.assertEqual(origin.port, 443)
        self.assertEqual(target, "/v1/chat/completions?trace=1")

    def test_split_url_rejects_unsupported_scheme_as_upstream_error(self) -> None:
        with self.assertRaises(UpstreamTransportError):
            split_url("ftp://api.example.test/chat", 30)


class AgentRunnerTests(unittest.TestCase):
    def test_agent_runner_builds_provider_payload_with_skill_context(self) -> None:
        runner = AgentRunner(SkillRegistry())

        payload = runner.build_provider_payload(
            {
                "provider": "openai",
                "instructions": "Be brief.",
                "input": "What time is it?",
                "skills": [{"name": "echo", "arguments": {"value": "now"}}],
            }
        )

        self.assertNotIn("input", payload)
        self.assertNotIn("instructions", payload)
        self.assertNotIn("skills", payload)
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertTrue(any("Skill echo result" in message["content"] for message in payload["messages"]))


class GatewayAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_app_defaults_to_redis_cache_backend(self) -> None:
        resolved_settings = load_settings(
            env_file=Path("/tmp/justfastllm-missing.env"),
            environ={"OPENAI_API_KEY": "openai-key"},
        )

        app = create_app(settings=resolved_settings, logger=logging.getLogger("test"))

        self.assertIsInstance(app.cache, RedisResponseCache)

    async def test_health_includes_request_id_and_cors_headers(self) -> None:
        app = create_app(
            settings=settings(JUSTFASTLLM_CORS_ALLOW_ORIGIN="https://app.example.test"),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )

        status, headers, body = await asgi_request(
            app,
            "GET",
            "/health",
            headers={"x-request-id": "req-123"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-request-id"], "req-123")
        self.assertEqual(headers["access-control-allow-origin"], "https://app.example.test")
        self.assertIn("openai", loads_bytes(body)["providers"])

    async def test_openapi_json_includes_core_gateway_paths(self) -> None:
        app = create_app(
            settings=settings(),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )

        status, headers, body = await asgi_request(app, "GET", "/openapi.json")

        payload = loads_bytes(body)
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(payload["openapi"], "3.1.0")
        self.assertIn("/v1/chat/completions", payload["paths"])
        self.assertIn("/v1/guardrails", payload["paths"])
        self.assertIn("/v1/users/{user_id}/preferences", payload["paths"])

    async def test_provider_models_route_proxies_provider_models(self) -> None:
        http = FakeHttpClient(
            UpstreamResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                body=dumps_bytes({"object": "list", "data": [{"id": "model-a"}]}),
            )
        )
        resolved_settings = settings()
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )

        status, headers, body = await asgi_request(app, "GET", "/v1/providers/openai/models")

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-justfastllm-provider"], "openai")
        self.assertEqual(loads_bytes(body)["data"][0]["id"], "model-a")
        self.assertEqual(http.requests[0].url, "https://api.openai.com/v1/models")

    async def test_options_preflight_includes_cors_headers(self) -> None:
        app = create_app(
            settings=settings(JUSTFASTLLM_CORS_ALLOW_ORIGIN="*"),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )

        status, headers, body = await asgi_request(app, "OPTIONS", "/v1/chat/completions")

        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertEqual(headers["access-control-allow-origin"], "*")
        self.assertIn("x-request-id", headers)

    async def test_chat_completion_is_cached(self) -> None:
        http = FakeHttpClient()
        resolved_settings = settings()
        factory = ProviderFactory(resolved_settings, http)
        app = create_app(
            settings=resolved_settings,
            provider_factory=factory,
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )
        payload = {"provider": "openai", "messages": [{"role": "user", "content": "hi"}]}

        first = await asgi_request(app, "POST", "/v1/chat/completions", payload)
        second = await asgi_request(app, "POST", "/v1/chat/completions", payload)

        self.assertEqual(first[0], 200)
        self.assertEqual(first[1]["x-justfastllm-cache"], "miss")
        self.assertEqual(second[1]["x-justfastllm-cache"], "hit")
        self.assertEqual(len(http.requests), 1)

    async def test_user_preferences_are_stored_and_injected_into_provider_messages(self) -> None:
        http = FakeHttpClient()
        resolved_settings = settings()
        memory_store = MemoryUserMemoryStore()
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            memory_store=memory_store,
            logger=logging.getLogger("test"),
        )

        save_status, _, save_body = await asgi_request(
            app,
            "PUT",
            "/v1/users/user-1/preferences",
            {"preferences": {"tone": "warm", "format": "bullets"}},
        )
        chat_status, _, _ = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "user": "user-1", "messages": [{"role": "user", "content": "hi"}]},
        )

        request_body = loads_bytes(http.requests[0].body)
        self.assertEqual(save_status, 200)
        self.assertEqual(loads_bytes(save_body)["preferences"]["tone"], "warm")
        self.assertEqual(chat_status, 200)
        self.assertEqual(request_body["messages"][0]["role"], "system")
        self.assertIn("tone: warm", request_body["messages"][0]["content"])

    async def test_feedback_endpoint_stores_and_returns_feedback(self) -> None:
        app = create_app(
            settings=settings(),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            memory_store=MemoryUserMemoryStore(),
            logger=logging.getLogger("test"),
        )

        post_status, _, post_body = await asgi_request(
            app,
            "POST",
            "/v1/users/user-1/feedback",
            {"feedback": {"rating": 5, "comment": "great"}},
        )
        get_status, _, get_body = await asgi_request(app, "GET", "/v1/users/user-1/feedback")

        self.assertEqual(post_status, 201)
        self.assertEqual(loads_bytes(post_body)["feedback"]["rating"], 5)
        self.assertEqual(get_status, 200)
        self.assertEqual(loads_bytes(get_body)["feedback"][0]["comment"], "great")

    async def test_upstream_transport_error_returns_502(self) -> None:
        resolved_settings = settings()
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, FailingHttpClient()),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )

        status, _, body = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "messages": [{"role": "user", "content": "hi"}]},
        )

        payload = loads_bytes(body)
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"], "upstream_transport_error")

    async def test_skills_endpoint_runs_redaction(self) -> None:
        app = create_app(settings=settings(), cache=MemoryResponseCache(max_items=10, ttl_seconds=60), logger=logging.getLogger("test"))

        status, _, body = await asgi_request(
            app,
            "POST",
            "/v1/skills/redact/run",
            {"arguments": {"text": "Email me at test@example.com with Bearer secret"}},
        )

        self.assertEqual(status, 200)
        self.assertIn("[redacted-email]", loads_bytes(body)["result"])
        self.assertIn("Bearer [redacted]", loads_bytes(body)["result"])

    async def test_messages_endpoint_uses_provider_messages_method(self) -> None:
        http = FakeHttpClient()
        resolved_settings = settings()
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )

        status, headers, _ = await asgi_request(
            app,
            "POST",
            "/v1/messages",
            {"provider": "anthropic", "messages": [{"role": "user", "content": "hi"}]},
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-justfastllm-provider"], "anthropic")
        self.assertEqual(http.requests[0].url, "https://api.anthropic.com/v1/messages")

    async def test_agent_run_calls_provider_with_skill_context(self) -> None:
        http = FakeHttpClient()
        resolved_settings = settings()
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )

        status, headers, _ = await asgi_request(
            app,
            "POST",
            "/v1/agents/runs",
            {
                "provider": "openai",
                "instructions": "Use tools when useful.",
                "input": "What time is it?",
                "skills": [{"name": "utc_time", "arguments": {}}],
            },
        )

        request_body = loads_bytes(http.requests[0].body)
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-justfastllm-agent"], "run")
        self.assertTrue(any("Skill utc_time result" in message["content"] for message in request_body["messages"]))

    async def test_guardrail_violation_returns_422(self) -> None:
        app = create_app(
            settings=settings(),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            guardrails=Guardrails(max_message_chars=10, block_patterns=("blocked",)),
            logger=logging.getLogger("test"),
        )

        status, _, body = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "messages": [{"role": "user", "content": "blocked"}]},
        )

        self.assertEqual(status, 422)
        self.assertEqual(loads_bytes(body)["error"]["code"], "guardrail_violation")

    async def test_specific_guardrail_can_be_disabled(self) -> None:
        resolved_settings = settings()
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, FakeHttpClient()),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            guardrails=Guardrails(
                max_message_chars=100,
                block_patterns=("blocked",),
                disabled=("block_patterns",),
            ),
            logger=logging.getLogger("test"),
        )

        status, _, _ = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "messages": [{"role": "user", "content": "blocked"}]},
        )

        self.assertEqual(status, 200)

    async def test_guardrails_can_be_inspected_and_updated_at_runtime(self) -> None:
        resolved_settings = settings()
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, FakeHttpClient()),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            guardrails=Guardrails(max_message_chars=100, block_patterns=("blocked",)),
            logger=logging.getLogger("test"),
        )

        get_status, _, get_body = await asgi_request(app, "GET", "/v1/guardrails")
        patch_status, _, patch_body = await asgi_request(
            app,
            "PATCH",
            "/v1/guardrails",
            {"disable": ["block_patterns"]},
        )
        chat_status, _, _ = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "messages": [{"role": "user", "content": "blocked"}]},
        )

        self.assertEqual(get_status, 200)
        self.assertTrue(loads_bytes(get_body)["enabled"])
        self.assertEqual(patch_status, 200)
        checks = {item["name"]: item["enabled"] for item in loads_bytes(patch_body)["checks"]}
        self.assertFalse(checks["block_patterns"])
        self.assertEqual(chat_status, 200)

    async def test_guardrails_can_be_globally_disabled_at_runtime(self) -> None:
        resolved_settings = settings()
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, FakeHttpClient()),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            guardrails=Guardrails(max_message_chars=1, block_patterns=("blocked",)),
            logger=logging.getLogger("test"),
        )

        patch_status, _, body = await asgi_request(
            app,
            "PATCH",
            "/v1/guardrails",
            {"enabled": False},
        )
        chat_status, _, _ = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "messages": [{"role": "user", "content": "blocked and long"}]},
        )

        self.assertEqual(patch_status, 200)
        self.assertFalse(loads_bytes(body)["enabled"])
        self.assertEqual(chat_status, 200)


if __name__ == "__main__":
    unittest.main()
