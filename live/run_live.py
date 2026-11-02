"""Atlas Live M1e — end-to-end live runner (REAL asyncio pipeline, no mocks).

Pipeline (every stage is the production piece, wired together)::

    simstream.restream_to (ffmpeg -re, labeled "simulated-stream")
      -- audio-only Matroska into a fifo --> ffmpeg demux (PCM s16le
         mono 24 kHz; MKV because the static build segfaults demuxing
         MPEG-TS — see FifoStream)
      -- PCM chunks --> live.oido.asr_ws.transcribe_stream (Meta realtime WS)
      -- transcript.final --> Detector.process_turn
      --> EventBus: transcript.final + claim.detected + claim.state(escuchada)
      --> live.server app re-emits every bus event over WS /ws

The bus log (``--log run.jsonl``) keeps the flat server envelope
(``{seq, bus_ts, type, ...}`` per ``live/UI_CONTRACT.md``) and is the
replay base for M4. Run metadata (latency, cost, counts) goes to a
sibling ``<log>.summary.json`` — never into the replayable log.

Latency model: media clock (ASR ``t_end``) is mapped to wall clock via
``t0_wall`` (first PCM byte ~= media 0) under ``-re`` pacing, so
``t_end -> bus`` latency is ``claim.detected.bus_ts - (t0_wall + t_end)``.
``fin-frase -> tarjeta-WS`` is measured by a local WS subscriber inside
this script (join key ``claim_id``), i.e. what a browser card would see
minus paint time.

Cost model (measured, not estimated): ASR = floor(audio_s)/3600*$0.18
(per ``live/oido/CONTRACT.md``); model detector = summed ``last_usage``
tokens through ``src.atlas.agent.loop.estimate_cost``.

Usage (repo root on sys.path; MODEL_API_KEY from env — source .env)::

    .venv/bin/python live/run_live.py --video data/keynote/keynote_en.mp4
    .venv/bin/python live/run_live.py --detector model --duration 60
    .venv/bin/python live/run_live.py --no-server --log data/runs/run.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live.detector.claims import Detector  # noqa: E402
from live.ingest.demux import BYTES_PER_SEC, ffmpeg_exe, probe_streams  # noqa: E402
from live.ingest.simstream import SOURCE_LABEL, restream_to  # noqa: E402
from live.oido.asr_ws import ASRConfig, transcribe_stream  # noqa: E402
from live.orchestrator.bus import EventBus  # noqa: E402
from live.orchestrator.states import ClaimTracker  # noqa: E402
from live.server.app import create_app, percentile  # noqa: E402

ASR_USD_PER_HOUR = 0.18
DEFAULT_PORT = 8765
DEFAULT_VIDEO = os.path.join("data", "keynote", "keynote_en.mp4")
TRUTH_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "material", "truth.json"
)


# --------------------------------------------------------------------------
# Pure helpers (also used by live/tests/test_e2e_live.py, 0 live calls)
# --------------------------------------------------------------------------

def flatten_final(ev: dict | None) -> dict | None:
    """ASR ``transcript.final`` -> flat turn dict (server/bus shape).

    Accepts the oido envelope (``{"type","t_wall","t_media","payload"}``)
    and already-flat dicts (offline replay). Anything that is not a
    non-empty final returns None.
    """
    if not isinstance(ev, dict) or ev.get("type") != "transcript.final":
        return None
    payload = ev.get("payload", ev)
    if not isinstance(payload, dict):
        return None
    text = str(payload.get("text", "") or "")
    if not text.strip():
        return None
    return {
        "turn": payload.get("turn"),
        "text": text,
        "speaker": payload.get("speaker"),
        "t_start": payload.get("t_start"),
        "t_end": payload.get("t_end"),
    }


def summarize_ms(samples: list[float]) -> dict:
    """p50/p95 summary over latency samples (already in ms)."""
    return {
        "count": len(samples),
        "p50_ms": percentile(samples, 50),
        "p95_ms": percentile(samples, 95),
    }


def estimate_asr_usd(audio_s: float) -> float:
    """Measured ASR cost: whole seconds floored, $0.18/h streamed."""
    return math.floor(max(0.0, audio_s)) / 3600.0 * ASR_USD_PER_HOUR


def collect_usage(llm, acc: dict) -> dict:
    """Fold one ``MetaClient.last_usage`` into ``acc`` (idempotent per seq)."""
    usage = getattr(llm, "last_usage", None)
    seq = getattr(llm, "last_usage_seq", None)
    if not usage or not seq or seq == acc.get("seq"):
        return acc
    acc["seq"] = seq
    # F2-3: cached_tokens folded too (prompt-cache accounting; 0/absent
    # when the provider omits it — same int guard as the other keys).
    for key in ("prompt_tokens", "completion_tokens", "cached_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            acc[key] = acc.get(key, 0) + value
    return acc


# --------------------------------------------------------------------------
# F2-3 (e): prefetch from PROVISIONAL transcripts (cache warmup, no verdicts)
# --------------------------------------------------------------------------

PREFETCH_MIN_CHARS = 40
PREFETCH_THROTTLE_S = 2.0
PREFETCH_TOP_K = 2


class PrefetchManager:
    """Warm the fetch cache from ``transcript.partial`` turns.

    A partial never decides anything (no detector, no bus events, no
    verdicts): it only searches + fetches the top hits so the later
    ``transcript.final`` investigation finds warm cache entries. A final
    CONFIRMS its turn (warming already banked) and CANCELS prefetches
    still pending for older turns. All failures are swallowed: prefetch
    can only save time, never break the live path.
    """

    def __init__(self, search_fn=None, fetch_fn=None,
                 min_chars: int = PREFETCH_MIN_CHARS,
                 throttle_s: float = PREFETCH_THROTTLE_S,
                 top_k: int = PREFETCH_TOP_K) -> None:
        self._search_fn = search_fn
        self._fetch_fn = fetch_fn
        self._min_chars = min_chars
        self._throttle_s = throttle_s
        self._top_k = top_k
        self._last_launch: dict = {}
        self._pending: dict = {}  # turn -> asyncio.Task
        self.stats = {"partials": 0, "launched": 0, "urls_warmed": 0,
                      "confirmed": 0, "cancelled": 0}

    def _search(self, query: str, top_k: int) -> list:
        if self._search_fn is not None:
            return self._search_fn(query, top_k)
        from live.swarm.coordinator import web_search

        return web_search(query, top_k)

    def _fetch(self, url: str) -> None:
        if self._fetch_fn is not None:
            self._fetch_fn(url)
            return
        from live.swarm.fetch import fetch_url

        fetch_url(url)

    def _warm_sync(self, text: str) -> list[str]:
        """Search + fetch top hits (blocking; runs in a worker thread)."""
        try:
            hits = self._search(text[:300], self._top_k) or []
        except Exception:
            return []
        warmed: list[str] = []
        for hit in hits[:self._top_k]:
            if not isinstance(hit, dict):
                continue
            url = str(hit.get("url", ""))
            if not url.startswith(("http://", "https://")):
                continue
            try:
                self._fetch(url)
            except Exception:
                continue
            warmed.append(url)
        return warmed

    async def on_partial(self, turn, text: str) -> None:
        """Maybe launch a warmup task for a provisional turn."""
        self.stats["partials"] += 1
        clean = str(text or "")
        if len(clean.strip()) < self._min_chars:
            return
        now = time.monotonic()
        if now - self._last_launch.get(turn, 0.0) < self._throttle_s:
            return
        self._last_launch[turn] = now
        old = self._pending.pop(turn, None)
        if old is not None and not old.done():
            old.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _run() -> None:
            try:
                warmed = await loop.run_in_executor(None, self._warm_sync,
                                                    clean)
            except asyncio.CancelledError:
                raise
            except Exception:
                return
            self.stats["urls_warmed"] += len(warmed)

        try:
            self._pending[turn] = loop.create_task(_run())
        except RuntimeError:
            return
        self.stats["launched"] += 1
        self._pending[turn].add_done_callback(
            lambda task, key=turn: self._pending.pop(key, None)
            if self._pending.get(key) is task else None)

    def on_final(self, turn) -> None:
        """Confirm ``turn``; cancel prefetches still pending for others."""
        self.stats["confirmed"] += 1
        for key in [k for k in self._pending if k != turn]:
            task = self._pending.pop(key)
            if not task.done():
                task.cancel()
                self.stats["cancelled"] += 1

    async def close(self) -> None:
        """Cancel every pending warmup (idempotent)."""
        pending = list(self._pending.values())
        self._pending.clear()
        for task in pending:
            if not task.done():
                task.cancel()
        for task in pending:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


async def process_final(
    turn: dict,
    *,
    detector: Detector,
    bus: EventBus,
    tracker: ClaimTracker,
    context: list[dict],
    llm=None,
    usage: dict | None = None,
) -> list[dict]:
    """Publish one final turn + its claims; return enveloped claim.detected.

    Mirrors the ``POST /run`` path in ``live/server/app.py``: flat
    ``transcript.final`` -> detector (in a worker thread: the model path
    is blocking) -> ``claim.detected`` + ``claim.state`` per claim.
    ``context`` keeps the last 3 turns for detector coreference.
    """
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
    claims, _notes = await asyncio.to_thread(
        detector.process_turn, turn, list(context)
    )
    context.append(turn)
    del context[:-3]
    if llm is not None and usage is not None:
        collect_usage(llm, usage)
    detected: list[dict] = []
    for claim in claims:
        ev = await bus.publish(
            {
                "type": "claim.detected",
                "claim_id": claim.claim_id,
                "text": claim.text,
                "resolved": claim.resolved,
                "t_start": claim.t_start,
                "t_end": claim.t_end,
            }
        )
        detected.append(ev)
        await tracker.mark_heard(claim.claim_id, text=claim.text)
    return detected


# --------------------------------------------------------------------------
# simstream -> demux over a fifo (both stages are real ffmpeg processes)
# --------------------------------------------------------------------------

class FifoStream:
    """Paced replay through a fifo into a PCM demuxer.

    ``live.ingest.simstream.restream_to`` (``ffmpeg -re``, labeled
    ``"simulated-stream"``) replays the file into a fifo as audio-only
    Matroska; a second ffmpeg reads the fifo and emits raw s16le mono
    24 kHz PCM on stdout. ``chunks()`` is the async PCM source for
    ``transcribe_stream``. ``t0_wall`` anchors media 0 to wall clock at
    the first PCM byte (for t_end latencies).

    Why Matroska-over-fifo and not the default MPEG-TS-over-stdout:
    the imageio static ffmpeg demuxes MP4/MKV fine but SEGFAULTS
    demuxing MPEG-TS (rc=139, any audio codec), and fragmented-MP4
    muxing buffers the whole replay instead of streaming it. MKV
    flows progressively (first PCM ~= 0.5 s wall). The sim stage is
    still simstream.py with ``-re``; only the restream container is
    overridden via its ``extra_args`` seam.
    """

    FIRST_BYTE_TIMEOUT_S = 30.0

    def __init__(
        self,
        video: str,
        chunk_ms: int = 80,
        duration: float | None = None,
    ) -> None:
        self.video = video
        self.chunk_bytes = int(24000 * 2 * chunk_ms / 1000)
        self.duration = duration
        self.source_label = SOURCE_LABEL
        self.t0_wall: float | None = None
        self.restream_extra: tuple[str, ...] = (
            "-y",
            "-map",
            "0:a?",
            "-c",
            "copy",
            "-flush_packets",
            "1",
            "-f",
            "matroska",
        ) + ((("-t", str(duration))) if duration else ())
        self._tmpdir: str | None = None
        self._fifo: str | None = None
        self._sim: subprocess.Popen | None = None
        self._proc: subprocess.Popen | None = None
        self._pump: threading.Thread | None = None
        self._err: bytearray = bytearray()
        self._sent_bytes = 0

    @staticmethod
    def demux_argv_for(input_path: str) -> list[str]:
        """PCM demux argv (audio twin of ``Demuxer`` for any path/fifo)."""
        return [
            ffmpeg_exe(),
            "-hide_banner",
            "-nostats",
            "-i",
            input_path,
            "-map",
            "0:a?",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-f",
            "s16le",
            "-",
        ]

    def start(self) -> "FifoStream":
        """Spawn paced restream + demux, rendezvous on the fifo."""
        import tempfile

        streams = probe_streams(self.video)
        if not streams["audio"]:
            raise RuntimeError(f"no audio stream in {self.video}")
        self._tmpdir = tempfile.mkdtemp(prefix="m1e-fifo")
        self._fifo = os.path.join(self._tmpdir, "live.mkv")
        os.mkfifo(self._fifo)
        self._sim, label = restream_to(
            self.video, self._fifo, extra_args=self.restream_extra
        )
        assert label == SOURCE_LABEL == self.source_label
        self._proc = subprocess.Popen(
            self.demux_argv_for(self._fifo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._pump = threading.Thread(target=self._pump_stderr, daemon=True)
        self._pump.start()
        return self

    def _pump_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        try:
            for chunk in iter(lambda: self._proc.stderr.read(4096), b""):
                self._err.extend(chunk)
                del self._err[:-8192]  # keep the tail only
        except (ValueError, OSError):
            pass

    async def chunks(self):
        """Yield paced PCM chunks until the replay ends (async source)."""
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            if self.t0_wall is None:
                try:
                    data = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._proc.stdout.read, self.chunk_bytes
                        ),
                        timeout=self.FIRST_BYTE_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    raise RuntimeError(
                        "no PCM in "
                        f"{self.FIRST_BYTE_TIMEOUT_S:.0f}s "
                        f"(sim={self._poll(self._sim)} "
                        f"demux={self._poll(self._proc)} "
                        f"err={self.stderr_tail[-300:]!r})"
                    )
            else:
                data = await asyncio.to_thread(
                    self._proc.stdout.read, self.chunk_bytes
                )
            if not data:
                return
            if self.t0_wall is None:
                # First byte ~= media 0: back off this chunk's duration.
                self.t0_wall = time.time() - len(data) / BYTES_PER_SEC
            self._sent_bytes += len(data)
            yield data

    @staticmethod
    def _poll(proc: subprocess.Popen | None) -> int | None | str:
        try:
            return proc.poll() if proc is not None else "missing"
        except Exception:
            return "?"

    @property
    def media_seconds(self) -> float:
        """PCM seconds emitted so far (media clock)."""
        return self._sent_bytes / BYTES_PER_SEC

    @property
    def stderr_tail(self) -> str:
        """Last demux stderr bytes (for failure diagnosis)."""
        return bytes(self._err[-2000:]).decode("utf-8", "replace")

    def wait(self, timeout_s: float = 60.0) -> tuple[int | None, int | None]:
        """Wait for both ffmpeg processes; return (sim, demux) exit codes."""
        sim_code = demux_code = None
        try:
            if self._sim is not None:
                sim_code = self._sim.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            pass
        try:
            if self._proc is not None:
                demux_code = self._proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            pass
        finally:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.kill()
        if self._pump is not None:
            self._pump.join(timeout=5)
        return sim_code, demux_code

    def close(self) -> None:
        """Terminate both ffmpeg processes; remove the fifo (idempotent)."""
        for proc in (self._sim, self._proc):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if self._fifo is not None:
            try:
                os.unlink(self._fifo)
            except OSError:
                pass
            self._fifo = None
        if self._tmpdir is not None:
            try:
                os.rmdir(self._tmpdir)
            except OSError:
                pass
            self._tmpdir = None


# --------------------------------------------------------------------------
# In-process server + local WS subscriber (fin-frase -> tarjeta-WS latency)
# --------------------------------------------------------------------------

def _probe_health(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=2
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


async def serve_in_background(app, port: int):
    """Run the live.server app on 127.0.0.1; return (server, task)."""
    import uvicorn

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(150):
        if await asyncio.to_thread(_probe_health, port):
            return server, task
        if task.done():
            break
        await asyncio.sleep(0.1)
    server.should_exit = True
    raise RuntimeError(f"live server did not come up on port {port}")


async def ws_subscriber(
    port: int, arrivals: dict, stop: asyncio.Event
) -> None:
    """Local /ws client: record wall arrival per claim.detected (by id)."""
    from websockets.asyncio.client import connect

    async with connect(f"ws://127.0.0.1:{port}/ws") as ws:
        await ws.recv()  # hello frame
        while not stop.is_set():
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            now = time.time()
            try:
                ev = json.loads(msg)
            except (json.JSONDecodeError, TypeError):
                continue
            if ev.get("type") == "claim.detected" and ev.get("claim_id"):
                arrivals.setdefault(ev["claim_id"], now)


def truth_counts(path: str = TRUTH_PATH, lang: str = "en") -> dict:
    """Scripted-claim counts (for N-detected-vs-truth; never verdicts)."""
    try:
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"path": path, "lang": lang, "total": None, "checkable": None}
    rows = [r for r in rows if r.get("lang") == lang]
    checkable = sum(1 for r in rows if r.get("expected") in ("true", "false"))
    return {"path": path, "lang": lang, "total": len(rows),
            "checkable": checkable}


def default_log_path() -> str:
    """data/runs/run-<UTC-timestamp>.jsonl (created on --log default)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return os.path.join("data", "runs", f"run-{stamp}.jsonl")


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """CLI flags for the M1e live run."""
    parser = argparse.ArgumentParser(
        description="Atlas Live M1e: simstream -> demux -> ASR -> "
        "detector -> bus -> WS, with latency + cost measurement."
    )
    parser.add_argument("--video", default=DEFAULT_VIDEO,
                        help=f"input video (default: {DEFAULT_VIDEO})")
    parser.add_argument("--detector", choices=("model", "heuristic"),
                        default="heuristic",
                        help="claim detector path (default: heuristic)")
    parser.add_argument("--no-server", action="store_true",
                        help="bus + run log only; no HTTP/WS server, "
                        "no WS card latency")
    parser.add_argument("--log", default=None,
                        help="bus event log (default: data/runs/run-<ts>.jsonl)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"live server port (default: {DEFAULT_PORT})")
    parser.add_argument("--duration", type=float, default=None,
                        help="limit the replay to N media seconds "
                        "(short cost-capped clips)")
    parser.add_argument("--chunk-ms", type=int, default=80,
                        help="PCM/ASR frame size in ms (default: 80)")
    parser.add_argument("--keywords", default="",
                        help="comma-separated ASR keyword hints")
    parser.add_argument("--language-bias", default="",
                        help="comma-separated ASR language bias "
                        '(full names, e.g. "English")')
    parser.add_argument("--api-key", default="",
                        help="Meta API key (default: MODEL_API_KEY env)")
    parser.add_argument("--record", default=None,
                        help="optional ASR wire-record jsonl path")
    return parser


async def amain(args: argparse.Namespace) -> dict:
    """Run the live pipeline; return the JSON-serializable summary."""
    api_key = args.api_key or os.environ.get("MODEL_API_KEY", "")
    if not api_key:
        # Presence only — never print the key or its length here.
        raise SystemExit("MODEL_API_KEY not configured; source .env first.")
    if not os.path.isfile(args.video):
        raise SystemExit(f"video not found: {args.video}")

    log_path = args.log or default_log_path()
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)

    llm = None
    if args.detector == "model":
        from src.atlas.models.meta import MetaClient

        llm = MetaClient(api_key=api_key)
        if not llm.available:
            raise SystemExit("model detector needs MODEL_API_KEY.")

    with_server = not args.no_server
    if with_server:
        app = create_app(run_log=log_path, llm=llm)
        bus: EventBus = app.state.bus
        tracker: ClaimTracker = app.state.tracker
        detector: Detector = app.state.detector
        context: list[dict] = app.state.context
    else:
        app = None
        bus = EventBus(run_log=log_path)
        tracker = ClaimTracker(bus)
        detector = Detector(llm=llm)
        context = []

    server = server_task = sub_task = None
    arrivals: dict[str, float] = {}
    stop = asyncio.Event()
    if with_server:
        server, server_task = await serve_in_background(app, args.port)
        sub_task = asyncio.create_task(ws_subscriber(args.port, arrivals, stop))

    demux = FifoStream(args.video, chunk_ms=args.chunk_ms,
                       duration=args.duration).start()
    print(f"[m1e] source={demux.source_label} video={args.video} "
          f"detector={args.detector} server={'on' if with_server else 'off'} "
          f"log={log_path}", flush=True)

    state = {
        "partials": 0, "finals": 0,
        "lat_bus_ms": [], "claim_t_end": {}, "claim_bus_ts": {},
        "usage": {},
    }
    prefetch = PrefetchManager()

    async def on_asr_event(ev: dict) -> None:
        etype = ev.get("type")
        if etype == "transcript.partial":
            state["partials"] += 1
            # F2-3 (e): provisional text warms the fetch cache; the
            # final below confirms/cancels. Never raises into ASR.
            try:
                payload = ev.get("payload", ev)
                if isinstance(payload, dict):
                    await prefetch.on_partial(
                        payload.get("turn"), payload.get("text", ""))
            except Exception:
                pass
            return
        turn = flatten_final(ev)
        if turn is None:
            return
        state["finals"] += 1
        try:
            prefetch.on_final(turn.get("turn"))
        except Exception:
            pass
        print(f"[m1e] final turn={turn.get('turn')} "
              f"t=[{turn.get('t_start')},{turn.get('t_end')}] "
              f"{str(turn.get('text'))[:90]}", flush=True)
        detected = await process_final(
            turn, detector=detector, bus=bus, tracker=tracker,
            context=context, llm=llm, usage=state["usage"],
        )
        t0 = demux.t0_wall
        for dev in detected:
            cid = dev["claim_id"]
            state["claim_t_end"][cid] = dev.get("t_end")
            state["claim_bus_ts"][cid] = dev["bus_ts"]
            if t0 is not None and isinstance(dev.get("t_end"), (int, float)):
                state["lat_bus_ms"].append(
                    (dev["bus_ts"] - (t0 + dev["t_end"])) * 1000.0
                )
            print(f"[m1e] claim {cid} resolved={dev.get('resolved')!r} "
                  f"text={dev.get('text')[:80]!r}", flush=True)

    keywords = tuple(k.strip() for k in args.keywords.split(",") if k.strip())
    lang_bias = tuple(k.strip() for k in args.language_bias.split(",")
                      if k.strip())
    config = ASRConfig(api_key=api_key, chunk_ms=args.chunk_ms,
                       keywords=keywords, language_bias=lang_bias)
    wall_start = time.time()
    sessions = []
    try:
        sessions = await transcribe_stream(
            config, demux.chunks(), on_event=on_asr_event,
            record_path=args.record,
        )
    finally:
        sim_code, demux_code = await asyncio.to_thread(demux.wait)
        await asyncio.sleep(2.5 if with_server else 0.0)  # drain WS fan-out
        try:
            await prefetch.close()
        except Exception:
            pass
        stop.set()
        if sub_task is not None:
            await sub_task
        if server is not None and server_task is not None:
            server.should_exit = True
            await server_task
        await bus.close()
        demux.close()
    wall_s = time.time() - wall_start

    audio_s = sum(s.audio_sent_s for s in sessions)
    asr_usd = estimate_asr_usd(audio_s)
    model_usd = 0.0
    prompt_tokens = state["usage"].get("prompt_tokens", 0)
    completion_tokens = state["usage"].get("completion_tokens", 0)
    if args.detector == "model" and (prompt_tokens or completion_tokens):
        from src.atlas.agent.loop import estimate_cost

        model_usd = estimate_cost(prompt_tokens, completion_tokens)

    lat_ws_ms: list[float] = []
    fanout_ms: list[float] = []
    t0 = demux.t0_wall
    if with_server and t0 is not None:
        for cid, recv_ts in arrivals.items():
            t_end = state["claim_t_end"].get(cid)
            if isinstance(t_end, (int, float)):
                lat_ws_ms.append((recv_ts - (t0 + t_end)) * 1000.0)
            bus_ts = state["claim_bus_ts"].get(cid)
            if bus_ts is not None:
                fanout_ms.append((recv_ts - bus_ts) * 1000.0)

    summary = {
        "video": args.video,
        "source": demux.source_label,
        "detector": args.detector,
        "server": with_server,
        "port": args.port if with_server else None,
        "duration_limit_s": args.duration,
        "wall_s": round(wall_s, 1),
        "media_s": round(demux.media_seconds, 1),
        "audio_sent_s": round(audio_s, 1),
        "asr_outcomes": [s.outcome for s in sessions],
        "asr_partials": state["partials"],
        "turns_final": state["finals"],
        "claims_n": len(state["claim_t_end"]),
        "claim_ids": sorted(state["claim_t_end"]),
        "lat_bus_ms": summarize_ms(state["lat_bus_ms"]),
        "lat_ws_ms": summarize_ms(lat_ws_ms) if with_server else None,
        "fanout_ms": summarize_ms(fanout_ms) if with_server else None,
        "asr_usd": round(asr_usd, 4),
        "model_prompt_tokens": prompt_tokens,
        "model_completion_tokens": completion_tokens,
        "model_cached_tokens": state["usage"].get("cached_tokens", 0),
        "prefetch": dict(prefetch.stats),
        "model_usd": round(model_usd, 4),
        "total_usd": round(asr_usd + model_usd, 4),
        "truth": truth_counts(),
        "ffmpeg": {"simstream_code": sim_code, "demux_code": demux_code,
                   "demux_stderr_tail": demux.stderr_tail[-500:]},
        "run_log": log_path,
        "record_path": args.record,
    }
    summary_path = log_path + ".summary.json"
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    summary["summary_path"] = summary_path
    print(f"[m1e] turns={state['finals']} claims={len(state['claim_t_end'])} "
          f"bus_p50/p95={summary['lat_bus_ms']['p50_ms']}/"
          f"{summary['lat_bus_ms']['p95_ms']}ms "
          + (f"ws_p50/p95={summary['lat_ws_ms']['p50_ms']}/"
               f"{summary['lat_ws_ms']['p95_ms']}ms "
               if with_server else "")
          + f"usd={summary['total_usd']}", flush=True)
    print(f"[m1e] summary: {summary_path}", flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint (argv injectable for tests)."""
    args = build_parser().parse_args(argv)
    asyncio.run(amain(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
