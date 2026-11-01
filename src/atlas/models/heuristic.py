"""Heuristic (regex/keyword) claim extractor.

Degraded-mode fallback when no LLM is available, and a deterministic
baseline for evaluation. It never guesses beyond explicit patterns:
what it cannot parse becomes an explicit uncertainty downstream.
"""

from __future__ import annotations

import re

from ..connectors.countries import resolve_country
from ..connectors.worldbank import match_indicator
from ..core.types import GeoRef, StructuredClaim, TimeRef
from ..core.units import parse_quantities

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_RANGE_RE = re.compile(r"\b((?:19|20)\d{2})\s*[–—-]\s*((?:19|20)\d{2})\b")

ECONOMY_KEYWORDS_EN = (
    "gdp",
    "gross domestic product",
    "inflation",
    "unemployment",
    "population",
    "exports",
    "imports",
    "debt",
    "consumer prices",
    "per capita",
    "recession",
    "deficit",
    "interest rate",
)

# Spanish economy keywords. Accented and unaccented variants are both
# listed because domain matching is a plain substring check (no accent
# folding here; see countries.resolve_country for accent handling).
ECONOMY_KEYWORDS_ES = (
    "pib",
    "producto interno bruto",
    "producto interior bruto",
    "desempleo",
    "inflación",
    "inflacion",
    "población",
    "poblacion",
    "exportaciones",
    "importaciones",
    "deuda",
    "precios al consumidor",
    "per cápita",
    "per capita",
    "recesión",
    "recesion",
    "déficit",
    "deficit",
    "tasa de interés",
    "tasa de interes",
    "tipo de interés",
    "tipo de interes",
)

ECONOMY_KEYWORDS = ECONOMY_KEYWORDS_EN + ECONOMY_KEYWORDS_ES

SCIENCE_KEYWORDS_EN = (
    "study",
    "studies",
    "research",
    "paper",
    "published",
    "journal",
    "experiment",
    "vaccine",
    "climate",
    "species",
    "genome",
    "particle",
    "speed of light",
    "temperature",
    "boiling",
    "melting",
    "scientists",
    "researchers",
    "trial",
)

SCIENCE_KEYWORDS_ES = (
    "estudio",
    "estudios",
    "investigación",
    "investigacion",
    "artículo",
    "articulo",
    "publicado",
    "publicada",
    "revista",
    "experimento",
    "vacuna",
    "clima",
    "especie",
    "especies",
    "genoma",
    "partícula",
    "particula",
    "velocidad de la luz",
    "temperatura",
    "ebullición",
    "ebullicion",
    "fusión",
    "fusion",
    "científicos",
    "cientificos",
    "investigadores",
    "ensayo",
)

SCIENCE_KEYWORDS = SCIENCE_KEYWORDS_EN + SCIENCE_KEYWORDS_ES

COMPARATIVE_WORDS_EN = (
    "higher than",
    "lower than",
    "more than",
    "less than",
    "greater than",
    "faster than",
    "twice",
    "double",
    "increased",
    "decreased",
    "rose",
    "fell",
    "grew",
    "compared",
)

COMPARATIVE_WORDS_ES = (
    "mayor que",
    "menor que",
    "más que",
    "mas que",
    "menos que",
    "superior a",
    "inferior a",
    "doble",
    "triple",
    "el doble",
    "aumentó",
    "aumento",
    "aumentaron",
    "incrementó",
    "incremento",
    "disminuyó",
    "disminuyo",
    "disminuyeron",
    "cayó",
    "cayo",
    "creció",
    "crecio",
    "comparado",
    "comparada",
    "en comparación con",
    "en comparacion con",
)

COMPARATIVE_WORDS = COMPARATIVE_WORDS_EN + COMPARATIVE_WORDS_ES


def classify_domain(text: str) -> str:
    lowered = text.lower()
    eco = sum(1 for k in ECONOMY_KEYWORDS if k in lowered)
    sci = sum(1 for k in SCIENCE_KEYWORDS if k in lowered)
    if eco == 0 and sci == 0:
        return "general"
    return "economy" if eco >= sci else "science"


def classify_claim_type(text: str) -> str:
    lowered = text.lower()
    if any(w in lowered for w in COMPARATIVE_WORDS):
        return "comparative"
    if parse_quantities(text):
        return "quantitative"
    if re.search(
        r"\b(is|are|was|were|has|have|exists?|"
        r"es|son|fue|fueron|hay|existe|existen|tiene|tienen)\b",
        lowered,
    ):
        return "existence"
    return "other"


def extract_times(text: str) -> list[TimeRef]:
    times: list[TimeRef] = []
    for m in _RANGE_RE.finditer(text):
        times.append(
            TimeRef(
                raw=m.group(0),
                start_year=int(m.group(1)),
                end_year=int(m.group(2)),
                period_type="range",
            )
        )
    covered = {m.group(0) for m in _RANGE_RE.finditer(text)}
    for m in _YEAR_RE.finditer(text):
        if any(m.group(0) in c for c in covered):
            continue
        times.append(
            TimeRef(raw=m.group(0), year=int(m.group(0)), period_type="year")
        )
    if re.search(
        r"\b(recently|lately|nowadays|currently|today|"
        r"recientemente|actualmente|hoy|últimamente|ultimamente|ahora)\b",
        text.lower(),
    ):
        times.append(TimeRef(raw="vague present reference", period_type="vague"))
    return times


def extract_heuristic(raw_text: str) -> StructuredClaim:
    text = raw_text.strip()
    quantities = parse_quantities(text)
    name, iso3 = resolve_country(text)
    matched = match_indicator(text)
    geo = [GeoRef(raw=name or "", name=name, iso3=iso3)] if iso3 else []
    subject = text[:160]
    return StructuredClaim(
        raw_text=text,
        domain=classify_domain(text),  # type: ignore[arg-type]
        claim_type=classify_claim_type(text),  # type: ignore[arg-type]
        subject=subject,
        indicator=matched[0] if matched else None,
        entities=([name] if name else []),
        quantities=quantities,
        time_refs=extract_times(text),
        geo_refs=geo,
        conditions=[],
        context_notes="",
        extraction_actor="heuristic",
        extraction_model=None,
    )
