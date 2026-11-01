"""A2b: provider errors, query echo, iso3 allowlist, store, audit timing.

Fully offline: httpx/fetch functions are monkeypatched, never the network.
"""

import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import httpx
import pytest

import atlas.connectors.openalex as oamod
import atlas.connectors.worldbank as wbmod
import atlas.pipeline.run as runmod
from atlas.connectors import InvalidGeoError, ProviderError
from atlas.connectors.openalex import OpenAlexConnector
from atlas.connectors.worldbank import WorldBankConnector, is_allowed_iso3
from atlas.core.types import Evidence, GeoRef, Report, StructuredClaim, TimeRef
from atlas.models.scripted import ScriptedClient
from atlas.pipeline.run import APP_VERSION, analyze
from atlas.storage import store as storemod


def _claim(**kw) -> StructuredClaim:
    base = dict(raw_text="GDP grew strongly", domain="economy",
                claim_type="quantitative", subject="gdp")
    base.update(kw)
    return StructuredClaim(**base)


def _http_status_error() -> httpx.HTTPStatusError:
    # Permanent client error: 429/5xx are transient by classification rule
    # (see test_run_fixes.py), so the permanent path uses a 404.
    return httpx.HTTPStatusError(
        "404 error",
        request=httpx.Request("GET", "https://example.org"),
        response=httpx.Response(404),
    )


# --- 1. error propagation ------------------------------------------------

TRANSIENT = [httpx.ConnectError("conn refused"), httpx.ConnectTimeout("t/o"),
             httpx.ReadTimeout("t/o"), httpx.PoolTimeout("t/o")]
PERMANENT = [_http_status_error(), ValueError("bad json")]


@pytest.mark.parametrize("exc", TRANSIENT)
def test_worldbank_transient_raises_provider_error(monkeypatch, exc):
    monkeypatch.setattr(wbmod, "fetch_observations", lambda *a, **k: (_ for _ in ()).throw(exc))
    claim = _claim(geo_refs=[GeoRef(raw="France", name="france", iso3="FRA")])
    with pytest.raises(ProviderError) as ei:
        WorldBankConnector().retrieve(claim)
    assert ei.value.transient is True


@pytest.mark.parametrize("exc", PERMANENT)
def test_worldbank_permanent_raises_provider_error(monkeypatch, exc):
    monkeypatch.setattr(wbmod, "fetch_observations", lambda *a, **k: (_ for _ in ()).throw(exc))
    claim = _claim(geo_refs=[GeoRef(raw="France", name="france", iso3="FRA")])
    with pytest.raises(ProviderError) as ei:
        WorldBankConnector().retrieve(claim)
    assert ei.value.transient is False


@pytest.mark.parametrize("exc", TRANSIENT)
def test_openalex_transient_raises_provider_error(monkeypatch, exc):
    monkeypatch.setattr(oamod, "search_works", lambda *a, **k: (_ for _ in ()).throw(exc))
    with pytest.raises(ProviderError) as ei:
        OpenAlexConnector().retrieve(_claim())
    assert ei.value.transient is True


@pytest.mark.parametrize("exc", PERMANENT)
def test_openalex_permanent_raises_provider_error(monkeypatch, exc):
    monkeypatch.setattr(oamod, "search_works", lambda *a, **k: (_ for _ in ()).throw(exc))
    with pytest.raises(ProviderError) as ei:
        OpenAlexConnector().retrieve(_claim())
    assert ei.value.transient is False


def test_fetch_helpers_raise_provider_error_directly(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx.Client, "get", boom)
    with pytest.raises(ProviderError) as ei:
        wbmod.fetch_observations("NY.GDP.MKTP.CD", "USA", 2020, 2023)
    assert ei.value.transient is True
    with pytest.raises(ProviderError):
        oamod.search_works("climate")


def test_no_match_still_returns_empty_list(monkeypatch):
    def guarded(*a, **k):
        raise AssertionError("no HTTP for no-match claims")

    monkeypatch.setattr(wbmod, "fetch_observations", guarded)
    # no indicator match
    assert WorldBankConnector().retrieve(_claim(raw_text="cats are lovely",
                                                subject="cats")) == []
    # indicator match but no geo anywhere
    assert WorldBankConnector().retrieve(_claim()) == []


# --- 2. query echo ----------------------------------------------------------

def test_worldbank_query_echo(monkeypatch):
    seen = {}

    def fake_fetch(indicator, iso3, start_year, end_year):
        seen.update(indicator=indicator, iso3=iso3,
                    start_year=start_year, end_year=end_year)
        return [{"value": 123.0, "date": "2021",
                 "country": {"value": "France"}}]

    monkeypatch.setattr(wbmod, "fetch_observations", fake_fetch)
    claim = _claim(raw_text="GDP of France was high in 2021",
                   time_refs=[TimeRef(raw="2021", year=2021,
                                       period_type="year")])
    conn = WorldBankConnector()
    found = conn.retrieve(claim)
    assert len(found) == 1
    expected = f"{seen['indicator']} {seen['iso3']} {seen['start_year']}:{seen['end_year']}"
    assert found[0].payload["query"] == expected
    assert conn.last_query == expected


def test_openalex_query_echo_truncated(monkeypatch):
    seen = {}

    def fake_search(query, per_page=5):
        seen["query"] = query
        return []

    monkeypatch.setattr(oamod, "search_works", fake_search)
    long_subject = "x" * 500
    conn = OpenAlexConnector()
    assert conn.retrieve(_claim(subject=long_subject)) == []
    assert seen["query"] == "x" * 300
    assert conn.last_query == "x" * 300


def test_openalex_query_echo_in_payload(monkeypatch):
    monkeypatch.setattr(oamod, "search_works", lambda query, per_page=5: [
        {"id": "W1", "title": "T", "publication_year": 2020, "doi": "",
         "cited_by_count": 0, "authorships": [], "primary_location": {},
         "type": "article"},
    ])
    found = OpenAlexConnector().retrieve(_claim(subject="climate"))
    assert found[0].payload["query"] == "climate"


# --- 3. run.py: audit on failure + uncertainty mapping ----------------------

class _Failing:
    name = "worldbank"
    last_query = "NY.GDP.MKTP.CD USA 2020:2023"

    def __init__(self, exc):
        self._exc = exc

    def retrieve(self, claim, limit=5):
        raise self._exc


def _analyze_with(monkeypatch, connectors):
    monkeypatch.setattr(runmod, "for_domain", lambda domain: connectors)
    monkeypatch.setattr(runmod, "RETRY_DELAY_S", 0)  # no real sleeps in tests
    return analyze("GDP grew strongly", persist=False)


def test_audit_written_on_transient_failure(monkeypatch):
    report = _analyze_with(monkeypatch, [_Failing(ProviderError("down", transient=True))])
    assert any(u.code == "connector_error" for u in report.uncertainties)
    steps = [a for a in report.audit if a.step == "retrieve"]
    assert len(steps) == 1
    assert "status=" in steps[0].detail
    assert "worldbank" in steps[0].detail


def test_audit_written_on_permanent_failure(monkeypatch):
    report = _analyze_with(monkeypatch, [_Failing(ProviderError("gone", transient=False))])
    assert any(u.code == "source_unavailable" for u in report.uncertainties)
    steps = [a for a in report.audit if a.step == "retrieve"]
    assert len(steps) == 1
    assert "status=" in steps[0].detail


def test_exception_text_truncated_to_200_chars(monkeypatch):
    report = _analyze_with(monkeypatch, [_Failing(ProviderError("z" * 500, transient=True))])
    descs = [u.description for u in report.uncertainties if u.code == "connector_error"]
    assert len(descs) == 1
    assert "z" * 201 not in descs[0]
    assert "z" * 200 in descs[0]


def test_retrieve_audit_detail_has_query_and_count(monkeypatch):
    class _Stub:
        name = "worldbank"
        last_query = "NY.GDP.MKTP.CD USA 2023:2023"

        def retrieve(self, claim, limit=5):
            return [Evidence(claim_id=claim.claim_id, connector=self.name,
                             source_name="s", source_kind="secondary",
                             evidence_kind="direct",
                             payload={"query": self.last_query, "value": 1.0})]

    report = _analyze_with(monkeypatch, [_Stub()])
    detail = next(a.detail for a in report.audit if a.step == "retrieve")
    assert "NY.GDP.MKTP.CD USA 2023:2023" in detail
    assert "count=1" in detail


# --- 4. iso3 allowlist ------------------------------------------------------

HOSTILE_ISO3 = ["XX", "US", "USA1", "usa", "Usa", "U S A", "", "ZZZ",
                "'; DROP TABLE--", "WLD1", "EU", "FRA ", " USA", "12",
                "ABCD", "usA", None, 123]


@pytest.mark.parametrize("iso3", HOSTILE_ISO3)
def test_iso3_hostile_rejected_zero_http(monkeypatch, iso3):
    def guarded(*a, **k):
        raise AssertionError("allowlist bypass: HTTP attempted")

    monkeypatch.setattr(wbmod, "fetch_observations", guarded)
    monkeypatch.setattr(httpx.Client, "get", guarded)
    assert is_allowed_iso3(iso3) is False
    if not isinstance(iso3, str):
        # non-strings cannot even be modeled as GeoRef; allowlist rejects them
        claim = _claim(geo_refs=[GeoRef(raw="x", iso3=None)])
    else:
        claim = _claim(geo_refs=[GeoRef(raw="x", iso3=iso3)])
    if isinstance(iso3, str):
        with pytest.raises(InvalidGeoError):
            WorldBankConnector().retrieve(claim)
    else:
        # absent/unmodelable geo keeps the legacy "no match -> []" behavior
        assert WorldBankConnector().retrieve(claim) == []


@pytest.mark.parametrize("iso3", ["USA", "FRA", "CHN", "WLD", "EUU", "GBR"])
def test_iso3_allowlist_accepts_known(monkeypatch, iso3):
    monkeypatch.setattr(wbmod, "fetch_observations", lambda *a, **k: [])
    assert is_allowed_iso3(iso3) is True
    claim = _claim(geo_refs=[GeoRef(raw="x", iso3=iso3)])
    assert WorldBankConnector().retrieve(claim) == []


def test_invalid_geo_uncertainty_end_to_end(monkeypatch):
    def guarded(*a, **k):
        raise AssertionError("allowlist bypass: HTTP attempted")

    monkeypatch.setattr(wbmod, "fetch_observations", guarded)
    monkeypatch.setattr(httpx.Client, "get", guarded)
    llm = ScriptedClient({"Claim:": {
        "domain": "economy", "claim_type": "quantitative",
        "subject": "gdp of xanadu", "indicator_hint": "gdp",
        "entities": [], "quantities": [],
        "time_refs": [], "conditions": [], "context_notes": "",
        "geo_refs": [{"raw": "Xanadu", "name": "Xanadu", "iso3": "XX"}],
    }}, default={})
    report = analyze("GDP grew strongly", llm=llm, persist=False)
    assert any(u.code == "invalid_geo" and u.severity == "major"
               for u in report.uncertainties)
    detail = next(a.detail for a in report.audit if a.step == "retrieve")
    assert "status=invalid_geo" in detail


# --- 5. store ----------------------------------------------------------------

def test_reads_do_not_create_db(tmp_path):
    db = tmp_path / "newdir" / "atlas.db"
    assert storemod.get_report("rep_x", path=db) is None
    assert storemod.list_reports(path=db) == []
    assert storemod.delete_report("rep_x", path=db) is False
    assert not db.exists()
    assert not (tmp_path / "newdir").exists()


def test_delete_report_roundtrip(tmp_path):
    db = tmp_path / "atlas.db"
    report = Report(claim=_claim())
    storemod.save_report(report, path=db)
    assert storemod.get_report(report.report_id, path=db) is not None
    assert storemod.delete_report(report.report_id, path=db) is True
    assert storemod.get_report(report.report_id, path=db) is None
    assert storemod.delete_report(report.report_id, path=db) is False
    assert storemod.list_reports(path=db) == []


# --- 6. timing + meta --------------------------------------------------------

def test_duration_ms_positive_on_a_stage(monkeypatch):
    class _Slow:
        name = "worldbank"
        last_query = None

        def retrieve(self, claim, limit=5):
            time.sleep(0.01)
            return []

    report = _analyze_with(monkeypatch, [_Slow()])
    durations = [getattr(a, "duration_ms", None) for a in report.audit]
    assert all(isinstance(d, float) and d >= 0 for d in durations)
    assert any(d > 0 for d in durations)
    assert [a.step for a in report.audit] == ["extract", "route", "retrieve",
                                              "verify", "falsify",
                                              "synthesize"]


# --- 7. M3: abstract retrieval (additive) -------------------------------------

def test_reconstruct_abstract_basic():
    work = {"abstract_inverted_index": {"Cats": [0], "are": [1], "lovely": [2]}}
    assert oamod.reconstruct_abstract(work) == "Cats are lovely"


def test_reconstruct_abstract_inverts_positions():
    work = {"abstract_inverted_index": {"b": [2, 4], "a": [0], "c": [1, 3]}}
    assert oamod.reconstruct_abstract(work) == "a c b c b"


def test_reconstruct_abstract_missing_or_invalid():
    assert oamod.reconstruct_abstract({}) is None
    assert oamod.reconstruct_abstract({"abstract_inverted_index": None}) is None
    assert oamod.reconstruct_abstract({"abstract_inverted_index": "text"}) is None
    assert oamod.reconstruct_abstract({"abstract_inverted_index": {}}) is None
    assert oamod.reconstruct_abstract(
        {"abstract_inverted_index": {"x": ["0", -1, True, None]}}) is None


def test_reconstruct_abstract_caps_length():
    inv = {f"w{i}": [i] for i in range(1000)}
    text = oamod.reconstruct_abstract({"abstract_inverted_index": inv})
    assert text is not None
    assert len(text) <= oamod.ABSTRACT_MAX_CHARS == 2000


def test_search_works_selects_abstract(monkeypatch):
    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": []}

    def fake_get(self, url, params=None, **kw):
        seen["params"] = params
        return _Resp()

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    assert oamod.search_works("climate") == []
    assert "abstract_inverted_index" in seen["params"]["select"]


def test_retrieve_stores_abstract_in_payload(monkeypatch):
    monkeypatch.setattr(oamod, "search_works", lambda query, per_page=5: [
        {"id": "W1", "title": "T", "publication_year": 2020, "doi": "",
         "cited_by_count": 0, "authorships": [], "primary_location": {},
         "type": "article",
         "abstract_inverted_index": {"Some": [0], "finding": [1]}},
        {"id": "W2", "title": "U", "publication_year": 2021, "doi": "",
         "cited_by_count": 0, "authorships": [], "primary_location": {},
         "type": "article"},
    ])
    found = OpenAlexConnector().retrieve(_claim(subject="climate"))
    assert found[0].payload["abstract"] == "Some finding"
    assert found[1].payload["abstract"] is None


def test_meta_and_elapsed_ms(monkeypatch):
    monkeypatch.setattr(runmod, "for_domain", lambda domain: [])
    text = "GDP grew strongly"
    report = analyze(text, persist=False)
    assert APP_VERSION == "0.3.0"
    meta = report.meta
    assert meta["app_version"] == "0.3.0"
    for key in ("model", "temperature", "max_tokens", "tolerance",
                "default", "prompt_version", "input_sha256",
                "usage_extract", "usage_science", "usage_falsify",
                "usage_summary"):
        assert key in meta
    # Degraded run: no stage made a model call, so every usage is None.
    assert meta["usage_extract"] is None
    assert meta["usage_science"] is None
    assert meta["usage_falsify"] is None
    assert meta["usage_summary"] is None
    assert meta["input_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert meta["tolerance"] == 0.02
    assert isinstance(report.elapsed_ms, float) and report.elapsed_ms > 0
