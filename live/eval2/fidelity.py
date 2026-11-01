"""F2-1 citation fidelity: deterministic re-check of baseline quotes.

For every literal quote the baseline judge produced (``quote`` + browsed
``urls`` in a ``*-baseline-run*.json`` file), this module re-fetches the
cited URL responsibly (timeout, UA, size cap — no model, no search) and
applies a DETERMINISTIC check reusing the Atlas parser pieces
(import-only):

- ``quote_found``: the quote appears literally in the page
  (whitespace-normalized, ``live.verifier.check.normalize_ws``);
- ``number_backed``: a non-year number re-extracted from the quote
  matches the claim number within 2% with a compatible unit
  (``src.atlas.core.units`` + ``compare.is_year_quantity``).

A citation is FAITHFUL iff both hold. The report gives the faithful
fraction + Wilson 95% over checked citations, the unresolved list
(dead URL, no quote, no number), and declares snapshot usage: when a
URL is dead and ``--snapshot-dir`` holds a capture named
``sha256(url).html|.txt``, that snapshot is used and flagged
(``snapshot_used: true``) instead of silently dropping the citation.

Usage::

    .venv/bin/python live/eval2/fidelity.py --run data/runs/eval2-*-baseline-run1.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from live.auditor.wilson import wilson_interval  # noqa: E402
from live.evidence.package import source_meta_for_snapshot  # noqa: E402 (import-only)
from live.run_verify import claim_input_for, load_entries  # noqa: E402
from live.verifier.check import normalize_ws  # noqa: E402 (import-only)

try:  # repo-root-on-path convention (live runners)
    from src.atlas.core.compare import (
        DEFAULT_REL_TOLERANCE,
        is_year_quantity,
    )
    from src.atlas.core.units import parse_quantities, units_compatible
except ImportError:  # src/-on-path convention (tests)
    from atlas.core.compare import DEFAULT_REL_TOLERANCE, is_year_quantity
    from atlas.core.units import parse_quantities, units_compatible

LIVE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CLAIMS = LIVE_DIR / "eval2" / "dev.json"

FETCH_TIMEOUT_S = 15.0
FETCH_MAX_BYTES = 2_000_000
FETCH_UA = "AtlasLive-F2-fidelity/1.0 (+bench audit; single fetch)"

FetchFn = Callable[[str], "str | None"]


def fetch_page(url: str, timeout_s: float = FETCH_TIMEOUT_S,
               max_bytes: int = FETCH_MAX_BYTES,
               fetch_fn: FetchFn | None = None) -> tuple[str | None, str]:
    """Responsible single fetch. Returns (text|None, error|"").

    ``fetch_fn`` overrides the network (tests inject doubles raising or
    returning canned pages). Decode errors fall back to replace mode.
    """
    if fetch_fn is not None:
        try:
            return fetch_fn(url), ""
        except Exception as exc:  # noqa: BLE001 — unresolved, recorded
            return None, f"{type(exc).__name__}: {exc}"
    req = urllib.request.Request(url, headers={"User-Agent": FETCH_UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read(max_bytes + 1)
    except Exception as exc:  # noqa: BLE001 — unresolved, recorded
        return None, f"{type(exc).__name__}: {exc}"
    if len(raw) > max_bytes:
        return None, f"page exceeds {max_bytes} bytes (refusing)"
    try:
        return raw.decode("utf-8"), ""
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), ""


def snapshot_for(snapshot_dir: str | Path | None,
                 url: str) -> tuple[str | None, str | None]:
    """Declared snapshot fallback: sha256(url).html|.txt under dir."""
    if not snapshot_dir:
        return None, None
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    base = Path(snapshot_dir)
    for suffix in (".html", ".txt"):
        cand = base / f"{digest}{suffix}"
        if cand.is_file():
            try:
                return cand.read_text(encoding="utf-8"), str(cand)
            except (OSError, UnicodeDecodeError):
                continue
    return None, None


def assess_quote(page_text: str | None, quote: str | None,
                 claim_value: float, claim_unit: str | None,
                 tolerance: float = DEFAULT_REL_TOLERANCE,
                 url: str | None = None,
                 ) -> dict[str, Any]:
    """Deterministic citation check (pure, tested).

    ``faithful`` = quote found literally AND a non-year number in the
    quote backs the claim figure (rel diff <= tolerance, units
    compatible). No network, no model. Mirrors the verifier's M2d
    structured fallback: unitless World Bank API quotes get their unit
    from the snapshot-declared indicator (re-derived from the page via
    ``source_meta_for_snapshot``, never trusted from the quote).
    """
    norm_quote = normalize_ws(quote or "")
    if not norm_quote:
        return {"faithful": False, "reason": "no_quote",
                "quote_found": False, "number_backed": False}
    if not page_text:
        return {"faithful": False, "reason": "page_unresolved",
                "quote_found": False, "number_backed": False}
    found = norm_quote in normalize_ws(page_text)
    if not found:
        return {"faithful": False, "reason": "quote_not_found",
                "quote_found": False, "number_backed": False}
    cands = [q for q in parse_quantities(quote or "", label="quote")
             if not is_year_quantity(q)]
    if not cands:
        return {"faithful": False, "reason": "no_number_in_quote",
                "quote_found": True, "number_backed": False}
    if claim_unit is None:
        return {"faithful": False, "reason": "claim_unit_unknown",
                "quote_found": True, "number_backed": False}
    compat = [q for q in cands
              if units_compatible(claim_unit, q.normalized_unit)]
    scale = 1.0
    unit_from: str | None = None
    if not compat and url:
        # M2d structured fallback (same rule as live/verifier/check.py):
        # unitless JSON quotes from the WB API get unit+scale from the
        # page-declared indicator; values still come from the quote.
        meta = source_meta_for_snapshot(url, page_text or "")
        if meta is not None and units_compatible(claim_unit, meta.unit):
            compat = list(cands)
            scale = float(meta.scale)
            unit_from = "source_meta"
    if not compat:
        return {"faithful": False, "reason": "unit_mismatch",
                "quote_found": True, "number_backed": False}
    best = min(compat, key=lambda q: abs(claim_value - q.value * scale)
               / (abs(q.value * scale) or 1.0))
    evidence_value = best.value * scale
    denom = abs(evidence_value) if evidence_value != 0 else 1.0
    rel = abs(claim_value - evidence_value) / denom
    if rel > tolerance:
        return {"faithful": False, "reason": "numeric_mismatch",
                "quote_found": True, "number_backed": False,
                "rel_diff": round(rel, 4),
                "evidence_value": evidence_value,
                "evidence_unit": best.normalized_unit or unit_from}
    out = {"faithful": True, "reason": "faithful",
           "quote_found": True, "number_backed": True,
           "rel_diff": round(rel, 4), "evidence_value": evidence_value,
           "evidence_unit": best.normalized_unit or unit_from}
    if unit_from:
        out["unit_from"] = unit_from
    return out


def assess_run(run_path: str | Path, claims_path: str | Path,
               lang: str = "en",
               snapshot_dir: str | Path | None = None,
               fetch_fn: FetchFn | None = None,
               ) -> dict[str, Any]:
    """Fidelity report for one baseline run file (network only via fetch).

    Checks the FIRST browsed URL per cited claim (``urls[0]``), falling
    back to the model's stated URL (``judge_urls[0]``, flagged
    ``url_source: stated``) when the client returns no browsed
    citations — the observed shape in M8 (0/31 browsed) and F2 smoke
    (0/2): the judge carries its sources in ``judge_urls``.
    Claims without quote+URL are reported as ``unchecked`` (not as
    unfaithful): the fraction is over CHECKED citations only.
    """
    with open(run_path, encoding="utf-8") as fh:
        run = json.load(fh)
    entries = {e["id"]: e for e in load_entries(claims_path, lang)}
    # Fallback: unfiltered rows (claims files are truth-shaped, so the
    # filter above suffices; this only guards hand-made fixtures).
    if not entries:
        with open(claims_path, encoding="utf-8") as fh:
            rows = json.load(fh)
        entries = {r["id"]: r for r in rows if r.get("id")}
    rows: list[dict[str, Any]] = []
    for item in run.get("claims", []):
        cid = item.get("claim_id", "?")
        quote = item.get("quote")
        urls = item.get("urls") or []
        judge_urls = item.get("judge_urls") or []
        if urls:
            url, url_source = urls[0], "browsed"
        elif judge_urls:
            url, url_source = judge_urls[0], "stated"
        else:
            url, url_source = None, None
        row: dict[str, Any] = {"claim_id": cid, "url": url,
                               "url_source": url_source,
                               "quote": quote,
                               "status": "unchecked", "snapshot_used": False}
        entry = entries.get(cid)
        if entry is None or not quote or not url:
            row["reason"] = ("claim_not_in_bench" if entry is None
                             else "no_quote_or_no_url")
            rows.append(row)
            continue
        ci = claim_input_for(entry)
        page, err = fetch_page(url, fetch_fn=fetch_fn)
        if page is None and snapshot_dir is not None:
            page, snap_path = snapshot_for(snapshot_dir, url)
            if page is not None:
                row["snapshot_used"] = True
                row["snapshot_path"] = snap_path
        if page is None:
            row["status"] = "unresolved"
            row["reason"] = f"fetch_failed: {err}" if err else "fetch_failed"
            rows.append(row)
            continue
        verdict = assess_quote(page, quote, ci.value, ci.unit, ci.tolerance,
                                url)
        row["status"] = ("faithful" if verdict["faithful"]
                         else "unfaithful")
        row.update({k: v for k, v in verdict.items() if k != "faithful"})
        rows.append(row)
    checked = [r for r in rows if r["status"] in ("faithful", "unfaithful")]
    faithful = [r for r in checked if r["status"] == "faithful"]
    lo, hi = wilson_interval(len(faithful), len(checked))
    return {
        "run_path": str(run_path),
        "claims_path": str(claims_path),
        "n_claims": len(rows),
        "n_checked": len(checked),
        "n_faithful": len(faithful),
        "fraction_faithful": (round(len(faithful) / len(checked), 4)
                              if checked else None),
        "wilson_lo": round(lo, 4),
        "wilson_hi": round(hi, 4),
        "n_unresolved": sum(1 for r in rows if r["status"] == "unresolved"),
        "n_unchecked": sum(1 for r in rows if r["status"] == "unchecked"),
        "n_snapshot_used": sum(1 for r in rows if r.get("snapshot_used")),
        "n_url_stated": sum(1 for r in rows
                            if r.get("url_source") == "stated"),
        "n_url_browsed": sum(1 for r in rows
                             if r.get("url_source") == "browsed"),
        "unresolved": sorted(r["claim_id"] for r in rows
                            if r["status"] == "unresolved"),
        "unfaithful": sorted(r["claim_id"] for r in rows
                            if r["status"] == "unfaithful"),
        "rows": rows,
    }


def build_parser() -> argparse.ArgumentParser:
    """CLI flags for the fidelity audit."""
    parser = argparse.ArgumentParser(
        description="F2-1: deterministic re-check of baseline literal "
        "quotes against their browsed URLs.")
    parser.add_argument("--run", required=True,
                        help="baseline run JSON (*-baseline-run*.json)")
    parser.add_argument("--claims", default=str(DEFAULT_CLAIMS))
    parser.add_argument("--lang", default="en")
    parser.add_argument("--snapshot-dir", default=None,
                        help="declared snapshot fallback dir "
                        "(sha256(url).html|.txt)")
    parser.add_argument("--out", default=None,
                        help="report JSON path "
                        "(default: <run>.fidelity.json)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint (argv injectable for tests)."""
    args = build_parser().parse_args(argv)
    report = assess_run(args.run, args.claims, args.lang,
                        args.snapshot_dir)
    out = args.out or (args.run + ".fidelity.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    frac = report["fraction_faithful"]
    frac_s = f"{frac:.3f}" if frac is not None else "n/a"
    print(f"[f2-fidelity] {report['n_faithful']}/{report['n_checked']}="
          f"{frac_s} [{report['wilson_lo']:.3f},{report['wilson_hi']:.3f}] "
          f"unresolved={report['unresolved']} "
          f"snapshots={report['n_snapshot_used']}", flush=True)
    print(f"[f2-fidelity] report: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
