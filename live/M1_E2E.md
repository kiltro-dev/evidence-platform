# M1e — E2E live integration (Atlas Live)

Status: done (heuristic full-pass green; model short-pass exposes a
detector-budget bug, see Unresolved). Live spend **$0.0213** (cap $3).

## Pipeline (all real, asyncio, no mocks)

```
video ──▶ simstream.restream_to  (ffmpeg -re, "simulated-stream")
              audio-only Matroska ──▶ fifo ──▶ ffmpeg demux (PCM s16le 24 kHz)
              ──▶ live/oido/asr_ws.transcribe_stream (Meta realtime WS)
              ──▶ per transcript.final: Detector.process_turn
              ──▶ EventBus: transcript.final + claim.detected + claim.state(escuchada)
              ──▶ live.server app re-emits every bus event over WS /ws
              ──▶ local WS subscriber in run_live.py (card latency)
```

Runner: `live/run_live.py`. Flags: `--video`, `--detector
{model,heuristic}`, `--no-server` (bus+log only), `--log run.jsonl`,
plus `--port`, `--duration`, `--chunk-ms`, `--keywords`,
`--language-bias`, `--api-key` (default `MODEL_API_KEY` env), `--record`.

Why Matroska-over-fifo instead of the default MPEG-TS-over-stdout: the
imageio static ffmpeg **segfaults demuxing MPEG-TS** (rc=139, any audio
codec; MP4/MKV demux fine), and fragmented-MP4 muxing buffers the whole
replay instead of streaming (first PCM at 7.5 s for an 8 s clip). MKV
flows progressively (first PCM ≈ 0.5 s wall). The sim stage is still
`live/ingest/simstream.py` with `-re`; only the restream container is
overridden via its `extra_args` seam (needs `-y`: ffmpeg refuses to
open an existing fifo otherwise). Audio-only restream: M1e needs no
video frames (vista scope).

Latency model: media clock (`t_end`) → wall via `t0_wall` (first PCM
byte ≈ media 0) under `-re` pacing. `t_end→claim.detected-bus` =
`bus_ts − (t0_wall + t_end)`; `fin-frase→tarjeta-WS` = local `/ws`
arrival − same origin (join key `claim_id`). Bus→WS fan-out kept as a
diagnostic (`fanout_ms`).

Cost model (measured): ASR = `floor(audio_s)/3600×$0.18` (per
`live/oido/CONTRACT.md`); model detector = summed `last_usage` tokens
through `src.atlas.agent.loop.estimate_cost` ($1.25/$4.25 per 1M).

## Exact commands (repo root `evidence-platform/`, `.env` sourced)

```bash
# Full keynote, heuristic detector (3:29, green)
set -a && . ./.env && set +a
.venv/bin/python live/run_live.py \
  --video data/keynote/keynote_en.mp4 --detector heuristic \
  --log data/runs/run-m1e-heuristic.jsonl \
  --record data/runs/run-m1e-heuristic.asr.jsonl

# Short clip, model detector (60 s, exposes U1 below)
.venv/bin/python live/run_live.py \
  --video data/keynote/keynote_en.mp4 --detector model --duration 60 \
  --log data/runs/run-m1e-model-60s.jsonl \
  --record data/runs/run-m1e-model-60s.asr.jsonl

# Offline tests (0 live: recorded ASR fixture + detector doubles)
.venv/bin/python -m pytest live/tests/test_e2e_live.py -q
```

## Results

| run | turns | claims | bus p50/p95 (ms) | WS p50/p95 (ms) | fanout p50 (ms) | ASR USD | model USD | total USD |
|---|---|---|---|---|---|---|---|---|
| heuristic, full 209 s | 11 | 20 | 1240 / 1314 | 1240 / 1314 | 0.4 | 0.0104 | 0.0000 | **0.0104** |
| model, 60 s clip | 2 | 2* | 5160 / 5160 | 5162 / 5162 | 1.6 | 0.0030 | 0.0079 | **0.0109** |

`*` model path failed on both turns → heuristic fallback (see U1).
ASR outcome `closed-1000` on all sessions; ffmpeg exit codes 0/0.

## N claims detected vs truth (counts only — verdicts are M2)

Truth `live/material/truth.json` (EN): **41** scripted entries, **31**
checkable (`true` 16 + `false` 15), 10 unverifiable.

- Heuristic full pass: **20** `claim.detected` spans from 11 ASR turns.
- Model 60 s clip: **2** spans (heuristic fallback) from 2 turns.

Counts are side-by-side, not 1:1: the detector emits verbatim spans of
*transcribed* turns (streaming endpointing merges several scripted
sentences per turn; missing mid-turn punctuation merges spans further),
while truth lists scripted claims. No verdicts here by design.

## Artifacts

- `live/run_live.py` — the runner (helpers `flatten_final`,
  `process_final`, `FifoStream`, `estimate_asr_usd`, `summarize_ms` are
  reused by the tests).
- `live/tests/test_e2e_live.py` — 6 offline tests: heuristic pipeline
  over `asr_sample.jsonl` (3 finals → 2 claims), model-double pipeline
  (worthiness/resolution/usage), event-contract test (JSON-only,
  `{seq,bus_ts}`, `claim.detected` shape, `escuchada`-only states
  joinable by `claim_id`, dense seqs, log == bus history), helper +
  wiring-shape tests.
- `data/runs/run-m1e-heuristic.jsonl` (51 events: 11 final + 20
  detected + 20 state; dense seq; M4 replay base) +
  `.summary.json` + `.asr.jsonl` (wire record).
- `data/runs/run-m1e-model-60s.jsonl` (6 events) + `.summary.json` +
  `.asr.jsonl`.

Full `live/tests/` suite: 50 passed (44 pre-existing + 6 new).

## Unresolved

- **U1 (detector owner, blocks model path):** `Detector` with
  `muse-spark` fails every turn: `finish_reason=length`, empty content
  — the 800-token completion budget (`DETECT_MAX_TOKENS` in
  `live/detector/claims.py`) is fully consumed by hidden reasoning
  (measured: 1600 completion tokens / 2 calls = exactly 2×800; prompts
  were small, ~440 tokens). The detector then falls back to heuristic
  (correct behavior), but model latency/cost is spent for nothing
  ($0.0079 wasted here). Fix belongs to the detector owner (raise the
  budget / cut reasoning); M1e deliberately does not wrap or mask it.
  `live/detector/claims.py` is outside the M1e file list.
- **U2 (observation):** streaming endpointing is nondeterministic
  across sessions — same audio split 0–30.5 s as one turn in the
  heuristic run vs 0–12.9 s + 13.1–59.6 s in the model run. Latency/count
  comparisons across runs must account for this.
- **U3 (pre-existing, static ffmpeg):** MPEG-TS demux segfaults
  (rc=139). M1e works around it with MKV-over-fifo; any future stage
  that demuxes MPEG-TS with this binary will hit the same crash.
