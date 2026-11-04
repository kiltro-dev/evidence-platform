# Atlas Live — agent newsroom (real-time evidence engine)

Listens to and watches a video file or link ingest (realtime-paced),
extracts checkable claims as they are spoken, investigates them with
parallel Muse agents over the open web, verifies with deterministic
code, and publishes verdict cards with re-verifiable proof over
WebSocket while the video runs.

Status: shipped (tags `submittable-1..18`). Entry points:
`live/run_live.py` (listen pipeline), `live/run_verify.py`
(investigation), `live/server/app.py` + `live/ui/` (server + newsroom),
`scripts/check_claims.py` (numbers guard).

Layout (actual):

- `live/ingest/` — link resolve (SSRF) + Turbo modes, HLS fetch,
  TS→MKV remux, ffmpeg demux (PCM 24 kHz + scene frames), simulated
  stream (`ffmpeg -re`, labeled `simulated-stream`).
- `live/oido/` — muse-voice-transcribe WebSocket client (streaming,
  diarization, endpointing, keywords, language_bias).
- `live/vista/` — on-screen reads + said-vs-shown cross-check
  (offline path over materialized files).
- `live/detector/` — claim worthiness, coreference, dedup.
- `live/orchestrator/` — event bus, claim state machine, budgets,
  deadlines, early aborts.
- `live/swarm/` — parallel investigators (structurer, retriever,
  red-team) → evidence packages (URL, capture date, hash, local
  snapshot, literal quote + offsets, source type, numbers).
- `live/library/` — persistent snapshot/claim store (FTS5 + BM25 +
  Muse re-rank; no embeddings).
- `live/verifier/` — deterministic package validation + arithmetic
  on top of the v1 numeric core (`src/atlas/core/`).
- `live/skeptic/` (advisory, deferred), `live/editor/` (ES/EN cards),
  `live/auditor/` (canaries + Wilson), `live/longctx/`
  (speaker self-contradictions, code-confirmed).
- `live/server/` + `live/ui/` — FastAPI app + WebSocket + newsroom
  page; `live/replay/` (recorded runs + player); `live/report/`
  (HTML/JSON/ZIP export); `live/bundle/` (proof package +
  `verify_bundle.py`); `live/simulive/` (HLS loop + mount).
- `live/mic/` (server mic/file input; no browser capture),
  `live/eval2/` (benchmark v2: prereg, dev/retention, fidelity,
  committed `scores/`), `live/synthqa/`, `live/material/`,
  `live/fixtures/`, `live/ablation/`, `live/demo/` (judge kit).
- `live/tests/` — offline gate (1085 passed); `live/claims_registry.json`
  — published-numbers registry + recompute specs.

Milestone notes (`M*.md`, `F2_*.md`, `S*.md`, `V5_*.md`, `V6_LINK.md`)
are frozen per-experiment records; current figures live in
`live/claims_registry.json` and the README metrics table.
