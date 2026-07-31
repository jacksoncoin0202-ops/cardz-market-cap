# -*- coding: utf-8 -*-
"""Purge eBay price/sale rows that lack exact identity bind.

Root cause (daddy 2026-07-30): ebay medians landed on variants without
catalog_source_identity(source=ebay, match_status=exact). SNK scripts already
document exact-only binding — orphan rows pollute G10-first ranking.

Keep:
  - ebay prices when exact ebay identity exists
  - ebay sales when exact ebay identity OR (external_entity_id pc:* AND exact pricecharting)

Usage:
  python -X utf8 tools/purge_orphan_ebay_prices.py --dry-run
  python -X utf8 tools/purge_orphan_ebay_prices.py --write
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    load_env()
    conn = db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COUNT(*) cnt, COUNT(DISTINCT variant_id) vids
        FROM market_price_observation p
        WHERE p.source_code='ebay'
          AND NOT EXISTS (
            SELECT 1 FROM catalog_source_identity i
            WHERE i.variant_id=p.variant_id
              AND i.source_code='ebay' AND i.match_status='exact'
          )
        """
    )
    px = dict(cur.fetchone())

    cur.execute(
        """
        SELECT COUNT(*) cnt, COUNT(DISTINCT variant_id) vids
        FROM market_sale_observation p
        WHERE p.source_code='ebay'
          AND NOT EXISTS (
            SELECT 1 FROM catalog_source_identity i
            WHERE i.variant_id=p.variant_id
              AND i.source_code='ebay' AND i.match_status='exact'
          )
          AND NOT (
            p.external_entity_id LIKE 'pc:%%'
            AND EXISTS (
              SELECT 1 FROM catalog_source_identity i
              WHERE i.variant_id=p.variant_id
                AND i.source_code='pricecharting' AND i.match_status='exact'
            )
          )
        """
    )
    sl = dict(cur.fetchone())

    report = {
        "orphanEbayPrices": px,
        "orphanEbaySalesExcludingPcExact": sl,
        "write": bool(args.write),
    }

    if not args.write:
        print(json.dumps(report, indent=2, default=str))
        print("dry-run only; pass --write to delete")
        return 0

    cur.execute(
        """
        DELETE p FROM market_price_observation p
        WHERE p.source_code='ebay'
          AND NOT EXISTS (
            SELECT 1 FROM catalog_source_identity i
            WHERE i.variant_id=p.variant_id
              AND i.source_code='ebay' AND i.match_status='exact'
          )
        """
    )
    report["deletedPriceRows"] = cur.rowcount

    cur.execute(
        """
        DELETE p FROM market_sale_observation p
        WHERE p.source_code='ebay'
          AND NOT EXISTS (
            SELECT 1 FROM catalog_source_identity i
            WHERE i.variant_id=p.variant_id
              AND i.source_code='ebay' AND i.match_status='exact'
          )
          AND NOT (
            p.external_entity_id LIKE 'pc:%%'
            AND EXISTS (
              SELECT 1 FROM catalog_source_identity i
              WHERE i.variant_id=p.variant_id
                AND i.source_code='pricecharting' AND i.match_status='exact'
            )
          )
        """
    )
    report["deletedSaleRows"] = cur.rowcount
    conn.commit()
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
