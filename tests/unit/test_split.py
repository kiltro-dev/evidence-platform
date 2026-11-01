"""Unit tests: claim splitter (model path + heuristic fallback)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.models.scripted import FaultClient, ScriptedClient  # noqa: E402
from atlas.pipeline.split import (  # noqa: E402
    MAX_SUBCLAIMS,
    SPLIT_INPUT_CHARS,
    SPLIT_SCHEMA,
    split_claims,
    split_heuristic,
)


class RecordingClient(ScriptedClient):
    """Captures complete_json kwargs to assert schema wiring."""

    def __init__(self, responses, default=None):
        super().__init__(responses, default)
        self.kwargs: list[dict] = []

    def complete_json(self, system, user, max_tokens=1500,
                      reasoning_effort=None, json_schema=None):
        self.kwargs.append({"json_schema": json_schema,
                            "reasoning_effort": reasoning_effort})
        return super().complete_json(
            system, user, max_tokens, reasoning_effort, json_schema)


TEXT = "US GDP reached $27.36 trillion in 2023. Unemployment was 3.1% that year."


def _model(text=TEXT, spans=None):
    spans = spans if spans is not None else [
        "US GDP reached $27.36 trillion in 2023.",
        "Unemployment was 3.1% that year.",
    ]
    return ScriptedClient(
        {"Text:": {"claims": [{"text": s} for s in spans]}}, default={"claims": []}
    )


def test_model_path_keeps_verbatim_spans():
    claims, actor, model, notes = split_claims(TEXT, _model())
    assert actor == "model"
    assert model == "scripted-test-double"
    assert claims == [
        "US GDP reached $27.36 trillion in 2023.",
        "Unemployment was 3.1% that year.",
    ]


def test_model_path_passes_strict_schema():
    llm = RecordingClient({"Text:": {"claims": [{"text": TEXT}]}})
    split_claims(TEXT, llm)
    assert len(llm.kwargs) == 1
    assert llm.kwargs[0]["json_schema"] == SPLIT_SCHEMA
    assert llm.kwargs[0]["reasoning_effort"] == "low"


def test_invented_spans_dropped_with_note():
    llm = _model(spans=["US GDP reached $27.36 trillion in 2023.",
                        "Inflation hit 9% in 2024."])  # not in input
    claims, actor, _, notes = split_claims(TEXT, llm)
    assert actor == "model"
    assert claims == ["US GDP reached $27.36 trillion in 2023."]
    assert any("dropped 1" in n for n in notes)


def test_duplicates_and_empties_dropped():
    llm = _model(spans=["US GDP reached $27.36 trillion in 2023.",
                        "US GDP reached  $27.36  trillion in 2023.",
                        "   ", "Unemployment was 3.1% that year."])
    claims, _, _, notes = split_claims(TEXT, llm)
    assert claims == ["US GDP reached $27.36 trillion in 2023.",
                      "Unemployment was 3.1% that year."]
    assert any("dropped 2" in n for n in notes)


def test_model_path_drops_fragments():
    # Short spans ("2023.", "US") pass the verbatim check but assert
    # nothing; the model path honors the same floor as the heuristic.
    text = "US GDP was 5% in 2023. That year mattered a lot."
    llm = _model(text, ["2023.", "US", "US GDP was 5% in 2023."])
    claims, actor, _, notes = split_claims(text, llm)
    assert actor == "model"
    assert claims == ["US GDP was 5% in 2023."]
    assert any("dropped 2" in n for n in notes)


def test_model_output_capped():
    long_text = " ".join(f"Fact number {i} states 1% in 2020." for i in range(12))
    spans = [f"Fact number {i} states 1% in 2020." for i in range(12)]
    claims, actor, _, notes = split_claims(long_text, _model(long_text, spans))
    assert actor == "model"
    assert len(claims) == MAX_SUBCLAIMS
    assert any("truncated split" in n for n in notes)


def test_model_total_failure_falls_back_to_heuristic():
    llm = _model(spans=["completely invented claim"])  # all dropped
    claims, actor, model, notes = split_claims(TEXT, llm)
    assert actor == "heuristic" and model is None
    assert len(claims) == 2  # heuristic sentence split still works
    assert any("heuristic used instead" in n for n in notes)


def test_transport_failure_falls_back_to_heuristic():
    llm = FaultClient(faults={"Text:": "raise"})
    claims, actor, _, notes = split_claims(TEXT, llm)
    assert actor == "heuristic"
    assert len(claims) == 2
    assert any("failed; heuristic" in n for n in notes)


def test_no_llm_uses_heuristic():
    claims, actor, model, notes = split_claims(TEXT, None)
    assert (actor, model) == ("heuristic", None)
    assert claims == ["US GDP reached $27.36 trillion in 2023.",
                      "Unemployment was 3.1% that year."]
    assert any("no model available" in n for n in notes)


def test_heuristic_splits_sentences_verbatim():
    text = "First claim here, with numbers 5%.\nSecond claim follows in 2021! Hi."
    out, notes = split_heuristic(text)
    assert out == ["First claim here, with numbers 5%.",
                   "Second claim follows in 2021!"]
    assert notes == ["dropped 1 too-short segment(s)"]  # F8: "Hi." disclosed
    for seg in out:  # exact substrings, never invented
        assert seg in text


def test_heuristic_caps_and_drops_fragments():
    text = " ".join([f"Long enough sentence number {i} ok. Hi."
                     for i in range(10)])
    out, notes = split_heuristic(text)
    assert len(out) == MAX_SUBCLAIMS
    assert all(len(s) >= 12 for s in out)
    assert notes == ["dropped 10 too-short segment(s)",
                     f"truncated split from 10 to {MAX_SUBCLAIMS}"]


def test_heuristic_abbreviation_guard():
    # Red-team F1: "U.S." must not split geo from quantity.
    text = ("The GDP of the U.S. was $27.36 trillion in 2023. "
            "The population of Japan was 125 million in 2023.")
    out, _ = split_heuristic(text)
    assert out == ["The GDP of the U.S. was $27.36 trillion in 2023.",
                   "The population of Japan was 125 million in 2023."]
    for seg in out:
        assert seg in text  # masking restores exact substrings
    out2, _ = split_heuristic(
        "GDP rose, e.g. in 2021 greatly. Something else happened here.")
    assert out2 == ["GDP rose, e.g. in 2021 greatly.",
                    "Something else happened here."]


def test_heuristic_truncation_note_reaches_batch():
    # Red-team F2: silent 12-claim drops are a coverage lie.
    text = " ".join(f"Numbered economy sentence {i} recorded." for i in range(12))
    claims, actor, _, notes = split_claims(text, None)
    assert actor == "heuristic"
    assert len(claims) == MAX_SUBCLAIMS
    assert any("truncated split from 12 to" in n for n in notes)


def test_long_input_truncated_with_note():
    text = ("A sufficiently long sentence with a 5% figure inside. "
            * (SPLIT_INPUT_CHARS // 50 + 10))
    claims, actor, _, notes = split_claims(text, None)
    assert actor == "heuristic"
    assert any("truncated to" in n for n in notes)
    assert len(claims) <= MAX_SUBCLAIMS


def test_heuristic_too_short_note_reaches_batch():
    # Red-team F8: short-sentence drops surface in splitter_notes.
    text = "It grew 5% this year. Wow! The GDP of France was $3 trillion in 2023."
    claims, actor, _, notes = split_claims(text, None)
    assert actor == "heuristic"
    assert len(claims) == 2
    assert any("dropped 1 too-short" in n for n in notes)


def test_input_truncation_names_rollup_scope():
    # Red-team F9: the receipt says the roll-up covers analyzed claims only.
    text = ("A sufficiently long sentence with a 5% figure inside. "
            * (SPLIT_INPUT_CHARS // 50 + 10))
    _, _, _, notes = split_claims(text, None)
    assert any("roll-up covers analyzed claims only" in n for n in notes)


def test_split_with_context_carries_country():
    # UT-g (g1/g2/g3): verbatim spans + resolved country apart.
    from atlas.pipeline.split import split_with_context

    spans, ctxs, _, _ = split_with_context(
        "France's population is 68 million. Its GDP is $3 trillion.",
        llm=None)
    assert len(spans) == 2
    assert spans[1] == "Its GDP is $3 trillion."
    assert ctxs[1].get("country_iso3") == "FRA"
    assert ctxs[1].get("country_source") == "inherited"
    assert ctxs[0].get("country_source") == "own"


def test_split_context_own_beats_inherited():
    from atlas.pipeline.split import split_with_context

    spans, ctxs, _, _ = split_with_context(
        "France's population is 68 million. Germany's GDP is $4 trillion.",
        llm=None)
    assert ctxs[1].get("country_iso3") == "DEU"
    assert ctxs[1].get("country_source") == "own"


def test_split_context_no_antecedent_invents_nothing():
    from atlas.pipeline.split import split_with_context

    spans, ctxs, _, _ = split_with_context("Its GDP is $3 trillion.",
                                           llm=None)
    assert len(spans) == 1
    assert "country_iso3" not in ctxs[0]


def test_split_context_years_own_and_inherited():
    from atlas.pipeline.split import split_with_context

    spans, ctxs, _, _ = split_with_context(
        "GDP was $27 trillion in 2020. It grew steadily after that.",
        llm=None)
    assert ctxs[0].get("years") == [2020]
    assert ctxs[0].get("years_source") == "own"
    assert ctxs[1].get("years") == [2020]
    assert ctxs[1].get("years_source") == "inherited"


def test_split_context_spanish_anaphora():
    from atlas.pipeline.split import split_with_context

    spans, ctxs, _, _ = split_with_context(
        "La población de Francia es 68 millones. Su PIB es $3 billones.",
        llm=None)
    assert len(spans) == 2
    assert ctxs[1].get("country_iso3") == "FRA"
