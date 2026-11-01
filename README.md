# Evidence Atlas

Traceable claim analysis: structure a claim, retrieve real evidence, run
deterministic checks, and show exactly what is evidence, what is
interpretation, and what remains uncertain.

Built for the **Meta Global AI Developer Hackathon 2026** (Indie track).
See [docs/HACKATHON.md](docs/HACKATHON.md) for the verified rules and deadlines.

> Evidence Atlas is an analysis aid, not an arbiter of truth. It never
> invents sources, and every conclusion links back to the evidence behind it.

## Problem

Fact-checking chatbots answer "true or false" without showing their work:
which sources were consulted, what each source actually says, how numbers
were compared, or what the model guessed versus what code verified.

## What this does

Given a factual claim, Evidence Atlas:

1. **Structures** it (domain, quantities, units, time, geography, conditions)
   with Muse Spark, falling back to deterministic heuristics offline.
2. **Retrieves** evidence from real, keyless public sources:
   World Bank Open Data (economy) and OpenAlex (science).
3. **Verifies** with deterministic code: unit normalization, same-period
   gating, numeric comparison with an explicit 2% tolerance rule.
4. **Reports** stance (one of five, including `not_assessable` for opinions
   and non-claims), evidence for/against, uncertainties, limitations, and a
   full audit trail where every step is badged `CODE`, `MODEL`, or `HEURISTIC`.

No confidence scores, no invented citations. When evidence is missing or
incomparable, the report says so. Snippets and excerpts are pointers to
sources, never Evidence on their own — see [docs/AGENTS.md](docs/AGENTS.md).

## Demo (3 minutes)

```bash
git clone https://github.com/kiltro-dev/evidence-platform && cd evidence-platform
uv venv -p 3.12 && uv pip install --python .venv/bin/python -r requirements.txt
PYTHONPATH=src .venv/bin/python -m uvicorn atlas.api.app:app --host 127.0.0.1 --port 8765
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) and try:

- `The GDP of the United States was $27.36 trillion in 2023.` → supported
- `The population of Japan was 500 million in 2020.` → contradicted
- `Democracy is the best form of government.` → not assessable (opinion)

Full script: [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).
Screenshots: none — no browser is available in this build environment,
so no screenshots were captured.

## Stack

Python 3.12 monolith (3.11+ supported), one process: FastAPI + static
single-page UI, httpx connectors, SQLite persistence, pydantic models.
The only model dependency is the Meta Model API (OpenAI-compatible SDK,
`muse-spark-1.3`). Seven dependencies total — see
[requirements.txt](requirements.txt).

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

API: `GET /api/status`, `POST /api/analyze`, `GET /api/reports`,
`GET /api/reports/{id}`, `DELETE /api/reports/{id}`. UI at `/`.
Full reference: [docs/API.md](docs/API.md).

## Tests & evaluation

```bash
.venv/bin/python -m pytest tests/unit tests/e2e -q        # offline gate (185 tests)
.venv/bin/python -m pytest tests/integration -q -m live   # live sources (needs network)
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --mode degraded  # 7 scripted cases, offline
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --mode full      # all 24 cases, needs network + key
bash scripts/smoke.sh --port 8765                           # offline smoke (29 checks)
bash scripts/smoke.sh --port 8765 --live --eval              # + live API + full eval
```

- `tests/unit tests/e2e` is the offline gate: **185 tests, all green**,
  no network, no key.
- `-m live` selects the `live` pytest marker (registered in
  `pyproject.toml`): tests that hit real public APIs.
- Eval: 24 cases total. `--mode degraded` runs the 7 offline-capable cases
  (2 `both` + 5 `scripted`) and skips the 17 `live` ones; `--mode full`
  runs everything but needs network and takes several minutes
  (live source latency plus up to 2 model calls per case).
- Current results: **185/185 offline, 7/7 degraded eval, 24/24 full-mode
  eval with real muse-spark-1.3** (stance accuracy 1.00; includes EN/ES,
  adversarial, injection, and provider-failure cases).
  Methodology: [docs/EVAL.md](docs/EVAL.md).
  Latest run detail: [docs/EVAL_REPORT.md](docs/EVAL_REPORT.md).
  Project's evaluation proposal: [docs/FRAMEWORK.md](docs/FRAMEWORK.md).

## Limitations

- Economy verification covers the World Bank indicator/country catalog coded
  in [src/atlas/connectors/](src/atlas/connectors/) (~16 indicator keywords
  plus Spanish aliases, ~50 countries).
- Science evidence is bibliographic metadata; full texts are not read.
- Bare numbers are compared as counts only, with an explicit flag.
- Tolerance default is 2% relative; methodology differences are the user's call.
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

v2 (0.2.0) complete and verified end-to-end. Progress log: [PROGRESS.md](PROGRESS.md).
Study/defense guide: [docs/STUDY_GUIDE.md](docs/STUDY_GUIDE.md).
Developer guide: [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md).

## License

MIT — see [LICENSE](LICENSE).
