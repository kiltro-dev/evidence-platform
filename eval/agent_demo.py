"""Live demo: one self-correction run with before/after metric (Pilar A-backend).

LIVE: spends real model tokens (bounded: 1 run, small max_tokens).
Never import this from pytest. Run:
    .venv/bin/python eval/agent_demo.py

Metric: contradicted-figures before vs after — for each sentence carrying
>=1 figure in draft/final, `analyze` (heuristic, in-process, no writes)
assigns a stance; figures inside contradicted sentences count as bad.
Cost: ($1.25 prompt / $4.25 completion per 1M tokens).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from atlas.agent.loop import MetaToolClient, count_figures, extract_claims, run_agent
from atlas.pipeline.run import analyze

TOPIC = (
    "Short 2023 economic snapshot: United States GDP, Germany unemployment "
    "rate, Japan population, and China GDP. Give concrete figures for each."
)

BUDGET_USD = 3.0


def contradicted_figures(text: str) -> tuple[int, list[dict]]:
    """(n_bad_figures, per-sentence stances) for sentences carrying figures."""
    bad = 0
    detail = []
    for sent in extract_claims(text):
        report = analyze(sent, llm=None, persist=False)
        stance = report.conclusion.stance if report.conclusion else "not_assessable"
        n = count_figures(sent)
        if stance == "contradicted":
            bad += n
        detail.append({"sentence": sent[:120], "stance": stance, "figures": n})
    return bad, detail


def main() -> dict:
    client = MetaToolClient()
    if not client.available:
        raise SystemExit("MODEL_API_KEY missing; cannot run live demo.")
    result = run_agent(TOPIC, client=client, max_tokens=4000)

    bad_before, det_before = contradicted_figures(result["draft"])
    bad_after, det_after = contradicted_figures(result["final"])
    result["metric"] = {
        "contradicted_figures_before": bad_before,
        "contradicted_figures_after": bad_after,
        "sentences_before": det_before,
        "sentences_after": det_after,
    }

    print("=== DRAFT ===")
    print(result["draft"])
    print(f"\n(figures: {result['figures']['draft']})")
    print("\n=== TOOL CALLS (finish_reasons=%s) ===" % result["finish_reasons"])
    for tc in result["tool_calls"]:
        print(f"- {tc['name']} {tc['arguments'][:160]}")
    print("\n=== RECEIPTS ===")
    for r in result["receipts"]:
        print(f"- [{r['stance']}] {r['claim_text'][:110]} (report {r['report_id']})")
    print("\n=== FINAL ===")
    print(result["final"])
    print(f"\n(figures: {result['figures']['final']})")
    print("\n=== METRIC ===")
    print(f"contradicted figures before: {bad_before}")
    print(f"contradicted figures after:  {bad_after}")
    print("\n=== COST ===")
    print(f"usage: {json.dumps(result['usage'])}")
    print(f"cost_usd: ${result['cost_usd']:.6f} (budget ${BUDGET_USD})")

    assert "tool_calls" in result["finish_reasons"], "no confirmed tool_calls turn!"
    assert result["figures"]["draft"] >= 4, "draft has <4 figures"
    assert result["cost_usd"] <= BUDGET_USD, "over budget"
    print("\nOK: tool calling confirmed, draft>=4 figures, within budget.")
    return result


if __name__ == "__main__":
    main()
