# Evidence Atlas — ShipCheck sidecar

Paste AI-generated text, get a verdict per claim: Evidence Atlas splits
it into atomic claims, structures each one, retrieves real evidence,
runs deterministic checks, tries to disprove its own draft against the
live web, and shows exactly what is evidence, what is interpretation,
and what remains uncertain.

Built for the **Meta Global AI Developer Hackathon 2026** (Indie track).
See [docs/HACKATHON.md](docs/HACKATHON.md) for the verified rules and deadlines.

> Evidence Atlas is an analysis aid, not an arbiter of truth. It never
> invents sources, and every conclusion links back to the evidence behind it.

## Problem

AI apps ship statistics nobody checks — and LLM-judge guardrails can
hallucinate about hallucinations. You get "true or false" without the
work: which sources were consulted, what each source actually says, how
numbers were compared, or what the model guessed versus what code
verified.

## What this does

Given pasted text (e.g. an AI answer), ShipCheck (`POST /api/check`):

0. **Splits** it into atomic checkable claims (model-first under a
   strict schema, verbatim-spans-only, heuristic fallback; max 8).
   Every coverage cut (8-cap, 6000-char slice, short drops) is
   disclosed on the receipt.
1. **Structures** each claim (domain, quantities, units, time,
   geography, conditions) with Muse Spark under strict decoding
   schemas, falling back to deterministic heuristics offline.
2. **Retrieves** evidence from real, keyless public sources — World Bank
   Open Data (economy) and OpenAlex (science, incl. abstracts) — in parallel.
3. **Verifies** with deterministic code: unit normalization, same-period
   gating, numeric comparison under an explicit 2% tolerance rule, plus a
   claimed-precision rule (stated digits vs. retrieved value), absolute-gap
   disclosure, and methodological uncertainties.
4. **Red-teams itself**: Muse Spark attacks the draft verdict through a
   falsify-only schema, then hunts LIVE counter-evidence via Responses
   API `web_search`; code validates every attack and citation and
   surfaces them as doubt (advisory only — stances stay code-decided),
   ordering official sources first and listing retrieved-but-uncited
   results as attributed doubt. Science abstracts get a bounded
   stance assessment that corroborates but never verifies.
5. **Reports** per claim: stance (one of five, including
   `not_assessable` for opinions and non-claims), evidence for/against,
   uncertainties, limitations, a full audit trail where every step is
   badged `CODE`, `MODEL`, or `HEURISTIC`, and per-stage token usage —
   plus a batch roll-up. Batch sub-claims run in parallel (input
   order kept). Reports export as methods receipts and re-run
   deterministically (cache-bypassing re-run, hash + verdict shown).

No confidence scores, no invented citations. When evidence is missing or
incomparable, the report says so. Snippets and excerpts are pointers to
sources, never Evidence on their own — see [docs/AGENTS.md](docs/AGENTS.md).

## Demo (3 minutes)

```bash
git clone https://github.com/kiltro-dev/evidence-platform && cd evidence-platform
uv venv -p 3.12 && uv pip install --python .venv/bin/python -r requirements.txt
PYTHONPATH=src .venv/bin/python -m uvicorn atlas.api.app:app --host 127.0.0.1 --port 8765
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765), switch to **Check AI
output**, and paste:

> The GDP of the United States was $27.36 trillion in 2023. The
> population of Japan was 125 million in 2023. Unemployment in Germany
> was 9.8% in 2023.

You get three receipts: precision-capped `partially_supported` (Δ1.62%
but the stated digits exceed what the retrieved value confirms),
`supported`, and `contradicted` (Δ219% — the hallucination, caught) —
with live-web counter-evidence attached as doubt (recorded run:
`eval/results/demo_batch_20261101T040906Z.json`). Single-claim mode
(`POST /api/analyze`) still works: try `The population of Japan was 500
million in 2020.` → contradicted, or `Democracy is the best form of
government.` → not assessable (opinion).

Full script: [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).

## Stack

Python 3.12 monolith (3.11+ supported), one process: FastAPI + static
single-page UI, httpx connectors, SQLite persistence, pydantic models.
The only model dependency is the Meta Model API: Chat Completions
(`muse-spark-1.3`, strict `json_schema` decoding) for structuring and
judgment calls, Responses API + `web_search` for live counter-evidence.
Seven dependencies total — see [requirements.txt](requirements.txt).

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
.venv/bin/python -m pytest tests/unit tests/e2e -q        # offline gate (486 tests)
.venv/bin/python -m pytest tests/integration -q -m live   # live sources (needs network)
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --mode degraded  # 11 scripted cases, offline
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --mode full      # all 43 cases, needs network + key
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --paraphrase     # 36-input stability probe, offline
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --unanswerable   # 20-variant abstention probe, offline
bash scripts/smoke.sh --port 8765                           # offline smoke (31 checks)
bash scripts/smoke.sh --port 8765 --live --eval              # + live API + full eval
```

- `tests/unit tests/e2e` is the offline gate: **486 tests, all green**,
  no network, no key.
- `-m live` selects the `live` pytest marker (registered in
  `pyproject.toml`): tests that hit real public APIs.
- Eval: 43 cases total. `--mode degraded` runs the 11 offline-capable
  cases and skips the 32 `live` ones; `--mode full` runs everything but
  needs network and takes many minutes (live source latency plus up to
  5 model calls per case, incl. the token-hungry web pass).
- Current results: **486/486 offline, 11/11 degraded eval, 36/36
  paraphrase agreement, full-mode eval with real muse-spark-1.3** —
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
