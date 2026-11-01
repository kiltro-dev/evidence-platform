"""M8 ablation runner: baseline over the 31 verifiable EN claims.

Runs the model-only judge (``baseline.judge_claim``) with semaphore 4
and a 120 s deadline per claim, saves one
``data/runs/ablation-<stamp>-run<N>.json`` per run, then compares
against the REUSED Atlas M2d summary (Atlas is never re-run here) via
``report.compare`` and saves ``ablation-<stamp>-report.json``.

Cost guards (LIVE budget $8): a small serial probe (default 2 claims)
projects cost/claim BEFORE the full runs. If the projection exceeds
$0.30/claim or ``runs * n_claims * probe`` exceeds the budget, the
second baseline run is skipped and the report carries an honest note.
Probe results are discarded so both runs stay independent.

Usage (repo root on sys.path; MODEL_API_KEY from env — source .env)::

    .venv/bin/python live/ablation/run.py
    .venv/bin/python live/ablation/run.py --limit 3 --runs 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from live.ablation.baseline import (  # noqa: E402
    BaselineResult,
    judge_claim,
    load_transcript,
)
from live.ablation.report import compare, render_text_table  # noqa: E402
from live.run_verify import load_entries  # noqa: E402

LIVE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = LIVE_DIR.parent
DEFAULT_TRUTH = LIVE_DIR / "material" / "truth.json"
DEFAULT_TRANSCRIPT = REPO_ROOT / "data" / "keynote" / "keynote_en.srt"
DEFAULT_ATLAS_SUMMARY = (REPO_ROOT / "data" / "runs" /
                         "verify-20261101-212343.jsonl.summary.json")

MAX_CONCURRENCY = 4
CLAIM_DEADLINE_S = 120.0
BUDGET_USD = 8.0
MAX_COST_PER_CLAIM_USD = 0.30
PROBE_CLAIMS = 2
DEFAULT_RUNS = 2


def decide_runs(probe_cost_per_claim: float, n_claims: int,
                requested_runs: int, budget_usd: float = BUDGET_USD,
                max_per_claim_usd: float = MAX_COST_PER_CLAIM_USD,
                ) -> tuple[int, str | None]:
    """Runs to execute after the probe (pure, tested).

    Returns (runs, note): ``note`` is None when both runs proceed,
    else the honest reason the second run was skipped.
    """
    if requested_runs <= 1:
        return requested_runs, None
    if probe_cost_per_claim > max_per_claim_usd:
        return 1, (f"cost guard: probe ${probe_cost_per_claim:.4f}/claim "
                   f"> ${max_per_claim_usd:.2f}/claim -> 1 baseline run")
    projected = probe_cost_per_claim * n_claims * requested_runs
    if projected > budget_usd:
        return 1, (f"budget guard: projected ${projected:.2f} "
                   f"> ${budget_usd:.2f} -> 1 baseline run")
    return requested_runs, None


def default_out_prefix() -> str:
    """data/runs/ablation-<UTC-timestamp> (suffixes appended per file)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return str(REPO_ROOT / "data" / "runs" / f"ablation-{stamp}")


def build_parser() -> argparse.ArgumentParser:
    """CLI flags for the M8 ablation run."""
    parser = argparse.ArgumentParser(
        description="Atlas Live M8: model-only baseline vs reused "
        "Atlas M2d summary, with honest comparison report.")
    parser.add_argument("--claims", default=str(DEFAULT_TRUTH))
    parser.add_argument("--lang", default="en")
    parser.add_argument("--limit", type=int, default=None,
                        help="judge only the first N checkable claims")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS,
                        help="independent baseline runs "
                        f"(default: {DEFAULT_RUNS})")
    parser.add_argument("--transcript", default=str(DEFAULT_TRANSCRIPT))
    parser.add_argument("--atlas-summary", default=str(DEFAULT_ATLAS_SUMMARY),
                        help="reused Atlas M2d .summary.json "
                        "(Atlas is never re-run here)")
    parser.add_argument("--out-prefix", default=None,
                        help="output prefix (default: "
                        "data/runs/ablation-<ts>)")
    parser.add_argument("--max-concurrency", type=int,
                        default=MAX_CONCURRENCY)
    parser.add_argument("--deadline", type=float, default=CLAIM_DEADLINE_S)
    parser.add_argument("--budget", type=float, default=BUDGET_USD)
    parser.add_argument("--max-cost-per-claim", type=float,
                        default=MAX_COST_PER_CLAIM_USD)
    parser.add_argument("--probe-claims", type=int, default=PROBE_CLAIMS,
                        help="serial probe claims for cost projection "
                        "(0 = skip probe)")
    parser.add_argument("--api-key", default="",
                        help="Meta API key (default: MODEL_API_KEY env)")
    return parser


def _fresh_client(api_key: str, timeout_s: float) -> Any:
    """One ResponsesClient per claim (last_usage is per-call mutable)."""
    try:
        from src.atlas.models.responses import ResponsesClient
    except ImportError:
        from atlas.models.responses import ResponsesClient
    return ResponsesClient(api_key=api_key, timeout_s=timeout_s)


async def _judge_one(entry: dict, transcript: str, run_id: str,
                     api_key: str, deadline_s: float,
                     sem: asyncio.Semaphore) -> BaselineResult:
    """One claim through the judge (semaphore + deadline, never raises)."""
    async with sem:
        started = time.time()
        print(f"[m8] {run_id} {entry['id']} judging "
              f"({str(entry['text'])[:70]}...)", flush=True)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    judge_claim, _fresh_client(api_key, deadline_s),
                    entry["id"], entry["text"], transcript, run_id),
                timeout=deadline_s)
        except asyncio.TimeoutError:
            result = BaselineResult(
                claim_id=entry["id"], verdict="unverifiable",
                latency_s=time.time() - started, run_id=run_id,
                parse_ok=False, error=f"deadline {deadline_s}s exceeded")
        print(f"[m8] {run_id} {entry['id']} -> {result.verdict} in "
              f"{time.time() - started:.1f}s", flush=True)
        return result


async def amain(args: argparse.Namespace) -> dict:
    """Probe, run the baseline, compare vs reused Atlas, save JSON."""
    try:  # honor a local .env without requiring it (mirror MetaClient)
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    api_key = args.api_key or os.environ.get("MODEL_API_KEY", "")
    if not api_key:
        raise SystemExit("MODEL_API_KEY not configured; source .env first.")
    entries = load_entries(args.claims, args.lang, args.limit)
    if not entries:
        raise SystemExit(f"no checkable claims for lang={args.lang!r} "
                         f"in {args.claims}")
    transcript = load_transcript(args.transcript)
    if not transcript.strip():
        raise SystemExit(f"empty transcript: {args.transcript}")
    try:
        with open(args.atlas_summary, encoding="utf-8") as fh:
            atlas_summary = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"Atlas summary missing/unreadable ({args.atlas_summary}): "
            f"{exc}. Refusing to re-run Atlas silently — pass "
            "--atlas-summary explicitly.") from exc

    out_prefix = args.out_prefix or default_out_prefix()
    os.makedirs(os.path.dirname(out_prefix) or ".", exist_ok=True)

    # --- serial cost probe (discarded; runs stay independent) -----------
    probe_n = max(0, min(args.probe_claims, len(entries)))
    probe_cost = 0.0
    if probe_n:
        for entry in entries[:probe_n]:
            res = await asyncio.to_thread(
                judge_claim, _fresh_client(api_key, args.deadline),
                entry["id"], entry["text"], transcript, "probe")
            probe_cost += res.cost_usd
        print(f"[m8] probe: {probe_n} claims, "
              f"${probe_cost / probe_n:.4f}/claim", flush=True)
    probe_per_claim = (probe_cost / probe_n) if probe_n else 0.0
    n_runs, guard_note = decide_runs(probe_per_claim, len(entries),
                                     max(1, args.runs), args.budget,
                                     args.max_cost_per_claim)
    if guard_note:
        print(f"[m8] {guard_note}", flush=True)

    # --- full baseline runs ---------------------------------------------
    sem = asyncio.Semaphore(max(1, args.max_concurrency))
    wall_start = time.time()
    run_files: list[str] = []
    run_payloads: list[dict[str, Any]] = []
    for run_idx in range(1, n_runs + 1):
        run_id = f"baseline-run{run_idx}"
        results = await asyncio.gather(
            *[_judge_one(e, transcript, run_id, api_key, args.deadline, sem)
              for e in entries])
        payload = {
            "run_id": run_id,
            "claims_path": args.claims,
            "lang": args.lang,
            "transcript": args.transcript,
            "probe_cost_per_claim_usd": round(probe_per_claim, 6),
            "guard_note": guard_note,
            "total_cost_usd": round(sum(r.cost_usd for r in results), 4),
            "claims": [r.to_dict() for r in results],
        }
        run_path = f"{out_prefix}-{run_id}.json"
        with open(run_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        run_files.append(run_path)
        run_payloads.append(payload)
        print(f"[m8] {run_id}: saved {run_path} "
              f"(${payload['total_cost_usd']})", flush=True)
    wall_s = round(time.time() - wall_start, 1)

    # --- honest comparison (whatever the numbers say) -------------------
    report = compare(entries, atlas_summary, run_payloads)
    report["wall_s"] = wall_s
    report["guard_note"] = guard_note
    report["run_files"] = run_files
    report["atlas_summary"] = args.atlas_summary
    report_path = f"{out_prefix}-report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    report["report_path"] = report_path
    print(f"[m8] wall={wall_s}s guard={guard_note!r}", flush=True)
    print(render_text_table(report), flush=True)
    print(f"[m8] report: {report_path}", flush=True)
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint (argv injectable for tests)."""
    args = build_parser().parse_args(argv)
    asyncio.run(amain(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
