# Architecture

Evidence Atlas is a **Python monolith in one process**: FastAPI serves a
JSON API and a static single-page UI; a staged pipeline turns a claim into
a traceable report; SQLite persists reports; two HTTP connectors reach
public sources; one model client reaches the Meta Model API.

Version described here: **0.2.0** (`APP_VERSION` in `pipeline/run.py`,
surfaced as `version` in `GET /api/status` and `meta.app_version`).

## C4 L1 — System context

```mermaid
flowchart TB
    user(["Evaluator / demo user<br/>(browser)"])
    atlas["Evidence Atlas<br/>(this system)"]
    wb[("World Bank Open Data<br/>(keyless HTTPS API)")]
    oa[("OpenAlex<br/>(keyless HTTPS API)")]
    meta[("Meta Model API<br/>(muse-spark-1.3, keyed)")]

    user -- "claim text, reads report" --> atlas
    atlas -- "indicator observations" --> wb
    atlas -- "works metadata" --> oa
    atlas -- "structure + summarize (optional)" --> meta
```

The system is single-user and localhost-only. Without a model key it
degrades deterministically (heuristic extraction + template summaries);
the verification core is identical in both modes.

## C4 L2 — Containers (all in one process)

```mermaid
flowchart TB
    subgraph proc["One uvicorn process (127.0.0.1)"]
        ui["Static UI<br/>web/index.html<br/>(no build step, HTTP only)"]
        api["FastAPI app<br/>src/atlas/api/app.py"]
        pipe["Pipeline<br/>src/atlas/pipeline/"]
        store[("SQLite store<br/>src/atlas/storage/<br/>data/atlas.db")]
    end
    wb[("World Bank API")]
    oa[("OpenAlex API")]
    meta[("Meta Model API")]

    ui -- "GET / + /api/*" --> api
    api -- "analyze(text, limit, domain, persist)" --> pipe
    api -- "save/get/list/delete" --> store
    pipe -- "retrieve()" --> wb
    pipe -- "retrieve()" --> oa
    pipe -- "complete_json() x <= 2" --> meta
```

| Container | Path | Responsibility |
|---|---|---|
| UI | `web/index.html` | One static page: input, verdict, evidence, comparisons, uncertainties, audit, recent-analyses history with delete. Never imports the pipeline. |
| API | `api/app.py` | Endpoints, unified error envelope, rate limit, identical-text cache, static mount. |
| Pipeline | `pipeline/` | Six audited stages: extract → route → retrieve → verify → synthesize → persist. |
| Store | `storage/store.py` + `data/atlas.db` | One-table SQLite persistence. Reads never create files. |

## C4 L3 — Components

```mermaid
flowchart TB
    api["api/app.py<br/>endpoints + envelope<br/>+ rate limit + cache"]
    run["pipeline/run.py<br/>orchestrator + audit<br/>+ meta block"]
    ext["pipeline/extract.py<br/>structure_claim"]
    ver["pipeline/verify.py<br/>verify_quantitative<br/>assess_science"]
    syn["pipeline/synthesize.py<br/>decide_stance<br/>synthesize"]
    cmp["core/compare.py<br/>numbers + time"]
    typ["core/types.py<br/>pydantic model"]
    units["core/units.py<br/>parse + normalize"]
    reg["connectors/__init__.py<br/>protocol + registry"]
    cwb["connectors/worldbank.py<br/>+ countries.py"]
    coa["connectors/openalex.py"]
    meta["models/meta.py<br/>MetaClient"]
    heu["models/heuristic.py<br/>regex fallback"]
    scr["models/scripted.py<br/>test doubles"]
    store["storage/store.py<br/>save/get/list/delete"]

    api --> run
    api --> store
    run --> ext
    run --> reg
    run --> ver
    run --> syn
    run --> store
    reg --> cwb
    reg --> coa
    ext --> heu
    ext --> meta
    syn --> meta
    ver --> cmp
    ver --> units
    ext --> units
    run --> typ
    scr -. "tests + eval only" .-> run
```

### Component table

| Component | Path | Responsibility |
|---|---|---|
| Types | `core/types.py` | Pydantic domain model: claim, evidence, comparison, uncertainty, conclusion, audit, report. Five stances incl. `not_assessable`. |
| Units | `core/units.py` | Deterministic parsing/normalization of quantities, scales, currencies, temperatures, decimal commas. |
| Compare | `core/compare.py` | Deterministic numeric + time comparison; strict comparability; 2% tolerance default (`DEFAULT_REL_TOLERANCE`). |
| Connectors | `connectors/` | `SourceConnector` protocol + `REGISTRY`; `worldbank.py` (+ ISO3 allowlist, `countries.py`), `openalex.py`. `ProviderError(transient)` / `InvalidGeoError`. `last_query` echo per retrieve. |
| Models | `models/` | `meta.py` (`MetaClient`, lazy OpenAI SDK), `heuristic.py` (regex fallback), `scripted.py` (test doubles). |
| Extract | `pipeline/extract.py` | Claim structuring via LLM with heuristic fallback; model output sanitized (unit allowlist, finite floats, year-like drop, 20-item caps); LLM gaps backfilled deterministically. |
| Verify | `pipeline/verify.py` | `verify_quantitative` (economy) and `assess_science` (bibliographic); `detect_claim_shape` (opinion/compound flags); code-only. |
| Synthesize | `pipeline/synthesize.py` | Deterministic stance rules (`decide_stance`); citation validation; caveat union; optional LLM summary rewrite. |
| Run | `pipeline/run.py` | Orchestrator: 6 audited stages, domain override, uncertainty dedupe, connector error isolation, `meta` block, persistence. |
| Store | `storage/store.py` | SQLite: save/get/list/delete reports. Init-on-write; reads are create-free. |
| API | `api/app.py` | FastAPI app; status/analyze/reports/delete endpoints; envelope; 60/min/IP rate limit; 128-entry 1h cache; static mount. |

## Sequence — analysis (`POST /api/analyze`)

```mermaid
sequenceDiagram
    participant U as UI / client
    participant A as api/app.py
    participant R as pipeline/run.py
    participant X as extract
    participant C as connectors
    participant V as verify
    participant S as synthesize
    participant M as MetaClient (opt)
    participant D as SQLite

    U->>A: POST /api/analyze {text, limit, domain, persist}
    A->>A: rate limit 60/min/IP
    A->>A: validate (non-empty, ≤4000, domain, clamp limit)
    A->>A: cache lookup (text, domain, limit)
    alt cache hit
        A-->>U: Report (cached, no pipeline run)
    else cache miss
        A->>R: analyze(text, llm, limit, persist, domain)
        R->>X: structure_claim (extract)
        X->>M: complete_json(structure) [full mode]
        M-->>X: JSON claim fields
        X-->>R: StructuredClaim (or heuristic fallback)
        R->>R: route: domain override + for_domain()
        loop each connector
            R->>C: retrieve(claim, limit)
            C-->>R: list[Evidence] / ProviderError / InvalidGeoError
        end
        R->>V: verify_quantitative / assess_science (verify)
        V-->>R: comparisons + uncertainties
        R->>S: synthesize (stance in code)
        S->>M: complete_json(summary) [full mode]
        M-->>S: {summary, caveats}
        S-->>R: Conclusion (validated citations, caveat union)
        opt persist=true
            R->>D: save_report (persist)
        end
        R-->>A: Report (+ meta, elapsed_ms)
        A->>A: cache put (FIFO 128, TTL 1h)
        A-->>U: Report
    end
```

## Sequence — model interaction (full vs degraded)

```mermaid
sequenceDiagram
    participant R as pipeline/run.py
    participant M as MetaClient
    participant H as heuristic/template

    Note over R: full mode: llm available
    R->>M: complete_json(STRUCTURE_SYSTEM, max_tokens=4000)
    alt valid JSON
        M-->>R: structured fields (sanitized + backfilled)
    else transport/JSON error
        M-->>R: exception → log warning
        R->>H: extract_heuristic (actor: heuristic)
    end
    R->>M: complete_json(SUMMARY_SYSTEM, max_tokens=4000)
    alt valid summary
        M-->>R: {summary, caveats} (citations validated)
    else error / missing key
        R->>H: template_summary (actor: code)
    end
    Note over R: degraded mode: llm=None → heuristic + template directly
```

At most 2 model calls per analysis (structure + summary), asserted by
`tests/e2e/test_pipeline_offline.py`. Both use `max_tokens=4000`,
`temperature=0`.

## Sequence — retrieval (per connector)

```mermaid
sequenceDiagram
    participant R as pipeline/run.py
    participant W as worldbank.py
    participant O as openalex.py
    participant N as HTTPS APIs

    R->>R: connectors = for_domain(effective_domain)
    alt economy claim
        R->>W: retrieve(claim, limit)
        W->>W: match_indicator + resolve_country + ISO3 allowlist
        W->>N: GET indicator observations (20s timeout)
        N-->>W: rows
        W-->>R: direct Evidence (+ last_query echo)
    else science claim
        R->>O: retrieve(claim, limit)
        O->>O: subject query (≤300 chars)
        O->>N: GET works search (20s timeout)
        N-->>O: works
        O-->>R: contextual Evidence (+ last_query echo)
    else general claim
        R->>W: retrieve (fallback: both run)
        R->>O: retrieve (fallback: both run)
    end
```

`general`-tagged connectors run only for `general` claims, so domain
claims never collect cross-domain noise. Audit `retrieve` steps record
`status=ok|error|invalid_geo`, `count`, and the echoed `query`.

## Sequence — error / fallback paths

```mermaid
sequenceDiagram
    participant A as api/app.py
    participant R as pipeline/run.py
    participant C as connector

    Note over A: input faults → envelope, no pipeline run
    A-->>A: empty → 400 empty_claim
    A-->>A: >4000 chars → 400 claim_too_long
    A-->>A: bad domain → 400 invalid_domain
    A-->>A: non-integer limit → 422 invalid_limit
    A-->>A: 61st POST/min/IP → 429 rate_limited

    Note over R: connector faults → uncertainties, analysis continues
    R->>C: retrieve()
    alt invalid ISO3 (allowlist)
        C-->>R: InvalidGeoError → invalid_geo (major), no request sent
    else timeout/connect/429/5xx (transient)
        R->>C: retry once after 2s (RETRY_DELAY_S)
        C-->>R: still failing → connector_error (major)
    else HTTP 4xx / bad payload (permanent)
        C-->>R: ProviderError(permanent) → source_unavailable (major)
    else unexpected exception
        C-->>R: Exception → connector_error (major, ≤200 chars)
    end

    Note over A: pipeline crash → envelope, traceback stays server-side
    A-->>A: any unhandled exception → 500 analysis_failed
    A-->>A: unknown report / route → 404 report_not_found
```

## Data model (`core/types.py`)

`Report` is the unit of output and persistence:

- `report_id`, `created_at`, `elapsed_ms` (wall ms for `analyze()`).
- `claim: StructuredClaim` — `raw_text`, `domain` (economy/science/general),
  `claim_type`, `subject`, `indicator`, `entities`, `quantities[]`,
  `time_refs[]`, `geo_refs[]`, `conditions[]`, `context_notes`,
  `extraction_actor` (code/model/heuristic), `extraction_model`.
- `evidences[]: Evidence` — `connector`, `source_name`, `source_url`,
  `source_kind` (primary/secondary/reproduction), `evidence_kind`
  (direct/contextual), `locator`, `excerpt`, `payload` (unit, year, value,
  query echo, ...).
- `comparisons[]: Comparison` — `metric`, `verdict`
  (consistent/contradicts/inconclusive/not_comparable), `rel_diff_pct`,
  `tolerance_rule`, `within_tolerance`, `notes`, `actor` (always code).
- `uncertainties[]: Uncertainty` — `code`, `description`, `severity`
  (blocking/major/minor). Deduped on (code, description).
- `conclusion: Conclusion` — `stance` (5 values), `summary`,
  `support_comparison_ids[]`, `contradict_comparison_ids[]`, `caveats[]`
  (code ∪ model), `actor`, `model`.
- `audit[]: AuditStep` — `step`, `actor`, `started_at`, `ended_at`,
  `duration_ms`, `detail`. Order: extract → route → retrieve* → verify →
  synthesize → persist?.
- `limitations[]` — fixed 4-item honesty block (`LIMITATIONS` in `run.py`).
- `meta{}` — `app_version`, `model` (name or null), `temperature` (0),
  `max_tokens` (4000), `tolerance` (0.02), `default` (limit default 5),
  `prompt_version` ("2": structure prompt with domain-preference rule), `input_sha256`.

SQLite (`storage/store.py`): one `reports` table
`(report_id PK, created_at, claim_text, stance, report_json)`.
`save_report` does `INSERT OR REPLACE` after `init_db` (init-on-write);
`get_report`/`list_reports`/`delete_report` first check
`_has_reports_table` and return empty without creating anything.

## Execution flow

`POST /api/analyze {text, limit, domain, persist}` → `analyze()`:

1. **extract** — `structure_claim`: LLM JSON (actor `model`) or heuristic
   (actor `heuristic`). Model output is sanitized (unit allowlist, finite
   floats, year-like quantities dropped, 20-item list caps). Years/countries/
   quantities the LLM drops are backfilled from regex so explicit text is
   never silently lost.
2. **route** — `for_domain` on the effective domain. An explicit
   `domain` override replaces the extracted domain and is recorded in the
   audit detail (`domain_override=A->B`). Economy→worldbank,
   science→openalex, general→both. Connectors tagged `general` are
   fallbacks only.
3. **retrieve** — each connector returns `Evidence` with source kind
   (secondary for both MVP sources) and evidence kind (World Bank values
   `direct`, OpenAlex metadata `contextual`). `last_query` is echoed into
   the audit detail. `InvalidGeoError` → `invalid_geo` uncertainty (no
   request sent); `ProviderError(transient)` → `connector_error`;
   `ProviderError(permanent)` → `source_unavailable`; anything else →
   `connector_error` with ≤200 chars of text. Analysis continues.
4. **verify** — code-only. Economy/general: per-quantity same-unit matching,
   same-period gating, `compare_numbers`. Science: year-match checks plus a
   mandatory `metadata_only` uncertainty; general claims with no numeric
   comparisons also get the science assessment. `detect_claim_shape` flags
   opinions/non-claims (`opinion_or_nonclaim`) and compound claims
   (`compound_claim`, detect+disclose only). Nothing here calls a model.
5. **synthesize** — `decide_stance` rules (see below); LLM summary rewrite
   attempted only if a client is available, else the deterministic template.
   Model citations to unknown `[cmp_*]` ids are stripped (logged);
   blocking/major template caveats are always retained (caveat union).
6. **persist** — report JSON saved to SQLite when `persist=true`;
   report id returned. Skipped (and absent from the audit) otherwise.

Every stage appends an `AuditStep{step, actor, detail, duration_ms}`; the
UI renders `CODE` / `MODEL` / `HEURISTIC` badges from it.

## Stance rules (`synthesize.decide_stance`)

Checked in order; the first match wins:

1. `opinion_or_nonclaim` uncertainty present, or claim empty of assessable
   content (zero quantities, times, geos + `claim_type == "other"`) →
   `not_assessable`.
2. Any **verifying** `contradicts` → `contradicted`.
3. Any verifying `consistent` → `supported` (`partially_supported` if a
   blocking uncertainty exists).
4. Only non-verifying consistency (`time_compatibility`,
   `publication_year_match`) → `partially_supported`.
5. Otherwise → `unverifiable`.

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

- Connector exceptions → coded uncertainties (`invalid_geo`,
  `connector_error`, `source_unavailable`); pipeline continues.
- LLM transport/JSON errors → silent fallback to heuristic/template;
  the audit trail records which actor actually produced the output.
- API validates input (non-empty, ≤4000 chars, `auto|economy|science|general`
  domain, limit clamped 1–10); all failures use the unified envelope
  `{"error": {"code", "message", "request_id"}}`.
- Storage uses `INSERT OR REPLACE` on report id; reads return 404 when missing.
- Identical `(text, domain, limit)` analyses are cached (128 entries FIFO,
  1h TTL); a hit skips the pipeline entirely.

## Persistence

Single SQLite file (`data/atlas.db`, overridable via `ATLAS_DB`):
one `reports` table (id, timestamp, claim text, stance, full JSON).
Init-on-write: the directory/table are created only by `save_report`.
No migrations needed at this scale; no concurrent-write requirements.
No TTL on stored reports — delete explicitly.

## Configuration

Environment only: `MODEL_API_KEY` (enables full mode),
`MODEL_API_BASE_URL` (default `https://api.meta.ai/v1`),
`MODEL_API_MODEL` (default `muse-spark-1.3`), `ATLAS_DB`.
See `.env.example`. No secrets are ever written to the repo or the database.
