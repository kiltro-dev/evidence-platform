"""S3 directo-simulado: HLS loop from one synthetic piece + HTTP serving.

``hls`` builds/parses the moving-window playlist; ``serve`` mounts
``GET /hls/*`` + ``GET /hls-status`` under the Atlas Live server app.
Offline-friendly: generation uses the repo imageio-ffmpeg static binary.
"""

from live.simulive.hls import (
    CUSTOM_COMMENT,
    CUSTOM_TAG,
    HLS_LIST_SIZE,
    HLS_TIME,
    PLAYLIST_NAME,
    build_loop_cmd,
    ensure_metadata,
    parse_playlist,
)
from live.simulive.serve import hls_status, mount_simulive

__all__ = [
    "CUSTOM_COMMENT",
    "CUSTOM_TAG",
    "HLS_LIST_SIZE",
    "HLS_TIME",
    "PLAYLIST_NAME",
    "build_loop_cmd",
    "ensure_metadata",
    "hls_status",
    "mount_simulive",
    "parse_playlist",
]
