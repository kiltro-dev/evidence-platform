# Provenance — synthetic EN corpus (Atlas Live)

6 pieces x 40 claims. Everything below is free / $0 except the live ASR
measurement (~$0.13 total, see `live/SYNTH_EN.md`). No YouTube, no music,
no real persons (names, voices, faces), no GPUs.

## Voices (TTS)

Engine: **piper** (MIT), run locally from the repo `.venv`. Phonemizer
espeak-ng (GPLv3) as a local tool only, not distributed.

| Voice | Role | Dataset license | Training | sha256 (.onnx) |
|---|---|---|---|---|
| `en_US-ljspeech-medium` (A) | all pieces; panel host | **public domain** (LJ Speech) | from scratch | `6f52a751…81b87f22` |
| `en_US-norman-medium` (B) | panel guest | **public domain** (LibriVox.org, ~15.5 h) | from scratch | `b9739443…1351cb71` |

Full hashes: `.onnx` above; `.onnx.json` `141d612c…65860` (ljspeech),
`6c2db7f5…888ee9fe1d` (norman). Files downloaded 2026-11-02 from
`https://huggingface.co/rhasspy/piper-voices` (`en/en_US/<voice>/medium/`),
MODEL_CARDs read the same day. Local cache (not versioned):
`/tmp/piper_voices_en/`.

Why norman as the second voice (WER + quality + license):

- License chain is clean and non-NC: public-domain dataset, trained from
  scratch, no research-only or NC base (unlike `joe`, `amy`, `arctic`,
  `sam`, which fine-tune `lessac`, or `hfc_*`/`ryan`, which are BY-NC-SA).
- Male timbre contrasts the female LJSpeech voice, which helps the
  two-speaker panel and diarization.
- Intelligibility probe 2026-11-02 (same 4 sentences, ~25 s each, POST asr
  with EN bias + keywords): norman WER **0.08**, ljspeech WER **0.16**
  where every ljspeech error was a duplicated-word insertion, the known
  file-decode artifact (`live/oido/CONTRACT.md`), not a voice defect.

Rhythm variation: `--sentence-silence 0.35` plus a per-slide
`--length-scale` cycle (1.00 / 0.96 / 1.04).

## Fact sources (references only, baked into `truth_en/`, not redistributed)

- World Bank Open Data API (`api.worldbank.org`, CC BY-4.0): 19
  indicators x 20 countries x 2023-2024, fetched 2026-11-02 into
  `data/synth/en/snap/` (38 files + `MANIFEST.json` with sha256 + URLs,
  177 kB). Every TRUE WB claim cites its value and `[snap file]`; every
  d-category claim shows its exact calculation.
- Recent/model-read sources (<= 5 per piece; false variants reuse them):
  IMF WEO October 2026 (global 3.1%, advanced 2.1%),
  BLS Employment Situation September 2026 (4.2%, 7.1M) — both verified
  2026-11-01, URLs checked 2026-11-02 (IMF 200, BLS 403 bot-wall);
  WMO 2024 warmest year (+1.55 C, URL checked 200),
  NOAA GML 2024 CO2 422.8 ppm (URL checked 200),
  UN World Population Prospects 2024 (peak ~10.3B mid-2080s, URL
  checked 200) — all three verified 2026-11-02.
- Textbook constants (BIPM SI, IAU, NASA, CODATA, 2020 China-Nepal
  survey, Britannica, UN 8-billion day): cited per claim in `truth_en/`.

## Charts (matplotlib props)

Honest charts carry exact labeled values. Deliberate traps, 2 per
keynote piece: `trunc-usa` + `trunc-elec` (truncated Y axes with honest
axis labels), `rate-pop` + `said-co2` (honest charts the narration
contradicts: said-vs-shown). Earnings charts plot INVENTED company
figures and are labeled fictional.

## Fictitious company

Helioshore Industries (earnings piece) is invented for this demo; every
company figure is made up and internally consistent (segments and
regions sum to revenue, growth rates recompute). Truth marks these
`expected=coherent-internal`: consistent with the invented Q3 2026
report, NOT verifiable against the real world. No real company,
no real persons; speakers are identified by role only.

## Presentation

Slides + burned-in "Synthetic demo" badge + an ffmpeg-drawn animated
progress bar ("SYNTHETIC PRESENTER (code)"). No avatar, no faces, no
photorealistic persons. Room-noise variants: ffmpeg `anoisesrc`
(pink) mixed at exact SNRs 20/10/0 dB (measured 20.0/10.0/0.0).

## This fixture's own content

Scripts, slides, narration audio, videos, subtitles: synthetic
original material for the Atlas Live exercise. False claims and
misleading charts are deliberate fact-checking traps.
