"""Asyncio client for the muse-voice-transcribe realtime WebSocket.

Verified contract (LIVE-S1 spike, see ``live/oido/CONTRACT.md``):

- Handshake: first text frame ``{mode, authorization.accessToken,
  audioEncoding: PCM_24KHZ, model, partialMode: CUMULATIVE,
  emitAudioProgress: false, keywords?, languageBias?}`` with
  ``Bearer <key>``. Ack is ``{"sessionId": ...}`` (no ``type``).
- ENDPOINTING/DIARIZATION turns: ``speechStart{turnId}`` -> cumulative
  ``transcript{final:false}`` partials (NO turnId) -> ``speaker{label}``
  (DIARIZATION, no turnId, one per turn at turn end) ->
  ``speechEnd{turnId}`` -> ``speechComplete{turnId,transcript}``.
- Session-final ``transcript{final:true}`` arrives EMPTY — text lives
  only in ``speechComplete``. (PUSH_TO_TALK differs: final:true WITH
  text, no speech events; handled too.)
- Keep sending PCM (silence if idle); never stop without ``endStream``.
- Server closes 1000 "No more transcript segments" after endStream.
- Sessions run up to 60 min with no resume token: reconnect proactively
  (~55 min) and rejoin turns by cumulative ``audioProcessedMs`` offset.

Bus envelope (shared with M1b): ``{"type","t_wall","t_media","payload"}``.
Types emitted here: ``transcript.partial{turn,text}``,
``transcript.final{turn,text,speaker,t_start,t_end}``.
"""

from __future__ import annotations

import asyncio
import json
import time
import wave
from dataclasses import dataclass, field

WS_URL = "wss://api.meta.ai/v1/asr/realtime"
ASR_MODEL = "muse-voice-transcribe-1.0"
SAMPLE_RATE = 24000
CHUNK_MS = 80
CHUNK_BYTES = int(SAMPLE_RATE * 2 * CHUNK_MS / 1000)
BYTES_PER_SEC = SAMPLE_RATE * 2


def bus_event(etype: str, t_media: float | None, payload: dict) -> dict:
    """Shared bus envelope: {"type","t_wall","t_media","payload"}."""
    return {
        "type": etype,
        "t_wall": time.time(),
        "t_media": t_media,
        "payload": payload,
    }


def partial_event(turn: int, text: str, t_media: float) -> dict:
    """transcript.partial{turn,text} bus event."""
    return bus_event("transcript.partial", t_media, {"turn": turn, "text": text})


def final_event(
    turn: int,
    text: str,
    speaker: str | None,
    t_start: float | None,
    t_end: float | None,
) -> dict:
    """transcript.final{turn,text,speaker,t_start,t_end} bus event."""
    return bus_event(
        "transcript.final",
        t_end,
        {
            "turn": turn,
            "text": text,
            "speaker": speaker,
            "t_start": t_start,
            "t_end": t_end,
        },
    )


@dataclass
class ASRConfig:
    """Fixed-per-session ASR settings (handshake + client behavior)."""

    api_key: str = ""
    url: str = WS_URL
    model: str = ASR_MODEL
    mode: str = "DIARIZATION"
    keywords: tuple[str, ...] = ()
    language_bias: tuple[str, ...] = ()
    chunk_ms: int = CHUNK_MS
    partial_mode: str = "CUMULATIVE"
    emit_audio_progress: bool = False
    reconnect_at_min: float = 55.0
    drain_timeout_s: float = 15.0
    connect_timeout_s: float = 30.0
    max_reconnects: int = 5

    @property
    def chunk_bytes(self) -> int:
        """PCM bytes per binary frame."""
        return int(SAMPLE_RATE * 2 * self.chunk_ms / 1000)


def build_handshake(config: ASRConfig) -> dict:
    """First text frame of a session (fixed for its lifetime)."""
    hs: dict = {
        "mode": config.mode,
        "authorization": {"accessToken": f"Bearer {config.api_key}"},
        "audioEncoding": "PCM_24KHZ",
        "model": config.model,
        "partialMode": config.partial_mode,
        "emitAudioProgress": config.emit_audio_progress,
    }
    if config.keywords:
        hs["keywords"] = list(config.keywords)
    if config.language_bias:
        hs["languageBias"] = list(config.language_bias)
    return hs


def _redact_handshake(hs: dict) -> dict:
    out = dict(hs)
    out["authorization"] = {"accessToken": "***REDACTED***"}
    return out


class TurnTracker:
    """Pure server-event -> bus-event parser (offline-replayable).

    Feed parsed server JSON dicts via :meth:`feed`; it returns bus events.
    ``offset_ms`` rejoins turns across reconnects (cumulative audio ms
    sent in earlier sessions of the same stream).
    """

    def __init__(self, offset_ms: int = 0) -> None:
        self.offset_ms = offset_ms
        self.turns: dict[int, dict] = {}
        self.current: int | None = None
        self.unknown: list[dict] = []  # error/unknown server messages

    def _media_s(self, audio_processed_ms: int) -> float:
        return (self.offset_ms + audio_processed_ms) / 1000.0

    def feed(self, msg: dict, t_wall: float | None = None) -> list[dict]:
        """Parse one server message into zero or more bus events."""
        if t_wall is None:
            t_wall = time.time()
        mtype = msg.get("type")
        ap = msg.get("audioProcessedMs", 0) or 0
        if mtype == "speechStart":
            tid = int(msg["turnId"])
            self.turns.setdefault(tid, {})["start_ms"] = ap
            self.current = tid
            return []
        if mtype == "transcript":
            text = msg.get("transcript", "") or ""
            if msg.get("final"):
                if not text.strip():
                    return []  # session-final empty marker: NOT text
                tid = msg.get("turnId", self.current)
                if tid is None:
                    tid = 0
                tid = int(tid)
                st = self.turns.get(tid, {})
                ev = final_event(
                    tid,
                    text.strip(),
                    st.get("speaker"),
                    self._media_s(st["start_ms"]) if "start_ms" in st else None,
                    self._media_s(ap),
                )
                ev["t_wall"] = t_wall
                return [ev]
            ev = partial_event(
                self.current if self.current is not None else 0,
                text,
                self._media_s(ap),
            )
            ev["t_wall"] = t_wall
            return [ev]
        if mtype == "speaker":
            # No turnId: attribute to the currently open turn.
            tid = self.current if self.current is not None else 0
            self.turns.setdefault(tid, {})["speaker"] = msg.get("label")
            return []
        if mtype == "speechEnd":
            tid = int(msg["turnId"])
            self.turns.setdefault(tid, {})["end_ms"] = ap
            return []
        if mtype == "speechComplete":
            tid = int(msg["turnId"])
            st = self.turns.setdefault(tid, {})
            start = st.get("start_ms")
            end = st.get("end_ms", ap)
            ev = final_event(
                tid,
                (msg.get("transcript", "") or "").strip(),
                st.get("speaker"),
                self._media_s(start) if start is not None else None,
                self._media_s(end),
            )
            ev["t_wall"] = t_wall
            return [ev]
        # Handshake ack ({"sessionId": ...}, no "type"), errors, unknowns.
        self.unknown.append(msg)
        return []


@dataclass
class SessionStats:
    """Per-session observations (latency, volume, close)."""

    session_id: str = ""
    close_code: int | None = None
    close_reason: str = ""
    audio_sent_s: float = 0.0
    silence_sent_s: float = 0.0
    partials: int = 0
    finals: int = 0
    first_partial_s: float | None = None  # wall: connect -> first partial
    end_to_complete_ms: dict = field(default_factory=dict)  # turn -> ms
    outcome: str = ""


class PCMFeed:
    """Bridges an async PCM iterator to timeout-safe queued reads.

    ``None`` from :meth:`get` means end of audio (caller sends endStream).
    """

    def __init__(self, source) -> None:
        self._source = source
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._eof = False

    async def start(self) -> None:
        """Spawn the feeder task."""

        async def _feed() -> None:
            try:
                async for chunk in self._source:
                    if chunk:
                        await self._queue.put(bytes(chunk))
            finally:
                await self._queue.put(None)

        self._task = asyncio.create_task(_feed())

    async def get(self, timeout: float) -> bytes | None:
        """Next audio chunk, or None on timeout/EOF (timeout != EOF)."""
        if self._eof:
            return None
        try:
            item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return b""  # starved: caller sends silence, session stays alive
        if item is None:
            self._eof = True
            return None
        return item

    async def close(self) -> None:
        """Stop the feeder task."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


async def pcm_chunks_from_wav(path: str, chunk_ms: int = CHUNK_MS):
    """Async PCM chunk iterator over a mono s16 24 kHz WAV file."""
    n = int(SAMPLE_RATE * 2 * chunk_ms / 1000)
    with wave.open(path, "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, SAMPLE_RATE):
            raise ValueError(f"need mono s16 {SAMPLE_RATE}Hz WAV: {path}")
        while True:
            data = w.readframes(n // 2)
            if not data:
                return
            yield data


class _Recorder:
    """Append-only jsonl event log (handshake token always redacted)."""

    def __init__(self, path: str | None, t0: float) -> None:
        self._fh = open(path, "w") if path else None
        self._t0 = t0

    def _write(self, obj: dict) -> None:
        if self._fh is not None:
            self._fh.write(json.dumps(obj) + "\n")

    def c2s_handshake(self, hs: dict) -> None:
        """Log the redacted handshake."""
        self._write(
            {"dir": "c2s", "dt": round(time.monotonic() - self._t0, 3),
             "kind": "handshake", "msg": _redact_handshake(hs)}
        )

    def c2s_bin(self, n: int, silence: bool) -> None:
        """Log a binary audio frame (byte count only, never content)."""
        self._write(
            {"dir": "c2s", "dt": round(time.monotonic() - self._t0, 3),
             "kind": "silence" if silence else "bin", "n": n}
        )

    def c2s_end(self) -> None:
        """Log endStream."""
        self._write(
            {"dir": "c2s", "dt": round(time.monotonic() - self._t0, 3),
             "kind": "endStream"}
        )

    def s2c(self, msg) -> None:
        """Log a raw server message."""
        self._write(
            {"dir": "s2c", "dt": round(time.monotonic() - self._t0, 3), "msg": msg}
        )

    def net(self, kind: str, **kw) -> None:
        """Log a network lifecycle event (connect/close/error)."""
        self._write(
            {"dir": "net", "dt": round(time.monotonic() - self._t0, 3),
             "kind": kind, **kw}
        )

    def close(self) -> None:
        """Flush and close the log file."""
        if self._fh is not None:
            self._fh.close()
            self._fh = None


class ASRSession:
    """One realtime WebSocket session: paced sender + parsing receiver."""

    def __init__(
        self,
        config: ASRConfig,
        feed: PCMFeed,
        on_event=None,
        tracker_offset_ms: int = 0,
        record_path: str | None = None,
    ) -> None:
        self.config = config
        self.feed = feed
        self.on_event = on_event
        self.tracker = TurnTracker(offset_ms=tracker_offset_ms)
        self.record_path = record_path
        self.stats = SessionStats()
        self._end_wall: dict[int, float] = {}  # turnId -> speechEnd wall time

    async def _emit(self, events: list[dict]) -> None:
        for ev in events:
            if ev["type"] == "transcript.partial":
                self.stats.partials += 1
                if self.stats.first_partial_s is None:
                    self.stats.first_partial_s = self._wall() - self._t0
            elif ev["type"] == "transcript.final":
                self.stats.finals += 1
            if self.on_event is not None:
                res = self.on_event(ev)
                if asyncio.iscoroutine(res):
                    await res

    def _wall(self) -> float:
        return time.monotonic()

    async def run(self, max_audio_s: float | None = None) -> SessionStats:
        """Run the session until audio EOF (or ``max_audio_s``), then drain.

        ``max_audio_s`` caps audio per session for proactive <60min
        reconnects; the caller opens the next session with an updated
        tracker offset (see :func:`transcribe_stream`).
        """
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed

        cfg = self.config
        self._t0 = self._wall()
        rec = _Recorder(self.record_path, self._t0)
        chunk_dur = cfg.chunk_ms / 1000.0
        silence_frame = b"\x00" * cfg.chunk_bytes
        hs = build_handshake(cfg)
        try:
            async with connect(
                cfg.url, max_size=None, open_timeout=cfg.connect_timeout_s
            ) as ws:
                rec.net("connected")
                await ws.send(json.dumps(hs))
                rec.c2s_handshake(hs)
                try:
                    ack_raw = await asyncio.wait_for(ws.recv(), timeout=20.0)
                except asyncio.TimeoutError:
                    self.stats.outcome = "timeout-handshake-ack"
                    return self.stats
                rec.s2c(ack_raw if isinstance(ack_raw, str) else f"<{len(ack_raw)}B>")
                ack = json.loads(ack_raw) if isinstance(ack_raw, str) else {}
                if ack.get("type") == "error" or "sessionId" not in ack:
                    self.stats.outcome = f"handshake-error: {ack}"
                    return self.stats
                self.stats.session_id = ack["sessionId"]

                sender_done = asyncio.Event()

                async def sender() -> None:
                    start = self._wall()
                    sent_bytes = 0
                    try:
                        while True:
                            if (
                                max_audio_s is not None
                                and sent_bytes / BYTES_PER_SEC >= max_audio_s
                            ):
                                break
                            item = await self.feed.get(timeout=chunk_dur)
                            if item is None:  # EOF
                                break
                            if item == b"":  # starved: hold with silence
                                await ws.send(silence_frame)
                                rec.c2s_bin(len(silence_frame), True)
                                sent_bytes += len(silence_frame)
                                self.stats.silence_sent_s += chunk_dur
                            else:
                                await ws.send(item)
                                rec.c2s_bin(len(item), False)
                                sent_bytes += len(item)
                            # Real-time pacing: never ahead of the media clock.
                            behind = start + sent_bytes / BYTES_PER_SEC - self._wall()
                            if behind > 0:
                                await asyncio.sleep(behind)
                    finally:
                        self.stats.audio_sent_s = sent_bytes / BYTES_PER_SEC
                        sender_done.set()
                        try:
                            await ws.send(json.dumps({"type": "endStream"}))
                            rec.c2s_end()
                        except ConnectionClosed:
                            pass

                stask = asyncio.create_task(sender())
                try:
                    while True:
                        try:
                            timeout = cfg.drain_timeout_s if sender_done.is_set() else 60.0
                            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                        except asyncio.TimeoutError:
                            self.stats.outcome = (
                                "timeout-drain" if sender_done.is_set()
                                else "timeout-mid-stream"
                            )
                            break
                        except ConnectionClosed as e:
                            rcvd = getattr(e, "rcvd", None)
                            reason = rcvd.reason if rcvd is not None else ""
                            rec.net("closed", code=e.code, reason=reason)
                            self.stats.close_code = e.code
                            self.stats.close_reason = reason or ""
                            self.stats.outcome = f"closed-{e.code}"
                            break
                        if isinstance(msg, bytes):
                            rec.net("recv-bytes", n=len(msg))
                            continue
                        ev = json.loads(msg)
                        rec.s2c(ev)
                        now = self._wall()
                        if ev.get("type") == "speechEnd":
                            self._end_wall[int(ev["turnId"])] = now
                        if ev.get("type") == "speechComplete":
                            tid = int(ev["turnId"])
                            if tid in self._end_wall:
                                self.stats.end_to_complete_ms[tid] = round(
                                    (now - self._end_wall[tid]) * 1000, 1
                                )
                        await self._emit(self.tracker.feed(ev))
                finally:
                    if not stask.done():
                        stask.cancel()
                        try:
                            await stask
                        except asyncio.CancelledError:
                            pass
                    else:
                        await stask
                if not self.stats.outcome:
                    self.stats.outcome = "drained"
        except Exception as e:  # connect errors, handshake transport errors
            rec.net("exception", err=repr(e))
            if not self.stats.outcome:
                self.stats.outcome = f"exception: {e!r}"
        finally:
            rec.close()
        return self.stats


async def transcribe_stream(
    config: ASRConfig,
    audio_source,
    on_event=None,
    record_path: str | None = None,
) -> list[SessionStats]:
    """Transcribe a PCM async-iterable across one or more WS sessions.

    Sessions rotate proactively at ``config.reconnect_at_min`` (<60min
    server cap, no resume token): turn media times rejoin via the
    cumulative tracker offset. Unexpected failures reconnect with
    backoff up to ``config.max_reconnects``.
    """
    feed = PCMFeed(audio_source)
    await feed.start()
    all_stats: list[SessionStats] = []
    offset_ms = 0
    max_audio_s = config.reconnect_at_min * 60.0
    failures = 0
    try:
        while True:
            session = ASRSession(
                config,
                feed,
                on_event=on_event,
                tracker_offset_ms=offset_ms,
                record_path=record_path,
            )
            stats = await session.run(max_audio_s=max_audio_s)
            all_stats.append(stats)
            offset_ms += int(stats.audio_sent_s * 1000)
            if feed._eof and stats.audio_sent_s < max_audio_s:
                break  # source exhausted inside this session: done
            if stats.outcome.startswith(("closed-", "drained", "timeout-drain")):
                if feed._eof:
                    break
                failures = 0  # clean rotation: keep going, same feed
                continue
            failures += 1  # unexpected failure: backoff + reconnect
            if failures > config.max_reconnects:
                break
            await asyncio.sleep(min(2.0**failures, 30.0))
    finally:
        await feed.close()
    return all_stats


def transcribe_wav(
    wav_path: str,
    config: ASRConfig,
    on_event=None,
    record_path: str | None = None,
) -> list[SessionStats]:
    """Blocking helper: transcribe one WAV file (DIARIZATION default)."""
    return asyncio.run(
        transcribe_stream(
            config,
            pcm_chunks_from_wav(wav_path, config.chunk_ms),
            on_event=on_event,
            record_path=record_path,
        )
    )
