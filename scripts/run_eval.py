#!/usr/bin/env python3
"""Run evaluation cases against live sources and score honest metrics.

Metrics (all defined in docs/EVAL.md):
  - stance_accuracy: expected stance == observed stance
  - domain_accuracy: expected domain == observed domain
  - retrieval_hit: >= min_evidences retrieved (when the case requires it)
  - verdict_recall: expected verdict present among comparisons
  - uncertainty_recall: expected uncertainty codes present / absent as required
  - traceability: every cited comparison id resolves and links to an evidence

Usage: PYTHONPATH=src .venv/bin/python scripts/run_eval.py [--db PATH]
Writes JSON results to eval/results/<utc-timestamp>.json (gitignored).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

from atlas.pipeline.run import analyze  # noqa: E402
from cases import CASES  # noqa: E402


def score_case(case: dict, **analyze_kw) -> dict:
    report = analyze(case["text"], llm=None, persist=False, **analyze_kw)
    exp = case["expected"]
    checks: dict[str, bool] = {}
    observed = {
        "stance": report.conclusion.stance,
        "domain": report.claim.domain,
        "n_evidences": len(report.evidences),
        "verdicts": sorted({c.verdict for c in report.comparisons}),
        "uncertainties": sorted({u.code for u in report.uncertainties}),
    }
    if "stance" in exp:
        checks["stance"] = observed["stance"] == exp["stance"]
    if "domain" in exp:
        checks["domain"] = observed["domain"] == exp["domain"]
    if "min_evidences" in exp:
        checks["retrieval"] = observed["n_evidences"] >= exp["min_evidences"]
    if "verdict_present" in exp:
        checks["verdict"] = exp["verdict_present"] in observed["verdicts"]
    for code in exp.get("uncertainty_present", []):
        checks[f"unc+{code}"] = code in observed["uncertainties"]
    for code in exp.get("uncertainty_absent", []):
        checks[f"unc-{code}"] = code not in observed["uncertainties"]
    # traceability: every cited comparison resolves and points at real evidence
    by_id = {c.comparison_id: c for c in report.comparisons}
    ev_ids = {e.evidence_id for e in report.evidences}
    cited = (report.conclusion.support_comparison_ids
             + report.conclusion.contradict_comparison_ids)
    checks["traceability"] = all(
        cid in by_id and by_id[cid].evidence_id in ev_ids for cid in cited
    )
    passed = all(checks.values())
    return {"id": case["id"], "passed": passed, "checks": checks,
            "observed": observed, "report_id": report.report_id}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    if args.db:
        os.environ["ATLAS_DB"] = args.db
    results = [score_case(c) for c in CASES]
    n_pass = sum(1 for r in results for _ in [0] if r["passed"])
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "degraded (deterministic only, llm=None)",
        "cases": len(results),
        "passed": n_pass,
        "stance_accuracy": sum(1 for r in results if r["checks"].get("stance", True))
        / len(results),
        "results": results,
    }
    outdir = ROOT / "eval" / "results"
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (outdir / f"{stamp}.json").write_text(json.dumps(summary, indent=2))
    print(f"{n_pass}/{len(results)} cases passed "
          f"(stance accuracy {summary['stance_accuracy']:.2f})")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        failed = [k for k, v in r["checks"].items() if not v]
        print(f"  [{mark}] {r['id']} stance={r['observed']['stance']}"
              + (f" failed={failed}" if failed else ""))
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
