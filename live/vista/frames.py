"""M3 vista frames: scene + periodic frame extraction (Atlas Live).

One ffmpeg pass over a keynote MP4 (imageio_ffmpeg static binary): keep
frames on scene change (``gt(scene,T)``) plus a periodic heartbeat
(``gte(t-prev_selected_t,P)``), recovering ``t_sec`` from ``showinfo``
``pts_time`` — the same mechanism as ``live/ingest/demux.py`` (idea
reused read-only; this module stays dependency-free so tests need only
imageio_ffmpeg).

Each returned frame is ``{"path": str, "t_sec": float, "slide": int|None}``.
``slide`` comes from an SRT parsed with :func:`load_srt` (1-based cue
index) when ``srt_path`` resolves (default: ``<video>.srt`` next to the
video when it exists).
"""

from __future__ import annotations

import glob
import os
import re
import subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_VIDEO = os.path.join(REPO_ROOT, "data", "keynote", "keynote_en.mp4")
DEFAULT_OUT_DIR = os.path.join(REPO_ROOT, "data", "frames")

SCENE_THRESHOLD = 0.3
PERIODIC_S = 5.0
DEDUP_S = 0.4
TIMEOUT_S = 300.0

_FRAME_GLOB = "vista-*.png"
_SHOWINFO_RE = re.compile(r"pts_time:([0-9.]+)")
_SRT_CUE_RE = re.compile(
    r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)"
)


def ffmpeg_exe() -> str:
    """Static ffmpeg binary path (imageio_ffmpeg)."""
    from imageio_ffmpeg import get_ffmpeg_exe

    return get_ffmpeg_exe()


def _srt_stamp(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def load_srt(srt_path: str) -> list[tuple[float, float, str]]:
    """Parse an SRT file into ``[(t0, t1, text), ...]`` (cue order)."""
    with open(srt_path, encoding="utf-8") as f:
        raw = f.read()
    cues: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        m = _SRT_CUE_RE.search(lines[1])
        if not m:
            continue
        g = m.groups()
        t0 = _srt_stamp(*g[0:4])
        t1 = _srt_stamp(*g[4:8])
        cues.append((t0, t1, " ".join(lines[2:])))
    return cues


def slide_at(t_sec: float, cues: list[tuple[float, float, str]]) -> int | None:
    """1-based cue index containing ``t_sec`` (None when outside all cues)."""
    for i, (t0, t1, _text) in enumerate(cues, 1):
        if t0 <= t_sec < t1:
            return i
    if cues and t_sec >= cues[-1][1]:
        return len(cues)
    return None


def _resolve_srt(video_path: str, srt_path: str | None) -> str | None:
    if srt_path is not None:
        return srt_path if os.path.exists(srt_path) else None
    cand = os.path.splitext(video_path)[0] + ".srt"
    return cand if os.path.exists(cand) else None


def extract_frames(
    video_path: str = DEFAULT_VIDEO,
    out_dir: str = DEFAULT_OUT_DIR,
    scene_threshold: float = SCENE_THRESHOLD,
    periodic_s: float = PERIODIC_S,
    dedup_s: float = DEDUP_S,
    srt_path: str | None = None,
    timeout_s: float = TIMEOUT_S,
) -> list[dict]:
    """Extract scene-change + periodic frames from ``video_path``.

    Returns ``[{"path", "t_sec", "slide"}, ...]`` sorted by ``t_sec``,
    deduplicated to one frame per ``dedup_s`` window (first kept).
    ``slide`` is None when no SRT resolves. Frames land in ``out_dir``
    (``data/frames/``, git-ignored via ``data/``).
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"video not found: {video_path}")
    os.makedirs(out_dir, exist_ok=True)
    for stale in glob.glob(os.path.join(out_dir, _FRAME_GLOB)):
        try:
            os.remove(stale)
        except OSError:
            pass
    filt = (
        "select='gt(scene,%s)+isnan(prev_selected_t)"
        "+gte(t-prev_selected_t\\,%s)',showinfo"
    ) % (scene_threshold, periodic_s)
    proc = subprocess.run(
        [
            ffmpeg_exe(),
            "-hide_banner",
            "-nostats",
            "-y",
            "-i",
            video_path,
            "-map",
            "0:v?",
            "-vf",
            filt,
            "-vsync",
            "vfr",
            os.path.join(out_dir, "vista-%06d.png"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extract failed: {(proc.stderr or '')[-500:]}")
    pts = [float(m.group(1)) for m in _SHOWINFO_RE.finditer(proc.stderr or "")]
    files = sorted(glob.glob(os.path.join(out_dir, _FRAME_GLOB)))
    # Pair in order (showinfo only sees surviving frames, same as demux.py).
    # out_dir was cleared of this pattern above, so files are this run's.
    n = min(len(pts), len(files))
    pairs = sorted(zip(pts[:n], files[:n]))
    cues: list[tuple[float, float, str]] = []
    resolved = _resolve_srt(video_path, srt_path)
    if resolved is not None:
        cues = load_srt(resolved)
    frames: list[dict] = []
    last_kept: float | None = None
    for t, path in pairs:
        if last_kept is not None and t - last_kept < dedup_s:
            continue
        last_kept = t
        frames.append({
            "path": path,
            "t_sec": round(t, 3),
            "slide": slide_at(t, cues) if cues else None,
        })
    return frames


def closest_frame(frames: list[dict], t_sec: float) -> dict | None:
    """Frame nearest to ``t_sec`` (None when the list is empty)."""
    if not frames:
        return None
    return min(frames, key=lambda f: abs(float(f["t_sec"]) - t_sec))
