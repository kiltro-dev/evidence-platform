"""Country name -> ISO3 resolution for statistical connectors."""

from __future__ import annotations

import unicodedata


def _strip_accents(s: str) -> str:
    """Fold accented characters to ASCII (méxico -> mexico)."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )

COUNTRIES: dict[str, str] = {
    "united states": "USA",
    "usa": "USA",
    "u.s.": "USA",
    "u.s.a.": "USA",
    "america": "USA",
    "china": "CHN",
    "japan": "JPN",
    "germany": "DEU",
    "united kingdom": "GBR",
    "uk": "GBR",
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
    "eu": "EUU",
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


def resolve_all_countries(text: str) -> list[str]:
    """Return distinct ISO3s mentioned in `text` (longest-match order).

    Same matching semantics as resolve_country (case- and
    accent-insensitive substring; longest names first so "united states"
    wins over shorter overlaps), but collects every distinct ISO3
    instead of returning only the first. Longest-first span claiming:
    an accepted match owns its character span, so "uk" inside
    "ukraine" never becomes a phantom second geography (lead fix;
    without it every Ukraine claim mis-flagged multi-geo). Overlapping
    names mapping to the same ISO3 ("korea"/"south korea") collapse
    to one entry. Known limitation (shared with resolve_country):
    short keys are plain substrings, so "eu" matches inside "euro".
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
    return found


def resolve_country(text: str) -> tuple[str | None, str | None]:
    """Return (display_name, iso3) for the first country mentioned, else (None, None).

    Matching is case- and accent-insensitive: "México" and "Mexico" both
    resolve to MEX. The returned display name is the canonical dict key.
    For pure-ASCII (EN) inputs normalization is the identity, so EN
    behavior is unchanged.
    """
    lowered = _strip_accents(text.lower())
    # longest names first so "united states" wins over shorter overlaps
    for name in sorted(COUNTRIES, key=len, reverse=True):
        if _strip_accents(name) in lowered:
            return name, COUNTRIES[name]
    return None, None
