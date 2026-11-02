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
this same path so their quotes grep too.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
import zlib
from dataclasses import dataclass
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
) -> FetchResult:
    """Fetch ``url`` responsibly; snapshot the bytes; return provenance.

    Raises :class:`FetchBlocked` on robots/paywall refusal and
    :class:`FetchError` on any other failure. The snapshot file is
    written atomically (tmp + rename) and never modified afterwards:
    same bytes -> same sha -> same path (content-addressed).
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

    req = Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            status = getattr(resp, "status", 200)
            if status in REFUSED_STATUSES:
                raise FetchBlocked(f"HTTP {status} (paywall/login) for {url!r}")
            content_type = resp.headers.get("Content-Type", "")
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
    snap_dir = Path(snapshot_dir) if snapshot_dir else default_snapshot_dir()
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / f"{sha}.html"
    if not snap_path.exists():
        tmp = snap_path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(snap_path)  # atomic publish
    captured = _utcnow_iso()
    text = _decode_body(raw, content_type)
    if is_pdf_body(content_type, raw):  # F2-2: PDFs read as extracted text
        extracted = extract_pdf_text(raw)
        if extracted.strip():
            text = extracted
    return FetchResult(
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


async def fetch_url_async(
    url: str,
    *,
    snapshot_dir: str | Path | None = None,
    timeout_s: float = TIMEOUT_S,
    max_bytes: int = MAX_BYTES,
) -> FetchResult:
    """Asyncio wrapper (blocking urllib runs in a worker thread)."""
    return await asyncio.to_thread(
        fetch_url, url,
        snapshot_dir=snapshot_dir, timeout_s=timeout_s, max_bytes=max_bytes,
    )
