from __future__ import annotations

import logging
import time
import uuid
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import parse_qs

from justfastllm.agents import AgentRunner
from justfastllm.cache import CacheBackend, MemoryResponseCache, RedisResponseCache, cache_key
from justfastllm.config import Settings, load_settings
from justfastllm.errors import GatewayError, PayloadTooLargeError
from justfastllm.guardrails import Guardrails
from justfastllm.jsonutil import dumps_bytes, loads_bytes
from justfastllm.logging import configure_logging, sanitize_headers
from justfastllm.memory import MemoryUserMemoryStore, RedisUserMemoryStore, UserMemoryStore, preferences_prompt
from justfastllm.openapi import openapi_schema
from justfastllm.plugins import PluginManager
from justfastllm.policies import PolicyEngine
from justfastllm.providers.factory import ProviderFactory
from justfastllm.proxy_control import (
    InMemoryProxyControlPlane,
    RequestEvent,
    estimate_cost_usd,
    estimate_prompt_tokens,
    pricing_table,
    usage_from_body,
)
from justfastllm.skills import SkillRegistry

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

OPENAI_COMPATIBLE_ENDPOINTS = {
    "/v1/completions": ("completions", "completions", True),
    "/v1/responses": ("responses", "responses", False),
    "/v1/embeddings": ("embeddings", "embeddings", True),
    "/v1/images/generations": ("images/generations", "images", False),
    "/v1/images/edits": ("images/edits", "images", False),
    "/v1/images/variations": ("images/variations", "images", False),
    "/v1/audio/transcriptions": ("audio/transcriptions", "audio", False),
    "/v1/audio/translations": ("audio/translations", "audio", False),
    "/v1/moderations": ("moderations", "moderations", False),
    "/v1/rerank": ("rerank", "rerank", True),
    "/rerank": ("rerank", "rerank", True),
}


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
                "authorization,content-type,x-llm-provider,x-request-id,x-user-id,x-justfastllm-key"
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
        control_plane: InMemoryProxyControlPlane,
        policy_engine: PolicyEngine,
        plugins: PluginManager,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.provider_factory = provider_factory
        self.cache = cache
        self.guardrails = guardrails
        self.memory_store = memory_store
        self.skills = skills
        self.agent_runner = agent_runner
        self.control_plane = control_plane
        self.policy_engine = policy_engine
        self.plugins = plugins
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
        query = _query_params(scope)
        headers = _headers(scope)
        request_id = headers.get("x-request-id") or uuid.uuid4().hex
        responder = Responder(send, request_id, self.settings.cors_allow_origin)
        try:
            if method == "OPTIONS":
                await responder.empty(204)
            elif method == "GET" and path == "/openapi.json":
                await responder.json(200, openapi_schema())
            elif method == "GET" and path == "/health":
                await responder.json(200, {"status": "ok", "providers": self.provider_factory.names()})
            elif method == "GET" and path == "/v1/models":
                await self._models(responder)
            elif method == "GET" and path.startswith("/v1/providers/") and path.endswith("/models"):
                await self._provider_models(path, responder)
            elif path.startswith("/v1/keys") or path.startswith("/key/"):
                await self._keys_route(method, path, query, receive, responder, headers)
            elif path in {
                "/v1/proxy/users",
                "/v1/proxy/users/info",
                "/v1/proxy/users/delete",
                "/user/new",
                "/user/info",
                "/user/update",
                "/user/delete",
                "/v1/proxy/teams",
                "/v1/proxy/teams/info",
                "/v1/proxy/teams/delete",
                "/team/new",
                "/team/info",
                "/team/list",
                "/team/update",
                "/team/delete",
                "/v1/service-accounts/keys",
                "/service_account/key/generate",
            }:
                await self._identity_route(method, path, query, receive, responder, headers)
            elif method == "GET" and path == "/v1/metrics":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, self.control_plane.metrics())
            elif method == "GET" and path == "/v1/usage":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, {"object": "list", "data": self.control_plane.events(limit=200)})
            elif method == "GET" and path == "/v1/audit":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, {"object": "list", "data": self.control_plane.audit_events(limit=200)})
            elif method == "GET" and path == "/v1/providers/health":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, self.control_plane.provider_health())
            elif method == "GET" and path == "/v1/alerts":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, self.control_plane.alerts())
            elif method == "GET" and path == "/v1/pricing":
                await responder.json(200, {"object": "list", "data": pricing_table()})
            elif method == "GET" and path == "/v1/proxy/features":
                await responder.json(200, self._proxy_features())
            elif path == "/v1/policies":
                await self._policies_route(method, responder, headers)
            elif path == "/v1/plugins":
                await self._plugins_route(method, responder, headers)
            elif method == "POST" and path == "/v1/config/reload":
                await self._config_reload(responder, headers)
            elif method == "POST" and path == "/v1/chat/completions":
                await self._provider_route(receive, responder, headers, route="chat")
            elif method == "POST" and path == "/v1/messages":
                await self._provider_route(receive, responder, headers, route="messages")
            elif method == "POST" and path in OPENAI_COMPATIBLE_ENDPOINTS:
                endpoint, route, cacheable = OPENAI_COMPATIBLE_ENDPOINTS[path]
                await self._openai_compatible_endpoint_route(
                    receive,
                    responder,
                    headers,
                    endpoint=endpoint,
                    route=route,
                    cacheable=cacheable,
                )
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
        call_id = uuid.uuid4().hex
        started = time.perf_counter()
        body = await _read_body(receive, self.settings.max_body_bytes)
        payload = loads_bytes(body)
        if not isinstance(payload, dict):
            raise GatewayError("JSON body must be an object", status_code=400)

        provider_name, routed_payload = self.provider_factory.resolve(payload, headers)
        routed_payload = self._with_user_preferences(routed_payload, headers)
        model = _resolved_model(self.settings, provider_name, routed_payload)
        prompt_estimate = estimate_prompt_tokens(routed_payload)
        virtual_key = self.control_plane.authenticate(headers, model=model, estimated_tokens=prompt_estimate, route=route)
        if virtual_key is not None:
            routed_payload = self.control_plane.apply_aliases(virtual_key, routed_payload)
            model = _resolved_model(self.settings, provider_name, routed_payload)
        routed_payload = self.plugins.before_request(
            _context(route=route, provider=provider_name, model=model),
            routed_payload,
        )
        model = _resolved_model(self.settings, provider_name, routed_payload)
        self.policy_engine.validate_request(
            route=route,
            provider=provider_name,
            model=model,
            payload=routed_payload,
            estimated_tokens=prompt_estimate,
        )
        self.guardrails.validate_chat_payload(routed_payload)
        stream = bool(routed_payload.get("stream", False))

        key = cache_key(f"{route}:{provider_name}", routed_payload)
        if not stream:
            cached = self.cache.get(key)
            if cached is not None:
                self.policy_engine.validate_response(cached)
                cached = self.plugins.after_response(
                    _context(route=route, provider=provider_name, model=model, cache="hit"),
                    cached,
                    "application/json",
                )
                prompt_tokens, completion_tokens, total_tokens = usage_from_body(cached, prompt_estimate)
                cost_usd = estimate_cost_usd(model, prompt_tokens, completion_tokens)
                self._record_proxy_event(
                    call_id=call_id,
                    route=route,
                    provider_name=provider_name,
                    model=model,
                    status_code=200,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost_usd,
                    cache="hit",
                    virtual_key=virtual_key,
                    payload=routed_payload,
                )
                await responder.raw(
                    200,
                    cached,
                    {
                        "content-type": "application/json",
                        "x-justfastllm-cache": "hit",
                        "x-justfastllm-call-id": call_id,
                        "x-justfastllm-cost-usd": f"{cost_usd:.8f}",
                    },
                )
                return

        upstream_provider, upstream = await self._call_with_fallbacks(
            provider_name,
            routed_payload,
            route=route,
            fallback_providers=_fallbacks(payload, self.settings.fallback_providers),
        )
        response_headers = _response_headers(upstream.headers)
        response_headers["x-justfastllm-provider"] = upstream_provider

        if upstream.status_code < 400:
            self.policy_engine.validate_response(upstream.body)
            upstream_body = self.plugins.after_response(
                _context(route=route, provider=upstream_provider, model=model, cache="miss"),
                upstream.body,
                response_headers.get("content-type", "application/json"),
            )
            upstream = type(upstream)(status_code=upstream.status_code, headers=upstream.headers, body=upstream_body)
        if upstream.status_code < 400 and not stream:
            self.cache.set(key, upstream.body)
            response_headers["x-justfastllm-cache"] = "miss"
        prompt_tokens, completion_tokens, total_tokens = usage_from_body(upstream.body, prompt_estimate)
        cost_usd = estimate_cost_usd(model, prompt_tokens, completion_tokens)
        self._record_proxy_event(
            call_id=call_id,
            route=route,
            provider_name=upstream_provider,
            model=model,
            status_code=upstream.status_code,
            latency_ms=(time.perf_counter() - started) * 1000,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            cache=response_headers.get("x-justfastllm-cache", "bypass" if stream else "miss"),
            virtual_key=virtual_key,
            payload=routed_payload,
        )
        self._mirror_if_configured(payload, routed_payload, route, upstream_provider)
        response_headers["x-justfastllm-call-id"] = call_id
        response_headers["x-justfastllm-cost-usd"] = f"{cost_usd:.8f}"
        await responder.raw(upstream.status_code, upstream.body, response_headers)

    async def _openai_compatible_endpoint_route(
        self,
        receive: Receive,
        responder: "Responder",
        headers: Mapping[str, str],
        *,
        endpoint: str,
        route: str,
        cacheable: bool,
    ) -> None:
        call_id = uuid.uuid4().hex
        started = time.perf_counter()
        body = await _read_body(receive, self.settings.max_body_bytes)
        content_type = headers.get("content-type", "application/json")
        payload = _json_payload_or_empty(body, content_type)
        provider_name, routed_payload = self.provider_factory.resolve(payload, headers)
        model = _resolved_model(self.settings, provider_name, routed_payload)
        prompt_estimate = estimate_prompt_tokens(routed_payload)
        virtual_key = self.control_plane.authenticate(
            headers,
            model=model,
            estimated_tokens=prompt_estimate,
            route=route,
        )
        if virtual_key is not None and payload:
            routed_payload = self.control_plane.apply_aliases(virtual_key, routed_payload)
            model = _resolved_model(self.settings, provider_name, routed_payload)
            body = dumps_bytes(routed_payload)
        if payload:
            routed_payload = self.plugins.before_request(
                _context(route=route, provider=provider_name, model=model),
                routed_payload,
            )
            model = _resolved_model(self.settings, provider_name, routed_payload)
            body = dumps_bytes(routed_payload)
        self.policy_engine.validate_request(
            route=route,
            provider=provider_name,
            model=model,
            payload=routed_payload,
            estimated_tokens=prompt_estimate,
        )

        key = cache_key(f"{route}:{provider_name}:{endpoint}", routed_payload) if cacheable and payload else ""
        if key:
            cached = self.cache.get(key)
            if cached is not None:
                self.policy_engine.validate_response(cached)
                cached = self.plugins.after_response(
                    _context(route=route, provider=provider_name, model=model, cache="hit"),
                    cached,
                    "application/json",
                )
                prompt_tokens, completion_tokens, total_tokens = usage_from_body(cached, prompt_estimate)
                cost_usd = estimate_cost_usd(model, prompt_tokens, completion_tokens)
                self._record_proxy_event(
                    call_id=call_id,
                    route=route,
                    provider_name=provider_name,
                    model=model,
                    status_code=200,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost_usd,
                    cache="hit",
                    virtual_key=virtual_key,
                    payload=routed_payload,
                )
                await responder.raw(
                    200,
                    cached,
                    {
                        "content-type": "application/json",
                        "x-justfastllm-cache": "hit",
                        "x-justfastllm-call-id": call_id,
                        "x-justfastllm-cost-usd": f"{cost_usd:.8f}",
                    },
                )
                return

        upstream_provider, upstream = await self._call_openai_endpoint_with_fallbacks(
            provider_name,
            endpoint,
            body,
            content_type,
            fallback_providers=_fallbacks(payload, self.settings.fallback_providers),
        )
        response_headers = _response_headers(upstream.headers)
        response_headers["x-justfastllm-provider"] = upstream_provider
        if upstream.status_code < 400:
            self.policy_engine.validate_response(upstream.body)
            upstream_body = self.plugins.after_response(
                _context(route=route, provider=upstream_provider, model=model, cache="miss"),
                upstream.body,
                response_headers.get("content-type", content_type),
            )
            upstream = type(upstream)(status_code=upstream.status_code, headers=upstream.headers, body=upstream_body)
        if key and upstream.status_code < 400:
            self.cache.set(key, upstream.body)
            response_headers["x-justfastllm-cache"] = "miss"
        prompt_tokens, completion_tokens, total_tokens = usage_from_body(upstream.body, prompt_estimate)
        cost_usd = estimate_cost_usd(model, prompt_tokens, completion_tokens)
        self._record_proxy_event(
            call_id=call_id,
            route=route,
            provider_name=upstream_provider,
            model=model,
            status_code=upstream.status_code,
            latency_ms=(time.perf_counter() - started) * 1000,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            cache=response_headers.get("x-justfastllm-cache", "bypass"),
            virtual_key=virtual_key,
            payload=routed_payload,
        )
        response_headers["x-justfastllm-call-id"] = call_id
        response_headers["x-justfastllm-cost-usd"] = f"{cost_usd:.8f}"
        await responder.raw(upstream.status_code, upstream.body, response_headers)

    async def _call_with_fallbacks(
        self,
        provider_name: str,
        payload: dict[str, object],
        *,
        route: str,
        fallback_providers: tuple[str, ...],
    ):
        candidates = (provider_name, *tuple(provider for provider in fallback_providers if provider != provider_name))
        last_upstream = None
        for candidate in candidates:
            provider = self.provider_factory.create(candidate)
            upstream = await (provider.messages(payload) if route == "messages" else provider.chat_completion(payload))
            last_upstream = upstream
            if upstream.status_code < 500:
                return candidate, upstream
        return candidates[-1], last_upstream

    async def _call_openai_endpoint_with_fallbacks(
        self,
        provider_name: str,
        endpoint: str,
        body: bytes,
        content_type: str,
        *,
        fallback_providers: tuple[str, ...],
    ):
        candidates = (provider_name, *tuple(provider for provider in fallback_providers if provider != provider_name))
        last_upstream = None
        for candidate in candidates:
            provider = self.provider_factory.create(candidate)
            upstream = await provider.openai_endpoint(endpoint, body, content_type)
            last_upstream = upstream
            if upstream.status_code < 500:
                return candidate, upstream
        return candidates[-1], last_upstream

    def _mirror_if_configured(self, original_payload: Mapping[str, object], routed_payload: dict[str, object], route: str, upstream_provider: str) -> None:
        mirror_provider = str(original_payload.get("mirror_provider") or self.settings.mirror_provider or "").lower()
        if not mirror_provider or mirror_provider == upstream_provider:
            return

        async def mirror() -> None:
            try:
                provider = self.provider_factory.create(mirror_provider)
                if route == "messages":
                    await provider.messages(dict(routed_payload))
                else:
                    await provider.chat_completion(dict(routed_payload))
            except Exception:
                self.logger.debug("mirror provider call failed", exc_info=True)

        asyncio.create_task(mirror())

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

    async def _keys_route(
        self,
        method: str,
        path: str,
        query: Mapping[str, str],
        receive: Receive,
        responder: "Responder",
        headers: Mapping[str, str],
    ) -> None:
        if method == "POST" and path in {"/v1/keys", "/key/generate"}:
            self.control_plane.authenticate(headers, require_admin=True)
            payload = await _json_body(receive, self.settings.max_body_bytes)
            await responder.json(201, self.control_plane.generate_key(payload))
            return
        if method == "GET" and path == "/v1/keys":
            self.control_plane.authenticate(headers, require_admin=True)
            await responder.json(200, {"object": "list", "data": self.control_plane.list_keys()})
            return
        if path in {"/v1/keys/info", "/key/info"} and method in {"GET", "POST"}:
            self.control_plane.authenticate(headers, require_admin=True)
            payload = await _json_body(receive, self.settings.max_body_bytes) if method == "POST" else query
            info = self.control_plane.key_info(str(payload.get("key", "")))
            await responder.json(200 if info else 404, info or {"error": {"code": "not_found", "message": "key not found"}})
            return
        if method in {"PATCH", "POST"} and path in {"/v1/keys/update", "/key/update"}:
            self.control_plane.authenticate(headers, require_admin=True)
            payload = await _json_body(receive, self.settings.max_body_bytes)
            updated = self.control_plane.update_key(str(payload.get("key", "")), payload)
            await responder.json(200 if updated else 404, updated or {"error": {"code": "not_found", "message": "key not found"}})
            return
        if method in {"DELETE", "POST"} and path in {"/v1/keys", "/key/delete"}:
            self.control_plane.authenticate(headers, require_admin=True)
            payload = await _json_body(receive, self.settings.max_body_bytes) if method == "POST" else query
            deleted = self.control_plane.delete_key(str(payload.get("key", "")))
            await responder.json(200, {"deleted": deleted})
            return
        await responder.json(404, {"error": {"code": "not_found", "message": "route not found"}})

    async def _identity_route(
        self,
        method: str,
        path: str,
        query: Mapping[str, str],
        receive: Receive,
        responder: "Responder",
        headers: Mapping[str, str],
    ) -> None:
        self.control_plane.authenticate(headers, require_admin=True)
        if method == "POST" and path in {"/v1/proxy/users", "/user/new"}:
            await responder.json(201, self.control_plane.create_user(await _json_body(receive, self.settings.max_body_bytes)))
            return
        if method == "GET" and path == "/v1/proxy/users":
            await responder.json(200, {"object": "list", "data": self.control_plane.list_users()})
            return
        if path in {"/v1/proxy/users/info", "/user/info"} and method in {"GET", "POST"}:
            payload = await _json_body(receive, self.settings.max_body_bytes) if method == "POST" else query
            info = self.control_plane.user_info(str(payload.get("user_id", "")))
            await responder.json(200 if info else 404, info or {"error": {"code": "not_found", "message": "user not found"}})
            return
        if path == "/user/update" and method in {"PATCH", "POST"}:
            payload = await _json_body(receive, self.settings.max_body_bytes)
            updated = self.control_plane.update_user(str(payload.get("user_id", "")), payload)
            await responder.json(200 if updated else 404, updated or {"error": {"code": "not_found", "message": "user not found"}})
            return
        if path in {"/v1/proxy/users", "/v1/proxy/users/delete", "/user/delete"} and method in {"DELETE", "POST"}:
            payload = await _json_body(receive, self.settings.max_body_bytes) if method == "POST" else query
            deleted = self.control_plane.delete_user(str(payload.get("user_id", "")))
            await responder.json(200, {"deleted": deleted})
            return
        if method == "POST" and path in {"/v1/proxy/teams", "/team/new"}:
            await responder.json(201, self.control_plane.create_team(await _json_body(receive, self.settings.max_body_bytes)))
            return
        if method == "GET" and path in {"/v1/proxy/teams", "/team/list"}:
            await responder.json(200, {"object": "list", "data": self.control_plane.list_teams()})
            return
        if path in {"/v1/proxy/teams/info", "/team/info"} and method in {"GET", "POST"}:
            payload = await _json_body(receive, self.settings.max_body_bytes) if method == "POST" else query
            info = self.control_plane.team_info(str(payload.get("team_id", "")))
            await responder.json(200 if info else 404, info or {"error": {"code": "not_found", "message": "team not found"}})
            return
        if path == "/team/update" and method in {"PATCH", "POST"}:
            payload = await _json_body(receive, self.settings.max_body_bytes)
            updated = self.control_plane.update_team(str(payload.get("team_id", "")), payload)
            await responder.json(200 if updated else 404, updated or {"error": {"code": "not_found", "message": "team not found"}})
            return
        if path in {"/v1/proxy/teams", "/v1/proxy/teams/delete", "/team/delete"} and method in {"DELETE", "POST"}:
            payload = await _json_body(receive, self.settings.max_body_bytes) if method == "POST" else query
            deleted = self.control_plane.delete_team(str(payload.get("team_id", "")))
            await responder.json(200, {"deleted": deleted})
            return
        if method == "POST" and path in {"/v1/service-accounts/keys", "/service_account/key/generate"}:
            await responder.json(
                201,
                self.control_plane.generate_service_account_key(await _json_body(receive, self.settings.max_body_bytes)),
            )
            return
        await responder.json(404, {"error": {"code": "not_found", "message": "route not found"}})

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
        call_id = uuid.uuid4().hex
        started = time.perf_counter()
        body = await _read_body(receive, self.settings.max_body_bytes)
        payload = loads_bytes(body)
        if not isinstance(payload, dict):
            raise GatewayError("JSON body must be an object", status_code=400)

        routed_payload = self.agent_runner.build_provider_payload(payload)
        provider_name, provider_payload = self.provider_factory.resolve(routed_payload, headers)
        provider_payload = self._with_user_preferences(provider_payload, headers)
        model = _resolved_model(self.settings, provider_name, provider_payload)
        prompt_estimate = estimate_prompt_tokens(provider_payload)
        virtual_key = self.control_plane.authenticate(
            headers,
            model=model,
            estimated_tokens=prompt_estimate,
            route="agents",
        )
        if virtual_key is not None:
            provider_payload = self.control_plane.apply_aliases(virtual_key, provider_payload)
            model = _resolved_model(self.settings, provider_name, provider_payload)
        provider_payload = self.plugins.before_request(
            _context(route="agents", provider=provider_name, model=model),
            provider_payload,
        )
        model = _resolved_model(self.settings, provider_name, provider_payload)
        self.policy_engine.validate_request(
            route="agents",
            provider=provider_name,
            model=model,
            payload=provider_payload,
            estimated_tokens=prompt_estimate,
        )
        self.guardrails.validate_chat_payload(provider_payload)
        upstream_provider, upstream = await self._call_with_fallbacks(
            provider_name,
            provider_payload,
            route="chat",
            fallback_providers=_fallbacks(payload, self.settings.fallback_providers),
        )
        response_headers = _response_headers(upstream.headers)
        response_headers["x-justfastllm-provider"] = upstream_provider
        response_headers["x-justfastllm-agent"] = "run"
        if upstream.status_code < 400:
            self.policy_engine.validate_response(upstream.body)
            upstream_body = self.plugins.after_response(
                _context(route="agents", provider=upstream_provider, model=model),
                upstream.body,
                response_headers.get("content-type", "application/json"),
            )
            upstream = type(upstream)(status_code=upstream.status_code, headers=upstream.headers, body=upstream_body)
        prompt_tokens, completion_tokens, total_tokens = usage_from_body(upstream.body, prompt_estimate)
        cost_usd = estimate_cost_usd(model, prompt_tokens, completion_tokens)
        self._record_proxy_event(
            call_id=call_id,
            route="agents",
            provider_name=upstream_provider,
            model=model,
            status_code=upstream.status_code,
            latency_ms=(time.perf_counter() - started) * 1000,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            cache="bypass",
            virtual_key=virtual_key,
            payload=provider_payload,
        )
        response_headers["x-justfastllm-call-id"] = call_id
        response_headers["x-justfastllm-cost-usd"] = f"{cost_usd:.8f}"
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

    def _record_proxy_event(
        self,
        *,
        call_id: str,
        route: str,
        provider_name: str,
        model: str,
        status_code: int,
        latency_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost_usd: float,
        cache: str,
        virtual_key,
        payload: Mapping[str, object],
    ) -> None:
        self.control_plane.record_event(
            RequestEvent(
                call_id=call_id,
                route=route,
                provider=provider_name,
                model=model,
                status_code=status_code,
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost_usd,
                cache=cache,
                key_preview=virtual_key.preview if virtual_key else "master-or-open",
                user_id=(virtual_key.user_id if virtual_key else _user_id(payload, {})),
                team_id=virtual_key.team_id if virtual_key else "",
                tags=_tags(payload),
            ),
            key=virtual_key,
        )

    def _proxy_features(self) -> dict[str, object]:
        return {
            "openai_compatible_endpoints": True,
            "anthropic_messages": True,
            "text_completions": True,
            "responses": True,
            "embeddings": True,
            "image_endpoints": True,
            "audio_endpoints": True,
            "moderations": True,
            "rerank": True,
            "virtual_keys": True,
            "master_key": bool(self.settings.master_key),
            "model_access_controls": True,
            "rpm_tpm_rate_limits": True,
            "budget_enforcement": True,
            "user_budget_tracking": True,
            "team_budget_tracking": True,
            "service_account_keys": True,
            "model_aliases": True,
            "custom_key_header": True,
            "persistent_control_plane": True,
            "config_file": True,
            "usage_tracking": True,
            "spend_tracking": True,
            "audit_log": True,
            "provider_health": True,
            "alerts": True,
            "key_update_delete": True,
            "user_team_update_delete": True,
            "pricing_table": True,
            "redis_cache": True,
            "guardrail_toggles": True,
            "policies": True,
            "plugin_hooks": True,
            "config_reload": True,
            "fallbacks": True,
            "traffic_mirroring": True,
            "agents": True,
            "skills": True,
            "openapi": True,
            "dashboard_api": True,
        }

    def _authenticate_admin_if_configured(self, headers: Mapping[str, str]) -> None:
        if self.settings.master_key:
            self.control_plane.authenticate(headers, require_admin=True)

    async def _policies_route(self, method: str, responder: "Responder", headers: Mapping[str, str]) -> None:
        if method != "GET":
            await responder.json(405, {"error": {"code": "method_not_allowed", "message": "method not allowed"}})
            return
        self._authenticate_admin_if_configured(headers)
        await responder.json(200, self.policy_engine.status())

    async def _plugins_route(self, method: str, responder: "Responder", headers: Mapping[str, str]) -> None:
        if method != "GET":
            await responder.json(405, {"error": {"code": "method_not_allowed", "message": "method not allowed"}})
            return
        self._authenticate_admin_if_configured(headers)
        await responder.json(200, self.plugins.status())

    async def _config_reload(self, responder: "Responder", headers: Mapping[str, str]) -> None:
        self.control_plane.authenticate(headers, require_admin=True)
        settings = load_settings()
        self.settings = settings
        self.provider_factory = ProviderFactory(settings, self.provider_factory.http_client)
        self.cache = _cache_from_settings(settings)
        self.guardrails = _guardrails_from_settings(settings)
        self.memory_store = _memory_from_settings(settings)
        self.control_plane.master_key = settings.master_key
        self.control_plane.key_header_name = settings.key_header_name
        self.policy_engine = _policy_from_settings(settings)
        self.plugins = PluginManager(settings.plugin_modules)
        await responder.json(200, {"reloaded": True, "providers": self.provider_factory.names()})


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
    control_plane: InMemoryProxyControlPlane | None = None,
) -> GatewayASGI:
    resolved_settings = settings or load_settings()
    resolved_logger = logger or configure_logging(resolved_settings.log_level)
    resolved_factory = provider_factory or ProviderFactory(resolved_settings)
    resolved_cache = cache or _cache_from_settings(resolved_settings)
    resolved_guardrails = guardrails or _guardrails_from_settings(resolved_settings)
    resolved_memory_store = memory_store or _memory_from_settings(resolved_settings)
    resolved_skills = skills or SkillRegistry()
    resolved_agent_runner = agent_runner or AgentRunner(resolved_skills)
    resolved_control_plane = control_plane or InMemoryProxyControlPlane(
        master_key=resolved_settings.master_key,
        key_header_name=resolved_settings.key_header_name,
        storage_path=resolved_settings.control_plane_storage_path,
    )
    resolved_policy = _policy_from_settings(resolved_settings)
    resolved_plugins = PluginManager(resolved_settings.plugin_modules)
    return GatewayASGI(
        settings=resolved_settings,
        provider_factory=resolved_factory,
        cache=resolved_cache,
        guardrails=resolved_guardrails,
        memory_store=resolved_memory_store,
        skills=resolved_skills,
        agent_runner=resolved_agent_runner,
        control_plane=resolved_control_plane,
        policy_engine=resolved_policy,
        plugins=resolved_plugins,
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


def _guardrails_from_settings(settings: Settings) -> Guardrails:
    return Guardrails(
        max_message_chars=settings.max_message_chars,
        block_patterns=settings.block_patterns,
        enabled=settings.guardrails_enabled,
        disabled=settings.disabled_guardrails,
    )


def _policy_from_settings(settings: Settings) -> PolicyEngine:
    return PolicyEngine(
        denied_models=settings.policy_denied_models,
        allowed_providers=settings.policy_allowed_providers,
        required_tags=settings.policy_required_tags,
        max_prompt_tokens=settings.policy_max_prompt_tokens,
        response_block_patterns=settings.policy_response_block_patterns,
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


def _query_params(scope: Scope) -> dict[str, str]:
    raw = scope.get("query_string", b"")
    if isinstance(raw, str):
        raw = raw.encode("latin-1")
    parsed = parse_qs(raw.decode("latin-1"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _json_payload_or_empty(body: bytes, content_type: str) -> dict[str, object]:
    if "application/json" not in content_type.lower():
        return {}
    payload = loads_bytes(body) if body else {}
    if not isinstance(payload, dict):
        raise GatewayError("JSON body must be an object", status_code=400)
    return payload


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


def _resolved_model(settings: Settings, provider_name: str, payload: Mapping[str, object]) -> str:
    model = payload.get("model")
    if isinstance(model, str) and model:
        return model
    config = settings.providers.get(provider_name)
    return config.default_model if config else ""


def _fallbacks(payload: Mapping[str, object], configured: tuple[str, ...]) -> tuple[str, ...]:
    value = payload.get("fallback_providers")
    if isinstance(value, list):
        return tuple(str(item).lower() for item in value if str(item))
    return configured


def _tags(payload: Mapping[str, object]) -> tuple[str, ...]:
    value = payload.get("tags")
    if isinstance(value, list):
        return tuple(str(item) for item in value if str(item))
    return ()


def _context(**values: object) -> dict[str, object]:
    return values


app = create_app()
