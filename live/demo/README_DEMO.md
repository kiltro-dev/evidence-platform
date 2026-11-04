# Atlas Live — judge README (30 s to get it, 3 min to try it, $0)

**What it is:** a real-time agent newsroom. It listens to and watches a
video while it plays, extracts checkable claims as spoken, investigates
them with parallel Muse agents over the open web, and publishes verdict
cards (ES/EN) with re-verifiable proof. The model proposes; deterministic
code disposes. Every figure below comes from `live/M*.md` or
`data/runs/*.summary.json`.

**Proof in one line:** on a synthetic keynote (31 checkable claims), 9
decided, 8/9 correct with 1 declared false-supported (F14-en) and 0
false contradicts, canaries 7/7 decided of 12 — with all limits
disclosed in `SUBMISSION.md`. (This is the M2d keynote experiment, not
benchmark v2; bench numbers: README metrics table.)

## Install (<10 min, clean machine)

```bash
git clone https://github.com/kiltro-dev/evidence-platform && cd evidence-platform
uv venv -p 3.12 && uv pip install --python .venv/bin/python -r requirements.txt
```

Requires Python ≥3.11; no API key for the $0 path.

## 1 command: $0 replay (no key, no model calls)

```bash
PYTHONPATH=src:live .venv/bin/python -m uvicorn live.server.app:create_app --factory --host 127.0.0.1 --port 8891
```

Then open `http://127.0.0.1:8891/?replay=run-es-native`
(or pick any run from the "Real-run replay" dropdown on `/`;
`run-m1e-heuristic` and `verify-keynote-en-investigate` also ship).

This re-emits the recorded 51-event M1e run (11 ASR turns → 20 claims →
states) through the newsroom UI at original wall-clock pacing. $0: replay
serves `data/runs/*.jsonl` verbatim, no network, no model.

## 1 command: live keynote (needs key)

Live local-file mode needs the git-ignored `data/` corpus; on a fresh
clone use link ingest instead (needs `MODEL_API_KEY` in `.env`):

```bash
curl localhost:8891/ingest -H 'Content-Type: application/json' \
  -d '{"url":"https://images-assets.nasa.gov/video/A1Launch/A1Launch~preview.mp4","mode":"vivo","duration":30}'
```

Cost $0.0015–0.01 per 30 s depending on claims (proof run $0.0015,
0 claims). Then `?replay=link-<run_id>`.
Full verification of all 31 keynote claims (`live/run_verify.py
--lang en`) cost $0.96 and 611 s wall in M2d — run only if you accept
that (and only with the local `data/` corpus).

## What to look at

1. **Cards** (right side): `escuchada → investigando → veredicto` per
   claim, ES/EN toggle, click the timeline to jump the video (M4).
2. **The caught lie**: F3 "China fell to 141M" contradicted via World
   Bank (M2d), and self-contradicted vs T2 "1.41B" ~59 s earlier with
   `rel_diff=0.9` (M7, `speaker.contradiction`).
3. **The chart vs the speaker**: slide 7 says "40%" but shows 0.97%
   (`visual.mismatch`, d=0.976); slides 6/10 truncated-axis flags (M3).
4. **The proof bundle**: any run exports a ZIP that re-verifies offline
   via `live/bundle/verify_bundle.py` — ALL PASS or per-claim FAIL (M5).
5. **The honest panel**: 12 canaries, 7 decided 7/7, Wilson 95%
   [0.65, 1.0] (M6); the shipped verify log itself is 8/9 with the
   F14-en false-supported disclosed above; full limits in
   `live/demo/SUBMISSION.md`.

## Offline tests ($0)

```bash
PYTHONPATH=src:live .venv/bin/python -m pytest live/tests -q   # 1071 passed
PYTHONPATH=src:live .venv/bin/python scripts/check_claims.py   # numbers guard
```
