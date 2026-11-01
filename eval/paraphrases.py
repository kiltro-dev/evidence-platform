"""E1 paraphrase-robustness harness (M6 eval expansion).

6 base claims x 5 paraphrases = 30 runs. Every base pins an expected
stance; each paraphrase runs through the SAME scripted evidence and must
produce the SAME stance (stance-agreement gate: 100%).

Coverage: unit rewordings (symbol/code/word, %/percent, EUR forms),
EN/ES (billones, por ciento, comma decimals, euro forms), scale words
(trillion/billion/mn, full digits, 0.5 billion).

All bases are offline (scripted evidence, llm=None in degraded mode).
Live mode (--paraphrase --mode full) is supported by the runner but was
NOT run by M6 (offline only).

Deliberately excluded (verified product gaps, see eval/NOTES_M6.md):
- bare "T" abbreviation ("$27.36T" parses as 27.36 USD and contradicts);
- paraphrases that change the verdict-relevant quantity or drop the year.
"""

PARAPHRASE_BASES = [
    {
        "id": "para_gdp_supported",
        "base": "The GDP of the United States was $27.36 trillion in 2023.",
        # Exact-match evidence (27.36e12): E1 measures paraphrase
        # agreement, not precision, so the evidence matches exactly and
        # the precision rule (abs_diff > implied resolution) cannot
        # fire -- supported under RULE=0 and RULE=1 alike.
        "script": {
            "evidence": [
                {
                    "connector": "worldbank",
                    "label": "GDP (current US$)",
                    "unit": "USD",
                    "year": "2023",
                    "value": 27.36e12,
                }
            ]
        },
        "expected_stance": "supported",
        "paraphrases": [
            # scale-word / abbreviation rewordings (same 27.36e12, USD)
            "US GDP was $27.36tn in 2023.",
            "The GDP of the United States was 27360 billion dollars in 2023.",
            "The GDP of the United States was $27,360,000,000,000 in 2023.",
            # ES: billones = 1e12
            "El PIB de Estados Unidos fue de $27.36 billones en 2023.",
            # time-fronted reorder
            "In 2023, the GDP of the United States was $27.36 trillion.",
        ],
    },
    {
        "id": "para_unemployment_supported",
        "base": "Unemployment in Germany was 3.1% in 2023.",
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
        "expected_stance": "supported",
        "paraphrases": [
            # unit rewordings: % -> word / possessive / trailing zero
            "Unemployment in Germany was 3.1 percent in 2023.",
            "Germany's unemployment rate was 3.1 percent in 2023.",
            "Unemployment in Germany was 3.10% in 2023.",
            # time-fronted reorder
            "In 2023, unemployment in Germany stood at 3.1%.",
            # ES with comma decimal + por ciento
            "El desempleo en Alemania fue de 3,1 por ciento en 2023.",
        ],
    },
    {
        "id": "para_population_contradicted",
        "base": "The population of Japan was 500 million in 2020.",
        "script": {
            "evidence": [
                {
                    "connector": "worldbank",
                    "label": "Population",
                    "unit": "count",
                    "year": "2020",
                    "value": 125.8e6,
                }
            ]
        },
        "expected_stance": "contradicted",
        "paraphrases": [
            # scale rewordings of the same 500M bare figure
            "The population of Japan was 500,000,000 in 2020.",
            "The population of Japan was 0.5 billion in 2020.",
            "Japan's population was 500 mn in 2020.",
            # ES scale word
            "La población de Japón fue de 500 millones en 2020.",
            # explicit count unit (measured path instead of bare-number
            # assumption; stance must still be contradicted)
            "In 2020, 500 million people lived in Japan.",
        ],
    },
    {
        "id": "para_unit_mismatch",
        "base": "The GDP of the United States was 27 trillion euros in 2023.",
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
        "expected_stance": "unverifiable",
        "paraphrases": [
            # unit rewordings: symbol / code / scale shift, all EUR vs USD
            "The GDP of the United States was €27 trillion in 2023.",
            "US GDP was 27 trillion EUR in 2023.",
            "The GDP of the United States was 27000 billion euros in 2023.",
            # ES euros
            "El PIB de Estados Unidos fue de 27 billones de euros en 2023.",
            # time-fronted reorder
            "In 2023, the GDP of the United States was 27 trillion euros.",
        ],
    },
    {
        "id": "para_opinion_not_assessable",
        "base": "Democracy is the best form of government.",
        "script": {"evidence": []},
        "expected_stance": "not_assessable",
        "paraphrases": [
            # opinion-lexicon variants (all must keep the
            # opinion_or_nonclaim flag, hence not_assessable)
            "Democracy is the worst form of government.",
            "I think democracy is the best form of government.",
            "Democracy should be the only form of government.",
            "What is the best form of government?",
            "Democracy is believed to be the best form of government.",
        ],
    },
    {
        "id": "para_vague_unverifiable",
        "base": "The inflation of Brazil is high.",
        "script": {"evidence": []},
        "expected_stance": "unverifiable",
        "paraphrases": [
            # no-measurable-quantity rewordings, none opinionated
            "Brazil's inflation is high.",
            "Inflation in Brazil is elevated.",
            "High inflation in Brazil.",
            "Brazil has high inflation.",
            # ES vague (no opinion lexicon hit -> still unverifiable,
            # not not_assessable)
            "La inflación de Brasil es alta.",
        ],
    },
]
