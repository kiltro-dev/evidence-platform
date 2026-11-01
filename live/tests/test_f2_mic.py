"""F2-5 tests: real-mic capture contract (offline doubles) + clip2 provenance.

Local-only: capture tests use explicit lavfi/file doubles (no hardware);
the one live-mic smoke test skips honestly when no microphone exists
(CI/headless). Provenance tests verify the committed PROVENANCE_clip2.md
plus the ignored binaries when present. No network, no key.
"""

import asyncio
import hashlib
import os
import re
import subprocess
import sys
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.ingest.demux import (  # noqa: E402
    BYTES_PER_SEC as DEMUX_BPS,
    ffmpeg_exe,
    probe_streams,
)
from live.ingest.simstream import SOURCE_LABEL as SIM_LABEL  # noqa: E402
from live.mic.capture import (  # noqa: E402
    BYTES_PER_SEC,
    SOURCE_LABEL,
    MicCapture,
    NoMicrophoneError,
    detect_microphone,
    file_test_input,
    lavfi_test_input,
)
from live.oido.asr_ws import PCMFeed  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLIP2 = os.path.join(ROOT, "data", "clips", "VOA_20170203_SuperBowl_AdFirms.mp4")
LICENSE2 = os.path.join(
    ROOT, "data", "clips", "license_VOA_20170203_SuperBowl_AdFirms.txt"
)
PROV2 = os.path.join(ROOT, "live", "material", "PROVENANCE_clip2.md")


def sha256_of(path: str) -> str:
    """Hex SHA256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def probe_duration_s(path: str) -> float:
    """Duration seconds from ``ffmpeg -i`` stderr (offline)."""
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", proc.stderr or "")
    assert m, f"no Duration in ffmpeg output for {path}"
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def _write_sine_wav(path: str, seconds: float = 0.5) -> str:
    """Synthetic mono s16 24 kHz WAV via stdlib wave (offline double)."""
    import math
    import struct

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


def _check_t_media_contract(chunks, chunk_bytes: int):
    """t_media starts at 0 and tracks bytes_read / 48000 exactly."""
    assert chunks, "expected at least one chunk"
    assert chunks[0][0] == 0.0
    total = 0
    for t_media, data in chunks:
        assert t_media == pytest.approx(total / BYTES_PER_SEC)
        assert len(data) > 0
        total += len(data)
    assert total > 0


# --- constants & labels ---

def test_pcm_constants_match_demux_and_oido():
    """24 kHz mono s16: 48000 B/s, 80 ms frames = 3840 bytes."""
    assert BYTES_PER_SEC == 48000 == DEMUX_BPS
    assert MicCapture.for_test_lavfi().chunk_bytes == 3840


def test_source_label_is_live_not_simulated():
    """Mic label is distinct from the simstream label."""
    assert SOURCE_LABEL == "live-microphone"
    assert SOURCE_LABEL != SIM_LABEL


def test_test_doubles_are_labeled_not_real():
    """Doubles never pose as a microphone (real=False, test-double label)."""
    assert lavfi_test_input().real is False
    assert file_test_input("x.wav").real is False
    assert MicCapture.for_test_lavfi().mic_input.label.startswith("test-double")


def test_argv_emits_pcm_24k_mono():
    """ffmpeg argv: chosen input -> -ac 1 -ar 24000 -f s16le stdout."""
    cap = MicCapture.for_test_lavfi()  # explicit input: no autodetect
    argv = cap.argv
    assert argv[-3:] == ["-f", "s16le", "-"]
    assert argv[argv.index("-ac") + 1] == "1"
    assert argv[argv.index("-ar") + 1] == "24000"


# --- offline doubles: t_media contract ---

def test_lavfi_double_t_media_contract():
    """Lavfi double: t_media tracks bytes/48000 from 0.0."""
    chunks = _drain(MicCapture.for_test_lavfi(duration=0.5))
    _check_t_media_contract(chunks, 3840)


def test_wav_double_t_media_contract(tmp_path):
    """Stdlib-wave double replayed: same t_media contract + media_seconds."""
    wav = _write_sine_wav(str(tmp_path / "tone.wav"))
    cap = MicCapture.for_test_file(wav)
    chunks = _drain(cap)
    _check_t_media_contract(chunks, 3840)
    total = sum(len(d) for _, d in chunks)
    assert cap.media_seconds == pytest.approx(total / BYTES_PER_SEC)
    assert cap.media_seconds == pytest.approx(0.5, abs=0.1)
    assert cap.t0_wall is not None


def test_chunks_drop_into_pcmfeed():
    """chunks() feeds PCMFeed bytes then EOF (transcribe_stream shape)."""
    async def _go():
        cap = MicCapture.for_test_lavfi(duration=0.3)
        try:
            feed = PCMFeed(cap.chunks())
            await feed.start()
            seen = b""
            try:
                while True:
                    item = await feed.get(timeout=5.0)
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
    assert len(seen) == pytest.approx(0.3 * BYTES_PER_SEC, abs=BYTES_PER_SEC * 0.15)


# --- honest degradation ---

def test_detect_honest_error_when_nothing_captures(monkeypatch):
    """No demuxers/devices -> NoMicrophoneError naming pulse+alsa + CI."""
    monkeypatch.setattr(
        "live.mic.capture._has_demuxer", lambda binary, name: False
    )
    with pytest.raises(NoMicrophoneError) as ei:
        detect_microphone()
    msg = str(ei.value)
    assert "pulse" in msg and "alsa" in msg
    assert "CI" in msg or "headless" in msg


def test_capture_start_raises_without_mic(monkeypatch):
    """MicCapture().start() surfaces the honest error (no silent synth)."""
    def _boom(**kwargs):
        raise NoMicrophoneError("no mic here")

    monkeypatch.setattr("live.mic.capture.detect_microphone", _boom)
    with pytest.raises(NoMicrophoneError):
        MicCapture().start()


def test_live_mic_smoke():
    """REAL hardware path: skipped honestly when no microphone exists."""
    try:
        found = detect_microphone()
    except NoMicrophoneError as e:
        pytest.skip(f"no microphone on this host: {e}")
    assert found.real is True
    assert found.kind in ("pulse", "alsa")
    cap = MicCapture(found, duration=0.5)
    chunks = _drain(cap)
    assert chunks and chunks[0][0] == 0.0


# --- clip2 provenance (complete) ---

def test_provenance_file_complete():
    """PROVENANCE_clip2.md exists with fuente+licencia+hash+duracion."""
    assert os.path.isfile(PROV2), f"missing {PROV2}"
    text = open(PROV2, encoding="utf-8").read()
    for field in ("Fuente", "Licencia", "SHA256 (mp4)", "SHA256 (licencia)",
                  "Duración", "URL descarga directa"):
        assert field in text, f"provenance missing field: {field}"
    assert re.search(r"SHA256 \(mp4\):\*\*\s*`[0-9a-f]{64}`", text)
    assert re.search(r"SHA256 \(licencia\):\*\*\s*`[0-9a-f]{64}`", text)


def test_clip2_hash_matches_provenance():
    """Downloaded mp4 hashes to the value recorded in provenance."""
    if not os.path.isfile(CLIP2):
        pytest.skip("clip2 mp4 not downloaded (see PROVENANCE_clip2.md)")
    text = open(PROV2, encoding="utf-8").read()
    want = re.search(r"SHA256 \(mp4\):\*\*\s*`([0-9a-f]{64})`", text).group(1)
    assert sha256_of(CLIP2) == want


def test_clip2_duration_and_streams():
    """Clip2 is <= 4 min with audio+video streams (offline probe)."""
    if not os.path.isfile(CLIP2):
        pytest.skip("clip2 mp4 not downloaded (see PROVENANCE_clip2.md)")
    assert probe_duration_s(CLIP2) <= 4 * 60
    streams = probe_streams(CLIP2)
    assert streams["audio"] is True
    assert streams["video"] is True


def test_license2_hash_matches_provenance():
    """Saved license text hashes to the value recorded in provenance."""
    if not os.path.isfile(LICENSE2):
        pytest.skip("clip2 license text not saved (see PROVENANCE_clip2.md)")
    text = open(PROV2, encoding="utf-8").read()
    want = re.search(r"SHA256 \(licencia\):\*\*\s*`([0-9a-f]{64})`", text).group(1)
    assert sha256_of(LICENSE2) == want
