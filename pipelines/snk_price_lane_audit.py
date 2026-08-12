#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adjudicate SNK-family price rows written outside the canonical kline lanes.

2026-08-12 事故形狀：一批 ad-hoc「補數」lane（gap_snk_fallback / snk_kline_sparse /
snk_harvest_chip / snk_flood_chip / snk_kline_940 / snk_price_full_sales / a06_* /
sales_cache_* / pm_sales_gap_price / …）將「最低掛價」「carry-forward 舊價」「JST 錯日」
寫入 market_price_observation 當成 psa10 成交參考價。SNKRDUNK 自己嘅 trading chart
（成交履歷，非掛價）先係唯一權威：canonical lane（snk_kline_ingest_* /
rebuild036_snk_kline*）100% 由佢嚟。呢個 audit 將所有非 canonical lane 嘅 ready 行
逐行同 provider chart 對數：

先過身份閘，再對 chart：

  identity_not_strict   (variant,item) 唔喺 operator_strict_source_identity（037 授權面）
                        —— manual_review / conflict / 形狀 20 嘅 pre-036 陳年 exact 全中。
                        呢類係「身份未證實」，用標準身份隔離字 'quarantined'：第日
                        binding 證實到 exact+strict，rebuild_036 release lane 會放返佢出嚟
                        （嗰條 lane 本來就係為呢個生命週期而設）。
  keep                  strict 綁定 + chart 同日同值（native JPY ±10%；USD 行用
                        JPY/USD∈[100,200] 合理帶，90,611 行 canonical 對照組驗證過）
  quarantine ('quarantined_lane'，捏造行，唔入 release 生命週期):
    fabricated_day      chart 嗰日根本冇成交，±1 日都對唔上值（carry-forward、掛價冒充）
    value_mismatch      同日但值差超帶（lane 錯體/單位錯）
    shifted_day_dup     ±1 日對上值，而且 canonical 已經載住嗰單成交（JST 幻影重複日）
    unbound_snk_row     行自稱 SNK 但冇 entity id、變體又冇任何 SNK identity 可對證
  keep (sole carrier)   ±1 日對上值但 canonical 冇兄弟行 —— 真成交錯咗日，證據唔銷毀
  unadjudicated         strict 綁定但攞唔到 chart（EN storefront endpoint 未支援/停牌）
                        —— fail-closed，唔郁，出報告

'quarantined_lane' 唔借用 'quarantined'：後者係身份隔離字，release lane 會放行
exact+strict 嘅行；捏造行冇得「證實返」（一欄兩義=形狀 1）。所有讀者本身用
metric_status='ready' 等值 filter，兩個新流向都自動 fail-closed。

用法：
  python -X utf8 pipelines/snk_price_lane_audit.py                 # dry-run + receipt
  python -X utf8 pipelines/snk_price_lane_audit.py --fetch-missing # 補攞未覆蓋 item chart
  python -X utf8 pipelines/snk_price_lane_audit.py --write         # 落隔離印
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

CANONICAL_RUN_PREFIXES = ("snk_kline_ingest_", "rebuild036_snk_kline")
SNK_SOURCES = ("snkrdunk", "snk", "snk_psa10")
QUARANTINE_STATUS = "quarantined_lane"
IDENTITY_QUARANTINE_STATUS = "quarantined"
# 90,611 行 2026 canonical 對照組：JPY/USD 比率全部落喺呢個帶入面。
USD_JPY_RATIO_BAND = (100.0, 200.0)
NATIVE_TOLERANCE = 0.10
COLLECT_DIR = ROOT / "data" / "runtime" / "operator" / "collect"
AUDIT_DIR = ROOT / "data" / "runtime" / "operator" / "audit"

# 呢條 query 用 cur.execute(sql)（冇 args）行：pymysql 唔會做 %-interpolation，
# 所以 LIKE pattern 要用單 %。
SUSPECT_ROWS_SQL = f"""
    SELECT p.id, p.variant_id, p.source_code, p.source_external_entity_id AS item,
           p.observed_date, p.price_usd, p.native_price, r.run_key
    FROM market_price_observation p
    INNER JOIN market_ingest_run r ON r.id = p.run_id
    WHERE p.source_code IN {SNK_SOURCES!r}
      AND p.metric_status = 'ready'
      AND r.run_key NOT LIKE '{CANONICAL_RUN_PREFIXES[0]}%'
      AND r.run_key NOT LIKE '{CANONICAL_RUN_PREFIXES[1]}%'
"""

APPLY_QUARANTINE_SQL = f"""
    UPDATE market_price_observation
    SET metric_status = '{QUARANTINE_STATUS}'
    WHERE id IN ({{placeholders}})
      AND metric_status = 'ready'
"""

APPLY_IDENTITY_QUARANTINE_SQL = f"""
    UPDATE market_price_observation
    SET metric_status = '{IDENTITY_QUARANTINE_STATUS}'
    WHERE id IN ({{placeholders}})
      AND metric_status = 'ready'
"""


def load_charts_from_harvests() -> dict[int, dict[str, float]]:
    """每個 item 用最新 harvest 嗰份 chart（range=all，provider 全歷史）。"""
    charts: dict[int, dict[str, float]] = {}
    files = sorted(
        [*COLLECT_DIR.glob("snk_price_harvest_*.jsonl"), *COLLECT_DIR.glob("snk_shared_harvest_*.jsonl")],
        key=lambda p: p.stat().st_mtime,
    )
    for path in files:  # 舊先新後，新嘅覆蓋舊嘅
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                item_id = row.get("item_id")
                kline = row.get("kline")
                if not isinstance(item_id, int) or not isinstance(kline, list) or not kline:
                    continue
                points = {
                    str(pt.get("date")): float(pt["price_jpy"])
                    for pt in kline
                    if isinstance(pt, dict) and isinstance(pt.get("price_jpy"), (int, float))
                }
                if points:
                    charts[item_id] = points
    return charts


def fetch_missing_charts(item_ids: list[int], delay: float) -> dict[int, dict[str, float]]:
    from snk_market_data import pull_market_data  # noqa: E402
    from snkrdunk_bulk import SnkrdunkApi  # noqa: E402
    import snk_market_data as smd  # noqa: E402

    api = SnkrdunkApi(delay=delay)
    charts: dict[int, dict[str, float]] = {}
    for item_id in item_ids:
        try:
            row = pull_market_data(api, item_id, condition_code=smd.PSA10_CONDITION)
        except Exception as exc:  # 停牌/404 —— fail-closed 留空
            print(f"  fetch {item_id}: {type(exc).__name__}: {exc}")
            continue
        kline = row.get("kline") or []
        charts[item_id] = {
            str(pt.get("date")): float(pt["price_jpy"])
            for pt in kline
            if isinstance(pt, dict) and isinstance(pt.get("price_jpy"), (int, float))
        }
    return charts


def adjudicate(
    day: str,
    price_usd: float | None,
    native_jpy: float | None,
    chart: dict[str, float],
) -> tuple[str, dict[str, Any]]:
    """裁決一行 vs provider chart。回 (verdict, evidence)。

    verdict ∈ {keep, value_mismatch, fabricated_day, shifted:<day>}
    """
    jpy = chart.get(day)
    if jpy is not None:
        if native_jpy is not None:
            ok = abs(float(native_jpy) - jpy) <= max(1.0, jpy * NATIVE_TOLERANCE)
            return ("keep" if ok else "value_mismatch", {"chartJpy": jpy})
        if price_usd is None or price_usd <= 0:
            return ("value_mismatch", {"chartJpy": jpy})
        ratio = jpy / float(price_usd)
        ok = USD_JPY_RATIO_BAND[0] <= ratio <= USD_JPY_RATIO_BAND[1]
        return ("keep" if ok else "value_mismatch", {"chartJpy": jpy, "impliedRatio": round(ratio, 2)})
    day0 = date.fromisoformat(day)
    for offset in (-1, 1):
        near_day = (day0 + timedelta(days=offset)).isoformat()
        near = chart.get(near_day)
        if near is None:
            continue
        if native_jpy is not None:
            if abs(float(native_jpy) - near) <= max(1.0, near * NATIVE_TOLERANCE):
                return (f"shifted:{near_day}", {"chartJpy": near})
        elif price_usd and USD_JPY_RATIO_BAND[0] <= near / float(price_usd) <= USD_JPY_RATIO_BAND[1]:
            return (f"shifted:{near_day}", {"chartJpy": near})
    return ("fabricated_day", {})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="落 quarantined_lane 印（默認 dry-run）")
    parser.add_argument("--fetch-missing", action="store_true", help="用 SNK API 補攞未覆蓋 item 嘅 chart")
    parser.add_argument("--delay", type=float, default=0.4)
    args = parser.parse_args()

    from rebuild_036 import connect, DAILY_CREDENTIALS_ENV  # noqa: E402

    conn = connect(DAILY_CREDENTIALS_ENV)
    cur = conn.cursor()

    cur.execute(SUSPECT_ROWS_SQL)
    rows = [dict(r) for r in cur.fetchall()]
    print(f"suspect ready rows: {len(rows)}")

    # 空 entity 行 → 用變體任何 SNK identity 嘅 item 對證
    unbound_vids = sorted({int(r["variant_id"]) for r in rows if not str(r["item"] or "").strip()})
    vid_item: dict[int, int] = {}
    if unbound_vids:
        ph = ",".join(["%s"] * len(unbound_vids))
        cur.execute(
            f"""
            SELECT variant_id, external_entity_id FROM catalog_source_identity
            WHERE source_code='snkrdunk' AND variant_id IN ({ph})
            """,
            tuple(unbound_vids),
        )
        for r in cur.fetchall():
            try:
                vid_item[int(r["variant_id"])] = int(str(r["external_entity_id"]))
            except (TypeError, ValueError):
                pass

    def row_item(r: dict[str, Any]) -> int | None:
        raw = str(r["item"] or "").strip()
        if raw.isdigit():
            return int(raw)
        return vid_item.get(int(r["variant_id"]))

    charts = load_charts_from_harvests()
    print(f"charts from harvests: {len(charts)} items")
    needed = sorted({i for r in rows if (i := row_item(r)) is not None})
    missing = [i for i in needed if i not in charts]
    print(f"items needed {len(needed)}, missing charts {len(missing)}")
    if missing and args.fetch_missing:
        charts.update(fetch_missing_charts(missing, args.delay))
        missing = [i for i in needed if i not in charts]
        print(f"after fetch: missing {len(missing)}")

    # 身份閘：(variant,item) 必須喺 037 strict view，先有資格入 chart 裁決。
    strict_pairs: set[tuple[int, int]] = set()
    pair_list = sorted({(int(r["variant_id"]), i) for r in rows if (i := row_item(r)) is not None})
    if pair_list:
        ph = ",".join(["(%s,%s)"] * len(pair_list))
        flat: list[Any] = []
        for vid, item in pair_list:
            flat.extend((vid, str(item)))
        cur.execute(
            f"""
            SELECT variant_id, external_entity_id FROM operator_strict_source_identity
            WHERE source_code='snkrdunk' AND (variant_id, external_entity_id) IN ({ph})
            """,
            tuple(flat),
        )
        for r in cur.fetchall():
            try:
                strict_pairs.add((int(r["variant_id"]), int(str(r["external_entity_id"]))))
            except (TypeError, ValueError):
                pass

    # shifted 行要查 canonical 兄弟：載入涉事 item 嘅 canonical 行 (item, day) 集合
    canonical_days: set[tuple[int, str]] = set()
    if needed:
        ph = ",".join(["%s"] * len(needed))
        cur.execute(
            f"""
            SELECT p.source_external_entity_id AS item, p.observed_date
            FROM market_price_observation p
            INNER JOIN market_ingest_run r ON r.id = p.run_id
            WHERE p.source_code IN ('snkrdunk','snk','snk_psa10')
              AND p.metric_status = 'ready'
              AND (r.run_key LIKE 'snk_kline_ingest_%%' OR r.run_key LIKE 'rebuild036_snk_kline%%')
              AND p.source_external_entity_id IN ({ph})
            """,
            tuple(str(i) for i in needed),
        )
        for r in cur.fetchall():
            canonical_days.add((int(str(r["item"])), str(r["observed_date"])))

    receipts: list[dict[str, Any]] = []
    verdict_counts: defaultdict[str, int] = defaultdict(int)
    quarantine_ids: list[int] = []
    identity_quarantine_ids: list[int] = []
    for r in rows:
        item = row_item(r)
        day = str(r["observed_date"])
        base = {
            "priceObservationId": int(r["id"]),
            "variantId": int(r["variant_id"]),
            "itemId": item,
            "observedDate": day,
            "priceUsd": float(r["price_usd"]) if r["price_usd"] is not None else None,
            "nativeJpy": float(r["native_price"]) if r["native_price"] is not None else None,
            "runKey": str(r["run_key"]),
        }
        if item is None:
            verdict_counts["quarantine:unbound_snk_row"] += 1
            quarantine_ids.append(int(r["id"]))
            receipts.append({**base, "verdict": "quarantine", "reason": "unbound_snk_row"})
            continue
        if (int(r["variant_id"]), item) not in strict_pairs:
            verdict_counts["identity_quarantine:identity_not_strict"] += 1
            identity_quarantine_ids.append(int(r["id"]))
            receipts.append({**base, "verdict": "identity_quarantine", "reason": "identity_not_strict"})
            continue
        chart = charts.get(item)
        if not chart:
            verdict_counts["unadjudicated"] += 1
            receipts.append({**base, "verdict": "unadjudicated", "reason": "no_chart_evidence"})
            continue
        verdict, evidence = adjudicate(day, base["priceUsd"], base["nativeJpy"], chart)
        if verdict == "keep":
            verdict_counts["keep"] += 1
            receipts.append({**base, "verdict": "keep", **evidence})
        elif verdict.startswith("shifted:"):
            true_day = verdict.split(":", 1)[1]
            if (item, true_day) in canonical_days:
                verdict_counts["quarantine:shifted_day_dup"] += 1
                quarantine_ids.append(int(r["id"]))
                receipts.append({**base, "verdict": "quarantine", "reason": "shifted_day_dup", "chartDay": true_day, **evidence})
            else:
                verdict_counts["keep:shifted_sole_carrier"] += 1
                receipts.append({**base, "verdict": "keep", "reason": "shifted_sole_carrier", "chartDay": true_day, **evidence})
        else:
            verdict_counts[f"quarantine:{verdict}"] += 1
            quarantine_ids.append(int(r["id"]))
            receipts.append({**base, "verdict": "quarantine", "reason": verdict, **evidence})

    print("\nverdicts:")
    for key in sorted(verdict_counts):
        print(f"  {key:<44} {verdict_counts[key]}")
    print(f"  lane-quarantine total: {len(quarantine_ids)}")
    print(f"  identity-quarantine total: {len(identity_quarantine_ids)}")

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    receipt_path = AUDIT_DIR / f"snk_price_lane_audit_{stamp}.jsonl"
    with receipt_path.open("w", encoding="utf-8") as fh:
        for entry in receipts:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    summary = {
        "generatedAt": stamp,
        "mode": "write" if args.write else "dry-run",
        "suspectRows": len(rows),
        "verdicts": dict(verdict_counts),
        "quarantineStatus": QUARANTINE_STATUS,
        "receiptPath": str(receipt_path),
    }
    summary_path = AUDIT_DIR / f"snk_price_lane_audit_{stamp}_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nreceipt: {receipt_path}")
    print(f"summary: {summary_path}")

    if not args.write:
        print("\ndry-run（冇寫 DB）。用 --write 落印。")
        conn.close()
        return 0

    for label, ids, sql, status in (
        ("lane", quarantine_ids, APPLY_QUARANTINE_SQL, QUARANTINE_STATUS),
        ("identity", identity_quarantine_ids, APPLY_IDENTITY_QUARANTINE_SQL, IDENTITY_QUARANTINE_STATUS),
    ):
        if not ids:
            print(f"{label}: nothing to quarantine")
            continue
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(sql.format(placeholders=placeholders), tuple(ids))
        updated = cur.rowcount
        if updated != len(ids):
            conn.rollback()
            raise RuntimeError(
                f"{label} quarantine touched {updated} rows, expected {len(ids)}; rolled back"
            )
        conn.commit()
        print(f"{label}: quarantined {updated} rows -> metric_status='{status}'")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
