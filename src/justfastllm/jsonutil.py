from __future__ import annotations

import json
from typing import Any


try:  # pragma: no cover - depends on optional speedups extra
    import orjson
except ImportError:  # pragma: no cover - the stdlib path is covered
    orjson = None


def dumps_bytes(value: Any) -> bytes:
    if orjson is not None:
        return orjson.dumps(value)
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def loads_bytes(value: bytes) -> Any:
    return json.loads(value.decode("utf-8"))

