"""M6 accuracy math: accuracy over DECIDED + 95% Wilson interval.

CODE ONLY — pure functions, no model, no network.

Decided = any verdict other than ``unverifiable`` (i.e. supported,
contradicted, disputed). Only ``unverifiable`` is excluded from the
accuracy denominator; ``disputed`` counts as decided-but-wrong (the
pipeline committed to no side, so it matched neither expectation) and
is also reported separately as ``n_disputed``. Abstention is reported
as its own rate, never folded into accuracy.
"""

from __future__ import annotations

import math
from typing import Any

_VERIFIABLE = ("supported", "contradicted", "disputed")


def wilson_interval(correct: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k/n (standard formula, z=1.96).

    center = (p + z^2/2n) / (1 + z^2/n)
    half   = z * sqrt(p(1-p)/n + z^2/4n^2) / (1 + z^2/n)
    with p = correct / n, clamped to [0, 1].

    n <= 0 -> (0.0, 1.0): no data, maximal uncertainty.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = correct / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _is_correct(expected: str, verdict: str) -> bool:
    return ((expected == "true" and verdict == "supported")
            or (expected == "false" and verdict == "contradicted"))


def summarize(expected: dict[str, str],
              verdicts: dict[str, str]) -> dict[str, Any]:
    """Accuracy panel over decided canaries.

    ``expected`` maps canary id -> "true"/"false"; ``verdicts`` maps id
    -> verdict label (missing id counts as "unverifiable").
    """
    per_canary: list[dict[str, Any]] = []
    decided = 0
    correct = 0
    n_disputed = 0
    falsos_contradicted: list[str] = []  # expected TRUE, got contradicted
    falsos_supported: list[str] = []  # expected FALSE, got supported
    for cid, exp in expected.items():
        verdict = verdicts.get(cid, "unverifiable")
        is_decided = verdict in _VERIFIABLE
        ok = is_decided and _is_correct(exp, verdict)
        if is_decided:
            decided += 1
        if ok:
            correct += 1
        if verdict == "disputed":
            n_disputed += 1
        if exp == "true" and verdict == "contradicted":
            falsos_contradicted.append(cid)
        if exp == "false" and verdict == "supported":
            falsos_supported.append(cid)
        per_canary.append({"canary_id": cid, "expected": exp,
                           "verdict": verdict, "decided": is_decided,
                           "correct": ok})
    n = len(expected)
    accuracy: float | None = (correct / decided) if decided else None
    if decided:
        lo, hi = wilson_interval(correct, decided)
    else:
        lo, hi = (0.0, 1.0)
    return {
        "n": n,
        "decided": decided,
        "correct": correct,
        "accuracy": accuracy,
        "wilson_lo": round(lo, 4),
        "wilson_hi": round(hi, 4),
        "abstention_rate": round((n - decided) / n, 4) if n else 1.0,
        "falsos_contradicted": falsos_contradicted,
        "falsos_supported": falsos_supported,
        "n_disputed": n_disputed,
        "per_canary": per_canary,
    }
