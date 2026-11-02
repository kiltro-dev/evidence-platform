"""V5-G2 exportable reports: HTML/JSON goldens + FOMC case. Offline ($0).

Hermetic fixture goldens (HTML + JSON, tmp-path normalized), badge +
escaping + PDF-decision unit tests, FOMC provenance completeness, and
offline bundle verification: a hermetic fixture roundtrip plus the real
FOMC bundle when present (data/fomc/ is git-ignored, so that leg skips
on a fresh checkout).
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.bundle.verify_bundle import verify_bundle  # noqa: E402
from live.evidence.package import sha256_of_text  # noqa: E402
from live.report.export import (  # noqa: E402
    PDF_DECISION,
    SUPPORTED_FORMATS,
    export_bundle_for_run,
    export_html,
    export_json,
    load_run,
)
from live.swarm.coordinator import numbers_from_quote  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
FOMC_DIR = os.path.join(REPO_ROOT, "data", "fomc")

SNAP1 = ("Testland census: Testland is home to 10 million people "
         "in 2024, officials said.")
QUOTE1 = "home to 10 million people"
SHA1 = sha256_of_text(SNAP1)
START1 = SNAP1.find(QUOTE1)
C1 = "Testland is home to 10 million people in 2024."
C2 = "Testland is home to 99 million people in 2024."
CREATED_AT = "2026-11-02T00:00:00+00:00"
PROV = {"source_name": "fixture",
        "urls": ["https://example.com/census"],
        "accessed": "2026-11-02", "notes": ["offline fixture"]}

GOLDEN_JSON = """{
 "tool": "v5-report",
 "title": "V5-REPORT fixture",
 "source": "synthetic",
 "created_at": "2026-11-02T00:00:00+00:00",
 "run_log": "<TMP>/run.jsonl",
 "lang": "en",
 "n_claims": 2,
 "provenance": {
  "source_name": "fixture",
  "urls": [
   "https://example.com/census"
  ],
  "accessed": "2026-11-02",
  "notes": [
   "offline fixture"
  ]
 },
 "wilson": {
  "n": 2,
  "decided": 2,
  "correct": 2,
  "accuracy": 1.0,
  "wilson_lo": 0.3424,
  "wilson_hi": 1.0,
  "abstention_rate": 0.0,
  "falsos_contradicted": [],
  "falsos_supported": [],
  "n_disputed": 0,
  "per_canary": [
   {
    "canary_id": "R1",
    "expected": "true",
    "verdict": "supported",
    "decided": true,
    "correct": true
   },
   {
    "canary_id": "R2",
    "expected": "false",
    "verdict": "contradicted",
    "decided": true,
    "correct": true
   }
  ]
 },
 "totals": {
  "cost_usd": 0.0123,
  "usage": {
   "prompt_tokens": 100,
   "completion_tokens": 20,
   "model_calls": 2
  },
  "latency_s": {
   "count": 2,
   "p50": 5.0,
   "p95": 6.0
  },
  "wall_s": 12.5
 },
 "claims": [
  {
   "claim_id": "R1",
   "text": "Testland is home to 10 million people in 2024.",
   "expected": "true",
   "verdict": "supported",
   "reasons": [
    "numeric_match"
   ],
   "package_ids": [
    "pkg-R1-0"
   ],
   "latency_s": 5.0,
   "packages": [
    {
     "package_id": "pkg-R1-0",
     "url": "https://example.com/census",
     "source_type": "secondary",
     "sha256": "5712896e1a2cafdbe1f009d9e56fce0a17d25c6b0f7f052d2b8e6bd9214659c9",
     "snapshot_path": "data/snap/5712896e1a2cafdbe1f009d9e56fce0a17d25c6b0f7f052d2b8e6bd9214659c9.html",
     "quote": "home to 10 million people",
     "source_year": 2024,
     "extractor": "code"
    }
   ]
  },
  {
   "claim_id": "R2",
   "text": "Testland is home to 99 million people in 2024.",
   "expected": "false",
   "verdict": "contradicted",
   "reasons": [
    "numeric_mismatch"
   ],
   "package_ids": [
    "pkg-R2-0"
   ],
   "latency_s": 6.0,
   "packages": [
    {
     "package_id": "pkg-R2-0",
     "url": "https://example.com/census",
     "source_type": "secondary",
     "sha256": "5712896e1a2cafdbe1f009d9e56fce0a17d25c6b0f7f052d2b8e6bd9214659c9",
     "snapshot_path": "data/snap/5712896e1a2cafdbe1f009d9e56fce0a17d25c6b0f7f052d2b8e6bd9214659c9.html",
     "quote": "home to 10 million people",
     "source_year": 2024,
     "extractor": "code"
    }
   ]
  }
 ]
}
"""

GOLDEN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>V5-REPORT fixture</title>
<style>
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
</style>
</head>
<body>
<p><span class="badge synthetic">SYNTHETIC</span></p>
<h1>V5-REPORT fixture</h1>
<p class="mono">run: <TMP>/run.jsonl &middot; lang: en &middot;
exported: 2026-11-02T00:00:00+00:00 &middot; tool: v5-report</p>
<div class="panel">
<h2>Accuracy (decided + 95% Wilson)</h2>
<p>decided 2/2 &middot; correct 2 &middot; accuracy 100.0% &middot; 95% Wilson [34.2%, 100.0%] &middot; abstention 0.0% &middot; disputed 0</p>
<p class="receipt">cost $0.0123 &middot; wall 12.5s &middot; latency p50 5.0s / p95 6.0s</p>
</div>
<h2>Verdicts</h2>
<table>
<tr><th>claim</th><th>expected</th><th>verdict</th><th>reasons</th>
<th>evidence</th></tr>
<tr><td><span class="mono">R1</span><br>Testland is home to 10 million people in 2024.</td><td>true</td><td><span class="chip supported">supported</span></td><td><code class="q">numeric_match</code></td><td>1 pkg(s)</td></tr>
<tr><td><span class="mono">R2</span><br>Testland is home to 99 million people in 2024.</td><td>false</td><td><span class="chip contradicted">contradicted</span></td><td><code class="q">numeric_mismatch</code></td><td>1 pkg(s)</td></tr>
</table>
<h2>Evidence &amp; receipts</h2>
<details class="claim"><summary><span class="mono">R1</span> — supported</summary><p>Testland is home to 10 million people in 2024.</p><table><tr><th>package</th><th>type</th><th>url</th><th>sha12</th><th>year</th><th>quote</th></tr><tr><td><span class="mono">pkg-R1-0</span></td><td>secondary</td><td><span class="mono">https://example.com/census</span></td><td><span class="mono">5712896e1a2c</span></td><td>2024</td><td><code class="q">home to 10 million people</code></td></tr></table><p class="receipt mono">receipt: verdict=supported; reasons=numeric_match; packages=pkg-R1-0</p></details>
<details class="claim"><summary><span class="mono">R2</span> — contradicted</summary><p>Testland is home to 99 million people in 2024.</p><table><tr><th>package</th><th>type</th><th>url</th><th>sha12</th><th>year</th><th>quote</th></tr><tr><td><span class="mono">pkg-R2-0</span></td><td>secondary</td><td><span class="mono">https://example.com/census</span></td><td><span class="mono">5712896e1a2c</span></td><td>2024</td><td><code class="q">home to 10 million people</code></td></tr></table><p class="receipt mono">receipt: verdict=contradicted; reasons=numeric_mismatch; packages=pkg-R2-0</p></details>
<h2>Provenance</h2>
<table><tr><th>field</th><th>value</th></tr><tr><td><span class="mono">accessed</span></td><td>2026-11-02</td></tr><tr><td><span class="mono">notes</span></td><td>offline fixture</td></tr><tr><td><span class="mono">source_name</span></td><td>fixture</td></tr><tr><td><span class="mono">urls</span></td><td>https://example.com/census</td></tr></table>
<footer>Atlas Live v5-report &middot; formats: HTML + JSON + ZIP (M5 bundle)
&middot; verify offline: <span class="mono">python verify_bundle.py
bundle.zip --engine stdlib</span></footer>
</body>
</html>
"""

FOMC_STATEMENT_URL = ("https://www.federalreserve.gov/newsevents/"
                      "pressreleases/monetary20261028a.htm")
FOMC_IMPLNOTE_URL = ("https://www.federalreserve.gov/newsevents/"
                     "pressreleases/monetary20261028a1.htm")
FOMC_STATEMENT_SHA = ("e88e59613b54da64e82fdc05a8193cc1a17ce17c9dd319"
                      "593002d87c5241439c")
FOMC_IMPLNOTE_SHA = ("cdb328488e81c0ac680a10430ae82ec7b6113520833ac79"
                     "048a3d749a39b8354")


def _pkg(pid, cid):
    assert START1 >= 0
    return {
        "package_id": pid, "claim_id": cid,
        "url": "https://example.com/census",
        "captured_at": CREATED_AT,
        "sha256": SHA1, "snapshot_path": f"data/snap/{SHA1}.html",
        "quote": QUOTE1, "offsets": [START1, START1 + len(QUOTE1)],
        "source_type": "secondary",
        "numbers": numbers_from_quote(QUOTE1),
        "extractor": "code", "source_year": 2024,
    }


def _fixture_run(tmp_path):
    claims = [
        {"id": "R1", "lang": "en", "text": C1, "expected": "true"},
        {"id": "R2", "lang": "en", "text": C2, "expected": "false"},
    ]
    cpath = tmp_path / "claims.json"
    cpath.write_text(json.dumps(claims), encoding="utf-8")
    events = [
        {"seq": 1, "bus_ts": 1.0, "type": "claim.state", "claim_id": "R1",
         "state": "escuchada", "note": C1},
        {"seq": 2, "bus_ts": 2.0, "type": "claim.state", "claim_id": "R2",
         "state": "escuchada", "note": C2},
        {"seq": 3, "bus_ts": 3.0, "type": "claim.evidence", "claim_id": "R1",
         "packages": [_pkg("pkg-R1-0", "R1")]},
        {"seq": 4, "bus_ts": 4.0, "type": "claim.evidence", "claim_id": "R2",
         "packages": [_pkg("pkg-R2-0", "R2")]},
        {"seq": 5, "bus_ts": 5.0, "type": "claim.verdict", "claim_id": "R1",
         "verdict": "supported", "reasons": ["numeric_match"],
         "package_ids": ["pkg-R1-0"]},
        {"seq": 6, "bus_ts": 6.0, "type": "claim.verdict", "claim_id": "R2",
         "verdict": "contradicted", "reasons": ["numeric_mismatch"],
         "package_ids": ["pkg-R2-0"]},
    ]
    log = tmp_path / "run.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n",
                   encoding="utf-8")
    summary = {
        "total_cost_usd": 0.0123,
        "total_usage": {"prompt_tokens": 100, "completion_tokens": 20,
                        "model_calls": 2},
        "latency_s": {"count": 2, "p50": 5.0, "p95": 6.0},
        "wall_s": 12.5,
        "per_claim": [{"claim_id": "R1", "latency_s": 5.0},
                      {"claim_id": "R2", "latency_s": 6.0}],
    }
    (tmp_path / "run.jsonl.summary.json").write_text(
        json.dumps(summary), encoding="utf-8")
    return log, cpath


def _norm(text, tmp_path):
    return text.replace(str(tmp_path), "<TMP>")


# ---------------------------------------------------------------------------
# goldens
# ---------------------------------------------------------------------------

def test_json_golden(tmp_path):
    log, cpath = _fixture_run(tmp_path)
    report = load_run(log, claims_path=cpath, lang="en")
    out = tmp_path / "r.json"
    payload = export_json(report, out, source="synthetic", provenance=PROV,
                          title="V5-REPORT fixture", created_at=CREATED_AT)
    assert payload["source"] == "synthetic"
    assert payload["wilson"]["decided"] == 2
    assert payload["wilson"]["correct"] == 2
    assert _norm(out.read_text(encoding="utf-8"), tmp_path) == GOLDEN_JSON


def test_html_golden(tmp_path):
    log, cpath = _fixture_run(tmp_path)
    report = load_run(log, claims_path=cpath, lang="en")
    out = export_html(report, tmp_path / "r.html", source="synthetic",
                      provenance=PROV, title="V5-REPORT fixture",
                      created_at=CREATED_AT)
    text = out.read_text(encoding="utf-8")
    assert '<span class="badge synthetic">SYNTHETIC</span>' in text
    assert "95% Wilson [34.2%, 100.0%]" in text
    assert "receipt: verdict=supported" in text
    assert "receipt: verdict=contradicted" in text
    assert _norm(text, tmp_path) == GOLDEN_HTML


def test_html_self_contained_no_external_refs(tmp_path):
    log, cpath = _fixture_run(tmp_path)
    report = load_run(log, claims_path=cpath, lang="en")
    out = export_html(report, tmp_path / "r.html", source="synthetic",
                      created_at=CREATED_AT)
    text = out.read_text(encoding="utf-8").lower()
    assert "<script" not in text
    assert "<link" not in text
    assert "@import" not in text


# ---------------------------------------------------------------------------
# badge / escaping / formats
# ---------------------------------------------------------------------------

def test_source_badge_real_vs_synthetic(tmp_path):
    log, cpath = _fixture_run(tmp_path)
    report = load_run(log, claims_path=cpath, lang="en")
    real = export_html(report, tmp_path / "real.html", source="real",
                       created_at=CREATED_AT).read_text(encoding="utf-8")
    assert '<span class="badge real">REAL SOURCE</span>' in real
    with pytest.raises(ValueError):
        export_html(report, tmp_path / "x.html", source="maybe")
    with pytest.raises(ValueError):
        export_json(report, tmp_path / "x.json", source="")


def test_html_escapes_claim_text(tmp_path):
    evil = 'X <script>alert("q")</script> & "quotes" in 2024.'
    cpath = tmp_path / "claims.json"
    cpath.write_text(json.dumps(
        [{"id": "E1", "lang": "en", "text": evil,
          "expected": "unverifiable"}]), encoding="utf-8")
    log = tmp_path / "run.jsonl"
    log.write_text(json.dumps(
        {"seq": 1, "bus_ts": 1.0, "type": "claim.verdict",
         "claim_id": "E1", "verdict": "unverifiable",
         "reasons": ["no_usable_package"], "package_ids": []}) + "\n",
        encoding="utf-8")
    report = load_run(log, claims_path=cpath, lang="en")
    text = export_html(report, tmp_path / "e.html",
                       created_at=CREATED_AT).read_text(encoding="utf-8")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&amp;" in text


def test_supported_formats_and_pdf_decision():
    assert SUPPORTED_FORMATS == ("html", "json", "zip")
    assert "reportlab" in PDF_DECISION
    assert "fpdf" in PDF_DECISION
    assert "chromium" in PDF_DECISION.lower()


def test_load_run_without_claims_file_falls_back_to_notes(tmp_path):
    log, _ = _fixture_run(tmp_path)
    report = load_run(log)  # no claims_path, no lang rows
    assert report["n_claims"] == 2
    assert report["claims"][0]["text"] == C1
    assert report["claims"][0]["expected"] is None
    assert report["wilson"]["n"] == 0


def test_export_bundle_needs_claim_numerics(tmp_path):
    log, _ = _fixture_run(tmp_path)
    report = load_run(log)
    with pytest.raises(ValueError):
        export_bundle_for_run(report, tmp_path / "b.zip")


# ---------------------------------------------------------------------------
# offline bundle roundtrip (hermetic fixture)
# ---------------------------------------------------------------------------

def test_fixture_bundle_roundtrip_offline_both_engines(tmp_path):
    log, _ = _fixture_run(tmp_path)
    snap_dir = tmp_path / "data" / "snap"
    snap_dir.mkdir(parents=True)
    (snap_dir / f"{SHA1}.html").write_text(SNAP1, encoding="utf-8")
    report = load_run(log)
    rows = [
        {"claim_id": "R1", "text": C1, "value": 10e6, "unit": "people",
         "raw": "10 million", "claim_year": 2024, "tolerance": 0.02,
         "verdict": "supported"},
        {"claim_id": "R2", "text": C2, "value": 99e6, "unit": "people",
         "raw": "99 million", "claim_year": 2024, "tolerance": 0.02,
         "verdict": "contradicted"},
    ]
    manifest = export_bundle_for_run(
        report, tmp_path / "r_bundle.zip", claim_rows=rows,
        repo_root=tmp_path)
    assert (tmp_path / "r_bundle.zip").is_file()
    assert manifest["claims"] == ["R1", "R2"]
    for engine in ("stdlib", "live"):
        result = verify_bundle(tmp_path / "r_bundle.zip", engine=engine)
        assert result["files"]["ok"], (engine, result["files"])
        assert result["ok"], (engine, result["claims"])
        assert [c["status"] for c in result["claims"]] == ["PASS", "PASS"]


# ---------------------------------------------------------------------------
# FOMC real case (versioned provenance + ignored artifacts)
# ---------------------------------------------------------------------------

def test_provenance_fomc_complete():
    prov = os.path.join(REPO_ROOT, "live", "material", "PROVENANCE_FOMC.md")
    assert os.path.isfile(prov), "live/material/PROVENANCE_FOMC.md missing"
    text = open(prov, encoding="utf-8").read()
    for needle in (FOMC_STATEMENT_URL, FOMC_IMPLNOTE_URL,
                   FOMC_STATEMENT_SHA, FOMC_IMPLNOTE_SHA,
                   "2026-11-02", "SEP", "robots",
                   "3-3/4 to 4 percent", "10", "2 percent",
                   "3.90 percent", "$160 billion", "4.0 percent"):
        assert needle in text, f"provenance missing {needle!r}"
    # When the (git-ignored) downloads exist, hashes must match exactly.
    import hashlib

    def _sha(path):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    stmt = os.path.join(FOMC_DIR, "statement_20261028.htm")
    note = os.path.join(FOMC_DIR, "implnote_20261028.htm")
    if os.path.isfile(stmt):
        assert _sha(stmt) == FOMC_STATEMENT_SHA
    if os.path.isfile(note):
        assert _sha(note) == FOMC_IMPLNOTE_SHA


def test_fomc_bundle_verifies_offline():
    bundle = os.path.join(FOMC_DIR, "fomc_bundle.zip")
    if not os.path.isfile(bundle):
        pytest.skip("data/fomc/ artifacts not generated (git-ignored)")
    for engine in ("stdlib", "live"):
        result = verify_bundle(bundle, engine=engine)
        assert result["files"]["ok"], (engine, result["files"])
        assert result["ok"], (engine, result["claims"])
    assert os.path.isfile(os.path.join(FOMC_DIR, "fomc.html"))
    assert os.path.isfile(os.path.join(FOMC_DIR, "fomc.json"))
    payload = json.load(open(os.path.join(FOMC_DIR, "fomc.json"),
                             encoding="utf-8"))
    assert payload["source"] == "real"
    assert payload["n_claims"] == 5
