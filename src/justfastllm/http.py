from __future__ import annotations

import http.client
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from justfastllm.errors import UpstreamTransportError
from justfastllm.models import HttpRequest, UpstreamResponse


class HttpClient:
    def send(self, request: HttpRequest) -> UpstreamResponse:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Origin:
    scheme: str
    host: str
    port: int
    timeout: float


class PooledHttpClient(HttpClient):
    """Tiny stdlib HTTP client with per-origin connection reuse."""

    def __init__(self, *, max_connections_per_origin: int = 8) -> None:
        self.max_connections_per_origin = max(1, max_connections_per_origin)
        self._pools: dict[Origin, list[http.client.HTTPConnection]] = {}
        self._lock = threading.Lock()

    def send(self, request: HttpRequest) -> UpstreamResponse:
        origin, target = split_url(request.url, request.timeout)
        last_error: Exception | None = None
        for _ in range(2):
            connection = self._acquire(origin)
            try:
                connection.request(
                    request.method.upper(),
                    target,
                    body=request.body,
                    headers=dict(request.headers),
                )
                response = connection.getresponse()
                body = response.read()
                headers = dict(response.getheaders())
                should_reuse = response.getheader("Connection", "").lower() != "close"
                if should_reuse:
                    self._release(origin, connection)
                else:
                    connection.close()
                return UpstreamResponse(
                    status_code=response.status,
                    headers=headers,
                    body=body,
                )
            except (OSError, http.client.HTTPException) as exc:
                last_error = exc
                connection.close()
        if last_error is not None:
            raise UpstreamTransportError(str(last_error)) from last_error
        raise UpstreamTransportError("HTTP request failed without an exception")

    def close(self) -> None:
        with self._lock:
            pools = self._pools
            self._pools = {}
        for connections in pools.values():
            for connection in connections:
                connection.close()

    def _acquire(self, origin: Origin) -> http.client.HTTPConnection:
        with self._lock:
            pool = self._pools.get(origin)
            if pool:
                return pool.pop()
        if origin.scheme == "https":
            return http.client.HTTPSConnection(origin.host, origin.port, timeout=origin.timeout)
        return http.client.HTTPConnection(origin.host, origin.port, timeout=origin.timeout)

    def _release(self, origin: Origin, connection: http.client.HTTPConnection) -> None:
        with self._lock:
            pool = self._pools.setdefault(origin, [])
            if len(pool) < self.max_connections_per_origin:
                pool.append(connection)
                return
        connection.close()


def split_url(url: str, timeout: float) -> tuple[Origin, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise UpstreamTransportError(f"unsupported URL scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise UpstreamTransportError("URL host is required")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return Origin(parsed.scheme, parsed.hostname, port, timeout), path


UrllibHttpClient = PooledHttpClient
