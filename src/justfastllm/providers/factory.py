from __future__ import annotations

from collections.abc import Callable, Mapping

from justfastllm.config import Settings
from justfastllm.errors import ProviderNotFoundError
from justfastllm.http import HttpClient, PooledHttpClient
from justfastllm.providers.anthropic import AnthropicProvider
from justfastllm.providers.base import ProviderClient
from justfastllm.providers.openai_compatible import OpenAICompatibleProvider


ProviderBuilder = Callable[[str], ProviderClient]


class ProviderFactory:
    def __init__(self, settings: Settings, http_client: HttpClient | None = None) -> None:
        self.settings = settings
        self.http_client = http_client or PooledHttpClient()
        self._builders: dict[str, ProviderBuilder] = {}
        self.register_defaults()

    def register(self, name: str, builder: ProviderBuilder) -> None:
        self._builders[name.lower()] = builder

    def register_defaults(self) -> None:
        for name in self.settings.providers:
            if name == "anthropic":
                self.register(name, lambda provider_name, n=name: AnthropicProvider(self.settings.providers[n], self.http_client))
            else:
                self.register(name, lambda provider_name, n=name: OpenAICompatibleProvider(self.settings.providers[n], self.http_client))

    def create(self, name: str) -> ProviderClient:
        provider_name = name.lower()
        builder = self._builders.get(provider_name)
        if builder is None or provider_name not in self.settings.providers:
            raise ProviderNotFoundError(f"provider '{name}' is not configured")
        return builder(provider_name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._builders))

    def resolve(self, payload: dict[str, object], headers: Mapping[str, str]) -> tuple[str, dict[str, object]]:
        routed_payload = dict(payload)
        provider = str(
            routed_payload.pop("provider", "")
            or headers.get("x-llm-provider", "")
            or self._provider_from_model(routed_payload)
            or self.settings.default_provider
        ).lower()
        return provider, self._strip_model_prefix(provider, routed_payload)

    def _provider_from_model(self, payload: Mapping[str, object]) -> str:
        model = payload.get("model")
        if not isinstance(model, str) or "/" not in model:
            return ""
        prefix = model.split("/", 1)[0].lower()
        return prefix if prefix in self._builders else ""

    def _strip_model_prefix(self, provider: str, payload: dict[str, object]) -> dict[str, object]:
        model = payload.get("model")
        if isinstance(model, str) and model.lower().startswith(f"{provider}/"):
            payload["model"] = model.split("/", 1)[1]
        return payload
