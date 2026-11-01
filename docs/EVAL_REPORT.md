# Eval report — v3 dataset (2026-11-01)

Runner: `PYTHONPATH=src .venv/bin/python scripts/run_eval.py`.
Dataset: 28 cases in `eval/cases.py` (10 original + 14 v2 + 4 v3) plus
two offline probes (`--paraphrase`: 36 inputs; `--unanswerable`: 20
variants). Result JSONs: `eval/results/` (gitignored; timestamped).

"N/N" below is a green regression suite, not a benchmark score:
hand-picked cases with wide margins, single-author expectations.
What the numbers do and do not prove is restated at the end.

## Degraded run (offline) — 11/11

```text
[degraded] 11/11 cases passed (stance accuracy 1.00, skipped 17, blocked 0)
```

The 11 executed cases cover the v2 honesty machinery (geo abstention,
opinion refusal, injection resistance, provider death, malformed-model
fallbacks, input boundaries) plus the 4 new v3 scripted pins:
exact-figure precision flag, rounded-figure no-flag, and the 1.9% /
2.1% tolerance-boundary synthetics. The 17 `live` cases skip without
network, as designed.

## Probes (offline)

- `--paraphrase`: **36/36 agreement = 1.000, gate PASS.** 6 base claims
  × (base + 5 paraphrases: unit rewordings, EN/ES incl. decimal commas,
  scale words), scripted evidence, `llm=None`. Any split would be a
  determinism/extraction bug; none found.
- `--unanswerable`: **17/20 abstention = 0.850** (offline). Three
  non-abstentions, all dispositioned, none a false verdict:
  1. `e2_question_with_quantity` ("Was unemployment in Germany 3.1% in
     2023?") → `supported`. Design decision, not a bug: a polar
     question with a verifiable proposition gets answered.
  2. `e2_compound_half_vague` → `supported` on the verifiable half with
     no caveat. Known compound-detection gap (documented; compound
     splitting stays detect-and-disclose per PRD §2).
  3. `e2_compound_half_unretrievable` → `contradicted` via the
     retrievable half. Harness artifact (both halves get canned
     evidence); live behavior would still conclude via the USA half.
- `--ablation` (ATLAS_PRECISION_RULE=0/1): flip set = 1
  (`e2_compound_half_unretrievable` gains/loses
  `claimed_precision_exceeded`; stance unchanged). The rule binds where
  designed and nowhere else.

## Full run 1 (`--mode full`, pre-fix code) — 22/28

```text
[full] 22/28 cases passed (stance accuracy 0.79, skipped 0, blocked 0)
  [FAIL] econ_supported_gdp (stance: partially_supported — EXPECTED, see §v3 methodology)
  [FAIL] econ_supported_unemployment (falsify override — spurious)
  [FAIL] econ_contradicted_wrong_year_value (falsify override — spurious)
  [FAIL] econ_precision_rounded_ok (falsify override — spurious)
  [FAIL] econ_boundary_1_9_consistent (falsify override — spurious)
  [FAIL] econ_boundary_2_1_contradicts (falsify override — spurious)
```

Result JSON: `eval/results/20261101T013300Z.json` (observed values
preserved). This run used the original falsify design (fatal+high
attacks downgrade decisive drafts) at default reasoning depth.

Failure analysis (read the attacks, not just the count):

1. **GDP: the designed methodology flip.** Observed
   `partially_supported` + `claimed_precision_exceeded` (+ materiality
   and methodological codes), verdicts `consistent`, traceability 1.0.
   Live evidence $27.811T (lastupdated 2026-10-08) vs claimed $27.36T:
   Δ1.62% within tolerance, stated digits ($10B resolution) wrong by
   $451B. The case pin was updated to `partially_supported` with this
   rationale (Team-E F1 predicted exactly this flip a priori).
2. **The other five: falsifier false alarms, all spurious on
   inspection.** Every one shows `falsification_flag` + an
   `inconclusive` attack comparison downgrading a correct draft:
   - Unemployment (supported→partially): attacks targeted note
     phrasing ("overlapping periods") and weaponized the disclosed
     `indicator_definition_assumed` minor into a fatal
     verdict-invalidator — a heckler's-veto pattern (that code fires
     on every World Bank comparison) that would have kept downgrading
     supported verdicts indefinitely.
   - 1990 GDP (contradicted→partially, 78% margin): a fatal attack
     cited the disclosed `price_basis_unchecked` major as invalidating
     the contradiction — without margin analysis (price basis explains
     ~2×, not ~5×).
   - The three scripted numeric cases: core verdicts correct
     (`consistent`/`contradicts` as engineered); overrides only.
3. **Truncation evidence.** Several falsify/science calls returned
   empty content at default depth (3998 reasoning tokens observed on
   one call) and degraded safely to skips — correct fallback behavior,
   but the flagship stage was half-dead. Fix: `reasoning_effort="low"`
   on falsify/science (verified live: real attacks, ~2k tokens).
4. **Validator working as designed.** Three model summaries missing
   the measured Δ% and one missing the stance token were discarded for
   the template (logged); no invented `[cmp_*]` survived
   (prose_traceability held wherever model summaries were kept).

Decision (same day, ADR-0008): **falsify is advisory only — it never
moves stances.** Letting model severity labels move stances launders
model judgment through code. Attacks still surface as doubt
(comparisons + uncertainties + audit + yield counts); the stance stays
decided by verified comparisons alone. The override tests were
rewritten to pin the advisory contract.

Provenance note (red-team S2): run 1 predates per-case attack
recording, so the "5 spurious overrides" above is established by: the
run-1 JSON (5 stance FAILs beyond the GDP design flip, each carrying
`falsification_flag` + an `inconclusive` attack comparison), the two
live reproductions with captured attack text (unemployment,
wrong-year GDP — both fatal+high, both spurious on the merits argued
above), and the code path (the override was the only writer of those
downgrades). Since run 3 the harness records per-case
`observed.falsify = {attacks, fatal_high}` plus `observed.numeric`
(claim/evidence values, abs/rel gaps) and per-quantity `resolution`,
so both the advisory yield and the methodology arithmetic are
auditable from the artifact.

## Full run 2 (`--mode full`, final code) — 28/28

```text
[full] 28/28 cases passed (stance accuracy 1.00, skipped 0, blocked 0)
```

Result JSON: `eval/results/20261101T015005Z.json` (observed values
preserved). Code: advisory-only falsify, `reasoning_effort="low"` on
falsify/science, GDP re-pinned to `partially_supported`. Cost: up to 4
model calls per live case; per-stage usage in each report's
`meta.usage_*`.

Resilience observed (fallbacks firing under real faults, all cases
still green):

- 2 structure calls failed live (1 truncation, 1 malformed JSON) →
  heuristic fallback; both cases passed
  (`econ_compound_gdp_unemployment`, `econ_precision_rounded_ok`).
- 1 model summary rejected by strength validation (stance token
  missing) → template; case passed
  (`econ_france_population_precision`).
- Falsify surfaced fatal/serious attacks on 15/28 cases with zero
  stance interference (advisory by construction). Attack quality is
  ungraded — yield counted, correctness not claimed.
- No abstract-stance truncations at low effort (run 1 had 2+1 malformed
  at default depth).

v2→v3 reconciliation summary: one pin updated for a designed
methodology change (GDP → `partially_supported`, predicted a priori);
one feature demoted by measurement (falsify override removed after 5
spurious fired overrides + 2 live reproductions). No pin was edited to
match observed model variance.

## Full run 3 (`--mode full`, final code + red-team fixes) — 28/28

```text
[full] 28/28 cases passed (stance accuracy 1.00, skipped 0, blocked 0)
```

Result JSON: `eval/results/20261101T021039Z.json`. Deltas vs run 2:
single-letter scale guard, JPY dropped from materiality, extended
price-basis lexicon, `precision_unchecked` disclosure, overturn removed
from the falsify schema, `requires_code` fail-closed, and recorded
`observed` artifacts. All 28 green with zero pin changes — the fixes
altered disclosure and parsing, not verdicts.

Artifact check (red-team S2/M1 closure) — GDP case, from the JSON:

- `quantities[0]`: raw "27.36 trillion", value 2.736e13, resolution
  1e10 ($10B).
- `numeric[0]`: verdict `consistent`, evidence 27811517M,
  abs_diff 451.517e9 ($451.5B), rel 1.62%.
- `falsify`: attacks 2, fatal_high 2 — surfaced, stance unaffected
  (advisory by construction).

The demo's star arithmetic ($451B gap vs $10B resolution) and the
advisory yield are now auditable from the artifact, not just
derivable. This run is the pinned v0.3.0 evidence.

## v3 methodology note (what changed since v2)

- Claimed-precision rule: stated digits must survive contact with the
  retrieved value, else `claimed_precision_exceeded` (major) caps the
  stance at `partially_supported` (env-ablatable).
- Absolute-materiality disclosure (`material_absolute_gap`, minor) and
  methodological codes (`indicator_definition_assumed`,
  `price_basis_unchecked`, `source_revision_risk`) on World Bank
  comparisons; verdicts never move on these alone. Materiality
  thresholds are order-of-magnitude disclosure guides (USD/EUR/GBP
  $100B, count 1M, 0.5pp above value 25); JPY deliberately unlisted.
- Missing-span disclosure: when the precision rule cannot bind for
  lack of a usable numeric span, `precision_unchecked` (minor) says
  so instead of skipping silently.
- Scale parsing: single-letter scales attach only (`$27m`, `$27b`);
  spaced singles are units (`100 m` = meters, SI convention).
- Compound under-verification cap in `decide_stance`.
- Summary-strength validation (forbidden lexicon, verbatim stance
  token, measured Δ% for numeric verdicts) with template fallback.
- Science abstracts: one bounded model call over top-3 abstracts,
  non-verifying by construction (ceiling `partially_supported` kept).
- UI never renders bare "Supported" ("Consistent with evidence
  (within 2% relative)").
- Cost: up to 4 model calls per live case (structure + abstracts +
  falsify + summary; 3 on non-science paths); per-stage usage in
  `meta.usage_*`.

## Mock-vs-live distinction (read this before citing numbers)

- **Live cases (17)** exercise the real pipeline against real sources
  (real HTTP, real values, real revision risk) and, in `--mode full`,
  the real model. The only cases that catch source drift; failures
  may be "the world changed", not "the code broke".
- **`both` cases (2)** run the real pipeline but are offline-safe by
  construction (geo resolution fails pre-network). Fully honest.
- **Scripted cases (9, incl. subcases)** stub connectors and/or the
  model client. They prove *code paths*, not source behavior. In full
  mode the runner LLM applies where no client spec pins a double, so
  scripted numeric cases also probe real-model extraction.

## Full-mode live spot checks (verified, v3)

| Claim | Observed |
|---|---|
| EN GDP `$27.36 trillion in 2023` | `partially_supported` + precision flag |
| EN unemployment `3.1% in 2023` | `supported`, falsify 2 attacks / 0 fatal-high |
| ES `El desempleo en Alemania fue del 3,1% en 2023` | `supported` |
| Opinion `Democracy is the best form of government.` | `not_assessable` |

## Failure modes to watch in full runs

1. **Source revision** (informative): a value crosses a pinned stance
   boundary. Record the new value; widen the margin or convert to a
   property check. Note the GDP pin is methodology-based, not
   margin-based: a revision closing the absolute gap legitimately
   flips it back to `supported`.
2. **Source outage**: live cases abstain with labeled errors; non-set
   cases FAIL loudly — the honest signal. Transient gets one 2s retry.
3. **Model truncation**: empty content raises inside `MetaClient` and
   triggers deterministic fallbacks (incl. skipped falsify/science).
4. **Ranking shift** (OpenAlex): science year-match margins are wide
   but re-check the retrieved set on failure, not the verify logic.
5. **Model extraction variance**: scripted numeric cases assume exact
   extraction; a model rounding the claim value breaks engineered
   ratios. Triage via `observed` before touching code.

Triage rule: re-run the single case, read `observed` in the result
JSON, and classify into one of the five before touching code.

## Limits of this eval

- 28 hand-picked cases + 56 probe inputs; not a benchmark, not a
  sample of real traffic. No accuracy generalization is licensed.
- Degraded runs say nothing about model-assisted behavior (except
  scripted fault-injection paths, which use doubles).
- No retrieval-relevance scoring beyond indicator/year matching;
  abstract-stance judgments are ungraded (yield counted, quality not).
- Falsifier false-alarm rate is measured per run (run 1: 5 fired
  overrides, all spurious on inspection — the finding that killed the
  override; `observed.falsify` records yield since run 3), not bounded
  by a gate. Attack quality remains ungraded.
- No adversarial phrasing beyond the two pinned cases; no timing/SLO
  assertions (`elapsed_ms` recorded, not gated).
- Live results expire: source values drift. The result JSON (with
  observed values) is the durable artifact, not the PASS count.

## v2 history (condensed)

v2 closed at 24/24 (stance 1.00) after honest reconciliation:
not_assessable narrowing (real bug fix), `expected_full` overlays for
legitimate model variance (no stance/traceability weakened), transient
retry after an observed OpenAlex outage. Prior detail preserved in git
history.
