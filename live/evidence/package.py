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
from dataclasses import dataclass
from typing import Any, Literal

SourceType = Literal["primary", "secondary"]
Extractor = Literal["model", "code"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class NumberMention:
    """One number as extracted from the quote by the researcher."""

    value: float
    unit: str | None = None
    scale_raw: str = ""


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


def sha256_of_text(text: str) -> str:
    """Hex digest of snapshot text (utf-8 bytes)."""
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["EvidencePackage", "Extractor", "NumberMention", "SourceType", "sha256_of_text"]
