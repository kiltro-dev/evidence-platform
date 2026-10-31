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
4. **Reports** stance, evidence for/against, uncertainties, limitations, and a
   full audit trail where every step is badged `CODE`, `MODEL`, or `HEURISTIC`.

No confidence scores, no invented citations. When evidence is missing or
incomparable, the report says so.

## Demo (3 minutes)

```bash
uv venv && uv pip install --python .venv/bin/python -r requirements.txt
PYTHONPATH=src .venv/bin/python -m uvicorn atlas.api.app:app --port 8765
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765) and try:

- `The GDP of the United States was $27.36 trillion in 2023.` → supported
- `The population of Japan was 500 million in 2020.` → contradicted
- `A 2021 study found that global average temperature rose sharply.` → partial context

Full script: [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).

## Stack

Python 3.12 monolith, one process: FastAPI + static single-page UI,
httpx connectors, SQLite persistence, pydantic models. The only model
dependency is the Meta Model API (OpenAI-compatible SDK, `muse-spark-1.3`).
Six dependencies total — see [requirements.txt](requirements.txt).

Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
Decisions: [docs/adr/](docs/adr/) · Model/code split: [docs/AGENTS.md](docs/AGENTS.md)

## Requirements

- Python 3.11+ (3.12 verified), `uv` or `pip`
- Internet access for live sources
- Optional: `MODEL_API_KEY` for full model-assisted mode.
  Without it the system runs in degraded deterministic mode
  (heuristic extraction + template summaries, same verification core).

## Install & run

```bash
git clone <this-repo> && cd evidence-platform
uv venv && uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env   # optional; add MODEL_API_KEY for full mode
PYTHONPATH=src .venv/bin/python -m uvicorn atlas.api.app:app --port 8765
```

API: `GET /api/status`, `POST /api/analyze`, `GET /api/reports`,
`GET /api/reports/{id}`. UI at `/`.

## Tests & evaluation

```bash
.venv/bin/python -m pytest tests/unit tests/e2e -q        # offline gate (28 tests)
.venv/bin/python -m pytest tests/integration -q -m live   # live sources (3 tests)
PYTHONPATH=src .venv/bin/python scripts/run_eval.py       # 10-case eval, live
```

Current results: **28/28 offline, 3/3 live, 10/10 eval cases**.
Methodology and honest metric definitions: [docs/EVAL.md](docs/EVAL.md).
Project's evaluation proposal: [docs/FRAMEWORK.md](docs/FRAMEWORK.md).

## Limitations

- Economy verification covers the World Bank indicator/country catalog coded
  in [src/atlas/connectors/](src/atlas/connectors/) (~16 indicators, ~50 countries).
- Science evidence is bibliographic metadata; full texts are not read.
- Bare numbers are compared as counts only, with an explicit flag.
- Tolerance default is 2% relative; methodology differences are the user's call.
- No authentication or multi-user support (single-user MVP).

## Project status

MVP complete and verified end-to-end. Progress log: [PROGRESS.md](PROGRESS.md).
Study/defense guide: [docs/STUDY_GUIDE.md](docs/STUDY_GUIDE.md).

## License

MIT — see [LICENSE](LICENSE).
