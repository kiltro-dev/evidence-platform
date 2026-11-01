"""Spanish-language (ES) support tests: extraction, units, countries.

Plus EN regression spot-checks proving no behavior change for EN inputs.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.connectors.countries import resolve_country
from atlas.connectors.worldbank import match_indicator
from atlas.core.units import normalize_unit, parse_quantities
from atlas.models.heuristic import (
    classify_claim_type,
    classify_domain,
    extract_heuristic,
    extract_times,
)


# --- full ES extraction ---


def test_spanish_unemployment_claim_end_to_end():
    claim = extract_heuristic("El desempleo en Alemania fue del 3,1% en 2023")
    assert claim.domain == "economy"
    assert claim.claim_type == "quantitative"
    pct = [q for q in claim.quantities if q.normalized_unit == "percent"]
    assert len(pct) == 1
    assert pct[0].value == 3.1
    assert any(t.year == 2023 for t in claim.time_refs)
    assert claim.geo_refs and claim.geo_refs[0].iso3 == "DEU"
    assert claim.indicator == "SL.UEM.TOTL.ZS"


def test_spanish_domain_science():
    assert classify_domain("Un nuevo estudio sobre la temperatura") == "science"
    assert classify_domain("La investigación fue publicada en una revista") == "science"


def test_spanish_domain_economy():
    assert classify_domain("El pib creció el año pasado") == "economy"
    assert classify_domain("La inflación y el desempleo suben") == "economy"


def test_spanish_claim_types():
    assert classify_claim_type("El pib es mayor que el del año pasado") == "comparative"
    assert classify_claim_type("La deuda aumentó este año") == "comparative"
    assert classify_claim_type("Hay inflación en el país") == "existence"
    assert classify_claim_type("El desempleo existe desde hace años") == "existence"


def test_spanish_vague_time():
    assert any(
        t.period_type == "vague" for t in extract_times("Recientemente hubo cambios")
    )
    assert any(
        t.period_type == "vague" for t in extract_times("Actualmente hay inflación")
    )
    assert any(t.period_type == "vague" for t in extract_times("Hoy hay elecciones"))


# --- ES units and scales ---


def test_decimal_comma_percent():
    qs = parse_quantities("fue del 3,1%")
    assert len(qs) == 1
    assert qs[0].value == 3.1
    assert qs[0].normalized_unit == "percent"


def test_decimal_comma_two_digits():
    qs = parse_quantities("inflación del 12,25%")
    assert len(qs) == 1
    assert qs[0].value == 12.25


def test_thousands_still_win_over_decimal_comma():
    qs = parse_quantities("299,792,458 m/s")
    assert len(qs) == 1
    assert qs[0].value == 299792458.0


def test_spanish_scales():
    qs = parse_quantities("5 millones de personas")
    assert qs[0].value == 5e6
    assert qs[0].normalized_unit == "count"
    qs = parse_quantities("2 billones de euros")
    assert qs[0].value == 2e12
    assert qs[0].normalized_unit == "EUR"
    qs = parse_quantities("3 millardos de dólares")
    assert qs[0].value == 3e9
    assert qs[0].normalized_unit == "USD"
    qs = parse_quantities("10 mil habitantes")
    assert qs[0].value == 10e3
    assert qs[0].normalized_unit == "count"


def test_billon_is_long_scale_trillion():
    # ES billón = 1e12 = EN trillion, NOT EN billion.
    assert parse_quantities("1 billón")[0].value == 1e12
    assert parse_quantities("1 billion")[0].value == 1e9
    assert parse_quantities("1 millardo")[0].value == 1e9


def test_por_ciento_and_es_counts():
    qs = parse_quantities("el 5 por ciento de la población")
    assert len(qs) == 1
    assert qs[0].value == 5.0
    assert qs[0].normalized_unit == "percent"
    assert normalize_unit("personas") == "count"
    assert normalize_unit("habitantes") == "count"
    assert normalize_unit("muertes") == "count"
    assert normalize_unit("casos") == "count"
    assert normalize_unit("por ciento") == "percent"


def test_es_currency_and_years():
    assert normalize_unit("dólares") == "USD"
    assert normalize_unit("año") == "years"
    assert normalize_unit("años") == "years"


# --- accent-insensitive countries ---


def test_spanish_country_names():
    assert resolve_country("Alemania") == ("alemania", "DEU")
    assert resolve_country("Estados Unidos") == ("estados unidos", "USA")
    assert resolve_country("Reino Unido") == ("reino unido", "GBR")
    assert resolve_country("Corea del Sur") == ("corea del sur", "KOR")


def test_accent_insensitive_matching():
    assert resolve_country("México")[1] == "MEX"
    assert resolve_country("Mexico")[1] == "MEX"
    assert resolve_country("España")[1] == "ESP"
    assert resolve_country("Espana")[1] == "ESP"
    assert resolve_country("Japón")[1] == "JPN"
    assert resolve_country("Japon")[1] == "JPN"
    assert resolve_country("Perú")[1] == "PER"
    assert resolve_country("Peru")[1] == "PER"


# --- ES indicator keywords ---


def test_spanish_indicator_keywords():
    assert match_indicator("el pib creció")[0] == "NY.GDP.MKTP.CD"
    assert match_indicator("producto interno bruto")[0] == "NY.GDP.MKTP.CD"
    assert match_indicator("tasa de desempleo")[0] == "SL.UEM.TOTL.ZS"
    assert match_indicator("la inflación anual")[0] == "FP.CPI.TOTL.ZG"
    assert match_indicator("la inflacion anual")[0] == "FP.CPI.TOTL.ZG"
    assert match_indicator("la población total")[0] == "SP.POP.TOTL"
    assert match_indicator("esperanza de vida")[0] == "SP.DYN.LE00.IN"
    assert match_indicator("precios al consumidor")[0] == "FP.CPI.TOTL.ZG"
    assert match_indicator("la deuda pública")[0] == "GC.DOD.TOTL.GD.ZS"
    assert match_indicator("las exportaciones")[0] == "NE.EXP.GNFS.ZS"


# --- EN regression spot-checks ---


def test_en_regression_quantities():
    qs = parse_quantities("GDP was $27.36 trillion in 2023")
    money = [q for q in qs if q.normalized_unit == "USD"]
    assert len(money) == 1
    assert money[0].value == 27.36e12
    qs = parse_quantities("inflation of 3.2% and 5 million people")
    assert len(qs) == 2
    assert qs[0].normalized_unit == "percent"
    assert qs[1].value == 5e6


def test_en_regression_miles_not_mil_scale():
    # "mil" scale must not fire inside the EN word "miles".
    qs = parse_quantities("5 miles")
    assert len(qs) == 1
    assert qs[0].value == 5.0


def test_en_regression_domain_and_country():
    assert classify_domain("Unemployment and inflation rose") == "economy"
    assert classify_domain("A new study on temperature") == "science"
    assert classify_domain("The sky is blue today") == "general"
    assert resolve_country("Germany") == ("germany", "DEU")
    assert resolve_country("United States") == ("united states", "USA")
    assert match_indicator("unemployment rate")[0] == "SL.UEM.TOTL.ZS"
