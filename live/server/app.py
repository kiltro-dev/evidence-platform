"""M1b live server: serves the newsroom UI, re-emits the bus over WS.

Routes:
- ``GET /`` — the newsroom page (``live/ui/index.html``).
- ``GET /health`` — liveness probe.
- ``GET /metrics`` — run counters + end-phrase -> card latency p50/p95.
- ``WS /ws`` — every bus event, as JSON, to all connected clients.
- ``POST /run`` — replay a prerecorded ``transcript.jsonl`` (list of
  ``{turn, text, speaker, t_start, t_end}`` turns, inline or via
  ``transcript_path``) through detector -> bus. The video-fed M1a
  pipeline runs separately and plugs into the same bus; the E2E
  player connects in M1c.
- ``GET /replay`` / ``GET /replay/{run}`` (M4) — list recorded runs
  and describe one (event counts, span, demo video URL).
- ``WS /ws?replay=<run>&rate=<x>`` (M4) — re-emit a recorded
  ``data/runs/*.jsonl`` log with original ``bus_ts`` pacing divided
  by ``rate``; every frame carries ``replay:true`` and the stream
  ends with ``replay.done``. Plain ``/ws`` is unchanged (live bus).
- ``GET /media/{name}`` (M4) — keynote demo mp4s for the player
  (strict allowlist; the 4.4–5.7 MB repo-local files).
- ``GET /hls/{name}`` (S3) — simulive HLS playlist + segments
  (strict allowlist; playlist served with X-ATLAS-SIMULIVE metadata).
- ``GET /hls-status`` (S3) — simulive window state (seq, uptime).

Latency measured here is pipeline latency: ``transcript.final``
publish -> ``claim.detected`` publish per claim, in ms of wall
clock. Media-clock latency (sentence end -> card on screen) is
measured by the M1c E2E harness against this server's event log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from live.detector.claims import ClaimCandidate, Detector
from live.orchestrator.bus import EventBus
from live.orchestrator.states import ClaimTracker
from live.replay.player import (
    DEFAULT_MAX_GAP_S,
    iter_schedule,
    list_runs,
    load_events,
    run_info,
)
from live.simulive.serve import mount_simulive

log = logging.getLogger(__name__)

UI_PATH = Path(__file__).resolve().parent.parent / "ui" / "index.html"
MEDIA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "keynote"
MEDIA_ALLOW = {"keynote_en.mp4", "keynote_es.mp4"}

# Cap on turns per /run so a bad upload cannot wedge the demo server.
MAX_RUN_TURNS = 500


def percentile(samples: list[float], pct: float) -> float | None:
    """Nearest-rank percentile (pct in 0..100); None when empty."""
    if not samples:
        return None
    ordered = sorted(samples)
    rank = min(len(ordered) - 1, max(0, int((pct / 100) * len(ordered))))
    return ordered[rank]


def latency_summary(samples: list[float]) -> dict[str, Any]:
    """p50/p95 summary over latency samples (ms)."""
    return {
        "count": len(samples),
        "p50_ms": percentile(samples, 50),
        "p95_ms": percentile(samples, 95),
    }


def create_app(
    run_log: str | Path | None = None,
    llm: Any | None = None,
) -> FastAPI:
    """Build the app. ``llm`` (optional) drives the model detector path."""
    app = FastAPI(title="Atlas Live (M1b)")
    app.state.bus = EventBus(run_log=run_log)
    app.state.tracker = ClaimTracker(app.state.bus)
    app.state.detector = Detector(llm=llm)
    app.state.latencies: list[float] = []
    app.state.sockets: set[WebSocket] = set()
    app.state.context: list[dict] = []  # last turns, for coreference
    # F2-3: per-type event counts (early_exit/progress telemetry flows
    # through the same broadcast; keys appear on first sight).
    app.state.event_counts: dict[str, int] = {}

    async def _broadcast(event: dict) -> None:
        try:
            etype = str(event.get("type", "?"))
            counts = app.state.event_counts
            counts[etype] = counts.get(etype, 0) + 1
        except Exception:
            pass
        dead: list[WebSocket] = []
        line = json.dumps(event, ensure_ascii=False)
        for ws in app.state.sockets:
            try:
                await ws.send_text(line)
            except Exception:  # noqa: BLE001 - drop dead sockets quietly
                dead.append(ws)
        for ws in dead:
            app.state.sockets.discard(ws)

    app.state.bus.subscribe(None, _broadcast)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "milestone": "m1b"}

    @app.get("/metrics")
    async def metrics() -> dict:
        bus = app.state.bus
        return {
            "events": len(bus.history()),
            "latency_ms": latency_summary(app.state.latencies),
            "events_by_type": dict(app.state.event_counts),
        }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        try:
            html = UI_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return HTMLResponse(
                "<h1>Atlas Live</h1><p>UI not built yet (live/ui/index.html).</p>",
                status_code=503,
            )
        return HTMLResponse(html)

    @app.get("/replay")
    async def replay_list() -> dict:
        """List recorded runs available for replay."""
        return {"runs": list_runs()}

    @app.get("/replay/{run}")
    async def replay_get(run: str) -> JSONResponse:
        """Metadata for one recorded run (event counts, span, video)."""
        info = run_info(run)
        if info is None:
            return JSONResponse({"error": f"unknown run {run!r}"}, status_code=404)
        info["video"] = _video_for_run(info["run"])
        info["max_gap_s"] = DEFAULT_MAX_GAP_S
        return JSONResponse(info)

    @app.get("/media/{name}", response_model=None)
    async def media(name: str):
        """Serve keynote demo videos for the <video> player (allowlist)."""
        if name not in MEDIA_ALLOW:
            return JSONResponse({"error": f"unknown media {name!r}"}, status_code=404)
        path = MEDIA_DIR / name
        if not path.is_file():
            return JSONResponse({"error": f"missing media {name!r}"}, status_code=404)
        return FileResponse(path, media_type="video/mp4", filename=name)

    @app.websocket("/ws")
    async def ws_feed(
        websocket: WebSocket,
        replay: str | None = None,
        rate: float = 1.0,
    ) -> None:
        if replay:
            await _ws_replay(websocket, replay, rate)
            return
        await websocket.accept()
        app.state.sockets.add(websocket)
        try:
            await websocket.send_text(
                json.dumps({"type": "hello", "milestone": "m1b"})
            )
            while True:
                # Client -> server messages are ignored (PINGs welcome);
                # the feed is server -> client only.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            app.state.sockets.discard(websocket)

    @app.post("/run")
    async def run(payload: dict) -> JSONResponse:
        """Replay prerecorded transcript turns through detector -> bus."""
        turns = _load_turns(payload)
        if turns is None:
            return JSONResponse(
                {
                    "error": "payload needs 'transcript' (list of turns) "
                    "or 'transcript_path' (jsonl file)"
                },
                status_code=400,
            )
        if len(turns) > MAX_RUN_TURNS:
            return JSONResponse(
                {"error": f"too many turns (>{MAX_RUN_TURNS})"}, status_code=400
            )
        bus: EventBus = app.state.bus
        tracker: ClaimTracker = app.state.tracker
        detector: Detector = app.state.detector
        emitted = 0
        for turn in turns:
            if not isinstance(turn, dict) or not str(turn.get("text", "")).strip():
                continue
            t0 = time.perf_counter()
            await bus.publish(
                {
                    "type": "transcript.final",
                    "turn": turn.get("turn"),
                    "text": str(turn.get("text")),
                    "speaker": turn.get("speaker"),
                    "t_start": turn.get("t_start"),
                    "t_end": turn.get("t_end"),
                }
            )
            claims, _notes = detector.process_turn(turn, app.state.context)
            app.state.context.append(turn)
            app.state.context = app.state.context[-3:]
            for claim in claims:
                await _emit_claim(bus, tracker, claim)
                app.state.latencies.append((time.perf_counter() - t0) * 1000)
                emitted += 1
        return JSONResponse(
            {
                "turns": len(turns),
                "claims": emitted,
                "latency_ms": latency_summary(app.state.latencies),
            }
        )

    # S3 simulive: additive routes only (/hls/*, /hls-status);
    # M1b/M4 routes, WS contract and /run behavior untouched.
    mount_simulive(app)

    return app


def _video_for_run(run: str) -> str | None:
    """Demo video URL for a run (keynote runs -> keynote mp4)."""
    if "model-60s" in run or "heuristic" in run or run.startswith("run-m1"):
        name = "keynote_en.mp4"
        if (MEDIA_DIR / name).is_file():
            return f"/media/{name}"
    return None


async def _ws_replay(websocket: WebSocket, run: str, rate: float) -> None:
    """Serve one recorded run over WS with original pacing (/rate).

    First frame is ``hello`` (with ``replay``/``rate``), then the
    recorded events verbatim + ``replay:true``, then ``replay.done``.
    Pacing sleeps double as the client-disconnect watch: incoming
    frames are ignored per the /ws contract, a close ends the stream.
    """
    await websocket.accept()
    if not (rate > 0):
        await websocket.send_text(
            json.dumps({"type": "error", "error": f"bad rate {rate!r}"})
        )
        await websocket.close(code=4400)
        return
    events = load_events(run)
    if events is None:
        await websocket.send_text(
            json.dumps({"type": "error", "error": f"unknown run {run!r}"})
        )
        await websocket.close(code=4404)
        return
    stem = run[:-len(".jsonl")] if run.endswith(".jsonl") else run
    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "milestone": "m1b",
                    "mode": "replay",
                    "replay": stem,
                    "rate": rate,
                    "events": len(events),
                }
            )
        )
        for delay, frame in iter_schedule(events, rate=rate):
            # Pacing wait that also drains (ignored) client frames
            # and notices disconnects promptly (contract: client frames
            # never affect the feed, they just don't shorten the wait).
            deadline = time.monotonic() + delay
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(
                        websocket.receive_text(), timeout=remaining
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    break
            try:
                await websocket.send_text(json.dumps(frame, ensure_ascii=False))
            except (WebSocketDisconnect, RuntimeError):
                return
        await websocket.send_text(
            json.dumps(
                {
                    "type": "replay.done",
                    "replay": True,
                    "run": stem,
                    "events": len(events),
                }
            )
        )
        while True:
            await websocket.receive_text()  # stay open; client frames ignored
    except WebSocketDisconnect:
        pass


def _load_turns(payload: dict) -> list[dict] | None:
    if isinstance(payload.get("transcript"), list):
        return [t for t in payload["transcript"] if isinstance(t, dict)]
    path = payload.get("transcript_path")
    if isinstance(path, str) and path:
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        turns = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                turns.append(obj)
        return turns
    return None


async def _emit_claim(bus: EventBus, tracker: ClaimTracker, claim: ClaimCandidate) -> None:
    await bus.publish(
        {
            "type": "claim.detected",
            "claim_id": claim.claim_id,
            "text": claim.text,
            "resolved": claim.resolved,
            "t_start": claim.t_start,
            "t_end": claim.t_end,
        }
    )
    await tracker.mark_heard(claim.claim_id, text=claim.text)


# `uvicorn live.server.app:app` entrypoint (repo root on sys.path).
app = create_app()
