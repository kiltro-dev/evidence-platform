"""V6-1 HLS ingest without feeding https to the static ffmpeg.

The imageio static ffmpeg segfaults (rc=-11) on ANY https input, so
remote playlists are materialized in Python: fetch the (master) .m3u8
over SSRF-guarded urllib, pick the lowest-bandwidth variant, download
the init segment + media segments with the same per-URL SSRF/redirect
discipline as :mod:`live.ingest.resolve`, and concatenate them into one
local file the static ffmpeg can demux (byte-concat is valid for both
MPEG-TS segments and fmp4 init+segments).

Live windows (no ``EXT-X-ENDLIST``) are capped to ``max_segments``.
Encrypted playlists (``EXT-X-KEY`` with a real method) are refused.
DASH stays unsupported (no robust dep-free segmenter here).
"""

from __future__ import annotations

import os
import re
from urllib.parse import urljoin

from .resolve import (LinkBlocked, LinkError, MIN_HOST_INTERVAL_S, TIMEOUT_S,
                      check_url_public, download_media, fetch_page)

PLAYLIST_MAX_BYTES = 1 * 1024 * 1024
DEFAULT_MAX_SEGMENTS = 48
DEFAULT_MAX_BYTES = 200 * 1024 * 1024

_TAG_RE = re.compile(r'#EXT-X-STREAM-INF:(?P<attrs>.*)$')
_BW_RE = re.compile(r"BANDWIDTH=(\d+)", re.IGNORECASE)
_MAP_RE = re.compile(r'#EXT-X-MAP:\s*URI="(?P<uri>[^"]+)"')
_KEY_RE = re.compile(r'#EXT-X-KEY:\s*METHOD=(?P<method>[^,\s]+)', re.IGNORECASE)


def parse_m3u8(text: str) -> dict:
    """Parse an HLS playlist into variants/segments/init/live/encrypted."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("##")]
    if not lines or lines[0] != "#EXTM3U":
        raise LinkError("no es playlist HLS válida (falta #EXTM3U)")
    variants: list[dict] = []
    segments: list[str] = []
    init: str | None = None
    encrypted = False
    endlist = False
    pending_bw: int | None = None
    expect_variant = False
    for line in lines[1:]:
        if line.startswith("#EXT-X-STREAM-INF"):
            m = _BW_RE.search(line)
            pending_bw = int(m.group(1)) if m else None
            expect_variant = True
            continue
        if line.startswith("#EXT-X-KEY"):
            m = _KEY_RE.search(line)
            method = (m.group("method") if m else "").upper().strip("\"'")
            if method and method != "NONE":
                encrypted = True
            continue
        if line.startswith("#EXT-X-MAP"):
            m = _MAP_RE.search(line)
            if m:
                init = m.group("uri")
            continue
        if line == "#EXT-X-ENDLIST":
            endlist = True
            continue
        if line.startswith("#"):
            continue
        if expect_variant:
            variants.append({"uri": line, "bandwidth": pending_bw})
            expect_variant, pending_bw = False, None
        else:
            segments.append(line)
    return {
        "is_master": bool(variants),
        "variants": variants,
        "segments": segments,
        "init": init,
        "encrypted": encrypted,
        "endlist": endlist,
        "live": not endlist and bool(segments),
    }


def pick_variant(parsed: dict) -> str:
    """Lowest-bandwidth variant URI (bounded cost; documented choice)."""
    variants = parsed.get("variants") or []
    if not variants:
        raise LinkError("playlist maestra sin variantes")
    best = min(variants,
               key=lambda v: v["bandwidth"]
               if v["bandwidth"] is not None else 10 ** 18)
    return best["uri"]


def _default_page_fetcher(url: str, **kw):
    return fetch_page(url, max_bytes=PLAYLIST_MAX_BYTES,
                      min_interval_s=MIN_HOST_INTERVAL_S, **kw)


def fetch_hls_local(playlist_url: str, dest_path: str, *,
                    max_segments: int = DEFAULT_MAX_SEGMENTS,
                    max_bytes: int = DEFAULT_MAX_BYTES,
                    timeout_s: float = TIMEOUT_S,
                    min_interval_s: float = 0.0,
                    dns_resolver=None, robots_checker=None,
                    page_fetcher=None, downloader=None) -> dict:
    """Materialize an HLS playlist window to ``dest_path`` (local file).

    Every URL (playlist, variant, init, each segment) passes
    :func:`check_url_public` with the caller's ``dns_resolver``; the
    default ``downloader`` re-applies redirect/SSRF discipline per hop.
    Returns ``{"path","bytes","segments","variant","live","truncated"}``.
    """
    import tempfile

    get_page = page_fetcher or (lambda u: _default_page_fetcher(
        u, timeout_s=timeout_s, dns_resolver=dns_resolver,
        robots_checker=robots_checker))
    do_download = downloader or (lambda u, d: download_media(
        u, d, timeout_s=timeout_s, max_bytes=max_bytes,
        min_interval_s=min_interval_s, dns_resolver=dns_resolver))

    final_pl, _ctype, body = get_page(playlist_url)
    try:
        text = body.decode("utf-8", "replace")
    except Exception:
        text = ""
    parsed = parse_m3u8(text)
    if parsed["encrypted"]:
        raise LinkError(f"{final_pl}: playlist cifrada (EXT-X-KEY), "
                        f"no soportada")

    variant_url = final_pl
    if parsed["is_master"]:
        # Cheapest-first with fallback: CDNs rotate variants, so a
        # listed variant may 404 — try each in bandwidth order.
        ordered = sorted(
            parsed["variants"],
            key=lambda v: v["bandwidth"]
            if v["bandwidth"] is not None else 10 ** 18)
        errors: list[str] = []
        parsed = None  # type: ignore[assignment]
        for cand in ordered:
            candidate = urljoin(final_pl, cand["uri"])
            try:
                check_url_public(candidate, dns_resolver=dns_resolver)
                variant_url, _ctype, vbody = get_page(candidate)
            except LinkBlocked:
                raise  # policy refusals are final, not skipped
            except LinkError as exc:
                errors.append(f"{candidate}: {exc}")
                continue
            try:
                text = vbody.decode("utf-8", "replace")
            except Exception:
                text = ""
            try:
                parsed = parse_m3u8(text)
            except LinkError as exc:
                errors.append(f"{candidate}: {exc}")
                continue
            if parsed["encrypted"]:
                errors.append(f"{candidate}: variante cifrada")
                parsed = None  # type: ignore[assignment]
                continue
            if parsed["is_master"]:
                errors.append(f"{candidate}: maestra anidada")
                parsed = None  # type: ignore[assignment]
                continue
            break
        if parsed is None:
            detail = "; ".join(errors) if errors else "sin variantes"
            raise LinkError(f"{final_pl}: ninguna variante usable: {detail}")

    segments = parsed["segments"]
    truncated = False
    if len(segments) > max_segments:
        segments = segments[:max_segments]
        truncated = True
    if not segments:
        raise LinkError(f"{variant_url}: sin segmentos descargables")

    urls: list[str] = []
    if parsed["init"]:
        urls.append(urljoin(variant_url, parsed["init"]))
    urls.extend(urljoin(variant_url, s) for s in segments)
    for one in urls:
        check_url_public(one, dns_resolver=dns_resolver)

    tmp_dir = tempfile.mkdtemp(prefix="hls-seg-")
    total = 0
    tmp_path = f"{dest_path}.part"
    try:
        with open(tmp_path, "wb") as out:
            for idx, one in enumerate(urls):
                seg_path = os.path.join(tmp_dir, f"seg{idx}.part")
                # per-segment cap = remaining global budget
                info = do_download(one, seg_path)
                size = int(info.get("size", 0))
                total += size
                if total > max_bytes:
                    raise LinkBlocked(
                        "too-large",
                        f"ventana HLS supera el cap de {max_bytes} bytes")
                with open(seg_path, "rb") as fh:
                    while True:
                        chunk = fh.read(64 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
    finally:
        for name in os.listdir(tmp_dir):
            try:
                os.unlink(os.path.join(tmp_dir, name))
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
    os.replace(tmp_path, dest_path)
    return {"path": dest_path, "bytes": total, "segments": len(segments),
            "variant": variant_url, "live": parsed["live"],
            "truncated": truncated}


def materialize_media(media_url: str, media_kind: str, dest_dir: str, *,
                      prefix: str = "link", timeout_s: float = TIMEOUT_S,
                      dns_resolver=None, robots_checker=None,
                      page_fetcher=None, downloader=None) -> dict:
    """Local path for link media the static ffmpeg can ingest.

    ``mp4``/``audio`` stream straight to disk; ``hls`` goes through
    :func:`fetch_hls_local`; ``dash`` is refused (no dep-free
    segmenter). Returns ``{"path", ...}`` with ``path`` always local.
    """
    os.makedirs(dest_dir, exist_ok=True)
    if media_kind in ("mp4", "audio"):
        dest = os.path.join(dest_dir, f"{prefix}.media")
        do_download = downloader or (lambda u, d: download_media(
            u, d, timeout_s=timeout_s, dns_resolver=dns_resolver))
        return do_download(media_url, dest)
    if media_kind == "hls":
        from .tsdemux import is_ts, remux_to_mkv

        dest = os.path.join(dest_dir, f"{prefix}.media")
        out = fetch_hls_local(
            media_url, dest, timeout_s=timeout_s, dns_resolver=dns_resolver,
            robots_checker=robots_checker, page_fetcher=page_fetcher,
            downloader=downloader)
        # TS windows would segfault the static ffmpeg on read: remux
        # to MKV (no re-encode) so the pipeline ingests a safe path.
        if is_ts(out["path"]):
            mkv = remux_to_mkv(out["path"],
                               os.path.join(dest_dir, f"{prefix}.mkv"))
            out["window_path"] = out["path"]
            out["path"] = mkv["path"]
        return out
    raise LinkError(f"playlists {media_kind} aún no soportadas: sin "
                    f"segmentador DASH en esta build")
