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

On the fair benchmark v2 (36 DEV claims + 18 sealed retention claims,
preregistered), Atlas Live decides 22/36 = 0.611 on DEV and 12/18 = 0.667
on retention with zero false contradicts in both (22/22 + 12/12 decided
accuracy), caught a planted speaker self-contradiction (China 1.41B vs
141M, ~59 s apart in the native-ES keynote) and a said-vs-shown chart
contradiction (40% said vs 0.97% shown), transcribes native Spanish at
0.03 WER with 41/41 ASR-recall, and exports tamper-evident proof bundles
(31/31 ALL PASS; 1 flipped byte ⇒ FAIL). The model-only baseline beats
us on recall (70/72 DEV, 18/18 retention) — stated plainly — but only
14% of its citations are faithful to the source (9/63) and it exports
zero re-runnable proof. Every number is measured, with the honest limits
in the Limits section. (Sources: `live/F2_RECALL.md` + addendum,
`live/F2_BENCH.md`, `live/F2_LATENCY.md`, `live/F2_VOICE.md`,
`live/M3.md`, `live/M7.md`.)

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

- Recall 22/36 = 0.611 on DEV (a 5/6, b 0/6, c 6/6, d 0/6, e 5/6, f 6/6;
  14 abstentions) and 12/18 = 0.667 on sealed retention, 0 false
  contradicts in both; decided accuracy 22/22 + 12/12 = 1.000.
  Source: `data/runs/f2recall-fix-report.json` + `live/F2_RECALL.md`
  addendum (retention; `retention.json` never opened).
- Verdict latency (F2-9) p50 4.9/5.0 s per claim (invest→verdict),
  down from 80.3 s (16×); wall for 36 claims ~78 s (−79% vs 372.4 s);
  $0.0073/0.0059 per claim; 20/36 claims exit via the ~4 s fast lane.
  The p95 at 40.7/38.9 s MEETS the ≤60 s goal with ~20 s of margin —
  0 claims over 60 s in both runs (before: 7). Lever: a no-progress
  early abort cut the old A3+B1–B6 tail from 530 s to 274 s (−48%).
  Same 22/36 verdicts as F2-7, claim by claim. Source:
  `data/runs/f2p95-final-report.json`, `live/F2_P95.md` §4.
- DEV-b abstains 0/6 (IMF/BLS primaries return 403; only secondaries,
  out of budget) and DEV-d abstains 0/6 (multi-source arithmetic by
  design: `arithmetic_needs_computation` guard). Source: `live/F2_RECALL.md`.
- Zero false `contradicted` held across F2-2 (44/44 pooled), F2-3
  (22/22), F2-7 (44/44 pooled, both runs), and F2-9 (44/44 pooled,
  both runs); the F2-1 D5 false-C is eliminated by construction
  (F6 guard).
  Source: `live/F2_RECALL.md`, `live/F2_LATENCY.md`, `live/F2_LAT2.md`,
  `live/F2_P95.md`.
- Physics/geo units (`meters`, `kilometers`, `degrees`, `square
  kilometers`) are outside the unit parser → those claims abstain by
  design; numberless claims abstain by design. Source: `live/M2D.md`.
- Model detector path hits a detector-budget bug (`finish_reason=length`
  on an 800-token budget consumed by hidden reasoning; falls back to
  heuristic). Source: `live/M1_E2E.md` U1.
- Fair benchmark v2 (F2-1, `eval2-20261101-225539-report.json` +
  fidelity JSONs): Atlas DEV 22/36 vs model-only baseline pooled
  70/72 = 0.972 [0.904,0.992] (run1 35/36, run2 35/36, consistency
  36/36) and retention 18/18 [0.824,1.000]. The baseline wins recall —
  stated plainly. Where it matches us: verdicts (70/72) and speed
  (p50 ~5–10 s vs Atlas F2-9 ~5 s). Where it fails: citation fidelity pooled
  9/63 = 0.143 [0.077,0.250] (r1 5/31, r2 4/32; quote_not_found 34,
  unit_mismatch 13, numeric_mismatch 7; 72/72 urls model-stated,
  0 browsed), 2 false contradicts (D3×2), and zero offline proof.
  What Atlas adds: re-verifiable proof bundles (31/31 ALL PASS, 1-byte
  tamper ⇒ FAIL), 0 false contradicts, and said-vs-shown vision checks.
  Source: `live/F2_BENCH.md`.

## How to reproduce ($0 judge path + live path)

```bash
cd evidence-platform
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt imageio-ffmpeg
# $0 replay: recorded 48-event ES run, no model calls, no key needed
.venv/bin/python -m uvicorn live.server.app:create_app --factory --port 8891
# open http://127.0.0.1:8891/?replay=run-es-native
# Live keynote ES (needs MODEL_API_KEY in .env; ≈$0.01 ASR heuristic pass):
set -a && . ./.env && set +a
.venv/bin/python live/run_live.py --video data/keynote_es/keynote_es_native.mp4 \
  --detector heuristic --language-bias "Spanish" --log data/runs/judge.jsonl
# Offline tests (0 live): .venv/bin/python -m pytest live/tests/ -q -m "not live"
```

## Licenses & data rights

- **TTS voice**: Piper (MIT) with `es_ES-carlfm-x_low` (native Spanish,
  public domain, trained from scratch) for the ES keynote + demo
  narration; espeak-ng (GPLv3) used as a local build tool only, not
  distributed. Source: `live/material/LICENSES_VOZ.md`.
- **Video fixtures**: 100% synthetic original material (scripts,
  slides, TTS narration, code-rendered video) created for this project:
  12-piece corpus (keynotes, 2-speaker panels, science classes,
  fictional earnings calls, EN+ES), each labeled "Synthetic demo" on
  screen and in the UI. Fictional presenters, no real persons. False
  claims + misleading charts are deliberate traps with known truth.
  Corpus metrics measure the system against known truth, NOT real-world
  performance. Sources: `live/SYNTH_EN.md`, `live/SYNTH_ES.md`,
  `live/S4_SYNTHQA.md`, `live/material/PROVENANCE_{EN,ES}.md`.
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
