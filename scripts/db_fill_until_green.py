#!/usr/bin/env python3
"""Read-only release diagnostics for an exported CARDZ candidate.

The historical script name is retained for operator discoverability.  It no
longer harvests, writes MySQL, rebuilds snapshots, loops, or advances a pointer.
Green means that the supplied production snapshot, strict QC receipt, and all
generation media pass the same real eligibility checks as the candidate baker.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bake_publish_pack import DEFAULT_ASSETS_ROOT, inspect_candidate


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only publication-candidate diagnostics")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--qc-receipt", type=Path, required=True)
    parser.add_argument("--assets-root", type=Path, default=DEFAULT_ASSETS_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    inspection = inspect_candidate(
        args.snapshot.resolve(),
        args.qc_receipt.resolve(),
        args.assets_root.resolve(),
    )
    blockers = inspection["blockers"]
    assets = inspection["assets"]
    report = {
        "schemaVersion": 1,
        "status": "green" if not blockers else "blocked",
        "readOnly": True,
        "generationId": inspection["generationId"],
        "snapshotContentSha256": inspection["snapshotContentSha256"],
        "qcReceiptSha256": inspection["qcReceiptSha256"],
        "coverage": inspection["coverage"],
        "qc": {
            "passed": not blockers,
            "blockerCount": len(blockers),
            "blockers": blockers,
        },
        "media": {
            "fileCount": len(assets),
            "baseCount": sum(entry["kind"] == "base" for entry in assets),
        },
        "publication": {
            "eligible": not blockers,
            "pointerAction": "none",
        },
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())
