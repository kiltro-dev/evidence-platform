# Atlas Live — Hackathon Submission (paste-ready)

> Paste each section into the matching portal field. Every figure is
> sourced from `live/M*.md` or `data/runs/*.summary.json`; gaps are
> marked TODO-M8, never filled in.

## Title

Atlas Live — a real-time agent newsroom that fact-checks video while it plays

## Short description (≤280 chars)

Atlas Live listens to and watches a livestream, extracts checkable claims as
spoken, investigates them with parallel Muse agents, and publishes verdicts
with re-verifiable proof — model proposes, code disposes.

## Long description

AI-generated video is full of confident statistics nobody checks. Atlas Live
is an agent newsroom that fact-checks a stream in real time: Meta realtime
speech-to-text transcribes the audio, a claim detector extracts checkable
spans, parallel researcher agents gather evidence packages (URL, capture
date, sha256 snapshot, literal quote + offsets) from the open web, and a
deterministic verifier — never the model — decides `supported`,
`contradicted`, or `unverifiable`. A vision stage cross-checks said-vs-shown
numbers on slides, a skeptic red-teams every `supported` verdict with live
counter-evidence, a long-context check catches speaker self-contradictions,
and every run exports a self-verifying evidence bundle (`verify_bundle.py`
re-executes offline: ALL PASS or FAIL per claim). Verdict cards publish in
Spanish and English over WebSocket while the video keeps running.

## Problem

Live video carries false statistics at full confidence — wrong numbers,
truncated axes, cherry-picked charts — and post-hoc checking arrives after
the damage. LLM-judge guardrails can hallucinate about hallucinations. What
is missing is a real-time checker that shows its work: which sources were
consulted, what each source literally says, and what code verified versus
what the model guessed.

## Target user

Newsrooms and stream moderators running live events (keynotes, debates,
earnings calls), plus any viewer who wants verdict cards with proof instead
of a bare true/false label.

## Impact

On a synthetic 3:29 keynote with 41 scripted entries (31 checkable: 16 true
+ 15 false), Atlas Live decided 8 claims correctly with zero false
contradicts, caught a planted speaker self-contradiction (China 1.41B vs
141M, 57 s apart) and a said-vs-shown chart contradiction (40% said vs
0.97% shown), and held 1.0 accuracy on decided claims across 12 held-out
canary probes. Every number below is measured, with the honest limits in
the Limits section. (Sources: `live/M2D.md`, `live/M3.md`, `live/M6.md`,
`live/M7.md`.)

## Use of Meta capabilities (each with proving file)

1. **Muse Spark structured reasoning** — claim detection, swarm research
   roles, skeptic attacks, card drafting, and the long-context
   contradiction check all run as strict `json_schema` calls with pinned
   reasoning effort (`minimal`/`low`); every output is validated by code.
   Proving files: `live/detector/claims.py`, `live/swarm/roles.py`,
   `live/skeptic/attack.py`, `live/editor/cards.py`, `live/longctx/check.py`.
2. **Meta realtime speech-to-text** (`muse-voice-transcribe-1.0`) —
   streaming WebSocket ASR (PCM 24 kHz, diarization + endpointing,
   keywords, language bias) turns live audio into timestamped turns.
   Proving files: `live/oido/asr_ws.py`, `live/oido/CONTRACT.md`.
3. **Muse vision** — reads on-screen text, tables, and chart values/axes
   from scene frames under a strict schema; deterministic code then
   cross-checks said-vs-shown (2% tolerance). Proving files:
   `live/vista/read.py`, `live/vista/xcheck.py`.
4. **Web grounding (`web_search` + responsible fetch)** — researchers and
   the skeptic retrieve live counter-evidence (robots-respecting fetch,
   2 MB cap, paywall refusals). Proving files: `live/swarm/roles.py`,
   `live/skeptic/attack.py`.

## Architecture

```
video ─▶ simstream (ffmpeg -re, "simulated-stream") ─▶ fifo ─▶ ffmpeg demux (PCM 24 kHz)
  ─▶ Meta realtime ASR ─▶ per final: Detector.process_turn
  ─▶ EventBus (transcript.final / claim.detected / claim.state)
  ─▶ swarm researchers ─▶ claim.evidence ─▶ EvidencePackage ─▶ store
  ─▶ deterministic verifier ─▶ claim.verdict ─▶ skeptic ─▶ editor card (ES/EN)
  ─▶ WebSocket /ws + newsroom UI + evidence bundle ZIP
Side stages: vista (frames ─▶ vision read ─▶ said-vs-shown xcheck),
longctx (full transcript ─▶ proposed pairs ─▶ code-verified speaker.contradiction),
auditor (12 canaries ─▶ accuracy + Wilson panel).
```

Event log per run in `data/runs/*.jsonl` (+ `.summary.json`); UI replays
any recorded run with zero model cost (`GET /replay`, `WS /ws?replay=`).

## Stack

Python ≥3.11, FastAPI + uvicorn (server + WS), `imageio-ffmpeg` static
ffmpeg (demux/restream), Meta API (`muse-spark`, `muse-voice-transcribe-1.0`,
vision, `web_search`), World Bank Open Data + Wikipedia (evidence sources),
vanilla JS newsroom UI (no framework), pytest (206+ offline tests).

## Honest limits

- Recall 8/31 on the 31 checkable EN claims (7 true supported incl.
  T1/T2/T3/T5/T6/T7/T8, 1 false contradicted F3; 22 abstentions).
  Source: `live/M2D.md` + `data/runs/verify-20261101-212343.jsonl.summary.json`.
- Verdict latency p50 78.4 s / p95 90.0 s per claim (invest→verdict);
  ASR end-phrase→card p50 1240 ms on the M1e heuristic pass.
  Source: M2d summary, `live/M1_E2E.md`.
- One false `supported` on FALSE (F14: WB 2020 population within 1.86%
  of the 8B milestone claim — a tolerance-edge year-swap our neutralized
  temporal check cannot catch). Precision on decided: 8/9 (89%).
  Source: `live/M2D.md`.
- Zero false `contradicted` on TRUE held across M2c, M2d, and M6.
  Source: `live/M2_E2E.md`, `live/M2D.md`, `live/M6.md`.
- Physics/geo units (`meters`, `kilometers`, `degrees`, `square
  kilometers`) are outside the unit parser → those claims abstain by
  design; numberless claims abstain by design. Source: `live/M2D.md`.
- Swarm is non-deterministic across runs (F6 contradicted→unverifiable
  between M2c and M2d on 0 packages). Source: `live/M2D.md`.
- Model detector path hits a detector-budget bug (`finish_reason=length`
  on an 800-token budget consumed by hidden reasoning; falls back to
  heuristic). Source: `live/M1_E2E.md` U1.
- Formal ablation (M8, `ablation-20261101-214721-report.json`): Atlas
  M2d 8/9=0.889 [0.565,0.980] vs model-only baseline run1 30/31=0.968
  [0.838,0.994] vs run2 31/31=1.000 [0.890,1.000] (31 EN × 2 runs,
  run1-vs-run2 consistency 30/31; hallucinations 15/15 baseline vs
  Atlas 1; cost/claim $0.0310 vs $0.0355 vs $0.0281; p50 78.4s vs 4.5s
  vs 4.2s). The baseline wins everything measured — stated plainly.
  Atlas keeps only: re-verifiable proof bundles, 0 false-contradicted
  across M2d+M6 (baseline run1 committed one P0 on T7), and
  said-vs-shown vision checks. Caveat: baseline urls are model-stated,
  0/62 code-validated. Source: `live/M8.md`.

## How to reproduce ($0 judge path + live path)

```bash
cd evidence-platform
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt imageio-ffmpeg
# $0 replay: recorded 51-event run, no model calls, no key needed
.venv/bin/python -m uvicorn live.server.app:create_app --factory --port 8891
# open http://127.0.0.1:8891/?replay=run-m1e-heuristic
# Live keynote (needs MODEL_API_KEY in .env; ≈$0.01 ASR heuristic pass, M1e):
set -a && . ./.env && set +a
.venv/bin/python live/run_live.py --video data/keynote/keynote_en.mp4 \
  --detector heuristic --log data/runs/judge.jsonl
# Offline tests (0 live): .venv/bin/python -m pytest live/tests/ -q -m "not live"
```

## Licenses & data rights

- **TTS voice**: Piper (MIT) with `en_US-ljspeech-medium`; training data
  LJSpeech is public domain; espeak-ng (GPLv3) used as a local build tool
  only, not distributed. Source: `live/material/LICENSES.md`.
- **Video fixture**: synthetic original material (script, slides,
  narration, video) created for this project; fictional summit, no real
  persons. The 15 false claims + 4 misleading charts are deliberate traps.
  Source: `live/material/LICENSES.md`.
- **Fact references** (baked into `truth.json`, not redistributed):
  World Bank Open Data (CC BY-4.0), UN Day of Eight Billion, SI/BIPM
  constant, NASA planetary facts (U.S. government public domain), 2020
  China–Nepal Everest survey, Britannica (Sahara figure).
  Source: `live/material/LICENSES.md`.
- **Code deps**: FastAPI, uvicorn, httpx, pydantic, openai, python-dotenv,
  pytest, hypothesis (see `requirements.txt`); imageio-ffmpeg (BSD-2,
  bundles LGPL ffmpeg); matplotlib/numpy for fixture charts.
- **Repo license**: see `LICENSE` at repo root.

## Links (fill at submit time)

- Demo video URL: TODO-M8 (M9b produces the video; this kit is docs only)
- Evidence bundle ZIP (judge re-run): TODO-M8 (export from a fresh
  `verify-*` run; verifier: `live/bundle/verify_bundle.py bundle.zip`)
- Repo + commit: TODO-M8 (fill portal field after login — see
  `live/demo/CHECKLIST_HUMANO.md`)
