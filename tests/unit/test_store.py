"""Store contract tests: round-trip fidelity + read-only reads (offline).

Asserts the ACTUAL store.py contract: reads (get/list/delete) never
create the DB file or its tables (no-create-on-read IS implemented via
_has_reports_table), while save_report creates on demand.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from atlas.core.types import Conclusion, Report, StructuredClaim
from atlas.storage.store import delete_report, get_report, list_reports, save_report


def _report(text: str = "The GDP of Atlantis was $2 trillion in 2020.",
            stance: str = "unverifiable") -> Report:
    claim = StructuredClaim(raw_text=text, domain="economy",
                            claim_type="quantitative", subject=text[:160])
    return Report(claim=claim,
                  conclusion=Conclusion(stance=stance, summary="s"))  # type: ignore[arg-type]


def test_round_trip_is_byte_identical(tmp_path):
    p = tmp_path / "atlas.db"
    original = _report()
    save_report(original, path=p)
    loaded = get_report(original.report_id, path=p)
    assert loaded is not None
    assert loaded.model_dump_json() == original.model_dump_json()
    assert loaded.claim.raw_text == original.claim.raw_text
    assert loaded.conclusion.stance == "unverifiable"


def test_get_missing_returns_none_without_creating(tmp_path):
    p = tmp_path / "sub" / "nodb.db"
    assert get_report("rep_doesnotexist", path=p) is None
    assert not p.exists()
    assert not (tmp_path / "sub").exists()


def test_list_and_delete_on_missing_db_do_not_create(tmp_path):
    p = tmp_path / "nodb.db"
    assert list_reports(path=p) == []
    assert delete_report("rep_doesnotexist", path=p) is False
    assert not p.exists()


def test_atlas_db_env_selects_database(tmp_path, monkeypatch):
    p = tmp_path / "env.db"
    monkeypatch.setenv("ATLAS_DB", str(p))
    original = _report()
    save_report(original)  # no path -> ATLAS_DB
    assert p.exists()
    loaded = get_report(original.report_id)  # no path -> ATLAS_DB
    assert loaded is not None
    assert loaded.model_dump_json() == original.model_dump_json()


def test_save_overwrites_same_report_id(tmp_path):
    p = tmp_path / "atlas.db"
    first = _report(stance="unverifiable")
    save_report(first, path=p)
    second = _report(stance="supported")
    second.report_id = first.report_id
    save_report(second, path=p)
    loaded = get_report(first.report_id, path=p)
    assert loaded is not None
    assert loaded.conclusion.stance == "supported"
    assert len(list_reports(path=p)) == 1


def test_list_reports_returns_saved_rows(tmp_path):
    p = tmp_path / "atlas.db"
    first, second = _report(), _report()
    save_report(first, path=p)
    save_report(second, path=p)
    rows = list_reports(path=p)
    assert {r["report_id"] for r in rows} == {first.report_id,
                                              second.report_id}
    assert all(r["stance"] == "unverifiable" for r in rows)
