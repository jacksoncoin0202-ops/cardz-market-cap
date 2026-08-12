#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quarantine price rows whose source-family contradicts the variant's other family.

2026-08-12 用戶指出嘅「兩條 lane 價位交錯」形狀（同一張卡 ¥258,000 級同 ¥15,000 級
梅花間竹）：根因唔係窗口算式，係身份 —— 同一個 variant 下面，兩個 source 家族
（snk 家族 vs pricecharting）嘅 180 日中位數差 ≥3 倍，即係至少一邊綁錯咗產品。

裁決規則（fail-closed，唔揀「邊邊睇落啱」）：
  1. variant 兩個家族 180 日 ready 中位數比 ≥ CONFLICT_RATIO → 入矛盾名單；
  2. 矛盾 variant 入面，每個 (家族, entity) side 對 operator_strict_source_identity
     （037 授權面；snk/snk_psa10 嘅身份掛喺 snkrdunk 名下）：
       - side 唔喺 strict view（manual_review / conflict / 無 binding 行 / 空 entity）
         → 該 side 全歷史 ready 行隔離 'quarantined'（身份隔離字：第日 operator
           證實 binding exact+strict，rebuild_036 release lane 會放返出嚟）；
       - side 喺 strict view → 唔郁；
  3. 如果矛盾 variant 所有 side 都 strict → 唔郁任何行，出 both_strict_conflict
     報告（兩個 exact 印互相矛盾 = 其中一個 exact 印錯咗，要人手重裁，
     機器唔准自己揀邊個啱）。

只用 'quarantined'，唔用 'quarantined_lane'：呢啲行唔係捏造（provider 度真係有呢啲
價），只係身份未證實 —— 正正係身份隔離生命週期嘅本意。

用法：
  python -X utf8 pipelines/price_identity_conflict_audit.py           # dry-run + receipt
  python -X utf8 pipelines/price_identity_conflict_audit.py --write   # 落隔離印
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

QUARANTINE_STATUS = "quarantined"
CONFLICT_RATIO = 3.0
MEDIAN_WINDOW_DAYS = 180
SOURCES = ("snkrdunk", "snk", "snk_psa10", "pricecharting")
FAMILY_OF_SOURCE = {
    "snkrdunk": "snk",
    "snk": "snk",
    "snk_psa10": "snk",
    "pricecharting": "pricecharting",
}
IDENTITY_SOURCE_OF_FAMILY = {"snk": "snkrdunk", "pricecharting": "pricecharting"}
AUDIT_DIR = ROOT / "data" / "runtime" / "operator" / "audit"

READY_ROWS_SQL = f"""
    SELECT p.id, p.variant_id, p.source_code, p.source_external_entity_id AS entity,
           p.observed_date, p.price_usd
    FROM market_price_observation p
    WHERE p.metric_status = 'ready'
      AND p.source_code IN {SOURCES!r}
"""

APPLY_QUARANTINE_SQL = f"""
    UPDATE market_price_observation
    SET metric_status = '{QUARANTINE_STATUS}'
    WHERE id IN ({{placeholders}})
      AND metric_status = 'ready'
"""


def find_conflicts(rows: list[dict[str, Any]], today) -> dict[int, dict[str, Any]]:
    """180 日家族中位數比 ≥ CONFLICT_RATIO 嘅 variant → {vid: 證據}。"""
    recent: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        price = r["price_usd"]
        if price is None or float(price) <= 0:
            continue
        if (today - r["observed_date"]).days > MEDIAN_WINDOW_DAYS:
            continue
        fam = FAMILY_OF_SOURCE[str(r["source_code"])]
        recent[int(r["variant_id"])][fam].append(float(price))
    conflicts: dict[int, dict[str, Any]] = {}
    for vid, fams in recent.items():
        if len(fams) < 2:
            continue
        meds = {f: median(v) for f, v in fams.items()}
        lo, hi = min(meds.values()), max(meds.values())
        if lo > 0 and hi / lo >= CONFLICT_RATIO:
            conflicts[vid] = {
                "ratio": round(hi / lo, 2),
                "familyMedians": {f: round(m, 2) for f, m in meds.items()},
                "familyRowCounts": {f: len(v) for f, v in fams.items()},
            }
    return conflicts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="落 quarantined 印（默認 dry-run）")
    args = parser.parse_args()

    from rebuild_036 import connect, DAILY_CREDENTIALS_ENV  # noqa: E402

    conn = connect(DAILY_CREDENTIALS_ENV)
    cur = conn.cursor()

    cur.execute("SELECT CURDATE() d")
    today = cur.fetchone()["d"]

    cur.execute(READY_ROWS_SQL)
    rows = [dict(r) for r in cur.fetchall()]
    print(f"ready rows in scope: {len(rows)}")

    conflicts = find_conflicts(rows, today)
    print(f"conflict variants (>= {CONFLICT_RATIO}x family median, {MEDIAN_WINDOW_DAYS}d): {len(conflicts)}")

    # strict view membership per (variant, identity_source, entity)
    sides: set[tuple[int, str, str]] = set()
    for r in rows:
        vid = int(r["variant_id"])
        if vid not in conflicts:
            continue
        fam = FAMILY_OF_SOURCE[str(r["source_code"])]
        sides.add((vid, IDENTITY_SOURCE_OF_FAMILY[fam], str(r["entity"] or "").strip()))
    strict_sides: set[tuple[int, str, str]] = set()
    entity_sides = [s for s in sides if s[2]]
    if entity_sides:
        ph = ",".join(["(%s,%s,%s)"] * len(entity_sides))
        flat: list[Any] = []
        for vid, id_src, entity in entity_sides:
            flat.extend((vid, id_src, entity))
        cur.execute(
            f"""
            SELECT variant_id, source_code, external_entity_id
            FROM operator_strict_source_identity
            WHERE (variant_id, source_code, external_entity_id) IN ({ph})
            """,
            tuple(flat),
        )
        for r in cur.fetchall():
            strict_sides.add((int(r["variant_id"]), str(r["source_code"]), str(r["external_entity_id"])))

    receipts: list[dict[str, Any]] = []
    quarantine_ids: list[int] = []
    per_variant: dict[int, dict[str, Any]] = {}
    for vid, evidence in sorted(conflicts.items()):
        v_rows = [r for r in rows if int(r["variant_id"]) == vid]
        v_sides = sorted({(IDENTITY_SOURCE_OF_FAMILY[FAMILY_OF_SOURCE[str(r["source_code"])]], str(r["entity"] or "").strip()) for r in v_rows})
        side_strict = {s: (vid, s[0], s[1]) in strict_sides for s in v_sides}
        all_strict = all(side_strict.values())
        v_quarantined = 0
        if not all_strict:
            for r in v_rows:
                id_src = IDENTITY_SOURCE_OF_FAMILY[FAMILY_OF_SOURCE[str(r["source_code"])]]
                entity = str(r["entity"] or "").strip()
                if side_strict[(id_src, entity)]:
                    continue
                quarantine_ids.append(int(r["id"]))
                v_quarantined += 1
        per_variant[vid] = {
            **evidence,
            "sides": [
                {"identitySource": s[0], "entity": s[1] or None, "strict": side_strict[s]}
                for s in v_sides
            ],
            "verdict": "both_strict_conflict" if all_strict else "quarantine_non_strict_sides",
            "rowsQuarantined": v_quarantined,
        }
        receipts.append({"variantId": vid, **per_variant[vid]})
        print(f"  v{vid}: ratio {evidence['ratio']}x {evidence['familyMedians']} -> "
              f"{per_variant[vid]['verdict']} ({v_quarantined} rows)")

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt_path = AUDIT_DIR / f"price_identity_conflict_audit_{stamp}.jsonl"
    with receipt_path.open("w", encoding="utf-8") as fh:
        for entry in receipts:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    summary = {
        "generatedAt": stamp,
        "mode": "write" if args.write else "dry-run",
        "conflictVariants": len(conflicts),
        "bothStrictConflicts": sorted(v for v, d in per_variant.items() if d["verdict"] == "both_strict_conflict"),
        "rowsToQuarantine": len(quarantine_ids),
        "quarantineStatus": QUARANTINE_STATUS,
        "receiptPath": str(receipt_path),
    }
    summary_path = AUDIT_DIR / f"price_identity_conflict_audit_{stamp}_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nrows to quarantine: {len(quarantine_ids)}")
    print(f"receipt: {receipt_path}")
    print(f"summary: {summary_path}")

    if not args.write:
        print("\ndry-run（冇寫 DB）。用 --write 落印。")
        conn.close()
        return 0

    if not quarantine_ids:
        print("nothing to quarantine")
        conn.close()
        return 0

    placeholders = ",".join(["%s"] * len(quarantine_ids))
    cur.execute(APPLY_QUARANTINE_SQL.format(placeholders=placeholders), tuple(quarantine_ids))
    updated = cur.rowcount
    if updated != len(quarantine_ids):
        conn.rollback()
        raise RuntimeError(
            f"quarantine update touched {updated} rows, expected {len(quarantine_ids)}; rolled back"
        )
    conn.commit()
    print(f"quarantined {updated} rows -> metric_status='{QUARANTINE_STATUS}'")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
