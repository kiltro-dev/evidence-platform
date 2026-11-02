"""M2d recall tests: preamble rescue + structured-source adapter + budget.

All offline (0 live network, 0 model calls): FakeLLM/FakeSearch/FakeFetch
doubles plus direct unit pins. Covers the three M2d edits:

(a) rescue of preamble-wrapped JSON replies (live cause 2: 5 claims died
    on `"I'll search for ...{...valid...}"`),
(b) source_meta adapter for World Bank API JSON snapshots (live cause 1:
    unitless `"value": 8140897523` quotes were indecidible),
(c) token budget 12k -> 20k pin (live cause N3: 13% degraded).

Guards that must NEVER move: 2% tolerance, scale-slip backstop,
primary-beats-secondary hierarchy, claim_unit_unknown for numberless
claims. Each has a pin below running THROUGH the new code paths.
"""

import asyncio
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.evidence.package import (  # noqa: E402
    EvidencePackage,
    SourceMeta,
    sha256_of_text,
    source_meta_for_snapshot,
)
from live.orchestrator.bus import EventBus  # noqa: E402
from live.orchestrator.states import ClaimTracker  # noqa: E402
from live.run_verify import claim_input_for  # noqa: E402
from live.swarm.coordinator import (  # noqa: E402
    TOKEN_BUDGET_PER_CLAIM,
    Doc,
    SwarmConfig,
    build_package,
    run_claim,
)
from live.swarm.fetch import FetchError, FetchResult  # noqa: E402
from live.swarm.roles import extract_balanced_json  # noqa: E402
from live.verifier.check import ClaimInput, verify_claim  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _wb_body(indicator, date, value, country="WLD"):
    return (
        '[{"page": 1, "lastupdated": "2025-09-01"}, '
        f'[{{"indicator": {{"id": "{indicator}"}}, '
        f'"country": {{"id": "{country}"}}, '
        f'"date": "{date}", "value": {value}, "decimal": "0"}}]]'
    )


def _wb_quote(date, value):
    return f'"date": "{date}", "value": {value}'


WB_POP_BODY = _wb_body("SP.POP.TOTL", "2024", 8140897523)
WB_POP_QUOTE = _wb_quote("2024", 8140897523)
WB_GDP_BODY = _wb_body("NY.GDP.MKTP.CD", "2024", 29298013000000, "USA")
WB_GDP_QUOTE = _wb_quote("2024", 29298013000000)
WB_URL = ("https://api.worldbank.org/v2/country/WLD/indicator/SP.POP.TOTL"
          "?format=json&per_page=50&date=2024%3A2024")

WIKI_URL = "https://en.wikipedia.org/wiki/World_population"
WIKI_BODY = (
    "<html><body><p>The world population reached 8.14 billion people "
    "in 2024 according to United Nations estimates.</p></body></html>"
)
WIKI_QUOTE = ("The world population reached 8.14 billion people in 2024 "
              "according to United Nations estimates.")

ESTRUCT_PLAN = {
    "entities": ["World Bank", "world population"],
    "indicator_candidate": "SP.POP.TOTL",
    "geo": {"name": None, "iso3": None},
    "year": 2024,
    "web_query": "world population 2024",
    "numbers_in_claim": ["8.14 billion"],
}
REDTEAM_SKIP = {"counter_query": "", "rationale": "narrow", "skip": True}


class FakeLLM:
    name = "fake-llm"
    available = True

    def __init__(self, script, prompt_tokens=100, completion_tokens=20):
        self._script = list(script)
        self.calls: list = []
        self.last_usage = None
        self.last_usage_seq = 0
        self._pt = prompt_tokens
        self._ct = completion_tokens

    def complete_json(self, system, user, max_tokens=1500,
                      reasoning_effort=None, json_schema=None):
        self.calls.append(user)
        self.last_usage_seq += 1
        self.last_usage = {"prompt_tokens": self._pt,
                           "completion_tokens": self._ct}
        return self._script.pop(0)


def make_fetch_fn(bodies, snap_dir):
    def _fake(url, snapshot_dir=None):
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
                    content_type="application/json",
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


def _pkg(claim_id, url, snap, quote, source_type="primary",
         source_meta=None, year=2024, pid="pkg_m2d"):
    start = snap.find(quote)
    assert start >= 0, "quote must be a verbatim snapshot substring"
    return EvidencePackage(
        package_id=pid, claim_id=claim_id, url=url,
        captured_at="2026-11-01T00:00:00+00:00",
        sha256=sha256_of_text(snap), snapshot_path="data/snap/x.html",
        quote=quote, offsets=(start, start + len(quote)),
        source_type=source_type, numbers=(), extractor="model",
        source_year=year, source_meta=source_meta)


# ---------------------------------------------------------------------------
# (a) preamble rescue: extract_balanced_json unit pins
# ---------------------------------------------------------------------------

def test_rescue_doc_repr_preamble():
    """Real DOC-REPR shape: chat preamble + valid object (M2_E2E cause 2)."""
    inner = {"thought": "searching",
             "tool_calls": [{"name": "web_search", "arguments": {"query": "x"}}],
             "final": None}
    raw = ("I'll search for the World Bank population data first, "
           "then fetch the best sources." + json.dumps(inner))
    candidate = extract_balanced_json(raw)
    assert candidate is not None
    assert json.loads(candidate) == inner


def test_rescue_greeting_and_trailer():
    raw = ('Sure! Here is the plan: {"year": 2024, "skip": false} '
           'Hope that helps.')
    assert json.loads(extract_balanced_json(raw)) == {"year": 2024,
                                                      "skip": False}


def test_rescue_markdown_fence():
    raw = '```json\n{"a": 1, "b": [1, 2]}\n```'
    assert json.loads(extract_balanced_json(raw)) == {"a": 1, "b": [1, 2]}


def test_rescue_braces_inside_strings():
    inner = {"q": "{ not json } [also not]", "n": {"deep": [1, {"x": "}"}]}}
    raw = "preamble " + json.dumps(inner) + " trailer"
    assert json.loads(extract_balanced_json(raw)) == inner


def test_rescue_bracket_noise_then_object():
    """[doc 1]-style noise before the object falls back to the '{'."""
    raw = 'See [doc 1] and [doc 2]: {"final": {"findings": []}} done.'
    assert json.loads(extract_balanced_json(raw)) == {"final": {"findings": []}}


def test_rescue_top_level_array():
    raw = 'rows: [{"a": 1}, {"a": 2}]'
    assert json.loads(extract_balanced_json(raw)) == [{"a": 1}, {"a": 2}]


@pytest.mark.parametrize("raw", [
    "I'll search for it, one moment please...",  # no brackets at all
    '{"unclosed": [1, 2',  # unbalanced
    "",  # empty
    "}{",  # closers first
    "[oops",  # unbalanced bracket
])
def test_rescue_unrescuable_is_none(raw):
    assert extract_balanced_json(raw) is None


def test_rescue_non_string_is_none():
    assert extract_balanced_json(None) is None
    assert extract_balanced_json(123) is None  # type: ignore[arg-type]


def test_rescue_first_valid_span_wins():
    raw = '{"a": 1} {"b": '
    assert json.loads(extract_balanced_json(raw)) == {"a": 1}


# ---------------------------------------------------------------------------
# (a) wired: preamble flake no longer spends the retry / kills the claim
# ---------------------------------------------------------------------------

class PreambleFlakyLLM(FakeLLM):
    """First call raises with DOC-REPR preamble text, then the script."""

    def __init__(self, script, first_reply):
        super().__init__(script)
        self._first_reply = first_reply
        self._flaked = False

    def complete_json(self, *a, **k):
        if not self._flaked:
            self._flaked = True
            self.last_usage_seq += 1
            self.last_usage = {"prompt_tokens": 50, "completion_tokens": 10}
            raw = ("I'll search for the relevant data. "
                   + json.dumps(self._first_reply))
            raise json.JSONDecodeError("Expecting value", raw, 0)
        return super().complete_json(*a, **k)


def test_preamble_rescued_end_to_end(tmp_path, monkeypatch):
    """A preamble-wrapped estructurador reply still yields its package."""
    import urllib.request

    import live.swarm.fetch as fetch_mod

    def _boom(*a, **k):
        raise AssertionError("live network touched")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr(fetch_mod, "urlopen", _boom)

    bodies = {"worldbank": WB_POP_BODY, "wikipedia": WIKI_BODY}
    hits = [{"url": WIKI_URL, "title": "W", "snippet": ""}]
    snap_dir = str(tmp_path / "fetch")
    final = {"thought": "citing",
             "final": {"findings": [{"url": WIKI_URL, "quote": WIKI_QUOTE}]}}
    llm = PreambleFlakyLLM([final, dict(REDTEAM_SKIP)], dict(ESTRUCT_PLAN))
    # F2-7: redteam deferred by default; opt in to pin recup+redteam calls.
    config = SwarmConfig(llm=llm, search_fn=make_search_fn(hits),
                         fetch_fn=make_fetch_fn(bodies, snap_dir),
                         redteam_enabled=True)
    bus = EventBus()
    tracker = ClaimTracker(bus)
    result = asyncio.run(run_claim(bus, tracker, "c-pre",
                                   "We are 8.14 billion people.", config))
    assert result.degraded is False
    assert len(result.packages) == 1
    # flake branch records no scripted call: estructurador came from the
    # rescue, so only recuperador + redteam consumed the script (no retry).
    assert len(llm.calls) == 2


# ---------------------------------------------------------------------------
# (b) source_meta_for_snapshot: API-declared indicator only
# ---------------------------------------------------------------------------

def test_source_meta_wb_population():
    meta = source_meta_for_snapshot(WB_URL, WB_POP_BODY)
    assert meta == SourceMeta(indicator="SP.POP.TOTL", unit="count", scale=1.0)


def test_source_meta_wb_gdp():
    url = ("https://api.worldbank.org/v2/country/USA/indicator/NY.GDP.MKTP.CD"
           "?format=json")
    meta = source_meta_for_snapshot(url, WB_GDP_BODY)
    assert meta == SourceMeta(indicator="NY.GDP.MKTP.CD", unit="USD", scale=1.0)


def test_source_meta_rejects_non_wb_host():
    assert source_meta_for_snapshot("https://example.org/api", WB_POP_BODY) is None
    assert source_meta_for_snapshot(WIKI_URL, WB_POP_BODY) is None


def test_source_meta_rejects_prose_and_malformed():
    assert source_meta_for_snapshot(WB_URL, WIKI_BODY) is None
    assert source_meta_for_snapshot(WB_URL, "not json {{{") is None
    assert source_meta_for_snapshot(WB_URL, "") is None
    assert source_meta_for_snapshot(WB_URL, '{"a": 1}') is None  # not [m, rows]
    assert source_meta_for_snapshot(WB_URL, '[[], []]') is None  # empty rows


def test_source_meta_rejects_mixed_and_unknown_indicators():
    mixed = (
        '[{"page": 1}, '
        '[{"indicator": {"id": "SP.POP.TOTL"}, "date": "2024", "value": 1}, '
        ' {"indicator": {"id": "NY.GDP.MKTP.CD"}, "date": "2024", "value": 2}]]'
    )
    assert source_meta_for_snapshot(WB_URL, mixed) is None  # must agree
    # V5-2B policy revision (contract change, see live/V5_2B.md §2):
    # SP.POP.GROW ("Population growth (annual %)") now maps to percent —
    # percent-denominated rates are directly comparable to single-year
    # percent claims. The deliberately-unmapped exemplar moves to PPP
    # dollars ("current international $" is not USD).
    ppp = _wb_body("NY.GDP.MKTP.PP.CD", "2024", 41259823532528)
    assert source_meta_for_snapshot(WB_URL, ppp) is None
    assert source_meta_for_snapshot("not a url", WB_POP_BODY) is None


# ---------------------------------------------------------------------------
# (b) package contract: SourceMeta round-trip + legacy compat
# ---------------------------------------------------------------------------

def test_source_meta_round_trip():
    pkg = _pkg("T1-en", WB_URL, WB_POP_BODY, WB_POP_QUOTE,
               source_meta=SourceMeta("SP.POP.TOTL", "count", 1.0))
    clone = EvidencePackage.from_dict(pkg.to_dict())
    assert clone.source_meta == SourceMeta("SP.POP.TOTL", "count", 1.0)
    assert clone.to_dict() == pkg.to_dict()


def test_source_meta_invalid_unit_rejected():
    with pytest.raises(ValueError):
        SourceMeta("SP.POP.TOTL", "lightyears", 1.0)
    with pytest.raises(ValueError):
        SourceMeta("", "count", 1.0)
    with pytest.raises(ValueError):
        SourceMeta("SP.POP.TOTL", "count", 0.0)


def test_legacy_package_dict_without_source_meta():
    pkg = _pkg("T1-en", WB_URL, WB_POP_BODY, WB_POP_QUOTE)
    assert pkg.source_meta is None
    d = pkg.to_dict()
    del d["source_meta"]  # pre-M2d payload shape
    assert EvidencePackage.from_dict(d).source_meta is None


def test_build_package_attaches_meta_for_wb_only(tmp_path):
    def _doc(url, body):
        raw = body.encode("utf-8")
        fetch = FetchResult(
            url=url, final_url=url, status=200, content_type="application/json",
            size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
            snapshot_path=str(tmp_path / "s.html"),
            captured_at="2026-11-01T00:00:00+00:00", text=body)
        return Doc(url=url, title="t", fetch=fetch, excerpt=body[:100])

    wb = build_package("T1-en", WB_URL, WB_POP_QUOTE, _doc(WB_URL, WB_POP_BODY),
                       2024, 0)
    assert wb is not None
    assert wb["source_meta"] == {"indicator": "SP.POP.TOTL", "unit": "count",
                                 "scale": 1.0}
    EvidencePackage.from_dict(wb)  # M2a accepts the new dict shape
    wiki = build_package("T1-en", WIKI_URL, WIKI_QUOTE, _doc(WIKI_URL, WIKI_BODY),
                         2024, 1)
    assert wiki is not None
    assert wiki["source_meta"] is None


# ---------------------------------------------------------------------------
# (b) verifier: structured fallback decides, never trusts blindly
# ---------------------------------------------------------------------------

def test_wb_quote_with_meta_supported():
    claim = claim_input_for(
        {"id": "T1-en",
         "text": "We are 8.14 billion people sharing one planet."})
    pkg = _pkg("T1-en", WB_URL, WB_POP_BODY, WB_POP_QUOTE,
               source_meta=SourceMeta("SP.POP.TOTL", "count", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: WB_POP_BODY)
    assert verdict.verdict == "supported", verdict
    assert verdict.reasons == ["numeric_match"]
    assert verdict.details["pkg_m2d"]["unit_from"] == "source_meta"


def test_wb_quote_with_meta_contradicted():
    claim = claim_input_for(
        {"id": "F3-en",
         "text": "Meanwhile China's population has fallen to just "
                 "141 million people."})
    body = _wb_body("SP.POP.TOTL", "2024", 1408975000, "CHN")
    url = ("https://api.worldbank.org/v2/country/CHN/indicator/SP.POP.TOTL"
           "?format=json")
    pkg = _pkg("F3-en", url, body, _wb_quote("2024", 1408975000),
               source_meta=SourceMeta("SP.POP.TOTL", "count", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: body)
    assert verdict.verdict == "contradicted", verdict
    assert verdict.reasons == ["numeric_mismatch"]


def test_tampered_indicator_ignored():
    """Package claims GDP but the snapshot declares population -> mismatch."""
    claim = claim_input_for(
        {"id": "T1-en",
         "text": "We are 8.14 billion people sharing one planet."})
    pkg = _pkg("T1-en", WB_URL, WB_POP_BODY, WB_POP_QUOTE,
               source_meta=SourceMeta("NY.GDP.MKTP.CD", "USD", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: WB_POP_BODY)
    assert verdict.verdict == "unverifiable", verdict
    assert "unit_mismatch" in verdict.reasons


def test_tampered_unit_corrected_from_snapshot():
    """A lying unit field cannot flip the verdict: count claim still wins."""
    claim = claim_input_for(
        {"id": "T1-en",
         "text": "We are 8.14 billion people sharing one planet."})
    pkg = _pkg("T1-en", WB_URL, WB_POP_BODY, WB_POP_QUOTE,
               source_meta=SourceMeta("SP.POP.TOTL", "USD", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: WB_POP_BODY)
    assert verdict.verdict == "supported", verdict
    assert verdict.details["pkg_m2d"]["evidence_unit"] == "count"


def test_meta_does_not_bridge_unit_families():
    """Percent claim vs count indicator stays unit_mismatch (F6 guard)."""
    claim = claim_input_for(
        {"id": "F6-en",
         "text": "Last year alone, world population grew by nearly 40 percent."})
    assert claim.unit == "percent"
    pkg = _pkg("F6-en", WB_URL, WB_POP_BODY, WB_POP_QUOTE,
               source_meta=SourceMeta("SP.POP.TOTL", "count", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: WB_POP_BODY)
    assert verdict.verdict == "unverifiable", verdict
    assert "unit_mismatch" in verdict.reasons


def test_meta_does_not_rescue_numberless_claim():
    claim = claim_input_for(
        {"id": "F11-en",
         "text": "As this chart proves, India's economy is now larger than "
                 "America's."})
    assert claim.unit is None
    pkg = _pkg("F11-en", WB_URL, WB_POP_BODY, WB_POP_QUOTE,
               source_meta=SourceMeta("SP.POP.TOTL", "count", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: WB_POP_BODY)
    assert verdict.verdict == "unverifiable", verdict
    assert "claim_unit_unknown" in verdict.reasons


def test_scale_backstop_intact_with_meta():
    """Unscaled claim vs trillion-scale WB value still abstains."""
    claim = ClaimInput(claim_id="s", text="GDP was 29.3 dollars.",
                       value=29.3, unit="USD", raw="29.3", claim_year=None)
    pkg = _pkg("s", "https://api.worldbank.org/v2/x", WB_GDP_BODY, WB_GDP_QUOTE,
               source_meta=SourceMeta("NY.GDP.MKTP.CD", "USD", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: WB_GDP_BODY)
    assert verdict.verdict == "unverifiable", verdict
    assert "possible_scale_error" in verdict.reasons


@pytest.mark.parametrize("value,expected", [
    (101.9, "supported"),      # 1.9% off: inside the 2% tolerance
    (102.1, "contradicted"),   # 2.1% off: outside -> real contradiction
])
def test_tolerance_intact_with_meta(value, expected):
    claim = claim_input_for(
        {"id": "t", "text": "The fund holds 100 dollars in reserve."})
    assert claim.value == pytest.approx(100.0) and claim.unit == "USD"
    body = _wb_body("NY.GDP.MKTP.CD", "2024", value)
    url = "https://api.worldbank.org/v2/country/USA/indicator/NY.GDP.MKTP.CD"
    pkg = _pkg("t", url, body, _wb_quote("2024", value),
               source_meta=SourceMeta("NY.GDP.MKTP.CD", "USD", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: body)
    assert verdict.verdict == expected, verdict


def test_hierarchy_intact_with_meta():
    """Usable primary (contradict) still outranks secondary (support)."""
    claim = claim_input_for(
        {"id": "T1-en",
         "text": "We are 8.14 billion people sharing one planet."})
    prim_body = _wb_body("SP.POP.TOTL", "2024", 50000)
    prim = _pkg("T1-en", WB_URL, prim_body, _wb_quote("2024", 50000),
                source_type="primary",
                source_meta=SourceMeta("SP.POP.TOTL", "count", 1.0),
                pid="pkg_prim")
    sec = _pkg("T1-en", WIKI_URL, WIKI_BODY, WIKI_QUOTE,
               source_type="secondary", pid="pkg_sec")
    snaps = {"pkg_prim": prim_body, "pkg_sec": WIKI_BODY}
    verdict = verify_claim(claim, [prim, sec], lambda p: snaps[p.package_id])
    assert verdict.verdict == "contradicted", verdict
    assert verdict.details.get("hierarchy") == "primary_beats_secondary"
    assert verdict.package_ids == ["pkg_prim"]


def test_no_meta_still_unit_mismatch():
    """Without source_meta the old contract holds (unitless JSON decides
    nothing) — complements the M2c pin for the new-package path."""
    claim = claim_input_for(
        {"id": "T1-en",
         "text": "We are 8.14 billion people sharing one planet."})
    pkg = _pkg("T1-en", WB_URL, WB_POP_BODY, WB_POP_QUOTE,
               source_meta=None)
    verdict = verify_claim(claim, [pkg], lambda p: WB_POP_BODY)
    assert verdict.verdict == "unverifiable", verdict
    assert "unit_mismatch" in verdict.reasons


# ---------------------------------------------------------------------------
# (c) budget pin
# ---------------------------------------------------------------------------

def test_token_budget_raised_to_20k():
    assert TOKEN_BUDGET_PER_CLAIM == 20000
    assert SwarmConfig().token_budget_per_claim == 20000

