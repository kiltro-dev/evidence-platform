"""File fallback for Atlas Live oído: POST a WAV, get turns back.

``POST https://api.meta.ai/v1/asr/transcribe?sessionId=<id>`` with
multipart ``request`` (JSON settings, same as the realtime handshake
minus ``authorization``) + ``audio`` (RIFF/WAVE mono s16 16/24 kHz).
Caps: 32 MB body, 10 min audio. Default JSON response carries
``transcript`` + ``turns[{turnId,startMs,endMs,transcript,speaker?}]``.

Verified live by the spike (t7): 15.5 s audio -> 200 in 3.6 s. Caveat:
file decode once differed from realtime ("creció creció" duplicated
word; realtime was clean) — treat the WS path as primary.
"""

from __future__ import annotations

import json
import os
import time
import uuid
import wave

from .asr_ws import ASR_MODEL, bus_event

FILE_URL = "https://api.meta.ai/v1/asr/transcribe"
MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_AUDIO_S = 10 * 60
WAV_RATES = (16000, 24000)


def _default_post(url, headers, files, timeout_s):
    import httpx

    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(url, headers=headers, files=files)
        resp.raise_for_status()
        return resp.json()


def _wav_info(path: str) -> tuple[int, int, int, float]:
    """(channels, sampwidth, rate, seconds) or ValueError."""
    with wave.open(path, "rb") as w:
        seconds = w.getnframes() / w.getframerate()
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), seconds


def transcribe_file(
    wav_path: str,
    api_key: str,
    model: str = ASR_MODEL,
    mode: str = "DIARIZATION",
    keywords: tuple[str, ...] | None = None,
    language_bias: tuple[str, ...] | None = None,
    session_id: str | None = None,
    timeout_s: float = 180.0,
    post=_default_post,
) -> dict:
    """Transcribe one WAV file via the ``/v1/asr/transcribe`` endpoint.

    ``post`` is an injectable ``(url, headers, files, timeout_s) -> dict``
    seam (tests stub it; production uses httpx). Raises ValueError when
    the file violates the WAV/cap contract before any network happens.
    """
    if not api_key:
        raise ValueError("api_key required (MODEL_API_KEY)")
    channels, width, rate, seconds = _wav_info(wav_path)
    if (channels, width) != (1, 2) or rate not in WAV_RATES:
        raise ValueError(
            f"need mono s16 {WAV_RATES}Hz WAV, got ch={channels} w={width} r={rate}"
        )
    if seconds > MAX_AUDIO_S:
        raise ValueError(f"audio {seconds:.0f}s exceeds 10 min cap")
    if os.path.getsize(wav_path) > MAX_BODY_BYTES:
        raise ValueError("file exceeds 32 MB body cap")
    request: dict = {"mode": mode, "model": model, "audioEncoding": "WAV"}
    if keywords:
        request["keywords"] = list(keywords)
    if language_bias:
        request["languageBias"] = list(language_bias)
    sid = session_id or uuid.uuid4().hex
    with open(wav_path, "rb") as fh:
        audio_bytes = fh.read()
    files = {
        "request": (None, json.dumps(request), "application/json"),
        "audio": (os.path.basename(wav_path), audio_bytes, "audio/wav"),
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    return post(f"{FILE_URL}?sessionId={sid}", headers, files, timeout_s)


def file_response_to_bus_events(resp: dict) -> list[dict]:
    """Turn a ``/transcribe`` JSON response into transcript.final events.

    t_wall is conversion time (batch, not streaming); each event's t_media
    is its turn end. PUSH_TO_TALK responses (empty turns) collapse to one
    final for the whole transcript with unknown boundaries.
    """
    now = time.time()
    events: list[dict] = []
    turns = resp.get("turns") or []
    if not turns and (resp.get("transcript") or "").strip():
        dur_ms = resp.get("audioDurationMs", 0) or 0
        ev = bus_event(
            "transcript.final",
            dur_ms / 1000.0,
            {
                "turn": 0,
                "text": resp["transcript"].strip(),
                "speaker": None,
                "t_start": None,
                "t_end": dur_ms / 1000.0,
            },
        )
        ev["t_wall"] = now
        return [ev]
    for t in turns:
        t_start = t["startMs"] / 1000.0
        t_end = t["endMs"] / 1000.0
        ev = bus_event(
            "transcript.final",
            t_end,
            {
                "turn": int(t["turnId"]),
                "text": (t.get("transcript", "") or "").strip(),
                "speaker": t.get("speaker"),
                "t_start": t_start,
                "t_end": t_end,
            },
        )
        ev["t_wall"] = now
        events.append(ev)
    return events
