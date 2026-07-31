#!/usr/bin/env python3
"""接線審計：揾出「資料喺 DB 但冇人接落前端」嘅孤兒數據。

點解要有呢個腳本：最陰險嘅失敗唔係報錯，係一張表有三年數據坐喺度冇人讀，
而前端同時顯示緊「資料不可用」——兩邊都冇 error，靠人手考古先發現。
呢個 gate 將嗰個考古過程變成一條命令。

每個 probe 綁一個前端顯示元素，答三條嘢：需要邊張表 / 實測有幾多行 / 而家邊個讀緊。
`consumer` 欄係 code fact（producer 實際 SELECT 邊張表），改咗 producer 記得同步更新。

Exit codes: 0 = 冇孤兒數據, 1 = 揾到孤兒或空表, 2 = 驗唔到（DB 死／config 唔見）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data" / "runtime" / "config" / "backend.env"


def read_env_file(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def db_config() -> dict:
    import os

    file_values = read_env_file(CONFIG_PATH)

    def pick(key: str, default: str = "") -> str:
        return os.environ.get(key) or file_values.get(key) or default

    config = {
        "host": pick("CARDZ_DB_HOST", "127.0.0.1"),
        "port": int(pick("CARDZ_DB_PORT", "3308")),
        "database": pick("CARDZ_DB_NAME", "cardz_market_cap"),
        "user": pick("CARDZ_DB_USER", "cardz"),
        "password": pick("CARDZ_DB_PASSWORD"),
    }
    if not config["password"]:
        raise RuntimeError("CARDZ_DB_PASSWORD is not set (env or backend.env)")
    return config


def scalar(cursor, sql: str, default=0):
    cursor.execute(sql)
    row = cursor.fetchone()
    if not row:
        return default
    value = list(row.values())[0] if isinstance(row, dict) else row[0]
    return default if value is None else value


def probe_fx(cursor) -> dict:
    rows = scalar(cursor, "SELECT COUNT(*) FROM market_fx_rate_observation")
    return {
        "gap": "fx_rates",
        "surface": "所有錢銀欄位（7 個幣種）",
        "table": "market_fx_rate_observation",
        "rows": int(rows),
        "consumer": "canonical_public_snapshot.py",
        "verdict": "wired" if rows else "never_written",
        "detail": (
            f"{rows} 行"
            if rows
            else "0 行 —— pipelines/fx_rates.py 存在但從未接入 run_daily。"
            "format.ts:31-33 一個非 finite rate 就 blank 晒嗰個幣種所有錢銀欄位"
        ),
    }


def probe_tracked_sales(cursor) -> dict:
    """成交額窗口。2026-07-26 前 producer 讀住 market_tracked_sales_aggregate（0 行），
    而數據一直寫落 market_daily_sales_aggregate —— 典型孤兒。已改由每日表滾窗口。

    呢個 probe 而家守兩件事：每日表冇斷流，同埋窗口 anchor 追得上 snapshot。
    留意：唔准改返去讀 market_tracked_sales_aggregate，嗰張表由頭到尾冇 writer。
    """
    legacy = scalar(cursor, "SELECT COUNT(*) FROM market_tracked_sales_aggregate")
    rows = scalar(cursor, "SELECT COUNT(*) FROM market_daily_sales_aggregate")
    variants = scalar(cursor, "SELECT COUNT(DISTINCT variant_id) FROM market_daily_sales_aggregate")
    lag = scalar(
        cursor,
        "SELECT DATEDIFF((SELECT MAX(effective_at) FROM market_index_snapshot),"
        " (SELECT MAX(observed_date) FROM market_daily_sales_aggregate))",
        default=None,
    )
    if not rows:
        verdict = "never_written"
    elif lag is None or int(lag) > 2:
        verdict = "stale_feed"
    else:
        verdict = "wired"
    return {
        "gap": "tracked_sales",
        "surface": "成交額 / 成交 delta / 列表 sparkline",
        "table": "market_daily_sales_aggregate",
        "rows": int(rows),
        "consumer": (
            "canonical_public_snapshot.py:latest_sales() → windows[w].trackedSales "
            "+ windows[w].trackedSalesChangePct"
        ),
        "verdict": verdict,
        "detail": (
            f"{rows} 行 / {variants} 個 variant；最新成交日落後 snapshot {lag} 日。"
            f"舊表 market_tracked_sales_aggregate {legacy} 行（冇 writer，已棄用，唔好接返）。"
            "2026-07-26 起 latest_sales() 一次過撈兩倍窗口長度，前半段入 prev_* 欄砌成交額環比 —— "
            "UI 唔再攞價格 changePct 頂替成交 delta"
        ),
    }


def probe_pop_delta(cursor) -> dict:
    cursor.execute(
        "SELECT COUNT(*) rows_, "
        "SUM(population_change_7d_pct IS NOT NULL) pop7, "
        "SUM(population_change_30d_pct IS NOT NULL) pop30, "
        "SUM(change_7d_pct IS NOT NULL) px7 "
        "FROM market_candidate_daily_snapshot"
    )
    row = cursor.fetchone()
    total = int(row["rows_"] or 0)
    pop7 = int(row["pop7"] or 0)
    pop30 = int(row["pop30"] or 0)
    px7 = int(row["px7"] or 0)

    days = scalar(
        cursor,
        "SELECT COUNT(DISTINCT observed_date) FROM market_grader_population_observation",
    )
    span = scalar(
        cursor,
        "SELECT DATEDIFF(MAX(observed_date), MIN(observed_date)) "
        "FROM market_grader_population_observation",
    )
    starved = total > 0 and pop7 == 0 and px7 > 0
    return {
        "gap": "population_delta",
        "surface": "POP delta / Grading Pulse +N",
        "table": "market_candidate_daily_snapshot.population_change_{7d,30d}_pct",
        "rows": pop7 + pop30,
        "consumer": (
            "canonical_public_snapshot.py:population_change_windows() → "
            "graderPopulations.PSA.topGradePopulationChangePct + windows[w].marketCapChangePct"
        ),
        "verdict": "upstream_starved" if starved else ("wired" if pop7 else "never_written"),
        "detail": (
            f"POP change 欄 {pop7}/{total} 同 {pop30}/{total} 有值，"
            f"對照價格 change 欄 {px7}/{total} —— 上游冇數，因為 POP 得 {days} 個觀測日 / 跨度 {span} 日。"
            f"producer 已接線（2026-07-26），所以呢個數同時封頂咗市值 delta 嘅覆蓋率："
            f"ΔPOP 冇數嗰啲卡，市值 delta 一律出 null（fail-closed，唔准退返去用價格 delta 頂替）"
            if starved
            else f"pop7={pop7}/{total} pop30={pop30}/{total} px7={px7}/{total} days={days}"
        ),
    }


def probe_pop_coverage(cursor, roster_size: int) -> dict:
    best = scalar(
        cursor,
        "SELECT MAX(v) FROM ("
        " SELECT p.observed_date,COUNT(DISTINCT p.variant_id) v"
        " FROM market_grader_population_observation p"
        " JOIN market_universe_member m ON m.variant_id=p.variant_id"
        " JOIN market_universe_lock l ON l.id=m.universe_lock_id AND l.is_current=1"
        " GROUP BY p.observed_date"
        ") t",
    )
    pct = (best / roster_size * 100) if roster_size else 0.0
    return {
        "gap": "population_coverage",
        "surface": "POP 欄（每日覆蓋率）",
        "table": "market_grader_population_observation",
        "rows": int(best),
        "consumer": "run_daily.py",
        "verdict": "ok" if pct >= 90 else "below_target",
        "detail": f"歷來單日最高覆蓋 {best}/{roster_size} = {pct:.1f}%（目標 ≥90%）",
    }


def probe_price_coverage(cursor, roster_size: int) -> dict:
    exact_identity = scalar(
        cursor,
        "SELECT COUNT(DISTINCT m.variant_id)"
        " FROM market_universe_member m"
        " JOIN market_universe_lock l ON l.id=m.universe_lock_id AND l.is_current=1"
        " JOIN catalog_source_identity i ON i.variant_id=m.variant_id"
        " WHERE i.source_code='snkrdunk'",
    )
    covered = scalar(
        cursor,
        "SELECT COUNT(DISTINCT p.variant_id)"
        " FROM market_universe_member m"
        " JOIN market_universe_lock l ON l.id=m.universe_lock_id AND l.is_current=1"
        " JOIN market_price_observation p ON p.variant_id=m.variant_id",
    )
    fresh = scalar(
        cursor,
        "SELECT COUNT(DISTINCT p.variant_id)"
        " FROM market_universe_member m"
        " JOIN market_universe_lock l ON l.id=m.universe_lock_id AND l.is_current=1"
        " JOIN market_price_observation p ON p.variant_id=m.variant_id"
        " WHERE p.observed_date >= DATE_SUB("
        "   COALESCE((SELECT MAX(effective_date) FROM market_index_snapshot),"
        "            (SELECT MAX(observed_date) FROM market_price_observation)),"
        "   INTERVAL 2 DAY"
        " )",
    )
    pct = (covered / roster_size * 100) if roster_size else 0.0
    fresh_pct = (fresh / roster_size * 100) if roster_size else 0.0
    identity_pct = (exact_identity / roster_size * 100) if roster_size else 0.0
    return {
        "gap": "price_coverage",
        "surface": "價 / 價 delta / 市值",
        "table": "market_price_observation",
        "rows": int(covered),
        "exactIdentityRows": int(exact_identity),
        "freshRows": int(fresh),
        "consumer": "canonical_public_snapshot.py",
        "verdict": "ok" if pct >= 90 else "structural_ceiling",
        "detail": (
            f"current universe exact SNK identity {exact_identity}/{roster_size} = {identity_pct:.1f}%；"
            f"有過價格 {covered}/{roster_size} = {pct:.1f}%；"
            f"48h 新鮮價格 {fresh}/{roster_size} = {fresh_pct:.1f}%。"
            f"呢個係結構性上限，唔係等時間解決得到 —— 要加價格源"
        ),
    }


def current_universe_count(cursor) -> int:
    return int(
        scalar(
            cursor,
            "SELECT COUNT(*)"
            " FROM market_universe_member m"
            " JOIN market_universe_lock l ON l.id=m.universe_lock_id"
            " WHERE l.is_current=1",
        )
    )


# 會令 exit 1 嘅：可以修好、修好之後應該永久消失嘅斷點。
BAD_VERDICTS = {"orphan_data", "never_written", "upstream_starved", "below_target", "stale_feed"}
# 唔會令 exit 1，但唔准印 OK：已知結構性事實，等時間或者重跑都唔會清。
# 放入 BAD 會令個閘永遠 fail，做唔到 regression 偵測。
WARN_VERDICTS = {"structural_ceiling"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="淨係出 JSON")
    args = parser.parse_args()

    try:
        import pymysql
        from pymysql.cursors import DictCursor

        config = db_config()
        connection = pymysql.connect(
            host=config["host"], port=config["port"], database=config["database"],
            user=config["user"], password=config["password"],
            charset="utf8mb4", cursorclass=DictCursor,
        )
    except Exception as error:
        print(f"[wiring] FAIL 驗唔到: {type(error).__name__}: {error}", file=sys.stderr)
        return 2

    try:
        cursor = connection.cursor()
        roster = current_universe_count(cursor)
        probes = [
            probe_fx(cursor),
            probe_tracked_sales(cursor),
            probe_pop_delta(cursor),
            probe_pop_coverage(cursor, roster),
            probe_price_coverage(cursor, roster),
        ]
    finally:
        connection.close()

    bad = [p for p in probes if p["verdict"] in BAD_VERDICTS]

    if not args.json:
        for probe in probes:
            if probe["verdict"] in BAD_VERDICTS:
                mark = "GAP "
            elif probe["verdict"] in WARN_VERDICTS:
                mark = "WARN"
            else:
                mark = "OK  "
            print(f"[wiring] {mark} {probe['gap']} ({probe['verdict']})")
            print(f"           前端: {probe['surface']}")
            print(f"           {probe['detail']}")
        warn = sum(1 for p in probes if p["verdict"] in WARN_VERDICTS)
        print(f"[wiring] {len(bad)} 個可修斷點 + {warn} 個結構性；共 {len(probes)} probe，roster={roster}")

    print(json.dumps({"roster": roster, "gaps": len(bad), "probes": probes},
                     ensure_ascii=False, default=str))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
