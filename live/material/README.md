# M1c — Keynote sintético (Atlas Live, rama `live`)

Fictional keynote "The State of the World 2025" / "El estado del mundo 2025",
delivered at a fictional Atlas Summit. No real persons appear.
Built for live fact-checking exercises: every narration sentence is a planted
claim with ground truth in `truth.json`.

## Files (this folder — the only 6 files this task owns)

| File | What it is |
|---|---|
| `build_keynote.py` | Single source of truth (`SLIDES` table) + full pipeline: charts → slide PNGs → piper TTS → ffmpeg segments → concat → mp4 + srt + html + truth.json |
| `slides_en.html` | 12 slides EN (titles, bullets, 4 charts base64-embedded, verbatim speaker notes) |
| `slides_es.html` | Same, in Spanish (faithful translation) |
| `truth.json` | 82 rows = 41 claims × 2 langs: `{id, claim, lang, slide, text, t_aprox, expected, ref}` |
| `LICENSES.md` | Licenses & sources, all $0 |
| `README.md` | This file |

## Outputs (`data/keynote/`, gitignored via `data/` in `.gitignore`)

- `keynote_en.mp4` — **209.0 s (3.48 min)**, 1280×720 H.264 + AAC
- `keynote_es.mp4` — **263.2 s (4.37 min)**, 1280×720 H.264 + AAC
- `keynote_en.srt`, `keynote_es.srt` — subtitles from the script (1 entry/slide)
- `charts/`, `work/` — intermediates (chart PNGs, slide PNGs, per-slide WAVs,
  per-slide MP4 segments). Reproducible; safe to delete and rebuild.

## Claim mix (per language)

- **16 true** — checkable figures: World Bank GDP/pop (USA, China, Japan,
  Germany, France, India, Brazil, world), physics (c, boiling point), geography
  (Everest, Sahara, Earth circumference), UN 8-billion date.
- **15 false** — one per trap type: swapped year (F1, F14), ×10 (F3, F4, F7,
  F8, F10), rate-vs-level (F6, F13), neighboring entity (F2, F9, F15),
  chart-backed whoppers (F5, F11, F12).
- **10 unverifiable** — opinion (U1, U5), future (U2, U6, U8), vague (U3, U4,
  U7, U9, U10).

The 4 misleading charts: (6) truncated Y axis, (7) chart shows 0.97% while the
speaker says 40%, (10) broken scale flipping India/USA ranking, (11) three
quarters of 2020 framed as "every year this decade".

## Rebuild

```sh
.venv/bin/python live/material/build_keynote.py
```

Needs: repo `.venv` (piper, matplotlib, pillow, numpy, imageio-ffmpeg) and the
voice at `/tmp/piper_voices_lj/en_US-ljspeech-medium.onnx*`. No network.
Deterministic except TTS timing jitter (±1 s); `t_aprox` is slide-start
granularity per language.

## Verification (observed, 2026-11-01)

- Durations (ffmpeg): EN 3:29.6, ES 4:22.4 — both inside 2.5–5 min.
- `truth.json`: 82 rows, ids unique (`<CID>-<lang>`), 16/15/10 per lang.
- All 24 WAVs non-empty, non-silent (RMS 3400–4500, 11–28 s each).
- MP4s: H.264 1280×720 30 fps + AAC mono; SRTs align with slide timings.
- 3 sampled TRUE claims confirmed live against the World Bank API before
  freezing (all `lastupdated 2026-10-08`):
  1. T3 USA GDP 2024 = **29,298,013,000,000** → "29.3 trillion" ✓
     (`/v2/country/USA/indicator/NY.GDP.MKTP.CD`)
  2. T2 China pop 2024 = **1,408,975,000** → "1.41 billion" ✓
     (`/v2/country/CHN/indicator/SP.POP.TOTL`)
  3. T7 Japan GDP 2024 = **4,190,008,188,359** → "4.19 trillion" ✓
     (`/v2/country/JPN/indicator/NY.GDP.MKTP.CD`)
- False-claim refs also WB-backed: USA passed $20T in **2018**
  (2017 $19.61T → 2018 $20.66T, so F1 "2012" is false); France GDP 2024 is
  **$3.16T**, not $4.69T (F2); world pop 2023→2024 grew **+0.97 %**, not 40 %
  (F6); Japan GDP fell 2021 $5.23T → 2024 $4.19T (F12).

## Known caveats

- Spanish TTS uses the English LJSpeech voice (gringo accent, per task spec).
- The cherry-pick chart's quarterly values (−8.2 / +5.4 / +2.8 %) are
  illustrative props; the trap is the framing ("every year this decade"),
  and F12's falsity rests on the WB annual series cited in `truth.json`.
- Cost: $0 (all local CPU).
