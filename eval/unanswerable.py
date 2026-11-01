"""E2 abstention probe (M6 eval expansion): 20 unanswerable variants.

Every variant SHOULD abstain (stance in {unverifiable, not_assessable});
the probe reports the abstention rate (target 1.0) plus per-variant
stances and abstention codes. Variants that conclude anyway are
false-conclusion findings: they stay in the probe (failing) and are
reported, never silently dropped.

Categories: unknown geos (3), future years (2), unparsed units (3),
opinions/questions (5), empty (2), compound-with-unretrievable-half
(2), misc abstentions (3: missing year, geo-less, vague-temporal).

All variants are offline (scripted evidence, llm=None). Known current
findings (verified 2026-11-01, see eval/NOTES_M6.md):
- e2_question_with_quantity concludes supported (interrogative with a
  matching quantity is answered as an assertion);
- e2_compound_half_vague concludes supported with NO caveat (the vague
  half is silently ignored; only 1 real quantity -> no compound flag);
- e2_compound_half_unretrievable concludes (any verified half decides
  the compound; harness pairs both halves with the canned evidence).

Each variant: id, text, script (harness spec, same keys as cases.py),
expected_abstention: codes where >=1 must be present for the abstention
check (informational when the variant concludes).
"""

UNANSWERABLE = [
    # --- unknown geos (3) ---
    {
        "id": "e2_geo_atlantis_gdp",
        "text": "The GDP of Atlantis was $2 trillion in 2020.",
        "script": {"evidence": []},
        "expected_abstention": ["no_direct_evidence"],
    },
    {
        "id": "e2_geo_ruritania_unemp",
        "text": "Unemployment in Ruritania was 5% in 2023.",
        "script": {"evidence": []},
        "expected_abstention": ["no_direct_evidence"],
    },
    {
        "id": "e2_geo_narnia_flurbles",
        "text": "The GDP of Narnia was 5 flurbles in 2020.",
        "script": {"evidence": []},
        "expected_abstention": ["no_direct_evidence"],
    },
    # --- future years (2): canned evidence is 2023-only -> period_mismatch ---
    {
        "id": "e2_future_gdp_2030",
        "text": "The GDP of the United States will be $30 trillion in 2030.",
        "script": {
            "evidence": [
                {
                    "connector": "worldbank",
                    "label": "GDP (current US$)",
                    "unit": "USD",
                    "year": "2023",
                    "value": 27.81e12,
                }
            ]
        },
        "expected_abstention": ["period_mismatch"],
    },
    {
        "id": "e2_future_unemp_2030",
        "text": "Unemployment in Germany will be 2% in 2030.",
        "script": {
            "evidence": [
                {
                    "connector": "worldbank",
                    "label": "Unemployment",
                    "unit": "percent",
                    "year": "2023",
                    "value": 3.1,
                }
            ]
        },
        "expected_abstention": ["period_mismatch"],
    },
    # --- unparsed units (3) ---
    {
        "id": "e2_unit_basis_points",
        "text": "Unemployment in Germany was 310 basis points in 2023.",
        "script": {
            "evidence": [
                {
                    "connector": "worldbank",
                    "label": "Unemployment",
                    "unit": "percent",
                    "year": "2023",
                    "value": 3.1,
                }
            ]
        },
        "expected_abstention": ["unit_mismatch"],
    },
    {
        "id": "e2_unit_furlongs",
        "text": "The GDP of the United States was 5 furlongs in 2023.",
        "script": {
            "evidence": [
                {
                    "connector": "worldbank",
                    "label": "GDP (current US$)",
                    "unit": "USD",
                    "year": "2023",
                    "value": 27.81e12,
                }
            ]
        },
        "expected_abstention": ["unit_mismatch"],
    },
    {
        "id": "e2_unit_word_number",
        "text": "Unemployment in Germany was five percent in 2023.",
        "script": {"evidence": []},
        "expected_abstention": ["no_measurable_quantity"],
    },
    # --- opinions / questions (5) ---
    {
        "id": "e2_opinion_democracy",
        "text": "Democracy is the best form of government.",
        "script": {"evidence": []},
        "expected_abstention": ["opinion_or_nonclaim"],
    },
    {
        "id": "e2_opinion_with_number",
        "text": (
            "I think 3.1% unemployment in Germany in 2023 is the best outcome."
        ),
        "script": {
            "evidence": [
                {
                    "connector": "worldbank",
                    "label": "Unemployment",
                    "unit": "percent",
                    "year": "2023",
                    "value": 3.1,
                }
            ]
        },
        "expected_abstention": ["opinion_or_nonclaim"],
    },
    {
        "id": "e2_question_bare",
        "text": "Is the GDP of the United States high?",
        "script": {"evidence": []},
        "expected_abstention": ["opinion_or_nonclaim"],
    },
    {
        "id": "e2_question_wh",
        "text": "What was unemployment in Germany in 2023?",
        "script": {"evidence": []},
        "expected_abstention": ["opinion_or_nonclaim"],
    },
    {
        "id": "e2_question_with_quantity",
        "text": "Was unemployment in Germany 3.1% in 2023?",
        "script": {
            "evidence": [
                {
                    "connector": "worldbank",
                    "label": "Unemployment",
                    "unit": "percent",
                    "year": "2023",
                    "value": 3.1,
                }
            ]
        },
        "expected_abstention": ["opinion_or_nonclaim"],
    },
    # --- empty (2) ---
    {
        "id": "e2_empty",
        "text": "",
        "script": {"evidence": []},
        "expected_abstention": ["no_measurable_quantity"],
    },
    {
        "id": "e2_whitespace",
        "text": "   ",
        "script": {"evidence": []},
        "expected_abstention": ["no_measurable_quantity"],
    },
    # --- compound with an unretrievable / vague half (2) ---
    {
        "id": "e2_compound_half_vague",
        "text": (
            "Unemployment in Germany was 3.1% in 2023 and "
            "inflation in France was high."
        ),
        "script": {
            "evidence": [
                {
                    "connector": "worldbank",
                    "label": "Unemployment",
                    "unit": "percent",
                    "year": "2023",
                    "value": 3.1,
                }
            ]
        },
        "expected_abstention": [],
    },
    {
        "id": "e2_compound_half_unretrievable",
        "text": (
            "The GDP of the United States was $27.36 trillion in 2023 and "
            "the GDP of Atlantis was $2 trillion in 2020."
        ),
        "script": {
            "evidence": [
                {
                    "connector": "worldbank",
                    "label": "GDP (current US$)",
                    "unit": "USD",
                    "year": "2023",
                    "value": 27.81e12,
                }
            ]
        },
        "expected_abstention": [],
    },
    # --- misc abstentions (3) ---
    {
        "id": "e2_no_year",
        "text": "The GDP of the United States was $27.36 trillion.",
        "script": {
            "evidence": [
                {
                    "connector": "worldbank",
                    "label": "GDP (current US$)",
                    "unit": "USD",
                    "year": "2023",
                    "value": 27.81e12,
                }
            ]
        },
        "expected_abstention": ["period_mismatch"],
    },
    {
        "id": "e2_geoless",
        "text": "Unemployment was 3.1% in 2023.",
        "script": {"evidence": []},
        "expected_abstention": ["no_direct_evidence"],
    },
    {
        "id": "e2_vague_temporal",
        "text": "Unemployment in France recently fell a lot.",
        "script": {"evidence": []},
        "expected_abstention": ["no_measurable_quantity"],
    },
]

ABSTAINING_STANCES = frozenset({"unverifiable", "not_assessable"})
