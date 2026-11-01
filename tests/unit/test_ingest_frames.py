"""Frames ingest tests: per-frame vision, t_second anchors, timeline
JSON, audio NO-GO, ffmpeg-missing behavior. Offline: doubles only."""

import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.ingest import (  # noqa: E402
    PROVENANCE_MISSING,
    AudioNoGoError,
    ToolUnavailableError,
)
from atlas.ingest.frames import (  # noqa: E402
    VideoFrame,
    extract_frames_ffmpeg,
    ingest_frames,
    timeline_json,
    transcribe_audio,
)


def _build_png(width=32, height=24, color=(30, 30, 200)):
    rows = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    comp = zlib.compress(rows)

    def chunk(typ, data):
        out = struct.pack(">I", len(data)) + typ + data
        return out + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", comp)
            + chunk(b"IEND", b""))


class _ScriptedVision:
    name = "double-vision"
    available = True

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def complete_vision(self, system, user_text, urls, max_tokens=2000,
                        json_schema=None):
        self.calls.append(user_text)
        if len(self.payloads) > 1:
            return self.payloads.pop(0)
        return self.payloads[0]


def test_ingest_frames_t_second_anchors():
    vision = _ScriptedVision([
        {"claims": [{"text": "Frame shows a blue field.", "span": "blue field",
                     "region": "full"}]},
        {"claims": [{"text": "Frame still shows a blue field.",
                     "span": "blue field", "region": "center"}]},
    ])
    frames = [VideoFrame(0.0, _build_png(), "f0.png"),
              VideoFrame(2.5, _build_png(), "f1.png")]
    result = ingest_frames(frames, "clip.mp4", vision)
    assert len(result.claims) == 2
    assert result.claims[0].anchor["t_second"] == 0.0
    assert result.claims[1].anchor["t_second"] == 2.5
    assert result.claims[1].anchor["region"] == "center"
    assert "clip.mp4@t=" in result.claims[0].source
    assert "t=0.0s" in vision.calls[0] and "t=2.5s" in vision.calls[1]


def test_ingest_frames_unanchored_discarded():
    vision = _ScriptedVision([{"claims": [
        {"text": "Something happens here.", "span": "elsewhere entirely",
         "region": "full"},
    ]}])
    result = ingest_frames([VideoFrame(1.0, _build_png())], "c.mp4", vision)
    assert result.claims == []
    assert result.provenance_missing_count == 1
    assert result.discarded[0].code == PROVENANCE_MISSING


def test_ingest_frames_no_vision_zero_claims():
    result = ingest_frames([VideoFrame(0.0, _build_png())], "c.mp4", None)
    assert result.claims == []
    assert any("no vision client" in u for u in result.uncertainties)


def test_ingest_frames_unreadable_frame_skipped():
    vision = _ScriptedVision([{"claims": []}])
    frames = [VideoFrame(0.0, b"junk", "bad.png"),
              VideoFrame(1.0, _build_png(), "good.png")]
    result = ingest_frames(frames, "c.mp4", vision)
    assert any("unreadable" in u for u in result.uncertainties)
    assert len(vision.calls) == 1  # only the good frame was sent


def test_timeline_json_groups_by_t():
    vision = _ScriptedVision([
        {"claims": [{"text": "Frame shows a blue field.", "span": "blue field",
                     "region": "full"}]},
        {"claims": []},
    ])
    frames = [VideoFrame(0.0, _build_png(), "f0.png"),
              VideoFrame(5.0, _build_png(), "f1.png")]
    result = ingest_frames(frames, "clip.mp4", vision)
    timeline = json.loads(timeline_json(result, "clip.mp4", duration_s=5.0))
    assert timeline["source"] == "clip.mp4"
    assert timeline["duration_s"] == 5.0
    assert "NO-GO" in timeline["audio"]
    assert [f["t_second"] for f in timeline["frames"]] == [0.0]
    assert timeline["claim_count"] == 1


def test_audio_is_nogo():
    with pytest.raises(AudioNoGoError):
        transcribe_audio(b"\x00" * 16)


def test_ffmpeg_missing_raises(monkeypatch):
    monkeypatch.setattr("atlas.ingest.frames.shutil.which", lambda _: None)
    with pytest.raises(ToolUnavailableError):
        extract_frames_ffmpeg("clip.mp4")
