> **Histórico (v0.4.0).** v1-core era doc, kept in place; the product is Atlas Live ([README](../README.md), [INDEX](INDEX.md)).

# BREAKTHROUGH DECISION — Operation Find the Breakthrough

Date: 2026-11-01. Status: DECIDED. This document records the opportunity ranking,
the Red Team verdicts, the final direction, and the build plan. Evidence: 8 round-1
agent reports + Red Team report (session tool-outputs, 2026-11-01) + fresh web
sources cited per claim or marked UNCERTAIN.

## 1. Ranked opportunities (with uncertainty)

| # | Opportunity | Commercial | Innovation | Hack bet | Sustainable | Verdict |
|---|---|---|---|---|---|---|
| 1 | AI-output auditor sidecar ("verify before it ships" + defensible receipts) | strongest: AI platform budgets exist, self-serve motion, measurable ROI (escape rate, verification hours) | highest shippable: search-grounded adversarial falsification + code-decided verdicts | strongest real-engine demo (Depth/Tooling) | API wedge + OEM path | **GO — the bet** |
| 2 | Ad-claim pre-flight LINTER (NOT substantiation) | crowded (funded ad-tech workflows) | medium | most visceral narrative, but wall-of-shrugs demo risk | thin moat | CONDITIONAL: demo scenario only, never "substantiation file" |
| 3 | Post-hoc stat desk (current skin evolved) | killed: <$500k/yr buyers, free incumbents (established nonprofit fact-checkers) | low ("repo with new CSS") | safe bronze, ban-adjacent | no | FALLBACK floor only |
| 4 | Proposal/bid auditing vs pinned libraries | feature-not-platform (OEM at best) | — | synthetic-data demo reads as RAG | — | **KILLED: an incumbent GA'd this exact product with a free trial (details internal)** |

Killed elsewhere: legal/compliance (rebuild + liability/UPL flags), ops/GRC (paradigm
mismatch), newsroom stat-check (buyers + ban risk), grant/procurement (free
tiers from large platforms), education grading (different product + person-scoring), meeting ledgers
(tracking ≠ verification), pharma MLR (validation budgets), live debate verdicts
(latency + missing splitter + political risk).

## 2. The four questions (§4)

1. **Best commercial:** AI-output auditor. Only buyer where budget exists (LLMOps
   ~$1.8B 2025–26, directionally), pain is third-party quantified (76% of
   enterprise AI deployments hit hallucinations in production — IBM 2025 via
   secondary), buying is self-serve (dev credit cards), and our edge
   (deterministic receipt, zero retrieval cost) attacks incumbents' weakness
   (black-box LLM-judges, per-eval cost). Wedge: post-hoc numeric-claim
   verification API, free tier → $99–299/mo → OEM into eval platforms.
2. **Most innovative (shippable):** auditor + search-grounded adversarial
   falsification: the verifier tries to disprove its own draft verdict against
   the live web, on camera, with code-validated citations surfaced as attributed
   doubt. Model proposes, code disposes — extended to retrieval itself.
3. **Best hackathon bet:** auditor. Red Team ranked the ad-linter first on
   narrative, but its demo death (wall of `not_assessable` shrugs on real ad
   copy, or a mock connector caught agreeing with itself) is LIKELY, while the
   auditor's demo death (hand-split claims) is FIXABLE by building the splitter.
   Fixable beats likely-fatal. "Works" is an explicit Indie criterion: the
   auditor demos on the real unmodified engine.
4. **Most sustainable:** auditor API (usage-based, coverage expansion funded by
   revenue, OEM distribution). The linter competes with funded workflows; the
   desk has no buyers; the proposal play is now occupied.
5. **One combining all four: YES — the AI-output auditor** with the adversarial
   falsification layer. It is simultaneously the commercial wedge, the
   technical innovation, the winnable demo, and the sustainable API business.

## 3. DECISION: ShipCheck — the sidecar that checks every stat AI ships

(Provisional name; product surface, engine is Evidence Atlas v0.3.0+.)

- **Problem:** AI apps ship statistics nobody checks. LLM-judge guardrails can
  hallucinate about hallucinations; verification labor (~4.3 hrs/week/knowledge
  worker — vendor-sourced, UNCERTAIN) is the hidden tax on every deployment.
- **User:** AI engineers, content/support ops shipping stat-heavy outputs.
- **Buyer:** CTO / AI platform teams (self-serve → platform OEM).
- **Value prop:** paste any AI-generated text → every checkable stat verified
  against authoritative sources → per-claim verdict + exportable audit receipt.
  Deterministic where it counts; honest abstention where it can't.
- **Differentiation:** (1) code-decided verdicts + mechanically enforced
  traceability (reports are well-typed w.r.t. evidence), not LLM-judge vibes;
  (2) system tries to disprove itself against the live web (adversarial
  falsification with code-validated citations); (3) re-runnable deterministic
  core; (4) zero marginal retrieval cost on keyless sources.
- **Technical innovation:** claim-splitter → per-claim pipeline → Responses-API
  `web_search` counter-evidence → code adjudication; JSON-schema-constrained
  decoding. (Multimodal intake was CUT — see §4 P2.)
- **Architecture:** v0.3.0 7-stage pipeline UNCHANGED as the per-claim core; new:
  (a) splitter intake, (b) batch `POST /api/check` + roll-up receipt, (c) web
  counter-evidence in falsify stage (degrade-to-skip), (d) image/PDF intake
  (CUT — see §4 P2), (e) "paste AI output" UI mode with per-claim cards.
- **Meta role (substantial, not decorative):** Muse Spark structures claims,
  grades abstract stance, drafts summaries, splits multi-claim text, and runs
  the web-armed falsifier — all under `json_schema` decoding constraints; Chat
  Completions for the deterministic-adjacent calls, Responses API + `web_search`
  + reasoning for the adversarial layer. Cost OBSERVED (not estimated):
  ~$0.10–0.15/analysis with web falsify on (one 60k-input-token call
  dominates); `ATLAS_WEB_FALSIFY=0` returns to ~$0.02.
- **Demo scope (3 min):** (1) paste a stat-heavy AI answer → splitter → 3
  receipts (supported / precision-capped / contradicted); (2) the falsifier
  attacks the draft against the live web, on camera; (3) screenshot-of-chart
  intake (CUT — see §4 P2; text paste is the intake); (4) export receipt +
  deterministic re-run. Second input: an AI-written marketing paragraph
  (linter energy, zero FTC claims).
- **Post-deadline:** web-source verification connector (beyond counter-evidence),
  latency SLA for inline use, auth/multi-user, coverage packs (finance, polls),
  OEM pilots, inter-rater κ study (H1), threshold calibration study.
- **Risks/limits (honest):** WB/OpenAlex coverage is narrow (most AI claims →
  `unverifiable`, disclosed); 2% tolerance + materiality are unvalidated priors;
  repeatability measured 3× full (0 stance flips; see EVAL_REPORT);
  latency ~20s+ per claim, ~100s per 3-claim batch (post-hoc,
  not inline); funded incumbents own speed/distribution — we own the
  inspectable artifact.
- **Commercial argument:** land free tier + $99–299/mo with AI app builders;
  expand via OEM as "the deterministic numeric layer" under eval stacks;
  usage-based API at $0.01–0.05/claim anchor. 2-week pilots measurable on
  escape rate + verification hours. Newsrooms = logos, proposal vendors =
  distribution, publishers = depth — AI builders pay the bills.

Why superior: it is the only candidate that exercises the REAL engine end to
end (nothing mocked), fixes its load-bearing gap (splitter) instead of routing
around it, adds a Meta-frontier capability competitors can't copy in a sprint
(web-armed adversarial falsification under code adjudication), and tells a
commercial story with a real buyer and measurable ROI.

## 4. Build plan (P0 → P2) + ownership

- P0-A (lead): claim-splitter (schema-constrained) + batch `/api/check` + roll-up.
- P0-B (lead): `json_object` → `json_schema` decoding everywhere (reliability floor).
- P0-C (UI agent, after P0-A contract): "paste AI output" mode, per-claim cards.
- P1-A (agent W): Responses-API `web_search` counter-evidence in falsify
  (code-validated citations, advisory-only, degrade-to-skip). Files: NEW
  `models/responses.py`, `pipeline/falsify.py`, NEW tests. No other files.
- P1-B (agent H): EVS hardening H2 (rename→pin_conformance + Wilson CIs +
  pin-freeze log) + H4 (independent recompute + decide_stance truth-table sweep)
  + H5-minimal (partial-coverage caveat, no silent supported). Files:
  `scripts/run_eval.py`, NEW stance-table test, minimal `synthesize.py` caveat,
  NEW tests. No other files.
- P2 image/PDF intake: CUT (2026-11-01, lead). Vision mechanism verified live
  (data-URL input accepted, schema-constrained JSON out) but OCR on probe
  images was unreliable (misread digits, then a 4000-token reasoning
  truncation on a 3.7KB image), and this environment has no way to generate
  or verify a realistic screenshot fixture (no PIL/browsers/renderers). A
  flaky multimodal demo beat is worse than none; text paste is the intake.
  Revisit post-deadline with curated fixtures + bigger token budgets.
- Running in background: H3 5× full-mode repeatability baseline (v0.3.0).
- Deferred with honesty: H1 κ pilot (needs 2 human raters), calibration study,
  E-dimension retrieval scoring, representative benchmark.

## 5. Exit gate for this operation

Ranking ✓ (this doc) · Red Team ✓ · Decision ✓ · Build → · Demo+docs →
8-question gate + rubric scorecard → single milestone commit + push.
