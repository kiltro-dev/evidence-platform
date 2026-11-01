#!/usr/bin/env python3
"""Run evaluation cases and score honest metrics (benchmark v2).

Runner modes (--mode):
  - degraded: llm=None; fully offline. "live" cases are SKIPPED (recorded
    as skipped, excluded from pass/fail). "scripted" cases run via the
    local harness below; "both" cases run live but are offline-safe
    (resolution fails before any request is sent).
  - full:     llm=MetaClient(), constructed lazily HERE ONLY (imported and
    built inside main() under this branch; the degraded path never touches
    it). All cases run; live cases need network + MODEL_API_KEY.
  - both:     degraded pass + full pass, with a per-mode summary each.

Case modes (CASES[*]["mode"], default "live" when absent):
  - live:     needs real sources; skipped in degraded runs.
  - scripted: offline harness (stubbed connectors and/or scripted clients).
  - both:     runs in every runner mode.

Scripted harness ("script" key, or per-subcase "script"):
  - evidence: [ {connector?, label, unit, year, value, ...} ] canned
    evidence returned by EVERY registered connector (stubbed retrieve).
  - connector_error: true -> every connector raises httpx.ConnectError.
  - client: {kind: scripted|fault, responses?, faults?, default?} builds a
    ScriptedClient/FaultClient used as the llm for this case, overriding
    the runner llm. With no client spec the runner llm applies (so e.g.
    the injection case probes the template summary in degraded mode and
    the model summary in full mode).

Metrics (v1 kept, v2 added):
  - stance_accuracy: expected stance (or stance_set membership) matches
  - domain_accuracy, retrieval_hit, verdict checks, uncertainty checks
  - traceability: cited comparison ids resolve and link to an evidence
  - prose_traceability (auto): every [cmp_*] token in the summary resolves
  - abstention_any_of: >=1 listed abstention code present (opt-in)
  - degradation labeling (opt-in): extraction_actor / conclusion_actor /
    context_notes_contains pin which fallback path was taken
  - recompute_verdict (opt-in): each numeric verdict is re-derived
    independently from rel_diff_pct + the comparison's tolerance rule
  - supported_iff_both (opt-in): stance==supported iff >=2 numeric
    comparisons exist and all are consistent

Usage: PYTHONPATH=src .venv/bin/python scripts/run_eval.py [--mode M] [--db PATH]
Writes JSON results to eval/results/<utc-timestamp>.json (gitignored).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

import httpx  # noqa: E402  (dependency of connectors; used for fault stubs)

from atlas.connectors import REGISTRY  # noqa: E402
from atlas.core.types import Evidence  # noqa: E402
from atlas.models.scripted import FaultClient, ScriptedClient  # noqa: E402
from atlas.pipeline.run import analyze  # noqa: E402
from atlas.pipeline.synthesize import NON_VERIFYING_METRICS  # noqa: E402
from cases import CASES  # noqa: E402

_CMP_REF_RE = re.compile(r"\[cmp_[A-Za-z0-9_]+\]")
_TOL_RE = re.compile(r"<=\s*([0-9]*\.?[0-9]+)")
_DEFAULT_TOLERANCE = 0.02

_UNSET = object()  # sentinel: script sets no llm override


def build_client(spec: dict):
    """Build a ScriptedClient/FaultClient from a case script client spec."""
    kind = spec.get("kind", "scripted")
    if kind == "scripted":
        return ScriptedClient(
            responses=spec.get("responses", {}),
            default=spec.get("default"),
        )
    if kind == "fault":
        return FaultClient(
            responses=spec.get("responses", {}),
            faults=spec.get("faults", {}),
            default=spec.get("default"),
        )
    raise ValueError(f"unknown script client kind {kind!r}")


def _canned_evidence(claim, spec: dict) -> Evidence:
    label = spec.get("label", spec.get("connector", "worldbank"))
    year = spec.get("year")
    value = spec.get("value")
    return Evidence(
        claim_id=claim.claim_id,
        connector=spec.get("connector", "worldbank"),
        source_name=spec.get("source_name", "scripted-harness"),
        source_url=spec.get("source_url"),
        source_kind=spec.get("source_kind", "secondary"),
        evidence_kind=spec.get("evidence_kind", "direct"),
        locator=spec.get("locator", f"{label} {year}"),
        excerpt=spec.get("excerpt", f"{label} in {year}: {value}"),
        payload={
            "label": label,
            "unit": spec.get("unit"),
            "year": year,
            "value": value,
            **{k: v for k, v in spec.items()
               if k not in {"connector", "source_name", "source_url",
                            "source_kind", "evidence_kind", "locator",
                            "excerpt", "label", "unit", "year", "value"}},
        },
    )


@contextlib.contextmanager
def scripted_env(script: dict):
    """Apply a case script: stub connectors, optionally build an llm client.

    Yields the llm override, or _UNSET when the script sets no client (the
    caller then uses the runner llm). All connector patches are reverted.
    """
    if not script:
        yield _UNSET
        return
    llm = _UNSET
    if "client" in script:
        llm = build_client(script["client"])
    patches = []
    if script.get("connector_error"):
        def _raise(claim, limit=5):
            raise httpx.ConnectError("simulated provider death (eval harness)")
        for conn in REGISTRY.values():
            patches.append(mock.patch.object(conn, "retrieve", _raise))
    elif "evidence" in script:
        specs = script["evidence"]

        def _stub(claim, limit=5):
            return [_canned_evidence(claim, s) for s in specs[:limit]]

        for conn in REGISTRY.values():
            patches.append(mock.patch.object(conn, "retrieve", _stub))
    try:
        for p in patches:
            p.start()
        yield llm
    finally:
        for p in reversed(patches):
            p.stop()


def _as_list(value) -> list:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _recompute_ok(report) -> tuple[bool, str]:
    """Independently re-derive numeric verdicts from rel_diff_pct.

    Returns (ok, detail). Requires >=1 numeric comparison; each must equal
    consistent-iff-rel_diff_pct<=tolerance with tolerance parsed from the
    comparison's own tolerance_rule (default 0.02). Not-comparable and
    non-verifying (time/year) checks are out of scope.
    """
    numeric = [
        c for c in report.comparisons
        if c.metric not in NON_VERIFYING_METRICS and c.rel_diff_pct is not None
    ]
    if not numeric:
        return False, "no numeric comparisons to recompute"
    for c in numeric:
        m = _TOL_RE.search(c.tolerance_rule or "")
        tol = float(m.group(1)) if m else _DEFAULT_TOLERANCE
        want = "consistent" if c.rel_diff_pct <= tol * 100.0 else "contradicts"
        if c.verdict != want:
            return False, (
                f"{c.comparison_id}: verdict={c.verdict} rel={c.rel_diff_pct} "
                f"tol={tol} -> want {want}"
            )
    return True, f"{len(numeric)} verdict(s) match recomputation"


def score_report(report, exp: dict) -> tuple[dict, dict]:
    checks: dict[str, bool] = {}
    observed = {
        "stance": report.conclusion.stance,
        "domain": report.claim.domain,
        "n_evidences": len(report.evidences),
        "n_comparisons": len(report.comparisons),
        "n_quantities": len(report.claim.quantities),
        "verdicts": sorted({c.verdict for c in report.comparisons}),
        "uncertainties": sorted({u.code for u in report.uncertainties}),
        "extraction_actor": report.claim.extraction_actor,
        "conclusion_actor": report.conclusion.actor,
        "summary": report.conclusion.summary,
    }
    if "stance" in exp:
        checks["stance"] = observed["stance"] == exp["stance"]
    if "stance_set" in exp:
        checks["stance_set"] = observed["stance"] in exp["stance_set"]
    if "domain" in exp:
        checks["domain"] = observed["domain"] == exp["domain"]
    if "domain_set" in exp:
        checks["domain_set"] = observed["domain"] in exp["domain_set"]
    if "min_evidences" in exp:
        checks["retrieval"] = observed["n_evidences"] >= exp["min_evidences"]
    if "min_comparisons" in exp:
        checks["comparisons"] = observed["n_comparisons"] >= exp["min_comparisons"]
    if "min_quantities" in exp:
        checks["quantities_min"] = observed["n_quantities"] >= exp["min_quantities"]
    if "max_quantities" in exp:
        checks["quantities_max"] = observed["n_quantities"] <= exp["max_quantities"]
    if "verdict_present" in exp:
        checks["verdict"] = exp["verdict_present"] in observed["verdicts"]
    if "verdict_absent" in exp:
        for v in _as_list(exp["verdict_absent"]):
            checks[f"verdict-{v}"] = v not in observed["verdicts"]
    for code in exp.get("uncertainty_present", []):
        checks[f"unc+{code}"] = code in observed["uncertainties"]
    for code in exp.get("uncertainty_absent", []):
        checks[f"unc-{code}"] = code not in observed["uncertainties"]
    if "abstention_any_of" in exp:
        checks["abstention"] = any(
            c in observed["uncertainties"] for c in exp["abstention_any_of"]
        )
    # degradation labeling: which fallback path was taken
    if "extraction_actor" in exp:
        checks["extract_actor"] = (
            observed["extraction_actor"] == exp["extraction_actor"]
        )
    if "conclusion_actor" in exp:
        checks["conclusion_actor"] = (
            observed["conclusion_actor"] == exp["conclusion_actor"]
        )
    for sub in exp.get("context_notes_contains", []):
        checks[f"notes+{sub}"] = sub in (report.claim.context_notes or "")
    for sub in exp.get("summary_forbidden_substrings", []):
        checks[f"summary!{sub}"] = sub.lower() not in observed["summary"].lower()
    if exp.get("recompute_verdict"):
        ok, detail = _recompute_ok(report)
        checks["recompute_verdict"] = ok
        observed["recompute_detail"] = detail
    if exp.get("supported_iff_both"):
        numeric = [
            c for c in report.comparisons
            if c.metric not in NON_VERIFYING_METRICS
            and c.verdict in ("consistent", "contradicts")
        ]
        rhs = len(numeric) >= 2 and all(
            c.verdict == "consistent" for c in numeric
        )
        checks["supported_iff_both"] = (observed["stance"] == "supported") == rhs
        observed["iff_detail"] = (
            f"stance={observed['stance']} numeric="
            f"{[c.verdict for c in numeric]}"
        )
    # traceability: every cited comparison resolves and links to an evidence
    by_id = {c.comparison_id: c for c in report.comparisons}
    ev_ids = {e.evidence_id for e in report.evidences}
    cited = (report.conclusion.support_comparison_ids
             + report.conclusion.contradict_comparison_ids)
    checks["traceability"] = all(
        cid in by_id and by_id[cid].evidence_id in ev_ids for cid in cited
    )
    # prose traceability: every [cmp_*] token in the summary resolves
    tokens = {t[1:-1] for t in _CMP_REF_RE.findall(observed["summary"])}
    checks["prose_traceability"] = tokens <= set(by_id)
    return checks, observed


def run_text(text: str, script: dict, runner_llm, **analyze_kw):
    with scripted_env(script) as override:
        llm = runner_llm if override is _UNSET else override
        return analyze(text, llm=llm, persist=False, **analyze_kw)


def _effective_exp(holder: dict, is_full: bool) -> dict:
    """Merge mode-specific expectation overlays.

    `expected_full` replaces base keys when running --mode full (the real
    model legitimately extracts differently: assigned units instead of the
    bare-number path, specific-vs-general domain choices). `drop_in_full`
    removes base keys (e.g. domain checks that are incidental on
    abstention cases). Overlays document observed model behavior; they
    must never weaken stance or traceability requirements.
    """
    exp = dict(holder.get("expected", {}))
    if is_full:
        for key in holder.get("drop_in_full", []):
            exp.pop(key, None)
        exp.update(holder.get("expected_full", {}))
    return exp


def score_case(case: dict, runner_llm, is_full: bool = False,
               **analyze_kw) -> dict:
    if "subcases" in case:
        subs = []
        for sub in case["subcases"]:
            report = run_text(
                sub["text"], sub.get("script", {}), runner_llm, **analyze_kw
            )
            checks, observed = score_report(
                report, _effective_exp(sub, is_full))
            subs.append({
                "name": sub.get("name", sub["text"][:40]),
                "passed": all(checks.values()),
                "checks": checks,
                "observed": {k: v for k, v in observed.items()
                             if k != "summary"},
                "report_id": report.report_id,
            })
        passed = all(s["passed"] for s in subs)
        return {"id": case["id"], "passed": passed, "subcases": subs,
                "mode": case.get("mode", "live")}
    report = run_text(case["text"], case.get("script", {}), runner_llm,
                      **analyze_kw)
    checks, observed = score_report(
        report, _effective_exp(case, is_full))
    return {"id": case["id"], "passed": all(checks.values()), "checks": checks,
            "observed": observed, "report_id": report.report_id,
            "mode": case.get("mode", "live")}


def _stance_ok(result: dict) -> bool:
    if "subcases" in result:
        return all(
            s["checks"].get("stance", True)
            and s["checks"].get("stance_set", True)
            and s["checks"].get("supported_iff_both", True)
            for s in result["subcases"]
        )
    return (result["checks"].get("stance", True)
            and result["checks"].get("stance_set", True)
            and result["checks"].get("supported_iff_both", True))


def run_suite(cases: list[dict], runner_llm, skip_live: bool,
              is_full: bool = False) -> dict:
    results, skipped = [], []
    for case in cases:
        if skip_live and case.get("mode", "live") == "live":
            skipped.append(case["id"])
            continue
        try:
            results.append(score_case(case, runner_llm, is_full=is_full))
        except Exception as exc:  # a crashing case fails, never kills eval
            results.append({"id": case["id"], "passed": False, "checks": {},
                            "error": f"{type(exc).__name__}: {exc}"})
    n_pass = sum(1 for r in results if r["passed"])
    return {
        "cases": len(cases),
        "executed": len(results),
        "skipped": skipped,
        "passed": n_pass,
        "stance_accuracy": (sum(1 for r in results if _stance_ok(r))
                            / len(results) if results else 1.0),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    parser.add_argument("--mode", choices=["degraded", "full", "both"],
                        default="degraded")
    args = parser.parse_args()
    if args.db:
        os.environ["ATLAS_DB"] = args.db
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    modes: dict[str, dict] = {}
    if args.mode in ("degraded", "both"):
        # llm=None: the degraded path never imports or constructs MetaClient.
        modes["degraded"] = run_suite(CASES, None, skip_live=True)
    if args.mode in ("full", "both"):
        # Lazy: MetaClient exists only on the full path (needs network+key).
        from atlas.models.meta import MetaClient

        modes["full"] = run_suite(CASES, MetaClient(), skip_live=False,
                                  is_full=True)
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "modes": modes,
    }
    outdir = ROOT / "eval" / "results"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"{stamp}.json").write_text(json.dumps(summary, indent=2))
    code = 0
    for name, suite in modes.items():
        print(f"[{name}] {suite['passed']}/{suite['executed']} cases passed "
              f"(stance accuracy {suite['stance_accuracy']:.2f}, "
              f"skipped {len(suite['skipped'])})")
        if suite["skipped"]:
            print(f"  skipped (need live sources): {suite['skipped']}")
        for r in suite["results"]:
            mark = "PASS" if r["passed"] else "FAIL"
            if "subcases" in r:
                print(f"  [{mark}] {r['id']}")
                for s in r["subcases"]:
                    smark = "PASS" if s["passed"] else "FAIL"
                    failed = [k for k, v in s["checks"].items() if not v]
                    print(f"    [{smark}] {s['name']}"
                          + (f" failed={failed}" if failed else ""))
            else:
                failed = [k for k, v in r.get("checks", {}).items() if not v]
                err = f" error={r['error']}" if "error" in r else ""
                print(f"  [{mark}] {r['id']}"
                      + (f" failed={failed}" if failed else "") + err)
        if suite["passed"] != suite["executed"]:
            code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
