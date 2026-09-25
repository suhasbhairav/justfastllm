from __future__ import annotations

import hashlib
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

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

    def clear(self) -> None:
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

    def clear(self) -> None:
        self._entries.clear()


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

    def clear(self) -> None:
        # Production cache invalidation should be namespace based. Avoid SCAN/DEL here
        # to keep this client tiny and prevent accidental broad key deletion.
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


ResponseCache = MemoryResponseCache
