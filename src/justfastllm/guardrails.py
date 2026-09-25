from __future__ import annotations

import re
from typing import Any, Iterable

from justfastllm.errors import GuardrailViolationError


KNOWN_GUARDRAILS = ("required_messages", "message_length", "block_patterns")


class Guardrails:
    def __init__(
        self,
        *,
        max_message_chars: int,
        block_patterns: Iterable[str] = (),
        enabled: bool = True,
        disabled: Iterable[str] = (),
    ) -> None:
        self.max_message_chars = max_message_chars
        self.block_patterns = tuple(re.compile(pattern, re.IGNORECASE) for pattern in block_patterns)
        self.enabled = enabled
        self.disabled = set(disabled)

    def status(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "checks": [
                {"name": name, "enabled": self.enabled and name not in self.disabled}
                for name in KNOWN_GUARDRAILS
            ],
        }

    def configure(
        self,
        *,
        enabled: bool | None = None,
        enable: Iterable[str] = (),
        disable: Iterable[str] = (),
    ) -> dict[str, object]:
        if enabled is not None:
            self.enabled = enabled
        for name in enable:
            self.disabled.discard(_normalize_guardrail_name(name))
        for name in disable:
            self.disabled.add(_normalize_guardrail_name(name))
        self.disabled = {name for name in self.disabled if name in KNOWN_GUARDRAILS}
        return self.status()

    def validate_chat_payload(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return

        messages = payload.get("messages")
        if "required_messages" not in self.disabled and (not isinstance(messages, list) or not messages):
            raise GuardrailViolationError("messages must be a non-empty list")
        if not isinstance(messages, list):
            return

        text = _extract_text(messages)
        if "message_length" not in self.disabled and len(text) > self.max_message_chars:
            raise GuardrailViolationError("messages exceed JUSTFASTLLM_MAX_MESSAGE_CHARS")

        if "block_patterns" not in self.disabled:
            for pattern in self.block_patterns:
                if pattern.search(text):
                    raise GuardrailViolationError(f"blocked by guardrail pattern: {pattern.pattern}")


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_extract_text(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(_extract_text(item) for item in value.values())
    return ""


def _normalize_guardrail_name(name: object) -> str:
    return str(name).strip().lower().replace("-", "_")
