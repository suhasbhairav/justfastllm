from __future__ import annotations

import re
from typing import Mapping

from justfastllm.errors import PolicyViolationError
from justfastllm.jsonutil import loads_bytes


class PolicyEngine:
    def __init__(
        self,
        *,
        denied_models: tuple[str, ...] = (),
        allowed_providers: tuple[str, ...] = (),
        required_tags: tuple[str, ...] = (),
        max_prompt_tokens: int = 0,
        response_block_patterns: tuple[str, ...] = (),
    ) -> None:
        self.denied_models = tuple(item.lower() for item in denied_models)
        self.allowed_providers = tuple(item.lower() for item in allowed_providers)
        self.required_tags = required_tags
        self.max_prompt_tokens = max_prompt_tokens
        self.response_block_patterns = tuple(re.compile(pattern, re.IGNORECASE) for pattern in response_block_patterns)

    def status(self) -> dict[str, object]:
        return {
            "denied_models": list(self.denied_models),
            "allowed_providers": list(self.allowed_providers),
            "required_tags": list(self.required_tags),
            "max_prompt_tokens": self.max_prompt_tokens,
            "response_block_patterns": [pattern.pattern for pattern in self.response_block_patterns],
        }

    def validate_request(
        self,
        *,
        route: str,
        provider: str,
        model: str,
        payload: Mapping[str, object],
        estimated_tokens: int,
    ) -> None:
        del route
        if self.allowed_providers and provider.lower() not in self.allowed_providers:
            raise PolicyViolationError(f"provider '{provider}' is not allowed")
        normalized_model = model.lower()
        if normalized_model and (
            normalized_model in self.denied_models or normalized_model.split("/")[-1] in self.denied_models
        ):
            raise PolicyViolationError(f"model '{model}' is denied by policy")
        if self.max_prompt_tokens and estimated_tokens > self.max_prompt_tokens:
            raise PolicyViolationError("request exceeds policy token limit")
        if self.required_tags:
            tags = payload.get("tags", [])
            tag_set = {str(item) for item in tags} if isinstance(tags, list) else set()
            missing = [tag for tag in self.required_tags if tag not in tag_set]
            if missing:
                raise PolicyViolationError(f"missing required tags: {', '.join(missing)}")

    def validate_response(self, body: bytes) -> None:
        if not self.response_block_patterns:
            return
        try:
            payload = loads_bytes(body)
        except Exception:
            text = body.decode("utf-8", errors="ignore")
        else:
            text = _extract_text(payload)
        for pattern in self.response_block_patterns:
            if pattern.search(text):
                raise PolicyViolationError(f"response blocked by policy pattern: {pattern.pattern}")


def _extract_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_extract_text(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(_extract_text(item) for item in value.values())
    return str(value) if value is not None else ""
