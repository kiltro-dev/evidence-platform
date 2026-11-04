# Atlas Live — a real-time agent newsroom that checks video as it plays

Atlas Live transcribes video as it plays, extracts checkable claims as
they are said, investigates each one with parallel Muse agents, and
publishes verdict cards with re-verifiable proof. Model proposes,
deterministic code disposes: no valid evidence package means
`unverifiable`, never a guess.

Built for the **Meta Global AI Developer Hackathon 2026** (Indie).
Rules/deadlines: [docs/HACKATHON.md](docs/HACKATHON.md).

> Atlas Live is an analysis aid, not an arbiter of truth. It never
> invents sources, abstains when proof is missing, and reports the
> runs where the Muse-only baseline wins. Every figure below names
> its experiment and what it does NOT prove.

## Try it in 3 minutes ($0 replay, no key)

```bash
git clone https://github.com/kiltro-dev/evidence-platform && cd evidence-platform
uv venv -p 3.12 && uv pip install --python .venv/bin/python -r requirements.txt
PYTHONPATH=src:live .venv/bin/python -m uvicorn live.server.app:create_app --factory --host 127.0.0.1 --port 8891
```

Open [http://127.0.0.1:8891/?replay=run-es-native](http://127.0.0.1:8891/?replay=run-es-native):
a recorded 48-event native-Spanish run re-emitted through the newsroom
UI — subtitles, verdict cards, agent theater, timeline with click-seek.
Labeled as replay; $0, no network, no model calls. Other shipped runs
in the "Real-run replay" dropdown.

Judge kit: [`live/demo/README_DEMO.md`](live/demo/README_DEMO.md) ·
submission text: [`live/demo/SUBMISSION.md`](live/demo/SUBMISSION.md) ·
recording guide: [`live/demo/RECORDING_GUIDE.md`](live/demo/RECORDING_GUIDE.md).

## Modes (every label the UI can show)

| Mode (UI badge) | What it is | Proven by |
|---|---|---|
| Recorded replay | Re-emitted event log, labeled REPLAY | 4 shipped logs in `live/demo/replay/` |
| Directo simulado | `ffmpeg -re` paced replay of a local file over a fifo | M1e run + `/hls` simulive loop ([`live/S3_SIMULIVE.md`](live/S3_SIMULIVE.md)) |
| Vivo por enlace | Link ingest (mp4 or HLS window) paced in real time | Run `link-22abeaf5ee2e` (mux HLS, 30 s, done, $0.0015) |
| Turbo por enlace | Same ingest without the realtime governor | V5-G1 + `run.mode` pacing flag |
| Archivo en tiempo real | Local file ingested with realtime pacing | Same pipeline, `source=file` |

Vision on link media runs **offline** (said-vs-shown over the
materialized file, M3 4/4 incl. 40% said vs 0.97% shown) — not
inline-realtime. Claim detection publishes `claim.detected` p50/p95
1240/1314 ms after end-of-phrase (M1 bus measurement; detection only,
not verdict latency). Explicitly NOT implemented: browser
tab/microphone capture, DASH playlists, encrypted playlists, YouTube
(refused by policy). No other "live" claim is made.

## Metrics (each with its experiment)

<!-- METRICS:START -->
| Metric | Value | Experiment | Does NOT prove |
|---|---|---|---|
| Atlas recall, DEV (n=36) | 26/36 = 0.722, recall Wilson [0.560,0.842] | V5-1 guard | NOT real-world accuracy: 36 scripted claims against available sources; b 0/6 by source access, not reasoning |
| Atlas recall, sealed retention (n=18) | 14/18 = 0.778, recall Wilson [0.548,0.910] | V6 validation (ret ran 3x, seal kept) | NOT generalization to arbitrary claims; n=18, Wilson recall interval [0.548,0.910] |
| Decided accuracy (decided-accuracy Wilsons) | dev 26/26 [0.871,1.000], ret 14/14 [0.785,1.000], 0 false verdicts either set | V5-1 + V6 | says nothing about the 14 abstentions (10 dev + 4 ret); high precision WITH low coverage is the design trade |
| Verdict latency / cost | p50 ~5 s / p95 ~40 s / ~$0.007 per claim | V5-1 + V6 runs | NOT end-to-end video latency (excludes ASR+detection); NOT worst-case (deadline 90s caps the tail by aborting) |
| Baseline recall, retention (Atlas loses) | 18/18 = 1.000 [0.824,1.000] | F2 retention baseline | verdicts only: paired with fidelity 9/63, which is the wedge |
| Baseline citation fidelity, DEV pooled (fidelity Wilson) | 9/63 = 0.143 [0.077,0.250] (r1 5/31, r2 4/32); NOT directly comparable to Atlas fidelity (different mechanisms: audit vs package-verified) | F2-1 audit | NOT Atlas fidelity (Atlas quotes are package-verified by construction, different mechanism); reasons: quote_not_found 34, unit_mismatch 13, numeric_mismatch 7 |
| Said-vs-shown planted | 4/4 | M3 | NOT inline-realtime vision on link runs (V6-2: offline path on materialized files) |
| Canaries | 7/7 decided of 12 (5 abstained), accuracy 1.000 [0.646,1.000] | M6 live run | NOT a false-rate guarantee: Wilson [0.646,1.000] is wide at n=12; abstention 5/12 |
| Live offline tests | 1073 passed, 9 skipped (1082 collected) | gate 2026-11-02 | count only; live-network tests (ATLAS_LIVE_NET) excluded from the gate |
| v1 core offline tests (historical) | 755 passed | gate 2026-11-02 | v1 static checker, now the numeric library; Atlas Live behavior is covered by tests_live |
<!-- METRICS:END -->

Registry + recompute: [`live/claims_registry.json`](live/claims_registry.json)
(commit `ae7f8f4` numbers frozen 2026-11-03 12:00 Santiago, re-rendered by
`scripts/render_metrics.py`, guarded by `scripts/check_claims.py` in the
offline gate). Benchmark preregistration:
[`live/eval2/prereg.md`](live/eval2/prereg.md).

Scope honesty: the 12-piece demo corpus (keynotes, panels, science
classes, fictional earnings calls, EN+ES) is 100% synthetic — scripted,
TTS-voiced and code-rendered by us, labeled "Synthetic demo" on screen
([`live/S4_SYNTHQA.md`](live/S4_SYNTHQA.md)). Corpus metrics
(e.g. S4 114/117 = 0.974 decided accuracy) measure system-vs-known-truth
on that corpus — never confuse them with benchmark-v2 recall above.

## Limits (short; full list in SUBMISSION § Honest limits)

- Recall 26/36 DEV / 14/18 retention with 0 false verdicts both sets;
  the Muse-only baseline wins recall (70/72 DEV, 18/18 retention) but
  only 9/63 of its citations are faithful (verbatim quote + figure ±2%
  on the cited URL) and it exports zero proof.
- Category b 0/42 and FOMC 5/5 unverifiable: official APIs need keys
  nobody has added yet ([`live/KEYS_NEEDED.md`](live/KEYS_NEEDED.md));
  re-run planned if keys arrive before submission.
- 3 corpus false-contradicts FIXED by the V5-1 guard; 1 open
  retrieval-variance case (P1-17-en). The skeptic red-team pass is
  advisory-only (deferred by default), not blocking.
- Canaries: 12 canaries run (M6), 7 decided 7/7, Wilson [0.646,1.000]
  — wide at n=12, and 5 abstentions all on the FALSE side.
- Contributor tier: live runs send video/audio + claim text to Meta's
  Model API; don't stream sensitive content. Browser microphone/tab
  capture does not exist (server-side audio/file/link input only).
  Clip 2 is synthetic.

## Install & run (live path)

```bash
git clone https://github.com/kiltro-dev/evidence-platform
cd evidence-platform
uv venv -p 3.12
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env   # add MODEL_API_KEY for live (non-replay) mode
chmod 600 .env
PYTHONPATH=src:live .venv/bin/python -m uvicorn live.server.app:create_app --factory --host 127.0.0.1 --port 8891
```

`pip` alternative: `python3 -m venv .venv && .venv/bin/pip install -r
requirements.txt` (same commands after). Live link ingest:

```bash
curl localhost:8891/ingest -H 'Content-Type: application/json' \
  -d '{"url":"https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8","mode":"vivo","duration":30}'
```

Poll `GET /ingest/{id}`, replay it (`?replay=link-{id}`), export it
(`GET /report/link-{id}?format=html`). Localhost only, no auth — do
not expose it; see [docs/SECURITY_OPS.md](docs/SECURITY_OPS.md). Env
vars: `MODEL_API_KEY` (live mode), `OPENALEX_API_KEY` (own quota),
`ATLAS_DB`, key-gated sources in `live/KEYS_NEEDED.md`.

## Stack

Python 3.12 (3.11+ supported), one process: FastAPI + static
single-page UI, httpx connectors, SQLite, pydantic. Model surface is
only the Meta Model API: Chat Completions (`muse-spark-1.3`, strict
`json_schema`) for structuring/judgment, `muse-voice-transcribe-1.0`
for streaming ASR, vision for slide reads, Responses `web_search` for
counter-evidence. Thirteen direct dependencies (eleven runtime incl.
imageio-ffmpeg≥0.7, PyAV, websockets, matplotlib, mcp + pytest and
hypothesis) — see [requirements.txt](requirements.txt) (pinned:
[requirements.lock](requirements.lock)).

Architecture: [docs/ARCHITECTURE_LIVE.md](docs/ARCHITECTURE_LIVE.md) ·
API: [docs/API.md](docs/API.md) (v1 core) · Decisions:
[docs/adr/](docs/adr/) · Model/code split: Atlas
[docs/AGENTS_LIVE.md](docs/AGENTS_LIVE.md), v1 core
[docs/AGENTS.md](docs/AGENTS.md) (Histórico).

## Tests & evaluation (offline gate)

```bash
PYTHONPATH=src:live .venv/bin/python -m pytest live/tests -q   # Atlas Live: 1073 passed
PYTHONPATH=src .venv/bin/python -m pytest tests/unit tests/e2e -q  # v1 core: 755 passed
PYTHONPATH=src:live .venv/bin/python scripts/check_claims.py   # numbers guard
```

Live-network tests (`ATLAS_LIVE_NET=1`) and the retention re-run
(`live/eval2/run.py --claims live/eval2/retention.json --arms atlas`,
~$0.10) are opt-in and never part of the gate.

## Origen del trabajo (hackathon window Oct 26 – Nov 4)

All commits in this repo fall inside the window: first commit
`757d0e8` (2026-10-31, Evidence Atlas MVP), Atlas Live pivot
`b5c0841` (2026-11-01), ShipCheck v0.4.0 `4e13d7d` (2026-11-01) —
verified via `git log --reverse`. Nothing predates 2026-10-26. Work
after the Nov 3 12:00 Santiago freeze is docs/consistency only.

## Histórico (v0.4.0): ShipCheck static checker

> **Histórico (v0.4.0).** The static checker below is preserved as
> Atlas Live's deterministic numeric library (`src/atlas/`). It still
> runs standalone (paste text → receipts) but is no longer the
> product. Docs kept in place, unmoved.

```bash
PYTHONPATH=src .venv/bin/python -m uvicorn atlas.api.app:app --host 127.0.0.1 --port 8765
```

API: `GET /api/status`, `POST /api/analyze`, `POST /api/check`,
`GET /api/reports`, `DELETE /api/reports/{id}`; UI at `/`. Full
reference: [docs/API.md](docs/API.md). Eval: 43/43 full-mode with
real `muse-spark-1.3`, 11/11 degraded, 36/36 paraphrase
([docs/EVAL_REPORT.md](docs/EVAL_REPORT.md)); browser gate
(`tests/e2e-browser/`, 6 Playwright specs); smoke
(`bash scripts/smoke.sh --port 8765`, 32 checks). v1 limitations
(tolerance prior, unit parser, no auth/TTL) live in
[docs/API.md](docs/API.md) and [docs/EVAL_REPORT.md](docs/EVAL_REPORT.md).
Direction rationale:
[docs/BREAKTHROUGH_DECISION.md](docs/BREAKTHROUGH_DECISION.md) ·
progress: [PROGRESS.md](PROGRESS.md).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: live` / `atlas` | `PYTHONPATH` not set | Prefix with `PYTHONPATH=src:live` (Atlas) or `PYTHONPATH=src` (v1) |
| `address already in use` on 8891 | Port taken | Use `--port 8892` and open that port |
| Replay dropdown empty | Old server | Restart (shipped logs load at startup) |
| Live ingest error `MODEL_API_KEY not configured` | Key missing | Fill `.env`, `chmod 600 .env`, restart |
| Live tests fail on network | No internet | Run the offline gate only (no network needed) |

## License

MIT — see [LICENSE](LICENSE).
