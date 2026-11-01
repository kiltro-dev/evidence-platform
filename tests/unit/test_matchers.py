"""Matcher contract tests: indicators, countries, time refs (offline).

Asserts ACTUAL behavior: resolve_country is accent-insensitive (folds
accents before matching), while match_indicator is accent-sensitive
(aliases are listed explicitly, incl. Spanish variants).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.connectors.countries import resolve_country
from atlas.connectors.worldbank import match_indicator
from atlas.models.heuristic import extract_times


# --- match_indicator ---


def test_longest_keyword_wins():
    code, _, _ = match_indicator("GDP per capita grew")
    assert code == "NY.GDP.PCAP.CD"
    code, _, _ = match_indicator("gross domestic product")
    assert code == "NY.GDP.MKTP.CD"


def test_overlap_prefers_longer_indicator():
    code, label, unit = match_indicator(
        "The GDP was high and unemployment was low")
    assert code == "SL.UEM.TOTL.ZS"
    assert unit == "percent"


def test_match_is_case_insensitive():
    assert match_indicator("GDP") is not None
    assert match_indicator("Inflation rose") is not None


def test_match_is_accent_sensitive_with_explicit_aliases():
    assert match_indicator("life expectancy") is not None
    # Accented typos do NOT fold: no such alias exists.
    assert match_indicator("lifé expectancy") is None
    # ...but listed accented aliases match verbatim.
    assert match_indicator("inflación") is not None
    assert match_indicator("población") is not None


def test_match_unknown_returns_none():
    assert match_indicator("the moon is made of cheese") is None
    assert match_indicator("") is None


# --- resolve_country ---


def test_resolve_basic():
    assert resolve_country("Japan") == ("japan", "JPN")
    assert resolve_country("The GDP of the United States") == ("united states",
                                                               "USA")


def test_resolve_longest_overlap_wins():
    assert resolve_country("Korea and South Korea") == ("south korea", "KOR")


def test_resolve_case_insensitive():
    assert resolve_country("JAPAN")[1] == "JPN"
    assert resolve_country("germany")[1] == "DEU"


def test_resolve_accents_tolerant():
    assert resolve_country("México")[1] == "MEX"
    assert resolve_country("Mexico")[1] == "MEX"
    assert resolve_country("España")[1] == "ESP"
    assert resolve_country("Japón")[1] == "JPN"


def test_resolve_unknown_returns_nones():
    assert resolve_country("Atlantis") == (None, None)
    assert resolve_country("Freedonia") == (None, None)
    assert resolve_country("") == (None, None)


# --- extract_times ---


def test_single_year():
    times = extract_times("in 2020")
    assert [(t.year, t.period_type) for t in times] == [(2020, "year")]


def test_hyphen_range_forms_one_range():
    times = extract_times("the trend over 2020-2023")
    assert len(times) == 1  # members must NOT also appear as years
    assert (times[0].start_year, times[0].end_year,
            times[0].period_type) == (2020, 2023, "range")


def test_en_dash_range():
    times = extract_times("the trend over 2020–2023")
    assert len(times) == 1
    assert (times[0].start_year, times[0].end_year) == (2020, 2023)


def test_two_plain_years_stay_separate():
    times = extract_times("from 4% in 2020 to 3% in 2023")
    assert [(t.year, t.period_type) for t in times] == [(2020, "year"),
                                                       (2023, "year")]


def test_vague_present_reference():
    times = extract_times("Unemployment recently fell a lot.")
    assert len(times) == 1
    assert times[0].period_type == "vague"


def test_no_times_for_empty():
    assert extract_times("") == []


# --- F1-C: new World Bank indicator rows (keyword routing only, offline) ---


def test_match_new_indicator_rows_en():
    assert match_indicator("GDP growth was strong")[0] == "NY.GDP.MKTP.KD.ZG"
    assert match_indicator("poverty fell sharply")[0] == "SI.POV.DDAY"
    assert match_indicator("the gini coefficient rose")[0] == "SI.POV.GINI"
    assert match_indicator("renewables share rose")[0] == "EG.FEC.RNEW.ZS"
    assert match_indicator("child mortality declined")[0] == "SH.DYN.MORT"
    assert match_indicator("mobile subscriptions grew")[0] == "IT.CEL.SETS.P2"
    assert match_indicator("youth unemployment rose")[0] == "SL.UEM.1524.ZS"


def test_match_new_indicator_rows_es():
    assert match_indicator("el crecimiento pib fue alto")[0] == "NY.GDP.MKTP.KD.ZG"
    assert match_indicator("la pobreza disminuyó")[0] == "SI.POV.DDAY"
    assert match_indicator("gini")[0] == "SI.POV.GINI"
    assert match_indicator("las renovables crecieron")[0] == "EG.FEC.RNEW.ZS"
    assert match_indicator("la mortalidad infantil bajó")[0] == "SH.DYN.MORT"
    assert match_indicator("los móviles aumentaron")[0] == "IT.CEL.SETS.P2"
    assert match_indicator("los moviles aumentaron")[0] == "IT.CEL.SETS.P2"
    assert match_indicator("el desempleo juvenil subió")[0] == "SL.UEM.1524.ZS"


def test_new_rows_do_not_shadow_existing():
    assert match_indicator("unemployment was low")[0] == "SL.UEM.TOTL.ZS"
    assert match_indicator("GDP was high")[0] == "NY.GDP.MKTP.CD"
    assert match_indicator("el desempleo subió")[0] == "SL.UEM.TOTL.ZS"
    assert match_indicator("el pib creció")[0] == "NY.GDP.MKTP.CD"
