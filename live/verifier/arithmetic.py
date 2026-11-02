"""Deterministic arithmetic verification for Atlas Live V5-P0. CODE ONLY.

Two layers, both deterministic and never raising:

1. GUARD — :func:`needs_computation`: detects claims whose figure must be
   DERIVED (difference, ratio, percent change, average, sum) by general
   EN/ES patterns, never by claim id. A claim needing computation that the
   code did not compute from verified values is ``unverifiable``, NEVER
   ``contradicted`` (the verifier demotes component-evidence mismatches
   to ``arithmetic_not_computed``).
2. COMPUTE — :func:`compute_claim` + :func:`compute_verdict`: derives the
   figure from operand values re-extracted from valid packages (literal
   quote + aligned year/unit/scale) and compares the RESULT against the
   claim number with the claim tolerance (default 2%) after rounding the
   result to the claim's stated precision. Every derived verdict carries
   a receipt (formula + inputs) in ``details["computation"]``.

Orientation conventions (documented, pinned by tests):

- ``diff``: ``|a - b|`` (symmetric; entity gap and temporal gap alike).
- ``ratio``: caller passes ``[numerator, denominator]``; the orchestrator
  orients by the claim's own magnitude (fraction >= 1 -> max/min).
- ``pct_change``: caller passes ``[old, new]`` ordered by source year;
  result is ``(new - old) / |old| * 100``.
- ``average`` / ``sum``: n-ary over de-duplicated operand values.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Sequence

from live.evidence.package import (
    EvidencePackage,
    sha256_of_bytes,
    sha256_of_text,
    source_meta_for_snapshot,
)

try:  # pragma: no cover - import shim (mirrors live/verifier/check.py)
    from src.atlas.core.compare import is_year_quantity
    from src.atlas.core.units import parse_quantities, units_compatible
except ImportError:  # pragma: no cover - import shim
    from atlas.core.compare import is_year_quantity
    from atlas.core.units import parse_quantities, units_compatible

OpName = Literal["diff", "ratio", "pct_change", "average", "sum"]

# Priority when a text matches several ops (first wins). Percent-qualified
# ops are the most specific, so they are checked first.
_OP_PRIORITY: tuple[OpName, ...] = (
    "pct_change", "average", "ratio", "diff", "sum")

_YEAR = r"(?:19|20|21)\d{2}"

# Level assertions state the entity's own level ("grew TO x") and are
# directly comparable: they NEVER need computation. EN mirrors
# live.evidence.package._LEVEL_ASSERTION_RE; ES is the V5-P0 equivalent.
_LEVEL_RES = (
    re.compile(
        r"\b(doubled|tripled|quadrupled|grew|grown|growing|rose|risen"
        r"|fell|fallen|dropped|declined|decline|reached|climbed|surged"
        r"|jumped)\s+to\b",
        re.IGNORECASE),
    re.compile(
        r"\b(duplic[óo]|triplic[óo]|lleg[óo]\s+a|alcanz[óo]|ascendi[óo]\s+a"
        r"|totaliz[óo]|se\s+situ[óo]\s+en|creci[óo]\s+a|aument[óo]\s+a"
        r"|cay[óo]\s+a)\b",
        re.IGNORECASE),
)

# Year spans: EN "from 2023 to 2024" / "between 2023 and 2024",
# ES "de 2023 a 2024" / "entre 2023 y 2024".
_SPAN_RES = (
    re.compile(r"\bfrom\s+(%s)\s+to\s+(%s)\b" % (_YEAR, _YEAR),
               re.IGNORECASE),
    re.compile(r"\bbetween\s+(%s)\s+and\s+(%s)\b" % (_YEAR, _YEAR),
               re.IGNORECASE),
    re.compile(r"\bde\s+(%s)\s+a\s+(%s)\b" % (_YEAR, _YEAR),
               re.IGNORECASE),
    re.compile(r"\bentre\s+(%s)\s+y\s+(%s)\b" % (_YEAR, _YEAR),
               re.IGNORECASE),
)

_PERCENT_WORD_RE = re.compile(
    r"percent|por\s*ciento|porciento|%", re.IGNORECASE)

# Change words (EN mirrors _COMPUTED_FIGURE_RES; ES is V5-P0). Bare "bajo"
# (under/low) is deliberately excluded: only accented "bajó" (it fell).
_CHANGE_WORD_RES = (
    re.compile(
        r"\b(grew|growth|grown|increased|increase|decreased|decrease|rose"
        r"|risen|fell|fallen|declined|decline|change[sd]?)\b",
        re.IGNORECASE),
    re.compile(
        r"\b(creci[óo]|crecimiento|aument[óo]|aumento|increment[óo]"
        r"|incremento|disminu\w+|ca[íi]da|cay[óo]|subi[óo]|subida|baj[óo]"
        r"|bajada|alza|variaci[óo]n|cambio|cambi[óo])\b",
        re.IGNORECASE),
)

# Diff verbs / comparative frames. Surpass/exceed frames only count with
# the gap figure attached ("superó a China EN 42M" / "surpassed China
# BY 42M"): bare "X superó a Y" is a ranking and "produjo 4,7 billones,
# superando a Y" states a LEVEL with a comparative rider. Bounds
# ("por encima/debajo de N", "more than N") are levels, not derivations.
_NO_YEAR = r"(?!19\d{2}\b|20\d{2}\b|21\d{2}\b)"

_DIFF_VERB_RES = (
    re.compile(
        r"\b(surpass|exceed|outnumber)\w*\b"
        r"[^.?!]{0,60}?\bby\s+" + _NO_YEAR + r"\d",
        re.IGNORECASE),
    re.compile(r"\bdifference\s+of\b", re.IGNORECASE),
    re.compile(r"\bgap\b", re.IGNORECASE),
    re.compile(r"\bbrecha\b", re.IGNORECASE),
    re.compile(
        r"\b\d[\d.,]*\s*(?:\w+\s+){0,3}?"
        r"(more|less|fewer|greater)\s+than\b",
        re.IGNORECASE),
    re.compile(
        r"\bsuper\w*\s+a\b[^.?!]{0,60}?\b(en|por)\s+" + _NO_YEAR + r"\d",
        re.IGNORECASE),
    re.compile(
        r"\bexced\w*\b[^.?!]{0,60}?\b(en|por)\s+" + _NO_YEAR + r"\d",
        re.IGNORECASE),
    re.compile(r"\bdiferencia\s+de\b", re.IGNORECASE),
    re.compile(
        r"\b\d[\d.,]*\s*(?:\w+\s+){0,3}?"
        r"(m[áa]s|menos)\s+que\b",
        re.IGNORECASE),
)

# Absolute-change verbs: with a year span and NO percent word they assert
# an absolute gap (C1D3 class: "perdió 1,7M entre 2023 y 2024").
_ABSOLUTE_CHANGE_RES = (
    re.compile(r"\b(lost|gained|shed|added)\b", re.IGNORECASE),
    re.compile(
        r"\b(perdi[óo]|gan[óo]|cay[óo]|creci[óo]|aument[óo]"
        r"|disminu\w+|redujo|reducci[óo]n)\b",
        re.IGNORECASE),
)

# Average only counts in the closed-set "of"-frame ("average GDP OF
# Spain and Italy", "PIB medio DE ..."): bare "averaged 4.0% in 2024" is
# an open temporal aggregation whose full input set two packages cannot
# supply, so deriving from whatever rows arrived would be unsound.
# "Medio" needs the frame AND an en/por guard ("en medio de" / "por medio
# de" are not averages; "medio ambiente" has no frame).
_AVERAGE_WORD_RE = re.compile(
    r"\baverag(?:e|ed|es|ing)\b|\bmean\b|\bmedian\b"
    r"|\bpromedio\b|\bmedia\b|\bmediana\b"
    r"|(?<!\ben\s)(?<!\bpor\s)\bmedio\b",
    re.IGNORECASE)
_OF_FRAME_RE = re.compile(r"\b(of|de|del)\b", re.IGNORECASE)

_RATIO_RES = (
    re.compile(r"\bpercent\s+of\b", re.IGNORECASE),
    re.compile(r"\bpor\s*ciento\s+de(l)?\b", re.IGNORECASE),
    # "N times 10 to the K" is scientific notation, not a ratio claim.
    re.compile(r"\b\d[\d.,]*\s*times\b(?!\s*(10|ten)\b)", re.IGNORECASE),
    re.compile(r"\b\d[\d.,]*\s*veces\b(?!\s*(10|diez)\b)", re.IGNORECASE),
)

# Bare "total" is deliberately excluded (level trap: "Population, total").
_SUM_RES = (
    re.compile(r"\bcombined\b|\bsum\s+of\b|\bsum\s+to\b|\bjoint\s+total\b"
               r"|\btogether\b",
               re.IGNORECASE),
    re.compile(r"\bcombinad[oa]s?\b|\bsuma\s+de\b|\ben\s+conjunto\b"
               r"|\bjunt[oa]s\b",
               re.IGNORECASE),
)

# Expected sign of a pct_change result from direction words.
_GROWTH_RE = re.compile(
    r"\b(grew|grown|growth|increased|increase|rose|risen|surged|jumped"
    r"|climbed|creci[óo]|crecimiento|aument[óo]|aumento|increment[óo]"
    r"|incremento|subi[óo]|subida|alza)\b",
    re.IGNORECASE)
_DECLINE_RE = re.compile(
    r"\b(fell|fallen|declined|decline|decreased|decrease|dropped|shrank"
    r"|shrunk|shrink|cay[óo]|ca[íi]da|disminu\w+|baj[óo]|bajada"
    r"|redujo|reducci[óo]n)\b",
    re.IGNORECASE)

_WS_RE = re.compile(r"\s+")


def _normalize_ws(s: str) -> str:
    """Whitespace-collapse (5-line mirror of check.normalize_ws).

    Duplicated (not imported) so this module stays importable from
    check.py without a dependency cycle.
    """
    return _WS_RE.sub(" ", s or "").strip()


def needs_computation(text: str) -> OpName | None:
    """Operation the claim figure requires, or None (pure, never raises).

    General EN/ES patterns over the claim TEXT only — never claim ids.
    Level assertions ("grew TO x" / "llegó a x") return None: x is the
    entity's own level and stays directly comparable.
    """
    try:
        doc = text or ""
    except Exception:
        return None
    try:
        if any(rx.search(doc) for rx in _LEVEL_RES):
            return None
        if _is_pct_change(doc):
            return "pct_change"
        if _is_average(doc):
            return "average"
        if any(rx.search(doc) for rx in _RATIO_RES):
            return "ratio"
        if _is_diff(doc):
            return "diff"
        if any(rx.search(doc) for rx in _SUM_RES):
            return "sum"
    except Exception:
        return None
    return None


def _near(word_rx: re.Pattern, other_rx: re.Pattern, doc: str,
          window: int = 60) -> bool:
    """True when both patterns match within ``window`` chars of prose."""
    for match in word_rx.finditer(doc):
        start = max(0, match.start() - window)
        end = min(len(doc), match.end() + window)
        chunk = doc[start:end]
        if other_rx.search(chunk):
            return True
    return False


def _has_span(doc: str) -> bool:
    return any(rx.search(doc) for rx in _SPAN_RES)


def _is_pct_change(doc: str) -> bool:
    """Percent word tied to TWO vintages (span or >= 2 distinct years).

    A single-year rate ("growth of 3.1 percent in 2026") is a level the
    source states directly — not a derivation — so it must not fire
    (B1/B2 class). The change-word-near-percent form still requires the
    year pair: without two vintages there is nothing to order old/new.
    """
    if not _PERCENT_WORD_RE.search(doc):
        return False
    if _has_span(doc):
        return True
    if len(_claim_years(doc)) >= 2:
        return any(_near(change_rx, _PERCENT_WORD_RE, doc)
                   for change_rx in _CHANGE_WORD_RES)
    return False


def _is_average(doc: str) -> bool:
    """Average word followed by an of/de-frame within 60 chars."""
    for match in _AVERAGE_WORD_RE.finditer(doc):
        window = doc[match.end():match.end() + 60]
        window = re.split(r"\.(?!\d)", window)[0]  # sentence end, not 83.8
        if _OF_FRAME_RE.search(window):
            return True
    return False


def _is_diff(doc: str) -> bool:
    if any(rx.search(doc) for rx in _DIFF_VERB_RES):
        return True
    if _PERCENT_WORD_RE.search(doc):
        return False
    return _has_span(doc) and any(
        rx.search(doc) for rx in _ABSOLUTE_CHANGE_RES)


# ---------------------------------------------------------------------------
# Pure arithmetic core
# ---------------------------------------------------------------------------

@dataclass
class Computation:
    """Deterministic result of :func:`compute_claim`."""

    op: OpName
    result: float
    formula: str  # human-readable receipt line, e.g. "|a - b| = 41.96M"
    inputs: list[float] = field(default_factory=list)


def _finite(values: Sequence[float]) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v)
               for v in values)


def compute_claim(values: Sequence[float], op: str,
                  ) -> Computation | None:
    """Deterministic derivation over operand values (pure, never raises).

    Order contract: ``pct_change`` takes ``[old, new]`` (ordered by
    source year); ``ratio`` takes ``[numerator, denominator]`` (oriented
    by the claim's magnitude); ``diff`` is symmetric ``|a - b|``;
    ``average``/``sum`` are n-ary. Returns None on arity violations,
    non-finite inputs, or division by zero — never raises.
    """
    try:
        vals = [float(v) for v in (values or [])]
    except (TypeError, ValueError):
        return None
    if not _finite(vals):
        return None
    if op == "diff":
        if len(vals) != 2:
            return None
        a, b = vals
        return Computation(op="diff", result=abs(a - b), inputs=vals,
                           formula=f"|{a:g} - {b:g}| = {abs(a - b):g}")
    if op == "ratio":
        if len(vals) != 2 or vals[1] == 0:
            return None
        num, den = vals
        return Computation(op="ratio", result=num / den, inputs=vals,
                           formula=f"{num:g} / {den:g} = {num / den:g}")
    if op == "pct_change":
        if len(vals) != 2 or vals[0] == 0:
            return None
        old, new = vals
        pct = (new - old) / abs(old) * 100.0
        return Computation(
            op="pct_change", result=pct, inputs=vals,
            formula=f"({new:g} - {old:g}) / |{old:g}| * 100 = {pct:g}%")
    if op == "average":
        if len(vals) < 2:
            return None
        avg = sum(vals) / len(vals)
        return Computation(
            op="average", result=avg, inputs=vals,
            formula=f"({' + '.join(f'{v:g}' for v in vals)}) / {len(vals)}"
                    f" = {avg:g}")
    if op == "sum":
        if len(vals) < 2:
            return None
        total = sum(vals)
        return Computation(
            op="sum", result=total, inputs=vals,
            formula=f"{' + '.join(f'{v:g}' for v in vals)} = {total:g}")
    return None


# ---------------------------------------------------------------------------
# Claim stated precision (rounding-aware comparison)
# ---------------------------------------------------------------------------

_THOUSANDS_EN_RE = re.compile(r"^\d{1,3}(,\d{3})+$")
_THOUSANDS_ES_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")
_TOKEN_RE = re.compile(r"\d(?:[\d.,]*\d)?")


def claim_quantum(raw: str, value: float) -> float | None:
    """Quantum of the claim's stated precision, or None (pure).

    Derived from the FIRST numeric token of the claim's raw span plus the
    parsed value (the ratio pins the scale word: "1,7 millones" ->
    quantum 0.1M). Returns None when the token/scale is ambiguous, so the
    caller falls back to a strict tolerance-only comparison.
    """
    try:
        match = _TOKEN_RE.search(raw or "")
        if match is None:
            return None
        token = match.group(0)
        val = float(value)
        if not math.isfinite(val) or val == 0:
            return None
        decimals, number = _token_precision(token)
        if number == 0 or not math.isfinite(number):
            return None
        scale = abs(val) / abs(number)
        power = round(math.log10(scale))
        if not (-3 <= power <= 15):
            return None
        if abs(scale - 10.0 ** power) / (10.0 ** power) > 0.01:
            return None  # scale is not a clean power of ten: abstain
        quantum = (10.0 ** -decimals) * (10.0 ** power)
        if not math.isfinite(quantum) or quantum <= 0:
            return None
        return quantum
    except (ValueError, ArithmeticError):
        return None


def _token_precision(token: str) -> tuple[int, float]:
    """(decimals, number) for one numeric token.

    Thousands-grouped tokens ("27,811,517,000,000", "1.408.975") carry 0
    decimals; otherwise the last separator is the decimal mark. The
    3-digit ambiguity ("1,735": 1735 vs 1.735) resolves to thousands
    (fewer decimals = stricter comparison).
    """
    if _THOUSANDS_EN_RE.match(token):
        return 0, float(token.replace(",", ""))
    if _THOUSANDS_ES_RE.match(token):
        return 0, float(token.replace(".", ""))
    if "," in token or "." in token:
        last_comma, last_dot = token.rfind(","), token.rfind(".")
        if last_comma > last_dot:
            head, frac = token.rsplit(",", 1)
            number = float(head.replace(".", "").replace(",", "") + "."
                           + frac)
            return len(frac), number
        head, frac = token.rsplit(".", 1)
        number = float(head.replace(",", "").replace(".", "") + "."
                       + frac)
        return len(frac), number
    return 0, float(token)


def round_to_quantum(result: float, quantum: float | None) -> float:
    """Round a derived result to the claim's stated quantum (pure)."""
    try:
        res = float(result)
        q = float(quantum) if quantum is not None else None
    except (TypeError, ValueError):
        return result
    if q is None or not math.isfinite(res) or not math.isfinite(q) \
            or q <= 0:
        return res
    return round(res / q) * q


# ---------------------------------------------------------------------------
# Operand extraction from valid packages
# ---------------------------------------------------------------------------

SnapshotLoader = Callable[[EvidencePackage], str | bytes | None]


@dataclass
class Operand:
    """One verified endpoint value feeding a computation."""

    package_id: str
    value: float  # re-extracted from the quote (× source_meta scale)
    unit: str | None
    year: int | None
    scale: float = 1.0


def _snapshot_candidates(snapshot: str | bytes) -> list[str]:
    """Decoded texts to search the quote in (mirror of check.py)."""
    if isinstance(snapshot, str):
        return [snapshot]
    raw = bytes(snapshot)
    out = [raw.decode("utf-8", "replace")]
    try:
        latin = raw.decode("latin-1")
    except Exception:
        latin = ""
    if latin and latin != out[0]:
        out.append(latin)
    return out


def _quote_validated(pkg: EvidencePackage, claim_id: str,
                     loader: SnapshotLoader) -> list[str] | None:
    """Snapshot candidates when the package validates, else None.

    Mirrors ``check._check_package`` steps 1-2 (claim binding, snapshot
    hash over raw bytes, literal quote): an operand value is only usable
    when its quote literally occurs in the hashed snapshot.
    """
    try:
        if pkg.claim_id != claim_id:
            return None
        snapshot = loader(pkg)
    except Exception:
        return None
    if not snapshot:
        return None
    if isinstance(snapshot, (bytes, bytearray)):
        if sha256_of_bytes(bytes(snapshot)) != pkg.sha256:
            return None
        candidates = _snapshot_candidates(snapshot)
    else:
        if sha256_of_text(snapshot) != pkg.sha256:
            return None
        candidates = [snapshot]
    norm_quote = _normalize_ws(pkg.quote)
    if not norm_quote or not any(
            norm_quote in _normalize_ws(text) for text in candidates):
        return None
    return candidates


def extract_operands(claim_id: str, packages: Sequence[EvidencePackage],
                     loader: SnapshotLoader,
                     ) -> tuple[list[Operand], list[str]]:
    """Verified operand values, de-duplicated (never raises).

    Per package: the LARGEST non-year quantity re-extracted from the
    validated quote (endpoint figures dwarf JSON paging/metadata
    integers such as the "3" in "countryiso3code" — deterministic,
    receipt-auditable). The unit is the quantity's own normalized unit,
    or the M2d ``source_meta`` unit re-derived from the snapshot bytes
    (indicator must agree with the package declaration). Identical
    values quoted twice (same endpoint, two packages) collapse to ONE
    operand — the first package keeps provenance, the rest are noted.
    """
    operands: list[Operand] = []
    notes: list[str] = []
    try:
        pkgs = list(packages or [])
    except TypeError:
        return [], ["packages_unreadable"]
    for pkg in pkgs:
        try:
            candidates = _quote_validated(pkg, claim_id, loader)
        except Exception:
            notes.append(f"{getattr(pkg, 'package_id', '?')}:unreadable")
            continue
        if candidates is None:
            notes.append(f"{pkg.package_id}:quote_unvalidated")
            continue
        try:
            cands = [q for q in parse_quantities(pkg.quote, label="quote")
                     if not is_year_quantity(q)]
        except Exception:
            notes.append(f"{pkg.package_id}:parse_failed")
            continue
        if not cands:
            notes.append(f"{pkg.package_id}:no_number_in_quote")
            continue
        pick = max(cands, key=lambda q: q.value)
        try:
            value = float(pick.value)
        except (TypeError, ValueError):
            notes.append(f"{pkg.package_id}:value_unreadable")
            continue
        if not math.isfinite(value):
            notes.append(f"{pkg.package_id}:value_nonfinite")
            continue
        unit = pick.normalized_unit
        scale = 1.0
        if unit is None and pkg.source_meta is not None:
            rederived = None
            for text in candidates:
                rederived = source_meta_for_snapshot(pkg.url, text)
                if rederived is not None:
                    break
            if (rederived is not None
                    and rederived.indicator == pkg.source_meta.indicator):
                unit = rederived.unit
                scale = float(rederived.scale)
        if unit is None:
            notes.append(f"{pkg.package_id}:operand_unit_unknown")
            continue
        if not math.isfinite(scale) or scale <= 0:
            notes.append(f"{pkg.package_id}:scale_invalid")
            continue
        operands.append(Operand(package_id=pkg.package_id,
                                value=value * scale, unit=unit,
                                year=pkg.source_year, scale=scale))
    deduped: list[Operand] = []
    for op in operands:
        if any(op.value == seen.value for seen in deduped):
            notes.append(f"{op.package_id}:duplicate_value")
            continue
        deduped.append(op)
    return deduped, notes


# ---------------------------------------------------------------------------
# Orchestration: derive, align, compare
# ---------------------------------------------------------------------------

@dataclass
class ComputedOutcome:
    """Derived verdict with receipt (built into a Verdict by check.py)."""

    kind: Literal["supported", "contradicted"]
    reasons: list[str] = field(default_factory=list)
    package_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def _claim_years(text: str) -> set[int]:
    try:
        return {int(m.group(0)) for m in re.finditer(_YEAR, text or "")}
    except Exception:
        return set()


def _units_aligned(operands: Sequence[Operand]) -> bool:
    units = [op.unit for op in operands]
    if any(u is None for u in units):
        return False
    try:
        return all(units_compatible(units[0], u) for u in units[1:])
    except Exception:
        return False


def _years_ok(op: OpName, operands: Sequence[Operand],
              claim_text: str, claim_year: int | None) -> bool:
    """Year alignment per op (pure).

    Same-year ops (average/sum, entity diff/ratio): known operand years
    must agree, and match the claim year when it is known. Binary
    diff/ratio also accept a temporal pair (two distinct years, both
    stated in the claim text, e.g. C1D3 "entre 2023 y 2024").
    pct_change REQUIRES the temporal pair (the years order old/new).
    """
    known = [op_.year for op_ in operands if op_.year is not None]
    if op == "pct_change":
        if len(known) != 2 or known[0] == known[1]:
            return False
        return set(known) <= _claim_years(claim_text)
    if known and any(y != known[0] for y in known):
        if op in ("average", "sum"):
            return False
        if len(known) != 2 or len(set(known)) != 2:
            return False
        if set(known) > _claim_years(claim_text):
            return False
    elif known and claim_year is not None and known[0] != claim_year:
        return False
    return True


def _expected_sign(text: str) -> int | None:
    """+1 growth / -1 decline / None neutral-or-unknown (pure)."""
    try:
        grow = bool(_GROWTH_RE.search(text or ""))
        fall = bool(_DECLINE_RE.search(text or ""))
    except Exception:
        return None
    if grow and not fall:
        return 1
    if fall and not grow:
        return -1
    return None


def compute_verdict(claim: Any, packages: Sequence[EvidencePackage],
                    loader: SnapshotLoader, op: str,
                    ) -> ComputedOutcome | None:
    """Derive-and-compare verdict, or None when not computable.

    None (the GUARD path: unverifiable, never contradicted) covers:
    wrong operand arity, non-positive claim figure, unit/year/scale
    misalignment, unparseable inputs. Any exception also yields None:
    the verifier must never crash on odd evidence.
    """
    try:
        return _compute_verdict(claim, packages, loader, op)
    except Exception:
        return None


def _compute_verdict(claim: Any, packages: Sequence[EvidencePackage],
                     loader: SnapshotLoader, op: str,
                     ) -> ComputedOutcome | None:
    if op not in ("diff", "ratio", "pct_change", "average", "sum"):
        return None
    claim_value = float(getattr(claim, "value", 0.0) or 0.0)
    if not math.isfinite(claim_value) or claim_value <= 0:
        return None
    claim_unit = getattr(claim, "unit", None)
    claim_text = str(getattr(claim, "text", "") or "")
    claim_year = getattr(claim, "claim_year", None)
    tolerance = float(getattr(claim, "tolerance", 0.02) or 0.02)
    if not math.isfinite(tolerance) or tolerance < 0:
        return None
    claim_id = str(getattr(claim, "claim_id", ""))

    operands, notes = extract_operands(claim_id, packages, loader)
    arity = 2 if op in ("diff", "ratio", "pct_change") else None
    if arity is not None and len(operands) != arity:
        return None
    if arity is None and len(operands) < 2:
        return None
    if not _units_aligned(operands):
        return None
    scales = {op_.scale for op_ in operands}
    if len(scales) != 1:
        return None
    if not _years_ok(op, operands, claim_text, claim_year):
        return None

    percent_mode = False
    if op in ("diff", "average", "sum"):
        # The derived figure lives in the operands' unit: it must match
        # the claim's unit (USD gap vs USD claim, count average vs
        # count claim).
        if claim_unit is None or not units_compatible(operands[0].unit,
                                                      claim_unit):
            return None
        ordered = [op_.value for op_ in operands]
    elif op == "pct_change":
        if claim_unit != "percent":
            return None
        old, new = sorted(operands, key=lambda o: o.year or 0)
        ordered = [old.value, new.value]
    else:  # ratio: orient by the claim's own magnitude.
        if claim_unit == "percent":
            percent_mode = True
            fraction = claim_value / 100.0
        elif claim_unit in (None, "count"):
            fraction = claim_value
        else:
            return None
        values = [op_.value for op_ in operands]
        hi, lo = max(values), min(values)
        if lo == 0:
            return None
        ordered = [hi, lo] if fraction >= 1 else [lo, hi]

    comp = compute_claim(ordered, op)
    if comp is None:
        return None
    result = comp.result * 100.0 if percent_mode else comp.result
    formula = (f"{comp.formula} x100 = {result:g}%"
               if percent_mode else comp.formula)

    if op == "pct_change":
        want = _expected_sign(claim_text)
        if want is not None and result != 0:
            got = 1 if result > 0 else -1
            if got != want:
                return ComputedOutcome(
                    kind="contradicted",
                    reasons=["computed_sign_mismatch"],
                    package_ids=[op_.package_id for op_ in operands],
                    details={"computation": _receipt(
                        op, formula, operands, notes, result, result,
                        claim_value, tolerance, None,
                        sign_expected=want, sign_got=got)})

    quantum = claim_quantum(str(getattr(claim, "raw", "") or ""),
                            claim_value)
    rounded = round_to_quantum(result, quantum)
    gap = abs(claim_value - rounded)
    allowed = tolerance * (abs(rounded) if rounded != 0 else 1.0)
    if gap <= allowed:
        return ComputedOutcome(
            kind="supported", reasons=["computed_match"],
            package_ids=[op_.package_id for op_ in operands],
            details={"computation": _receipt(
                op, formula, operands, notes, result, rounded,
                claim_value, tolerance, quantum,
                gap=gap, allowed=allowed)})
    return ComputedOutcome(
        kind="contradicted", reasons=["computed_mismatch"],
        package_ids=[op_.package_id for op_ in operands],
        details={"computation": _receipt(
            op, formula, operands, notes, result, rounded,
            claim_value, tolerance, quantum, gap=gap, allowed=allowed)})


def _receipt(op: str, formula: str, operands: Sequence[Operand],
             notes: Sequence[str], result: float, rounded: float,
             claim_value: float, tolerance: float,
             quantum: float | None, **extra: Any) -> dict[str, Any]:
    """Audit receipt: the derivation and every input (pure shaping)."""
    return {
        "op": op,
        "formula": formula,
        "inputs": [{"package_id": op_.package_id, "value": op_.value,
                    "unit": op_.unit, "year": op_.year}
                   for op_ in operands],
        "operand_notes": list(notes),
        "result": result,
        "rounded_result": rounded,
        "rounding_quantum": quantum,
        "claim_value": claim_value,
        "tolerance": tolerance,
        **extra,
    }


__all__ = [
    "Computation",
    "ComputedOutcome",
    "Operand",
    "OpName",
    "claim_quantum",
    "compute_claim",
    "compute_verdict",
    "extract_operands",
    "needs_computation",
    "round_to_quantum",
]
