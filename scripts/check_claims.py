#!/usr/bin/env python3
"""Guard published numbers: recompute from artifacts, compare with docs.

Usage:  PYTHONPATH=src:live python3 scripts/check_claims.py

1. Recomputes every registry claim with a ``recompute`` spec from the
   committed artifacts (``live/eval2/scores/``) or from a pytest
   collect-only count, and fails when n/d differ from the registry.
2. Re-renders the METRICS blocks and fails when any doc differs.
3. Fails when a registry pin is missing from a listed doc.

Part of the offline gate (needs no network, no key, no data/).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from math import sqrt
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_render():
    spec = importlib.util.spec_from_file_location(
        "render_metrics", REPO / "scripts" / "render_metrics.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(max(0.0, (c - m) / d), 3), round(min(1.0, (c + m) / d), 3)


def load_scores(name: str) -> dict:
    return json.loads((REPO / "live" / "eval2" / "scores" / name).read_text())


def tally(run: dict) -> tuple[int, int, int]:
    """(decided, correct, false) for a scores run with expected+verdict."""
    dec = cor = fal = 0
    for c in run["claims"]:
        if c["verdict"] == "unverifiable":
            continue
        dec += 1
        if (c["expected"] == "true") == (c["verdict"] == "supported"):
            cor += 1
        else:
            fal += 1
    return dec, cor, fal


def check_scores(claim: dict, errors: list[str]) -> None:
    cid = claim["id"]
    spec = claim["recompute"] or ""
    if spec == "scores:recall":
        run = load_scores(Path(claim["artifact"]).name)
        dec, _cor, fal = tally(run)
        if dec != claim["n"] or len(run["claims"]) != claim["d"] or fal:
            errors.append(f"{cid}: recompute decided={dec}/{len(run['claims'])} "
                          f"false={fal}, registry {claim['n']}/{claim['d']}")
    elif spec == "scores:accuracy":
        tot_d = tot_c = tot_f = 0
        for name in ("dev-atlas.json", "ret-atlas.json"):
            dec, cor, fal = tally(load_scores(name))
            tot_d += dec
            tot_c += cor
            tot_f += fal
        if tot_d != claim["n"] or tot_c != claim["d"] or tot_f:
            errors.append(f"{cid}: recompute {tot_c}/{tot_d} false={tot_f}, "
                          f"registry {claim['d']}/{claim['n']}")
    elif spec == "scores:latency_cost":
        dev = load_scores("dev-atlas.json")
        ret = load_scores("ret-atlas.json")
        for name, run in (("dev", dev), ("ret", ret)):
            lat = sorted(c["latency_s"] for c in run["claims"])
            p50 = lat[min(len(lat) - 1, int(0.50 * len(lat)))]
            p95 = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
            mean_cost = run["total_cost_usd"] / len(run["claims"])
            if not (3.0 <= p50 <= 7.0 and 35.0 <= p95 <= 45.0
                    and mean_cost <= 0.010):
                errors.append(f"{cid}: {name} p50={p50} p95={p95} "
                              f"cost={mean_cost:.4f} outside published band")
    elif spec == "scores:pooled":
        blob = load_scores("dev-baseline.json")
        tot = cor = 0
        for run in blob["runs"]:
            for c in run["claims"]:
                tot += 1
                if (c["expected"] == "true") == (c["verdict"] == "supported"):
                    cor += 1
        if cor != claim["n"] or tot != claim["d"]:
            errors.append(f"{cid}: recompute {cor}/{tot}, "
                          f"registry {claim['n']}/{claim['d']}")
    elif spec == "scores:fidelity":
        blob = load_scores("dev-baseline-fidelity.json")
        n = sum(r["n_faithful"] for r in blob["runs"])
        d = sum(r["n_checked"] for r in blob["runs"])
        if n != claim["n"] or d != claim["d"]:
            errors.append(f"{cid}: recompute {n}/{d}, "
                          f"registry {claim['n']}/{claim['d']}")
    elif spec == "scores:canaries":
        panel = load_scores("canaries.json")["panel"]
        if (panel["n"] != claim["d"] or panel["decided"] != claim["n"]
                or panel["correct"] != claim["n"]):
            errors.append(f"{cid}: recompute {panel['correct']}/"
                          f"{panel['decided']} of {panel['n']}, registry "
                          f"{claim['n']}/{claim['n']} of {claim['d']}")
    elif spec == "scores:demo_verify":
        truth = {c["id"]: c
                 for c in json.loads(
                     (REPO / "live" / "material" / "truth.json")
                     .read_text(encoding="utf-8"))}
        dec = cor = 0
        for line in (REPO / claim["artifact"]).read_text(
                encoding="utf-8").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") != "claim.verdict":
                continue
            if ev.get("verdict") == "unverifiable":
                continue
            dec += 1
            exp = (truth.get(ev.get("claim_id")) or {}).get("expected")
            if (exp == "true") == (ev.get("verdict") == "supported"):
                cor += 1
        if cor != claim["n"] or dec != claim["d"]:
            errors.append(f"{cid}: recompute {cor}/{dec}, "
                          f"registry {claim['n']}/{claim['d']}")
    elif spec.startswith("collect:"):
        _check_collect(claim, errors)
    elif spec:
        errors.append(f"{cid}: unknown recompute spec {spec!r}")


def _check_collect(claim: dict, errors: list[str]) -> None:
    import os

    cid = claim["id"]
    targets = claim["recompute"].split(":", 1)[1].split()
    env = dict(os.environ)
    env["PYTHONPATH"] = "src:live" + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *targets, "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=300)
    count = 0
    for line in (proc.stdout or "").splitlines():
        if line.startswith("live/") or line.startswith("tests/"):
            count += 1
    if not count:  # fallback: "N tests collected" summary line
        for line in (proc.stdout or "").splitlines():
            if "collected" in line:
                digits = "".join(ch if ch.isdigit() else " "
                                  for ch in line).split()
                count = int(digits[0]) if digits else 0
    if count != claim["n"]:
        errors.append(f"{cid}: collect-only={count}, registry n={claim['n']}")


def check_wilson_intervals(reg: dict, errors: list[str]) -> None:
    c = {x["id"]: x for x in reg["claims"]}
    assert wilson(26, 26) == (0.871, 1.0), wilson(26, 26)
    assert wilson(14, 14) == (0.785, 1.0), wilson(14, 14)
    assert wilson(14, 18) == (0.548, 0.91), wilson(14, 18)
    assert wilson(9, 63) == (0.077, 0.25), wilson(9, 63)
    assert wilson(18, 18) == (0.824, 1.0), wilson(18, 18)
    assert wilson(70, 72) == (0.904, 0.992), wilson(70, 72)
    for text, interval in (
            (c["decided_accuracy"]["value"], ["0.871", "0.785"]),
            (c["citation_fidelity"]["value"], ["0.077", "0.250"]),
            (c["baseline_ret"]["value"], ["0.824"]),
            (c["baseline_dev"]["value"], ["0.904", "0.992"])):
        for frag in interval:
            if frag not in text:
                errors.append(f"wilson fragment {frag} missing: {text[:60]}")


def check_pins(reg: dict, errors: list[str]) -> None:
    for claim in reg["claims"]:
        for rel in claim.get("docs", []):
            try:
                text = (REPO / rel).read_text(encoding="utf-8")
            except FileNotFoundError:
                errors.append(f"{claim['id']}: doc missing {rel}")
                continue
            for pin in claim.get("pins", []):
                if pin not in text:
                    errors.append(f"{claim['id']}: pin {pin!r} missing "
                                  f"from {rel}")


def main() -> int:
    render = load_render()
    reg = render.load_registry()
    errors: list[str] = []
    for claim in reg["claims"]:
        if claim.get("recompute"):
            try:
                check_scores(claim, errors)
            except FileNotFoundError as exc:
                errors.append(f"{claim['id']}: artifact missing: {exc}")
    try:
        check_wilson_intervals(reg, errors)
    except AssertionError as exc:
        errors.append(f"wilson recompute mismatch: {exc}")
    check_pins(reg, errors)
    if render.main(check=True) != 0:
        errors.append("metric blocks differ from registry (run render)")
    if errors:
        print("check_claims FAILED:")
        for line in errors:
            print(f"  - {line}")
        return 1
    n = len(reg["claims"])
    print(f"check_claims OK: {n} claims recomputed + pinned + rendered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
