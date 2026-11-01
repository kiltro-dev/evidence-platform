"""M8 ablation report: Atlas arm vs baseline arm vs truth (branch live).

CODE ONLY — pure functions, no model, no network. Compares per-arm
verdicts against ``truth.json`` expectations and reports whatever the
numbers say, including every metric where the model-only baseline
beats Atlas (see ``leaders``).

Metric semantics (shared with M6 ``wilson.summarize``):
- decided = verdict != "unverifiable" (``disputed`` counts as
  decided-but-wrong); accuracy + Wilson 95% are over decided only;
  abstention is its own rate, never folded into accuracy.
- hallucinations detected = expected FALSE judged ``contradicted``.
- false contradicted = expected TRUE judged ``contradicted`` (the P0);
  false supported = expected FALSE judged ``supported``.
"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from live.auditor.wilson import summarize as wilson_summarize  # noqa: E402
from live.run_verify import percentile  # noqa: E402

LABELS = ("supported", "contradicted", "disputed", "unverifiable")
_EXPECTED = ("true", "false")


def build_matrix(expected: dict[str, str],
                 verdicts: dict[str, str]) -> dict[str, dict[str, int]]:
    """Expected x verdict counts (missing verdict -> unverifiable)."""
    matrix = {exp: {label: 0 for label in LABELS}
              for exp in _EXPECTED}
    for cid, exp in expected.items():
        if exp not in matrix:
            continue
        verdict = verdicts.get(cid, "unverifiable")
        if verdict not in LABELS:
            verdict = "unverifiable"
        matrix[exp][verdict] += 1
    return matrix


def _latencies(latency_map: dict[str, float | None] | None) -> list[float]:
    if not latency_map:
        return []
    return sorted(v for v in latency_map.values()
                  if isinstance(v, (int, float)))


def arm_metrics(name: str, expected: dict[str, str],
                verdicts: dict[str, str],
                costs_usd: dict[str, float] | None = None,
                latency_map: dict[str, float | None] | None = None,
                ) -> dict[str, Any]:
    """Full metric panel for one arm (Atlas or one baseline run)."""
    panel = wilson_summarize(expected, verdicts)
    matrix = build_matrix(expected, verdicts)
    hallucinations = sorted(
        cid for cid, exp in expected.items()
        if exp == "false" and verdicts.get(cid) == "contradicted")
    lat = _latencies(latency_map)
    total_cost = round(sum((costs_usd or {}).values()), 4)
    n = len(expected)
    return {
        "arm": name,
        "n": n,
        "matrix": matrix,
        "decided": panel["decided"],
        "correct": panel["correct"],
        "accuracy_decided": panel["accuracy"],
        "wilson_lo": panel["wilson_lo"],
        "wilson_hi": panel["wilson_hi"],
        "abstention_count": n - panel["decided"],
        "abstention_rate": panel["abstention_rate"],
        "hallucinations_detected": hallucinations,
        "n_hallucinations": len(hallucinations),
        "falsos_contradicted": panel["falsos_contradicted"],
        "falsos_supported": panel["falsos_supported"],
        "n_disputed": panel["n_disputed"],
        "total_cost_usd": total_cost,
        "cost_per_claim_usd": round(total_cost / n, 4) if n else 0.0,
        "latency_s": {
            "count": len(lat),
            "p50": percentile(lat, 50),
            "p95": percentile(lat, 95),
        },
    }


def consistency(verdicts_a: dict[str, str],
                verdicts_b: dict[str, str]) -> dict[str, Any]:
    """Run1-vs-run2 agreement for the baseline (same claim ids)."""
    ids = sorted(set(verdicts_a) | set(verdicts_b))
    disagreements = sorted(
        cid for cid in ids
        if verdicts_a.get(cid, "unverifiable")
        != verdicts_b.get(cid, "unverifiable"))
    agree = len(ids) - len(disagreements)
    return {
        "n": len(ids),
        "agree": agree,
        "agreement_rate": round(agree / len(ids), 4) if ids else 1.0,
        "disagreements": disagreements,
    }


def _leaders(arms: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Best arm per metric, computed — never narrated.

    Higher wins: accuracy_decided (None excluded), n_hallucinations.
    Lower wins: n_falsos_total, abstention_rate, cost_per_claim_usd,
    latency p50 (None excluded). Ties name every tied arm.
    """
    def _best(metric, values, higher=True):
        valid = {a: v for a, v in values.items() if v is not None}
        if not valid:
            return "n/a"
        target = (max if higher else min)(valid.values())
        winners = sorted(a for a, v in valid.items() if v == target)
        return "+".join(winners)

    acc = {a: m["accuracy_decided"] for a, m in arms.items()}
    hal = {a: m["n_hallucinations"] for a, m in arms.items()}
    fal = {a: len(m["falsos_contradicted"]) + len(m["falsos_supported"])
           for a, m in arms.items()}
    abst = {a: m["abstention_rate"] for a, m in arms.items()}
    cost = {a: m["cost_per_claim_usd"] for a, m in arms.items()}
    p50 = {a: m["latency_s"]["p50"] for a, m in arms.items()}
    return {
        "accuracy_decided_higher": _best("acc", acc, True),
        "hallucinations_higher": _best("hal", hal, True),
        "falsos_total_lower": _best("fal", fal, False),
        "abstention_rate_lower": _best("abst", abst, False),
        "cost_per_claim_lower": _best("cost", cost, False),
        "latency_p50_lower": _best("p50", p50, False),
    }


def compare(truth_entries: list[dict],
            atlas_summary: dict[str, Any],
            baseline_runs: list[dict[str, Any]],
            atlas_arm_name: str = "atlas-m2d",
            ) -> dict[str, Any]:
    """Honest comparison: one panel per arm + consistency + leaders.

    ``truth_entries``: truth.json rows (id/expected) defining the
    denominator. ``atlas_summary``: the reused M2d ``.summary.json``
    (``per_claim`` verdicts). Each baseline run: ``{"run_id", "claims":
    [{claim_id, verdict, cost_usd, latency_s, ...}]}``.
    """
    expected = {e["id"]: e["expected"] for e in truth_entries
                if e.get("expected") in _EXPECTED}
    atlas_verdicts = {c.get("claim_id"): c.get("verdict", "unverifiable")
                      for c in atlas_summary.get("per_claim", [])
                      if c.get("claim_id") in expected}
    atlas_lat = {c.get("claim_id"): c.get("latency_s")
                 for c in atlas_summary.get("per_claim", [])
                 if c.get("claim_id") in expected}
    n_atlas = atlas_summary.get("n_claims") or len(atlas_verdicts) or 1
    atlas_cost_each = (atlas_summary.get("total_cost_usd", 0.0) or 0.0)
    atlas_costs = {cid: atlas_cost_each / n_atlas for cid in atlas_verdicts}
    arms: dict[str, dict[str, Any]] = {
        atlas_arm_name: arm_metrics(atlas_arm_name, expected,
                                    atlas_verdicts, atlas_costs, atlas_lat),
    }
    run_verdicts: list[dict[str, str]] = []
    for run in baseline_runs:
        name = str(run.get("run_id", f"baseline-{len(arms)}"))
        verdicts = {c.get("claim_id"): c.get("verdict", "unverifiable")
                    for c in run.get("claims", [])
                    if c.get("claim_id") in expected}
        costs = {c.get("claim_id"): float(c.get("cost_usd", 0.0) or 0.0)
                 for c in run.get("claims", [])
                 if c.get("claim_id") in expected}
        lat = {c.get("claim_id"): c.get("latency_s")
               for c in run.get("claims", [])
               if c.get("claim_id") in expected}
        arms[name] = arm_metrics(name, expected, verdicts, costs, lat)
        run_verdicts.append(verdicts)
    consistency_panel = (consistency(run_verdicts[0], run_verdicts[1])
                         if len(run_verdicts) >= 2 else None)
    return {
        "n_claims": len(expected),
        "atlas_summary_ref": atlas_summary.get("summary_path",
                                               "reused-m2d-summary"),
        "arms": arms,
        "baseline_consistency_run1_vs_run2": consistency_panel,
        "leaders": _leaders(arms),
    }


def render_text_table(report: dict[str, Any]) -> str:
    """Fixed-width comparison table (console + M8.md embedding)."""
    arms = report.get("arms", {})
    names = list(arms)
    rows: list[tuple[str, list[str]]] = []

    def _fmt_acc(m: dict) -> str:
        acc = m["accuracy_decided"]
        acc_s = f"{acc:.3f}" if acc is not None else "n/a"
        return (f"{m['correct']}/{m['decided']}={acc_s} "
                f"[{m['wilson_lo']:.3f},{m['wilson_hi']:.3f}]")

    def _fmt_lat(m: dict) -> str:
        lat = m["latency_s"]
        p50 = f"{lat['p50']:.1f}" if lat["p50"] is not None else "n/a"
        p95 = f"{lat['p95']:.1f}" if lat["p95"] is not None else "n/a"
        return f"p50 {p50}s / p95 {p95}s"

    rows.append(("n", [str(arms[a]["n"]) for a in names]))
    rows.append(("decided", [str(arms[a]["decided"]) for a in names]))
    rows.append(("accuracy (Wilson95)",
                 [_fmt_acc(arms[a]) for a in names]))
    rows.append(("abstention",
                 [f"{arms[a]['abstention_count']} "
                  f"({arms[a]['abstention_rate']:.3f})" for a in names]))
    rows.append(("hallucinations FALSE->contra",
                 [str(arms[a]["n_hallucinations"]) for a in names]))
    rows.append(("falsos contra on TRUE",
                 [str(len(arms[a]["falsos_contradicted"])) for a in names]))
    rows.append(("falsos supported on FALSE",
                 [str(len(arms[a]["falsos_supported"])) for a in names]))
    rows.append(("cost/claim USD",
                 [f"${arms[a]['cost_per_claim_usd']:.4f}" for a in names]))
    rows.append(("total USD",
                 [f"${arms[a]['total_cost_usd']:.4f}" for a in names]))
    rows.append(("latency/claim", [_fmt_lat(arms[a]) for a in names]))
    cons = report.get("baseline_consistency_run1_vs_run2") or {}
    if cons:
        rows.append(("run1-vs-run2 agree",
                     ["" if i else
                      f"{cons['agree']}/{cons['n']}={cons['agreement_rate']:.3f}"
                      for i in range(len(names))]))
    width = max([len(r[0]) for r in rows] + [8])
    head = "metric".ljust(width) + " | " + " | ".join(names)
    bar = "-" * len(head)
    lines = [head, bar]
    for metric, vals in rows:
        lines.append(metric.ljust(width) + " | " + " | ".join(vals))
    leaders = report.get("leaders", {})
    lines.append(bar)
    for metric, arm in leaders.items():
        lines.append(f"leader {metric}: {arm}")
    return "\n".join(lines)
