"""Claim state machine for Atlas Live (M1b).

M1 lifecycle: ``escuchada`` -> ``investigando`` -> ``veredicto``.

Scope split (enforced by convention + hooks, not by hiding state):
- M1 (this milestone): the detector emits ``escuchada`` via
  :meth:`ClaimTracker.mark_heard`. Nothing else moves in M1.
- M2 (researchers/verifier, arrives later): drives ``investigando``
  and ``veredicto`` through :meth:`ClaimTracker.set_state`, attaches
  side effects via :meth:`ClaimTracker.register_hook`, and may extend
  the graph via :meth:`ClaimTracker.allow_transition` (e.g. error /
  retry states) without editing this file.

Every transition publishes ``claim.state{claim_id,state,prev,actor}``
on the bus.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable

from .bus import EventBus

log = logging.getLogger(__name__)

ST_ESCUCHADA = "escuchada"
ST_INVESTIGANDO = "investigando"
ST_VEREDICTO = "veredicto"

Hook = Callable[[str, dict], Any | Awaitable[Any]]


class ClaimTracker:
    """Owns per-claim state; validates transitions; emits ``claim.state``."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._states: dict[str, str] = {}
        self._allowed: dict[str | None, set[str]] = {
            None: {ST_ESCUCHADA},
            ST_ESCUCHADA: {ST_INVESTIGANDO},
            ST_INVESTIGANDO: {ST_VEREDICTO},
            ST_VEREDICTO: set(),
        }
        # M2 hook points: (from_state, to_state) -> [fn(claim_id, event)].
        # from_state None matches any origin (use for to_state-only hooks).
        self._hooks: dict[tuple[str | None, str], list[Hook]] = {}

    def state(self, claim_id: str) -> str | None:
        """Current state of ``claim_id`` (None when unknown)."""
        return self._states.get(claim_id)

    def allow_transition(self, from_state: str | None, to_state: str) -> None:
        """Extend the transition graph (M2: extra states without edits here)."""
        self._allowed.setdefault(from_state, set()).add(to_state)

    def register_hook(
        self, to_state: str, fn: Hook, from_state: str | None = None
    ) -> None:
        """Run ``fn(claim_id, event)`` after a matching transition.

        ``fn`` may be plain or async; exceptions are logged, never raised
        (a hook must not break the state commit or the bus event).
        """
        self._hooks.setdefault((from_state, to_state), []).append(fn)

    async def set_state(
        self,
        claim_id: str,
        to_state: str,
        actor: str = "m1",
        note: str = "",
    ) -> dict:
        """Move ``claim_id`` to ``to_state``; publish ``claim.state``.

        Raises ValueError on an illegal transition; the stored state is
        committed before hooks run so hooks always observe the new state.
        """
        prev = self._states.get(claim_id)
        if to_state not in self._allowed.get(prev, set()):
            raise ValueError(
                f"illegal claim transition {prev!r} -> {to_state!r}"
                f" for {claim_id}"
            )
        self._states[claim_id] = to_state
        event = await self._bus.publish(
            {
                "type": "claim.state",
                "claim_id": claim_id,
                "state": to_state,
                "prev": prev,
                "actor": actor,
                "note": note,
            }
        )
        for key in ((prev, to_state), (None, to_state)):
            for fn in self._hooks.get(key, []):
                try:
                    res = fn(claim_id, event)
                    if inspect.isawaitable(res):
                        await res
                except Exception as exc:  # noqa: BLE001 - hooks are side effects
                    log.warning("ClaimTracker hook failed for %s: %r", claim_id, exc)
        return event

    async def mark_heard(
        self,
        claim_id: str,
        text: str = "",
        actor: str = "m1-detector",
    ) -> dict:
        """M1 detector path: a new claim enters as ``escuchada``."""
        return await self.set_state(claim_id, ST_ESCUCHADA, actor=actor, note=text)
