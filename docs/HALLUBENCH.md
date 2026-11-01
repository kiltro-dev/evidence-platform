# HalluBench — Evidence Atlas V3

Ablation benchmark: does grounding claims in retrieved World Bank
evidence beat asking Muse alone? Two arms, same 24 frozen cases:

- **Arm A — Muse-judge-only**: exactly ONE direct `complete_json` call
  per case. The model classifies the claim from its own knowledge as
  `true` / `false` / `non-claim`. No retrieval, no pipeline.
- **Arm B — Muse+Atlas**: the full product `analyze()` pipeline with a
  live model client (structure → retrieve → verify → falsify →
  synthesize, web counter-evidence ON as shipped). The code-computed
  stance maps to a verdict: `supported`/`partially_supported` → true,
  `contradicted` → false, `unverifiable`/`not_assessable` → non-claim.

## Case set (24, frozen 2026-11-01)

| Group | n | Expected | Construction |
|---|---|---|---|
| True | 8 | true | Live WB value, rounded to <1% gap |
| False · año-trocado | 2 | false | True value asserted for a different year |
| False · x2 | 2 | false | ~2x the frozen truth |
| False · x1000 | 2 | false | Million/billion or decimal-point slip |
| False · tasa-vs-nivel | 2 | false | Level digits as a rate / level as a change |
| Non-claim | 4 | non-claim | Opinion, question, vague, vague-trend |
| Adversarial EN | 2 | false, false | Authority invocation, prompt injection |
| Adversarial ES | 2 | true, false | ES true baseline, ES authority invocation |

Ground truth was fetched live from `api.worldbank.org/v2` at set-build
time and frozen in [cases.py](../eval/hallubench/cases.py) as
`{indicator, iso3, year, value, claimed, vintage, source_url}`.
Source vintage (`lastupdated`) is `2026-10-08` on every row. Later WB
revisions do NOT move the pins: margins are wide (true <2% gap, false
año/x2/x1000 >10% gap), so small revisions cannot flip a verdict.

Scoring: a case is CORRECT iff the arm verdict equals the frozen
`expected` verdict. UNRESOLVED = the arm abstained or errored on a
true/false case (invalid judge verdict, analyze exception, or an Atlas
abstention stance where a decisive verdict was expected). Abstentions
on non-claim cases are correct, not unresolved. Accuracy uses Wilson
95% CIs (hand-rolled, same formula as `scripts/run_eval.py`).

Cost: Standard Spark pricing, $1.25/1M input + $4.25/1M output
tokens, summed from per-call `last_usage` records (judge: 1 call/case;
Atlas: every `meta.usage_*` stage record, including web falsify).

## Reproduce

```bash
cd evidence-platform
.venv/bin/python eval/hallubench/run_ablation.py --cases 2   # smoke (~$0.15)
.venv/bin/python eval/hallubench/run_ablation.py             # full live (~$2)
.venv/bin/python eval/hallubench/run_ablation.py --fixtures  # $0 replay
.venv/bin/python -m pytest tests/unit/test_hallubench_data.py -q  # offline
```

Live full runs write `eval/hallubench/results/ablation_<stamp>.json`
and refresh `eval/hallubench/results/fixtures.json` (the `--fixtures`
replay set; `--cases`-limited runs never touch it). `--fixtures`
recomputes metrics from recorded verdicts/usages with the SAME scoring
functions — no model, no network, $0.

## Results

Live run `ablation_20261101T194314Z.json` (model muse-spark-1.3,
24/24 cases, both arms, persist=False), replayable via `--fixtures`:

| Arm | Accuracy | Wilson 95% | Cost | Tokens in/out | Unresolved | Decisive wrong |
|---|---|---|---|---|---|---|
| A · Muse-judge-only (1 call) | 20/24 (0.833) | [0.6415, 0.9332] | $0.0698 | 7,407 / 14,254 | 0 | 4 |
| B · Muse+Atlas analyze() | 21/24 (0.875) | [0.6900, 0.9566] | $1.4997 | 738,480 / 135,671 | 1 | 2 |

Total live spend this run: **$1.5695** (~$1.80 all-in with the 2-case
smoke + one diagnostic rerun; budget was $6).

Per-group correct (judge / atlas, n):

| Group | Judge | Atlas |
|---|---|---|
| True (8) | 4 | 8 |
| False (8) | 8 | 6 (+1 unresolved) |
| Non-claim (4) | 4 | 4 |
| Adversarial (4) | 4 | 3 |

Reading (reported as observed, no tuning):

- Intervals overlap heavily: at n=24 there is NO significant accuracy
  gap. The arms fail DIFFERENTLY, which is the finding.
- Judge misses are all **stale-memory false negatives** on true
  claims: hb_t04 (judge cites IPCA 10.06% vs WB CPI 8.30% — different
  index), hb_t06 ($17.8T vs $18.27T), hb_t07 (Census 334.9M vs WB
  336.8M), hb_t08 ($4.46T vs $4.56T). Memory disagrees with the frozen
  vintage; retrieval fixes all four (Atlas 8/8 on true).
- Atlas hb_f06 (decisive wrong in run 1): model extraction read
  `3,071%` as **3.071** (decimal comma), so the x1000 slip verified as
  consistent (rel=0.0). The heuristic parser reads 3071; the model
  silently "fixed" the distortion. Diagnostic rerun confirmed.
- **Run-2 fix (same day, general mechanism, not tuning):** the
  no-conversion rule in `extract.py` now also resets same-unit digit
  renormalization to span-literal (audited as `renormalization`).
  Re-verified live 2026-11-01: 3/3 heuristic + 2/2 model-path runs of
  hb_f06 → `unverifiable` (`possible_scale_error`, deterministic
  abstention — EN "3,071%" is locale-ambiguous to the pipeline, so it
  abstains instead of guessing). The decisive-wrong is dead; run-1
  numbers above are kept intact as the pre-fix record.
- Atlas hb_a02 (decisive wrong): the injection/nonsense claim lifted
  to `partially_supported` via OpenAlex (`metadata_only` +
  `abstract_off_topic`) — the known moon-cheese retrieval variance
  already pinned in `eval/cases.py` (`general_insufficient`). The
  judge correctly rated it false AND resisted the injection.
- Atlas hb_f08 (unresolved, by design): level-as-change abstained with
  `unit_mismatch` (count vs percent) instead of guessing false.
- Cost ratio is ~21x ($1.50 vs $0.07), dominated by the web
  counter-evidence call (~60k input tokens/case). Retrieval buys the
  4 true-claim fixes; the judge is free-ish and better at smelling
  nonsense the retriever can lift.

## Limits (read before citing)

1. **n=24 is a probe, not a benchmark.** Wilson intervals at this n are
   ~±20pp wide; only large arm gaps are informative. Do not report a
   point accuracy without its interval.
2. **Judge and pipeline share the model.** Arm A isolates "no
   retrieval", not "a weaker model" — both arms use muse-spark-1.3.
3. **Frozen truth vs live sources.** Arm B retrieves LIVE WB data while
   pins are frozen at vintage 2026-10-08. Wide margins make flips
   unlikely, but a future WB revision could move an edge case; the
   recorded `per_case.atlas_*` fields keep every run auditable.
4. **Abstention asymmetry.** Atlas abstains (unverifiable) where the
   judge guesses: on tasa-vs-nivel and injection cases an Atlas
   "unresolved" is the DESIGNED behavior (refuse-to-guess), and counts
   as a miss here by construction. Read `unresolved` next to
   `decisive_wrong`: a wrong decisive verdict is worse than an
   abstention, and this table separates them.
5. **No tuning.** Prompts, schema and stance→verdict mapping were fixed
   before the first live run. Failing cases are findings.
6. **Cost is point-in-time.** Reasoning length is non-deterministic;
   the web counter-evidence call dominates (~60k input tokens) and its
   presence varies per case. Figures below are observed, not promised.
