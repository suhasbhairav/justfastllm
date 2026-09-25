from __future__ import annotations

import os
import unittest
import urllib.request
from pathlib import Path
from typing import Any

from justfastllm.app import create_app
from justfastllm.cache import MemoryResponseCache
from justfastllm.config import load_settings
from justfastllm.jsonutil import dumps_bytes, loads_bytes


async def asgi_request(app, method: str, path: str, payload: dict[str, Any] | None = None):
    body = dumps_bytes(payload) if payload is not None else b""
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[dict[str, Any]] = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
        send,
    )
    status = sent[0]["status"]
    headers = {key.decode(): value.decode() for key, value in sent[0].get("headers", [])}
    response_body = sent[1].get("body", b"")
    return status, headers, response_body


def ollama_model() -> str:
    configured = os.getenv("JUSTFASTLLM_OLLAMA_TEST_MODEL")
    if configured:
        return configured

    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as response:
        payload = loads_bytes(response.read())
    for model in payload.get("models", []):
        capabilities = model.get("capabilities", [])
        if "completion" in capabilities:
            return model["model"]
    raise RuntimeError("no Ollama completion model is available")


@unittest.skipUnless(
    os.getenv("JUSTFASTLLM_RUN_OLLAMA_TESTS") == "1",
    "set JUSTFASTLLM_RUN_OLLAMA_TESTS=1 to run local Ollama integration tests",
)
class OllamaIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def app_for_model(self, model: str):
        settings = load_settings(
            env_file=Path("/tmp/justfastllm-missing.env"),
            environ={
                "JUSTFASTLLM_CACHE_BACKEND": "memory",
                "JUSTFASTLLM_REQUEST_TIMEOUT_SECONDS": "180",
                "OLLAMA_BASE_URL": "http://localhost:11434/v1",
                "OLLAMA_DEFAULT_MODEL": model,
            },
        )
        return create_app(settings=settings, cache=MemoryResponseCache(max_items=8, ttl_seconds=30))

    async def test_gateway_chat_completion_against_local_ollama_model(self) -> None:
        model = ollama_model()
        app = self.app_for_model(model)

        status, headers, body = await asgi_request(
            app,
            "POST",
            "/v1/chat/completions",
            {
                "provider": "ollama",
                "model": model,
                "messages": [{"role": "user", "content": "Reply with one short sentence."}],
                "stream": False,
                "temperature": 0,
            },
        )

        payload = loads_bytes(body)
        self.assertEqual(status, 200, payload)
        self.assertEqual(headers["x-justfastllm-provider"], "ollama")
        self.assertIn("x-request-id", headers)
        self.assertTrue(payload.get("choices"), payload)

    async def test_gateway_model_discovery_against_local_ollama(self) -> None:
        model = ollama_model()
        app = self.app_for_model(model)

        status, headers, body = await asgi_request(app, "GET", "/v1/providers/ollama/models")

        payload = loads_bytes(body)
        model_ids = {item.get("id") for item in payload.get("data", []) if isinstance(item, dict)}
        self.assertEqual(status, 200, payload)
        self.assertEqual(headers["x-justfastllm-provider"], "ollama")
        self.assertIn("x-request-id", headers)
        self.assertIn(model, model_ids)


if __name__ == "__main__":
    unittest.main()
