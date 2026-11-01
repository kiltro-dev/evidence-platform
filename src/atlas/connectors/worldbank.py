"""World Bank indicators connector (no API key required).

Source: https://api.worldbank.org/v2 — secondary source (aggregates
national statistics), direct evidence for macro-economic quantities.
Docs: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392
"""

from __future__ import annotations

import re

import httpx

from ..core.types import Evidence, StructuredClaim
from . import InvalidGeoError, as_provider_error, register
from .countries import COUNTRIES, resolve_country

BASE_URL = "https://api.worldbank.org/v2"
TIMEOUT_S = 20.0

# S1: ISO3 allowlist — shape plus membership, checked before any request.
ISO3_RE = re.compile(r"^[A-Z]{3}$")
KNOWN_ISO3 = frozenset(set(COUNTRIES.values()) | {"WLD", "EUU"})


def is_allowed_iso3(iso3: object) -> bool:
    return (
        isinstance(iso3, str)
        and ISO3_RE.match(iso3) is not None
        and iso3 in KNOWN_ISO3
    )

# keyword -> (indicator_code, label, unit)
INDICATORS: dict[str, tuple[str, str, str]] = {
    "gdp": ("NY.GDP.MKTP.CD", "GDP (current US$)", "USD"),
    "gross domestic product": ("NY.GDP.MKTP.CD", "GDP (current US$)", "USD"),
    "gdp per capita": ("NY.GDP.PCAP.CD", "GDP per capita (current US$)", "USD"),
    "population": ("SP.POP.TOTL", "Population, total", "count"),
    "inflation": ("FP.CPI.TOTL.ZG", "Inflation, consumer prices (annual %)", "percent"),
    "consumer prices": ("FP.CPI.TOTL.ZG", "Inflation, consumer prices (annual %)", "percent"),
    "unemployment": ("SL.UEM.TOTL.ZS", "Unemployment, total (% of labor force)", "percent"),
    "life expectancy": ("SP.DYN.LE00.IN", "Life expectancy at birth (years)", "years"),
    "co2": ("EN.ATM.CO2E.PC", "CO2 emissions (metric tons per capita)", "t"),
    "carbon": ("EN.ATM.CO2E.PC", "CO2 emissions (metric tons per capita)", "t"),
    "fertility": ("SP.DYN.TFRT.IN", "Fertility rate, total (births per woman)", "count"),
    "exports": ("NE.EXP.GNFS.ZS", "Exports of goods and services (% of GDP)", "percent"),
    "imports": ("NE.IMP.GNFS.ZS", "Imports of goods and services (% of GDP)", "percent"),
    "debt": ("GC.DOD.TOTL.GD.ZS", "Central government debt (% of GDP)", "percent"),
    "internet": ("IT.NET.USER.ZS", "Individuals using the Internet (% of population)", "percent"),
    "literacy": ("SE.ADT.LITR.ZS", "Literacy rate, adult total (% 15+)", "percent"),
    # --- Spanish (ES) keyword aliases (match_indicator is accent-sensitive,
    # so accented and unaccented variants are both listed) ---
    "pib": ("NY.GDP.MKTP.CD", "GDP (current US$)", "USD"),
    "producto interno bruto": ("NY.GDP.MKTP.CD", "GDP (current US$)", "USD"),
    "producto interior bruto": ("NY.GDP.MKTP.CD", "GDP (current US$)", "USD"),
    "pib per cápita": ("NY.GDP.PCAP.CD", "GDP per capita (current US$)", "USD"),
    "pib per capita": ("NY.GDP.PCAP.CD", "GDP per capita (current US$)", "USD"),
    "población": ("SP.POP.TOTL", "Population, total", "count"),
    "poblacion": ("SP.POP.TOTL", "Population, total", "count"),
    "inflación": ("FP.CPI.TOTL.ZG", "Inflation, consumer prices (annual %)", "percent"),
    "inflacion": ("FP.CPI.TOTL.ZG", "Inflation, consumer prices (annual %)", "percent"),
    "precios al consumidor": ("FP.CPI.TOTL.ZG", "Inflation, consumer prices (annual %)", "percent"),
    "desempleo": ("SL.UEM.TOTL.ZS", "Unemployment, total (% of labor force)", "percent"),
    "esperanza de vida": ("SP.DYN.LE00.IN", "Life expectancy at birth (years)", "years"),
    "carbono": ("EN.ATM.CO2E.PC", "CO2 emissions (metric tons per capita)", "t"),
    "fecundidad": ("SP.DYN.TFRT.IN", "Fertility rate, total (births per woman)", "count"),
    "exportaciones": ("NE.EXP.GNFS.ZS", "Exports of goods and services (% of GDP)", "percent"),
    "importaciones": ("NE.IMP.GNFS.ZS", "Imports of goods and services (% of GDP)", "percent"),
    "deuda": ("GC.DOD.TOTL.GD.ZS", "Central government debt (% of GDP)", "percent"),
    "alfabetización": ("SE.ADT.LITR.ZS", "Literacy rate, adult total (% 15+)", "percent"),
    "alfabetizacion": ("SE.ADT.LITR.ZS", "Literacy rate, adult total (% 15+)", "percent"),
}


def match_indicator(text: str) -> tuple[str, str, str] | None:
    lowered = text.lower()
    for keyword in sorted(INDICATORS, key=len, reverse=True):
        if keyword in lowered:
            return INDICATORS[keyword]
    return None


def fetch_observations(
    indicator: str, iso3: str, start_year: int, end_year: int
) -> list[dict]:
    url = f"{BASE_URL}/country/{iso3}/indicator/{indicator}"
    params = {"format": "json", "per_page": 50, "date": f"{start_year}:{end_year}"}
    try:
        with httpx.Client(timeout=TIMEOUT_S, follow_redirects=True) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise as_provider_error(exc, f"worldbank {indicator} {iso3}") from exc
    if not isinstance(data, list) or len(data) < 2 or not data[1]:
        return []
    return [row for row in data[1] if row.get("value") is not None]


class WorldBankConnector:
    name = "worldbank"
    domains = ("economy", "general")
    last_query: str | None = None

    def retrieve(self, claim: StructuredClaim, limit: int = 5) -> list[Evidence]:
        self.last_query = None
        text = f"{claim.raw_text} {claim.subject} {claim.indicator or ''}"
        matched = match_indicator(text)
        if matched is None:
            return []
        code, label, unit = matched
        _, iso3 = resolve_country(text)
        if iso3 is None and claim.geo_refs:
            iso3 = claim.geo_refs[0].iso3
        if iso3 is None:
            return []
        if not is_allowed_iso3(iso3):
            raise InvalidGeoError(iso3)
        years = [t.year for t in claim.time_refs if t.year] + [
            y
            for t in claim.time_refs
            for y in (t.start_year, t.end_year)
            if y is not None
        ]
        if years:
            start_year, end_year = min(years), max(years)
        else:
            start_year, end_year = 2020, 2023
        self.last_query = f"{code} {iso3} {start_year}:{end_year}"
        try:
            rows = fetch_observations(code, iso3, start_year, end_year)
        except (httpx.HTTPError, ValueError) as exc:
            raise as_provider_error(exc, f"worldbank {self.last_query}") from exc
        evidences: list[Evidence] = []
        for row in rows[:limit]:
            evidences.append(
                Evidence(
                    claim_id=claim.claim_id,
                    connector=self.name,
                    source_name="World Bank Open Data",
                    source_url=(
                        f"https://data.worldbank.org/indicator/{code}"
                        f"?locations={iso3}"
                    ),
                    source_kind="secondary",
                    evidence_kind="direct",
                    locator=f"{code} {iso3} {row.get('date')}",
                    excerpt=(
                        f"{label} for {row.get('country', {}).get('value', iso3)} "
                        f"in {row.get('date')}: {row.get('value')}"
                    ),
                    payload={
                        "indicator": code,
                        "label": label,
                        "unit": unit,
                        "iso3": iso3,
                        "year": row.get("date"),
                        "value": row.get("value"),
                        "query": self.last_query,
                    },
                )
            )
        return evidences


register(WorldBankConnector())
