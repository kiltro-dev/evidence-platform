"""M2c E2E tests: coordinator -> EvidencePackage -> store -> verifier.

All offline (FakeLLM/FakeSearch/FakeFetch doubles, 0 live network, 0 model
calls): the full claim.evidence subscription chain in live/run_verify.py is
exercised with tmp_path fixtures only, plus the aligned-contract pins
(numbers[].unit vocab, raw-bytes sha256 incl. a legacy-charset case,
primary/secondary criterion on both sides).
"""

import asyncio
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.evidence.package import (  # noqa: E402
    UNIT_VOCAB,
    EvidencePackage,
    NumberMention,
    sha256_of_bytes,
    sha256_of_text,
    source_type_for_url as canonical_source_type,
)
from live.library.store import EvidenceStore  # noqa: E402
from live.orchestrator.bus import EventBus  # noqa: E402
from live.orchestrator.states import ClaimTracker  # noqa: E402
from live.run_verify import (  # noqa: E402
    build_report,
    claim_input_for,
    load_entries,
    make_evidence_handler,
)
from live.swarm.coordinator import (  # noqa: E402
    SwarmConfig,
    numbers_from_quote,
    run_claim,
    source_type_for_url as swarm_source_type,
)
from live.swarm.fetch import FetchError, FetchResult  # noqa: E402
from live.verifier.check import verify_claim  # noqa: E402


# ---------------------------------------------------------------------------
# doubles (same seams as test_m2b_swarm.py; 0 live)
# ---------------------------------------------------------------------------

class FakeLLM:
    name = "fake-llm"
    available = True

    def __init__(self, script, prompt_tokens=100, completion_tokens=20):
        self._script = list(script)
        self.last_usage = None
        self.last_usage_seq = 0
        self._pt = prompt_tokens
        self._ct = completion_tokens

    def complete_json(self, system, user, max_tokens=1500,
                      reasoning_effort=None, json_schema=None):
        self.last_usage_seq += 1
        self.last_usage = {"prompt_tokens": self._pt,
                           "completion_tokens": self._ct}
        return self._script.pop(0)


def make_fetch_fn(bodies, snap_dir, fail_urls=()):
    """url-substring -> body text; writes real tmp snapshots (utf-8)."""

    def _fake(url, snapshot_dir=None):
        for sub in fail_urls:
            if sub in url:
                raise FetchError(f"boom {url}")
        for sub, body in bodies.items():
            if sub in url:
                raw = body.encode("utf-8")
                sha = hashlib.sha256(raw).hexdigest()
                os.makedirs(snap_dir, exist_ok=True)
                path = os.path.join(snap_dir, f"{sha}.html")
                with open(path, "wb") as fh:
                    fh.write(raw)
                return FetchResult(
                    url=url, final_url=url, status=200,
                    content_type="text/html; charset=utf-8",
                    size=len(raw), sha256=sha, snapshot_path=path,
                    captured_at="2026-11-01T00:00:00+00:00",
                    text=body,
                )
        raise FetchError(f"404 {url}")

    return _fake


def make_search_fn(hits):
    def _fake(query, top_k=3):
        return list(hits[:top_k])

    return _fake


WB_BODY = (
    '[{"page": 1, "lastupdated": "2025-09-01"}, '
    '[{"indicator": {"id": "SP.POP.TOTL"}, "country": {"id": "WLD"}, '
    '"date": "2024", "value": 8140897523, "decimal": "0"}]]'
)
WIKI_URL = "https://en.wikipedia.org/wiki/World_population"
WIKI_BODY = (
    "<html><body><p>The world population reached 8.14 billion people "
    "in 2024 according to United Nations estimates.</p></body></html>"
)
WIKI_QUOTE = ("The world population reached 8.14 billion people in 2024 "
              "according to United Nations estimates.")

CHINA_URL = "https://en.wikipedia.org/wiki/Demographics_of_China"
CHINA_BODY = ("<html><body><p>China population stood at 1.41 billion people "
              "in 2024.</p></body></html>")
CHINA_QUOTE = "China population stood at 1.41 billion people in 2024."

ESTRUCT_PLAN = {
    "entities": ["World Bank", "world population"],
    "indicator_candidate": "SP.POP.TOTL",
    "geo": {"name": None, "iso3": None},
    "year": 2024,
    "web_query": "world population 2024",
    "numbers_in_claim": ["8.14 billion"],
}
REDTEAM_SKIP = {"counter_query": "", "rationale": "narrow", "skip": True}


def _final(url, quote):
    return {"thought": "citing",
            "final": {"findings": [{"url": url, "quote": quote}]}}


def _wired_chain(tmp_path, bodies, hits):
    """Bus + tracker + store + subscribed M2c handler (tmp fixtures)."""
    bus = EventBus()
    tracker = ClaimTracker(bus)
    store = EvidenceStore(tmp_path / "lib.db", tmp_path / "snap")
    snap_dir = str(tmp_path / "fetch")
    stats: dict = {}

    def _resolve(pkg):
        path = os.path.join(snap_dir, f"{pkg.sha256}.html")
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            return store.get_snapshot_bytes(pkg.sha256)

    return bus, tracker, store, snap_dir, stats, _resolve


# ---------------------------------------------------------------------------
# full chain: swarm -> handler -> store -> verifier -> claim.verdict
# ---------------------------------------------------------------------------

def test_e2e_supported_offline(tmp_path):
    bodies = {"worldbank": WB_BODY, "wikipedia": WIKI_BODY}
    hits = [{"url": WIKI_URL, "title": "World population", "snippet": ""}]
    bus, tracker, store, snap_dir, stats, resolve = _wired_chain(
        tmp_path, bodies, hits)
    claim_id = "T1-en"
    text = "We are 8.14 billion people sharing one planet."
    claims = {claim_id: claim_input_for({"id": claim_id, "text": text})}
    bus.subscribe("claim.evidence", make_evidence_handler(
        bus, tracker, store, claims, stats, snapshot_resolver=resolve))
    llm = FakeLLM([dict(ESTRUCT_PLAN), _final(WIKI_URL, WIKI_QUOTE),
                   dict(REDTEAM_SKIP)])
    config = SwarmConfig(llm=llm, search_fn=make_search_fn(hits),
                         fetch_fn=make_fetch_fn(bodies, snap_dir))

    result = asyncio.run(run_claim(bus, tracker, claim_id, text, config))

    assert len(result.packages) == 1
    pkg = result.packages[0]
    assert pkg["source_type"] == "secondary"  # Wikipedia: not primary
    assert stats.get("source_type_mismatch", 0) == 0
    assert stats.get("invalid_packages", 0) == 0
    # stored + re-verifiable from the store
    stored = store.packages_for_claim(claim_id)
    assert len(stored) == 1 and stored[0].package_id == pkg["package_id"]
    assert store.get_snapshot_bytes(pkg["sha256"]) is not None
    # exactly one verdict, supported, tracker closed the loop
    verdicts = bus.history("claim.verdict")
    assert len(verdicts) == 1
    assert verdicts[0]["claim_id"] == claim_id
    assert verdicts[0]["verdict"] == "supported"
    assert tracker.state(claim_id) == "veredicto"
    store.close()


def test_e2e_contradicted_offline(tmp_path):
    bodies = {"worldbank": WB_BODY, "wikipedia": CHINA_BODY}
    hits = [{"url": CHINA_URL, "title": "China", "snippet": ""}]
    bus, tracker, store, snap_dir, stats, resolve = _wired_chain(
        tmp_path, bodies, hits)
    claim_id = "F3-en"
    text = "Meanwhile China's population has fallen to just 141 million people."
    claims = {claim_id: claim_input_for({"id": claim_id, "text": text})}
    bus.subscribe("claim.evidence", make_evidence_handler(
        bus, tracker, store, claims, stats, snapshot_resolver=resolve))
    llm = FakeLLM([dict(ESTRUCT_PLAN), _final(CHINA_URL, CHINA_QUOTE),
                   dict(REDTEAM_SKIP)])
    config = SwarmConfig(llm=llm, search_fn=make_search_fn(hits),
                         fetch_fn=make_fetch_fn(bodies, snap_dir))

    asyncio.run(run_claim(bus, tracker, claim_id, text, config))

    verdicts = bus.history("claim.verdict")
    assert len(verdicts) == 1
    assert verdicts[0]["verdict"] == "contradicted", verdicts[0]
    assert tracker.state(claim_id) == "veredicto"
    store.close()


def test_e2e_invented_quote_is_unverifiable(tmp_path):
    bodies = {"worldbank": WB_BODY, "wikipedia": WIKI_BODY}
    hits = [{"url": WIKI_URL, "title": "W", "snippet": ""}]
    bus, tracker, store, snap_dir, stats, resolve = _wired_chain(
        tmp_path, bodies, hits)
    claim_id = "c-bad"
    text = "We are 8.14 billion people sharing one planet."
    claims = {claim_id: claim_input_for({"id": claim_id, "text": text})}
    bus.subscribe("claim.evidence", make_evidence_handler(
        bus, tracker, store, claims, stats, snapshot_resolver=resolve))
    bad = _final(WIKI_URL, "the moon is made of cheese")
    llm = FakeLLM([dict(ESTRUCT_PLAN), bad, dict(REDTEAM_SKIP)])
    config = SwarmConfig(llm=llm, search_fn=make_search_fn(hits),
                         fetch_fn=make_fetch_fn(bodies, snap_dir))

    result = asyncio.run(run_claim(bus, tracker, claim_id, text, config))

    assert result.packages == []
    verdicts = bus.history("claim.verdict")
    assert len(verdicts) == 1
    assert verdicts[0]["verdict"] == "unverifiable"
    assert "no_usable_package" in verdicts[0]["reasons"]
    assert tracker.state(claim_id) == "veredicto"
    store.close()


def test_wb_json_quote_is_unit_mismatch_unverifiable():
    """Pins the REAL contract: unitless WB JSON quotes cannot decide.

    The verifier re-extracts numbers from the quote; a World Bank API row
    carries no unit marker, so the package is unusable (unit_mismatch)
    and the claim stays unverifiable unless a unit-carrying quote
    (e.g. Wikipedia prose) decides it. No false contradicted from raw
    API rows by construction.
    """
    quote = '"date": "2024", "value": 8140897523'
    snap = WB_BODY
    pkg = EvidencePackage(
        package_id="pkg_wb", claim_id="T1-en",
        url="https://api.worldbank.org/v2/country/WLD/indicator/SP.POP.TOTL",
        captured_at="2026-11-01T00:00:00+00:00",
        sha256=sha256_of_text(snap), snapshot_path="data/snap/x.html",
        quote=quote, offsets=(snap.find(quote), snap.find(quote) + len(quote)),
        source_type="primary", numbers=(), extractor="model",
        source_year=2024)
    claim = claim_input_for(
        {"id": "T1-en",
         "text": "We are 8.14 billion people sharing one planet."})
    verdict = verify_claim(claim, [pkg], lambda p: snap)
    assert verdict.verdict == "unverifiable", verdict
    assert "unit_mismatch" in verdict.reasons


# ---------------------------------------------------------------------------
# aligned contract (1): numbers[].unit shared vocabulary
# ---------------------------------------------------------------------------

def test_numbers_unit_vocab_shared():
    assert UNIT_VOCAB >= {"USD", "percent", "count", "EUR", "m/s"}
    for quote in ("GDP was 29.3 trillion dollars in 2024.",
                  "Growth was nearly 40 percent last year.",
                  "France had 68.6 million inhabitants."):
        for mention in numbers_from_quote(quote):
            assert mention["unit"] in UNIT_VOCAB or mention["unit"] is None
            NumberMention(value=mention["value"], unit=mention["unit"],
                          scale_raw=mention["scale_raw"])  # must not raise
    with pytest.raises(ValueError):
        NumberMention(value=1.0, unit="lightyears", scale_raw="")


# ---------------------------------------------------------------------------
# aligned contract (2): sha256 over RAW snapshot bytes (legacy charset)
# ---------------------------------------------------------------------------

def test_sha256_raw_bytes_legacy_charset(tmp_path):
    text = "La dette caf\u00e9 atteint 27.72 trillion dollars en 2024."
    raw = text.encode("latin-1")  # legacy single-byte charset
    assert sha256_of_bytes(raw) != sha256_of_text(text)  # divergence is real
    bodies = {"legacy": None}  # placeholder; custom fetch below
    del bodies
    snap_dir = str(tmp_path / "fetch")
    os.makedirs(snap_dir, exist_ok=True)
    sha = hashlib.sha256(raw).hexdigest()
    with open(os.path.join(snap_dir, f"{sha}.html"), "wb") as fh:
        fh.write(raw)

    def _latin_fetch(url, snapshot_dir=None):
        if "legacy" not in url:
            raise FetchError(f"404 {url}")
        return FetchResult(
            url=url, final_url=url, status=200,
            content_type="text/html; charset=iso-8859-1",
            size=len(raw), sha256=sha,
            snapshot_path=os.path.join(snap_dir, f"{sha}.html"),
            captured_at="2026-11-01T00:00:00+00:00",
            text=text,  # fetch decodes with the declared charset
        )

    url = "https://legacy.example.org/debt"
    hits = [{"url": url, "title": "debt", "snippet": ""}]
    bus, tracker, store, _, stats, _ = _wired_chain(tmp_path, {}, hits)
    claim_id = "c-latin"
    claim_text = "Debt stands at 27.72 trillion dollars."
    claims = {claim_id: claim_input_for(
        {"id": claim_id, "text": claim_text})}
    bus.subscribe("claim.evidence", make_evidence_handler(
        bus, tracker, store, claims, stats,
        snapshot_resolver=lambda p: raw))
    llm = FakeLLM([dict(ESTRUCT_PLAN), _final(url, text),
                   dict(REDTEAM_SKIP)])
    config = SwarmConfig(llm=llm, search_fn=make_search_fn(hits),
                         fetch_fn=_latin_fetch)

    result = asyncio.run(run_claim(bus, tracker, claim_id, claim_text,
                                   config))

    assert len(result.packages) == 1
    assert result.packages[0]["sha256"] == sha  # raw-bytes hash end to end
    verdicts = bus.history("claim.verdict")
    assert len(verdicts) == 1
    assert verdicts[0]["verdict"] == "supported", verdicts[0]
    # store round-trips the same raw bytes (dedupe by raw sha)
    assert store.get_snapshot_bytes(sha) == raw
    store.close()


def test_store_bytes_text_paths_agree_on_utf8(tmp_path):
    store = EvidenceStore(tmp_path / "lib.db", tmp_path / "snap")
    text = "plain utf-8 snapshot with $27.72T in 2024."
    via_text = store.upsert_snapshot("https://example.org/a", text, "primary")
    via_bytes = store.upsert_snapshot_bytes(
        "https://example.org/a", text.encode("utf-8"), "primary")
    assert via_text.sha256 == via_bytes.sha256  # same bytes, same sha
    assert store.get_snapshot_text(via_text.sha256) == text
    store.close()


# ---------------------------------------------------------------------------
# aligned contract (3): primary/secondary criterion, both sides
# ---------------------------------------------------------------------------

def test_source_type_criterion_both_sides():
    primaries = [
        "https://api.worldbank.org/v2/country/WLD/x",
        "https://data.worldbank.org/indicator/x?foo=1",
        "https://fred.stlouisfed.org/series/GDP",
        "https://sub.ourworldindata.org/grapher/x",
        "https://query.wikidata.org/sparql?query=x",
    ]
    secondaries = [
        "https://en.wikipedia.org/wiki/X",
        "https://example.org/debt",
        "https://worldbank.org.evil.example.com/x",  # suffix spoof
        "not a url",
    ]
    for url in primaries:
        assert canonical_source_type(url) == "primary", url
        assert swarm_source_type(url) == "primary", url
    for url in secondaries:
        assert canonical_source_type(url) == "secondary", url
        assert swarm_source_type(url) == "secondary", url


# ---------------------------------------------------------------------------
# claim derivation + report helpers
# ---------------------------------------------------------------------------

def test_claim_input_derivation():
    claim = claim_input_for(
        {"id": "T3-en",
         "text": "The United States produced 29.3 trillion dollars of GDP "
                 "in 2024."})
    assert claim.value == pytest.approx(29.3e12)
    assert claim.unit == "USD"
    assert claim.raw == "29.3 trillion"
    assert claim.claim_year == 2024
    no_number = claim_input_for(
        {"id": "F11-en",
         "text": "As this chart proves, India's economy is now larger than "
                 "America's."})
    assert no_number.unit is None  # verifier abstains, never guesses
    no_year = claim_input_for(
        {"id": "T4-en",
         "text": "Its population reached 340 million people that same year."})
    assert no_year.value == pytest.approx(340e6)
    assert no_year.unit == "count"
    assert no_year.claim_year is None


def test_load_entries_filters_and_limits(tmp_path):
    rows = [
        {"id": "T1-en", "lang": "en", "expected": "true", "text": "t"},
        {"id": "F1-en", "lang": "en", "expected": "false", "text": "f"},
        {"id": "U1-en", "lang": "en", "expected": "unverifiable", "text": "u"},
        {"id": "T1-es", "lang": "es", "expected": "true", "text": "t"},
    ]
    path = tmp_path / "truth.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    assert [r["id"] for r in load_entries(path, "en")] == ["T1-en", "F1-en"]
    assert [r["id"] for r in load_entries(path, "en", limit=1)] == ["T1-en"]
    assert [r["id"] for r in load_entries(path, "es")] == ["T1-es"]


def test_build_report_matrix_and_false_contradicted():
    entries = [{"id": "T1", "expected": "true"},
               {"id": "T2", "expected": "true"},
               {"id": "F1", "expected": "false"}]
    verdicts = {"T1": "supported", "T2": "contradicted", "F1": "supported"}
    verdict_ts = {"T1": 110.0, "T2": 120.0, "F1": 130.0}
    invest_ts = {"T1": 100.0, "T2": 100.0, "F1": 100.0}
    report = build_report(entries, verdicts, verdict_ts, invest_ts)
    assert report["matrix"]["true"]["supported"] == 1
    assert report["matrix"]["true"]["contradicted"] == 1
    assert report["false_contradicted"] == ["T2"]
    assert report["false_supported"] == ["F1"]
    assert report["latency_s"]["count"] == 3
    assert report["latency_s"]["p50"] == pytest.approx(20.0)


def test_zero_live_guard(tmp_path, monkeypatch):
    """The offline E2E path touches no network even if one is available."""
    import urllib.request

    import live.swarm.fetch as fetch_mod

    def _boom(*a, **k):
        raise AssertionError("live network touched")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(fetch_mod, "urlopen", _boom)
    bodies = {"wikipedia": WIKI_BODY}
    hits = [{"url": WIKI_URL, "title": "W", "snippet": ""}]
    bus, tracker, store, snap_dir, stats, resolve = _wired_chain(
        tmp_path, bodies, hits)
    claim_id = "T1-en"
    text = "We are 8.14 billion people sharing one planet."
    claims = {claim_id: claim_input_for({"id": claim_id, "text": text})}
    bus.subscribe("claim.evidence", make_evidence_handler(
        bus, tracker, store, claims, stats, snapshot_resolver=resolve))
    llm = FakeLLM([dict(ESTRUCT_PLAN), _final(WIKI_URL, WIKI_QUOTE),
                   dict(REDTEAM_SKIP)])
    config = SwarmConfig(llm=llm, search_fn=make_search_fn(hits),
                         fetch_fn=make_fetch_fn(bodies, snap_dir))
    result = asyncio.run(run_claim(bus, tracker, claim_id, text, config))
    assert len(result.packages) == 1
    assert bus.history("claim.verdict")[0]["verdict"] == "supported"
    store.close()
