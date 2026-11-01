"""Swarm coordinator: parallel roles per claim + deterministic arbiter.

Flow per claim (``analyze_with_swarm``):

1. Fork the model client per worker (MetaClient carries mutable usage
   state; a fork per role + one for the pipeline keeps token
   attribution exact). Stateless doubles are shared as-is.
2. Run the full pipeline (``atlas.pipeline.run.analyze``) and the four
   roles concurrently on a ThreadPool (fork per claim inherited from
   the pipeline's own batching when claims run in parallel).
3. The ARBITER decides in code — deterministic rules, no model vote:

   - verdict/stance = the pipeline report's conclusion, verbatim;
   - roles may only ADD uncertainties / surface notes;
   - any role conflict (red-team high attack, struct mismatch vs the
     pipeline extraction, uncovered candidate source) becomes a
     ``major`` (or ``minor`` for pure advisories) Uncertainty;
   - the stance is NEVER flipped by roles: a conflicted claim keeps
     its pipeline stance with a major uncertainty attached — never
     ``contradicted`` by vote.

Long documents (``check_document_swarm``): chunk the text into
splitter-sized windows, split each into claims, dedupe, verify every
claim through the swarm, and report per-document wall-time and cost.

Cost model: USD is an ESTIMATE from recorded token usage at assumed
rates (DEFAULT_PRICING). Token counts are exact; the dollar figure is
labeled estimate everywhere it appears.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..core.types import Report, Uncertainty
from ..pipeline.run import DEFAULT_LIMIT, analyze
from ..pipeline.split import (
    MAX_SUBCLAIMS,
    SPLIT_INPUT_CHARS,
    split_claims,
)
from .roles import ROLE_NAMES, ROLE_RUNNERS

log = logging.getLogger(__name__)

SWARM_VERSION = "1.0.0"

# Chunk windows stay under the splitter's truncation limit so no chunk
# is silently cut (margin for the "[ROLE..]"-free split prompt).
CHUNK_CHARS = SPLIT_INPUT_CHARS - 500
# Cap uncertainties the swarm may attach to one report (advisory flood
# control; the pipeline's own list is untouched).
_MAX_SWARM_UNCERTAINTIES = 8
# Cap per-claim role workers + pipeline worker.
_POOL_WORKERS = len(ROLE_NAMES) + 1

# Assumed blended rates for the USD ESTIMATE (see module docstring).
# Tokens are exact; dollars are approximate and disclosed as such.
DEFAULT_PRICING = {
    "prompt_usd_per_1k": 0.0003,
    "completion_usd_per_1k": 0.0012,
    "note": "assumed rates; USD figures are estimates, token counts exact",
}

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])\s+|\n+")


@dataclass
class SwarmResult:
    claim_text: str
    report: Report
    roles: dict = field(default_factory=dict)
    arbiter: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0
    usage_total: dict = field(default_factory=dict)
    usd_estimate: float = 0.0

    @property
    def stance(self) -> str:
        c = self.report.conclusion
        return c.stance if c is not None else "unverifiable"

    def to_dict(self) -> dict:
        return {
            "claim_text": self.claim_text,
            "stance": self.stance,
            "report": self.report.model_dump(mode="json"),
            "roles": {k: _role_to_json(v) for k, v in self.roles.items()},
            "arbiter": self.arbiter,
            "elapsed_ms": self.elapsed_ms,
            "usage_total": self.usage_total,
            "usd_estimate": self.usd_estimate,
        }


def _role_to_json(outcome: dict) -> dict:
    return {
        "ok": outcome.get("ok"),
        "skipped": outcome.get("skipped"),
        "data": outcome.get("data", {}),
        "notes": outcome.get("notes", []),
        "usage": outcome.get("usage"),
    }


def _fork_llm(llm):
    """Per-worker client: fork stateful clients, share doubles/None."""
    fork = getattr(llm, "fork", None)
    if callable(fork):
        try:
            return fork()
        except Exception:
            return llm
    return llm


def _run_role(name: str, claim_text: str, llm, context: str) -> dict:
    try:
        return ROLE_RUNNERS[name](claim_text, llm, context)
    except Exception as exc:  # roles degrade, never kill the claim
        log.warning("Swarm role %s crashed, degraded: %r", name, exc)
        return {"role": name, "ok": False, "skipped": None,
                "data": {}, "notes": [f"role crashed: {str(exc)[:200]}"],
                "usage": None}


def run_roles_parallel(claim_text: str, llm=None, context: str = "") -> dict:
    """Run the four roles concurrently; keyed by role name.

    Each role gets its own forked client so token usage attributes
    exactly. With llm=None every role reports skipped.
    """
    if llm is None:
        return {n: ROLE_RUNNERS[n](claim_text, None, context)
                for n in ROLE_NAMES}
    workers = {n: _fork_llm(llm) for n in ROLE_NAMES}
    with ThreadPoolExecutor(max_workers=len(ROLE_NAMES),
                            thread_name_prefix="atlas-swarm") as pool:
        futs = {n: pool.submit(_run_role, n, claim_text, workers[n], context)
                for n in ROLE_NAMES}
        return {n: futs[n].result() for n in ROLE_NAMES}


def _add_swarm_uncertainty(report: Report, added: list[str],
                           code: str, description: str,
                           severity: str = "major") -> None:
    if len(added) >= _MAX_SWARM_UNCERTAINTIES:
        return
    report.uncertainties.append(Uncertainty(
        code=code, description=description, severity=severity,
        related_ids=[report.claim.claim_id]))
    added.append(code)


def _covered_by_evidence(candidate_source: str, connectors: set[str]) -> bool:
    cand = re.sub(r"[^a-z0-9]", "", candidate_source.strip().lower())
    if not cand:
        return True  # empty sources never validate; treat as moot, not uncovered
    for conn in connectors:
        c = re.sub(r"[^a-z0-9]", "", conn.lower())
        if cand in c or c in cand:
            return True
    return False


def arbitrate(report: Report, roles: dict, claim_text: str) -> dict:
    """Deterministic arbiter. Reads the pipeline verdict; only ADDS doubt.

    Rules (all in code, no model vote):
    - stance = report.conclusion.stance, copied, never recomputed;
    - red-team high-severity attack -> major `swarm_redteam_challenge`
      (+ each attack also logged as minor `swarm_redteam_note`);
    - estructurador quantity-count or domain mismatch vs the pipeline
      extraction -> major `swarm_struct_mismatch`;
    - recuperador candidate naming a source with no retrieved evidence
      -> minor `swarm_uncovered_candidate` (advisory);
    - explicador summary stored on the arbiter record, never merged
      into the conclusion.

    A conflicted claim keeps its pipeline stance with a major
    uncertainty attached. Roles can never produce `contradicted`.
    """
    stance = report.conclusion.stance if report.conclusion else "unverifiable"
    conflicts: list[str] = []
    added: list[str] = []
    roles_ok = sorted(n for n, o in roles.items() if o.get("ok"))
    roles_skipped = sorted(n for n, o in roles.items()
                           if not o.get("ok") and o.get("skipped"))
    roles_failed = sorted(n for n, o in roles.items()
                          if not o.get("ok") and not o.get("skipped"))

    struct = roles.get("estructurador", {})
    if struct.get("ok"):
        data = struct.get("data", {})
        n_role = len(data.get("quantities", []))
        n_pipe = len(report.claim.quantities)
        if n_role != n_pipe:
            conflicts.append(
                f"estructurador mapped {n_role} quantities vs pipeline "
                f"{n_pipe}")
            _add_swarm_uncertainty(
                report, added, "swarm_struct_mismatch",
                f"Swarm estructurador mapped {n_role} quantities while the "
                f"pipeline extraction holds {n_pipe}; the pipeline figures "
                "stand and the mismatch is disclosed, not resolved by vote.")
        if data.get("domain") and data["domain"] != report.claim.domain:
            conflicts.append(
                f"estructurador domain {data['domain']} vs pipeline "
                f"{report.claim.domain}")
            _add_swarm_uncertainty(
                report, added, "swarm_struct_mismatch",
                f"Swarm estructurador routed domain={data['domain']} while "
                f"the pipeline used domain={report.claim.domain}; pipeline "
                "routing stands.")

    retr = roles.get("recuperador", {})
    if retr.get("ok"):
        connectors = {e.connector for e in report.evidences}
        for cand in retr.get("data", {}).get("candidates", []):
            src = cand.get("source", "")
            if not _covered_by_evidence(src, connectors):
                _add_swarm_uncertainty(
                    report, added, "swarm_uncovered_candidate",
                    f"Swarm recuperador suggested source {src!r} "
                    f"(indicator: {cand.get('indicator', '')!r}) with no "
                    "retrieved evidence from it; advisory only.",
                    severity="minor")

    red = roles.get("red_team", {})
    if red.get("ok"):
        attacks = red.get("data", {}).get("attacks", [])
        highs = [a for a in attacks if a.get("severity") == "high"]
        for a in attacks:
            _add_swarm_uncertainty(
                report, added, "swarm_redteam_note",
                f"Swarm red-team [{a.get('severity')}] on "
                f"{a.get('claim_span', '')!r}: {a.get('weakness', '')}",
                severity="minor")
        if highs:
            conflicts.append(f"red-team raised {len(highs)} high-severity "
                             "attack(s)")
            _add_swarm_uncertainty(
                report, added, "swarm_redteam_challenge",
                f"Swarm red-team raised {len(highs)} high-severity "
                "challenge(s); the pipeline stance stands and the "
                "challenge is disclosed as doubt, never as a flip.")

    expl = roles.get("explicador", {})
    plain = expl.get("data", {}).get("summary", "") if expl.get("ok") else ""

    return {
        "verdict_source": "pipeline",
        "stance": stance,
        "roles_ok": roles_ok,
        "roles_skipped": roles_skipped,
        "roles_failed": roles_failed,
        "conflicts": conflicts,
        "uncertainties_added": added,
        "plain_summary": plain,
    }


def sum_usages(usages: list[dict | None]) -> dict:
    """Add token counters across usage dicts (None-safe, int-safe)."""
    total = {"prompt_tokens": 0, "completion_tokens": 0,
             "reasoning_tokens": 0, "total_tokens": 0,
             "cached_tokens": 0, "stages": 0}
    for u in usages:
        if not isinstance(u, dict):
            continue
        total["stages"] += 1
        for key in ("prompt_tokens", "completion_tokens",
                    "reasoning_tokens", "total_tokens", "cached_tokens"):
            val = u.get(key)
            if isinstance(val, int):
                total[key] += val
    return total


def estimate_usd(usage_total: dict, pricing: dict | None = None) -> float:
    """USD ESTIMATE from token totals at assumed rates (see DEFAULT_PRICING)."""
    pricing = pricing or DEFAULT_PRICING
    prompt = usage_total.get("prompt_tokens", 0) or 0
    completion = usage_total.get("completion_tokens", 0) or 0
    return (prompt / 1000.0 * pricing["prompt_usd_per_1k"]
            + completion / 1000.0 * pricing["completion_usd_per_1k"])


def _report_usages(report: Report) -> list[dict | None]:
    meta = report.meta if isinstance(report.meta, dict) else {}
    return [meta.get(f"usage_{stage}")
            for stage in ("extract", "science", "falsify", "summary")]


def analyze_with_swarm(claim_text: str, llm=None, limit: int = DEFAULT_LIMIT,
                       persist: bool = False,
                       domain: str = "auto") -> SwarmResult:
    """One claim through pipeline + roles in parallel, then the arbiter.

    ``persist`` defaults to False: the swarm is an eval harness and must
    not spam the report store on long-document runs unless asked.
    """
    t0 = time.perf_counter()
    if llm is None:
        report = analyze(claim_text, llm=None, limit=limit,
                         persist=persist, domain=domain)
        roles = run_roles_parallel(claim_text, None)
    else:
        with ThreadPoolExecutor(max_workers=_POOL_WORKERS,
                                thread_name_prefix="atlas-swarm") as pool:
            fut_pipe = pool.submit(analyze, claim_text,
                                   _fork_llm(llm), limit, persist, domain)
            futs = {n: pool.submit(_run_role, n, claim_text,
                                   _fork_llm(llm), "")
                    for n in ROLE_NAMES}
            report = fut_pipe.result()
            roles = {n: futs[n].result() for n in ROLE_NAMES}
    arbiter = arbitrate(report, roles, claim_text)
    usage_total = sum_usages(
        _report_usages(report)
        + [o.get("usage") for o in roles.values()])
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return SwarmResult(
        claim_text=claim_text, report=report, roles=roles,
        arbiter=arbiter, elapsed_ms=elapsed_ms,
        usage_total=usage_total,
        usd_estimate=estimate_usd(usage_total))


def chunk_document(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Greedy paragraph-aware chunking. Deterministic, model-free.

    Paragraphs accumulate until the window fills; an overlong paragraph
    splits on sentence boundaries; a still-overlong sentence is hard-cut
    (rare; disclosed by length, never silently dropped — every input
    char lands in exactly one chunk).
    """
    clean = text.strip()
    if not clean:
        return []
    if len(clean) <= max_chars:
        return [clean]
    paras: list[str] = []
    for para in re.split(r"\n\s*\n", clean):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            paras.append(para)
            continue
        for sent in _SENTENCE_BOUNDARY.split(para):
            sent = sent.strip()
            if not sent:
                continue
            if len(sent) <= max_chars:
                paras.append(sent)
            else:  # hard cut: every char still lands in some chunk
                paras.extend(sent[i:i + max_chars]
                             for i in range(0, len(sent), max_chars))
    chunks: list[str] = []
    buf = ""
    for para in paras:
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) <= max_chars:
            buf = candidate
        else:
            if buf:
                chunks.append(buf)
            buf = para
    if buf:
        chunks.append(buf)
    return chunks


def _normalize_span(span: str) -> str:
    return re.sub(r"\s+", " ", span).strip().lower()


def check_document_swarm(raw_text: str, llm=None, limit: int = 3,
                         persist: bool = False, domain: str = "auto",
                         max_claims: int | None = None,
                         on_claim=None) -> dict:
    """Long-document swarm run: chunk -> split -> map ALL figures -> verify.

    Returns a JSON-serializable dict: per-claim swarm entries (stance,
    roles, conflicts, figures mapped), stance roll-up, error count,
    token totals, USD estimate, and wall-time. Claims run sequentially
    (each already fans out to 5 workers); a failing claim is recorded
    as an error entry and never kills the document.
    """
    t_start = time.perf_counter()
    chunks = chunk_document(raw_text)
    notes: list[str] = [f"document split into {len(chunks)} chunk(s) "
                        f"(window={CHUNK_CHARS} chars)"]
    seen: set[str] = set()
    claims: list[str] = []
    split_actors: list[str] = []
    for chunk in chunks:
        spans, actor, _model, _notes = split_claims(chunk, llm)
        split_actors.append(actor)
        for span in spans:
            key = _normalize_span(span)
            if key and key not in seen:
                seen.add(key)
                claims.append(span)
    if max_claims is not None and len(claims) > max_claims:
        notes.append(f"capped claims from {len(claims)} to {max_claims}")
        claims = claims[:max_claims]
    notes.append(f"split actors per chunk: {','.join(split_actors) or 'none'}")

    entries: list[dict] = []
    rollup: dict[str, int] = {}
    errors = 0
    figures_mapped = 0
    usage_acc = {"prompt_tokens": 0, "completion_tokens": 0,
                 "reasoning_tokens": 0, "total_tokens": 0,
                 "cached_tokens": 0, "stages": 0}
    for i, claim_text in enumerate(claims):
        try:
            res = analyze_with_swarm(claim_text, llm=llm, limit=limit,
                                     persist=persist, domain=domain)
        except Exception as exc:  # one bad claim never kills the document
            errors += 1
            entries.append({"index": i, "claim_text": claim_text,
                            "error": str(exc)[:200]})
            if on_claim is not None:
                try:
                    on_claim(i, None)
                except Exception:
                    log.debug("on_claim delivery failed", exc_info=True)
            continue
        rollup[res.stance] = rollup.get(res.stance, 0) + 1
        figures_mapped += len(res.report.claim.quantities)
        for key in usage_acc:
            usage_acc[key] += res.usage_total.get(key, 0)
        entries.append({
            "index": i,
            "claim_text": claim_text,
            "stance": res.stance,
            "figures_mapped": len(res.report.claim.quantities),
            "roles_ok": res.arbiter["roles_ok"],
            "roles_failed": res.arbiter["roles_failed"],
            "conflicts": res.arbiter["conflicts"],
            "uncertainties_added": res.arbiter["uncertainties_added"],
            "plain_summary": res.arbiter["plain_summary"],
            "usage": res.usage_total,
            "usd_estimate": res.usd_estimate,
            "elapsed_ms": res.elapsed_ms,
        })
        if on_claim is not None:
            try:
                on_claim(i, res)
            except Exception:
                log.debug("on_claim delivery failed", exc_info=True)
    elapsed_ms = (time.perf_counter() - t_start) * 1000.0
    usd = estimate_usd(usage_acc)
    return {
        "swarm_version": SWARM_VERSION,
        "claim_count": len(claims),
        "claims": entries,
        "rollup": rollup,
        "errors": errors,
        "figures_mapped": figures_mapped,
        "elapsed_ms": elapsed_ms,
        "usage_total": usage_acc,
        "usd_estimate": usd,
        "meta": {
            "chunks": len(chunks),
            "chunk_chars": CHUNK_CHARS,
            "max_subclaims_per_chunk": MAX_SUBCLAIMS,
            "pricing": dict(DEFAULT_PRICING),
            "notes": notes,
            "input_chars": len(raw_text),
            "input_sha256": hashlib.sha256(
                raw_text.encode("utf-8")).hexdigest(),
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        },
    }
