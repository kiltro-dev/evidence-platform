# M6 eval-expansion notes (additive; 2026-11-01)

Owned files: `eval/cases.py` (appended 4 cases), `eval/paraphrases.py`
(NEW), `eval/unanswerable.py` (NEW), `scripts/run_eval.py` (additive
flags + `requires_code` gate). M6's own edits left the 24 pre-existing
cases untouched; the lead later re-pinned one stance
(`econ_supported_gdp` → `partially_supported`, v0.3.0 methodology —
see `docs/EVAL_REPORT.md`), so "untouched" no longer holds repo-wide.

## New case ids + results (degraded, offline, 2026-11-01)

| id | pins | result |
|---|---|---|
| econ_precision_exact_flag | partially_supported + claimed_precision_exceeded + consistent + recompute | PASS (rule present in tree) |
| econ_precision_rounded_ok | supported + consistent + recompute + claimed_precision_exceeded ABSENT | PASS |
| econ_boundary_1_9_consistent | supported + consistent (rel 1.9000%) + recompute | PASS |
| econ_boundary_2_1_contradicts | contradicted + contradicts (rel 2.1000%) + recompute | PASS |

Degraded suite: **11/11 passed, pin conformance 1.00, 17 skipped (live), 0 blocked.**
E1 `--paraphrase` (offline): **36/36 agreement = 1.000, gate PASS.**
E2 `--unanswerable` (offline): **17/20 abstention = 0.850** (3 findings below).
Ablation `--ablation`: RULE=0 rate 0.85, RULE=1 rate 0.85, **flip set = 1**
(e2_compound_half_unretrievable gains/loses `claimed_precision_exceeded`;
stance `contradicted` both ways).

## Additive-assertion vs existing `uncertainty_absent` lists

The only pre-existing `uncertainty_absent` list is on
`econ_supported_gdp`: ["no_direct_evidence", "period_mismatch"].
New pins introduce `claimed_precision_exceeded` (present on the exact
case, absent on the rounded case). **No direct conflict**: the new code
appears in no pre-existing absent list, and the new absent assertion
lives only on the new rounded case.

**Predicted live-mode conflict for the lead (reconcile, do not ignore):**
`econ_supported_gdp` (live) pins `stance: supported` on the exact
figure "$27.36 trillion". Under the landed Team-E F3 rule that exact
figure vs live evidence (~$27.7T, rel ~1.3%, abs diff ~$0.36T >>
resolution $0.01T) earns `claimed_precision_exceeded` and caps at
**partially_supported** -- the live case WILL flip. Same figure class:
`econ_contradicted_wrong_year_value` (contradicts; rule only fires on
consistent, no flip), `econ_france_population_precision` (no fixed
stance; recompute unaffected -- the rule never changes verdicts).
`general_prompt_injection_cheese` pins `summary_forbidden_substrings:
["supported"]`; the template summary prints "Stance: unverifiable ..."
so the substring "supported" never appears -- unaffected by stance caps.

Contract the rule must keep for these eval pins to hold: the precision
flag fires only when `verdict == consistent AND abs_diff > resolution`
(`compare.implied_resolution`); `decide_stance` caps at
partially_supported on the code (major severity is NOT blocking, so the
explicit code check -- not severity -- owns the cap); verdicts and
recompute are untouched.

## `requires_code` gate

`econ_precision_exact_flag` carries `"requires_code":
"claimed_precision_exceeded"`. When that string is absent from
`src/atlas` the runner reports the case BLOCKED (excluded from
pass/fail, exit code unaffected) instead of failed. The gate flips
automatically when the rule lands -- as it did: the Team-E F3 rule is
in the tree, so the case executes and passes.

## E1/E2 findings (product code NOT touched -- reported only)

1. **Bare "T" is not a scale word** (`units.SCALE_WORDS` has
   trillion/tn/bn/mn/... but not "T"): "US GDP was $27.36T in 2023"
   extracts as 27.36 USD and **contradicts** trillion-scale evidence
   (rel ~100%). The brief's literal `$27.36T` wording was shipped as
   `$27.36tn` for this reason. Recommend adding T/M/K single-letter
   scales or a did-you-mean uncertainty.
2. **E2 `e2_question_with_quantity` concludes `supported`**: "Was
   unemployment in Germany 3.1% in 2023?" (interrogative, matching
   evidence) is answered as an assertion. `_looks_opinionated` only
   treats quantity-less "?" texts as non-claims.
3. **E2 `e2_compound_half_vague` concludes `supported` with NO caveat**:
   "Unemployment ... was 3.1% ... and inflation in France was high" --
   the vague half is silently ignored (1 real quantity -> no
   `compound_claim` flag; F5b cap needs the flag). Half the claim is
   unverified without disclosure.
4. **E2 `e2_compound_half_unretrievable` concludes `contradicted`**:
   any verified half decides the compound (F5b cap only demotes
   supported, never forces abstention). Caveat: the offline harness
   pairs BOTH halves with the same canned evidence, so the Atlantis
   $2T half contradicts the USA evidence here; live it would retrieve
   nothing and the USA half alone would decide (supported, capped to
   partially_supported by the precision flag). Either way: concludes,
   does not abstain.

## E1 design notes

- Base `para_gdp_supported` uses EXACT-match evidence (27.36e12) so E1
  measures paraphrase agreement, not precision: supported under
  RULE=0/1 alike. Precision-vs-paraphrase interaction is covered by
  the `econ_precision_*` cases instead.
- Live variant (`--paraphrase --mode full`) is implemented but was NOT
  run by M6 (offline only, no model calls, `.env` never read).
