from __future__ import annotations

import logging
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from justfastllm.__main__ import compliance_check
from justfastllm.agents import AgentRunner
from justfastllm.app import create_app, route_inventory_paths
from justfastllm.cache import DatabaseResponseCache, MemoryResponseCache, RedisResponseCache, _command, cache_key
from justfastllm.config import load_settings
from justfastllm.dbstate import database_driver_dependency, database_family, database_transport_security, is_supported_database_url
from justfastllm.errors import UpstreamTransportError
from justfastllm.guardrails import Guardrails
from justfastllm.http import HttpClient, PooledHttpClient, split_url
from justfastllm.jsonutil import dumps_bytes, loads_bytes
from justfastllm.memory import DatabaseUserMemoryStore, MemoryUserMemoryStore, RedisUserMemoryStore
from justfastllm.models import HttpRequest, UpstreamResponse
from justfastllm.providers.anthropic import AnthropicProvider
from justfastllm.providers.factory import ProviderFactory
from justfastllm.providers.openai_compatible import OpenAICompatibleProvider
from justfastllm.proxy_control import InMemoryProxyControlPlane, _is_mongo_url
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


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return dumps_bytes(self.payload)


async def asgi_request(
    app,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    raw_body: bytes | None = None,
):
    path_only, _, raw_query = path.partition("?")
    body = raw_body if raw_body is not None else (dumps_bytes(payload) if payload is not None else b"")
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
            "path": path_only,
            "query_string": raw_query.encode("latin-1"),
            "headers": raw_headers,
        },
        receive,
        send,
    )
    status = sent[0]["status"]
    headers = {key.decode(): value.decode() for key, value in sent[0].get("headers", [])}
    response_body = sent[1].get("body", b"")
    return status, headers, response_body


def sample_route_path(pattern: str) -> str:
    return (
        pattern.replace("{provider}", "openai")
        .replace("{skill_name}", "echo")
        .replace("{request_id}", "privacy-request-1")
        .replace("{user_id}", "user-1")
    )


def first_openapi_method(methods: dict[str, object]) -> str:
    for method in ("get", "post", "put", "patch", "delete"):
        if method in methods:
            return method.upper()
    raise AssertionError(f"no supported method in {methods}")


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

    async def test_openai_compatible_provider_proxies_generic_endpoint(self) -> None:
        cfg = settings().providers["openai"]
        http = FakeHttpClient()
        provider = OpenAICompatibleProvider(cfg, http)

        await provider.openai_endpoint(
            "embeddings",
            dumps_bytes({"provider": "openai", "input": "hello", "model": "text-embedding-3-small"}),
            "application/json",
        )

        request = http.requests[0]
        body = loads_bytes(request.body)
        self.assertEqual(request.url, "https://api.openai.com/v1/embeddings")
        self.assertEqual(request.headers["Authorization"], "Bearer openai-key")
        self.assertEqual(body["input"], "hello")
        self.assertEqual(body["model"], "text-embedding-3-small")
        self.assertNotIn("provider", body)

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
    def test_settings_can_load_structured_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "gateway.json"
            config_path.write_text(
                json.dumps(
                    {
                        "server": {"host": "127.0.0.1", "port": 9000},
                        "gateway": {
                            "default_provider": "ollama",
                            "cache": {"backend": "memory", "ttl_seconds": 12},
                            "control_plane": {
                                "master_key": "from-config",
                                "storage_path": str(Path(tmpdir) / "state.json"),
                                "fallback_providers": ["openai"],
                            },
                            "compliance": {
                                "usage_retention_days": 14,
                                "audit_retention_days": 400,
                                "privacy_contact": "privacy@example.com",
                            },
                        },
                        "providers": {
                            "openai": {"api_key": "config-openai", "model": "gpt-5-mini"},
                            "ollama": {"base_url": "http://localhost:11434/v1", "model": "llama3.1"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_settings(
                env_file=Path("/tmp/justfastllm-missing.env"),
                environ={"JUSTFASTLLM_CONFIG_FILE": str(config_path), "JUSTFASTLLM_DEFAULT_PROVIDER": "openai"},
            )

        self.assertEqual(loaded.host, "127.0.0.1")
        self.assertEqual(loaded.port, 9000)
        self.assertEqual(loaded.default_provider, "openai")
        self.assertEqual(loaded.cache_backend, "memory")
        self.assertEqual(loaded.cache_ttl_seconds, 12)
        self.assertEqual(loaded.master_key, "from-config")
        self.assertEqual(loaded.fallback_providers, ("openai",))
        self.assertEqual(loaded.usage_retention_days, 14)
        self.assertEqual(loaded.audit_retention_days, 400)
        self.assertEqual(loaded.privacy_contact, "privacy@example.com")
        self.assertEqual(loaded.providers["openai"].api_key, "config-openai")
        self.assertEqual(loaded.providers["openai"].default_model, "gpt-5-mini")

    def test_control_plane_redis_url_falls_back_to_redis_url_when_blank(self) -> None:
        loaded = load_settings(
            env_file=Path("/tmp/justfastllm-missing.env"),
            environ={
                "REDIS_URL": "redis://shared-redis:6379/5",
                "JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND": "redis",
                "JUSTFASTLLM_CONTROL_PLANE_REDIS_URL": "",
            },
        )

        self.assertEqual(loaded.control_plane_storage_backend, "redis")
        self.assertEqual(loaded.control_plane_redis_url, "redis://shared-redis:6379/5")

    def test_database_control_plane_settings_support_major_engines(self) -> None:
        urls = {
            "postgres": "postgresql+psycopg://user:pass@localhost:5432/jfl",
            "mysql": "mysql+pymysql://user:pass@localhost:3306/jfl",
            "mssql": "mssql+pymssql://user:pass@localhost:1433/jfl",
            "mongodb": "mongodb://localhost:27017/jfl",
        }
        drivers = {
            "postgres": "psycopg[binary]",
            "mysql": "PyMySQL",
            "mssql": "pymssql",
            "mongodb": "pymongo",
        }
        for engine, url in urls.items():
            with self.subTest(engine=engine):
                loaded = load_settings(
                    env_file=Path("/tmp/justfastllm-missing.env"),
                    environ={
                        "JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND": "database",
                        "JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL": url,
                        "JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE": "jfl-state",
                        "JUSTFASTLLM_CACHE_BACKEND": "database",
                        "JUSTFASTLLM_CACHE_DATABASE_URL": url,
                        "JUSTFASTLLM_CACHE_DATABASE_TABLE": "jfl-cache",
                        "JUSTFASTLLM_USER_MEMORY_BACKEND": "database",
                        "JUSTFASTLLM_USER_MEMORY_DATABASE_URL": url,
                        "JUSTFASTLLM_USER_MEMORY_DATABASE_TABLE": "jfl-memory",
                    },
                )
                control_plane = InMemoryProxyControlPlane(
                    storage_backend=loaded.control_plane_storage_backend,
                    storage_database_url=loaded.control_plane_database_url,
                    storage_database_table=loaded.control_plane_database_table,
                )

                self.assertTrue(control_plane.durable_storage_enabled())
                self.assertEqual(control_plane.storage_database_table, "jfl_state")
                self.assertEqual(loaded.cache_backend, "database")
                self.assertEqual(loaded.cache_database_url, url)
                self.assertEqual(loaded.cache_database_table, "jfl-cache")
                self.assertEqual(loaded.user_memory_backend, "database")
                self.assertEqual(loaded.user_memory_database_url, url)
                self.assertEqual(loaded.user_memory_database_table, "jfl-memory")
                self.assertEqual(_is_mongo_url(url), engine == "mongodb")
                self.assertEqual(database_family(url), engine)
                self.assertEqual(database_driver_dependency(url), drivers[engine])
                self.assertTrue(is_supported_database_url(url))

    def test_sqlite_database_url_is_not_supported_for_compliance_storage(self) -> None:
        self.assertEqual(database_family("sqlite:////tmp/justfastllm.db"), "sqlite")
        self.assertEqual(database_driver_dependency("sqlite:////tmp/justfastllm.db"), "")
        self.assertFalse(is_supported_database_url("sqlite:////tmp/justfastllm.db"))

    def test_database_transport_security_detects_encrypted_remote_urls(self) -> None:
        encrypted_urls = [
            "postgresql+psycopg://user:pass@db.example.com:5432/jfl?sslmode=require",
            "mysql+pymysql://user:pass@db.example.com:3306/jfl?ssl_mode=VERIFY_IDENTITY",
            "mssql+pymssql://user:pass@db.example.com:1433/jfl?encrypt=true",
            "mongodb://db.example.com:27017/jfl?tls=true",
            "mongodb+srv://cluster.example.com/jfl",
        ]
        unencrypted_urls = [
            "postgresql+psycopg://user:pass@db.example.com:5432/jfl",
            "mysql+pymysql://user:pass@db.example.com:3306/jfl",
            "mssql+pymssql://user:pass@db.example.com:1433/jfl",
            "mongodb://db.example.com:27017/jfl",
            "mongodb+srv://cluster.example.com/jfl?tls=false",
        ]

        for url in encrypted_urls:
            with self.subTest(url=url):
                self.assertTrue(database_transport_security(url)["encrypted"])
        for url in unencrypted_urls:
            with self.subTest(url=url):
                self.assertFalse(database_transport_security(url)["encrypted"])

        local = database_transport_security("postgresql+psycopg://user:pass@localhost:5432/jfl")
        self.assertTrue(local["encrypted"])
        self.assertTrue(local["local_endpoint"])

    def test_settings_resolves_env_and_file_secret_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            secret_path = Path(tmpdir) / "anthropic-secret"
            secret_path.write_text("anthropic-from-file\n", encoding="utf-8")
            loaded = load_settings(
                env_file=Path("/tmp/justfastllm-missing.env"),
                environ={
                    "OPENAI_API_KEY": "env:REAL_OPENAI_KEY",
                    "REAL_OPENAI_KEY": "openai-from-env",
                    "ANTHROPIC_API_KEY": f"file:{secret_path}",
                    "JUSTFASTLLM_MASTER_KEY": "env:MASTER_KEY",
                    "MASTER_KEY": "master-from-env",
                },
            )

        self.assertEqual(loaded.providers["openai"].api_key, "openai-from-env")
        self.assertEqual(loaded.providers["anthropic"].api_key, "anthropic-from-file")
        self.assertEqual(loaded.master_key, "master-from-env")

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

    def test_compliance_check_verifies_deployed_readiness_endpoints(self) -> None:
        requested: list[tuple[str, str]] = []

        def urlopen(request, timeout):
            requested.append((request.full_url, request.headers.get("Authorization", "")))
            if request.full_url.endswith("/health"):
                return FakeHttpResponse({"status": "ok", "compliance": {"ready": True}})
            if request.full_url.endswith("/v1/compliance/report"):
                return FakeHttpResponse(
                    {
                        "status": "technical_controls_ready",
                        "assurance": {
                            "automated_deployment_gate_ready": True,
                            "runtime_readiness_ready": True,
                            "can_claim_automatic_legal_or_attested_compliance": False,
                        },
                    }
                )
            if request.full_url.endswith("/v1/compliance/integrity"):
                return FakeHttpResponse(
                    {
                        "object": "evidence_integrity",
                        "aggregate_sha256": "a" * 64,
                        "categories": {
                            "auth_events": {"hash_chain": {"valid": True}},
                            "access_events": {"hash_chain": {"valid": True}},
                            "audit_events": {"hash_chain": {"valid": True}},
                        },
                    }
                )
            raise AssertionError(request.full_url)

        with patch("justfastllm.__main__.urllib.request.urlopen", side_effect=urlopen):
            result = compliance_check("https://gateway.example.com/", master_key="master-key")

        self.assertTrue(result["ok"])
        self.assertEqual(len(requested), 3)
        self.assertEqual(requested[1][1], "Bearer master-key")
        self.assertEqual(requested[2][1], "Bearer master-key")
        self.assertIn("technical readiness", result["assurance_note"])

    def test_compliance_check_fails_closed_without_master_key(self) -> None:
        def urlopen(request, timeout):
            self.assertTrue(request.full_url.endswith("/health"))
            return FakeHttpResponse({"status": "ok", "compliance": {"ready": True}})

        with patch("justfastllm.__main__.urllib.request.urlopen", side_effect=urlopen):
            result = compliance_check("https://gateway.example.com", master_key="")

        self.assertFalse(result["ok"])
        self.assertIn("master key is required", result["errors"][0])
        self.assertFalse(result["checks"]["report_ready"])
        self.assertFalse(result["checks"]["integrity_ready"])

    def test_compliance_check_fails_closed_for_broken_evidence_hash_chain(self) -> None:
        def urlopen(request, timeout):
            if request.full_url.endswith("/health"):
                return FakeHttpResponse({"status": "ok", "compliance": {"ready": True}})
            if request.full_url.endswith("/v1/compliance/report"):
                return FakeHttpResponse(
                    {
                        "status": "technical_controls_ready",
                        "assurance": {
                            "automated_deployment_gate_ready": True,
                            "runtime_readiness_ready": True,
                            "can_claim_automatic_legal_or_attested_compliance": False,
                        },
                    }
                )
            if request.full_url.endswith("/v1/compliance/integrity"):
                return FakeHttpResponse(
                    {
                        "object": "evidence_integrity",
                        "aggregate_sha256": "a" * 64,
                        "categories": {
                            "auth_events": {"hash_chain": {"valid": True}},
                            "access_events": {"hash_chain": {"valid": True}},
                            "audit_events": {"hash_chain": {"valid": False}},
                        },
                    }
                )
            raise AssertionError(request.full_url)

        with patch("justfastllm.__main__.urllib.request.urlopen", side_effect=urlopen):
            result = compliance_check("https://gateway.example.com", master_key="master-key")

        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["integrity_ready"])

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

    def test_redis_cache_clear_deletes_only_configured_prefix(self) -> None:
        connections: list[FakeRedisConnection] = []
        responses = [
            (
                b"*2\r\n"
                b"$1\r\n0\r\n"
                b"*2\r\n"
                b"$7\r\njfl:abc\r\n"
                b"$7\r\njfl:def\r\n"
            ),
            b":2\r\n",
        ]

        def connect(address, timeout):
            connection = FakeRedisConnection(responses.pop(0))
            connections.append(connection)
            return connection

        cache = RedisResponseCache(
            url="redis://localhost:6379/0",
            ttl_seconds=9,
            key_prefix="jfl:",
        )
        with patch("justfastllm.cache.socket.create_connection", side_effect=connect):
            deleted = cache.clear()

        self.assertEqual(deleted, 2)
        scan_command = bytes(connections[0].sent)
        delete_command = bytes(connections[1].sent)
        self.assertIn(b"SCAN", scan_command)
        self.assertIn(b"MATCH", scan_command)
        self.assertIn(b"jfl:*", scan_command)
        self.assertIn(b"DEL", delete_command)
        self.assertIn(b"jfl:abc", delete_command)
        self.assertIn(b"jfl:def", delete_command)

    def test_redis_control_plane_persists_state(self) -> None:
        connections: list[FakeRedisConnection] = []
        responses = [b"$-1\r\n", b"+OK\r\n"]

        def connect(address, timeout):
            connection = FakeRedisConnection(responses.pop(0))
            connections.append(connection)
            return connection

        with patch("justfastllm.proxy_control.socket.create_connection", side_effect=connect):
            first = InMemoryProxyControlPlane(
                master_key="master-key",
                storage_backend="redis",
                storage_redis_url="redis://localhost:6379/0",
                storage_redis_key="jfl:control",
            )
            first.create_user({"user_id": "user-1", "user_email": "ops@example.com"})

        state = dumps_bytes(first.export_state())
        responses = [f"${len(state)}\r\n".encode("ascii") + state + b"\r\n"]

        with patch("justfastllm.proxy_control.socket.create_connection", side_effect=connect):
            second = InMemoryProxyControlPlane(
                master_key="master-key",
                storage_backend="redis",
                storage_redis_url="redis://localhost:6379/0",
                storage_redis_key="jfl:control",
            )

        self.assertEqual(second.user_info("user-1")["user_email"], "ops@example.com")
        self.assertIn(b"SET", bytes(connections[1].sent))
        self.assertIn(b"jfl:control", bytes(connections[1].sent))
        self.assertIn(b"GET", bytes(connections[2].sent))

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

    def test_database_user_memory_store_persists_preferences_feedback_and_delete(self) -> None:
        state: dict[str, object] = {}

        def load_state(url, table_name):
            self.assertEqual(url, "postgresql+psycopg://user:pass@localhost/jfl")
            self.assertEqual(table_name, "jfl_memory")
            return dict(state)

        def save_state(url, table_name, payload):
            self.assertEqual(url, "postgresql+psycopg://user:pass@localhost/jfl")
            self.assertEqual(table_name, "jfl_memory")
            state.clear()
            state.update(payload)

        store = DatabaseUserMemoryStore(
            url="postgresql+psycopg://user:pass@localhost/jfl",
            table_name="jfl-memory",
        )
        with (
            patch("justfastllm.memory._load_sql_state", side_effect=load_state),
            patch("justfastllm.memory._save_sql_state", side_effect=save_state),
        ):
            saved = store.save_preferences("user-1", {"tone": "warm"})
            feedback = store.add_feedback("user-1", {"rating": 5})
            loaded = store.get_preferences("user-1")
            loaded_feedback = store.get_feedback("user-1")
            deleted = store.delete_user("user-1")

        self.assertEqual(saved, {"tone": "warm"})
        self.assertEqual(feedback["rating"], 5)
        self.assertEqual(loaded, {"tone": "warm"})
        self.assertEqual(loaded_feedback[0]["rating"], 5)
        self.assertEqual(deleted, {"preferences_deleted": True, "feedback_deleted": True})
        self.assertEqual(state["preferences"], {})
        self.assertEqual(state["feedback"], {})

    def test_database_user_memory_store_storage_health_writes_state(self) -> None:
        state: dict[str, object] = {"preferences": {}, "feedback": {}}

        def load_state(url, table_name):
            self.assertEqual(url, "postgresql+psycopg://user:pass@localhost/jfl")
            self.assertEqual(table_name, "jfl_memory")
            return dict(state)

        def save_state(url, table_name, payload):
            self.assertEqual(url, "postgresql+psycopg://user:pass@localhost/jfl")
            self.assertEqual(table_name, "jfl_memory")
            state.clear()
            state.update(payload)

        store = DatabaseUserMemoryStore(
            url="postgresql+psycopg://user:pass@localhost/jfl",
            table_name="jfl-memory",
        )
        with (
            patch("justfastllm.memory._load_sql_state", side_effect=load_state),
            patch("justfastllm.memory._save_sql_state", side_effect=save_state),
        ):
            health = store.storage_health()

        self.assertTrue(health["ok"])
        self.assertEqual(health["backend"], "database")
        self.assertEqual(health["driver_dependency"], "psycopg[binary]")

    def test_database_user_memory_store_storage_health_fails_readback_mismatch(self) -> None:
        store = DatabaseUserMemoryStore(
            url="postgresql+psycopg://user:pass@localhost/jfl",
            table_name="jfl-memory",
        )
        with (
            patch("justfastllm.memory._load_sql_state", side_effect=lambda *_: {}),
            patch("justfastllm.memory._save_sql_state", return_value=None),
        ):
            health = store.storage_health()

        self.assertFalse(health["ok"])
        self.assertEqual(health["driver_dependency"], "psycopg[binary]")
        self.assertIn("readback mismatch", health["error"])

    def test_database_user_memory_strict_mode_raises_on_storage_failure(self) -> None:
        store = DatabaseUserMemoryStore(
            url="postgresql+psycopg://user:pass@localhost/jfl",
            table_name="jfl-memory",
            strict=True,
        )
        with patch("justfastllm.memory._load_sql_state", side_effect=OSError("database unavailable")):
            with self.assertRaises(OSError):
                store.save_preferences("user-1", {"tone": "warm"})

    def test_database_response_cache_stores_bytes_prunes_and_clears(self) -> None:
        state: dict[str, object] = {}

        def load_state(url, table_name):
            self.assertEqual(url, "postgresql+psycopg://user:pass@localhost/jfl")
            self.assertEqual(table_name, "jfl_cache")
            return dict(state)

        def save_state(url, table_name, payload):
            self.assertEqual(url, "postgresql+psycopg://user:pass@localhost/jfl")
            self.assertEqual(table_name, "jfl_cache")
            state.clear()
            state.update(payload)

        cache = DatabaseResponseCache(
            url="postgresql+psycopg://user:pass@localhost/jfl",
            table_name="jfl-cache",
            max_items=1,
            ttl_seconds=60,
        )
        with (
            patch("justfastllm.cache.load_sql_state", side_effect=load_state),
            patch("justfastllm.cache.save_sql_state", side_effect=save_state),
        ):
            cache.set("first", b"one")
            self.assertEqual(cache.get("first"), b"one")
            cache.set("second", b"two")
            self.assertIsNone(cache.get("first"))
            self.assertEqual(cache.get("second"), b"two")
            self.assertEqual(cache.clear(), 1)

        self.assertEqual(state["entries"], {})

    def test_database_response_cache_storage_health_writes_state(self) -> None:
        state: dict[str, object] = {"entries": {}}

        def load_state(url, table_name):
            self.assertEqual(url, "postgresql+psycopg://user:pass@localhost/jfl")
            self.assertEqual(table_name, "jfl_cache")
            return dict(state)

        def save_state(url, table_name, payload):
            self.assertEqual(url, "postgresql+psycopg://user:pass@localhost/jfl")
            self.assertEqual(table_name, "jfl_cache")
            state.clear()
            state.update(payload)

        cache = DatabaseResponseCache(
            url="postgresql+psycopg://user:pass@localhost/jfl",
            table_name="jfl-cache",
            max_items=1,
            ttl_seconds=60,
        )
        with (
            patch("justfastllm.cache.load_sql_state", side_effect=load_state),
            patch("justfastllm.cache.save_sql_state", side_effect=save_state),
        ):
            health = cache.storage_health()

        self.assertTrue(health["ok"])
        self.assertEqual(health["backend"], "database")
        self.assertEqual(health["driver_dependency"], "psycopg[binary]")

    def test_database_response_cache_storage_health_fails_readback_mismatch(self) -> None:
        cache = DatabaseResponseCache(
            url="postgresql+psycopg://user:pass@localhost/jfl",
            table_name="jfl-cache",
            max_items=1,
            ttl_seconds=60,
        )
        with (
            patch("justfastllm.cache.load_sql_state", side_effect=lambda *_: {}),
            patch("justfastllm.cache.save_sql_state", return_value=None),
        ):
            health = cache.storage_health()

        self.assertFalse(health["ok"])
        self.assertEqual(health["driver_dependency"], "psycopg[binary]")
        self.assertIn("readback mismatch", health["error"])

    def test_database_response_cache_strict_mode_raises_on_storage_failure(self) -> None:
        cache = DatabaseResponseCache(
            url="postgresql+psycopg://user:pass@localhost/jfl",
            table_name="jfl-cache",
            max_items=1,
            ttl_seconds=60,
            strict=True,
        )
        with patch("justfastllm.cache.load_sql_state", side_effect=OSError("database unavailable")):
            with self.assertRaises(OSError):
                cache.set("first", b"one")

    def test_database_control_plane_strict_mode_raises_on_evidence_save_failure(self) -> None:
        with (
            patch("justfastllm.proxy_control._load_sql_state", return_value=None),
            patch("justfastllm.proxy_control._save_sql_state", side_effect=OSError("database unavailable")),
        ):
            control_plane = InMemoryProxyControlPlane(
                master_key="master-key",
                storage_backend="database",
                storage_database_url="postgresql+psycopg://user:pass@localhost/jfl",
                storage_database_table="jfl-state",
                storage_strict=True,
            )
            with self.assertRaises(OSError):
                control_plane.generate_key({"name": "strict"})

    def test_database_control_plane_storage_health_fails_readback_mismatch(self) -> None:
        with (
            patch("justfastllm.proxy_control._load_sql_state", side_effect=[None, {"events": []}]),
            patch("justfastllm.proxy_control._save_sql_state", return_value=None),
        ):
            control_plane = InMemoryProxyControlPlane(
                master_key="master-key",
                storage_backend="database",
                storage_database_url="postgresql+psycopg://user:pass@localhost/jfl",
                storage_database_table="jfl-state",
            )
            health = control_plane.storage_health()

        self.assertFalse(health["ok"])
        self.assertEqual(health["driver_dependency"], "psycopg[binary]")
        self.assertIn("readback mismatch", health["error"])

    def test_retention_pruning_from_read_paths_persists_to_storage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "control-plane.json"
            control_plane = InMemoryProxyControlPlane(
                master_key="master-key",
                storage_backend="file",
                storage_path=state_path,
                audit_retention_days=1,
            )
            control_plane.record_access_event(
                request_id="request-1",
                method="GET",
                path="/openapi.json",
                status_code=200,
                latency_ms=1.0,
                auth_context="anonymous",
            )
            control_plane._access_events[0].created_at = 0

            self.assertEqual(control_plane.access_events(), [])
            persisted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(persisted["access_events"], [])

    def test_evidence_integrity_reports_hash_chains_and_tampering(self) -> None:
        control_plane = InMemoryProxyControlPlane(master_key="master-key")
        control_plane.record_audit_event("create", "key", "key-1", actor="master")
        control_plane.record_access_event(
            request_id="request-1",
            method="GET",
            path="/openapi.json",
            status_code=200,
            latency_ms=1.0,
            auth_context="anonymous",
        )
        control_plane._record_auth_event(
            auth_type="admin",
            outcome="success",
            reason="master_key",
            route="audit",
        )

        integrity = control_plane.evidence_integrity()

        self.assertTrue(integrity["categories"]["audit_events"]["hash_chain"]["valid"])
        self.assertTrue(integrity["categories"]["access_events"]["hash_chain"]["valid"])
        self.assertTrue(integrity["categories"]["auth_events"]["hash_chain"]["valid"])
        self.assertEqual(len(control_plane.audit_events()[0]["event_hash"]), 64)

        control_plane._audit_events[0].target_id = "tampered"
        tampered = control_plane.evidence_integrity()

        self.assertFalse(tampered["categories"]["audit_events"]["hash_chain"]["valid"])
        self.assertEqual(tampered["categories"]["audit_events"]["hash_chain"]["broken_index"], 0)

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


class DeploymentDescriptorTests(unittest.TestCase):
    def test_production_descriptors_are_database_first_for_compliance_state(self) -> None:
        descriptors = {
            "docker": (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"),
            "render": (REPO_ROOT / "render.yaml").read_text(encoding="utf-8"),
            "railway": (REPO_ROOT / "railway.toml").read_text(encoding="utf-8"),
            "aws": (REPO_ROOT / "deploy/aws/AppRunner.yaml").read_text(encoding="utf-8"),
            "gcp": (REPO_ROOT / "deploy/gcp/cloudrun-service.yaml").read_text(encoding="utf-8"),
        }

        for name, text in descriptors.items():
            with self.subTest(descriptor=name):
                self.assertIn("JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL", text)
                self.assertIn("JUSTFASTLLM_CACHE_DATABASE_URL", text)
                self.assertIn("JUSTFASTLLM_USER_MEMORY_DATABASE_URL", text)
                self.assertIn("JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND", text)
                self.assertIn("JUSTFASTLLM_REQUIRE_COMPLIANCE_READY", text)
                self.assertIn("JUSTFASTLLM_MASTER_KEY", text)
                self.assertIn("JUSTFASTLLM_PRIVACY_CONTACT", text)
                self.assertIn("JUSTFASTLLM_SECURITY_CONTACT", text)
                self.assertIn("JUSTFASTLLM_SUBPROCESSORS_URL", text)
                self.assertIn("JUSTFASTLLM_DPA_URL", text)
                self.assertIn("database", text.lower())

        self.assertIn("POSTGRES_PASSWORD", descriptors["docker"])
        self.assertIn("JUSTFASTLLM_DATABASE_URL", descriptors["render"])
        self.assertIn("JUSTFASTLLM_DATABASE_URL", descriptors["railway"])
        self.assertIn("DatabaseUrl", descriptors["aws"])
        self.assertIn("justfastllm-database-url", descriptors["gcp"])

        for name in ("render", "aws", "gcp"):
            with self.subTest(descriptor=name):
                self.assertNotIn("JUSTFASTLLM_CONTROL_PLANE_REDIS_URL", descriptors[name])
                self.assertNotIn("justfastllm-redis-url", descriptors[name])

    def test_docs_do_not_overclaim_encryption_or_legal_compliance(self) -> None:
        banned_phrases = (
            "100% compliant",
            "fully compliant",
            "guaranteed compliant",
            "automatically compliant",
            "encrypted file snapshot",
            "encrypted file snapshots",
            "encrypted json snapshot",
        )
        paths = [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").glob("*.md"))]

        for path in paths:
            text = path.read_text(encoding="utf-8").lower()
            for phrase in banned_phrases:
                with self.subTest(path=path.name, phrase=phrase):
                    self.assertNotIn(phrase, text)


class GatewayAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_app_defaults_to_redis_cache_backend(self) -> None:
        resolved_settings = load_settings(
            env_file=Path("/tmp/justfastllm-missing.env"),
            environ={"OPENAI_API_KEY": "openai-key"},
        )

        app = create_app(settings=resolved_settings, logger=logging.getLogger("test"))

        self.assertIsInstance(app.cache, RedisResponseCache)
        self.assertFalse(app.cache.enabled)

    async def test_compliance_mode_requires_explicit_personal_data_cache_opt_in(self) -> None:
        disabled_settings = settings(JUSTFASTLLM_CACHE_BACKEND="memory")
        disabled_app = create_app(settings=disabled_settings, logger=logging.getLogger("test"))
        enabled_settings = settings(
            JUSTFASTLLM_CACHE_BACKEND="memory",
            JUSTFASTLLM_CACHE_PERSONAL_DATA_ALLOWED="true",
        )
        enabled_app = create_app(settings=enabled_settings, logger=logging.getLogger("test"))

        self.assertFalse(disabled_app.cache.enabled)
        self.assertTrue(enabled_app.cache.enabled)

    async def test_response_cache_is_scoped_by_header_user_identity(self) -> None:
        http = FakeHttpClient()
        resolved_settings = settings(
            JUSTFASTLLM_CACHE_ENABLED="true",
            JUSTFASTLLM_CACHE_BACKEND="memory",
            JUSTFASTLLM_CACHE_PERSONAL_DATA_ALLOWED="true",
            JUSTFASTLLM_USER_MEMORY_ENABLED="false",
        )
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=True),
            logger=logging.getLogger("test"),
        )
        payload = {
            "provider": "openai",
            "model": "gpt-5-nano",
            "messages": [{"role": "user", "content": "same prompt"}],
        }

        first_status, first_headers, _ = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            payload,
            headers={"x-user-id": "user-a"},
        )
        second_status, second_headers, _ = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            payload,
            headers={"x-user-id": "user-b"},
        )
        third_status, third_headers, _ = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            payload,
            headers={"x-user-id": "user-a"},
        )

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(third_status, 200)
        self.assertEqual(first_headers["x-justfastllm-cache"], "miss")
        self.assertEqual(second_headers["x-justfastllm-cache"], "miss")
        self.assertEqual(third_headers["x-justfastllm-cache"], "hit")
        self.assertEqual(len(http.requests), 2)

    async def test_create_app_uses_database_cache_backend(self) -> None:
        resolved_settings = settings(
            JUSTFASTLLM_CACHE_BACKEND="database",
            JUSTFASTLLM_CACHE_DATABASE_URL="postgresql+psycopg://user:pass@localhost/jfl",
            JUSTFASTLLM_CACHE_DATABASE_TABLE="jfl-cache",
            JUSTFASTLLM_CACHE_PERSONAL_DATA_ALLOWED="true",
        )

        app = create_app(settings=resolved_settings, logger=logging.getLogger("test"))

        self.assertIsInstance(app.cache, DatabaseResponseCache)
        self.assertTrue(app.cache.enabled)
        self.assertEqual(app.cache.table_name, "jfl_cache")
        self.assertTrue(app.cache.strict)

    async def test_create_app_uses_strict_database_memory_backend_in_compliance_mode(self) -> None:
        resolved_settings = settings(
            JUSTFASTLLM_USER_MEMORY_BACKEND="database",
            JUSTFASTLLM_USER_MEMORY_DATABASE_URL="postgresql+psycopg://user:pass@localhost/jfl",
            JUSTFASTLLM_USER_MEMORY_DATABASE_TABLE="jfl-memory",
        )

        app = create_app(settings=resolved_settings, logger=logging.getLogger("test"))

        self.assertIsInstance(app.memory_store, DatabaseUserMemoryStore)
        self.assertTrue(app.memory_store.enabled)
        self.assertEqual(app.memory_store.table_name, "jfl_memory")
        self.assertTrue(app.memory_store.strict)

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
        self.assertIn("PATCH", headers["access-control-allow-methods"])
        self.assertIn("DELETE", headers["access-control-allow-methods"])
        self.assertIn("x-team-id", headers["access-control-allow-headers"])
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(headers["referrer-policy"], "no-referrer")
        self.assertIn("openai", loads_bytes(body)["providers"])

    async def test_health_compliance_gate_fails_closed_when_required(self) -> None:
        app = create_app(
            settings=settings(JUSTFASTLLM_REQUIRE_COMPLIANCE_READY="true"),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )

        status, _, body = await asgi_request(app, "GET", "/health")

        payload = loads_bytes(body)
        self.assertEqual(status, 503)
        self.assertEqual(payload["status"], "compliance_not_ready")
        self.assertFalse(payload["compliance"]["ready"])
        self.assertIn("master_key_configured", payload["compliance"]["missing"])

    async def test_health_compliance_gate_passes_when_required_settings_exist(self) -> None:
        app = create_app(
            settings=settings(
                JUSTFASTLLM_REQUIRE_COMPLIANCE_READY="true",
                JUSTFASTLLM_MASTER_KEY="master-key",
                JUSTFASTLLM_PRIVACY_CONTACT="privacy@example.com",
                JUSTFASTLLM_SECURITY_CONTACT="security@example.com",
                JUSTFASTLLM_SUBPROCESSORS_URL="https://example.com/subprocessors",
                JUSTFASTLLM_DPA_URL="https://example.com/dpa",
                JUSTFASTLLM_USER_MEMORY_ENABLED="false",
                JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND="file",
                JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH="/tmp/justfastllm-health-gate.json",
            ),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )

        status, _, body = await asgi_request(app, "GET", "/health")

        payload = loads_bytes(body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["compliance"]["ready"])
        self.assertEqual(payload["compliance"]["missing"], [])
        self.assertTrue(payload["compliance"]["checks"]["control_plane_storage_writable"])

    async def test_health_compliance_gate_fails_when_control_plane_storage_is_not_writable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(
                settings=settings(
                    JUSTFASTLLM_REQUIRE_COMPLIANCE_READY="true",
                    JUSTFASTLLM_MASTER_KEY="master-key",
                    JUSTFASTLLM_PRIVACY_CONTACT="privacy@example.com",
                    JUSTFASTLLM_SECURITY_CONTACT="security@example.com",
                    JUSTFASTLLM_SUBPROCESSORS_URL="https://example.com/subprocessors",
                    JUSTFASTLLM_DPA_URL="https://example.com/dpa",
                    JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND="file",
                    JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH=tmpdir,
                ),
                cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
                logger=logging.getLogger("test"),
            )

            status, _, body = await asgi_request(app, "GET", "/health")

        payload = loads_bytes(body)
        self.assertEqual(status, 503)
        self.assertIn("control_plane_storage_writable", payload["compliance"]["missing"])
        self.assertFalse(payload["compliance"]["storage_health"]["control_plane"]["ok"])

    async def test_health_compliance_gate_rejects_unsupported_database_family(self) -> None:
        with (
            patch("justfastllm.proxy_control._load_sql_state", return_value=None),
            patch("justfastllm.proxy_control._save_sql_state", return_value=None),
        ):
            app = create_app(
                settings=settings(
                    JUSTFASTLLM_REQUIRE_COMPLIANCE_READY="true",
                    JUSTFASTLLM_MASTER_KEY="master-key",
                    JUSTFASTLLM_PRIVACY_CONTACT="privacy@example.com",
                    JUSTFASTLLM_SECURITY_CONTACT="security@example.com",
                    JUSTFASTLLM_SUBPROCESSORS_URL="https://example.com/subprocessors",
                    JUSTFASTLLM_DPA_URL="https://example.com/dpa",
                    JUSTFASTLLM_USER_MEMORY_ENABLED="false",
                    JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND="database",
                    JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL="sqlite:////tmp/justfastllm.db",
                ),
                cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
                logger=logging.getLogger("test"),
            )

            status, _, body = await asgi_request(app, "GET", "/health")

        payload = loads_bytes(body)
        self.assertEqual(status, 503)
        self.assertIn("database_families_supported", payload["compliance"]["missing"])
        self.assertEqual(payload["compliance"]["database_families"]["control_plane"], "sqlite")
        self.assertEqual(payload["compliance"]["storage_health"]["control_plane"]["database_family"], "sqlite")

    async def test_health_compliance_gate_rejects_remote_database_without_encrypted_transport(self) -> None:
        with (
            patch("justfastllm.proxy_control._load_sql_state", return_value=None),
            patch("justfastllm.proxy_control._save_sql_state", return_value=None),
        ):
            app = create_app(
                settings=settings(
                    JUSTFASTLLM_REQUIRE_COMPLIANCE_READY="true",
                    JUSTFASTLLM_MASTER_KEY="master-key",
                    JUSTFASTLLM_PRIVACY_CONTACT="privacy@example.com",
                    JUSTFASTLLM_SECURITY_CONTACT="security@example.com",
                    JUSTFASTLLM_SUBPROCESSORS_URL="https://example.com/subprocessors",
                    JUSTFASTLLM_DPA_URL="https://example.com/dpa",
                    JUSTFASTLLM_USER_MEMORY_ENABLED="false",
                    JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND="database",
                    JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL="postgresql+psycopg://user:pass@db.example.com:5432/jfl",
                ),
                cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
                logger=logging.getLogger("test"),
            )

            status, _, body = await asgi_request(app, "GET", "/health")

        payload = loads_bytes(body)
        self.assertEqual(status, 503)
        self.assertIn("database_transport_encryption_configured", payload["compliance"]["missing"])
        self.assertFalse(payload["compliance"]["checks"]["database_transport_encryption_configured"])
        self.assertFalse(payload["compliance"]["database_transport_security"]["control_plane"]["encrypted"])
        self.assertFalse(payload["compliance"]["storage_health"]["control_plane"]["transport_security"]["encrypted"])

    async def test_health_compliance_gate_accepts_remote_database_with_encrypted_transport(self) -> None:
        stored_state: dict[str, object] = {}

        def save_state(url: str, table_name: str, state: dict[str, object]) -> None:
            stored_state.clear()
            stored_state.update(json.loads(json.dumps(state)))

        def load_state(url: str, table_name: str) -> dict[str, object] | None:
            return json.loads(json.dumps(stored_state)) if stored_state else None

        with (
            patch("justfastllm.proxy_control._load_sql_state", side_effect=load_state),
            patch("justfastllm.proxy_control._save_sql_state", side_effect=save_state),
        ):
            app = create_app(
                settings=settings(
                    JUSTFASTLLM_REQUIRE_COMPLIANCE_READY="true",
                    JUSTFASTLLM_MASTER_KEY="master-key",
                    JUSTFASTLLM_PRIVACY_CONTACT="privacy@example.com",
                    JUSTFASTLLM_SECURITY_CONTACT="security@example.com",
                    JUSTFASTLLM_SUBPROCESSORS_URL="https://example.com/subprocessors",
                    JUSTFASTLLM_DPA_URL="https://example.com/dpa",
                    JUSTFASTLLM_USER_MEMORY_ENABLED="false",
                    JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND="database",
                    JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL="postgresql+psycopg://user:pass@db.example.com:5432/jfl?sslmode=require",
                ),
                cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
                logger=logging.getLogger("test"),
            )

            status, _, body = await asgi_request(app, "GET", "/health")

        payload = loads_bytes(body)
        self.assertEqual(status, 200)
        self.assertNotIn("database_transport_encryption_configured", payload["compliance"]["missing"])
        self.assertTrue(payload["compliance"]["checks"]["database_transport_encryption_configured"])
        self.assertTrue(payload["compliance"]["database_transport_security"]["control_plane"]["encrypted"])
        self.assertTrue(payload["compliance"]["storage_health"]["control_plane"]["transport_security"]["encrypted"])

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
        self.assertEqual(set(payload["paths"]), route_inventory_paths())
        self.assertIn("MasterKeyAuth", payload["components"]["securitySchemes"])
        self.assertIn("GatewayAuth", payload["components"]["securitySchemes"])
        self.assertIn("/openapi.json", payload["paths"])
        self.assertIn("/v1/chat/completions", payload["paths"])
        self.assertIn("/v1/guardrails", payload["paths"])
        self.assertIn("/v1/users/{user_id}/preferences", payload["paths"])
        self.assertIn("/v1/access", payload["paths"])
        self.assertIn("/v1/auth/events", payload["paths"])
        self.assertIn("/v1/compliance/status", payload["paths"])
        self.assertIn("/v1/compliance/evidence", payload["paths"])
        self.assertIn("/v1/compliance/integrity", payload["paths"])
        self.assertIn("/v1/compliance/report", payload["paths"])
        self.assertIn("/v1/privacy/consents", payload["paths"])
        self.assertIn("/v1/privacy/consents/{consent_id}", payload["paths"])
        self.assertIn("/v1/privacy/consents/{consent_id}/withdraw", payload["paths"])
        self.assertIn("/v1/privacy/requests", payload["paths"])
        self.assertIn("/v1/privacy/requests/{request_id}", payload["paths"])
        self.assertIn("/v1/privacy/users/{user_id}/export", payload["paths"])
        self.assertIn("/v1/privacy/users/{user_id}/erase", payload["paths"])
        for alias_path in (
            "/user/new",
            "/user/info",
            "/v1/proxy/users/delete",
            "/team/new",
            "/team/info",
            "/team/list",
            "/v1/proxy/teams/update",
            "/v1/proxy/teams/delete",
            "/service_account/key/generate",
        ):
            with self.subTest(alias_path=alias_path):
                self.assertIn(alias_path, payload["paths"])
                method = next(iter(payload["paths"][alias_path].values()))
                self.assertEqual(method["security"], [{"MasterKeyAuth": []}])
        self.assertEqual(payload["paths"]["/health"]["get"].get("security"), None)
        self.assertEqual(payload["paths"]["/v1/audit"]["get"]["security"], [{"MasterKeyAuth": []}])
        self.assertIn("get", payload["paths"]["/v1/keys/info"])
        self.assertIn("post", payload["paths"]["/v1/keys/update"])
        self.assertIn("patch", payload["paths"]["/key/update"])
        self.assertIn("delete", payload["paths"]["/key/delete"])
        self.assertIn("delete", payload["paths"]["/v1/proxy/users/delete"])
        self.assertIn("patch", payload["paths"]["/user/update"])
        self.assertIn("delete", payload["paths"]["/user/delete"])
        self.assertIn("patch", payload["paths"]["/v1/proxy/teams/update"])
        self.assertIn("delete", payload["paths"]["/v1/proxy/teams/delete"])
        self.assertIn("patch", payload["paths"]["/team/update"])
        self.assertIn("delete", payload["paths"]["/team/delete"])
        self.assertEqual(payload["paths"]["/v1/chat/completions"]["post"]["security"], [{"GatewayAuth": []}])
        self.assertEqual(
            payload["paths"]["/v1/users/{user_id}/preferences"]["put"]["security"],
            [{"MasterKeyAuth": []}, {"GatewayAuth": []}],
        )
        self.assertIn("patch", payload["paths"]["/v1/users/{user_id}/preferences"])
        self.assertIn("post", payload["paths"]["/v1/users/{user_id}/preferences"])
        self.assertEqual(
            payload["paths"]["/v1/users/{user_id}/preferences"]["patch"]["security"],
            [{"MasterKeyAuth": []}, {"GatewayAuth": []}],
        )

    async def test_route_inventory_requests_are_recorded_as_access_events(self) -> None:
        app = create_app(
            settings=settings(JUSTFASTLLM_MASTER_KEY="master-key"),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )
        openapi_status, _, openapi_body = await asgi_request(app, "GET", "/openapi.json")
        paths = loads_bytes(openapi_body)["paths"]
        sampled: list[tuple[str, str]] = []

        self.assertEqual(openapi_status, 200)
        for pattern, methods in paths.items():
            method = first_openapi_method(methods)
            path = sample_route_path(pattern)
            status, _, _ = await asgi_request(app, method, path)
            self.assertNotEqual(status, 500, msg=f"{method} {path}")
            sampled.append((method, path))

        access_status, _, access_body = await asgi_request(
            app,
            "GET",
            "/v1/access",
            headers={"authorization": "Bearer master-key"},
        )
        events = loads_bytes(access_body)["data"]
        seen = {(str(event["method"]), str(event["path"])) for event in events}

        self.assertEqual(access_status, 200)
        for method, path in sampled:
            with self.subTest(method=method, path=path):
                self.assertIn((method, path), seen)

    async def test_malformed_json_returns_bad_request_and_access_evidence(self) -> None:
        app = create_app(
            settings=settings(JUSTFASTLLM_MASTER_KEY="master-key"),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )

        status, _, body = await asgi_request(app, "POST", "/v1/chat/completions", raw_body=b"{")
        access_status, _, access_body = await asgi_request(
            app,
            "GET",
            "/v1/access",
            headers={"authorization": "Bearer master-key"},
        )

        self.assertEqual(status, 400)
        self.assertEqual(loads_bytes(body)["error"]["code"], "bad_request")
        self.assertEqual(access_status, 200)
        self.assertTrue(
            any(
                item["path"] == "/v1/chat/completions" and item["status_code"] == 400
                for item in loads_bytes(access_body)["data"]
            )
        )

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

    async def test_metadata_routes_require_auth_when_master_key_is_configured(self) -> None:
        http = FakeHttpClient(
            UpstreamResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                body=dumps_bytes({"object": "list", "data": [{"id": "model-a"}]}),
            )
        )
        resolved_settings = settings(JUSTFASTLLM_MASTER_KEY="master-key")
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )
        admin = {"authorization": "Bearer master-key"}

        unauth_models_status, _, unauth_models_body = await asgi_request(app, "GET", "/v1/models")
        auth_models_status, _, auth_models_body = await asgi_request(app, "GET", "/v1/models", headers=admin)
        unauth_provider_status, _, unauth_provider_body = await asgi_request(app, "GET", "/v1/providers/openai/models")
        auth_provider_status, _, auth_provider_body = await asgi_request(app, "GET", "/v1/providers/openai/models", headers=admin)
        unauth_pricing_status, _, unauth_pricing_body = await asgi_request(app, "GET", "/v1/pricing")
        auth_pricing_status, _, auth_pricing_body = await asgi_request(app, "GET", "/v1/pricing", headers=admin)
        unauth_features_status, _, unauth_features_body = await asgi_request(app, "GET", "/v1/proxy/features")
        auth_features_status, _, auth_features_body = await asgi_request(app, "GET", "/v1/proxy/features", headers=admin)

        self.assertEqual(unauth_models_status, 401)
        self.assertEqual(loads_bytes(unauth_models_body)["error"]["code"], "unauthorized")
        self.assertEqual(auth_models_status, 200)
        self.assertEqual(loads_bytes(auth_models_body)["object"], "list")
        self.assertEqual(unauth_provider_status, 401)
        self.assertEqual(loads_bytes(unauth_provider_body)["error"]["code"], "unauthorized")
        self.assertEqual(auth_provider_status, 200)
        self.assertEqual(loads_bytes(auth_provider_body)["data"][0]["id"], "model-a")
        self.assertEqual(unauth_pricing_status, 401)
        self.assertEqual(loads_bytes(unauth_pricing_body)["error"]["code"], "unauthorized")
        self.assertEqual(auth_pricing_status, 200)
        self.assertEqual(loads_bytes(auth_pricing_body)["object"], "list")
        self.assertEqual(unauth_features_status, 401)
        self.assertEqual(loads_bytes(unauth_features_body)["error"]["code"], "unauthorized")
        self.assertEqual(auth_features_status, 200)
        self.assertTrue(loads_bytes(auth_features_body)["virtual_keys"])
        self.assertEqual(len(http.requests), 1)

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
        self.assertIn("PUT", headers["access-control-allow-methods"])
        self.assertIn("PATCH", headers["access-control-allow-methods"])
        self.assertIn("x-team-id", headers["access-control-allow-headers"])
        self.assertIn("x-request-id", headers)

    async def test_options_preflight_bypasses_protected_routes(self) -> None:
        app = create_app(
            settings=settings(
                JUSTFASTLLM_CORS_ALLOW_ORIGIN="https://dashboard.example.test",
                JUSTFASTLLM_MASTER_KEY="master-key",
            ),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )

        status, headers, body = await asgi_request(app, "OPTIONS", "/v1/keys")

        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertEqual(headers["access-control-allow-origin"], "https://dashboard.example.test")
        self.assertIn("DELETE", headers["access-control-allow-methods"])
        self.assertIn("authorization", headers["access-control-allow-headers"])
        self.assertIn("x-team-id", headers["access-control-allow-headers"])

    async def test_options_preflight_includes_custom_key_header(self) -> None:
        app = create_app(
            settings=settings(
                JUSTFASTLLM_CORS_ALLOW_ORIGIN="https://dashboard.example.test",
                JUSTFASTLLM_KEY_HEADER_NAME="x-gateway-key",
            ),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )

        status, headers, body = await asgi_request(app, "OPTIONS", "/v1/chat/completions")

        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertIn("x-gateway-key", headers["access-control-allow-headers"])

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

    async def test_virtual_key_generation_enforces_model_access_and_records_metrics(self) -> None:
        http = FakeHttpClient()
        resolved_settings = settings(JUSTFASTLLM_MASTER_KEY="master-key")
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )

        unauth_status, _, unauth_body = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "model": "gpt-5-nano", "messages": [{"role": "user", "content": "hi"}]},
        )
        key_status, _, key_body = await asgi_request(
            app,
            "POST",
            "/v1/keys",
            {"name": "app", "models": ["gpt-5-nano"], "rpm_limit": 10, "budget_usd": 1},
            headers={"authorization": "Bearer master-key"},
        )
        token = loads_bytes(key_body)["key"]
        chat_status, chat_headers, _ = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "model": "gpt-5-nano", "messages": [{"role": "user", "content": "hi"}]},
            headers={"authorization": f"Bearer {token}"},
        )
        blocked_status, _, blocked_body = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "model": "blocked-model", "messages": [{"role": "user", "content": "hi"}]},
            headers={"authorization": f"Bearer {token}"},
        )
        metrics_status, _, metrics_body = await asgi_request(
            app,
            "GET",
            "/v1/metrics",
            headers={"authorization": "Bearer master-key"},
        )
        auth_status, _, auth_body = await asgi_request(
            app,
            "GET",
            "/v1/auth/events",
            headers={"authorization": "Bearer master-key"},
        )

        self.assertEqual(unauth_status, 401)
        self.assertEqual(loads_bytes(unauth_body)["error"]["code"], "unauthorized")
        self.assertEqual(key_status, 201)
        self.assertEqual(chat_status, 200)
        self.assertIn("x-justfastllm-call-id", chat_headers)
        self.assertIn("x-justfastllm-cost-usd", chat_headers)
        self.assertEqual(blocked_status, 403)
        self.assertEqual(loads_bytes(blocked_body)["error"]["code"], "forbidden")
        metrics = loads_bytes(metrics_body)
        self.assertEqual(metrics_status, 200)
        self.assertEqual(metrics["requests"], 1)
        self.assertIn("gpt-5-nano", metrics["by_model"])
        auth_events = loads_bytes(auth_body)["data"]
        self.assertEqual(auth_status, 200)
        self.assertTrue(any(item["outcome"] == "failure" and item["reason"] == "missing_bearer_token" for item in auth_events))
        self.assertTrue(any(item["outcome"] == "failure" and item["reason"] == "model_forbidden" for item in auth_events))
        self.assertTrue(any(item["outcome"] == "success" and item["auth_type"] == "virtual_key" for item in auth_events))

    async def test_virtual_key_rpm_and_budget_limits_are_enforced(self) -> None:
        http = FakeHttpClient()
        resolved_settings = settings(JUSTFASTLLM_MASTER_KEY="master-key")
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )
        key_status, _, key_body = await asgi_request(
            app,
            "POST",
            "/v1/keys",
            {"name": "limited", "models": ["*"], "rpm_limit": 1, "budget_usd": 0.00000001},
            headers={"authorization": "Bearer master-key"},
        )
        token = loads_bytes(key_body)["key"]
        payload = {"provider": "openai", "model": "gpt-5-nano", "messages": [{"role": "user", "content": "hi"}]}

        first_status, _, _ = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            payload,
            headers={"authorization": f"Bearer {token}"},
        )
        second_status, _, second_body = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            payload,
            headers={"authorization": f"Bearer {token}"},
        )

        self.assertEqual(key_status, 201)
        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 429)
        self.assertEqual(loads_bytes(second_body)["error"]["code"], "rate_limit_exceeded")

    async def test_virtual_key_allowed_routes_are_enforced_for_agent_runs(self) -> None:
        http = FakeHttpClient()
        resolved_settings = settings(JUSTFASTLLM_MASTER_KEY="master-key")
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )
        _, _, key_body = await asgi_request(
            app,
            "POST",
            "/v1/keys",
            {"name": "chat-only", "models": ["*"], "allowed_routes": ["chat"], "budget_usd": 1},
            headers={"authorization": "Bearer master-key"},
        )
        token = loads_bytes(key_body)["key"]

        chat_status, _, _ = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "model": "gpt-5-nano", "messages": [{"role": "user", "content": "hi"}]},
            headers={"authorization": f"Bearer {token}"},
        )
        agent_status, _, agent_body = await asgi_request(
            app,
            "POST",
            "/v1/agents/runs",
            {"provider": "openai", "input": "hi"},
            headers={"authorization": f"Bearer {token}"},
        )

        self.assertEqual(chat_status, 200)
        self.assertEqual(agent_status, 403)
        self.assertEqual(loads_bytes(agent_body)["error"]["code"], "forbidden")

    async def test_embeddings_route_proxies_caches_and_records_usage(self) -> None:
        response = UpstreamResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=dumps_bytes(
                {
                    "object": "list",
                    "data": [{"object": "embedding", "embedding": [0.1, 0.2], "index": 0}],
                    "usage": {"prompt_tokens": 4, "total_tokens": 4},
                }
            ),
        )
        http = FakeHttpClient(response)
        resolved_settings = settings(JUSTFASTLLM_MASTER_KEY="master-key")
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )
        _, _, key_body = await asgi_request(
            app,
            "POST",
            "/v1/keys",
            {"name": "embedder", "models": ["text-embedding-3-small"], "allowed_routes": ["embeddings"], "budget_usd": 1},
            headers={"authorization": "Bearer master-key"},
        )
        token = loads_bytes(key_body)["key"]
        payload = {"provider": "openai", "model": "text-embedding-3-small", "input": "hello"}

        first_status, first_headers, _ = await asgi_request(
            app,
            "POST",
            "/v1/embeddings",
            payload,
            headers={"authorization": f"Bearer {token}"},
        )
        second_status, second_headers, _ = await asgi_request(
            app,
            "POST",
            "/v1/embeddings",
            payload,
            headers={"authorization": f"Bearer {token}"},
        )
        chat_status, _, chat_body = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "model": "text-embedding-3-small", "messages": [{"role": "user", "content": "hi"}]},
            headers={"authorization": f"Bearer {token}"},
        )
        metrics_status, _, metrics_body = await asgi_request(
            app,
            "GET",
            "/v1/metrics",
            headers={"authorization": "Bearer master-key"},
        )

        self.assertEqual(first_status, 200)
        self.assertEqual(first_headers["x-justfastllm-cache"], "miss")
        self.assertEqual(second_status, 200)
        self.assertEqual(second_headers["x-justfastllm-cache"], "hit")
        self.assertEqual(len(http.requests), 1)
        self.assertEqual(http.requests[0].url, "https://api.openai.com/v1/embeddings")
        self.assertEqual(chat_status, 403)
        self.assertEqual(loads_bytes(chat_body)["error"]["code"], "forbidden")
        metrics = loads_bytes(metrics_body)
        self.assertEqual(metrics_status, 200)
        self.assertEqual(metrics["requests"], 2)
        self.assertIn("text-embedding-3-small", metrics["by_model"])

    async def test_audio_route_forwards_raw_multipart_body(self) -> None:
        http = FakeHttpClient()
        resolved_settings = settings()
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )
        body = b"--boundary\r\ncontent\r\n--boundary--"

        status, headers, _ = await asgi_request(
            app,
            "POST",
            "/v1/audio/transcriptions",
            None,
            headers={
                "content-type": "multipart/form-data; boundary=boundary",
                "x-llm-provider": "openai",
            },
            raw_body=body,
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["x-justfastllm-provider"], "openai")
        self.assertEqual(http.requests[0].url, "https://api.openai.com/v1/audio/transcriptions")
        self.assertEqual(http.requests[0].body, body)
        self.assertEqual(http.requests[0].headers["Content-Type"], "multipart/form-data; boundary=boundary")

    async def test_request_and_response_policies_are_enforced(self) -> None:
        blocked_response_http = FakeHttpClient(
            UpstreamResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                body=dumps_bytes({"choices": [{"message": {"content": "blocked phrase"}}]}),
            )
        )
        blocked_request_settings = settings(
            JUSTFASTLLM_POLICY_DENIED_MODELS="gpt-5-nano",
            JUSTFASTLLM_POLICY_ALLOWED_PROVIDERS="openai",
            JUSTFASTLLM_POLICY_REQUIRED_TAGS="prod",
        )
        blocked_request_app = create_app(
            settings=blocked_request_settings,
            provider_factory=ProviderFactory(blocked_request_settings, FakeHttpClient()),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )
        blocked_response_settings = settings(JUSTFASTLLM_POLICY_RESPONSE_BLOCK_PATTERNS="blocked phrase")
        blocked_response_app = create_app(
            settings=blocked_response_settings,
            provider_factory=ProviderFactory(blocked_response_settings, blocked_response_http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )

        request_status, _, request_body = await asgi_request(
            blocked_request_app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "model": "gpt-5-nano", "tags": ["prod"], "messages": [{"role": "user", "content": "hi"}]},
        )
        response_status, _, response_body = await asgi_request(
            blocked_response_app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "model": "gpt-5-nano", "messages": [{"role": "user", "content": "hi"}]},
        )

        self.assertEqual(request_status, 403)
        self.assertEqual(loads_bytes(request_body)["error"]["code"], "policy_violation")
        self.assertEqual(response_status, 403)
        self.assertEqual(loads_bytes(response_body)["error"]["code"], "policy_violation")

    async def test_provider_health_and_alerts_are_derived_from_usage(self) -> None:
        response = UpstreamResponse(
            status_code=500,
            headers={"content-type": "application/json"},
            body=dumps_bytes({"error": {"message": "upstream failed"}}),
        )
        http = FakeHttpClient(response)
        resolved_settings = settings(JUSTFASTLLM_MASTER_KEY="master-key")
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )
        for _ in range(5):
            await asgi_request(
                app,
                "POST",
                "/v1/chat/completions",
                {"provider": "openai", "model": "gpt-5-nano", "messages": [{"role": "user", "content": "hi"}]},
                headers={"authorization": "Bearer master-key"},
            )

        health_status, _, health_body = await asgi_request(
            app,
            "GET",
            "/v1/providers/health",
            headers={"authorization": "Bearer master-key"},
        )
        alert_status, _, alert_body = await asgi_request(
            app,
            "GET",
            "/v1/alerts",
            headers={"authorization": "Bearer master-key"},
        )

        openai_health = next(item for item in loads_bytes(health_body)["data"] if item["provider"] == "openai")
        self.assertEqual(health_status, 200)
        self.assertEqual(openai_health["status"], "critical")
        self.assertEqual(openai_health["errors"], 5)
        self.assertEqual(alert_status, 200)
        self.assertTrue(any(alert["type"] == "provider_error_rate" for alert in loads_bytes(alert_body)["data"]))

    async def test_plugin_hooks_transform_request_and_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_path = Path(tmpdir) / "gateway_plugin.py"
            plugin_path.write_text(
                """
def before_request(context, payload):
    payload["plugin_request"] = context["route"]
    return payload

def after_response(context, payload):
    payload["plugin_response"] = context["provider"]
    return payload
""".strip(),
                encoding="utf-8",
            )
            sys.path.insert(0, tmpdir)
            try:
                http = FakeHttpClient()
                resolved_settings = settings(JUSTFASTLLM_PLUGIN_MODULES="gateway_plugin")
                app = create_app(
                    settings=resolved_settings,
                    provider_factory=ProviderFactory(resolved_settings, http),
                    cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
                    logger=logging.getLogger("test"),
                )
                status, _, body = await asgi_request(
                    app,
                    "POST",
                    "/v1/chat/completions",
                    {"provider": "openai", "messages": [{"role": "user", "content": "hi"}]},
                )
            finally:
                sys.path.remove(tmpdir)
                sys.modules.pop("gateway_plugin", None)

        self.assertEqual(status, 200)
        self.assertEqual(loads_bytes(http.requests[0].body)["plugin_request"], "chat")
        self.assertEqual(loads_bytes(body)["plugin_response"], "openai")

    async def test_config_reload_replaces_live_gateway_settings(self) -> None:
        original = settings(JUSTFASTLLM_MASTER_KEY="master-key", JUSTFASTLLM_DEFAULT_PROVIDER="openai")
        updated = settings(
            JUSTFASTLLM_MASTER_KEY="rotated-master-key",
            JUSTFASTLLM_DEFAULT_PROVIDER="ollama",
            JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND="database",
            JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL="postgresql+psycopg://user:pass@localhost/jfl",
            JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE="jfl-state",
            JUSTFASTLLM_CACHE_BACKEND="database",
            JUSTFASTLLM_CACHE_DATABASE_URL="postgresql+psycopg://user:pass@localhost/jfl",
            JUSTFASTLLM_CACHE_DATABASE_TABLE="jfl-cache",
            JUSTFASTLLM_CACHE_PERSONAL_DATA_ALLOWED="true",
            JUSTFASTLLM_USER_MEMORY_BACKEND="database",
            JUSTFASTLLM_USER_MEMORY_DATABASE_URL="postgresql+psycopg://user:pass@localhost/jfl",
            JUSTFASTLLM_USER_MEMORY_DATABASE_TABLE="jfl-memory",
        )
        app = create_app(
            settings=original,
            provider_factory=ProviderFactory(original, FakeHttpClient()),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )

        with (
            patch("justfastllm.app.load_settings", return_value=updated),
            patch("justfastllm.proxy_control._load_sql_state", return_value=None),
            patch("justfastllm.proxy_control._save_sql_state", return_value=None),
        ):
            status, _, body = await asgi_request(
                app,
                "POST",
                "/v1/config/reload",
                headers={"authorization": "Bearer master-key"},
            )

        self.assertEqual(status, 200)
        self.assertTrue(loads_bytes(body)["reloaded"])
        self.assertEqual(app.settings.default_provider, "ollama")
        self.assertEqual(app.settings.master_key, "rotated-master-key")
        self.assertIsInstance(app.cache, DatabaseResponseCache)
        self.assertTrue(app.cache.strict)
        self.assertIsInstance(app.memory_store, DatabaseUserMemoryStore)
        self.assertTrue(app.memory_store.strict)
        self.assertEqual(app.control_plane.storage_backend, "database")
        self.assertTrue(app.control_plane.storage_strict)
        reload_audit = next(item for item in app.control_plane.audit_events() if item["target_type"] == "config")
        self.assertEqual(reload_audit["action"], "reload")
        self.assertEqual(reload_audit["actor"], "master")

    async def test_config_reload_loads_existing_database_control_plane_state(self) -> None:
        seed_control_plane = InMemoryProxyControlPlane()
        seed_control_plane.generate_key({"name": "persisted-after-reload"})
        persisted_state = seed_control_plane.export_state()
        original = settings(JUSTFASTLLM_MASTER_KEY="master-key", JUSTFASTLLM_DEFAULT_PROVIDER="openai")
        updated = settings(
            JUSTFASTLLM_MASTER_KEY="master-key",
            JUSTFASTLLM_DEFAULT_PROVIDER="ollama",
            JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND="database",
            JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL="postgresql+psycopg://user:pass@localhost/jfl",
            JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE="jfl-state",
        )
        app = create_app(
            settings=original,
            provider_factory=ProviderFactory(original, FakeHttpClient()),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )

        with (
            patch("justfastllm.app.load_settings", return_value=updated),
            patch("justfastllm.proxy_control._load_sql_state", return_value=persisted_state),
            patch("justfastllm.proxy_control._save_sql_state", return_value=None),
        ):
            status, _, body = await asgi_request(
                app,
                "POST",
                "/v1/config/reload",
                headers={"authorization": "Bearer master-key"},
            )

        self.assertEqual(status, 200)
        self.assertTrue(loads_bytes(body)["reloaded"])
        self.assertEqual(app.control_plane.storage_backend, "database")
        self.assertTrue(any(key["name"] == "persisted-after-reload" for key in app.control_plane.list_keys()))
        self.assertTrue(any(item["target_type"] == "config" and item["action"] == "reload" for item in app.control_plane.audit_events()))

    async def test_proxy_control_users_teams_service_keys_and_aliases(self) -> None:
        http = FakeHttpClient()
        resolved_settings = settings(
            JUSTFASTLLM_MASTER_KEY="master-key",
            JUSTFASTLLM_KEY_HEADER_NAME="x-justfastllm-key",
        )
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )
        admin_headers = {"x-justfastllm-key": "master-key"}

        user_status, _, _ = await asgi_request(
            app,
            "POST",
            "/v1/proxy/users",
            {"user_id": "user-1", "max_budget": 1, "models": ["gpt-5-nano"]},
            headers=admin_headers,
        )
        team_status, _, _ = await asgi_request(
            app,
            "POST",
            "/team/new",
            {"team_id": "team-1", "max_budget": 1, "models": ["gpt-5-nano"]},
            headers=admin_headers,
        )
        key_status, _, key_body = await asgi_request(
            app,
            "POST",
            "/service_account/key/generate",
            {
                "name": "worker",
                "user_id": "user-1",
                "team_id": "team-1",
                "models": ["gpt-5-nano"],
                "aliases": {"fast": "gpt-5-nano"},
                "budget_usd": 1,
            },
            headers=admin_headers,
        )
        token = loads_bytes(key_body)["key"]
        chat_status, _, _ = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "model": "fast", "messages": [{"role": "user", "content": "hi"}]},
            headers={"x-justfastllm-key": token},
        )
        user_info_status, _, user_info_body = await asgi_request(
            app,
            "GET",
            "/user/info?user_id=user-1",
            headers=admin_headers,
        )
        team_info_status, _, team_info_body = await asgi_request(
            app,
            "POST",
            "/team/info",
            {"team_id": "team-1"},
            headers=admin_headers,
        )
        metrics_status, _, metrics_body = await asgi_request(
            app,
            "GET",
            "/v1/metrics",
            headers=admin_headers,
        )

        self.assertEqual(user_status, 201)
        self.assertEqual(team_status, 201)
        self.assertEqual(key_status, 201)
        self.assertEqual(chat_status, 200)
        self.assertEqual(loads_bytes(http.requests[0].body)["model"], "gpt-5-nano")
        self.assertEqual(user_info_status, 200)
        self.assertEqual(len(loads_bytes(user_info_body)["keys"]), 1)
        self.assertEqual(team_info_status, 200)
        self.assertEqual(len(loads_bytes(team_info_body)["keys"]), 1)
        metrics = loads_bytes(metrics_body)
        self.assertEqual(metrics_status, 200)
        self.assertEqual(metrics["users"]["count"], 1)
        self.assertEqual(metrics["teams"]["count"], 1)

    async def test_proxy_control_update_delete_and_audit_routes(self) -> None:
        http = FakeHttpClient()
        resolved_settings = settings(JUSTFASTLLM_MASTER_KEY="master-key")
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )
        admin = {"authorization": "Bearer master-key"}

        await asgi_request(app, "POST", "/user/new", {"user_id": "user-1", "max_budget": 1}, headers=admin)
        await asgi_request(app, "POST", "/team/new", {"team_id": "team-1", "max_budget": 1}, headers=admin)
        _, _, key_body = await asgi_request(
            app,
            "POST",
            "/v1/keys",
            {"name": "mutable", "models": ["*"], "allowed_routes": ["chat"], "budget_usd": 1},
            headers=admin,
        )
        key_payload = loads_bytes(key_body)
        key_preview = key_payload["preview"]

        key_update_status, _, key_update_body = await asgi_request(
            app,
            "PATCH",
            "/v1/keys/update",
            {"key": key_preview, "disabled": True, "allowed_routes": ["embeddings"], "metadata": {"owner": "ops"}},
            headers=admin,
        )
        user_update_status, _, user_update_body = await asgi_request(
            app,
            "POST",
            "/user/update",
            {"user_id": "user-1", "user_email": "ops@example.com", "max_budget": 2},
            headers=admin,
        )
        team_update_status, _, team_update_body = await asgi_request(
            app,
            "POST",
            "/team/update",
            {"team_id": "team-1", "team_alias": "Ops", "max_budget": 3},
            headers=admin,
        )
        audit_status, _, audit_body = await asgi_request(app, "GET", "/v1/audit", headers=admin)
        user_delete_status, _, user_delete_body = await asgi_request(app, "POST", "/user/delete", {"user_id": "user-1"}, headers=admin)
        team_delete_status, _, team_delete_body = await asgi_request(app, "DELETE", "/v1/proxy/teams?team_id=team-1", headers=admin)
        key_delete_status, _, key_delete_body = await asgi_request(app, "DELETE", f"/v1/keys?key={key_preview}", headers=admin)

        self.assertEqual(key_update_status, 200)
        self.assertTrue(loads_bytes(key_update_body)["disabled"])
        self.assertEqual(loads_bytes(key_update_body)["allowed_routes"], ["embeddings"])
        self.assertEqual(user_update_status, 200)
        self.assertEqual(loads_bytes(user_update_body)["user_email"], "ops@example.com")
        self.assertEqual(team_update_status, 200)
        self.assertEqual(loads_bytes(team_update_body)["team_alias"], "Ops")
        self.assertEqual(audit_status, 200)
        audit_actions = [event["action"] for event in loads_bytes(audit_body)["data"]]
        self.assertIn("create", audit_actions)
        self.assertIn("update", audit_actions)
        self.assertEqual(user_delete_status, 200)
        self.assertTrue(loads_bytes(user_delete_body)["deleted"])
        self.assertEqual(team_delete_status, 200)
        self.assertTrue(loads_bytes(team_delete_body)["deleted"])
        self.assertEqual(key_delete_status, 200)
        self.assertTrue(loads_bytes(key_delete_body)["deleted"])

    async def test_privacy_export_and_erasure_routes_cover_user_subject_data(self) -> None:
        http = FakeHttpClient()
        resolved_settings = settings(JUSTFASTLLM_MASTER_KEY="master-key")
        cache = MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=True)
        cache.set("subject-cache-key", b"cached personal response")
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=cache,
            memory_store=MemoryUserMemoryStore(),
            logger=logging.getLogger("test"),
        )
        admin = {"authorization": "Bearer master-key"}

        await asgi_request(app, "POST", "/user/new", {"user_id": "user-1", "user_email": "ops@example.com"}, headers=admin)
        _, _, key_body = await asgi_request(
            app,
            "POST",
            "/v1/keys",
            {"name": "privacy-key", "user_id": "user-1", "models": ["*"]},
            headers=admin,
        )
        token = loads_bytes(key_body)["key"]
        await asgi_request(
            app,
            "PUT",
            "/v1/users/user-1/preferences",
            {"preferences": {"region": "EU"}},
            headers=admin,
        )
        await asgi_request(
            app,
            "PUT",
            "/v1/users/user-1/preferences",
            {"preferences": {"theme": "dark"}},
            headers={"authorization": f"Bearer {token}"},
        )
        create_request_status, _, create_request_body = await asgi_request(
            app,
            "POST",
            "/v1/privacy/requests",
            {"user_id": "user-1", "request_type": "access", "source": "email", "notes": "identity verified"},
            headers=admin,
        )
        request_id = loads_bytes(create_request_body)["request_id"]
        update_request_status, _, update_request_body = await asgi_request(
            app,
            "PATCH",
            f"/v1/privacy/requests/{request_id}",
            {"status": "completed", "notes": "export delivered"},
            headers=admin,
        )
        list_request_status, _, list_request_body = await asgi_request(
            app,
            "GET",
            "/v1/privacy/requests?user_id=user-1",
            headers=admin,
        )
        create_consent_status, _, create_consent_body = await asgi_request(
            app,
            "POST",
            "/v1/privacy/consents",
            {
                "user_id": "user-1",
                "purpose": "llm_gateway_processing",
                "notice_version": "privacy-v1",
                "source": "signup",
            },
            headers=admin,
        )
        consent_id = loads_bytes(create_consent_body)["consent_id"]
        withdraw_consent_status, _, withdraw_consent_body = await asgi_request(
            app,
            "POST",
            f"/v1/privacy/consents/{consent_id}/withdraw",
            {"source": "privacy-center"},
            headers=admin,
        )
        list_consent_status, _, list_consent_body = await asgi_request(
            app,
            "GET",
            "/v1/privacy/consents?user_id=user-1",
            headers=admin,
        )
        await asgi_request(app, "POST", "/v1/users/user-1/feedback", {"rating": 5}, headers=admin)
        await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "model": "gpt-5-nano", "messages": [{"role": "user", "content": "hi"}]},
            headers={"authorization": f"Bearer {token}"},
        )

        unauth_status, _, _ = await asgi_request(app, "GET", "/v1/privacy/users/user-1/export")
        export_status, _, export_body = await asgi_request(app, "GET", "/v1/privacy/users/user-1/export", headers=admin)
        erase_status, _, erase_body = await asgi_request(app, "DELETE", "/v1/privacy/users/user-1/erase", headers=admin)
        post_erase_export_status, _, post_erase_export_body = await asgi_request(app, "GET", "/v1/privacy/users/user-1/export", headers=admin)
        keys_status, _, keys_body = await asgi_request(app, "GET", "/v1/keys", headers=admin)

        self.assertEqual(unauth_status, 401)
        self.assertEqual(create_request_status, 201)
        self.assertEqual(update_request_status, 200)
        self.assertEqual(loads_bytes(update_request_body)["status"], "completed")
        self.assertEqual(list_request_status, 200)
        listed_requests = loads_bytes(list_request_body)
        self.assertEqual(len(listed_requests["data"]), 1)
        self.assertIn("withdrawal", listed_requests["supported_request_types"])
        self.assertIn("nomination", listed_requests["supported_request_types"])
        self.assertIn("appeal", listed_requests["supported_request_types"])
        self.assertIn("in_progress", listed_requests["supported_statuses"])
        self.assertEqual(create_consent_status, 201)
        self.assertEqual(withdraw_consent_status, 200)
        self.assertEqual(loads_bytes(withdraw_consent_body)["status"], "withdrawn")
        self.assertEqual(list_consent_status, 200)
        listed_consents = loads_bytes(list_consent_body)
        self.assertEqual(listed_consents["data"][0]["consent_id"], consent_id)
        self.assertIn("withdrawn", listed_consents["supported_statuses"])
        self.assertEqual(export_status, 200)
        exported = loads_bytes(export_body)
        self.assertEqual(exported["user"]["user_email"], "ops@example.com")
        self.assertEqual(exported["memory"]["preferences"]["region"], "EU")
        self.assertEqual(exported["memory"]["preferences"]["theme"], "dark")
        self.assertEqual(len(exported["memory"]["feedback"]), 1)
        self.assertTrue(any(str(item["actor"]).startswith("virtual_key:sk-jfl-") for item in exported["audit_events"]))
        self.assertEqual(len(exported["usage_events"]), 1)
        self.assertTrue(any(item["outcome"] == "success" for item in exported["auth_events"]))
        self.assertEqual(exported["privacy_requests"][0]["request_id"], request_id)
        self.assertEqual(exported["consents"][0]["consent_id"], consent_id)
        self.assertEqual(exported["consents"][0]["status"], "withdrawn")
        self.assertEqual(erase_status, 200)
        erased = loads_bytes(erase_body)
        self.assertTrue(erased["user_deleted"])
        self.assertEqual(erased["keys_deleted"], 1)
        self.assertEqual(erased["usage_events_deleted"], 1)
        self.assertGreaterEqual(erased["auth_events_pseudonymized"], 1)
        self.assertEqual(erased["privacy_requests_pseudonymized"], 1)
        self.assertEqual(erased["consents_pseudonymized"], 1)
        self.assertGreaterEqual(erased["audit_events_pseudonymized"], 1)
        self.assertTrue(erased["memory"]["preferences_deleted"])
        self.assertTrue(erased["memory"]["feedback_deleted"])
        self.assertEqual(erased["response_cache_deleted"], 2)
        self.assertIsNone(cache.get("subject-cache-key"))
        self.assertEqual(post_erase_export_status, 200)
        post_erase = loads_bytes(post_erase_export_body)
        self.assertIsNone(post_erase["user"])
        self.assertEqual(post_erase["memory"]["preferences"], {})
        self.assertEqual(post_erase["usage_events"], [])
        self.assertEqual(keys_status, 200)
        self.assertEqual(loads_bytes(keys_body)["data"], [])
        self.assertTrue(any(item["actor"] == "[erased]" for item in app.control_plane.audit_events()))
        self.assertFalse(any("user-1" in str(item["actor"]) or "sk-jfl-" in str(item["actor"]) for item in app.control_plane.audit_events()))

    async def test_privacy_request_register_supports_dpdp_workflows_and_rejects_invalid_values(self) -> None:
        app = create_app(settings=settings(JUSTFASTLLM_MASTER_KEY="master-key"), logger=logging.getLogger("test"))
        admin = {"authorization": "Bearer master-key"}

        for request_type in ("withdrawal", "nomination", "appeal"):
            status, _, body = await asgi_request(
                app,
                "POST",
                "/v1/privacy/requests",
                {"user_id": "user-1", "request_type": request_type, "status": "in_progress"},
                headers=admin,
            )
            self.assertEqual(status, 201)
            self.assertEqual(loads_bytes(body)["request_type"], request_type)
            self.assertEqual(loads_bytes(body)["status"], "in_progress")

        invalid_type_status, _, invalid_type_body = await asgi_request(
            app,
            "POST",
            "/v1/privacy/requests",
            {"user_id": "user-1", "request_type": "anything"},
            headers=admin,
        )
        _, _, created_body = await asgi_request(
            app,
            "POST",
            "/v1/privacy/requests",
            {"user_id": "user-1", "request_type": "access"},
            headers=admin,
        )
        request_id = loads_bytes(created_body)["request_id"]
        overdue_status, _, overdue_body = await asgi_request(
            app,
            "POST",
            "/v1/privacy/requests",
            {"user_id": "user-1", "request_type": "erasure", "due_at": 1},
            headers=admin,
        )
        invalid_status, _, invalid_status_body = await asgi_request(
            app,
            "PATCH",
            f"/v1/privacy/requests/{request_id}",
            {"status": "maybe"},
            headers=admin,
        )
        compliance_status, _, compliance_body = await asgi_request(
            app,
            "GET",
            "/v1/compliance/status",
            headers=admin,
        )

        self.assertEqual(invalid_type_status, 400)
        self.assertEqual(loads_bytes(invalid_type_body)["error"]["code"], "bad_request")
        self.assertEqual(overdue_status, 201)
        self.assertEqual(loads_bytes(overdue_body)["request_type"], "erasure")
        self.assertEqual(invalid_status, 400)
        self.assertEqual(loads_bytes(invalid_status_body)["error"]["code"], "bad_request")
        self.assertEqual(compliance_status, 200)
        privacy_summary = loads_bytes(compliance_body)["privacy_requests"]
        self.assertEqual(privacy_summary["total"], 5)
        self.assertEqual(privacy_summary["overdue_open"], 1)
        self.assertEqual(privacy_summary["by_type"]["withdrawal"], 1)
        self.assertEqual(privacy_summary["by_type"]["nomination"], 1)
        self.assertEqual(privacy_summary["by_type"]["appeal"], 1)
        self.assertIn("withdrawal", privacy_summary["supported_request_types"])

    async def test_user_correction_route_updates_subject_export(self) -> None:
        app = create_app(settings=settings(JUSTFASTLLM_MASTER_KEY="master-key"), logger=logging.getLogger("test"))
        admin = {"authorization": "Bearer master-key"}

        create_status, _, _ = await asgi_request(
            app,
            "POST",
            "/v1/proxy/users",
            {"user_id": "user-1", "user_email": "old@example.com", "metadata": {"name": "Old"}},
            headers=admin,
        )
        update_status, _, update_body = await asgi_request(
            app,
            "PATCH",
            "/v1/proxy/users/update",
            {"user_id": "user-1", "user_email": "new@example.com", "metadata": {"name": "New"}},
            headers=admin,
        )
        export_status, _, export_body = await asgi_request(
            app,
            "GET",
            "/v1/privacy/users/user-1/export",
            headers=admin,
        )
        openapi_status, _, openapi_body = await asgi_request(app, "GET", "/openapi.json")

        self.assertEqual(create_status, 201)
        self.assertEqual(update_status, 200)
        self.assertEqual(loads_bytes(update_body)["user_email"], "new@example.com")
        self.assertEqual(export_status, 200)
        exported = loads_bytes(export_body)
        self.assertEqual(exported["user"]["user_email"], "new@example.com")
        self.assertEqual(exported["user"]["metadata"]["name"], "New")
        self.assertEqual(openapi_status, 200)
        self.assertIn("/v1/proxy/users/update", loads_bytes(openapi_body)["paths"])

    async def test_team_correction_route_updates_team_record(self) -> None:
        app = create_app(settings=settings(JUSTFASTLLM_MASTER_KEY="master-key"), logger=logging.getLogger("test"))
        admin = {"authorization": "Bearer master-key"}

        create_status, _, _ = await asgi_request(
            app,
            "POST",
            "/v1/proxy/teams",
            {"team_id": "team-1", "team_alias": "Old", "metadata": {"owner": "legacy"}},
            headers=admin,
        )
        update_status, _, update_body = await asgi_request(
            app,
            "PATCH",
            "/v1/proxy/teams/update",
            {"team_id": "team-1", "team_alias": "New", "metadata": {"owner": "ops"}},
            headers=admin,
        )
        info_status, _, info_body = await asgi_request(
            app,
            "GET",
            "/v1/proxy/teams/info?team_id=team-1",
            headers=admin,
        )
        openapi_status, _, openapi_body = await asgi_request(app, "GET", "/openapi.json")

        self.assertEqual(create_status, 201)
        self.assertEqual(update_status, 200)
        self.assertEqual(loads_bytes(update_body)["team_alias"], "New")
        self.assertEqual(loads_bytes(update_body)["metadata"]["owner"], "ops")
        self.assertEqual(info_status, 200)
        self.assertEqual(loads_bytes(info_body)["team_alias"], "New")
        self.assertEqual(loads_bytes(info_body)["metadata"]["owner"], "ops")
        self.assertEqual(openapi_status, 200)
        self.assertIn("/v1/proxy/teams/update", loads_bytes(openapi_body)["paths"])

    async def test_privacy_export_and_erasure_cover_header_user_usage_events(self) -> None:
        http = FakeHttpClient()
        resolved_settings = settings(JUSTFASTLLM_MASTER_KEY="master-key")
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, http),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            logger=logging.getLogger("test"),
        )
        admin = {"authorization": "Bearer master-key"}
        traffic_headers = {"authorization": "Bearer master-key", "x-user-id": "header-user", "x-team-id": "team-a"}

        chat_status, _, _ = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {"provider": "openai", "model": "gpt-5-nano", "messages": [{"role": "user", "content": "hi"}]},
            headers=traffic_headers,
        )
        export_status, _, export_body = await asgi_request(
            app,
            "GET",
            "/v1/privacy/users/header-user/export",
            headers=admin,
        )
        erase_status, _, erase_body = await asgi_request(
            app,
            "DELETE",
            "/v1/privacy/users/header-user/erase",
            headers=admin,
        )
        post_erase_status, _, post_erase_body = await asgi_request(
            app,
            "GET",
            "/v1/privacy/users/header-user/export",
            headers=admin,
        )

        self.assertEqual(chat_status, 200)
        self.assertEqual(export_status, 200)
        exported = loads_bytes(export_body)
        self.assertEqual(len(exported["usage_events"]), 1)
        self.assertEqual(exported["usage_events"][0]["user_id"], "header-user")
        self.assertEqual(exported["usage_events"][0]["team_id"], "team-a")
        self.assertTrue(any(item["user_id"] == "header-user" and item["path"] == "/v1/chat/completions" for item in exported["access_events"]))
        self.assertTrue(any(item["user_id"] == "header-user" and item["auth_type"] == "admin" for item in exported["auth_events"]))
        self.assertEqual(erase_status, 200)
        erased = loads_bytes(erase_body)
        self.assertEqual(erased["usage_events_deleted"], 1)
        self.assertGreaterEqual(erased["access_events_pseudonymized"], 1)
        self.assertGreaterEqual(erased["auth_events_pseudonymized"], 1)
        self.assertEqual(post_erase_status, 200)
        post_erase = loads_bytes(post_erase_body)
        self.assertEqual(post_erase["usage_events"], [])
        self.assertEqual(post_erase["access_events"], [])
        self.assertEqual(post_erase["auth_events"], [])

    async def test_compliance_status_and_retention_settings(self) -> None:
        resolved_settings = settings(
            JUSTFASTLLM_MASTER_KEY="master-key",
            JUSTFASTLLM_USAGE_RETENTION_DAYS="30",
            JUSTFASTLLM_AUDIT_RETENTION_DAYS="180",
            JUSTFASTLLM_PRIVACY_CONTACT="privacy@example.com",
            JUSTFASTLLM_SECURITY_CONTACT="security@example.com",
            JUSTFASTLLM_SUBPROCESSORS_URL="https://example.com/subprocessors",
            JUSTFASTLLM_DPA_URL="https://example.com/dpa",
            JUSTFASTLLM_CACHE_ENABLED="false",
            JUSTFASTLLM_USER_MEMORY_ENABLED="false",
            JUSTFASTLLM_REQUIRE_COMPLIANCE_READY="true",
            JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND="file",
            JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH="/tmp/justfastllm-compliance-report.json",
        )
        app = create_app(
            settings=resolved_settings,
            logger=logging.getLogger("test"),
        )

        status, _, body = await asgi_request(
            app,
            "GET",
            "/v1/compliance/status",
            headers={"authorization": "Bearer master-key"},
        )
        evidence_status, _, evidence_body = await asgi_request(
            app,
            "GET",
            "/v1/compliance/evidence",
            headers={"authorization": "Bearer master-key"},
        )
        report_status, _, report_body = await asgi_request(
            app,
            "GET",
            "/v1/compliance/report",
            headers={"authorization": "Bearer master-key"},
        )
        integrity_status, _, integrity_body = await asgi_request(
            app,
            "GET",
            "/v1/compliance/integrity",
            headers={"authorization": "Bearer master-key"},
        )

        payload = loads_bytes(body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["compliance_mode"])
        self.assertEqual(payload["retention"]["usage_retention_days"], 30)
        self.assertEqual(payload["retention"]["audit_retention_days"], 180)
        self.assertEqual(payload["retention"]["access_retention_days"], 180)
        self.assertEqual(payload["retention"]["auth_retention_days"], 180)
        self.assertEqual(payload["retention"]["retention_policy"]["access_events"], "audit_retention_days")
        self.assertEqual(payload["retention"]["retention_policy"]["auth_events"], "audit_retention_days")
        self.assertEqual(payload["privacy_contacts"]["privacy"], "privacy@example.com")
        self.assertIn("gdpr", payload["framework_sources"])
        self.assertIn("soc2", payload["framework_sources"])
        self.assertEqual(payload["framework_sources"]["gdpr"]["reviewed_at"], "2026-09-26")
        self.assertEqual(
            payload["framework_sources"]["gdpr"]["references"],
            ["https://eur-lex.europa.eu/eli/reg/2016/679/oj"],
        )
        self.assertIn(
            "https://www.edpb.europa.eu/topics/ai-and-technology/privacy-by-design-and-by-default_en",
            payload["framework_sources"]["edpb"]["references"],
        )
        self.assertIn(
            "https://www.aicpa-cima.com/resources/landing/system-and-organization-controls-soc-suite-of-services",
            payload["framework_sources"]["soc2"]["references"],
        )
        self.assertIn(
            "https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf",
            payload["framework_sources"]["dpdp_india"]["references"],
        )
        self.assertEqual(payload["frameworks"]["ddpa"], "alias_for_india_dpdp_in_project_docs")
        self.assertTrue(payload["controls"]["subject_access_export"])
        self.assertTrue(payload["controls"]["control_plane_storage_writable"])
        self.assertTrue(payload["controls"]["durable_user_memory"])
        self.assertTrue(payload["controls"]["response_cache_storage_ready"])
        self.assertFalse(payload["controls"]["response_cache_enabled"])
        self.assertTrue(payload["controls"]["security_headers"])
        self.assertEqual(evidence_status, 200)
        evidence = loads_bytes(evidence_body)["data"]
        self.assertTrue(any(item["control_id"] == "data_subject_rights" for item in evidence))
        self.assertTrue(any(item["automated"] is False for item in evidence))
        self.assertTrue(any(item["control_id"] == "breach_and_incident_response" for item in evidence))
        self.assertTrue(any(item["control_id"] == "retention_and_storage_limitation" for item in evidence))
        self.assertTrue(all("mapped_requirements" in item for item in evidence))
        self.assertEqual(report_status, 200)
        report = loads_bytes(report_body)
        self.assertEqual(report["status"], "technical_controls_ready")
        self.assertTrue(report["assurance"]["automated_deployment_gate_ready"])
        self.assertEqual(report["assurance"]["ddpa_note"], "DDPA is treated as the user's shorthand for India's DPDP framework in this project.")
        self.assertIn("dpdp_india", report["framework_sources"])
        self.assertTrue(report["readiness"]["checks"]["control_plane_storage_writable"])
        self.assertFalse(report["assurance"]["can_claim_automatic_legal_or_attested_compliance"])
        operator_control_ids = {item["control_id"] for item in report["operator_required_controls"]}
        self.assertIn("soc2_independent_examination", operator_control_ids)
        self.assertIn("infrastructure_security_and_encryption", operator_control_ids)
        self.assertIn("identity_verification_and_rights_operations", operator_control_ids)
        integrity = loads_bytes(integrity_body)
        self.assertEqual(integrity_status, 200)
        self.assertEqual(integrity["object"], "evidence_integrity")
        self.assertEqual(len(integrity["aggregate_sha256"]), 64)
        self.assertIn("audit_events", integrity["categories"])
        self.assertTrue(integrity["categories"]["audit_events"]["hash_chain"]["valid"])
        self.assertTrue(integrity["categories"]["access_events"]["hash_chain"]["valid"])
        self.assertTrue(integrity["categories"]["auth_events"]["hash_chain"]["valid"])

    async def test_compliance_health_gate_rejects_nondurable_user_memory(self) -> None:
        resolved_settings = settings(
            JUSTFASTLLM_MASTER_KEY="master-key",
            JUSTFASTLLM_REQUIRE_COMPLIANCE_READY="true",
            JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND="file",
            JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH="/tmp/justfastllm-compliance-memory.json",
            JUSTFASTLLM_USER_MEMORY_BACKEND="memory",
            JUSTFASTLLM_PRIVACY_CONTACT="privacy@example.com",
            JUSTFASTLLM_SECURITY_CONTACT="security@example.com",
            JUSTFASTLLM_SUBPROCESSORS_URL="https://example.com/subprocessors",
            JUSTFASTLLM_DPA_URL="https://example.com/dpa",
        )
        app = create_app(settings=resolved_settings, logger=logging.getLogger("test"))

        status, _, body = await asgi_request(app, "GET", "/health")

        payload = loads_bytes(body)
        self.assertEqual(status, 503)
        self.assertIn("durable_user_memory_configured", payload["compliance"]["missing"])

    async def test_proxy_control_state_persists_to_json_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "proxy-control.json"
            first_http = FakeHttpClient()
            first_settings = settings(
                JUSTFASTLLM_MASTER_KEY="master-key",
                JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH=str(state_path),
            )
            first_app = create_app(
                settings=first_settings,
                provider_factory=ProviderFactory(first_settings, first_http),
                cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
                logger=logging.getLogger("test"),
            )
            await asgi_request(
                first_app,
                "POST",
                "/user/new",
                {"user_id": "user-1", "max_budget": 1},
                headers={"authorization": "Bearer master-key"},
            )
            _, _, key_body = await asgi_request(
                first_app,
                "POST",
                "/v1/keys",
                {"name": "persisted", "user_id": "user-1", "models": ["*"], "budget_usd": 1},
                headers={"authorization": "Bearer master-key"},
            )
            token = loads_bytes(key_body)["key"]
            await asgi_request(
                first_app,
                "POST",
                "/v1/privacy/requests",
                {"user_id": "user-1", "request_type": "erasure", "source": "support"},
                headers={"authorization": "Bearer master-key"},
            )
            await asgi_request(
                first_app,
                "POST",
                "/v1/chat/completions",
                {"provider": "openai", "model": "gpt-5-nano", "messages": [{"role": "user", "content": "hi"}]},
                headers={"authorization": f"Bearer {token}"},
            )

            second_http = FakeHttpClient()
            second_settings = settings(
                JUSTFASTLLM_MASTER_KEY="master-key",
                JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH=str(state_path),
            )
            second_app = create_app(
                settings=second_settings,
                provider_factory=ProviderFactory(second_settings, second_http),
                cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
                logger=logging.getLogger("test"),
            )
            keys_status, _, keys_body = await asgi_request(
                second_app,
                "GET",
                "/v1/keys",
                headers={"authorization": "Bearer master-key"},
            )
            chat_status, _, _ = await asgi_request(
                second_app,
                "POST",
                "/v1/chat/completions",
                {"provider": "openai", "model": "gpt-5-nano", "messages": [{"role": "user", "content": "again"}]},
                headers={"authorization": f"Bearer {token}"},
            )
            user_status, _, user_body = await asgi_request(
                second_app,
                "GET",
                "/user/info?user_id=user-1",
                headers={"authorization": "Bearer master-key"},
            )
            audit_status, _, audit_body = await asgi_request(
                second_app,
                "GET",
                "/v1/audit",
                headers={"authorization": "Bearer master-key"},
            )
            access_status, _, access_body = await asgi_request(
                second_app,
                "GET",
                "/v1/access",
                headers={"authorization": "Bearer master-key"},
            )
            auth_status, _, auth_body = await asgi_request(
                second_app,
                "GET",
                "/v1/auth/events",
                headers={"authorization": "Bearer master-key"},
            )
            privacy_requests_status, _, privacy_requests_body = await asgi_request(
                second_app,
                "GET",
                "/v1/privacy/requests?user_id=user-1",
                headers={"authorization": "Bearer master-key"},
            )

        self.assertEqual(keys_status, 200)
        self.assertEqual(len(loads_bytes(keys_body)["data"]), 1)
        self.assertEqual(chat_status, 200)
        self.assertEqual(user_status, 200)
        self.assertGreater(loads_bytes(user_body)["spend_usd"], 0)
        self.assertEqual(audit_status, 200)
        self.assertGreaterEqual(len(loads_bytes(audit_body)["data"]), 2)
        self.assertEqual(access_status, 200)
        self.assertTrue(any(item["path"] == "/v1/chat/completions" for item in loads_bytes(access_body)["data"]))
        self.assertEqual(auth_status, 200)
        self.assertTrue(any(item["route"] == "chat" and item["outcome"] == "success" for item in loads_bytes(auth_body)["data"]))
        self.assertEqual(privacy_requests_status, 200)
        self.assertEqual(loads_bytes(privacy_requests_body)["data"][0]["request_type"], "erasure")

    async def test_database_control_plane_persists_endpoint_auth_audit_and_privacy_evidence(self) -> None:
        stored_state: dict[str, object] = {}

        def save_state(url: str, table_name: str, state: dict[str, object]) -> None:
            self.assertEqual(url, "postgresql+psycopg://user:pass@localhost:5432/jfl")
            self.assertEqual(table_name, "jfl_compliance_state")
            stored_state.clear()
            stored_state.update(json.loads(json.dumps(state)))

        def load_state(url: str, table_name: str) -> dict[str, object] | None:
            self.assertEqual(url, "postgresql+psycopg://user:pass@localhost:5432/jfl")
            self.assertEqual(table_name, "jfl_compliance_state")
            return json.loads(json.dumps(stored_state)) if stored_state else None

        with (
            patch("justfastllm.proxy_control._save_sql_state", side_effect=save_state),
            patch("justfastllm.proxy_control._load_sql_state", side_effect=load_state),
        ):
            database_url = "postgresql+psycopg://user:pass@localhost:5432/jfl"
            first_http = FakeHttpClient()
            first_settings = settings(
                JUSTFASTLLM_MASTER_KEY="master-key",
                JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND="database",
                JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL=database_url,
                JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE="jfl-compliance-state",
            )
            first_app = create_app(
                settings=first_settings,
                provider_factory=ProviderFactory(first_settings, first_http),
                cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
                logger=logging.getLogger("test"),
            )
            admin = {"authorization": "Bearer master-key"}
            await asgi_request(first_app, "POST", "/user/new", {"user_id": "user-db", "max_budget": 1}, headers=admin)
            _, _, key_body = await asgi_request(
                first_app,
                "POST",
                "/v1/keys",
                {"name": "db-persisted", "user_id": "user-db", "models": ["*"], "budget_usd": 1},
                headers=admin,
            )
            token = loads_bytes(key_body)["key"]
            await asgi_request(
                first_app,
                "POST",
                "/v1/privacy/requests",
                {"user_id": "user-db", "request_type": "access", "source": "support", "notes": "verified"},
                headers=admin,
            )
            await asgi_request(
                first_app,
                "POST",
                "/v1/chat/completions",
                {"provider": "openai", "model": "gpt-5-nano", "messages": [{"role": "user", "content": "persist"}]},
                headers={"authorization": f"Bearer {token}"},
            )

            second_http = FakeHttpClient()
            second_settings = settings(
                JUSTFASTLLM_MASTER_KEY="master-key",
                JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND="database",
                JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL=database_url,
                JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE="jfl-compliance-state",
            )
            second_app = create_app(
                settings=second_settings,
                provider_factory=ProviderFactory(second_settings, second_http),
                cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
                logger=logging.getLogger("test"),
            )
            keys_status, _, keys_body = await asgi_request(second_app, "GET", "/v1/keys", headers=admin)
            usage_status, _, usage_body = await asgi_request(second_app, "GET", "/v1/usage", headers=admin)
            audit_status, _, audit_body = await asgi_request(second_app, "GET", "/v1/audit", headers=admin)
            access_status, _, access_body = await asgi_request(second_app, "GET", "/v1/access", headers=admin)
            auth_status, _, auth_body = await asgi_request(second_app, "GET", "/v1/auth/events", headers=admin)
            privacy_status, _, privacy_body = await asgi_request(
                second_app,
                "GET",
                "/v1/privacy/requests?user_id=user-db",
                headers=admin,
            )
            integrity_status, _, integrity_body = await asgi_request(
                second_app,
                "GET",
                "/v1/compliance/integrity",
                headers=admin,
            )

        self.assertEqual(keys_status, 200)
        self.assertEqual(loads_bytes(keys_body)["data"][0]["name"], "db-persisted")
        self.assertEqual(usage_status, 200)
        self.assertTrue(any(item["user_id"] == "user-db" for item in loads_bytes(usage_body)["data"]))
        self.assertEqual(audit_status, 200)
        self.assertTrue(any(item["target_type"] == "privacy_request" for item in loads_bytes(audit_body)["data"]))
        self.assertEqual(access_status, 200)
        self.assertTrue(any(item["path"] == "/v1/chat/completions" for item in loads_bytes(access_body)["data"]))
        self.assertEqual(auth_status, 200)
        self.assertTrue(any(item["auth_type"] == "virtual_key" and item["outcome"] == "success" for item in loads_bytes(auth_body)["data"]))
        self.assertEqual(privacy_status, 200)
        self.assertEqual(loads_bytes(privacy_body)["data"][0]["request_type"], "access")
        self.assertEqual(integrity_status, 200)
        integrity = loads_bytes(integrity_body)
        self.assertEqual(integrity["storage"]["backend"], "database")
        self.assertEqual(integrity["storage"]["database_table"], "jfl_compliance_state")
        self.assertGreater(integrity["categories"]["auth_events"]["count"], 0)
        self.assertGreater(integrity["categories"]["access_events"]["count"], 0)

    async def test_public_endpoint_access_event_persists_to_database_state(self) -> None:
        stored_state: dict[str, object] = {}
        database_url = "postgresql+psycopg://user:pass@localhost:5432/jfl"

        def save_state(url: str, table_name: str, state: dict[str, object]) -> None:
            self.assertEqual(url, database_url)
            self.assertEqual(table_name, "jfl_endpoint_state")
            stored_state.clear()
            stored_state.update(json.loads(json.dumps(state)))

        def load_state(url: str, table_name: str) -> dict[str, object] | None:
            self.assertEqual(url, database_url)
            self.assertEqual(table_name, "jfl_endpoint_state")
            return json.loads(json.dumps(stored_state)) if stored_state else None

        with (
            patch("justfastllm.proxy_control._save_sql_state", side_effect=save_state),
            patch("justfastllm.proxy_control._load_sql_state", side_effect=load_state),
        ):
            first_settings = settings(
                JUSTFASTLLM_MASTER_KEY="master-key",
                JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND="database",
                JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL=database_url,
                JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE="jfl-endpoint-state",
            )
            first_app = create_app(
                settings=first_settings,
                cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
                logger=logging.getLogger("test"),
            )
            openapi_status, _, _ = await asgi_request(first_app, "GET", "/openapi.json")

            second_settings = settings(
                JUSTFASTLLM_MASTER_KEY="master-key",
                JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND="database",
                JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL=database_url,
                JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE="jfl-endpoint-state",
            )
            second_app = create_app(
                settings=second_settings,
                cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
                logger=logging.getLogger("test"),
            )
            access_status, _, access_body = await asgi_request(
                second_app,
                "GET",
                "/v1/access",
                headers={"authorization": "Bearer master-key"},
            )

        self.assertEqual(openapi_status, 200)
        self.assertEqual(access_status, 200)
        self.assertTrue(any(item["path"] == "/openapi.json" and item["status_code"] == 200 for item in loads_bytes(access_body)["data"]))

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
        preference_audit = next(item for item in app.control_plane.audit_events() if item["target_type"] == "user_preferences")
        self.assertEqual(preference_audit["target_id"], "user-1")
        self.assertEqual(preference_audit["actor"], "anonymous")
        self.assertEqual(chat_status, 200)
        self.assertEqual(request_body["messages"][0]["role"], "system")
        self.assertIn("tone: warm", request_body["messages"][0]["content"])

    async def test_user_memory_routes_require_configured_auth_and_scope_virtual_keys(self) -> None:
        resolved_settings = settings(JUSTFASTLLM_MASTER_KEY="master-key")
        app = create_app(
            settings=resolved_settings,
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            memory_store=MemoryUserMemoryStore(),
            logger=logging.getLogger("test"),
        )
        admin = {"authorization": "Bearer master-key"}
        _, _, key_body = await asgi_request(
            app,
            "POST",
            "/v1/keys",
            {"name": "memory-key", "user_id": "user-1", "models": ["*"]},
            headers=admin,
        )
        token = loads_bytes(key_body)["key"]

        unauth_status, _, unauth_body = await asgi_request(
            app,
            "PUT",
            "/v1/users/user-1/preferences",
            {"preferences": {"tone": "warm"}},
        )
        own_status, _, own_body = await asgi_request(
            app,
            "PUT",
            "/v1/users/user-1/preferences",
            {"preferences": {"tone": "warm"}},
            headers={"authorization": f"Bearer {token}"},
        )
        other_status, _, other_body = await asgi_request(
            app,
            "GET",
            "/v1/users/user-2/preferences",
            headers={"authorization": f"Bearer {token}"},
        )

        self.assertEqual(unauth_status, 401)
        self.assertEqual(loads_bytes(unauth_body)["error"]["code"], "unauthorized")
        self.assertEqual(own_status, 200)
        self.assertEqual(loads_bytes(own_body)["preferences"]["tone"], "warm")
        preference_audit = next(item for item in app.control_plane.audit_events() if item["target_type"] == "user_preferences")
        self.assertTrue(str(preference_audit["actor"]).startswith("virtual_key:sk-jfl-"))
        self.assertEqual(other_status, 403)
        self.assertEqual(loads_bytes(other_body)["error"]["code"], "forbidden")

    async def test_unhandled_errors_do_not_leak_exception_details(self) -> None:
        logger = logging.getLogger("test-unhandled-redaction")
        app = create_app(
            settings=settings(),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60, enabled=False),
            memory_store=MemoryUserMemoryStore(),
            logger=logger,
        )

        with patch.object(app.memory_store, "get_feedback", side_effect=RuntimeError("secret-token=abc123")):
            with self.assertLogs(logger, level="ERROR") as captured:
                status, _, body = await asgi_request(app, "GET", "/v1/users/user-1/feedback")

        payload = loads_bytes(body)
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"]["code"], "internal_error")
        self.assertEqual(payload["error"]["message"], "internal server error")
        self.assertNotIn("abc123", body.decode("utf-8"))
        self.assertIn("RuntimeError", "\n".join(captured.output))
        self.assertNotIn("abc123", "\n".join(captured.output))

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
        self.assertTrue(any(item["target_type"] == "user_feedback" and item["target_id"] == "user-1" for item in app.control_plane.audit_events()))

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

    async def test_skills_endpoints_require_master_key_when_configured(self) -> None:
        app = create_app(
            settings=settings(JUSTFASTLLM_MASTER_KEY="master-key"),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            logger=logging.getLogger("test"),
        )

        unauth_list_status, _, unauth_list_body = await asgi_request(app, "GET", "/v1/skills")
        unauth_run_status, _, unauth_run_body = await asgi_request(
            app,
            "POST",
            "/v1/skills/echo/run",
            {"arguments": {"value": "hello"}},
        )
        auth_list_status, _, auth_list_body = await asgi_request(
            app,
            "GET",
            "/v1/skills",
            headers={"authorization": "Bearer master-key"},
        )
        auth_run_status, _, auth_run_body = await asgi_request(
            app,
            "POST",
            "/v1/skills/echo/run",
            {"arguments": {"value": "hello"}},
            headers={"authorization": "Bearer master-key"},
        )

        self.assertEqual(unauth_list_status, 401)
        self.assertEqual(loads_bytes(unauth_list_body)["error"]["code"], "unauthorized")
        self.assertEqual(unauth_run_status, 401)
        self.assertEqual(loads_bytes(unauth_run_body)["error"]["code"], "unauthorized")
        self.assertEqual(auth_list_status, 200)
        self.assertEqual(loads_bytes(auth_list_body)["object"], "list")
        self.assertEqual(auth_run_status, 200)
        self.assertEqual(loads_bytes(auth_run_body)["result"], {"value": "hello"})

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
        self.assertTrue(any(item["target_type"] == "guardrails" and item["action"] == "update" for item in app.control_plane.audit_events()))

    async def test_guardrails_route_requires_master_key_when_configured(self) -> None:
        resolved_settings = settings(JUSTFASTLLM_MASTER_KEY="master-key")
        app = create_app(
            settings=resolved_settings,
            provider_factory=ProviderFactory(resolved_settings, FakeHttpClient()),
            cache=MemoryResponseCache(max_items=10, ttl_seconds=60),
            guardrails=Guardrails(max_message_chars=100, block_patterns=("blocked",)),
            logger=logging.getLogger("test"),
        )

        unauth_get_status, _, unauth_get_body = await asgi_request(app, "GET", "/v1/guardrails")
        unauth_patch_status, _, unauth_patch_body = await asgi_request(
            app,
            "PATCH",
            "/v1/guardrails",
            {"disable": ["block_patterns"]},
        )
        auth_get_status, _, auth_get_body = await asgi_request(
            app,
            "GET",
            "/v1/guardrails",
            headers={"authorization": "Bearer master-key"},
        )
        auth_patch_status, _, auth_patch_body = await asgi_request(
            app,
            "PATCH",
            "/v1/guardrails",
            {"disable": ["block_patterns"]},
            headers={"authorization": "Bearer master-key"},
        )

        self.assertEqual(unauth_get_status, 401)
        self.assertEqual(loads_bytes(unauth_get_body)["error"]["code"], "unauthorized")
        self.assertEqual(unauth_patch_status, 401)
        self.assertEqual(loads_bytes(unauth_patch_body)["error"]["code"], "unauthorized")
        self.assertEqual(auth_get_status, 200)
        self.assertTrue(loads_bytes(auth_get_body)["enabled"])
        self.assertEqual(auth_patch_status, 200)
        checks = {item["name"]: item["enabled"] for item in loads_bytes(auth_patch_body)["checks"]}
        self.assertFalse(checks["block_patterns"])
        guardrail_audit = next(item for item in app.control_plane.audit_events() if item["target_type"] == "guardrails")
        self.assertEqual(guardrail_audit["actor"], "master")

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
