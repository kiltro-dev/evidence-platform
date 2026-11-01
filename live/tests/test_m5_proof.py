"""M5 proof tests: skeptic + editor + bundle (offline) + one gated LIVE run.

Offline tests use FakeLLM/FakeSearch/FakeFetch doubles and the REAL
M2a verifier (``verify_claim``) + real ZIP roundtrips — $0. The LIVE
test (``ATLAS_LIVE=1``) runs 1 real skeptic attack + 1 real editor card
on TRUE keynote claim T2-en with a measured budget of <= $1.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import zipfile

import pytest

from live.bundle.export import export_bundle
from live.bundle.verify_bundle import verify_bundle
from live.editor.cards import CardConfig, Editor
from live.evidence.package import EvidencePackage, sha256_of_text
from live.orchestrator.bus import EventBus
from live.orchestrator.states import ClaimTracker
from live.skeptic.attack import AttackConfig, run_attack
from live.swarm.coordinator import numbers_from_quote, source_type_for_url
from live.swarm.fetch import FetchError, FetchResult
from live.verifier.check import ClaimInput, verify_and_emit, verify_claim

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


# ---------------------------------------------------------------------------
# doubles
# ---------------------------------------------------------------------------

class FakeLLM:
    name = "fake-llm"
    available = True

    def __init__(self, script, prompt_tokens=100, completion_tokens=20):
        self._script = list(script)
        self.calls = []
        self.last_usage = None
        self.last_usage_seq = 0
        self._pt = prompt_tokens
        self._ct = completion_tokens

    def complete_json(self, system, user, max_tokens=1500,
                      reasoning_effort=None, json_schema=None):
        self.calls.append({"system": system, "user": user,
                           "max_tokens": max_tokens,
                           "reasoning_effort": reasoning_effort,
                           "schema": bool(json_schema)})
        self.last_usage_seq += 1
        self.last_usage = {"prompt_tokens": self._pt,
                           "completion_tokens": self._ct,
                           "reasoning_tokens": 0,
                           "total_tokens": self._pt + self._ct,
                           "cached_tokens": 0}
        return self._script.pop(0)


class FailingLLM(FakeLLM):
    available = False

    def complete_json(self, *a, **k):
        raise RuntimeError("no model")


def make_fetch_fn(bodies, snap_dir, fail_urls=()):
    def _fake(url, snapshot_dir=None):
        for sub in fail_urls:
            if sub in url:
                raise FetchError(f"boom {url}")
        for sub, body in bodies.items():
            if sub in url:
                raw = body.encode("utf-8")
                sha = hashlib.sha256(raw).hexdigest()
                os.makedirs(snap_dir, exist_ok=True)
                path = os.path.join(snap_dir, f"{sha}.html")
                with open(path, "wb") as fh:
                    fh.write(raw)
                return FetchResult(
                    url=url, final_url=url, status=200,
                    content_type="text/html; charset=utf-8",
                    size=len(raw), sha256=sha, snapshot_path=path,
                    captured_at="2026-11-01T00:00:00+00:00", text=body)
        raise FetchError(f"404 {url}")

    return _fake


def make_search_fn(hits_by_query, seen=None):
    def _fake(query, top_k=3):
        if seen is not None:
            seen.append(query)
        for sub, hits in hits_by_query.items():
            if sub.lower() in query.lower():
                return hits[:top_k]
        return hits_by_query.get("*", [])[:top_k]

    return _fake


SUPPORT_SNAP = ("China is home to 1.41 billion people, the World Bank "
                "reported for 2024.")
SUPPORT_QUOTE = "home to 1.41 billion people"
CONTRA_BODY = ("Revised estimates put China at only 1.10 billion people "
               "in 2024, demographers say.")
CONTRA_QUOTE = "China at only 1.10 billion people in 2024"
CONTRA_URL = "https://example.org/china-revised"


def _package_dict(pkg_id, claim_id, url, snapshot, quote,
                  source_type="secondary", extractor="code",
                  source_year=None):
    start = snapshot.find(quote)
    assert start >= 0, "fixture quote must be verbatim"
    return {
        "package_id": pkg_id, "claim_id": claim_id, "url": url,
        "captured_at": "2026-11-01T00:00:00+00:00",
        "sha256": sha256_of_text(snapshot),
        "snapshot_path": f"data/snap/{sha256_of_text(snapshot)}.html",
        "quote": quote, "offsets": [start, start + len(quote)],
        "source_type": source_type,
        "numbers": numbers_from_quote(quote),
        "extractor": extractor, "source_year": source_year,
    }


def _claim_t2():
    return ClaimInput(claim_id="T2-en",
                      text="China is home to 1.41 billion people.",
                      value=1.41e9, unit="people", raw="1.41 billion",
                      claim_year=2024)


def _tmp_loader(snap_dir):
    def _load(pkg):
        base = os.path.basename(pkg.snapshot_path)
        cand = os.path.join(snap_dir, base)
        if os.path.isfile(cand):
            with open(cand, encoding="utf-8") as fh:
                return fh.read()
        return None

    return _load


def _write_tmp_snapshot(snap_dir, text):
    sha = sha256_of_text(text)
    os.makedirs(snap_dir, exist_ok=True)
    with open(os.path.join(snap_dir, f"{sha}.html"),
              "w", encoding="utf-8") as fh:
        fh.write(text)
    return sha


SKEPTIC_PLAN = {
    "counter_queries": [{"query": "china population revised 2024",
                         "rationale": "2024 figure may be revised"}],
    "skip": False, "skip_reason": "",
}


# ---------------------------------------------------------------------------
# skeptic: challenge-or-note gating (REAL verifier underneath)
# ---------------------------------------------------------------------------

def _supported_setup(tmp_path):
    snap_dir = str(tmp_path / "snap")
    _write_tmp_snapshot(snap_dir, SUPPORT_SNAP)
    pkg = _package_dict("pkg_t2_1", "T2-en", "https://example.org/china",
                        SUPPORT_SNAP, SUPPORT_QUOTE)
    claim = _claim_t2()
    verdict = verify_claim(claim, [EvidencePackage.from_dict(pkg)],
                           _tmp_loader(snap_dir))
    assert verdict.verdict == "supported", verdict
    return snap_dir, claim, verdict, pkg


def test_skeptic_challenge_on_contradicting_evidence(tmp_path):
    snap_dir, claim, verdict, pkg = _supported_setup(tmp_path)
    search = make_search_fn({
        "*": [{"url": CONTRA_URL, "title": "Revised", "snippet": ""}],
    })
    llm = FakeLLM([dict(SKEPTIC_PLAN),
                   {"quote": CONTRA_QUOTE, "targets_claim": True,
                    "rationale": "22% below the claim"}])
    config = AttackConfig(
        llm=llm, search_fn=search,
        fetch_fn=make_fetch_fn({"revised": CONTRA_BODY}, snap_dir))
    bus = EventBus()
    result = asyncio.run(run_attack(bus, claim, verdict, [pkg],
                                    _tmp_loader(snap_dir), config))
    assert result.challenged is True
    assert result.counter_query == "china population revised 2024"
    assert result.package is not None
    # GATE A holds on the published dict; quote greps in its snapshot.
    EvidencePackage.from_dict(result.package)
    snap = open(os.path.join(
        snap_dir, f"{result.package['sha256']}.html"),
        encoding="utf-8").read()
    assert result.package["quote"] in snap
    assert result.package["package_id"].startswith("m5s-T2-en-")
    challenges = bus.history("skeptic.challenge")
    assert len(challenges) == 1
    assert challenges[0]["package"] == result.package
    assert bus.history("skeptic.note") == []
    assert result.cost_usd > 0 and result.usage["model_calls"] == 2
    # reasoning minimal + structured output on every call.
    assert all(c["reasoning_effort"] == "minimal" for c in llm.calls)
    assert all(c["schema"] for c in llm.calls)


def test_skeptic_note_when_quote_invented(tmp_path):
    snap_dir, claim, verdict, pkg = _supported_setup(tmp_path)
    search = make_search_fn({
        "*": [{"url": CONTRA_URL, "title": "R", "snippet": ""}],
    })
    llm = FakeLLM([dict(SKEPTIC_PLAN),
                   {"quote": "the moon is made of cheese",
                    "targets_claim": True, "rationale": "lying"}])
    config = AttackConfig(
        llm=llm, search_fn=search,
        fetch_fn=make_fetch_fn({"revised": CONTRA_BODY}, snap_dir))
    bus = EventBus()
    result = asyncio.run(run_attack(bus, claim, verdict, [pkg],
                                    _tmp_loader(snap_dir), config))
    assert result.challenged is False
    assert result.package is None
    assert "non-verbatim-quote" in result.note
    assert bus.history("skeptic.challenge") == []
    notes = bus.history("skeptic.note")
    assert len(notes) == 1 and notes[0]["challenged"] is False


def test_skeptic_note_when_attack_corrorborates(tmp_path):
    snap_dir, claim, verdict, pkg = _supported_setup(tmp_path)
    corroborate = ("Beijing confirms China is home to 1.41 billion people "
                   "this year.")
    search = make_search_fn({
        "*": [{"url": "https://example.org/china-confirm",
               "title": "C", "snippet": ""}],
    })
    llm = FakeLLM([dict(SKEPTIC_PLAN),
                   {"quote": "home to 1.41 billion people this year",
                    "targets_claim": True, "rationale": "check"}])
    config = AttackConfig(
        llm=llm, search_fn=search,
        fetch_fn=make_fetch_fn({"confirm": corroborate}, snap_dir))
    bus = EventBus()
    result = asyncio.run(run_attack(bus, claim, verdict, [pkg],
                                    _tmp_loader(snap_dir), config))
    assert result.challenged is False
    assert "corroborated" in result.note
    assert bus.history("skeptic.challenge") == []


def test_skeptic_skips_non_supported_without_model_calls(tmp_path):
    snap_dir = str(tmp_path / "snap")
    claim = _claim_t2()
    verdict = {"verdict": "contradicted", "reasons": ["numeric_mismatch"],
               "package_ids": []}
    llm = FakeLLM([dict(SKEPTIC_PLAN)])
    bus = EventBus()
    result = asyncio.run(run_attack(
        bus, claim, verdict, [], _tmp_loader(snap_dir),
        AttackConfig(llm=llm)))
    assert result.challenged is False
    assert "only supported verdicts" in result.note
    assert llm.calls == []
    assert len(bus.history("skeptic.note")) == 1


def test_skeptic_note_when_no_model(tmp_path):
    snap_dir, claim, verdict, pkg = _supported_setup(tmp_path)
    bus = EventBus()
    result = asyncio.run(run_attack(
        bus, claim, verdict, [pkg], _tmp_loader(snap_dir),
        AttackConfig(llm=FailingLLM([]))))
    assert result.challenged is False
    assert result.note == "no model available"


def test_skeptic_note_when_fetch_fails(tmp_path):
    snap_dir, claim, verdict, pkg = _supported_setup(tmp_path)
    search = make_search_fn({
        "*": [{"url": CONTRA_URL, "title": "R", "snippet": ""}],
    })
    llm = FakeLLM([dict(SKEPTIC_PLAN)])
    config = AttackConfig(
        llm=llm, search_fn=search,
        fetch_fn=make_fetch_fn({}, snap_dir))  # every URL 404s
    bus = EventBus()
    result = asyncio.run(run_attack(bus, claim, verdict, [pkg],
                                    _tmp_loader(snap_dir), config))
    assert result.challenged is False
    assert "FetchError" in result.note or "no fresh" in result.note
    assert len(llm.calls) == 1  # extract call never spent


# ---------------------------------------------------------------------------
# editor: citation confinement + corrections
# ---------------------------------------------------------------------------

def _verdict_dict(label="supported", ids=("pkg_1",)):
    return {"verdict": label, "reasons": ["numeric_match"],
            "package_ids": list(ids)}


def _pkg_row(pid, quote="home to 1.41 billion people"):
    return {"package_id": pid, "claim_id": "T2-en",
            "url": f"https://example.org/{pid}", "quote": quote}


_GOOD_DRAFT = {
    "headline_es": "China tiene 1410 millones de habitantes",
    "headline_en": "China is home to 1.41 billion people",
    "body_es": "La cifra coincide con la fuente verificada citada.",
    "body_en": "The figure matches the verified source cited.",
    "citations": ["pkg_1"],
}


def test_editor_card_happy_path():
    bus = EventBus()
    editor = Editor(CardConfig(llm=FakeLLM([dict(_GOOD_DRAFT)])))
    card = asyncio.run(editor.emit_card(
        bus, claim_id="T2-en", claim_text="China is home to 1.41 billion.",
        verdict=_verdict_dict(), packages=[_pkg_row("pkg_1")]))
    assert card.event_type == "card.published"
    assert card.fallback is False
    assert card.citations == ["pkg_1"]
    assert card.headline_es and card.headline_en
    assert card.body_es and card.body_en
    assert card.correction_of is None
    events = bus.history("card.published")
    assert len(events) == 1 and events[0]["card_id"] == card.card_id
    assert bus.history("card.correction") == []


def test_editor_card_citation_outside_set_falls_back():
    bus = EventBus()
    evil = dict(_GOOD_DRAFT, citations=["pkg_1", "pkg_evil"])
    editor = Editor(CardConfig(llm=FakeLLM([evil])))
    card = asyncio.run(editor.emit_card(
        bus, claim_id="T2-en", claim_text="China is home to 1.41 billion.",
        verdict=_verdict_dict(), packages=[_pkg_row("pkg_1")]))
    assert card.fallback is True
    assert card.citations == ["pkg_1"]  # confined to the verified set
    assert "Respaldada" in card.headline_es
    assert "Supported" in card.headline_en
    assert len(bus.history("card.published")) == 1


def test_editor_card_falls_back_without_model():
    bus = EventBus()
    editor = Editor(CardConfig(llm=FailingLLM([])))
    card = asyncio.run(editor.emit_card(
        bus, claim_id="c-u", claim_text="Something vague.",
        verdict=_verdict_dict("unverifiable", ()),
        packages=[]))
    assert card.fallback is True
    assert card.citations == []
    assert "No verificable" in card.headline_es


def test_editor_correction_on_new_package():
    bus = EventBus()
    editor = Editor(CardConfig(llm=FakeLLM([dict(_GOOD_DRAFT),
                                            dict(_GOOD_DRAFT,
                                                 citations=["pkg_2"])])))
    first = asyncio.run(editor.emit_card(
        bus, claim_id="T2-en", claim_text="China is home to 1.41 billion.",
        verdict=_verdict_dict(ids=("pkg_1",)),
        packages=[_pkg_row("pkg_1")]))
    assert first.event_type == "card.published"
    second = asyncio.run(editor.emit_card(
        bus, claim_id="T2-en", claim_text="China is home to 1.41 billion.",
        verdict=_verdict_dict(ids=("pkg_2",)),
        packages=[_pkg_row("pkg_2")]))
    assert second.event_type == "card.correction"
    assert second.correction_of == first.card_id
    assert second.new_packages == ["pkg_2"]
    assert second.citations == ["pkg_2"]
    assert len(bus.history("card.correction")) == 1


def test_editor_repeat_without_new_package_is_not_a_correction():
    bus = EventBus()
    editor = Editor(CardConfig(llm=FakeLLM([dict(_GOOD_DRAFT),
                                            dict(_GOOD_DRAFT)])))
    kw = dict(claim_id="T2-en", claim_text="China is home to 1.41 billion.",
              verdict=_verdict_dict(), packages=[_pkg_row("pkg_1")])
    asyncio.run(editor.emit_card(bus, **kw))
    second = asyncio.run(editor.emit_card(bus, **kw))
    assert second.event_type == "card.published"
    assert second.new_packages == []
    assert bus.history("card.correction") == []


# ---------------------------------------------------------------------------
# bundle: real ZIP roundtrip (REAL verifier re-run, $0)
# ---------------------------------------------------------------------------

def _roundtrip_run(tmp_path):
    """Build a 3-claim run (supported/contradicted/unverifiable) in tmp."""
    snap_dir = str(tmp_path / "snap")
    snap_a = "America produced $29.3 trillion dollars of GDP in 2024."
    snap_b = "America produced $20.1 trillion dollars of GDP in 2018."
    _write_tmp_snapshot(snap_dir, snap_a)
    _write_tmp_snapshot(snap_dir, snap_b)
    pkg_a = _package_dict("pkg_a_1", "T3-en",
                          "https://example.org/gdp-2024", snap_a,
                          "$29.3 trillion dollars", source_year=2024)
    pkg_b = _package_dict("pkg_b_1", "F1x",
                          "https://example.org/gdp-2018", snap_b,
                          "$20.1 trillion dollars", source_year=2018)
    claim_a = ClaimInput(claim_id="T3-en", value=29.3e12, unit="USD",
                         raw="29.3 trillion dollars", claim_year=2024)
    claim_b = ClaimInput(claim_id="F1x", value=29.3e12, unit="USD",
                         raw="29.3 trillion dollars", claim_year=2024)
    claim_c = ClaimInput(claim_id="U1-en", value=1.0, unit="USD",
                         raw="1 dollar")
    log_path = str(tmp_path / "run.jsonl")
    bus = EventBus(run_log=log_path)
    loader = _tmp_loader(snap_dir)

    async def _go():
        va = await verify_and_emit(claim_a, [EvidencePackage.from_dict(pkg_a)],
                                   loader, bus)
        vb = await verify_and_emit(claim_b, [EvidencePackage.from_dict(pkg_b)],
                                   loader, bus)
        vc = await verify_and_emit(claim_c, [], loader, bus)
        await bus.close()
        return va, vb, vc

    va, vb, vc = asyncio.run(_go())
    assert (va.verdict, vb.verdict, vc.verdict) == (
        "supported", "contradicted", "unverifiable"), (va, vb, vc)
    claims = [
        {"claim_id": "T3-en", "text": "America produced 29.3T in 2024.",
         "value": 29.3e12, "unit": "USD", "raw": "29.3 trillion dollars",
         "claim_year": 2024, "verdict": va.verdict,
         "reasons": va.reasons},
        {"claim_id": "F1x", "text": "America produced 29.3T (2018 src).",
         "value": 29.3e12, "unit": "USD", "raw": "29.3 trillion dollars",
         "claim_year": 2024, "verdict": vb.verdict,
         "reasons": vb.reasons},
        {"claim_id": "U1-en", "text": "Something vague.",
         "value": 1.0, "unit": "USD", "raw": "1 dollar",
         "claim_year": None, "verdict": vc.verdict,
         "reasons": vc.reasons},
    ]
    return log_path, snap_dir, [pkg_a, pkg_b], claims


def test_bundle_roundtrip_passes_live_engine(tmp_path):
    log_path, snap_dir, pkgs, claims = _roundtrip_run(tmp_path)
    out = str(tmp_path / "bundle.zip")
    manifest = export_bundle(run_log=log_path, packages=pkgs, claims=claims,
                             out_path=out, snapshot_dir=snap_dir,
                             truth_ref=os.path.join(
                                 REPO_ROOT, "live", "material", "truth.json"))
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert {"run.jsonl", "packages.json", "claims.json", "truth_ref.json",
            "verify_bundle.py", "README.txt", "manifest.json"} <= names
    assert sum(1 for n in names if n.startswith("snapshots/")) == 2
    # manifest hashes match the ZIP bytes.
    with zipfile.ZipFile(out) as zf:
        for arcname, want in manifest["files"].items():
            assert hashlib.sha256(zf.read(arcname)).hexdigest() == want
    # CLI re-execution with the REAL verifier (offline: no net, no model).
    proc = subprocess.run(
        [sys.executable,
         os.path.join(REPO_ROOT, "live", "bundle", "verify_bundle.py"),
         out],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
    print(proc.stdout)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "engine=live.verifier" in proc.stdout
    for cid, label in (("T3-en", "supported"),
                       ("F1x", "contradicted"),
                       ("U1-en", "unverifiable")):
        assert f"{cid}: expected={label} got={label}" in proc.stdout
    assert "ALL PASS (3/3 claims)" in proc.stdout


def test_bundle_stdlib_mirror_agrees_with_live(tmp_path):
    log_path, snap_dir, pkgs, claims = _roundtrip_run(tmp_path)
    out = str(tmp_path / "bundle.zip")
    export_bundle(run_log=log_path, packages=pkgs, claims=claims,
                  out_path=out, snapshot_dir=snap_dir)
    live_report = verify_bundle(out, engine="live")
    mirror_report = verify_bundle(out, engine="stdlib")
    assert live_report["ok"] is True
    assert mirror_report["ok"] is True
    assert [c["got"] for c in mirror_report["claims"]] == [
        c["got"] for c in live_report["claims"]]
    assert "stdlib-mirror" in mirror_report["engine"]


def test_bundle_detects_tampered_snapshot(tmp_path):
    log_path, snap_dir, pkgs, claims = _roundtrip_run(tmp_path)
    out = str(tmp_path / "bundle.zip")
    export_bundle(run_log=log_path, packages=pkgs, claims=claims,
                  out_path=out, snapshot_dir=snap_dir)
    tampered = tmp_path / "tampered"
    with zipfile.ZipFile(out) as zf:
        zf.extractall(tampered)
    snaps = list((tampered / "snapshots").glob("*"))
    assert snaps
    with open(snaps[0], "ab") as fh:
        fh.write(b"tampered")
    report = verify_bundle(tampered)
    assert report["ok"] is False
    assert report["files"]["bad"]


def test_export_rejects_hash_mismatch(tmp_path):
    log_path, snap_dir, pkgs, claims = _roundtrip_run(tmp_path)
    # snapshot_path resolves (repo file) but its bytes are not the package sha.
    bad = dict(pkgs[0], snapshot_path="live/material/truth.json")
    with pytest.raises(ValueError, match="hash mismatch"):
        export_bundle(run_log=log_path, packages=[bad], claims=claims,
                      out_path=str(tmp_path / "bad.zip"),
                      snapshot_dir=snap_dir)


def test_export_rejects_invalid_package(tmp_path):
    log_path, snap_dir, pkgs, claims = _roundtrip_run(tmp_path)
    bad = dict(pkgs[0], quote="   ")
    with pytest.raises(ValueError):
        export_bundle(run_log=log_path, packages=[bad], claims=claims,
                      out_path=str(tmp_path / "bad.zip"),
                      snapshot_dir=snap_dir)


# ---------------------------------------------------------------------------
# LIVE: 1 real attack + 1 real card on TRUE keynote claim T2-en (gated)
# ---------------------------------------------------------------------------

LIVE_BUDGET_USD = 1.0
LIVE_CLAIM_ID = "T7-en"
# NOTE (M5 finding): the brief prefers reusing a real WB snapshot from
# data/snap, but WB API quotes are bare JSON numbers with no unit word,
# so M2a reports them unit_mismatch/unusable and no supported verdict is
# reachable from them. The live proof therefore takes the brief's
# alternative (fetch 1 URL): a robots-allowed article page whose prose
# carries a contiguous unit-bearing figure.
LIVE_EVIDENCE_URL = "https://en.wikipedia.org/wiki/Economy_of_Japan"
LIVE_QUOTE_START = "From 1995 to 2023"
LIVE_NEEDLE = "$4.2 trillion in nominal terms"


@pytest.mark.live
def test_live_attack_and_card_on_true_claim(tmp_path):
    """LIVE: real skeptic attack + real editor card on T7-en (<= $1)."""
    if os.environ.get("ATLAS_LIVE") != "1":
        pytest.skip("live test: set ATLAS_LIVE=1 to run")
    import time as _time

    from src.atlas.models.meta import MetaClient

    from live.swarm.fetch import fetch_url

    llm = MetaClient()
    if not llm.available:
        pytest.skip("live test: MODEL_API_KEY not configured")
    with open(os.path.join(REPO_ROOT, "live", "material", "truth.json"),
              encoding="utf-8") as fh:
        truth = {row["id"]: row for row in json.load(fh)}
    claim_text = truth[LIVE_CLAIM_ID]["text"]
    assert truth[LIVE_CLAIM_ID]["expected"] == "true"

    # Fetch 1 URL: robots-allowed article prose with a contiguous
    # unit-bearing figure ("$4.2 trillion", 0.24% off the T7 keynote
    # figure of 4.19T). source_year stays None: the quote spans a
    # 1995-2023 range, and M2a skips temporal alignment when the source
    # year is unknown (contract: "when both are known").
    _time.sleep(8)  # polite pacing (Wikipedia rate-limits bursts)
    try:
        fetched = fetch_url(LIVE_EVIDENCE_URL)
    except Exception:
        _time.sleep(15)
        fetched = fetch_url(LIVE_EVIDENCE_URL)  # fail loudly on retry
    print(f"\n[LIVE m5] evidence snapshot: {fetched.snapshot_path} "
          f"({fetched.size} bytes)")
    body = fetched.text or ""
    sent_start = body.find(LIVE_QUOTE_START)
    assert sent_start >= 0, "article no longer carries the T7 sentence"
    sent_end = body.find(LIVE_NEEDLE, sent_start)
    assert sent_end >= 0, "article no longer carries the T7 figure"
    sent_end += len(LIVE_NEEDLE)
    quote = body[sent_start:sent_end].strip()
    assert len(quote) <= 600
    q_start = body.find(quote)
    try:
        rel_snap = str(os.path.relpath(fetched.snapshot_path, REPO_ROOT))
    except ValueError:
        rel_snap = f"data/snap/{fetched.sha256}.html"
    wiki_pkg = {
        "package_id": "m5live-wiki-t7", "claim_id": LIVE_CLAIM_ID,
        "url": fetched.final_url or fetched.url,
        "captured_at": fetched.captured_at, "sha256": fetched.sha256,
        "snapshot_path": rel_snap, "quote": quote,
        "offsets": [q_start, q_start + len(quote)],
        "source_type": source_type_for_url(fetched.final_url or fetched.url),
        "numbers": numbers_from_quote(quote),
        "extractor": "code", "source_year": None,
    }
    EvidencePackage.from_dict(wiki_pkg)

    def _live_loader(pkg):
        cand = os.path.join(REPO_ROOT, pkg.snapshot_path)
        if os.path.isfile(cand):
            with open(cand, encoding="utf-8") as fh:
                return fh.read()
        return None

    claim = ClaimInput(claim_id=LIVE_CLAIM_ID, text=claim_text,
                       value=4.19e12, unit="USD",
                       raw="4.19 trillion dollars", claim_year=2024)
    pkgs = [EvidencePackage.from_dict(wiki_pkg)]
    verdict = verify_claim(claim, pkgs, _live_loader)
    print(f"[LIVE m5] verdict: {verdict.verdict} reasons={verdict.reasons} "
          f"deciding={verdict.package_ids}")
    assert verdict.verdict == "supported", verdict.details
    assert verdict.package_ids == ["m5live-wiki-t7"]

    async def _go():
        bus = EventBus(run_log=str(tmp_path / "run.jsonl"))
        tracker = ClaimTracker(bus)
        await tracker.mark_heard(LIVE_CLAIM_ID, text=claim_text,
                                 actor="m5-live")
        await tracker.set_state(LIVE_CLAIM_ID, "investigando",
                                actor="m5-live")
        emitted = await verify_and_emit(claim, pkgs, _live_loader, bus)
        await tracker.set_state(LIVE_CLAIM_ID, "veredicto",
                                actor="m5-live")
        _time.sleep(5)  # pacing before the attack's search+fetch
        attack = await run_attack(
            bus, claim, emitted, [wiki_pkg], _live_loader,
            AttackConfig(llm=llm.fork(), max_counter_queries=1,
                         fetch_top_k=2))
        card = await Editor(CardConfig(llm=llm.fork())).emit_card(
            bus, claim_id=LIVE_CLAIM_ID, claim_text=claim_text,
            verdict=emitted, packages=[wiki_pkg])
        await bus.close()
        return bus, emitted, attack, card

    bus, emitted, attack, card = asyncio.run(_go())
    total_usd = attack.cost_usd + card.cost_usd
    print(f"[LIVE m5] attack: challenged={attack.challenged} "
          f"note={attack.note!r} queries={attack.queries} "
          f"cost=${attack.cost_usd:.4f} usage={attack.usage}")
    print(f"[LIVE m5] card: fallback={card.fallback} "
          f"citations={card.citations} cost=${card.cost_usd:.4f} "
          f"usage={card.usage}")
    print(f"[LIVE m5] TOTAL measured cost: ${total_usd:.4f}")

    # Attack published exactly one challenge-or-note; a challenge must be
    # structurally valid with a greppable quote.
    challenges = bus.history("skeptic.challenge")
    notes = bus.history("skeptic.note")
    assert len(challenges) + len(notes) == 1
    if challenges:
        EvidencePackage.from_dict(challenges[0]["package"])
        cpkg = challenges[0]["package"]
        cpath = os.path.join(REPO_ROOT, cpkg["snapshot_path"])
        with open(cpath, encoding="utf-8") as fh:
            assert cpkg["quote"] in fh.read()
    # Card cites ONLY deciding (verified) package ids, in both languages.
    assert card.event_type == "card.published"
    assert set(card.citations) <= set(emitted.package_ids)
    assert card.headline_es and card.headline_en
    assert card.body_es and card.body_en
    assert len(bus.history("card.published")) == 1
    assert total_usd <= LIVE_BUDGET_USD, f"live cost ${total_usd:.4f}"
