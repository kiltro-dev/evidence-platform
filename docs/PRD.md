# Evidence Atlas — PRD (v4 ShipCheck; v2 MVP frozen 2026-10-31, amended 2026-11-01)

## 1. Problem & users

AI apps ship statistics nobody checks, and people who encounter factual
claims (stats in articles, study references, AI answers, social posts)
cannot tell what is sourced, what is inferred, and what is missing.
Target users: hackathon evaluators, AI engineers shipping stat-heavy
outputs, journalists/researchers, curious non-technical readers. They
need a traceable verdict per claim, not a true/false oracle.

## 2. Scope (v4 ShipCheck)

**In:** single free-text claim (EN/ES) → validation → extraction →
domain routing (auto + user override) → World Bank / OpenAlex retrieval →
deterministic verification → adversarial self-review (schema attacks +
live-web counter-evidence, advisory only) → 5-outcome assessment →
traceable report UI with evidence, comparisons, uncertainties, audit;
**ShipCheck batch** (`POST /api/check`): pasted multi-claim text → split
(≤8 verbatim sub-claims) → per-claim reports + stance roll-up; history
(recent list + deep link + delete); eval benchmark (scripted + live,
dual-mode, paraphrase/abstention probes, decision-table sweep); smoke
script; professional docs.

**Out (documented, not silent):** in-pipeline compound splitting
(intake-level split + detect+disclose + `partial_coverage` caveat
instead), async job polling (honest staged skeleton + abort instead),
web-source *verification* (web is counter-evidence/doubt only, never a
verdict input), image/PDF intake (cut after unreliable probes —
ADR-0009), token accounting per report beyond best-effort usage persist,
auth/multi-user, charts, i18n, file/URL ingestion.

## 3. User journeys

- J0: paste whole AI answer → per-claim verdicts + roll-up
  (ShipCheck batch; the headline journey).
- J1: paste EN statistical claim → verdict + inspectable WB evidence.
- J2: paste false stat → contradicted with measured Δ and honest flags.
- J3: paste ES claim → same quality as EN (model path; heuristic gap disclosed).
- J4: paste opinion/compound/vague text → `not_assessable` or flagged
  partial analysis with explanation, never a fabricated verdict.
- J5: revisit prior analysis via Recent list or `?report=` link.
- J6: new dev installs, runs tests, evaluates, demos from README alone.

## 4. Input/output contract

**POST /api/analyze** `{text (1..4000 chars), limit? (1..10, default 5),
domain?: auto|economy|science|general, persist?: bool default true}`.
**POST /api/check** (ShipCheck batch): same fields, `text` 1..12000
chars → `{claim_count, reports[] (full Report or {claim_text, error}),
rollup{5 stances}, errors, elapsed_ms, meta{splitter_actor,
splitter_model, splitter_notes[], usage_split, input_sha256}}`.
**Errors (unified envelope):** `{error:{code,message,request_id}}` for
400/404/422/405/500. Codes: `empty_claim, claim_too_long, invalid_domain,
invalid_limit, report_not_found, analysis_failed, rate_limited,
bad_request` (`bad_request`: unforeseen validation shapes and framework
4xx such as 405; missing/wrong-typed fields return 422 with the
field-specific code).
**Report:** `report_id, claim{subclaims? no — compound disclosed via
uncertainty}, domain (effective + override flag), evidences[],
comparisons[] (stable display ids C1.. derived client-side from
comparison_id), uncertainties[] (coded+severity), limitations[],
conclusion{stance∈{supported,contradicted,partially_supported,
unverifiable,not_assessable}, summary, caveats (code∪model),
support/contradict ids}, audit[] (with duration_ms), meta{app_version,
model, temperature, max_tokens, tolerance, prompt_version,
input_sha256, usage_extract, usage_science, usage_falsify,
usage_summary}, elapsed_ms`.

**Stance semantics:** supported (verifying consistent, no blocking unc.,
claimed precision holds, all real quantities verified on compounds),
contradicted (any verifying contradicts), partially_supported (context
only, blocked consistent, claimed-precision exceeded, or compound
under-verified), unverifiable
(searched, insufficient), not_assessable (opinion/non-claim/empty —
no retrieval attempted or retrieval meaningless). UI copy never renders
bare "Supported": "Consistent with evidence (within 2% relative)".

## 5. Non-functional decisions

- Reliability: no silent failure; connector/LLM faults labeled with cause.
- Perf: sync analyze; UI aborts >45s; elapsed + skeleton shown.
- Cost: ≤5 model calls/single-claim analysis (structure + science +
  falsify + web counter-evidence + summary), +1 split call and a full
  run per sub-claim (cap 8) on the batch path; identical-text cache
  (analyze only; size-capped, in-memory, model-aware); per-IP rate
  limit, shared bucket (generous, documented); per-stage usage in meta
  (+ `usage_split` on the roll-up).
- Security: localhost-only documented property; iso3 allowlist; finite
  floats + list caps; http(s) link allowlist; no key in responses/logs/DB.
- Reproducibility: README-verbatim install; `scripts/smoke.sh` gate.

## 6. Acceptance (exit gate)

§8 criteria 1–14 of the mission; benchmark v2 green in degraded mode +
full-mode spot-check; independent reviewer sign-off; single milestone
commit + push.
