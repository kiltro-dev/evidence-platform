# Licenses & sources (M1c synthetic keynote)

Everything below is free / $0. No YouTube, no paid API, no network at build time.

## Voice (TTS)

- Engine: **piper** (MIT license), run locally from the repo `.venv`.
- Voice model: `en_US-ljspeech-medium` (`/tmp/piper_voices_lj/*.onnx*`).
- Training data: **LJSpeech**, which is **public domain** (single-speaker
  English readings of public-domain texts).
- Phonemizer used by piper at build time: **espeak-ng** (GPLv3) — used as a
  local tool only, not distributed with this material.
- The same English voice reads both the EN and ES tracks, so the Spanish
  narration has an English accent. Disclosed, not a defect for this fixture.

## Fonts, charts, video

- Slide/chart text: **DejaVu Sans** (bundled with matplotlib; Bitstream Vera
  + Arev Fonts licenses, free).
- Charts + slide PNGs: **matplotlib** (PSF-based license) + **numpy** (BSD).
- Video assembly: **imageio-ffmpeg** (BSD-2-clause) which bundles an ffmpeg
  binary (LGPL build); output is vanilla H.264 + AAC in MP4.

## Fact sources (references only; baked into `truth.json`, not redistributed)

- World Bank Open Data API (`api.worldbank.org`), indicator data under
  **CC BY-4.0**: GDP `NY.GDP.MKTP.CD`, population `SP.POP.TOTL`.
- UN Day of Eight Billion (15 Nov 2022) for the 8-billion milestone.
- SI defining constant of c (BIPM), NASA planetary facts, 2020 China–Nepal
  Everest survey, Britannica (Sahara) — cited in `truth.json` refs.

## This fixture's own content

- Script, slides, narration audio, videos, subtitles: **synthetic original
  material** created for the Atlas Live M1c exercise (fictional "Atlas Summit",
  no real persons). The 15 `false` claims and 4 misleading charts are
  deliberate traps for fact-checking; see `README.md`.
