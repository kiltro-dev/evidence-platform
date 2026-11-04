> **Histórico (v0.4.0).** v1-core era doc, kept in place; the product is Atlas Live ([README](../README.md), [INDEX](INDEX.md)).

# Evidence–Validity Dual Assessment (project proposal)

> A methodological proposal of this project, not an established academic
> standard. Names researched 2026-10-31: VERA, EVS-style acronyms, Evalens
> and similar short brands all collide with existing projects; this
> proposal therefore uses a plain descriptive name.

## Idea

Evaluating an AI-and-evidence system conflates two independent questions:

1. **Evidence** — was the material the system used any good?
2. **Validity** — was the process that turned material into conclusions sound?

A system can score well on one and fail on the other: excellent sources
with sloppy comparison logic, or rigorous logic over invented citations.
Single-number benchmarks hide exactly this distinction.

## Dimension E: Evidence quality

| Criterion | Question | MVP operationalization |
|---|---|---|
| Provenance | Where does each item come from? | source name + URL + kind recorded per evidence |
| Relevance | Does it bear on the claim? | same-indicator / same-subject retrieval; year-overlap gate |
| Sufficiency | Is there enough to conclude? | `no_direct_evidence`, `metadata_only` uncertainties when not |
| Verifiability | Can a third party re-fetch it? | stable source URLs; payloads preserved in the report |

## Dimension V: Process validity

| Criterion | Question | MVP operationalization |
|---|---|---|
| Consistency | Same input → same verdict? | deterministic core; temperature-0 model calls; 11/11 repeatable offline eval + 36/36 paraphrase agreement |
| Correctness | Are the checks right? | unit tests on comparison/stance rules; strict comparability |
| Traceability | Does every conclusion resolve to evidence? | `traceability` eval check (must be 1.0) |
| Disclosure | Are model/code roles and limits explicit? | audit trail badges; limitations block; no hidden scores |

## Procedure

1. Run the case set (`scripts/run_eval.py`).
2. Score E per case: provenance present? relevance gated? insufficiency
   disclosed rather than papered over?
3. Score V per case: verdicts follow the published rules? conclusion
   traces to evidence? actors labeled?
4. Report E and V separately with the raw result JSON. Never average
   them into one number — a 0.9/0.3 split is the finding, not noise.

## Application to this MVP (honest self-grade)

- E: strong for economy (direct values, stable URLs); weak-by-design for
  science (metadata + abstract stance — disclosed, capped at
  `partially_supported`).
- V: strong on traceability (1.0, enforced; prose citations checked too),
  consistency (deterministic), disclosure (badges + limitations); narrow
  on correctness coverage (28 hand-picked cases).
- Known gaps: no full-text science verification, no retrieval-relevance
  scoring beyond year/indicator matching, only two pinned adversarial
  phrasing cases (authority invocation, prompt injection).

## E/V on stage (demo tie-in)

The demo makes the split legible in one beat each: the GDP precision cap
is high-V/low-E on the stated digits (the instrument works; the evidence
does not confirm the digits); the ⊘ opinion refusal is the instrument
declining to score E at all. When the falsifier fires, say so: V
auditing itself, live.

## Limitations of the proposal

- It assesses *a run of the system*, not source truthfulness: perfect V
  over a lying source still yields a wrong-but-traceable conclusion.
- Criteria E3/V2 need human judgment at the boundary (is this source
  "relevant"?); the MVP approximates with deterministic gates.
- Not validated outside this project; offered as a working method with
  open procedure and data, not as a standard.
