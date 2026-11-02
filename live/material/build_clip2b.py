#!/usr/bin/env python3
"""Build clip2b: synthetic 100%-own science clip for Atlas Live (F2-10).

Sistema solar en español, ~8 afirmaciones numéricas verificables, voz
sintética carlfm (dominio público), slides matplotlib, ffmpeg:

  - data/clips/clip2b.mp4 (60-90 s, 1280x720)
  - data/clips/clip2b.srt (1 entry/slide)
  - data/clips/truth2b.json ([{id,texto,t,esperado,ref_url}])
  - intermediates under data/clips/work_2b/ and data/clips/charts_2b/

All local, $0. Never touches YouTube or any network at build time.
Pattern reused from build_keynote_es.py (TTS+ffmpeg helpers via bk).

Cifras verificadas contra las NASA Planetary Fact Sheets (2026-11-02):
tabla maestra https://nssdc.gsfc.nasa.gov/planetary/factsheet/index.html
(metric; distances 10^6 km, diameters km, orbital periods days) y las
fichas por planeta citadas en cada claim (ref_url).

Usage (repo root): .venv/bin/python live/material/build_clip2b.py
  .venv/bin/python live/material/build_clip2b.py --score-transcript f.txt

Env knobs:
  VOICE_ES  path to the es_ES piper .onnx (default /tmp/piper_voices_es/...)
  PIPER_BIN path to the piper CLI (default .venv/bin/piper)
"""
import argparse
import json
import os
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_keynote as bk  # noqa: E402  (pipeline helpers, read-only)

ROOT = bk.ROOT
OUTDIR = os.path.join(ROOT, "data", "clips")
WORK = os.path.join(OUTDIR, "work_2b")
CHARTS = os.path.join(OUTDIR, "charts_2b")

PIPER_BIN = os.environ.get("PIPER_BIN", bk.PIPER_BIN)
VOICE_ES = os.environ.get(
    "VOICE_ES", "/tmp/piper_voices_es/es_ES-carlfm-x_low.onnx")

FINAL_BASENAME = "clip2b"
MIN_DUR, MAX_DUR = 60.0, 90.0

NASA = "https://nssdc.gsfc.nasa.gov/planetary/factsheet/"

# ----------------------------------------------------------------------------
# CLAIMS: single source of truth. Texto = oración literal narrada (dígitos
# estilo ES: coma decimal, sin separador de miles). Todas "true" (clip de
# ciencia honesto) con ref_url a la ficha NASA de cada planeta.
# Valores copiados de la tabla métrica (index.html, 2026-11-02):
#   distancias (10^6 km): Mercurio 57.9, Tierra 149.6, Júpiter 778.5
#   diámetros (km): Venus 12,104, Marte 6792, Saturno 120,536
#   periodos (días): Marte 687.0, Júpiter 4331
# ----------------------------------------------------------------------------
CLAIMS = [
    {"id": "2b-1",
     "texto": "Mercurio orbita a 57,9 millones de kilómetros del Sol.",
     "esperado": "true",
     "ref_url": NASA + "mercuryfact.html"},
    {"id": "2b-2",
     "texto": "La Tierra está a 149,6 millones de kilómetros del Sol.",
     "esperado": "true",
     "ref_url": NASA + "earthfact.html"},
    {"id": "2b-3",
     "texto": "Júpiter orbita a 778,5 millones de kilómetros del Sol.",
     "esperado": "true",
     "ref_url": NASA + "jupiterfact.html"},
    {"id": "2b-4",
     "texto": "Venus mide 12104 kilómetros de diámetro.",
     "esperado": "true",
     "ref_url": NASA + "venusfact.html"},
    {"id": "2b-5",
     "texto": "Marte mide 6792 kilómetros de diámetro.",
     "esperado": "true",
     "ref_url": NASA + "marsfact.html"},
    {"id": "2b-6",
     "texto": "Saturno mide 120536 kilómetros de diámetro.",
     "esperado": "true",
     "ref_url": NASA + "saturnfact.html"},
    {"id": "2b-7",
     "texto": "Marte tarda 687 días en completar su órbita alrededor del Sol.",
     "esperado": "true",
     "ref_url": NASA + "marsfact.html"},
    {"id": "2b-8",
     "texto": "Júpiter tarda 4331 días en completar su órbita alrededor del Sol.",
     "esperado": "true",
     "ref_url": NASA + "jupiterfact.html"},
]

# Relleno (no afirmaciones; sin cifras nuevas): contexto cualitativo.
FILLER = {
    "intro": ("Bienvenidos a este paseo por el sistema solar. "
              "Todas las cifras que escucharán provienen de las fact sheets "
              "planetarias de la NASA. El sistema solar tiene ocho planetas "
              "girando alrededor de una estrella."),
    "dist lead": ("Empecemos con las distancias al Sol, medidas en millones "
                  "de kilómetros. Noten cuánto más lejos está Júpiter que "
                  "la Tierra."),
    "diam lead": ("Pasemos a los tamaños, medidos como diámetro en el ecuador "
                 "de cada planeta. Saturno es mucho más ancho que la Tierra."),
    "per lead": ("Y ahora los años de cada planeta, o sea el tiempo que tarda "
                "su órbita completa. El año de Júpiter dura mucho más que "
                "el nuestro."),
    "outro": ("Estas ocho cifras describen a nuestros vecinos más cercanos. "
              "Pueden comprobar cada dato en la ficha de cada planeta. "
              "Gracias por escuchar este atlas en miniatura."),
}

SLIDES = [
    {"n": 1, "chart": None, "claims": [],
     "title": "Un paseo por el sistema solar",
     "bullets": ["8 planetas, 1 estrella", "Cifras: NASA fact sheets",
                 "Voz sintética (dominio público)"],
     "lead": FILLER["intro"]},
    {"n": 2, "chart": "distances", "claims": ["2b-1", "2b-2", "2b-3"],
     "title": "Distancias al Sol",
     "bullets": ["Mercurio: 57,9 millones de km",
                 "Tierra: 149,6 millones de km",
                 "Júpiter: 778,5 millones de km"],
     "lead": FILLER["dist lead"]},
    {"n": 3, "chart": "diameters", "claims": ["2b-4", "2b-5", "2b-6"],
     "title": "Diámetros ecuatoriales",
     "bullets": ["Venus: 12104 km", "Marte: 6792 km",
                 "Saturno: 120536 km"],
     "lead": FILLER["diam lead"]},
    {"n": 4, "chart": None, "claims": ["2b-7", "2b-8"],
     "title": "Años planetarios",
     "bullets": ["Marte: 687 días por órbita",
                 "Júpiter: 4331 días por órbita"],
     "lead": FILLER["per lead"]},
    {"n": 5, "chart": None, "claims": [],
     "title": "Gracias",
     "bullets": ["8 cifras, 8 fichas NASA", "Atlas Live, clip sintético 2b"],
     "lead": FILLER["outro"]},
]

CLAIM_BY_ID = {c["id"]: c for c in CLAIMS}


def narration(slide):
    """Full TTS text for a slide: lead-in + literal claim sentences."""
    parts = [slide["lead"]] + [CLAIM_BY_ID[c]["texto"] for c in slide["claims"]]
    return " ".join(parts)


# ----------------------------------------------------------------------------
# Charts: honest linear bars (clip de ciencia, sin trucos visuales).
# ----------------------------------------------------------------------------
def make_charts():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(CHARTS, exist_ok=True)
    out = {}

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["Mercurio", "Tierra", "Júpiter"], [57.9, 149.6, 778.5],
           color=["#94a3b8", "#38bdf8", "#f5b301"])
    ax.set_ylim(0, 900)
    ax.set_ylabel("Millones de km al Sol")
    ax.set_title("Distancia al Sol (NASA)")
    for x, v in (("Mercurio", 57.9), ("Tierra", 149.6), ("Júpiter", 778.5)):
        ax.text(x, v + 15, f"{v}", ha="center", fontsize=11)
    p = os.path.join(CHARTS, "chart_2b_distances.png")
    fig.tight_layout()
    fig.savefig(p, dpi=100)
    plt.close(fig)
    out["distances"] = p

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["Venus", "Marte", "Saturno"], [12104, 6792, 120536],
           color=["#f0abfc", "#ef4444", "#f5b301"])
    ax.set_ylim(0, 140000)
    ax.set_ylabel("Diámetro ecuatorial (km)")
    ax.set_title("Tamaño de los planetas (NASA)")
    for x, v in (("Venus", 12104), ("Marte", 6792), ("Saturno", 120536)):
        ax.text(x, v + 2500, f"{v}", ha="center", fontsize=11)
    p = os.path.join(CHARTS, "chart_2b_diameters.png")
    fig.tight_layout()
    fig.savefig(p, dpi=100)
    plt.close(fig)
    out["diameters"] = p

    return out


# ----------------------------------------------------------------------------
# Slide PNGs (layout bk, kicker propio 2b).
# ----------------------------------------------------------------------------
def render_slide_png(slide, chart_paths):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg

    n = slide["n"]
    fig = plt.figure(figsize=(bk.WIDTH / bk.DPI, bk.HEIGHT / bk.DPI), dpi=bk.DPI)
    fig.patch.set_facecolor(bk.BG)
    has_chart = bool(slide["chart"])

    fig.text(0.06, 0.96, slide["title"], color=bk.ACCENT,
             fontsize=32, fontweight="bold", va="top", ha="left")
    fig.text(0.06, 0.845, "ATLAS CLIP 2b  \u2022  Sistema solar (NASA)  "
             "\u2022  Sint\u00e9tico ES",
             color=bk.MUTED, fontsize=13, va="top", ha="left")

    y = 0.71
    for b in slide["bullets"]:
        wrapped = textwrap.wrap(b, width=34 if has_chart else 60)
        fig.text(0.06, y, "\u2022  " + wrapped[0], color=bk.FG,
                 fontsize=21, va="top", ha="left")
        y -= 0.075
        for cont in wrapped[1:]:
            fig.text(0.085, y, cont, color=bk.FG, fontsize=21, va="top", ha="left")
            y -= 0.07
        y -= 0.02

    fig.text(0.06, 0.045, f"Slide {n} / {len(SLIDES)}", color=bk.MUTED,
             fontsize=13)
    fig.text(0.94, 0.045, "2b", color=bk.MUTED, fontsize=13, ha="right")

    if has_chart:
        img = mpimg.imread(chart_paths[slide["chart"]])
        ax = fig.add_axes([0.55, 0.12, 0.40, 0.62])
        ax.imshow(img)
        ax.axis("off")
        for spine in ax.spines.values():
            spine.set_visible(False)

    p = os.path.join(WORK, f"slide_2b_{n:02d}.png")
    fig.savefig(p, dpi=bk.DPI, facecolor=bk.BG)
    plt.close(fig)
    return p


# ----------------------------------------------------------------------------
# TTS (piper CLI, voz carlfm dominio público) + ensamblado ffmpeg.
# ----------------------------------------------------------------------------
def tts(text, wav_path):
    cmd = [PIPER_BIN, "--model", VOICE_ES, "--output_file", wav_path]
    r = subprocess.run(cmd, input=text.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(f"piper failed: {r.stderr.decode()[-500:]}")
    return wav_path


def write_truth2b(timings):
    """truth2b.json: [{id,texto,t,esperado,ref_url}], t = inicio del slide."""
    t0_by_slide = {s["n"]: t0 for s, (t0, _t1) in zip(SLIDES, timings)}
    rows = []
    for slide in SLIDES:
        for cid in slide["claims"]:
            c = CLAIM_BY_ID[cid]
            rows.append({"id": c["id"], "texto": c["texto"],
                         "t": round(t0_by_slide[slide["n"]], 2),
                         "esperado": c["esperado"], "ref_url": c["ref_url"]})
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)) == len(CLAIMS), "truth2b ids mismatch"
    p = os.path.join(OUTDIR, "truth2b.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    return p, rows


def score_transcript_file(path):
    """Offline ($0) ASR-recall of a saved transcript vs CLAIMS (F2-4 method)."""
    import measure_wer as mw  # noqa: E402  (same dir; offline helpers)

    with open(path, encoding="utf-8") as f:
        transcript = f.read().strip()
    texts = [c["texto"] for c in CLAIMS]
    rec = mw.asr_recall(texts, transcript)
    print(f"ASR-recall: {rec['recall']:.4f} "
          f"({len(rec['hits'])}/{rec['n']})")
    for miss in rec["misses"]:
        print(f"  MISS: {miss[:110]}")
    out = os.path.join(OUTDIR, "clip2b_recall.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"transcript": path, "recall": rec["recall"],
                   "n": rec["n"], "misses": rec["misses"]}, f,
                  ensure_ascii=False, indent=2)
    print(f"report: {out}")
    return rec["recall"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build synthetic science clip 2b")
    ap.add_argument("--score-transcript", default=None, metavar="F.TXT",
                    help="offline $0 recall of a saved transcript vs CLAIMS")
    args = ap.parse_args(argv)
    if args.score_transcript:
        score_transcript_file(args.score_transcript)
        return 0

    os.makedirs(WORK, exist_ok=True)
    os.makedirs(OUTDIR, exist_ok=True)
    assert os.path.exists(PIPER_BIN), f"missing piper: {PIPER_BIN}"
    assert os.path.exists(VOICE_ES + ".json"), f"missing voice: {VOICE_ES}"
    ff = bk.ffmpeg_bin()

    print("== charts ==", flush=True)
    chart_paths = make_charts()
    for k, v in chart_paths.items():
        print(f"  {k}: {v} ({os.path.getsize(v)} bytes)")

    print("== slides+tts [2b] ==", flush=True)
    timings, texts, segs = [], [], []
    t = 0.0
    for slide in SLIDES:
        n = slide["n"]
        png = render_slide_png(slide, chart_paths)
        wav = os.path.join(WORK, f"narr_2b_{n:02d}.wav")
        tts(narration(slide), wav)
        info = bk.wav_info(wav)
        assert info["frames"] > 0, f"empty wav: {wav}"
        assert info["rms"] > 50, f"silent wav: {wav} rms={info['rms']:.1f}"
        dur = info["dur"] + bk.PAD_AFTER_AUDIO
        print(f"  slide {n:02d}: wav {info['bytes']} bytes, "
              f"{info['dur']:.1f}s audio, rms={info['rms']:.0f} "
              f"-> seg {dur:.1f}s")
        seg = os.path.join(WORK, f"seg_2b_{n:02d}.mp4")
        bk.build_segment(ff, png, wav, dur, seg)
        timings.append((t, t + dur))
        t += dur
        texts.append(narration(slide))
        segs.append(seg)

    final = os.path.join(OUTDIR, f"{FINAL_BASENAME}.mp4")
    lst = os.path.join(WORK, "concat_%s.txt" % os.path.basename(final))
    with open(lst, "w") as f:
        for s in segs:
            f.write(f"file '{s}'\n")
    bk.run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", lst, "-c", "copy", "-movflags", "+faststart", final])
    bk.write_srt(os.path.join(OUTDIR, f"{FINAL_BASENAME}.srt"), timings, texts)
    tp, rows = write_truth2b(timings)
    print(f"  VIDEO {final}: {t:.1f}s total")
    print(f"  TRUTH {tp}: {len(rows)} claims")
    assert MIN_DUR <= t <= MAX_DUR, f"duration {t:.1f}s outside 60-90 s!"
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
