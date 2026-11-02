"""V5-EN Atlas Live: English-coverage swarm fixes (offline, $0).

Three GENERAL fixes (no per-claim rules) for the EN abstention gap
(S4: EN 0.862 vs ES 0.540 — bench ran pre-V5-P0; replay with the
current verifier decides 28/181, abstention 0.845):

(a) ``row_year_for_quote`` + ``build_package`` resolution: a package's
    ``source_year`` dates the CITED World Bank row, not the plan. The
    plan carries one year while pct_change claims cite two rows
    (2023+2024); stamping the plan year on both collapsed the pair the
    V5-P0 derivation needs (K1-08/K1-11/P1-06/P1-11/P1-12 class).
(b) Claim-aware excerpt ranking (``excerpt_needles_for_claim`` +
    ``doc_excerpts(..., needles=...)``): segments mentioning the
    claim's figures/entities read first so the model sees a citable
    sentence within budget (S1-05 class: the boiling sentence sat past
    budget in document order). ``needles=None`` keeps the historical
    order byte-for-byte.
(c) RECUPERADOR quotecraft nudge REVERTED: iteration 1 added
    unit-steering sentences to ``live/swarm/roles.py`` and live probes
    measured harm (cited rows -> 0 packages + budget burn, 0/7 prose
    companions). ``roles.py`` is byte-identical to pre-V5-EN; pins
    below lock the revert.

Also pins: ``numbers_from_quote`` billion/trillion scaling (verified,
unchanged) and the V5-EN diagnosis inputs (DDG dead, wiki-EN live).
All tests offline: synthetic snapshots, no network, no model calls.
"""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.swarm.coordinator import (  # noqa: E402
    DOC_EXCERPT_CHARS,
    Doc,
    SwarmConfig,
    _ClaimCtx,
    build_package,
    doc_excerpts,
    excerpt_needles_for_claim,
    numbers_from_quote,
    row_year_for_quote,
)
from live.swarm.fetch import FetchResult  # noqa: E402
from live.swarm.roles import RECUPERADOR_SYSTEM  # noqa: E402

WB_URL = ("https://api.worldbank.org/v2/country/USA/indicator/"
          "NY.GDP.MKTP.CD?format=json&per_page=50&date=2024%3A2024")
WIKI_URL = "https://en.wikipedia.org/wiki/Speed_of_light"


def _wb_body(date="2024", value=29298013000000):
    return (
        '[{"page": 1}, '
        f'[{{"indicator": {{"id": "NY.GDP.MKTP.CD"}}, '
        f'"countryiso3code": "USA", "date": "{date}", '
        f'"value": {value}}}]]'
    )


def _doc(url, body, excerpt=""):
    raw = body.encode("utf-8")
    fetch = FetchResult(
        url=url, final_url=url, status=200,
        content_type="application/json", size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        snapshot_path="/tmp/v5en-test-snap.html",
        captured_at="2026-11-02T00:00:00+00:00", text=body)
    return Doc(url=url, title="t", fetch=fetch, excerpt=excerpt or body[:80])


# ---------------------------------------------------------------------------
# (a) row_year_for_quote
# ---------------------------------------------------------------------------

class TestRowYearForQuote:
    def test_compact_row(self):
        q = '"countryiso3code": "USA", "date": "2024", "value": 29298'
        assert row_year_for_quote(q, WB_URL) == 2024

    def test_spaced_separators(self):
        q = '"date" : "2023" , "value" : 27811'
        assert row_year_for_quote(q, WB_URL) == 2023

    def test_two_distinct_dates_is_none(self):
        q = '"date": "2023", "value": 1, "date": "2024", "value": 2'
        assert row_year_for_quote(q, WB_URL) is None

    def test_same_date_twice_is_that_year(self):
        q = '"date": "2024", "value": 1, "date": "2024", "value": 1'
        assert row_year_for_quote(q, WB_URL) == 2024

    def test_prose_quote_is_none(self):
        assert row_year_for_quote("GDP was 29.3 trillion in 2024", WB_URL) is None

    def test_non_wb_host_is_none(self):
        q = '"date": "2024", "value": 29298'
        assert row_year_for_quote(q, WIKI_URL) is None
        assert row_year_for_quote(q, "https://data.worldbank.org/x") is None

    def test_bad_inputs_are_none(self):
        assert row_year_for_quote("", WB_URL) is None
        assert row_year_for_quote(None, WB_URL) is None  # type: ignore[arg-type]
        assert row_year_for_quote('"date": "2024"', "") is None
        assert row_year_for_quote('"date": "2024"', None) is None  # type: ignore[arg-type]
        assert row_year_for_quote('"date": "9999"', WB_URL) is None
        assert row_year_for_quote('"date": "99"', WB_URL) is None
        assert row_year_for_quote(123, WB_URL) is None  # type: ignore[arg-type]

    def test_never_raises(self):
        assert row_year_for_quote(object(), object()) is None  # type: ignore[arg-type]


class TestBuildPackageYearResolution:
    def test_wb_row_year_wins_over_plan_year(self):
        body = _wb_body("2023", 27811517000000)
        quote = '"countryiso3code": "USA", "date": "2023", "value": 27811517000000'
        pkg = build_package("c1", WB_URL, quote, _doc(WB_URL, body), 2024, 0)
        assert pkg is not None
        assert pkg["source_year"] == 2023

    def test_matching_year_unchanged(self):
        body = _wb_body("2024", 29298013000000)
        quote = '"countryiso3code": "USA", "date": "2024", "value": 29298013000000'
        pkg = build_package("c1", WB_URL, quote, _doc(WB_URL, body), 2024, 0)
        assert pkg is not None
        assert pkg["source_year"] == 2024

    def test_prose_quote_keeps_plan_year(self):
        body = "<html><body><p>Unemployment averaged 4.0 percent in 2024.</p></body></html>"
        quote = "Unemployment averaged 4.0 percent in 2024."
        pkg = build_package("c1", WIKI_URL, quote, _doc(WIKI_URL, body),
                            2024, 0)
        assert pkg is not None
        assert pkg["source_year"] == 2024

    def test_multi_row_quote_keeps_plan_year(self):
        body = ('[{"page": 1}, '
                '[{"date": "2024", "value": 1}, '
                '{"date": "2023", "value": 2}]]')
        quote = '"date": "2024", "value": 1}, {"date": "2023", "value": 2'
        assert quote in body
        pkg = build_package("c1", WB_URL, quote, _doc(WB_URL, body), 2024, 0)
        assert pkg is not None
        assert pkg["source_year"] == 2024

    def test_none_plan_year_with_row_year(self):
        body = _wb_body("2025", 5050922925047)
        quote = '"countryiso3code": "USA", "date": "2025", "value": 5050922925047'
        pkg = build_package("c1", WB_URL, quote, _doc(WB_URL, body), None, 0)
        assert pkg is not None
        assert pkg["source_year"] == 2025

    def test_pct_change_pair_resolves_distinct_years(self):
        """K1-08 shape: two endpoint rows stamped with one plan year."""
        bodies = {"2024": _wb_body("2024", 29298013000000),
                  "2023": _wb_body("2023", 27811517000000)}
        years = set()
        for idx, year in enumerate(("2024", "2023")):
            body = bodies[year]
            quote = (f'"countryiso3code": "USA", "date": "{year}", '
                     f'"value": {29298013000000 if year == "2024" else 27811517000000}')
            pkg = build_package("K1-08-en", WB_URL, quote,
                                _doc(WB_URL, body), 2024, idx)
            assert pkg is not None
            years.add(pkg["source_year"])
        assert years == {2023, 2024}


# ---------------------------------------------------------------------------
# (b) claim-aware excerpt ranking
# ---------------------------------------------------------------------------

class TestExcerptNeedles:
    def test_digits_and_variants(self):
        needles = excerpt_needles_for_claim(
            "Light travels at exactly 299,792,458 meters per second.")
        assert "299,792,458" in needles
        assert "299792458" in needles
        assert "light" in needles
        assert "meters" in needles

    def test_short_words_dropped(self):
        needles = excerpt_needles_for_claim("It is at 100 degrees.")
        assert "100" in needles
        assert "degrees" in needles
        assert "it" not in needles
        assert "is" not in needles
        assert "at" not in needles

    def test_dedup_and_cap(self):
        needles = excerpt_needles_for_claim("growth " * 100 + "2024 2024")
        assert len(needles) <= 32
        assert len(set(needles)) == len(needles)

    def test_spanish_words(self):
        needles = excerpt_needles_for_claim(
            "La población mundial creció un 1,0 por ciento.")
        assert "población" in needles
        assert "creció" in needles

    def test_empty_and_none(self):
        assert excerpt_needles_for_claim("") == []
        assert excerpt_needles_for_claim(None) == []  # type: ignore[arg-type]

    def test_never_raises(self):
        assert excerpt_needles_for_claim(object()) == []  # type: ignore[arg-type]


def _wiki_like_html():
    filler = ("<p>The history of metrology spans many centuries of gradual "
              "refinement across dozens of national laboratories worldwide.</p>")
    key = ("<p>At sea level, pure water boils at 100 degrees Celsius under "
           "standard atmospheric pressure conditions today.</p>")
    return "<html><body>" + filler * 40 + key + filler * 40 + "</body></html>"


class TestDocExcerptsNeedles:
    def test_none_keeps_document_order(self):
        html = _wiki_like_html()
        assert doc_excerpts(html) == doc_excerpts(html, needles=None)
        assert doc_excerpts(html) == doc_excerpts(html, needles=[])

    def test_ranked_surfaces_claim_sentence_first(self):
        html = _wiki_like_html()
        needles = excerpt_needles_for_claim(
            "Water boils at 100 degrees Celsius at sea level.")
        ranked = doc_excerpts(html, needles=needles)
        first = ranked.split("\n")[0]
        assert "100 degrees Celsius" in first
        assert "100 degrees Celsius" not in doc_excerpts(html, limit=600)

    def test_verbatim_pact_holds_when_ranked(self):
        html = _wiki_like_html()
        needles = excerpt_needles_for_claim("Water boils at 100 degrees.")
        ranked = doc_excerpts(html, needles=needles)
        for line in ranked.split("\n"):
            if line.startswith("TABLE "):
                continue
            assert line in html.replace("\n", " ") or line in html

    def test_ties_keep_document_order(self):
        html = ("<html><body>"
                "<p>Alpha segment about boiling water temperature here ok today indeed.</p>"
                "<p>Beta segment about boiling water pressure here ok now indeed.</p>"
                "</body></html>")
        ranked = doc_excerpts(html, needles=["boiling", "water"])
        lines = ranked.split("\n")
        assert lines[0].startswith("Alpha segment")
        assert lines[1].startswith("Beta segment")

    def test_junk_still_dropped_when_ranked(self):
        html = ("<html><body>"
                '<p>{"template": "chart", "data-mw": [1, 2, 3, 100]}</p>'
                "<p>Real sentence stating water boils at 100 degrees ok.</p>"
                "</body></html>")
        ranked = doc_excerpts(html, needles=["100", "water", "boils"])
        assert "template" not in ranked
        assert "100 degrees" in ranked

    def test_limit_still_honored(self):
        html = _wiki_like_html()
        ranked = doc_excerpts(html, limit=200, needles=["100", "water"])
        assert len(ranked) <= 200 + 200  # one segment may use the budget

    def test_default_limit_unchanged(self):
        assert DOC_EXCERPT_CHARS == 6000

    def test_ctx_threads_needles(self):
        ctx = _ClaimCtx("c1", "Water boils at 100 degrees Celsius.",
                        SwarmConfig(llm=None))
        assert ctx.tools.excerpt_needles
        assert "100" in ctx.tools.excerpt_needles


# ---------------------------------------------------------------------------
# (c) RECUPERADOR prompt: quotecraft nudge REVERTED (measured harm)
# ---------------------------------------------------------------------------
#
# Iteration 1 added two unit-steering sentences (quote figure WITH its
# unit; ALSO cite prose for non-curated WB rows). Live probes showed
# harm, not help: K1-18/K1-21 went from cited-WB-row (control, old
# prompt) to 0 packages + budget burn (3/3 runs), and two rephrased
# variants (quotecraft-only, instead-of-JSON) scored 0/4 citations on
# the same claims. The instruction makes the model dither instead of
# finalizing, and no prose companion ever materialized in 7 runs. The
# pins below lock the revert: no unit-steering sentences without a new
# measured win (see live/V5_EN.md iteration log).

class TestRecuperadorPrompt:
    def test_no_unit_steering_sentences(self):
        assert "WITH that unit word right after it" not in RECUPERADOR_SYSTEM
        assert "ALSO cite a prose document" not in RECUPERADOR_SYSTEM
        assert "instead of the JSON row" not in RECUPERADOR_SYSTEM

    def test_no_indicator_codes_in_prompt(self):
        assert "SP.POP.TOTL" not in RECUPERADOR_SYSTEM
        assert "NY.GDP.MKTP.CD" not in RECUPERADOR_SYSTEM

    def test_verbatim_rule_intact(self):
        assert "CHARACTER-FOR-CHARACTER" in RECUPERADOR_SYSTEM

    def test_empty_findings_rule_intact(self):
        assert '{"findings": []}' in RECUPERADOR_SYSTEM


# ---------------------------------------------------------------------------
# number extraction: billion/trillion verified (unchanged behavior)
# ---------------------------------------------------------------------------

class TestNumbersBillionTrillion:
    @pytest.mark.parametrize(("text", "value"), [
        ("111.7 trillion dollars", 111.7e12),
        ("29.3 trillion dollars", 29.3e12),
        ("3.76 trillion dollars", 3.76e12),
        ("8.14 billion people", 8.14e9),
        ("340 million people", 340e6),
        ("10.3 billion", 10.3e9),
    ])
    def test_scale_words(self, text, value):
        nums = numbers_from_quote(text)
        assert nums, text
        assert nums[0]["value"] == pytest.approx(value, rel=1e-9)

    def test_trillion_beats_billion_by_1000x(self):
        tn = numbers_from_quote("111.7 trillion dollars")[0]["value"]
        bn = numbers_from_quote("111.7 billion dollars")[0]["value"]
        assert tn / bn == pytest.approx(1000.0)
