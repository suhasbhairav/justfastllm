from __future__ import annotations

from collections.abc import Mapping

from justfastllm.skills import SkillRegistry


class AgentRunner:
    """Prepare lightweight agent runs before handing off to a provider."""

    def __init__(self, skills: SkillRegistry) -> None:
        self.skills = skills

    def build_provider_payload(self, payload: Mapping[str, object]) -> dict[str, object]:
        messages = self._messages(payload)
        for skill_call in payload.get("skills", []):
            if not isinstance(skill_call, dict):
                continue
            name = str(skill_call.get("name", ""))
            arguments = skill_call.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {"input": arguments}
            result = self.skills.run(name, arguments)
            messages.append(
                {
                    "role": "system",
                    "content": f"Skill {name} result: {result}",
                }
            )

        provider_payload = dict(payload)
        provider_payload["messages"] = messages
        provider_payload.pop("input", None)
        provider_payload.pop("instructions", None)
        provider_payload.pop("skills", None)
        return provider_payload

    @staticmethod
    def _messages(payload: Mapping[str, object]) -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        instructions = payload.get("instructions")
        if isinstance(instructions, str) and instructions:
            messages.append({"role": "system", "content": instructions})

        existing = payload.get("messages")
        if isinstance(existing, list):
            messages.extend(message for message in existing if isinstance(message, dict))

        user_input = payload.get("input")
        if isinstance(user_input, str) and user_input:
            messages.append({"role": "user", "content": user_input})
        return messages

