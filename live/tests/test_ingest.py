"""M1a ingest tests: demux + simstream over a synthetic clip.

Local-only: the clip is generated with the imageio_ffmpeg static binary
(color bars + sine tone), no network, no key.
"""

import glob
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.ingest.demux import (  # noqa: E402
    BYTES_PER_SEC,
    Demuxer,
    eof_event,
    extract_pcm,
    extract_scene_frames,
    ffmpeg_exe,
    probe_streams,
)
from live.ingest.simstream import SOURCE_LABEL, SimulatedStream, restream_to  # noqa: E402

CLIP_S = 3.0  # red 1s + blue 1s + green 1s


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """Synthetic AV clip: 3 color segments (2 hard cuts) + sine audio."""
    path = str(tmp_path_factory.mktemp("ingest") / "clip.mkv")
    proc = subprocess.run(
        [
            ffmpeg_exe(),
            "-hide_banner",
            "-nostats",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240:r=10:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:r=10:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=320x240:r=10:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "3:a",
            "-c:v",
            "mpeg4",
            "-c:a",
            "pcm_s16le",
            "-shortest",
            path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1000:]
    assert os.path.getsize(path) > 0
    return path


def test_probe_streams(clip):
    """probe finds audio + video without ffprobe."""
    assert probe_streams(clip) == {"audio": True, "video": True}


def test_extract_pcm_length(clip):
    """PCM demux yields 24 kHz mono s16 for the full duration."""
    pcm = extract_pcm(clip)
    expected = int(CLIP_S * BYTES_PER_SEC)
    assert abs(len(pcm) - expected) <= BYTES_PER_SEC // 10  # ±0.1 s


def test_scene_frames_find_cuts(clip, tmp_path):
    """Scene select finds both hard cuts with t_media near 1 s and 2 s."""
    frames = extract_scene_frames(clip, str(tmp_path), min_interval_s=0.0)
    assert len(frames) >= 2
    times = [f["t_media"] for f in frames]
    assert times == sorted(times)
    assert any(abs(t - 1.0) < 0.25 for t in times), times
    assert any(abs(t - 2.0) < 0.25 for t in times), times
    for f in frames:
        assert f["path"].endswith(".png") and os.path.getsize(f["path"]) > 0


def test_scene_frames_min_interval(clip, tmp_path):
    """Rate cap decimates: min_interval=5 s keeps a single frame."""
    many = extract_scene_frames(clip, str(tmp_path / "a"), min_interval_s=0.0)
    few = extract_scene_frames(clip, str(tmp_path / "b"), min_interval_s=5.0)
    assert len(many) >= 2
    assert len(few) == 1


def test_demuxer_streaming(clip, tmp_path):
    """Streaming Demuxer: full PCM + frame events + clean wait."""
    frames_dir = str(tmp_path / "frames")
    chunks = 0
    total = 0
    with Demuxer(clip, frames_dir, min_interval_s=0.0) as dx:
        for t_media, data in dx.iter_audio():
            assert t_media >= 0
            chunks += 1
            total += len(data)
            dx.poll_frames()  # mid-stream poll must be safe
        code = dx.wait()
        events = dx.poll_frames()
    assert code == 0
    assert chunks > 5
    assert abs(total - int(CLIP_S * BYTES_PER_SEC)) <= BYTES_PER_SEC // 10
    assert abs(dx.audio_seconds - CLIP_S) < 0.15
    assert len(events) >= 2
    for ev in events:
        assert ev["type"] == "ingest.frame"
        assert set(ev) == {"type", "t_wall", "t_media", "payload"}
        assert set(ev["payload"]) == {"t_media", "path"}
        assert ev["t_media"] == ev["payload"]["t_media"]
    eof = eof_event()
    assert set(eof) == {"type", "t_wall", "t_media", "payload"}
    assert (eof["type"], eof["t_media"], eof["payload"]) == ("ingest.eof", None, {})


def test_simstream_label_and_argv(clip):
    """Simulated stream is always labeled and always paced with -re."""
    sim = SimulatedStream(clip)
    assert sim.label == "simulated-stream" == SOURCE_LABEL
    assert "-re" in sim.argv
    assert sim.argv[sim.argv.index("-re") + 1] == "-i"


def test_simstream_paced_mpegts(clip):
    """Replay outputs MPEG-TS bytes paced to real time."""
    t0 = time.monotonic()
    total = 0
    first = None
    with SimulatedStream(clip) as sim:
        for chunk in sim.iter_chunks():
            if first is None:
                first = chunk[:1]
            total += len(chunk)
    wall = time.monotonic() - t0
    assert total > 10_000
    assert first == b"\x47"  # MPEG-TS sync byte
    assert wall >= CLIP_S - 1.0, f"replay not paced: {wall:.2f}s wall"


def test_restream_to_file(clip, tmp_path):
    """restream_to copies the clip in real time, labeled simulated."""
    dst = str(tmp_path / "out.ts")
    proc, label = restream_to(clip, dst)
    assert label == SOURCE_LABEL
    assert proc.wait(timeout=60) == 0
    assert os.path.getsize(dst) > 10_000
