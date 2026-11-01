# 3-minute demo script

Setup: server running
(`PYTHONPATH=src .venv/bin/python -m uvicorn atlas.api.app:app --host 127.0.0.1 --port 8765`),
browser on http://127.0.0.1:8765, this file open for the exact claim strings.
Check `GET /api/status` first so you can name the mode (full vs degraded).

## 0:00–0:25 — Problem (25s)

"Fact-checking chatbots give you 'true or false' with no receipts: no
sources you can open, no math you can check, no line between what the
model guessed and what was actually verified. Evidence Atlas is the
opposite: a workbench that shows its work for every claim."

## 0:25–1:10 — Supported claim (45s)

Click the **True stat: US GDP 2023** example (or paste
`The GDP of the United States was $27.36 trillion in 2023.`).
Narrate while it loads:

- "Structured: economy, quantitative, 27.36 trillion USD, USA, 2023 —
  badge shows who extracted it."
- "Evidence: one World Bank row, link included, retrieved live."
- "Comparison: same unit, same year, Δ1.6% inside the explicit 2% rule —
  consistent, computed in code, not by the model."
- "Stance: supported, with the audit trail below."
- Click a **trace pill** (C1/C2, E1): "Every verdict number jumps to the
  exact comparison and evidence behind it. That is the traceability
  guarantee, clickable."

## 1:10–1:50 — Contradicted claim (40s)

Click **False stat: Japan population**
(`The population of Japan was 500 million in 2020.`):

- "Same pipeline, different outcome: World Bank says ~126 million —
  measured difference, flagged as contradicts."
- "Note the honesty flag: the claim states a bare number, so the report
  marks the count assumption explicitly instead of hiding it."
- Scroll to **Recent analyses**: "Both reports persisted automatically.
  Each re-opens by deep link, and each can be deleted — no dead data."

## 1:50–2:25 — What it refuses to do (35s)

Paste `Democracy is the best form of government.` (opinion):

- "Not assessable — the ⊘ badge. An opinion is not a factual claim, so
  the system refuses a verdict instead of laundering one through
  retrieval noise. That fifth stance exists so 'I can't judge this' is a
  first-class answer."

If time allows, paste the Spanish claim
`El desempleo en Alemania fue del 3,1% en 2023` (decimal comma, ES
keywords): "Same verification core in Spanish — supported."

For the science refusal story instead, click **Vague science: green tea**:
"Real OpenAlex works — but titles aren't verification. The system caps
itself at partially supported and records WHY: metadata only. It would
rather underclaim than overclaim."

## 2:25–3:00 — Close (35s)

"Three ideas: real sources with links, deterministic checks the model
can't skip, and uncertainties printed next to conclusions. Muse Spark
structures and summarizes; code verifies. Everything you saw — 179
offline tests, 7/7 offline eval cases plus live full-mode checks —
reruns from a clean checkout. Evidence Atlas: show your work."

## Backup (if network dies mid-demo)

- `eval/results/*.json` contains a recorded 7/7 offline run; show the JSON.
- `tests/e2e` runs the full pipeline offline with scripted doubles.
- The **Recent analyses** history still opens previously persisted
  reports (they render fully offline once stored).
