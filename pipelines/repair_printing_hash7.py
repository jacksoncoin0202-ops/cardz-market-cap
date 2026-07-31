#!/usr/bin/env python3
"""Repair only drifted 7-field canonical printing hashes.

Dry-run is the default.  ``--write`` updates only explicitly requested,
complete printing rows in one transaction.  Source bindings, identity fields,
approval evidence, opaque IDs, and public data are never changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from card_identity import printing_identity_sha256_from_row  # noqa: E402
from db_runtime import add_connection_args, connection_from_args  # noqa: E402

try:
    from qualified_pool_operator import load_env  # noqa: E402

    load_env()
except Exception:
    pass


FIELDS = (
    "tcg_code",
    "card_language",
    "set_name",
    "collector_number",
    "edition_code",
    "parallel_code",
    "finish_code",
)


def build_plan(
    rows: Sequence[Mapping[str, Any]], requested: set[int]
) -> dict[str, Any]:
    complete: dict[int, dict[str, Any]] = {}
    expected_owners: dict[str, list[int]] = defaultdict(list)
    for source in rows:
        row = dict(source)
        variant_id = int(row["variant_id"])
        if any(not str(row.get(field) or "").strip() for field in FIELDS):
            continue
        expected = printing_identity_sha256_from_row(row)
        row["expected_sha256"] = expected
        complete[variant_id] = row
        expected_owners[expected].append(variant_id)

    repairs: list[dict[str, Any]] = []
    unchanged: list[int] = []
    incomplete: list[int] = []
    conflicts: list[dict[str, Any]] = []
    missing: list[int] = []
    all_ids = {int(row["variant_id"]) for row in rows}
    for variant_id in sorted(requested):
        if variant_id not in all_ids:
            missing.append(variant_id)
            continue
        row = complete.get(variant_id)
        if row is None:
            incomplete.append(variant_id)
            continue
        expected = str(row["expected_sha256"])
        owners = sorted(expected_owners[expected])
        if len(owners) != 1:
            conflicts.append(
                {
                    "variantId": variant_id,
                    "expectedSha256": expected,
                    "owners": owners,
                }
            )
            continue
        current = str(row.get("canonical_printing_sha256") or "").casefold()
        if current == expected:
            unchanged.append(variant_id)
            continue
        repairs.append(
            {
                "variantId": variant_id,
                "oldSha256": current,
                "newSha256": expected,
            }
        )
    return {
        "requested": sorted(requested),
        "repairs": repairs,
        "unchanged": unchanged,
        "incomplete": incomplete,
        "conflicts": conflicts,
        "missing": missing,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_connection_args(parser)
    parser.add_argument("--variant-id", action="append", type=int, required=True)
    parser.add_argument("--write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    requested = {int(value) for value in args.variant_id}
    connection = connection_from_args(args)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT variant_id, tcg_code, card_language, set_name,
                       collector_number, edition_code, parallel_code, finish_code,
                       canonical_printing_sha256
                FROM catalog_printing_identity
                ORDER BY variant_id
                """
            )
            rows = list(cursor.fetchall())
            plan = build_plan(rows, requested)
            plan["write"] = bool(args.write)
            plan["evidencePreserved"] = True
            blockers = plan["missing"] or plan["incomplete"] or plan["conflicts"]
            if blockers:
                connection.rollback()
                print(json.dumps(plan, indent=2, sort_keys=True))
                return 2
            if not args.write:
                connection.rollback()
                print(json.dumps(plan, indent=2, sort_keys=True))
                return 0

            for repair in plan["repairs"]:
                variant_id = int(repair["variantId"])
                temporary = hashlib.sha256(
                    f"tmp-printing-hash7|{variant_id}|{repair['newSha256']}".encode()
                ).hexdigest()
                cursor.execute(
                    """
                    UPDATE catalog_printing_identity
                    SET canonical_printing_sha256=%s
                    WHERE variant_id=%s AND canonical_printing_sha256=%s
                    """,
                    (temporary, variant_id, repair["oldSha256"]),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(f"printing hash precondition failed: {variant_id}")
            for repair in plan["repairs"]:
                cursor.execute(
                    """
                    UPDATE catalog_printing_identity
                    SET canonical_printing_sha256=%s
                    WHERE variant_id=%s
                    """,
                    (repair["newSha256"], repair["variantId"]),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"printing hash final update failed: {repair['variantId']}"
                    )

            if plan["repairs"]:
                marks = ", ".join(["%s"] * len(plan["repairs"]))
                ids = [int(row["variantId"]) for row in plan["repairs"]]
                cursor.execute(
                    f"""
                    SELECT variant_id, canonical_printing_sha256
                    FROM catalog_printing_identity
                    WHERE variant_id IN ({marks})
                    ORDER BY variant_id
                    """,
                    tuple(ids),
                )
                readback = {
                    int(row["variant_id"]): str(row["canonical_printing_sha256"])
                    for row in cursor.fetchall()
                }
                for repair in plan["repairs"]:
                    if readback.get(int(repair["variantId"])) != repair["newSha256"]:
                        raise RuntimeError(
                            f"printing hash readback failed: {repair['variantId']}"
                        )
            connection.commit()
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
