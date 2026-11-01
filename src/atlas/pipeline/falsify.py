"""Adversarial self-review: the model attacks the draft, code owns consequences.

Stage order: extract -> retrieve -> verify -> falsify -> synthesize.
`falsify_draft` runs AFTER verify (it needs real comparisons to target) and
BEFORE synthesize (its outputs merge into the synthesize inputs).

The model gets a falsify-only JSON schema and can ONLY point at flaws in
existing comparisons; every attack is validated by code (target id must
resolve, enums must be known) and surfaced as native Comparison/Uncertainty
objects, so no type changes are needed anywhere downstream.

ADVISORY ONLY (lead decision 2026-11-01, see ADR-0008): falsify NEVER moves
stances. The original override fired 5 stance downgrades in full eval run 1
and all 5 were spurious on inspection (note-phrasing nitpicks; disclosed
uncertainties weaponized against robust-margin verdicts), as were 2 more
reproduced live with captured attack text. Letting model severity labels
move stances launders model judgment through code. Attacks surface as doubt
(comparisons + uncertainties + audit) with code-owned severity mapping;
the stance stays decided by verified comparisons alone. Per-case
attacks/fatal_high are recorded in eval `observed.falsify`.

LIVE-WEB COUNTER-EVIDENCE (P1-A): after the schema-constrained pass, an
optional second pass asks the model (Responses API + `web_search`) to
find live counter-evidence to the draft verdict. The SAME advisory-only
contract holds: every `url_citation` annotation is validated by
deterministic code (https URL, non-empty quoted span, garbage-domain
block) and survivors surface as `web_counter_evidence` comparisons
(verdict `inconclusive`) plus minor uncertainties — stances stay
decided by verified comparisons alone. Raw search results the model
retrieved but did NOT cite surface as minor `web_considered_not_cited`
uncertainties (never evidence, never stance-moving); the audit records
`cited=X/considered=Y`. Default ON,
`ATLAS_WEB_FALSIFY=0` disables; any API/parse failure degrades to skip;
at most ONE extra model call per analysis. Under test doubles (or no
LLM) the web pass is skipped without constructing a client, so offline
tests never touch the network even when `MODEL_API_KEY` is set.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

from ..core.types import (
    AuditStep,
    Comparison,
    StructuredClaim,
    Uncertainty,
)
from ..models.meta import MetaClient
from ..models.responses import ResponsesClient, is_authoritative_host

log = logging.getLogger(__name__)

FALSIFY_SYSTEM = """You red-team a draft evidence analysis. Return ONLY a JSON object:
{"attacks": [{"target_comparison_id": "cmp_xxx", "attack_type": "wrong_unit|wrong_period|wrong_geo|non_verifying_overclaim|missing_uncertainty|citation_error", "argument": "why this comparison is flawed", "severity": "fatal|serious|nit", "confidence": "low|med|high"}]}
Rules: attack ONLY the comparisons provided, by their exact ids.
A fatal attack means the comparison's verdict cannot be trusted.
Your attacks are ADVISORY notes for a human reader: you do not decide,
recommend or vote on the verdict — code owns the conclusion.
Never introduce new facts, numbers, sources or verdicts."""

ATTACK_TYPES = frozenset({
    "wrong_unit",
    "wrong_period",
    "wrong_geo",
    "non_verifying_overclaim",
    "missing_uncertainty",
    "citation_error",
})
SEVERITIES = frozenset({"fatal", "serious", "nit"})
CONFIDENCES = frozenset({"low", "med", "high"})

# Safety cap: the model cannot flood the report with attacks.
MAX_ATTACKS = 50

# Strict decoding schema for the main falsify call (lead's `json_schema`
# support in MetaClient.complete_json; clients without the parameter keep
# json_object mode). Mirrors exactly what `_validate_attacks` parses:
# enums are pinned, every object is closed. Hand validation stays as-is
# since test doubles bypass decoding constraints.
FALSIFY_SCHEMA = {
    "name": "falsify_attacks",
    "schema": {
        "type": "object",
        "properties": {
            "attacks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "target_comparison_id": {"type": "string"},
                        "attack_type": {
                            "type": "string",
                            "enum": sorted(ATTACK_TYPES),
                        },
                        "argument": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": sorted(SEVERITIES),
                        },
                        "confidence": {
                            "type": "string",
                            "enum": sorted(CONFIDENCES),
                        },
                    },
                    "required": [
                        "target_comparison_id",
                        "attack_type",
                        "argument",
                        "severity",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["attacks"],
        "additionalProperties": False,
    },
}

# --- live-web counter-evidence pass (P1-A) ---

# Kill-switch: default ON (absent/anything-but-falsey runs the web
# pass); set ATLAS_WEB_FALSIFY=0 (or false/no/off) to disable.
WEB_FALSIFY_ENV_VAR = "ATLAS_WEB_FALSIFY"
WEB_FALSIFY_DISABLE_VALUES = frozenset({"0", "false", "no", "off"})

# Safety cap: at most this many web citations surface per analysis.
MAX_WEB_CITATIONS = 5

# Authoritative-source ordering (F11): obvious official statistics and
# institutional publishers come first when the model retrieves a mix.
# This is NOT an endorsement ranking — just the hosts whose pages are
# least likely to be junk (observed failure: whatisgdp.com x4 and a
# textile-news page outranking real sources while "prefer authoritative
# sources" was prompt-only). Disclosed selection-ordering, not
# blocking: junk still surfaces when it's all there is (honest),
# authoritative wins ties. Matching is exact-or-subdomain,
# case-insensitive.
# Authoritative-first ordering uses is_authoritative_host from
# models.responses (single source of truth; parse-time ordering there
# already applies it before the citation cap).

WEB_FALSIFY_MAX_OUTPUT_TOKENS = 2000

WEB_FALSIFY_INSTRUCTIONS = """You are an adversarial fact-checker with live web search. The input states a statistical claim, a draft verdict on it, and the checks behind that verdict. Use web search to hunt for LIVE counter-evidence: browsed pages whose numbers or findings disagree with the draft verdict or the claim's figures. Prefer authoritative sources (official statistics, primary reports). Cite every counter-finding to the page you actually browsed. If the live web supports the draft verdict, say so briefly with citations. Be concise: report only findings with citations, no narration of your search process. Rules: search the live web — never answer from memory; never invent URLs — only cite pages the search tool returned; you surface doubt with citations, you do not decide or recommend any verdict."""


def _validate_attacks(raw: object, comparisons: list[Comparison]) -> list[dict]:
    """Keep only well-formed attacks whose target id resolves.

    Drops (and logs) unknown target ids, unknown enum values, and
    malformed entries. Never raises on model-shaped input.
    """
    if not isinstance(raw, list):
        return []
    valid_ids = {c.comparison_id for c in comparisons}
    valid: list[dict] = []
    dropped = 0
    for item in raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        target = item.get("target_comparison_id")
        attack_type = item.get("attack_type")
        severity = item.get("severity")
        confidence = item.get("confidence")
        argument = item.get("argument")
        if (
            not isinstance(target, str)
            or target not in valid_ids
            or attack_type not in ATTACK_TYPES
            or severity not in SEVERITIES
            or confidence not in CONFIDENCES
            or not isinstance(argument, str)
            or not argument.strip()
        ):
            dropped += 1
            continue
        valid.append({
            "target_comparison_id": target,
            "attack_type": attack_type,
            "severity": severity,
            "confidence": confidence,
            "argument": argument.strip(),
        })
        if len(valid) >= MAX_ATTACKS:
            break
    if dropped:
        log.warning("Dropped %d invalid falsification attack(s)", dropped)
    return valid


def _complete_json_schema(llm, system: str, user: str) -> dict:
    """Main-pass model call: strict schema where the client supports it.

    MetaClient takes `json_schema` (strict decoding, verified live);
    older test doubles don't declare the parameter, so it is passed
    only when the signature advertises it. Doubles keep json_object
    behavior and hand validation stays authoritative either way.
    """
    # reasoning_effort="low": at default depth Spark spends the whole
    # 4000-token budget on hidden reasoning and returns empty content
    # (observed live 2026-11-01: 3998 reasoning tokens, truncated).
    # The judgment is small and schema-narrow; low effort suffices.
    kwargs: dict = {"max_tokens": 4000, "reasoning_effort": "low"}
    try:
        import inspect

        params = inspect.signature(llm.complete_json).parameters
    except (TypeError, ValueError):
        params = {}
    if "json_schema" in params:
        kwargs["json_schema"] = FALSIFY_SCHEMA
    return llm.complete_json(system, user, **kwargs)


def _web_falsify_enabled() -> bool:
    """Kill-switch check: default ON, ATLAS_WEB_FALSIFY=0 disables."""
    return (
        os.environ.get(WEB_FALSIFY_ENV_VAR, "1").strip().lower()
        not in WEB_FALSIFY_DISABLE_VALUES
    )


def _build_web_prompt(
    claim: StructuredClaim,
    draft_stance: str,
    comparisons: list[Comparison],
) -> str:
    """Slim prompt for the web pass: claim + draft + check summaries."""
    lines = [
        f"Claim: {claim.raw_text}",
        f"Draft verdict: {draft_stance}",
        "Checks behind the draft:",
    ]
    for c in comparisons[:10]:
        lines.append(
            f"- [{c.comparison_id}] {c.metric} = {c.verdict}: "
            f"{(c.notes or '')[:200]}"
        )
    lines.append(
        "Task: search the live web for counter-evidence to the draft "
        "verdict above. Cite every counter-finding to the browsed page."
    )
    return "\n".join(lines)[:2000]


def _merge_usage(
    first: dict | None, second: dict | None
) -> dict | None:
    """Combine two MetaClient-style usage dicts into one stage cost."""
    if not isinstance(first, dict):
        return second
    if not isinstance(second, dict):
        return first
    merged: dict = {}
    for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens",
                "total_tokens", "cached_tokens"):
        a, b = first.get(key), second.get(key)
        if isinstance(a, int) and isinstance(b, int):
            merged[key] = a + b
        else:
            merged[key] = a if a is not None else b
    return merged


def _candidate_host(url: object) -> str:
    """Best-effort host label for a considered-not-cited candidate."""
    if not isinstance(url, str) or not url:
        return "(unknown host)"
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".") or url[:60]
    except ValueError:
        return url[:60]


def _run_web_pass(
    claim: StructuredClaim,
    draft_stance: str,
    comparisons: list[Comparison],
    llm,
    web_client,
) -> tuple[list[Comparison], list[Uncertainty], str, str | None]:
    """Optional live-web counter-evidence pass (at most ONE model call).

    Returns (comparisons, uncertainties, audit_fragment, model_name).
    NEVER raises and NEVER moves stances: survivors surface as
    `web_counter_evidence` comparisons (verdict `inconclusive`) plus
    minor uncertainties. Degrades to skip on: env disable, test
    doubles / no real client, missing key, any transport/parse failure.
    """
    if not _web_falsify_enabled():
        return [], [], "web=disabled(ATLAS_WEB_FALSIFY=0)", None
    client = web_client
    if client is None:
        # Only a real MetaClient authorizes a live web call: doubles
        # (ScriptedClient/FaultClient) and llm=None mean offline. The
        # client is not even constructed then, so offline tests never
        # touch the network even when MODEL_API_KEY is set.
        if not isinstance(llm, MetaClient):
            return [], [], "web=skipped(no real client: offline/test double)", None
        try:
            client = ResponsesClient()
        except Exception as exc:
            log.warning("Web counter-evidence client init failed: %s",
                        type(exc).__name__)
            return [], [], f"web=skipped(client init failed {type(exc).__name__})", None
    if not getattr(client, "available", False):
        return [], [], "web=skipped(no MODEL_API_KEY)", None
    try:
        result = client.search_counter_evidence(
            _build_web_prompt(claim, draft_stance, comparisons),
            instructions=WEB_FALSIFY_INSTRUCTIONS,
            max_output_tokens=WEB_FALSIFY_MAX_OUTPUT_TOKENS,
        )
    except Exception as exc:
        log.warning("Web counter-evidence skipped: %s: %s",
                    type(exc).__name__, str(exc)[:200])
        return [], [], f"web=skipped(model call failed {type(exc).__name__})", None
    # F11: authoritative-FIRST (stable: others keep model order after),
    # applied BEFORE the <=5 cap so official sources win ties instead of
    # being crowded out by junk retrieved earlier.
    citations = sorted(
        list(getattr(result, "citations", None) or []),
        key=lambda c: not is_authoritative_host(getattr(c, "domain", "")),
    )[:MAX_WEB_CITATIONS]
    # Considered-not-cited (F2-A): raw search results the model retrieved
    # but did not cite. Same authoritative-first ordering, then bounded
    # like citations; surfaced as minor uncertainties ONLY — never
    # comparisons, never evidence, never stance-moving. Doubles without
    # `candidates` degrade to [] here.
    considered = sorted(
        list(getattr(result, "candidates", None) or []),
        key=lambda c: not is_authoritative_host(
            _candidate_host(getattr(c, "url", ""))),
    )
    uncited = [c for c in considered
               if not getattr(c, "cited", False)][:MAX_WEB_CITATIONS]
    try:  # attribute the web spend to the falsify stage (best effort)
        usage = getattr(client, "last_usage", None)
        if usage is not None and hasattr(llm, "last_usage_seq"):
            llm.last_usage = _merge_usage(getattr(llm, "last_usage", None), usage)
            llm.last_usage_seq += 1
    except Exception:
        log.debug("web usage propagation failed", exc_info=True)
    model_name = getattr(client, "name", "model")
    new_comparisons = [
        Comparison(
            claim_id=claim.claim_id,
            actor="model",
            model=model_name,
            metric="web_counter_evidence",
            claim_value=c.url[:300],
            evidence_value=c.span[:500],
            verdict="inconclusive",  # doubt without a vote: stance-neutral
            # UT-A4 (arch 5d): quote is model-reported from search results;
            # code never fetched the page, so label it — not a verified cite.
            notes=(f"[{c.domain}] {c.span[:400]} — {c.url[:300]} "
                   "(model-reported quote; page not fetched by code)"),
        )
        for c in citations
    ]
    new_uncertainties = [
        Uncertainty(
            code="web_counter_evidence",
            description=(
                f"Live-web counter-evidence ({c.domain}): "
                f"{c.span[:300]} [{c.url[:200]}] (model-reported quote; "
                "page not fetched by code)"
            ),
            severity="minor",  # never blocking: advisory only, stance-neutral
        )
        for c in citations
    ]
    considered_uncertainties = [
        Uncertainty(
            code="web_considered_not_cited",
            description=(
                f"Considered but not cited "
                f"({_candidate_host(getattr(c, 'url', ''))}): "
                f"{(getattr(c, 'title', '') or '(untitled)')[:200]} "
                f"[{str(getattr(c, 'url', ''))[:200]}] — retrieved by "
                f"the model, listed by code; not evidence."
            ),
            severity="minor",  # never blocking: advisory only, stance-neutral
        )
        for c in uncited
    ]
    if citations:
        fragment = f"web_citations={len(citations)}"
    elif getattr(result, "truncated", False):
        fragment = "web_citations=0(TRUNCATED: output cap hit, zero uninformative)"
    else:
        fragment = "web_citations=0(none validated)"
    fragment += f" cited={len(citations)}/considered={len(considered)}"
    return (new_comparisons, new_uncertainties + considered_uncertainties,
            fragment, model_name)


def falsify_draft(
    claim: StructuredClaim,
    comparisons: list[Comparison],
    uncertainties: list[Uncertainty],
    draft_stance: str,
    llm=None,
    web_client=None,
) -> tuple[list[Comparison], list[Uncertainty], AuditStep]:
    """Adversarial review of the draft analysis.

    Returns (new_comparisons, new_uncertainties, audit_entry). ADVISORY
    ONLY: outputs surface doubt but never move stances (see module
    docstring). New comparisons use metric `falsification_review`, actor
    `model`, and an `inconclusive` verdict (they flag doubt without
    casting their own vote, so the synthesize stance rules are
    undisturbed). Fatal attacks also add a MAJOR uncertainty (never
    blocking: a contested draft must stay decidable, not collapse to
    unverifiable). Degraded (no LLM) or invalid model output is a safe
    no-op with an explanatory audit entry.

    The optional live-web pass (`web_client`, injected in tests and
    auto-constructed only under a real MetaClient) adds at most one
    Responses-API `web_search` call whose code-validated citations
    surface the same advisory way; it degrades to skip on any failure
    and is disabled by `ATLAS_WEB_FALSIFY=0`.
    """
    llm_up = llm is not None and getattr(llm, "available", False)
    if not llm_up and web_client is None:
        return [], [], AuditStep(
            step="falsify",
            actor="code",
            detail="skipped: no LLM available (degraded no-op)",
        )
    # --- main pass: schema-constrained attacks (unchanged semantics) ---
    if not llm_up:
        # Only reachable with an explicitly injected web_client (the
        # degraded early-return above covers llm-down otherwise).
        main_c: list[Comparison] = []
        main_u: list[Uncertainty] = []
        main_actor = "code"
        main_detail = "main=skipped(no LLM)"
    else:
        # Slim payload: same style as synthesize.py -- only what the review needs.
        payload = {
            "claim": claim.raw_text,
            "draft_stance": draft_stance,
            "comparisons": [
                {"id": c.comparison_id, "metric": c.metric,
                 "verdict": c.verdict, "notes": c.notes}
                for c in comparisons
            ],
            "uncertainties": [
                {"code": u.code, "description": u.description,
                 "severity": u.severity}
                for u in uncertainties
            ],
        }
        try:
            import json as _json

            data = _complete_json_schema(
                llm, FALSIFY_SYSTEM, _json.dumps(payload)[:3000])
        except Exception as exc:
            log.warning("Falsification LLM call failed, skipping: %r", exc)
            main_c, main_u = [], []
            main_actor = "code"
            main_detail = f"skipped: model call failed ({exc!r})"
        else:
            if not isinstance(data, dict):
                log.warning("Falsification output not a JSON object, skipping")
                main_c, main_u = [], []
                main_actor = "code"
                main_detail = "skipped: model output was not a JSON object"
            else:
                # NOTE: unknown top-level keys (incl. any legacy
                # "overturn_recommended" the model volunteers) are ignored:
                # the schema is attacks-only and advisory-only.
                valid_attacks = _validate_attacks(
                    data.get("attacks"), comparisons)

                model_name = getattr(llm, "name", "model")
                main_c = [
                    Comparison(
                        claim_id=claim.claim_id,
                        actor="model",
                        model=model_name,
                        metric="falsification_review",
                        claim_value=a["target_comparison_id"],
                        evidence_value="",
                        verdict="inconclusive",
                        notes=(
                            f"[{a['attack_type']}/{a['severity']}/"
                            f"{a['confidence']}] {a['argument']}"
                        ),
                    )
                    for a in valid_attacks
                ]
                main_u = [
                    Uncertainty(
                        code="falsification_flag",
                        description=(
                            f"Self-review {a['severity']} flag on "
                            f"[{a['target_comparison_id']}] ({a['attack_type']}): "
                            f"{a['argument']}"
                        ),
                        severity="major" if a["severity"] == "fatal" else "minor",  # type: ignore[arg-type]
                        related_ids=[a["target_comparison_id"]],
                    )
                    for a in valid_attacks
                    if a["severity"] in ("fatal", "serious")
                ]
                main_detail = (
                    f"attacks={len(valid_attacks)} fatal_high="
                    f"{sum(1 for a in valid_attacks if a['severity'] == 'fatal' and a['confidence'] == 'high')} "
                    "advisory_only"
                )
                main_actor = f"model:{model_name}"

    # --- optional live-web counter-evidence pass (<=1 extra call) ---
    web_c, web_u, web_fragment, web_model = _run_web_pass(
        claim, draft_stance, comparisons, llm, web_client)
    actor = main_actor
    if main_actor == "code" and (web_c or web_u) and web_model:
        actor = f"model:{web_model}"
    return main_c + web_c, main_u + web_u, AuditStep(
        step="falsify",
        actor=actor,
        detail=f"{main_detail} {web_fragment}",
    )
