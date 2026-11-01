# 3-minute demo script (v0.4.0 ShipCheck)

Setup: server running
(`PYTHONPATH=src .venv/bin/python -m uvicorn atlas.api.app:app --host 127.0.0.1 --port 8765`),
browser on http://127.0.0.1:8765, this file open for the exact text.
Check `GET /api/status` first so you can name the mode (full vs degraded).
Pre-warm: run the batch once before recording so connectors are warm;
clear Recent analyses for a clean take. Batch took 38.7s for 3 claims
in rehearsal (recorded:
`eval/results/demo_batch_ns_20261101T062500Z.json`, parallel
sub-claims, roll-up 1-1-1, 0 errors; the v0.4.0 sequential run took
95.3s); edit the wait out of the video — the elapsed timer proves it
ran live.

Narrative spine: AI ships stats nobody checks → paste the whole answer
→ three receipts (caught hallucination + refused full credit +
confirmed) → the system tried to disprove itself against the live web →
reproducibility proof (re-run + receipt export).

## 0:00–0:20 — Problem (20s)

"Every AI app ships statistics nobody checks — and guardrails that use
one model to judge another can hallucinate about hallucinations. Watch:
I paste a whole AI answer, and ShipCheck gives every claim its own
verdict with its own receipt. The model is allowed to *read*, but only
code is allowed to *judge*."

Switch to **Check AI output**. Paste the demo text (below) but don't
run yet.

## 0:20–1:30 — The batch: three claims, three receipts (70s)

Demo text (paste verbatim):

> The GDP of the United States was $27.36 trillion in 2023. The
> population of Japan was 125 million in 2023. Unemployment in Germany
> was 9.8% in 2023.

Hit **Check AI output**. Narrate over the skeleton (edit the wait):

"The splitter found 3 checkable claims — verbatim spans, nothing
invented. Each one just ran the full pipeline in parallel:
structure, retrieve, verify, self-attack, synthesize."

When the roll-up lands (1 supported / 1 partially_supported /
1 contradicted):

"Three receipts. Claim three: ✕ Contradicted — Germany's unemployment
was ~3.1%, not 9.8%, measured Δ219%. That's the hallucination, caught
with the World Bank row linked. Claim two: Consistent with evidence —
World Bank value inside the explicit 2% rule of 125M claimed. And
claim one is the star…"

**Scroll to the GDP card:**

"Δ1.6% is inside tolerance — but the claim states digits down to $10
billion while the retrieved value differs by $451 billion. So the
verdict caps at ◐ Partially supported, with a claimed-precision flag
and an absolute-gap disclosure. A chatbot would say 'true'. Atlas says:
'relatively close, but not as stated.'"

## 1:30–2:10 — It tried to disprove itself (40s)

On the GDP or Japan card, open the web counter-evidence comparison:

"And here's the beat no guardrail gives you: after reaching its draft,
the system searched the LIVE web to disprove itself — on camera. Real
citations, validated by code, surfaced as attributed doubt. In
rehearsal the GDP card drew a page quoting a BEA vintage at
$27.83T against the claimed $27.36T (in the recorded artifact —
note the citation is the quoting page, not BEA itself; the engine
now orders official sources first but still shows what it found).
Live web varies run to run, so read whatever this run found — the
point is the hunting, not any single number. It cannot move the
verdict; it can only attach the doubt. Intellectual honesty,
visible."

If the falsify attacks fired, point at one: "It also argued with its
own draft from the retrieved evidence." Then scroll the uncertainties:
"And look — pages it retrieved but did NOT cite, listed as
considered-not-cited, official sources first. In rehearsal the GDP
card showed BEA and World Bank files here even though the model
cited third-party pages. The engine shows its whole hand, not just
the cards it played."

## 2:10–2:30 — The honest refusal (20s)

Switch to **Single claim**, paste
`Democracy is the best form of government.`:

"And the beat no chatbot gives you: ⊘ Not assessable. An opinion is
not a factual claim, so Atlas returns 'I can't judge this' as a
first-class verdict instead of laundering one through retrieval noise."

## 2:30–2:50 — Reproducibility proof: re-run + receipt (20s)

On any per-claim card, hit **Re-run**: "Same input re-analyzed live —
the card shows both verdicts and the input hash side by side, so you can
see the determinism instead of taking our word for it. ChatGPT cannot do
this." Then **Export receipt**:
"And the methods appendix you could file: claim, verdict, Δ, sources
with links, caveats, versions."

## 2:50–3:00 — Close (10s)

"Real sources with links you can open. Deterministic checks the model
can't skip. A system that hunts counter-evidence against itself — then
shows its work. 640 offline tests, 43 pinned eval cases plus
paraphrase, abstention, and decision-table probes. Evidence Atlas:
verify before it ships."

## Backup (if network dies mid-demo)

- Keep a saved `/api/check` response JSON from rehearsal; show it as
  the recorded run.
- `eval/results/*.json` contains recorded runs; show the JSON.
- `tests/e2e` runs the full pipeline offline with scripted doubles.
- **Recent analyses** history opens previously persisted reports fully
  offline once stored.
- Record the golden path on video regardless: the submission IS video.
- Kill-switch honesty: `ATLAS_WEB_FALSIFY=0` disables the live-web pass
  (audit says `web=disabled`); the batch still verifies everything else.
