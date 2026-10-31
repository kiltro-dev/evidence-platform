"""World Bank indicators connector (no API key required).

Source: https://api.worldbank.org/v2 — secondary source (aggregates
national statistics), direct evidence for macro-economic quantities.
Docs: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392
"""

from __future__ import annotations

import httpx

from ..core.types import Evidence, StructuredClaim
from . import register
from .countries import resolve_country

BASE_URL = "https://api.worldbank.org/v2"
TIMEOUT_S = 20.0

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
    with httpx.Client(timeout=TIMEOUT_S, follow_redirects=True) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, list) or len(data) < 2 or not data[1]:
        return []
    return [row for row in data[1] if row.get("value") is not None]


class WorldBankConnector:
    name = "worldbank"
    domains = ("economy", "general")

    def retrieve(self, claim: StructuredClaim, limit: int = 5) -> list[Evidence]:
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
        try:
            rows = fetch_observations(code, iso3, start_year, end_year)
        except (httpx.HTTPError, ValueError):
            return []
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
                    },
                )
            )
        return evidences


register(WorldBankConnector())
