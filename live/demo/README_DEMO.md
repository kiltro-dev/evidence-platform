# Atlas Live — judge README (30 s to get it, 3 min to try it, $0)

**What it is:** a real-time agent newsroom. It listens to and watches a
video while it plays, extracts checkable claims as spoken, investigates
them with parallel Muse agents over the open web, and publishes verdict
cards (ES/EN) with re-verifiable proof. The model proposes; deterministic
code disposes. Every figure below comes from `live/M*.md` or
`data/runs/*.summary.json`.

**Proof in one line:** on a synthetic keynote (31 checkable claims), 8
decided correctly, 0 false contradicts, 1.0 canary accuracy — with all
limits disclosed in `SUBMISSION.md`.

## Install (<10 min, clean machine)

```bash
cd evidence-platform
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt imageio-ffmpeg
```

Requires Python ≥3.11 (`pyproject.toml`); no API key for the $0 path.

## 1 command: $0 replay (no key, no model calls)

```bash
.venv/bin/python -m uvicorn live.server.app:create_app --factory --port 8891
```

Then open `http://127.0.0.1:8891/?replay=run-m1e-heuristic`
(or pick the run from the "Real-run replay" dropdown on `/`).

This re-emits the recorded 51-event M1e run (11 ASR turns → 20 claims →
states) through the newsroom UI at original wall-clock pacing. $0: replay
serves `data/runs/*.jsonl` verbatim, no network, no model.

## 1 command: live keynote (needs key)

```bash
set -a && . ./.env && set +a   # must define MODEL_API_KEY
.venv/bin/python live/run_live.py --video data/keynote/keynote_en.mp4 \
  --detector heuristic --log data/runs/judge.jsonl
```

Cost ≈$0.01 ASR for the 3:29 keynote (M1e measured $0.0104 heuristic
pass). Full verification of all 31 claims (`live/run_verify.py --lang en`)
cost $0.96 and 611 s wall in M2d — run only if you accept that.

## What to look at

1. **Cards** (right side): `escuchada → investigando → veredicto` per
   claim, ES/EN toggle, click the timeline to jump the video (M4).
2. **The caught lie**: F3 "China fell to 141M" contradicted via World
   Bank (M2d), and self-contradicted vs T2 "1.41B" 57 s earlier with
   `rel_diff=0.9` (M7, `speaker.contradiction`).
3. **The chart vs the speaker**: slide 7 says "40%" but shows 0.97%
   (`visual.mismatch`, d=0.976); slides 6/10 truncated-axis flags (M3).
4. **The proof bundle**: any run exports a ZIP that re-verifies offline
   via `live/bundle/verify_bundle.py` — ALL PASS or per-claim FAIL (M5).
5. **The honest panel**: 12 canaries, accuracy 1.0, Wilson 95%
   [0.65, 1.0] (M6); full limits (recall 8/31, p50 78 s) in
   `live/demo/SUBMISSION.md`.

## Offline tests ($0)

```bash
.venv/bin/python -m pytest live/tests/ -q -m "not live"   # 206 passed (M6 era)
```
