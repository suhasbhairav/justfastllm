from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from justfastllm.cache import _command, _read_response
from justfastllm.dbstate import (
    database_family as _database_family,
    database_driver_dependency as _database_driver_dependency,
    database_transport_security as _database_transport_security,
    is_mongo_url as _is_mongo_url,
    load_mongo_state as _load_mongo_state,
    load_sql_state as _load_sql_state,
    safe_storage_name as _safe_storage_name,
    save_mongo_state as _save_mongo_state,
    save_sql_state as _save_sql_state,
    state_matches as _state_matches,
)
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

SUPPORTED_PRIVACY_REQUEST_TYPES = {
    "access",
    "erasure",
    "correction",
    "restriction",
    "objection",
    "portability",
    "withdrawal",
    "grievance",
    "nomination",
    "appeal",
}

SUPPORTED_PRIVACY_REQUEST_STATUSES = {
    "open",
    "in_progress",
    "completed",
    "closed",
    "denied",
    "canceled",
}

SUPPORTED_CONSENT_STATUSES = {
    "granted",
    "withdrawn",
    "revoked",
    "expired",
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
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    previous_hash: str = ""
    event_hash: str = ""

    def public(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "actor": self.actor,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "previous_hash": self.previous_hash,
            "event_hash": self.event_hash,
        }


@dataclass(slots=True)
class AccessEvent:
    request_id: str
    method: str
    path: str
    status_code: int
    latency_ms: float
    auth_context: str
    key_preview: str = ""
    user_id: str = ""
    team_id: str = ""
    created_at: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    previous_hash: str = ""
    event_hash: str = ""

    def public(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "request_id": self.request_id,
            "method": self.method,
            "path": self.path,
            "status_code": self.status_code,
            "latency_ms": round(self.latency_ms, 3),
            "auth_context": self.auth_context,
            "key_preview": self.key_preview,
            "user_id": self.user_id,
            "team_id": self.team_id,
            "created_at": self.created_at,
            "previous_hash": self.previous_hash,
            "event_hash": self.event_hash,
        }


@dataclass(slots=True)
class AuthEvent:
    auth_type: str
    outcome: str
    reason: str
    route: str = ""
    model: str = ""
    key_preview: str = ""
    user_id: str = ""
    team_id: str = ""
    created_at: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    previous_hash: str = ""
    event_hash: str = ""

    def public(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "auth_type": self.auth_type,
            "outcome": self.outcome,
            "reason": self.reason,
            "route": self.route,
            "model": self.model,
            "key_preview": self.key_preview,
            "user_id": self.user_id,
            "team_id": self.team_id,
            "created_at": self.created_at,
            "previous_hash": self.previous_hash,
            "event_hash": self.event_hash,
        }


@dataclass(slots=True)
class PrivacyRequest:
    request_id: str
    user_id: str
    request_type: str
    status: str = "open"
    source: str = ""
    notes: str = ""
    received_at: float = field(default_factory=time.time)
    due_at: float = 0.0
    completed_at: float = 0.0
    metadata: dict[str, object] = field(default_factory=dict)

    def public(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "user_id": self.user_id,
            "request_type": self.request_type,
            "status": self.status,
            "source": self.source,
            "notes": self.notes,
            "received_at": self.received_at,
            "due_at": self.due_at,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class ConsentRecord:
    consent_id: str
    user_id: str
    purpose: str
    status: str = "granted"
    lawful_basis: str = "consent"
    notice_version: str = ""
    source: str = ""
    granted_at: float = field(default_factory=time.time)
    withdrawn_at: float = 0.0
    expires_at: float = 0.0
    metadata: dict[str, object] = field(default_factory=dict)

    def public(self) -> dict[str, object]:
        return {
            "consent_id": self.consent_id,
            "user_id": self.user_id,
            "purpose": self.purpose,
            "status": self.status,
            "lawful_basis": self.lawful_basis,
            "notice_version": self.notice_version,
            "source": self.source,
            "granted_at": self.granted_at,
            "withdrawn_at": self.withdrawn_at,
            "expires_at": self.expires_at,
            "metadata": self.metadata,
        }


class InMemoryProxyControlPlane:
    def __init__(
        self,
        *,
        master_key: str = "",
        key_header_name: str = "authorization",
        storage_backend: str = "memory",
        storage_path: str | Path = "",
        storage_redis_url: str = "",
        storage_redis_key: str = "justfastllm:control-plane:state",
        storage_database_url: str = "",
        storage_database_table: str = "justfastllm_control_plane_state",
        storage_strict: bool = False,
        max_events: int = 5000,
        usage_retention_days: int = 90,
        audit_retention_days: int = 365,
    ) -> None:
        self.master_key = master_key
        self.key_header_name = key_header_name.lower()
        self.configure_storage(
            storage_backend=storage_backend,
            storage_path=storage_path,
            storage_redis_url=storage_redis_url,
            storage_redis_key=storage_redis_key,
            storage_database_url=storage_database_url,
            storage_database_table=storage_database_table,
            storage_strict=storage_strict,
        )
        self.usage_retention_days = usage_retention_days
        self.audit_retention_days = audit_retention_days
        self._keys: dict[str, VirtualKey] = {}
        self._users: dict[str, UserAccount] = {}
        self._teams: dict[str, TeamAccount] = {}
        self._events: deque[RequestEvent] = deque(maxlen=max_events)
        self._audit_events: deque[AuditEvent] = deque(maxlen=max_events)
        self._access_events: deque[AccessEvent] = deque(maxlen=max_events)
        self._auth_events: deque[AuthEvent] = deque(maxlen=max_events)
        self._privacy_requests: dict[str, PrivacyRequest] = {}
        self._consents: dict[str, ConsentRecord] = {}
        self._request_windows: dict[str, deque[float]] = {}
        self._token_windows: dict[str, deque[tuple[float, int]]] = {}
        self._load()
        self._prune_retention_and_persist()

    def enabled(self) -> bool:
        return bool(self.master_key or self._keys)

    def configure_storage(
        self,
        *,
        storage_backend: str,
        storage_path: str | Path = "",
        storage_redis_url: str = "",
        storage_redis_key: str = "justfastllm:control-plane:state",
        storage_database_url: str = "",
        storage_database_table: str = "justfastllm_control_plane_state",
        storage_strict: bool = False,
    ) -> None:
        self.storage_backend = storage_backend.lower() or ("file" if storage_path else "memory")
        self.storage_path = Path(storage_path) if storage_path else None
        self.storage_redis_url = storage_redis_url
        self.storage_redis_key = storage_redis_key
        self.storage_database_url = storage_database_url
        self.storage_database_table = _safe_storage_name(storage_database_table)
        self.storage_strict = storage_strict

    def reload_storage_state(self) -> None:
        self._load()
        self._prune_retention_and_persist()

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

    def create_privacy_request(self, payload: Mapping[str, object]) -> dict[str, object]:
        user_id = str(payload.get("user_id") or "")
        request_type = str(payload.get("request_type") or payload.get("type") or "access").lower()
        if request_type not in SUPPORTED_PRIVACY_REQUEST_TYPES:
            raise ValueError(f"unsupported privacy request type: {request_type}")
        status = str(payload.get("status") or "open").lower()
        if status not in SUPPORTED_PRIVACY_REQUEST_STATUSES:
            raise ValueError(f"unsupported privacy request status: {status}")
        received_at = _float_value(payload.get("received_at"), time.time())
        due_at = _float_value(payload.get("due_at"), received_at + 30 * 86400)
        request = PrivacyRequest(
            request_id=str(payload.get("request_id") or f"prr-{uuid.uuid4().hex[:12]}"),
            user_id=user_id,
            request_type=request_type,
            status=status,
            source=str(payload.get("source") or ""),
            notes=str(payload.get("notes") or ""),
            received_at=received_at,
            due_at=due_at,
            completed_at=_float_value(payload.get("completed_at"), 0.0),
            metadata=dict(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
        )
        self._privacy_requests[request.request_id] = request
        self._audit("create", "privacy_request", request.request_id, metadata={"user_id": user_id, "request_type": request_type})
        self._save()
        return request.public()

    def list_privacy_requests(
        self,
        *,
        user_id: str = "",
        status: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        requests = sorted(self._privacy_requests.values(), key=lambda item: item.received_at, reverse=True)
        if user_id:
            requests = [item for item in requests if item.user_id == user_id]
        if status:
            requests = [item for item in requests if item.status == status]
        return [item.public() for item in requests[: max(0, limit)]]

    def privacy_request_summary(self) -> dict[str, object]:
        now = time.time()
        by_type = {request_type: 0 for request_type in sorted(SUPPORTED_PRIVACY_REQUEST_TYPES)}
        by_status = {status: 0 for status in sorted(SUPPORTED_PRIVACY_REQUEST_STATUSES)}
        overdue_open = 0
        due_soon_7d = 0
        terminal_statuses = {"completed", "closed", "denied", "canceled"}
        for request in self._privacy_requests.values():
            by_type[request.request_type] = by_type.get(request.request_type, 0) + 1
            by_status[request.status] = by_status.get(request.status, 0) + 1
            if request.status not in terminal_statuses and request.due_at:
                if request.due_at < now:
                    overdue_open += 1
                elif request.due_at <= now + 7 * 86400:
                    due_soon_7d += 1
        return {
            "total": len(self._privacy_requests),
            "open_or_in_progress": by_status.get("open", 0) + by_status.get("in_progress", 0),
            "overdue_open": overdue_open,
            "due_soon_7d": due_soon_7d,
            "by_type": by_type,
            "by_status": by_status,
            "supported_request_types": sorted(SUPPORTED_PRIVACY_REQUEST_TYPES),
            "supported_statuses": sorted(SUPPORTED_PRIVACY_REQUEST_STATUSES),
        }

    def update_privacy_request(self, request_id: str, payload: Mapping[str, object]) -> dict[str, object] | None:
        request = self._privacy_requests.get(request_id)
        if request is None:
            return None
        if "status" in payload:
            status = str(payload.get("status") or request.status).lower()
            if status not in SUPPORTED_PRIVACY_REQUEST_STATUSES:
                raise ValueError(f"unsupported privacy request status: {status}")
            request.status = status
            if request.status in {"completed", "closed", "denied"} and not request.completed_at:
                request.completed_at = time.time()
        if "notes" in payload:
            request.notes = str(payload.get("notes") or "")
        if "due_at" in payload:
            request.due_at = _float_value(payload.get("due_at"), request.due_at)
        if "completed_at" in payload:
            request.completed_at = _float_value(payload.get("completed_at"), request.completed_at)
        if "metadata" in payload and isinstance(payload.get("metadata"), dict):
            request.metadata = dict(payload["metadata"])
        self._audit("update", "privacy_request", request.request_id, metadata={"status": request.status})
        self._save()
        return request.public()

    def create_consent(self, payload: Mapping[str, object]) -> dict[str, object]:
        user_id = str(payload.get("user_id") or "")
        purpose = str(payload.get("purpose") or payload.get("processing_purpose") or "")
        if not user_id:
            raise ValueError("user_id is required")
        if not purpose:
            raise ValueError("purpose is required")
        status = str(payload.get("status") or "granted").lower()
        if status not in SUPPORTED_CONSENT_STATUSES:
            raise ValueError(f"unsupported consent status: {status}")
        granted_at = _float_value(payload.get("granted_at"), time.time())
        consent = ConsentRecord(
            consent_id=str(payload.get("consent_id") or f"cst-{uuid.uuid4().hex[:12]}"),
            user_id=user_id,
            purpose=purpose,
            status=status,
            lawful_basis=str(payload.get("lawful_basis") or "consent"),
            notice_version=str(payload.get("notice_version") or ""),
            source=str(payload.get("source") or ""),
            granted_at=granted_at,
            withdrawn_at=_float_value(payload.get("withdrawn_at"), 0.0),
            expires_at=_float_value(payload.get("expires_at"), 0.0),
            metadata=dict(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
        )
        if consent.status in {"withdrawn", "revoked"} and not consent.withdrawn_at:
            consent.withdrawn_at = time.time()
        self._consents[consent.consent_id] = consent
        self._audit("create", "consent", consent.consent_id, metadata={"user_id": user_id, "purpose": purpose, "status": status})
        self._save()
        return consent.public()

    def list_consents(
        self,
        *,
        user_id: str = "",
        status: str = "",
        purpose: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        consents = sorted(self._consents.values(), key=lambda item: item.granted_at, reverse=True)
        if user_id:
            consents = [item for item in consents if item.user_id == user_id]
        if status:
            consents = [item for item in consents if item.status == status]
        if purpose:
            consents = [item for item in consents if item.purpose == purpose]
        return [item.public() for item in consents[: max(0, limit)]]

    def consent_summary(self) -> dict[str, object]:
        by_status = {status: 0 for status in sorted(SUPPORTED_CONSENT_STATUSES)}
        by_purpose: dict[str, int] = {}
        for consent in self._consents.values():
            by_status[consent.status] = by_status.get(consent.status, 0) + 1
            by_purpose[consent.purpose] = by_purpose.get(consent.purpose, 0) + 1
        return {
            "total": len(self._consents),
            "active": by_status.get("granted", 0),
            "withdrawn_or_revoked": by_status.get("withdrawn", 0) + by_status.get("revoked", 0),
            "by_status": by_status,
            "by_purpose": by_purpose,
            "supported_statuses": sorted(SUPPORTED_CONSENT_STATUSES),
        }

    def update_consent(self, consent_id: str, payload: Mapping[str, object]) -> dict[str, object] | None:
        consent = self._consents.get(consent_id)
        if consent is None:
            return None
        if "status" in payload:
            status = str(payload.get("status") or consent.status).lower()
            if status not in SUPPORTED_CONSENT_STATUSES:
                raise ValueError(f"unsupported consent status: {status}")
            consent.status = status
            if status in {"withdrawn", "revoked"} and not consent.withdrawn_at:
                consent.withdrawn_at = time.time()
        if "purpose" in payload or "processing_purpose" in payload:
            consent.purpose = str(payload.get("purpose") or payload.get("processing_purpose") or consent.purpose)
        if "lawful_basis" in payload:
            consent.lawful_basis = str(payload.get("lawful_basis") or consent.lawful_basis)
        if "notice_version" in payload:
            consent.notice_version = str(payload.get("notice_version") or "")
        if "source" in payload:
            consent.source = str(payload.get("source") or "")
        if "withdrawn_at" in payload:
            consent.withdrawn_at = _float_value(payload.get("withdrawn_at"), consent.withdrawn_at)
        if "expires_at" in payload:
            consent.expires_at = _float_value(payload.get("expires_at"), consent.expires_at)
        if "metadata" in payload and isinstance(payload.get("metadata"), dict):
            consent.metadata = dict(payload["metadata"])
        self._audit("update", "consent", consent.consent_id, metadata={"status": consent.status, "purpose": consent.purpose})
        self._save()
        return consent.public()

    def withdraw_consent(self, consent_id: str, payload: Mapping[str, object] | None = None) -> dict[str, object] | None:
        update_payload = dict(payload or {})
        update_payload["status"] = str(update_payload.get("status") or "withdrawn")
        update_payload.setdefault("withdrawn_at", time.time())
        return self.update_consent(consent_id, update_payload)

    def export_user_subject(self, user_id: str) -> dict[str, object]:
        key_previews = {key.preview for key in self._keys.values() if key.user_id == user_id}
        return {
            "user_id": user_id,
            "user": self.user_info(user_id),
            "keys": [key.public() for key in self._keys.values() if key.user_id == user_id],
            "usage_events": [
                event.public()
                for event in self._events
                if event.user_id == user_id or event.key_preview in key_previews
            ],
            "audit_events": [
                event.public()
                for event in self._audit_events
                if (
                    event.target_id == user_id
                    or event.target_id in key_previews
                    or _audit_actor_matches_subject(event.actor, user_id, key_previews)
                )
            ],
            "access_events": [
                event.public()
                for event in self._access_events
                if event.user_id == user_id or event.key_preview in key_previews
            ],
            "auth_events": [
                event.public()
                for event in self._auth_events
                if event.user_id == user_id or event.key_preview in key_previews
            ],
            "privacy_requests": [
                request.public()
                for request in self._privacy_requests.values()
                if request.user_id == user_id
            ],
            "consents": [
                consent.public()
                for consent in self._consents.values()
                if consent.user_id == user_id
            ],
        }

    def erase_user_subject(self, user_id: str) -> dict[str, object]:
        key_hashes = [token_hash for token_hash, key in self._keys.items() if key.user_id == user_id]
        key_previews = {self._keys[token_hash].preview for token_hash in key_hashes}
        user_deleted = self._users.pop(user_id, None) is not None
        for token_hash in key_hashes:
            self._keys.pop(token_hash, None)

        before_events = len(self._events)
        self._events = deque(
            (
                event
                for event in self._events
                if event.user_id != user_id and event.key_preview not in key_previews
            ),
            maxlen=self._events.maxlen,
        )
        removed_events = before_events - len(self._events)

        pseudonymized_audit = 0
        for event in self._audit_events:
            audit_match = (
                event.target_id == user_id
                or event.target_id in key_previews
                or _audit_actor_matches_subject(event.actor, user_id, key_previews)
            )
            if audit_match:
                event.target_id = "[erased]"
                if _audit_actor_matches_subject(event.actor, user_id, key_previews):
                    event.actor = "[erased]"
                event.metadata = {"erased": True}
                pseudonymized_audit += 1
        pseudonymized_access = 0
        for event in self._access_events:
            if event.user_id == user_id or event.key_preview in key_previews:
                event.key_preview = "[erased]"
                event.user_id = "[erased]"
                event.team_id = ""
                pseudonymized_access += 1
        pseudonymized_auth = 0
        for event in self._auth_events:
            if event.user_id == user_id or event.key_preview in key_previews:
                event.key_preview = "[erased]"
                event.user_id = "[erased]"
                event.team_id = ""
                pseudonymized_auth += 1
        pseudonymized_privacy_requests = 0
        for request in self._privacy_requests.values():
            if request.user_id == user_id:
                request.user_id = "[erased]"
                request.metadata = {"erased": True}
                pseudonymized_privacy_requests += 1
        pseudonymized_consents = 0
        for consent in self._consents.values():
            if consent.user_id == user_id:
                consent.user_id = "[erased]"
                consent.metadata = {"erased": True}
                pseudonymized_consents += 1
        self._audit(
            "erase",
            "user_subject",
            "[erased]",
            metadata={
                "user_deleted": user_deleted,
                "keys_deleted": len(key_hashes),
                "usage_events_deleted": removed_events,
                "audit_events_pseudonymized": pseudonymized_audit,
                "access_events_pseudonymized": pseudonymized_access,
                "auth_events_pseudonymized": pseudonymized_auth,
                "privacy_requests_pseudonymized": pseudonymized_privacy_requests,
                "consents_pseudonymized": pseudonymized_consents,
            },
        )
        self._rebuild_evidence_chains()
        self._save()
        return {
            "user_id": user_id,
            "user_deleted": user_deleted,
            "keys_deleted": len(key_hashes),
            "usage_events_deleted": removed_events,
            "audit_events_pseudonymized": pseudonymized_audit,
            "access_events_pseudonymized": pseudonymized_access,
            "auth_events_pseudonymized": pseudonymized_auth,
            "privacy_requests_pseudonymized": pseudonymized_privacy_requests,
            "consents_pseudonymized": pseudonymized_consents,
        }

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
        subject_user_id = str(headers.get("x-user-id", ""))
        subject_team_id = str(headers.get("x-team-id", ""))

        def record_auth(auth_type: str, outcome: str, reason: str, key: VirtualKey | None = None) -> None:
            self._record_auth_event(
                auth_type=auth_type,
                outcome=outcome,
                reason=reason,
                route=route,
                model=model,
                key=key,
                subject_user_id=subject_user_id,
                subject_team_id=subject_team_id,
            )

        if require_admin:
            if self.is_admin(headers):
                record_auth("admin", "success", "master_key")
                return None
            record_auth("admin", "failure", "missing_or_invalid_master_key")
            raise UnauthorizedError("proxy admin key is required")

        if not self.enabled():
            record_auth("none", "bypass", "auth_not_configured")
            return None
        token = bearer_token(headers, self.key_header_name)
        if self.master_key and token == self.master_key:
            record_auth("admin", "success", "master_key")
            return None
        if not token:
            record_auth("virtual_key", "failure", "missing_bearer_token")
            raise UnauthorizedError("Bearer token is required")
        key = self._keys.get(hash_token(token))
        if key is None:
            record_auth("virtual_key", "failure", "invalid_virtual_key")
            raise UnauthorizedError("invalid or disabled virtual key")
        if key.disabled:
            record_auth("virtual_key", "failure", "disabled_virtual_key", key)
            raise UnauthorizedError("invalid or disabled virtual key")
        if key.expires_at and time.time() > key.expires_at:
            record_auth("virtual_key", "failure", "expired_virtual_key", key)
            raise UnauthorizedError("virtual key has expired")
        if key.allowed_routes and route and not _route_allowed(route, key.allowed_routes):
            record_auth("virtual_key", "failure", "route_forbidden", key)
            raise ForbiddenError(f"virtual key cannot access route '{route}'")
        access_model = key.aliases.get(model, model)
        if key.models and model and not _model_allowed(access_model, key.models):
            record_auth("virtual_key", "failure", "model_forbidden", key)
            raise ForbiddenError(f"virtual key cannot access model '{model}'")
        try:
            self._enforce_rate_limits(key, estimated_tokens)
        except RateLimitError as exc:
            record_auth("virtual_key", "failure", exc.code, key)
            raise
        if key.budget_usd and key.spend_usd >= key.budget_usd:
            record_auth("virtual_key", "failure", "key_budget_exhausted", key)
            raise BudgetExceededError("virtual key budget is exhausted")
        user = self._users.get(key.user_id)
        if user and user.max_budget and user.spend_usd >= user.max_budget:
            record_auth("virtual_key", "failure", "user_budget_exhausted", key)
            raise BudgetExceededError("user budget is exhausted")
        team = self._teams.get(key.team_id)
        if team and team.max_budget and team.spend_usd >= team.max_budget:
            record_auth("virtual_key", "failure", "team_budget_exhausted", key)
            raise BudgetExceededError("team budget is exhausted")
        record_auth("virtual_key", "success", "virtual_key", key)
        return key

    def record_event(self, event: RequestEvent, key: VirtualKey | None = None) -> None:
        self._events.append(event)
        if key is not None:
            key.spend_usd += event.cost_usd
            if key.user_id in self._users:
                self._users[key.user_id].spend_usd += event.cost_usd
            if key.team_id in self._teams:
                self._teams[key.team_id].spend_usd += event.cost_usd
        self.prune_retention()
        self._save()

    def apply_aliases(self, key: VirtualKey, payload: Mapping[str, object]) -> dict[str, object]:
        routed = dict(payload)
        model = routed.get("model")
        if isinstance(model, str) and model in key.aliases:
            routed["model"] = key.aliases[model]
        return routed

    def events(self, limit: int = 100) -> list[dict[str, object]]:
        self._prune_retention_and_persist()
        return [event.public() for event in list(self._events)[-limit:]][::-1]

    def audit_events(self, limit: int = 100) -> list[dict[str, object]]:
        self._prune_retention_and_persist()
        return [event.public() for event in list(self._audit_events)[-limit:]][::-1]

    def record_access_event(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        status_code: int,
        latency_ms: float,
        auth_context: str,
        key_preview: str = "",
        user_id: str = "",
        team_id: str = "",
    ) -> None:
        self._append_access_event(
            AccessEvent(
                request_id=request_id,
                method=method,
                path=path,
                status_code=status_code,
                latency_ms=latency_ms,
                auth_context=auth_context,
                key_preview=key_preview,
                user_id=user_id,
                team_id=team_id,
            )
        )
        self.prune_retention()
        self._save()

    def subject_from_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        token = bearer_token(headers, self.key_header_name)
        if token and self.master_key and token == self.master_key:
            return {
                "key_preview": "master",
                "user_id": str(headers.get("x-user-id", "")),
                "team_id": str(headers.get("x-team-id", "")),
            }
        key = self._keys.get(hash_token(token)) if token else None
        if key is not None:
            return {"key_preview": key.preview, "user_id": key.user_id, "team_id": key.team_id}
        return {
            "key_preview": "",
            "user_id": str(headers.get("x-user-id", "")),
            "team_id": str(headers.get("x-team-id", "")),
        }

    def access_events(self, limit: int = 100) -> list[dict[str, object]]:
        self._prune_retention_and_persist()
        return [event.public() for event in list(self._access_events)[-limit:]][::-1]

    def auth_events(self, limit: int = 100) -> list[dict[str, object]]:
        self._prune_retention_and_persist()
        return [event.public() for event in list(self._auth_events)[-limit:]][::-1]

    def metrics(self) -> dict[str, object]:
        self._prune_retention_and_persist()
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
                "backend": self.storage_backend,
                "path": str(self.storage_path) if self.storage_path else "",
                "redis_key": self.storage_redis_key if self.storage_backend == "redis" else "",
                "database_table": self.storage_database_table if self.storage_backend == "database" else "",
            },
        }

    def provider_health(self) -> dict[str, object]:
        self._prune_retention_and_persist()
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
        self.prune_retention()
        return {
            "version": 1,
            "keys": [_virtual_key_state(key) for key in self._keys.values()],
            "users": [_user_state(user) for user in self._users.values()],
            "teams": [_team_state(team) for team in self._teams.values()],
            "events": [_event_state(event) for event in self._events],
            "audit_events": [_audit_event_state(event) for event in self._audit_events],
            "access_events": [_access_event_state(event) for event in self._access_events],
            "auth_events": [_auth_event_state(event) for event in self._auth_events],
            "privacy_requests": [_privacy_request_state(request) for request in self._privacy_requests.values()],
            "consents": [_consent_state(consent) for consent in self._consents.values()],
        }

    def evidence_integrity(self) -> dict[str, object]:
        self._prune_retention_and_persist()
        categories: dict[str, object] = {
            "keys": [key.public() for key in self._keys.values()],
            "users": [user.public() for user in self._users.values()],
            "teams": [team.public() for team in self._teams.values()],
            "usage_events": [event.public() for event in self._events],
            "audit_events": [event.public() for event in self._audit_events],
            "access_events": [event.public() for event in self._access_events],
            "auth_events": [event.public() for event in self._auth_events],
            "privacy_requests": [request.public() for request in self._privacy_requests.values()],
            "consents": [consent.public() for consent in self._consents.values()],
        }
        category_digests = {
            name: {
                "count": len(value) if isinstance(value, list) else 0,
                "sha256": _state_digest(value),
            }
            for name, value in categories.items()
        }
        category_digests["audit_events"]["hash_chain"] = _chain_integrity(list(self._audit_events))
        category_digests["access_events"]["hash_chain"] = _chain_integrity(list(self._access_events))
        category_digests["auth_events"]["hash_chain"] = _chain_integrity(list(self._auth_events))
        return {
            "object": "evidence_integrity",
            "generated_at": int(time.time()),
            "storage": {
                "backend": self.storage_backend,
                "database_table": self.storage_database_table if self.storage_backend == "database" else "",
                "database_family": _database_family(self.storage_database_url) if self.storage_backend == "database" else "",
                "redis_key": self.storage_redis_key if self.storage_backend == "redis" else "",
                "path": str(self.storage_path) if self.storage_path else "",
            },
            "categories": category_digests,
            "aggregate_sha256": _state_digest(categories),
        }

    def retention_status(self) -> dict[str, object]:
        self._prune_retention_and_persist()
        return {
            "usage_retention_days": self.usage_retention_days,
            "audit_retention_days": self.audit_retention_days,
            "access_retention_days": self.audit_retention_days,
            "auth_retention_days": self.audit_retention_days,
            "retention_policy": {
                "usage_events": "usage_retention_days",
                "audit_events": "audit_retention_days",
                "access_events": "audit_retention_days",
                "auth_events": "audit_retention_days",
            },
            "usage_events": len(self._events),
            "audit_events": len(self._audit_events),
            "access_events": len(self._access_events),
            "auth_events": len(self._auth_events),
            "storage_backend": self.storage_backend,
        }

    def durable_storage_enabled(self) -> bool:
        if self.storage_backend == "redis":
            return bool(self.storage_redis_url and self.storage_redis_key)
        if self.storage_backend == "file":
            return self.storage_path is not None
        if self.storage_backend == "database":
            return bool(self.storage_database_url and self.storage_database_table)
        return False

    def storage_health(self) -> dict[str, object]:
        database_family = _database_family(self.storage_database_url) if self.storage_backend == "database" else ""
        driver = _database_driver_dependency(self.storage_database_url) if self.storage_backend == "database" else ""
        transport = _database_transport_security(self.storage_database_url) if self.storage_backend == "database" else {}
        if not self.durable_storage_enabled():
            return {
                "ok": False,
                "backend": self.storage_backend,
                "database_family": database_family,
                "driver_dependency": driver,
                "transport_security": transport,
                "error": "durable control-plane storage is not configured",
            }
        try:
            self._verify_storage_write()
        except Exception as exc:
            return {
                "ok": False,
                "backend": self.storage_backend,
                "database_family": database_family,
                "driver_dependency": driver,
                "transport_security": transport,
                "error": _safe_error(exc),
            }
        result: dict[str, object] = {"ok": True, "backend": self.storage_backend}
        if self.storage_backend == "database":
            result["database_family"] = database_family
            result["driver_dependency"] = driver
            result["transport_security"] = transport
            result["table"] = self.storage_database_table
        return result

    def prune_retention(self) -> dict[str, int]:
        now = time.time()
        usage_before = len(self._events)
        audit_before = len(self._audit_events)
        access_before = len(self._access_events)
        auth_before = len(self._auth_events)
        if self.usage_retention_days > 0:
            usage_cutoff = now - (self.usage_retention_days * 86400)
            self._events = deque(
                (event for event in self._events if event.created_at >= usage_cutoff),
                maxlen=self._events.maxlen,
            )
        if self.audit_retention_days > 0:
            audit_cutoff = now - (self.audit_retention_days * 86400)
            self._audit_events = deque(
                (event for event in self._audit_events if event.created_at >= audit_cutoff),
                maxlen=self._audit_events.maxlen,
            )
            self._access_events = deque(
                (event for event in self._access_events if event.created_at >= audit_cutoff),
                maxlen=self._access_events.maxlen,
            )
            self._auth_events = deque(
                (event for event in self._auth_events if event.created_at >= audit_cutoff),
                maxlen=self._auth_events.maxlen,
            )
        return {
            "usage_events_pruned": usage_before - len(self._events),
            "audit_events_pruned": audit_before - len(self._audit_events),
            "access_events_pruned": access_before - len(self._access_events),
            "auth_events_pruned": auth_before - len(self._auth_events),
        }

    def _prune_retention_and_persist(self) -> dict[str, int]:
        pruned = self.prune_retention()
        if any(pruned.values()) and self.durable_storage_enabled():
            self._save()
        return pruned

    def _load(self) -> None:
        if self.storage_backend == "redis":
            payload = self._load_redis_state()
        elif self.storage_backend == "database":
            payload = self._load_database_state()
        else:
            payload = self._load_file_state()
        if payload is None:
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
        self._access_events.clear()
        for event in (_access_event_from_state(item) for item in _list(payload.get("access_events"))):
            if event is not None:
                self._access_events.append(event)
        self._auth_events.clear()
        for event in (_auth_event_from_state(item) for item in _list(payload.get("auth_events"))):
            if event is not None:
                self._auth_events.append(event)
        self._privacy_requests = {
            request.request_id: request
            for request in (_privacy_request_from_state(item) for item in _list(payload.get("privacy_requests")))
            if request is not None
        }
        self._consents = {
            consent.consent_id: consent
            for consent in (_consent_from_state(item) for item in _list(payload.get("consents")))
            if consent is not None
        }
        self._rebuild_missing_evidence_chains()

    def _save(self) -> None:
        if self.storage_backend == "redis":
            self._save_redis_state()
            return
        if self.storage_backend == "database":
            self._save_database_state()
            return
        if self.storage_backend == "file":
            self._save_file_state()

    def _load_file_state(self) -> dict[str, object] | None:
        if not self.storage_path or not self.storage_path.exists():
            return
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _save_file_state(self) -> None:
        if not self.storage_path:
            return
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.storage_path.with_name(f".{self.storage_path.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps(self.export_state(), sort_keys=True), encoding="utf-8")
            temporary.replace(self.storage_path)
        except OSError:
            return

    def _load_redis_state(self) -> dict[str, object] | None:
        if not self.storage_redis_url or not self.storage_redis_key:
            return None
        try:
            value = _redis_execute(self.storage_redis_url, "GET", self.storage_redis_key)
            if not isinstance(value, bytes) or not value:
                return None
            payload = json.loads(value.decode("utf-8"))
            return payload if isinstance(payload, dict) else None
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _save_redis_state(self) -> None:
        if not self.storage_redis_url or not self.storage_redis_key:
            return
        try:
            _redis_execute(
                self.storage_redis_url,
                "SET",
                self.storage_redis_key,
                json.dumps(self.export_state(), sort_keys=True).encode("utf-8"),
            )
        except OSError:
            return

    def _load_database_state(self) -> dict[str, object] | None:
        if not self.storage_database_url or not self.storage_database_table:
            return None
        try:
            if _is_mongo_url(self.storage_database_url):
                return _load_mongo_state(self.storage_database_url, self.storage_database_table)
            return _load_sql_state(self.storage_database_url, self.storage_database_table)
        except Exception as exc:
            if self.storage_strict:
                raise OSError("database control-plane load failed") from exc
            return None

    def _save_database_state(self) -> None:
        if not self.storage_database_url or not self.storage_database_table:
            return
        try:
            state = self.export_state()
            if _is_mongo_url(self.storage_database_url):
                _save_mongo_state(self.storage_database_url, self.storage_database_table, state)
            else:
                _save_sql_state(self.storage_database_url, self.storage_database_table, state)
        except Exception as exc:
            if self.storage_strict:
                raise OSError("database control-plane save failed") from exc
            return

    def _verify_storage_write(self) -> None:
        state = self.export_state()
        if self.storage_backend == "file":
            if not self.storage_path:
                raise OSError("storage path is not configured")
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.storage_path.with_name(f".{self.storage_path.name}.{os.getpid()}.health.tmp")
            temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
            temporary.replace(self.storage_path)
            loaded = json.loads(self.storage_path.read_text(encoding="utf-8"))
            if not _state_matches(state, loaded):
                raise OSError("file control-plane readback mismatch")
            return
        if self.storage_backend == "redis":
            if not self.storage_redis_url or not self.storage_redis_key:
                raise OSError("redis control-plane storage is not configured")
            _redis_execute(
                self.storage_redis_url,
                "SET",
                self.storage_redis_key,
                json.dumps(state, sort_keys=True).encode("utf-8"),
            )
            value = _redis_execute(self.storage_redis_url, "GET", self.storage_redis_key)
            if not isinstance(value, bytes) or not _state_matches(state, json.loads(value.decode("utf-8"))):
                raise OSError("redis control-plane readback mismatch")
            return
        if self.storage_backend == "database":
            if not self.storage_database_url or not self.storage_database_table:
                raise OSError("database control-plane storage is not configured")
            if _is_mongo_url(self.storage_database_url):
                _save_mongo_state(self.storage_database_url, self.storage_database_table, state)
                loaded = _load_mongo_state(self.storage_database_url, self.storage_database_table)
            else:
                _save_sql_state(self.storage_database_url, self.storage_database_table, state)
                loaded = _load_sql_state(self.storage_database_url, self.storage_database_table)
            if not _state_matches(state, loaded):
                raise OSError("database control-plane readback mismatch")
            return
        raise OSError("durable control-plane storage is not configured")

    def _audit(
        self,
        action: str,
        target_type: str,
        target_id: str,
        *,
        actor: str = "master",
        metadata: dict[str, object] | None = None,
    ) -> None:
        self._append_audit_event(
            AuditEvent(
                action=action,
                target_type=target_type,
                target_id=target_id,
                actor=actor,
                metadata=metadata or {},
            )
        )

    def _append_audit_event(self, event: AuditEvent) -> None:
        _seal_chained_event(event, self._audit_events[-1].event_hash if self._audit_events else "")
        self._audit_events.append(event)

    def _append_access_event(self, event: AccessEvent) -> None:
        _seal_chained_event(event, self._access_events[-1].event_hash if self._access_events else "")
        self._access_events.append(event)

    def _append_auth_event(self, event: AuthEvent) -> None:
        _seal_chained_event(event, self._auth_events[-1].event_hash if self._auth_events else "")
        self._auth_events.append(event)

    def _rebuild_evidence_chains(self) -> None:
        _rebuild_chain(self._audit_events)
        _rebuild_chain(self._access_events)
        _rebuild_chain(self._auth_events)

    def _rebuild_missing_evidence_chains(self) -> None:
        if _chain_has_missing_hashes(self._audit_events):
            _rebuild_chain(self._audit_events)
        if _chain_has_missing_hashes(self._access_events):
            _rebuild_chain(self._access_events)
        if _chain_has_missing_hashes(self._auth_events):
            _rebuild_chain(self._auth_events)

    def record_audit_event(
        self,
        action: str,
        target_type: str,
        target_id: str,
        *,
        actor: str = "master",
        metadata: dict[str, object] | None = None,
    ) -> None:
        self._audit(action, target_type, target_id, actor=actor, metadata=metadata)
        self.prune_retention()
        self._save()

    def _record_auth_event(
        self,
        *,
        auth_type: str,
        outcome: str,
        reason: str,
        route: str = "",
        model: str = "",
        key: VirtualKey | None = None,
        subject_user_id: str = "",
        subject_team_id: str = "",
    ) -> None:
        self._append_auth_event(
            AuthEvent(
                auth_type=auth_type,
                outcome=outcome,
                reason=reason,
                route=route,
                model=model,
                key_preview=key.preview if key else "",
                user_id=key.user_id if key else subject_user_id,
                team_id=key.team_id if key else subject_team_id,
            )
        )
        self.prune_retention()
        self._save()

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


def _state_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _seal_chained_event(event: AuditEvent | AccessEvent | AuthEvent, previous_hash: str) -> None:
    event.previous_hash = previous_hash
    event.event_hash = _event_hash(event)


def _event_hash(event: AuditEvent | AccessEvent | AuthEvent) -> str:
    payload = event.public()
    payload.pop("event_hash", None)
    return _state_digest(payload)


def _rebuild_chain(events: deque[AuditEvent] | deque[AccessEvent] | deque[AuthEvent]) -> None:
    previous_hash = events[0].previous_hash if events else ""
    for event in events:
        _seal_chained_event(event, previous_hash)
        previous_hash = event.event_hash


def _chain_has_missing_hashes(events: deque[AuditEvent] | deque[AccessEvent] | deque[AuthEvent]) -> bool:
    return any(not event.event_id or not event.event_hash for event in events)


def _chain_integrity(events: list[AuditEvent] | list[AccessEvent] | list[AuthEvent]) -> dict[str, object]:
    previous_hash = events[0].previous_hash if events else ""
    latest_hash = previous_hash
    for index, event in enumerate(events):
        expected_hash = _event_hash(event)
        if event.previous_hash != previous_hash or event.event_hash != expected_hash:
            return {
                "valid": False,
                "count": len(events),
                "broken_index": index,
                "latest_hash": latest_hash,
                "anchored_previous_hash": events[0].previous_hash if events else "",
            }
        latest_hash = event.event_hash
        previous_hash = event.event_hash
    return {
        "valid": True,
        "count": len(events),
        "broken_index": -1,
        "latest_hash": latest_hash,
        "anchored_previous_hash": events[0].previous_hash if events else "",
    }


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    for marker in ("@", "://", "/"):
        if marker in message:
            return exc.__class__.__name__
    return message[:160]


def preview_token(token: str) -> str:
    return f"{token[:10]}...{token[-4:]}" if len(token) > 16 else token


def _redis_execute(url: str, *parts: str | bytes) -> object:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    password = parsed.password
    db = int((parsed.path or "/0").lstrip("/") or "0")
    with socket.create_connection((host, port), timeout=1.0) as conn:
        if password:
            conn.sendall(_command("AUTH", password))
            _read_response(conn)
        if db:
            conn.sendall(_command("SELECT", str(db)))
            _read_response(conn)
        conn.sendall(_command(*parts))
        return _read_response(conn)


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
        event_id=str(payload.get("event_id") or uuid.uuid4().hex),
        previous_hash=str(payload.get("previous_hash") or ""),
        event_hash=str(payload.get("event_hash") or ""),
    )


def _access_event_state(event: AccessEvent) -> dict[str, object]:
    return event.public()


def _access_event_from_state(payload: object) -> AccessEvent | None:
    if not isinstance(payload, dict):
        return None
    return AccessEvent(
        request_id=str(payload.get("request_id") or ""),
        method=str(payload.get("method") or ""),
        path=str(payload.get("path") or ""),
        status_code=_int_value(payload.get("status_code"), 0),
        latency_ms=_float_value(payload.get("latency_ms"), 0.0),
        auth_context=str(payload.get("auth_context") or ""),
        key_preview=str(payload.get("key_preview") or ""),
        user_id=str(payload.get("user_id") or ""),
        team_id=str(payload.get("team_id") or ""),
        created_at=_float_value(payload.get("created_at"), time.time()),
        event_id=str(payload.get("event_id") or uuid.uuid4().hex),
        previous_hash=str(payload.get("previous_hash") or ""),
        event_hash=str(payload.get("event_hash") or ""),
    )


def _auth_event_state(event: AuthEvent) -> dict[str, object]:
    return event.public()


def _auth_event_from_state(payload: object) -> AuthEvent | None:
    if not isinstance(payload, dict):
        return None
    return AuthEvent(
        auth_type=str(payload.get("auth_type") or ""),
        outcome=str(payload.get("outcome") or ""),
        reason=str(payload.get("reason") or ""),
        route=str(payload.get("route") or ""),
        model=str(payload.get("model") or ""),
        key_preview=str(payload.get("key_preview") or ""),
        user_id=str(payload.get("user_id") or ""),
        team_id=str(payload.get("team_id") or ""),
        created_at=_float_value(payload.get("created_at"), time.time()),
        event_id=str(payload.get("event_id") or uuid.uuid4().hex),
        previous_hash=str(payload.get("previous_hash") or ""),
        event_hash=str(payload.get("event_hash") or ""),
    )


def _privacy_request_state(request: PrivacyRequest) -> dict[str, object]:
    return request.public()


def _privacy_request_from_state(payload: object) -> PrivacyRequest | None:
    if not isinstance(payload, dict):
        return None
    request_id = str(payload.get("request_id") or "")
    if not request_id:
        return None
    return PrivacyRequest(
        request_id=request_id,
        user_id=str(payload.get("user_id") or ""),
        request_type=str(payload.get("request_type") or "access"),
        status=str(payload.get("status") or "open"),
        source=str(payload.get("source") or ""),
        notes=str(payload.get("notes") or ""),
        received_at=_float_value(payload.get("received_at"), time.time()),
        due_at=_float_value(payload.get("due_at"), 0.0),
        completed_at=_float_value(payload.get("completed_at"), 0.0),
        metadata=dict(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
    )


def _consent_state(consent: ConsentRecord) -> dict[str, object]:
    return consent.public()


def _consent_from_state(payload: object) -> ConsentRecord | None:
    if not isinstance(payload, dict):
        return None
    consent_id = str(payload.get("consent_id") or "")
    if not consent_id:
        return None
    return ConsentRecord(
        consent_id=consent_id,
        user_id=str(payload.get("user_id") or ""),
        purpose=str(payload.get("purpose") or ""),
        status=str(payload.get("status") or "granted"),
        lawful_basis=str(payload.get("lawful_basis") or "consent"),
        notice_version=str(payload.get("notice_version") or ""),
        source=str(payload.get("source") or ""),
        granted_at=_float_value(payload.get("granted_at"), time.time()),
        withdrawn_at=_float_value(payload.get("withdrawn_at"), 0.0),
        expires_at=_float_value(payload.get("expires_at"), 0.0),
        metadata=dict(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
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


def _audit_actor_matches_subject(actor: str, user_id: str, key_previews: set[str]) -> bool:
    if actor == f"user:{user_id}":
        return True
    if actor.startswith("virtual_key:"):
        return actor.removeprefix("virtual_key:") in key_previews
    return False


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
