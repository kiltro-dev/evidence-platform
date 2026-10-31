# ADR-0002: Staged pipeline instead of a multi-agent loop

- Status: accepted. Date: 2026-10-31.

## Context

The mission lists candidate agent roles (claim researcher, extractor,
verifier, adversarial reviewer, synthesizer) but forbids forcing
multi-agent architecture without need.

## Options

1. Fixed six-stage pipeline with roles as modules.
2. Tool-calling agent loop (planner + tools, turn budget).
3. Hybrid: loop for retrieval, fixed stages for verification.

## Decision

Option 1. `extract → route → retrieve → verify → synthesize → persist`,
each stage one module with a fixed schema. At most 2 model calls per
analysis (structure + summary), asserted in e2e tests.

## Rationale

- The product promise is *deterministic, auditable verification*. A loop
  adds runaway, skipped-check, and prompt-injection failure modes with no
  accuracy gain at this scope.
- Fixed stages make the model/code boundary enforceable by imports:
  `pipeline/verify.py` has no model dependency at all.
- Simpler to test hermetically (scripted model + stub connectors).

## Consequences

- No iterative re-querying ("retrieval didn't satisfy, try again").
  Queries are single-shot per connector; broadening recall is future work.
- No adversarial reviewer role; its job is approximated by strict
  comparability rules that refuse to compare instead of guessing.
