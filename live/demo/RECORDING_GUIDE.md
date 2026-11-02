# RECORDING GUIDE — Atlas Live demo (~2:30, human voice)

Record your screen while driving the app below. Total budget 2:30
(portal cap 3:00). Speak the SAY lines verbatim; do the DO lines
on screen. Work in English UI (toggle EN, top-right).

## 0. Setup (before recording, ~3 min)

1. `git clone https://github.com/kiltro-dev/evidence-platform && cd evidence-platform`
2. `uv venv -p 3.12 && uv pip install --python .venv/bin/python -r requirements.txt`
3. `PYTHONPATH=src:live .venv/bin/python -m uvicorn live.server.app:create_app --factory --host 127.0.0.1 --port 8891`
4. Open http://127.0.0.1:8891 — you should see the newsroom UI.
5. Recording: Windows `Win+Alt+R` (Xbox Game Bar, captures tab audio
   off — speak over it later, or narrate live) or OBS (Display Capture
   + Mic). Record at 1080p. Clap once at start for sync.

## 1. Script (timestamps = video time)

### 0:00–0:20 — Hook
- DO: UI visible, replay selector on `verify-keynote-en-investigate`,
  rate 4×. Press **Watch replay**.
- SAY: "This is Atlas Live. It watches video and checks every number
  while it plays — with proof you can re-verify yourself."

### 0:20–0:55 — False claim caught live
- DO: let subtitles + cards run. Pause on the CONTRADICTED card
  (China population). Click the card → show quote + snapshot hash.
- SAY: "The speaker says China has 141 million people. Agents open
  the World Bank, quote it literally, and the code — not the model —
  calls it contradicted. Here is the quote and its hash."

### 0:55–1:25 — Said-vs-shown
- DO: switch replay to `run-keynote-en-listen`, jump timeline to the
  chart slide. Hover the mismatch badge (40% said vs 0.97% shown).
- SAY: "It also watches the slides. The speaker says forty percent;
  his own chart shows zero point nine seven. Said versus shown —
  caught on screen."

### 1:25–1:55 — Proof bundle
- DO: open a terminal. Run verify on the shipped bundle
  (see `live/M5.md` for the exact command). Show `ALL PASS`.
- SAY: "Every verdict ships a proof bundle: snapshots, hashes, and a
  script anyone re-runs offline. No trust required."

### 1:55–2:20 — Honest numbers
- DO: show the metrics panel (or `live/F2_P95.md` summary):
  retention 12/18, zero false verdicts on benchmark v2,
  p50 ~5 seconds, Muse-only baseline wins recall but only
  14% of its quotes exist.
- SAY: "On sealed ground truth: twelve of eighteen, zero false
  verdicts, five-second median. The raw model answers more — but
  only fourteen percent of its quotes are real. We abstain instead
  of guessing."

### 2:20–2:30 — Close
- DO: back to UI, banner "REPLAY OF REAL RUN" visible.
- SAY: "Atlas Live: model proposes, code disposes. All measured,
  all reproducible — try it."

## 2. After recording
1. Trim head/tail; check duration ≤ 3:00.
2. Watch once: no secrets, emails, paths, or billing on screen.
3. Upload to YouTube/Loom (unlisted OK) → paste link in portal.
4. If anything breaks live, record the replay path only — it is
   labeled as replay and needs no API key.
