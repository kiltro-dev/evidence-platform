# Live Meta Model API integration (verified 2026-10-31)

## Configuration

- `.env` (gitignored, mode 600) provides `MODEL_API_KEY`,
  `MODEL_API_BASE_URL=https://api.meta.ai/v1`,
  `MODEL_API_MODEL=muse-spark-1.3`.
- `MetaClient` loads `.env` via `python-dotenv` on construction
  (pinned in `requirements.txt`); explicit args and process env still win.
- Key presence is verified by boolean + length only; values never appear
  in logs, output, or commits.

## Verification record

1. `MetaClient().available` → `True` (fresh process, `.env` only).
2. Minimal call `{"pong": 1}` → `{"pong": 1}`, ~3s, valid JSON dict.
3. Full `analyze()` (GDP claim) → stance `supported`, `extract: model`,
   `summary: model`, real comparison ids cited.
4. HTTP: `GET /api/status` → `full (model + deterministic)`,
   `llm_model: muse-spark-1.3`; `POST /api/analyze` (unemployment claim)
   → `supported`, model/model actors.
5. Offline suite still green (179/179 at v2; 28/28 at the time) —
   the model path is additive only.

## v2 live verification (2026-11-01)

Full-mode spot checks against the live app (`muse-spark-1.3`): EN GDP
claim → `supported` (under the v2 tolerance-only rule); ES unemployment
claim (`El desempleo en Alemania fue del 3,1% en 2023`) → `supported`;
pure opinion → `not_assessable`. Degraded offline eval: 7/7.
Detail: [EVAL_REPORT.md](EVAL_REPORT.md).

## v3 live verification (2026-11-01)

Same claims under the claimed-precision rule: EN GDP →
`partially_supported` + `claimed_precision_exceeded` (Δ1.62% within
tolerance, stated digits unconfirmed); ES unemployment → `supported`;
opinion → `not_assessable`. Falsify stage returns real attacks at
`reasoning_effort="low"` (2 attacks, 0 fatal/high on the unemployment
claim). Per-stage usage persists in `meta.usage_*`; observed totals
~2–4k tokens/analysis. Detail: [EVAL_REPORT.md](EVAL_REPORT.md).

## v4 live verification (2026-11-01, v0.4.0 ShipCheck)

- Strict `json_schema` decoding verified on Chat Completions (incl.
  null-union types); structure/science/summary/falsify/split calls use
  it. GDP structuring probe: correct value/unit/spans/geo under the
  schema.
- Responses API + `web_search` verified: end-to-end counter-evidence
  call returned 5 real `url_citation`s (INSEE + WorldEconomics,
  genuine 7.4%/7.5% vs 7.3% counter-evidence). Usage observed:
  **60,001 in / 1,532 out / 61,533 total tokens** — search context
  inflates input ~20–60k tokens/call. At Standard Spark pricing
  ($1.25/in + $4.25/out per 1M) that single call costs ~$0.08; a full
  analysis with web falsify lands ~$0.10–0.15, not ~$0.02. Disable
  with `ATLAS_WEB_FALSIFY=0` when cost matters.
- Vision input verified mechanically (data-URL accepted,
  schema-constrained JSON out) but OCR on probe images was unreliable
  (misread digits, then a reasoning truncation on a 3.7KB image);
  image intake is CUT for v0.4.0 (see ADR-0009). No vision code
  ships.

## Operational findings (muse-spark-1.3)

- The model spends heavily on hidden reasoning tokens (~48+ even for
  trivial prompts; `response_format: json_object` does not reduce it).
  Budgets under ~1500 tokens risk `finish_reason=length` with `None`
  content. Structure/falsify/summary use `max_tokens=4000`, abstracts
  1500 — and small judgment calls (falsify, abstract stance) pass
  `reasoning_effort="low"`: at default depth the falsify call burned
  3998 reasoning tokens and truncated to empty content (observed live).
- Empty content raises a descriptive `RuntimeError` (no silent `{}`),
  and all fallbacks (heuristic extract, skipped falsify/science,
  template summary) log a
  warning naming the cause. No credentials appear in these logs.
- Summary payload was slimmed to id/metric/verdict/notes + code/
  description/severity to cut prompt + reasoning cost.
- Single-analysis cost observed (v0.4.0): up to 5 model calls on the
  single-claim path (structure + abstract-stance + falsify + summary +
  web counter-evidence; 3–4 on non-science/no-web paths), plus 1 split
  call on the batch path. Chat calls cost a few thousand tokens total;
  the web call adds ~20–60k input tokens (~$0.08 at Standard pricing).
  Per-stage usage persists in `meta.usage_*`; batch cost scales with
  sub-claims (cap 8).

## Limits

- Per-call usage IS persisted (`meta.usage_*` per stage); totals vary
  with reasoning length and are point-in-time observations.
- Reasoning length is non-deterministic; truncation fallbacks remain
  possible under load and are handled by degradation, not retry.
- `reasoning_effort` values other than `"low"` on falsify/science are
  untested; structure/summary stay at default depth by design.
