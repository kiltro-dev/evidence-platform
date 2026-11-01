# ADR-0009: ShipCheck sidecar — multi-claim intake, schema decoding, web falsification

- Status: accepted. Date: 2026-11-01 (v0.4.0, Operation Find the Breakthrough).

## Context

Eight parallel research agents + an independent red team converged: the
v0.3.0 engine (deterministic verification, code-decided stances, receipts)
is the moat, but the single-claim fact-checker skin has no buyer (newsroom
budgets ~$0, free incumbents) and no ambition story for Indie. The two
surviving directions both needed what the engine lacked: multi-claim
intake (no claim splitter — compound was detect-only, and the EVS
critique found silent half-checked conjunctions concluding `supported`).
Separately, model calls used `json_object` + hand-validation, leaving a
whole class of truncation/parse fallbacks, and the falsifier could only
attack from retrieved evidence — never from the live web.

## Decision

1. **ShipCheck sidecar** (`pipeline/split.py`, `run.check_document`,
   `POST /api/check`, batch UI mode): paste any text (e.g. an AI answer),
   split into atomic claims, verify each through the UNCHANGED 7-stage
   pipeline, return per-claim reports + stance roll-up. Split spans survive
   only when verbatim in the input; heuristic sentence-split fallback when
   no model; one failing sub-claim never kills the batch. Rejected:
   in-pipeline compound splitting (intake-level keeps per-claim reports
   re-fetchable and the core untouched).
2. **Strict decoding schemas** (`MetaClient.complete_json(json_schema=...)`,
   verified live incl. null-union types): structure/science/summary/falsify/
   split calls constrain decoding itself. Hand-validation stays (mocks
   bypass decoding). Rejected: leaving `json_object` (parse-fallback
   class) and enum-forcing free fields like units (distorts extraction —
   code still validates against `KNOWN_UNITS`).
3. **Web-armed falsification** (`models/responses.py`, falsify web pass,
   default ON, `ATLAS_WEB_FALSIFY=0` disables): one Responses-API
   `web_search` call hunts live counter-evidence; deterministic code
   validates every `url_citation` (https, quoted span, garbage-domain
   block) and survivors surface as `inconclusive` doubt. Advisory-only is
   preserved structurally. Cost note: ~20–60k input tokens/call reshapes
   the per-analysis estimate (see LIVE_MODEL).
4. **EVS hardening H2/H4/H5**: `stance_accuracy` → `pin_conformance` +
   Wilson CIs + pin-freeze preamble; independent recompute from raw floats
   + `decide_stance` truth-table sweep; `partial_coverage` caveat so no
   half-checked conjunction concludes silently.
5. **Cut**: multimodal image intake (vision mechanism verified live, but
   OCR unreliable on probes + token bloat + no fixture tooling here) and
   the proposal-audit / ad-substantiation / legal skins (occupied,
   misleading, or rebuild-scale — see BREAKTHROUGH_DECISION).

## Consequences

- Per-claim pipeline, stances, and receipts are byte-compatible with
  v0.3.0 semantics; all v0.4.0 power is additive (intake, decoding,
  doubt). Pin-conformance outcome on the v0.4.0 tree is recorded in
  EVAL_REPORT (run notes); any pin move there takes precedence over
  this paragraph.
- Model-call budget rises from ≤4 to ≤5 per claim (web pass added),
  plus 1 split call per batch; an 8-claim batch costs up to 1+8×5=41
  calls — documented, not hidden.
- Deferred with honesty: inter-rater κ pilot (needs 2 humans), threshold
  calibration study, retrieval-relevance scoring, representative
  benchmark, inline-latency SLA.
