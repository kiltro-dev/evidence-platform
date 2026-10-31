"""Evaluation cases. Expected values use wide margins against live sources.

Live-source caveat: World Bank revises past values and OpenAlex rankings
shift. Cases pin stances that survive small data revisions (see docs/EVAL.md).
"""

CASES = [
    {
        "id": "econ_supported_gdp",
        "text": "The GDP of the United States was $27.36 trillion in 2023.",
        "expected": {
            "domain": "economy",
            "stance": "supported",
            "min_evidences": 1,
            "verdict_present": "consistent",
            "uncertainty_absent": ["no_direct_evidence", "period_mismatch"],
        },
    },
    {
        "id": "econ_supported_unemployment",
        "text": "Unemployment in Germany was 3.1% in 2023.",
        "expected": {
            "domain": "economy",
            "stance": "supported",
            "min_evidences": 1,
            "verdict_present": "consistent",
        },
    },
    {
        "id": "econ_contradicted_population",
        "text": "The population of Japan was 500 million in 2020.",
        "expected": {
            "domain": "economy",
            "stance": "contradicted",
            "min_evidences": 1,
            "verdict_present": "contradicts",
            "uncertainty_present": ["unit_assumed_from_context"],
        },
    },
    {
        "id": "econ_contradicted_wrong_year_value",
        "text": "The GDP of the United States was $27.36 trillion in 1990.",
        "expected": {
            "domain": "economy",
            "stance": "contradicted",
            "min_evidences": 1,
            "verdict_present": "contradicts",
        },
    },
    {
        "id": "econ_unit_mismatch",
        "text": "The GDP of the United States was 27 trillion euros in 2023.",
        "expected": {
            "domain": "economy",
            "stance": "unverifiable",
            "uncertainty_present": ["unit_mismatch"],
        },
    },
    {
        "id": "econ_incomplete_no_quantity",
        "text": "The inflation of Brazil is high.",
        "expected": {
            "domain": "economy",
            "stance": "unverifiable",
            "uncertainty_present": ["no_measurable_quantity"],
        },
    },
    {
        "id": "econ_temporally_vague",
        "text": "Unemployment in France recently fell a lot.",
        "expected": {
            "domain": "economy",
            "stance": "unverifiable",
        },
    },
    {
        "id": "sci_contextual",
        "text": "A 2021 study found that global average temperature rose sharply.",
        "expected": {
            "domain": "science",
            "stance": "partially_supported",
            "min_evidences": 1,
            "uncertainty_present": ["metadata_only"],
        },
    },
    {
        "id": "sci_no_year_match",
        "text": "A 1812 study measured the speed of light precisely.",
        "expected": {
            "domain": "science",
            "stance": "unverifiable",
            "min_evidences": 1,
            "uncertainty_present": ["metadata_only"],
        },
    },
    {
        "id": "general_insufficient",
        "text": "The moon is made of cheese.",
        "expected": {
            "domain": "general",
            "stance": "unverifiable",
        },
    },
]
