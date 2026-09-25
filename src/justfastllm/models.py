from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


JsonDict = dict[str, object]


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes
    timeout: float


@dataclass(frozen=True, slots=True)
class UpstreamResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    api_key: str
    base_url: str
    default_model: str
    timeout_seconds: float
    requires_api_key: bool = True
    extra_headers: Mapping[str, str] = field(default_factory=dict)
