"""Unit tests for the extra keyless connectors and agentic retrieval.

All tests are offline except the single ``live``-marked probe (excluded
from the gate). Network is stubbed by monkeypatching the ``extra._get_text``
/ ``extra._get_json`` seam with recorded payload shapes.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.connectors import agentic, extra  # noqa: E402
from atlas.core.types import StructuredClaim  # noqa: E402

FRED_UNRATE_CSV = """observation_date,UNRATE
2024-01-01,3.7
2024-02-01,3.9
2024-03-01,.
"""

OWID_CO2_CSV = """Entity,Code,Year,Annual CO₂ emissions
United States,USA,2021,5001000000
United States,USA,2022,5035000000
World,,2022,37786000000
"""

WD_POP_JSON = {
    "head": {"vars": ["value", "time"]},
    "results": {
        "bindings": [
            {
                "value": {"type": "literal", "value": "340110988"},
                "time": {"type": "literal", "value": "2024-07-01T00:00:00Z"},
            },
            {
                "value": {"type": "literal", "value": "332278200"},
                "time": {"type": "literal", "value": "2021-01-01T00:00:00Z"},
            },
        ]
    },
}

WD_CAPITAL_JSON = {
    "head": {"vars": ["valueLabel"]},
    "results": {
        "bindings": [{"valueLabel": {"type": "literal", "value": "Canberra"}}]
    },
}


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    extra.clear_cache()
    yield
    extra.clear_cache()


def _claim(text, **kw):
    return StructuredClaim(raw_text=text, **kw)


# ---------------------------------------------------------------- FRED ---


def test_fred_fetch_parses_csv_and_skips_missing(monkeypatch):
    monkeypatch.setattr(extra, "_get_text", lambda *a, **k: FRED_UNRATE_CSV)
    rows = extra.fetch_fred("UNRATE")
    assert rows == [
        {"date": "2024-01-01", "value": 3.7},
        {"date": "2024-02-01", "value": 3.9},
    ]


def test_fred_evidence_carries_provenance(monkeypatch):
    monkeypatch.setattr(extra, "_get_text", lambda *a, **k: FRED_UNRATE_CSV)
    evs = extra.fetch_fred_evidence("clm_1", "UNRATE", year=2024)
    assert len(evs) == 2
    ev = evs[0]
    assert ev.connector == "fred"
    assert ev.source_url == "https://fred.stlouisfed.org/series/UNRATE"
    assert ev.retrieved_at is not None
    assert ev.payload["source_vintage"] == "2024-02-01"
    assert ev.payload["semantic"] == "rate"
    assert ev.payload["iso3"] == "USA"


def test_fred_month_filter(monkeypatch):
    monkeypatch.setattr(extra, "_get_text", lambda *a, **k: FRED_UNRATE_CSV)
    evs = extra.fetch_fred_evidence("clm_1", "UNRATE", year=2024, month=1)
    assert [e.payload["date"] for e in evs] == ["2024-01-01"]


def test_fred_retrieve_requires_us_geo(monkeypatch):
    monkeypatch.setattr(extra, "_get_text", lambda *a, **k: FRED_UNRATE_CSV)
    conn = extra.FredConnector()
    ok = conn.retrieve(_claim("The US unemployment rate was 3.7% in 2024."))
    assert len(ok) == 2
    # Non-US geo must never receive US-only data.
    assert conn.retrieve(_claim("Germany unemployment rate was 3% in 2024.")) == []
    assert conn.retrieve(_claim("The GDP of France was high.")) == []


def test_fred_unknown_series_returns_empty():
    assert extra.fetch_fred_evidence("clm_1", "NOPE", year=2024) == []


# ---------------------------------------------------------------- OWID ---


def test_owid_fetch_filters_iso3_and_year(monkeypatch):
    monkeypatch.setattr(extra, "_get_text", lambda *a, **k: OWID_CO2_CSV)
    rows = extra.fetch_owid("annual-co-emissions-by-region", "USA", year=2022)
    assert rows == [{"entity": "United States", "year": 2022, "value": 5035000000.0}]


def test_owid_world_aggregate_matches_empty_code(monkeypatch):
    monkeypatch.setattr(extra, "_get_text", lambda *a, **k: OWID_CO2_CSV)
    rows = extra.fetch_owid("annual-co-emissions-by-region", "WLD", year=2022)
    assert rows == [{"entity": "World", "year": 2022, "value": 37786000000.0}]


def test_owid_evidence_carries_provenance(monkeypatch):
    monkeypatch.setattr(extra, "_get_text", lambda *a, **k: OWID_CO2_CSV)
    evs = extra.fetch_owid_evidence(
        "clm_1", "annual-co-emissions-by-region", "WLD", year=2022
    )
    assert len(evs) == 1
    ev = evs[0]
    assert ev.connector == "owid"
    assert ev.source_url.endswith("/grapher/annual-co-emissions-by-region")
    assert ev.payload["source_vintage"] == "2022"
    assert ev.payload["semantic"] == "level"


def test_owid_longest_keyword_wins(monkeypatch):
    seen = {}

    def fake_fetch(claim_id, slug, iso3, limit=5, year=None):
        seen["slug"] = slug
        return []

    monkeypatch.setattr(extra, "fetch_owid_evidence", fake_fetch)
    extra.OwidConnector().retrieve(
        _claim("US co2 per capita was 14 tonnes in 2022.")
    )
    assert seen["slug"] == "co-emissions-per-capita"


# ------------------------------------------------------------- Wikidata ---


def test_wikidata_fetch_parses_bindings(monkeypatch):
    monkeypatch.setattr(extra, "_get_json", lambda *a, **k: WD_POP_JSON)
    rows = extra.fetch_wikidata("Q30", "P1082")
    assert rows[0] == {
        "value": "340110988", "time": "2024-07-01", "_via": "sparql",
    }
    assert len(rows) == 2


def test_wikidata_evidence_year_prefers_dated_row(monkeypatch):
    monkeypatch.setattr(extra, "_get_json", lambda *a, **k: WD_POP_JSON)
    evs = extra.fetch_wikidata_evidence("clm_1", "USA", "P1082", year=2021)
    assert len(evs) == 1
    assert evs[0].payload["value"] == "332278200"
    assert evs[0].source_url == "https://www.wikidata.org/wiki/Q30#P1082"
    assert evs[0].payload["source_vintage"]  # live-snapshot date


def test_wikidata_capital_evidence(monkeypatch):
    monkeypatch.setattr(extra, "_get_json", lambda *a, **k: WD_CAPITAL_JSON)
    evs = extra.fetch_wikidata_evidence("clm_1", "AUS", "P36")
    assert len(evs) == 1
    assert "Canberra" in evs[0].excerpt
    assert evs[0].payload["semantic"] == "other"


def test_wikidata_retrieve_unknown_country_returns_empty(monkeypatch):
    conn = extra.WikidataConnector()
    # WLD has no country QID mapping.
    monkeypatch.setattr(
        extra, "_get_json", lambda *a, **k: {"results": {"bindings": []}}
    )
    assert conn.retrieve(_claim("World population was 8 billion.")) == []
    monkeypatch.setattr(extra, "_get_json", lambda *a, **k: WD_CAPITAL_JSON)
    assert (
        conn.retrieve(_claim("The capital of Australia is Canberra."))
        != []
    )


WD_ACTION_AUS = {
    "entities": {
        "Q408": {
            "claims": {
                "P36": [
                    {
                        "mainsnak": {
                            "snaktype": "value",
                            "datavalue": {
                                "value": {
                                    "entity-type": "item",
                                    "numeric-id": 3114,
                                    "id": "Q3114",
                                }
                            },
                        }
                    }
                ],
                "P1082": [
                    {
                        "mainsnak": {
                            "snaktype": "value",
                            "datavalue": {
                                "value": {
                                    "amount": "+26000000",
                                    "unit": "1",
                                }
                            },
                        },
                        "qualifiers": {
                            "P585": [
                                {
                                    "datavalue": {
                                        "value": {
                                            "time": "+2023-06-01T00:00:00Z"
                                        }
                                    }
                                }
                            ]
                        },
                    }
                ],
            }
        }
    }
}

WD_ACTION_LABEL = {
    "entities": {"Q3114": {"labels": {"en": {"value": "Canberra"}}}}
}


def _sparql_down_then_action(monkeypatch):
    from atlas.connectors import ProviderError

    def fake_json(url, params=None, headers=None):
        raise ProviderError("sparql down", transient=True)

    def fake_text(url, params=None, headers=None):
        ids = (params or {}).get("ids", "")
        if ids == "Q3114":
            return json.dumps(WD_ACTION_LABEL)
        return json.dumps(WD_ACTION_AUS)

    monkeypatch.setattr(extra, "_get_json", fake_json)
    monkeypatch.setattr(extra, "_get_text_urllib", fake_text)


def test_wikidata_falls_back_to_action_api_on_sparql_failure(monkeypatch):
    _sparql_down_then_action(monkeypatch)
    rows = extra.fetch_wikidata("Q408", "P1082")
    assert rows == [
        {"_via": "action-api", "value": "26000000", "time": "2023-06-01"}
    ]


def test_wikidata_fallback_resolves_capital_label(monkeypatch):
    _sparql_down_then_action(monkeypatch)
    evs = extra.fetch_wikidata_evidence("clm_1", "AUS", "P36")
    assert len(evs) == 1
    assert "Canberra" in evs[0].excerpt
    assert evs[0].payload["via"] == "action-api"


# -------------------------------------------------------------- Agentic ---

GOOD_FRED = json.dumps(
    {
        "source": "fred",
        "indicator": "UNRATE",
        "iso3": "USA",
        "year": 2024,
        "expected_semantic": "rate",
    }
)


def test_parse_rejects_malformed_and_extra_fields():
    out = agentic.retrieve_proposal("not json", "clm_1")
    assert out.abstention is not None and out.abstention.code == "invalid_schema"
    out = agentic.retrieve_proposal('["a", "list"]', "clm_1")
    assert out.abstention is not None and out.abstention.code == "invalid_schema"
    with_extra = json.dumps({**json.loads(GOOD_FRED), "sneaky": 1})
    out = agentic.retrieve_proposal(with_extra, "clm_1")
    assert out.abstention is not None and out.abstention.code == "invalid_schema"


def test_semantic_mismatch_abstains_without_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no network on semantic mismatch")

    monkeypatch.setattr(extra, "_get_text", boom)
    bad = json.dumps(
        {
            "source": "worldbank",
            "indicator": "NY.GDP.MKTP.CD",  # a level, proposed as a rate
            "iso3": "USA",
            "year": 2023,
            "expected_semantic": "rate",
        }
    )
    out = agentic.retrieve_proposal(bad, "clm_1")
    assert out.abstention is not None
    assert out.abstention.code == "semantic_mismatch"
    assert out.requests_made == 0


def test_unknown_indicator_and_geo_abstain():
    bad_ind = json.dumps(
        {
            "source": "fred",
            "indicator": "NOPE",
            "iso3": "USA",
            "year": 2024,
            "expected_semantic": "level",
        }
    )
    out = agentic.retrieve_proposal(bad_ind, "clm_1")
    assert out.abstention is not None and out.abstention.code == "unknown_indicator"
    bad_geo = json.dumps(
        {
            "source": "fred",
            "indicator": "UNRATE",
            "iso3": "XXX",
            "year": 2024,
            "expected_semantic": "rate",
        }
    )
    out = agentic.retrieve_proposal(bad_geo, "clm_1")
    assert out.abstention is not None and out.abstention.code == "invalid_geo"


def test_fred_non_usa_abstains_invalid_geo():
    prop = json.dumps(
        {
            "source": "fred",
            "indicator": "UNRATE",
            "iso3": "DEU",
            "year": 2024,
            "expected_semantic": "rate",
        }
    )
    out = agentic.retrieve_proposal(prop, "clm_1")
    assert out.abstention is not None and out.abstention.code == "invalid_geo"


def test_valid_proposal_fetches_recorded_evidence(monkeypatch):
    monkeypatch.setattr(extra, "_get_text", lambda *a, **k: FRED_UNRATE_CSV)
    out = agentic.retrieve_proposal(GOOD_FRED, "clm_1")
    assert out.abstention is None
    assert len(out.evidences) == 2
    assert out.requests_made == 1


def test_no_evidence_abstains_on_empty_source(monkeypatch):
    monkeypatch.setattr(
        extra, "_get_text", lambda *a, **k: "observation_date,UNRATE\n"
    )
    out = agentic.retrieve_proposal(GOOD_FRED, "clm_1")
    assert out.abstention is not None and out.abstention.code == "no_evidence"


def test_allowlist_covers_all_connector_catalogs():
    assert set(agentic.SEMANTIC_ALLOWLIST) == {
        "worldbank",
        "fred",
        "owid",
        "wikidata",
    }
    assert agentic.SEMANTIC_ALLOWLIST["fred"]["UNRATE"] == "rate"
    assert agentic.SEMANTIC_ALLOWLIST["fred"]["GDP"] == "level"
    assert agentic.SEMANTIC_ALLOWLIST["worldbank"]["NY.GDP.MKTP.KD.ZG"] == "rate"
    assert agentic.SEMANTIC_ALLOWLIST["worldbank"]["NY.GDP.MKTP.CD"] == "level"
    assert agentic.SEMANTIC_ALLOWLIST["worldbank"]["GC.DOD.TOTL.GD.ZS"] == "share_gdp"
    assert agentic.SEMANTIC_ALLOWLIST["wikidata"]["P36"] == "other"


# ------------------------------------------------------------------ Live ---


@pytest.mark.live
def test_live_fred_unrate_jan_2024():
    rows = extra.fetch_fred("UNRATE", start="2024-01-01", end="2024-01-31")
    assert len(rows) == 1
    assert rows[0]["value"] == pytest.approx(3.7, abs=0.05)
