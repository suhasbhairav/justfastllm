from __future__ import annotations

import asyncio
import time
from typing import Any

from justfastllm.errors import BadRequestError, ProviderAuthError, ProviderConfigurationError
from justfastllm.http import HttpClient
from justfastllm.jsonutil import dumps_bytes, loads_bytes
from justfastllm.models import HttpRequest, ProviderConfig, UpstreamResponse


class AnthropicProvider:
    def __init__(self, config: ProviderConfig, http_client: HttpClient) -> None:
        self.config = config
        self.http_client = http_client

    async def chat_completion(self, payload: dict[str, object]) -> UpstreamResponse:
        if not self.config.api_key:
            raise ProviderAuthError("anthropic API key is not configured")
        if not self.config.base_url:
            raise ProviderConfigurationError("anthropic base URL is not configured")

        stream = bool(payload.get("stream", False))
        response = await self._send_messages(payload)
        if stream or response.status_code >= 400:
            return response
        return UpstreamResponse(
            status_code=response.status_code,
            headers=response.headers,
            body=dumps_bytes(self._to_openai_response(loads_bytes(response.body))),
        )

    async def messages(self, payload: dict[str, object]) -> UpstreamResponse:
        if not self.config.api_key:
            raise ProviderAuthError("anthropic API key is not configured")
        if not self.config.base_url:
            raise ProviderConfigurationError("anthropic base URL is not configured")
        return await self._send_messages(payload)

    async def models(self) -> UpstreamResponse:
        if not self.config.api_key:
            raise ProviderAuthError("anthropic API key is not configured")
        if not self.config.base_url:
            raise ProviderConfigurationError("anthropic base URL is not configured")

        request = HttpRequest(
            method="GET",
            url=f"{self.config.base_url}/models",
            headers={
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
                "Accept": "application/json",
                "User-Agent": "justfastllm/0.1",
                **dict(self.config.extra_headers),
            },
            body=b"",
            timeout=self.config.timeout_seconds,
        )
        return await asyncio.to_thread(self.http_client.send, request)

    async def openai_endpoint(self, endpoint: str, body: bytes, content_type: str) -> UpstreamResponse:
        if endpoint.strip("/") == "messages":
            payload = loads_bytes(body) if body else {}
            if not isinstance(payload, dict):
                raise BadRequestError("JSON body must be an object")
            return await self.messages(payload)
        raise BadRequestError(f"endpoint '{endpoint}' is not supported by provider '{self.config.name}'")

    async def _send_messages(self, payload: dict[str, object]) -> UpstreamResponse:
        request = HttpRequest(
            method="POST",
            url=f"{self.config.base_url}/messages",
            headers={
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "justfastllm/0.1",
                **dict(self.config.extra_headers),
            },
            body=dumps_bytes(self._to_anthropic_payload(payload)),
            timeout=self.config.timeout_seconds,
        )
        return await asyncio.to_thread(self.http_client.send, request)

    def _to_anthropic_payload(self, payload: dict[str, object]) -> dict[str, object]:
        messages = payload.get("messages", [])
        system_parts: list[str] = []
        anthropic_messages: list[dict[str, object]] = []

        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role", "user"))
                content = message.get("content", "")
                if role == "system":
                    system_parts.append(_content_to_text(content))
                    continue
                anthropic_messages.append(
                    {
                        "role": "assistant" if role == "assistant" else "user",
                        "content": content,
                    }
                )

        model = payload.get("model") or self.config.default_model
        if not model:
            raise BadRequestError(f"model is required for provider '{self.config.name}'")

        result: dict[str, object] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": payload.get("max_tokens") or payload.get("max_completion_tokens") or 1024,
        }
        if system_parts:
            result["system"] = "\n\n".join(system_parts)

        passthrough = {
            "temperature",
            "top_p",
            "top_k",
            "metadata",
            "stream",
            "tools",
            "tool_choice",
        }
        for key in passthrough:
            if key in payload:
                result[key] = payload[key]
        if "stop" in payload:
            stop = payload["stop"]
            result["stop_sequences"] = stop if isinstance(stop, list) else [stop]
        return result

    def _to_openai_response(self, response: dict[str, Any]) -> dict[str, object]:
        content = response.get("content", [])
        text = _content_to_text(content)
        usage = response.get("usage", {})
        prompt_tokens = _int_from_mapping(usage, "input_tokens")
        completion_tokens = _int_from_mapping(usage, "output_tokens")
        return {
            "id": response.get("id", ""),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": response.get("model", self.config.default_model),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": _map_stop_reason(response.get("stop_reason")),
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif "content" in item:
                    parts.append(_content_to_text(item["content"]))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content) if content is not None else ""


def _int_from_mapping(value: object, key: str) -> int:
    if isinstance(value, dict):
        raw = value.get(key, 0)
        return raw if isinstance(raw, int) else 0
    return 0


def _map_stop_reason(stop_reason: object) -> str:
    mapping = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
    }
    return mapping.get(str(stop_reason), "stop")
