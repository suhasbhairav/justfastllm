from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from justfastllm.errors import BudgetExceededError, ForbiddenError, RateLimitError, UnauthorizedError
from justfastllm.jsonutil import loads_bytes


MODEL_PRICES_PER_1K = {
    "gpt-5-nano": (0.00005, 0.0004),
    "gpt-5-mini": (0.00025, 0.002),
    "gpt-5": (0.00125, 0.01),
    "claude-sonnet-4-5": (0.003, 0.015),
    "deepseek-chat": (0.00014, 0.00028),
    "grok-4.7": (0.003, 0.015),
    "qwen-max": (0.0016, 0.0064),
    "kimi-k2.6": (0.0006, 0.0025),
    "llama3.1": (0.0, 0.0),
}


@dataclass(slots=True)
class VirtualKey:
    token_hash: str
    preview: str
    name: str
    user_id: str
    team_id: str
    models: tuple[str, ...]
    rpm_limit: int
    tpm_limit: int
    budget_usd: float
    aliases: dict[str, str] = field(default_factory=dict)
    allowed_routes: tuple[str, ...] = ()
    key_type: str = "virtual"
    spend_usd: float = 0.0
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    disabled: bool = False
    metadata: dict[str, object] = field(default_factory=dict)

    def public(self) -> dict[str, object]:
        return {
            "preview": self.preview,
            "name": self.name,
            "user_id": self.user_id,
            "team_id": self.team_id,
            "models": list(self.models),
            "rpm_limit": self.rpm_limit,
            "tpm_limit": self.tpm_limit,
            "budget_usd": self.budget_usd,
            "aliases": self.aliases,
            "allowed_routes": list(self.allowed_routes),
            "key_type": self.key_type,
            "spend_usd": round(self.spend_usd, 8),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "disabled": self.disabled,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class UserAccount:
    user_id: str
    user_email: str = ""
    models: tuple[str, ...] = ()
    max_budget: float = 0.0
    spend_usd: float = 0.0
    rpm_limit: int = 0
    tpm_limit: int = 0
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, object] = field(default_factory=dict)

    def public(self) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "user_email": self.user_email,
            "models": list(self.models),
            "max_budget": self.max_budget,
            "spend_usd": round(self.spend_usd, 8),
            "rpm_limit": self.rpm_limit,
            "tpm_limit": self.tpm_limit,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class TeamAccount:
    team_id: str
    team_alias: str = ""
    models: tuple[str, ...] = ()
    max_budget: float = 0.0
    spend_usd: float = 0.0
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, object] = field(default_factory=dict)

    def public(self) -> dict[str, object]:
        return {
            "team_id": self.team_id,
            "team_alias": self.team_alias,
            "models": list(self.models),
            "max_budget": self.max_budget,
            "spend_usd": round(self.spend_usd, 8),
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class RequestEvent:
    call_id: str
    route: str
    provider: str
    model: str
    status_code: int
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    cache: str
    key_preview: str
    user_id: str
    team_id: str
    tags: tuple[str, ...]
    created_at: float = field(default_factory=time.time)

    def public(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "route": self.route,
            "provider": self.provider,
            "model": self.model,
            "status_code": self.status_code,
            "latency_ms": round(self.latency_ms, 3),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 8),
            "cache": self.cache,
            "key_preview": self.key_preview,
            "user_id": self.user_id,
            "team_id": self.team_id,
            "tags": list(self.tags),
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class AuditEvent:
    action: str
    target_type: str
    target_id: str
    actor: str
    metadata: dict[str, object] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def public(self) -> dict[str, object]:
        return {
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "actor": self.actor,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


class InMemoryProxyControlPlane:
    def __init__(
        self,
        *,
        master_key: str = "",
        key_header_name: str = "authorization",
        storage_path: str | Path = "",
        max_events: int = 5000,
    ) -> None:
        self.master_key = master_key
        self.key_header_name = key_header_name.lower()
        self.storage_path = Path(storage_path) if storage_path else None
        self._keys: dict[str, VirtualKey] = {}
        self._users: dict[str, UserAccount] = {}
        self._teams: dict[str, TeamAccount] = {}
        self._events: deque[RequestEvent] = deque(maxlen=max_events)
        self._audit_events: deque[AuditEvent] = deque(maxlen=max_events)
        self._request_windows: dict[str, deque[float]] = {}
        self._token_windows: dict[str, deque[tuple[float, int]]] = {}
        self._load()

    def enabled(self) -> bool:
        return bool(self.master_key or self._keys)

    def is_admin(self, headers: Mapping[str, str]) -> bool:
        token = bearer_token(headers, self.key_header_name)
        return bool(self.master_key and token == self.master_key)

    def generate_key(self, payload: Mapping[str, object]) -> dict[str, object]:
        token = "sk-jfl-" + secrets.token_urlsafe(32)
        token_hash = hash_token(token)
        key = VirtualKey(
            token_hash=token_hash,
            preview=preview_token(token),
            name=str(payload.get("name") or "default"),
            user_id=str(payload.get("user_id") or ""),
            team_id=str(payload.get("team_id") or ""),
            models=_string_tuple(payload.get("models")),
            rpm_limit=_int_value(payload.get("rpm_limit"), 0),
            tpm_limit=_int_value(payload.get("tpm_limit"), 0),
            budget_usd=_float_value(payload.get("budget_usd"), 0.0),
            aliases=_string_dict(payload.get("aliases")),
            allowed_routes=_string_tuple(payload.get("allowed_routes")),
            key_type=str(payload.get("key_type") or "virtual"),
            expires_at=_float_value(payload.get("expires_at"), 0.0),
            metadata=dict(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
        )
        self._keys[token_hash] = key
        self._audit("create", "key", key.preview, metadata={"key_type": key.key_type, "name": key.name})
        self._save()
        public = key.public()
        public["key"] = token
        return public

    def list_keys(self) -> list[dict[str, object]]:
        return [key.public() for key in self._keys.values()]

    def key_info(self, token: str) -> dict[str, object] | None:
        key = self._key_for_identifier(token)
        return key.public() if key else None

    def delete_key(self, token: str) -> bool:
        key = self._key_for_identifier(token)
        if key is not None:
            self._keys.pop(key.token_hash, None)
        deleted = key is not None
        if deleted:
            self._audit("delete", "key", key.preview if key else preview_token(token))
            self._save()
        return deleted

    def update_key(self, token: str, payload: Mapping[str, object]) -> dict[str, object] | None:
        key = self._key_for_identifier(token)
        if key is None:
            return None
        if "name" in payload:
            key.name = str(payload.get("name") or key.name)
        if "user_id" in payload:
            key.user_id = str(payload.get("user_id") or "")
        if "team_id" in payload:
            key.team_id = str(payload.get("team_id") or "")
        if "models" in payload:
            key.models = _string_tuple(payload.get("models"))
        if "rpm_limit" in payload:
            key.rpm_limit = _int_value(payload.get("rpm_limit"), key.rpm_limit)
        if "tpm_limit" in payload:
            key.tpm_limit = _int_value(payload.get("tpm_limit"), key.tpm_limit)
        if "budget_usd" in payload:
            key.budget_usd = _float_value(payload.get("budget_usd"), key.budget_usd)
        if "expires_at" in payload:
            key.expires_at = _float_value(payload.get("expires_at"), key.expires_at)
        if "disabled" in payload:
            key.disabled = bool(payload.get("disabled"))
        if "aliases" in payload:
            key.aliases = _string_dict(payload.get("aliases"))
        if "allowed_routes" in payload:
            key.allowed_routes = _string_tuple(payload.get("allowed_routes"))
        if "metadata" in payload and isinstance(payload.get("metadata"), dict):
            key.metadata = dict(payload["metadata"])
        self._audit("update", "key", key.preview, metadata={"name": key.name})
        self._save()
        return key.public()

    def create_user(self, payload: Mapping[str, object]) -> dict[str, object]:
        user_id = str(payload.get("user_id") or f"user-{uuid.uuid4().hex[:12]}")
        user = UserAccount(
            user_id=user_id,
            user_email=str(payload.get("user_email") or payload.get("email") or ""),
            models=_string_tuple(payload.get("models")),
            max_budget=_float_value(payload.get("max_budget") or payload.get("budget_usd"), 0.0),
            rpm_limit=_int_value(payload.get("rpm_limit"), 0),
            tpm_limit=_int_value(payload.get("tpm_limit"), 0),
            metadata=dict(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
        )
        self._users[user_id] = user
        self._audit("create", "user", user_id)
        self._save()
        return user.public()

    def user_info(self, user_id: str) -> dict[str, object] | None:
        user = self._users.get(user_id)
        if user is None:
            return None
        payload = user.public()
        payload["keys"] = [key.public() for key in self._keys.values() if key.user_id == user_id]
        return payload

    def list_users(self) -> list[dict[str, object]]:
        return [user.public() for user in self._users.values()]

    def update_user(self, user_id: str, payload: Mapping[str, object]) -> dict[str, object] | None:
        user = self._users.get(user_id)
        if user is None:
            return None
        if "user_email" in payload or "email" in payload:
            user.user_email = str(payload.get("user_email") or payload.get("email") or "")
        if "models" in payload:
            user.models = _string_tuple(payload.get("models"))
        if "max_budget" in payload or "budget_usd" in payload:
            user.max_budget = _float_value(payload.get("max_budget") or payload.get("budget_usd"), user.max_budget)
        if "rpm_limit" in payload:
            user.rpm_limit = _int_value(payload.get("rpm_limit"), user.rpm_limit)
        if "tpm_limit" in payload:
            user.tpm_limit = _int_value(payload.get("tpm_limit"), user.tpm_limit)
        if "metadata" in payload and isinstance(payload.get("metadata"), dict):
            user.metadata = dict(payload["metadata"])
        self._audit("update", "user", user_id)
        self._save()
        return user.public()

    def delete_user(self, user_id: str) -> bool:
        deleted = self._users.pop(user_id, None) is not None
        if deleted:
            self._audit("delete", "user", user_id)
            self._save()
        return deleted

    def create_team(self, payload: Mapping[str, object]) -> dict[str, object]:
        team_id = str(payload.get("team_id") or f"team-{uuid.uuid4().hex[:12]}")
        team = TeamAccount(
            team_id=team_id,
            team_alias=str(payload.get("team_alias") or payload.get("name") or ""),
            models=_string_tuple(payload.get("models")),
            max_budget=_float_value(payload.get("max_budget") or payload.get("budget_usd"), 0.0),
            metadata=dict(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
        )
        self._teams[team_id] = team
        self._audit("create", "team", team_id)
        self._save()
        return team.public()

    def team_info(self, team_id: str) -> dict[str, object] | None:
        team = self._teams.get(team_id)
        if team is None:
            return None
        payload = team.public()
        payload["keys"] = [key.public() for key in self._keys.values() if key.team_id == team_id]
        return payload

    def list_teams(self) -> list[dict[str, object]]:
        return [team.public() for team in self._teams.values()]

    def update_team(self, team_id: str, payload: Mapping[str, object]) -> dict[str, object] | None:
        team = self._teams.get(team_id)
        if team is None:
            return None
        if "team_alias" in payload or "name" in payload:
            team.team_alias = str(payload.get("team_alias") or payload.get("name") or "")
        if "models" in payload:
            team.models = _string_tuple(payload.get("models"))
        if "max_budget" in payload or "budget_usd" in payload:
            team.max_budget = _float_value(payload.get("max_budget") or payload.get("budget_usd"), team.max_budget)
        if "metadata" in payload and isinstance(payload.get("metadata"), dict):
            team.metadata = dict(payload["metadata"])
        self._audit("update", "team", team_id)
        self._save()
        return team.public()

    def delete_team(self, team_id: str) -> bool:
        deleted = self._teams.pop(team_id, None) is not None
        if deleted:
            self._audit("delete", "team", team_id)
            self._save()
        return deleted

    def generate_service_account_key(self, payload: Mapping[str, object]) -> dict[str, object]:
        enriched = dict(payload)
        enriched["key_type"] = "service_account"
        enriched.setdefault("name", "service-account")
        enriched.setdefault("user_id", "")
        return self.generate_key(enriched)

    def authenticate(
        self,
        headers: Mapping[str, str],
        *,
        model: str = "",
        estimated_tokens: int = 0,
        route: str = "",
        require_admin: bool = False,
    ) -> VirtualKey | None:
        if require_admin:
            if self.is_admin(headers):
                return None
            raise UnauthorizedError("proxy admin key is required")

        if not self.enabled():
            return None
        token = bearer_token(headers, self.key_header_name)
        if self.master_key and token == self.master_key:
            return None
        if not token:
            raise UnauthorizedError("Bearer token is required")
        key = self._keys.get(hash_token(token))
        if key is None or key.disabled:
            raise UnauthorizedError("invalid or disabled virtual key")
        if key.expires_at and time.time() > key.expires_at:
            raise UnauthorizedError("virtual key has expired")
        if key.allowed_routes and route and not _route_allowed(route, key.allowed_routes):
            raise ForbiddenError(f"virtual key cannot access route '{route}'")
        access_model = key.aliases.get(model, model)
        if key.models and model and not _model_allowed(access_model, key.models):
            raise ForbiddenError(f"virtual key cannot access model '{model}'")
        self._enforce_rate_limits(key, estimated_tokens)
        if key.budget_usd and key.spend_usd >= key.budget_usd:
            raise BudgetExceededError("virtual key budget is exhausted")
        user = self._users.get(key.user_id)
        if user and user.max_budget and user.spend_usd >= user.max_budget:
            raise BudgetExceededError("user budget is exhausted")
        team = self._teams.get(key.team_id)
        if team and team.max_budget and team.spend_usd >= team.max_budget:
            raise BudgetExceededError("team budget is exhausted")
        return key

    def record_event(self, event: RequestEvent, key: VirtualKey | None = None) -> None:
        self._events.append(event)
        if key is not None:
            key.spend_usd += event.cost_usd
            if key.user_id in self._users:
                self._users[key.user_id].spend_usd += event.cost_usd
            if key.team_id in self._teams:
                self._teams[key.team_id].spend_usd += event.cost_usd
        self._save()

    def apply_aliases(self, key: VirtualKey, payload: Mapping[str, object]) -> dict[str, object]:
        routed = dict(payload)
        model = routed.get("model")
        if isinstance(model, str) and model in key.aliases:
            routed["model"] = key.aliases[model]
        return routed

    def events(self, limit: int = 100) -> list[dict[str, object]]:
        return [event.public() for event in list(self._events)[-limit:]][::-1]

    def audit_events(self, limit: int = 100) -> list[dict[str, object]]:
        return [event.public() for event in list(self._audit_events)[-limit:]][::-1]

    def metrics(self) -> dict[str, object]:
        events = list(self._events)
        total_cost = sum(event.cost_usd for event in events)
        total_tokens = sum(event.total_tokens for event in events)
        latencies = sorted(event.latency_ms for event in events)
        by_model: dict[str, dict[str, object]] = {}
        by_provider: dict[str, dict[str, object]] = {}
        for event in events:
            _rollup(by_model, event.model, event)
            _rollup(by_provider, event.provider, event)
        return {
            "requests": len(events),
            "tokens": total_tokens,
            "cost_usd": round(total_cost, 8),
            "latency_ms": {
                "p50": round(_percentile(latencies, 50), 3),
                "p95": round(_percentile(latencies, 95), 3),
                "p99": round(_percentile(latencies, 99), 3),
            },
            "cache": {
                "hits": sum(1 for event in events if event.cache == "hit"),
                "misses": sum(1 for event in events if event.cache == "miss"),
            },
            "by_model": by_model,
            "by_provider": by_provider,
            "keys": {
                "count": len(self._keys),
                "active": sum(1 for key in self._keys.values() if not key.disabled),
            },
            "users": {
                "count": len(self._users),
                "spend_usd": round(sum(user.spend_usd for user in self._users.values()), 8),
            },
            "teams": {
                "count": len(self._teams),
                "spend_usd": round(sum(team.spend_usd for team in self._teams.values()), 8),
            },
            "storage": {
                "backend": "json_file" if self.storage_path else "memory",
                "path": str(self.storage_path) if self.storage_path else "",
            },
        }

    def provider_health(self) -> dict[str, object]:
        events = list(self._events)
        providers = sorted({event.provider for event in events if event.provider} | {"openai", "anthropic", "deepseek", "grok", "qwen", "kimi", "ollama"})
        data = []
        for provider in providers:
            provider_events = [event for event in events if event.provider == provider]
            latencies = sorted(event.latency_ms for event in provider_events)
            errors = sum(1 for event in provider_events if event.status_code >= 500)
            requests = len(provider_events)
            error_rate = errors / requests if requests else 0.0
            p95 = _percentile(latencies, 95)
            data.append(
                {
                    "provider": provider,
                    "status": _provider_status(requests, error_rate, p95),
                    "requests": requests,
                    "errors": errors,
                    "error_rate": round(error_rate, 4),
                    "latency_ms": {
                        "p50": round(_percentile(latencies, 50), 3),
                        "p95": round(p95, 3),
                        "p99": round(_percentile(latencies, 99), 3),
                    },
                    "tokens": sum(event.total_tokens for event in provider_events),
                    "cost_usd": round(sum(event.cost_usd for event in provider_events), 8),
                    "last_seen_at": max((event.created_at for event in provider_events), default=0.0),
                }
            )
        return {"object": "list", "data": data}

    def alerts(self) -> dict[str, object]:
        alerts: list[dict[str, object]] = []
        for item in self.provider_health()["data"]:
            if not isinstance(item, dict) or int(item.get("requests", 0)) < 5:
                continue
            error_rate = float(item.get("error_rate", 0.0))
            p95 = float(_dict(item.get("latency_ms")).get("p95", 0.0))
            if error_rate >= 0.1:
                alerts.append(
                    {
                        "severity": "critical" if error_rate >= 0.25 else "warning",
                        "type": "provider_error_rate",
                        "message": f"{item['provider']} error rate is {error_rate:.1%}",
                        "target": item["provider"],
                    }
                )
            if p95 >= 5000:
                alerts.append(
                    {
                        "severity": "warning",
                        "type": "provider_latency",
                        "message": f"{item['provider']} p95 latency is {p95:.0f} ms",
                        "target": item["provider"],
                    }
                )
        for key in self._keys.values():
            if key.budget_usd and key.spend_usd >= key.budget_usd * 0.8:
                alerts.append(
                    {
                        "severity": "critical" if key.spend_usd >= key.budget_usd else "warning",
                        "type": "key_budget",
                        "message": f"{key.preview} has used {key.spend_usd:.4f} of {key.budget_usd:.4f} USD",
                        "target": key.preview,
                    }
                )
        for user in self._users.values():
            if user.max_budget and user.spend_usd >= user.max_budget * 0.8:
                alerts.append(
                    {
                        "severity": "critical" if user.spend_usd >= user.max_budget else "warning",
                        "type": "user_budget",
                        "message": f"{user.user_id} has used {user.spend_usd:.4f} of {user.max_budget:.4f} USD",
                        "target": user.user_id,
                    }
                )
        for team in self._teams.values():
            if team.max_budget and team.spend_usd >= team.max_budget * 0.8:
                alerts.append(
                    {
                        "severity": "critical" if team.spend_usd >= team.max_budget else "warning",
                        "type": "team_budget",
                        "message": f"{team.team_id} has used {team.spend_usd:.4f} of {team.max_budget:.4f} USD",
                        "target": team.team_id,
                    }
                )
        return {"object": "list", "data": alerts}

    def export_state(self) -> dict[str, object]:
        return {
            "version": 1,
            "keys": [_virtual_key_state(key) for key in self._keys.values()],
            "users": [_user_state(user) for user in self._users.values()],
            "teams": [_team_state(team) for team in self._teams.values()],
            "events": [_event_state(event) for event in self._events],
            "audit_events": [_audit_event_state(event) for event in self._audit_events],
        }

    def _load(self) -> None:
        if not self.storage_path or not self.storage_path.exists():
            return
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        self._keys = {
            key.token_hash: key
            for key in (_virtual_key_from_state(item) for item in _list(payload.get("keys")))
            if key is not None
        }
        self._users = {
            user.user_id: user
            for user in (_user_from_state(item) for item in _list(payload.get("users")))
            if user is not None
        }
        self._teams = {
            team.team_id: team
            for team in (_team_from_state(item) for item in _list(payload.get("teams")))
            if team is not None
        }
        self._events.clear()
        for event in (_event_from_state(item) for item in _list(payload.get("events"))):
            if event is not None:
                self._events.append(event)
        self._audit_events.clear()
        for event in (_audit_event_from_state(item) for item in _list(payload.get("audit_events"))):
            if event is not None:
                self._audit_events.append(event)

    def _save(self) -> None:
        if not self.storage_path:
            return
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.storage_path.with_name(f".{self.storage_path.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps(self.export_state(), sort_keys=True), encoding="utf-8")
            temporary.replace(self.storage_path)
        except OSError:
            return

    def _audit(self, action: str, target_type: str, target_id: str, *, metadata: dict[str, object] | None = None) -> None:
        self._audit_events.append(
            AuditEvent(
                action=action,
                target_type=target_type,
                target_id=target_id,
                actor="master",
                metadata=metadata or {},
            )
        )

    def _key_for_identifier(self, value: str) -> VirtualKey | None:
        if not value:
            return None
        if value in self._keys:
            return self._keys[value]
        hashed = hash_token(value)
        if hashed in self._keys:
            return self._keys[hashed]
        for key in self._keys.values():
            if key.preview == value:
                return key
        return None

    def _enforce_rate_limits(self, key: VirtualKey, estimated_tokens: int) -> None:
        now = time.time()
        if key.rpm_limit:
            window = self._request_windows.setdefault(key.token_hash, deque())
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= key.rpm_limit:
                raise RateLimitError("virtual key RPM limit exceeded")
            window.append(now)
        if key.tpm_limit:
            token_window = self._token_windows.setdefault(key.token_hash, deque())
            while token_window and now - token_window[0][0] >= 60:
                token_window.popleft()
            used = sum(tokens for _, tokens in token_window)
            if used + estimated_tokens > key.tpm_limit:
                raise RateLimitError("virtual key TPM limit exceeded")
            token_window.append((now, estimated_tokens))


def bearer_token(headers: Mapping[str, str], key_header_name: str = "authorization") -> str:
    value = headers.get(key_header_name.lower(), "")
    if key_header_name.lower() != "authorization" and value and not value.lower().startswith("bearer "):
        return value.strip()
    if value.lower().startswith("bearer "):
        return value.split(" ", 1)[1].strip()
    return ""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def preview_token(token: str) -> str:
    return f"{token[:10]}...{token[-4:]}" if len(token) > 16 else token


def estimate_prompt_tokens(payload: Mapping[str, object]) -> int:
    messages = payload.get("messages")
    if isinstance(messages, list):
        text = " ".join(str(item.get("content", "")) for item in messages if isinstance(item, dict))
    elif "input" in payload:
        value = payload.get("input")
        if isinstance(value, list):
            text = " ".join(str(item) for item in value)
        else:
            text = str(value)
    else:
        text = str(payload.get("prompt", ""))
    return max(1, len(text) // 4)


def usage_from_body(body: bytes, fallback_prompt_tokens: int = 0) -> tuple[int, int, int]:
    try:
        payload = loads_bytes(body)
    except Exception:
        return fallback_prompt_tokens, 0, fallback_prompt_tokens
    usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
    if not isinstance(usage, dict):
        return fallback_prompt_tokens, 0, fallback_prompt_tokens
    prompt = _int_value(usage.get("prompt_tokens") or usage.get("input_tokens"), fallback_prompt_tokens)
    completion = _int_value(usage.get("completion_tokens") or usage.get("output_tokens"), 0)
    total = _int_value(usage.get("total_tokens"), prompt + completion)
    return prompt, completion, total


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    prices = MODEL_PRICES_PER_1K.get(model, MODEL_PRICES_PER_1K.get(model.split("/")[-1], (0.0, 0.0)))
    return (prompt_tokens / 1000 * prices[0]) + (completion_tokens / 1000 * prices[1])


def pricing_table() -> list[dict[str, object]]:
    return [
        {
            "model": model,
            "input_per_1k_usd": input_price,
            "output_per_1k_usd": output_price,
        }
        for model, (input_price, output_price) in sorted(MODEL_PRICES_PER_1K.items())
    ]


def _virtual_key_state(key: VirtualKey) -> dict[str, object]:
    payload = key.public()
    payload["token_hash"] = key.token_hash
    return payload


def _virtual_key_from_state(payload: object) -> VirtualKey | None:
    if not isinstance(payload, dict):
        return None
    token_hash = str(payload.get("token_hash") or "")
    if not token_hash:
        return None
    return VirtualKey(
        token_hash=token_hash,
        preview=str(payload.get("preview") or ""),
        name=str(payload.get("name") or "default"),
        user_id=str(payload.get("user_id") or ""),
        team_id=str(payload.get("team_id") or ""),
        models=_string_tuple(payload.get("models")),
        rpm_limit=_int_value(payload.get("rpm_limit"), 0),
        tpm_limit=_int_value(payload.get("tpm_limit"), 0),
        budget_usd=_float_value(payload.get("budget_usd"), 0.0),
        aliases=_string_dict(payload.get("aliases")),
        allowed_routes=_string_tuple(payload.get("allowed_routes")),
        key_type=str(payload.get("key_type") or "virtual"),
        spend_usd=_float_value(payload.get("spend_usd"), 0.0),
        created_at=_float_value(payload.get("created_at"), time.time()),
        expires_at=_float_value(payload.get("expires_at"), 0.0),
        disabled=bool(payload.get("disabled", False)),
        metadata=dict(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
    )


def _user_state(user: UserAccount) -> dict[str, object]:
    return user.public()


def _user_from_state(payload: object) -> UserAccount | None:
    if not isinstance(payload, dict):
        return None
    user_id = str(payload.get("user_id") or "")
    if not user_id:
        return None
    return UserAccount(
        user_id=user_id,
        user_email=str(payload.get("user_email") or ""),
        models=_string_tuple(payload.get("models")),
        max_budget=_float_value(payload.get("max_budget"), 0.0),
        spend_usd=_float_value(payload.get("spend_usd"), 0.0),
        rpm_limit=_int_value(payload.get("rpm_limit"), 0),
        tpm_limit=_int_value(payload.get("tpm_limit"), 0),
        created_at=_float_value(payload.get("created_at"), time.time()),
        metadata=dict(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
    )


def _team_state(team: TeamAccount) -> dict[str, object]:
    return team.public()


def _team_from_state(payload: object) -> TeamAccount | None:
    if not isinstance(payload, dict):
        return None
    team_id = str(payload.get("team_id") or "")
    if not team_id:
        return None
    return TeamAccount(
        team_id=team_id,
        team_alias=str(payload.get("team_alias") or ""),
        models=_string_tuple(payload.get("models")),
        max_budget=_float_value(payload.get("max_budget"), 0.0),
        spend_usd=_float_value(payload.get("spend_usd"), 0.0),
        created_at=_float_value(payload.get("created_at"), time.time()),
        metadata=dict(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
    )


def _event_state(event: RequestEvent) -> dict[str, object]:
    return event.public()


def _event_from_state(payload: object) -> RequestEvent | None:
    if not isinstance(payload, dict):
        return None
    return RequestEvent(
        call_id=str(payload.get("call_id") or ""),
        route=str(payload.get("route") or ""),
        provider=str(payload.get("provider") or ""),
        model=str(payload.get("model") or ""),
        status_code=_int_value(payload.get("status_code"), 0),
        latency_ms=_float_value(payload.get("latency_ms"), 0.0),
        prompt_tokens=_int_value(payload.get("prompt_tokens"), 0),
        completion_tokens=_int_value(payload.get("completion_tokens"), 0),
        total_tokens=_int_value(payload.get("total_tokens"), 0),
        cost_usd=_float_value(payload.get("cost_usd"), 0.0),
        cache=str(payload.get("cache") or ""),
        key_preview=str(payload.get("key_preview") or ""),
        user_id=str(payload.get("user_id") or ""),
        team_id=str(payload.get("team_id") or ""),
        tags=_string_tuple(payload.get("tags")),
        created_at=_float_value(payload.get("created_at"), time.time()),
    )


def _audit_event_state(event: AuditEvent) -> dict[str, object]:
    return event.public()


def _audit_event_from_state(payload: object) -> AuditEvent | None:
    if not isinstance(payload, dict):
        return None
    return AuditEvent(
        action=str(payload.get("action") or ""),
        target_type=str(payload.get("target_type") or ""),
        target_id=str(payload.get("target_id") or ""),
        actor=str(payload.get("actor") or ""),
        metadata=dict(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
        created_at=_float_value(payload.get("created_at"), time.time()),
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if str(item))


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if str(key) and str(item)}


def _int_value(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_value(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _model_allowed(model: str, models: tuple[str, ...]) -> bool:
    return "*" in models or model in models or model.split("/")[-1] in models


def _route_allowed(route: str, routes: tuple[str, ...]) -> bool:
    return "*" in routes or route in routes


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, int((pct / 100) * len(sorted_values) + 0.999999) - 1))
    return sorted_values[index]


def _rollup(target: dict[str, dict[str, object]], key: str, event: RequestEvent) -> None:
    bucket = target.setdefault(key or "unknown", {"requests": 0, "tokens": 0, "cost_usd": 0.0})
    bucket["requests"] = int(bucket["requests"]) + 1
    bucket["tokens"] = int(bucket["tokens"]) + event.total_tokens
    bucket["cost_usd"] = round(float(bucket["cost_usd"]) + event.cost_usd, 8)


def _dict(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _provider_status(requests: int, error_rate: float, p95_latency_ms: float) -> str:
    if requests == 0:
        return "unknown"
    if error_rate >= 0.25:
        return "critical"
    if error_rate >= 0.1 or p95_latency_ms >= 5000:
        return "degraded"
    return "healthy"
