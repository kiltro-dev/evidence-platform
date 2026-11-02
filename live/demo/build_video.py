#!/usr/bin/env python3
"""M9b/F2-6 — Atlas Live demo video builder (checkout compartido).

Pipeline todo local, $0 (el log ES live se corre aparte, ver F2_VIDEO.md):
  1. TTS piper (voz nativa ES carlfm, dominio publico, misma que F2-4)
     desde live/demo/narration_es.txt -> 11 WAV (reescritos a 22050 Hz).
  2. Exporta bundle real (offline) desde la corrida M2d
     (data/runs/verify-20261101-212343*) -> data/demo/atlas-live-bundle.zip
     y lo re-verifica con live/bundle/verify_bundle.py (captura real stdout).
  3. Extrae frames reales del keynote ES nativo (slides 6/7/10 ES).
  4. Genera panels.html (cifras F2 + M3/M7 citadas, arco baseline-honesto).
  5. Levanta el server replay (uvicorn, puerto 8897) + estatico ES (8899)
     y graba con playwright recordVideo 1920x1080: UI replay ES + paneles
     + terminal. Mata ambos servers.
  6. ffmpeg compone data/demo/atlas-live-demo.mp4 (1080p, <=2:50) con
     narracion + SRT EN incrustados (live/demo/narration_en.srt).

Uso:
  cd evidence-platform && .venv/bin/python live/demo/build_video.py

No hace llamadas live (QA de transcripcion va aparte). No toca git.
"""

from __future__ import annotations

import functools
import http.server
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from live.bundle.export import export_bundle  # noqa: E402
from live.library.store import EvidenceStore  # noqa: E402
from live.run_verify import claim_input_for, load_entries  # noqa: E402

# ---------------------------------------------------------------- config

DEMO = REPO / "live" / "demo"
DATA_DEMO = REPO / "data" / "demo"
WORK = Path("/tmp/atlas_demo")
ASSETS = WORK / "assets"
CLIPS = WORK / "clips"

NARR_ES = DEMO / "narration_es.txt"
NARR_SRT = DEMO / "narration_en.srt"
OUT_MP4 = DATA_DEMO / "atlas-live-demo.mp4"
OUT_BUNDLE = DATA_DEMO / "atlas-live-bundle.zip"

PORT = 8897
MPORT = 8899          # estatico para el mp4 ES nativo (sin tocar app.py)
PAD_S = 0.8          # silencio tras cada bloque
LEAD_S = 0.5         # silencio inicial
MAX_TOTAL_S = 170.0  # hard cap F2-6 (requerido <=2:50, margen >=10s)

VOICE = Path("/tmp/piper_voices_es/es_ES-carlfm-x_low.onnx")
VOICE_RATE = 16000   # carlfm x_low; se reescribe a OUT_RATE tras piper
OUT_RATE = 22050
PIPER = REPO / ".venv" / "bin" / "piper"

M2D_SUMMARY = REPO / "data/runs/verify-20261101-212343.jsonl.summary.json"
M2D_LOG = REPO / "data/runs/verify-20261101-212343.jsonl"
M2D_DB = REPO / "data/runs/verify-20261101-212343.jsonl.db"
TRUTH = REPO / "live/material/truth.json"
KEYNOTE = REPO / "data/keynote_es/keynote_es_native.mp4"
KEYNOTE_DIR = REPO / "data/keynote_es"
KEYNOTE_NAME = "keynote_es_native.mp4"
ES_RUN = "run-es-native"  # log live ES: data/runs/run-es-native.jsonl

# Reportes F2 (fuente de cada cifra nueva; retencion SOLO via addendum).
F2RECALL_REPORT = REPO / "data/runs/f2recall-fix-report.json"
F2LAT_REPORT = REPO / "data/runs/f2lat-atlas-report.json"
F2LAT_RUN = REPO / "data/runs/f2lat-atlas-atlas-run1.json"
# F2-6b: latencia F2-7 (antes=F2-3, despues=F2-7 r1/r2).
F2LAT2_REPORT = REPO / "data/runs/f2lat2-final-report.json"
F2LAT2_RUN1 = REPO / "data/runs/f2lat2-final-atlas-run1.json"
F2LAT2_RUN2 = REPO / "data/runs/f2lat2-final-atlas-run2.json"
# F2-6c: latencia F2-9 (antes=F2-3, despues=F2-9 r1/r2; F2-7 = ref intermedia).
F2P95_REPORT = REPO / "data/runs/f2p95-final-report.json"
F2P95_RUN1 = REPO / "data/runs/f2p95-final-atlas-run1.json"
F2P95_RUN2 = REPO / "data/runs/f2p95-final-atlas-run2.json"
EVAL2_REPORT = REPO / "data/runs/eval2-20261101-225539-report.json"
FID_R1 = REPO / "data/runs/eval2-20261101-225539-baseline-run1.json.fidelity.json"
FID_R2 = REPO / "data/runs/eval2-20261101-225539-baseline-run2.json.fidelity.json"
WER_REPORT = REPO / "data/keynote_es/wer_report.json"
TAMPER_DEMO = REPO / "data/runs/f2-tamper-demo/demo.json"

# Cifras M3/M7 (sin artefacto JSON; fuente citada en cada panel).
M3 = {"f6_said": 40.0, "f6_shown": 0.97, "f6_d": 0.976,
      "f5_axis": "27-30", "f5_vals": "27.8->29.3", "f5_cue": "doubled",
      "f11_cue": "larger than", "t10_d": 0.0}
# Par ES medido (log run-es-native.jsonl + SRT nativo; metodo M7).
M7ES = {"t2_win": "0.0-18.5", "f3_win": "63.5-81.1", "gap": 59,
        "a": "1.41e9", "b": "1.41e8", "rel_diff": 0.9}
# F2 recall/latencia/baseline/voz (verificadas en qa_video.py vs JSONs).
F2R = {"dev_dec": 22, "dev_n": 36, "dev_wilson": "[0.851, 1.000]",
       "ret_dec": 12, "ret_n": 18, "ret_wilson": "[0.757, 1.000]",
       # F2-6c: brief ejecutado (retencion F2-9), ver F2_VIDEO3.md
       "ret_p50": 4.8, "ret_p95": 40.8, "ret_cost": 0.0067,
       "cats": "a 5/6 · b 0/6 · c 6/6 · d 0/6 · e 5/6 · f 6/6"}
# F2-6c: latencia F2-9 (F2_P95.md §4 + f2p95-final-*.json).
F2L = {"wall_before": 372.4, "wall_after": 77.7, "wall_pct": 79,
       "p50_before": 80.3, "p95_before": 115.7, "cost_before": 0.0227,
       "p50_r1": 4.9, "p50_r2": 5.0, "p95_r1": 40.7, "p95_r2": 38.9,
       "p95_meta": 60, "cost_r1": 0.0073, "cost_r2": 0.0059,
       "ee_sup_n": 9, "ee_sup_med": "3.6/4.2", "ee_sup_rng": "2.9-5.6",
       "ee_ref_n": 11, "ee_ref_med": "3.8/3.8", "ee_ref_rng": "2.7-11.0",
       "slow_n": "8/9", "slow_med": "28.0/28.4", "slow_rng": "23.3-33.1",
       "abort_n": 7, "abort_med": "38.2/38.1", "abort_rng": "35.8-41.3",
       "per_claim": 0.0073}
F2B = {"dev_ok": 70, "dev_n": 72, "dev_wilson": "[0.904, 0.992]",
       "ret_n": 18, "ret_wilson": "[0.824, 1.000]",
       "fid": "9/63 = 0.143", "fid_wilson": "[0.077, 0.250]",
       "fid_r1": "5/31 = 0.161", "fid_r2": "4/32 = 0.125",
       "reasons": "quote_not_found 34 · unit_mismatch 13 · numeric_mismatch 7"}
F2V = {"wer": 0.0296, "recall": "41/41", "dur_s": 189.6}

EN_CUES = [
    ("One speaker says two things that can't both be true, 59 seconds apart.",
     "Atlas Live catches it live \u2014 caught live \u2014 and shows the proof, not just the verdict."),
    ("Atlas Live listens to and watches a live video, extracts every checkable claim,",
     "investigates it with Muse agents on the open web; deterministic code decides. Native Spanish: 0.03 word error."),
    ("Minute one-ten. The speaker says China fell to 141 million people.",
     "Three researchers hit the open web while the video keeps running."),
    ("Contradicted. The World Bank says 1.409 billion \u2014 ten times more.",
     "And at minute zero he had said 1.41 billion: self-contradiction verified by code, rel diff of 0.9."),
    ("And you don't have to take our word for it. Every run exports a proof bundle",
     "anyone can re-run with no network and no model: all pass. Flip one byte and the verifier fails."),
    ("But there's something better. The speaker says forty percent, and his own chart shows 0.97.",
     "The chart contradicts the speaker. Vision reads the frame; code decides."),
    ("Same trick on two slides: a truncated axis turning five percent into a doubling,",
     "and a broken scale putting India above the United States. Two automatic flags."),
    ("The fair benchmark, v2: 36 dev claims and 18 sealed retention claims.",
     "Atlas decides 61% on dev and 67% on retention, with zero false contradicts in both."),
    ("And now it's much faster: median down from 80 seconds to 5, at six to seven tenths of a cent per claim.",
     "And the tail now meets our 60-second goal at 39 to 41 seconds \u2014 a no-progress abort cut the queue from 530 to 274 seconds."),
    ("The baseline, as is: 70 of 72 on dev and 18 of 18 on retention \u2014 it beats us on recall.",
     "But only 14% of its citations are faithful to the source, and it exports zero re-runnable proof."),
    ("Model proposes, code disposes. Our wedge: re-verifiable proof, zero false contradicts,",
     "and said-vs-shown. All measured, all reproducible: try it."),
]


def ffmpeg() -> str:
    from imageio_ffmpeg import get_ffmpeg_exe
    return get_ffmpeg_exe()


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def wav_dur(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


# ---------------------------------------------------------------- TTS

def _ensure_out_rate(path: Path) -> None:
    """Reescribe WAV a OUT_RATE mono si piper entrego otra tasa (carlfm=16k)."""
    with wave.open(str(path), "rb") as w:
        rate, ch = w.getframerate(), w.getnchannels()
    if (rate, ch) == (OUT_RATE, 1):
        return
    tmp = path.with_suffix(".rs.wav")
    r = run([ffmpeg(), "-y", "-i", str(path), "-ac", "1", "-ar",
             str(OUT_RATE), str(tmp)])
    assert tmp.is_file(), f"resample fallo: {r.stderr[-300:]}"
    tmp.replace(path)


def step_tts() -> list[float]:
    """Sintetiza los 11 bloques ES con piper. Devuelve duraciones."""
    blocks = [b.strip() for b in NARR_ES.read_text(encoding="utf-8").split("\n\n")
              if b.strip()]
    assert len(blocks) == 11, f"narration_es.txt debe tener 11 bloques, hay {len(blocks)}"
    assert len(EN_CUES) == 11
    durs = []
    for i, text in enumerate(blocks):
        out = WORK / f"narr_{i:02d}.wav"
        r = subprocess.run([str(PIPER), "--model", str(VOICE),
                            "--output_file", str(out)],
                           input=text.encode("utf-8"), capture_output=True)
        if r.returncode != 0 or not out.is_file():
            raise RuntimeError(f"piper fallo bloque {i}: {r.stderr.decode()[-300:]}")
        _ensure_out_rate(out)
        d = wav_dur(out)
        durs.append(d)
        print(f"[tts] bloque {i}: {len(text)} chars -> {d:.1f}s", flush=True)
    total = LEAD_S + sum(durs) + PAD_S * len(durs)
    print(f"[tts] total previsto: {total:.1f}s (cap {MAX_TOTAL_S}s)", flush=True)
    assert total <= MAX_TOTAL_S, f"narracion {total:.1f}s excede {MAX_TOTAL_S}s"
    return durs


# ---------------------------------------------------------------- bundle real

def step_bundle() -> str:
    """Exporta bundle real M2d (offline) y lo re-verifica. Devuelve stdout."""
    DATA_DEMO.mkdir(parents=True, exist_ok=True)
    entries = load_entries(TRUTH, "en")
    summ = json.loads(M2D_SUMMARY.read_text(encoding="utf-8"))
    verdicts = {r["claim_id"]: r["verdict"] for r in summ["per_claim"]}
    store = EvidenceStore(M2D_DB, snapshot_dir=REPO / "data/snap")
    claims, packages = [], []
    try:
        for e in entries:
            ci = claim_input_for(e)
            claims.append({"claim_id": ci.claim_id, "text": ci.text,
                           "value": ci.value, "unit": ci.unit, "raw": ci.raw,
                           "claim_year": ci.claim_year,
                           "verdict": verdicts[ci.claim_id]})
            for p in store.packages_for_claim(ci.claim_id):
                packages.append(p.to_dict())
    finally:
        store.close()
    export_bundle(run_log=M2D_LOG, packages=packages, claims=claims,
                  out_path=OUT_BUNDLE, truth_ref=TRUTH,
                  snapshot_dir=REPO / "data/snap")
    r = run([sys.executable, "live/bundle/verify_bundle.py",
             str(OUT_BUNDLE.relative_to(REPO))], cwd=str(REPO))
    out = (r.stdout or "") + (r.stderr or "")
    print(out, flush=True)
    assert r.returncode == 0 and "ALL PASS" in out, "verify_bundle no paso"
    return out.strip()


def f3_evidence() -> dict:
    """Paquete WB real de F3 (cita literal + sha + url) desde el store M2d."""
    con = sqlite3.connect(M2D_DB)
    try:
        rows = con.execute(
            "SELECT payload FROM packages WHERE claim_id='F3-en'").fetchall()
    finally:
        con.close()
    for (payload,) in rows:
        d = json.loads(payload)
        if "api.worldbank.org" in (d.get("url") or ""):
            return {"quote": d.get("quote", "")[:110],
                    "sha": (d.get("sha256") or "")[:12],
                    "url": "api.worldbank.org/.../CHN/.../SP.POP.TOTL",
                    "pkg": d.get("package_id", "")}
    raise RuntimeError("paquete WB de F3 no encontrado")


# ---------------------------------------------------------------- frames keynote

def step_frames() -> None:
    ff = ffmpeg()
    # Slides ES nativos (keynote_es_native.srt: s6 81.5-93.6, s7 93.6-110.6,
    # s10 150.7-164.4; mid-slide para charts estables).
    picks = {"slide6.png": 87.5, "slide7.png": 102.0, "slide10.png": 157.5}
    # Sentinel: re-extrae si cambian keynote o tiempos (evita frames rancios).
    want = {"keynote": KEYNOTE.name, "mtime": KEYNOTE.stat().st_mtime,
            "picks": picks}
    sentinel = ASSETS / ".frames.json"
    try:
        fresh = json.loads(sentinel.read_text(encoding="utf-8")) == want
    except (OSError, ValueError):
        fresh = False
    for name, t in picks.items():
        out = ASSETS / name
        if fresh and out.is_file():
            continue
        r = run([ff, "-y", "-ss", str(t), "-i", str(KEYNOTE),
                 "-frames:v", "1", str(out)])
        assert out.is_file(), f"frame {name} fallo: {r.stderr[-300:]}"
        print(f"[frames] {name} @ {t}s", flush=True)
    sentinel.write_text(json.dumps(want), encoding="utf-8")


# ---------------------------------------------------------------- paneles

PANELS_CSS = """
:root { --bg:#0f1420; --panel:#1a2233; --line:#2c3852; --txt:#e8ecf4;
  --mut:#9aa6bf; --acc:#6cb2ff; --ok:#4cc38a; --bad:#e5636f;
  --warn:#e5a763; --na:#8a93a8; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--txt);
  font:21px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif; }
section { min-height:100vh; max-width:1500px; margin:0 auto;
  padding:44px 56px; border-bottom:2px solid var(--line);
  display:flex; flex-direction:column; justify-content:center; }
h1 { font-size:40px; margin:0 0 6px; }
h1 .en { color:var(--acc); font-size:24px; font-weight:600; }
.sub { color:var(--mut); font-size:19px; margin:0 0 26px; }
.src { color:var(--mut); font-size:15px; margin-top:26px;
  border-top:1px dashed var(--line); padding-top:10px; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:22px; }
.card { background:var(--panel); border:1px solid var(--line);
  border-radius:14px; padding:20px 24px; }
.card h2 { margin:0 0 10px; font-size:23px; }
.big { font-size:30px; font-weight:800; }
.ok { color:var(--ok); } .bad { color:var(--bad); }
.warn { color:var(--warn); } .acc { color:var(--acc); }
.mono { font-family:"DejaVu Sans Mono",monospace; font-size:17px; }
table { border-collapse:collapse; width:100%; font-size:19px; }
th, td { border:1px solid var(--line); padding:8px 12px; text-align:left; }
th { background:#0d1220; }
td.n, th.n { text-align:right; font-variant-numeric:tabular-nums; }
.hl { outline:3px solid var(--acc); border-radius:14px; transition:outline .3s; }
.pipe { display:flex; gap:10px; align-items:stretch; margin:26px 0; flex-wrap:wrap; }
.pbox { flex:1 1 150px; background:var(--panel); border:1px solid var(--line);
  border-radius:12px; padding:16px 12px; text-align:center; font-size:19px; }
.pbox b { display:block; font-size:21px; margin-bottom:4px; }
.pbox small { color:var(--mut); font-size:15px; }
.parr { align-self:center; color:var(--acc); font-size:30px; font-weight:800; }
.pbox.code { border-color:var(--ok); }
.pbox.code b { color:var(--ok); }
img.slide { width:100%; border-radius:12px; border:1px solid var(--line); }
.vs { display:flex; gap:22px; align-items:center; }
.vs .said, .vs .shown { flex:1; text-align:center; }
#term { background:#05080f; border:1px solid var(--line); border-radius:12px;
  padding:18px 22px; }
#term .cmd { color:var(--ok); }
#termout { height:560px; overflow-y:auto; white-space:pre-wrap; margin:10px 0 0;
  font-family:"DejaVu Sans Mono",monospace; font-size:16.5px; line-height:1.45; }
.pass { color:var(--ok); } .info { color:var(--mut); }
.banner { border-radius:12px; padding:14px 22px; font-size:24px; font-weight:800;
  margin:18px 0; }
.banner.lose { border:2px solid var(--warn); color:var(--warn); }
.banner.wedge { border:2px solid var(--ok); color:var(--ok); }
.kv { display:grid; grid-template-columns:auto 1fr; gap:6px 18px; font-size:20px; }
.kv dt { color:var(--mut); } .kv dd { margin:0; }
"""

PANELS_BODY = """
<section id="pipeline">
<h1>Que es Atlas Live <span class="en">a real-time agent newsroom</span></h1>
<p class="sub">Escucha y mira un video en directo, investiga cada afirmacion y publica veredictos con prueba.</p>
<div class="pipe">
<div class="pbox"><b>video</b><small>keynote ES 3:10 nativo</small></div><div class="parr">&rarr;</div>
<div class="pbox"><b>ASR</b><small>realtime ES · WER 0.03</small></div><div class="parr">&rarr;</div>
<div class="pbox"><b>claims</b><small>detector de afirmaciones</small></div><div class="parr">&rarr;</div>
<div class="pbox"><b>agentes x3</b><small>Muse + web abierta</small></div><div class="parr">&rarr;</div>
<div class="pbox code"><b>codigo decide</b><small>verificador determinista</small></div><div class="parr">&rarr;</div>
<div class="pbox"><b>tarjeta ES/EN</b><small>por WebSocket en vivo</small></div>
</div>
<div class="grid2">
<div class="card"><h2>Ojos: vista</h2>Lee graficos y ejes en los frames; codigo cruza <b>dicho-vs-mostrado</b> (tol. 2%).</div>
<div class="card"><h2>Oidos ES nativos</h2>Keynote re-generado con voz espa&ntilde;ola carlfm: WER <b>0.03</b> (2 sub + 14 ins + 1 del / 574) y ASR-recall <b>41/41</b>.</div>
</div>
<p class="src">Fuentes: live/README.md, live/M1_E2E.md (bus + /ws) · live/F2_VOICE.md + wer_report.json (WER 0.0296, recall 1.0). Lema: the model proposes, code disposes.</p>
</section>

<section id="contradiction">
<h1>La falsa, atrapada dos veces <span class="en">F3 caught: evidence + self-contradiction</span></h1>
<p class="sub">Minuto 1:10 del keynote ES: &laquo;{{F3_TEXT}}&raquo;</p>
<div class="grid2">
<div class="card" id="c-f3"><h2>F3 &rarr; <span class="bad">contradicted</span> <span class="mono info">(3 paquetes WB)</span></h2>
<dl class="kv">
<dt>Banco Mundial</dt><dd class="mono">{{F3_QUOTE}}</dd>
<dt>URL</dt><dd class="mono acc">{{F3_URL}}</dd>
<dt>sha256</dt><dd class="mono">{{F3_SHA}}&hellip;</dd>
<dt>Ref 2024</dt><dd>China = <b>1,408,975,000</b> (no 141M: error x10)</dd>
</dl></div>
<div class="card" id="c-sc"><h2><span class="mono">speaker.contradiction</span> <span class="ok">verificada por codigo</span></h2>
<dl class="kv">
<dt>A (turno 0: 0.0-18.5s)</dt><dd>&laquo;China tiene <b>mil cuatrocientos diez millones</b> de habitantes&raquo;</dd>
<dt>B (turno 6: 63.5-81.1s)</dt><dd>&laquo;cay&oacute; a solo <b>ciento cuarenta y uno millones</b> de personas&raquo;</dd>
<dt>Choque</dt><dd class="mono">1.41e9 vs 1.41e8 count, gap ~59s, <b>rel_diff=0.9</b></dd>
</dl></div>
</div>
<p class="src">Fuentes: verify-20261101-212343.jsonl.summary.json (F3-en M2d, misma falsedad plantada: contradicted, 3 paquetes) · run-es-native.jsonl (turnos ASR 0 y 6) + SRT nativo (gap ~59s) · metodo live/M7.md (rel_diff=0.9).</p>
</section>

<section id="terminal">
<h1>Prueba re-verificable <span class="en">don't trust us, re-run it</span></h1>
<p class="sub">Cada corrida exporta un ZIP que cualquiera re-ejecuta sin red ni modelo.</p>
<div id="term"><div class="cmd mono">demo@atlas:~/evidence-platform$ python live/bundle/verify_bundle.py {{BUNDLE_NAME}}</div>
<div id="termout">{{TERMINAL_OUT}}</div></div>
<div class="card" id="c-tamper"><h2>Tamper: 1 byte &rarr; <span class="bad">FAIL</span></h2>
<p class="mono">pristine 31/31 ok=True &rarr; 1 byte flip en snapshots/06c3ddaa&hellip;.html &rarr; ok=False <span class="bad">(hash mismatch)</span> · demo_pass=true</p></div>
<p class="src">Salida real, verbatim, de {{BUNDLE_NAME}} (31 claims M2d) · motor live.verifier · verificador: live/bundle/verify_bundle.py · tamper: data/runs/f2-tamper-demo/demo.json.</p>
</section>

<section id="f6">
<h1>El grafico contradice al orador <span class="en">said 40.0 vs shown 0.97</span></h1>
<p class="sub">Slide 7 (frame ES real t=102.0s): barra honesta de 0.97% mientras el habla dice &laquo;creci&oacute; casi un 40 por ciento&raquo;.</p>
<div class="vs">
<div style="flex:1.4"><img class="slide" src="slide7.png" alt="slide 7"></div>
<div class="shown card" id="c-f6"><h2><span class="mono">visual.mismatch</span></h2>
<p class="big">said <span class="bad">40.0</span> / shown <span class="ok">0.97</span></p>
<p class="mono">d = 0.976 &gt; tol 2% &rarr; mismatch</p>
<p>Vision lee el frame (schema estricto); <b>codigo</b> cruza dicho-vs-mostrado.</p></div>
</div>
<p class="src">Fuente: live/M3.md (F6, match T10 d=0.0 como control honesto). Frame ES nativo (mismos datos de chart).</p>
</section>

<section id="axes">
<h1>Ejes tramposos <span class="en">truncated axis x2</span></h1>
<p class="sub">Mismo truco en dos slides: un 5% que parece duplicacion, y una escala rota que invierte el ranking.</p>
<div class="grid2">
<div class="card" id="c-f5"><h2>Slide 6 &middot; F5 <span class="mono warn">visual.misleading_axis</span> (cue &laquo;doubled&raquo;)</h2>
<img class="slide" src="slide6.png" alt="slide 6">
<p class="mono">barras 27.8&rarr;29.3, eje Y 27-30, truncated=true</p></div>
<div class="card" id="c-f11"><h2>Slide 10 &middot; F11 <span class="mono warn">visual.misleading_axis</span> (cue &laquo;larger than&raquo;)</h2>
<img class="slide" src="slide10.png" alt="slide 10">
<p>India $3.76T dibujada mas alta que EE.UU. $29.3T: escala rota con zigzag.</p></div>
</div>
<p class="src">Fuente: live/M3.md (F5 + F11 flags sobre habla EN; F12 cherry-pick fuera de contrato). Frames ES nativos (mismos datos).</p>
</section>

<section id="recall">
<h1>Recall que generaliza <span class="en">dev 0.611 &rarr; retention 0.667</span></h1>
<p class="sub">Benchmark justo v2: 36 DEV + 18 retencion sellada (prereg sellado antes de correr). Fixes generales, 0 falsos-contradicted.</p>
<div class="grid2">
<div class="card" id="c-dev"><h2>DEV: <span class="big ok">22/36 = 0.611</span></h2>
<dl class="kv">
<dt>Exactitud decidida</dt><dd class="mono">22/22 = 1.000 Wilson 95% [0.851, 1.000]</dd>
<dt>Falsos-C / falsos-S</dt><dd class="big ok">0 / 0</dd>
<dt>Por categoria</dt><dd class="mono">a 5/6 · b 0/6 · c 6/6 · d 0/6 · e 5/6 · f 6/6</dd>
<dt>Consistencia r1-r2</dt><dd class="mono">36/36 = 1.000 (+14 decididos: rescate code + citas refutatorias)</dd>
</dl></div>
<div class="card" id="c-ret"><h2>RETENCION: <span class="big ok">12/18 = 0.667</span></h2>
<dl class="kv">
<dt>Exactitud decidida</dt><dd class="mono">12/12 Wilson 95% [0.757, 1.000]</dd>
<dt>Falsos-C / falsos-S</dt><dd class="big ok">0 / 0</dd>
<dt>Latencia F2-9</dt><dd class="mono">p50 4.8s, p95 40.8s, $0.0067 (antes p50 82.8s, código F2-2)</dd>
<dt>Lectura</dt><dd>Dev 0.611 &rarr; ret 0.667: <b>generaliza</b> (b: primarios 403; d: aritmetica honesta).</dd>
</dl></div>
</div>
<p class="src">Fuentes: data/runs/f2recall-fix-report.json (DEV, ambas corridas 22/22) · live/F2_RECALL.md addendum (retencion: 12/18, 12/12, 0 falsos) · live/F2_VIDEO3.md (retencion F2-9: p50 4.8s/p95 40.8s/$0.0067, brief ejecutado).</p>
</section>

<section id="latency">
<h1>Mucho mas rapido, con honestidad <span class="en">p50 5s, p95 meets 60s</span></h1>
<p class="sub">F2-9: mismos 22/36 veredictos que F2-7 (ninguna palanca cambio un veredicto); meta p50 &le;25s CUMPLE, p95 &le;60s CUMPLE.</p>
<div class="grid2">
<div class="card" id="c-wall"><h2>Antes &rarr; despues</h2>
<table><tr><th>metrica</th><th class="n">F2-3</th><th class="n">F2-9 r1</th><th class="n">F2-9 r2</th></tr>
<tr><td>p50 / claim</td><td class="n">80.3s</td><td class="n ok">4.9s</td><td class="n ok">5.0s</td></tr>
<tr><td>p95 / claim</td><td class="n">115.7s</td><td class="n ok">40.7s</td><td class="n ok">38.9s</td></tr>
<tr><td>wall 36 claims</td><td class="n">372.4s</td><td class="n ok" colspan="2">~78s (&minus;79%)</td></tr>
<tr><td>$/claim</td><td class="n">0.0227</td><td class="n">0.0073 (&minus;68%)</td><td class="n">0.0059 (&minus;74%)</td></tr></table>
<p class="info">El p95 CUMPLE la meta de 60s con ~20s de margen: 0 claims &gt;60s en ambas corridas (antes 7). Palanca: aborto no-progress; la cola A3+B1-B6 paso de 530s a 274s (&minus;48%).</p></div>
<div class="card" id="c-ee"><h2>Por grupo de ejecucion (r1/r2)</h2>
<table><tr><th>grupo</th><th class="n">n</th><th class="n">mediana</th><th class="n">rango</th></tr>
<tr><td>early-exit apoyo</td><td class="n">9/9</td><td class="n ok">3.6/4.2s</td><td class="n">2.9-5.6s</td></tr>
<tr><td>early-exit refuta</td><td class="n">11/11</td><td class="n ok">3.8/3.8s</td><td class="n">2.7-11.0s</td></tr>
<tr><td>lento ok</td><td class="n">8/9</td><td class="n">28.0/28.4s</td><td class="n">23.3-33.1s</td></tr>
<tr><td>early-abort</td><td class="n">7/7</td><td class="n ok">38.2/38.1s</td><td class="n">35.8-41.3s</td></tr></table>
<p class="info">20/36 por via rapida ~4s; 3 lentos deciden (C1/C4/C6); D5-run1 budget 30.1s con mismo veredicto.</p></div>
</div>
<p class="src">Fuente: data/runs/f2p95-final-report.json (p50 4.9/5.0s, p95 40.7/38.9s, wall 155.3s/2, $0.0073/0.0059) + runs r1/r2 (grupos medidos, nearest-rank) + live/F2_P95.md §4.</p>
</section>

<section id="baseline">
<h1>El baseline, tal cual <span class="en">wins recall, loses proof</span></h1>
<p class="sub">Model-only (1 llamada + web_search por claim), mismo DEV, 2 corridas. Donde acierta, donde falla, que agrega Atlas.</p>
<div class="grid2">
<div class="card" id="c-beq"><h2>Donde acierta igual <span class="ok">veredictos</span></h2>
<dl class="kv">
<dt>DEV pooled</dt><dd class="mono">70/72 = 0.972 [0.904, 0.992] (r1 35/36, r2 35/36)</dd>
<dt>Retencion</dt><dd class="mono">18/18 [0.824, 1.000] · recall 1.000 · consistencia 36/36</dd>
<dt>Velocidad</dt><dd class="mono">p50 ~5-10s/claim (Atlas F2-9: ~5s)</dd>
<dt>Veredicto</dt><dd><b>Nos gana en recall.</b> Punto.</dd>
</dl></div>
<div class="card" id="c-bfail"><h2>Donde falla <span class="bad">citas 14% fieles</span></h2>
<dl class="kv">
<dt>Fidelidad pooled</dt><dd class="mono bad">9/63 = 0.143 [0.077, 0.250] (r1 5/31, r2 4/32)</dd>
<dt>Motivos</dt><dd class="mono">quote_not_found 34 · unit_mismatch 13 · numeric_mismatch 7</dd>
<dt>URLs</dt><dd class="mono">72/72 stated por el modelo, 0 browsed · falsos-C: D3 x2</dd>
<dt>Prueba offline</dt><dd class="big bad">0</dd>
</dl></div>
</div>
<p class="src">Fuentes: eval2-20261101-225539-report.json (70/72) + fidelity JSONs r1/r2 (9/63) · addendum F2_RECALL (ret 18/18).</p>
</section>

<section id="close">
<h1>Cierre: numeros F2 <span class="en">the baseline wins recall &mdash; and that's the point</span></h1>
<div class="banner lose">Atlas dev 0.611 / ret 0.667 vs baseline 1.000 &mdash; el baseline gana en recall.</div>
<table><tr><th>metrica</th><th class="n">Atlas F2</th><th class="n">baseline</th></tr>
<tr><td>recall DEV / retencion</td><td class="n">0.611 / 0.667</td><td class="n">1.000 / 1.000</td></tr>
<tr><td>exactitud decidida</td><td class="n">1.000 (22/22 + 12/12)</td><td class="n">0.972 (70/72)</td></tr>
<tr><td>citas fieles a la fuente</td><td class="n ok">verbatim por construccion</td><td class="n bad">0.143 (9/63)</td></tr>
<tr><td>falsos contradicted</td><td class="n ok">0</td><td class="n bad">2 (D3 x2)</td></tr>
<tr><td>prueba offline re-ejecutable</td><td class="n ok">si (bundle + tamper FAIL)</td><td class="n bad">no</td></tr>
<tr><td>dicho-vs-mostrado</td><td class="n ok">si (F6, F5, F11)</td><td class="n bad">no</td></tr>
<tr><td>latencia p50 / wall 36</td><td class="n">4.9s / ~78s (p95 39-41s: CUMPLE vs 60)</td><td class="n">~5s / &mdash;</td></tr>
<tr><td>ASR ES (WER / recall)</td><td class="n" colspan="2">0.03 / 41 de 41 (wer_report.json)</td></tr></table>
<div class="banner wedge">Nuestro wedge: paquete re-verificable + 0 falsos-contradicted + dicho-vs-mostrado. Todo medido, todo reproducible: pruebalo.</div>
<p class="src">Fuentes: f2recall-fix-report.json · addendum F2_RECALL · eval2-20261101-225539-report.json + fidelity r1/r2 · f2p95-final-report.json · wer_report.json.</p>
</section>
"""

PANELS_JS = """
function hl(id){ var el=document.getElementById(id); if(el) el.classList.add('hl'); }
function unhl(id){ var el=document.getElementById(id); if(el) el.classList.remove('hl'); }
function termScroll(frac){
  var t=document.getElementById('termout'); if(!t) return;
  t.scrollTop = frac * (t.scrollHeight - t.clientHeight);
}
"""


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def step_panels(verify_out: str) -> Path:
    """Genera panels.html con cifras reales + salida verify verbatim."""
    truth = {e["id"]: e for e in json.loads(TRUTH.read_text(encoding="utf-8"))}
    f3 = f3_evidence()
    lines = []
    for ln in verify_out.splitlines():
        cls = "pass" if "PASS" in ln or "OK" in ln else "info"
        lines.append(f'<span class="{cls}">{esc(ln)}</span>')
    body = PANELS_BODY.replace("{{F3_TEXT}}", esc(truth["F3-es"]["text"]))
    body = body.replace("{{F3_QUOTE}}", esc(f3["quote"]) + "&hellip;")
    body = body.replace("{{F3_URL}}", esc(f3["url"]))
    body = body.replace("{{F3_SHA}}", esc(f3["sha"]))
    body = body.replace("{{TERMINAL_OUT}}", "\n".join(lines))
    body = body.replace("{{BUNDLE_NAME}}", OUT_BUNDLE.name)
    html = ("<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>"
            "<title>Atlas Live - paneles demo</title><style>" + PANELS_CSS +
            "</style></head><body>" + body +
            "<script>" + PANELS_JS + "</script></body></html>")
    out = ASSETS / "panels.html"
    out.write_text(html, encoding="utf-8")
    print(f"[panels] {out}", flush=True)
    return out


# ---------------------------------------------------------------- server + record

def start_server() -> subprocess.Popen:
    srv = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "live.server.app:create_app",
         "--factory", "--port", str(PORT)],
        cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health",
                                        timeout=2) as r:
                if r.status == 200:
                    print(f"[server] up :{PORT} pid={srv.pid}", flush=True)
                    return srv
        except Exception:
            time.sleep(0.5)
    srv.terminate()
    raise RuntimeError("server no levanto")


def _record(browser, url: str, seg_dur: float, actions) -> Path:
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080},
                              record_video_dir=str(CLIPS),
                              record_video_size={"width": 1920,
                                                 "height": 1080})
    page = ctx.new_page()
    t0 = time.monotonic()
    try:
        page.goto(url, wait_until="load", timeout=30000)
        page.wait_for_timeout(800)
        actions(page, seg_dur)
        el = time.monotonic() - t0
        if el < seg_dur:
            page.wait_for_timeout(int((seg_dur - el) * 1000))
    finally:
        path = page.video.path() if page.video else None
        ctx.close()
    assert path and Path(path).is_file(), "clip no generado"
    return Path(path)


def ui_play(page, seek: float | None = None) -> None:
    if seek is not None:
        # Solo metadata: el static propio SI soporta Range, asi que el
        # seek directo pega (un wait de buffer colgaria el segmento).
        try:
            page.wait_for_function(
                "() => { const v = document.getElementById('player');"
                " return v && v.readyState >= 1; }", timeout=8000)
        except Exception:
            pass
    page.evaluate("""(seek) => {
      const v = document.getElementById('player');
      if (v) { v.muted = true; if (seek !== null) { try { v.currentTime = seek; } catch(e){} }
        v.play().catch(()=>{}); }
    }""", seek)


def ui_pin_es_video(page) -> None:
    """Fija el mp4 ES nativo en el <video> (el server replay no lo mapea)."""
    page.evaluate(
        "(src) => { const v = document.getElementById('player');"
        " if (v && !v.src) { v.src = src; } }",
        f"http://127.0.0.1:{MPORT}/{KEYNOTE_NAME}")


def ui_flash_141m(page) -> None:
    page.evaluate("""() => {
      const els = [...document.querySelectorAll('.claim')];
      const hit = els.find(e => (e.textContent||'').includes('141 million'))
        || els.find(e => (e.textContent||'').includes('cayó a'));
      if (hit) { hit.scrollIntoView({block:'center'}); hit.classList.remove('flash');
        void hit.offsetWidth; hit.classList.add('flash'); }
    }""")


def serve_range_forever(port: int = MPORT) -> None:
    """Sirve KEYNOTE_DIR con soporte Range (el seek de Chrome lo exige)."""

    class _H(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Accept-Ranges", "bytes")
            super().end_headers()

        def do_GET(self):  # noqa: N802
            rng = (self.headers.get("Range") or "").strip()
            m = re.match(r"bytes=(\d*)-(\d*)$", rng)
            if not m or not self.path.endswith(".mp4"):
                return super().do_GET()
            data = (KEYNOTE_DIR / KEYNOTE_NAME).read_bytes()
            n = len(data)
            s, e = m.group(1), m.group(2)
            start = int(s) if s else max(0, n - int(e or n))
            end = min(int(e) if e else n - 1, n - 1)
            if start >= n:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{n}")
                self.end_headers()
                return
            chunk = data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Range",
                             f"bytes {start}-{end}/{n}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)

        def log_message(self, *a):
            pass

    handler = functools.partial(_H, directory=str(KEYNOTE_DIR))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port),
                                         handler) as httpd:
        httpd.serve_forever()


def start_static() -> subprocess.Popen:
    """Sirve data/keynote_es/ por HTTP (solo para pinchar el <video>)."""
    srv = subprocess.Popen(
        [sys.executable, "-c",
         "from live.demo.build_video import serve_range_forever;"
         f" serve_range_forever({MPORT})"],
        cwd=str(REPO),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{MPORT}/{KEYNOTE_NAME}",
                    timeout=2) as r:
                if r.status == 200:
                    print(f"[static] up :{MPORT} pid={srv.pid}", flush=True)
                    return srv
        except Exception:
            time.sleep(0.25)
    srv.terminate()
    raise RuntimeError("static no levanto")


def step_record(durs: list[float], panels: Path) -> list[tuple[Path, float]]:
    from playwright.sync_api import sync_playwright
    segs = [d + PAD_S for d in durs]
    base = f"http://127.0.0.1:{PORT}"
    pan = panels.as_uri()
    clips: list[tuple[Path, float]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            args=["--autoplay-policy=no-user-gesture-required",
                  "--mute-audio"])
        try:
            def act_ui_hook(page, dur):
                ui_pin_es_video(page)
                page.wait_for_timeout(1500)
                ui_play(page)
            def act_pipe(page, dur):
                page.evaluate("document.getElementById('pipeline').scrollIntoView()")
            def act_ui_f3(page, dur):
                ui_pin_es_video(page)
                page.wait_for_timeout(1500)
                ui_play(page, seek=70.0)
                try:
                    page.get_by_text("ciento cuarenta y uno").first.wait_for(
                        timeout=25000)
                except Exception:
                    pass
                ui_flash_141m(page)
            def act_contra(page, dur):
                page.evaluate("document.getElementById('contradiction').scrollIntoView()")
                page.wait_for_timeout(int(dur * 300))
                page.evaluate("hl('c-f3')")
                page.wait_for_timeout(int(dur * 300))
                page.evaluate("unhl('c-f3'); hl('c-sc')")
            def act_term(page, dur):
                page.evaluate("document.getElementById('terminal').scrollIntoView()")
                page.evaluate("termScroll(0)")
                steps = 8
                for k in range(1, steps + 1):
                    page.wait_for_timeout(int(dur * 1000 / (steps + 1)))
                    page.evaluate(f"termScroll({k / steps})")
            def act_f6(page, dur):
                page.evaluate("document.getElementById('f6').scrollIntoView()")
                page.wait_for_timeout(int(dur * 500))
                page.evaluate("hl('c-f6')")
            def act_axes(page, dur):
                page.evaluate("document.getElementById('c-f5').scrollIntoView({block:'center'})")
                page.wait_for_timeout(int(dur * 400))
                page.evaluate("hl('c-f5')")
                page.wait_for_timeout(int(dur * 300))
                page.evaluate("unhl('c-f5'); document.getElementById('c-f11').scrollIntoView({block:'center'})")
                page.evaluate("hl('c-f11')")
            def act_recall(page, dur):
                page.evaluate("document.getElementById('recall').scrollIntoView()")
                page.wait_for_timeout(int(dur * 300))
                page.evaluate("hl('c-dev')")
                page.wait_for_timeout(int(dur * 300))
                page.evaluate("unhl('c-dev'); hl('c-ret')")
            def act_lat(page, dur):
                page.evaluate("document.getElementById('latency').scrollIntoView()")
                page.wait_for_timeout(int(dur * 300))
                page.evaluate("hl('c-wall')")
                page.wait_for_timeout(int(dur * 300))
                page.evaluate("unhl('c-wall'); hl('c-ee')")
            def act_base(page, dur):
                page.evaluate("document.getElementById('baseline').scrollIntoView()")
                page.wait_for_timeout(int(dur * 300))
                page.evaluate("hl('c-beq')")
                page.wait_for_timeout(int(dur * 300))
                page.evaluate("unhl('c-beq'); hl('c-bfail')")
            def act_panel(sid):
                def _fn(page, dur):
                    page.evaluate(f"document.getElementById('{sid}').scrollIntoView()")
                return _fn

            plan = [
                (f"{base}/?replay={ES_RUN}&rate=4", act_ui_hook),
                (f"{pan}#pipeline", act_pipe),
                (f"{base}/?replay={ES_RUN}&rate=8", act_ui_f3),
                (f"{pan}#contradiction", act_contra),
                (f"{pan}#terminal", act_term),
                (f"{pan}#f6", act_f6),
                (f"{pan}#axes", act_axes),
                (f"{pan}#recall", act_recall),
                (f"{pan}#latency", act_lat),
                (f"{pan}#baseline", act_base),
                (f"{pan}#close", act_panel("close")),
            ]
            for i, (url, actions) in enumerate(plan):
                print(f"[record] seg {i} {segs[i]:.1f}s :: {url[:80]}", flush=True)
                t0 = time.monotonic()
                clip = _record(browser, url, segs[i] + 1.5, actions)
                dst = CLIPS / f"seg_{i:02d}.webm"
                if dst.exists():
                    dst.unlink()
                shutil.move(str(clip), str(dst))
                clips.append((dst, segs[i]))
                print(f"[record] seg {i} ok ({time.monotonic()-t0:.0f}s wall)", flush=True)
        finally:
            browser.close()
    return clips


# ---------------------------------------------------------------- compose

def srt_ts(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, ms = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ass_ts(sec: float) -> str:
    cs = max(0, int(round(sec * 100)))
    h, r = divmod(cs, 360000)
    m, r = divmod(r, 6000)
    s, cs = divmod(r, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def wrap2(txt: str) -> str:
    """Parte en 2 lineas por el espacio central (ASS \\N)."""
    if len(txt) <= 52:
        return txt
    mid = len(txt) // 2
    spaces = [i for i, c in enumerate(txt) if c == " "]
    cut = min(spaces, key=lambda i: abs(i - mid))
    return txt[:cut] + "\\N" + txt[cut + 1:]


def step_audio_srt(durs: list[float]) -> tuple[Path, float]:
    """Concatena narracion + pads -> WAV 22050 mono; escribe narration_en.srt."""
    segs = []
    for i, d in enumerate(durs):
        with wave.open(str(WORK / f"narr_{i:02d}.wav"), "rb") as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, OUT_RATE)
            segs.append(w.readframes(w.getnframes()))
    rate = OUT_RATE
    silence = lambda s: b"\x00" * int(s * rate) * 2
    full = silence(LEAD_S)
    starts = []
    t = LEAD_S
    for i, raw in enumerate(segs):
        starts.append(t)
        full += raw + silence(PAD_S)
        t += durs[i] + PAD_S
    out = WORK / "narration_full.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(full)
    # SRT: 2 cues por bloque, reparto proporcional a chars.
    cues = []
    n = 0
    for i, d in enumerate(durs):
        c1, c2 = EN_CUES[i]
        w1 = len(c1) / (len(c1) + len(c2))
        mid = starts[i] + d * w1
        cues.append((starts[i], mid, c1))
        cues.append((mid, starts[i] + d, c2))
    with open(NARR_SRT, "w", encoding="utf-8") as fh:
        for k, (a, b, txt) in enumerate(cues, 1):
            fh.write(f"{k}\n{srt_ts(a)} --> {srt_ts(b)}\n{txt}\n\n")
    # ASS gemelo para incrustar (PlayRes real 1080p; SRT directo escala mal).
    ass = WORK / "en.ass"
    header = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n"
              "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
              "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
              "OutlineColour, BorderStyle, Outline, Shadow, Alignment, "
              "MarginL, MarginR, MarginV, Encoding\n"
              "Style: Default,DejaVu Sans,52,&H00FFFFFF,&H80000000,1,2,0,2,60,60,40,1\n\n"
              "[Events]\nFormat: Layer, Start, End, Style, Text\n")
    with open(ass, "w", encoding="utf-8") as fh:
        fh.write(header)
        for (a, b, txt) in cues:
            fh.write(f"Dialogue: 0,{ass_ts(a)},{ass_ts(b)},Default,{wrap2(txt)}\n")
    print(f"[audio] {out} {t:.1f}s · {NARR_SRT.name} {len(cues)} cues", flush=True)
    return out, t


def step_compose(clips: list[tuple[Path, float]], audio: Path,
                 total: float) -> Path:
    ff = ffmpeg()
    normed = []
    for i, (clip, seg_dur) in enumerate(clips):
        out = WORK / f"n_{i:02d}.mp4"
        r = run([ff, "-y", "-i", str(clip),
                 "-t", f"{seg_dur:.3f}",
                 "-vf", ("scale=1920:1080:force_original_aspect_ratio=decrease,"
                         "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"),
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-pix_fmt", "yuv420p", "-an", str(out)])
        assert out.is_file(), f"normalize seg {i} fallo: {r.stderr[-300:]}"
        normed.append(out)
    lst = WORK / "concat.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in normed), encoding="utf-8")
    ass_path = str(WORK / "en.ass").replace(":", "\\:").replace("'", "")
    r = run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-i", str(audio),
             "-vf", f"ass={ass_path}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
             "-ar", "48000", "-t", f"{total:.3f}", str(OUT_MP4)])
    assert OUT_MP4.is_file(), f"compose fallo: {r.stderr[-500:]}"
    r2 = run([ff, "-hide_banner", "-i", str(OUT_MP4)])
    print(f"[compose] {OUT_MP4} {OUT_MP4.stat().st_size} bytes", flush=True)
    for ln in (r2.stderr or "").splitlines():
        if "Duration" in ln or "Stream" in ln:
            print(f"[compose] {ln.strip()}", flush=True)
    return OUT_MP4


# ---------------------------------------------------------------- main

def main() -> int:
    skip_tts = "--skip-tts" in sys.argv
    skip_record = "--skip-record" in sys.argv
    WORK.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    CLIPS.mkdir(parents=True, exist_ok=True)
    assert PIPER.is_file(), "falta .venv/bin/piper"
    assert VOICE.is_file(), f"falta voz {VOICE}"
    if skip_tts:
        durs = [wav_dur(WORK / f"narr_{i:02d}.wav") for i in range(11)]
        print(f"[tts] reused, total {LEAD_S + sum(durs) + PAD_S * 11:.1f}s",
              flush=True)
    else:
        durs = step_tts()
    verify_out = step_bundle()
    step_frames()
    panels = step_panels(verify_out)
    if skip_record:
        clips = [(CLIPS / f"seg_{i:02d}.webm", d + PAD_S)
                 for i, d in enumerate(durs)]
        assert all(c.is_file() for c, _ in clips), "faltan clips grabados"
        print("[record] reused 11 clips", flush=True)
    else:
        srv = start_server()
        static = start_static()
        try:
            clips = step_record(durs, panels)
        finally:
            for s, name in ((srv, "server"), (static, "static")):
                s.terminate()
                try:
                    s.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    s.kill()
                print(f"[{name}] killed", flush=True)
    audio, total = step_audio_srt(durs)
    assert total <= MAX_TOTAL_S, f"total {total:.1f}s excede cap"
    step_compose(clips, audio, total)
    print(f"[done] {OUT_MP4} dur={total:.1f}s bytes={OUT_MP4.stat().st_size}",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
