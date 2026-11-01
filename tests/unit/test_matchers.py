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


def test_rate_language_routes_to_rate_indicator():
    # UT-c: a change magnitude needs a change indicator, not a level.
    from atlas.connectors.worldbank import match_indicator

    code, _, _ = match_indicator("US GDP grew 5%")
    assert code == "NY.GDP.MKTP.KD.ZG"
    code, _, _ = match_indicator("prices rose 3 percent")
    assert code == "FP.CPI.TOTL.ZG"


def test_rate_language_without_magnitude_keeps_level():
    # No number, no confusion: existing longest-match behavior stands.
    from atlas.connectors.worldbank import match_indicator

    code, _, _ = match_indicator("GDP per capita grew")
    assert code == "NY.GDP.PCAP.CD"


def test_to_phrase_is_level_not_change():
    from atlas.connectors.worldbank import is_change_magnitude

    assert is_change_magnitude("US GDP grew 5%") is True
    assert is_change_magnitude("exports rose 5%") is True
    assert is_change_magnitude("las exportaciones crecieron un 5%") is True
    assert is_change_magnitude("fell from 4% in 2020 to 3% in 2023") is False
    assert is_change_magnitude("rose to 5%") is False
    assert is_change_magnitude("aumentó a 5%") is False
    assert is_change_magnitude("Unemployment was 3%") is False


def test_change_verb_coverage():
    # UT-c iter2 (judge): each verb below must fire is_change_magnitude.
    from atlas.connectors.worldbank import is_change_magnitude as f

    for text in ("surged 5%", "tumbled 3%", "contracted 2%", "soared 4%",
                 "slid 1%", "collapsed 9%", "gained 2%", "cooled 1%",
                 "eased 1%", "expanded 3%", "rallied 4%", "sank 2%",
                 "GDP up 5%", "GDP down 3%", "5% higher than last year",
                 "avanzó 3%", "retrocedió 2%", "se disparó 5%",
                 "la pobreza disminuyó 5%", "rose 5% to a record",
                 "a 5% rise"):
        # Nominal change language ("a 5% rise") still states a rate.
        assert f(text) is True, text
    for text in ("rose to a record 5%",
                 "GDP rose over a 5-year period",
                 "el PIB creció durante 5 años",
                 "higher than 5%", "pasó de 3% a 5%"):
        # "pasó de X a Y" pins two levels (per-quantity split covers it).
        assert f(text) is False, text


def test_per_capita_and_prices_routing():
    # UT-c iter2 (judge): wrong-denominator and overbroad-prices guards.
    from atlas.connectors.worldbank import match_indicator

    m = match_indicator("GDP per capita grew 5%")
    assert m is None or m[0] != "NY.GDP.MKTP.KD.ZG", m
    m = match_indicator("export prices rose 5%")
    assert m is None or m[0] != "FP.CPI.TOTL.ZG", m
    m = match_indicator("poblacion crecio 1%")
    assert m is None or m[0] in ("SP.POP.GROW", "SP.POP.TOTL"), m


def test_iter3_judge_verbs_and_nominals():
    # UT-c iter3: re-judge P1-P7, P23 — every probe below is a rate.
    from atlas.connectors.worldbank import is_change_magnitude as f

    for text in ("US exports plummeted 20% in 2023.",
                 "A 5% reduction in US unemployment in 2023.",
                 "US exports were up by 5% in 2023.",
                 "US exports inched 2% higher in 2023.",
                 "Las exportaciones de España crecen un 5%.",
                 "El desempleo en España baja un 2%.",
                 "US exports saw a 2% contraction in 2023.",
                 "US exports skyrocketed 50% in 2023.",
                 "GDP 5 percentage points higher than last year.",
                 "exports nosedived 10%", "stocks rebounded 4%",
                 "prices retreated 3%", "GDP is 5% higher.",
                 "GDP is 5% lower than in 2020.",
                 "un 5% superior al año pasado",
                 "sales ballooned 30%", "output slumped 4%",
                 "demand recovered 6%", "GDP advanced 2%"):
        assert f(text) is True, text


def test_iter3_judge_level_pins():
    # UT-c iter3: re-judge P8-P13, P24 — pinned levels, not changes.
    from atlas.connectors.worldbank import (
        is_change_magnitude as f, is_change_quantity as g)

    assert f("Unemployment rose to almost 5% in 2023.") is False
    assert f("GDP rose to an all-time high of 5% in 2023.") is False
    assert g("US GDP rose 5% to about $30 trillion.", "$30 trillion") is False
    assert g("US GDP rose 5% to about $30 trillion.", "5%") is True
    assert g("Unemployment fell from about 4% in 2020 to 3% in 2023.",
             "4%") is False
    assert f("La inflación pasó del 3% al 5%.") is False
    assert g("La inflación pasó del 3% al 5%.", "3%") is False
    assert g("La inflación pasó del 3% al 5%.", "5%") is False
    assert f("La inflación pasó de un 3% a un 5%.") is False
    assert g("La inflación pasó de un 3% a un 5%.", "3%") is False
    assert g("La inflación pasó de un 3% a un 5%.", "5%") is False
    assert g("GDP rose over 5 years, reaching 3%.", "3%") is False
    assert f("La inflación alcanzó el 5% en 2023.") is False
    assert f("El paro bajó desde el 4% hasta el 3%.") is False


def test_iter3_judge_lone_year_and_period():
    # UT-c iter3: P21/P22 + year-digit guard.
    from atlas.connectors.worldbank import is_change_magnitude as f

    assert f("US GDP rose over a 5 year period.") is False
    assert f("El PIB creció durante 1 año.") is False
    assert f("The rise of GDP in 2023 was historic.") is False
    assert f("GDP rose in 2023.") is False
    assert f("GDP grew 5% over 5 years.") is True


def test_iter3_judge_per_capita_and_prices():
    # UT-c iter3: P15-P20 — wrong-denominator and qualifier guards.
    from atlas.connectors.worldbank import match_indicator as m

    r = m("US GDP per-capita grew 5%")
    assert r is None or r[0] != "NY.GDP.MKTP.KD.ZG", r
    r = m("US GDP per person grew 5%")
    assert r is None or r[0] != "NY.GDP.MKTP.KD.ZG", r
    r = m("Prices of oil rose 5%")
    assert r is None or r[0] != "FP.CPI.TOTL.ZG", r
    r = m("US gasoline prices rose 5%")
    assert r is None or r[0] != "FP.CPI.TOTL.ZG", r
    r = m("US home prices rose 5%")
    assert r is None or r[0] != "FP.CPI.TOTL.ZG", r
    r = m("Los precios del petróleo subieron un 5%")
    assert r is None or r[0] != "FP.CPI.TOTL.ZG", r
    # ...while genuine CPI language still routes:
    r = m("Prices rose 5%")
    assert r is not None and r[0] == "FP.CPI.TOTL.ZG", r
    r = m("Los precios de consumo subieron un 5%")
    assert r is not None and r[0] == "FP.CPI.TOTL.ZG", r
    r = m("Los precios al consumidor subieron un 5%")
    assert r is not None and r[0] == "FP.CPI.TOTL.ZG", r


def test_iter4_judge_verbs_and_gaps():
    # UT-c iter4: re-judge P01-P05, P09 — verb paradigm audit.
    from atlas.connectors.worldbank import is_change_magnitude as f

    for text in ("US exports escalated 5% in 2023.",
                 "Prices jump 5% in March.",
                 "Unemployment climbs 2% each year.",
                 "El desempleo ha disminuido un 2%.",
                 "La economía mejoró un 5% en 2023.",
                 "US exports were up by nearly 5% in 2023.",
                 "GDP down by 3% last year.",
                 "sales are ballooning 10%",
                 "output contracts 2%",
                 "demand is recovering 6%",
                 "el PIB sigue creciendo un 3%",
                 "el paro sigue bajando un 1%",
                 "los precios fueron aumentando un 2%"):
        assert f(text) is True, text


def test_iter4_judge_level_qualifiers():
    # UT-c iter4: P13-P15, P17 — stacked qualifiers + de-X-al-Y.
    from atlas.connectors.worldbank import (
        is_change_magnitude as f, is_change_quantity as g)

    assert f("GDP rose to just 5% in 2023.") is False
    assert g("GDP rose 5% to exactly $30 trillion.",
             "$30 trillion") is False
    assert f("GDP rose to well above 5%.") is False
    assert f("GDP rose to only 5% in 2023.") is False
    assert g("El paro bajó de 4% al 3%.", "4%") is False
    assert g("El paro bajó de 4% al 3%.", "3%") is False


def test_iter4_judge_year_magnitudes():
    # UT-c iter4: P26-P29 — year-shaped magnitudes survive; decades strip.
    from atlas.connectors.worldbank import is_change_magnitude as f

    assert f("GDP rose 2000 dollars.") is True
    assert f("Exports increased 2010 units in 2023.") is True
    assert f("GDP rose $2000 per person.") is True
    assert f("GDP rose over 5 decades.") is False
    assert f("GDP grew in 2020 dollars to 25T.") is False
    assert f("The rise of GDP in 2023 was historic.") is False


def test_iter4_judge_denominator_and_prices():
    # UT-c iter4: P21-P25.
    from atlas.connectors.worldbank import match_indicator as m

    for text in ("US GDP per individual grew 5%",
                 "US GDP per resident grew 5%"):
        r = m(text)
        assert r is None or r[0] != "NY.GDP.MKTP.KD.ZG", (text, r)
    for text in ("US fuel prices rose 5%",
                 "US rental prices rose 5%",
                 "Los precios inmobiliarios subieron un 5%"):
        r = m(text)
        assert r is None or r[0] != "FP.CPI.TOTL.ZG", (text, r)


def test_iter5_judge_es_paradigms_and_futures():
    # UT-c iter5: re-judge E1/E2/E3 — increment-/descend- + futures.
    from atlas.connectors.worldbank import is_change_magnitude as f

    for text in ("Las exportaciones se incrementaron un 5%.",
                 "El desempleo descendió un 2%.",
                 "El PIB crecerá un 3%.",
                 "La inflación aumentará un 1%.",
                 "El paro bajará un 2%."):
        assert f(text) is True, text


def test_iter5_judge_up_over_and_ranges():
    # UT-c iter5: U2/U3/D2/D3/Q1/Q4 + P-fixes.
    from atlas.connectors.worldbank import (
        is_change_magnitude as f, is_change_quantity as g,
        match_indicator as m)

    assert f("US exports were up by over 5% in 2023.") is True
    assert f("US exports were up over 5% in 2023.") is True
    assert f("GDP up by roughly 2%.") is True
    assert f("El paro bajó del 4% a un 3%.") is False
    assert g("El paro bajó del 4% a un 3%.", "4%") is False
    assert f("La inflación subió hasta un 5%.") is False
    assert f("GDP rose to a mere 5%.") is False
    assert f("GDP rose to approximately 5%.") is False
    assert f("Exports rose 2015 rupees.") is True
    assert f("Exports rose 2000 francs.") is True
    # P-fixes: \b guards + household synonyms.
    r = m("US GDP per household grew 5%")
    assert r is None or r[0] != "NY.GDP.MKTP.KD.ZG", r
    r = m("US GDP per headline grew 5%")
    assert r is not None and r[0] == "NY.GDP.MKTP.KD.ZG", r


def test_iter6_judge_residual_rates():
    # UT-c iter6 (convergence): judge M1-M6, M20 + E6.
    from atlas.connectors.worldbank import is_change_magnitude as f

    for text in ("Las exportaciones se redujeron un 5%.",
                 "Las ventas avanzaron un 3%.",
                 "Los precios retrocedieron un 2%.",
                 "Una disminución del 5% en el desempleo.",
                 "La reducción del gasto fue del 3%.",
                 "La caída de la producción fue del 4%.",
                 "El descenso del paro fue del 1%.",
                 "Una subida del 2% en precios.",
                 "Los precios bajaban un 2%.",
                 "La producción caía un 3%.",
                 "El PIB crecía un 4%.",
                 "A 5% spike in prices.",
                 "An uptick of 2% in sales.",
                 "A dip of 3% in output.",
                 "A boost of 4% in demand.",
                 "A rate hike of 1%.",
                 "Demand is gaining 6%.",
                 "Costs were cut 5%.",
                 "The budget cuts spending by 5 percent.",
                 "Wages rose 2000 kroner."):
        assert f(text) is True, text
    # %-gate holds: bare cut with a count is not a rate.
    assert f("The firm cut 5 jobs in 2023.") is False


def test_iter6_judge_residual_levels():
    # UT-c iter6: judge M7/M8 — record bug + max/min/peak/trough.
    from atlas.connectors.worldbank import is_change_magnitude as f

    for text in ("GDP rose to record 5%.",
                 "GDP rose to record high 5%.",
                 "GDP rose to a maximum of 5%.",
                 "Unemployment fell to a minimum of 3%.",
                 "Inflation hit a peak of 8%.",
                 "Output sank to a trough of 2%.",
                 "La inflación subió hasta un máximo de 5%.",
                 "El paro bajó hasta un mínimo del 3%."):
        assert f(text) is False, text


def test_indicator_coverage_pop_growth():
    # UT-i (i2): population growth routes to the rate indicator.
    from atlas.connectors.worldbank import match_indicator as m

    r = m("population growth was 1 percent")
    assert r is not None and r[0] == "SP.POP.GROW", r
    r = m("el crecimiento de la población fue del 1%")
    assert r is not None and r[0] == "SP.POP.GROW", r
    # ...while plain population keeps the level.
    r = m("population was 68 million")
    assert r is not None and r[0] == "SP.POP.TOTL", r


def test_indicator_coverage_usd_levels():
    # UT-i (i1): exports/imports in USD route to current-US$ levels;
    # shares keep the % of GDP indicator.
    from atlas.connectors.worldbank import match_indicator as m

    r = m("exports in current USD")
    assert r is not None and r[0] == "NE.EXP.GNFS.CD", r
    assert r[2] == "USD"
    r = m("US exports were $3 trillion in 2023")
    assert r is not None and r[0] == "NE.EXP.GNFS.CD", r
    r = m("imports in current USD")
    assert r is not None and r[0] == "NE.IMP.GNFS.CD", r
    r = m("las exportaciones fueron 500.000 millones de dólares")
    assert r is not None and r[0] == "NE.EXP.GNFS.CD", r
    r = m("exports were 12% of GDP")
    assert r is not None and r[0] == "NE.EXP.GNFS.ZS", r
    r = m("las exportaciones son el 30% del PIB")
    assert r is not None and r[0] == "NE.EXP.GNFS.ZS", r
    # UT-c intact: change language still refuses the level compare.
    r = m("US exports grew 5%")
    assert r is not None and r[0] == "NE.EXP.GNFS.ZS", r


def test_indicator_usd_dual_family_guard():
    # UT-i iter2 (judge M1): combined trade never picks one flow.
    from atlas.connectors.worldbank import match_indicator as m

    r = m("in 2023, imports and exports were $7T combined")
    assert r is None or r[0] != "NE.EXP.GNFS.CD", r
    assert r is None or r[0] != "NE.IMP.GNFS.CD", r


def test_indicator_usd_singulars_and_hygiene():
    # UT-i iter2 minors: singulars route, petrodollars/important safe.
    from atlas.connectors.worldbank import match_indicator as m

    r = m("export share 20% in dollars")
    assert r is None or r[0] in ("NE.EXP.GNFS.CD", "NE.EXP.GNFS.ZS"), r
    r = m("la exportación en dólares fue alta")
    assert r is not None and r[0] == "NE.EXP.GNFS.CD", r
    r = m("important: exports were $3T in 2023")
    assert r is not None and r[0] == "NE.EXP.GNFS.CD", r
    r = m("exports ratio to GDP in dollar terms was high")
    assert r is None or r[0] == "NE.EXP.GNFS.ZS", r
