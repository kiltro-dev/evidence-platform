#!/usr/bin/env python3
"""M5 bundle verifier: offline re-execution of an Atlas Live run.

Usage (from the repo root, or anywhere with the repo on PYTHONPATH)::

    python verify_bundle.py BUNDLE.zip [--engine auto|live|stdlib]

or against an extracted directory::

    python verify_bundle.py ./bundle_dir/

Re-checks, with NO network and NO model calls:

1. File integrity: every file re-hashed against ``manifest.json``.
2. Structure: every package dict re-validated (M2a rules).
3. Literal grounding: every quote re-grepped in its bundle snapshot
   (whitespace-normalized) + snapshot sha256 re-checked.
4. Verdicts: the verifier re-runs per claim; each re-run verdict must
   equal the verdict recorded in ``claims.json`` AND the latest
   ``claim.verdict`` in ``run.jsonl``.

Engines: ``live`` uses the REAL ``live.verifier.check`` (needs the
repo importable); ``stdlib`` uses a vendored stdlib-only mirror of the
M2a pipeline (same file; always available); ``auto`` (default) prefers
``live`` and falls back to ``stdlib``. Prints PASS/FAIL per claim and
exits 0 iff every claim passes.

This file is stdlib-only by itself: it is copied verbatim into every
bundle ZIP so a bundle stays verifiable without the repo (mirror
engine). The mirror duplicates ``src/atlas/core/units.py``,
``src/atlas/core/compare.py`` (year/scale rules) and
``live/verifier/check.py`` (pipeline) as of M5 (2026-11-01); any drift
between engines is itself a finding — the M5 tests assert both engines
agree on the roundtrip bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path

MIRROR_OF = "src/atlas/core + live/verifier/check.py @ M5 2026-11-01"
DEFAULT_REL_TOLERANCE = 0.02
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
WS_RE = re.compile(r"\s+")


def normalize_ws(s: str) -> str:
    return WS_RE.sub(" ", s or "").strip()


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# stdlib mirror of src/atlas/core/units.py + compare.py (year/scale rules)
# ---------------------------------------------------------------------------

_M_SCALE_WORDS = {
    "trillion": 1e12, "trillions": 1e12, "tn": 1e12,
    "billion": 1e9, "billions": 1e9, "b": 1e9, "bn": 1e9,
    "million": 1e6, "millions": 1e6, "mn": 1e6, "m": 1e6,
    "thousand": 1e3, "thousands": 1e3, "k": 1e3,
    "hundred": 1e2, "hundreds": 1e2,
    "billón": 1e12, "billon": 1e12, "billones": 1e12,
    "millardo": 1e9, "millardos": 1e9,
    "millón": 1e6, "millon": 1e6, "millones": 1e6,
    "mil": 1e3, "mil millones": 1e9,
    "T": 1e12,
}

_M_CURRENCY = {"$": "USD", "usd": "USD", "us$": "USD", "€": "EUR",
               "eur": "EUR", "£": "GBP", "gbp": "GBP", "¥": "JPY",
               "jpy": "JPY"}

_M_ALIASES = {
    "usd": "USD", "dollar": "USD", "dollars": "USD",
    "eur": "EUR", "euro": "EUR", "euros": "EUR", "gbp": "GBP",
    "percent": "percent", "%": "percent",
    "percentage point": "pp", "percentage points": "pp", "pp": "pp",
    "people": "count", "inhabitants": "count", "deaths": "count",
    "cases": "count",
    "dólar": "USD", "dolar": "USD", "dólares": "USD", "dolares": "USD",
    "libra": "GBP", "libras": "GBP", "por ciento": "percent",
    "punto porcentual": "pp", "puntos porcentuales": "pp",
    "persona": "count", "personas": "count", "habitante": "count",
    "habitantes": "count", "muerte": "count", "muertes": "count",
    "caso": "count", "casos": "count",
    "año": "years", "ano": "years", "años": "years", "anos": "years",
    "m/s": "m/s", "meters per second": "m/s",
    "metres per second": "m/s", "km/s": "km/s",
    "celsius": "celsius", "c": "celsius", "°c": "celsius",
    "fahrenheit": "fahrenheit", "°f": "fahrenheit", "kelvin": "kelvin",
    "years": "years", "year": "years",
}

_M_QUANTITY_RE = re.compile(
    r"(?P<symbol>[$€£¥])?\s*(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+,\d{1,2}(?!\d)|"
    r"\d{1,3}(?:\.\d{3})+,\d+|\d{1,3}(?:\.\d{3}){2,}(?![\d.])|\d{1,3}\.\d{3}(?![\d.])|\d+(?:\.\d+)?)"
    r"(?:\s*(?P<scale>trillions?|billions?|millions?|thousands?|hundreds?|"
    r"tn|bn|mn|billones|billón|billon|millones|millón|millon|"
    r"millardos|millardo|mil millones|mil)(?![a-zA-Záéíóúüñ/])"
    r"|(?P<scale_short>k(?![a-zA-Z/])|m(?![a-zA-Z/])|b(?![a-zA-Z/])|(?-i:T)(?![a-zA-Z/])))?"
    r"(?P<pct>\s*%)?",
    re.IGNORECASE,
)

_M_UNIT_AFTER_RE = re.compile(
    r"^\s*(?:de\s+)?(usd|us\$|dollars?|dólares|dolares|dólar|dolar|euros?|eur|gbp|pounds?|pesos?|libras?|jpy|yen|"
    r"percentage points?|percent|pp(?![a-zA-Z])|por ciento|puntos? porcentuales?|"
    r"people|personas?|inhabitants|habitantes?|deaths|muertes?|cases|casos?|"
    r"m/s|meters per second|metres per second|km/s|"
    r"celsius|fahrenheit|kelvin|°c|°f|years?|años?|anos?)",
    re.IGNORECASE,
)

_M_EXPLICIT_SCALE_RE = re.compile(
    r"\b(trillions?|billions?|millions?|thousands?|hundreds?|billones?|"
    r"bill[oó]n(?:es)?|millardos?|millones?|mill[oó]n(?:es)?|mil|"
    r"centenas?|centenares?)\b",
    re.IGNORECASE,
)

_M_DOT_MULTI = re.compile(r"\d{1,3}(?:\.\d{3}){2,}$")
_M_DOT_SINGLE = re.compile(r"\d{1,3}\.\d{3}$")
_M_DOT_COMMA = re.compile(r"\d{1,3}(?:\.\d{3})+,\d+$")
_M_DEC_COMMA = re.compile(r"\d+,\d{1,2}$")
_M_ES_SCALES = frozenset({"millones", "millón", "millon", "billones",
                          "billón", "billon", "millardos", "millardo",
                          "mil", "mil millones"})
_M_ES_UNITS = frozenset({"dólares", "dolares", "dólar", "dolar", "euros",
                         "euro", "pesos", "peso", "libras", "libra",
                         "personas", "persona", "habitantes", "habitante",
                         "muertes", "muerte", "casos", "caso", "años",
                         "anos", "año", "ano", "por ciento",
                         "puntos porcentuales", "punto porcentual"})


def _m_normalize_unit(raw):
    if not raw:
        return None
    key = raw.strip().lower()
    if key in _M_CURRENCY:
        return _M_CURRENCY[key]
    return _M_ALIASES.get(key, raw.strip())


def _m_units_compatible(a, b):
    na, nb = _m_normalize_unit(a), _m_normalize_unit(b)
    if na is None or nb is None:
        return False
    return na == nb


def _m_parse_quantities(text):
    out = []
    for m in _M_QUANTITY_RE.finditer(text or ""):
        raw_num = m.group("num")
        scale_word = m.group("scale") or m.group("scale_short") or ""
        scale = _M_SCALE_WORDS.get(
            scale_word, _M_SCALE_WORDS.get(scale_word.lower(), 1.0))
        if m.group("pct"):
            unit = "percent"
        elif m.group("symbol"):
            unit = _M_CURRENCY.get(m.group("symbol"))
        else:
            after = text[m.end(): m.end() + 28]
            um = _M_UNIT_AFTER_RE.match(after)
            unit = um.group(1).strip() if um else None
        if _M_DOT_MULTI.fullmatch(raw_num):
            num = float(raw_num.replace(".", ""))
        elif _M_DOT_COMMA.fullmatch(raw_num):
            int_part, frac = raw_num.split(",")
            num = float(int_part.replace(".", "") + "." + frac)
        elif _M_DOT_SINGLE.fullmatch(raw_num):
            u = unit or ""
            u = u[3:] if u.lower().startswith("de ") else u
            es_marker = (scale_word.lower() in _M_ES_SCALES
                         or u.lower() in _M_ES_UNITS)
            num = (float(raw_num.replace(".", "")) if es_marker
                   else float(raw_num))
        elif _M_DEC_COMMA.fullmatch(raw_num):
            num = float(raw_num.replace(",", "."))
        else:
            num = float(raw_num.replace(",", ""))
        out.append({"raw_text": m.group(0).strip(),
                    "value": num * scale,
                    "normalized_unit": _m_normalize_unit(unit)})
    return out


def _m_is_year_quantity(q):
    raw = (q["raw_text"] or "").strip()
    return raw.isdigit() and len(raw) == 4 and 1000 <= q["value"] <= 2100


def _m_is_explicit_scale(raw):
    return bool(_M_EXPLICIT_SCALE_RE.search(raw or ""))


def _m_scale_error_power(claim_value, evidence_value):
    if not claim_value or not evidence_value:
        return None
    try:
        ratio = abs(float(evidence_value) / float(claim_value))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    for k in (3, 6, 9, 12):
        target = 10.0 ** k
        if abs(ratio - target) / target <= 0.01:
            return k
        inv = 1.0 / target
        if abs(ratio - inv) / inv <= 0.01:
            return -k
    return None


def _m_check_package(pkg, claim, snapshot):
    """Mirror of live.verifier.check._check_package. Returns (outcome, reason)."""
    if pkg.get("claim_id") != claim.get("claim_id"):
        return None, "claim_mismatch"
    if snapshot is None:
        return None, "snapshot_missing"
    if sha256_of_text(snapshot) != pkg.get("sha256"):
        return None, "snapshot_hash_mismatch"
    if not normalize_ws(pkg.get("quote", "")) or \
            normalize_ws(pkg.get("quote", "")) not in normalize_ws(snapshot):
        return None, "quote_not_found"
    cands = [q for q in _m_parse_quantities(pkg.get("quote", ""))
             if not _m_is_year_quantity(q)]
    if not cands:
        return None, "no_number_in_quote"
    if not _m_is_explicit_scale(claim.get("raw") or ""):
        for q in cands:
            if _m_scale_error_power(claim.get("value"), q["value"]) is not None:
                return None, "possible_scale_error"
    if claim.get("unit") is None:
        return None, "claim_unit_unknown"
    compat = [q for q in cands
              if _m_units_compatible(claim.get("unit"), q["normalized_unit"])]
    if not compat:
        return None, "unit_mismatch"
    best = min(compat, key=lambda q: abs(claim["value"] - q["value"])
               / (abs(q["value"]) or 1.0))
    denom = abs(best["value"]) if best["value"] != 0 else 1.0
    if abs(claim["value"] - best["value"]) / denom > float(
            claim.get("tolerance", DEFAULT_REL_TOLERANCE)):
        return "contradict", "numeric_mismatch"
    if (claim.get("claim_year") is not None
            and pkg.get("source_year") is not None
            and claim["claim_year"] != pkg["source_year"]):
        return None, "temporal_mismatch"
    return "support", "numeric_match"


def _m_verify_claim(claim, packages, snapshots):
    """Mirror of live.verifier.check.verify_claim. Returns verdict dict."""
    usable, unusable = [], []
    for pkg in packages or []:
        snap = snapshots.get(pkg.get("sha256", ""))
        outcome, reason = _m_check_package(pkg, claim, snap)
        if outcome is None:
            unusable.append(reason)
        else:
            usable.append((pkg, outcome, reason))
    if not usable:
        reasons = ["no_usable_package"] + sorted(set(unusable))
        return {"verdict": "unverifiable", "reasons": reasons,
                "package_ids": [p.get("package_id") for p in packages or []]}
    primaries = [u for u in usable if u[0].get("source_type") == "primary"]
    deciding = primaries or usable
    outcomes = {o for _, o, _ in deciding}
    if outcomes == {"support"}:
        label = "supported"
    elif outcomes == {"contradict"}:
        label = "contradicted"
    else:
        label = "disputed"
    reasons = sorted({r for _, _, r in deciding})
    if label == "disputed":
        reasons = ["packages_disagree"] + reasons
    return {"verdict": label, "reasons": reasons,
            "package_ids": [p.get("package_id") for p, _, _ in deciding]}


def _m_validate_package_dict(pkg):
    """Mirror of EvidencePackage structural rules. Raises ValueError."""
    if not isinstance(pkg, dict):
        raise ValueError("package must be a dict")
    for name in ("package_id", "claim_id", "url", "captured_at",
                 "snapshot_path", "quote"):
        val = pkg.get(name)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"{name} must be a non-empty string")
    if not isinstance(pkg.get("sha256"), str) or \
            not SHA_RE.match(pkg["sha256"]):
        raise ValueError("sha256 must be a 64-char lowercase hex digest")
    off = pkg.get("offsets")
    if (not isinstance(off, (list, tuple)) or len(off) != 2
            or not all(isinstance(i, int) for i in off)
            or off[0] < 0 or off[1] <= off[0]):
        raise ValueError("offsets must be [start, end] with 0 <= start < end")
    if pkg.get("source_type") not in ("primary", "secondary"):
        raise ValueError("source_type must be 'primary' or 'secondary'")
    if pkg.get("extractor") not in ("model", "code"):
        raise ValueError("extractor must be 'model' or 'code'")
    for n in pkg.get("numbers", []):
        float(n["value"])
    sy = pkg.get("source_year")
    if sy is not None and not (isinstance(sy, int) and 1000 <= sy <= 2100):
        raise ValueError("source_year must be an int in [1000, 2100] or None")


# ---------------------------------------------------------------------------
# bundle loading + verification
# ---------------------------------------------------------------------------

def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _try_live_engine():
    """Import the REAL verifier when the repo is importable, else None."""
    here = Path(__file__).resolve()
    repo_guess = here.parents[2] if len(here.parents) > 2 else here.parent
    for candidate in (Path.cwd(), repo_guess):
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
        try:
            from live.evidence.package import (  # noqa: E402,I001
                EvidencePackage,
            )
            from live.verifier.check import (  # noqa: E402
                ClaimInput,
                verify_claim,
            )
            return EvidencePackage, ClaimInput, verify_claim
        except Exception:
            continue
    return None


def _runlog_verdicts(runlog_path):
    """Latest claim.verdict per claim_id from run.jsonl (or {})."""
    latest = {}
    try:
        with open(runlog_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and ev.get("type") == "claim.verdict" \
                        and ev.get("claim_id"):
                    latest[str(ev["claim_id"])] = {
                        "verdict": str(ev.get("verdict", "")),
                        "reasons": list(ev.get("reasons") or []),
                        "package_ids": list(ev.get("package_ids") or []),
                    }
    except OSError:
        pass
    return latest


def verify_bundle(bundle, engine="auto"):
    """Verify an M5 bundle (.zip or extracted dir). Returns a report dict.

    ``engine``: "auto" (live when importable, else stdlib mirror),
    "live" (fail when the repo is not importable), or "stdlib".
    """
    bundle = Path(bundle)
    if engine not in ("auto", "live", "stdlib"):
        raise ValueError("engine must be auto|live|stdlib")
    tmp = None
    if bundle.is_file() and zipfile.is_zipfile(bundle):
        tmp = tempfile.TemporaryDirectory(prefix="m5-verify-")
        root = Path(tmp.name)
        with zipfile.ZipFile(bundle) as zf:
            zf.extractall(root)
    elif bundle.is_dir():
        root = bundle
    else:
        raise FileNotFoundError(f"not a bundle zip or dir: {bundle}")
    try:
        return _verify_root(root, engine)
    finally:
        if tmp is not None:
            tmp.cleanup()


def _verify_root(root, engine):
    report = {"bundle": str(root), "engine": "", "files": {},
              "claims": [], "ok": False}
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        report["files"] = {"error": "manifest.json missing"}
        return report
    manifest = _load_json(manifest_path)
    files = manifest.get("files", {}) or {}
    bad, checked = [], 0
    for arcname, want in sorted(files.items()):
        p = root / arcname
        if not p.is_file():
            bad.append(f"{arcname}: missing")
            continue
        checked += 1
        if sha256_of_file(p) != want:
            bad.append(f"{arcname}: hash mismatch")
    report["files"] = {"checked": checked, "bad": bad,
                       "ok": not bad and checked > 0}
    if bad or not checked:
        return report

    claims_doc = _load_json(root / "claims.json")
    pkgs_doc = _load_json(root / "packages.json")
    claims = claims_doc.get("claims", [])
    packages = pkgs_doc.get("packages", [])
    by_claim = {}
    for pkg in packages:
        by_claim.setdefault(pkg.get("claim_id"), []).append(pkg)
    snapshots = {}  # sha256 -> decoded text
    for snap_file in sorted((root / "snapshots").glob("*")):
        if snap_file.is_file():
            try:
                text = snap_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = snap_file.read_text(encoding="utf-8",
                                           errors="replace")
            snapshots[sha256_of_text(text)] = text
            snapshots[snap_file.stem] = snapshots.get(snap_file.stem, text)
    runlog = _runlog_verdicts(root / "run.jsonl")

    live = _try_live_engine()
    if engine == "live" and live is None:
        raise RuntimeError("live engine requested but the repo with "
                           "live.verifier is not importable")
    use_live = live is not None and engine in ("auto", "live")
    report["engine"] = "live.verifier" if use_live else \
        f"stdlib-mirror ({MIRROR_OF})"

    for claim in claims:
        cid = str(claim.get("claim_id", ""))
        pkgs = by_claim.get(cid, [])
        entry = {"claim_id": cid,
                 "expected": str(claim.get("verdict", "")),
                 "got": "", "reasons": [], "packages": len(pkgs),
                 "status": "FAIL", "detail": ""}
        try:
            if use_live:
                EvidencePackage, ClaimInput, verify_claim = live
                objs = [EvidencePackage.from_dict(p) for p in pkgs]
                claim_in = ClaimInput(
                    claim_id=cid, text=str(claim.get("text", "") or ""),
                    value=float(claim.get("value", 0.0)),
                    unit=claim.get("unit"),
                    raw=str(claim.get("raw", "") or ""),
                    claim_year=claim.get("claim_year"),
                    tolerance=float(claim.get("tolerance",
                                              DEFAULT_REL_TOLERANCE)))

                def _loader(pkg, _snaps=snapshots):
                    text = _snaps.get(pkg.sha256)
                    if text is not None:
                        return text
                    cand = root / "snapshots" / Path(
                        pkg.snapshot_path).name
                    return cand.read_text(encoding="utf-8") \
                        if cand.is_file() else None

                got = verify_claim(claim_in, objs, _loader)
                entry["got"] = got.verdict
                entry["reasons"] = list(got.reasons)
            else:
                for p in pkgs:
                    _m_validate_package_dict(p)
                got = _m_verify_claim(claim, pkgs, snapshots)
                entry["got"] = got["verdict"]
                entry["reasons"] = list(got["reasons"])
        except Exception as exc:
            entry["detail"] = f"{type(exc).__name__}: {exc}"
            report["claims"].append(entry)
            continue
        recorded = runlog.get(cid, {}).get("verdict", "")
        if recorded and recorded != entry["got"]:
            entry["detail"] = (f"run.jsonl says {recorded}, "
                               f"re-run says {entry['got']}")
            report["claims"].append(entry)
            continue
        if entry["got"] == entry["expected"]:
            entry["status"] = "PASS"
        else:
            entry["detail"] = (f"recorded {entry['expected']}, "
                               f"re-run {entry['got']}")
        report["claims"].append(entry)
    report["ok"] = bool(report["claims"]) and all(
        c["status"] == "PASS" for c in report["claims"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="M5: offline re-verification of an Atlas Live bundle.")
    parser.add_argument("bundle", help=".zip bundle or extracted dir")
    parser.add_argument("--engine", default="auto",
                        choices=("auto", "live", "stdlib"))
    args = parser.parse_args(argv)
    try:
        report = verify_bundle(args.bundle, engine=args.engine)
    except Exception as exc:
        print(f"[m5-verify] ERROR: {type(exc).__name__}: {exc}")
        return 2
    files = report.get("files", {})
    if files.get("ok"):
        print(f"[m5-verify] files: {files['checked']}/{files['checked']} "
              f"hashes OK (engine={report['engine']})")
    else:
        print(f"[m5-verify] files: FAILED "
              f"({files.get('bad') or files.get('error')})")
        return 1
    for entry in report["claims"]:
        line = (f"[m5-verify] {entry['claim_id']}: "
                f"expected={entry['expected']} got={entry['got']} "
                f"pkgs={entry['packages']} -> {entry['status']}")
        if entry.get("detail"):
            line += f" ({entry['detail']})"
        print(line)
    print(f"[m5-verify] {'ALL PASS' if report['ok'] else 'FAILURES PRESENT'} "
          f"({sum(1 for c in report['claims'] if c['status'] == 'PASS')}/"
          f"{len(report['claims'])} claims)")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

