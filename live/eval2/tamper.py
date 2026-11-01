"""F2-1 tamper demo: 1 altered byte in 1 snapshot => bundle FAIL.

Takes a REAL bundle (default: ``data/demo/atlas-live-bundle.zip``),
copies it, flips exactly one byte of the first non-empty snapshot
entry, and re-runs the UNMODIFIED ``live.bundle.verify_bundle``
(import-only) to show the verdict flips PASS -> FAIL. ``manifest.json``
is left byte-identical inside the tampered copy, so the failure is the
integrity check doing its job — not a rigged manifest.

Usage::

    .venv/bin/python live/eval2/tamper.py
    .venv/bin/python live/eval2/tamper.py --bundle data/runs/eval2-....zip
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from live.bundle.verify_bundle import verify_bundle  # noqa: E402 (import-only)

LIVE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = LIVE_DIR.parent
DEFAULT_BUNDLE = REPO_ROOT / "data" / "demo" / "atlas-live-bundle.zip"

ENGINE = "live"  # the real verifier (stdlib mirror has known M2d drift)


def first_snapshot_entry(bundle: str | Path) -> str:
    """First non-empty ``snapshots/*`` arcname in the ZIP (pure read)."""
    with zipfile.ZipFile(bundle) as zf:
        cands = sorted(i.filename for i in zf.infolist()
                       if i.filename.startswith("snapshots/")
                       and not i.is_dir() and i.file_size > 0)
    if not cands:
        raise ValueError(f"no snapshot entries in {bundle}")
    return cands[0]


def tamper_bundle(src: str | Path, dst: str | Path,
                  byte_offset: int = 0, xor: int = 0xFF,
                  ) -> dict[str, Any]:
    """Copy ``src`` ZIP to ``dst`` flipping 1 byte of 1 snapshot.

    Every other entry (including ``manifest.json``) is copied
    byte-identical. Returns the tamper record (arcname, offset,
    original/new byte). Pure ZIP surgery — no verification here.
    """
    src, dst = Path(src), Path(dst)
    target = first_snapshot_entry(src)
    info: dict[str, Any] = {"arcname": target, "byte_offset": byte_offset,
                            "xor": xor}
    with zipfile.ZipFile(src) as zin:
        raw = bytearray(zin.read(target))
        if not raw:
            raise ValueError(f"empty snapshot entry: {target}")
        offset = byte_offset % len(raw)
        info["byte_offset"] = offset
        info["orig_byte"] = raw[offset]
        raw[offset] ^= xor
        info["new_byte"] = raw[offset]
        info["entry_bytes"] = len(raw)
        manifest_before = zin.read("manifest.json")
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = bytes(raw) if item.filename == target \
                    else zin.read(item.filename)
                zout.writestr(item, data)
    with zipfile.ZipFile(dst) as zcheck:
        assert zcheck.read("manifest.json") == manifest_before, \
            "manifest must stay byte-identical (integrity demo)"
    info["src"] = str(src)
    info["dst"] = str(dst)
    return info


def demo(bundle: str | Path = DEFAULT_BUNDLE,
         workdir: str | Path | None = None) -> dict[str, Any]:
    """Pristine PASS vs 1-byte-tampered FAIL on a real bundle.

    Runs the unmodified ``verify_bundle`` (live engine) on the pristine
    bundle and on the tampered copy. Returns the full demo report.
    """
    bundle = Path(bundle)
    if not bundle.is_file():
        raise FileNotFoundError(f"bundle not found: {bundle}")
    tmp = Path(workdir) if workdir else Path(
        tempfile.mkdtemp(prefix="f2-tamper-"))
    tmp.mkdir(parents=True, exist_ok=True)
    tampered = tmp / f"{bundle.stem}-tampered.zip"
    pristine_copy = tmp / bundle.name
    if pristine_copy.resolve() != bundle.resolve():
        shutil.copy2(bundle, pristine_copy)
    tamper_info = tamper_bundle(pristine_copy, tampered)
    before = verify_bundle(str(pristine_copy), engine=ENGINE)
    after = verify_bundle(str(tampered), engine=ENGINE)
    return {
        "bundle": str(bundle),
        "engine": ENGINE,
        "tampered_copy": str(tampered),
        "tamper": tamper_info,
        "pristine": {"ok": before["ok"],
                     "files": before["files"],
                     "n_claims": len(before["claims"])},
        "tampered": {"ok": after["ok"],
                     "files": after["files"],
                     "n_claims": len(after["claims"])},
        "demo_pass": bool(before["ok"] and not after["ok"]),
    }


def build_parser() -> argparse.ArgumentParser:
    """CLI flags for the tamper demo."""
    parser = argparse.ArgumentParser(
        description="F2-1: flip 1 byte of 1 snapshot in a real bundle "
        "and show verify_bundle PASS -> FAIL.")
    parser.add_argument("--bundle", default=str(DEFAULT_BUNDLE))
    parser.add_argument("--workdir", default=None,
                        help="output dir (default: mkdtemp f2-tamper-*)")
    parser.add_argument("--out", default=None,
                        help="demo report JSON (default: <workdir>/demo.json)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint (argv injectable for tests)."""
    args = build_parser().parse_args(argv)
    report = demo(args.bundle, args.workdir)
    out = args.out or str(Path(report["tampered_copy"]).parent / "demo.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"[f2-tamper] pristine ok={report['pristine']['ok']} -> "
          f"tampered ok={report['tampered']['ok']} "
          f"(files.bad={report['tampered']['files'].get('bad')}) "
          f"demo_pass={report['demo_pass']}", flush=True)
    print(f"[f2-tamper] report: {out}", flush=True)
    return 0 if report["demo_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
