"""Unit tests: Evidence Atlas MCP tools (Pilar F). Offline, stubbed pipeline."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

mcp = pytest.importorskip("mcp")

ROOT = Path(__file__).resolve().parents[2]


def _load_server():
    sys.path.insert(0, str(ROOT / "src"))
    spec = importlib.util.spec_from_file_location(
        "atlas_mcp_server", ROOT / "mcp_server" / "server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


srv = _load_server()


def _stub_report(**kw):
    base = {
        "report_id": "rep_test123",
        "conclusion": SimpleNamespace(
            stance="supported", summary="Matches World Bank data."
        ),
    }
    base.update(kw)
    return SimpleNamespace(**base)


# -- verify_claim ----------------------------------------------------

def test_verify_claim_maps_pipeline_result():
    out = srv.do_verify_claim("GDP was $27T.", analyze_fn=lambda *a, **k: _stub_report())
    assert out == {
        "stance": "supported",
        "summary": "Matches World Bank data.",
        "receipt_id": "rep_test123",
    }


def test_verify_claim_rejects_empty_text():
    assert srv.do_verify_claim("   ")["error"] == "empty_text"


def test_verify_claim_surfaces_pipeline_errors():
    def boom(*a, **k):
        raise RuntimeError("kaput")

    out = srv.do_verify_claim("GDP was $27T.", analyze_fn=boom)
    assert out["error"] == "analysis_failed"
    assert "kaput" in out["message"]


def test_verify_claim_missing_conclusion_degrades():
    out = srv.do_verify_claim(
        "GDP was $27T.",
        analyze_fn=lambda *a, **k: SimpleNamespace(
            report_id="rep_x", conclusion=None
        ),
    )
    assert out["stance"] == "unverifiable"
    assert out["receipt_id"] == "rep_x"


# -- verify_document -------------------------------------------------

def test_verify_document_returns_rollup_and_reports():
    batch = {
        "rollup": {"supported": 1, "contradicted": 1},
        "reports": [{"conclusion": {"stance": "supported"}}, {"error": "boom"}],
        "claim_count": 2,
        "errors": 1,
    }
    out = srv.do_verify_document("Two. Sentences.", check_fn=lambda *a, **k: batch)
    assert out["rollup"] == {"supported": 1, "contradicted": 1}
    assert len(out["reports"]) == 2
    assert out["claim_count"] == 2
    assert out["errors"] == 1


def test_verify_document_rejects_empty_text():
    assert srv.do_verify_document("")["error"] == "empty_text"


def test_verify_document_surfaces_pipeline_errors():
    def boom(*a, **k):
        raise TimeoutError("slow")

    out = srv.do_verify_document("Some text.", check_fn=boom)
    assert out["error"] == "analysis_failed"


# -- get_receipt -----------------------------------------------------

def test_get_receipt_dumps_report():
    dumped = {"report_id": "rep_1", "conclusion": {"stance": "supported"}}

    class FakeReport:
        def model_dump(self, mode=None):
            assert mode == "json"
            return dumped

    out = srv.do_get_receipt("rep_1", get_fn=lambda rid: FakeReport())
    assert out == dumped


def test_get_receipt_missing_is_error():
    out = srv.do_get_receipt("rep_nope", get_fn=lambda rid: None)
    assert out["error"] == "receipt_not_found"


def test_get_receipt_rejects_empty_id():
    assert srv.do_get_receipt("  ")["error"] == "empty_id"


def test_get_receipt_accepts_plain_dict():
    out = srv.do_get_receipt("rep_1", get_fn=lambda rid: {"report_id": "rep_1"})
    assert out["report_id"] == "rep_1"


# -- tool registration + schemas -------------------------------------

EXPECTED = {
    "verify_claim": {"text"},
    "verify_document": {"text"},
    "get_receipt": {"receipt_id"},
}


def test_all_three_tools_registered_with_schema():
    tools = asyncio.run(srv.mcp.list_tools())
    by_name = {t.name: t for t in tools}
    assert set(by_name) == set(EXPECTED), [t.name for t in tools]
    for name, required in EXPECTED.items():
        schema = by_name[name].inputSchema
        assert set(schema.get("required", [])) == required, name
        for param in required:
            assert param in schema["properties"], (name, param)
            assert schema["properties"][param].get("type") == "string"


def test_tool_descriptions_present():
    tools = asyncio.run(srv.mcp.list_tools())
    for tool in tools:
        assert tool.description, tool.name
