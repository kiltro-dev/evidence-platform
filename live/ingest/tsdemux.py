"""V6-1 TS bridge: the static ffmpeg READS mp4/MKV but SEGFAULTS on TS.

Documented twice already (``run_live.FifoStream``: rc=139 on any
MPEG-TS demux; V5_LINK: rc=-11 on any https input). HLS windows are
usually MPEG-TS, so after :mod:`live.ingest.hlsfetch` materializes the
segments locally they are remuxed (no re-encode) to Matroska with PyAV
— whose bundled FFmpeg libs demux TS fine — and the existing pipeline
ingests the MKV unchanged. Media clock still derives from the PCM
byte offset, so timestamp resets at segment joints do not matter.
"""

from __future__ import annotations

import os

import av

TS_PACKET = 188


def is_ts(path: str) -> bool:
    """True when ``path`` smells like MPEG-TS (0x47 sync pattern)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(TS_PACKET * 4 + 1)
    except OSError:
        return False
    if len(head) < TS_PACKET * 2 + 1:
        return False
    return (head[0] == 0x47 and head[TS_PACKET] == 0x47
            and head[TS_PACKET * 2] == 0x47)


def probe_streams_ts(path: str) -> dict[str, bool]:
    """Stream presence via PyAV (never feeds TS to the static binary)."""
    with av.open(path) as container:
        types = {s.type for s in container.streams}
    return {"audio": "audio" in types, "video": "video" in types}


def remux_to_mkv(src: str, dst: str) -> dict:
    """Remux ``src`` (TS window or anything PyAV reads) to MKV ``dst``.

    Stream-copy, no re-encode. Returns
    ``{"path","bytes","audio","video"}``. Raises ``RuntimeError`` when
    PyAV cannot read the source.
    """
    tmp = f"{dst}.part"
    try:
        with av.open(src) as src_c:
            if not src_c.streams:
                raise RuntimeError(f"sin streams legibles en {src!r}")
            with av.open(tmp, "w", format="matroska") as dst_c:
                streams = [dst_c.add_stream_from_template(template=s)
                           for s in src_c.streams]
                for packet in src_c.demux():
                    if packet.dts is None:
                        continue
                    packet.stream = streams[packet.stream.index]
                    dst_c.mux(packet)
    except av.FFmpegError as exc:
        raise RuntimeError(f"remux PyAV falló para {src!r}: {exc}") from exc
    os.replace(tmp, dst)
    info = probe_streams_ts(dst)
    return {"path": dst, "bytes": os.path.getsize(dst),
            "audio": info["audio"], "video": info["video"]}
