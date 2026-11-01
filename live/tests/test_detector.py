"""Detector tests (M1b). Doubles + recorded model outputs only — 0 live calls."""

from live.detector.claims import (
    DETECT_MAX_TOKENS,
    HEADROOM_TOKENS,
    Detector,
    check_headroom,
    claim_id_for,
    fuzzy_similarity,
    normalize,
)


class FakeLLM:
    """Test double for the model client (complete_json interface)."""

    name = "fake-llm"
    available = True

    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def complete_json(self, system, user, max_tokens=1500,
                      reasoning_effort=None, json_schema=None):
        self.calls.append(
            {
                "system": system,
                "user": user,
                "max_tokens": max_tokens,
                "reasoning_effort": reasoning_effort,
                "schema": (json_schema or {}).get("name"),
            }
        )
        return self._script.pop(0)


TURN = {
    "turn": 3,
    "speaker": "Ministra",
    "text": "Su PIB creció un 5,2 por ciento en 2023.",
    "t_start": 7.1,
    "t_end": 11.0,
}
CTX = [
    {"speaker": "Ministra", "text": "España cerró el año con un crecimiento histórico."}
]

# Recorded model output #1: worthiness + coreference ("su PIB" -> España).
RECORDED_COREf = {
    "claims": [
        {
            "text": "Su PIB creció un 5,2 por ciento en 2023.",
            "worth_checking": True,
            "resolved": "El PIB de España creció un 5,2 por ciento en 2023.",
            "reason": "statistic with year",
        }
    ]
}
# Recorded model output #2: not-worthy + non-verbatim spans.
RECORDED_MIXED = {
    "claims": [
        {
            "text": "Buenas noches",
            "worth_checking": False,
            "resolved": None,
            "reason": "greeting",
        },
        {
            "text": "El PIB creció un 9 por ciento",
            "worth_checking": True,
            "resolved": None,
            "reason": "invented span",
        },
    ]
}


def test_model_path_worthiness_and_coreference():
    det = Detector(llm=FakeLLM([RECORDED_COREf]))
    claims, _notes = det.process_turn(TURN, CTX)
    assert len(claims) == 1
    c = claims[0]
    assert c.text == "Su PIB creció un 5,2 por ciento en 2023."
    assert c.resolved == "El PIB de España creció un 5,2 por ciento en 2023."
    assert c.actor == "model" and c.model == "fake-llm"
    assert (c.turn, c.speaker, c.t_start, c.t_end) == (3, "Ministra", 7.1, 11.0)
    assert c.claim_id == claim_id_for(normalize(c.text))


def test_model_call_uses_strict_schema_and_budgets():
    fake = FakeLLM([RECORDED_COREf])
    Detector(llm=fake).process_turn(TURN, CTX)
    call = fake.calls[0]
    assert call["schema"] == "live_claim_detect"
    assert call["max_tokens"] == DETECT_MAX_TOKENS
    # Pinned by LIVE PROBE 2026-11-01 (M1e U1): "low" burns all 800
    # max_tokens in hidden reasoning (finish_reason=length, empty content).
    assert call["reasoning_effort"] == "minimal"


def test_not_worthy_and_nonverbatim_dropped():
    det = Detector(llm=FakeLLM([RECORDED_MIXED]))
    claims, notes = det.process_turn(
        {"turn": 1, "speaker": "X", "text": "Buenas noches, bienvenidos al debate."}
    )
    assert claims == []
    assert any("dropped" in n for n in notes)


def test_unguessed_coreference_dropped_span_survives():
    # Model "resolves" to a country never mentioned: resolution dropped,
    # verbatim span kept.
    out = {
        "claims": [
            {
                "text": "Su PIB creció un 5,2 por ciento en 2023.",
                "worth_checking": True,
                "resolved": "El PIB de Francia creció un 5,2 por ciento en 2023.",
                "reason": "statistic",
            }
        ]
    }
    det = Detector(llm=FakeLLM([out]))
    claims, _notes = det.process_turn(TURN, CTX)  # CTX names España, not Francia
    assert len(claims) == 1
    assert claims[0].resolved is None


def test_dedup_exact_and_fuzzy_repeats_not_reemitted():
    det = Detector(llm=FakeLLM([RECORDED_COREf, RECORDED_COREf]))
    first, _ = det.process_turn(TURN, CTX)
    assert len(first) == 1
    # Exact repeat next turn -> nothing new.
    second, notes = det.process_turn(TURN, CTX)
    assert second == []
    assert any("dropped" in n for n in notes)


def test_dedup_fuzzy_near_duplicate():
    det = Detector()
    det._recent.append((normalize("el desempleo bajo al 11 por ciento"), "clm_x"))
    assert (
        det._find_duplicate(normalize("El desempleo bajó al 11 por ciento!"))
        == "clm_x"
    )
    assert det._find_duplicate(normalize("el pib crecio un 5 por ciento")) is None


def test_heuristic_path_numbers_kept_greetings_dropped():
    det = Detector(llm=None)
    claims, _notes = det.process_turn(
        {
            "turn": 4,
            "speaker": "Ministra",
            "text": "Buenas noches. El desempleo bajó al 11,8 por ciento en 2023.",
            "t_start": 11.0,
            "t_end": 15.4,
        }
    )
    assert [c.text for c in claims] == [
        "El desempleo bajó al 11,8 por ciento en 2023."
    ]
    assert claims[0].actor == "heuristic"
    assert claims[0].resolved is None  # heuristic never resolves


def test_model_failure_falls_back_to_heuristic():
    class Broken(FakeLLM):
        def complete_json(self, *a, **k):
            raise RuntimeError("boom")

    det = Detector(llm=Broken([]))
    claims, notes = det.process_turn(TURN, CTX)
    assert len(claims) == 1 and claims[0].actor == "heuristic"
    assert any("heuristic" in n for n in notes)


def test_headroom_check_requires_1500_tokens():
    assert HEADROOM_TOKENS >= 1500
    ok, remaining = check_headroom(prompt_chars=2000)
    assert ok and remaining > 0
    ok_tiny, _ = check_headroom(
        prompt_chars=2000, max_tokens=800, context_tokens=1000
    )
    assert not ok_tiny  # budget honestly refuses an impossible fit


def test_normalize_and_fuzzy_helpers():
    assert normalize("¡Su PIB creció 5%!") == "su pib crecio 5"
    assert fuzzy_similarity("El PIB creció", "El PIB creció") == 1.0
    assert fuzzy_similarity("El PIB creció un 5%", "el pib crecio un 5") > 0.85
    assert fuzzy_similarity("El PIB creció", "Buenas noches amigos") < 0.5
