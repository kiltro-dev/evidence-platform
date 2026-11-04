"""V7 FRED leg: official api.stlouisfed.org, key-gated, offline doubles.

All tests except the ATLAS_LIVE=1 pin run on fakes (zero packets,
zero key needed). The live pin needs FRED_API_KEY + net and asserts
the key is never persisted in urls/snapshots.
"""

from __future__ import annotations

import hashlib
import os

import pytest

from live.swarm import fred as F
from live.swarm.altroutes import attempt_altroutes
from live.swarm.coordinator import SwarmConfig, _DocStore, _Tools
from live.swarm.fetch import FetchResult

FEDFUNDS_BODY = (
    '{"realtime_start":"2026-11-04","realtime_end":"2026-11-04",'
    '"observation_start":"2024-01-01","observation_end":"2025-01-01",'
    '"units":"lin","output":1,"file_type":"json","order_by":"date",'
    '"sort_order":"asc","count":2,"offset":0,"limit":100000,'
    '"observations":[{"realtime_start":"2026-11-04",'
    '"realtime_end":"2026-11-04","date":"2024-01-01","value":"5.33"},'
    '{"realtime_start":"2026-11-04","realtime_end":"2026-11-04",'
    '"date":"2024-02-01","value":"5.33"}]}')

CLAIM = "The federal funds effective rate was 5.33 percent in January 2024."


def _fetch(url, body=FEDFUNDS_BODY):
    raw = body.encode("utf-8")
    return FetchResult(
        url=url, final_url=url, status=200,
        content_type="application/json", size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        snapshot_path="/tmp/x.json",
        captured_at="2026-11-04T00:00:00+00:00", text=body)


def test_match_series_en_es():
    assert F.match_fred_series(CLAIM) == ["FEDFUNDS"]
    assert F.match_fred_series("La tasa de fondos federales era 5,33%") == [
        "FEDFUNDS"]
    assert F.match_fred_series("US CPI rose in 2024") == ["CPIAUCSL"]
    assert F.match_fred_series("El IPC subió en 2024") == ["CPIAUCSL"]
    assert F.match_fred_series("unemployment rate 4%") == ["UNRATE"]
    assert F.match_fred_series("real GDP grew") == ["GDPC1"]
    # vague concepts must NOT match (PCE vs CPI, nominal vs real)
    assert F.match_fred_series("inflation was high in 2024") == []
    assert F.match_fred_series("GDP grew in 2024") == []
    assert F.match_fred_series("") == []


def test_url_year_bounded_and_latest():
    url = F.fred_url("FEDFUNDS", "KEY", 2024)
    assert "series_id=FEDFUNDS" in url
    assert "observation_start=2024-01-01" in url
    assert "observation_end=2025-01-01" in url
    latest = F.fred_url("UNRATE", "KEY", None)
    assert "sort_order=desc" in latest and "limit=4" in latest


def test_parse_pick_quote_pure():
    obs = F.parse_observations(FEDFUNDS_BODY)
    assert [o["value"] for o in obs] == ["5.33", "5.33"]
    assert F.parse_observations("not json") == []
    assert F.parse_observations('{"observations":[{"date":"2024-01-01",'
                               '"value":"."}]}') == []
    picked = F.pick_observation(obs, 2024, 1)
    assert picked == {"date": "2024-01-01", "value": "5.33"}
    assert F.pick_observation(obs, 1999, None) is None
    assert F.pick_observation([], 2024, 1) is None
    quote = F.fred_quote_for(FEDFUNDS_BODY, picked)
    assert quote == '"value":"5.33"'  # value-only: no date fragments
    assert quote in FEDFUNDS_BODY  # verbatim span
    assert F.fred_quote_for(FEDFUNDS_BODY, {"date": "x", "value": "y"}) is None
    assert F.fred_vintage(picked) == "2024-01"
    assert F.fred_vintage({}) is None
    assert F.fred_source_meta("fedfunds") == {
        "indicator": "FEDFUNDS", "unit": "percent", "scale": 1.0}
    assert F.fred_source_meta("nope") is None


def test_no_key_zero_packets(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    calls = []

    def _get(url, **kw):
        calls.append(url)
        raise AssertionError("must not fetch without key")

    out = attempt_altroutes(CLAIM, lambda *a, **k: None, get=_get)
    assert calls == []
    assert out.calls_made == 0
    assert out.candidates == []
    assert {"route": "fred-api/FEDFUNDS", "action": "skipped",
            "reason": "FRED_API_KEY unset"} in out.routes_tried


def test_no_seam_zero_packets(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "K")
    out = attempt_altroutes(CLAIM, lambda *a, **k: None)
    assert out.calls_made == 0
    assert {"route": "fred-api/FEDFUNDS", "action": "skipped",
            "reason": "no GET seam"} in out.routes_tried


def test_candidate_key_never_persisted(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "SECRETKEY")
    seen = {}

    def _get(url, snapshot_dir=None, use_cache=True):
        seen["url"] = url
        seen["use_cache"] = use_cache
        return _fetch(url)

    out = attempt_altroutes(CLAIM, lambda *a, **k: None, get=_get)
    assert seen["use_cache"] is False  # bypass: keyed URL never cached
    assert out.calls_made == 1
    assert len(out.candidates) == 1
    cand = out.candidates[0]
    assert cand.quote == '"value":"5.33"'
    assert cand.source_year == 2024
    assert cand.vintage == "2024-01"
    assert cand.source_meta["indicator"] == "FEDFUNDS"
    # key scrubbed everywhere it could persist
    assert "SECRETKEY" not in cand.url
    assert "SECRETKEY" not in cand.fetch_result.url
    assert "SECRETKEY" not in cand.fetch_result.final_url
    assert F.REDACTED in cand.fetch_result.url


def test_transport_failure_recorded_not_raised(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "K")

    def _boom(url, **kw):
        raise ConnectionError("down")

    out = attempt_altroutes(CLAIM, lambda *a, **k: None, get=_boom)
    assert out.candidates == []
    assert out.calls_made == 1
    assert {"route": "fred-api/FEDFUNDS", "action": "refused",
            "reason": "ConnectionError"} in out.routes_tried


def test_budget_shared_with_bls(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "K")
    # matches BLS [LNS14000000] (rate cue, no level cue) + FRED [UNRATE]
    text = "US unemployment rate was 4 percent in 2024."
    got = []

    def _get(url, **kw):
        got.append(url)
        return _fetch(url, '{"observations":[]}')

    def _post(url, payload, snapshot_dir=None):
        return _fetch(url, '{"Results":{"series":[]}}')

    out = attempt_altroutes(text, _post, get=_get, budget=1)
    assert out.calls_made == 1  # BLS spent it; FRED skipped
    assert got == []
    assert any(r["route"].startswith("fred-api/")
               and r["action"] == "skipped" for r in out.routes_tried)


def test_non_blike_claim_zero_packets(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "K")
    out = attempt_altroutes("The moon is made of cheese.",
                            lambda *a, **k: None,
                            get=lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("no fetch")))
    assert out.calls_made == 0 and out.candidates == []


def test_alt_lookup_wires_get_seam(monkeypatch, tmp_path):
    """Coordinator passes its GET seam through; key stays scrubbed."""
    monkeypatch.setenv("FRED_API_KEY", "SECRETKEY")
    seen = {}

    def _get(url, snapshot_dir=None, use_cache=True):
        seen["use_cache"] = use_cache
        return _fetch(url)

    config = SwarmConfig(post_fn=lambda *a, **k: None,
                         fetch_fn=lambda url, **kw: _get(url, **kw),
                         snapshot_dir=tmp_path)
    res = _Tools(config, _DocStore()).alt_lookup(CLAIM)
    assert seen["use_cache"] is False
    assert res["ok"] is True and res["calls"] == 1
    assert len(res["candidates"]) == 1
    assert res["candidates"][0]["quote"] == (
        '"value":"5.33"')
    assert "SECRETKEY" not in res["candidates"][0]["url"]


def test_rederivation_fred_branch():
    from live.evidence.package import source_meta_for_snapshot
    url = ("https://api.stlouisfed.org/fred/series/observations"
           "?series_id=UNRATE&api_key=key-redacted&file_type=json")
    meta = source_meta_for_snapshot(url, FEDFUNDS_BODY)
    # Body shape is generic FRED observations; the series id comes
    # from the code-built request URL (redacted key still parses).
    assert meta is not None and meta.indicator == "UNRATE"
    assert meta.unit == "percent" and meta.scale == 1.0
    assert source_meta_for_snapshot(
        url.replace("UNRATE", "NOPE"), FEDFUNDS_BODY) is None
    assert source_meta_for_snapshot(url, "not json") is None
    assert source_meta_for_snapshot(
        url, '{"observations":[]}') is None
    assert source_meta_for_snapshot(
        "https://evil.example/x?series_id=UNRATE",
        FEDFUNDS_BODY) is None
    # WB branch untouched
    assert source_meta_for_snapshot(
        "https://api.worldbank.org/v2/x", "not a list") is None


def test_quote_has_single_numeric_candidate():
    from src.atlas.core.compare import is_year_quantity
    from src.atlas.core.units import parse_quantities
    quote = F.fred_quote_for(
        FEDFUNDS_BODY, {"date": "2024-01-01", "value": "5.33"})
    cands = [q for q in parse_quantities(quote, label="quote")
             if not is_year_quantity(q)]
    assert [(q.value, q.normalized_unit) for q in cands] == [(5.33, None)]


@pytest.mark.live
def test_live_fred_pin():
    if os.environ.get("ATLAS_LIVE") != "1":
        pytest.skip("live test: set ATLAS_LIVE=1 to run")
    key = F.read_key()
    if not key:
        pytest.skip("live test: FRED_API_KEY unset")
    from live.swarm.fetch import fetch_url
    out = attempt_altroutes(CLAIM, lambda *a, **k: None, get=fetch_url)
    assert out.calls_made == 1
    assert len(out.candidates) == 1
    cand = out.candidates[0]
    assert cand.quote in (cand.fetch_result.text or "")
    assert key not in cand.url
    assert key not in (cand.fetch_result.text or "")
    assert cand.source_meta["indicator"] == "FEDFUNDS"
