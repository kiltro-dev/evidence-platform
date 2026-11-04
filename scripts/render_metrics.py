#!/usr/bin/env python3
"""Render canonical metric blocks from live/claims_registry.json.

Usage:  PYTHONPATH=. python3 scripts/render_metrics.py [--check]

Rewrites the text between METRICS markers in README.md,
live/demo/SUBMISSION.md, live/demo/script.md and
live/demo/RECORDING_GUIDE.md. With --check, exits 1 if any block
differs (used by scripts/check_claims.py).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "live" / "claims_registry.json"


def load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def by_id(reg: dict) -> dict:
    return {c["id"]: c for c in reg["claims"]}


def readme_block(reg: dict) -> str:
    c = by_id(reg)
    rows = [
        ("Atlas recall, DEV (n=36)",
         f"{c['dev_recall']['n']}/{c['dev_recall']['d']} = "
         f"{c['dev_recall']['value']:.3f}, recall Wilson "
         f"{c['dev_recall']['recall_wilson']}", "V5-1 guard",
         c['dev_recall']['not_proven']),
        ("Atlas recall, sealed retention (n=18)",
         f"{c['ret_recall']['n']}/{c['ret_recall']['d']} = "
         f"{c['ret_recall']['value']:.3f}, recall Wilson "
         f"{c['ret_recall']['recall_wilson']}", "V6 validation (ret ran 3x, seal kept)",
         c['ret_recall']['not_proven']),
        ("Decided accuracy (decided-accuracy Wilsons)",
         c['decided_accuracy']['value'], "V5-1 + V6",
         c['decided_accuracy']['not_proven']),
        ("Verdict latency / cost", "p50 ~5 s / p95 ~40 s / ~$0.007 per claim",
         "V5-1 + V6 runs", c['latency_cost']['not_proven']),
        ("Baseline recall, retention (Atlas loses)",
         c['baseline_ret']['value'], "F2 retention baseline",
         c['baseline_ret']['not_proven']),
        ("Baseline citation fidelity, DEV pooled (fidelity Wilson)",
         c['citation_fidelity']['value'] + "; NOT directly comparable to "
         "Atlas fidelity (different mechanisms: audit vs package-verified)",
         "F2-1 audit", c['citation_fidelity']['not_proven']),
        ("Said-vs-shown planted", c['said_shown']['value'], "M3",
         c['said_shown']['not_proven']),
        ("Canaries", c['canaries']['value'], "M6 live run",
         c['canaries']['not_proven']),
        ("Live offline tests", c['tests_live']['value'],
         "gate 2026-11-02", c['tests_live']['not_proven']),
        ("v1 core offline tests (historical)", c['tests_v1']['value'],
         "gate 2026-11-02", c['tests_v1']['not_proven']),
    ]
    out = ["| Metric | Value | Experiment | Does NOT prove |",
           "|---|---|---|---|"]
    out += [f"| {m} | {v} | {e} | {n} |" for m, v, e, n in rows]
    return "\n".join(out) + "\n"


def submission_impact_block(reg: dict) -> str:
    c = by_id(reg)
    return (
        f"On the fair benchmark v2 (36 DEV claims + 18 sealed retention claims, "
        f"preregistered), Atlas Live decides "
        f"{c['dev_recall']['n']}/{c['dev_recall']['d']} = "
        f"{c['dev_recall']['value']:.3f} (recall Wilson "
        f"{c['dev_recall']['recall_wilson']}) on DEV and "
        f"{c['ret_recall']['n']}/{c['ret_recall']['d']} = "
        f"{c['ret_recall']['value']:.3f} (recall Wilson "
        f"{c['ret_recall']['recall_wilson']}) on retention with zero false "
        f"verdicts on either set ({c['decided_accuracy']['pins'][0]} "
        f"[0.871,1.000] + {c['decided_accuracy']['pins'][1]} [0.785,1.000] "
        f"decided-accuracy Wilsons; coverage and precision always reported "
        f"together), "
        f"verdict latency p50 ~5 s / p95 ~40 s at ~$0.007/claim "
        f"(invest-to-verdict only), said-vs-shown {c['said_shown']['value']} "
        f"planted (40% said vs 0.97% shown), canaries 7/7 decided of 12 "
        f"(5 abstained, all FALSE-side), "
        f"proof bundles {c['bundle_proof']['value']}, Spanish ASR "
        f"{c['asr_voice']['value']}, and a planted speaker self-contradiction "
        f"caught (China 1.41B vs 141M, ~59 s apart in the native-ES keynote). "
        f"The model-only baseline beats us on recall "
        f"({c['baseline_dev']['value'].split(' ')[0]} DEV, "
        f"{c['baseline_ret']['value'].split(' ')[0]} retention) — stated plainly "
        f"— but only {c['citation_fidelity']['value'].split(' ')[0]} = "
        f"{c['citation_fidelity']['value'].split(' ')[2]} (fidelity Wilson "
        f"[0.077,0.250]) of its citations are faithful (verbatim quote + "
        f"figure within 2% on the cited URL; NOT directly comparable to Atlas "
        f"fidelity, different mechanisms) and it exports zero re-runnable "
        f"proof.\n"
    )


def submission_bench_block(reg: dict) -> str:
    c = by_id(reg)
    return (
        f"- Fair benchmark v2 (F2-1, `eval2-20261101-225539-report.json` + "
        f"fidelity JSONs): Atlas DEV {c['dev_recall']['n']}/"
        f"{c['dev_recall']['d']} vs model-only baseline pooled "
        f"{c['baseline_dev']['value']} (run1 35/36, run2 35/36, consistency "
        f"36/36) and retention {c['baseline_ret']['value']}. The baseline wins "
        f"recall — stated plainly. Where it fails: citation fidelity pooled "
        f"{c['citation_fidelity']['value']} (r1 5/31, r2 4/32; "
        f"quote_not_found 34, unit_mismatch 13, numeric_mismatch 7; 72/72 urls "
        f"model-stated, 0 browsed), 2 false contradicts (D3x2), and zero "
        f"offline proof. Source: `live/F2_BENCH.md`.\n"
    )


def recording_block(reg: dict) -> str:
    c = by_id(reg)
    n, d = c['ret_recall']['n'], c['ret_recall']['d']
    words = {12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen",
             16: "sixteen", 17: "seventeen", 18: "eighteen"}
    dn, dd = c['dev_recall']['n'], c['dev_recall']['d']
    return (
        f"- DO: show the metrics panel (`live/claims_registry.json`): DEV "
        f"{dn}/{dd} = {dn / dd:.3f} (recall Wilson "
        f"{c['dev_recall']['recall_wilson']}), retention "
        f"{n}/{d} = {n / d:.3f} (recall Wilson "
        f"{c['ret_recall']['recall_wilson']}), decided 26/26 [0.871,1.000] + "
        f"14/14 [0.785,1.000] (decided-accuracy Wilsons), zero false verdicts "
        f"on benchmark v2, p50 ~5 s / p95 ~40 s / ~$0.007 per claim, "
        f"Muse-only baseline wins recall but only 9/63 = 0.143 (fidelity "
        f"Wilson [0.077,0.250]) of its quotes exist.\n"
        f"- SAY: \"On sealed ground truth: {words.get(n, n)} of {words.get(d, d)}, "
        f"zero false verdicts, five-second median. The raw model answers more "
        f"— but only fourteen percent of its quotes are real. We abstain "
        f"instead of guessing.\"\n"
    )


def script_block(reg: dict) -> str:
    c = by_id(reg)
    return (
        f"- Figures (V6 frozen): DEV {c['dev_recall']['n']}/"
        f"{c['dev_recall']['d']} = {c['dev_recall']['value']:.3f} (recall "
        f"Wilson {c['dev_recall']['recall_wilson']}), retention "
        f"{c['ret_recall']['n']}/{c['ret_recall']['d']} = "
        f"{c['ret_recall']['value']:.3f} (recall Wilson "
        f"{c['ret_recall']['recall_wilson']}), decided "
        f"{c['decided_accuracy']['pins'][0]} [0.871,1.000] + "
        f"{c['decided_accuracy']['pins'][1]} [0.785,1.000] (decided-accuracy "
        f"Wilsons), 0 false verdicts, baseline "
        f"{c['baseline_ret']['value'].split(' ')[0]} retention with fidelity "
        f"{c['citation_fidelity']['value'].split(' ')[0]} = 0.143 (fidelity "
        f"Wilson [0.077,0.250]; NOT comparable to Atlas fidelity, different "
        f"mechanisms).\n"
    )


def blocks(reg: dict) -> dict:
    return {
        "README.md": {"METRICS": readme_block(reg)},
        "live/demo/SUBMISSION.md": {
            "METRICS:IMPACT": submission_impact_block(reg),
            "METRICS:BENCH": submission_bench_block(reg),
        },
        "live/demo/RECORDING_GUIDE.md": {"METRICS": recording_block(reg)},
        "live/demo/script.md": {"METRICS": script_block(reg)},
    }


def apply_block(text: str, name: str, body: str) -> str:
    start = f"<!-- {name}:START -->"
    end = f"<!-- {name}:END -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end),
                         re.DOTALL)
    if start not in text or end not in text:
        raise SystemExit(f"markers {start} ... {end} not found")
    return pattern.sub(start + "\n" + body.rstrip("\n") + "\n" + end, text,
                       count=1)


def main(check: bool = False) -> int:
    reg = load_registry()
    failed = False
    for rel, names in blocks(reg).items():
        path = REPO / rel
        text = path.read_text(encoding="utf-8")
        for name, body in names.items():
            try:
                new = apply_block(text, name, body)
            except SystemExit as exc:
                print(f"FAIL {rel}: {exc}")
                return 1
            if new != text:
                if check:
                    print(f"FAIL {rel} [{name}]: block differs from registry "
                          f"(run render_metrics.py)")
                    failed = True
                else:
                    path.write_text(new, encoding="utf-8")
                    print(f"rendered {rel} [{name}]")
                    text = new
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv[1:]))
