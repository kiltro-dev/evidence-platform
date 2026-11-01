# Evidence Atlas — PRD (v2 MVP, frozen 2026-10-31)

## 1. Problem & users

Users who encounter factual claims (stats in articles, study references,
social posts) cannot tell what is sourced, what is inferred, and what is
missing. Target users: hackathon evaluators, journalists/researchers,
curious non-technical readers. They need a traceable verdict, not a
true/false oracle.

## 2. Scope (MVP v2)

**In:** single free-text claim (EN/ES) → validation → extraction →
domain routing (auto + user override) → World Bank / OpenAlex retrieval →
deterministic verification → 5-outcome assessment → traceable report UI
with evidence, comparisons, uncertainties, audit; history (recent list +
deep link + delete); eval benchmark v2 (scripted + live, dual-mode);
smoke script; professional docs.

**Out (documented, not silent):** full compound splitting (detect+disclose
only), async job polling (honest staged skeleton + abort instead), web
search grounding integration (interface seam + policy only), token
accounting per report (best-effort usage persist only), auth/multi-user,
charts, export, i18n, file/URL ingestion.

## 3. User journeys

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
input_sha256, usage?}, elapsed_ms`.

**Stance semantics:** supported (verifying consistent, no blocking unc.),
contradicted (any verifying contradicts), partially_supported (context
only or blocked consistent), unverifiable (searched, insufficient),
not_assessable (opinion/non-claim/compound-unresolvable/empty-after-
extract — no retrieval attempted or retrieval meaningless).

## 5. Non-functional decisions

- Reliability: no silent failure; connector/LLM faults labeled with cause.
- Perf: sync analyze; UI aborts >45s; elapsed + skeleton shown.
- Cost: ≤2 model calls/analysis; identical-text cache (size-capped,
  in-memory); per-IP rate limit (generous, documented).
- Security: localhost-only documented property; iso3 allowlist; finite
  floats + list caps; http(s) link allowlist; no key in responses/logs/DB.
- Reproducibility: README-verbatim install; `scripts/smoke.sh` gate.

## 6. Acceptance (exit gate)

§8 criteria 1–14 of the mission; benchmark v2 green in degraded mode +
full-mode spot-check; independent reviewer sign-off; single milestone
commit + push.
