# API reference (v0.3.0)

Base URL (local): `http://127.0.0.1:8765`. Machine-readable schema:
[openapi.json](openapi.json) (exported from the live app's `/openapi.json`;
byte-identical to the served document).

All endpoints are localhost-only with no authentication (single-user demo).
Every request and response body is JSON.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | Service, version, mode, connectors |
| POST | `/api/analyze` | Analyze a claim → full `Report` |
| GET | `/api/reports?limit=N` | List recent reports (default 50, clamped 1–200) |
| GET | `/api/reports/{report_id}` | Fetch one report |
| DELETE | `/api/reports/{report_id}` | Delete one report |
| GET | `/` | Static UI (HTML, not in schema) |

### GET /api/status

No parameters. Use it to check full vs degraded mode before a demo:

```json
{
  "service": "evidence-atlas",
  "version": "0.3.0",
  "llm_available": true,
  "llm_model": "muse-spark-1.3",
  "mode": "full (model + deterministic)",
  "connectors": ["worldbank", "openalex"]
}
```

Without a key: `llm_available: false`, `llm_model: null`,
`mode: "degraded (deterministic only)"`.

### POST /api/analyze

Request (`AnalyzeRequest`):

| Field | Type | Default | Rules |
|---|---|---|---|
| `text` | string | required | Trimmed; 1–4000 chars after trim |
| `limit` | int | 5 | Clamped to 1–10 (documented clamp, not an error); non-integers 422 |
| `domain` | string | `"auto"` | One of `auto`, `economy`, `science`, `general` |
| `persist` | bool | true | `false` skips SQLite (no row, no `persist` audit step) |

`domain` overrides the extracted domain when it differs from `auto`
(recorded as `domain_override=A->B` in the route audit step).

 guards:

- Rate limit: 60 `POST /api/analyze` per minute per IP (429 after that).
- Cache: identical `(text, domain, limit)` hits return the cached `Report`
  without running the pipeline (128 entries FIFO, 1h TTL).

Response: `200` with the full `Report` object (see Schemas).
Success bodies carry no `request_id`.

### GET /api/reports

```json
{"reports": [{"report_id": "rep_…", "created_at": "…",
               "claim_text": "…", "stance": "supported"}]}
```

Ordered newest first. `limit` query param defaults to 50, clamped 1–200.

### GET /api/reports/{report_id}

`200` with the full `Report`, or `404 report_not_found`.

### DELETE /api/reports/{report_id}

`200 {"deleted": true}`, or `404 report_not_found` when the id is unknown.
Deletion is permanent; the report JSON is removed from SQLite.

## Error envelope

Every 4xx/5xx uses the unified envelope:

```json
{"error": {"code": "empty_claim", "message": "Empty claim text.",
            "request_id": "9f2c…(32 hex)"}}
```

`request_id` is a fresh uuid4 hex per error, for correlating user reports
with server logs. Tracebacks and request bodies never appear in responses.

| HTTP | `code` | When |
|---|---|---|
| 400 | `empty_claim` | `text` blank after trim |
| 400 | `claim_too_long` | `text` longer than 4000 chars |
| 400 | `invalid_domain` | `domain` not in `auto/economy/science/general` |
| 422 | `invalid_limit` | Non-integer `limit` (out-of-range integers are clamped, not rejected). Missing/wrong-typed `text` also returns 422 with a field-specific mapping |
| 404 | `report_not_found` | Unknown report id, or unknown route |
| 405 | `bad_request` | Framework 4xx (e.g. wrong method); unforeseen validation shapes also use this code |
| 429 | `rate_limited` | More than 60 analyzes/min from one IP |
| 500 | `analysis_failed` | Pipeline fault (traceback stays server-side), or any unhandled error |

Example:

```bash
curl -s -X POST http://127.0.0.1:8765/api/analyze \
  -H 'Content-Type: application/json' -d '{"text": "   "}'
# {"error":{"code":"empty_claim","message":"Empty claim text.","request_id":"…"}}
```

## Schemas (abridged)

`Report`:

| Field | Type | Notes |
|---|---|---|
| `report_id` | string | `rep_` + 12 hex |
| `claim` | object | StructuredClaim (below) |
| `evidences` | object[] | Retrieved evidence |
| `comparisons` | object[] | Deterministic checks |
| `uncertainties` | object[] | `{code, description, severity}` |
| `limitations` | string[] | Fixed 4-item honesty block |
| `conclusion` | object | `{stance, summary, support/contradict ids, caveats, actor, model}` |
| `audit` | object[] | `{step, actor, started_at, ended_at, duration_ms, detail}` |
| `created_at` | string | ISO-8601 |
| `meta` | object | Reproducibility block (below) |
| `elapsed_ms` | number | Wall ms for `analyze()` |

`meta` block: `app_version` ("0.3.0"), `model` (name or null),
`temperature` (0), `max_tokens` (4000), `tolerance` (0.02),
`default` (limit default 5), `prompt_version` ("3"), `input_sha256`,
plus per-stage token usage `usage_extract`, `usage_science`,
`usage_falsify`, `usage_summary` (each
`{prompt_tokens, completion_tokens, reasoning_tokens, total_tokens}`
or null when the stage made no model call).

Stances: `supported`, `partially_supported`, `contradicted`,
`unverifiable`, `not_assessable`.
Verdicts: `consistent`, `contradicts`, `inconclusive`, `not_comparable`.
Actors: `code`, `model`, `heuristic`.
Full field shapes are in [openapi.json](openapi.json) and
`src/atlas/core/types.py`.

## Worked example 1 — GDP claim capped by the precision rule

Source: local run (`analyze(llm=None, persist=False)`, degraded mode,
live World Bank source, 2026-11-01). Request:

```json
{"text": "The GDP of the United States was $27.36 trillion in 2023."}
```

Observed (ids shortened):

- `claim.domain`: `economy`; 1 quantity (27.36e12 USD), year 2023, USA.
- `evidences`: 1 World Bank row, `NY.GDP.MKTP.CD USA 2023`,
  `source_url: https://data.worldbank.org/indicator/NY.GDP.MKTP.CD?locations=USA`.
- `comparisons`: `time_compatibility → consistent`;
  `GDP (current US$) → consistent`, `rel_diff_pct ≈ 1.62`,
  tolerance rule `rel_diff_pct <= 2.0`.
- `uncertainties`: `claimed_precision_exceeded` (major),
  `material_absolute_gap` (minor), `indicator_definition_assumed`
  (minor), `price_basis_unchecked` (major), `source_revision_risk`
  (minor). `conclusion.stance`: `partially_supported` (actor `code`,
  template summary in degraded mode): relative agreement holds, but the
  stated digits ($10B resolution) exceed what the retrieved value
  confirms ($451B gap).
- `meta`: `app_version 0.3.0`, `model null`, `temperature 0`,
  `max_tokens 4000`, `tolerance 0.02`, plus `input_sha256`; all
  `usage_*` null (degraded mode made no model call).

## Worked example 2 — opinion (not_assessable)

Source: local run #2 of 2 (`analyze(llm=None, persist=False)`).
Request:

```json
{"text": "Democracy is the best form of government."}
```

Observed:

- `claim.domain`: `general`; 0 quantities; opinion lexicon hit (`best`).
- `uncertainties`: `opinion_or_nonclaim` (blocking),
  `no_measurable_quantity` (blocking), `metadata_only` (major).
- `conclusion.stance`: `not_assessable`, decided in code before any
  verdict logic runs. Summary is the deterministic template.
- Note: retrieval still ran (general claims query both connectors), but
  with zero quantities no comparison is possible — retrieval noise can
  never rescue a non-claim into a verdict.

## Curl cookbook (read-only except where noted)

```bash
BASE=http://127.0.0.1:8765
curl -s $BASE/api/status            # mode check (full vs degraded)
curl -s "$BASE/api/reports?limit=5" # recent reports
curl -s $BASE/api/reports/REP_ID    # one report (replace REP_ID)
curl -s $BASE/openapi.json -o /tmp/openapi.json  # live schema
# Analysis (writes a report unless persist=false; counts toward rate limit):
curl -s -X POST $BASE/api/analyze -H 'Content-Type: application/json' \
  -d '{"text": "Unemployment in Germany was 3.1% in 2023.", "persist": false}'
# Delete (destructive):
curl -s -X DELETE $BASE/api/reports/REP_ID
```
