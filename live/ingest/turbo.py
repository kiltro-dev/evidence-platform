"""V5-G1 Turbo mode: ingest WITHOUT realtime pacing.

``vivo`` (default) keeps the production ``ffmpeg -re`` behavior: the
source is replayed in real time. ``turbo`` drops ``-re`` (ingest as
fast as ffmpeg can demux), raises worker concurrency to the machine
maximum, and enables fast-paths (no artificial pacing sleeps, no
per-chunk latency sleeps). Nothing else changes: same demux, same ASR,
same detector, same bus — Turbo only removes the realtime governor.

``run.mode`` (``{"type","mode","pacing","source"}``) is published once
per run so the UI, logs, and replay can tell which governor was used.
"""

from __future__ import annotations

import os
import subprocess
import time

from .demux import ffmpeg_exe
from .simstream import SOURCE_LABEL

MODES = ("vivo", "turbo")
DEFAULT_MODE = "vivo"


def check_mode(mode: str) -> str:
    """Validate + normalize the run mode (ValueError on unknown)."""
    clean = (mode or "").strip().lower()
    if clean not in MODES:
        raise ValueError(f"mode debe ser vivo|turbo, recibido {mode!r}")
    return clean


def pacing_args(mode: str) -> tuple[str, ...]:
    """ffmpeg realtime-pacing args for ``mode`` (turbo = none)."""
    return ("-re",) if check_mode(mode) == "vivo" else ()


def concurrency_for_mode(mode: str) -> int:
    """Worker concurrency: 1 paced lane in vivo, CPU max in turbo."""
    if check_mode(mode) == "vivo":
        return 1
    return max(1, os.cpu_count() or 4)


def fast_paths_for_mode(mode: str) -> dict[str, bool]:
    """Which ingest fast-paths ``mode`` enables (all turbo-only)."""
    turbo = check_mode(mode) == "turbo"
    return {
        "no_realtime_pacing": turbo,  # ffmpeg runs without -re
        "max_concurrency": turbo,  # worker pool = cpu_count
        "no_pacing_sleeps": turbo,  # no artificial sleeps between chunks
    }


def run_label(mode: str, source: str) -> str:
    """Human run label (ES): Vivo / Turbo / archivo / directo simulado.

    Link origins (``link-hls`` / ``link-mp4`` / ``link-audio``) always
    show the link origin; the file/sim/microphone origins show how the
    bytes were paced. The UI badge and the exported report use this.
    """
    clean = check_mode(mode)
    src = (source or "").strip().lower()
    if src.startswith("link-"):
        return "Turbo por enlace" if clean == "turbo" else "Vivo por enlace"
    if src == "live-microphone":
        return "Micrófono en vivo"
    if src == SOURCE_LABEL:
        return "Directo simulado" if clean == "vivo" else "Turbo simulado"
    return "Turbo de archivo" if clean == "turbo" else "Archivo en tiempo real"


def mode_event(mode: str, source: str = SOURCE_LABEL) -> dict:
    """``run.mode`` bus event: which governor this run uses."""
    clean = check_mode(mode)
    return {
        "type": "run.mode",
        "t_wall": time.time(),
        "mode": clean,
        "pacing": "realtime" if clean == "vivo" else "turbo",
        "concurrency": concurrency_for_mode(clean),
        "source": source,
        "label": run_label(clean, source),
    }


def restream_argv_for_mode(src: str, dst: str, mode: str,
                           extra_args: tuple[str, ...] = ("-c", "copy"),
                           ) -> list[str]:
    """``restream_to`` argv honoring ``mode`` (turbo drops ``-re``).

    Same shape as ``live.ingest.simstream.restream_to`` so ``FifoStream``
    can swap governors with one call; the simulated-source label still
    travels alongside the process handle.
    """
    return [
        ffmpeg_exe(),
        "-hide_banner",
        "-nostats",
        *pacing_args(mode),
        "-i",
        src,
        *extra_args,
        dst,
    ]


def restream_for_mode(src: str, dst: str, mode: str,
                      extra_args: tuple[str, ...] = ("-c", "copy"),
                      ) -> tuple[subprocess.Popen, str]:
    """Replay ``src`` to ``dst`` under ``mode``'s pacing governor.

    Returns ``(proc, SOURCE_LABEL)`` exactly like ``restream_to``.
    """
    argv = restream_argv_for_mode(src, dst, mode, extra_args)
    proc = subprocess.Popen(argv, stderr=subprocess.DEVNULL)
    return proc, SOURCE_LABEL
