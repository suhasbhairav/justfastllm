# Testing

## Standard Suite

Run the default suite before every change:

```bash
PYTHONPATH=src python3 -m unittest
```

This covers:

- Provider request construction.
- Provider factory routing.
- Dynamic OpenAI-compatible provider registration.
- Gateway routes for chat, messages, skills, and agents.
- Guardrails.
- Guardrail enable/disable controls.
- User preferences and feedback memory.
- Request IDs and CORS headers.
- Memory cache behavior.
- Redis cache and user-memory protocol behavior with deterministic fake sockets.

## Compile Check

```bash
PYTHONPATH=src python3 -m compileall -q src tests
```

## Performance Check

```bash
PYTHONPATH=src python3 benchmarks/benchmark_gateway.py --iterations 1000 --warmup 50
```

This reports throughput, mean latency, and `tracemalloc` memory usage for the in-process gateway path.

## Real Ollama Verification

Major gateway changes should also be verified against a local Ollama model:

```bash
JUSTFASTLLM_RUN_OLLAMA_TESTS=1 \
JUSTFASTLLM_OLLAMA_TEST_MODEL=qwen3:8b \
PYTHONPATH=src python3 -m unittest tests.test_ollama_integration
```

If `JUSTFASTLLM_OLLAMA_TEST_MODEL` is omitted, the test selects the first local Ollama model with completion capability.

## Notes

The Ollama integration test is opt-in so ordinary CI does not require a local model server. It exercises the real gateway path through Ollama's OpenAI-compatible API for chat completions and live model discovery.
