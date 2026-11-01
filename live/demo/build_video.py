#!/usr/bin/env python3
"""M9b — Atlas Live demo video builder (rama live, checkout compartido).

Pipeline todo local, $0:
  1. TTS piper (voz LJSpeech, dominio publico, misma que M1c) desde
     live/demo/narration_es.txt -> 11 WAV (22050 Hz mono).
  2. Exporta bundle real (offline) desde la corrida M2d
     (data/runs/verify-20261101-212343*) -> data/demo/atlas-live-bundle.zip
     y lo re-verifica con live/bundle/verify_bundle.py (captura real stdout).
  3. Extrae frames reales del keynote (M3 picks) con imageio-ffmpeg.
  4. Genera panels.html (cifras de summaries JSON + M3/M5/M7 citadas).
  5. Levanta el server replay (uvicorn, puerto 8897) y graba con playwright
     recordVideo 1920x1080: UI replay + paneles + terminal. Mata el server.
  6. ffmpeg compone data/demo/atlas-live-demo.mp4 (1080p, <=3:00) con
     narracion + SRT EN incrustados (live/demo/narration_en.srt).

Uso:
  cd evidence-platform && .venv/bin/python live/demo/build_video.py

No hace llamadas live (QA de transcripcion va aparte). No toca git.
"""

from __future__ import annotations

import json
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
PAD_S = 0.8          # silencio tras cada bloque
LEAD_S = 0.5         # silencio inicial
MAX_TOTAL_S = 179.0  # hard cap (requerido <=180)

VOICE = Path("/tmp/piper_voices_lj/en_US-ljspeech-medium.onnx")
PIPER = REPO / ".venv" / "bin" / "piper"

M2D_SUMMARY = REPO / "data/runs/verify-20261101-212343.jsonl.summary.json"
M2D_LOG = REPO / "data/runs/verify-20261101-212343.jsonl"
M2D_DB = REPO / "data/runs/verify-20261101-212343.jsonl.db"
AUDIT_JSON = REPO / "data/runs/audit-20261101-213747.json"
ABLATION_JSON = REPO / "data/runs/ablation-20261101-214721-report.json"
TRUTH = REPO / "live/material/truth.json"
KEYNOTE = REPO / "data/keynote/keynote_en.mp4"

# Cifras M3/M5/M7 (sin artefacto JSON; fuente citada en cada panel).
M3 = {"f6_said": 40.0, "f6_shown": 0.97, "f6_d": 0.976,
      "f5_axis": "27-30", "f5_vals": "27.8->29.3", "f5_cue": "doubled",
      "f11_cue": "larger than", "t10_d": 0.0}
M5 = {"t7_attack_usd": 0.0041, "t7_card_usd": 0.0026,
      "t7_query": "Japan nominal GDP 2024 IMF World Bank trillion dollars"}
M7 = {"t2_t": 13.3, "f3_t": 70.2, "gap": 56.9, "a": "1.41e9",
      "b": "1.41e8", "rel_diff": 0.9}

EN_CUES = [
    ("One speaker says two things that can't both be true, 57 seconds apart.",
     "Atlas Live catches it live \u2014 caught live \u2014 and shows the proof, not just the verdict."),
    ("Atlas Live listens to and watches a live video, extracts every checkable claim,",
     "investigates it with Muse agents on the open web; deterministic code decides: the model proposes, code disposes."),
    ("Minute one-ten. The speaker says China fell to 141 million people.",
     "Three researchers hit the open web while the video keeps running."),
    ("Contradicted. The World Bank says 1.409 billion \u2014 ten times more.",
     "And at minute zero he had said 1.41 billion: self-contradiction verified by code, rel diff of 0.9."),
    ("And you don't have to take our word for it. Every run exports a proof bundle",
     "anyone can re-run with no network and no model: all pass. Literal quote, snapshot hash, reproduced verdict."),
    ("But there's something better. The speaker says forty percent, and his own chart shows 0.97.",
     "The chart contradicts the speaker. Vision reads the frame; code decides."),
    ("Same trick on two slides: a truncated axis turning five percent into a doubling,",
     "and a broken scale putting India above the United States. Two automatic flags."),
    ("Every supporting verdict is attacked by a skeptic hunting live counter-evidence.",
     "Here it found nothing \u2014 honest abstention \u2014 and the card publishes in Spanish and English."),
    ("Twelve internal canaries, disjoint from the video, through the real pipeline:",
     "perfect accuracy on decided claims, with an honest Wilson interval. And zero false contradicts."),
    ("The limits, unvarnished: 8 of 31 claims decided correctly,",
     "one false claim accepted 0.14 tolerance points from the line, and a 78-second median per claim."),
    ("Model proposes, code disposes, skeptic attacks, auditor measures. The honest ablation: the baseline wins, 31 of 31.",
     "Our wedge: re-verifiable proof, zero false contradicts, and said-vs-shown."),
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
    picks = {"slide6.png": 95.56, "slide7.png": 115.56, "slide10.png": 173.21}
    for name, t in picks.items():
        out = ASSETS / name
        if out.is_file():
            continue
        r = run([ff, "-y", "-ss", str(t), "-i", str(KEYNOTE),
                 "-frames:v", "1", str(out)])
        assert out.is_file(), f"frame {name} fallo: {r.stderr[-300:]}"
        print(f"[frames] {name} @ {t}s", flush=True)


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
<div class="pbox"><b>video</b><small>keynote 3:29</small></div><div class="parr">&rarr;</div>
<div class="pbox"><b>ASR</b><small>muse-voice-transcribe realtime</small></div><div class="parr">&rarr;</div>
<div class="pbox"><b>claims</b><small>detector de afirmaciones</small></div><div class="parr">&rarr;</div>
<div class="pbox"><b>agentes x3</b><small>Muse + web abierta</small></div><div class="parr">&rarr;</div>
<div class="pbox code"><b>codigo decide</b><small>verificador determinista</small></div><div class="parr">&rarr;</div>
<div class="pbox"><b>tarjeta ES/EN</b><small>por WebSocket en vivo</small></div>
</div>
<div class="grid2">
<div class="card"><h2>Ojos: vista</h2>Lee graficos y ejes en los frames; codigo cruza <b>dicho-vs-mostrado</b> (tol. 2%).</div>
<div class="card"><h2>Guardianes</h2>Esceptico ataca cada <span class="mono">supported</span>; auditor mide 12 canarios; longctx caza auto-contradicciones.</div>
</div>
<p class="src">Fuente: live/README.md, live/M1_E2E.md (bus + /ws). Lema: the model proposes, code disposes.</p>
</section>

<section id="contradiction">
<h1>La falsa, atrapada dos veces <span class="en">F3 caught: evidence + self-contradiction</span></h1>
<p class="sub">Minuto 1:10 del keynote: &laquo;{{F3_TEXT}}&raquo;</p>
<div class="grid2">
<div class="card" id="c-f3"><h2>F3-en &rarr; <span class="bad">contradicted</span> <span class="mono info">(3 paquetes)</span></h2>
<dl class="kv">
<dt>Banco Mundial</dt><dd class="mono">{{F3_QUOTE}}</dd>
<dt>URL</dt><dd class="mono acc">{{F3_URL}}</dd>
<dt>sha256</dt><dd class="mono">{{F3_SHA}}&hellip;</dd>
<dt>Ref 2024</dt><dd>China = <b>1,408,975,000</b> (no 141M: error x10)</dd>
</dl></div>
<div class="card" id="c-sc"><h2><span class="mono">speaker.contradiction</span> <span class="ok">verificada por codigo</span></h2>
<dl class="kv">
<dt>A (t=13.3s, T2)</dt><dd>&laquo;China is home to <b>1.41 billion</b> people&raquo;</dd>
<dt>B (t=70.2s, F3)</dt><dd>&laquo;fallen to just <b>141 million</b> people&raquo;</dd>
<dt>Choque</dt><dd class="mono">1.41e9 vs 1.41e8 count, gap 56.9s, <b>rel_diff=0.9</b></dd>
</dl></div>
</div>
<p class="src">Fuentes: verify-20261101-212343.jsonl.summary.json (F3 contradicted, 3 paquetes) · live/M7.md (par T2/F3, mismo par en 2 corridas).</p>
</section>

<section id="terminal">
<h1>Prueba re-verificable <span class="en">don't trust us, re-run it</span></h1>
<p class="sub">Cada corrida exporta un ZIP que cualquiera re-ejecuta sin red ni modelo.</p>
<div id="term"><div class="cmd mono">demo@atlas:~/evidence-platform$ python live/bundle/verify_bundle.py {{BUNDLE_NAME}}</div>
<div id="termout">{{TERMINAL_OUT}}</div></div>
<p class="src">Salida real, verbatim, de {{BUNDLE_NAME}} (31 claims M2d) · motor live.verifier · verificador: live/bundle/verify_bundle.py.</p>
</section>

<section id="f6">
<h1>El grafico contradice al orador <span class="en">said 40.0 vs shown 0.97</span></h1>
<p class="sub">Slide 7 &laquo;Percent Games&raquo; (frame real t=115.6s): barra honesta de 0.97% mientras el habla dice &laquo;grew by nearly 40 percent&raquo;.</p>
<div class="vs">
<div style="flex:1.4"><img class="slide" src="slide7.png" alt="slide 7"></div>
<div class="shown card" id="c-f6"><h2><span class="mono">visual.mismatch</span></h2>
<p class="big">said <span class="bad">40.0</span> / shown <span class="ok">0.97</span></p>
<p class="mono">d = 0.976 &gt; tol 2% &rarr; mismatch</p>
<p>Vision lee el frame (schema estricto); <b>codigo</b> cruza dicho-vs-mostrado.</p></div>
</div>
<p class="src">Fuente: live/M3.md (F6-en, match T10 d=0.0 como control honesto).</p>
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
<p class="src">Fuente: live/M3.md (F5 + F11 flags; F12 cherry-pick fuera de contrato: solo notas de vision).</p>
</section>

<section id="skeptic">
<h1>El esceptico <span class="en">we attack our own verdicts</span></h1>
<p class="sub">Cada <span class="mono">supported</span> es atacado con contra-evidencia real antes de publicarse.</p>
<div class="grid2">
<div class="card" id="c-t7"><h2>T7-en &rarr; <span class="ok">supported</span> <span class="mono info">[numeric_match]</span></h2>
<p>&laquo;Jap&oacute;n PIB 4.19T en 2024&raquo; vs quote 4.2T: 0.24% off.</p></div>
<div class="card" id="c-atk"><h2>Ataque real: <span class="mono">skeptic.note</span> <span class="info">(honest abstention)</span></h2>
<dl class="kv">
<dt>Contra-query</dt><dd class="mono">{{M5_QUERY}}</dd>
<dt>Docs</dt><dd>1 fetched &rarr; extract abstiene (claim verdadero, nada que refutar)</dd>
<dt>Costo</dt><dd class="mono">$0.0041 ataque + $0.0026 tarjeta = $0.0067</dd>
<dt>Tarjeta</dt><dd>ES+EN model-drafted, citas confinadas, <span class="mono">fallback=False</span></dd>
</dl></div>
</div>
<p class="src">Fuente: live/M5.md (T7 live; GATE A+B publican challenge solo con contra-prueba usable).</p>
</section>

<section id="auditor">
<h1>Fiabilidad medida, no prometida <span class="en">12 canaries, zero false contradicts</span></h1>
<p class="sub">Doce canarios internos (MEX/IDN/NGA/GBR/ITA/CAN, disjuntos del video) por el pipeline real.</p>
<div class="grid2">
<div class="card"><h2>Panel auditor</h2>
<p class="big">accuracy <span class="ok">1.0</span> <span class="info mono" style="font-size:19px">Wilson 95% [0.6457, 1.0]</span></p>
<dl class="kv">
<dt>Decididos</dt><dd class="mono">7/12 correctos (6 TRUE + F1)</dd>
<dt>Abstencion</dt><dd class="mono">0.4167 (5/12, toda del lado FALSE)</dd>
<dt>Falsos contradicted</dt><dd class="big ok">0</dd>
<dt>Costo</dt><dd class="mono">$0.2914 · 58 llamadas · wall 210s</dd>
</dl></div>
<div class="card"><h2>Matriz canarios</h2>
<table><tr><th>expected \\ verdict</th><th class="n">supported</th><th class="n">contradicted</th><th class="n">unverif.</th></tr>
<tr><td>true (6)</td><td class="n ok">6</td><td class="n">0</td><td class="n">0</td></tr>
<tr><td>false (6)</td><td class="n">0</td><td class="n ok">1 (F1)</td><td class="n warn">5</td></tr></table>
<p class="info">Asimetria honesta: el swarm devuelve 0 paquetes ante falsedades (vacio honesto, causa A1).</p></div>
</div>
<p class="src">Fuente: audit-20261101-213747.json (panel + matriz verbatim).</p>
</section>

<section id="limits">
<h1>Limites honestos <span class="en">8/31, one miss, 78s median</span></h1>
<p class="sub">31 claims EN chequeables del keynote (16 true + 15 false). Abstenerse es virtud cuando la evidencia no alcanza.</p>
<div class="grid2">
<div class="card"><h2>Matriz M2d</h2>
<table><tr><th>expected \\ verdict</th><th class="n">supported</th><th class="n">contradicted</th><th class="n">unverif.</th></tr>
<tr><td>true (16)</td><td class="n ok">7</td><td class="n">0</td><td class="n warn">9</td></tr>
<tr><td>false (15)</td><td class="n bad">1 (F14)</td><td class="n ok">1 (F3)</td><td class="n warn">13</td></tr></table>
<p class="mono">decididos correctos: 8/31 · precision decidida: 8/9 (89%)</p></div>
<div class="card"><h2>Costo del recall</h2>
<dl class="kv">
<dt>F14</dt><dd>WLD 2020 7.85B vs hito 8B: rel 1.86% &lt; 2% &rarr; <span class="mono">supported</span> (a 0.14pp de la linea; year-swap fuera de alcance)</dd>
<dt>Latencia</dt><dd class="mono">p50 78.4s / p95 90.0s por claim</dd>
<dt>Costo</dt><dd class="mono">$0.96 · 155 llamadas · wall 611s</dd>
<dt>F6</dt><dd>varianza del swarm: contradicted&rarr;unverifiable entre corridas (0 paquetes)</dd>
</dl></div>
</div>
<p class="src">Fuente: verify-20261101-212343.jsonl.summary.json (verbatim).</p>
</section>

<section id="close">
<h1>Cierre: ablacion honesta <span class="en">the baseline wins &mdash; and that's the point</span></h1>
<div class="banner lose">Atlas 8/9 vs baseline run1 30/31 vs run2 31/31 &mdash; el baseline gana en todo lo medido.</div>
<table><tr><th>metrica</th><th class="n">Atlas M2d</th><th class="n">baseline run1</th><th class="n">baseline run2</th></tr>
<tr><td>accuracy (Wilson 95%)</td><td class="n">0.889 [0.565, 0.980]</td><td class="n">0.968 [0.838, 0.994]</td><td class="n">1.000 [0.890, 1.000]</td></tr>
<tr><td>alucinaciones FALSE&rarr;contra</td><td class="n">1 (F3)</td><td class="n">15/15</td><td class="n">15/15</td></tr>
<tr><td>falsos contradicted (P0)</td><td class="n ok">0</td><td class="n bad">1 (T7)</td><td class="n ok">0</td></tr>
<tr><td>costo/claim</td><td class="n">$0.0310</td><td class="n">$0.0355</td><td class="n">$0.0281</td></tr>
<tr><td>latencia p50</td><td class="n">78.4s</td><td class="n">4.5s</td><td class="n">4.2s</td></tr>
<tr><td>consistencia r1-vs-r2</td><td class="n">&mdash;</td><td class="n" colspan="2">30/31 = 0.968 (difiere T7)</td></tr></table>
<div class="banner wedge">Nuestro wedge: paquete re-verificable + 0 falsos-contradicted + dicho-vs-mostrado. Codigo y pruebas reproducibles: pruebalo.</div>
<p class="src">Fuente: ablation-20261101-214721-report.json (verbatim; Atlas no lidera ninguna metrica; caveat: urls baseline model-stated, 0/62 validadas).</p>
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
    body = PANELS_BODY.replace("{{F3_TEXT}}", esc(truth["F3-en"]["text"]))
    body = body.replace("{{F3_QUOTE}}", esc(f3["quote"]) + "&hellip;")
    body = body.replace("{{F3_URL}}", esc(f3["url"]))
    body = body.replace("{{F3_SHA}}", esc(f3["sha"]))
    body = body.replace("{{TERMINAL_OUT}}", "\n".join(lines))
    body = body.replace("{{BUNDLE_NAME}}", OUT_BUNDLE.name)
    body = body.replace("{{M5_QUERY}}", esc(M5["t7_query"]))
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
    page.evaluate("""(seek) => {
      const v = document.getElementById('player');
      if (v) { v.muted = true; if (seek !== null) { try { v.currentTime = seek; } catch(e){} }
        v.play().catch(()=>{}); }
    }""", seek)


def ui_flash_141m(page) -> None:
    page.evaluate("""() => {
      const els = [...document.querySelectorAll('.claim')];
      const hit = els.find(e => (e.textContent||'').includes('141 million'));
      if (hit) { hit.scrollIntoView({block:'center'}); hit.classList.remove('flash');
        void hit.offsetWidth; hit.classList.add('flash'); }
    }""")


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
                ui_play(page)
            def act_pipe(page, dur):
                page.evaluate("document.getElementById('pipeline').scrollIntoView()")
            def act_ui_f3(page, dur):
                ui_play(page, seek=70.0)
                try:
                    page.get_by_text("141 million").first.wait_for(timeout=25000)
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
            def act_skep(page, dur):
                page.evaluate("document.getElementById('skeptic').scrollIntoView()")
                page.wait_for_timeout(int(dur * 300))
                page.evaluate("hl('c-t7')")
                page.wait_for_timeout(int(dur * 300))
                page.evaluate("unhl('c-t7'); hl('c-atk')")
            def act_panel(sid):
                def _fn(page, dur):
                    page.evaluate(f"document.getElementById('{sid}').scrollIntoView()")
                return _fn

            plan = [
                (f"{base}/?replay=run-m1e-heuristic&rate=4", act_ui_hook),
                (f"{pan}#pipeline", act_pipe),
                (f"{base}/?replay=run-m1e-heuristic&rate=8", act_ui_f3),
                (f"{pan}#contradiction", act_contra),
                (f"{pan}#terminal", act_term),
                (f"{pan}#f6", act_f6),
                (f"{pan}#axes", act_axes),
                (f"{pan}#skeptic", act_skep),
                (f"{pan}#auditor", act_panel("auditor")),
                (f"{pan}#limits", act_panel("limits")),
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
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 22050)
            segs.append(w.readframes(w.getnframes()))
    rate = 22050
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
        try:
            clips = step_record(durs, panels)
        finally:
            srv.terminate()
            try:
                srv.wait(timeout=10)
            except subprocess.TimeoutExpired:
                srv.kill()
            print("[server] killed", flush=True)
    audio, total = step_audio_srt(durs)
    assert total <= MAX_TOTAL_S, f"total {total:.1f}s excede cap"
    step_compose(clips, audio, total)
    print(f"[done] {OUT_MP4} dur={total:.1f}s bytes={OUT_MP4.stat().st_size}",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
