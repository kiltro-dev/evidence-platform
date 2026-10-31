# ADR-0003: Model strategy — Muse Spark for language, code for truth

- Status: accepted. Date: 2026-10-31.

## Context

Hackathon criterion "Use of Meta AI Tooling" (25%) rewards real Meta
model integration. Budget: ~$150 Meta credits. No API key is provisioned
in this environment (verified 2026-10-31).

## Options

1. Model does everything (structure + retrieve-judge + compare + summarize).
2. Model for language tasks only (structure, summary); code verifies.
3. No model at all (pure heuristics + templates).

## Decision

Option 2. `muse-spark-1.3` via Meta Model API (OpenAI-compatible SDK)
handles claim structuring and summary wording. Retrieval parsing,
unit math, comparisons, and stance rules are code-only. A heuristic
extractor + template summary provide a fully working degraded mode
when no key is configured; the audit trail always records the actor.

## Rationale

- Deterministic checks are more reliable in code and cannot be skipped
  by the model (import-level separation).
- Two narrow JSON contracts minimize tokens, latency, and cost per
  analysis; no agentic turn loops.
- Degraded mode keeps the product demonstrable and testable without
  credentials; full mode activates by setting `MODEL_API_KEY`.

## Consequences

- Full mode is implemented but, without a provisioned key, exercised
  here only through scripted doubles. The verification core is identical
  in both modes and verified live.
- Token/cost accounting per analysis is future work (no usage API read).
