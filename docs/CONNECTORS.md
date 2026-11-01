# Connectors — Evidence Atlas V3 (Pilar D)

Keyless public sources beyond World Bank / OpenAlex: **FRED** (US monthly /
quarterly macro), **Our World in Data** (global annual datasets incl. CO2
totals), **Wikidata** (entity facts). Plus **agentic retrieval**: the model
proposes, deterministic code validates.

Code: `src/atlas/connectors/extra.py`, `src/atlas/connectors/agentic.py`.
Tests: `tests/unit/test_extra_connectors.py`. Probe: `eval/coverage_probe.py`.

## Catalog

| Connector | Source | Key | Indicator catalog | Granularity | Semantic classes |
|---|---|---|---|---|---|
| `fred` | FRED `fredgraph.csv` | no | UNRATE, FEDFUNDS, CPIAUCSL, PAYEMS, GDP | monthly / quarterly, USA only | level, rate |
| `owid` | OWID `grapher/<slug>.csv` | no | `annual-co-emissions-by-region`, `co-emissions-per-capita`, `life-expectancy` | annual, global | level |
| `wikidata` | SPARQL `query.wikidata.org`, failover to Action API `wbgetentities` | no | P1082 population, P2046 area, P36 capital, P571 inception | per-statement | level, other |

`fred` hard-refuses non-US geos (returns `[]`, never misattributed US data).
`owid` matches the `World` aggregate by entity name (its `Code` cell is empty).
`wikidata` covers 39 ISO3→QID country mappings. SPARQL is primary; on
transport failure (e.g. the WDQS 1-req/min outage throttle observed
2026-11-01) it fails over to the Action API on `www.wikidata.org` — a
different host, so this is failover, not a retry hammer. Rows carry
`payload["via"]` (`"sparql"` / `"action-api"`). The Action API path uses
stdlib `urllib`, because `www.wikidata.org`'s bot mitigation 403s httpx's
request fingerprint while allowing urllib/curl with the identical URL and
User-Agent (observed and verified 2026-11-01).

## Provenance

Every `Evidence` carries `source_url` (canonical dataset page), `retrieved_at`
(UTC fetch time) and `payload["source_vintage"]`:

- FRED/OWID: last observation date (or year) in the fetched slice.
- Wikidata: retrieval date — it is a live KB with no release marker, so the
  vintage is explicitly a live-snapshot date.

## Rate limits

- Shared in-memory TTL cache (1 h, 32 entries) for CSV downloads —
  repeated probes do not re-download.
- One SPARQL query per `retrieve`; `User-Agent: EvidenceAtlas/0.4` on all
  requests (required etiquette for Wikidata).
- No retries: failures surface as `ProviderError`, never as empty silence
  vs. "no match" confusion (`[]` strictly means no match).

## Discarded candidates (verified 2026-11-01, with rationale)

| Candidate | Verdict | Evidence |
|---|---|---|
| FRED official JSON API | rejected | `api.stlouisfed.org … observations` without key → `400 Variable api_key is not set`. Used `fredgraph.csv` instead (verified: UNRATE/FEDFUNDS/CPIAUCSL/PAYEMS/GDP). |
| OWID v1 indicators API | rejected | guessed `api.ourworldindata.org/v1/indicators/…` → 404 (Cloudflare). Used `grapher/<slug>.csv` (verified 200). |
| OWID `co2-emissions` slug | rejected | `grapher/co2-emissions.csv` → 404 `{"status":404}`. Used `annual-co-emissions-by-region` (verified 200). |
| WHO GHO OData API | rejected | `ghoapi.azureedge.net/api/Indicator` → `500 Server Error` (repeated, both `/Indicator` and an indicator path). Unstable; no JSON to build on. |
| FAOSTAT API | rejected | `fenixservices.fao.org/…` → `521` origin-down (Cloudflare). Unreachable. |
| REST Countries v3.1 | rejected | `restcountries.com/v3.1/…` → "This API version has been deprecated … migrate to v5". Not pursued: Wikidata already covers capital/area/population facts. |

## Agentic retrieval (`agentic.py`)

Flow: `retrieve_proposal(model_output_json, claim_id)` → `Outcome(evidences | abstention)`.

1. Strict parse (`extra="forbid"`, no JSON repair) → `invalid_schema`.
2. ISO3 allowlist (+ FRED USA-only, Wikidata QID mapping) → `invalid_geo`.
3. Per-source semantic allowlist (`SEMANTIC_ALLOWLIST`, derived from the
   connectors' own catalogs so it cannot drift): `level` vs `rate` vs
   `share_gdp` vs `share` vs `other`. Wrong semantic → `semantic_mismatch`
   **before any network call** (e.g. `worldbank/NY.GDP.MKTP.CD` proposed as
   `rate`, or `wikidata/P36` proposed as `level`).
4. Real existence call → empty is `no_evidence`, transport failure is
   `provider_error`.

No model is ever called from this module: the model is an untrusted proposer
whose raw JSON string is the input. Cost $0, fully deterministic.

## Coverage probe (`eval/coverage_probe.py`)

12 claims — 6 WB-covered, 6 needing the new connectors (monthly UNRATE,
FEDFUNDS, PAYEMS, world CO2 total, Canberra capital, Brazil area). Coverage
is strict: evidence must match the claim's `(connector, indicator)` accept
rule, so annual-for-monthly or per-capita-for-total substitutions do not
count. Run: `.venv/bin/python eval/coverage_probe.py` (live network, $0).
