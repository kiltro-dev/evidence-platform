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
"""

from __future__ import annotations

import logging

from ..core.types import (
    AuditStep,
    Comparison,
    StructuredClaim,
    Uncertainty,
)

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


def falsify_draft(
    claim: StructuredClaim,
    comparisons: list[Comparison],
    uncertainties: list[Uncertainty],
    draft_stance: str,
    llm=None,
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
    """
    if llm is None or not getattr(llm, "available", False):
        return [], [], AuditStep(
            step="falsify",
            actor="code",
            detail="skipped: no LLM available (degraded no-op)",
        )
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

        # reasoning_effort="low": at default depth Spark spends the whole
        # 4000-token budget on hidden reasoning and returns empty content
        # (observed live 2026-11-01: 3998 reasoning tokens, truncated).
        # The judgment is small and schema-narrow; low effort suffices.
        data = llm.complete_json(
            FALSIFY_SYSTEM, _json.dumps(payload)[:3000], max_tokens=4000,
            reasoning_effort="low",
        )
    except Exception as exc:
        log.warning("Falsification LLM call failed, skipping: %r", exc)
        return [], [], AuditStep(
            step="falsify",
            actor="code",
            detail=f"skipped: model call failed ({exc!r})",
        )
    if not isinstance(data, dict):
        log.warning("Falsification output not a JSON object, skipping")
        return [], [], AuditStep(
            step="falsify",
            actor="code",
            detail="skipped: model output was not a JSON object",
        )
    # NOTE: unknown top-level keys (incl. any legacy
    # "overturn_recommended" the model volunteers) are ignored: the schema
    # is attacks-only and advisory-only.
    valid_attacks = _validate_attacks(data.get("attacks"), comparisons)

    model_name = getattr(llm, "name", "model")
    new_comparisons = [
        Comparison(
            claim_id=claim.claim_id,
            actor="model",
            model=model_name,
            metric="falsification_review",
            claim_value=a["target_comparison_id"],
            evidence_value="",
            verdict="inconclusive",
            notes=(
                f"[{a['attack_type']}/{a['severity']}/{a['confidence']}] "
                f"{a['argument']}"
            ),
        )
        for a in valid_attacks
    ]
    new_uncertainties = [
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
    detail = (
        f"attacks={len(valid_attacks)} fatal_high="
        f"{sum(1 for a in valid_attacks if a['severity'] == 'fatal' and a['confidence'] == 'high')} "
        "advisory_only"
    )
    return new_comparisons, new_uncertainties, AuditStep(
        step="falsify",
        actor=f"model:{model_name}",
        detail=detail,
    )
