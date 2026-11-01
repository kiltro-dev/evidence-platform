"""Asyncio event bus for Atlas Live (M1b).

JSON-only events: every published dict is validated JSON-serializable
and gets an envelope (``seq``, ``bus_ts``). Subscribers register per
event ``type`` (or ``None`` for all events); callbacks may be plain or
async functions. Each publish is appended to a per-run ``.jsonl`` log
so any run is replayable by M1c E2E tooling.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

Callback = Callable[[dict], Any | Awaitable[Any]]


class EventBus:
    """In-process pub/sub with history and a JSONL run log."""

    def __init__(self, run_log: str | Path | None = None) -> None:
        self._subs: dict[str | None, list[Callback]] = defaultdict(list)
        self._history: list[dict] = []
        self._seq = 0
        self._lock = asyncio.Lock()
        self._logfh = (
            open(run_log, "a", encoding="utf-8") if run_log is not None else None
        )

    def subscribe(self, event_type: str | None, callback: Callback) -> None:
        """Register ``callback`` for ``event_type`` (``None`` = all events)."""
        self._subs[event_type].append(callback)

    def unsubscribe(self, event_type: str | None, callback: Callback) -> None:
        """Remove a previously registered ``callback`` (no-op if absent)."""
        try:
            self._subs[event_type].remove(callback)
        except ValueError:
            pass

    async def publish(self, event: dict) -> dict:
        """Validate, envelope, log and dispatch one event.

        Returns the enveloped event. A failing subscriber is logged and
        skipped — one bad consumer never blocks the bus or the run log.
        """
        if not isinstance(event, dict) or "type" not in event:
            raise ValueError("event must be a dict with a 'type' key")
        async with self._lock:
            self._seq += 1
            enveloped = {"seq": self._seq, "bus_ts": time.time(), **event}
            # JSON-only contract: fail fast on non-serializable payloads.
            line = json.dumps(enveloped, ensure_ascii=False)
            self._history.append(enveloped)
            if self._logfh is not None:
                self._logfh.write(line + "\n")
                self._logfh.flush()
            targets = list(self._subs.get(event["type"], [])) + list(
                self._subs.get(None, [])
            )
        for cb in targets:
            try:
                res = cb(enveloped)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:  # noqa: BLE001 - bus must survive consumers
                log.warning("EventBus subscriber failed for %s: %r", event["type"], exc)
        return enveloped

    def history(self, event_type: str | None = None) -> list[dict]:
        """In-memory copy of published events, optionally filtered by type."""
        if event_type is None:
            return list(self._history)
        return [e for e in self._history if e.get("type") == event_type]

    async def close(self) -> None:
        """Flush and close the run log (idempotent)."""
        if self._logfh is not None:
            self._logfh.close()
            self._logfh = None
