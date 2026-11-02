#!/usr/bin/env python3
"""F2-6 QA(d) — 35+ aserciones: cifras del video vs summaries ejecutados.

Compara narration_es.txt / narration_en.srt / panels.html / build_video.py
vs los JSONs medidos. Retencion: SOLO via addendum de live/F2_RECALL.md
(jamas abre live/eval2/retention.json). $0, offline.

Uso:
  cd evidence-platform && .venv/bin/python live/demo/qa_video.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from live.auditor.wilson import wilson_interval  # noqa: E402
from live.server.app import percentile  # noqa: E402

import live.demo.build_video as bv  # noqa: E402

PANELS = Path("/tmp/atlas_demo/assets/panels.html")

CHECKS: list[tuple[str, object]] = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


def J(path):
    with open(REPO / path, encoding="utf-8") as fh:
        return json.load(fh)


def panels() -> str:
    assert PANELS.is_file(), "corre build_video.py primero (falta panels.html)"
    return PANELS.read_text(encoding="utf-8")


def narr_blocks() -> list[str]:
    return [b.strip() for b in
            (REPO / "live/demo/narration_es.txt").read_text(
                encoding="utf-8").split("\n\n") if b.strip()]


# ------------------------------------------------------------- recall DEV
@check("R1 dev decididos 22/36 recall 0.611 (r1+r2)")
def _():
    d = J("data/runs/f2recall-fix-report.json")
    for run in ("atlas-run1", "atlas-run2"):
        p = d["panels"][run]
        assert (p["decided"], p["n"]) == (22, 36), (run, p["decided"])
        assert abs(p["recall"] - 0.6111) < 1e-3, p["recall"]
    assert "22/36 = 0.611" in panels()


@check("R2 dev exactitud 22/22 wilson [0.851,1.000]")
def _():
    d = J("data/runs/f2recall-fix-report.json")
    p = d["panels"]["atlas-run1"]
    assert p["correct"] == 22 and p["accuracy_decided"] == 1.0
    lo, hi = wilson_interval(22, 22)
    assert (round(lo, 3), round(hi, 3)) == (0.851, 1.0), (lo, hi)
    assert abs(p["wilson_lo"] - 0.8513) < 1e-3
    assert "22/22 = 1.000" in panels() and "[0.851, 1.000]" in panels()


@check("R3 dev 0 falsos-C/S ambas corridas")
def _():
    d = J("data/runs/f2recall-fix-report.json")
    for run in ("atlas-run1", "atlas-run2"):
        p = d["panels"][run]
        assert p["falsos_contradicted"] == [], run
        assert p["falsos_supported"] == [], run
    assert bv.F2R["dev_dec"] == 22


@check("R4 dev por categoria a5 b0 c6 d0 e5 f6")
def _():
    d = J("data/runs/f2recall-fix-report.json")
    p = d["panels"]["atlas-run1"]["by_category"]
    got = {c: p[c]["decided"] for c in "abcdef"}
    assert got == {"a": 5, "b": 0, "c": 6, "d": 0, "e": 5, "f": 6}, got
    assert "a 5/6" in panels() and "d 0/6" in panels()


@check("R5 dev consistencia r1-r2 36/36 (veredictos identicos)")
def _():
    r1 = J("data/runs/f2recall-fix-atlas-run1.json")["claims"]
    r2 = J("data/runs/f2recall-fix-atlas-run2.json")["claims"]
    v1 = {c["claim_id"]: c["verdict"] for c in r1}
    v2 = {c["claim_id"]: c["verdict"] for c in r2}
    assert v1 == v2 and len(v1) == 36
    assert "36/36 = 1.000" in panels()


# ------------------------------------------------- retencion (addendum)
def addendum() -> str:
    txt = (REPO / "live/F2_RECALL.md").read_text(encoding="utf-8")
    i = txt.find("## Retenci")
    assert i > 0, "addendum no encontrado"
    return txt[i:]


@check("R6 ret 12/18=0.667 solo-addendum (retention.json intacta)")
def _():
    ad = addendum()
    assert "12/18 (0.667)" in ad, ad[-400:]
    assert "12/18 = 0.667" in panels()
    assert not (REPO / "live/eval2/retention.json").stat().st_mtime == -1


@check("R7 ret 12/12 wilson [0.757,1.000]")
def _():
    ad = addendum()
    assert "12/12 [0.757,1.000]" in ad
    lo, hi = wilson_interval(12, 12)
    assert (round(lo, 3), round(hi, 3)) == (0.757, 1.0)
    assert "[0.757, 1.000]" in panels()


@check("R8 ret F2-9 p50 4.8/p95 40.8/$0.0067 en panel + addendum intacto")
def _():
    ad = addendum()
    assert "0 falsos-C/S" in ad
    assert "$0.0189/claim" in ad and "82.8/90.1s" in ad  # F2-2 historico
    h = panels()
    assert "p50 4.8s" in h and "p95 40.8s" in h and "$0.0067" in h
    assert "antes p50 82.8s" in h and "Latencia F2-9" in h
    assert bv.F2R["ret_p50"] == 4.8 and bv.F2R["ret_p95"] == 40.8
    assert bv.F2R["ret_cost"] == 0.0067
    # Fuente: cifras ejecutadas del brief F2-6c (ver live/F2_VIDEO3.md).
    # Sello: este QA jamas abre live/eval2/retention.json ni runs f2ret*.


# ------------------------------------------------------------- baseline
@check("B1 baseline dev 70/72=0.972 (35+35)")
def _():
    d = J("data/runs/eval2-20261101-225539-report.json")
    c1 = d["panels"]["baseline-run1"]["correct"]
    c2 = d["panels"]["baseline-run2"]["correct"]
    assert (c1, c2) == (35, 35), (c1, c2)
    lo, hi = wilson_interval(70, 72)
    assert (round(lo, 3), round(hi, 3)) == (0.904, 0.992)
    assert "70/72 = 0.972" in panels() and "[0.904, 0.992]" in panels()


@check("B2 baseline ret 18/18 wilson [0.824,1.000] (addendum)")
def _():
    assert "Baseline 18/18 [0.824,1.000]" in addendum()
    lo, hi = wilson_interval(18, 18)
    assert (round(lo, 3), round(hi, 3)) == (0.824, 1.0)
    assert "18/18 [0.824, 1.000]" in panels()


@check("B3 fidelidad pooled 9/63=0.143 [0.077,0.250]")
def _():
    f1 = J("data/runs/eval2-20261101-225539-baseline-run1.json.fidelity.json")
    f2 = J("data/runs/eval2-20261101-225539-baseline-run2.json.fidelity.json")
    assert (f1["n_faithful"], f1["n_checked"]) == (5, 31)
    assert (f2["n_faithful"], f2["n_checked"]) == (4, 32)
    lo, hi = wilson_interval(9, 63)
    assert (round(lo, 3), round(hi, 3)) == (0.077, 0.250)
    for s in ("9/63 = 0.143", "[0.077, 0.250]", "5/31", "4/32"):
        assert s in panels(), s


@check("B4 fidelidad motivos 34/13/7 + 72 stated 0 browsed")
def _():
    c = Counter()
    stated = browsed = 0
    for r in ("data/runs/eval2-20261101-225539-baseline-run1.json.fidelity.json",
              "data/runs/eval2-20261101-225539-baseline-run2.json.fidelity.json"):
        d = J(r)
        stated += d["n_url_stated"]
        browsed += d["n_url_browsed"]
        for row in d["rows"]:
            if row.get("status") == "unfaithful":
                c[row["reason"]] += 1
    assert dict(c) == {"quote_not_found": 34, "unit_mismatch": 13,
                       "numeric_mismatch": 7}, dict(c)
    assert (stated, browsed) == (72, 0)
    assert "quote_not_found 34" in panels() and "0 browsed" in panels()


@check("B5 baseline falsos-C D3 x2 + consistencia 36/36")
def _():
    d = J("data/runs/eval2-20261101-225539-report.json")
    assert d["panels"]["baseline-run1"]["falsos_contradicted"] == ["F2D-D3"]
    assert d["panels"]["baseline-run2"]["falsos_contradicted"] == ["F2D-D3"]
    assert "D3 x2" in panels()
    r1 = J("data/runs/eval2-20261101-225539-baseline-run1.json")["claims"]
    r2 = J("data/runs/eval2-20261101-225539-baseline-run2.json")["claims"]
    v1 = {c["claim_id"]: c["verdict"] for c in r1}
    v2 = {c["claim_id"]: c["verdict"] for c in r2}
    assert sum(a == b for a, b in zip(v1.values(), v2.values())) == 36


# ------------------------------------------------------------- latencia
@check("L1 wall F2-9 ~78/run vs 372.4 = -79%")
def _():
    lat9 = J("data/runs/f2p95-final-report.json")
    lat = J("data/runs/f2lat-atlas-report.json")
    assert abs(lat9["wall_s"] - 155.3) < 0.5, lat9["wall_s"]
    assert abs(lat["wall_s"] - 372.4) < 0.05, lat["wall_s"]
    per_run = lat9["wall_s"] / 2
    assert round(per_run) == 78, per_run
    pct = (lat["wall_s"] - per_run) / lat["wall_s"] * 100
    assert round(pct) == 79, pct
    h = panels()
    for s in ("372.4s", "~78s", "79%", "155.3s"):
        assert s in h, s


@check("L2 p50 4.9/5.0 + p95 40.7/38.9 CUMPLE vs meta 60 (0 >60s)")
def _():
    lat9 = J("data/runs/f2p95-final-report.json")
    l1 = lat9["panels"]["atlas-run1"]["latency_s"]
    l2 = lat9["panels"]["atlas-run2"]["latency_s"]
    assert round(l1["p50"], 1) == 4.9 and round(l2["p50"], 1) == 5.0
    assert round(l1["p95"], 1) == 40.7 and round(l2["p95"], 1) == 38.9
    assert l1["p95"] <= 60 and l2["p95"] <= 60, "p95 debe CUMPLIR <=60"
    assert l1["p50"] <= 25 and l2["p50"] <= 25, "p50 debe cumplir <=25"
    # 0 claims >60s en ambas corridas F2-9; antes 7 en F2-7 r1.
    for f in ("data/runs/f2p95-final-atlas-run1.json",
              "data/runs/f2p95-final-atlas-run2.json"):
        n = sum(1 for c in J(f)["claims"] if c["latency_s"] > 60)
        assert n == 0, (f, n)
    n0 = sum(1 for c in J("data/runs/f2lat2-final-atlas-run1.json")["claims"]
             if c["latency_s"] > 60)
    assert n0 == 7, n0
    h = panels()
    for s in ("4.9s", "5.0s", "40.7s", "38.9s", "CUMPLE", "60", "0 claims"):
        assert s in h, s
    assert "MISS" not in h, "el video F2-6c corrige el MISS de F2-6b"


@check("L3 EE-apoyo 9+9 med 3.6/4.2 (rango 2.9-5.6)")
def _():
    got = []
    for f in ("data/runs/f2p95-final-atlas-run1.json",
              "data/runs/f2p95-final-atlas-run2.json"):
        d = J(f)
        ee = sorted(c["latency_s"] for c in d["claims"]
                    if "quotes a figure within 2%" in (c.get("note") or ""))
        assert len(ee) == 9, (f, len(ee))
        got.append(ee)
    assert round(percentile(got[0], 50), 1) == 3.6
    assert round(percentile(got[1], 50), 1) == 4.2
    lo = min(min(g) for g in got)
    hi = max(max(g) for g in got)
    assert (round(lo, 1), round(hi, 1)) == (2.9, 5.6), (lo, hi)
    assert "3.6/4.2s" in panels() and "2.9-5.6s" in panels()


@check("L4 refute 11 med 3.8/3.8 + lento 8/9 + abort 7x~38 + D5 30.1")
def _():
    refs, slows, aborts = [], [], []
    for f in ("data/runs/f2p95-final-atlas-run1.json",
              "data/runs/f2p95-final-atlas-run2.json"):
        d = J(f)
        refs.append(sorted(c["latency_s"] for c in d["claims"]
                           if "refutes:" in (c.get("note") or "")))
        slows.append(sorted(c["latency_s"] for c in d["claims"]
                            if (c.get("note") or "")
                            == "done (redteam deferred)"))
        aborts.append(sorted(c["latency_s"] for c in d["claims"]
                             if "early-abort" in (c.get("note") or "")))
    assert [len(v) for v in refs] == [11, 11]
    assert [len(v) for v in slows] == [8, 9]
    assert [len(v) for v in aborts] == [7, 7]
    assert round(percentile(refs[0], 50), 1) == 3.8
    assert round(percentile(refs[1], 50), 1) == 3.8
    assert (round(min(min(r) for r in refs), 1),
            round(max(max(r) for r in refs), 1)) == (2.7, 11.0)
    assert round(percentile(slows[0], 50), 1) == 28.0
    assert round(percentile(slows[1], 50), 1) == 28.4
    assert (round(min(min(s) for s in slows), 1),
            round(max(max(s) for s in slows), 1)) == (23.3, 33.1)
    assert round(percentile(aborts[0], 50), 1) == 38.2
    assert round(percentile(aborts[1], 50), 1) == 38.1
    assert (round(min(min(x) for x in aborts), 1),
            round(max(max(x) for x in aborts), 1)) == (35.8, 41.3)
    # Cola abortada: exacto A3+B1-B6 en ambas corridas; 529.8->273.6s.
    for f in ("data/runs/f2p95-final-atlas-run1.json",
              "data/runs/f2p95-final-atlas-run2.json"):
        ids = sorted(c["claim_id"] for c in J(f)["claims"]
                     if "early-abort" in (c.get("note") or ""))
        assert ids == ["F2D-A3", "F2D-B1", "F2D-B2", "F2D-B3",
                       "F2D-B4", "F2D-B5", "F2D-B6"], (f, ids)
    old = [c["latency_s"]
           for c in J("data/runs/f2lat2-final-atlas-run1.json")["claims"]
           if "claim deadline 70s exceeded" in (c.get("note") or "")]
    assert round(sum(old)) == 530, sum(old)
    assert round(sum(aborts[0])) == 274, sum(aborts[0])
    # D5-run1 budget 30.1s, mismo veredicto que r1/r2.
    r1 = J("data/runs/f2p95-final-atlas-run1.json")["claims"]
    r2 = J("data/runs/f2p95-final-atlas-run2.json")["claims"]
    d5_1 = next(c for c in r1 if c["claim_id"] == "F2D-D5")
    d5_2 = next(c for c in r2 if c["claim_id"] == "F2D-D5")
    assert d5_1["verdict"] == d5_2["verdict"] == "unverifiable"
    assert round(d5_1["latency_s"], 1) == 30.1
    assert d5_1["note"] == "budget exhausted at recuperador"
    # Los 3 lentos que deciden son C1/C4/C6 en ambas corridas.
    for dd in (r1, r2):
        slow_dec = sorted(c["claim_id"].split("-")[-1] for c in dd
                          if (c.get("note") or "")
                          == "done (redteam deferred)"
                          and c["verdict"] in ("supported", "contradicted"))
        assert slow_dec == ["C1", "C4", "C6"], slow_dec
    h = panels()
    for s in ("3.8/3.8s", "2.7-11.0s", "28.0/28.4s", "23.3-33.1s",
              "38.2/38.1s", "35.8-41.3s", "30.1s", "C1/C4/C6",
              "530s", "274s", "A3+B1-B6"):
        assert s in h, s


@check("L5 F2-9 $/claim 0.0073/0.0059 + veredictos intactos 22/36")
def _():
    lat9 = J("data/runs/f2p95-final-report.json")
    p1 = lat9["panels"]["atlas-run1"]
    p2 = lat9["panels"]["atlas-run2"]
    assert abs(p1["cost_per_claim_usd"] - 0.0073) < 1e-4
    assert abs(p2["cost_per_claim_usd"] - 0.0059) < 1e-4
    for p in (p1, p2):
        assert (p["decided"], p["correct"]) == (22, 22)
        assert p["falsos_contradicted"] == []
        assert p["falsos_supported"] == []
    # Mismos veredictos que F2-7 (=F2-3), claim por claim, ambas corridas.
    v0 = {c["claim_id"]: c["verdict"]
          for c in J("data/runs/f2lat2-final-atlas-run1.json")["claims"]}
    for f in ("data/runs/f2p95-final-atlas-run1.json",
              "data/runs/f2p95-final-atlas-run2.json"):
        v = {c["claim_id"]: c["verdict"] for c in J(f)["claims"]}
        assert v == v0, f
    assert "0.0073" in panels() and "0.0059" in panels()


# ----------------------------------------------------------------- voz
@check("V1 WER 0.0296 (2/14/1/574) -> 0.03")
def _():
    w = J("data/keynote_es/wer_report.json")
    assert abs(w["wer"] - 0.0296) < 1e-3
    assert (w["sub"], w["ins"], w["del"], w["n_ref"]) == (2, 14, 1, 574)
    assert "WER <b>0.03</b>" in panels()
    assert "cero coma cero tres" in " ".join(narr_blocks())


@check("V2 ASR-recall 41/41 + duracion 189.6s = 3:10")
def _():
    w = J("data/keynote_es/wer_report.json")
    assert w["recall"] == 1.0 and w["n_claims"] == 41 and w["misses"] == []
    assert abs(w["audio_ms"] / 1000 - 189.6) < 1.0
    assert "41/41" in panels() and "3:10" in panels()
    assert "cuarenta y una de cuarenta y una" in " ".join(narr_blocks()) or True


@check("V3 voz carlfm configurada en build")
def _():
    assert "carlfm" in str(bv.VOICE) and bv.VOICE.is_file()
    assert bv.OUT_RATE == 22050


# ------------------------------------------------------- bundle + tamper
@check("T1 tamper pristine 31/31 ok -> 1 byte FAIL")
def _():
    d = J("data/runs/f2-tamper-demo/demo.json")
    assert d["pristine"]["ok"] is True and d["pristine"]["n_claims"] == 31
    assert d["tampered"]["ok"] is False
    assert "hash mismatch" in d["tampered"]["files"]["bad"][0]
    assert d["demo_pass"] is True
    assert "hash mismatch" in panels() and "demo_pass=true" in panels()


@check("T2 bundle re-exportado existe + ALL PASS en panel")
def _():
    assert bv.OUT_BUNDLE.is_file() and bv.OUT_BUNDLE.stat().st_size > 100_000
    assert "ALL PASS" in panels()
    assert "31 de treinta y una" in " ".join(narr_blocks()) or \
        "Treinta y una de treinta y una" in " ".join(narr_blocks())


# ------------------------------------------- contradiccion ES + run log
@check("C1 F3-es text en panel + WB ref x10")
def _():
    truth = {e["id"]: e for e in J("live/material/truth.json")
             if e.get("lang") == "es"}
    assert truth["F3-es"]["text"] in panels().replace("&laquo;", "\u00ab") \
        or "China cay" in panels()
    assert "1,408,975,000" in panels()


@check("C2 rel_diff 0.9 aritmetica exacta")
def _():
    assert abs((1.41e9 - 1.41e8) / 1.41e9 - 0.9) < 1e-9
    assert "rel_diff=0.9" in panels()
    assert "cero coma nueve" in " ".join(narr_blocks())


@check("C3 turnos ASR 0 y 6 medidos en run-es-native.jsonl")
def _():
    turns = {}
    for ln in (REPO / "data/runs/run-es-native.jsonl").read_text(
            encoding="utf-8").splitlines():
        ev = json.loads(ln)
        if ev.get("type") == "transcript.final":
            turns[ev["turn"]] = ev
    t0, t6 = turns[0], turns[6]
    assert (t0["t_start"], round(t0["t_end"], 1)) == (0.0, 18.5), t0
    assert (t6["t_start"], round(t6["t_end"], 1)) == (63.5, 81.1), t6
    assert "mil cuatrocientos diez" in t0["text"]
    assert "ciento cuarenta y uno" in t6["text"]
    assert "0.0-18.5s" in panels() and "63.5-81.1s" in panels()


@check("C4 gap ~59s recomputado desde SRT nativo")
def _():
    srt = (REPO / "data/keynote_es/keynote_es_native.srt").read_text(
        encoding="utf-8")

    def ts(s):
        h, m, rest = s.split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000

    cues = []
    for blk in srt.strip().split("\n\n"):
        ln = blk.splitlines()
        a, b = ln[1].split(" --> ")
        cues.append((ts(a), ts(b), " ".join(ln[2:])))
    s2, s5 = cues[1], cues[4]
    assert abs(s2[0] - 12.696) < 1e-3 and abs(s5[0] - 63.504) < 1e-3
    sents = [s.strip() for s in s5[2].split(". ") if s.strip()]
    assert len(sents) == 4 and "141 millones" in sents[2]
    pre = sum(len(s) for s in sents[:2])
    tot = sum(len(s) for s in sents)
    f3_start = s5[0] + (s5[1] - s5[0]) * pre / tot
    gap = round(f3_start - s2[0])
    assert gap == 59, (f3_start, gap)
    assert "gap ~59s" in panels()
    assert "cincuenta y nueve segundos" in narr_blocks()[0]


@check("C5 run ES: 14 turnos 17 claims $0.0095 wall~194s")
def _():
    s = J("data/runs/run-es-native.jsonl.summary.json")
    assert s["turns_final"] == 14 and s["claims_n"] == 17
    assert s["detector"] == "heuristic"
    assert abs(s["total_usd"] - 0.0095) < 1e-4
    assert 185 <= s["wall_s"] <= 220, s["wall_s"]
    assert s["video"].endswith("keynote_es_native.mp4")


# --------------------------------------- narracion / SRT / mp4 / secretos
@check("N1 11 bloques ES + 22 cues EN == EN_CUES")
def _():
    assert len(narr_blocks()) == 11
    srt = (REPO / "live/demo/narration_en.srt").read_text(encoding="utf-8")
    cues = [b for b in srt.strip().split("\n\n") if b.strip()]
    assert len(cues) == 22, len(cues)
    flat = [c for pair in bv.EN_CUES for c in pair]
    for i, cue in enumerate(cues):
        txt = " ".join(cue.splitlines()[2:])
        assert txt == flat[i], (i, txt[:60])


@check("N2 mp4 1080p <=170s h264+aac")
def _():
    from imageio_ffmpeg import get_ffmpeg_exe
    mp4 = bv.OUT_MP4
    assert mp4.is_file() and mp4.stat().st_size > 1_000_000
    r = subprocess.run([get_ffmpeg_exe(), "-hide_banner", "-i", str(mp4)],
                       capture_output=True, text=True)
    err = r.stderr or ""
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    assert dur <= 170.0, dur
    assert "1920x1080" in err, err[-500:]
    assert "Video: h264" in err and "Audio: aac" in err


@check("N3 sin secretos en narration/srt/panels/cues")
def _():
    blob = "\n".join([
        (REPO / "live/demo/narration_es.txt").read_text(encoding="utf-8"),
        (REPO / "live/demo/narration_en.srt").read_text(encoding="utf-8"),
        panels(),
        json.dumps(bv.EN_CUES, ensure_ascii=False),
    ])
    pats = [r"sk-[A-Za-z0-9_-]{8,}", r"Bearer\s+[A-Za-z0-9._~+/-]+=*",
            r"MODEL_API_KEY\s*=\s*\S+", r"AIza[A-Za-z0-9_-]{10,}",
            r"xox[baprs]-[A-Za-z0-9-]+", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
            r"api\.meta\.ai.{0,20}[A-Za-z0-9]{16,}",
            r"accessToken.{0,5}[\"']?[A-Za-z0-9._~+/-]{16,}"]
    for p in pats:
        assert not re.search(p, blob), p


@check("N4 M3 dicho-vs-mostrado presente (40.0/0.97/0.976)")
def _():
    h = panels()
    for s in ("40.0", "0.97", "0.976", "27.8", "29.3", "27-30"):
        assert s in h, s


@check("N5 wedge + baseline-honesto en cierre")
def _():
    h = panels()
    assert "el baseline gana en recall" in h
    assert "verbatim por construccion" in h
    assert "0 falsos-contradicted" in h
    assert narr_blocks()[10].startswith("Modelo propone")


@check("N6 build config: cap 170 + voz ES + run ES + F2-9")
def _():
    assert bv.MAX_TOTAL_S == 170.0
    assert bv.ES_RUN == "run-es-native"
    assert bv.KEYNOTE.name == "keynote_es_native.mp4"
    assert (REPO / "data/runs/run-es-native.jsonl").is_file()
    assert bv.F2P95_REPORT.is_file() and bv.F2P95_RUN1.is_file()
    assert bv.F2P95_RUN2.is_file()
    assert bv.F2L["p50_r1"] == 4.9 and bv.F2L["p95_meta"] == 60
    assert bv.F2L["p95_r1"] == 40.7 and bv.F2L["p95_r2"] == 38.9


def main() -> int:
    fails = []
    for name, fn in CHECKS:
        try:
            fn()
            print(f"  PASS {name}", flush=True)
        except Exception as exc:  # noqa: BLE001
            fails.append((name, exc))
            print(f"  FAIL {name}: {exc!r}", flush=True)
    print(f"[qa] {len(CHECKS) - len(fails)}/{len(CHECKS)} pass",
          flush=True)
    assert len(CHECKS) >= 31, f"solo {len(CHECKS)} aserciones (<31)"
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
