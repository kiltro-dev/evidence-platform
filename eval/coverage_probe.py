"""Coverage probe: World Bank-only ("before") vs + FRED/OWID/Wikidata ("after").

12 claims: 6 covered by the World Bank connector today, 6 requiring the new
extra connectors (monthly US labor data, Fed policy rate, global CO2 totals,
capital/area facts). Run live::

    .venv/bin/python eval/coverage_probe.py

"Covered" is deliberately strict: a claim counts as covered only when at
least one retrieved Evidence matches its ``accept`` rule
``(connector, indicator)``. A World Bank *annual* unemployment row does NOT
cover a *monthly* unemployment claim, and a per-capita CO2 row does NOT
cover a world-total CO2 claim — granularity and semantics are part of
coverage. Results are written to ``eval/results/coverage_probe_<ts>.json``.

Cost: $0.00 — no model calls; only free keyless public APIs. HTTP request
counts are reported as the cost proxy.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.connectors import ProviderError  # noqa: E402
from atlas.connectors import extra as extra_mod  # noqa: E402
from atlas.connectors.extra import (  # noqa: E402
    FredConnector,
    OwidConnector,
    WikidataConnector,
)
from atlas.connectors.worldbank import WorldBankConnector  # noqa: E402
from atlas.core.types import GeoRef, StructuredClaim, TimeRef  # noqa: E402

USD_COST = 0.0  # no model calls anywhere in this probe


class CountingClient(httpx.Client):
    requests_made = 0

    def request(self, *args, **kwargs):  # type: ignore[override]
        type(self).requests_made += 1
        return super().request(*args, **kwargs)


CLAIMS = [
    # --- 6 covered by the World Bank connector today ---
    {
        "id": "wb_gdp_usa",
        "text": "The GDP of the United States was $27.36 trillion in 2023.",
        "iso3": "USA", "year": 2023,
        "accept": [("worldbank", "NY.GDP.MKTP.CD")],
    },
    {
        "id": "wb_unemp_deu",
        "text": "Unemployment in Germany was 3.1% in 2023.",
        "iso3": "DEU", "year": 2023,
        "accept": [("worldbank", "SL.UEM.TOTL.ZS")],
    },
    {
        "id": "wb_pop_jpn",
        "text": "The population of Japan was 125.12 million in 2023.",
        "iso3": "JPN", "year": 2023,
        "accept": [("worldbank", "SP.POP.TOTL")],
    },
    {
        "id": "wb_infl_bra",
        "text": "Inflation in Brazil was 4.59% in 2023.",
        "iso3": "BRA", "year": 2023,
        "accept": [("worldbank", "FP.CPI.TOTL.ZG")],
    },
    {
        "id": "wb_le_fra",
        "text": "Life expectancy in France was 82.5 years in 2021.",
        "iso3": "FRA", "year": 2021,
        "accept": [("worldbank", "SP.DYN.LE00.IN")],
    },
    {
        "id": "wb_gdpgrowth_chn",
        "text": "GDP growth in China was 5.2% in 2023.",
        "iso3": "CHN", "year": 2023,
        "accept": [("worldbank", "NY.GDP.MKTP.KD.ZG")],
    },
    # --- 6 NOT covered today (need the extra connectors) ---
    {
        "id": "fred_unrate_monthly",
        "text": "The US unemployment rate was 3.7% in January 2024.",
        "iso3": "USA", "year": 2024,
        "accept": [("fred", "UNRATE")],
        "note": "WB has only annual unemployment; the monthly value needs FRED.",
    },
    {
        "id": "fred_fedfunds",
        "text": "The US federal funds effective rate was 5.33% in January 2024.",
        "iso3": "USA", "year": 2024,
        "accept": [("fred", "FEDFUNDS")],
        "note": "No World Bank equivalent indicator.",
    },
    {
        "id": "fred_payems",
        "text": "US nonfarm payroll employment was 157 million in January 2024.",
        "iso3": "USA", "year": 2024,
        "accept": [("fred", "PAYEMS")],
        "note": "No World Bank equivalent indicator.",
    },
    {
        "id": "owid_co2_world",
        "text": "Annual CO2 emissions of the world were 37.8 billion tonnes in 2022.",
        "iso3": "WLD", "year": 2022,
        "accept": [("owid", "annual-co-emissions-by-region")],
        "note": "WB has per-capita CO2 only; world totals need OWID.",
    },
    {
        "id": "wd_capital_aus",
        "text": "The capital of Australia is Canberra.",
        "iso3": "AUS", "year": None,
        "accept": [("wikidata", "P36")],
        "note": "Existence fact; no statistical source covers it.",
    },
    {
        "id": "wd_area_bra",
        "text": "The area of Brazil is 8.51 million square kilometers.",
        "iso3": "BRA", "year": None,
        "accept": [("wikidata", "P2046")],
        "note": "No World Bank area indicator.",
    },
]


def _claim(spec: dict) -> StructuredClaim:
    time_refs = [TimeRef(year=spec["year"])] if spec["year"] else []
    return StructuredClaim(
        raw_text=spec["text"],
        subject=spec["text"],
        geo_refs=[GeoRef(iso3=spec["iso3"])],
        time_refs=time_refs,
    )


def _accepted(spec: dict, evidences: list) -> list:
    rules = set(tuple(a) for a in spec["accept"])
    return [
        e for e in evidences
        if (e.connector, e.payload.get("indicator")) in rules
    ]


def _run_phase(connectors: list, claims: list[dict]) -> tuple[list[dict], int]:
    CountingClient.requests_made = 0
    extra_mod.URLLIB_REQUESTS = 0
    rows = []
    for spec in claims:
        claim = _claim(spec)
        evidences: list = []
        errors: list[str] = []
        for conn in connectors:
            try:
                evidences.extend(conn.retrieve(claim, limit=5))
            except ProviderError as exc:
                # One compliant retry on 429 (WDQS outage rule: 1 req/min).
                if "429" in str(exc):
                    time.sleep(70)
                    try:
                        evidences.extend(conn.retrieve(claim, limit=5))
                        continue
                    except ProviderError as exc2:
                        errors.append(f"{conn.name}: {exc2}")
                    except Exception as exc2:  # probe must not die
                        errors.append(
                            f"{conn.name}: {type(exc2).__name__}: {exc2}"
                        )
                else:
                    errors.append(f"{conn.name}: {exc}")
            except Exception as exc:  # probe must not die on one claim
                errors.append(f"{conn.name}: {type(exc).__name__}: {exc}")
        rows.append(
            {
                "id": spec["id"],
                "n_evidence": len(evidences),
                "n_accepted": len(_accepted(spec, evidences)),
                "covered": bool(_accepted(spec, evidences)),
                "errors": errors,
            }
        )
    total = CountingClient.requests_made + extra_mod.URLLIB_REQUESTS
    return rows, total


def main() -> int:
    real_client = httpx.Client
    httpx.Client = CountingClient  # type: ignore[misc]
    try:
        t0 = time.time()
        before, req_before = _run_phase([WorldBankConnector()], CLAIMS)
        after, req_after = _run_phase(
            [WorldBankConnector(), FredConnector(), OwidConnector(),
             WikidataConnector()],
            CLAIMS,
        )
        elapsed = time.time() - t0
    finally:
        httpx.Client = real_client  # type: ignore[misc]

    cov_before = sum(r["covered"] for r in before)
    cov_after = sum(r["covered"] for r in after)
    result = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "claims": len(CLAIMS),
        "before": {"covered": cov_before, "requests": req_before, "rows": before},
        "after": {"covered": cov_after, "requests": req_after, "rows": after},
        "usd_cost": USD_COST,
        "elapsed_s": round(elapsed, 1),
    }
    out_dir = Path(__file__).resolve().parent / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"coverage_probe_{stamp}.json"
    out_path.write_text(json.dumps(result, indent=2))

    print(f"{'claim':22s} {'before':>8s} {'after':>8s}  detail")
    for b, a in zip(before, after):
        mark = lambda r: "COVERED" if r["covered"] else f"miss({r['n_evidence']})"
        print(f"{b['id']:22s} {mark(b):>8s} {mark(a):>8s}  "
              f"ev {b['n_evidence']}->{a['n_evidence']}")
    print(f"\ncoverage: {cov_before}/{len(CLAIMS)} -> "
          f"{cov_after}/{len(CLAIMS)}  "
          f"(+{cov_after - cov_before})")
    print(f"requests: {req_before} -> {req_after} total; "
          f"cost: ${USD_COST:.2f}; elapsed: {elapsed:.1f}s")
    print(f"results: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
