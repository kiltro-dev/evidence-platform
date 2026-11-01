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
        json_schema: dict | None = None,
    ) -> dict:
        """Return parsed JSON from the model. Raises on transport errors.

        `reasoning_effort` (e.g. "low") is honored by MetaClient and
        ignored by test doubles; None preserves default model behavior.
        `json_schema` is {"name": str, "schema": dict} requesting
        decoding-constrained structured output; test doubles accept and
        ignore it (hand-validation stays at every call site).
        """
        ...
