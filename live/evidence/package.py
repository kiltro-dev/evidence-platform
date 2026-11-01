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
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvidencePackage":
        numbers = tuple(
            NumberMention(value=float(n["value"]),
                          unit=n.get("unit"),
                          scale_raw=n.get("scale_raw", ""))
            for n in d.get("numbers", [])
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
    "UNIT_VOCAB",
    "SourceType",
    "sha256_of_bytes",
    "sha256_of_text",
    "source_type_for_url",
]
