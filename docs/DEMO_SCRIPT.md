# 3-minute demo script

Setup: server running (`uvicorn atlas.api.app:app --port 8765`), browser
on http://127.0.0.1:8765, this file open for the exact claim strings.

## 0:00–0:25 — Problem (25s)

"Fact-checking chatbots give you 'true or false' with no receipts: no
sources you can open, no math you can check, no line between what the
model guessed and what was actually verified. Evidence Atlas is the
opposite: a workbench that shows its work for every claim."

## 0:25–1:15 — Supported claim (50s)

Paste: `The GDP of the United States was $27.36 trillion in 2023.`
Click Analyze. Narrate while it loads:

- "Structured: economy, quantitative, 27.36 trillion USD, USA, 2023 —
  badge shows who extracted it."
- "Evidence: one World Bank row, link included, retrieved live."
- "Comparison: same unit, same year, Δ1.6% inside the explicit 2% rule —
  consistent, computed in code, not by the model."
- "Stance: supported, with the audit trail below."

## 1:15–2:00 — Contradicted claim (45s)

Paste: `The population of Japan was 500 million in 2020.`

- "Same pipeline, different outcome: World Bank says ~126 million —
  measured difference, flagged as contradicts."
- "Note the honesty flag: the claim states a bare number, so the report
  marks the count assumption explicitly instead of hiding it."

## 2:00–2:35 — What it refuses to do (35s)

Paste: `A 2021 study found that global average temperature rose sharply.`

- "Five real OpenAlex works — but titles aren't verification. The system
  caps itself at partially supported and records WHY: metadata only.
  It would rather underclaim than overclaim."

## 2:35–3:00 — Close (25s)

"Three ideas: real sources with links, deterministic checks the model
can't skip, and uncertainties printed next to conclusions. Muse Spark
structures and summarizes; code verifies. Everything you saw — 28
offline tests, 3 live, 10 recorded eval cases — reruns from a clean
checkout. Evidence Atlas: show your work."

## Backup (if network dies mid-demo)

`eval/results/*.json` contains a recorded 10/10 run; `tests/e2e` runs
the full pipeline offline with scripted doubles. Show the JSON.
