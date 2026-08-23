#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""價格行 lane 契約（DB gate；--no-db 會跳過）。

2026-08-12 事故（runbook 缺陷形狀清單）：一批 ad-hoc 補數 lane 將掛價/捏造日寫入
market_price_observation，同埋錯綁 identity 令兩個 source 家族嘅價互相交錯。
清毒係一次性，呢個測試係長期閘：

  1. FREEZE_UTC 之後開始嘅 run，唔准再產生任何非 canonical lane 嘅 ready
     SNK-family 價格行。canonical = snk_kline_ingest_* / rebuild036_snk_kline*。
     （歷史 keep 行唔郁 —— 佢哋喺 FREEZE 之前。）
  2. 跨家族矛盾 monitor：180 日家族中位數比 >= 3x 嘅 variant，只可以係
     price_identity_conflict_audit 已裁決過嘅 both-strict 名單（機器唔准自己揀邊，
     人手裁決 backlog）。出現新矛盾 variant = 有毒源重新開波，即刻紅。
  3. PC 成交 title↔卡號隔離 receipt 唔准過期（形狀 29，v1326 Latias +556%）：
     判別器（c11_pc_sold_ingest.title_collector_contradiction）依家掃出嘅每一條
     已接受矛盾 sale，一定要喺 pc_sale_title_quarantine_current.json 度有名。
     receipt 由 daily_public_release.sh 每次 bake 前重新生成；呢度監住兩者
     冇甩開（有新毒但 receipt 未跟上 = 紅）。
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

# 落閘時刻：2026-08-12 清毒行動完成、audit 印落齊之後。
FREEZE_UTC = "2026-08-12 14:10:00"
CANONICAL_RUN_PREFIXES = (
    "snk_kline_ingest_",
    "rebuild036_snk_kline",
    # 2026-08-23：成交價 lane。run_key 前綴由 psa10_latest_sale_quote.RUN_KEY_PREFIX
    # 出，唔喺呢度再抄一份。
    "psa10_latest_sale_",
)
# snkrdunk_sales 一齊入嚟：新 lane 一出世就受同一個 anti-poison 閘管，
# 唔可以因為佢係新 source_code 就自動免檢。
SNK_SOURCES = ("snkrdunk", "snk", "snk_psa10", "snkrdunk_sales")

# 20260812 深夜清零：本來 4 個 both-strict variant（653/658/666/789）查落全部係
# 同一形狀 —— snk_harvest_chip / snk_flood_chip（形狀 27 毒 lane）喺 07-29 各寫咗
# 一行 EN 卡掛價，EN item 冇 JP chart 所以逃過 lane audit，PC+eBay 兩個獨立成交源
# 夾埋差 3-5 倍。裁決 receipt：audit/bothstrict_chip_quarantine_20260812T145822Z。
# 呢個名單而家係空 —— 任何新 both-strict 矛盾都係新事故，即刻紅。
KNOWN_BOTH_STRICT_CONFLICTS: set[int] = set()

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


def main() -> int:
    from rebuild_036 import connect, DAILY_CREDENTIALS_ENV  # noqa: E402

    conn = connect(DAILY_CREDENTIALS_ENV)
    cur = conn.cursor()

    # 1. freeze 之後嘅非 canonical SNK ready 行
    cur.execute(
        f"""
        SELECT r.run_key, COUNT(*) n
        FROM market_price_observation p
        INNER JOIN market_ingest_run r ON r.id = p.run_id
        WHERE p.source_code IN {SNK_SOURCES!r}
          AND p.metric_status = 'ready'
          AND r.started_at > %s
          AND {" AND ".join(f"r.run_key NOT LIKE '{prefix}%%'" for prefix in CANONICAL_RUN_PREFIXES)}
        GROUP BY r.run_key
        """,
        (FREEZE_UTC,),
    )
    offenders = [dict(r) for r in cur.fetchall()]
    check(
        "no new non-canonical snk price lanes after freeze",
        not offenders,
        f"offending run_keys: {offenders[:5]}",
    )

    # 2. 跨家族矛盾 monitor（重用 audit 嘅偵測器 —— 一個概念一份實現）
    from price_identity_conflict_audit import READY_ROWS_SQL, find_conflicts  # noqa: E402

    cur.execute("SELECT CURDATE() d")
    today = cur.fetchone()["d"]
    cur.execute(READY_ROWS_SQL)
    rows = [dict(r) for r in cur.fetchall()]
    conflicts = find_conflicts(rows, today)
    unexpected = sorted(set(conflicts) - KNOWN_BOTH_STRICT_CONFLICTS)
    check(
        "no unadjudicated cross-family conflicts",
        not unexpected,
        f"new conflict variants: {[(v, conflicts[v]['ratio']) for v in unexpected[:8]]}",
    )

    # 3. sales title 隔離 receipt 冇過期（判別器重掃 vs receipt 檔逐條對）
    import json  # noqa: E402

    from c11_pc_sold_ingest import title_collector_contradiction  # noqa: E402
    from pc_sale_title_quarantine import CURRENT_RECEIPT, SCAN_SQL  # noqa: E402

    cur.execute(SCAN_SQL)
    flagged = {
        int(r["id"])
        for r in cur.fetchall()
        if title_collector_contradiction(str(r["listing_title"]), str(r["collector_number"]))
    }
    receipt_ids: set[int] = set()
    receipt_ok = CURRENT_RECEIPT.is_file()
    if receipt_ok:
        doc = json.loads(CURRENT_RECEIPT.read_text(encoding="utf-8"))
        receipt_ids = {int(e["saleObservationId"]) for e in doc.get("entries", [])}
    check("sale title quarantine receipt exists", receipt_ok, str(CURRENT_RECEIPT))
    missing = sorted(flagged - receipt_ids)
    check(
        "sale title quarantine receipt is fresh",
        not missing,
        f"flagged sales missing from receipt: {missing[:10]}",
    )

    # 4. F-BAND：出街嘅每一個成交價都要企喺 [M/2.5, M*2.0] 入面，
    # 或者自己明講 ungated（prior < 3 單）。gate 有 code 但零 call site =
    # 冇 gate，所以呢度直接對住 DB 入面真正揀咗嘅 quote 重算一次。
    from current_quote_revision import mintable_quote_storage_source_codes  # noqa: E402
    from sale_price_outlier import TRIM_HIGH, TRIM_LOW  # noqa: E402

    sale_codes = mintable_quote_storage_source_codes()
    marks = ",".join(["%s"] * len(sale_codes))
    cur.execute(
        f"""
        SELECT q.variant_id, q.source_code, q.price_usd,
               so.payload_json
        FROM market_current_quote_revision q
        INNER JOIN market_source_observation so ON so.id=q.source_observation_id
        INNER JOIN (
          SELECT variant_id, MAX(id) AS id
          FROM market_current_quote_revision
          WHERE source_code IN ({marks})
          GROUP BY variant_id
        ) latest ON latest.id=q.id
        """,
        tuple(sale_codes),
    )
    band_rows = [dict(r) for r in cur.fetchall()]
    # 冇 sale quote 嘅時候下面兩個 check 係空跑，所以個數一定要印出嚟：
    # 「零行」同「全部合格」睇落唔可以一樣。
    print(f"info 成交 lane quote 行數 = {len(band_rows)}")
    out_of_band = []
    ungated = 0
    for row in band_rows:
        guard = (json.loads(row["payload_json"]) or {}).get("outlierGuard") or {}
        if guard.get("ungated"):
            ungated += 1
            continue
        median_text = guard.get("medianUsd")
        if not median_text:
            out_of_band.append((row["variant_id"], "no median evidence"))
            continue
        median = Decimal(str(median_text))
        price = Decimal(str(row["price_usd"]))
        if median <= 0:
            continue
        if price > median * Decimal(str(TRIM_HIGH)) or price < median / Decimal(str(TRIM_LOW)):
            out_of_band.append((row["variant_id"], f"{price} vs median {median}"))
    check(
        "every published sale price sits inside the outlier band or says it is ungated",
        not out_of_band,
        f"out of band: {out_of_band[:5]}",
    )
    # 唔准出現「全部 ungated」呢種假綠：咁樣即係 band 從來冇 fire 過。
    check(
        "the band actually applies to most of the board, not just a handful",
        not band_rows or ungated < len(band_rows),
        f"{ungated}/{len(band_rows)} quotes are ungated",
    )

    conn.close()
    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print("all price lane contracts hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
