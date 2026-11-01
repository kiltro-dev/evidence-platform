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

    def complete_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 1500,
        reasoning_effort: str | None = None,
    ) -> dict:
        _ = reasoning_effort  # test double: accepted, ignored
        self.calls.append(user)
        for key, payload in self.responses.items():
            if key in user:
                return dict(payload)
        return dict(self.default)


class FaultClient(ScriptedClient):
    """Scripted double with per-key fault injection for fallback tests.

    `faults` maps a user-prompt substring to a fault mode, checked BEFORE
    the canned `responses`:
      - "raise":     raise RuntimeError (simulates a transport/HTTP failure)
      - "empty":     raise RuntimeError("... empty content ..."), mirroring
                     MetaClient's empty-content error
      - "garbage":   return {"bogus": 1} (valid JSON, wrong schema)
      - "oversized": return a structuring-shaped payload with 5000
                     quantities (extraction must cap, never crash)

    Prompts matching no fault key fall through to the normal ScriptedClient
    responses/default behavior. Unknown fault modes raise ValueError.
    """

    name = "fault-injection-test-double"

    OVERSIZED_N = 5000

    def __init__(
        self,
        responses: dict[str, dict] | None = None,
        faults: dict[str, str] | None = None,
        default: dict | None = None,
    ) -> None:
        super().__init__(responses or {}, default)
        self.faults = faults or {}

    def complete_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 1500,
        reasoning_effort: str | None = None,
    ) -> dict:
        _ = reasoning_effort  # test double: accepted, ignored
        self.calls.append(user)
        for key, mode in self.faults.items():
            if key in user:
                return self._apply_fault(key, mode)
        for key, payload in self.responses.items():
            if key in user:
                return dict(payload)
        return dict(self.default)

    def _apply_fault(self, key: str, mode: str) -> dict:
        if mode == "raise":
            raise RuntimeError(f"scripted fault 'raise' for key {key!r}")
        if mode == "empty":
            raise RuntimeError(
                "Model returned empty content "
                "(scripted fault 'empty'); mirroring MetaClient."
            )
        if mode == "garbage":
            return {"bogus": 1}
        if mode == "oversized":
            return {
                "domain": "economy",
                "claim_type": "quantitative",
                "subject": "oversized scripted payload",
                "indicator_hint": None,
                "entities": [],
                "quantities": [
                    {"label": f"q{i}", "value": float(i), "unit": "USD"}
                    for i in range(self.OVERSIZED_N)
                ],
                "time_refs": [],
                "geo_refs": [],
                "conditions": [],
                "context_notes": "",
            }
        raise ValueError(f"unknown fault mode {mode!r} for key {key!r}")
