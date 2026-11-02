"""V5-2B Atlas Live: units verifier — WB indicator map + general EN/ES parser.

Cause measured in V5-EN (live/V5_EN.md §2): ~40 EN claims abstain on
units — class A36 (WB row cited, quote without units, indicator outside
the 2 curated entries -> unit_mismatch) and class B13 (claim unit the
parser does not know -> claim_unit_unknown).

Three GENERAL pieces (no per-claim rules):

(a) ``WB_INDICATOR_UNITS`` +12 mappings, each verified against the WB
    API indicator metadata (name pins one unambiguous UNIT_VOCAB unit;
    ambiguous indicators — PPP international $, composite t CO2e/capita
    — are deliberately OUT, pinned below).
(b) General EN/ES unit parser extension (src/atlas/core/units.py): kwh
    (+ megawatt x1000), km2 vs m2 (DELIBERATELY distinct), meters
    (+ bare lowercase "m", ES "metros"), ES counts (desempleados),
    ES/spaced speed mirrors. Unknown stays unknown (tonnes, miles,
    ppm, bare scale, bare "degrees", word-numbers) — never guessed.
(c) Rule: cited-but-unitless WB quote + known claim unit + mapped
    indicator -> decide via the existing source_meta fallback (M2d);
    plus a general monthly-frequency guard (monthly claim text x
    annual-rate indicator -> abstain, P1-36 class).

All tests offline: synthetic snapshots, no network, no model calls.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.evidence.package import (  # noqa: E402
    UNIT_VOCAB,
    WB_ANNUAL_RATE_INDICATORS,
    WB_INDICATOR_UNITS,
    EvidencePackage,
    claim_has_monthly_frequency,
    sha256_of_text,
    source_meta_for_snapshot,
)
from live.run_verify import claim_input_for  # noqa: E402
from live.verifier.arithmetic import extract_operands, needs_computation  # noqa: E402
from live.verifier.check import verify_claim  # noqa: E402
from src.atlas.core.compare import is_year_quantity  # noqa: E402
from src.atlas.core.units import (  # noqa: E402
    normalize_unit,
    parse_quantities,
    units_compatible,
)

WB_URL = "https://api.worldbank.org/v2/country/USA/indicator/%s?format=json"
WIKI_URL = "https://en.wikipedia.org/wiki/Speed_of_light"


def _wb_snapshot(indicator, iso3="USA", date="2024", value="2.9"):
    """Minimal WB API JSON snapshot (hand-written literals, exact quotes)."""
    row = ('{"indicator":{"id":"%s"},"country":{"id":"X","value":"Y"},'
           '"countryiso3code":"%s","date":"%s","value":%s}'
           % (indicator, iso3, date, value))
    return '[{"page":1},[%s]]' % row


def _wb_package(pid, cid, indicator, snapshot, year, iso3="USA",
                date="2024", value="2.9"):
    quote = ('"countryiso3code":"%s","date":"%s","value":%s'
             % (iso3, date, value))
    assert quote in snapshot
    start = snapshot.find(quote)
    meta = source_meta_for_snapshot(WB_URL % indicator, snapshot)
    assert meta is not None, indicator
    return EvidencePackage(
        package_id=pid, claim_id=cid, url=WB_URL % indicator,
        captured_at="2026-11-02T00:00:00+00:00",
        sha256=sha256_of_text(snapshot),
        snapshot_path="data/snap/v52b-test.html", quote=quote,
        offsets=(start, start + len(quote)), source_type="primary",
        extractor="code", source_year=year,
        source_meta=meta)


def _verify(text, packages, snapshots, cid="T-01"):
    claim = claim_input_for({"id": cid, "text": text})
    by_sha = {sha256_of_text(s): s for s in snapshots}
    loader = lambda p: by_sha.get(p.sha256)  # noqa: E731
    return claim, verify_claim(claim, packages, loader)


# ---------------------------------------------------------------------------
# (a) WB_INDICATOR_UNITS: mapping pins (+ WB metadata names cited)
# ---------------------------------------------------------------------------

# indicator -> (canonical unit, WB API metadata name, 2026-11-02).
_V52B_MAPPINGS = {
    "FP.CPI.TOTL.ZG": ("percent", "Inflation, consumer prices (annual %)"),
    "SL.UEM.TOTL.ZS": ("percent",
                       "Unemployment, total (% of total labor force)"
                       " (modeled ILO estimate)"),
    "NY.GDP.MKTP.KD.ZG": ("percent", "GDP growth (annual %)"),
    "SP.POP.GROW": ("percent", "Population growth (annual %)"),
    "EG.ELC.ACCS.ZS": ("percent", "Access to electricity (% of population)"),
    "AG.LND.FRST.ZS": ("percent", "Forest area (% of land area)"),
    "SP.URB.TOTL.IN.ZS": ("percent",
                          "Urban population (% of total population)"),
    "IT.NET.USER.ZS": ("percent",
                      "Individuals using the Internet (% of population)"),
    "SP.DYN.LE00.IN": ("years", "Life expectancy at birth, total (years)"),
    "SP.URB.TOTL": ("count", "Urban population"),
    "AG.LND.TOTL.K2": ("km2", "Land area (sq. km)"),
    "EG.USE.ELEC.KH.PC": ("kwh", "Electric power consumption (kWh per capita)"),
}


class TestWBIndicatorUnits:
    def test_preexisting_entries_intact(self):
        assert WB_INDICATOR_UNITS["SP.POP.TOTL"] == "count"
        assert WB_INDICATOR_UNITS["NY.GDP.MKTP.CD"] == "USD"

    @pytest.mark.parametrize("code", sorted(_V52B_MAPPINGS))
    def test_v52b_mapping(self, code):
        unit, _name = _V52B_MAPPINGS[code]
        assert WB_INDICATOR_UNITS[code] == unit

    def test_all_units_in_vocab(self):
        for code, unit in WB_INDICATOR_UNITS.items():
            assert unit in UNIT_VOCAB, code

    def test_new_canonical_units_in_vocab(self):
        assert UNIT_VOCAB >= {"kwh", "km2", "m2", "meters"}

    def test_ambiguous_indicators_excluded(self):
        # PPP international $ is not USD; t CO2e/capita has no canonical
        # unit in scope (see package.py policy note).
        assert "NY.GDP.MKTP.PP.CD" not in WB_INDICATOR_UNITS
        assert "EN.GHG.ALL.PC.CE.AR5" not in WB_INDICATOR_UNITS

    def test_excluded_snapshot_yields_no_meta(self):
        snap = _wb_snapshot("NY.GDP.MKTP.PP.CD", value="41259823532528")
        assert source_meta_for_snapshot(WB_URL % "NY.GDP.MKTP.PP.CD",
                                        snap) is None

    def test_annual_rate_set_pinned(self):
        assert WB_ANNUAL_RATE_INDICATORS == frozenset({
            "FP.CPI.TOTL.ZG", "NY.GDP.MKTP.KD.ZG", "SP.POP.GROW"})
        for code in WB_ANNUAL_RATE_INDICATORS:
            assert WB_INDICATOR_UNITS[code] == "percent"


class TestSourceMetaForSnapshot:
    @pytest.mark.parametrize("code", sorted(_V52B_MAPPINGS))
    def test_meta_unit_per_mapping(self, code):
        unit, _name = _V52B_MAPPINGS[code]
        snap = _wb_snapshot(code, value="12.5")
        meta = source_meta_for_snapshot(WB_URL % code, snap)
        assert meta is not None
        assert meta.indicator == code
        assert meta.unit == unit
        assert meta.scale == 1.0

    def test_mixed_indicators_is_none(self):
        snap = ('[{"page":1},[{"indicator":{"id":"FP.CPI.TOTL.ZG"}},'
                '{"indicator":{"id":"SL.UEM.TOTL.ZS"}}]]')
        assert source_meta_for_snapshot(WB_URL % "FP.CPI.TOTL.ZG",
                                        snap) is None

    def test_prose_and_garbage_are_none(self):
        assert source_meta_for_snapshot(WB_URL % "FP.CPI.TOTL.ZG",
                                        "inflation was 2.9 percent") is None
        assert source_meta_for_snapshot(WB_URL % "FP.CPI.TOTL.ZG",
                                        "not json {[") is None
        assert source_meta_for_snapshot(WB_URL % "FP.CPI.TOTL.ZG", "") is None

    def test_non_wb_host_is_none(self):
        snap = _wb_snapshot("FP.CPI.TOTL.ZG", value="2.9")
        assert source_meta_for_snapshot(WIKI_URL, snap) is None
        assert source_meta_for_snapshot("", snap) is None
        assert source_meta_for_snapshot(None, snap) is None  # type: ignore[arg-type]

    def test_none_inputs_are_none(self):
        # Pre-existing behavior: None/"" degrade to None (the producer
        # additionally guards the call; non-string truthy input still
        # raises inside urlsplit — untouched pre-existing edge).
        assert source_meta_for_snapshot(None, None) is None  # type: ignore[arg-type]
        assert source_meta_for_snapshot(WB_URL % "FP.CPI.TOTL.ZG", None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# (b) General EN/ES parser: kwh / km2 / m2 / meters / counts / speeds
# ---------------------------------------------------------------------------

def _first_unit(text):
    qs = [q for q in parse_quantities(text, label="claim")
          if not is_year_quantity(q)]
    assert qs, text
    return qs[0].value, qs[0].normalized_unit


class TestParserKwh:
    @pytest.mark.parametrize("form", [
        "kilowatt hour", "kilowatt hours", "kilowatt-hour",
        "kilowatt-hours", "kwh", "kWh", "KWH",
    ])
    def test_kilowatt_forms(self, form):
        assert _first_unit("used 141 %s per person" % form) == (141.0, "kwh")

    @pytest.mark.parametrize("form", [
        "megawatt hour", "megawatt hours", "megawatt-hour",
        "megawatt-hours", "mwh", "MWh",
    ])
    def test_megawatt_scales_x1000(self, form):
        # 140 MWh reads as 140,000 kwh — never 140 kwh.
        assert _first_unit("used 140 %s per person" % form) == (140000.0, "kwh")

    def test_canonical_kwh(self):
        assert _first_unit("12,934 kWh per person") == (12934.0, "kwh")

    def test_per_person_qualifier_dropped(self):
        v, u = _first_unit(
            "Canada used 14,260 kilowatt hours of electricity per person"
            " in 2024.")
        assert (v, u) == (14260.0, "kwh")

    def test_megawatt_with_scale_word(self):
        assert _first_unit("1.5 thousand megawatt hours") == (1500000.0, "kwh")


class TestParserArea:
    @pytest.mark.parametrize("form", [
        "square kilometer", "square kilometers", "square kilometre",
        "square kilometres", "sq km", "sq. km", "km2",
    ])
    def test_km2_forms(self, form):
        assert _first_unit("spans 9.2 million %s" % form) == (9200000.0, "km2")

    def test_km2_superscript(self):
        assert _first_unit("spans 9.2 million km²") == (9200000.0, "km2")

    @pytest.mark.parametrize("form", [
        "kilómetro cuadrado", "kilómetros cuadrados",
        "kilometro cuadrado", "kilometros cuadrados",
    ])
    def test_km2_spanish(self, form):
        assert _first_unit("cubre 920000 %s" % form) == (920000.0, "km2")

    @pytest.mark.parametrize("form", [
        "square meter", "square meters", "square metre",
        "square metres", "sq m", "sq. m", "m2",
    ])
    def test_m2_forms(self, form):
        assert _first_unit("covers just 97,600 %s" % form) == (97600.0, "m2")

    def test_m2_superscript(self):
        assert _first_unit("covers just 97,600 m²") == (97600.0, "m2")

    @pytest.mark.parametrize("form", ["metro cuadrado", "metros cuadrados"])
    def test_m2_spanish(self, form):
        assert _first_unit("cubre 500 %s" % form) == (500.0, "m2")

    def test_km2_and_m2_incompatible(self):
        assert not units_compatible("km2", "m2")
        assert not units_compatible("m2", "km2")
        assert units_compatible("km2", "sq. km")
        assert units_compatible("m2", "square meters")

    def test_sq_miles_not_sq_meters(self):
        _v, u = _first_unit("spans 3,600,000 sq miles")
        assert u is None


class TestParserMeters:
    @pytest.mark.parametrize("form", [
        "meter", "meters", "metre", "metres", "metro", "metros",
    ])
    def test_meter_forms(self, form):
        assert _first_unit("rises 8,849 %s above sea level" % form) == (
            8849.0, "meters")

    def test_bare_lowercase_m(self):
        assert _first_unit("(8849 m MSL)") == (8849.0, "meters")
        assert _first_unit("100 m") == (100.0, "meters")

    def test_uppercase_m_stays_unknown(self):
        assert _first_unit("42 M")[1] is None

    def test_attached_m_still_million(self):
        v, u = _first_unit("$27m")
        assert v == 27000000.0 and u == "USD"


class TestParserCountsSpeeds:
    @pytest.mark.parametrize("form", [
        "desempleado", "desempleados", "desempleada", "desempleadas",
    ])
    def test_es_unemployed_counts(self, form):
        assert _first_unit("había 7,1 millones de %s" % form) == (
            7100000.0, "count")

    def test_es_meters_per_second(self):
        assert _first_unit(
            "La luz viaja a exactamente 299792458 metros por segundo.") == (
            299792458.0, "m/s")

    @pytest.mark.parametrize("form", [
        "kilometer per second", "kilometers per second",
        "kilometre per second", "kilometres per second",
    ])
    def test_spaced_km_per_second(self, form):
        assert _first_unit("light moves at 29,979 %s" % form) == (
            29979.0, "km/s")

    def test_preexisting_speeds_intact(self):
        assert _first_unit("299,792,458 m/s") == (299792458.0, "m/s")
        assert _first_unit("5 meters per second") == (5.0, "m/s")


class TestParserUnknownStaysUnknown:
    """Out-of-scope units abstain (unit None) — never guessed."""

    @pytest.mark.parametrize("text", [
        "averaged 21.7 tonnes of greenhouse gas per person",
        "6.36 kilotonnes per person",
        "3.1 megatonnes of carbon",
        "stretches 40,075 miles around",
        "384,400 kilometers from Earth",
        "400 parts per million",
        "2 ppm",
        "peak near 10.3 billion in 2086",
        "peak at 10.4 billion in 2086",
        "7 degrees at noon",
        "20 grados",
    ])
    def test_unknown_units(self, text):
        _v, u = _first_unit(text)
        assert u is None, text

    @pytest.mark.parametrize("text", [
        "Canada uses triple the electricity per person",
        "reaching zero percent",
    ])
    def test_word_numbers_have_no_quantity(self, text):
        qs = [q for q in parse_quantities(text, label="claim")
              if not is_year_quantity(q)]
        assert qs == []

    def test_normalize_none_never_compatible(self):
        assert not units_compatible(None, "kwh")
        assert not units_compatible("kwh", None)
        assert not units_compatible(None, None)
        assert units_compatible("kwh", "kilowatt hours")
        assert units_compatible("meters", "m")


# ---------------------------------------------------------------------------
# (c) Rule: unitless WB quote + known claim unit + mapped indicator -> decide
# ---------------------------------------------------------------------------

class TestQuotedRowDecides:
    def test_percent_supported(self):
        snap = _wb_snapshot("FP.CPI.TOTL.ZG", iso3="USA", date="2024",
                            value="2.94952520485207")
        pkg = _wb_package("p1", "T-01", "FP.CPI.TOTL.ZG", snap, 2024,
                          iso3="USA", date="2024",
                          value="2.94952520485207")
        claim, verdict = _verify(
            "Consumer prices in the United States rose 2.9 percent in 2024.",
            [pkg], [snap])
        assert claim.unit == "percent"
        assert verdict.verdict == "supported"
        assert verdict.reasons == ["numeric_match"]
        assert verdict.details["p1"]["unit_from"] == "source_meta"

    def test_percent_contradicted(self):
        snap = _wb_snapshot("FP.CPI.TOTL.ZG", iso3="CHN", date="2024",
                            value="0.218128938439177")
        pkg = _wb_package("p1", "T-01", "FP.CPI.TOTL.ZG", snap, 2024,
                          iso3="CHN", date="2024",
                          value="0.218128938439177")
        _claim, verdict = _verify(
            "Consumer prices in China rose 2.2 percent in 2024.",
            [pkg], [snap])
        assert verdict.verdict == "contradicted"
        assert verdict.reasons == ["numeric_mismatch"]

    def test_years_supported(self):
        snap = _wb_snapshot("SP.DYN.LE00.IN", iso3="JPN", date="2024",
                            value="84.0363414634146")
        pkg = _wb_package("p1", "T-01", "SP.DYN.LE00.IN", snap, 2024,
                          iso3="JPN", date="2024",
                          value="84.0363414634146")
        _claim, verdict = _verify(
            "Japan's life expectancy reached 84.0 years in 2024.",
            [pkg], [snap])
        assert verdict.verdict == "supported"

    def test_km2_supported(self):
        snap = _wb_snapshot("AG.LND.TOTL.K2", iso3="CHN", date="2024",
                            value="9388210")
        pkg = _wb_package("p1", "T-01", "AG.LND.TOTL.K2", snap, 2024,
                          iso3="CHN", date="2024", value="9388210")
        claim, verdict = _verify(
            "China's land area is 9.39 million square kilometers.",
            [pkg], [snap])
        assert claim.unit == "km2"
        assert verdict.verdict == "supported"

    def test_kwh_supported(self):
        snap = _wb_snapshot("EG.USE.ELEC.KH.PC", iso3="CAN", date="2024",
                            value="14260.1257432657")
        pkg = _wb_package("p1", "T-01", "EG.USE.ELEC.KH.PC", snap, 2024,
                          iso3="CAN", date="2024",
                          value="14260.1257432657")
        claim, verdict = _verify(
            "Canada used 14,260 kilowatt hours of electricity per person"
            " in 2024.",
            [pkg], [snap])
        assert claim.unit == "kwh"
        assert verdict.verdict == "supported"

    def test_megawatt_exact_1000x_abstains_on_backstop(self):
        # 140 MWh reads as 140,000 kwh vs a 140.56 kwh row: exactly the
        # 1000x gap the scale-slip backstop owns (claim raw carries no
        # scale word — units never enter raw_text), so the package
        # abstains with possible_scale_error instead of contradicting
        # (safe miss on a false claim; documents the boundary).
        snap = _wb_snapshot("EG.USE.ELEC.KH.PC", iso3="NGA", date="2024",
                            value="140.562460777052")
        pkg = _wb_package("p1", "T-01", "EG.USE.ELEC.KH.PC", snap, 2024,
                          iso3="NGA", date="2024",
                          value="140.562460777052")
        claim, verdict = _verify(
            "Nigeria used 140 megawatt hours per person.", [pkg], [snap])
        assert claim.value == 140000.0 and claim.unit == "kwh"
        assert verdict.verdict == "unverifiable"
        assert "possible_scale_error" in verdict.reasons

    def test_count_false_contradicted(self):
        snap = _wb_snapshot("SP.URB.TOTL", iso3="IND", date="2025",
                            value="522441248")
        pkg = _wb_package("p1", "T-01", "SP.URB.TOTL", snap, 2025,
                          iso3="IND", date="2025", value="522441248")
        _claim, verdict = _verify(
            "India's cities hold 35.4 billion people.", [pkg], [snap])
        assert verdict.verdict == "contradicted"

    def test_m2_vs_km2_abstains_despite_equal_figures(self):
        # 97,600 square meters vs a 97,600 sq-km row: identical figures,
        # incompatible units -> unverifiable, never supported (S2-12).
        snap = _wb_snapshot("AG.LND.TOTL.K2", iso3="KOR", date="2024",
                            value="97600")
        pkg = _wb_package("p1", "T-01", "AG.LND.TOTL.K2", snap, 2024,
                          iso3="KOR", date="2024", value="97600")
        claim, verdict = _verify(
            "South Korea covers just 97,600 square meters, a small campus.",
            [pkg], [snap])
        assert claim.unit == "m2"
        assert verdict.verdict == "unverifiable"
        assert "unit_mismatch" in verdict.reasons

    def test_unmapped_indicator_still_abstains(self):
        snap = _wb_snapshot("NY.GDP.MKTP.PP.CD", iso3="CHN", date="2025",
                            value="41259823532528")
        quote = ('"countryiso3code":"CHN","date":"2025",'
                 '"value":41259823532528')
        pkg = EvidencePackage(
            package_id="p1", claim_id="T-01",
            url=WB_URL % "NY.GDP.MKTP.PP.CD",
            captured_at="2026-11-02T00:00:00+00:00",
            sha256=sha256_of_text(snap),
            snapshot_path="data/snap/v52b-test.html", quote=quote,
            offsets=(snap.find(quote), snap.find(quote) + len(quote)),
            source_type="primary", extractor="code", source_year=2025,
            source_meta=None)
        _claim, verdict = _verify(
            "China's economy is now 156 percent larger than America's.",
            [pkg], [snap])
        assert verdict.verdict == "unverifiable"
        assert "unit_mismatch" in verdict.reasons

    def test_phantom_iso3_digit_loses_to_closer_row_value(self):
        # Every WB quote carries a phantom "3" (countryiso3code); the
        # closest-candidate rule must prefer the row value (documents
        # current M2d behavior — see live/V5_2B.md residual R3).
        snap = _wb_snapshot("SL.UEM.TOTL.ZS", iso3="JPN", date="2024",
                            value="2.5")
        pkg = _wb_package("p1", "T-01", "SL.UEM.TOTL.ZS", snap, 2024,
                          iso3="JPN", date="2024", value="2.5")
        _claim, verdict = _verify(
            "Japan's unemployment was 2.5 percent in 2024.", [pkg], [snap])
        assert verdict.verdict == "supported"
        assert verdict.details["p1"]["evidence_value"] == 2.5

    def test_prose_meters_supported_without_meta(self):
        snap = ("Height above mean sea level (8849 m MSL), or Mount "
                "Everest (8849 m).")
        quote = "(8849 m MSL)"
        pkg = EvidencePackage(
            package_id="p1", claim_id="T-01", url=WIKI_URL,
            captured_at="2026-11-02T00:00:00+00:00",
            sha256=sha256_of_text(snap),
            snapshot_path="data/snap/v52b-test.html", quote=quote,
            offsets=(snap.find(quote), snap.find(quote) + len(quote)),
            source_type="secondary", extractor="model",
            source_year=None, source_meta=None)
        _claim, verdict = _verify(
            "Mount Everest rises 8,849 meters above sea level.",
            [pkg], [snap])
        assert verdict.verdict == "supported"

    def test_prose_km2_supported_without_meta(self):
        snap = "The Sahara spans 9,200,000 square kilometres (3,600,000 sq mi)."
        quote = "9,200,000 square kilometres (3,600,000"
        pkg = EvidencePackage(
            package_id="p1", claim_id="T-01", url=WIKI_URL,
            captured_at="2026-11-02T00:00:00+00:00",
            sha256=sha256_of_text(snap),
            snapshot_path="data/snap/v52b-test.html", quote=quote,
            offsets=(snap.find(quote), snap.find(quote) + len(quote)),
            source_type="secondary", extractor="model",
            source_year=None, source_meta=None)
        _claim, verdict = _verify(
            "The Sahara spans 9.2 million square kilometers.", [pkg], [snap])
        assert verdict.verdict == "supported"

    def test_ratio_over_kwh_operands(self):
        # K2-16 class: "110.3 percent of" derives from two kwh rows.
        snap_c = _wb_snapshot("EG.USE.ELEC.KH.PC", iso3="CAN", date="2024",
                              value="14654.6404772939")
        snap_u = _wb_snapshot("EG.USE.ELEC.KH.PC", iso3="USA", date="2024",
                              value="13063.5600394666")
        pk_c = _wb_package("p1", "T-01", "EG.USE.ELEC.KH.PC", snap_c, 2024,
                           iso3="CAN", date="2024",
                           value="14654.6404772939")
        pk_u = _wb_package("p2", "T-01", "EG.USE.ELEC.KH.PC", snap_u, 2024,
                           iso3="USA", date="2024",
                           value="13063.5600394666")
        _claim, verdict = _verify(
            "Canada used 110.3 percent of the electricity per person that"
            " America used.",
            [pk_c, pk_u], [snap_c, snap_u])
        assert verdict.verdict == "supported"
        assert verdict.reasons == ["computed_match"]

    def test_sum_over_km2_operands(self):
        snap_c = _wb_snapshot("AG.LND.TOTL.K2", iso3="CHN", date="2024",
                              value="9388210")
        snap_u = _wb_snapshot("AG.LND.TOTL.K2", iso3="USA", date="2024",
                              value="9147420")
        pk_c = _wb_package("p1", "T-01", "AG.LND.TOTL.K2", snap_c, 2024,
                           iso3="CHN", date="2024", value="9388210")
        pk_u = _wb_package("p2", "T-01", "AG.LND.TOTL.K2", snap_u, 2024,
                           iso3="USA", date="2024", value="9147420")
        _claim, verdict = _verify(
            "China and the United States together span 18.5 million square"
            " kilometers of land.",
            [pk_c, pk_u], [snap_c, snap_u])
        assert verdict.verdict == "supported"
        assert verdict.reasons == ["computed_match"]


# ---------------------------------------------------------------------------
# Frequency guard: monthly claim x annual-rate row -> abstain (P1-36 class)
# ---------------------------------------------------------------------------

class TestMonthlyFrequency:
    @pytest.mark.parametrize("text", [
        "prices rose 219.9 percent every month in 2024",
        "prices rose 2.1 percent per month",
        "2.1 percent each month",
        "7.1 million people were unemployed that month",
        "unemployment this month was 4.0 percent",
        "monthly inflation was 2.9 percent",
        "la inflación mensual fue 2,9 por ciento",
        "precios subieron cada mes",
        "subió 2 por ciento por mes",
        "2 por ciento al mes",
    ])
    def test_monthly_detected(self, text):
        assert claim_has_monthly_frequency(text) is True

    @pytest.mark.parametrize("text", [
        "prices rose 2.9 percent in 2024",
        "prices rose 2.9 percent that year",
        "annual inflation was 2.9 percent",
        "growth was 2.9 percent per year",
        "unemployed for 6 months",  # duration, not frequency
        "",
    ])
    def test_non_monthly_rejected(self, text):
        assert claim_has_monthly_frequency(text) is False

    def test_never_raises(self):
        assert claim_has_monthly_frequency(None) is False  # type: ignore[arg-type]
        assert claim_has_monthly_frequency(123) is False  # type: ignore[arg-type]
        assert claim_has_monthly_frequency(object()) is False  # type: ignore[arg-type]

    def test_monthly_claim_needs_no_computation(self):
        # Monthly claims carry no year-pair span, so no V5-P0 op fires;
        # the per-package guard below is the deciding protection.
        assert needs_computation(
            "Argentina's prices rose 219.9 percent every month in 2024."
        ) is None


class TestFrequencyGuard:
    def _argentina(self):
        snap = _wb_snapshot("FP.CPI.TOTL.ZG", iso3="ARG", date="2024",
                            value="219.883929014578")
        pkg = _wb_package("p1", "T-01", "FP.CPI.TOTL.ZG", snap, 2024,
                          iso3="ARG", date="2024",
                          value="219.883929014578")
        return snap, pkg

    def test_monthly_claim_abstains_despite_numeric_match(self):
        # 219.9 vs 219.8839 is within tolerance — without the guard this
        # monthly claim would SUPPORT on an annual row (false-S, P1-36).
        snap, pkg = self._argentina()
        _claim, verdict = _verify(
            "Argentina's prices rose 219.9 percent every month in 2024.",
            [pkg], [snap])
        assert verdict.verdict == "unverifiable"
        assert "unit_mismatch" in verdict.reasons
        assert verdict.details["p1"]["frequency_mismatch"] is True

    def test_annual_claim_with_same_evidence_supports(self):
        # Guard is text-conditional: the same annual row DOES verify an
        # annual claim.
        snap, pkg = self._argentina()
        _claim, verdict = _verify(
            "Argentina's inflation reached 219.9 percent in 2024.",
            [pkg], [snap])
        assert verdict.verdict == "supported"

    def test_non_annual_indicator_unaffected(self):
        # Guard is indicator-conditional: unemployment (a level rate,
        # not an annual-change row) still decides monthly claims.
        snap = _wb_snapshot("SL.UEM.TOTL.ZS", iso3="USA", date="2024",
                            value="4.0")
        pkg = _wb_package("p1", "T-01", "SL.UEM.TOTL.ZS", snap, 2024,
                          iso3="USA", date="2024", value="4.0")
        _claim, verdict = _verify(
            "Unemployment averaged 4.0 percent that month.", [pkg], [snap])
        assert verdict.verdict == "supported"

    def test_operands_guard_note(self):
        snap, pkg = self._argentina()
        loader = lambda _p: snap  # noqa: E731
        ops, notes = extract_operands(
            "T-01", [pkg], loader,
            "prices rose 219.9 percent every month from 2023 to 2024")
        assert ops == []
        assert notes == ["p1:frequency_mismatch"]
        # Empty claim text disables the guard (backward compatible).
        ops2, _notes2 = extract_operands("T-01", [pkg], loader)
        assert len(ops2) == 1 and ops2[0].unit == "percent"


# ---------------------------------------------------------------------------
# Exact-rounding agreement: coarse claim vs precise row abstains (P1-35)
# ---------------------------------------------------------------------------

class TestRoundedMatch:
    def test_coarse_claim_vs_precise_row_abstains(self):
        # "0.2 percent" vs a 0.218 row: 8.2% strict gap, but the row
        # rounds EXACTLY to the claim at its stated precision — not a
        # sound refutation (abstain, never contradicted).
        snap = _wb_snapshot("FP.CPI.TOTL.ZG", iso3="CHN", date="2024",
                            value="0.218128938439177")
        pkg = _wb_package("p1", "T-01", "FP.CPI.TOTL.ZG", snap, 2024,
                          iso3="CHN", date="2024",
                          value="0.218128938439177")
        _claim, verdict = _verify(
            "China's inflation was only 0.2 percent.", [pkg], [snap])
        assert verdict.verdict == "unverifiable"
        assert "rounded_match" in verdict.reasons
        assert verdict.details["p1"]["rounding_quantum"] == 0.1

    def test_102_1_pin_compatible(self):
        # 100 vs 102.1 rounds to 102 != 100: still a real contradiction
        # (the test_f2_recall/test_m2d tolerance pins hold by design).
        snap = _wb_snapshot("NY.GDP.MKTP.CD", iso3="USA", date="2024",
                            value="102.1")
        pkg = _wb_package("p1", "T-01", "NY.GDP.MKTP.CD", snap, 2024,
                          iso3="USA", date="2024", value="102.1")
        _claim, verdict = _verify(
            "The fund holds 100 dollars in reserve.", [pkg], [snap])
        assert verdict.verdict == "contradicted"
        assert verdict.reasons == ["numeric_mismatch"]

    def test_near_miss_still_contradicts(self):
        # 2.2 vs 0.218 rounds to 0.2 != 2.2: real contradiction.
        snap = _wb_snapshot("FP.CPI.TOTL.ZG", iso3="CHN", date="2024",
                            value="0.218128938439177")
        pkg = _wb_package("p1", "T-01", "FP.CPI.TOTL.ZG", snap, 2024,
                          iso3="CHN", date="2024",
                          value="0.218128938439177")
        _claim, verdict = _verify(
            "Consumer prices in China rose 2.2 percent in 2024.",
            [pkg], [snap])
        assert verdict.verdict == "contradicted"

    def test_ambiguous_quantum_keeps_strict_path(self):
        # Unparseable raw -> quantum None -> strict mismatch stands.
        from live.verifier.check import ClaimInput
        snap = _wb_snapshot("NY.GDP.MKTP.CD", iso3="USA", date="2024",
                            value="102.1")
        pkg = _wb_package("p1", "t", "NY.GDP.MKTP.CD", snap, 2024,
                          iso3="USA", date="2024", value="102.1")
        claim = ClaimInput(claim_id="t", text="reserve", value=100.0,
                           unit="USD", raw="", claim_year=None)
        verdict = verify_claim(claim, [pkg], lambda _p: snap)
        assert verdict.verdict == "contradicted"

    def test_strict_match_unaffected(self):
        # 2.9 vs 2.9495: strict 1.7% support, no rounding receipt needed.
        snap = _wb_snapshot("FP.CPI.TOTL.ZG", iso3="USA", date="2024",
                            value="2.94952520485207")
        pkg = _wb_package("p1", "T-01", "FP.CPI.TOTL.ZG", snap, 2024,
                          iso3="USA", date="2024",
                          value="2.94952520485207")
        _claim, verdict = _verify(
            "Consumer prices in the United States rose 2.9 percent in 2024.",
            [pkg], [snap])
        assert verdict.verdict == "supported"
        assert "rounding_quantum" not in verdict.details["p1"]
