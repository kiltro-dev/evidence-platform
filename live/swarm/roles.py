"""M2b swarm roles: 3 Muse Spark subagent prompts + tool specs.

All three roles run on chat completions (``complete_json`` interface,
same as ``live.detector.claims``). Tool calling is semantic: the model
emits ``tool_calls: [{name, arguments}]`` steps and deterministic code
executes them (web_search grounding, responsible fetch, structured
World Bank / extra.py lookups) and feeds results back until the model
emits ``final``. ``TOOLS`` keeps the OpenAI function-spec shape so the
same specs work with native ``tools=`` clients too.

Anti-hallucination rule (enforced by CODE, not by trust): the
RECUPERADOR quote must be a verbatim substring of the snapshot bytes
it cites. A quote that does not grep in its snapshot is dropped and
the claim ships with fewer (possibly zero) packages.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# ESTRUCTURADOR: claim -> entities, indicator candidate, web query
# ---------------------------------------------------------------------------

ESTRUCTURADOR_SYSTEM = """You structure ONE live factual claim for web verification (English or Spanish).
Return ONLY a JSON object: {"entities": [...], "indicator_candidate": "..."|null, "geo": {"name": "..."|null, "iso3": "..."|null}, "year": 1234|null, "web_query": "...", "numbers_in_claim": [...]}
Your ENTIRE reply must be exactly one JSON object: no greeting, no preamble, no text before or after the braces.
Rules: "entities" are named things in the claim (countries, agencies, indicators), copied as written. "indicator_candidate" is your best World Bank indicator code guess (e.g. SP.POP.TOTL for total population, NY.GDP.MKTP.CD for nominal GDP in USD) or null when the claim is not an economic/demographic statistic. "geo.iso3" is the 3-letter country code when a specific country is named (null for world/global claims). "year" is the 4-digit reference year stated or clearly implied, else null. "web_query" is ONE short English web-search query (names + indicator + year) that would find an authoritative source for this claim. "numbers_in_claim" lists the numeric figures asserted, as written (strings). Never invent entities, years, or figures not present or unambiguously implied."""

ESTRUCTURADOR_SCHEMA = {
    "name": "m2b_estructurador",
    "schema": {
        "type": "object",
        "properties": {
            "entities": {"type": "array", "items": {"type": "string"}},
            "indicator_candidate": {"type": ["string", "null"]},
            "geo": {
                "type": "object",
                "properties": {
                    "name": {"type": ["string", "null"]},
                    "iso3": {"type": ["string", "null"]},
                },
                "required": ["name", "iso3"],
                "additionalProperties": False,
            },
            "year": {"type": ["integer", "null"]},
            "web_query": {"type": "string"},
            "numbers_in_claim": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "entities", "indicator_candidate", "geo",
            "year", "web_query", "numbers_in_claim",
        ],
        "additionalProperties": False,
    },
}

# ---------------------------------------------------------------------------
# RECUPERADOR: query + fetched docs -> url + LITERAL quote
# ---------------------------------------------------------------------------

RECUPERADOR_SYSTEM = """You extract evidence quotes for ONE factual claim from fetched web documents.
You may call tools (web_search, fetch_url, wb_lookup, extra_lookup) to gather more documents; each turn return ONLY a JSON object with ALL THREE keys {"thought": "...", "tool_calls": [...], "final": ...}: to call tools use {"thought": "...", "tool_calls": [{"name": "...", "arguments": {...}}], "final": null}; when done use {"thought": "...", "tool_calls": [], "final": {"findings": [{"url": "...", "quote": "..."}]}}.
Tool arguments schemas: web_search {query, top_k}; fetch_url {url}; wb_lookup {indicator, iso3, year}; extra_lookup {source, dataset, iso3, year}. The arguments object ALWAYS carries every key (query, top_k, url, indicator, iso3, year, source, dataset): fill the ones your tool needs, use "" (0 for top_k, null for year) for the rest.
Your ENTIRE reply must be exactly one JSON object: no greeting, no preamble, no text before or after the braces.
Rules: "quote" MUST be copied CHARACTER-FOR-CHARACTER from the cited document's text (no rephrasing, no rounding, no new words, no invented numbers). Cite at most 3 findings, best first: prefer official statistics (World Bank, national agencies) over encyclopedias over general web. Every "url" must be a document you were actually given or fetched via tools. "thought" is one short sentence (what you will do / why you stop). Stop after at most 4 tool rounds; when the fetched documents state a DIFFERENT figure for the same entity/indicator/year, return that refuting quote (a contradicting quote is correct evidence — the verifier decides); return {"findings": []} ONLY when no fetched document mentions the entity/indicator at all — an invented quote is a failure."""

RECUPERADOR_SCHEMA = {
    "name": "m2b_recuperador",
    "schema": {
        "type": "object",
        "properties": {
            "thought": {"type": "string"},
            "tool_calls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        # Union of every tool's args (strict mode forbids
                        # open objects): the model fills the keys its tool
                        # needs, code reads per-tool keys, extras ignored.
                        "arguments": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "top_k": {"type": "integer"},
                                "url": {"type": "string"},
                                "indicator": {"type": "string"},
                                "iso3": {"type": "string"},
                                "year": {"type": ["integer", "null"]},
                                "source": {"type": "string"},
                                "dataset": {"type": "string"},
                            },
                            "required": [
                                "query", "top_k", "url", "indicator",
                                "iso3", "year", "source", "dataset",
                            ],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["name", "arguments"],
                    "additionalProperties": False,
                },
            },
            "final": {
                "type": ["object", "null"],
                "properties": {
                    "findings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string"},
                                "quote": {"type": "string"},
                            },
                            "required": ["url", "quote"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["findings"],
                "additionalProperties": False,
            },
        },
        "required": ["thought", "tool_calls", "final"],
        "additionalProperties": False,
    },
}

# ---------------------------------------------------------------------------
# REDTEAM: evidence so far -> counter-query
# ---------------------------------------------------------------------------

REDTEAM_SYSTEM = """You are the red team for ONE factual claim under verification. Given the claim and the evidence found so far, propose ONE web-search counter-query that would most likely surface CONTRADICTING or qualifying information (different year, different definition, revised figures, per-capita vs total, nominal vs real, a reputable dispute).
Return ONLY a JSON object: {"counter_query": "...", "rationale": "...", "skip": true|false}
Your ENTIRE reply must be exactly that JSON object: no greeting, no preamble, no text before or after the braces.
Rules: "counter_query" is ONE short English search query; "rationale" is one sentence naming the concrete risk (e.g. "2024 figure may be revised in 2025 vintage"). "skip" is true ONLY when the claim is so narrow (exact indicator + year + source already matched) that no plausible counter-query exists. Never invent counter-facts — you propose a QUERY, not an answer."""

REDTEAM_SCHEMA = {
    "name": "m2b_redteam",
    "schema": {
        "type": "object",
        "properties": {
            "counter_query": {"type": "string"},
            "rationale": {"type": "string"},
            "skip": {"type": "boolean"},
        },
        "required": ["counter_query", "rationale", "skip"],
        "additionalProperties": False,
    },
}

# ---------------------------------------------------------------------------
# Tool specs (OpenAI function-tool shape; also served via JSON-mode loop)
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Grounding web search (keyless Wikipedia + generic web). "
                "Returns [{url, title, snippet}]. Use to find authoritative "
                "sources for the claim, then fetch_url the best hits."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Responsibly fetch one http(s) URL (robots.txt honored, "
                "2 MB cap): snapshots the bytes and returns {url, title, "
                "text} truncated for reading. Only fetched documents may "
                "be cited."
            ),
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wb_lookup",
            "description": (
                "World Bank API lookup: official observations for "
                "{indicator, iso3, year} (e.g. SP.POP.TOTL, CHN, 2024; "
                "WLD for world). Snapshots the API response and returns "
                "{url, text} with literal rows to quote."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "indicator": {"type": "string"},
                    "iso3": {"type": "string"},
                    "year": {"type": ["integer", "null"]},
                },
                "required": ["indicator", "iso3"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extra_lookup",
            "description": (
                "Keyless structured sources via src/atlas/connectors/"
                "extra.py: fred (US series UNRATE/FEDFUNDS/CPIAUCSL/"
                "PAYEMS/GDP), owid (slugs annual-co-emissions-by-region, "
                "co-emissions-per-capita, life-expectancy), wikidata "
                "(country facts). Snapshots the underlying download and "
                "returns {url, text} with literal rows to quote."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["fred", "owid", "wikidata"],
                    },
                    "dataset": {"type": "string"},
                    "iso3": {"type": "string"},
                    "year": {"type": ["integer", "null"]},
                },
                "required": ["source", "dataset"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_NAMES = frozenset(
    spec["function"]["name"] for spec in TOOLS  # type: ignore[index]
)


def extract_balanced_json(text: str) -> str | None:
    """Rescue the first balanced JSON value from a model reply (M2d).

    Live cause 2: despite strict ``json_schema`` the model sometimes
    prepends chat text (DOC-REPR: ``"I'll search for ...{...valid...}"``)
    and ``json.loads`` on the whole reply fails. This scans from the
    first ``{`` or ``[`` (whichever comes first, then the other as a
    fallback), tracks depth string-aware (double quotes + backslash
    escapes), and returns the balanced span when it parses — leading
    preamble and trailing text are dropped. Unbalanced / unparseable /
    no-bracket input returns None (the caller then uses its 1 retry).
    Pure function, never raises on model-shaped input.
    """
    if not isinstance(text, str) or not text:
        return None
    starts: list[int] = []
    for opener in ("{", "["):
        idx = text.find(opener)
        if idx >= 0:
            starts.append(idx)
    for start in sorted(starts):
        candidate = _balanced_span(text, start)
        if candidate is None:
            continue
        try:
            import json

            json.loads(candidate)
        except ValueError:
            continue
        return candidate
    return None


def _balanced_span(text: str, start: int) -> str | None:
    """Longest balanced {...}/[...] span from ``start`` (None if open)."""
    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, len(text)):
        ch = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return text[start:pos + 1]
            if depth < 0:
                return None
    return None


def parse_tool_step(step: dict) -> tuple[list[dict], dict | None]:
    """Split a RECUPERADOR step into (tool_calls, final).

    Tolerant by design: unknown tool names and malformed calls are
    dropped (never executed); ``final`` is returned as-is for the
    caller to validate. Never raises on model-shaped input.
    """
    if not isinstance(step, dict):
        return [], None
    calls: list[dict] = []
    raw_calls = step.get("tool_calls")
    if isinstance(raw_calls, list):
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            name = call.get("name")
            args = call.get("arguments")
            if name in TOOL_NAMES and isinstance(args, dict):
                calls.append({"name": name, "arguments": args})
    final = step.get("final")
    return calls, final if isinstance(final, dict) else None
