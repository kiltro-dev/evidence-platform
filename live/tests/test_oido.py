"""M1a oído tests: parser replay + mock WS server + mocked file fallback.

Local-only: server messages replay from the REAL recorded fixture
(``live/fixtures/asr_sample.jsonl``); the WS test runs against a local
mock server; the file test stubs the HTTP transport. No network, no key.
"""

import asyncio
import json
import os
import sys
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.oido.asr_file import file_response_to_bus_events, transcribe_file  # noqa: E402
from live.oido.asr_ws import (  # noqa: E402
    ASRConfig,
    ASRSession,
    PCMFeed,
    TurnTracker,
    build_handshake,
)

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "fixtures", "asr_sample.jsonl")


def load_fixture_lines():
    """Raw recorded lines (c2s/s2c/net)."""
    with open(FIXTURE) as fh:
        return [json.loads(line) for line in fh]


def load_server_messages():
    """Server messages from the fixture as parsed dicts (offline replay)."""
    msgs = []
    for line in load_fixture_lines():
        if line.get("dir") != "s2c":
            continue
        msg = line["msg"]
        msgs.append(json.loads(msg) if isinstance(msg, str) else msg)
    return msgs


def replay(messages, offset_ms=0):
    """Feed server messages through a fresh TurnTracker."""
    tracker = TurnTracker(offset_ms=offset_ms)
    events = []
    for msg in messages:
        events.extend(tracker.feed(msg))
    return tracker, events


def finals(events):
    """Only transcript.final bus events."""
    return [e for e in events if e["type"] == "transcript.final"]


def partials(events):
    """Only transcript.partial bus events."""
    return [e for e in events if e["type"] == "transcript.partial"]


# --- handshake ---

def test_handshake_shape():
    """Handshake carries Bearer auth + fixed session settings."""
    cfg = ASRConfig(api_key="k", keywords=("Popocatepetl",), language_bias=("Spanish",))
    hs = build_handshake(cfg)
    assert hs["authorization"] == {"accessToken": "Bearer k"}
    assert hs["audioEncoding"] == "PCM_24KHZ"
    assert hs["model"] == "muse-voice-transcribe-1.0"
    assert hs["mode"] == "DIARIZATION"
    assert hs["partialMode"] == "CUMULATIVE"
    assert hs["emitAudioProgress"] is False
    assert hs["keywords"] == ["Popocatepetl"]
    assert hs["languageBias"] == ["Spanish"]


def test_handshake_omits_empty_bias():
    """keywords/languageBias absent when unset (autodetect)."""
    hs = build_handshake(ASRConfig(api_key="k"))
    assert "keywords" not in hs
    assert "languageBias" not in hs


# --- offline replay of the REAL fixture ---

def test_fixture_has_no_secret():
    """Recorded handshake redacts the token; no long blobs stored."""
    for line in load_fixture_lines():
        if line.get("kind") == "handshake":
            assert line["msg"]["authorization"] == {"accessToken": "***REDACTED***"}
        if line.get("dir") == "c2s" and line.get("kind") in ("bin", "silence"):
            assert set(line) == {"dir", "dt", "kind", "n"}  # counts, never audio


def test_replay_finals():
    """Fixture replays to 3 sequential finals with A/B/A speakers."""
    _, events = replay(load_server_messages())
    fin = finals(events)
    assert [e["payload"]["turn"] for e in fin] == [0, 1, 2]
    assert [e["payload"]["speaker"] for e in fin] == ["A", "B", "A"]
    for e in fin:
        assert set(e) == {"type", "t_wall", "t_media", "payload"}
        p = e["payload"]
        assert set(p) == {"turn", "text", "speaker", "t_start", "t_end"}
        assert p["text"].strip()
        assert p["t_start"] is not None and p["t_end"] is not None
        assert 0 <= p["t_start"] < p["t_end"]
        assert e["t_media"] == p["t_end"]
    assert "briefing" in fin[0]["payload"]["text"]
    assert "Producto Interno Bruto" in fin[1]["payload"]["text"] or \
        "producto interno bruto" in fin[1]["payload"]["text"]
    assert "Inflation" in fin[2]["payload"]["text"]


def test_replay_partials_shape():
    """Partials precede finals and carry {turn, text}."""
    _, events = replay(load_server_messages())
    part = partials(events)
    assert len(part) >= 10
    for e in part:
        assert set(e["payload"]) == {"turn", "text"}
        assert e["payload"]["turn"] in (0, 1, 2)
    assert events[0]["type"] == "transcript.partial"


def test_replay_no_ghost_final_from_empty_marker():
    """The session-final empty transcript{final:true} yields no event."""
    msgs = load_server_messages()
    empties = [m for m in msgs
               if m.get("type") == "transcript" and m.get("final") is True]
    assert empties, "fixture must contain the empty session-final marker"
    assert all((m.get("transcript") or "") == "" for m in empties)
    _, events = replay(msgs)
    assert len(finals(events)) == 3  # only the 3 speechComplete finals


def test_empty_final_ignored_unit():
    """Unit: empty final:true -> no events, ever."""
    assert TurnTracker().feed({"type": "transcript", "transcript": "",
                               "final": True, "audioProcessedMs": 9999}) == []


def test_ptt_style_final_with_text():
    """Unit: PUSH_TO_TALK final:true WITH text becomes a final."""
    (ev,) = TurnTracker().feed({"type": "transcript", "transcript": "Hi.",
                                "final": True, "audioProcessedMs": 2000})
    assert ev["type"] == "transcript.final"
    assert ev["payload"]["text"] == "Hi."
    assert ev["payload"]["t_end"] == 2.0


def test_speaker_attribution_and_times():
    """Unit: speaker (no turnId) binds to the open turn; times in seconds."""
    tr = TurnTracker()
    tr.feed({"type": "speechStart", "turnId": 5, "audioProcessedMs": 100})
    tr.feed({"type": "speaker", "label": "C", "audioProcessedMs": 900})
    tr.feed({"type": "speechEnd", "turnId": 5, "audioProcessedMs": 800})
    (ev,) = tr.feed({"type": "speechComplete", "turnId": 5,
                     "transcript": "  Hola. ", "audioProcessedMs": 800})
    assert ev["payload"] == {"turn": 5, "text": "Hola.", "speaker": "C",
                             "t_start": 0.1, "t_end": 0.8}


def test_reconnect_offset_rejoins_media_time():
    """Tracker offset shifts t_media across <60min reconnects."""
    _, events = replay(load_server_messages(), offset_ms=60_000)
    fin = finals(events)
    assert fin[0]["payload"]["t_start"] >= 60.0
    assert fin[0]["t_media"] >= 60.0


# --- mock WS server: full client run, local only ---

async def _silence(seconds=0.5, chunk_ms=80):
    n = int(24000 * 2 * chunk_ms / 1000)
    frames, _ = divmod(int(seconds * 1000), chunk_ms)
    for _ in range(frames):
        yield b"\x00" * n


def test_client_against_mock_server():
    """Full ASRSession run vs a local server replaying the fixture."""
    from websockets.asyncio.server import serve

    server_msgs = load_server_messages()
    seen = {}

    async def run_case():
        async def handler(ws):
            raw = await ws.recv()
            hs = json.loads(raw)
            seen["handshake"] = hs
            await ws.send(json.dumps({"sessionId": "mock-session"}))
            n_bytes = 0
            async for msg in ws:
                if isinstance(msg, bytes):
                    n_bytes += len(msg)
                elif json.loads(msg).get("type") == "endStream":
                    break
            seen["audio_bytes"] = n_bytes
            for m in server_msgs:  # replay the REAL server script
                if isinstance(m, str):
                    continue  # live ack already answered above
                await ws.send(json.dumps(m))
            await ws.close(code=1000, reason="No more transcript segments")

        server = await serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            got = []
            cfg = ASRConfig(api_key="test-key", url=f"ws://127.0.0.1:{port}")
            feed = PCMFeed(_silence())
            await feed.start()
            try:
                stats = await ASRSession(cfg, feed, on_event=got.append).run()
            finally:
                await feed.close()
            return stats, got
        finally:
            server.close()
            await server.wait_closed()

    stats, got = asyncio.run(run_case())
    assert seen["handshake"]["authorization"] == {"accessToken": "Bearer test-key"}
    assert seen["handshake"]["mode"] == "DIARIZATION"
    assert seen["audio_bytes"] > 0
    assert stats.outcome == "closed-1000"
    assert stats.close_code == 1000
    _, expected = replay(server_msgs)
    assert [e["type"] for e in got] == [e["type"] for e in expected]
    assert [e["payload"] for e in finals(got)] == \
        [e["payload"] for e in finals(expected)]
    assert stats.finals == 3


# --- file fallback: transport stubbed, no network ---

def _write_wav(path, seconds=0.5, channels=1, rate=24000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(seconds * rate * channels * 2))
    return str(path)


def test_file_fallback_mocked(tmp_path):
    """POST shape (request+audio parts, Bearer, sessionId) + turns->finals."""
    wav = _write_wav(tmp_path / "s.wav")
    calls = {}

    def fake_post(url, headers, files, timeout_s):
        calls["url"] = url
        calls["headers"] = headers
        calls["files"] = files
        return {
            "sessionId": "s", "transcript": "A. B.",
            "audioDurationMs": 1500,
            "turns": [
                {"turnId": 0, "startMs": 100, "endMs": 700,
                 "transcript": "A.", "speaker": "A"},
                {"turnId": 1, "startMs": 800, "endMs": 1400,
                 "transcript": "B.", "speaker": "B"},
            ],
        }

    resp = transcribe_file(wav, "k", keywords=("X",), session_id="sid1", post=fake_post)
    assert calls["url"] == "https://api.meta.ai/v1/asr/transcribe?sessionId=sid1"
    assert calls["headers"] == {"Authorization": "Bearer k"}
    name, body, ctype = calls["files"]["request"]
    assert name is None and ctype == "application/json"
    req = json.loads(body)
    assert (req["mode"], req["model"], req["audioEncoding"]) == \
        ("DIARIZATION", "muse-voice-transcribe-1.0", "WAV")
    assert req["keywords"] == ["X"]
    assert calls["files"]["audio"][2] == "audio/wav"
    events = file_response_to_bus_events(resp)
    assert [e["payload"]["turn"] for e in events] == [0, 1]
    assert [e["payload"]["speaker"] for e in events] == ["A", "B"]
    assert events[0]["payload"]["t_start"] == 0.1
    assert events[1]["payload"]["t_end"] == 1.4
    assert all(e["type"] == "transcript.final" for e in events)


def test_file_validates_before_network(tmp_path):
    """Bad WAV / missing key raise without touching the transport."""

    def boom(url, headers, files, timeout_s):
        raise AssertionError("network must not happen")

    stereo = _write_wav(tmp_path / "st.wav", channels=2)
    with pytest.raises(ValueError):
        transcribe_file(stereo, "k", post=boom)
    mono = _write_wav(tmp_path / "m.wav")
    with pytest.raises(ValueError):
        transcribe_file(mono, "", post=boom)


def test_file_empty_turns_single_final():
    """PUSH_TO_TALK-style response (no turns) collapses to one final."""
    (ev,) = file_response_to_bus_events(
        {"transcript": "Hello.", "audioDurationMs": 1200, "turns": []})
    assert ev["payload"]["turn"] == 0
    assert ev["payload"]["text"] == "Hello."
    assert ev["payload"]["t_start"] is None
    assert ev["t_media"] == 1.2
