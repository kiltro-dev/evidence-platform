"""Falsification stage tests (advisory self-review). Offline:
ScriptedClient / FaultClient + constructed objects only. No live calls.

Advisory-only contract (lead decision 2026-11-01): attacks surface as
doubt (inconclusive comparisons + uncertainties + audit) and NEVER move
stances — a measured live run showed fatal+high false alarms on every
decisive draft. These tests pin that contract.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.core.types import (  # noqa: E402
    Comparison,
    StructuredClaim,
    Uncertainty,
)
from atlas.models.scripted import FaultClient, ScriptedClient  # noqa: E402
from atlas.pipeline.falsify import falsify_draft  # noqa: E402
from atlas.pipeline.synthesize import decide_stance, synthesize  # noqa: E402


def _claim(**kw) -> StructuredClaim:
    base = dict(raw_text="GDP grew 5% in 2023", domain="economy",
                claim_type="quantitative")
    base.update(kw)
    return StructuredClaim(**base)


def _cmp(verdict: str = "consistent", cid: str = "cmp_real",
         metric: str = "quantity_match") -> Comparison:
    return Comparison(  # type: ignore[arg-type]
        claim_id="c", comparison_id=cid, metric=metric, verdict=verdict,
        notes="value matches source",
    )


def _unc(code: str = "x", severity: str = "minor") -> Uncertainty:
    return Uncertainty(code=code, description=f"{code} happened",  # type: ignore[arg-type]
                       severity=severity)


def _attack(cid="cmp_real", atype="wrong_unit", sev="fatal", conf="high",
            arg="units mismatch: claim is USD, source is EUR"):
    return {"target_comparison_id": cid, "attack_type": atype,
            "severity": sev, "confidence": conf, "argument": arg}


# --- advisory surfacing (no stance consequences, by design) ---


def test_valid_fatal_high_surfaces_doubt_without_moving_stance():
    llm = ScriptedClient({}, default={"attacks": [_attack()],
                                      "overturn_recommended": True})
    claim, cmps = _claim(), [_cmp()]
    new_c, new_u, audit = falsify_draft(claim, cmps, [], "supported", llm)
    assert len(new_c) == 1
    assert new_c[0].metric == "falsification_review"
    assert new_c[0].actor == "model"
    assert new_c[0].verdict == "inconclusive"  # flags doubt, casts no vote
    assert "cmp_real" in new_c[0].claim_value
    assert "wrong_unit" in new_c[0].notes
    assert len(new_u) == 1
    assert new_u[0].severity == "major"  # never blocking
    assert new_u[0].code == "falsification_flag"
    assert new_u[0].related_ids == ["cmp_real"]
    assert audit.step == "falsify"
    assert "advisory_only" in audit.detail
    # The decisive assertion: even a fatal+high attack leaves the
    # code-decided stance untouched.
    assert decide_stance(cmps + new_c, new_u, claim) == "supported"
    assert synthesize(claim, cmps + new_c, new_u, llm=None).stance == \
        "supported"


def test_unresolvable_ids_dropped():
    llm = ScriptedClient({}, default={"attacks": [_attack(cid="cmp_nope")],
                                      "overturn_recommended": True})
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm)
    assert (new_c, new_u) == ([], [])
    assert "attacks=0" in audit.detail


def test_unknown_enums_dropped():
    bad = [_attack(atype="bogus_type"), _attack(sev="catastrophic"),
           _attack(conf="certain"), "not-a-dict", {"attack_type": "wrong_unit"}]
    llm = ScriptedClient({}, default={"attacks": bad,
                                      "overturn_recommended": True})
    new_c, new_u, _ = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm)
    assert (new_c, new_u) == ([], [])


def test_unknown_top_level_keys_ignored():
    # Legacy "overturn_recommended" (removed from the schema): volunteered
    # keys must not appear in the audit nor affect anything.
    llm = ScriptedClient({}, default={"attacks": [_attack(sev="serious")],
                                      "overturn_recommended": True})
    claim, cmps = _claim(), [_cmp()]
    new_c, new_u, audit = falsify_draft(claim, cmps, [], "supported", llm)
    assert "overturn" not in audit.detail
    assert len(new_c) == 1
    assert decide_stance(cmps, new_u, claim) == "supported"


def test_serious_attack_adds_minor_uncertainty_only():
    llm = ScriptedClient({}, default={"attacks": [_attack(sev="serious")],
                                      "overturn_recommended": False})
    new_c, new_u, _ = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm)
    assert len(new_c) == 1 and len(new_u) == 1
    assert new_u[0].severity == "minor"


def test_nit_attack_comparison_only():
    llm = ScriptedClient({}, default={"attacks": [_attack(sev="nit")]})
    new_c, new_u, _ = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm)
    assert len(new_c) == 1 and new_u == []


def test_degraded_noop_llm_none():
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", None)
    assert (new_c, new_u) == ([], [])
    assert audit.step == "falsify" and "skipped" in audit.detail


def test_degraded_noop_llm_unavailable():
    class Down:
        name = "down"
        available = False

        def complete_json(self, system, user, max_tokens=1500, **kw):
            raise AssertionError("must not be called")
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", Down())
    assert (new_c, new_u) == ([], [])
    assert "skipped" in audit.detail


def test_invalid_json_shapes_dropped_safely():
    for default in ({"bogus": 1}, {"attacks": "not-a-list"},
                    {"attacks": None}, {}, {"attacks": [{"bogus": 1}]}):
        llm = ScriptedClient({}, default=default)
        new_c, new_u, audit = falsify_draft(
            _claim(), [_cmp()], [], "supported", llm)
        assert (new_c, new_u) == ([], []), default
        assert audit.step == "falsify"


def test_model_transport_failure_dropped_safely():
    llm = FaultClient(faults={"GDP": "raise"})
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", llm)
    assert (new_c, new_u) == ([], [])
    assert "skipped" in audit.detail


def test_non_dict_model_output_dropped_safely():
    class Weird(ScriptedClient):
        def complete_json(self, system, user, max_tokens=1500, **kw):
            return ["not", "a", "dict"]  # type: ignore[return-value]
    new_c, new_u, audit = falsify_draft(
        _claim(), [_cmp()], [], "supported", Weird({}))
    assert (new_c, new_u) == ([], [])
    assert "skipped" in audit.detail


# --- scripted-client integration: falsify -> synthesize ---


def test_integration_fatal_attack_leaves_supported_stance():
    llm = ScriptedClient({}, default={"attacks": [_attack()],
                                      "overturn_recommended": True})
    claim, cmps, uncs = _claim(), [_cmp()], [_unc()]
    draft = decide_stance(cmps, uncs, claim)
    assert draft == "supported"
    new_c, new_u, _ = falsify_draft(claim, cmps, uncs, draft, llm)
    all_c, all_u = cmps + new_c, uncs + new_u
    # Falsify artifacts surface doubt but must not flip code stance rules.
    assert decide_stance(all_c, all_u, claim) == "supported"
    conclusion = synthesize(claim, all_c, all_u, llm=None)
    assert conclusion.stance == "supported"
    assert any(u.severity == "major" and u.code == "falsification_flag"
               for u in all_u)


def test_integration_clean_review_leaves_stance_untouched():
    llm = ScriptedClient({}, default={"attacks": [],
                                      "overturn_recommended": False})
    claim, cmps, uncs = _claim(), [_cmp()], []
    new_c, new_u, _ = falsify_draft(
        claim, cmps, uncs, "supported", llm)
    assert (new_c, new_u) == ([], [])
    assert synthesize(claim, cmps, uncs, llm=None).stance == "supported"
