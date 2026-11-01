"""Extra keyless public connectors: FRED, Our World in Data, Wikidata.

All three sources work without an API key (verified 2026-11-01):

- FRED: ``fredgraph.csv`` download endpoint (the official JSON API needs a
  key, so it is deliberately NOT used — see docs/CONNECTORS.md).
- Our World in Data: ``/grapher/<slug>.csv`` downloads.
- Wikidata: SPARQL endpoint with a polite User-Agent.

Every fetch returns :class:`Evidence` with provenance: ``source_url`` (the
canonical dataset URL), ``retrieved_at`` (UTC time of the fetch) and
``payload["source_vintage"]`` (the dataset's own currency marker — last
observation date for FRED/OWID, live-snapshot date for Wikidata).

Rate-limit posture: a shared in-memory TTL cache bounds repeat downloads
(FRED/OWID CSVs), one SPARQL query per retrieve, a contact User-Agent on
every request, and no retries (a failure surfaces as ProviderError).
"""

from __future__ import annotations

import csv
import io
import re
import threading
import time
from datetime import datetime, timezone

import httpx

from ..core.types import Evidence, StructuredClaim
from . import ProviderError, as_provider_error, register
from .countries import resolve_country
from .worldbank import is_allowed_iso3

TIMEOUT_S = 25.0
USER_AGENT = "EvidenceAtlas/0.4 (research fact-checking; contact: evidence-atlas)"

#: Counts stdlib-urllib requests (Wikidata Action API fallback), which the
#: probe's httpx counter cannot see. Read-only for callers; reset per run.
URLLIB_REQUESTS = 0

__all__ = [
    "FRED_SERIES",
    "OWID_DATASETS",
    "WD_PROPS",
    "WD_COUNTRIES",
    "fetch_fred",
    "fetch_owid",
    "fetch_wikidata",
    "fetch_fred_evidence",
    "fetch_owid_evidence",
    "fetch_wikidata_evidence",
    "FredConnector",
    "OwidConnector",
    "WikidataConnector",
    "clear_cache",
]


# ---------------------------------------------------------------------------
# HTTP helpers (single seam for test doubles)
# ---------------------------------------------------------------------------

def _get_text(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
) -> str:
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    try:
        with httpx.Client(timeout=TIMEOUT_S, follow_redirects=True) as client:
            resp = client.get(url, params=params, headers=hdrs)
            resp.raise_for_status()
            resp.read()
    except (httpx.HTTPError, ValueError) as exc:
        raise as_provider_error(exc, url) from exc
    return resp.text


def _get_json(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
) -> dict:
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    try:
        with httpx.Client(timeout=TIMEOUT_S, follow_redirects=True) as client:
            resp = client.get(url, params=params, headers=hdrs)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise as_provider_error(exc, url) from exc
    if not isinstance(data, dict):
        raise as_provider_error(
            ValueError(f"unexpected payload type {type(data).__name__}"), url
        )
    return data


# ---------------------------------------------------------------------------
# TTL cache for CSV downloads (thread-safe, bounded)
# ---------------------------------------------------------------------------

_CACHE_TTL_S = 3600.0
_CACHE_MAX_ENTRIES = 32
_cache: dict[str, tuple[float, str]] = {}
_cache_lock = threading.Lock()


def _cached_text(key: str, url: str, params: dict | None = None) -> str:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < _CACHE_TTL_S:
            return hit[1]
    text = _get_text(url, params=params)
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            del _cache[oldest]
        _cache[key] = (now, text)
    return text


def clear_cache() -> None:
    """Drop all cached downloads (tests, or to force fresh vintage)."""
    with _cache_lock:
        _cache.clear()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# FRED (Federal Reserve Bank of St. Louis) — US-only series, monthly/quarterly
# ---------------------------------------------------------------------------

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_SERIES_URL = "https://fred.stlouisfed.org/series"

# series_id -> (label, unit, semantic, keywords)
# semantic: "level" (a stock/index/amount) vs "rate" (a % rate).
FRED_SERIES: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "UNRATE": (
        "Unemployment rate", "percent", "rate",
        ("unemployment rate", "jobless rate", "unrate"),
    ),
    "FEDFUNDS": (
        "Federal funds effective rate", "percent", "rate",
        ("federal funds", "fed funds", "fedfunds"),
    ),
    "CPIAUCSL": (
        "Consumer Price Index for All Urban Consumers", "index", "level",
        ("consumer price index", "cpi index", "cpi "),
    ),
    "PAYEMS": (
        "All Employees, Total Nonfarm", "thousands of persons", "level",
        ("nonfarm payroll", "payroll employment", "payems"),
    ),
    "GDP": (
        "Gross Domestic Product (quarterly, nominal)", "billion USD", "level",
        ("quarterly gdp", "gdp quarterly", "nominal gdp"),
    ),
}

_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
)
_MONTH_RE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\b"
)


def _month_from_text(text: str) -> int | None:
    m = _MONTH_RE.search(text.lower())
    return _MONTHS.index(m.group(1)) + 1 if m else None


def fetch_fred(
    series_id: str,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """Download a FRED series as ``[{date, value}]`` (oldest first).

    ``start``/``end`` are ``YYYY-MM-DD`` bounds passed to the server.
    ``"."`` values (FRED's missing marker) are dropped.
    """
    params: dict[str, str] = {"id": series_id}
    if start:
        params["cosd"] = start
    if end:
        params["coed"] = end
    text = _cached_text(f"fred:{series_id}:{start}:{end}", FRED_CSV_URL, params)
    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    for raw in reader:
        date = (raw.get("observation_date") or "").strip()
        val = (raw.get(series_id) or "").strip()
        if not date or not val or val == ".":
            continue
        try:
            rows.append({"date": date, "value": float(val)})
        except ValueError:
            continue
    return rows


def fetch_fred_evidence(
    claim_id: str,
    series_id: str,
    limit: int = 5,
    year: int | None = None,
    month: int | None = None,
) -> list[Evidence]:
    """Fetch FRED observations as Evidence (newest first, year-filtered)."""
    if series_id not in FRED_SERIES:
        return []
    label, unit, semantic, _ = FRED_SERIES[series_id]
    start = f"{year}-01-01" if year else None
    end = f"{year}-12-31" if year else None
    rows = fetch_fred(series_id, start=start, end=end)
    if month:
        rows = [r for r in rows if r["date"][5:7] == f"{month:02d}"]
    retrieved = _utcnow()
    vintage = max((r["date"] for r in rows), default=None)
    evidences: list[Evidence] = []
    for row in sorted(rows, key=lambda r: r["date"], reverse=True)[:limit]:
        evidences.append(
            Evidence(
                claim_id=claim_id,
                connector="fred",
                source_name="FRED (Federal Reserve Bank of St. Louis)",
                source_url=f"{FRED_SERIES_URL}/{series_id}",
                source_kind="secondary",
                evidence_kind="direct",
                locator=f"{series_id} USA {row['date']}",
                excerpt=(
                    f"{label} for the United States on {row['date']}: "
                    f"{row['value']} ({unit})"
                ),
                payload={
                    "indicator": series_id,
                    "label": label,
                    "unit": unit,
                    "semantic": semantic,
                    "iso3": "USA",
                    "date": row["date"],
                    "value": row["value"],
                    "source_vintage": vintage,
                },
                retrieved_at=retrieved,
            )
        )
    return evidences


def _match_fred(text: str) -> str | None:
    lowered = text.lower()
    for series_id, (_, _, _, keywords) in FRED_SERIES.items():
        if any(k in lowered for k in keywords):
            return series_id
    return None


class FredConnector:
    """FRED monthly/quarterly US series. USA-only by construction."""

    name = "fred"
    domains = ("economy", "general")

    def __init__(self) -> None:
        self._query_local = threading.local()

    @property
    def last_query(self) -> str | None:
        return getattr(self._query_local, "value", None)

    @last_query.setter
    def last_query(self, value: str | None) -> None:
        self._query_local.value = value

    def retrieve(self, claim: StructuredClaim, limit: int = 5) -> list[Evidence]:
        self.last_query = None
        text = f"{claim.raw_text} {claim.subject} {claim.indicator or ''}"
        series_id = _match_fred(text)
        if series_id is None:
            return []
        # FRED series describe the United States only: any non-US geo is a
        # hard mismatch (return [], never misattributed evidence).
        _, iso3 = resolve_country(text)
        if iso3 is None and claim.geo_refs:
            iso3 = claim.geo_refs[0].iso3
        if iso3 is not None and iso3 != "USA":
            return []
        years = [t.year for t in claim.time_refs if t.year]
        year = years[0] if years else None
        month = _month_from_text(claim.raw_text)
        self.last_query = f"{series_id} USA {year or 'latest'}"
        return fetch_fred_evidence(
            claim.claim_id, series_id, limit=limit, year=year, month=month
        )


# ---------------------------------------------------------------------------
# Our World in Data — grapher CSVs (global, annual)
# ---------------------------------------------------------------------------

OWID_GRAPHER_URL = "https://ourworldindata.org/grapher"

# slug -> (label, unit, semantic, keywords)
OWID_DATASETS: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "annual-co-emissions-by-region": (
        "Annual CO2 emissions", "tonnes", "level",
        ("annual co2", "annual co₂", "annual carbon", "co2 emissions",
         "co₂ emissions", "carbon emissions", "greenhouse gas emissions"),
    ),
    "co-emissions-per-capita": (
        "CO2 emissions per capita", "tonnes", "level",
        ("co2 per capita", "co₂ per capita", "carbon per capita",
         "emissions per capita"),
    ),
    "life-expectancy": (
        "Life expectancy", "years", "level",
        ("life expectancy", "esperanza de vida"),
    ),
}


def fetch_owid(
    slug: str,
    iso3: str,
    year: int | None = None,
) -> list[dict]:
    """Download an OWID grapher CSV and filter to ``[{entity, year, value}]``.

    Entity matching is by ISO3 ``Code`` column; the ``World`` aggregate has
    an empty code, so ISO3 ``WLD`` falls back to the ``World`` entity name.
    """
    url = f"{OWID_GRAPHER_URL}/{slug}.csv"
    text = _cached_text(f"owid:{slug}", url)
    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or len(reader.fieldnames) < 4:
        return []
    value_col = reader.fieldnames[3]
    for raw in reader:
        code = (raw.get("Code") or "").strip().upper()
        entity = (raw.get("Entity") or "").strip()
        if iso3 == "WLD":
            if entity != "World":
                continue
        elif code != iso3:
            continue
        year_raw = (raw.get("Year") or "").strip()
        val_raw = (raw.get(value_col) or "").strip()
        if not year_raw or not val_raw:
            continue
        try:
            row_year = int(float(year_raw))
            value = float(val_raw)
        except ValueError:
            continue
        if year is not None and row_year != year:
            continue
        rows.append({"entity": entity, "year": row_year, "value": value})
    return rows


def fetch_owid_evidence(
    claim_id: str,
    slug: str,
    iso3: str,
    limit: int = 5,
    year: int | None = None,
) -> list[Evidence]:
    """Fetch OWID observations as Evidence (newest first)."""
    if slug not in OWID_DATASETS:
        return []
    if not is_allowed_iso3(iso3):
        return []
    label, unit, semantic, _ = OWID_DATASETS[slug]
    rows = fetch_owid(slug, iso3, year=year)
    retrieved = _utcnow()
    vintage = str(max((r["year"] for r in rows), default="")) or None
    evidences: list[Evidence] = []
    for row in sorted(rows, key=lambda r: r["year"], reverse=True)[:limit]:
        evidences.append(
            Evidence(
                claim_id=claim_id,
                connector="owid",
                source_name="Our World in Data",
                source_url=f"{OWID_GRAPHER_URL}/{slug}",
                source_kind="secondary",
                evidence_kind="direct",
                locator=f"{slug} {iso3} {row['year']}",
                excerpt=(
                    f"{label} for {row['entity']} in {row['year']}: "
                    f"{row['value']} ({unit})"
                ),
                payload={
                    "indicator": slug,
                    "label": label,
                    "unit": unit,
                    "semantic": semantic,
                    "iso3": iso3,
                    "year": row["year"],
                    "value": row["value"],
                    "source_vintage": vintage,
                },
                retrieved_at=retrieved,
            )
        )
    return evidences


def _match_owid(text: str) -> str | None:
    lowered = text.lower()
    # Longest keyword wins so "co2 per capita" beats "co2 emissions".
    best: str | None = None
    best_len = 0
    for slug, (_, _, _, keywords) in OWID_DATASETS.items():
        for k in keywords:
            if k in lowered and len(k) > best_len:
                best, best_len = slug, len(k)
    return best


class OwidConnector:
    name = "owid"
    domains = ("economy", "general")

    def __init__(self) -> None:
        self._query_local = threading.local()

    @property
    def last_query(self) -> str | None:
        return getattr(self._query_local, "value", None)

    @last_query.setter
    def last_query(self, value: str | None) -> None:
        self._query_local.value = value

    def retrieve(self, claim: StructuredClaim, limit: int = 5) -> list[Evidence]:
        self.last_query = None
        text = f"{claim.raw_text} {claim.subject} {claim.indicator or ''}"
        slug = _match_owid(text)
        if slug is None:
            return []
        _, iso3 = resolve_country(text)
        if iso3 is None and claim.geo_refs:
            iso3 = claim.geo_refs[0].iso3
        if iso3 is None:
            return []
        if not is_allowed_iso3(iso3):
            return []
        years = [t.year for t in claim.time_refs if t.year]
        year = years[0] if years else None
        self.last_query = f"{slug} {iso3} {year or 'latest'}"
        return fetch_owid_evidence(
            claim.claim_id, slug, iso3, limit=limit, year=year
        )


# ---------------------------------------------------------------------------
# Wikidata — SPARQL facts (capitals, population, area, inception)
# ---------------------------------------------------------------------------

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"

# ISO3 -> (QID, label). Covers the largest economies + probe geos.
WD_COUNTRIES: dict[str, tuple[str, str]] = {
    "USA": ("Q30", "United States"),
    "CHN": ("Q148", "China"),
    "JPN": ("Q17", "Japan"),
    "DEU": ("Q183", "Germany"),
    "GBR": ("Q145", "United Kingdom"),
    "FRA": ("Q142", "France"),
    "IND": ("Q668", "India"),
    "ITA": ("Q38", "Italy"),
    "BRA": ("Q155", "Brazil"),
    "CAN": ("Q16", "Canada"),
    "RUS": ("Q159", "Russia"),
    "KOR": ("Q884", "South Korea"),
    "AUS": ("Q408", "Australia"),
    "ESP": ("Q29", "Spain"),
    "MEX": ("Q96", "Mexico"),
    "IDN": ("Q252", "Indonesia"),
    "NLD": ("Q55", "Netherlands"),
    "SAU": ("Q851", "Saudi Arabia"),
    "TUR": ("Q43", "Turkey"),
    "CHE": ("Q39", "Switzerland"),
    "POL": ("Q36", "Poland"),
    "ARG": ("Q414", "Argentina"),
    "SWE": ("Q34", "Sweden"),
    "BEL": ("Q31", "Belgium"),
    "AUT": ("Q40", "Austria"),
    "NOR": ("Q20", "Norway"),
    "IRL": ("Q27", "Ireland"),
    "ZAF": ("Q258", "South Africa"),
    "CHL": ("Q298", "Chile"),
    "COL": ("Q739", "Colombia"),
    "PER": ("Q419", "Peru"),
    "PRT": ("Q45", "Portugal"),
    "GRC": ("Q41", "Greece"),
    "DNK": ("Q35", "Denmark"),
    "FIN": ("Q33", "Finland"),
    "EGY": ("Q79", "Egypt"),
    "NGA": ("Q1033", "Nigeria"),
    "KEN": ("Q114", "Kenya"),
    "VNM": ("Q881", "Vietnam"),
    "UKR": ("Q212", "Ukraine"),
}

# prop -> (label, semantic, kind, keywords)
# kind "quantity" returns numbers with optional point-in-time; "entity"
# returns a labeled entity; "time" returns a date.
WD_PROPS: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "P1082": (
        "population", "level", "quantity",
        ("population", "población", "poblacion", "inhabitants",
         "habitantes"),
    ),
    "P2046": (
        "area", "level", "quantity",
        ("area of", "surface area", "superficie", "square kilometer",
         "square kilometre", "km2", "km²"),
    ),
    "P36": (
        "capital", "other", "entity",
        ("capital of", "capital city", "capital es"),
    ),
    "P571": (
        "inception", "other", "time",
        ("founded in", "founded on", "established in", "inception",
         "independence in", "fundada", "fundado", "independencia"),
    ),
}

_LABEL_SERVICE = (
    "SERVICE wikibase:label { bd:serviceParam wikibase:language 'en'. }"
)


def _sparql_for(qid: str, prop: str) -> str:
    if prop == "P36":
        return (
            f"SELECT ?valueLabel WHERE {{ wd:{qid} wdt:{prop} ?value. "
            f"{_LABEL_SERVICE} }}"
        )
    if prop == "P571":
        return f"SELECT ?value WHERE {{ wd:{qid} wdt:{prop} ?value. }}"
    return (
        f"SELECT ?value ?time WHERE {{ wd:{qid} p:{prop} ?s. "
        f"?s ps:{prop} ?value. OPTIONAL {{ ?s pq:P585 ?time }} }} "
        f"ORDER BY DESC(?time) LIMIT 10"
    )


WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"


def fetch_wikidata(qid: str, prop: str) -> list[dict]:
    """Run the fact query for ``qid``/``prop``; rows are ``{value, time?}``.

    Primary path is SPARQL (WDQS). On transport failure (rate limits,
    outages) it falls back to the Action API ``wbgetentities`` endpoint —
    a different host, so this is failover, not a retry hammer. Rows carry
    ``_via`` (``"sparql"`` or ``"action-api"``) for transparency.
    """
    query = _sparql_for(qid, prop)
    try:
        data = _get_json(
            WIKIDATA_SPARQL_URL,
            params={"query": query, "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
        )
    except ProviderError:
        return _fetch_wikidata_action(qid, prop)
    rows: list[dict] = []
    for b in data.get("results", {}).get("bindings", []):
        val = b.get("value", b.get("valueLabel", {}))
        raw = val.get("value") if isinstance(val, dict) else None
        if raw is None or raw == "":
            continue
        row: dict = {"value": raw, "_via": "sparql"}
        t = b.get("time", {})
        if isinstance(t, dict) and t.get("value"):
            row["time"] = t["value"][:10]
        rows.append(row)
    return rows


def _get_text_urllib(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
) -> str:
    """GET via stdlib urllib (same contract as :func:`_get_text`).

    Exists because www.wikidata.org's bot mitigation 403s httpx's
    request fingerprint while allowing urllib/curl with the identical
    URL and User-Agent (observed 2026-11-01). Used only for the
    Wikidata Action API fallback.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    full = url
    if params:
        full += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers=hdrs)
    global URLLIB_REQUESTS
    URLLIB_REQUESTS += 1
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        transient = exc.code == 429 or (exc.code is not None and exc.code >= 500)
        raise ProviderError(f"{url}: HTTP {exc.code}: {exc.reason}",
                            transient=transient) from exc
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise ProviderError(f"{url}: {exc}", transient=True) from exc


def _cached_json(key: str, url: str, params: dict | None = None) -> dict:
    import json as _json

    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < _CACHE_TTL_S:
            return _json.loads(hit[1])
    text = _get_text_urllib(url, params=params)
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            del _cache[oldest]
        _cache[key] = (now, text)
    return _json.loads(text)


def _fetch_wikidata_action(qid: str, prop: str) -> list[dict]:
    """Same row shape as :func:`fetch_wikidata`, via ``wbgetentities``."""
    data = _cached_json(
        f"wdent:{qid}",
        WIKIDATA_API_URL,
        {"action": "wbgetentities", "ids": qid,
         "props": "claims", "format": "json"},
    )
    try:
        claims = data["entities"][qid]["claims"]
    except (KeyError, TypeError):
        return []
    rows: list[dict] = []
    for stmt in claims.get(prop, []):
        mainsnak = stmt.get("mainsnak", {})
        dv = mainsnak.get("datavalue", {})
        if mainsnak.get("snaktype") != "value" or not isinstance(dv, dict):
            continue
        value = dv.get("value")
        row: dict = {"_via": "action-api"}
        if prop == "P36" and isinstance(value, dict) and value.get("id"):
            label = _wikidata_label(value["id"])
            row["value"] = label or value["id"]
        elif prop == "P571" and isinstance(value, dict) and value.get("time"):
            row["value"] = value["time"].lstrip("+")[:10]
        elif isinstance(value, dict) and value.get("amount") is not None:
            row["value"] = str(value["amount"]).lstrip("+")
        elif isinstance(value, str):
            row["value"] = value
        else:
            continue
        quals = stmt.get("qualifiers", {}).get("P585", [])
        if quals:
            t = quals[0].get("datavalue", {}).get("value", {}).get("time", "")
            if isinstance(t, str) and t:
                row["time"] = t.lstrip("+")[:10]
        rows.append(row)
    # Newest first when point-in-time qualifiers exist.
    rows.sort(key=lambda r: r.get("time", ""), reverse=True)
    return rows


def _wikidata_label(qid: str) -> str | None:
    data = _cached_json(
        f"wdlabel:{qid}",
        WIKIDATA_API_URL,
        {"action": "wbgetentities", "ids": qid, "props": "labels",
         "languages": "en", "format": "json"},
    )
    try:
        return data["entities"][qid]["labels"]["en"]["value"]
    except (KeyError, TypeError):
        return None


def fetch_wikidata_evidence(
    claim_id: str,
    iso3: str,
    prop: str,
    limit: int = 5,
    year: int | None = None,
) -> list[Evidence]:
    """Fetch Wikidata facts as Evidence."""
    if prop not in WD_PROPS or iso3 not in WD_COUNTRIES:
        return []
    label, semantic, kind, _ = WD_PROPS[prop]
    qid, country = WD_COUNTRIES[iso3]
    rows = fetch_wikidata(qid, prop)
    if year is not None and kind == "quantity":
        dated = [r for r in rows if r.get("time", "")[:4] == str(year)]
        rows = dated or rows
    retrieved = _utcnow()
    # Wikidata is a live knowledge base with no dataset release marker;
    # vintage is the snapshot date of this retrieval.
    vintage = retrieved.date().isoformat()
    url = f"https://www.wikidata.org/wiki/{qid}#{prop}"
    evidences: list[Evidence] = []
    for row in rows[:limit]:
        excerpt = f"{country} {label}: {row['value']}"
        if row.get("time"):
            excerpt += f" (point in time {row['time']})"
        evidences.append(
            Evidence(
                claim_id=claim_id,
                connector="wikidata",
                source_name="Wikidata",
                source_url=url,
                source_kind="secondary",
                evidence_kind="direct",
                locator=f"{qid} {prop} {row.get('time', '')}".rstrip(),
                excerpt=excerpt,
                payload={
                    "indicator": prop,
                    "label": label,
                    "semantic": semantic,
                    "iso3": iso3,
                    "qid": qid,
                    "value": row["value"],
                    "time": row.get("time"),
                    "source_vintage": vintage,
                    "via": row.get("_via"),
                },
                retrieved_at=retrieved,
            )
        )
    return evidences


def _match_wikidata(text: str) -> str | None:
    lowered = text.lower()
    best: str | None = None
    best_len = 0
    for prop, (_, _, _, keywords) in WD_PROPS.items():
        for k in keywords:
            if k in lowered and len(k) > best_len:
                best, best_len = prop, len(k)
    return best


class WikidataConnector:
    name = "wikidata"
    domains = ("general",)

    def __init__(self) -> None:
        self._query_local = threading.local()

    @property
    def last_query(self) -> str | None:
        return getattr(self._query_local, "value", None)

    @last_query.setter
    def last_query(self, value: str | None) -> None:
        self._query_local.value = value

    def retrieve(self, claim: StructuredClaim, limit: int = 5) -> list[Evidence]:
        self.last_query = None
        text = f"{claim.raw_text} {claim.subject} {claim.indicator or ''}"
        prop = _match_wikidata(text)
        if prop is None:
            return []
        _, iso3 = resolve_country(text)
        if iso3 is None and claim.geo_refs:
            iso3 = claim.geo_refs[0].iso3
        if iso3 is None or iso3 not in WD_COUNTRIES:
            return []
        years = [t.year for t in claim.time_refs if t.year]
        year = years[0] if years else None
        self.last_query = f"{iso3} {prop} {year or 'any'}"
        return fetch_wikidata_evidence(
            claim.claim_id, iso3, prop, limit=limit, year=year
        )


register(FredConnector())
register(OwidConnector())
register(WikidataConnector())
