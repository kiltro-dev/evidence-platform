"""F2-2 recall tests: general fixes, general fixtures (offline).

Covers the F2_RECALL edits (all live-network/model-free):

(a) multi-query grounding: >=3 diverse queries per claim,
(b) primary-first fetch ordering,
(c) stdlib HTML table reading,
(d) stdlib-minimal PDF text reading (fetch.text),
(e) deterministic World Bank row rescue (structured-unit rescue),
(f) multi-source arithmetic guard (no false-contradicted from components),
(g) RECUPERADOR refuting-quote instruction pin.

Guards that must NEVER move: 2% tolerance, scale-slip backstop,
primary-beats-secondary hierarchy. Each has a pin below running
THROUGH the new code paths.

Fixtures are synthetic (Chile/Tuvalu/Banaba: never dev claims, never
dev figures): every fix is general or it is not a fix.
"""

import hashlib
import os
import sys
import zlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.evidence.package import (  # noqa: E402
    EvidencePackage,
    SourceMeta,
    is_multi_source_arithmetic,
    sha256_of_text,
)
from live.run_verify import claim_input_for  # noqa: E402
from live.swarm.coordinator import (  # noqa: E402
    Doc,
    build_package,
    build_search_queries,
    doc_excerpts,
    rank_hits_primary_first,
    tables_from_html,
    wb_fallback_eligible,
    wb_row_quote_for,
)
from live.swarm.fetch import FetchResult, extract_pdf_text, is_pdf_body  # noqa: E402
from live.swarm.roles import RECUPERADOR_SYSTEM  # noqa: E402
from live.verifier.check import verify_claim  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures (synthetic: never dev claims, never dev figures)
# ---------------------------------------------------------------------------

SYN_PLAN = {
    "entities": ["Chile", "population"],
    "indicator": "SP.POP.TOTL",
    "iso3": "CHL",
    "year": 2024,
    "web_query": "Chile population 2024 World Bank",
    "numbers_in_claim": ["12,500"],
}

SYN_TEXT = "Chile had 12,500 people in 2024, according to the World Bank."

SYN_WB_BODY = (
    '[{"page":1,"pages":1,"per_page":"50","total":1},'
    '[{"indicator":{"id":"SP.POP.TOTL","value":"Population, total"},'
    '"country":{"id":"CL","value":"Chile"},"countryiso3code":"CHL",'
    '"date":"2024","value":12500,"unit":"","obs_status":"","decimal":0}]]'
)

SYN_WB_URL = ("https://api.worldbank.org/v2/country/CHL/indicator/SP.POP.TOTL"
              "?format=json&per_page=50&date=2024%3A2024")

SYN_HTML_TABLE = """<html><head><title>Labor report</title></head><body>
<h1>Employment Situation</h1>
<p>The unemployment rate was 4.2 percent in September.</p>
<table><caption>Table A. Labor figures</caption>
<thead><tr><th>Measure</th><th>September</th><th>Unit</th></tr></thead>
<tbody>
<tr><td>Unemployment rate</td><td>4.2</td><td>percent</td></tr>
<tr><td>Unemployed persons</td><td>7.1</td><td>million</td></tr>
</tbody></table>
<table><tr><td>footnote</td><td>seasonally adjusted</td></tr></table>
</body></html>"""


def _pkg(claim_id, url, snap, quote, source_type="primary",
         source_meta=None, year=2024, pid="pkg_f2r"):
    start = snap.find(quote)
    assert start >= 0, "quote must be a verbatim snapshot substring"
    return EvidencePackage(
        package_id=pid, claim_id=claim_id, url=url,
        captured_at="2026-11-01T00:00:00+00:00",
        sha256=sha256_of_text(snap), snapshot_path="data/snap/x.html",
        quote=quote, offsets=(start, start + len(quote)),
        source_type=source_type, numbers=(), extractor="model",
        source_year=year, source_meta=source_meta)


def _claim(text, cid="syn-1"):
    return claim_input_for({"id": cid, "text": text})


def _doc(url, body):
    raw = body.encode("utf-8")
    fetch = FetchResult(
        url=url, final_url=url, status=200, content_type="application/json",
        size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
        snapshot_path="/tmp/f2r-snap.html",
        captured_at="2026-11-01T00:00:00+00:00", text=body)
    return Doc(url=url, title="t", fetch=fetch, excerpt=body[:100])


# ---------------------------------------------------------------------------
# (a) multi-query grounding: >=3 diverse queries
# ---------------------------------------------------------------------------

def test_build_search_queries_at_least_three():
    queries = build_search_queries(dict(SYN_PLAN), SYN_TEXT)
    assert len(queries) >= 3
    assert all(isinstance(q, str) and q.strip() for q in queries)
    assert len(set(queries)) == len(queries)  # unique


def test_build_search_queries_covers_facets():
    queries = build_search_queries(dict(SYN_PLAN), SYN_TEXT)
    blob = " ".join(queries).lower()
    assert "chile" in blob  # entity facet
    assert "2024" in blob  # year facet
    assert any("official statistics" in q or "SP.POP.TOTL" in q
               for q in queries)  # authority/code facet


def test_build_search_queries_degenerate_plan_still_three():
    assert len(build_search_queries({}, "")) >= 3
    empty_plan = {"entities": [], "indicator": None, "iso3": None,
                  "year": None, "web_query": "",
                  "numbers_in_claim": []}
    queries = build_search_queries(empty_plan, "Tuvalu had many seabirds.")
    assert len(queries) >= 3
    assert all(q.strip() for q in queries)
    assert len(set(queries)) == len(queries)


def test_build_search_queries_bounded_length():
    plan = dict(SYN_PLAN, web_query="x" * 500,
                entities=["y" * 200, "z" * 200])
    for q in build_search_queries(plan, "w" * 500):
        assert len(q) <= 300


# ---------------------------------------------------------------------------
# (b) primary-first fetch ordering
# ---------------------------------------------------------------------------

def test_rank_hits_primary_first_stable():
    hits = [
        {"url": "https://en.wikipedia.org/wiki/Chile", "title": "w"},
        {"url": "https://api.worldbank.org/v2/x", "title": "wb"},
        {"url": "https://example.com/blog", "title": "b"},
        {"url": "https://fred.stlouisfed.org/series/X", "title": "f"},
    ]
    ranked = rank_hits_primary_first(hits)
    assert [h["title"] for h in ranked] == ["wb", "f", "w", "b"]
    assert hits[0]["title"] == "w"  # input untouched


def test_rank_hits_all_secondary_keeps_order():
    hits = [{"url": f"https://example.com/{i}"} for i in range(3)]
    assert rank_hits_primary_first(hits) == hits
    assert rank_hits_primary_first([]) == []


# ---------------------------------------------------------------------------
# (c) stdlib HTML table reading
# ---------------------------------------------------------------------------

def test_tables_from_html_two_tables():
    tables = tables_from_html(SYN_HTML_TABLE)
    assert len(tables) == 2
    assert tables[0][0] == ["Measure", "September", "Unit"]
    assert ["Unemployment rate", "4.2", "percent"] in tables[0]
    assert ["Unemployed persons", "7.1", "million"] in tables[0]
    assert tables[1] == [["footnote", "seasonally adjusted"]]


def test_tables_from_html_no_tables_and_empty():
    assert tables_from_html("<html><body><p>no tables</p></body></html>") == []
    assert tables_from_html("") == []
    assert tables_from_html("<table><tr><td>a</td><td>b</td></tr></table>") == [
        [["a", "b"]]]


def test_tables_from_html_never_raises_on_malformed():
    tables = tables_from_html("<table><tr><td>a")
    assert isinstance(tables, list)  # truncated markup: best effort, no crash


def test_doc_excerpts_surface_table_figures():
    excerpt = doc_excerpts(SYN_HTML_TABLE)
    assert "4.2" in excerpt
    assert "7.1" in excerpt
    assert "TABLE" in excerpt


def test_doc_excerpts_prose_unchanged():
    prose = ("<html><body><p>The Andean nation reached 12,500 people "
             "in 2024 according to the latest census estimates published "
             "this week for all residents.</p></body></html>")
    assert "12,500" in doc_excerpts(prose)


# ---------------------------------------------------------------------------
# (d) stdlib-minimal PDF text reading
# ---------------------------------------------------------------------------

def _uncompressed_pdf(body_text):
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>endobj\n"
        b"4 0 obj<</Length 60>>stream\nBT /F1 12 Tf 72 720 Td "
        + b"(" + body_text + b") Tj ET\nendstream\nendobj\n"
        b"trailer<</Root 1 0 R>>"
    )


def test_is_pdf_body_by_magic_and_content_type():
    raw = _uncompressed_pdf(b"4.2 percent")
    assert is_pdf_body("application/pdf", raw) is True
    assert is_pdf_body("application/pdf; charset=utf-8", b"junk") is True
    assert is_pdf_body("text/html", raw) is True  # magic wins
    assert is_pdf_body("text/html", b"<html></html>") is False
    assert is_pdf_body("", b"") is False


def test_extract_pdf_text_uncompressed():
    raw = _uncompressed_pdf(b"Unemployment was 4.2 percent")
    text = extract_pdf_text(raw)
    assert "4.2" in text
    assert "Unemployment" in text


def test_extract_pdf_text_flate():
    stream = zlib.compress(b"BT /F1 12 Tf (Growth was 3.1 percent) Tj ET")
    raw = (b"%PDF-1.4\n1 0 obj<</Length " + str(len(stream)).encode()
           + b"/Filter/FlateDecode>>stream\n" + stream
           + b"\nendstream\nendobj\ntrailer<</Root 1 0 R>>")
    text = extract_pdf_text(raw)
    assert "3.1" in text


def test_extract_pdf_text_non_pdf_empty():
    assert extract_pdf_text(b"<html>not a pdf</html>") == ""
    assert extract_pdf_text(b"") == ""


# ---------------------------------------------------------------------------
# (e) deterministic WB row rescue (structured-unit rescue)
# ---------------------------------------------------------------------------

def test_wb_row_quote_compact_separators():
    quote = wb_row_quote_for(SYN_WB_BODY, 2024)
    assert quote is not None
    assert quote in SYN_WB_BODY  # verbatim by construction
    assert '"2024"' in quote and "12500" in quote


def test_wb_row_quote_pretty_and_float():
    body = ('[{"page": 1}, [{"indicator": {"id": "NY.GDP.MKTP.CD"}, '
            '"date": "2024", "value": 1725671652742.19}]]')
    quote = wb_row_quote_for(body, 2024)
    assert quote is not None and quote in body
    assert "1725671652742.19" in quote


def test_wb_row_quote_year_mismatch_and_null():
    assert wb_row_quote_for(SYN_WB_BODY, 2023) is None
    null_body = SYN_WB_BODY.replace('"value":12500', '"value":null')
    assert wb_row_quote_for(null_body, 2024) is None
    assert wb_row_quote_for("not json", 2024) is None
    assert wb_row_quote_for("", 2024) is None


def test_wb_fallback_eligible_happy_path():
    assert wb_fallback_eligible(SYN_TEXT, dict(SYN_PLAN)) is True


def test_wb_fallback_eligible_rejects_mismatch_and_computed():
    # model iso3 disagrees with the resolver -> skip (wrong-country guard)
    assert wb_fallback_eligible(SYN_TEXT, dict(SYN_PLAN, iso3="TUV")) is False
    # unmapped indicator (per-capita/growth) -> skip
    plan = dict(SYN_PLAN, indicator="NY.GDP.PCAP.CD")
    assert wb_fallback_eligible(SYN_TEXT, plan) is False
    # multi-source arithmetic -> skip (component rows cannot decide it)
    avg_text = ("The average population of Chile and Tuvalu in 2024 was "
                "11,000 people, according to the World Bank.")
    avg_plan = dict(SYN_PLAN, entities=["Chile", "Tuvalu"])
    assert wb_fallback_eligible(avg_text, avg_plan) is False
    # no indicator / no iso3 / no year -> skip
    assert wb_fallback_eligible(SYN_TEXT, dict(SYN_PLAN, indicator=None)) is False
    assert wb_fallback_eligible(SYN_TEXT, dict(SYN_PLAN, iso3=None)) is False
    assert wb_fallback_eligible(SYN_TEXT, dict(SYN_PLAN, year=None)) is False


def test_wb_fallback_decides_false_claim_offline():
    """Synthetic FALSE WB claim: rescued row package -> contradicted."""
    claim = _claim("Chile had 99 million people in 2024.", "syn-false")
    assert claim.unit == "count"
    quote = wb_row_quote_for(SYN_WB_BODY, 2024)
    pkg = _pkg("syn-false", SYN_WB_URL, SYN_WB_BODY, quote,
               source_meta=SourceMeta("SP.POP.TOTL", "count", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: SYN_WB_BODY)
    assert verdict.verdict == "contradicted", verdict
    assert verdict.reasons == ["numeric_mismatch"]


def test_wb_fallback_decides_true_claim_offline():
    claim = _claim(SYN_TEXT, "syn-true")
    quote = wb_row_quote_for(SYN_WB_BODY, 2024)
    pkg = _pkg("syn-true", SYN_WB_URL, SYN_WB_BODY, quote,
               source_meta=SourceMeta("SP.POP.TOTL", "count", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: SYN_WB_BODY)
    assert verdict.verdict == "supported", verdict


def test_build_package_code_extractor_attaches_meta():
    quote = wb_row_quote_for(SYN_WB_BODY, 2024)
    pkg = build_package("syn-1", SYN_WB_URL, quote,
                        _doc(SYN_WB_URL, SYN_WB_BODY), 2024, 0,
                        extractor="code")
    assert pkg is not None
    assert pkg["extractor"] == "code"
    assert pkg["source_meta"] == {"indicator": "SP.POP.TOTL",
                                  "unit": "count", "scale": 1.0}
    EvidencePackage.from_dict(pkg)  # M2a accepts the code-built dict


# ---------------------------------------------------------------------------
# (f) multi-source arithmetic guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "The average GDP of Banaba and Chile in 2024 was 9 trillion dollars.",
    "Banaba GDP grew 5.3 percent from 2023 to 2024.",
    "In 2024, Banaba GDP was 150 percent of Chile GDP.",
    "Banaba GDP growth of 5 percent in 2024 outpaced its peers.",
    "The mean income across the islands rose 4 percent from 2020 to 2024.",
])
def test_is_multi_source_arithmetic_true(text):
    assert is_multi_source_arithmetic(text) is True


@pytest.mark.parametrize("text", [
    "Banaba GDP doubled to 800 billion dollars in 2024.",
    "Banaba GDP grew to 600 billion dollars in 2024.",
    "The jobless rate fell to 3.1 percent in September.",
    "Chile had 12,500 people in 2024.",
    "Sound travels at 343 meters per second.",
])
def test_is_multi_source_arithmetic_false(text):
    assert is_multi_source_arithmetic(text) is False


def _gdp_body(value, date="2024", country="Banaba", iso3="BUB"):
    return (
        '[{"page":1}, '
        f'[{{"indicator":{{"id":"NY.GDP.MKTP.CD"}},'
        f'"country":{{"value":"{country}"}},'
        f'"countryiso3code":"{iso3}","date":"{date}","value":{value}}}]]'
    )


def test_arithmetic_component_mismatch_abstains():
    """Average claim vs one component row: unverifiable, never contradicted."""
    claim = _claim("The average GDP of Banaba and Chile in 2024 was "
                   "9 trillion dollars.", "syn-avg")
    body = _gdp_body(1725671652742.19)
    quote = wb_row_quote_for(body, 2024)
    pkg = _pkg("syn-avg", "https://api.worldbank.org/v2/country/BUB/x",
               body, quote,
               source_meta=SourceMeta("NY.GDP.MKTP.CD", "USD", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: body)
    assert verdict.verdict == "unverifiable", verdict
    assert "arithmetic_needs_computation" in verdict.reasons


def test_arithmetic_direct_prose_refutation_still_contradicts():
    """A prose quote that states the computed figure can still refute."""
    claim = _claim("Banaba GDP grew 12.4 percent from 2023 to 2024.",
                   "syn-grow")
    assert claim.unit == "percent"
    snap = ("<html><body><p>Banaba GDP grew 7.4 percent from 2023 to 2024, "
            "the statistics office said.</p></body></html>")
    quote = "Banaba GDP grew 7.4 percent from 2023 to 2024"
    pkg = _pkg("syn-grow", "https://example.com/gdp", snap, quote,
               source_type="secondary", pid="pkg_prose")
    verdict = verify_claim(claim, [pkg], lambda p: snap)
    assert verdict.verdict == "contradicted", verdict
    assert verdict.reasons == ["numeric_mismatch"]


def test_arithmetic_match_still_supported():
    claim = _claim("The average GDP of Banaba and Chile in 2024 was "
                   "1.7 trillion dollars.", "syn-avg2")
    body = _gdp_body(1725671652742.19)  # 1.726T vs 1.7T: 1.5% off
    quote = wb_row_quote_for(body, 2024)
    pkg = _pkg("syn-avg2", "https://api.worldbank.org/v2/country/BUB/x",
               body, quote,
               source_meta=SourceMeta("NY.GDP.MKTP.CD", "USD", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: body)
    assert verdict.verdict == "supported", verdict


def test_non_arithmetic_mismatch_still_contradicted():
    """The guard only fires on arithmetic claims; plain refutations stand."""
    claim = _claim("Chile had 99 million people in 2024.", "syn-plain")
    assert is_multi_source_arithmetic(claim.text) is False
    quote = wb_row_quote_for(SYN_WB_BODY, 2024)
    pkg = _pkg("syn-plain", SYN_WB_URL, SYN_WB_BODY, quote,
               source_meta=SourceMeta("SP.POP.TOTL", "count", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: SYN_WB_BODY)
    assert verdict.verdict == "contradicted", verdict


def test_level_assertion_with_to_still_contradicted():
    """'Doubled/grew/fell TO x' asserts a level: directly comparable."""
    claim = _claim("Banaba GDP doubled to 800 billion dollars in 2024.",
                   "syn-level")
    body = _gdp_body(420504033143.0)
    quote = wb_row_quote_for(body, 2024)
    pkg = _pkg("syn-level", "https://api.worldbank.org/v2/country/BUB/x",
               body, quote,
               source_meta=SourceMeta("NY.GDP.MKTP.CD", "USD", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: body)
    assert verdict.verdict == "contradicted", verdict


# ---------------------------------------------------------------------------
# (g) RECUPERADOR refuting-quote instruction pin
# ---------------------------------------------------------------------------

def test_recuperador_system_allows_refuting_quotes():
    lowered = RECUPERADOR_SYSTEM.lower()
    assert "contradict" in lowered
    assert "findings" in lowered and "[]" in RECUPERADOR_SYSTEM


# ---------------------------------------------------------------------------
# guards that must NEVER move (through the new code paths)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (101.9, "supported"),      # 1.9% off: inside the 2% tolerance
    (102.1, "contradicted"),   # 2.1% off: outside -> real contradiction
])
def test_tolerance_intact_for_rescued_rows(value, expected):
    claim = _claim("The trust fund holds 100 dollars in reserve.", "syn-tol")
    body = _gdp_body(value)
    quote = wb_row_quote_for(body, 2024)
    assert quote is not None
    pkg = _pkg("syn-tol", "https://api.worldbank.org/v2/country/BUB/x",
               body, quote,
               source_meta=SourceMeta("NY.GDP.MKTP.CD", "USD", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: body)
    assert verdict.verdict == expected, verdict


def test_scale_backstop_intact_for_rescued_rows():
    from live.verifier.check import ClaimInput

    claim = ClaimInput(claim_id="syn-scale", text="GDP was 29.3 dollars.",
                       value=29.3, unit="USD", raw="29.3", claim_year=None)
    assert is_multi_source_arithmetic(claim.text) is False
    body = _gdp_body(29298013000000)
    quote = wb_row_quote_for(body, 2024)
    pkg = _pkg("syn-scale", "https://api.worldbank.org/v2/country/BUB/x",
               body, quote,
               source_meta=SourceMeta("NY.GDP.MKTP.CD", "USD", 1.0))
    verdict = verify_claim(claim, [pkg], lambda p: body)
    assert verdict.verdict == "unverifiable", verdict
    assert "possible_scale_error" in verdict.reasons


def test_hierarchy_intact_with_code_package():
    """Usable primary (contradict) still outranks secondary (support)."""
    claim = _claim(SYN_TEXT, "syn-hier")
    prim_body = SYN_WB_BODY.replace('"value":12500', '"value":50')
    prim_quote = wb_row_quote_for(prim_body, 2024)
    prim = _pkg("syn-hier", SYN_WB_URL, prim_body, prim_quote,
                source_type="primary",
                source_meta=SourceMeta("SP.POP.TOTL", "count", 1.0),
                pid="pkg_prim")
    sec_body = ("<html><body><p>Chile had 12,500 people in 2024 according "
                "to the census.</p></body></html>")
    sec_quote = "Chile had 12,500 people in 2024"
    sec = _pkg("syn-hier", "https://example.com/census", sec_body, sec_quote,
               source_type="secondary", pid="pkg_sec")
    snaps = {"pkg_prim": prim_body, "pkg_sec": sec_body}
    verdict = verify_claim(claim, [prim, sec], lambda p: snaps[p.package_id])
    assert verdict.verdict == "contradicted", verdict
    assert verdict.details.get("hierarchy") == "primary_beats_secondary"
    assert verdict.package_ids == ["pkg_prim"]
