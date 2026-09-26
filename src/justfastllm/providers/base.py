from __future__ import annotations

from typing import Protocol

from justfastllm.models import ProviderConfig, UpstreamResponse


class ProviderClient(Protocol):
    config: ProviderConfig

    async def chat_completion(self, payload: dict[str, object]) -> UpstreamResponse:
        """Send a chat completion request to the provider."""

    async def messages(self, payload: dict[str, object]) -> UpstreamResponse:
        """Send a messages-style request to the provider."""

    async def models(self) -> UpstreamResponse:
        """List models from the provider."""

    async def openai_endpoint(self, endpoint: str, body: bytes, content_type: str) -> UpstreamResponse:
        """Send a raw OpenAI-compatible request to a provider endpoint."""
