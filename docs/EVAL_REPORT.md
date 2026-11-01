# Eval report — v2 dataset (2026-11-01)

Runner: `PYTHONPATH=src .venv/bin/python scripts/run_eval.py --mode degraded`.
Dataset: 24 cases in `eval/cases.py` (10 original + 14 v2).
Result JSON: `eval/results/` (gitignored; timestamped per run).

## Degraded run (offline) — exact output

```text
[degraded] 7/7 cases passed (stance accuracy 1.00, skipped 17)
  skipped (need live sources): ['econ_supported_gdp', 'econ_supported_unemployment',
    'econ_contradicted_population', 'econ_contradicted_wrong_year_value',
    'econ_unit_mismatch', 'econ_incomplete_no_quantity', 'econ_temporally_vague',
    'sci_contextual', 'sci_no_year_match', 'general_insufficient',
    'econ_contradicted_life_expectancy', 'econ_unit_mismatch_basis_points',
    'econ_compound_gdp_unemployment', 'econ_brazil_inflation_multi_year',
    'econ_contradicted_unemployment_trend', 'econ_france_population_precision',
    'econ_adversarial_wb_invocation']
  [PASS] econ_unknown_geo_atlantis
  [PASS] econ_unverifiable_geoless_gdp
  [PASS] general_opinion_democracy
  [PASS] general_prompt_injection_cheese
  [PASS] econ_provider_death_connector_error
  [PASS] malformed_model_fallbacks
    [PASS] minimal_json
    [PASS] invalid_json
    [PASS] unit_null_string
    [PASS] year_as_quantity
  [PASS] boundary_empty_oversize
    [PASS] empty
    [PASS] oversize
```

7/7 passed, stance accuracy 1.00, 0 failures. What the 7 executed cases
prove without any network: unknown/geo-less claims abstain pre-network;
opinions refuse with `not_assessable`; prompt injection neither flips the
stance nor leaks "supported" into the summary; total provider death
degrades to `connector_error` + abstention (no crash); malformed model
output falls back or sanitizes along the pinned paths; empty input is
`not_assessable` and a ~66KB oversize input extracts and abstains.

## Mock-vs-live distinction (read this before citing numbers)

Not all 24 cases mean the same thing:

- **Live cases (17)** exercise the real pipeline against real sources:
  real HTTP to World Bank/OpenAlex, real values, real revision risk.
  In `--mode full` they also use the real model. These are the only cases
  that can catch source drift, and the only ones whose failures might be
  "the world changed" rather than "the code broke".
- **`both` cases (2)** run the real pipeline with real routing, but are
  offline-safe by construction: geo resolution fails before any request
  is sent, so no stubbing is involved and the pass is fully honest.
- **Scripted cases (5, incl. 6 subcases)** stub connectors
  (canned evidence or forced `ConnectError`) and/or the model client
  (scripted responses, fault injection). They prove *code paths* —
  fallback selection, sanitization, degradation, abstention — not
  source behavior. A scripted PASS says "given this input, the code does
  the pinned thing", never "the live source agrees".

The degraded 7/7 therefore covers honesty machinery; live behavior is
covered by the 17 live cases in `--mode full` plus the full-mode spot
checks below.

## Full-mode live spot checks (verified)

Run against the live app in full mode (`muse-spark-1.3`):

| Claim | Observed |
|---|---|
| EN: `The GDP of the United States was $27.36 trillion in 2023.` | `supported` |
| ES: `El desempleo en Alemania fue del 3,1% en 2023` | `supported` |
| Opinion: `Democracy is the best form of government.` | `not_assessable` |

These confirm the model path end to end (structure with a real model,
real retrieval, code verdicts, model summary) in both supported languages
plus the opinion refusal.

## Full run (`--mode full`, real model + live sources) — 24/24

```text
[full] 24/24 cases passed (stance accuracy 1.00, skipped 0)
```

Result JSON: `eval/results/20261101T002822Z.json` (gitignored; observed
values preserved). Cost: ~2 model calls per live case (structure +
summary).

Path to green (honest history, not a clean first pass):

1. First full run: 17/24. Six failures were expectation gaps, one a real
   product bug. Fixed the bug: `not_assessable` rule (b) fired on any
   claim with zero parsed refs, misclassifying bare factual claims
   (moon-cheese); narrowed to empty/whitespace input only, since
   retrieval also searches subject text. Added `expected_full` /
   `drop_in_full` overlays for legitimate model-variance (model assigns
   explicit units → measured path instead of the bare-number flag;
   economy-vs-general routing; model sampling on 66KB input). No overlay
   weakens any stance or traceability requirement.
2. Second run: 22/24. Both failures were an OpenAlex outage window
   (`connector_error`, properly labeled, correct abstention). Fixed
   properly instead of re-running until lucky: one retry after 2s on
   transient `ProviderError` (timeout/connect/429/5xx), covered by
   offline unit tests.
3. Third run: 24/24, stance accuracy 1.00.

## Failure analysis

No failures in the reported degraded run. Known failure *modes* to watch
in full runs, by cause:

1. **Source revision** (informative, not a regression): a World Bank value
   crosses a pinned stance boundary. Record the new value from the result
   JSON; widen the margin or convert the case to a property check
   (as done for France population).
2. **Source outage**: live cases expecting evidence get `connector_error` /
   `source_unavailable` and abstain. The compound case tolerates this via
   `stance_set`; others will FAIL loudly — that is the honest signal.
3. **Model truncation**: `finish_reason=length` with empty content raises
   inside `MetaClient` and triggers deterministic fallbacks. Cases pinning
   `extraction_actor: model` would fail; the analysis itself still
   completes honestly.
4. **Ranking shift** (OpenAlex): science cases depend on year-match over
   top-N works. Margins are wide (1812 vs modern works), but a FAIL here
   means re-checking the retrieved set, not the verify logic.

Triage rule: re-run the single case, read `observed` in the result JSON,
and classify into one of the four before touching code.

## Limits of this eval

- 24 hand-picked cases; not a benchmark, not a sample of real traffic.
- Degraded runs say nothing about model-assisted extraction/summaries
  (except the scripted fault-injection paths, which use doubles).
- No retrieval-relevance scoring beyond indicator/year matching.
- No adversarial phrasing beyond the two pinned cases (authority
  invocation, prompt injection).
- No timing/SLO assertions; `elapsed_ms` is recorded, not gated.
- Live results expire: source values drift, so a green full run is a
  point-in-time claim. The result JSON (with observed values) is the
  durable artifact, not the PASS count.
