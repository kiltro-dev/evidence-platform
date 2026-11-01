"""M6 canary auditor: 12 INTERNAL calibration claims (branch live).

These canaries are NEVER from the video and are NEVER shown as video
claims. They exist only to measure pipeline accuracy on World Bank
population/GDP facts whose ground truth is pinned below and remains
verifiable today via each ``ref_url`` (all api.worldbank.org).

Ground truth pinned 2026-11-01 from the cited URLs:
  MEX SP.POP.TOTL 2024 = 130,861,007      IDN SP.POP.TOTL 2024 = 283,487,931
  NGA SP.POP.TOTL 2024 = 232,679,478      GBR NY.GDP.MKTP.CD 2024 = $3.6955T
  ITA NY.GDP.MKTP.CD 2024 = $2.3834T      CAN SP.POP.TOTL 2024 = 41,262,329

TRUE canaries sit <=0.2% off the pinned value (inside the 2% verifier
tolerance); FALSE canaries sit >=27% off (a correct pipeline must
contradict them). Country/indicator pairs are disjoint from every
``live/material/truth.json`` EN checkable claim (video uses WLD/CHN/BRA/
USA/DEU/FRA/JPN/IND; canaries use MEX/IDN/NGA/GBR/ITA/CAN).

Runner: each canary goes through ``run_verify.run_claim`` (imported and
reused, semaphore 4) -> EvidenceStore -> verifier -> claim.verdict,
exactly like the M2c E2E loop. The panel is published on the bus as
``auditor.report`` and written to ``data/runs/audit-*.json``.

Usage (repo root on sys.path; MODEL_API_KEY from env — source .env)::

    .venv/bin/python live/auditor/canaries.py
    .venv/bin/python live/auditor/canaries.py --limit 2 --log /tmp/audit.jsonl
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

# Reuse the M2c loop: run_claim is re-exported through live.run_verify
# (it imports it from live.swarm.coordinator), so this IS run_verify's
# pipeline entry — same grounding, same deadline, same evidence shape.
from live.run_verify import (  # noqa: E402
    CLAIM_DEADLINE_S,
    MAX_CONCURRENCY,
    SwarmConfig,
    claim_input_for,
    make_evidence_handler,
    run_claim,
)
from live.auditor.wilson import summarize  # noqa: E402
from live.library.store import EvidenceStore  # noqa: E402
from live.orchestrator.bus import EventBus  # noqa: E402
from live.orchestrator.states import ClaimTracker  # noqa: E402

LIVE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = LIVE_DIR.parent

_WB = "https://api.worldbank.org/v2/country/{cc}/indicator/{ind}?format=json&date=2024"

# id/text/expected/ref_url (+ human-readable pinned ref). 6 TRUE + 6 FALSE.
CANARIES: list[dict[str, str]] = [
    # -- TRUE (<=0.2% off pinned WB 2024) ---------------------------------
    {"id": "M6-T1", "expected": "true",
     "text": "Mexico had 131 million people in 2024, according to the World Bank.",
     "ref_url": _WB.format(cc="MEX", ind="SP.POP.TOTL"),
     "ref": "WB SP.POP.TOTL MEX 2024 = 130,861,007"},
    {"id": "M6-T2", "expected": "true",
     "text": "Indonesia's population reached 283 million people in 2024, the World Bank reports.",
     "ref_url": _WB.format(cc="IDN", ind="SP.POP.TOTL"),
     "ref": "WB SP.POP.TOTL IDN 2024 = 283,487,931"},
    {"id": "M6-T3", "expected": "true",
     "text": "Nigeria was home to 233 million people in 2024, per World Bank data.",
     "ref_url": _WB.format(cc="NGA", ind="SP.POP.TOTL"),
     "ref": "WB SP.POP.TOTL NGA 2024 = 232,679,478"},
    {"id": "M6-T4", "expected": "true",
     "text": "The United Kingdom produced 3.70 trillion dollars of GDP in 2024.",
     "ref_url": _WB.format(cc="GBR", ind="NY.GDP.MKTP.CD"),
     "ref": "WB NY.GDP.MKTP.CD GBR 2024 = 3,695,539,513,534.15"},
    {"id": "M6-T5", "expected": "true",
     "text": "Italy's GDP stood at 2.38 trillion dollars in 2024.",
     "ref_url": _WB.format(cc="ITA", ind="NY.GDP.MKTP.CD"),
     "ref": "WB NY.GDP.MKTP.CD ITA 2024 = 2,383,435,562,458.12"},
    {"id": "M6-T6", "expected": "true",
     "text": "Canada had 41.3 million people in 2024, according to the World Bank.",
     "ref_url": _WB.format(cc="CAN", ind="SP.POP.TOTL"),
     "ref": "WB SP.POP.TOTL CAN 2024 = 41,262,329"},
    # -- FALSE (>=27% off pinned WB 2024: must contradict) ------------------
    {"id": "M6-F1", "expected": "false",
     "text": "Mexico had only 95 million people in 2024, according to the World Bank.",
     "ref_url": _WB.format(cc="MEX", ind="SP.POP.TOTL"),
     "ref": "WB says 130,861,007; 95M is 27% low"},
    {"id": "M6-F2", "expected": "false",
     "text": "Indonesia's population was just 190 million people in 2024, the World Bank reports.",
     "ref_url": _WB.format(cc="IDN", ind="SP.POP.TOTL"),
     "ref": "WB says 283,487,931; 190M is 33% low"},
    {"id": "M6-F3", "expected": "false",
     "text": "Nigeria surged to 320 million people in 2024, per World Bank data.",
     "ref_url": _WB.format(cc="NGA", ind="SP.POP.TOTL"),
     "ref": "WB says 232,679,478; 320M is 37% high"},
    {"id": "M6-F4", "expected": "false",
     "text": "The United Kingdom produced 5.2 trillion dollars of GDP in 2024.",
     "ref_url": _WB.format(cc="GBR", ind="NY.GDP.MKTP.CD"),
     "ref": "WB says $3.6955T; 5.2T is 41% high"},
    {"id": "M6-F5", "expected": "false",
     "text": "Italy's GDP collapsed to 1.4 trillion dollars in 2024.",
     "ref_url": _WB.format(cc="ITA", ind="NY.GDP.MKTP.CD"),
     "ref": "WB says $2.3834T; 1.4T is 41% low"},
    {"id": "M6-F6", "expected": "false",
     "text": "Canada had 58 million people in 2024, according to the World Bank.",
     "ref_url": _WB.format(cc="CAN", ind="SP.POP.TOTL"),
     "ref": "WB says 41,262,329; 58M is 41% high"},
]


def default_log_path() -> str:
    """data/runs/audit-<UTC-timestamp>.jsonl (bus log; panel -> .json)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return str(REPO_ROOT / "data" / "runs" / f"audit-{stamp}.jsonl")


def report_event(panel: dict[str, Any]) -> dict[str, Any]:
    """auditor.report bus event from a summarize() panel."""
    return {
        "type": "auditor.report",
        "n": panel["n"],
        "decided": panel["decided"],
        "correct": panel["correct"],
        "accuracy": panel["accuracy"],
        "wilson_lo": panel["wilson_lo"],
        "wilson_hi": panel["wilson_hi"],
        "abstention_rate": panel["abstention_rate"],
        "falsos_contradicted": list(panel["falsos_contradicted"]),
        "actor": "m6-auditor",
    }


def build_parser() -> argparse.ArgumentParser:
    """CLI flags for the M6 canary audit run."""
    parser = argparse.ArgumentParser(
        description="Atlas Live M6: 12 internal canaries through "
        "run_verify.run_claim, with accuracy + Wilson panel.")
    parser.add_argument("--limit", type=int, default=None,
                        help="audit only the first N canaries")
    parser.add_argument("--log", default=None,
                        help="bus event log (default: "
                        "data/runs/audit-<ts>.jsonl)")
    parser.add_argument("--db", default=None,
                        help="library sqlite path (default: <log>.db)")
    parser.add_argument("--snapshot-dir",
                        default=str(REPO_ROOT / "data" / "snap"),
                        help="snapshot dir (default: data/snap)")
    parser.add_argument("--max-concurrency", type=int,
                        default=MAX_CONCURRENCY,
                        help=f"parallel canaries (default: {MAX_CONCURRENCY})")
    parser.add_argument("--deadline", type=float, default=CLAIM_DEADLINE_S,
                        help="seconds per canary "
                        f"(default: {CLAIM_DEADLINE_S})")
    parser.add_argument("--api-key", default="",
                        help="Meta API key (default: MODEL_API_KEY env)")
    return parser


async def amain(args: argparse.Namespace) -> dict:
    """Run the audit; publish auditor.report; write audit JSON."""
    try:  # honor a local .env without requiring it (mirror MetaClient)
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    api_key = args.api_key or os.environ.get("MODEL_API_KEY", "")
    if not api_key:
        raise SystemExit("MODEL_API_KEY not configured; source .env first.")
    entries = list(CANARIES)
    if args.limit is not None:
        entries = entries[:max(0, args.limit)]
    if not entries:
        raise SystemExit("no canaries selected (empty --limit?)")
    claims = {entry["id"]: claim_input_for(entry) for entry in entries}

    log_path = args.log or default_log_path()
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    db_path = args.db or (log_path + ".db")

    from src.atlas.models.meta import MetaClient

    llm = MetaClient(api_key=api_key)
    if not llm.available:
        raise SystemExit("audit needs MODEL_API_KEY.")

    bus = EventBus(run_log=log_path)
    tracker = ClaimTracker(bus)
    store = EvidenceStore(db_path, args.snapshot_dir)
    stats: dict[str, Any] = {}
    bus.subscribe("claim.evidence",
                  make_evidence_handler(bus, tracker, store, claims, stats))

    sem = asyncio.Semaphore(max(1, args.max_concurrency))

    async def _one(entry: dict) -> Any:
        config = SwarmConfig(llm=llm.fork(),
                             snapshot_dir=args.snapshot_dir,
                             max_concurrency=args.max_concurrency,
                             claim_deadline_s=args.deadline)
        async with sem:
            started = time.time()
            print(f"[m6] {entry['id']} investigating "
                  f"({str(entry['text'])[:70]}...)", flush=True)
            try:
                return await run_claim(bus, tracker, entry["id"],
                                       entry["text"], config)
            finally:
                print(f"[m6] {entry['id']} done in "
                      f"{time.time() - started:.1f}s", flush=True)

    wall_start = time.time()
    try:
        results = await asyncio.gather(
            *[_one(entry) for entry in entries])
    finally:
        # Panel BEFORE closing the bus so auditor.report lands in the log.
        verdicts = {e["claim_id"]: e["verdict"]
                    for e in bus.history("claim.verdict")}
        expected = {e["id"]: e["expected"] for e in entries}
        panel = summarize(expected, verdicts)
        panel["total_cost_usd"] = round(
            sum(r.cost_usd for r in results), 4)
        panel["total_usage"] = {
            "prompt_tokens": sum(r.usage.get("prompt_tokens", 0)
                                 for r in results),
            "completion_tokens": sum(r.usage.get("completion_tokens", 0)
                                     for r in results),
            "model_calls": sum(r.usage.get("model_calls", 0)
                               for r in results),
        }
        panel["wall_s"] = round(time.time() - wall_start, 1)
        panel["run_log"] = log_path
        panel["db_path"] = db_path
        panel["invalid_packages"] = stats.get("invalid_packages", 0)
        panel["source_type_mismatch"] = stats.get(
            "source_type_mismatch", 0)
        panel["snapshots_unresolved"] = stats.get(
            "snapshots_unresolved", 0)
        panel["degraded_canaries"] = sorted(
            r.claim_id for r in results if r.degraded)
        panel["packages_per_canary"] = {r.claim_id: len(r.packages)
                                        for r in results}
        await bus.publish(report_event(panel))
        await bus.close()
        store.close()

    report_path = log_path.replace(".jsonl", ".json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(panel, fh, ensure_ascii=False, indent=2)
    panel["report_path"] = report_path
    print(f"[m6] n={panel['n']} decided={panel['decided']} "
          f"correct={panel['correct']} accuracy={panel['accuracy']} "
          f"wilson=[{panel['wilson_lo']},{panel['wilson_hi']}] "
          f"abstention={panel['abstention_rate']} "
          f"falsos_contradicted={panel['falsos_contradicted']} "
          f"usd={panel['total_cost_usd']}", flush=True)
    print(f"[m6] report: {report_path}", flush=True)
    return panel


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint (argv injectable for tests)."""
    args = build_parser().parse_args(argv)
    asyncio.run(amain(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
