"""Scripted model double for offline deterministic tests."""

from __future__ import annotations


class ScriptedClient:
    """Returns canned JSON when a key substring matches the user prompt."""

    name = "scripted-test-double"

    def __init__(self, responses: dict[str, dict], default: dict | None = None) -> None:
        self.responses = responses
        self.default = default or {}
        self.calls: list[str] = []

    @property
    def available(self) -> bool:
        return True

    def complete_json(self, system: str, user: str, max_tokens: int = 1500) -> dict:
        self.calls.append(user)
        for key, payload in self.responses.items():
            if key in user:
                return dict(payload)
        return dict(self.default)
