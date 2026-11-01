"""Multimodal intake: PDF / image / video-frames -> anchored claims.

Pilar B-ingest (Evidence Atlas V3). Each modality extracts *anchored*
claims: a claim survives only when it carries a machine-checkable
provenance anchor (page / region / timestamp) PLUS a literal span that
is verbatim in the claim text. Anything else is discarded by
:func:`validate_anchors` with code ``provenance_missing`` — model
proposes, code disposes, extended to intake itself.

Deliberate NO-GOs (see docs/INGEST.md): video has NO audio track
(:class:`AudioNoGoError`), and frame decoding from containers needs an
external ffmpeg (stdlib-only here otherwise).
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Claim without a checkable anchor -> discarded, never surfaced silently.
PROVENANCE_MISSING = "provenance_missing"

# Qualitative image regions (vision models cannot emit reliable bboxes;
# the schema constrains decoding to this enum instead of free floats).
IMAGE_REGIONS = frozenset({
    "full",
    "top", "bottom", "left", "right", "center",
    "top-left", "top-right", "bottom-left", "bottom-right",
})

# Safety caps (PRD §5 spirit: model list fields are bounded).
MAX_CLAIMS_PER_PAGE = 20
MAX_CLAIMS_PER_IMAGE = 20
MAX_FRAMES_PER_VIDEO = 30


class IngestError(Exception):
    """Base error for ingest failures (malformed input, missing tool)."""


class AudioNoGoError(IngestError):
    """Raised by any audio path: video intake is frames-only by design."""


class ToolUnavailableError(IngestError):
    """Raised when an optional external tool (ffmpeg) is missing."""


@dataclass(frozen=True)
class IngestedClaim:
    """One claim extracted from a document, with its provenance anchor.

    `anchor` is modality-specific and always carries `span` (verbatim
    substring of `text`):
    - pdf:    {"page": int (1-based), "span": str}
    - image:  {"region": str (IMAGE_REGIONS), "resolution": "WxH px",
               "span": str}
    - frames: {"t_second": float, "region": str, "resolution": str,
               "span": str}
    """

    text: str
    anchor: dict
    source: str  # e.g. "report.pdf", "chart_a.png", "clip.mp4@t=2.0s"
    actor: str = "code"  # "model:<name>" when an LLM drafted it


@dataclass(frozen=True)
class DiscardedClaim:
    """A claim dropped by validation, kept for the audit trail."""

    text: str
    code: str  # PROVENANCE_MISSING (+ suffix detail in `detail`)
    detail: str
    source: str


@dataclass
class IngestResult:
    """Validated intake output: survivors + audit-visible discards."""

    claims: list[IngestedClaim] = field(default_factory=list)
    discarded: list[DiscardedClaim] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)

    @property
    def provenance_missing_count(self) -> int:
        return sum(1 for d in self.discarded if d.code == PROVENANCE_MISSING)


def _anchor_modality(anchor: object) -> str | None:
    if not isinstance(anchor, dict):
        return None
    if "t_second" in anchor:
        return "frames"
    if "region" in anchor:
        return "image"
    if "page" in anchor:
        return "pdf"
    return None


def validate_anchors(
    claims: list[IngestedClaim],
    uncertainties: list[str] | None = None,
) -> IngestResult:
    """Keep only anchored claims; discard the rest as provenance_missing.

    Rules (deterministic, no model judgment):
    - `span` must be a non-empty string AND a verbatim substring of
      `text` (the claim quotes its own evidence literally).
    - pdf anchor: `page` int >= 1.
    - image anchor: `region` in IMAGE_REGIONS + non-empty `resolution`.
    - frames anchor: image rules + `t_second` int/float >= 0.
    Anything violating any rule is discarded with code
    ``provenance_missing`` and a detail naming the violated rule.
    Never raises on claim-shaped input.
    """
    result = IngestResult(uncertainties=list(uncertainties or []))
    for claim in claims:
        try:
            reason = _anchor_violation(claim)
        except Exception:  # one bad claim never kills validation
            reason = "unvalidatable claim object"
        if reason is None:
            result.claims.append(claim)
        else:
            result.discarded.append(DiscardedClaim(
                text=claim.text if isinstance(getattr(claim, "text", None), str) else "",
                code=PROVENANCE_MISSING,
                detail=reason,
                source=getattr(claim, "source", ""),
            ))
    if result.discarded:
        result.uncertainties.append(
            f"discarded {len(result.discarded)} unanchored claim(s) "
            f"({PROVENANCE_MISSING})"
        )
    return result


def _anchor_violation(claim: IngestedClaim) -> str | None:
    """Return the violated anchor rule, or None when fully anchored."""
    text = claim.text
    if not isinstance(text, str) or not text.strip():
        return "empty claim text"
    anchor = claim.anchor
    if not isinstance(anchor, dict):
        return "anchor is not an object"
    span = anchor.get("span")
    if not isinstance(span, str) or not span.strip():
        return "missing span"
    if span not in text:
        return "span not verbatim in claim text"
    modality = _anchor_modality(anchor)
    if modality == "pdf":
        page = anchor.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            return "page must be an int >= 1"
        return None
    if modality in ("image", "frames"):
        region = anchor.get("region")
        if region not in IMAGE_REGIONS:
            return f"region {region!r} outside vocabulary"
        resolution = anchor.get("resolution")
        if not isinstance(resolution, str) or not resolution.strip():
            return "missing resolution note"
        if modality == "frames":
            t = anchor.get("t_second")
            if isinstance(t, bool) or not isinstance(t, (int, float)) or t < 0:
                return "t_second must be a number >= 0"
        return None
    return "anchor carries no page/region/t_second key"
