# Atlas Live — agent newsroom (real-time evidence engine)

Listens to and watches a video/stream, extracts checkable claims as they
are spoken, investigates them with parallel Muse agents over the open
web, verifies with deterministic code, and publishes verdicts with
re-verifiable proof over the video itself, over WebSocket, while the
video keeps running.

Status: scaffold (branch `live`). Milestones M1–M9 per mission ledger.

Layout (target):

- `live/ingest/` — ffmpeg demux: PCM 24 kHz mono → ASR, scene frames,
  short video windows. Simulated stream (`ffmpeg -re`) labeled as such.
- `live/oido/` — muse-voice-transcribe WebSocket client (streaming,
  diarization, endpointing, keywords, language_bias).
- `live/vista/` — on-screen text/tables/charts + said-vs-shown cross-check.
- `live/detector/` — claim worthiness, coreference, dedup.
- `live/orchestrator/` — claim queue, state machine, fan-out, budget,
  backpressure, per-claim deadlines, event bus.
- `live/researchers/` — parallel investigators → evidence packages
  (url, capture date, hash, local snapshot, literal quote + offsets,
  source type, extracted numbers).
- `live/library/` — persistent snapshot/claim store (FTS5 + embeddings
  if spike confirms, else BM25 + Muse re-rank).
- `live/verifier/` — deterministic package validation on top of the v1
  numeric core (`src/atlas/core/`).
- `live/skeptic/`, `live/editor/`, `live/auditor/`, `live/longctx/`.
- `live/server/` — asyncio app + WebSocket; `live/ui/` — newsroom page;
  `live/bundle/` — exportable proof package + `verify_bundle.py`.
- `live/tests/` — recorded-fixture tests; `live/fixtures/` — synthetic
  keynote material (own/TTS, rights-cleared).
