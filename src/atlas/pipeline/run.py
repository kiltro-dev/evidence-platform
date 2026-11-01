"""End-to-end analysis orchestrator with a full audit trail."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

from ..connectors import InvalidGeoError, ProviderError, for_domain  # noqa: F401  (registry access)
from ..core.compare import DEFAULT_REL_TOLERANCE
from ..core.types import AuditStep, Evidence, Report, Uncertainty
from ..storage.store import save_report
from . import extract as _extract  # noqa: F401
from .extract import structure_claim
from .synthesize import synthesize
from .verify import assess_science, verify_quantitative

import atlas.connectors.openalex  # noqa: F401  (self-register)
import atlas.connectors.worldbank  # noqa: F401  (self-register)

# Single app-version constant. NOTE (A2b integration): api/app.py is owned by
# another agent; it should `from atlas.pipeline.run import APP_VERSION` rather
# than defining its own copy, until the constant moves to a shared module.
APP_VERSION = "0.2.0"
PROMPT_VERSION = "2"
MODEL_TEMPERATURE = 0
MODEL_MAX_TOKENS = 4000
DEFAULT_LIMIT = 5
# S5: persisted exception text is truncated to this many chars.
EXC_TEXT_LIMIT = 200
# Transient source failures (timeout/connect/429/5xx) get one retry after
# this delay; permanent failures degrade immediately. Monkeypatched to 0
# in tests.
RETRY_DELAY_S = 2.0

LIMITATIONS = [
    "Evidence Atlas is an analysis aid, not an arbiter of truth.",
    "Retrieved sources may disagree for legitimate methodological reasons.",
    "A measured discrepancy is not proof of falsehood.",
    "Results depend on the sources reachable at analysis time.",
]


def _start_step(audit: list[AuditStep], step: str, actor: str) -> AuditStep:
    s = AuditStep(step=step, actor=actor, started_at=datetime.now(timezone.utc))
    s.__dict__["_t0"] = time.perf_counter()
    audit.append(s)
    return s


def _end_step(s: AuditStep, detail: str = "") -> AuditStep:
    t1 = time.perf_counter()
    t0 = s.__dict__.pop("_t0", t1)
    s.ended_at = datetime.now(timezone.utc)
    s.detail = detail
    s.duration_ms = (t1 - t0) * 1000.0
    return s


def _truncate(text: str) -> str:
    return text[:EXC_TEXT_LIMIT]


def _retrieve_with_retry(conn, claim, limit: int) -> list[Evidence]:
    """Call conn.retrieve, retrying once after RETRY_DELAY_S on transient
    ProviderError. Permanent errors, InvalidGeoError and anything else
    propagate immediately to the caller's handlers."""
    try:
        return conn.retrieve(claim, limit=limit)
    except ProviderError as exc:
        if not exc.transient:
            raise
        time.sleep(RETRY_DELAY_S)
        return conn.retrieve(claim, limit=limit)


def _retrieve_query(conn, found: list[Evidence]) -> str | None:
    query = getattr(conn, "last_query", None)
    if query is None:
        for ev in found:
            q = ev.payload.get("query")
            if q:
                query = q
                break
    return query


VALID_DOMAINS = ("auto", "economy", "science", "general")


def analyze(
    raw_text: str,
    llm=None,
    limit: int = DEFAULT_LIMIT,
    persist: bool = True,
    domain: str = "auto",
) -> Report:
    if domain not in VALID_DOMAINS:
        raise ValueError(f"Invalid domain {domain!r}; expected one of {VALID_DOMAINS}.")
    t_start = time.perf_counter()
    audit: list[AuditStep] = []

    es = _start_step(audit, "extract", "code")
    report = Report(claim=structure_claim(raw_text, llm))
    claim = report.claim
    es.actor = f"{claim.extraction_actor}:{claim.extraction_model or 'heuristic'}"
    _end_step(
        es,
        f"domain={claim.domain} type={claim.claim_type} "
        f"quantities={len(claim.quantities)} times={len(claim.time_refs)} "
        f"geo={len(claim.geo_refs)}",
    )

    rs = _start_step(audit, "route", "code")
    effective_domain = claim.domain
    override_note = ""
    if domain != "auto" and domain != claim.domain:
        override_note = f" domain_override={claim.domain}->{domain}"
        claim.domain = domain
        effective_domain = domain
    connectors = for_domain(effective_domain)
    _end_step(rs, f"connectors={[c.name for c in connectors]}" + override_note)

    evidences: list[Evidence] = []
    for conn in connectors:
        rstep = _start_step(audit, "retrieve", "code")
        try:
            found = _retrieve_with_retry(conn, claim, limit)
        except InvalidGeoError as exc:
            report.uncertainties.append(
                Uncertainty(
                    code="invalid_geo",
                    description=(
                        f"Connector {conn.name} rejected geo reference "
                        f"{exc.iso3!r}: not in the ISO3 allowlist; "
                        "no request was sent."
                    ),
                    severity="major",
                    related_ids=[claim.claim_id],
                )
            )
            _end_step(
                rstep,
                f"{conn.name}: status=invalid_geo count=0 iso3={exc.iso3!r}",
            )
            continue
        except ProviderError as exc:
            code = "connector_error" if exc.transient else "source_unavailable"
            report.uncertainties.append(
                Uncertainty(
                    code=code,
                    description=(
                        f"Connector {conn.name} failed: {_truncate(str(exc))}"
                    ),
                    severity="major",
                    related_ids=[claim.claim_id],
                )
            )
            query = getattr(conn, "last_query", None)
            detail = f"{conn.name}: status=error count=0"
            if query:
                detail += f" query={query!r}"
            detail += f" error={_truncate(str(exc))}"
            _end_step(rstep, detail)
            continue
        except Exception as exc:  # a dead source degrades, never kills analysis
            report.uncertainties.append(
                Uncertainty(
                    code="connector_error",
                    description=(
                        f"Connector {conn.name} failed: {_truncate(str(exc))}"
                    ),
                    severity="major",
                    related_ids=[claim.claim_id],
                )
            )
            query = getattr(conn, "last_query", None)
            detail = f"{conn.name}: status=error count=0"
            if query:
                detail += f" query={query!r}"
            detail += f" error={_truncate(str(exc))}"
            _end_step(rstep, detail)
            continue
        evidences.extend(found)
        query = _retrieve_query(conn, found)
        detail = f"{conn.name}: status=ok count={len(found)}"
        if query is not None:
            detail += f" query={query!r}"
        _end_step(rstep, detail)

    vs = _start_step(audit, "verify", "code")
    if claim.domain == "science":
        comparisons, uncertainties = assess_science(claim, evidences)
    else:
        comparisons, uncertainties = verify_quantitative(claim, evidences)
        if claim.domain == "general" and not comparisons:
            extra_c, extra_u = assess_science(claim, evidences)
            comparisons.extend(extra_c)
            uncertainties.extend(extra_u)
    _end_step(
        vs,
        f"{len(comparisons)} comparison(s), {len(uncertainties)} uncertaintie(s)",
    )

    ss = _start_step(audit, "synthesize", "code")
    conclusion = synthesize(claim, comparisons, uncertainties, llm)
    ss.actor = f"{conclusion.actor}:{conclusion.model or 'deterministic-rules'}"
    _end_step(ss, f"stance={conclusion.stance}")

    report.evidences = evidences
    report.comparisons = comparisons
    # Dedupe identical uncertainties (e.g. general-domain claims run both
    # verify paths and would otherwise repeat the same code twice).
    seen: set[tuple[str, str]] = set()
    deduped: list[Uncertainty] = []
    for u in report.uncertainties + uncertainties:
        key = (u.code, u.description)
        if key not in seen:
            seen.add(key)
            deduped.append(u)
    report.uncertainties = deduped
    report.limitations = list(LIMITATIONS)
    report.conclusion = conclusion
    report.audit = audit

    if persist:
        ps = _start_step(audit, "persist", "code")
        save_report(report)
        _end_step(ps, f"report_id={report.report_id}")

    elapsed_ms = (time.perf_counter() - t_start) * 1000.0
    model_name = None
    if llm is not None:
        model_name = getattr(llm, "model", getattr(llm, "name", None))
    report.meta = {
        "app_version": APP_VERSION,
        "model": model_name,
        "temperature": MODEL_TEMPERATURE,
        "max_tokens": MODEL_MAX_TOKENS,
        "tolerance": DEFAULT_REL_TOLERANCE,
        "default": DEFAULT_LIMIT,
        "prompt_version": PROMPT_VERSION,
        "input_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
    }
    report.elapsed_ms = elapsed_ms
    return report
