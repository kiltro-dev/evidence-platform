> **Histórico (v0.4.0).** v1-core era doc, kept in place; the product is Atlas Live ([README](../README.md), [INDEX](INDEX.md)).

# API reference (v0.4.0)

Base URL (local): `http://127.0.0.1:8765`. Machine-readable schema:
[openapi.json](openapi.json) (exported from the live app's `/openapi.json`;
byte-identical at export time; the served document is authoritative —
regenerate with `curl -s http://127.0.0.1:8765/openapi.json -o docs/openapi.json`
while the app runs, and keep `test_openapi_doc_matches_served_schema` green).

All endpoints are localhost-only with no authentication (single-user demo).
Every request and response body is JSON.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | Service, version, mode, connectors |
| POST | `/api/analyze` | Analyze a claim → full `Report` |
| POST | `/api/check` | ShipCheck batch: split text → per-claim reports + roll-up |
| POST | `/api/check/stream` | Same batch over SSE: `started` → `claim` events → `done` (never cached) |
| GET | `/openapi.json` | Live OpenAPI schema (exported copy in `docs/openapi.json`) |
| GET | `/api/reports?limit=N` | List recent reports (default 50, clamped 1–200) |
| GET | `/api/reports/{report_id}` | Fetch one report |
| DELETE | `/api/reports/{report_id}` | Delete one report |
| GET | `/` | Static UI (HTML, not in schema) |

### GET /api/status

No parameters. Use it to check full vs degraded mode before a demo:

```json
{
  "service": "evidence-atlas",
  "version": "0.4.0",
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
| `persist` | bool | false | `true` writes SQLite (row + `persist` audit step); default `false` so public callers never archive visitor texts by accident (UT-j; the UI sends `true` explicitly) |
| `fresh` | bool | false | `true` skips the identical-text cache READ (re-runs the pipeline) but still refreshes the entry — the UI re-run button uses this |

`domain` overrides the extracted domain when it differs from `auto`
(recorded as `domain_override=A->B` in the route audit step).

 guards:

- Rate limit: 60 quota units per minute per client (429 after that),
  per API key when authenticated else per IP (see Auth below),
  shared bucket across `/api/analyze` (cost 1) and `/api/check`
  (fixed worst-case cost 8 — charging actuals would need a model
  split before the gate, an abuse vector). Validation runs before
  the charge, so malformed (400) requests are free. Loopback
  spellings (`127.0.0.1`/`::1`/`localhost`) share one bucket.
- Cache: identical `(text, domain, limit, persist, model)` hits return
  the cached `Report` without running the pipeline (128 entries FIFO,
  1h TTL), unless `fresh: true`. Concurrent identical misses are
  coalesced: one request runs the pipeline while the others wait and
  share its cached report (a waiter whose owner failed runs the
  pipeline itself instead of inheriting the error). `/api/check`
  caches `persist: false` batches only (same 128/FIFO/1h policy);
  persisted batches always re-run so report ids stay fresh.
  `DELETE /api/reports/{id}` also evicts
  that report's cache entries (no deleted-id resurrection).
- Loopback only: non-`127.0.0.1`/`::1`/`localhost` clients get 403
  `non_loopback_blocked` unless `ATLAS_ALLOW_NON_LOOPBACK=1` or the
  request carries a valid `X-API-Key` (see Auth below, UT-j).

Response: `200` with the full `Report` object (see Schemas).
Success bodies carry no `request_id`.

### POST /api/check

ShipCheck batch (v0.4.0): split pasted text (e.g. an AI answer) into
atomic claims and analyze each one through the full pipeline.

Request (`CheckRequest`): same fields as `AnalyzeRequest`, except `text`
allows 1–12000 chars after trim. `limit`/`domain`/`persist` apply to
every sub-claim; each persisted sub-claim is re-fetchable via
`GET /api/reports/{report_id}`.

Response: `200` with a roll-up object (NOT a `Report` — there is no
single claim):

```json
{
  "claim_count": 2,
  "reports": [{}, {}],
  "rollup": {"supported": 1, "partially_supported": 0, "contradicted": 0,
             "unverifiable": 1, "not_assessable": 0},
  "errors": 0,
  "elapsed_ms": 11800.5,
  "meta": {"app_version": "0.4.0", "prompt_version": "4",
           "splitter_actor": "heuristic", "splitter_model": null,
           "splitter_notes": ["no model available; heuristic sentence split used"],
           "usage_split": null,
           "input_sha256": "…"}
}
```

- `reports[i]` is a full `Report` per sub-claim, or
  `{"claim_text": "…", "error": "…"}` when that sub-claim failed — one
  failure never kills the batch, and error entries stay out of the
  roll-up.
- At most 8 sub-claims (splitter cap); over-long input is truncated
  to the first 6000 chars before splitting, short segments (<12
  chars) are dropped, and past-the-cap claims are cut — all
  disclosed in `splitter_notes`, which also states the roll-up
  covers analyzed claims only. The heuristic path guards common
  abbreviations (`U.S.`, `e.g.`…) so they never split mid-clause.
- Splitting limitation: a verbatim span can drop a qualifier living in
  another clause, changing the meaning. Spans are exact substrings,
  never complete interpretations — each report checks the span, not
  the discourse.
- Guards: same 60-units/min bucket per client (a batch costs 8,
  charged after validation; per API key when authenticated, else per
  IP). Identical-text cache covers `persist: false` batches only
  (and only when no `deadline_s` is set).
  Sub-claims run in parallel (one thread each, input order kept).
- `deadline_s` (optional, clamped 1–600s): global batch budget.
  Late sub-claims become `deadline_exceeded` error entries; partial
  results keep their roll-up; `meta.deadline_exceeded` flags it.

### POST /api/check/stream (SSE, UT-l)

Same contract as `/api/check` (validation, auth, cost 8, deadline),
but `text/event-stream`: `started` → one light `claim` event per
finished sub-claim (`index`/`stance` or `error`) → final `done` with
the complete batch dict. Never cached. Pipeline failures stream a
single generic `error` event instead of an HTTP 500 body.
  Batch wall time is the slowest sub-claim: there is no batch-level
  deadline, each leaf times out on its own (connectors 20s + one
  retry, model 120s). Per-claim elapsed times ride in each sub-report.
- Cost scales with sub-claims: each runs the full pipeline (up to 5
  model calls per claim, plus 1 split call per batch — an 8-claim batch
  costs up to 1+8×5=41 calls). Post-hoc use, not inline.

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

### Auth (UT-j public-deploy mode)

- Without `ATLAS_API_KEY`: localhost only (loopback guard, no auth).
- With `ATLAS_API_KEY`: `X-API-Key` header REQUIRED on
  `/api/analyze`, `/api/check`, `GET /api/reports[/{id}]` and
  `DELETE /api/reports/{id}` (401 `unauthorized` otherwise);
  `/api/status` and `/` stay open. A valid key also bypasses the
  loopback guard (public deploy behind a proxy).
- Buckets are per API key when authenticated (one user cannot burn
  another's quota behind a shared proxy IP), else per client IP.
  `X-Forwarded-For` is honored ONLY with `ATLAS_TRUST_PROXY=1`
  (set it solely behind a proxy that overwrites the header).
- Cost ceiling by construction: 60 units/min × batch-cost 8 ×
  ≤8 sub-claims × bounded model calls (4000 tokens each) — see
  `docs/SECURITY_OPS.md` for the deploy checklist.

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
| 400 | `claim_too_long` | `text` longer than 4000 chars (`/api/analyze`) or 12000 (`/api/check`) |
| 400 | `invalid_domain` | `domain` not in `auto/economy/science/general` |
| 422 | `invalid_limit` | Non-integer `limit` (out-of-range integers are clamped, not rejected). Missing/wrong-typed `text` also returns 422 with a field-specific mapping |
| 404 | `report_not_found` | Unknown report id, or unknown route |
| 403 | `non_loopback_blocked` | Non-loopback client (demo server is localhost-only; override `ATLAS_ALLOW_NON_LOOPBACK=1`) |
| 405 | `bad_request` | Framework 4xx (e.g. wrong method); unforeseen validation shapes also use this code |
| 429 | `rate_limited` | More than 60 quota units/min from one IP (`/api/analyze` = 1, `/api/check` = 8; malformed requests are free) |
| 500 | `analysis_failed` | Pipeline fault (traceback stays server-side), or any unhandled error |

Codes name the failing check; the HTTP status distinguishes a malformed
shape (422, FastAPI validation before the handler runs) from a semantic
rejection (400, the handler's own rules) — so 422s reuse the 400-code of
the field at fault (e.g. wrong-typed `text` is 422 `empty_claim`).

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

`meta` block: `app_version` ("0.4.0"), `model` (name or null),
`temperature` (0), `max_tokens` (4000), `tolerance` (0.02),
`default` (limit default 5), `prompt_version` ("4"), `input_sha256`,
plus per-stage token usage `usage_extract`, `usage_science`,
`usage_falsify`, `usage_summary` (each
`{prompt_tokens, completion_tokens, reasoning_tokens, total_tokens,
cached_tokens}` — `cached_tokens` is prompt-cache accounting, 0 when
the provider omits it — or null when the stage made no model call).

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
- `meta`: `app_version 0.4.0`, `model null`, `temperature 0`,
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
