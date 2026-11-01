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

Latency measured here is pipeline latency: ``transcript.final``
publish -> ``claim.detected`` publish per claim, in ms of wall
clock. Media-clock latency (sentence end -> card on screen) is
measured by the M1c E2E harness against this server's event log.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from live.detector.claims import ClaimCandidate, Detector
from live.orchestrator.bus import EventBus
from live.orchestrator.states import ClaimTracker

log = logging.getLogger(__name__)

UI_PATH = Path(__file__).resolve().parent.parent / "ui" / "index.html"

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

    async def _broadcast(event: dict) -> None:
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

    @app.websocket("/ws")
    async def ws_feed(websocket: WebSocket) -> None:
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

    return app


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
