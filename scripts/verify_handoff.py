#!/usr/bin/env python3
"""Fail-closed portability checks for a CARDZ backend handoff.

This is deliberately a repository check, not a deployment command.  It proves
that a clone has the tracked release archive materialized by Git LFS and that
the platform launchers/units are present before an operator bootstraps MySQL.
It never reads an environment file, connects to a database, or starts a job.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_RELATIVE = Path("data/private/cardz-active-bootstrap.tar.gz")
REQUIRED_FILES = (
    ".gitattributes",
    ".gitignore",
    "scripts/backend.py",
    "scripts/backend.sh",
    "scripts/backend.ps1",
    "scripts/bootstrap_archive.py",
    "deploy/systemd/cardz-market-cap-bootstrap.service",
    "deploy/systemd/cardz-market-cap-daily.service",
    "deploy/systemd/cardz-market-cap-daily.timer",
    "deploy/systemd/run-cardz-daily.sh",
    "deploy/systemd/README.md",
    "docs/AWS_HANDOFF.md",
    "integrations/grade10/run_service.py",
    "integrations/grade10/OPERATOR_GUIDE.md",
)


class HandoffError(RuntimeError):
    """A portable handoff prerequisite is missing or unsafe."""


def is_lfs_pointer(path: Path) -> bool:
    if not path.is_file():
        return False
    prefix = path.read_bytes()[:128].replace(b"\r\n", b"\n")
    return prefix.startswith(b"version https://git-lfs.github.com/spec/v1\n")


def archive_is_tracked(root: Path, relative: Path = ARCHIVE_RELATIVE) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", relative.as_posix()],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


def validate_layout(root: Path) -> dict[str, Any]:
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        raise HandoffError(f"handoff files are missing: {', '.join(missing)}")

    attributes = (root / ".gitattributes").read_text(encoding="utf-8")
    expected = "data/private/cardz-active-bootstrap.tar.gz filter=lfs diff=lfs merge=lfs -text"
    if expected not in attributes:
        raise HandoffError("bootstrap archive is not pinned to Git LFS")

    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    for required in (".env", "data/runtime/"):
        if required not in ignored:
            raise HandoffError(f"gitignore is missing required private rule: {required}")

    daily_unit = (root / "deploy/systemd/cardz-market-cap-daily.service").read_text(encoding="utf-8")
    bootstrap_unit = (root / "deploy/systemd/cardz-market-cap-bootstrap.service").read_text(encoding="utf-8")
    if "run-cardz-daily.sh" not in daily_unit or "backend.sh bootstrap --external-db" not in bootstrap_unit:
        raise HandoffError("systemd units do not invoke the portable backend launchers")

    return {"requiredFiles": len(REQUIRED_FILES), "lfsRule": "pinned", "privateRules": "present"}


def validate_archive(root: Path, *, require_archive: bool, require_tracked: bool, verify_archive: bool) -> dict[str, Any]:
    archive = root / ARCHIVE_RELATIVE
    if not archive.is_file():
        if require_archive:
            raise HandoffError(f"required bootstrap archive is missing: {ARCHIVE_RELATIVE.as_posix()}")
        return {"present": False, "tracked": archive_is_tracked(root), "verified": False}
    if is_lfs_pointer(archive):
        raise HandoffError("bootstrap archive is an unresolved Git LFS pointer; run git lfs pull")
    tracked = archive_is_tracked(root)
    if require_tracked and not tracked:
        raise HandoffError("bootstrap archive is not Git-tracked; a clean clone cannot restore it")
    result: dict[str, Any] = {"present": True, "tracked": tracked, "verified": False, "bytes": archive.stat().st_size}
    if verify_archive:
        sys.path.insert(0, str(root / "scripts"))
        import bootstrap_archive

        manifest = bootstrap_archive.verify_archive(archive)
        result.update({"verified": True, "lockId": manifest["lockId"], "entries": len(manifest["entries"])})
    return result


def verify_handoff(root: Path, *, require_archive: bool, require_tracked: bool, verify_archive: bool) -> dict[str, Any]:
    root = root.resolve()
    return {
        "root": str(root),
        "layout": validate_layout(root),
        "archive": validate_archive(
            root,
            require_archive=require_archive,
            require_tracked=require_tracked,
            verify_archive=verify_archive,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify CARDZ clean-clone/AWS handoff prerequisites")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--require-archive", action="store_true", help="Fail if the Git LFS bootstrap archive is absent")
    parser.add_argument("--require-tracked", action="store_true", help="Fail if the archive is not tracked by Git")
    parser.add_argument("--verify-archive", action="store_true", help="Verify archive paths, lock hash and per-entry checksums")
    args = parser.parse_args()
    report = verify_handoff(
        args.root,
        require_archive=args.require_archive,
        require_tracked=args.require_tracked,
        verify_archive=args.verify_archive,
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HandoffError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
