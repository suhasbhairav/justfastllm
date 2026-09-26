from __future__ import annotations

import logging
import hashlib
import time
import uuid
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import parse_qs

from justfastllm.agents import AgentRunner
from justfastllm.cache import CacheBackend, DatabaseResponseCache, MemoryResponseCache, RedisResponseCache, cache_key
from justfastllm.config import Settings, load_settings
from justfastllm.dbstate import database_family, database_transport_security, is_supported_database_url
from justfastllm.errors import BadRequestError, ForbiddenError, GatewayError, PayloadTooLargeError
from justfastllm.guardrails import Guardrails
from justfastllm.jsonutil import dumps_bytes, loads_bytes
from justfastllm.logging import configure_logging, sanitize_headers
from justfastllm.memory import DatabaseUserMemoryStore, MemoryUserMemoryStore, RedisUserMemoryStore, UserMemoryStore, preferences_prompt
from justfastllm.openapi import openapi_schema
from justfastllm.plugins import PluginManager
from justfastllm.policies import PolicyEngine
from justfastllm.providers.factory import ProviderFactory
from justfastllm.proxy_control import (
    InMemoryProxyControlPlane,
    RequestEvent,
    SUPPORTED_CONSENT_STATUSES,
    SUPPORTED_PRIVACY_REQUEST_STATUSES,
    SUPPORTED_PRIVACY_REQUEST_TYPES,
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
    def __init__(
        self,
        send: Send,
        request_id: str,
        cors_allow_origin: str = "",
        key_header_name: str = "authorization",
    ) -> None:
        self.send = send
        self.request_id = request_id
        self.cors_allow_origin = cors_allow_origin
        self.key_header_name = key_header_name.lower()
        self.status_code = 0

    async def json(self, status: int, payload: dict[str, object]) -> None:
        await self.raw(status, dumps_bytes(payload), {"content-type": "application/json"})

    async def empty(self, status: int) -> None:
        await self.raw(status, b"", {})

    async def raw(self, status: int, body: bytes, headers: Mapping[str, str]) -> None:
        self.status_code = status
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
        response_headers.setdefault("cache-control", "no-store")
        response_headers.setdefault("pragma", "no-cache")
        response_headers.setdefault("x-content-type-options", "nosniff")
        response_headers.setdefault("referrer-policy", "no-referrer")
        response_headers.setdefault("permissions-policy", "geolocation=(), microphone=(), camera=()")
        if self.cors_allow_origin:
            response_headers["access-control-allow-origin"] = self.cors_allow_origin
            response_headers["access-control-allow-methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
            allowed_headers = {
                "authorization",
                "content-type",
                "x-llm-provider",
                "x-request-id",
                "x-user-id",
                "x-team-id",
                "x-justfastllm-key",
                self.key_header_name,
            }
            response_headers["access-control-allow-headers"] = ",".join(sorted(header for header in allowed_headers if header))
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
            responder = Responder(
                send,
                uuid.uuid4().hex,
                self.settings.cors_allow_origin,
                self.settings.key_header_name,
            )
            await responder.json(500, {"error": {"code": "unsupported_scope", "message": "unsupported ASGI scope"}})
            return

        started = time.perf_counter()
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")
        query = _query_params(scope)
        headers = _headers(scope)
        request_id = headers.get("x-request-id") or uuid.uuid4().hex
        responder = Responder(send, request_id, self.settings.cors_allow_origin, self.settings.key_header_name)
        try:
            if method == "OPTIONS":
                await responder.empty(204)
            elif method == "GET" and path == "/openapi.json":
                await responder.json(200, openapi_schema())
            elif method == "GET" and path == "/health":
                readiness = self._compliance_readiness()
                status_code = 200 if readiness["ready"] or not self.settings.require_compliance_ready else 503
                await responder.json(
                    status_code,
                    {
                        "status": "ok" if status_code == 200 else "compliance_not_ready",
                        "providers": self.provider_factory.names(),
                        "compliance": readiness,
                    },
                )
            elif method == "GET" and path == "/v1/models":
                self.control_plane.authenticate(headers, route="models")
                await self._models(responder)
            elif method == "GET" and path.startswith("/v1/providers/") and path.endswith("/models"):
                await self._provider_models(path, responder, headers)
            elif path.startswith("/v1/keys") or path.startswith("/key/"):
                await self._keys_route(method, path, query, receive, responder, headers)
            elif path in {
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
            elif method == "GET" and path == "/v1/access":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, {"object": "list", "data": self.control_plane.access_events(limit=200)})
            elif method == "GET" and path == "/v1/auth/events":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, {"object": "list", "data": self.control_plane.auth_events(limit=200)})
            elif method == "GET" and path == "/v1/providers/health":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, self.control_plane.provider_health())
            elif method == "GET" and path == "/v1/alerts":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, self.control_plane.alerts())
            elif method == "GET" and path == "/v1/compliance/status":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, self._compliance_status())
            elif method == "GET" and path == "/v1/compliance/evidence":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, self._compliance_evidence())
            elif method == "GET" and path == "/v1/compliance/integrity":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, self.control_plane.evidence_integrity())
            elif method == "GET" and path == "/v1/compliance/report":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, self._compliance_report())
            elif path == "/v1/privacy/consents":
                await self._privacy_consents_route(method, query, receive, responder, headers)
            elif path.startswith("/v1/privacy/consents/"):
                await self._privacy_consent_item_route(method, path, receive, responder, headers)
            elif path == "/v1/privacy/requests":
                await self._privacy_requests_route(method, query, receive, responder, headers)
            elif path.startswith("/v1/privacy/requests/"):
                await self._privacy_request_item_route(method, path, receive, responder, headers)
            elif path.startswith("/v1/privacy/users/") and (
                path.endswith("/export") or path.endswith("/erase")
            ):
                await self._privacy_route(method, path, responder, headers)
            elif method == "GET" and path == "/v1/pricing":
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, {"object": "list", "data": pricing_table()})
            elif method == "GET" and path == "/v1/proxy/features":
                self._authenticate_admin_if_configured(headers)
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
                self._authenticate_admin_if_configured(headers)
                await responder.json(200, {"object": "list", "data": self.skills.list()})
            elif method == "POST" and path.startswith("/v1/skills/") and path.endswith("/run"):
                await self._skill_run(path, receive, responder, headers)
            elif path == "/v1/guardrails":
                await self._guardrails_route(method, receive, responder, headers)
            elif path.startswith("/v1/users/") and path.endswith("/preferences"):
                await self._preferences_route(method, path, receive, responder, headers)
            elif path.startswith("/v1/users/") and path.endswith("/feedback"):
                await self._feedback_route(method, path, receive, responder, headers)
            elif method == "POST" and path == "/v1/agents/runs":
                await self._agent_run(receive, responder, headers)
            else:
                await responder.json(404, {"error": {"code": "not_found", "message": "route not found"}})
        except GatewayError as exc:
            await responder.json(exc.status_code, {"error": {"code": exc.code, "message": str(exc)}})
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self.logger.error("unhandled gateway error type=%s", exc.__class__.__name__)
            await responder.json(
                500,
                {
                    "error": {
                        "code": "internal_error",
                        "message": "internal server error",
                    }
                },
            )
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            subject = self.control_plane.subject_from_headers(headers)
            self.control_plane.record_access_event(
                request_id=request_id,
                method=method,
                path=path,
                status_code=responder.status_code,
                latency_ms=elapsed_ms,
                auth_context=_auth_context(headers, self.settings.master_key, self.settings.key_header_name),
                key_preview=subject["key_preview"],
                user_id=subject["user_id"],
                team_id=subject["team_id"],
            )
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

    async def _provider_models(self, path: str, responder: "Responder", headers: Mapping[str, str]) -> None:
        self.control_plane.authenticate(headers, route="models")
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
        payload = _json_object_from_body(body)

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

        key = _scoped_cache_key(f"{route}:{provider_name}", routed_payload, headers, virtual_key)
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
                    headers=headers,
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
            headers=headers,
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

        key = _scoped_cache_key(f"{route}:{provider_name}:{endpoint}", routed_payload, headers, virtual_key) if cacheable and payload else ""
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
                    headers=headers,
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
            headers=headers,
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

    async def _skill_run(
        self,
        path: str,
        receive: Receive,
        responder: "Responder",
        headers: Mapping[str, str],
    ) -> None:
        self._authenticate_admin_if_configured(headers)
        skill_name = path.removeprefix("/v1/skills/").removesuffix("/run").strip("/")
        body = await _read_body(receive, self.settings.max_body_bytes)
        payload = _json_object_from_body(body)
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
        if path in {"/v1/proxy/users/update", "/user/update"} and method in {"PATCH", "POST"}:
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
        if path in {"/v1/proxy/teams/update", "/team/update"} and method in {"PATCH", "POST"}:
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

    async def _guardrails_route(
        self,
        method: str,
        receive: Receive,
        responder: "Responder",
        headers: Mapping[str, str],
    ) -> None:
        self._authenticate_admin_if_configured(headers)
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
        configured = self.guardrails.configure(enabled=enabled, enable=enable, disable=disable)
        self.control_plane.record_audit_event(
            "update",
            "guardrails",
            "runtime",
            actor=_audit_actor(self.control_plane, headers, self.settings),
            metadata={
                "enabled": configured.get("enabled"),
                "enable": [str(item) for item in enable],
                "disable": [str(item) for item in disable],
            },
        )
        await responder.json(200, configured)

    async def _preferences_route(
        self,
        method: str,
        path: str,
        receive: Receive,
        responder: "Responder",
        headers: Mapping[str, str],
    ) -> None:
        user_id = _user_id_from_path(path, "preferences")
        self._authenticate_user_memory_access(headers, user_id)
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
        self.control_plane.record_audit_event(
            "update",
            "user_preferences",
            user_id,
            actor=_audit_actor(self.control_plane, headers, self.settings),
            metadata={"keys": sorted(str(key) for key in preferences.keys())},
        )
        await responder.json(200, {"user_id": user_id, "preferences": saved})

    async def _feedback_route(
        self,
        method: str,
        path: str,
        receive: Receive,
        responder: "Responder",
        headers: Mapping[str, str],
    ) -> None:
        user_id = _user_id_from_path(path, "feedback")
        self._authenticate_user_memory_access(headers, user_id)
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
        self.control_plane.record_audit_event(
            "create",
            "user_feedback",
            user_id,
            actor=_audit_actor(self.control_plane, headers, self.settings),
            metadata={"keys": sorted(str(key) for key in feedback.keys())},
        )
        await responder.json(201, {"user_id": user_id, "feedback": saved})

    def _authenticate_user_memory_access(self, headers: Mapping[str, str], user_id: str) -> None:
        virtual_key = self.control_plane.authenticate(headers, route="memory")
        if virtual_key is not None and virtual_key.user_id and virtual_key.user_id != user_id:
            raise ForbiddenError("virtual key cannot access another user's memory")

    async def _agent_run(self, receive: Receive, responder: "Responder", headers: Mapping[str, str]) -> None:
        call_id = uuid.uuid4().hex
        started = time.perf_counter()
        body = await _read_body(receive, self.settings.max_body_bytes)
        payload = _json_object_from_body(body)

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
            headers=headers,
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
        headers: Mapping[str, str],
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
                user_id=(virtual_key.user_id if virtual_key else _user_id(payload, headers)),
                team_id=virtual_key.team_id if virtual_key else str(headers.get("x-team-id", "")),
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
            "persistent_control_plane": self.control_plane.durable_storage_enabled(),
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
        audit_actor = _audit_actor(self.control_plane, headers, self.settings)
        settings = load_settings()
        self.settings = settings
        self.provider_factory = ProviderFactory(settings, self.provider_factory.http_client)
        self.cache = _cache_from_settings(settings)
        self.guardrails = _guardrails_from_settings(settings)
        self.memory_store = _memory_from_settings(settings)
        self.control_plane.master_key = settings.master_key
        self.control_plane.key_header_name = settings.key_header_name
        self.control_plane.configure_storage(
            storage_backend=settings.control_plane_storage_backend,
            storage_path=settings.control_plane_storage_path,
            storage_redis_url=settings.control_plane_redis_url,
            storage_redis_key=settings.control_plane_redis_key,
            storage_database_url=settings.control_plane_database_url,
            storage_database_table=settings.control_plane_database_table,
            storage_strict=settings.compliance_mode or settings.require_compliance_ready,
        )
        self.control_plane.reload_storage_state()
        self.control_plane.usage_retention_days = settings.usage_retention_days
        self.control_plane.audit_retention_days = settings.audit_retention_days
        self.policy_engine = _policy_from_settings(settings)
        self.plugins = PluginManager(settings.plugin_modules)
        self.control_plane.record_audit_event(
            "reload",
            "config",
            "runtime",
            actor=audit_actor,
            metadata={
                "default_provider": settings.default_provider,
                "cache_backend": settings.cache_backend,
                "user_memory_backend": settings.user_memory_backend,
                "control_plane_storage_backend": settings.control_plane_storage_backend,
                "compliance_mode": settings.compliance_mode,
                "require_compliance_ready": settings.require_compliance_ready,
            },
        )
        await responder.json(200, {"reloaded": True, "providers": self.provider_factory.names()})

    def _compliance_status(self) -> dict[str, object]:
        control_plane_storage_health = self.control_plane.storage_health()
        memory_storage_health = self.memory_store.storage_health()
        cache_storage_health = self.cache.storage_health()
        return {
            "compliance_mode": self.settings.compliance_mode,
            "frameworks": {
                "gdpr": "technical_controls_ready",
                "soc2": "control_evidence_ready",
                "dpdp_india": "technical_controls_ready",
                "ddpa": "alias_for_india_dpdp_in_project_docs",
                "dpa": "operator_contract_required",
            },
            "framework_sources": _framework_sources(),
            "privacy_contacts": {
                "privacy": self.settings.privacy_contact,
                "security": self.settings.security_contact,
                "subprocessors_url": self.settings.subprocessors_url,
                "dpa_url": self.settings.dpa_url,
            },
            "controls": {
                "master_key_required": bool(self.settings.master_key),
                "virtual_keys": True,
                "budget_and_rate_limits": True,
                "audit_log": True,
                "durable_control_plane": self.control_plane.durable_storage_enabled(),
                "control_plane_storage_writable": bool(control_plane_storage_health.get("ok")),
                "durable_user_memory": bool(memory_storage_health.get("ok")),
                "response_cache_storage_ready": bool(cache_storage_health.get("ok")),
                "database_families_supported": _database_families_supported(self.settings),
                "database_transport_encryption": _database_transport_encryption_configured(self.settings),
                "usage_retention": True,
                "subject_access_export": True,
                "subject_erasure": True,
                "privacy_request_register": True,
                "consent_ledger": True,
                "memory_erasure": True,
                "user_memory_backend": self.settings.user_memory_backend,
                "user_memory_database_enabled": self.settings.user_memory_backend == "database",
                "header_redaction": True,
                "secret_references": True,
                "response_cache_enabled": bool(self.cache.enabled),
                "response_cache_personal_data_allowed": self.settings.cache_personal_data_allowed,
                "security_headers": True,
                "guardrails": self.settings.guardrails_enabled,
                "policies": True,
                "provider_routing_controls": True,
                "openapi_contract": True,
            },
            "retention": self.control_plane.retention_status(),
            "privacy_requests": self.control_plane.privacy_request_summary(),
            "consents": self.control_plane.consent_summary(),
            "storage_health": {
                "control_plane": control_plane_storage_health,
                "user_memory": memory_storage_health,
                "response_cache": cache_storage_health,
            },
            "database_families": _configured_database_families(self.settings),
            "database_transport_security": _configured_database_transport_security(self.settings),
            "operator_required": [
                "sign customer data-processing terms where required",
                "maintain subprocessors and transfer-impact records",
                "complete SOC 2 audit with an independent CPA",
                "document incident-response and breach-notification procedures",
                "set lawful processing purpose, notices, and consent workflows",
            ],
        }

    def _compliance_readiness(self) -> dict[str, object]:
        control_plane_storage_health = self.control_plane.storage_health()
        memory_storage_health = self.memory_store.storage_health()
        cache_storage_health = self.cache.storage_health()
        checks = {
            "compliance_mode_enabled": self.settings.compliance_mode,
            "master_key_configured": bool(self.settings.master_key),
            "usage_retention_configured": self.settings.usage_retention_days > 0,
            "audit_retention_configured": self.settings.audit_retention_days > 0,
            "durable_control_plane_configured": self.control_plane.durable_storage_enabled(),
            "control_plane_storage_writable": bool(control_plane_storage_health.get("ok")),
            "durable_user_memory_configured": _user_memory_storage_ready(self.settings) and bool(memory_storage_health.get("ok")),
            "response_cache_storage_configured": _response_cache_storage_ready(self.settings, self.cache) and bool(cache_storage_health.get("ok")),
            "database_families_supported": _database_families_supported(self.settings),
            "database_transport_encryption_configured": _database_transport_encryption_configured(self.settings),
            "privacy_contact_configured": bool(self.settings.privacy_contact),
            "security_contact_configured": bool(self.settings.security_contact),
            "subprocessors_url_configured": bool(self.settings.subprocessors_url),
            "dpa_url_configured": bool(self.settings.dpa_url),
            "cors_not_wildcard": self.settings.cors_allow_origin != "*",
            "response_cache_minimized": (not self.cache.enabled) or self.settings.cache_personal_data_allowed,
        }
        missing = [name for name, ok in checks.items() if not ok]
        return {
            "ready": not missing,
            "required": self.settings.require_compliance_ready,
            "checks": checks,
            "missing": missing,
            "storage_health": {
                "control_plane": control_plane_storage_health,
                "user_memory": memory_storage_health,
                "response_cache": cache_storage_health,
            },
            "database_families": _configured_database_families(self.settings),
            "database_transport_security": _configured_database_transport_security(self.settings),
        }

    def _compliance_evidence(self) -> dict[str, object]:
        return {
            "object": "list",
            "framework_sources": _framework_sources(),
            "data": [
                _evidence(
                    "privacy_by_design_default",
                    ["gdpr", "dpdp_india", "soc2_privacy"],
                    "Data minimization, retention, subject export, subject erasure, and no-store API defaults are built into the gateway.",
                    ["/v1/compliance/status", "/v1/privacy/users/{user_id}/export", "/v1/privacy/users/{user_id}/erase"],
                    mapped_requirements=[
                        "GDPR Article 5(1)(c), 5(1)(e), and 25",
                        "SOC 2 privacy criteria",
                        "India DPDP data minimization, erasure, and fiduciary accountability duties",
                    ],
                ),
                _evidence(
                    "access_control",
                    ["gdpr_security", "soc2_security", "dpdp_india"],
                    "Master key and scoped virtual keys protect admin and application traffic with model, route, budget, RPM, and TPM controls; sanitized auth events retain success and failure evidence without storing raw tokens.",
                    ["/v1/keys", "/v1/service-accounts/keys", "/v1/audit", "/v1/auth/events"],
                    mapped_requirements=[
                        "GDPR Article 32",
                        "SOC 2 common/security criteria",
                        "India DPDP reasonable security safeguards",
                    ],
                ),
                _evidence(
                    "data_subject_rights",
                    ["gdpr", "dpdp_india"],
                    "User-linked data can be exported and erased through authenticated privacy endpoints, and privacy requests can be tracked through a durable request register.",
                    ["/v1/privacy/requests", "/v1/privacy/users/{user_id}/export", "/v1/privacy/users/{user_id}/erase"],
                    mapped_requirements=[
                        "GDPR Articles 12, 15, 16, 17, 18, 20, and 21",
                        "India DPDP Data Principal rights and grievance workflow",
                    ],
                ),
                _evidence(
                    "auditability",
                    ["soc2_security", "soc2_processing_integrity", "gdpr_accountability"],
                    "Administrative creates, updates, deletes, erasures, runtime config reloads, guardrail changes, user-memory writes, endpoint access events, and authentication events are retained with sanitized context subject to configured retention, and evidence digests can be exported for release and review records.",
                    ["/v1/audit", "/v1/compliance/status", "/v1/compliance/integrity"],
                    mapped_requirements=[
                        "GDPR Article 5(2)",
                        "GDPR Article 24",
                        "SOC 2 common/security and processing-integrity evidence",
                    ],
                ),
                _evidence(
                    "endpoint_access_evidence",
                    ["soc2_security", "soc2_processing_integrity", "gdpr_accountability", "dpdp_india"],
                    "Every endpoint records compact access evidence with request ID, method, path, status, latency, auth context, and timestamp; auth decisions record sanitized outcome and reason evidence without persisting request bodies, response bodies, headers, or raw tokens.",
                    ["/v1/access", "/v1/auth/events", "/v1/compliance/report"],
                    mapped_requirements=[
                        "SOC 2 security and processing integrity monitoring",
                        "GDPR accountability and security evidence",
                        "India DPDP security safeguard evidence",
                    ],
                ),
                _evidence(
                    "durable_evidence_storage",
                    ["gdpr_accountability", "soc2_security", "soc2_processing_integrity", "dpdp_india"],
                    "Control-plane keys, users, teams, spend, usage, access, auth, audit, privacy request, erasure, preference, and feedback records can be persisted to Postgres, MySQL, MSSQL, MongoDB, Redis, or an atomic JSON file snapshot on operator-managed encrypted storage.",
                    ["/v1/compliance/status", "/v1/compliance/report", "/v1/metrics"],
                    mapped_requirements=[
                        "GDPR Article 5(2)",
                        "SOC 2 evidence retention",
                        "India DPDP fiduciary accountability evidence",
                    ],
                ),
                _evidence(
                    "confidentiality",
                    ["soc2_confidentiality", "gdpr_security", "dpdp_india"],
                    "Headers and common secrets are redacted from logs; secret values can be loaded through env/file references; response caching is personal-data opt-in in compliance mode.",
                    ["/v1/compliance/status"],
                    mapped_requirements=[
                        "GDPR Article 32",
                        "SOC 2 confidentiality criteria",
                        "India DPDP security safeguards",
                    ],
                ),
                _evidence(
                    "availability_monitoring",
                    ["soc2_availability"],
                    "Health, provider-health, fallback routing, alerts, and metrics support availability monitoring.",
                    ["/health", "/v1/providers/health", "/v1/alerts", "/v1/metrics"],
                    mapped_requirements=["SOC 2 availability criteria"],
                ),
                _evidence(
                    "processing_integrity",
                    ["soc2_processing_integrity"],
                    "OpenAPI, request validation, policy checks, usage events, pricing, tests, and evidence-integrity digests support complete and accurate processing evidence.",
                    ["/openapi.json", "/v1/policies", "/v1/usage", "/v1/pricing", "/v1/compliance/integrity"],
                    mapped_requirements=["SOC 2 processing integrity criteria"],
                ),
                _evidence(
                    "retention_and_storage_limitation",
                    ["gdpr", "dpdp_india", "soc2_privacy"],
                    "Usage, audit, endpoint access, and authentication evidence are automatically pruned to configured retention windows, while response-cache storage is disabled for personal data unless the operator explicitly opts in.",
                    ["/v1/compliance/status", "/v1/compliance/report"],
                    mapped_requirements=[
                        "GDPR Article 5(1)(e)",
                        "SOC 2 privacy criteria",
                        "India DPDP erasure when purpose is complete",
                    ],
                    operator_responsibilities=[
                        "Apply the same retention schedule to cloud logs, backups, provider logs, SIEMs, warehouses, and support systems.",
                    ],
                ),
                _evidence(
                    "subprocessor_and_transfer_governance",
                    ["gdpr_processor", "dpa", "dpdp_india"],
                    "Runtime compliance metadata exposes DPA and subprocessor URLs so customer-facing processor records can be linked from the deployed gateway.",
                    ["/v1/compliance/status", "/v1/compliance/report"],
                    automated=False,
                    mapped_requirements=[
                        "GDPR Article 28",
                        "GDPR Chapter V transfer governance",
                        "Customer DPA and India processor/vendor governance",
                    ],
                    operator_responsibilities=[
                        "Maintain signed customer processor terms, subprocessor notices, transfer mechanism records, and vendor due diligence.",
                    ],
                ),
                _evidence(
                    "breach_and_incident_response",
                    ["gdpr_security", "soc2_security", "dpdp_india"],
                    "Security contact metadata, endpoint access evidence, auth events, audit logs, and usage records support incident investigation and breach assessment.",
                    ["/v1/compliance/status", "/v1/access", "/v1/auth/events", "/v1/audit"],
                    automated=False,
                    mapped_requirements=[
                        "GDPR Articles 33 and 34",
                        "SOC 2 incident response and monitoring evidence",
                        "India DPDP breach intimation obligations",
                    ],
                    operator_responsibilities=[
                        "Maintain detection, triage, legal assessment, notification, regulator/customer communications, and post-incident review procedures.",
                    ],
                ),
                _evidence(
                    "records_of_processing_and_purpose_governance",
                    ["gdpr_accountability", "dpdp_india", "soc2_privacy"],
                    "Required tags, route scopes, model scopes, provider controls, and usage records help link gateway traffic to approved purposes.",
                    ["/v1/keys", "/v1/policies", "/v1/usage", "/v1/compliance/report"],
                    automated=False,
                    mapped_requirements=[
                        "GDPR Articles 5, 24, and 30",
                        "SOC 2 privacy criteria",
                        "India DPDP lawful purpose and notice governance",
                    ],
                    operator_responsibilities=[
                        "Maintain records of processing, approved purposes, privacy notice text, consent or instruction records, and DPIA/TIA records where required.",
                    ],
                ),
                _evidence(
                    "operator_contracts",
                    ["dpa", "gdpr_processor", "dpdp_india"],
                    "Runtime metadata exposes DPA and subprocessor URLs, but the operator must maintain signed terms and vendor records.",
                    ["/v1/compliance/status"],
                    automated=False,
                    mapped_requirements=["GDPR Article 28", "Customer DPA terms", "India processor governance"],
                    operator_responsibilities=["Use counsel-reviewed processing terms and keep executed customer/vendor copies."],
                ),
                _evidence(
                    "independent_attestation",
                    ["soc2"],
                    "The gateway exposes evidence surfaces, but SOC 2 attestation requires an independent CPA examination of the deployed organization.",
                    ["/v1/compliance/status", "/v1/compliance/evidence"],
                    automated=False,
                    mapped_requirements=["AICPA SOC 2 Trust Services Criteria"],
                    operator_responsibilities=["Complete a Type 1 or Type 2 SOC 2 examination over the operator's system and operating period."],
                ),
            ],
        }

    def _compliance_report(self) -> dict[str, object]:
        readiness = self._compliance_readiness()
        evidence = self._compliance_evidence()["data"]
        evidence_items = evidence if isinstance(evidence, list) else []
        production_gate_ready = bool(readiness["ready"] and self.settings.require_compliance_ready)
        automated = [
            _report_control(
                str(item.get("control_id", "")),
                "implemented" if item.get("automated") else "operator_required",
                list(item.get("frameworks", [])) if isinstance(item.get("frameworks"), list) else [],
                str(item.get("description", "")),
                list(item.get("evidence_endpoints", [])) if isinstance(item.get("evidence_endpoints"), list) else [],
            )
            for item in evidence_items
            if isinstance(item, dict)
        ]
        checks = readiness["checks"] if isinstance(readiness["checks"], dict) else {}
        readiness_controls = [
            _report_control(
                control_id=name,
                status="pass" if bool(passed) else "fail",
                frameworks=["gdpr", "soc2", "dpdp_india", "dpa"],
                description=_readiness_description(name),
                evidence=["/health", "/v1/compliance/status"],
            )
            for name, passed in checks.items()
        ]
        operator_tasks = [
            {
                "control_id": "legal_basis_and_notice",
                "status": "operator_required",
                "frameworks": ["gdpr", "dpdp_india"],
                "description": "Define lawful basis, notice, consent or alternative grounds, and customer instructions for each processing purpose.",
                "evidence": ["privacy notice", "record of processing activities", "consent or instruction records"],
            },
            {
                "control_id": "customer_processing_terms",
                "status": "operator_required",
                "frameworks": ["gdpr", "dpa", "dpdp_india"],
                "description": "Execute customer data-processing terms and keep subprocessors, transfer mechanisms, and vendor records current.",
                "evidence": [self.settings.dpa_url or "operator DPA", self.settings.subprocessors_url or "operator subprocessor register"],
            },
            {
                "control_id": "records_of_processing_and_dpia",
                "status": "operator_required",
                "frameworks": ["gdpr", "dpdp_india", "soc2_privacy"],
                "description": "Maintain records of processing, privacy-impact assessments where needed, purpose approvals, and data-flow inventories for prompts, metadata, memories, provider calls, logs, and backups.",
                "evidence": ["record of processing activities", "DPIA or risk assessment", "data inventory", "approved purpose register"],
            },
            {
                "control_id": "identity_verification_and_rights_operations",
                "status": "operator_required",
                "frameworks": ["gdpr", "dpdp_india"],
                "description": "Verify requesters, decide legal validity and exemptions, communicate outcomes, and handle correction, portability, restriction, objection, withdrawal, grievance, nomination, and appeal workflows that happen outside the API.",
                "evidence": ["DSR runbook", "identity-verification log", "/v1/privacy/requests", "customer communications"],
            },
            {
                "control_id": "infrastructure_security_and_encryption",
                "status": "operator_required",
                "frameworks": ["gdpr", "soc2", "dpdp_india"],
                "description": "Operate cloud, network, SSO, encryption-at-rest, backup, logging, vulnerability-management, and employee-access controls outside the gateway process.",
                "evidence": ["cloud security configuration", "SSO policy", "key-management policy", "vulnerability-management records", "access reviews"],
            },
            {
                "control_id": "backup_log_and_provider_retention",
                "status": "operator_required",
                "frameworks": ["gdpr", "dpdp_india", "soc2_privacy"],
                "description": "Align provider consoles, cloud logs, SIEM, dashboards, support systems, analytics stores, and backups with the gateway retention and erasure policy.",
                "evidence": ["retention schedule", "backup policy", "provider retention settings", "log lifecycle policy"],
            },
            {
                "control_id": "incident_and_breach_process",
                "status": "operator_required",
                "frameworks": ["gdpr", "soc2", "dpdp_india"],
                "description": "Maintain breach assessment, notification, escalation, and evidence-retention procedures outside the gateway.",
                "evidence": ["incident response runbook", "security contact", "breach notification records"],
            },
            {
                "control_id": "soc2_independent_examination",
                "status": "independent_attestation_required",
                "frameworks": ["soc2"],
                "description": "SOC 2 can only be attested by an independent CPA over the deployed organization and operating period.",
                "evidence": ["SOC 2 Type 1 or Type 2 report"],
            },
        ]
        return {
            "object": "compliance_report",
            "status": "technical_controls_ready" if production_gate_ready else "technical_controls_incomplete",
            "generated_at": int(time.time()),
            "assurance": {
                "automated_deployment_gate_ready": production_gate_ready,
                "runtime_readiness_ready": bool(readiness["ready"]),
                "health_gate_required": self.settings.require_compliance_ready,
                "legal_compliance": "operator_and_counsel_required",
                "soc2_attestation": "independent_cpa_required",
                "ddpa_note": "DDPA is treated as the user's shorthand for India's DPDP framework in this project.",
                "can_claim_automatic_legal_or_attested_compliance": False,
            },
            "framework_sources": _framework_sources(),
            "readiness": readiness,
            "runtime_controls": self._compliance_status()["controls"],
            "automated_controls": readiness_controls + automated,
            "operator_required_controls": operator_tasks,
            "summary": {
                "automated_controls_total": len(readiness_controls) + sum(1 for item in automated if item["status"] == "implemented"),
                "failed_readiness_checks": readiness["missing"],
                "operator_required_total": len(operator_tasks) + sum(1 for item in automated if item["status"] != "implemented"),
            },
        }

    async def _privacy_route(
        self,
        method: str,
        path: str,
        responder: "Responder",
        headers: Mapping[str, str],
    ) -> None:
        self.control_plane.authenticate(headers, require_admin=True)
        user_id = _privacy_user_id_from_path(path)
        if not user_id:
            await responder.json(400, {"error": {"code": "bad_request", "message": "user_id is required"}})
            return
        if method == "GET" and path.endswith("/export"):
            payload = self.control_plane.export_user_subject(user_id)
            payload["memory"] = {
                "preferences": self.memory_store.get_preferences(user_id),
                "feedback": self.memory_store.get_feedback(user_id),
            }
            await responder.json(200, payload)
            return
        if method in {"DELETE", "POST"} and path.endswith("/erase"):
            result = self.control_plane.erase_user_subject(user_id)
            result["memory"] = self.memory_store.delete_user(user_id)
            result["response_cache_deleted"] = self.cache.clear()
            await responder.json(200, result)
            return
        await responder.json(405, {"error": {"code": "method_not_allowed", "message": "method not allowed"}})

    async def _privacy_requests_route(
        self,
        method: str,
        query: Mapping[str, str],
        receive: Receive,
        responder: "Responder",
        headers: Mapping[str, str],
    ) -> None:
        self.control_plane.authenticate(headers, require_admin=True)
        if method == "GET":
            await responder.json(
                200,
                {
                    "object": "list",
                    "data": self.control_plane.list_privacy_requests(
                        user_id=query.get("user_id", ""),
                        status=query.get("status", ""),
                        limit=_int_query(query.get("limit"), 100),
                    ),
                    "supported_request_types": sorted(SUPPORTED_PRIVACY_REQUEST_TYPES),
                    "supported_statuses": sorted(SUPPORTED_PRIVACY_REQUEST_STATUSES),
                },
            )
            return
        if method == "POST":
            payload = await _json_body(receive, self.settings.max_body_bytes)
            try:
                created = self.control_plane.create_privacy_request(payload)
            except ValueError as exc:
                await responder.json(400, {"error": {"code": "bad_request", "message": str(exc)}})
                return
            await responder.json(201, created)
            return
        await responder.json(405, {"error": {"code": "method_not_allowed", "message": "method not allowed"}})

    async def _privacy_request_item_route(
        self,
        method: str,
        path: str,
        receive: Receive,
        responder: "Responder",
        headers: Mapping[str, str],
    ) -> None:
        self.control_plane.authenticate(headers, require_admin=True)
        request_id = path.removeprefix("/v1/privacy/requests/").strip("/")
        if not request_id:
            await responder.json(400, {"error": {"code": "bad_request", "message": "request_id is required"}})
            return
        if method in {"PATCH", "POST"}:
            try:
                updated = self.control_plane.update_privacy_request(
                    request_id,
                    await _json_body(receive, self.settings.max_body_bytes),
                )
            except ValueError as exc:
                await responder.json(400, {"error": {"code": "bad_request", "message": str(exc)}})
                return
            if updated is None:
                await responder.json(404, {"error": {"code": "not_found", "message": "privacy request not found"}})
                return
            await responder.json(200, updated)
            return
        await responder.json(405, {"error": {"code": "method_not_allowed", "message": "method not allowed"}})

    async def _privacy_consents_route(
        self,
        method: str,
        query: Mapping[str, str],
        receive: Receive,
        responder: "Responder",
        headers: Mapping[str, str],
    ) -> None:
        self.control_plane.authenticate(headers, require_admin=True)
        if method == "GET":
            await responder.json(
                200,
                {
                    "object": "list",
                    "data": self.control_plane.list_consents(
                        user_id=query.get("user_id", ""),
                        status=query.get("status", ""),
                        purpose=query.get("purpose", ""),
                        limit=_int_query(query.get("limit"), 100),
                    ),
                    "supported_statuses": sorted(SUPPORTED_CONSENT_STATUSES),
                },
            )
            return
        if method == "POST":
            payload = await _json_body(receive, self.settings.max_body_bytes)
            try:
                created = self.control_plane.create_consent(payload)
            except ValueError as exc:
                await responder.json(400, {"error": {"code": "bad_request", "message": str(exc)}})
                return
            await responder.json(201, created)
            return
        await responder.json(405, {"error": {"code": "method_not_allowed", "message": "method not allowed"}})

    async def _privacy_consent_item_route(
        self,
        method: str,
        path: str,
        receive: Receive,
        responder: "Responder",
        headers: Mapping[str, str],
    ) -> None:
        self.control_plane.authenticate(headers, require_admin=True)
        suffix = path.removeprefix("/v1/privacy/consents/").strip("/")
        consent_id = suffix.removesuffix("/withdraw").strip("/")
        if not consent_id:
            await responder.json(400, {"error": {"code": "bad_request", "message": "consent_id is required"}})
            return
        if method in {"PATCH", "POST"}:
            try:
                payload = await _json_body(receive, self.settings.max_body_bytes)
                updated = (
                    self.control_plane.withdraw_consent(consent_id, payload)
                    if suffix.endswith("/withdraw")
                    else self.control_plane.update_consent(consent_id, payload)
                )
            except ValueError as exc:
                await responder.json(400, {"error": {"code": "bad_request", "message": str(exc)}})
                return
            if updated is None:
                await responder.json(404, {"error": {"code": "not_found", "message": "consent not found"}})
                return
            await responder.json(200, updated)
            return
        await responder.json(405, {"error": {"code": "method_not_allowed", "message": "method not allowed"}})


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
        storage_backend=resolved_settings.control_plane_storage_backend,
        storage_path=resolved_settings.control_plane_storage_path,
        storage_redis_url=resolved_settings.control_plane_redis_url,
        storage_redis_key=resolved_settings.control_plane_redis_key,
        storage_database_url=resolved_settings.control_plane_database_url,
        storage_database_table=resolved_settings.control_plane_database_table,
        storage_strict=resolved_settings.compliance_mode or resolved_settings.require_compliance_ready,
        usage_retention_days=resolved_settings.usage_retention_days,
        audit_retention_days=resolved_settings.audit_retention_days,
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
    enabled = settings.cache_enabled and (
        not settings.compliance_mode or settings.cache_personal_data_allowed
    )
    if settings.cache_backend == "memory":
        return MemoryResponseCache(
            max_items=settings.cache_max_items,
            ttl_seconds=settings.cache_ttl_seconds,
            enabled=enabled,
        )
    if settings.cache_backend == "database":
        return DatabaseResponseCache(
            url=settings.cache_database_url,
            table_name=settings.cache_database_table,
            max_items=settings.cache_max_items,
            ttl_seconds=settings.cache_ttl_seconds,
            enabled=enabled,
            strict=settings.compliance_mode or settings.require_compliance_ready,
        )
    return RedisResponseCache(
        url=settings.redis_url,
        ttl_seconds=settings.cache_ttl_seconds,
        key_prefix=settings.redis_key_prefix,
        enabled=enabled,
    )


def _memory_from_settings(settings: Settings) -> UserMemoryStore:
    if settings.user_memory_backend == "memory":
        return MemoryUserMemoryStore(enabled=settings.user_memory_enabled)
    if settings.user_memory_backend == "database":
        return DatabaseUserMemoryStore(
            url=settings.user_memory_database_url,
            table_name=settings.user_memory_database_table,
            enabled=settings.user_memory_enabled,
            strict=settings.compliance_mode or settings.require_compliance_ready,
        )
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
    return _json_object_from_body(body)


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


def _auth_context(headers: Mapping[str, str], master_key: str, key_header_name: str) -> str:
    token = headers.get(key_header_name.lower(), "")
    if key_header_name.lower() == "authorization" and token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()
    if not token:
        return "anonymous"
    if master_key and token == master_key:
        return "admin"
    return "credential"


def _audit_actor(control_plane: InMemoryProxyControlPlane, headers: Mapping[str, str], settings: Settings) -> str:
    auth_context = _auth_context(headers, settings.master_key, settings.key_header_name)
    if auth_context == "admin":
        return "master"
    subject = control_plane.subject_from_headers(headers)
    key_preview = subject.get("key_preview", "")
    if key_preview:
        return f"virtual_key:{key_preview}"
    user_id = subject.get("user_id", "")
    if user_id:
        return f"user:{user_id}"
    return auth_context


def _query_params(scope: Scope) -> dict[str, str]:
    raw = scope.get("query_string", b"")
    if isinstance(raw, str):
        raw = raw.encode("latin-1")
    parsed = parse_qs(raw.decode("latin-1"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _int_query(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None and value != "" else default
    except ValueError:
        return default


def _json_payload_or_empty(body: bytes, content_type: str) -> dict[str, object]:
    if "application/json" not in content_type.lower():
        return {}
    return _json_object_from_body(body)


def _json_object_from_body(body: bytes) -> dict[str, object]:
    try:
        payload = loads_bytes(body) if body else {}
    except ValueError as exc:
        raise BadRequestError("JSON body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise BadRequestError("JSON body must be an object")
    return payload


def _user_id(payload: Mapping[str, object], headers: Mapping[str, str]) -> str:
    user = payload.get("user_id") or payload.get("user") or headers.get("x-user-id", "")
    return str(user) if user else ""


def _scoped_cache_key(
    provider: str,
    payload: dict[str, object],
    headers: Mapping[str, str],
    virtual_key: object | None,
) -> str:
    scoped_payload = dict(payload)
    cache_scope: dict[str, object] = {}
    key_preview = str(getattr(virtual_key, "preview", "") or "")
    user_id = str(getattr(virtual_key, "user_id", "") or _user_id(payload, headers))
    team_id = str(getattr(virtual_key, "team_id", "") or headers.get("x-team-id", ""))
    if key_preview:
        cache_scope["key_preview_hash"] = _cache_scope_hash(key_preview)
    if user_id:
        cache_scope["user_hash"] = _cache_scope_hash(user_id)
    if team_id:
        cache_scope["team_hash"] = _cache_scope_hash(team_id)
    if cache_scope:
        scoped_payload["_justfastllm_cache_scope"] = cache_scope
    return cache_key(provider, scoped_payload)


def _cache_scope_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _user_id_from_path(path: str, suffix: str) -> str:
    prefix = "/v1/users/"
    user_id = path.removeprefix(prefix).removesuffix(f"/{suffix}").strip("/")
    return user_id


def _privacy_user_id_from_path(path: str) -> str:
    user_id = path.removeprefix("/v1/privacy/users/")
    for suffix in ("/export", "/erase"):
        if user_id.endswith(suffix):
            return user_id.removesuffix(suffix).strip("/")
    return ""


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


def _framework_sources() -> dict[str, object]:
    reviewed_at = "2026-09-26"
    return {
        "gdpr": {
            "name": "Regulation (EU) 2016/679, General Data Protection Regulation",
            "official_url": "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
            "reviewed_at": reviewed_at,
            "source_type": "official_text",
            "references": [
                "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
            ],
            "mapped_areas": [
                "principles and accountability",
                "data subject rights",
                "privacy by design and default",
                "processor obligations",
                "security of processing",
                "breach notification",
            ],
        },
        "edpb": {
            "name": "European Data Protection Board guidance",
            "official_url": "https://www.edpb.europa.eu/",
            "reviewed_at": reviewed_at,
            "source_type": "official_guidance",
            "references": [
                "https://www.edpb.europa.eu/topics/ai-and-technology/privacy-by-design-and-by-default_en",
                "https://www.edpb.europa.eu/system/files/2026-02/edpb-summary-gdpr-data-protection-design-default_en.pdf",
                "https://www.edpb.europa.eu/topics/key-gdpr-concepts/data-subject-rights_en",
                "https://www.edpb.europa.eu/sme/be-compliant/be-compliant_en",
            ],
            "mapped_areas": [
                "data protection by design and by default",
                "data subject rights",
                "small-organization compliance guidance",
            ],
        },
        "soc2": {
            "name": "AICPA SOC 2 Trust Services Criteria",
            "official_url": "https://www.aicpa-cima.com/resources/landing/system-and-organization-controls-soc-suite-of-services",
            "reviewed_at": reviewed_at,
            "source_type": "official_framework",
            "references": [
                "https://www.aicpa-cima.com/resources/landing/system-and-organization-controls-soc-suite-of-services",
                "https://assets.ctfassets.net/rb9cdnjh59cm/72xv4p67HVXKp6CjWmjkPk/1cdbfa19f6307e2720396b66a6194dc9/trust-services-criteria-updated-copyright.pdf",
            ],
            "mapped_areas": [
                "security",
                "availability",
                "processing integrity",
                "confidentiality",
                "privacy",
            ],
        },
        "dpdp_india": {
            "name": "India Digital Personal Data Protection Act, 2023 and DPDP Rules, 2025",
            "official_url": "https://www.meity.gov.in/digital-personal-data-protection-act-2023-4",
            "reviewed_at": reviewed_at,
            "source_type": "official_text",
            "references": [
                "https://www.meity.gov.in/digital-personal-data-protection-act-2023-4",
                "https://www.meity.gov.in/static/uploads/2024/02/Digital-Personal-Data-Protection-Act-2023.pdf",
                "https://www.meity.gov.in/documents/act-and-policies/digital-personal-data-protection-rules-2025-gDOxUjMtQWa?pageTitle=Digital-Personal-Data-Protection-Rules-2025",
                "https://www.meity.gov.in/static/uploads/2025/11/53450e6e5dc0bfa85ebd78686cadad39.pdf",
            ],
            "mapped_areas": [
                "notice and lawful purpose",
                "Data Principal rights",
                "erasure",
                "security safeguards",
                "breach intimation",
                "grievance workflow",
            ],
        },
        "dpa": {
            "name": "Customer data-processing agreement and processor terms",
            "official_url": "operator_supplied",
            "reviewed_at": reviewed_at,
            "source_type": "operator_supplied_contract",
            "references": [
                "operator_supplied",
            ],
            "mapped_areas": [
                "processing instructions",
                "subprocessors",
                "international transfers",
                "security measures",
                "rights-request assistance",
                "audit evidence",
            ],
        },
    }


def _evidence(
    control_id: str,
    frameworks: list[str],
    description: str,
    evidence_endpoints: list[str],
    *,
    automated: bool = True,
    mapped_requirements: list[str] | None = None,
    operator_responsibilities: list[str] | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {
        "control_id": control_id,
        "frameworks": frameworks,
        "automated": automated,
        "description": description,
        "evidence_endpoints": evidence_endpoints,
    }
    if mapped_requirements is not None:
        item["mapped_requirements"] = mapped_requirements
    if operator_responsibilities is not None:
        item["operator_responsibilities"] = operator_responsibilities
    return item


def _report_control(
    control_id: str,
    status: str,
    frameworks: list[str],
    description: str,
    evidence: list[str],
) -> dict[str, object]:
    return {
        "control_id": control_id,
        "status": status,
        "frameworks": frameworks,
        "description": description,
        "evidence": evidence,
    }


def _readiness_description(control_id: str) -> str:
    descriptions = {
        "compliance_mode_enabled": "Compliance mode is enabled so privacy defaults and evidence metadata are active.",
        "master_key_configured": "A master key protects administrative, privacy, compliance, metrics, and audit endpoints.",
        "usage_retention_configured": "Usage-event retention has a positive retention window.",
        "audit_retention_configured": "Audit, endpoint-access, and authentication-event retention has a positive retention window.",
        "durable_control_plane_configured": "Control-plane keys, usage, spend, access, auth, audit, and erasure evidence use durable storage.",
        "control_plane_storage_writable": "Configured control-plane storage accepts evidence writes and returns matching readback during the readiness check.",
        "durable_user_memory_configured": "Enabled user-memory preferences and feedback use Redis or database storage with write/read health checks.",
        "response_cache_storage_configured": "Enabled response-cache storage uses Redis or database storage with write/read health checks; disabled caches pass this check.",
        "database_families_supported": "Configured database URLs use supported production families: Postgres, MySQL, MSSQL, or MongoDB.",
        "database_transport_encryption_configured": "Enabled remote database URLs explicitly request encrypted transport; local database URLs pass for development and CI.",
        "privacy_contact_configured": "A privacy contact is configured for subject-rights and customer workflows.",
        "security_contact_configured": "A security contact is configured for incident and vulnerability workflows.",
        "subprocessors_url_configured": "A subprocessor URL is configured for customer and vendor-governance evidence.",
        "dpa_url_configured": "A data-processing agreement URL is configured for customer contract evidence.",
        "cors_not_wildcard": "CORS is not configured with a wildcard origin.",
        "response_cache_minimized": "Response-cache storage is disabled or explicitly allowed by the operator.",
    }
    return descriptions.get(control_id, "Compliance-readiness check.")


def _user_memory_storage_ready(settings: Settings) -> bool:
    if not settings.user_memory_enabled:
        return True
    if settings.user_memory_backend == "database":
        return bool(settings.user_memory_database_url and settings.user_memory_database_table)
    if settings.user_memory_backend == "redis":
        return bool(settings.redis_url)
    return False


def _response_cache_storage_ready(settings: Settings, cache: CacheBackend) -> bool:
    if not settings.cache_enabled or not cache.enabled:
        return True
    if settings.cache_backend == "database":
        return bool(settings.cache_database_url and settings.cache_database_table)
    if settings.cache_backend == "redis":
        return bool(settings.redis_url)
    return False


def _configured_database_families(settings: Settings) -> dict[str, str]:
    return {
        name: database_family(url)
        for name, url in _enabled_database_urls(settings).items()
        if url
    }


def _configured_database_transport_security(settings: Settings) -> dict[str, dict[str, object]]:
    return {
        name: database_transport_security(url)
        for name, url in _enabled_database_urls(settings).items()
    }


def _database_families_supported(settings: Settings) -> bool:
    return all(bool(url) and is_supported_database_url(url) for url in _enabled_database_urls(settings).values())


def _database_transport_encryption_configured(settings: Settings) -> bool:
    return all(
        bool(url) and bool(database_transport_security(url)["encrypted"])
        for url in _enabled_database_urls(settings).values()
    )


def _enabled_database_urls(settings: Settings) -> dict[str, str]:
    database_urls: dict[str, str] = {}
    if settings.control_plane_storage_backend == "database":
        database_urls["control_plane"] = settings.control_plane_database_url
    if settings.user_memory_enabled and settings.user_memory_backend == "database":
        database_urls["user_memory"] = settings.user_memory_database_url
    cache_storage_enabled = settings.cache_enabled and (
        not settings.compliance_mode or settings.cache_personal_data_allowed
    )
    if cache_storage_enabled and settings.cache_backend == "database":
        database_urls["response_cache"] = settings.cache_database_url
    return database_urls


def route_inventory_paths() -> set[str]:
    """Route patterns that should appear in OpenAPI and access evidence."""
    return {
        "/health",
        "/openapi.json",
        "/v1/models",
        "/v1/providers/{provider}/models",
        "/v1/chat/completions",
        "/v1/messages",
        *OPENAI_COMPATIBLE_ENDPOINTS.keys(),
        "/v1/agents/runs",
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
        "/v1/users/{user_id}/preferences",
        "/v1/users/{user_id}/feedback",
    }


app = create_app()
