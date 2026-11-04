"""V5-CATB alternative official routes for category-b (2026 vintage) claims.

Category b (IMF WEO Oct-2026, BLS Employment Situation Sep-2026) scores
0/42 on the corpus and 0/6 on dev because its primaries bot-wall
automated reads (``imf.org`` / ``bls.gov`` Akamai 403) and the keyless
structured fallbacks are either key-gated, robots-disallowed, or
unreachable from here. Every candidate below was probed live on
2026-11-02 (single polite requests, ``atlas-live-research`` UA); the
full evidence table lives in ``live/V5_CATB.md``.

Registry verdict (2026-11-02):

- ``bls-api`` (api.bls.gov POST): keyless WORKS (observed
  ``REQUEST_SUCCEEDED`` + exact Sep-2026 figures) but ``robots.txt`` is
  ``Disallow: /`` -> implemented, policy-refused at runtime.
- ``fred-csv`` / ``fred-api``: host stalls all GETs here (0 bytes);
  JSON API needs ``api_key`` -> not implemented (no transport).
- ``bea-api``: key-gated (``Invalid API UserId``) -> not implemented.
- ``census-api``: now serves a ``Missing Key`` page keyless -> not
  implemented (and carries no BLS/IMF series anyway).
- ``imf-weo`` / ``imf-datamapper`` (www.imf.org): 403 bot-wall.
- ``imf-sdmx`` (api.imf.org): gateway 404/502 on documented paths; the
  legacy host is NXDOMAIN. No keyless WEO route found.
- ``imf-elibrary``: homepage 200, but ``/search`` and ``/display/book``
  403 for automated clients -> no deterministic content URL.
- ``datagov``: CKAN API v3 ``Not Found`` (catalog migrated); no
  WEO/BLS-Sep-2026 dataset URL discovered -> no candidate resource.
- ``nber``: open (200) but collections are papers/lectures/microdata
  programs; no WEO/empsit-headline mirror exists.

What this module DOES ship (all offline-testable, zero model cost):

- :func:`is_blike_claim`: pure b-like topic matcher (IMF/BLS classes,
  EN+ES). No claim ids, no per-claim rules.
- :func:`attempt_altroutes`: deterministic route attempt capped at
  ``MAX_ALT_CALLS_PER_CLAIM`` (2) network calls. Registry-verified
  403-walled routes are skipped WITHOUT sending requests (politeness);
  the BLS API route IS attempted live so the repo's robots policy
  refuses it locally (cached ``robots.txt`` lookup, no POST sent).
- BLS response parsing -> verbatim quote span + ``SourceMeta``-shaped
  ``{indicator, unit, scale}`` + vintage (``source_year`` + ``vintage``
  string; the package schema has no vintage field).
- PDF leg: :func:`pdf_quote_exists` (deterministic literal check of a
  quote against archived extracted text) + content-addressed sidecar
  archive + :func:`ingest_open_pdf`. No open primary PDF exists for b
  today, so the live path is untested-live by necessity; see
  ``live/V5_CATB.md`` for the verifier-side gap that keeps even
  extracted quotes from deciding (the verifier reads snapshot bytes,
  never the sidecar).

Scope: this module never imports the coordinator (no cycles) and never
touches the network except through injected ``fetch``/``post``
callables, so every path runs offline in tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# viability registry (verified 2026-11-02; evidence in live/V5_CATB.md)
# ---------------------------------------------------------------------------

#: route -> (viable_today, one-line reason). ``attempt_altroutes`` only
#: sends network requests for viable routes; every other route is
#: recorded as tried-skipped with its reason (no packets).
ROUTES: dict[str, tuple[bool, str]] = {
    # POST-only JSON API: skipped by policy at runtime, never retried
    # blindly (robots Disallow:/ short-circuits before any POST).
    "bls-api": (False, "keyless works but robots.txt Disallow:/ refuses it"),
    "fred-csv": (False, "host stalls all GETs here (0 bytes, Akamai)"),
    "fred-api": (False, "requires api_key (400 without one)"),
    "bea-api": (False, "key-gated (Invalid API UserId)"),
    "census-api": (False, "keyless serves a Missing Key page"),
    "imf-weo": (False, "403 bot-wall (Akamai EdgeSuite)"),
    "imf-datamapper": (False, "403 bot-wall (Akamai EdgeSuite)"),
    "imf-sdmx": (False, "gateway 404/502; legacy host NXDOMAIN"),
    "imf-elibrary": (False, "/search + /display/book 403 for bots"),
    "datagov": (False, "catalog API moved; no candidate resource URL"),
    "nber": (False, "open but no WEO/empsit-headline collection"),
}


def route_status(name: str) -> tuple[bool, str] | None:
    """``(viable, reason)`` for a registry route, or None (pure)."""
    try:
        return ROUTES.get(str(name or "").strip().lower())
    except Exception:
        return None


#: Hard cap on network calls per claim inside attempt_altroutes.
MAX_ALT_CALLS_PER_CLAIM = 2

# ---------------------------------------------------------------------------
# b-like topic matchers (pure; EN+ES; never claim ids)
# ---------------------------------------------------------------------------

_BLS_NAME_RE = re.compile(r"bureau of labor statistics|\bbls\b", re.IGNORECASE)
_UNEMPLOY_RE = re.compile(r"unemploy|desemple", re.IGNORECASE)
_PARO_RE = re.compile(r"\bparo\b|\bparad[oa]s?\b", re.IGNORECASE)
_IMF_RE = re.compile(r"\bimf\b|\bfmi\b|world economic outlook",
                     re.IGNORECASE)
_OUTLOOK_RE = re.compile(r"\boutlook\b", re.IGNORECASE)
_OUTLOOK_TOPIC_RE = re.compile(
    r"project|global|growth|advanced|proyect|crecim|mundial|avanzad",
    re.IGNORECASE)


def match_bls(text: str) -> bool:
    """True when the claim is BLS-employment shaped (pure)."""
    try:
        doc = text or ""
    except Exception:
        return False
    try:
        return bool(_BLS_NAME_RE.search(doc)
                    or _UNEMPLOY_RE.search(doc)
                    or _PARO_RE.search(doc))
    except Exception:
        return False


def match_imf(text: str) -> bool:
    """True when the claim is IMF-outlook shaped (pure)."""
    try:
        doc = text or ""
    except Exception:
        return False
    try:
        if _IMF_RE.search(doc):
            return True
        return bool(_OUTLOOK_RE.search(doc)
                    and _OUTLOOK_TOPIC_RE.search(doc))
    except Exception:
        return False


def match_fred(text: str) -> bool:
    """True when the claim names a FRED-covered concept (pure, V7)."""
    try:
        from live.swarm import fred as _fred
        return bool(_fred.match_fred_series(text))
    except Exception:
        return False


def is_blike_claim(text: str) -> bool:
    """True for b-like (IMF/BLS/FRED-topic) claims (pure).

    Covers the corpus/dev b classes except the UN-population claim
    (S2-04), which has no altroute and is out of scope by design.
    V7 admits FRED topics (fed funds, CPI, unemployment, real GDP,
    10y treasury, payrolls) — they share the same per-claim budget.
    """
    return match_bls(text) or match_imf(text) or match_fred(text)


# ---------------------------------------------------------------------------
# BLS API route (implemented; policy-refused live — see module docstring)
# ---------------------------------------------------------------------------

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

#: series -> {label, unit (UNIT_VOCAB), scale to canonical unit}.
#: LNS14000000: unemployment rate, percent, monthly.
#: LNS13000000: unemployed level, THOUSANDS of persons (scale x1000).
BLS_SERIES: dict[str, dict[str, Any]] = {
    "LNS14000000": {"label": "Unemployment rate",
                    "unit": "percent", "scale": 1.0, "kind": "rate"},
    "LNS13000000": {"label": "Unemployed level",
                    "unit": "count", "scale": 1000.0, "kind": "level"},
}

_RATE_CUE_RE = re.compile(
    r"\brate\b|\btasa\b|percent|por ciento|%", re.IGNORECASE)
_LEVEL_CUE_RE = re.compile(
    r"millions?|millones|mill[oó]n|people|personas?|desempleados?|"
    r"parados?|unemployed\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20|21)\d{2}\b")
_MONTHS = (
    ("january", "enero", "jan"),
    ("february", "febrero", "feb"),
    ("march", "marzo", "mar"),
    ("april", "abril", "apr", "abr"),
    ("may", "mayo", "may"),
    ("june", "junio", "jun"),
    ("july", "julio", "jul"),
    ("august", "agosto", "aug", "ago"),
    ("september", "septiembre", "sept", "sep"),
    ("october", "octubre", "oct"),
    ("november", "noviembre", "nov"),
    ("december", "diciembre", "dec", "dic"),
)
_MONTH_RE = re.compile(
    r"\b(" + "|".join(alias for group in _MONTHS for alias in group)
    + r")\b", re.IGNORECASE)


def _month_alias_to_number(alias: str) -> int | None:
    clean = (alias or "").strip().lower()
    for idx, group in enumerate(_MONTHS, 1):
        if clean in group:
            return idx
    return None


def match_bls_series(text: str) -> list[str]:
    """BLS series ids for a claim, priority-ordered (pure).

    Rate cues -> LNS14000000 only; level cues -> LNS13000000 only;
    both/neither -> both (the attempt budget of 2 covers the pair).
    Non-BLS claims get [] (IMF percent claims must NOT try BLS).
    """
    if not match_bls(text):
        return []
    try:
        doc = text or ""
        rate = bool(_RATE_CUE_RE.search(doc))
        level = bool(_LEVEL_CUE_RE.search(doc))
    except Exception:
        return []
    if rate and not level:
        return ["LNS14000000"]
    if level and not rate:
        return ["LNS13000000"]
    return ["LNS14000000", "LNS13000000"]


def year_from_text(text: str) -> int | None:
    """First 1960-2100 year in the text, or None (pure)."""
    try:
        match = _YEAR_RE.search(text or "")
    except Exception:
        return None
    if not match:
        return None
    try:
        year = int(match.group(0))
    except (TypeError, ValueError):
        return None
    return year if 1960 <= year <= 2100 else None


def month_from_text(text: str) -> int | None:
    """First EN/ES month name in the text (1-12), or None (pure)."""
    try:
        match = _MONTH_RE.search(text or "")
    except Exception:
        return None
    if not match:
        return None
    try:
        return _month_alias_to_number(match.group(1))
    except Exception:
        return None


def bls_payload(series_ids: list[str], year: int | None = None) -> dict:
    """Exact JSON payload for one BLS API call (pure).

    Single-year window when the claim states a year; otherwise the
    payload carries no years and the API defaults to recent years
    (the attempt then quotes the ``latest`` observation).
    """
    ids = [str(s or "").strip().upper() for s in (series_ids or [])]
    ids = [s for s in ids if s][:25]
    payload: dict[str, Any] = {"seriesid": ids}
    if isinstance(year, int) and 1960 <= year <= 2100:
        payload["startyear"] = str(year)
        payload["endyear"] = str(year)
    return payload


def parse_bls_observations(body: str) -> list[dict]:
    """``[{year, period, period_name, latest, value}]`` newest-first.

    Parses a BLS API ``REQUEST_SUCCEEDED`` body (any series); anything
    else (error payloads, HTML, garbage) -> []. Never raises.
    """
    try:
        import json as _json

        data = _json.loads(body or "")
    except (ValueError, TypeError):
        return []
    try:
        if not isinstance(data, dict):
            return []
        if str(data.get("status") or "").upper() != "REQUEST_SUCCEEDED":
            return []
        results = data.get("Results") or {}
        series = results.get("series") or []
        if not series or not isinstance(series[0], dict):
            return []
        out: list[dict] = []
        for row in series[0].get("data") or []:
            if not isinstance(row, dict):
                continue
            try:
                year = int(str(row.get("year") or "").strip())
                value = float(str(row.get("value") or "").strip())
            except (TypeError, ValueError):
                continue
            out.append({
                "year": year,
                "period": str(row.get("period") or "").strip(),
                "period_name": str(row.get("periodName") or "").strip(),
                "latest": str(row.get("latest") or "").strip().lower()
                == "true",
                "value": value,
            })
        return out
    except Exception:
        return []


def pick_observation(observations: list[dict], year: int | None = None,
                     month: int | None = None) -> dict | None:
    """Requested (year, month) obs, else the ``latest`` one (pure).

    Month matches the ``MNN`` period; a year without month picks that
    year's newest period; no year picks the API-flagged latest row
    (first row fallback). Never raises.
    """
    rows = [r for r in (observations or []) if isinstance(r, dict)]
    if not rows:
        return None
    try:
        if isinstance(year, int):
            pool = [r for r in rows if r.get("year") == year] or rows
        else:
            pool = rows
        if isinstance(month, int) and 1 <= month <= 12:
            want = f"M{month:02d}"
            for row in pool:
                if str(row.get("period") or "").upper() == want:
                    return row
        if isinstance(year, int):
            return pool[0]
        for row in pool:
            if row.get("latest") is True:
                return row
        return pool[0]
    except Exception:
        return None


def bls_quote_for(body: str, observation: dict) -> str | None:
    """Verbatim JSON-object span for one observation, or None (pure).

    Returns the MATCHED span (verbatim by construction): the full
    ``{...}`` row object carrying this observation's year/period/value.
    """
    try:
        year = str(observation.get("year"))
        period = re.escape(str(observation.get("period") or ""))
        value = re.escape(str(observation.get("value")
                              if isinstance(observation.get("value"), str)
                              else repr(float(observation["value"]))))
    except (KeyError, TypeError, ValueError):
        return None
    # The API renders values plainly ("4.2", "7109"); match the float
    # both raw and quote-wrapped ("value":"4.2").
    try:
        numeric = float(observation["value"])
    except (KeyError, TypeError, ValueError):
        return None
    spellings = {repr(numeric)}
    if numeric == int(numeric):
        spellings.add(str(int(numeric)))
    for spelling in sorted(spellings, key=len, reverse=True):
        # Row-anchored at '{"year"'; the tail tolerates one nesting
        # level (the "footnotes":[{}] object) so the span closes at
        # the row's own brace. Matched span => verbatim by design.
        pattern = re.compile(
            r"\{\s*\"year\"\s*:\s*\"" + re.escape(year) + r"\""
            r"[^{}]*\"period\"\s*:\s*\"" + period + r"\""
            r"[^{}]*\"value\"\s*:\s*\"?" + re.escape(spelling) + r"\"?"
            r"(?:[^{}]|\{[^{}]*\})*?\}")
        try:
            match = pattern.search(body or "")
        except Exception:
            return None
        if match:
            return match.group(0)
    return None


def bls_source_meta(series_id: str) -> dict | None:
    """``SourceMeta``-shaped ``{indicator, unit, scale}`` (pure).

    Shape-validated downstream by ``EvidencePackage.from_dict``; the
    verifier re-derives meta from WB snapshots only, so BLS meta stays
    inert until verifier-side support lands (documented gap).
    """
    try:
        spec = BLS_SERIES.get(str(series_id or "").strip().upper())
    except Exception:
        return None
    if not spec:
        return None
    return {"indicator": str(series_id).strip().upper(),
            "unit": spec["unit"], "scale": float(spec["scale"])}


def bls_vintage(observation: dict) -> str | None:
    """``YYYY-MM`` vintage for an observation, or None (pure)."""
    try:
        year = int(observation.get("year"))
        period = str(observation.get("period") or "").upper()
    except (TypeError, ValueError, AttributeError):
        return None
    match = re.fullmatch(r"M(\d{2})", period)
    if not (1960 <= year <= 2100) or not match:
        return None
    return f"{year}-{match.group(1)}"


@dataclass(frozen=True)
class AltCandidate:
    """One quotable altroute hit (fetch already snapshotted)."""

    url: str
    quote: str  # verbatim substring of the snapshot text
    source_meta: dict | None  # SourceMeta-shaped or None
    source_year: int | None
    vintage: str | None  # observation vintage (YYYY-MM) for BLS rows
    fetch_result: Any  # FetchResult (kept untyped: fetch-module seam)


@dataclass(frozen=True)
class AltAttempt:
    """Outcome of :func:`attempt_altroutes` (budget-capped)."""

    candidates: list[AltCandidate] = field(default_factory=list)
    calls_made: int = 0  # network attempts started (refusals count)
    routes_tried: list[dict] = field(default_factory=list)


def attempt_altroutes(
    text: str,
    post: Callable[..., Any],
    *,
    snapshot_dir: str | Path | None = None,
    budget: int = MAX_ALT_CALLS_PER_CLAIM,
    get: Callable[..., Any] | None = None,
) -> AltAttempt:
    """Deterministic altroute attempt for one claim (sync, capped).

    ``post`` is ``fetch_post`` (or a test double) with signature
    ``post(url, payload_dict, snapshot_dir=...)``; ``get`` is the GET
    seam (``fetch_url`` or a double) used ONLY by the V7 FRED leg —
    ``None`` skips FRED with zero packets. At most ``budget``
    network attempts (default 2); registry-dead routes are recorded
    as skipped WITHOUT network traffic. Never raises on
    claim/post-shaped input: transport failures become ``refused``
    entries with the exception name as reason.
    """
    tried: list[dict] = []
    try:
        blike = is_blike_claim(text)
    except Exception:
        return AltAttempt([], 0, tried)
    if not blike:
        return AltAttempt([], 0, tried)
    try:
        if match_imf(text):
            viable, reason = ROUTES["imf-weo"]
            tried.append({"route": "imf-weo", "action": "skipped",
                          "reason": reason})
    except Exception:
        pass
    try:
        series = match_bls_series(text)
    except Exception:
        series = []
    try:
        year = year_from_text(text)
        month = month_from_text(text)
    except Exception:
        year, month = None, None
    candidates: list[AltCandidate] = []
    calls = 0
    cap = max(0, int(budget or 0))
    for sid in series:
        if calls >= cap:
            tried.append({"route": f"bls-api/{sid}", "action": "skipped",
                          "reason": "budget exhausted"})
            break
        payload = bls_payload([sid], year)
        calls += 1
        try:
            fetched = post(BLS_API_URL, payload,
                           snapshot_dir=snapshot_dir)
        except Exception as exc:  # robots/transport refusal: recorded
            tried.append({"route": f"bls-api/{sid}", "action": "refused",
                          "reason": f"{type(exc).__name__}"})
            continue
        try:
            body = fetched.text or ""
            obs = pick_observation(parse_bls_observations(body),
                                   year, month)
            quote = bls_quote_for(body, obs) if obs else None
        except Exception:
            obs, quote = None, None
        if obs is None or not quote:
            tried.append({"route": f"bls-api/{sid}",
                          "action": "no-observation",
                          "reason": "requested period not in response"})
            continue
        try:
            url = fetched.final_url or fetched.url
        except Exception:
            url = BLS_API_URL
        candidates.append(AltCandidate(
            url=url, quote=quote, source_meta=bls_source_meta(sid),
            source_year=obs.get("year")
            if isinstance(obs.get("year"), int) else None,
            vintage=bls_vintage(obs), fetch_result=fetched))
        tried.append({"route": f"bls-api/{sid}", "action": "candidate",
                      "reason": f"vintage {bls_vintage(obs)}"})
    # V7 FRED leg: remaining budget only, key-gated, GET seam.
    try:
        fred_out = _fred_leg(text, get, snapshot_dir, cap - calls, tried,
                             year, month)
    except Exception:
        fred_out = AltAttempt([], 0, tried)
    return AltAttempt(candidates + fred_out.candidates,
                      calls + fred_out.calls_made, fred_out.routes_tried)


def _fred_leg(text: str, get: Callable[..., Any] | None,
              snapshot_dir: str | Path | None, remaining: int,
              tried: list[dict], year: int | None = None,
              month: int | None = None) -> AltAttempt:
    """FRED observations via official API (V7; key-gated; capped).

    Zero packets when ``get`` is None, no series matches, or
    ``FRED_API_KEY`` is unset. Cache bypassed + urls redacted so the
    key never persists. Never raises on claim-shaped input.
    """
    from live.swarm import fred as _fred

    try:
        series = _fred.match_fred_series(text)
    except Exception:
        series = []
    if not series:
        return AltAttempt([], 0, tried)
    if get is None:
        for sid in series:
            tried.append({"route": f"fred-api/{sid}", "action": "skipped",
                          "reason": "no GET seam"})
        return AltAttempt([], 0, tried)
    key = _fred.read_key()
    if not key:
        for sid in series:
            tried.append({"route": f"fred-api/{sid}", "action": "skipped",
                          "reason": "FRED_API_KEY unset"})
        return AltAttempt([], 0, tried)
    candidates: list[AltCandidate] = []
    calls = 0
    cap = max(0, int(remaining or 0))
    for sid in series:
        if calls >= cap:
            tried.append({"route": f"fred-api/{sid}", "action": "skipped",
                          "reason": "budget exhausted"})
            break
        calls += 1
        try:
            fetched = get(_fred.fred_url(sid, key, year),
                          snapshot_dir=snapshot_dir, use_cache=False)
            fetched = _fred.redact_result(fetched, key)
        except Exception as exc:
            tried.append({"route": f"fred-api/{sid}", "action": "refused",
                          "reason": f"{type(exc).__name__}"})
            continue
        try:
            body = fetched.text or ""
            obs = _fred.pick_observation(
                _fred.parse_observations(body), year, month)
            quote = _fred.fred_quote_for(body, obs) if obs else None
        except Exception:
            obs, quote = None, None
        if obs is None or not quote:
            tried.append({"route": f"fred-api/{sid}",
                          "action": "no-observation",
                          "reason": "requested period not in response"})
            continue
        try:
            url = fetched.final_url or fetched.url
        except Exception:
            url = _fred.FRED_API_URL
        try:
            source_year = int(str(obs.get("date", ""))[:4])
        except (TypeError, ValueError):
            source_year = None
        candidates.append(AltCandidate(
            url=url, quote=quote, source_meta=_fred.fred_source_meta(sid),
            source_year=source_year,
            vintage=_fred.fred_vintage(obs), fetch_result=fetched))
        tried.append({"route": f"fred-api/{sid}", "action": "candidate",
                      "reason": f"vintage {_fred.fred_vintage(obs)}"})
    return AltAttempt(candidates, calls, tried)


# ---------------------------------------------------------------------------
# PDF leg: archived-text sidecar + deterministic quote check
# ---------------------------------------------------------------------------

def _normalize_ws(text: str) -> str:
    """Whitespace-normalized text for literal containment (pure)."""
    try:
        return " ".join(str(text or "").split())
    except Exception:
        return ""


def pdf_quote_exists(quote: str, archived_text: str) -> bool:
    """True when the quote is a literal span of the archived text.

    Deterministic consistency check (pure): both sides are
    whitespace-normalized, then plain substring. Empty quotes never
    match. Never raises.
    """
    try:
        needle = _normalize_ws(quote)
        haystack = _normalize_ws(archived_text)
    except Exception:
        return False
    if not needle:
        return False
    try:
        return needle in haystack
    except Exception:
        return False


def archive_pdf_text(sha256: str, text: str,
                     snapshot_dir: str | Path) -> Path:
    """Persist extracted PDF text as ``data/snap/{sha}.pdftxt``.

    Content-addressed sidecar next to the raw-bytes snapshot (atomic
    tmp + rename; never modified afterwards, like snapshots).
    """
    digest = str(sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("archive_pdf_text needs a sha256 hex digest")
    snap_dir = Path(snapshot_dir)
    snap_dir.mkdir(parents=True, exist_ok=True)
    target = snap_dir / f"{digest}.pdftxt"
    if not target.exists():
        tmp = target.with_suffix(".pdftmp")
        tmp.write_text(str(text or ""), encoding="utf-8")
        tmp.replace(target)
    return target


def read_archived_pdf_text(sha256: str,
                           snapshot_dir: str | Path) -> str | None:
    """Archived extracted text for a snapshot sha, or None."""
    try:
        path = Path(snapshot_dir) / f"{str(sha256).strip().lower()}.pdftxt"
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class PdfIngest:
    """Outcome of :func:`ingest_open_pdf`."""

    fetch_result: Any  # FetchResult for the PDF URL (bytes snapshotted)
    archived_text: str  # extracted text (stdlib, or ``reader`` when given)
    sidecar_path: str | None  # .pdftxt path, or None when empty/refused


def ingest_open_pdf(
    url: str,
    fetch: Callable[..., Any],
    *,
    snapshot_dir: str | Path | None = None,
    reader: Callable[[Any], str] | None = None,
) -> PdfIngest:
    """Fetch an OPEN primary PDF -> snapshot + archived text (sync).

    ``fetch`` is ``fetch_url`` (or a test double): robots.txt, the
    403/paywall refusals, and the size cap all still apply, so walled
    PDFs (BLS empsit, IMF WEO) raise before anything is archived.
    ``reader`` is the model-vision-PDF seam (``reader(fetch_result)``
    -> text); the default is the deterministic stdlib extraction the
    fetch already placed in ``FetchResult.text``. No in-repo model
    client takes PDF bytes today, so live calls always use the
    default; the seam exists for the client that adds document input.
    """
    fetched = fetch(url, snapshot_dir=snapshot_dir)
    if reader is not None:
        archived = reader(fetched)
    else:
        archived = fetched.text or ""
    archived = str(archived or "")
    sidecar: str | None = None
    if archived.strip():
        try:
            snap_dir = (Path(snapshot_dir) if snapshot_dir
                        else Path(fetched.snapshot_path).parent)
            sidecar = str(archive_pdf_text(fetched.sha256, archived,
                                           snap_dir))
        except (ValueError, OSError):
            sidecar = None
    return PdfIngest(fetch_result=fetched, archived_text=archived,
                     sidecar_path=sidecar)


__all__ = [
    "MAX_ALT_CALLS_PER_CLAIM",
    "BLS_API_URL",
    "BLS_SERIES",
    "ROUTES",
    "AltAttempt",
    "AltCandidate",
    "PdfIngest",
    "archive_pdf_text",
    "attempt_altroutes",
    "bls_payload",
    "bls_quote_for",
    "bls_source_meta",
    "bls_vintage",
    "ingest_open_pdf",
    "is_blike_claim",
    "match_bls",
    "match_bls_series",
    "match_imf",
    "month_from_text",
    "parse_bls_observations",
    "pdf_quote_exists",
    "pick_observation",
    "read_archived_pdf_text",
    "route_status",
    "year_from_text",
]
