"""EvidencePackage contract for Atlas Live M2a.

A package binds one claim to one captured source snapshot through a
literal quote. It is a frozen dataclass: structural validation runs in
``__post_init__`` and raises ``ValueError`` on any violation, so an
invalid package can never be constructed silently.

Snapshots live under ``data/snap/`` (git-ignored via ``data/``); the
package carries only the path + sha256, never the snapshot bytes.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Literal

SourceType = Literal["primary", "secondary"]
Extractor = Literal["model", "code"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Shared numbers[].unit vocabulary (M2c alignment, single source of truth).
# Canonical units are the normalized targets of src/atlas/core/units.py
# (normalize_unit); None means "no unambiguous unit marker". The M2b
# researcher emits a subset (USD/percent/None today); the M2a verifier
# re-extracts numbers from the quote and never trusts numbers[] blindly,
# so the vocab keeps both sides forward-compatible without changing
# verdicts. Anything outside the vocab is rejected at construction.
UNIT_VOCAB = frozenset({
    "USD", "EUR", "GBP", "JPY",
    "percent", "pp", "count",
    "m/s", "km/s",
    "celsius", "fahrenheit", "kelvin",
    "years",
    # V5-2B: canonical units for the general EN/ES parser extension
    # (src/atlas/core/units.py): energy, area (km2 vs m2 DISTINCT),
    # linear length.
    "kwh", "km2", "m2", "meters",
})

# Hosts whose snapshots count as "primary" sources (M2c alignment,
# single source of truth shared by swarm + verifier sides). Official
# statistics APIs; everything else (incl. Wikipedia) is secondary.
PRIMARY_HOSTS = (
    "api.worldbank.org",
    "data.worldbank.org",
    "worldbank.org",
    "fred.stlouisfed.org",
    "ourworldindata.org",
    "query.wikidata.org",
    "wikidata.org",
)


def source_type_for_url(url: str) -> SourceType:
    """Canonical primary/secondary criterion shared by M2a and M2b.

    Primary = snapshot host is an official-statistics host (PRIMARY_HOSTS,
    exact or subdomain). Secondary = everything else. Pure function of
    the URL: producer (swarm) and consumer (store/verifier audit) agree
    by construction.
    """
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return "secondary"
    if any(host == h or host.endswith("." + h) for h in PRIMARY_HOSTS):
        return "primary"
    return "secondary"


# Curated World Bank indicator -> canonical unit map (M2d recall fix,
# cause 1: unitless WB JSON quotes). The indicator id is NEVER trusted
# from the producer: source_meta_for_snapshot() re-derives it from the
# snapshot bytes, and the verifier re-checks it the same way. Values are
# full-precision (scale 1): what the API row says is what it means.
# Extension policy: add an indicator ONLY when its WB metadata name pins
# one unambiguous UNIT_VOCAB unit (e.g. "GDP (current US$)" -> USD).
# V5-2B revision (measured on the EN bench): percent-denominated rate
# indicators ARE directly comparable to single-year percent claims (a
# level rate, not a derivation: "grew 2.9 percent in 2024" vs the 2024
# row), and unit-compatibility (percent != count/USD) already blocks the
# total-vs-rate confusion the old growth-rate exclusion feared. What
# stays OUT: (a) PPP international dollars (NY.GDP.MKTP.PP.CD: "current
# international $" is not USD — mapping it would verify USD claims
# against PPP rows); (b) composite per-capita emissions
# (EN.GHG.ALL.PC.CE.AR5: "t CO2e/capita" has no canonical unit in scope).
# EG.USE.ELEC.KH.PC ("kWh per capita") maps to kwh: the UNIT is
# unequivocal and every kWh claim in the corpus is per-person (audited
# EN/ES/dev/truth — no total-kWh claim exists), so the qualifier is
# symmetric in practice; the theoretical total-vs-per-capita confusion
# is documented in live/V5_2B.md with a qualifier-units follow-up.
# All names below verified against the WB API metadata
# (api.worldbank.org/v2/indicator/<code>, 2026-11-02).
WB_INDICATOR_UNITS = {
    # "Population, total" (SP.POP.TOTL) -> headcount.
    "SP.POP.TOTL": "count",
    # "GDP (current US$)" (NY.GDP.MKTP.CD) -> nominal dollars.
    "NY.GDP.MKTP.CD": "USD",
    # --- V5-2B: percent-denominated (name states % explicitly) ---
    # "Inflation, consumer prices (annual %)" -> percent.
    "FP.CPI.TOTL.ZG": "percent",
    # "Unemployment, total (% of total labor force) (modeled ILO
    # estimate)" -> percent.
    "SL.UEM.TOTL.ZS": "percent",
    # "GDP growth (annual %)" -> percent.
    "NY.GDP.MKTP.KD.ZG": "percent",
    # "Population growth (annual %)" -> percent.
    "SP.POP.GROW": "percent",
    # "Access to electricity (% of population)" -> percent.
    "EG.ELC.ACCS.ZS": "percent",
    # "Forest area (% of land area)" -> percent.
    "AG.LND.FRST.ZS": "percent",
    # "Urban population (% of total population)" -> percent.
    "SP.URB.TOTL.IN.ZS": "percent",
    # "Individuals using the Internet (% of population)" -> percent.
    "IT.NET.USER.ZS": "percent",
    # --- V5-2B: years / headcount / area / energy ---
    # "Life expectancy at birth, total (years)" -> years.
    "SP.DYN.LE00.IN": "years",
    # "Urban population" -> headcount (parallel to SP.POP.TOTL).
    "SP.URB.TOTL": "count",
    # "Land area (sq. km)" -> km2.
    "AG.LND.TOTL.K2": "km2",
    # "Electric power consumption (kWh per capita)" -> kwh (see
    # policy note above on the symmetric per-capita qualifier).
    "EG.USE.ELEC.KH.PC": "kwh",
}

# V5-2B: annual-rate indicators (WB metadata name states "annual %").
# A MONTHLY-frequency claim ("rose 219.9 percent every month", P1-36) is
# unverifiable from these rows: the annual figure never engages the
# monthly figure, so the verifier abstains (frequency_mismatch) instead
# of supporting on a coincidental numeric match. General: indicator
# codes + claim-text pattern, never claim ids.
WB_ANNUAL_RATE_INDICATORS = frozenset({
    "FP.CPI.TOTL.ZG",
    "NY.GDP.MKTP.KD.ZG",
    "SP.POP.GROW",
})

_MONTHLY_FREQUENCY_RES = (
    re.compile(
        r"\b(every month|per month|each month|that month|this month"
        r"|monthly)\b",
        re.IGNORECASE),
    re.compile(
        r"\b(mensual(?:es)?|cada mes|por mes|al mes)\b",
        re.IGNORECASE),
)


def claim_has_monthly_frequency(text: str) -> bool:
    """True when the claim asserts a MONTHLY-frequency figure (V5-2B).

    General EN/ES pattern over the claim TEXT only — never claim ids.
    Pure, never raises (non-string input reads as no frequency).
    """
    try:
        doc = text or ""
    except Exception:
        return False
    try:
        return any(rx.search(doc) for rx in _MONTHLY_FREQUENCY_RES)
    except Exception:
        return False

# Level assertions ("doubled/grew/fell TO x") state the entity's own level and
# ARE directly comparable to a same-indicator row. Everything else matching
# _COMPUTED_FIGURE_RES states a figure computed across rows/years (average,
# percent change, share-of) that no single API row can confirm or refute.
_LEVEL_ASSERTION_RE = re.compile(
    r"\b(doubled|tripled|quadrupled|grew|grown|growing|rose|risen|fell"
    r"|fallen|dropped|declined|decline|reached|climbed|surged|jumped)\s+to\b",
    re.IGNORECASE,
)

_COMPUTED_FIGURE_RES = (
    re.compile(r"\baverag(?:e|ed|es|ing)\b|\bmean\b|\bmedian\b",
               re.IGNORECASE),
    re.compile(r"\bpercent\s+of\b", re.IGNORECASE),
    re.compile(
        r"\b(grew|growth|grown|increased|increase|decreased|decrease|rose"
        r"|risen|fell|fallen|declined|decline|change[sd]?)\b"
        r"[^.?!]{0,60}\bpercent\b",
        re.IGNORECASE),
    re.compile(r"\bfrom\s+(19|20|21)\d{2}\s+to\s+(19|20|21)\d{2}\b",
               re.IGNORECASE),
)


def is_multi_source_arithmetic(text: str) -> bool:
    """True when the claim figure is computed across rows/years (F2-2).

    Average/mean/median, percent change, share-of ("N percent of"), or an
    explicit from-year-to-year span: no single snapshot row holds the
    figure, so a single-quote numeric mismatch is not a sound refutation
    (the verifier must abstain, never contradict, on component evidence).
    Level assertions ("doubled/grew/fell TO x") return False: x is the
    entity's own level and stays directly comparable. Pure, never raises.
    """
    try:
        doc = text or ""
    except Exception:
        return False
    if _LEVEL_ASSERTION_RE.search(doc):
        return False
    return any(rx.search(doc) for rx in _COMPUTED_FIGURE_RES)


_WB_API_HOSTS = ("api.worldbank.org",)
_FRED_API_HOSTS = ("api.stlouisfed.org",)


def _fred_meta_for_snapshot(url: str, snapshot_text: str) -> "SourceMeta | None":
    """FRED observations metadata, or None (V7; pure leaf of below).

    Host must be api.stlouisfed.org, ``series_id`` in the query must be
    a curated FRED_SERIES id, and the bytes must parse as a FRED
    observations object (``observations[]`` with date/value rows).
    The series id comes from the code-built request URL (altroutes
    candidates are model-unreachable), the values from the bytes.
    """
    try:
        from live.swarm import fred as _fred  # lazy: swarm/__init__ cycle
    except Exception:
        return None
    try:
        parts = urllib.parse.urlsplit(url or "")
        query = urllib.parse.parse_qs(parts.query or "")
        sid = (query.get("series_id") or [""])[0].strip().upper()
    except (ValueError, AttributeError, TypeError):
        return None
    spec = _fred.FRED_SERIES.get(sid)
    if not spec:
        return None
    try:
        rows = _fred.parse_observations(snapshot_text or "")
    except Exception:
        return None
    if not rows:
        return None
    return SourceMeta(indicator=sid, unit=str(spec["unit"]),
                      scale=float(spec["scale"]))


def source_meta_for_snapshot(url: str, snapshot_text: str) -> "SourceMeta | None":
    """Structured-source metadata for a snapshot, or None.

    Applies ONLY to World Bank API JSON (``[meta, rows]`` with
    ``rows[].indicator.id``) and (V7) FRED observations JSON: returns
    the single consistent indicator id plus its curated unit when
    every row agrees and the indicator is curated. Anything else
    (prose, CSV, mixed/unknown indicators, malformed JSON, other
    hosts) -> None. Never raises: the producer must never crash on an
    odd snapshot.
    """
    try:
        host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return None
    if any(host == h or host.endswith("." + h) for h in _FRED_API_HOSTS):
        return _fred_meta_for_snapshot(url, snapshot_text)
    if not any(host == h or host.endswith("." + h) for h in _WB_API_HOSTS):
        return None
    try:
        import json

        data = json.loads(snapshot_text or "")
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list) or len(data) < 2:
        return None
    rows = data[1]
    if not isinstance(rows, list) or not rows:
        return None
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        indicator = row.get("indicator")
        code = indicator.get("id") if isinstance(indicator, dict) else None
        if isinstance(code, str) and code.strip():
            ids.add(code.strip().upper())
    if len(ids) != 1:
        return None
    (code,) = ids
    unit = WB_INDICATOR_UNITS.get(code)
    if unit is None:
        return None
    return SourceMeta(indicator=code, unit=unit, scale=1.0)


@dataclass(frozen=True)
class SourceMeta:
    """Structured-source metadata declared by the snapshot's API JSON.

    ``indicator`` is the API's own indicator id (re-derived from snapshot
    bytes, never trusted from the model); ``unit`` is the curated
    WB_INDICATOR_UNITS entry for it; ``scale`` multiplies raw row values
    (1.0 for WB: values are full-precision). The verifier uses ``unit``
    to resolve otherwise unitless JSON quotes — values always come from
    the quote itself, never from this metadata.
    """

    indicator: str
    unit: str | None = None
    scale: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.indicator, str) or not self.indicator.strip():
            raise ValueError("SourceMeta.indicator must be a non-empty string")
        if self.unit is not None and self.unit not in UNIT_VOCAB:
            raise ValueError(
                f"SourceMeta.unit must be one of {sorted(UNIT_VOCAB)} or None")
        if not isinstance(self.scale, (int, float)) or not self.scale > 0:
            raise ValueError("SourceMeta.scale must be a positive number")


@dataclass(frozen=True)
class NumberMention:
    """One number as extracted from the quote by the researcher."""

    value: float
    unit: str | None = None
    scale_raw: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.value, (int, float)):
            raise ValueError("NumberMention.value must be a number")
        if self.unit is not None and self.unit not in UNIT_VOCAB:
            raise ValueError(
                f"NumberMention.unit must be one of {sorted(UNIT_VOCAB)} or None")
        if not isinstance(self.scale_raw, str):
            raise ValueError("NumberMention.scale_raw must be a string")


@dataclass(frozen=True)
class EvidencePackage:
    """Frozen, validated claim-to-source binding (M2a contract)."""

    package_id: str
    claim_id: str
    url: str
    captured_at: str  # ISO-8601 timestamp of the capture
    sha256: str  # hex digest of the snapshot bytes (utf-8)
    snapshot_path: str  # relative path under data/snap/
    quote: str  # literal span, must appear verbatim in the snapshot
    offsets: tuple[int, int] = (0, 0)  # (start, end) of quote in snapshot
    source_type: SourceType = "secondary"
    numbers: tuple[NumberMention, ...] = ()
    extractor: Extractor = "code"
    source_year: int | None = None  # publication year of the source, if known
    # Structured-source metadata (M2d): set only when the snapshot is API
    # JSON declaring a mapped indicator (see source_meta_for_snapshot).
    source_meta: SourceMeta | None = None

    def __post_init__(self) -> None:
        for name in ("package_id", "claim_id", "url", "captured_at",
                     "snapshot_path", "quote"):
            val = getattr(self, name)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.sha256, str) or not _SHA256_RE.match(self.sha256):
            raise ValueError("sha256 must be a 64-char lowercase hex digest")
        if (not isinstance(self.offsets, tuple) or len(self.offsets) != 2
                or not all(isinstance(i, int) for i in self.offsets)
                or self.offsets[0] < 0 or self.offsets[1] <= self.offsets[0]):
            raise ValueError("offsets must be a (start, end) int tuple with 0 <= start < end")
        if self.source_type not in ("primary", "secondary"):
            raise ValueError("source_type must be 'primary' or 'secondary'")
        if self.extractor not in ("model", "code"):
            raise ValueError("extractor must be 'model' or 'code'")
        if not isinstance(self.numbers, tuple):
            raise ValueError("numbers must be a tuple of NumberMention")
        for n in self.numbers:
            if not isinstance(n, NumberMention):
                raise ValueError("numbers must be a tuple of NumberMention")
        if self.source_year is not None and not (
                isinstance(self.source_year, int) and 1000 <= self.source_year <= 2100):
            raise ValueError("source_year must be an int in [1000, 2100] or None")
        if self.source_meta is not None and not isinstance(
                self.source_meta, SourceMeta):
            raise ValueError("source_meta must be a SourceMeta or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "claim_id": self.claim_id,
            "url": self.url,
            "captured_at": self.captured_at,
            "sha256": self.sha256,
            "snapshot_path": self.snapshot_path,
            "quote": self.quote,
            "offsets": list(self.offsets),
            "source_type": self.source_type,
            "numbers": [
                {"value": n.value, "unit": n.unit, "scale_raw": n.scale_raw}
                for n in self.numbers
            ],
            "extractor": self.extractor,
            "source_year": self.source_year,
            "source_meta": (
                {"indicator": self.source_meta.indicator,
                 "unit": self.source_meta.unit,
                 "scale": self.source_meta.scale}
                if self.source_meta is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvidencePackage":
        numbers = tuple(
            NumberMention(value=float(n["value"]),
                          unit=n.get("unit"),
                          scale_raw=n.get("scale_raw", ""))
            for n in d.get("numbers", [])
        )
        raw_meta = d.get("source_meta")
        meta = (
            SourceMeta(indicator=raw_meta["indicator"],
                       unit=raw_meta.get("unit"),
                       scale=raw_meta.get("scale", 1.0))
            if isinstance(raw_meta, dict) else None
        )
        return cls(
            package_id=d["package_id"],
            claim_id=d["claim_id"],
            url=d["url"],
            captured_at=d["captured_at"],
            sha256=d["sha256"],
            snapshot_path=d["snapshot_path"],
            quote=d["quote"],
            offsets=tuple(d.get("offsets", (0, 0))),  # type: ignore[arg-type]
            source_type=d.get("source_type", "secondary"),
            numbers=numbers,
            extractor=d.get("extractor", "code"),
            source_year=d.get("source_year"),
            source_meta=meta,
        )


def sha256_of_bytes(raw: bytes) -> str:
    """Hex digest of the RAW snapshot bytes (M2c canonical sha256).

    The snapshot hash is always over bytes as captured, never over
    re-encoded text: identical bytes -> identical sha on every side
    (fetch, store, verifier), including legacy non-UTF-8 charsets.
    """
    import hashlib

    return hashlib.sha256(bytes(raw)).hexdigest()


def sha256_of_text(text: str) -> str:
    """Hex digest of snapshot text (utf-8 bytes).

    Convenience for in-memory text fixtures; equals sha256_of_bytes
    whenever the text is the UTF-8 decoding of the snapshot bytes.
    """
    return sha256_of_bytes(text.encode("utf-8"))


__all__ = [
    "EvidencePackage",
    "Extractor",
    "NumberMention",
    "PRIMARY_HOSTS",
    "claim_has_monthly_frequency",
    "is_multi_source_arithmetic",
    "UNIT_VOCAB",
    "SourceMeta",
    "SourceType",
    "WB_ANNUAL_RATE_INDICATORS",
    "WB_INDICATOR_UNITS",
    "sha256_of_bytes",
    "sha256_of_text",
    "source_meta_for_snapshot",
    "source_type_for_url",
]
