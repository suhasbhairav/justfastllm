from __future__ import annotations

import argparse
import asyncio
import logging
import time
import tracemalloc
from pathlib import Path
from typing import Any

from justfastllm.app import create_app
from justfastllm.cache import MemoryResponseCache
from justfastllm.config import load_settings
from justfastllm.http import HttpClient
from justfastllm.jsonutil import dumps_bytes, loads_bytes
from justfastllm.models import HttpRequest, UpstreamResponse
from justfastllm.providers.factory import ProviderFactory


class BenchmarkHttpClient(HttpClient):
    def __init__(self) -> None:
        self.requests = 0
        self.response = UpstreamResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=dumps_bytes(
                {
                    "id": "bench",
                    "object": "chat.completion",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                }
            ),
        )

    def send(self, request: HttpRequest) -> UpstreamResponse:
        self.requests += 1
        return self.response


async def asgi_request(app, payload: dict[str, Any]) -> tuple[int, bytes]:
    body = dumps_bytes(payload)
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[dict[str, Any]] = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
        send,
    )
    return sent[0]["status"], sent[1].get("body", b"")


async def run_benchmark(iterations: int, warmup: int) -> dict[str, object]:
    env = {
        "JUSTFASTLLM_CACHE_BACKEND": "memory",
        "JUSTFASTLLM_CACHE_ENABLED": "false",
        "OPENAI_API_KEY": "benchmark-key",
    }
    settings = load_settings(env_file=Path("/tmp/justfastllm-missing.env"), environ=env)
    http = BenchmarkHttpClient()
    logger = logging.getLogger("justfastllm.benchmark")
    logger.disabled = True
    app = create_app(
        settings=settings,
        provider_factory=ProviderFactory(settings, http),
        cache=MemoryResponseCache(max_items=1, ttl_seconds=1, enabled=False),
        logger=logger,
    )
    payload = {
        "provider": "openai",
        "model": "benchmark-model",
        "messages": [{"role": "user", "content": "hello"}],
    }

    for _ in range(warmup):
        await asgi_request(app, payload)

    tracemalloc.start()
    started = time.perf_counter()
    for _ in range(iterations):
        status, body = await asgi_request(app, payload)
        if status != 200:
            raise RuntimeError(f"unexpected status={status} body={loads_bytes(body)}")
    elapsed = time.perf_counter() - started
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "iterations": iterations,
        "total_seconds": round(elapsed, 6),
        "requests_per_second": round(iterations / elapsed, 2),
        "mean_ms": round((elapsed / iterations) * 1000, 4),
        "current_kib": round(current / 1024, 2),
        "peak_kib": round(peak / 1024, 2),
        "upstream_calls": http.requests,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark justfastllm ASGI gateway overhead.")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=50)
    args = parser.parse_args()
    result = asyncio.run(run_benchmark(args.iterations, args.warmup))
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
