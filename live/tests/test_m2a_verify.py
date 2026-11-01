"""M2a verifier + library tests. Local fixtures in tmp_path only (no live
network, no model, no files under live/) — $0."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from live.evidence.package import (  # noqa: E402
    EvidencePackage,
    NumberMention,
    sha256_of_text,
)
from live.library.store import EvidenceStore  # noqa: E402
from live.orchestrator.bus import EventBus  # noqa: E402
from live.verifier.check import (  # noqa: E402
    ClaimInput,
    normalize_ws,
    verify_and_emit,
    verify_claim,
)


def _pkg(claim_id, snapshot, quote, url="https://example.org/debt",
         source_type="primary", source_year=2024, extractor="code",
         start=0):
    q = normalize_ws(quote)
    s = normalize_ws(snapshot)
    i = s.find(q)
    assert i >= 0, "fixture quote must occur in fixture snapshot"
    return EvidencePackage(
        package_id=f"pkg_{claim_id}_{source_type}",
        claim_id=claim_id,
        url=url,
        captured_at="2026-01-01T00:00:00+00:00",
        sha256=sha256_of_text(snapshot),
        snapshot_path="data/snap/fixture.txt",
        quote=quote,
        offsets=(start, start + len(quote)),
        source_type=source_type,
        numbers=(NumberMention(value=0.0, unit=None, scale_raw=""),),
        extractor=extractor,
        source_year=source_year,
    )


def test_quote_mismatch_is_unverifiable(tmp_path):
    snap = "The public debt stands at $27.72T in fiscal 2024."
    pkg = EvidencePackage(
        package_id="pkg_qm", claim_id="c_qm", url="https://example.org/x",
        captured_at="2026-01-01T00:00:00+00:00", sha256=sha256_of_text(snap),
        snapshot_path="data/snap/fixture.txt",
        quote="The public debt stands at $99.99T in fiscal 2024.",
        offsets=(0, 10), source_type="primary",
        numbers=(), extractor="code", source_year=2024)
    claim = ClaimInput(claim_id="c_qm", value=27.72e12, unit="USD",
                       raw="$27.72T", claim_year=2024)
    v = verify_claim(claim, [pkg], lambda p: snap)
    assert v.verdict == "unverifiable"
    assert "quote_not_found" in v.reasons


def test_reextracted_number_supports():
    snap = "Report: the public debt stands at $27.72T in fiscal 2024, a record."
    quote = "the public debt stands at $27.72T in fiscal 2024"
    pkg = _pkg("c_ok", snap, quote)
    claim = ClaimInput(claim_id="c_ok", value=27.72e12, unit="USD",
                       raw="$27.72T", claim_year=2024)
    v = verify_claim(claim, [pkg], lambda p: snap)
    assert v.verdict == "supported", v
    assert v.reasons == ["numeric_match"]


def test_explicit_scale_mismatch_contradicts():
    snap = "Reserves total 1.25 million dollars as of March."
    quote = "Reserves total 1.25 million dollars"
    pkg = _pkg("c_sc", snap, quote)
    claim = ClaimInput(claim_id="c_sc", value=1.25e9, unit="USD",
                       raw="1.25 billion", claim_year=None)
    v = verify_claim(claim, [pkg], lambda p: snap)
    assert v.verdict == "contradicted", v
    assert "numeric_mismatch" in v.reasons


def test_scale_slip_abstains_unverifiable():
    snap = "The public debt stands at $27.72T in fiscal 2024."
    quote = "The public debt stands at $27.72T in fiscal 2024"
    pkg = _pkg("c_slip", snap, quote)
    claim = ClaimInput(claim_id="c_slip", value=27.72, unit="USD",
                       raw="27.72", claim_year=2024)
    v = verify_claim(claim, [pkg], lambda p: snap)
    assert v.verdict == "unverifiable", v
    assert "possible_scale_error" in v.reasons


def test_temporal_mismatch_is_unverifiable():
    snap = "The public debt stood at $27.72T in fiscal 2020."
    quote = "The public debt stood at $27.72T in fiscal 2020"
    pkg = _pkg("c_tm", snap, quote, source_year=2020)
    claim = ClaimInput(claim_id="c_tm", value=27.72e12, unit="USD",
                       raw="$27.72T", claim_year=2024)
    v = verify_claim(claim, [pkg], lambda p: snap)
    assert v.verdict == "unverifiable", v
    assert "temporal_mismatch" in v.reasons


def test_primary_beats_secondary():
    snap_p = "The public debt stands at $27.72T in fiscal 2024."
    qp = "The public debt stands at $27.72T in fiscal 2024"
    snap_s = "Blog: the debt is only $10.00T this year, experts say."
    qs = "the debt is only $10.00T this year"
    pp = _pkg("c_h", snap_p, qp, source_type="primary")
    ps = _pkg("c_h", snap_s, qs, source_type="secondary")
    loader = lambda p: snap_p if p.source_type == "primary" else snap_s  # noqa: E731
    claim = ClaimInput(claim_id="c_h", value=27.72e12, unit="USD",
                       raw="$27.72T", claim_year=2024)
    v = verify_claim(claim, [pp, ps], loader)
    assert v.verdict == "supported", v
    assert v.details.get("hierarchy") == "primary_beats_secondary"


def test_two_primaries_disagree_is_disputed():
    snap_a = "The public debt stands at $27.72T in fiscal 2024."
    qa = "The public debt stands at $27.72T in fiscal 2024"
    snap_b = "The public debt stands at $10.00T in fiscal 2024."
    qb = "The public debt stands at $10.00T in fiscal 2024"
    pa = _pkg("c_d", snap_a, qa, source_type="primary")
    pb = EvidencePackage(
        package_id="pkg_c_d_primary_b", claim_id="c_d",
        url="https://example.org/other",
        captured_at="2026-01-01T00:00:00+00:00", sha256=sha256_of_text(snap_b),
        snapshot_path="data/snap/fixture.txt", quote=qb, offsets=(0, len(qb)),
        source_type="primary", numbers=(), extractor="code", source_year=2024)
    loader = lambda p: snap_a if p.package_id.endswith("primary") else snap_b  # noqa: E731
    claim = ClaimInput(claim_id="c_d", value=27.72e12, unit="USD",
                       raw="$27.72T", claim_year=2024)
    v = verify_claim(claim, [pa, pb], loader)
    assert v.verdict == "disputed", v
    assert "packages_disagree" in v.reasons


def test_no_package_is_always_unverifiable():
    claim = ClaimInput(claim_id="c_np", value=1.0, unit="USD", raw="1 dollar")
    v = verify_claim(claim, [], lambda p: None)
    assert v.verdict == "unverifiable"
    assert "no_usable_package" in v.reasons


def test_es_thousands_reextraction():
    snap = "El fondo asciende a 1.400.000 euros en 2024."
    quote = "El fondo asciende a 1.400.000 euros en 2024"
    pkg = _pkg("c_es", snap, quote, source_year=2024)
    claim = ClaimInput(claim_id="c_es", value=1400000.0, unit="EUR",
                       raw="1.400.000 euros", claim_year=2024)
    v = verify_claim(claim, [pkg], lambda p: snap)
    assert v.verdict == "supported", v


def test_verdict_emitted_on_bus():
    async def go():
        bus = EventBus()
        seen = []
        bus.subscribe("claim.verdict", lambda e: seen.append(e))
        snap = "The public debt stands at $27.72T in fiscal 2024."
        quote = "The public debt stands at $27.72T in fiscal 2024"
        pkg = _pkg("c_bus", snap, quote)
        claim = ClaimInput(claim_id="c_bus", value=27.72e12, unit="USD",
                           raw="$27.72T", claim_year=2024)
        v = await verify_and_emit(claim, [pkg], lambda p: snap, bus)
        assert v.verdict == "supported"
        assert len(seen) == 1
        assert seen[0]["type"] == "claim.verdict"
        assert seen[0]["claim_id"] == "c_bus"
        assert seen[0]["verdict"] == "supported"
        await bus.close()

    asyncio.run(go())


def test_package_contract_validation():
    import pytest

    good = dict(package_id="p1", claim_id="c1", url="https://example.org/",
                captured_at="2026-01-01T00:00:00+00:00",
                sha256="a" * 64, snapshot_path="data/snap/x.txt",
                quote="literal quote", offsets=(0, 5))
    EvidencePackage(**good)  # valid: no raise
    bad_sha = dict(good, sha256="zzz")
    with pytest.raises(ValueError):
        EvidencePackage(**bad_sha)
    bad_type = dict(good, source_type="blog")
    with pytest.raises(ValueError):
        EvidencePackage(**bad_type)
    bad_off = dict(good, offsets=(9, 2))
    with pytest.raises(ValueError):
        EvidencePackage(**bad_off)
    bad_quote = dict(good, quote="  ")
    with pytest.raises(ValueError):
        EvidencePackage(**bad_quote)


def test_fts5_search_and_reuse_between_claims(tmp_path):
    store = EvidenceStore(tmp_path / "lib.db", tmp_path / "snap")
    debt = ("U.S. Treasury report: the public debt stands at $27.72T "
            "in fiscal 2024, a new record for federal borrowing.")
    pop = ("World Bank data: Brazil has 212 million people in 2024, "
           "growing slowly year over year.")
    r_debt = store.upsert_snapshot("https://treasury.example/debt", debt,
                                   "primary")
    r_pop = store.upsert_snapshot("https://worldbank.example/pop", pop,
                                  "primary")
    # BM25 ranks the debt snapshot first for a debt query.
    hits = store.search("public debt $27.72T treasury borrowing")
    assert hits and hits[0].sha256 == r_debt.sha256, [h.url for h in hits]
    hits_pop = store.search("Brazil 212 million people")
    assert hits_pop and hits_pop[0].sha256 == r_pop.sha256
    # Dedup: same text is stored once.
    again = store.upsert_snapshot("https://treasury.example/debt", debt, "primary")
    assert again.sha256 == r_debt.sha256
    assert store.get_snapshot_text(r_debt.sha256) == debt
    # Reuse between claims: claim B reuses claim A's snapshot (same sha).
    quote = "the public debt stands at $27.72T in fiscal 2024"
    assert normalize_ws(quote) in normalize_ws(debt)
    pa = EvidencePackage(
        package_id="pkg_reuse_a", claim_id="c_reuse_a",
        url="https://treasury.example/debt",
        captured_at="2026-01-01T00:00:00+00:00", sha256=r_debt.sha256,
        snapshot_path=r_debt.path, quote=quote, offsets=(0, len(quote)),
        source_type="primary", numbers=(), extractor="code", source_year=2024)
    pb = EvidencePackage(
        package_id="pkg_reuse_b", claim_id="c_reuse_b",
        url="https://treasury.example/debt",
        captured_at="2026-01-01T00:00:00+00:00", sha256=r_debt.sha256,
        snapshot_path=r_debt.path, quote=quote, offsets=(0, len(quote)),
        source_type="primary", numbers=(), extractor="code", source_year=2024)
    store.store_package(pa)
    store.store_package(pb)
    assert store.get_package("pkg_reuse_a").sha256 == r_debt.sha256
    assert len(store.packages_for_snapshot(r_debt.sha256)) == 2
    # Both claims verify supported off the SAME snapshot bytes.
    loader = lambda p: store.get_snapshot_text(p.sha256)  # noqa: E731
    for cid, pkg in (("c_reuse_a", pa), ("c_reuse_b", pb)):
        claim = ClaimInput(claim_id=cid, value=27.72e12, unit="USD",
                           raw="$27.72T", claim_year=2024)
        v = verify_claim(claim, [pkg], loader)
        assert v.verdict == "supported", (cid, v)
    store.close()
