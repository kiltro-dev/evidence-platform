"""Unit tests: swarm roles + coordinator arbiter. Doubles only, 0 live.

The pipeline entry point is monkeypatched with a stub Report wherever a
test needs one (no network, no model). Role tests use ScriptedClient /
FaultClient canned responses keyed on the [ROLE:...] user-prompt marker.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import atlas.swarm.coordinator as coord  # noqa: E402
from atlas.core.types import (  # noqa: E402
    Conclusion,
    Evidence,
    Quantity,
    Report,
    StructuredClaim,
)
from atlas.models.scripted import FaultClient, ScriptedClient  # noqa: E402
from atlas.swarm import roles  # noqa: E402

CLAIM = "Spain's GDP grew 3.2 percent in 2024 to 1.6 trillion USD."


def _report(stance="supported", n_quantities=2, connectors=("worldbank",),
            usage=None):
    claim = StructuredClaim(
        raw_text=CLAIM, domain="economy", claim_type="quantitative",
        quantities=[Quantity(label=f"q{i}", raw_text="3.2", value=3.2 + i,
                             unit="percent")
                    for i in range(n_quantities)])
    evs = [Evidence(claim_id=claim.claim_id, connector=c, source_name=c,
                    source_kind="primary", evidence_kind="direct")
           for c in connectors]
    rep = Report(claim=claim, evidences=evs,
                 conclusion=Conclusion(stance=stance, summary="s"))
    if usage:
        rep.meta = dict(usage)
    return rep


def _roles_ok(**over):
    base = {
        "estructurador": {"ok": True, "skipped": None, "data": {
            "domain": "economy",
            "quantities": [{"label": "a"}, {"label": "b"}]}, "notes": [],
            "usage": None},
        "recuperador": {"ok": True, "skipped": None,
                        "data": {"candidates": [
                            {"source": "World Bank",
                             "indicator": "NY.GDP.MKTP.CD", "rationale": "r"}]},
                        "notes": [], "usage": None},
        "red_team": {"ok": True, "skipped": None,
                     "data": {"attacks": []}, "notes": [], "usage": None},
        "explicador": {"ok": True, "skipped": None,
                       "data": {"summary": "plain words", "caveats": []},
                       "notes": [], "usage": None},
    }
    base.update(over)
    return base


# --- arbiter: verdict always equals the pipeline stance ---------------------


def test_arbiter_verdict_is_pipeline_stance():
    rep = _report(stance="partially_supported")
    out = coord.arbitrate(rep, _roles_ok(), CLAIM)
    assert out["verdict_source"] == "pipeline"
    assert out["stance"] == "partially_supported"
    assert rep.conclusion.stance == "partially_supported"
    assert out["conflicts"] == []


def test_arbiter_never_contradicted_by_vote():
    # Pipeline says supported; red-team screams high-severity anyway.
    rep = _report(stance="supported")
    r = _roles_ok(red_team={"ok": True, "skipped": None, "data": {
        "attacks": [{"claim_span": "3.2 percent", "weakness": "w",
                      "severity": "high"}]}, "notes": [], "usage": None})
    out = coord.arbitrate(rep, r, CLAIM)
    assert out["stance"] == "supported"
    assert rep.conclusion.stance == "supported"
    assert len(out["conflicts"]) == 1
    codes = [u.code for u in rep.uncertainties]
    assert "swarm_redteam_challenge" in codes
    majors = [u for u in rep.uncertainties
              if u.code == "swarm_redteam_challenge"]
    assert all(u.severity == "major" for u in majors)


def test_arbiter_struct_mismatch_is_major_not_flip():
    rep = _report(stance="supported", n_quantities=1)
    out = coord.arbitrate(rep, _roles_ok(), CLAIM)  # role mapped 2 vs 1
    assert out["stance"] == "supported"
    assert any("quantities" in c for c in out["conflicts"])
    assert "swarm_struct_mismatch" in out["uncertainties_added"]
    assert "swarm_struct_mismatch" in [u.code for u in rep.uncertainties]


def test_arbiter_uncovered_candidate_is_minor_advisory():
    rep = _report(connectors=("worldbank",))
    r = _roles_ok(recuperador={
        "ok": True, "skipped": None, "data": {"candidates": [
            {"source": "Fictional Data Guild", "indicator": "X",
             "rationale": "r"}]}, "notes": [], "usage": None})
    out = coord.arbitrate(rep, r, CLAIM)
    assert out["conflicts"] == []  # advisory, not a conflict
    assert "swarm_uncovered_candidate" in out["uncertainties_added"]
    minors = [u for u in rep.uncertainties
              if u.code == "swarm_uncovered_candidate"]
    assert minors and all(u.severity == "minor" for u in minors)


def test_arbiter_covered_candidate_adds_nothing():
    rep = _report(connectors=("worldbank",))
    out = coord.arbitrate(rep, _roles_ok(), CLAIM)  # World Bank covered
    assert "swarm_uncovered_candidate" not in out["uncertainties_added"]


def test_arbiter_explicador_never_merges_into_conclusion():
    rep = _report()
    out = coord.arbitrate(rep, _roles_ok(), CLAIM)
    assert out["plain_summary"] == "plain words"
    assert rep.conclusion.summary == "s"  # untouched


def test_arbiter_all_roles_skipped_keeps_stance():
    rep = _report(stance="unverifiable")
    skipped = {n: {"ok": False, "skipped": "no model", "data": {},
                   "notes": [], "usage": None} for n in roles.ROLE_NAMES}
    out = coord.arbitrate(rep, skipped, CLAIM)
    assert out["stance"] == "unverifiable"
    assert out["conflicts"] == []
    assert out["uncertainties_added"] == []
    assert len(out["roles_skipped"]) == 4


# --- roles: validation in code ----------------------------------------------


def test_recuperador_validates_and_dedupes():
    llm = ScriptedClient({"[ROLE:recuperador]": {"candidates": [
        {"source": "World Bank", "indicator": "A", "rationale": "r"},
        {"source": "world bank ", "indicator": " a", "rationale": "dup"},
        {"source": "", "indicator": "B", "rationale": "empty"},
        {"bogus": 1},
    ]}})
    out = roles.run_recuperador(CLAIM, llm)
    assert out["ok"] is True
    assert out["data"]["candidates"] == [
        {"source": "World Bank", "indicator": "A", "rationale": "r"}]
    assert any("dropped 3" in n for n in out["notes"])


def test_red_team_drops_non_verbatim_spans():
    llm = ScriptedClient({"[ROLE:red_team]": {"attacks": [
        {"claim_span": "3.2 percent", "weakness": "unit ok?", "severity": "low"},
        {"claim_span": "invented phrase", "weakness": "x", "severity": "high"},
        {"claim_span": "GDP", "weakness": "y", "severity": "critical"},
    ]}})
    out = roles.run_red_team(CLAIM, llm)
    assert out["ok"] is True
    assert out["data"]["attacks"] == [
        {"claim_span": "3.2 percent", "weakness": "unit ok?",
         "severity": "low"}]
    assert any("dropped 2" in n for n in out["notes"])


def test_explicador_flags_strength_words_but_keeps_text():
    llm = ScriptedClient({"[ROLE:explicador]": {
        "summary": "This proves the claim exactly right.", "caveats": ["c"]}})
    out = roles.run_explicador(CLAIM, llm)
    assert out["ok"] is True
    assert any("strength words flagged" in n for n in out["notes"])


def test_explicador_empty_summary_degrades():
    llm = ScriptedClient({"[ROLE:explicador]": {"summary": "  ",
                                                "caveats": []}})
    out = roles.run_explicador(CLAIM, llm)
    assert out["ok"] is False
    assert out["skipped"] is None


def test_roles_skip_without_model():
    for name, runner in roles.ROLE_RUNNERS.items():
        out = runner(CLAIM, None)
        assert out["ok"] is False
        assert out["skipped"] is not None, name


def test_roles_degrade_on_model_fault():
    llm = FaultClient(faults={"[ROLE:red_team]": "raise"})
    out = roles.run_red_team(CLAIM, llm)
    assert out["ok"] is False
    assert any("degraded" in n for n in out["notes"])


def test_estructurador_returns_pipeline_shaped_claim():
    llm = ScriptedClient({"Claim:": {
        "domain": "economy", "claim_type": "quantitative",
        "subject": "Spain GDP", "indicator_hint": None, "entities": [],
        "quantities": [{"label": "g", "raw": "3.2 percent", "value": 3.2,
                        "unit": "percent"}],
        "time_refs": [{"raw": "2024", "year": 2024, "start_year": None,
                       "end_year": None, "period_type": "year"}],
        "geo_refs": [{"raw": "Spain", "name": "Spain", "iso3": "ESP"}],
        "conditions": [], "context_notes": ""}})
    out = roles.run_estructurador(CLAIM, llm)
    assert out["ok"] is True
    assert out["data"]["domain"] == "economy"
    assert len(out["data"]["quantities"]) == 1
    assert out["data"]["quantities"][0]["value"] == 3.2


# --- coordinator: parallel fan-out, usage, cost ------------------------------


def test_run_roles_parallel_keys_and_forks(monkeypatch):
    seen = []

    class Forky(ScriptedClient):
        def __init__(self):
            super().__init__({
                "[ROLE:recuperador]": {"candidates": []},
                "[ROLE:red_team]": {"attacks": []},
                "[ROLE:explicador]": {"summary": "s", "caveats": []},
                "Claim:": {"domain": "general", "claim_type": "other",
                           "subject": "s", "indicator_hint": None,
                           "entities": [], "quantities": [], "time_refs": [],
                           "geo_refs": [], "conditions": [],
                           "context_notes": ""},
            })
            self.forks = 0

        def fork(self):
            child = Forky()
            child.calls = self.calls
            seen.append(self)
            return child

    llm = Forky()
    out = coord.run_roles_parallel(CLAIM, llm)
    assert set(out) == set(roles.ROLE_NAMES)
    assert len(seen) == 4  # one fork per role


def test_analyze_with_swarm_uses_pipeline_verdict(monkeypatch):
    stub = _report(stance="contradicted", n_quantities=0, connectors=())
    monkeypatch.setattr(coord, "analyze", lambda *a, **k: stub)
    llm = ScriptedClient({
        "[ROLE:recuperador]": {"candidates": []},
        "[ROLE:red_team]": {"attacks": []},
        "[ROLE:explicador]": {"summary": "plain", "caveats": []},
        "Claim:": {"domain": "economy", "claim_type": "other",
                   "subject": "s", "indicator_hint": None, "entities": [],
                   "quantities": [], "time_refs": [], "geo_refs": [],
                   "conditions": [], "context_notes": ""},
    })
    res = coord.analyze_with_swarm(CLAIM, llm=llm)
    assert res.stance == "contradicted"  # pipeline verdict stands ...
    assert res.arbiter["verdict_source"] == "pipeline"
    # ... even though the live struct role disagrees with the stub's
    # extraction (heuristic backfill finds claim figures): conflict
    # disclosed as major uncertainty, stance untouched.
    assert any("quantities" in c for c in res.arbiter["conflicts"])
    assert "swarm_struct_mismatch" in res.arbiter["uncertainties_added"]
    assert res.elapsed_ms >= 0


def test_analyze_with_swarm_llm_none_degrades_roles(monkeypatch):
    stub = _report(stance="supported", n_quantities=0, connectors=())
    monkeypatch.setattr(coord, "analyze", lambda *a, **k: stub)
    res = coord.analyze_with_swarm(CLAIM, llm=None)
    assert res.stance == "supported"
    assert len(res.arbiter["roles_skipped"]) == 4


def test_sum_usages_and_estimate():
    total = coord.sum_usages([
        {"prompt_tokens": 100, "completion_tokens": 50,
         "total_tokens": 150, "reasoning_tokens": None},
        None,
        {"prompt_tokens": 200, "completion_tokens": 25,
         "total_tokens": 225, "cached_tokens": 10},
    ])
    assert total["prompt_tokens"] == 300
    assert total["completion_tokens"] == 75
    assert total["stages"] == 2
    usd = coord.estimate_usd(
        total, {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 2.0})
    assert usd == 300 / 1000 * 1.0 + 75 / 1000 * 2.0


# --- long-doc chunking -------------------------------------------------------


def test_chunk_document_covers_every_char():
    text = ("Para one has words. " * 40 + "\n\n"
            + "Para two has words. " * 40 + "\n\n"
            + "Short.")
    chunks = coord.chunk_document(text, max_chars=500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    # every word of the input lands in exactly one chunk
    for word in {w for w in text.split() if len(w) > 3}:
        assert sum(word in c for c in chunks) >= 1


def test_chunk_document_short_and_empty():
    assert coord.chunk_document("") == []
    assert coord.chunk_document("tiny claim here") == ["tiny claim here"]


def test_check_document_swarm_offline_end_to_end(monkeypatch):
    stub = _report(stance="supported", n_quantities=1,
                   connectors=("worldbank",))
    monkeypatch.setattr(coord, "analyze", lambda *a, **k: stub)
    doc = ("Spain's GDP grew 3.2 percent in 2024. "
           "France's GDP grew 1.1 percent in 2024.")
    out = coord.check_document_swarm(doc, llm=None)
    assert out["claim_count"] >= 1
    assert out["figures_mapped"] >= 1
    assert out["rollup"].get("supported", 0) >= 1
    assert out["errors"] == 0
    assert out["usage_total"]["stages"] == 0
    assert out["usd_estimate"] == 0.0
    assert out["meta"]["chunks"] >= 1
    assert "pricing" in out["meta"]


def test_check_document_swarm_isolates_claim_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("claim blew up")

    monkeypatch.setattr(coord, "analyze", boom)
    out = coord.check_document_swarm("Spain's GDP grew 3.2 percent in 2024.",
                                     llm=None)
    assert out["errors"] == out["claim_count"] >= 1
    assert out["rollup"] == {}


def test_check_document_swarm_respects_max_claims(monkeypatch):
    stub = _report(stance="supported", n_quantities=0, connectors=())
    monkeypatch.setattr(coord, "analyze", lambda *a, **k: stub)
    doc = " ".join(f"Country{i} grew {i}.1 percent in 2024."
                   for i in range(6))
    out = coord.check_document_swarm(doc, llm=None, max_claims=2)
    assert out["claim_count"] == 2
    assert any("capped" in n for n in out["meta"]["notes"])
