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
claim → `supported`; ES unemployment claim
(`El desempleo en Alemania fue del 3,1% en 2023`) → `supported`;
pure opinion → `not_assessable`. Degraded offline eval: 7/7.
Detail: [EVAL_REPORT.md](EVAL_REPORT.md).

## Operational findings (muse-spark-1.3)

- The model spends heavily on hidden reasoning tokens (~48+ even for
  trivial prompts; `response_format: json_object` does not reduce it).
  Budgets under ~1500 tokens risk `finish_reason=length` with `None`
  content. Both pipeline calls now use `max_tokens=4000`.
- Empty content raises a descriptive `RuntimeError` (no silent `{}`),
  and both fallbacks (heuristic extract, template summary) log a
  warning naming the cause. No credentials appear in these logs.
- Summary payload was slimmed to id/metric/verdict/notes + code/
  description/severity to cut prompt + reasoning cost.
- Single-analysis cost observed: 2 model calls, a few thousand tokens
  total (exact usage varies with reasoning length).

## Limits

- No per-call usage accounting persisted (usage object available on the
  raw response; not yet recorded per report).
- Reasoning length is non-deterministic; truncation fallbacks remain
  possible under load and are handled by degradation, not retry.
