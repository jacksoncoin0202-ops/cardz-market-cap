#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""將 rebind 後遺留喺舊 variant 度嘅 PC 成交行 re-stamp 歸現任 exact 主人。

缺陷形狀（2026-08-13 發現，runbook 形狀 29 嘅「rebind 遺物」變種）：
market_sale_observation.variant_id 係 ingest 嗰刻由 map 摘落嚟嘅印，
c11_pc_sold_ingest 對已存在 fingerprint 只 dedupe 唔 re-stamp，所以
identity 修正改綁一個 product 之後，佢啲歷史成交仍然掛喺舊 variant：
  - 新主人 FE 30d 零成交（灰）——用戶 #24 Ace OP02-013 Manga 就係咁；
  - 舊主人靠 strict-ext join 先冇出毒（eligible view 擋住），
    但真數同時亦都入唔到任何人個史。

修法：ext 係產品身份、variant_id 係我哋嘅歸屬印——跟現任 exact binding
re-stamp 係修我哋自己嘅 join，唔係改 source 數據（頁面原文 sha 不變）。
淨係處理「現任主人存在」嘅組；無主孤兒（ext 已無 exact binding）留低，
view 本身已隔離佢哋。

精確名單 fail-closed（掃描證據 temp/_eligible_rows_ddl.py 2026-08-13）：
每組行前重驗 (a) 現任主人 binding 仲係 exact、(b) 行數同掃描一致、
(c) 舊 variant 嘅行先郁。收據落 data/runtime/operator/audit/。

用法：
  python -X utf8 pipelines/restamp_rebound_pc_sales_20260813.py --dry-run
  python -X utf8 pipelines/restamp_rebound_pc_sales_20260813.py --write
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from rebuild_036 import connect, DAILY_CREDENTIALS_ENV  # noqa: E402

# (ext, 舊 variant, 現任 exact 主人, 掃描時行數)
PAIRS = [
    ("6905554", 1447, 107, 30),
    ("8828331", 1723, 1212, 30),
    ("6235390", 1199, 1251, 30),
    ("6235454", 1427, 188, 30),
    ("6235917", 1752, 24, 29),
]

RECEIPT = ROOT / "data" / "runtime" / "operator" / "audit" / "pc_sales_restamp_20260813.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args()

    conn = connect(DAILY_CREDENTIALS_ENV)
    cur = conn.cursor()

    plan = []
    for ext, old_v, owner, expected_n in PAIRS:
        cur.execute(
            """SELECT COUNT(*) n FROM catalog_source_identity
               WHERE source_code='pricecharting' AND external_entity_id=%s
                 AND variant_id=%s AND match_status='exact'""",
            (ext, owner),
        )
        if int(cur.fetchone()["n"]) != 1:
            raise SystemExit(f"ext={ext}: v{owner} is not the exact owner any more, refusing")
        cur.execute(
            """SELECT COUNT(*) n FROM market_sale_observation
               WHERE source_code='pricecharting' AND external_entity_id=%s AND variant_id=%s""",
            (ext, old_v),
        )
        n = int(cur.fetchone()["n"])
        if n != expected_n:
            raise SystemExit(f"ext={ext}: rows on v{old_v} = {n}, scan said {expected_n}, refusing")
        cur.execute(
            """SELECT COUNT(*) n FROM market_sale_observation
               WHERE source_code='pricecharting' AND external_entity_id=%s AND variant_id=%s""",
            (ext, owner),
        )
        already = int(cur.fetchone()["n"])
        plan.append({"ext": ext, "fromVariant": old_v, "toVariant": owner,
                     "rows": n, "ownerAlreadyHad": already})

    print(json.dumps({"plan": plan}, ensure_ascii=False, indent=1))
    if args.dry_run:
        conn.close()
        return 0

    moved = {}
    try:
        for p in plan:
            cur.execute(
                """UPDATE market_sale_observation
                   SET variant_id=%s
                   WHERE source_code='pricecharting' AND external_entity_id=%s AND variant_id=%s""",
                (p["toVariant"], p["ext"], p["fromVariant"]),
            )
            if cur.rowcount != p["rows"]:
                raise RuntimeError(f"ext={p['ext']}: updated {cur.rowcount} != planned {p['rows']}")
            moved[p["ext"]] = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    receipt = {
        "completedAt": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "why": "product rebinds left historical PC sales stamped on the old variant;"
               " restamped to the product's current exact owner (shape-29 rebind residue)",
        "orphanPolicy": "73 no-exact-owner groups untouched; eligible view already excludes them",
        "moved": moved,
        "plan": plan,
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"moved": moved}))
    print(f"receipt: {RECEIPT}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
