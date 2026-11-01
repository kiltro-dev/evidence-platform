# Atlas Live — a real-time agent newsroom that checks video as it plays

> **Provenance (hackathon compliance):** all work in this repo was done
> 2026-10-31 → 2026-11-04 (first commit `757d0e8`, 2026-10-31 — inside
> the Oct 26–Nov 4 window). The static claim-checker (`src/atlas/`,
> tag `pre-pivot-v1`) is now the deterministic numeric library of
> **Atlas Live**, the real-time agent newsroom in [`live/`](live/).
> New demo work lands only in `live/` with dated commits.

Play a video and watch agents check it live: Atlas Live transcribes
speech in real time, extracts checkable claims as they are said,
investigates each one with parallel Muse agents over the open web,
verifies with deterministic code, and publishes verdict cards with
re-verifiable proof over WebSocket — while the video keeps running.
It also watches the slides: when the speaker says 40% and the chart
shows 0.97%, that mismatch is caught on screen
([`live/M3.md`](live/M3.md)).

Built for the **Meta Global AI Developer Hackathon 2026** (Indie track).
See [docs/HACKATHON.md](docs/HACKATHON.md) for the verified rules and deadlines.
Judge path (3 min, $0 replay): [`live/demo/README_DEMO.md`](live/demo/README_DEMO.md) ·
Submission text: [`live/demo/SUBMISSION.md`](live/demo/SUBMISSION.md).

> Atlas Live is an analysis aid, not an arbiter of truth. It never
> invents sources, every verdict links to hashed snapshots anyone can
> re-verify offline, and when proof is missing it says "unverifiable"
> instead of guessing. Honest numbers below — including the ones where
> the Muse-only baseline wins ([`live/M8.md`](live/M8.md)).

## Problem

AI apps ship statistics nobody checks — and LLM-judge guardrails can
hallucinate about hallucinations. You get "true or false" without the
work: which sources were consulted, what each source actually says, how
numbers were compared, or what the model guessed versus what code
verified. Video makes it worse: claims fly by at speaking pace, and
charts on screen can contradict the speaker with nobody noticing.

## What this does

Given a video or stream, Atlas Live ([`live/`](live/)):

0. **Listens** with `muse-voice-transcribe` over streaming WebSocket
   (diarization, endpointing, ES/EN) and **watches** slides, tables and
   charts — each visual claim anchored to its frame and second.
1. **Detects** checkable claims live (model-first under a strict
   schema, verbatim spans, coreference resolved, deduped), publishing
   `claim.detected` ~1.2 s (p50/p95 1240/1314 ms) after end-of-phrase.
2. **Investigates** with parallel Muse agents (structurer, retriever,
   red-team) over the open web: responsible fetch (robots.txt,
   user-agent, rate limits, no paywalls), hashed local snapshots, and
   literal quotes with offsets — [evidence packages](live/M2_E2E.md).
3. **Verifies** with deterministic code only: quote-must-exist,
   number re-extraction, 2% tolerance, scale-error guard, temporal
   alignment, primary-beats-secondary. No valid package → `unverifiable`.
   Models never vote on verdicts.
4. **Cross-checks said-vs-shown**: speech numbers vs on-screen charts
   (±15 s window), truncated-axis and broken-scale flags.
5. **Publishes** verdict cards (ES/EN) over WebSocket as the video runs,
   with live corrections, a canary-measured reliability panel (no
   invented confidence), and an exportable proof bundle
   (`verify_bundle.py` re-verifies offline).

No confidence scores, no invented citations. When evidence is missing or
incomparable, the card says so. Snippets are pointers, never evidence —
see [docs/AGENTS.md](docs/AGENTS.md).

Measured on a synthetic keynote (41 planted claims × EN/ES, ground
truth in [`live/material/truth.json`](live/material/truth.json)):
8/31 decided correctly with 0 false-contradicted
([`live/M2D.md`](live/M2D.md)); said-vs-shown 4/4 planted cases
([`live/M3.md`](live/M3.md)); canaries 7/7, Wilson 95% [0.65, 1.0]
([`live/M6.md`](live/M6.md)). The Muse-only baseline scores higher on
this set (30–31/31, [`live/M8.md`](live/M8.md)) — without snapshots,
literal-quote checks, or re-verifiable proof; Atlas abstains (71%)
rather than assert without evidence. Latency p50 ~78 s/claim
investigated (parallel ×4) is the known bottleneck.

The v1 static checker (paste text → receipts) still works under
`src/atlas/` + `POST /api/check` (tag `pre-pivot-v1`, 755 offline
tests, 43/43 eval) and now serves as Atlas Live's numeric library.

## Demo (3 minutes, $0 replay)

```bash
git clone https://github.com/kiltro-dev/evidence-platform && cd evidence-platform
uv venv -p 3.12 && uv pip install --python .venv/bin/python -r requirements.txt
PYTHONPATH=src:live .venv/bin/python -m uvicorn live.server.app:create_app --factory --host 127.0.0.1 --port 8891
```

Open [http://127.0.0.1:8891](http://127.0.0.1:8891), pick a recorded run
and press **Watch replay**: live subtitles, verdict cards appearing as
the video runs, the agent theater, and the timeline — a replay of a
real run, labeled as such. Live mode needs `MODEL_API_KEY` (see
[`.env.example`](.env.example)).

Full judge path: [`live/demo/README_DEMO.md`](live/demo/README_DEMO.md) ·
3-min video script: [`live/demo/script.md`](live/demo/script.md).

> Demo notice: live video/audio is sent to Meta's Model API (Standard no-training tier); don't stream sensitive content.

## Stack

Python 3.12 monolith (3.11+ supported), one process: FastAPI + static
single-page UI, httpx connectors, SQLite persistence, pydantic models.
The only model dependency is the Meta Model API: Chat Completions
(`muse-spark-1.3`, strict `json_schema` decoding) for structuring and
judgment calls, Responses API + `web_search` for live counter-evidence.
Eleven dependencies total (nine runtime incl. imageio-ffmpeg,
websockets and matplotlib for Atlas Live + pytest and hypothesis) —
see [requirements.txt](requirements.txt).

Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
API: [docs/API.md](docs/API.md) ·
Decisions: [docs/adr/](docs/adr/) ·
Model/code split: [docs/AGENTS.md](docs/AGENTS.md) ·
Ops: [docs/SECURITY_OPS.md](docs/SECURITY_OPS.md)

## Requirements

- Python 3.11+ (3.12 verified), `uv` (or `pip` — see below)
- Internet access for live sources (World Bank, OpenAlex)
- Optional: `MODEL_API_KEY` for full model-assisted mode.
  Without it the system runs in degraded deterministic mode
  (heuristic extraction + template summaries, same verification core).
  Check the current mode any time with `GET /api/status`.

## Install & run

Single supported path (`uv`):

```bash
git clone https://github.com/kiltro-dev/evidence-platform
cd evidence-platform
uv venv -p 3.12
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env   # optional; add MODEL_API_KEY for full mode
chmod 600 .env         # the key file must not be group/world-readable
PYTHONPATH=src .venv/bin/python -m uvicorn atlas.api.app:app --host 127.0.0.1 --port 8765
```

`pip` alternative (no `uv`):

```bash
git clone https://github.com/kiltro-dev/evidence-platform
cd evidence-platform
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m uvicorn atlas.api.app:app --host 127.0.0.1 --port 8765
```

Notes:

- `PYTHONPATH=src` is required: the importable package lives in `src/atlas`
  and is not pip-installed. Every Python invocation below includes it.
- The server binds **localhost only** (`127.0.0.1`) by default. There is no
  authentication; do not expose it to a network. See
  [docs/SECURITY_OPS.md](docs/SECURITY_OPS.md).
- If port 8765 is taken, pick another: `--port 8770`, then use
  `http://127.0.0.1:8770`. The smoke script auto-resolves a busy port.
- Reports persist to `data/atlas.db` by default; override with `ATLAS_DB`.
  The directory is created on first write. Reads never create it.
- After adding or changing `MODEL_API_KEY` in `.env`, **restart the server**:
  the key is read once at startup and a running server keeps its old mode.

API: `GET /api/status`, `POST /api/analyze`, `POST /api/check`,
`GET /api/reports`, `GET /api/reports/{id}`, `DELETE /api/reports/{id}`.
UI at `/`. Full reference: [docs/API.md](docs/API.md).

## Tests & evaluation

```bash
.venv/bin/python -m pytest tests/unit tests/e2e -q        # offline gate (755 tests)
.venv/bin/python -m pytest tests/integration -q -m live   # live sources (needs network)
cd tests/e2e-browser && npm ci && npm run install-browser-shell && npm test  # 6 browser specs, degraded
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --mode degraded  # 11 scripted cases, offline
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --mode full      # all 43 cases, needs network + key
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --paraphrase     # 36-input stability probe, offline
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --unanswerable   # 20-variant abstention probe, offline
bash scripts/smoke.sh --port 8765                           # offline smoke (32 checks)
bash scripts/smoke.sh --port 8765 --live --eval              # + live API + full eval
```

- `tests/unit tests/e2e` is the offline gate: **755 tests, all green**,
  no network, no key (pytest 9). No `PYTHONPATH` needed: the test
  modules self-insert `src/` on `sys.path`. Direct scripts
  (`scripts/run_eval.py`, `repro_findings.py`, `uvicorn`) DO need
  `PYTHONPATH=src`.
- `tests/e2e-browser` is the browser gate: 6 Playwright specs drive the
  real UI in degraded mode (a11y contract, abort paths, journeys,
  render fidelity, UI units, judge mode) — see
  [tests/e2e-browser/README.md](tests/e2e-browser/README.md).
- `-m live` selects the `live` pytest marker (registered in
  `pyproject.toml`): tests that hit real public APIs.
- Eval: 43 cases total. `--mode degraded` runs the 11 offline-capable
  cases and skips the 32 `live` ones; `--mode full` runs everything but
  needs network and takes many minutes (live source latency plus up to
  5 model calls per case, incl. the token-hungry web pass).
- Current results: **755/755 offline, 43/43 full-mode eval with real
  muse-spark-1.3, 11/11 degraded eval, 36/36 paraphrase agreement** —
  see [docs/EVAL_REPORT.md](docs/EVAL_REPORT.md) for the pinned run.
  What these numbers do and do not prove is stated there honestly:
  pinned regressions green, not an accuracy benchmark.
  Methodology: [docs/EVAL.md](docs/EVAL.md).
  Project's evaluation proposal: [docs/FRAMEWORK.md](docs/FRAMEWORK.md).

## Limitations

- Economy verification covers the World Bank indicator/country catalog coded
  in [src/atlas/connectors/](src/atlas/connectors/) (49 indicator
  keywords incl. Spanish aliases, 51 countries/aggregates).
- Science evidence is OpenAlex metadata plus abstract-stance judgments;
  full texts are not read and abstracts never verify a claim.
- Bare numbers are compared as counts only, with an explicit flag.
- Tolerance default is 2% relative (an explicit engineering prior, not a
  validated constant); methodology differences are the user's call. The
  claimed-precision rule additionally requires stated digits to survive
  contact with the retrieved value. Absolute-gap guides ($100B, 1M,
  0.5pp) are order-of-magnitude disclosure thresholds with the same
  honesty status.
- Heuristic scale abbreviations are limited (e.g. bare "T" for trillion
  is not parsed); the model path handles the general case.
- No authentication or multi-user support (single-user demo; localhost-only).
- Stored reports never expire (no TTL); delete unneeded ones via the UI or
  `DELETE /api/reports/{id}`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: atlas` | `PYTHONPATH` not set | Prefix Python/uvicorn/pytest with `PYTHONPATH=src` |
| `/api/status` shows degraded after adding a key | Server reads `.env` once at startup | Restart the server |
| `address already in use` on 8765 | Port taken | Use `--port 8770` (server) or let `smoke.sh` resolve one |
| `uvicorn: command not found` | `.venv` not on PATH | Use `.venv/bin/python -m uvicorn ...` |
| Live tests / full eval fail on network | No internet or source outage | Run the offline gate + `--mode degraded`; both need no network |
| `MODEL_API_KEY` errors in full mode | Key missing or invalid | Fill `.env` from `.env.example`, `chmod 600 .env`, restart |

## Project status

v4 (0.4.0) ShipCheck sidecar: multi-claim batch intake, strict decoding
schemas, live-web adversarial falsification, EVS hardening
(pin-conformance + Wilson CIs, independent recompute, partial-coverage
caveat). Night-shift hardening on top: parallel batch analysis,
authoritative-first web ordering, World Bank vintage/mrnev/coverage
upgrades, multi-geo disclosure, 43-case eval. Prior v3 (0.3.0):
adversarial self-review, claimed-precision
methodology, abstract-stance science path, per-stage token accounting,
receipt export + deterministic re-run. Direction rationale:
[docs/BREAKTHROUGH_DECISION.md](docs/BREAKTHROUGH_DECISION.md).
Progress log: [PROGRESS.md](PROGRESS.md).
Study/defense guide: [docs/STUDY_GUIDE.md](docs/STUDY_GUIDE.md).
Developer guide: [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md).

## License

MIT — see [LICENSE](LICENSE).
