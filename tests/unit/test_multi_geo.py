"""Multi-geo compound hole (V1): detection + disclosure + cap (offline).

`resolve_country` returns the LONGEST country name only, so retrieval is
single-geo: "Unemployment in Germany was 3.1% ... and unemployment in
France was 7.3% ..." compared BOTH numbers against GERMAN evidence with
no disclosure (both clauses digit-bearing defeats both H5 signals).
Fix: disclose via `multi_geo_unchecked` (major) and cap supported at
partially_supported through the compound-cap path. Per-clause splitting
is out of scope.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.connectors.countries import resolve_all_countries  # noqa: E402
from atlas.core.types import (  # noqa: E402
    Comparison,
    Evidence,
    Quantity,
    StructuredClaim,
    TimeRef,
    Uncertainty,
)
from atlas.pipeline.synthesize import decide_stance  # noqa: E402
from atlas.pipeline.verify import detect_claim_shape, verify_quantitative  # noqa: E402

BUG_TEXT = (
    "Unemployment in Germany was 3.1% in 2023 and "
    "unemployment in France was 7.3% in 2023."
)
GERMANY_ONLY = "Unemployment in Germany was 3.1% in 2023."
GERMANY_TWICE = "Unemployment in Germany was 3.1% in 2023 and Germany added jobs."


def _pct(raw: str, value: float) -> Quantity:
    return Quantity(raw_text=raw, value=value, unit="percent",
                    normalized_unit="percent")


def _claim(text: str, quantities) -> StructuredClaim:
    return StructuredClaim(
        raw_text=text,
        domain="economy",
        claim_type="quantitative",
        quantities=list(quantities),
        time_refs=[TimeRef(raw="2023", year=2023, period_type="year")],
    )


def _deu_evidence(claim_id: str) -> Evidence:
    return Evidence(
        claim_id=claim_id,
        connector="worldbank",
        source_name="World Bank Open Data",
        source_kind="secondary",
        evidence_kind="direct",
        locator="SL.UEM.TOTL.ZS DEU 2023",
        excerpt="Unemployment, total (% of labor force) for Germany in 2023: 3.071",
        payload={"label": "Unemployment, total (% of labor force)",
                 "unit": "percent", "iso3": "DEU", "year": "2023",
                 "value": 3.071},
    )


def _verifying(verdict: str, cid: str) -> Comparison:
    return Comparison(claim_id="clm_t", metric="Unemployment, total",
                      verdict=verdict, comparison_id=cid)  # type: ignore[arg-type]


def _multi_geo() -> Uncertainty:
    return Uncertainty(code="multi_geo_unchecked", description="multi-geo flag",
                       severity="major", related_ids=["clm_t"])  # type: ignore[arg-type]


# --- resolve_all_countries ---


def test_resolve_all_two_geos():
    assert resolve_all_countries(BUG_TEXT) == ["DEU", "FRA"]


def test_resolve_all_single_geo():
    assert resolve_all_countries(GERMANY_ONLY) == ["DEU"]


def test_resolve_all_dedupes_duplicate_mentions():
    assert resolve_all_countries(GERMANY_TWICE) == ["DEU"]
    assert resolve_all_countries("Korea and South Korea") == ["KOR"]


def test_resolve_all_unknown_and_empty():
    assert resolve_all_countries("Atlantis") == []
    assert resolve_all_countries("") == []


# --- detection ---


def test_detect_two_geos_flagged():
    claim = _claim(BUG_TEXT, [_pct("3.1%", 3.1), _pct("7.3%", 7.3)])
    flags = detect_claim_shape(claim)
    hit = [u for u in flags if u.code == "multi_geo_unchecked"]
    assert len(hit) == 1
    assert hit[0].severity == "major"
    assert "one geography only" in hit[0].description
    assert "unchecked" in hit[0].description


def test_detect_single_geo_clean():
    claim = _claim(GERMANY_ONLY, [_pct("3.1%", 3.1)])
    flags = detect_claim_shape(claim)
    assert not any(u.code == "multi_geo_unchecked" for u in flags)


def test_detect_duplicate_mentions_deduped():
    claim = _claim(GERMANY_TWICE, [_pct("3.1%", 3.1)])
    flags = detect_claim_shape(claim)
    assert not any(u.code == "multi_geo_unchecked" for u in flags)


def test_verify_quantitative_carries_flag_with_constructed_evidence():
    claim = _claim(BUG_TEXT, [_pct("3.1%", 3.1), _pct("7.3%", 7.3)])
    comparisons, uncertainties = verify_quantitative(
        claim, [_deu_evidence(claim.claim_id)])
    # Both quantities compared against the single-geo evidence, as live.
    numeric = [c for c in comparisons if c.metric != "time_compatibility"]
    assert {c.verdict for c in numeric} == {"consistent", "contradicts"}
    hit = [u for u in uncertainties if u.code == "multi_geo_unchecked"]
    assert len(hit) == 1
    assert hit[0].severity == "major"


def test_verify_quantitative_single_geo_clean():
    claim = _claim(GERMANY_ONLY, [_pct("3.1%", 3.1)])
    _, uncertainties = verify_quantitative(
        claim, [_deu_evidence(claim.claim_id)])
    assert not any(u.code == "multi_geo_unchecked" for u in uncertainties)


# --- cap (existing cap semantics: caps bind supported only) ---


def test_cap_would_be_supported_capped_at_partial():
    # Full coverage (2 verifying for 2 quantities): the compound coverage
    # check alone would NOT cap here, but single-geo evidence for a
    # two-geo claim always overclaims, so multi_geo caps unconditionally.
    claim = _claim(BUG_TEXT, [_pct("3.1%", 3.1), _pct("7.3%", 7.3)])
    stance = decide_stance(
        [_verifying("consistent", "cmp_a"), _verifying("consistent", "cmp_b")],
        [_multi_geo()],
        claim,
    )
    assert stance == "partially_supported"


def test_contradicted_stays_contradicted_with_disclosure():
    # Per existing cap semantics contradiction dominates every cap
    # (precision, compound, blocking); the multi_geo flag discloses on
    # this path instead of capping.
    claim = _claim(BUG_TEXT, [_pct("3.1%", 3.1), _pct("7.3%", 7.3)])
    stance = decide_stance([_verifying("contradicts", "cmp_a")],
                           [_multi_geo()], claim)
    assert stance == "contradicted"


def test_resolve_all_overlapping_names_claim_span_once():
    # Lead fix: "uk" is a substring of "ukraine" — longest-match-first
    # claims the span, so single-geo Ukraine must not flag multi-geo.
    assert resolve_all_countries("Ukraine") == ["UKR"]
    assert resolve_all_countries(
        "The GDP of Ukraine was $100 billion in 2023.") == ["UKR"]


def test_resolve_all_overlap_prone_pair_when_both_named():
    # Both spans present and non-overlapping -> genuine two-geo hit.
    assert resolve_all_countries(
        "Ukraine's trade with the UK grew in 2023.") == ["UKR", "GBR"]


def test_detect_ukraine_single_geo_clean():
    claim = _claim("The GDP of Ukraine was $100 billion in 2023.",
                   [_pct("x", 1.0)])
    flags = detect_claim_shape(claim)
    assert not any(u.code == "multi_geo_unchecked" for u in flags)


def test_bare_us_resolves_and_flags_multi_geo():
    # HIGH-2: bare 'US' was unresolvable, so China+US claims concluded
    # without any multi_geo disclosure.
    assert resolve_all_countries(
        "The populations of China and the US were 1.4 billion "
        "and 330 million in 2023.") == ["CHN", "USA"]


def test_short_codes_need_standalone_uppercase():
    # 'us' pronoun, 'usage', Spanish 'usa' verb, and 'euro' must not
    # resolve; sloppy-lowercase codes degrade to geo-less (honest).
    assert "USA" not in resolve_all_countries("The data told us little.")
    assert resolve_all_countries("GDP usage grew 5% in 2023.") == []
    assert resolve_all_countries("El desempleo usa cifras de 2023.") == []
    assert "EUU" not in resolve_all_countries("The euro fell in France.")
    assert resolve_all_countries("Travel to usa was cheap.") == []


def test_dotted_and_spanish_usa_forms_still_resolve():
    assert resolve_all_countries("U.S. GDP was $27tn in 2023.") == ["USA"]
    assert resolve_all_countries("El PIB de EE. UU. fue alto.") == ["USA"]


def test_geo_word_boundaries_no_substring_phantoms():
    # P0b: substring matching resolved countries inside other words.
    from atlas.connectors.countries import resolve_country

    for text in ("Indiana", "I visited Latin America last year",
                 "otherworldly", "inmundo", "turkey", "turkeys",
                 "American jobs report"):
        assert resolve_country(text) == (None, None), text


def test_geo_new_keys_and_case_rule():
    # P0b: North Korea was KOR (South!), Northern Ireland was IRL.
    from atlas.connectors.countries import resolve_country

    assert resolve_country("North Korea")[1] == "PRK"
    assert resolve_country("corea del norte")[1] == "PRK"
    assert resolve_country("Northern Ireland")[1] == "GBR"
    assert resolve_country("irlanda del norte")[1] == "GBR"
    assert resolve_country("Turkey's GDP grew 5%")[1] == "TUR"
    assert resolve_country("TURKEY UNEMPLOYMENT WAS 9%")[1] == "TUR"
    assert resolve_country("turquía")[1] == "TUR"


def test_geo_regressions_still_resolve():
    from atlas.connectors.countries import resolve_country

    for text, want in (("United States", "USA"), ("US", "USA"),
                       ("Mexico", "MEX"), ("world", "WLD"), ("mundo", "WLD"),
                       ("South Korea", "KOR"), ("Ireland", "IRL"),
                       ("America's GDP", "USA"), ("U.S. jobs", "USA")):
        assert resolve_country(text)[1] == want, text


def test_geo_all_countries_boundaries():
    assert resolve_all_countries("Indiana and Latin America") == []
    assert resolve_all_countries("North Korea and South Korea") == ["PRK", "KOR"]


def test_geo_region_guard_whitespace_variants():
    # UT-b iter2 (judge): hyphen/tabs/doubles must not leak USA.
    from atlas.connectors.countries import resolve_country

    for text in ("Latin-America", "Latin  America", "Latin\tAmerica",
                 "Latin\nAmerica", "CentralAmerica", "latinamerica",
                 "South China Sea", "China Sea", "Indo-China",
                 "Turkey is delicious", "Turkey"):
        assert resolve_country(text) == (None, None), text


def test_geo_tie_break_is_first_mentioned():
    # UT-b iter2 (judge): equal-length matches prefer earliest span.
    from atlas.connectors.countries import resolve_country

    assert resolve_country("South Korea and North Korea")[1] == "KOR"
    assert resolve_country("North Korea and South Korea")[1] == "PRK"


def test_geo_america_synonyms_and_slash():
    # UT-b iter3 (judge): synonym regions + slash variants leak USA.
    from atlas.connectors.countries import resolve_country

    for text in ("Latinx America", "Latino America", "Hispanic America",
                 "Ibero-America", "Pan America", "Pan-America",
                 "Inter-America", "Afro-America", "Anglo-America",
                 "Latin/America"):
        assert resolve_country(text) == (None, None), text
    assert resolve_country("China seaside resorts")[1] == "CHN"


def test_geo_turkey_smuggling_blocked():
    # UT-b iter3 (judge): clause-local context + bird veto.
    from atlas.connectors.countries import (resolve_all_countries,
                                            resolve_country)

    for text in ("Turkey is delicious and GDP grew",
                 "Turkey is a bird.",
                 "We ate Turkey on Thanksgiving, up 5% from last year",
                 "Turkey sandwich 20% off today",
                 "Turkey Trot raises $50k for charity",
                 "Wild Turkey bourbon 40% alcohol",
                 "Turkey population of wild birds grew",
                 "Turkey costs $20 at the store"):
        assert resolve_country(text) == (None, None), text
    assert resolve_all_countries(
        "Turkey is a bird. Germany's GDP grew 5%") == ["DEU"]
    for text in ("Turkey's economy grew", "Turkey's lira crashed",
                 "Turkey's central bank raised rates", "Turkey prices rose"):
        assert resolve_country(text)[1] == "TUR", text
