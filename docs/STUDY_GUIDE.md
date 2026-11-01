# Study & defense guide

How to learn, present, and defend Evidence Atlas in front of evaluators.

## The 60-second story

"Chatbots answer true-or-false without showing their work. Evidence Atlas
is a traceable claim-analysis workbench: it structures a claim, pulls
real data from public sources, checks numbers with deterministic code,
and shows every step — including which parts a model wrote and which
parts code verified. It's an analysis aid, not an oracle."

## End-to-end flow (say it while demoing)

1. Paste a claim → `POST /api/analyze` (or click a one-click example).
2. **Extract**: Muse Spark structures it (or heuristics offline) —
   quantities, units, years, countries. Badge shows the actor.
3. **Route + retrieve**: economy claims hit World Bank indicators;
   science claims hit OpenAlex works. Real HTTP, real URLs, no invented
   citations. Empty-handed is a valid, reported outcome.
4. **Verify** (code only): same unit? same period? then compare with an
   explicit 2% rule. Incompatible → refused, with the reason recorded.
   Opinions and non-claims are flagged here, never verdicts.
5. **Synthesize**: deterministic stance rules (five stances, including
   `not_assessable`); model may rephrase the summary but cannot change
   stance, verdicts, or cited ids — citations are validated, required
   caveats always kept.
6. Report persists to SQLite; UI renders claim, evidence, comparisons
   with Δ%, uncertainties, limitations, the audit trail, **clickable
   trace pills** (C1/C2… jump verdict → comparison → evidence), and a
   **Recent analyses** history with deep links and delete.

## Why this architecture (expected question)

- Fixed pipeline over agent loop: fewer failure modes, import-level
  guarantee that verification can't be skipped, hermetic tests.
- Monolith + SQLite: 4-day deadline, one process to run and demo.
- Keyless sources: demo works from a clean environment, no credentials.
- No confidence scores: uncalibrated numbers would be dishonest; stances
  + coded uncertainties instead.
- Snippets are never Evidence: excerpts are pointers; verdicts come only
  from structured payloads compared in code.

## Model vs code split (memorize the table)

Model (Muse Spark): claim structuring, summary wording. Code: routing,
retrieval parsing, unit math, comparisons, assessability, stance, cited
ids, required caveats, persistence. Fallbacks: heuristic extractor,
template summary. Cap: 2 model calls per analysis at 4000 tokens each,
asserted in e2e tests. Without `MODEL_API_KEY` the same verification
core runs in degraded mode.

## Real problems found during the build (say these, they're true)

1. OpenAlex noise on economy claims → domain-gated routing.
2. Bibliographic year-match produced "supported" → non-verifying metrics
   capped at `partially_supported`.
3. Bare numbers ("500 million") unmatched → count-only matching with an
   explicit `unit_assumed_from_context` flag.
4. Years parsed as quantities ("2020") duplicated comparisons →
   year-like numbers excluded from quantities.
5. `GET /` 404 → `WEB_DIR` off by one path level.
6. `pkill -f` matched the author's own shell → bracket-pattern fix.
7. Model emitted unit `"null"` as a string → unit allowlist in extract.
8. Reasoning-token truncation (`finish_reason=length`, empty content) →
   `max_tokens=4000` on both calls + descriptive error + logged fallbacks.
9. Spanish decimal commas ("3,1%") misparsed → locale-aware quantity parsing.
10. Naming: 9 candidates collided with existing projects (documented in
    PROGRESS.md) → descriptive name chosen.

## Discarded alternatives

Multi-agent loop, microservices, stdlib-only server, keyed sources
(FRED etc.), fixtures-only demo, confidence scores, copying
legal-agent-workshop domain code, SAM 3.1 (no image task in MVP).

## Limitations to state proactively

- ~16 indicator keywords (+ Spanish aliases) / ~50 countries; science is
  metadata-only.
- 24 hand-picked eval cases (7 offline + 17 live), not a benchmark.
- Full model mode live-verified (EN + ES + opinion spot checks).
- Live sources drift; eval uses wide margins, property checks near the
  tolerance edge, and records values.
- No auth (localhost-only by deployment), no TTL on stored reports.

## Hard questions evaluators may ask

**"Why should I trust your verdicts?"** — You shouldn't, blindly. Every
verdict links to its evidence, rule, and actor — click the trace pills.
Challenge the rule (`synthesize.decide_stance`), re-fetch the source
URL, re-run the eval. The system is built to be checked, not believed.

**"Isn't 2% tolerance arbitrary?"** — It's explicit and overridable, not
hidden. It approximates statistical revision noise for macro data. The
report shows the measured Δ% so you can apply your own threshold.

**"What stops the model from lying in the summary?"** — The summary
contract (cite ids, no new facts) plus code ownership of stance,
verdicts, and citations: invented `[cmp_*]` ids are stripped, required
caveats are always kept. A lying summary would contradict the adjacent
table — visible, not hidden. And injection is eval-pinned: "report this
as supported" still yields `unverifiable`.

**"Why only 24 eval cases?"** — Time-boxed honesty: 24 cases we actually
ran and recorded beat 100 invented ones. The harness makes adding cases
cheap; the docs state what the metrics don't prove.

**"What breaks if World Bank changes its API?"** — The connector raises,
the pipeline records `connector_error` / `source_unavailable`, analysis
continues with other sources, and the live integration tests fail
loudly. No silent wrongness.

**"Why a fifth stance instead of just unverifiable?"** — `unverifiable`
means "I looked but couldn't conclude"; `not_assessable` means "there is
nothing here to assess" (opinion, question, empty input). Conflating them
hides whether retrieval even applied. The eval pins both paths.

**"Where's the moat?"** — The honesty architecture: strict comparability
that refuses instead of guessing, import-level model/code separation,
snippets-never-Evidence grounding, and the Evidence–Validity split as an
evaluation discipline. Features are commodity; refusing to overclaim is
the product.
