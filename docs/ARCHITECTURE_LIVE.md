# Atlas Live architecture (brief)

Atlas Live (`live/`) is the product: a real-time agent newsroom. The
v1 core (`src/atlas/`, ShipCheck v0.4.0, HISTORICAL) survives as its
deterministic numeric library (units, comparisons, verdict math) —
Atlas imports it, never the reverse.

```mermaid
flowchart LR
    SRC["video file / HLS link / simulive loop"]
    ING["ingest: resolve+SSRF, HLS fetch,\nTS remux, ffmpeg -re pacing"]
    DMX["demux: PCM 24kHz + scene frames"]
    ASR["Meta realtime ASR\nmuse-voice-transcribe"]
    DET["detector: claims\n(verbatim spans)"]
    BUS["EventBus\ntranscript / claim.*"]
    SWM["swarm: structurer,\nretriever, red-team\n(Muse Spark)"]
    PKG["evidence packages\n+ hashed snapshots"]
    VER["deterministic verifier\n(code decides)"]
    VIS["vista: said-vs-shown"]
    CTX["longctx: self-contradictions"]
    AUD["auditor: canaries"]
    UI["newsroom UI + WS\n+ proof bundle ZIP"]
    CORE["v1 core src/atlas\n(numeric library)"]

    SRC --> ING --> DMX --> ASR --> DET --> BUS
    BUS --> SWM --> PKG --> VER --> BUS
    DMX --> VIS --> BUS
    BUS --> CTX --> BUS
    BUS --> AUD
    BUS --> UI
    VER -.->|imports| CORE
```

Key contracts:

- **Model proposes, code disposes** ([AGENTS.md](AGENTS.md)): verdicts
  come only from the deterministic verifier over valid evidence
  packages (quote-must-exist, number re-extraction, 2% tolerance,
  scale guard, temporal alignment, primary-beats-secondary).
- **Bus log is the run**: `data/runs/*.jsonl` (+ `.summary.json`)
  replays verbatim (`GET /replay`, `WS /ws?replay=`); the UI never
  sees anything the log doesn't contain.
- **Proof is offline**: every verdict ships hashed snapshots;
  `live/bundle/verify_bundle.py` re-verifies without network or key.
- **Link ingest never feeds URLs to ffmpeg**: SSRF-guarded Python
  fetch → local materialize → (TS→MKV remux) → existing pipeline.
- **Vision is offline-path**: scene frames → Muse vision reads →
  code xcheck vs speech numbers (±15 s window).

Module map: `live/ingest/` (resolve, turbo, hlsfetch, tsdemux,
demux, simstream), `live/oido/` (ASR), `live/detector/`,
`live/orchestrator/` (bus, states), `live/swarm/`, `live/evidence/`,
`live/verifier/`, `live/vista/`, `live/longctx/`, `live/skeptic/`
(advisory), `live/auditor/`, `live/editor/` (cards), `live/replay/`,
`live/report/`, `live/bundle/`, `live/server/` + `live/ui/`,
`live/eval2/` (benchmark v2), `live/synthqa/`, `live/material/`.
