"""Snapshot library for Atlas Live M2a (SQLite FTS5 + BM25, stdlib only)."""

from .store import EvidenceStore, SnapshotRecord

__all__ = ["EvidenceStore", "SnapshotRecord"]
