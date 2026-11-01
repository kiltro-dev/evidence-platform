"""PDF ingest tests: stdlib text-per-page, model/heuristic drafting,
anchor validation. Offline: generated PDF bytes + doubles only."""

import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.ingest import PROVENANCE_MISSING, validate_anchors  # noqa: E402
from atlas.ingest.pdf import extract_pages, structure_pdf_claims  # noqa: E402


def _build_pdf(lines_per_stream, flate=False):
    """Minimal multi-object PDF; one content stream per page."""
    import struct as _s  # noqa: F401 (kept local: builder only)
    streams = []
    for lines in lines_per_stream:
        content = b"BT /F1 18 Tf 72 720 Td 22 TL\n"
        for i, line in enumerate(lines):
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content += f"({esc}) Tj\n".encode("latin-1")
            if i < len(lines) - 1:
                content += b"T*\n"
        content += b"ET"
        streams.append(content)
    n = len(streams)
    # objs: 1 catalog, 2 pages, 3..2+n page objs, then font, then streams
    font_id = 3 + n
    stream_ids = [font_id + 1 + i for i in range(n)]
    kids = " ".join(f"{3 + i} 0 R" for i in range(n))
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
    }
    for i in range(n):
        objs[3 + i] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {stream_ids[i]} 0 R >>"
        ).encode()
    objs[font_id] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for sid, content in zip(stream_ids, streams):
        payload = zlib.compress(content) if flate else content
        filt = b"/Filter /FlateDecode " if flate else b""
        objs[sid] = (b"<< /Length %d %s>>\nstream\n" % (len(payload), filt)
                     + payload + b"\nendstream")
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for i in sorted(objs):
        offsets[i] = len(pdf)
        pdf += b"%d 0 obj\n" % i + objs[i] + b"\nendobj\n"
    xref_at = len(pdf)
    pdf += b"xref\n0 %d\n" % (max(objs) + 1)
    pdf += b"0000000000 65535 f \n"
    for i in range(1, max(objs) + 1):
        pdf += b"%010d 00000 n \n" % offsets.get(i, 0)
    pdf += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (max(objs) + 1, xref_at))
    return bytes(pdf)


class _ScriptedLLM:
    name = "double"
    available = True

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def complete_json(self, system, user, **kw):
        self.calls.append({"system": system, "user": user, **kw})
        return self.payload


class _FailingLLM:
    name = "double-fail"
    available = True

    def complete_json(self, *a, **k):
        raise RuntimeError("boom")


def test_extract_pages_single_page_fixture():
    pdf = _build_pdf([["The Atlas probe facility produced 4,200 units in 2024."]])
    pages, notes = extract_pages(pdf)
    assert len(pages) == 1 and pages[0].page == 1
    assert "4,200 units" in pages[0].text
    assert notes == []


def test_extract_pages_multi_page_mapping():
    pdf = _build_pdf([["Alpha output was 10 units."], ["Beta output was 20 units."]])
    pages, _ = extract_pages(pdf)
    assert [p.page for p in pages] == [1, 2]
    assert "Alpha" in pages[0].text and "Beta" not in pages[0].text
    assert "Beta" in pages[1].text and "Alpha" not in pages[1].text


def test_extract_pages_flate_decode():
    pdf = _build_pdf([["Compressed line states 7 gadgets."]], flate=True)
    pages, _ = extract_pages(pdf)
    assert "7 gadgets" in pages[0].text


def test_extract_pages_rejects_non_pdf():
    import pytest

    from atlas.ingest import IngestError
    with pytest.raises(IngestError):
        extract_pages(b"definitely not a pdf")


def test_model_path_keeps_verbatim_spans_only():
    pdf = _build_pdf([["Output reached 4,200 units in 2024."]])
    llm = _ScriptedLLM({"claims": [
        {"text": "Output reached 4,200 units in 2024.",
         "span": "4,200 units"},
        {"text": "Output doubled overnight.", "span": "doubled"},  # invented
    ]})
    result = structure_pdf_claims(pdf, "doc.pdf", llm)
    assert len(result.claims) == 1
    assert result.claims[0].anchor == {"page": 1, "span": "4,200 units"}
    assert result.claims[0].actor == "model:double"
    assert result.provenance_missing_count == 1
    assert llm.calls[0]["json_schema"]["name"] == "pdf_page_claims"


def test_model_claim_without_anchor_discarded():
    pdf = _build_pdf([["Output reached 4,200 units."]])
    llm = _ScriptedLLM({"claims": [
        {"text": "Something entirely elsewhere.", "span": "elsewhere"},
    ]})
    result = structure_pdf_claims(pdf, "doc.pdf", llm)
    assert result.claims == []
    assert len(result.discarded) == 1
    assert result.discarded[0].code == PROVENANCE_MISSING


def test_heuristic_fallback_no_llm():
    pdf = _build_pdf([["First line has 3 widgets. Second line has 5 widgets."]])
    result = structure_pdf_claims(pdf, "doc.pdf", llm=None)
    assert len(result.claims) == 2
    assert all(c.anchor["page"] == 1 and c.anchor["span"] == c.text
               for c in result.claims)
    assert result.discarded == []


def test_heuristic_fallback_on_model_failure():
    pdf = _build_pdf([["Fallback line has 9 widgets."]])
    result = structure_pdf_claims(pdf, "doc.pdf", _FailingLLM())
    assert len(result.claims) == 1
    assert any("heuristic fallback" in u for u in result.uncertainties)


def test_committed_fixture_parses():
    fixture = (Path(__file__).resolve().parents[2]
               / "eval" / "fixtures" / "ingest_doc.pdf")
    pages, notes = extract_pages(fixture.read_bytes())
    assert len(pages) == 1
    assert "4,200 units" in pages[0].text
    assert "1.8 percent" in pages[0].text
    result = structure_pdf_claims(fixture.read_bytes(), fixture.name, llm=None)
    assert len(result.claims) == 2
    assert validate_anchors(result.claims).discarded == []
