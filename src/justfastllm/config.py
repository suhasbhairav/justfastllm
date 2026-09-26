from __future__ import annotations

import os
import re
import json
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
    "openai": "gpt-5-nano",
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

MODEL_ENV_ALIASES = {
    "openai": ("OPENAI_MODEL",),
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
    cache_personal_data_allowed: bool
    cache_database_url: str
    cache_database_table: str
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
    user_memory_database_url: str = ""
    user_memory_database_table: str = "justfastllm_user_memory"
    block_patterns: tuple[str, ...] = ()
    policy_denied_models: tuple[str, ...] = ()
    policy_allowed_providers: tuple[str, ...] = ()
    policy_required_tags: tuple[str, ...] = ()
    policy_max_prompt_tokens: int = 0
    policy_response_block_patterns: tuple[str, ...] = ()
    plugin_modules: tuple[str, ...] = ()
    master_key: str = ""
    key_header_name: str = "authorization"
    control_plane_storage_backend: str = "memory"
    control_plane_storage_path: str = ""
    control_plane_redis_url: str = ""
    control_plane_redis_key: str = "justfastllm:control-plane:state"
    control_plane_database_url: str = ""
    control_plane_database_table: str = "justfastllm_control_plane_state"
    fallback_providers: tuple[str, ...] = ()
    mirror_provider: str = ""
    compliance_mode: bool = True
    usage_retention_days: int = 90
    audit_retention_days: int = 365
    privacy_contact: str = ""
    security_contact: str = ""
    subprocessors_url: str = ""
    dpa_url: str = ""
    require_compliance_ready: bool = False
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
    config_values = load_config_file(live_env.get("JUSTFASTLLM_CONFIG_FILE", file_values.get("JUSTFASTLLM_CONFIG_FILE", "")))

    def get(name: str, default: str = "") -> str:
        return live_env.get(name, file_values.get(name, config_values.get(name, default)))

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
        cache_personal_data_allowed=_as_bool(get("JUSTFASTLLM_CACHE_PERSONAL_DATA_ALLOWED", "false")),
        cache_database_url=_resolve_secret(get("JUSTFASTLLM_CACHE_DATABASE_URL", ""), get),
        cache_database_table=get("JUSTFASTLLM_CACHE_DATABASE_TABLE", "justfastllm_response_cache"),
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
        user_memory_database_url=_resolve_secret(get("JUSTFASTLLM_USER_MEMORY_DATABASE_URL", ""), get),
        user_memory_database_table=get("JUSTFASTLLM_USER_MEMORY_DATABASE_TABLE", "justfastllm_user_memory"),
        block_patterns=block_patterns,
        policy_denied_models=_csv_tuple(get("JUSTFASTLLM_POLICY_DENIED_MODELS", "")),
        policy_allowed_providers=_csv_tuple(get("JUSTFASTLLM_POLICY_ALLOWED_PROVIDERS", "")),
        policy_required_tags=tuple(item.strip() for item in get("JUSTFASTLLM_POLICY_REQUIRED_TAGS", "").split(",") if item.strip()),
        policy_max_prompt_tokens=int(get("JUSTFASTLLM_POLICY_MAX_PROMPT_TOKENS", "0")),
        policy_response_block_patterns=tuple(
            pattern.strip()
            for pattern in get("JUSTFASTLLM_POLICY_RESPONSE_BLOCK_PATTERNS", "").split(",")
            if pattern.strip()
        ),
        plugin_modules=tuple(item.strip() for item in get("JUSTFASTLLM_PLUGIN_MODULES", "").split(",") if item.strip()),
        master_key=_resolve_secret(get("JUSTFASTLLM_MASTER_KEY", ""), get),
        key_header_name=get("JUSTFASTLLM_KEY_HEADER_NAME", "authorization").lower(),
        control_plane_storage_backend=get("JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND", "").lower()
        or ("file" if get("JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH", "") else "memory"),
        control_plane_storage_path=get("JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH", ""),
        control_plane_redis_url=get("JUSTFASTLLM_CONTROL_PLANE_REDIS_URL", "") or get("REDIS_URL", "redis://localhost:6379/0"),
        control_plane_redis_key=get("JUSTFASTLLM_CONTROL_PLANE_REDIS_KEY", "justfastllm:control-plane:state"),
        control_plane_database_url=_resolve_secret(get("JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL", ""), get),
        control_plane_database_table=get("JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE", "justfastllm_control_plane_state"),
        fallback_providers=_csv_tuple(get("JUSTFASTLLM_FALLBACK_PROVIDERS", "")),
        mirror_provider=get("JUSTFASTLLM_MIRROR_PROVIDER", "").lower(),
        compliance_mode=_as_bool(get("JUSTFASTLLM_COMPLIANCE_MODE", "true")),
        usage_retention_days=int(get("JUSTFASTLLM_USAGE_RETENTION_DAYS", "90")),
        audit_retention_days=int(get("JUSTFASTLLM_AUDIT_RETENTION_DAYS", "365")),
        privacy_contact=get("JUSTFASTLLM_PRIVACY_CONTACT", ""),
        security_contact=get("JUSTFASTLLM_SECURITY_CONTACT", ""),
        subprocessors_url=get("JUSTFASTLLM_SUBPROCESSORS_URL", ""),
        dpa_url=get("JUSTFASTLLM_DPA_URL", ""),
        require_compliance_ready=_as_bool(get("JUSTFASTLLM_REQUIRE_COMPLIANCE_READY", "false")),
        providers=providers,
    )


def load_config_file(path: str | Path) -> dict[str, str]:
    if not path:
        return {}
    config_path = Path(path)
    if not config_path.exists():
        return {}
    if config_path.suffix.lower() == ".json":
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        raw = _load_yaml_like(config_path)
    if not isinstance(raw, dict):
        return {}
    return _flatten_config(raw)


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv_tuple(value: str) -> tuple[str, ...]:
    return tuple(item.strip().lower() for item in value.split(",") if item.strip())


def _flatten_config(raw: Mapping[str, object]) -> dict[str, str]:
    values: dict[str, str] = {}
    server = _dict(raw.get("server"))
    gateway = _dict(raw.get("gateway"))
    cache = _dict(gateway.get("cache"))
    guardrails = _dict(gateway.get("guardrails"))
    memory = _dict(gateway.get("memory"))
    control = _dict(gateway.get("control_plane"))
    compliance = _dict(gateway.get("compliance"))
    policies = _dict(gateway.get("policies"))
    plugins = raw.get("plugins")
    providers = _dict(raw.get("providers"))

    _put(values, "JUSTFASTLLM_HOST", server.get("host"))
    _put(values, "JUSTFASTLLM_PORT", server.get("port"))
    _put(values, "JUSTFASTLLM_LOG_LEVEL", server.get("log_level"))
    _put(values, "JUSTFASTLLM_DEFAULT_PROVIDER", gateway.get("default_provider"))
    _put(values, "JUSTFASTLLM_REQUEST_TIMEOUT_SECONDS", gateway.get("request_timeout_seconds"))
    _put(values, "JUSTFASTLLM_MAX_BODY_BYTES", gateway.get("max_body_bytes"))
    _put(values, "JUSTFASTLLM_MAX_MESSAGE_CHARS", gateway.get("max_message_chars"))
    _put(values, "JUSTFASTLLM_CORS_ALLOW_ORIGIN", gateway.get("cors_allow_origin"))
    _put(values, "REDIS_URL", gateway.get("redis_url"))

    _put(values, "JUSTFASTLLM_CACHE_ENABLED", cache.get("enabled"))
    _put(values, "JUSTFASTLLM_CACHE_BACKEND", cache.get("backend"))
    _put(values, "JUSTFASTLLM_CACHE_TTL_SECONDS", cache.get("ttl_seconds"))
    _put(values, "JUSTFASTLLM_CACHE_MAX_ITEMS", cache.get("max_items"))
    _put(values, "JUSTFASTLLM_CACHE_PERSONAL_DATA_ALLOWED", cache.get("personal_data_allowed"))
    _put(values, "JUSTFASTLLM_CACHE_DATABASE_URL", cache.get("database_url"))
    _put(values, "JUSTFASTLLM_CACHE_DATABASE_TABLE", cache.get("database_table"))
    _put(values, "JUSTFASTLLM_REDIS_KEY_PREFIX", cache.get("redis_key_prefix"))

    _put(values, "JUSTFASTLLM_GUARDRAILS_ENABLED", guardrails.get("enabled"))
    _put_csv(values, "JUSTFASTLLM_DISABLED_GUARDRAILS", guardrails.get("disabled"))
    _put_csv(values, "JUSTFASTLLM_BLOCK_PATTERNS", guardrails.get("block_patterns"))

    _put_csv(values, "JUSTFASTLLM_POLICY_DENIED_MODELS", policies.get("denied_models"))
    _put_csv(values, "JUSTFASTLLM_POLICY_ALLOWED_PROVIDERS", policies.get("allowed_providers"))
    _put_csv(values, "JUSTFASTLLM_POLICY_REQUIRED_TAGS", policies.get("required_tags"))
    _put(values, "JUSTFASTLLM_POLICY_MAX_PROMPT_TOKENS", policies.get("max_prompt_tokens"))
    _put_csv(values, "JUSTFASTLLM_POLICY_RESPONSE_BLOCK_PATTERNS", policies.get("response_block_patterns"))
    _put_csv(values, "JUSTFASTLLM_PLUGIN_MODULES", plugins)

    _put(values, "JUSTFASTLLM_USER_MEMORY_ENABLED", memory.get("enabled"))
    _put(values, "JUSTFASTLLM_USER_MEMORY_BACKEND", memory.get("backend"))
    _put(values, "JUSTFASTLLM_USER_MEMORY_KEY_PREFIX", memory.get("key_prefix"))
    _put(values, "JUSTFASTLLM_USER_MEMORY_DATABASE_URL", memory.get("database_url"))
    _put(values, "JUSTFASTLLM_USER_MEMORY_DATABASE_TABLE", memory.get("database_table"))

    _put(values, "JUSTFASTLLM_MASTER_KEY", control.get("master_key"))
    _put(values, "JUSTFASTLLM_KEY_HEADER_NAME", control.get("key_header_name"))
    _put(values, "JUSTFASTLLM_CONTROL_PLANE_STORAGE_BACKEND", control.get("storage_backend"))
    _put(values, "JUSTFASTLLM_CONTROL_PLANE_STORAGE_PATH", control.get("storage_path"))
    _put(values, "JUSTFASTLLM_CONTROL_PLANE_REDIS_URL", control.get("redis_url"))
    _put(values, "JUSTFASTLLM_CONTROL_PLANE_REDIS_KEY", control.get("redis_key"))
    _put(values, "JUSTFASTLLM_CONTROL_PLANE_DATABASE_URL", control.get("database_url"))
    _put(values, "JUSTFASTLLM_CONTROL_PLANE_DATABASE_TABLE", control.get("database_table"))
    _put_csv(values, "JUSTFASTLLM_FALLBACK_PROVIDERS", control.get("fallback_providers"))
    _put(values, "JUSTFASTLLM_MIRROR_PROVIDER", control.get("mirror_provider"))

    _put(values, "JUSTFASTLLM_COMPLIANCE_MODE", compliance.get("enabled"))
    _put(values, "JUSTFASTLLM_USAGE_RETENTION_DAYS", compliance.get("usage_retention_days"))
    _put(values, "JUSTFASTLLM_AUDIT_RETENTION_DAYS", compliance.get("audit_retention_days"))
    _put(values, "JUSTFASTLLM_PRIVACY_CONTACT", compliance.get("privacy_contact"))
    _put(values, "JUSTFASTLLM_SECURITY_CONTACT", compliance.get("security_contact"))
    _put(values, "JUSTFASTLLM_SUBPROCESSORS_URL", compliance.get("subprocessors_url"))
    _put(values, "JUSTFASTLLM_DPA_URL", compliance.get("dpa_url"))
    _put(values, "JUSTFASTLLM_REQUIRE_COMPLIANCE_READY", compliance.get("require_ready"))

    custom_names = []
    custom = raw.get("custom_openai_compatible_providers", [])
    if isinstance(custom, list):
        for item in custom:
            provider = _dict(item)
            name = str(provider.get("name") or "").strip()
            if not name:
                continue
            custom_names.append(name)
            prefix = _env_prefix(name)
            _put(values, f"{prefix}_API_KEY", provider.get("api_key"))
            _put(values, f"{prefix}_BASE_URL", provider.get("base_url"))
            _put(values, f"{prefix}_DEFAULT_MODEL", provider.get("model") or provider.get("default_model"))
            _put(values, f"{prefix}_REQUIRES_API_KEY", provider.get("requires_api_key"))
    if custom_names:
        values["JUSTFASTLLM_OPENAI_COMPATIBLE_PROVIDERS"] = ",".join(custom_names)

    for name in DEFAULT_BASE_URLS:
        provider = _dict(providers.get(name))
        _put(values, API_KEY_ENV[name], provider.get("api_key"))
        _put(values, BASE_URL_ENV[name], provider.get("base_url"))
        _put(values, MODEL_ENV[name], provider.get("model") or provider.get("default_model"))
    return values


def _load_yaml_like(path: Path) -> dict[str, object]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return _parse_simple_yaml(path.read_text(encoding="utf-8"))
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _parse_simple_yaml(text: str) -> dict[str, object]:
    root: dict[str, object] = {}
    stack: list[tuple[int, object]] = [(-1, root)]
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if stripped.startswith("- "):
            item = _yaml_value(stripped[2:])
            if isinstance(parent, list):
                parent.append(item)
                if isinstance(item, dict):
                    stack.append((indent, item))
            continue
        key, _, raw_value = stripped.partition(":")
        key = key.strip()
        value = raw_value.strip()
        if value:
            _yaml_assign(parent, key, _yaml_value(value))
        else:
            child: dict[str, object] = {}
            _yaml_assign(parent, key, child)
            stack.append((indent, child))
    return root


def _yaml_assign(parent: object, key: str, value: object) -> None:
    if isinstance(parent, dict):
        parent[key] = value


def _yaml_value(value: str) -> object:
    value = value.strip().strip("'").strip('"')
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_yaml_value(item.strip()) for item in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _dict(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _put(values: dict[str, str], name: str, value: object) -> None:
    if value is None:
        return
    values[name] = str(value)


def _put_csv(values: dict[str, str], name: str, value: object) -> None:
    if value is None:
        return
    if isinstance(value, list):
        values[name] = ",".join(str(item) for item in value)
    else:
        values[name] = str(value)


def _load_provider_configs(get: Callable[[str, str], str], timeout: float) -> dict[str, ProviderConfig]:
    providers = {
        name: ProviderConfig(
            name=name,
            api_key=_resolve_secret(get(API_KEY_ENV[name]), get),
            base_url=get(BASE_URL_ENV[name], DEFAULT_BASE_URLS[name]).rstrip("/"),
            default_model=_model_value(get, name),
            timeout_seconds=timeout,
            requires_api_key=name != "ollama",
        )
        for name in DEFAULT_BASE_URLS
    }

    for name in _custom_provider_names(get(OPENAI_COMPATIBLE_PROVIDER_ENV, "")):
        env_prefix = _env_prefix(name)
        providers[name] = ProviderConfig(
            name=name,
            api_key=_resolve_secret(get(f"{env_prefix}_API_KEY"), get),
            base_url=get(f"{env_prefix}_BASE_URL").rstrip("/"),
            default_model=get(f"{env_prefix}_DEFAULT_MODEL", ""),
            timeout_seconds=timeout,
            requires_api_key=_as_bool(get(f"{env_prefix}_REQUIRES_API_KEY", "true")),
        )
    return providers


def _model_value(get: Callable[[str, str], str], provider_name: str) -> str:
    for alias in MODEL_ENV_ALIASES.get(provider_name, ()):
        value = get(alias, "")
        if value:
            return value
    return get(MODEL_ENV[provider_name], DEFAULT_MODELS[provider_name])


def _resolve_secret(value: str, get: Callable[[str, str], str]) -> str:
    if value.startswith("env:"):
        return get(value.removeprefix("env:"), "")
    if value.startswith("file:"):
        path = Path(value.removeprefix("file:"))
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return value


def _custom_provider_names(raw: str) -> tuple[str, ...]:
    names = []
    for name in raw.split(","):
        normalized = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-_")
        if normalized and normalized not in DEFAULT_BASE_URLS:
            names.append(normalized)
    return tuple(dict.fromkeys(names))


def _env_prefix(provider_name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", provider_name.upper()).strip("_")
