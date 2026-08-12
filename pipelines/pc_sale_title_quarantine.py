#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC 成交 title↔卡號矛盾隔離 receipt（sales 軌嘅 quarantine ledger）。

2026-07-14 v1326 Latias 事故（runbook 形狀 29）：PC exact 產品頁被 PC 自己嘅
fuzzy match 塞入第二張卡嘅成交（title 印住 #060/095，我哋張卡係 #113），
$91 成交接受咗之後變成 30d 窗 anchor，出 +556%。

清毒分兩截：
  1. 未來：c11_pc_sold_ingest.verify_sale 嘅 exact-gate 加咗
     title_collector_contradiction 檢查，新毒 listing 落唔到 landing。
  2. 現在（呢個腳本）：已 landing、已 accepted 嘅毒 sales ——
     market_sale_observation 冇 status 欄，acceptance 係 append-only，
     sales history 係 VIEW，所以用 receipt ledger 隔離：
     判別器（同一份實現，import 返嚟）掃全部 PC 成交，寫
     data/runtime/operator/audit/pc_sale_title_quarantine_current.json，
     live-db-snapshot.ts bake 嗰陣讀佢，將呢啲 sale 嘅貢獻由日聚合度扣除。

receipt 係推導出嚟嘅（成日可以由判別器重新生成，唔係人手抄名單），
test_price_lane_contracts.py 有 DB gate 監住佢唔准過期：任何 accepted
矛盾 sale 唔喺 receipt 度 = 紅。

用法：
  python -X utf8 pipelines/pc_sale_title_quarantine.py          # 重新生成 receipt
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from c11_pc_sold_ingest import title_collector_contradiction  # noqa: E402

AUDIT_DIR = ROOT / "data" / "runtime" / "operator" / "audit"
CURRENT_RECEIPT = AUDIT_DIR / "pc_sale_title_quarantine_current.json"

SCAN_SQL = """
    SELECT s.id, s.variant_id, s.sold_at, s.unit_price_usd, s.quantity,
           s.transaction_value_usd, s.listing_title, p.collector_number
    FROM market_sale_observation s
    INNER JOIN catalog_printing_identity p ON p.variant_id = s.variant_id
    WHERE s.source_code = 'pricecharting' AND s.listing_title IS NOT NULL
"""


def build_receipt(rows) -> list[dict]:
    entries: list[dict] = []
    for r in rows:
        title = str(r["listing_title"])
        wanted = str(r["collector_number"])
        if not title_collector_contradiction(title, wanted):
            continue
        entries.append({
            "saleObservationId": int(r["id"]),
            "variantId": int(r["variant_id"]),
            "observedDate": str(r["sold_at"])[:10],
            "unitPriceUsd": float(r["unit_price_usd"]) if r["unit_price_usd"] is not None else None,
            "quantity": int(r["quantity"]) if r["quantity"] is not None else None,
            "transactionValueUsd": float(r["transaction_value_usd"]) if r["transaction_value_usd"] is not None else None,
            "wantedCollectorNumber": wanted,
            "listingTitle": title[:200],
            "reason": "title_collector_contradiction",
        })
    entries.sort(key=lambda e: e["saleObservationId"])
    return entries


def main() -> int:
    from rebuild_036 import connect, DAILY_CREDENTIALS_ENV  # noqa: E402

    conn = connect(DAILY_CREDENTIALS_ENV)
    cur = conn.cursor()
    cur.execute(SCAN_SQL)
    rows = cur.fetchall()
    conn.close()

    entries = build_receipt(rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    doc = {
        "generatedAt": stamp,
        "discriminator": "c11_pc_sold_ingest.title_collector_contradiction",
        "scannedSales": len(rows),
        "quarantinedSales": len(entries),
        "entries": entries,
    }
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_RECEIPT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    archive = AUDIT_DIR / f"pc_sale_title_quarantine_{stamp}.json"
    archive.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"scanned {len(rows)} pc sales; quarantined {len(entries)}")
    print(f"receipt: {CURRENT_RECEIPT}")
    print(f"archive: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
