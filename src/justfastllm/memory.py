from __future__ import annotations

import socket
import time
from typing import Any, Protocol
from urllib.parse import quote, urlparse

from justfastllm.cache import _command, _read_response
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


def preferences_prompt(preferences: dict[str, object]) -> str:
    if not preferences:
        return ""
    lines = ["User preferences remembered by the gateway:"]
    for key, value in sorted(preferences.items()):
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}

