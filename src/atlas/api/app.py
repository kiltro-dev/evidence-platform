"""FastAPI service: JSON API + static single-page UI, one process.

Error contract (PRD §4, BACKLOG frozen): every 4xx/5xx from this API uses
the unified envelope ``{"error": {"code", "message", "request_id"}}``.
``request_id`` (uuid4 hex, fresh per error) appears on errors only —
success bodies carry no request_id. No handler ever leaks tracebacks or
request bodies into logs or responses.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path

import atlas.pipeline.run as pipeline_run
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from atlas.core.types import Report
from atlas.models.meta import MetaClient
from atlas.storage.store import delete_report, get_report, list_reports

WEB_DIR = Path(__file__).resolve().parents[3] / "web"

app = FastAPI(title="Evidence Atlas", version=pipeline_run.APP_VERSION)

# UT-n: opt-in CORS for sidecar/browser integrations. Origins come ONLY
# from ATLAS_CORS_ORIGINS (comma-separated, e.g.
# "https://app.example.com,https://demo.example.org"); unset/empty
# means same-origin only (no CORS headers emitted). Methods/headers
# are allowlisted (X-API-Key included for authed browser calls);
# credentials stay disabled (key travels in the header, never cookies).
_cors_origins = [o.strip() for o in
                 os.environ.get("ATLAS_CORS_ORIGINS", "").split(",")
                 if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
    allow_credentials=False,
    max_age=600,
)
_llm: MetaClient | None = None

VALID_DOMAINS = pipeline_run.VALID_DOMAINS

# --- In-memory per-IP rate limit: 60 req/min on POST /api/analyze. ---
# Dependency-free: {client_ip: [monotonic timestamps of recent hits]}.
RATE_LIMIT_PER_MINUTE = 60
RATE_WINDOW_S = 60.0
_MAX_TRACKED_IPS = 10_000  # hygiene cap so one dict cannot grow unbounded
_rate_hits: dict[str, list[float]] = {}
# Sync endpoints run on uvicorn's threadpool: one lock guards the
# check-then-act mutations on _rate_hits and _cache below.
_state_lock = threading.Lock()

# --- Identical-text cache: (text, domain, limit, persist, model) -> (Report, ts).
# Dependency-free dict, FIFO eviction at 128 entries, TTL 1h. A hit skips
# analyze() entirely (no pipeline, no connectors, no model, no persist).
# `persist` is part of the key: a cached persist=false report must never
# satisfy a persist=true request (the report_id would 404 on re-fetch).
# `model` is part of the key (stale-model fix): a report synthesized by one
# model must never satisfy a request that would run under another model.
# None marks the degraded path (no LLM configured).
CACHE_CAP = 128
CACHE_TTL_S = 3600.0
_cache: dict[tuple[str, str, int, bool, str | None], tuple[Report, float]] = {}
# In-flight singleflight: cache_key -> Event set when the owner's pipeline
# run finishes (success or failure). Concurrent identical requests join the
# owner's run instead of each running the pipeline (pre-fix: 20 parallel
# identical POSTs ran analyze() 20x). Guarded by _state_lock; the pipeline
# itself runs OUTSIDE the lock. The owner's try/finally guarantees the
# event is set, and a joiner whose owner failed loops back to try owning,
# so no request waits forever and none fails just because a sibling did.
_inflight: dict[tuple[str, str, int, bool, str | None], threading.Event] = {}


def get_llm() -> MetaClient | None:
    global _llm
    if _llm is None:
        # MetaClient loads .env on construction; a key added later
        # requires a server restart.
        client = MetaClient()
        _llm = client if client.available else None
    return _llm


class AnalyzeRequest(BaseModel):
    text: str
    limit: int = 5
    domain: str = "auto"
    # UT-j: persist defaults to False — a public default-true would
    # archive every visitor's pasted text (user-data leak by design).
    # The UI sends persist:true explicitly when history is wanted.
    persist: bool = False
    # fresh=true skips the identical-text cache READ (re-runs the
    # pipeline) but still refreshes the cache entry on success.
    fresh: bool = False


class CheckRequest(BaseModel):
    text: str
    limit: int = 5
    domain: str = "auto"
    persist: bool = False
    # UT-l: optional global batch budget in seconds (clamped 1..600 at
    # the endpoints; None waits for every sub-claim as before).
    deadline_s: float | None = None


class ApiError(Exception):
    """Endpoint-raised error that renders as the unified envelope."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _envelope(code: str, message: str) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": uuid.uuid4().hex,
        }
    }


@app.exception_handler(ApiError)
async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content=_envelope(exc.code, exc.message)
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Normalize FastAPI's list-shaped 422 detail into one message string.
    parts: list[str] = []
    fields: set[str] = set()
    for err in exc.errors():
        loc = err.get("loc", ())
        field = str(loc[-1]) if loc else ""
        fields.add(field)
        where = ".".join(str(p) for p in loc) if loc else "body"
        parts.append(f"{where}: {err.get('msg', 'invalid')}")
    message = "; ".join(parts) if parts else "Invalid request."
    if "text" in fields:
        code = "empty_claim"
    elif "domain" in fields:
        code = "invalid_domain"
    elif "limit" in fields:
        code = "invalid_limit"
    elif "report_id" in fields:
        code = "report_not_found"
    else:  # pragma: no cover - fallback for unforeseen shapes
        code = "bad_request"
    return JSONResponse(status_code=422, content=_envelope(code, message))


@app.exception_handler(StarletteHTTPException)
async def _http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Framework-raised HTTP errors (unknown route, method not allowed, ...).
    status = exc.status_code
    if status == 404:
        code, message = "report_not_found", "Not found."
    elif status == 429:
        code, message = "rate_limited", "Rate limit exceeded."
    elif status >= 500:
        code, message = "analysis_failed", "Internal error."
    else:
        code, message = "bad_request", "Bad request."
    detail = exc.detail if isinstance(exc.detail, str) else None
    return JSONResponse(
        status_code=status, content=_envelope(code, detail or message)
    )


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    # Last resort: never leak tracebacks or request bodies.
    return JSONResponse(
        status_code=500, content=_envelope("analysis_failed", "Analysis failed.")
    )


# Worst-case sub-claims per batch (splitter cap): /api/check is charged
# this many quota units. Charging actuals would require splitting (a
# model call) before the gate — an abuse vector — so the fixed
# worst-case cost is the honest choice.
BATCH_RATE_COST = 8


def _check_rate_limit(bucket: str, cost: int = 1) -> None:
    with _state_lock:
        _check_rate_limit_locked(bucket, cost)


def _check_rate_limit_locked(bucket: str, cost: int) -> None:
    # Loopback callers share one bucket: 127.0.0.1/::1/localhost are
    # spellings of the same local user, so per-spelling buckets would
    # multiply the quota. Key buckets ("key:...") never collapse.
    if bucket.startswith("ip:") and bucket[3:] in _LOOPBACK_HOSTS:
        bucket = "ip:loopback"
    now = time.monotonic()
    hits = _rate_hits.get(bucket)
    if hits is None:
        if len(_rate_hits) >= _MAX_TRACKED_IPS:
            _rate_hits.pop(next(iter(_rate_hits)))
        if cost > RATE_LIMIT_PER_MINUTE:
            raise ApiError(
                429, "rate_limited", "Rate limit exceeded."
            )
        _rate_hits[bucket] = [now] * cost
        return
    cutoff = now - RATE_WINDOW_S
    i = 0
    while i < len(hits) and hits[i] <= cutoff:
        i += 1
    del hits[:i]
    if len(hits) + cost > RATE_LIMIT_PER_MINUTE:
        raise ApiError(
            429, "rate_limited", "Rate limit exceeded."
        )
    hits.extend([now] * cost)


def _cache_get(key: tuple[str, str, int, bool, str | None]) -> Report | None:
    with _state_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        report, ts = entry
        if time.monotonic() - ts > CACHE_TTL_S:
            _cache.pop(key, None)
            return None
        return report


def _cache_put(
    key: tuple[str, str, int, bool, str | None], report: Report
) -> None:
    with _state_lock:
        if key not in _cache and len(_cache) >= CACHE_CAP:
            _cache.pop(next(iter(_cache)))  # evict oldest-inserted (FIFO)
        _cache[key] = (report, time.monotonic())


# UT-j: identical-text cache for /api/check (persist=false only — see
# the endpoint docstring). Same FIFO+TTL policy as the analyze cache.
_check_cache: dict[tuple[str, str, int, str | None], tuple[dict, float]] = {}


def _check_cache_get(key: tuple[str, str, int, str | None]) -> dict | None:
    with _state_lock:
        entry = _check_cache.get(key)
        if entry is None:
            return None
        out, ts = entry
        if time.monotonic() - ts > CACHE_TTL_S:
            _check_cache.pop(key, None)
            return None
        return out


def _check_cache_put(key: tuple[str, str, int, str | None], out: dict) -> None:
    with _state_lock:
        if key not in _check_cache and len(_check_cache) >= CACHE_CAP:
            _check_cache.pop(next(iter(_check_cache)))
        _check_cache[key] = (out, time.monotonic())


def _cache_evict_report(report_id: str) -> None:
    """Drop cached entries for a deleted report id.

    Without this, DELETE-then-rePOST resurrects the deleted report_id
    from cache and the re-fetch 404s (red-team R5).
    """
    with _state_lock:
        dead = [
            key
            for key, (report, _) in _cache.items()
            if report.report_id == report_id
        ]
        for key in dead:
            _cache.pop(key, None)


def _inflight_acquire(
    key: tuple[str, str, int, bool, str | None],
) -> tuple[bool, threading.Event]:
    """Join or own an in-flight pipeline run. Returns (owner, event)."""
    with _state_lock:
        event = _inflight.get(key)
        if event is not None:
            return False, event
        event = threading.Event()
        _inflight[key] = event
        return True, event


def _inflight_release(key: tuple[str, str, int, bool, str | None]) -> None:
    with _state_lock:
        event = _inflight.pop(key, None)
    if event is not None:
        event.set()


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _expected_api_key() -> str:
    return os.environ.get("ATLAS_API_KEY", "")


def _request_api_key(request: Request) -> str:
    return request.headers.get("x-api-key", "")


def _api_key_ok(request: Request) -> bool:
    expected = _expected_api_key()
    if not expected:
        return False
    return hmac.compare_digest(_request_api_key(request), expected)


def require_api_key(request: Request) -> str | None:
    """Auth dependency for sensitive endpoints (UT-j).

    Two modes: without ATLAS_API_KEY the server is localhost-only
    (the loopback guard below is the boundary; dependency returns
    None). With a key configured, X-API-Key is REQUIRED (401
    otherwise) and the returned key identifies the caller for
    per-user rate limiting. Constant-time comparison.
    """
    expected = _expected_api_key()
    if not expected:
        return None
    if not hmac.compare_digest(_request_api_key(request), expected):
        raise ApiError(
            401, "unauthorized",
            "Missing or invalid X-API-Key header.",
        )
    return _request_api_key(request)


def _client_allowed(host: str | None) -> bool:
    """Localhost-only enforcement (code, not just docs).

    The demo server has no auth, so it must not serve the network:
    non-loopback clients get 403 unless ATLAS_ALLOW_NON_LOOPBACK=1.
    "testclient" is Starlette's in-process test host — it never
    traverses a network, so it bypasses the guard by design.
    """
    if os.environ.get("ATLAS_ALLOW_NON_LOOPBACK") == "1":
        return True
    return (host or "") in _LOOPBACK_HOSTS


@app.middleware("http")
async def _loopback_guard(request: Request, call_next):
    # UT-j: a valid API key bypasses the loopback guard (public deploy
    # behind a proxy authenticates instead of relying on source IP).
    if _api_key_ok(request):
        return await call_next(request)
    host = request.client.host if request.client else None
    if not _client_allowed(host):
        return JSONResponse(
            status_code=403,
            content=_envelope(
                "non_loopback_blocked",
                "This server accepts loopback clients only "
                "(set ATLAS_ALLOW_NON_LOOPBACK=1 to override).",
            ),
        )
    return await call_next(request)


def _request_ip(request: Request) -> str:
    """Client IP for rate-limit buckets (UT-j).

    Behind a trusted proxy (ATLAS_TRUST_PROXY=1 ONLY behind a proxy
    that overwrites X-Forwarded-For), the leftmost forwarded address
    identifies the caller; otherwise the connection peer does. XFF is
    never trusted by default: it is client-spoofable.
    """
    if os.environ.get("ATLAS_TRUST_PROXY") == "1":
        first = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def _rate_bucket(request: Request, api_key: str | None) -> str:
    """Rate-limit bucket key (UT-j): per API key when authenticated
    (one user cannot burn another's quota behind a shared proxy IP),
    else per client IP. Keys hash before storage (never log raw)."""
    if api_key:
        digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:32]
        return f"key:{digest}"
    return f"ip:{_request_ip(request)}"


def _call_pipeline(text: str, limit: int, domain: str, persist: bool) -> Report:
    """Call run.analyze, tolerating pipeline versions without `domain`.

    The `domain` override param is pipeline-owned; older/newer signatures
    are handled here so the API contract (PRD §4) holds either way. When
    the pipeline cannot take the override, the requested domain is still
    recorded as the claim's effective domain rather than silently dropped.
    """
    # UT-m: each request gets a forked client — the singleton's
    # last_usage/seq would otherwise cross-attribute tokens between
    # concurrent requests. Stateless doubles (no fork method) share.
    shared = get_llm()
    fork = getattr(shared, "fork", None)
    llm = fork() if callable(fork) else shared
    try:
        return pipeline_run.analyze(
            text, llm=llm, limit=limit, persist=persist, domain=domain
        )
    except TypeError:
        report = pipeline_run.analyze(text, llm=llm, limit=limit, persist=persist)
        if domain != "auto":
            report.claim.domain = domain  # type: ignore[assignment]
        return report


@app.get("/api/status")
def status() -> dict:
    llm = get_llm()
    return {
        "service": "evidence-atlas",
        "version": pipeline_run.APP_VERSION,
        "llm_available": llm is not None,
        "llm_model": llm.model if llm else None,
        "mode": "full (model + deterministic)" if llm else "degraded (deterministic only)",
        "connectors": ["worldbank", "openalex"],
    }


@app.post("/api/analyze", response_model=Report)
def analyze_claim(
    req: AnalyzeRequest,
    request: Request,
    api_key: str | None = Depends(require_api_key),
) -> Report:
    text = req.text.strip()
    if not text:
        raise ApiError(400, "empty_claim", "Empty claim text.")
    if len(text) > 4000:
        raise ApiError(
            400, "claim_too_long", "Claim text too long (max 4000 chars)."
        )
    if req.domain not in VALID_DOMAINS:
        raise ApiError(
            400,
            "invalid_domain",
            f"Invalid domain {req.domain!r} (expected one of: auto, economy, science, general).",
        )
    # R4: quota is charged AFTER validation — malformed requests are
    # free (localhost-only demo; a future multi-user server should
    # throttle invalid floods separately, see SECURITY_OPS).
    _check_rate_limit(_rate_bucket(request, api_key))
    # BACKLOG decision: keep clamp (documented) instead of 400 for
    # out-of-range limits; non-integer limits 422 via the validator.
    clamped = max(1, min(req.limit, 10))
    key_llm = get_llm()
    model_name = key_llm.model if key_llm is not None else None
    cache_key = (text, req.domain, clamped, req.persist, model_name)
    cached = None if req.fresh else _cache_get(cache_key)
    if cached is not None:
        return cached
    if not req.fresh:
        # Singleflight: join the owner's run instead of racing it. A
        # joiner whose owner failed (nothing cached) loops to try owning
        # rather than inheriting the sibling's failure.
        while True:
            owner, event = _inflight_acquire(cache_key)
            if owner:
                break
            event.wait()
            joined = _cache_get(cache_key)
            if joined is not None:
                return joined
        try:
            report = _call_pipeline(text, clamped, req.domain, req.persist)
        except ApiError:
            raise
        except Exception:
            # Pipeline/connector/model faults map to analysis_failed; the
            # traceback stays server-side and never reaches the client.
            raise ApiError(500, "analysis_failed", "Analysis failed.") from None
        else:
            _cache_put(cache_key, report)
        finally:
            _inflight_release(cache_key)
        return report
    try:
        report = _call_pipeline(text, clamped, req.domain, req.persist)
    except ApiError:
        raise
    except Exception:
        raise ApiError(500, "analysis_failed", "Analysis failed.") from None
    _cache_put(cache_key, report)
    return report


@app.post("/api/check")
def check_document(
    req: CheckRequest,
    request: Request,
    api_key: str | None = Depends(require_api_key),
) -> dict:
    """ShipCheck batch: split pasted text (e.g. an AI answer) into atomic
    claims and analyze each one. Returns per-claim reports + stance roll-up.
    Identical-text cache (UT-j) covers persist=false only: persisted
    batches mint fresh report ids per call and must never be served
    from cache (DELETE-then-rePOST would 404 on re-fetch)."""
    text = req.text.strip()
    if not text:
        raise ApiError(400, "empty_claim", "Empty claim text.")
    if len(text) > 12000:
        raise ApiError(
            400, "claim_too_long", "Text too long (max 12000 chars)."
        )
    if req.domain not in VALID_DOMAINS:
        raise ApiError(
            400,
            "invalid_domain",
            f"Invalid domain {req.domain!r} (expected one of: auto, economy, science, general).",
        )
    # R4: an invalid batch must not burn 8 quota units — charge after
    # validation (same localhost-only caveat as /api/analyze).
    _check_rate_limit(_rate_bucket(request, api_key), cost=BATCH_RATE_COST)
    clamped = max(1, min(req.limit, 10))
    deadline = (_clamp_deadline(req.deadline_s)
                if req.deadline_s is not None else None)
    key_llm = get_llm()
    model_name = key_llm.model if key_llm is not None else None
    check_key = (text, req.domain, clamped, model_name)
    if not req.persist and deadline is None:
        hit = _check_cache_get(check_key)
        if hit is not None:
            return hit
    shared = get_llm()
    fork = getattr(shared, "fork", None)
    req_llm = fork() if callable(fork) else shared
    try:
        out = pipeline_run.check_document(
            text, llm=req_llm, limit=clamped,
            persist=req.persist, domain=req.domain,
            deadline_s=deadline,
        )
    except ApiError:
        raise
    except Exception:
        raise ApiError(500, "analysis_failed", "Analysis failed.") from None
    if not req.persist and deadline is None:
        _check_cache_put(check_key, out)
    return out


def _clamp_deadline(value: float) -> float:
    return max(1.0, min(float(value), 600.0))


@app.post("/api/check/stream")
def check_stream(
    req: CheckRequest,
    request: Request,
    api_key: str | None = Depends(require_api_key),
) -> StreamingResponse:
    """SSE batch with live progress (UT-l).

    Same validation, auth, quota (cost 8) and deadline semantics as
    /api/check, but streams `text/event-stream`: a `started` event,
    one light `claim` event per finished sub-claim (index/stance or
    error), and a final `done` event carrying the complete batch dict
    (reports included). Never cached: each stream re-runs.
    """
    text = req.text.strip()
    if not text:
        raise ApiError(400, "empty_claim", "Empty claim text.")
    if len(text) > 12000:
        raise ApiError(
            400, "claim_too_long", "Text too long (max 12000 chars)."
        )
    if req.domain not in VALID_DOMAINS:
        raise ApiError(
            400,
            "invalid_domain",
            f"Invalid domain {req.domain!r} (expected one of: auto, economy, science, general).",
        )
    _check_rate_limit(_rate_bucket(request, api_key), cost=BATCH_RATE_COST)
    clamped = max(1, min(req.limit, 10))
    deadline = (_clamp_deadline(req.deadline_s)
                if req.deadline_s is not None else None)
    shared = get_llm()
    fork = getattr(shared, "fork", None)
    req_llm = fork() if callable(fork) else shared
    events: queue.Queue = queue.Queue()

    def _run() -> None:
        try:
            out = pipeline_run.check_document(
                text, llm=req_llm, limit=clamped,
                persist=req.persist, domain=req.domain,
                deadline_s=deadline, on_event=events.put,
            )
        except Exception:  # noqa: BLE001 — streamed, not raised
            # Generic message, mirroring the REST 500 envelope (no
            # traceback/detail leak into the stream).
            events.put({"type": "error",
                        "error": "analysis_failed",
                        "message": "Analysis failed."})
        else:
            events.put({"type": "done", "result": out})
        finally:
            events.put(None)

    threading.Thread(target=_run, daemon=True,
                     name="atlas-check-stream").start()

    def _generate():
        while True:
            event = events.get()
            if event is None:
                return
            yield "data: " + json.dumps(event) + "\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


@app.get("/api/reports")
def reports(
    limit: int = 50, api_key: str | None = Depends(require_api_key)
) -> dict:
    return {"reports": list_reports(limit=max(1, min(limit, 200)))}


@app.get("/api/reports/{report_id}", response_model=Report)
def report_detail(
    report_id: str, api_key: str | None = Depends(require_api_key)
) -> Report:
    report = get_report(report_id)
    if report is None:
        raise ApiError(404, "report_not_found", "Report not found.")
    return report


@app.delete("/api/reports/{report_id}")
def remove_report(
    report_id: str, api_key: str | None = Depends(require_api_key)
) -> dict:
    if get_report(report_id) is None:
        raise ApiError(404, "report_not_found", "Report not found.")
    delete_report(report_id)
    _cache_evict_report(report_id)
    return {"deleted": True}


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
