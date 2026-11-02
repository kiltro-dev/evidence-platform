"""F2-10 (mic3) tests: driver-stubbed mic E2E contract + clip2b synthetic clip.

All offline: no hardware, no network, no key. The two live calls of this
task (1 WS mic-E2E + 1 file transcribe for clip2b recall, USD ~0.005)
are pinned by their saved artifacts + report, not re-executed here.

Task A pins the file-fed live path: MicCapture accepts arbitrary ffmpeg
inputs, and the paced ("-re") file feed is honestly labeled
"driver-stubbed-file-fed" (see live/F2_MIC3.md for the kernel verdict).
Task B pins the clip2b ground truth: 8 NASA-backed claims, well-formed
truth2b, complete provenance, 60-90 s artifact when built.
"""

import asyncio
import json
import math
import os
import re
import struct
import subprocess
import sys
import wave

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "live", "material"))

from live.ingest.demux import ffmpeg_exe, probe_streams  # noqa: E402
from live.mic.capture import (  # noqa: E402
    BYTES_PER_SEC,
    DRIVER_STUBBED_LABEL,
    MicCapture,
    MicInput,
    NoMicrophoneError,
    custom_ffmpeg_input,
    detect_microphone,
    driver_stubbed_file_input,
)
from live.oido.asr_ws import PCMFeed  # noqa: E402

import build_clip2b as b2b  # noqa: E402

CLIP2B = os.path.join(ROOT, "data", "clips", "clip2b.mp4")
SRT2B = os.path.join(ROOT, "data", "clips", "clip2b.srt")
TRUTH2B = os.path.join(ROOT, "data", "clips", "truth2b.json")
PROV2B = os.path.join(ROOT, "live", "material", "PROVENANCE_clip2b.md")
REPORT3 = os.path.join(ROOT, "live", "F2_MIC3.md")
MIC3_TRANSCRIPT = os.path.join(ROOT, "data", "clips", "mic3_e2e_transcript.json")


def _write_sine_wav(path: str, seconds: float = 0.5) -> str:
    """Synthetic mono s16 24 kHz WAV via stdlib wave (offline double)."""
    rate = 24000
    frames = int(seconds * rate)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        for i in range(frames):
            s = int(12000 * math.sin(2 * math.pi * 440 * i / rate))
            w.writeframes(struct.pack("<h", s))
    return path


def _drain(capture: MicCapture):
    """Run iter_audio to completion, return [(t_media, bytes)]."""
    async def _go():
        out = []
        async for t_media, data in capture.iter_audio():
            out.append((t_media, data))
        return out

    try:
        return asyncio.run(_go())
    finally:
        capture.close()


def _check_t_media_contract(chunks):
    """t_media starts at 0 and tracks bytes_read / 48000 exactly."""
    assert chunks, "expected at least one chunk"
    assert chunks[0][0] == 0.0
    total = 0
    for t_media, data in chunks:
        assert t_media == pytest.approx(total / BYTES_PER_SEC)
        assert len(data) > 0
        total += len(data)
    assert total > 0


# --- Task A: arbitrary ffmpeg source + driver-stubbed label ---

def test_custom_input_passes_args_verbatim():
    """Arbitrary input args land verbatim before the PCM output flags."""
    mi = custom_ffmpeg_input(("-re", "-i", "feed.wav"), device="feed.wav",
                             label="test-double:custom")
    assert mi.real is False
    cap = MicCapture.for_custom_input(mi)
    argv = cap.argv
    assert argv[3:6] == ["-re", "-i", "feed.wav"]
    assert argv[-3:] == ["-f", "s16le", "-"]
    assert argv.index("-re") < argv.index("-i") < argv.index("-ac")


def test_driver_stubbed_label_and_pacing():
    """Paced file feed: honest label + -re before -i, never real."""
    mi = driver_stubbed_file_input("feed.wav")
    assert DRIVER_STUBBED_LABEL == "driver-stubbed-file-fed"
    assert mi.label == DRIVER_STUBBED_LABEL
    assert mi.ffmpeg_input_args == ("-re", "-i", "feed.wav")
    assert mi.real is False
    assert mi.kind == "file"


def test_for_custom_input_rejects_real_inputs():
    """for_custom_input only accepts explicit doubles (no autodetect mix)."""
    real = MicInput(kind="alsa", ffmpeg_input_args=("-f", "alsa", "-i", "hw:0"),
                    device="hw:0", binary="ffmpeg", label="live-microphone",
                    real=True)
    with pytest.raises(ValueError):
        MicCapture.for_custom_input(real)


def test_driver_stubbed_file_fed_t_media_contract(tmp_path):
    """Paced file feed: t_media tracks bytes/48000 + honest source label."""
    wav = _write_sine_wav(str(tmp_path / "tone.wav"))
    cap = MicCapture.for_custom_input(driver_stubbed_file_input(wav))
    chunks = _drain(cap)
    _check_t_media_contract(chunks)
    total = sum(len(d) for _, d in chunks)
    assert cap.media_seconds == pytest.approx(total / BYTES_PER_SEC)
    assert cap.media_seconds == pytest.approx(0.5, abs=0.1)
    assert cap.source_label == DRIVER_STUBBED_LABEL
    assert cap.t0_wall is not None


def test_driver_stubbed_chunks_drop_into_pcmfeed(tmp_path):
    """chunks() feeds PCMFeed bytes then EOF (transcribe_stream shape)."""
    async def _go():
        wav = _write_sine_wav(str(tmp_path / "tone.wav"), seconds=0.4)
        cap = MicCapture.for_custom_input(driver_stubbed_file_input(wav))
        try:
            feed = PCMFeed(cap.chunks())
            await feed.start()
            seen = b""
            try:
                while True:
                    item = await feed.get(timeout=10.0)
                    if item is None:
                        break
                    seen += item
            finally:
                await feed.close()
            return seen
        finally:
            cap.close()

    seen = asyncio.run(_go())
    assert len(seen) > 0
    assert len(seen) == pytest.approx(0.4 * BYTES_PER_SEC, abs=BYTES_PER_SEC * 0.15)


def test_autodetect_never_returns_custom_or_file():
    """Autodetect yields a REAL pulse/alsa mic or raises; never a double."""
    try:
        found = detect_microphone()
    except NoMicrophoneError:
        return  # honest degradation on mic-less hosts
    assert found.real is True
    assert found.kind in ("pulse", "alsa")
    assert found.label != DRIVER_STUBBED_LABEL


def test_mic3_live_transcript_saved_when_run():
    """Saved WS run (ignored): 2 finals with transcript + in-band times."""
    if not os.path.isfile(MIC3_TRANSCRIPT):
        pytest.skip("live mic-E2E not run in this checkout")
    doc = json.load(open(MIC3_TRANSCRIPT, encoding="utf-8"))
    assert doc["label"] == DRIVER_STUBBED_LABEL
    finals = [e for e in doc["events"] if e["type"] == "transcript.final"]
    assert len(finals) >= 1
    full = " ".join(e["payload"]["text"] for e in finals).lower()
    assert "tierra" in full and "marte" in full
    for e in finals:
        p = e["payload"]
        assert p["t_start"] is not None and p["t_end"] is not None
        assert 0.0 <= p["t_start"] <= p["t_end"] <= 26.0
    assert doc["stats"][0]["audio_sent_s"] >= 20.0


# --- Task B: clip2b ground truth (pure table, no build needed) ---

def test_claims_table_eight_nasa_backed():
    """8 claims: unique ids, texto, esperado=true, NASA ref_url each."""
    assert len(b2b.CLAIMS) == 8
    ids = [c["id"] for c in b2b.CLAIMS]
    assert ids == [f"2b-{i}" for i in range(1, 9)]
    for c in b2b.CLAIMS:
        assert set(c) == {"id", "texto", "esperado", "ref_url"}
        assert c["esperado"] == "true"
        assert c["ref_url"].startswith(
            "https://nssdc.gsfc.nasa.gov/planetary/factsheet/")
        assert c["ref_url"].endswith("fact.html")
        assert c["texto"].endswith(".")


def test_claims_cover_distances_diameters_periods():
    """Claim texts carry the 8 NASA figures (3 + 3 + 2)."""
    full = " ".join(c["texto"] for c in b2b.CLAIMS)
    for fig in ("57,9", "149,6", "778,5",  # 10^6 km to the Sun
                "12104", "6792", "120536",  # equatorial diameter km
                "687", "4331"):  # orbital period days
        assert fig in full, f"figure missing: {fig}"


def test_slides_quote_claims_literally():
    """Every slide narration embeds its claim sentences verbatim."""
    seen = []
    for slide in b2b.SLIDES:
        narr = b2b.narration(slide)
        for cid in slide["claims"]:
            assert cid in b2b.CLAIM_BY_ID, f"unknown claim ref: {cid}"
            assert b2b.CLAIM_BY_ID[cid]["texto"] in narr
            seen.append(cid)
    assert sorted(seen) == sorted(b2b.CLAIM_BY_ID)


def test_duration_band_constants():
    """Builder enforces the 60-90 s band."""
    assert (b2b.MIN_DUR, b2b.MAX_DUR) == (60.0, 90.0)


def test_truth2b_well_formed_when_built():
    """truth2b.json: 8 rows {id,texto,t,esperado,ref_url}, t in band."""
    if not os.path.isfile(TRUTH2B):
        pytest.skip("clip2b not built (run live/material/build_clip2b.py)")
    rows = json.load(open(TRUTH2B, encoding="utf-8"))
    assert len(rows) == 8
    assert [r["id"] for r in rows] == [c["id"] for c in b2b.CLAIMS]
    prev_t = 0.0
    for row, claim in zip(rows, b2b.CLAIMS):
        assert set(row) == {"id", "texto", "t", "esperado", "ref_url"}
        assert row["texto"] == claim["texto"]
        assert row["esperado"] == claim["esperado"] == "true"
        assert row["ref_url"] == claim["ref_url"]
        assert 0.0 <= prev_t <= row["t"] <= 90.0
        prev_t = row["t"]


def test_provenance_2b_complete():
    """PROVENANCE_clip2b.md: own work + MIT + PD voice + hashes + NASA."""
    assert os.path.isfile(PROV2B), f"missing {PROV2B}"
    text = open(PROV2B, encoding="utf-8").read()
    for marker in ("propia", "MIT", "dominio público", "carlfm",
                   "SHA256 (mp4)", "Duración", "NASA", "truth2b",
                   "60–90", "Unresolved"):
        assert marker in text, f"provenance missing marker: {marker}"


def test_clip2b_artifact_in_band_when_built():
    """Built mp4: 60-90 s, audio+video; srt has 5 entries."""
    if not os.path.isfile(CLIP2B):
        pytest.skip("clip2b not built (run live/material/build_clip2b.py)")
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", CLIP2B],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, timeout=60)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", proc.stderr or "")
    assert m, "no Duration in ffmpeg output"
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    assert 60.0 <= dur <= 90.0, f"clip2b {dur:.1f}s outside 60-90 s"
    streams = probe_streams(CLIP2B)
    assert streams["audio"] is True and streams["video"] is True
    assert os.path.isfile(SRT2B), "missing clip2b.srt"
    entries = re.findall(r"^\d+$", open(SRT2B, encoding="utf-8").read(), re.M)
    assert len(entries) == len(b2b.SLIDES) == 5


# --- report ---

def test_report_f2_mic3_complete():
    """live/F2_MIC3.md: kernel verdict + E2E PASS + clip2b + USD + open."""
    assert os.path.isfile(REPORT3), f"missing {REPORT3}"
    text = open(REPORT3, encoding="utf-8").read()
    for marker in ("driver-stubbed-file-fed", "kernel", "PASS",
                   "transcript", "clip2b", "recall", "USD", "Unresolved"):
        assert marker in text, f"report missing marker: {marker}"
