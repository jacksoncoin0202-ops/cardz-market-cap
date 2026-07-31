#!/usr/bin/env python3
"""Repair SNK PSA10 bundle-contaminated daily prices from labelled trades."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args
from snk_market_data import trade_quantity

DEFAULT_RUNS = ROOT / "data" / "runtime" / "private-source-runs"


def load_trade_groups(root: Path) -> dict[tuple[int, str], list[dict[str, Any]]]:
    unique: dict[tuple[int, str, float, int], dict[str, Any]] = {}
    bundle_days: set[tuple[int, str]] = set()
    for path in sorted(root.glob("sources_*/snk-psa10.jsonl")):
        for line in path.open(encoding="utf-8-sig"):
            row = json.loads(line)
            item_id = row.get("item_id")
            if not isinstance(item_id, int):
                continue
            for trade in row.get("recent_trades") or []:
                if not isinstance(trade, dict):
                    continue
                quantity = trade_quantity(trade)
                sold_at = trade.get("soldAt")
                price = trade.get("price")
                if (
                    quantity is None
                    or not isinstance(sold_at, str)
                    or "T" not in sold_at
                    or not isinstance(price, (int, float))
                    or price <= 0
                ):
                    continue
                day = datetime.fromisoformat(sold_at.replace("Z", "+00:00")).date().isoformat()
                key = (item_id, sold_at, float(price), quantity)
                unique[key] = {"item_id": item_id, "day": day, "price": float(price), "quantity": quantity}
                if quantity > 1:
                    bundle_days.add((item_id, day))
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for trade in unique.values():
        key = (int(trade["item_id"]), str(trade["day"]))
        if key in bundle_days:
            grouped[key].append(trade)
    return grouped


def repair(connection: Any, groups: dict[tuple[int, str], list[dict[str, Any]]], apply: bool) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    with connection.cursor() as cursor:
        for (item_id, day), trades in sorted(groups.items()):
            cursor.execute(
                """
                SELECT COALESCE(alias.canonical_variant_id, identity.variant_id) AS variant_id
                FROM catalog_source_identity AS identity
                LEFT JOIN catalog_variant_alias AS alias
                  ON alias.duplicate_variant_id=identity.variant_id
                WHERE identity.source_code='snkrdunk' AND identity.external_entity_id=%s
                """,
                (str(item_id),),
            )
            identity = cursor.fetchone()
            if identity is None:
                continue
            variant_id = int(identity["variant_id"])
            unit_price = float(median(float(t["price"]) / int(t["quantity"]) for t in trades))
            card_count = sum(int(t["quantity"]) for t in trades)
            sales_value = sum(float(t["price"]) for t in trades)
            cursor.execute(
                """
                SELECT id,native_price,price_usd
                FROM market_price_observation
                WHERE variant_id=%s AND source_code='snk_psa10' AND observed_date=%s
                """,
                (variant_id, day),
            )
            price_row = cursor.fetchone()
            cursor.execute(
                """
                SELECT id,sales_count,native_sales_value
                FROM market_daily_sales_aggregate
                WHERE variant_id=%s AND source_code='snk_psa10' AND observed_date=%s
                """,
                (variant_id, day),
            )
            sales_row = cursor.fetchone()
            change = {
                "itemId": item_id,
                "variantId": variant_id,
                "observedDate": day,
                "transactions": len(trades),
                "cardCount": card_count,
                "normalizedUnitPriceJpy": round(unit_price, 6),
                "salesValueJpy": round(sales_value, 6),
                "oldPriceJpy": float(price_row["native_price"]) if price_row else None,
                "oldPriceUsd": float(price_row["price_usd"]) if price_row and price_row["price_usd"] is not None else None,
                "oldSalesCount": int(sales_row["sales_count"]) if sales_row else None,
                "oldSalesValueJpy": (
                    float(sales_row["native_sales_value"])
                    if sales_row and sales_row["native_sales_value"] is not None
                    else None
                ),
            }
            changes.append(change)
            if not apply:
                continue
            audit_hash = hashlib.sha256(
                json.dumps(change, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if price_row and float(price_row["native_price"] or 0) > 0:
                old_native = float(price_row["native_price"])
                old_usd = float(price_row["price_usd"]) if price_row["price_usd"] is not None else None
                new_usd = old_usd * unit_price / old_native if old_usd is not None else None
                cursor.execute(
                    """
                    UPDATE market_price_observation
                    SET native_price=%s, price_usd=%s, payload_sha256=%s
                    WHERE id=%s
                    """,
                    (unit_price, new_usd, audit_hash, int(price_row["id"])),
                )
            if sales_row:
                cursor.execute(
                    """
                    UPDATE market_daily_sales_aggregate
                    SET sales_count=%s, native_sales_value=%s, payload_sha256=%s
                    WHERE id=%s
                    """,
                    (card_count, sales_value, audit_hash, int(sales_row["id"])),
                )
    if apply:
        connection.commit()
    return {
        "mode": "apply" if apply else "dry-run",
        "bundleDays": len(groups),
        "dbChanges": len(changes),
        "changesSha256": hashlib.sha256(
            json.dumps(changes, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "changes": changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    add_connection_args(parser)
    args = parser.parse_args()
    connection = connection_from_args(args)
    try:
        report = repair(connection, load_trade_groups(args.runs_root), args.apply)
    finally:
        connection.close()
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "changes"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
