#!/usr/bin/env python3
"""Run PriceCharting full-universe shards serially under one supervisor."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNNER = ROOT / "pipelines" / "pc_full_shard_runner.py"
MAP_DIR = ROOT / "data" / "runtime" / "private-source-map"
CONSOLIDATED_MAP = MAP_DIR / "c11_pc_ebay_map_full900.jsonl"
try:
    from .failure_ledger import record_failure, record_resolution
    from .qualified_pool_operator import db
except ImportError:
    from failure_ledger import record_failure, record_resolution
    from qualified_pool_operator import db


def load_exact_product_by_variant() -> dict[int, str]:
    """Current exact DB identity is the only authority over historical map rows."""

    by_variant: dict[int, str] = {}
    with db() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT identity.variant_id AS direct_variant_id,
                   COALESCE(alias.canonical_variant_id, identity.variant_id)
                       AS canonical_variant_id,
                   identity.external_entity_id
            FROM catalog_source_identity AS identity
            LEFT JOIN catalog_variant_alias AS alias
              ON alias.duplicate_variant_id = identity.variant_id
            WHERE identity.source_code='pricecharting'
              AND identity.match_status='exact'
            """
        )
        for row in cursor.fetchall():
            product_id = str(row["external_entity_id"]).strip()
            for key in {
                int(row["direct_variant_id"]),
                int(row["canonical_variant_id"]),
            }:
                existing = by_variant.get(key)
                if existing is not None and existing != product_id:
                    raise ValueError(
                        f"multiple exact PriceCharting products for variant {key}"
                    )
                by_variant[key] = product_id
    return by_variant


def consolidate_maps(
    shards: int,
    output: Path = CONSOLIDATED_MAP,
    *,
    exact_product_by_variant: Mapping[int, str] | None = None,
) -> dict[str, int | str]:
    """Build one deterministic C11 input and fail closed on conflicting links."""

    by_variant: dict[int, dict] = {}
    duplicate_rows = 0
    stale_rows = 0
    non_exact_rows = 0
    for shard in range(shards):
        path = MAP_DIR / f"c11_pc_ebay_map_full900_shard_{shard}.jsonl"
        if not path.is_file():
            raise FileNotFoundError(f"missing shard map: {path.name}")
    map_paths = sorted(
        MAP_DIR.glob("c11_pc_ebay_map_full900_shard_*.jsonl")
    )
    for path in map_paths:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            row = json.loads(line)
            variant_id = int(row.get("variant_id") or 0)
            product_id = str(row.get("pc_product_id") or "").strip()
            if variant_id <= 0 or not product_id:
                raise ValueError(
                    f"invalid shard map row: {path.name}:{line_number}"
                )
            if exact_product_by_variant is not None:
                exact_product = exact_product_by_variant.get(variant_id)
                if exact_product is None:
                    non_exact_rows += 1
                    continue
                if str(exact_product) != product_id:
                    stale_rows += 1
                    continue
            current = by_variant.get(variant_id)
            if current is not None:
                current_product_id = str(current.get("pc_product_id") or "").strip()
                if current_product_id != product_id:
                    raise ValueError(
                        f"conflicting PriceCharting products for variant {variant_id}"
                    )
                duplicate_rows += 1
            by_variant[variant_id] = row

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for variant_id in sorted(by_variant):
            stream.write(
                json.dumps(
                    by_variant[variant_id],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
    os.replace(temporary, output)
    return {
        "rows": len(by_variant),
        "duplicateRows": duplicate_rows,
        "staleRowsSkipped": stale_rows,
        "nonExactRowsSkipped": non_exact_rows,
        "output": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shards", type=int, default=6)
    parser.add_argument("--runner", type=Path, default=DEFAULT_RUNNER)
    parser.add_argument(
        "--consolidate-only",
        action="store_true",
        help="rebuild the current-exact map without rerunning provider shards",
    )
    args = parser.parse_args()

    runner = args.runner.resolve()
    if not args.consolidate_only:
        for shard in range(args.shards):
            print(f"SUPERVISOR_SHARD {shard}", flush=True)
            result = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(runner),
                    "--shard",
                    str(shard),
                    "--shards",
                    str(args.shards),
                ],
                cwd=ROOT,
                check=False,
            )
            print(
                f"SUPERVISOR_SHARD_DONE {shard} rc {result.returncode}",
                flush=True,
            )
            if result.returncode != 0:
                record_failure(
                    source="pricecharting",
                    stage="serial_shard",
                    script=__file__,
                    item_key=shard,
                    reason_code="shard_nonzero_exit",
                    message="PriceCharting shard returned non-zero",
                    retryable=True,
                    context={"returnCode": result.returncode, "shards": args.shards},
                    next_action="supervisor_retry",
                )
                return result.returncode
            record_resolution(
                source="pricecharting",
                stage="serial_shard",
                script=__file__,
                item_key=shard,
                resolution="shard_completed",
            )
    try:
        consolidated = consolidate_maps(
            args.shards,
            exact_product_by_variant=load_exact_product_by_variant(),
        )
    except Exception as error:
        record_failure(
            source="pricecharting",
            stage="consolidate_full_map",
            script=__file__,
            item_key="PC-FULL-900",
            reason_code="map_consolidation_failed",
            message=str(error),
            retryable=True,
            next_action="agent_review",
            error_type=type(error).__name__,
        )
        raise
    record_resolution(
        source="pricecharting",
        stage="consolidate_full_map",
        script=__file__,
        item_key="PC-FULL-900",
        resolution="full_map_consolidated",
        evidence_paths=[Path(str(consolidated["output"]))],
    )
    print(f"SUPERVISOR_MAP {json.dumps(consolidated, sort_keys=True)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
