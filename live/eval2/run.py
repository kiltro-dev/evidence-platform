"""F2-1 eval2 runner: baseline vs Atlas over dev.json (2 runs each).

Reuse-only harness (F2-1 prereg, branch live):

- baseline arm: ``live.ablation.baseline.judge_claim`` (model-only judge,
  verbatim verdict), one fresh ResponsesClient per claim, semaphore 4,
  120 s deadline — the M8 pattern. The keynote transcript is replaced
  by a neutral standalone note (``EVAL2_CONTEXT``): F2 claims are
  independent sentences, not talk excerpts (declared in prereg.md).
- atlas arm: the full ``live.run_verify`` pipeline
  (``run_verify.run_claim`` + ``make_evidence_handler`` + store +
  ``verify_and_emit``), semaphore 4, 90 s deadline — the M2c pattern.

Metrics reuse ``live.ablation.report.arm_metrics`` /
``consistency`` + ``live.auditor.wilson``. Cost guards: a 1-claim
serial probe per arm projects the total BEFORE the full runs; when the
projection exceeds the budget (default $12) the second run per arm is
skipped and the report carries an honest note.

Retention (``retention.json``) stays SEALED until F2-2: this runner
defaults to ``dev.json`` and only reads whatever ``--claims`` points
at when measuring.

Usage (repo root on sys.path; MODEL_API_KEY from env — source .env)::

    .venv/bin/python live/eval2/run.py --arms baseline --limit 4
    .venv/bin/python live/eval2/run.py --arms both
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

from live import run_verify  # noqa: E402  (reuse: run_claim/handler/loader)
from live.ablation.baseline import BaselineResult, judge_claim  # noqa: E402
from live.ablation.report import arm_metrics, consistency  # noqa: E402
from live.auditor.wilson import wilson_interval  # noqa: E402
from live.orchestrator.bus import EventBus  # noqa: E402
from live.orchestrator.states import ClaimTracker  # noqa: E402
from live.library.store import EvidenceStore  # noqa: E402

LIVE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = LIVE_DIR.parent
DEFAULT_CLAIMS = LIVE_DIR / "eval2" / "dev.json"
DEFAULT_SNAPSHOT_DIR = REPO_ROOT / "data" / "snap"

EVAL2_CONTEXT = (
    "Standalone fact-checking task: each claim below is an independent "
    "sentence about the world (economics, demographics, physics, recent "
    "2026 events). There is no talk transcript; judge each claim only "
    "against reliable browsed sources, exactly as instructed."
)

MAX_CONCURRENCY = 4
BASELINE_DEADLINE_S = 120.0
ATLAS_DEADLINE_S = 90.0
BUDGET_USD = 12.0
MAX_COST_PER_CLAIM_USD = 0.30
PROBE_CLAIMS = 1
DEFAULT_RUNS = 2


# ---------------------------------------------------------------------------
# Pure helpers (tested offline)
# ---------------------------------------------------------------------------

def decide_total_runs(probe_per_claim: dict[str, float], n_claims: int,
                      requested_runs: int,
                      budget_usd: float = BUDGET_USD,
                      max_per_claim_usd: float = MAX_COST_PER_CLAIM_USD,
                      ) -> tuple[int, str | None]:
    """Runs per arm after the probe (pure, tested).

    ``probe_per_claim`` maps arm -> $/claim measured on the serial probe
    (probe claims are discarded so runs stay independent). Returns
    (runs, note): ``note`` is None when all requested runs proceed.
    """
    if requested_runs <= 1:
        return requested_runs, None
    for arm, cost in sorted(probe_per_claim.items()):
        if cost > max_per_claim_usd:
            return 1, (f"cost guard: {arm} probe ${cost:.4f}/claim "
                       f"> ${max_per_claim_usd:.2f}/claim -> 1 run/arm")
    per_claim = sum(probe_per_claim.values())
    projected = per_claim * n_claims * requested_runs
    if projected > budget_usd:
        return 1, (f"budget guard: projected ${projected:.2f} "
                   f"> ${budget_usd:.2f} -> 1 run/arm")
    return requested_runs, None


def pool_runs(expected: dict[str, str],
              run_verdicts: list[dict[str, str]]) -> tuple[dict, dict]:
    """Pool run1+run2 into one 2n denominator (prereg decision rule).

    Keys are namespaced ``{claim_id}::r{i}`` so both runs count; the
    expectation is duplicated per run. Pure, tested.
    """
    pooled_expected: dict[str, str] = {}
    pooled_verdicts: dict[str, str] = {}
    for i, verdicts in enumerate(run_verdicts, 1):
        for cid, exp in expected.items():
            key = f"{cid}::r{i}"
            pooled_expected[key] = exp
            pooled_verdicts[key] = verdicts.get(cid, "unverifiable")
    return pooled_expected, pooled_verdicts


def leaders(panels: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Best panel per metric, computed — never narrated (pure, tested).

    Higher wins: accuracy_decided (None excluded), n_hallucinations.
    Lower wins: falsos total, abstention_rate, cost_per_claim_usd,
    latency p50 (None excluded). Ties name every tied panel.
    """
    def _best(values: dict[str, Any], higher: bool) -> str:
        valid = {a: v for a, v in values.items() if v is not None}
        if not valid:
            return "n/a"
        target = (max if higher else min)(valid.values())
        return "+".join(sorted(a for a, v in valid.items()
                              if v == target))

    return {
        "accuracy_decided_higher": _best(
            {a: m["accuracy_decided"] for a, m in panels.items()}, True),
        "hallucinations_higher": _best(
            {a: m["n_hallucinations"] for a, m in panels.items()}, True),
        "falsos_total_lower": _best(
            {a: len(m["falsos_contradicted"]) + len(m["falsos_supported"])
             for a, m in panels.items()}, False),
        "abstention_rate_lower": _best(
            {a: m["abstention_rate"] for a, m in panels.items()}, False),
        "cost_per_claim_lower": _best(
            {a: m["cost_per_claim_usd"] for a, m in panels.items()}, False),
        "latency_p50_lower": _best(
            {a: m["latency_s"]["p50"] for a, m in panels.items()}, False),
    }


def default_out_prefix() -> str:
    """data/runs/eval2-<UTC-timestamp> (suffixes appended per file)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return str(REPO_ROOT / "data" / "runs" / f"eval2-{stamp}")


def build_parser() -> argparse.ArgumentParser:
    """CLI flags for the F2-1 eval2 run."""
    parser = argparse.ArgumentParser(
        description="F2-1: baseline vs Atlas over eval2 dev.json "
        "(default), 2 runs each, honest report.")
    parser.add_argument("--claims", default=str(DEFAULT_CLAIMS),
                        help="claims JSON (default: live/eval2/dev.json; "
                        "retention stays sealed until F2-2)")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--arms", default="both",
                        choices=("both", "baseline", "atlas"))
    parser.add_argument("--out-prefix", default=None)
    parser.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    parser.add_argument("--max-concurrency", type=int,
                        default=MAX_CONCURRENCY)
    parser.add_argument("--baseline-deadline", type=float,
                        default=BASELINE_DEADLINE_S)
    parser.add_argument("--atlas-deadline", type=float,
                        default=ATLAS_DEADLINE_S)
    parser.add_argument("--budget", type=float, default=BUDGET_USD)
    parser.add_argument("--max-cost-per-claim", type=float,
                        default=MAX_COST_PER_CLAIM_USD)
    parser.add_argument("--probe-claims", type=int, default=PROBE_CLAIMS)
    parser.add_argument("--api-key", default="",
                        help="Meta API key (default: MODEL_API_KEY env)")
    return parser


# ---------------------------------------------------------------------------
# Live arms (thin orchestration over reused pieces)
# ---------------------------------------------------------------------------

def _fresh_baseline_client(api_key: str, timeout_s: float) -> Any:
    """One ResponsesClient per claim (last_usage is per-call mutable)."""
    try:
        from src.atlas.models.responses import ResponsesClient
    except ImportError:
        from atlas.models.responses import ResponsesClient
    return ResponsesClient(api_key=api_key, timeout_s=timeout_s)


async def _judge_one(entry: dict, run_id: str, api_key: str,
                     deadline_s: float, sem: asyncio.Semaphore,
                     ) -> BaselineResult:
    """One claim through the reused baseline judge (never raises)."""
    async with sem:
        started = time.time()
        print(f"[f2] {run_id} {entry['id']} judging "
              f"({str(entry['text'])[:70]}...)", flush=True)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    judge_claim, _fresh_baseline_client(api_key, deadline_s),
                    entry["id"], entry["text"], EVAL2_CONTEXT, run_id),
                timeout=deadline_s)
        except asyncio.TimeoutError:
            result = BaselineResult(
                claim_id=entry["id"], verdict="unverifiable",
                latency_s=time.time() - started, run_id=run_id,
                parse_ok=False, error=f"deadline {deadline_s}s exceeded")
        print(f"[f2] {run_id} {entry['id']} -> {result.verdict} in "
              f"{time.time() - started:.1f}s", flush=True)
        return result


async def run_baseline_arm(entries: list[dict], run_id: str, api_key: str,
                           deadline_s: float, max_concurrency: int,
                           ) -> list[BaselineResult]:
    """Full baseline run over entries (M8 pattern, EVAL2_CONTEXT)."""
    sem = asyncio.Semaphore(max(1, max_concurrency))
    return list(await asyncio.gather(
        *[_judge_one(e, run_id, api_key, deadline_s, sem)
          for e in entries]))


async def run_atlas_arm(entries: list[dict], run_id: str, api_key: str,
                        deadline_s: float, max_concurrency: int,
                        snapshot_dir: str, log_path: str, db_path: str,
                        ) -> dict[str, Any]:
    """Full Atlas run over entries (M2c pattern via run_verify pieces).

    Returns the run payload: verdict rows, per-claim packages + claim
    inputs (bundle-export provenance), costs, stats.
    """
    from src.atlas.models.meta import MetaClient

    claims = {entry["id"]: run_verify.claim_input_for(entry)
              for entry in entries}
    llm = MetaClient(api_key=api_key)
    if not llm.available:
        raise SystemExit("atlas arm needs MODEL_API_KEY.")
    bus = EventBus(run_log=log_path)
    tracker = ClaimTracker(bus)
    store = EvidenceStore(db_path, snapshot_dir)
    stats: dict[str, Any] = {}
    bus.subscribe("claim.evidence",
                  run_verify.make_evidence_handler(
                      bus, tracker, store, claims, stats))
    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def _one(entry: dict) -> Any:
        config = run_verify.SwarmConfig(
            llm=llm.fork(), snapshot_dir=snapshot_dir,
            max_concurrency=max_concurrency,
            claim_deadline_s=deadline_s)
        async with sem:
            started = time.time()
            print(f"[f2] {run_id} {entry['id']} investigating "
                  f"({str(entry['text'])[:70]}...)", flush=True)
            try:
                return await run_verify.run_claim(
                    bus, tracker, entry["id"], entry["text"], config)
            finally:
                print(f"[f2] {run_id} {entry['id']} done in "
                      f"{time.time() - started:.1f}s", flush=True)

    try:
        results = await asyncio.gather(*[_one(e) for e in entries])
    finally:
        await bus.close()
        store.close()
    verdicts = {e["claim_id"]: e["verdict"]
                for e in bus.history("claim.verdict")}
    verdict_ts = {e["claim_id"]: e["bus_ts"]
                  for e in bus.history("claim.verdict")}
    invest_ts = {e["claim_id"]: e["bus_ts"]
                 for e in bus.history("claim.state")
                 if e.get("state") == "investigando"}
    rows = []
    for entry, res in zip(entries, results):
        cid = entry["id"]
        latency = None
        if cid in verdict_ts and cid in invest_ts:
            latency = max(0.0, verdict_ts[cid] - invest_ts[cid])
        ci = claims[cid]
        rows.append({
            "claim_id": cid, "verdict": verdicts.get(cid, "unverifiable"),
            "cost_usd": res.cost_usd, "latency_s": latency,
            "usage": res.usage, "degraded": res.degraded, "note": res.note,
            "n_packages": len(res.packages), "packages": res.packages,
            "claim_input": {"value": ci.value, "unit": ci.unit,
                            "raw": ci.raw, "claim_year": ci.claim_year,
                            "tolerance": ci.tolerance},
        })
    return {
        "run_id": run_id,
        "claims": rows,
        "total_cost_usd": round(sum(r.cost_usd for r in results), 4),
        "invalid_packages": stats.get("invalid_packages", 0),
        "source_type_mismatch": stats.get("source_type_mismatch", 0),
        "snapshots_unresolved": stats.get("snapshots_unresolved", 0),
        "degraded_claims": sorted(r.claim_id for r in results if r.degraded),
        "run_log": log_path,
        "db_path": db_path,
    }


def _panel_for(name: str, entries: list[dict],
               verdicts: dict[str, str],
               costs: dict[str, float],
               latencies: dict[str, float | None]) -> dict[str, Any]:
    """arm_metrics panel + recall alias (recall = decided/n)."""
    expected = {e["id"]: e["expected"] for e in entries
                if e.get("expected") in ("true", "false")}
    panel = arm_metrics(name, expected, verdicts, costs, latencies)
    panel["recall"] = round(panel["decided"] / panel["n"], 4) if panel["n"] else 0.0
    return panel


def _per_category(entries: list[dict], verdicts: dict[str, str],
                  costs: dict[str, float],
                  latencies: dict[str, float | None],
                  ) -> dict[str, dict[str, Any]]:
    """One arm_metrics panel per eval2 category (pure shaping)."""
    cats: dict[str, list[dict]] = {}
    for e in entries:
        cats.setdefault(str(e.get("category", "?")), []).append(e)
    return {cat: _panel_for(cat, rows, verdicts, costs, latencies)
            for cat, rows in sorted(cats.items())}


def render_text_table(report: dict[str, Any]) -> str:
    """Fixed-width arm comparison table (console + F2_BENCH.md)."""
    panels = report.get("panels", {})
    names = list(panels)
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

    rows.append(("n", [str(panels[a]["n"]) for a in names]))
    rows.append(("decided (recall)",
                 [f"{panels[a]['decided']} ({panels[a]['recall']:.3f})"
                  for a in names]))
    rows.append(("accuracy (Wilson95)",
                 [_fmt_acc(panels[a]) for a in names]))
    rows.append(("falsos contra on TRUE",
                 [str(len(panels[a]["falsos_contradicted"])) for a in names]))
    rows.append(("falsos supported on FALSE",
                 [str(len(panels[a]["falsos_supported"])) for a in names]))
    rows.append(("cost/claim USD",
                 [f"${panels[a]['cost_per_claim_usd']:.4f}" for a in names]))
    rows.append(("total USD",
                 [f"${panels[a]['total_cost_usd']:.4f}" for a in names]))
    rows.append(("latency/claim", [_fmt_lat(panels[a]) for a in names]))
    for arm, cons in (report.get("consistency") or {}).items():
        if cons:
            rows.append((
                f"{arm} r1-vs-r2 agree",
                [f"{cons['agree']}/{cons['n']}="
                 f"{cons['agreement_rate']:.3f}" if n.startswith(arm) else ""
                 for n in names]))
    width = max([len(r[0]) for r in rows] + [8])
    head = "metric".ljust(width) + " | " + " | ".join(names)
    bar = "-" * len(head)
    lines = [head, bar]
    for metric, vals in rows:
        lines.append(metric.ljust(width) + " | " + " | ".join(vals))
    lines.append(bar)
    for metric, panel in report.get("leaders", {}).items():
        lines.append(f"leader {metric}: {panel}")
    return "\n".join(lines)


async def amain(args: argparse.Namespace) -> dict:
    """Probe, run both arms, save runs + honest report."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    api_key = args.api_key or os.environ.get("MODEL_API_KEY", "")
    if not api_key:
        raise SystemExit("MODEL_API_KEY not configured; source .env first.")
    entries = run_verify.load_entries(args.claims, args.lang, args.limit)
    if not entries:
        raise SystemExit(f"no checkable claims for lang={args.lang!r} "
                         f"in {args.claims}")
    want_baseline = args.arms in ("both", "baseline")
    want_atlas = args.arms in ("both", "atlas")
    out_prefix = args.out_prefix or default_out_prefix()
    os.makedirs(os.path.dirname(out_prefix) or ".", exist_ok=True)

    # --- serial cost probe (discarded; runs stay independent) -----------
    probe_n = max(0, min(args.probe_claims, len(entries)))
    probe_per_claim: dict[str, float] = {}
    if probe_n and want_baseline:
        cost = 0.0
        for entry in entries[:probe_n]:
            res = await asyncio.to_thread(
                judge_claim,
                _fresh_baseline_client(api_key, args.baseline_deadline),
                entry["id"], entry["text"], EVAL2_CONTEXT, "probe")
            cost += res.cost_usd
        probe_per_claim["baseline"] = cost / probe_n
    if probe_n and want_atlas:
        from src.atlas.models.meta import MetaClient

        llm = MetaClient(api_key=api_key)
        cost = 0.0
        for entry in entries[:probe_n]:
            bus = EventBus()
            tracker = ClaimTracker(bus)
            config = run_verify.SwarmConfig(
                llm=llm.fork(), snapshot_dir=args.snapshot_dir,
                claim_deadline_s=args.atlas_deadline)
            res = await run_verify.run_claim(
                bus, tracker, entry["id"], entry["text"], config)
            cost += res.cost_usd
            await bus.close()
        probe_per_claim["atlas"] = cost / probe_n
    print(f"[f2] probe $/claim: {probe_per_claim}", flush=True)
    n_runs, guard_note = decide_total_runs(
        probe_per_claim, len(entries), max(1, args.runs), args.budget,
        args.max_cost_per_claim)
    if guard_note:
        print(f"[f2] {guard_note}", flush=True)

    # --- full runs ------------------------------------------------------
    wall_start = time.time()
    run_files: list[str] = []
    panels: dict[str, dict[str, Any]] = {}
    verdicts_by_arm: dict[str, list[dict[str, str]]] = {}
    costs_by_arm: dict[str, list[dict[str, float]]] = {}
    lat_by_arm: dict[str, list[dict[str, float | None]]] = {}
    for arm in (["baseline"] if want_baseline else []) + (
            ["atlas"] if want_atlas else []):
        verdicts_by_arm[arm] = []
        costs_by_arm[arm] = []
        lat_by_arm[arm] = []
        for run_idx in range(1, n_runs + 1):
            run_id = f"{arm}-run{run_idx}"
            if arm == "baseline":
                results = await run_baseline_arm(
                    entries, run_id, api_key, args.baseline_deadline,
                    args.max_concurrency)
                payload: dict[str, Any] = {
                    "run_id": run_id,
                    "arm": arm,
                    "claims_path": args.claims,
                    "lang": args.lang,
                    "context": "EVAL2_CONTEXT(standalone)",
                    "probe_cost_per_claim_usd": round(
                        probe_per_claim.get(arm, 0.0), 6),
                    "guard_note": guard_note,
                    "total_cost_usd": round(
                        sum(r.cost_usd for r in results), 4),
                    "claims": [r.to_dict() for r in results],
                }
            else:
                log_path = f"{out_prefix}-{run_id}.jsonl"
                db_path = log_path + ".db"
                payload = await run_atlas_arm(
                    entries, run_id, api_key, args.atlas_deadline,
                    args.max_concurrency, args.snapshot_dir,
                    log_path, db_path)
                payload["arm"] = arm
                payload["claims_path"] = args.claims
                payload["lang"] = args.lang
                payload["probe_cost_per_claim_usd"] = round(
                    probe_per_claim.get(arm, 0.0), 6)
                payload["guard_note"] = guard_note
            run_path = f"{out_prefix}-{run_id}.json"
            with open(run_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            run_files.append(run_path)
            print(f"[f2] {run_id}: saved {run_path} "
                  f"(${payload['total_cost_usd']})", flush=True)
            verdicts = {c.get("claim_id"): c.get("verdict", "unverifiable")
                        for c in payload["claims"]}
            costs = {c.get("claim_id"): float(c.get("cost_usd", 0.0) or 0.0)
                     for c in payload["claims"]}
            lat = {c.get("claim_id"): c.get("latency_s")
                   for c in payload["claims"]}
            verdicts_by_arm[arm].append(verdicts)
            costs_by_arm[arm].append(costs)
            lat_by_arm[arm].append(lat)
            panels[run_id] = _panel_for(run_id, entries, verdicts,
                                        costs, lat)
            panels[run_id]["by_category"] = _per_category(
                entries, verdicts, costs, lat)
    wall_s = round(time.time() - wall_start, 1)

    # --- pooled panels (prereg decision rule) + consistency + leaders ---
    expected = {e["id"]: e["expected"] for e in entries
                if e.get("expected") in ("true", "false")}
    pooled: dict[str, dict[str, Any]] = {}
    for arm, runs in verdicts_by_arm.items():
        if len(runs) >= 2:
            pexp, pver = pool_runs(expected, runs)
        else:
            pexp, pver = expected, (runs[0] if runs else {})
        n = len(pexp)
        dec = sum(1 for v in pver.values() if v != "unverifiable")
        ok = sum(1 for k, v in pver.items()
                 if (pexp[k] == "true" and v == "supported")
                 or (pexp[k] == "false" and v == "contradicted"))
        lo, hi = wilson_interval(ok, dec)
        pooled[arm] = {
            "arm": f"{arm}-pooled", "n": n, "decided": dec,
            "correct": ok,
            "accuracy_decided": (ok / dec) if dec else None,
            "wilson_lo": round(lo, 4), "wilson_hi": round(hi, 4),
            "recall": round(dec / n, 4) if n else 0.0,
        }
    consistency_panel = {
        arm: (consistency(runs[0], runs[1]) if len(runs) >= 2 else None)
        for arm, runs in verdicts_by_arm.items()
    }
    report = {
        "claims_path": args.claims,
        "lang": args.lang,
        "n_claims": len(expected),
        "n_runs_per_arm": n_runs,
        "wall_s": wall_s,
        "guard_note": guard_note,
        "probe_per_claim_usd": probe_per_claim,
        "run_files": run_files,
        "panels": panels,
        "pooled": pooled,
        "consistency": consistency_panel,
        "leaders": leaders(panels),
    }
    report_path = f"{out_prefix}-report.json"
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    report["report_path"] = report_path
    print(f"[f2] wall={wall_s}s guard={guard_note!r}", flush=True)
    print(render_text_table(report), flush=True)
    print(f"[f2] report: {report_path}", flush=True)
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint (argv injectable for tests)."""
    args = build_parser().parse_args(argv)
    asyncio.run(amain(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
