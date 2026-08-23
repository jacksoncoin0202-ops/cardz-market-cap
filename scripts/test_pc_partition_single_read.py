#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""partition_local_pc_stock_pages must not read a page the validator already hashed.

A04 2026-08-23: 33 s of the PC lane's pre-child time was this loop re-reading
and re-hashing 1238 pages (718 MB over /mnt/c) that validate_pc_psa10 had just
read and hashed. Contract:
  * validator returns artifact_sha256 -> partition reads 0 bytes, reports it
  * validator without artifact_sha256  -> partition reads once, hashes itself
  * missing file / directory           -> local_exact_html_missing, no exception

Run: python -X utf8 scripts/test_pc_partition_single_read.py
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import collect_control as cc  # noqa: E402
import pc_psa10_price_derivation as deriv  # noqa: E402

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def main() -> int:
    test_tmp_root = ROOT / "data" / "runtime" / "test-tmp"
    test_tmp_root.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(prefix="pc_partition_single_read_", dir=str(test_tmp_root)))
    html_path = tmpdir / "1.html"
    html_path.write_bytes(b"<html>" + b"x" * 6000 + b"</html>")
    real_sha = hashlib.sha256(html_path.read_bytes()).hexdigest()
    row = {
        "variant_id": 1,
        "pc_product_id": "7108819",
        "pc_url": "https://www.pricecharting.com/game/pokemon-promo/test-card-85",
        "html_path": str(html_path.relative_to(ROOT)).replace("\\", "/"),
    }
    item = {"variantId": 1, "externalId": "7108819", "modeNeeded": "incr"}

    reads: list[str] = []
    original_read_bytes = Path.read_bytes

    def counting_read_bytes(self: Path) -> bytes:
        reads.append(str(self))
        return original_read_bytes(self)

    original_map = cc._pc_subset_map
    original_validate = deriv.validate_pc_psa10
    cc._pc_subset_map = lambda items, *, mode, label: (tmpdir / "map.jsonl", [row])  # type: ignore[assignment]
    Path.read_bytes = counting_read_bytes  # type: ignore[assignment]
    try:
        # 1. validator carries the hash -> zero reads in partition, hash reused
        deriv.validate_pc_psa10 = lambda _row: (  # type: ignore[assignment]
            {"field": "VGPC.chart_data.manualonly.last", "observed_date": "2026-08-14",
             "artifact_sha256": real_sha},
            "accepted",
        )
        reads.clear()
        replayed, network, report = cc.partition_local_pc_stock_pages([item], mode="incr", dry_run=False)
        check("hashed page replays", [i["variantId"] for i in replayed], [1])
        check("validator hash reused", report["payloadShaByVariant"].get("1"), real_sha)
        check("evidence row carries the same hash", report["rows"][0]["htmlSha256"], real_sha)
        check("partition reads zero bytes when validator hashed", reads, [])

        # 2. validator without the hash -> partition reads exactly once and hashes
        deriv.validate_pc_psa10 = lambda _row: (  # type: ignore[assignment]
            {"field": "VGPC.chart_data.manualonly.last", "observed_date": "2026-08-14"},
            "accepted",
        )
        reads.clear()
        replayed, network, report = cc.partition_local_pc_stock_pages([item], mode="incr", dry_run=False)
        check("fallback still replays", [i["variantId"] for i in replayed], [1])
        check("fallback hash is the file hash", report["payloadShaByVariant"].get("1"), real_sha)
        check("fallback reads exactly once", len(reads), 1)

        # 3. missing file / directory -> network lane, no exception
        row["html_path"] = str((tmpdir / "missing.html").relative_to(ROOT)).replace("\\", "/")
        replayed, network, report = cc.partition_local_pc_stock_pages([item], mode="incr", dry_run=False)
        check("missing file -> network", report["networkReasons"].get("1"), "local_exact_html_missing")
        (tmpdir / "dir.html").mkdir()
        row["html_path"] = str((tmpdir / "dir.html").relative_to(ROOT)).replace("\\", "/")
        replayed, network, report = cc.partition_local_pc_stock_pages([item], mode="incr", dry_run=False)
        check("directory -> network", report["networkReasons"].get("1"), "local_exact_html_missing")
    finally:
        Path.read_bytes = original_read_bytes  # type: ignore[assignment]
        cc._pc_subset_map = original_map  # type: ignore[assignment]
        deriv.validate_pc_psa10 = original_validate  # type: ignore[assignment]

    for line in FAILED:
        print(line)
    print(f"{'FAIL' if FAILED else 'OK'} checks={CHECKS} failed={len(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
