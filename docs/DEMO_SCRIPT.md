# 3-minute demo script (v0.3.0)

Setup: server running
(`PYTHONPATH=src .venv/bin/python -m uvicorn atlas.api.app:app --host 127.0.0.1 --port 8765`),
browser on http://127.0.0.1:8765, this file open for the exact claim strings.
Check `GET /api/status` first so you can name the mode (full vs degraded).
Pre-warm: run each claim once before recording so the cache + connectors
are warm; clear Recent analyses for a clean take.

Narrative spine: chatbots assert without receipts → Atlas inverts it
(model reads, only code judges) → the precision beat (refusing full
credit is the feature) → refusal as first-class verdict → live
reproducibility proof (re-run + receipt export).

## 0:00–0:20 — Problem (20s)

"Every fact-checking chatbot gives you 'true or false' with no receipts —
no sources you can open, no math you can check, and no line between what
the model guessed and what was actually verified. Evidence Atlas inverts
that: the model is allowed to *read*, but only code is allowed to
*judge*. And the page proves it on every claim."

Point at the footer ("Deterministic checks always run in code and cannot
be skipped by the model") and name the `#modeBanner` state.

## 0:20–1:00 — Clean supported claim + traceability (40s)

Click **Jobs stat: German unemployment**
(`Unemployment in Germany was 3.1% in 2023.`). Narrate over the staged
skeleton:

"Watch the verdict card: Consistent with evidence, based on N sources
and M checks — and the checks bar visualizes the work, not a confidence
score we refuse to invent. Now the before/after — *before*, that's a
chatbot sentence. *After*, click this C1 pill…"

**Click a `C1` trace pill.** The page scrolls and flashes the exact row:
claimed vs. World Bank value, measured Δ inside the explicit 2% rule,
`✓ Consistent`, `CODE` badge.

"…and you land on the exact check: claimed vs. sourced value, measured
difference, computed in code, badged CODE. Click E1 and you land on the
World Bank row with a live outbound link. Every number jumps to its
receipt."

## 1:00–1:35 — Contradicted claim (35s)

Click **False stat: Japan population**
(`The population of Japan was 500 million in 2020.`):

"Same pipeline, no mercy: World Bank says ~126 million — measured gap,
✕ Contradicts. And the honesty flag: the claim states a bare number,
so the report marks the count assumption instead of reinterpreting it."

## 1:35–2:10 — The precision beat: refusing full credit (35s) — STAR

Click **US GDP 2023** (`The GDP of the United States was $27.36 trillion
in 2023.`):

"Now watch it refuse full credit. Δ1.6% is inside the 2% tolerance —
but the claim states digits down to $10 billion while the retrieved
value differs by $451 billion. So the verdict caps at ◐ Partially
supported, with a claimed-precision flag, an absolute-gap disclosure,
and the self-review note. A chatbot would say 'true'. Atlas says:
'relatively close, but not as stated.'"

Point at the severity rail (flags next to the verdict) and, if the
falsify step fired, its attack note: "It argued with its own draft."

## 2:10–2:30 — The honest refusal (20s)

Paste `Democracy is the best form of government.`:

"And the beat no chatbot gives you: ⊘ Not assessable. An opinion is
not a factual claim, so Atlas returns 'I can't judge this' as a
first-class verdict instead of laundering one through retrieval noise."

## 2:30–2:50 — Reproducibility proof: re-run + receipt (20s)

On any report, hit **Re-run**: "Same input, same verdict, same
input hash — live. ChatGPT cannot do this." Then **Export receipt**:
"And the methods appendix a journalist could file: claim, verdict, Δ,
sources with links, caveats, versions."

Optional ES beat (swap with refusal if judges are technical): paste
`El desempleo en Alemania fue del 3,1% en 2023` — "Same core in
Spanish: decimal comma, Spanish phrasing, still structured by Muse
Spark, still judged by code."

## 2:50–3:00 — Close (10s)

"Real sources with links you can open. Deterministic checks the model
can't skip. Uncertainties printed next to conclusions. 306 offline
tests, 28 pinned eval cases plus paraphrase and abstention probes.
Evidence Atlas: show your work."

## Backup (if network dies mid-demo)

- `eval/results/*.json` contains recorded runs; show the JSON.
- `tests/e2e` runs the full pipeline offline with scripted doubles.
- **Recent analyses** history opens previously persisted reports fully
  offline once stored.
- Record the golden path on video regardless: the submission IS video.
