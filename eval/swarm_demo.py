"""Swarm long-doc demo: 1 live run over the synthetic fixture.

Runs check_document_swarm() over eval/fixtures/long_report_synth.txt
with the real MetaClient and prints the metric: N claims mapped,
verdict roll-up, figures mapped, wall-time, tokens, USD estimate.

Budget: refuses to run when the rough input estimate exceeds
MAX_USD (default $4). Secrets: the client reads .env itself; this
script prints usage/tokens only, never keys or URLs.

Usage:
    .venv/bin/python eval/swarm_demo.py [--max-claims N] [--persist]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.models.meta import MetaClient  # noqa: E402
from atlas.swarm.coordinator import (  # noqa: E402
    DEFAULT_PRICING,
    check_document_swarm,
)

FIXTURE = (Path(__file__).resolve().parent / "fixtures"
           / "long_report_synth.txt")
RESULTS_DIR = Path(__file__).resolve().parent / "results"
MAX_USD = 4.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Swarm long-doc live demo.")
    ap.add_argument("--max-claims", type=int, default=None,
                    help="cap verified claims (default: all)")
    ap.add_argument("--persist", action="store_true",
                    help="persist per-claim pipeline reports (default: off)")
    args = ap.parse_args()

    text = FIXTURE.read_text(encoding="utf-8")
    rough_input_tokens = len(text) / 4 * 3  # chunk re-reads + roles
    rough_usd = (rough_input_tokens / 1000
                 * DEFAULT_PRICING["prompt_usd_per_1k"] * 4)  # wide margin
    print(f"fixture: {FIXTURE.name} ({len(text)} chars, "
          f"~{len(text) / 3000:.1f} pages)")
    print(f"budget guard: rough estimate ${rough_usd:.2f} vs cap ${MAX_USD:.2f}")
    if rough_usd > MAX_USD:
        print("REFUSED: estimate exceeds budget. Pass --max-claims to shrink.")
        return 2

    llm = MetaClient()
    if not llm.available:
        print("REFUSED: MODEL_API_KEY not configured; refusing live run.")
        return 2

    t0 = time.perf_counter()
    done = 0

    def _progress(i, _res):
        nonlocal done
        done += 1
        print(f"  claim {done} done (index {i})", flush=True)

    out = check_document_swarm(text, llm=llm, limit=3,
                               persist=args.persist,
                               max_claims=args.max_claims,
                               on_claim=_progress)
    wall_s = time.perf_counter() - t0
    u = out["usage_total"]
    print("---- swarm demo metric ----")
    print(f"claims mapped : {out['claim_count']} "
          f"(chunks={out['meta']['chunks']}, errors={out['errors']})")
    print(f"figures mapped: {out['figures_mapped']}")
    print(f"verdicts      : {json.dumps(out['rollup'], sort_keys=True)}")
    roles_ok = sum(len(c.get("roles_ok", [])) for c in out["claims"]
                   if "roles_ok" in c)
    conflicts = sum(len(c.get("conflicts", [])) for c in out["claims"]
                    if "conflicts" in c)
    print(f"roles ok      : {roles_ok} role-runs, "
          f"{conflicts} conflict(s) disclosed")
    print(f"wall-time     : {wall_s:.1f}s (pipeline {out['elapsed_ms']/1000:.1f}s)")
    print(f"tokens        : prompt={u['prompt_tokens']} "
          f"completion={u['completion_tokens']} total={u['total_tokens']} "
          f"(stages={u['stages']})")
    print(f"USD estimate  : ${out['usd_estimate']:.4f} "
          f"(rates {DEFAULT_PRICING['prompt_usd_per_1k']}/"
          f"{DEFAULT_PRICING['completion_usd_per_1k']} per 1k)")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = RESULTS_DIR / f"swarm_demo_{stamp}.json"
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    print(f"saved         : {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
