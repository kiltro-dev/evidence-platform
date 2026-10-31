# Evaluation

Runner: `scripts/run_eval.py` (live sources, `llm=None` degraded mode).
Cases: `eval/cases.py` (10 cases). Results: `eval/results/*.json` (gitignored).

## Cases

| id | claim type | expected stance |
|---|---|---|
| econ_supported_gdp | backed by evidence | supported |
| econ_supported_unemployment | backed by evidence | supported |
| econ_contradicted_population | contradicting data | contradicted |
| econ_contradicted_wrong_year_value | contradicting data | contradicted |
| econ_unit_mismatch | incompatible units (EUR vs USD) | unverifiable |
| econ_incomplete_no_quantity | no measurable quantity | unverifiable |
| econ_temporally_vague | vague time, no quantity | unverifiable |
| sci_contextual | year-matching works, metadata only | partially_supported |
| sci_no_year_match | works exist, wrong era | unverifiable |
| general_insufficient | no applicable source | unverifiable |

Coverage vs mission §7: backed ✓, contradicting ✓, incomplete ✓,
temporally ambiguous ✓, insufficient sources ✓, unit differences ✓,
cannot-conclude ✓.

## Metrics (definitions, not marketing)

- **stance_accuracy** — fraction of cases where observed stance equals
  expected. Measures end-to-end behavior only; says nothing about *why*.
- **domain_accuracy** — extraction routed to the intended domain.
- **retrieval_hit** — ≥N evidences retrieved when the case requires it.
  Measures plumbing + source reachability, not relevance.
- **verdict_recall** — expected comparison verdict present. Measures the
  verification core on the happy path.
- **uncertainty recall/precision (coded)** — required uncertainty codes
  present (`unc+`), forbidden ones absent (`unc-`). Measures honesty
  instrumentation, the closest proxy to "knows what it doesn't know".
- **traceability** — every comparison id cited by the conclusion resolves
  and points to a retrieved evidence. Must always be 1.0; anything less
  is a system bug, not a quality gradient.

What these metrics do NOT prove: real-world accuracy (10 hand-picked
cases), robustness to adversarial phrasing, source correctness, or full-mode
(model-assisted) behavior — the eval runs degraded mode only.

## Last results

2026-10-31, live sources, degraded mode: **10/10 passed, stance 1.00**.
Result JSON preserved under `eval/results/`.

## Live-source caveat

World Bank revises historical values; OpenAlex rankings shift. Cases pin
stances with wide margins (e.g. 500M vs ~126M; 2% tolerance vs 1.6%
observed Δ). If a revision flips a case, the failure is informative, not
a regression per se — record the new source value in the result JSON.

## Test suites (pytest)

- `tests/unit` — 25 tests, offline: units, comparison, stance rules,
  verification edge cases. The offline gate.
- `tests/e2e` — 3 tests, offline: scripted LLM + stub connectors;
  asserts ≤2 model calls, model/code actor split, conclusion traceability.
- `tests/integration -m live` — 3 tests against real APIs (not in the
  offline gate; fail on network/source breakage, honestly labeled).
