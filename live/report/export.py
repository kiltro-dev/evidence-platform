"""V5-G2 exportable report from a ``run_verify`` run.

Reads a run's bus log (``run.jsonl``) plus its sibling ``.summary.json``
and optional claims file, and produces:

- **HTML**: one self-contained file (inline CSS, no JS, no external
  refs) with the verdict table, evidence + receipts, Wilson panel and a
  Synthetic/Real source badge;
- **JSON**: the same content, machine-readable;
- **ZIP**: the existing M5 self-verifying bundle (reused, not
  reimplemented) via :func:`live.bundle.export.export_bundle`.

HTML/JSON rendering is stdlib-only (``string.Template`` + ``html``).
No new pip dependencies. PDF is NOT shipped — see :data:`PDF_DECISION`.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any

from live.auditor.wilson import summarize as wilson_summarize

SUPPORTED_FORMATS = ("html", "json", "zip")

PDF_DECISION = (
    "PDF not shipped. Constraints: reportlab explicitly forbidden, no new "
    "pip deps allowed (fpdf not installed), and no guaranteed headless "
    "browser in the environment (chromium-headless-print unavailable), so "
    "there is no dependency-free PDF writer. A weasyprint binary happens "
    "to exist on this dev machine, but it is not a declared project "
    "dependency and would make the export non-hermetic. Shipped formats "
    "are HTML (self-contained, printable to PDF from any browser) + JSON "
    "(machine) + ZIP (M5 offline-verifiable bundle)."
)

TOOL_NAME = "v5-report"
SOURCE_REAL = "real"
SOURCE_SYNTHETIC = "synthetic"

_VERDICTS = ("supported", "contradicted", "disputed", "unverifiable")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_source(source: str) -> str:
    norm = (source or "").strip().lower()
    if norm not in (SOURCE_REAL, SOURCE_SYNTHETIC):
        raise ValueError(
            f"source must be {SOURCE_REAL!r} or {SOURCE_SYNTHETIC!r}, "
            f"got {source!r}")
    return norm


# ---------------------------------------------------------------------------
# Load a run
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def _read_claims_file(claims_path: Path, lang: str) -> dict[str, dict]:
    """All ``lang`` rows keyed by id (any ``expected`` value)."""
    with open(claims_path, encoding="utf-8") as fh:
        rows = json.load(fh)
    out: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("lang") != lang:
            continue
        cid = str(row.get("id", "") or "")
        if cid and cid not in out:
            out[cid] = row
    return out


def load_run(run_log: str | Path,
             *,
             summary_path: str | Path | None = None,
             claims_path: str | Path | None = None,
             lang: str = "en") -> dict[str, Any]:
    """Assemble everything the exporters need from one verify run.

    Claim texts/``expected`` come from ``claims_path`` when given (file
    order wins); otherwise texts fall back to the ``escuchada`` notes in
    the log and ``expected`` is None. ``summary_path`` defaults to
    ``<run_log>.summary.json`` when that file exists.
    """
    run_log = Path(run_log)
    if not run_log.is_file():
        raise FileNotFoundError(f"run log not found: {run_log}")
    events = _read_jsonl(run_log)

    notes: dict[str, str] = {}
    evidence: dict[str, list[dict]] = {}
    verdicts: dict[str, dict] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        cid = str(ev.get("claim_id", "") or "")
        if not cid:
            continue
        if etype == "claim.state" and ev.get("state") == "escuchada":
            notes.setdefault(cid, str(ev.get("note", "") or ""))
        elif etype == "claim.evidence":
            evidence[cid] = [p for p in ev.get("packages", []) or []
                             if isinstance(p, dict)]
        elif etype == "claim.verdict":
            verdicts[cid] = ev  # latest wins (bus replay contract)

    file_rows = _read_claims_file(Path(claims_path), lang) \
        if claims_path is not None else {}

    order: list[str] = list(file_rows) + [
        cid for cid in list(notes) + list(verdicts)
        if cid not in file_rows]
    # de-dup, keep first-seen order
    order = list(dict.fromkeys(order))

    summary: dict[str, Any] = {}
    spath = (Path(summary_path) if summary_path is not None
             else Path(str(run_log) + ".summary.json"))
    if spath.is_file():
        with open(spath, encoding="utf-8") as fh:
            summary = json.load(fh)
    lat_by_claim = {str(r.get("claim_id")): r.get("latency_s")
                    for r in summary.get("per_claim", [])
                    if isinstance(r, dict)}

    claims: list[dict[str, Any]] = []
    for cid in order:
        row = file_rows.get(cid, {})
        text = str(row.get("text", "") or notes.get(cid, "") or "")
        exp_raw = row.get("expected")
        expected = exp_raw if exp_raw in ("true", "false") else None
        ver = verdicts.get(cid, {})
        verdict = str(ver.get("verdict", "") or "")
        if verdict not in _VERDICTS:
            verdict = "unverifiable"
        reasons = [str(r) for r in ver.get("reasons", []) or []]
        pkgs = evidence.get(cid, [])
        claims.append({
            "claim_id": cid,
            "text": text,
            "expected": expected,
            "expected_raw": exp_raw,
            "verdict": verdict,
            "reasons": reasons,
            "package_ids": [str(p.get("package_id", ""))
                            for p in pkgs],
            "latency_s": lat_by_claim.get(cid),
            "packages": pkgs,
        })

    exp_map = {c["claim_id"]: c["expected"] for c in claims
               if c["expected"] in ("true", "false")}
    ver_map = {c["claim_id"]: c["verdict"] for c in claims}
    wilson = wilson_summarize(exp_map, ver_map)

    return {
        "tool": TOOL_NAME,
        "run_log": str(run_log),
        "summary_path": str(spath) if spath.is_file() else None,
        "claims_path": str(claims_path) if claims_path else None,
        "lang": lang,
        "n_claims": len(claims),
        "claims": claims,
        "summary": summary,
        "wilson": wilson,
    }


# ---------------------------------------------------------------------------
# JSON export (machine-readable)
# ---------------------------------------------------------------------------

def _json_claim(c: dict[str, Any]) -> dict[str, Any]:
    pkgs = []
    for p in c.get("packages", []) or []:
        pkgs.append({
            "package_id": p.get("package_id"),
            "url": p.get("url"),
            "source_type": p.get("source_type"),
            "sha256": p.get("sha256"),
            "snapshot_path": p.get("snapshot_path"),
            "quote": p.get("quote"),
            "source_year": p.get("source_year"),
            "extractor": p.get("extractor"),
        })
    return {
        "claim_id": c["claim_id"],
        "text": c["text"],
        "expected": c["expected"],
        "verdict": c["verdict"],
        "reasons": list(c.get("reasons", [])),
        "package_ids": list(c.get("package_ids", [])),
        "latency_s": c.get("latency_s"),
        "packages": pkgs,
    }


def export_json(report: dict[str, Any],
                out_path: str | Path,
                *,
                source: str = SOURCE_SYNTHETIC,
                provenance: dict[str, Any] | None = None,
                title: str = "Atlas Live verification report",
                created_at: str | None = None) -> dict[str, Any]:
    """Write the machine-readable JSON report. Returns the payload."""
    norm_source = _check_source(source)
    payload = {
        "tool": TOOL_NAME,
        "title": title,
        "source": norm_source,
        "created_at": created_at or _utcnow_iso(),
        "run_log": report.get("run_log"),
        "lang": report.get("lang"),
        "n_claims": report.get("n_claims"),
        "provenance": provenance or {},
        "wilson": report.get("wilson", {}),
        "totals": {
            "cost_usd": (report.get("summary", {}) or {}).get(
                "total_cost_usd"),
            "usage": (report.get("summary", {}) or {}).get("total_usage"),
            "latency_s": (report.get("summary", {}) or {}).get(
                "latency_s"),
            "wall_s": (report.get("summary", {}) or {}).get("wall_s"),
        },
        "claims": [_json_claim(c) for c in report.get("claims", [])],
    }
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1)
                        + "\n", encoding="utf-8")
    return payload


# ---------------------------------------------------------------------------
# HTML export (self-contained: inline CSS, no JS, no external refs)
# ---------------------------------------------------------------------------

_CSS = """
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
margin:2em auto;max-width:60em;padding:0 1em;color:#1a1a1a;line-height:1.45}
.badge{display:inline-block;font-weight:700;font-size:.8em;letter-spacing:.08em;
padding:.25em .8em;border-radius:1em;color:#fff}
.badge.real{background:#1a7f37}.badge.synthetic{background:#9a6700}
table{border-collapse:collapse;width:100%;margin:1em 0;font-size:.92em}
th,td{border:1px solid #ccc;padding:.4em .6em;text-align:left;vertical-align:top}
th{background:#f0f0f0}
.chip{display:inline-block;font-weight:700;font-size:.82em;color:#fff;
padding:.15em .7em;border-radius:1em;white-space:nowrap}
.chip.supported{background:#1a7f37}.chip.contradicted{background:#cf222e}
.chip.disputed{background:#b25f09}.chip.unverifiable{background:#6e7781}
code.q{background:#f6f8fa;padding:.1em .3em;border-radius:.3em}
.panel{background:#f6f8fa;border:1px solid #ddd;border-radius:.5em;
padding:.8em 1em;margin:1em 0}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.85em}
details.claim{border:1px solid #ddd;border-radius:.5em;margin:.7em 0;
padding:.5em .9em}summary{font-weight:600;cursor:pointer}
.receipt{color:#444}
footer{margin-top:2em;color:#666;font-size:.85em;border-top:1px solid #ddd;
padding-top:.7em}
"""

_HTML = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title_esc</title>
<style>$css</style>
</head>
<body>
<p><span class="badge $source">$badge_label</span></p>
<h1>$title_esc</h1>
<p class="mono">run: $run_log_esc &middot; lang: $lang_esc &middot;
exported: $created_at_esc &middot; tool: $tool_esc</p>
<div class="panel">
<h2>Accuracy (decided + 95% Wilson)</h2>
<p>$wilson_line</p>
<p class="receipt">$cost_line</p>
</div>
<h2>Verdicts</h2>
<table>
<tr><th>claim</th><th>expected</th><th>verdict</th><th>reasons</th>
<th>evidence</th></tr>
$verdict_rows
</table>
<h2>Evidence &amp; receipts</h2>
$claim_details
<h2>Provenance</h2>
$provenance_block
<footer>Atlas Live $tool_esc &middot; formats: HTML + JSON + ZIP (M5 bundle)
&middot; verify offline: <span class="mono">python verify_bundle.py
bundle.zip --engine stdlib</span></footer>
</body>
</html>
""")


def _badge(source: str) -> tuple[str, str]:
    if source == SOURCE_REAL:
        return ("real", "REAL SOURCE")
    return ("synthetic", "SYNTHETIC")


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100.0 * value:.1f}%"


def _wilson_line(wilson: dict[str, Any]) -> str:
    lo = wilson.get("wilson_lo", 0.0)
    hi = wilson.get("wilson_hi", 1.0)
    return (f"decided {wilson.get('decided', 0)}/"
            f"{wilson.get('n', 0)} &middot; correct "
            f"{wilson.get('correct', 0)} &middot; accuracy "
            f"{_fmt_pct(wilson.get('accuracy'))} &middot; 95% Wilson "
            f"[{100.0 * lo:.1f}%, {100.0 * hi:.1f}%] &middot; abstention "
            f"{_fmt_pct(wilson.get('abstention_rate'))} &middot; disputed "
            f"{wilson.get('n_disputed', 0)}")


def _cost_line(report: dict[str, Any]) -> str:
    summary = report.get("summary", {}) or {}
    cost = summary.get("total_cost_usd")
    cost_s = "n/a" if cost is None else f"${cost:.4f}"
    lat = (summary.get("latency_s", {}) or {})
    p50 = lat.get("p50")
    p95 = lat.get("p95")
    wall = summary.get("wall_s")
    return (f"cost {html.escape(str(cost_s))} &middot; wall "
            f"{html.escape(str(wall))}s &middot; latency p50 "
            f"{html.escape(str(p50))}s / p95 "
            f"{html.escape(str(p95))}s")


def _verdict_rows(report: dict[str, Any]) -> str:
    rows = []
    for c in report.get("claims", []):
        verdict = c.get("verdict", "unverifiable")
        reasons = ", ".join(c.get("reasons", []))
        n_pkgs = len(c.get("packages", []) or [])
        rows.append(
            "<tr><td><span class=\"mono\">" + html.escape(c["claim_id"])
            + "</span><br>" + html.escape(c.get("text", "")) + "</td><td>"
            + html.escape(str(c.get("expected")))
            + "</td><td><span class=\"chip " + html.escape(verdict) + "\">"
            + html.escape(verdict) + "</span></td><td><code class=\"q\">"
            + html.escape(reasons) + "</code></td><td>" + str(n_pkgs)
            + " pkg(s)</td></tr>")
    return "\n".join(rows)


def _claim_details(report: dict[str, Any]) -> str:
    blocks = []
    for c in report.get("claims", []):
        verdict = c.get("verdict", "unverifiable")
        pkgs = c.get("packages", []) or []
        if pkgs:
            prow = []
            for p in pkgs:
                sha = str(p.get("sha256", "") or "")
                prow.append(
                    "<tr><td><span class=\"mono\">"
                    + html.escape(str(p.get("package_id", "")))
                    + "</span></td><td>"
                    + html.escape(str(p.get("source_type", "")))
                    + "</td><td><span class=\"mono\">"
                    + html.escape(str(p.get("url", "")))
                    + "</span></td><td><span class=\"mono\">"
                    + html.escape(sha[:12])
                    + "</span></td><td>"
                    + html.escape(str(p.get("source_year")))
                    + "</td><td><code class=\"q\">"
                    + html.escape(str(p.get("quote", ""))[:600])
                    + "</code></td></tr>")
            ev_table = ("<table><tr><th>package</th><th>type</th>"
                        "<th>url</th><th>sha12</th><th>year</th>"
                        "<th>quote</th></tr>" + "".join(prow)
                        + "</table>")
        else:
            ev_table = "<p><em>No evidence packages.</em></p>"
        receipt = ("receipt: verdict=" + verdict + "; reasons="
                   + ",".join(c.get("reasons", [])) + "; packages="
                   + ",".join(c.get("package_ids", [])))
        blocks.append(
            "<details class=\"claim\"><summary><span class=\"mono\">"
            + html.escape(c["claim_id"]) + "</span> — " + html.escape(verdict)
            + "</summary><p>" + html.escape(c.get("text", "")) + "</p>"
            + ev_table + "<p class=\"receipt mono\">"
            + html.escape(receipt) + "</p></details>")
    return "\n".join(blocks) if blocks else "<p><em>No claims.</em></p>"


def _provenance_block(provenance: dict[str, Any] | None) -> str:
    if not provenance:
        return "<p><em>No provenance recorded.</em></p>"
    rows = []
    for key in sorted(provenance):
        val = provenance[key]
        if isinstance(val, list):
            cell = "<br>".join(html.escape(str(v)) for v in val)
        else:
            cell = html.escape(str(val))
        rows.append("<tr><td><span class=\"mono\">" + html.escape(str(key))
                    + "</span></td><td>" + cell + "</td></tr>")
    return ("<table><tr><th>field</th><th>value</th></tr>"
            + "".join(rows) + "</table>")


def export_html(report: dict[str, Any],
                out_path: str | Path,
                *,
                source: str = SOURCE_SYNTHETIC,
                provenance: dict[str, Any] | None = None,
                title: str = "Atlas Live verification report",
                created_at: str | None = None) -> Path:
    """Write the self-contained HTML report. Returns the path."""
    norm_source = _check_source(source)
    css_class, badge_label = _badge(norm_source)
    page = _HTML.substitute(
        title_esc=html.escape(title),
        css=_CSS,
        source=css_class,
        badge_label=badge_label,
        run_log_esc=html.escape(str(report.get("run_log", ""))),
        lang_esc=html.escape(str(report.get("lang", ""))),
        created_at_esc=html.escape(created_at or _utcnow_iso()),
        tool_esc=html.escape(str(report.get("tool", TOOL_NAME))),
        wilson_line=_wilson_line(report.get("wilson", {}) or {}),
        cost_line=_cost_line(report),
        verdict_rows=_verdict_rows(report),
        claim_details=_claim_details(report),
        provenance_block=_provenance_block(provenance),
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# ZIP reuse (M5 bundle) + export_all
# ---------------------------------------------------------------------------

def export_bundle_for_run(report: dict[str, Any],
                          out_path: str | Path,
                          *,
                          claim_rows: list[dict] | None = None,
                          claims_path: str | Path | None = None,
                          lang: str = "en",
                          repo_root: str | Path | None = None,
                          snapshot_dir: str | Path | None = None,
                          truth_ref: str | Path | None = None,
                          ) -> dict[str, Any]:
    """Pack this run as an M5 self-verifying ZIP (reuses bundle export).

    Needs claim numerics for offline re-verification: pass explicit
    ``claim_rows`` (``claim_id``/``value``/``verdict`` + ``unit``/``raw``
    /``claim_year``/``tolerance`` as available) or a ``claims_path``
    truth file whose rows are parsed with ``run_verify.claim_input_for``.
    """
    from live.bundle.export import export_bundle
    from live.run_verify import claim_input_for, load_entries

    rows: list[dict] = []
    if claim_rows is not None:
        rows = [dict(r) for r in claim_rows]
    elif claims_path is not None:
        entries = {e["id"]: e for e in
                   load_entries(claims_path, lang, None)}
        # load_entries filters expected true/false; re-add the rest so
        # every verdict in the report gets a row.
        with open(claims_path, encoding="utf-8") as fh:
            for row in json.load(fh):
                if (isinstance(row, dict) and row.get("lang") == lang
                        and str(row.get("id", "")) not in entries):
                    entries[str(row["id"])] = row
        for c in report.get("claims", []):
            entry = entries.get(c["claim_id"])
            if entry is None:
                raise ValueError(
                    f"no claims-file row for {c['claim_id']!r}")
            ci = claim_input_for(entry)
            rows.append({
                "claim_id": ci.claim_id, "text": entry.get("text", ""),
                "value": ci.value, "unit": ci.unit, "raw": ci.raw,
                "claim_year": ci.claim_year, "tolerance": ci.tolerance,
                "verdict": c["verdict"],
                "expected": c.get("expected"),
            })
    else:
        raise ValueError("export_bundle_for_run needs claim_rows or "
                         "claims_path (claim numerics are not in run.jsonl)")

    packages: list[dict] = []
    for c in report.get("claims", []):
        packages.extend(c.get("packages", []) or [])
    return export_bundle(
        run_log=report["run_log"], packages=packages, claims=rows,
        out_path=out_path, truth_ref=truth_ref, repo_root=repo_root,
        snapshot_dir=snapshot_dir)


def export_all(run_log: str | Path,
               out_dir: str | Path,
               basename: str = "report",
               *,
               source: str = SOURCE_SYNTHETIC,
               provenance: dict[str, Any] | None = None,
               title: str = "Atlas Live verification report",
               claims_path: str | Path | None = None,
               lang: str = "en",
               with_bundle: bool = True,
               created_at: str | None = None) -> dict[str, str]:
    """HTML + JSON (+ ZIP bundle) for one run. Returns format -> path."""
    report = load_run(run_log, claims_path=claims_path, lang=lang)
    out_dir = Path(out_dir)
    html_path = export_html(report, out_dir / f"{basename}.html",
                            source=source, provenance=provenance,
                            title=title, created_at=created_at)
    json_path = out_dir / f"{basename}.json"
    export_json(report, json_path, source=source, provenance=provenance,
                title=title, created_at=created_at)
    paths = {"html": str(html_path), "json": str(json_path)}
    if with_bundle:
        if claims_path is None:
            raise ValueError("with_bundle needs claims_path")
        manifest = export_bundle_for_run(
            report, out_dir / f"{basename}_bundle.zip",
            claims_path=claims_path, lang=lang)
        paths["zip"] = str(manifest["bundle"])
    return paths


__all__ = [
    "PDF_DECISION",
    "SUPPORTED_FORMATS",
    "export_all",
    "export_bundle_for_run",
    "export_html",
    "export_json",
    "load_run",
]
