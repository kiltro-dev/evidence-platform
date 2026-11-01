"""Country name -> ISO3 resolution for statistical connectors."""

from __future__ import annotations

import re
import unicodedata


def _strip_accents(s: str) -> str:
    """Fold accented characters to ASCII (méxico -> mexico)."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


# Short all-caps codes match as standalone UPPERCASE tokens only (never
# substrings, never lowercase): bare "us"/"usa" collide with the English
# pronoun, the Spanish verb ("usa"), and plain words ("usage"), and "eu"
# collides with "euro". Full names below stay case-insensitive
# substrings. Sloppy-lowercase codes ("travel to usa") degrade to
# geo-less, which fails safe toward unverifiable.
SHORT_CODES: dict[str, str] = {
    "USA": "USA",
    "US": "USA",
    "UK": "GBR",
    "EU": "EUU",
}
_SHORT_CODE_RE = re.compile(r"\b(USA|US|UK|EU)\b")

COUNTRIES: dict[str, str] = {
    "united states": "USA",
    "u.s.": "USA",
    "u.s.a.": "USA",
    "ee.uu.": "USA",
    "ee. uu.": "USA",
    "america": "USA",
    "china": "CHN",
    "japan": "JPN",
    "germany": "DEU",
    "united kingdom": "GBR",
    "u.k.": "GBR",
    "britain": "GBR",
    "france": "FRA",
    "india": "IND",
    "italy": "ITA",
    "brazil": "BRA",
    "canada": "CAN",
    "russia": "RUS",
    "south korea": "KOR",
    "korea": "KOR",
    "australia": "AUS",
    "spain": "ESP",
    "mexico": "MEX",
    "indonesia": "IDN",
    "netherlands": "NLD",
    "saudi arabia": "SAU",
    "turkey": "TUR",
    "taiwan": "TWN",
    "switzerland": "CHE",
    "poland": "POL",
    "argentina": "ARG",
    "sweden": "SWE",
    "belgium": "BEL",
    "thailand": "THA",
    "israel": "ISR",
    "austria": "AUT",
    "norway": "NOR",
    "ireland": "IRL",
    "singapore": "SGP",
    "malaysia": "MYS",
    "south africa": "ZAF",
    "philippines": "PHL",
    "colombia": "COL",
    "chile": "CHL",
    "peru": "PER",
    "new zealand": "NZL",
    "portugal": "PRT",
    "greece": "GRC",
    "denmark": "DNK",
    "finland": "FIN",
    "egypt": "EGY",
    "nigeria": "NGA",
    "kenya": "KEN",
    "vietnam": "VNM",
    "bangladesh": "BGD",
    "pakistan": "PAK",
    "ukraine": "UKR",
    "world": "WLD",
    "global": "WLD",
    "european union": "EUU",
    # --- Spanish (ES) aliases (canonical keys keep their accents; matching
    # is accent-insensitive so unaccented spellings work too) ---
    "estados unidos": "USA",
    "américa": "USA",
    "alemania": "DEU",
    "reino unido": "GBR",
    "gran bretaña": "GBR",
    "españa": "ESP",
    "méxico": "MEX",
    "japón": "JPN",
    "francia": "FRA",
    "italia": "ITA",
}


COUNTRIES.update(
    {
        "brasil": "BRA",
        "canadá": "CAN",
        "rusia": "RUS",
        "corea del sur": "KOR",
        "corea": "KOR",
        "perú": "PER",
        "grecia": "GRC",
        "países bajos": "NLD",
        "arabia saudita": "SAU",
        "turquía": "TUR",
        "suiza": "CHE",
        "polonia": "POL",
        "suecia": "SWE",
        "bélgica": "BEL",
        "tailandia": "THA",
        "noruega": "NOR",
        "irlanda": "IRL",
        "singapur": "SGP",
        "malasia": "MYS",
        "sudáfrica": "ZAF",
        "filipinas": "PHL",
        "nueva zelanda": "NZL",
        "dinamarca": "DNK",
        "finlandia": "FIN",
        "egipto": "EGY",
        "kenia": "KEN",
        "ucrania": "UKR",
        "mundo": "WLD",
        "unión europea": "EUU",
    }
)


def _short_code_spans(text: str) -> list[tuple[int, int, str]]:
    """Standalone-uppercase short-code matches on the ORIGINAL text.

    Case-sensitive by design (see SHORT_CODES): the "us" pronoun, the
    Spanish "usa" verb, and "usage"/"euro"-style words must never
    resolve. Spans are original-text coordinates; accent-stripping is
    length-preserving for precomposed Latin, so they coincide with
    lowered-text spans for realistic inputs.
    """
    return [(m.start(), m.end(), m.group(1))
            for m in _SHORT_CODE_RE.finditer(text)]


def resolve_all_countries(text: str) -> list[str]:
    """Return distinct ISO3s mentioned in `text` (longest-match order).

    Full names match as case- and accent-insensitive substrings,
    longest first with span claiming (an accepted match owns its
    span; same-ISO3 overlaps collapse). Short codes (US/USA/UK/EU)
    match as standalone UPPERCASE tokens only and are appended after
    full-name matches.
    """
    lowered = _strip_accents(text.lower())
    found: list[str] = []
    claimed: list[tuple[int, int]] = []
    for name in sorted(COUNTRIES, key=len, reverse=True):
        needle = _strip_accents(name)
        if not needle:
            continue
        start = 0
        matched = False
        while True:
            at = lowered.find(needle, start)
            if at < 0:
                break
            span = (at, at + len(needle))
            start = at + 1
            if any(s < span[1] and span[0] < e for s, e in claimed):
                continue
            claimed.append(span)
            matched = True
        if matched:
            iso3 = COUNTRIES[name]
            if iso3 not in found:
                found.append(iso3)
    for start, end, code in _short_code_spans(text):
        if any(s < end and start < e for s, e in claimed):
            continue
        claimed.append((start, end))
        iso3 = SHORT_CODES[code]
        if iso3 not in found:
            found.append(iso3)
    return found


def resolve_country(text: str) -> tuple[str | None, str | None]:
    """Return (display_name, iso3) for the first country mentioned, else (None, None).

    Full names match case- and accent-insensitively ("México" and
    "Mexico" both resolve to MEX), longest first; the returned display
    name is the canonical dict key. Short codes (US/USA/UK/EU) match
    as standalone UPPERCASE tokens only (SHORT_CODES) and lose to any
    full-name match; the returned display name is the code as written.
    """
    lowered = _strip_accents(text.lower())
    # longest names first so "united states" wins over shorter overlaps
    for name in sorted(COUNTRIES, key=len, reverse=True):
        if _strip_accents(name) in lowered:
            return name, COUNTRIES[name]
    spans = _short_code_spans(text)
    if spans:
        _, _, code = spans[0]
        return code, SHORT_CODES[code]
    return None, None
