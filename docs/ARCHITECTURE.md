# Architecture

Evidence Atlas is a **Python monolith in one process**: FastAPI serves a
JSON API and a static single-page UI; a staged pipeline turns a claim into
a traceable report; SQLite persists reports; two HTTP connectors reach
public sources; one model client reaches the Meta Model API.

```
                    ┌──────────────┐
                    │  web/index   │  static UI (no build step)
                    └──────┬───────┘
                           │  GET /  +  /api/*
┌─────────┐   ┌────────────▼──────────────────────────────┐
│ World   │   │ FastAPI (src/atlas/api/app.py)            │
│ Bank    │◄──┤  POST /api/analyze → pipeline.run.analyze │
│ OpenAlex│◄──┤  GET  /api/reports[/{id}] ← storage.store │──► data/atlas.db
└─────────┘   └────────────┬──────────────────────────────┘
                           │ ① extract  ② route  ③ retrieve
                           │ ④ verify   ⑤ synthesize  ⑥ persist
              ┌────────────▼─────────────┐
              │ Meta Model API (optional)│  muse-spark-1.3 via OpenAI SDK
              │ structure + summarize    │  (never verifies numbers)
              └──────────────────────────┘
```

## Components

| Component | Path | Responsibility |
|---|---|---|
| Types | `core/types.py` | Pydantic domain model: claim, evidence, comparison, uncertainty, conclusion, audit, report |
| Units | `core/units.py` | Deterministic parsing/normalization of quantities, scales, currencies, temperatures |
| Compare | `core/compare.py` | Deterministic numeric + time comparison; strict comparability; 2% tolerance default |
| Connectors | `connectors/` | `SourceConnector` protocol + registry; `worldbank.py`, `openalex.py`, `countries.py` |
| Models | `models/` | `LLMClient` protocol; `meta.py` (Meta API), `scripted.py` (test double), `heuristic.py` (regex fallback) |
| Extract | `pipeline/extract.py` | Claim structuring via LLM with heuristic fallback; LLM gaps backfilled deterministically |
| Verify | `pipeline/verify.py` | `verify_quantitative` (economy) and `assess_science` (bibliographic); code-only |
| Synthesize | `pipeline/synthesize.py` | Deterministic stance rules; optional LLM summary rewrite |
| Run | `pipeline/run.py` | Orchestrator: 6 audited stages, connector error isolation, persistence |
| Store | `storage/store.py` | SQLite: save/get/list reports |
| API | `api/app.py` | FastAPI app, status/analyze/reports endpoints, static mount |

## Execution flow

`POST /api/analyze {text, limit}` → `analyze()`:

1. **extract** — `structure_claim`: LLM JSON (actor `model`) or heuristic
   (actor `heuristic`). Years/countries the LLM drops are backfilled from
   regex so explicit text is never silently lost.
2. **route** — `for_domain`: economy→worldbank, science→openalex,
   general→both. Connectors tagged `general` are fallbacks only.
3. **retrieve** — each connector returns `Evidence` with source kind
   (primary/secondary/reproduction) and evidence kind (direct/contextual).
   A failing connector appends a `connector_error` uncertainty; analysis
   continues.
4. **verify** — code-only. Economy: per-quantity same-unit matching,
   same-period gating, `compare_numbers`. Science: year-match checks plus a
   mandatory `metadata_only` uncertainty. Nothing here calls a model.
5. **synthesize** — `decide_stance` rules (see below); LLM summary rewrite
   attempted only if a client is available, else the deterministic template.
6. **persist** — report JSON saved to SQLite; report id returned.

Every stage appends an `AuditStep{step, actor, detail}`; the UI renders
`CODE` / `MODEL` / `HEURISTIC` badges from it.

## Stance rules (`synthesize.decide_stance`)

- Any **verifying** `contradicts` → `contradicted`.
- Any verifying `consistent` → `supported` (`partially_supported` if a
  blocking uncertainty exists).
- Only non-verifying consistency (`time_compatibility`,
  `publication_year_match`) → `partially_supported`.
- Otherwise → `unverifiable`.

Verifying metrics are numeric comparisons on same-unit, same-period data.
Bibliographic year matches can never yield `supported` by construction.

## Comparability contract (`core/compare.py`)

- Unknown unit on either side → `not_comparable`, no number emitted.
- Mismatched units → `not_comparable`, except temperature (converted).
- Non-overlapping periods → values are not compared; `period_mismatch`
  uncertainty recorded instead.
- Bare claim numbers match `count` evidence only, flagged with
  `unit_assumed_from_context` on both comparison and uncertainties.
- Year-like numbers (4 digits, 1000–2100) are time references, never quantities.
- A discrepancy is a measured difference with a methodology caveat,
  never a verdict of falsehood.

## Error handling

- Connector exceptions → `connector_error` uncertainty, pipeline continues.
- LLM transport/JSON errors → silent fallback to heuristic/template;
  the audit trail records which actor actually produced the output.
- API validates input (non-empty, ≤4000 chars, limit clamped 1–10).
- Storage uses `INSERT OR REPLACE` on report id; reads return 404 when missing.

## Persistence

Single SQLite file (`data/atlas.db`, overridable via `ATLAS_DB`):
one `reports` table (id, timestamp, claim text, stance, full JSON).
No migrations needed at this scale; no concurrent-write requirements.

## Configuration

Environment only: `MODEL_API_KEY` (enables full mode),
`MODEL_API_BASE_URL` (default `https://api.meta.ai/v1`),
`MODEL_API_MODEL` (default `muse-spark-1.3`), `ATLAS_DB`.
See `.env.example`. No secrets are ever written to the repo or the database.
