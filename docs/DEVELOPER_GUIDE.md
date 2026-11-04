> **Histórico (v0.4.0).** v1-core era doc, kept in place; the product is Atlas Live ([README](../README.md), [INDEX](INDEX.md)).

# Developer guide

How to extend Evidence Atlas: new domains, connectors, strategies, and
providers. Read [ARCHITECTURE.md](ARCHITECTURE.md) first for the map.

## Layout

```text
src/atlas/
  api/app.py            # FastAPI: endpoints, envelope, rate limit, cache
  core/types.py         # Pydantic model (Report, Claim, Evidence, ...)
  core/units.py         # quantity parsing + normalization
  core/compare.py       # numeric + time comparison, tolerance
  connectors/__init__.py# SourceConnector protocol + REGISTRY + for_domain
  connectors/worldbank.py, openalex.py, countries.py
  models/meta.py        # MetaClient (real model, usage, effort)
  models/heuristic.py   # regex fallback extractor
  models/scripted.py    # test doubles (ScriptedClient, FaultClient)
  pipeline/extract.py   # structure_claim (+ sanitize + spans + backfill)
  pipeline/retrieve.py  # parallel connector fan-out
  pipeline/verify.py    # verify_quantitative, assess_science
  pipeline/science.py   # abstract-stance assessment (non-verifying)
  pipeline/falsify.py   # adversarial self-review (advisory only)
  pipeline/synthesize.py# decide_stance, summaries, citation+strength checks
  pipeline/run.py       # analyze() orchestrator, APP_VERSION, meta block
  storage/store.py      # SQLite save/get/list/delete
web/index.html          # static UI (HTTP only, never imports pipeline)
eval/cases.py           # 28 eval cases + rationale strings
eval/paraphrases.py     # 6x5 stability probe inputs
eval/unanswerable.py    # 20-variant abstention probe inputs
scripts/run_eval.py     # eval runner (--mode, --paraphrase, --unanswerable)
scripts/smoke.sh        # 29-check smoke (offline default)
tests/unit tests/e2e tests/integration  # 306 offline + live-marked
```

## Add a model-assisted stage (falsify/science pattern)

New model calls must follow the `falsify.py` / `science.py` contract:

1. One narrow JSON schema with a falsify/judge-only job; the model may
   point at flaws, never assert verdicts.
2. Code validates everything: ids must resolve, enums must be known,
   malformed output degrades to a logged no-op (never raises).
3. Stages are advisory: they surface doubt (comparisons/uncertainties),
   never move stances — eval run 1 fired 5 falsify overrides, all
   spurious on inspection, plus 2 reproduced live (see
   `docs/EVAL_REPORT.md`). New metrics that must not verify go into
   `NON_VERIFYING_METRICS`.
4. Degraded-safe: `llm=None`/unavailable returns empty with an audit
   skip entry. Wire usage via `_usage_seq`/`_take_usage` in `run.py`
   so `meta.usage_*` attributes spend to the spending stage.
5. Tests: scripted-client unit tests (valid/invalid/degraded) + e2e
   call-count update in `tests/e2e/test_pipeline_offline.py`.

## Add a domain (e.g. `climate`)

1. Extend `Domain` in `core/types.py` with the new literal.
2. Extend `VALID_DOMAINS` in `pipeline/run.py` (API validation imports it).
3. Add keyword classification in `models/heuristic.py` (EN + ES lists if
   the domain has Spanish vocabulary).
4. Route retrieval: either tag an existing connector's `domains` with the
   new domain, or write a new connector (below) and tag it.
5. Add a verify path in `pipeline/verify.py` (new function) and dispatch
   it from `pipeline/run.py` next to the science/economy branch.
6. Add eval cases in `eval/cases.py` with `mode` set (`live` unless the
   case is offline-safe) and a `rationale` string.
7. Update the UI `stanceInfo`/routing-agnostic parts only if the domain
   needs new display (usually not — the report shape is unchanged).

## Add a connector (e.g. a new source)

1. Create `connectors/mysource.py` with a class exposing `name: str`,
   `domains: tuple[str, ...]`, `last_query: str | None`, and
   `retrieve(claim, limit=5) -> list[Evidence]`.
2. Transport failures: catch `httpx.HTTPError`/`ValueError` and raise
   `as_provider_error(exc, context)` from `connectors/__init__.py`
   (transient ⇒ `connector_error`, permanent ⇒ `source_unavailable`).
   "No match" is `[]`, never an exception.
3. Validate anything interpolated into URLs (see the ISO3 allowlist in
   `connectors/worldbank.py`); set `self.last_query` before sending so
   the audit trail echoes the query.
4. Register at import: `register(MyConnector())`, and import the module
   in `pipeline/run.py` (self-register pattern, next to the existing two).
5. Set `source_kind`/`evidence_kind` honestly: keyless aggregate APIs are
   `secondary`; measured values are `direct`, metadata is `contextual`.
6. Tests: unit tests with stubbed HTTP (see `tests/unit/test_providers.py`
   pattern), one offline e2e with canned evidence, and — if the source is
   keyless and stable — a `-m live` integration test.

## Add a verify strategy (new check type)

1. Implement it in `pipeline/verify.py` returning
   `(list[Comparison], list[Uncertainty])`. Keep it model-free:
   `verify.py` must never import anything from `models/`.
2. Decide whether its metrics are *verifying* (can yield `supported`) or
   not; if not, add the metric name to `NON_VERIFYING_METRICS` in
   `pipeline/synthesize.py` with a comment.
3. New uncertainty codes: use `snake_case`, add a one-line description,
   pick the honest severity (`blocking` only if no conclusion is possible).
4. Wire the dispatch in `pipeline/run.py` (domain branch) and add eval
   coverage (`uncertainty_present`, `verdict_present`, property checks).

## Add a model provider (alternative to MetaClient)

1. Implement the used surface: `.available: bool`, `.name: str`,
   `.complete_json(system, user, max_tokens) -> dict`. Raising on empty
   content (with `finish_reason` context) is required so fallbacks trigger.
2. Keep both pipeline call sites at `max_tokens=4000`, `temperature=0`
   unless you re-verify truncation behavior (see
   [LIVE_MODEL.md](LIVE_MODEL.md): hidden reasoning eats the budget).
3. Lazy-import third-party SDKs inside the client (as `meta.py` does with
   `openai`) so degraded mode never needs the dependency.
4. Swap the construction site: `get_llm()` in `api/app.py` and the `full`
   branch of `scripts/run_eval.py`. Everything downstream takes `llm` as
   an opaque object — no other call sites exist.
5. Add a double in `models/scripted.py` if the provider needs new fault
   shapes for tests.

## Test commands

```bash
.venv/bin/python -m pytest tests/unit tests/e2e -q       # offline gate (306)
.venv/bin/python -m pytest tests/unit/test_verify.py -q  # one file
.venv/bin/python -m pytest tests/integration -q -m live  # live sources
.venv/bin/python -m pytest --collect-only -q tests/unit tests/e2e  # count only
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --mode degraded  # offline eval
bash scripts/smoke.sh --port 8765                        # end-to-end smoke
```

Conventions: offline tests must not touch the network (stub HTTP or
connectors); live tests get `-m live` and fail honestly on breakage;
every new uncertainty code or stance rule needs a unit test plus an eval
case where behavior is user-visible.

## Definition of done (docs wave)

- New user-visible behavior is in README + API.md + the relevant guide.
- New eval cases have `rationale` strings; EVAL.md's table stays complete.
- `docs/openapi.json` re-exported if any endpoint changed
  (`curl -s localhost:8765/openapi.json -o docs/openapi.json`, then
  byte-compare with a fresh fetch).
- Smoke + offline gate green; no `POST /api/analyze` against the metered
  model in routine verification (use `llm=None` or scripted doubles).
