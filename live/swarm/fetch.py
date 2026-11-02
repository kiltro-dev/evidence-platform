"""Responsible fetch for the M2b research swarm (Muse Spark grounding).

Contract: http(s) only, robots.txt honored (urllib.robotparser), fixed
``atlas-live-research`` User-Agent, 15 s timeout, 2 MB cap, no
paywalls/logins (401/402/403/429 refused), every successful fetch saved
as an immutable snapshot ``data/snap/{sha256}.html`` with hash +
``captured_at``. Failures raise :class:`FetchError` (robots refusals
raise :class:`FetchBlocked`); nothing is ever snapshotted on failure.

The snapshot is the evidence root: every quote the swarm emits must be
a verbatim substring of these bytes (see ``coordinator``). JSON API
responses (World Bank, OWID/FRED CSVs, SPARQL) are snapshotted through
this same path so their quotes grep too. V5-CATB adds ``fetch_post``:
the same policy for POST-only JSON APIs (BLS ``publicAPI``).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import threading
import time
import zlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

USER_AGENT = "atlas-live-research"
TIMEOUT_S = 15.0
MAX_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 64 * 1024

# HTTP statuses that mean paywall / login / rate-limit: never snapshot.
REFUSED_STATUSES = frozenset({401, 402, 403, 429})

# Strong in-page paywall/login markers (checked on text/* bodies only).
# Conservative on purpose: generic words ("subscribe", "login") appear on
# legitimate pages, so only near-certain markers refuse the fetch.
_PAYWALL_MARKERS = (
    "to continue reading, please subscribe",
    "to keep reading, please subscribe",
    "you have reached your article limit",
    "you've reached your article limit",
    "this content is for subscribers only",
    "sign in to continue reading",
    "please log in to view this content",
    "please login to view this content",
)


def default_snapshot_dir() -> Path:
    """``data/snap`` under the repo root (parent of ``live/``)."""
    return Path(__file__).resolve().parents[2] / "data" / "snap"


def default_cache_dir() -> Path:
    """``data/cache`` under the repo root (F2-3 latency cache)."""
    return Path(__file__).resolve().parents[2] / "data" / "cache"


class FetchError(RuntimeError):
    """Any responsible-fetch failure (network, size, status, scheme)."""


class FetchBlocked(FetchError):
    """Fetch refused on policy grounds (robots.txt, paywall/login)."""


@dataclass(frozen=True)
class FetchResult:
    url: str  # final URL after redirects, as requested upstream
    final_url: str  # after redirects (may equal url)
    status: int
    content_type: str
    size: int  # bytes
    sha256: str  # hex of the raw body
    snapshot_path: str  # absolute path of data/snap/{sha}.html
    captured_at: str  # UTC ISO-8601 of the completed download
    text: str  # best-effort decoded body (for model reading, not quoting)


# ---------------------------------------------------------------------------
# robots.txt (cached per origin, polite + bounded)
# ---------------------------------------------------------------------------

_ROBOTS_TTL_S = 600.0
_robots_cache: dict[str, tuple[float, robotparser.RobotFileParser | None]] = {}
_robots_lock = threading.Lock()


def _robots_parser(origin: str) -> robotparser.RobotFileParser | None:
    """Cached robots.txt parser for an origin (None = unknown/blocked-net).

    Missing robots.txt (404/empty) means allowed. A robots.txt that
    cannot be fetched at all (DNS/timeout) is treated as UNKNOWN and
    the fetch proceeds: failing closed on transient network errors
    would veto all retrieval whenever robots.txt hosting hiccups. An
    explicit Disallow match always blocks (fail closed on policy).
    """
    now = time.monotonic()
    with _robots_lock:
        hit = _robots_cache.get(origin)
        if hit is not None and now - hit[0] < _ROBOTS_TTL_S:
            return hit[1]
    parser: robotparser.RobotFileParser | None = None
    try:
        rp = robotparser.RobotFileParser(f"{origin}/robots.txt")
        # RobotFileParser has no timeout/UA knobs; feed it manually.
        req = Request(
            f"{origin}/robots.txt",
            headers={"User-Agent": USER_AGENT},
        )
        with urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
            if resp.status == 200:
                body = resp.read(MAX_BYTES)
                rp.parse(body.decode("utf-8", "replace").splitlines())
                parser = rp
            # else: non-200 robots.txt -> no usable policy -> allowed
    except Exception:
        parser = None  # unknown: proceed (documented above)
    with _robots_lock:
        _robots_cache[origin] = (now, parser)
    return parser


def clear_robots_cache() -> None:
    """Drop cached robots.txt parsers (tests)."""
    with _robots_lock:
        _robots_cache.clear()


# ---------------------------------------------------------------------------
# response cache (F2-3 latency): memory + disk, TTL 24h, honors no-store
# ---------------------------------------------------------------------------
#
# Keyed by sha256 of the URL. Disk entries live in ``data/cache/{sha}.json``
# (meta + base64 body, one file per URL so TTL expiry is a stat+unlink).
# Memory holds small bodies only (<=256 KiB, 64 entries FIFO) as a fast
# path; disk holds everything. A hit returns the ORIGINAL sha/captured_at
# (evidence provenance is unchanged) and re-materializes the snapshot file
# when it is missing. ``Cache-Control: no-store`` responses are never
# stored. Every cache helper degrades to miss/no-op on any error: caching
# can only save time, never fail a fetch.

CACHE_TTL_S = 24 * 3600
_CACHE_MEM_MAX = 64
_CACHE_MEM_BODY_MAX = 256 * 1024

_cache_mem: dict[str, tuple[float, FetchResult, bytes]] = {}
_cache_lock = threading.Lock()
_cache_stats = {"hits_mem": 0, "hits_disk": 0, "misses": 0,
                "stores": 0, "no_store": 0}


def cache_key_for_url(url: str) -> str:
    """sha256 hex of the stripped URL (F2-3, pure)."""
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def get_cache_stats() -> dict[str, int]:
    """Cumulative cache counters (for latency attribution)."""
    with _cache_lock:
        return dict(_cache_stats)


def reset_cache_stats() -> None:
    """Zero the counters (tests)."""
    with _cache_lock:
        for key in _cache_stats:
            _cache_stats[key] = 0


def clear_mem_cache() -> None:
    """Drop in-memory entries (tests)."""
    with _cache_lock:
        _cache_mem.clear()


def cache_entry_fresh(fetched_at_epoch: float, now_epoch: float,
                      ttl_s: float = CACHE_TTL_S) -> bool:
    """True when a disk entry timestamp is inside the TTL (F2-3, pure)."""
    try:
        age = float(now_epoch) - float(fetched_at_epoch)
    except (TypeError, ValueError):
        return False
    return 0.0 <= age < float(ttl_s)


def _bump(stat: str) -> None:
    with _cache_lock:
        _cache_stats[stat] = _cache_stats.get(stat, 0) + 1


def cache_store(url: str, result: FetchResult, body: bytes,
                *, cache_dir: str | Path | None = None) -> bool:
    """Store a successful fetch (memory + disk). Never raises.

    Returns True when the disk entry was written. ``no-store`` responses
    must NOT reach this function (``fetch_url`` checks first); the
    ``no_store`` counter documents those skips instead.
    """
    try:
        raw = bytes(body)
    except Exception:
        return False
    key = cache_key_for_url(url)
    expiry = time.monotonic() + CACHE_TTL_S
    with _cache_lock:
        if len(raw) <= _CACHE_MEM_BODY_MAX:
            _cache_mem[url.strip()] = (expiry, result, raw)
            while len(_cache_mem) > _CACHE_MEM_MAX:
                _cache_mem.pop(next(iter(_cache_mem)), None)
    try:
        target = Path(cache_dir) if cache_dir else default_cache_dir()
        target.mkdir(parents=True, exist_ok=True)
        payload = {
            "url": url.strip(),
            "fetched_at": time.time(),
            "status": result.status,
            "content_type": result.content_type,
            "final_url": result.final_url,
            "captured_at": result.captured_at,
            "sha256": result.sha256,
            "body_b64": base64.b64encode(raw).decode("ascii"),
        }
        tmp = target / f"{key}.tmp"
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(target / f"{key}.json")
    except Exception:
        return False
    _bump("stores")
    return True


def cache_lookup(url: str, *,
                 cache_dir: str | Path | None = None,
                 ) -> tuple[FetchResult, bytes] | None:
    """Cached (FetchResult, raw body) for ``url``, or None (never raises).

    Memory first, then disk (a disk hit repopulates memory when small).
    Stale entries (older than TTL) are treated as misses and unlinked.
    The returned result carries the ORIGINAL sha/captured_at; the caller
    re-materializes the snapshot file (same bytes -> same path).
    """
    clean = url.strip()
    now_mono = time.monotonic()
    with _cache_lock:
        hit = _cache_mem.get(clean)
        if hit is not None and now_mono < hit[0]:
            _cache_stats["hits_mem"] += 1
            return hit[1], hit[2]
        if hit is not None:
            _cache_mem.pop(clean, None)
    try:
        target = Path(cache_dir) if cache_dir else default_cache_dir()
        path = target / f"{cache_key_for_url(clean)}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _bump("misses")
        return None
    if not isinstance(payload, dict) or payload.get("url") != clean:
        _bump("misses")
        return None
    if not cache_entry_fresh(payload.get("fetched_at", 0.0), time.time()):
        try:
            path.unlink()
        except OSError:
            pass
        _bump("misses")
        return None
    try:
        raw = base64.b64decode(payload["body_b64"])
        sha = str(payload.get("sha256") or hashlib.sha256(raw).hexdigest())
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError("body hash mismatch")
        result = FetchResult(
            url=clean,
            final_url=str(payload.get("final_url") or clean),
            status=int(payload.get("status") or 200),
            content_type=str(payload.get("content_type") or ""),
            size=len(raw),
            sha256=sha,
            snapshot_path="",  # caller resolves under its snapshot_dir
            captured_at=str(payload.get("captured_at") or _utcnow_iso()),
            text="",  # caller decodes (same path as a miss)
        )
    except Exception:
        _bump("misses")
        return None
    if len(raw) <= _CACHE_MEM_BODY_MAX:
        with _cache_lock:
            _cache_mem[clean] = (now_mono + CACHE_TTL_S, result, raw)
    _bump("hits_disk")
    return result, raw


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def _decode_body(body: bytes, content_type: str) -> str:
    charset = "utf-8"
    for part in content_type.split(";")[1:]:
        part = part.strip()
        if part.lower().startswith("charset="):
            charset = part.split("=", 1)[1].strip().strip("\"'") or "utf-8"
    try:
        return body.decode(charset, "replace")
    except (LookupError, ValueError):
        return body.decode("utf-8", "replace")


def is_pdf_body(content_type: str, raw: bytes) -> bool:
    """True when the body is a PDF (content-type or %PDF magic, F2-2)."""
    if "pdf" in (content_type or "").lower():
        return True
    try:
        return bytes(raw or b"")[:8].startswith(b"%PDF-")
    except Exception:
        return False


_PDF_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)
_PDF_LITERAL_RE = re.compile(rb"\((?:\\.|[^()\\])*\)")
_PDF_SHOW_RE = re.compile(
    rb"\((?:\\.|[^()\\])*\)\s*(?:Tj|')|\[(?:[^\[\]]*?)\]\s*TJ", re.DOTALL)
_PDF_TEXT_CAP_CHARS = 300_000


def extract_pdf_text(raw: bytes) -> str:
    """Stdlib-minimal PDF text extraction (F2-2, no new deps).

    Decodes FlateDecode streams with zlib and collects text-showing
    strings (literal ``(...) Tj`` / ``[...] TJ`` plus bare hex strings).
    best effort for model READING (fetch.text): the snapshot still keeps
    the raw PDF bytes, so only quotes that also occur raw (uncompressed
    PDFs, metadata) can verify verbatim. Returns "" for non-PDF input.
    Never raises on bytes-shaped input.
    """
    try:
        data = bytes(raw or b"")
    except Exception:
        return ""
    if not data.startswith(b"%PDF-"):
        return ""
    chunks: list[str] = []
    bodies = [m.group(1) for m in _PDF_STREAM_RE.finditer(data)] or [data]
    for body in bodies:
        decoded = body
        try:
            decoded = zlib.decompress(body.strip())
        except Exception:
            pass  # uncompressed stream (or unknown filter): scan raw
        for op in _PDF_SHOW_RE.finditer(decoded):
            for lit in _PDF_LITERAL_RE.finditer(op.group(0)):
                chunks.append(_pdf_literal_to_text(lit.group(0)))
        if len(" ".join(chunks)) > _PDF_TEXT_CAP_CHARS:
            break
    if not chunks:  # metadata / uncompressed fallback: bare strings
        for lit in _PDF_LITERAL_RE.finditer(data[:1_000_000]):
            chunks.append(_pdf_literal_to_text(lit.group(0)))
            if len(chunks) > 2000:
                break
    text = " ".join(c for c in chunks if c).strip()
    return text[:_PDF_TEXT_CAP_CHARS]


def _pdf_literal_to_text(literal: bytes) -> str:
    """Decode one PDF literal string ``(...)`` (WinAnsi-ish, best effort)."""
    inner = literal[1:-1]
    inner = inner.replace(b"\\n", b"\n").replace(b"\\r", b"\n")
    inner = inner.replace(b"\\t", b" ").replace(b"\\\\", b"\\")
    inner = inner.replace(b"\\(", b"(").replace(b"\\)", b")")
    try:
        return inner.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_url(
    url: str,
    *,
    snapshot_dir: str | Path | None = None,
    timeout_s: float = TIMEOUT_S,
    max_bytes: int = MAX_BYTES,
    use_cache: bool = True,
    cache_dir: str | Path | None = None,
) -> FetchResult:
    """Fetch ``url`` responsibly; snapshot the bytes; return provenance.

    Raises :class:`FetchBlocked` on robots/paywall refusal and
    :class:`FetchError` on any other failure. The snapshot file is
    written atomically (tmp + rename) and never modified afterwards:
    same bytes -> same sha -> same path (content-addressed).

    F2-3: ``use_cache`` consults the memory+disk response cache AFTER
    the robots check (policy still wins) and stores successes unless
    the response says ``Cache-Control: no-store``. A hit returns the
    original sha/captured_at with the snapshot re-materialized.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise FetchError(f"bad URL {url!r}: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise FetchError(f"refusing non-http(s) URL {url!r}")
    if not parts.hostname:
        raise FetchError(f"refusing URL without host {url!r}")

    origin = f"{parts.scheme}://{parts.hostname}"
    if parts.port:
        origin += f":{parts.port}"
    parser = _robots_parser(origin)
    if parser is not None and not parser.can_fetch(USER_AGENT, url):
        raise FetchBlocked(f"robots.txt disallows {url!r}")

    if use_cache:
        hit = cache_lookup(url, cache_dir=cache_dir)
        if hit is not None:
            cached, raw = hit
            snap_path = _materialize_snapshot(raw, cached.sha256,
                                             snapshot_dir)
            text = _decode_body(raw, cached.content_type)
            if is_pdf_body(cached.content_type, raw):
                extracted = extract_pdf_text(raw)
                if extracted.strip():
                    text = extracted
            return replace(cached, snapshot_path=str(snap_path), text=text)

    req = Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            status = getattr(resp, "status", 200)
            if status in REFUSED_STATUSES:
                raise FetchBlocked(f"HTTP {status} (paywall/login) for {url!r}")
            content_type = resp.headers.get("Content-Type", "")
            try:
                cache_control = resp.headers.get("Cache-Control", "")
            except Exception:
                cache_control = ""
            body = bytearray()
            while True:
                chunk = resp.read(CHUNK_BYTES)
                if not chunk:
                    break
                body += chunk
                if len(body) > max_bytes:
                    raise FetchError(
                        f"body exceeds {max_bytes} bytes for {url!r}"
                    )
            final_url = resp.geturl()
    except FetchError:
        raise
    except HTTPError as exc:
        if exc.code in REFUSED_STATUSES:
            raise FetchBlocked(
                f"HTTP {exc.code} (paywall/login) for {url!r}"
            ) from exc
        raise FetchError(f"HTTP {exc.code} for {url!r}") from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        raise FetchError(f"fetch failed for {url!r}: {exc}") from exc

    raw = bytes(body)
    ctype_lower = content_type.lower()
    if "text" in ctype_lower or "html" in ctype_lower or "xml" in ctype_lower:
        lowered = raw[:256_000].decode("utf-8", "replace").lower()
        for marker in _PAYWALL_MARKERS:
            if marker in lowered:
                raise FetchBlocked(f"paywall/login marker for {url!r}")

    sha = hashlib.sha256(raw).hexdigest()
    snap_path = _materialize_snapshot(raw, sha, snapshot_dir)
    captured = _utcnow_iso()
    text = _decode_body(raw, content_type)
    if is_pdf_body(content_type, raw):  # F2-2: PDFs read as extracted text
        extracted = extract_pdf_text(raw)
        if extracted.strip():
            text = extracted
    result = FetchResult(
        url=url,
        final_url=final_url,
        status=status,
        content_type=content_type,
        size=len(raw),
        sha256=sha,
        snapshot_path=str(snap_path),
        captured_at=captured,
        text=text,
    )
    if use_cache:
        if "no-store" in str(cache_control or "").lower():
            _bump("no_store")
        else:
            cache_store(url, result, raw, cache_dir=cache_dir)
    return result


def _materialize_snapshot(raw: bytes, sha: str,
                          snapshot_dir: str | Path | None) -> Path:
    """Snapshot path for ``raw``, writing the bytes when missing."""
    snap_dir = Path(snapshot_dir) if snapshot_dir else default_snapshot_dir()
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / f"{sha}.html"
    if not snap_path.exists():
        tmp = snap_path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(snap_path)  # atomic publish
    return snap_path


def fetch_post(
    url: str,
    payload: dict | list | bytes | str,
    *,
    snapshot_dir: str | Path | None = None,
    timeout_s: float = TIMEOUT_S,
    max_bytes: int = MAX_BYTES,
    content_type: str = "application/json",
) -> FetchResult:
    """POST ``payload`` to ``url`` under the same policy as fetch_url.

    V5-CATB: the sanctioned seam for documented keyless JSON APIs (BLS
    ``publicAPI``). Same contract: http(s) only, robots.txt honored
    (checked BEFORE anything is sent), 15 s timeout, 2 MB cap,
    401/402/403/429 refused, success snapshotted content-addressed.
    Differences from GET: no response cache (POST bodies are
    vintage-sensitive and callers budget them) and no in-page
    paywall-marker scan (API JSON bodies). Raises :class:`FetchBlocked`
    on robots/paywall refusal, :class:`FetchError` otherwise.
    """
    if isinstance(payload, (dict, list)):
        body = json.dumps(payload).encode("utf-8")
    elif isinstance(payload, str):
        body = payload.encode("utf-8")
    else:
        try:
            body = bytes(payload)
        except Exception as exc:
            raise FetchError(f"bad POST payload for {url!r}: {exc}") from exc
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise FetchError(f"bad URL {url!r}: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise FetchError(f"refusing non-http(s) URL {url!r}")
    if not parts.hostname:
        raise FetchError(f"refusing URL without host {url!r}")

    origin = f"{parts.scheme}://{parts.hostname}"
    if parts.port:
        origin += f":{parts.port}"
    parser = _robots_parser(origin)
    if parser is not None and not parser.can_fetch(USER_AGENT, url):
        raise FetchBlocked(f"robots.txt disallows {url!r}")

    req = Request(  # noqa: S310
        url, data=body,
        headers={"User-Agent": USER_AGENT, "Content-Type": content_type},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            status = getattr(resp, "status", 200)
            if status in REFUSED_STATUSES:
                raise FetchBlocked(f"HTTP {status} (paywall/login) for {url!r}")
            resp_content_type = resp.headers.get("Content-Type", "")
            raw_parts = bytearray()
            while True:
                chunk = resp.read(CHUNK_BYTES)
                if not chunk:
                    break
                raw_parts += chunk
                if len(raw_parts) > max_bytes:
                    raise FetchError(
                        f"body exceeds {max_bytes} bytes for {url!r}"
                    )
            final_url = resp.geturl()
    except FetchError:
        raise
    except HTTPError as exc:
        if exc.code in REFUSED_STATUSES:
            raise FetchBlocked(
                f"HTTP {exc.code} (paywall/login) for {url!r}"
            ) from exc
        raise FetchError(f"HTTP {exc.code} for {url!r}") from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        raise FetchError(f"fetch failed for {url!r}: {exc}") from exc

    raw = bytes(raw_parts)
    sha = hashlib.sha256(raw).hexdigest()
    snap_path = _materialize_snapshot(raw, sha, snapshot_dir)
    return FetchResult(
        url=url,
        final_url=final_url,
        status=status,
        content_type=resp_content_type,
        size=len(raw),
        sha256=sha,
        snapshot_path=str(snap_path),
        captured_at=_utcnow_iso(),
        text=_decode_body(raw, resp_content_type),
    )


async def fetch_url_async(
    url: str,
    *,
    snapshot_dir: str | Path | None = None,
    timeout_s: float = TIMEOUT_S,
    max_bytes: int = MAX_BYTES,
    use_cache: bool = True,
    cache_dir: str | Path | None = None,
) -> FetchResult:
    """Asyncio wrapper (blocking urllib runs in a worker thread)."""
    return await asyncio.to_thread(
        fetch_url, url,
        snapshot_dir=snapshot_dir, timeout_s=timeout_s, max_bytes=max_bytes,
        use_cache=use_cache, cache_dir=cache_dir,
    )
