"""Deterministic unit parsing and normalization. No models here."""

from __future__ import annotations

import re

from .types import Quantity

SCALE_WORDS: dict[str, float] = {
    "trillion": 1e12,
    "trillions": 1e12,
    "tn": 1e12,
    "billion": 1e9,
    "billions": 1e9,
    # "b" (attached only, e.g. "$27b") = billion; bare "t" is DELIBERATELY
    # absent — "5 t" reads as 5 tonnes, never 5 trillion (M6 finding).
    "b": 1e9,
    "bn": 1e9,
    "million": 1e6,
    "millions": 1e6,
    "mn": 1e6,
    "m": 1e6,
    "thousand": 1e3,
    "thousands": 1e3,
    "k": 1e3,
    "hundred": 1e2,
    "hundreds": 1e2,
    # --- Spanish (ES) scale words ---
    # NOTE: ES "billón" = 1e12, i.e. the same magnitude as EN "trillion"
    # (long scale), NOT EN "billion". ES "millardo" = 1e9 = EN "billion".
    "billón": 1e12,
    "billon": 1e12,
    "billones": 1e12,
    "millardo": 1e9,
    "millardos": 1e9,
    "millón": 1e6,
    "millon": 1e6,
    "millones": 1e6,
    "mil": 1e3,
    # Compound ES scale: "5 mil millones" = 5e9 (short "mil" alone stays 1e3).
    "mil millones": 1e9,
    # Attached UPPERCASE "T" only ("$27.72T" = trillion). Lowercase "t" is
    # tonnes ("5 t"), never trillion — enforced case-sensitively in the
    # regex, not here (see _QUANTITY_RE).
    "T": 1e12,
}

CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD",
    "usd": "USD",
    "us$": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "¥": "JPY",
    "jpy": "JPY",
}

# canonical unit -> aliases that map to it
UNIT_ALIASES: dict[str, str] = {
    "usd": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "gbp": "GBP",
    "percent": "percent",
    "%": "percent",
    "percentage point": "pp",
    "percentage points": "pp",
    "pp": "pp",
    "people": "count",
    "inhabitants": "count",
    "deaths": "count",
    "cases": "count",
    # --- Spanish (ES) aliases ---
    # "euro"/"euros" already covered above. Bare "ciento" is deliberately
    # NOT an alias (too risky as a substring-adjacent common word); use
    # the full "por ciento" form or "%" instead.
    "dólar": "USD",
    "dolar": "USD",
    "dólares": "USD",
    "dolares": "USD",
    "libra": "GBP",
    "libras": "GBP",
    "por ciento": "percent",
    "punto porcentual": "pp",
    "puntos porcentuales": "pp",
    "persona": "count",
    "personas": "count",
    "habitante": "count",
    "habitantes": "count",
    "muerte": "count",
    "muertes": "count",
    "caso": "count",
    "casos": "count",
    "año": "years",
    "ano": "years",
    "años": "years",
    "anos": "years",
    "m/s": "m/s",
    "meters per second": "m/s",
    "metres per second": "m/s",
    "km/s": "km/s",
    # --- V5-2B: energy / area / length / ES mirrors (general EN/ES) ---
    # kWh family. Megawatt forms ALSO normalize here, but parse_quantities
    # additionally scales their value x1000 (see _MEGAWATT_UNIT_RE): the
    # canonical unit is kwh in both cases ("140 megawatt hours" reads as
    # 140,000 kwh, never 140 kwh).
    "kilowatt hour": "kwh",
    "kilowatt hours": "kwh",
    "kilowatt-hour": "kwh",
    "kilowatt-hours": "kwh",
    "kwh": "kwh",
    "megawatt hour": "kwh",
    "megawatt hours": "kwh",
    "megawatt-hour": "kwh",
    "megawatt-hours": "kwh",
    "mwh": "kwh",
    # Area. Square kilometers and square METERS are DELIBERATELY distinct
    # canonical units (km2 vs m2): conflating them would verify a false
    # "97,600 square meters" land claim against a 97,600 sq-km row (S2-12).
    "square kilometer": "km2",
    "square kilometers": "km2",
    "square kilometre": "km2",
    "square kilometres": "km2",
    "sq km": "km2",
    "sq. km": "km2",
    "km2": "km2",
    "km²": "km2",
    "kilómetro cuadrado": "km2",
    "kilómetros cuadrados": "km2",
    "kilometro cuadrado": "km2",
    "kilometros cuadrados": "km2",
    "square meter": "m2",
    "square meters": "m2",
    "square metre": "m2",
    "square metres": "m2",
    "sq m": "m2",
    "sq. m": "m2",
    "m2": "m2",
    "m²": "m2",
    "metro cuadrado": "m2",
    "metros cuadrados": "m2",
    # Linear length. Bare "m" (lowercase, spaced: "8849 m") is meters by
    # SI convention — the documented intent of the scale-guard comment
    # below ("100 m" = 100 meters, never 100 million); uppercase "M"
    # stays unitless (conservative: million-abbreviation collision).
    "meter": "meters",
    "meters": "meters",
    "metre": "meters",
    "metres": "meters",
    "metro": "meters",
    "metros": "meters",
    "m": "meters",
    # ES counts mirror ("7,1 millones de desempleados").
    "desempleado": "count",
    "desempleados": "count",
    "desempleada": "count",
    "desempleadas": "count",
    # ES speed mirror ("299792458 metros por segundo").
    "metro por segundo": "m/s",
    "metros por segundo": "m/s",
    # Spaced EN speed ("29,979 kilometers per second", S1-04 class).
    "kilometer per second": "km/s",
    "kilometers per second": "km/s",
    "kilometre per second": "km/s",
    "kilometres per second": "km/s",
    "celsius": "celsius",
    "c": "celsius",
    "°c": "celsius",
    "fahrenheit": "fahrenheit",
    "°f": "fahrenheit",
    "kelvin": "kelvin",
    "years": "years",
    "year": "years",
}

# Decimal-comma rule (Spanish-style "3,1%"): a comma followed by exactly
# 1-2 digits NOT followed by another digit is a decimal separator, e.g.
# "3,1" -> 3.1. Comma groups of 3 digits remain thousands separators, e.g.
# "299,792,458" -> 299792458 (the thousands alternative is tried first,
# so EN behavior is unchanged). "1,000" therefore still reads as one
# thousand even in Spanish text — a documented limitation.
_DECIMAL_COMMA_RE = re.compile(r"\d+,\d{1,2}$")

# Spanish thousand separators: dot groups of EXACTLY 3 digits. Two or
# more groups ("1.400.000") are never a decimal in any locale, so they
# always read as thousands. A SINGLE group ("1.500") is genuinely
# ambiguous (ES 1500 vs EN 1.5): it reads as thousands ONLY with a
# Spanish scale/unit marker nearby, else decimal (fuel prices like
# "1.459 EUR/L" and EN decimals win the default).
_THOUSAND_DOT_MULTI_RE = re.compile(r"\d{1,3}(?:\.\d{3}){2,}$")
_THOUSAND_DOT_SINGLE_RE = re.compile(r"\d{1,3}\.\d{3}$")
# ES mirror of EN "1,000.50": dot-thousands + comma-decimal.
_THOUSAND_DOT_COMMA_RE = re.compile(r"\d{1,3}(?:\.\d{3})+,\d+$")

_ES_SCALES = frozenset({
    "millones", "millón", "millon", "billones", "billón", "billon",
    "millardos", "millardo", "mil", "mil millones",
})
_ES_UNITS = frozenset({
    "dólares", "dolares", "dólar", "dolar", "euros", "euro", "pesos",
    "peso", "libras", "libra", "personas", "persona", "habitantes",
    "habitante", "muertes", "muerte", "casos", "caso", "años", "anos",
    "año", "ano", "por ciento", "puntos porcentuales",
    "punto porcentual",
})

# Single-letter scales (k/m/b) count ONLY when attached to the digits
# ("$27m", "5k"): space-separated single letters are units, not scales
# ("100 m" = 100 meters, never 100 million — SI convention). Multi-char
# scale words allow the space ("27.36 trillion", "5 mil").
# Attached UPPERCASE "T" ("$27.72T") is trillion; the (?-i:T) scope keeps
# it case-sensitive inside this IGNORECASE pattern so "5 t" (tonnes)
# never matches. "mil millones" precedes "mil" (ordered alternation).
# Every scale word carries ONE trailing-letter guard so prefixes never
# fabricate scale ("millionaires", "billonarios", "thousandth" take no
# scale); plurals are explicit ("millions" still works). Spaced "tn"
# stays trillion (pinned by a unit test; live eval cases pin attached
# "$27.36tn") even though "Tn" is also tonnes — documented collision,
# claims context wins.
_QUANTITY_RE = re.compile(
    r"(?P<symbol>[$€£¥])?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+,\d{1,2}(?!\d)|"
    r"\d{1,3}(?:\.\d{3})+,\d+|\d{1,3}(?:\.\d{3}){2,}(?![\d.])|\d{1,3}\.\d{3}(?![\d.])|\d+(?:\.\d+)?)"
    r"(?:\s*(?P<scale>trillions?|billions?|millions?|thousands?|hundreds?|"
    r"tn|bn|mn|billones|billón|billon|millones|millón|millon|"
    r"millardos|millardo|mil millones|mil)(?![a-zA-Záéíóúüñ/])"
    r"|(?P<scale_short>k(?![a-zA-Z/])|m(?![a-zA-Z/])|b(?![a-zA-Z/])|(?-i:T)(?![a-zA-Z/])))?"
    r"(?P<pct>\s*%)?",
    re.IGNORECASE,
)

# The optional "de" handles Spanish "5 millones de personas" (the
# preposition sits between scale word and unit). EN-safe: "de" + space is
# not an English construction after a bare number.
# Alternation is longest-first within each family: "percentage points"
# must precede "percent" (regex alternation is ordered, and the prefix
# would otherwise win). Bare "pp" needs a trailing guard so "2 ppm"
# (parts per million) does not read as percentage points.
# V5-2B: energy (kwh/mwh), area (km2 vs m2 kept DISTINCT — see aliases),
# linear length (meters + bare lowercase "m" per the SI convention noted
# in the scale guard above), ES counts (desempleados) and ES/spaced speed
# mirrors. Same longest-first rule: "metros cuadrados" and "metros por
# segundo" precede "metros"; bare "(?-i:m)" is last and case-sensitive so
# spaced uppercase "M" (million abbreviation) stays unitless.
_UNIT_AFTER_RE = re.compile(
    r"^\s*(?:de\s+)?(usd|us\$|dollars?|dólares|dolares|dólar|dolar|euros?|eur|gbp|pounds?|pesos?|libras?|jpy|yen|"
    r"percentage points?|percent|pp(?![a-zA-Z])|por ciento|puntos? porcentuales?|"
    r"people|personas?|inhabitants|habitantes?|deaths|muertes?|cases|casos?|desemplead[oa]s?|"
    r"kilowatt-hours?|kilowatt hours?|megawatt-hours?|megawatt hours?|kwh|mwh|"
    r"square kilometers?|square kilometres?|kil[oó]metros? cuadrados?|sq\.?\s*km|km2(?![a-zA-Z])|km²|"
    r"square meters?|square metres?|metros? cuadrados?|sq\.?\s*m(?![a-zA-Z/])|m2(?![a-zA-Z])|m²|"
    r"m/s|meters per second|metres per second|km/s|kilometers? per second|kilometres? per second|metros? por segundo|"
    r"meters?|metres?|metros?|(?-i:m)(?![a-zA-Z/])|"
    r"celsius|fahrenheit|kelvin|°c|°f|years?|años?|anos?)",
    re.IGNORECASE,
)

# V5-2B: megawatt-denominated quantities read in the canonical kwh unit
# scaled x1000 ("140 megawatt hours" -> 140,000 kwh). Matched against the
# raw unit text captured by _UNIT_AFTER_RE (case-insensitive).
_MEGAWATT_UNIT_RE = re.compile(r"^megawatt[-\s]?hours?$|^mwh$", re.IGNORECASE)


def normalize_unit(raw: str | None) -> str | None:
    if not raw:
        return None
    key = raw.strip().lower()
    if key in CURRENCY_SYMBOLS:
        return CURRENCY_SYMBOLS[key]
    return UNIT_ALIASES.get(key, raw.strip())


def units_compatible(a: str | None, b: str | None) -> bool:
    """Two units are comparable only if both are known and equal after norm.

    Unknown (None) units are NEVER silently treated as compatible.
    """
    na, nb = normalize_unit(a), normalize_unit(b)
    if na is None or nb is None:
        return False
    return na == nb


def parse_quantities(text: str, label: str = "") -> list[Quantity]:
    """Extract raw quantities with scale words, symbols and trailing units."""
    out: list[Quantity] = []
    for m in _QUANTITY_RE.finditer(text):
        raw_num = m.group("num")
        scale_word = m.group("scale") or m.group("scale_short") or ""
        # Exact-case lookup first ("T" = trillion), then lowercase.
        scale = SCALE_WORDS.get(
            scale_word, SCALE_WORDS.get(scale_word.lower(), 1.0)
        )
        if m.group("pct"):
            unit: str | None = "percent"
        elif m.group("symbol"):
            unit = CURRENCY_SYMBOLS.get(m.group("symbol"))
        else:
            after = text[m.end() : m.end() + 28]
            um = _UNIT_AFTER_RE.match(after)
            unit = um.group(1).strip() if um else None
        if unit is not None and _MEGAWATT_UNIT_RE.match(unit):
            # V5-2B: canonical energy unit is kwh — megawatt readings
            # scale x1000 ("140 megawatt hours" -> 140,000 kwh).
            scale = scale * 1000.0
        if _THOUSAND_DOT_MULTI_RE.fullmatch(raw_num):
            num = float(raw_num.replace(".", ""))
        elif _THOUSAND_DOT_COMMA_RE.fullmatch(raw_num):
            int_part, frac = raw_num.split(",")
            num = float(int_part.replace(".", "") + "." + frac)
        elif _THOUSAND_DOT_SINGLE_RE.fullmatch(raw_num):
            # Ambiguous "1.500": thousands only with a Spanish marker.
            u = unit or ""
            u = u[3:] if u.lower().startswith("de ") else u
            es_marker = (scale_word.lower() in _ES_SCALES
                         or u.lower() in _ES_UNITS)
            num = (float(raw_num.replace(".", "")) if es_marker
                   else float(raw_num))
        elif _DECIMAL_COMMA_RE.fullmatch(raw_num):
            num = float(raw_num.replace(",", "."))
        else:
            num = float(raw_num.replace(",", ""))
        value = num * scale
        norm_unit = normalize_unit(unit)
        out.append(
            Quantity(
                label=label,
                raw_text=m.group(0).strip(),
                value=value,
                unit=unit,
                normalized_value=value,
                normalized_unit=norm_unit,
            )
        )
    return out


def convert_temperature(value: float, from_unit: str, to_unit: str) -> float | None:
    fu, tu = normalize_unit(from_unit), normalize_unit(to_unit)
    if fu == tu:
        return value
    to_c: dict[str, object] = {
        "celsius": lambda v: v,
        "fahrenheit": lambda v: (v - 32) * 5 / 9,
        "kelvin": lambda v: v - 273.15,
    }
    from_c: dict[str, object] = {
        "celsius": lambda v: v,
        "fahrenheit": lambda v: v * 9 / 5 + 32,
        "kelvin": lambda v: v + 273.15,
    }
    if fu not in to_c or tu not in from_c:
        return None
    c = to_c[fu](value)  # type: ignore[operator]
    return from_c[tu](c)  # type: ignore[operator]


# UT-e: full scale WORDS (any case, ES accents optional) state an
# unambiguous scale. Single letters ("27.72T", "5 M") and bare digit
# strings ("27720000000000") do NOT: a letter is ambiguous (tonnes?)
# and a long digit run is typo-prone, so both stay conservative
# (scale slips abstain via possible_scale_error).
_EXPLICIT_SCALE_RE = re.compile(
    r"\b(trillions?|billions?|millions?|thousands?|hundreds?|billones?|"
    r"bill[oó]n(?:es)?|millardos?|millones?|mill[oó]n(?:es)?|mil|"
    r"centenas?|centenares?)\b",
    re.IGNORECASE,
)


def is_explicit_scale(raw: str) -> bool:
    """True when raw text states its scale in unambiguous words."""
    return bool(_EXPLICIT_SCALE_RE.search(raw or ""))


# UT-e: 10^k backstop powers (both directions; k a multiple of 3).
_SCALE_POWERS = (3, 6, 9, 12)
_SCALE_BAND = 0.01  # ±1% around the exact power


def scale_error_power(claim_value: float | None,
                      evidence_value: float | None) -> int | None:
    """Signed k when ev/claim sits within ±1% of 10^k, else None.

    k ranges over ±{3, 6, 9, 12} (thousand to trillion and back).
    Zero/None values never match; ratios beyond 1e12 (or below
    1e-12) are real hallucinations, not scale slips.
    """
    if not claim_value or not evidence_value:
        return None
    try:
        ratio = abs(float(evidence_value) / float(claim_value))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    for k in _SCALE_POWERS:
        target = 10.0 ** k
        if abs(ratio - target) / target <= _SCALE_BAND:
            return k
        inv = 1.0 / target
        if abs(ratio - inv) / inv <= _SCALE_BAND:
            return -k
    return None
