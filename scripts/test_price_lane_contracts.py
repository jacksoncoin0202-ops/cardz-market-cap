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
     2026-09-25 加：第二個判別器（sale_price_outlier.is_isolated_price_outlier，
     reason price_isolated_spike）重算出嚟嘅每一單都要喺 receipt；
     market_pc_sale_title_quarantine 每一行都要喺 receipt（receipt ⊇ 表）；
     已知嘅 Latias & Latios 170/181 等尖刺永遠要喺度。
     驗 dry-run receipt：PC_SALE_QUARANTINE_RECEIPT=<pc_sale_title_quarantine.py --out 個檔>。
     2026-09-25 再加：listing 判別器（c11_pc_sold_ingest.title_listing_conflict：
     lot／爛殼／PSA 9／語言）同 title↔卡號一齊用 pc_sale_title_quarantine.title_reason 重算，
     一樣要全部喺 receipt；佢喺真 board 上一定要 fire 過；價審點名嗰 7 單永遠喺度。
"""
import json
import os
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

# 2026-09-25 量度到嘅孤立價格尖刺（PC，前後成交都判同一方向出界）：
# 1920226 / 1920225 / 2249919 = Latias & Latios GX 170/181（variant 1148）嘅
# $1,485 / $1,908 / $4,662，06-27 嗰單做過 90d 錨；2228905、2264434 同樣形狀。
# 佢哋一旦跌出 receipt，就會重新做返 FE 窗錨。
KNOWN_PRICE_SPIKES: set[int] = {1920225, 1920226, 2249919, 2228905, 2264434}

# 2026-09-25 價審點名、listing 判別器捉到嘅 PC 成交：2557711 Mewtwo + Charizard
# Pair（variant 2066 rank-45 價 $1,999）、2252501 Wooper & Quagsire SET、
# 2546238 DAMAGED/CRACKED SLAB、2377821 / 1952262 / 1829027 / 2558370 另一語言版本。
KNOWN_LISTING_CONFLICTS: set[int] = {2557711, 2252501, 2546238, 2377821, 1952262, 1829027, 2558370}

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

    # 1b. 每一行 ready 嘅 PC/SNK 價都要係「本卡已證實 = 呢個 provider item」。
    # 2026-09-25：18,078 行冇 strict 身份（錯綁 31 張、manual_review 66 張 EN OP
    # 掛住 JA SNK 價），17,682 行餵緊卡頁長圖同 90/180/365 後備錨。
    # 每日由 rebuild_036.quarantine_unproven_price_rows 清；呢度用同一條 SQL 驗。
    from rebuild_036 import COUNT_UNPROVEN_READY_PRICE_ROWS_SQL  # noqa: E402

    cur.execute(COUNT_UNPROVEN_READY_PRICE_ROWS_SQL)
    unproven = cur.fetchone()
    check(
        "every ready PC/SNK price row is proven to be its own card",
        int(unproven["rows_n"]) == 0,
        f"{unproven['rows_n']} rows on {unproven['variants_n']} variants"
        " -- run rebuild_036.quarantine_unproven_price_rows",
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

    from pc_sale_title_quarantine import (  # noqa: E402
        CURRENT_RECEIPT,
        REASON_PRICE,
        REASON_TITLE,
        SCAN_SQL,
        collect_document,
        load_stored_rows,
        release_problems,
        split_released,
        title_reason,
    )

    # 唔設 env = live receipt（平時就係監呢個）；設咗 = 驗一份 dry-run receipt。
    receipt_path = Path(os.environ.get("PC_SALE_QUARANTINE_RECEIPT") or CURRENT_RECEIPT)
    print(f"info sale quarantine receipt = {receipt_path}")
    # 063：market_pc_sale_title_quarantine_effective 放返咗嘅成交，佢嗰條 reason
    # 唔再算數（其他判別器照判）；表嘅覆蓋要求 = (表 − 放返) ⊆ receipt。
    stored_rows = load_stored_rows(cur)
    active_stored, released = split_released(stored_rows)
    print(f"info 隔離表 = {len(stored_rows)} 行；有證據放返 = {len(released)}：{sorted(released)[:20]}")
    cur.execute(SCAN_SQL)
    title_reasons = {
        int(r["id"]): title_reason(
            r, released_reason=str(released[int(r["id"])]["reason"]) if int(r["id"]) in released else None
        )
        for r in cur.fetchall()
    }
    flagged = {sale_id for sale_id, reason in title_reasons.items() if reason is not None}
    listing_flagged = {
        sale_id for sale_id, reason in title_reasons.items() if reason not in (None, REASON_TITLE)
    }
    print(f"info title 判別器重算 = {len(flagged)}（listing {len(listing_flagged)}）")
    receipt_ids: set[int] = set()
    receipt_ok = receipt_path.is_file()
    if receipt_ok:
        doc = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt_ids = {int(e["saleObservationId"]) for e in doc.get("entries", [])}
    check("sale title quarantine receipt exists", receipt_ok, str(receipt_path))
    missing = sorted(flagged - receipt_ids)
    check(
        "sale title quarantine receipt is fresh",
        not missing,
        f"flagged sales missing from receipt: {missing[:10]}",
    )
    check("the listing-conflict discriminator fires on the live board", bool(listing_flagged), "")
    missing_listing = sorted(KNOWN_LISTING_CONFLICTS - receipt_ids)
    check(
        "the price-audit lot / damaged / language sales stay quarantined",
        not missing_listing,
        f"missing: {missing_listing}",
    )

    # 3b. 價格尖刺判別器：用 builder 自己嘅 collect_document 重算（同一份實現、
    # 同一個 planner 成交範圍），唔喺度抄一份規則。
    regenerated = collect_document(cur, stamp="price-lane-gate")
    price_ids = {
        int(e["saleObservationId"]) for e in regenerated["entries"] if e["reason"] == REASON_PRICE
    }
    print(f"info price_isolated_spike 重算 = {len(price_ids)}；receipt entries = {len(receipt_ids)}")
    # 「零單」同「全部喺 receipt」睇落唔可以一樣：判別器喺真 board 上一定要 fire 過。
    check("the isolated price spike discriminator fires on the live board", bool(price_ids), "")
    missing_price = sorted(price_ids - receipt_ids)
    check(
        "every isolated price spike is in the receipt",
        not missing_price,
        f"{len(missing_price)} missing, e.g. {missing_price[:10]}",
    )
    stored_ids = {int(r["id"]) for r in active_stored}
    missing_stored = sorted(stored_ids - receipt_ids)
    check(
        "every market_pc_sale_title_quarantine row is still in the receipt",
        not missing_stored,
        f"{len(missing_stored)} table rows missing, e.g. {missing_stored[:10]}",
    )
    problems = release_problems(stored_rows, receipt_ids)
    check(
        "a released sale is out of the receipt and carries its evidence",
        not problems,
        "; ".join(problems) + " -- revoke the release (status='revoked') or regenerate the receipt",
    )
    missing_known = sorted(KNOWN_PRICE_SPIKES - receipt_ids)
    check(
        "the known Latias & Latios / price spike sales stay quarantined",
        not missing_known,
        f"missing: {missing_known}",
    )

    # 3c. pc_psa10_price_derivation 嘅 eBay 30d 中位數都要避開隔離表：每一行
    # 表入面嘅 PC 成交，喺佢自己嘅 30 日窗入面（as_of = sold_at + 1 日）用
    # load_pc_sales 真讀一次，都唔准再出現。
    from datetime import datetime, timedelta, timezone  # noqa: E402

    from collection_contract import LIVE_EBAY_SOLD_SOURCE_CODES  # noqa: E402
    from pc_psa10_price_derivation import load_pc_sales  # noqa: E402

    live_codes = ",".join(["%s"] * len(LIVE_EBAY_SOLD_SOURCE_CODES))
    cur.execute(
        f"""
        SELECT s.id, s.variant_id, s.sold_at, s.transaction_fingerprint
        FROM market_pc_sale_title_quarantine tq
        INNER JOIN market_sale_observation s ON s.id = tq.sale_observation_id
        WHERE s.source_code IN ({live_codes})
        """,
        tuple(LIVE_EBAY_SOLD_SOURCE_CODES),
    )
    # 放返咗嘅成交（063）係真成交，中位數本來就要食返佢，唔算漏。
    stored_sales = [dict(r) for r in cur.fetchall() if int(r["id"]) not in released]
    print(f"info 隔離表 PC 成交 = {len(stored_sales)}")
    check("the quarantine table holds PC sales to test the median against", bool(stored_sales), "")
    by_window: dict[tuple[int, object], set[str]] = {}
    for sale in stored_sales:
        key = (int(sale["variant_id"]), sale["sold_at"].date())
        by_window.setdefault(key, set()).add(str(sale["transaction_fingerprint"]))
    leaked = []
    for (variant_id, sold_date), fingerprints in sorted(by_window.items()):
        as_of = datetime.combine(sold_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)
        loaded = {str(r["transaction_fingerprint"]) for r in load_pc_sales(conn, [variant_id], as_of=as_of)}
        leaked.extend((variant_id, fp) for fp in sorted(fingerprints & loaded))
    check(
        "the eBay 30d median never loads a quarantined sale",
        not leaked,
        f"{len(leaked)} leaked, e.g. {leaked[:5]}",
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
