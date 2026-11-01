#!/usr/bin/env python3
"""Build the Atlas Live keynote with a NATIVE Spanish voice (F2-4).

Reuses the ES script from build_keynote.py (imports SLIDES + narration,
never duplicates the script) and regenerates charts + slide PNGs + TTS
with the native es_ES piper voice (see LICENSES_VOZ.md) + ffmpeg into:

  - data/keynote_es/keynote_es_native.mp4 (2.5-5 min, 1280x720)
  - data/keynote_es/keynote_es_native.srt (1 entry/slide)
  - intermediates under data/keynote_es/work/ and data/keynote_es/charts/

All local, $0. Never touches the network at build time.

Usage (repo root): .venv/bin/python live/material/build_keynote_es.py

Env knobs:
  VOICE_ES  path to the es_ES piper .onnx (default /tmp/piper_voices_es/...)
  PIPER_BIN path to the piper CLI (default .venv/bin/piper)
"""
import os
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_keynote as bk  # noqa: E402  (SLIDES + pipeline helpers, read-only)

ROOT = bk.ROOT
OUTDIR = os.path.join(ROOT, "data", "keynote_es")
WORK = os.path.join(OUTDIR, "work")
CHARTS = os.path.join(OUTDIR, "charts")

PIPER_BIN = os.environ.get("PIPER_BIN", bk.PIPER_BIN)
VOICE_ES = os.environ.get(
    "VOICE_ES", "/tmp/piper_voices_es/es_ES-carlfm-x_low.onnx")

LANG = "es"
FINAL_BASENAME = "keynote_es_native"


# ----------------------------------------------------------------------------
# Charts: same 4 deliberately misleading props as build_keynote.py, regenerated
# into data/keynote_es/charts/ (own outdir; shared checkout safety).
# ----------------------------------------------------------------------------
def make_charts():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(CHARTS, exist_ok=True)
    out = {}

    # 1) Truncated Y axis: 27.8 -> 29.3 (+5.4%) drawn to look ~3x.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["2023", "2024"], [27.8, 29.3], color=["#94a3b8", "#f5b301"])
    ax.set_ylim(27.0, 30.0)
    ax.set_ylabel("Trillion dollars (axis starts at 27)")
    ax.set_title("US GDP doubled in one year (!)")
    for x, v in (("2023", 27.8), ("2024", 29.3)):
        ax.text(x, v + 0.03, f"{v}", ha="center", fontsize=11)
    p = os.path.join(CHARTS, "chart_truncated.png")
    fig.tight_layout()
    fig.savefig(p, dpi=100)
    plt.close(fig)
    out["truncated"] = p

    # 2) Honest 0.97% chart that CONTRADICTS the spoken "40%".
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["Last year"], [0.97], color="#38bdf8")
    ax.set_ylim(0, 5)
    ax.set_ylabel("Percent (%)")
    ax.set_title("World population growth, last year")
    ax.text("Last year", 1.1, "0.97%", ha="center", fontsize=12, fontweight="bold")
    p = os.path.join(CHARTS, "chart_rate40.png")
    fig.tight_layout()
    fig.savefig(p, dpi=100)
    plt.close(fig)
    out["rate40"] = p

    # 3) Broken scale: India (3.76) drawn TALLER than USA (29.3).
    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(6.4, 4.2), sharex=True,
        gridspec_kw={"hspace": 0.08, "height_ratios": [1, 2]})
    ax_bot.bar([0], [3.76], color="#f5b301")
    ax_bot.set_xlim(-0.6, 1.6)
    ax_bot.set_xticks([0, 1])
    ax_bot.set_xticklabels(["India", "USA"])
    ax_top.bar([1], [1.3], bottom=[28.0], color="#94a3b8")
    ax_top.set_xlim(-0.6, 1.6)
    ax_top.set_ylim(27.5, 30.0)
    ax_bot.set_ylim(0, 4.2)
    ax_top.spines["bottom"].set_visible(False)
    ax_bot.spines["top"].set_visible(False)
    ax_top.tick_params(bottom=False, labelbottom=False)
    d = 0.015
    kwargs = dict(transform=ax_top.transAxes, color="k", clip_on=False)
    ax_top.plot((-d, +d), (-d, +d), **kwargs)
    ax_top.plot((1 - d, 1 + d), (-d, +d), **kwargs)
    kwargs.update(transform=ax_bot.transAxes)
    ax_bot.plot((-d, +d), (1 - d, 1 + d), **kwargs)
    ax_bot.plot((1 - d, 1 + d), (1 - d, 1 + d), **kwargs)
    fig.suptitle("GDP 2024: India vs USA (trillion dollars)")
    ax_bot.set_ylabel("Trillion dollars (broken scale!)")
    p = os.path.join(CHARTS, "chart_broken.png")
    fig.tight_layout()
    fig.savefig(p, dpi=100)
    plt.close(fig)
    out["broken"] = p

    # 4) Temporal cherry-pick: three quarters of 2020 sold as "every year".
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    qs = ["2020-Q2", "2020-Q3", "2020-Q4"]
    g = [-8.2, 5.4, 2.8]  # illustrative quarterly rebound, see README
    bars = ax.bar(qs, g, color=["#ef4444", "#22c55e", "#22c55e"])
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("Quarterly growth (%)")
    ax.set_title("Japan: growth every year this decade")
    for b, v in zip(bars, g):
        ax.text(b.get_x() + b.get_width() / 2, v + (0.2 if v > 0 else -0.5),
                f"{v}%", ha="center", fontsize=11)
    p = os.path.join(CHARTS, "chart_cherry.png")
    fig.tight_layout()
    fig.savefig(p, dpi=100)
    plt.close(fig)
    out["cherry"] = p

    return out


# ----------------------------------------------------------------------------
# Slide PNGs (same layout as build_keynote.py, own work dir).
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

    fig.text(0.06, 0.96, slide["title"][LANG], color=bk.ACCENT,
             fontsize=32, fontweight="bold", va="top", ha="left")
    fig.text(0.06, 0.845, "ATLAS SUMMIT 2025  \u2022  Voz nativa ES (F2-4)",
             color=bk.MUTED, fontsize=13, va="top", ha="left")

    y = 0.71
    for b in slide["bullets"][LANG]:
        wrapped = textwrap.wrap(b, width=34 if has_chart else 60)
        fig.text(0.06, y, "\u2022  " + wrapped[0], color=bk.FG,
                 fontsize=21, va="top", ha="left")
        y -= 0.075
        for cont in wrapped[1:]:
            fig.text(0.085, y, cont, color=bk.FG, fontsize=21, va="top", ha="left")
            y -= 0.07
        y -= 0.02

    fig.text(0.06, 0.045, f"Slide {n} / {len(bk.SLIDES)}", color=bk.MUTED,
             fontsize=13)
    fig.text(0.94, 0.045, "ES", color=bk.MUTED, fontsize=13, ha="right")

    if has_chart:
        img = mpimg.imread(chart_paths[slide["chart"]])
        ax = fig.add_axes([0.55, 0.12, 0.40, 0.62])
        ax.imshow(img)
        ax.axis("off")
        for spine in ax.spines.values():
            spine.set_visible(False)

    p = os.path.join(WORK, f"slide_{n:02d}_{LANG}_native.png")
    fig.savefig(p, dpi=bk.DPI, facecolor=bk.BG)
    plt.close(fig)
    return p


# ----------------------------------------------------------------------------
# TTS (piper CLI, native es_ES voice) + ffmpeg assembly.
# ----------------------------------------------------------------------------
def tts(text, wav_path):
    cmd = [PIPER_BIN, "--model", VOICE_ES, "--output_file", wav_path]
    r = subprocess.run(cmd, input=text.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(f"piper failed: {r.stderr.decode()[-500:]}")
    return wav_path


def concat_segments(ff, seg_paths, final_path):
    lst = os.path.join(WORK, "concat_%s.txt" % os.path.basename(final_path))
    with open(lst, "w") as f:
        for s in seg_paths:
            f.write(f"file '{s}'\n")
    bk.run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", lst, "-c", "copy", "-movflags", "+faststart", final_path])


def main():
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(OUTDIR, exist_ok=True)
    assert os.path.exists(PIPER_BIN), f"missing piper: {PIPER_BIN}"
    assert os.path.exists(VOICE_ES + ".json"), f"missing voice: {VOICE_ES}"
    ff = bk.ffmpeg_bin()

    print("== charts ==", flush=True)
    chart_paths = make_charts()
    for k, v in chart_paths.items():
        print(f"  {k}: {v} ({os.path.getsize(v)} bytes)")

    print(f"== slides+tts [{LANG}, native] ==", flush=True)
    timings = []
    texts = []
    segs = []
    t = 0.0
    for slide in bk.SLIDES:
        n = slide["n"]
        png = render_slide_png(slide, chart_paths)
        wav = os.path.join(WORK, f"narr_{n:02d}_{LANG}_native.wav")
        tts(bk.narration(slide, LANG), wav)
        info = bk.wav_info(wav)
        assert info["frames"] > 0, f"empty wav: {wav}"
        assert info["rms"] > 50, f"silent wav: {wav} rms={info['rms']:.1f}"
        dur = info["dur"] + bk.PAD_AFTER_AUDIO
        print(f"  slide {n:02d}: wav {info['bytes']} bytes, "
              f"{info['dur']:.1f}s audio, rms={info['rms']:.0f} "
              f"-> seg {dur:.1f}s")
        seg = os.path.join(WORK, f"seg_{n:02d}_{LANG}_native.mp4")
        bk.build_segment(ff, png, wav, dur, seg)
        timings.append((t, t + dur))
        t += dur
        texts.append(bk.narration(slide, LANG))
        segs.append(seg)

    final = os.path.join(OUTDIR, f"{FINAL_BASENAME}.mp4")
    concat_segments(ff, segs, final)
    bk.write_srt(os.path.join(OUTDIR, f"{FINAL_BASENAME}.srt"), timings, texts)
    print(f"  VIDEO {final}: {t:.1f}s total ({t/60:.2f} min)")
    assert 150 <= t <= 300, f"duration {t:.1f}s outside 2.5-5 min!"
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
