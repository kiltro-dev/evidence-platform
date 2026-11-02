"""S3 directo-simulado: HLS VOD-loop generation + playlist helpers.

Live loop (one corpus piece, infinite): the ffmpeg command built by
:func:`build_loop_cmd` replays ``data/synth/en/k1_econ.mp4`` with
``-re -stream_loop -1`` and segments it into ``data/synth/hls/`` as a
moving window (``-hls_flags delete_segments+append_list``, ``hls_time``
4 s, ``hls_list_size`` 10). ffmpeg never emits our custom lines, so
:func:`ensure_metadata` injects them whenever the playlist is served
(see ``live.simulive.serve``) or materialized for tests.

Offline test path: :func:`generate_short_hls` builds a few seconds of
real HLS from a tiny synthetic mp4 in a tmp dir (repo imageio-ffmpeg
static binary, $0). :func:`write_live_window` rewrites a playlist with
live-window semantics (rising ``EXT-X-MEDIA-SEQUENCE``, no ENDLIST,
optional ``EXT-X-DISCONTINUITY`` on trim) so mid-join / pause tests do
not need a long-running ffmpeg.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from live.ingest.demux import ffmpeg_exe

#: Default loop source: one EN synthetic piece (SYNTH_EN.md).
SOURCE_DEFAULT = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "synth"
    / "en"
    / "k1_econ.mp4"
)
#: Default HLS output dir (gitignored data/).
HLS_DIR_DEFAULT = (
    Path(__file__).resolve().parent.parent.parent / "data" / "synth" / "hls"
)
PLAYLIST_NAME = "stream.m3u8"

#: Custom manifest metadata (injected by ensure_metadata on serve).
CUSTOM_TAG = "X-ATLAS-SIMULIVE:1"
CUSTOM_COMMENT = "# ATLAS simulive loop"

HLS_TIME = 4
HLS_LIST_SIZE = 10

_SEG_RE = re.compile(r"^(?!#)(\S+\.ts)\s*$")


def build_loop_cmd(
    src: str | Path = SOURCE_DEFAULT,
    out_dir: str | Path = HLS_DIR_DEFAULT,
    hls_time: int = HLS_TIME,
    hls_list_size: int = HLS_LIST_SIZE,
) -> list[str]:
    """ffmpeg argv for the infinite VOD-loop -> HLS moving window."""
    playlist = Path(out_dir) / PLAYLIST_NAME
    return [
        ffmpeg_exe(),
        "-hide_banner",
        "-nostats",
        "-re",
        "-stream_loop",
        "-1",
        "-i",
        str(src),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        # HLS can only cut at keyframes: force one per segment so the
        # window advances at hls_time regardless of source GOP.
        "-force_key_frames",
        f"expr:gte(t,n_forced*{hls_time})",
        "-f",
        "hls",
        "-hls_time",
        str(hls_time),
        "-hls_list_size",
        str(hls_list_size),
        "-hls_flags",
        "delete_segments+append_list",
        "-hls_segment_filename",
        str(Path(out_dir) / "seg-%05d.ts"),
        str(playlist),
    ]


def start_loop(
    src: str | Path = SOURCE_DEFAULT,
    out_dir: str | Path = HLS_DIR_DEFAULT,
    **kwargs: Any,
) -> subprocess.Popen:
    """Spawn the infinite loop; caller owns the handle (terminate it)."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        build_loop_cmd(src, out_dir, **kwargs),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def ensure_metadata(text: str) -> str:
    """Insert CUSTOM tag + comment after ``#EXTM3U`` when missing."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "#EXTM3U":
        return text
    if CUSTOM_TAG in text and CUSTOM_COMMENT in text:
        return text
    insert = [lines[0]]
    if CUSTOM_TAG not in text:
        insert.append(f"#EXT-{CUSTOM_TAG}")
    if CUSTOM_COMMENT not in text:
        insert.append(CUSTOM_COMMENT)
    return "\n".join(insert + lines[1:]) + "\n"


def parse_playlist(text: str) -> dict[str, Any]:
    """Parse a playlist into seq/segments/flags (never raises)."""
    seq = 0
    segments: list[str] = []
    target = 0.0
    endlist = False
    discontinuity = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                seq = int(line.split(":", 1)[1])
            except ValueError:
                pass
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            try:
                target = float(line.split(":", 1)[1])
            except ValueError:
                pass
        elif line == "#EXT-X-ENDLIST":
            endlist = True
        elif line == "#EXT-X-DISCONTINUITY":
            discontinuity = True
        else:
            m = _SEG_RE.match(line)
            if m:
                segments.append(m.group(1))
    return {
        "seq": seq,
        "segments": segments,
        "target_duration": target,
        "endlist": endlist,
        "discontinuity": discontinuity,
        "has_custom": CUSTOM_TAG in text,
        "has_comment": CUSTOM_COMMENT in text,
    }


def generate_tiny_mp4(path: str | Path, duration_s: float = 5.0) -> Path:
    """Render a tiny synthetic mp4 (testsrc + sine audio) for tests."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg_exe(),
            "-hide_banner",
            "-nostats",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=128x72:rate=10:duration={duration_s}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration_s}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    if proc.returncode != 0 or not path.is_file():
        raise RuntimeError(
            f"tiny mp4 failed: {proc.stderr.decode()[-500:]}"
        )
    return path


def generate_short_hls(
    workdir: str | Path,
    duration_s: float = 5.0,
    hls_time: int = 1,
) -> Path:
    """Tiny mp4 -> real HLS segments + metadata-injected playlist.

    Returns the playlist path. VOD-style (full list + ENDLIST); use
    :func:`write_live_window` to reshape it into a live moving window.
    """
    workdir = Path(workdir)
    src = generate_tiny_mp4(workdir / "tiny.mp4", duration_s)
    playlist = workdir / PLAYLIST_NAME
    proc = subprocess.run(
        [
            ffmpeg_exe(),
            "-hide_banner",
            "-nostats",
            "-y",
            "-i",
            str(src),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-g",
            "10",
            "-keyint_min",
            "10",
            "-f",
            "hls",
            "-hls_time",
            str(hls_time),
            "-hls_list_size",
            "0",
            "-hls_segment_filename",
            str(workdir / "seg-%05d.ts"),
            str(playlist),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=180,
    )
    if proc.returncode != 0 or not playlist.is_file():
        raise RuntimeError(
            f"short HLS failed: {proc.stderr.decode()[-500:]}"
        )
    playlist.write_text(
        ensure_metadata(playlist.read_text(encoding="utf-8")), encoding="utf-8"
    )
    return playlist


def write_live_window(
    playlist: str | Path,
    segments: list[str],
    seq_start: int,
    target_duration: float = 4.0,
    discontinuity_before: set[int] | None = None,
) -> str:
    """Rewrite ``playlist`` with live-window semantics; return its text.

    Drops ENDLIST, sets ``EXT-X-MEDIA-SEQUENCE`` to ``seq_start`` and
    lists ``segments`` verbatim. ``discontinuity_before`` holds segment
    indices that start after a trim/loop point (each gets an
    ``EXT-X-DISCONTINUITY`` line). Metadata lines always injected.
    """
    playlist = Path(playlist)
    disc = discontinuity_before or set()
    lines = ["#EXTM3U", "#EXT-X-VERSION:3",
             f"#EXT-X-TARGETDURATION:{int(target_duration)}",
             f"#EXT-X-MEDIA-SEQUENCE:{seq_start}"]
    for i, seg in enumerate(segments):
        if i in disc:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(f"#EXTINF:{target_duration:.1f},")
        lines.append(seg)
    text = ensure_metadata("\n".join(lines) + "\n")
    playlist.write_text(text, encoding="utf-8")
    return text
