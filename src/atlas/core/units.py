"""Deterministic unit parsing and normalization. No models here."""

from __future__ import annotations

import re

from .types import Quantity

SCALE_WORDS: dict[str, float] = {
    "trillion": 1e12,
    "tn": 1e12,
    "billion": 1e9,
    # "b" (attached only, e.g. "$27b") = billion; bare "t" is DELIBERATELY
    # absent — "5 t" reads as 5 tonnes, never 5 trillion (M6 finding).
    "b": 1e9,
    "bn": 1e9,
    "million": 1e6,
    "mn": 1e6,
    "m": 1e6,
    "thousand": 1e3,
    "k": 1e3,
    "hundred": 1e2,
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

# Single-letter scales (k/m/b) count ONLY when attached to the digits
# ("$27m", "5k"): space-separated single letters are units, not scales
# ("100 m" = 100 meters, never 100 million — SI convention). Multi-char
# scale words allow the space ("27.36 trillion", "5 mil").
_QUANTITY_RE = re.compile(
    r"(?P<symbol>[$€£¥])?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+,\d{1,2}(?!\d)|\d+(?:\.\d+)?)"
    r"(?:\s*(?P<scale>trillion|billion|million|thousand|hundred|tn|bn|mn|"
    r"billones|billón|billon|millones|millón|millon|millardos|millardo|"
    r"mil(?![a-zA-Z/]))"
    r"|(?P<scale_short>k(?![a-zA-Z/])|m(?![a-zA-Z/])|b(?![a-zA-Z/])))?"
    r"(?P<pct>\s*%)?",
    re.IGNORECASE,
)

# The optional "de" handles Spanish "5 millones de personas" (the
# preposition sits between scale word and unit). EN-safe: "de" + space is
# not an English construction after a bare number.
_UNIT_AFTER_RE = re.compile(
    r"^\s*(?:de\s+)?(usd|us\$|dollars?|dólares|dolares|dólar|dolar|eur|euros?|gbp|pounds?|libras?|jpy|yen|"
    r"percent|percentage points?|por ciento|puntos? porcentuales?|"
    r"people|personas?|inhabitants|habitantes?|deaths|muertes?|cases|casos?|"
    r"m/s|meters per second|metres per second|km/s|"
    r"celsius|fahrenheit|kelvin|°c|°f|years?|años?|anos?)",
    re.IGNORECASE,
)


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
        if _DECIMAL_COMMA_RE.fullmatch(raw_num):
            num = float(raw_num.replace(",", "."))
        else:
            num = float(raw_num.replace(",", ""))
        scale_word = (
            m.group("scale") or m.group("scale_short") or ""
        ).lower()
        scale = SCALE_WORDS.get(scale_word, 1.0)
        if m.group("pct"):
            unit: str | None = "percent"
        elif m.group("symbol"):
            unit = CURRENCY_SYMBOLS.get(m.group("symbol"))
        else:
            after = text[m.end() : m.end() + 28]
            um = _UNIT_AFTER_RE.match(after)
            unit = um.group(1).strip() if um else None
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
