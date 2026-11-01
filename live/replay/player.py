"""M4 replay player: serve a recorded JSONL event-log over WS.

Pacing is the original wall-clock pacing: the delay before each event
is the ``bus_ts`` gap to the previous event divided by ``rate``
(``rate=1`` = real time, ``rate=4`` = 4x). Every emitted frame is the
recorded event verbatim plus ``\"replay\": true``.

A per-gap cap (``max_gap_s``) keeps very long idle spans demo-friendly;
it applies AFTER the rate division and is reported, not hidden.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

# evidence-platform/ repo root = parent of live/.
RUNS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "runs"

# Run names are bare file stems: no separators, no traversal.
_SAFE_RUN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.~-]*")

#: Default cap for a single inter-event gap (seconds, after /rate).
DEFAULT_MAX_GAP_S = 10.0

#: Glob for replayable run logs (excludes *.asr.jsonl partial logs).
_RUN_GLOB = "run-*.jsonl"


def is_safe_run_name(run: str) -> bool:
    """True when ``run`` is a bare stem that cannot escape RUNS_DIR."""
    return bool(run) and bool(_SAFE_RUN_RE.fullmatch(run)) and "/" not in run


def resolve_run(run: str) -> Path | None:
    """Resolve ``run`` (stem or stem.jsonl) to a log path, or None."""
    if not run:
        return None
    name = run[:-len(".jsonl")] if run.endswith(".jsonl") else run
    if not is_safe_run_name(name):
        return None
    candidate = (RUNS_DIR / f"{name}.jsonl").resolve()
    try:
        candidate.relative_to(RUNS_DIR.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def load_events(run: str) -> list[dict] | None:
    """Load the recorded events for ``run`` (None when unknown).

    Malformed lines are skipped (same tolerance as POST /run's loader).
    The returned dicts are the recorded frames verbatim (no replay tag;
    the tag is added at emit time by :func:`iter_schedule`).
    """
    path = resolve_run(run)
    if path is None:
        return None
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "type" in obj:
            events.append(obj)
    return events


def list_runs() -> list[dict[str, Any]]:
    """List replayable runs: ``[{run, events, path}]`` sorted by name."""
    runs: list[dict[str, Any]] = []
    if not RUNS_DIR.is_dir():
        return runs
    for path in sorted(RUNS_DIR.glob(_RUN_GLOB)):
        if path.name.endswith(".asr.jsonl"):
            continue
        events = load_events(path.stem)
        runs.append(
            {
                "run": path.stem,
                "events": len(events) if events is not None else 0,
                "path": str(path),
            }
        )
    return runs


def run_info(run: str) -> dict[str, Any] | None:
    """Metadata for one run (None when unknown)."""
    events = load_events(run)
    if events is None:
        return None
    types: dict[str, int] = {}
    for ev in events:
        t = str(ev.get("type"))
        types[t] = types.get(t, 0) + 1
    stamps = [e["bus_ts"] for e in events if isinstance(e.get("bus_ts"), (int, float))]
    media_end = max(
        (
            e.get("t_end")
            for e in events
            if isinstance(e.get("t_end"), (int, float))
        ),
        default=None,
    )
    return {
        "run": run[:-len(".jsonl")] if run.endswith(".jsonl") else run,
        "events": len(events),
        "types": types,
        "span_s": (max(stamps) - min(stamps)) if len(stamps) >= 2 else 0.0,
        "media_end_s": media_end,
    }


def iter_schedule(
    events: list[dict],
    rate: float = 1.0,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
) -> Iterator[tuple[float, dict]]:
    """Yield ``(delay_s, frame)`` for each event in log order.

    ``delay_s`` is the wait BEFORE emitting the frame: the recorded
    ``bus_ts`` gap to the previous event divided by ``rate``, clamped
    to ``[0, max_gap_s]``. The first frame has delay 0. Each frame is a
    shallow copy of the recorded event plus ``\"replay\": True``.
    """
    if not (rate > 0):
        raise ValueError(f"rate must be > 0, got {rate!r}")
    prev_ts: float | None = None
    for ev in events:
        ts = ev.get("bus_ts")
        if prev_ts is None or not isinstance(ts, (int, float)):
            delay = 0.0
        else:
            delay = max(0.0, (float(ts) - prev_ts) / rate)
            delay = min(delay, max_gap_s)
        if isinstance(ts, (int, float)):
            prev_ts = float(ts)
        frame = dict(ev)
        frame["replay"] = True
        yield (delay, frame)
