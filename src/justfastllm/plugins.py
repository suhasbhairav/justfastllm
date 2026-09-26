from __future__ import annotations

import importlib
from collections.abc import Mapping
from types import ModuleType

from justfastllm.jsonutil import dumps_bytes, loads_bytes


class PluginManager:
    def __init__(self, modules: tuple[str, ...] = ()) -> None:
        self.module_names = modules
        self.modules = tuple(_load_module(name) for name in modules)

    def status(self) -> dict[str, object]:
        return {"modules": list(self.module_names), "loaded": [module.__name__ for module in self.modules]}

    def before_request(self, context: Mapping[str, object], payload: dict[str, object]) -> dict[str, object]:
        current = dict(payload)
        for module in self.modules:
            hook = getattr(module, "before_request", None)
            if hook is None:
                continue
            updated = hook(dict(context), dict(current))
            if isinstance(updated, dict):
                current = updated
        return current

    def after_response(self, context: Mapping[str, object], body: bytes, content_type: str) -> bytes:
        if "application/json" not in content_type.lower():
            return body
        try:
            payload = loads_bytes(body)
        except Exception:
            return body
        current = payload if isinstance(payload, dict) else {"result": payload}
        for module in self.modules:
            hook = getattr(module, "after_response", None)
            if hook is None:
                continue
            updated = hook(dict(context), dict(current))
            if isinstance(updated, dict):
                current = updated
        return dumps_bytes(current)


def _load_module(name: str) -> ModuleType:
    return importlib.import_module(name)
