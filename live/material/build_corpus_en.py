#!/usr/bin/env python3
"""Build the synthetic EN corpus: 6 pieces x 40 claims (Atlas Live).

Pipeline (per piece): SCRIPT (synth_en_data.P IECES) -> matplotlib slide
PNGs (1080p, badge burned in) + charts -> piper TTS per slide (two
verified EN voices) -> ffmpeg segments with an animated code presenter
bar -> concat -> mp4 + srt + truth.json. Then: clean 16 kHz reference
wav + 3 room-noise SNR variants (ffmpeg anoisesrc, exact numpy mix).

Outputs (gitignored via data/):
  data/synth/en/<piece>.mp4            1920x1080, 30 fps, 2.5-5 min
  data/synth/en/<piece>.srt            1 entry per slide (narration)
  data/synth/en/wavs/<piece>_clean_16k.wav + _snr20/_snr10/_snr00.wav
  data/synth/en/snap/                  World Bank snapshots + MANIFEST.json
  data/synth/en/wer_reports/<piece>.json   (only with --measure, live $)
  live/material/truth_en/<piece>.json  claim ground truth (committed)

Usage (repo root):
  .venv/bin/python live/material/build_corpus_en.py [--pieces k1_econ,...]
  .venv/bin/python live/material/build_corpus_en.py --measure [--pieces ...]

Env knobs: PIPER_BIN, VOICE_A (default /tmp/piper_voices_en/
en_US-ljspeech-medium.onnx), VOICE_B (default .../en_US-norman-medium.onnx).
Build is fully offline ($0). Only --measure touches the network (ASR).
"""
import json
import os
import re
import struct
import subprocess
import sys
import textwrap
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_keynote as bk  # noqa: E402 (ffmpeg/wav/srt helpers, read-only)
import synth_en_data as se  # noqa: E402 (PIECES scripts, read-only)

ROOT = bk.ROOT
MATERIAL = os.path.join(ROOT, "live", "material")
TRUTH_DIR = os.path.join(MATERIAL, "truth_en")
OUTDIR = os.path.join(ROOT, "data", "synth", "en")
WORK = os.path.join(OUTDIR, "work")
CHARTS = os.path.join(OUTDIR, "charts")
WAVS = os.path.join(OUTDIR, "wavs")
WERDIR = os.path.join(OUTDIR, "wer_reports")
SNAPDIR = os.path.join(OUTDIR, "snap")

PIPER_BIN = os.environ.get("PIPER_BIN", os.path.join(ROOT, ".venv", "bin", "piper"))
VOICES = {
    "A": os.environ.get("VOICE_A",
        "/tmp/piper_voices_en/en_US-ljspeech-medium.onnx"),
    "B": os.environ.get("VOICE_B",
        "/tmp/piper_voices_en/en_US-norman-medium.onnx"),
}
VOICE_NAMES = {"A": "en_US-ljspeech-medium (host/female)",
               "B": "en_US-norman-medium (guest/male)"}

WIDTH, HEIGHT, DPI = 1920, 1080, 100
FPS = 30
PAD_AFTER_AUDIO = 0.8
SENTENCE_SILENCE = 0.35  # extra pause between sentences (piper)
LENGTH_SCALES = (1.00, 0.96, 1.04)  # rhythm variation, cycled per slide

# Synthetic-demo badge (burned into every slide PNG corner). Tests check
# this flag, not pixels.
BADGE_ENABLED = True
BADGE_TEXT = "Synthetic demo"

# Room-noise variants, dB. Must stay 3 distinct values (tests check).
SNR_DB = (20, 10, 0)
NOISE_COLOR = "pink"

USD_PER_HOUR = 0.18
FUZZY_THRESHOLD = 0.9


# ----------------------------------------------------------------------------
# Charts (matplotlib). Honest ones carry exact labeled values; the 4
# misleading props (trunc-*, rate-pop, said-co2) are deliberate traps:
# truncated axes with honest axis labels, or a chart that contradicts
# the narration (said-vs-shown). Documented in PROVENANCE_EN.md.
# ----------------------------------------------------------------------------
def make_charts():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(CHARTS, exist_ok=True)
    out = {}

    def save(fig, name):
        p = os.path.join(CHARTS, name + ".png")
        fig.tight_layout()
        fig.savefig(p, dpi=100)
        plt.close(fig)
        out[name] = p

    # honest-unemp: USA 4.0, Spain 11.4, Japan 2.5.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["USA", "Spain", "Japan"], [4.0, 11.4, 2.5],
           color=["#38bdf8", "#f5b301", "#22c55e"])
    ax.set_ylim(0, 13)
    ax.set_ylabel("Percent (%)")
    ax.set_title("Unemployment 2024 (modeled estimates)")
    for x, v in (("USA", 4.0), ("Spain", 11.4), ("Japan", 2.5)):
        ax.text(x, v + 0.25, f"{v}%", ha="center", fontsize=11)
    save(fig, "honest-unemp")

    # trunc-usa (MISLEADING): 27.8 -> 29.3 (+5.3%) drawn to look ~3x.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["2023", "2024"], [27.8, 29.3], color=["#94a3b8", "#f5b301"])
    ax.set_ylim(27.0, 30.0)
    ax.set_ylabel("Trillion dollars (axis starts at 27)")
    ax.set_title("US GDP doubled in one year (!)")
    for x, v in (("2023", 27.8), ("2024", 29.3)):
        ax.text(x, v + 0.03, f"{v}", ha="center", fontsize=11)
    save(fig, "trunc-usa")

    # rate-pop (said-vs-shown prop): honest 0.96% chart; the speaker says 40%.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["Last year"], [0.96], color="#38bdf8")
    ax.set_ylim(0, 5)
    ax.set_ylabel("Percent (%)")
    ax.set_title("World population growth, last year")
    ax.text("Last year", 1.1, "0.96%", ha="center", fontsize=12,
            fontweight="bold")
    save(fig, "rate-pop")

    # honest-forest: Brazil 58.5, Japan 68.3, UK 13.5.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["Brazil", "Japan", "UK"], [58.5, 68.3, 13.5],
           color=["#22c55e", "#16a34a", "#94a3b8"])
    ax.set_ylim(0, 80)
    ax.set_ylabel("Forest share of land (%)")
    ax.set_title("Forest cover 2024")
    for x, v in (("Brazil", 58.5), ("Japan", 68.3), ("UK", 13.5)):
        ax.text(x, v + 1, f"{v}%", ha="center", fontsize=11)
    save(fig, "honest-forest")

    # trunc-elec (MISLEADING): 12,934 -> 14,260 (+10.3%) drawn ~3x.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["USA", "Canada"], [12933.7, 14260.1],
           color=["#94a3b8", "#f5b301"])
    ax.set_ylim(12000, 15000)
    ax.set_ylabel("kWh per person (axis starts at 12,000)")
    ax.set_title("Canada uses triple the power (!)")
    for x, v in (("USA", 12933.7), ("Canada", 14260.1)):
        ax.text(x, v + 40, f"{v:,.0f}", ha="center", fontsize=11)
    save(fig, "trunc-elec")

    # said-co2 (said-vs-shown prop): honest 52.8%; the speaker says 95%.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["Top 3 emitters"], [52.8], color="#38bdf8")
    ax.set_ylim(0, 100)
    ax.set_ylabel("Share of world CO2 (%)")
    ax.set_title("Top three emitters share of CO2")
    ax.text("Top 3 emitters", 55, "52.8%", ha="center", fontsize=12,
            fontweight="bold")
    save(fig, "said-co2")

    # honest-growth: current-dollar GDP growth 2023-2024.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["USA", "China", "India"], [5.3, 2.5, 7.4],
           color=["#38bdf8", "#f5b301", "#22c55e"])
    ax.set_ylim(0, 9)
    ax.set_ylabel("Percent (%)")
    ax.set_title("GDP growth 2023-2024, current dollars")
    for x, v in (("USA", 5.3), ("China", 2.5), ("India", 7.4)):
        ax.text(x, v + 0.15, f"{v}%", ha="center", fontsize=11)
    save(fig, "honest-growth")

    # honest-co2: China 13125, USA 4632, India 3154 megatonnes.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["China", "USA", "India"], [13125, 4632, 3154],
           color=["#f5b301", "#38bdf8", "#22c55e"])
    ax.set_ylabel("Megatonnes CO2 equivalent")
    ax.set_title("CO2 emissions 2024")
    for x, v in (("China", 13125), ("USA", 4632), ("India", 3154)):
        ax.text(x, v + 250, f"{v:,}", ha="center", fontsize=11)
    save(fig, "honest-co2")

    # honest-speeds: log scale, sound 343 vs light 299,792,458 m/s.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["Sound", "Light"], [343, 299792458], color=["#38bdf8", "#f5b301"])
    ax.set_yscale("log")
    ax.set_ylabel("Meters per second (log scale)")
    ax.set_title("How fast is fast")
    ax.text("Sound", 343 * 3, "343", ha="center", fontsize=11)
    ax.text("Light", 299792458 * 0.25, "299,792,458", ha="center",
            fontsize=11)
    save(fig, "honest-speeds")

    # honest-life: Japan 84.0, Italy 84.0, Nigeria 54.6 years.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["Japan", "Italy", "Nigeria"], [84.0, 84.0, 54.6],
           color=["#38bdf8", "#22c55e", "#f5b301"])
    ax.set_ylim(0, 95)
    ax.set_ylabel("Years")
    ax.set_title("Life expectancy 2024")
    for x, v in (("Japan", 84.0), ("Italy", 84.0), ("Nigeria", 54.6)):
        ax.text(x, v + 1.5, f"{v}", ha="center", fontsize=11)
    save(fig, "honest-life")

    # honest-net: World 71.2, Korea 97.9, Nigeria 41.2.
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["World", "S. Korea", "Nigeria"], [71.2, 97.9, 41.2],
           color=["#94a3b8", "#38bdf8", "#f5b301"])
    ax.set_ylim(0, 110)
    ax.set_ylabel("Internet users (%)")
    ax.set_title("Internet use 2024")
    for x, v in (("World", 71.2), ("S. Korea", 97.9), ("Nigeria", 41.2)):
        ax.text(x, v + 2, f"{v}%", ha="center", fontsize=11)
    save(fig, "honest-net")

    # honest-cash (INVENTED company figures, labeled as such).
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["Op. cash flow", "Cash", "Debt"], [0.89, 2.30, 1.10],
           color=["#22c55e", "#38bdf8", "#ef4444"])
    ax.set_ylabel("Billion dollars (INVENTED figures)")
    ax.set_title("Helioshore Q3 2026 cash (fictional)")
    for x, v in (("Op. cash flow", 0.89), ("Cash", 2.30), ("Debt", 1.10)):
        ax.text(x, v + 0.05, f"{v:.2f}", ha="center", fontsize=11)
    save(fig, "honest-cash")

    # honest-regions (INVENTED company figures, labeled as such).
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.bar(["Americas", "Europe", "Asia-Pacific"], [2.41, 1.69, 0.72],
           color=["#38bdf8", "#94a3b8", "#f5b301"])
    ax.set_ylabel("Billion dollars (INVENTED figures)")
    ax.set_title("Helioshore Q3 revenue by region (fictional)")
    for x, v in (("Americas", 2.41), ("Europe", 1.69), ("Asia-Pacific", 0.72)):
        ax.text(x, v + 0.05, f"{v:.2f}", ha="center", fontsize=11)
    save(fig, "honest-regions")

    return out


# ----------------------------------------------------------------------------
# Slide PNGs, 1920x1080, badge burned into the top-right corner.
# ----------------------------------------------------------------------------
BG, FG, ACCENT, MUTED = "#101820", "#f1f5f9", "#f5b301", "#94a3b8"


def render_slide_png(piece_id, slide, chart_paths, outdir, badge=BADGE_ENABLED):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg

    piece = se.PIECES[piece_id]
    n = slide["n"]
    fig = plt.figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI)
    fig.patch.set_facecolor(BG)
    has_chart = bool(slide["chart"])

    fig.text(0.05, 0.96, slide["title"], color=ACCENT,
             fontsize=44, fontweight="bold", va="top", ha="left")
    kicker = f"{piece['title']}  \u2022  Synthetic EN corpus  \u2022  {piece['kind']}"
    if "voice" in slide:
        who = "HOST (voice A)" if slide["voice"] == "A" else "GUEST (voice B)"
        kicker += f"  \u2022  {who}"
    fig.text(0.05, 0.875, kicker, color=MUTED, fontsize=18, va="top",
             ha="left")

    y = 0.74
    for b in slide["bullets"]:
        wrapped = textwrap.wrap(b, width=30 if has_chart else 56)
        fig.text(0.05, y, "\u2022  " + wrapped[0], color=FG,
                 fontsize=30, va="top", ha="left")
        y -= 0.075
        for cont in wrapped[1:]:
            fig.text(0.075, y, cont, color=FG, fontsize=30, va="top",
                     ha="left")
            y -= 0.068
        y -= 0.025

    fig.text(0.05, 0.045,
             f"Slide {n} / {len(piece['slides'])}  \u2022  "
             f"SYNTHETIC PRESENTER (code)",
             color=MUTED, fontsize=18)
    fig.text(0.95, 0.045, "EN", color=MUTED, fontsize=18, ha="right")

    if badge:
        fig.text(0.95, 0.955, BADGE_TEXT, color="#101820", fontsize=24,
                 fontweight="bold", ha="right", va="top",
                 bbox=dict(boxstyle="round,pad=0.4", facecolor=ACCENT,
                           edgecolor="none"))

    if has_chart:
        img = mpimg.imread(chart_paths[slide["chart"]])
        ax = fig.add_axes([0.55, 0.12, 0.40, 0.62])
        ax.imshow(img)
        ax.axis("off")
        for spine in ax.spines.values():
            spine.set_visible(False)

    p = os.path.join(outdir, f"slide_{piece_id}_{n:02d}.png")
    fig.savefig(p, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return p


# ----------------------------------------------------------------------------
# TTS (piper CLI, two verified EN voices) + ffmpeg assembly with an
# animated code-presenter bar (no photorealistic avatar anywhere).
# ----------------------------------------------------------------------------
def tts(text, wav_path, voice="A", length_scale=1.0):
    cmd = [PIPER_BIN, "--model", VOICES[voice], "--output_file", wav_path,
           "--sentence-silence", str(SENTENCE_SILENCE),
           "--length-scale", str(length_scale)]
    r = subprocess.run(cmd, input=text.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(f"piper failed: {r.stderr.decode()[-500:]}")
    return wav_path


def build_segment(ff, png, wav, dur, seg_path):
    # Animated presenter: a progress bar sweeping left->right, drawn by
    # ffmpeg (code shape, not a person). Frame width is hardcoded: drawbox
    # x/y expressions only know n/t, not W/H.
    vf = (f"drawbox=x='{WIDTH - 260}*t/{dur:.3f}':y={HEIGHT - 70}:w=220:h=12:"
          f"c=0xF5B301:t=fill")
    cmd = [ff, "-y", "-v", "error",
           "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}",
           "-i", png, "-i", wav,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
           "-vf", vf,
           "-af", f"apad=whole_dur={dur:.3f}",
           "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
           "-t", f"{dur:.3f}", "-movflags", "+faststart", seg_path]
    bk.run(cmd)


def concat_wavs(wav_paths, out_path):
    """Concatenate same-format WAVs (used for the clean master)."""
    import shutil
    with wave.open(wav_paths[0], "rb") as w0:
        params = w0.getparams()
    with wave.open(out_path, "wb") as out:
        out.setparams(params)
        for p in wav_paths:
            with wave.open(p, "rb") as w:
                assert (w.getnchannels(), w.getsampwidth(),
                        w.getframerate()) == params[:3], p
                out.writeframes(w.readframes(w.getnframes()))
    return out_path


# ----------------------------------------------------------------------------
# Room noise: ffmpeg anoisesrc generates the noise bed; numpy mixes it at
# exact SNRs. Returns {snr_db: measured_snr_db}.
# ----------------------------------------------------------------------------
def _read_mono16(path):
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2, path
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    import numpy as np
    return rate, np.frombuffer(raw, dtype=np.int16).astype(np.float64)


def _write_mono16(path, rate, samples):
    import numpy as np
    clipped = np.clip(np.round(samples), -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(clipped.tobytes())
    return path


def mix_room_noise(ff, clean_16k, dur_s, out_prefix):
    import numpy as np
    rate, clean = _read_mono16(clean_16k)
    assert rate == 16000, clean_16k
    noise_wav = out_prefix + "_noise_only.wav"
    bk.run([ff, "-y", "-v", "error", "-f", "lavfi",
            "-i", f"anoisesrc=color={NOISE_COLOR}:duration={dur_s:.3f}"
                  f":sample_rate=16000:amplitude=1.0:seed=7",
            "-c:a", "pcm_s16le", noise_wav])
    _, noise = _read_mono16(noise_wav)
    n = min(len(clean), len(noise))
    clean, noise = clean[:n], noise[:n]
    os.remove(noise_wav)
    p_clean = float(np.mean(clean ** 2)) + 1e-9
    p_noise = float(np.mean(noise ** 2)) + 1e-9
    measured = {}
    for snr in SNR_DB:
        scaled = noise * ((p_clean / p_noise / (10.0 ** (snr / 10.0))) ** 0.5)
        mixed = clean + scaled
        tag = f"{snr:02d}" if snr >= 0 else f"neg{abs(snr):02d}"
        _write_mono16(f"{out_prefix}_snr{tag}.wav", rate, mixed)
        p_err = float(np.mean((mixed - clean) ** 2)) + 1e-9
        import math
        measured[snr] = round(10.0 * math.log10(p_clean / p_err), 2)
    return measured


# ----------------------------------------------------------------------------
# EN scoring helpers (offline; tests import these).
# ----------------------------------------------------------------------------
NUM_RE = re.compile(r"\d[\d.,]*")
CAP_WORD = r"[A-Z][a-z]+"
ACRONYM_RE = re.compile(r"\b[A-Z]{2,}(?:\.[A-Z])*\.?")
MULTI_CAP_RE = re.compile(r"\b(%s(?:\s+%s)+)\b" % (CAP_WORD, CAP_WORD))
MONTHS = ("january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december")
UNIT_WORDS = {"million", "millions", "billion", "billions", "trillion",
              "trillions", "thousand", "thousands", "meters", "meter",
              "kilometers", "kilometer", "degrees", "dollars", "cents",
              "people", "percent", "tonnes", "megatonnes", "years",
              "hours", "seconds", "watts", "liters"}
ARTICLES = {"the", "a", "an"}

_EN_0_19 = ("zero one two three four five six seven eight nine ten eleven "
            "twelve thirteen fourteen fifteen sixteen seventeen eighteen "
            "nineteen").split()
_EN_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty",
            60: "sixty", 70: "seventy", 80: "eighty", 90: "ninety"}


def int_to_en(n):
    """Integer (0 <= n < 1e12) -> English words, lowercase."""
    if n < 0 or n >= 10 ** 12:
        raise ValueError(f"out of range: {n}")
    if n < 20:
        return _EN_0_19[n]
    if n < 100:
        tens, rest = (n // 10) * 10, n % 10
        return _EN_TENS[tens] if not rest else f"{_EN_TENS[tens]} {_EN_0_19[rest]}"
    if n < 1000:
        hundreds, rest = n // 100, n % 100
        head = f"{_EN_0_19[hundreds]} hundred"
        return head if not rest else f"{head} {int_to_en(rest)}"
    if n < 10 ** 6:
        thousands, rest = n // 1000, n % 1000
        head = "one thousand" if thousands == 1 else f"{int_to_en(thousands)} thousand"
        return head if not rest else f"{head} {int_to_en(rest)}"
    if n < 10 ** 9:
        millions, rest = n // 10 ** 6, n % 10 ** 6
        head = "one million" if millions == 1 else f"{int_to_en(millions)} million"
        return head if not rest else f"{head} {int_to_en(rest)}"
    billions, rest = n // 10 ** 9, n % 10 ** 9
    head = "one billion" if billions == 1 else f"{int_to_en(billions)} billion"
    return head if not rest else f"{head} {int_to_en(rest)}"


def expand_numbers_en(text):
    """Replace digit tokens with English words.

    Thousand-separator commas are stripped (``299,792,458`` -> one number);
    decimals become ``point`` + digit words (``29.3`` -> ``twenty nine
    point three``); trailing decimal zeros are dropped (``5.60`` -> ``5.6``)
    so ASR ``5.6``/``5.60`` variants score identically.
    """
    def expand_match(m):
        raw = m.group(0).rstrip(".,")
        if re.fullmatch(r"\d{1,3}(,\d{3})+", raw):
            raw = raw.replace(",", "")
        elif "," in raw and "." not in raw:
            raw = raw.replace(",", "")
        if "." in raw:
            int_part, frac_part = raw.split(".", 1)
            frac_part = frac_part.rstrip("0")
            head = int_to_en(int(int_part or 0))
            if not frac_part:
                return head
            return head + " point " + " ".join(_EN_0_19[int(d)] for d in frac_part)
        return int_to_en(int(raw))
    return NUM_RE.sub(expand_match, text)


def normalize_en(text):
    """Lowercase, strip punctuation, collapse whitespace (digits kept)."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def score_text(text):
    """Normalized text for scoring: numbers expanded, then normalized."""
    return normalize_en(expand_numbers_en(text))


def wer_details(ref_text, hyp_text):
    """Word-level Levenshtein; returns dict(wer, sub, ins, del, n_ref)."""
    ref = score_text(ref_text).split()
    hyp = score_text(hyp_text).split()
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1,
                          d[i - 1][j - 1] + cost)
    sub = ins = dele = 0
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (
                0 if ref[i - 1] == hyp[j - 1] else 1):
            if ref[i - 1] != hyp[j - 1]:
                sub += 1
            i -= 1
            j -= 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            dele += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    dist = d[n][m]
    return {"wer": (dist / n) if n else (0.0 if not m else 1.0),
            "sub": sub, "ins": ins, "del": dele, "n_ref": n, "n_hyp": m}


MAX_KEYWORD_CHARS = 30  # server-side ASR keyword limit (400 beyond)


def extract_keywords_en(texts):
    """Ordered, deduped ASR keywords: numbers + quantity phrases + entities.

    Keywords longer than MAX_KEYWORD_CHARS are replaced by their adjacent
    2-word shingles (each within the limit) so multiword entities keep
    coverage without tripping the server 400.
    """
    kws = []

    def add(k):
        k = k.strip(" .,;:")
        if not k or k in kws:
            return
        if len(k) <= MAX_KEYWORD_CHARS:
            kws.append(k)
            return
        words = k.split()
        for a, b in zip(words, words[1:]):
            shingle = f"{a} {b}"
            if len(shingle) <= MAX_KEYWORD_CHARS and shingle not in kws:
                kws.append(shingle)

    for text in texts:
        for m in NUM_RE.finditer(text):
            num = m.group(0).rstrip(".,")
            add(num)
            rest = text[m.end():m.end() + 28]
            words = re.findall(r"[A-Za-z]+", rest)
            if words:
                if words[0].lower() in UNIT_WORDS:
                    add(f"{num} {words[0]}")
                elif (words[0].lower() == "point" or
                      (len(words) > 1 and words[0].lower() in
                       {"per", "times"})):
                    add(f"{num} {words[0]} {words[1]}") if len(words) > 1 else add(num)
        multi_parts = set()
        for m in MULTI_CAP_RE.finditer(text):
            phrase = m.group(1)
            words = phrase.split()
            while len(words) > 1 and words[0].lower() in ARTICLES:
                words.pop(0)
            phrase = " ".join(words)
            add(phrase)
            multi_parts.update(words)
        for m in ACRONYM_RE.finditer(text):
            if len(m.group(0).rstrip(".")) >= 2:
                add(m.group(0))
        for sent in re.split(r"[.!?;]\s*", text):
            toks = re.findall(r"[A-Za-z0-9.,]+", sent)
            for tok in toks[1:]:
                tok = tok.strip(".,;:()\"'")
                if re.fullmatch(CAP_WORD, tok) and tok not in multi_parts:
                    add(tok)
        low = f" {text.lower()} "
        for mon in MONTHS:
            if f" {mon} " in low:
                add(mon)
    return kws


def span_found(hyp_norm, hyp_tokens, span_norm, threshold=FUZZY_THRESHOLD):
    """Literal substring, else token-window fuzzy ratio >= threshold."""
    import difflib
    if not span_norm:
        return False
    if span_norm in hyp_norm:
        return True
    span_tokens = span_norm.split()
    if len(span_tokens) < 3 or len(hyp_tokens) < len(span_tokens):
        return False
    n = len(span_tokens)
    span_set = set(span_tokens)
    best = 0.0
    for size in (n - 1, n, n + 1):
        if size < 3 or size > len(hyp_tokens):
            continue
        for i in range(len(hyp_tokens) - size + 1):
            window = hyp_tokens[i:i + size]
            if len(span_set & set(window)) < 0.5 * len(span_set):
                continue
            r = difflib.SequenceMatcher(None, span_tokens, window).ratio()
            if r >= threshold:
                return True
            best = max(best, r)
    return best >= threshold


def claim_recall(claim_texts, transcript, threshold=FUZZY_THRESHOLD):
    """(recall dict) over claim spans in a transcription."""
    hyp_norm = score_text(transcript)
    hyp_tokens = hyp_norm.split()
    hits, misses = [], []
    for text in claim_texts:
        (hits if span_found(hyp_norm, hyp_tokens, score_text(text),
                            threshold) else misses).append(text)
    recall = len(hits) / len(claim_texts) if claim_texts else 0.0
    return {"recall": recall, "hits": hits, "misses": misses,
            "n": len(claim_texts)}


# ----------------------------------------------------------------------------
# Truth writing.
# ----------------------------------------------------------------------------
URL_RE = re.compile(r"https?://\S+")
SNAP_RE = re.compile(r"\[snap ([A-Za-z0-9_.-]+\.json)\]")


def load_manifest():
    mp = os.path.join(SNAPDIR, "MANIFEST.json")
    with open(mp, encoding="utf-8") as f:
        return json.load(f)


def write_truth(piece_id, timings, manifest):
    rows = []
    piece = se.PIECES[piece_id]
    for slide, (t0, _t1) in zip(piece["slides"], timings):
        for seg in slide["segs"]:
            if "c" not in seg:
                continue
            row = {
                "id": f"{seg['c']}-en",
                "claim": seg["c"],
                "piece": piece_id,
                "lang": "en",
                "slide": slide["n"],
                "text": seg["t"],
                "t_aprox": round(t0, 2),
                "minuto": round(t0 / 60.0, 3),
                "expected": seg["e"],
                "ref": seg["r"],
                "category": seg["g"],
                "modality": seg["m"],
            }
            m = URL_RE.search(seg["r"])
            if m:
                row["ref_url"] = m.group(0).rstrip("),")
            m = SNAP_RE.search(seg["r"])
            if m:
                snap = m.group(1)
                row["snapshot"] = f"data/synth/en/snap/{snap}"
                if snap in manifest:
                    row["snapshot_sha256"] = manifest[snap]["sha256"]
            if "s" in seg:
                row["shown"] = seg["s"]
            rows.append(row)
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), f"duplicate ids in {piece_id}"
    os.makedirs(TRUTH_DIR, exist_ok=True)
    p = os.path.join(TRUTH_DIR, f"{piece_id}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    return p, rows


# ----------------------------------------------------------------------------
# Per-piece build.
# ----------------------------------------------------------------------------
def build_piece(ff, piece_id, chart_paths, manifest):
    piece = se.PIECES[piece_id]
    pwork = os.path.join(WORK, piece_id)
    os.makedirs(pwork, exist_ok=True)
    os.makedirs(WAVS, exist_ok=True)
    print(f"== {piece_id}: {piece['title']} [{piece['kind']}] ==", flush=True)

    timings, texts, segs, slide_wavs = [], [], [], []
    t = 0.0
    for i, slide in enumerate(piece["slides"]):
        n = slide["n"]
        voice = slide.get("voice", piece.get("voice", "A"))
        png = render_slide_png(piece_id, slide, chart_paths, pwork)
        wav = os.path.join(pwork, f"narr_{n:02d}.wav")
        tts(se.narration_text(slide), wav, voice,
            LENGTH_SCALES[i % len(LENGTH_SCALES)])
        info = bk.wav_info(wav)
        assert info["frames"] > 0, f"empty wav: {wav}"
        assert info["rms"] > 50, f"silent wav: {wav} rms={info['rms']:.1f}"
        dur = info["dur"] + PAD_AFTER_AUDIO
        print(f"  slide {n:02d} [voice {voice}]: {info['dur']:.1f}s audio, "
              f"rms={info['rms']:.0f} -> seg {dur:.1f}s", flush=True)
        seg = os.path.join(pwork, f"seg_{n:02d}.mp4")
        build_segment(ff, png, wav, dur, seg)
        timings.append((t, t + dur))
        t += dur
        texts.append(se.narration_text(slide))
        segs.append(seg)
        slide_wavs.append(wav)

    final = os.path.join(OUTDIR, f"{piece_id}.mp4")
    lst = os.path.join(pwork, "concat.txt")
    with open(lst, "w") as f:
        for s in segs:
            f.write(f"file '{s}'\n")
    bk.run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", lst, "-c", "copy", "-movflags", "+faststart", final])
    srt_path = os.path.join(OUTDIR, f"{piece_id}.srt")
    bk.write_srt(srt_path, timings, texts)
    print(f"  VIDEO {final}: {t:.1f}s total ({t/60:.2f} min)", flush=True)
    assert 150 <= t <= 300, f"duration {t:.1f}s outside 2.5-5 min!"

    # Clean 16 kHz reference master + room-noise variants.
    master_22k = os.path.join(pwork, "clean_master_22k.wav")
    concat_wavs(slide_wavs, master_22k)
    clean_16k = os.path.join(WAVS, f"{piece_id}_clean_16k.wav")
    bk.run([ff, "-y", "-v", "error", "-i", master_22k,
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", clean_16k])
    with wave.open(clean_16k, "rb") as w:
        clean_dur = w.getnframes() / w.getframerate()
    measured = mix_room_noise(ff, clean_16k, clean_dur,
                              os.path.join(WAVS, piece_id))
    print(f"  WAVS clean {clean_dur:.1f}s + noise {measured}", flush=True)

    tp, rows = write_truth(piece_id, timings, manifest)
    print(f"  TRUTH {tp} ({len(rows)} claims)", flush=True)
    return {"piece": piece_id, "duration_s": round(t, 2),
            "clean_s": round(clean_dur, 2), "snr_measured": measured,
            "n_claims": len(rows)}


# ----------------------------------------------------------------------------
# Live ASR measurement (one POST per piece).
# ----------------------------------------------------------------------------
def measure_piece(piece_id, attempt=0):
    sys.path.insert(0, ROOT)
    from live.oido.asr_file import transcribe_file

    api_key = os.environ.get("MODEL_API_KEY", "")
    if not api_key and os.path.exists(os.path.join(ROOT, ".env")):
        for line in open(os.path.join(ROOT, ".env")):
            if line.startswith("MODEL_API_KEY="):
                api_key = line.split("=", 1)[1].strip().strip("'\"")
    if not api_key:
        raise SystemExit("MODEL_API_KEY not configured; source .env first.")

    with open(os.path.join(TRUTH_DIR, f"{piece_id}.json"),
              encoding="utf-8") as f:
        rows = json.load(f)
    claim_texts = [r["text"] for r in rows]
    srt_path = os.path.join(OUTDIR, f"{piece_id}.srt")
    ref = " ".join(tx for _t0, _t1, tx in _parse_srt(srt_path))
    kws_all = extract_keywords_en(claim_texts)
    if attempt == 0:
        keywords = tuple(kws_all)
    elif attempt == 1:
        keywords = tuple(k for k in kws_all if NUM_RE.search(k))
    else:
        keywords = ()
    print(f"measure {piece_id} (attempt {attempt}): "
          f"{len(claim_texts)} claims, {len(keywords)} keywords", flush=True)
    wav = os.path.join(WAVS, f"{piece_id}_clean_16k.wav")
    resp = transcribe_file(wav, api_key, language_bias=("English",),
                           keywords=keywords)
    transcript = (resp.get("transcript") or "").strip()
    if not transcript:
        turns = resp.get("turns") or []
        transcript = " ".join((t.get("transcript") or "")
                              for t in turns).strip()
    audio_ms = resp.get("audioDurationMs", 0) or 0
    usd = (audio_ms / 3600000.0) * USD_PER_HOUR
    w = wer_details(ref, transcript)
    rec = claim_recall(claim_texts, transcript)
    print(f"  WER {w['wer']:.4f} (n_ref={w['n_ref']}) "
          f"recall {rec['recall']:.4f} ({len(rec['hits'])}/{rec['n']}) "
          f"${usd:.4f}", flush=True)
    for miss in rec["misses"][:6]:
        print(f"  MISS: {miss[:100]}", flush=True)
    report = {"piece": piece_id, "attempt": attempt,
              "n_keywords": len(keywords), "wer": w["wer"],
              "sub": w["sub"], "ins": w["ins"], "del": w["del"],
              "n_ref": w["n_ref"], "n_hyp": w["n_hyp"],
              "recall": rec["recall"], "n_claims": rec["n"],
              "misses": rec["misses"], "audio_ms": audio_ms,
              "usd": round(usd, 4),
              "transcript_chars": len(transcript)}
    os.makedirs(WERDIR, exist_ok=True)
    out = os.path.join(WERDIR, f"{piece_id}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    tpath = os.path.join(WERDIR, f"{piece_id}.transcript.txt")
    with open(tpath, "w", encoding="utf-8") as f:
        f.write(transcript + "\n")
    return report


SRT_BLOCK_RE = re.compile(
    r"(\d+)\s*\n(\d\d:\d\d:\d\d,\d\d\d)\s*-->\s*(\d\d:\d\d:\d\d,\d\d\d)\s*\n(.*?)(?=\n\n|\Z)",
    re.S)


def _parse_srt(path):
    def to_s(ts):
        h, m, rest = ts.split(":")
        s, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
    with open(path, encoding="utf-8") as f:
        body = f.read().strip() + "\n"
    out = []
    for _idx, t0, t1, tx in SRT_BLOCK_RE.findall(body):
        out.append((to_s(t0), to_s(t1), " ".join(tx.split())))
    return out


# ----------------------------------------------------------------------------
# Main.
# ----------------------------------------------------------------------------
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Build the synthetic EN corpus")
    ap.add_argument("--pieces", default=",".join(se.PIECES),
                    help="comma-separated subset to build/measure")
    ap.add_argument("--measure", action="store_true",
                    help="transcribe clean wavs via POST asr (live $, ~1 call/piece)")
    ap.add_argument("--charts-only", action="store_true")
    args = ap.parse_args(argv)
    wanted = [p.strip() for p in args.pieces.split(",") if p.strip()]
    for p in wanted:
        assert p in se.PIECES, f"unknown piece: {p}"

    os.makedirs(WORK, exist_ok=True)
    os.makedirs(OUTDIR, exist_ok=True)
    assert os.path.exists(PIPER_BIN), f"missing piper: {PIPER_BIN}"
    for v, path in VOICES.items():
        assert os.path.exists(path + ".json"), f"missing voice {v}: {path}"

    if args.measure:
        total_usd, summary = 0.0, {}
        for piece_id in wanted:
            report = None
            for attempt in range(3):  # initial + max 2 retries
                report = measure_piece(piece_id, attempt)
                if report["wer"] < 0.15 and report["recall"] >= 0.9:
                    break
                print(f"  RETRY {piece_id}: wer={report['wer']:.3f} "
                      f"recall={report['recall']:.3f}", flush=True)
            total_usd += report["usd"]
            summary[piece_id] = report
            ok = report["wer"] < 0.15 and report["recall"] >= 0.9
            print(f"{piece_id}: {'PASS' if ok else 'UNRESOLVED'} "
                  f"wer={report['wer']:.4f} recall={report['recall']:.4f} "
                  f"(attempt {report['attempt']})", flush=True)
        print(f"TOTAL live ${total_usd:.4f}", flush=True)
        with open(os.path.join(WERDIR, "summary.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"usd": round(total_usd, 4), "pieces": summary},
                      f, ensure_ascii=False, indent=2)
        return 0

    ff = bk.ffmpeg_bin()
    print("== charts ==", flush=True)
    chart_paths = make_charts()
    for k, v in sorted(chart_paths.items()):
        print(f"  {k}: {os.path.getsize(v)} bytes")
    if args.charts_only:
        print("DONE (charts only)")
        return 0

    manifest = load_manifest()
    print(f"== manifest: {len(manifest)} snapshots ==", flush=True)
    build_report = {}
    for piece_id in wanted:
        build_report[piece_id] = build_piece(ff, piece_id, chart_paths,
                                             manifest)
    with open(os.path.join(OUTDIR, "build_report.json"), "w",
              encoding="utf-8") as f:
        json.dump(build_report, f, ensure_ascii=False, indent=2)
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())

