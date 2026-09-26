from __future__ import annotations

import asyncio

from justfastllm.errors import BadRequestError, ProviderAuthError, ProviderConfigurationError
from justfastllm.http import HttpClient
from justfastllm.jsonutil import dumps_bytes, loads_bytes
from justfastllm.models import HttpRequest, ProviderConfig, UpstreamResponse


class OpenAICompatibleProvider:
    def __init__(self, config: ProviderConfig, http_client: HttpClient) -> None:
        self.config = config
        self.http_client = http_client

    async def chat_completion(self, payload: dict[str, object]) -> UpstreamResponse:
        if self.config.requires_api_key and not self.config.api_key:
            raise ProviderAuthError(f"{self.config.name} API key is not configured")
        if not self.config.base_url:
            raise ProviderConfigurationError(f"{self.config.name} base URL is not configured")

        upstream_payload = dict(payload)
        upstream_payload.pop("provider", None)
        upstream_payload.pop("user_id", None)
        model = upstream_payload.get("model") or self.config.default_model
        if not model:
            raise BadRequestError(f"model is required for provider '{self.config.name}'")
        upstream_payload["model"] = model

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "justfastllm/0.1",
            **dict(self.config.extra_headers),
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        request = HttpRequest(
            method="POST",
            url=f"{self.config.base_url}/chat/completions",
            headers=headers,
            body=dumps_bytes(upstream_payload),
            timeout=self.config.timeout_seconds,
        )
        return await asyncio.to_thread(self.http_client.send, request)

    async def messages(self, payload: dict[str, object]) -> UpstreamResponse:
        return await self.chat_completion(payload)

    async def models(self) -> UpstreamResponse:
        if self.config.requires_api_key and not self.config.api_key:
            raise ProviderAuthError(f"{self.config.name} API key is not configured")
        if not self.config.base_url:
            raise ProviderConfigurationError(f"{self.config.name} base URL is not configured")

        headers = {
            "Accept": "application/json",
            "User-Agent": "justfastllm/0.1",
            **dict(self.config.extra_headers),
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        request = HttpRequest(
            method="GET",
            url=f"{self.config.base_url}/models",
            headers=headers,
            body=b"",
            timeout=self.config.timeout_seconds,
        )
        return await asyncio.to_thread(self.http_client.send, request)

    async def openai_endpoint(self, endpoint: str, body: bytes, content_type: str) -> UpstreamResponse:
        if self.config.requires_api_key and not self.config.api_key:
            raise ProviderAuthError(f"{self.config.name} API key is not configured")
        if not self.config.base_url:
            raise ProviderConfigurationError(f"{self.config.name} base URL is not configured")

        cleaned_body = _clean_gateway_json_body(body, self.config.default_model) if _is_json(content_type) else body
        headers = {
            "Content-Type": content_type or "application/json",
            "Accept": "application/json",
            "User-Agent": "justfastllm/0.1",
            **dict(self.config.extra_headers),
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        request = HttpRequest(
            method="POST",
            url=f"{self.config.base_url}/{endpoint.strip('/')}",
            headers=headers,
            body=cleaned_body,
            timeout=self.config.timeout_seconds,
        )
        return await asyncio.to_thread(self.http_client.send, request)


def _is_json(content_type: str) -> bool:
    return "application/json" in content_type.lower()


def _clean_gateway_json_body(body: bytes, default_model: str) -> bytes:
    payload = loads_bytes(body) if body else {}
    if not isinstance(payload, dict):
        raise BadRequestError("JSON body must be an object")
    upstream_payload = dict(payload)
    for key in {
        "provider",
        "user_id",
        "fallback_providers",
        "mirror_provider",
        "tags",
    }:
        upstream_payload.pop(key, None)
    if default_model and not upstream_payload.get("model"):
        upstream_payload["model"] = default_model
    return dumps_bytes(upstream_payload)
