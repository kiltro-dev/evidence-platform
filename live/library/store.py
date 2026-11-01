"""Snapshot library for Atlas Live M2a: SQLite FTS5 + BM25, stdlib only.

- Snapshots are stored once (deduplicated by sha256) as files under
  ``snapshot_dir`` (default ``data/snap/``, git-ignored) plus one FTS5
  row for full-text search.
- ``search(query)`` ranks by BM25 so a new claim reuses an already
  captured snapshot instead of re-fetching ($0, no network).
- Packages (see ``live.evidence``) are stored as JSON and can be listed
  per claim or per snapshot (reuse audit).
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from live.evidence.package import (
    EvidencePackage,
    sha256_of_bytes,
    sha256_of_text,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  sha256      TEXT PRIMARY KEY,
  url         TEXT NOT NULL,
  source_type TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  path        TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS snapshots_fts USING fts5(
  sha256 UNINDEXED, url, text, tokenize = 'unicode61'
);
CREATE TABLE IF NOT EXISTS packages (
  package_id  TEXT PRIMARY KEY,
  claim_id    TEXT NOT NULL,
  sha256      TEXT NOT NULL,
  source_type TEXT NOT NULL,
  payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_packages_claim ON packages(claim_id);
CREATE INDEX IF NOT EXISTS idx_packages_sha ON packages(sha256);
"""

_TOKEN_RE = re.compile(r"[\w$%]+", re.UNICODE)


@dataclass
class SnapshotRecord:
    sha256: str
    url: str
    source_type: str
    captured_at: str
    path: str
    rank: float | None = None  # BM25 rank for search hits (lower = better)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fts_query(query: str) -> str:
    """Build a recall-oriented FTS5 query: quoted tokens joined by OR.

    Quoting each token disables FTS5 special-character injection while
    keeping unicode words (accents included) searchable.
    """
    tokens = _TOKEN_RE.findall(query or "")
    if not tokens:
        raise ValueError("query has no searchable tokens")
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens[:32])


class EvidenceStore:
    """SQLite-backed snapshot + package library."""

    def __init__(self, db_path: str | Path, snapshot_dir: str | Path = "data/snap") -> None:
        self.db_path = Path(db_path)
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -- snapshots ----------------------------------------------------

    def upsert_snapshot(self, url: str, text: str, source_type: str = "secondary",
                        captured_at: str | None = None) -> SnapshotRecord:
        """Store a snapshot once (dedupe by sha256); return its record."""
        return self.upsert_snapshot_bytes(
            url, text.encode("utf-8"), source_type, captured_at)

    def upsert_snapshot_bytes(self, url: str, raw: bytes,
                              source_type: str = "secondary",
                              captured_at: str | None = None) -> SnapshotRecord:
        """Store RAW snapshot bytes once (dedupe by sha256 of the bytes).

        M2c canonical path: the digest is over bytes as captured, so it
        matches the fetch-side sha even for legacy non-UTF-8 charsets.
        Text fixtures keep working via upsert_snapshot (utf-8 encode).
        """
        if not url or not url.strip():
            raise ValueError("url must be non-empty")
        if not raw or not bytes(raw).strip():
            raise ValueError("snapshot bytes must be non-empty")
        if source_type not in ("primary", "secondary"):
            raise ValueError("source_type must be 'primary' or 'secondary'")
        digest = sha256_of_bytes(bytes(raw))
        row = self._conn.execute(
            "SELECT sha256, url, source_type, captured_at, path FROM snapshots WHERE sha256 = ?",
            (digest,),
        ).fetchone()
        if row is not None:
            return SnapshotRecord(row["sha256"], row["url"], row["source_type"],
                                  row["captured_at"], row["path"])
        captured_at = captured_at or _utcnow_iso()
        path = str(self.snapshot_dir / f"{digest}.txt")
        Path(path).write_bytes(bytes(raw))
        text = bytes(raw).decode("utf-8", "replace")  # FTS index text
        self._conn.execute(
            "INSERT INTO snapshots (sha256, url, source_type, captured_at, path)"
            " VALUES (?, ?, ?, ?, ?)",
            (digest, url, source_type, captured_at, path),
        )
        self._conn.execute(
            "INSERT INTO snapshots_fts (sha256, url, text) VALUES (?, ?, ?)",
            (digest, url, text),
        )
        self._conn.commit()
        return SnapshotRecord(digest, url, source_type, captured_at, path)

    def get_snapshot_bytes(self, sha256: str) -> bytes | None:
        """Raw snapshot bytes by sha (None when unknown/missing)."""
        row = self._conn.execute(
            "SELECT path FROM snapshots WHERE sha256 = ?", (sha256,)
        ).fetchone()
        if row is None:
            return None
        p = Path(row["path"])
        return p.read_bytes() if p.exists() else None

    def get_snapshot_text(self, sha256: str) -> str | None:
        row = self._conn.execute(
            "SELECT path FROM snapshots WHERE sha256 = ?", (sha256,)
        ).fetchone()
        if row is None:
            return None
        p = Path(row["path"])
        if not p.exists():
            return None
        try:
            return p.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # legacy-charset bytes: never crash
            return p.read_bytes().decode("utf-8", "replace")

    def search(self, query: str, limit: int = 5) -> list[SnapshotRecord]:
        """Rank snapshots by BM25 (best first)."""
        fts_q = _fts_query(query)
        rows = self._conn.execute(
            """SELECT s.sha256, s.url, s.source_type, s.captured_at, s.path,
                      bm25(snapshots_fts) AS rank
               FROM snapshots_fts JOIN snapshots s USING (sha256)
               WHERE snapshots_fts MATCH ? ORDER BY rank LIMIT ?""",
            (fts_q, limit),
        ).fetchall()
        return [SnapshotRecord(r["sha256"], r["url"], r["source_type"],
                               r["captured_at"], r["path"], r["rank"]) for r in rows]

    # -- packages -----------------------------------------------------

    def store_package(self, pkg: EvidencePackage) -> None:
        """Insert or replace a package (upsert on package_id)."""
        self._conn.execute(
            "INSERT INTO packages (package_id, claim_id, sha256, source_type, payload)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(package_id) DO UPDATE SET claim_id=excluded.claim_id,"
            " sha256=excluded.sha256, source_type=excluded.source_type,"
            " payload=excluded.payload",
            (pkg.package_id, pkg.claim_id, pkg.sha256, pkg.source_type,
             json.dumps(pkg.to_dict(), ensure_ascii=False)),
        )
        self._conn.commit()

    def get_package(self, package_id: str) -> EvidencePackage | None:
        row = self._conn.execute(
            "SELECT payload FROM packages WHERE package_id = ?", (package_id,)
        ).fetchone()
        return EvidencePackage.from_dict(json.loads(row["payload"])) if row else None

    def packages_for_claim(self, claim_id: str) -> list[EvidencePackage]:
        rows = self._conn.execute(
            "SELECT payload FROM packages WHERE claim_id = ?", (claim_id,)
        ).fetchall()
        return [EvidencePackage.from_dict(json.loads(r["payload"])) for r in rows]

    def packages_for_snapshot(self, sha256: str) -> list[EvidencePackage]:
        """All packages reusing one snapshot (cross-claim reuse audit)."""
        rows = self._conn.execute(
            "SELECT payload FROM packages WHERE sha256 = ?", (sha256,)
        ).fetchall()
        return [EvidencePackage.from_dict(json.loads(r["payload"])) for r in rows]


__all__ = ["EvidenceStore", "SnapshotRecord"]
