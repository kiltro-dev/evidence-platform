"""Country name -> ISO3 resolution for statistical connectors."""

from __future__ import annotations

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
}


def resolve_country(text: str) -> tuple[str | None, str | None]:
    """Return (display_name, iso3) for the first country mentioned, else (None, None)."""
    lowered = text.lower()
    # longest names first so "united states" wins over shorter overlaps
    for name in sorted(COUNTRIES, key=len, reverse=True):
        if name in lowered:
            return name, COUNTRIES[name]
    return None, None
