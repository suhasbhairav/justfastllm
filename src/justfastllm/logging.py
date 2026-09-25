from __future__ import annotations

import logging
import re
from typing import Mapping


SENSITIVE_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(r"([A-Za-z0-9_]*API[_-]?KEY[A-Za-z0-9_]*\s*[:=]\s*)[^\s,}]+", re.IGNORECASE),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
)


def configure_logging(level: str) -> logging.Logger:
    logger = logging.getLogger("justfastllm")
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    return logger


def redact(value: object) -> str:
    text = str(value)
    for pattern in SENSITIVE_PATTERNS:
        if pattern.pattern.startswith("([A-Za-z0-9_]*API"):
            text = pattern.sub(r"\1[redacted]", text)
        elif "@" in pattern.pattern:
            text = pattern.sub("[redacted-email]", text)
        else:
            text = pattern.sub("Bearer [redacted]", text)
    return text


def sanitize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    sanitized = {}
    for key, value in headers.items():
        if key.lower() in {"authorization", "x-api-key", "api-key"}:
            sanitized[key] = "[redacted]"
        else:
            sanitized[key] = redact(value)
    return sanitized

