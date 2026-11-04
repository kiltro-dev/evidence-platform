"""V7 FRED connector: official api.stlouisfed.org, key-gated, no scraping.

Official JSON API (terms: key required, attribution "Federal Reserve
Bank of St. Louis"). Only called from
:func:`live.swarm.altroutes.attempt_altroutes` inside its per-claim
budget, and only when ``FRED_API_KEY`` is set — otherwise the route is
recorded skipped with zero packets.

Key hygiene: the key travels in the request query (FRED's design) but
NEVER persists — fetches bypass the response cache and the returned
``FetchResult`` urls are redacted before packaging/snapshots.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from typing import Any
from urllib.parse import quote as url_quote

FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_KEY_ENV = "FRED_API_KEY"
REDACTED = "key-redacted"

#: series -> {patterns (EN+ES), unit, scale}. Explicit names only:
#: "inflation" alone does NOT match (could be PCE), "GDP" alone does
#: NOT match (could be nominal) — the claim must name the FRED concept.
FRED_SERIES: dict[str, dict[str, Any]] = {
    "FEDFUNDS": {
        "patterns": (r"fed(?:eral)?\s+funds(?:\s+effective)?\s+rate",
                     r"tasa\s+de\s+fondos\s+federales",
                     r"fondos\s+federales"),
        "unit": "percent", "scale": 1.0},
    "DGS10": {
        "patterns": (r"10[-\s]?year\s+treasury",
                     r"bono(?:s)?\s+a\s+10\s+a[ñn]os",
                     r"treasury\s+10\s+years?"),
        "unit": "percent", "scale": 1.0},
    "CPIAUCSL": {
        "patterns": (r"\bcpi\b", r"consumer\s+price\s+index",
                     r"\bipc\b", r"[íi]ndice\s+de\s+precios\s+al\s+consumidor"),
        "unit": "index_1982_84_100", "scale": 1.0},
    "UNRATE": {
        "patterns": (r"unemployment\s+rate",
                     r"tasa\s+de\s+desempleo",
                     r"\bu3\b\s+unemployment"),
        "unit": "percent", "scale": 1.0},
    "GDPC1": {
        "patterns": (r"real\s+gdp", r"\bpib\s+real\b",
                     r"gdp\s+in\s+chained.*dollars",
                     r"pib\s+en\s+d[óo]lares\s+encadenados"),
        "unit": "USD", "scale": 1e9},
    "PAYEMS": {
        "patterns": (r"nonfarm\s+payrolls?", r"n[óo]minas\s+no\s+agr[íi]colas",
                     r"payroll\s+employment"),
        "unit": "persons", "scale": 1e3},
}


def match_fred_series(text: str) -> list[str]:
    """Series ids whose concept is named in ``text`` (pure, EN+ES)."""
    try:
        doc = str(text or "")
    except Exception:
        return []
    hits: list[str] = []
    for sid, spec in FRED_SERIES.items():
        for pat in spec["patterns"]:
            try:
                if re.search(pat, doc, re.IGNORECASE):
                    hits.append(sid)
                    break
            except re.error:
                continue
    return hits


def fred_url(series_id: str, key: str,
             year: int | None = None) -> str:
    """Observations URL, year-bounded when a year is known (pure)."""
    sid = url_quote(str(series_id).strip().upper())
    params = [f"series_id={sid}", f"api_key={url_quote(key)}",
              "file_type=json"]
    if isinstance(year, int) and 1900 <= year <= 2100:
        params.append(f"observation_start={year}-01-01")
        params.append(f"observation_end={year + 1}-01-01")
    else:
        params.append("sort_order=desc")
        params.append("limit=4")
    return f"{FRED_API_URL}?" + "&".join(params)


def parse_observations(body: str) -> list[dict]:
    """``[{date, value}]`` from a FRED observations body (pure)."""
    try:
        payload = json.loads(body or "")
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    obs = payload.get("observations")
    if not isinstance(obs, list):
        return []
    out = []
    for row in obs:
        if not isinstance(row, dict):
            continue
        date = row.get("date")
        value = row.get("value")
        if isinstance(date, str) and isinstance(value, str) and value != ".":
            out.append({"date": date, "value": value})
    return out


def pick_observation(observations: list[dict],
                     year: int | None = None,
                     month: int | None = None) -> dict | None:
    """Latest observation in the requested period (pure)."""
    cands = [o for o in observations if len(o.get("date", "")) >= 7]
    if isinstance(year, int):
        cands = [o for o in cands if o["date"][:4] == f"{year:04d}"]
        if isinstance(month, int) and 1 <= month <= 12:
            in_month = [o for o in cands
                        if o["date"][5:7] == f"{month:02d}"]
            if in_month:
                cands = in_month
    if not cands:
        return None
    return sorted(cands, key=lambda o: o["date"])[-1]


def fred_quote_for(body: str, observation: dict) -> str | None:
    """Verbatim ``"value":"…"`` span of the picked observation (pure).

    Value-only BY DESIGN: the ``"date":"2026-09-01"`` fragment would
    inject live numeric candidates (9, 1) into the verifier's
    closest-match, so a claim of "1 percent" could falsely match the
    DAY. The vintage travels in ``source_year``/``vintage`` meta +
    the snapshot, never in the quote.
    """
    try:
        date = observation.get("date", "")
        value = observation.get("value", "")
        if not date or not value:
            return None
        # The observation must exist as a date+value pair (guards
        # against quoting a stray value); the QUOTE is value-only.
        pair = re.compile(
            r'"date"\s*:\s*"' + re.escape(date) + r'"\s*,\s*"value"\s*:\s*"'
            + re.escape(value) + r'"')
        if not pair.search(body or ""):
            return None
        needle = re.compile(r'"value"\s*:\s*"' + re.escape(value) + r'"')
        match = needle.search(body or "")
        return match.group(0) if match else None
    except (re.error, TypeError, AttributeError):
        return None


def fred_source_meta(series_id: str) -> dict | None:
    """``{indicator, unit, scale}`` SourceMeta shape (pure)."""
    try:
        spec = FRED_SERIES.get(str(series_id or "").strip().upper())
    except Exception:
        return None
    if not spec:
        return None
    return {"indicator": str(series_id).strip().upper(),
            "unit": spec["unit"], "scale": float(spec["scale"])}


def fred_vintage(observation: dict) -> str | None:
    """``YYYY-MM`` vintage, or None (pure)."""
    try:
        date = observation.get("date", "")
        return date[:7] if len(date) >= 7 else None
    except (AttributeError, TypeError):
        return None


def read_key() -> str:
    """FRED key from env, stripped; empty when unset (impure leaf)."""
    try:
        return (os.environ.get(FRED_KEY_ENV) or "").strip()
    except Exception:
        return ""


def redact_result(fetched: Any, key: str) -> Any:
    """Copy of ``fetched`` with the key scrubbed from stored urls."""
    if not key:
        return fetched
    try:
        return replace(
            fetched,
            url=str(getattr(fetched, "url", "")).replace(key, REDACTED),
            final_url=str(getattr(fetched, "final_url", ""))
            .replace(key, REDACTED))
    except Exception:
        return fetched
