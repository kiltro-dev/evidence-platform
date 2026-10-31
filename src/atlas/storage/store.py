"""SQLite store for analysis reports. Single file, zero infrastructure."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from ..core.types import Report

DEFAULT_DB = Path("data/atlas.db")


def db_path() -> Path:
    return Path(os.environ.get("ATLAS_DB", str(DEFAULT_DB)))


def init_db(path: Path | None = None) -> Path:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS reports (
                   report_id TEXT PRIMARY KEY,
                   created_at TEXT NOT NULL,
                   claim_text TEXT NOT NULL,
                   stance TEXT NOT NULL,
                   report_json TEXT NOT NULL
               )"""
        )
    return p


def save_report(report: Report, path: Path | None = None) -> str:
    p = init_db(path)
    with sqlite3.connect(p) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reports "
            "(report_id, created_at, claim_text, stance, report_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                report.report_id,
                report.created_at.isoformat(),
                report.claim.raw_text,
                report.conclusion.stance if report.conclusion else "unverifiable",
                report.model_dump_json(),
            ),
        )
    return report.report_id


def get_report(report_id: str, path: Path | None = None) -> Report | None:
    p = init_db(path)
    with sqlite3.connect(p) as conn:
        row = conn.execute(
            "SELECT report_json FROM reports WHERE report_id = ?", (report_id,)
        ).fetchone()
    if row is None:
        return None
    return Report.model_validate_json(row[0])


def list_reports(limit: int = 50, path: Path | None = None) -> list[dict]:
    p = init_db(path)
    with sqlite3.connect(p) as conn:
        rows = conn.execute(
            "SELECT report_id, created_at, claim_text, stance FROM reports "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"report_id": r[0], "created_at": r[1], "claim_text": r[2], "stance": r[3]}
        for r in rows
    ]
