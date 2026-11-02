"""V5-G1 link ingest: resolve a page URL to a direct media URL.

Pipeline: validate scheme/host -> SSRF check (DNS resolve + ``ipaddress``
private/loopback/link-local/reserved rejection, after EVERY redirect, max
5 redirects) -> robots.txt + per-host rate throttle -> GET page (2 MB cap)
-> extract direct media (``og:video``, ``<video src>`` / ``<source src>``,
links ending in ``.m3u8`` / ``.mpd`` / ``.mp4``) -> HEAD verify
content-type + size (500 MB cap).

YouTube (``youtube.*`` / ``youtu.be``) is ALWAYS refused with a clear
error: no YouTube downloads, embeds, or transcripts in this project.

All network seams are injectable (``page_fetcher``, ``head_checker``,
``dns_resolver``, ``robots_checker``) so ``live/tests/test_v5_link.py``
stays fully offline. Blocking follows the ``live.swarm.fetch``
vocabulary: :class:`LinkBlocked` (policy refusal, carries ``reason``)
vs :class:`LinkError` (any other resolution failure).
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

USER_AGENT = "atlas-live-link"
TIMEOUT_S = 15.0
MAX_REDIRECTS = 5
PAGE_MAX_BYTES = 2 * 1024 * 1024
MEDIA_MAX_BYTES = 500 * 1024 * 1024  # 500 MB cap on resolved media
CHUNK_BYTES = 64 * 1024
MIN_HOST_INTERVAL_S = 1.0  # polite per-host throttle between GETs

MEDIA_EXTENSIONS = (".m3u8", ".mpd", ".mp4")

# Hosts that always refuse (YouTube embargo: no downloads/embeds here).
YOUTUBE_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be", "www.youtu.be",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
})

_HLS_TYPES = {"application/vnd.apple.mpegurl", "application/x-mpegurl"}
_DASH_TYPES = {"application/dash+xml"}
_VIDEO_TYPES = {"video/mp4", "video/webm", "video/x-matroska", "video/quicktime"}
_AUDIO_TYPES = {"audio/mpeg", "audio/mp4", "audio/ogg", "audio/webm",
                "audio/wav", "audio/x-wav"}
_OCTET_TYPES = {"application/octet-stream", "binary/octet-stream"}
_ALLOWED_MEDIA_TYPES = (_HLS_TYPES | _DASH_TYPES | _VIDEO_TYPES
                        | _AUDIO_TYPES | _OCTET_TYPES)


class LinkError(RuntimeError):
    """Any link-resolution failure (network, no media found, bad HEAD)."""


class LinkBlocked(LinkError):
    """Link refused on policy grounds (SSRF, scheme, robots, YouTube, size).

    ``reason`` is a short machine-readable tag (``"ssrf"``, ``"scheme"``,
    ``"youtube"``, ``"robots"``, ``"rate"``, ``"too-large"``); the message
    carries the human-readable detail for the UI.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ResolvedMedia:
    url: str  # final direct media URL (after redirects)
    kind: str  # "hls" | "dash" | "mp4" | "audio"
    content_type: str
    size: int | None  # Content-Length when the server sent one
    page_url: str  # the input URL the media was resolved from


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------

def _host_is_youtube(host: str) -> bool:
    h = (host or "").lower().rstrip(".")
    return h in YOUTUBE_HOSTS


def resolve_host_ips(host: str, dns_resolver=None) -> list[str]:
    """All IP strings for ``host`` (numeric literals pass through)."""
    if dns_resolver is not None:
        return [str(ip) for ip in dns_resolver(host)]
    infos = socket.getaddrinfo(host, None, family=socket.AF_UNSPEC,
                               type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def check_url_public(url: str, *, dns_resolver=None) -> str:
    """Policy-gate one URL: scheme + YouTube + DNS -> public-IP check.

    Returns the URL unchanged. Raises :class:`LinkBlocked` with a typed
    reason on any refusal. Call AFTER every redirect hop.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise LinkBlocked("bad-url", f"URL no válida {url!r}: {exc}")
    if parts.scheme not in ("http", "https"):
        raise LinkBlocked(
            "scheme",
            f"solo se permiten enlaces http(s); recibido {parts.scheme or '?'}://",
        )
    host = parts.hostname or ""
    if not host:
        raise LinkBlocked("bad-url", f"enlace sin host {url!r}")
    if _host_is_youtube(host):
        raise LinkBlocked(
            "youtube",
            "YouTube no está permitido: pega un enlace directo "
            "(mp4/m3u8/mpd) o una página con vídeo embebido.",
        )
    try:
        ips = resolve_host_ips(host, dns_resolver)
    except LinkBlocked:
        raise
    except Exception as exc:
        raise LinkError(f"no se pudo resolver {host!r}: {exc}") from exc
    if not ips:
        raise LinkError(f"DNS sin registros para {host!r}")
    for raw in ips:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise LinkBlocked("ssrf", f"IP no válida {raw!r} para {host!r}")
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise LinkBlocked(
                "ssrf",
                f"IP no pública {ip} para {host!r}: solo hosts públicos",
            )
    return url


# ---------------------------------------------------------------------------
# polite fetch: robots.txt + per-host throttle + manual redirects
# ---------------------------------------------------------------------------

_throttle_lock = threading.Lock()
_last_host_hit: dict[str, float] = {}


def throttle_host(host: str, min_interval_s: float = MIN_HOST_INTERVAL_S,
                  _now: float | None = None,
                  _sleep=None) -> None:
    """Sleep so two GETs to one host keep ``min_interval_s`` apart."""
    if min_interval_s <= 0:
        return
    now = time.monotonic() if _now is None else _now
    sleeper = time.sleep if _sleep is None else _sleep
    with _throttle_lock:
        last = _last_host_hit.get(host, 0.0)
        wait = min_interval_s - (now - last)
        if wait > 0:
            sleeper(wait)
            now = time.monotonic() if _now is None else _now + wait
        _last_host_hit[host] = now


def clear_throttle() -> None:
    """Drop throttle state (tests)."""
    with _throttle_lock:
        _last_host_hit.clear()


def default_robots_checker(url: str) -> bool:
    """True when robots.txt allows ``url`` (fail-open on net errors).

    Reuses ``live.swarm.fetch``'s cached robots parser + UA semantics;
    an explicit Disallow always blocks (fail closed on policy).
    """
    from live.swarm import fetch as swarm_fetch

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    origin = f"{parts.scheme}://{parts.hostname}"
    if parts.port:
        origin += f":{parts.port}"
    parser = swarm_fetch._robots_parser(origin)
    if parser is None:
        return True  # unknown (net error): proceed, same as swarm fetch
    try:
        return bool(parser.can_fetch(USER_AGENT, url))
    except Exception:
        return True


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """urllib handler that surfaces 3xx instead of following them."""

    def http_error_302(self, req, fp, code, msg, headers):
        return _RedirectResponse(req, fp, code, msg, headers)

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


class _RedirectResponse:
    def __init__(self, req, fp, code, msg, headers):
        self.req, self.fp, self.code, self.msg, self.headers = (
            req, fp, code, msg, headers)
        self.status = code

    def geturl(self):
        return self.req.get_full_url()

    def read(self, *a):
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.fp.close()
        except Exception:
            pass
        return False


def _open_no_redirect(req: Request, timeout_s: float):
    opener = urllib.request.build_opener(_NoRedirectHandler())
    opener.addheaders = list(req.header_items())
    return opener.open(req, timeout=timeout_s)


def fetch_page(url: str, *, timeout_s: float = TIMEOUT_S,
               max_bytes: int = PAGE_MAX_BYTES,
               min_interval_s: float = MIN_HOST_INTERVAL_S,
               dns_resolver=None, robots_checker=None,
               opener=None) -> tuple[str, str, bytes]:
    """GET ``url`` politely; return ``(final_url, content_type, body)``.

    SSRF-checked before the first hop and after every redirect (cap
    ``MAX_REDIRECTS``). ``opener`` is an injectable
    ``(request, timeout) -> response`` double for offline tests.
    """
    robots_ok = (robots_checker or default_robots_checker)
    current = url.strip()
    for _hop in range(MAX_REDIRECTS + 1):
        check_url_public(current, dns_resolver=dns_resolver)
        host = urlsplit(current).hostname or ""
        if not robots_ok(current):
            raise LinkBlocked("robots",
                              f"robots.txt no permite leer {current!r}")
        throttle_host(host, min_interval_s)
        req = Request(current, headers={"User-Agent": USER_AGENT})  # noqa: S310
        do_open = opener or _open_no_redirect
        try:
            with do_open(req, timeout_s) as resp:
                status = getattr(resp, "status", 200) or 200
                if status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "")
                    if not location:
                        raise LinkError(
                            f"redirect sin Location para {current!r}")
                    current = urljoin(current, location)
                    continue
                if status in (401, 402, 403, 429):
                    raise LinkBlocked(
                        "paywall",
                        f"HTTP {status} (paywall/login) para {current!r}")
                if status >= 400:
                    raise LinkError(f"HTTP {status} para {current!r}")
                content_type = resp.headers.get("Content-Type", "")
                body = bytearray()
                while True:
                    chunk = resp.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    body += chunk
                    if len(body) > max_bytes:
                        raise LinkError(
                            f"página supera {max_bytes} bytes: {current!r}")
                final = resp.geturl() or current
                check_url_public(final, dns_resolver=dns_resolver)
                return final, content_type, bytes(body)
        except LinkError:
            raise
        except HTTPError as exc:
            raise LinkError(f"HTTP {exc.code} para {current!r}") from exc
        except (URLError, TimeoutError, ValueError, OSError) as exc:
            raise LinkError(f"falló GET {current!r}: {exc}") from exc
    raise LinkError(f"demasiados redirects (>{MAX_REDIRECTS}) para {url!r}")


def head_media(url: str, *, timeout_s: float = TIMEOUT_S,
               dns_resolver=None, opener=None) -> tuple[str, str, int | None]:
    """HEAD ``url``; return ``(final_url, content_type, size_or_None)``.

    Follows up to ``MAX_REDIRECTS`` with an SSRF check per hop. A 405/501
    (HEAD no soportado) passes through with unknown type/size so strict
    servers still work; the kind then comes from the URL extension.
    """
    current = url.strip()
    for _hop in range(MAX_REDIRECTS + 1):
        check_url_public(current, dns_resolver=dns_resolver)
        req = Request(current, headers={"User-Agent": USER_AGENT},  # noqa: S310
                      method="HEAD")
        do_open = opener or _open_no_redirect
        try:
            with do_open(req, timeout_s) as resp:
                status = getattr(resp, "status", 200) or 200
                if status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "")
                    if not location:
                        raise LinkError(
                            f"redirect sin Location para {current!r}")
                    current = urljoin(current, location)
                    continue
                if status in (405, 501):
                    return current, "", None
                if status >= 400:
                    raise LinkError(f"HEAD HTTP {status} para {current!r}")
                content_type = resp.headers.get("Content-Type", "")
                size: int | None = None
                raw_len = resp.headers.get("Content-Length", "")
                if str(raw_len or "").strip().isdigit():
                    size = int(raw_len)
                    if size > MEDIA_MAX_BYTES:
                        raise LinkBlocked(
                            "too-large",
                            f"medio de {size} bytes supera el cap de "
                            f"{MEDIA_MAX_BYTES} bytes (500 MB)",
                        )
                final = resp.geturl() or current
                check_url_public(final, dns_resolver=dns_resolver)
                return final, content_type, size
        except LinkError:
            raise
        except HTTPError as exc:
            if exc.code in (405, 501):
                return current, "", None
            raise LinkError(f"HEAD HTTP {exc.code} para {current!r}") from exc
        except (URLError, TimeoutError, ValueError, OSError) as exc:
            raise LinkError(f"falló HEAD {current!r}: {exc}") from exc
    raise LinkError(f"demasiados redirects (>{MAX_REDIRECTS}) para {url!r}")


# ---------------------------------------------------------------------------
# media extraction
# ---------------------------------------------------------------------------

class _MediaHTMLParser(HTMLParser):
    """Collect og:video / video+source src / media-extension links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.og_video: list[str] = []
        self.video_src: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attrs_l = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            prop = attrs_l.get("property", "").lower()
            if prop in ("og:video", "og:video:url", "og:video:secure_url",
                        "twitter:player:stream"):
                content = attrs_l.get("content", "").strip()
                if content:
                    self.og_video.append(content)
        elif tag in ("video", "source", "audio"):
            src = attrs_l.get("src", "").strip()
            if src:
                self.video_src.append(src)
        elif tag in ("a", "link"):
            href = attrs_l.get("href", "").strip()
            if href and _path_has_media_ext(href):
                self.links.append(href)


def _path_has_media_ext(url: str) -> bool:
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return False
    return path.endswith(MEDIA_EXTENSIONS)


def extract_media_candidates(html: str, base_url: str) -> list[str]:
    """Ordered direct-media candidates (og:video > video/src > links)."""
    parser = _MediaHTMLParser()
    try:
        parser.feed(html)
    except Exception:
        pass  # best effort: keep whatever was collected so far
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in parser.og_video + parser.video_src + parser.links:
        absolute = urljoin(base_url, raw)
        if absolute not in seen:
            seen.add(absolute)
            ordered.append(absolute)
    return ordered


def kind_for_media(url: str, content_type: str = "") -> str:
    """Media kind from content-type (preferred) or URL extension."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in _HLS_TYPES:
        return "hls"
    if ctype in _DASH_TYPES:
        return "dash"
    if ctype in _VIDEO_TYPES:
        return "mp4"
    if ctype in _AUDIO_TYPES:
        return "audio"
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        path = ""
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(".mpd"):
        return "dash"
    if path.endswith(".mp4"):
        return "mp4"
    if ctype in _OCTET_TYPES:
        return "mp4"  # opaque bytes + media context: treat as file media
    return "mp4"


def _content_type_ok(content_type: str) -> bool:
    if not content_type.strip():
        return True  # unknown (HEAD 405 path): extension decides
    ctype = content_type.split(";")[0].strip().lower()
    if ctype in _ALLOWED_MEDIA_TYPES:
        return True
    return ctype.startswith(("video/", "audio/"))


# ---------------------------------------------------------------------------
# guarded download (the imageio static ffmpeg segfaults on https input,
# rc=-11 on ANY https URL — so file media is materialized locally and
# the pipeline ingests the local path; playlists stay remote-only and
# are documented as unsupported by this static build in V5_LINK.md)
# ---------------------------------------------------------------------------

def download_media(url: str, dest_path: str, *,
                   timeout_s: float = TIMEOUT_S,
                   max_bytes: int = MEDIA_MAX_BYTES,
                   min_interval_s: float = 0.0,
                   dns_resolver=None, opener=None) -> dict:
    """Streaming SSRF-guarded download of file media to ``dest_path``.

    Same redirect/SSRF discipline as :func:`fetch_page` (check per hop,
    max ``MAX_REDIRECTS``), content-type must be media, size capped at
    ``max_bytes`` (Content-Length upfront + streaming enforcement).
    Writes atomically (tmp + rename). Returns
    ``{"path","url","size","content_type"}``.
    """
    import os

    current = url.strip()
    for _hop in range(MAX_REDIRECTS + 1):
        check_url_public(current, dns_resolver=dns_resolver)
        req = Request(current, headers={"User-Agent": USER_AGENT})  # noqa: S310
        do_open = opener or _open_no_redirect
        try:
            with do_open(req, timeout_s) as resp:
                status = getattr(resp, "status", 200) or 200
                if status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "")
                    if not location:
                        raise LinkError(
                            f"redirect sin Location para {current!r}")
                    current = urljoin(current, location)
                    continue
                if status >= 400:
                    raise LinkError(f"GET HTTP {status} para {current!r}")
                content_type = resp.headers.get("Content-Type", "")
                if not _content_type_ok(content_type):
                    raise LinkError(
                        f"{current!r}: content-type {content_type!r} "
                        f"no es medio")
                raw_len = str(resp.headers.get("Content-Length", "")).strip()
                if raw_len.isdigit() and int(raw_len) > max_bytes:
                    raise LinkBlocked(
                        "too-large",
                        f"medio de {raw_len} bytes supera el cap de "
                        f"{max_bytes} bytes",
                    )
                final = resp.geturl() or current
                check_url_public(final, dns_resolver=dns_resolver)
                tmp_path = f"{dest_path}.part"
                size = 0
                with open(tmp_path, "wb") as fh:
                    while True:
                        chunk = resp.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > max_bytes:
                            try:
                                os.unlink(tmp_path)
                            except OSError:
                                pass
                            raise LinkBlocked(
                                "too-large",
                                f"descarga supera el cap de {max_bytes} bytes",
                            )
                        fh.write(chunk)
                os.replace(tmp_path, dest_path)
                return {"path": dest_path, "url": final, "size": size,
                        "content_type": content_type}
        except LinkError:
            raise
        except HTTPError as exc:
            raise LinkError(f"GET HTTP {exc.code} para {current!r}") from exc
        except (URLError, TimeoutError, ValueError, OSError) as exc:
            raise LinkError(f"falló descarga {current!r}: {exc}") from exc
    raise LinkError(f"demasiados redirects (>{MAX_REDIRECTS}) para {url!r}")


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------

def resolve_link(url: str, *, timeout_s: float = TIMEOUT_S,
                 min_interval_s: float = MIN_HOST_INTERVAL_S,
                 dns_resolver=None, robots_checker=None,
                 page_fetcher=None, head_checker=None) -> ResolvedMedia:
    """Resolve a page-or-media ``url`` to a verified direct media URL.

    Direct media URLs (``.m3u8`` / ``.mpd`` / ``.mp4`` path) skip the page
    GET and go straight to HEAD verification; anything else is fetched
    as a page and scanned for embedded media. Raises :class:`LinkBlocked`
    on policy refusals, :class:`LinkError` when no usable media is found.
    """
    clean = (url or "").strip()
    if not clean:
        raise LinkBlocked("bad-url", "enlace vacío: pega una URL http(s)")
    check_url_public(clean, dns_resolver=dns_resolver)

    get_page = page_fetcher or (lambda u: fetch_page(
        u, timeout_s=timeout_s, min_interval_s=min_interval_s,
        dns_resolver=dns_resolver, robots_checker=robots_checker))
    do_head = head_checker or (lambda u: head_media(
        u, timeout_s=timeout_s, dns_resolver=dns_resolver))

    candidates: list[str] = []
    page_url = clean
    if _path_has_media_ext(clean):
        candidates = [clean]
    else:
        _final_page, _ctype, body = get_page(clean)
        page_url = _final_page
        try:
            html = body.decode("utf-8", "replace")
        except Exception:
            html = ""
        candidates = extract_media_candidates(html, page_url)
        if not candidates:
            raise LinkError(
                "sin medio directo: la página no expone og:video, "
                "<video src> ni enlaces .m3u8/.mpd/.mp4")

    errors: list[str] = []
    for candidate in candidates:
        try:
            final_url, content_type, size = do_head(candidate)
        except LinkBlocked:
            raise  # policy refusals (SSRF/size) are final, not skipped
        except LinkError as exc:
            errors.append(str(exc))
            continue
        if not _content_type_ok(content_type):
            errors.append(f"{candidate}: content-type {content_type!r} "
                          f"no es medio")
            continue
        return ResolvedMedia(url=final_url,
                             kind=kind_for_media(final_url, content_type),
                             content_type=content_type, size=size,
                             page_url=page_url)
    detail = "; ".join(errors) if errors else "sin candidatos"
    raise LinkError(f"ningún medio verificable para {clean!r}: {detail}")
