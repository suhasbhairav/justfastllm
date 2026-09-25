from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from justfastllm.logging import redact


SkillHandler = Callable[[Mapping[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    handler: SkillHandler


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self.register(Skill("echo", "Return the input arguments unchanged.", self._echo))
        self.register(Skill("redact", "Redact emails and API secrets from text.", self._redact))
        self.register(Skill("utc_time", "Return the current UTC timestamp.", self._utc_time))

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def list(self) -> list[dict[str, str]]:
        return [
            {"name": skill.name, "description": skill.description}
            for skill in sorted(self._skills.values(), key=lambda item: item.name)
        ]

    def run(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        skill = self._skills.get(name)
        if skill is None:
            return {"error": {"code": "skill_not_found", "message": f"skill '{name}' is not registered"}}
        return skill.handler(arguments)

    @staticmethod
    def _echo(arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {"result": dict(arguments)}

    @staticmethod
    def _redact(arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {"result": redact(arguments.get("text", ""))}

    @staticmethod
    def _utc_time(arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {"result": dt.datetime.now(dt.UTC).isoformat()}

