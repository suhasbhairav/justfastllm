from __future__ import annotations

import base64
import hashlib
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from justfastllm.dbstate import (
    database_family,
    database_driver_dependency,
    database_transport_security,
    is_mongo_url,
    load_mongo_state,
    load_sql_state,
    safe_storage_name,
    save_mongo_state,
    save_sql_state,
    state_matches,
)
from justfastllm.jsonutil import dumps_bytes


@dataclass(slots=True)
class CacheEntry:
    expires_at: float
    value: bytes


class CacheBackend(Protocol):
    enabled: bool

    def get(self, key: str) -> bytes | None:
        ...

    def set(self, key: str, value: bytes) -> None:
        ...

    def clear(self) -> int:
        ...

    def storage_health(self) -> dict[str, object]:
        ...


class MemoryResponseCache:
    def __init__(self, *, max_items: int, ttl_seconds: float, enabled: bool = True) -> None:
        self.max_items = max(0, max_items)
        self.ttl_seconds = max(0.0, ttl_seconds)
        self.enabled = enabled and self.max_items > 0 and self.ttl_seconds > 0
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()

    def get(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        entry = self._entries.get(key)
        now = time.monotonic()
        if entry is None:
            return None
        if entry.expires_at <= now:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return entry.value

    def set(self, key: str, value: bytes) -> None:
        if not self.enabled:
            return
        self._entries[key] = CacheEntry(expires_at=time.monotonic() + self.ttl_seconds, value=value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_items:
            self._entries.popitem(last=False)

    def clear(self) -> int:
        count = len(self._entries)
        self._entries.clear()
        return count

    def storage_health(self) -> dict[str, object]:
        if not self.enabled:
            return {"ok": True, "backend": "memory", "disabled": True}
        return {"ok": False, "backend": "memory", "error": "memory response cache is not durable"}


class RedisResponseCache:
    def __init__(
        self,
        *,
        url: str,
        ttl_seconds: float,
        key_prefix: str,
        enabled: bool = True,
        timeout_seconds: float = 1.0,
    ) -> None:
        parsed = urlparse(url)
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port or 6379
        self.password = parsed.password
        self.db = int((parsed.path or "/0").lstrip("/") or "0")
        self.ttl_seconds = max(1, int(ttl_seconds))
        self.key_prefix = key_prefix
        self.timeout_seconds = timeout_seconds
        self.enabled = enabled

    def get(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        try:
            value = self._execute("GET", self.key_prefix + key)
            return value if isinstance(value, bytes) else None
        except OSError:
            return None

    def set(self, key: str, value: bytes) -> None:
        if not self.enabled:
            return
        try:
            self._execute("SETEX", self.key_prefix + key, str(self.ttl_seconds), value)
        except OSError:
            return

    def clear(self) -> int:
        deleted = 0
        cursor = "0"
        iterations = 0
        try:
            while True:
                response = self._execute("SCAN", cursor, "MATCH", f"{self.key_prefix}*", "COUNT", "100")
                if not isinstance(response, list) or len(response) != 2:
                    return deleted
                cursor = _text(response[0])
                keys = response[1] if isinstance(response[1], list) else []
                if keys:
                    result = self._execute("DEL", *[_bytes(key) for key in keys])
                    if isinstance(result, int):
                        deleted += result
                iterations += 1
                if cursor == "0" or iterations >= 1000:
                    return deleted
        except OSError:
            return deleted

    def storage_health(self) -> dict[str, object]:
        if not self.enabled:
            return {"ok": True, "backend": "redis", "disabled": True}
        key = f"{self.key_prefix}health:{int(time.time() * 1000)}"
        try:
            self._execute("SETEX", key, "5", b"ok")
            value = self._execute("GET", key)
            self._execute("DEL", key)
        except Exception as exc:
            return {"ok": False, "backend": "redis", "error": _safe_error(exc)}
        return {"ok": value == b"ok", "backend": "redis", "error": "" if value == b"ok" else "redis health value mismatch"}

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


class DatabaseResponseCache:
    def __init__(
        self,
        *,
        url: str,
        table_name: str = "justfastllm_response_cache",
        max_items: int,
        ttl_seconds: float,
        enabled: bool = True,
        strict: bool = False,
    ) -> None:
        self.url = url
        self.table_name = safe_storage_name(table_name, "justfastllm_response_cache")
        self.max_items = max(0, max_items)
        self.ttl_seconds = max(0.0, ttl_seconds)
        self.enabled = enabled and bool(url) and self.max_items > 0 and self.ttl_seconds > 0
        self.strict = strict

    def get(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        state = self._load_state()
        entries = _as_dict(state.get("entries"))
        entry = _as_dict(entries.get(key))
        if not entry:
            return None
        now = time.time()
        expires_at = float(entry.get("expires_at") or 0)
        if expires_at <= now:
            entries.pop(key, None)
            state["entries"] = entries
            self._save_state(state)
            return None
        encoded = entry.get("value")
        if not isinstance(encoded, str):
            return None
        try:
            return base64.b64decode(encoded.encode("ascii"))
        except Exception:
            return None

    def set(self, key: str, value: bytes) -> None:
        if not self.enabled:
            return
        now = time.time()
        state = self._load_state()
        entries = {
            item_key: item_value
            for item_key, item_value in _as_dict(state.get("entries")).items()
            if isinstance(item_value, dict) and float(item_value.get("expires_at") or 0) > now
        }
        entries[key] = {
            "value": base64.b64encode(value).decode("ascii"),
            "expires_at": now + self.ttl_seconds,
            "stored_at": now,
        }
        while len(entries) > self.max_items:
            oldest_key = min(entries, key=lambda item_key: float(_as_dict(entries[item_key]).get("stored_at") or 0))
            entries.pop(oldest_key, None)
        state["entries"] = entries
        self._save_state(state)

    def clear(self) -> int:
        if not self.enabled:
            return 0
        state = self._load_state()
        entries = _as_dict(state.get("entries"))
        count = len(entries)
        state["entries"] = {}
        self._save_state(state)
        return count

    def storage_health(self) -> dict[str, object]:
        family = database_family(self.url)
        driver = database_driver_dependency(self.url)
        transport = database_transport_security(self.url)
        if not self.enabled:
            return {"ok": True, "backend": "database", "disabled": True, "database_family": family, "driver_dependency": driver, "transport_security": transport}
        try:
            state = load_mongo_state(self.url, self.table_name) if is_mongo_url(self.url) else load_sql_state(self.url, self.table_name)
            if not isinstance(state, dict):
                state = {"entries": {}}
            state.setdefault("entries", {})
            if is_mongo_url(self.url):
                save_mongo_state(self.url, self.table_name, state)
                reloaded = load_mongo_state(self.url, self.table_name)
            else:
                save_sql_state(self.url, self.table_name, state)
                reloaded = load_sql_state(self.url, self.table_name)
            if not state_matches(state, reloaded):
                raise OSError("database response cache readback mismatch")
        except Exception as exc:
            return {"ok": False, "backend": "database", "database_family": family, "driver_dependency": driver, "transport_security": transport, "error": _safe_error(exc)}
        return {"ok": True, "backend": "database", "database_family": family, "driver_dependency": driver, "transport_security": transport, "table": self.table_name}

    def _load_state(self) -> dict[str, object]:
        try:
            state = load_mongo_state(self.url, self.table_name) if is_mongo_url(self.url) else load_sql_state(self.url, self.table_name)
        except Exception as exc:
            if self.strict:
                raise OSError("database response cache load failed") from exc
            return {"entries": {}}
        if not isinstance(state, dict):
            return {"entries": {}}
        state.setdefault("entries", {})
        return state

    def _save_state(self, state: dict[str, object]) -> None:
        try:
            if is_mongo_url(self.url):
                save_mongo_state(self.url, self.table_name, state)
            else:
                save_sql_state(self.url, self.table_name, state)
        except Exception as exc:
            if self.strict:
                raise OSError("database response cache save failed") from exc
            return


def cache_key(provider: str, payload: dict[str, Any]) -> str:
    filtered = {
        key: value
        for key, value in payload.items()
        if key not in {"stream", "provider", "metadata"}
    }
    blob = dumps_bytes({"provider": provider, "payload": filtered})
    return hashlib.sha256(blob).hexdigest()


def _command(*parts: str | bytes) -> bytes:
    encoded = [part if isinstance(part, bytes) else part.encode("utf-8") for part in parts]
    chunks = [f"*{len(encoded)}\r\n".encode("ascii")]
    for part in encoded:
        chunks.append(f"${len(part)}\r\n".encode("ascii"))
        chunks.append(part)
        chunks.append(b"\r\n")
    return b"".join(chunks)


def _read_response(conn: socket.socket) -> object:
    prefix = _read_exact(conn, 1)
    if prefix == b"+":
        return _read_line(conn).decode("utf-8")
    if prefix == b"-":
        raise OSError(_read_line(conn).decode("utf-8"))
    if prefix == b":":
        return int(_read_line(conn))
    if prefix == b"$":
        length = int(_read_line(conn))
        if length == -1:
            return None
        data = _read_exact(conn, length)
        _read_exact(conn, 2)
        return data
    if prefix == b"*":
        count = int(_read_line(conn))
        return [_read_response(conn) for _ in range(count)]
    raise OSError("invalid Redis response")


def _read_line(conn: socket.socket) -> bytes:
    chunks = []
    while True:
        chunk = _read_exact(conn, 1)
        if chunk == b"\r":
            _read_exact(conn, 1)
            return b"".join(chunks)
        chunks.append(chunk)


def _read_exact(conn: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = conn.recv(count - len(data))
        if not chunk:
            raise OSError("connection closed")
        data += chunk
    return data


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8")


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    for marker in ("@", "://", "/"):
        if marker in message:
            return exc.__class__.__name__
    return message[:160]


ResponseCache = MemoryResponseCache
