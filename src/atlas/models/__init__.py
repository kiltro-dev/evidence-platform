"""Model layer: LLM behind a narrow interface, always swappable."""

from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    name: str

    @property
    def available(self) -> bool:
        ...

    def complete_json(self, system: str, user: str, max_tokens: int = 1500) -> dict:
        """Return parsed JSON from the model. Raises on transport errors."""
        ...
