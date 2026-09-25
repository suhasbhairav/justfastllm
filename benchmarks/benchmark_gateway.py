from __future__ import annotations

import argparse
import asyncio
import gc
import json
import logging
import os
import platform
import resource
import statistics
import time
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


async def asgi_request(app, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
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
    headers = {key.decode(): value.decode() for key, value in sent[0].get("headers", [])}
    return sent[0]["status"], headers, sent[1].get("body", b"")


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((pct / 100) * len(ordered) + 0.999999) - 1))
    return ordered[index]


def rss_mb() -> float:
    gc.collect()
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if platform.system() == "Darwin":
        return usage / (1024 * 1024)
    return usage / 1024


def base_env() -> dict[str, str]:
    return {
        "JUSTFASTLLM_CACHE_BACKEND": "memory",
        "JUSTFASTLLM_CACHE_ENABLED": "false",
        "JUSTFASTLLM_USER_MEMORY_BACKEND": "memory",
        "JUSTFASTLLM_USER_MEMORY_ENABLED": "false",
    }


async def run_overhead_benchmark(iterations: int, warmup: int) -> dict[str, object]:
    env = {
        **base_env(),
        "OPENAI_API_KEY": "benchmark-key",
        "OPENAI_MODEL": "gpt-5-nano",
    }
    settings = load_settings(env_file=Path("/tmp/justfastllm-missing.env"), environ=env)
    http = BenchmarkHttpClient()
    logger = logging.getLogger("justfastllm.benchmark")
    logger.disabled = True
    memory_before_mb = rss_mb()
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

    memory_at_rest_mb = rss_mb()
    latencies_ms: list[float] = []
    started = time.perf_counter()
    for _ in range(iterations):
        request_started = time.perf_counter()
        status, _, body = await asgi_request(app, payload)
        latencies_ms.append((time.perf_counter() - request_started) * 1000)
        if status != 200:
            raise RuntimeError(f"unexpected status={status} body={loads_bytes(body)}")
    elapsed = time.perf_counter() - started
    memory_after_mb = rss_mb()

    return {
        "mode": "in_process_added_latency",
        "iterations": iterations,
        "warmup": warmup,
        "total_seconds": round(elapsed, 6),
        "requests_per_second": round(iterations / elapsed, 2),
        "mean_ms": round(statistics.fmean(latencies_ms), 4),
        "median_ms": round(statistics.median(latencies_ms), 4),
        "p95_ms": round(percentile(latencies_ms, 95), 4),
        "p99_ms": round(percentile(latencies_ms, 99), 4),
        "min_ms": round(min(latencies_ms), 4),
        "max_ms": round(max(latencies_ms), 4),
        "memory_before_app_mb": round(memory_before_mb, 3),
        "memory_at_rest_mb": round(memory_at_rest_mb, 3),
        "memory_after_benchmark_mb": round(memory_after_mb, 3),
        "memory_app_delta_mb": round(memory_at_rest_mb - memory_before_mb, 3),
        "upstream_calls": http.requests,
        "provider_sdks": "none",
    }


async def run_real_openai_verification(calls: int, model: str, max_completion_tokens: int) -> dict[str, object]:
    environ = dict(os.environ)
    settings = load_settings(environ={**environ, **base_env(), "OPENAI_MODEL": model})
    if not settings.providers["openai"].api_key:
        raise RuntimeError("OPENAI_API_KEY is required for real OpenAI verification")

    logger = logging.getLogger("justfastllm.real_openai_benchmark")
    logger.disabled = True
    app = create_app(
        settings=settings,
        provider_factory=ProviderFactory(settings),
        cache=MemoryResponseCache(max_items=1, ttl_seconds=1, enabled=False),
        logger=logger,
    )

    latencies_ms: list[float] = []
    failures: list[dict[str, object]] = []
    verified = 0
    started = time.perf_counter()
    for index in range(calls):
        payload = {
            "provider": "openai",
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": f"Return exactly this token: ok-{index + 1}",
                }
            ],
            "max_completion_tokens": max_completion_tokens,
            "reasoning_effort": "minimal",
        }
        request_started = time.perf_counter()
        status, _, body = await asgi_request(app, payload)
        latencies_ms.append((time.perf_counter() - request_started) * 1000)
        parsed = loads_bytes(body)
        if status != 200:
            failures.append({"call": index + 1, "status": status, "body": parsed})
            continue
        choices = parsed.get("choices", []) if isinstance(parsed, dict) else []
        content = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message", {})
            if isinstance(message, dict):
                content = str(message.get("content", ""))
        if content.strip():
            verified += 1
        else:
            failures.append({"call": index + 1, "status": status, "body": parsed})

    elapsed = time.perf_counter() - started
    return {
        "mode": "real_openai_gateway_verification",
        "provider": "openai",
        "model": model,
        "max_completion_tokens": max_completion_tokens,
        "reasoning_effort": "minimal",
        "calls_requested": calls,
        "calls_verified": verified,
        "failures": len(failures),
        "failure_samples": failures[:3],
        "total_seconds": round(elapsed, 6),
        "mean_ms": round(statistics.fmean(latencies_ms), 3) if latencies_ms else 0,
        "median_ms": round(statistics.median(latencies_ms), 3) if latencies_ms else 0,
        "p95_ms": round(percentile(latencies_ms, 95), 3),
        "p99_ms": round(percentile(latencies_ms, 99), 3),
        "min_ms": round(min(latencies_ms), 3) if latencies_ms else 0,
        "max_ms": round(max(latencies_ms), 3) if latencies_ms else 0,
    }


def print_result(result: dict[str, object], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    for key, value in result.items():
        print(f"{key}: {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark justfastllm ASGI gateway overhead.")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--real-openai-calls", type=int, default=0)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL") or "gpt-5-nano")
    parser.add_argument("--max-completion-tokens", type=int, default=128)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.real_openai_calls > 0:
        result = asyncio.run(
            run_real_openai_verification(
                args.real_openai_calls,
                args.model,
                args.max_completion_tokens,
            )
        )
    else:
        result = asyncio.run(run_overhead_benchmark(args.iterations, args.warmup))
    print_result(result, json_output=args.json)


if __name__ == "__main__":
    main()
