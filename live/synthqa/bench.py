"""S4 benchmark: run_verify over the 12 truths + E1 internal scoring.

World scoring (vs the real world through the swarm + verifier) covers
ONLY claims with ``expected`` true/false AND a world-checkable ref.
Excluded from scoring and counted apart (declared):

- ``coherent-internal`` (fictitious-company arithmetic/declarations),
- ``unverifiable`` (opinions, greetings, meta),
- ``expected=false`` inside earnings pieces (traps contradicting the
  INVENTED report, e.g. "385%" vs report "38.5%" — not world refs).

Consequence: both earnings pieces (``e1_earn``, ``earnings_luminara``)
have an EMPTY world slice and get no run_verify run; they are scored
SEPARATELY by :func:`score_earnings` — arithmetic re-computed by code
vs the truth (never vs the world).

One run_verify run per remaining piece (10), semaphore 8 inside each
run (``--max-concurrency 8``). Results land in
``data/synth/bench/results.json`` (gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import ast
import json
import math
import os
import re
import sys
import time
from argparse import Namespace
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from live.synthqa import EARNINGS_IDS, PIECES, REPO_ROOT, media_paths  # noqa: E402

BENCH_DIR = os.path.join(REPO_ROOT, "data", "synth", "bench")
FILTERED_DIR = os.path.join(BENCH_DIR, "filtered")

WORLD_EXPECTED = ("true", "false")
MAX_CONCURRENCY = 8
CLAIM_DEADLINE_S = 90.0

# Percent-point tolerance (said vs recomputed) and relative tolerance
# for plain sums: truths round to 1 decimal ("12,4" for 12.393).
PP_TOL = 0.10
REL_TOL = 0.005

_NUM_RE = re.compile(r"-?\d[\d.,]*")
_PCT_RE = re.compile(r"(-?\d[\d.,]*)\s*(?:%|percent|por ciento)", re.I)
_NOTNUM_RE = re.compile(r"not\s+(-?\d[\d.,]*)", re.I)
_BIGM_RE = re.compile(r"(\d[\d.,]*)\s*B\s*=\s*(\d[\d.,]*)\s*M")
_ARITH_SPAN_RE = re.compile(r"\d[\d.,\s]*[+\-*/()][\d.,\s+\-*/()]*\d")
_UNIT_WORDS = ("trillion", "billones", "billion", "millones", "million",
               "thousand", "percent", "por ciento", "%")

_FALL_WORDS = ("cayó", "cayo", "caída", "caida", "disminu", "bajó", "bajado",
               "fell", "fallen", "dropped", "declin", "down ")

_DECL_SIGNALS = ("fictici", "fictitious", "demo", "invented",
                 "coherent", "declarad", "construcc", "guion", "report")


# ---------------------------------------------------------------------------
# Split: world-scorable vs excluded vs E1-internal
# ---------------------------------------------------------------------------

def split_entries(rows: list[dict], piece_id: str) -> dict:
    """Split truth rows into world / e1_internal / excluded counts."""
    world, e1_internal = [], []
    excluded = {"coherent_internal": 0, "unverifiable": 0,
                "earnings_false_internal": 0, "other": 0}
    for row in rows:
        exp = row.get("expected")
        if piece_id in EARNINGS_IDS:
            e1_internal.append(row)
            if exp == "coherent-internal":
                excluded["coherent_internal"] += 1
            elif exp == "unverifiable":
                excluded["unverifiable"] += 1
            elif exp == "false":
                excluded["earnings_false_internal"] += 1
            else:
                excluded["other"] += 1
        elif exp in WORLD_EXPECTED:
            world.append(row)
        elif exp == "coherent-internal":
            excluded["coherent_internal"] += 1
        elif exp == "unverifiable":
            excluded["unverifiable"] += 1
        else:
            excluded["other"] += 1
    return {"world": world, "e1_internal": e1_internal, "excluded": excluded}


def write_filtered(piece_id: str, world_rows: list[dict],
                   outdir: str = FILTERED_DIR) -> str:
    """Write the world-scorable slice run_verify will consume."""
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{piece_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(world_rows, fh, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# E1 internal-coherence scoring (code vs truth, never vs world)
# ---------------------------------------------------------------------------

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)


def _eval_node(node) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(
            node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _ALLOWED_UNARY):
        val = _eval_node(node.operand)
        return val if isinstance(node.op, ast.UAdd) else -val
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Mod):
            return left % right
        return left / right
    raise ValueError(f"unsupported expression: {ast.dump(node)[:80]}")


def normalize_calc_expr(expr: str, lang: str) -> str:
    """Normalize a truth calc string into evaluable arithmetic.

    ES uses comma decimals ("753,6") and 'x' products; EN uses comma
    thousands ("27,811,517") and '.' decimals. Only [0-9eE.\\s+\\-*/%()]
    survive; anything else raises ValueError.
    """
    cleaned = expr.replace("×", "*").replace("x", "*").replace("X", "*")
    cleaned = cleaned.replace("%", "")
    if lang == "es":
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    cleaned = cleaned.strip()
    if not cleaned or not re.fullmatch(r"[0-9eE.\s+\-*/%()]+", cleaned):
        raise ValueError(f"not arithmetic: {expr[:80]!r}")
    # Guard against '**' (power) smuggled through the charset.
    if "**" in cleaned:
        raise ValueError(f"not arithmetic: {expr[:80]!r}")
    return cleaned


def safe_eval(expr: str, lang: str) -> float:
    """Evaluate builder-style arithmetic with a restricted AST."""
    tree = ast.parse(normalize_calc_expr(expr, lang), mode="eval")
    return _eval_node(tree)


def parse_number(token: str, lang: str) -> float:
    """Parse one builder number token ("12,4" ES / "27,811" EN)."""
    token = token.strip().rstrip("%").strip()
    if lang == "es":
        token = token.replace(".", "").replace(",", ".")
    else:
        token = token.replace(",", "")
    return float(token)


def first_percent(text: str, lang: str) -> float | None:
    """First ``N%`` / ``N percent`` / ``N por ciento`` in a claim text."""
    match = _PCT_RE.search(text or "")
    if not match:
        return None
    try:
        return parse_number(match.group(1), lang)
    except ValueError:
        return None


def arith_parts(calc: str) -> list[str]:
    """Split a ``a = b = c%`` calc chain into its sides."""
    return [p.strip() for p in str(calc or "").split("=") if p.strip()]


def _close_enough(said: float, recomputed: float, is_percent: bool) -> bool:
    if is_percent:
        return abs(said - recomputed) <= PP_TOL
    denom = max(abs(said), abs(recomputed), 1e-9)
    return abs(said - recomputed) / denom <= REL_TOL


def _calc_source(row: dict) -> str:
    calc = row.get("calculation")
    if isinstance(calc, str) and calc.strip():
        return calc
    ref = row.get("ref") or ""
    # EN earnings refs embed calcs: "Invented calc (4.82-4.29)/4.29 = 12.35%"
    match = re.search(r"[Cc]alc\s+(.+)", ref)
    if match:
        return match.group(1)
    if "=" in ref and re.search(r"\d", ref):
        return ref
    return ""


def text_numbers(text: str, lang: str) -> list[float]:
    """All parsed numbers in a claim text, in order."""
    nums = []
    for tok in _NUM_RE.findall(text or ""):
        try:
            nums.append(parse_number(tok, lang))
        except ValueError:
            continue
    return nums


def _step_results(step: str, lang: str) -> tuple[list[float], list[float]]:
    """(recomputed values, truth-side values) for one ``a = b`` step.

    Multi-step calcs ("0,42 x 120 = 50,4; 50,4/96,3 = ...") are split
    by the caller on ';'. Percent steps yield raw AND x100 candidates;
    the match decides which scale the truth used.
    """
    parts = arith_parts(step)
    if len(parts) < 2:
        return [], []
    try:
        raw = safe_eval(parts[0], lang)
    except (ValueError, SyntaxError, ZeroDivisionError):
        # Prose-wrapped calc ("Invented report: APAC 0.72B ..."): scan
        # for an evaluable arithmetic span instead of failing.
        raw = None
        for span in _ARITH_SPAN_RE.findall(parts[0]):
            try:
                raw = safe_eval(span, lang)
                break
            except (ValueError, SyntaxError, ZeroDivisionError):
                continue
        if raw is None:
            return [], []
    truth_vals = []
    for side in parts[1:]:
        for tok in _NUM_RE.findall(side):
            try:
                truth_vals.append(parse_number(tok, lang))
            except ValueError:
                continue
    cands = [raw]
    if "%" in step or "percent" in step.lower() or "por ciento" in step:
        cands.append(raw * 100.0)
    return cands, truth_vals


def _unit_conversion_ok(calc: str, lang: str) -> float | None:
    """Verify an ``XB = YM`` unit anchor; return Y when exact, else None."""
    match = _BIGM_RE.search(calc or "")
    if not match:
        return None
    try:
        big = parse_number(match.group(1), lang)
        small = parse_number(match.group(2), lang)
    except ValueError:
        return None
    if abs(big * 1000.0 - small) / max(small, 1e-9) <= REL_TOL:
        return small
    return None


def check_coherent(row: dict, lang: str) -> dict:
    """Code-vs-truth check for one coherent-internal claim.

    Arithmetic claims: re-evaluate every calc step in the truth and
    require (a) the truth's stated values to match the recomputation
    (``math_vs_truth``) and (b) the claim text to state a matching
    number — a step result, a truth value, every operand (sums whose
    total lives only in the truth), or a magnitude with a fall-verb
    ("cayó un 5,2%" for -5.196%). Declarative claims (no arithmetic):
    label-consistency only — the truth must declare the fictitious
    construction (explicitly the weaker check).
    """
    cid = row.get("id", "?")
    text = str(row.get("text", "") or "")
    calc = _calc_source(row)
    steps = [s for s in str(calc or "").split(";") if s.strip()]
    if len(arith_parts(calc)) >= 2:
        # Exact-unit anchor ("0.72B = 720M"): conversion, not arithmetic.
        anchor = _unit_conversion_ok(calc, lang)
        if anchor is not None:
            said = next((n for n in text_numbers(text, lang)
                         if _close_enough(n, anchor, False)), None)
            ok = said is not None
            return {"claim_id": cid, "check": "arith",
                    "recomputed": round(anchor, 4), "said": said,
                    "math_vs_truth": True, "said_vs_math": ok,
                    "pass": ok, "detail": f"unit anchor {calc[:120]}"}
        all_cands, all_truth, operands = [], [], []
        math_ok = True
        for step in steps:
            cands, truth_vals = _step_results(step, lang)
            if not cands:
                math_ok = False
                continue
            all_cands.extend(cands)
            all_truth.extend(truth_vals)
            first = arith_parts(step)[0]
            operands.extend(text_numbers(first, lang))
            # A truth side matches on either scale (ratio or percent).
            if truth_vals and not any(
                    _close_enough(v, c, False)
                    or _close_enough(v, c, True)
                    for v in truth_vals for c in cands):
                math_ok = False
        said_pct = first_percent(text, lang)
        is_percent = said_pct is not None or "%" in calc
        text_nums = text_numbers(text, lang)
        said = next((n for n in text_nums
                     if any(_close_enough(n, c, is_percent)
                            for c in all_cands + all_truth)), None)
        said_via = "result"
        if said is None and operands and all(
                any(_close_enough(o, n, False) for n in text_nums)
                for o in operands):
            # Operands stated, total only in truth ("612 over 431").
            said, said_via = operands[0], "operands"
        if said is None and any(w in text.lower() for w in _FALL_WORDS):
            said = next((n for n in text_nums
                         if any(_close_enough(abs(n), abs(c), is_percent)
                                for c in all_cands)), None)
            said_via = "magnitude+fall-verb"
        if said is None:
            said = said_pct if said_pct is not None else (
                text_nums[0] if text_nums else None)
        ok_said = said is not None and (
            said_via != "result" or any(
                _close_enough(said, c, is_percent)
                for c in all_cands + all_truth))
        shown = all_cands[-1] if all_cands else None
        return {"claim_id": cid, "check": "arith",
                "recomputed": round(shown, 4) if shown is not None else None,
                "truth_vals": [round(v, 4) for v in all_truth],
                "said": said, "said_via": said_via,
                "math_vs_truth": math_ok, "said_vs_math": ok_said,
                "pass": bool(math_ok and ok_said),
                "detail": calc[:160]}
    blob = f"{row.get('ref', '')} {row.get('ground_truth', '')}".lower()
    ok = any(sig in blob for sig in _DECL_SIGNALS)
    return {"claim_id": cid, "check": "decl_label",
            "pass": ok, "detail": "declarative: truth declares the "
            f"fictitious construction ({'found' if ok else 'MISSING'})"}


def _unit_after(haystack: str, token: str) -> str | None:
    """Unit word within ~20 chars after a number token occurrence."""
    idx = haystack.find(token)
    if idx < 0:
        return None
    window = haystack[idx + len(token):idx + len(token) + 20].lower()
    for unit in _UNIT_WORDS:
        if re.search(rf"\b{re.escape(unit)}", window):
            return unit
    return None


def check_trap(row: dict, lang: str) -> dict:
    """Code-vs-truth check for one EN-E1 ``expected=false`` trap.

    The label is internally consistent when the claim is INCOMPATIBLE
    with the invented report: an arithmetic span in the ref recomputes
    to a different value, the ref explicitly negates the said number
    ("not 2,400"), the units differ ("410 billion" vs "410 MILLION"),
    or the claim is impossible on its face (Feb 30) / refuted by the
    fictitious-construction ref (E1-38).
    """
    cid = row.get("id", "?")
    text = str(row.get("text", "") or "")
    ref = str(row.get("ref", "") or "")
    # Impossible-date trap ("February 30, 2026").
    if re.search(r"february\s+30", text, re.I):
        try:
            import datetime as _dt

            _dt.date(2026, 2, 30)
            return {"claim_id": cid, "check": "date", "pass": False,
                    "detail": "Feb 30 parsed?!"}
        except ValueError:
            return {"claim_id": cid, "check": "date", "pass": True,
                    "detail": "February 30 does not exist: trap confirmed"}
    # Entity trap ("real listed company" vs fictitious-by-construction).
    if re.search(r"real\s+listed\s+compan", text, re.I):
        ok = "fictitious" in ref.lower()
        return {"claim_id": cid, "check": "entity", "pass": ok,
                "detail": "ref declares Helioshore fictitious"
                if ok else "ref does not declare fictitious"}
    said_pct = first_percent(text, lang)
    said_toks = _NUM_RE.findall(text)
    try:
        said = (said_pct if said_pct is not None
                else parse_number(said_toks[0], lang))
    except (IndexError, ValueError):
        said = None
    if said is None:
        return {"claim_id": cid, "check": "trap", "pass": False,
                "detail": "no number in claim text"}
    is_pct = said_pct is not None
    # (a) Explicit negation in the ref ("not 29.1B", "not 2,400").
    for tok in _NOTNUM_RE.findall(ref):
        try:
            if _close_enough(parse_number(tok, lang), said, is_pct):
                return {"claim_id": cid, "check": "trap", "said": said,
                        "pass": True,
                        "detail": f"ref explicitly negates {tok}: "
                        "trap holds"}
        except ValueError:
            continue
    # (b) Arithmetic spans in the ref recompute away from the claim.
    for span in _ARITH_SPAN_RE.findall(ref):
        try:
            recomputed = safe_eval(span, lang)
        except (ValueError, SyntaxError, ZeroDivisionError):
            continue
        cands = [recomputed]
        if abs(recomputed) < 1.0:
            cands.append(recomputed * 100.0)
        if not any(_close_enough(said, c, is_pct) for c in cands):
            return {"claim_id": cid, "check": "trap", "said": said,
                    "report": round(cands[-1], 4), "pass": True,
                    "detail": f"ref span {span[:60]} recomputes away "
                    "from the claim: trap holds"}
        return {"claim_id": cid, "check": "trap", "said": said,
                "report": round(cands[-1], 4), "pass": False,
                "detail": "said MATCHES recomputed report: label suspect"}
    # (c) Unit trap: same digits, different units ("410 billion" said,
    # "410 MILLION" in the report).
    said_unit = _unit_after(text, said_toks[0]) if said_toks else None
    for tok in _NUM_RE.findall(ref):
        try:
            num = parse_number(tok, lang)
        except ValueError:
            continue
        if _close_enough(num, said, is_pct):
            ref_unit = _unit_after(ref, tok)
            if (said_unit and ref_unit and said_unit != ref_unit
                    and not (said_unit in ("percent", "%")
                             and ref_unit in ("percent", "%"))):
                return {"claim_id": cid, "check": "trap", "said": said,
                        "report": num, "pass": True,
                        "detail": f"unit trap ({said_unit} vs {ref_unit}): "
                        "trap holds"}
    # (d) Report quotes a different value ("guide is 19.1B" vs 29.1B).
    ref_nums = text_numbers(ref, lang)
    if ref_nums and not any(_close_enough(n, said, is_pct)
                            for n in ref_nums):
        return {"claim_id": cid, "check": "trap", "said": said,
                "report": ref_nums[0], "pass": True,
                "detail": "report quotes a different value: trap holds"}
    return {"claim_id": cid, "check": "trap", "said": said,
            "pass": False,
            "detail": "said matches the report with no negation or "
            "unit split: label suspect"}


def score_earnings(rows: list[dict], lang: str) -> dict:
    """Internal-coherence panel for one earnings piece (code only)."""
    per_claim = []
    for row in rows:
        exp = row.get("expected")
        if exp == "coherent-internal":
            per_claim.append(check_coherent(row, lang))
        elif exp == "false":
            per_claim.append(check_trap(row, lang))
        else:  # unverifiable greetings/meta: nothing to recompute
            per_claim.append({"claim_id": row.get("id", "?"),
                              "check": "skip_unverifiable", "pass": True,
                              "detail": "meta/greeting, excluded"})
    scored = [c for c in per_claim if c["check"] != "skip_unverifiable"]
    passed = sum(1 for c in scored if c["pass"])
    return {"n": len(rows), "n_scored": len(scored), "n_pass": passed,
            "coherence": round(passed / len(scored), 4) if scored else None,
            "per_claim": per_claim}


# ---------------------------------------------------------------------------
# World runs (run_verify, 1 run/piece, semaphore 8)
# ---------------------------------------------------------------------------

def run_world_piece(piece: dict, filtered_path: str, api_key: str,
                    snapshot_dir: str | None = None) -> dict:
    """One run_verify run over a piece's world slice (semaphore 8)."""
    # Imported here: run_verify inserts the repo root on sys.path.
    from live import run_verify  # noqa: E402

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_path = os.path.join(BENCH_DIR, "runs",
                            f"verify-{piece['id']}-{stamp}.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    args = Namespace(
        claims=filtered_path, lang=piece["lang"], limit=None,
        log=log_path, db=None,
        snapshot_dir=snapshot_dir or os.path.join(REPO_ROOT, "data", "snap"),
        max_concurrency=MAX_CONCURRENCY, deadline=CLAIM_DEADLINE_S,
        api_key=api_key)
    t0 = time.time()
    summary = asyncio.run(run_verify.amain(args))
    summary["bench_wall_s"] = round(time.time() - t0, 1)
    return summary


def load_truth(path: str) -> list[dict]:
    """Load one builder truth file."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """CLI flags for the S4 benchmark."""
    parser = argparse.ArgumentParser(
        description="S4 bench: run_verify per piece (sem 8) + E1 scoring.")
    parser.add_argument("--pieces", default="",
                        help="comma-separated piece ids (default: all 12)")
    parser.add_argument("--api-key", default="",
                        help="Meta API key (default: MODEL_API_KEY env)")
    parser.add_argument("--skip-world", action="store_true",
                        help="skip run_verify (E1 code scoring only, $0)")
    parser.add_argument("--out", default=None,
                        help="JSON output (default: "
                        "data/synth/bench/results.json)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark; write results.json."""
    args = build_parser().parse_args(argv)
    try:
        from dotenv import load_dotenv  # noqa: E402

        load_dotenv()
    except ImportError:
        pass
    api_key = args.api_key or os.environ.get("MODEL_API_KEY", "")
    wanted = [p.strip() for p in args.pieces.split(",") if p.strip()]
    selected = [p for p in PIECES if not wanted or p["id"] in wanted]
    if wanted and len(selected) != len(wanted):
        missing = {*wanted} - {p["id"] for p in selected}
        raise SystemExit(f"unknown pieces: {sorted(missing)}")
    if not args.skip_world and not api_key:
        raise SystemExit("MODEL_API_KEY not configured; source .env first.")

    pieces_out: dict = {}
    for piece in selected:
        pid = piece["id"]
        paths = media_paths(piece)
        rows = load_truth(paths["truth"])
        split = split_entries(rows, pid)
        entry: dict = {
            "piece": pid, "lang": piece["lang"], "kind": piece["kind"],
            "speakers": piece["speakers"], "n_truth": len(rows),
            "n_world": len(split["world"]), "excluded": split["excluded"],
        }
        if pid in EARNINGS_IDS:
            entry["e1_internal"] = score_earnings(split["e1_internal"],
                                                  piece["lang"])
        if split["world"] and not args.skip_world:
            filtered = write_filtered(pid, split["world"])
            print(f"[bench] {pid}: {len(split['world'])} world claims "
                  f"(sem {MAX_CONCURRENCY}) ...", flush=True)
            entry["world_run"] = run_world_piece(piece, filtered, api_key)
            print(f"[bench] {pid}: matrix={entry['world_run']['matrix']} "
                  f"usd={entry['world_run']['total_cost_usd']}", flush=True)
        else:
            entry["world_run"] = None
            entry["world_skip_reason"] = (
                "--skip-world" if args.skip_world
                else "empty world slice (earnings: internal scoring only)")
            print(f"[bench] {pid}: world skipped "
                  f"({entry['world_skip_reason']})", flush=True)
        pieces_out[pid] = entry

    out = args.out or os.path.join(BENCH_DIR, "results.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    payload = {"generated_utc": datetime.now(timezone.utc).isoformat(),
               "semaphore": MAX_CONCURRENCY,
               "deadline_s": CLAIM_DEADLINE_S,
               "scoring_rule": "world = expected true/false with a "
               "world-checkable ref; coherent-internal + unverifiable + "
               "earnings-false excluded and counted apart; earnings "
               "scored separately by code (score_earnings)",
               "pieces": pieces_out}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    usd = round(sum((p.get("world_run") or {}).get("total_cost_usd", 0.0)
                    for p in pieces_out.values()), 4)
    print(f"[bench] world usd={usd}, out={out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
