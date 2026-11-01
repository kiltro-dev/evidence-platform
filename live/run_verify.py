"""Atlas Live M2c — E2E verification runner (shared checkout, branch live).

Pipeline per claim (all real pieces, asyncio)::

    truth.json entry --> ClaimInput (deterministic quantity parse)
    swarm.coordinator.run_claim (semaphore 4, 90 s deadline per claim)
      --> claim.evidence (EvidencePackage-compatible dicts)
      --> [M2c subscription] EvidencePackage.from_dict
      --> EvidenceStore (snapshot bytes + package JSON)
      --> verifier.verify_and_emit --> claim.verdict
      --> ClaimTracker veredicto

Every bus event lands in the per-run JSONL log (``data/runs/verify-*.jsonl``,
replayable); run metadata (matrix vs truth, cost, latency) goes to a sibling
``.summary.json`` — never into the replayable log.

Cost model (measured): per-claim ``ClaimResult.cost_usd`` from the swarm
(token usage through ``src.atlas.agent.loop.estimate_cost``).

Usage (repo root on sys.path; MODEL_API_KEY from env — source .env)::

    .venv/bin/python live/run_verify.py --claims live/material/truth.json --lang en
    .venv/bin/python live/run_verify.py --lang en --limit 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live.evidence.package import (  # noqa: E402
    EvidencePackage,
    source_type_for_url,
)
from live.library.store import EvidenceStore  # noqa: E402
from live.orchestrator.bus import EventBus  # noqa: E402
from live.orchestrator.states import ClaimTracker  # noqa: E402
from live.swarm.coordinator import (  # noqa: E402
    CLAIM_DEADLINE_S,
    MAX_CONCURRENCY,
    SwarmConfig,
    run_claim,
)
from live.verifier.check import (  # noqa: E402
    ClaimInput,
    SnapshotLoader,
    Verdict,
    verify_and_emit,
)

try:  # repo-root-on-path convention (live runners)
    from src.atlas.core.compare import is_year_quantity
    from src.atlas.core.units import parse_quantities
except ImportError:  # src/-on-path convention (tests)
    from atlas.core.compare import is_year_quantity
    from atlas.core.units import parse_quantities

log = logging.getLogger(__name__)

LIVE_DIR = Path(__file__).resolve().parent
REPO_ROOT = LIVE_DIR.parent
DEFAULT_TRUTH = LIVE_DIR / "material" / "truth.json"
DEFAULT_SNAPSHOT_DIR = REPO_ROOT / "data" / "snap"

_YEAR_RE = re.compile(r"\b(19|20|21)\d{2}\b")
_VERIFIABLE = ("true", "false")


# ---------------------------------------------------------------------------
# Pure helpers (also used by live/tests/test_m2c_e2e.py, 0 live calls)
# ---------------------------------------------------------------------------

def load_entries(path: str | Path, lang: str,
                 limit: int | None = None) -> list[dict]:
    """truth.json rows for ``lang`` with checkable expectations (true/false).

    Order is file order (scripted talk order); ``limit`` caps the head.
    """
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    out = [r for r in rows
           if r.get("lang") == lang and r.get("expected") in _VERIFIABLE]
    if limit is not None:
        out = out[:max(0, limit)]
    return out


def claim_input_for(entry: dict) -> ClaimInput:
    """Deterministic ClaimInput from a truth entry (code only, no model).

    Value/unit/raw come from the FIRST non-year quantity parsed from the
    claim text (same parser the verifier re-extracts with); the year is
    the first 1960-2100 year in the text. Claims with no numeric quantity
    (opinions phrased as facts, e.g. "larger than America's") get
    unit=None and value 0.0: the verifier then abstains with
    ``claim_unit_unknown`` (unverifiable) instead of guessing.
    """
    text = str(entry.get("text", "") or "")
    claim_id = str(entry.get("id", "") or "")
    cands = [q for q in parse_quantities(text, label="claim")
             if not is_year_quantity(q)]
    years = [int(m.group(0)) for m in _YEAR_RE.finditer(text)]
    year = next((y for y in years if 1960 <= y <= 2100), None)
    if cands:
        first = cands[0]
        return ClaimInput(claim_id=claim_id, text=text, value=first.value,
                          unit=first.normalized_unit, raw=first.raw_text,
                          claim_year=year)
    return ClaimInput(claim_id=claim_id, text=text, value=0.0, unit=None,
                      raw="", claim_year=year)


def percentile(samples: list[float], pct: float) -> float | None:
    """Nearest-rank percentile (None when empty)."""
    if not samples:
        return None
    ordered = sorted(samples)
    rank = max(0, min(len(ordered) - 1,
                      int(-(-pct * len(ordered) // 100)) - 1))
    return ordered[rank]


def default_log_path() -> str:
    """data/runs/verify-<UTC-timestamp>.jsonl."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return str(REPO_ROOT / "data" / "runs" / f"verify-{stamp}.jsonl")


# ---------------------------------------------------------------------------
# M2c subscription: claim.evidence -> store -> verifier -> claim.verdict
# ---------------------------------------------------------------------------

SnapshotResolver = Callable[[EvidencePackage], bytes | None]


def default_snapshot_resolver(repo_root: str | Path = REPO_ROOT,
                              store: EvidenceStore | None = None,
                              ) -> SnapshotResolver:
    """Raw snapshot bytes for a package: repo file first, store fallback."""

    def _resolve(pkg: EvidencePackage) -> bytes | None:
        candidate = Path(repo_root) / pkg.snapshot_path
        try:
            return candidate.read_bytes()
        except OSError:
            pass
        if store is not None:
            try:
                return store.get_snapshot_bytes(pkg.sha256)
            except Exception:
                return None
        return None

    return _resolve


def make_evidence_handler(
    bus: EventBus,
    tracker: ClaimTracker,
    store: EvidenceStore,
    claims: dict[str, ClaimInput],
    stats: dict[str, Any],
    snapshot_resolver: SnapshotResolver | None = None,
) -> Any:
    """Build the claim.evidence subscriber closing the M2 loop.

    Per event: validate each dict into EvidencePackage (rejects are
    counted, never crash the run) -> audit source_type against the
    canonical URL criterion -> persist snapshot bytes + package in the
    store -> verify_and_emit (claim.verdict) -> tracker veredicto.
    Exactly one claim.verdict per claim.evidence, even with zero
    (or zero valid) packages — then unverifiable/no_usable_package.
    """
    resolve = snapshot_resolver or default_snapshot_resolver(
        REPO_ROOT, store)

    async def on_evidence(event: dict) -> None:
        claim_id = event.get("claim_id")
        claim = claims.get(str(claim_id))
        if claim is None:
            return
        packages: list[EvidencePackage] = []
        for raw_pkg in event.get("packages", []) or []:
            if not isinstance(raw_pkg, dict):
                stats["invalid_packages"] = (
                    stats.get("invalid_packages", 0) + 1)
                continue
            try:
                pkg = EvidencePackage.from_dict(raw_pkg)
            except (ValueError, KeyError, TypeError) as exc:
                stats["invalid_packages"] = (
                    stats.get("invalid_packages", 0) + 1)
                log.warning("dropping invalid package for %s: %r",
                            claim_id, exc)
                continue
            if pkg.source_type != source_type_for_url(pkg.url):
                stats["source_type_mismatch"] = (
                    stats.get("source_type_mismatch", 0) + 1)
                log.warning("source_type %r disagrees with canonical "
                            "criterion for %s", pkg.source_type, pkg.url)
            raw = resolve(pkg)
            if raw is not None:
                try:
                    store.upsert_snapshot_bytes(
                        pkg.url, raw, pkg.source_type, pkg.captured_at)
                except (ValueError, OSError) as exc:
                    log.warning("snapshot persist failed for %s: %r",
                                pkg.package_id, exc)
            else:
                stats["snapshots_unresolved"] = (
                    stats.get("snapshots_unresolved", 0) + 1)
            try:
                store.store_package(pkg)
            except (ValueError, OSError) as exc:
                log.warning("package persist failed for %s: %r",
                            pkg.package_id, exc)
                continue
            packages.append(pkg)

        def _loader(p: EvidencePackage) -> bytes | None:
            return resolve(p)

        loader: SnapshotLoader = _loader
        verdict = await verify_and_emit(claim, packages, loader, bus)
        stats["verdicts"] = stats.get("verdicts", {})
        stats["verdicts"][claim_id] = verdict.verdict
        try:
            if tracker.state(str(claim_id)) == "investigando":
                await tracker.set_state(str(claim_id), "veredicto",
                                        actor="m2c-run_verify")
        except ValueError as exc:
            log.warning("veredicto transition failed for %s: %r",
                        claim_id, exc)

    return on_evidence


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(entries: list[dict], verdicts: dict[str, str],
                 verdict_ts: dict[str, float],
                 invest_ts: dict[str, float]) -> dict[str, Any]:
    """Matrix vs truth + false-contradicted + latency staring at the bus."""
    labels = ("supported", "contradicted", "disputed", "unverifiable")
    matrix = {exp: {label: 0 for label in labels} for exp in _VERIFIABLE}
    false_contradicted: list[str] = []  # expected TRUE, got contradicted
    false_supported: list[str] = []  # expected FALSE, got supported
    per_claim: list[dict[str, Any]] = []
    latencies_s: list[float] = []
    for entry in entries:
        cid = entry["id"]
        expected = entry["expected"]
        verdict = verdicts.get(cid, "unverifiable")
        if verdict in labels:
            matrix[expected][verdict] += 1
        if expected == "true" and verdict == "contradicted":
            false_contradicted.append(cid)
        if expected == "false" and verdict == "supported":
            false_supported.append(cid)
        latency = None
        if cid in verdict_ts and cid in invest_ts:
            latency = max(0.0, verdict_ts[cid] - invest_ts[cid])
            latencies_s.append(latency)
        per_claim.append({"claim_id": cid, "expected": expected,
                          "verdict": verdict, "latency_s": latency})
    return {
        "n_claims": len(entries),
        "matrix": matrix,
        "false_contradicted": false_contradicted,
        "false_supported": false_supported,
        "latency_s": {
            "count": len(latencies_s),
            "p50": percentile(latencies_s, 50),
            "p95": percentile(latencies_s, 95),
        },
        "per_claim": per_claim,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """CLI flags for the M2c E2E verify run."""
    parser = argparse.ArgumentParser(
        description="Atlas Live M2c: swarm -> store -> verifier over "
        "truth.json, with matrix-vs-truth + cost + latency report.")
    parser.add_argument("--claims", default=str(DEFAULT_TRUTH),
                        help="truth.json path "
                        f"(default: {DEFAULT_TRUTH})")
    parser.add_argument("--lang", default="en",
                        help="language slice (default: en)")
    parser.add_argument("--limit", type=int, default=None,
                        help="verify only the first N checkable claims")
    parser.add_argument("--log", default=None,
                        help="bus event log (default: "
                        "data/runs/verify-<ts>.jsonl)")
    parser.add_argument("--db", default=None,
                        help="library sqlite path (default: <log>.db)")
    parser.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR),
                        help="snapshot dir (default: data/snap)")
    parser.add_argument("--max-concurrency", type=int,
                        default=MAX_CONCURRENCY,
                        help=f"parallel claims (default: {MAX_CONCURRENCY})")
    parser.add_argument("--deadline", type=float, default=CLAIM_DEADLINE_S,
                        help="seconds per claim "
                        f"(default: {CLAIM_DEADLINE_S})")
    parser.add_argument("--api-key", default="",
                        help="Meta API key (default: MODEL_API_KEY env)")
    return parser


async def amain(args: argparse.Namespace) -> dict:
    """Run the E2E pipeline; return the JSON-serializable summary."""
    try:  # honor a local .env without requiring it (mirror MetaClient)
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    api_key = args.api_key or os.environ.get("MODEL_API_KEY", "")
    if not api_key:
        # Presence only — never print the key or its length here.
        raise SystemExit("MODEL_API_KEY not configured; source .env first.")
    entries = load_entries(args.claims, args.lang, args.limit)
    if not entries:
        raise SystemExit(f"no checkable claims for lang={args.lang!r} "
                         f"in {args.claims}")
    claims = {entry["id"]: claim_input_for(entry) for entry in entries}

    log_path = args.log or default_log_path()
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    db_path = args.db or (log_path + ".db")
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    from src.atlas.models.meta import MetaClient

    llm = MetaClient(api_key=api_key)
    if not llm.available:
        raise SystemExit("swarm needs MODEL_API_KEY.")

    bus = EventBus(run_log=log_path)
    tracker = ClaimTracker(bus)
    store = EvidenceStore(db_path, args.snapshot_dir)
    stats: dict[str, Any] = {}
    bus.subscribe("claim.evidence",
                  make_evidence_handler(bus, tracker, store, claims, stats))

    sem = asyncio.Semaphore(max(1, args.max_concurrency))

    async def _one(entry: dict) -> Any:
        # One forked client per claim: last_usage state is per-request
        # mutable (same pattern as the M2b live test).
        config = SwarmConfig(llm=llm.fork(),
                             snapshot_dir=args.snapshot_dir,
                             max_concurrency=args.max_concurrency,
                             claim_deadline_s=args.deadline)
        async with sem:
            started = time.time()
            print(f"[m2c] {entry['id']} investigating "
                  f"({str(entry['text'])[:70]}...)", flush=True)
            try:
                return await run_claim(bus, tracker, entry["id"],
                                       entry["text"], config)
            finally:
                print(f"[m2c] {entry['id']} done in "
                      f"{time.time() - started:.1f}s", flush=True)

    wall_start = time.time()
    try:
        results = await asyncio.gather(
            *[_one(entry) for entry in entries])
    finally:
        await bus.close()
        store.close()
    wall_s = time.time() - wall_start

    total_cost = sum(r.cost_usd for r in results)
    total_usage = {
        "prompt_tokens": sum(r.usage.get("prompt_tokens", 0)
                             for r in results),
        "completion_tokens": sum(r.usage.get("completion_tokens", 0)
                                 for r in results),
        "model_calls": sum(r.usage.get("model_calls", 0) for r in results),
    }
    verdicts = {e["claim_id"]: e["verdict"]
                for e in bus.history("claim.verdict")}
    verdict_ts = {e["claim_id"]: e["bus_ts"]
                  for e in bus.history("claim.verdict")}
    invest_ts = {e["claim_id"]: e["bus_ts"]
                 for e in bus.history("claim.state")
                 if e.get("state") == "investigando"}
    report = build_report(entries, verdicts, verdict_ts, invest_ts)
    summary = {
        "claims_path": args.claims,
        "lang": args.lang,
        "limit": args.limit,
        "wall_s": round(wall_s, 1),
        "n_claims": report["n_claims"],
        "matrix": report["matrix"],
        "false_contradicted": report["false_contradicted"],
        "false_supported": report["false_supported"],
        "per_claim": report["per_claim"],
        "latency_s": report["latency_s"],
        "total_cost_usd": round(total_cost, 4),
        "total_usage": total_usage,
        "invalid_packages": stats.get("invalid_packages", 0),
        "source_type_mismatch": stats.get("source_type_mismatch", 0),
        "snapshots_unresolved": stats.get("snapshots_unresolved", 0),
        "degraded_claims": sorted(r.claim_id for r in results if r.degraded),
        "packages_per_claim": {r.claim_id: len(r.packages)
                               for r in results},
        "run_log": log_path,
        "db_path": db_path,
    }
    summary_path = log_path + ".summary.json"
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    summary["summary_path"] = summary_path
    print(f"[m2c] claims={report['n_claims']} "
          f"matrix={report['matrix']} "
          f"false_contradicted={report['false_contradicted']} "
          f"usd={summary['total_cost_usd']} "
          f"lat_p50/p95={report['latency_s']['p50']}/"
          f"{report['latency_s']['p95']}s", flush=True)
    print(f"[m2c] summary: {summary_path}", flush=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint (argv injectable for tests)."""
    args = build_parser().parse_args(argv)
    asyncio.run(amain(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
