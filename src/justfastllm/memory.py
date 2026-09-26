from __future__ import annotations

import socket
import time
from typing import Any, Protocol
from urllib.parse import quote, urlparse

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
from justfastllm.jsonutil import dumps_bytes, loads_bytes


class UserMemoryStore(Protocol):
    enabled: bool

    def get_preferences(self, user_id: str) -> dict[str, object]:
        ...

    def save_preferences(self, user_id: str, preferences: dict[str, object]) -> dict[str, object]:
        ...

    def add_feedback(self, user_id: str, feedback: dict[str, object]) -> dict[str, object]:
        ...

    def get_feedback(self, user_id: str) -> list[dict[str, object]]:
        ...

    def delete_user(self, user_id: str) -> dict[str, object]:
        ...

    def storage_health(self) -> dict[str, object]:
        ...


class MemoryUserMemoryStore:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self._preferences: dict[str, dict[str, object]] = {}
        self._feedback: dict[str, list[dict[str, object]]] = {}

    def get_preferences(self, user_id: str) -> dict[str, object]:
        if not self.enabled:
            return {}
        return dict(self._preferences.get(user_id, {}))

    def save_preferences(self, user_id: str, preferences: dict[str, object]) -> dict[str, object]:
        if not self.enabled:
            return {}
        current = dict(self._preferences.get(user_id, {}))
        current.update(preferences)
        self._preferences[user_id] = current
        return dict(current)

    def add_feedback(self, user_id: str, feedback: dict[str, object]) -> dict[str, object]:
        if not self.enabled:
            return {}
        entry = {"created_at": int(time.time()), **feedback}
        self._feedback.setdefault(user_id, []).append(entry)
        return dict(entry)

    def get_feedback(self, user_id: str) -> list[dict[str, object]]:
        if not self.enabled:
            return []
        return [dict(item) for item in self._feedback.get(user_id, [])]

    def delete_user(self, user_id: str) -> dict[str, object]:
        preferences = self._preferences.pop(user_id, None) is not None
        feedback = self._feedback.pop(user_id, None) is not None
        return {"preferences_deleted": preferences, "feedback_deleted": feedback}

    def storage_health(self) -> dict[str, object]:
        if not self.enabled:
            return {"ok": True, "backend": "memory", "disabled": True}
        return {"ok": False, "backend": "memory", "error": "memory user store is not durable"}


class RedisUserMemoryStore:
    def __init__(
        self,
        *,
        url: str,
        key_prefix: str,
        enabled: bool = True,
        timeout_seconds: float = 1.0,
    ) -> None:
        parsed = urlparse(url)
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port or 6379
        self.password = parsed.password
        self.db = int((parsed.path or "/0").lstrip("/") or "0")
        self.key_prefix = key_prefix
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds

    def get_preferences(self, user_id: str) -> dict[str, object]:
        if not self.enabled:
            return {}
        return _as_dict(self._get_json(self._key("preferences", user_id)))

    def save_preferences(self, user_id: str, preferences: dict[str, object]) -> dict[str, object]:
        if not self.enabled:
            return {}
        current = self.get_preferences(user_id)
        current.update(preferences)
        self._set_json(self._key("preferences", user_id), current)
        return current

    def add_feedback(self, user_id: str, feedback: dict[str, object]) -> dict[str, object]:
        if not self.enabled:
            return {}
        entry = {"created_at": int(time.time()), **feedback}
        current = self.get_feedback(user_id)
        current.append(entry)
        self._set_json(self._key("feedback", user_id), current)
        return entry

    def get_feedback(self, user_id: str) -> list[dict[str, object]]:
        if not self.enabled:
            return []
        value = self._get_json(self._key("feedback", user_id))
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def delete_user(self, user_id: str) -> dict[str, object]:
        if not self.enabled:
            return {"preferences_deleted": False, "feedback_deleted": False}
        preferences_deleted = self._delete(self._key("preferences", user_id))
        feedback_deleted = self._delete(self._key("feedback", user_id))
        return {"preferences_deleted": preferences_deleted, "feedback_deleted": feedback_deleted}

    def storage_health(self) -> dict[str, object]:
        if not self.enabled:
            return {"ok": True, "backend": "redis", "disabled": True}
        key = f"{self.key_prefix}health:{int(time.time() * 1000)}"
        try:
            self._execute("SET", key, dumps_bytes({"ok": True}))
            value = self._execute("GET", key)
            self._execute("DEL", key)
        except Exception as exc:
            return {"ok": False, "backend": "redis", "error": _safe_error(exc)}
        return {"ok": isinstance(value, bytes), "backend": "redis", "error": "" if isinstance(value, bytes) else "redis health value missing"}

    def _key(self, namespace: str, user_id: str) -> str:
        return f"{self.key_prefix}{namespace}:{quote(user_id, safe='')}"

    def _get_json(self, key: str) -> object:
        try:
            value = self._execute("GET", key)
        except OSError:
            return None
        if not isinstance(value, bytes):
            return None
        return loads_bytes(value)

    def _set_json(self, key: str, value: object) -> None:
        try:
            self._execute("SET", key, dumps_bytes(value))
        except OSError:
            return

    def _delete(self, key: str) -> bool:
        try:
            deleted = self._execute("DEL", key)
        except OSError:
            return False
        return bool(deleted)

    def _execute(self, *parts: str | bytes) -> object:
        with socket.create_connection((self.host, self.port), timeout=self.timeout_seconds) as conn:
            if self.password:
                conn.sendall(_command("AUTH", self.password))
                _read_response(conn)
            if self.db:
                conn.sendall(_command("SELECT", str(self.db)))
                _read_response(conn)
            conn.sendall(_command(*parts))
            return _read_response(conn)


class DatabaseUserMemoryStore:
    def __init__(
        self,
        *,
        url: str,
        table_name: str = "justfastllm_user_memory",
        enabled: bool = True,
        strict: bool = False,
    ) -> None:
        self.url = url
        self.table_name = _safe_storage_name(table_name)
        self.enabled = enabled and bool(url)
        self.strict = strict

    def get_preferences(self, user_id: str) -> dict[str, object]:
        if not self.enabled:
            return {}
        return _as_dict(_as_dict(self._load_state().get("preferences")).get(user_id))

    def save_preferences(self, user_id: str, preferences: dict[str, object]) -> dict[str, object]:
        if not self.enabled:
            return {}
        state = self._load_state()
        all_preferences = _as_dict(state.get("preferences"))
        current = _as_dict(all_preferences.get(user_id))
        current.update(preferences)
        all_preferences[user_id] = current
        state["preferences"] = all_preferences
        self._save_state(state)
        return dict(current)

    def add_feedback(self, user_id: str, feedback: dict[str, object]) -> dict[str, object]:
        if not self.enabled:
            return {}
        entry = {"created_at": int(time.time()), **feedback}
        state = self._load_state()
        all_feedback = _as_dict(state.get("feedback"))
        current = _as_list(all_feedback.get(user_id))
        current.append(entry)
        all_feedback[user_id] = current
        state["feedback"] = all_feedback
        self._save_state(state)
        return dict(entry)

    def get_feedback(self, user_id: str) -> list[dict[str, object]]:
        if not self.enabled:
            return []
        return [item for item in _as_list(_as_dict(self._load_state().get("feedback")).get(user_id)) if isinstance(item, dict)]

    def delete_user(self, user_id: str) -> dict[str, object]:
        if not self.enabled:
            return {"preferences_deleted": False, "feedback_deleted": False}
        state = self._load_state()
        all_preferences = _as_dict(state.get("preferences"))
        all_feedback = _as_dict(state.get("feedback"))
        preferences_deleted = all_preferences.pop(user_id, None) is not None
        feedback_deleted = all_feedback.pop(user_id, None) is not None
        state["preferences"] = all_preferences
        state["feedback"] = all_feedback
        self._save_state(state)
        return {"preferences_deleted": preferences_deleted, "feedback_deleted": feedback_deleted}

    def storage_health(self) -> dict[str, object]:
        family = _database_family(self.url)
        driver = _database_driver_dependency(self.url)
        transport = _database_transport_security(self.url)
        if not self.enabled:
            return {"ok": True, "backend": "database", "disabled": True, "database_family": family, "driver_dependency": driver, "transport_security": transport}
        try:
            state = _load_mongo_state(self.url, self.table_name) if _is_mongo_url(self.url) else _load_sql_state(self.url, self.table_name)
            if not isinstance(state, dict):
                state = {"preferences": {}, "feedback": {}}
            state.setdefault("preferences", {})
            state.setdefault("feedback", {})
            if _is_mongo_url(self.url):
                _save_mongo_state(self.url, self.table_name, state)
                reloaded = _load_mongo_state(self.url, self.table_name)
            else:
                _save_sql_state(self.url, self.table_name, state)
                reloaded = _load_sql_state(self.url, self.table_name)
            if not _state_matches(state, reloaded):
                raise OSError("database user memory readback mismatch")
        except Exception as exc:
            return {"ok": False, "backend": "database", "database_family": family, "driver_dependency": driver, "transport_security": transport, "error": _safe_error(exc)}
        return {"ok": True, "backend": "database", "database_family": family, "driver_dependency": driver, "transport_security": transport, "table": self.table_name}

    def _load_state(self) -> dict[str, object]:
        try:
            state = _load_mongo_state(self.url, self.table_name) if _is_mongo_url(self.url) else _load_sql_state(self.url, self.table_name)
        except Exception as exc:
            if self.strict:
                raise OSError("database user memory load failed") from exc
            return {"preferences": {}, "feedback": {}}
        if not isinstance(state, dict):
            return {"preferences": {}, "feedback": {}}
        state.setdefault("preferences", {})
        state.setdefault("feedback", {})
        return state

    def _save_state(self, state: dict[str, object]) -> None:
        try:
            if _is_mongo_url(self.url):
                _save_mongo_state(self.url, self.table_name, state)
            else:
                _save_sql_state(self.url, self.table_name, state)
        except Exception as exc:
            if self.strict:
                raise OSError("database user memory save failed") from exc
            return


def preferences_prompt(preferences: dict[str, object]) -> str:
    if not preferences:
        return ""
    lines = ["User preferences remembered by the gateway:"]
    for key, value in sorted(preferences.items()):
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    for marker in ("@", "://", "/"):
        if marker in message:
            return exc.__class__.__name__
    return message[:160]
