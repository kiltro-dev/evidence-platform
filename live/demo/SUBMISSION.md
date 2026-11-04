# Atlas Live — Hackathon Submission (paste-ready)

> Paste each section into the matching portal field. Every figure is
> sourced from `live/M*.md` or `data/runs/*.summary.json`; gaps are
> marked TODO-M8, never filled in.

## Title

Atlas Live — a real-time agent newsroom that checks video with re-verifiable proof

## Short description (≤280 chars)

Atlas Live listens to and watches a video stream (file or link, realtime-paced),
extracts checkable claims as spoken, investigates them with parallel Muse agents,
and publishes verdicts with re-verifiable proof — model proposes, code disposes.

## Long description

AI-generated video is full of confident statistics nobody checks. Atlas Live
is an agent newsroom that fact-checks a stream in real time (realtime-paced
ingest of file/link windows, incl. HLS): Meta realtime speech-to-text
transcribes the audio, a claim detector extracts checkable spans,
parallel researcher agents gather evidence packages (URL, capture
date, sha256 snapshot, literal quote + offsets) from the open web, and a
deterministic verifier — never the model — decides `supported`,
`contradicted`, or `unverifiable`. A vision stage cross-checks said-vs-shown
numbers on slides (offline path over the materialized file), an advisory
skeptic pass probes `supported` verdicts with counter-evidence fetched from the open web
(deferred by default, never blocking), a long-context check catches
speaker self-contradictions, and every run exports a self-verifying
evidence bundle (`verify_bundle.py` re-executes offline: ALL PASS or
FAIL per claim). Verdict cards publish in Spanish and English over
WebSocket while the video keeps running.

## Problem

Published video carries false statistics at full confidence — wrong numbers,
truncated axes, cherry-picked charts — and post-hoc checking arrives after
the damage. LLM-judge guardrails can hallucinate about hallucinations. What
is missing is a real-time checker that shows its work: which sources were
consulted, what each source literally says, and what code verified versus
what the model guessed.

## Target user

Newsrooms and stream moderators running streamed events (keynotes, debates,
earnings calls), plus any viewer who wants verdict cards with proof instead
of a bare true/false label.

## Impact

<!-- METRICS:IMPACT:START -->
On the fair benchmark v2 (36 DEV claims + 18 sealed retention claims, preregistered), Atlas Live decides 26/36 = 0.722 (recall Wilson [0.560,0.842]) on DEV and 14/18 = 0.778 (recall Wilson [0.548,0.910]) on retention with zero false verdicts on either set (26/26 [0.871,1.000] + 14/14 [0.785,1.000] decided-accuracy Wilsons; coverage and precision always reported together), verdict latency p50 ~5 s / p95 ~40 s at ~$0.007/claim (invest-to-verdict only), said-vs-shown 4/4 planted (40% said vs 0.97% shown), canaries 7/7 decided of 12 (5 abstained, all FALSE-side), proof bundles 31/31 ALL PASS; 1-byte tamper => FAIL, Spanish ASR 0.03 WER, 41/41 ASR-recall, and a planted speaker self-contradiction caught (China 1.41B vs 141M, ~59 s apart in the native-ES keynote). The model-only baseline beats us on recall (70/72 DEV, 18/18 retention) — stated plainly — but only 9/63 = 0.143 (fidelity Wilson [0.077,0.250]) of its citations are faithful (verbatim quote + figure within 2% on the cited URL; NOT directly comparable to Atlas fidelity, different mechanisms) and it exports zero re-runnable proof.
<!-- METRICS:IMPACT:END -->
(Sources: `live/F2_RECALL.md` + addendum,
`live/F2_BENCH.md`, `live/F2_LATENCY.md`, `live/F2_VOICE.md`,
`live/M3.md`, `live/M7.md`. Every figure is rendered from
`live/claims_registry.json`; honest limits in the Limits section.)

## Use of Meta capabilities (each with proving file)

1. **Muse Spark structured reasoning** — claim detection, swarm research
   roles, skeptic attacks, card drafting, and the long-context
   contradiction check all run as strict `json_schema` calls with pinned
   reasoning effort (`minimal`/`low`); every output is validated by code.
   Proving files: `live/detector/claims.py`, `live/swarm/roles.py`,
   `live/skeptic/attack.py`, `live/editor/cards.py`, `live/longctx/check.py`.
2. **Meta realtime speech-to-text** (`muse-voice-transcribe-1.0`) —
   streaming WebSocket ASR (PCM 24 kHz, diarization + endpointing,
   keywords, language bias) turns stream audio into timestamped turns.
   Proving files: `live/oido/asr_ws.py`, `live/oido/CONTRACT.md`.
3. **Muse vision** — reads on-screen text, tables, and chart values/axes
   from scene frames under a strict schema; deterministic code then
   cross-checks said-vs-shown (2% tolerance). Proving files:
   `live/vista/read.py`, `live/vista/xcheck.py`.
4. **Web grounding (`web_search` + responsible fetch)** — researchers and
   the skeptic retrieve counter-evidence from the open web (robots-respecting fetch,
   2 MB cap, paywall refusals). Proving files: `live/swarm/roles.py`,
   `live/skeptic/attack.py`.

## Architecture

```
video ─▶ simstream (ffmpeg -re, "simulated-stream") ─▶ fifo ─▶ ffmpeg demux (PCM 24 kHz)
  ─▶ Meta realtime ASR ─▶ per final: Detector.process_turn
  ─▶ EventBus (transcript.final / claim.detected / claim.state)
  ─▶ swarm researchers ─▶ claim.evidence ─▶ EvidencePackage ─▶ store
  ─▶ deterministic verifier ─▶ claim.verdict ─▶ editor card (ES/EN)
  (+ advisory skeptic probe, deferred by default, never blocking)
  ─▶ WebSocket /ws + newsroom UI + evidence bundle ZIP
Side stages: vista (frames ─▶ vision read ─▶ said-vs-shown xcheck),
longctx (full transcript ─▶ proposed pairs ─▶ code-verified speaker.contradiction),
auditor (12 canaries, 7 decided 7/7 ─▶ accuracy + Wilson panel).
```

Event log per run in `data/runs/*.jsonl` (+ `.summary.json`); UI replays
any recorded run with zero model cost (`GET /replay`, `WS /ws?replay=`).

## Stack

Python ≥3.11, FastAPI + uvicorn (server + WS), `imageio-ffmpeg` static
ffmpeg (demux/restream), Meta API (`muse-spark`, `muse-voice-transcribe-1.0`,
vision, `web_search`), World Bank Open Data + Wikipedia (evidence sources),
vanilla JS newsroom UI (no framework), pytest (1073 passed +
9 skipped of 1082 collected; v1-core 755 passed).

## Honest limits

- Recall 26/36 = 0.722 on DEV (a 5/6, b 0/6, c 6/6, d 4/6, e 5/6, f 6/6;
  10 abstentions) and 14/18 = 0.778 on sealed retention, 0 false
  verdicts on either set; decided accuracy 26/26 + 14/14 = 1.000.
  Source: `data/runs/v5p0-dev-atlas-run1.json` + V6 validation
  (`data/runs/v6ret-atlas-run1.json`; seal 5278ba4c intact, retention
  ran 3x Atlas-arm total — 12/18 (F2-2), 12/18 (F2-3), 14/18 (V6) —
  read-only, no tuning: the V5-1 arithmetic guard generalizes to 2/3
  unseen d-claims).
- Corpus false contradicts (declared): 3 systematic difference-claim
  errors found in S4 (C1D3/D5/D7) — FIXED by the V5-1 arithmetic guard
  (s1 false-C list empty on re-run); 1 open retrieval-variance case on
  the EN bench (P1-17-en: spurious wiki quote cited, model-path,
  pre-existing, not verifier-caused). Corpus EN post-fix: 75/181
  decided, 74/75 correct. Source: `live/V5_P0.md`, `live/V5_2B.md`.
- Verdict latency (F2-9) p50 4.9/5.0 s per claim (invest→verdict
  ONLY — excludes ASR+detection; NOT end-to-end video latency),
  down from 80.3 s (16×); wall for 36 claims ~78 s (−79% vs 372.4 s);
  $0.0073/0.0059 per claim; 20/36 claims exit via the ~4 s fast lane.
  The p95 at 40.7/38.9 s MEETS the ≤60 s goal with ~20 s of margin —
  0 claims over 60 s in both runs (before: 7), with the 90 s deadline
  aborting the tail (it caps, it doesn't measure, the worst case).
  Lever: a no-progress early abort cut the old A3+B1–B6 tail from
  530 s to 274 s (−48%). Same 22/36 verdicts as F2-7, claim by claim
  (at F2-9 time; V5-1 later moved DEV to 26/36). Source:
  `data/runs/f2p95-final-report.json`, `live/F2_P95.md` §4.
- Canaries (M6 run): 12 run, 7 decided 7/7 correct, Wilson
  [0.646,1.000] (wide at n=12), 5 abstentions ALL on the FALSE side,
  $0.29. Source: `live/M6.md`, `live/eval2/scores/canaries.json`.
- DEV-b abstains 0/6 (IMF/BLS primaries return 403; only secondaries,
  out of budget). DEV-d decides 4/6 after the V5-1 guard (was 0/6:
  multi-source arithmetic by design, `arithmetic_needs_computation`);
  the 2 remaining abstain by design. Category b overall is 0/42 and
  the real FOMC case 5/5 unverifiable for lack of official API keys —
  see `live/KEYS_NEEDED.md`; both will be re-run if keys arrive
  before submission. Source: `live/F2_RECALL.md`, `live/V5_P0.md`,
  `live/V5_CATB.md`.
- Data & tier: non-replay runs send video/audio + claim text
  to Meta's Model API on the Contributor tier; don't stream sensitive
  content. Browser microphone/tab capture is NOT implemented
  (server mic/file/link input only); DASH playlists, encrypted
  playlists, and YouTube (refused by policy — paste a direct
  mp4/m3u8 or a page with embedded video instead) are NOT supported.
  The "clip 2" voice robustness
  case is synthetic (VOA-mix had no clean ≥20 s segment; clip2b
  synthetic closes it). Source: `live/F2_VOICE.md`, `live/V6_LINK.md`.
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
<!-- METRICS:BENCH:START -->
- Fair benchmark v2 (F2-1, `eval2-20261101-225539-report.json` + fidelity JSONs): Atlas DEV 26/36 vs model-only baseline pooled 70/72 = 0.972 [0.904,0.992] (run1 35/36, run2 35/36, consistency 36/36) and retention 18/18 = 1.000 [0.824,1.000]. The baseline wins recall — stated plainly. Where it fails: citation fidelity pooled 9/63 = 0.143 [0.077,0.250] (r1 5/31, r2 4/32; quote_not_found 34, unit_mismatch 13, numeric_mismatch 7; 72/72 urls model-stated, 0 browsed), 2 false contradicts (D3x2), and zero offline proof. Source: `live/F2_BENCH.md`.
<!-- METRICS:BENCH:END -->
  What Atlas adds: re-verifiable proof bundles (31/31 ALL PASS, 1-byte
  tamper ⇒ FAIL — shipped: `live/demo/atlas-live-bundle.zip`),
  0 false contradicts on benchmark v2 dev+retention,
  and said-vs-shown vision checks.

## How to reproduce ($0 judge path + link-ingest path)

```bash
git clone https://github.com/kiltro-dev/evidence-platform && cd evidence-platform
uv venv -p 3.12 && uv pip install --python .venv/bin/python -r requirements.txt
# $0 replay: recorded 48-event ES run, no model calls, no key needed
PYTHONPATH=src:live .venv/bin/python -m uvicorn live.server.app:create_app --factory --host 127.0.0.1 --port 8891
# open http://127.0.0.1:8891/?replay=run-es-native
# Link ingest, Vivo por enlace (needs MODEL_API_KEY in .env; $0.0015–0.01 per 30 s
# depending on claims — V6 proof run $0.0015 with 0 claims):
curl localhost:8891/ingest -H 'Content-Type: application/json' \
  -d '{"url":"https://images-assets.nasa.gov/video/A1Launch/A1Launch~preview.mp4","mode":"vivo","duration":30}'
# then open ?replay=link-<run_id> and /report/link-<run_id>?format=html
# Offline gate (no network, no key):
PYTHONPATH=src:live .venv/bin/python -m pytest live/tests -q
PYTHONPATH=src:live .venv/bin/python scripts/check_claims.py
# Proof bundle re-verify (shipped): PYTHONPATH=src:live .venv/bin/python live/bundle/verify_bundle.py live/demo/atlas-live-bundle.zip
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

- Demo video URL: TODO-HUMAN (record with `live/demo/RECORDING_GUIDE.md`,
  upload, paste the link here and in the portal)
- Evidence bundle ZIP (judge re-run): `live/demo/atlas-live-bundle.zip`
  (31/31 ALL PASS; verifier: `live/bundle/verify_bundle.py bundle.zip`)
- Repo + commit: https://github.com/kiltro-dev/evidence-platform ,
  tag `submittable-18` (coherence freeze; see
  `live/demo/CHECKLIST_HUMANO.md` for the video + portal steps)
