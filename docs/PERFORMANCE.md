# Performance

`justfastllm` is designed to keep the runtime small:

- Raw ASGI app instead of a large web framework.
- Dataclasses and plain dictionaries instead of runtime model validation frameworks.
- Provider APIs are called directly over HTTP; provider SDKs are not used.
- Pooled stdlib HTTP transport reuses upstream connections.
- Redis cache and user memory use a small RESP client instead of a Redis SDK.
- Optional `orjson` speedup is isolated behind `.[speedups]`.

## Gateway Overhead Benchmark

Run the stdlib-only in-process benchmark:

```bash
PYTHONPATH=src python3 benchmarks/benchmark_gateway.py --iterations 1000 --warmup 50
```

The benchmark uses a fake upstream transport so it measures gateway routing, JSON handling, guardrails, cache checks, and ASGI response overhead without network variance.

Example output:

```text
iterations: 1000
total_seconds: 0.123456
requests_per_second: 8100.05
mean_ms: 0.1235
current_kib: 15.2
peak_kib: 48.7
upstream_calls: 1050
```

Use the benchmark for relative comparisons between commits and configuration choices.

