# V6-1/V6-2 — live-link completion note

## V6-1: https media without feeding URLs to ffmpeg (priority fix)

Reproduced: imageio static ffmpeg 7.0.2 segfaults on ANY https input
(rc=-11, TLS init) AND on ANY MPEG-TS input incl. its own remux output
(rc=139, mpegts demuxer). ffmpeg 4.2.2 (same builder) crashes the same
way; johnvansickle "release" is the same 7.0.2. No system ffmpeg here.

Fix (no ffmpeg build change, SSRF intact):

- `live/ingest/hlsfetch.py`: master/variant playlist fetch over the
  existing SSRF-guarded urllib stack (check per URL, redirects gated,
  robots + throttle), cheapest-usable variant with 404 fallback,
  init + segments concatenated locally. Encrypted/nested refused.
- `live/ingest/tsdemux.py`: PyAV (`av>=19,<20`, new dep) remuxes TS
  windows to MKV (stream copy); the static binary reads MKV fine.
- `materialize_media()`: mp4/audio stream to disk, hls = fetch+remux,
  dash still refused with a clear error.
- `probe_streams()` now raises loudly on rc=-11 instead of silently
  reporting "no streams".
- `live/tests/test_v6_hls.py`: 26 offline (parse, fallback, caps,
  8 SSRF attack URLs, dispatch) + live (`ATLAS_LIVE_NET=1`): NASA mp4
  resolve (4,493,927 B pinned), Archive mp4 resolve, VOD HLS window,
  full HLS→remux→static-demux E2E. NASA TV HLS is best-effort xfail:
  its master lists variants that 404 (verified 2026-11-02, Akamai
  rotation) — the fallback logic this exposed is covered offline.

Live result: 30 passed, 1 xfailed (90 s).

## V6-2: what the table asked, in order

- (c) Vivo-by-link HLS: DONE. Proof run `link-22abeaf5ee2e` (mux VOD,
  vivo, 30 s): status done, source=link-hls, label "Vivo por enlace",
  1 turn, 0 claims (test content has no numeric claims — honest),
  $0.0015. `POST /ingest` accepts `duration` (0,3600]s.
- Mode labels: `run_label()` — Vivo/Turbo por enlace, Directo
  simulado, Archivo en tiempo real, Micrófono en vivo — published in
  `run.mode` + summary `source`; UI badge shows it live and on replay.
- (f) Synced player for link runs: DONE. `link-*.jsonl` replayable,
  `/media/link-*` serves the materialized file (strict regex, no
  traversal), `/replay/link-*` pins its video, UI polls
  `/ingest/{id}` then offers Replay + Report.
- (g) Live + exported report: DONE. Newsroom cards unchanged (live);
  `GET /report/{run}?format=json|html` exports any replayable run
  (link runs badged `real`, corpus runs `synthetic`).
- (e) Realtime vision on link frames: PARTIAL. Said-vs-shown (M3,
  4/4 incl. the 40-vs-0.97 case) runs offline on any local video,
  link cache files included — not inline in the link pipeline.
- (d) Browser tab/mic capture: MISSING. No getUserMedia in the UI;
  server-side mic only (Pulse/ALSA, file-fed E2E proven). Declared,
  not faked.
- (a) HLS simulive, (b) Turbo-by-link: already worked (S3, V5-G1).
