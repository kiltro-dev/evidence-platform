"""F2-8 (mic2) tests: virtual-capture degradation + clip2 no-derivative verdict.

All offline: no hardware, no network, no key. There are no live-spend
tests by design — this task made zero live calls (USD 0.00).

Task A pins the honest-degradation path: the static ffmpeg build cannot
talk to PulseAudio, this host has no ALSA capture nodes, and the WSL2
kernel cannot load snd-aloop (see live/F2_MIC2.md for the evidence).
Task B pins the clip2 verdict: no narration-only >= 20 s segment exists
in the VOA clip, so no derivative was produced and the mixed file is
conserved bit-identical (see the F2-8 addendum in PROVENANCE_clip2.md).
"""

import hashlib
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.ingest.demux import ffmpeg_exe  # noqa: E402
from live.mic.capture import (  # noqa: E402
    NoMicrophoneError,
    _has_demuxer,
    describe_host,
    detect_microphone,
)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLIP2 = os.path.join(ROOT, "data", "clips", "VOA_20170203_SuperBowl_AdFirms.mp4")
PROV2 = os.path.join(ROOT, "live", "material", "PROVENANCE_clip2.md")
REPORT2 = os.path.join(ROOT, "live", "F2_MIC2.md")


def sha256_of(path: str) -> str:
    """Hex SHA256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# --- Task A: demuxer detection is honest (F2-8 fix) ---

def test_static_ffmpeg_lacks_pulse_demuxer():
    """Pinned static build (v7.0.2) has no pulse input device."""
    assert _has_demuxer(ffmpeg_exe(), "pulse") is False


def test_static_ffmpeg_has_alsa_demuxer():
    """Pinned static build keeps the alsa input device (capture-capable)."""
    assert _has_demuxer(ffmpeg_exe(), "alsa") is True


def test_has_demuxer_rejects_unknown_names():
    """Unknown device names are False (guards the old always-True bug)."""
    assert _has_demuxer(ffmpeg_exe(), "nosuchxyz") is False


def test_describe_host_consistent_with_demuxers():
    """describe_host reports the real per-binary demuxer facts."""
    host = describe_host()
    assert "alsa_capture_nodes" in host and "pulse_server_hint" in host
    static = [b for b in host["binaries"] if b["path"] == ffmpeg_exe()]
    assert len(static) == 1
    assert static[0]["pulse_demuxer"] is False
    assert static[0]["alsa_demuxer"] is True


def test_detect_error_names_real_cause_when_nothing_captures(monkeypatch):
    """Pinned binary + no nodes -> error names pulse-build + alsa-nodes."""
    monkeypatch.setattr(
        "live.mic.capture.candidate_binaries", lambda: [ffmpeg_exe()]
    )
    monkeypatch.setattr("live.mic.capture._alsa_capture_nodes", lambda: [])
    with pytest.raises(NoMicrophoneError) as ei:
        detect_microphone()
    msg = str(ei.value)
    assert "no pulse demuxer in this build" in msg
    assert "no capture nodes" in msg


def test_autodetect_never_returns_doubles():
    """Autodetect yields a REAL mic or raises; never a synthetic double."""
    try:
        found = detect_microphone()
    except NoMicrophoneError:
        return  # honest degradation on mic-less hosts (this host)
    assert found.real is True
    assert found.kind in ("pulse", "alsa")


# --- Task B: clip2 verdict (no derivative) ---

def test_provenance_f2_8_addendum_present():
    """Addendum documents method + timeline + no-derivative verdict."""
    assert os.path.isfile(PROV2), f"missing {PROV2}"
    text = open(PROV2, encoding="utf-8").read()
    assert "Addendum F2-8" in text
    for marker in ("sin derivado", "24–38", "presentadora", "Case",
                   "51,4–74,3", "mixto documentado"):
        assert marker in text, f"addendum missing marker: {marker}"


def test_clip2_source_hash_unchanged():
    """Mixed clip conserved bit-identical (no in-place alteration)."""
    if not os.path.isfile(CLIP2):
        pytest.skip("clip2 mp4 not downloaded (see PROVENANCE_clip2.md)")
    text = open(PROV2, encoding="utf-8").read()
    want = re.search(r"SHA256 \(mp4\):\*\*\s*`([0-9a-f]{64})`", text).group(1)
    assert sha256_of(CLIP2) == want


def test_report_f2_mic2_complete():
    """live/F2_MIC2.md exists with mic verdict + clip2 verdict + USD."""
    assert os.path.isfile(REPORT2), f"missing {REPORT2}"
    text = open(REPORT2, encoding="utf-8").read()
    for marker in ("degrad", "snd-aloop", "sin derivado", "USD",
                   "Unresolved"):
        assert marker in text, f"report missing marker: {marker}"
