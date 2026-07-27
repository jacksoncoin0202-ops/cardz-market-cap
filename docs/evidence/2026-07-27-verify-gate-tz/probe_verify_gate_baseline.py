#!/usr/bin/env python3
"""驗收閘日期基準嘅唯讀重現器。

答三條問題：

1. 舊基準（UTC today）同新基準（T-1）今日各自出咩判決？
2. `volume_floor` 攞 T-2 做 baseline，喺「回填延遲」之下係咪結構性偏 FAIL？
3. `ingest_activity` 數今日 UTC 寫入行數，入面有幾多其實係回填舊觀察日？

**完全唯讀**：只 import `verify_daily_run` 嘅 pure function（`collect_facts` /
`evaluate_checks`），刻意繞開 `main()` —— `main()` 會寫 volume ledger 同 alert 檔。

跑法：
    set -a && . data/runtime/config/backend.env && set +a
    python -X utf8 docs/evidence/2026-07-27-verify-gate-tz/probe_verify_gate_baseline.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_daily_run import collect_facts, db_config, evaluate_checks  # noqa: E402


def open_connection():
    import pymysql
    from pymysql.cursors import DictCursor

    config = db_config()
    return pymysql.connect(
        host=config["host"], port=config["port"], database=config["database"],
        user=config["user"], password=config["password"],
        charset="utf8mb4", cursorclass=DictCursor,
    )


def run_baseline(connection, label: str, expected_date: str) -> bool:
    facts = collect_facts(connection, expected_date)
    checks, ok = evaluate_checks(expected_date, *facts)
    print(f"\n--- {label}: expected_date={expected_date} -> {'PASS' if ok else 'FAIL'}")
    for check in checks:
        status = "PASS" if check["pass"] else "FAIL"
        if check.get("warning"):
            status = "WARN"
        print(f"    [{status}] {check['check']}: {check['detail']}")
    return ok


def backfill_profile(connection) -> None:
    """每個 observed_date 嘅行數，按『觀察日之後第 N 日先寫入』拆開。

    volume_floor 拎 T-1 對 T-2。如果 T-2 已經回填多咗一日而 T-1 未，
    個比率就係度緊回填進度，唔係度緊採集量。
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT observed_date,
                   DATEDIFF(DATE(created_at), observed_date) AS lag_days,
                   COUNT(*) AS n
            FROM market_source_observation
            WHERE observed_date >= DATE_SUB(UTC_DATE(), INTERVAL 6 DAY)
            GROUP BY observed_date, DATEDIFF(DATE(created_at), observed_date)
            ORDER BY observed_date DESC, lag_days
            """
        )
        rows = cursor.fetchall()

    by_date: dict[str, dict[int, int]] = {}
    for row in rows:
        by_date.setdefault(str(row["observed_date"]), {})[int(row["lag_days"])] = int(row["n"])

    print("\n--- 回填輪廓：observed_date 嘅行數幾時先落地")
    print("    observed_date | lag0   lag<=1  lag<=2  最終   | 當日佔比")
    for day in sorted(by_date, reverse=True):
        buckets = by_date[day]
        total = sum(buckets.values())
        lag0 = buckets.get(0, 0)
        lag1 = lag0 + buckets.get(1, 0)
        lag2 = lag1 + buckets.get(2, 0)
        print(f"    {day}    | {lag0:<6} {lag1:<7} {lag2:<7} {total:<6} | {lag0 / total * 100:.1f}%")

    print("\n--- 同齡對比：拎『觀察日當日』行數比『觀察日當日』行數（消除回填偏差）")
    days = sorted(by_date, reverse=True)
    for newer, older in zip(days, days[1:]):
        new_raw, old_raw = sum(by_date[newer].values()), sum(by_date[older].values())
        new_lag0, old_lag0 = by_date[newer].get(0, 0), by_date[older].get(0, 0)
        raw_pct = new_raw / old_raw * 100 if old_raw else float("nan")
        fair_pct = new_lag0 / old_lag0 * 100 if old_lag0 else float("nan")
        print(
            f"    {newer} vs {older}: "
            f"gate 睇到 {new_raw}/{old_raw}={raw_pct:.0f}%  |  同齡 {new_lag0}/{old_lag0}={fair_pct:.0f}%"
        )


def ingest_activity_breakdown(connection) -> None:
    """`ingest_activity` 數今日 UTC 寫入嘅所有行 —— 唔分新觀察定回填。"""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT observed_date, COUNT(*) AS n
            FROM market_source_observation
            WHERE created_at >= UTC_DATE()
            GROUP BY observed_date
            ORDER BY observed_date DESC
            """
        )
        rows = cursor.fetchall()

    total = sum(int(r["n"]) for r in rows)
    today = datetime.now(timezone.utc).date().isoformat()
    fresh = sum(int(r["n"]) for r in rows if str(r["observed_date"]) == today)
    print(f"\n--- ingest_activity 拆解：今日(UTC {today}) 寫入 {total} 行")
    for row in rows:
        marker = "  <- 今日觀察" if str(row["observed_date"]) == today else "  (回填舊觀察日)"
        print(f"    observed_date={row['observed_date']}: {row['n']}{marker}")
    backfill = total - fresh
    print(f"    => 新觀察 {fresh} 行 / 回填 {backfill} 行"
          f"（回填佔 {backfill / total * 100:.0f}%，全部計入同一個 pass 條件）" if total else "")


def main() -> int:
    now = datetime.now(timezone.utc)
    utc_today = now.date().isoformat()
    t_minus_1 = (now.date() - timedelta(days=1)).isoformat()

    print(f"量度時刻: {now.isoformat()} (UTC) = {(now + timedelta(hours=9)).strftime('%Y-%m-%d %H:%M')} JST")

    connection = open_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@system_time_zone AS stz, NOW() AS db_now, UTC_TIMESTAMP() AS db_utc")
            row = cursor.fetchone()
            print(f"DB 時區: system_time_zone={row['stz']}  NOW()={row['db_now']}  UTC_TIMESTAMP()={row['db_utc']}")

        run_baseline(connection, "舊基準 (UTC today)", utc_today)
        run_baseline(connection, "新基準 (T-1, 現行 HEAD)", t_minus_1)
        backfill_profile(connection)
        ingest_activity_breakdown(connection)
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
