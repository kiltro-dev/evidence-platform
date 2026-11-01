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
    "north korea": "PRK",
    "corea del norte": "PRK",
    "south korea": "KOR",
    "korea": "KOR",
    "australia": "AUS",
    "spain": "ESP",
    "mexico": "MEX",
    "indonesia": "IDN",
    "netherlands": "NLD",
    "saudi arabia": "SAU",
    # NOTE: no "turkey" key — the bird/country collision is resolved by
    # case on the original text (_TURKEY_RE): capitalized Turkey -> TUR,
    # lowercase "turkey"/"turkeys" -> no match (fail-safe). ES "turquia"
    # is unambiguous (the bird is "pavo"/"guajolote").
    "turquía": "TUR",
    "turquia": "TUR",
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
    "northern ireland": "GBR",
    "irlanda del norte": "GBR",
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


# P0b: full names match with word boundaries, never as raw substrings
# ("Indiana" must not resolve IND, "otherworldly" must not resolve WLD).
# (?<!\w)/(?!\w) instead of \b so dotted abbreviations ("u.s.", "ee.uu.")
# keep working: \b fails after a trailing dot.
_KEY_RES: dict[str, re.Pattern] = {}


def _key_pattern(name: str) -> re.Pattern:
    pat = _KEY_RES.get(name)
    if pat is None:
        needle = re.escape(_strip_accents(name))
        if _strip_accents(name) == "america":
            # "America" alone (or "America's") is the USA, but regions
            # are not: Latin/North/South/Central plus synonym stems
            # (latinx, latino, hispanic, ibero, pan, inter, afro,
            # anglo, euro, luso) on the left, and Latina/Central/del
            # Sur/del Norte on the right. The scan text is
            # whitespace-normalized (see _full_name_spans), so single
            # spaces here cover tabs/newlines/hyphens/slashes/doubles.
            pat = re.compile(
                r"(?<!\w)(?<!latin )(?<!north )(?<!south )(?<!central )"
                r"(?<!latinx )(?<!latino )(?<!hispanic )(?<!ibero )"
                r"(?<!pan )(?<!inter )(?<!afro )(?<!anglo )(?<!euro )"
                r"(?<!luso )"
                + needle +
                r"(?!\s+latina)(?!\s+central)(?!\s+del\s+(?:sur|norte))(?!\w)"
            )
        elif _strip_accents(name) == "china":
            # Seas and regions are not the country: "South China Sea",
            # "China Sea", "Indo-China" (normalized to "indo china").
            # The \b keeps "China seaside resorts" matching (seaside
            # resorts in China ARE about China).
            pat = re.compile(
                r"(?<!\w)(?<!indo )(?<!south )" + needle +
                r"(?!\s+sea\b)(?!\w)"
            )
        else:
            pat = re.compile(r"(?<!\w)" + needle + r"(?!\w)")
        _KEY_RES[name] = pat
    return pat


# Whitespace/hyphen/slash runs collapse to one space for full-name
# matching so "Latin-America", "Latin/America" and "Latin\nAmerica"
# all hit the region guard. Spans are used for overlap-claiming only,
# so the shift vs original coordinates is harmless (same caveat as
# accent-strip). Slashes are safe here: no key contains "/" and no
# legit match depends on a slash boundary.
_WS_NORM_RE = re.compile(r"[\s\u2013\u2014\-/]+")


# Turkey/TURKEY (original text) is the country ONLY with economic
# context in the SAME clause and no bird words; otherwise it is the
# bird ("Turkey is delicious", "roast turkey", bare "Turkey") and
# matches nothing. Clause-locality stops cross-clause smuggling
# ("Turkey is a bird. Germany's GDP grew" must not flag multi-geo);
# the veto wins over context ("Turkey is delicious and GDP grew" is
# still the bird). Failing safe toward unverifiable.
_TURKEY_RE = re.compile(r"(?<![\w])Turkey(?![\w])|(?<![\w])TURKEY(?![\w])")
_TURKEY_CONTEXT_RE = re.compile(
    r"gdp|pib|unemployment|desempleo|inflation|inflaci[oó]n|population|"
    r"poblaci[oó]n|exports?|imports?|exportaciones|importaciones|debt|"
    r"deuda|deficit|d[eé]ficit|surplus|super[aá]vit|growth|crecimiento|"
    r"recession|recesi[oó]n|economy|economies|econom[ií]a|grew|grown|"
    r"rose|risen|fell|fallen|prices?|precios|"
    r"lira|bank|banco|rates?|tasas?|trade|comercio|market|mercado|"
    r"percent|por\s*ciento|%|millions?|billions?|"
    r"trillions?|millones|billones|usd|d[oó]lar|\$|€|£",
    re.IGNORECASE,
)
# Short veto words carry \b so econ text never trips them ("debate"
# must not veto via "ate", "prices rose" stays econ — hence NO bare
# "price" here; grocery is covered by store/shop/sale/cost).
_TURKEY_VETO_RE = re.compile(
    r"delicious|roast|eating|sandwich|stuffing|thanksgiving|dinner|"
    r"recipe|bourbon|charity|grocery|supermarket|"
    r"\b(ate|bird|wild|farm|hunt|store|shop|trot|sale|costs?)\b",
    re.IGNORECASE,
)


def _turkey_span(text: str) -> tuple[int, int] | None:
    for m in _TURKEY_RE.finditer(text):
        start = text.rfind(".", 0, m.start())
        start = max(start, text.rfind("!", 0, m.start()))
        start = max(start, text.rfind("?", 0, m.start()))
        start = max(start, text.rfind(";", 0, m.start()))
        end = len(text)
        for sep in (".", "!", "?", ";"):
            at = text.find(sep, m.end())
            if at != -1:
                end = min(end, at)
        clause = text[start + 1:end]
        if _TURKEY_VETO_RE.search(clause):
            continue
        if _TURKEY_CONTEXT_RE.search(clause):
            return (m.start(), m.end())
    return None


def _full_name_spans(lowered: str) -> list[tuple[int, int, str]]:
    """(start, end, key) for every boundary-respecting full-name hit."""
    scan = _WS_NORM_RE.sub(" ", lowered)
    out: list[tuple[int, int, str]] = []
    for name in sorted(COUNTRIES, key=len, reverse=True):
        for m in _key_pattern(name).finditer(scan):
            out.append((m.start(), m.end(), name))
    return out


def resolve_all_countries(text: str) -> list[str]:
    """Return distinct ISO3s mentioned in `text` (longest-match order).

    Full names match case- and accent-insensitively WITH word
    boundaries, longest first with span claiming (an accepted match
    owns its span; same-ISO3 overlaps collapse). Short codes
    (US/USA/UK/EU) match as standalone UPPERCASE tokens only and are
    appended after full-name matches. Capitalized "Turkey" resolves
    via case rule (lowercase turkey = the bird, no match).
    """
    lowered = _strip_accents(text.lower())
    found: list[str] = []
    claimed: list[tuple[int, int]] = []
    for start, end, name in _full_name_spans(lowered):
        if any(s < end and start < e for s, e in claimed):
            continue
        claimed.append((start, end))
        iso3 = COUNTRIES[name]
        if iso3 not in found:
            found.append(iso3)
    tspan = _turkey_span(text)
    if tspan is not None and not any(
            s < tspan[1] and tspan[0] < e for s, e in claimed):
        claimed.append(tspan)
        if "TUR" not in found:
            found.append("TUR")
    for start, end, code in _short_code_spans(text):
        if any(s < end and start < e for s, e in claimed):
            continue
        claimed.append((start, end))
        iso3 = SHORT_CODES[code]
        if iso3 not in found:
            found.append(iso3)
    return found


def resolve_country(text: str) -> tuple[str | None, str | None]:
    """Return (display_name, iso3) for the best country match, else (None, None).

    Full names match case- and accent-insensitively WITH word
    boundaries ("México" and "Mexico" both resolve to MEX; "Indiana"
    resolves nothing), longest match wins with earliest-span tie-break
    ("United States and China" resolves USA by length; "South Korea
    and North Korea" resolves South Korea by position); the returned
    display name is the canonical dict key.
    Turkey/TURKEY resolve only with economic context (else the bird);
    short codes (US/USA/UK/EU) match as standalone UPPERCASE tokens
    only (SHORT_CODES) and lose to any full-name match.
    """
    lowered = _strip_accents(text.lower())
    # longest names first so "united states" wins over shorter overlaps
    spans = _full_name_spans(lowered)
    if spans:
        best = max(len(n) for _, _, n in spans)
        start, _, name = min(s for s in spans if len(s[2]) == best)
        return name, COUNTRIES[name]
    if _turkey_span(text) is not None:
        return "Turkey", "TUR"
    spans = _short_code_spans(text)
    if spans:
        _, _, code = spans[0]
        return code, SHORT_CODES[code]
    return None, None
