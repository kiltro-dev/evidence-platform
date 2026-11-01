"""End-to-end analysis orchestrator with a full audit trail."""

from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from ..connectors import InvalidGeoError, ProviderError, for_domain  # noqa: F401  (registry access)
from ..core.compare import DEFAULT_REL_TOLERANCE
from ..core.types import AuditStep, Evidence, Report, Uncertainty
from ..models.meta import MetaClient
from ..storage.store import save_report
from . import extract as _extract  # noqa: F401
from .extract import structure_claim
from .falsify import falsify_draft
from .retrieve import retrieve_all
from .science import assess_science_abstracts
from .split import split_claims
from .synthesize import decide_stance, synthesize
from .verify import assess_science, verify_quantitative

import atlas.connectors.openalex  # noqa: F401  (self-register)
import atlas.connectors.worldbank  # noqa: F401  (self-register)

# Single app-version constant. NOTE (A2b integration): api/app.py is owned by
# another agent; it should `from atlas.pipeline.run import APP_VERSION` rather
# than defining its own copy, until the constant moves to a shared module.
APP_VERSION = "0.4.0"
PROMPT_VERSION = "4"
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


def _usage_seq(llm) -> int:
    """Monotonic usage counter of the LLM client (0 when absent)."""
    if llm is None:
        return 0
    seq = getattr(llm, "last_usage_seq", 0)
    return seq if isinstance(seq, int) else 0


def _take_usage(llm, seq_before: int) -> dict | None:
    """Usage spent since `seq_before`, or None when the stage made no call.

    MetaClient records `last_usage` + bumps `last_usage_seq` after every
    call that gets an HTTP response; other clients lack both attributes.
    The copy matters: each new model call overwrites the client's record.
    """
    if llm is None or _usage_seq(llm) == seq_before:
        return None
    usage = getattr(llm, "last_usage", None)
    return dict(usage) if isinstance(usage, dict) else None


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
    seq_before = _usage_seq(llm)
    report = Report(claim=structure_claim(raw_text, llm))
    usage_extract = _take_usage(llm, seq_before)
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

    # Parallel fan-out (Operation First Place M4): every connector runs
    # on its own thread via retrieve_all; evidence stays in connector
    # order and errors keep their types. The per-connector audit steps
    # and handlers below preserve the sequential loop's exact behavior.
    # Per-connector wall seconds, filled by the fan-out below. The audit
    # steps are created after retrieve_all returns, so each step is
    # backdated by its connector's measured time (observability fix:
    # pre-fix every retrieve step read ~0.0ms and the ~140ms network
    # wait was invisible in the audit trail).
    retrieve_timings: dict[str, float] = {}
    found_all, retrieve_errors = retrieve_all(
        connectors, claim, limit, timings=retrieve_timings
    )
    by_connector: dict[str, list[Evidence]] = {}
    for ev in found_all:
        by_connector.setdefault(ev.connector, []).append(ev)
    error_by_connector = {name: exc for name, exc in retrieve_errors}

    evidences: list[Evidence] = []
    for conn in connectors:
        rstep = _start_step(audit, "retrieve", "code")
        fanout_s = retrieve_timings.get(conn.name, 0.0)
        rstep.__dict__["_t0"] -= fanout_s
        rstep.started_at -= timedelta(seconds=fanout_s)
        exc = error_by_connector.get(conn.name)
        if isinstance(exc, InvalidGeoError):
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
        if isinstance(exc, ProviderError):
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
        if exc is not None:  # a dead source degrades, never kills analysis
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
        found = by_connector.get(conn.name, [])
        evidences.extend(found)
        query = _retrieve_query(conn, found)
        detail = f"{conn.name}: status=ok count={len(found)}"
        if query is not None:
            detail += f" query={query!r}"
        _end_step(rstep, detail)

    vs = _start_step(audit, "verify", "code")
    usage_science = None
    if claim.domain == "science":
        comparisons, uncertainties = assess_science(claim, evidences)
        # Additive abstract stance (M3): one bounded model call over the
        # top-3 abstracts; non-verifying by construction. Science-branch
        # only, so general-domain runs stay byte-identical.
        seq_before = _usage_seq(llm)
        extra_c, extra_u = assess_science_abstracts(claim, evidences, llm)
        usage_science = _take_usage(llm, seq_before)
        comparisons.extend(extra_c)
        uncertainties.extend(extra_u)
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

    # Adversarial self-review (M2): the model attacks the draft verdict
    # through a falsify-only schema; code validates every attack and
    # surfaces it as doubt. ADVISORY ONLY — falsify never moves stances
    # (see falsify.py docstring). Degraded-safe no-op without an LLM.
    fs = _start_step(audit, "falsify", "code")
    draft_stance = decide_stance(comparisons, uncertainties, claim)
    seq_before = _usage_seq(llm)
    new_cmps, new_uncs, fals_entry = falsify_draft(
        claim, comparisons, uncertainties, draft_stance, llm
    )
    comparisons.extend(new_cmps)
    uncertainties.extend(new_uncs)
    usage_falsify = _take_usage(llm, seq_before)
    fs.actor = fals_entry.actor
    _end_step(fs, fals_entry.detail)

    ss = _start_step(audit, "synthesize", "code")
    seq_before = _usage_seq(llm)
    conclusion = synthesize(claim, comparisons, uncertainties, llm)
    usage_summary = _take_usage(llm, seq_before)
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
        # Per-stage token usage (None when the stage made no model call).
        "usage_extract": usage_extract,
        "usage_science": usage_science,
        "usage_falsify": usage_falsify,
        "usage_summary": usage_summary,
    }
    report.elapsed_ms = elapsed_ms
    if persist:
        # Re-store the stamped copy (upsert). The first write above is
        # timed by the persist step, so it necessarily precedes
        # meta/elapsed/sha assignment; without this second write,
        # reloaded reports show meta={} elapsed=0 with no rerun proof.
        save_report(report)
    return report


# Stances counted in the check roll-up (must match core.types.Stance).
ROLLUP_STANCES = (
    "supported",
    "partially_supported",
    "contradicted",
    "unverifiable",
    "not_assessable",
)


def _fork_llm(llm):
    """Per-claim model client for parallel batch analysis.

    MetaClient carries mutable per-call state (last_usage + seq), so
    sharing one across threads would misattribute token usage between
    sub-claims: each claim gets a fresh client on the same key/model.
    Stateless doubles (and None) are shared as-is.
    """
    if isinstance(llm, MetaClient):
        return MetaClient(
            api_key=llm.api_key,
            base_url=llm.base_url,
            model=llm.model,
            timeout_s=llm.timeout_s,
        )
    return llm


def _analyze_one(args) -> Report | Exception:
    """Run one sub-claim, capturing its exception instead of raising."""
    sub, llm, limit, persist, domain = args
    try:
        return analyze(sub, llm=llm, limit=limit, persist=persist, domain=domain)
    except Exception as exc:  # same net as the old sequential loop
        return exc


def _analyze_parallel(claims, llm, limit, persist, domain) -> list:
    """Analyze sub-claims on one thread each, results in input order."""
    if not claims:
        return []
    with ThreadPoolExecutor(
        max_workers=len(claims), thread_name_prefix="atlas-batch"
    ) as pool:
        return list(
            pool.map(
                _analyze_one,
                [
                    (sub, _fork_llm(llm), limit, persist, domain)
                    for sub in claims
                ],
            )
        )


def check_document(
    raw_text: str,
    llm=None,
    limit: int = DEFAULT_LIMIT,
    persist: bool = True,
    domain: str = "auto",
) -> dict:
    """ShipCheck batch: split pasted text into claims, analyze each one.

    Returns a JSON-serializable dict (NOT a Report — the roll-up has no
    single claim): {"claim_count", "reports" (full per-claim reports),
    "rollup" (stance counts), "errors" (failed sub-claim count),
    "elapsed_ms", "meta" (splitter provenance)}.
    Each sub-claim runs the full analyze() pipeline, including persist,
    so per-claim report_ids stay re-fetchable via GET /api/reports/{id}.
    A sub-claim that raises does not kill the batch: it is recorded as
    an error entry and counted outside the stance roll-up.
    """
    if domain not in VALID_DOMAINS:
        raise ValueError(f"Invalid domain {domain!r}; expected one of {VALID_DOMAINS}.")
    t_start = time.perf_counter()
    seq_before = _usage_seq(llm)
    claims, actor, model, notes = split_claims(raw_text, llm)
    usage_split = _take_usage(llm, seq_before)
    # Parallel sub-claim analysis (one thread each, input order kept via
    # executor.map). Semantics mirror the old sequential loop exactly:
    # per-claim error isolation, same roll-up, same persistence. No
    # global timeout — each analyze() governs itself, as sequential did.
    # Thread-safety: MetaClient is forked per claim (mutable usage
    # state); connectors keep thread-local query echo; SQLite opens a
    # fresh connection per write; shared doubles are append-only.
    outcomes = _analyze_parallel(claims, llm, limit, persist, domain)
    reports: list[dict] = []
    rollup: dict[str, int] = {s: 0 for s in ROLLUP_STANCES}
    errors = 0
    for sub, outcome in zip(claims, outcomes):
        if isinstance(outcome, Exception):
            errors += 1
            reports.append({"claim_text": sub, "error": _truncate(str(outcome))})
            continue
        stance = outcome.conclusion.stance if outcome.conclusion else "unverifiable"
        rollup[stance] = rollup.get(stance, 0) + 1
        reports.append(outcome.model_dump(mode="json"))
    elapsed_ms = (time.perf_counter() - t_start) * 1000.0
    return {
        "claim_count": len(claims),
        "reports": reports,
        "rollup": rollup,
        "errors": errors,
        "elapsed_ms": elapsed_ms,
        "meta": {
            "app_version": APP_VERSION,
            "prompt_version": PROMPT_VERSION,
            "splitter_actor": actor,
            "splitter_model": model,
            "splitter_notes": notes,
            "usage_split": usage_split,
            "input_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        },
    }
