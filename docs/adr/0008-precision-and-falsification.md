# ADR-0008: Claimed-precision rule + adversarial falsification pass

- Status: accepted. Date: 2026-11-01 (v0.3.0, Operation First Place).

## Context

Two independent audits converged on the same weakness from opposite
directions. The methodology review (Team E) showed the 2% relative
tolerance lets false precision through: "$27.36 trillion" vs a retrieved
$27.81T (Δ1.62%, $451B) read `supported`, even though the stated digits
are wrong by 45× their implied resolution — and the UI glossed it as
"The evidence agrees with the claim as stated." The red team (Team G)
showed the model side was decorative: structuring + summary rewrites
are thin "use of Meta AI tooling" against the 25% criterion.

## Decision

1. **Claimed-precision rule** (`core/compare.py::implied_resolution`,
   enforced in `verify_quantitative`, capped in `decide_stance`): parse
   the resolution the claim's own digits declare (decimals × scale word,
   ES decimal commas, long-scale "billones"); a would-be-consistent
   verdict whose absolute gap exceeds that resolution emits
   `claimed_precision_exceeded` (major) and caps the stance at
   `partially_supported`. Gated by `ATLAS_PRECISION_RULE` (default on)
   so the eval can ablate it. Rejected alternative: per-indicator
   tolerance floors — more knobs, same blind spot for stated digits.
2. **Falsification pass** (`pipeline/falsify.py`, new audited stage
   between verify and synthesize): the model attacks the draft through
   a falsify-only schema; code validates target ids/enums and surfaces
   attacks as doubt (comparisons + uncertainties + audit). ADVISORY
   ONLY: an earlier revision let fatal+high attacks downgrade decisive
   drafts via a code-owned table, but full eval run 1
   (`eval/results/20261101T013300Z.json`, gitignored) fired 5 such
   overrides and all 5 were spurious on inspection (note-phrasing
   nitpicks; disclosed uncertainties weaponized against robust-margin
   verdicts), as were 2 more reproduced live with captured attack text —
   so the override was removed the same day. Letting model severity
   labels move stances launders model judgment through code. The
   harness records per-case attacks/fatal_high in `observed.falsify`,
   so the advisory yield stays auditable.
   Rejected alternatives: second-model ensembles (no tie-break beats
   the stance table; new judging-day fragility), model-owned retrieval
   (violates ADR-0006 grounding policy).
3. Supporting changes: absolute-materiality disclosure, methodological
   uncertainty codes, compound under-verification cap, summary-strength
   validation, verbatim-span recovery (so the precision rule also binds
   in full mode), abstract-stance science assessment (non-verifying),
   parallel retrieval, per-stage usage accounting.

## Consequences

- `econ_supported_gdp` now pins `partially_supported` by methodology;
  the README/API/EVAL surfaces were updated to the observed behavior.
- Up to 4 model calls per analysis (3 on non-science paths); usage
  persists per stage in `meta.usage_*`.
- UI never renders bare "Supported"; the supported gloss is "Consistent
  with evidence (within 2% relative)".
- Known residual: spans the parser cannot match keep `raw_text=""`
  and skip the precision rule (documented, tested); falsifier
  false-alarm rate is measured per eval run, not yet bounded by a gate.
