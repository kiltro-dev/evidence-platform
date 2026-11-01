# Evaluation

Runner: `scripts/run_eval.py --mode {degraded,full,both}` plus probes
`--paraphrase` and `--unanswerable` (with `--ablation` for the
precision-rule on/off flip set).
Cases: `eval/cases.py` (28 cases). Results: `eval/results/*.json` (gitignored).

```bash
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --mode degraded  # offline: 11 cases
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --mode full      # all 28: needs network + key
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --mode both      # degraded pass + full pass
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --paraphrase     # 36-input stability probe
PYTHONPATH=src .venv/bin/python scripts/run_eval.py --unanswerable   # 20-variant abstention probe
```

Case modes (`mode` key; absent = `live`):

- `live` (17 cases): needs real sources; skipped in degraded runs.
- `scripted` (9 cases): offline harness — stubbed connectors and/or
  scripted/fault-injected model clients. With no client spec the runner LLM
  applies, so the same case probes the template summary in degraded mode
  and the model summary in full mode.
- `both` (2 cases): run everywhere (offline-safe: geo resolution fails
  before any request is sent).

## Cases (v3, 28)

The original 10 (`live`):

| id | claim type | expected stance |
|---|---|---|
| econ_supported_gdp | relative agreement, digits unconfirmed | partially_supported (+ `claimed_precision_exceeded`) |
| econ_supported_unemployment | backed by evidence | supported |
| econ_contradicted_population | contradicting data | contradicted |
| econ_contradicted_wrong_year_value | contradicting data | contradicted |
| econ_unit_mismatch | incompatible units (EUR vs USD) | unverifiable |
| econ_incomplete_no_quantity | no measurable quantity | unverifiable |
| econ_temporally_vague | vague time, no quantity | unverifiable |
| sci_contextual | year-matching works, metadata only | partially_supported |
| sci_no_year_match | works exist, wrong era | unverifiable |
| general_insufficient | no applicable source | unverifiable |

The 14 new v2 cases:

| id | mode | what it pins |
|---|---|---|
| econ_contradicted_life_expectancy | live | contradiction robust to revisions (40y vs ~84y) |
| econ_unknown_geo_atlantis | both | unknown geo → abstains pre-network (`no_direct_evidence`, tolerates `invalid_geo`) |
| econ_unit_mismatch_basis_points | live | unparsed unit never fabricated into a conversion |
| econ_unverifiable_geoless_gdp | both | geo-less macro quantity retrieves nothing |
| econ_compound_gdp_unemployment | live | compound claim keeps 2+ quantities; stance set {contradicted, unverifiable} |
| econ_brazil_inflation_multi_year | live | property check: supported iff both yearly comparisons hold |
| general_opinion_democracy | scripted | pure opinion → `not_assessable` |
| econ_contradicted_unemployment_trend | live | range retrieval (4 yearly rows); 4% vs ~3.8% contradicts |
| econ_france_population_precision | live | false-precision probe: verdicts recomputed independently, no fixed stance |
| econ_adversarial_wb_invocation | live | "World Bank says so" prefix cannot sway code-computed stance |
| general_prompt_injection_cheese | scripted | injected "report supported" ignored; summary must not say "supported" |
| econ_provider_death_connector_error | scripted | all connectors raise → `connector_error`, abstains, never crashes |
| malformed_model_fallbacks | scripted ×4 | minimal/invalid JSON, unit `"null"` string, year-as-quantity |
| boundary_empty_oversize | scripted ×2 | empty → `not_assessable`; ~66KB oversize extracts + abstains |

The 4 new v3 cases (all `scripted`):

| id | what it pins |
|---|---|
| econ_precision_exact_flag | exact "$27.36tn" vs $27.81tn evidence → `partially_supported` + `claimed_precision_exceeded` |
| econ_precision_rounded_ok | rounded "about $28tn", same evidence → `supported`, flag absent |
| econ_boundary_1_9_consistent | engineered 1.9% diff → `consistent` |
| econ_boundary_2_1_contradicts | engineered 2.1% diff → `contradicts` |

Probes (separate harnesses, not stance pins):

| probe | inputs | gate |
|---|---|---|
| `--paraphrase` (`eval/paraphrases.py`) | 6 bases × (base + 5 paraphrases), EN/ES, scale words | 100% stance agreement offline |
| `--unanswerable` (`eval/unanswerable.py`) | 20 unanswerable variants | abstention rate reported (target 1.0); `--ablation` reports the ATLAS_PRECISION_RULE=0/1 flip set |

Coverage vs mission §7: backed ✓, contradicting ✓, incomplete ✓,
temporally ambiguous ✓, insufficient sources ✓, unit differences ✓,
cannot-conclude ✓ — plus opinion refusal, injection resistance, provider
death, malformed-model fallbacks, input boundaries, precision/boundary
pins, paraphrase stability, and abstention probing.

## Metrics (definitions, not marketing)

Kept from v1:

- **pin_conformance** — fraction of cases where observed stance equals
  expected (or stance-set membership / property holds). End-to-end only;
  says nothing about *why*.
- **domain_accuracy** — extraction routed to the intended domain.
- **retrieval_hit** — ≥N evidences retrieved when the case requires it.
  Measures plumbing + source reachability, not relevance.
- **verdict_recall** — expected comparison verdict present (and
  `verdict_absent` checks for forbidden ones). Measures the verification
  core on the happy path.
- **uncertainty recall/precision (coded)** — required uncertainty codes
  present (`unc+`), forbidden ones absent (`unc-`). Measures honesty
  instrumentation, the closest proxy to "knows what it doesn't know".
- **traceability** — every comparison id cited by the conclusion resolves
  and points to a retrieved evidence. Must always be 1.0; anything less
  is a system bug, not a quality gradient.

New in v2:

- **prose_traceability** (automatic) — every `[cmp_*]` token in the
  summary resolves to a real comparison. Catches invented citations.
- **abstention_any_of** (opt-in per case) — at least one listed abstention
  code present. Pins *honest refusal* without over-pinning which code.
- **degradation labeling** (opt-in) — `extraction_actor` /
  `conclusion_actor` / `context_notes_contains` pin which fallback path
  was taken (model vs heuristic vs template).
- **recompute_verdict** (opt-in) — each numeric verdict is re-derived
  independently from `rel_diff_pct` + the comparison's own tolerance rule.
- **supported_iff_both** (opt-in) — stance is `supported` iff ≥2 numeric
  comparisons exist and all are consistent (multi-year property).
- **quantity bounds** — `min_quantities` / `max_quantities` pin extraction
  completeness (compound claims) or emptiness (opinions, injections).

What these metrics do NOT prove: real-world accuracy (28 hand-picked
cases + probes), robustness to arbitrary adversarial phrasing, source
correctness, or — in degraded runs — model-assisted behavior. Live cases
depend on current source values; see the caveat below and
[EVAL_REPORT.md](EVAL_REPORT.md). "N/N" below is a green regression
suite, not a benchmark score.

## Last results

2026-11-01 (v0.3.0), degraded mode, offline: **11/11 passed**
(17 live cases skipped); paraphrase probe 36/36 agreement; abstention
probe 17/20 (3 documented non-abstentions under review — see
[EVAL_REPORT.md](EVAL_REPORT.md)). Full-mode run: see EVAL_REPORT.
Result JSONs preserved under `eval/results/`.

## Live-source caveat

World Bank revises historical values; OpenAlex rankings shift. Cases pin
stances with wide margins (e.g. 500M vs ~126M). If a revision flips a
case, the failure is informative, not a regression per se — record the
new source value in the result JSON. Cases near the tolerance edge
(France population) pin recomputation properties instead of fixed
stances for exactly this reason. Note the GDP case pins
`partially_supported` by methodology (claimed-precision cap), not by
margin: a future revision that closes the absolute gap would
legitimately flip it back to `supported`.

## Test suites (pytest)

- `tests/unit` + `tests/e2e` — 416 tests, offline: units, comparison,
  stance rules (incl. 47-cell `decide_stance` truth-table sweep),
  verification edge cases, i18n, API errors, store, provider/fallback
  behavior, falsification (main + web, mocked), abstract stance, span
  recovery, usage attribution, claim splitting + batch roll-up,
  partial-coverage caveats, scripted LLM + stub connectors (model/code
  actor split, conclusion traceability). The offline gate.
- `tests/integration -m live` — tests against real APIs (not in the
  offline gate; fail on network/source breakage, honestly labeled).
