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

---

# EVS slate #1-14 notes (build agent V2, additive; 2026-11-01)

Owned files: `eval/cases.py` (appended 14 live cases; the 28 existing
cases untouched), this file (this section). No commits. `src/`, `tests/`,
`scripts/`, `docs/` otherwise untouched. `--mode full` NOT run (lead
runs the full gate at cycle end on the merged tree).

Caveat: the tree was already dirty when this slate was built
(`src/atlas/pipeline/run.py`, both connectors, two unit tests, two
untracked docs from other agents). Probes below ran against that
working tree; if the merged tree changes retrieval/verify behavior,
re-check the #5/#14 dispositions first.

## New case ids + pins

| # | id | stance pin | key checks |
|---|---|---|---|
| 1 | econ_future_gdp_2030 | unverifiable | no_direct_evidence + abstention + min_q 1 |
| 2 | econ_taiwan_no_data | unverifiable | no_direct_evidence + abstention + min_q 1 |
| 3 | econ_gdp_per_capita_norway | contradicted | contradicts + min_ev 1 |
| 4 | econ_exports_germany | contradicted | contradicts + min_ev 1 |
| 5 | econ_debt_italy | unverifiable (DISPOSITION, was: contradicted) | no_direct_evidence + abstention + min_q 1 |
| 6 | econ_internet_usa | contradicted | contradicts + min_ev 1, NO domain pin |
| 7 | econ_fertility_nigeria | contradicted | contradicts + min_ev 1 + unit_assumed + full overlay |
| 8 | econ_comparative_abstains | unverifiable | no_measurable_quantity |
| 9 | econ_pp_vs_percent | unverifiable | unit_mismatch + abstention |
| 10 | econ_es_unemployment_live | supported | consistent + min_ev 1 |
| 11 | econ_es_inflation_decimal | supported | consistent + min_ev 1 |
| 12 | econ_es_billones | supported | consistent + recompute + precision-flag ABSENT |
| 13 | econ_price_basis_stated | supported | consistent + recompute + both flags ABSENT |
| 14 | econ_compound_conflict_cap | partially_supported | consistent + min_ev 1 + min_q 2 + partial_coverage + unit_mismatch |

All 14 carry explicit `"mode": "live"` and a `rationale`; no new
imports (pure-data append). Texts are byte-identical to the brief.

## Source values verified (direct WB reads, 0 model calls, 2026-11-01)

| check | rows | value |
|---|---|---|
| USA GDP 2030 (future) | 0 | empty (nulls filtered) |
| TWN GDP 2023 (non-member) | 0 | empty |
| NOR GDPpc 2023 | 1 | 90984.41 (claim $10k -> ~89% gap) |
| DEU exports 2023 | 1 | 42.967% (claim 5% -> ~88% gap) |
| ITA debt 2010:2024 | 0 | NO Italy series (see #5 disposition) |
| DEU debt 2023 | 0 | also empty (indicator gaps, not just Italy) |
| USA debt 2020:2023 | 4 | 2023 = 114.74% (lead alternative for #5) |
| USA internet 2023 | 1 | 93.526% (claim 5% -> ~95% gap) |
| NGA fertility 2020 | 1 | 4.7 (claim 1.2 -> ~74% gap) |
| DEU unemp 2023 | 1 | 3.071 (claim 3.1 -> rel 0.94%) |
| BRA infl 2021 | 1 | 8.3017 (claim 8.3 -> rel 0.02%) |
| USA GDP 2023 | 1 | 27.811517e12 (claim 28e12 -> rel 0.68%) |

Two transient WB timeouts occurred mid-batch; retries + the ITA-GDP
control (green in the same batch) confirm the empties are real gaps,
not outages.

## Probes (7/8 LIVE model calls; ATLAS_WEB_FALSIFY=0 for budget)

Per-analyze accounting with a live MetaClient is extract(1) +
falsify-main(1) + web-pass(1, default ON) + summary(1) = 4; disabling
the web pass makes 3. Web-off probes are representative for every
check pinned here: the web pass is advisory-only by construction
(`inconclusive` verdicts + minor `web_counter_evidence` codes,
`web_counter_evidence` in NON_VERIFYING_METRICS, excluded from the
ceiling allowlist), so it cannot move stances, add consistent/
contradicts verdicts, fire any pinned uncertainty code, or affect
recompute (no rel_diff_pct). No pinned check names a web artifact.

- #2 Taiwan, `analyze(llm=None)` live-network, 0 calls:
  `worldbank: status=ok count=0` -> `no_direct_evidence` (blocking)
  -> `unverifiable`. Domain economy, $800B parsed. Pin HOLDS.
- #12 billones, `analyze(MetaClient)` , 3 calls (extract/falsify/
  summary all completed): model expanded billones to 28e12 USD with
  verbatim raw span `28 billones de dólares` (resolution 1e12 > abs
  gap 1.88e11); rel 0.6777% consistent; `claimed_precision_exceeded`
  ABSENT; stance `supported`. Query `NY.GDP.MKTP.CD USA 2023:2023`.
  Pin HOLDS. (Model summary discarded for missing delta percent;
  template used; stance code-owned. Falsify filed 1 fatal_high
  price-basis nitpick, advisory-only per ADR-0008.)
- #14 compound, `analyze(MetaClient)`, 3 calls: model EXTRACTION
  failed (empty content, finish_reason=length after ~3998 reasoning
  tokens; known Spark blowup) -> heuristic fallback. Longest-match
  `unemployment` (12) beats `gdp` (3) -> `SL.UEM.TOTL.ZS DEU
  2023:2023`; 3.1 vs 3.071 consistent (rel 0.94%); $500T USD ->
  `unit_mismatch` (uncertainty only, no comparison);
  `compound_claim` + 1 verifying < 2 real caps at
  `partially_supported` (F5b); synthesize appends
  `partial_coverage`. Pin HOLDS on the observed path.
- #14 follow-up `structure_claim` peek, 1 call: extraction failed
  AGAIN identically (2/2). The indicator_hint flip question is moot
  in practice -- the model never gets to hint -- but unobserved, not
  disproven (see residual risk below).

Total completed model calls: 7 (usage_seq 6 + 1 peek). No more were
spent: a second peek was judged negative-value (temperature-0 repeat
of a systematic truncation) and #2's model-geo question answers
itself from code (resolve_country hits Taiwan in the raw text before
model geo_refs are ever consulted).

## Prediction-vs-actual dispositions

1. **#5 econ_debt_italy: stance flipped contradicted ->
   unverifiable + no_direct_evidence (text unchanged).** The brief
   predicted a debt contradiction, but GC.DOD.TOTL.GD.ZS has no
   Italy series at all (n=0 across 2010:2024; ITA-GDP control and
   USA-debt green alongside), so retrieval is `[]` and verify
   deterministically yields blocking `no_direct_evidence` (same
   mechanism as the #2 probe, demonstrated end to end). Not forced:
   the pin follows the source. Lead option, if the debt-contradiction
   slot matters more than the Italy text: swapping the geo to the
   United States (114.74% @2023, verified) restores a ~91%-margin
   `contradicted` pin with a one-line text change.
2. **#14 econ_compound_conflict_cap: pins probe-OBSERVED behavior
   (partially_supported + partial_coverage + unit_mismatch), not
   forced.** Both observations agree (1 end-to-end + 1 structure
   peek): extraction falls back, unemployment wins, no GDP flip
   (0/2). Residual risk, stated plainly: if a gate-run extraction
   ever SUCCEEDS with an indicator_hint/subject containing `gross
   domestic product` (the only GDP keyword longer than
   `unemployment`), retrieval flips to GDP and the stance becomes
   `contradicted`. That path is 0/2 observed; the pin reflects
   actuals. If the gate ever shows extraction succeeding on this
   input, convert the stance pin to a `stance_set` of
   {partially_supported, contradicted} (compound precedent) --
   partial_coverage + unit_mismatch hold on both paths, only the
   stance/verdict differ.

## Overlay / no-pin rationale

- #7 carries the population-case trio (`drop_in_full: [domain]` +
  `expected_full: {domain_set, uncertainty_present: []}`): the
  overlay drops requirements, never asserts absence, so it is safe
  whether the model assigns an explicit unit or sanitizes to bare;
  the domain_set tolerates documented model wobble on demographic
  claims (cf. life_expectancy).
- #6 pins NO domain: heuristic routes general, the model's
  prefer-specific rule may say economy, retrieval is identical
  (worldbank serves both domains).
- #12/#13 pin `recompute_verdict` (rounded_ok mirror): unique raw
  pairs, independent rel re-derivation guards the 0.68% verdicts.
- #1 vs E2 `e2_future_gdp_2030`: both abstain but by different
  mechanisms -- live #1 gets empty WB rows (`no_direct_evidence`)
  while the E2 canned-evidence harness yields `period_mismatch`.
  Same honesty, different code path; not a contradiction.

## Offline gates after edits (2026-11-01)

- degraded: **11/11 passed**, pin conformance 1.00, 31 skipped
  (17 old live + 14 new live), 0 blocked -- new cases correctly
  skipped, existing counts unchanged.
- paraphrase: **36/36 agreement = 1.000**, gate PASS.
- unanswerable: **17/20 abstention = 0.850** (same 3 documented
  non-abstentions as M6; see EVAL_REPORT.md).

## Lead dispositions (2026-11-01, cycle 2)

- #5 REDESIGNED pre-freeze: Italy draft probed empty (no
  GC.DOD.TOTL.GD.ZS series 2010:2024), which would duplicate #2's
  no-data mechanism. Geo moved to USA (id econ_debt_usa): 10% vs
  114.74% @2023 (~91% margin) -> contradicted. Case design, not
  pin-chasing: the pin follows the probed source.
- #14 converted to pre-registered stance_set
  {partially_supported, contradicted}: extraction fails 2/2 (heuristic
  path -> partial), but a gate-run extraction success via a GDP hint
  would resolve contradicted; both members keep partial_coverage +
  unit_mismatch. verdict_present dropped (members disagree).
- #15 econ_multigeo_compound_disclosed (new): tripwire for the V1
  multi-geo fix. Germany+France text, DEU-only evidence, 3.1
  consistent + 7.3 contradicts -> contradicted WITH multi_geo_unchecked
  (major). Pre-fix this concluded with zero geo disclosure.

## Lead honesty fixes (2026-11-01, cycle 2)

- F4 `best`-collocation carve-out (verify.py): "best estimate" /
  "best available data" no longer trigger opinion_or_nonclaim; bare
  "best" still does (3 unit tests).
- F6 claim-side period description (verify.py): a claim with no year
  keeps code period_mismatch/major but the receipt now says "The
  claim states no year or period" instead of sounding evidence-side
  (1 unit test; e2_no_year expectation unchanged).
- R5 per-item time_ref sanitation (extract.py): model year "unknown"
  or a bad period_type coerces to None/"none" with audit notes
  instead of nuking the whole model extraction to heuristic; a
  dateless model time_ref no longer blocks heuristic year backfill
  (2 unit tests, both fail pre-fix).
- Span-verbatim splitter coverage reviewed: already enforced
  (verbatim-only + dedup + floor) and tested; no change.

## Full run 5 triage: basis-points model variance -> no-conversion rule (2026-11-01)

- Run 5 (`--mode both`, artifact `20261101T051507Z.json`): full
  **42/43**; the single FAIL is the OLD case
  `econ_unit_mismatch_basis_points` (stance supported, expected
  unverifiable + unit_mismatch). All 15 new cases pass first try,
  incl. #14 (heuristic fallback -> partial, as probed 2/2) and #15
  (contradicted WITH multi_geo_unchecked -- tripwire fires live).
- Root cause (triage category 5, model extraction variance -- NOT a
  tree regression; no diff touches unit parsing): the model
  converted "310 basis points" -> value 3.1 unit percent at
  extraction in the gate run. Single-case probe 3x: 1/3 convert
  (3.1 percent -> supported), 2/3 bare (310.0 None -> unverifiable).
  Temperature-0 reasoning still varies.
- Fix (lead, principled -- not pin-tuning; the pin is byte-identical):
  no-conversion rule in `_parse_llm_quantities`: when the model's
  own verbatim span deterministically parses to exactly one number
  disagreeing with the model value (>1% rel) while the span carries
  no unit and the model assigned one, reset to the span-literal
  parse with an audit note. Spans the heuristic cannot parse (or
  parses ambiguously) are trusted as today; equal values never
  trigger, so scale application and same-value unit inference are
  untouched (fertility overlay safe). Doctrine: code never trusts
  model arithmetic it cannot independently re-derive (H4 spirit).
- Post-fix single-case probe 3x: **3/3 unverifiable** -- bare 310
  (2x) and, newly observed, honest 310 pp (1x); the conversion path
  is code-impossible now. 5 unit tests (2 fail pre-fix).
- Cost note: run 5 + probes this cycle are Meta-billed; usage
  accounting from report `meta.usage_*` where persisted.

## Full run 6 triage: moon-cheese retrieval variance -> stance_set (2026-11-01)

- Run 6 (`--mode both`, artifact `20261101T054849Z.json`, delta vs
  run 5 = no-conversion rule only): full **42/43**. bp case PASSED
  live (fix holds); #14 took the MODEL path yet stayed partial.
- Single FAIL: `general_insufficient` (moon-cheese, pinned
  unverifiable) -> partially_supported via 2 model-judged
  consistent abstract_stance (ceiling lift). Triage cat.4 PROVEN:
  3x probe with work IDs -- runs 1&3 same 5 OpenAlex works ->
  inconclusive -> unverifiable; run 2 different 5 works -> model
  judges relevant+supporting -> lift. Driver = OpenAlex ranking
  instability on a nonsense query x ungraded abstract judgments
  (documented limitation); not verify logic, not the diff.
- Disposition: variance-declared stance_set
  {unverifiable, partially_supported} (#14 precedent); both
  members observed BEFORE the change; load-bearing property kept
  (never supported/contradicted/not_assessable). Code alternative
  (off-topic blocks lift) deferred with rationale in EVAL_REPORT.
- H3 restated: 0 flips on deterministic paths; model-judgment
  flips carry mechanisms + bounds; no suite-wide 0-flip claim.
