# SYNTH_EN — synthetic EN corpus (Atlas Live)

6 pieces x 40 claims (240 total), categories a-f in every piece.
Builder: `live/material/build_corpus_en.py` (scripts in
`live/material/synth_en_data.py`). Truths: `live/material/truth_en/`.
Outputs (`data/synth/en/`, gitignored): 1080p30 mp4 + srt + clean 16 kHz
wav + 3 room-noise SNR variants per piece.

## Results

| Piece | Kind | Duration | WER | Recall | Attempt |
|---|---|---|---|---|---|
| k1_econ | keynote, economy | 267.0 s (4.45 min) | 0.060 | 0.925 (37/40) | 0 |
| k2_clim | keynote, climate/energy | 242.9 s (4.05 min) | 0.058 | 0.925 (37/40) | 1 |
| p1_panel | panel, 2 speakers/voices | 254.2 s (4.24 min) | 0.062 | 0.900 (36/40) | 1 |
| s1_sci | science class, physics | 252.3 s (4.21 min) | 0.033 | 0.900 (36/40) | 0 |
| s2_sci | science class, earth/life | 246.8 s (4.11 min) | 0.036 | 1.000 (40/40) | 0 |
| e1_earn | earnings, fictitious co. | 219.0 s (3.65 min) | 0.034 | 0.950 (38/40) | 0 |

Targets were WER < 0.15 and recall >= 0.9: all 6 pass. Retries only
adjusted ASR keywords (attempt 1 = numbers-only subset); no TTS rebuilds
were needed. Misses are single-number substitutions inside long claims
(e.g. "17.4" for "17.4 tonnes" contexts), never whole-claim drops.

## Voices + licenses

- A `en_US-ljspeech-medium`: public domain, from scratch (all pieces).
- B `en_US-norman-medium`: public domain LibriVox data, from scratch
  (panel guest). Chosen over lessac-derived / NC voices; probe WER 0.08
  vs 0.16 for A on identical text (A's errors were file-path
  duplications). Details + hashes: `live/material/PROVENANCE_EN.md`.

## Cost

Live ASR total **$0.13** (final passes $0.07 + superseded attempts and
debug probes $0.05), far under the $6 cap. Build is $0 local CPU
(~10 min for all 6 pieces).

## Unresolved

- None for the corpus targets. One process note: the 31-char keyword
  "World Meteorological Organization" tripped a server-side 30-char
  keyword limit (HTTP 400); the builder now shingles long keywords
  (`MAX_KEYWORD_CHARS = 30`) and tests lock the behavior.
- Recall misses (16/240) are documented per piece in
  `data/synth/en/wer_reports/*.json` (`misses` field).

## Rebuild

```sh
.venv/bin/python live/material/build_corpus_en.py            # offline build
.venv/bin/python live/material/build_corpus_en.py --measure  # live ASR (~$0.07)
.venv/bin/python -m pytest live/tests/test_synth_en.py -q   # offline tests
```

Needs: repo `.venv` (piper, matplotlib, numpy, imageio-ffmpeg) and the
two voices in `/tmp/piper_voices_en/`. Never touches git, secrets
beyond `.env` key presence, YouTube, music, real persons, or GPUs.
