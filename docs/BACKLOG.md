# Mission backlog (living, lead-owned)

STATUS 2026-11-01 (Operation Find the Breakthrough, v0.4.0 ShipCheck):
8 research agents + red team → ranked opportunities → ONE bet
(AI-output auditor sidecar; rationale in BREAKTHROUGH_DECISION.md).
Build: claim-splitter + POST /api/check + strict json_schema decoding
+ Responses web_search counter-evidence (default on) + EVS hardening
(H2/H4/H5) + sidecar UI. 416 unit+e2e green. Killed: proposal-audit
(Vera GA'd), ad-substantiation skin, legal/GRC/newsroom/live-debate,
image intake (unreliable probes). Exit gate: full-eval green +
repeatability + red-team sign-off + milestone commit + push.

Prior STATUS (Operation First Place, v0.3.0): 7-team competitive
research → readiness report → 6-agent implementation wave (M1 methodology
core, M2 falsification pass, M3 science abstracts, M4 infra, M5 UX,
M6 eval) + lead integration (run.py wiring, span recovery, usage
attribution). 306 unit+e2e green; eval 28 cases + paraphrase/abstention
probes. Methodology change: claimed-precision rule caps exact-figure
GDP-style claims at partially_supported (designed a priori, Team-E F1).
Exit gate: red-team re-review + milestone commit + push.

Prior STATUS (v0.2.0): Waves A–D landed and integrated. 185 unit+e2e
green, smoke 29 PASS, full-mode eval 24/24 after lead reconciliation.
Reviewer REJECT→fixes→SIGN OFF.

FROZEN CONTRACTS (frontend builds against these; backend must match):
- Errors: `{error:{code,message,request_id}}`, codes per PRD §4.
- `POST /api/analyze {text, limit?, domain?, persist?}`; `domain` invalid → 400.
- `DELETE /api/reports/{id}` → 200 `{deleted:true}` / 404 envelope.
- Report adds: `meta{...}`, `elapsed_ms`, `AuditStep.duration_ms`;
  `Stance += not_assessable`; caveats = code ∪ model.
- `GET /api/reports?limit=` unchanged shape.

## Wave A — backend (parallel, disjoint files, NO live model calls, NO git)

- A1 api-owner: `src/atlas/api/app.py`, `tests/unit/test_api_errors.py`,
  `tests/e2e/test_api.py`. Envelope + handlers + request_id; domain
  param; persist flag; DELETE; clamp→400? (decide: keep clamp, document);
  TestClient e2e (400/404/clamp/round-trip/status); basic per-IP rate
  limit (60/min, 429 envelope); identical-text cache (cap 128, TTL 1h).
- A2a pipeline-owner: `core/types.py`, `pipeline/extract.py`,
  `pipeline/synthesize.py`, `pipeline/verify.py`, `models/meta.py`
  (no—meta owned by none; usage persist: add `usage` passthrough in
  complete_json? DECISION: skip usage persist this wave, keep LIVE_MODEL
  note). Tasks: 5th stance + rule; compound detect+flag; STRUCTURE
  hardening (F1–F4,F6) + `_from_llm_json` validation (unit enum, finite
  floats, list caps ≤20, year-quantity guard via label/kind); mixed
  provenance actor; caveat union; summary citation post-validation
  (strip unknown [cmp_*]); empty/unlabeled paths logged.
- A2b connectors-owner: `connectors/*.py`, `pipeline/run.py`,
  `storage/store.py`. Typed `ProviderError(transient)`; propagate
  transport errors → `connector_error`/`source_unavailable` + failed
  audit step (never silent []); query echo in `payload["query"]`;
  iso3 allowlist (S1); store init-on-write-only + `delete_report`;
  audit `duration_ms` real timing; `meta` block assembly in run.py;
  `elapsed_ms`.
- A3 i18n-owner: `models/heuristic.py`, `core/units.py`,
  `connectors/countries.py`, `connectors/worldbank.py` (INDICATORS only —
  coordinate: A2b must not touch INDICATORS), `tests/unit/test_i18n.py`.
  Spanish keyword/unit/country packs; ES parse tests; EN suite stays green.

## Wave B — frontend (one owner, `web/index.html` only)

- B1: full IA rebuild per UX audit (F-01..F-10): sticky verdict + trace
  pills (client-side C/E numbering from ids), staged skeleton + elapsed
  + 45s abort + typed errors + retry, numbered evidence cards + mobile
  tables, actor legend + stance definitions, one-click intent examples,
  Recent strip + `?report=` deep link, label/counter/shortcut, severity
  pills + Method&limits box, a11y pass, mode banner, http(s) link
  allowlist, domain select (auto/economy/science), degraded banner.

## Wave C — eval/tooling (parallel)

- C1 eval-owner: `eval/cases.py` (add cases 4,6,8,10,11,12,13,17,18,19,
  20,22,23,24 per AI audit), `scripts/run_eval.py` (`--mode both`,
  split expectations, stance-sets, abstention + prose-traceability
  checks), `models/scripted.py` (fault modes: raise/empty/garbage/
  oversized), `tests/unit/test_fallbacks.py`, `tests/unit/test_store.py`,
  determinism + connector-death tests.
- C2 tooling-owner: `scripts/smoke.sh` (phases 0–4 offline, --live,
  --eval), `pyproject.toml` (pytest marker reg), `requirements.lock`
  (uv pip compile) + README-independent? No—README in docs wave; C2
  verifies smoke passes and reports exact commands for docs.

## Wave D — docs (after waves A–C integrate)

- Single docs agent: README rewrite (R1–R12), C4 Mermaid, OpenAPI export
  + docs/API.md, AI methodology, eval report, security/ops, developer
  guide, demo refresh, grounding policy line, drift fixes.

## Deferred (in PRD §2 + rationale on request)

Full compound split, async jobs, grounding integration, token
accounting persist (can't bill/attribute per-report cost), retention TTL
(DB grows unbounded until manual DELETE), rate-limit gateway (in-memory
limits don't span processes), charts/export/i18n (no visualizations,
no report export, EN-only UI copy).
