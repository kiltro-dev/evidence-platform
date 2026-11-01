"""Model layer: LLM behind a narrow interface, always swappable."""

from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    name: str

    @property
    def available(self) -> bool:
        ...

    def complete_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 1500,
        reasoning_effort: str | None = None,
    ) -> dict:
        """Return parsed JSON from the model. Raises on transport errors.

        `reasoning_effort` (e.g. "low") is honored by MetaClient and
        ignored by test doubles; None preserves default model behavior.
        """
        ...
