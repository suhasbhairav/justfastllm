from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from justfastllm.agents import AgentRunner
from justfastllm.cache import CacheBackend, MemoryResponseCache, RedisResponseCache, cache_key
from justfastllm.config import Settings, load_settings
from justfastllm.errors import GatewayError, PayloadTooLargeError
from justfastllm.guardrails import Guardrails
from justfastllm.jsonutil import dumps_bytes, loads_bytes
from justfastllm.logging import configure_logging, sanitize_headers
from justfastllm.memory import MemoryUserMemoryStore, RedisUserMemoryStore, UserMemoryStore, preferences_prompt
from justfastllm.openapi import openapi_schema
from justfastllm.providers.factory import ProviderFactory
from justfastllm.skills import SkillRegistry

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class Responder:
    def __init__(self, send: Send, request_id: str, cors_allow_origin: str = "") -> None:
        self.send = send
        self.request_id = request_id
        self.cors_allow_origin = cors_allow_origin

    async def json(self, status: int, payload: dict[str, object]) -> None:
        await self.raw(status, dumps_bytes(payload), {"content-type": "application/json"})

    async def empty(self, status: int) -> None:
        await self.raw(status, b"", {})

    async def raw(self, status: int, body: bytes, headers: Mapping[str, str]) -> None:
        response_headers = self._headers(headers)
        encoded_headers = [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in response_headers.items()
        ]
        await self.send({"type": "http.response.start", "status": status, "headers": encoded_headers})
        await self.send({"type": "http.response.body", "body": body})

    def _headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        response_headers = {key.lower(): value for key, value in headers.items()}
        response_headers["x-request-id"] = self.request_id
        if self.cors_allow_origin:
            response_headers["access-control-allow-origin"] = self.cors_allow_origin
            response_headers["access-control-allow-methods"] = "GET,POST,OPTIONS"
            response_headers["access-control-allow-headers"] = (
                "authorization,content-type,x-llm-provider,x-request-id"
            )
        return response_headers


class GatewayASGI:
    def __init__(
        self,
        *,
        settings: Settings,
        provider_factory: ProviderFactory,
        cache: CacheBackend,
        guardrails: Guardrails,
        memory_store: UserMemoryStore,
        skills: SkillRegistry,
        agent_runner: AgentRunner,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.provider_factory = provider_factory
        self.cache = cache
        self.guardrails = guardrails
        self.memory_store = memory_store
        self.skills = skills
        self.agent_runner = agent_runner
        self.logger = logger

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            responder = Responder(send, uuid.uuid4().hex, self.settings.cors_allow_origin)
            await responder.json(500, {"error": {"code": "unsupported_scope", "message": "unsupported ASGI scope"}})
            return

        started = time.perf_counter()
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")
        headers = _headers(scope)
        request_id = headers.get("x-request-id") or uuid.uuid4().hex
        responder = Responder(send, request_id, self.settings.cors_allow_origin)
        try:
            if method == "GET" and path == "/openapi.json":
                await responder.json(200, openapi_schema())
            elif method == "GET" and path == "/health":
                await responder.json(200, {"status": "ok", "providers": self.provider_factory.names()})
            elif method == "GET" and path == "/v1/models":
                await self._models(responder)
            elif method == "GET" and path.startswith("/v1/providers/") and path.endswith("/models"):
                await self._provider_models(path, responder)
            elif method == "POST" and path == "/v1/chat/completions":
                await self._provider_route(receive, responder, headers, route="chat")
            elif method == "POST" and path == "/v1/messages":
                await self._provider_route(receive, responder, headers, route="messages")
            elif method == "GET" and path == "/v1/skills":
                await responder.json(200, {"object": "list", "data": self.skills.list()})
            elif method == "POST" and path.startswith("/v1/skills/") and path.endswith("/run"):
                await self._skill_run(path, receive, responder)
            elif path == "/v1/guardrails":
                await self._guardrails_route(method, receive, responder)
            elif path.startswith("/v1/users/") and path.endswith("/preferences"):
                await self._preferences_route(method, path, receive, responder)
            elif path.startswith("/v1/users/") and path.endswith("/feedback"):
                await self._feedback_route(method, path, receive, responder)
            elif method == "POST" and path == "/v1/agents/runs":
                await self._agent_run(receive, responder, headers)
            elif method == "OPTIONS":
                await responder.empty(204)
            else:
                await responder.json(404, {"error": {"code": "not_found", "message": "route not found"}})
        except GatewayError as exc:
            await responder.json(exc.status_code, {"error": {"code": exc.code, "message": str(exc)}})
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self.logger.exception("unhandled gateway error")
            await responder.json(500, {"error": {"code": "internal_error", "message": str(exc)}})
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            if self.logger.isEnabledFor(logging.INFO):
                self.logger.info(
                    "request request_id=%s method=%s path=%s elapsed_ms=%s headers=%s",
                    request_id,
                    method,
                    path,
                    elapsed_ms,
                    sanitize_headers(headers),
                )

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _models(self, responder: "Responder") -> None:
        data = {
            "object": "list",
            "data": [
                {
                    "id": config.default_model,
                    "object": "model",
                    "owned_by": name,
                }
                for name, config in sorted(self.settings.providers.items())
            ],
        }
        await responder.json(200, data)

    async def _provider_models(self, path: str, responder: "Responder") -> None:
        provider_name = path.removeprefix("/v1/providers/").removesuffix("/models").strip("/")
        provider = self.provider_factory.create(provider_name)
        upstream = await provider.models()
        response_headers = _response_headers(upstream.headers)
        response_headers["x-justfastllm-provider"] = provider_name
        await responder.raw(upstream.status_code, upstream.body, response_headers)

    async def _provider_route(self, receive: Receive, responder: "Responder", headers: Mapping[str, str], *, route: str) -> None:
        body = await _read_body(receive, self.settings.max_body_bytes)
        payload = loads_bytes(body)
        if not isinstance(payload, dict):
            raise GatewayError("JSON body must be an object", status_code=400)

        provider_name, routed_payload = self.provider_factory.resolve(payload, headers)
        routed_payload = self._with_user_preferences(routed_payload, headers)
        self.guardrails.validate_chat_payload(routed_payload)
        stream = bool(routed_payload.get("stream", False))

        key = cache_key(f"{route}:{provider_name}", routed_payload)
        if not stream:
            cached = self.cache.get(key)
            if cached is not None:
                await responder.raw(200, cached, {"content-type": "application/json", "x-justfastllm-cache": "hit"})
                return

        provider = self.provider_factory.create(provider_name)
        if route == "messages":
            upstream = await provider.messages(routed_payload)
        else:
            upstream = await provider.chat_completion(routed_payload)
        response_headers = _response_headers(upstream.headers)
        response_headers["x-justfastllm-provider"] = provider_name

        if upstream.status_code < 400 and not stream:
            self.cache.set(key, upstream.body)
            response_headers["x-justfastllm-cache"] = "miss"
        await responder.raw(upstream.status_code, upstream.body, response_headers)

    async def _skill_run(self, path: str, receive: Receive, responder: "Responder") -> None:
        skill_name = path.removeprefix("/v1/skills/").removesuffix("/run").strip("/")
        body = await _read_body(receive, self.settings.max_body_bytes)
        payload = loads_bytes(body) if body else {}
        if not isinstance(payload, dict):
            raise GatewayError("JSON body must be an object", status_code=400)
        arguments = payload.get("arguments", payload)
        if not isinstance(arguments, dict):
            arguments = {"input": arguments}
        result = self.skills.run(skill_name, arguments)
        status = 404 if "error" in result else 200
        await responder.json(status, result)

    async def _guardrails_route(self, method: str, receive: Receive, responder: "Responder") -> None:
        if method == "GET":
            await responder.json(200, self.guardrails.status())
            return
        if method != "PATCH":
            await responder.json(405, {"error": {"code": "method_not_allowed", "message": "method not allowed"}})
            return
        payload = await _json_body(receive, self.settings.max_body_bytes)
        enabled = payload.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise GatewayError("enabled must be a boolean", status_code=400)
        enable = payload.get("enable", [])
        disable = payload.get("disable", [])
        if not isinstance(enable, list) or not isinstance(disable, list):
            raise GatewayError("enable and disable must be arrays", status_code=400)
        await responder.json(
            200,
            self.guardrails.configure(enabled=enabled, enable=enable, disable=disable),
        )

    async def _preferences_route(self, method: str, path: str, receive: Receive, responder: "Responder") -> None:
        user_id = _user_id_from_path(path, "preferences")
        if method == "GET":
            await responder.json(200, {"user_id": user_id, "preferences": self.memory_store.get_preferences(user_id)})
            return
        if method not in {"PUT", "PATCH", "POST"}:
            await responder.json(405, {"error": {"code": "method_not_allowed", "message": "method not allowed"}})
            return
        payload = await _json_body(receive, self.settings.max_body_bytes)
        preferences = payload.get("preferences", payload)
        if not isinstance(preferences, dict):
            raise GatewayError("preferences must be an object", status_code=400)
        saved = self.memory_store.save_preferences(user_id, preferences)
        await responder.json(200, {"user_id": user_id, "preferences": saved})

    async def _feedback_route(self, method: str, path: str, receive: Receive, responder: "Responder") -> None:
        user_id = _user_id_from_path(path, "feedback")
        if method == "GET":
            await responder.json(200, {"user_id": user_id, "feedback": self.memory_store.get_feedback(user_id)})
            return
        if method != "POST":
            await responder.json(405, {"error": {"code": "method_not_allowed", "message": "method not allowed"}})
            return
        payload = await _json_body(receive, self.settings.max_body_bytes)
        feedback = payload.get("feedback", payload)
        if not isinstance(feedback, dict):
            raise GatewayError("feedback must be an object", status_code=400)
        saved = self.memory_store.add_feedback(user_id, feedback)
        await responder.json(201, {"user_id": user_id, "feedback": saved})

    async def _agent_run(self, receive: Receive, responder: "Responder", headers: Mapping[str, str]) -> None:
        body = await _read_body(receive, self.settings.max_body_bytes)
        payload = loads_bytes(body)
        if not isinstance(payload, dict):
            raise GatewayError("JSON body must be an object", status_code=400)

        routed_payload = self.agent_runner.build_provider_payload(payload)
        provider_name, provider_payload = self.provider_factory.resolve(routed_payload, headers)
        provider_payload = self._with_user_preferences(provider_payload, headers)
        self.guardrails.validate_chat_payload(provider_payload)
        provider = self.provider_factory.create(provider_name)
        upstream = await provider.chat_completion(provider_payload)
        response_headers = _response_headers(upstream.headers)
        response_headers["x-justfastllm-provider"] = provider_name
        response_headers["x-justfastllm-agent"] = "run"
        await responder.raw(upstream.status_code, upstream.body, response_headers)

    def _with_user_preferences(self, payload: dict[str, object], headers: Mapping[str, str]) -> dict[str, object]:
        user_id = _user_id(payload, headers)
        if not user_id:
            return payload
        prompt = preferences_prompt(self.memory_store.get_preferences(user_id))
        if not prompt:
            return payload
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return payload
        routed = dict(payload)
        routed["messages"] = [{"role": "system", "content": prompt}, *messages]
        return routed


def create_app(
    *,
    settings: Settings | None = None,
    provider_factory: ProviderFactory | None = None,
    cache: CacheBackend | None = None,
    guardrails: Guardrails | None = None,
    memory_store: UserMemoryStore | None = None,
    skills: SkillRegistry | None = None,
    agent_runner: AgentRunner | None = None,
    logger: logging.Logger | None = None,
) -> GatewayASGI:
    resolved_settings = settings or load_settings()
    resolved_logger = logger or configure_logging(resolved_settings.log_level)
    resolved_factory = provider_factory or ProviderFactory(resolved_settings)
    resolved_cache = cache or _cache_from_settings(resolved_settings)
    resolved_guardrails = guardrails or Guardrails(
        max_message_chars=resolved_settings.max_message_chars,
        block_patterns=resolved_settings.block_patterns,
        enabled=resolved_settings.guardrails_enabled,
        disabled=resolved_settings.disabled_guardrails,
    )
    resolved_memory_store = memory_store or _memory_from_settings(resolved_settings)
    resolved_skills = skills or SkillRegistry()
    resolved_agent_runner = agent_runner or AgentRunner(resolved_skills)
    return GatewayASGI(
        settings=resolved_settings,
        provider_factory=resolved_factory,
        cache=resolved_cache,
        guardrails=resolved_guardrails,
        memory_store=resolved_memory_store,
        skills=resolved_skills,
        agent_runner=resolved_agent_runner,
        logger=resolved_logger,
    )


def _cache_from_settings(settings: Settings) -> CacheBackend:
    if settings.cache_backend == "memory":
        return MemoryResponseCache(
            max_items=settings.cache_max_items,
            ttl_seconds=settings.cache_ttl_seconds,
            enabled=settings.cache_enabled,
        )
    return RedisResponseCache(
        url=settings.redis_url,
        ttl_seconds=settings.cache_ttl_seconds,
        key_prefix=settings.redis_key_prefix,
        enabled=settings.cache_enabled,
    )


def _memory_from_settings(settings: Settings) -> UserMemoryStore:
    if settings.user_memory_backend == "memory":
        return MemoryUserMemoryStore(enabled=settings.user_memory_enabled)
    return RedisUserMemoryStore(
        url=settings.redis_url,
        key_prefix=settings.user_memory_key_prefix,
        enabled=settings.user_memory_enabled,
    )


async def _json_body(receive: Receive, max_body_bytes: int) -> dict[str, object]:
    body = await _read_body(receive, max_body_bytes)
    payload = loads_bytes(body) if body else {}
    if not isinstance(payload, dict):
        raise GatewayError("JSON body must be an object", status_code=400)
    return payload


async def _read_body(receive: Receive, max_body_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        chunk = message.get("body", b"")
        if chunk:
            total += len(chunk)
            if total > max_body_bytes:
                raise PayloadTooLargeError("request body exceeds JUSTFASTLLM_MAX_BODY_BYTES")
            chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _headers(scope: Scope) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _user_id(payload: Mapping[str, object], headers: Mapping[str, str]) -> str:
    user = payload.get("user_id") or payload.get("user") or headers.get("x-user-id", "")
    return str(user) if user else ""


def _user_id_from_path(path: str, suffix: str) -> str:
    prefix = "/v1/users/"
    user_id = path.removeprefix(prefix).removesuffix(f"/{suffix}").strip("/")
    return user_id


def _response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    allowed = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in {"content-type", "cache-control"}:
            allowed[lowered] = value
    allowed.setdefault("content-type", "application/json")
    return allowed


app = create_app()
