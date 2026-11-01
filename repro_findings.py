"""War-room reproduction of the v0.4.0 review findings (defects a-o).

Run:  PYTHONPATH=src python repro_findings.py
Exit 0 iff every finding is FIXED. Every case below must also exist as a
regression test in tests/ before its fix UT closes. Offline only: no
network, no model calls (doubles + stubs throughout).

Scale conventions asserted here (documented, not invented):
- EN short scale: trillion=1e12, billion=1e9, million=1e6.
- ES long scale: billon/billones=1e12, millardo(s)=1e9,
  millon/millones=1e6, mil=1e3, "mil millones"=1e9.
- ES thousand separators: "1.400"=1400, "1.400.000"=1400000
  (a dot group of exactly 3 digits). Ambiguous "1.40" stays decimal.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

FAILURES: list[str] = []
PASSES = 0


def check(fid: str, name: str, ok: bool, detail: str = "") -> None:
    global PASSES
    if ok:
        PASSES += 1
        print(f"  ok   [{fid}] {name}")
    else:
        FAILURES.append(fid)
        print(f"  FAIL [{fid}] {name} :: {detail}")


def section(title: str) -> None:
    print(f"== {title}")


# ---------------------------------------------------------------- a) units
def repro_a() -> None:
    from atlas.core.units import parse_quantities

    section("a) unit/scale parsing (false contradicted)")
    q = parse_quantities("$27.72T")
    check("a1", '"$27.72T" parses as 27.72e12 USD',
          len(q) == 1 and q[0].value == 27.72e12 and q[0].normalized_unit == "USD",
          f"got {[(x.value, x.normalized_unit) for x in q]}")
    q = parse_quantities("1.400 millones de dolares")
    check("a2", '"1.400 millones" parses as 1.4e9',
          len(q) == 1 and q[0].value == 1.4e9, f"got {[x.value for x in q]}")
    q = parse_quantities("1.400.000 habitantes")
    check("a3", '"1.400.000 habitantes" parses once as 1400000 count',
          len(q) == 1 and q[0].value == 1400000.0 and q[0].normalized_unit == "count",
          f"got {[(x.value, x.normalized_unit) for x in q]}")
    q = parse_quantities("5 mil millones")
    check("a4", '"5 mil millones" parses as 5e9',
          len(q) == 1 and q[0].value == 5e9, f"got {[x.value for x in q]}")
    # Adjacent regressions that must keep working.
    q = parse_quantities("$27.36 trillion")
    check("a5", '"$27.36 trillion" still 27.36e12 USD',
          len(q) == 1 and q[0].value == 27.36e12, f"got {[x.value for x in q]}")
    q = parse_quantities("5 t")
    check("a6", '"5 t" still reads as 5 (tonnes, never trillion)',
          len(q) == 1 and q[0].value == 5.0, f"got {[x.value for x in q]}")
    q = parse_quantities("3,1%")
    check("a7", '"3,1%" still reads as 3.1 percent',
          len(q) == 1 and q[0].value == 3.1, f"got {[x.value for x in q]}")


# ------------------------------------------------- b) country matching
def repro_b() -> None:
    from atlas.connectors.countries import resolve_country

    section("b) country matching with word boundaries")
    cases = [
        ("Indiana", None), ("I visited Latin America last year", None),
        ("Northern Ireland", "GBR"), ("turkey", None), ("turkeys", None),
        ("otherworldly", None), ("inmundo", None),
    ]
    for text, want in cases:
        _, iso = resolve_country(text)
        check(f"b-{text[:12]}", f"{text!r} -> {want}",
              iso == want, f"got {iso!r}")
    _, iso = resolve_country("North Korea")
    check("b-nk", '"North Korea" -> PRK (not KOR)', iso == "PRK", f"got {iso!r}")
    _, iso = resolve_country("Turkey's GDP grew 5%")
    check("b-tr", '"Turkey\'s GDP" (context) -> TUR', iso == "TUR", f"got {iso!r}")
    _, iso = resolve_country("Turkey is delicious")
    check("b-bird", '"Turkey is delicious" (bird) -> None', iso is None, f"got {iso!r}")
    for text, want in [("United States", "USA"), ("US", "USA"),
                       ("Mexico", "MEX"), ("world", "WLD"), ("mundo", "WLD"),
                       ("South Korea", "KOR"), ("Ireland", "IRL")]:
        _, iso = resolve_country(text)
        check(f"b-ok-{want}", f"{text!r} still -> {want}",
              iso == want, f"got {iso!r}")


# --------------------------------------- c) indicator rate-vs-level
def repro_c() -> None:
    from atlas.connectors.worldbank import is_change_magnitude, match_indicator

    section("c) indicator semantics: rate-vs-level abstention")
    for text in ["US exports rose 5%", "exports grew 3 percent",
                 "las exportaciones crecieron un 5%"]:
        c = _mkclaim(text, [("5%", 5.0, "percent")], [2023])
        s, cmps, uncs = _stance(c, [_mkev(2023, 12.0, "percent",
                                          "Exports (% of GDP)")])
        codes = [u.code for u in uncs]
        check(f"c-{text[:14]}", f"{text!r} never contradicts a level",
              s != "contradicted" and "rate_level_mismatch" in codes,
              f"got {s} {codes}")
    m = match_indicator("US GDP grew 5%")
    check("c-gdp", '"US GDP grew 5%" routes to NY.GDP.MKTP.KD.ZG',
          m is not None and m[0] == "NY.GDP.MKTP.KD.ZG", f"got {m}")
    check("c-ism", 'is_change_magnitude("rose to 5%") is False',
          is_change_magnitude("rose to 5%") is False, "got True")


# ------------------------------------------------- verify-level harness
def _mkclaim(text, quants, years, domain="economy"):
    from atlas.core.types import Quantity, StructuredClaim, TimeRef

    qs = [Quantity(label="q", raw_text=r, value=v, unit=u,
                   normalized_value=v, normalized_unit=u)
          for (r, v, u) in quants]
    ts = [TimeRef(raw=str(y), year=y, period_type="year") for y in years]
    return StructuredClaim(raw_text=text, domain=domain, quantities=qs,
                           time_refs=ts)


def _mkev(year, value, unit="USD", label="GDP (current US$)"):
    from atlas.core.types import Evidence

    return Evidence(claim_id="c", connector="worldbank",
                    source_name="WB (repro stub)", source_kind="secondary",
                    evidence_kind="direct", locator=f"X USA {year}",
                    excerpt=f"{label} {year}: {value}",
                    payload={"label": label, "unit": unit, "year": str(year),
                             "value": value})


def _stance(claim, evidences):
    from atlas.pipeline.synthesize import decide_stance
    from atlas.pipeline.verify import verify_quantitative

    cmps, uncs = verify_quantitative(claim, evidences)
    return decide_stance(cmps, uncs, claim), cmps, uncs


# ------------------------------------------------- d) multi-year pairing
def repro_d() -> None:
    section("d) multi-year claims pair quantities with years (order-free)")
    q = [("27 trillion", 27e12, "USD"), ("28 trillion", 28e12, "USD")]
    evs = [_mkev(2020, 27e12), _mkev(2023, 28e12)]
    s1, _, _ = _stance(_mkclaim("t", q, [2020, 2023]), evs)
    s2, _, _ = _stance(_mkclaim("t", q, [2023, 2020]), evs)
    check("d1", "verdict independent of time_ref order",
          s1 == s2 == "supported", f"got {s1} vs {s2}")
    # Distant years must survive retrieval truncation (stub the fetch).
    import atlas.connectors.worldbank as wb

    rows = [{"date": str(y), "value": 1.0 * y,
             "country": {"value": "United States"}} for y in range(2015, 2025)]
    orig = wb.fetch_observations
    wb.fetch_observations = lambda *a, **k: [dict(r) for r in rows]
    try:
        got = wb.WorldBankConnector().retrieve(
            _mkclaim("GDP of the United States was X in 2015 and Y in 2024.",
                     [("x", 1.0, "USD")], [2015, 2024]), limit=5)
    finally:
        wb.fetch_observations = orig
    years = sorted(e.payload["year"] for e in got)
    check("d2", "distant years survive retrieval (2015 and 2024 present)",
          "2015" in years and "2024" in years, f"got {years}")


# ------------------------------------------------- e) scale-error guard
def repro_e() -> None:
    section("e) possible_scale_error guard (10^k backstop)")
    c = _mkclaim("t", [("27.72", 27.72, "USD")], [2023])
    s, _, uncs = _stance(c, [_mkev(2023, 27.72e12)])
    codes = [u.code for u in uncs]
    check("e1", "1000x gap with scale-less parse abstains, not contradicted",
          s != "contradicted" and "possible_scale_error" in codes,
          f"got {s} {codes}")
    c = _mkclaim("t", [("1.25 billion", 1.25e9, "USD")], [2023])
    s, _, _ = _stance(c, [_mkev(2023, 1.25e12)])
    check("e2", "1000x gap with EXPLICIT scale stays contradicted",
          s == "contradicted", f"got {s}")


# --------------------------------------- f) present-tense vs latest
def repro_f() -> None:
    from atlas.core.types import StructuredClaim

    section("f) present-tense claims compare vs labeled latest (capped)")
    c = _mkclaim("population is 125 million", [("125 million", 125e6, "count")], [])
    c.time_refs = []
    s, _, uncs = _stance(c, [_mkev(2023, 125e6, "count", "Population, total")])
    codes = [u.code for u in uncs]
    blob = " ".join(u.description for u in uncs)
    check("f1", "present-tense + matching latest caps at partially_supported",
          s == "partially_supported", f"got {s}")
    check("f2", "assumed period disclosed via assumed_period code + year",
          "assumed_period" in codes and "2023" in blob,
          f"got {codes} {blob[:160]}")


# ------------------------------------------------- g) split anaphora
def repro_g() -> None:
    section("g) split carries resolved context (span untouched)")
    try:
        from atlas.pipeline.split import split_with_context
    except ImportError as exc:
        check("g1", "split_with_context API exists", False, str(exc))
        return
    spans, ctxs, _, _ = split_with_context(
        "France's population is 68 million. Its GDP is $3 trillion.", llm=None)
    check("g1", "two spans", len(spans) == 2, f"got {spans}")
    second = ctxs[1] if len(ctxs) == 2 else {}
    check("g2", "second span keeps resolved country (France/FRA)",
          second.get("country_iso3") == "FRA" or "france" in
          str(second.get("country", "")).lower(), f"got {second}")
    check("g3", "span text itself is verbatim (no rewrite)",
          spans[1] == "Its GDP is $3 trillion.", f"got {spans[1]!r}")


# ------------------------------------------------- h) ES opinion lexicon
def repro_h() -> None:
    from atlas.pipeline.verify import _looks_opinionated

    section("h) Spanish opinion detection")
    for text in ["Creo que la economia va bien", "el mejor pais del mundo",
                 "deberian bajar los impuestos ya"]:
        check(f"h-{text[:12]}", f"{text!r} is opinionated",
              _looks_opinionated(text, 0) is True, "got False")
    check("h-ok", '"best available data" still not opinionated',
          _looks_opinionated("best available data", 0) is False, "got True")


# ------------------------------------------------- i) indicator coverage
def repro_i() -> None:
    from atlas.connectors.worldbank import match_indicator

    section("i) indicator coverage (levels in USD, pop growth)")
    m = match_indicator("exports in current USD")
    check("i1", '"exports in current USD" -> NE.EXP.GNFS.CD',
          m is not None and m[0] == "NE.EXP.GNFS.CD", f"got {m}")
    m = match_indicator("population growth was 1 percent")
    check("i2", '"population growth" -> SP.POP.GROW',
          m is not None and m[0] == "SP.POP.GROW", f"got {m}")


# ------------------------------------------------- j) public-deploy guards
def repro_j() -> None:
    section("j) public-deploy guards (auth, persist default)")

    import atlas.api.app as appmod
    from atlas.api.app import AnalyzeRequest

    check("j1", "AnalyzeRequest.persist defaults to False",
          AnalyzeRequest.model_fields["persist"].default is False,
          f"got {AnalyzeRequest.model_fields['persist'].default!r}")
    check("j2", "API-key auth dependency exists",
          hasattr(appmod, "require_api_key"), "missing require_api_key")


# ------------------------------------------------- k) OpenAlex budget
def repro_k() -> None:
    import os

    import atlas.connectors.openalex as ox

    section("k) OpenAlex key + real mailto")
    check("k1", "MAILTO is not the example.org placeholder",
          getattr(ox, "MAILTO", "") != "evidence-atlas@example.org",
          f"got {getattr(ox, 'MAILTO', None)!r}")
    has_key = bool(os.environ.get("OPENALEX_API_KEY", "").strip())
    print(f"  info [k2] OPENALEX_API_KEY present in env: {has_key} "
          f"(human action, not a code gate)")


# ------------------------------------------------- l) batch streaming
def repro_l() -> None:
    import inspect

    import atlas.api.app as appmod
    from atlas.pipeline.run import check_document

    section("l) batch progress + global deadline")
    paths = [getattr(r, "path", "") for r in appmod.app.routes]
    check("l1", "SSE stream route for batch exists",
          any("stream" in p or "events" in p for p in paths), f"got {paths}")
    check("l2", "check_document accepts a deadline",
          "deadline_s" in inspect.signature(check_document).parameters,
          "no deadline_s param")


# ------------------------------------------------- m) client fork
def repro_m() -> None:
    section("m) per-request client fork (usage attribution)")

    from atlas.models.meta import MetaClient

    check("m1", "MetaClient.fork exists", hasattr(MetaClient, "fork"),
          "no fork method")
    if not hasattr(MetaClient, "fork"):
        check("m2", "forks attribute usage independently", False,
              "no fork method")
        return
    from types import SimpleNamespace

    from atlas.pipeline.run import _take_usage, _usage_seq

    base = MetaClient.__new__(MetaClient)
    base.last_usage_seq, base.last_usage = 0, None
    fa, fb = base.fork(), base.fork()
    seq_a = _usage_seq(fa)
    fb.last_usage_seq, fb.last_usage = 1, {"total_tokens": 50}
    got = _take_usage(fa, seq_a)
    check("m2", "sibling request usage never leaks across forks",
          got is None, f"got {got}")


# ------------------------------------------------- n) sidecar integration
def repro_n() -> None:
    from pathlib import Path

    import atlas.api.app as appmod

    section("n) CORS policy + reference client")
    mw = [m.cls.__name__ for m in appmod.app.user_middleware]
    check("n1", "CORS middleware configured", "CORSMiddleware" in mw,
          f"got {mw}")
    check("n2", "reference Python client shipped",
          Path("clients/python/shipcheck.py").exists(), "missing file")


# ------------------------------------------------- o) reasoning effort
class _Rec:
    name = "recording-double"
    available = True

    def __init__(self):
        self.kwargs = None

    def complete_json(self, system, user, **kw):
        self.kwargs = kw
        raise RuntimeError("stop-after-record")


def repro_o() -> None:
    section("o) reasoning_effort on structure + synthesize")

    from atlas.pipeline.extract import structure_claim
    from atlas.pipeline.synthesize import synthesize

    rec = _Rec()
    structure_claim("GDP was $5 in 2023.", llm=rec)
    check("o1", "structure_claim sends reasoning_effort",
          bool(rec.kwargs and rec.kwargs.get("reasoning_effort")),
          f"got {rec.kwargs}")
    rec2 = _Rec()
    c = _mkclaim("t", [("5", 5.0, "USD")], [2023])
    s, cmps, uncs = _stance(c, [_mkev(2023, 5.0)])
    try:
        synthesize(c, cmps, uncs, llm=rec2)
    except RuntimeError:
        pass
    check("o2", "synthesize sends reasoning_effort",
          bool(rec2.kwargs and rec2.kwargs.get("reasoning_effort")),
          f"got {rec2.kwargs}")


def main() -> int:
    repro_a(); repro_b(); repro_c(); repro_d(); repro_e(); repro_f()
    repro_g(); repro_h(); repro_i(); repro_j(); repro_k(); repro_l()
    repro_m(); repro_n(); repro_o()
    n_fail = len(FAILURES)
    print(f"\nrepro: {PASSES} passed, {n_fail} failed "
          f"({', '.join(sorted(set(FAILURES))) or 'none'})")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
