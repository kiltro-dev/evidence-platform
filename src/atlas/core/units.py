"""Deterministic unit parsing and normalization. No models here."""

from __future__ import annotations

import re

from .types import Quantity

SCALE_WORDS: dict[str, float] = {
    "trillion": 1e12,
    "tn": 1e12,
    "billion": 1e9,
    "bn": 1e9,
    "million": 1e6,
    "mn": 1e6,
    "m": 1e6,
    "thousand": 1e3,
    "k": 1e3,
    "hundred": 1e2,
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

_QUANTITY_RE = re.compile(
    r"(?P<symbol>[$€£¥])?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<scale>trillion|billion|million|thousand|hundred|tn|bn|mn|"
    r"k(?![a-zA-Z/])|m(?![a-zA-Z/])|b(?![a-zA-Z/]))?"
    r"(?P<pct>\s*%)?",
    re.IGNORECASE,
)

_UNIT_AFTER_RE = re.compile(
    r"^\s*(usd|us\$|dollars?|eur|euros?|gbp|pounds?|jpy|yen|percent|percentage points?|"
    r"people|inhabitants|deaths|cases|m/s|meters per second|metres per second|km/s|"
    r"celsius|fahrenheit|kelvin|°c|°f|years?)",
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
        num = float(m.group("num").replace(",", ""))
        scale_word = (m.group("scale") or "").lower()
        scale = SCALE_WORDS.get(scale_word, 1.0)
        if m.group("pct"):
            unit: str | None = "percent"
        elif m.group("symbol"):
            unit = CURRENCY_SYMBOLS.get(m.group("symbol"))
        else:
            after = text[m.end() : m.end() + 28]
            um = _UNIT_AFTER_RE.match(after)
            unit = um.group(0).strip() if um else None
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
