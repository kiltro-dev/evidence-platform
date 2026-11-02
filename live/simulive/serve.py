"""Serve the S3 simulive HLS dir under the Atlas Live server app.

Mounted routes (additive; M1b/M4 contract untouched):

- ``GET /hls/{name}`` — playlist (``.m3u8``, served with the injected
  ``X-ATLAS-SIMULIVE:1`` metadata) and segments (``.ts``). Strict
  allowlist: basenames of ``[A-Za-z0-9_.-]+`` ending in ``.m3u8`` or
  ``.ts``, resolved inside the HLS dir (no traversal).
- ``GET /hls-status`` — ``{live, seq, segments, uptime_s, playlist}``:
  current media sequence + mount uptime.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response

from live.simulive.hls import (
    HLS_DIR_DEFAULT,
    PLAYLIST_NAME,
    ensure_metadata,
    parse_playlist,
)

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.(m3u8|ts)")

_mounted_at: float | None = None


def hls_status(hls_dir: str | Path = HLS_DIR_DEFAULT) -> dict[str, Any]:
    """Current window state: seq actual + uptime since mount."""
    playlist = Path(hls_dir) / PLAYLIST_NAME
    now = time.monotonic()
    base: dict[str, Any] = {
        "live": False,
        "seq": None,
        "segments": 0,
        "uptime_s": round(now - _mounted_at, 1) if _mounted_at else 0.0,
        "playlist": f"/hls/{PLAYLIST_NAME}",
    }
    if not playlist.is_file():
        return base
    try:
        parsed = parse_playlist(playlist.read_text(encoding="utf-8"))
    except OSError:
        return base
    base.update(
        live=True,
        seq=parsed["seq"],
        segments=len(parsed["segments"]),
    )
    return base


def mount_simulive(
    app: FastAPI, hls_dir: str | Path = HLS_DIR_DEFAULT
) -> FastAPI:
    """Mount ``/hls/*`` + ``/hls-status`` on ``app`` (returns ``app``)."""
    global _mounted_at
    if _mounted_at is None:
        _mounted_at = time.monotonic()
    root = Path(hls_dir)

    @app.get("/hls-status")
    async def simulive_status() -> dict[str, Any]:
        return hls_status(root)

    @app.get("/hls/{name}", response_model=None)
    async def simulive_file(name: str):
        if not _NAME_RE.fullmatch(name or ""):
            return JSONResponse(
                {"error": f"unknown hls file {name!r}"}, status_code=404
            )
        path = (root / name).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            return JSONResponse(
                {"error": f"unknown hls file {name!r}"}, status_code=404
            )
        if not path.is_file():
            return JSONResponse(
                {"error": f"missing hls file {name!r}"}, status_code=404
            )
        if name.endswith(".m3u8"):
            # Inject custom metadata on every serve: the moving window
            # ffmpeg rewrites always carries it, with zero loop edits.
            text = ensure_metadata(path.read_text(encoding="utf-8"))
            return Response(
                text, media_type="application/vnd.apple.mpegurl"
            )
        return FileResponse(path, media_type="video/mp2t", filename=name)

    return app
