"""M5 bundle export: pack a run into a self-verifying ZIP.

Layout (top level + ``snapshots/``)::

    run.jsonl          bus event log (verbatim copy)
    snapshots/         snapshot bytes referenced by the packages
    packages.json      {"packages": [EvidencePackage.to_dict(), ...]}
    claims.json        {"claims": [{claim inputs + recorded verdict}, ...]}
    truth_ref.json     keynote truth rows (optional, when provided)
    verify_bundle.py   verbatim copy of live/bundle/verify_bundle.py
    README.txt         contents + how to verify
    manifest.json      {files: {arcname: sha256}, ...}

``claims.json`` is an addition to the M5 brief's file list (which names
``packages.json`` but no claim inputs): re-running the verifier needs
the claim value/unit/raw/year, which ``run.jsonl`` verdicts do not
carry. Everything else matches the brief.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from live.evidence.package import EvidencePackage


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_snapshot(pkg: dict, repo_root: Path,
                      snapshot_dir: Path | None) -> Path:
    """Locate a package's snapshot bytes (repo-relative first)."""
    rel = pkg.get("snapshot_path", "")
    candidates = []
    if rel:
        p = Path(rel)
        candidates.append(p if p.is_absolute() else repo_root / p)
    if snapshot_dir is not None:
        sha = pkg.get("sha256", "")
        candidates.append(snapshot_dir / f"{sha}.html")
        candidates.append(snapshot_dir / f"{sha}.txt")
    for cand in candidates:
        if cand.is_file():
            return cand
    raise FileNotFoundError(
        f"snapshot missing for {pkg.get('package_id')}: {rel!r}")


README_TEMPLATE = """Atlas Live M5 evidence bundle
=============================

Exported: {created_at}
Claims: {n_claims}  Packages: {n_packages}  Snapshots: {n_snapshots}

Contents
--------
run.jsonl        bus event log (verbatim copy of the run)
snapshots/       immutable source snapshots (content-addressed)
packages.json    evidence packages (M2a EvidencePackage dicts)
claims.json      claim inputs + recorded verdicts (verifier re-run needs these)
{truth_line}verify_bundle.py standalone offline re-verifier (no network, no model)
README.txt       this file
manifest.json    sha256 of every file above (verify_bundle.py excluded:
                 it verifies the rest; re-hash it against the repo when needed)

How to verify
-------------
  python verify_bundle.py bundle.zip
  python verify_bundle.py bundle.zip --engine stdlib   # without the repo
  python verify_bundle.py ./extracted_dir/

PASS/FAIL is printed per claim; exit code is 0 iff every claim passes.
Engines: "live.verifier" (the real live/verifier/check.py, needs the repo
on PYTHONPATH) or "stdlib-mirror" (vendored in verify_bundle.py).
"""


def export_bundle(*, run_log: str | Path,
                  packages: list[dict | EvidencePackage],
                  claims: list[dict],
                  out_path: str | Path,
                  truth_ref: str | Path | None = None,
                  repo_root: str | Path | None = None,
                  snapshot_dir: str | Path | None = None,
                  run_claims_for_truth: list[str] | None = None,
                  ) -> dict[str, Any]:
    """Pack a run into a self-verifying ZIP. Returns the manifest dict.

    Raises FileNotFoundError (missing run log / snapshot / truth ref)
    and ValueError (structurally invalid package, hash mismatch).
    """
    root = Path(repo_root) if repo_root else _repo_root()
    snap_dir = Path(snapshot_dir) if snapshot_dir else None
    run_log = Path(run_log)
    if not run_log.is_file():
        raise FileNotFoundError(f"run log not found: {run_log}")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pkg_dicts: list[dict] = [
        p.to_dict() if isinstance(p, EvidencePackage) else dict(p)
        for p in packages]
    for pkg in pkg_dicts:
        EvidencePackage.from_dict(pkg)  # structural gate at export time

    # Snapshot bytes (hash-checked against the package claim).
    snapshots: dict[str, bytes] = {}
    for pkg in pkg_dicts:
        src = _resolve_snapshot(pkg, root, snap_dir)
        raw = src.read_bytes()
        if _sha256_bytes(raw) != pkg["sha256"]:
            raise ValueError(
                f"snapshot hash mismatch for {pkg['package_id']}: "
                f"{src} does not hash to {pkg['sha256'][:12]}…")
        snapshots[f"snapshots/{src.name}"] = raw

    claim_rows: list[dict] = []
    for claim in claims:
        row = dict(claim)
        for key in ("claim_id", "value", "verdict"):
            if key not in row:
                raise ValueError(f"claim row missing {key!r}: {row!r}")
        claim_rows.append(row)

    run_bytes = run_log.read_bytes()
    # run.jsonl must be line-JSON (bus contract); validate cheaply.
    for i, line in enumerate(run_bytes.decode("utf-8").splitlines(), 1):
        if line.strip():
            try:
                json.loads(line)
            except ValueError as exc:
                raise ValueError(f"run.jsonl line {i} is not JSON") from exc

    verifier_src = (Path(__file__).resolve().parent
                    / "verify_bundle.py").read_bytes()

    truth_bytes: bytes | None = None
    if truth_ref is not None:
        tp = Path(truth_ref)
        if not tp.is_file():
            raise FileNotFoundError(f"truth ref not found: {tp}")
        truth_bytes = tp.read_bytes()
        json.loads(truth_bytes.decode("utf-8"))  # must be JSON

    created_at = _utcnow_iso()
    truth_line = ("truth_ref.json   keynote truth rows (judge reference)\n"
                  if truth_bytes is not None else "")
    readme = README_TEMPLATE.format(
        created_at=created_at, n_claims=len(claim_rows),
        n_packages=len(pkg_dicts), n_snapshots=len(snapshots),
        truth_line=truth_line).encode("utf-8")
    payloads: dict[str, bytes] = {
        "run.jsonl": run_bytes,
        "packages.json": json.dumps(
            {"packages": pkg_dicts}, ensure_ascii=False, indent=1
        ).encode("utf-8"),
        "claims.json": json.dumps(
            {"claims": claim_rows}, ensure_ascii=False, indent=1
        ).encode("utf-8"),
        "verify_bundle.py": verifier_src,
        "README.txt": readme,
    }
    if truth_bytes is not None:
        payloads["truth_ref.json"] = truth_bytes
    payloads.update(snapshots)

    manifest = {
        "tool": "m5-export",
        "created_at": created_at,
        "claims": [c["claim_id"] for c in claim_rows],
        "packages": [p["package_id"] for p in pkg_dicts],
        "files": {name: _sha256_bytes(raw)
                  for name, raw in sorted(payloads.items())},
    }
    payloads["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, indent=1).encode("utf-8")

    with ZipFile(out_path, "w", ZIP_DEFLATED) as zf:
        for name in sorted(payloads):
            zf.writestr(name, payloads[name])
    manifest["bundle"] = str(out_path)
    _ = run_claims_for_truth  # reserved: truth-row filtering (unused in M5)
    return manifest


__all__ = ["export_bundle"]
