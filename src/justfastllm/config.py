from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from justfastllm.models import ProviderConfig


DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "grok": "https://api.x.ai/v1",
    "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "kimi": "https://api.moonshot.ai/v1",
    "ollama": "http://localhost:11434/v1",
}

DEFAULT_MODELS = {
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-sonnet-4-5",
    "deepseek": "deepseek-chat",
    "grok": "grok-4.7",
    "qwen": "qwen-max",
    "kimi": "kimi-k2.6",
    "ollama": "llama3.1",
}

API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "grok": "XAI_API_KEY",
    "qwen": "QWEN_API_KEY",
    "kimi": "KIMI_API_KEY",
    "ollama": "OLLAMA_API_KEY",
}

BASE_URL_ENV = {
    "openai": "OPENAI_BASE_URL",
    "anthropic": "ANTHROPIC_BASE_URL",
    "deepseek": "DEEPSEEK_BASE_URL",
    "grok": "XAI_BASE_URL",
    "qwen": "QWEN_BASE_URL",
    "kimi": "KIMI_BASE_URL",
    "ollama": "OLLAMA_BASE_URL",
}

MODEL_ENV = {
    "openai": "OPENAI_DEFAULT_MODEL",
    "anthropic": "ANTHROPIC_DEFAULT_MODEL",
    "deepseek": "DEEPSEEK_DEFAULT_MODEL",
    "grok": "XAI_DEFAULT_MODEL",
    "qwen": "QWEN_DEFAULT_MODEL",
    "kimi": "KIMI_DEFAULT_MODEL",
    "ollama": "OLLAMA_DEFAULT_MODEL",
}

OPENAI_COMPATIBLE_PROVIDER_ENV = "JUSTFASTLLM_OPENAI_COMPATIBLE_PROVIDERS"


@dataclass(frozen=True, slots=True)
class Settings:
    host: str
    port: int
    log_level: str
    default_provider: str
    cache_enabled: bool
    cache_backend: str
    cache_ttl_seconds: float
    cache_max_items: int
    redis_url: str
    redis_key_prefix: str
    request_timeout_seconds: float
    max_body_bytes: int
    max_message_chars: int
    cors_allow_origin: str
    guardrails_enabled: bool
    disabled_guardrails: tuple[str, ...] = ()
    user_memory_enabled: bool = True
    user_memory_backend: str = "redis"
    user_memory_key_prefix: str = "justfastllm:memory:"
    block_patterns: tuple[str, ...] = ()
    providers: Mapping[str, ProviderConfig] = field(default_factory=dict)


def load_env_file(path: str | Path = ".env") -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = value
    return values


def load_settings(env_file: str | Path = ".env", environ: Mapping[str, str] | None = None) -> Settings:
    file_values = load_env_file(env_file)
    live_env = os.environ if environ is None else environ

    def get(name: str, default: str = "") -> str:
        return live_env.get(name, file_values.get(name, default))

    timeout = float(get("JUSTFASTLLM_REQUEST_TIMEOUT_SECONDS", "60"))
    providers = _load_provider_configs(get, timeout)

    block_patterns = tuple(
        pattern.strip()
        for pattern in get("JUSTFASTLLM_BLOCK_PATTERNS", "").split(",")
        if pattern.strip()
    )

    return Settings(
        host=get("JUSTFASTLLM_HOST", "0.0.0.0"),
        port=int(get("JUSTFASTLLM_PORT", "8000")),
        log_level=get("JUSTFASTLLM_LOG_LEVEL", "INFO").upper(),
        default_provider=get("JUSTFASTLLM_DEFAULT_PROVIDER", "openai").lower(),
        cache_enabled=_as_bool(get("JUSTFASTLLM_CACHE_ENABLED", "true")),
        cache_backend=get("JUSTFASTLLM_CACHE_BACKEND", "redis").lower(),
        cache_ttl_seconds=float(get("JUSTFASTLLM_CACHE_TTL_SECONDS", "60")),
        cache_max_items=int(get("JUSTFASTLLM_CACHE_MAX_ITEMS", "1024")),
        redis_url=get("REDIS_URL", "redis://localhost:6379/0"),
        redis_key_prefix=get("JUSTFASTLLM_REDIS_KEY_PREFIX", "justfastllm:cache:"),
        request_timeout_seconds=timeout,
        max_body_bytes=int(get("JUSTFASTLLM_MAX_BODY_BYTES", "1048576")),
        max_message_chars=int(get("JUSTFASTLLM_MAX_MESSAGE_CHARS", "200000")),
        cors_allow_origin=get("JUSTFASTLLM_CORS_ALLOW_ORIGIN", ""),
        guardrails_enabled=_as_bool(get("JUSTFASTLLM_GUARDRAILS_ENABLED", "true")),
        disabled_guardrails=_csv_tuple(get("JUSTFASTLLM_DISABLED_GUARDRAILS", "")),
        user_memory_enabled=_as_bool(get("JUSTFASTLLM_USER_MEMORY_ENABLED", "true")),
        user_memory_backend=get("JUSTFASTLLM_USER_MEMORY_BACKEND", "redis").lower(),
        user_memory_key_prefix=get("JUSTFASTLLM_USER_MEMORY_KEY_PREFIX", "justfastllm:memory:"),
        block_patterns=block_patterns,
        providers=providers,
    )


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv_tuple(value: str) -> tuple[str, ...]:
    return tuple(item.strip().lower() for item in value.split(",") if item.strip())


def _load_provider_configs(get: Callable[[str, str], str], timeout: float) -> dict[str, ProviderConfig]:
    providers = {
        name: ProviderConfig(
            name=name,
            api_key=get(API_KEY_ENV[name]),
            base_url=get(BASE_URL_ENV[name], DEFAULT_BASE_URLS[name]).rstrip("/"),
            default_model=get(MODEL_ENV[name], DEFAULT_MODELS[name]),
            timeout_seconds=timeout,
            requires_api_key=name != "ollama",
        )
        for name in DEFAULT_BASE_URLS
    }

    for name in _custom_provider_names(get(OPENAI_COMPATIBLE_PROVIDER_ENV, "")):
        env_prefix = _env_prefix(name)
        providers[name] = ProviderConfig(
            name=name,
            api_key=get(f"{env_prefix}_API_KEY"),
            base_url=get(f"{env_prefix}_BASE_URL").rstrip("/"),
            default_model=get(f"{env_prefix}_DEFAULT_MODEL", ""),
            timeout_seconds=timeout,
            requires_api_key=_as_bool(get(f"{env_prefix}_REQUIRES_API_KEY", "true")),
        )
    return providers


def _custom_provider_names(raw: str) -> tuple[str, ...]:
    names = []
    for name in raw.split(","):
        normalized = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-_")
        if normalized and normalized not in DEFAULT_BASE_URLS:
            names.append(normalized)
    return tuple(dict.fromkeys(names))


def _env_prefix(provider_name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", provider_name.upper()).strip("_")
